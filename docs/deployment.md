# 部署与运维

## Compose 服务

```text
frontend -> backend -> agent-runtime -> local capabilities / optional mcp-server
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

生产环境应根据并发量独立扩容 Runtime；checkpoint 只能保存执行状态。当前记忆 SQLite 共享 volume 适合单机部署；跨主机扩容需迁移索引和任务调度，并处理会话并发次序。

## 回滚与索引恢复

MySQL 是知识原文事实源。Runtime 索引异常时：

1. 修复 Chroma/Ollama。
2. 调用 `POST /knowledge/reindex?source=...`。
3. 检查 Runtime metrics 和应用日志。
4. 用管理端搜索验证 citation 分数与来源。

不要把 Chroma volume 当作唯一知识备份。

## 三 Laya 与记忆存储

`compose.laya.yml` 提供三个独立 CPU worker，权重只读挂载。配置和检查点门槛见 [本地三 Laya Agent](local-laya-agent.md)。健康接口的 ready=false 表示没有加载权重；HTTP 200 仅表示进程可访问。

`memory-index` volume 保存 SQLite 证据索引；MySQL 消息与 AgentTurnReceipt outbox 是事实源。索引写入失败保留任务为 PENDING 并重试；没有自动历史回填或失败队列。清空索引后不能仅靠已标 DONE 的队列自动恢复全部记忆，恢复工具尚待实现，请保留 volume 备份。

lab 的全部环境、缓存、数据、权重和日志必须在 `/home/ubuntu/data/psych-agent-routing-20261009`。不要使用本地启动脚本的默认临时缓存路径在 lab 安装，先加载数据盘环境。

## 升级注意

本次删除旧 Java Agent、ToolRegistry、/tools、/message 和普通 /chat 接口；统一入口为 /agent/chat(/stream)，报告分析 /chat/analyze 保留。目标新增 support_goals，幂等与记忆队列新增 agent_turn_receipts。当前使用项目已有的 Hibernate schema 更新机制，正式环境需先备份并审查表结构变更。旧摘要字段保留兼容读取，停止自动生成压缩。
