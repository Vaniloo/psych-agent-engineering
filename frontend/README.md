# Psych Agent Frontend

原生 HTML/CSS/JavaScript 静态应用，无需 Node 构建。采用侧栏、单列对话和底部输入框的布局；保留 Psych Agent 品牌与心理支持功能。

主要能力：

- JWT 登录、退出及 USER/ADMIN 权限视图
- 新对话欢迎页、起始话题、会话标题搜索与历史恢复
- POST SSE 流式回复；Enter 发送，Shift + Enter 换行，输入法组合输入不会误发送
- Markdown 标题、列表、表格、代码与引用；本地 Marked 解析后经 DOMPurify 白名单过滤
- 复制回复、自动滚动和回到最新消息；阅读旧消息时不强制滚动
- 停止接收回复并保留已收到的文字；这不保证服务器推理或记录保存也被取消
- 引用、处理过程与运行指标收纳在对话详情中
- 目标与行动反馈、陪伴风格、个人报告、帮助资源；管理员保留记忆与知识运维
- 桌面侧栏折叠、手机抽屉、键盘焦点管理、深浅主题与减少动画偏好

三路 Laya 的权限判断、工具选择和记忆处理仍在后端运行。前端通过既有 SSE 契约展示结果，不在浏览器复制这些决策。

## 本地运行

在 `frontend` 目录执行：

```bash
python3 -m http.server 5173
```

访问 `http://localhost:5173`。默认 API 为 `http://localhost:8080`，可用 `?apiBaseUrl=http://localhost:8080` 指定。正式 Nginx 环境使用同源 `/api/` 反代 Spring，并关闭 buffering 以实时输出 SSE。

## 静态依赖

`vendor/` 固定保存 Marked 18.1.0 与 DOMPurify 3.4.16。无需 CDN 或联网加载脚本，Docker 镜像同时复制这些文件。`vendor/manifest.json` 记录来源与校验和，许可证随文件附带。

## 验证

```bash
node --check app.js
node --check chat-renderer.js
node --check config.js
```

发布前以测试账户检查登录、历史切换、流式完成/异常/停止、目标保存与风格切换。分别检查桌面和手机布局，并用不可信 HTML/危险链接确认回复渲染仅保留静态文档元素。后端授权始终以服务端检查为准。
