# 训练代码导航

训练目录不参与 Runtime 启动。它保留当前训练入口、发布检查和历史实验，避免把多轮实验脚本误认为在线 Agent 的复杂度。

## 当前入口

| 阶段 | 文件 | 用途 |
| --- | --- | --- |
| 固定契约 | `export_decision_contracts.py`、`decision_data.py` | 导出任务/样板，验证数据与划分 |
| 领域数据 | `build_domain_decisions.py`、`prepare_dialogue_decisions.py` | 场景与公开对话转决策样本 |
| 教师草稿 | `annotate_decisions.py` | API 辅助标注，保留 teacher_draft，不充当人工金标 |
| Laya 微调 | `train_decisions.py` | Router / Executor / Memory 独立训练、校准与报告 |
| Qwen 数据 | `qwen_choice_data.py`、`qwen_memory_data.py` | 角色数据清洗与 family 划分 |
| Qwen 微调 | `train_qwen_choices.py`、`train_qwen_memory.py` | 4B LoRA、有限候选概率与独立校准 |
| 挑战评测 | `qwen_choice_challenge.py`、`qwen_memory_challenge.py`、`evaluate_readiness.py` | 冻结开发挑战，原始正确、接受/弃权、应用边界分开统计 |
| 发布 | `freeze_deployment.py`、`promotion_gate.py`、`release_readiness.py` | 固定权重/契约/数据哈希与候选回归门槛 |

在 `agent-runtime/` 用 `python -m training.<module> --help` 查看实际参数。模型训练额外依赖与运行环境记录见各训练文档，不要在轻量 Runtime 容器中安装整套 GPU 环境。

## 历史实验为什么保留

- `train_router.py` / `build_dataset.py`：旧六类 v1 单路由基线，不能部署到当前三角色契约。
- `build_public_scale.py` / `scale_public.py` / `finish_public_round.py`：约三万样本扩量流程与单次后处理。
- `refine_*` / `augment_*` / `scale_executor.py`：定向迭代，记录错误修复和被拒绝的候选。
- `bundle_*` / `task_focus_*`：任务隔离与适配器实验，不能与当前 LoRA 方案混淆。
- `calibration_*` / `stratified_calibration.py` / `audit_calibration_data.py` / `unrounded_laya.py`：标签、校准和数值精度诊断。

这些脚本支持复核实验结果与测试，不是多套线上链路。首次读项目只需看当前入口；不要为了目录更短删除能解释模型选择的证据。

## 训练与评测边界

按会话/场景 family 划分 train、calibration、validation、test，检查跨集合重复。训练集优化权重，calibration 拟合温度，validation 选择阈值；挑战与回归不能冒充独立人工真实用户测试。部署清单校验角色、契约、渲染器与权重摘要，任务不达门槛则禁用。

实验包含程序场景和教师草稿，不能声称临床标签。程序规则的保护收益另计，不能归为模型准确率；原始正确、正确接受、错误接受和弃权同时报告。

训练数据、缓存、权重、检查点和日志在 lab 数据盘，使用 `scripts/lab-routing-env.sh` 配置。仓库发布代码、合同和说明，不发布私有用户记录、API key 或领域权重。

## 结果阅读

- [Qwen Memory 一轮微调](../../docs/qwen-memory-finetune-20261012.md)
- [Qwen Router / Executor 对照](../../docs/qwen-router-executor-finetune-20261012.md)
- [当前 Router 接入与 Executor 分工](../../docs/router-executor-serving-20261012.md)
- [记忆检索优化](../../docs/qwen-memory-serving-20261012.md)
- [三万样本历史扩量](../../docs/training-public-round6.md)
- [校准与数据审核](../../docs/calibration-review-round12.md)

以上均保留当时的版本和评测范围；当前模型列表以根 README 和实际 `/v1/runtime/info` 为准。
