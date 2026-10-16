"""Source-backed bounded memory items and shadow audit, separate from raw history."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .memory_facts import target_verified


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def proposal(job, start, end, *, scope="temporary"):
    text = job["userMessage"]
    if (
        not isinstance(text, str)
        or type(start) is not int
        or type(end) is not int
        or not 0 <= start < end <= len(text)
        or end - start > 400
        or scope not in {"stable", "temporary"}
    ):
        raise ValueError("complete bounded source span required")
    return {
        "id": identity(job["username"], job["requestId"], start, end),
        "owner": job["username"],
        "requestId": job["requestId"],
        "sessionId": job["sessionId"],
        "start": start,
        "end": end,
        "sourceHash": hashlib.sha256(text.encode()).hexdigest(),
        "content": text[start:end],
        "scope": scope,
        "observedAt": job["createdAt"],
    }


def choose_action(decisions, has_existing=False):
    """Only accepted propositions authorize a suggestion; application still owns writes."""
    needed = {"write_worthy", "grounded"} | (
        {"duplicate", "supersedes", "conflict"} if has_existing else set()
    )
    if any(
        decisions.get(k, {}).get("accepted") is not True
        or decisions[k].get("choice") not in {"yes", "no"}
        for k in needed
    ):
        return "review"
    yes = lambda k: decisions[k]["choice"] == "yes"
    if not yes("write_worthy") or not yes("grounded"):
        return "skip"
    if not has_existing:
        return "add"
    # Inconsistent model outputs never silently replace a fact.
    if yes("duplicate") and (yes("supersedes") or yes("conflict")):
        return "review"
    if yes("duplicate"):
        return "support"
    if yes("supersedes"):
        return "replace"
    return "keep_separate" if yes("conflict") else "add"


class MemoryLedger:
    def __init__(self, path, *, shadow_limit=200, active_limit=200):
        if not 1 <= shadow_limit <= 2000 or not 1 <= active_limit <= 2000:
            raise ValueError("bounded ledger limits required")
        self.shadow_limit, self.active_limit = shadow_limit, active_limit
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, content TEXT NOT NULL,
                    scope TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
                    replacement_id TEXT, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS lifecycle_owner ON memory_items(owner,status);
                CREATE TABLE IF NOT EXISTS memory_sources (
                    owner TEXT NOT NULL, item_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, session_id INTEGER NOT NULL,
                    span_start INTEGER NOT NULL, span_end INTEGER NOT NULL, source_hash TEXT NOT NULL,
                    PRIMARY KEY(owner,proposal_id));
                CREATE TABLE IF NOT EXISTS memory_shadow (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                    proposal_json TEXT NOT NULL, decision_json TEXT NOT NULL,
                    target_id TEXT, action TEXT NOT NULL, observed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memory_changes (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, proposal_id TEXT NOT NULL,
                    action TEXT NOT NULL, item_id TEXT NOT NULL, target_id TEXT,
                    item_version INTEGER NOT NULL, target_version INTEGER,
                    state TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memory_versions (
                    owner TEXT PRIMARY KEY, revision INTEGER NOT NULL);
            """)
            db.execute("BEGIN IMMEDIATE")
            if "status" not in {r["name"] for r in db.execute("PRAGMA table_info(memory_sources)")}:
                db.execute(
                    "ALTER TABLE memory_sources ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
                )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def candidates(self, owner, limit=8):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM memory_items WHERE owner=? AND status='active' ORDER BY created_at DESC,id LIMIT ?",
                    (owner, min(200, max(0, limit))),
                )
            ]

    def get(self, owner, item_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM memory_items WHERE owner=? AND id=?", (owner, item_id)
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _revision(db, owner):
        row = db.execute("SELECT revision FROM memory_versions WHERE owner=?", (owner,)).fetchone()
        return identity(owner, row[0] if row else 0)

    @staticmethod
    def _advance(db, owner):
        db.execute(
            "INSERT INTO memory_versions VALUES (?,1) ON CONFLICT(owner) DO UPDATE SET revision=revision+1",
            (owner,),
        )

    def revision(self, owner):
        with self.connect() as db:
            return self._revision(db, owner)

    def read_snapshot(self, owner):
        """One bounded, consistent lifecycle view, including inactive source masks."""
        with self.connect() as db:
            db.execute("BEGIN")
            revision = self._revision(db, owner)
            rows = [dict(r) for r in db.execute(
                "SELECT s.*,i.content,i.scope,i.status AS item_status,i.version "
                "FROM memory_sources s JOIN memory_items i ON i.id=s.item_id AND i.owner=s.owner "
                "WHERE s.owner=? ORDER BY s.rowid DESC LIMIT 2001", (owner,),
            )]
            if len(rows) > 2000:
                raise ValueError("lifecycle read source budget exceeded")
            return revision, rows

    def record_shadow(
        self,
        item,
        decisions,
        target=None,
        *,
        model="unversioned",
        review_reason=None,
        comparisons=(),
        policy_version="memory-lifecycle-shadow-v1",
        ledger_revision=None,
    ):
        if target is not None and target["owner"] != item["owner"]:
            raise ValueError("cross-owner target")
        action = "review" if review_reason else choose_action(decisions, target is not None)
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO memory_shadow VALUES (?,?,?,?,?,?,?,?)",
                (
                    identity(item["id"], model)
                    if policy_version == "memory-lifecycle-shadow-v1"
                    else identity(item["id"], model, policy_version),
                    item["owner"],
                    item["requestId"],
                    json.dumps(item, ensure_ascii=False),
                    json.dumps(
                        {
                            "model": model,
                            "policyVersion": policy_version,
                            "ledgerRevision": ledger_revision,
                            "tasks": decisions,
                            "reviewReason": review_reason,
                            "comparisons": list(comparisons),
                        }
                    ),
                    target["id"] if target else None,
                    action,
                    item["observedAt"],
                ),
            )
            db.execute(
                "DELETE FROM memory_shadow WHERE owner=? AND id NOT IN "
                "(SELECT id FROM memory_shadow WHERE owner=? ORDER BY rowid DESC LIMIT ?)",
                (item["owner"], item["owner"], self.shadow_limit),
            )
        return action

    def audit(self, owner, proposal_id, model, policy_version):
        audit_id = (
            identity(proposal_id, model)
            if policy_version == "memory-lifecycle-shadow-v1"
            else identity(proposal_id, model, policy_version)
        )
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM memory_shadow WHERE owner=? AND id=?", (owner, audit_id)
            ).fetchone()
            return dict(row) if row else None

    def apply(
        self,
        item,
        decisions,
        *,
        target=None,
        expected_version=None,
        enabled=False,
        expected_revision=None,
    ):
        """Opt-in experimental mutation, never a public HTTP write endpoint."""
        if not enabled:
            raise PermissionError("lifecycle writes are disabled")
        action = choose_action(decisions, target is not None)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                expected_revision is not None
                and self._revision(db, item["owner"]) != expected_revision
            ):
                raise ValueError("stale ledger revision")
            previous = db.execute(
                "SELECT item_id,source_hash,status FROM memory_sources WHERE owner=? AND proposal_id=?",
                (item["owner"], item["id"]),
            ).fetchone()
            if previous:
                if previous["source_hash"] != item["sourceHash"]:
                    raise ValueError("source changed on replay")
                return {
                    "action": "replay",
                    "itemId": previous["item_id"],
                    "applied": False,
                    "sourceStatus": previous["status"],
                    "revision": self._revision(db, item["owner"]),
                }
            if target:
                old = db.execute(
                    "SELECT * FROM memory_items WHERE id=? AND owner=?",
                    (target["id"], item["owner"]),
                ).fetchone()
                if old is None or old["status"] != "active" or old["version"] != expected_version:
                    raise ValueError("missing, foreign or stale target")
                if action in {"support", "replace"} and (
                    item["scope"] != old["scope"] or item["scope"] != "stable"
                ):
                    return {"action": "keep_separate", "applied": False}
                if not target_verified(action, item["content"], old["content"]):
                    return {
                        "action": "review",
                        "applied": False,
                        "reason": "attribute_not_verified",
                    }
            if action in {"review", "skip", "keep_separate"}:
                return {"action": action, "applied": False}
            target_id = target["id"] if action == "support" else item["id"]
            if (
                action == "add"
                and db.execute(
                    "SELECT 1 FROM memory_items WHERE owner=? AND status='active' AND content=? AND scope=?",
                    (item["owner"], item["content"], item["scope"]),
                ).fetchone()
            ):
                return {"action": "review", "applied": False, "reason": "duplicate_requires_plan"}
            if (
                action == "add"
                and db.execute(
                    "SELECT count(*) FROM memory_items WHERE owner=? AND status='active'",
                    (item["owner"],),
                ).fetchone()[0]
                >= self.active_limit
            ):
                return {"action": "review", "applied": False, "reason": "active_budget_full"}
            if action != "support":
                db.execute(
                    "INSERT INTO memory_items VALUES (?,?,?,?,1,'active',NULL,?)",
                    (target_id, item["owner"], item["content"], item["scope"], item["observedAt"]),
                )
            if action == "replace":
                db.execute(
                    "UPDATE memory_items SET status='superseded',replacement_id=?,version=version+1 WHERE id=? AND owner=?",
                    (target_id, target["id"], item["owner"]),
                )
            elif action == "support":
                db.execute(
                    "UPDATE memory_items SET version=version+1 WHERE id=? AND owner=?",
                    (target_id, item["owner"]),
                )
            db.execute(
                "INSERT INTO memory_sources (owner,item_id,proposal_id,request_id,session_id,span_start,span_end,source_hash) VALUES (?,?,?,?,?,?,?,?)",
                (
                    item["owner"],
                    target_id,
                    item["id"],
                    item["requestId"],
                    item["sessionId"],
                    item["start"],
                    item["end"],
                    item["sourceHash"],
                ),
            )
            item_version = old["version"] + 1 if action == "support" else 1
            target_version = old["version"] + 1 if action == "replace" else None
            change_id = identity(item["owner"], item["id"], "change")
            db.execute(
                "INSERT INTO memory_changes VALUES (?,?,?,?,?,?,?,?,'applied',?)",
                (
                    change_id,
                    item["owner"],
                    item["id"],
                    action,
                    target_id,
                    target["id"] if action == "replace" else None,
                    item_version,
                    target_version,
                    item["observedAt"],
                ),
            )
            self._advance(db, item["owner"])
            return {
                "action": action,
                "applied": True,
                "itemId": target_id,
                "changeId": change_id,
                "revision": self._revision(db, item["owner"]),
            }

    def undo(self, owner, change_id, *, enabled=False):
        """Undo one unchanged experimental operation; later modifications require review."""
        if not enabled:
            raise PermissionError("lifecycle undo is disabled")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            change = db.execute(
                "SELECT * FROM memory_changes WHERE owner=? AND id=?", (owner, change_id)
            ).fetchone()
            if change is None:
                raise ValueError("missing or foreign change")
            if change["state"] == "reverted":
                return {"action": "undo_replay", "applied": False}
            current = db.execute(
                "SELECT * FROM memory_items WHERE owner=? AND id=?", (owner, change["item_id"])
            ).fetchone()
            if (
                current is None
                or current["status"] != "active"
                or current["version"] != change["item_version"]
            ):
                raise ValueError("later changes prevent undo")
            if change["action"] == "replace":
                old = db.execute(
                    "SELECT * FROM memory_items WHERE owner=? AND id=?",
                    (owner, change["target_id"]),
                ).fetchone()
                if (
                    old is None
                    or old["status"] != "superseded"
                    or old["version"] != change["target_version"]
                    or old["replacement_id"] != current["id"]
                ):
                    raise ValueError("replacement chain changed")
                db.execute(
                    "UPDATE memory_items SET status='active',replacement_id=NULL,version=version+1 WHERE owner=? AND id=?",
                    (owner, old["id"]),
                )
            db.execute(
                "UPDATE memory_items SET status=?,version=version+1 WHERE owner=? AND id=?",
                (
                    "active" if change["action"] == "support" else "reverted",
                    owner,
                    current["id"],
                ),
            )
            db.execute(
                "UPDATE memory_sources SET status='reverted' WHERE owner=? AND proposal_id=?",
                (owner, change["proposal_id"]),
            )
            db.execute(
                "UPDATE memory_changes SET state='reverted' WHERE owner=? AND id=?",
                (owner, change_id),
            )
            self._advance(db, owner)
            return {
                "action": "undo",
                "applied": True,
                "changeId": change_id,
                "revision": self._revision(db, owner),
            }
