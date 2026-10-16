# Memory边界专项第二轮

状态：训练进行中，未接入正式Memory。第一轮候选及冻结Release不变；本轮从正式10月12日adapter开始，避免把第一轮回退继续累积。

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
