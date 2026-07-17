from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest

from portfolio_app.api.routes import performance as performance_routes
from portfolio_app.services import performance, return_chain


def _portfolio(portfolio_id: str, as_of_date: date) -> dict[str, object]:
    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio_id,
        "base_currency": "USD",
        "valuation_timezone": "Asia/Shanghai",
        "valuation_cutoff_policy": "latest_complete_eod",
        "as_of_date": as_of_date.isoformat(),
    }


def _accounts(portfolio_id: str) -> list[dict[str, object]]:
    return [
        {
            "account_id": "cash-usd-main",
            "portfolio_id": portfolio_id,
            "account_name": "Cash",
            "account_type": "deposit_account",
            "currency": "USD",
            "cost_basis_method": None,
        },
        {
            "account_id": "broker-us-main",
            "portfolio_id": portfolio_id,
            "account_name": "Broker",
            "account_type": "brokerage",
            "currency": "USD",
            "cost_basis_method": "fifo",
            "default_settlement_cash_account_id": "cash-usd-main",
        },
    ]


def _transaction(
    portfolio_id: str,
    transaction_id: str,
    transaction_type: str,
    trade_date: date,
    *,
    gross_amount: float,
    account_id: str = "cash-usd-main",
    instrument_id: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
) -> dict[str, object]:
    instrument_ref = None
    if instrument_id is not None:
        instrument_ref = {
            "instrument_id": instrument_id,
            "instrument_name": "Gap Asset",
            "instrument_type": "equity",
            "currency": "USD",
            "identifiers": [],
        }
    return {
        "transaction_id": transaction_id,
        "portfolio_id": portfolio_id,
        "transaction_type": transaction_type,
        "trade_date": trade_date.isoformat(),
        "settlement_date": trade_date.isoformat(),
        "account_id": account_id,
        "settlement_cash_account_id": "cash-usd-main" if instrument_id else None,
        "instrument_id": instrument_id,
        "instrument_ref": instrument_ref,
        "quantity": quantity,
        "price": price,
        "gross_amount": gross_amount,
        "fees": 0.0,
        "taxes": 0.0,
        "currency": "USD",
        "created_at": f"{trade_date.isoformat()}T09:00:00Z",
    }


def _snapshot(
    as_of_date: date,
    *,
    nav: float | None,
    daily_twr: float | None,
    valuation_coverage_state: str = "complete",
    return_coverage_state: str = "complete",
    stale_price_flag: bool = False,
) -> dict[str, object]:
    aggregate_coverage = (
        "complete"
        if valuation_coverage_state == return_coverage_state == "complete"
        else "partial"
    )
    beginning_nav = None if nav is None or daily_twr is None else nav / (1.0 + daily_twr)
    return {
        "as_of_date": as_of_date,
        "base_currency": "USD",
        "valuation_timezone": "Asia/Shanghai",
        "valuation_cutoff_policy": "latest_complete_eod",
        "coverage_state": aggregate_coverage,
        "valuation_coverage_state": valuation_coverage_state,
        "return_coverage_state": return_coverage_state,
        "book_pnl_coverage_state": "complete",
        "attribution_coverage_state": "complete",
        "return_chain_continuous": return_coverage_state == "complete",
        "stale_price_flag": stale_price_flag,
        "stale_fx_flag": False,
        "market_observation_count": 1,
        "return_observation_eligible": daily_twr is not None,
        "nav": nav,
        "beginning_nav": beginning_nav,
        "ending_nav": nav,
        "external_cash_in": 0.0,
        "external_cash_out": 0.0,
        "net_external_inflow": 0.0,
        "absolute_change": None if nav is None or beginning_nav is None else nav - beginning_nav,
        "delta": None if nav is None or beginning_nav is None else nav - beginning_nav,
        "daily_twr": daily_twr,
        "cumulative_twr": daily_twr,
        "drawdown": 0.0 if daily_twr is not None else None,
    }


def test_fair_value_nav_and_twr_do_not_depend_on_book_pnl_coverage(monkeypatch) -> None:
    portfolio_id = "coverage-split-test"
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )
    monkeypatch.setattr(
        performance,
        "_sum_period_realized_capital_gains",
        lambda *args, **kwargs: {
            "realized_capital_gains": 0.0,
            "coverage_complete": False,
            "stale_fx_flag": False,
        },
    )
    transactions = [
        _transaction(portfolio_id, "txn-open", "opening_balance", date(2026, 1, 1), gross_amount=100.0),
        _transaction(portfolio_id, "txn-interest", "interest", date(2026, 1, 2), gross_amount=10.0),
    ]

    snapshots = performance.build_daily_portfolio_snapshots(
        _portfolio(portfolio_id, date(2026, 1, 2)),
        _accounts(portfolio_id),
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
    )

    second_day = snapshots[-1]
    assert second_day["valuation_coverage_state"] == "complete"
    assert second_day["return_coverage_state"] == "complete"
    assert second_day["book_pnl_coverage_state"] == "partial"
    assert second_day["coverage_state"] == "partial"
    assert second_day["nav"] == pytest.approx(110.0)
    assert second_day["daily_twr"] == pytest.approx(0.10)

    report = performance.build_portfolio_performance_report_from_snapshots(
        _portfolio(portfolio_id, date(2026, 1, 2)),
        snapshots,
        transactions=transactions,
    )
    assert report["summary"]["valuation_coverage_state"] == "complete"
    assert report["summary"]["return_coverage_state"] == "complete"
    assert report["summary"]["book_pnl_coverage_state"] == "partial"
    assert report["summary"]["cumulative_twr"] == pytest.approx(0.10)


def test_external_flow_inside_unreliable_valuation_gap_reanchors_return_chain(monkeypatch) -> None:
    portfolio_id = "flow-gap-reanchor-test"
    instrument_id = "equity-gap-asset"
    instrument_detail = {
        "instrument_id": instrument_id,
        "instrument_name": "Gap Asset",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": day.isoformat(),
                "value": "1.00",
                "currency": "USD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            }
            for day in (date(2026, 1, 3), date(2026, 1, 4))
        ],
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda candidate: deepcopy(instrument_detail) if candidate == instrument_id else None,
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {"supported_currencies": ["USD"], "maintained_pairs": [], "rates": []},
    )
    transactions = [
        _transaction(portfolio_id, "txn-open", "opening_balance", date(2026, 1, 1), gross_amount=100.0),
        _transaction(portfolio_id, "txn-deposit", "deposit", date(2026, 1, 2), gross_amount=100.0),
        _transaction(
            portfolio_id,
            "txn-buy",
            "buy",
            date(2026, 1, 2),
            gross_amount=100.0,
            account_id="broker-us-main",
            instrument_id=instrument_id,
            quantity=100.0,
            price=1.0,
        ),
    ]

    snapshots = performance.build_daily_portfolio_snapshots(
        _portfolio(portfolio_id, date(2026, 1, 4)),
        _accounts(portfolio_id),
        transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 4),
    )
    by_date = {snapshot["as_of_date"]: snapshot for snapshot in snapshots}

    assert by_date[date(2026, 1, 1)]["nav"] == pytest.approx(100.0)
    assert by_date[date(2026, 1, 2)]["nav"] is None
    assert by_date[date(2026, 1, 2)]["external_cash_in"] == pytest.approx(100.0)
    assert by_date[date(2026, 1, 3)]["nav"] == pytest.approx(200.0)
    assert by_date[date(2026, 1, 3)]["daily_twr"] is None
    assert by_date[date(2026, 1, 3)]["return_coverage_state"] != "complete"
    assert by_date[date(2026, 1, 4)]["beginning_nav"] == pytest.approx(200.0)
    assert by_date[date(2026, 1, 4)]["daily_twr"] == pytest.approx(0.0)
    assert by_date[date(2026, 1, 2)]["cumulative_twr"] is None
    assert by_date[date(2026, 1, 3)]["drawdown"] is None
    assert by_date[date(2026, 1, 4)]["cumulative_twr"] is None
    assert by_date[date(2026, 1, 4)]["return_chain_continuous"] is False

    report = performance.build_portfolio_performance_report_from_snapshots(
        _portfolio(portfolio_id, date(2026, 1, 4)),
        snapshots,
        transactions=transactions,
    )
    assert report["summary"]["cumulative_twr"] is None
    assert report["summary"]["current_drawdown"] is None
    assert report["summary"]["irr"] is None
    assert report["daily_series"][-1]["cumulative_twr"] is None
    assert report["daily_series"][-1]["drawdown"] is None


def test_performance_summary_clamps_to_latest_reliable_endpoint() -> None:
    snapshots = [
        _snapshot(date(2025, 12, 31), nav=100.0, daily_twr=0.0),
        _snapshot(date(2026, 1, 1), nav=101.0, daily_twr=0.01),
        _snapshot(date(2026, 1, 2), nav=102.0, daily_twr=0.01, stale_price_flag=True),
        _snapshot(
            date(2026, 1, 3),
            nav=None,
            daily_twr=None,
            valuation_coverage_state="partial",
            return_coverage_state="unavailable",
        ),
    ]

    report = performance.build_portfolio_performance_report_from_snapshots(
        _portfolio("as-of-clamp-test", date(2026, 1, 4)),
        snapshots,
        transactions=[],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 4),
    )

    summary = report["summary"]
    assert summary["requested_start_date"] == date(2026, 1, 1)
    assert summary["requested_end_date"] == date(2026, 1, 4)
    assert summary["effective_start_date"] == date(2026, 1, 1)
    assert summary["effective_end_date"] == date(2026, 1, 1)
    assert summary["as_of_clamp_reason"] == "requested_end_after_latest_reliable_endpoint"
    assert [point["as_of_date"] for point in report["daily_series"]] == [date(2026, 1, 1)]


def test_performance_request_before_inception_uses_complete_inception_return_chain() -> None:
    portfolio_id = "pre-inception-window-test"
    snapshots = [
        _snapshot(date(2026, 1, 5), nav=100.0, daily_twr=0.0),
        _snapshot(date(2026, 1, 6), nav=101.0, daily_twr=0.01),
        _snapshot(date(2026, 1, 7), nav=99.99, daily_twr=-0.01),
    ]
    transactions = [
        _transaction(
            portfolio_id,
            "txn-open",
            "opening_balance",
            date(2026, 1, 5),
            gross_amount=100.0,
        )
    ]

    report = performance.build_portfolio_performance_report_from_snapshots(
        _portfolio(portfolio_id, date(2026, 1, 7)),
        snapshots,
        transactions=transactions,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 7),
    )

    summary = report["summary"]
    assert summary["requested_start_date"] == date(2026, 1, 1)
    assert summary["effective_start_date"] == date(2026, 1, 5)
    assert summary["return_coverage_state"] == "complete"
    assert summary["cumulative_twr"] == pytest.approx(-0.0001)
    assert summary["risk_return_observation_count"] == 3


def test_reliable_endpoint_clamp_distinguishes_stale_price() -> None:
    window = return_chain.resolve_reliable_snapshot_window(
        [
            _snapshot(date(2026, 1, 1), nav=100.0, daily_twr=0.0),
            _snapshot(date(2026, 1, 2), nav=100.0, daily_twr=0.0, stale_price_flag=True),
        ],
        requested_end_date=date(2026, 1, 2),
    )

    assert window["effective_end_date"] == date(2026, 1, 1)
    assert window["as_of_clamp_reason"] == "requested_end_stale_price"


def test_requested_period_requires_continuous_return_coverage() -> None:
    snapshots = [
        _snapshot(date(2025, 12, 31), nav=100.0, daily_twr=0.0),
        _snapshot(date(2026, 1, 1), nav=101.0, daily_twr=0.01),
        _snapshot(
            date(2026, 1, 2),
            nav=101.0,
            daily_twr=None,
            return_coverage_state="unavailable",
        ),
        _snapshot(date(2026, 1, 3), nav=102.01, daily_twr=0.01),
    ]

    report = performance.build_portfolio_performance_report_from_snapshots(
        _portfolio("continuous-return-test", date(2026, 1, 3)),
        snapshots,
        transactions=[],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
    )

    assert report["summary"]["return_coverage_state"] == "partial"
    assert report["summary"]["cumulative_twr"] is None


def test_full_loss_annualizes_to_negative_one_for_eligible_period() -> None:
    snapshots = [_snapshot(date(2024, 12, 31), nav=100.0, daily_twr=0.0)]
    current = date(2025, 1, 1)
    while current <= date(2026, 1, 1):
        point = _snapshot(current, nav=100.0, daily_twr=0.0)
        if current == date(2026, 1, 1):
            point.update(
                {
                    "nav": 0.0,
                    "beginning_nav": 100.0,
                    "ending_nav": 0.0,
                    "absolute_change": -100.0,
                    "delta": -100.0,
                    "daily_twr": -1.0,
                    "cumulative_twr": -1.0,
                    "drawdown": -1.0,
                }
            )
        snapshots.append(point)
        current += timedelta(days=1)

    report = performance.build_portfolio_performance_report_from_snapshots(
        _portfolio("full-loss-annualization-test", date(2026, 1, 1)),
        snapshots,
        transactions=[],
        start_date=date(2025, 1, 1),
        end_date=date(2026, 1, 1),
    )

    summary = report["summary"]
    assert summary["annualization_eligible"] is True
    assert summary["annualization_unavailable_reason"] is None
    assert summary["cumulative_twr"] == pytest.approx(-1.0)
    assert summary["annualized_twr"] == pytest.approx(-1.0)


def test_closed_month_does_not_publish_inception_inside_period_as_full_return(monkeypatch) -> None:
    daily_series = []
    current = date(2026, 2, 10)
    nav = 100.0
    while current <= date(2026, 2, 28):
        daily_series.append(_snapshot(current, nav=nav, daily_twr=0.0))
        current += timedelta(days=1)
    monkeypatch.setattr(
        performance,
        "build_portfolio_performance_report",
        lambda *args, **kwargs: {
            "portfolio_id": "inception-month-test",
            "base_currency": "USD",
            "valuation_timezone": "Asia/Shanghai",
            "valuation_cutoff_policy": "latest_complete_eod",
            "summary": {
                "requested_start_date": None,
                "requested_end_date": None,
                "effective_start_date": date(2026, 2, 10),
                "effective_end_date": date(2026, 2, 28),
            },
            "daily_series": daily_series,
        },
    )

    report = performance.build_return_calendar_report(
        _portfolio("inception-month-test", date(2026, 2, 28)),
        [],
        [],
        frequency="monthly",
    )

    bucket = report["buckets"][0]
    assert bucket["coverage_state"] == "partial"
    assert bucket["cumulative_twr"] is None


def test_snapshot_endpoint_returns_requested_and_effective_as_of(client, monkeypatch) -> None:
    snapshots = [
        _snapshot(date(2026, 1, 1), nav=100.0, daily_twr=0.0),
        _snapshot(
            date(2026, 1, 2),
            nav=None,
            daily_twr=None,
            valuation_coverage_state="partial",
            return_coverage_state="unavailable",
        ),
    ]
    monkeypatch.setattr(
        performance_routes,
        "get_portfolio",
        lambda portfolio_id: _portfolio(portfolio_id, date(2026, 1, 3)),
    )
    monkeypatch.setattr(
        performance_routes,
        "list_materialized_daily_snapshots",
        lambda *args, **kwargs: deepcopy(snapshots),
    )

    response = client.get(
        "/api/portfolios/snapshot-as-of-test/snapshots/daily",
        params={"start_date": "2026-01-01", "end_date": "2026-01-03"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["requested_start_date"] == "2026-01-01"
    assert payload["summary"]["requested_end_date"] == "2026-01-03"
    assert payload["summary"]["effective_start_date"] == "2026-01-01"
    assert payload["summary"]["effective_end_date"] == "2026-01-01"
    assert payload["summary"]["as_of_clamp_reason"] == "requested_end_after_latest_reliable_endpoint"
    assert [snapshot["as_of_date"] for snapshot in payload["snapshots"]] == ["2026-01-01"]
