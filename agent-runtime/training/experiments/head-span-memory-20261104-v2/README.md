# 第二阶段：head / 语义范围诊断

结论：**无可用于权重编辑的稳定头级补丁，继续保留原权重。** [完整报告](../../../../docs/head-span-memory-diagnostics-20261104.md)。

- `cases.json`：24 个家族、156 条助手编写的开发对照，预先分定位与确认。角色 span 元数据不进入模型输入；显式记忆请求、翻译均不作同义 donor。
- `protocol.json` / `summary.json`：实际输入与源码 SHA、160 点扫描规则、8 对主要确认结果及不部署决定。
- `selection.json`：仅用定位组选择的三个 head/位置；不是权重补丁清单。
- `followup-protocol.json` / `followup-summary.json`：冻结位置的整节点、最终输出状态正对照及未通过的条件必要性门槛。
- `audit.json`：源文件/输入/固定选择、零分约定、逐例指标及 Attention 重构误差核验。

实现为 [`head_span_diagnostics.py`](../../head_span_diagnostics.py)，测试为 [`test_head_span_diagnostics.py`](../../../tests/test_head_span_diagnostics.py)。大激活文件及完整逐例表留在 lab data 盘和会话 `outputs/head-span-memory-20261104-v2/`，不加入 Git。没有新模型权重。

主要限制：定位组只剩 4 对；确认组 8 对全部是预约更正（中英文各 4），缺少合格的关系绑定失败确认。模板仍共享，标签不是独立人工金标。不能把差分排名当作语义因果证明，也不能把最后 residual 移植的 8/8 当作修复收益。
