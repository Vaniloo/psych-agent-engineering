from collections import Counter

from benchmarks.launch_scenario import arrival_plan, rollout_summary


def test_open_arrivals_have_two_turns_per_user_at_two_minute_intervals():
    rows = arrival_plan()
    assert len(rows) == 100
    assert Counter(r["user"] for r in rows) == {i: 2 for i in range(50)}
    for user in range(50):
        pair = [r["atSeconds"] for r in rows if r["user"] == user]
        assert abs(pair[1] - pair[0] - 120) < 1e-8
    assert rows == sorted(rows, key=lambda r: (r["atSeconds"], r["user"]))
    assert rows == arrival_plan(seed=20261014)
    assert rows != arrival_plan(seed=7)
    assert {r["scenarioIndex"] for r in rows} == set(range(8))


def test_fast_overload_rejections_do_not_count_as_successful_inference():
    rows = [
        {"ok": True, "latencyMs": 5000, "httpStatus": 200, "scenario": "chat"},
        {
            "ok": False,
            "latencyMs": 100,
            "httpStatus": 503,
            "admissionRejected": True,
            "scenario": "chat",
        },
        {
            "ok": False,
            "latencyMs": 8000,
            "httpStatus": 200,
            "routerUnavailable": True,
            "scenario": "chat",
        },
    ]
    report = rollout_summary(rows, 10)
    assert report["admitted"] == 2 and report["rejected"] == 1
    assert report["successes"] == 1 and report["admittedSuccessRate"] == 0.5
    assert report["successThroughputRps"] == 0.1
    assert report["routerUnavailable"] == 1


async def test_probe_routes_to_requested_proxy_and_keeps_owned_account(tmp_path):
    import json
    from types import SimpleNamespace

    import httpx

    from benchmarks.capacity import Probe

    env = tmp_path / "test.env"
    env.write_text("BACKEND_BASE_URL=http://backend.test\n")
    probe = Probe(SimpleNamespace(env=env, deepseek_env=env, out=tmp_path, label="test"))
    probe.accounts = [{"token": "fixture-token", "canary": "CANARY_fixture", "sessionId": None}]
    done = {
        "sessionId": 42,
        "reply": "测试回复",
        "metrics": {
            "status": "success",
            "persistenceStatus": "saved",
            "totalTokens": 3,
            "pipeline": {},
        },
    }

    async def transport(request):
        assert str(request.url) == "https://deployment.test/api/agent/chat/stream"
        assert request.headers["Authorization"] == "Bearer fixture-token"
        assert json.loads(request.content)["message"] == "自定义场景输入"
        return httpx.Response(200, text="event: done\ndata: " + json.dumps(done) + "\n\n")

    await probe.client.aclose()
    probe.client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    try:
        row = await probe.chat(
            0,
            0,
            message="自定义场景输入",
            scenario="custom",
            proxy_url="https://deployment.test/api/agent/chat/stream",
        )
        assert row["ok"] and row["scenario"] == "custom"
        assert probe.accounts[0]["sessionId"] == 42
        assert row["persistenceStatus"] == "saved"
    finally:
        await probe.client.aclose()
        probe.records.close()


async def test_component_uses_toolace_catalog_without_retired_services(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import httpx
    from benchmarks import capacity
    from psych_agent_runtime.public_tool_client import PublicToolDecisions
    from psych_agent_runtime.public_tool_contract import CONTRACT

    env = tmp_path / "local.env"
    env.write_text("EXECUTOR_BASE_URL=http://worker.test\nEXECUTOR_API_KEY=fixture\nEXECUTOR_ADAPTER_SHA256=fixture\n")
    probe = capacity.Probe(SimpleNamespace(env=env, deepseek_env=None, out=tmp_path,
                                           label="test", mode="components"))
    requests = []

    def reply(request):
        payload = json.loads(request.content)
        names = [c["tool"]["name"] if c["kind"] == "tool" else "assistant_text"
                 for c in payload["row"]["candidateTools"]]
        assert request.url.path == "/v1/tool-scores" and payload["contractVersion"] == CONTRACT
        assert names == ["get_goal_context", "memory_search", "search_knowledge", "assistant_text"]
        requests.append(request)
        return httpx.Response(200, json={"contractVersion": CONTRACT, "model": "fixture",
                                         "candidateIds": names, "scores": [0, 0, 0, 1],
                                         "adapterSha256": "fixture"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(reply), **kw))
    await probe.run()
    assert isinstance(probe.executor, PublicToolDecisions) and requests
    assert all(phase["successRate"] == 1 for phase in probe.phases)
