# Weight-Space Programming：记忆 LoRA 实验

日期：2026-11-04。研究性实验，独立 data 盘目录，不替换当前模型，不开放新的记忆写入权限。代码为 [`training/weight_space.py`](../agent-runtime/training/weight_space.py)，数学与源权重保护测试为 [`test_weight_space.py`](../agent-runtime/tests/test_weight_space.py)。

## 方法来源与本实验定位

[Sparks of Weight-Space Programming](https://rikiyat.github.io/blog/weight-space-programming/)（2026-11-02）研究让通用 LLM 读取、分析并直接修改网络权重，不通过梯度优化器更新；部分实验允许有限前向反馈。作者同时报告了局部故障修复成功与能力提升失败的情形；[官方实验日志](https://github.com/RikiyaT/lm_understand-logs)可供核对。这是早期研究结果，不能推导出任意模型都能无训练升级。

本实验由本会话助手根据真实权重检查结果编写一个受限补丁程序，使用前向评测检验；不是原文 Qwen3.5 数学实验的复现，也不声称从参数数值直接识别了某个事实判断电路。普通 LoRA 缩放和随机修改只是对照，不把它们单独包装成新的 WSP 能力。

## 为什么选择记忆生命周期 v2

- 基础模型为 Qwen3-Reranker-4B，生命周期 adapter 为 `qwen-memory-lifecycle-4b-lora-20261016-v2`。
- 有冻结课程、真实初始化父 adapter、12 个任务的明确判断契约以及历史边界错误，便于检验局部修复能否保留已有能力。
- 只评测决策，不涉及真实账号、数据库写入、在线生成或工具执行。
- 目标是减少错误判断，特别是生命周期任务的错误肯定，同时保留 relevance 等旧任务；不是提升聊天语气或生成能力。

来源 adapter SHA：

```text
生命周期 v2：09a35bde69776a6b7da22392062f89aca3f851b543870be4bb31f95b3f4bdc18
基础 Memory：f679f7d5e2c22b7c32c4f18535e149d2ce9f73438412407e5634a861872b9467
```

## 实验设计

LoRA 的有效更新为 `ΔW = (alpha/r) BA`。直接比较 A、B 的范数容易受等价基变换影响，因此比较生命周期与父适配器的 `BA - B0A0`。两份适配器均为 r16、alpha32，公共缩放为 2，不影响层排序或奇异能量占比；检查文件中的平方范数是未乘这个公共缩放的 BA 范数。

检查发现变化范数最大的层是 23、25；单个变化最大的模块为第 1 层 `up_proj`。这些只是数值定位线索，不证明它们导致了语义错误。

在首次前向前冻结程序列表、数据 SHA、验证样本选择、源码 SHA 和选型规则：

| 程序 | 修改 | 作用 |
| --- | --- | --- |
| original | 无 | 同批量原模型基线 |
| global_scale_080 | 所有 B 乘 0.8 | 检查是否只是整体适配强度变化 |
| rollback_top1 / top2 | 将指定层 A/B 恢复为父适配器 | 检验局部回退 |
| project_top_module_050 / 100 | 对最大变化模块的 B 削弱主输出方向 50% / 100% | 检验局部方向消融 |
| random_direction_050 / 100 | 同模块随机方向，匹配有效 BA 扰动范数 | 排除任意同规模扰动解释 |
| random_layer / random_layers2 | 回退预先指定的第 12 层或第 10/12 层 | 层数量匹配对照；名称沿用脚本 ID，层并非随机抽取，不宣称完整范数匹配 |

主方向通过低秩 QR + 小矩阵 SVD 计算，不展开整块 dense 权重，不计算梯度。投影只修改 LoRA 的 B，保留原 rank 和原 alpha；原 A/B 和基座文件保持不变。

验证：从冻结 validation 每个 task/label 按输入 hash 最多取 16 条，实际 375 条（部分标签不足 16 条）。只用这部分挑选候选。要求宏平均正确率和 relevance 不低于基线、生命周期 false-positive 不多于基线，并且总正确数严格增加；若没有满足条件者，只导出最佳诊断补丁，不晋级。

在保存 `selection.json` 后才打开完整 test（3,644 条），评测原模型、选中的诊断/候选、整体缩放及对应随机对照，不根据 test 再改补丁。原有阈值不因本实验自动有效；本轮报告原始 yes/no 决策，不冒充已校准上线结果。数据仍是共享模板的开发课程，不是独立人工金标。

## 运行和产物

服务器：`/home/ubuntu/data/psych-agent-routing-20261009/runs/weight-space-20261104-v1/`。原始源码快照在 `source/`、开发结果在 `results/`；修复输出格式后的源码和完整评测分别在 `source-evaluation-v2/`、`results-evaluation-v2/`。使用现有 CUDA 环境和空闲 GPU，不停止其他任务，不调用教师 API。

运行入口（路径替换为独立 data 盘目录；候选输出目录必须不存在）：

```bash
python -m training.weight_space \
  --base /data/base/22e683669bc0f0bd69640a1354a6d0aebcfeede5 \
  --current /data/models/qwen-memory-lifecycle-4b-lora-20261016-v2 \
  --parent /data/models/qwen-memory-4b-lora-20261012 \
  --data /data/datasets/memory-lifecycle-20261016-v2/course \
  --out /data/runs/weight-space-new/results --per-cell 16 --batch-size 16
```

需要与现有 Qwen CUDA 环境一致的 Torch、Transformers、PEFT、safetensors、NumPy；单次前向最多 2,048 tokens，不截断。操作者显式选择有余量的 GPU；程序要求至少 24 GiB 空闲，并限制自身显存比例。结果文件保留每阶段指标和前向批次数；批次数不是独立实验次数或原文的 forward-pass 预算单位。

主要文件：`protocol.json`、`weight-inspection.json`、逐程序 `*-dev.json`、`selection.json`、四组 `*-test.json`、`candidate-adapter/`、`results.json`。候选单独保存并核对重载，不改训练报告，不复制原部署凭据，也不提供可直接晋升服务的伪造训练报告。

首次运行完成开发比较并冻结选择后，在写出首组 test 结果时因课程不含 `id` 字段发生 `KeyError`。修复为按 task/state 生成稳定标识，并补充对应测试；使用 `--selection-from <原 selection.json>` 只恢复测试，不重复开发筛选。恢复前核对程序列表、数据/权重/指令 hash、开发样本选择和选型规则，保存原 selection 和 protocol 的 SHA；两次源码与失败记录均保留。没有根据测试成绩改变补丁。

## 结果

**完成，保留原模型，不晋级。** [可版本管理的结果摘要](../agent-runtime/training/experiments/weight-space-20261104-v1/summary.json)保存逐任务指标、权重 SHA 和配对变化；[实验目录](../agent-runtime/training/experiments/weight-space-20261104-v1/README.md)保存协议入口。

下表为与冻结标签一致的数量，既不是用户任务成功率，也不是临床指标：

| 版本 | validation 子集 / 375 | 完整 course test / 3,644 | 边界挑战 / 491 |
| --- | ---: | ---: | ---: |
| 原始生命周期 v2 | 373 | 3,636 | 471 |
| 定向主方向削弱 50% | 373 | 3,636 | 470 |
| 所有 LoRA B 缩放到 0.8 | 373 | 3,637 | 468 |
| 同模块、等扰动范数的随机方向 | 374 | 3,636 | 471 |

所有定向程序都未提高 validation 正确数，故 `passedDevGate=false`。按冻结规则仅将 `project_top_module_050` 导出为诊断补丁，不将随机对照的偶然改善重新解释成定向方法成功。

补丁实际修改 1 个 B 张量、155,648 个 LoRA 参数（适配器参数总数 33,030,144，约 0.471%）。course test 的分数最大变化为 1.8125，但 3,644 条 yes/no 选择全部与原模型一致；不是没有应用补丁。边界挑战没有修复条目，新增 1 条 episodic 漏判；relevance 仍为 112/116，生命周期五任务的 raw false-positive 仍为 5。

整体缩放在 course test 修复 1 条，在边界挑战修复 2 条、破坏 5 条，净退步 3 条。随机方向在挑战修复 1 条、破坏 1 条，总正确数相同；不能只看均值就认为行为完全不变。

### 保存与核对

- 原始两个 adapter SHA 未变；所有参数冻结，无反向传播、优化器或新标签生成。
- course 和 challenge 两次导出的诊断 adapter SHA 一致：`bedec2eadb7e3bccf76dd5b82312c686e0b7357c3501d31b5934fd2eb1c072be`；文件 132,187,888 字节。
- 两次 CPU tensor 重载核对通过，8 条前向重载分数最大差为 0。完整逐例指标已在本机独立重算；选型文件与最初冻结版本一致。
- 恢复后的完整课程评测约 208.8 秒、边界补充约 36.0 秒，不含最初开发比较与失败运行的额外时间。课程评测峰值分配约 8,368.9 MiB；不是在线延迟或并发能力。
- 本地数学/格式测试 4 项通过。实验程序自然退出，没有修改已有服务、账号数据、正式权重或冻结标签。

完整原始结果与诊断 adapter 位于本机会话 `outputs/weight-space-20261104/`；服务器完整课程结果为 `results-evaluation-v2/`，补充挑战结果为 `results-challenge-v3/`。新增源码快照 `source-challenge-v3/` 只增加冻结挑战的附加评测入口，不改变补丁或选型规则。`--challenge-file <已冻结 cases.jsonl>` 必须配合 `--selection-from`，并核对训练报告中的 challenge SHA；这批 491 条仅作开发边界回归，不是新的独立盲测。

### 本轮能支持的结论

可以把它保留为离线 LoRA 诊断/补丁实验入口：分析有效更新、编写有限补丁、设对照、冻结选择、评测、核对保存。**本轮没有证明这些权重范数/SVD 启发式能修复项目记忆错误。** 大变化方向不等于错误行为方向；课程原本接近满分，且部分 episodic/procedural 标签存在解释边界，程序化数据也不足以证明真实用户收益。

如继续研究，优先冻结真实失败家族并复核标签，再对错误与正确例做激活/分数路径定位，检验相关方向的因果作用；不直接扩大随机消融搜索，也不拿本轮 test 反复选参数。现有补丁不接入应用、不复用旧校准策略。

同日后续已转为[只读激活诊断](activation-memory-diagnostics-20261104.md)：先测试表达敏感性与临时激活干预，再检查部分路径；原始模型权重继续保持不变。
