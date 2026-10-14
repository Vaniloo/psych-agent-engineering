"""Fetch the exact public Qwen base and verify it against the released training report."""

import argparse
import json
from pathlib import Path

from .fetch_artifacts import digest


def prepare(root, existing=None):
    report = json.loads(
        (root / "models/qwen-router-4b-lora-20261012/training-report.json").read_text()
    )
    revision = report["baseRevision"]
    base = existing or root / "base" / revision
    if base.name != revision:
        raise ValueError("base directory name must be the frozen revision")
    if existing is None:
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=report["baseModel"], revision=revision, local_dir=base)
    for name, expected in report["baseWeightsSha256"].items():
        if digest(base / name) != expected:
            raise ValueError("public base weight checksum mismatch")
    output = root / "base-report.json"
    output.write_text(json.dumps({
        "model": report["baseModel"], "revision": revision,
        "weightsSha256": report["baseWeightsSha256"],
    }, indent=2) + "\n")
    print(f"Verified base: {base}\nBase report: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent / "artifacts/20261014")
    parser.add_argument("--existing", type=Path, help="Use an existing frozen-revision snapshot")
    args = parser.parse_args()
    prepare(args.root, args.existing)
