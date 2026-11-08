# 本地决策角色与模型接入

> 本页部分角色表、读取预算和接入描述保留的是早期阶段。2026-10-20冻结已使用ToolACE Qwen4B作为主工具选择器、Laya作为故障回退，并增加独立生命周期/目标门控。接手当前版本先读[模型训练交接](model-training-handoff.md)、[当前架构](architecture.md)和[冻结说明](freeze-20261020.md)；不要直接按下方旧表配置当前主模型。

当前是三个角色、按任务分工的混合部署。模型仅做有限候选概率判断，生成模型另负责回答与目标拆解。

| 角色 | 当前权重 | 契约 | 判断任务 |
| --- | --- | --- | --- |
| Router | psych-router-qwen4b-lora-20261012 | psych-router-v2 | conversation_kind、route、risk_band、support_need |
| Executor / 工具 | psych-executor-domain-v6 | psych-executor-v2 | next_action：授权工具 / finish / clarify |
| Executor / 目标 | psych-executor-qwen4b-lora-20261012 | psych-executor-v2 | goal_creation、goal_action、goal_outcome |
| Memory | psych-memory-qwen4b-lora-20261012 | psych-memory-v4 | 分类、相关性、充分性；预留 temporal_need 尚未参与在线控制 |

另有原始 Qwen3-Reranker-0.6B，仅排序近期记忆候选，未微调。JEV/Laya 的有限候选思路用于控制层；Qwen 适配契约不代表完整复现 JEV，Memory 也没有完整四视图图检索。

## 授权与调用

Router 每轮一次 HTTP 请求，返回四项判断；`route_policy.py` 映射工具白名单，并保障明确读取。规则危机不可降级，可信模型可升级；风险不明的心理支持提供专业资源。

Executor 在 `start_loop / decide / execute / observe` 中选择尚可执行的授权工具。常规预算两次，明确复合读取最多四次；查询修正最多一次，只能改变受限检索参数。固定安全、上下文与持久化节点不开放给模型。

目标判断通过原话前置筛选后才调用独立 worker。创建只请求 goal_creation，进度匹配有限本人行动及结果类型；独立目标模型失败或弃权时跳过写入，不借用工具 worker。begin-turn 记录原始命令后才能写，实际结果先于最终回复；消息与 outbox 最后同事务保存。

Memory 后台保存原始观察并分类，读取时判断相关性与充分性。普通查询使用 BM25 + 近期原话重排；明确本人回顾还补读近期已保存消息，最多合并 32 条候选、每批相关性最多 16 条。账号、来源、日期和原话由应用控制，旧助手回复不能作为用户事实。历史 Laya 与回滚检查点保留。

## 配置

`ROUTER_PROVIDER`、`EXECUTOR_PROVIDER`、`MEMORY_PROVIDER` 支持 rules / laya / qwen，默认 rules。每个角色有独立 `*_BASE_URL`、`*_API_KEY`、超时与接受门槛。

- Qwen Router/Executor 仍用 v2 契约；Qwen Memory 必须设置 `MEMORY_CONTRACT_VERSION=psych-memory-v4`。
- `EXECUTOR_GOAL_PROVIDER` 为空沿用工具模型；显式 qwen/laya/rules 使用独立 `EXECUTOR_GOAL_*` 配置。
- 重排独立配置 `MEMORY_RERANK_BASE_URL`、`MEMORY_RERANK_API_KEY`。
- `MEMORY_QUERY_TIMEZONE=Asia/Shanghai`；旧无时区观察按 `MEMORY_LEGACY_TIMEZONE=UTC` 解释。
- `TOOL_TRANSPORT=local` 为默认；mcp 共享同一执行器，不改变权限。

完整模板在 `.env.example`。当前领域权重仅在私有数据盘，代码克隆不会自动获得这些模型。

## 独立服务

原 Laya 使用 `scripts/start-laya.sh router|executor|memory`，配 `DECISION_CHECKPOINT` 与 `DECISION_API_KEY`。没有检查点时 health.ready=false，不能把 HTTP 200 当模型就绪。

Qwen Router/Executor 使用 `python -m psych_agent_runtime.qwen_choice_service --profile router|executor --base <base> --checkpoint <adapter> --port <port>`，认证通过 `QWEN_CHOICE_API_KEY`。

Qwen Memory 使用 `python -m psych_agent_runtime.qwen_memory_service --base <base> --checkpoint <adapter> --port <port>`，认证通过 `QWEN_MEMORY_API_KEY`。参数与依赖以模块及对应训练环境记录为准；Qwen 训练/服务环境与轻量 Runtime 分开。

`compose.laya.yml` 仅提供历史三 Laya CPU worker 示例，并非当前四决策 worker 的 Qwen GPU 部署方案。当前混合模型 Compose 全栈尚未验收。

## 检查点与评测

服务校验角色、contractVersion、固定问题/渲染器摘要、权重摘要、输入预算及逐任务 enabled/calibrated/temperature/threshold。客户端再校验候选集与概率分布。输入超预算拒判；清单声明 calibrated 不是独立评测证明。

训练、校准、验证和测试分开，错误接受与弃权同时报告。动态工具子集与行动候选需专项覆盖。程序场景、教师草稿、开发诊断不能当作人工金标或临床验证。

当前取舍来自 [Qwen 对照](qwen-router-executor-finetune-20261012.md) 与 [Executor 分工](router-executor-serving-20261012.md)；Memory 见 [微调](qwen-memory-finetune-20261012.md)、[服务优化](qwen-memory-serving-20261012.md)。Laya 多轮训练保留在各带日期报告中，入口导航见 [训练目录](../agent-runtime/training/README.md)。最新状态以实际 runtime/info 为准，旧报告中的“当前”仅指其记录日期。
