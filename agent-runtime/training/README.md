# ToolACE LoRA 微调

把 Qwen3-Reranker-4B 微调成“下一步动作打分器”：给一段对话和若干候选（工具 + “直接回复”），为每个候选打分。在 Attune 里用作每一步的工具预筛。

**先读 [LoRA 微调详解](../../docs/lora-walkthrough.md)**：原理、一条数据走完全程、训练设置、效果解读和面试常见追问。

## 代码

| 步骤 | 文件 | 做什么 |
| --- | --- | --- |
| 1 | [`lora/step1_download.py`](lora/step1_download.py) | 下载固定版本的 ToolACE 数据集 |
| 2 | [`lora/step2_convert.py`](lora/step2_convert.py) | 解析工具列表和调用块，转换成对话事件；解析失败的记录整条丢掉 |
| 3 | [`lora/step3_samples.py`](lora/step3_samples.py) | 每次助手行动生成一条样本；按工具名和首句提问分组切分，防止泄漏 |
| 4 | [`lora/step4_train.py`](lora/step4_train.py) | 排除超长样本，LoRA 训练，对比微调前后的测试集结果 |
| — | [`lora/common.py`](lora/common.py) | 候选渲染（与线上同一个函数）、listwise 损失、指标 |

部署相关：[`fetch_artifacts.py`](fetch_artifacts.py) 下载并校验已发布的 adapter，[`prepare_qwen_base.py`](prepare_qwen_base.py) 下载并校验固定版本的基座。

## 运行

在有 GPU 的机器的 `agent-runtime/` 目录：

```sh
uv sync --extra executor
python -m training.lora.step1_download --out work/raw
python -m training.lora.step2_convert --raw work/raw/data.json --out work/episodes.jsonl
python -m training.lora.step3_samples --episodes work/episodes.jsonl --out work/samples
python -m training.prepare_qwen_base   # 下载基座到 artifacts/20261020/base/<revision>
python -m training.lora.step4_train --data work/samples \
    --base training/artifacts/20261020/base/22e683669bc0f0bd69640a1354a6d0aebcfeede5 \
    --out work/run --limit 64          # 先小规模跑通，去掉 --limit 跑全量
```

第 1 到 3 步不需要 GPU，可以在本机跑，看看样本长什么样。

## 已发布的模型

| | 基座 | LoRA |
| --- | --- | --- |
| ToolACE 测试集 Top-1 一致率（1,204 条） | 80.65% | 93.36% |
| 其中示范是直接回复（244 条） | 23.8% | 78.7% |

10,556 条训练样本、1 个 epoch、1,320 次参数更新，约 5.5 小时。[训练报告](artifacts/20261020/models/toolace-qwen4b-20261019/training-report.json)

这个 adapter 是用精简前的训练代码产出的，代码在 tag [`archive/pre-resume-scope`](https://github.com/Vaniloo/psych-agent-engineering/tree/archive/pre-resume-scope/agent-runtime/training/public_tools)，带完整的哈希清单、断点续训和磁盘重载校验。精简版的数据处理和训练逻辑与之等价，但不保证逐字节复现同一个 adapter。
