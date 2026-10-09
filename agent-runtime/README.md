# Psych Agent Runtime

Python 3.11+ 模型执行层：

- LangGraph StateGraph、条件边、Redis checkpoint
- Ollama chat token streaming 与批量 embedding
- Chroma dense + 中文 BM25 sparse + RRF/权重融合
- 上下文 token 预算、历史/引用裁剪
- 分层记忆压缩
- MCP Streamable HTTP
- POST SSE、request ID、Prometheus metrics
- 可选 Laya 本地六类决策路由、独立模型服务、监督微调和概率校准

训练与接入：[本地路由说明](../docs/local-routing.md)。默认仍使用现有 Planner；模型依赖由 `routing` extra 单独安装。

开发：

```bash
uv sync --extra dev
uv run uvicorn psych_agent_runtime.api:app --reload --port 8090
```

另一个终端启动 MCP：

```bash
uv run python -m psych_agent_runtime.mcp_server
```

验证：

```bash
uv run pytest
uv run ruff check .
```

主要入口：

- `psych_agent_runtime/api.py`：HTTP/SSE/knowledge API
- `psych_agent_runtime/graph.py`：LangGraph
- `psych_agent_runtime/rag.py`：混合 RAG
- `psych_agent_runtime/mcp_server.py`：MCP Tools
- `psych_agent_runtime/token_budget.py`：上下文预算
- `psych_agent_runtime/memory.py`：记忆压缩
