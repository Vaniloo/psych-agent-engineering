# 可重建的心理知识语料

2026-10-19 新增 `practical-corpus-20261019-en.json` / `practical-corpus-20261019-zh.json`：来自 NIMH、NHLBI、NCCIH、CDC 的17个页面、103条资料。它们补充睡眠习惯、放松与安全、情绪调节、社会支持、就诊准备、儿童和青少年支持。当前开发环境共336条原始资料、454个向量片段，其中包括原有30条FAQ、1条NHS资料和202条NIMH资料。片段数不是问答数。

来源清单与类别见 `practical-sources-20261019.json`；扩充、检索结果和公开数据集调研见 [扩充记录](https://github.com/Vaniloo/psych-agent-engineering/blob/797d683770e23f1f0336f824a3315adb275b91f1/docs/knowledge-expansion-20261019.md)。原有语料没有被覆盖。新资料只收录机构文本，不收录图片、标识、药物指导、研究招募或地区热线；正文含中文自动译文和完整对应英文段落，尚未逐条经过专业审核。

Source: National Heart, Lung, and Blood Institute; National Institutes of Health; U.S. Department of Health and Human Services. 其余来源分别为 National Institute of Mental Health、National Center for Complementary and Integrative Health、Centers for Disease Control and Prevention。原文可在条目来源链接免费访问；内容整理与翻译不代表机构认可本应用。

`nimh-corpus-20261014.json` 保存33个NIMH官方页面的英文文本快照，清洗去重后202个条目；`nimh-corpus-20261014-zh.json` 保留中文自动译文与英文原文。只收录文本，不复制图片；导航、研究招募、药物章节和地区热线信息排除。全量译文尚未由专业人员逐条审核。

信息来源是 National Institute of Mental Health（NIMH）。网站文本依据其 [Copyright Policy](https://www.nimh.nih.gov/site-info/policies) 在公共领域内复用，修改不得误导，不代表NIMH对本应用的认可。来源页面可能更新，应定期重新核对。

每条记录含稳定ID、来源链接、主题、章节、语言、许可、语料版本、内容和来源页面SHA256。`sourceReviewedAt` 是来源声明的审核日期，`reviewedAt` 是本轮来源访问/核对日期，不代表临床审核；`translationReviewStatus` 明确为 `not-human-reviewed`。

在 `agent-runtime/` 运行以下流程，凭据文件保持在仓库之外或忽略文件中：

```bash
# 生成来源快照，原始HTML缓存建议放在工作产物目录
.venv/bin/python -m psych_agent_runtime.knowledge_corpus \
  --output knowledge/nimh-corpus.json --cache /path/to/work/nimh-html

# 可选中文翻译；按原文哈希缓存，可从缓存恢复；服务失败时停止后续调用
.venv/bin/python benchmarks/translate_knowledge_corpus.py \
  --env /path/to/private-generation.env --source knowledge/nimh-corpus.json \
  --output knowledge/nimh-corpus-zh.json --cache /path/to/work/translations

# 内部认证接口先保存SQL原文，再分块嵌入Chroma；同版本重放验证无重复
.venv/bin/python benchmarks/import_knowledge_corpus.py \
  --env /path/to/private-runtime.env --documents knowledge/nimh-corpus-zh.json \
  --output /path/to/work/import-report

.venv/bin/python benchmarks/knowledge_corpus_retrieval.py \
  --env /path/to/private-runtime.env --cases knowledge/corpus-retrieval-cases.json \
  --output /path/to/work/retrieval.json
```

导入器使用当前语料清单内的版本，不删除原FAQ或其他知识；本次稳定ID允许只更新本语料。SQL保存成功但向量化失败会返回 `indexed=false`，重试可以补索引。相同索引版本不重新生成向量，版本变更先成功写入新片段再清理旧片段。原文是事实来源，向量是可重建索引。

`corpus-retrieval-cases.json` 是开发者编写的中文预期来源检查，检验来源页召回，不是段落准确率、回复正确率或临床有效性评测。首轮英文资料召回不足的结果保留在会话产物，不能只报道翻译后的结果。

扩充语料可用相同的翻译、导入、检索命令；构建阶段改用经过来源审核的清单：

```bash
.venv/bin/python -m psych_agent_runtime.practical_corpus \
  --manifest knowledge/practical-sources-20261019.json \
  --existing knowledge/nimh-corpus-20261014.json \
  --output /path/to/work/practical-en.json --cache /path/to/work/practical-html

.venv/bin/python benchmarks/translate_knowledge_corpus.py \
  --env /path/to/private-generation.env --source /path/to/work/practical-en.json \
  --output /path/to/work/practical-zh.json --cache /path/to/work/translations \
  --version practical-mental-health-YYYYMMDD-zh-v1
```

未知页面结构、未审核的来源、许可不匹配或抓取失败均不入库。`reviewedAt` 只表示此次资料处理日期，不能解释为临床审核。新增网站需同步审核 Python 与 Java 的 `KnowledgeSourcePolicy`；不能仅凭传入的许可字符串获得可信来源身份。

MedQuAD 是单独的历史问答候选区，没有接入正式索引：

```bash
# 使用固定版本的作者原仓库；输出目录必须尚不存在
.venv/bin/python -m benchmarks.prepare_medquad_candidates \
  --repository /path/to/MedQuAD --output /path/to/work/medquad-candidates
```

候选保留原始回答、来源URL、数据集提交版本和 CC BY 4.0 许可，标为 `runtimeEligible=false`。当前筛出61条心理/睡眠相关有答案记录，需核对现行原文再晋级。不要把47,457条的全医学宣传规模写成项目心理知识库规模；作者已移除三个子集的答案。
