# MCP 设计

项目使用官方 Python MCP SDK 的 Streamable HTTP transport。MCP Server 与 Agent Runtime 使用同一个 Python 包、不同进程和同一个镜像。

## 工具

| Tool | 实现位置 | 副作用 |
| --- | --- | --- |
| `risk_scan` | Spring deterministic scanner | 无 |
| `get_user_context` | Spring memory service | 可能初始化空记忆记录 |
| `search_knowledge` | Python hybrid RAG | 查询缓存 |
| `get_dashboard` | Spring report service | 无 |
| `crisis_support` | Spring crisis workflow | 写报告与告警事件 |
| `persist_turn` | Spring memory service | 写消息与压缩记忆 |

## 为什么保留 MCP Sidecar

- Tool Schema 与 Agent 框架解耦。
- 可用 MCP Inspector 或其他兼容客户端测试工具。
- Agent Runtime 不需要知道 Spring 内部类结构。
- 同一工具未来可以被其他 Agent 复用。

## 身份与安全

MCP 工具中的 `username` 不是可信认证信息。真正的用户身份来自 Spring 网关的 JWT，Runtime 只注入已认证用户名；MCP Server 再用内部服务 token 访问 Spring。Spring 最终验证用户与 session 的归属。

Compose 不向主机发布 `8001` 和 `8090`，防止客户端绕过 JWT 直接构造 MCP 或 Runtime 请求。内部 token 必须与 JWT secret 独立，并在生产环境通过 secret manager 注入。

## Fallback

Runtime 对 MCP 调用设置了同进程受限 fallback：

- 安全、上下文、Dashboard、持久化仍调用 Spring trusted API。
- RAG 直接调用同进程 RagEngine。
- 未知工具拒绝执行。

该 fallback 是可用性措施，不是另一个任意工具执行入口。

参考：[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)。
