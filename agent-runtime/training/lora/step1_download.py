"""第 1 步：下载固定版本的 ToolACE 数据集。

ToolACE（Team-ACE/ToolACE，Apache-2.0）是一份公开的工具调用对话数据：每条记录有一段
system 提示（里面声明了可用工具）和一段多轮对话（用户、助手、工具返回交替出现）。
固定 revision 是为了让别人下载到的数据和训练时完全一样。

    python -m training.lora.step1_download --out work/raw
"""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

REPOSITORY = "Team-ACE/ToolACE"
REVISION = "6bda777c88d21e5a204703c1ee45597a8fa4f734"
DATA_FILE = "data.json"


def download(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    target = out / DATA_FILE
    url = f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{REVISION}/{DATA_FILE}"
    with urllib.request.urlopen(url, timeout=120) as response:
        target.write_bytes(response.read())
    records = json.loads(target.read_text())
    info = {"repository": REPOSITORY, "revision": REVISION, "records": len(records),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    (out / "source.json").write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps(info))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    download(parser.parse_args().out)
