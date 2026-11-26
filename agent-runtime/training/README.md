# 唯一的微调模型 · ToolACE 工具预筛

在这套系统里，主模型通过 function calling 自己选工具；ToolACE 模型在每一步之前给候选工具和“直接回复”打分，只有分数高于“直接回复”的工具才交给主模型。它的输入是截至当前的对话、已有工具调用和结果，以及候选工具的描述与参数结构，输出是每个候选的分数。最终回复由主模型撰写。

## 先读这三个入口

| 环节 | 要讲清楚的问题 | 源码 |
| --- | --- | --- |
| 生成训练样本 | 从公开对话逐步截取历史，用作者的下一动作做标签。同一工具名、首问或相同对话关联的记录分在同一组，再切训练、验证和测试集。 | [course.py](public_tools/course.py) |
| 微调与评测 | 用 Qwen3-Reranker-4B 对每个候选计算 yes/no 的 logit 差，通过 LoRA 学习让示范动作排在前面。同一步出现多个工具时，共享监督权重。 | [train.py](public_tools/train.py) |
| 接入 Agent | 线上预筛按相同契约组装候选，比“直接回复”分数高的工具才交给主模型；评分服务加载基座与 adapter。 | [工具预筛](../psych_agent_runtime/prefilter.py) · [评分服务](../psych_agent_runtime/public_tool_service.py) |

已发布模型使用 **10,556 个训练状态、1 个 epoch、1,320 次参数更新**。在通过输入预算筛选的 1,204 个 ToolACE 测试状态上，下一动作与公开示范的一致率从 **80.65% 提升到 93.36%**。项目内的工具选择效果另用对话回放评估。[原始训练报告](artifacts/20261020/models/toolace-qwen4b-20261019/training-report.json) · [阅读路线](../../docs/source-learning-roadmap.md)

## 复现

以下命令从 `agent-runtime` 目录执行。只演示现有模型时，直接按[部署说明](../../README.md#运行)恢复已发布权重。

**1. 准备公开数据。** 下载器固定 ToolACE 的版本和 Apache-2.0 许可。转换保留原始对话与来源哈希，构建训练集时会校验转换结果。用新的工作目录存放本次输出。

```sh
WORK=$(mktemp -d)
uv run python -m training.public_tools.fetch --out "$WORK/raw"
uv run python -m training.public_tools.convert --raw "$WORK/raw" --out "$WORK/converted"
uv run python -m training.public_tools.course --raw "$WORK/raw" --converted "$WORK/converted" --out "$WORK/course"
```

**2. 在 CUDA 机器上预检和训练。** 准备固定基座后，预检记录通过 token 和候选预算的样本。下面选择全部合格样本，训练一轮。

```sh
uv run --extra executor python -m training.prepare_qwen_base
RELEASE=training/artifacts/20261020
BASE="$RELEASE/base/22e683669bc0f0bd69640a1354a6d0aebcfeede5"
uv run --extra executor python -m training.public_tools.train --preflight-only \
  --data "$WORK/course" --base "$BASE" --out "$WORK/preflight" --max-train-rows 0 --eval-rows 0
uv run --extra executor python -m training.public_tools.train \
  --data "$WORK/course" --base "$BASE" --base-report "$RELEASE/base-report.json" \
  --preflight "$WORK/preflight" --out "$WORK/candidate" --epochs 1 \
  --checkpoint-every-steps 128 --max-wall-seconds 27000 --evaluation-reserve-seconds 3600
```

**3. 核对保存结果。** 重新加载新 adapter，对照训练时保存的验证样本分数。

```sh
uv run --extra executor python -m training.public_tools.verify_candidate \
  --data "$WORK/course" --preflight "$WORK/preflight" --candidate "$WORK/candidate" --out "$WORK/reload.json"
```

已有训练检查点须用生成它的源码、预检和数据续训。旧 Router、Memory、Goal Gate、Laya 实验及其测试可从[精简前的固定提交](https://github.com/Vaniloo/psych-agent-engineering/tree/63ae9a1abbb94ed3d5b7386fc7e598c2aeac74a0/agent-runtime/training)取回。
