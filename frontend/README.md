# Attune Frontend

原生 HTML/CSS/JavaScript 静态应用，无需 Node 构建。采用侧栏、单列对话和底部输入框的布局；保留 Attune 品牌与心理支持功能。

主要能力：

- JWT 登录、退出及 USER/ADMIN 权限视图
- 新对话欢迎页、起始话题、会话标题搜索与历史恢复
- 新对话随机显示简短问候；仅在初始化和开始新对话时更新，连续两次不重复
- POST SSE 流式回复；Enter 发送，Shift + Enter 换行，输入法组合输入不会误发送
- Markdown 标题、列表、表格、代码与引用；本地 Marked 解析后经 DOMPurify 白名单过滤
- 复制回复、自动滚动和回到最新消息；阅读旧消息时不强制滚动
- 停止接收回复并保留已收到的文字；这不保证服务器推理或记录保存也被取消
- 引用、处理过程与运行指标收纳在对话详情中
- 新对话回复上方显示实际工具调用：调用中、完成、失败、未更新和未确认；展开查看简短结果与耗时
- 新回复下方显示真实记忆处理状态：后台整理、近期索引就绪或待确认；点开查看说明
- 帮助资源；管理员保留记忆与知识运维
- 桌面侧栏折叠、手机抽屉、键盘焦点管理、深浅主题与减少动画偏好

后端规则限定工具权限，ToolACE Executor 可为候选读取工具评分。前端通过 SSE 展示本轮实际执行的工具和记忆处理状态。

工具活动使用 `tool_activity` SSE 事件，以 callId 配对开始和结束；最终 capabilityCalls 可补齐未收到的条目，不将规划事件当作已调用。活动跟随本次回复，断线或停止接收只标记结果未确认，不宣称服务器操作被取消。旧历史消息接口没有按消息关联的工具轨迹，因此恢复历史时不推测调用记录；完整已保存运行信息仍在运行指标页查看。

记忆提示由 `memory-status.js` 驱动。聊天确认保存后，以本轮requestId查询`GET /agent/memory-updates/{requestId}`；Spring先校验当前JWT账号的已保存receipt，再请求内部Runtime状态。后台在原话索引完成时记录状态，不能以outbox已确认代替索引完成。查询最多10次，处理超过45秒或网络结果未确认时提供只读“重新检查”；切换会话/账号后停止更新，状态接口禁止缓存。旧历史消息缺少requestId映射，因此不补造历史提示。

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
node --check tool-activity.js
node tests/tool-activity.test.cjs
node tests/memory-status.test.cjs
node --check config.js
node tests/chat-renderer.test.cjs
```

发布前以测试账户检查登录、历史切换、流式完成/异常/停止。分别检查桌面和手机布局，并用不可信 HTML/危险链接确认回复渲染仅保留静态文档元素。后端授权始终以服务端检查为准。
