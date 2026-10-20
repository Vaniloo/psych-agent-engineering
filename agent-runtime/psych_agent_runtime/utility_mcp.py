"""Optional local MCP reads for exercising discovery with varied real schemas."""

import argparse
import math
from datetime import UTC, date, datetime
from statistics import fmean, median
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field


def calendar_day_difference(start_date: str, end_date: str) -> dict:
    """Calculate the signed number of calendar days between two explicit dates
    (日期间隔/相隔多少天). Dates must be YYYY-MM-DD with a known year. Excludes
    the starting day; identical dates give zero. Not a business-day calculator.
    """
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if start.isoformat() != start_date or end.isoformat() != end_date:
        raise ValueError("dates must use YYYY-MM-DD")
    return {
        "available": True,
        "start_date": start_date,
        "end_date": end_date,
        "days": (end - start).days,
        "convention": "end_minus_start_exclusive",
    }


def convert_time_zone(local_datetime: str, source_zone: str, target_zone: str) -> dict:
    """Convert a date AND time between IANA time zones (时区换算/当地几点),
    e.g. Asia/Shanghai, Asia/Tokyo, Europe/London, America/New_York, UTC.
    local_datetime must be YYYY-MM-DDTHH:MM[:SS], without an offset. Requires
    the date to handle daylight saving. Ambiguous/nonexistent local times are
    rejected instead of guessed. Does not look up events or the current time.
    """
    if len(local_datetime) > 26 or "T" not in local_datetime:
        raise ValueError("provide an explicit date and local time")
    local = datetime.fromisoformat(local_datetime)
    if local.tzinfo is not None:
        raise ValueError("local_datetime must have no UTC offset")
    try:
        source, target = ZoneInfo(source_zone), ZoneInfo(target_zone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown IANA time zone") from exc
    candidates = []
    for fold in (0, 1):
        aware = local.replace(tzinfo=source, fold=fold)
        instant = aware.astimezone(UTC)
        if instant.astimezone(source).replace(tzinfo=None) == local and instant not in candidates:
            candidates.append(instant)
    if len(candidates) != 1:
        raise ValueError("local time is ambiguous or nonexistent; clarify the time")
    result = candidates[0].astimezone(target)
    return {
        "available": True,
        "local_datetime": local_datetime,
        "source_zone": source_zone,
        "target_zone": target_zone,
        "result": result.isoformat(timespec="seconds"),
    }


def summarize_numbers(values: Annotated[list[float], Field(min_length=1, max_length=200)]) -> dict:
    """Compute exact descriptive summary statistics for a supplied numeric list
    (数字列表统计/总和/平均值/中位数/最小最大值). Preserve repeated values. Returns
    count, sum, mean, median, min and max. Does not infer absent data, make
    psychological assessments, or query personal records.
    """
    if not 1 <= len(values) <= 200 or any(not math.isfinite(v) or abs(v) > 1e12 for v in values):
        raise ValueError("provide 1..200 finite numbers with absolute value at most 1e12")
    return {
        "available": True,
        "count": len(values),
        "sum": math.fsum(values),
        "mean": fmean(values),
        "median": median(values),
        "min": min(values),
        "max": max(values),
    }


def create_server(port=18133):
    server = FastMCP(
        "Psych Agent local utilities", host="127.0.0.1", port=port, stateless_http=True
    )
    for function in (calendar_day_difference, convert_time_zone, summarize_numbers):
        server.tool(annotations=ToolAnnotations(readOnlyHint=True))(function)
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18133)
    create_server(parser.parse_args().port).run(transport="streamable-http")
