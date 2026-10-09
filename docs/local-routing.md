# 本地决策路由与微调

官方 [TypeSafe Jev 文档](https://docs.typesafe.ai/models#customizing-jev) 当前未提供客户微调接口或开放权重。本项目使用独立开源实现 [Laya](https://github.com/NandhaKishorM/laya)，基座为 [laya-multilingual](https://huggingface.co/convaiinnovations/laya-multilingual)（约 322M 参数，Apache-2.0），不能把它称作官方 Jev 权重。

本页仅记录旧 `psych-route-v1` 六类路由基线。当前架构使用 [三个独立 Laya 角色](local-laya-agent.md)，旧权重没有风险、工具执行或记忆任务，不能加载到新 worker。

实测：lab 单张 RTX PRO 6000 Blackwell，Torch 2.8/cu128，soft CE 全参数微调四轮约 97.6 秒，训练时进程显存约 8GB。41 条独立合成测试准确率从 36.6% 到 87.8%，macro F1 从 0.282 到 0.874；校准温度 2.475。验证门槛未通过，检查点 `enabled=false`，不自动替换业务路由。GPU HTTP 服务已验证六类示例和 Planner 回退后停止，释放 GPU；权重与日志保留在数据盘。

## 执行边界

路由服务与 LangGraph 分进程，运行时不需要加载 Torch。`psych-route-v1` 协议输出 `chat / knowledge / memory / dashboard / strategy / clarify` 六选一，完整概率分布、校准状态和接收阈值。运行时验证协议、归一化、最大概率标签和本地最低概率（默认 0.85）。服务接收且运行时通过阈值才执行该路线，否则走原有受限 Planner。

| 路线 | 能力与回复要求 |
| --- | --- |
| chat | 陪伴和倾听，无工具 |
| knowledge | 查询知识库，解释概念和适用边界 |
| strategy | 查询知识库，结合处境给少量行动步骤 |
| memory | 使用已加载记忆，不推测未记录的经历 |
| dashboard | 查询已存在的报告和统计，不自动生成报告 |
| clarify | 无工具，提出一个澄清问题 |

确定性危机扫描先于模型路由。路由模型不能产生工具名、URL、身份或执行参数；模型进程不持有业务数据库凭据。前端 `plan` 事件显示路线、概率及回退状态。证据不足由 `evidence` 事件标记。`strategy` 目前是带专用约束的检索回复流程，并非多步自主规划器。

## lab 数据盘训练

所有安装、模型、缓存、临时文件和日志写入 `/home/ubuntu/data/psych-agent-routing-20261009`。使用独立虚拟环境，清除服务器共享 `PYTHONPATH`，不改服务器全局环境，不把密码保存到代码。

从仓库根目录执行以下步骤；源代码也应位于数据盘：

```bash
source scripts/lab-routing-env.sh
python3 -m venv "$PSYCH_ROUTE_ROOT/.venv"
PY="$PSYCH_ROUTE_ROOT/.venv/bin/python"
"$PY" -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128
"$PY" -m pip install laya==0.4.1 transformers==4.57.6 fastapi uvicorn httpx
export PYTHONPATH="$PWD/agent-runtime"
"$PY" agent-runtime/training/build_dataset.py --out "$PSYCH_ROUTE_ROOT/datasets/bootstrap"
# 先确认所选 GPU 空闲；编号仅为示例。训练显存限额为单卡总量的 22%。
CUDA_VISIBLE_DEVICES=0 "$PY" -u agent-runtime/training/train_router.py \
  --data "$PSYCH_ROUTE_ROOT/datasets/bootstrap" \
  --out "$PSYCH_ROUTE_ROOT/models/psych-route-soft-ce-s42" \
  --loss soft-ce --epochs 4 --seed 42
```

默认全参数监督微调（soft CE），不是从零预训练；同一脚本支持 `--loss rlcd` 后训练实验。当前没有声称完成 RLCD 对比。Torch 2.8/cu128 支持 lab 的 Blackwell GPU。ModernBERT 内部编译在训练时关闭，避免依赖系统 C 编译器。CPU/MPS 可探索 `--device cpu/mps`，但本次只实测 CUDA。

训练集包含 96 个手写场景的 3456 种措辞变体，独立校准集和验证集各 24 个场景、864 条。测试集有 41 条新措辞和上下文省略请求。按场景隔离，避免把同场景的装饰变体随机分到不同集合。它们全是工程用合成数据，不含真实用户对话，不能证明线上泛化或心理支持质量。

训练按验证损失保存最佳轮次，独立校准集拟合温度，验证集选择接收阈值，最终测试在保存并重载的检查点上执行。阈值选择仅取每个验证场景一条，要求至少 20 条被接收且单侧 95% Wilson 错误率上界不超过 15%。这是小样本工程门槛；阈值搜索和合成分布限制了它的统计意义。报告同时给出准确率、macro F1、ECE、Brier、混淆矩阵和失败案例。

产物为 `model.safetensors`、编码器配置、tokenizer、`rl_agent_config.json`、`training-report.json` 和 `deployment.json`。服务验证权重 SHA256；如果门槛失败，`enabled=false`，保留权重供分析，运行时继续回退。

## 当前用途

保留训练脚本、数据构建和历史评测用于复现实验；`train_router.py` 输出 v1 基线检查点。它不能部署到新的 `psych-router-v2` 服务。新服务的部署配置与训练契约见 [三 Laya Agent](local-laya-agent.md)，请勿把旧 enabled/calibrated 结果迁移为新任务的能力声明。
