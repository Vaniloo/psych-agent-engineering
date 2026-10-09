# 模型工程

决策与生成分开：Router、Executor、Memory 使用独立的 Laya 契约和权重；生成模型可选 DeepSeek Flash / Ollama，embedding 继续由本地 Ollama 提供。三角色支持独立领域微调、校准和部署，异常时受控回退，详见 [三 Laya 设计](local-laya-agent.md)。

## 两种预算

生成上下文按 `CONTEXT_WINDOW_TOKENS - RESPONSE_RESERVE_TOKENS` 分配给当前输入、旧摘要/画像、近期历史、工具结果及引用，默认 8192 - 1200。历史从最新向前装入，内容超长时裁剪；该估算不是精确 tokenizer。Ollama num_ctx 使用相同窗口设置。

Laya worker 使用实际 tokenizer 检查 maxStateTokens，且部署预算不得超过编码器扣除题目开销后的容量。超长直接拒绝，应用回退；不能依赖 SDK 静默截断来做风险判定。Router、Executor 和 Memory 的状态格式各自固定，问题摘要与权重哈希共同校验。

## 记忆

MySQL 原始消息与 outbox 在同一事务保存。后台 Memory 控制层对用户观察做 episodic/semantic/procedural/preference 多标签判断；即使模型失败仍保留原始记录。用户与助手观察分开，检索仅返回用户表述，不把助手建议写成用户偏好或经历。

SQLite 目前用 BM25/近期记录生成最多八项候选；独立 Memory Laya 分两批筛选相关性、判断证据是否充分。最多两轮，证据最多六项，观察时间不冒充事件时间。当前不是 Jev-Mem 完整四视图图实现；旧 MemoryCompressor 已删除。

## SSE 与指标

事件包含 meta、status、node_metric、plan、decision、tool_decision、tool、evidence、context、token、memory、warning、metrics、done、error。memory=pending 表示消息已保存、后台索引待更新，不表示索引已经完成。

生成服务的实际 usage 字段计入 prompt/completion tokens、吞吐与生成模型调用次数。Laya 选择过程保留在 plan/decision/tool_decision 和图状态，尚未汇总独立的 Laya 次数、token 和延迟指标；不能把生成服务的 modelCallCount 当成整个系统的模型调用总数。

本地 actualCostUsd=0 是 API 费用口径，不含硬件/电力。远程费用按配置单价估算，并非账单；未配置单价的零估算不代表免费。Java 目标反思单独统计，尚未计入对话 Token 用量。Runtime 首 token 前失败由 Spring 返回固定不可用文本，避免上游已提交但响应丢失时重复追加消息；部分回复已输出后不拼接第二份回复。
