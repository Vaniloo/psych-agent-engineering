import pytest

from psych_agent_runtime.units_mcp import convert_temperature


@pytest.mark.parametrize(
    "value,source,target,expected",
    [
        (36, "C", "F", 96.8),
        (86, "F", "C", 30),
        (0, "C", "K", 273.15),
        (273.15, "K", "C", 0),
        (-40, "C", "F", -40),
        (0, "K", "F", -459.67),
    ],
)
def test_real_temperature_conversion(value, source, target, expected):
    assert convert_temperature(value, source, target)["result"]["value"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "value,unit",
    [(float("nan"), "C"), (float("inf"), "F"), (-274, "C"), (-1, "K"), (-460, "F"), (1e20, "C")],
)
def test_invalid_values_never_return_a_success(value, unit):
    with pytest.raises(ValueError):
        convert_temperature(value, unit, "C")
