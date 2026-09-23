"""A risk scope reuses retained comparisons without changing their financial meaning."""
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from watchlist_app.db.models import (
    InstrumentChartReadModel, InstrumentDetail, InstrumentManualProfile,
    InstrumentPerformanceReadModel,
)
from watchlist_app.services.risk_performance import performance_context, performance_evidence


TARGETS = ["target-a", "target-b", "target-missing"]


@pytest.fixture
def retained_evidence():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (InstrumentDetail, InstrumentManualProfile, InstrumentChartReadModel,
                  InstrumentPerformanceReadModel):
        model.__table__.create(engine)
    stamp = datetime(2026, 9, 4, tzinfo=UTC)
    ids = [*TARGETS, "peer-same", "peer-missing", "explicit", "wrong-frequency",
           "wrong-currency", "wrong-return", "unrelated"]
    with Session(engine) as session:
        session.add_all(InstrumentDetail(instrument_id=iid, instrument_name=iid.upper(),
            instrument_type="private_fund", detail_view_type="private_fund") for iid in ids)
        session.flush()
        session.add_all([
            InstrumentManualProfile(instrument_id="target-a", updated_at=stamp, nav_settings_json={
                "default_benchmark_instrument_id": "peer-same",
                "peer_baseline_instrument_ids": ["peer-same", "explicit", "wrong-frequency",
                    "wrong-currency", "wrong-return", "absent"]}),
            InstrumentManualProfile(instrument_id="target-missing", updated_at=stamp,
                nav_settings_json={"default_benchmark_instrument_id": "absent"}),
        ])
        for iid in set(ids) - {"target-missing", "peer-missing"}:
            series = {"currency": "CNY", "frequency": {"resolved_frequency": "weekly"},
                "metadata": {"return_kind": "total_return", "return_series_status": "complete",
                    "quote_basis": "total_return_nav"},
                "points": [{"date": day, "value": value} for day, value in [
                    ("2026-06-26", 1), ("2026-07-31", 1.01),
                    ("2026-08-28", 1.04), ("2026-09-04", 1.05)]]}
            if iid == "target-a":
                series["points"] = [{"date": day, "value": value} for day, value in [
                    ("2026-06-19", .9), ("2026-06-26", 1), ("2026-07-01", 0),
                    ("2026-07-31", .98), ("2026-08-28", .95), ("2026-09-04", .96)]]
            if iid == "wrong-frequency":
                series["frequency"] = {"resolved_frequency": "daily"}
            if iid == "wrong-currency":
                series["currency"] = "USD"
            if iid == "wrong-return":
                series["metadata"]["return_kind"] = "price_return"
            session.add(InstrumentChartReadModel(instrument_id=iid,
                payload_json={"research_returns": series}, data_freshness_status="stale" if iid == "target-a" else "current",
                source_cutoff_at=stamp))
        session.add(InstrumentPerformanceReadModel(instrument_id="target-a", data_freshness_status="current",
            source_cutoff_at=stamp, payload_json={"trailing_returns": [{"window": "1m", "value": -2}],
                "snapshot_metadata": {"observations": 5, "calculation_basis": "retained"}}))
        session.commit()
    node = SimpleNamespace(node_id="comparable-fund-leaf", is_leaf=True, instrument_type="private_fund",
        path_node_ids_json=["fund", "comparable-fund-leaf"], path_labels_json=["Fund", "Comparable"])
    assigned = {iid: node for iid in ("target-a", "target-b", "peer-same", "peer-missing", "unrelated")}
    scope = {"node_by_id": {node.node_id: node}, "assigned_node_by_asset": assigned,
        "attributes_by_asset": {iid: {"primary_geographic_exposure": "US" if iid == "unrelated" else "CN"}
                                for iid in assigned}}
    try:
        yield engine, scope
    finally:
        engine.dispose()


def test_batch_context_preserves_exact_evidence_and_missing_comparators(retained_evidence):
    engine, scope = retained_evidence
    with Session(engine) as session:
        expected = {iid: performance_evidence(session, iid, peer_scope=scope) for iid in TARGETS}
    with Session(engine) as session:
        queries = []
        event.listen(session, "do_orm_execute", lambda state: queries.append(state.statement))
        context = performance_context(session, TARGETS, peer_scope=scope)
        assert len(queries) == 4
        assert "unrelated" not in context.records[InstrumentChartReadModel]
        for _ in range(2):
            actual = {iid: performance_evidence(session, iid, context=context) for iid in TARGETS}
            assert actual == expected
        assert len(queries) == 4, "Repeated peers and missing rows must not trigger individual reads"
        assert not session.dirty
    target = actual["target-a"]
    assert target["freshness"] == "stale"
    assert target["trailing_returns_metadata"]["freshness"] == "current"
    assert target["trailing_negative_completed_observed_months"] == 2
    assert [(row["instrument_id"], row["role"]) for row in target["comparisons"]] == [
        ("peer-same", "configured_benchmark"), ("target-b", "taxonomy_peer"), ("explicit", "configured_peer")]
    comparison = target["comparisons"][0]["comparison"]
    assert comparison["dates"] == ["2026-06-26", "2026-07-31", "2026-08-28", "2026-09-04"]
    assert comparison["rows"][0]["excess_return_pp"] == pytest.approx(-9)
    assert all(any(reason in row for row in target["limitations"])
               for reason in ("频率不同", "币种不同", "收益口径不同", "absent", "PEER-MISSING"))
    assert actual["target-missing"]["available"] is False
    assert actual["target-missing"]["sample_return_pct"] is None
    assert actual["target-b"]["trailing_returns_metadata"] is None


def test_performance_context_does_not_cache_across_reads(retained_evidence):
    engine, scope = retained_evidence
    with Session(engine) as session:
        context = performance_context(session, ["target-a"], peer_scope=scope)
        before = performance_evidence(session, "target-a", context=context)
    with Session(engine) as session:
        chart = session.get(InstrumentChartReadModel, "peer-same")
        changed = deepcopy(chart.payload_json)
        changed["research_returns"]["points"][-1]["value"] = 1.2
        chart.payload_json = changed
        session.commit()
    with Session(engine) as session:
        context = performance_context(session, ["target-a"], peer_scope=scope)
        after = performance_evidence(session, "target-a", context=context)
    assert before["sample_return_pct"] == after["sample_return_pct"]
    assert before["comparisons"][0]["comparison"] != after["comparisons"][0]["comparison"]
    assert after["comparisons"][0]["comparison"]["rows"][0]["excess_return_pp"] == pytest.approx(-24)


def test_empty_context_reads_nothing_and_does_not_turn_outside_targets_into_missing(retained_evidence):
    engine, _ = retained_evidence
    with Session(engine) as session:
        queries = []
        event.listen(session, "do_orm_execute", lambda state: queries.append(state.statement))
        context = performance_context(session, [])
        with pytest.raises(ValueError, match="outside this performance evidence context"):
            performance_evidence(session, "target-a", context=context)
        assert not queries
