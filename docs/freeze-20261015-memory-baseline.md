# 记忆重构前的冻结基线 · 2026-10-15

冻结标记：`freeze-20261015-memory-baseline`。标签所指提交包含本说明、当前代码与测试；后续记忆生命周期实验在 `feat/jev-memory-lifecycle` 分支进行。

## 包含范围

- 当前有界 LangGraph loop、受控目标操作、对话与 outbox 持久化。
- 本人跨会话事实读取保障、提问噪声过滤、最多两条候选原话核对、来源追问与自然引用。
- 心理知识 RAG 的口语触发、自我照顾查询和生成来源约束。
- 此前发布的训练源码、数据与模型产物引用。没有加入新的记忆写入治理任务或训练新权重。

## 冻结模型

权重继续使用同仓库 [models-20261014 Release](https://github.com/Vaniloo/psych-agent-engineering/releases/tag/models-20261014)，模型/数据清单见 [发布 manifest](../agent-runtime/training/artifacts/20261014/manifest.json)。冻结前实际服务清单中的模型摘要如下：

| 职责 | 模型 | 权重 SHA256 |
| --- | --- | --- |
| Router | psych-router-qwen4b-lora-20261012 | `719ab99d776302adab820972d75e74c862b7f5f84b2a590ef7e68d85d779a949` |
| 工具 Executor | psych-executor-domain-v6 | `0c293ee2dad3429dcfb3564c15ec6408e22e6052f1381fae74a071490c26609a` |
| 目标 Executor | psych-executor-qwen4b-lora-20261012 | `9ede558e8e5c9936fb68dbccb9e67ea5fcd73ca57cf61340a5819ee4018ec65d` |
| Memory | psych-memory-qwen4b-lora-20261012 | `f679f7d5e2c22b7c32c4f18535e149d2ce9f73438412407e5634a861872b9467` |

三个 Qwen adapter 使用相同基座版本 `22e683669bc0f0bd69640a1354a6d0aebcfeede5`。另有原始 Qwen3-Reranker-0.6B，不重新训练。最终生成提供商为 DeepSeek Flash；外部服务不受 Git 标签冻结，不能承诺逐字重现回复。

## 已取得的验证

- 完整 Python 535 项通过，Ruff、差异格式检查通过；有一项原有 Starlette/httpx 弃用警告。
- 跨会话开发复测：原八项正向用例8/8、额外问法6/6、负向边界6/6、名字及来源追问2/2；共22轮保存成功。它们使用同一组合成事实，不是独立盲测。
- 心理知识集合311片段，五条向量抽查均为768维、有限非零；18次绕过缓存的检索17次命中预期来源。12轮对话的8项应检索与4项不检索符合预期，另3轮方法请求成功读取并保存。知识工具成功不代表回复逐句严格有据。
- 实际单轮loop：Router → 必读知识 → 观察 → 必读记忆 → 观察 → 预算停止 → 生成 → 保存，约3.50秒；该轮工具选择由policy推进，没有Laya自主选择调用，知识命中缓存。后台索引已包含该轮用户与助手原话。

细节见 [跨会话修复](cross-session-recall-fix-20261015.md)、[RAG检查](rag-verification-20261015.md)、[当前测试报告](test-report.md)。原始合成联调及运行清单保存在本地会话outputs，不发布私有凭据或真实用户记录。Java、前端与容量结果以各带日期报告为准，本次没有重新验证生产容量。

## 保留的限制

当前记忆是原话追加、分类和查询筛选，没有活跃记忆治理、事实合并、更正版本及过期归档；长期摘要/用户画像不随主聊天自动重写。分类失败会保存原话但不自动重新分类。Memory是Qwen有限候选实现，不是完整复现Jev-Mem。儿童创伤专门来源仍有漏召回，生成仍可能扩写资料未支持的细节。

Git冻结覆盖源码、测试、语料快照和产物引用，不包含实时业务数据库、私有配置、队列状态或完整开发服务镜像。回到该标签不会自动回退业务数据或切换运行中的模型进程。后续数据库变更必须保持迁移可回退，新增模型通过独立评测后再接入。
