"""Fresh native inference through the same parameter dispatch and calibration.

Read-only model/application-seam diagnostics. No worker replacement or business
writes. Timings include local scoring, not HTTP or production load testing.
"""

import argparse
import asyncio
import gc
import hashlib
import json
import math
import os
import time
from pathlib import Path

from psych_agent_runtime.decision_contract import (
    memory_evidence_state,
    memory_pair_state,
    profile_contract,
)
from psych_agent_runtime.memory_head import load_memory_head
from psych_agent_runtime.routing_contract import validate_probabilities
from psych_agent_runtime.routing_service import calibrate_probabilities
from psych_agent_runtime.task_adapter import load_task_adapters

from .decision_data import selected_questions
from .evaluate_readiness import application_probe, isolate, matches, summary


def load_agent(checkpoint, device):
    import laya

    metadata = json.loads((checkpoint / "deployment.json").read_text())
    with (checkpoint / "model.safetensors").open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != metadata["weightsSha256"]:
            raise ValueError("checkpoint checksum mismatch")
    agent = laya.load(str(checkpoint), device=device)
    if str(agent.device) != device:
        raise ValueError("requested device was not used")
    adapter = None
    if metadata.get("pairAdapterSha256"):
        adapter = load_memory_head(agent, checkpoint, metadata)
    _, questions = profile_contract(metadata["profile"], metadata["contractVersion"])
    if metadata.get("isolatedTaskAdapters"):
        adapter = load_task_adapters(
            agent, checkpoint, metadata, questions, adapter.predict if adapter else None
        )
    return agent, adapter, metadata


async def evaluate(checkpoint, rows, device, defer_application=False):
    agent, adapter, metadata = load_agent(checkpoint, device)
    role, contract = metadata["profile"], metadata["contractVersion"]
    _, canonical = profile_contract(role, contract)
    cases = []
    for index, case in enumerate(rows):
        tasks = list(canonical) if role == "router" else list(case["expected"])
        if role == "executor" and "goal_action" in tasks and "goal_outcome" not in tasks:
            tasks.append("goal_outcome")
        state = case["state"]
        if role == "memory" and "relevance" in tasks:
            state = memory_pair_state(state)
        if role == "memory" and "evidence_sufficient" in tasks:
            state = memory_evidence_state(state)
        questions = selected_questions(role, state, tasks, contract)
        text = json.dumps(state, ensure_ascii=False).replace(agent.tok.mask_token, " ")
        if len(agent.tok.encode(text, add_special_tokens=False)) > metadata["maxStateTokens"]:
            raise ValueError("diagnostic state exceeds serving token budget")
        start = time.monotonic()
        result = (adapter.predict if adapter else agent.predict)(state, questions)
        decisions = {}
        for task in tasks:
            answer = result["answers"][task]
            choice = answer["choice"]
            probs = validate_probabilities(
                choice, answer["probabilities"], questions[task]["criteria"]
            )
            policy = metadata["tasks"][task]
            probs = calibrate_probabilities(probs, policy)
            threshold = float(policy["threshold"])
            if not math.isfinite(threshold) or not 0 <= threshold <= 1:
                raise ValueError("invalid threshold")
            accepted = (
                policy["enabled"] and policy["calibrated"] and probs[choice] >= max(0.85, threshold)
            )
            decisions[task] = {
                "choice": choice,
                "probabilities": probs,
                "probability": probs[choice],
                "threshold": threshold,
                "accepted": accepted,
                "status": "accepted" if accepted else "abstained",
                "scope": policy["validationScope"],
            }
        item = {**case, "decisions": decisions}
        item["correct"] = all(
            matches(decisions[t]["choice"], label) for t, label in case["expected"].items()
        )
        item["accepted"] = all(decisions[t]["accepted"] for t in case["expected"])
        item["usableCorrect"] = item["correct"] and item["accepted"]
        item["application"] = {} if defer_application else await application_probe(case, decisions)
        item["latencyMs"] = round((time.monotonic() - start) * 1000, 2)
        cases.append(item)
        if (index + 1) % 25 == 0:
            print(
                json.dumps(
                    {
                        "role": role,
                        "model": checkpoint.name,
                        "completed": index + 1,
                        "total": len(rows),
                    }
                ),
                flush=True,
            )
    return {
        "role": role,
        "health": {
            "ready": True,
            "profile": role,
            "model": metadata["model"],
            "contractVersion": contract,
            "artifact": {
                k: metadata.get(k)
                for k in (
                    "weightsSha256",
                    "pairAdapterSha256",
                    "taskAdapters",
                    "isolatedTaskAdapters",
                )
            },
            "tasks": {t: p["enabled"] for t, p in metadata["tasks"].items()},
        },
        "cases": cases,
        "all": summary(cases),
        "clientMinimumProbability": 0.85,
    }


async def run(args):
    import torch

    if args.out.exists():
        raise ValueError("refuse to overwrite evaluation")
    os.environ["LAYA_CUDA_AMP"] = "fp16"
    torch.set_num_threads(4)
    torch.cuda.set_per_process_memory_fraction(0.20)
    raw = args.data.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != json.loads(args.data.with_name("manifest.json").read_text())["challengeSha256"]:
        raise ValueError("frozen challenge changed")
    rows, audit = isolate(
        [json.loads(line) for line in raw.decode().splitlines() if line], args.datasets
    )
    if audit["excludedNames"]:
        raise ValueError("challenge overlaps a curriculum split")
    audit["challengeSha256"] = digest
    models = json.loads(args.models.read_text())
    report = {
        "challengeSha256": digest,
        "scope": "Native GPU inference with serving dispatch/calibration and Python probes; authored development labels, no HTTP/load/clinical validation.",
        "isolationAudit": audit,
        "roles": {},
        "applicationProbesDeferred": args.defer_application,
    }
    for role, model in models.items():
        report["roles"][role] = await evaluate(
            args.datasets.parent / "models" / model,
            [r for r in rows if r["profile"] == role],
            args.device,
            args.defer_application,
        )
        gc.collect()
        torch.cuda.empty_cache()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({role: r["all"] for role, r in report["roles"].items()}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "models", "datasets", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--defer-application", action="store_true", help="Replay application guards on runtime host"
    )
    asyncio.run(run(parser.parse_args()))
