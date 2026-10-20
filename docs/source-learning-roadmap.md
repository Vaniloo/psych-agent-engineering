# 源码学习路线：架构与模型训练

适用版本：`freeze-20261020-attune`。目标是沿源码解释设计、定位故障、读懂训练结果，并回答面试追问。建议安排 **10次学习，每次约90–120分钟**。最新入口以[根README](../README.md)、[精简后的训练导航](../agent-runtime/training/README.md)和[冻结说明](freeze-20261020.md)为准；下文保留早期学习题，历史实验命令不必逐个复现。

先读架构，再读训练。每次只跟一条数据流，采用“画流程 → 看关键函数 → 看对应测试 → 用自己的话解释”的顺序。首次跳过 UI 样式、DTO 全集和历史扩量脚本；遇到不懂的依赖再补读。

## 学习前的项目地图

| 区域 | 职责 | 当前实现 |
| --- | --- | --- |
| `frontend/` | 发请求、接 SSE、显示实际工具与目标结果 | 普通 JavaScript |
| `backend/` | 身份、会话归属、入场控制、业务事务 | Spring Boot |
| `agent-runtime/psych_agent_runtime/` | 状态图、工具循环、检索、证据和生成 | Python / LangGraph |
| 同目录下的模型 worker | 有限候选预测、校准和拒判 | Laya / Qwen |
| `agent-runtime/training/` | 数据、训练、评测、发布检查 | 离线训练代码，不参与正常请求 |
| `docs/` | 当前设计与按日期保留的实验结果 | 当前配置不能从早期实验文档推断 |

当前三个主角色：Router为Qwen4B；工具Executor为ToolACE Qwen4B、Laya保留回退；Memory为Qwen4B。目标门控和事实生命周期有独立适配器，记忆与知识分别用原版0.6B重排；生成服务独立。工具训练先读`training/public_tools`，其余训练入口按导航按需展开。角色数、基座数和worker数量要分开理解。

## 第一部分：系统架构（5 次）

### A1：沿一次普通聊天走完整链路

**问题：用户输入最终如何变成一条保存成功的回复？**

按顺序阅读：

1. [架构图](architecture.md)、[根 README](../README.md)。
2. `frontend/app.js` 中发送聊天和消费流式事件的路径。
3. `backend/src/main/java/com/vanilo/psych/agent/controller/AgentController.java` → `service/AgentGatewayService.java` → `service/AgentRuntimeClient.java`。
4. `agent-runtime/psych_agent_runtime/api.py` → `graph.py` 的 `AgentState`、`_build_graph`、`stream`、`_persist_turn`。

不用展开每个节点，先画出 **浏览器 → Spring → Runtime → 模型/工具 → Runtime → Spring 保存 → 浏览器事件**。指出 username、sessionId、requestId 分别来自哪里，以及何时产生 token、何时才能认定消息保存成功。

配套测试：`backend/src/test/java/com/vanilo/psych/agent/service/AgentRuntimeContractTests.java`、`agent-runtime/tests/test_tool_activity.py`。

**完成标准：** 不看代码，能讲清一次请求跨了哪些服务；能解释 HTTP/SSE 成功为什么不等于目标、记忆或消息已保存。

### A2：读懂 Router 与有界 Agent loop

**问题：谁决定权限，谁决定下一步，循环如何结束？**

阅读 `graph.py` 的 `_planner`、`_loop_initialize`、`_loop_decide`、`_loop_tool`、`_loop_observe`、`_after_loop_decision`，随后读 `planner.py`、`route_policy.py` 和 `tool_controller.py` 的 `start_loop/decide/execute/observe`。

跟踪“解释一下焦虑，再回顾我今天的心情”：先预测路线、映射权限，再从合法工具和 finish/clarify 中选择，执行结果进入 observation，下一步使用更新后的状态。

记录工具预算、期限、参数去重、拒判回退和一次查询修正分别在哪里；修正为什么只能改查询，不能改账号、业务 ID 或写入权限。

配套测试：`test_agent_loop.py`、`test_runtime_boundaries.py`、`test_policy_provenance.py`（均在 `agent-runtime/tests/`）。

**完成标准：** 画出实际 loop；解释规则保障与模型预测的差别；指出危机路径为何绕过自由循环。

### A3：工具与目标写入：从预测到可信业务结果

**问题：模型说“创建好了”，如何保证真的创建好了？**

阅读 `capabilities.py`、`mcp_client.py`、`goal_operations.py`，按需要展开 `goal_creation.py`、`goal_progress.py`、`goal_evidence.py`。再读 Java 的 `ConversationMemoryService.java`、`GoalCreationService.java` 和 `GoalService.java`。

用“创建一个睡前读一页书的目标”跟踪 **begin-turn 原始凭据 → 候选与判断 → 业务校验/事务 → 实际回执 → 回复 → persist-turn**。再对照“我打算明天读”和“我刚读了一页”，说明计划与事实如何区分。

重点理解所有权、原话、行动 ID、乐观版本、requestId 幂等，以及写入响应丢失时为什么不能随意重放。

配套测试：`agent-runtime/tests/test_goal_creation.py`、`test_goal_progress.py`；Java 的 `AgentPersistenceIntegrationTests.java`、`TurnPersistenceTests.java`。

**完成标准：** 能解释读 loop 与受控写入的边界；能说明目标已写入但最终回复保存失败这一未完全恢复的边界。

### A4：跨会话记忆与公共知识 RAG

**问题：系统在查用户事实，还是查公共知识？检索失败在哪一层？**

记忆顺序：`memory.py` 的 `MemoryStore.ingest/candidates/recent` 与 `MemoryService.retrieve/_retrieve_qwen/_worker` → `personal_recall.py` → `context_recall.py` → `memory_scope.py`、`memory_source_guard.py` → `memory_reranker.py`。

公共知识顺序：`knowledge_corpus.py` → `chunking.py` → `rag.py` 的 `upsert_document/search_with_metrics/_merge_candidates/_rank`；结合 [RAG 链路](rag-pipeline.md)。

画两条流水线：

- 个人原话：消息事务与 outbox → 派生索引 → BM25/近期语义候选 → 重排 → 4B 相关性/充分性 → 来源和日期约束。
- 公共知识：原文 → 分块 → embedding/Chroma 与 BM25 → 合并排序 → 带来源的知识片段。

配套测试：`test_personal_recall.py`、`test_memory_batch.py`、`test_memory_reranker.py`、`test_knowledge_corpus.py`。

本分支的生命周期实验另读 `memory_facts.py` → `memory_shadow.py` → `memory_projection.py` → `memory_ledger.py` → `memory_read.py`，结合`test_memory_projection.py`、`test_memory_read.py`和 [第四阶段报告](memory-read-v1-20261016.md)。练习说明：为什么片段价值与完整来源核对要分开，为什么撤销后版本不能倒退，为什么只增加新事实召回还会被旧原话污染，以及如何让近期情绪独立于长期保留门槛。当前只对专用合成账号启用。

练习使用 [50 用户测试中的记忆诊断](launch-simulation-20261014.md)：原话存在，单独给 4B 能判对，却没有进入候选池。逐段解释为什么这时应先改召回，而不是先训练。

**完成标准：** 区分“没路由、没执行、没召回、被筛掉、证据被裁剪、生成没使用”；解释 outbox 的最终一致性，以及个人记忆目前不是全历史向量检索。

### A5：回复、可观察性与上线边界

**问题：如何知道回复用了什么、哪里慢、哪里失败？**

阅读 `response.py`、`response_prompt.py`、`response_context.py`、`token_budget.py`、`tool_activity.py`；再看 `frontend/tool-activity.js`、`metrics-view.js`，以及网关的执行池、拒绝和降级路径。

用一个实际 trace 对照：路由耗时、工具耗时、首 token、完整回复、保存状态。理解证据优先的上下文预算、历史助手回答污染、首 token 前后失败的不同处理。

配套测试：`test_response_evidence.py`、`test_pipeline_metrics.py`、`test_launch_scenario.py`；阅读 `benchmarks/launch_scenario.py` 和 [最新压力报告](launch-simulation-20261014.md)。

**完成标准：** 能区分在线用户数、同时推理数、RPS、TTFT 和完整耗时；准确解释“50 账号、每 120 秒交互、四分钟 100/100 成功”，以及“50 同时请求接纳 8、拒绝 42”的不同含义。

## 第二部分：模型训练（5 次）

### M1：先理解预测任务和契约

**问题：到底训练模型输出什么？它为什么不能自由生成工具？**

阅读 `decision_contract.py`、`qwen_choice_contract.py` 的 `normalize/choices/render`、`qwen_memory_contract.py` 的 `normalize/render`，然后对照 `tests/test_qwen_choices.py`、`test_qwen_memory_finetune.py`。

手写三个样本，各包括输入状态、候选、标签和拒判后的应用行为：

- Router：route / conversation_kind / risk_band / support_need。
- Executor：next_action 与独立的 goal_creation / goal_action / goal_outcome。
- Memory：相关性、充分性、事件/事实/偏好/方法分类、时间需求。

**完成标准：** 能说明任务、候选、概率、阈值、业务操作之间的关系；理解 JEV/Laya 的有限候选思路与当前 Qwen 适配的关系，不能把项目描述成完整 JEV-Mem 实现。

### M2：数据构造与防泄漏

**问题：一段对话怎样变成训练样本，标签有多可靠？**

阅读 `training/decision_data.py`、`build_domain_decisions.py`、`prepare_dialogue_decisions.py`、`annotate_decisions.py`，重点展开 `qwen_choice_data.py` 和 `qwen_memory_data.py` 的 `build`。

亲手跟踪一条源行如何完成任务展开、规范化输入、合法标签校验、冲突去除、family 关联、跨 split 去重和 challenge 排除。理解 teacher_draft、reviewed、程序策略标签和独立人工金标的区别。

配套测试：`test_dialogue_preparation.py`、`test_decision_training.py`、`test_qwen_choices.py`、`test_qwen_memory_finetune.py`。

**完成标准：** 画出 train / calibration / validation / test / challenge 的各自用途；解释为什么同一场景改写后随机切分仍可能泄漏，也为什么“源对话数”不等于“展开决策数”。

### M3：当前 Qwen 4B LoRA 的训练核心

**问题：训练的是生成能力，还是有限候选的判断分数？**

先读二分类 `training/train_qwen_memory.py`，再读多候选 `train_qwen_choices.py`，重点看输入渲染、yes/no token、LoRA 配置、loss、梯度累积、保存与预测；最后对照旧 `train_decisions.py`，理解 Laya 路径与 Qwen 路径的差别。

这里的 4B 基座为 **Qwen3-Reranker-4B**。Memory 使用最后位置的 no/yes logits 做二分类交叉熵；Router/Executor 给每个合法候选渲染输入，以 `s_i = logit(yes) - logit(no)` 得到候选分数，再在整组候选上做多分类交叉熵。不是让模型生成一段 JSON 再解析标签。

LoRA 冻结基座，训练投影层低秩增量；对应实验 r16、alpha32、dropout 0.05。有效批量要结合 micro batch 和累积步数理解，多候选任务的 forward 数还取决于候选数量。

结合 [Memory 微调报告](qwen-memory-finetune-20261012.md) 与 [Router/Executor 微调报告](qwen-router-executor-finetune-20261012.md)，查清数据规模、一次遍历、学习率、显存、优化耗时和全流程耗时。

**完成标准：** 能解释 loss 从哪里来、哪些参数会更新、为什么三个角色分别训练；能说明 0.6B 只负责原始重排，没有在本项目微调。

### M4：校准、拒判与效果评测

**问题：模型“很有把握”可靠吗？高准确率是否意味着可上线？**

阅读两份 Qwen 训练器的 `calibrate/aggregate`，再看 `qwen_choice_challenge.py`、`qwen_memory_challenge.py` 和 `evaluate_readiness.py`。结合 [模型工程](model-engineering.md)、[校准审核](calibration-review-round12.md)。

候选分数经温度处理：`p_i = softmax(s_i / T)`；二分类使用 logit 差对应概率。温度用 calibration 拟合，接受阈值用 validation 选择；challenge 不用于调权重或降低阈值。

手算一个例子：100 项判断，70 正确接受、5 错误接受、25 弃权。接受覆盖率为 75%，被接受的判断中错误比例为 5/75；25 项弃权本身不告诉你原始分类是否正确，所以无法据此计算原始准确率。

同时看错误类别、稀少标签、不同来源、端到端检索与延迟。模型正确、规则保护成功、最终回复正确要分别计数；开发挑战和模板 test 不等于独立真实用户准确率。

**完成标准：** 能解释温度、阈值、校准集和拒判；能读懂正确接受/错误接受/弃权，说明误差与覆盖率的取舍。

### M5：从检查点到实际部署，以及为什么混合部署

**问题：训练完成的模型如何成为真正在线使用的模型？**

阅读 `training/freeze_deployment.py`、`promotion_gate.py`、`release_readiness.py`；随后读在线 `qwen_choice_service.py`、`qwen_memory_service.py`、`routing_service.py`、`decision_router.py` 与 `config.py` 的角色配置。

区分 Laya 的冻结入口与 Qwen 训练器生成的适配器/部署清单，查出各自如何固定基座、权重、数据、renderer 和契约摘要，如何验证错误版本、缺权限、超预算和不可用。发布 gate 不是所有 worker 必然共用的一个自动入口，应沿各实际接入代码确认。

结合 [Router 切换与 Executor 分工](router-executor-serving-20261012.md)、[Memory 批量服务](qwen-memory-serving-20261012.md)，解释为什么目标判断用 4B，而工具选择保留 Laya：分任务收益、延迟与保护边界共同决定，参数大并不意味着每项都值得替换。

配套测试：`test_freeze_provenance.py`、`test_promotion_gate.py`、`test_executor_split.py`。

**完成标准：** 能画出“数据 → 训练 → 校准 → 阈值 → 冻结评测 → 候选 HTTP 验证 → 应用对照 → 接入/回滚”；能区分训练成功、候选通过和实际启用。

## 每次学习留下什么

每次做一页笔记即可，统一写五项：

1. 入口和输出：只记录 2–3 个关键函数。
2. 状态变化：数据从哪里来，如何改变，存在哪里。
3. 一次成功路径和一次失败路径。
4. 一项设计取舍及其代价。
5. 一个仍不懂的问题，下一次沿调用关系补查。

学习测试优先使用已有合成 fixture 与 Mock。不要用真实账号制造练习数据，不要为理解训练器重复启动服务器训练，也不要把私有配置、用户原话或密钥放入学习笔记。

## 最后一次自测

用 60–90 分钟完成三项，过程中不背文件名：

- **架构讲解：** 用“会话 A 表达心情，会话 B 回顾当天心情”讲清身份、路由、loop、检索、判断、生成、保存；画出边界。
- **训练讲解：** 用一条目标行动匹配样本讲清候选渲染、loss、LoRA、校准、拒判和部署；引用对应日期的实验范围。
- **故障诊断：** 分析“工具没调”“原话没召回”“目标没有真正保存”“50 用户高峰”四类问题，先提出可验证的定位步骤，再提出优化方向。

能独立完成这些，再用 [面试讲解稿](interview-guide.md) 压缩成 30 秒和 3 分钟版本。核心是讲明实现与证据，不需要记住全部历史训练轮次。
