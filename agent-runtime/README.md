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
| `api.py`、`graph.py` | 服务入口；load_context → guard → prepare → agent ⇄ tools → persist → metrics |
| `guard.py`、`safety.py` | 输入护栏：确定性危机扫描 + 模型风险复核，输出三档支持级别 |
| `llm.py`、`prompts.py` | OpenAI 兼容客户端（DeepSeek / Ollama），原生 tool calling 与流式输出；系统提示 |
| `tools.py` | 模型可调用的 4 个工具：参数结构、身份绑定、结果摘要 |
| `prefilter.py`、`public_tool_service.py` | ToolACE 工具预筛客户端与 GPU 评分服务 |
| `capabilities.py`、`mcp_client.py` | 底层能力：参数校验、超时、本地或 MCP 传输、遥测 |
| `memory.py`、`memory_scope.py` | 原话索引、异步消费 outbox、按时间限定 |
| `rag.py`、`bm25.py`、`chunking.py` | 公共知识混合检索与切块 |
| `web_search.py` | SearXNG 搜索 |
| `training/public_tools/`、`benchmarks/`、`tests/` | 单 Executor 训练、回放、压测和自动化回归 |

`AI_PROVIDER=deepseek` 使用 DeepSeek，`ollama` 使用本地 Ollama 的 `/v1` 接口，两者都走原生 tool calling。设置 `EXECUTOR_PROVIDER=public_qwen` 启用 ToolACE 预筛，设为 `rules` 则每步把全部可用工具交给主模型。

[当前版本交接](../HANDOFF.md) · [启动与部署](../README.md#运行) · [训练导航](training/README.md)
