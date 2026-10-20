# Attune 账号模型 API 设置

入口在左下角账号菜单 →「模型 API」。可选择应用默认模型，或填写自定义接口的 Base URL、API Key、模型名；支持测试连接、保存和恢复默认。保存后下一轮对话生效，已经开始的回复使用该请求开始时取得的配置。

目前支持公开HTTPS、443端口的 Chat Completions 兼容服务，需要流式输出和JSON模式。Base URL可含 `/v1` 等服务前缀；粘贴完整 `/chat/completions` 地址时会去掉该后缀。连接测试只发送一条固定的简短请求，不带用户对话或个人记忆，也不自动保存配置。测试仍会消耗服务商的一次少量调用。

## 生效范围

设置应用于Runtime内本轮聊天的生成模型调用，包括回复、对话理解和Agentic RAG中的问题规划/证据判断。本地Router、Executor、Memory、向量嵌入保持原配置。Java侧的目标规划和报告生成继续使用应用配置；界面明确说明此范围，不将一次设置描述为全部模型都已切换。

## 数据与调用链路

```text
账号菜单 → 模型API弹窗 → JWT认证的 /model-api
  → 当前账号的SQL记录（API Key为AES-256-GCM密文）

下一轮聊天 → Runtime按用户名向内部认证接口读取配置
  → 请求范围内创建兼容客户端（ContextVar）
  → 图中的生成调用复用该客户端 → 请求结束关闭客户端、清理上下文
```

凭据不进入浏览器localStorage、公开AgentContext、LangGraph状态/检查点、SSE或运行指标。API Key输入框关闭时清空；读取配置只返回是否已有密钥。留空表示保留已存密钥，但更换接口地址必须重新填写，不能把旧密钥转发到新地址。加密附加认证数据绑定用户ID，复制数据库密文到另一账号不能解密。

自定义服务请求不跟随重定向，每次请求都解析并核对所有DNS结果，只连接公开IP，使用校验后的IP建立连接并保留原域名的TLS校验及Host。拒绝回环、内网、链路本地等目标；本版不是用于访问服务器内网推理端口的代理。

配置按请求隔离，不修改进程级默认客户端；并发用户互不覆盖。自定义API返回失败时不偷偷改用应用密钥重试。运行指标记录实际使用的模型与Token数；因为未知用户服务的价格，成本标为 `external_unpriced`，前端显示「由接口服务计费」，不套用DeepSeek价格。

## 接口

| 接口 | 权限和行为 |
| --- | --- |
| `GET /model-api` | JWT当前账号；返回配置元数据，永不返回密钥 |
| `PUT /model-api` | JWT当前账号；保存 `baseUrl/model/apiKey` |
| `POST /model-api/test` | JWT当前账号；测试提交的配置，空密钥可复用同地址的已存密钥，不保存 |
| `DELETE /model-api` | JWT当前账号；移除自定义配置，恢复应用默认 |
| `GET /internal/agent/model-api?username=...` | 独立服务令牌；仅用于Runtime取得请求范围内的凭据 |
| `POST /v1/model-api/test` | Runtime内部服务令牌；执行受限连接测试 |

配置响应使用 `Cache-Control: no-store`。接口错误返回标准化中文原因，不透传上游响应正文、请求头或密钥。

## 部署

后端新增 `user_model_api` 表，开发环境由Hibernate创建。关闭自动建表的MySQL部署可使用：

```sql
CREATE TABLE user_model_api (
  user_id BIGINT NOT NULL PRIMARY KEY,
  base_url VARCHAR(500) NOT NULL,
  model VARCHAR(160) NOT NULL,
  encrypted_key VARCHAR(8192) NOT NULL
);
```

后端需要 `MODEL_SETTINGS_ENCRYPTION_KEY`，值为32字节随机数据的Base64编码；保存在私有环境配置或密钥管理系统中。Docker Compose已传递该变量。必须在重启和迁移后保留同一密钥；丢失时需要用户重新填写配置，不能从密文恢复。未配置时应用默认聊天仍可用，自定义配置保存/测试关闭。当前本地开发环境已生成独立密钥，未写入仓库。

## 验证

- Python：1056通过、1跳过；包括不同账号并发客户端隔离、DNS私网拒绝、公开IP固定、密钥不进入序列化结果。
- Java：98项测试，0失败、0错误、1跳过；包括加密存储、账号绑定、跨地址密钥保护、读取不回显及恢复默认。
- 既有前端5个测试文件通过，脚本语法检查通过。
- 两个新建合成账号，经真实前端代理、Java、Runtime验证：未登录返回401；仅一个账号保存自定义配置，另一账号保持默认；测试连接成功；实际SSE回复使用自定义模型，运行指标为`external_unpriced`；恢复默认成功。未修改实际用户的模型设置。

本会话原始结果：`outputs/model-api-20261019/gateway.json`。只保留无凭据的测试结论，测试密钥不落入结果文件。
