"""Admission reads bounded registration facts without changing the public scope."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from investment_studio_instrument_core.db_models import Instrument, InstrumentReferenceSnapshot
from watchlist_app.db.models import (
    InstrumentAttributeValue, InstrumentDetail, InstrumentTaxonomyAssignment, InstrumentTaxonomyNode,
)
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.reference_data.instrument_taxonomy import INSTRUMENT_TAXONOMY_CODE
from watchlist_app.services import research_policy as policy
from watchlist_app.services.research_applicability import investment_research_supported
from watchlist_app.services.research_dossier import _registration
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env
from .test_research_policy import invested


CASES = [
    ("equity", {"fund_info": {"assetClass": "Bond"}}, None, True),
    ("private_fund", {"fund_info": {"assetClass": "Equity"}}, None, False),
    ("crypto", None, None, False),
    ("public_fund", None, None, False),
    ("public_fund", {}, "fund-public-equity-policy-fixture", True),
    ("public_fund", {}, "fund-public-allocation-equity-biased", True),
    ("public_fund", {}, "fund-public-fof-equity", True),
    ("public_fund", {}, "fund-public-bond-policy-fixture", False),
    ("etf", None, None, True),
    ("index", {}, None, True),
    ("etf", {}, "etf-commodity-policy-fixture", False),
    ("index", {}, "index-fixed-income-policy-fixture", False),
    ("etf", {"fund_info": {"assetClass": " Equity "}}, "etf-bond-policy-fixture", True),
    ("etf", {"fund_info": {"assetClass": "Commodities", "asset_class": "Equity"}}, None, False),
    ("etf", {"fund_info": {"assetClass": "", "asset_class": "股票"}}, None, True),
    ("index", {"fund_info": {"asset_class": None}, "index_info": {"asset_class": "债券"}}, None, False),
    ("etf", {"fund_info": {"assetClass": 0, "asset_class": False}, "index_info": {"asset_class": "stocks"}}, None, True),
    ("etf", {"fund_info": {"assetClass": " "}, "index_info": {"asset_class": "Bond"}}, None, True),
    ("public_fund", {}, " fund-public-equity-space-fixture ", True),
    ("etf", {"fund_info": {"assetClass": "Equity"}, "profile": {"retained": "original\x00text"}}, None, True),
]


def assert_registration_projection(monkeypatch):
    with get_session_factory()() as session:
        baseline = policy.research_capability(session)["eligible_instruments"]
        for index, (kind, sections, node_id, _) in enumerate(CASES):
            iid = f"policy-projection-{index:02d}"
            session.add(Instrument(instrument_id=iid, instrument_name=iid, instrument_type=kind,
                currency="USD", exchange_code="XNYS" if kind in {"equity", "etf"} else None,
                quote_selection_policy_json={"total_return": ["adjusted_close"]},
                source_settings_json={}, lifecycle_state_json={"status": "active"}))
            session.add(InstrumentDetail(instrument_id=iid, instrument_name=iid,
                instrument_type=kind, detail_view_type=kind, metadata_json={}))
            session.flush()
            session.add(InstrumentAttributeValue(instrument_id=iid, attribute_key="coverage_status",
                value_json="Invested", adopted_at=datetime.now(UTC)))
            if sections is not None:
                session.add(InstrumentReferenceSnapshot(instrument_id=iid,
                    value_json={"sections": sections, "unrelated_original": "not needed for admission" * 1000}))
            if node_id:
                if session.get(InstrumentTaxonomyNode, node_id) is None:
                    session.add(InstrumentTaxonomyNode(node_id=node_id, taxonomy_code=INSTRUMENT_TAXONOMY_CODE,
                        instrument_type=kind, label=node_id, level_index=1))
                    session.flush()
                session.add(InstrumentTaxonomyAssignment(instrument_id=iid,
                    taxonomy_code=INSTRUMENT_TAXONOMY_CODE, node_id=node_id, assigned_at=datetime.now(UTC)))
            elif index == 8:
                session.add(InstrumentTaxonomyAssignment(instrument_id=iid,
                    taxonomy_code=INSTRUMENT_TAXONOMY_CODE, node_id=None, assigned_at=datetime.now(UTC)))
            if index == 9:
                # Another taxonomy's assignment must neither exclude this
                # instrument nor duplicate its canonical registration row.
                session.add(InstrumentTaxonomyNode(node_id="other-taxonomy-bond", taxonomy_code="other",
                    instrument_type=kind, label="Other taxonomy", level_index=1))
                session.flush()
                session.add(InstrumentTaxonomyAssignment(instrument_id=iid, taxonomy_code="other",
                    node_id="other-taxonomy-bond", assigned_at=datetime.now(UTC)))
        session.commit()
        expected = list(baseline)
        for index, (_, _, _, allowed) in enumerate(CASES):
            iid = f"policy-projection-{index:02d}"
            # Compare with the actual dossier registration, including source
            # precedence, missing snapshots, taxonomy normalization and NUL text.
            actual = investment_research_supported(_registration(session, session.get(InstrumentDetail, iid)))
            assert actual is allowed
            if actual:
                expected.append(iid)

    def no_full_registration(*_):
        pytest.fail("Policy must not materialize each candidate's complete dossier registration")
    monkeypatch.setattr("watchlist_app.services.research_dossier._registration", no_full_registration)
    queries = []
    def count_queries(_conn, _cursor, statement, *_):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)
    engine = get_engine()
    event.listen(engine, "before_cursor_execute", count_queries)
    try:
        with get_session_factory()() as session:
            complete = policy.research_capability(session)
        assert len(queries) == 2
        assert complete["eligible_instruments"] == sorted(expected)
        assert [row["instrument_id"] for row in complete["instruments"]] == sorted(expected)
        queries.clear()
        with get_session_factory()() as session:
            scoped = policy.research_capability(session, "policy-projection-00")
        assert len(queries) == 2
        assert scoped == {**complete, "instruments": [{"instrument_id": "policy-projection-00",
            "instrument_type": "equity", "eligible": True, "reason": None}]}
        with get_session_factory()() as session:
            excluded = policy.research_capability(session, "policy-projection-01")
            missing = policy.research_capability(session, "missing-policy-fixture")
            risk = policy.research_capability(session, "policy-projection-01", risk=True)
        assert excluded["eligible_instruments"] == missing["eligible_instruments"] == sorted(expected)
        assert excluded["instruments"][0]["instrument_type"] == "private_fund"
        assert excluded["instruments"][0]["eligible"] is False
        assert missing["instruments"][0]["instrument_type"] is None
        assert missing["instruments"][0]["eligible"] is False
        assert set(risk["eligible_instruments"]) >= {f"policy-projection-{index:02d}" for index in range(len(CASES))}
        assert risk["instruments"][0]["eligible"] is True
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)


def test_registration_projection_matches_full_dossier_with_constant_queries(client, monkeypatch):
    assert_registration_projection(monkeypatch)


@pytest.mark.postgresql_integration
def test_postgres_registration_projection_matches_full_dossier(postgres_watchlist_env, monkeypatch):
    assert_registration_projection(monkeypatch)


def test_single_instrument_policy_and_sector_routes_keep_global_and_scoped_contracts(invested, monkeypatch):
    def no_full_registration(*_):
        pytest.fail("Reading policy must not load full registrations")
    monkeypatch.setattr("watchlist_app.services.research_dossier._registration", no_full_registration)
    full = invested.get("/api/research-policy").json()
    scoped = invested.get("/api/research-policy?instrument_id=xlk").json()
    assert full["eligible_instruments"] == scoped["eligible_instruments"] == ["xlf", "xlk"]
    assert scoped["instruments"] == [row for row in full["instruments"] if row["instrument_id"] == "xlk"]
    sector = invested.get("/api/sector-research?instrument_id=xlk&include_events=false")
    assert sector.status_code == 200, sector.text
    assert sector.json()["research_enabled"] is True
    assert [row["instrument_id"] for row in sector.json()["sectors"]] == ["xlk"]
    assert sector.json()["events"] == []
