# 官方公开数据下一动作试验：2026-10-19

本轮承接[来源审计](public-tool-data-20261018.md)，只用固定版本ToolACE；不调用DeepSeek、不混旧自造课程或APIGen-MT。课程构建与长度预检后先启动2,048条pilot，随后按用户要求完成全部合格数据的独立Qwen3-Reranker-4B LoRA单轮试验。完整结果见文末；没有替换正式模型。

## 监督契约

原始消息标明的下一步调用映射到当时目录中的工具；原始普通文字回复映射到`assistant_text`。这是对可观察事件的机械转换，不是生成新问题或推断“应当结束/澄清”的语义标签。文字回复可以是追问、说明、过渡、拒绝或回答，不代表整个任务完成。

保留共享候选评分：输入完整的当时工具描述、schema和过去对话，对每个候选取原始LM head的yes/no logits差。没有固定num_tools分类头，候选工具数量可变。本地Qwen实现不冒称官方JEV模型或其训练复现。

损失只用示范动作交叉熵；同一源调用块中多个不同工具的目标权重相等。未选工具在相对选择中竞争，但没有被赋予绝对不适用的标签。没有utility BCE、完整优劣排序、finish/clarify语义损失或接受阈值校准。因此示范一致率不能解释成工具绝对适用度准确率，或作为上线门槛。

模型输入不包含目标回复、目标调用参数、目标索引、分区或来源标签；只保留目标之前的消息与观察。数据中此前工具观察可以为后续调用提供事实，助手猜测不自动升级为外部事实。暂不训练参数生成器。

## 数据与隔离

| 项目 | 数量 |
| --- | ---: |
| 原始ToolACE对话 | 11,300 |
| 排除解析问题 | 519 |
| 相同目录+完整对话重复 | 3 |
| 保留对话 | 10,778 |
| 抽取决策点 | 13,297 |
| train / validation / test决策点 | 10,788 / 1,275 / 1,234 |
| 完整候选及文本预算内的有区分决策点 | 10,556 / 1,239 / 1,204 |
| 本次冻结选用 | 2,048 / 256 / 256 |

248个只有“回复文字”单一动作的记录不参与本次试训/主评测；另外46条train超候选批量token预算、4条train超单输入长度预算，不截断、不部分保留候选。预算为单候选4,096 tokens、同一状态padding后16,384 tokens。

原有审计的18条重复对话中，有些目录不同，可能体现工具可用性变化；这里只删除3条目录和对话同时一致的重复。不同目录的相同对话保留，并强制进入同一分区。

共享规范化工具名、规范化首问、完整对话hash构成连通组件，共3,658组，最大组5,834条对话。完整组件分区，任何同名工具、相同首问、同一对话或同一episode均不跨分区。没有宣称语义等价工具、所有后续用户短句或业务家族完全隔离。

本次hash抽样不生成新内容，选中train含1,533个调用、515个文字回复；validation为211/45，test为203/53。抽样与全部分区ID均在训练前冻结，没有根据测试成绩选样。

## 试训参数与边界

- 基座：`Qwen/Qwen3-Reranker-4B`，revision `22e683669bc0f0bd69640a1354a6d0aebcfeede5`，重新从冻结基座起步，不继承失败的V2 adapter。
- BF16 LoRA r16、alpha32、dropout0.05，注意力与MLP线性层；学习率2e-5、1轮、梯度累积8，计划256次更新。
- 启动前核验课程、预检、源码、renderer和基座权重；分配显存上限为单GPU的24%，保留其他进程余量，不停止或修改其他任务。
- 在同一冻结test子集先测基座、训练后再测候选，另测validation；不据测试集调参，也不自动替换服务。
- 29项本地相关测试通过；服务器额外验证了多目标交叉熵值及梯度。训练尚未结束，不能用训练状态推断有效提升。

## 服务器产物

```text
/home/ubuntu/data/psych-agent-routing-20261009/
  datasets/public-toolace-next-action-20261019-v1/
    manifest.json, train.jsonl, validation.jsonl, test.jsonl, excluded.jsonl
  runs/public-toolace-preflight-20261019-v1/
    preflight.json, selected.json, exclusions.json
  runs/public-toolace-qwen4b-20261019-pilot-v1/
    source/, job.json, training.log
    candidate/progress.json
    candidate/base_test.json
    candidate/training-report.json
    candidate/adapter/
    candidate/validation.json, test.json
```

末尾结果文件在对应阶段完成时才产生，不能仅凭路径列表认为已完成。状态以`progress.json`、`failure.json`、实际进程和训练报告共同判断。本地outputs同步课程清单、预检与启动信息；不复制完整数据到普通Git历史。

下一步根据真实的基座/候选比较检查：是否改善未见同名工具集合上的相对选择、文字回复样本是否仍被错触发工具，以及长上下文和候选顺序是否影响结果。即使本轮提升，也需额外检查停止/澄清、参数绑定和运行时兼容，不能直接替换完整Executor。

## 后续：完整单轮、7–8小时预算

用户要求睡前安排长训练，随后明确只训练1轮，控制在7–8小时。已启动独立后台worker，不继承pilot adapter，也没有覆盖或停止pilot进程。

完整任务使用全部10,556个长度与候选预算合格train、1,239个validation、1,204个test，从相同冻结基座开始，BF16 LoRA r16、alpha32、学习率2e-5、累积8、1轮，计划1,320次优化器更新。数据来源、组件隔离和模型输入契约保持一致，没有生成新问题或猜补语义标签。

整条流程上限28,200秒（7小时50分），包含全量预检、基座测试、训练、验证/测试和磁盘adapter重载核对。训练程序最多27,000秒，其中预留3,600秒给最终评测与保存，主训练达到预算后在更新边界停止；遇到实际负载变慢不能同时保证“完整一轮”与“严格时限”时，优先遵守时限，保留状态并标记实际完成数量。不会把部分完成描述为完整训练。

每128次优化器更新以及最终保存时写独立检查点，包含可恢复的LoRA、优化器、随机状态和游标，逐文件hash并以原子更名完成；保存不会覆盖已有检查点。CPU小模型测试已经验证adapter重载logits完全一致、优化器恢复、游标恢复、hash校验及拒绝覆盖；本地相关测试增至30项通过。

完整worker为本次子进程设置整体超时，只处理自己创建的进程组，不停止其他训练或服务。GPU选择排除仍在运行pilot使用的设备，要求至少40,000MiB空闲，并保留显存上限；所有源代码、缓存、临时文件与产物均位于data盘。

完整任务路径：

```text
/home/ubuntu/data/psych-agent-routing-20261009/runs/public-toolace-qwen4b-20261019-full-v1/
  source/, worker.py, job.json, worker-status.json
  preflight/, launch-plan.json
  candidate/progress.json, candidate/training-report.json
  candidate/checkpoints/, candidate/last-checkpoint.json, candidate/adapter/
  reload-verification.json
  aggregate-results.json, RESULT.md
```

这些文件按阶段产生。worker状态`preflight`不代表GPU已开始训练；最终`complete`要求全部训练状态与完整评测完成。时间预算导致的部分完成标为`completed_with_budget_limit`，异常标为`failed`并保留现有检查点。本次不自动追加训练、不调用教师、不替换正式模型。

## 完整单轮结果核对

实际worker及训练报告均为`complete`，全流程20,844.62秒（5小时47分25秒），优化阶段19,929.08秒（5小时32分9秒）。全部10,556个状态、1,320次优化器更新完成，`fullTrainingCompleted=true`、`budgetStopped=false`。完整validation 1,239条、test 1,204条均评完，没有用时间限额截取评测子集。

| 同一冻结test上的下一动作示范一致率 | 基座 | 单轮LoRA |
| --- | ---: | ---: |
| 总体 | 971/1,204（80.65%） | 1,124/1,204（93.36%） |
| 原文为工具调用 | 913/960（95.10%） | 932/960（97.08%） |
| 原文为文字回复 | 58/244（23.77%） | 192/244（78.69%） |

validation为1,177/1,239（94.996%）：工具分支995/1,021、文字分支182/218。相同测试集总体提升12.71个百分点，153个净增一致样本中134个来自文字分支；剩余80个不一致中，文字分支52个、工具分支28个。文字分支仍需重点检查，但不能把所有示范不一致直接当成绝对工具错误，原文示范不是所有可行动作的穷尽金标。

磁盘adapter重载核对16条既有验证样本，选择16/16相同，所有分数差为0。另独立重算三个评测文件逐条计数、完整训练数量及adapter SHA256，与报告一致，结果保存在服务器`result-audit.json`及本地outputs。峰值分配显存16,676.87MiB；平均训练损失0.33263，仅描述优化过程，不作为效果门槛。

权重位于完整任务的`candidate/adapter/`，文件`adapter_model.safetensors`为132,187,888字节，SHA256 `5c526403a928b637774d7e1304a49fa3979224dad3ce5e8437b37d1bdc4f04a7`。最终可恢复检查点为`candidate/checkpoints/step-001320-final/`。

正式服务保持原模型。下一步应先评估项目工具与真实loop回放，尤其是何时先回复文字、动态schema及参数绑定；这轮未测试运行时接入、真实端到端成功率或中文自然表达覆盖。当前不自动部署，也不按本次test成绩再调参或追加轮次。
