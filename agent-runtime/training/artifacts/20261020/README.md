# 2026-10-20 adapter supplement

Pinned adapters for the Attune freeze. Download with `python -m training.fetch_artifacts --root training/artifacts/20261020` from agent-runtime. Existing Router, base Memory, legacy goal decisions and Laya fallback remain in the models-20261014 Release.

The three binary adapters are Release assets, never ordinary Git objects. The manifest pins archive/member SHA256 and byte sizes. Original reports and adapter configurations are included. Base model revision: `22e683669bc0f0bd69640a1354a6d0aebcfeede5`; obtain original base weights from Qwen and validate the per-file hashes in the reports. Original 0.6B rerankers are not redistributed.

ToolACE: publisher demonstrations, not absolute utility gold or production success. Goal gate and memory lifecycle: programmatic/development curricula, not independent real-user labels. Goal writes still require application checks; no clinical claims. See repository docs/freeze-20261020.md and training/README.md.
