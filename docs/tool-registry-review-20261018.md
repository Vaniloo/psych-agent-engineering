# 工具扩展性审查：从固定名单走向候选描述判断

状态：架构审查与建议，尚未实施候选评分Executor v3，也未开始新训练。角色语气搜索已按用户要求暂停；当前只推进通用搜索。

更新（2026-10-19）：本地开发服务现已接入可变候选 ToolACE 4B，并打通宿主授权的 MCP 发现、参数、执行和前端显示。以下是当时的审查快照；当前实施见[动态 MCP 与搜索提速](dynamic-mcp-search-latency-20261019.md)。

后续已完成[未见工具离线探针与训练设计](../agent-runtime/training/experiments/tool-utility-20261018-v3/README.md)：现有Executor动态候选直接选择24/36，逐候选评分7/36。以下评分方案保留为候选思路；当前证据更支持先训练动态候选choice，不能预设逐候选utility一定更优。

## 当前代码事实

项目已经有一套注册表，不需要再新建平行系统：

|位置|已有职责|限制|
|---|---|---|
|`psych_agent_runtime/capabilities.py` 的 `CAPABILITIES`|参数模型、超时、scope、重放安全，MCP/本地调用共用校验|缺少统一的用途/不用场景/模型可见描述；dispatch仍按名称分支|
|`decision_contract.py` 的 `TOOL_CHOICES`|Executor v2有限决策候选|固定五类读取工具，加finish/clarify，没有search_web|
|`tool_controller.py`|授权、预算、观察、去重与有限补查|读取准入依赖TOOL_CHOICES及POLICY_READ_TOOLS两套名单|
|`training/decision_data.py` 的 `selected_questions`|训练/服务共用问题契约|拒绝不在旧TOOL_CHOICES中的工具，注册工具不等于旧权重可选择它|
|`mcp_server.py`|传输层工具及描述|函数签名/描述与注册表分别维护；MCP发现不等于模型理解|
|`tool_activity.py`、前端tool-activity|显示真实工具执行|工具中文名也独立维护|

因此问题不只是“有没有注册表”，而是注册、权限、候选描述、训练名单、传输定义还没有完全统一。

## 建议的轻量演进

扩展现有Capability元数据，统一提供`name/version/description/use_when/avoid_when/input_schema/scope/side_effect/timeout/handler`。从注册表派生模型工具描述、MCP定义和显示名，减少手工同步。身份、session、request_id及写入批准回执为运行时绑定，不让模型自由生成。

每轮：任务+观察 → 应用过滤已授权且可用工具 → 候选描述 → JEV效用评估 → 生成模型构造选中工具参数 → 运行时校验执行 → 结果进入观察 → 再决策或停止。

当前工具少，直接把少量授权工具提供给选择层即可。工具多到影响上下文/延迟时，再对注册表做BM25/向量候选召回，保持安全必需工具直接可见。暂不新增向量库、插件市场或远程执行平台。

JEV学习`当前任务+已有证据+候选工具用途`的关系，输出固定效用等级/分数，而非学习不断增加的工具ID。先以排序/弃权建议形式试验，生成模型负责复杂参数，应用负责身份、权限、超时、去重和写入边界。finish/clarify单独保留，不能“有候选就必须调用”。

普通工具新增通常是注册handler、描述和schema，补测试、更新索引，而不是立即训练。遇到全新能力或稳定误用，再补数据训练。新工具仍要接入代码、权限和验证；“无需每次训练”不等于“零改动、零评测”。

## 训练判断

当前新增主动搜索已经由生成模型作语义判断，再作为策略授权读取进入原loop；这不是已训练Executor自主选中了search_web。关键词前置条件仅在旧显式模式保留，白名单的主动模式会直接让模型判断外部信息需要；私有记忆、目标、报告及心理知识优先内部工具。保留用户拒绝联网和公开查询约束。

18条人为编写的开发探针：6条隐式外查、1条显式查文档、1条缺城市澄清、10条内部/稳定概念/引用/拒绝/闲聊，DeepSeek Flash语义判断18/18符合预期。样本小，只证明新入口覆盖这些案例，不能作为上线准确率或本地JEV效果。

建议先确定候选描述契约，再进行一次专项后训练；不要仅给旧固定分类新增一个搜索标签后继续反复加标签。任务覆盖：什么时候用公开搜索、什么时候用私有记忆/心理库、结果足够后停止、工具失败后补查、相近工具辨别与缺参数澄清。

训练与评测按工具家族/场景分组隔离，并保留完整未见工具作为测试；增加工具改名、描述改写、参数变体、未授权工具和拒绝调用负例。报告未见工具选择、误用、漏用、弃权、参数校验成功率及延迟。随机拆同一套模板不能证明新工具泛化。

## 成熟参考

- [Anthropic Tool Search](https://www.anthropic.com/engineering/advanced-tool-use)：工具按需发现、加载相关定义，控制大量工具对上下文的占用。
- [LangChain LLM Tool Selector](https://docs.langchain.com/oss/python/langchain/middleware/built-in#llm-tool-selector)：根据工具名称和描述动态选择相关工具。
- [MCP Tools规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)：tools/list及工具变更通知解决发现和契约交换，不保证语义选择正确，也不替应用授予权限。

面试应区分现状与计划：已有统一参数执行边界和有界loop；动态候选JEV选择是下一版演进方案，尚不能称为已经验证的新工具零样本泛化能力。
