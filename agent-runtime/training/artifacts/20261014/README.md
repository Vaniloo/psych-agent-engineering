# 公开模型与冻结课程 · 2026-10-14

数据、配置、报告和哈希集中在这里。大权重是 [同仓库 Release 附件](https://github.com/Vaniloo/psych-agent-engineering/releases/tag/models-20261014)，下载后也进入本目录的 `models/`。这是开发研究发布，不是生产或临床认证。

| 课程 | train | calibration | validation | test | 权重 |
| --- | ---: | ---: | ---: | ---: | --- |
| Qwen Router | 4,432 | 1,018 | 1,016 | 1,006 | 4B LoRA，约 132 MB |
| Qwen Executor | 3,406 | 969 | 957 | 981 | 4B LoRA，约 132 MB，当前仅用于目标判断 |
| Qwen Memory | 2,882 | 832 | 852 | 919 | 4B LoRA，约 132 MB |
| Laya Executor | 10,000 | 672 | 672 | 672 | 完整检查点，约 678 MB，当前用于工具选择 |

四套课程共 31,286 行，20,720 行用于训练；不是独立真实会话数。两个 `challenges/` 目录合计 1,089 条冻结开发挑战行，一行可能展开多项判断。

`datasets/` 保留原始冻结划分及 manifest；`models/` 保留配置与训练报告；`manifest.json` 提供所有文件和 Release 压缩包哈希；`NOTICE.md` 与 `licenses/` 保留来源和许可。程序/教师标签不是人工金标，Laya 课程的 teacher_draft 未改标。公开发布不改变历史 deploymentEnabled/promotionStatus 或门槛。服务器凭据、账号库、本应用真实聊天和完整原始公开对话库未打包。

## 下载与校验

从仓库根进入 `agent-runtime/`，用 Python 3.11+：

```bash
python -m training.fetch_artifacts --verify-data
python -m training.fetch_artifacts
python -m training.fetch_artifacts --verify-only
```

可用 `--models qwen-memory-4b-lora-20261012` 只取一个模型。工具验证压缩包及全部成员，拒绝多余路径、链接和修改过的目标文件。它不会重启或切换服务。

## 基座与环境

三个 LoRA 都依赖 Qwen/Qwen3-Reranker-4B，固定 revision `22e683669bc0f0bd69640a1354a6d0aebcfeede5`，约 8 GB 官方基座未重复打包。0.6B 使用官方原始重排权重，未微调。

在独立 CUDA Python 环境中安装依赖，Torch 按实际 CUDA 选择。关键依赖在 `requirements-models.txt`，没有锁定驱动或所有传递依赖，不保证重训权重逐字节一致。

```bash
pip install -e .
pip install -r training/requirements-models.txt
python -m training.prepare_qwen_base
```

基座目录名必须等于 revision，worker 同时检查文件哈希。已有快照可传 `--existing /path/to/22e683669bc0f0bd69640a1354a6d0aebcfeede5`，随后把 `QWEN_BASE` 指向该目录。工具生成 `base-report.json`。

lab 上先从仓库根加载 `scripts/lab-routing-env.sh`，并在 data 盘 checkout 中运行；所有基座、缓存与输出均在 data 盘。以下命令从 `agent-runtime/` 执行。

## 一轮微调

```bash
export COURSE_ROOT="$PWD/training/artifacts/20261014"
export QWEN_BASE="$COURSE_ROOT/base/22e683669bc0f0bd69640a1354a6d0aebcfeede5"

python -m training.train_qwen_choices --profile router \
  --base "$QWEN_BASE" --base-report "$COURSE_ROOT/base-report.json" \
  --data "$COURSE_ROOT/datasets/qwen-router" \
  --challenge "$COURSE_ROOT/challenges/qwen-choices/cases.jsonl" \
  --out "$COURSE_ROOT/retrained/router" --device cuda:0

python -m training.train_qwen_choices --profile executor \
  --base "$QWEN_BASE" --base-report "$COURSE_ROOT/base-report.json" \
  --data "$COURSE_ROOT/datasets/qwen-executor" \
  --challenge "$COURSE_ROOT/challenges/qwen-choices/cases.jsonl" \
  --out "$COURSE_ROOT/retrained/executor" --device cuda:0

python -m training.train_qwen_memory \
  --base "$QWEN_BASE" --base-report "$COURSE_ROOT/base-report.json" \
  --data "$COURSE_ROOT/datasets/qwen-memory" \
  --challenge "$COURSE_ROOT/challenges/qwen-memory/cases.jsonl" \
  --out "$COURSE_ROOT/retrained/memory" --device cuda:0
```

每个角色独立一轮 LoRA，r16/alpha32/dropout 0.05。根据 GPU 容量顺序运行，输出全新目录，不覆盖公开权重和在线配置；课程/renderer 哈希不匹配会停止。挑战不用于优化或调阈值。

Laya 原始课程含教师草稿，需显式开启实验模式：

```bash
python -m training.train_decisions --profile executor \
  --data "$COURSE_ROOT/datasets/laya-executor" \
  --out "$COURSE_ROOT/retrained/laya-executor" \
  --epochs 1 --micro-batch 1 --max-len 1536 --head-max-len 512 \
  --allow-teacher-only
```

这是当前训练入口加原始课程，不重演旧轮次自动后处理，也不保证生成同一部署清单。公开的 Laya 权重及原部署报告可直接用于现有服务；旧后处理保存在 Git 历史。

## 启动公开权重

设置独立私有的 `QWEN_CHOICE_API_KEY` 与 `QWEN_MEMORY_API_KEY`，不要写入仓库。三个命令分别在独立进程执行：

```bash
python -m psych_agent_runtime.qwen_choice_service --profile router \
  --base "$QWEN_BASE" \
  --checkpoint "$COURSE_ROOT/models/qwen-router-4b-lora-20261012" --port 18098

python -m psych_agent_runtime.qwen_choice_service --profile executor \
  --base "$QWEN_BASE" \
  --checkpoint "$COURSE_ROOT/models/qwen-executor-4b-lora-20261012" --port 18099

python -m psych_agent_runtime.qwen_memory_service --base "$QWEN_BASE" \
  --checkpoint "$COURSE_ROOT/models/qwen-memory-4b-lora-20261012" --port 18096
```

Laya 工具 worker 另设置私有 `DECISION_API_KEY`，按下面方式启动；首次加载还可能获取官方 encoder 配置与 tokenizer：

```bash
export DECISION_PROFILE=executor
export DECISION_CHECKPOINT="$COURSE_ROOT/models/psych-executor-domain-v6"
uvicorn psych_agent_runtime.routing_service:app --host 127.0.0.1 --port 18093
```

先按需求单独启动，不保证任意显卡同时容纳全部 worker。应用配置见 [角色部署](../../../../docs/local-laya-agent.md)。工具/目标分工由 Runtime 配置决定，启动 worker 不等于应用已经接入。

此次发布核对权重、冻结数据哈希及内容/来源，未追加训练或重启在线模型。效果与限制见 [训练导航](../../README.md) 的带日期报告。
