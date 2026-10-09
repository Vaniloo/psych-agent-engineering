# 心理对话数据与 Jev 决策样本

核查日期：2026-10-09。公开可下载不等于无限制使用；这里记录作者页面及所下载版本的标签，不将代码许可证直接当成数据许可。

| 数据 | 来源与规模 | 本项目用途 | 获取与发布说明 |
| --- | --- | --- | --- |
| [PsyDial-D4](https://huggingface.co/datasets/qiuhuachuan/PsyDial-D4) | 2382 段中文长程对话；真实咨询经掩码、重建、改写 | 多轮省略、会话阶段、复杂性路由的上下文来源 | 作者 HF 数据卡标 Apache-2.0；GitHub 仓库标 CC0，记录所用 HF 版本 |
| [CPsyCounD](https://huggingface.co/datasets/CAS-SIAT-XinHai/CPsyCoun) | 3134 段报告重建的中文多轮对话 | 心理支持、自助与专业资源需求的样本来源 | HF 标 CC-BY-SA-4.0；GitHub 标 CC-BY-4.0，存在标注差异；原始报告 CPsyCounR 另需申请 |
| [PsyDTCorpus](https://github.com/scutcyr/SoulChat2.0#数据) | 5000 段合成咨询，4760 训练 / 240 测试，90365 轮 | 支持风格与疗法阶段；更适合回复模型的对照实验 | ModelScope 有作者下载入口；代码仓库 Apache-2.0，数据使用条件应按数据页确认 |
| [CPsDD](https://github.com/FakerBoom/CPsDD) | 68136 段中文模拟咨询，有策略路径、用户情境和严重程度变化 | 最值得进一步研究的策略/资源决策数据 | 作者提供 Google Drive；README 明确仅限研究用途，不能视为无条件商用开放数据 |
| [ESConv](https://github.com/thu-coai/Emotional-Support-Conversation) | 1300 段英文情感支持对话，有支持策略标签 | 带策略标注的决策实验、跨语言对照 | 官方 README 限学术研究；另有 196 条失败样本，适合研究偏好而非直接复制 |
| [PsyQA](https://github.com/thu-coai/PsyQA) | 中文长回答心理支持问答 | 单轮知识和支持策略补充 | 完整数据需签协议并申请；只公开少量样例，不作为本次直接下载数据 |

作者来源：[PsyDial 仓库](https://github.com/qiuhuachuan/PsyDial)、[CPsyCoun 仓库](https://github.com/CAS-SIAT-XinHai/CPsyCoun)。这些数据多为重建或合成对话，不应描述成完整的真实咨询记录。抽查 CPsyCoun 的公开样例发现有未经医学排查就断言身体症状源于心理的回复，因此不能将其所有回答直接当作高质量正确答案。

## 已下载到 lab data 盘

根目录：`/home/ubuntu/data/psych-agent-routing-20261009/datasets/raw/`。

- `CPsyCoun/CPsyCounD.json`：约 8.36 MB，3134 段；固定 HF revision `8fe0fa0f77630b921932bdaa6bae7481bc611cf2`。
- `PsyDial-D4/PsyDial-D4.json`：约 35.54 MB，2382 段；固定 HF revision `0ceef5ac19fb8174baa6be31ce1b2637711cb4c2`。
- 各目录保留作者 README，以及记录 revision 和文件 SHA256 的 `source.json`。

未申请 PsyQA/CPsyCounR，未向数据作者发送消息；未将受申请限制的数据从第三方镜像搬入项目。

## 训练对象与标签

本项目重点是 Jev 决策，而非泛化地训练一个聊天机器人。第一阶段标注当前请求的 `conversation_kind` 与 `route`，第二阶段标注 `risk_band` 与 `needs_professional_support`。原数据的咨询师回复不等同于这些标签。尤其是助手主动建议，不代表用户主动要求步骤；助手澄清，也不代表用户请求没有明确目标。

预处理方式：

```bash
source scripts/lab-routing-env.sh
export PYTHONPATH="$PWD/agent-runtime"
"$PSYCH_ROUTE_ROOT/.venv/bin/python" agent-runtime/training/prepare_dialogue_decisions.py \
  --sources "$PSYCH_ROUTE_ROOT/datasets/raw/CPsyCoun/CPsyCounD.json" \
            "$PSYCH_ROUTE_ROOT/datasets/raw/PsyDial-D4/PsyDial-D4.json" \
  --out "$PSYCH_ROUTE_ROOT/datasets/jev-annotation-v1" --limit 1000
```

输出是待标注的 1000 个状态，不是已标好的训练集。使用完整会话的哈希隔离集合，精确重复会话去重，当前输入只带先前上下文。语义相近与改写重复仍需额外检查。风险标签应由合适的人工审查，Jev 教师标签只能作为草稿；不能使用同一个教师评测它自己生成的标签后宣称真实效果。

正式风险实验还需要额外收集和标注明确否定、历史风险、第三人称引用、无即时意图的绝望表达、迫近计划与不同资源需求。公开咨询对话不足以覆盖业务中的报告/记忆查询，这些标签需补充本项目场景。当前没有使用这批未标注数据启动新的训练。
