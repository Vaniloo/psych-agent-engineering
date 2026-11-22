"""Bounded, reproducible capacity probes. Credentials stay in private env files.

The component probe scores the same candidates and uses the same timeout as the runtime. Full chat
uses dedicated test users, real generation, SSE and persistence, without retries.
"""

import argparse
import asyncio
import json
import math
import secrets
import shlex
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from psych_agent_runtime.config import Settings
from psych_agent_runtime.memory import MemoryStore
from psych_agent_runtime.public_tool_client import PublicToolDecisions


def read_env(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        k, sep, v = line.removeprefix("export ").partition("=")
        if sep and not k.startswith("#"):
            result[k] = (shlex.split(v) or [""])[0]
    return result


def distribution(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        **{f"p{p}Ms": round(values[math.ceil(len(values) * p / 100) - 1], 2) for p in (50, 95, 99)},
        "maxMs": round(values[-1], 2),
    }


def summary(rows, elapsed):
    return {
        "requests": len(rows),
        "successes": sum(r["ok"] for r in rows),
        "successRate": sum(r["ok"] for r in rows) / len(rows),
        "elapsedSeconds": round(elapsed, 2),
        "throughputRps": round(len(rows) / elapsed, 3),
        "successThroughputRps": round(sum(r["ok"] for r in rows) / elapsed, 3),
        "latency": distribution([r["latencyMs"] for r in rows]),
        "ttft": distribution([r.get("ttftMs") for r in rows]),
        "preMeta": distribution([r.get("metaMs") for r in rows]),
        "errors": dict(Counter(r.get("error") for r in rows if r.get("error"))),
        "productionBudgetExceeded": sum(r.get("budgetExceeded", False) for r in rows),
        "routerUnavailable": sum(r.get("routerUnavailable", False) for r in rows),
        "executorUnavailable": sum(r.get("executorUnavailable", False) for r in rows),
        "toolErrors": sum(r.get("toolErrors", 0) for r in rows),
        "admissionRejected": sum(r.get("admissionRejected", False) for r in rows),
        "memoryDegraded": sum(r.get("memoryDegraded", False) for r in rows),
        "crossOwnerLeaks": sum(r.get("crossOwnerLeak", False) for r in rows),
        "promptTokens": sum(r.get("promptTokens", 0) for r in rows),
        "completionTokens": sum(r.get("completionTokens", 0) for r in rows),
    }


class Probe:
    def __init__(self, args):
        self.args = args
        self.env = read_env(args.env)
        if args.deepseek_env:
            self.env.update(read_env(args.deepseek_env))
        self.out = Path(args.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.records = (self.out / (args.label + "-requests.jsonl")).open("w")
        self.phases = []
        self.client = httpx.AsyncClient(
            trust_env=False,
            timeout=httpx.Timeout(75, connect=10),
            limits=httpx.Limits(max_connections=160, max_keepalive_connections=80),
        )
        self.accounts = []
        self.executor = None

    def save_status(self, **details):
        data = {
            "label": self.args.label,
            "updatedAt": datetime.now(UTC).isoformat(),
            "phases": self.phases,
            **details,
        }
        (self.out / (self.args.label + "-status.json")).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )

    async def score_read(self, i):
        state = {"request": ("请查看我的心理报告。", "我上次说喜欢什么？")[i % 2],
                 "allowedTools": ["get_dashboard", "memory_search", "search_knowledge"]}
        started = time.perf_counter()
        try:
            answer = await self.executor.evaluate(state, ["next_action"])
            result = {"ok": True, "httpStatus": 200, "model": answer["model"]}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            result = {"ok": False, "error": type(exc).__name__}
        elapsed = (time.perf_counter() - started) * 1000
        return {**result, "latencyMs": elapsed,
                "budgetExceeded": elapsed > float(self.env.get("EXECUTOR_TIMEOUT_SECONDS", 2)) * 1000}

    async def provision(self):
        private = Path(self.args.accounts)
        if private.exists():
            self.accounts = json.loads(private.read_text())
            if len(self.accounts) >= 32:
                return
        e = self.env
        store = MemoryStore(e["MEMORY_STORE_PATH"])
        prefix = (
            self.accounts[0]["username"].rsplit("_", 1)[0]
            if self.accounts
            else "cap" + datetime.now(UTC).strftime("%m%d%H%M%S")
        )

        async def auth_post(path, payload):
            for attempt in range(4):
                r = await self.client.post(e["BACKEND_BASE_URL"] + path, json=payload)
                if r.status_code != 429:
                    r.raise_for_status()
                    return r
                print(
                    "Respecting existing auth bootstrap rate limit; waiting for next minute.",
                    flush=True,
                )
                await asyncio.sleep(61)
            r.raise_for_status()

        for i in range(len(self.accounts), 32):
            account = {
                "username": f"{prefix}_{i:02}",
                "password": secrets.token_urlsafe(20),
                "canary": f"CANARY_{secrets.token_hex(8)}",
                "sessionId": None,
            }
            r = await auth_post(
                "/auth/register", {"username": account["username"], "password": account["password"]}
            )
            r = await auth_post(
                "/auth/login", {"username": account["username"], "password": account["password"]}
            )
            account["token"] = r.json()["token"]
            for j, text in enumerate(
                [
                    "我喜欢安静散步，能慢慢整理思绪。",
                    "我喜欢听轻音乐放松。",
                    "我最近喜欢读纸质书。",
                    "我有时会写三句话整理感受。",
                    "我昨天出去散步了五分钟。",
                    "我今天喝了一杯水。",
                    "我休息时喜欢看看窗外。",
                    "我喜欢在安静的地方休息。",
                ]
            ):
                store.ingest(
                    {
                        "username": account["username"],
                        "sessionId": 1,
                        "requestId": f"{prefix}-{i}-{j}",
                        "userMessage": text + " 专属测试标记：" + account["canary"],
                        "assistantReply": "",
                        "createdAt": datetime.now(UTC).isoformat(),
                    }
                )
            self.accounts.append(account)
            private.touch(mode=0o600, exist_ok=True)
            private.chmod(0o600)
            private.write_text(json.dumps(self.accounts, ensure_ascii=False, indent=2) + "\n")
        private.touch(mode=0o600, exist_ok=True)
        private.chmod(0o600)
        private.write_text(json.dumps(self.accounts, ensure_ascii=False, indent=2) + "\n")
        print("Provisioned 32 isolated test users and owned memory fixtures.", flush=True)

    async def chat(
        self, index, worker, target="proxy", *, message=None, scenario=None, proxy_url=None
    ):
        account = self.accounts[worker % len(self.accounts)]
        scenarios = [
            ("chat", "今天忙完了，想简单聊聊。请简短回复。"),
            ("knowledge", "什么是认知行为疗法？请用两三句话解释。"),
            ("memory", "你记得我喜欢什么放松活动吗？请只根据已有记录简短回答。"),
            ("progress", "我今天出去散步了一点，还没完成五分钟。请简短回应。"),
            ("chat", "今天很开心，想分享一件小事。"),
            ("chat", "我正在安排明天的日常休息，想听你简短的看法。"),
            ("report", "请查看我的心理报告，如果没有记录请直接告诉我。"),
            ("memory", "我之前提到喜欢听什么、在哪里休息？请根据记录简短回答。"),
        ]
        default_scenario, default_message = scenarios[index % len(scenarios)]
        scenario, message = scenario or default_scenario, message or default_message
        if target == "long":
            scenario = "long"
            message = "请详细介绍如何安排工作间隙的休息，给出五个例子，大约三百字。"
        body = {"message": message, "sessionId": account["sessionId"]}
        headers = {"Authorization": "Bearer " + account["token"], "Accept": "text/event-stream"}
        url = proxy_url or "http://127.0.0.1:5175/api/agent/chat/stream"
        if target in {"backend", "long"}:
            url = self.env["BACKEND_BASE_URL"] + "/agent/chat/stream"
        if target == "runtime":
            assert account["sessionId"]
            url = "http://127.0.0.1:18090/v1/agent/stream"
            body.update(username=account["username"], requestId="capacity-" + secrets.token_hex(12))
            headers = {"X-Internal-Service-Token": self.env["INTERNAL_SERVICE_TOKEN"]}
        start = time.perf_counter()
        ttft = meta = None
        done = None
        errors = []
        events = []
        current = "message"
        texts = []
        router_reason = None
        try:
            async with self.client.stream("POST", url, headers=headers, json=body) as response:
                if not response.is_success:
                    await response.aread()
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        current = line[6:].strip()
                    elif line.startswith("data:"):
                        texts.append(line[5:].strip())
                    elif not line and texts:
                        p = json.loads("\n".join(texts))
                        texts = []
                        events.append(current)
                        if current == "plan":
                            router_reason = (p.get("routing") or {}).get("fallback")
                        if current == "meta" and meta is None:
                            meta = (time.perf_counter() - start) * 1000
                        if current == "token" and p.get("text") and ttft is None:
                            ttft = (time.perf_counter() - start) * 1000
                        if current == "error":
                            errors.append(p.get("type", "sse_error"))
                        if current == "done":
                            done = p
                        current = "message"
            elapsed = (time.perf_counter() - start) * 1000
            metrics = (done or {}).get("metrics") or {}
            pipeline = metrics.get("pipeline") or {}
            reply = (done or {}).get("reply", "")
            leak = any(a["canary"] in reply for a in self.accounts if a is not account)
            account["sessionId"] = (done or {}).get("sessionId", account["sessionId"])
            router = pipeline.get("router") or {}
            executor = pipeline.get("executor") or {}
            memory = (pipeline.get("memory") or {}).get("retrieval") or {}
            ok = (
                bool(done)
                and not errors
                and metrics.get("status") == "success"
                and metrics.get("persistenceStatus") == "saved"
                and metrics.get("totalTokens", 0) > 0
                and not leak
                and "暂时无法连接到模型服务" not in reply
                and "对话服务暂时不可用" not in reply
            )
            result = {
                "ok": ok,
                "httpStatus": response.status_code,
                "ttftMs": ttft,
                "metaMs": meta,
                "error": None
                if ok
                else ("cross_owner_leak" if leak else "|".join(errors) or "degraded_or_unsaved"),
                "routerUnavailable": router_reason == "router_unavailable_or_invalid",
                "routerFallback": router_reason,
                "executorUnavailable": any(
                    d.get("fallback") == "executor_unavailable_or_invalid"
                    for d in executor.get("decisions", [])
                ),
                "toolErrors": sum(
                    c.get("status") == "error" for c in metrics.get("capabilityCalls", [])
                ),
                "memoryDegraded": memory.get("status") == "unavailable"
                or (memory.get("rerank") or {}).get("status") == "fallback",
                "crossOwnerLeak": leak,
                "promptTokens": metrics.get("promptTokens", 0),
                "completionTokens": metrics.get("completionTokens", 0),
                "executorDecisions": executor.get("decisions", []),
                "memoryStatus": memory,
                "nodes": metrics.get("nodeDurationsMs", {}),
                "tools": [c.get("name") for c in metrics.get("capabilityCalls", [])],
                "streamEvents": dict(Counter(events)),
                "runtimeDurationMs": metrics.get("totalDurationMs"),
                "requestId": metrics.get("requestId"),
                "sessionId": (done or {}).get("sessionId"),
                "persistenceStatus": metrics.get("persistenceStatus"),
                "reply": reply,
                "loop": pipeline.get("loop"),
                "route": router.get("route"),
                "supportLevel": router.get("supportLevel"),
            }
        except (httpx.HTTPError, ValueError, KeyError, AssertionError) as exc:
            elapsed = (time.perf_counter() - start) * 1000
            result = {"ok": False, "error": type(exc).__name__, "ttftMs": ttft, "metaMs": meta}
            if isinstance(exc, httpx.HTTPStatusError):
                result["httpStatus"] = exc.response.status_code
                result["retryAfter"] = exc.response.headers.get("Retry-After")
                result["admissionRejected"] = (
                    exc.response.status_code == 503 and "当前对话请求较多" in exc.response.text
                )
        return {**result, "latencyMs": elapsed, "scenario": scenario, "accountIndex": worker}

    async def phase(self, name, concurrency, rounds=2, target=None, duration=None, cap=None):
        start = time.perf_counter()
        rows = []
        counter = 0
        limit = cap or max(8, concurrency * rounds)

        async def worker(w):
            nonlocal counter
            for _ in range(math.ceil(limit / concurrency)):
                if duration and time.perf_counter() - start >= duration:
                    return
                i = counter
                counter += 1
                if i >= limit:
                    return
                r = await (
                    self.chat(i, w, target or "proxy")
                    if name == "chat"
                    else self.score_read(i)
                )
                r.update(phase=name, concurrency=concurrency, requestIndex=i)
                rows.append(r)
                self.records.write(json.dumps(r, ensure_ascii=False) + "\n")
                self.records.flush()
                if r.get("crossOwnerLeak"):
                    raise RuntimeError("cross_owner_leak_stop")

        await asyncio.gather(*(worker(w) for w in range(concurrency)))
        phase = {
            "name": name,
            "concurrency": concurrency,
            "target": target,
            **summary(rows, time.perf_counter() - start),
        }
        self.phases.append(phase)
        self.save_status(state="running")
        print(json.dumps(phase, ensure_ascii=False), flush=True)
        return phase

    async def run(self):
        try:
            if self.args.mode == "components":
                if self.env.get("EXECUTOR_PROVIDER", "public_qwen") != "public_qwen":
                    raise ValueError("components mode requires the ToolACE executor")
                self.executor = PublicToolDecisions(Settings(
                    executor_base_url=self.env["EXECUTOR_BASE_URL"],
                    executor_api_key=self.env["EXECUTOR_API_KEY"],
                    executor_adapter_sha256=self.env.get("EXECUTOR_ADAPTER_SHA256", Settings.executor_adapter_sha256),
                    executor_timeout_seconds=float(self.env.get("EXECUTOR_TIMEOUT_SECONDS", 2)),
                ))
                for c in [1, 4, 8, 16, 32]:
                    p = await self.phase("executor-tools", c, rounds=2)
                    if (
                        p["successRate"] < 0.95
                        or p["productionBudgetExceeded"] / p["requests"] > 0.2
                    ):
                        break
            else:
                await self.provision()
                for c in [1, 4, 8, 16, 32]:
                    p = await self.phase("chat", c)
                    if (
                        p["successRate"] < 0.95
                        or p["ttft"].get("p95Ms", 0) > 20000
                        or p["executorUnavailable"] / p["requests"] > 0.1
                    ):
                        break
                self.save_accounts()
            self.save_status(state="complete")
        except Exception as exc:
            self.save_status(state="failed", error=type(exc).__name__)
            raise
        finally:
            if self.executor:
                await self.executor.close()
            self.records.close()
            await self.client.aclose()

    def save_accounts(self):
        p = Path(self.args.accounts)
        p.write_text(json.dumps(self.accounts, ensure_ascii=False, indent=2) + "\n")
        p.chmod(0o600)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--env", required=True)
    p.add_argument("--deepseek-env")
    p.add_argument("--out", required=True)
    p.add_argument("--accounts", help="Test account file used in e2e mode")
    p.add_argument("--label", required=True)
    p.add_argument("--mode", choices=["components", "e2e"], required=True)
    args = p.parse_args()
    if args.mode == "e2e" and not args.accounts:
        p.error("--accounts is required in e2e mode")
    asyncio.run(Probe(args).run())
