# 测试报告

测试日期：2026-07-25

## 自动化回归

### Spring Boot

```bash
cd backend
mvn test
```

- 13 个测试：12 个通过，1 个外部依赖集成测试跳过
- 0 failures，0 errors
- 覆盖 JWT 失效处理、危机识别、工具参数校验、知识初始化降级、
  Runtime 合同和内部服务认证等路径

`PsychAgentApplicationTests` 依赖 MySQL、Redis、Chroma 和 Ollama，默认标记为
Disabled；本报告后面的实服务验收覆盖了这些依赖。

### Agent Runtime

```bash
cd agent-runtime
PYTHONPATH=. uv run ruff check .
PYTHONPATH=. uv run pytest -q
```

- Ruff：通过
- Pytest：9 个测试全部通过
- 覆盖安全扫描、中文 BM25、领域查询扩展、Token 预算、记忆上下文裁剪、
  Ollama 用量与成本、RAG 相关性计算

### 前端与部署配置

```bash
node --check frontend/app.js
node --check frontend/config.js
docker compose --env-file local.env config --quiet
```

三项均通过。

## 本地真实服务

本次没有使用 mock 模型或内存向量库：

- Ollama `0.32.0`：`qwen3.5:4b`、`nomic-embed-text`
- `qwen3.5:4b` 实例按 Runtime 配置以 `context_length=8192` 加载，
  与前端上下文预算一致
- MySQL `9.6.0`：`psych_agent`，本机 root 无密码
- Redis：Docker，负责 RAG Cache 和 LangGraph Checkpoint
- Chroma `1.0.0`：Docker，runtime collection 为
  `psych-agent-knowledge-v2`
- MCP Server：`http://127.0.0.1:8001/mcp`
- Agent Runtime：`http://127.0.0.1:8090`
- Spring Boot：`http://127.0.0.1:18080`
- Frontend：`http://127.0.0.1:15173`

健康检查确认 Chroma、Ollama、Runtime Graph 和 Spring Boot 均为 `UP`。

## 知识入库与 RAG 评测

真实入库链路：

`Spring/MySQL → Runtime 分块 → Ollama Embedding → Chroma Upsert → Redis 版本失效`

- MySQL 共 34 篇知识，其中内置心理 FAQ 30 篇、30 个分类
- Runtime Chroma collection 为 30 个有效 chunk
- Java legacy collection 与 Python runtime collection 已隔离，避免双写产生重复候选
- Redis `rag:runtime:version` 在重建索引后更新

离线数据集包含 21 条人工标注查询，评测强制绕过 Redis，测量冷检索：

| 指标 | 结果 |
| --- | ---: |
| Recall@1 | 0.9524 |
| Recall@3 | 1.0000 |
| Recall@5 | 1.0000 |
| Precision@5 | 0.2000 |
| Hit Rate@5 | 1.0000 |
| MRR | 0.9762 |
| nDCG@5 | 0.9824 |
| 平均冷检索延迟 | 44.58 ms |
| P95 冷检索延迟 | 56.47 ms |
| Cache Hit Rate | 0（评测策略为 bypass） |

补充的“改善睡眠”泛化样本中，`sleep` 文档排名第一，相关度 `0.9155`；
“拖延任务如何把第一步变小”样本中，`procrastination` 文档排名第一，
相关度 `0.8574`。

## 端到端 SSE 验收

最终请求通过浏览器和 API 两种方式验证：

`Frontend → Spring JWT/SSE → LangGraph → MCP → Chroma/Redis → Ollama → MySQL`

最终 API 请求结果：

- SSE 正常收到 `meta/status/node_metric/graph/plan/tool/context/token/memory/metrics/done`
- RAG Top-1：`sleep`，相关度 `0.9364`
- 总耗时：`7352.39 ms`
- TTFT：`4695.97 ms`
- 总 Token：`1019`
- 本轮 RAG：`686.04 ms`，语料规模 `30`
- MCP fallback：`0`
- 持久化状态：`saved`

浏览器实测确认：

- 登录、历史会话和流式 Token 输出正常
- 7 个 LangGraph 节点耗时实时更新
- 总耗时、TTFT、Token、吞吐、成本、RAG 延迟、缓存状态和上下文预算可见
- RAG 引用、分数、分类、候选数和来源可见
- 指标页可展示趋势、成功率、无降级率、最近运行和离线评测
- 前端“运行评测”操作可完成并刷新面板

## 实测中发现并修复的问题

- Spring Security 在 `SseEmitter` 的 ASYNC/ERROR redispatch 中丢失认证，导致流提前断开
- Java legacy 向量与 Python runtime chunk 共用 collection，造成重复召回
- MCP 独立进程中的检索指标未自动进入 Runtime ContextVar
- 中文泛化查询缺少领域查询扩展，导致“睡眠”意图被相关但非目标文档抢位
- 连续离线评测命中 Redis，产生失真的 2 ms P95
- 本机 SOCKS 代理污染 localhost 请求
- 中断的 Ollama 推理请求曾占住单并发队列；重启服务后恢复，4-token 探针约 3.14 秒

这些问题均已修复或形成明确的运行恢复方式，并完成回归。
