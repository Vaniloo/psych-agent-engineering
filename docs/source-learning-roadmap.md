# 15天源码学习路线：模型训练与应用架构

基准：`freeze-20261020-attune` / `48cfcb9`。本学习文档在冻结之后更新；学习对象仍固定为这份源码。预计每天2～3小时，总计约30～45小时。目标是能沿源码追踪、解释取舍、定位常见失败，并完成项目面试讲解。

## 推进约定

- **第1～8天：模型训练。第9～15天：应用架构。** 训练沿一条ToolACE数据流先走通，再对比其他角色；应用沿一条请求流走通，再展开工具和证据。
- 每天按表内顺序读。聊天讲解一次只展开一个文件的一段或一个函数，先交代输入和输出，再逐段解释、跟一个样本、核对测试。
- 主读文件只阅读指定函数。遇到辅助函数按调用关系补读；延伸内容列在当日备注，不提前跳入后续章节。
- 完成标准由你能否复述和完成小练习判断，不以我读过源码、测试通过或天数过去作为你已经掌握的证明。
- 每天建议：回顾15分钟、跟源码60～90分钟、小练习/已有测试30分钟、笔记与复述15～30分钟。第5天可优先留3小时。
- 使用现有合成fixture和离线测试。学习过程无需启动新GPU训练、教师API、全量下载或真实账号写入。

当前进度：**D01，待从 `fetch.py` 的 `SOURCES` 开始；尚未标记任何一天完成。**

## 总览

| 天 | 部分 | 核心问题 |
| --- | --- | --- |
| 01 | 模型训练 | [获取固定版本的数据](#d01) |
| 02 | 模型训练 | [把原始对话转换成统一事件](#d02) |
| 03 | 模型训练 | [展开决策样本并划分数据集](#d03) |
| 04 | 模型训练 | [定义模型真正看到的输入](#d04) |
| 05 | 模型训练 | [训练预检、LoRA和损失反传](#d05) |
| 06 | 模型训练 | [对比Router、基础Memory与校准](#d06) |
| 07 | 模型训练 | [长期事实与目标门控的专门任务](#d07) |
| 08 | 模型训练 | [评测、重载与在线服务](#d08) |
| 09 | 应用架构 | [一条消息进入Java与Runtime](#d09) |
| 10 | 应用架构 | [权限与有界Agent Loop](#d10) |
| 11 | 应用架构 | [注册表、MCP与联网工具接入](#d11) |
| 12 | 应用架构 | [记忆写入、后台处理与事实更新](#d12) |
| 13 | 应用架构 | [跨会话召回与证据覆盖](#d13) |
| 14 | 应用架构 | [Agentic RAG与回复上下文](#d14) |
| 15 | 应用架构 | [目标闭环与最终讲解](#d15) |

## 每日阅读清单

<a id="d01"></a>

### D01 · 获取固定版本的数据

主读顺序：

1. [agent-runtime/training/public_tools/fetch.py](../agent-runtime/training/public_tools/fetch.py)：`SOURCES → fetch → verify`。

配套测试：[test_public_tools.py](../agent-runtime/tests/test_public_tools.py)。

当天产出：手写ToolACE来源记录，说明repository、revision、dataFile、license分别控制什么；列出下载目录与manifest的关系。

完成前核对：

- 为什么只记仓库名不够？
- revision、文件SHA256和文件大小各自检查什么？

阅读边界：暂不下载完整数据，不读模型、loss或GPU代码。先只读SOURCES，再读fetch，最后读verify。

<a id="d02"></a>

### D02 · 把原始对话转换成统一事件

主读顺序：

1. [agent-runtime/training/public_tools/convert.py](../agent-runtime/training/public_tools/convert.py)：`source_tools → parse_toolace_calls → convert_record`。

配套测试：[test_public_tools.py](../agent-runtime/tests/test_public_tools.py)。

当天产出：用测试中的record构造器，逐项对照原始对话与converted记录，标出tools、events、original、parseIssues和unknown_not_negative。

完成前核对：

- 助手文字为什么不能统一标成finish？
- 解析不了工具调用时为什么保留原文并标记问题？

阅读边界：literal和参数解析按调用关系展开，先跟一个成功样本，再看一个解析失败样本。

<a id="d03"></a>

### D03 · 展开决策样本并划分数据集

主读顺序：

1. [agent-runtime/training/public_tools/course.py](../agent-runtime/training/public_tools/course.py)：`decisions → keys → assign_components → build`。
2. [agent-runtime/training/public_tools/verify_interchange.py](../agent-runtime/training/public_tools/verify_interchange.py)：`check`。

配套测试：[test_public_tool_course.py](../agent-runtime/tests/test_public_tool_course.py)。

当天产出：从一段包含两次工具调用的对话画出决策时刻；手写一个context/candidateTools/targetIndices样本，再画共享工具名如何联结样本。

完成前核对：

- 源对话数、决策状态数和候选评分输入数有什么差别？
- 当前分组隔离保证了哪些键不跨split，又没有保证什么？

阅读边界：只跟ToolACE主来源。APIGen-MT留作来源与许可差异的补充阅读。

<a id="d04"></a>

### D04 · 定义模型真正看到的输入

主读顺序：

1. [agent-runtime/psych_agent_runtime/public_tool_contract.py](../agent-runtime/psych_agent_runtime/public_tool_contract.py)：`INSTRUCTION → TEXT → render → specification`。

配套测试：[test_public_tool_course.py](../agent-runtime/tests/test_public_tool_course.py)、[test_public_tool_serving.py](../agent-runtime/tests/test_public_tool_serving.py)。

当天产出：手写两个工具和TEXT候选，用同一行数据分别渲染候选0和候选1；圈出共同上下文、全部候选和待评分候选。

完成前核对：

- 输入里哪些内容是任务说明，哪些是样本数据？
- 为什么候选描述能变化，工具数量不要求扩固定分类头？

阅读边界：先理解输入结构和输入中没有标签。loss留到第5天。

<a id="d05"></a>

### D05 · 训练预检、LoRA和损失反传

主读顺序：

1. [agent-runtime/training/public_tools/train.py](../agent-runtime/training/public_tools/train.py)：`encode → prepare → run中的score和训练循环`。
2. [agent-runtime/training/public_tools/contract.py](../agent-runtime/training/public_tools/contract.py)：`loss`。

配套测试：[test_public_tool_course.py](../agent-runtime/tests/test_public_tool_course.py)。

当天产出：分两段读：前半段画token预算与样本排除流程；后半段标出LoraConfig、yes/no logits、loss、backward和optimizer.step。手算两候选的CE。

完成前核对：

- 哪些参数更新，哪些参数冻结？
- 候选数、微批和梯度累积怎样影响实际工作量？

阅读边界：这是训练部分最重的一天，可用3小时。只阅读与跑离线测试，不启动GPU训练。

<a id="d06"></a>

### D06 · 对比Router、基础Memory与校准

主读顺序：

1. [agent-runtime/psych_agent_runtime/qwen_choice_contract.py](../agent-runtime/psych_agent_runtime/qwen_choice_contract.py)：`normalize → choices → render`。
2. [agent-runtime/psych_agent_runtime/qwen_memory_contract.py](../agent-runtime/psych_agent_runtime/qwen_memory_contract.py)：`INSTRUCTIONS → normalize → render`。
3. [agent-runtime/training/train_qwen_memory.py](../agent-runtime/training/train_qwen_memory.py)：`predictions → aggregate → calibrate`。

配套测试：[test_qwen_choices.py](../agent-runtime/tests/test_qwen_choices.py)、[test_qwen_memory_finetune.py](../agent-runtime/tests/test_qwen_memory_finetune.py)。

当天产出：同一个问题分别写Router候选标签和Memory的yes/no标签；画calibration、validation、test各自用途。按需对照train_qwen_choices的calibrate。

完成前核对：

- 候选间选择和逐记录二分类有什么不同？
- 为什么不能把ToolACE选择器说成已经训练了绝对适用性或做过项目概率校准？

阅读边界：训练主循环沿用第5天的认知，这天只看任务差异和校准，不重读完整训练器。

<a id="d07"></a>

### D07 · 长期事实与目标门控的专门任务

主读顺序：

1. [agent-runtime/psych_agent_runtime/memory_lifecycle_contract.py](../agent-runtime/psych_agent_runtime/memory_lifecycle_contract.py)：`INSTRUCTIONS → normalize → render`。
2. [agent-runtime/psych_agent_runtime/goal_gate_contract.py](../agent-runtime/psych_agent_runtime/goal_gate_contract.py)：`INSTRUCTIONS → normalize → render`。
3. [agent-runtime/training/goal_gate_data.py](../agent-runtime/training/goal_gate_data.py)：`scenarios → build`。

配套测试：[test_memory_lifecycle.py](../agent-runtime/tests/test_memory_lifecycle.py)、[test_goal_model_gate.py](../agent-runtime/tests/test_goal_model_gate.py)。

当天产出：分别写“旧偏好被明确更正”和“步骤做完并反馈太难”的输入、候选与判断任务；区分训练标签和业务写入授权。

完成前核对：

- 为什么基础Memory、生命周期和目标门控分别保留契约？
- 为什么同模板test高分不能代表真实表达可靠？

阅读边界：memory_lifecycle_data和memory_lifecycle_refinement只跟build入口及manifest；大量模板不逐条背诵。

<a id="d08"></a>

### D08 · 评测、重载与在线服务

主读顺序：

1. [agent-runtime/training/public_tools/verify_candidate.py](../agent-runtime/training/public_tools/verify_candidate.py)：`verify`。
2. [agent-runtime/psych_agent_runtime/public_tool_service.py](../agent-runtime/psych_agent_runtime/public_tool_service.py)：`create_app → predict → score`。
3. [agent-runtime/training/fetch_artifacts.py](../agent-runtime/training/fetch_artifacts.py)：`verify → install_archive → main`。

配套测试：[test_artifact_publication.py](../agent-runtime/tests/test_artifact_publication.py)、[test_public_tool_serving.py](../agent-runtime/tests/test_public_tool_serving.py)。

当天产出：对照ToolACE训练报告与20261020 manifest，画“训练输出→磁盘重载→权重与契约检查→HTTP评分→应用接入”；分别写示范一致率和端到端成功率的含义。

完成前核对：

- 磁盘重载分数一致能证明什么，不能证明什么？
- 模型文件、renderer或契约不一致时在哪里拒绝？

阅读边界：阶段复盘：独立讲完一条ToolACE样本从原始记录到服务评分的全过程。0.6B只阅读其固定原版身份，不安排训练。

<a id="d09"></a>

### D09 · 一条消息进入Java与Runtime

主读顺序：

1. [backend/src/main/java/com/vanilo/psych/agent/service/AgentGatewayService.java](../backend/src/main/java/com/vanilo/psych/agent/service/AgentGatewayService.java)：`chat → stream → streamRuntimeOrFallback`。
2. [agent-runtime/psych_agent_runtime/api.py](../agent-runtime/psych_agent_runtime/api.py)：`chat → stream_chat`。
3. [agent-runtime/psych_agent_runtime/graph.py](../agent-runtime/psych_agent_runtime/graph.py)：`AgentState → stream → _build_graph`。

配套测试：[AgentRuntimeContractTests.java](../backend/src/test/java/com/vanilo/psych/agent/service/AgentRuntimeContractTests.java)、[AgentGatewayFallbackTests.java](../backend/src/test/java/com/vanilo/psych/agent/service/AgentGatewayFallbackTests.java)。

当天产出：画浏览器→Java→Runtime→SSE→保存的时序图，标出username、sessionId和requestId由谁提供；按需跳读AgentController与AgentRuntimeClient。

完成前核对：

- 浏览器可否自行指定其他用户身份？
- 收到token、收到done与消息保存成功有什么差别？

阅读边界：前端app.js只追发送和消费SSE的调用点，CSS与组件布局留作延伸。

<a id="d10"></a>

### D10 · 权限与有界Agent Loop

主读顺序：

1. [agent-runtime/psych_agent_runtime/graph.py](../agent-runtime/psych_agent_runtime/graph.py)：`_planner → _loop_initialize → _loop_decide → _loop_tool → _loop_observe`。
2. [agent-runtime/psych_agent_runtime/route_policy.py](../agent-runtime/psych_agent_runtime/route_policy.py)：`plan_for_route → guarantee_requested_reads`。
3. [agent-runtime/psych_agent_runtime/tool_controller.py](../agent-runtime/psych_agent_runtime/tool_controller.py)：`start_loop → decide → execute → observe`。

配套测试：[test_agent_loop.py](../agent-runtime/tests/test_agent_loop.py)、[test_read_obligations.py](../agent-runtime/tests/test_read_obligations.py)。

当天产出：纸面走一轮复合查询，记录每次候选、调用参数、observation、剩余预算和停止原因。再让模型过早返回TEXT，观察应用如何处理必需读取。

完成前核对：

- 模型选择、宿主权限和必需读取分别由谁控制？
- 什么情况可以继续同一工具的新参数，什么情况会去重？

阅读边界：planner.plan只在需要解释Router调用时展开。固定危机路径单独标记。

<a id="d11"></a>

### D11 · 注册表、MCP与联网工具接入

主读顺序：

1. [agent-runtime/psych_agent_runtime/capabilities.py](../agent-runtime/psych_agent_runtime/capabilities.py)：`Capability → register_read → validate_arguments → CapabilityExecutor.execute`。
2. [agent-runtime/psych_agent_runtime/dynamic_tools.py](../agent-runtime/psych_agent_runtime/dynamic_tools.py)：`DynamicReadCatalog.refresh → _discover → authorized_extensions`。
3. [agent-runtime/psych_agent_runtime/mcp_client.py](../agent-runtime/psych_agent_runtime/mcp_client.py)：`call → _call`。

配套测试：[test_dynamic_tools.py](../agent-runtime/tests/test_dynamic_tools.py)、[test_units_mcp.py](../agent-runtime/tests/test_units_mcp.py)。

当天产出：使用测试fixture画一个新读取工具从发现、授权、描述评分、参数绑定到真实调用的流程；网络搜索视为这个工具层的一种实现。

完成前核对：

- 即插即用为什么仍需要宿主授权和Schema校验？
- 新增工具名、描述和输入Schema分别在哪一步被使用？

阅读边界：选读web_search.search和web_context.run了解两级网页筛选。角色语气搜索为已停用实验，不列入主线。

<a id="d12"></a>

### D12 · 记忆写入、后台处理与事实更新

主读顺序：

1. [backend/src/main/java/com/vanilo/psych/agent/service/ConversationMemoryService.java](../backend/src/main/java/com/vanilo/psych/agent/service/ConversationMemoryService.java)：`rememberAgentTurn`。
2. [agent-runtime/psych_agent_runtime/memory.py](../agent-runtime/psych_agent_runtime/memory.py)：`MemoryStore.ingest → MemoryService._worker`。
3. [agent-runtime/psych_agent_runtime/memory_shadow.py](../agent-runtime/psych_agent_runtime/memory_shadow.py)：`MemoryLifecycleShadow.inspect`。

配套测试：[TurnPersistenceTests.java](../backend/src/test/java/com/vanilo/psych/agent/service/TurnPersistenceTests.java)、[test_memory_update.py](../agent-runtime/tests/test_memory_update.py)、[test_memory_projection.py](../agent-runtime/tests/test_memory_projection.py)。

当天产出：画消息事务、outbox、近期原话索引、分类、生命周期判断、来源复核、投影写入及处理回执；沿调用点选读memory_projection.project和memory_ledger.apply。

完成前核对：

- 为什么消息已保存时，长期事实仍可能尚未更新？
- 原话、候选、事实账本和可撤销变更分别存什么？

阅读边界：先追一条新增事实，再追一条被否决的疑问句；完整ledger表结构留在追踪需要时展开。

<a id="d13"></a>

### D13 · 跨会话召回与证据覆盖

主读顺序：

1. [agent-runtime/psych_agent_runtime/personal_recall.py](../agent-runtime/psych_agent_runtime/personal_recall.py)：`personal_recall_request`。
2. [agent-runtime/psych_agent_runtime/memory.py](../agent-runtime/psych_agent_runtime/memory.py)：`retrieve → _retrieve_qwen → retrieve_context`。
3. [agent-runtime/psych_agent_runtime/memory_read.py](../agent-runtime/psych_agent_runtime/memory_read.py)：`select_candidates → MemoryReadView.assemble`。

配套测试：[test_cross_session_recall.py](../agent-runtime/tests/test_cross_session_recall.py)、[test_memory_read.py](../agent-runtime/tests/test_memory_read.py)、[test_memory_source_review.py](../agent-runtime/tests/test_memory_source_review.py)。

当天产出：用合成fixture复现“会话A给出事实，会话B询问”；分别制造未触发、未入候选、被模型筛除、上下文未覆盖四种失败，并写出定位证据。

完成前核对：

- 分类如何影响候选召回，是否已经证明独立准确率增益？
- 为什么没找到证据不能回答用户从没说过？

阅读边界：按需选读memory_types.diversify、memory_reranker和response_context，明确0.6B重排与4B证据判断的分工。

<a id="d14"></a>

### D14 · Agentic RAG与回复上下文

主读顺序：

1. [agent-runtime/psych_agent_runtime/rag.py](../agent-runtime/psych_agent_runtime/rag.py)：`upsert_document → search_with_metrics → _merge_candidates → _rank`。
2. [agent-runtime/psych_agent_runtime/knowledge_reranker.py](../agent-runtime/psych_agent_runtime/knowledge_reranker.py)：`KnowledgeReranker.scores`。
3. [agent-runtime/psych_agent_runtime/agentic_rag.py](../agent-runtime/psych_agent_runtime/agentic_rag.py)：`AgenticKnowledge.prepare → assess → validate_review`。

配套测试：[test_agentic_rag.py](../agent-runtime/tests/test_agentic_rag.py)、[test_knowledge_reranker.py](../agent-runtime/tests/test_knowledge_reranker.py)。

当天产出：画查询规划、双路召回、独立知识重排、证据审查、补检索与上下文交付；对照13/14来源命中的失败项，不把它写成回答准确率。

完成前核对：

- 为什么知识重排不能直接复用“仅用户本人事实”记忆指令？
- 候选池没有证据和排序太靠后分别应在哪层处理？

阅读边界：先追一次充分证据，再追一次partial；选读response.py和token_budget.py说明交付证据可能被预算裁剪。

<a id="d15"></a>

### D15 · 目标闭环与最终讲解

主读顺序：

1. [agent-runtime/psych_agent_runtime/goal_model_gate.py](../agent-runtime/psych_agent_runtime/goal_model_gate.py)：`inspect → _review_progress`。
2. [agent-runtime/psych_agent_runtime/goal_operations.py](../agent-runtime/psych_agent_runtime/goal_operations.py)：`create → sync_progress → manage`。
3. [backend/src/main/java/com/vanilo/psych/agent/service/GoalService.java](../backend/src/main/java/com/vanilo/psych/agent/service/GoalService.java)：`syncFromTurn → manageFromTurn`。

配套测试：[test_goal_advancement.py](../agent-runtime/tests/test_goal_advancement.py)、[test_goal_management.py](../agent-runtime/tests/test_goal_management.py)、[GoalReadProgressTests.java](../backend/src/test/java/com/vanilo/psych/agent/service/GoalReadProgressTests.java)。

当天产出：走完“创建→完成并反馈困难→调整下一步→partial回读→整目标收尾”。沿advance_goal调用点读GoalCoachService.advanceFromTurn，最后用30分钟做全项目口述与故障定位。

完成前核对：

- 模型判断、写前核对、签名、版本与幂等各保护什么？
- 如何用实际回执回答已保存，而不是只在聊天里口头承诺？

阅读边界：复盘运行指标和历史容量边界：在线账号数、推理并发、TTFT与完整耗时分别是什么。部署细节按冻结说明查证，不背所有历史数字。

## 每天的笔记只留五项

1. 今天的入口函数、输入、输出。
2. 一个样本经过函数前后的变化。
3. 一条成功路径和一条失败路径。
4. 一项设计取舍与实际代价。
5. 尚未理解的问题，下一次从哪里接着读。

第8天阶段验收：不看稿讲清“原始示范→统一事件→决策样本→候选输入→loss/LoRA→评测→服务”。

第15天最终验收：分别用3分钟讲清模型训练和应用架构；再定位工具漏调用、跨会话漏记忆、目标未落库、知识证据不足四类问题。说明结论所依据的测试和仍存在的限制。

## 后续每一轮怎样继续

你说“继续”时，从当前函数接着读。你对某一段有疑问时，停在该段用小例子解释；学完并完成核对后，再更新当前进度。第一轮只读D01的`SOURCES`，接着才是同文件的`fetch()`和`verify()`。

## 2026-11-08 精简源码对照

当前学习分支为 `refactor/study-core-20261108`。[清理记录](code-cleanup-20261108.md)解释保留的边界和删除的重复逻辑；[简历成稿](resume.md)按当前模型角色更新。学习 `graph.py` 时先读 `_build_graph`，再读 `invoke/stream` 共用的 `_run`；MCP 结果格式只需读 `mcp_result.py`；ToolACE SHA 读取统一在 `public_tools/fetch.py::sha256_file`。训练数据和模型身份校验仍是学习内容，不按“防御性代码”略过。
