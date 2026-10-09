# Agent Tools

系统同时保留两层 Tool 机制：

1. 主链路：LangGraph 通过 MCP Streamable HTTP 调用七个受控工具，见 [MCP 设计](mcp.md)。
2. 降级链路：原 Java `ToolRegistry` 保留五个 `ToolExecutor`，供 Runtime 故障时使用。

## Java ToolRegistry

- `search_knowledge`
- `get_dashboard`
- `risk_scan`
- `get_user_memory`
- `recommend_strategy`

管理员调试接口：

- `GET /tools`
- `GET /tools/{toolName}`
- `POST /tools/call`
- `POST /tools/{toolName}`

Agent 场景中的 `username` 由登录态覆盖，客户端生成的同名参数不能用于读取其他用户数据。高风险扫描在任何工具决策前已经执行，不能依赖模型“记得调用 risk_scan”。

## Planner 约束

主 LangGraph Planner 当前只允许规划无副作用的：

- `search_knowledge`
- `get_dashboard`

上下文加载、安全扫描、危机流程与持久化是图中的固定节点，不交给模型决定。这是“模型做语义选择、程序做控制流”的边界。

Jev 可以升级到危机流程，但不能跳过已触发的确定性危机门。风险升高或不确定时，由控制器添加只读 `get_support_resources` 能力；这不创建危机报告。

Python 工具层统一在 `capabilities.py`：MCP Server 和客户端回退复用同一个执行器，未知能力、额外参数、越界 limit 和无效结果在边界被拒绝。每项能力有超时与重放策略。`persist_turn` 与 `crisis_support` 在请求已发送且结果不确定时不直接再执行一次，避免重复消息、报告和告警；目前未实现端到端幂等键，因此此类故障可能需要后续核对。
