"""Finish one lab training run: freeze, evaluate, gate, and optionally promote.

No teacher calls, scheduled polling, or credential files are created. The caller
supplies private role credentials in the environment. Only matching role workers
may be replaced; failed replacements restore the previous startup mapping.
"""

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from psych_agent_runtime.decision_contract import PROFILES

from .evaluate_challenge import evaluate
from .freeze_deployment import freeze
from .promotion_gate import compare

VERSIONS = {
    "router": ("domain-v3", "domain-v4", 18091, 18101),
    "executor": ("domain-v4", "domain-v6", 18093, 18103),
    "memory": ("domain-v2", "domain-v5", 18092, 18102),
}


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def process_matches(pid, role, checkpoint, port=None, training=False):
    try:
        proc = Path("/proc") / str(pid)
        args = proc.joinpath("cmdline").read_bytes().split(b"\0")
        if training:
            return (
                b"training.train_decisions" in args
                and role.encode() in args
                and str(checkpoint).encode() in args
            )
        env = dict(
            item.split(b"=", 1)
            for item in proc.joinpath("environ").read_bytes().split(b"\0")
            if b"=" in item
        )
        return (
            b"uvicorn" in args
            and b"psych_agent_runtime.routing_service:app" in args
            and str(port).encode() in args
            and env.get(b"DECISION_PROFILE") == role.encode()
            and env.get(b"DECISION_CHECKPOINT") == str(checkpoint).encode()
        )
    except (OSError, ValueError):
        return False


def worker_pid(role, checkpoint, port):
    matches = [
        int(p.name)
        for p in Path("/proc").iterdir()
        if p.name.isdigit() and process_matches(int(p.name), role, checkpoint, port)
    ]
    if len(matches) != 1:
        raise RuntimeError("expected exactly one owned worker: " + role)
    return matches[0]


def health(port):
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=4, trust_env=False)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        return {}


def ready(role, model, port, deadline=180):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        value = health(port)
        if value.get("ready") and value.get("profile") == role and value.get("model") == model:
            return value
        time.sleep(2)
    raise RuntimeError("worker readiness timed out: " + role)


def stop_owned(pid, role, checkpoint, port):
    if not process_matches(pid, role, checkpoint, port):
        raise RuntimeError("worker ownership changed: " + role)
    os.kill(pid, signal.SIGTERM)
    for _ in range(45):
        if not process_matches(pid, role, checkpoint, port):
            return
        time.sleep(1)
    raise RuntimeError("worker did not terminate: " + role)


def start_canary(root, role, checkpoint, port):
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError("canary port is already occupied: " + role)
    env = os.environ.copy()
    env.update(
        DECISION_PROFILE=role,
        DECISION_CHECKPOINT=str(checkpoint),
        DECISION_API_KEY=env[role.upper() + "_API_KEY"],
        ROUTER_HOST="127.0.0.1",
        ROUTER_DEVICE="cpu" if role == "router" else "cuda:0",
        CUDA_VISIBLE_DEVICES="" if role == "router" else ("0" if role == "executor" else "1"),
    )
    with (root / "logs" / f"{role}-public-round6-canary.log").open("w") as log:
        p = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "psych_agent_runtime.routing_service:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (root / "run" / f"{role}-public-round6-canary.pid").write_text(str(p.pid))
    return p


def start_main(script, role):
    subprocess.run(["bash", str(script), role], check=True, stdin=subprocess.DEVNULL)


def rewrite_role(script_text, role, old, new):
    prefix = " " + role + ")"
    lines = script_text.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(matches) != 1 or f"version={old};" not in lines[matches[0]]:
        raise ValueError("startup mapping changed: " + role)
    i = matches[0]
    lines[i] = lines[i].replace(f"version={old};", f"version={new};")
    if role == "memory":
        lines[i] = lines[i].replace("CUDA_VISIBLE_DEVICES=5", "CUDA_VISIBLE_DEVICES=1")
    return "".join(lines)


def same_predictions(before, after, role):
    def inventory(report):
        return {
            r["name"]: (r["actual"], r["accepted"]) for r in report["cases"] if r["profile"] == role
        }

    return inventory(before) == inventory(after)


def run(args):
    root = args.root.resolve()
    if not str(root).startswith("/home/ubuntu/data/"):
        raise ValueError("lab artifacts must remain on the data disk")
    data = root / "datasets/public-scale-round6"
    out = data / "evaluation"
    out.mkdir(exist_ok=True)
    state = {
        "stage": "waiting_for_training",
        "startedAt": datetime.now(UTC).isoformat(),
        "roles": {},
    }

    def status(stage):
        state["stage"] = stage
        state["updatedAt"] = datetime.now(UTC).isoformat()
        write(out / "round-status.json", state)
        print(json.dumps({"stage": stage, "roles": state["roles"]}, ensure_ascii=False), flush=True)

    reports = {
        name: out / f"baseline-{name}.json"
        for name in ["round5", "round4", "round3", "memory-adapted"]
    }
    if not all(p.exists() for p in reports.values()):
        raise ValueError("frozen baseline evaluations must finish before candidate monitoring")
    deadline = time.monotonic() + args.timeout_hours * 3600
    status("waiting_for_training")
    while True:
        complete = True
        for role, (_, version, _, _) in VERSIONS.items():
            checkpoint = root / "models" / f"psych-{role}-{version}"
            if (checkpoint / "training-report.json").exists():
                state["roles"][role] = {"training": "complete", "candidate": checkpoint.name}
                continue
            pid = int((root / "run" / f"{role}-public-round6-training.pid").read_text())
            if not process_matches(pid, role, checkpoint, training=True):
                raise RuntimeError("training exited without a complete report: " + role)
            complete = False
        if complete:
            break
        if time.monotonic() > deadline:
            raise RuntimeError("training monitoring deadline reached; workers left running")
        time.sleep(30)
    status("freezing_candidates")
    for role, (_, version, _, _) in VERSIONS.items():
        freeze(
            root / "models" / f"psych-{role}-{version}",
            root / "datasets/three-laya-public-r6" / role,
            role,
        )
    canaries = {}
    try:
        for role, (_, version, _, port) in VERSIONS.items():
            checkpoint = root / "models" / f"psych-{role}-{version}"
            canaries[role] = start_canary(root, role, checkpoint, port)
            ready(role, checkpoint.name, port)
            os.environ[role.upper() + "_CHALLENGE_URL"] = f"http://127.0.0.1:{port}"
        status("evaluating_candidates")
        inputs = {
            name: data / "frozen" / f"{name}.jsonl" for name in ["round5", "round4", "round3"]
        }
        inputs["memory-adapted"] = data / "longmemeval-adapted.jsonl"
        gates = {role: [] for role in VERSIONS}
        candidates = {}
        for name, path in inputs.items():
            report = evaluate(path, out / f"candidate-{name}.json", "public-round6-candidate")
            if name == "memory-adapted":
                report["scope"] = (
                    "Adapted short-context LongMemEval evidence ownership diagnostics, not original QA accuracy; no training use."
                )
                write(out / f"candidate-{name}.json", report)
            candidates[name] = report
            comparison = compare(json.loads(reports[name].read_text()), report)
            write(out / f"gate-{name}.json", comparison)
            for role, item in comparison.items():
                if not item["passed"]:
                    gates[role].extend(name + ": " + reason for reason in item["reasons"])
        for role, (_, version, _, _) in VERSIONS.items():
            checkpoint = root / "models" / f"psych-{role}-{version}"
            metadata = json.loads((checkpoint / "deployment.json").read_text())
            if set(metadata["tasks"]) != set(PROFILES[role][1]) or not all(
                p["enabled"] and p["calibrated"] for p in metadata["tasks"].values()
            ):
                gates[role].append("all deployment tasks must be enabled and calibrated")
            metadata.update(
                promotionStatus="rejected" if gates[role] else "approved",
                promotionReasons=gates[role],
                promotionEvaluation="public-scale-round6",
                promotionEvaluations={
                    name: hashlib.sha256((out / f"candidate-{name}.json").read_bytes()).hexdigest()
                    for name in inputs
                },
            )
            write(checkpoint / "deployment.json", metadata)
            state["roles"][role]["gate"] = "rejected" if gates[role] else "passed"
            state["roles"][role]["reasons"] = gates[role]
        write(out / "promotion-comparison.json", state["roles"])
        status("gates_complete")
        if args.promote_passed:
            script = root / "src/three-laya-current/start-decision-services.sh"
            backup = root / "src/public-scale-round6/start-decision-services.before.sh"
            if backup.exists():
                raise ValueError("rollout already attempted; inspect its existing record")
            backup.write_text(script.read_text())
            for role, (old, new, main_port, _) in VERSIONS.items():
                if gates[role]:
                    state["roles"][role]["deployment"] = "retained_previous"
                    continue
                previous = root / "models" / f"psych-{role}-{old}"
                candidate = root / "models" / f"psych-{role}-{new}"
                original = script.read_text()
                if health(main_port).get("model") != previous.name:
                    raise RuntimeError("live baseline changed: " + role)
                pid = worker_pid(role, previous, main_port)
                updated = rewrite_role(original, role, old, new)
                script.write_text(updated)
                try:
                    stop_owned(pid, role, previous, main_port)
                    start_main(script, role)
                    info = ready(role, candidate.name, main_port)
                    if (
                        info["artifact"]["weightsSha256"]
                        != json.loads((candidate / "deployment.json").read_text())["weightsSha256"]
                    ):
                        raise RuntimeError("live weight hash differs: " + role)
                    os.environ[role.upper() + "_CHALLENGE_URL"] = f"http://127.0.0.1:{main_port}"
                    for name, path in inputs.items():
                        if role not in candidates[name]["summary"]:
                            continue
                        live = evaluate(
                            path,
                            out / f"deployed-{role}-{name}.json",
                            "public-round6-deployed",
                            profile=role,
                        )
                        if not same_predictions(candidates[name], live, role):
                            raise RuntimeError("live diagnostic drift: " + role)
                    state["roles"][role]["deployment"] = "promoted"
                    state["roles"][role]["liveHealth"] = info
                except Exception:
                    script.write_text(original)
                    matching = [
                        int(p.name)
                        for p in Path("/proc").iterdir()
                        if p.name.isdigit()
                        and process_matches(int(p.name), role, candidate, main_port)
                    ]
                    for current in matching:
                        stop_owned(current, role, candidate, main_port)
                    if not health(main_port).get("ready"):
                        start_main(script, role)
                    ready(role, previous.name, main_port)
                    state["roles"][role]["deployment"] = "rolled_back"
                    metadata = json.loads((candidate / "deployment.json").read_text())
                    metadata.update(
                        promotionStatus="rejected",
                        promotionReasons=["live rollout failed; previous worker restored"],
                    )
                    write(candidate / "deployment.json", metadata)
                    raise
                status("promoting_passed_roles")
        # Export aggregate reports without copying public benchmark turns locally.
        summary = {
            name: {
                label: json.loads((out / f"{label}-{name}.json").read_text())["summary"]
                for label in ["baseline", "candidate"]
            }
            for name in inputs
        }
        write(
            out / "aggregate-results.json",
            {
                "scope": "project diagnostics and adapted evidence checks only; not clinical validation",
                "diagnostics": summary,
                "roles": state["roles"],
            },
        )
        status("complete")
    finally:
        for role, p in canaries.items():
            if p.poll() is None:
                _, version, _, port = VERSIONS[role]
                stop_owned(p.pid, role, root / "models" / f"psych-{role}-{version}", port)
                p.wait(timeout=15)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--timeout-hours", type=float, default=10)
    p.add_argument("--promote-passed", action="store_true")
    args = p.parse_args()
    try:
        run(args)
    except Exception as exc:
        path = args.root / "datasets/public-scale-round6/evaluation/round-status.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        state.update(
            stage="failed",
            errorType=type(exc).__name__,
            reason=str(exc),
            updatedAt=datetime.now(UTC).isoformat(),
        )
        write(path, state)
        raise


if __name__ == "__main__":
    main()
