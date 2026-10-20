# 官方工具数据导入

下载固定版本、保留原始记录并做机械格式转换，不生成新问题、新工具描述、反事实工具集、负例、排序或finish/clarify标签。现用ToolACE单轮已完成并接入；训练与服务共用Runtime内的输入契约。旧toolgen v1/v2未晋级，已从当前冻结归档，不再提供那些入口或混入其数据。现用路线见[训练导航](../README.md)。

| 来源 | 原始对话数 | 版本 | 许可与用途 |
| --- | ---: | --- | --- |
| [ToolACE](https://huggingface.co/datasets/Team-ACE/ToolACE) | 11,300 | `6bda777c88d21e5a204703c1ee45597a8fa4f734` | Apache-2.0，主来源 |
| [APIGen-MT-5k](https://huggingface.co/datasets/Salesforce/APIGen-MT-5k) | 5,000 | `abc4a517d67c541f85f6470cbd8fd3186b36830e` | CC-BY-NC-4.0，单独的非商业研究支线 |

公开数据也包含模型合成内容，不是人工金标或无错误保证。下载两者不代表自动混合训练，更不能据此宣称产物均可商用。xLAM-60k有访问门槛，本轮未下载；BFCL等评测数据不进入训练。

## 复现

Python 3.12+，标准库即可；在 `agent-runtime` 目录运行。`PUBLIC_DATA_ROOT` 必须指向 data 盘上尚不存在的目录：

```sh
export PUBLIC_DATA_ROOT=/home/ubuntu/data/psych-agent-routing-20261009/datasets/public-tools-official-20261018
python -m training.public_tools.fetch --out "$PUBLIC_DATA_ROOT"
python -m training.public_tools.fetch --out "$PUBLIC_DATA_ROOT" --verify-only
python -m training.public_tools.convert --raw "$PUBLIC_DATA_ROOT" --out "$PUBLIC_DATA_ROOT/interchange-v3"
python -m training.public_tools.verify_interchange --raw "$PUBLIC_DATA_ROOT" --converted "$PUBLIC_DATA_ROOT/interchange-v3"
```

已有下载只执行校验，不重复下载。已有转换目录不覆盖；调整解析器后写新目录。原始文件设为只读，清单保存仓库、revision、许可证、URL、大小和SHA256。

## 转换与质量边界

- 每行保留完整 `original`、原始消息内容和来源索引/hash。`events`只增加机械解析视图，不执行任何原始指令或函数。
- ToolACE支持原生JSON工具目录与方括号调用；允许参数名为`from`、`media-type`、`$top`。只解析字面量，不使用`eval`。其他目录格式保留为`tools=null`并标记，不能当成空目录。
- 真正的空目录保留`tools=[]`；普通回复的`finishOrClarifyLabel=null`，不猜测语义类别。
- APIGen的参数若为JSON字符串，仅解码一层并记录转换标记。调用未声明的工具保留为`unresolvedCalls`，不补造schema。
- 被选择的工具来自原始调用；未选候选的适用性仍是未知，不能自动成为负样本。`think`是原始数据中的虚拟动作，不注册为在线工具，也不作为用户可见推理输出。
- `strictCallParsingPassed`仅代表目录与调用可解析，不代表schema参数已验证、执行正确、语义正确或适合训练。重复记录先统计，后续切分需去重并隔离工具/来源，不能宣称已完成未见工具评测划分。

完整来源核验会重算原始及转换文件hash、逐条比对原始记录与消息、检查来源元数据和索引覆盖。`verification.json`只在核验成功时写入；`audit.json`记录格式问题及统计。导入不会启动训练、调用教师或替换正式权重。

当前不是旧V2训练课程。旧训练器需要完整排序、绝对适用度和模式标签；公开数据缺少这些标签，须先设计部分监督或原生轨迹训练接口。不得为凑旧格式重新自行编造监督。

## 独立的公开下一动作课程（2026-10-19）

新入口`training.public_tools.course`与`training.public_tools.train`使用`public-tool-next-action-v1`契约，不调用旧V2训练器。它们只学习原文下一步实际出现的动作，普通文字对应`assistant_text`，绝不改称`finish`。同一调用块内多个不同工具均保留为监督目标。原文目标回复及目标调用参数不会进入该状态的模型输入；此前真实观察则可作为后续调用依据。

采用原始LM head的yes/no logits差，为每个候选共享评分；候选由当时工具描述/schema与一个“回复文字”动作组成。训练损失为原文动作目标的均匀交叉熵。未选工具只参与相对示范选择的竞争，不产生绝对no标签；没有utility BCE、语义finish/clarify分类或完整优劣排序损失。评测是示范一致率，不是全工具适用性准确率、真实任务完成率或上线通过结论。

源记录先排除解析问题，按“工具目录+对话”精确去重。相同规范化工具名、相同首问、完整对话hash通过连通组件一起划分，保证这些键不跨train/validation/test；不宣称语义家族隔离。空工具目录的文字回复保留在课程中，但不计入有区分意义的试训/主评测。

```sh
python -m training.public_tools.course --raw "$PUBLIC_DATA_ROOT" \
  --converted "$PUBLIC_DATA_ROOT/interchange-v3" --source toolace --out /data/toolace-course
python -m training.public_tools.train --preflight-only --data /data/toolace-course \
  --base /data/base/snapshots/REVISION --out /data/toolace-preflight
python -m training.public_tools.train --data /data/toolace-course \
  --base /data/base/snapshots/REVISION --base-report /data/base-report.json \
  --preflight /data/toolace-preflight --out /data/toolace-candidate --epochs 1
```

预检额外需要`transformers`，训练需要已验证环境中的PyTorch/PEFT与CUDA。`--base-report`沿用已有基座下载报告，核验revision及所有权重hash；不临时联网下载模型。预检不截断文本、不裁掉候选；整条输入或候选批量超预算时排除并记录。默认hash抽样2,048个train、各256个validation/test；`--max-train-rows 0 --eval-rows 0`才选择全部预算内数据，不自动扩量。

训练只接受ToolACE主来源；APIGen研究课程不会被该试训入口默认接受。试训有独立源码快照、预检hash、基座hash和课程hash；GPU不足直接失败，不干预其他进程。候选权重独立落盘，现有停止、澄清、权限与执行机制不改变。试验完成后仍需进一步评测和接入适配，不自动发布。

完整单轮可在预检时设置`--max-train-rows 0 --eval-rows 0`。长训练额外使用`--checkpoint-every-steps 128 --max-wall-seconds 27000 --evaluation-reserve-seconds 3600`。时间预算从训练程序调用开始计算，先为评测和最终保存留1小时；达到训练预算后，在优化器更新边界停止、保存权重并标记是否完成全部状态。评测若耗尽整体预算，会记录实际覆盖数，不能当作完整评测。上层worker另为预检和重载核对设置7小时50分全流程期限。

检查点按临时目录写入后原子更名，包含adapter、tokenizer、优化器、CPU/CUDA随机状态、训练游标、同源身份和文件hash；已存在目录不覆盖。`--resume-from /data/prior-candidate/checkpoints/step-NNNNNN`可在新的输出目录恢复，要求相同课程、预检、源码、基座及训练超参数。长任务不会无限重试或自动追加轮次。
