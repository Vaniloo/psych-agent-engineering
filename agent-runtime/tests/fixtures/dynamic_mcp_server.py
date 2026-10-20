"""Isolated synthetic MCP transport fixture, never a real web/version provider."""

import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

server = FastMCP(
    "dynamic-read-fixture", host="127.0.0.1", port=int(sys.argv[1]), stateless_http=True
)
READ = ToolAnnotations(readOnlyHint=True)


@server.tool(annotations=READ)
async def lookup_package_version(query: str) -> dict:
    """Look up the latest release version of a named software package. Use for version queries, not unit conversion."""
    return {"fixture": True, "kind": "package_release", "query": query}


@server.tool(annotations=READ)
async def convert_units(query: str) -> dict:
    """Convert between measurement units, such as Celsius and Fahrenheit or metres and feet. Not software versions."""
    return {
        "fixture": True,
        "kind": "unit_conversion",
        "query": query,
        "exampleInput": "36 Celsius",
        "exampleOutput": "96.8 Fahrenheit",
        "note": "Synthetic integration example, not a general conversion service",
    }


@server.tool(annotations=READ)
async def owned_lookup(account: str, topic: str) -> dict:
    """Read a topic from the server-bound account."""
    return {"fixture": True, "account": account, "topic": topic}


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
async def remove_everything(query: str) -> dict:
    """A destructive tool that must never be granted to the read loop."""
    raise AssertionError("write tool was dispatched")


server.run(transport="streamable-http")
