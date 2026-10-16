"""Opt-in current-fact view: source-verified spans plus untouched recent evidence.

The ledger never supplies generated text. Managed spans are removed from raw
copies, including superseded/reverted sources; active canonical spans are added
once. The raw outbox index is a rebuildable replica, not the source of truth.
"""

import hashlib

from .bm25 import BM25
from .memory_scope import merge_sources

READ_POLICY = "memory-current-facts-v1"


def select_candidates(records, query, limit=8):
    index = BM25([r["content"] for r in records])
    hits = index.search(query, limit)
    return [records[h.index] for h in hits] if hits else records[:min(4, limit)]


class MemoryReadView:
    def __init__(self, ledger, store, owner):
        self.ledger, self.owner = ledger, owner
        self.revision, self.sources = ledger.read_snapshot(owner)
        self.raw = store.source_rows(owner, {s["request_id"] for s in self.sources})
        self.masks, self.aliases, self.facts = {}, {}, []
        seen = set()
        for source in self.sources:
            record = self.raw.get(source["request_id"])
            start, end = source["span_start"], source["span_end"]
            if (
                record is None or record["sessionId"] != source["session_id"]
                or hashlib.sha256(record["content"].encode()).hexdigest() != source["source_hash"]
                or not 0 <= start < end <= len(record["content"])
            ):
                raise ValueError("missing or changed lifecycle source")
            self.masks.setdefault(source["request_id"], []).append((start, end))
            self.aliases[(record["sessionId"], record["content"])] = source["request_id"]
            if (source["item_status"] == "active" and source["status"] == "active"
                    and source["proposal_id"] == source["item_id"]):
                if record["content"][start:end] != source["content"]:
                    raise ValueError("canonical span does not match source")
                if source["item_id"] not in seen:
                    self.facts.append(self._span(record, start, end, source["item_id"]))
                    seen.add(source["item_id"])

    @staticmethod
    def _span(record, start, end, identity):
        while start < end and record["content"][start] in " ，,；;。\n":
            start += 1
        return {**record, "id": identity, "content": record["content"][start:end][:500],
                "truncated": end - start > 500}

    def assemble(self, records, *, exclude_session_id=None):
        selected = list(self.facts)
        for record in records:
            request = record["source"] if record["source"] in self.masks else self.aliases.get(
                (record["sessionId"], record["content"])
            )
            # Recent SQL messages use a different ID. A truncated copy may only
            # be masked after exact prefix + session matching to a verified source.
            if request is None and record.get("truncated"):
                matches = [key for (session, text), key in self.aliases.items()
                           if session == record["sessionId"] and text.startswith(record["content"])]
                if matches:
                    continue  # no partial managed copy may reintroduce a stale fact
            if request is None:
                selected.append(record)
                continue
            full = self.raw[request]
            end = 0
            for start, stop in sorted(set(self.masks[request])):
                if start > end and full["content"][end:start].strip(" ，,；;。\n"):
                    selected.append(self._span(full, end, start, f"{full['id']}:{end}:{start}"))
                end = max(end, stop)
            if end < len(full["content"]) and full["content"][end:].strip(" ，,；;。\n"):
                selected.append(self._span(full, end, len(full["content"]), f"{full['id']}:{end}"))
        return [r for r in merge_sources(selected)
                if exclude_session_id is None or r["sessionId"] != exclude_session_id]

    def unchanged(self):
        return self.ledger.revision(self.owner) == self.revision
