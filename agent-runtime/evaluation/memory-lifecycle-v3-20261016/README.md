# 记忆生命周期应用策略v3验证

本目录是工程验证产物，不是第三轮训练。模型仍为4B v2，正式Memory不变。完整边界见 [阶段报告](../../../docs/memory-lifecycle-projection-20261016.md)。

- `model-health.json`：实际模型契约、任务与权重SHA。
- `shadow-results.json`：8项只读合成场景，8/8动作符合预期。
- `projection-results.json`：新建独立SQLite账本内的10项连续合成诊断，10/10符合动作/实验写入数预期；实际6次实验变更及持久化撤销检查。
- `gateway-summary.json`：新建专用合成账号的5轮网关联调、5次实验变更、3个活跃实验事实，以及跨会话当天心情和撤销核对；不含账号凭据或原始业务数据库。
- `source-sha256.json`：最终验证涉及的源码与测试校验值。

在 `agent-runtime` 下、配置好私有服务密钥环境变量后运行。输出必须是不存在的新目录；以下诊断只写输出目录下新建的实验数据库，不修改正式记忆：

```sh
python -m benchmarks.memory_lifecycle_shadow \
  --url http://127.0.0.1:18101 \
  --key-env MEMORY_LIFECYCLE_SHADOW_KEY \
  --out /data/experiments/memory-lifecycle-v3/shadow

python -m benchmarks.memory_projection \
  --url http://127.0.0.1:18101 \
  --key-env MEMORY_LIFECYCLE_SHADOW_KEY \
  --out /data/experiments/memory-lifecycle-v3/projection
```

应用配置默认只读或关闭；只有显式 `MEMORY_LIFECYCLE_SANDBOX_USERS` 子集才允许向独立实验账本投影。不要把该账本路径设置成原话索引路径。未提供对真实账号开放的写入或撤销HTTP端点。

结果为作者编写的少量开发场景，未知属性仍会转复核；不等于真实用户准确率、临床验证、并发能力或完整Jev-Mem复现。
