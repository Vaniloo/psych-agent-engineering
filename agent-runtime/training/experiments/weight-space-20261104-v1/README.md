# Weight-Space LoRA pilot · 2026-11-04

结论：**保留原始记忆生命周期 v2，诊断补丁不晋级。** [完整实验说明](../../../../docs/weight-space-pilot-20261104.md)包含方法来源、操作、失败恢复、结果与边界。

- `summary.json`：两套评测的逐任务指标、配对修复/退步、adapter SHA、原权重未变与不部署决定。
- `protocol.json`：首次前向前冻结的 10 个程序、375 条开发选择、数据 hash 和晋级规则。
- `course-evaluation-protocol.json`：只恢复完整 test 的源码 hash、原选型/协议 SHA；不重新选择补丁。
- `challenge-evaluation-protocol.json`：沿用同一选择，并核对原有 491 条挑战 SHA 的补充诊断协议。

源码为 [`training/weight_space.py`](../../weight_space.py)。训练生成器、在线模型和数据库均未改动。LoRA 大文件及逐例分数留在会话 outputs 和 lab 的独立 data 盘作业目录，不加入普通 Git 历史。

摘要中的“正确”表示符合开发课程标签；这些数据不是独立人工金标。原有校准不能直接用于编辑后的 adapter。实验首次写出 test 结果时遇到缺失 `id` 字段，后续按输入生成稳定标识并复用冻结选择恢复；失败与两次评测源码快照保留，见完整说明。
