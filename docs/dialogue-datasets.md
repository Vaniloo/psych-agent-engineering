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

本项目重点是 Jev 决策，而非泛化地训练一个聊天机器人。Router 独立标注 `conversation_kind`、`route`、`risk_band` 与 `support_need` 四个任务；Executor 需要工具轨迹，Memory 需要独立的记忆证据标签。原数据的咨询师回复不等同于这些标签。尤其是助手主动建议，不代表用户主动要求步骤；助手澄清，也不代表用户请求没有明确目标。

预处理方式：

```bash
source scripts/lab-routing-env.sh
export PYTHONPATH="$PWD/agent-runtime"
"$PSYCH_ROUTE_ROOT/.venv/bin/python" agent-runtime/training/prepare_dialogue_decisions.py \
  --sources "$PSYCH_ROUTE_ROOT/datasets/raw/CPsyCoun/CPsyCounD.json" \
            "$PSYCH_ROUTE_ROOT/datasets/raw/PsyDial-D4/PsyDial-D4.json" \
  --out "$PSYCH_ROUTE_ROOT/datasets/router-annotation-v2" --limit 1000
```

输出是待标注的 1000 个状态，不是已标好的训练集。使用完整会话的哈希隔离集合，精确重复会话去重，当前输入只带先前上下文。语义相近与改写重复仍需额外检查。风险标签应由合适的人工审查，Jev 教师标签只能作为草稿；不能使用同一个教师评测它自己生成的标签后宣称真实效果。

正式风险实验还需要额外收集和标注明确否定、历史风险、第三人称引用、无即时意图的绝望表达、迫近计划与不同资源需求。公开咨询对话不足以覆盖业务中的报告/记忆查询，这些标签需补充本项目场景。本轮只将经教师标注的子集加入训练，领域留出集仍独立；这些教师标签不作为风险金标准。

## 本轮选择与新增候选（2026-10-10）

已在 data 盘上的 PsyDial-D4 和 CPsyCoun 被转换为当前输入+先前历史，DeepSeek Flash 辅助标注 400 个 Router 状态，并标注对应 400 个用户观察的 Memory 写入类别。没有把咨询师后续回答当成当前用户意图、已执行的行动或风险金标准。

- [WildChat-1M](https://huggingface.co/datasets/allenai/WildChat-1M)：官方数据卡目前为 ODC-BY，适合补充中文普通聊天、话题切换和日常请求；不应携带 IP、请求头、地理等元数据进入模型。本轮尚未下载完整 3.36GB 文件。
- [xLAM function calling 60k](https://huggingface.co/datasets/Salesforce/xlam-function-calling-60k)：工具调用参考，数据卡为 CC-BY-4.0，但当前需要登录并接受访问条件。README 另有研究用途说明，未绕过访问门槛。本项目工具及权限有限，不能把它的外部 API 名称直接加入应用权限。
- [LongMemEval cleaned](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned)：官方数据卡标 MIT，可用于 Memory 的跨会话、知识更新、时间与拒答评测。它是评测基准；应保留独立评测用途，不能把同一实例同时训练和报告成绩。
- [LoCoMo](https://github.com/snap-research/locomo)：十段长对话的记忆评测集；官方 `LICENSE.txt` 是 CC-BY-NC-4.0。本轮未把它纳入训练，不将其代码镜像的许可误当作数据许可。
- Chinese SmolTalk 的数据卡与正文的社区/商业条款存在差异，暂未纳入；PsyQA 仍需申请完整数据。

当前优先级：用已有中文心理对话补 Router 的语言覆盖，以实际工具目录和可执行权限场景训练 Executor，以用户原始观察和独立证据场景训练 Memory；回复模型不使用这三种分类标签进行聊天微调。

本轮 Executor 与 Memory 的第二阶段各补充 1,200 条项目场景。它们是按任务定义构造的合成样本，仍保留 programmatic 标签来源；相关性任务把待判断的候选原文放入问题说明，训练和运行时保持一致。此轮训练不提供心理风险临床验证，也不把咨询师回答视为标准答案。

## 三万条扩量轮（2026-10-11）

已下载 WildChat-1M 第一个分片（约 230.8 MB），固定 revision `7d6490e462285cf85d91eabea0f9a954fbddcd1f`，只取中文对话 role/content，不带 IP、请求头或位置元数据。与 PsyDial-D4、CPsyCoun 合计预处理 12,000 个训练桶状态；本轮已构建 Router / Executor / Memory 各 10,000 条训练状态并完成本轮训练、校准与评测；Executor v6 已接入，Router / Memory 保留旧版本。具体组成及教师标签局限见 [公开数据扩量记录](training-public-round6.md)。

另外已检查、下载以下作者发布，用途严格区分：

| 数据 | 发布许可 / 固定版本 | 本轮实际用途 |
| --- | --- | --- |
| [ToolACE](https://huggingface.co/datasets/Team-ACE/ToolACE) | Apache-2.0；`6bda777c88d21e5a204703c1ee45597a8fa4f734` | 检查 11,300 条合成工具轨迹；本轮没有直接用于训练，外部 API 不能随意映射成本项目心理工具权限 |
| [LongMemEval cleaned](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) | MIT；`98d7416c24c778c2fee6e6f3006e7a073259d48f` | 下载 oracle 500 题；全部保留评测用途，生成 90 条有界来源诊断；不是原始完整 QA 评测 |
| [ConversationChronicles](https://huggingface.co/datasets/jihyoung/ConversationChronicles) | CC-BY-4.0；`9639b3552594a612ceafa76238a34f0f6eeaa207` | 百万量级英文合成多会话候选，本轮尚未下载或训练；不将作者代码的 MIT 当成数据许可 |

教师辅助标注中途遇到鉴权/额度拒绝，保留有效草稿并停止新调用；已有数据配合回放足以形成三万条训练状态。教师风险标签、同教师两遍一致性以及 programmatic 业务标签均不等于临床审核。
