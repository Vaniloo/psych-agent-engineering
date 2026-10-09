"""Export annotation contracts for future independent fine-tuning; no model loading."""

import argparse
import json
from pathlib import Path

from psych_agent_runtime.decision_contract import PROFILES
from psych_agent_runtime.routing_service import questions_digest


def export(root: Path):
    for profile, (contract, questions) in PROFILES.items():
        target = root / profile
        target.mkdir(parents=True, exist_ok=True)
        payload = {
            "profile": profile,
            "contractVersion": contract,
            "questions": questions,
            "questionsSha256": questions_digest(questions),
        }
        (target / "questions.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        example = {
            "router": {"message": "", "history": [], "session_summary": ""},
            "executor": {
                "request": "",
                "workflow": "strategy",
                "allowedTools": ["get_goal_context", "search_knowledge"],
                "remaining_calls": 2,
                "observations": {},
            },
            "memory": {"observation": "", "query": "", "candidates": [], "evidence": []},
        }[profile]
        row = {
            "contractVersion": contract,
            "state": example,
            "expected": {task: None for task in questions},
            "annotationStatus": "pending",
            "splitGroup": "conversation-id-required",
        }
        (target / "annotation-template.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2)
        )
        manifest = {
            "profile": profile,
            "contractVersion": contract,
            "questionsSha256": payload["questionsSha256"],
            "weightsSha256": "REPLACE_AFTER_TRAINING",
            "model": "UNTRAINED",
            "amp": "fp16",
            "maxStateTokens": 256,
            "tasks": {
                task: {
                    "enabled": False,
                    "calibrated": False,
                    "temperature": 1.0,
                    "threshold": 1.0,
                    "validationScope": "unvalidated",
                }
                for task in questions
            },
        }
        (target / "deployment.template.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2)
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    export(parser.parse_args().out)
