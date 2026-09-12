"""Actual MCP wire replay with an in-memory run; no model or persistent database."""
import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
import pytest
from studio_identity import Principal, principal_context

from watchlist_app import research_mcp as mcp
from watchlist_app.api.routes import sector_research, workbench
from watchlist_app.db.models import InstrumentChartReadModel
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.services import research_access, research_metrics
from watchlist_app.services.research_workbench import compare_series


CUTOFF = datetime(2026, 9, 13, tzinfo=UTC)


class RunSession:
    def __init__(self, run, charts=None):
        self.run, self.charts, self.commits = run, charts or {}, 0

    def scalar(self, query):
        return self.run

    def get(self, model, identifier):
        if model is ResearchEntry:
            return self.run if identifier == self.run.entry_id else None
        if model is ResearchTopic:
            return SimpleNamespace(topic_id="topic", visibility="private", team_id="default",
                                   created_by_user_id="pm-one", portfolio_id=None)
        if model is InstrumentChartReadModel:
            return self.charts.get(identifier)
        raise AssertionError(model)

    def commit(self):
        self.commits += 1


def run_context():
    return SimpleNamespace(entry_id="run-one", topic_id="topic", kind="analysis", team_id="default", status="running",
        context_json={"research_run": True, "cutoff": CUTOFF.isoformat(),
            "research_actor": {"kind": "user", "user_id": "pm-one"},
            "catalogue": [{"instrument_id": value} for value in ("ONE", "TWO")]})


def fixture_rows(counts):
    # The observed live one-year BTC/gold shape is 365 + 312 points. The second
    # case exercises the supported 10000-row boundary without using a live run.
    start = date(2025, 9, 11) if counts == (365, 312) else date(2010, 1, 1)
    return [{"symbol": symbol, "date": (start + timedelta(days=i)).isoformat(), "close": 100 + i * .01,
             "unit": "currency", "currency": "USD", "source_id": f"numeric:{symbol}:{i}:original-version",
             "batch_id": "original-batch", "observed_at": CUTOFF.isoformat(), "available_at": CUTOFF.isoformat(),
             "availability_precision": "observed_at"}
            for symbol, count in zip(("ONE", "TWO"), counts) for i in range(count)]


def wire(tool, arguments):
    result = asyncio.run(mcp.mcp.call_tool(tool, arguments))
    text = result.content[0].text
    assert len(text.encode()) <= 48000
    assert json.loads(text) == result.structured_content
    return result.structured_content


def complete(tool, source_id, section, path=None):
    offset, value = 0, None
    while offset is not None:
        page = wire(tool, {"source_id": source_id, "section": section, "path": path or [], "offset": offset})
        assert page["source_id"] == source_id
        if value is None:
            value = {} if page["data_type"] == "object" else [None] * page["total"] if page["data_type"] == "array" else "" if page["data_type"] == "text" else page["data"]
        if page["data_type"] == "object":
            value.update(page["data"])
        elif page["data_type"] == "array":
            value[offset:offset + len(page["data"])]=page["data"]
        elif page["data_type"] == "text":
            value += page["data"]
        for child in page["deferred"]:
            value[child["path"][-1]] = complete(tool, source_id, section, child["path"])
        offset = page["next_offset"]
    return value


@pytest.mark.parametrize("counts", [(365, 312), (5000, 5000)])
def test_numeric_samples_page_the_original_single_calculation(monkeypatch, counts):
    rows, calls = fixture_rows(counts), []
    class Store:
        def query(self, *args, **kwargs):
            calls.append(kwargs)
            return {"rows": deepcopy(rows), "total": len(rows), "provenance": {"as_of": CUTOFF.isoformat(),
                "version_policy": "latest_observed_per_fact"}}
    monkeypatch.setattr(research_metrics, "numeric_store", lambda: Store())
    monkeypatch.setattr(research_access, "topic_portfolio_ids", lambda *_: set())
    run = run_context()
    session = RunSession(run)
    def request(suffix, payload=None):
        if suffix == "numeric":
            return sector_research.numeric_research(run.entry_id, sector_research.NumericResearchInput(**payload), session)
        query = parse_qs(urlsplit(suffix).query)
        assert urlsplit(suffix).path == "computed-source" and payload is None
        return workbench.run_computed_source(run.entry_id, query["source_id"][0], session)
    monkeypatch.setattr(mcp, "request", request)
    overview = wire("read_research_numbers", {"action": "series", "dataset": "market_series_daily",
        "series_ids": ["ONE", "TWO"], "start": min(row["date"] for row in rows), "end": "2026-09-10"})
    source_id = overview["source_id"]
    retained = deepcopy(run.context_json["computed_metrics"][0])
    assert [item["observations"] for item in overview["data"]["series"]] == list(counts)
    assert overview["data"]["series"][0]["first"]["value"] == 100
    read = overview["data"]["series"][0]["points"]["read"]
    assert read == {"tool": "read_research_numbers", "source_id": source_id, "section": "data", "path": ["series", 0, "points"]}
    # A later run horizon and later available source values cannot rebind a page.
    run.context_json["cutoff"] = "2026-09-14T00:00:00+00:00"
    rows[0]["close"] = -999
    assert complete("read_research_numbers", source_id, "data") == retained["data"]
    assert complete("read_research_numbers", source_id, "sources") == retained["sources"]
    assert complete("read_research_numbers", source_id, "evidence") == retained
    assert len(calls) == session.commits == len(run.context_json["computed_metrics"]) == 1
    assert run.context_json["computed_metrics"][0] == retained


@pytest.mark.parametrize("counts", [(365, 312), (5000, 5000)])
def test_comparison_dates_and_inputs_page_without_recomputing(monkeypatch, counts):
    rows = fixture_rows(counts)
    series = {symbol: {"currency": "USD", "metadata": {"return_series_status": "ready", "return_kind": "price_return", "quote_basis": "close"},
        "points": [{"date": row["date"], "value": row["close"]} for row in rows if row["symbol"] == symbol]} for symbol in ("ONE", "TWO")}
    run = run_context()
    session = RunSession(run, {iid: SimpleNamespace(last_recalculated_at=CUTOFF,
        payload_json={"research_returns": value}) for iid, value in series.items()})
    monkeypatch.setattr(research_access, "topic_portfolio_ids", lambda *_: set())
    computations = []
    def compare(*args):
        computations.append(True)
        return compare_series(*args)
    monkeypatch.setattr(workbench, "compare_series", compare)
    def request(suffix, payload=None):
        if suffix == "tools":
            return workbench.research_tool(run.entry_id, workbench.ResearchToolInput(**payload), session)
        assert urlsplit(suffix).path == "computed-source" and payload is None
        return workbench.run_computed_source(run.entry_id, parse_qs(urlsplit(suffix).query)["source_id"][0], session)
    monkeypatch.setattr(mcp, "request", request)
    actor = Principal("pm-one", "PM", "default", resource_scope={"kind": "run", "id": run.entry_id})
    with principal_context(actor):
        overview = wire("compare_instruments", {"instrument_ids": list(series), "start_date": min(row["date"] for row in rows),
            "end_date": "2026-09-10", "target_id": "ONE", "benchmark_id": "TWO"})
        source_id = overview["source_id"]
        retained = deepcopy(run.context_json["computed_metrics"][0])
        assert overview["result"]["rows"] == retained["data"]["rows"]
        assert overview["request"]["target_id"] == "ONE" and overview["request"]["benchmark_id"] == "TWO"
        assert overview["sample_dates"]["count"] == min(counts)
        assert complete("compare_instruments", source_id, "dates") == retained["data"]["dates"]
        assert complete("compare_instruments", source_id, "result") == retained["data"]
        assert complete("compare_instruments", source_id, "input_series") == retained["input_series"]
        assert complete("compare_instruments", source_id, "evidence") == retained
    assert len(computations) == session.commits == len(run.context_json["computed_metrics"]) == 1
    assert run.context_json["computed_metrics"][0] == retained


def test_computed_source_is_run_bound_and_unknown_sources_are_unreadable(monkeypatch):
    monkeypatch.setattr(research_access, "topic_portfolio_ids", lambda *_: set())
    run = run_context()
    run.context_json["computed_metrics"] = [{"source_id": "computed:one", "source_type": "computed_metric", "data": {"value": 7}}]
    session = RunSession(run)
    actor = Principal("pm-one", "PM", "default", resource_scope={"kind": "run", "id": "run-one"})
    with principal_context(actor):
        assert workbench.run_computed_source("run-one", "computed:one", session)["data"]["value"] == 7
        with pytest.raises(HTTPException) as missing:
            workbench.run_computed_source("run-one", "computed:not-read", session)
        assert missing.value.status_code == 404
        old = {"source_id": "comparison:old", "source_type": "computed_metric", "input_series": {}}
        run.context_json["computed_metrics"].append(old)
        receipt = {"source_id": "comparison:old", "tool": "comparison", "request": {"target_id": "ONE", "benchmark_id": "TWO"},
                   "retrieved_at": "2026-09-12T12:00:00+00:00"}
        run.context_json["tool_evidence"] = [receipt]
        historical = workbench.run_computed_source("run-one", "comparison:old", session)
        assert historical["request"] == receipt["request"] and historical["retrieved_at"] == receipt["retrieved_at"]
        assert "request" not in old  # Reading an older metric never updates it.
        run.entry_id = "other-run"
        with pytest.raises(HTTPException) as outside:
            workbench.run_computed_source("other-run", "computed:one", session)
        assert outside.value.status_code == 403
    assert session.commits == 0
    for tool in (mcp.read_research_numbers, mcp.compare_instruments):
        with pytest.raises(ValueError, match="source_id"):
            tool(section="evidence")
        with pytest.raises(ValueError, match="续读"):
            tool(source_id="computed:one", **({"start": "2026-01-01"} if tool is mcp.read_research_numbers else {"start_date": "2026-01-01"}))
