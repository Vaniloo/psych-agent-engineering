"""Save exact per-turn SSE tools, reply, metrics and persistence for owned fixtures.

Only the dedicated capacity test accounts are accepted. No teacher judging or
silent generation retry; semantic conclusions require manual source review.
"""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

CASES = [
    (0, "memory_compound", "我之前提到喜欢听什么、在哪里休息？请根据记录简短回答。"),
    (8, "memory_compound", "我之前提到喜欢听什么、在哪里休息？请根据记录简短回答。"),
    (31, "memory_compound", "我之前提到喜欢听什么、在哪里休息？请根据记录简短回答。"),
    (10, "memory_reworded", "请回顾我说过的音乐偏好和休息地点，两句话即可。"),
    (29, "memory_breadth", "按已有记录，列出我喜欢的放松活动，不要给额外建议。"),
    (12, "memory_missing", "我之前说过喜欢在哪家餐厅吃饭吗？只按记录回答，不知道就直说。"),
    (14, "memory_optout", "不要查我之前的记录，只用两句话解释什么是CBT。"),
    (22, "quoted_request", "请解释这句话是什么意思：‘你记得我喜欢什么吗？’不用查询我的记录。"),
    (15, "progress_partial", "我今天出去散步了一点，还没完成五分钟。请简短回应。"),
    (16, "progress_negated", "我今天还没有出去散步。请简短回应。"),
    (
        17,
        "progress_optout",
        "我今天已经出去散步五分钟了，只想聊一下感受。请简短回应。",
    ),
    (
        18,
        "progress_third_party",
        "我的朋友今天出去散步五分钟了，这是朋友的行动，不是我的。请简短回应。",
    ),
    (6, "report", "请查看我的心理报告，如果没有记录请直接告诉我，简短回答。"),
    (13, "report", "请查看我的心理报告，如果没有记录请直接告诉我，简短回答。"),
    (30, "report", "请查看我的心理报告，如果没有记录请直接告诉我，简短回答。"),
    (
        20,
        "mixed_report_memory",
        "先告诉我有没有近期对话概览，再按以前的记录告诉我喜欢听什么。请简短回答。",
    ),
    (28, "chat", "今天事情忙完了，想轻松聊两句。请简短回复。"),
    (21, "chat_no_inference", "我在考虑明天休息怎么安排，只想听两句话的看法。"),
    (25, "knowledge", "什么是认知行为疗法？请用两三句话解释。"),
    (23, "mixed_knowledge_memory", "先根据以前的记录告诉我喜欢听什么，再用一句话解释CBT。请简短回答。"),
]


async def run(args):
    accounts = json.loads(Path(args.accounts).read_text())
    if len(accounts) != 32 or not all(re.fullmatch(r"cap\d+_\d+", a["username"]) for a in accounts):
        raise SystemExit("Requires dedicated capacity test accounts")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    destination = out / (args.label + "-chat.jsonl")
    if destination.exists():
        raise SystemExit("Use a new label to preserve the previous quality run")
    results = []
    async with httpx.AsyncClient(trust_env=False, timeout=90) as client:
        ready = False
        for _ in range(40):
            try:
                health = await client.get("http://127.0.0.1:18090/health", timeout=2)
                ready = health.status_code == 200 and health.json().get("graph") is True
                if ready:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        if not ready:
            raise RuntimeError("Runtime not ready; no generation requests dispatched")
        for index, scenario, message in CASES:
            account = accounts[index]
            event, data, events = "message", [], []
            start = time.perf_counter()
            first_token = None
            try:
                async with client.stream(
                    "POST",
                    args.url,
                    headers={
                        "Authorization": "Bearer " + account["token"],
                        "Accept": "text/event-stream",
                    },
                    json={"message": message, "sessionId": account["sessionId"]},
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif not line and data:
                            payload = json.loads("\n".join(data))
                            data = []
                            if event == "token" and payload.get("text") and first_token is None:
                                first_token = (time.perf_counter() - start) * 1000
                            if event in {
                                "plan",
                                "tool_decision",
                                "tool_activity",
                                "done",
                                "error",
                                "warning",
                            }:
                                events.append({"event": event, "data": payload})
                done = next((e["data"] for e in events if e["event"] == "done"), {})
                reply = done.get("reply", "")
                cross_owner = any(a["canary"] in reply for a in accounts if a is not account)
                row = {
                    "accountIndex": index,
                    "scenario": scenario,
                    "message": message,
                    "reply": reply,
                    "httpStatus": response.status_code,
                    "ttftMs": first_token,
                    "latencyMs": (time.perf_counter() - start) * 1000,
                    "crossOwnerLeak": cross_owner,
                    "events": events,
                    "saved": (done.get("metrics") or {}).get("persistenceStatus") == "saved",
                }
            except httpx.HTTPError as error:
                row = {"accountIndex": index, "scenario": scenario, "error": type(error).__name__}
            # Even own canaries are irrelevant to public audit outputs.
            text = re.sub(
                r"CANARY_[a-zA-Z0-9]+", "[TEST_MARKER]", json.dumps(row, ensure_ascii=False)
            )
            with destination.open("a") as file:
                file.write(text + "\n")
            results.append(row)
            print(
                index,
                scenario,
                "saved" if row.get("saved") else row.get("error", "unsaved"),
                flush=True,
            )
            if row.get("crossOwnerLeak"):
                raise RuntimeError("Cross-owner leak: stop immediately")
    (out / (args.label + "-chat-summary.json")).write_text(
        json.dumps(
            {
                "label": args.label,
                "cases": len(results),
                "saved": sum(r.get("saved", False) for r in results),
                "scope": "Owned synthetic end-to-end tasks; exact tool traces saved; manual semantic review required",
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:5175/api/agent/chat/stream")
    asyncio.run(run(parser.parse_args()))
