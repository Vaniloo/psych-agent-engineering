"""Shared versioned inputs for annotation and training; no model imports."""

import hashlib
import json
from pathlib import Path

from psych_agent_runtime.decision_contract import (
    MEMORY_EVIDENCE_CONTRACT,
    MEMORY_PAIR_CONTRACTS,
    TOOL_CHOICES,
    focused_memory_questions,
    memory_evidence_state,
    memory_pair_state,
    profile_contract,
)


def selected_questions(profile, state, tasks, contract=None):
    version, canonical = profile_contract(profile, contract)
    if not tasks or any(t not in canonical for t in tasks):
        raise ValueError("unsupported decision tasks")
    result = (
        focused_memory_questions(state, tasks)
        if profile == "memory" and version not in MEMORY_PAIR_CONTRACTS
        else {t: dict(canonical[t]) for t in tasks}
    )
    if profile == "executor" and "next_action" in result:
        allowed = state.get("allowedTools", [])
        if not isinstance(allowed, list) or any(
            t not in TOOL_CHOICES or t in {"finish", "clarify"} for t in allowed
        ):
            raise ValueError("invalid allowed tools")
        result["next_action"]["criteria"] = {
            t: TOOL_CHOICES[t] for t in [*dict.fromkeys(allowed), "finish", "clarify"]
        }
    if profile == "executor" and "goal_action" in result:
        candidates = state.get("candidates", [])
        if not 1 <= len(candidates) <= 8:
            raise ValueError("goal candidates required")
        result["goal_action"]["criteria"] = {
            "none": canonical["goal_action"]["criteria"]["none"],
            **{
                f"action_{i}": f"候选{i}：目标“{c['goalTitle']}”中的小行动“{c['actionTitle']}”"
                for i, c in enumerate(candidates)
            },
        }
    return result


def prepare_row(row, profile, allow_teacher=False, contract=None):
    contract, _ = profile_contract(profile, contract)
    if row.get("contractVersion") != contract:
        raise ValueError("contract mismatch")
    if row.get("reviewed") is not True and not (
        allow_teacher and row.get("annotationStatus") == "teacher_draft"
    ):
        raise ValueError("unreviewed labels; teacher drafts require explicit experimental mode")
    state, expected = row.get("state"), row.get("expected")
    if not isinstance(state, dict) or not isinstance(expected, dict):
        raise TypeError("invalid state/labels")
    if contract in MEMORY_PAIR_CONTRACTS and "relevance" in expected:
        state = memory_pair_state(state)
    if contract == MEMORY_EVIDENCE_CONTRACT and "evidence_sufficient" in expected:
        if set(expected) != {"evidence_sufficient"}:
            raise ValueError("sufficiency must be an independent decision")
        state = memory_evidence_state(state)
    questions = selected_questions(profile, state, list(expected), contract)
    if any(expected[t] not in q["criteria"] for t, q in questions.items()):
        raise ValueError("invalid or missing label")
    group = row.get("splitGroup") or row.get("conversationId")
    if not isinstance(group, str) or not group or group == "conversation-id-required":
        raise ValueError("conversation/scenario split group required")
    return {**row, "state": state, "questions": questions, "splitGroup": group}


def load_parts(root: Path, profile, allow_teacher=False, contract=None):
    parts, seen_groups, seen_inputs = {}, set(), set()
    for name in ("train", "calibration", "validation", "test"):
        rows = [
            prepare_row(json.loads(line), profile, allow_teacher, contract)
            for line in (root / f"{name}.jsonl").read_text().splitlines()
            if line.strip()
        ]
        if not rows:
            raise ValueError(f"empty {name} split")
        groups = {r["splitGroup"] for r in rows}
        inputs = {
            hashlib.sha256(
                json.dumps(r["state"], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            for r in rows
        }
        if groups & seen_groups or inputs & seen_inputs:
            raise ValueError(f"conversation or exact state leakage in {name}")
        seen_groups |= groups
        seen_inputs |= inputs
        parts[name] = rows
    return parts
