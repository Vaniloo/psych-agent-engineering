# 目标门控4B实验

候选`psych-goal-gate-qwen4b-20261017-v1`不是正式Release模型。首轮4B单独门控存在误写；当前白名单接入的是4B意图门控 + 现有生成模型写前核对/有限补判的组合。完整范围、失败及最终链路见[实验记录](../../../../docs/goal-model-gate-20261017.md)。

- `challenge/`：冻结29个合成场景、250项二分类决策，不参与权重或阈值选择。
- `fresh-cases.json`：31个合成口语开发场景，后续用于候选格式改进，不能称为独立人工评测。
- `training-report.json`：训练/校准及原生评分报告，`deploymentEnabled=false`表示没有替换正式角色。`trainingSeconds`包含评估时间。
- `compound-results.json`：4B单独门控的组合误写/漏写。
- `combined-http-results.json`：最终组合链路的HTTP开发回归，不包含应用私钥、写入凭证或真实用户对话。

在`agent-runtime`中复现课程（目标目录必须不存在）：

```sh
python -m training.goal_gate_data --out /data/experiments/goal-gate-v1/course
```

复用`training.train_qwen_memory`，选择`--contract psych-goal-gate-v1`。本轮参数：Qwen3-Reranker-4B基座，从已发布目标Executor4B的LoRA继续，`--learning-rate 0.0001 --memory-fraction 0.28`，其余为一轮、batch4/累积4、种子1412；基座与初始化适配器校验值在报告中。`--data`指向课程，`--challenge`指向本目录`challenge/cases.jsonl`，`--out`必须为不存在的data盘目录。

训练结束后复放原生决策：

```sh
python -m training.evaluate_goal_gate \
  --checkpoint /data/models/goal-gate-v1 \
  --challenge training/experiments/goal-gate-20261017-v1/challenge \
  --out /data/experiments/goal-gate-v1/compound-results.json
```

该命令仅复放4B评分，不能验证生成模型补判、数据库落库或前端事件。在线组合机制由`GoalModelGate`及`GoalWriteReviewer`执行，必须单独通过实服务联调后再启用白名单；不支持训练完自动全量替换。
