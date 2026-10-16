# Memory 生命周期第一轮：候选，未部署

实验从10月15日启动，10月16日整理。正式模型仍为 `psych-memory-qwen4b-lora-20261012`，冻结 Release 不变。结果见 [阶段报告](../../../../docs/memory-lifecycle-v1-20261016.md)。

## 产物

- `training-report.json`：训练参数、基座/初始适配器/候选适配器 SHA、校准策略及逐任务结果。
- `dataset-manifest.json`：四分区数量、SHA、来源与合成数据限制。
- `challenge-cases.jsonl`、`challenge-manifest.json`：371条冻结开发挑战，旧311条+新60条。它们不参与训练或阈值选择。
- `training-source-sha256.json`：服务器实际训练时的源码快照校验值。后续 Runtime 旁路接入代码另有修改，不能将整个当前目录当成当时逐字相同的源码。
- `shadow-results.json`、`live-integration-summary.json`：候选HTTP的8项只读诊断和1个专用合成账号的网关联调，未开放真实账号写入。
- `decision.json`：未晋升决定及逐任务对照，不是自动部署记录。

候选 adapter 留在 lab 的 data 盘 `models/qwen-memory-lifecycle-4b-lora-20261015-v1/`，未上传为正式 Release。未复制旧训练集；用已发布的冻结课程和 `training.memory_lifecycle_data` 可重建本轮课程，并核对 manifest 的 SHA。新增数据与挑战均为项目编写的程序/开发标签，没有采集真实聊天，没有调用教师API。

## 复现训练

在独立 GPU 环境安装 `training/requirements-models.txt`，按主训练 README 生成新课程。基座为 `Qwen/Qwen3-Reranker-4B`，revision `22e683669bc0f0bd69640a1354a6d0aebcfeede5`；旧 adapter 从冻结 Release 获取并按原清单校验。`base-report.json` 使用 `model`、`revision`、`weightsSha256` 三项，值分别来自本实验报告的 `baseModel`、`baseRevision`、`baseWeightsSha256`。

以下命令在 `agent-runtime` 下运行；`/data/experiments` 示例路径须换成实际 data 盘目录，输出目录必须不存在。一次执行训练一轮，不自动接入 Runtime。

```sh
python -m training.train_qwen_memory \
  --base /data/cache/qwen4b/22e683669bc0f0bd69640a1354a6d0aebcfeede5 \
  --base-report /data/experiments/base-report.json \
  --data /data/experiments/memory-lifecycle-v1/course \
  --challenge /data/experiments/memory-lifecycle-v1/course/challenge/cases.jsonl \
  --out /data/experiments/memory-lifecycle-v1/model \
  --contract psych-memory-lifecycle-v1 \
  --init-adapter /data/models/qwen-memory-4b-lora-20261012 \
  --model-name psych-memory-lifecycle-qwen4b-20261015-v1 \
  --memory-fraction 0.18 \
  --learning-rate 0.00003
```

实际为 BF16 LoRA r16/alpha32，微批4、梯度累积4，训练5882行、368次优化，峰值分配8714.8MiB。`trainingSeconds=551.99` 从优化循环开始计时并包含后续校准与评测；不能当作含模型加载和训练前对照的全作业时间。

## 只读服务验证

候选 worker 通过 `--checkpoint` 读取实验报告选择独立的12任务契约，旧7任务 worker 仍使用 v4。单独设置候选服务密钥，勿将候选 URL 写入正式 `MEMORY_BASE_URL`。

离线合成旁路诊断：

```sh
python -m benchmarks.memory_lifecycle_shadow \
  --url http://127.0.0.1:18100 \
  --key-env MEMORY_LIFECYCLE_SHADOW_KEY \
  --out /data/experiments/memory-lifecycle-v1/shadow-diagnostic
```

密钥通过环境变量私下配置。诊断只在新建的合成 SQLite 数据库预置两条本人事实和一条其他账号事实，再运行8种只读建议；不变更真实聊天或正式记忆。它报告实际失败，不把“数据库没被修改”当作语义预测正确。

Runtime 旁路使用 `MEMORY_LIFECYCLE_SHADOW_URL`、`MEMORY_LIFECYCLE_SHADOW_KEY`、显式 `MEMORY_LIFECYCLE_SHADOW_USERS` 和独立 `MEMORY_LIFECYCLE_STORE_PATH`；默认关闭，拒绝 `*`。此阶段不启用自动事实写入，也未完成旧索引迁移或存储量优化。
