from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from math import isclose, sqrt

import pytest
from sqlalchemy import event

from portfolio_app.api import contracts
from portfolio_app.api.routes import performance as performance_routes
from portfolio_app.api.routes import workspace as workspace_routes
from portfolio_app.db.models import (
    PortfolioCalculationStateModel,
    PortfolioDailyContributionSliceModel,
    PortfolioDailyHoldingSnapshotModel,
    PortfolioDailySnapshotModel,
)
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services import daily_snapshots, ledger, performance, portfolio_store
from portfolio_app.services.canonical_quotes import resolve_quote_window_books_in_session
from portfolio_ops_instrument_core import instrument_store as shared_store
from portfolio_ops_instrument_core.db_models import Instrument
from tests.store_fixture import TEST_PORTFOLIO_STORE


# Every integration scenario in this module explicitly owns the full portfolio
# state it exercises.  Starting with an empty ledger keeps fixture loading on
# the same append-only contract as a newly provisioned database.
PORTFOLIO_LEDGER_FIXTURE_MODE = "empty"


class _CanonicalSeedCatalog:
    def get(self, instrument_id: str) -> dict[str, object] | None:
        del instrument_id
        return None


_TEST_INSTRUMENT_CATALOG = _CanonicalSeedCatalog()


def _write_store(store: dict[str, object]) -> None:
    """Persist portfolio facts and seed their mocked instruments canonically.

    Historical kernel tests replace the registry-detail read used by the
    non-valuation analytics paths.  Daily NAV now resolves valuations directly
    from the shared quote tables, so those same fixtures must be represented in
    the real canonical store instead of reviving a production fallback.
    """

    revision_store = deepcopy(store)
    for transaction in revision_store.get("transactions", []):
        if not isinstance(transaction, dict):
            continue
        if (
            transaction.get("transaction_type") == "opening_balance"
            and transaction.get("instrument_id") is not None
            and transaction.get("acquisition_date") is None
        ):
            # Synthetic performance histories treat the opening trade date as
            # the acquisition date unless the scenario states otherwise.  The
            # revision ledger requires that economic fact explicitly.
            transaction["acquisition_date"] = transaction["trade_date"]

    instrument_ids = {
        str(transaction.get("instrument_id") or "").strip()
        for transaction in revision_store.get("transactions", [])
        if isinstance(transaction, dict)
        and str(transaction.get("instrument_id") or "").strip()
    }
    instrument_ids.update(
        instrument_id
        for instrument_id in ("fx-usd-hkd", "fx-usd-cny")
        if _TEST_INSTRUMENT_CATALOG.get(instrument_id) is not None
    )
    session_factory = get_session_factory()
    for instrument_id in sorted(instrument_ids):
        detail = _TEST_INSTRUMENT_CATALOG.get(instrument_id)
        if not isinstance(detail, dict):
            continue
        instrument_type = str(detail.get("instrument_type") or "other").strip().lower()
        default_policy = deepcopy(
            shared_store.QUOTE_SELECTION_POLICY_DEFAULTS.get(
                instrument_type,
                shared_store.QUOTE_SELECTION_POLICY_DEFAULTS["other"],
            )
        )
        raw_policy = detail.get("quote_selection_policy")
        if isinstance(raw_policy, dict):
            for role, bases in raw_policy.items():
                if role in default_policy and isinstance(bases, list):
                    default_policy[role] = deepcopy(bases)

        with session_factory() as session:
            instrument = session.get(Instrument, instrument_id)
            if instrument is None:
                instrument = Instrument(
                    instrument_id=instrument_id,
                    instrument_name=str(detail.get("instrument_name") or instrument_id),
                    instrument_type=instrument_type,
                    currency=str(detail.get("currency") or "USD").strip().upper(),
                    quote_selection_policy_json=default_policy,
                    source_settings_json={},
                    refresh_status_json={},
                    lifecycle_state_json={"status": "active"},
                    market_data_updated_at=None,
                )
                session.add(instrument)
            else:
                instrument.quote_selection_policy_json = default_policy
            session.commit()

        market_data_rows = [
            deepcopy(point)
            for point in detail.get("market_data", [])
            if isinstance(point, dict)
        ]
        for point in market_data_rows:
            quote_basis = str(point.get("quote_basis") or "").strip().lower()
            canonical_metric_family = shared_store.VALID_QUOTE_BASES.get(quote_basis)
            if canonical_metric_family is not None:
                point["metric_family"] = canonical_metric_family
        if market_data_rows:
            shared_store.upsert_market_data_points(
                session_factory,
                instrument_id=instrument_id,
                rows=market_data_rows,
            )
    portfolio_store.reset_store(revision_store)


def test_holding_day_change_uses_adjusted_return_but_raw_market_value_across_split() -> None:
    change_pct, change_value = performance._holding_day_change_metrics(
        quantity=200.0,
        current_price=0.905,
        current_total_return_price=0.905,
        previous_total_return_price=0.9730108306861102,
        instrument_ref={"instrument_type": "etf"},
    )
    expected_return = 0.905 / 0.9730108306861102 - 1.0
    assert change_pct == pytest.approx(expected_return)
    assert change_value == pytest.approx(200.0 * 0.905 - 200.0 * 0.905 / (1.0 + expected_return))
    assert change_pct > -0.10


@pytest.mark.parametrize(
    ("current_total_return_price", "previous_total_return_price"),
    [(None, 100.0), (101.0, None), (None, None)],
)
def test_holding_day_change_never_falls_back_to_valuation_prices(
    current_total_return_price: float | None,
    previous_total_return_price: float | None,
) -> None:
    assert performance._holding_day_change_metrics(
        quantity=10.0,
        current_price=101.0,
        current_total_return_price=current_total_return_price,
        previous_total_return_price=previous_total_return_price,
        instrument_ref={"instrument_type": "equity"},
    ) == (None, None)


@pytest.mark.parametrize(
    "contract_model",
    [
        contracts.DailySnapshotRecord,
        contracts.DailyPerformancePoint,
        contracts.PerformanceSummary,
        contracts.ReturnCalendarBucket,
        contracts.ReturnCalendarSummary,
        contracts.DailyContributionSliceRecord,
        contracts.ContributionLineRecord,
        contracts.ContributionReportSummary,
        contracts.ContributionCalendarBucketRecord,
        contracts.ContributionCalendarSummary,
        contracts.ContributionBucketGroupRecord,
        contracts.ContributionBucketSummary,
        contracts.ContributionBucketCalendarBucketRecord,
        contracts.ContributionBucketCalendarSummary,
    ],
)
def test_twr_reliability_contract_fields_are_required(contract_model) -> None:
    for field_name in (
        "twr_state",
        "twr_reliability_status",
        "twr_reliability_reasons",
    ):
        assert contract_model.model_fields[field_name].is_required()


def _test_instrument_detail(
    *,
    instrument_id: str,
    instrument_name: str,
    history: list[tuple[str, str]],
    instrument_type: str = "equity",
) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "instrument_name": instrument_name,
        "instrument_type": instrument_type,
        "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": instrument_id.upper(), "is_primary": True}],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": as_of_date,
                "value": value,
                "currency": "USD",
                "status": "complete",
            }
            for as_of_date, value in history
        ],
    }


def test_realized_risk_contribution_uses_common_matrix_for_sparse_instruments():
    portfolio_daily_series = [
        {"as_of_date": date(2026, 1, 1), "daily_twr": 0.01, "return_observation_eligible": True},
        {"as_of_date": date(2026, 1, 2), "daily_twr": 0.02, "return_observation_eligible": True},
        {"as_of_date": date(2026, 1, 3), "daily_twr": -0.01, "return_observation_eligible": True},
    ]
    daily_slices = []
    for as_of_date, a_contribution, has_b_slice, b_contribution in [
        (date(2026, 1, 1), 0.004, True, 0.006),
        (date(2026, 1, 2), 0.020, False, 0.000),
        (date(2026, 1, 3), -0.006, True, -0.004),
    ]:
        daily_slices.append(
            {
                "as_of_date": as_of_date,
                "group_key": "instrument-a",
                "daily_return": a_contribution,
                "daily_contribution": a_contribution,
                "return_observation_eligible": True,
            }
        )
        if has_b_slice:
            daily_slices.append(
                {
                    "as_of_date": as_of_date,
                    "group_key": "instrument-b",
                    "daily_return": b_contribution,
                    "daily_contribution": b_contribution,
                    "return_observation_eligible": True,
                }
            )

    metrics = performance._realized_risk_attribution_by_group(
        daily_slices,
        portfolio_daily_series,
        calculation_frequency="daily",
        final_date=date(2026, 1, 3),
    )

    assert isclose(metrics["instrument-a"]["realized_risk_contribution"], 0.8142857142857135)
    assert isclose(metrics["instrument-b"]["realized_risk_contribution"], 0.18571428571428558)
    assert isclose(
        sum(item["realized_risk_contribution"] or 0.0 for item in metrics.values()),
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert metrics["instrument-a"]["risk_return_observation_count"] == 3
    assert metrics["instrument-b"]["risk_return_observation_count"] == 2


def test_realized_risk_contribution_links_daily_contributions_for_weekly_frequency():
    daily_rows = [
        (date(2026, 1, 5), 0.10, 0.06, 0.04),
        (date(2026, 1, 6), 0.10, 0.04, 0.06),
        (date(2026, 1, 12), -0.05, -0.03, -0.02),
        (date(2026, 1, 13), 0.02, 0.01, 0.01),
        (date(2026, 1, 19), 0.03, 0.02, 0.01),
        (date(2026, 1, 20), 0.04, 0.02, 0.02),
    ]
    portfolio_daily_series = [
        {"as_of_date": as_of_date, "daily_twr": portfolio_return, "return_observation_eligible": True}
        for as_of_date, portfolio_return, _a_contribution, _b_contribution in daily_rows
    ]
    daily_slices = []
    for as_of_date, _portfolio_return, a_contribution, b_contribution in daily_rows:
        daily_slices.extend(
            [
                {
                    "as_of_date": as_of_date,
                    "group_key": "instrument-a",
                    "daily_return": a_contribution,
                    "daily_contribution": a_contribution,
                    "return_observation_eligible": True,
                },
                {
                    "as_of_date": as_of_date,
                    "group_key": "instrument-b",
                    "daily_return": b_contribution,
                    "daily_contribution": b_contribution,
                    "return_observation_eligible": True,
                },
            ]
        )

    metrics = performance._realized_risk_attribution_by_group(
        daily_slices,
        portfolio_daily_series,
        calculation_frequency="weekly",
        final_date=date(2026, 1, 20),
    )

    assert isclose(metrics["instrument-a"]["realized_risk_contribution"], 0.522095588536811)
    assert isclose(metrics["instrument-b"]["realized_risk_contribution"], 0.47790441146318824)
    assert isclose(
        sum(item["realized_risk_contribution"] or 0.0 for item in metrics.values()),
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert metrics["instrument-a"]["risk_return_observation_count"] == 3
    assert metrics["instrument-b"]["risk_return_observation_count"] == 3


def _create_canonical_return_test_instrument(
    *,
    ticker: str,
    total_return_bases: list[str],
) -> str:
    instrument = shared_store.create_instrument(
        get_session_factory(),
        instrument_name=f"Canonical Return Test {ticker}",
        instrument_type="equity",
        currency="USD",
        identifiers=[
            {
                "identifier_type": "ticker",
                "identifier_value": ticker,
                "is_primary": True,
            }
        ],
        quote_selection_policy={
            "trading": ["close"],
            "valuation": ["close"],
            "total_return": total_return_bases,
            "chart": ["adjusted_close", "close"],
            "reference": ["close"],
        },
    )
    return str(instrument["instrument_id"])


def _upsert_canonical_return_test_points(
    instrument_id: str,
    rows: list[dict[str, object]],
) -> None:
    assert shared_store.upsert_market_data_points(
        get_session_factory(),
        instrument_id=instrument_id,
        rows=rows,
    ) == len(rows)


def _canonical_return_test_store(instrument_id: str) -> dict[str, object]:
    portfolio_id = f"canonical-return-{instrument_id}"
    store = _minimal_store(
        portfolio_id=portfolio_id,
        transactions=[
            {
                "transaction_id": "txn-opening",
                "portfolio_id": portfolio_id,
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": None,
                "instrument_id": instrument_id,
                "instrument_ref": {
                    "instrument_id": instrument_id,
                    "instrument_name": instrument_id,
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 50.0,
                "gross_amount": 500.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "created_at": "2026-01-01T09:00:00Z",
            }
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)
    return store


def test_holding_day_change_uses_only_same_locked_total_return_series():
    instrument_id = _create_canonical_return_test_instrument(
        ticker="TR-GOLDEN",
        total_return_bases=["adjusted_close", "reinvested_nav"],
    )
    _upsert_canonical_return_test_points(
        instrument_id,
        [
            {"metric_family": "price", "quote_basis": "close", "as_of_date": "2026-01-01", "value": "50", "currency": "USD", "source_ref": "test:close:1", "status": "complete"},
            {"metric_family": "price", "quote_basis": "close", "as_of_date": "2026-01-02", "value": "55", "currency": "USD", "source_ref": "test:close:2", "status": "complete"},
            {"metric_family": "price", "quote_basis": "adjusted_close", "as_of_date": "2026-01-01", "value": "100", "currency": "USD", "source_ref": "test:adjusted:1", "status": "complete"},
            {"metric_family": "price", "quote_basis": "adjusted_close", "as_of_date": "2026-01-02", "value": "102", "currency": "USD", "source_ref": "test:adjusted:2", "status": "complete"},
            {"metric_family": "nav", "quote_basis": "reinvested_nav", "as_of_date": "2026-01-01", "value": "1000", "currency": "USD", "source_ref": "test:lower:1", "status": "complete"},
            {"metric_family": "nav", "quote_basis": "reinvested_nav", "as_of_date": "2026-01-02", "value": "1500", "currency": "USD", "source_ref": "test:lower:2", "status": "complete"},
        ],
    )
    store = _canonical_return_test_store(instrument_id)

    with get_session_factory()() as quote_session:
        books = resolve_quote_window_books_in_session(
            quote_session,
            instrument_ids=[instrument_id],
            roles=["valuation", "total_return"],
            start_date=date(2025, 12, 1),
            end_date=date(2026, 1, 2),
        )
    current = books["total_return"].quote_at(instrument_id, date(2026, 1, 2))
    previous = books["total_return"].previous_quote(instrument_id, current or {})
    assert current is not None and previous is not None
    assert current["role"] == previous["role"] == "total_return"
    assert current["quote_basis"] == previous["quote_basis"] == "adjusted_close"
    assert current["quote_series_id"] == previous["quote_series_id"]
    assert current["source_ref"] == "test:adjusted:2"
    assert previous["source_ref"] == "test:adjusted:1"

    report = performance.build_holdings_report(
        store["portfolios"][0],
        store["accounts"],
        store["transactions"],
        as_of_date=date(2026, 1, 2),
    )
    holding = report["positions"][0]
    expected_return = 102.0 / 100.0 - 1.0
    assert holding["quote_basis"] == "close"
    assert holding["last_price"] == pytest.approx(55.0)
    assert holding["market_value"] == pytest.approx(550.0)
    assert holding["cost_basis"] == pytest.approx(500.0)
    assert holding["cost_basis_base"] == pytest.approx(500.0)
    assert holding["unrealized_pnl"] == pytest.approx(50.0)
    assert holding["unrealized_pnl_base"] == pytest.approx(50.0)
    assert holding["unrealized_return"] == pytest.approx(0.1)
    assert holding["day_change_pct"] == pytest.approx(expected_return)
    assert holding["day_change_value"] == pytest.approx(
        550.0 - 550.0 / (1.0 + expected_return)
    )


def test_preferred_total_return_series_late_start_never_stitches_lower_series():
    instrument_id = _create_canonical_return_test_instrument(
        ticker="TR-LATE-START",
        total_return_bases=["adjusted_close", "reinvested_nav"],
    )
    _upsert_canonical_return_test_points(
        instrument_id,
        [
            {"metric_family": "price", "quote_basis": "close", "as_of_date": "2026-01-02", "value": "55", "currency": "USD", "source_ref": "test:close", "status": "complete"},
            {"metric_family": "price", "quote_basis": "adjusted_close", "as_of_date": "2026-01-02", "value": "200", "currency": "USD", "source_ref": "test:preferred", "status": "complete"},
            {"metric_family": "nav", "quote_basis": "reinvested_nav", "as_of_date": "2026-01-01", "value": "100", "currency": "USD", "source_ref": "test:lower:1", "status": "complete"},
            {"metric_family": "nav", "quote_basis": "reinvested_nav", "as_of_date": "2026-01-02", "value": "101", "currency": "USD", "source_ref": "test:lower:2", "status": "complete"},
        ],
    )
    store = _canonical_return_test_store(instrument_id)

    report = performance.build_holdings_report(
        store["portfolios"][0],
        store["accounts"],
        store["transactions"],
        as_of_date=date(2026, 1, 2),
    )
    holding = report["positions"][0]
    assert holding["market_value"] == pytest.approx(550.0)
    assert holding["day_change_pct"] is None
    assert holding["day_change_value"] is None


def test_holding_unrealized_fields_fail_closed_without_market_value():
    instrument_id = _create_canonical_return_test_instrument(
        ticker="UNREALIZED-NO-PRICE",
        total_return_bases=["adjusted_close"],
    )
    store = _canonical_return_test_store(instrument_id)

    report = performance.build_holdings_report(
        store["portfolios"][0],
        store["accounts"],
        store["transactions"],
        as_of_date=date(2026, 1, 2),
    )
    holding = report["positions"][0]

    assert holding["cost_basis"] == pytest.approx(500.0)
    assert holding["market_value"] is None
    assert holding["market_value_base"] is None
    assert holding["unrealized_pnl"] is None
    assert holding["unrealized_pnl_base"] is None
    assert holding["unrealized_return"] is None


def test_period_and_contribution_valuation_ignore_registry_detail_market_data(
    monkeypatch,
):
    instrument_id = _create_canonical_return_test_instrument(
        ticker="VALUATION-AUTHORITY",
        total_return_bases=["adjusted_close"],
    )
    _upsert_canonical_return_test_points(
        instrument_id,
        [
            {"metric_family": "price", "quote_basis": "close", "as_of_date": "2026-01-01", "value": "50", "currency": "USD", "source_ref": "test:canonical:1", "status": "complete"},
            {"metric_family": "price", "quote_basis": "close", "as_of_date": "2026-01-02", "value": "55", "currency": "USD", "source_ref": "test:canonical:2", "status": "complete"},
        ],
    )
    store = _canonical_return_test_store(instrument_id)
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda requested_id: {
            "instrument_id": instrument_id,
            "instrument_name": instrument_id,
            "instrument_type": "equity",
            "currency": "USD",
            "quote_selection_policy": {"valuation": ["close"]},
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-01-02",
                    "value": "999",
                    "currency": "USD",
                    "status": "complete",
                }
            ],
        }
        if requested_id == instrument_id
        else None,
    )

    calculation = performance.build_period_calculation_report(
        store["portfolios"][0],
        store["accounts"],
        store["transactions"],
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
    )
    assert calculation["summary"]["initial_value"] == pytest.approx(500.0)
    assert calculation["summary"]["final_value"] == pytest.approx(550.0)
    assert calculation["summary"]["unrealized_capital_gains"] == pytest.approx(50.0)

    contribution = performance.build_contribution_report(
        store["portfolios"][0],
        store["accounts"],
        store["transactions"],
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        axis="instrument",
    )
    slice_record = next(
        item
        for item in contribution["daily_slices"]
        if item["group_key"] == instrument_id
        and item["as_of_date"] == date(2026, 1, 2)
    )
    assert slice_record["ending_value_base"] == pytest.approx(550.0)


@pytest.mark.parametrize(
    ("bad_status", "expected_reason"),
    [
        ("partial", "partial_series"),
        ("rejected", "rejected_observation"),
        ("withdrawn", "withdrawn_observation"),
    ],
)
def test_bad_total_return_revision_blocks_holding_return_with_lineage(
    bad_status: str,
    expected_reason: str,
):
    total_return_basis = (
        "total_return_nav" if bad_status == "withdrawn" else "adjusted_close"
    )
    instrument_id = _create_canonical_return_test_instrument(
        ticker=f"TR-{bad_status.upper()}",
        total_return_bases=[total_return_basis],
    )
    _upsert_canonical_return_test_points(
        instrument_id,
        [
            {"metric_family": "price", "quote_basis": "close", "as_of_date": "2026-01-02", "value": "55", "currency": "USD", "source_ref": "test:close", "status": "complete"},
            {"metric_family": "nav" if bad_status == "withdrawn" else "price", "quote_basis": total_return_basis, "as_of_date": "2026-01-01", "value": "100", "currency": "USD", "source_ref": "test:prior", "status": "complete"},
            {"metric_family": "nav" if bad_status == "withdrawn" else "price", "quote_basis": total_return_basis, "as_of_date": "2026-01-02", "value": "102", "currency": "USD", "source_ref": f"test:{bad_status}", "status": "complete" if bad_status == "withdrawn" else bad_status},
        ],
    )
    if bad_status == "withdrawn":
        assert shared_store.replace_nav_history(
            get_session_factory(),
            instrument_id=instrument_id,
            rows=[
                {
                    "as_of_date": "2026-01-02",
                    "nav": "55",
                    "currency": "USD",
                }
            ],
            source_ref="test:withdrawn",
            point_status="complete",
            refresh_status="imported",
            updated_by="pytest",
            message="withdraw invalid total-return observation",
        ) is not None
    store = _canonical_return_test_store(instrument_id)

    with get_session_factory()() as quote_session:
        books = resolve_quote_window_books_in_session(
            quote_session,
            instrument_ids=[instrument_id],
            roles=["total_return"],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
        )
    rejected_point = books["total_return"].quote_at(
        instrument_id,
        date(2026, 1, 2),
    )
    assert rejected_point is not None
    assert rejected_point["resolution_status"] == "unavailable"
    assert rejected_point["source_status"] == bad_status
    assert rejected_point["source_ref"] == f"test:{bad_status}"
    assert rejected_point["reason_codes"] == [expected_reason]
    assert rejected_point["revision_id"]
    assert rejected_point["payload_hash"]

    report = performance.build_holdings_report(
        store["portfolios"][0],
        store["accounts"],
        store["transactions"],
        as_of_date=date(2026, 1, 2),
    )
    holding = report["positions"][0]
    assert holding["market_value"] == pytest.approx(550.0)
    assert holding["day_change_pct"] is None
    assert holding["day_change_value"] is None


@pytest.mark.parametrize(
    (
        "stale_price_flag",
        "stale_fx_flag",
        "carry_forward_valuation_flag",
        "external_cash_in",
        "external_cash_out",
        "expected_state",
        "expected_status",
        "expected_reasons",
    ),
    [
        (False, False, False, 0.0, 0.0, "linked", "reliable", []),
        (False, False, False, 10.0, 0.0, "linked", "reliable", []),
        (False, False, False, 0.0, 10.0, "linked", "reliable", []),
        (
            False,
            False,
            True,
            0.0,
            0.0,
            "carry_forward",
            "qualified",
            ["carried_forward_valuation_without_external_flow"],
        ),
        (
            False,
            False,
            True,
            10.0,
            0.0,
            "broken",
            "unavailable",
            ["carried_forward_valuation_on_external_flow"],
        ),
        (
            False,
            False,
            True,
            0.0,
            10.0,
            "broken",
            "unavailable",
            ["carried_forward_valuation_on_external_flow"],
        ),
        (
            True,
            False,
            False,
            0.0,
            0.0,
            "carry_forward",
            "qualified",
            ["stale_valuation_without_external_flow"],
        ),
        (
            False,
            True,
            False,
            0.0,
            0.0,
            "carry_forward",
            "qualified",
            ["stale_valuation_without_external_flow"],
        ),
        (
            True,
            False,
            False,
            10.0,
            0.0,
            "broken",
            "unavailable",
            ["stale_valuation_on_external_flow"],
        ),
        (
            False,
            True,
            False,
            10.0,
            0.0,
            "broken",
            "unavailable",
            ["stale_valuation_on_external_flow"],
        ),
        (
            True,
            False,
            False,
            0.0,
            10.0,
            "broken",
            "unavailable",
            ["stale_valuation_on_external_flow"],
        ),
        (
            False,
            True,
            False,
            0.0,
            10.0,
            "broken",
            "unavailable",
            ["stale_valuation_on_external_flow"],
        ),
    ],
)
def test_daily_twr_reliability_truth_table(
    stale_price_flag: bool,
    stale_fx_flag: bool,
    carry_forward_valuation_flag: bool,
    external_cash_in: float,
    external_cash_out: float,
    expected_state: str,
    expected_status: str,
    expected_reasons: list[str],
) -> None:
    nav = 100.0 + external_cash_in - external_cash_out
    result = performance._daily_twr_link_result(
        nav=nav,
        valuation_complete=True,
        stale_price_flag=stale_price_flag,
        stale_fx_flag=stale_fx_flag,
        flow_coverage_complete=True,
        has_external_flow=bool(external_cash_in or external_cash_out),
        external_cash_in=external_cash_in,
        external_cash_out=external_cash_out,
        previous_anchor_nav=100.0,
        awaiting_fresh_anchor=False,
        carry_forward_valuation_flag=carry_forward_valuation_flag,
    )

    assert result["twr_state"] == expected_state
    assert result["twr_reliability_status"] == expected_status
    assert result["twr_reliability_reasons"] == expected_reasons
    if expected_status == "unavailable":
        assert result["daily_twr"] is None
        assert result["awaiting_fresh_anchor"] is True
    else:
        assert result["daily_twr"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    (
        "market_observation_count",
        "twr_reliability_status",
        "valuation_reliability_stale",
        "expected",
    ),
    [
        pytest.param(0, "qualified", False, False, id="pure-calendar-carry"),
        pytest.param(1, "qualified", False, True, id="mixed-frequency-carry"),
        pytest.param(1, "qualified", True, False, id="resolver-explicit-stale"),
        pytest.param(1, "unavailable", True, False, id="broken-boundary"),
    ],
)
def test_risk_observation_eligibility_distinguishes_calendar_carry_from_stale(
    market_observation_count: int,
    twr_reliability_status: str,
    valuation_reliability_stale: bool,
    expected: bool,
) -> None:
    assert (
        performance._return_observation_eligible(
            daily_twr=0.01,
            market_observation_count=market_observation_count,
            twr_reliability_status=twr_reliability_status,
            valuation_reliability_stale=valuation_reliability_stale,
        )
        is expected
    )


def test_twr_window_reliability_preserves_distinct_carry_reasons() -> None:
    result = performance._twr_window_reliability(
        [
            {
                "as_of_date": date(2026, 1, 2),
                "daily_twr": 0.01,
                "twr_state": "carry_forward",
                "twr_reliability_status": "qualified",
                "twr_reliability_reasons": [
                    "stale_valuation_without_external_flow"
                ],
            },
            {
                "as_of_date": date(2026, 1, 1),
                "daily_twr": 0.0,
                "twr_state": "carry_forward",
                "twr_reliability_status": "qualified",
                "twr_reliability_reasons": [
                    "carried_forward_valuation_without_external_flow"
                ],
            },
        ]
    )

    assert result == {
        "twr_state": "carry_forward",
        "twr_reliability_status": "qualified",
        "twr_reliability_reasons": [
            "carried_forward_valuation_without_external_flow",
            "stale_valuation_without_external_flow",
        ],
        "linkable": True,
    }


def _daily_snapshot_row_count(portfolio_id: str) -> int:
    session_factory = get_session_factory()
    with session_factory() as session:
        return int(
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == portfolio_id)
            .count()
        )


def _daily_holding_row_count(portfolio_id: str) -> int:
    session_factory = get_session_factory()
    with session_factory() as session:
        return int(
            session.query(PortfolioDailyHoldingSnapshotModel)
            .filter(PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id)
            .count()
        )


def _daily_contribution_slice_count(portfolio_id: str, axis: str) -> int:
    session_factory = get_session_factory()
    with session_factory() as session:
        return int(
            session.query(PortfolioDailyContributionSliceModel)
            .filter(
                PortfolioDailyContributionSliceModel.portfolio_id == portfolio_id,
                PortfolioDailyContributionSliceModel.axis == axis,
            )
            .count()
        )


def _minimal_store(
    *,
    portfolio_id: str,
    transactions: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "portfolios": [
            {
                "portfolio_id": portfolio_id,
                "portfolio_name": portfolio_id,
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": max(str(item["trade_date"]) for item in transactions),
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 1,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-usd-main",
                "portfolio_id": portfolio_id,
                "account_name": "Main USD Cash",
                "account_type": "deposit_account",
                "currency": "USD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_instrument_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            },
            {
                "account_id": "broker-us-core",
                "portfolio_id": portfolio_id,
                "account_name": "Core Brokerage",
                "account_type": "securities_account",
                "currency": "USD",
                "institution": "Test Broker",
                "default_settlement_cash_account_id": "cash-usd-main",
                "cost_basis_method": "fifo",
                "allowed_instrument_types": ["equity"],
                "opened_at": "2026-01-01",
                "status": "active",
            },
        ],
        "transactions": transactions,
    }


def test_seed_portfolio_performance_uses_external_boundary_flows(client):
    _write_store(deepcopy(TEST_PORTFOLIO_STORE))

    snapshots_response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert snapshots_response.status_code == 200
    snapshots_payload = snapshots_response.json()
    assert snapshots_payload["summary"]["latest_complete_as_of_date"] == "2026-04-15"

    by_date = {item["as_of_date"]: item for item in snapshots_payload["snapshots"]}
    assert by_date["2026-02-03"]["external_cash_in"] == 50000.0
    assert by_date["2026-04-08"]["external_cash_in"] == 0.0
    assert by_date["2026-04-08"]["external_cash_out"] == 0.0
    assert by_date["2026-04-11"]["external_cash_out"] == 12000.0
    assert by_date["2026-04-11"]["twr_state"] == "broken"
    assert by_date["2026-04-11"]["twr_reliability_reasons"] == [
        "carried_forward_valuation_on_external_flow"
    ]
    assert by_date["2026-04-12"]["external_cash_in"] == 0.0
    assert by_date["2026-04-12"]["external_cash_out"] == 0.0

    performance_response = client.get("/api/portfolios/portfolio-ops/performance")
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    summary = performance_payload["summary"]
    assert summary["latest_complete_as_of_date"] == "2026-04-15"
    assert summary["external_cash_in"] == 50000.0
    assert summary["external_cash_out"] == 12000.0
    assert summary["net_external_inflow"] == 38000.0
    assert summary["cumulative_twr"] is None
    assert summary["twr_reliability_status"] == "unavailable"
    assert summary["twr_reliability_reasons"] == [
        "crosses_broken_twr_boundary"
    ]
    assert summary["irr"] is None
    assert summary["mwror"] is None
    assert summary["history_reliability"]["annualized_return_eligible"] is False


def test_performance_endpoints_reuse_materialized_daily_snapshots(client, monkeypatch):
    _write_store(deepcopy(TEST_PORTFOLIO_STORE))

    first_response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert first_response.status_code == 200
    assert _daily_snapshot_row_count("portfolio-ops") > 0

    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, "portfolio-ops")
        assert state is not None
        assert state.daily_snapshot_status == "current"

    def fail_dynamic_snapshot_build(*_args, **_kwargs):
        raise AssertionError("materialized daily snapshots should satisfy this read")

    monkeypatch.setattr(performance, "build_daily_portfolio_snapshots", fail_dynamic_snapshot_build)

    second_snapshot_response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert second_snapshot_response.status_code == 200
    performance_response = client.get("/api/portfolios/portfolio-ops/performance")
    assert performance_response.status_code == 200


def test_refresh_materializes_holdings_and_contribution_slices(client):
    _write_store(deepcopy(TEST_PORTFOLIO_STORE))

    response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert response.status_code == 200

    assert _daily_snapshot_row_count("portfolio-ops") > 0
    assert _daily_holding_row_count("portfolio-ops") > 0
    assert _daily_contribution_slice_count("portfolio-ops", "instrument") > 0
    assert _daily_contribution_slice_count("portfolio-ops", "account") > 0
    assert _daily_contribution_slice_count("portfolio-ops", "instrument_type") > 0
    assert _daily_contribution_slice_count("portfolio-ops", "currency") > 0
    assert _daily_contribution_slice_count("portfolio-ops", "cash_detail") > 0
    assert _daily_contribution_slice_count("portfolio-ops", "instrument_detail") > 0
    assert _daily_contribution_slice_count("portfolio-ops", "account_detail") > 0
    assert _daily_contribution_slice_count("portfolio-ops", "instrument_type_detail") > 0
    assert _daily_contribution_slice_count("portfolio-ops", "currency_detail") > 0


def test_holdings_and_contribution_endpoints_reuse_materialized_read_models(client, monkeypatch):
    _write_store(deepcopy(TEST_PORTFOLIO_STORE))

    response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert response.status_code == 200

    def fail_live_holdings(*_args, **_kwargs):
        raise AssertionError("materialized holdings should satisfy this read")

    def fail_dynamic_contribution(*_args, **_kwargs):
        raise AssertionError("materialized contribution slices should satisfy this read")

    monkeypatch.setattr(workspace_routes, "build_holdings_report", fail_live_holdings)
    monkeypatch.setattr(performance_routes, "build_contribution_report", fail_dynamic_contribution)

    holdings_response = client.get("/api/workspace/holdings?portfolio_id=portfolio-ops")
    assert holdings_response.status_code == 200
    assert all("instrument_return_series_all" not in row for row in holdings_response.json()["rows"])

    full_holdings_response = client.get(
        "/api/workspace/holdings?portfolio_id=portfolio-ops&include_return_series=true"
    )
    assert full_holdings_response.status_code == 200
    assert all("instrument_return_series_all" in row for row in full_holdings_response.json()["rows"])

    session_factory = get_session_factory()
    with session_factory() as session:
        persisted_payloads = [
            row.holding_json
            for row in session.query(PortfolioDailyHoldingSnapshotModel).all()
        ]
    assert persisted_payloads
    assert all("price_chart_6m" not in payload for payload in persisted_payloads)
    assert all("instrument_return_series_all" not in payload for payload in persisted_payloads)
    holdings_payload = holdings_response.json()
    holdings_rows = holdings_payload["rows"]
    assert holdings_rows
    abbv_row = next(row for row in holdings_rows if row["instrument_core"]["instrument_id"] == "equity-us-abbv")
    # The seed has distinct canonical valuation and adjusted total-return
    # series, so valuation and one-day return semantics remain separate.
    assert abbv_row["last_price"] == pytest.approx(206.47)
    assert abbv_row["market_value"] is not None
    assert abbv_row["cost_basis"] is not None
    assert abbv_row["cost_basis_base"] is not None
    assert abbv_row["unrealized_pnl"] == pytest.approx(
        abbv_row["market_value"] - abbv_row["cost_basis"]
    )
    assert abbv_row["unrealized_pnl_base"] == pytest.approx(
        abbv_row["market_value_base"] - abbv_row["cost_basis_base"]
    )
    assert abbv_row["unrealized_return"] == pytest.approx(
        abbv_row["unrealized_pnl"] / abs(abbv_row["cost_basis"])
    )
    assert abbv_row["day_change_pct"] == pytest.approx(206.47 / 207.18 - 1.0)
    assert abbv_row["day_change_value"] is not None
    assert holdings_payload["totals"]["day_change_value"] is not None
    assert holdings_payload["totals"]["day_change_pct"] is not None
    noncash_rows = [
        row
        for row in holdings_rows
        if row["instrument_core"]["instrument_type"] != "cash"
    ]
    cash_rows = [
        row
        for row in holdings_rows
        if row["instrument_core"]["instrument_type"] == "cash"
    ]
    assert noncash_rows
    assert all(row["unrealized_pnl_base"] is not None for row in noncash_rows)
    assert all(
        row["unrealized_pnl"] is None
        and row["unrealized_pnl_base"] is None
        and row["unrealized_return"] is None
        for row in cash_rows
    )
    expected_unrealized_pnl_base = sum(
        row["unrealized_pnl_base"] for row in noncash_rows
    )
    expected_cost_basis_base = sum(row["cost_basis_base"] for row in noncash_rows)
    assert holdings_payload["totals"]["unrealized_pnl_base"] == pytest.approx(
        expected_unrealized_pnl_base
    )
    assert holdings_payload["totals"]["unrealized_return"] == pytest.approx(
        expected_unrealized_pnl_base / abs(expected_cost_basis_base)
    )

    contribution_response = client.get("/api/portfolios/portfolio-ops/performance/contribution?axis=instrument")
    assert contribution_response.status_code == 200
    assert contribution_response.json()["daily_slices"]

    lookback_response = client.get(
        "/api/portfolios/portfolio-ops/performance/contribution"
        "?axis=instrument&start_date=2025-01-01&end_date=2026-04-15"
    )
    assert lookback_response.status_code == 200
    lookback_payload = lookback_response.json()
    assert lookback_payload["daily_slices"]
    assert lookback_payload["summary"]["start_date"] == lookback_payload["daily_slices"][0]["as_of_date"]


def test_materialized_contribution_rejects_missing_tail_snapshot(client):
    _write_store(deepcopy(TEST_PORTFOLIO_STORE))

    response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert response.status_code == 200

    session_factory = get_session_factory()
    with session_factory() as session:
        latest_snapshot = (
            session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == "portfolio-ops")
            .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
            .first()
        )
        assert latest_snapshot is not None
        latest_snapshot_date = latest_snapshot.as_of_date
        session.delete(latest_snapshot)
        session.commit()

    report = daily_snapshots.build_materialized_contribution_report(
        "portfolio-ops",
        start_date=latest_snapshot_date,
        end_date=latest_snapshot_date,
        axis="instrument",
    )

    assert report is None


def test_daily_snapshot_refresh_endpoint_refreshes_impacted_instrument_portfolios(client):
    _write_store(deepcopy(TEST_PORTFOLIO_STORE))

    initial_response = client.get("/api/portfolios/portfolio-ops/snapshots/daily")
    assert initial_response.status_code == 200

    refresh_response = client.post(
        "/api/portfolios/snapshots/daily/refresh",
        json={
            "instrument_ids": ["fund-us-agg"],
            "dirty_from": "2026-04-14",
        },
    )
    assert refresh_response.status_code == 200
    refresh_payload = refresh_response.json()
    assert refresh_payload["portfolio_ids"] == ["portfolio-ops"]
    assert refresh_payload["refreshed"][0]["snapshot_count"] == _daily_snapshot_row_count("portfolio-ops")

    empty_refresh_response = client.post(
        "/api/portfolios/snapshots/daily/refresh",
        json={"instrument_ids": ["not-held"]},
    )
    assert empty_refresh_response.status_code == 200
    assert empty_refresh_response.json()["portfolio_ids"] == []


def test_daily_twr_neutralizes_external_deposit(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="twr-deposit-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "twr-deposit-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "twr-deposit-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "twr-deposit-test",
                "transaction_type": "deposit",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 50.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Capital contribution.",
                "created_at": "2026-01-02T08:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get("/api/portfolios/twr-deposit-test/performance")
    assert response.status_code == 200
    payload = response.json()
    by_date = {item["as_of_date"]: item for item in payload["daily_series"]}

    assert by_date["2026-01-02"]["beginning_nav"] == 100.0
    assert by_date["2026-01-02"]["ending_nav"] == 160.0
    assert by_date["2026-01-02"]["external_cash_in"] == 50.0
    assert by_date["2026-01-02"]["daily_twr"] == 0.06666666666666665

    contribution_response = client.get(
        "/api/portfolios/twr-deposit-test/performance/contribution?axis=instrument"
    )
    assert contribution_response.status_code == 200
    contribution_summary = contribution_response.json()["summary"]
    assert contribution_summary["portfolio_arithmetic_return"] == pytest.approx(
        0.06666666666666665
    )
    assert contribution_summary["total_period_contribution"] == pytest.approx(
        0.06666666666666665
    )
    assert contribution_summary["contribution_residual"] == pytest.approx(0.0)


def test_external_cash_value_date_is_shared_by_nav_twr_irr_and_period_reports(
    client,
    monkeypatch,
):

    def cash_transaction(
        transaction_id: str,
        transaction_type: str,
        trade_date: str,
        settlement_date: str,
        gross_amount: float,
    ) -> dict[str, object]:
        return {
            "transaction_id": transaction_id,
            "portfolio_id": "external-value-date-test",
            "transaction_type": transaction_type,
            "trade_date": trade_date,
            "settlement_date": settlement_date,
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": None,
            "instrument_id": None,
            "instrument_ref": None,
            "quantity": None,
            "price": None,
            "gross_amount": gross_amount,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "transfer_scope": None,
            "transfer_object_type": None,
            "transfer_group_id": None,
            "counterparty_account_id": None,
            "note": transaction_type,
            "created_at": f"{trade_date}T09:00:00Z",
        }

    store = _minimal_store(
        portfolio_id="external-value-date-test",
        transactions=[
            cash_transaction(
                "txn-opening",
                "opening_balance",
                "2026-01-30",
                "2026-01-30",
                100.0,
            ),
            cash_transaction(
                "txn-deposit",
                "deposit",
                "2026-01-31",
                "2026-02-01",
                50.0,
            ),
            cash_transaction(
                "txn-withdrawal",
                "withdrawal",
                "2026-02-28",
                "2026-03-01",
                20.0,
            ),
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2027-01-30"
    _write_store(store)

    performance_response = client.get(
        "/api/portfolios/external-value-date-test/performance"
    )
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    by_date = {
        point["as_of_date"]: point for point in performance_payload["daily_series"]
    }

    assert by_date["2026-01-31"]["ending_nav"] == pytest.approx(100.0)
    assert by_date["2026-01-31"]["external_cash_in"] == pytest.approx(0.0)
    assert by_date["2026-01-31"]["daily_twr"] == pytest.approx(0.0)
    assert by_date["2026-02-01"]["ending_nav"] == pytest.approx(150.0)
    assert by_date["2026-02-01"]["external_cash_in"] == pytest.approx(50.0)
    assert by_date["2026-02-01"]["daily_twr"] == pytest.approx(0.0)
    assert by_date["2026-02-28"]["ending_nav"] == pytest.approx(150.0)
    assert by_date["2026-02-28"]["external_cash_out"] == pytest.approx(0.0)
    assert by_date["2026-02-28"]["daily_twr"] == pytest.approx(0.0)
    assert by_date["2026-03-01"]["ending_nav"] == pytest.approx(130.0)
    assert by_date["2026-03-01"]["external_cash_out"] == pytest.approx(20.0)
    assert by_date["2026-03-01"]["daily_twr"] == pytest.approx(0.0)

    summary = performance_payload["summary"]
    assert summary["external_cash_in"] == pytest.approx(50.0)
    assert summary["external_cash_out"] == pytest.approx(20.0)
    assert summary["net_external_inflow"] == pytest.approx(30.0)
    assert summary["delta"] == pytest.approx(0.0)
    assert summary["cumulative_twr"] == pytest.approx(0.0)
    assert summary["history_reliability"]["annualized_return_eligible"] is True
    assert summary["irr"] == pytest.approx(0.0, abs=1e-8)

    calendar_response = client.get(
        "/api/portfolios/external-value-date-test/performance/calendar?frequency=monthly"
    )
    assert calendar_response.status_code == 200
    calendar = {
        bucket["bucket_key"]: bucket for bucket in calendar_response.json()["buckets"]
    }
    assert calendar["2026-01"]["external_cash_in"] == pytest.approx(0.0)
    assert calendar["2026-02"]["external_cash_in"] == pytest.approx(50.0)
    assert calendar["2026-02"]["external_cash_out"] == pytest.approx(0.0)
    assert calendar["2026-03"]["external_cash_out"] == pytest.approx(20.0)

    calculation_response = client.get(
        "/api/portfolios/external-value-date-test/performance/calculation"
    )
    assert calculation_response.status_code == 200
    calculation_summary = calculation_response.json()["summary"]
    assert calculation_summary["initial_value"] == pytest.approx(100.0)
    assert calculation_summary["final_value"] == pytest.approx(130.0)
    assert calculation_summary["deposits"] == pytest.approx(50.0)
    assert calculation_summary["withdrawals"] == pytest.approx(20.0)
    assert calculation_summary["net_external_inflow"] == pytest.approx(30.0)
    assert calculation_summary["delta"] == pytest.approx(0.0)

    deposit_entries_response = client.get(
        "/api/portfolios/external-value-date-test/performance/calculation/entries"
        "?axis=account&bucket=deposits"
    )
    withdrawal_entries_response = client.get(
        "/api/portfolios/external-value-date-test/performance/calculation/entries"
        "?axis=account&bucket=withdrawals"
    )
    assert deposit_entries_response.status_code == 200
    assert withdrawal_entries_response.status_code == 200
    deposit_entry = deposit_entries_response.json()["entries"][0]
    withdrawal_entry = withdrawal_entries_response.json()["entries"][0]
    assert deposit_entry["trade_date"] == "2026-01-31"
    assert deposit_entry["settlement_date"] == "2026-02-01"
    assert deposit_entry["effective_date"] == "2026-02-01"
    assert withdrawal_entry["trade_date"] == "2026-02-28"
    assert withdrawal_entry["settlement_date"] == "2026-03-01"
    assert withdrawal_entry["effective_date"] == "2026-03-01"

    deposit_entry_calendar_response = client.get(
        "/api/portfolios/external-value-date-test/performance/calculation/entries/calendar"
        "?axis=account&bucket=deposits&frequency=monthly"
    )
    withdrawal_entry_calendar_response = client.get(
        "/api/portfolios/external-value-date-test/performance/calculation/entries/calendar"
        "?axis=account&bucket=withdrawals&frequency=monthly"
    )
    assert deposit_entry_calendar_response.status_code == 200
    assert withdrawal_entry_calendar_response.status_code == 200
    assert {
        bucket["bucket_key"]
        for bucket in deposit_entry_calendar_response.json()["buckets"]
    } == {"2026-02"}
    assert {
        bucket["bucket_key"]
        for bucket in withdrawal_entry_calendar_response.json()["buckets"]
    } == {"2026-03"}

    portfolio = portfolio_store.get_portfolio("external-value-date-test")
    assert portfolio is not None
    contribution = performance.build_contribution_report(
        portfolio,
        portfolio_store.list_accounts("external-value-date-test"),
        portfolio_store.list_transactions("external-value-date-test"),
        axis="account",
    )
    cash_slices = {
        item["as_of_date"]: item
        for item in contribution["daily_slices"]
        if item["group_key"] == "cash-usd-main"
    }
    assert cash_slices[date(2026, 1, 31)]["capital_flow_in_base"] == pytest.approx(0.0)
    assert cash_slices[date(2026, 2, 1)]["capital_flow_in_base"] == pytest.approx(50.0)
    assert cash_slices[date(2026, 2, 28)]["capital_flow_out_base"] == pytest.approx(0.0)
    assert cash_slices[date(2026, 3, 1)]["capital_flow_out_base"] == pytest.approx(20.0)


@pytest.mark.parametrize(
    ("transaction_type", "calculation_bucket"),
    [("deposit", "deposits"), ("withdrawal", "withdrawals")],
)
def test_external_cash_without_value_date_fails_closed(
    client,
    monkeypatch,
    transaction_type: str,
    calculation_bucket: str,
):
    portfolio_id = f"missing-value-date-{transaction_type}"
    transactions = [
        {
            "transaction_id": "txn-opening",
            "portfolio_id": portfolio_id,
            "transaction_type": "opening_balance",
            "trade_date": "2026-01-01",
            "settlement_date": "2026-01-01",
            "account_id": "cash-usd-main",
            "instrument_id": None,
            "instrument_ref": None,
            "gross_amount": 100.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
        {
            "transaction_id": "txn-invalid-external",
            "portfolio_id": portfolio_id,
            "transaction_type": transaction_type,
            "trade_date": "2026-01-02",
            "settlement_date": None,
            "account_id": "cash-usd-main",
            "instrument_id": None,
            "instrument_ref": None,
            "gross_amount": 20.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    ]
    store = _minimal_store(portfolio_id=portfolio_id, transactions=transactions)
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    original_transaction_serializer = daily_snapshots._serialize_transaction_row

    def serialize_corrupt_historical_transaction(row):
        payload = original_transaction_serializer(row)
        if payload.get("transaction_id") == "txn-invalid-external":
            payload["settlement_date"] = None
        return payload

    monkeypatch.setattr(
        daily_snapshots,
        "_serialize_transaction_row",
        serialize_corrupt_historical_transaction,
    )
    monkeypatch.setattr(
        performance_routes,
        "list_transactions",
        lambda requested_portfolio_id: deepcopy(transactions)
        if requested_portfolio_id == portfolio_id
        else [],
    )

    error_pattern = "requires a valid settlement/value date"
    with pytest.raises(performance.PerformanceDataIntegrityError, match=error_pattern):
        performance.transaction_performance_effective_date(transactions[1])
    with pytest.raises(performance.PerformanceDataIntegrityError, match=error_pattern):
        performance.build_daily_portfolio_snapshots(
            store["portfolios"][0],
            store["accounts"],
            transactions,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
        )
    with pytest.raises(performance.PerformanceDataIntegrityError, match=error_pattern):
        performance.build_period_calculation_entries_report(
            store["portfolios"][0],
            store["accounts"],
            transactions,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
            axis="account",
            bucket=calculation_bucket,
        )

    for endpoint in (
        f"/api/portfolios/{portfolio_id}/snapshots/daily",
        f"/api/portfolios/{portfolio_id}/performance",
        f"/api/portfolios/{portfolio_id}/performance/calculation",
    ):
        response = client.get(endpoint)
        assert response.status_code == 422
        assert error_pattern in response.json()["detail"]


def test_failed_value_date_refresh_preserves_last_good_materialization(
    monkeypatch,
):
    portfolio_id = "value-date-refresh-preservation"
    transactions = [
        {
            "transaction_id": "txn-opening",
            "portfolio_id": portfolio_id,
            "transaction_type": "opening_balance",
            "trade_date": "2026-01-01",
            "settlement_date": "2026-01-01",
            "account_id": "cash-usd-main",
            "instrument_id": None,
            "instrument_ref": None,
            "gross_amount": 100.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
        {
            "transaction_id": "txn-deposit",
            "portfolio_id": portfolio_id,
            "transaction_type": "deposit",
            "trade_date": "2026-01-02",
            "settlement_date": "2026-01-02",
            "account_id": "cash-usd-main",
            "instrument_id": None,
            "instrument_ref": None,
            "gross_amount": 20.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        },
    ]
    store = _minimal_store(portfolio_id=portfolio_id, transactions=transactions)
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)
    assert daily_snapshots.refresh_portfolio_daily_snapshots(portfolio_id) is not None

    with get_session_factory()() as session:
        before = [
            deepcopy(row.snapshot_json)
            for row in session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == portfolio_id)
            .order_by(PortfolioDailySnapshotModel.as_of_date)
            .all()
        ]
    assert before

    original_transaction_serializer = daily_snapshots._serialize_transaction_row

    def serialize_corrupt_historical_transaction(row):
        payload = original_transaction_serializer(row)
        if payload.get("transaction_id") == "txn-deposit":
            payload["settlement_date"] = None
        return payload

    monkeypatch.setattr(
        daily_snapshots,
        "_serialize_transaction_row",
        serialize_corrupt_historical_transaction,
    )
    daily_snapshots.mark_portfolio_daily_snapshots_stale(
        portfolio_id,
        dirty_from=date(2026, 1, 1),
    )
    with pytest.raises(
        performance.PerformanceDataIntegrityError,
        match="requires a valid settlement/value date",
    ):
        daily_snapshots.refresh_portfolio_daily_snapshots(portfolio_id)

    with get_session_factory()() as session:
        after = [
            deepcopy(row.snapshot_json)
            for row in session.query(PortfolioDailySnapshotModel)
            .filter(PortfolioDailySnapshotModel.portfolio_id == portfolio_id)
            .order_by(PortfolioDailySnapshotModel.as_of_date)
            .all()
        ]
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
    assert after == before
    assert state is not None
    assert state.daily_snapshot_status == "failed"
    assert "requires a valid settlement/value date" in str(state.error_message)


def test_contribution_entry_calendar_uses_entitlement_effective_date_across_month(
    monkeypatch,
):
    portfolio = {
        "portfolio_id": "entry-effective-date-test",
        "base_currency": "USD",
        "as_of_date": "2026-02-02",
    }
    accounts = [
        {
            "account_id": "broker-us-core",
            "portfolio_id": "entry-effective-date-test",
            "account_name": "Core Brokerage",
            "account_type": "securities_account",
            "currency": "USD",
        }
    ]
    transactions = [
        {
            "transaction_id": "txn-dividend",
            "portfolio_id": "entry-effective-date-test",
            "transaction_type": "dividend",
            "trade_date": "2026-01-31",
            "settlement_date": "2026-02-02",
            "entitlement_date": "2026-02-01",
            "account_id": "broker-us-core",
            "instrument_id": "equity-us-abbv",
            "instrument_ref": {
                "instrument_id": "equity-us-abbv",
                "instrument_name": "AbbVie Inc",
                "instrument_type": "equity",
                "currency": "USD",
                "identifiers": [],
            },
            "gross_amount": 10.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
        }
    ]

    report = performance.build_contribution_entries_calendar_report(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 2, 2),
        axis="account",
        bucket="income_cash_amount",
        frequency="monthly",
    )

    assert len(report["buckets"]) == 1
    assert report["buckets"][0]["bucket_key"] == "2026-02"
    assert report["buckets"][0]["start_date"] == date(2026, 2, 1)
    assert report["buckets"][0]["end_date"] == date(2026, 2, 1)


def test_inception_day_twr_includes_bod_funding_and_first_day_pnl(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-inception",
        instrument_name="Inception Equity",
        history=[("2026-01-01", "110.00")],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-inception" else None,
    )
    instrument_ref = {
        "instrument_id": "equity-us-inception",
        "instrument_name": "Inception Equity",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [],
    }
    store = _minimal_store(
        portfolio_id="inception-return-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "inception-return-test",
                "transaction_type": "deposit",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "created_at": "2026-01-01T08:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "inception-return-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-inception",
                "instrument_ref": instrument_ref,
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "created_at": "2026-01-01T09:00:00Z",
            },
        ],
    )
    _write_store(store)

    payload = client.get("/api/portfolios/inception-return-test/performance").json()
    first_day = payload["daily_series"][0]
    assert first_day["beginning_nav"] == pytest.approx(0.0)
    assert first_day["ending_nav"] == pytest.approx(110.0)
    assert first_day["daily_twr"] == pytest.approx(0.10)
    assert payload["summary"]["start_nav"] == pytest.approx(0.0)
    assert payload["summary"]["external_cash_in"] == pytest.approx(100.0)
    assert payload["summary"]["delta"] == pytest.approx(10.0)
    assert payload["summary"]["cumulative_twr"] == pytest.approx(0.10)

    contribution_response = client.get(
        "/api/portfolios/inception-return-test/performance/contribution?axis=instrument"
    )
    assert contribution_response.status_code == 200
    contribution_summary = contribution_response.json()["summary"]
    assert contribution_summary["portfolio_arithmetic_return"] == pytest.approx(0.10)
    assert contribution_summary["total_period_contribution"] == pytest.approx(0.10)
    assert contribution_summary["contribution_residual"] == pytest.approx(0.0)


def test_default_inception_calculation_counts_first_day_deposit_once(client, monkeypatch):
    portfolio_id = "inception-calculation-deposit-test"
    store = _minimal_store(
        portfolio_id=portfolio_id,
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": portfolio_id,
                "transaction_type": "deposit",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "created_at": "2026-01-01T08:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": portfolio_id,
                "transaction_type": "interest",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "created_at": "2026-01-02T08:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get(f"/api/portfolios/{portfolio_id}/performance/calculation")

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["nav_coverage_state"] == "complete"
    assert summary["book_pnl_coverage_state"] == "complete"
    assert summary["initial_value"] == pytest.approx(0.0)
    assert summary["final_value"] == pytest.approx(220.0)
    assert summary["deposits"] == pytest.approx(200.0)
    assert summary["net_external_inflow"] == pytest.approx(200.0)
    assert summary["delta"] == pytest.approx(20.0)
    assert summary["earnings"] == pytest.approx(20.0)
    assert summary["capital_gains"] == pytest.approx(0.0)
    assert summary["realized_capital_gains"] == pytest.approx(0.0)
    assert summary["unrealized_capital_gains"] == pytest.approx(0.0)


def test_same_day_inception_cash_flows_have_no_money_weighted_return(client, monkeypatch):
    portfolio_id = "same-day-inception-mwrr-test"
    store = _minimal_store(
        portfolio_id=portfolio_id,
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": portfolio_id,
                "transaction_type": "deposit",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200_000.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "created_at": "2026-01-01T08:00:00Z",
            }
        ],
    )
    _write_store(store)

    performance_response = client.get(f"/api/portfolios/{portfolio_id}/performance")
    calculation_response = client.get(f"/api/portfolios/{portfolio_id}/performance/calculation")

    assert performance_response.status_code == 200
    performance_summary = performance_response.json()["summary"]
    assert performance_summary["cumulative_twr"] == pytest.approx(0.0)
    assert performance_summary["irr"] is None
    assert performance_summary["mwror"] is None

    assert calculation_response.status_code == 200
    calculation_summary = calculation_response.json()["summary"]
    assert calculation_summary["nav_coverage_state"] == "complete"
    assert calculation_summary["book_pnl_coverage_state"] == "complete"
    assert calculation_summary["initial_value"] == pytest.approx(0.0)
    assert calculation_summary["final_value"] == pytest.approx(200_000.0)
    assert calculation_summary["deposits"] == pytest.approx(200_000.0)
    assert calculation_summary["delta"] == pytest.approx(0.0)
    assert calculation_summary["capital_gains"] == pytest.approx(0.0)

    daily_snapshots.refresh_portfolio_daily_snapshots(portfolio_id)
    portfolios_response = client.get("/api/portfolios")
    assert portfolios_response.status_code == 200
    portfolio_summary = next(
        item for item in portfolios_response.json() if item["portfolio_id"] == portfolio_id
    )
    assert portfolio_summary["nav"] == pytest.approx(200_000.0)
    assert portfolio_summary["day_change_value"] == pytest.approx(0.0)
    assert portfolio_summary["day_change_pct"] == pytest.approx(0.0)


def test_xirr_rejects_zero_duration_even_when_same_day_flows_do_not_net_to_zero():
    assert performance._solve_xirr(
        [
            (date(2026, 1, 1), -100.0),
            (date(2026, 1, 1), 110.0),
        ]
    ) is None


def test_dividend_receivable_is_accrued_on_entitlement_date(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-dividend",
        instrument_name="Dividend Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "90.00"),
            ("2026-01-03", "90.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-dividend" else None,
    )
    instrument_ref = {
        "instrument_id": "equity-us-dividend",
        "instrument_name": "Dividend Equity",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [],
    }
    store = _minimal_store(
        portfolio_id="dividend-receivable-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "dividend-receivable-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": None,
                "instrument_id": "equity-us-dividend",
                "instrument_ref": instrument_ref,
                "quantity": 1.0,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "dividend-receivable-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-03",
                "entitlement_date": "2026-01-02",
                "settlement_date": "2026-01-03",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-dividend",
                "instrument_ref": instrument_ref,
                "quantity": None,
                "price": None,
                "gross_amount": 10.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "created_at": "2026-01-03T09:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    snapshots = client.get("/api/portfolios/dividend-receivable-test/snapshots/daily").json()["snapshots"]
    by_date = {item["as_of_date"]: item for item in snapshots}
    assert by_date["2026-01-02"]["position_market_value"] == pytest.approx(90.0)
    assert by_date["2026-01-02"]["pending_settlement"] == pytest.approx(10.0)
    assert by_date["2026-01-02"]["nav"] == pytest.approx(100.0)
    assert by_date["2026-01-02"]["daily_twr"] == pytest.approx(0.0)
    assert by_date["2026-01-03"]["pending_settlement"] == pytest.approx(0.0)
    assert by_date["2026-01-03"]["nav"] == pytest.approx(100.0)
    assert by_date["2026-01-03"]["daily_twr"] == pytest.approx(0.0)


def test_explicit_performance_period_uses_beginning_nav_boundary(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-03-31", "100.00"),
            ("2026-04-01", "110.00"),
            ("2026-04-20", "120.00"),
        ],
    )
    instrument_ref = {
        "instrument_id": "equity-us-test",
        "instrument_name": "Test Equity",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="period-boundary-nav-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "period-boundary-nav-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-03-31",
                "settlement_date": "2026-03-31",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-03-31T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "period-boundary-nav-test",
                "transaction_type": "buy",
                "trade_date": "2026-03-31",
                "settlement_date": "2026-03-31",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": instrument_ref,
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-03-31T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-04-20"
    _write_store(store)

    response = client.get(
        "/api/portfolios/period-boundary-nav-test/performance?start_date=2026-04-01&end_date=2026-04-20"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    first_point = payload["daily_series"][0]

    assert summary["start_date"] == "2026-04-01"
    assert summary["end_date"] == "2026-04-20"
    assert isclose(summary["start_nav"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["end_nav"], 120.0, rel_tol=0.0, abs_tol=1e-12)
    assert first_point["as_of_date"] == "2026-04-01"
    assert isclose(first_point["beginning_nav"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(first_point["ending_nav"], 110.0, rel_tol=0.0, abs_tol=1e-12)

    calculation_response = client.get(
        "/api/portfolios/period-boundary-nav-test/performance/calculation?start_date=2026-04-01&end_date=2026-04-20"
    )
    assert calculation_response.status_code == 200
    calculation_summary = calculation_response.json()["summary"]
    assert isclose(calculation_summary["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["final_value"], 120.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["delta"], 20.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_clamps_future_end_date_to_portfolio_as_of(client):
    _write_store(deepcopy(TEST_PORTFOLIO_STORE))

    response = client.get(
        "/api/portfolios/portfolio-ops/performance/calculation?start_date=2026-04-12&end_date=2026-05-11"
    )
    assert response.status_code == 200
    assert response.json()["summary"]["end_date"] == "2026-04-15"

    groups_response = client.get(
        "/api/portfolios/portfolio-ops/performance/calculation/groups"
        "?axis=instrument&start_date=2026-04-12&end_date=2026-05-11"
    )
    assert groups_response.status_code == 200
    assert groups_response.json()["summary"]["end_date"] == "2026-04-15"


def test_daily_twr_ignores_internal_sale_but_cuts_on_withdrawal(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="twr-internal-sale-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "twr-internal-sale-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "twr-internal-sale-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "twr-internal-sale-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell test equity.",
                "created_at": "2026-01-02T15:00:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "twr-internal-sale-test",
                "transaction_type": "withdrawal",
                "trade_date": "2026-01-03",
                "settlement_date": "2026-01-03",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Withdraw proceeds.",
                "created_at": "2026-01-03T10:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/twr-internal-sale-test/performance")
    assert response.status_code == 200
    payload = response.json()
    by_date = {item["as_of_date"]: item for item in payload["daily_series"]}

    assert by_date["2026-01-02"]["external_cash_in"] == 0.0
    assert by_date["2026-01-02"]["external_cash_out"] == 0.0
    assert by_date["2026-01-02"]["daily_twr"] == 0.10000000000000009
    assert by_date["2026-01-03"]["external_cash_out"] == 110.0
    assert by_date["2026-01-03"]["daily_twr"] == 0.0


def test_performance_summary_reports_one_year_twr_annualized_and_irr(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2027-01-01", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="performance-one-year-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "performance-one-year-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "performance-one-year-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2027-01-01"
    _write_store(store)

    response = client.get("/api/portfolios/performance-one-year-test/performance")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    last_point = payload["daily_series"][-1]
    expected_annualized = (1.1 ** (365.25 / 365.0)) - 1.0

    assert isclose(summary["cumulative_twr"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["annualized_twr"], expected_annualized, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["irr"], expected_annualized, rel_tol=0.0, abs_tol=1e-10)
    assert isclose(summary["mwror"], summary["irr"], rel_tol=0.0, abs_tol=1e-12)
    assert summary["external_cash_in"] == 0.0
    assert summary["external_cash_out"] == 0.0
    assert summary["realized_pnl"] == 0.0
    assert summary["unrealized_pnl"] == 10.0
    assert summary["total_pnl"] == 10.0
    assert last_point["cumulative_twr"] == summary["cumulative_twr"]
    assert last_point["unrealized_pnl"] == 10.0


def test_performance_summary_reports_pnl_decomposition_and_risk_metrics(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="performance-pnl-risk-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "performance-pnl-risk-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "performance-pnl-risk-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "performance-pnl-risk-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell test equity.",
                "created_at": "2026-01-02T15:00:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "performance-pnl-risk-test",
                "transaction_type": "withdrawal",
                "trade_date": "2026-01-03",
                "settlement_date": "2026-01-03",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Withdraw proceeds.",
                "created_at": "2026-01-03T10:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/performance-pnl-risk-test/performance")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    by_date = {item["as_of_date"]: item for item in payload["daily_series"]}
    expected_daily_returns = [0.10000000000000009]
    expected_mean_daily_return = sum(expected_daily_returns) / len(expected_daily_returns)
    expected_periods_per_year = 1 / 2 * performance.DAYS_PER_YEAR
    expected_annualized_mean = expected_mean_daily_return * expected_periods_per_year

    assert summary["realized_pnl"] == 10.0
    assert summary["unrealized_pnl"] == 0.0
    assert summary["income_cash_amount"] == 0.0
    assert summary["expense_cash_amount"] == 0.0
    assert summary["total_pnl"] == 10.0
    assert summary["return_observation_count"] == 2
    assert summary["risk_return_observation_count"] == 1
    assert isclose(summary["risk_annualization_periods_per_year"], expected_periods_per_year, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["mean_daily_return"], expected_mean_daily_return, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["annualized_return_from_daily_mean"], expected_annualized_mean, rel_tol=0.0, abs_tol=1e-12)
    assert summary["annualized_volatility"] is None
    assert summary["annualized_downside_volatility"] is None
    assert summary["sharpe_ratio"] is None
    assert summary["sortino_ratio"] is None
    assert summary["max_drawdown"] == 0.0
    assert summary["max_drawdown_days"] == 0
    assert summary["drawdown_duration_days"] is None
    assert by_date["2026-01-02"]["realized_pnl"] == 10.0
    assert by_date["2026-01-03"]["realized_pnl"] == 10.0
    assert by_date["2026-01-03"]["total_pnl"] == 10.0


def test_risk_metrics_exclude_carry_forward_non_trading_days(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-02", "100.00"),
            ("2026-01-05", "110.00"),
            ("2026-01-06", "105.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="non-trading-risk-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "non-trading-risk-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-02T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "non-trading-risk-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-02T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-06"
    _write_store(store)

    response = client.get("/api/portfolios/non-trading-risk-test/performance")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    by_date = {item["as_of_date"]: item for item in payload["daily_series"]}
    risk_returns = [0.10, 105.0 / 110.0 - 1.0]
    mean_return = sum(risk_returns) / len(risk_returns)
    daily_stddev = sqrt(sum((value - mean_return) ** 2 for value in risk_returns) / (len(risk_returns) - 1))
    downside_deviation = sqrt(sum(min(0.0, value) ** 2 for value in risk_returns) / len(risk_returns))
    expected_periods_per_year = 2 / 4 * performance.DAYS_PER_YEAR
    expected_annualized_mean = mean_return * expected_periods_per_year
    expected_annualized_volatility = daily_stddev * sqrt(expected_periods_per_year)
    expected_annualized_downside_volatility = downside_deviation * sqrt(expected_periods_per_year)

    assert by_date["2026-01-03"]["market_observation_count"] == 0
    assert by_date["2026-01-03"]["return_observation_eligible"] is False
    assert by_date["2026-01-03"]["twr_state"] == "carry_forward"
    assert by_date["2026-01-03"]["twr_reliability_status"] == "qualified"
    assert by_date["2026-01-03"]["twr_reliability_reasons"] == [
        "carried_forward_valuation_without_external_flow"
    ]
    assert by_date["2026-01-04"]["market_observation_count"] == 0
    assert by_date["2026-01-04"]["return_observation_eligible"] is False
    assert by_date["2026-01-04"]["twr_state"] == "carry_forward"
    assert by_date["2026-01-04"]["twr_reliability_status"] == "qualified"
    assert by_date["2026-01-04"]["twr_reliability_reasons"] == [
        "carried_forward_valuation_without_external_flow"
    ]
    assert by_date["2026-01-05"]["market_observation_count"] == 1
    assert by_date["2026-01-05"]["return_observation_eligible"] is True
    assert by_date["2026-01-06"]["market_observation_count"] == 1
    assert by_date["2026-01-06"]["return_observation_eligible"] is True
    assert summary["return_observation_count"] == 4
    assert summary["risk_return_observation_count"] == 2
    assert summary["twr_state"] == "carry_forward"
    assert summary["twr_reliability_status"] == "qualified"
    assert summary["twr_reliability_reasons"] == [
        "carried_forward_valuation_without_external_flow"
    ]
    assert isclose(summary["risk_annualization_periods_per_year"], expected_periods_per_year, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["mean_daily_return"], sum(risk_returns) / len(risk_returns), rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["annualized_return_from_daily_mean"], expected_annualized_mean, rel_tol=0.0, abs_tol=1e-12)
    assert summary["annualized_twr"] is None
    assert summary["history_reliability"]["annualized_return_eligible"] is False
    assert isclose(summary["annualized_volatility"], expected_annualized_volatility, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["annualized_downside_volatility"], expected_annualized_downside_volatility, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["sharpe_ratio"], expected_annualized_mean / expected_annualized_volatility, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["sortino_ratio"], expected_annualized_mean / expected_annualized_downside_volatility, rel_tol=0.0, abs_tol=1e-12)

    calendar_response = client.get(
        "/api/portfolios/non-trading-risk-test/performance/calendar?frequency=monthly"
    )
    assert calendar_response.status_code == 200
    calendar_payload = calendar_response.json()
    assert calendar_payload["summary"]["twr_reliability_reasons"] == [
        "carried_forward_valuation_without_external_flow"
    ]
    assert calendar_payload["buckets"][0]["twr_reliability_reasons"] == [
        "carried_forward_valuation_without_external_flow"
    ]

    contribution_response = client.get(
        "/api/portfolios/non-trading-risk-test/performance/contribution?axis=instrument"
    )
    assert contribution_response.status_code == 200
    contribution_payload = contribution_response.json()
    assert contribution_payload["summary"]["twr_reliability_reasons"] == [
        "carried_forward_valuation_without_external_flow"
    ]
    assert all(
        line["twr_reliability_reasons"]
        == ["carried_forward_valuation_without_external_flow"]
        for line in contribution_payload["lines"]
    )

    window_response = client.get(
        "/api/portfolios/non-trading-risk-test/performance?start_date=2026-01-05&end_date=2026-01-06"
    )
    assert window_response.status_code == 200
    window_payload = window_response.json()
    assert window_payload["summary"]["start_date"] == "2026-01-05"
    assert window_payload["summary"]["start_nav"] == 100.0
    assert isclose(window_payload["summary"]["cumulative_twr"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_external_flow_on_carried_valuation_breaks_twr_until_fresh_reanchor(monkeypatch):
    instrument_id = "equity-us-twr-boundary"
    instrument_detail = _test_instrument_detail(
        instrument_id=instrument_id,
        instrument_name="TWR Boundary Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "105.00"),
                ("2026-01-04", "120.00"),
                ("2026-01-05", "126.00"),
            ],
        )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda requested_id: deepcopy(instrument_detail)
        if requested_id == instrument_id
        else None,
    )

    portfolio_id = "twr-reliability-boundary-test"
    instrument_ref = {
        "instrument_id": instrument_id,
        "instrument_name": "TWR Boundary Equity",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "TWRB",
                "is_primary": True,
            }
        ],
    }
    transactions = [
        {
            "transaction_id": "txn-0001",
            "portfolio_id": portfolio_id,
            "transaction_type": "opening_balance",
            "trade_date": "2026-01-01",
            "settlement_date": "2026-01-01",
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": None,
            "instrument_id": None,
            "instrument_ref": None,
            "quantity": None,
            "price": None,
            "gross_amount": 100.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "created_at": "2026-01-01T09:00:00Z",
        },
        {
            "transaction_id": "txn-0002",
            "portfolio_id": portfolio_id,
            "transaction_type": "buy",
            "trade_date": "2026-01-01",
            "settlement_date": "2026-01-01",
            "account_id": "broker-us-core",
            "settlement_cash_account_id": "cash-usd-main",
            "instrument_id": instrument_id,
            "instrument_ref": instrument_ref,
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "created_at": "2026-01-01T09:30:00Z",
        },
        {
            "transaction_id": "txn-0003",
            "portfolio_id": portfolio_id,
            "transaction_type": "deposit",
            "trade_date": "2026-01-03",
            "settlement_date": "2026-01-03",
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": None,
            "instrument_id": None,
            "instrument_ref": None,
            "quantity": None,
            "price": None,
            "gross_amount": 50.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "created_at": "2026-01-03T09:00:00Z",
        },
    ]
    store = _minimal_store(portfolio_id=portfolio_id, transactions=transactions)
    portfolio = store["portfolios"][0]
    portfolio["as_of_date"] = "2026-01-05"
    accounts = store["accounts"]
    _write_store(store)

    snapshots = performance.build_daily_portfolio_snapshots(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
    )
    by_date = {snapshot["as_of_date"]: snapshot for snapshot in snapshots}

    assert by_date[date(2026, 1, 2)]["twr_state"] == "linked"
    assert by_date[date(2026, 1, 3)]["twr_state"] == "broken"
    assert by_date[date(2026, 1, 3)]["twr_reliability_reasons"] == [
        "carried_forward_valuation_on_external_flow"
    ]
    assert by_date[date(2026, 1, 3)]["daily_twr"] is None
    assert by_date[date(2026, 1, 4)]["twr_state"] == "reanchor"
    assert by_date[date(2026, 1, 4)]["daily_twr"] is None
    assert by_date[date(2026, 1, 5)]["twr_state"] == "linked"
    assert by_date[date(2026, 1, 5)]["daily_twr"] == pytest.approx(
        176.0 / 170.0 - 1.0
    )
    for boundary_date in (date(2026, 1, 3), date(2026, 1, 4), date(2026, 1, 5)):
        assert by_date[boundary_date]["cumulative_twr"] is None
        assert by_date[boundary_date]["drawdown"] is None

    full_report = performance.build_portfolio_performance_report_from_snapshots(
        portfolio,
        snapshots,
        transactions=transactions,
    )
    assert full_report["summary"]["twr_reliability_status"] == "unavailable"
    assert full_report["summary"]["twr_reliability_reasons"] == [
        "crosses_broken_twr_boundary"
    ]


    assert full_report["summary"]["cumulative_twr"] is None
    assert full_report["summary"]["annualized_twr"] is None
    assert full_report["summary"]["max_drawdown"] is None

    post_reanchor_report = performance.build_portfolio_performance_report_from_snapshots(
        portfolio,
        snapshots,
        transactions=transactions,
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 5),
    )
    assert post_reanchor_report["summary"]["twr_reliability_status"] == "reliable"
    assert post_reanchor_report["summary"]["cumulative_twr"] == pytest.approx(
        176.0 / 170.0 - 1.0
    )

    contribution_report = performance.build_contribution_report(
        portfolio,
        accounts,
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
        axis="instrument",
    )
    assert contribution_report["summary"]["twr_reliability_status"] == "unavailable"
    assert contribution_report["summary"]["portfolio_arithmetic_return"] is None
    assert contribution_report["summary"]["portfolio_cumulative_twr"] is None
    assert contribution_report["summary"]["total_period_contribution"] is None
    assert contribution_report["summary"]["contribution_residual"] is None
    assert contribution_report["lines"]
    assert all(
        line["period_contribution"] is None
        for line in contribution_report["lines"]
    )


def test_daily_snapshot_quote_query_count_is_independent_of_day_count(monkeypatch):
    instrument_id = "equity-us-window-query-count"
    first_date = date(2026, 1, 1)
    instrument_detail = _test_instrument_detail(
        instrument_id=instrument_id,
        instrument_name="Window Query Count Equity",
        history=[
            (
                (first_date + timedelta(days=offset)).isoformat(),
                str(100 + offset),
            )
            for offset in range(90)
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda requested_id: deepcopy(instrument_detail)
        if requested_id == instrument_id
        else None,
    )
    transactions = [
        {
            "transaction_id": "txn-0001",
            "portfolio_id": "window-query-count-test",
            "transaction_type": "opening_balance",
            "trade_date": first_date.isoformat(),
            "settlement_date": first_date.isoformat(),
            "account_id": "broker-us-core",
            "settlement_cash_account_id": None,
            "instrument_id": instrument_id,
            "instrument_ref": {
                "instrument_id": instrument_id,
                "instrument_name": "Window Query Count Equity",
                "instrument_type": "equity",
                "currency": "USD",
                "identifiers": [],
            },
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "created_at": "2026-01-01T09:00:00Z",
        }
    ]
    store = _minimal_store(
        portfolio_id="window-query-count-test",
        transactions=transactions,
    )
    store["portfolios"][0]["as_of_date"] = "2026-03-31"
    _write_store(store)

    def quote_query_count(end_date: date) -> tuple[int, int]:
        statements: list[str] = []

        def record_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            normalized = str(statement).lower()
            if "instrument" in normalized or "quote_" in normalized:
                statements.append(normalized)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            with get_session_factory()() as quote_session:
                snapshots = performance.build_daily_portfolio_snapshots(
                    store["portfolios"][0],
                    store["accounts"],
                    transactions,
                    start_date=first_date,
                    end_date=end_date,
                    include_materialized_rows=True,
                    quote_session=quote_session,
                )
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)
        return len(statements), len(snapshots)

    short_query_count, short_snapshot_count = quote_query_count(
        first_date + timedelta(days=4)
    )
    long_query_count, long_snapshot_count = quote_query_count(
        first_date + timedelta(days=89)
    )

    assert short_snapshot_count == 5
    assert long_snapshot_count == 90
    assert short_query_count == long_query_count == 5


def test_daily_snapshot_fx_query_count_is_independent_of_day_count(monkeypatch):
    first_date = date(2026, 1, 1)
    fx_detail = {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "USDHKD",
                "is_primary": True,
            }
        ],
        "quote_selection_policy": {
            "valuation": ["spot"],
            "reference": ["spot"],
        },
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": (first_date + timedelta(days=offset)).isoformat(),
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            }
            for offset in range(90)
        ],
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(fx_detail)
        if instrument_id == "fx-usd-hkd"
        else None,
    )
    transactions = [
        {
            "transaction_id": "txn-0001",
            "portfolio_id": "fx-window-query-count-test",
            "transaction_type": "opening_balance",
            "trade_date": first_date.isoformat(),
            "settlement_date": first_date.isoformat(),
            "account_id": "cash-hkd-main",
            "settlement_cash_account_id": None,
            "instrument_id": None,
            "instrument_ref": None,
            "quantity": None,
            "price": None,
            "gross_amount": 780.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "HKD",
            "created_at": "2026-01-01T09:00:00Z",
        }
    ]
    store = {
        "portfolios": [
            {
                "portfolio_id": "fx-window-query-count-test",
                "portfolio_name": "FX Window Query Count",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-03-31",
                "nav": 100.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 0,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "fx-window-query-count-test",
                "account_name": "HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_instrument_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": transactions,
    }
    _write_store(store)

    def fx_query_count(end_date: date) -> tuple[int, int]:
        statements: list[str] = []

        def record_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            normalized = str(statement).lower()
            if normalized.lstrip().startswith("select") and (
                "instrument" in normalized or "quote_" in normalized
            ):
                statements.append(normalized)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            with get_session_factory()() as quote_session:
                snapshots = performance.build_daily_portfolio_snapshots(
                    store["portfolios"][0],
                    store["accounts"],
                    transactions,
                    start_date=first_date,
                    end_date=end_date,
                    quote_session=quote_session,
                )
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)
        return len(statements), len(snapshots)

    short_query_count, short_snapshot_count = fx_query_count(
        first_date + timedelta(days=4)
    )
    long_query_count, long_snapshot_count = fx_query_count(
        first_date + timedelta(days=89)
    )

    assert short_snapshot_count == 5
    assert long_snapshot_count == 90
    assert short_query_count == long_query_count == 3


def test_materialized_window_summary_rebases_twr_and_drawdown(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
            ("2026-01-04", "108.90"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="window-rebase-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "window-rebase-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "window-rebase-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-04"
    _write_store(store)

    full_response = client.get("/api/portfolios/window-rebase-test/performance")
    assert full_response.status_code == 200
    assert isclose(full_response.json()["summary"]["cumulative_twr"], 0.089, rel_tol=0.0, abs_tol=1e-12)

    window_response = client.get(
        "/api/portfolios/window-rebase-test/performance?start_date=2026-01-03&end_date=2026-01-04"
    )
    assert window_response.status_code == 200
    window_payload = window_response.json()
    summary = window_payload["summary"]
    by_date = {item["as_of_date"]: item for item in window_payload["daily_series"]}
    assert isclose(summary["cumulative_twr"], -0.01, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["current_drawdown"], -0.10, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["max_drawdown"], -0.10, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(by_date["2026-01-03"]["cumulative_twr"], 0.10, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(by_date["2026-01-04"]["cumulative_twr"], -0.01, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(by_date["2026-01-04"]["drawdown"], -0.10, rel_tol=0.0, abs_tol=1e-12)


def test_window_drawdown_uses_period_start_anchor_when_first_return_is_loss(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "90.00"),
            ("2026-01-03", "100.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="drawdown-anchor-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "drawdown-anchor-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "drawdown-anchor-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/drawdown-anchor-test/performance?start_date=2026-01-02&end_date=2026-01-03"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    by_date = {item["as_of_date"]: item for item in payload["daily_series"]}
    assert isclose(summary["current_drawdown"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["max_drawdown"], -0.10, rel_tol=0.0, abs_tol=1e-12)
    assert summary["max_drawdown_days"] == 1
    assert summary["drawdown_duration_days"] == 2
    assert isclose(by_date["2026-01-02"]["drawdown"], -0.10, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(by_date["2026-01-03"]["drawdown"], 0.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_report_reconciles_initial_delta_transfers_and_final_value(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="period-calculation-bridge-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "period-calculation-bridge-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "period-calculation-bridge-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "period-calculation-bridge-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell test equity.",
                "created_at": "2026-01-02T15:00:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "period-calculation-bridge-test",
                "transaction_type": "withdrawal",
                "trade_date": "2026-01-03",
                "settlement_date": "2026-01-03",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Withdraw proceeds.",
                "created_at": "2026-01-03T10:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/period-calculation-bridge-test/performance/calculation")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    lines = {item["key"]: item for item in payload["lines"]}

    assert summary["initial_value"] == 100.0
    assert summary["final_value"] == 0.0
    assert summary["delta"] == 10.0
    assert summary["capital_gains"] == 10.0
    assert summary["realized_capital_gains"] == 10.0
    assert summary["unrealized_capital_gains"] == 0.0
    assert summary["earnings"] == 0.0
    assert summary["fees"] == 0.0
    assert summary["taxes"] == 0.0
    assert summary["deposits"] == 0.0
    assert summary["withdrawals"] == 110.0
    assert summary["net_external_inflow"] == -110.0
    assert "cash_fx_residual_gains" not in summary
    assert lines["performance_neutral_transfers"]["amount"] == -110.0
    assert lines["capital_gains"]["amount"] == 10.0
    assert lines["realized_capital_gains"]["amount"] == 10.0
    assert lines["unrealized_capital_gains"]["amount"] == 0.0
    assert lines["deposits"]["amount"] == 0.0
    assert lines["withdrawals"]["amount"] == 110.0


def test_period_calculation_report_splits_earnings_fees_and_taxes(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "100.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="period-calculation-income-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "period-calculation-income-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 1000.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "period-calculation-income-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "period-calculation-income-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Dividend payment.",
                "created_at": "2026-01-02T10:00:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "period-calculation-income-test",
                "transaction_type": "fee",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 2.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Account fee.",
                "created_at": "2026-01-02T11:00:00Z",
            },
            {
                "transaction_id": "txn-0005",
                "portfolio_id": "period-calculation-income-test",
                "transaction_type": "tax",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 3.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Tax payment.",
                "created_at": "2026-01-02T12:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/period-calculation-income-test/performance/calculation")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]

    assert summary["initial_value"] == 1000.0
    assert summary["final_value"] == 1015.0
    assert summary["delta"] == 15.0
    assert summary["capital_gains"] == 0.0
    assert summary["realized_capital_gains"] == 0.0
    assert summary["earnings"] == 20.0
    assert summary["fees"] == 2.0
    assert summary["taxes"] == 3.0
    assert summary["deposits"] == 0.0
    assert summary["withdrawals"] == 0.0
    assert summary["net_external_inflow"] == 0.0
    assert "cash_fx_residual_gains" not in summary


def test_period_boundary_holdings_report_returns_start_and_end_positions(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="boundary-holdings-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "boundary-holdings-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "boundary-holdings-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "boundary-holdings-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell test equity.",
                "created_at": "2026-01-02T15:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/boundary-holdings-test/performance/boundary-holdings")
    assert response.status_code == 200
    payload = response.json()

    assert payload["summary"]["start_position_count"] == 1
    assert payload["summary"]["end_position_count"] == 0
    assert payload["summary"]["start_total_market_value_base"] == 100.0
    assert payload["summary"]["end_total_market_value_base"] == 0.0
    assert payload["start_positions"][0]["instrument_id"] == "equity-us-test"
    assert payload["start_positions"][0]["instrument_ref"]["instrument_id"] == "equity-us-test"
    assert payload["start_positions"][0]["instrument_ref"]["instrument_name"] == "Test Equity"
    assert payload["start_positions"][0]["instrument_ref"]["instrument_type"] == "equity"
    assert payload["start_positions"][0]["account_ids"] == ["broker-us-core"]
    assert payload["start_positions"][0]["market_value_base"] == 100.0
    assert payload["start_positions"][0]["portfolio_weight"] == 1.0
    assert payload["end_positions"] == []


def test_period_boundary_holdings_can_filter_by_account_group(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="boundary-holdings-account-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "boundary-holdings-account-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "boundary-holdings-account-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/boundary-holdings-account-filter-test/performance/boundary-holdings"
        "?axis=account&group_key=broker-us-core"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "account"
    assert payload["summary"]["group_key"] == "broker-us-core"
    assert payload["summary"]["group_label"] == "Core Brokerage"
    assert payload["summary"]["start_position_count"] == 1
    assert payload["summary"]["end_position_count"] == 1
    assert payload["start_positions"][0]["account_ids"] == ["broker-us-core"]


def test_period_boundary_holdings_can_filter_by_taxonomy_group(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="boundary-holdings-taxonomy-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "boundary-holdings-taxonomy-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "boundary-holdings-taxonomy-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "boundary-holdings-taxonomy-filter-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/boundary-holdings-taxonomy-filter-test/performance/boundary-holdings"
        "?axis=taxonomy&taxonomy_id=tax-sector&group_key=tax-sector-value"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "taxonomy"
    assert payload["summary"]["taxonomy_id"] == "tax-sector"
    assert payload["summary"]["group_key"] == "tax-sector-value"
    assert payload["summary"]["group_label"] == "Value"
    assert payload["summary"]["start_position_count"] == 1
    assert payload["summary"]["end_position_count"] == 1
    assert payload["start_positions"][0]["instrument_id"] == "equity-us-test"


def test_return_calendar_report_rolls_daily_returns_into_monthly_buckets(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        instrument_type="fund",
        history=[
            ("2026-01-31", "100.00"),
            ("2026-02-01", "110.00"),
            ("2026-03-01", "121.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="return-calendar-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "return-calendar-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-31",
                "settlement_date": "2026-01-31",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-31T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "return-calendar-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-31",
                "settlement_date": "2026-01-31",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "fund",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-31T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-03-01"
    _write_store(store)

    response = client.get("/api/portfolios/return-calendar-test/performance/calendar?frequency=monthly")
    assert response.status_code == 200
    payload = response.json()
    buckets = {item["bucket_key"]: item for item in payload["buckets"]}

    assert payload["summary"]["frequency"] == "monthly"
    assert payload["summary"]["bucket_count"] == 3
    assert buckets["2026-01"]["cumulative_twr"] is None
    assert buckets["2026-02"]["cumulative_twr"] == 0.10000000000000009
    assert buckets["2026-03"]["cumulative_twr"] == 0.10000000000000009
    assert buckets["2026-02"]["start_nav"] == 100.0
    assert buckets["2026-02"]["end_nav"] == 110.0
    assert buckets["2026-03"]["start_nav"] == 110.0
    assert buckets["2026-03"]["end_nav"] == 121.0


def test_instrument_contribution_report_tracks_daily_pnl_and_residual(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get("/api/portfolios/instrument-contribution-test/performance/contribution?axis=instrument")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert isclose(summary["portfolio_arithmetic_return"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    line = next(item for item in payload["lines"] if item["group_key"] == "equity-us-test")
    assert isclose(line["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["unrealized_pnl_change"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)

    slices = [item for item in payload["daily_slices"] if item["group_key"] == "equity-us-test"]
    slice_by_date = {item["as_of_date"]: item for item in slices}
    assert slice_by_date["2026-01-01"]["daily_contribution"] is None
    assert isclose(slice_by_date["2026-01-02"]["daily_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)


@pytest.mark.parametrize(
    ("portfolio_id", "scenario"),
    [
        ("line-twr-new-buy-test", "new_buy"),
        ("line-twr-add-buy-test", "add_buy"),
        ("line-twr-roundtrip-test", "roundtrip"),
    ],
)
def test_calculation_period_return_uses_group_twr_with_intraperiod_trades(
    client,
    monkeypatch,
    portfolio_id: str,
    scenario: str,
):
    instrument_id = "equity-us-line-twr"
    instrument_ref = {
        "instrument_id": instrument_id,
        "instrument_name": "Line TWR Equity",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "LTWR", "is_primary": True}],
    }
    instrument_detail = _test_instrument_detail(
        instrument_id=instrument_id,
        instrument_name="Line TWR Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda requested_instrument_id: deepcopy(instrument_detail) if requested_instrument_id == instrument_id else None,
    )

    def transaction(
        transaction_id: str,
        transaction_type: str,
        trade_date: str,
        *,
        portfolio_id: str,
        account_id: str,
        instrument_id_value: str | None,
        quantity: float | None,
        price: float | None,
        gross_amount: float,
        created_at: str,
    ) -> dict[str, object]:
        return {
            "transaction_id": transaction_id,
            "portfolio_id": portfolio_id,
            "transaction_type": transaction_type,
            "trade_date": trade_date,
            "settlement_date": trade_date,
            "account_id": account_id,
            "settlement_cash_account_id": (
                "cash-usd-main" if transaction_type in {"buy", "sell"} else None
            ),
            "instrument_id": instrument_id_value,
            "instrument_ref": deepcopy(instrument_ref) if instrument_id_value else None,
            "quantity": quantity,
            "price": price,
            "gross_amount": gross_amount,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "transfer_scope": None,
            "transfer_object_type": None,
            "transfer_group_id": None,
            "counterparty_account_id": None,
            "note": transaction_type,
            "created_at": created_at,
        }

    def calculation_group_period_return(portfolio_id: str, transactions: list[dict[str, object]]) -> float:
        store = _minimal_store(portfolio_id=portfolio_id, transactions=transactions)
        store["portfolios"][0]["as_of_date"] = "2026-01-02"
        _write_store(store)
        response = client.get(f"/api/portfolios/{portfolio_id}/performance/calculation/groups?axis=instrument")
        assert response.status_code == 200
        group = next(item for item in response.json()["groups"] if item["group_key"] == instrument_id)
        return group["period_return"]

    new_buy_transactions = [
        transaction(
            "txn-0001",
            "opening_balance",
            "2026-01-01",
            portfolio_id="line-twr-new-buy-test",
            account_id="cash-usd-main",
            instrument_id_value=None,
            quantity=None,
            price=None,
            gross_amount=100.0,
            created_at="2026-01-01T09:00:00Z",
        ),
        transaction(
            "txn-0002",
            "buy",
            "2026-01-02",
            portfolio_id="line-twr-new-buy-test",
            account_id="broker-us-core",
            instrument_id_value=instrument_id,
            quantity=1.0,
            price=100.0,
            gross_amount=100.0,
            created_at="2026-01-02T09:30:00Z",
        ),
    ]
    add_buy_transactions = [
        transaction(
            "txn-0001",
            "opening_balance",
            "2026-01-01",
            portfolio_id="line-twr-add-buy-test",
            account_id="cash-usd-main",
            instrument_id_value=None,
            quantity=None,
            price=None,
            gross_amount=200.0,
            created_at="2026-01-01T09:00:00Z",
        ),
        transaction(
            "txn-0002",
            "buy",
            "2026-01-01",
            portfolio_id="line-twr-add-buy-test",
            account_id="broker-us-core",
            instrument_id_value=instrument_id,
            quantity=1.0,
            price=100.0,
            gross_amount=100.0,
            created_at="2026-01-01T09:30:00Z",
        ),
        transaction(
            "txn-0003",
            "buy",
            "2026-01-02",
            portfolio_id="line-twr-add-buy-test",
            account_id="broker-us-core",
            instrument_id_value=instrument_id,
            quantity=1.0,
            price=100.0,
            gross_amount=100.0,
            created_at="2026-01-02T09:30:00Z",
        ),
    ]
    roundtrip_transactions = [
        transaction(
            "txn-0001",
            "opening_balance",
            "2026-01-01",
            portfolio_id="line-twr-roundtrip-test",
            account_id="cash-usd-main",
            instrument_id_value=None,
            quantity=None,
            price=None,
            gross_amount=100.0,
            created_at="2026-01-01T09:00:00Z",
        ),
        transaction(
            "txn-0002",
            "buy",
            "2026-01-02",
            portfolio_id="line-twr-roundtrip-test",
            account_id="broker-us-core",
            instrument_id_value=instrument_id,
            quantity=1.0,
            price=100.0,
            gross_amount=100.0,
            created_at="2026-01-02T09:30:00Z",
        ),
        transaction(
            "txn-0003",
            "sell",
            "2026-01-02",
            portfolio_id="line-twr-roundtrip-test",
            account_id="broker-us-core",
            instrument_id_value=instrument_id,
            quantity=1.0,
            price=110.0,
            gross_amount=110.0,
            created_at="2026-01-02T10:30:00Z",
        ),
    ]

    transactions_by_scenario = {
        "new_buy": new_buy_transactions,
        "add_buy": add_buy_transactions,
        "roundtrip": roundtrip_transactions,
    }
    assert isclose(
        calculation_group_period_return(
            portfolio_id,
            transactions_by_scenario[scenario],
        ),
        0.1,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


@pytest.mark.parametrize(
    ("cost_basis_method", "final_sale"),
    [
        pytest.param("fifo", False, id="fifo-open-position"),
        pytest.param("moving_average", False, id="moving-average-open-position"),
        pytest.param("fifo", True, id="fifo-sold-out-position"),
    ],
)
def test_cost_basis_method_changes_book_split_not_economic_contribution(
    client,
    monkeypatch,
    cost_basis_method: str,
    final_sale: bool,
):
    instrument_id = "equity-us-cost-method"
    instrument_ref = {
        "instrument_id": instrument_id,
        "instrument_name": "Instrument A",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "A", "is_primary": True}],
    }
    instrument_detail = _test_instrument_detail(
        instrument_id=instrument_id,
        instrument_name="Instrument A",
        history=[
            ("2026-01-01", "50.00"),
            ("2026-01-02", "100.00"),
            ("2026-01-03", "75.00"),
            ("2026-01-04", "80.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda requested_instrument_id: deepcopy(instrument_detail) if requested_instrument_id == instrument_id else None,
    )
    def transaction(
        transaction_id: str,
        transaction_type: str,
        trade_date: str,
        *,
        account_id: str,
        instrument_id_value: str | None,
        quantity: float | None,
        price: float | None,
        gross_amount: float,
        note: str,
    ) -> dict[str, object]:
        return {
            "transaction_id": transaction_id,
            "portfolio_id": "",
            "transaction_type": transaction_type,
            "trade_date": trade_date,
            "settlement_date": trade_date,
            "account_id": account_id,
            "settlement_cash_account_id": (
                "cash-usd-main" if transaction_type in {"buy", "sell"} else None
            ),
            "instrument_id": instrument_id_value,
            "instrument_ref": deepcopy(instrument_ref) if instrument_id_value else None,
            "quantity": quantity,
            "price": price,
            "gross_amount": gross_amount,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "transfer_scope": None,
            "transfer_object_type": None,
            "transfer_group_id": None,
            "counterparty_account_id": None,
            "note": note,
            "created_at": f"{trade_date}T09:00:00Z",
        }

    def build_store(portfolio_id: str, cost_basis_method: str, *, final_sale: bool = False) -> dict[str, object]:
        transactions = [
            transaction(
                "txn-0001",
                "opening_balance",
                "2026-01-01",
                account_id="cash-usd-main",
                instrument_id_value=None,
                quantity=None,
                price=None,
                gross_amount=750000.0,
                note="Opening cash.",
            ),
            transaction(
                "txn-0002",
                "buy",
                "2026-01-01",
                account_id="broker-us-core",
                instrument_id_value=instrument_id,
                quantity=5000.0,
                price=50.0,
                gross_amount=250000.0,
                note="Buy 5000 at 50.",
            ),
            transaction(
                "txn-0003",
                "buy",
                "2026-01-02",
                account_id="broker-us-core",
                instrument_id_value=instrument_id,
                quantity=5000.0,
                price=100.0,
                gross_amount=500000.0,
                note="Buy 5000 at 100.",
            ),
            transaction(
                "txn-0004",
                "sell",
                "2026-01-03",
                account_id="broker-us-core",
                instrument_id_value=instrument_id,
                quantity=6000.0,
                price=75.0,
                gross_amount=450000.0,
                note="Sell 6000 at 75.",
            ),
        ]
        if final_sale:
            transactions.append(
                transaction(
                    "txn-0005",
                    "sell",
                    "2026-01-04",
                    account_id="broker-us-core",
                    instrument_id_value=instrument_id,
                    quantity=4000.0,
                    price=80.0,
                    gross_amount=320000.0,
                    note="Sell remaining 4000 at 80.",
                )
            )
        for item in transactions:
            item["portfolio_id"] = portfolio_id
        store = _minimal_store(portfolio_id=portfolio_id, transactions=transactions)
        store["accounts"][1]["cost_basis_method"] = cost_basis_method
        return store

    def run_case(portfolio_id: str, cost_basis_method: str) -> dict[str, object]:
        _write_store(build_store(portfolio_id, cost_basis_method))

        holdings_response = client.get(f"/api/workspace/holdings?portfolio_id={portfolio_id}")
        assert holdings_response.status_code == 200
        holding = next(row for row in holdings_response.json()["rows"] if row["instrument_core"]["instrument_id"] == instrument_id)

        contribution_response = client.get(
            f"/api/portfolios/{portfolio_id}/performance/contribution?axis=instrument"
        )
        assert contribution_response.status_code == 200
        contribution_payload = contribution_response.json()
        line = next(item for item in contribution_payload["lines"] if item["group_key"] == instrument_id)
        calculation_groups_response = client.get(
            f"/api/portfolios/{portfolio_id}/performance/calculation/groups?axis=instrument"
        )
        assert calculation_groups_response.status_code == 200
        calculation_group = next(
            item
            for item in calculation_groups_response.json()["groups"]
            if item["group_key"] == instrument_id
        )
        performance_response = client.get(f"/api/portfolios/{portfolio_id}/performance")
        assert performance_response.status_code == 200
        return {
            "holding": holding,
            "line": line,
            "calculation_group": calculation_group,
            "summary": performance_response.json()["summary"],
        }

    if final_sale:
        portfolio_id = "cost-method-sold-out-test"
        _write_store(build_store(portfolio_id, cost_basis_method, final_sale=True))
        sold_out_response = client.get(
            f"/api/portfolios/{portfolio_id}/performance/calculation/groups?axis=instrument"
        )
        assert sold_out_response.status_code == 200
        sold_out_group = next(
            item
            for item in sold_out_response.json()["groups"]
            if item["group_key"] == instrument_id
        )
        assert sold_out_group["final_value"] == 0.0
        assert sold_out_group["capital_gains"] == 20000.0
        assert sold_out_group["realized_capital_gains"] == 20000.0
        assert sold_out_group["unrealized_pnl_change"] == 0.0

        sold_out_summary_response = client.get(
            f"/api/portfolios/{portfolio_id}/performance/calculation"
        )
        assert sold_out_summary_response.status_code == 200
        sold_out_summary = sold_out_summary_response.json()["summary"]
        assert sold_out_summary["capital_gains"] == 20000.0
        assert sold_out_summary["realized_capital_gains"] == 20000.0
        assert sold_out_summary["unrealized_capital_gains"] == 0.0
        return

    portfolio_id = f"cost-method-{cost_basis_method.replace('_', '-')}-test"
    result = run_case(portfolio_id, cost_basis_method)

    assert result["holding"]["quantity"] == 4000.0
    # The live holdings surface advances to the latest complete canonical
    # valuation date (2026-01-04), independently of the last trade date.
    assert result["holding"]["quote_as_of_date"] == "2026-01-04"
    assert result["holding"]["market_value"] == 320000.0

    expected_book_split = {
        "fifo": {
            "cost_basis": 400000.0,
            "realized_pnl": 100000.0,
            "unrealized_pnl_change": -80000.0,
        },
        "moving_average": {
            "cost_basis": 300000.0,
            "realized_pnl": 0.0,
            "unrealized_pnl_change": 20000.0,
        },
    }[cost_basis_method]
    assert result["holding"]["cost_basis"] == expected_book_split["cost_basis"]
    assert result["line"]["realized_pnl"] == expected_book_split["realized_pnl"]
    assert (
        result["line"]["unrealized_pnl_change"]
        == expected_book_split["unrealized_pnl_change"]
    )

    # Economic contribution and TWR are invariant to the account's book-cost
    # convention.  Both parametrized cases assert the same economic outputs.
    assert result["line"]["total_pnl"] == 20000.0
    assert result["calculation_group"]["capital_gains"] == 20000.0
    assert result["calculation_group"]["realized_capital_gains"] == 100000.0
    assert result["calculation_group"]["unrealized_pnl_change"] == -80000.0
    assert result["calculation_group"]["total_pnl"] == 20000.0
    assert result["calculation_group"]["beginning_weight"] is not None
    assert result["calculation_group"]["average_weight"] is not None
    assert result["calculation_group"]["ending_weight"] is not None
    assert result["line"]["period_contribution"] == pytest.approx(0.11)
    assert result["summary"]["cumulative_twr"] == pytest.approx(0.0266666666666666)


def test_account_contribution_report_tracks_interest_income(client, monkeypatch):
    monkeypatch.setattr(_TEST_INSTRUMENT_CATALOG, "get", lambda instrument_id: None)

    store = _minimal_store(
        portfolio_id="account-contribution-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "account-contribution-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "account-contribution-test",
                "transaction_type": "interest",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 5.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Interest income.",
                "created_at": "2026-01-02T10:00:00Z",
            },
        ],
    )
    _write_store(store)

    response = client.get("/api/portfolios/account-contribution-test/performance/contribution?axis=account")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "account"
    assert isclose(summary["portfolio_arithmetic_return"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    line = next(item for item in payload["lines"] if item["group_key"] == "cash-usd-main")
    assert isclose(line["income_cash_amount"], 5.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["total_pnl"], 5.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)

    slices = [item for item in payload["daily_slices"] if item["group_key"] == "cash-usd-main"]
    slice_by_date = {item["as_of_date"]: item for item in slices}
    assert isclose(slice_by_date["2026-01-02"]["daily_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["total_pnl"], 5.0, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_and_calculation_capture_attached_buy_charges(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-fee-test",
        instrument_name="Fee Test Equity",
        history=[
            ("2026-01-02", "93.00"),
            ("2026-01-03", "93.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-fee-test" else None,
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-fee-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-fee-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-fee-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-fee-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-fee-test",
                    "instrument_name": "Fee Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "FEE", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 93.0,
                "gross_amount": 93.0,
                "fees": 5.0,
                "taxes": 2.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy with attached charges.",
                "created_at": "2026-01-02T10:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    contribution_response = client.get(
        "/api/portfolios/instrument-contribution-fee-test/performance/contribution?axis=instrument"
    )
    assert contribution_response.status_code == 200
    contribution_payload = contribution_response.json()
    summary = contribution_payload["summary"]
    assert isclose(summary["portfolio_arithmetic_return"], -0.07, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], -0.07, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    line = next(item for item in contribution_payload["lines"] if item["group_key"] == "equity-us-fee-test")
    assert isclose(line["expense_cash_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["fee_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["tax_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["total_pnl"], -7.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], -0.07, rel_tol=0.0, abs_tol=1e-12)

    contribution_entries_response = client.get(
        "/api/portfolios/instrument-contribution-fee-test/performance/contribution/entries"
        "?axis=instrument&bucket=fee_amount"
    )
    assert contribution_entries_response.status_code == 200
    contribution_entries_payload = contribution_entries_response.json()
    assert contribution_entries_payload["summary"]["entry_count"] == 0
    assert isclose(contribution_entries_payload["summary"]["total_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert contribution_entries_payload["entries"] == []

    calculation_response = client.get("/api/portfolios/instrument-contribution-fee-test/performance/calculation")
    assert calculation_response.status_code == 200
    calculation_payload = calculation_response.json()
    calculation_summary = calculation_payload["summary"]
    assert isclose(calculation_summary["capital_gains"], -7.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["fees"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["taxes"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert "residual_gains" not in calculation_summary


def test_instrument_contribution_report_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            instrument_id="equity-us-alpha",
            instrument_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "110.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            instrument_id="equity-us-beta",
            instrument_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "90.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_details.get(instrument_id)),
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-filter-test/performance/contribution"
        "?axis=instrument&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["group_count"] == 1
    assert summary["slice_count"] == 2
    assert summary["observation_count"] == 1
    assert isclose(summary["portfolio_arithmetic_return"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], -0.05, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["lines"]) == 1
    line = payload["lines"][0]
    assert line["group_key"] == "equity-us-alpha"
    assert isclose(line["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_bucket_drilldown_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            instrument_id="equity-us-alpha",
            instrument_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "110.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            instrument_id="equity-us-beta",
            instrument_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "90.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_details.get(instrument_id)),
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-drilldown-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-drilldown-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-drilldown-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-drilldown-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-drilldown-filter-test/performance/contribution/drilldown"
        "?axis=instrument&bucket=contribution&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["bucket"] == "contribution"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["group_count"] == 1
    assert isclose(summary["total_amount"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["group_key"] == "equity-us-alpha"
    assert isclose(group["amount"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_entries_can_filter_income_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            instrument_id="equity-us-alpha",
            instrument_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "100.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            instrument_id="equity-us-beta",
            instrument_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "100.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_details.get(instrument_id)),
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-entries-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-entries-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-entries-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-entries-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "instrument-contribution-entries-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Alpha dividend.",
                "created_at": "2026-01-02T12:00:00Z",
            },
            {
                "transaction_id": "txn-0005",
                "portfolio_id": "instrument-contribution-entries-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 30.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Beta dividend.",
                "created_at": "2026-01-02T12:01:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-entries-filter-test/performance/contribution/entries"
        "?axis=instrument&bucket=income_cash_amount&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["bucket"] == "income_cash_amount"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["entry_count"] == 1
    assert isclose(summary["total_amount"], 20.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["group_key"] == "equity-us-alpha"
    assert entry["component_kind"] == "gross_amount"
    assert isclose(entry["base_amount"], 20.0, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_report_surfaces_instrument_currency_gains(client, monkeypatch):
    fx_detail = {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    fund_detail = {
        "instrument_id": "fund-hk-contribution-test",
        "instrument_name": "HK Contribution Fund",
        "instrument_type": "fund",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "HKCONFUND", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-01",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-02",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    details = {
        "fx-usd-hkd": fx_detail,
        "fund-hk-contribution-test": fund_detail,
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(details.get(instrument_id)),
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "instrument-fx-contribution-test",
                "portfolio_name": "instrument-fx-contribution-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 1,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "broker-hk-core",
                "portfolio_id": "instrument-fx-contribution-test",
                "account_name": "HK Brokerage",
                "account_type": "securities_account",
                "currency": "HKD",
                "institution": "Test Broker",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": "fifo",
                "allowed_instrument_types": ["fund"],
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-fx-contribution-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": None,
                "instrument_id": "fund-hk-contribution-test",
                "instrument_ref": {
                    "instrument_id": "fund-hk-contribution-test",
                    "instrument_name": "HK Contribution Fund",
                    "instrument_type": "fund",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 78.0,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Open HK fund.",
                "created_at": "2026-01-01T09:00:00Z",
            },
        ],
    }
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-fx-contribution-test/performance/contribution?axis=instrument"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert isclose(summary["portfolio_arithmetic_return"], 0.04, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    line = next(item for item in payload["lines"] if item["group_key"] == "fund-hk-contribution-test")
    assert isclose(line["instrument_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["unrealized_pnl_change"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)

    slices = [item for item in payload["daily_slices"] if item["group_key"] == "fund-hk-contribution-test"]
    slice_by_date = {item["as_of_date"]: item for item in slices}
    assert isclose(slice_by_date["2026-01-02"]["instrument_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["unrealized_pnl_change"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["daily_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_bucket_drilldown_surfaces_instrument_currency_gains(client, monkeypatch):
    fx_detail = {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    fund_detail = {
        "instrument_id": "fund-hk-contribution-drilldown-test",
        "instrument_name": "HK Drilldown Fund",
        "instrument_type": "fund",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "HKDRILL", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-01",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-02",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    details = {
        "fx-usd-hkd": fx_detail,
        "fund-hk-contribution-drilldown-test": fund_detail,
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(details.get(instrument_id)),
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "instrument-fx-contribution-drilldown-test",
                "portfolio_name": "instrument-fx-contribution-drilldown-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 1,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "broker-hk-core",
                "portfolio_id": "instrument-fx-contribution-drilldown-test",
                "account_name": "HK Brokerage",
                "account_type": "securities_account",
                "currency": "HKD",
                "institution": "Test Broker",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": "fifo",
                "allowed_instrument_types": ["fund"],
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-fx-contribution-drilldown-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": None,
                "instrument_id": "fund-hk-contribution-drilldown-test",
                "instrument_ref": {
                    "instrument_id": "fund-hk-contribution-drilldown-test",
                    "instrument_name": "HK Drilldown Fund",
                    "instrument_type": "fund",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 78.0,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Open HK fund.",
                "created_at": "2026-01-01T09:00:00Z",
            },
        ],
    }
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-fx-contribution-drilldown-test/performance/contribution/drilldown"
        "?axis=instrument&bucket=instrument_currency_gains"
    )
    assert response.status_code == 200
    payload = response.json()
    assert isclose(payload["summary"]["total_amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["group_key"] == "fund-hk-contribution-drilldown-test"
    assert isclose(group["amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_entries_support_realized_pnl_realizations(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-realized-entry",
        instrument_name="Realized Entry Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-realized-entry" else None,
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-realized-entry-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-realized-entry-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-realized-entry-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-realized-entry",
                "instrument_ref": {
                    "instrument_id": "equity-us-realized-entry",
                    "instrument_name": "Realized Entry Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "REAL", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy realized equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-realized-entry-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-realized-entry",
                "instrument_ref": {
                    "instrument_id": "equity-us-realized-entry",
                    "instrument_name": "Realized Entry Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "REAL", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell realized equity.",
                "created_at": "2026-01-02T10:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-realized-entry-test/performance/contribution/entries"
        "?axis=instrument&bucket=realized_pnl"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["entry_count"] == 1
    assert isclose(summary["total_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    entry = payload["entries"][0]
    assert entry["entry_kind"] == "realization"
    assert entry["transaction_type"] == "sell"
    assert entry["group_key"] == "equity-us-realized-entry"
    assert isclose(entry["base_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)


def test_account_contribution_report_tracks_cash_currency_gains(client, monkeypatch):
    fx_detail = {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(fx_detail) if instrument_id == "fx-usd-hkd" else None,
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "account-cash-fx-contribution-test",
                "portfolio_name": "account-cash-fx-contribution-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 0,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "account-cash-fx-contribution-test",
                "account_name": "Main HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_instrument_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "account-cash-fx-contribution-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-hkd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Open HKD cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
        ],
    }
    _write_store(store)

    response = client.get("/api/portfolios/account-cash-fx-contribution-test/performance/contribution?axis=account")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert isclose(summary["portfolio_arithmetic_return"], 0.04, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    line = next(item for item in payload["lines"] if item["group_key"] == "cash-hkd-main")
    assert isclose(line["cash_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)

    slices = [item for item in payload["daily_slices"] if item["group_key"] == "cash-hkd-main"]
    slice_by_date = {item["as_of_date"]: item for item in slices}
    assert isclose(slice_by_date["2026-01-02"]["cash_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(slice_by_date["2026-01-02"]["daily_contribution"], 0.04, rel_tol=0.0, abs_tol=1e-12)


def test_taxonomy_catalog_route_returns_taxonomy_tree_and_assignments(client):
    store = _minimal_store(
        portfolio_id="taxonomy-catalog-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-catalog-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            }
        ],
    )
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-catalog-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    _write_store(store)

    response = client.get("/api/portfolios/taxonomy-catalog-test/taxonomies")
    assert response.status_code == 200
    payload = response.json()
    assert payload["portfolio_id"] == "taxonomy-catalog-test"
    assert len(payload["taxonomies"]) == 1
    assert payload["taxonomies"][0]["taxonomy_id"] == "tax-sector"
    assert len(payload["taxonomy_nodes"]) == 1
    assert payload["taxonomy_nodes"][0]["node_name"] == "Value"
    assert len(payload["taxonomy_assignments"]) == 1
    assert payload["taxonomy_assignments"][0]["target_entity_id"] == "equity-us-test"


def test_taxonomy_contribution_report_uses_current_assignment_for_full_period(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="taxonomy-contribution-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-contribution-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-contribution-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-contribution-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        },
        {
            "taxonomy_node_id": "tax-sector-growth",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Growth",
            "node_code": "GROWTH",
            "sort_order": 1,
            "is_terminal": True,
            "status": "active",
        },
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-02",
            "status": "active",
        },
        {
            "assignment_id": "assign-0002",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-growth",
            "effective_from": "2026-01-03",
            "effective_to": None,
            "status": "active",
        },
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/taxonomy-contribution-test/performance/contribution"
        "?axis=taxonomy&taxonomy_id=tax-sector"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "taxonomy"
    assert summary["taxonomy_id"] == "tax-sector"
    assert isclose(summary["portfolio_arithmetic_return"], 0.2, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.2, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    lines = {item["group_key"]: item for item in payload["lines"]}
    assert "tax-sector-value" not in lines
    assert isclose(lines["tax-sector-growth"]["total_pnl"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(lines["tax-sector-growth"]["period_contribution"], 0.2, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(lines["tax-sector-growth"]["end_value_base"], 121.0, rel_tol=0.0, abs_tol=1e-12)

    slices = {(item["as_of_date"], item["group_key"]): item for item in payload["daily_slices"]}
    assert isclose(slices[("2026-01-02", "tax-sector-growth")]["daily_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(
        slices[("2026-01-03", "tax-sector-growth")]["daily_contribution"],
        0.1,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def test_taxonomy_group_return_uses_capital_flow_denominator_for_in_period_buys(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-flow-test",
        instrument_name="Flow Test Equity",
        history=[
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-flow-test" else None,
    )

    store = _minimal_store(
        portfolio_id="taxonomy-flow-return-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-flow-return-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-flow-return-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-flow-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-flow-test",
                    "instrument_name": "Flow Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "FLOW", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy flow test equity.",
                "created_at": "2026-01-02T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-flow-return-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-core",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Core",
            "node_code": "CORE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-flow-test",
            "taxonomy_node_id": "tax-sector-core",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    _write_store(store)

    contribution_response = client.get(
        "/api/portfolios/taxonomy-flow-return-test/performance/contribution"
        "?axis=taxonomy&taxonomy_id=tax-sector&start_date=2026-01-02&end_date=2026-01-02"
    )
    assert contribution_response.status_code == 200
    contribution_payload = contribution_response.json()
    contribution_slices = {
        (item["as_of_date"], item["group_key"]): item for item in contribution_payload["daily_slices"]
    }
    core_slice = contribution_slices[("2026-01-02", "tax-sector-core")]
    assert isclose(core_slice["daily_return"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert core_slice["return_observation_eligible"] is True

    groups_response = client.get(
        "/api/portfolios/taxonomy-flow-return-test/performance/calculation/groups"
        "?axis=taxonomy&taxonomy_id=tax-sector&start_date=2026-01-02&end_date=2026-01-02"
    )
    assert groups_response.status_code == 200
    groups_payload = groups_response.json()
    groups = {item["group_key"]: item for item in groups_payload["groups"]}
    assert isclose(groups["tax-sector-core"]["period_return"], 0.1, rel_tol=0.0, abs_tol=1e-12)


def test_taxonomy_contribution_and_entries_support_cash_bucket_scope(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-cash-bucket",
        instrument_name="Cash Bucket Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-cash-bucket" else None,
    )

    store = _minimal_store(
        portfolio_id="taxonomy-cash-bucket-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-cash-bucket-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-cash-bucket-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": None,
                "instrument_id": "equity-us-cash-bucket",
                "instrument_ref": {
                    "instrument_id": "equity-us-cash-bucket",
                    "instrument_name": "Cash Bucket Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "CASH", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Open equity position.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "taxonomy-cash-bucket-test",
                "transaction_type": "interest",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 5.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Interest income.",
                "created_at": "2026-01-02T10:00:00Z",
            },
        ],
    )
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-liquidity",
            "portfolio_id": "taxonomy-cash-bucket-test",
            "name": "Liquidity",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "cash_bucket",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-liquidity-core",
            "taxonomy_id": "tax-liquidity",
            "parent_taxonomy_node_id": None,
            "node_name": "Core Cash",
            "node_code": "CORE_CASH",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-liquidity",
            "target_scope": "cash_bucket",
            "target_entity_id": "cash-usd-main",
            "taxonomy_node_id": "tax-liquidity-core",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    contribution_response = client.get(
        "/api/portfolios/taxonomy-cash-bucket-test/performance/contribution"
        "?axis=taxonomy&taxonomy_id=tax-liquidity"
    )
    assert contribution_response.status_code == 200
    contribution_payload = contribution_response.json()
    summary = contribution_payload["summary"]
    assert summary["axis"] == "taxonomy"
    assert summary["taxonomy_id"] == "tax-liquidity"
    assert isclose(summary["portfolio_arithmetic_return"], 0.075, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_period_contribution"], 0.025, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert len(contribution_payload["lines"]) == 1
    line = contribution_payload["lines"][0]
    assert line["group_key"] == "tax-liquidity-core"
    assert isclose(line["income_cash_amount"], 5.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["total_pnl"], 5.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(line["period_contribution"], 0.025, rel_tol=0.0, abs_tol=1e-12)

    entries_response = client.get(
        "/api/portfolios/taxonomy-cash-bucket-test/performance/contribution/entries"
        "?axis=taxonomy&taxonomy_id=tax-liquidity&bucket=income_cash_amount"
    )
    assert entries_response.status_code == 200
    entries_payload = entries_response.json()
    assert entries_payload["summary"]["entry_count"] == 1
    assert isclose(entries_payload["summary"]["total_amount"], 5.0, rel_tol=0.0, abs_tol=1e-12)
    entry = entries_payload["entries"][0]
    assert entry["group_key"] == "tax-liquidity-core"
    assert entry["component_kind"] == "gross_amount"
    assert entry["transaction_id"] == "txn-0003"
    assert isclose(entry["base_amount"], 5.0, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_calendar_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            instrument_id="equity-us-alpha",
            instrument_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "110.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            instrument_id="equity-us-beta",
            instrument_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "90.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_details.get(instrument_id)),
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-calendar-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-calendar-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-calendar-filter-test/performance/contribution/calendar"
        "?axis=instrument&frequency=monthly&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["bucket_count"] == 1
    assert summary["group_count"] == 1
    assert summary["observation_count"] == 1
    assert isclose(summary["total_bucket_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["buckets"]) == 1
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "equity-us-alpha"
    assert bucket["group_label"] == "Alpha Equity"
    assert isclose(bucket["bucket_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_calendar_bucket_drilldown_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            instrument_id="equity-us-alpha",
            instrument_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "110.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            instrument_id="equity-us-beta",
            instrument_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "90.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_details.get(instrument_id)),
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-calendar-drilldown-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-calendar-drilldown-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-calendar-drilldown-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-calendar-drilldown-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-calendar-drilldown-filter-test/performance/contribution/calendar/drilldown"
        "?axis=instrument&frequency=monthly&bucket=contribution&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["bucket"] == "contribution"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["bucket_count"] == 1
    assert summary["group_count"] == 1
    assert summary["observation_count"] == 1
    assert isclose(summary["total_amount"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["buckets"]) == 1
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "equity-us-alpha"
    assert isclose(bucket["amount"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_entries_calendar_can_filter_income_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            instrument_id="equity-us-alpha",
            instrument_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-15", "100.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            instrument_id="equity-us-beta",
            instrument_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-15", "100.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_details.get(instrument_id)),
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-entries-calendar-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-entries-calendar-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-entries-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "instrument-contribution-entries-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "instrument-contribution-entries-calendar-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-15",
                "settlement_date": "2026-01-15",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Alpha dividend.",
                "created_at": "2026-01-15T12:00:00Z",
            },
            {
                "transaction_id": "txn-0005",
                "portfolio_id": "instrument-contribution-entries-calendar-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-15",
                "settlement_date": "2026-01-15",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 30.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Beta dividend.",
                "created_at": "2026-01-15T12:01:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-15"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-entries-calendar-filter-test/performance/contribution/entries/calendar"
        "?axis=instrument&bucket=income_cash_amount&frequency=monthly&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["bucket"] == "income_cash_amount"
    assert summary["group_key"] == "equity-us-alpha"
    assert summary["group_label"] == "Alpha Equity"
    assert summary["bucket_count"] == 1
    assert summary["group_count"] == 1
    assert summary["entry_count"] == 1
    assert isclose(summary["total_amount"], 20.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["buckets"]) == 1
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "equity-us-alpha"
    assert bucket["contribution_bucket"] == "income_cash_amount"
    assert isclose(bucket["total_amount"], 20.0, rel_tol=0.0, abs_tol=1e-12)


def test_instrument_contribution_calendar_rolls_daily_slices_into_monthly_bucket(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="instrument-contribution-calendar-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-contribution-calendar-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "instrument-contribution-calendar-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/instrument-contribution-calendar-test/performance/contribution/calendar"
        "?axis=instrument&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "instrument"
    assert summary["frequency"] == "monthly"
    assert summary["bucket_count"] == 1
    assert isclose(summary["total_bucket_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    bucket = payload["buckets"][0]
    assert bucket["bucket_key"] == "2026-01"
    assert bucket["group_key"] == "equity-us-test"
    assert isclose(bucket["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["bucket_contribution"], 0.1, rel_tol=0.0, abs_tol=1e-12)


def test_taxonomy_contribution_calendar_uses_current_assignment_for_full_bucket(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="taxonomy-contribution-calendar-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-contribution-calendar-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-contribution-calendar-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-contribution-calendar-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        },
        {
            "taxonomy_node_id": "tax-sector-growth",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Growth",
            "node_code": "GROWTH",
            "sort_order": 1,
            "is_terminal": True,
            "status": "active",
        },
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-02",
            "status": "active",
        },
        {
            "assignment_id": "assign-0002",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-growth",
            "effective_from": "2026-01-03",
            "effective_to": None,
            "status": "active",
        },
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/taxonomy-contribution-calendar-test/performance/contribution/calendar"
        "?axis=taxonomy&taxonomy_id=tax-sector&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "taxonomy"
    assert summary["taxonomy_id"] == "tax-sector"
    assert summary["frequency"] == "monthly"
    assert isclose(summary["total_bucket_contribution"], 0.2, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    buckets = {item["group_key"]: item for item in payload["buckets"]}
    assert "tax-sector-value" not in buckets
    assert isclose(buckets["tax-sector-growth"]["bucket_contribution"], 0.2, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(buckets["tax-sector-growth"]["ending_value_base"], 121.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_groups_calendar_rolls_monthly_instrument_bridge(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="calculation-groups-calendar-instrument-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-groups-calendar-instrument-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-groups-calendar-instrument-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-groups-calendar-instrument-test/performance/calculation/groups/calendar"
        "?axis=instrument&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "instrument"
    assert payload["summary"]["frequency"] == "monthly"
    assert payload["summary"]["bucket_count"] == 1
    assert isclose(payload["summary"]["total_delta"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_residual_delta"], 100.0, rel_tol=0.0, abs_tol=1e-12)

    bucket = payload["buckets"][0]
    assert bucket["bucket_key"] == "2026-01"
    assert bucket["group_key"] == "equity-us-test"
    assert isclose(bucket["initial_value"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["final_value"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["delta"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["residual_delta"], 100.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_groups_support_instrument_type_axis(client, monkeypatch):
    equity_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
        instrument_type="equity",
    )
    fund_detail = _test_instrument_detail(
        instrument_id="fund-us-test",
        instrument_name="Test Fund",
        history=[
            ("2026-01-01", "200.00"),
            ("2026-01-02", "190.00"),
        ],
        instrument_type="fund",
    )
    instrument_details = {
        "equity-us-test": equity_detail,
        "fund-us-test": fund_detail,
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_details.get(instrument_id)),
    )

    portfolio_id = "calculation-groups-instrument-type-test"
    transactions = [
        {
            "transaction_id": "txn-0001",
            "portfolio_id": portfolio_id,
            "transaction_type": "opening_balance",
            "trade_date": "2026-01-01",
            "settlement_date": "2026-01-01",
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": None,
            "instrument_id": None,
            "instrument_ref": None,
            "quantity": None,
            "price": None,
            "gross_amount": 300.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "transfer_scope": None,
            "transfer_object_type": None,
            "transfer_group_id": None,
            "counterparty_account_id": None,
            "note": "Opening cash.",
            "created_at": "2026-01-01T09:00:00Z",
        },
    ]
    for index, (instrument_id, instrument_name, instrument_type, amount) in enumerate(
        [
            ("equity-us-test", "Test Equity", "equity", 100.0),
            ("fund-us-test", "Test Fund", "fund", 200.0),
        ],
        start=2,
    ):
        transactions.append(
            {
                "transaction_id": f"txn-{index:04d}",
                "portfolio_id": portfolio_id,
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": instrument_id,
                "instrument_ref": {
                    "instrument_id": instrument_id,
                    "instrument_name": instrument_name,
                    "instrument_type": instrument_type,
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": instrument_id.upper(), "is_primary": True}],
                },
                "quantity": 1.0,
                "price": amount,
                "gross_amount": amount,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": f"Buy {instrument_name}.",
                "created_at": f"2026-01-01T09:{index}0:00Z",
            }
        )

    store = _minimal_store(portfolio_id=portfolio_id, transactions=transactions)
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(f"/api/portfolios/{portfolio_id}/performance/calculation/groups?axis=instrument_type")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "instrument_type"
    groups = {item["group_key"]: item for item in payload["groups"]}

    assert groups["equity"]["group_label"] == "Equity"
    assert isclose(groups["equity"]["final_value"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["equity"]["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["equity"]["unrealized_pnl_change"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    equity_children = {item["item_key"]: item for item in groups["equity"]["children"]}
    assert equity_children["equity-us-test"]["item_label"] == "Test Equity"
    assert isclose(equity_children["equity-us-test"]["final_value"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert groups["fund"]["group_label"] == "Fund"
    assert isclose(groups["fund"]["final_value"], 190.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["fund"]["total_pnl"], -10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["fund"]["unrealized_pnl_change"], -10.0, rel_tol=0.0, abs_tol=1e-12)
    fund_children = {item["item_key"]: item for item in groups["fund"]["children"]}
    assert fund_children["fund-us-test"]["item_label"] == "Test Fund"
    assert isclose(fund_children["fund-us-test"]["final_value"], 190.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_groups_use_unified_weekly_risk_basis_for_mixed_frequency(client, monkeypatch):
    daily_detail = _test_instrument_detail(
        instrument_id="equity-us-daily-risk-test",
        instrument_name="Daily Risk Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "101.00"),
            ("2026-01-05", "103.00"),
            ("2026-01-06", "102.00"),
            ("2026-01-07", "104.00"),
            ("2026-01-08", "105.00"),
            ("2026-01-09", "106.00"),
            ("2026-01-12", "107.00"),
            ("2026-01-13", "106.00"),
            ("2026-01-14", "108.00"),
            ("2026-01-15", "109.00"),
            ("2026-01-16", "111.00"),
        ],
    )
    weekly_detail = _test_instrument_detail(
        instrument_id="fund-us-weekly-risk-test",
        instrument_name="Weekly Risk Test Fund",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-08", "104.00"),
            ("2026-01-16", "102.00"),
        ],
        instrument_type="fund",
    )
    for detail in (daily_detail, weekly_detail):
        detail["quote_selection_policy"]["total_return"] = ["adjusted_close"]
        detail["market_data"].extend(
            {
                **deepcopy(point),
                "quote_basis": "adjusted_close",
            }
            for point in list(detail["market_data"])
        )
    instrument_details = {
        "equity-us-daily-risk-test": daily_detail,
        "fund-us-weekly-risk-test": weekly_detail,
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_details.get(instrument_id)),
    )

    portfolio_id = "calculation-groups-mixed-risk-basis-test"
    transactions = [
        {
            "transaction_id": "txn-0001",
            "portfolio_id": portfolio_id,
            "transaction_type": "opening_balance",
            "trade_date": "2026-01-01",
            "settlement_date": "2026-01-01",
            "account_id": "cash-usd-main",
            "settlement_cash_account_id": None,
            "instrument_id": None,
            "instrument_ref": None,
            "quantity": None,
            "price": None,
            "gross_amount": 200.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "USD",
            "transfer_scope": None,
            "transfer_object_type": None,
            "transfer_group_id": None,
            "counterparty_account_id": None,
            "note": "Opening cash.",
            "created_at": "2026-01-01T09:00:00Z",
        },
    ]
    for index, (instrument_id, instrument_name, instrument_type) in enumerate(
        [
            ("equity-us-daily-risk-test", "Daily Risk Test Equity", "equity"),
            ("fund-us-weekly-risk-test", "Weekly Risk Test Fund", "fund"),
        ],
        start=2,
    ):
        transactions.append(
            {
                "transaction_id": f"txn-{index:04d}",
                "portfolio_id": portfolio_id,
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": instrument_id,
                "instrument_ref": {
                    "instrument_id": instrument_id,
                    "instrument_name": instrument_name,
                    "instrument_type": instrument_type,
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "ticker",
                            "identifier_value": instrument_id.upper(),
                            "is_primary": True,
                        }
                    ],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": f"Buy {instrument_name}.",
                "created_at": f"2026-01-01T09:{index}0:00Z",
            }
        )

    store = _minimal_store(portfolio_id=portfolio_id, transactions=transactions)
    store["portfolios"][0]["as_of_date"] = "2026-01-16"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-risk-basis",
            "portfolio_id": portfolio_id,
            "name": "Risk Basis",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-risk-basis-core",
            "taxonomy_id": "tax-risk-basis",
            "parent_taxonomy_node_id": None,
            "node_name": "Core",
            "node_code": "CORE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": f"assign-risk-basis-{instrument_id}",
            "taxonomy_id": "tax-risk-basis",
            "target_scope": "instrument",
            "target_entity_id": instrument_id,
            "taxonomy_node_id": "tax-risk-basis-core",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
        for instrument_id in ("equity-us-daily-risk-test", "fund-us-weekly-risk-test")
    ]
    _write_store(store)

    response = client.get(
        f"/api/portfolios/{portfolio_id}/performance/calculation/groups"
        "?axis=instrument&start_date=2026-01-01&end_date=2026-01-16"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["risk_calculation_frequency"] == "weekly"
    assert payload["summary"]["risk_frequency_status_label"] == "Weekly risk basis - mixed daily/weekly data"
    assert payload["summary"]["risk_return_observation_count"] == 3
    groups = {item["group_key"]: item for item in payload["groups"]}

    assert groups["equity-us-daily-risk-test"]["risk_calculation_frequency"] == "weekly"
    assert groups["fund-us-weekly-risk-test"]["risk_calculation_frequency"] == "weekly"
    assert groups["equity-us-daily-risk-test"]["risk_return_observation_count"] == 3
    assert groups["fund-us-weekly-risk-test"]["risk_return_observation_count"] == 2
    assert groups["equity-us-daily-risk-test"]["annualized_volatility"] is not None
    assert groups["fund-us-weekly-risk-test"]["annualized_volatility"] is not None

    taxonomy_response = client.get(
        f"/api/portfolios/{portfolio_id}/performance/calculation/groups"
        "?axis=taxonomy&taxonomy_id=tax-risk-basis&start_date=2026-01-01&end_date=2026-01-16"
    )
    assert taxonomy_response.status_code == 200
    taxonomy_payload = taxonomy_response.json()
    taxonomy_groups = {item["group_key"]: item for item in taxonomy_payload["groups"]}
    core_group = taxonomy_groups["tax-risk-basis-core"]
    child_total_pnl = sum(item["total_pnl"] for item in core_group["children"])
    child_volatility = sum(
        item["annualized_volatility"]
        for item in core_group["children"]
        if item["annualized_volatility"] is not None
    )

    assert core_group["risk_calculation_frequency"] == "weekly"
    assert core_group["risk_return_observation_count"] == 3
    assert core_group["annualized_volatility"] is not None
    assert not isclose(core_group["annualized_volatility"], child_volatility, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(core_group["total_pnl"], child_total_pnl, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_groups_instrument_includes_cash_balance(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-cash-line-test",
        instrument_name="Cash Line Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-cash-line-test" else None,
    )

    portfolio_id = "calculation-instrument-cash-line-test"
    store = _minimal_store(
        portfolio_id=portfolio_id,
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": portfolio_id,
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 150.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": portfolio_id,
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-cash-line-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-cash-line-test",
                    "instrument_name": "Cash Line Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [
                        {"identifier_type": "ticker", "identifier_value": "CASHLINE", "is_primary": True}
                    ],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy with remaining cash.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(f"/api/portfolios/{portfolio_id}/performance/calculation/groups?axis=instrument")
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    groups = {item["group_key"]: item for item in payload["groups"]}

    assert isclose(summary["total_initial_value"], 150.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_final_value"], 160.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_delta"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert groups["cash"]["group_label"] == "Cash"
    assert isclose(groups["cash"]["initial_value"], 50.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["cash"]["final_value"], 50.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["cash"]["beginning_weight"], 50.0 / 150.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["cash"]["total_pnl"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["equity-us-cash-line-test"]["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["equity-us-cash-line-test"]["final_value"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(
        groups["equity-us-cash-line-test"]["beginning_weight"],
        100.0 / 150.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )

    explicit_calculation_response = client.get(
        f"/api/portfolios/{portfolio_id}/performance/calculation"
        "?start_date=2026-01-02&end_date=2026-01-02"
    )
    assert explicit_calculation_response.status_code == 200
    explicit_calculation_summary = explicit_calculation_response.json()["summary"]
    assert isclose(explicit_calculation_summary["initial_value"], 150.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(explicit_calculation_summary["final_value"], 160.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(explicit_calculation_summary["delta"], 10.0, rel_tol=0.0, abs_tol=1e-12)

    explicit_groups_response = client.get(
        f"/api/portfolios/{portfolio_id}/performance/calculation/groups"
        "?axis=instrument&start_date=2026-01-02&end_date=2026-01-02"
    )
    assert explicit_groups_response.status_code == 200
    explicit_groups_payload = explicit_groups_response.json()
    explicit_groups = {item["group_key"]: item for item in explicit_groups_payload["groups"]}
    assert isclose(
        explicit_groups_payload["summary"]["total_initial_value"],
        150.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert isclose(
        explicit_groups_payload["summary"]["total_final_value"],
        160.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert isclose(explicit_groups["cash"]["initial_value"], 50.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(explicit_groups["cash"]["final_value"], 50.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(explicit_groups["cash"]["beginning_weight"], 50.0 / 150.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(
        explicit_groups["equity-us-cash-line-test"]["initial_value"],
        100.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert isclose(
        explicit_groups["equity-us-cash-line-test"]["final_value"],
        110.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert isclose(
        explicit_groups["equity-us-cash-line-test"]["beginning_weight"],
        100.0 / 150.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )

    explicit_calendar_response = client.get(
        f"/api/portfolios/{portfolio_id}/performance/calculation/groups/calendar"
        "?axis=instrument&frequency=weekly&start_date=2026-01-02&end_date=2026-01-02"
    )
    assert explicit_calendar_response.status_code == 200
    explicit_calendar_buckets = {
        item["group_key"]: item for item in explicit_calendar_response.json()["buckets"]
    }
    assert isclose(explicit_calendar_buckets["cash"]["initial_value"], 50.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(explicit_calendar_buckets["cash"]["final_value"], 50.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(
        explicit_calendar_buckets["cash"]["beginning_weight"],
        50.0 / 150.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert isclose(
        explicit_calendar_buckets["equity-us-cash-line-test"]["initial_value"],
        100.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert isclose(
        explicit_calendar_buckets["equity-us-cash-line-test"]["final_value"],
        110.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert isclose(
        explicit_calendar_buckets["equity-us-cash-line-test"]["beginning_weight"],
        100.0 / 150.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def test_period_calculation_groups_calendar_supports_monthly_taxonomy_bridge(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="calculation-groups-calendar-taxonomy-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-groups-calendar-taxonomy-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-groups-calendar-taxonomy-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "calculation-groups-calendar-taxonomy-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-groups-calendar-taxonomy-test/performance/calculation/groups/calendar"
        "?axis=taxonomy&taxonomy_id=tax-sector&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "taxonomy"
    assert payload["summary"]["taxonomy_id"] == "tax-sector"
    assert payload["summary"]["frequency"] == "monthly"
    assert isclose(payload["summary"]["total_delta"], 121.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_pnl"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_residual_delta"], 100.0, rel_tol=0.0, abs_tol=1e-12)

    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "tax-sector-value"
    assert isclose(bucket["initial_value"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["final_value"], 121.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["delta"], 121.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["total_pnl"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(bucket["residual_delta"], 100.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_groups_calendar_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            instrument_id="equity-us-alpha",
            instrument_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "110.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            instrument_id="equity-us-beta",
            instrument_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-02", "90.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_details.get(instrument_id)),
    )

    store = _minimal_store(
        portfolio_id="calculation-groups-calendar-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-groups-calendar-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-groups-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-groups-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-groups-calendar-filter-test/performance/calculation/groups/calendar"
        "?axis=instrument&frequency=monthly&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["group_key"] == "equity-us-alpha"
    assert payload["summary"]["group_label"] == "Alpha Equity"
    assert payload["summary"]["bucket_count"] == 1
    assert payload["summary"]["group_count"] == 1
    assert payload["summary"]["observation_count"] == 1
    assert isclose(payload["summary"]["total_delta"], 110.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_pnl"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_residual_delta"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["total_bucket_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["summary"]["contribution_residual"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["buckets"]) == 1
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "equity-us-alpha"
    assert isclose(bucket["bucket_contribution"], 0.05, rel_tol=0.0, abs_tol=1e-12)


def test_taxonomy_boundary_groups_report_uses_current_assignment_on_start_and_end_dates(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="taxonomy-boundary-groups-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-boundary-groups-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-boundary-groups-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-boundary-groups-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        },
        {
            "taxonomy_node_id": "tax-sector-growth",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Growth",
            "node_code": "GROWTH",
            "sort_order": 1,
            "is_terminal": True,
            "status": "active",
        },
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-02",
            "status": "active",
        },
        {
            "assignment_id": "assign-0002",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-growth",
            "effective_from": "2026-01-03",
            "effective_to": None,
            "status": "active",
        },
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/taxonomy-boundary-groups-test/performance/boundary-groups?taxonomy_id=tax-sector"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["taxonomy_id"] == "tax-sector"
    assert payload["summary"]["start_group_count"] == 1
    assert payload["summary"]["end_group_count"] == 1
    assert payload["start_groups"][0]["group_key"] == "tax-sector-growth"
    assert payload["end_groups"][0]["group_key"] == "tax-sector-growth"
    assert isclose(payload["start_groups"][0]["market_value_base"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(payload["end_groups"][0]["market_value_base"], 121.0, rel_tol=0.0, abs_tol=1e-12)


def test_taxonomy_calculation_groups_use_period_end_view_and_preserve_cash_group(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="taxonomy-calculation-groups-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "taxonomy-calculation-groups-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 150.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "taxonomy-calculation-groups-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "taxonomy-calculation-groups-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        },
        {
            "taxonomy_node_id": "tax-sector-growth",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Growth",
            "node_code": "GROWTH",
            "sort_order": 1,
            "is_terminal": True,
            "status": "active",
        },
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-02",
            "status": "active",
        },
        {
            "assignment_id": "assign-0002",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-growth",
            "effective_from": "2026-01-03",
            "effective_to": None,
            "status": "active",
        },
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/taxonomy-calculation-groups-test/performance/calculation/groups"
        "?axis=taxonomy&taxonomy_id=tax-sector"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert summary["axis"] == "taxonomy"
    assert summary["taxonomy_id"] == "tax-sector"
    assert isclose(summary["total_initial_value"], 150.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_final_value"], 171.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_delta"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_pnl"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_residual_delta"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    groups = {item["group_key"]: item for item in payload["groups"]}
    assert "unassigned:tax-sector" not in groups
    assert "tax-sector-value" not in groups
    assert set(groups) == {"tax-sector-growth", "cash"}
    assert isclose(groups["tax-sector-growth"]["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-growth"]["final_value"], 121.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-growth"]["delta"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-growth"]["total_pnl"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-growth"]["residual_delta"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["cash"]["initial_value"], 50.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["cash"]["final_value"], 50.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["cash"]["delta"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["cash"]["total_pnl"], 0.0, rel_tol=0.0, abs_tol=1e-12)


def test_taxonomy_calculation_groups_keep_sold_out_instruments_in_effective_group(client, monkeypatch):
    instrument_id = "equity-us-sold-tax-test"
    portfolio_id = "taxonomy-sold-out-period-end-test"
    instrument_detail = _test_instrument_detail(
        instrument_id=instrument_id,
        instrument_name="Sold Taxonomy Equity",
        history=[
            ("2026-01-02", "100.00"),
            ("2026-01-03", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda target_id: deepcopy(instrument_detail) if target_id == instrument_id else None,
    )

    instrument_ref = {
        "instrument_id": instrument_id,
        "instrument_name": "Sold Taxonomy Equity",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "SOLD", "is_primary": True}],
    }
    store = _minimal_store(
        portfolio_id=portfolio_id,
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": portfolio_id,
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": portfolio_id,
                "transaction_type": "buy",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": instrument_id,
                "instrument_ref": instrument_ref,
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy sold taxonomy equity.",
                "created_at": "2026-01-02T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": portfolio_id,
                "transaction_type": "sell",
                "trade_date": "2026-01-03",
                "settlement_date": "2026-01-03",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": instrument_id,
                "instrument_ref": instrument_ref,
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell sold taxonomy equity.",
                "created_at": "2026-01-03T15:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-31"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": portfolio_id,
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-core",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Core",
            "node_code": "CORE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": instrument_id,
            "taxonomy_node_id": "tax-sector-core",
            "effective_from": "2026-01-02",
            "effective_to": "2026-01-03",
            "status": "active",
        }
    ]
    _write_store(store)

    response = client.get(
        f"/api/portfolios/{portfolio_id}/performance/calculation/groups"
        "?axis=taxonomy&taxonomy_id=tax-sector&start_date=2026-01-02&end_date=2026-01-31"
    )
    assert response.status_code == 200
    payload = response.json()
    groups = {item["group_key"]: item for item in payload["groups"]}
    assert "unassigned:tax-sector" not in groups
    assert set(groups) == {"tax-sector-core", "cash"}
    assert isclose(groups["tax-sector-core"]["final_value"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-core"]["capital_gains"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-core"]["realized_capital_gains"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["tax-sector-core"]["unrealized_pnl_change"], 0.0, rel_tol=0.0, abs_tol=1e-12)

    children = {item["item_key"]: item for item in groups["tax-sector-core"]["children"]}
    assert instrument_id in children
    assert isclose(children[instrument_id]["final_value"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(children[instrument_id]["realized_capital_gains"], 10.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_drilldown_returns_instrument_capital_gains(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="calculation-drilldown-instrument-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-drilldown-instrument-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-drilldown-instrument-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-drilldown-instrument-test/performance/calculation/drilldown"
        "?axis=instrument&bucket=capital_gains"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "instrument"
    assert payload["summary"]["bucket"] == "capital_gains"
    assert isclose(payload["summary"]["total_amount"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    groups = {item["group_key"]: item for item in payload["groups"]}
    group = groups["equity-us-test"]
    assert group["group_key"] == "equity-us-test"
    assert isclose(group["amount"], 21.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(group["final_value"], 121.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["cash"]["amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_drilldown_taxonomy_uses_period_end_view(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
            ("2026-01-03", "121.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="calculation-drilldown-taxonomy-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-drilldown-taxonomy-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-drilldown-taxonomy-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "calculation-drilldown-taxonomy-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-value",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Value",
            "node_code": "VALUE",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        },
        {
            "taxonomy_node_id": "tax-sector-growth",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Growth",
            "node_code": "GROWTH",
            "sort_order": 1,
            "is_terminal": True,
            "status": "active",
        },
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-value",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-02",
            "status": "active",
        },
        {
            "assignment_id": "assign-0002",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-growth",
            "effective_from": "2026-01-03",
            "effective_to": None,
            "status": "active",
        },
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-drilldown-taxonomy-test/performance/calculation/drilldown"
        "?axis=taxonomy&taxonomy_id=tax-sector&bucket=residual_delta"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "taxonomy"
    assert payload["summary"]["bucket"] == "residual_delta"
    assert payload["summary"]["taxonomy_id"] == "tax-sector"
    assert isclose(payload["summary"]["total_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    groups = {item["group_key"]: item for item in payload["groups"]}
    assert "tax-sector-value" not in groups
    assert set(groups) == {"tax-sector-growth", "cash"}
    assert isclose(groups["tax-sector-growth"]["amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(groups["cash"]["amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_entries_extract_attached_tax_bucket_by_instrument(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "100.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="calculation-entries-tax-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-entries-tax-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-entries-tax-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-entries-tax-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 3.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Dividend with withholding tax.",
                "created_at": "2026-01-02T12:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-entries-tax-test/performance/calculation/entries"
        "?axis=instrument&bucket=taxes"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["bucket"] == "taxes"
    assert payload["summary"]["entry_count"] == 1
    assert isclose(payload["summary"]["total_amount"], 3.0, rel_tol=0.0, abs_tol=1e-12)
    entry = payload["entries"][0]
    assert entry["entry_kind"] == "transaction"
    assert entry["component_kind"] == "attached_tax"
    assert entry["transaction_id"] == "txn-0003"
    assert entry["group_key"] == "equity-us-test"
    assert isclose(entry["base_amount"], 3.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_entries_return_realized_gain_realizations(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "110.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="calculation-entries-realized-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-entries-realized-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-entries-realized-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-entries-realized-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 110.0,
                "gross_amount": 110.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell test equity.",
                "created_at": "2026-01-02T15:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-02"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-entries-realized-test/performance/calculation/entries"
        "?axis=instrument&bucket=realized_capital_gains"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["bucket"] == "realized_capital_gains"
    assert payload["summary"]["entry_count"] == 1
    assert isclose(payload["summary"]["total_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    entry = payload["entries"][0]
    assert entry["entry_kind"] == "realization"
    assert entry["component_kind"] == "realization"
    assert entry["transaction_id"] == "txn-0003"
    assert entry["group_key"] == "equity-us-test"
    assert isclose(entry["base_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_entries_calendar_rolls_up_monthly_earnings(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "100.00"),
            ("2026-02-03", "100.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="calculation-entries-calendar-earnings-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-entries-calendar-earnings-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-entries-calendar-earnings-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-entries-calendar-earnings-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "January dividend.",
                "created_at": "2026-01-02T12:00:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "calculation-entries-calendar-earnings-test",
                "transaction_type": "dividend",
                "trade_date": "2026-02-03",
                "settlement_date": "2026-02-03",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 30.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "February dividend.",
                "created_at": "2026-02-03T12:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-02-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-entries-calendar-earnings-test/performance/calculation/entries/calendar"
        "?axis=instrument&bucket=earnings&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["bucket"] == "earnings"
    assert payload["summary"]["frequency"] == "monthly"
    assert payload["summary"]["bucket_count"] == 2
    assert payload["summary"]["entry_count"] == 2
    assert isclose(payload["summary"]["total_amount"], 50.0, rel_tol=0.0, abs_tol=1e-12)
    buckets = {(item["bucket_key"], item["group_key"]): item for item in payload["buckets"]}
    assert isclose(buckets[("2026-01", "equity-us-test")]["total_amount"], 20.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(buckets[("2026-02", "equity-us-test")]["total_amount"], 30.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_entries_calendar_rolls_up_taxonomy_taxes(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-test",
        instrument_name="Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-15", "100.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-test" else None,
    )

    store = _minimal_store(
        portfolio_id="calculation-entries-calendar-taxonomy-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-entries-calendar-taxonomy-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-entries-calendar-taxonomy-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy test equity.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-entries-calendar-taxonomy-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-15",
                "settlement_date": "2026-01-15",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-test",
                    "instrument_name": "Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "TEST", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 20.0,
                "fees": 0.0,
                "taxes": 4.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Dividend with tax.",
                "created_at": "2026-01-15T12:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-15"
    store["taxonomies"] = [
        {
            "taxonomy_id": "tax-sector",
            "portfolio_id": "calculation-entries-calendar-taxonomy-test",
            "name": "Sector",
            "taxonomy_type": "custom",
            "purpose": "performance_grouping",
            "primary_assignment_scope": "instrument",
            "planning_enabled": False,
            "budgeting_level": None,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
            "source_template_ref": None,
        }
    ]
    store["taxonomy_nodes"] = [
        {
            "taxonomy_node_id": "tax-sector-income",
            "taxonomy_id": "tax-sector",
            "parent_taxonomy_node_id": None,
            "node_name": "Income",
            "node_code": "INCOME",
            "sort_order": 0,
            "is_terminal": True,
            "status": "active",
        }
    ]
    store["taxonomy_assignments"] = [
        {
            "assignment_id": "assign-0001",
            "taxonomy_id": "tax-sector",
            "target_scope": "instrument",
            "target_entity_id": "equity-us-test",
            "taxonomy_node_id": "tax-sector-income",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "status": "active",
        }
    ]
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-entries-calendar-taxonomy-test/performance/calculation/entries/calendar"
        "?axis=taxonomy&taxonomy_id=tax-sector&bucket=taxes&frequency=monthly"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["axis"] == "taxonomy"
    assert payload["summary"]["bucket"] == "taxes"
    assert payload["summary"]["taxonomy_id"] == "tax-sector"
    assert payload["summary"]["bucket_count"] == 1
    assert isclose(payload["summary"]["total_amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "tax-sector-income"
    assert isclose(bucket["total_amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)


def test_period_calculation_entries_calendar_can_filter_by_group_key(client, monkeypatch):
    instrument_details = {
        "equity-us-alpha": _test_instrument_detail(
            instrument_id="equity-us-alpha",
            instrument_name="Alpha Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-20", "100.00"),
            ],
        ),
        "equity-us-beta": _test_instrument_detail(
            instrument_id="equity-us-beta",
            instrument_name="Beta Equity",
            history=[
                ("2026-01-01", "100.00"),
                ("2026-01-20", "100.00"),
            ],
        ),
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_details.get(instrument_id)),
    )

    store = _minimal_store(
        portfolio_id="calculation-entries-calendar-filter-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "calculation-entries-calendar-filter-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 200.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "calculation-entries-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy alpha.",
                "created_at": "2026-01-01T09:30:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "calculation-entries-calendar-filter-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy beta.",
                "created_at": "2026-01-01T09:31:00Z",
            },
            {
                "transaction_id": "txn-0004",
                "portfolio_id": "calculation-entries-calendar-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-15",
                "settlement_date": "2026-01-15",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-alpha",
                "instrument_ref": {
                    "instrument_id": "equity-us-alpha",
                    "instrument_name": "Alpha Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "ALPHA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 10.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Alpha dividend.",
                "created_at": "2026-01-15T12:00:00Z",
            },
            {
                "transaction_id": "txn-0005",
                "portfolio_id": "calculation-entries-calendar-filter-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-20",
                "settlement_date": "2026-01-20",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-beta",
                "instrument_ref": {
                    "instrument_id": "equity-us-beta",
                    "instrument_name": "Beta Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [{"identifier_type": "ticker", "identifier_value": "BETA", "is_primary": True}],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 15.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Beta dividend.",
                "created_at": "2026-01-20T12:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-20"
    _write_store(store)

    response = client.get(
        "/api/portfolios/calculation-entries-calendar-filter-test/performance/calculation/entries/calendar"
        "?axis=instrument&bucket=earnings&frequency=monthly&group_key=equity-us-alpha"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["group_key"] == "equity-us-alpha"
    assert payload["summary"]["group_label"] == "Alpha Equity"
    assert payload["summary"]["bucket_count"] == 1
    assert payload["summary"]["group_count"] == 1
    assert payload["summary"]["entry_count"] == 1
    assert isclose(payload["summary"]["total_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert len(payload["buckets"]) == 1
    bucket = payload["buckets"][0]
    assert bucket["group_key"] == "equity-us-alpha"
    assert bucket["group_label"] == "Alpha Equity"
    assert isclose(bucket["total_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)


def test_cash_currency_gains_flow_through_performance_and_calculation(client, monkeypatch):
    fx_detail = {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(fx_detail) if instrument_id == "fx-usd-hkd" else None,
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "cash-fx-test",
                "portfolio_name": "cash-fx-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 0,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "cash-fx-test",
                "account_name": "Main HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_instrument_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "cash-fx-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-hkd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening HKD cash.",
                "created_at": "2026-01-01T09:00:00Z",
            }
        ],
    }
    _write_store(store)

    performance_response = client.get("/api/portfolios/cash-fx-test/performance")
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    performance_summary = performance_payload["summary"]
    assert isclose(performance_summary["cash_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["cumulative_twr"], 0.04, rel_tol=0.0, abs_tol=1e-12)

    snapshot_response = client.get(
        "/api/portfolios/cash-fx-test/snapshots/daily"
    )
    assert snapshot_response.status_code == 200
    final_snapshot = next(
        item
        for item in snapshot_response.json()["snapshots"]
        if item["as_of_date"] == "2026-01-02"
    )
    fx_manifest = final_snapshot["fx_dependency_manifest"]
    assert fx_manifest["requested_as_of_date"] == "2026-01-02"
    assert fx_manifest["dependencies"]
    expected_fx_fingerprint = hashlib.sha256(
        json.dumps(
            {
                key: value
                for key, value in fx_manifest.items()
                if key != "fingerprint"
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert fx_manifest["fingerprint"] == expected_fx_fingerprint

    holdings_response = client.get("/api/workspace/holdings", params={"portfolio_id": "cash-fx-test"})
    assert holdings_response.status_code == 200
    holdings_payload = holdings_response.json()
    hkd_cash_row = next(
        row for row in holdings_payload["rows"] if row["instrument_core"]["instrument_id"] == "cash:HKD"
    )
    assert hkd_cash_row["instrument_core"]["instrument_type"] == "cash"
    assert hkd_cash_row["coverage_status"] == "cash"
    assert isclose(hkd_cash_row["quantity"], 780.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(hkd_cash_row["market_value"], 780.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(hkd_cash_row["market_value_base"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert hkd_cash_row["unrealized_pnl"] is None
    assert hkd_cash_row["unrealized_pnl_base"] is None
    assert hkd_cash_row["unrealized_return"] is None
    assert isclose(hkd_cash_row["day_change_pct"], 0.04, rel_tol=0.0, abs_tol=1e-12)
    assert hkd_cash_row["day_change_value"] is None
    assert isclose(hkd_cash_row["day_change_value_base"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(hkd_cash_row["allocation"], 1.0, rel_tol=0.0, abs_tol=1e-12)
    assert hkd_cash_row["price_chart_1m"] == []
    assert "instrument_return_series_all" not in hkd_cash_row
    assert isclose(holdings_payload["totals"]["market_value"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(holdings_payload["totals"]["day_change_value"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(holdings_payload["totals"]["day_change_pct"], 0.04, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(holdings_payload["totals"]["cash_balance"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(holdings_payload["totals"]["nav"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert holdings_payload["totals"]["unrealized_pnl_base"] == 0.0
    assert holdings_payload["totals"]["unrealized_return"] is None

    calculation_response = client.get("/api/portfolios/cash-fx-test/performance/calculation")
    assert calculation_response.status_code == 200
    calculation_payload = calculation_response.json()
    calculation_summary = calculation_payload["summary"]
    assert isclose(calculation_summary["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["final_value"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["delta"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["cash_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert "residual_gains" not in calculation_summary
    assert "cash_fx_residual_gains" not in calculation_summary

    line_by_key = {item["key"]: item for item in calculation_payload["lines"]}
    assert isclose(line_by_key["cash_currency_gains"]["amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert "residual_gains" not in line_by_key

    groups_response = client.get("/api/portfolios/cash-fx-test/performance/calculation/groups?axis=instrument")
    assert groups_response.status_code == 200
    cash_group = next(item for item in groups_response.json()["groups"] if item["group_key"] == "cash")
    assert isclose(cash_group["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(cash_group["final_value"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(cash_group["cash_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(cash_group["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    instrument_cash_child = next(item for item in cash_group["children"] if item["item_kind"] == "cash")
    assert instrument_cash_child["item_key"] == "cash:cash-hkd-main:HKD"
    assert instrument_cash_child["item_label"] == "Main HKD Cash (HKD)"
    assert isclose(instrument_cash_child["final_value"], 104.0, rel_tol=0.0, abs_tol=1e-12)

    instrument_type_groups_response = client.get("/api/portfolios/cash-fx-test/performance/calculation/groups?axis=instrument_type")
    assert instrument_type_groups_response.status_code == 200
    instrument_type_cash_group = next(
        item for item in instrument_type_groups_response.json()["groups"] if item["group_key"] == "cash"
    )
    cash_child = next(item for item in instrument_type_cash_group["children"] if item["item_kind"] == "cash")
    assert cash_child["item_key"] == "cash:cash-hkd-main:HKD"
    assert cash_child["item_label"] == "Main HKD Cash (HKD)"
    assert isclose(cash_child["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(cash_child["final_value"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(cash_child["cash_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)


def test_complete_nav_does_not_hide_incomplete_book_pnl_or_twr_state(
    client,
    monkeypatch,
):
    fx_detail = {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "USDHKD",
                "is_primary": True,
            }
        ],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            }
        ],
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: (
            deepcopy(fx_detail) if instrument_id == "fx-usd-hkd" else None
        ),
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "split-coverage-test",
                "portfolio_name": "Split Coverage Test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 0,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "split-coverage-test",
                "account_name": "HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_instrument_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "split-coverage-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-hkd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 750.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening HKD cash before the first FX observation.",
                "created_at": "2026-01-01T09:00:00Z",
            }
        ],
    }
    _write_store(store)

    response = client.get(
        "/api/portfolios/split-coverage-test/snapshots/daily"
    )
    assert response.status_code == 200
    payload = response.json()
    by_date = {item["as_of_date"]: item for item in payload["snapshots"]}
    day_two = by_date["2026-01-02"]

    assert day_two["nav_coverage_state"] == "complete"
    assert day_two["nav_coverage_reason_codes"] == []
    assert day_two["ending_nav"] == pytest.approx(100.0)
    assert day_two["book_pnl_coverage_state"] == "partial"
    assert "cash_currency_attribution_incomplete" in day_two[
        "book_pnl_coverage_reason_codes"
    ]
    assert day_two["realized_pnl"] is None
    assert day_two["cash_currency_gains"] is None
    assert day_two["instrument_currency_gains"] is None
    assert day_two["total_pnl"] is None
    # Book-P&L history is unavailable, but the current fair-value NAV is still
    # allowed to establish the first TWR anchor.  There was no prior broken
    # link, so this is a qualified ``no_anchor`` day rather than a re-anchor.
    assert day_two["twr_state"] == "no_anchor"
    assert day_two["twr_reliability_status"] == "qualified"
    assert day_two["twr_reliability_reasons"] == []
    assert payload["summary"]["latest_complete_as_of_date"] == "2026-01-02"


def test_instrument_currency_gains_flow_through_performance_and_calculation(client, monkeypatch):
    fx_detail = {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    hk_fund_detail = {
        "instrument_id": "fund-hk-test",
        "instrument_name": "HK Fund",
        "instrument_type": "fund",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "HKFUND", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-01",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-01-02",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    details = {
        "fx-usd-hkd": fx_detail,
        "fund-hk-test": hk_fund_detail,
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(details.get(instrument_id)),
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "instrument-fx-test",
                "portfolio_name": "instrument-fx-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 1,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "instrument-fx-test",
                "account_name": "Main HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_instrument_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            },
            {
                "account_id": "broker-hk-core",
                "portfolio_id": "instrument-fx-test",
                "account_name": "HK Brokerage",
                "account_type": "securities_account",
                "currency": "HKD",
                "institution": "Test Broker",
                "default_settlement_cash_account_id": "cash-hkd-main",
                "cost_basis_method": "fifo",
                "allowed_instrument_types": ["fund"],
                "opened_at": "2026-01-01",
                "status": "active",
            },
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "instrument-fx-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": None,
                "instrument_id": "fund-hk-test",
                "instrument_ref": {
                    "instrument_id": "fund-hk-test",
                    "instrument_name": "HK Fund",
                    "instrument_type": "fund",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 78.0,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening HK fund position.",
                "created_at": "2026-01-01T09:00:00Z",
            }
        ],
    }
    _write_store(store)

    performance_response = client.get("/api/portfolios/instrument-fx-test/performance")
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    performance_summary = performance_payload["summary"]
    assert isclose(performance_summary["instrument_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["unrealized_pnl"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["total_pnl"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["cumulative_twr"], 0.04, rel_tol=0.0, abs_tol=1e-12)

    calculation_response = client.get("/api/portfolios/instrument-fx-test/performance/calculation")
    assert calculation_response.status_code == 200
    calculation_payload = calculation_response.json()
    calculation_summary = calculation_payload["summary"]
    assert isclose(calculation_summary["initial_value"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["final_value"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["delta"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["capital_gains"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(calculation_summary["instrument_currency_gains"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert "residual_gains" not in calculation_summary

    line_by_key = {item["key"]: item for item in calculation_payload["lines"]}
    assert isclose(line_by_key["instrument_currency_gains"]["amount"], 4.0, rel_tol=0.0, abs_tol=1e-12)


def test_foreign_currency_income_and_realized_pnl_are_reported_in_base_currency(client, monkeypatch):
    fx_detail = {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    hk_equity_detail = {
        "instrument_id": "equity-hk-income-test",
        "instrument_name": "HK Income Equity",
        "instrument_type": "equity",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "HKINC", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-01-01",
                "value": "78.00",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-01-02",
                "value": "82.00",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    details = {
        "fx-usd-hkd": fx_detail,
        "equity-hk-income-test": hk_equity_detail,
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(details.get(instrument_id)),
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "foreign-income-realized-test",
                "portfolio_name": "foreign-income-realized-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 1,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "foreign-income-realized-test",
                "account_name": "Main HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_instrument_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            },
            {
                "account_id": "broker-hk-core",
                "portfolio_id": "foreign-income-realized-test",
                "account_name": "HK Brokerage",
                "account_type": "securities_account",
                "currency": "HKD",
                "institution": "Test Broker",
                "default_settlement_cash_account_id": "cash-hkd-main",
                "cost_basis_method": "fifo",
                "allowed_instrument_types": ["equity"],
                "opened_at": "2026-01-01",
                "status": "active",
            },
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "foreign-income-realized-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": None,
                "instrument_id": "equity-hk-income-test",
                "instrument_ref": {
                    "instrument_id": "equity-hk-income-test",
                    "instrument_name": "HK Income Equity",
                    "instrument_type": "equity",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 78.0,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening HK equity position.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "foreign-income-realized-test",
                "transaction_type": "dividend",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": "cash-hkd-main",
                "instrument_id": "equity-hk-income-test",
                "instrument_ref": {
                    "instrument_id": "equity-hk-income-test",
                    "instrument_name": "HK Income Equity",
                    "instrument_type": "equity",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": None,
                "price": None,
                "gross_amount": 75.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "entitlement_date": "2026-01-02",
                "note": "HK dividend.",
                "created_at": "2026-01-02T09:00:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "foreign-income-realized-test",
                "transaction_type": "sell",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "broker-hk-core",
                "settlement_cash_account_id": "cash-hkd-main",
                "instrument_id": "equity-hk-income-test",
                "instrument_ref": {
                    "instrument_id": "equity-hk-income-test",
                    "instrument_name": "HK Income Equity",
                    "instrument_type": "equity",
                    "currency": "HKD",
                    "identifiers": [],
                },
                "quantity": 10.0,
                "price": 82.0,
                "gross_amount": 820.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Sell HK equity.",
                "created_at": "2026-01-02T10:00:00Z",
            },
        ],
    }
    _write_store(store)

    performance_response = client.get("/api/portfolios/foreign-income-realized-test/performance")
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    performance_summary = performance_payload["summary"]
    assert isclose(performance_summary["income_cash_amount"], 10.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["realized_pnl"], 40.0 / 7.5, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(performance_summary["total_pnl"], (75.0 + 40.0) / 7.5, rel_tol=0.0, abs_tol=1e-12)


def test_foreign_currency_external_flow_is_converted_before_daily_twr(client, monkeypatch):
    fx_detail = {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "status": "complete",
            },
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-02",
                "value": "7.50",
                "currency": "HKD",
                "status": "complete",
            },
        ],
    }
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(fx_detail) if instrument_id == "fx-usd-hkd" else None,
    )

    store = {
        "portfolios": [
            {
                "portfolio_id": "foreign-external-flow-test",
                "portfolio_name": "foreign-external-flow-test",
                "base_currency": "USD",
                "valuation_timezone": "Asia/Shanghai",
                "valuation_cutoff_policy": "latest_complete_eod",
                "as_of_date": "2026-01-02",
                "nav": 0.0,
                "day_change_value": 0.0,
                "day_change_pct": 0.0,
                "securities_count": 0,
                "sort_order": 0,
            }
        ],
        "accounts": [
            {
                "account_id": "cash-hkd-main",
                "portfolio_id": "foreign-external-flow-test",
                "account_name": "Main HKD Cash",
                "account_type": "deposit_account",
                "currency": "HKD",
                "institution": "Test Bank",
                "default_settlement_cash_account_id": None,
                "cost_basis_method": None,
                "allowed_instrument_types": None,
                "opened_at": "2026-01-01",
                "status": "active",
            }
        ],
        "transactions": [
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "foreign-external-flow-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-hkd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 780.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening HKD cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "foreign-external-flow-test",
                "transaction_type": "deposit",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-hkd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 750.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "HKD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "HKD deposit.",
                "created_at": "2026-01-02T09:00:00Z",
            },
        ],
    }
    _write_store(store)

    snapshots_response = client.get("/api/portfolios/foreign-external-flow-test/snapshots/daily")
    assert snapshots_response.status_code == 200
    payload = snapshots_response.json()
    by_date = {item["as_of_date"]: item for item in payload["snapshots"]}
    day_two = by_date["2026-01-02"]
    assert isclose(day_two["external_cash_in"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(day_two["absolute_change"], 104.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(day_two["delta"], 4.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(day_two["daily_twr"], 0.02, rel_tol=0.0, abs_tol=1e-12)


def test_performance_summary_custom_period_uses_period_deltas(client, monkeypatch):
    monkeypatch.setattr(_TEST_INSTRUMENT_CATALOG, "get", lambda instrument_id: None)

    store = _minimal_store(
        portfolio_id="performance-period-delta-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "performance-period-delta-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "performance-period-delta-test",
                "transaction_type": "interest",
                "trade_date": "2026-01-02",
                "settlement_date": "2026-01-02",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 5.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Day-two interest.",
                "created_at": "2026-01-02T09:00:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "performance-period-delta-test",
                "transaction_type": "interest",
                "trade_date": "2026-01-03",
                "settlement_date": "2026-01-03",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 7.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Day-three interest.",
                "created_at": "2026-01-03T09:00:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/performance-period-delta-test/performance"
        "?start_date=2026-01-02&end_date=2026-01-03"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]

    assert summary["start_date"] == "2026-01-02"
    assert summary["end_date"] == "2026-01-03"
    assert isclose(summary["start_nav"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["end_nav"], 112.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["absolute_change"], 12.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["delta"], 12.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["income_cash_amount"], 12.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["realized_pnl"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["unrealized_pnl"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["expense_cash_amount"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["external_cash_in"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["external_cash_out"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_pnl"], 12.0, rel_tol=0.0, abs_tol=1e-12)


def test_performance_summary_ignores_flows_before_first_complete_snapshot(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-partial-anchor",
        instrument_name="Partial Anchor Equity",
        history=[
            ("2026-01-02", "150.00"),
            ("2026-01-03", "165.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-partial-anchor" else None,
    )

    store = _minimal_store(
        portfolio_id="performance-partial-anchor-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "performance-partial-anchor-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "performance-partial-anchor-test",
                "transaction_type": "deposit",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 50.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Capital contribution before first complete snapshot.",
                "created_at": "2026-01-01T09:05:00Z",
            },
            {
                "transaction_id": "txn-0003",
                "portfolio_id": "performance-partial-anchor-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-partial-anchor",
                "instrument_ref": {
                    "instrument_id": "equity-us-partial-anchor",
                    "instrument_name": "Partial Anchor Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "ticker",
                            "identifier_value": "PARTIAL",
                            "is_primary": True,
                        }
                    ],
                },
                "quantity": 1.0,
                "price": 150.0,
                "gross_amount": 150.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy before first priced day.",
                "created_at": "2026-01-01T09:10:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/performance-partial-anchor-test/performance"
        "?start_date=2026-01-01&end_date=2026-01-03"
    )
    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]

    assert summary["start_date"] == "2026-01-02"
    assert summary["end_date"] == "2026-01-03"
    assert summary["latest_complete_as_of_date"] == "2026-01-03"
    assert isclose(summary["start_nav"], 150.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["end_nav"], 165.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["absolute_change"], 15.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["external_cash_in"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["external_cash_out"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["delta"], 15.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["unrealized_pnl"], 15.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(summary["total_pnl"], 15.0, rel_tol=0.0, abs_tol=1e-12)


def test_daily_snapshots_keep_nav_constant_until_security_cash_settles(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-settlement-test",
        instrument_name="Settlement Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "100.00"),
            ("2026-01-03", "100.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-settlement-test" else None,
    )

    store = _minimal_store(
        portfolio_id="settlement-replay-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "settlement-replay-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "settlement-replay-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-03",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-settlement-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-settlement-test",
                    "instrument_name": "Settlement Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "ticker",
                            "identifier_value": "SETL",
                            "is_primary": True,
                        }
                    ],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy with delayed settlement.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get("/api/portfolios/settlement-replay-test/snapshots/daily")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["latest_complete_as_of_date"] == "2026-01-03"

    by_date = {item["as_of_date"]: item for item in payload["snapshots"]}
    assert by_date["2026-01-01"]["cash_balance"] == 100.0
    assert by_date["2026-01-02"]["cash_balance"] == 100.0
    assert by_date["2026-01-03"]["cash_balance"] == 0.0
    assert by_date["2026-01-01"]["ending_nav"] == 100.0
    assert by_date["2026-01-02"]["ending_nav"] == 100.0
    assert by_date["2026-01-03"]["ending_nav"] == 100.0


def test_account_contribution_carries_pending_settlement_in_ending_values(client, monkeypatch):
    instrument_detail = _test_instrument_detail(
        instrument_id="equity-us-account-settlement-test",
        instrument_name="Account Settlement Test Equity",
        history=[
            ("2026-01-01", "100.00"),
            ("2026-01-02", "100.00"),
            ("2026-01-03", "100.00"),
        ],
    )
    monkeypatch.setattr(
        _TEST_INSTRUMENT_CATALOG,
        "get",
        lambda instrument_id: deepcopy(instrument_detail) if instrument_id == "equity-us-account-settlement-test" else None,
    )

    store = _minimal_store(
        portfolio_id="account-contribution-settlement-test",
        transactions=[
            {
                "transaction_id": "txn-0001",
                "portfolio_id": "account-contribution-settlement-test",
                "transaction_type": "opening_balance",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-01",
                "account_id": "cash-usd-main",
                "settlement_cash_account_id": None,
                "instrument_id": None,
                "instrument_ref": None,
                "quantity": None,
                "price": None,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Opening cash.",
                "created_at": "2026-01-01T09:00:00Z",
            },
            {
                "transaction_id": "txn-0002",
                "portfolio_id": "account-contribution-settlement-test",
                "transaction_type": "buy",
                "trade_date": "2026-01-01",
                "settlement_date": "2026-01-03",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-account-settlement-test",
                "instrument_ref": {
                    "instrument_id": "equity-us-account-settlement-test",
                    "instrument_name": "Account Settlement Test Equity",
                    "instrument_type": "equity",
                    "currency": "USD",
                    "identifiers": [
                        {
                            "identifier_type": "ticker",
                            "identifier_value": "ACTSETL",
                            "is_primary": True,
                        }
                    ],
                },
                "quantity": 1.0,
                "price": 100.0,
                "gross_amount": 100.0,
                "fees": 0.0,
                "taxes": 0.0,
                "currency": "USD",
                "transfer_scope": None,
                "transfer_object_type": None,
                "transfer_group_id": None,
                "counterparty_account_id": None,
                "note": "Buy with delayed settlement.",
                "created_at": "2026-01-01T09:30:00Z",
            },
        ],
    )
    store["portfolios"][0]["as_of_date"] = "2026-01-03"
    _write_store(store)

    response = client.get(
        "/api/portfolios/account-contribution-settlement-test/performance/contribution?axis=account"
    )
    assert response.status_code == 200
    payload = response.json()
    slices = [
        item
        for item in payload["daily_slices"]
        if item["as_of_date"] == "2026-01-01"
    ]
    by_group = {item["group_key"]: item for item in slices}

    assert isclose(by_group["cash-usd-main"]["ending_value_base"], 0.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(by_group["broker-us-core"]["ending_value_base"], 100.0, rel_tol=0.0, abs_tol=1e-12)
    assert isclose(
        sum((item["ending_value_base"] or 0.0) for item in slices),
        100.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert isclose(
        sum((item["ending_weight"] or 0.0) for item in slices),
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
