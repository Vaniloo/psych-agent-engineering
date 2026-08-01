# 模型工程细节

## 上下文预算

Runtime 不把完整历史直接交给模型。预算计算：

```text
available = context_window - response_reserve
available = memory + current_message + prompt_overhead
          + bounded_citations + newest_history
```

默认窗口 8192，预留回复 1200。记忆文本最多使用约三分之一可用预算，引用最多 1800 tokens，历史从最新消息向前装入。若最新一条消息本身超预算，会保留其有界前缀；`estimatedTokens` 始终不应超过模型输入预算。

TokenEstimator 针对中文字符、英文 token 长度和标点做独立估算。它不是模型 tokenizer 的精确替代，但不会把超长英文串误算为一个 token，适合作为模型无关的保守控制层。

`CONTEXT_WINDOW_TOKENS` 同时用于 Runtime 预算和 Ollama `/api/chat`
请求的 `options.num_ctx`。因此面板显示的上限与模型实例实际分配的
Context 一致，避免应用按 8192 预算而模型只开 4096 的隐性截断。

## 分层记忆

| 层级 | 存储 | 用途 |
| --- | --- | --- |
| 最近消息 | Redis + MySQL | 连贯对话 |
| 当前会话摘要 | MySQL ConversationSession | 单会话压缩 |
| 中期摘要 | MySQL ConversationMemory | 跨会话主题 |
| 长期记忆 | MySQL ConversationMemory | 相对稳定事实 |
| 用户画像 | MySQL UserProfile | 偏好、困扰、应对方式、目标 |
| 图 checkpoint | Redis Stack | LangGraph 执行状态 |

当“历史 + 新消息 + 回复”的估算 token 达到阈值时，MemoryCompressor 调用低温结构化模型，只允许更新有限字段，每个字段有长度上限。模型失败时使用确定性会话摘要，不因压缩失败丢失本轮回复。

## SSE 协议

Spring 使用 POST 接收 JWT 和请求体，再返回 `text/event-stream`。事件：

| event | 内容 |
| --- | --- |
| `meta` | requestId、sessionId、runtime |
| `status` | 当前图节点和人类可读标签 |
| `node_metric` | 节点名和真实执行毫秒数 |
| `plan` | intent、工具计划 |
| `tool` | 已执行工具、引用数 |
| `context` | token 估算、窗口、裁剪消息数 |
| `token` | 增量文本 |
| `memory` | 是否触发压缩 |
| `warning` | 非致命问题 |
| `metrics` | E2E、TTFT、Token、吞吐、成本、节点与 RAG 指标 |
| `graph` | 完成节点 |
| `done` | 完整 AgentChatResponse |
| `error` | 结构化错误 |

Nginx 对 `/api/` 关闭 buffering，并设置长读超时。前端使用 `ReadableStream + TextDecoder` 解析多段 SSE，而不是浏览器 `EventSource`，因为 EventSource 不支持携带 POST body。

## 模型失败策略

- Planner：规则优先，模型补充，工具白名单过滤。
- Rerank：默认关闭；开启后只重排前 10 条，失败回确定性融合。
- Chat：Ollama 流失败时 Runtime 输出安全的短降级文本。
- Compression：失败回确定性有界摘要。
- Runtime：调用前失败由 Spring Java Agent 接管；已输出 token 后不切换，避免重复回答。

## Token、延迟与成本口径

Ollama 最后一帧直接返回 `prompt_eval_count`、`eval_count`、`total_duration`、`load_duration` 和 `eval_duration`。Runtime 不用字符数猜用量，而是按每个用途（planner、query embedding、rerank、response、memory compression）记录真实字段。

- E2E：进入 LangGraph 到 `finalize_metrics`。
- TTFT：进入 Runtime 到第一次发送 `token` 事件。
- tokens/s：completion token 数除以 Ollama `eval_duration`。
- 节点耗时：每个 LangGraph node 包装器的 `perf_counter` 差值。
- 本地实际成本：0 USD。
- 等价估算：`prompt_tokens * input_price + completion_tokens * output_price`，单价按每百万 token 配置。

指标在 Runtime 侧写 Prometheus，在 Spring 网关收到 `done` 后写 MySQL；前端只读取用户自己的历史。

## 关键配置

```text
CONTEXT_WINDOW_TOKENS=8192
RESPONSE_RESERVE_TOKENS=1200
MEMORY_COMPRESSION_TRIGGER_TOKENS=4200
MAX_HISTORY_MESSAGES=40
RAG_DENSE_K=20
RAG_SPARSE_K=20
RAG_FINAL_K=6
RAG_MIN_SCORE=0.18
RAG_LLM_RERANK_ENABLED=false
EMBEDDING_BATCH_SIZE=32
MODEL_COST_MODE=local
INPUT_COST_PER_MILLION_TOKENS=0
OUTPUT_COST_PER_MILLION_TOKENS=0
```
