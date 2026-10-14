# 模型工程与运行口径

当前决策角色为 Qwen Router、Laya 工具 Executor + Qwen 目标 Executor、Qwen Memory，重排使用原始 0.6B。角色权重、契约和配置统一见 [本地决策角色](local-laya-agent.md)，训练入口见 [训练导航](../agent-runtime/training/README.md)。本页不重复逐轮版本。

## 输入与执行预算

生成上下文默认 8192 token，预留回复 1200。`response.py` 优先保留本轮相关工具证据与引用，再装入有界历史；截断显式标注。`token_budget.py` 的估算不是计费 tokenizer，Ollama num_ctx 使用同一窗口设置。

决策 worker 使用自己的实际 tokenizer、渲染器和部署输入预算。问题、候选集及权重/渲染器摘要校验失败或输入超预算时拒判，不能靠静默截断降低风险。逐任务温度校准后检查接受阈值，并报告拒判和错误接受。

执行预算另外控制只读 loop：通常两次、明确复合读取最多四次；默认读阶段期限 35 秒，参数去重，一次受限查询修正。目标规划和最终生成有独立超时。

## 证据控制

MySQL 消息与 outbox 同事务保存。后台分类失败仍保留原始观察，用户/助手来源分开。Memory 相关性和充分性判断不能修复完全没召回的原话；近期实际消息补读、来源/日期校验及上下文裁剪是应用策略，收益不能混入模型准确率。

个人记忆以原话为依据，公共心理知识以带来源片段为依据。知识用于支持性回答，不证明个人诊断；历史助手回答不能充当用户事实。详情见 [架构](architecture.md)、[个人状态回顾](personal-state-recall-20261014.md) 与 [知识链路](rag-pipeline.md)。

## SSE、延迟与成本

事件包含 meta、status、node_metric、plan、decision、tool_decision、tool_activity、tool、loop_observation、evidence、context、token、goal_created、goal_progress、memory、warning、metrics、done、error。memory=pending 表示后台索引待更新，不表示已经完成。

TTFT 是请求到首个回复 token 的时间，包含前面的路由、工具和目标阶段；单个 worker 推理耗时不能当作端到端 TTFT。运行 trace 记录节点与实际能力调用，pipeline 记录角色与 loop；生成服务 usage 的 modelCallCount 不等于所有决策 worker 总调用数。

本地 actualCostUsd=0 是生成 API 费用口径，不含显卡、电力或常驻模型费用；远程按配置单价估算，未配单价的零估算不表示免费。Java 目标反思另行统计，不包含在对话生成用量里。Executor 分工减少 4B 调用，没有减少常驻显存。

首 token 前 Runtime 故障由网关返回固定不可用回复；生成部分输出后中断不拼接第二份回答。工具 unavailable 与保存失败显式显示，不能把正常 HTTP/SSE 连接当作模型、工具或业务已成功。
