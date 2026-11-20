# 面试学习路线 · 90 分钟走完一条请求

用同一个例子贯穿全程：用户上次说“面试前我喜欢散步”，这次开新会话问“你记得我怎么放松吗？”先预判程序会查什么，再对照代码。**如果只有 30 分钟，先走前两步，再看下方的三项评测。**

| 时间 | 读哪里 | 读完当场回答 |
| --- | --- | --- |
| 0–10 分钟 · 画图 | [总架构](architecture.md)的两张图 | 合上文档，画出浏览器、Spring、Runtime、工具、评分和生成。指出聊天记录、个人记忆索引、公共知识各存哪里。 |
| 10–25 分钟 · 跟一次工具调用 | 在 [graph.py](../agent-runtime/psych_agent_runtime/graph.py) 找 `_build_graph`；在 [tool_controller.py](../agent-runtime/psych_agent_runtime/tool_controller.py) 找 `decide` | 指出“必读记忆”“可选读取”“结束回答”三条边。若本轮根本没调用 LoRA，怎样用轨迹说明原因？ |
| 25–40 分钟 · 读懂 Memory 和 RAG | 对照 [memory.py](../agent-runtime/psych_agent_runtime/memory.py) 的 `retrieve` 与 [agentic_rag.py](../agent-runtime/psych_agent_runtime/agentic_rag.py) 的 `AgenticKnowledge` | 把例句换成“请用知识库解释面试焦虑”，说明数据源、引用、何时补查。再换成“请联网找一篇资料”，说明回答怎样区分摘要和正文。 |
| 40–55 分钟 · 看一次真实写入 | 在 [graph.py](../agent-runtime/psych_agent_runtime/graph.py) 找 `_loop_goal_write`、`_persist_turn`；在 [goal_operations.py](../agent-runtime/psych_agent_runtime/goal_operations.py) 找 `create` | 用户说“把每天散步设成目标”。依次说出原指令回执、业务写入、回复、消息保存。假设业务写失败，界面能确认什么？ |
| 55–75 分钟 · 讲透唯一的 LoRA | [course.py](../agent-runtime/training/public_tools/course.py) 的 `decisions` 和 `assign_components` → [train.py](../agent-runtime/training/public_tools/train.py) → [public_tool_client.py](../agent-runtime/psych_agent_runtime/public_tool_client.py) 的 `candidate_row` | 拿一段含工具调用的对话，写出一个输入、两个候选和示范标签。说明为什么输入只含当前动作之前的历史，以及为什么共享工具名或相同首问要分在同一组。 |
| 75–90 分钟 · 演示和复盘 | 按[交接文档](../HANDOFF.md)挑一个已有账号回忆或公开知识样例，打开运行指标 | 用“用户问了什么 → 读到了什么 → 回答依据什么”讲两分钟。再说一个观测到的失败，以及系统如何处理。 |

## 三个数字怎么讲

| 历史评测 | 基座 → ToolACE LoRA | 面试时说明 |
| --- | --- | --- |
| ToolACE 公开测试集 | 80.65% → 93.36% | 指标是下一动作与作者示范的一致率。 |
| 项目首动作 | 26/44 → 36/44 | 22 个项目问题各换两种候选顺序，衡量在应用候选上的迁移。 |
| 隔离图回放 | 11/12 → 10/12 | 微调后此组回放少通过一例。公共数据上的增益不能直接推成端到端提升；个人记忆等明确请求保留必读规则。 |

以上来自重构前的开发实验。本次代码回归的范围与结果见交接文档。[公开训练报告](https://github.com/Vaniloo/psych-agent-engineering/blob/35bd857284a6b9d80c342aa27bc177e8273f4556d1dec2af8d2897ab3dc39/docs/public-tool-next-action-20261019.md) · [项目回放报告](https://github.com/Vaniloo/psych-agent-engineering/blob/35bd857284a6b9d80c342aa27bc177e8273f4556d1dec2af8d2897ab3dc39/docs/public-tool-project-replay-20261019.md)

## 30 秒口述

Attune 是一个能回顾个人对话、查公共知识和跟踪目标的 Agent。Spring 管账号、会话和事务，Python 用 LangGraph 管一次对话的工具循环。规则先限定允许和必须读取的工具，唯一微调的模型是 ToolACE Executor，它根据已读结果选择再查一个工具或开始回答。生成模型拿到原话、引用或写入回执后组织回复。我分别用公开示范、项目回放和真实请求检查模型选得准不准、整条链路走得对不对。
