# 心理知识库扩充与公开数据集核对（2026-10-19）

当前开发环境已导入103条新资料，来自17个机构页面。SQL原文与元数据读回一致，向量索引由311个片段增加到454个；完整重放103条后仍为454个片段，没有重复增加。原有311个片段的ID、内容、元数据逐项比对一致。

这里的“资料”是一个来源章节，不等于整页、对话或训练样本。当前336条资料组成如下：

| 来源 | 资料数 | 本轮新增 |
| --- | ---: | ---: |
| 原始本地FAQ | 30 | 0 |
| NHS CBT介绍 | 1 | 0 |
| NIMH | 228 | 26 |
| NHLBI | 27 | 27 |
| NCCIH | 25 | 25 |
| CDC | 25 | 25 |
| 合计 | 336 | 103 |

此前已有的202条NIMH资料主要覆盖焦虑、抑郁、双相障碍、创伤、社交焦虑、惊恐、饮食障碍等主题。新资料重点补日常支持与求助准备：

| 新增类别 | 条数 | 示例 |
| --- | ---: | --- |
| 睡眠 | 27 | 作息、咖啡因、睡眠日记、失眠求助、CBT-I介绍 |
| 放松 | 14 | 渐进式肌肉放松、引导想象、放松练习适用范围 |
| 正念 | 11 | 研究证据与安全性限制 |
| 情绪调节 | 9 | 表达感受、处理问题、情绪健康 |
| 社会支持 | 16 | 建立联系、支持他人、孤独与归属感 |
| 求助准备 | 11 | 与医护沟通、描述困扰、准备就诊问题 |
| 儿童与青少年 | 15 | 寻求支持、成长与压力、睡眠 |

18个拟收录页面中17个抓取成功。CDC `mental-health/living-with/index.html` 本轮返回403，未收录；不以错误页、导航或自行生成的替代文本填充。成功来源清单在语料JSON的 `sources` 字段，拟收录清单不代表全部导入成功。

## 数据来源与复用

新增内容来自原始机构页面，不是把咨询对话当作医学结论：

- [NIMH 心理健康资料](https://www.nimh.nih.gov/health/topics)，[复用政策](https://www.nimh.nih.gov/site-info/policies)。
- [NHLBI 睡眠习惯](https://www.nhlbi.nih.gov/health/sleep-deprivation/healthy-sleep-habits)，[复用政策](https://www.nhlbi.nih.gov/about/contact/trademark-branding-and-logo)。Source: National Heart, Lung, and Blood Institute; National Institutes of Health; U.S. Department of Health and Human Services.
- [NCCIH 放松技巧](https://www.nccih.nih.gov/health/relaxation-techniques-what-you-need-to-know)，[复用政策](https://www.nccih.nih.gov/tools/privacy)。
- [CDC 情绪健康](https://www.cdc.gov/emotional-well-being/improve-your-emotional-well-being/)，[材料复用政策](https://www.cdc.gov/other/agencymaterials.html)。

只处理选定的机构文本；不复制图片、标识或第三方媒体。导航、参考文献目录、药物指导、研究招募及地区热线信息不入库。原文可免费访问；引用不代表机构认可本应用。前端在本轮接受的机构来源下显示原文链接，打开新窗口，并提示自动译文未经过专业审核。

英文正文按来源章节保留，中文译文后附对应英文；来源URL、章节、受众、许可链接、内容哈希、版本和审核状态随条目保存。全103条通过格式及数字保留检查，但这些检查不等于语义或临床审核；`not-human-reviewed` 状态保留。`reviewedAt` 是此次资料处理日期；`sourceReviewedAt=not-stated` 表示未提取到页面审核日期，不能用站点页脚年份冒充。

## 可复现的处理流程

1. 已审核的来源清单 → 只允许准确主机与路径的抓取；每次跳转重新核对目的地址。
2. 站点结构解析 → 去除样式、导航、排除章节和过短文本；未知结构隔离，不猜测正文。
3. 稳定章节ID与正文哈希去重 → 英文快照。
4. 按原文哈希缓存的可恢复翻译 → 中英对照资料；服务失败停止后续调用。
5. Python与Java一致的来源、许可、元数据校验 → SQL保存 → Chroma分块嵌入。
6. 全量重放验证幂等 → SQL读回核对 → 原有片段逐项保留检查 → 检索与Agentic RAG验证。

当前开发数据库为文件H2，生产配置可用MySQL；语料导入始终走内部服务接口，不直接改数据库文件。索引包含受众、标题、关键词、章节的有限长度前缀。原有通用FAQ、检索排序权重和个人记忆库没有删除或重训。

新增站点不能只写一个 `public-domain` 标签就得到可信身份；Python `knowledge_sources.py` 与 Java `KnowledgeSourcePolicy` 核对主机、路径、许可声明和发布机构。原NIMH记录兼容历史元数据。来源信任只表示可追溯与复用身份，不表示自动译文或生成回答已获临床验证。

重建命令见 [knowledge/README](../agent-runtime/knowledge/README.md)。

## 公开问答 / 对话数据库

检查的是2026-10-19作者仓库及数据卡的现行声明，公开可下载不等于允许任意上线复用。

| 数据集 | 适合的用途 | 本轮核对结果与处理 |
| --- | --- | --- |
| [MedQuAD](https://github.com/abachaa/MedQuAD) | 有来源的医学问答、检索诊断候选 | CC BY 4.0；下载作者原仓库并固定提交，筛出61条心理/睡眠相关有答案记录，放入历史候选区，未进正式索引 |
| [PsyQA](https://github.com/thu-coai/PsyQA) | 中文心理支持回答、援助策略研究 | 仓库只有少量样例；完整集需填写协议、签名并经作者审核。没有申请或导入完整集 |
| [ESConv](https://github.com/thu-coai/Emotional-Support-Conversation) | 支持策略、对话评测 | 作者说明1300段对话，另有196段失败样本；限学术研究。未接入产品事实知识库 |
| [Amod counseling conversations](https://huggingface.co/datasets/Amod/mental_health_counseling_conversations) | 咨询风格研究候选 | 当前数据卡为RAIL-D，另有商业使用、内容改动与再分发约束，不能沿用旧资料里“随便用”的判断；未导入 |

本项目当前需要的是可以支持回答的知识证据。咨询记录的某一句建议有特定上下文，不能因数据集公开就当作普适结论。PsyQA、ESConv更适合单独的风格/策略研究与评估，使用前仍须满足其协议。

MedQuAD实测清点：

- 作者宣称47,457个医学问答；固定提交实际读到47,441个问答节点、16,407个非空答案。两种口径均保留。
- 作者因版权移除了三个MedlinePlus子集的答案，本项目没有补抓或重建这些答案。
- 心理与睡眠的精确主题标签筛出126个问答节点，其中65个缺问题或答案，留下61个候选；包含8条治疗类问题，且部分原站点已归档。
- `historical=true`、`currentSourceReviewed=false`、`runtimeEligible=false`；核对现行原文和回答后才能进入正式知识库。不能把整个47k医学规模记为心理知识库规模。

固定版本：`577bd37b96c02d1833b2c9eed2de9f96964e96cb`。引用：Asma Ben Abacha and Dina Demner-Fushman, *A Question-Entailment Approach to Question Answering*, BMC Bioinformatics, 2019。可复现的筛选器为 `benchmarks/prepare_medquad_candidates.py`；候选保留原始答案，不进行未经核对的中文改写。

## 验证与已知缺口

结果文件在 `agent-runtime/evaluation/knowledge-expansion-20261019/`；运行原始产物保留在本会话 `outputs/knowledge-expansion-20261019/`，不包含在公开提交中。

- Python：1043通过、1跳过，另有现存Starlette/httpx弃用提示。
- Java：94项测试，0失败、0错误、1跳过。
- 前端：5个测试文件通过，覆盖知识进度、来源URL过滤、译文提示及既有工具展示行为。
- 新建合成测试账号，经前端代理、Java和Runtime的真实SSE验证通过：1次知识读取返回新增NCCIH资料及原文链接；不写入真实账号的对话和记忆。最后的来源进度专项回归30项通过。
- 新资料固定14个中文预期来源用例：扩充前0/14，扩充后Top3与Top6均9/14。这是来源覆盖诊断，原先没有这些页面，不能把差值解读成回答准确率提升。
- 原有18个用例：Top3、Top6均17/18；儿童创伤支持的预期来源仍未命中，旧问题保留。
- 5个真实语料、真实生成服务的Agentic RAG诊断，固定知识工具选择器以隔离检索行为，不写真实用户对话或记忆。夜班与安全问题各2次读取并标记部分证据；支持朋友通过第二次读取找到CDC来源。表达感受与就诊准备仍接受了旧FAQ。读取及审查耗时约1.5–4.8秒，不含最终回复生成，不是并发延迟测试。

优先缺口：部分个人化中文查询仍使短FAQ占据前排，新增官方片段没有稳定覆盖；一条CDC来源抓取失败；安全/夜班问题仍有证据缺口。模型的“足够”判定不是人工标签，最终生成也可能补入常识性建议。例如夜班回答出现了已保留片段没有直接支持的“路上戴墨镜”，不能声称每句都被证据约束。下一步应针对这些真实漏召回和证据覆盖做检索优化及独立评测，不通过事后改预期来源或批量生成常识条目掩盖问题。

这次没有重新训练本地JEV/Laya/Qwen模型；既有模型配置、记忆和目标状态保持原样。当前开发环境Agentic RAG继续启用，审查仍使用已配置的DeepSeek服务。
