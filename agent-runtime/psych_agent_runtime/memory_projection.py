"""Explicit-user experimental projection; formal chat retrieval never reads this ledger."""

import json

from .memory_facts import attribute_relation
from .memory_ledger import choose_action, proposal
from .memory_shadow import POLICY_VERSION


class MemoryProjection:
    def __init__(self, ledger, users):
        self.ledger, self.users = ledger, set(users)
        if "*" in self.users:
            raise ValueError("projection requires explicit users")

    def project(self, job, preview):
        if job["username"] not in self.users:
            return []
        if not preview.get("proposals"):
            return []
        if preview.get("policyVersion") != POLICY_VERSION:
            return [{"action": "review", "applied": False, "reason": "policy_mismatch"}]
        revision = preview.get("ledgerRevision")
        initial_revision = revision
        if not isinstance(revision, str):
            return [{"action": "review", "applied": False, "reason": "missing_revision"}]
        results, created = [], []
        for row in preview.get("proposals", [])[:2]:
            if (
                row.get("action") not in {"add", "support", "replace"}
                or row.get("reviewReason")
                or row.get("previewError")
            ):
                results.append({"action": row.get("action", "review"), "applied": False})
                continue
            try:
                span = row["sourceSpan"]
                item = proposal(job, span["start"], span["end"], scope=span["scope"])
                if (
                    item["id"] != row["proposalId"]
                    or not isinstance(row["model"], str)
                    or row["model"]
                    in {
                        "",
                        "unknown",
                        "unavailable",
                    }
                ):
                    raise ValueError("source identity mismatch")
                audit = self.ledger.audit(job["username"], item["id"], row["model"], POLICY_VERSION)
                if audit is None:
                    raise ValueError("missing audit")
                stored = json.loads(audit["decision_json"])
                if (
                    json.loads(audit["proposal_json"]) != item
                    or stored["tasks"] != row["decisions"]
                    or stored["ledgerRevision"] != initial_revision
                    or audit["action"] != row["action"]
                    or audit["target_id"] != row.get("targetId")
                    or stored["comparisons"] != row.get("comparisons", [])
                ):
                    raise ValueError("preview audit mismatch")
                target = None
                expected_version = None
                if row.get("targetId"):
                    target = self.ledger.get(job["username"], row["targetId"])
                    matches = [c for c in row["comparisons"] if c["targetId"] == row["targetId"]]
                    if (
                        target is None
                        or len(matches) != 1
                        or type(matches[0]["targetVersion"]) is not int
                    ):
                        raise ValueError("unbound target")
                    expected_version = matches[0]["targetVersion"]
                if choose_action(row["decisions"], target is not None) != row["action"]:
                    raise ValueError("action mismatch")
                # Two candidates planned against one snapshot cannot silently
                # establish new relations to each other within the same message.
                if any(attribute_relation(item["content"], old) != "different" for old in created):
                    results.append(
                        {"action": "review", "applied": False, "reason": "peer_requires_replan"}
                    )
                    continue
                result = self.ledger.apply(
                    item,
                    row["decisions"],
                    target=target,
                    expected_version=expected_version,
                    expected_revision=revision,
                    enabled=True,
                )
                revision = result.get("revision", revision)
                if result.get("applied") and result["action"] in {"add", "replace"}:
                    created.append(item["content"])
                results.append(result)
            except (KeyError, TypeError, ValueError):
                results.append(
                    {"action": "review", "applied": False, "reason": "invalid_or_stale_preview"}
                )
        return results
