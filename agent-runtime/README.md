# Psych Agent Runtime

Python 3.11+，负责 LangGraph 编排、三 Laya 决策适配、混合 RAG、记忆证据索引、DeepSeek Flash / Ollama 流式回复和 SSE。模型权重在独立 worker 进程加载，Runtime 不需要 Torch。

默认 Router/Executor/Memory 均为规则模式，工具同进程执行。`TOOL_TRANSPORT=mcp` 可切换到共享能力执行器的 MCP 服务。三个角色已完成独立领域微调；当前开发服务使用 Router domain-v2、Executor domain-v4、Memory domain-v2，领域权重尚未随仓库发布。旧六类路由实验仅作基线，不能加载到当前契约。

```bash
uv sync --extra dev
uv run uvicorn psych_agent_runtime.api:app --reload --port 8090
uv run pytest
uv run ruff check .
```

API 除 health/metrics 外要求 `X-Internal-Service-Token`，入口由 Spring JWT 网关保护。环境配置、模型部署、训练契约和限制见 [三 Laya 架构](../docs/local-laya-agent.md)。

入口：graph.py 编排，decision_contract.py 三角色任务，planner.py 权限路由，tool_controller.py 工具循环，capabilities.py 工具注册表，memory.py 后台记忆，routing_service.py 模型 worker，rag.py 混合检索。
