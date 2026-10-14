from copy import deepcopy
from datetime import UTC, datetime, timedelta
from math import sqrt
from statistics import stdev

import pytest

from watchlist_app.services.risk_monitoring import build_monitoring_snapshot
from .test_price_risk import series, observe, seed, workspace


def calculate(source, *, clock=None, kind="etf", instrument_id="sample"):
    source = deepcopy(source)
    source["wealth_points"] = source.pop("points")
    source["metadata"]["return_measure"] = "simple"
    clock = clock or datetime.fromisoformat(source["wealth_points"][-1]["date"]).replace(tzinfo=UTC) + timedelta(days=1, hours=12)
    return build_monitoring_snapshot({"instrument_id": instrument_id, "return_series": source},
        instrument_type=kind, source_settings={}, risk_payload={"current_drawdown": -1},
        input_snapshot_version="retained-input", calculated_at=clock, source_cutoff_at=clock)


def metric(snapshot, identifier):
    return next(row for row in snapshot["metrics"] if row["metric_id"] == identifier)


def test_fixed_metrics_use_actual_window_simple_returns_and_all_downside_observations():
    source = series([100 * (1.01 if i % 2 else 1) for i in range(100)])
    snapshot = calculate(source)
    row = metric(snapshot, "rolling_volatility")
    points = [point for point in source["points"] if point["date"] >= row["window_start_date"]]
    returns = [b["value"] / a["value"] - 1 for a, b in zip(points, points[1:])]
    factor = row["definition"]["annualization_factor"]
    assert row["value"] == pytest.approx(stdev(returns) * sqrt(factor) * 100)
    downside = metric(snapshot, "downside_deviation")
    assert downside["value"] == pytest.approx(sqrt(sum(min(r, 0) ** 2 for r in returns) / len(returns) * factor) * 100)
    assert row["metric_snapshot_id"] == downside["metric_snapshot_id"] == snapshot["metric_snapshot_id"]
    assert len(row["series"]) == len(source["points"])
    assert metric(snapshot, "holding_period_max_drawdown")["status"] == "not_applicable"


def test_positive_path_has_zero_downside_and_old_gap_does_not_disable_later_rolling_window():
    source = series([100 * 1.001 ** i for i in range(100)])
    source["intervals"][5].update(status="unavailable", reason="missing_market_session")
    snapshot = calculate(source)
    assert metric(snapshot, "current_drawdown")["value"] is None
    assert metric(snapshot, "downside_deviation")["value"] == 0
    assert metric(snapshot, "rolling_volatility")["value"] is not None
    assert metric(snapshot, "current_drawdown")["series"][-1]["value"] is None
    source["intervals"][-1]["status"] = "unavailable"
    assert metric(calculate(source), "rolling_volatility")["value"] is None


def test_snapshot_identity_does_not_change_on_clock_only_recalculation_but_does_on_input_correction():
    source = series([100] * 100)
    clock = datetime.fromisoformat(source["points"][-1]["date"]).replace(tzinfo=UTC) + timedelta(days=1, hours=12)
    first = calculate(source, clock=clock)
    second = calculate(source, clock=clock + timedelta(minutes=1))
    assert first["metric_snapshot_id"] == second["metric_snapshot_id"]
    source["points"][-5]["value"] = 99
    assert calculate(source, clock=clock)["metric_snapshot_id"] != first["metric_snapshot_id"]


def test_event_driven_and_unconfirmed_economic_basis_do_not_invent_annualized_risk():
    source = series([100] * 100, resolved_frequency="event_driven")
    assert metric(calculate(source), "rolling_volatility")["value"] is None
    source["metadata"]["return_kind"] = "price_return"
    assert metric(calculate(source), "current_drawdown")["value"] is None


def test_severe_rules_are_explicit_versioned_and_preserve_initial_calibration(client):
    seed(client)
    observe(series([100] * 80))
    before = workspace(client)["instruments"][0]
    calibrated = before["price_risk_calibration"]
    response = client.put("/api/risk/rules/sxv264", json={"period_limits": {"day": 2},
        "severities": {"day": "severe"}, "change_reason": "明确严重的日跌幅约束"})
    assert response.status_code == 200
    observe(series([100] * 79 + [95]))
    result = workspace(client)
    asset = result["instruments"][0]
    rule = next(row for row in asset["price_rule_summary"]["rules"] if row["key"] == "day")
    assert rule["mandatory_notice"] and rule["severity"] == "severe"
    assert asset["price_risk_calibration"]["sample_start"] == calibrated["sample_start"]
    history = asset["price_rule_summary"]["rule_history"]
    assert history[-1]["previous"]["period_limits"] == before["period_limits"]
    assert history[-1]["reason"] == "明确严重的日跌幅约束"
    case = next(row for row in result["cases"] if row["signal"] == "period_loss" and row["trigger_active"])
    assert case["evidence_json"]["mandatory_notice"] and case["severity"] == "severe"
    revision = asset["rule_version"]
    observe(series([100] * 79 + [94]))
    assert workspace(client)["instruments"][0]["rule_version"] == revision


def test_rule_save_rejects_stale_editor_and_preview_has_no_side_effect(client):
    seed(client)
    observe(series([100] * 80))
    version = workspace(client)["instruments"][0]["rule_version"]
    assert client.put("/api/risk/rules/sxv264", json={"drawdown_limit": 5, "expected_rule_version": version}).status_code == 200
    assert client.put("/api/risk/rules/sxv264", json={"drawdown_limit": 8, "expected_rule_version": version}).status_code == 409
    before = workspace(client)["instruments"][0]
    preview = client.post("/api/risk/rules/sxv264/preview", json={"drawdown_limit": 9})
    assert preview.status_code == 200
    after = workspace(client)["instruments"][0]
    assert after["drawdown_limit"] == before["drawdown_limit"] == 5
    assert after["rule_version"] == before["rule_version"]


def test_short_new_sample_keeps_unavailable_severe_horizon_independent_of_other_trigger(client):
    seed(client)
    observe(series([100] * 80))
    client.put("/api/risk/rules/sxv264", json={"period_limits": {"day": 2, "quarter": 3}, "severities": {"quarter": "severe"}})
    observe(series([100] * 79 + [95]))
    original = next(row for row in workspace(client)["cases"] if row["signal"] == "period_loss" and row["trigger_active"])
    observe(series([100] * 29 + [95]))
    current = next(row for row in workspace(client)["cases"] if row["signal"] == "period_loss" and row["trigger_active"])
    assert current["case_id"] == original["case_id"] and current["severity"] == "severe"
    retained = next(row for row in current["evidence_json"]["periods"] if row["period"] == "quarter")
    assert retained["evaluation_status"] == "unavailable"
    assert retained["end_date"] != current["observed_on"]
    assert current["evidence_json"]["mandatory_notice"] is True


def test_historical_preview_uses_unrounded_signed_values_and_keeps_missing_observations():
    from watchlist_app.services.risk_monitoring import preview_rule_changes
    snapshot = calculate(series([100] * 79 + [95.00001]))
    preview = preview_rule_changes(snapshot, {"period_limits": {"day": 5}}, {"period_limits": {"day": 4.99}})
    day = next(row for row in preview["rules"] if row["key"] == "day")
    assert day["previous_trigger_count"] == 0 and day["proposed_trigger_count"] == 1
    assert day["unavailable_observations"] == 1


def test_monitoring_endpoints_read_current_and_retained_snapshots_without_recalculation(client, monkeypatch):
    from watchlist_app.db.models import InstrumentRiskReadModel
    from watchlist_app.db.models.analytics import RiskSnapshot
    from watchlist_app.db.session import get_session_factory
    import watchlist_app.services.risk_monitoring as service
    seed(client)
    source = {**series([100] * 80), "currency": "USD"}
    clock = datetime.now(UTC)
    snapshot = calculate(source, clock=clock, instrument_id="sxv264")
    with get_session_factory()() as session:
        record = session.get(InstrumentRiskReadModel, "sxv264")
        record.payload_json = {"monitoring": snapshot, "monitoring_summary": service.monitoring_summary(snapshot),
            "metric_snapshot_id": snapshot["metric_snapshot_id"]}
        record.last_recalculated_at = clock
        session.add(RiskSnapshot(snapshot_id="retained-monitoring-test", instrument_id="sxv264",
            as_of_date=datetime.fromisoformat(snapshot["as_of"]).date(), source_cutoff_at=clock,
            methodology_version=service.MONITORING_DEFINITION_VERSION, input_hash=snapshot["input_snapshot_version"],
            calculated_at=clock, is_current=False, monitoring_json=snapshot))
        session.commit()
    monkeypatch.setattr(service, "build_monitoring_snapshot", lambda *args, **kwargs: pytest.fail("Read must not calculate"))
    current = client.get("/api/instruments/sxv264/monitoring")
    assert current.status_code == 200 and current.json()["metric_snapshot_id"] == snapshot["metric_snapshot_id"]
    for risk in (workspace(client)["instruments"][0]["risk"], client.get("/api/instruments/sxv264/risk").json()):
        assert "monitoring" not in risk
        assert risk["monitoring_summary"]["metric_snapshot_id"] == snapshot["metric_snapshot_id"]
        assert all("series" not in row for row in risk["monitoring_summary"]["metrics"])
    retained = client.get("/api/instruments/sxv264/monitoring", params={"metric_snapshot_id": snapshot["metric_snapshot_id"]})
    assert retained.status_code == 200 and retained.json() == snapshot
    assert client.get("/api/instruments/sxv264/monitoring", params={"metric_snapshot_id": "other-instrument-snapshot"}).status_code == 404


def test_retained_snapshot_keeps_exact_outputs_and_identity_without_daily_curve_duplication():
    from watchlist_app.services.risk_monitoring import retained_monitoring_snapshot
    current = calculate(series([100 * 1.001 ** i for i in range(100)]))
    retained = retained_monitoring_snapshot(current)
    assert retained["metric_snapshot_id"] == current["metric_snapshot_id"]
    assert retained["input_snapshot_version"] == current["input_snapshot_version"]
    assert retained["tail_risk_options"] == current["tail_risk_options"]
    assert retained["path_series_status"] == "not_retained" and "period_series" not in retained
    for before, after in zip(current["metrics"], retained["metrics"], strict=True):
        assert {key: value for key, value in before.items() if key != "series"} == {
            key: value for key, value in after.items() if key not in {"series", "series_status"}}
        assert after["series"] == [] and after["series_status"] == "not_retained"
    assert metric(current, "rolling_volatility")["series"]


@pytest.mark.parametrize("corrected_last_observation", [False, True])
def test_tail_trend_keeps_original_reading_when_new_due_session_has_no_source_data(client, corrected_last_observation):
    from watchlist_app.db.models import InstrumentRiskReadModel
    from watchlist_app.db.models.analytics import RiskSnapshot
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.risk_monitoring import retained_monitoring_snapshot
    seed(client)
    clock = datetime.now(UTC)
    current = calculate({**series([100] * 80), "currency": "USD"}, clock=clock, instrument_id="sxv264")
    original_day = datetime.fromisoformat(current["as_of"]).date()
    due_day = original_day + timedelta(days=1)
    previous = deepcopy(current)
    previous["metric_snapshot_id"] = "retained-original-session"
    previous["tail_risk_options"][2].update(window_end_date=original_day.isoformat(), var=.02,
        expected_shortfall=.03, status="ready")
    current["tail_risk_options"][2].update(window_end_date=due_day.isoformat(), var=None,
        expected_shortfall=None, status="unavailable")
    with get_session_factory()() as session:
        record = session.get(InstrumentRiskReadModel, "sxv264")
        record.payload_json = {"monitoring": current}
        record.last_recalculated_at = clock
        if corrected_last_observation:
            removed = deepcopy(previous)
            removed["tail_risk_options"][2]["window_end_date"] = due_day.isoformat()
            session.add(RiskSnapshot(snapshot_id="tail-trend-before-source-correction", instrument_id="sxv264",
                as_of_date=due_day, source_cutoff_at=clock - timedelta(hours=12), calculated_at=clock - timedelta(hours=12),
                methodology_version="test", input_hash="superseded", is_current=False,
                monitoring_json=retained_monitoring_snapshot(removed)))
        for name, snapshot, calculated_at in (("original", previous, clock - timedelta(days=1)), ("missing", current, clock)):
            session.add(RiskSnapshot(snapshot_id=f"tail-trend-{name}", instrument_id="sxv264",
                as_of_date=original_day, source_cutoff_at=calculated_at, calculated_at=calculated_at,
                methodology_version="test", input_hash=name, is_current=False,
                monitoring_json=retained_monitoring_snapshot(snapshot)))
        session.commit()
    result = client.get("/api/instruments/sxv264/monitoring").json()
    assert metric(result, "var")["series"] == [
        {"date": original_day.isoformat(), "value": 2, "status": "ready"},
        {"date": due_day.isoformat(), "value": None, "status": "unavailable"},
    ]


def test_trigger_version_ignores_numerical_refresh_and_pm_followup_but_tracks_rule_change(client):
    seed(client)
    observe(series([100] * 80))
    client.put("/api/risk/rules/sxv264", json={"period_limits": {"day": 2}, "severities": {"day": "severe"}})
    observe(series([100] * 79 + [95]))
    case = next(row for row in workspace(client)["cases"] if row["signal"] == "period_loss" and row["trigger_active"])
    version = case["evidence_json"]["trigger_version"]
    assert client.put(f"/api/risk/cases/{case['case_id']}", json={"status": "investigating", "note": "跟进负责人已阅读"}).status_code == 200
    observe(series([100] * 79 + [94]))
    current = next(row for row in workspace(client)["cases"] if row["case_id"] == case["case_id"])
    assert current["evidence_json"]["trigger_version"] == version
    client.put("/api/risk/rules/sxv264", json={"period_limits": {"day": 3}})
    changed = next(row for row in workspace(client)["cases"] if row["case_id"] == case["case_id"])
    assert changed["evidence_json"]["trigger_version"] != version
