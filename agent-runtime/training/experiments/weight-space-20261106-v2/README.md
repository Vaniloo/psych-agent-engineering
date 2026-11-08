# 第二轮权重修改诊断

固定候选见 programs.json；最终负结果见 [报告](../../../../docs/weight-space-edit-round-20261106.md)。

复现时在 agent-runtime 运行 `python -m training.weight_space --help`。先提供 `--programs-file` 本目录 programs.json、冻结基座、current/parent 适配器、课程 data 和全新 out，使用 `--per-cell 10000 --batch-size 16` 评估完整验证集并冻结选择；再以新的 out 和 `--selection-from` 前次 selection.json、`--challenge-file` 原课程 challenge/cases.jsonl 运行回归。

本目录保留方案、协议、选择及汇总，不包含临时 PID、机器控制脚本、权重二进制或私人聊天。历史协议中的机器路径仅为当时的来源记录，复现须指定自己的数据盘路径。
