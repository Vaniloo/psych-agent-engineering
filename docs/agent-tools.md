# Agent Tools

系统同时保留两层 Tool 机制：

1. 主链路：LangGraph 通过 MCP Streamable HTTP 调用六个受控工具，见 [MCP 设计](mcp.md)。
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
