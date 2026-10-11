"""A risk scope reuses retained comparisons without changing their financial meaning."""
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from investment_studio_instrument_core import build_return_intervals
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from watchlist_app.db.models import (
    InstrumentChartReadModel, InstrumentDetail, InstrumentManualProfile,
    InstrumentPerformanceReadModel,
)
from watchlist_app.services.risk_performance import performance_context, performance_evidence
from watchlist_app.db.models.recalc import RecalcJob


TARGETS = ["target-a", "target-b", "target-missing"]


@pytest.fixture
def retained_evidence(monkeypatch):
    import watchlist_app.services.risk_performance as performance
    monkeypatch.setattr(performance, "datetime", SimpleNamespace(now=lambda _: datetime(2026, 9, 4, 12, tzinfo=UTC)))
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (InstrumentDetail, InstrumentManualProfile, InstrumentChartReadModel,
                  InstrumentPerformanceReadModel, RecalcJob):
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
            series = {"currency": "CNY", "frequency": {"resolved_frequency": "monthly"},
                "metadata": {"return_kind": "total_return", "return_series_status": "complete",
                    "quote_basis": "total_return_nav"},
                "points": [{"date": day, "value": value} for day, value in [
                    ("2026-06-30", 1), ("2026-07-31", 1.01),
                    ("2026-08-31", 1.04), ("2026-09-04", 1.05)]]}
            if iid == "target-a":
                series["points"] = [{"date": day, "value": value} for day, value in [
                    ("2026-06-19", .9), ("2026-06-30", 1),
                    ("2026-07-31", .98), ("2026-08-31", .95), ("2026-09-04", .96)]]
            if iid == "wrong-frequency":
                series["frequency"] = {"resolved_frequency": "daily"}
            if iid == "wrong-currency":
                series["currency"] = "USD"
            if iid == "wrong-return":
                series["metadata"]["return_kind"] = "price_return"
            series["intervals"] = build_return_intervals(series["points"])
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
        assert len(queries) == 8  # Four batch loads plus before/after target and comparator fences.
        assert "unrelated" not in context.records[InstrumentChartReadModel]
        for _ in range(2):
            actual = {iid: performance_evidence(session, iid, context=context) for iid in TARGETS}
            assert actual == expected
        assert len(queries) == 8, "Repeated peers and missing rows must not trigger individual reads"
        assert not session.dirty
    target = actual["target-a"]
    assert target["freshness"] == "stale"
    assert target["trailing_returns_metadata"]["freshness"] == "current"
    assert target["trailing_negative_completed_observed_months"] == 2
    assert [(row["instrument_id"], row["role"]) for row in target["comparisons"]] == [
        ("peer-same", "configured_benchmark"), ("target-b", "taxonomy_peer"), ("explicit", "configured_peer")]
    comparison = target["comparisons"][0]["comparison"]
    assert comparison["dates"] == ["2026-06-30", "2026-07-31", "2026-08-31", "2026-09-04"]
    assert comparison["rows"][0]["excess_return_pp"] == pytest.approx(-9)
    assert all(any(reason in row for row in target["limitations"])
               for reason in ("频率不同", "币种不同", "收益口径不同", "absent", "PEER-MISSING"))
    assert actual["target-missing"]["available"] is False
    assert actual["target-missing"]["sample_return_pct"] is None
    assert actual["target-missing"]["latest_completed_observed_month"] is None
    assert actual["target-missing"]["latest_negative_completed_observed_month"] is None
    assert actual["target-b"]["trailing_returns_metadata"] is None
    assert actual["target-b"]["latest_negative_completed_observed_month"] is None


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


def test_monthly_risk_evidence_keeps_new_segment_and_never_pairs_different_months(retained_evidence):
    engine, scope = retained_evidence
    with Session(engine) as session:
        for iid, reset_index in [("target-a", 1), ("peer-same", 2)]:
            chart = session.get(InstrumentChartReadModel, iid)
            data = deepcopy(chart.payload_json)
            series = data["research_returns"]
            series["metadata"].update(return_series_status="partial", return_segment_breaks=[{}])
            series["points"] = [{"date": day, "value": value, "segment_id": "old" if i < reset_index else "new"}
                for i, (day, value) in enumerate([("2026-06-30", 100), ("2026-07-31", 1), ("2026-08-31", .95), ("2026-09-04", .96)])]
            series["intervals"] = build_return_intervals(series["points"], break_dates=[series["points"][reset_index]["date"]])
            chart.payload_json = data
        session.commit()
        result = performance_evidence(session, "target-a", peer_scope=scope)
    assert result["sample_return_pct"] is None
    assert [row["month"] for row in result["monthly_periods"]] == ["2026-07", "2026-08", "2026-09"]
    assert result["monthly_periods"][0]["status"] == "unavailable"
    assert result["monthly_periods"][1]["return_pct"] == pytest.approx(-5)
    comparison = next(row for row in result["comparisons"] if row["instrument_id"] == "peer-same")
    assert [row["status"] for row in comparison["monthly_periods"]] == ["unavailable", "unavailable", "complete"]
    assert comparison["monthly_periods"][-1]["excess_return_pp"] == 0


def test_invalid_retained_observation_is_not_deleted_to_create_an_interval(retained_evidence):
    engine, scope = retained_evidence
    with Session(engine) as session:
        chart = session.get(InstrumentChartReadModel, "target-a")
        changed = deepcopy(chart.payload_json)
        changed["research_returns"]["points"][2]["value"] = 0
        chart.payload_json = changed
        session.commit()
        result = performance_evidence(session, "target-a", peer_scope=scope)
    assert not result["available"]
    assert result["sample_return_pct"] is None
    assert result["monthly_periods"] == []


def test_negative_month_streak_cannot_jump_an_unavailable_latest_completed_month():
    from watchlist_app.services.risk_performance import _negative_month_count
    rows = [{"month": "2026-07", "return_pct": -5, "latest_month_to_date": False},
            {"month": "2026-09", "return_pct": 1, "latest_month_to_date": True}]
    assert _negative_month_count(rows, "2026-09") == 0
    rows.insert(1, {"month": "2026-08", "return_pct": -2, "latest_month_to_date": False})
    assert _negative_month_count(rows, "2026-09") == 2


@pytest.mark.parametrize("september_unavailable", [False, True])
def test_latest_completed_month_is_not_replaced_by_an_older_negative_month(retained_evidence, september_unavailable, monkeypatch):
    import watchlist_app.services.risk_performance as performance
    monkeypatch.setattr(performance, "datetime", SimpleNamespace(now=lambda _: datetime(2026, 10, 8, 12, tzinfo=UTC)))
    engine, scope = retained_evidence
    with Session(engine) as session:
        chart = session.get(InstrumentChartReadModel, "target-a")
        changed = deepcopy(chart.payload_json)
        series = changed["research_returns"]
        series["points"] = [{"date": day, "value": value, "segment_id":
            "new" if september_unavailable and day >= "2026-09-30" else "old"} for day, value in [
                ("2026-05-31", 100), ("2026-06-30", 92.73), ("2026-07-31", 99),
                ("2026-08-31", 101), ("2026-09-30", 106), ("2026-10-08", 108)]]
        if september_unavailable:
            series["metadata"].update(return_series_status="partial", return_segment_breaks=[{}])
        series["intervals"] = build_return_intervals(series["points"],
            break_dates=["2026-09-30"] if september_unavailable else [])
        chart.payload_json = changed
        session.commit()
        evidence = performance_evidence(session, "target-a", peer_scope=scope)
    latest = evidence["latest_completed_observed_month"]
    last_negative = evidence["latest_negative_completed_observed_month"]
    assert latest["month"] == "2026-09" and latest["latest_month_to_date"] is False
    assert last_negative["month"] == "2026-06" and last_negative["return_pct"] == pytest.approx(-7.27)
    assert evidence["monthly_periods"][-1]["month"] == "2026-10"
    assert evidence["monthly_periods"][-1]["latest_month_to_date"] is True
    assert evidence["trailing_negative_completed_observed_months"] == 0
    if september_unavailable:
        assert latest["return_pct"] is None and latest["status"] == "unavailable" and latest["limitation"]
    else:
        assert latest["return_pct"] == pytest.approx((106 / 101 - 1) * 100) and latest["status"] == "complete"


def test_monthly_comparison_does_not_move_a_missing_month_end_to_a_common_earlier_date(retained_evidence):
    engine, scope = retained_evidence
    with Session(engine) as session:
        chart = session.get(InstrumentChartReadModel, "peer-same")
        changed = deepcopy(chart.payload_json)
        series = changed["research_returns"]
        series["points"] = [point for point in series["points"] if point["date"] != "2026-08-31"]
        series["intervals"] = build_return_intervals(series["points"])
        chart.payload_json = changed
        session.commit()
        evidence = performance_evidence(session, "target-a", peer_scope=scope)
    comparison = next(row for row in evidence["comparisons"] if row["instrument_id"] == "peer-same")
    assert "缺少整期披露" in comparison["comparison"]["excluded"][0]["reason"]
    july, august, september = comparison["monthly_periods"]
    assert july["status"] == "complete"
    for row in (august, september):
        assert row["excess_return_pp"] is None
        assert row["status"] == "unavailable"
        assert row["limitation"] == "月度实际起止日期不同或缺失"
