# 0.6B事实性门控专项试验

一轮独立LoRA二分类试验，未启用在线写入。详见[报告](../../../../docs/memory-fact-gate-06b-20261016.md)。

- `data/`：合成课程、分区SHA和固定提示，事实族隔离，仍共享模板。
- `diagnostic-cases.json`、`fresh-authored.json`：开发诊断与训练前冻结的新编自然题，不用于训练/阈值选择；标注修订见`annotation-review.json`。
- `training-report.json`、`natural-scored.json`：配置、校准、错误明细、adapter SHA。
- `reload-results.json`：重新加载结果与模型计算耗时。预设margin探测和训练后校准阈值并非同一接受规则。
- `existing-4b-results.json`、`composition-results.json`：既有grounded头和4个误接受的只读参考，不是同任务专训4B的公平对照。
- `zero-shot-*-results.json`：英文/中文原生探测。

从`agent-runtime`运行，目标目录必须不存在，GPU依赖复用Qwen训练环境：

```sh
python -m training.fact_gate_data --out /data/new-curriculum
python -m training.train_fact_gate \
  --model /data/pinned-qwen3-0.6b-snapshot --data /data/new-curriculum \
  --diagnostic training/experiments/memory-fact-gate-06b-20261016/diagnostic-cases.json \
  --holdout training/experiments/memory-fact-gate-06b-20261016/fresh-authored.json \
  --out /data/new-pilot
```

权重只保存在服务器data盘的候选目录，本目录不复制基座或冒充正式模型发布。不使用私人聊天或教师API，不修改线上角色权重。
