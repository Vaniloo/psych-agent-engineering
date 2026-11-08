# 原始 Memory LoRA 激活诊断

本实验**只做行为测试、激活采集与临时激活干预，不修改或导出模型权重**。[完整报告](../../../../docs/activation-memory-diagnostics-20261104.md)。

- `cases.json`：16 个已知失败家族、68 条助手编写的开发对照；不是训练集或独立人工金标。定位组/确认组预先划分，任务无关 source 字段控制单独标注。
- `failure-audit.json`：前一轮 491 条挑战中 20 条不一致及按任务契约的初步审核；原标签保持不变。
- `summary.json` / `protocol.json`：主诊断摘要和实际执行协议。
- `path-summary.json` / `path-protocol.json`：沿用冻结候选位置的部分路径检验。
- `controls-summary.json` / `matched-donors.json`：来自既有挑战的异例同任务同标签 donor 结果，未重新选点。
- `audit.json`：源码/输入/固定位置选择 SHA、逐例指标和结构性负对照复核。

源码为 [`training/activation_diagnostics.py`](../../activation_diagnostics.py)，测试为 [`test_activation_diagnostics.py`](../../../tests/test_activation_diagnostics.py)。原始逐例轨迹、激活缓存和实际源码快照保存在 lab data 盘及本机会话 `outputs/activation-memory-20261104/`，不将大激活文件加入 Git。

层号从 0 开始。单点干预不等于完整因果路径，logit lens 不等于因果证据；确认组主要同义对照只有 5 对。保留英文难例无可用正确 donor、任务边界和不同批量的 BF16 分数差异，不能将临时纠偏数量当作上线提升。

结论：源字段末端的 L17 Attention、判断位置的 L23/L26 MLP 是敏感候选；下游阻断仅支持部分中介。更强的异例 donor 也让两个晚层 MLP 转对 3/5，因而不支持“已定位语义错误电路”的说法。保持原权重，下一步仍是定位和扩大确认，不从本轮自动生成权重补丁。
