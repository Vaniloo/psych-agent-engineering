# 统一工具层

工具注册表在 `agent-runtime/psych_agent_runtime/capabilities.py`。本地调用和 MCP 复用参数校验、结果归一化、执行器和超时。模型只能选授权名称，不能生成身份或任意工具参数。

## Executor 可选能力

| 能力 | 用途 | 范围 | 超时 |
| --- | --- | --- | --- |
| search_knowledge | 知识检索、来源与引用 | knowledge:read | 20s |
| get_goal_context | 本人目标、行动/练习、反馈及行动素材 | goals:read | 10s |
| memory_search | 本人既往用户表述证据 | memory:read | 25s |
| get_dashboard | 本人跨对话概要及已有报告 | reports:read | 10s |
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

常规最多两次只读调用，明确复合读取最多四次；相同工具和参数去重，空结果最多一次受限查询修正。Executor 读取每次结果后选择继续、finish 或 clarify；预算耗尽强制结束。参数禁止额外字段，limit 有界，输出验证结构；失败结果显式 unavailable。tool_decision 事件记录每步选择及回退原因。

## 固定系统能力

`risk_scan`、`get_user_context`、`crisis_support`、`begin_turn`、`persist_turn` 由控制器固定节点调用，不能交给 Executor 选择。危机支持、原始凭据与完整消息保存有写副作用，请求发出后的不确定结果禁止传输层自动重放；persist_turn 另有用户 + requestId 幂等保护，crisis_support 尚无同等端到端幂等保证。

## 用户写入

目标由 JWT 保护的 `/goals` 接口读写：GET/POST，PUT/DELETE `/goals/{id}`。POST/PUT 要求 confirmed=true；PUT 要求当前 version。行动内含 planned/done/skipped 状态与实际反馈；目标内含 active/paused/completed 状态。页面 check-in 保存实际进展及感受；`/{id}/reflection` 在保存后调用生成模型。完成可以调整下一步并判断完成标准，partial 只生成建议，不能写完成状态。周回顾聚合实际记录。

### 受限的目标写入工具

- `create_goal`（goals:write:create，135s）：只有用户本轮明确命令且原始请求已由 begin_turn 记录时调用。Executor 的 goal_creation 判断与应用授权检查同时通过；Spring 从原始凭据读取用户原话生成有序步骤，同一用户/requestId 最多创建一次。
- `sync_goal_progress`（goals:write:progress，10s）：Executor 的 goal_action / goal_outcome 只能匹配最多八个本人已存在行动；Spring 校验引用确实来自本轮，排除计划/假设，核对版本并幂等保存。用户可关闭自动同步、撤销尚无后续变更的同步记录。

这两项走 loop 内独立的受控写入阶段，先记录原始命令，真实结果进入最终回复，再保存完整消息和记忆 outbox，不能成为自由选择的任意写入工具；身份、会话、候选 ID 与原话由 Runtime 注入，不接受模型创造的用户或行动 ID。写入失败显式报告；消息保存与目标事务之间不存在跨工具强事务。

旧 Java ToolRegistry、/tools 调试接口、/message 路由入口、普通 /chat 入口及生成式 Planner 已删除。显式报告分析 `/chat/analyze` 保留为业务接口。
