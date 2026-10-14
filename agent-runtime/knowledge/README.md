# 可重建的心理知识语料

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

# 内部认证接口先保存MySQL原文，再分块嵌入Chroma；同版本重放验证无重复
.venv/bin/python benchmarks/import_knowledge_corpus.py \
  --env /path/to/private-runtime.env --documents knowledge/nimh-corpus-zh.json \
  --output /path/to/work/import-report

.venv/bin/python benchmarks/knowledge_corpus_retrieval.py \
  --env /path/to/private-runtime.env --cases knowledge/corpus-retrieval-cases.json \
  --output /path/to/work/retrieval.json
```

导入器使用当前语料清单内的版本，不删除原FAQ或其他知识；本次稳定ID允许只更新本语料。SQL保存成功但向量化失败会返回 `indexed=false`，重试可以补索引。相同索引版本不重新生成向量，版本变更先成功写入新片段再清理旧片段。原文是事实来源，向量是可重建索引。

`corpus-retrieval-cases.json` 是开发者编写的中文预期来源检查，检验来源页召回，不是段落准确率、回复正确率或临床有效性评测。首轮英文资料召回不足的结果保留在会话产物，不能只报道翻译后的结果。
