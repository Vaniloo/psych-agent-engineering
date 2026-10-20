# ToolACE 4B 本地接入（2026-10-19）

按用户确认，已将本地开发服务的**读取工具选择**切换为完整单轮 ToolACE Qwen 4B。Router、Qwen 目标判断、Qwen Memory、0.6B 重排及回复生成保持原配置；原 Laya 读取服务保留为失败回退。此变更没有更新 GitHub Release，也没有启动新训练。

## 接口与边界

`public_tool_client.py` 将本轮授权的真实工具描述与参数 schema、最近最多六条有界对话和已有读取结果组成动态候选。身份、会话与请求签名由服务器绑定，不作为缺失的用户参数交给模型判断。评分使用训练时原样的 renderer、共享 LM head 的 yes/no logit 差，没有固定工具分类头。

契约为 `public-tool-next-action-v1`。模型在授权读取候选与 `assistant_text` 中排序；文字动作仅结束当前读取子循环，交给回复生成器。它不是“目标完成”或已训练的“澄清”标签，不参与目标写入判断。必读请求在模型前执行；目标创建、更新、完成继续使用原专用模型及写入回执保护。

这是未经项目概率校准的受限 argmax 策略，接口明确返回 `calibrated=false`，不把候选间相对分数称为绝对适用性概率。上一轮原始个人记忆漏选等弱点仍存在，本次依靠既有读取保障控制影响。

## 配置与回退

`psych_agent_runtime.public_tool_service` 是独立、仅评分的 GPU 服务。启动核对训练报告、renderer、基座权重和 adapter 校验和；监听 lab 回环端口，经现有 SSH 连接接入本机。服务器源码、日志及缓存均在 data 盘。

- adapter SHA256：`5c526403a928b637774d7e1304a49fa3979224dad3ce5e8437b37d1bdc4f04a7`。
- `EXECUTOR_PROVIDER=public_qwen`，`EXECUTOR_BASE_URL` 指向新服务，`EXECUTOR_ADAPTER_SHA256` 固定此权重；API key 仅存私有环境文件。
- 必须显式设置独立的 `EXECUTOR_GOAL_PROVIDER`；不允许目标任务继承读取模型。本地仍用原 Qwen 目标服务。
- `EXECUTOR_FALLBACK_BASE_URL` / `EXECUTOR_FALLBACK_API_KEY` 指向原 Laya。新服务超时、繁忙、契约/权重/分数对齐异常时回到原 Laya；再失败则走原有权限内默认次序。
- 每次最多 17 个动作、4096 token。超过预算拒绝并回退，不静默截断。一个 GPU 推理槽，繁忙返回 429，无无界推理队列；这是本地试用配置，不是多用户容量承诺。

其他机器可在独立 GPU 环境设置 `PUBLIC_TOOL_API_KEY`，运行 `python -m psych_agent_runtime.public_tool_service --base <冻结基座目录> --checkpoint <含训练报告及adapter的目录> --port <端口>`；需要仓库 `training/public_tools` 中的冻结 renderer 与模型依赖。主 Agent 进程不需要 torch。

## 验证与当前状态

278 项 Python 回归、前端五个测试文件通过。运行指标页面可显示新 ToolACE 模型与选择来源。

上一轮冻结的 12 个 graph 场景使用**真实新 4B 服务、正式读取客户端与控制器**重放，12/12 通过，其中实际有 5 次评分决策。路由、工具返回和回复生成仍为 fixture，写入被拦截；这不是回答质量或独立泛化准确率。另确认真实新模型直接评分可用；两条同时进入时分别使用新模型和 Laya，实际断连时也成功回到原 Laya。该探针不是容量压测。

本地默认配置已更新并刷新 Runtime，保存了权限为 0600 的旧配置以便恢复。新 Executor 已就绪，Router、Memory 和目标模型与切换前一致。既有测试账号经前端代理登录，Java 后端转发的运行清单已显示新模型；前端、Java 健康检查和 Runtime 健康检查均返回 200。

本会话 `outputs/public-tool-switch-20261019/` 保留真实 graph 轨迹、回退探针、回归日志、部署/激活清单和后端冒烟检查，不含私有 key 或登录 token。原始训练与上一轮评测产物保持冻结。
