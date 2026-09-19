from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import attribution, performance, period_metrics


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
        "market_risk_observation_count": 1,
        "market_risk_return_coverage_state": "complete",
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
        "market_risk_excluded_pnl": 0.0,
        "market_risk_total_pnl": pnl,
        "market_risk_daily_return": pnl / beginning_value,
        "market_risk_daily_contribution": contribution,
        "market_risk_return_observation_eligible": True,
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
        (
            {
                "beginning_value_base": 100.0,
                "ending_value_base": 190.0,
                "total_pnl": -10.0,
                "capital_flow_in_base": 100.0,
                "capital_flow_out_base": 0.0,
                "capital_flow_in_eod_base": 100.0,
            },
            -0.1,
        ),
    ],
)
def test_daily_group_return_golden(
    values: dict[str, float], expected: float | None
) -> None:
    extracted = attribution.daily_group_return_from_components(**values)
    assert extracted == pytest.approx(expected) if expected is not None else extracted is None


def test_taxonomy_group_labels_use_full_paths_when_leaf_names_repeat() -> None:
    taxonomy = {"taxonomy_id": "allocation"}
    nodes = {
        "equity": {
            "taxonomy_node_id": "equity",
            "node_name": "Equity",
            "parent_taxonomy_node_id": None,
            "status": "active",
        },
        "alternatives": {
            "taxonomy_node_id": "alternatives",
            "node_name": "Alternatives",
            "parent_taxonomy_node_id": None,
            "status": "active",
        },
        "equity-growth": {
            "taxonomy_node_id": "equity-growth",
            "node_name": "Growth",
            "parent_taxonomy_node_id": "equity",
            "status": "active",
        },
        "alternatives-growth": {
            "taxonomy_node_id": "alternatives-growth",
            "node_name": "Growth",
            "parent_taxonomy_node_id": "alternatives",
            "status": "active",
        },
    }
    assignments = {
        "equity-fund": [
            {
                "taxonomy_node_id": "equity-growth",
                "status": "active",
            }
        ],
        "macro-fund": [
            {
                "taxonomy_node_id": "alternatives-growth",
                "status": "active",
            }
        ],
    }

    equity_key, equity_label = attribution.resolve_taxonomy_group(
        taxonomy=taxonomy,
        taxonomy_nodes_by_id=nodes,
        assignments_by_entity=assignments,
        target_entity_id="equity-fund",
    )
    alternatives_key, alternatives_label = attribution.resolve_taxonomy_group(
        taxonomy=taxonomy,
        taxonomy_nodes_by_id=nodes,
        assignments_by_entity=assignments,
        target_entity_id="macro-fund",
    )

    assert equity_key == "equity-growth"
    assert alternatives_key == "alternatives-growth"
    assert equity_label == "Equity / Growth"
    assert alternatives_label == "Alternatives / Growth"


def test_taxonomy_reducers_keep_cash_and_derivatives_as_system_groups() -> None:
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
        {
            **_complete_slice(
                as_of_date=date(2026, 1, 2),
                group_key="fcn-local-1",
                group_label="FCN Local 1",
                beginning_value=10.0,
                ending_value=11.0,
                pnl=1.0,
                contribution=0.01,
            ),
            "_system_holding_category": "derivatives",
        },
    ]
    kwargs = {
        "taxonomy": taxonomy,
        "taxonomy_nodes": nodes,
        "taxonomy_assignments": assignments,
        "base_daily_slices": base_slices,
    }
    extracted = attribution.group_contribution_slices_by_taxonomy(**kwargs)
    assert [item["group_key"] for item in extracted] == ["__cash__", "__derivatives__", "growth"]
    assert extracted[2]["daily_return"] == pytest.approx(0.05)

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
    assert {line["group_key"] for line in report["lines"]} == {
        "__cash__",
        "__derivatives__",
        "growth",
    }


def test_contribution_report_core_and_filter_golden_contract() -> None:
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


@pytest.mark.parametrize(
    ("start_day", "expected"),
    [(1, {"asset": -0.013, "cash": 0.003}),
     (2, {"asset": -0.08, "cash": -0.02}),
     (3, {"asset": 0.0, "cash": 0.0})],
)
def test_linked_contributions_reconcile_twr_and_reset_at_selected_close(
    start_day: int, expected: dict[str, float]
) -> None:
    # Deposit 100 on day 2, withdraw 50 on day 3. Linking uses the flow-neutral
    # operational return, including cash P&L on days without a market-risk return.
    snapshots = [
        {"as_of_date": date(2026, 1, day), "nav": nav, "ending_nav": nav,
         "beginning_nav": beginning, "daily_twr": daily_twr,
         "external_cash_in": cash_in, "external_cash_out": cash_out,
         "coverage_state": "complete", "market_risk_daily_return": 0.0}
        for day, beginning, nav, daily_twr, cash_in, cash_out in [
            (1, 80, 100, 0.25, 0, 0),
            (2, 100, 220, 0.1, 100, 0),
            (3, 220, 148, -0.1, 0, 50),
        ]
    ]
    slices = [
        _complete_slice(
            as_of_date=date(2026, 1, day), group_key=key, group_label=key,
            beginning_value=50, ending_value=50, pnl=contribution * denominator,
            contribution=contribution,
        )
        for day, denominator, asset_contribution, cash_contribution in [
            (1, 80, 0.2, 0.05), (2, 200, 0.075, 0.025), (3, 220, -0.08, -0.02)
        ]
        for key, contribution in [("asset", asset_contribution), ("cash", cash_contribution)]
    ]
    report = attribution.build_contribution_report_from_daily_slices_core(
        portfolio_id="linked", base_currency="USD", valuation_timezone="UTC",
        valuation_cutoff_policy="close", snapshots=snapshots, daily_slices=slices,
        start_date=date(2026, 1, start_day), end_date=date(2026, 1, 3),
        start_is_close_boundary=True,
    )
    linked = attribution.linked_return_contributions_by_group(
        report["daily_slices"], report["_portfolio_daily_series"]
    )
    assert linked == pytest.approx(expected)
    assert sum(linked.values()) == pytest.approx(report["summary"]["portfolio_cumulative_twr"])
    grouped = [{**item, "group_key": "all"} for item in report["daily_slices"]]
    assert attribution.linked_return_contributions_by_group(
        grouped, report["_portfolio_daily_series"]
    )["all"] == pytest.approx(sum(expected.values()))


@pytest.mark.parametrize("missing_field", ["daily_twr", "daily_contribution"])
def test_linked_contributions_do_not_zero_fill_unknown_returns(missing_field: str) -> None:
    points = [{"as_of_date": date(2026, 1, day), "daily_twr": 0.1} for day in (1, 2)]
    slices = [{"as_of_date": point["as_of_date"], "group_key": "asset",
               "daily_contribution": 0.1} for point in points]
    (points if missing_field == "daily_twr" else slices)[0][missing_field] = None
    assert attribution.linked_return_contributions_by_group(slices, points) == {"asset": None}


def test_linked_contributions_support_zero_period_twr() -> None:
    points = [{"as_of_date": date(2026, 1, day), "daily_twr": daily_twr}
              for day, daily_twr in [(1, 0.1), (2, -1 / 11)]]
    slices = [{"as_of_date": point["as_of_date"], "group_key": "asset",
               "daily_contribution": point["daily_twr"]} for point in points]
    assert attribution.linked_return_contributions_by_group(slices, points)["asset"] == pytest.approx(0.0)


def test_average_group_weights_zero_fill_dates_when_group_is_absent() -> None:
    snapshots = [
        {
            "as_of_date": as_of_date,
            "beginning_nav": 100.0,
            "ending_nav": 100.0,
            "daily_twr": 0.0,
            "return_observation_eligible": True,
            "market_observation_count": 1,
            "coverage_state": "complete",
        }
        for as_of_date in ("2026-01-02", "2026-01-03")
    ]
    slices = [
        _complete_slice(
            as_of_date=date(2026, 1, 2),
            group_key="asset-a",
            group_label="Asset A",
            beginning_value=60.0,
            ending_value=60.0,
            pnl=0.0,
            contribution=0.0,
        ),
        _complete_slice(
            as_of_date=date(2026, 1, 2),
            group_key="asset-b",
            group_label="Asset B",
            beginning_value=40.0,
            ending_value=40.0,
            pnl=0.0,
            contribution=0.0,
        ),
        _complete_slice(
            as_of_date=date(2026, 1, 3),
            group_key="asset-b",
            group_label="Asset B",
            beginning_value=100.0,
            ending_value=100.0,
            pnl=0.0,
            contribution=0.0,
        ),
    ]
    report = attribution.build_contribution_report_from_daily_slices_core(
        portfolio_id="average-weight-test",
        base_currency="USD",
        valuation_timezone="Asia/Shanghai",
        valuation_cutoff_policy="latest_complete_eod",
        snapshots=snapshots,
        daily_slices=slices,
    )
    lines = {
        str(line["group_key"]): line for line in list(report.get("lines") or [])
    }
    assert lines["asset-a"]["average_weight"] == pytest.approx(0.30)
    assert lines["asset-b"]["average_weight"] == pytest.approx(0.70)
    assert sum(float(line["average_weight"]) for line in lines.values()) == (
        pytest.approx(1.0)
    )


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


def test_period_group_return_fails_closed_on_active_incomplete_row() -> None:
    contracts = attribution.period_return_contracts_by_group(
        [
            {
                "group_key": "asset-1",
                "coverage_state": "complete",
                "beginning_value_base": 100.0,
                "ending_value_base": 110.0,
                "capital_flow_in_base": 0.0,
                "capital_flow_out_base": 0.0,
                "daily_return": 0.10,
            },
            {
                "group_key": "asset-1",
                "coverage_state": "partial",
                "beginning_value_base": 110.0,
                "ending_value_base": None,
                "capital_flow_in_base": 0.0,
                "capital_flow_out_base": 0.0,
                "daily_return": None,
            },
        ]
    )
    assert contracts["asset-1"]["period_return"] is None
    assert contracts["asset-1"]["coverage_state"] == "partial"
    assert contracts["asset-1"]["observation_count"] == 1


def test_realized_risk_attribution_golden_contract() -> None:
    calculation_frequency = "daily"
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
            "market_risk_daily_return": portfolio_return,
            "market_risk_return_observation_eligible": True,
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
            "market_risk_daily_return": contribution,
            "market_risk_daily_contribution": contribution,
            "market_risk_return_observation_eligible": True,
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


def test_realized_risk_attribution_does_not_report_portfolio_observations_for_cash() -> None:
    portfolio_daily_series = [
        {
            "as_of_date": as_of_date,
            "market_risk_daily_return": portfolio_return,
            "market_risk_return_observation_eligible": True,
        }
        for as_of_date, portfolio_return in (
            (date(2026, 1, 5), 0.01),
            (date(2026, 1, 6), -0.01),
            (date(2026, 1, 7), 0.02),
        )
    ]
    cash_slices = [
        {
            "as_of_date": point["as_of_date"],
            "group_key": "cash",
            "daily_return": 0.0,
            "daily_contribution": 0.0,
            "return_observation_eligible": True,
            "market_risk_daily_return": 0.0,
            "market_risk_daily_contribution": 0.0,
            "market_risk_return_observation_eligible": False,
        }
        for point in portfolio_daily_series
    ]

    metrics = attribution.realized_risk_attribution_by_group(
        cash_slices,
        portfolio_daily_series,
        calculation_frequency="daily",
        final_date=date(2026, 1, 7),
    )

    assert metrics["cash"]["risk_return_observation_count"] == 0
    assert metrics["cash"]["annualized_volatility"] is None
    assert metrics["cash"]["correlation_to_portfolio"] is None
    assert metrics["cash"]["realized_risk_contribution"] == 0


def test_unknown_active_contribution_excludes_common_day_without_hiding_own_risk():
    dates = [date(2026, 1, day) for day in (5, 6, 7)]
    portfolio = [
        {"as_of_date": day, "market_risk_daily_return": value,
         "market_risk_return_observation_eligible": True}
        for day, value in zip(dates, [0.01, 0.20, -0.01])
    ]
    slices = [
        {"as_of_date": point["as_of_date"], "group_key": group,
         "market_risk_daily_return": point["market_risk_daily_return"],
         "market_risk_daily_contribution": point["market_risk_daily_return"] * weight,
         "market_risk_return_observation_eligible": True}
        for point in portfolio for group, weight in [("a", 0.6), ("b", 0.4)]
    ]
    slices[3].update(market_risk_daily_return=None,
                     market_risk_daily_contribution=None,
                     market_risk_return_observation_eligible=False)
    metrics = attribution.realized_risk_attribution_by_group(
        slices, portfolio, calculation_frequency="daily", final_date=dates[-1],
    )
    assert metrics["a"]["risk_return_observation_count"] == 3
    assert metrics["b"]["risk_return_observation_count"] == 2
    assert metrics["a"]["annualized_volatility"] is not None
    assert metrics["b"]["correlation_to_portfolio"] == pytest.approx(1)
    assert metrics["a"]["realized_risk_contribution"] == pytest.approx(0.6)
    assert metrics["b"]["realized_risk_contribution"] == pytest.approx(0.4)


def test_realized_risk_attribution_keeps_non_base_cash_fx_contribution() -> None:
    daily_rows = [
        (date(2026, 1, 5), 0.01),
        (date(2026, 1, 6), -0.02),
        (date(2026, 1, 7), 0.03),
    ]
    portfolio_daily_series = [
        {
            "as_of_date": as_of_date,
            "market_risk_daily_return": daily_return,
            "market_risk_return_observation_eligible": True,
        }
        for as_of_date, daily_return in daily_rows
    ]
    foreign_cash_slices = [
        {
            "as_of_date": as_of_date,
            "group_key": "cash",
            "daily_return": daily_return,
            "daily_contribution": daily_return,
            "return_observation_eligible": True,
            "market_risk_daily_return": daily_return,
            "market_risk_daily_contribution": daily_return,
            "market_risk_return_observation_eligible": True,
        }
        for as_of_date, daily_return in daily_rows
    ]

    metrics = attribution.realized_risk_attribution_by_group(
        foreign_cash_slices,
        portfolio_daily_series,
        calculation_frequency="daily",
        final_date=date(2026, 1, 7),
    )

    assert metrics["cash"]["risk_return_observation_count"] == 3
    assert metrics["cash"]["realized_risk_contribution"] == pytest.approx(1.0)


def test_group_risk_annualization_uses_each_groups_actual_exposure_span() -> None:
    portfolio_daily_series = [
        {
            "as_of_date": as_of_date,
            "market_risk_daily_return": daily_return,
            "market_risk_return_observation_eligible": True,
        }
        for as_of_date, daily_return in (
            (date(2026, 1, 5), 0.01),
            (date(2026, 1, 9), -0.01),
            (date(2026, 1, 10), 0.02),
        )
    ]
    daily_slices: list[dict[str, object]] = []
    for as_of_date in [
        date(2026, 1, day) for day in range(2, 11)
    ]:
        eligible = as_of_date in {date(2026, 1, 5), date(2026, 1, 10)}
        daily_slices.append(
            {
                "as_of_date": as_of_date,
                "group_key": "full-window-asset",
                "beginning_value_base": 100.0,
                "ending_value_base": 100.0,
                "capital_flow_in_base": 0.0,
                "capital_flow_out_base": 0.0,
                "daily_return": 0.01 if eligible else 0.0,
                "daily_contribution": 0.005 if eligible else 0.0,
                "return_observation_eligible": eligible,
                "market_risk_daily_return": 0.01 if eligible else 0.0,
                "market_risk_daily_contribution": 0.005 if eligible else 0.0,
                "market_risk_return_observation_eligible": eligible,
            }
        )
    for as_of_date in [
        date(2026, 1, day) for day in range(8, 11)
    ]:
        eligible = as_of_date in {date(2026, 1, 9), date(2026, 1, 10)}
        daily_slices.append(
            {
                "as_of_date": as_of_date,
                "group_key": "late-entry-asset",
                "beginning_value_base": 50.0,
                "ending_value_base": 50.0,
                "capital_flow_in_base": 0.0,
                "capital_flow_out_base": 0.0,
                "daily_return": 0.01 if eligible else 0.0,
                "daily_contribution": 0.005 if eligible else 0.0,
                "return_observation_eligible": eligible,
                "market_risk_daily_return": 0.01 if eligible else 0.0,
                "market_risk_daily_contribution": 0.005 if eligible else 0.0,
                "market_risk_return_observation_eligible": eligible,
            }
        )

    metrics = attribution.realized_risk_attribution_by_group(
        daily_slices,
        portfolio_daily_series,
        calculation_frequency="daily",
        start_date=date(2026, 1, 1),
        final_date=date(2026, 1, 10),
    )

    assert metrics["full-window-asset"][
        "risk_annualization_periods_per_year"
    ] == pytest.approx(2 / 9 * period_metrics.DAYS_PER_YEAR)
    assert metrics["late-entry-asset"][
        "risk_annualization_periods_per_year"
    ] == pytest.approx(2 / 3 * period_metrics.DAYS_PER_YEAR)


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
        "start_is_close_boundary": False,
    }


def test_current_assignment_restates_every_slice_without_changing_financial_dates():
    taxonomy = {"taxonomy_id": "strategy"}
    nodes = [
        {"taxonomy_id": "strategy", "taxonomy_node_id": node, "node_name": node, "status": "active"}
        for node in ("growth", "defensive")
    ]
    slices = [
        _complete_slice(as_of_date=date(2024, 1, day), group_key="asset", group_label="Asset",
                        beginning_value=100.0, ending_value=100.0 + day, pnl=float(day), contribution=day / 100.0)
        for day in (2, 3)
    ]
    original = deepcopy(slices)
    assignment = {"assignment_id": "assignment", "taxonomy_id": "strategy", "target_scope": "instrument",
                  "target_entity_id": "asset", "taxonomy_node_id": "growth", "status": "active"}
    saved = attribution.group_contribution_slices_by_taxonomy(
        taxonomy=taxonomy, taxonomy_nodes=nodes, taxonomy_assignments=[assignment], base_daily_slices=slices,
    )
    restated = attribution.group_contribution_slices_by_taxonomy(
        taxonomy=taxonomy, taxonomy_nodes=nodes,
        taxonomy_assignments=[{**assignment, "taxonomy_node_id": "defensive"}], base_daily_slices=slices,
    )
    assert slices == original
    assert {row["group_key"] for row in saved} == {"growth"}
    assert {row["group_key"] for row in restated} == {"defensive"}
    fields = ("as_of_date", "beginning_value_base", "ending_value_base", "total_pnl", "daily_contribution")
    assert [tuple(row[field] for field in fields) for row in saved] == [
        tuple(row[field] for field in fields) for row in restated
    ]
