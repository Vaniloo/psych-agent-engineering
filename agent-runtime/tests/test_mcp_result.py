from mcp.types import CallToolResult, TextContent

from psych_agent_runtime.mcp_result import decode_result


def test_structured_empty_object_is_not_replaced_by_text():
    result = CallToolResult(structuredContent={}, content=[TextContent(type="text", text='{"wrong":1}')])
    assert decode_result(result) == {}






