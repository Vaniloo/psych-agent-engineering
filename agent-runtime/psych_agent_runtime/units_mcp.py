"""Small optional MCP service for real temperature conversion in development."""

import argparse
import math
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

Unit = Literal["C", "F", "K"]


def convert_temperature(value: float, source_unit: Unit, target_unit: Unit) -> dict:
    """Convert a temperature between Celsius (C/摄氏度), Fahrenheit (F/华氏度)
    and Kelvin (K/开尔文). Obtain a calculated numeric result for temperature
    conversion requests. This tool does not look up weather or location.
    """
    if not math.isfinite(value) or abs(value) > 1e12:
        raise ValueError("temperature must be a finite number within the supported range")
    if source_unit not in {"C", "F", "K"} or target_unit not in {"C", "F", "K"}:
        raise ValueError("supported units are C, F and K")
    celsius = (
        value
        if source_unit == "C"
        else (value - 32) * 5 / 9
        if source_unit == "F"
        else value - 273.15
    )
    if celsius < -273.15 - 1e-9:
        raise ValueError("temperature is below absolute zero")
    result = (
        celsius
        if target_unit == "C"
        else celsius * 9 / 5 + 32
        if target_unit == "F"
        else celsius + 273.15
    )
    return {
        "available": True,
        "input": {"value": value, "unit": source_unit},
        "result": {"value": round(result, 9), "unit": target_unit},
        "method": "deterministic_temperature_conversion",
    }


def create_server(port=18132):
    server = FastMCP("Psych Agent conversions", host="127.0.0.1", port=port, stateless_http=True)
    server.tool(annotations=ToolAnnotations(readOnlyHint=True))(convert_temperature)
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18132)
    create_server(parser.parse_args().port).run(transport="streamable-http")
