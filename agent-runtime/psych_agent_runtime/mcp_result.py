"""Decode the supported MCP SDK result before tool-specific validation."""

import json

from mcp.types import CallToolResult


def decode_result(result: CallToolResult, *, max_text_chars: int | None = None):
    if result.isError:
        raise ValueError("MCP tool returned an error")
    if result.structuredContent is not None:
        return result.structuredContent
    text = "\n".join(item.text for item in result.content if item.type == "text")
    if max_text_chars is not None and len(text) > max_text_chars:
        raise ValueError("MCP result exceeds response budget")
    if not text:
        raise ValueError("Empty MCP tool result")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
