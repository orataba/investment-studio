"""Exact JSON text for research originals, with optional structured MCP output."""
import json
import math

from mcp.types import CallToolResult, TextContent


def _has_negative_zero(value):
    if isinstance(value, float):
        return value == 0 and math.copysign(1, value) < 0
    if isinstance(value, dict):
        return any(_has_negative_zero(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_negative_zero(item) for item in value)
    return False


def compact_json_result(payload):
    # Non-finite values are invalid JSON and must fail explicitly. Signed zero
    # is valid JSON: retain it in the text, rather than silently changing data.
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    # The pinned Harness rejects JS negative zero in structuredContent. MCP
    # permits text-only results when no outputSchema requires structured data.
    # Normal pages retain structured data for existing MCP consumers.
    return CallToolResult(content=[TextContent(type="text", text=text)],
                          structured_content=None if _has_negative_zero(payload) else payload)
