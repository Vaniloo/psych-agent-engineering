# Attune 单 Executor 版交接

2026-11-20 · 分支 `interview/single-executor`

Attune 能回顾用户自己的历史对话、查公共心理知识、跟踪目标，并在得到授权后联网查资料。面试先读[总架构](docs/architecture.md)和[学习路线](docs/source-learning-roadmap.md)。

## 启动和验收

1. 用 `.env.example` 创建根目录 `.env`，设置数据库密码、JWT、内部服务密钥，选择 Ollama 或 DeepSeek 生成回答。Docker 使用 `.env`，本机开发脚本读取 `local.env`。聊天模型和 embedding 模型分别配置。
2. **先跑业务**可以设 `EXECUTOR_PROVIDER=rules`，在根目录运行 `docker compose up --build -d`，打开 `http://localhost` 创建演示账号。
3. **演示真实 LoRA** 时，在 GPU 服务器按 README 恢复固定基座和 ToolACE adapter，启动端口 8093 的评分服务。切到 `EXECUTOR_PROVIDER=public_qwen`，设置容器能访问的 `EXECUTOR_BASE_URL` 和匹配的 `EXECUTOR_API_KEY`，重新创建应用容器。在运行指标页核对 Executor 已就绪，再看具体运行记录里的工具选择来源。启动命令见[根 README](README.md#运行)。
4. **演示检索**时，设置 `AGENTIC_RAG_ENABLED=true` 并准备已索引的知识。联网还需配置账号名单并启动 SearXNG。如需读网页正文，再启用 `WEB_CONTEXT_ENABLED`。[联网配置](README.md#启用联网搜索)

| 演示输入 | 看哪里 |
| --- | --- |
| 先聊一条自己的偏好，另开会话问“我之前提到喜欢什么？” | 运行记录中有个人记忆读取，回答能追溯到该账号的原话。 |
| “请用知识库解释什么是认知重评” | 查看检索命中和引用。是否补查取决于证据评估。 |
| “请联网找 Python asyncio 官方文档并给我链接” | 搜索工具的来源列表和回答链接一致。再发“不要联网”，观察是否跳过外部搜索。 |
| “帮我创建一个每天散步的目标”，随后回报某个明确步骤已完成 | 在目标页核对创建和进度回执；新开会话仍能读到保存结果。 |

## 这次精简了什么

- 路由直接走规则，读取控制器只接 ToolACE 或规则；运行状态接口只探测正在使用的 Executor。目标创建和进度同步复用同一条写入前置检查。Java 在同一处解析已保存的行动列表。
- 容量探针复用应用的 ToolACE 评分客户端，场景探针从运行状态判断准备情况。原来的多模型压测数字对应当时的服务组合。
- 前端页面共用 HTML 转义函数。镜像统一复制页面脚本，补齐了工具活动、记忆状态、角色工作台和模型设置脚本。本机后端、Runtime 和 MCP 启动脚本共用环境加载逻辑。
- 移除了依赖个人绝对路径的 BGE 演示入口。权重编辑、激活移植和注意力头诊断的代码、实验数据和原测试可以从固定历史版本取回。旧多模型训练流程、分轮报告、旧面试稿及对应实验测试可在同一历史版本复查。当前 docs 目录只保留总架构和学习路线。[历史源码与实验](https://github.com/Vaniloo/psych-agent-engineering/tree/35bd857284a6b9d80c342aa27bc177e8273f4556d1dec2af8d2897ab3dc39)

微调训练只维护 ToolACE Executor。数据准备、重训和部署核验集中在[训练说明](agent-runtime/training/README.md)，旧模型训练资产保存在该页链接的历史提交中。

## 回归与排查

在仓库根目录可复跑下面三组测试。

```sh
(cd agent-runtime && uv run --extra dev pytest -q)
(cd backend && ./mvnw test)
node --test frontend/tests/*.test.cjs
```

训练目录精简后，Python 回归 936 项通过；旧训练专用用例见固定历史提交。前端基线为 5 个测试文件通过。前端镜像已构建，从 Nginx 实际读取了首页引用的 15 个文件，内容与源码一致。规则模式下 Runtime 成功启动，Chroma 和 Ollama 健康检查通过，带内部凭据可以读取运行状态。退役模型组合和移出分支的独立研究用例不计入当前测试数。

Java 后端使用 MyBatis。空库启动时，Flyway 执行 V1，创建 15 张业务表及其外键、唯一约束与索引。由上一版完整建好的业务库首次启动时，Flyway 将现有结构登记为 V1，保留数据。已有表但来源或版本不明的库，升级前先对照[基线 SQL](backend/src/main/resources/db/migration/V1__application_schema.sql)核对结构。后续结构变更添加新的版本脚本。

Java 回归共 103 项，102 项通过，1 项依赖完整外部服务而预设跳过。43 项持久化集成测试分别在 MySQL 8.4 空库和预置旧版数据的库上通过。复验时可把 `ATTUNE_TEST_JDBC_URL`、`ATTUNE_TEST_DB_USER`、`ATTUNE_TEST_DB_PASSWORD` 指向独立测试库，在 backend 目录执行 `./mvnw -Dtest=AgentPersistenceIntegrationTests test`。

遇到问题先看实际停在哪一步：

- **Executor 一直显示规则**。核对部署配置、评分服务的健康信息、adapter 指纹和实际轨迹。必读工具由规则直接执行，这类样例可能没有模型评分。
- **RAG 没有引用**。先看知识库是否已有索引、embedding 服务是否可用，再查看检索命中与证据评估。**搜索没有链接**时，查看账号是否获准、SearXNG 是否可达，以及工具返回的引擎尝试记录。
- **目标没有变化**。按本轮请求查看原始指令是否被接受、业务写入是否有回执，涉及已有步骤时核对目标归属和版本。
- **比较效果**。公开测试集的 93.36% 是历史训练时的下一动作示范一致率。本轮做了代码回归和容器验证；GPU 权重训练、真实模型质量评估和并发压测需要分别执行，并保留各自报告。
