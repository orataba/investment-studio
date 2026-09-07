"""Sector research reads the project's retained ETF reference snapshot."""
from copy import deepcopy
from datetime import datetime

import pytest

from investment_studio_instrument_core.db_models import Instrument, InstrumentReferenceObservation, InstrumentReferenceSnapshot
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.sector_market_data import read_sector_market_data


@pytest.fixture
def sector_snapshot(client):
    estimate = {"symbol": "AAA", "estimate_period": "annual", "target_period_end": "2099-12-31",
        "revenue_avg": 1000, "eps_avg": 2.5, "num_analysts_revenue": 12, "num_analysts_eps": 11,
        "source_dataset": "fmp_analyst_estimates_bulk", "raw_sha256": "estimate-source",
        "collected_at": "2026-08-10T00:00:00+00:00", "historical_use": "since_capture",
        "currency": None, "currency_status": "not_supplied"}
    equity = {"holding_key": "stock", "holding_symbol": "AAA", "holding_name": "Alpha",
        "holding_type": "equity", "weight_percent": 99.5, "snapshot_date": "2026-09-04",
        "source_dataset": "fmp_etf_current_holdings", "raw_sha256": "holdings-source",
        "collected_at": "2026-09-05T00:00:00+00:00",
        "company_profile": {"symbol": "AAA", "company_name": "Alpha", "currency": "USD",
            "collected_at": "2026-08-10T00:00:00+00:00"},
        "annual_estimates": [estimate], "quarterly_estimates": [{**estimate,
            "estimate_period": "quarter", "target_period_end": "2000-12-31"}],
        "latest_price": {"symbol": "AAA", "date": "2026-09-04", "close": 50, "adjusted_close": 48},
        "price_coverage": {"first_date": "2026-09-04", "last_date": "2026-09-04", "observations": 1}}
    holdings = [equity, *[{"holding_key": key, "holding_symbol": symbol, "holding_name": name,
        "holding_type": kind, "weight_percent": weight, "company_profile": None,
        "annual_estimates": [], "quarterly_estimates": [], "latest_price": None, "price_coverage": None}
        for key, symbol, name, kind, weight in [
            ("cash", None, "US DOLLAR", "cash", -0.1),
            ("fund", None, "SSI US GOV MONEY MARKET CLASS", "fund", 0.4),
            ("future", "IXTU6", "XAK TECHNOLOGY SEP26", "future", 0.1),
            ("contra", "2602335D", "CONTRA HOLOGIC", "other", 0),
            ("unknown", "BBB", "Unresolved holding", "unclassified", 0.1)]]]
    raw = {"ticker": "XLK", "source": {"provider": "FMP", "collected_at": "2026-09-05T00:00:00+00:00"},
        "etf": {"info": {"symbol": "XLK", "name": "Technology ETF"},
            "latest_price": {"symbol": "XLK", "date": "2026-09-04", "close": 102, "adjusted_close": 101},
            "price_coverage": {"first_date": "2026-09-03", "last_date": "2026-09-04", "observations": 2}},
        "holdings": holdings, "dataset_status": [{"dataset": "fmp_us_eod", "status": "failed"}],
        "gaps": [{"kind": "unclassified_holding", "symbol": "BBB"}]}
    with get_session_factory()() as session:
        session.add(Instrument(instrument_id="xlk", instrument_name="Technology ETF", instrument_type="etf",
            currency="USD", exchange_code="XNYS", quote_selection_policy_json={}))
        session.flush()
        session.add(InstrumentReferenceSnapshot(instrument_id="xlk", value_json={"instrument_id": "xlk",
            "provider": "fmp", "source": {"collected_at": "2026-09-05T00:00:00+00:00"},
            "sections": {"profile": {"name": "Technology ETF"}, "fund_info": {"holdingsCount": 6},
                "holdings": [{"symbol": "AAA", "weightPercent": 99.5}], "sector_market_data": raw}}))
        session.commit()
    return raw


def test_project_snapshot_preserves_constituents_dates_and_unknown_estimate_currency(sector_snapshot):
    with get_session_factory()() as session:
        result = read_sector_market_data(session, " xlk ")
        assert result["ticker"] == "XLK"
        assert result["holdings"] == sector_snapshot["holdings"]
        assert sum(row["weight_percent"] for row in result["holdings"]) == pytest.approx(100)
        equity = result["holdings"][0]
        assert equity["company_profile"]["currency"] == "USD"
        assert equity["annual_estimates"][0]["currency"] is None
        assert equity["quarterly_estimates"][0]["target_period_end"] == "2000-12-31"
        assert result["source"]["read_at"] != equity["annual_estimates"][0]["collected_at"]
        assert {row["kind"] for row in result["gaps"]} == {"unclassified_holding", "no_forward_quarter_estimates"}
        assert session.get(InstrumentReferenceSnapshot, "xlk").value_json["sections"]["sector_market_data"] == sector_snapshot


def test_missing_project_snapshot_is_explicit_and_unsupported_ticker_is_rejected(client):
    with get_session_factory()() as session:
        assert read_sector_market_data(session, "XLE") is None
        with pytest.raises(ValueError, match="11"):
            read_sector_market_data(session, "SPY")


def test_snapshot_cannot_be_read_as_another_etf(sector_snapshot):
    with get_session_factory()() as session:
        row = session.get(InstrumentReferenceSnapshot, "xlk")
        payload = deepcopy(row.value_json)
        payload["sections"]["sector_market_data"]["ticker"] = "XLF"
        row.value_json = payload
        session.flush()
        with pytest.raises(ValueError, match="归属"):
            read_sector_market_data(session, "XLK")


def test_company_snapshot_retains_all_estimate_periods_and_source_provenance(sector_snapshot):
    from watchlist_app.services.sector_research import sector_snapshot as project_snapshot
    with get_session_factory()() as session:
        _, evidence, companies = project_snapshot("xlk", session)
    company = companies["AAA"]
    estimate = company["annual_estimates"][0]
    assert estimate["source_dataset"] == "fmp_analyst_estimates_bulk"
    assert estimate["raw_sha256"] == "estimate-source" and estimate["currency"] is None
    assert company["quarterly_estimates"][0]["target_period_end"] == "2000-12-31"
    assert evidence["source"]["read_at"] != estimate["collected_at"]
    assert evidence["holdings_as_of"] is None
    assert evidence["holdings_observed_on"] == "2026-09-04"


def test_normal_instrument_reference_excludes_heavy_sector_packet(sector_snapshot, monkeypatch):
    from watchlist_app.services import shared_instrument_registry as registry
    monkeypatch.setattr(registry, "get_shared_instrument", lambda _: {"instrument_id": "xlk"})
    reference = registry.get_shared_reference_data("xlk")
    assert reference["sections"] == {"profile": {"name": "Technology ETF"}, "fund_info": {"holdingsCount": 6},
                                    "holdings": [{"symbol": "AAA", "weightPercent": 99.5}]}
    assert reference["provider"] == "fmp" and reference["source"] == {"collected_at": "2026-09-05T00:00:00+00:00"}
    with get_session_factory()() as session:
        assert "sector_market_data" in session.get(InstrumentReferenceSnapshot, "xlk").value_json["sections"]


def test_research_cutoff_reads_the_retained_observation_without_later_collection_leakage(sector_snapshot, monkeypatch):
    from watchlist_app.db.models import InstrumentDetail
    from watchlist_app.db.models.workbench import ResearchEntry
    from watchlist_app.services import sector_research, shared_instrument_registry as registry
    cutoff = datetime.fromisoformat("2026-09-05T12:00:00+00:00")
    class Clock:
        @staticmethod
        def now(tz):
            return cutoff.astimezone(tz)
        fromisoformat = datetime.fromisoformat
    monkeypatch.setattr(sector_research, "datetime", Clock)
    monkeypatch.setattr(registry, "get_shared_instrument", lambda _: {"instrument_id": "xlk", "instrument_type": "etf"})
    with get_session_factory()() as session:
        raw = deepcopy(session.get(InstrumentReferenceSnapshot, "xlk").value_json)
        raw["fetched_at"] = "2026-09-05T00:00:00+00:00"
        earlier = InstrumentReferenceObservation(observation_id="earlier", instrument_id="xlk", collected_at=datetime.fromisoformat("2026-09-05T00:00:00+00:00"), value_json=raw)
        session.add(earlier)
        session.flush()
        raw = deepcopy(raw)
        raw["fetched_at"] = "2026-09-06T00:00:00+00:00"
        raw["sections"]["fund_info"]["holdingsCount"] = 7
        raw["sections"]["sector_market_data"]["holdings"][0]["annual_estimates"][0]["revenue_avg"] = 2000
        session.add(InstrumentReferenceObservation(observation_id="later", instrument_id="xlk", collected_at=datetime.fromisoformat("2026-09-06T00:00:00+00:00"), value_json=raw))
        session.get(InstrumentReferenceSnapshot, "xlk").value_json = raw
        session.add(InstrumentDetail(instrument_id="xlk", instrument_type="etf", detail_view_type="etf",
            instrument_name="Technology ETF", metadata_json={}))
        session.commit()
        result = read_sector_market_data(session, "XLK", as_of=cutoff)
        assert result["holdings"][0]["annual_estimates"][0]["revenue_avg"] == 1000
        assert result["source"]["observation_id"] == earlier.observation_id
        assert read_sector_market_data(session, "XLK")["holdings"][0]["annual_estimates"][0]["revenue_avg"] == 2000
        assert read_sector_market_data(session, "XLK", as_of=datetime.fromisoformat("2026-09-04T00:00:00+00:00")) is None
        run, _ = sector_research.begin_run(session, ["xlk"])
        run_id = run.entry_id
    sector_research.prepare_run(run_id)
    with get_session_factory()() as session:
        context = session.get(ResearchEntry, run_id).context_json
        nested = context["instrument_inputs"][0]
        assert nested["reference_data"]["sections"]["fund_info"]["holdingsCount"] == 6
        assert "sector_market_data" not in nested["reference_data"]["sections"]
        assert nested["analyst_estimate_history"]["current_snapshot"]["observation_id"] == "earlier"
        assert context["sector_estimate_evidence"][0]["current_snapshot"]["observation_id"] == "earlier"
        assert context["sector_inputs"][0]["source"]["observation_id"] == "earlier"
        assert registry.get_shared_reference_data("xlk")["sections"]["fund_info"]["holdingsCount"] == 7
        assert registry.get_shared_reference_data("xlk", as_of=datetime.fromisoformat("2026-09-04T00:00:00+00:00"))["sections"] == {}
