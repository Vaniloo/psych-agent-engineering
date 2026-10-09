# Psych Agent Frontend

零依赖静态应用，使用原生 HTML/CSS/JavaScript。

主要能力：

- JWT 登录与 USER/ADMIN 视图
- `fetch + ReadableStream` 消费 POST SSE
- token 增量输出
- LangGraph 执行时间线
- 授权工具、上下文 token、后台记忆状态
- RAG citation 与分数展示
- 会话、角色卡、报告、知识运维和工具调试

本地运行：

```bash
python3 -m http.server 5173
```

访问 `http://localhost:5173`。默认 API 为 `http://localhost:8080`。

Nginx 部署时 `/api/` 反代 Spring，并关闭代理 buffering，避免 SSE token 被批量缓存后才显示。
