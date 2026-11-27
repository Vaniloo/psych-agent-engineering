# 阅读路线 · 跟着一次请求读懂代码

用同一个例子贯穿全程：用户上次说过“面试前我喜欢散步”，这次开新会话问“你记得我怎么放松吗？”。每一步先预判程序会做什么，再对照代码，读完回答右边的问题。**只有 30 分钟的话，先读第 1、2、3、6、12、14 步。**

Python 文件在 `agent-runtime/psych_agent_runtime/`，Java 文件在 `backend/src/main/java/com/vanilo/psych/agent/`。

## 第一轮 · Agent 主线（约 40 分钟）

| 步骤 | 读哪里 | 读完回答 |
| --- | --- | --- |
| 1 | [graph.py](../agent-runtime/psych_agent_runtime/graph.py) 文件头和 `_build_graph` | 一次请求经过哪几个节点？`agent` 和 `tools` 怎么循环、什么时候结束？ |
| 2 | 同上，`_agent` 和 `_execute` | 模型返回 tool_calls 之后宿主做了哪几道检查？调用预算、步数上限、单工具次数上限分别防什么？ |
| 3 | [tools.py](../agent-runtime/psych_agent_runtime/tools.py) | 模型能填哪些参数？`username` 从哪来？证据偏弱时模型会看到什么？ |
| 4 | [llm.py](../agent-runtime/psych_agent_runtime/llm.py) 的 `stream_step` | 流式返回时，tool_calls 的参数怎么一片片拼起来？ |
| 5 | [prompts.py](../agent-runtime/psych_agent_runtime/prompts.py) | 哪些行为写在提示里交给模型，哪些在代码里强制执行？ |
| 6 | [guard.py](../agent-runtime/psych_agent_runtime/guard.py) → [safety.py](../agent-runtime/psych_agent_runtime/safety.py) 的 `scan` | 模型说低风险但关键词命中了怎么办？转述朋友的话为什么不会升级成危机？ |
| 7 | [prefilter.py](../agent-runtime/psych_agent_runtime/prefilter.py) | ToolACE 在哪一步起作用？什么情况下会被绕过？ |

配套读 [test_agent.py](../agent-runtime/tests/test_agent.py)，每个测试就是一个完整场景。

## 第二轮 · Java 网关与事务（约 30 分钟）

| 步骤 | 读哪里 | 读完回答 |
| --- | --- | --- |
| 8 | `security/JwtAuthenticationFilter.java`、`config/SecurityConfig.java` | `/internal/agent/**` 为什么放行？它靠什么鉴权？（看 `InternalServiceAuthenticator`） |
| 9 | `config/RateLimitInterceptor.java`、`config/AsyncConfig.java` | 这个限流有什么漏洞？队列满了会怎样？ |
| 10 | `service/AgentGatewayService.java` 的 `chat`、`stream`、`handleCrisis` | Semaphore 拿不到许可时怎么处理？Runtime 挂了返回什么？ |
| 11 | `service/AgentRuntimeClient.java` | 超时和连接中断分别在哪里处理？ |
| 12 | [ConversationMemoryService.java](../backend/src/main/java/com/vanilo/psych/agent/service/ConversationMemoryService.java) 的 `rememberAgentTurn`、`completeMemoryJob` | 事务边界在哪？重试为什么不会重复写入？为什么先锁用户、再锁任务？ |
| 13 | `repository/AgentTurnReceiptMapper.java`、[V1 建表脚本](../backend/src/main/resources/db/migration/V1__application_schema.sql) | 去重靠的是数据库的哪一层机制？ |

配套读 `backend/src/test/.../AgentPersistenceIntegrationTests.java`。

## 第三轮 · 检索与记忆（约 30 分钟）

| 步骤 | 读哪里 | 读完回答 |
| --- | --- | --- |
| 14 | [rag.py](../agent-runtime/psych_agent_runtime/rag.py) 的 `search_with_metrics`、`_sparse_index`、`_evidence` | 两路召回怎么用 RRF 融合？BM25 索引何时重建？strong / weak 怎么判定？ |
| 15 | [bm25.py](../agent-runtime/psych_agent_runtime/bm25.py) | 中文为什么只切双字、不切单字？ |
| 16 | [memory.py](../agent-runtime/psych_agent_runtime/memory.py) 的 `rank`、`retrieve`、`_worker` | 候选池里有哪些来源？为什么近期消息不能按时间重排？ |
| 17 | [memory_writer.py](../agent-runtime/psych_agent_runtime/memory_writer.py) | 四层记忆各存什么？模型失败了会怎样？ |
| 18 | [web_search.py](../agent-runtime/psych_agent_runtime/web_search.py) | 为什么要过滤私网地址？缓存多久？谁决定一个账号能不能联网？ |

配套读 `test_rag.py`、`test_memory_recall.py`、`test_memory_writer.py`。

## 第四轮 · 微调模型（约 20 分钟）

| 步骤 | 读哪里 | 读完回答 |
| --- | --- | --- |
| 19 | [LoRA 微调详解](lora-walkthrough.md) 第 1–3 节 | 为什么用 reranker 当基座？一条原始记录怎么变成训练样本？ |
| 20 | [step2_convert.py](../agent-runtime/training/lora/step2_convert.py) 的 `parse_calls` → [step3_samples.py](../agent-runtime/training/lora/step3_samples.py) 的 `samples`、`split_by_group` | 解析失败为什么要整条丢掉？为什么输入只含当前动作之前的历史？为什么共享工具名或相同首问的样本要分在同一组？ |
| 21 | [step4_train.py](../agent-runtime/training/lora/step4_train.py) 的 `score`、训练循环 → [common.py](../agent-runtime/training/lora/common.py) 的 `listwise_loss` | yes/no logit 差怎么算？为什么用 listwise 而不是 BCE？梯度累积和学习率怎么调度？ |

可以先跳过的支撑代码：`capabilities.py`、`mcp_client.py`、`api.py`、`telemetry.py`、`observability.py`、`token_budget.py`、`chunking.py`。

## 评测数字怎么讲

| 评测 | 基座 → ToolACE LoRA | 说明 |
| --- | --- | --- |
| ToolACE 公开测试集 | 80.65% → 93.36% | 下一动作与示范一致的比例，1,204 个测试状态。 |
| 项目首动作 | 26/44 → 36/44 | 22 个项目问题各换两种候选顺序，衡量在本应用候选上的迁移。 |
| 隔离图回放 | 11/12 → 10/12 | 微调后少通过一例：公共数据上的提升不能直接推成端到端提升。 |

后两项是在旧架构下测的：当时 LoRA 直接选下一步，现在改为工具预筛，需要在新链路上重新评测。[公开训练报告](https://github.com/Vaniloo/psych-agent-engineering/blob/35bd857284a6b9d80c342aa27bc177e8273f4556d1dec2af8d2897ab3dc39/docs/public-tool-next-action-20261019.md) · [项目回放报告](https://github.com/Vaniloo/psych-agent-engineering/blob/35bd857284a6b9d80c342aa27bc177e8273f4556d1dec2af8d2897ab3dc39/docs/public-tool-project-replay-20261019.md)

## 30 秒口述

Attune 是一个心理支持对话 Agent，能回顾用户自己说过的话、检索心理知识，并在授权后联网查资料。Spring 负责账号、会话和事务，用 Outbox 把对话和记忆任务放在同一个事务里提交；Python 用 LangGraph 跑 function calling 循环，模型自己选工具、写查询词，宿主负责注入身份、限定工具和控制预算。进入循环前有一道安全护栏，危机消息直接走固定流程。我用 ToolACE 对 Qwen3-Reranker-4B 做了 LoRA 微调，作为每一步的工具预筛，只把值得调用的工具交给主模型。知识检索是向量加 BM25 的 RRF 融合，证据偏弱时模型会改写查询再查一次；记忆由后台模型增量维护摘要和画像，和 Outbox 完成在同一事务里提交。
