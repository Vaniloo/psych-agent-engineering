# 训练源码与冻结产物

只保留现用模型、回退模型和其复现所需代码。工具输入格式位于 [`psych_agent_runtime/public_tool_contract.py`](../psych_agent_runtime/public_tool_contract.py)，训练与服务共用，已训练模型的renderer指纹保持不变。GPU依赖与普通Runtime分开安装。

## 当前入口

| 用途 | 数据 | 训练模块 | 实际模型 |
| --- | --- | --- | --- |
| 动态工具选择 | ToolACE固定作者版本 → `public_tools.fetch/convert/course` | `public_tools.train` | `psych-toolace-qwen4b-20261019` |
| Router / 旧目标决策 | 冻结课程 → `qwen_choice_data` | `train_qwen_choices` | 2026-10-12 Qwen4B适配器 |
| 基础Memory | 冻结课程 → `qwen_memory_data` | `train_qwen_memory` | `psych-memory-v4` |
| 长期事实生命周期 | `memory_lifecycle_data` → `memory_lifecycle_refinement` | 同上，`--contract psych-memory-lifecycle-v1` | v2，显式账号范围启用 |
| 目标门控 | `goal_gate_data` | 同上，`--contract psych-goal-gate-v1` | 独立4B + 生成模型写前核对 |
| Laya回退 | `artifacts/20261014/datasets/laya-executor` | `train_decisions` | Executor v6故障回退 |
| 0.6B重排 | 原作者固定revision | **不训练** | 记忆和知识分别使用独立指令 |

以下命令均在 `agent-runtime` 下运行。所有数据、模型、缓存和输出指定到数据盘，输出目录必须未使用。

## 1. ToolACE公开示范训练

先读 [public_tools/README](public_tools/README.md)。按作者固定版本下载、校验、转换和分组切分；ToolACE为主线，APIGen-MT是独立非商业研究支线，不混入默认训练。

```bash
python -m training.public_tools.fetch --help
python -m training.public_tools.convert --help
python -m training.public_tools.course --help
python -m training.public_tools.train --help
python -m training.public_tools.verify_candidate --help
```

完整单轮示意：

```bash
python -m training.public_tools.train --preflight-only \
  --data /data/toolace-course --base /data/base/snapshots/REVISION \
  --out /data/toolace-preflight --max-train-rows 0 --eval-rows 0

python -m training.public_tools.train \
  --data /data/toolace-course --base /data/base/snapshots/REVISION \
  --base-report /data/base-report.json --preflight /data/toolace-preflight \
  --out /data/toolace-candidate --epochs 1 \
  --checkpoint-every-steps 128 --max-wall-seconds 27000 \
  --evaluation-reserve-seconds 3600
```

损失是作者示范下一动作的候选间CE；未选工具不等于绝对无用。文字动作可能回答、澄清或解释，不等于目标完成。完整训练10,556个预算合格状态、1,320次更新，流程约5小时47分；公开test示范一致率80.65%→93.36%，不等于项目任务完成率。见 [训练](../../docs/public-tool-next-action-20261019.md)、[项目回放](../../docs/public-tool-project-replay-20261019.md) 和 [接入](../../docs/public-tool-serving-20261019.md)。

## 2. Router、Memory和目标模型

`train_qwen_choices.py`训练有限候选任务；`train_qwen_memory.py`复用二分类流程，以显式契约选择任务和renderer。先审核数据分区、标签来源和校准支持。

```bash
python -m training.train_qwen_choices --profile router \
  --base /data/pinned-base --base-report /data/base-report.json \
  --data /data/router-course --challenge /data/router-challenge --out /data/router-candidate

python -m training.goal_gate_data --out /data/goal-course
python -m training.train_qwen_memory --contract psych-goal-gate-v1 \
  --base /data/pinned-base --base-report /data/base-report.json \
  --data /data/goal-course \
  --challenge training/experiments/goal-gate-20261017-v1/challenge \
  --out /data/goal-candidate
```

格式以各入口 `--help` 和manifest为准。基础Memory与生命周期的初始化、挑战和校准见 [Memory训练](../../docs/qwen-memory-finetune-20261012.md)、[生命周期v2](experiments/memory-lifecycle-20261016-v2/README.md)、[目标门控](experiments/goal-gate-20261017-v1/README.md)。v2在v1课程上追加，因此保留 `memory_lifecycle_data.py` 这一真实依赖。

## 3. 冻结权重

两个Release共同组成当前清单：

- [models-20261014](https://github.com/Vaniloo/psych-agent-engineering/releases/tag/models-20261014)：Router、旧目标决策、基础Memory和Laya回退；课程及许可在 `artifacts/20261014/`。
- [freeze-20261020-attune](https://github.com/Vaniloo/psych-agent-engineering/releases/tag/freeze-20261020-attune)：ToolACE工具选择、目标门控、生命周期v2三个新增适配器，元数据在 `artifacts/20261020/`。

```bash
python -m training.fetch_artifacts --verify-data
python -m training.fetch_artifacts
python -m training.fetch_artifacts --root training/artifacts/20261020
python -m training.fetch_artifacts --root training/artifacts/20261020 --verify-only
```

Qwen基座和原版0.6B从作者获取并按固定revision/文件SHA校验，不重复分发。适配器通过Release下载到同目录，不进入Git历史。依赖见 `requirements-models.txt`，安装到独立CUDA环境；普通Runtime不需要Torch。公开数据转换还需要 `pyarrow`，可用 `uv sync --extra training-data`。

## 其余保留代码

- `decision_data / build_domain_decisions / prepare_dialogue_decisions / annotate_decisions`：旧领域课程及Laya回退复现。
- `train_decisions / unrounded_laya` 与校准模块：Laya训练、阈值和原始logit校准。
- `qwen_*_challenge / evaluate_* / offline_task_evaluation`：分角色挑战与现行模型诊断。
- `freeze_deployment / promotion_gate / release_readiness`：来源冻结、比较及晋级门槛。

本次归档了未晋级的toolgen v1/v2、旧utility原型、0.6B事实性试训代码及专属测试；没有现用服务依赖。失败结论保留在历史报告，清理前源码另有本地归档；已提交的0.6B试验可从历史提交 `da83a33` 查阅。当前不提供这些废弃入口，历史报告命令不是当前运行说明。

当前源码可继续训练同契约模型，不冒充旧训练时逐字节源码。旧报告源码hash、数据hash和权重hash保留原值；恢复旧优化器检查点应使用对应冻结源码。训练不自动替换服务，仍需独立评测和显式切换。
