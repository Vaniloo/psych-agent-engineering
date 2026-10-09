# 三 Laya 架构重构验证

本次验证对象是控制器、工具、业务持久化与部署配置；没有启动新模型训练，也没有把模拟模型响应当成真实预测效果。

| 验证 | 结果 | 实际覆盖 |
| --- | --- | --- |
| Python pytest | 50 passed | 分层风险升级、权限集合、两次调用上限、强制支持资源、模型契约、温度校准、截断拒绝、鉴权、记忆用户隔离与幂等索引、真实工具/HTTP 适配链路 |
| Java Maven | 18 passed，1 skipped | JWT、风险/危机业务、目标归属与版本、重复请求/冲突、真实 H2 消息/outbox 与目标反馈持久化、响应丢失不追加消息 |
| Python lint | passed | Runtime、测试和训练准备脚本 |
| JS syntax | passed | app.js、config.js |
| shell syntax | passed | start-laya.sh |
| Compose config | passed | 基础配置及三 worker 覆盖配置；验证用占位 key |
| 导出训练契约 | passed | 三个问题定义、待标注样板、禁用部署模板；未加载模型 |
| 前端浏览器检查 | passed | 模拟 API 下登录、目标创建、行动状态更新、反馈保存与回显；不等于真实全栈验收 |

Python 有一条依赖层 Starlette TestClient/httpx 弃用提示，无失败。跳过项为原有 Spring 完整应用上下文测试，需要真实外部服务。

尚未验证完整 Docker 构建、MySQL/Redis/Chroma/Ollama 联调、真实三个新模型权重、线上心理场景安全与质量。Jev-Mem 完整四视图检索、历史回填/索引恢复、用户记忆删除入口、失败队列、Laya 独立用量指标和会话并发排序留作下一阶段，具体边界见 local-laya-agent.md。
