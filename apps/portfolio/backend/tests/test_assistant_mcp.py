from __future__ import annotations

import asyncio
import base64

from mcp import Client
from mcp.types import ImageContent
import pytest

from portfolio_app import assistant_mcp
from portfolio_app.api.contracts import TransactionCaptureAnalysis


@pytest.fixture(autouse=True)
def bind_mcp_scope(monkeypatch) -> None:
    monkeypatch.setenv(assistant_mcp.PORTFOLIO_ID_ENV, "portfolio/a")
    monkeypatch.setenv(assistant_mcp.BATCH_ID_ENV, "batch 1")
    monkeypatch.setenv(assistant_mcp.MODEL_NAME_ENV, "bound-vision-model")


def test_mcp_catalog_exposes_analysis_without_commit() -> None:
    async def inspect_catalog() -> None:
        async with Client(assistant_mcp.mcp, raise_exceptions=True) as client:
            result = await client.list_tools()

        tools = {tool.name: tool for tool in result.tools}
        assert set(tools) == {
            "get_screenshot_analysis_context",
            "get_screenshot_image",
            "search_canonical_instruments",
            "search_portfolio_transaction_facts",
            "get_portfolio_position_context",
            "preview_screenshot_transaction_proposal",
            "submit_screenshot_analysis",
        }
        assert all("commit" not in name for name in tools)
        assert tools["get_screenshot_image"].annotations is not None
        assert tools["get_screenshot_image"].annotations.read_only_hint is True
        assert tools["submit_screenshot_analysis"].annotations is not None
        assert (
            tools["submit_screenshot_analysis"].annotations.destructive_hint is False
        )
        for tool in tools.values():
            properties = tool.input_schema.get("properties", {})
            assert "portfolio_id" not in properties
            assert "batch_id" not in properties
        submission_properties = tools["submit_screenshot_analysis"].input_schema[
            "properties"
        ]
        assert "source" not in submission_properties
        assert "harness" not in submission_properties
        assert "provider" not in submission_properties
        assert "model_name" not in submission_properties

    asyncio.run(inspect_catalog())


def test_mcp_returns_only_images_belonging_to_the_requested_batch(monkeypatch) -> None:
    requested_paths: list[str] = []

    def fake_api_json(path: str, **_kwargs):
        requested_paths.append(path)
        return {
            "batch": {
                "captures": [
                    {"capture_id": "capture-1"},
                ]
            }
        }

    def fake_api_image(path: str) -> tuple[bytes, str]:
        requested_paths.append(path)
        return b"synthetic-png", "image/png"

    monkeypatch.setattr(assistant_mcp, "_api_json", fake_api_json)
    monkeypatch.setattr(assistant_mcp, "_api_image", fake_api_image)

    async def read_image() -> None:
        async with Client(assistant_mcp.mcp, raise_exceptions=True) as client:
            result = await client.call_tool(
                "get_screenshot_image",
                {
                    "capture_id": "capture-1",
                },
            )

        assert result.is_error is False
        assert len(result.content) == 1
        assert isinstance(result.content[0], ImageContent)
        assert result.content[0].mime_type == "image/png"
        assert base64.b64decode(result.content[0].data) == b"synthetic-png"

    asyncio.run(read_image())
    assert requested_paths == [
        "/portfolios/portfolio%2Fa/transaction-capture-batches/batch%201/agent-context",
        "/portfolios/portfolio%2Fa/transaction-captures/capture-1/image",
    ]


def test_mcp_searches_only_the_requested_portfolio_transaction_facts(monkeypatch) -> None:
    requested_paths: list[str] = []

    def fake_api_json(path: str, **_kwargs):
        requested_paths.append(path)
        return {"transactions": []}

    monkeypatch.setattr(assistant_mcp, "_api_json", fake_api_json)

    result = assistant_mcp.search_portfolio_transaction_facts(
        start_date="2026-07-01",
        end_date="2026-07-31",
        account_id="broker account/1",
        transaction_type="sell short",
    )

    assert result == {"transactions": []}
    assert requested_paths == [
        "/portfolios/portfolio%2Fa/transactions?"
        "start_date=2026-07-01&end_date=2026-07-31&"
        "account_id=broker+account%2F1&transaction_type=sell+short"
    ]


def test_mcp_searches_compact_canonical_instruments_by_exchange_ticker(monkeypatch) -> None:
    requested_paths: list[str] = []

    def fake_api_json(path: str, **_kwargs):
        requested_paths.append(path)
        if path.endswith("/agent-context"):
            return {
                "portfolio": {"portfolio_id": "portfolio/a"},
                "batch": {"batch_id": "batch 1"},
            }
        if path.endswith("/instruments"):
            return {
                "instruments": [
                    {"instrument_core": {
                    "instrument_id": "3690-hk",
                    "instrument_name": "Meituan",
                    "instrument_type": "equity",
                    "currency": "HKD",
                    "exchange_code": "XHKG",
                    "identifiers": [
                        {
                            "identifier_type": "exchange_ticker",
                            "identifier_value": "3690.HK",
                            "is_primary": True,
                        }
                    ],
                    "broker_identifiers": [],
                    }},
                    {"instrument_core": {
                    "instrument_id": "518880-sh",
                    "instrument_name": "华安黄金ETF",
                    "instrument_type": "etf",
                    "currency": "CNY",
                    "exchange_code": "XSHG",
                    "identifiers": [
                        {
                            "identifier_type": "ticker",
                            "identifier_value": "518880.SH",
                            "is_primary": True,
                        }
                    ],
                    "broker_identifiers": [],
                    }},
                ],
            }
        raise AssertionError(f"Unexpected API path: {path}")

    monkeypatch.setattr(assistant_mcp, "_api_json", fake_api_json)

    result = assistant_mcp.search_canonical_instruments(
        "03690",
        currency="HKD",
    )

    assert result["match_count"] == 1
    assert result["truncated"] is False
    assert [item["instrument_id"] for item in result["matches"]] == ["3690-hk"]
    assert requested_paths == [
        "/portfolios/portfolio%2Fa/transaction-capture-batches/batch%201/agent-context",
        "/portfolios/portfolio%2Fa/instruments",
    ]


def test_mcp_reads_position_context_for_the_fixed_portfolio(monkeypatch) -> None:
    requested_paths: list[str] = []

    def fake_api_json(path: str, **_kwargs):
        requested_paths.append(path)
        return {"holdings": []}

    monkeypatch.setattr(assistant_mcp, "_api_json", fake_api_json)

    result = assistant_mcp.get_portfolio_position_context(
        as_of_date="2026-08-24",
    )

    assert result == {"holdings": []}
    assert requested_paths == [
        "/workspace/holdings?portfolio_id=portfolio%2Fa&"
        "include_details=true&as_of_date=2026-08-24"
    ]


def test_mcp_rejects_screenshot_outside_the_batch(monkeypatch) -> None:
    monkeypatch.setattr(
        assistant_mcp,
        "_api_json",
        lambda *_args, **_kwargs: {"batch": {"captures": []}},
    )

    async def read_image() -> None:
        async with Client(assistant_mcp.mcp) as client:
            result = await client.call_tool(
                "get_screenshot_image",
                {
                    "capture_id": "capture-other",
                },
            )

        assert result.is_error is True
        assert "not part of this analysis batch" in result.content[0].text

    asyncio.run(read_image())


def test_mcp_requires_explicit_bound_scope(monkeypatch) -> None:
    monkeypatch.delenv(assistant_mcp.PORTFOLIO_ID_ENV)

    with pytest.raises(RuntimeError, match="must bind the portfolio scope"):
        assistant_mcp.get_screenshot_analysis_context()


def test_mcp_attaches_runtime_owned_analysis_identity(monkeypatch) -> None:
    requests: list[tuple[str, str, object | None]] = []

    def fake_api_json(path: str, *, method: str = "GET", payload=None):
        requests.append((path, method, payload))
        return {"saved": True}

    monkeypatch.setattr(assistant_mcp, "_api_json", fake_api_json)
    analysis = TransactionCaptureAnalysis.model_validate(
        {
            "summary": "No transaction proposal.",
            "documents": [
                {
                    "capture_id": "capture-1",
                    "document_kind": "unknown",
                }
            ],
            "candidates": [],
            "questions": [],
        }
    )

    result = assistant_mcp.submit_screenshot_analysis(
        analysis,
        finish_reason="completed",
    )

    assert result == {"saved": True}
    assert len(requests) == 1
    path, method, payload = requests[0]
    assert path == (
        "/portfolios/portfolio%2Fa/transaction-capture-batches/"
        "batch%201/analysis-revisions"
    )
    assert method == "POST"
    assert isinstance(payload, dict)
    assert payload["source"] == "assistant"
    assert payload["harness"] == "deepseek-harness"
    assert payload["provider"] == "deepseek"
    assert payload["model_name"] == "bound-vision-model"
