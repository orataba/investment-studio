from copy import deepcopy
from datetime import UTC, datetime
import json
from types import SimpleNamespace

import pytest
from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.providers.financials import normalize_financial_payload

from watchlist_app.services.research_financials import financial_page


def stamp(day):
    return datetime(2026, 9, day, 12, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    result = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'market.db'}", tmp_path / "data"))
    result.create_schema_for_testing()
    yield result
    result.close()


def asset():
    return {"instrument_id": "company", "name": "Company", "instrument_type": "equity",
            "snapshot_cutoff": stamp(2).isoformat(),
            "reference_data": {"provider": "fmp", "provider_symbol": "AAA", "sections": {
                "financials": [{"period": "FY", "date": "2025-12-31", "revenue": 100}]}}}


def ingest(store, kind, period, *, day=1, values=None, accepted=None, period_end=None):
    period_end = period_end or ("2025-12-31" if period == "FY" else "2026-06-30")
    statements, facts = normalize_financial_payload([{
        "symbol": "AAA", "date": period_end,
        "fiscalYear": int(period_end[:4]), "period": period,
        "reportedCurrency": "CNY", "acceptedDate": accepted,
        **(values or {"revenue": 100, "eps": 0.75}),
    }], statement_type=kind, allowed_symbols={"AAA"}, raw_sha256=str(day) * 64,
        collected_at=stamp(day), source_dataset=f"fmp_{kind}")
    by_hash = {row["statement_content_sha256"]: row for row in statements}
    store.ingest("financial_statements", [statements], source="fixture")
    store.ingest("financial_facts", [[{**by_hash[row["statement_content_sha256"]], **row} for row in facts]], source="fixture")


def test_quarterly_and_three_statements_are_complete_and_paged_without_changing_page_summary(store):
    for kind in ("income", "balance_sheet", "cash_flow"):
        ingest(store, kind, "Q2")
    ingest(store, "income", "FY")
    bound = asset()
    original = deepcopy(bound)
    recovered, headers, offset = [], {}, 0
    while offset is not None:
        page = financial_page(bound, as_of=stamp(2), store=store, offset=offset, limit=3, view="facts")
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) < 50000
        assert page["run_cutoff"] == stamp(2).isoformat()
        recovered.extend(page["company"]["financials"])
        headers.update({row["statement_content_sha256"]: row for row in page["company"]["statements"]})
        offset = page["company"]["next_offset"]
    assert len(recovered) == 8
    assert {(row["statement_type"], row["fiscal_period"]) for row in recovered} == {
        ("income", "FY"), ("income", "Q2"), ("balance_sheet", "Q2"), ("cash_flow", "Q2")}
    for fact in recovered:
        statement = headers[fact["statement_content_sha256"]]
        assert statement["reported_currency"] == "CNY"
        assert fact["source_id"] and statement["source_id"]
        assert store.read_source(fact["source_id"])["value"] == fact["value"]
        assert datetime.fromisoformat(fact["observed_at"]) == datetime.fromisoformat(statement["observed_at"]) == stamp(1)
    assert bound == original
    quarter = financial_page(bound, as_of=stamp(2), store=store, statement_type="cash_flow", fiscal_period="Q2", period_end="2026-06-30", view="facts")
    assert quarter["company"]["total_rows"] == 2
    assert quarter["company"]["statement_types"] == {"cash_flow": 1}


def test_statement_directory_selects_exact_year_and_report_without_reading_all_facts(store):
    ingest(store, "income", "Q2", period_end="2025-06-30", values={"revenue": 100})
    ingest(store, "income", "Q2", period_end="2026-06-30", values={"revenue": 110, "eps": 1})
    ingest(store, "balance_sheet", "Q2", values={"totalAssets": 300})
    directory = financial_page(asset(), as_of=stamp(2), store=store, limit=2)
    assert directory["company"]["page_kind"] == "statements"
    assert directory["company"]["financials"] == []
    assert directory["company"]["next_offset"] == 2
    assert directory["company"]["total_rows"] == 3
    assert {row["period_end"] for row in directory["company"]["statements"]} == {"2026-06-30"}
    assert {row["matching_fact_count"] for row in directory["company"]["statements"]} == {1, 2}
    selected = financial_page(asset(), as_of=stamp(2), store=store, statement_type="income",
                               fiscal_period="Q2", period_end="2025-06-30", view="facts")
    assert selected["company"]["total_rows"] == 1
    assert selected["company"]["financials"][0]["value"] == 100
    assert selected["company"]["statements"][0]["period_end"] == "2025-06-30"


def test_large_financial_pages_reduce_transport_size_without_losing_original_rows(store):
    values = {"原始科目" * 120 + str(index): index for index in range(65)}
    ingest(store, "income", "Q2", values=values)
    recovered, offset, page_count = {}, 0, 0
    while offset is not None:
        page = financial_page(asset(), as_of=stamp(2), store=store, offset=offset, limit=100, view="facts")
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) < 48000
        recovered.update({row["line_item"]: row["value"] for row in page["company"]["financials"]})
        offset = page["company"]["next_offset"]
        page_count += 1
    assert recovered == values and page_count > 1


def test_revision_joins_only_exact_statement_version_and_does_not_backdate_capture(store):
    ingest(store, "income", "Q2", values={"revenue": 100, "removedFact": 9})
    ingest(store, "income", "Q2", day=3, values={"revenue": 110})
    early = financial_page(asset(), as_of=stamp(2), store=store, view="facts")
    late = financial_page(asset(), as_of=stamp(4), store=store, view="facts")
    assert {row["line_item"]: row["value"] for row in early["company"]["financials"]} == {"revenue": 100, "removedFact": 9}
    assert {row["line_item"]: row["value"] for row in late["company"]["financials"]} == {"revenue": 110}
    assert early["company"]["statements"][0]["statement_content_sha256"] != late["company"]["statements"][0]["statement_content_sha256"]
    unavailable = financial_page(asset(), as_of=datetime(2026, 8, 31, tzinfo=UTC), store=store)
    assert unavailable["company"]["available"] is False
    assert unavailable["company"]["financials"] == []


def test_read_recorded_before_source_release_still_respects_available_clock(store):
    ingest(store, "income", "Q2", accepted="2026-09-04T08:00:00-04:00")
    assert financial_page(asset(), as_of=stamp(2), store=store, view="facts")["company"]["financials"] == []


def test_read_pages_enter_same_run_original_pool_and_independent_receipt_review(monkeypatch, store):
    from watchlist_app.services import sector_fact_review, research_notebook
    ingest(store, "balance_sheet", "Q2", values={"totalAssets": 150})
    source = financial_page(asset(), as_of=stamp(2), store=store, view="facts")
    context = {"cutoff": stamp(3).isoformat(), "instrument_inputs": [asset()], "financial_sources": [source]}
    assert research_notebook.research_sources(context, "run")[source["source_id"]] == source
    notebook = research_notebook.ResearchNotebook.model_validate({"source_ids": [source["source_id"]]})
    research_notebook.validate_notebook(notebook, "company", research_notebook.research_sources(context, "run"))
    packet = sector_fact_review._evidence_packet(context, [{"instrument_id": "company", "events": [],
        "reflection": {"summary": "复核财报", "source_ids": []}}], "run")
    assert packet["sources"] == [source]
    assert packet["sources"][0]["company"]["financials"][0]["value"] == 150
    with pytest.raises(ValueError, match="其他标的"):
        research_notebook.validate_notebook(notebook, "another", research_notebook.research_sources(context, "run"))


def test_endpoint_keeps_bound_cutoff_and_reuses_the_exact_read_receipt(monkeypatch):
    from watchlist_app.api.routes import sector_research as routes
    from watchlist_app.services import research_financials
    context = {"cutoff": stamp(5).isoformat(), "input_snapshot_cutoff": stamp(2).isoformat(), "instrument_inputs": [asset()]}
    run = SimpleNamespace(context_json=context)
    session = SimpleNamespace(commit=lambda: None)
    calls = []
    monkeypatch.setattr(routes, "market_run", lambda *args: run)
    monkeypatch.setattr(routes.service, "bind_research_instruments", lambda *args: None)
    def read(bound, **kwargs):
        calls.append(kwargs)
        return {"source_id": "financials:read", "instrument_id": "company", "run_cutoff": kwargs["as_of"],
                "request": {key: value for key, value in kwargs.items() if key != "as_of"}, "company": {"financials": []}}
    monkeypatch.setattr(research_financials, "financial_page", read)
    request = routes.FinancialResearchInput(instrument_id="company", statement_type="cash_flow", fiscal_period="Q2")
    first = routes.financial_research("run", request, session)
    second = routes.financial_research("run", request, session)
    assert first == second and len(calls) == 1
    assert calls[0]["as_of"] == stamp(2).isoformat()
    assert run.context_json["financial_sources"] == [first]


def test_mcp_uses_complete_financial_reader_and_keeps_other_reference_sections(monkeypatch):
    from watchlist_app import research_mcp
    context = {"catalogue": [{"instrument_id": "company"}], "instrument_inputs": [asset()]}
    calls = []
    def request(suffix, payload=None):
        if suffix == "context":
            return context
        calls.append((suffix, payload))
        return {"source_id": "financials:receipt", "company": {"financials": [{"line_item": "totalAssets", "value": 150}]}}
    monkeypatch.setattr(research_mcp, "request", request)
    result = research_mcp.read_research_instrument("company", "financials", statement_type="balance_sheet", fiscal_period="Q2")
    assert result["source_id"] == "financials:receipt"
    assert calls == [("financials", {"instrument_id": "company", "offset": 0, "limit": 20,
        "statement_type": "balance_sheet", "fiscal_period": "Q2", "period_end": None, "view": "statements"})]
    with pytest.raises(ValueError, match="财报类型"):
        research_mcp.read_research_instrument("company", "holdings", statement_type="balance_sheet")
