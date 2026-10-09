# Psych Agent

心理支持 Agent：Spring Boot 管身份、风险规则、业务归属和事务，LangGraph 管执行链路，三个独立本地 Laya 角色管语义决策，DeepSeek Flash 或本地 Ollama 生成回复。重点是 Jev 风格的受控决策层：先确定工具权限，再在权限内选择工具，记忆控制独立训练。

```mermaid
flowchart LR
    U[浏览器 JWT / SSE] --> J[Spring 安全与业务网关]
    J --> G[LangGraph]
    G --> R[Router Laya：类型 / 风险 / 权限路线]
    R --> E[Executor Laya：授权工具 / finish / clarify]
    E --> T[统一工具执行器]
    T --> M[Memory Laya：相关证据 / 停止检索]
    E --> O[DeepSeek Flash / Ollama 回复]
    O --> D[(MySQL 消息与记忆 outbox)]
    D --> M
```

项目提供支持性对话和工程演示，不替代心理咨询、医学诊断或紧急服务。

## 已实现

- 三角色独立契约、服务、配置、概率门槛与规则回退；未训练也能运行固定流程。
- 应用持有工具权限，Executor 最多两次只读工具调用，读取结果后再选择下一步。
- 双重规则风险门、固定危机回复、模型风险升级、必需专业资源读取。
- 一套工具注册表：知识、本人记忆、目标/行动/反馈、报告与支持资源；默认本地执行，可选 MCP。
- 页面保存目标与有序小步骤；Agent 按明确命令创建目标、根据实际原话同步进展，可撤销；JWT 归属、版本和幂等校验。
- 消息与后台记忆任务同事务保存，用户 + requestId 幂等；记忆原始观察与助手建议隔离。
- Chroma + 中文 BM25 混合知识检索、上下文预算、生成服务 SSE、执行事件与实际运行指标。
- 完成步骤后生成下一步、结合真实感受反馈，依据完成标准判断目标状态；部分进展不计为完成，每周回顾使用实际记录。
- 内部服务 token、用户隔离、Redis checkpoint，以及既有报告、角色卡、知识管理和告警业务。

三个角色支持独立领域微调、逐任务校准、验证门槛和带哈希的部署清单。当前领域数据以合成场景为主，另有 Router / Memory 各 400 条公开中文对话的教师训练标签；不能据此宣称真实咨询效果。旧六类路由权重不能加载到新契约。记忆当前是 Jev-Mem 启发的第一阶段证据索引，尚未实现完整四视图图检索。旧 Java Agent/ToolRegistry、官方决策接口及同步记忆压缩已删除。

## 启动

```bash
cp .env.example .env
# 修改 JWT_SECRET、INTERNAL_SERVICE_TOKEN 和数据库密码
docker compose up --build
```

前端 `http://localhost`，Spring `http://localhost:8080`。默认三角色 `*_PROVIDER=rules`、`TOOL_TRANSPORT=local`。模型 worker 的可选 Compose 配置、独立 API key 与检查点接入见 [三 Laya 设计](docs/local-laya-agent.md)。

分进程开发：复制 `local.env.example` 为 `local.env`，启动 MySQL、Redis、Chroma、Ollama 并准备聊天/embedding 模型，再运行 `scripts/start-agent-runtime.sh`、`scripts/start-backend.sh` 和 `scripts/start-frontend.sh`。MCP 模式另启动 `scripts/start-mcp-server.sh`。前端默认 5173。

lab 上训练环境、缓存、数据、权重、日志全部放在 data 盘。三角色完整训练与第二阶段改进使用独立目录和检查点，见三 Laya 文档；运行页面读取当前工作进程的真实模型版本及启用任务。

## 验证

```bash
mvn -f backend/pom.xml clean test
uv run --directory agent-runtime --extra dev pytest
uv run --directory agent-runtime --extra dev ruff check .
node --check frontend/app.js
docker compose config --quiet
```

自动化覆盖契约、权限、风险升级、记忆隔离、幂等保存、目标版本和 H2 事务。单元边界测试使用模拟权重；真实权重评测由训练器另存 `training-report.json`，端到端验收还需验证真实 SSE、目标持久化、记忆证据和运行记录。

## 阅读顺序

1. [架构与故障边界](docs/architecture.md)
2. [三 Laya、部署与训练契约](docs/local-laya-agent.md)
3. [统一工具层](docs/agent-tools.md) 与 [MCP](docs/mcp.md)
4. [模型工程与记忆](docs/model-engineering.md)
5. [知识检索](docs/rag-pipeline.md)、[部署](docs/deployment.md)
6. [公开对话数据](docs/dialogue-datasets.md) 与 [旧路由基线](docs/local-routing.md)

源码入口：Java `AgentGatewayService`，Python `graph.py`、`decision_contract.py`、`route_policy.py`、`tool_controller.py`、`capabilities.py`、`memory.py`。
