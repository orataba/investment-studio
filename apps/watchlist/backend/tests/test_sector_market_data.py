"""Research consumes shared observations without a registered company per constituent."""
from datetime import UTC, datetime

import pytest

from watchlist_app.services import sector_market_data as market
from watchlist_app.services.sector_estimates import read_estimate_evidence
from watchlist_app.db.session import get_session_factory


def register_company_source(iid, kind, symbol):
    from .conftest import canonical_quote_policy, seed_shared_instrument
    seed_shared_instrument({"instrument_id": iid, "instrument_name": f"Registered {symbol}",
        "instrument_type": kind, "currency": "CNY" if symbol.endswith(".SS") else "USD",
        "exchange_code": "XSHG" if symbol.endswith(".SS") else "ARCX",
        "quote_selection_policy": canonical_quote_policy(kind),
        "identifiers": [{"identifier_type": "provider_symbol", "identifier_value": f"fmp:{symbol}", "is_primary": True}]})


@pytest.fixture
def market_store(client):
    store = market.numeric_store()
    def ingest(name, rows, clock="2026-09-05T08:00:00+00:00"):
        return store.ingest(name, [rows], source="fixture", observed_at=datetime.fromisoformat(clock))
    ingest("etf_info", [{"symbol": "XLK", "name": "Technology ETF"}])
    ingest("etf_holdings", [
        {"etf_symbol": "XLK", "holding_key": "AAA", "holding_symbol": "AAA", "holding_name": "Alpha", "weight_percent": 99.9, "snapshot_date": "2026-09-05"},
        {"etf_symbol": "XLK", "holding_key": "cash", "holding_symbol": "", "holding_name": "US DOLLAR", "weight_percent": 0.1, "snapshot_date": "2026-09-05"},
    ])
    ingest("company_profiles", [{"symbol": "AAA", "company_name": "Alpha", "currency": "USD", "is_etf": False, "is_fund": False}])
    ingest("us_eod_daily", [{"symbol": symbol, "date": "2026-09-04", "open": 100, "high": 103, "low": 99, "close": 102, "adjusted_close": 101, "volume": 100} for symbol in ["AAA", "XLK"]])
    ingest("analyst_estimates", [{"symbol": "AAA", "estimate_period": frequency, "target_period_end": "2026-12-31", "revenue_avg": 100, "eps_avg": 2, "num_analysts_revenue": 10, "num_analysts_eps": 9} for frequency in ["annual", "quarter"]])
    return store, ingest


def test_shared_holdings_keep_cash_price_basis_and_unknown_currency(market_store):
    store, _ = market_store
    raw = market.read_sector_market_data(None, " xlk ")
    assert raw["ticker"] == "XLK"
    assert len(raw["holdings"]) == 2
    assert sum(row["weight_percent"] for row in raw["holdings"]) == 100
    equity, cash = raw["holdings"]
    assert cash["holding_type"] == "cash"
    assert equity["company_profile"]["currency"] == "USD"
    assert equity["annual_estimates"][0]["currency"] is None
    assert equity["latest_price"]["close"] == 102
    assert equity["latest_price"]["adjusted_close"] == 101
    assert raw["source"]["collected_at"] != raw["source"]["read_at"]
    assert all(store.read_source(sid) for sid in raw["source"]["source_ids"])


def test_shared_latest_and_historical_cutoff_do_not_mix_captures(market_store):
    _, ingest = market_store
    ingest("analyst_estimates", [{"symbol": "AAA", "estimate_period": "annual", "target_period_end": "2026-12-31", "revenue_avg": 150}], "2026-09-06T08:00:00+00:00")
    earlier = market.read_sector_market_data(None, "XLK", as_of=datetime(2026, 9, 5, 12, tzinfo=UTC))
    assert earlier["holdings"][0]["annual_estimates"][0]["revenue_avg"] == 100
    assert market.read_sector_market_data(None, "XLK")["holdings"][0]["annual_estimates"][0]["revenue_avg"] == 150
    assert market.read_sector_market_data(None, "XLK", as_of=datetime(2026, 9, 4, tzinfo=UTC)) is None
    assert market.read_sector_market_data(None, "XLE") is None
    assert market.read_sector_market_data(None, "SPY") is None
    with pytest.raises(ValueError, match="timezone"):
        market.read_sector_market_data(None, "XLK", as_of=datetime(2026, 9, 5))


def test_two_captures_compare_without_running_research_or_registering_constituents(market_store):
    _, ingest = market_store
    baseline = read_estimate_evidence(None, "xlk")
    assert baseline["status"] == "baseline"
    ingest("financial_statements", [{"symbol": "AAA", "statement_type": "income", "period_end": "2026-06-30", "fiscal_year": 2026, "fiscal_period": "Q2", "reported_currency": "USD"}], "2026-09-04T08:00:00+00:00")
    ingest("analyst_estimates", [{"symbol": "AAA", "estimate_period": frequency, "target_period_end": "2026-12-31", "revenue_avg": 110, "eps_avg": 2, "num_analysts_revenue": 10, "num_analysts_eps": 9} for frequency in ["annual", "quarter"]], "2026-09-06T08:00:00+00:00")
    result = read_estimate_evidence(None, "xlk", as_of=datetime(2026, 9, 6, 12, tzinfo=UTC))
    assert result["status"] == "comparable"
    assert len(result["changes"]) == 2
    assert all(row["delta"] == 10 and row["currency"] == "USD" for row in result["changes"])
    assert result["source_ids"]
    earlier = read_estimate_evidence(None, "xlk", as_of=datetime(2026, 9, 5, 12, tzinfo=UTC))
    assert earlier["status"] == "baseline" and not earlier["changes"]
    assert read_estimate_evidence(None, "512880")["status"] == "unsupported"


def test_bound_statement_versions_match_historical_queries_and_tie_order(market_store):
    store, ingest = market_store
    income = {"symbol": "AAA", "statement_type": "income", "period_end": "2026-06-30",
              "fiscal_year": 2026, "fiscal_period": "Q2", "reported_currency": "USD"}
    ingest("financial_statements", [income], "2026-09-04T08:00:00+00:00")
    ingest("financial_statements", [{**income, "reported_currency": "EUR",
        "available_at": "2026-09-05T08:00:00.000002+00:00"}], "2026-09-05T08:00:00.000001+00:00")
    # Same observation clock, distinct batches and duplicate rows exercise both
    # revision winner ordering and the original result-page ordering.
    ingest("financial_statements", [{**income, "reported_currency": "GBP"},
        {**income, "reported_currency": "CHF"},
        {**income, "fiscal_period": "FY", "reported_currency": "CAD"}], "2026-09-06T08:00:00+00:00")
    ingest("financial_statements", [{**income, "reported_currency": "AUD"}], "2026-09-06T08:00:00+00:00")
    ingest("financial_statements", [{**income, "period_end": "2026-09-30", "fiscal_period": "Q3",
        "available_at": "2026-09-09T08:00:00+00:00", "reported_currency": "JPY"}], "2026-09-07T08:00:00+00:00")
    ingest("financial_statements", [{**income, "reported_currency": "CNY"}], "2026-09-09T08:00:00+00:00")
    cutoff = datetime(2026, 9, 8, tzinfo=UTC)
    history = market.all_rows(store, "financial_statements", symbols=["AAA"], as_of=cutoff, versions=True)
    for clock in ["2026-09-04T07:59:59Z", "2026-09-04T08:00:00Z", "2026-09-05T08:00:00Z",
                  "2026-09-05T08:00:00.000001Z", "2026-09-05T08:00:00.000002Z",
                  "2026-09-06T08:00:00Z", cutoff.isoformat()]:
        expected = market.reporting_statements(store, ["AAA"], clock)
        assert market.reporting_statements_at(history, ["AAA"], clock) == expected
    assert market.reporting_statements_at(history, ["BBB"], cutoff) == {}
    assert market.reporting_statements_at(history, ["AAA"], "2026-09-05T08:00:00.000001Z")["AAA"]["reported_currency"] == "USD"
    assert market.reporting_statements_at(history, ["AAA"], "2026-09-05T08:00:00.000002Z")["AAA"]["reported_currency"] == "EUR"


@pytest.mark.parametrize("page_limit", [None, 2])
def test_estimate_currencies_reuse_one_complete_bound_statement_history(market_store, monkeypatch, page_limit):
    store, ingest = market_store
    income = {"symbol": "AAA", "statement_type": "income", "period_end": "2026-06-30",
              "fiscal_year": 2026, "fiscal_period": "Q2", "reported_currency": "USD"}
    old = ingest("financial_statements", [income], "2026-09-04T08:00:00+00:00")
    new = ingest("financial_statements", [{**income, "reported_currency": "EUR"}], "2026-09-05T12:00:00+00:00")
    ingest("financial_statements", [{**income, "statement_type": "balance", "reported_currency": "GBP"}], "2026-09-05T13:00:00+00:00")
    ingest("financial_statements", [{**income, "reported_currency": "JPY",
        "available_at": "2026-09-07T00:00:00+00:00"}], "2026-09-05T14:00:00+00:00")
    ingest("financial_statements", [{**income, "reported_currency": "CNY"}], "2026-09-07T00:00:00+00:00")
    for frequency, clock in [("annual", "2026-09-06T08:00:00+00:00"), ("quarter", "2026-09-06T09:00:00+00:00")]:
        ingest("analyst_estimates", [{"symbol": "AAA", "estimate_period": frequency,
            "target_period_end": "2026-12-31", "revenue_avg": 110, "eps_avg": 2,
            "num_analysts_revenue": 10, "num_analysts_eps": 9}], clock)
    query, financial_calls = store.query, []

    def counted_query(dataset, **kwargs):
        if dataset == "financial_statements":
            financial_calls.append(dict(kwargs))
            if page_limit is not None:
                kwargs["limit"] = page_limit
        return query(dataset, **kwargs)

    monkeypatch.setattr(store, "query", counted_query)
    result = read_estimate_evidence(None, "xlk", as_of=datetime(2026, 9, 6, 12, tzinfo=UTC))
    assert result["changes"] == []
    assert len(result["observations"]) == 2
    for row in result["observations"]:
        assert row["reason"] == "currency_changed"
        assert (row["previous_currency"], row["currency"]) == ("USD", "EUR")
        assert row["previous_currency_source"]["source_id"] == f"numeric:{old['batch_id']}:0"
        assert row["current_currency_source"]["source_id"] == f"numeric:{new['batch_id']}:0"
    assert [call["offset"] for call in financial_calls] == ([0] if page_limit is None else [0, 2])
    assert all(call["versions"] and call["limit"] == 100000 for call in financial_calls)


@pytest.mark.parametrize("iid,kind,symbol", [("600036-sh", "equity", "600036.SS"), ("broad-market", "etf", "SPY")])
def test_registered_equity_and_broad_fund_share_real_company_captures_and_bound_tools(client, market_store, monkeypatch, iid, kind, symbol):
    from watchlist_app.services import sector_research as service
    from watchlist_app.services import sector_fact_review as fact_review
    from watchlist_app.services.research_notebook import ResearchNotebook, research_sources, validate_notebook
    from watchlist_app.services.shared_instrument_registry import get_shared_instrument
    from watchlist_app.db.models import InstrumentDetail
    from watchlist_app.db.models.workbench import ResearchEntry
    _, ingest = market_store
    register_company_source(iid, kind, symbol)
    company = symbol if kind == "equity" else "AAA"
    currency = "CNY" if kind == "equity" else "USD"
    if kind == "etf":
        ingest("etf_info", [{"symbol": symbol, "name": "Broad market ETF"}])
        ingest("etf_holdings", [
            {"etf_symbol": symbol, "holding_key": "AAA", "holding_symbol": "AAA", "holding_name": "Alpha", "weight_percent": 80, "snapshot_date": "2026-09-05"},
            {"etf_symbol": symbol, "holding_key": "cash", "holding_symbol": "", "holding_name": "US DOLLAR", "weight_percent": 20, "snapshot_date": "2026-09-05"}])
    else:
        ingest("company_profiles", [{"symbol": symbol, "company_name": "招商银行", "currency": currency, "is_etf": False, "is_fund": False}])
    ingest("financial_statements", [{"symbol": company, "statement_type": "income", "period_end": "2026-06-30", "fiscal_year": 2026,
                                    "fiscal_period": "Q2", "reported_currency": currency}], "2026-09-04T08:00:00+00:00")
    for day, value in ((5, 100), (6, 110)):
        ingest("analyst_estimates", [{"symbol": company, "estimate_period": "annual", "target_period_end": "2027-12-31",
            "revenue_avg": value, "eps_avg": 2, "num_analysts_revenue": 10, "num_analysts_eps": 9,
            "raw_sha256": str(day) * 64}], f"2026-09-0{day}T08:00:00+00:00")
    with get_session_factory()() as session:
        result = read_estimate_evidence(session, iid, as_of=datetime(2026, 9, 6, 12, tzinfo=UTC))
        assert result["status"] == "comparable" and result["company_symbols"] == [company]
        assert result["changes"][0]["delta"] == 10 and result["changes"][0]["currency"] == currency
        assert datetime.fromisoformat(result["changes"][0]["current_collected_at"]) == datetime(2026, 9, 6, 8, tzinfo=UTC)
        assert result["changes"][0]["current_currency_source"]["statement_date"] == "2026-06-30"
        assert result["coverage"]["equity_weight_pct"] == (None if kind == "equity" else 80)
        if kind == "equity":
            assert all(row["current_weight_pct"] is None for row in result["coverage"]["metrics"])
        snapshot = service.sector_snapshot(iid, session)
        assert snapshot[1]["company_symbols"] == [company]
        if kind == "equity":
            assert snapshot[1]["holdings"] == [] and snapshot[1]["top_holdings"] == []
            assert not any(row["dataset"].startswith("etf_") for row in snapshot[1]["dataset_status"])
        session.add(InstrumentDetail(instrument_id=iid, instrument_type=kind, detail_view_type=kind,
                                     instrument_name=f"Registered {symbol}", metadata_json={}))
        session.commit()
        monkeypatch.setattr(service, "_research_market", lambda session, iid: "us")
        run, _ = service.begin_run(session, [iid])
        rid = run.entry_id
    service.prepare_run(rid)
    context = client.get(f"/api/research/runs/{rid}/context?originals=true").json()
    assert "sector_company_data" not in context
    assert context["instrument_inputs"][0]["analyst_estimate_history"]["company_symbols"] == [company]
    response = client.get(f"/api/research/runs/{rid}/sector-company/{iid}/{company.lower()}")
    assert response.status_code == 200
    retained = response.json()
    assert retained["source_id"] == f"fmp:{rid}:{iid}:{company}"
    assert retained["source_type"] == "company_snapshot"
    assert retained["instrument_id"] == iid and retained["source_run_id"] == rid
    row = next(row for row in retained["company"]["annual_estimates"] if row["target_period_end"] == "2027-12-31")
    assert row["revenue_avg"] == 110 and row["currency"] == currency and row["raw_sha256"]
    assert row["source_id"] and row["currency_source"]["source_id"]
    assert client.get(f"/api/research/runs/{rid}/sector-company/{iid}/UNRELATED").status_code == 404
    assert client.get(f"/api/research/runs/{rid}/sector-company/unbound/{company}").status_code == 404
    with get_session_factory()() as session:
        retained_context = session.get(ResearchEntry, rid).context_json
        assert set(retained_context["sector_company_data"][iid]) == {company}
        assert retained_context["sector_estimate_evidence"][0]["company_symbols"] == [company]
        assert research_sources(retained_context, rid)[retained["source_id"]] == retained

    # The production context deliberately excludes company data. Exercise the
    # real on-demand API, then the same notebook and receipt checks as publication.
    calls = []
    def api(run_id, suffix, payload=None):
        assert run_id == rid and payload is None
        calls.append(suffix)
        response = client.get(f"/api/research/runs/{run_id}/{suffix}")
        assert response.status_code == 200
        return response.json()
    monkeypatch.setattr(fact_review, "_api_request", api)
    proposed = {"instrument_id": iid, "change_kind": "knowledge", "events": [],
        "research": {"fundamental_view": "已取得成分收入与EPS预测；预测仍需后续兑现。", "source_ids": [retained["source_id"]]},
        "reflection": {"status": "reviewed", "summary": "已核对本轮取得的公司预测及其财期、币种。",
                       "source_ids": [retained["source_id"]]}}
    packet = fact_review._evidence_packet(context, [proposed], rid)
    sources = {source["source_id"]: source for source in packet["sources"]}
    assert calls == [f"sector-company/{iid}/{company}"]
    assert sources[retained["source_id"]] == retained
    validate_notebook(ResearchNotebook.model_validate(proposed["research"]), iid, sources)
    with pytest.raises(ValueError, match="其他标的"):
        validate_notebook(ResearchNotebook(source_ids=[retained["source_id"]]), "unbound", sources)
    checked = {**proposed, "summary": "", "coverage": [], "decisions": []}
    checked.pop("events")
    verified = fact_review._apply_checks({"reviews": [proposed]}, {"reviews": [checked]}, packet["sources"])
    assert verified["reviews"][0]["research"] == proposed["research"]
    assert verified["reviews"][0]["reflection"]["source_ids"] == [retained["source_id"]]
    if kind == "etf":
        assert get_shared_instrument("aaa") is None


@pytest.mark.parametrize("iid,symbol,name", [("tlt", "TLT", "US TREASURY NOTE"), ("gold", "GLD", "GOLD BULLION"), ("oil", "USO", "WTI FUTURE")])
def test_non_equity_fund_exposure_does_not_become_company_estimates(client, market_store, iid, symbol, name):
    _, ingest = market_store
    register_company_source(iid, "etf", symbol)
    ingest("etf_info", [{"symbol": symbol, "name": name}])
    ingest("etf_holdings", [{"etf_symbol": symbol, "holding_key": "non-equity", "holding_symbol": "UNKNOWN", "holding_name": name,
                             "weight_percent": 100, "snapshot_date": "2026-09-05"}])
    with get_session_factory()() as session:
        result = read_estimate_evidence(session, iid)
    assert result["status"] == "not_applicable" and not result["supported"]
    assert not result["changes"] and not result["coverage"]
    assert "EPS" in result["gaps"][0]
