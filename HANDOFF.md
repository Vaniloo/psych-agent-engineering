# 交接文档

2026-11-26 · 默认分支 `interview/resume-scope`

先读[总架构](docs/architecture.md)和[阅读路线](docs/source-learning-roadmap.md)。这份文档讲怎么启动、怎么验收、这一版改了什么，以及出问题时先看哪里。

## 启动和验收

1. 按[根 README](README.md#快速开始)创建 `.env`，选好回复模型（DeepSeek 或 Ollama），embedding 模型单独配置。
2. **先跑通业务**：设 `EXECUTOR_PROVIDER=rules`，在根目录运行 `docker compose up --build -d`，打开 `http://localhost` 注册演示账号。
3. **演示 LoRA 预筛**：在 GPU 机器按 README 启动端口 8093 的评分服务，切到 `EXECUTOR_PROVIDER=public_qwen`，配好 `EXECUTOR_BASE_URL` 和 `EXECUTOR_API_KEY` 后重建应用容器。在运行记录里确认每一步的预筛来源是 `public_qwen`，不是 `fallback`。
4. **演示检索**：先按[知识库说明](agent-runtime/knowledge/README.md)导入资料。联网需要把演示账号加进 `WEB_SEARCH_USERS`，并启动 SearXNG。

| 演示输入 | 看哪里 |
| --- | --- |
| 先聊一条自己的偏好，另开会话问“我之前提到喜欢什么？” | 运行记录里有 `recall_memory` 调用，查询词由模型生成，回答能追溯到该账号的原话。几秒后用户画像里出现这条偏好。 |
| “考前很焦虑，有什么办法？” | `search_knowledge` 的调用次数和每轮证据强弱；第一轮偏弱时，第二轮查询词是改写过的。 |
| “请联网找 Python asyncio 官方文档并给我链接” | 搜索结果的链接与回答里的链接一致。换一个不在白名单里的账号，模型看不到 `search_web`。 |
| “最近真的撑不下去了” | 支持级别为专业支持，回复里包含援助资源。 |

## 这一版改了什么

**按简历范围精简。** 只保留认证与会话、SSE 对话、事务与幂等、限流与过载保护、跨会话记忆、知识检索、授权联网搜索和 ToolACE 微调。删除了报告与情绪分析、角色卡、用户自带模型 API、邮件和 Excel 告警、Java 端重排、动态 MCP 扩展、网页正文读取，以及配置里早已关闭的记忆生命周期实验和旁路模型服务。危机流程保留固定回复和援助资源，每条触发消息去重后写一条 `risk_alert_events` 记录。

**改成 function calling Agent。** 原来先用 DeepSeek 分类意图、再用几百行正则决定必读工具，最后由 ToolACE 选下一步；现在主模型自己选工具、写查询词，宿主只保留身份注入、工具白名单、参数校验、去重、预算、步数和超时。ToolACE 改为每一步的工具预筛。安全护栏合并成 `guard.py` 一个函数。

**补齐记忆和检索。**
- 分层记忆现在真的会写入。原来保存对话时摘要字段始终为空，四层记忆一直是“暂无”；现在后台消费 Outbox 时由模型增量更新，Java 在完成任务的同一事务里写入。
- 知识检索的 BM25 索引常驻内存，不再每次查询都拉取整个语料库；去掉了主题硬过滤、同义词扩展和类别加分，改用 RRF 融合，并标注证据强弱。中文只按双字切分，避免“的”“是”这类字让所有文档都算命中。
- 原话检索加了向量召回，并修复了合并近期消息时按时间重排、把相关度排序丢掉的问题。

精简前的完整代码在 tag [`archive/pre-resume-scope`](https://github.com/Vaniloo/psych-agent-engineering/tree/archive/pre-resume-scope)；更早的多模型实验和训练资产在[这个历史提交](https://github.com/Vaniloo/psych-agent-engineering/tree/35bd857284a6b9d80c342aa27bc177e8273f4556d1dec2af8d2897ab3dc39)。

**升级注意：** `V1__application_schema.sql` 直接删掉了废弃的表和列，已有的本地 MySQL 数据卷需要删掉重建，否则 Flyway 会因校验和不一致拒绝启动。已有的 SQLite 原话索引可以继续用，其中的旧记录没有向量，只参与 BM25。

## 回归测试

```sh
(cd agent-runtime && uv run --extra dev pytest -q)
(cd backend && ./mvnw test)
for f in frontend/tests/*.test.cjs; do node "$f"; done
```

当前结果：Python 165 项、Java 38 项、前端 3 个测试文件全部通过。Python 测试用脚本化的假模型覆盖 Agent 循环、预筛、护栏、检索和记忆写回；Java 持久化测试在 H2 上运行。要在真实 MySQL 上复验，把 `ATTUNE_TEST_JDBC_URL`、`ATTUNE_TEST_DB_USER`、`ATTUNE_TEST_DB_PASSWORD` 指向一个独立的测试库，在 `backend/` 运行 `./mvnw -Dtest=AgentPersistenceIntegrationTests test`。

还没做的：接真实模型的端到端评测、新链路下的 ToolACE 回放评测、并发压测。

## 出问题先看哪里

- **预筛一直显示 `disabled` 或 `fallback`**：先看 `EXECUTOR_PROVIDER`，再看评分服务的 `/health`、adapter 指纹是否和 `EXECUTOR_ADAPTER_SHA256` 一致，以及应用进程能否访问 `EXECUTOR_BASE_URL`。
- **回复说“暂时无法连接”**：主模型不可用。DeepSeek 检查密钥；Ollama 检查服务是否启动、模型是否支持 tool calling。
- **知识检索没有引用**：先确认知识库已导入、embedding 服务可用，再看运行记录里 `knowledge` 每轮的证据强弱。
- **画像一直是“暂无”**：后台写入要等 outbox 消费完成，正常是几秒；再看 Runtime 日志里有没有 `Memory summary update skipped`。
- **搜索没有链接**：确认账号在 `WEB_SEARCH_USERS` 里、SearXNG 可达，再看工具结果里的引擎尝试记录。
