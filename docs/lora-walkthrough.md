# ToolACE LoRA 微调 · 从数据到上线

这份文档对照 [`agent-runtime/training/lora/`](../agent-runtime/training/lora) 的四个文件，讲清楚这个模型做什么、数据怎么来、怎么训、效果怎么解读。每一节末尾标了对应代码。

## 1. 一句话

把 Qwen3-Reranker-4B 微调成一个“下一步动作打分器”：给它一段对话和一组候选（若干工具 + “现在直接回复”），它给每个候选打分，分数最高的就是它认为下一步该做的事。在 Attune 里，它每一步先给工具打分，只把分数高于“直接回复”的工具交给主模型。

## 2. 为什么选 Reranker 当基座

Qwen3-Reranker 本来就是“给一对（Query, Document）判断相关与否”的模型：输入一段固定格式的提示，看最后一个位置上 `yes` 和 `no` 两个 token 的 logit。把 `logit(yes) - logit(no)` 当分数，天然适合给候选排序。

- 不需要新加分类头：直接复用原来的语言模型头，只看两个 token。
- 每个候选单独打分：候选数量可以变（训练时一段对话可能有 2 个工具，也可能有 10 个），不需要固定类别数。
- 4B 的规模，单卡 bf16 推理只要十几 GB 显存，能做成一个独立的评分服务。

## 3. 一条数据走完全程

**原始记录（ToolACE）**：system 提示里有一段 JSON 工具列表，对话是用户、助手、工具返回交替。助手的工具调用不是 JSON，是 `[Get Weather(city='Paris')]` 这种类似 Python 的写法。

```
user:      Weather in Paris?
assistant: [Get Weather(city='Paris')]
tool:      [{"temp": 21}]
assistant: It is 21°C in Paris.
```

**第 2 步 · 转换**：解析出工具列表和每个调用块，得到统一的事件序列。

```
user → tool_call(Get Weather, {city: Paris}) → observation → assistant
```

调用块用一个手写的严格解析器读：工具名可能含空格、撇号、斜杠，所以拿已知工具名做前缀匹配；参数值只用 `ast.literal_eval` 读字面量，不执行任何代码。解析失败的记录整条丢掉——如果只丢那一句，那一步就会被误当成“文字回复”，变成错误标签。 → [`step2_convert.py`](../agent-runtime/training/lora/step2_convert.py) 的 `parse_calls`

**第 3 步 · 生成样本**：助手每次行动生成一条样本，输入是行动之前的全部事件。

| 样本 | 输入（context） | 候选 | 标签 |
| --- | --- | --- | --- |
| 1 | [user] | Get Weather, TEXT | Get Weather |
| 2 | [user, tool_call, observation] | Get Weather, TEXT | TEXT |

要点：要预测的那一步本身（包括它的参数）绝不能出现在输入里；同一步并行调用了多个工具时，每个都算正确答案。 → [`step3_samples.py`](../agent-runtime/training/lora/step3_samples.py) 的 `samples`

**渲染成提示**：每个候选渲染一段，N 个候选就是 N 段，只有最后的 Document 不同。

```
<|im_start|>system
Judge whether the Document meets the requirements based on the Query and the Instruct provided.
Note that the answer can only be "yes" or "no".<|im_end|>
<|im_start|>user
<Instruct>: Given the conversation so far and the currently available tools, judge whether the
            candidate matches the demonstrated next assistant action. ...
<Query>: {"conversation": [...], "availableActions": [所有候选]}
<Document>: {某一个候选}<|im_end|>
<|im_start|>assistant
<think>

</think>

```

这个渲染函数在运行时包里（`public_tool_contract.render`），训练和线上评分服务调用的是同一个函数，所以输入格式完全一致。 → [`common.py`](../agent-runtime/training/lora/common.py)

## 4. 防泄漏的切分

如果两段对话用了同一个工具，一段在训练集、一段在测试集，模型就可能“背下”这个工具什么时候该用，测试分数虚高。所以切分前先用并查集把对话连成组：**共享任何一个工具名，或首句提问相同，就是同一组**，整组只进一个集合。再按组的大小从大到小，依次放进离 80/10/10 目标比例差得最多的集合。 → `step3_samples.split_by_group`

注意这是“名字完全相同”的隔离，不是语义隔离：名字不同但功能相近的工具（`get_weather` 和 `weather_lookup`）仍可能分在两边。

## 5. 损失函数

一条样本的 N 个分数一起做 softmax，最大化示范动作的概率：

```python
loss = -log_softmax(scores)[targets].mean()
```

- **为什么是 listwise，而不是每个候选单独做二分类（BCE）？** 数据只告诉我们“示范里选了哪个”，没告诉我们“其他工具有没有用”。没被选中的工具可能也合理，只是这次没选。BCE 会把它们都当成负样本，listwise 只要求“示范动作比其他候选分数高”，更符合数据实际能提供的信息。
- **多个正确答案**：`targets` 有多个时取平均，每个被调用的工具平分监督信号。

→ `common.listwise_loss`

## 6. LoRA 和训练设置

| 设置 | 值 | 原因 |
| --- | --- | --- |
| 秩 r / alpha | 16 / 32 | 常用默认值，alpha/r = 2；这次没有做秩的对比实验 |
| 目标层 | q/k/v/o_proj + gate/up/down_proj | 覆盖注意力和 MLP 的全部线性层，是 LoRA 微调的常见做法 |
| 可训练参数 | 3,303 万 | 约占 4B 基座的 0.8%，基座权重冻结 |
| 精度 | bf16 | 4B 模型全精度放不进单卡训练 |
| 梯度检查点 | 开 | 一条样本 N 个候选一起前向，显存主要花在激活值上 |
| 梯度累积 | 8 | 一次只能放一条样本，累积 8 条再更新，等效 batch=8 |
| 学习率 | 2e-5，前 10 步预热，之后线性衰减到 10% | LoRA 常用量级；预热避免一开始梯度太大 |
| 梯度裁剪 | 1.0 | 防止个别长样本梯度爆炸 |
| 超长样本 | 超过 4096 token 直接排除 | 截断会切掉工具定义或历史，标签就不再可信 |

发布的那次训练：10,556 条训练样本、1 个 epoch、1,320 次参数更新，用时约 5.5 小时，峰值显存约 16.3 GB。 → [`step4_train.py`](../agent-runtime/training/lora/step4_train.py)

## 7. 效果怎么讲

测试集 1,204 条，指标是 Top-1 一致率（分数最高的候选是不是示范里的下一步）：

| | 基座 | LoRA |
| --- | --- | --- |
| 全部 | 80.65% | **93.36%** |
| 示范是调用工具（960 条） | 95.1% | 97.1% |
| 示范是直接回复（244 条） | 23.8% | **78.7%** |

**真正的提升在“直接回复”那一行。** 基座本来就很会挑工具，它的问题是太爱调工具：该直接回答的时候，四次里有三次还在调工具。微调主要教会了它“信息够了就停”。这也正是它在 Attune 里能当预筛的原因：只有分数高过“直接回复”的工具才交给主模型。

需要主动说明的前提：
- 衡量的是“和示范一致”，不是“工具真的有用”，也不是端到端任务成功率。
- 测试集只含 4096 token 以内的样本，长对话被排除了。
- 测试集里 80% 是工具调用，类别不均衡，所以要拆开看两类。

## 8. 上线后怎么用

线上的评分服务（[`public_tool_service.py`](../agent-runtime/psych_agent_runtime/public_tool_service.py)）启动时校验基座和 adapter 的哈希、渲染函数是否和训练时一致，对不上就拒绝启动。Agent 每一步调用它给可用工具打分（[`prefilter.py`](../agent-runtime/psych_agent_runtime/prefilter.py)），分数高于“直接回复”的工具交给主模型；服务不可用时退回全部工具，所以它只是优化，不会成为单点故障。

## 9. 跑一遍

在有 GPU 的机器的 `agent-runtime/` 目录：

```sh
uv sync --extra executor
python -m training.lora.step1_download --out work/raw
python -m training.lora.step2_convert --raw work/raw/data.json --out work/episodes.jsonl
python -m training.lora.step3_samples --episodes work/episodes.jsonl --out work/samples
# 先用少量数据把流程跑通，再去掉 --limit 跑全量
python -m training.lora.step4_train --data work/samples --base <Qwen3-Reranker-4B 快照目录> \
    --out work/run --limit 64
```

基座快照可以用 `python -m training.prepare_qwen_base` 下载并校验；快照目录名必须是 revision（`22e683…`），评分服务按这个名字核对。直接使用已发布的 adapter 见[根 README](../README.md#启用-toolace-预筛需要-gpu)。

## 10. 面试常见追问

- **为什么不直接让大模型选工具，还要微调一个小模型？** 主模型确实能选，这里的小模型是预筛：缩短主模型看到的工具列表，降低乱调工具的概率；它可以独立部署、延迟低，挂了也不影响主流程。
- **为什么用 reranker 而不是生成式模型输出工具名？** 打分模型对候选数量不敏感，不需要生成和解析文本，输出是连续分数，可以设阈值（这里的阈值就是“直接回复”的分数）。
- **数据里没有“该不该停”的标签，模型怎么学会停？** 示范里助手说一段话的那一步，标签就是 TEXT。不额外推断“完成”或“澄清”这类语义标签，只用示范实际发生的动作。
- **怎么保证测试集没泄漏？** 并查集按工具名和首句提问分组，整组进一个集合；目标动作不进输入；完全重复的对话去重。说明局限：只隔离了完全相同的名字。
- **为什么超长样本要排除，而不是截断？** 截断会切掉工具定义或关键历史，模型看不到做决定所需的信息，标签却还是原来的，等于训练噪声。
- **LoRA 的 r 和 alpha 怎么定的？** 用了常见默认值（r=16、alpha=32）。这次没有做超参数搜索，验证集只在训练后评测了一次，这是可以继续做的地方。
- **只训了 1 个 epoch 够吗？** 验证集 95.0%、测试集 93.4%，差距不大，说明没有明显过拟合；多训几轮可能有提升，但没有实验数据支撑，不要编。
- **多个工具同时正确时怎么算 loss？** 对每个正确答案的 log 概率取平均。
- **训练和线上的输入怎么保证一致？** 两边调用同一个渲染函数；评分服务启动时还会比对训练报告里记录的渲染函数哈希。

## 11. 已知局限

- 发布的 adapter 是用精简前的代码训练的（tag [`archive/pre-resume-scope`](https://github.com/Vaniloo/psych-agent-engineering/tree/archive/pre-resume-scope) 里的 `training/public_tools/`）。精简版去掉了哈希清单、断点续训和磁盘重载校验，数据处理和训练逻辑等价，但不保证逐字节复现同一个 adapter。
- 线上拼历史工具调用时用的是 JSON 写法，训练数据里是 ToolACE 原文写法，第二步之后存在格式差异。
- 训练目标是“在候选里选出示范动作”，线上用法是“每个工具和直接回复比分数”，两者方向一致，但后者还没有单独评测。
