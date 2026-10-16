"""Small delivery receipts, derived from actual projection results, never predictions."""


def projection_status(preview, projected):
    count = sum(p.get("applied") is True for p in projected)
    uncertain = any(p.get("previewError") for p in preview.get("proposals", []))
    uncertain |= any(p.get("action") in {"review", "replay"} for p in projected)
    if count:
        return {"status": "partial" if uncertain else "updated", "changedCount": count}
    if uncertain:
        return {"status": "unconfirmed" if any(
            p.get("previewError") for p in preview.get("proposals", [])
        ) else "review", "changedCount": 0}
    if any(p.get("action") == "keep_separate" for p in projected):
        return {"status": "review", "changedCount": 0}
    return {"status": "unchanged", "changedCount": 0,
            "retentionOptout": preview.get("reason") == "retention_optout"}
