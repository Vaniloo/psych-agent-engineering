# Router / Executor Qwen 4B 单轮微调 · 2026-10-12

**接入更新**：下文保留训练完成时的候选状态。随后用户确认切换，当前本地 Router 已使用本轮 Qwen 4B，Executor 工具选择保留 Laya、目标子任务接入本轮 Qwen 4B。最新实测与回滚说明见 [Router 切换与 Executor 分工](router-executor-serving-20261012.md)。模型权重、校准和冻结挑战未改动。

两路训练、校准、留出测试及实际候选接口评测已完成。每个角色从固定原始 Qwen3-Reranker-4B 基座独立训练一个 LoRA，完整一个 epoch，不复用 Memory 的适配器。候选服务已准备好，Runtime 已支持可选 qwen provider；当前应用 Router v5 / Executor v6 保持原版本，Memory 继续使用此前的 Qwen 4B。推荐优先试用新 Router；Executor 的主要收益在目标子任务，工具循环收益较小且延迟增加，值得单独评估。

## 方法与数据

Router 覆盖 route、conversation_kind、risk_band、support_need；Executor 覆盖 next_action、goal_creation、goal_action、goal_outcome。每个合法选项各渲染一份同源输入，以原始 yes/no logit 间隔作为该选项得分，按整组候选做多分类交叉熵。动态工具权限与行动位置使用现有契约；模型不能产生新工具、参数、ID 或扩大权限。

固定基座 revision `22e683669bc0f0bd69640a1354a6d0aebcfeede5`；BF16、LoRA r16/alpha32/dropout 0.05、attention/MLP 全投影、每角色 33,030,144 个可训练参数。micro batch 2 条决策、累积 8、有效批量 16、LR 1e-4、一次遍历。不同决策有不同候选数量，不能把决策数当成二分类 forward 数。goal_action 按匹配/none 平衡，不把少量高位置编号样本放大成一个独立语义类别。

课程来自现有 public-r6、semantic-contrast-r10 和 task-optimization-r11。只接受 reviewed=true 且非 teacher_draft 的项目/程序标签；没有新增教师 API 标注。删除人工记录时间与跟踪字段，保留真实事件表述、来源、目标/行动标题、工具结果、历史上下文、否定和权限预算。按声明 family 与规范化输入合并关联组件，跨 split 优先 test、validation、calibration、train；冲突标签和冻结挑战组件排除，任务/输入去重。语义模板仍共享，没有独立人工/临床金标。

| 角色 | train | calibration | validation | test | train 声明 family |
| --- | ---: | ---: | ---: | ---: | ---: |
| Router | 4,432 | 1,018 | 1,016 | 1,006 | 2,467 |
| Executor | 3,406 | 969 | 957 | 981 | 2,162 |

各排除 10,078 / 9,969 条未复核或教师草稿源行；任务展开与重复合并后，不能将源行数直接相加解释为训练规模。Executor 行动后几个位置正例较少：action_4/5/6/7 训练分别 6/3/3/1 条；会检查动态候选泛化，不以整体高分掩盖稀少位置支持。

温度只拟合 calibration 的完整未舍入候选分数；接受阈值只选自 validation，至少 0.85，至少 20 个接受声明组、每个标签/行动匹配桶至少 8 个验证声明组、观察错误接受比例不超过 10%。额外要求最高分与次高分原始间隔至少 2，作为数值稳定性启发式；不是独立错误率保证。挑战不选择权重、温度、门槛，检查点默认 deploymentEnabled=false。

## 冻结基线

训练前冻结 778 项判断：Router 440、Executor 338，包含未修改的开发旧题与 32 个新编写状态（64 项 Router 判断、20 项 Executor 判断）。合并完全相同规范输入，保留原标签；本轮没有发现重复输入标签分歧。新题仍由开发者定义，不称为独立人类盲测。

| 角色 / 任务 | 判断数 | 当前 Laya 原始正确 | 原始 Qwen 4B 原始正确 | 微调 4B HTTP 原始正确 |
| --- | ---: | ---: | ---: | ---: |
| Router / conversation_kind | 65 | 57 | 49 | 64 |
| Router / risk_band | 185 | 148 | 166 | 166 |
| Router / route | 69 | 60 | 62 | 65 |
| Router / support_need | 121 | 105 | 111 | 121 |
| Executor / goal_action | 143 | 105 | 85 | 132 |
| Executor / goal_creation | 35 | 30 | 30 | 35 |
| Executor / goal_outcome | 137 | 104 | 91 | 134 |
| Executor / next_action | 23 | 17 | 17 | 18 |

当前 Laya 实际 HTTP：Router 正确接受 350、错误接受 47、弃权 43；Executor 正确接受 247、错误接受 67、弃权 24。原始 4B 尚未为角色校准，不能把其“未接受”记为安全改进。参数更大不保证所有任务更好，尤其原始 Executor 尚未对项目行动匹配策略适配。

只读应用边界对照：Router 110 个检查中 15 个不符合项目标签；Executor 178 个检查中 25 个不符合项目标签，错误写入候选为 0。现有规则已挡住错误目标写入，后续看正确更新覆盖率与模型本身错误，而不是删除保护获得更高通过率。检查不执行 Java 事务、真实目标保存或回复生成。

Laya Router 当前 CPU，中位请求约 1,141 ms / P95 1,415 ms；Executor GPU，中位 41.6 ms / P95 104.0 ms。候选 Qwen 使用 GPU，设备不同，不能将延迟差异只归因于模型架构。每个 Router 请求含四项任务，每个进度请求含行动与结果，耗时不是单个候选判断。

## 微调后实际效果

| 指标 | Router Laya → Qwen 4B | Executor Laya → Qwen 4B |
| --- | --- | --- |
| 原始正确 | 370 → 416 / 440 | 256 → 319 / 338 |
| 原始正确比例 | 84.1% → 94.5% | 75.7% → 94.4% |
| 正确接受 | 350 → 408 | 247 → 314 |
| 错误接受 | 47 → 13 | 67 → 9 |
| 弃权 | 43 → 19 | 24 → 15 |
| 请求中位 / P95 | 1,141 / 1,415 → 331 / 409 ms | 41.6 / 104 → 243 / 313 ms |
| 应用检查不符合标签 | 15 → 11 / 110 | 25 → 20 / 178 |

两个角色的原生与实际 HTTP 在全部 778 项判断上，原始类别和汇总接受结果均一致；服务核对适配器与原始基座摘要。每项在课程验证上启用，阈值均 0.85；Router 温度 route/kind/risk/support 为 1/2/4/2，Executor next_action/creation/action/outcome 为 0.25/0.25/2/0.25。没有用挑战降低门槛或增加训练轮次。

新编写状态展开后：Router 原始正确 58 → 59 / 64，Executor 17 → 18 / 20。改善幅度比整个开发题集小，整体收益主要体现在已有诊断难题；不能把总体 94% 宣称为独立新用户准确率。另一个未参与训练/校准的正例，分别放在行动第 1–8 个位置，两模型均原始正确且接受 8/8；这是同一 family 的八个位置，不是八个独立案例。后几个位置训练支持仍稀少。

课程 test：Router 998/1,006、Executor 978/981 原始正确，模板性较强。风险任务仍有 4 个 urgent→elevated 原始错误，其中 1 个被接受；没有 urgent→low，但不能依赖模型独自识别所有紧急情况。现有来源、否定、当前具体危险行动及近期用户风险规则继续保留。风险 rawCorrect 与原始 4B 持平，错误结构改变：当前微调避免了原始模型的一些更低等级漏判，同时增加了一些保守升级。

Executor 的目标创建 35/35、行动匹配 132/143、完成程度 134/137 有明显进步；next_action 仅 18/23，正确接受仍为 17，与 Laya 持平，主要仍会偏早 finish。部分无工具/工具失败后的 finish 与 clarify 标签有策略歧义，保留原标签和评分，需要产品策略/人工复核。实际应用边界两边错误写入候选均为 0；模型错误减少不等于原来已经发生错误写入，保守原话与写入门槛仍会导致一些有效进展未被接纳。

两个角色纯优化约 29.1 / 29.2 分钟，含准备、原始基座对照、校准与留出评测的训练器总时长约 37.4 / 40.3 分钟，不含随后服务加载和 HTTP 对照。峰值 Torch 分配约 11,020 / 12,960 MiB。基座使用 BF16，PEFT 适配器参数保持默认稳定精度；适配器文件各约 126 MiB。没有改原始基座、Memory 权重或 0.6B 重排器。

## 执行与产物

所有源码、课程、缓存、依赖、适配器、日志与候选配置位于 `/home/ubuntu/data/psych-agent-routing-20261009/`，没有写入系统盘。训练器与服务复用 data 盘 PEFT/Accelerate overlay，没有替换运行服务依赖或改动其他进程。

- 课程/挑战/日志：`datasets/qwen-roles-finetune-20261012/`。
- 独立适配器：`models/qwen-router-4b-lora-20261012/`、`models/qwen-executor-4b-lora-20261012/`。
- 完成后的候选服务：回环 18098 / 18099，使用独立私有密钥、原始基座/适配器摘要及 renderer 校验。
- 单次后处理仅等待已有训练自然结束并启动候选服务，不重复启动训练，不切换应用模型。
- 本会话 `outputs/qwen-roles-finetune-20261012/` 保存冻结输入、课程摘要、原始 4B 和 Laya 对照、训练进度，完成后补充候选原生/HTTP/应用对照及汇总。

完整状态为 `complete_candidate_evaluated`，没有自动推广任何模型。本会话 `aggregate-results.json` 汇总原始基座、Laya、微调原生/HTTP、应用边界和延迟；`candidate-service-verification.json` 记录 401/409/422 边界与权重一致性，`active-runtime.json` 记录仍使用 Laya 的实际应用配置。**305 项 Python 测试通过**；Ruff、差异格式及指标 JS 语法检查通过。可选 qwen provider 已通过图路由、权限、预算及必需资源测试，前端可显示其来源，但未改私有 dev-stack.env 或重启应用切换模型。本轮没有调用回复生成、新增用户对话/目标/进度、进行临床或并发验收，也未推送 Git 或发布生产版本。
