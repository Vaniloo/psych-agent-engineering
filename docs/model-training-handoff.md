# 模型训练交接

整理日期：2026-10-21；基线 `freeze-20261020-attune` / `48cfcb9`。返回[项目交接入口](../HANDOFF.md)。本页以冻结清单、训练报告和当前源码为依据；未在本次文档整理中重新连接 lab、启动训练或验证线上模型。

## 1. 先认清：训练什么、产物在哪里

本项目主要微调判断模型，不是在训练一个心理咨询生成大模型。生成回答、规划和写前理解使用独立生成服务。JEV 风格体现在“有限候选判断与生成分离”；当前主线是 Qwen LoRA，不是官方 JEV 权重或 API。

Qwen 4B 共同基座：`Qwen/Qwen3-Reranker-4B`，固定 revision：

```text
22e683669bc0f0bd69640a1354a6d0aebcfeede5
```

模型沿用原始 LM head 的 yes/no logits 进行判断，未为每个工具增加固定输出维度。ToolACE 将对话和动态工具目录放入输入，对每个候选共享评分；Router 等任务的候选及监督契约不同，不能混用适配器和阈值。

| 模型 / 状态 | 契约 | 数据与训练入口 | 冻结产物目录 |
| --- | --- | --- | --- |
| Router，主路线判断 | `psych-router-v2` | `qwen_choice_data.py` → `train_qwen_choices.py --profile router` | `artifacts/20261014/models/qwen-router-4b-lora-20261012/` |
| ToolACE，主工具选择 | `public-tool-next-action-v1` | `public_tools/fetch.py` → `convert.py` → `course.py` → `train.py` | `artifacts/20261020/models/toolace-qwen4b-20261019/` |
| 基础 Memory，读取与分类 | `psych-memory-v4` | `qwen_memory_data.py` → `train_qwen_memory.py` | `artifacts/20261014/models/qwen-memory-4b-lora-20261012/` |
| 生命周期 v2，受控账号范围 | `psych-memory-lifecycle-v1` | `memory_lifecycle_data.py` → `memory_lifecycle_refinement.py` → `train_qwen_memory.py --contract psych-memory-lifecycle-v1` | `artifacts/20261020/models/qwen-memory-lifecycle-4b-lora-20261016-v2/` |
| 目标门控，组合链路 | `psych-goal-gate-v1` | `goal_gate_data.py` → `train_qwen_memory.py --contract psych-goal-gate-v1` | `artifacts/20261020/models/qwen-goal-gate-4b-lora-20261017-v1/` |
| 旧目标判断，兼容路径 | `psych-executor-v2` | `qwen_choice_data.py` → `train_qwen_choices.py --profile executor` | `artifacts/20261014/models/qwen-executor-4b-lora-20261012/` |
| Laya v6，工具故障回退 | `psych-executor-v2` | 冻结 `datasets/laya-executor/` → `train_decisions.py` | `artifacts/20261014/models/psych-executor-domain-v6/` |
| 原版 0.6B，记忆 / 知识重排 | 两种独立任务指令 | 不微调；`qwen_rerank_service.py`，客户端为 `memory_reranker.py` / `knowledge_reranker.py` | 从原作者固定版本获取，不在自训 LoRA 包内 |

表中相对源码路径均在 `agent-runtime/training/`，产物目录也相对该目录；服务与契约文件在 `agent-runtime/psych_agent_runtime/`。目录名、报告 modelName 和服务展示名可能不同，身份以契约、基座 revision、adapter SHA 和 manifest 对齐。

### 参数与规模快照

以下六个 Qwen 适配器报告均为 1 轮、BF16 LoRA r16 / alpha32；不同表行的“训练行”定义不同，不应相加当成独立对话数。

| 模型 | 训练行 / 状态 | 学习率 | 优化器更新 | 初始化 |
| --- | ---: | ---: | ---: | --- |
| Router | 4,432 | 1e-4 | 277 | 固定基座 |
| 旧目标 Executor | 3,406 | 1e-4 | 213 | 固定基座 |
| 基础 Memory | 2,882 | 1e-4 | 181 | 固定基座 |
| 生命周期 v2 | 9,137 | 2e-5 | 572 | 10 月 12 日基础 Memory 适配器，不是 v1 候选 |
| 目标门控 | 5,504 | 1e-4 | 344 | 10 月 12 日目标 Executor 适配器 |
| ToolACE | 10,556 个预算合格决策状态 | 2e-5 | 1,320 | 固定基座，不继承旧失败工具 adapter |

原始参数、数据 hash、初始化 adapter hash、校准策略与成绩以各模型目录的 `training-report.json` 为准。旧报告的 `deploymentEnabled=false` 表示训练时没有自动部署，不证明后来没有接入；后续接入看冻结说明和服务验收。

## 2. 训练代码地图

先读完 ToolACE 这一条链路，再比较其他任务，不必从历史轮次逐篇读起。

| 阶段 | 关键文件 / 函数 | 需要理解的内容 |
| --- | --- | --- |
| 数据下载 | [`public_tools/fetch.py`](../agent-runtime/training/public_tools/fetch.py)：`SOURCES/fetch/verify` | 固定 revision、下载清单、SHA、完整状态；不调用教师 |
| 格式转换 | [`public_tools/convert.py`](../agent-runtime/training/public_tools/convert.py)：`source_tools/parse_toolace_calls/convert_record/run` | 原文、工具目录、统一事件与异常；未知不改成负标签 |
| 样本构建 | [`public_tools/course.py`](../agent-runtime/training/public_tools/course.py)：`decisions/keys/assign_components/build` | 目标前上下文、候选与目标索引；去重、连通组件切分 |
| 来源复核 | [`public_tools/verify_interchange.py`](../agent-runtime/training/public_tools/verify_interchange.py)：`check` | 对照原始记录、消息、来源、索引与 hash |
| 输入契约 | [`public_tool_contract.py`](../agent-runtime/psych_agent_runtime/public_tool_contract.py)：`render/specification` | 训练和服务共用；完整工具目录 + 单个被评分候选 |
| 训练 | [`public_tools/train.py`](../agent-runtime/training/public_tools/train.py)：`encode/prepare/run` | 长度预检、LoRA、梯度累积、检查点、预算、评测 |
| 损失 | [`public_tools/contract.py`](../agent-runtime/training/public_tools/contract.py)：`loss` | 示范下一动作候选间 CE，不是绝对适用度 BCE |
| 磁盘重载 | [`public_tools/verify_candidate.py`](../agent-runtime/training/public_tools/verify_candidate.py)：`verify` | adapter SHA 和既有验证样本的重载一致性，不是新增质量评测 |
| 模型服务 | [`public_tool_service.py`](../agent-runtime/psych_agent_runtime/public_tool_service.py)：`create_app/predict/score` | yes/no logit 差、候选对齐、输入预算、单推理槽与鉴权 |
| 应用接入 | [`public_tool_client.py`](../agent-runtime/psych_agent_runtime/public_tool_client.py)：`candidate_row/PublicToolDecisions.evaluate` | 真实授权工具、移除宿主绑定身份参数、TEXT、校验和回退 |

其他任务的差异入口：

- Router / 旧目标：`qwen_choice_contract.py` + `train_qwen_choices.py` + `qwen_choice_service.py`。
- 基础 Memory：`qwen_memory_contract.py` + `train_qwen_memory.py` + `qwen_memory_service.py`。
- 生命周期：`memory_lifecycle_contract.py` 决定 write_worthy、grounded、duplicate、supersedes、conflict 等判断；`memory_shadow.py` / `memory_projection.py` / `memory_ledger.py` 才负责应用门控与受控写入。
- 目标门控：`goal_gate_contract.py` + `goal_model_gate.py` + `goal_write_review.py`；数据库写入仍由 Java `GoalService` 校验，不能只凭 4B 的 yes 写入。
- 原 Laya：`train_decisions.py` / `unrounded_laya.py`。这是回退与旧权重复现入口，不是新工具主线。

## 3. 数据在哪里，哪些可以直接用

### ToolACE 主线

来源和固定版本见 [`public_tools/fetch.py`](../agent-runtime/training/public_tools/fetch.py) 的 `SOURCES`。ToolACE 原始 11,300 段对话；排除解析问题与精确重复后构建 13,297 个决策点，完整预检采用 train 10,556 / validation 1,239 / test 1,204 个预算合格、有区分意义的状态。

APIGen-MT 被独立下载、转换，不进入默认 ToolACE 训练。来源声明与许可说明见[公开数据 README](../agent-runtime/training/public_tools/README.md)。公开数据含合成内容，不等于独立人工标注。

监督只使用示范中实际出现的下一动作：工具调用或 `assistant_text`。目标回复、目标调用参数和目标索引不进入输入。未选工具没有绝对 no 标签；普通文字不补造成 finish/clarify 标签。相同规范化工具名、首问和完整对话 hash 按连通组件隔离，不宣称全部语义家族隔离。

### 已冻结课程与专项数据

- `training/artifacts/20261014/datasets/`：Router、基础 Memory、旧目标 Executor 与 Laya 回退的冻结分区。`challenges/` 是独立挑战；`manifest.json` 保存字节校验与来源限制。复现已有权重优先用冻结课程，不重新生成不同标签的数据替代。
- 生命周期：v1 以冻结 Memory 课程为 replay，v2 在 v1 课程上追加；v1 生成器是 v2 真依赖，不能删。构建参数及初始化见 [v1 README](../agent-runtime/training/experiments/memory-lifecycle-20261015-v1/README.md) / [v2 README](../agent-runtime/training/experiments/memory-lifecycle-20261016-v2/README.md)。
- 目标门控：`goal_gate_data.py` 生成专项开发课程；冻结挑战在 `training/experiments/goal-gate-20261017-v1/challenge/cases.jsonl`。标签为程序/开发标签，报告中保留边界说明。
- 旧课程及专项挑战不能称为独立用户金标；读取真实失败记录也不会自动把真实聊天转成训练集。本次文档整理没有调用 DeepSeek 或生产新数据。

## 4. 环境与恢复已有权重

以下命令在 `agent-runtime` 目录执行。普通 Runtime 与 CUDA 模型环境分开，GPU 依赖见 [`requirements-models.txt`](../agent-runtime/training/requirements-models.txt)。先核对驱动、PyTorch/CUDA 兼容与可用显存，不因历史显存数字推断当前机器一定能跑。

既有 lab 数据盘根目录：

```bash
export ATTUNE_DATA_ROOT=/home/ubuntu/data/psych-agent-routing-20261009
export ATTUNE_RELEASES="$ATTUNE_DATA_ROOT/artifacts"
export HF_HOME="$ATTUNE_DATA_ROOT/cache/huggingface"
export XDG_CACHE_HOME="$ATTUNE_DATA_ROOT/cache/xdg"
export TMPDIR="$ATTUNE_DATA_ROOT/tmp"
mkdir -p "$ATTUNE_RELEASES" "$HF_HOME" "$XDG_CACHE_HOME" "$TMPDIR"
```

换机器时先把 `ATTUNE_DATA_ROOT` 改为实际 data 盘挂载路径。所有下载、缓存、临时文件、模型环境、训练源码副本和输出均留在数据盘。下列拷贝步骤适用于尚无对应目录的新环境；已有目录先校验，不覆盖修改过的产物。

```bash
cp -a training/artifacts/20261014 "$ATTUNE_RELEASES/"
cp -a training/artifacts/20261020 "$ATTUNE_RELEASES/"

python -m training.fetch_artifacts --root "$ATTUNE_RELEASES/20261014" --verify-data
python -m training.fetch_artifacts --root "$ATTUNE_RELEASES/20261020" --verify-data

python -m training.fetch_artifacts --root "$ATTUNE_RELEASES/20261014"
python -m training.fetch_artifacts --root "$ATTUNE_RELEASES/20261020"

python -m training.fetch_artifacts --root "$ATTUNE_RELEASES/20261014" --verify-only
python -m training.fetch_artifacts --root "$ATTUNE_RELEASES/20261020" --verify-only
```

两个 Release 共同组成权重集合：[models-20261014](https://github.com/Vaniloo/psych-agent-engineering/releases/tag/models-20261014) 和 [freeze-20261020-attune](https://github.com/Vaniloo/psych-agent-engineering/releases/tag/freeze-20261020-attune)。仓库私有，下载器优先复用有仓库权限的 `gh` 登录；不要把 token 写进命令或文档。

`--verify-data` 跳过 `.safetensors`，检查随仓库保留的数据和元信息；它不代表权重就绪。`--verify-only` 包括权重。Release 包包含 adapter、配置、训练报告及许可说明，不含 Qwen 基座、训练优化器和全部中间作业文件。

下载并核对固定 Qwen 基座：

```bash
python -m training.prepare_qwen_base --root "$ATTUNE_RELEASES/20261014"
export ATTUNE_BASE="$ATTUNE_RELEASES/20261014/base/22e683669bc0f0bd69640a1354a6d0aebcfeede5"
export ATTUNE_BASE_REPORT="$ATTUNE_RELEASES/20261014/base-report.json"
```

这会从作者仓库下载固定 revision 并验证训练报告列出的权重 SHA。已有基座时用 `--existing <已有快照目录>` 校验并生成报告；目录名须为固定 revision，随后将 `ATTUNE_BASE` 指向该目录。0.6B 不使用这份 4B 基座，另按重排服务配置恢复。

## 5. ToolACE 从数据到训练的完整操作路径

这是准备重训时的操作说明，本次交接不执行。新作业的源码副本应位于 data 盘，并固定 Git 提交、依赖和源码 fingerprint；预检与训练之间不修改源码。每个输出子目录必须不存在。以下 `ATTUNE_RUN` 是新作业示例，不指向历史正式目录：

```bash
export ATTUNE_RUN="$ATTUNE_DATA_ROOT/runs/toolace-reproduction-20261021-v1"
```

### A. 下载、转换、核验和构建课程

```bash
python -m training.public_tools.fetch --out "$ATTUNE_RUN/raw"
python -m training.public_tools.fetch --out "$ATTUNE_RUN/raw" --verify-only
python -m training.public_tools.convert --raw "$ATTUNE_RUN/raw" --out "$ATTUNE_RUN/interchange"
python -m training.public_tools.verify_interchange --raw "$ATTUNE_RUN/raw" --converted "$ATTUNE_RUN/interchange"
python -m training.public_tools.course --raw "$ATTUNE_RUN/raw" \
  --converted "$ATTUNE_RUN/interchange" --source toolace --out "$ATTUNE_RUN/course"
```

若已有经核验的原始下载或课程，复用其实际路径并重新核对清单，不重复下载或覆盖。`fetch` 会获取两个注册数据源，`course --source toolace` 明确选主线。

### B. 全量预检

```bash
python -m training.public_tools.train --preflight-only \
  --data "$ATTUNE_RUN/course" --base "$ATTUNE_BASE" \
  --out "$ATTUNE_RUN/preflight" --max-train-rows 0 --eval-rows 0
```

两个 `0` 表示选择全部预算合格状态。默认值是 train 2,048、评测各 256；漏掉它们会跑成抽样试训。单候选默认最多 4,096 tokens，同状态 padding 后候选批量最多 16,384 tokens；不静默截断或丢掉部分候选。

### C. 单轮训练与评测

在已选定的空闲 GPU 上执行；`CUDA_VISIBLE_DEVICES` 由操作者显式选择，不在示例中固定某张共享卡。

```bash
python -m training.public_tools.train \
  --data "$ATTUNE_RUN/course" --base "$ATTUNE_BASE" \
  --base-report "$ATTUNE_BASE_REPORT" --preflight "$ATTUNE_RUN/preflight" \
  --out "$ATTUNE_RUN/candidate" --epochs 1 \
  --checkpoint-every-steps 128 --max-wall-seconds 27000 \
  --evaluation-reserve-seconds 3600
```

时间预算属于这次训练程序，不能保证下载、课程构建、预检、重载核对也全部在该时限内。若要求整条作业 7–8 小时，还须为外层流程设整体期限；不能仅凭这里的参数声称总时长受控。历史全流程约 5 小时 47 分，不是新机器时长承诺。

重点查看 `candidate/progress.json`、`training-report.json`、`base_test.json`、`validation.json`、`test.json`、`adapter/`、`checkpoints/`；失败还需检查 `failure.json`、退出状态和日志。`status`、实际训练状态数、`fullTrainingCompleted`、`budgetStopped`、评测实际覆盖数要共同核对。

### D. 磁盘重载核对

```bash
python -m training.public_tools.verify_candidate \
  --data "$ATTUNE_RUN/course" --preflight "$ATTUNE_RUN/preflight" \
  --candidate "$ATTUNE_RUN/candidate" --out "$ATTUNE_RUN/reload-verification.json"
```

这一步确认保存后的 adapter 能重现既有验证分数和选择，不等于证明项目效果足够好。继续通过独立模型服务和真实应用链路回归，再决定接入。

## 6. 其他模型怎样继续训练

复现 Router 和基础 Memory 时直接使用恢复好的冻结课程和挑战。两个训练器的 `--challenge` 均接受 **`cases.jsonl` 文件**，不是目录。课程构建器、HTTP 评测器的同名参数可能含义不同，逐个看 `--help`。

```bash
python -m training.train_qwen_choices --profile router \
  --base "$ATTUNE_BASE" --base-report "$ATTUNE_BASE_REPORT" \
  --data "$ATTUNE_RELEASES/20261014/datasets/qwen-router" \
  --challenge "$ATTUNE_RELEASES/20261014/challenges/qwen-choices/cases.jsonl" \
  --out "$ATTUNE_DATA_ROOT/runs/router-reproduction-20261021-v1"

python -m training.train_qwen_memory --contract psych-memory-v4 \
  --base "$ATTUNE_BASE" --base-report "$ATTUNE_BASE_REPORT" \
  --data "$ATTUNE_RELEASES/20261014/datasets/qwen-memory" \
  --challenge "$ATTUNE_RELEASES/20261014/challenges/qwen-memory/cases.jsonl" \
  --out "$ATTUNE_DATA_ROOT/runs/memory-reproduction-20261021-v1"
```

生命周期构建依赖为：

```text
恢复的 datasets/qwen-memory + challenges/qwen-memory/cases.jsonl
    ↓ memory_lifecycle_data --replay <课程目录> --old-challenge <文件> --out <v1目录>
v1 课程及 challenge/
    ↓ memory_lifecycle_refinement --parent <v1目录> --out <v2目录>
v2 课程及 challenge/
    ↓ train_qwen_memory --contract psych-memory-lifecycle-v1
```

v2 的 `--init-adapter` 指向含 `training-report.json` 和 `adapter/` 的基础 Memory 模型目录；使用报告对应的 `--learning-rate 0.00002`。目标门控同样通过 `train_qwen_memory` 训练，但选择 `psych-goal-gate-v1`，初始化改为旧目标 Executor，学习率为 `0.0001`，不能串用生命周期适配器。完整专项参数见对应实验 README 和冻结训练报告。

训练、校准、validation、test 和独立挑战必须分清。某些专项挑战虽然先于当轮评分冻结，其设计参考了上一轮失败，属于开发回归，不能称为独立泛化金标。`train_qwen_memory` 是复用训练流程的文件名，不代表选择任何 contract 都在训练“记忆”。

## 7. 从权重到应用：独立服务、再接入

训练完成不自动部署。先启动候选 worker，检查 `health.ready`、contract、基座 revision、adapter SHA 和任务配置，再运行应用回归。启动示例要求已私下配置对应服务密钥，不将其写入命令：

```bash
python -m psych_agent_runtime.public_tool_service \
  --base "$ATTUNE_BASE" \
  --checkpoint "$ATTUNE_RELEASES/20261020/models/toolace-qwen4b-20261019" \
  --port 18121
```

`--checkpoint` 指含报告的模型目录，不是单独的 `adapter/`。ToolACE 服务使用环境变量 `PUBLIC_TOOL_API_KEY`；Router / 旧目标用 `qwen_choice_service`，基础 Memory / 生命周期 / 目标门控用 `qwen_memory_service` 并从各自报告选择契约。服务密钥、端口与进程相互独立，候选端口先确认未占用。

Runtime 的主要接入项：

| 模块 | 核对配置 | 边界 |
| --- | --- | --- |
| ToolACE | `EXECUTOR_PROVIDER=public_qwen`、`EXECUTOR_BASE_URL`、`EXECUTOR_API_KEY`、`EXECUTOR_ADAPTER_SHA256` | 动态授权读取 + TEXT；分数不是已校准适用概率 |
| Laya 回退 | `EXECUTOR_FALLBACK_BASE_URL` / `EXECUTOR_FALLBACK_API_KEY` | 仅支持其已有契约的候选，不自动学会新 MCP 工具 |
| 旧目标判断 | 独立 `EXECUTOR_GOAL_PROVIDER` / `EXECUTOR_GOAL_*` | 不能继承 ToolACE 读取模型来判断目标写入 |
| 基础 Memory | `MEMORY_PROVIDER`、`MEMORY_BASE_URL`、`MEMORY_CONTRACT_VERSION` | 与生命周期服务分开 |
| 生命周期 | `MEMORY_LIFECYCLE_SHADOW_*`、READ / ACTIVE 等账号范围、`MEMORY_LIFECYCLE_EXPECTED_SHA` | 预览、读取和事实写入的开关不同；默认不全量开放 |
| 目标门控 | `GOAL_GATE_MODE` / `GOAL_GATE_USERS` / `GOAL_GATE_BASE_URL` / `GOAL_GATE_EXPECTED_SHA` | 保留生成模型核对、后端归属及保存凭据校验 |
| 重排 | `MEMORY_RERANK_*` 与 `RAG_RERANK_*` | 原版 0.6B，任务指令和回退机制独立 |

完整配置见 [`.env.example`](../.env.example) 与 [`config.py`](../agent-runtime/psych_agent_runtime/config.py)。默认配置偏向 rules / 关闭专项功能，不会因下载权重就自动启用所有任务。接入历史见 [ToolACE 服务记录](public-tool-serving-20261019.md)；其中 renderer 路径及旧测试数量以冻结后的共享契约和冻结说明为准。

## 8. 效果与故障验收

### 已记录的 ToolACE 结果

完整单轮：10,556 状态、1,320 次更新，完整 validation / test 为 1,239 / 1,204。test 示范一致率由 80.65% 提升到 93.36%；文字分支仍弱于工具调用分支。16 条既有 validation 样本重载选择一致。详见[训练报告解读](public-tool-next-action-20261019.md)末尾完整单轮结果及[项目回放](public-tool-project-replay-20261019.md)。

这不是端到端任务成功率。当前工具分数未做项目概率校准，TEXT 表示交给回复生成器，不表示目标完成；同样，Memory 分类不是事实的真实性认证，目标门控也不能直接代替业务授权。

### 交付新模型至少保留

1. 数据来源 / 版本 / 许可证声明、manifest 和分区 hash；明确标签是原始示范、程序标签还是审核标签。
2. 基座 revision / 权重 SHA、初始化 adapter SHA、Git 提交或源码快照、renderer 指纹、依赖及硬件记录。
3. 实际训练数量、超预算排除数、训练参数、进度、退出状态和完整评测覆盖数。
4. 基座与候选在同一冻结评测上的比较；分别列误调用、漏调用、TEXT、弃权及错误接受，不能只报均值。
5. adapter 重载一致性、候选 HTTP 健康/契约/权重对齐、应用回归、回退与回滚配置。
6. 明确晋级决定与启用账号范围。旧报告、清单与标签保持不变，新实验写新目录。

建议优先复查动态新工具、长句夹杂背景信息、无合适工具、候选顺序变化、本人事实召回、目标完成 + 感受反馈、撤回/否认/未来计划及服务不可用。已有失败结论不能因公开 test 提升被覆盖。

### 现有测试入口

在开发环境、`agent-runtime` 目录运行：

```bash
python -m pytest -q \
  tests/test_public_tools.py tests/test_public_tool_course.py \
  tests/test_public_tool_serving.py tests/test_public_tool_replay.py \
  tests/test_artifact_publication.py tests/test_qwen_choices.py \
  tests/test_qwen_memory_finetune.py tests/test_memory_lifecycle.py \
  tests/test_goal_model_gate.py
```

这是代码和契约回归，不启动完整 4B 训练，也不能替代真实 GPU / Java / 数据库 / 网页的端到端验收。冻结完整测试数量见[冻结说明](freeze-20261020.md)，不在新交接文档中伪造新一次全栈结果。

## 9. 服务器历史产物与恢复限制

下列是已记录的 ToolACE 完整单轮路径，本次没有重新核实服务器存在性：

```text
/home/ubuntu/data/psych-agent-routing-20261009/
  datasets/public-toolace-next-action-20261019-v1/
  runs/public-toolace-qwen4b-20261019-full-v1/
    source/
    preflight/
    candidate/training-report.json
    candidate/adapter/
    candidate/checkpoints/step-001320-final/
    reload-verification.json
    aggregate-results.json
    result-audit.json
```

普通 Git 包含源码、课程 / 挑战和报告元信息；Release 存放 adapter 大文件；原始公开 ToolACE 数据按固定来源重建；服务器作业目录另保留源码快照、优化器检查点和逐阶段日志。三者不能互相替代。

`--resume-from` 只用于相同课程、预检、源码、基座及超参数的兼容检查点，并写新输出目录。清理后的源码能继续训练同契约新模型，不保证可直接续跑旧优化器；真正续跑应使用对应训练快照。Release 只有 adapter 时，可以恢复推理或按明确方案初始化新训练，不能冒称恢复了原优化器进度。

## 10. 当前交接的下一步

学习从 `course.py` 开始，先手工追踪一段多轮对话如何得到决策状态，再读共享 renderer、LoRA 和 loss，最后读服务客户端。完整 15 天计划见[源码学习路线](source-learning-roadmap.md)。本轮整理没有训练、替换、发布或运行容量测试。

后续优化优先围绕真实漏调用、记忆证据覆盖、动态新工具及搜索延迟补评测；不因一次开发测试不一致就无条件追加训练。旧 toolgen v1/v2、utility 原型与 0.6B 事实性失败试训已归档，历史报告用于理解失败，不作为当前可运行入口。
