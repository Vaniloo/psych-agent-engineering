import pytest

from psych_agent_runtime.utility_mcp import (
    calendar_day_difference,
    convert_time_zone,
    summarize_numbers,
)


@pytest.mark.parametrize(
    "start,end,days",
    [
        ("2026-10-20", "2026-11-03", 14),
        ("2024-02-28", "2024-03-01", 2),
        ("2026-10-20", "2026-10-20", 0),
        ("2026-11-03", "2026-10-20", -14),
    ],
)
def test_calendar_days(start, end, days):
    assert calendar_day_difference(start, end)["days"] == days


@pytest.mark.parametrize("start", ["2026-02-30", "10-20", "20261020", "2026-W43-2"])
def test_calendar_rejects_invalid_or_noncanonical_dates(start):
    with pytest.raises(ValueError):
        calendar_day_difference(start, "2026-11-03")


@pytest.mark.parametrize(
    "local,source,target,expected",
    [
        ("2026-10-20T09:00", "Asia/Shanghai", "Asia/Tokyo", "2026-10-20T10:00:00+09:00"),
        ("2026-07-01T09:00", "America/New_York", "Asia/Shanghai", "2026-07-01T21:00:00+08:00"),
        ("2026-01-01T09:00", "America/New_York", "Asia/Shanghai", "2026-01-01T22:00:00+08:00"),
        ("2026-10-20T23:30", "Asia/Shanghai", "Asia/Tokyo", "2026-10-21T00:30:00+09:00"),
    ],
)
def test_time_zone_and_daylight_saving(local, source, target, expected):
    assert convert_time_zone(local, source, target)["result"] == expected


@pytest.mark.parametrize(
    "local,zone",
    [
        ("2026-03-08T02:30", "America/New_York"),
        ("2026-11-01T01:30", "America/New_York"),
        ("2026-10-20T09:00+08:00", "Asia/Shanghai"),
        ("09:00", "Asia/Shanghai"),
        ("2026-10-20T09:00", "Unknown/Zone"),
    ],
)
def test_time_zone_does_not_guess_ambiguous_or_missing_values(local, zone):
    with pytest.raises(ValueError):
        convert_time_zone(local, zone, "UTC")


def test_statistics_preserve_repetition_and_stable_sum():
    assert summarize_numbers([12, 18, 20, 18, 32]) == {
        "available": True,
        "count": 5,
        "sum": 100,
        "mean": 20,
        "median": 18,
        "min": 12,
        "max": 32,
    }
    assert summarize_numbers([0.1, 0.1, 0.1])["sum"] == pytest.approx(0.3)


@pytest.mark.parametrize("values", [[], [0] * 201, [float("inf")], [float("nan")], [1e13]])
def test_statistics_reject_unbounded_or_nonfinite_input(values):
    with pytest.raises(ValueError):
        summarize_numbers(values)
