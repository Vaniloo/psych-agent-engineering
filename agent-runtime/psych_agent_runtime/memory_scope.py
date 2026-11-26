"""Timezone-aware observation scope; timestamps are never invented event dates."""

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
