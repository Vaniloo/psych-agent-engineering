# Psych Agent Runtime

Python 3.11+ 服务，负责有界 LangGraph Loop、决策 worker 适配、知识与个人记忆检索、上下文预算、流式回答和 SSE。Runtime 本身不加载 Torch。

```bash
uv sync --extra dev
uv run uvicorn psych_agent_runtime.api:app --port 8090
uv run pytest -q
uv run ruff check .
```

需要配置可访问的 Spring、Redis Stack、Chroma 与 Ollama。除 health/metrics 外，API 要求 `X-Internal-Service-Token`，浏览器统一经过 Spring JWT 网关。

| 模块 | 责任 |
| --- | --- |
| `api.py`、`graph.py` | 服务入口、请求状态图、loop 和持久化顺序 |
| `planner.py`、`route_policy.py` | Router 判断与应用权限策略 |
| `tool_controller.py` | start_loop / decide / execute / observe，预算、去重与一次修正 |
| `capabilities.py`、`mcp_client.py` | 统一参数/结果/超时，本地或 MCP 传输 |
| `response.py`、`response_prompt.py` | 证据组装、预算、回答规则与流式生成 |
| `memory.py`、`personal_recall.py`、`memory_scope.py` | 原话索引、异步消费、个人回顾与来源/日期限制 |
| `rag.py`、`agentic_rag.py` | 公共知识混合检索，证据评估后按需补查一次 |
| `web_policy.py`、`web_search.py` | 联网授权判断与 SearXNG 搜索 |
| `public_tool_client.py`、`public_tool_service.py` | ToolACE 读取 Executor 的契约、客户端与 GPU 评分服务 |
| `training/public_tools/`、`benchmarks/`、`tests/` | 单 Executor 训练、回放、压测和自动化回归 |

当前服务通过 `support_decisions.py` 分两次调用 DeepSeek，分别处理意图分类和风险复核。两步使用服务端 `DEEPSEEK_API_KEY`，缺少密钥时使用本地规则。运行时还提供账号范围内的记忆检索和独立的通用生成模型。设置 `EXECUTOR_PROVIDER=public_qwen` 接入 ToolACE，或设为 `rules` 运行规则模式。正式应用由图节点执行写入，离线读取探针位于 `benchmarks/loop_probe.py`。

[当前版本交接](../HANDOFF.md) · [启动与部署](../README.md#运行) · [训练导航](training/README.md)
