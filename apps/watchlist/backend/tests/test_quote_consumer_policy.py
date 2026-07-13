from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from .conftest import seed_shared_instrument
from portfolio_ops_instrument_core import instrument_store as shared_store
from portfolio_ops_instrument_core.quote_resolver import (
    resolve_quote_series_observation_at,
)
from watchlist_app.services.canonical_recalc import (
    _last_successful_snapshot_at,
    _resolve_canonical_series,
)
from watchlist_app.services.quote_consumer_policy import (
    quote_consumer_dependency,
    watchlist_freshness_profile,
)


def test_consumer_profile_is_based_only_on_canonical_instrument_type() -> None:
    fund = watchlist_freshness_profile(" FUND ")
    listed = watchlist_freshness_profile("etf")
    unknown = watchlist_freshness_profile("")

    assert fund.profile == "periodic_fund_nav"
    assert fund.reason_code == "canonical_fund_periodic_publication_window"
    assert fund.resolver_policy.max_age_days == 45
    assert listed.profile == "daily_market"
    assert listed.reason_code == "canonical_nonfund_daily_market_window"
    assert listed.resolver_policy.max_age_days == 5
    assert unknown.profile == "daily_market"
    assert unknown.resolver_policy.max_age_days == 5


def test_combined_last_success_uses_older_of_performance_and_risk() -> None:
    performance_time = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    risk_time = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)

    assert _last_successful_snapshot_at(
        SimpleNamespace(calculated_at=performance_time),
        SimpleNamespace(calculated_at=risk_time),
    ) == risk_time
    assert _last_successful_snapshot_at(
        SimpleNamespace(calculated_at=performance_time), None
    ) is None
    assert _last_successful_snapshot_at(
        None, SimpleNamespace(calculated_at=risk_time)
    ) is None


def test_consumer_dependency_fingerprint_has_stable_golden_format() -> None:
    dependency = quote_consumer_dependency(
        canonical_dependency_fingerprint="sha256:canonical-test",
        consumer_profile=watchlist_freshness_profile("fund"),
    )

    assert dependency == {
        "dependency_kind": "watchlist_quote_consumer_dependency",
        "dependency_version": "v1",
        "canonical_dependency_fingerprint": "sha256:canonical-test",
        "consumer_freshness_profile": {
            "profile": "periodic_fund_nav",
            "policy_version": "watchlist_quote_consumer.v1",
            "reason_code": "canonical_fund_periodic_publication_window",
            "canonical_instrument_type": "fund",
            "resolver_policy": {
                "policy_version": "canonical_quote_freshness.v1",
                "mode": "calendar_day_carry_forward",
                "max_age_days": 45,
            },
        },
        "fingerprint": (
            "sha256:66e0ec807033f1f120b7d13408484e49e6bd86ec"
            "dbc0e9d4311822111bbca1b8"
        ),
    }


@pytest.mark.parametrize("role", ["total_return", "chart"])
@pytest.mark.parametrize(
    ("valuation_date", "expected_status"),
    [
        (date(2026, 1, 11), "resolved"),
        (date(2026, 1, 12), "resolved"),
        (date(2026, 2, 15), "resolved"),
        (date(2026, 2, 16), "unavailable"),
    ],
)
def test_fund_quote_roles_use_45_calendar_day_freshness(
    client: TestClient,
    role: str,
    valuation_date: date,
    expected_status: str,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "monthly-policy-fund",
            "instrument_name": "Monthly Policy Fund",
            "instrument_type": "fund",
            "currency": "USD",
            "identifiers": [],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "as_of_date": "2026-01-01",
                    "value": "100",
                    "currency": "USD",
                    "status": "complete",
                }
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    from watchlist_app.db.session import get_session_factory

    with get_session_factory()() as session:
        window = _resolve_canonical_series(
            session,
            instrument_id="monthly-policy-fund",
            role=role,
            valuation_date=valuation_date,
        )
        endpoint = resolve_quote_series_observation_at(
            window, requested_as_of_date=valuation_date
        )

    assert endpoint.resolution_status == expected_status
    assert window.freshness_policy.max_age_days == 45


@pytest.mark.parametrize("role", ["total_return", "chart"])
@pytest.mark.parametrize(
    ("valuation_date", "expected_status"),
    [
        (date(2026, 1, 4), "resolved"),  # weekend carry from Friday
        (date(2026, 1, 7), "resolved"),
        (date(2026, 1, 8), "unavailable"),
    ],
)
def test_listed_quote_roles_use_5_calendar_day_freshness(
    client: TestClient,
    role: str,
    valuation_date: date,
    expected_status: str,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "daily-policy-etf",
            "instrument_name": "Daily Policy ETF",
            "instrument_type": "etf",
            "currency": "USD",
            "identifiers": [],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": quote_basis,
                    "as_of_date": "2026-01-02",
                    "value": value,
                    "currency": "USD",
                    "status": "complete",
                }
                for quote_basis, value in (
                    ("adjusted_close", "100"),
                    ("close", "99"),
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    from watchlist_app.db.session import get_session_factory

    with get_session_factory()() as session:
        window = _resolve_canonical_series(
            session,
            instrument_id="daily-policy-etf",
            role=role,
            valuation_date=valuation_date,
        )
        endpoint = resolve_quote_series_observation_at(
            window, requested_as_of_date=valuation_date
        )

    assert endpoint.resolution_status == expected_status
    assert window.freshness_policy.max_age_days == 5


def test_late_recalc_preserves_last_good_snapshots_and_nulls_current_metrics(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "late-last-good-fund",
            "instrument_name": "Late Last Good Fund",
            "instrument_type": "fund",
            "currency": "USD",
            "market_data_updated_at": "2026-04-15T22:30:45.123456Z",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "LLGF",
                    "is_primary": True,
                }
            ],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "as_of_date": observation_date,
                    "value": value,
                    "currency": "USD",
                    "status": "complete",
                }
                for observation_date, value in (
                    ("2025-12-31", "95"),
                    ("2026-03-31", "100"),
                    ("2026-04-15", "105"),
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Late last-good coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    assert client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["late-last-good-fund"]},
    ).status_code == 200

    from watchlist_app.db.models.analytics import PerformanceSnapshot, RiskSnapshot
    from watchlist_app.db.models.read_models import (
        InstrumentPerformanceReadModel,
        InstrumentRiskReadModel,
        InstrumentSummaryReadModel,
        WatchlistRowReadModel,
    )
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services import canonical_recalc
    from watchlist_app.services.canonical_recalc import CanonicalRecalcService

    with get_session_factory()() as session:
        before_performance = session.get(
            PerformanceSnapshot,
            session.query(PerformanceSnapshot.snapshot_id)
            .filter_by(instrument_id="late-last-good-fund", is_current=True)
            .scalar(),
        )
        before_risk = session.get(
            RiskSnapshot,
            session.query(RiskSnapshot.snapshot_id)
            .filter_by(instrument_id="late-last-good-fund", is_current=True)
            .scalar(),
        )
        before_row = session.get(
            WatchlistRowReadModel,
            (watchlist_id, "late-last-good-fund"),
        )
        before_performance_read_model = session.get(
            InstrumentPerformanceReadModel, "late-last-good-fund"
        )
        before_risk_read_model = session.get(
            InstrumentRiskReadModel, "late-last-good-fund"
        )
        before_performance_payload = dict(
            before_performance_read_model.payload_json
        )
        before_risk_payload = dict(before_risk_read_model.payload_json)
        before = {
            "performance_id": before_performance.snapshot_id,
            "performance_hash": before_performance.input_hash,
            "performance_calculated_at": before_performance.calculated_at,
            "risk_id": before_risk.snapshot_id,
            "risk_hash": before_risk.input_hash,
            "risk_calculated_at": before_risk.calculated_at,
            "last_successful_snapshot_at": before_row.last_successful_snapshot_at,
            "performance_historical_resolution": before_performance_payload[
                "historical_quote_resolution"
            ],
            "risk_historical_resolution": before_risk_payload[
                "historical_quote_resolution"
            ],
        }
        before_performance_read_model.payload_json = {
            **before_performance_payload,
            "legacy_unbounded_blob": {"observations": ["must-not-survive"]},
        }
        before_risk_read_model.payload_json = {
            **before_risk_payload,
            "legacy_unbounded_blob": {"points": ["must-not-survive"]},
        }
        session.commit()

    attempted_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(canonical_recalc, "_utcnow", lambda: attempted_at)
    with get_session_factory()() as session:
        CanonicalRecalcService().execute_recalc(
            session,
            instrument_id="late-last-good-fund",
            job_type="all",
            trigger_type="late-regression-test",
            trigger_ref_type="test",
            trigger_ref_id="late-regression-test",
            valuation_date=date(2026, 6, 1),
            commit=True,
        )

    with get_session_factory()() as session:
        after_performance = session.get(
            PerformanceSnapshot, before["performance_id"]
        )
        after_risk = session.get(RiskSnapshot, before["risk_id"])
        after_row = session.get(
            WatchlistRowReadModel,
            (watchlist_id, "late-last-good-fund"),
        )
        summary = session.get(
            InstrumentSummaryReadModel, "late-last-good-fund"
        )
        performance_read_model = session.get(
            InstrumentPerformanceReadModel, "late-last-good-fund"
        )
        risk_read_model = session.get(
            InstrumentRiskReadModel, "late-last-good-fund"
        )
        assert session.query(PerformanceSnapshot).filter_by(
            instrument_id="late-last-good-fund", is_current=True
        ).count() == 1
        assert session.query(RiskSnapshot).filter_by(
            instrument_id="late-last-good-fund", is_current=True
        ).count() == 1

    assert after_performance.snapshot_id == before["performance_id"]
    assert after_performance.input_hash == before["performance_hash"]
    assert after_performance.calculated_at == before["performance_calculated_at"]
    assert after_risk.snapshot_id == before["risk_id"]
    assert after_risk.input_hash == before["risk_hash"]
    assert after_risk.calculated_at == before["risk_calculated_at"]
    assert after_performance.market_data_input_watermark_at == datetime(
        2026, 4, 15, 22, 30, 45, 123456
    )
    assert summary.payload_json["freshness"][
        "market_data_input_watermark_status"
    ] == "known"
    assert summary.payload_json["freshness"][
        "market_data_input_watermark_at"
    ] == "2026-04-15T22:30:45.123456Z"
    assert after_row.data_freshness_status == "stale"
    assert after_row.last_recalculated_at == attempted_at.replace(tzinfo=None)
    assert (
        after_row.last_successful_snapshot_at
        == before["last_successful_snapshot_at"]
    )
    for metric_name in (
        "return_ytd",
        "return_1w",
        "return_mtd",
        "return_1m",
        "return_1y",
        "annualized_return",
        "return_3y",
        "return_5y",
        "max_drawdown",
        "volatility",
        "sharpe_ratio",
    ):
        assert getattr(after_row, metric_name) is None
    assert summary.payload_json["freshness"]["data_freshness_status"] == "stale"
    assert summary.payload_json["calculation_state"] == {
        "current_endpoint_state": "stale",
        "historical_calculation_state": "last_good_preserved",
        "analytics_snapshot_state": "unqualified",
        "history_as_of_date": "2026-04-15",
        "reason_codes": [
            "late_observation",
            "freshness_limit_exceeded",
            "unknown_ingestion_time",
        ],
    }
    assert performance_read_model.payload_json["calculation_state"] == (
        summary.payload_json["calculation_state"]
    )
    assert "legacy_unbounded_blob" not in performance_read_model.payload_json
    assert "legacy_unbounded_blob" not in risk_read_model.payload_json
    assert performance_read_model.payload_json[
        "historical_quote_resolution"
    ] == before["performance_historical_resolution"]
    assert risk_read_model.payload_json[
        "historical_quote_resolution"
    ] == before["risk_historical_resolution"]
    assert (
        performance_read_model.payload_json["quote_resolution"][
            "consumer_dependency"
        ]["fingerprint"]
        != performance_read_model.payload_json["historical_quote_resolution"][
            "consumer_dependency"
        ]["fingerprint"]
    )

    # A truly missing canonical series is not mislabeled stale. Historical
    # availability remains explicit and the last-good snapshots stay intact.
    from portfolio_ops_instrument_core.db_models import QuoteSeries

    with get_session_factory()() as session:
        session.query(QuoteSeries).filter_by(
            instrument_id="late-last-good-fund"
        ).delete(synchronize_session=False)
        corrupt_performance_read_model = session.get(
            InstrumentPerformanceReadModel, "late-last-good-fund"
        )
        corrupt_performance_read_model.payload_json = {
            **corrupt_performance_read_model.payload_json,
            "historical_quote_resolution": {
                **corrupt_performance_read_model.payload_json[
                    "historical_quote_resolution"
                ],
                "observations": ["invalid-unbounded-lineage"],
            },
        }
        session.commit()
    missing_attempted_at = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        canonical_recalc, "_utcnow", lambda: missing_attempted_at
    )
    with get_session_factory()() as session:
        CanonicalRecalcService().execute_recalc(
            session,
            instrument_id="late-last-good-fund",
            job_type="all",
            trigger_type="missing-regression-test",
            trigger_ref_type="test",
            trigger_ref_id="missing-regression-test",
            valuation_date=date(2026, 6, 2),
            commit=True,
        )
    with get_session_factory()() as session:
        missing_summary = session.get(
            InstrumentSummaryReadModel, "late-last-good-fund"
        )
        missing_performance = session.get(
            PerformanceSnapshot, before["performance_id"]
        )
        missing_risk = session.get(RiskSnapshot, before["risk_id"])
        missing_performance_read_model = session.get(
            InstrumentPerformanceReadModel, "late-last-good-fund"
        )
        missing_risk_read_model = session.get(
            InstrumentRiskReadModel, "late-last-good-fund"
        )

    assert missing_summary.payload_json["freshness"]["data_freshness_status"] == (
        "unavailable"
    )
    assert missing_summary.payload_json["calculation_state"] == {
        "current_endpoint_state": "unavailable",
        "historical_calculation_state": "last_good_preserved",
        "analytics_snapshot_state": "unqualified",
        "history_as_of_date": "2026-04-15",
        "reason_codes": [
            "missing_quote_series",
            "analytics_historical_dependency_manifest_unavailable",
        ],
    }
    assert missing_performance.snapshot_id == before["performance_id"]
    assert missing_performance.calculated_at == before["performance_calculated_at"]
    assert missing_risk.snapshot_id == before["risk_id"]
    assert missing_risk.calculated_at == before["risk_calculated_at"]
    assert (
        missing_performance_read_model.payload_json[
            "historical_quote_resolution"
        ]
        is None
    )
    assert (
        missing_risk_read_model.payload_json["historical_quote_resolution"]
        is None
    )


def test_first_late_recalc_materializes_history_as_of_last_valid_observation(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "first-late-history-fund",
            "instrument_name": "First Late History Fund",
            "instrument_type": "fund",
            "currency": "USD",
            "identifiers": [],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "as_of_date": observation_date,
                    "value": value,
                    "currency": "USD",
                    "status": "complete",
                }
                for observation_date, value in (
                    ("2025-12-01", "95"),
                    ("2026-01-01", "100"),
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "First late history", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    assert client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["first-late-history-fund"]},
    ).status_code == 200

    from watchlist_app.db.models.analytics import PerformanceSnapshot, RiskSnapshot
    from watchlist_app.db.models.read_models import (
        InstrumentSummaryReadModel,
        WatchlistRowReadModel,
    )
    from watchlist_app.db.session import get_session_factory

    with get_session_factory()() as session:
        performance = session.query(PerformanceSnapshot).filter_by(
            instrument_id="first-late-history-fund", is_current=True
        ).one()
        risk = session.query(RiskSnapshot).filter_by(
            instrument_id="first-late-history-fund", is_current=True
        ).one()
        summary = session.get(
            InstrumentSummaryReadModel, "first-late-history-fund"
        )
        row = session.get(
            WatchlistRowReadModel,
            (watchlist_id, "first-late-history-fund"),
        )

    assert performance.as_of_date == date(2026, 1, 1)
    assert risk.as_of_date == date(2026, 1, 1)
    assert performance.input_hash.startswith("sha256:")
    assert risk.input_hash.startswith("sha256:")
    assert summary.payload_json["calculation_state"][
        "historical_calculation_state"
    ] == "historical_as_of_last_observation"
    assert summary.payload_json["calculation_state"]["current_endpoint_state"] == (
        "stale"
    )
    assert summary.payload_json["freshness"]["data_freshness_status"] == "stale"
    assert row.data_freshness_status == "stale"
    assert row.return_ytd is None
    assert row.volatility is None


def test_invalid_registry_watermark_is_explicitly_unknown_without_now_fallback(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "unknown-watermark-fund",
            "instrument_name": "Unknown Watermark Fund",
            "instrument_type": "fund",
            "currency": "USD",
            "market_data_updated_at": "not-a-timestamp",
            "identifiers": [],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "as_of_date": "2026-04-15",
                    "value": "100",
                    "currency": "USD",
                    "status": "complete",
                }
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Unknown watermark", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    assert client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["unknown-watermark-fund"]},
    ).status_code == 200

    from watchlist_app.db.models.analytics import PerformanceSnapshot
    from watchlist_app.db.models.read_models import InstrumentSummaryReadModel
    from watchlist_app.db.session import get_session_factory

    with get_session_factory()() as session:
        snapshot = session.query(PerformanceSnapshot).filter_by(
            instrument_id="unknown-watermark-fund", is_current=True
        ).one()
        summary = session.get(
            InstrumentSummaryReadModel, "unknown-watermark-fund"
        )

    freshness = summary.payload_json["freshness"]
    assert snapshot.market_data_input_watermark_at is None
    assert freshness["market_data_input_watermark_at"] is None
    assert freshness["market_data_input_watermark_status"] == "unknown"
    assert freshness["market_data_input_watermark_reason_code"] == (
        "missing_or_invalid_canonical_market_data_updated_at"
    )


@pytest.mark.parametrize(
    ("revision_status", "revision_value", "expected_reason"),
    [
        ("partial", "106", "partial_series"),
        ("rejected", "106", "rejected_observation"),
        ("withdrawn", None, "withdrawn_observation"),
    ],
)
def test_noncomplete_current_revision_preserves_last_good_pair(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    revision_status: str,
    revision_value: str | None,
    expected_reason: str,
) -> None:
    instrument_id = f"last-good-{revision_status}-fund"
    seed_shared_instrument(
        {
            "instrument_id": instrument_id,
            "instrument_name": f"Last Good {revision_status.title()} Fund",
            "instrument_type": "fund",
            "currency": "USD",
            "identifiers": [],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "as_of_date": observation_date,
                    "value": value,
                    "currency": "USD",
                    "status": "complete",
                }
                for observation_date, value in (
                    ("2025-12-31", "95"),
                    ("2026-04-01", "100"),
                    ("2026-04-15", "105"),
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": f"{revision_status} preservation", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    assert client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": [instrument_id]},
    ).status_code == 200

    from watchlist_app.db.models.analytics import PerformanceSnapshot, RiskSnapshot
    from watchlist_app.db.models.read_models import (
        InstrumentSummaryReadModel,
        WatchlistRowReadModel,
    )
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services import canonical_recalc
    from watchlist_app.services.canonical_recalc import CanonicalRecalcService

    with get_session_factory()() as session:
        performance = session.query(PerformanceSnapshot).filter_by(
            instrument_id=instrument_id, is_current=True
        ).one()
        risk = session.query(RiskSnapshot).filter_by(
            instrument_id=instrument_id, is_current=True
        ).one()
        row = session.get(
            WatchlistRowReadModel, (watchlist_id, instrument_id)
        )
        before = {
            "performance_id": performance.snapshot_id,
            "performance_hash": performance.input_hash,
            "performance_calculated_at": performance.calculated_at,
            "risk_id": risk.snapshot_id,
            "risk_hash": risk.input_hash,
            "risk_calculated_at": risk.calculated_at,
            "last_successful_snapshot_at": row.last_successful_snapshot_at,
        }

    if revision_status == "withdrawn":
        shared_store.replace_nav_history(
            get_session_factory(),
            instrument_id=instrument_id,
            rows=[
                {
                    "as_of_date": "2026-04-15",
                    "nav": "106",
                    "currency": "USD",
                }
            ],
            source_ref="test:withdrawn",
            point_status="complete",
            refresh_status="completed",
            updated_by="test",
            message="withdraw total-return observation",
        )
    else:
        changed_count = shared_store.upsert_market_data_points(
            get_session_factory(),
            instrument_id=instrument_id,
            rows=[
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "as_of_date": "2026-04-15",
                    "value": revision_value,
                    "currency": "USD",
                    "status": revision_status,
                    "source_ref": f"test:{revision_status}",
                }
            ],
        )
        assert changed_count == 1

    attempted_at = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(canonical_recalc, "_utcnow", lambda: attempted_at)
    with get_session_factory()() as session:
        CanonicalRecalcService().execute_recalc(
            session,
            instrument_id=instrument_id,
            job_type="all",
            trigger_type=f"{revision_status}-regression-test",
            trigger_ref_type="test",
            trigger_ref_id=f"{revision_status}-regression-test",
            valuation_date=date(2026, 4, 15),
            commit=True,
        )

    with get_session_factory()() as session:
        after_performance = session.query(PerformanceSnapshot).filter_by(
            instrument_id=instrument_id, is_current=True
        ).one()
        after_risk = session.query(RiskSnapshot).filter_by(
            instrument_id=instrument_id, is_current=True
        ).one()
        after_row = session.get(
            WatchlistRowReadModel, (watchlist_id, instrument_id)
        )
        summary = session.get(InstrumentSummaryReadModel, instrument_id)

    assert after_performance.snapshot_id == before["performance_id"]
    assert after_performance.input_hash == before["performance_hash"]
    assert after_performance.calculated_at == before["performance_calculated_at"]
    assert after_risk.snapshot_id == before["risk_id"]
    assert after_risk.input_hash == before["risk_hash"]
    assert after_risk.calculated_at == before["risk_calculated_at"]
    assert after_row.last_successful_snapshot_at == before[
        "last_successful_snapshot_at"
    ]
    assert after_row.data_freshness_status == "partial"
    assert after_row.last_recalculated_at == attempted_at.replace(tzinfo=None)
    for metric_name in (
        "return_ytd",
        "return_1w",
        "return_mtd",
        "return_1m",
        "return_1y",
        "annualized_return",
        "return_3y",
        "return_5y",
        "max_drawdown",
        "volatility",
        "sharpe_ratio",
    ):
        assert getattr(after_row, metric_name) is None
    calculation_state = summary.payload_json["calculation_state"]
    assert calculation_state["current_endpoint_state"] == "partial"
    assert calculation_state["historical_calculation_state"] == (
        "last_good_preserved"
    )
    assert expected_reason in calculation_state["reason_codes"]
    assert summary.payload_json["freshness"]["data_freshness_status"] == "partial"
