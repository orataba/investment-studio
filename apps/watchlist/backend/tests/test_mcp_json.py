"""Research originals cross the pinned Harness JSON boundary without mutation."""
import asyncio
import json
import math
from pathlib import Path
import shutil
import subprocess

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from watchlist_app import research_mcp, review_mcp
from watchlist_app.mcp_json import compact_json_result


def _read(monkeypatch, tmp_path, consumer, value):
    source = {"source_id": "computed:exact", "data": {"rows": [{"residual": value}]}}
    if consumer == "research":
        monkeypatch.setattr(research_mcp, "request", lambda *a, **kw: source)
        server, name = research_mcp.mcp, "read_quant_analysis"
    else:
        packet = tmp_path / "packet.json"
        packet.write_text(json.dumps({"packet": {"sources": [source]}, "response_schema": {}}))
        monkeypatch.setenv("INVESTMENT_STUDIO_REVIEW_PACKET", str(packet))
        monkeypatch.setenv("INVESTMENT_STUDIO_REVIEW_READS", str(tmp_path / "reads.jsonl"))
        server, name = review_mcp.mcp, "read_review_source"
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == name)
    assert tool.output_schema is None  # structuredContent is optional on the wire.
    arguments = {"source_id": source["source_id"]}
    if consumer == "review":
        arguments["path"] = ["data"]
    return asyncio.run(server.call_tool(name, arguments))


@pytest.mark.parametrize("consumer", ["research", "review"])
@pytest.mark.parametrize("value", [-0.0, 0.0, -0.125, 1.2345678901234567])
def test_actual_read_tools_preserve_numbers_and_optional_structure(monkeypatch, tmp_path, consumer, value):
    result = _read(monkeypatch, tmp_path, consumer, value)
    payload = json.loads(result.content[0].text)
    delivered = payload["data"]["rows"][0]["residual"]
    assert delivered == value and math.copysign(1, delivered) == math.copysign(1, value)
    negative_zero = value == 0 and math.copysign(1, value) < 0
    assert (result.structured_content is None) == negative_zero
    wire = result.model_dump(by_alias=True, exclude_none=True)
    assert ("structuredContent" not in wire) == negative_zero
    if not negative_zero:
        assert result.structured_content == payload


@pytest.mark.parametrize("consumer", ["research", "review"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_outputs_are_explicit_errors(monkeypatch, tmp_path, consumer, value):
    with pytest.raises(ToolError, match="Out of range float values are not JSON compliant"):
        _read(monkeypatch, tmp_path, consumer, value)


def test_signed_zero_text_survives_installed_harness_snapshotter():
    root = Path(__file__).resolve().parents[4]
    modules = list((root / "infra/harness/node_modules/.pnpm").glob(
        "@deepseek-ai+dsh-session@*/node_modules/@deepseek-ai/dsh-session/lib/types/json.js"))
    node = shutil.which("node")
    if not node or not modules:
        pytest.skip("Pinned local Harness installation is unavailable")
    original = {"data": {"rows": [{"identity_residual_pp": -0.0}]}}
    wire = compact_json_result(original).model_dump(by_alias=True, exclude_none=True)
    script = """
import {readFileSync} from 'node:fs';
const {snapshotJsonValue} = await import(process.argv[1]);
const wire = JSON.parse(readFileSync(0, 'utf8'));
if (snapshotJsonValue({content: [], structuredContent: {residual: -0}}) !== undefined)
  throw new Error('Expected the pinned Harness to reject structured negative zero');
if (snapshotJsonValue(wire) === undefined) throw new Error('Exact text output was rejected');
const data = JSON.parse(wire.content[0].text);
if (!Object.is(data.data.rows[0].identity_residual_pp, -0))
  throw new Error('Signed zero was changed');
"""
    subprocess.run([node, "--input-type=module", "-e", script, modules[0].as_uri()],
                   input=json.dumps(wire), text=True, check=True, capture_output=True, timeout=10)
    assert math.copysign(1, original["data"]["rows"][0]["identity_residual_pp"]) < 0
