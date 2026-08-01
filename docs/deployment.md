# 部署与运维

## Compose 服务

```text
frontend -> backend -> agent-runtime -> mcp-server
              |              |              |
            MySQL        Redis Stack     Chroma/Ollama
```

```bash
cp .env.example .env
docker compose config
docker compose up --build
```

首次启动会拉取 Chat 与 Embedding 模型，因此耗时取决于网络和磁盘。

## 必改配置

- `JWT_SECRET`
- `INTERNAL_SERVICE_TOKEN`：必须与 JWT secret 不同
- `MYSQL_PASSWORD`
- `MYSQL_ROOT_PASSWORD`
- 邮件告警启用时的账号与授权信息

不要把真实 secret 写入仓库。生产环境应使用平台 secret manager，并限制 Spring `/internal/agent/**` 的网络来源。

## 网络边界

- 只发布 frontend `80` 和 backend `8080`。
- Runtime `8090` 与 MCP `8001` 仅在 Compose 网络 `expose`。
- MySQL、Redis、Chroma、Ollama 默认不发布到宿主机。

## 健康与指标

- Spring：`GET /actuator/health`
- Runtime：容器内部 `GET /health`
- Runtime Prometheus：容器内部 `GET /metrics`
- MCP：TCP readiness healthcheck

Runtime `/health` 区分整体 `UP/DEGRADED`，并返回 Chroma/Ollama 依赖状态。Redis checkpoint 初始化失败会记录警告并切换内存 checkpoint。

## 容量与超时

- Spring SSE emitter：180 秒。
- Java Runtime HTTP：3 秒连接、180 秒总请求。
- Nginx read timeout：190 秒，buffering disabled。
- Agent stream executor：core 4、max 16、queue 200。
- RAG cache TTL：10 分钟。
- Chroma sparse corpus：默认最多 5000 chunks。

生产环境应根据并发量独立扩容 Runtime；若保留 Redis checkpoint，可让无状态 Runtime 实例共享会话执行状态。MCP Server 使用 stateless HTTP，可水平扩容。

## 回滚与索引恢复

MySQL 是知识原文事实源。Runtime 索引异常时：

1. 修复 Chroma/Ollama。
2. 调用 `POST /knowledge/reindex?source=...`。
3. 检查 Runtime metrics 和应用日志。
4. 用管理端搜索验证 citation 分数与来源。

不要把 Chroma volume 当作唯一知识备份。
