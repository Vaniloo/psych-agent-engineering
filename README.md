# Attune · 心理支持对话 Agent

Attune 是一个面向多轮心理支持对话的 Agent：能回顾用户自己说过的话、检索心理健康知识库，并在获得授权后联网查资料。Java 业务网关负责账号、会话和事务，Python Runtime 用 LangGraph 跑一个原生 function calling 的 Agent 循环，SSE 把回复和工具执行状态实时推给前端。

> 这是一个工程学习项目，不是医疗产品。危机场景只给出固定的援助信息，不能替代专业帮助。

## 架构一览

```
浏览器 ─JWT/SSE─► Spring Boot 网关 ─HTTP─► Python Agent Runtime ─► 工具
                  · JWT 认证、会话隔离      · guard：安全扫描 + 风险复核     · search_knowledge（Chroma + BM25）
                  · Redis 限流、并发闸门    · prepare：上下文与系统提示      · recall_memory（SQLite 原话索引）
                  · 危机关键词快速拦截      · agent ⇄ tools：function calling · search_web（SearXNG）
                  · MySQL 事务 + Outbox    · ToolACE LoRA 每步预筛工具        · get_support_resources
```

- **Agent 循环**：主模型自己决定调哪个工具、查询词怎么写；宿主只做模型不该做的事，包括注入账号身份、按账号限定工具、校验参数、去重，以及限制调用预算、步数和超时。[graph.py](agent-runtime/psych_agent_runtime/graph.py) · [tools.py](agent-runtime/psych_agent_runtime/tools.py)
- **安全护栏**：确定性规则扫描加模型风险复核，把对话分为普通、专业支持、危机三档。危机直接返回固定回复，不经过模型。[guard.py](agent-runtime/psych_agent_runtime/guard.py)
- **工具预筛**：基于 ToolACE 对 Qwen3-Reranker-4B 做 LoRA 微调，每一步给候选工具和“直接回复”打分，只把分数更高的工具交给主模型；评分服务不可用时交出全部工具。[prefilter.py](agent-runtime/psych_agent_runtime/prefilter.py) · [LoRA 微调详解](docs/lora-walkthrough.md)
- **知识检索**：Chroma 向量和常驻内存的 BM25 双路召回，用 RRF 融合并标注证据强弱。证据偏弱时，模型改写查询再查一次。[rag.py](agent-runtime/psych_agent_runtime/rag.py)
- **分层记忆**：原话索引、会话摘要、中长期记忆和用户画像四层。后台消费 Outbox 时先把原话和向量写入索引，再由模型把本轮内容增量合并进摘要和画像；Java 在完成任务的同一事务里写入，重试不会重复写。[memory.py](agent-runtime/psych_agent_runtime/memory.py) · [memory_writer.py](agent-runtime/psych_agent_runtime/memory_writer.py)
- **事务与幂等**：两条消息和记忆任务回执在同一个事务里提交；“用户 ID + 请求 ID”联合唯一约束加 `SELECT … FOR UPDATE` 防止重复写入。[ConversationMemoryService.java](backend/src/main/java/com/vanilo/psych/agent/service/ConversationMemoryService.java)

完整的请求流程和设计取舍见[总架构](docs/architecture.md)；想跟着代码读懂一次请求，按[阅读路线](docs/source-learning-roadmap.md)走。

## 目录

| 目录 | 内容 |
| --- | --- |
| `backend/` | Spring Boot 网关：认证、会话、事务、限流、SSE 转发、Outbox |
| `agent-runtime/` | Python Runtime：Agent 图、工具、检索、记忆、ToolACE 评分服务与训练代码 |
| `frontend/` | 原生 HTML/JS 前端，无需构建 |
| `contracts/` | Java 与 Python 共用的安全规则对照用例 |
| `docs/` | 总架构、阅读路线、LoRA 微调详解 |

## 快速开始

1. 复制 `.env.example` 为 `.env`，填写数据库密码、`JWT_SECRET` 和 `INTERNAL_SERVICE_TOKEN`。选择回复模型：
   - `AI_PROVIDER=deepseek` 并设置 `DEEPSEEK_API_KEY`；或
   - `AI_PROVIDER=ollama`，使用本地 Ollama（需要支持 tool calling 的模型）。

   embedding 始终由 Ollama 的 `OLLAMA_EMBEDDING_MODEL` 提供。
2. 不需要 GPU 时设 `EXECUTOR_PROVIDER=rules`：跳过 ToolACE 预筛，每步把全部可用工具交给主模型。
3. 在仓库根目录启动：

   ```sh
   docker compose up --build
   ```

   打开 `http://localhost` 注册账号即可对话。运行记录页可以查看每轮调用了哪些工具、预筛来源和各节点耗时。

Docker Compose 读取 `.env`；本机分别启动各服务时，`scripts/` 里的脚本读取 `local.env`（参考 `local.env.example`）。

### 启用 ToolACE 预筛（需要 GPU）

在 GPU 机器的 `agent-runtime/` 目录下载 adapter 和固定版本的基座，然后启动评分服务：

```sh
uv sync --extra executor
uv run --extra executor python -m training.fetch_artifacts
uv run --extra executor python -m training.prepare_qwen_base
PUBLIC_TOOL_API_KEY=<自己生成的密钥> uv run --extra executor python -m psych_agent_runtime.public_tool_service \
  --base training/artifacts/20261020/base/22e683669bc0f0bd69640a1354a6d0aebcfeede5 \
  --checkpoint training/artifacts/20261020/models/toolace-qwen4b-20261019 \
  --port 8093
```

评分服务只监听回环地址，远程机器可以用 `ssh -N -L 8093:127.0.0.1:8093 <gpu-host>` 转发。然后在 `.env` 设置 `EXECUTOR_PROVIDER=public_qwen`、`EXECUTOR_API_KEY`（与 `PUBLIC_TOOL_API_KEY` 相同）和应用进程能访问到的 `EXECUTOR_BASE_URL`。基座已经缓存时，可以给 `prepare_qwen_base` 传 `--existing`，并把 `--base` 改为缓存快照的路径。

### 启用联网搜索

在 `.env` 设置 `WEB_SEARCH_ENABLED=true` 和 `WEB_SEARCH_USERS=允许联网的账号`（多个账号用逗号隔开），然后连同 SearXNG 一起启动：

```sh
python3 scripts/prepare_web_search.py --data-dir data/web-search
docker compose --env-file .env --env-file data/web-search/compose.env \
  -f docker-compose.yml -f compose.search.yml up --build -d
```

只有白名单里的账号能看到 `search_web` 工具，结果只含摘要和链接。主引擎默认是 360search 和 Yandex，没有可用结果时，在同一时限内补查 Naver 和 Mwmbl；两组都可以用 `WEB_SEARCH_ENGINES` 和 `WEB_SEARCH_FALLBACK_ENGINES` 修改。

### 导入知识库

`agent-runtime/knowledge/` 里提交了 NIMH 等机构的公开资料快照，导入方式见[知识库说明](agent-runtime/knowledge/README.md)。

## 测试

```sh
(cd agent-runtime && uv run --extra dev pytest -q)
(cd backend && ./mvnw test)
for f in frontend/tests/*.test.cjs; do node "$f"; done
```

Python 测试用脚本化的假模型和假检索库覆盖完整的 Agent 循环；Java 持久化测试在 H2 上验证事务、幂等和记忆写回。

## 历史版本

精简和重构之前的完整代码保存在 tag [`archive/pre-resume-scope`](https://github.com/Vaniloo/psych-agent-engineering/tree/archive/pre-resume-scope)，包括报告分析、角色卡、动态 MCP 扩展和多模型实验等功能。改动记录见[交接文档](HANDOFF.md)。
