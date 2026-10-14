# 工具传输与 MCP

默认 `TOOL_TRANSPORT=local`，通过同进程 `CapabilityExecutor` 执行已校验能力，减少内部网络跳转。可选 `TOOL_TRANSPORT=mcp` 经 MCP Streamable HTTP 调用同一执行器。工具目录、权限与写入规则见 [统一工具层](agent-tools.md)。

MCP 暴露与本地注册表相同的十二项能力：risk_scan、get_user_context、search_knowledge、get_goal_context、memory_search、get_dashboard、get_support_resources、crisis_support、create_goal、sync_goal_progress、begin_turn、persist_turn。存在于 MCP 目录不等于对 Executor 开放，模型仍受当轮工具白名单限制。

Runtime 到 MCP、MCP 到 Spring 使用 `X-Internal-Service-Token`。用户名来自 Spring JWT 认证后的请求；Spring 校验会话和业务数据归属。两个服务在 Compose 内部网络运行，均不发布主机端口。MCP 对 HTTP 请求先验服务 token，再进入协议处理。

MCP 读取失败可回退到本地执行器。写能力一旦已发送，响应丢失时不自动再执行；连接尚未建立则可本地执行。未知工具、额外参数、越界 limit 和非法结果被统一拒绝。

Runtime 后台消费记忆 outbox；MCP 进程仅做记忆查询，两个进程挂载同一 SQLite volume。当前共享本机索引适合单机部署，跨主机扩容需要迁移索引存储与任务调度。
