# 统一工具层

工具注册表在 `agent-runtime/psych_agent_runtime/capabilities.py`。本地调用和 MCP 复用参数校验、结果归一化、执行器和超时。模型只能选授权名称，不能生成身份或任意工具参数。

## Executor 可选能力

| 能力 | 用途 | 范围 | 超时 |
| --- | --- | --- | --- |
| search_knowledge | 知识检索、来源与引用 | knowledge:read | 20s |
| get_goal_context | 本人目标、行动/练习、反馈及行动素材 | goals:read | 10s |
| memory_search | 本人既往用户表述证据 | memory:read | 5s |
| get_dashboard | 本人已有报告和统计 | reports:read | 10s |
| get_support_resources | 已配置的非紧急支持资源 | resources:read | 10s |

scope 是能力说明元数据；真正授权由应用的 allowed_tools 白名单、服务鉴权和业务归属共同执行。

| 路线 | 开放工具 | 规则默认顺序 |
| --- | --- | --- |
| chat / clarify | 无 | 直接回复/澄清 |
| knowledge | search_knowledge, memory_search | search_knowledge |
| strategy | get_goal_context, search_knowledge, memory_search | get_goal_context → search_knowledge |
| memory | memory_search, get_goal_context | memory_search |
| dashboard | get_dashboard | get_dashboard |
| professional_support | 原路线工具 + get_support_resources | 必须先读取支持资源 |

每个工具每轮最多一次，总计最多两次。Executor 读取每次结果后选择继续、finish 或 clarify；预算耗尽强制结束。参数禁止额外字段，limit 有界，输出验证结构；失败结果显式 unavailable。tool_decision 事件记录每步选择及回退原因。

## 固定系统能力

`risk_scan`、`get_user_context`、`crisis_support`、`persist_turn` 由控制器固定节点调用，不能交给 Executor 选择。后两项有写副作用，请求发出后的不确定结果禁止传输层自动重放；persist_turn 另有用户 + requestId 幂等保护，crisis_support 尚无同等端到端幂等保证。

## 用户写入

目标由 JWT 保护的 `/goals` 接口读写：GET/POST，PUT/DELETE `/goals/{id}`。POST/PUT 要求 confirmed=true；PUT 要求当前 version。行动内含 planned/done/skipped 状态与实际反馈；目标内含 active/paused/completed 状态。模型不写目标或反馈。

旧 Java ToolRegistry、/tools 调试接口、/message 路由入口、普通 /chat 入口及生成式 Planner 已删除。显式报告分析 `/chat/analyze` 保留为业务接口。
