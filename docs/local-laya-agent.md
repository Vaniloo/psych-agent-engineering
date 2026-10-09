# 本地三 Laya Agent

项目采用 Jev 风格的结构化决策思路，以开源 Laya 在本地实现。三个角色共用基础模型的选择，但各自训练、校准、部署和回滚；生成回复可使用本地 Ollama 或配置的 DeepSeek Flash。

| 角色 | 契约 | 决策任务 | 调用边界 |
| --- | --- | --- | --- |
| Router | `psych-router-v2` | `conversation_kind`、`route`、`risk_band`、`support_need` | 每轮一次 HTTP 请求，包含四项判断 |
| Executor | `psych-executor-v2` | `next_action`、目标创建、行动匹配与进度类型 | 根据上一步结果选择工具、finish 或 clarify；最多两次工具调用 |
| Memory | `psych-memory-v2` | 四类记忆标签、候选相关性、证据充分性 | 后台分类；检索最多两轮，每轮最多四项候选 |

Router 输出标签与概率，应用自己的 `route_policy.py` 把标签映射为工具权限。Executor 的候选项进一步缩小为本轮尚未调用的授权工具，加上 `finish/clarify`。身份、会话和检索参数由 Runtime 注入。模型没有任意 URL、代码执行或修改权限的能力。目标写入只能走用户明确授权且经应用验证的专用工具。

规则命中危机直接执行固定危机流程；可信模型判断可以升级风险，不能降低规则命中的风险。心理支持风险不确定时进入专业支持资源路径。`get_support_resources` 在这个路径是必需步骤，Executor 不能用 finish 跳过；资源不可用时报告不可用。

## 运行配置

三项 `*_PROVIDER` 分别支持 `rules`、`laya`，默认 rules。架构和工具先在规则模式运行，验证后的权重逐个接入。

```text
ROUTER_PROVIDER=rules
ROUTER_BASE_URL=http://localhost:8091
EXECUTOR_PROVIDER=rules
EXECUTOR_BASE_URL=http://localhost:8093
MEMORY_PROVIDER=rules
MEMORY_BASE_URL=http://localhost:8092
MEMORY_STORE_PATH=data/memory.sqlite3
TOOL_TRANSPORT=local
```

单独启动模型服务：

```bash
DECISION_CHECKPOINT=/absolute/path/to/router DECISION_API_KEY=replace-me ./scripts/start-laya.sh router
DECISION_CHECKPOINT=/absolute/path/to/executor DECISION_API_KEY=replace-me ./scripts/start-laya.sh executor
DECISION_CHECKPOINT=/absolute/path/to/memory DECISION_API_KEY=replace-me ./scripts/start-laya.sh memory
```

Runtime 分别配置对应的 `ROUTER_API_KEY`、`EXECUTOR_API_KEY`、`MEMORY_API_KEY`，及 `*_PROVIDER=laya`。模型依赖在 `routing` extra；Runtime 只通过 HTTP 调用，不加载模型。未配置检查点时 worker 可以启动，`/health.ready=false`，预测返回 503。

Compose 可选模型服务：

```bash
docker compose -f docker-compose.yml -f compose.laya.yml --profile laya up --build
```

设置三个独立 API key，`LAYA_MODELS_DIR` 为宿主机权重目录，三个 `*_CHECKPOINT` 使用容器 `/models/...` 路径。容器内三个 worker 均监听 8091，由服务名区分；该覆盖文件会修正 Runtime 和 MCP 的地址。示例使用 CPU，尚未执行完整镜像构建和全栈验收。

## 检查点接入门槛

服务核对 `deployment.json` 的 profile、contractVersion、questionsSha256、weightsSha256、amp、maxStateTokens。超出模型输入预算直接拒绝，不接受截断后“低风险”的判断。每项任务独立标明 enabled、calibrated、temperature、threshold、validationScope；应用温度校准后再检查门槛。客户端也会校验完整概率分布、候选集合和最小门槛。声明 calibrated 仍须有独立评测支持，配置文件不是校准证明。

Executor 训练和评测必须覆盖动态候选集合、已执行工具结果、finish/clarify，以及预算耗尽场景。不能把完整工具集上的校准结果直接当成所有权限子集的效果。

已有 v1 六类路由权重只是历史基线：没有风险头、Executor 或 Memory 能力，不能加载到新契约。当前三角色采用各自的领域微调检查点；数据、校准与适用范围见下文。

## 训练准备

```bash
uv run --directory agent-runtime python -m training.export_decision_contracts --out /absolute/data/path/decision-contracts
```

导出三个独立问题定义、待标注样板和默认禁用的部署模板。模板不是训练数据，也不是可部署检查点。Router 使用对话上下文的人工标签；Executor 使用工具执行轨迹；Memory 使用用户原始表述及查询证据判断。按完整会话/用户划分集合，避免同一对话和改写泄漏；独立校准、验证、测试，分别测覆盖率和错误率。

lab 上环境、缓存、数据、权重、日志、检查点必须位于数据盘 `/home/ubuntu/data/psych-agent-routing-20261009`，沿用 `scripts/lab-routing-env.sh`。当前 `training/train_decisions.py` 支持三角色独立微调；旧 `training/train_router.py` 只训练 v1 基线。

## Memory 的实现范围

Jev-Mem 启发的独立控制层已接通：用户/助手原始观察分开保存，后台多标签分类，按用户检索候选、相关性筛选和证据充分性判断，支持有限轮次停止。Java 消息与事务 outbox 是事实源；SQLite 是可重建索引。模型故障不会丢弃原始观察。时间字段表示记录时间，不冒充事件发生时间。

当前候选召回为有界 BM25 加近期记录回退；每项展示最多 500 字符并标注 truncated，不能把缺少某项信息视为否定事实；仅建立观察先后边，尚未实现完整语义/时间/因果/实体四视图图检索。预留的 temporal_need 任务尚未参与在线控制。历史消息尚未自动回填，删除/修正记忆的用户接口也未接通。不能宣称完整复现 Jev-Mem 或已经获得论文效果。

## 目标链路

Executor 契约升级到 `psych-executor-v2`，新增 `goal_creation`、`goal_action`、`goal_outcome`。目标创建只有当前用户明确命令、非危机且原始轮次已保存时才能执行；进度同步只选择用户已存在的有限行动候选，可关闭和撤销。创建与同步均通过自有目标工具服务验证归属、原话和重复请求；新的检查点须覆盖这些任务，并重新导出契约、训练和校准，旧 executor-v1 权重不能直接接入。

用户手动“完成这一步”后，先事务保存事实，再请求配置的生成模型结合行动、感受及最近 8 条用户表达生成下一步。模型故障不撤回已保存的记录；后续目标版本冲突不覆盖。计划选择已有步骤或插入一个新步骤，保留旧步骤和反馈。目标自动完成必须原步骤全部完成、模型判定达成标准且引用实际完成行动；用户可修改标准或重新打开。持续性目标不能仅凭一次行动完成。部分进展只产生建议，不写完成状态。每周回顾从本周实际记录聚合，撤销记录不计入，最多读取 1000 条并明确标示截断。

## 三模型领域微调与接入（2026-10-10）

当前训练器为 `training/train_decisions.py`，可分别训练 Router、Executor、Memory；旧 `train_router.py` 仅适用于旧版 v1 单一路由，不能生成当前三角色的部署清单。

训练使用已有 `laya-multilingual` 权重。训练、校准、验证、测试按会话/场景隔离，并检查跨集合的完全重复输入。校准集拟合每个任务的温度，验证集选择拒答阈值，测试集在选择检查点后评测一次；部署权重保持中性温度，服务端只应用一次任务校准。权限掩码和目标候选项与运行时一致。

本轮每角色有 2,400 个领域场景（1,680 / 240 / 240 / 240），Router 与 Memory 各额外使用 400 个 DeepSeek Flash 标注的公开对话状态，仅进入训练集。Executor 的场景覆盖只读工具选择、结束/澄清、创建目标授权，以及完成/部分推进/未实际行动的目标匹配。Memory 覆盖写入分类、八个候选位置的相关性、时间需要和证据充分性。应用实际检索按每轮四个候选运行，最多两轮。

`reviewedBy=programmatic_scenario_contract` 表示按场景构造规则给出并检查标签，不表示心理专业人员已复核。领域留出集以合成场景为主，且同类措辞模板在不同划分中出现，因此这些准确率只验证领域训练流程，不能代替真实对话泛化评测或临床效度。教师生成的标签保留 `teacher_draft` 状态；它们可以进入训练集，不能作为通过正式部署门槛的验证/测试标签。使用 `--allow-teacher-only` 允许教师训练数据，但只要校准/验证/测试中有未复核教师标签，全部部署任务仍禁用。

```bash
source scripts/lab-routing-env.sh
export PYTHONPATH="$PWD/agent-runtime"
python -m training.build_domain_decisions --out "$PSYCH_ROUTE_ROOT/datasets/domain-v1" --count 2400
python -m training.annotate_decisions --profile router --data "$PSYCH_ROUTE_ROOT/datasets/pending.jsonl" --out "$PSYCH_ROUTE_ROOT/datasets/drafts.jsonl" --limit 100
python -m training.train_decisions --profile router \
  --data "$PSYCH_ROUTE_ROOT/datasets/domain-v1/router" \
  --out "$PSYCH_ROUTE_ROOT/models/psych-router-domain-v1" \
  --base "$PSYCH_ROUTE_ROOT/models/base-laya-multilingual" --epochs 6
python -m training.freeze_deployment --profile router \
  --checkpoint "$PSYCH_ROUTE_ROOT/models/psych-router-domain-v1" \
  --dataset "$PSYCH_ROUTE_ROOT/datasets/domain-v1/router"
```

Router 完成首轮六 epoch 后，补充 1,200 条日常措辞的记忆、概况、目标请求及支持场景，再从自己的检查点完成六 epoch 微调；当前输出为 `psych-router-domain-v2`，数据划分 2,920 / 360 / 360 / 360。

Executor 完成首轮六 epoch 后，加入 1,200 条自然表达与工具返回场景，再从自己的检查点完成六 epoch 微调；当前输出为 `psych-executor-domain-v2`，数据划分 2,520 / 360 / 360 / 360。自然表达诊断与领域留出分开报告，不能用调整过的诊断题充当新测试集。

Memory 首轮的候选相关性未通过门槛。当前 `psych-memory-v2` 将每个相关性问题与其对应候选原文绑定，训练和在线服务使用同一渲染函数，并拒绝缺失候选位置。新增 1,200 条自然用户事实、不同候选数量与充分/不足证据场景，再完成独立六 epoch 微调；数据划分 2,920 / 360 / 360 / 360。旧 `psych-memory-v1` 清单不兼容，不能强行启用旧相关性任务。仍然是一个独立 Memory 模型，未引入生成模型做隐式记忆决策。

Executor / Memory 使用各自的数据目录和输出目录，禁止用同一个检查点冒充三个角色。部署清单固定问题摘要、权重 SHA256、逐任务校准/阈值、训练时间及数据文件 SHA256。运行指标从实际工作进程读取当前模型、契约和启用任务数；已有硬规则、安全资源路径、所有权及写入幂等检查仍生效。

## DeepSeek Flash 生成接口

`AI_PROVIDER=deepseek` 时，Python 的流式回复/结构化生成，以及 Java 的心理概要、目标创建、步骤反思，使用 `https://api.deepseek.com` 的 `deepseek-flash`。默认关闭 thinking，限制输出长度，不接受截断的结构化回复。不在故障时暗中切换到其他生成模型。嵌入继续使用本地 Ollama；三份 Laya 权重继续本地托管。

密钥只通过 `DEEPSEEK_API_KEY` 注入，不能出现在前端、Git、模型清单、训练样本或指标里。`.env.deepseek.local.env` 被 Git 忽略，三个本地启动脚本可以加载它；Compose 可通过 `--env-file` 指定该文件。API 标注器只接受明确传入的源状态、固定候选标签、有限批次；支持续跑，保留来源、教师版本、依据及未复核状态。
