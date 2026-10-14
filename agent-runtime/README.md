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
| `goal_operations.py` | 受控创建/进度同步，原话与实际回执 |
| `response.py`、`response_prompt.py` | 证据组装、预算、回答规则与流式生成 |
| `memory.py`、`personal_recall.py`、`memory_scope.py` | 原话索引、异步消费、个人回顾与来源/日期限制 |
| `rag.py`、`knowledge_corpus.py` | 公共知识混合检索与来源语料 |
| `decision_contract.py`、`qwen_*_contract.py` | 固定任务、有限候选与模型输入契约 |
| `routing_service.py`、`qwen_*_service.py` | 独立模型 worker，部署清单与服务鉴权 |
| `training/`、`benchmarks/`、`tests/` | 训练/校准、离线和实服务诊断、自动化回归 |

默认三角色为 rules，工具 local。当前开发接入为 Qwen 4B Router、Laya 工具 Executor + Qwen 4B 目标 Executor、Qwen 4B Memory + 原始 0.6B 重排。领域权重未随仓库发布，默认模式不代表训练模型效果。

主链路只使用图节点；离线只读探针在 `benchmarks/loop_probe.py`，不参与业务写入。旧分支调度、兼容工具循环与保存后目标 preamble 已清除。

详见 [架构](../docs/architecture.md)、[角色配置](../docs/local-laya-agent.md)、[训练导航](training/README.md) 和 [面试阅读路线](../docs/interview-guide.md)。
