# 实验记忆读取诊断 · 2026-10-16

- `comparison-results.json`：8轮自编合成历史经真实模型建账本，13项同预算证据选择对照，包含失败项；不是独立留出集或向量检索对照。
- `gateway-summary.json`：新的专用合成账号8轮独立会话，实际outbox写入和实验读取摘要。
- `model-health.json`：正式相关性与实验生命周期模型身份；两者均沿用既有权重。
- `source-sha256.json`：本次关键源码与测试指纹。

复现时从`agent-runtime`运行，密钥只通过环境变量提供，输出目录必须不存在：

```sh
python -m benchmarks.memory_read_compare \
  --url http://127.0.0.1:18096 --key-env MEMORY_API_KEY \
  --lifecycle-url http://127.0.0.1:18101 \
  --lifecycle-key-env MEMORY_LIFECYCLE_SHADOW_KEY --out /path/to/new-diagnostic
```

只创建该新目录里的合成SQLite数据，不调用生成/教师API，不写业务账号或修改线上模型。详见[第四阶段报告](../../../docs/memory-read-v1-20261016.md)。
