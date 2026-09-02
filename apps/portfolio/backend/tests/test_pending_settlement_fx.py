from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import performance


PORTFOLIO_ID = "pending-settlement-fx"
FX_INSTRUMENT_ID = "fx-usd-hkd"
SECURITY_INSTRUMENT_ID = "equity-hkd-pending-settlement"


def _portfolio() -> dict[str, object]:
    return {
        "portfolio_id": PORTFOLIO_ID,
        "portfolio_name": "Pending Settlement FX Golden",
        "base_currency": "USD",
        "valuation_timezone": "Asia/Shanghai",
        "valuation_cutoff_policy": "latest_complete_eod",
        "as_of_date": "2026-01-02",
    }


def _accounts() -> list[dict[str, object]]:
    return [
        {
            "account_id": "cash-hkd",
            "portfolio_id": PORTFOLIO_ID,
            "account_name": "HKD Cash",
            "account_type": "deposit_account",
            "currency": "HKD",
            "default_settlement_cash_account_id": None,
            "cost_basis_method": None,
        },
        {
            "account_id": "broker-hkd",
            "portfolio_id": PORTFOLIO_ID,
            "account_name": "HKD Broker",
            "account_type": "securities_account",
            "currency": "HKD",
            "default_settlement_cash_account_id": "cash-hkd",
            "cost_basis_method": "fifo",
        },
    ]


def _transactions() -> list[dict[str, object]]:
    instrument_ref = {
        "instrument_id": SECURITY_INSTRUMENT_ID,
        "instrument_name": "Flat-price HKD Equity",
        "instrument_type": "equity",
        "exchange_code": "XNYS",
        "currency": "HKD",
        "identifiers": [],
    }
    return [
        {
            "transaction_id": "txn-opening-cash",
            "transaction_sequence": 1,
            "portfolio_id": PORTFOLIO_ID,
            "transaction_type": "opening_balance",
            "trade_date": "2026-01-01",
            "settlement_date": "2026-01-01",
            "account_id": "cash-hkd",
            "settlement_cash_account_id": None,
            "instrument_id": None,
            "instrument_ref": None,
            "quantity": None,
            "price": None,
            "gross_amount": 100.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "HKD",
            "created_at": "2026-01-01T09:00:00Z",
        },
        {
            "transaction_id": "txn-unsettled-buy",
            "transaction_sequence": 2,
            "portfolio_id": PORTFOLIO_ID,
            "transaction_type": "buy",
            "trade_date": "2026-01-01",
            "settlement_date": "2026-01-03",
            "account_id": "broker-hkd",
            "settlement_cash_account_id": "cash-hkd",
            "instrument_id": SECURITY_INSTRUMENT_ID,
            "instrument_ref": instrument_ref,
            "quantity": 1.0,
            "price": 100.0,
            "gross_amount": 100.0,
            "fees": 0.0,
            "taxes": 0.0,
            "currency": "HKD",
            "created_at": "2026-01-01T09:30:00Z",
        },
    ]


def _security_detail() -> dict[str, object]:
    return {
        "instrument_id": SECURITY_INSTRUMENT_ID,
        "instrument_name": "Flat-price HKD Equity",
        "instrument_type": "equity",
        "exchange_code": "XNYS",
        "currency": "HKD",
        "identifiers": [],
        "quote_selection_policy": {"valuation": ["close"], "reference": ["close"]},
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": as_of_date,
                "value": "100.00",
                "currency": "HKD",
                "price_unit": "per_unit",
                "price_scale": 1.0,
                "status": "complete",
            }
            for as_of_date in ("2026-01-01", "2026-01-02")
        ],
    }


def _fx_detail(*, include_start_rate: bool = True) -> dict[str, object]:
    market_data = [
        {
            "metric_family": "fx",
            "quote_basis": "spot",
            "as_of_date": "2026-01-02",
            "value": "7.50",
            "currency": "HKD",
            "price_unit": "rate",
            "price_scale": 1.0,
            "status": "complete",
        }
    ]
    if include_start_rate:
        market_data.insert(
            0,
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-01-01",
                "value": "7.80",
                "currency": "HKD",
                "price_unit": "rate",
                "price_scale": 1.0,
                "status": "complete",
            },
        )
    return {
        "instrument_id": FX_INSTRUMENT_ID,
        "instrument_name": "USD/HKD",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": market_data,
    }


def _patch_market_data(monkeypatch: pytest.MonkeyPatch, *, include_start_rate: bool = True) -> None:
    details = {
        SECURITY_INSTRUMENT_ID: _security_detail(),
        FX_INSTRUMENT_ID: _fx_detail(include_start_rate=include_start_rate),
    }
    monkeypatch.setattr(
        performance,
        "get_registry_instrument_detail",
        lambda instrument_id: deepcopy(details.get(instrument_id)),
    )
    monkeypatch.setattr(
        performance,
        "get_platform_fx_rates",
        lambda: {
            "supported_currencies": ["USD", "HKD"],
            "maintained_pairs": ["USD/HKD"],
            "rates": [
                {
                    "base_currency": "USD",
                    "quote_currency": "HKD",
                    "rate": 7.5,
                    "as_of_date": "2026-01-02",
                    "source_kind": "direct",
                    "instrument_id": FX_INSTRUMENT_ID,
                    "source_instrument_ids": [FX_INSTRUMENT_ID],
                    "status": "complete",
                }
            ],
        },
    )


def test_unsettled_foreign_security_fx_is_not_absorbed_by_asset_capital_gain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_market_data(monkeypatch)
    expected_fx_change = (100.0 / 7.5) - (100.0 / 7.8)

    snapshots = performance.build_daily_portfolio_snapshots(
        _portfolio(),
        _accounts(),
        _transactions(),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        include_materialized_rows=True,
    )
    end_snapshot = snapshots[-1]
    settlement_payable = next(
        row
        for row in end_snapshot["_holding_rows"]
        if row["holding_kind"] == "settlement_payable"
    )

    assert end_snapshot["cash_currency_gains"] == pytest.approx(expected_fx_change)
    assert end_snapshot["instrument_currency_gains"] == pytest.approx(expected_fx_change)
    assert end_snapshot["pending_settlement_currency_gains"] == pytest.approx(
        -expected_fx_change
    )
    assert end_snapshot["total_pnl"] == pytest.approx(expected_fx_change)
    assert settlement_payable["settlement_date"] == "2026-01-03"
    assert settlement_payable["pending_until_date"] == "2026-01-03"
    assert settlement_payable["pending_status"] == "awaiting_settlement"
    assert settlement_payable["settlement_amount"] == pytest.approx(-100.0)
    assert settlement_payable["settlement_amount_base"] == pytest.approx(-100.0 / 7.5)

    calculation = performance.build_period_calculation_report(
        _portfolio(),
        _accounts(),
        _transactions(),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
    )
    summary = calculation["summary"]
    assert summary["delta"] == pytest.approx(expected_fx_change)
    assert summary["capital_gains"] == pytest.approx(0.0, abs=1e-12)
    assert summary["realized_capital_gains"] == pytest.approx(0.0, abs=1e-12)
    assert summary["unrealized_capital_gains"] == pytest.approx(0.0, abs=1e-12)
    assert summary["pending_settlement_currency_gains"] == pytest.approx(
        -expected_fx_change
    )
    line_by_key = {str(line["key"]): line for line in calculation["lines"]}
    assert line_by_key["pending_settlement_currency_gains"]["label"] == (
        "Pending Settlement FX"
    )
    assert line_by_key["pending_settlement_currency_gains"]["amount"] == pytest.approx(
        -expected_fx_change
    )

    grouped = performance.build_period_calculation_groups_report(
        _portfolio(),
        _accounts(),
        _transactions(),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        axis="account",
    )
    groups_by_key = {str(group["group_key"]): group for group in grouped["groups"]}
    cash_group = groups_by_key["cash-hkd"]
    assert cash_group["cash_currency_gains"] == pytest.approx(expected_fx_change)
    assert cash_group["pending_settlement_currency_gains"] == pytest.approx(
        -expected_fx_change
    )
    assert cash_group["capital_gains"] == pytest.approx(0.0, abs=1e-12)


def test_pending_settlement_fx_fails_closed_when_prior_fx_boundary_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_market_data(monkeypatch, include_start_rate=False)

    snapshots = performance.build_daily_portfolio_snapshots(
        _portfolio(),
        _accounts(),
        _transactions(),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
    )
    end_snapshot = snapshots[-1]

    assert end_snapshot["pending_settlement_currency_gains"] is None
    assert end_snapshot["total_pnl"] is None
    assert end_snapshot["book_pnl_coverage_state"] == "partial"
    assert end_snapshot["attribution_coverage_state"] == "partial"

    grouped = performance.build_period_calculation_groups_report(
        _portfolio(),
        _accounts(),
        _transactions(),
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        axis="account",
    )
    groups_by_key = {str(group["group_key"]): group for group in grouped["groups"]}
    cash_group = groups_by_key["cash-hkd"]
    assert cash_group["pending_settlement_currency_gains"] is None
    assert cash_group["total_pnl"] is None
    assert cash_group["capital_gains"] is None
