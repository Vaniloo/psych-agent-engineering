# 三角色增量训练第五轮 · 2026-10-10

三个候选均完成 3 epoch 微调、任务校准、合成留出测试及鉴权 worker 评测。上线 **Router domain-v3**；Executor v5 和 Memory v4 拒绝上线，分别保留 **Executor domain-v4、Memory domain-v2**。没有降低客户端概率下限。

## 数据与训练

| 角色 | 起点 → 候选 | 训练：旧训练回放 + 新案例 | 校准 / 验证 / 测试各自条数 |
|---|---|---:|---:|
| Router | v2 → v3 | 650 + 192 = 842 | 456（360 + 96） |
| Executor | v4 → v5 | 650 + 80 = 730 | 672（592 + 80） |
| Memory | v2 → v4 | 650 + 208 = 858 | 464（360 + 104） |

三角色独立 warm-start，micro-batch 1、梯度累积 4，使用 lab GPU 0 / 7 / 5。新增案例覆盖近期风险与当前否定、实际部分行动与未行动、第三方和助手来源、复合记忆证据。旧校准、验证、测试仍留在原划分；旧教师草稿仅可能通过训练回放进入训练集。本轮没有新增 API 教师标注。

全部环境、缓存、数据、权重与日志位于 `/home/ubuntu/data/psych-agent-routing-20261009`。数据目录为 `datasets/three-laya-round5/{role}`，候选为 `models/psych-{router,executor,memory}-domain-{v3,v5,v4}`。部署清单保存四份数据与权重哈希、课程清单哈希；冻结器默认的粗粒度来源字段已明确替换成 `round5_authored_boundaries_with_training_replay`。

常规合成测试 Router 四个任务均为 100%；Executor 行动匹配为 328/342，其他三个任务为 100%；Memory 十四个任务均为 100%。这些成绩与下面的边界失败同时存在，不能用模板留出分数判断真实能力。

## 冻结开发诊断

35 条新增诊断在训练前冻结，不进入训练、校准、验证、测试或检查点选择。另复测旧 40 条与旧 53 条诊断。每条只有全部任务预测正确且通过任务门槛与客户端 0.85 下限才计为“可用正确”；“接受错误”指全部通过门槛但至少一个任务错误。

| 诊断 / 角色 | 可用正确：线上 → 候选 | 接受错误：线上 → 候选 |
|---|---:|---:|
| 新 35 条 / Router（8 条） | 3/8 → 7/8 | 4 → 1 |
| 新 35 条 / Executor（18 条） | 11/18 → 9/18 | 3 → 3 |
| 新 35 条 / Memory（9 条） | 4/9 → 5/9 | 4 → 4 |
| 旧 40 条 / Executor | 27/40 → 38/40 | 3 → 0 |
| 旧 53 条 / Executor（29 条） | 23/29 → 25/29 | 2 → 2 |
| 旧 53 条 / Memory（24 条） | 17/24 → 20/24 | 7 → 4 |

新增诊断 SHA-256：`da12dd6540e51cadf703e905affa1c2ced18735f4f364ee33e9cae3cdb6c8983`。

Router 通过：近期表达过风险后当前否定，仍给出 elevated / professional；准备行动或已经行动的两例仍为 urgent。明确寻找心理咨询渠道的一条仍错误选择 strategy / general，保留为已知模型问题。部署 CPU 与评测 GPU 的八条选择完全一致；CPU 该组 P95 约 1.26 秒，不能用 GPU 评测延迟宣称线上提速。

Executor 拒绝：旧题改善明显，但新题可用覆盖下降，部分数量进展仍漏判。Memory 拒绝：总分改善，但两条原本正确的来源筛选被改成多个候选全相关，旧 write-7 从正确变为错误。先比较总数，再检查逐条受保护行为；平均分提升不能掩盖回归。拒绝候选保存 `promotionStatus=rejected` 与具体原因，服务拒绝加载。

这些是作者编写的开发诊断；历史失败案例用于开发定位，旧题曾指导前轮课程设计。同类语义与模板相似性仍存在。没有完全重复输入不等于真实对话泛化，也不表示临床审核或临床效度。

## 实际应用联调

通过前端代理 → Java → Runtime → lab worker → DeepSeek → 数据库验证，24 项中 23 项通过：

- 当前清单准确显示 Router v3、Executor v4、Memory v2。
- 普通聊天、Agent 创建多步骤目标、完成反思、部分行动同步与撤销均成功；第三方、假设、未来计划没有写入目标进度。
- 自有原始偏好已入记忆，实际检索返回证据并用于回复；后续仍可能由规则在工具预算耗尽时结束。
- 迫近行动保持固定危机支持路径；上一轮“危机表达后明确否定”的原始输入，现在由 Router v3 选 elevated / professional_support，并保存正常回复。

剩余失败是另一种否定措辞“我现在没有想伤害自己”被 Java 关键词规则提前拦截，未到达 Laya。本轮未修改该规则，不能将它记成模型预测，也不宣称全流程全部通过。初版验证中把原始证据文本当作指标字段、把支持级别写成错误枚举的两个断言已按实际接口修正，说明保留在联调报告中。

85 项 Python 测试及改动文件 Ruff 检查通过；本轮没有修改 Java 或前端源码。

## 复现入口

在 `agent-runtime` 目录运行，训练与推理前先加载 `scripts/lab-routing-env.sh`。以下使用 lab 数据盘真实路径；base 的三个角色目录分别指向 router v2、executor v4、memory v2 的原四个数据划分。

```bash
root=/home/ubuntu/data/psych-agent-routing-20261009
python -m training.refine_round --out "$root/datasets/three-laya-round5" --challenge-only
# 训练前对当前正式 worker 保存冻结诊断 baseline.json。
python -m training.refine_round --base "$root/datasets/three-laya-round5-base" \
  --out "$root/datasets/three-laya-round5"
python -m training.train_decisions --profile router --base "$root/models/psych-router-domain-v2" \
  --data "$root/datasets/three-laya-round5/router" --out "$root/models/psych-router-domain-v3" \
  --epochs 3 --micro-batch 1 --allow-teacher-only
python -m training.freeze_deployment --profile router \
  --dataset "$root/datasets/three-laya-round5/router" --checkpoint "$root/models/psych-router-domain-v3"
# 冻结后补充准确数据来源、课程清单哈希；单独启动候选 worker。
# 评测器从私有环境读取各角色 URL/API_KEY，不输出凭据。
python -m training.evaluate_challenge --data "$root/datasets/three-laya-round5/challenge.jsonl" \
  --out candidate.json --label candidate
python -m training.promotion_gate --baseline baseline.json --candidate candidate.json --out gate.json
```

输出目录必须选未使用的新版本；不要覆盖已部署或已拒绝的检查点。分别训练另外两个角色，对每组新旧诊断执行比较。比较工具不自动部署：还须核对启用任务、契约及权重哈希，保存接受/拒绝状态，再按角色切换并验证实际链路。

下一轮优先补不同数量/时长的部分行动泛化、候选位置与用户/助手来源对照、专业资源请求。需要扩大真实对话的独立审核集，不能只重复冻结题直到变绿。
