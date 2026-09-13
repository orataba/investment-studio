from datetime import UTC, date, datetime, timedelta, timezone
from math import exp, log, sqrt
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest

from watchlist_app.db.models.watchlists import InstrumentTaxonomyAssignment
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.calculation_frequency import (
    assess_latest_observation_freshness, build_calculation_frequency_context, source_calendar_date,
)
from watchlist_app.services.price_risk import initial_price_limits, period_loss_readings
from watchlist_app.services.research_metrics import ewma_price_evidence

from .conftest import seed_shared_instrument


def _migration_config():
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config


def test_crypto_scope_migration_is_reversible_without_registered_crypto(client):
    command.downgrade(_migration_config(), "20260908_0056")
    definitions = client.get("/api/instrument-attributes/definitions").json()
    coverage = next(row for row in definitions if row["attribute_key"] == "coverage_status")
    assert "crypto" not in coverage["instrument_scope_json"]
    command.upgrade(_migration_config(), "head")
    definitions = client.get("/api/instrument-attributes/definitions").json()
    coverage = next(row for row in definitions if row["attribute_key"] == "coverage_status")
    assert "crypto" in coverage["instrument_scope_json"]


def _series(count=130):
    start = date(2026, 1, 1)
    points = [{"date": (start + timedelta(days=i)).isoformat(), "value": 100 * exp(.005 * (i % 3))}
              for i in range(count)]
    return {"points": points, "metadata": {"return_kind": "price_return", "quote_basis": "close", "return_series_status": "ready"},
            "frequency": {"resolved_frequency": "daily", "gap_count": 0, "gap_detection_basis": "market_calendar:24/7"}}


def test_crypto_calendar_includes_weekends_detects_single_day_gaps_and_uses_utc():
    points = [{"as_of_date": date(2026, 9, day), "value": 100} for day in (4, 5, 6, 7)]
    profile = build_calculation_frequency_context(points, market_calendar="24/7")["profile"]
    assert profile["annualization_periods_per_year"] == pytest.approx(365.25)
    assert profile["gap_count"] == 0
    gap = build_calculation_frequency_context([points[0], *points[2:]], market_calendar="24/7")["profile"]
    assert gap["missing_observation_date_sample"] == ["2026-09-05"]
    now = datetime(2026, 9, 8, 1, tzinfo=timezone(timedelta(hours=8)))
    assert source_calendar_date(now, "24/7") == date(2026, 9, 7)
    freshness = assess_latest_observation_freshness(latest_observation_date=date(2026, 9, 5),
        current_date=date(2026, 9, 7), resolved_frequency="daily", market_calendar="24/7")
    assert freshness["status"] == "stale"
    assert freshness["expected_latest_date"] == "2026-09-06"


def test_crypto_ewma_uses_completed_utc_days_and_its_calendar_year():
    series = _series()
    cutoff = datetime.combine(date.fromisoformat(series["points"][-1]["date"]), datetime.min.time(), tzinfo=UTC)
    result = ewma_price_evidence(series, instrument_type="crypto", calendar="24/7", as_of=cutoff)
    assert result["status"] == "available"
    assert result["current"]["date"] == series["points"][-2]["date"]
    assert result["methodology"]["annualization"] == 365.25
    assert result["methodology"]["half_life_sessions"] == 30
    values = [log(b["value"] / a["value"]) for a, b in zip(series["points"][:-2], series["points"][1:-1])]
    weights = [.5 ** (i / 30) for i in range(len(values) - 1, -1, -1)]
    mean = sum(w * value for w, value in zip(weights, values)) / sum(weights)
    expected = sqrt(sum(w * (value - mean) ** 2 for w, value in zip(weights, values)) / sum(weights) * 365.25) * 100
    assert result["current"]["volatility_pct"] == pytest.approx(expected)
    assert ewma_price_evidence(series, instrument_type="crypto", calendar="XNYS", as_of=cutoff)["status"] == "unavailable"
    series["points"].pop(-30)
    assert ewma_price_evidence(series, instrument_type="crypto", calendar="24/7", as_of=cutoff)["status"] == "unavailable"


def test_crypto_price_review_horizons_are_calendar_days():
    series = _series(400)
    readings = period_loss_readings(series, {})
    assert [row["observations"] for row in readings] == [1, 7, 30, 90]
    assert initial_price_limits(series)[1]["observations"] == 365


def test_registered_crypto_has_native_detail_status_and_price_return_series(client):
    series = _series()
    seed_shared_instrument({
        "instrument_id": "btcusd", "instrument_name": "Bitcoin", "instrument_type": "crypto", "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "BTCUSD", "is_primary": True}],
        "market_data": [{"metric_family": "price", "quote_basis": "close", "as_of_date": row["date"],
            "value": str(row["value"]), "currency": "USD", "price_unit": "per_unit", "price_scale": "1", "status": "complete"}
            for row in series["points"]], "lifecycle_state": {"status": "active"},
    })
    assert client.get("/api/watchlists/all-instruments").status_code == 200
    with get_session_factory()() as session:
        assignment = session.get(InstrumentTaxonomyAssignment, ("btcusd", "instrument_taxonomy"))
        assert assignment.node_id == "crypto-native"
        assert assignment.source_record_id == "registry_type:crypto"
        # An existing human assignment, including an explicit unclassification,
        # must not be replaced by the initial type-based default.
        assignment.node_id = None
        assignment.source_record_id = "user:review-owner"
        session.commit()
        assigned_at = assignment.assigned_at
    resolved = client.post("/api/instruments/btcusd/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["detail_view_type"] == "crypto"
    assert resolved.json()["instrument_type"] == "crypto"
    with get_session_factory()() as session:
        assignment = session.get(InstrumentTaxonomyAssignment, ("btcusd", "instrument_taxonomy"))
        assert assignment.node_id is None
        assert assignment.source_record_id == "user:review-owner"
        assert assignment.assigned_at == assigned_at
    updated = client.post("/api/instrument-attributes/instruments/btcusd", json={
        "values": [{"attribute_key": "coverage_status", "value": "Invested"}],
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["values"]["coverage_status"] == "Invested"
    chart = client.get("/api/instruments/btcusd/chart")
    assert chart.status_code == 200, chart.text
    assert chart.json()["selected_series"]["return_kind"] == "price_return"
    risk = client.get("/api/instruments/btcusd/risk")
    assert risk.status_code == 200, risk.text
    assert risk.json()["calculation_frequency_profile"]["annualization_periods_per_year"] == pytest.approx(365.25)
    with pytest.raises(RuntimeError, match="Crypto research or taxonomy assignments exist"):
        command.downgrade(_migration_config(), "20260908_0056")


def test_tiny_spot_prices_keep_precision_and_chart_returns_match_canonical_performance(client):
    points = [("2025-01-01", "0.000000764"), ("2026-01-01", "0.00000123456")]
    seed_shared_instrument({
        "instrument_id": "tiny-spot", "instrument_name": "Tiny Spot", "instrument_type": "crypto", "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "TINYUSD", "is_primary": True}],
        "market_data": [{"metric_family": "price", "quote_basis": "close", "as_of_date": day,
            "value": value, "currency": "USD", "price_unit": "per_unit", "price_scale": "1", "status": "complete"}
            for day, value in points], "lifecycle_state": {"status": "active"},
    })
    assert client.post("/api/instruments/tiny-spot/resolve").status_code == 200
    assert client.post("/api/recalc/instruments/tiny-spot/execute", json={"job_type": "all"}).status_code == 200
    chart = client.get("/api/instruments/tiny-spot/chart").json()
    levels = chart["series"][0]["points"]
    assert levels == [{"date": day, "value": float(value)} for day, value in points]
    assert chart["latest_values"]["valuation"]["value"] == float(points[-1][1])
    performance = client.get("/api/instruments/tiny-spot/performance").json()
    annualized = next(row["investment_nav"] for row in performance["trailing_returns"] if row["window"] == "Ann.")
    # Exactly one calendar year: chart endpoint and canonical annualized returns
    # must agree even when every raw price is below four decimal places.
    assert annualized == pytest.approx((levels[-1]["value"] / levels[0]["value"] - 1) * 100, abs=1e-6)
