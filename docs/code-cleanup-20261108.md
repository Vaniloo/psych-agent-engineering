# 最新学习版：源码精简与完整性核对

分支：`refactor/study-core-20261108`。以 `freeze-20261020-attune` 为起点，上传完整应用、现用及回退训练代码、学习文档和最近的独立研究实验。冻结标签仍还原原版本，当前学习以本分支为准。

## 这次精简了什么

- `graph.py`：构造时明确初始化依赖；删除依赖存在性反射和临时重建 Controller 的回退。普通调用与 SSE 共用请求上下文、错误计数和清理逻辑。模型拒判和外部服务失败的业务回退继续存在。
- `mcp_result.py`：统一解码受支持 MCP SDK 的结构化/文本结果。内置工具和动态工具复用，删除猜测 snake_case/camelCase 字段及一层转发函数。空结构化对象保留优先级，动态结果仍有大小限制。
- `train_decisions.py`、`train_qwen_memory.py`：参数默认值由 argparse 唯一定义，删除各处 getattr 再定义默认值。外部数据、数值有效性、权重身份和契约检查继续保留。
- `public_tools/fetch.py`：集中流式 SHA-256 读取，课程、检查点和重载评测复用；避免重载验证把整份权重读入内存。下载、格式清洗、去重与分区隔离没有删减。
- `benchmarks/runtime_fixture.py`：把测试/离线回放中缺省依赖放在显式替身构造器，业务代码不再兼容只初始化一半的对象。原断言和场景保留。
- 文档：补齐交接入口、逐文件学习路径、现用训练地图和简历成稿；纠正面试指南中已过时的主工具模型说明。

这次不是重新训练或全栈重新部署；运行配置、训练 renderer、模型契约与权重不因清理自动改变。旧训练报告的源码指纹保持原值，恢复旧优化器检查点须使用对应旧源码。

## GitHub 应包含的内容

| 部分 | 源码 / 说明 | 实际交付方式 |
| --- | --- | --- |
| 应用 | frontend、backend、agent-runtime/psych_agent_runtime、contracts、启动脚本 | Git 本分支 |
| 动态工具训练 | public_tools 的 fetch、convert、course、train、verify_candidate、serving 等 | Git；ToolACE 按固定来源下载重建，不重新自造默认训练集 |
| Router / Memory / 专项门控 | train_qwen_choices、train_qwen_memory、数据构建、校准、挑战、评测 | Git；所需开发课程和来源元数据按现有目录保留 |
| Laya 回退 | train_decisions、unrounded_laya、领域课程与校准 | Git；继续作为真实兼容/回退依赖保留 |
| 模型参数 | 两份 artifact manifest、下载、校验和恢复入口 | GitHub Release 附件；基础模型按作者固定 revision 获取 |
| 独立 WSP 研究 | weight_space、activation_diagnostics、head_span_diagnostics、测试和结果元数据 | Git；激活缓存/候选二进制为实验产物，不作为正式模型 |
| 运行数据与凭据 | 私人对话、真实账号、API Key、服务器临时文件、缓存 | 不属于源码分发，不能写进 Git |

“完整”指支持当前架构、训练/评测和权重恢复的源码及公开来源步骤齐全。克隆仓库仍需安装依赖、获取获准数据/基础模型、配置自己的服务凭据；不承诺离线克隆即能 GPU 训练。

## 可复查的检查

在仓库根目录：

```bash
python scripts/audit-source-coverage.py
cd agent-runtime
uv sync --extra dev
uv run pytest -q
cd ../backend
./mvnw test -q
cd ..
node --test frontend/tests/*.test.cjs
```

完整性脚本核对应用/训练/测试/基准/脚本是否进入 Git 索引、Python 语法、项目内模块路径、当前入口文档链接和两份权重 manifest；不使用 GPU，也不访问私人服务。输出见 [source-coverage-20261108.json](source-coverage-20261108.json)。归档的旧实验报告按历史保留，不把其中退出维护的命令视为最新入口。

最终检查结果：

- Python 完整套件 **1015 通过**，包含新结果解码边界测试；1 条既有 Starlette/httpx 弃用提示。
- Java **99 项，0 失败、0 错误、1 跳过**；前端 **5 个测试文件通过**。
- **466 个源码/脚本文件**（246 个 Python，其中 44 个训练 Python）已进入 Git 索引；缺失项目模块、语法错误、当前入口断链均为 0。
- **12 个训练/研究 CLI 的 --help** 正常；所有启动脚本通过语法检查；两个 artifact 目录的非权重数据/报告逐文件大小及 SHA-256 校验通过。
- 两个 GitHub Release 的 **7 份模型附件**均存在，大小与平台提供的 SHA-256 匹配冻结 manifest；核对记录见 [Release 附件清单](source-release-assets-20261108.json)。本轮没有重复下载约 1GB 权重。
- 待上传变更中未发现实际 API Key 或私钥格式。私人运行目录与候选二进制没有加入 Git。

历史真实模型效果来自各日期报告，本轮不重新宣称 GPU 训练或生产并发已验收。
