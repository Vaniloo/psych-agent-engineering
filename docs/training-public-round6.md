# 公开数据扩量轮 · 2026-10-11

本轮三个角色各准备 **10,000 条训练状态，合计 30,000 条**，在 lab data 盘分别从当前正式权重 warm-start。三个角色均完成 1 epoch、校准、留出测试及鉴权 worker 诊断。**Executor v6 已接入；Router v4 和 Memory v5 拒绝上线，分别保留 Router v3、Memory v2。** 角色、基线和候选如下：

| 角色 | 正式基线 → 候选 | 训练状态 | 校准 / 验证 / 测试各自条数 |
| --- | --- | ---: | ---: |
| Router | domain-v3 → domain-v4 | 10,000 | 456 |
| Executor | domain-v4 → domain-v6 | 10,000 | 672 |
| Memory | domain-v2 → domain-v5 | 10,000 | 464 |

一条状态可包含多个分类任务：本轮 Router 39,628、Executor 17,054、Memory 33,300 个任务训练项，合计 89,982 项。不能将三万条描述为三万个独立原始咨询会话。

## 来源与课程

中文原始输入来自作者发布的 [PsyDial-D4](https://huggingface.co/datasets/qiuhuachuan/PsyDial-D4)、[CPsyCoun](https://huggingface.co/datasets/CAS-SIAT-XinHai/CPsyCoun)、[WildChat-1M](https://huggingface.co/datasets/allenai/WildChat-1M)。前两者是重建对话；WildChat 本轮只下载第一个分片。版本和文件哈希保存在 data 盘的 `datasets/raw/*/source.json`。实际所用发布的许可证分别为 Apache-2.0、CC-BY-SA-4.0、ODC-BY；保留来源与许可信息，不将代码许可证当成数据许可。

预处理产生 12,000 个唯一中文状态：PsyDial 4,030、CPsyCoun 3,989、WildChat 3,981。先对完整会话做哈希划分，仅使用训练桶，再取当前用户输入及最多两条此前消息；后续回复不进入该状态。输入仅带 role/content，剔除检测到密钥、邮箱、手机号的整段会话。没有完整匿名化或语义近重复保证。

DeepSeek 提供训练标签草稿。首批发现记忆 semantic 类漏标和非心理医学请求误分类，调整说明后重做小批次，并隔离原失败草稿。有效对话标注共 8,724 条；后段 API 返回鉴权或额度错误，停止调用并保存已有结果。训练和评测不依赖教师 API。

| 角色 | 本轮 10,000 条组成 |
| --- | --- |
| Router | 8,500 个公开对话教师状态 + 1,500 个已有训练状态回放 |
| Executor | 6,500 个公开主题启发的虚构目标进度状态 + 1,500 个项目工具权限状态 + 1,000 个目标创建授权状态 + 1,000 个已有训练状态回放 |
| Memory | 4,481 个公开用户观察 + 2,261 个事实/问题及来源对照 + 1,548 个归属证据对照 + 710 个缺失时长对照 + 1,000 个已有训练状态回放 |

Executor 从公开心理对话主题生成低压力的虚构目标、具体行动标准，以及本人部分完成、全部完成、卡住、第三方、假设、未来和助手引用等表达。教师另一次标注时看不到预设类别；8320 条草稿中保留 7689、排除 631 条有分歧的行，再从保留数据抽取 6500 条。**同一个教师的另一遍检查不是独立金标准。** 工具状态只使用本项目的工具白名单、剩余预算及实际结果字段。工具名称不会从外部工具数据集变成应用权限。

Memory 的事实片段必须逐字存在于当前用户表达；其配对问题仍是教师草稿。来源对照与缺失字段对照是衍生反事实，不是原始真实会话。所有这些教师或衍生行均保留 `reviewed=false / teacher_draft`。项目规则构造的数据中的 programmatic/reviewed 标记只表示规则契约，不表示人工或临床审核。

精确输入去重、完整状态长度检查、会话组隔离、原划分保留和冻结诊断排除均已执行。已有校准/验证/测试没有搬入训练；没有用重复填充凑到三万。短训练状态限制为 256 token；模型保留线上原有 1536 / 512 长度配置及 1008 token 状态预算，避免缩短已有输入能力。初次短预算尝试已中止并隔离；Executor/Memory 当时在旧留出长度检查阶段退出，Router 尚未保存权重。

## 独立评测和切换

旧三组 35 / 40 / 53 条开发诊断继续固定使用；它们曾参与开发定位，不能称为全新盲测。另从作者 [LongMemEval cleaned](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) 保留 30 个短输入问题，使用官方标注的单条用户答案证据，加上助手来源对照，形成 90 条 Memory 诊断。整个 LongMemEval 数据不进入训练。该改编评测检查短上下文中的证据相关性、充分性和归属，**不是原始 500 题 QA 准确率，也没有验证长上下文记忆能力。**

新改编诊断 SHA256：`b82127e93debe08c4620be842067d0ba931ee1af02525896923c35ed4d8232ab`。当前正式 Memory 在该组可用正确 29/90、接受错误 51；这暴露了需要改善的证据判断，不能用合成留出成绩掩盖。

`training.finish_public_round` 是本次运行的单次后处理：等待已启动的训练完成，校准与测试由训练器执行；随后冻结来源和权重哈希，启动独立候选 worker，通过真实鉴权接口复测相同诊断。只有各组可用正确不下降、接受错误不增加，且没有新增风险漏判、虚假行动授权或原本正确的记忆行为退化，所有任务也均启用并校准的角色才通过。客户端最低概率仍为 0.85。

传入 `--promote-passed` 时仅切换通过的角色：核对现有正式模型和进程归属，保存启动映射，切换后检查健康状态、权重哈希和实际推理是否与候选一致。失败恢复原模型，拒绝的检查点记录 `promotionStatus=rejected`。不会改动其他用户进程。本轮单次后处理已完成，过程和结论保存在 `datasets/public-scale-round6/evaluation/round-status.json`、`aggregate-results.json`。实际正式版本已复核为 Router v3 / Executor v6 / Memory v2；三个候选临时端口均已关闭。

所有原始数据、训练划分、缓存、权重、私有凭据和日志均在 `/home/ubuntu/data/psych-agent-routing-20261009`。仓库提交代码和来源记录，不发布原始会话、API 密钥或权重文件。

## 完成结果

可用正确要求一条状态的全部任务预测正确，且通过任务校准门槛与客户端 0.85 下限；接受错误表示已通过门槛但至少一项错误。

| 诊断 / 角色 | 可用正确：基线 → 候选 | 接受错误：基线 → 候选 |
| --- | ---: | ---: |
| 第五轮 / Router（8 条） | 7 → 6 | 1 → 1 |
| 第五轮 / Executor（18 条） | 11 → 15 | 3 → 3 |
| 第四轮 / Executor（40 条） | 27 → 39 | 3 → 0 |
| 第三轮 / Executor（29 条） | 23 → 28 | 2 → 0 |
| 第五轮 / Memory（9 条） | 4 → 2 | 4 → 2 |
| 第三轮 / Memory（24 条） | 17 → 11 | 7 → 5 |
| 新改编证据检查 / Memory（90 条） | 29 → 60 | 51 → 0 |

Executor 三组合计可用正确 **61/87 → 82/87**，接受错误 **8 → 3**。通过各组和逐条授权门槛后，切换至 `psych-executor-domain-v6`；切换后对同一三组复测，与候选的选择及接受状态完全一致。仍有三条实际部分行动被高置信度判为 none，不能宣称所有目标进度都已正确识别。

Router 的原始正确仍是 7/8，但一条明确专业支持请求的 route 概率约 0.50，低于客户端 0.85，因此可用覆盖下降，拒绝上线。近期风险缓和后仍应 elevated 的一例被判 low；该组接受错误总数没有增加，不表示每个个案都没有变化。

Memory 的新改编检查要拆开看：用户证据充分性 18/30 → 30/30，助手来源不足 11/30 → 30/30，但两候选相关性仍 **0/30 → 0/30**。后者从接受错误 27 次变为全部弃权，不能把总计接受错误清零说成相关性已解决。中文旧诊断的候选相关性概率也大量降至约 0.50；另有原本正确的 `write-7` 新增接受错误。旧任务覆盖退化，因此保留正式 Memory v2，即使部分新证据判断明显改善。

合成留出测试 Router 的 route/risk 各 454/456，其他两任务 100%；Executor goal_action 329/342，其他三任务 100%；Memory 除 relevance_1 187/189、relevance_3 183/184 外，其余任务 100%。这些模板留出成绩没有覆盖上述开发诊断中的失败，不作为临床或真实咨询泛化证明。

应用只读核验五项均通过：三个 SSH 转发 worker、Runtime 模型清单、前端代理经 Java 返回的模型清单都准确显示 Router v3 / Executor v6 / Memory v2 且 ready。该核验没有重新调用教师或回复 API，也没有写入目标/会话；本轮没有重新声称完成生成回复的全流程联调。教师 API 的鉴权/额度问题仍需单独处理。

94 项 Python 测试、训练源码 Ruff 检查和变更检查通过。后续应优先处理专业支持请求的路线置信度、记忆相关性弃权与分类回归，并补独立审核样本；本轮没有继续扩量或追加训练。

## 复现入口

2026-10-11 的后续只读诊断见 [扩量回归定位](training-public-round6-diagnostics.md)。Memory v5 在抽查的 20 个新增来源训练状态上也没有完整可用判断；精度、问题批处理和截断均未解释这一失败。候选隔离只修复部分中文诊断，实际业务元数据也影响结果，因此需先改输入课程和验证覆盖，不能直接变更阈值上线。本次诊断没有追加训练或修改正式模型。

以下在 `agent-runtime` 目录执行，先加载 `scripts/lab-routing-env.sh`；教师凭据仅从私有环境提供。输出目录须使用全新版本，不覆盖现有权重。

```bash
root=/home/ubuntu/data/psych-agent-routing-20261009
python -m training.scale_public prepare --raw "$root/datasets/raw" \
  --out "$root/datasets/public-scale-round6" \
  --checkpoint "$root/models/psych-router-domain-v3" --count 12000
python -m training.scale_public annotate \
  --data "$root/datasets/public-scale-round6/public-cases.jsonl" \
  --out "$root/datasets/public-scale-round6/teacher-public.jsonl" --limit 10000 --concurrency 8
python -m training.scale_executor \
  --data "$root/datasets/public-scale-round6/public-cases.jsonl" \
  --out "$root/datasets/public-scale-round6/executor-drafts" \
  --checkpoint "$root/models/psych-executor-domain-v4" --packs 520 --concurrency 4
python -m training.adapt_memory_benchmark \
  --raw "$root/datasets/raw/LongMemEval/longmemeval_oracle.json" \
  --checkpoint "$root/models/psych-memory-domain-v2" \
  --out "$root/datasets/public-scale-round6/longmemeval-adapted.jsonl"
```

准备 replay 目录，分别指向三个正式模型的原训练数据；holdouts 指向第五轮四个划分。执行 `training.build_public_scale --help` 提供 public、executor、holdouts、replay、models、out 及所有冻结诊断路径。该命令拒绝覆盖已有训练集。

```bash
python -m training.train_decisions --profile router \
  --base "$root/models/psych-router-domain-v3" \
  --data "$root/datasets/three-laya-public-r6/router" \
  --out "$root/models/psych-router-domain-v4" \
  --epochs 1 --micro-batch 1 --max-len 1536 --head-max-len 512 --allow-teacher-only
# 为另外两个角色各运行同样的一轮。先对现有鉴权 worker 保存四组 baseline 报告。
python -m training.finish_public_round --root "$root" --timeout-hours 10 --promote-passed
```

本次使用 GPU 0 / 7 / 1，micro-batch 1，梯度累积 4，fp16，每个进程显存上限 20%。完成时间受共享 GPU 负载影响。以上结论来自 data 盘真实报告与切换后的服务复核，原始对话和权重仍保留在 data 盘。
