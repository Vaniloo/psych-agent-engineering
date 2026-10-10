# Memory 逐条证据评分与 Router 上下文修正 · 2026-10-11

本轮针对三万条扩量后的回归，完成输入契约修改、补充训练、回归评测和正式接入。当前组合是 **Router domain-v5 / Executor domain-v6 / Memory domain-v7**；Executor 不参与本轮重训。训练、完整评测、权重和缓存均在 lab data 盘；本轮没有调用教师或生成 API。

## 输入与兼容

已有 `psych-memory-v2` 保留。新 `psych-memory-v3` 将八个位置相关性任务改为一个 `relevance` 任务，输入只有 `query` 与一条 `candidate={role,content}`。相同评分问题逐条用于最多八条候选。ID、会话、来源记录和时间等追溯字段由应用保存，相关性模型不接收这些字段；原文不被摘要替代。需要判断的事实必须来自用户表述，助手同文不能证明用户事实；本人明确否定也可以提供有用证据。

应用仍最多执行两轮记忆证据收集，每轮最多四条候选。逐条评分后，原始记录按原索引返回，保留所有业务追溯字段；证据充分性与写入分类仍由同一个 Memory 角色处理。Router、Executor、Memory 保持三个独立角色。

配置 `MEMORY_CONTRACT_VERSION` 默认仍是 v2。worker 从 checkpoint 校验其受支持的版本、问题哈希及权重哈希；显式指定的版本与权重不符时拒绝启动。Runtime 模型清单也核对应用配置的版本。只有新模型通过回归与业务输入检查后才切换为 v3，避免将旧权重静默用于新表示。

## 小规模实验

第一版 Memory 824 条、Router 592 条，分别训练两个 epoch。逐条表示恢复了原先 20 条公开训练状态的完整拟合，但旧 Memory 第三轮诊断可用正确由 17/24 降为 4/24；Router 由 7/8 降为 6/8，故未接入。

第二版 Memory 1149 条、Router 699 条，增加旧相关性回放、他人经历及专业请求/拒绝对照，再训练两个 epoch。Memory 中文第三轮恢复到 14/24，接受错误从基线 7 降为 1；改编证据检查 66/90、接受错误 0。Router 6/8、接受错误 0。覆盖仍下降，两个候选均不得据此上线。

检查发现旧记忆分类与充分性任务有遗忘，因此最终课程补入原正式模型训练分区的 reviewed 项；没有使用其校准、验证或测试分区。另排除公开衍生数据中“这是朋友说的……我自己不是这种情况”的候选对：其中的本人否定可能本身支持纠正问题前提，原 no 标签语义不充分。新增课程把“本人本周未做某事”与“仅转述朋友未做某事”分别处理；原始来源标签保持原样，不将教师草稿伪装成人工金标准。

这些实验使用开发诊断辅助定位，不属于新的盲测。旧的两次业务格式探针含本人否定但预设 no，不作为最终版本的正确性依据；最终改用纯他人经历对照，并在课程中独立保留本人否定的证据。

## 最终课程与验收

最终候选从正式 Router v3、Memory v2 重新 warm-start，分别输出 Router v5、Memory v6；两个 epoch、micro-batch 8、梯度累积 4、fp16。保持 1536 / 512 长度设置和 1008 token 状态预算。

| 角色 | 训练状态 | 校准 | 验证 | 测试 |
| --- | ---: | ---: | ---: | ---: |
| Memory v3 契约 | 5222 | 1392 | 1437 | 1486 |
| Router v2 契约 | 3917 | 486 | 486 | 486 |

Memory 来源包括公开用户事实片段/问题、来源对照、旧课程回放、自然否定和他人经历规则场景。Router 回放普通请求、近期风险、当前否定、专业渠道请求。教师来源仅用于训练并保留 draft；新增留出来自分开的规则表达族，属于合成项目策略验证，不能描述为真实咨询泛化或临床准确率。

状态去重在成对投影后进行，先保留各留出分区再筛训练状态；跨分区来源组隔离由 `load_parts` 检查。事实字段与措辞族不同，但模板生成依然有语义相关性，不等于自然对话独立审核。

验收保留原冻结案例及预期，不调整概率下限。新契约通过逐条 API 评分映射回原候选位置，比较每组整条状态的可用正确、接受错误及逐例回归。风险门槛补充“原先较高风险正确，现在被接受为 low”的保护；不能被总体改善抵消。公开改编检查仍是短证据任务，不是原始 LongMemEval QA 分数。

## 任务参数隔离与正式结果

共享参数的 Memory v6 改善了相关性，但仍损伤旧分类与充分性任务，因此整模型替换被拒绝。另做了仅训练 head、冻结 encoder 的四 epoch 实验，其合成验证准确率约 69.8%，不足以采用。

正式 Memory v7 采用 **原 Memory v2 基础权重 + Memory v6 的相关性完整参数适配器**。同一 Memory worker 仅在 `relevance` 请求时加载新参数，执行结束或异常后恢复原参数；其他六项任务使用原权重、原问题与原校准策略。worker 及适配器串行锁保护参数切换。它仍是三个角色中的 Memory 角色，不新增第四个 Laya 服务。

这个适配器包含完整参数，不是 LoRA，也不是仅替换 head；新相关性 encoder 已经过训练。`model.safetensors` 与原 Memory v2 字节一致，`pair-adapter.safetensors` 单独校验哈希。部署元数据明确记录 `unchangedBaseVerified=true`、`frozenEncoderVerified=false`、基础模型与适配器来源。Memory v7 报告中旧六项验证/测试成绩继承自原模型，并非本轮重新训练得到；新相关性成绩来自 Memory v6 的独立分区，验证 959/973（98.56%）、测试 944/1022（92.37%）。这些合成/项目策略留出分数与下面的实际 worker 开发诊断分别报告。

验收使用与基线相同的冻结案例、标签和来源文件，要求置信度不低于 0.85 且达到任务部署阈值。“可用正确”要求该状态所有任务正确且被接受；“接受错误”表示状态被接受但存在错误任务。

| 实际 worker 诊断 | 状态数 | 基线可用正确 | 本轮可用正确 | 基线接受错误 | 本轮接受错误 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Router 第五轮 | 8 | 7 | 7 | 1 | 0 |
| Memory 第五轮中文 | 9 | 4 | 6 | 4 | 3 |
| Memory 第三轮中文 | 24 | 17 | 17 | 7 | 7 |
| Memory 公开改编证据 | 90 | 29 | 58 | 51 | 24 |

公开改编的 30 条相关性状态可用正确 **0 → 29**，剩余 24 个被接受的错误全部来自原 `evidence_sufficient` 任务。旧中文两组总体覆盖没有下降，但保留原参数也保留了旧任务的不足。Router 仍有一条专业支持请求输出错误且未被完整接受，可用覆盖并未达到 8/8。不能将通过回归门槛解释为已消除所有错误。

新业务格式探针 8/8 通过：本人事实、助手同文、无关事实与纯朋友经历分别对照；加上追溯字段后逐例概率一致。另取实际保留训练状态做拟合检查 40/40，此项仅是拟合诊断，不计入泛化准确率。

两个候选经临时 worker 验收后，替换各自原正式进程。启动配置已更新；正式服务重新执行上述冻结诊断，其预测和接受决策与临时 worker 一致。共享 Memory v6、head-only 实验及两轮小规模候选均标记为 rejected；正式 Router v5 与 Memory v7 标记为 approved。

Runtime 和前端经 Java 的运行指标接口均确认 v5 / v6 / v7、契约匹配且 ready。正式 MemoryService 用独立合成 SQLite 索引验证原始观察写入、实际相关性筛选、来源 ID/时间保留、最多两轮收集与用户隔离，5 项通过。该例返回 `controlled` 而非 `sufficient`，没有将旧充分性判断的弃权伪装成确认。

本轮正式服务诊断的整状态延迟：Memory 90 条 p50 25.59ms / p95 60.54ms；两组中文分别 p50 114.69ms / p95 740.34ms（9 条）、p50 27.01ms / p95 247.91ms（24 条）；Router CPU p50 1081.88ms / p95 1404.86ms（8 条）。这些是少量串行请求的观测，包含参数切换，不是并发吞吐基准。完整参数缓存增加显存占用；应用每轮仍逐条处理最多四个候选，完整检索耗时取决于候选数与充分性调用。

正式部署标识：

- Router 权重 SHA256：`00ba350f3f5cef7f43b94f2ab3ef52a5f38bad720d16be5ea355d8f4359273a0`
- Memory 原基础权重 SHA256：`5f7bfef88610b68992e5d2252dba0d8e22b35e4e31ac258d9f26e4315ba0e1fe`
- Memory 相关性适配器 SHA256：`cca1b0b94f353f473c7822316a003ffeb32cf8409a2824122664eee8df79f80c`

服务器产物：`/home/ubuntu/data/psych-agent-routing-20261009/datasets/pair-round7-bundle/evaluation/`，包含 summary、正式复测、部署状态、失败任务摘要和原生链路检查。代码仓库不包含数据原文、权重或私有凭据。

## 复现

在 `agent-runtime` 下先加载 lab data 盘环境，再执行：

```bash
python -m training.refine_pair_round \
  --source /home/ubuntu/data/psych-agent-routing-20261009/datasets/three-laya-public-r6 \
  --legacy-source /home/ubuntu/data/psych-agent-routing-20261009/datasets/three-laya-domain-v2 \
  --out /home/ubuntu/data/psych-agent-routing-20261009/datasets/pair-round7-final
python -m training.train_decisions --profile memory --contract psych-memory-v3 \
  --data /home/ubuntu/data/psych-agent-routing-20261009/datasets/pair-round7-final/memory \
  --base /home/ubuntu/data/psych-agent-routing-20261009/models/psych-memory-domain-v2 \
  --out /home/ubuntu/data/psych-agent-routing-20261009/models/psych-memory-domain-v6 \
  --epochs 2 --micro-batch 8 --max-len 1536 --head-max-len 512 --allow-teacher-only
```

输出目录必须全新，禁止重复运行覆盖权重。Router 使用独立 GPU 与 Router v3 基线，并保持原 v2 契约。`training.evaluate_pair_challenge` 使用实际鉴权 worker；私有 API key 从环境读取，完整输出留在 data 盘。

训练并校准/冻结候选后，使用以下形式组装任务隔离候选；组装不是批准上线，仍需跑冻结诊断与逐例门槛：

```bash
python -m training.bundle_memory_head \
  --base /home/ubuntu/data/psych-agent-routing-20261009/models/psych-memory-domain-v2 \
  --trained /home/ubuntu/data/psych-agent-routing-20261009/models/psych-memory-domain-v6 \
  --out /home/ubuntu/data/psych-agent-routing-20261009/models/psych-memory-domain-v7 \
  --full-adapter
```

以上均为复现说明，已有目录不得再次执行。应用接入新权重时设置 `MEMORY_CONTRACT_VERSION=psych-memory-v3`，旧权重保持默认 v2。下一轮优先改善证据充分性；不扩大规模或再次调用已失败的教师 API。

当前验证：103 项 Python 测试通过，Ruff 与变更格式检查通过。参数切换异常后恢复、训练/服务规范化一致、来源字段保留、旧契约兼容、风险降级回归保护均有覆盖。
