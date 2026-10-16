# 模型训练与发布产物

训练代码、冻结数据、模型配置与复现说明集中在本目录。当前权重位于同一 GitHub 仓库的 [models-20261014 Release](https://github.com/Vaniloo/psych-agent-engineering/releases/tag/models-20261014)，下载后落入 `artifacts/20261014/models/`，不进入普通 Git 历史。

先读 [发布产物与复现说明](artifacts/20261014/README.md)。当前模型为 Qwen Router、Laya 工具 Executor、Qwen 目标 Executor、Qwen Memory；原始 0.6B 重排器不重复发布。四套课程共 31,286 条，其中训练 20,720 条，其余为 calibration/validation/test；另有 1,089 条冻结挑战行，不用于训练。

## 保留的源码

| 阶段 | 文件 |
| --- | --- |
| 数据与契约 | `decision_data.py`、`export_decision_contracts.py`、`build_domain_decisions.py`、`prepare_dialogue_decisions.py`、`annotate_decisions.py` |
| Qwen 课程 | `qwen_choice_data.py`、`qwen_memory_data.py` |
| 当前训练器 | `train_qwen_choices.py`、`train_qwen_memory.py`、`train_decisions.py` |
| 校准与审核 | `audit_calibration_data.py`、`clean_calibration_cohort.py`、`stratified_calibration.py`、`calibration_ablation.py`、`unrounded_laya.py` |
| 挑战与应用评测 | `qwen_choice_challenge.py`、`qwen_memory_challenge.py`、`qwen_memory_probe.py`、`readiness_challenge.py`、`evaluate_readiness.py`、`offline_task_evaluation.py`、`replay_application_guards.py` |
| 冻结与门槛 | `freeze_deployment.py`、`promotion_gate.py`、`release_readiness.py` |
| 获取产物 | `fetch_artifacts.py`、`prepare_qwen_base.py`、`requirements-models.txt` |

旧六类路由、扩量轮次、定向增广和多适配器拼接等 30 个实验模块已从当前源码移除。完整历史及对应测试仍在提交 [999df41](https://github.com/Vaniloo/psych-agent-engineering/tree/999df41/agent-runtime/training)；带日期的旧命令按当时提交阅读。源码从 55 个 Python 模块精简为 25 个原模块，加上 2 个发布/基座工具。

## 训练边界

训练集优化权重，calibration 拟合温度，validation 选择接受阈值；test 与冻结 challenge 保持留出。标签来自项目策略、程序场景或标记的教师草稿，不能称为独立人工或临床金标。原始正确、正确接受、错误接受和弃权分别报告，应用保护收益另计。

训练不参与 Runtime 正常启动。GPU 环境独立安装；lab 的源码、训练、缓存与检查点必须在 data 盘，使用 `scripts/lab-routing-env.sh`。新训练不会自动替换运行模型。

模型与历史收益见 [角色说明](../../docs/local-laya-agent.md)、[Memory 微调](../../docs/qwen-memory-finetune-20261012.md)、[Router/Executor 微调](../../docs/qwen-router-executor-finetune-20261012.md)、[部署分工](../../docs/router-executor-serving-20261012.md)。

## 记忆生命周期实验分支

`memory_lifecycle_data.py` 从冻结 Memory 课程回放旧七项任务，增加五项有来源约束的二元任务。第一轮数据、报告摘要与校验值见 [memory-lifecycle-20261015-v1](experiments/memory-lifecycle-20261015-v1/README.md)。它是候选实验，当前正式 Release 的模型和清单不变。

在 `agent-runtime` 下生成课程（输出目录必须不存在）：

```sh
python -m training.memory_lifecycle_data \
  --replay training/artifacts/20261014/datasets/qwen-memory \
  --old-challenge training/artifacts/20261014/challenges/qwen-memory/cases.jsonl \
  --out /data/experiments/memory-lifecycle-v1/course
```

训练器用 `--contract psych-memory-lifecycle-v1` 选择独立契约，`--init-adapter` 指向包含旧 training-report 和 adapter 的目录，用 `--learning-rate 0.00003` 继续一轮。完整命令和基座校验说明见实验 README；训练完成不会自动部署。当前候选只做白名单账号的只读旁路，仍存在相关性回退与错误接受。

边界专项第二轮由 `memory_lifecycle_refinement.py` 追加构建，保留第一轮原分区，训练9137行；课程、预先冻结门槛与结果见 [第二轮实验](experiments/memory-lifecycle-20261016-v2/README.md)。新增任务有提升，但未通过正式读取回归门槛，仅启用合成账号旁路。

应用策略v3没有第三轮训练：源文候选、属性约束、可撤销实验写入及复现诊断见 [工程验证](../evaluation/memory-lifecycle-v3-20261016/README.md)。它不改变第二轮模型的训练报告或正式读取回归结论。

## 实验：0.6B事实性门控

`fact_gate_data.py`构建合成二分类课程，`train_fact_gate.py`只训练yes/no得分；与原Memory训练器及权重分离。当前候选未接入在线写入，配置、合成数据和错误明细见[专项试验](experiments/memory-fact-gate-06b-20261016/README.md)。
