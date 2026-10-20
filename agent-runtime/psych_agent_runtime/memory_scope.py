"""Timezone-aware observation scope; timestamps are never invented event dates."""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def observation_time(record, legacy_zone="UTC"):
    try:
        value = datetime.fromisoformat(record["observedAt"])
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(legacy_zone))
        return value.astimezone(UTC)
    except (ValueError, TypeError, KeyError):
        return None


def time_scope(records, window, query_zone="Asia/Shanghai", legacy_zone="UTC", now=None):
    if window is None:
        return records, {}
    if window != "today":
        raise ValueError("unsupported memory time window")
    date = (now or datetime.now(UTC)).astimezone(ZoneInfo(query_zone)).date()
    selected = []
    for record in records:
        observed = observation_time(record, legacy_zone)
        if observed is None or observed.astimezone(ZoneInfo(query_zone)).date() != date:
            continue
        # Recorded today is not the same as happening today. Keep mixed sources
        # with an explicit today clause for the source model to judge.
        if (
            re.search(r"昨天|前天|上周|上个月|以前|曾经|明天|后天", record["content"])
            and "今天" not in record["content"]
        ):
            continue
        selected.append(record)
    selected.sort(key=lambda r: observation_time(r, legacy_zone), reverse=True)
    return selected, {
        "window": window,
        "date": date.isoformat(),
        "timezone": query_zone,
        "timestampRole": "observation_time",
        "filteredOut": len(records) - len(selected),
    }


def recency_key(record, legacy_zone="UTC"):
    return observation_time(record, legacy_zone) or datetime.min.replace(tzinfo=UTC)


def merge_sources(records, legacy_zone="UTC"):
    # An outbox record and its authoritative chat message are the same evidence.
    # Prefer the latest timestamp for repeated text in a session, without copying
    # business messages back into the derived SQLite index.
    ordered = sorted(
        records,
        key=lambda r: observation_time(r, legacy_zone) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    unique = {}
    for record in ordered:
        key = (record.get("sessionId"), record.get("role"), record["content"])
        if key not in unique:
            unique[key] = dict(record)
        elif (not unique[key].get("memoryTypes") and record.get("memoryTypes")
              and not record.get("truncated") and not unique[key].get("truncated")):
            # A recent authoritative message can precede index delivery. Only
            # transfer classification metadata between complete, identical text
            # in the same session; keep the preferred source and timestamp.
            unique[key]["memoryTypes"] = list(record["memoryTypes"])
    return list(unique.values())
