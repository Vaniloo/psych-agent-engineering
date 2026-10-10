# 面试讲解提纲

## 30 秒版本

这是一个 Java 业务网关 + Python LangGraph Runtime 的心理支持 Agent。Spring 负责 JWT、用户数据、安全前置和事务；LangGraph 负责状态图、SSE、上下文预算、独立记忆证据控制和 Redis checkpoint；Router 与 Executor 分开授权和选工具，工具默认同进程、可选 MCP Streamable HTTP 调用；RAG 使用 Chroma dense retrieval 和中文 BM25 sparse retrieval 融合。所有高风险规则都在模型之前执行，Runtime 故障时返回固定不可用回复；旧 Java Agent 已删除。

## 3 分钟版本

按四个问题讲：

1. **为什么拆服务？** 业务一致性与模型编排生命周期不同，Spring 不应该被长连接模型请求拖成 Agent 框架，Python 生态又不该重写用户权限和告警事务。
2. **RAG 为什么不是只查向量？** 中文短查询和术语对 BM25 更友好，语义改写对 dense 更友好；融合比单路稳，且引用有分数、来源与阈值。
3. **长会话怎么办？** 先分配上下文预算，保留结构化摘要和最新消息；原始消息与记忆任务同事务保存，后台独立 Laya 分类，检索时再筛选证据。
4. **出错怎么办？** MCP 本地 fallback、Redis cache 旁路、checkpoint 内存回退、Router/Executor/Memory 独立受限回退；Runtime 在首 token 前失败返回固定不可用回复，避免双回复。

## 面试官常追问

### 为什么用 LangGraph，不直接 while-loop？

固定节点、条件边、状态 Schema、checkpoint 和 stream mode 都是显式的。安全、持久化这类必须执行的节点不依赖模型选择，图也更容易做回放、可观察和中断恢复。

### MCP 有什么实际价值？

不是为了多一个协议名，而是把工具 Schema、transport 和 Agent 框架解耦。代价是增加一次网络跳转和鉴权边界，所以服务只在内部网络，且保留受限 fallback。

### RAG 的一致性怎么保证？

MySQL 是事实源，Chroma 可重建。写入先计算 embedding，再按 revision upsert 新 chunks，最后清理旧 revision；缓存用版本号失效。它仍不是跨 MySQL/Chroma 的强事务，因此提供 reindex 和“保存成功、索引待修复”的日志语义。

### SSE 为什么不用 WebSocket？

当前主要是服务端单向 token 与状态事件，HTTP POST SSE 能保留标准鉴权、网关和重试语义，复杂度更低。因为 EventSource 不能 POST body，前端用 fetch ReadableStream 解析。

### 上下文估算不精确怎么办？

估算器是模型无关的保守上限控制，不是计费 tokenizer。生产可为每个模型注入真实 tokenizer；预算组件接口不变。

### 还有哪些未完成？

- 知识索引与 MySQL 不是分布式事务，可继续加 outbox/reconciliation job。
- BM25 当前为有界单机语料，规模扩大后换 OpenSearch。
- 缺少真实模型、Chroma、Redis 的自动化端到端测试环境。
- 心理安全规则仍需专业人员评审、区域化资源和合规审计。

主动说出边界，比把项目包装成“完全生产可用”更可信。
