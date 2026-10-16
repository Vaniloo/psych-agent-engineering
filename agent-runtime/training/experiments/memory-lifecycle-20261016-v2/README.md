# Memory边界专项第二轮

状态：训练与HTTP/网关旁路联调完成，只对1个专用合成账号启用v2候选，未替换正式Memory，事实写入关闭。第一轮候选及冻结Release不变；本轮从正式10月12日adapter开始，避免把第一轮回退继续累积。

## 数据与对照

`training.memory_lifecycle_refinement` 完整保留第一轮四个分区的原始字节前缀，追加不同事实共存、属性不同、施受关系、第三人称、引用、临时状态、明确更正等18类场景。新增100个事实族按40/20/20/20划分，归一化输入去重，挑战不进入任何课程分区。不同分区仍共享模板；标签是程序/开发标签，不能当作独立用户金标。

| 分区 | 原有保留 | 新增 | 合计 |
| --- | ---: | ---: | ---: |
| train | 5882 | 3255 | 9137 |
| calibration | 1832 | 1640 | 3472 |
| validation | 1852 | 1650 | 3502 |
| test | 1919 | 1725 | 3644 |

挑战保留第一轮原371条，另外增加24个新情境×5任务，共491条。新增120条在v2评分前冻结；但设计参考了v1错误，所以属于开发回归。没有真实聊天或教师API。

正式Memory在旧311条HTTP挑战上301条正确；v1候选在旧311条302条正确、原生命周期60条53条正确、新边界120条107条正确。实际接受/错误/弃权及权重SHA见两份 `*-http-baseline.json`。`evaluation-gates.json` 在v2结果可见之前记录比较门槛，仅用于本轮开发对照，不自动授权替换或写库。

## 复现

先按第一轮README构建第一轮课程，然后在 `agent-runtime` 下运行，输出目录须不存在且位于data盘：

```sh
python -m training.memory_lifecycle_refinement \
  --parent /data/experiments/memory-lifecycle-v1/course \
  --out /data/experiments/memory-lifecycle-v2/course
```

训练沿用第一轮命令与独立12任务契约，但数据/输出路径改为v2、`--model-name psych-memory-lifecycle-qwen4b-20261016-v2`、`--learning-rate 0.00002`，`--init-adapter`仍指向正式10月12日adapter。BF16 LoRA r16/alpha32、一轮、微批4×累积4；学习率和课程同时改变，因此不是只改变数据的单变量实验。所有源码、缓存、数据、日志、检查点位于lab的data盘，新模型不会自动部署。

`training-source-sha256.json`对应训练启动时的独立源码快照；随后旁路多候选比较和审计策略版本另有修改，不以当前全部Runtime源码冒充训练时快照。

HTTP对照工具 `benchmarks.memory_lifecycle_decisions` 校验挑战SHA和起止模型SHA，分任务调用有界batch；正式模型用 `--contract psych-memory-v4` 配旧311条挑战，候选用默认生命周期契约配491条挑战。密钥只从环境变量读取。

## 应用保护

旁路策略 `memory-lifecycle-shadow-v2` 比较预算内所有候选；任何弃权不被后来的无关系结果抹掉，多个相关目标转review，不选择第一条直接替代。一次预览中的控制器版本变化也转review。审计按来源、模型、策略版本幂等，保留比较依据，避免同模型的新策略覆盖旧审计。仍然没有调用apply或开放真实账号写入。


## 最终结果与接入决定

实际HTTP评分：第一轮生命周期53→57/60，新增边界107→112/120，新任务合计160→169/180；错误接受16→9。正式relevance为113/116，v2为112/116，未通过正式读取回归门槛。同一新旁路策略下的8场景动作从v1的5/8到v2的7/8，仍有一次错误的多目标替代建议，被程序转review。

`comparison.json`按口径整理对照，`v2-http-results.json`保留服务健康/权重SHA和逐任务汇总，`shadow-results.json`保留8场景原始合成决策，`live-integration-summary.json`记录消息保存→outbox→1条预览审计、0个活跃写入。`decision.json`逐项核对评分前门槛；training-report保留batch4原生结果，和HTTP batch16有2条低裕量选择差异，不相互覆盖。

572次优化，报告计时933.5秒包含优化后的校准/评测，峰值分配8720.3MiB。旧候选推理进程已关闭以释放显存；新候选仍与正式模型分目录、分端口，权重未作为正式Release发布。完整说明见 [第二轮报告](../../../../docs/memory-lifecycle-v2-20261016.md)。
