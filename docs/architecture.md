# 总架构 · 一条心理支持对话

**主模型用原生 function calling 自己决定调哪个工具、写什么查询词；宿主负责安全护栏、身份注入、调用预算和超时；ToolACE LoRA 在每一步之前给候选工具打分，只把比“直接回复”分数高的工具交给主模型。**

## 部署和数据

```mermaid
flowchart LR
  UI[浏览器] -->|JWT 请求 / SSE 响应| J[Spring Boot 网关]
  J --> SQL[(MySQL：会话、消息与记忆任务回执)]
  J --> R[Python LangGraph Runtime]
  R -->|每步工具预筛评分| Q[GPU 上的 Qwen + ToolACE LoRA]
  R -->|风险复核、function calling、记忆摘要| L[DeepSeek 或 Ollama]
  R --> T[工具：本地调用或 MCP]
  R --> C[(Redis：检查点和缓存)]
  T -->|用户业务读写| J
  T --> M[(SQLite：按账号的原话索引)]
  T --> K[(Chroma：知识分块和向量)]
  T -->|授权的公开查询| S[SearXNG]
```

Spring Boot 通过 MyBatis 读写 MySQL 中的会话、消息、记忆任务等业务数据。一次对话结束时，两条消息和待处理的记忆回执在同一个 Spring 事务中提交。重试时按账号和请求 ID 查找已保存的回执，避免重复写入。Flyway 管理表结构。[Mapper 接口](../backend/src/main/java/com/vanilo/psych/agent/repository) · [数据库基线](../backend/src/main/resources/db/migration/V1__application_schema.sql)

后台消费者读取 outbox 中的记忆任务：先把用户原话连同 embedding 写进 SQLite 索引，再让模型把这一轮并入会话摘要、中期摘要、长期记忆和用户画像，完成任务时把更新一起交给 Java，Java 在同一个事务里写入并标记完成，重试不会重复写。[记忆写入](../agent-runtime/psych_agent_runtime/memory_writer.py)

知识库切块后用 embedding 写入 Chroma，BM25 索引在 Runtime 内存里常驻。Docker 服务与 GPU 评分服务的地址、端口分开配置。[部署配置](../docker-compose.yml) · [启动说明](../HANDOFF.md)

## 一条请求怎么走

```mermaid
flowchart TD
  A[网关：JWT、限流、关键词危机检查、并发闸门] --> L[load_context：摘要、画像、最近消息]
  L --> G{guard：确定性扫描 + 模型风险复核}
  G -->|crisis| C[固定危机回复与援助资源]
  G -->|ordinary / professional| P[prepare：系统提示 + 历史；professional 先读援助资源]
  P --> S[ToolACE 预筛本步可用工具]
  S --> M[主模型流式输出：文字或 tool_calls]
  M -->|tool_calls| T[tools：校验参数、绑定身份、去重、预算、并行执行]
  T --> S
  M -->|文字| R[持久化消息与记忆任务]
  C --> R
  R --> Z[记录运行指标]
```

`guard` 先用 `SafetyScanner` 做确定性扫描，明确的本人危机表达直接进入危机流程。否则让模型给出风险等级，与扫描结果合并成三档：ordinary、professional、crisis。[安全护栏](../agent-runtime/psych_agent_runtime/guard.py)

主循环是标准的 function calling：模型看到工具定义，自己决定调不调、调哪个、查询词怎么写。工具结果作为 `tool` 消息回填，模型可以据此换个查询词再查，或者直接回答。宿主只做模型做不了也不该做的事：注入账号身份、限制可用工具、去重、限制总调用次数和时间。[图与循环](../agent-runtime/psych_agent_runtime/graph.py) · [工具定义](../agent-runtime/psych_agent_runtime/tools.py)

| 用户意图 | 系统怎么处理 | 能拿什么验收 |
| --- | --- | --- |
| 闲聊、支持资源 | 闲聊可以直接回答；支持资源按授权从业务工具读取。危机分支走专门支持响应。 | 运行轨迹中的路线、工具与回复来源。 |
| 回忆自己的经历 | 模型调用 `recall_memory`，自己写查询词；宿主绑定账号。本人原话和尚未入索引的近期消息组成候选池，BM25 与向量相似度各排一次，用 RRF 融合。 | 原话、会话、时间；换账号后证据应隔离。[Memory](../agent-runtime/psych_agent_runtime/memory.py) |
| 查询心理知识 | 模型调用 `search_knowledge`。Chroma 向量和常驻内存的 BM25 各召回一路，用 RRF 融合，每篇文档最多两块。结果标注证据强弱（strong / weak / none）；偏弱时模型改写查询再查，每轮最多两次。 | 运行记录里的 `knowledge.rounds`、每轮查询词和证据强弱，以及回答引用。[混合检索](../agent-runtime/psych_agent_runtime/rag.py) |
| 查询公开网页 | 显式授权后，SearXNG 先查主引擎，再按需查备用组，只返回摘要和链接。 | 实际返回的 URL 和摘要，以及补查记录。[联网搜索](../agent-runtime/psych_agent_runtime/web_search.py) |

## ToolACE 工具预筛

预筛模型的输入是已有对话、已执行的工具调用和结果，以及候选工具的描述与参数结构；它给每个候选和“直接回复”各算一个 yes/no logit 差。分数比“直接回复”高的工具才交给主模型，宿主要求必读的工具始终保留；评分服务不可用或返回的分数对不上时，交出全部工具。[预筛](../agent-runtime/psych_agent_runtime/prefilter.py) · [训练代码](../agent-runtime/training/README.md)

公开测试集上的 93.36% 衡量的是“是否选中示范里的下一步”，不等于这个应用里工具选择的准确率。

## 设计取舍

- **为什么让模型自己选工具。** 早期版本用规则先判断意图、再决定必须读哪些工具，规则越补越多，彼此还会冲突。改成 function calling 后，“要不要查、查什么”交给模型，宿主只保留安全相关的硬约束，规则从几百行降到几十行。
- **为什么还要预筛。** 工具列表越短，主模型越不容易乱调工具，每步的提示也越短。预筛失败时直接退回全量工具，所以它只是优化，不会成为单点故障。
- **为什么记忆写入放在后台。** 回复延迟不受摘要生成影响；摘要失败只是跳过一次更新，原话索引照常写入。代价是新内容要几秒后才能检索到，所以检索时会补读 Java 里最新的消息。
- **为什么危机判断有两层。** 网关的关键词拦截不依赖 Python 服务，Runtime 挂了也能返回援助信息；Runtime 里的模型复核负责关键词覆盖不到的含蓄表达。

## 已知限制

- 限流先 `INCR` 再 `EXPIRE`，两步不是原子操作；Redis 出错时默认放行。
- 对话请求的 Request ID 由网关生成，前端重复提交同一条消息时无法去重；现在的幂等只覆盖 Runtime 回调网关时的重试。
- 原话索引中较早的记录没有向量，只参与 BM25。
- 目前的测试用假模型和假检索库验证流程，还缺少接真实模型的端到端评测。
