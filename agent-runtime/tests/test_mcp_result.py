import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from psych_agent_runtime.dynamic_tools import unpack_result
from psych_agent_runtime.mcp_result import decode_result


def test_structured_empty_object_is_not_replaced_by_text():
    result = CallToolResult(structuredContent={}, content=[TextContent(type="text", text='{"wrong":1}')])
    assert decode_result(result) == {}


def test_text_blocks_are_decoded_together_and_dynamic_lists_are_wrapped():
    result = CallToolResult(content=[TextContent(type="text", text='[1,'), TextContent(type="text", text='2]')])
    assert decode_result(result) == [1, 2]
    assert unpack_result(result) == {"result": [1, 2]}
    assert unpack_result(CallToolResult(content=[TextContent(type="text", text="hello")])) == {"text": "hello"}


def test_errors_empty_results_and_oversized_dynamic_results_are_rejected():
    with pytest.raises(ValueError, match="error"):
        decode_result(CallToolResult(isError=True, content=[]))
    with pytest.raises(ValueError, match="Empty"):
        decode_result(CallToolResult(content=[ImageContent(type="image", data="", mimeType="image/png")]))
    with pytest.raises(ValueError, match="budget"):
        unpack_result(CallToolResult(structuredContent={"text": "x" * 120001}, content=[]))


def test_text_limit_is_checked_before_json_decoding(monkeypatch):
    import psych_agent_runtime.mcp_result as decoder

    def unexpected_decode(_):
        raise AssertionError("oversized text must not reach JSON decoder")

    monkeypatch.setattr(decoder.json, "loads", unexpected_decode)
    with pytest.raises(ValueError, match="budget"):
        unpack_result(CallToolResult(content=[TextContent(type="text", text="x" * 120001)]))
