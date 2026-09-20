from datetime import date

import pytest
from investment_studio_instrument_core.db_models import Instrument
from sqlalchemy import select

from portfolio_app.services import holdings_workspace as workspace
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import performance


@pytest.mark.parametrize("watermark", ["market_data_updated_at", "calculation_inputs_updated_at"])
def test_detail_cache_observes_source_changes_within_ttl(client, monkeypatch, watermark):
    with get_session_factory()() as session:
        instrument_id = session.scalar(select(Instrument.instrument_id).order_by(Instrument.instrument_id).limit(1))
    calls = []

    def load(ids):
        calls.append(tuple(ids))
        return {instrument_id: {"version": len(calls)}}

    monkeypatch.setattr(performance, "get_registry_instrument_details", load)
    monkeypatch.setattr(performance, "monotonic", lambda: 0.0)
    performance._clear_calculation_instrument_detail_cache()
    assert performance._get_calculation_instrument_details([instrument_id])[instrument_id]["version"] == 1
    with get_session_factory()() as session:
        setattr(session.get(Instrument, instrument_id), watermark, "2026-09-06T10:00:00+00:00")
        session.commit()
    assert performance._get_calculation_instrument_details([instrument_id])[instrument_id]["version"] == 2
    assert len(calls) == 2


def test_details_changed_while_loading_are_not_cached(monkeypatch):
    source = {"generation": 1}
    calls = []
    monkeypatch.setattr(performance, "_calculation_instrument_source_generation", lambda _ids: (source["generation"],))

    def load(ids):
        calls.append(tuple(ids))
        old = source["generation"]
        source["generation"] = 2
        return {"asset": {"version": old}}

    monkeypatch.setattr(performance, "get_registry_instrument_details", load)
    performance._clear_calculation_instrument_detail_cache()
    assert performance._get_calculation_instrument_details(["asset"])["asset"]["version"] == 1
    assert performance._get_calculation_instrument_details(["asset"])["asset"]["version"] == 2
    assert len(calls) == 2


def test_holdings_cache_builder_reads_transactions_after_generation_boundary(monkeypatch):
    source = {"generation": 1}
    monkeypatch.setattr(workspace, "_require_portfolio", lambda *_a, **_k: {"portfolio_id": "p", "as_of_date": "2026-09-06"})
    monkeypatch.setattr(workspace, "list_transactions", lambda *_a, **_k: [{"generation": source["generation"]}])
    monkeypatch.setattr(workspace, "get_portfolio_risk_policy", lambda _pid: {})
    monkeypatch.setattr(workspace, "analytics_policy_version", lambda _pid: 1)
    monkeypatch.setattr(workspace, "_build_holdings_analytics_workspace", lambda _p, **kw: {"generation": kw["transactions"][0]["generation"]})
    monkeypatch.setattr(workspace, "_compact_holdings_workspace", lambda value: value)
    monkeypatch.setattr(workspace, "_public_holdings_workspace_response", lambda value, **_kw: value)

    def cache(_pid, **kwargs):
        source["generation"] = 2
        return kwargs["builder"]()

    monkeypatch.setattr(workspace, "get_cached_holdings_analytics_workspace", cache)
    assert workspace.holdings_workspace("p")["generation"] == 2


def test_risk_basis_cache_builder_reads_observations_after_generation_boundary(monkeypatch):
    source = {"generation": 1}
    monkeypatch.setattr(workspace, "get_cached_materialized_holdings_workspace", lambda *_a, **_kw: {"ids": [str(source["generation"])]})
    monkeypatch.setattr(workspace, "_instrument_ids_from_holdings_workspace", lambda value: value["ids"])
    monkeypatch.setattr(workspace, "get_registry_instrument_details", lambda ids: {key: {"generation": source["generation"]} for key in ids})
    monkeypatch.setattr(workspace, "calculation_frequency_profile_for_instruments", lambda ids, **kw: {"generation": kw["detail_loader"](ids[0])["generation"]})
    monkeypatch.setattr(workspace, "_holdings_workspace_has_market_profile", lambda *_a, **_kw: True)
    monkeypatch.setattr(workspace, "_materialized_holdings_workspace_response", lambda _value, **kw: kw["risk_basis_profile"])
    monkeypatch.setattr(workspace, "_enrich_holdings_analytics_scope", lambda value, **_kw: value)
    monkeypatch.setattr(workspace, "enrich_holdings_forward_risk", lambda value, **_kw: value)

    def cache(_pid, **kwargs):
        source["generation"] = 2
        return kwargs["builder"]()

    monkeypatch.setattr(workspace, "get_cached_portfolio_risk_basis", cache)
    assert workspace._build_holdings_analytics_workspace(
        {"portfolio_id": "p"}, resolved_as_of_date=date(2026, 9, 6),
        transactions=[], risk_policy={}, include_details=False,
    )["generation"] == 2
