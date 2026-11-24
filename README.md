# Attune · 单 Executor 面试版

Attune 是一个可以回顾历史对话、检索心理知识并按需联网查资料的对话 Agent。运行链路只接一个自训 LoRA，负责从当前允许的读取工具和文字回复中选择下一步。Planner 和风险判断分别调用 DeepSeek API。回答模型可独立配置。Spring Boot 使用 MyBatis 管理 MySQL 中的会话、消息和记忆任务，Python 负责编排 Agent、记忆检索与工具调用。

面试准备先读[总架构](docs/architecture.md)，再按[90 分钟学习路线](docs/source-learning-roadmap.md)跟一次请求。启动、演示与排查用[交接文档](HANDOFF.md)。

## 运行

从根目录复制 `.env.example` 为 `.env`，设置自己的数据库密码、JWT 和内部服务密钥。设置服务端 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_MODEL` 即可启用意图与风险判断。即使 `AI_PROVIDER=ollama`，这两步也使用 DeepSeek。缺少密钥时走本地规则。Compose 使用 `.env`，本机开发脚本使用 `local.env.example` 对应的 `local.env`。

1. 在 GPU 机器进入 `agent-runtime`，安装 Executor 依赖、下载 ToolACE adapter 和固定版本基座。下载项目 Release 需要有仓库访问权限。

   ```sh
   uv sync --extra executor
   uv run --extra executor python -m training.fetch_artifacts
   uv run --extra executor python -m training.prepare_qwen_base
   ```

2. 设置私有的 `PUBLIC_TOOL_API_KEY`，启动评分服务。如果基座已缓存，可在上一步给 `prepare_qwen_base` 传 `--existing`，并把下方 `--base` 改为该快照的路径。

   ```sh
   uv run --extra executor python -m psych_agent_runtime.public_tool_service \
     --base training/artifacts/20261020/base/22e683669bc0f0bd69640a1354a6d0aebcfeede5 \
     --checkpoint training/artifacts/20261020/models/toolace-qwen4b-20261019 \
     --port 8093
   ```

3. 服务监听 GPU 机器的回环地址。使用 lab 时可在开发机运行 `ssh -N -L 8093:127.0.0.1:8093 lab`。应用的 `EXECUTOR_API_KEY` 与 worker 密钥一致，`EXECUTOR_BASE_URL` 必须从应用进程可达。示例分别给出 Docker Desktop 和本机进程的地址。
4. 根目录执行 `docker compose up --build`，浏览器访问 `http://localhost`。仅调试业务流程时可设 `EXECUTOR_PROVIDER=rules`。在运行指标页核对 Executor 实际状态和调用来源。

## 启用联网搜索

在 `.env` 设置 `WEB_SEARCH_ENABLED=true` 和 `WEB_SEARCH_USERS=你的登录账号`。多个账号用逗号隔开。用户明确请求联网后，宿主才会把公开查询交给搜索工具。需要读取网页正文时，再设 `WEB_CONTEXT_ENABLED=true`。

从仓库根目录启动应用和 SearXNG：

```sh
python3 scripts/prepare_web_search.py --data-dir data/web-search
docker compose --env-file .env --env-file data/web-search/compose.env \
  -f docker-compose.yml -f compose.search.yml up --build -d
```

Compose 服务通过 `http://searxng:8080` 访问搜索，本机直接运行 Python 时用 `http://127.0.0.1:18095`。默认先用 360search 和 Yandex；没有可用链接时，在同一个总时限内尝试一次 Naver 和 Mwmbl。两组引擎可用 `WEB_SEARCH_ENGINES` 和 `WEB_SEARCH_FALLBACK_ENGINES` 修改，备用列表留空即可关闭补查。正文读取失败时，结果会标明仅有搜索摘要。
