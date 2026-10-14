"""FMP acquisition scope and specialist entry points use retained public facts."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import market_evidence, research_fmp_scope as scope
from .test_sector_market_data import market_store, register_company_source


def clock(day):
    return datetime(2026, 9, day, 12, tzinfo=UTC)


def register(session, iid, kind, symbol, status="Invested"):
    register_company_source(iid, kind, symbol)
    session.add(InstrumentDetail(instrument_id=iid, instrument_name=symbol, instrument_type=kind,
        detail_view_type=kind, metadata_json={}))
    session.add(InstrumentAttributeValue(instrument_id=iid, attribute_key="coverage_status",
        value_json=status, adopted_at=clock(1)))
    session.commit()


def add_research(session, key, entities, *, day=4, origin=None, visibility="team", team="default", portfolio=None):
    session.add(ResearchTopic(topic_id=key, title=key, instrument_ids=["xlk"], visibility=visibility,
        team_id=team, portfolio_id=portfolio))
    session.flush()
    session.add(ResearchEntry(entry_id=key, topic_id=key, title=key, kind="analysis", status="completed",
        team_id=team, created_at=clock(day), context_json={"sector_run": True, "instrument_ids": ["xlk"],
            "cutoff": clock(day).isoformat(), "market_queries": [{"instrument_id": "xlk", "query": "",
                "entities": entities, **({"origin": origin} if origin else {})}]}))
    session.flush()


@pytest.mark.parametrize("excluded", ["private", "portfolio", "historical_portfolio", "other_team"])
def test_explicit_focus_excludes_private_portfolio_other_team_and_generated_top10(client, excluded):
    with get_session_factory()() as session:
        add_research(session, "public-focus", ["fmp:AAA", "not-an-fmp-entity"])
        add_research(session, "excluded", ["fmp:SECRET"], day=6,
            visibility="private" if excluded == "private" else "team",
            team="other" if excluded == "other_team" else "default",
            portfolio="private-account" if excluded == "portfolio" else None)
        if excluded == "historical_portfolio":
            session.add(ResearchEntry(entry_id="retained-private-binding", topic_id="excluded", title="old",
                kind="analysis", created_at=clock(2), context_json={"portfolio_id": "private-account"}))
        session.commit()
        assert scope.latest_research_focus(session, "xlk", clock(7)) == ["AAA"]
        add_research(session, "fixed", ["fmp:OLD_CONSTITUENT"], day=8, origin=scope.FIXED_INPUT_ORIGIN)
        session.commit()
        assert scope.latest_research_focus(session, "xlk", clock(9)) == []
        assert scope.latest_research_focus(session, "xlk", clock(5)) == ["AAA"]


def test_real_scope_uses_current_eligible_top10_and_explicit_focus_without_weight_normalization(market_store):
    _, ingest = market_store
    symbols = [f"C{index:02}" for index in range(12)]
    profiles = [{"symbol": symbol, "company_name": symbol, "is_etf": False, "is_fund": False}
        for symbol in [*symbols, "MANUAL", "RESEARCH"]]
    profiles.append({"symbol": "FUND", "is_etf": True, "is_fund": False})
    ingest("company_profiles", profiles)
    ingest("etf_holdings", [{"etf_symbol": "XLK", "holding_key": symbol, "holding_symbol": symbol,
        "holding_name": symbol, "weight_percent": 12 - index, "snapshot_date": "2026-09-05"}
        for index, symbol in enumerate(symbols)] + [
        {"etf_symbol": "XLK", "holding_key": "FUND", "holding_symbol": "FUND", "holding_name": "ETF", "weight_percent": 30, "snapshot_date": "2026-09-05"},
        {"etf_symbol": "XLK", "holding_key": "cash", "holding_symbol": "", "holding_name": "US DOLLAR", "weight_percent": 1, "snapshot_date": "2026-09-05"}], "2026-09-05T10:00:00+00:00")
    with get_session_factory()() as session:
        register(session, "xlk", "etf", "XLK")
        register(session, "proposed", "equity", "MANUAL", "Proposed")
        register(session, "watch-only", "equity", "WATCH", "Watching")
        from watchlist_app.services.research_dossier import ResearchGuidanceInput, save_guidance
        save_guidance(session, "xlk", ResearchGuidanceInput(guidance="", focus_company_symbols=["MANUAL", "FUND"], expected_revision=0))
        add_research(session, "public-focus", ["fmp:RESEARCH"])
        session.commit()
        result = scope.build_fmp_research_scope(session, clock(6))
        assert {row["instrument_id"] for row in result["instruments"]} == {"xlk", "proposed"}
        etf = next(row for row in result["instruments"] if row["instrument_id"] == "xlk")
        assert [row["symbol"] for row in etf["companies"]] == [*symbols[:10], "MANUAL", "RESEARCH"]
        assert [row["weight_percent"] for row in etf["companies"][:10]] == list(range(12, 2, -1))
        assert etf["companies"][0]["holdings_as_of"] is None
        assert etf["companies"][0]["holdings_snapshot_date"] == "2026-09-05"
        assert etf["companies"][-1]["weight_percent"] is None
        assert any("FUND" in note for note in etf["coverage"])
        assert {row["symbol"]: row["kind"] for row in result["subjects"]} == {
            "XLK": "fund", **{symbol: "company" for symbol in [*symbols[:10], "MANUAL", "RESEARCH"]}}
        assert scope.build_fmp_research_scope(session, clock(6), ["watch-only"])["instruments"] == []


def test_no_explicit_fmp_identity_never_opens_market_database(client, monkeypatch):
    from .conftest import canonical_quote_policy, seed_shared_instrument
    seed_shared_instrument({"instrument_id": "non-fmp", "instrument_name": "Explicit identity only",
        "instrument_type": "equity", "currency": "USD", "exchange_code": "XNAS", "quote_selection_policy": canonical_quote_policy("equity"),
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "NO_GUESS", "is_primary": True}]})
    monkeypatch.setattr("watchlist_app.services.sector_market_data.numeric_store", lambda: pytest.fail("opened unused store"))
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="non-fmp", instrument_name="Name", instrument_type="equity", detail_view_type="equity"))
        session.add(InstrumentAttributeValue(instrument_id="non-fmp", attribute_key="coverage_status", value_json="Proposed", adopted_at=clock(1)))
        session.flush()
        result = scope.build_fmp_research_scope(session, clock(6))
        assert result["symbols"] == [] and result["subjects"] == []
        assert result["instruments"][0]["symbol"] is None


def test_unconfirmed_holdings_are_coverage_gaps_not_guessed_companies():
    companies, gaps = scope.select_companies("ETF", "Fund", "etf", [
        {"holding_symbol": "UNKNOWN", "holding_name": "Some Security", "weight_percent": 50},
        {"holding_symbol": "KNOWN", "holding_name": "Known", "weight_percent": None}],
        {"KNOWN": {"is_etf": False, "is_fund": False}}, [], [])
    assert companies == [] and len(gaps) == 2


def test_fixed_directories_are_paged_typed_and_readable_by_specialist_without_copying_originals(client, monkeypatch):
    from watchlist_app.services.research_sections import section_context
    from watchlist_app.services import sector_research
    current = datetime.now(UTC)
    with get_session_factory()() as session:
        register(session, "xlk", "etf", "XLK")
        register(session, "company", "equity", "AAA", "Proposed")
        session.commit()
        # Real corpus rows exercise exact entity/channel filtering and source/version pointers.
        store = market_evidence.text_store()
        from studio_market.text.fmp import _record
        for index in range(23):
            response = SimpleNamespace(received_at=current - timedelta(minutes=1), endpoint="news/stock")
            document, body, raw = _record(response, {"symbol": "AAA", "title": f"News {index}",
                "url": f"https://issuer.example/{index}", "text": "Actual provider excerpt", "publishedDate": "2026-09-01"},
                "AAA", "fmp:stock_news")
            store.capture_provider_document(document, body, raw)
        cutoff = datetime.now(UTC).isoformat()
        context = {"instrument_ids": ["company", "xlk"], "cutoff": cutoff, "input_snapshot_cutoff": cutoff}
        context["sector_estimate_evidence"] = [{"instrument_id": "company", "company_symbols": ["AAA"],
            "changes": [{"symbol": "AAA", "frequency": "annual", "metric": "eps_avg", "delta": 2}],
            "observations": [], "unmatched": []}]
        scope.bind_fmp_document_inputs(session, context, ["company", "xlk"], cutoff)
        company = context["fmp_inputs"]["company"]
        assert len(company["directories"]) == 4
        news = next(row for row in company["directories"] if row["channel_ids"] == ["fmp:stock_news"])
        assert news["total"] == 23 and len(news["rows"]) == 20
        assert news["next_offset"] == news["continuation"]["offset"] == 20
        assert "Actual provider excerpt" not in str(company)
        assert news["rows"][0]["information_type"] == "company_news"
        pointer = news["rows"][0]["read"]
        assert store.read(pointer["document_id"], version_id=pointer["version_id"])["content_text"] == "Actual provider excerpt"
        continuation = {key: value for key, value in news["continuation"].items() if key != "tool"}
        remaining = market_evidence.search_market_directory(context, continuation)
        assert len(remaining["rows"]) == 3
        assert context["market_queries"][-1]["origin"] == scope.FIXED_INPUT_ORIGIN
        fund_dirs = context["fmp_inputs"]["xlk"]["directories"]
        assert {row["channel_ids"][0] for row in fund_dirs if row["subject_kind"] == "fund"} == set(scope.FMP_FUND_CHANNELS)
        recent = section_context(context, "company", "recent_updates")
        assert recent["fmp_inputs"] == company
        assert recent["analyst_estimate_changes"][0]["change_counts"] == [{
            "frequency": "annual", "metric": "eps_avg", "direction": "increase", "count": 1}]
        assert "changes" not in recent["analyst_estimate_changes"][0]
        assert "fmp_inputs" not in section_context(context, "company", "themes")
        # API field projection must not drop the fixed directories before MCP reads them.
        run, _ = sector_research.begin_run(session, ["company"])
        run.status = "running"
        run.context_json = {**run.context_json, **context, "instrument_ids": ["company"],
            "execution": {"attempt": 1, "research_section": "overview"}}
        session.commit()
        result = client.post(f"/api/research/runs/{run.entry_id}/section-context", json={
            "instrument_id": "company", "section": "recent_updates", "path": ["fmp_inputs", "directories"]})
        assert result.status_code == 200, result.text
        assert result.json()["total"] == 4
        task = client.post(f"/api/research/runs/{run.entry_id}/tasks", json={
            "instrument_id": "company", "role": "recent_updates", "instruction": "核查公司披露"})
        assert task.status_code == 200, task.text
        result = client.get(f"/api/research/runs/{run.entry_id}/tasks/{task.json()['task_id']}/context")
        assert result.status_code == 200, result.text
        assert "fmp_inputs" in str(result.json()) and "analyst_estimate_changes" in str(result.json())


def test_fmp_text_incremental_scope_runs_without_previous_search_and_flags_filing_index(client, monkeypatch):
    from studio_market.text.fmp import _record
    from watchlist_app.services import research_triggers as triggers
    store = market_evidence.text_store()
    monkeypatch.setattr(triggers, "text_store", lambda: store)
    monkeypatch.setattr(triggers, "_numeric_changes", lambda *args, **kwargs: [])
    before = datetime.now(UTC)
    with get_session_factory()() as session:
        register(session, "company", "equity", "AAA")
        for symbol, channel, item in [
            ("AAA", "fmp:stock_news", {"title": "Relevant", "url": "https://issuer.example/relevant", "text": "Retained excerpt"}),
            ("AAA", "fmp:stock_news", {"title": "Title only", "url": "https://issuer.example/no-body"}),
            ("AAA", "fmp:sec_filings", {"formType": "8-K", "filingDate": "2026-09-05", "link": "https://www.sec.gov/Archives/fixture.htm"}),
            ("OTHER", "fmp:stock_news", {"title": "Unrelated", "url": "https://issuer.example/unrelated", "text": "Do not broaden scope"}),
        ]:
            response = SimpleNamespace(received_at=datetime.now(UTC), endpoint=channel)
            store.capture_provider_document(*_record(response, {"symbol": symbol, **item}, symbol, channel))
        # Later web retrieval may advance a run's final clock; the frozen FMP
        # input clock still controls which new arrivals have not been covered.
        prior = {"cutoff": datetime.now(UTC).isoformat(), "input_snapshot_cutoff": before.isoformat(),
            "instrument_ids": ["company"], "market_queries": []}
        result = triggers.research_trigger(session, "company", prior, now=datetime.now(UTC))
        sources = result["reasons"][0]["sources"]
        assert {row["title"] for row in sources} == {"Relevant", "AAA · SEC 8-K · 2026-09-05"}
        filing = next(row for row in sources if row["channel_id"] == "fmp:sec_filings")
        assert filing["evidence_scope"] == "filing_index" and filing["requires_original_read"] is True
        assert filing["content_completeness"] == "title_only"
        assert all(row["matched_query"]["entities"] == ["fmp:AAA"] for row in sources)
        prior["incremental_trigger"] = result
        assert triggers.research_trigger(session, "company", prior, now=datetime.now(UTC))["reasons"] == []
