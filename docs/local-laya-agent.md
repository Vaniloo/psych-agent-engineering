# 本地三 Laya Agent

项目采用 Jev 风格的结构化决策思路，以开源 Laya 在本地实现。三个角色共用基础模型的选择，但各自训练、校准、部署和回滚；生成回复仍由 Ollama 承担。

| 角色 | 契约 | 决策任务 | 调用边界 |
| --- | --- | --- | --- |
| Router | `psych-router-v2` | `conversation_kind`、`route`、`risk_band`、`support_need` | 每轮一次 HTTP 请求，包含四项判断 |
| Executor | `psych-executor-v1` | `next_action` | 根据上一步结果选择工具、finish 或 clarify；最多两次工具调用 |
| Memory | `psych-memory-v1` | 四类记忆标签、候选相关性、证据充分性 | 后台分类；检索最多两轮，每轮最多四项候选 |

Router 输出标签与概率，应用自己的 `route_policy.py` 把标签映射为工具权限。Executor 的候选项进一步缩小为本轮尚未调用的授权工具，加上 `finish/clarify`。身份、会话和检索参数由 Runtime 注入。模型没有任意 URL、代码执行、写目标或修改权限的能力。

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

已有 v1 六类路由权重只是历史基线：没有风险头、Executor 或 Memory 能力，不能加载到新契约。新三角色尚未训练，本次没有启动新的训练。

## 训练准备

```bash
uv run --directory agent-runtime python -m training.export_decision_contracts --out /absolute/data/path/decision-contracts
```

导出三个独立问题定义、待标注样板和默认禁用的部署模板。模板不是训练数据，也不是可部署检查点。Router 使用对话上下文的人工标签；Executor 使用工具执行轨迹；Memory 使用用户原始表述及查询证据判断。按完整会话/用户划分集合，避免同一对话和改写泄漏；独立校准、验证、测试，分别测覆盖率和错误率。

lab 上环境、缓存、数据、权重、日志、检查点必须位于数据盘 `/home/ubuntu/data/psych-agent-routing-20261009`，沿用 `scripts/lab-routing-env.sh`。下一步先完成领域标注与评测集，再改造训练器；旧 `training/train_router.py` 只训练 v1 基线。

## Memory 的实现范围

Jev-Mem 启发的独立控制层已接通：用户/助手原始观察分开保存，后台多标签分类，按用户检索候选、相关性筛选和证据充分性判断，支持有限轮次停止。Java 消息与事务 outbox 是事实源；SQLite 是可重建索引。模型故障不会丢弃原始观察。时间字段表示记录时间，不冒充事件发生时间。

当前候选召回为有界 BM25 加近期记录回退；每项展示最多 500 字符并标注 truncated，不能把缺少某项信息视为否定事实；仅建立观察先后边，尚未实现完整语义/时间/因果/实体四视图图检索。预留的 temporal_need 任务尚未参与在线控制。历史消息尚未自动回填，删除/修正记忆的用户接口也未接通。不能宣称完整复现 Jev-Mem 或已经获得论文效果。
