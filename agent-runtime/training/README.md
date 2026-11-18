# 面试版训练入口

应用只使用 ToolACE 读取工具 Executor。基座为 Qwen3-Reranker-4B，数据准备、训练和重载验证集中在 public_tools 目录。其他研究源码用于复查冻结实验。部署和模型选择见[面试版说明](../../README.md)。

## 1. ToolACE公开示范训练

先读 [public_tools/README](public_tools/README.md)。按作者固定版本下载、校验、转换和分组切分；ToolACE为主线，APIGen-MT是独立非商业研究支线，不混入默认训练。

```bash
python -m training.public_tools.fetch --help
python -m training.public_tools.convert --help
python -m training.public_tools.course --help
python -m training.public_tools.train --help
python -m training.public_tools.verify_candidate --help
```

完整单轮示意：

```bash
python -m training.public_tools.train --preflight-only \
  --data /data/toolace-course --base /data/base/snapshots/REVISION \
  --out /data/toolace-preflight --max-train-rows 0 --eval-rows 0

python -m training.public_tools.train \
  --data /data/toolace-course --base /data/base/snapshots/REVISION \
  --base-report /data/base-report.json --preflight /data/toolace-preflight \
  --out /data/toolace-candidate --epochs 1 \
  --checkpoint-every-steps 128 --max-wall-seconds 27000 \
  --evaluation-reserve-seconds 3600
```

损失是作者示范下一动作的候选间CE；未选工具不等于绝对无用。文字动作可能回答、澄清或解释，不等于目标完成。完整训练10,556个预算合格状态、1,320次更新，流程约5小时47分；公开test示范一致率80.65%→93.36%，不等于项目任务完成率。见 [训练](../../docs/public-tool-next-action-20261019.md)、[项目回放](../../docs/public-tool-project-replay-20261019.md) 和 [接入](../../docs/public-tool-serving-20261019.md)。
