from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import attribution, performance


def _complete_slice(
    *,
    as_of_date: date,
    group_key: str,
    group_label: str,
    beginning_value: float,
    ending_value: float,
    pnl: float,
    contribution: float,
) -> dict[str, object]:
    return {
        "as_of_date": as_of_date,
        "axis": "instrument",
        "group_key": group_key,
        "group_label": group_label,
        "coverage_state": "complete",
        "market_observation_count": 1,
        "beginning_value_base": beginning_value,
        "ending_value_base": ending_value,
        "beginning_weight": beginning_value / 100.0,
        "ending_weight": ending_value / 100.0,
        "cash_balance_base": 0.0,
        "position_market_value_base": ending_value,
        "open_cost_basis_base": beginning_value,
        "realized_pnl": 0.0,
        "unrealized_pnl": pnl,
        "unrealized_pnl_change": pnl,
        "income_cash_amount": 0.0,
        "expense_cash_amount": 0.0,
        "fee_amount": 0.0,
        "tax_amount": 0.0,
        "cash_currency_gains": 0.0,
        "pending_settlement_currency_gains": 0.0,
        "instrument_currency_gains": 0.0,
        "capital_flow_in_base": 0.0,
        "capital_flow_out_base": 0.0,
        "total_pnl": pnl,
        "daily_return": pnl / beginning_value,
        "daily_contribution": contribution,
        "return_observation_eligible": True,
    }


def test_attribution_grouping_helpers_golden_contract() -> None:
    account_names = {"broker-1": "Prime Broker"}
    position_lot = {
        "account_id": "broker-1",
        "instrument_id": "asset-1",
        "currency": "hkd",
        "instrument_ref": {
            "instrument_name": "Asia Fund",
            "instrument_type": "Private Fund",
        },
    }
    transaction = {
        "account_id": "broker-1",
        "instrument_id": "asset-1",
        "currency": "hkd",
        "instrument_ref": {
            "instrument_id": "asset-1",
            "instrument_name": "Asia Fund",
            "instrument_type": "Private Fund",
        },
    }
    cash_transaction = {"account_id": "broker-1", "currency": "hkd"}

    assert attribution.calculation_detail_axis("instrument") == "instrument_detail"
    encoded_key = attribution.encode_calculation_detail_group_key(
        parent_group_key="asset-1",
        item_kind="cash",
        item_key="cash:broker-1:HKD",
    )
    assert attribution.decode_calculation_detail_group_key(encoded_key) == (
        "asset-1",
        "cash",
        "cash:broker-1:HKD",
    )
    assert attribution.decode_calculation_detail_group_key("bad") is None

    for axis in (
        "instrument",
        "account",
        "instrument_type",
        "currency",
        "instrument_detail",
        "account_detail",
        "instrument_type_detail",
        "currency_detail",
    ):
        kwargs = {
            "axis": axis,
            "position_lot": position_lot,
            "account_name_map": account_names,
            "base_currency": "USD",
        }
        group_key, group_label = attribution.position_group_for_axis(**kwargs)
        assert group_key
        assert group_label

    for axis in (
        "instrument",
        "account",
        "instrument_type",
        "currency",
        "cash_detail",
        "instrument_detail",
        "account_detail",
        "instrument_type_detail",
        "currency_detail",
    ):
        cash_kwargs = {
            "axis": axis,
            "account_id": "broker-1",
            "currency": "HKD",
            "account_name_map": account_names,
        }
        cash_group = attribution.cash_group_for_axis(**cash_kwargs)
        assert len(cash_group) == 2
        for candidate in (transaction, cash_transaction):
            transaction_kwargs = {
                "axis": axis,
                "transaction": candidate,
                "account_name_map": account_names,
                "base_currency": "USD",
            }
            transaction_group = attribution.transaction_group_for_axis(
                **transaction_kwargs
            )
            assert len(transaction_group) == 2

    assert attribution.cash_bucket_account_ids(
        [
            {"account_id": "bank", "account_type": "deposit_account"},
            {"account_id": "broker", "account_type": "brokerage"},
        ]
    ) == {"bank"}


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            {
                "beginning_value_base": 100.0,
                "ending_value_base": 110.0,
                "total_pnl": 10.0,
                "capital_flow_in_base": 0.0,
                "capital_flow_out_base": 0.0,
            },
            0.1,
        ),
        (
            {
                "beginning_value_base": 100.0,
                "ending_value_base": 160.0,
                "total_pnl": 10.0,
                "capital_flow_in_base": 20.0,
                "capital_flow_out_base": 0.0,
            },
            10.0 / 150.0,
        ),
        (
            {
                "beginning_value_base": 0.0,
                "ending_value_base": 0.0,
                "total_pnl": 0.0,
                "capital_flow_in_base": 0.0,
                "capital_flow_out_base": 0.0,
            },
            None,
        ),
    ],
)
def test_daily_group_return_golden(
    values: dict[str, float], expected: float | None
) -> None:
    extracted = attribution.daily_group_return_from_components(**values)
    assert extracted == pytest.approx(expected) if expected is not None else extracted is None


def test_taxonomy_reducers_preserve_cash() -> None:
    taxonomy = {
        "taxonomy_id": "strategy",
        "primary_assignment_scope": "instrument",
    }
    nodes = [
        {
            "taxonomy_id": "strategy",
            "taxonomy_node_id": "growth",
            "node_name": "Growth",
            "status": "active",
        }
    ]
    assignments = [
        {
            "assignment_id": "assignment-1",
            "taxonomy_id": "strategy",
            "taxonomy_node_id": "growth",
            "target_scope": "instrument",
            "target_entity_id": "asset-1",
            "status": "active",
        }
    ]
    base_slices = [
        _complete_slice(
            as_of_date=date(2026, 1, 2),
            group_key="asset-1",
            group_label="Asia Fund",
            beginning_value=80.0,
            ending_value=84.0,
            pnl=4.0,
            contribution=0.04,
        ),
        _complete_slice(
            as_of_date=date(2026, 1, 2),
            group_key="cash",
            group_label="Cash",
            beginning_value=20.0,
            ending_value=20.0,
            pnl=0.0,
            contribution=0.0,
        ),
    ]
    kwargs = {
        "taxonomy": taxonomy,
        "taxonomy_nodes": nodes,
        "taxonomy_assignments": assignments,
        "base_daily_slices": base_slices,
        "preserve_cash_group": True,
    }
    extracted = attribution.group_contribution_slices_by_taxonomy(**kwargs)
    assert [item["group_key"] for item in extracted] == ["cash", "growth"]
    assert extracted[1]["daily_return"] == pytest.approx(0.05)

    base_report = {
        "portfolio_id": "portfolio-1",
        "base_currency": "USD",
        "valuation_timezone": "Asia/Hong_Kong",
        "valuation_cutoff_policy": "latest_complete_eod",
        "summary": {
            "axis": "instrument",
            "start_date": date(2026, 1, 2),
            "end_date": date(2026, 1, 2),
            "coverage_state": "complete",
            "start_nav": 100.0,
            "end_nav": 104.0,
            "portfolio_arithmetic_return": 0.04,
        },
        "lines": [],
        "daily_slices": base_slices,
        "_portfolio_daily_series": [],
    }
    report_kwargs = {
        "taxonomy": taxonomy,
        "base_report": base_report,
        "grouped_daily_slices": extracted,
    }
    report = attribution.build_taxonomy_contribution_report(**report_kwargs)
    assert report["summary"]["axis"] == "taxonomy"
    assert {line["group_key"] for line in report["lines"]} == {"cash", "growth"}


def test_contribution_report_core_and_filter_golden_contract() -> None:
    portfolio = {
        "portfolio_id": "portfolio-1",
        "base_currency": "hkd",
        "valuation_timezone": "Asia/Hong_Kong",
        "valuation_cutoff_policy": "latest_complete_eod",
    }
    snapshots = [
        {
            "as_of_date": "2026-01-02",
            "beginning_nav": 100.0,
            "ending_nav": 104.0,
            "daily_twr": 0.04,
            "return_observation_eligible": True,
            "market_observation_count": 1,
            "coverage_state": "complete",
        }
    ]
    slices = [
        _complete_slice(
            as_of_date=date(2026, 1, 2),
            group_key="asset-1",
            group_label="Asia Fund",
            beginning_value=100.0,
            ending_value=104.0,
            pnl=4.0,
            contribution=0.04,
        )
    ]
    extracted = attribution.build_contribution_report_from_daily_slices_core(
        portfolio_id="portfolio-1",
        base_currency="HKD",
        valuation_timezone="Asia/Hong_Kong",
        valuation_cutoff_policy="latest_complete_eod",
        snapshots=snapshots,
        daily_slices=slices,
        group_key="asset-1",
    )
    assert extracted["summary"]["portfolio_cumulative_twr"] == pytest.approx(0.04)
    assert extracted["summary"]["contribution_residual"] == pytest.approx(0.0)

    unfiltered = deepcopy(extracted)
    assert attribution.filter_contribution_report_by_group_key(
        unfiltered, group_key=None
    ) is unfiltered
    filtered = attribution.filter_contribution_report_by_group_key(
        unfiltered, group_key="missing"
    )
    assert filtered is not unfiltered
    assert filtered["lines"] == []
    assert unfiltered["lines"]


def test_detail_merge_and_period_returns_golden_contract() -> None:
    first = _complete_slice(
        as_of_date=date(2026, 1, 2),
        group_key="asset-1",
        group_label="Asia Fund",
        beginning_value=60.0,
        ending_value=63.0,
        pnl=3.0,
        contribution=0.03,
    )
    second = _complete_slice(
        as_of_date=date(2026, 1, 2),
        group_key="asset-1",
        group_label="Asia Fund",
        beginning_value=40.0,
        ending_value=41.0,
        pnl=1.0,
        contribution=0.01,
    )
    slices = [first, second]
    merged = attribution.merge_calculation_detail_daily_slices(slices)
    assert merged[0]["beginning_value_base"] == pytest.approx(100.0)
    assert merged[0]["daily_return"] == pytest.approx(0.04)

    return_slices = [
        {"group_key": "asset-1", "daily_return": 0.1},
        {"group_key": "asset-1", "daily_return": -0.05},
        {"group_key": "asset-2", "daily_return": None},
    ]
    extracted_returns = attribution.period_returns_by_group(return_slices)
    assert extracted_returns == {"asset-1": pytest.approx(0.045)}


@pytest.mark.parametrize("calculation_frequency", ["daily", "weekly"])
def test_realized_risk_attribution_golden_contract(
    calculation_frequency: str,
) -> None:
    daily_rows = [
        (date(2026, 1, 5), 0.10, 0.06, 0.04),
        (date(2026, 1, 6), 0.10, 0.04, 0.06),
        (date(2026, 1, 12), -0.05, -0.03, -0.02),
        (date(2026, 1, 13), 0.02, 0.01, 0.01),
        (date(2026, 1, 19), 0.03, 0.02, 0.01),
        (date(2026, 1, 20), 0.04, 0.02, 0.02),
    ]
    portfolio_daily_series = [
        {
            "as_of_date": as_of_date,
            "daily_twr": portfolio_return,
            "return_observation_eligible": True,
        }
        for as_of_date, portfolio_return, _a, _b in daily_rows
    ]
    daily_slices = [
        {
            "as_of_date": as_of_date,
            "group_key": group_key,
            "daily_return": contribution,
            "daily_contribution": contribution,
            "return_observation_eligible": True,
        }
        for as_of_date, _portfolio_return, a_contribution, b_contribution in daily_rows
        for group_key, contribution in (
            ("asset-a", a_contribution),
            ("asset-b", b_contribution),
        )
    ]
    kwargs = {
        "calculation_frequency": calculation_frequency,
        "final_date": date(2026, 1, 20),
    }
    extracted = attribution.realized_risk_attribution_by_group(
        daily_slices,
        portfolio_daily_series,
        **kwargs,
    )
    assert sum(
        float(item["realized_risk_contribution"] or 0.0)
        for item in extracted.values()
    ) == pytest.approx(1.0)
    summary = attribution.portfolio_realized_risk_summary(
        portfolio_daily_series, **kwargs
    )
    assert summary["risk_calculation_frequency"] == calculation_frequency


def test_performance_orchestrator_keeps_portfolio_metadata_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_core(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"delegated": True}

    monkeypatch.setattr(
        attribution,
        "build_contribution_report_from_daily_slices_core",
        fake_core,
    )
    result = performance.build_contribution_report_from_daily_slices(
        {
            "portfolio_id": "portfolio-1",
            "base_currency": "hkd",
            "valuation_timezone": "Asia/Hong_Kong",
            "valuation_cutoff_policy": "latest_complete_eod",
        },
        [],
        [],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        axis="account",
        group_key="broker-1",
    )
    assert result == {"delegated": True}
    assert captured == {
        "portfolio_id": "portfolio-1",
        "base_currency": "HKD",
        "valuation_timezone": "Asia/Hong_Kong",
        "valuation_cutoff_policy": "latest_complete_eod",
        "snapshots": [],
        "daily_slices": [],
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 1, 2),
        "axis": "account",
        "group_key": "broker-1",
    }
