"""Open-arrival rollout scenario: real owned users, steady use, synchronized burst, recovery.

Credentials remain outside the repo. Every user's requests are sequential; a
fast rejection is not counted as successful inference. No generation retry.
"""

import argparse
import asyncio
import json
import random
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capacity import Probe, distribution, summary


def arrival_plan(users=50, duration=240, think_seconds=120, seed=20261014):
    """Independent arrival offsets and fixed per-user think time; not a closed-loop load."""
    rng = random.Random(seed)
    rows = []
    for user in range(users):
        offset = rng.uniform(0, think_seconds)
        cycle = 0
        while offset + cycle * think_seconds < duration:
            rows.append(
                {
                    "atSeconds": offset + cycle * think_seconds,
                    "user": user,
                    "scenarioIndex": (user + cycle * 3) % 8,
                }
            )
            cycle += 1
    return sorted(rows, key=lambda r: (r["atSeconds"], r["user"]))


def rollout_summary(rows, elapsed):
    result = summary(rows, elapsed)
    rejected = [r for r in rows if r.get("admissionRejected")]
    accepted = [r for r in rows if r.get("httpStatus") == 200]
    result.update(
        admitted=len(accepted),
        rejected=len(rejected),
        admittedSuccesses=sum(r["ok"] for r in accepted),
        admittedLatency=distribution([r["latencyMs"] for r in accepted]),
        rejectedLatency=distribution([r["latencyMs"] for r in rejected]),
        scheduleLag=distribution([r.get("scheduleLagMs") for r in rows]),
        admittedSuccessRate=sum(r["ok"] for r in accepted) / len(accepted) if accepted else None,
        expectedToolMisses=sum(bool(r.get("expectedToolMissing")) for r in rows),
        scenarioCounts=dict(Counter(r.get("scenario") for r in rows)),
        coverage="Synthetic traffic; observed canary and access checks are not exhaustive privacy proof",
    )
    return result


class Rollout:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.out)
        if (self.out / "status.json").exists():
            raise ValueError("Choose a new output directory; preserve prior runs")
        self.out.mkdir(parents=True, exist_ok=True)
        self.probe = Probe(
            SimpleNamespace(
                env=args.env, deepseek_env=args.deepseek_env, out=args.out, label="rollout"
            )
        )
        self.probe.accounts = json.loads(Path(args.accounts).read_text())[: args.users]
        if len(self.probe.accounts) != args.users or not all(
            re.fullmatch(r"cap\d+_\d+", a["username"]) for a in self.probe.accounts
        ):
            raise ValueError("Requires the requested number of isolated synthetic users")
        self.locks = [asyncio.Lock() for _ in self.probe.accounts]
        self.stop = False
        self.active = 0
        self.peak_active = 0
        self.phases = []
        self.background = []
        self.run_id = datetime.now(UTC).strftime("%m%d%H%M%S")

    def status(self, state, **extra):
        (self.out / "status.json").write_text(
            json.dumps(
                {
                    "state": state,
                    "updatedAt": datetime.now(UTC).isoformat(),
                    "phases": self.phases,
                    **extra,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    async def readiness(self, label):
        account = self.probe.accounts[0]
        response = await self.probe.client.get(
            self.args.base_url + "/agent/metrics/runtime",
            headers={"Authorization": "Bearer " + account["token"]},
            timeout=15,
        )
        response.raise_for_status()
        manifest = response.json()
        (self.out / (label + "-runtime.json")).write_text(json.dumps(manifest, indent=2))
        profiles = {p["profile"]: p for p in manifest["profiles"]}
        executor = profiles["executor"]
        expected = {"public_qwen": "ready", "rules": "rules"}
        if (executor["provider"] not in expected or executor["status"] != expected[executor["provider"]]
                or any(profiles[name]["status"] != "rules" for name in ("router", "memory"))):
            raise RuntimeError("decision_dependency_unavailable")
        return manifest

    async def fixture(self, user):
        account = self.probe.accounts[user]
        account["sessionId"] = None  # All phases use this newly-created owned session.
        row = await self.probe.chat(
            0,
            user,
            message=(
                "我喜欢听轻音乐放松，也喜欢在安静的花园休息。今天心情还不错。"
                "这是我的专属测试标记：" + account["canary"] + "。请简单回应，不用给建议。"
            ),
            scenario="fixture_source",
            proxy_url=self.args.base_url + "/agent/chat/stream",
        )
        assert row["ok"] and account["sessionId"], "fixture_generation_or_persistence_failed"
        return row

    def save_accounts(self):
        p = Path(self.args.accounts)
        p.write_text(json.dumps(self.probe.accounts, ensure_ascii=False, indent=2))
        p.chmod(0o600)

    async def messages(self, user):
        a = self.probe.accounts[user]
        r = await self.probe.client.get(
            self.probe.env["BACKEND_BASE_URL"] + f"/conversations/{a['sessionId']}/messages",
            headers={"Authorization": "Bearer " + a["token"]},
        )
        r.raise_for_status()
        return r.json()

    async def request(self, user, scenario_index, phase, scheduled_at=None):
        if self.stop:
            return {
                "ok": False,
                "error": "not_dispatched_after_stop",
                "latencyMs": 0,
                "accountIndex": user,
                "phase": phase,
            }
        async with self.locks[user]:
            expected = {
                1: "search_knowledge",
                2: "memory_search",
                6: "get_dashboard",
                7: "memory_search",
            }.get(scenario_index)
            started = time.perf_counter()
            started_at = datetime.now(UTC).isoformat()
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            try:
                row = await self.probe.chat(
                    scenario_index,
                    user,
                    proxy_url=self.args.base_url + "/agent/chat/stream",
                )
            finally:
                self.active -= 1
            row.update(
                phase=phase,
                requestStartAt=started_at,
                scheduleLagMs=max(0, (started - scheduled_at) * 1000) if scheduled_at else 0,
                expectedToolMissing=bool(
                    expected and row.get("ok") and expected not in row.get("tools", [])
                ),
            )
            if row.get("crossOwnerLeak"):
                self.stop = True
                raise RuntimeError("cross_owner_canary_leak_stop")
            # Never continue a load run after generation, tool or persistence failures.
            if not row["ok"] and not row.get("admissionRejected"):
                self.stop = True
            self.probe.records.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.probe.records.flush()
            return row

    async def collect(self, name, work, minimum_duration=0):
        await self.readiness(name + "-before")
        self.peak_active = 0
        start = time.perf_counter()
        task = asyncio.create_task(work(start))
        samples = []
        last_print = start
        while not task.done():
            await asyncio.wait({task}, timeout=2)
            try:
                a = self.probe.accounts[0]
                response = await self.probe.client.get(
                    self.args.base_url + "/agent/metrics/runtime",
                    headers={"Authorization": "Bearer " + a["token"]},
                    timeout=5,
                )
                response.raise_for_status()
                samples.append(
                    {
                        "elapsedSeconds": round(time.perf_counter() - start, 2),
                        "admission": response.json().get("admission", {}),
                    }
                )
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                samples.append({"error": type(exc).__name__})
            if time.perf_counter() - last_print < 15 and not task.done():
                continue
            last_print = time.perf_counter()
            print(
                name,
                "elapsed",
                round(time.perf_counter() - start),
                "active",
                self.active,
                flush=True,
            )
            self.status("running", currentPhase=name, active=self.active)
        rows = await task
        (self.out / (name + "-admission.json")).write_text(json.dumps(samples, indent=2))
        left = minimum_duration - (time.perf_counter() - start)
        if left > 0:
            await asyncio.sleep(left)
        elapsed = time.perf_counter() - start
        phase = {
            "name": name,
            "peakClientInFlight": self.peak_active,
            "observedServerActiveMax": max(
                (r.get("admission", {}).get("active", 0) for r in samples), default=0
            ),
            "observedServerQueueMax": max(
                (r.get("admission", {}).get("queued", 0) for r in samples), default=0
            ),
            **rollout_summary(rows, elapsed),
        }
        self.phases.append(phase)
        self.status("running")
        print(json.dumps(phase, ensure_ascii=False), flush=True)
        await self.readiness(name + "-after")
        if self.stop:
            raise RuntimeError("non_overload_failure_stop; inspect exact requests")
        return rows

    async def stable(self, start):
        plan = arrival_plan(
            self.args.users, self.args.duration, self.args.think_seconds, self.args.seed
        )
        (self.out / "arrival-plan.json").write_text(json.dumps(plan, indent=2))

        async def job(row):
            target = start + row["atSeconds"]
            await asyncio.sleep(max(0, target - time.perf_counter()))
            return await self.request(row["user"], row["scenarioIndex"], "steady", target)

        return await asyncio.gather(*(job(row) for row in plan))

    async def run(self):
        try:
            self.status("preparing", users=self.args.users)
            await self.readiness("initial")
            limit = asyncio.Semaphore(4)

            async def seed(user):
                async with limit:
                    result = await self.fixture(user)
                    print("Owned fixtures prepared", user + 1, "/", self.args.users, flush=True)
                    return result

            fixture_rows = await asyncio.gather(*(seed(i) for i in range(self.args.users)))
            self.save_accounts()
            (self.out / "fixtures.json").write_text(json.dumps(fixture_rows, indent=2))
            self.baseline = [len(await self.messages(i)) for i in range(self.args.users)]
            # Simulate lightweight browsing before messages: SQL reads, no fabricated online sockets.
            view_rows = []

            async def browse(user):
                a = self.probe.accounts[user]
                start = time.perf_counter()
                for path in ["/conversations", f"/conversations/{a['sessionId']}/messages"]:
                    r = await self.probe.client.get(
                        self.probe.env["BACKEND_BASE_URL"] + path,
                        headers={"Authorization": "Bearer " + a["token"]},
                    )
                    r.raise_for_status()
                view_rows.append({"latencyMs": (time.perf_counter() - start) * 1000, "ok": True})

            await asyncio.gather(*(browse(i) for i in range(self.args.users)))
            (self.out / "online-reads.json").write_text(json.dumps(view_rows, indent=2))
            self.status("running", users=self.args.users)
            await self.collect("steady", self.stable, minimum_duration=self.args.duration)

            async def burst(_start):
                return await asyncio.gather(
                    *(self.request(i, 0, "burst") for i in range(self.args.users))
                )

            burst_rows = await self.collect("burst", burst)
            rejected = [r["accountIndex"] for r in burst_rows if r.get("admissionRejected")][:10]

            async def retry(start):
                async def job(index, user):
                    # Explicit one-time user retry, separated from first-attempt results.
                    target = start + 5 + index
                    await asyncio.sleep(max(0, target - time.perf_counter()))
                    return await self.request(user, 0, "delayed_user_retry", target)

                return await asyncio.gather(*(job(i, u) for i, u in enumerate(rejected)))

            if rejected:
                await self.collect("delayed_user_retry", retry)

            async def recovery(_start):
                return await asyncio.gather(
                    *(
                        self.request(i, scenario, "recovery")
                        for i, scenario in enumerate([0, 1, 2, 4])
                    )
                )

            await self.collect("recovery", recovery)
            all_rows = [
                json.loads(line)
                for line in (self.out / "rollout-requests.jsonl").read_text().splitlines()
            ]
            saved = Counter(
                r["accountIndex"] for r in all_rows if r.get("persistenceStatus") == "saved"
            )
            checks = []
            for i in range(self.args.users):
                actual = len(await self.messages(i)) - self.baseline[i]
                checks.append(
                    {
                        "accountIndex": i,
                        "messageDelta": actual,
                        "expectedDelta": 2 * saved[i],
                        "passed": actual == 2 * saved[i],
                    }
                )
            assert all(r["passed"] for r in checks), "message_count_mismatch"
            (self.out / "message-integrity.json").write_text(json.dumps(checks, indent=2))
            # Check actual cross-owner authorization, separately from reply-marker detection.
            access = []
            for i in range(5):
                a = self.probe.accounts[i]
                other = self.probe.accounts[(i + 1) % self.args.users]
                r = await self.probe.client.get(
                    self.probe.env["BACKEND_BASE_URL"]
                    + f"/conversations/{other['sessionId']}/messages",
                    headers={"Authorization": "Bearer " + a["token"]},
                )
                access.append(
                    {"attempt": i, "httpStatus": r.status_code, "passed": not r.is_success}
                )
            assert all(r["passed"] for r in access), "cross_owner_session_access"
            (self.out / "access-isolation.json").write_text(json.dumps(access, indent=2))
            self.status(
                "complete",
                users=self.args.users,
                messageIntegrity=True,
                crossOwnerAccessRejected=True,
                modelDependencies="ready",
                scope="One short actual rollout scenario; not production or long-duration SLA proof",
            )
        except Exception as exc:
            self.status("failed", error=type(exc).__name__, detail=str(exc))
            raise
        finally:
            self.save_accounts()
            self.probe.records.close()
            await self.probe.client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ["env", "deepseek-env", "accounts", "out"]:
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--duration", type=float, default=240)
    parser.add_argument("--think-seconds", type=float, default=120)
    parser.add_argument("--seed", type=int, default=20261014)
    parser.add_argument("--base-url", default="http://127.0.0.1:5175/api")
    args = parser.parse_args()
    if args.users < 5 or args.duration <= 0 or args.think_seconds <= 0:
        parser.error("users >= 5, duration > 0 and think-seconds > 0 required")
    asyncio.run(Rollout(args).run())
