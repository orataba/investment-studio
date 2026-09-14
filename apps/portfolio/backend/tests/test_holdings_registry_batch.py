from __future__ import annotations

from copy import deepcopy
from datetime import date
from types import SimpleNamespace

from portfolio_app.api.routes import workspace as workspace_routes
from portfolio_app.db.models import (
    DerivativeContractRecordModel,
    PortfolioCalculationStateModel,
    PortfolioDailyHoldingSnapshotModel,
    PortfolioDailySnapshotModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import (
    daily_snapshots,
    instrument_charts,
    instrument_registry,
    ledger,
    performance,
)
from portfolio_app.services.instrument_registry import get_registry_instrument_details


def _noncash_instrument_ids(payload: dict[str, object]) -> set[str]:
    return {
        str(instrument_core.get("instrument_id") or "")
        for row in payload.get("rows", [])
        if isinstance(row, dict)
        and isinstance((instrument_core := row.get("instrument_core")), dict)
        and str(instrument_core.get("instrument_type") or "") != "cash"
        and str(instrument_core.get("instrument_id") or "")
    }


def test_materialized_return_history_gets_coverage_without_rewriting_published_values() -> None:
    workspace = {"rows": [{
        "instrument_core": {"instrument_id": "fund"},
        "instrument_return_series_all": {
            "first_return_start_date": "2026-06-23",
            "points": [
                {"start_date": "2026-06-23", "date": "2026-06-25", "value": 0.01},
                {"start_date": "2026-06-25", "date": "2026-06-26", "value": 0.02},
            ],
        },
    }]}
    response = workspace_routes._materialized_holdings_workspace_response(
        workspace, risk_basis_profile={"resolved_frequency": "daily"},
        instrument_details={"fund": {"source_settings": {
            "expected_frequency": "daily", "market_calendar": "XSHG",
        }}},
    )
    old = workspace["rows"][0]["instrument_return_series_all"]
    series = response["rows"][0]["instrument_return_series_all"]
    assert series["observation_coverage"]["gap_dates"] == ["2026-06-24"]
    assert series["points"] == old["points"]
    assert "observation_coverage" not in old


def _populated_market_profile() -> dict[str, object]:
    profile = instrument_charts.empty_instrument_holdings_market_profile()
    points = [
        {"date": "2026-04-14", "value": 99.0},
        {"date": "2026-04-15", "value": 100.0},
    ]
    for range_key in instrument_charts.HOLDINGS_PRICE_CHART_RANGE_KEYS:
        profile[f"price_chart_{range_key}"] = deepcopy(points)
    profile.update(
        {
            "instrument_trend_as_of_date": "2026-04-15",
            "instrument_trend_basis": "adjusted_close",
            "instrument_trend_coverage": {
                "state": "complete",
                "observation_count": 2,
                "start_date": "2026-04-14",
                "end_date": "2026-04-15",
                "available_return_windows": ["1w"],
            },
            "instrument_return_1w": 0.01,
            "instrument_return_1m": 0.02,
            "instrument_return_3m": 0.03,
            "instrument_return_6m": 0.04,
            "instrument_return_mtd": 0.05,
            "instrument_return_ytd": 0.06,
            "instrument_return_1y": 0.07,
            "instrument_volatility_1m": 0.08,
            "instrument_volatility_3m": 0.09,
            "instrument_volatility_6m": 0.10,
            "instrument_volatility_1y": 0.11,
            "instrument_current_drawdown": -0.01,
            "instrument_max_drawdown": -0.02,
            "instrument_holding_max_drawdown": -0.03,
        }
    )
    for field_name in (
        "instrument_return_series_1m",
        "instrument_return_series_3m",
        "instrument_return_series_6m",
        "instrument_return_series_1y",
        "instrument_return_series_all",
        "instrument_holding_return_series",
    ):
        profile[field_name] = {
            "first_return_start_date": "2026-04-14",
            "points": [
                {
                    "start_date": "2026-04-14",
                    "date": "2026-04-15",
                    "value": 0.01,
                }
            ],
        }
    return profile


def _assert_empty_market_profile(row: dict[str, object]) -> None:
    for range_key in instrument_charts.HOLDINGS_PRICE_CHART_RANGE_KEYS:
        assert row[f"price_chart_{range_key}"] == []
    assert row["instrument_trend_as_of_date"] is None
    assert row["instrument_trend_basis"] is None
    assert row["instrument_trend_coverage"] == {
        "state": "unavailable",
        "observation_count": 0,
        "start_date": None,
        "end_date": None,
        "available_return_windows": [],
    }
    for field_name in (
        "instrument_return_1w",
        "instrument_return_1m",
        "instrument_return_3m",
        "instrument_return_6m",
        "instrument_return_mtd",
        "instrument_return_ytd",
        "instrument_return_1y",
        "instrument_volatility_1m",
        "instrument_volatility_3m",
        "instrument_volatility_6m",
        "instrument_volatility_1y",
        "instrument_current_drawdown",
        "instrument_max_drawdown",
        "instrument_holding_max_drawdown",
    ):
        assert row[field_name] is None
    for field_name in (
        "instrument_return_series_1m",
        "instrument_return_series_3m",
        "instrument_return_series_6m",
        "instrument_return_series_1y",
        "instrument_return_series_all",
        "instrument_holding_return_series",
    ):
        assert row[field_name]["points"] == []


def test_registry_batch_loads_full_details_and_marks_missing() -> None:
    details = get_registry_instrument_details(
        ["equity-us-abbv", "fund-us-agg", "equity-us-abbv", "missing-instrument", ""]
    )

    assert list(details) == ["equity-us-abbv", "fund-us-agg", "missing-instrument"]
    assert details["missing-instrument"] is None
    for instrument_id in ("equity-us-abbv", "fund-us-agg"):
        full_detail = instrument_registry.get_registry_instrument_detail(instrument_id)
        assert full_detail is not None
        assert details[instrument_id] is not None
        assert details[instrument_id]["market_data"] == full_detail["market_data"]
        assert len({point["as_of_date"] for point in full_detail["market_data"]}) > 1


def test_registry_batch_skips_fund_nav_audit_ledger(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def load_details(session_factory, instrument_ids, *, include_fund_nav_ledger):
        captured.update(
            {
                "session_factory": session_factory,
                "instrument_ids": list(instrument_ids),
                "include_fund_nav_ledger": include_fund_nav_ledger,
            }
        )
        return {"fund-us-agg": {"instrument_id": "fund-us-agg"}}

    monkeypatch.setattr(instrument_registry, "get_session_factory", lambda: "session-factory")
    monkeypatch.setattr(instrument_registry.shared_store, "get_instrument_details", load_details)

    result = instrument_registry.get_registry_instrument_details(["fund-us-agg"])

    assert result == {"fund-us-agg": {"instrument_id": "fund-us-agg"}}
    assert captured == {
        "session_factory": "session-factory",
        "instrument_ids": ["fund-us-agg"],
        "include_fund_nav_ledger": False,
    }


def test_public_holdings_response_uses_canonical_instrument_core_ids(
    monkeypatch,
) -> None:
    warning_calls: list[tuple[set[str], set[str]]] = []

    def capture_warnings(instrument_types, instrument_ids, **_kwargs):
        warning_calls.append((set(instrument_types), set(instrument_ids)))
        return []

    monkeypatch.setattr(
        workspace_routes,
        "corporate_action_quality_warnings",
        capture_warnings,
    )
    monkeypatch.setattr(
        workspace_routes,
        "instrument_event_task_quality_warnings",
        lambda _portfolio_id: [],
    )

    payload = workspace_routes._public_holdings_workspace_response(
        {
            "portfolio_id": "portfolio-1",
            "rows": [
                {
                    "line_id": "equity-1",
                    "instrument_core": {
                        "instrument_id": "equity-1",
                        "instrument_type": "equity",
                    },
                    "price_chart_1m": [],
                    "price_chart_3m": [],
                    "price_chart_6m": [],
                    "price_chart_1y": [],
                },
                {
                    "line_id": "cash:CNY",
                    "instrument_core": {
                        "instrument_id": "cash:CNY",
                        "instrument_type": "cash",
                    },
                    "price_chart_1m": [],
                    "price_chart_3m": [],
                    "price_chart_6m": [],
                    "price_chart_1y": [],
                },
            ],
        },
        include_details=False,
        transactions=[],
        as_of_date=date(2026, 7, 24),
    )

    assert payload["detail_level"] == "compact"
    assert warning_calls == [({"equity", "cash"}, {"equity-1"})]


def test_snapshot_holding_aggregation_preserves_accounts_and_earliest_holding_profile() -> None:
    def snapshot_row(
        *,
        account_id: str,
        holding_start_date: str,
        holding_max_drawdown: float,
        historical_cost_basis: float,
        fx_coverage_status: str,
    ) -> SimpleNamespace:
        holding_series = {
            "first_return_start_date": holding_start_date,
            "points": [
                {
                    "start_date": holding_start_date,
                    "date": "2026-07-24",
                    "value": holding_max_drawdown,
                }
            ],
        }
        return SimpleNamespace(
            as_of_date=date(2026, 7, 24),
            position_reference_id="fund-1",
            instrument_id="fund-1",
            derivative_contract_id=None,
            holding_kind="position",
            holding_json={
                "account_id": account_id,
                "account_ids": [account_id],
                "instrument_id": "fund-1",
                "instrument_ref": {
                    "instrument_id": "fund-1",
                    "instrument_name": "Fund 1",
                    "instrument_type": "public_fund",
                    "currency": "CNY",
                    "identifiers": [],
                },
                "quantity": 1,
                "last_price": 100,
                "market_value": 100,
                "market_value_base": 100,
                "day_change_pct": 0,
                "day_change_value": 0,
                "day_change_value_base": 0,
                "cost_basis_method": "fifo",
                "cost_basis": 90,
                "cost_basis_base": 90,
                "cost_basis_historical_base": historical_cost_basis,
                "cost_basis_current_fx_rate_to_base": 1,
                "cost_basis_fx_rate_to_base": historical_cost_basis / 90,
                "cost_basis_fx_coverage_status": fx_coverage_status,
                "unrealized_price_pnl": 10,
                "unrealized_price_pnl_base": 10,
                "unrealized_fx_pnl_base": 90 - historical_cost_basis,
                "unrealized_pnl_base": 100 - historical_cost_basis,
                "open_position_lot_count": 1,
                "instrument_holding_start_date": holding_start_date,
                "instrument_holding_return_series": holding_series,
                "instrument_holding_max_drawdown": holding_max_drawdown,
                "coverage_status": "price-nav-fx",
            },
        )

    rows = [
        snapshot_row(
            account_id="account-a",
            holding_start_date="2026-07-20",
            holding_max_drawdown=-0.01,
            historical_cost_basis=80,
            fx_coverage_status="complete",
        ),
        snapshot_row(
            account_id="account-b",
            holding_start_date="2026-06-01",
            holding_max_drawdown=-0.20,
            historical_cost_basis=85,
            fx_coverage_status="stale",
        ),
    ]

    aggregated = daily_snapshots._aggregate_holding_rows(
        rows,  # type: ignore[arg-type]
        total_nav_base=200,
    )

    assert len(aggregated) == 1
    assert aggregated[0]["account_ids"] == ["account-a", "account-b"]
    assert aggregated[0]["account_count"] == 2
    assert aggregated[0]["instrument_holding_start_date"] == "2026-06-01"
    assert aggregated[0]["instrument_holding_max_drawdown"] == -0.20
    assert aggregated[0]["cost_basis_historical_base"] == 165
    assert aggregated[0]["cost_basis_current_fx_rate_to_base"] == 1
    assert aggregated[0]["cost_basis_fx_rate_to_base"] == 165 / 180
    assert aggregated[0]["cost_basis_fx_coverage_status"] == "stale"
    assert aggregated[0]["unrealized_price_pnl_base"] == 20
    assert aggregated[0]["unrealized_fx_pnl_base"] == 15
    assert aggregated[0]["unrealized_pnl_base"] == 35
    assert aggregated[0]["instrument_holding_return_series"] == {
        "first_return_start_date": "2026-06-01",
        "points": [
            {
                "start_date": "2026-06-01",
                "date": "2026-07-24",
                "value": -0.20,
            }
        ],
    }

    rows[1].holding_json["cost_basis_historical_base"] = None
    rows[1].holding_json["cost_basis_fx_coverage_status"] = "unavailable"
    incomplete = daily_snapshots._aggregate_holding_rows(
        rows,  # type: ignore[arg-type]
        total_nav_base=200,
    )
    assert incomplete[0]["cost_basis_historical_base"] is None
    assert incomplete[0]["cost_basis_fx_coverage_status"] == "unavailable"


def test_materialized_holdings_uses_one_bulk_detail_map(client, monkeypatch) -> None:
    snapshot_response = client.get("/api/portfolios/investment-studio/snapshots/daily")
    assert snapshot_response.status_code == 200

    original_bulk_loader = workspace_routes.get_registry_instrument_details
    bulk_calls: list[tuple[str, ...]] = []

    def recording_bulk_loader(instrument_ids):
        bulk_calls.append(tuple(instrument_ids))
        return original_bulk_loader(instrument_ids)

    def fail_single_chart_load(*_args, **_kwargs):
        raise AssertionError("holdings chart enrichment must reuse the bulk detail map")

    def fail_duplicate_ledger_load(*_args, **_kwargs):
        raise AssertionError("position pricing must reuse the holdings detail map")

    monkeypatch.setattr(workspace_routes, "get_registry_instrument_details", recording_bulk_loader)
    monkeypatch.setattr(instrument_charts, "get_registry_instrument_detail", fail_single_chart_load)
    monkeypatch.setattr(ledger, "get_registry_instrument_details", fail_duplicate_ledger_load)
    monkeypatch.setattr(
        workspace_routes,
        "_holdings_workspace_has_market_profile",
        lambda *_args, **_kwargs: False,
    )

    response = client.get(
        "/api/workspace/holdings",
        params={
            "portfolio_id": "investment-studio",
            "as_of_date": "2026-04-15",
            "include_details": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(bulk_calls) == 1
    assert set(bulk_calls[0]) == _noncash_instrument_ids(payload)
    assert all(row.get("price_chart_6m") is not None for row in payload["rows"])


def test_workspace_summary_uses_one_bulk_detail_map(client, monkeypatch) -> None:
    snapshot_response = client.get("/api/portfolios/investment-studio/snapshots/daily")
    assert snapshot_response.status_code == 200

    original_bulk_loader = workspace_routes.get_registry_instrument_details
    bulk_calls: list[tuple[str, ...]] = []

    def recording_bulk_loader(instrument_ids):
        bulk_calls.append(tuple(instrument_ids))
        return original_bulk_loader(instrument_ids)

    monkeypatch.setattr(
        workspace_routes,
        "get_registry_instrument_details",
        recording_bulk_loader,
    )

    response = client.get(
        "/api/workspace/summary",
        params={"portfolio_id": "investment-studio"},
    )

    assert response.status_code == 200
    assert len(bulk_calls) == 1
    assert set(bulk_calls[0]) == {
        "equity-us-abbv",
        "fund-hk-2800",
        "fund-us-agg",
    }


def test_position_holding_projection_skips_portfolio_wide_analytics(client, monkeypatch) -> None:
    snapshot_response = client.get("/api/portfolios/investment-studio/snapshots/daily")
    assert snapshot_response.status_code == 200
    holdings_response = client.get(
        "/api/workspace/holdings",
        params={"portfolio_id": "investment-studio", "as_of_date": "2026-04-15"},
    )
    assert holdings_response.status_code == 200
    expected = next(
        row for row in holdings_response.json()["rows"]
        if row.get("instrument_core", {}).get("instrument_id") == "equity-us-abbv"
    )
    assert expected["break_even_price"] is not None

    def fail_portfolio_wide_load(*_args, **_kwargs):
        raise AssertionError("single-instrument holding projection must not build portfolio-wide analytics")

    monkeypatch.setattr(workspace_routes, "get_registry_instrument_details", fail_portfolio_wide_load)
    monkeypatch.setattr(workspace_routes, "get_portfolio_risk_policy", fail_portfolio_wide_load)
    monkeypatch.setattr(workspace_routes, "enrich_holdings_forward_risk", fail_portfolio_wide_load)

    response = client.get(
        "/api/workspace/holdings/position",
        params={
            "portfolio_id": "investment-studio",
            "position_reference_id": "equity-us-abbv",
            "as_of_date": "2026-04-15",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of_date"] == "2026-04-15"
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["instrument_core"]["instrument_id"] == "equity-us-abbv"
    assert payload["rows"][0]["break_even_price"] == expected["break_even_price"]
    assert payload["rows"][0]["net_invested"] == expected["net_invested"]
    assert "row" not in payload
    assert "price_chart_1m" not in payload["rows"][0]
    assert "instrument_return_series_all" not in payload["rows"][0]
    assert "forward_risk_share" not in payload["rows"][0]
    assert len(response.content) < 5_000


def test_position_holding_projection_maps_registry_enrichment_failure_to_502(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        workspace_routes,
        "build_materialized_position_holding_projection",
        lambda *_args, **_kwargs: {
            "portfolio_id": "investment-studio",
            "as_of_date": "2026-04-15",
            "rows": [
                {
                    "line_id": "option-registry-failure",
                    "holding_kind": "position",
                    "derivative_contract": {
                        "derivative_contract_id": "option-registry-failure",
                    },
                }
            ],
        },
    )
    monkeypatch.setattr(
        workspace_routes,
        "list_materialized_derivative_risk_context",
        lambda *_args, **_kwargs: [],
    )

    def fail_registry_enrichment(*_args, **_kwargs):
        raise workspace_routes.InstrumentRegistryError("Registry detail unavailable.")

    monkeypatch.setattr(
        workspace_routes,
        "enrich_derivative_holding_risk",
        fail_registry_enrichment,
    )

    response = client.get(
        "/api/workspace/holdings/position",
        params={
            "portfolio_id": "investment-studio",
            "position_reference_id": "option-registry-failure",
            "as_of_date": "2026-04-15",
        },
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Registry detail unavailable."


def test_materialized_option_position_and_obligation_remain_distinct_in_api_and_detail(
    client,
    monkeypatch,
) -> None:
    portfolio_id = "investment-studio"
    as_of_date = date(2026, 4, 15)
    account_id = "account-us-brokerage"
    derivative_contract_id = "option-us-short-call"
    derivative_contract = {
        "derivative_contract_id": derivative_contract_id,
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "contract_name": "Short Call Contract",
        "contract_type": "option",
        "currency": "USD",
        "external_reference": "TEST-SHORT-CALL",
        "terms": {
            "underlying_instrument_id": "equity-us-abbv",
            "option_type": "call",
            "expiry_date": "2026-12-18",
            "strike": "220",
            "contract_multiplier": "100",
        },
        "created_at": "2026-04-15T16:00:00Z",
    }
    calculated_at = "2026-04-15T16:00:00Z"
    position_payload = {
        "line_id": derivative_contract_id,
        "account_id": account_id,
        "account_ids": [account_id],
        "position_reference_id": derivative_contract_id,
        "instrument_id": None,
        "derivative_contract_id": derivative_contract_id,
        "derivative_contract": deepcopy(derivative_contract),
        "holding_kind": "position",
        "instrument_ref": None,
        "quantity": 1.0,
        "cost_basis_method": "fifo",
        "cost_basis": 500.0,
        "cost_basis_base": 500.0,
        "market_value": 500.0,
        "market_value_base": 500.0,
        "carrying_value": 500.0,
        "carrying_value_base": 500.0,
        "fair_value": None,
        "fair_value_coverage_status": "unavailable",
        "valuation_basis": "carried_cost",
        "performance_eligible": False,
        "risk_eligible": False,
        "coverage_status": "event-cost",
        "open_position_lot_count": 1,
    }
    obligation_payload = {
        "line_id": f"{derivative_contract_id}:obligation",
        "account_id": account_id,
        "account_ids": [account_id],
        "position_reference_id": derivative_contract_id,
        "instrument_id": None,
        "derivative_contract_id": derivative_contract_id,
        "derivative_contract": deepcopy(derivative_contract),
        "holding_kind": "option_obligation",
        "instrument_ref": None,
        "quantity": -1.0,
        "open_contract_quantity": 1.0,
        "required_underlying_quantity": 100.0,
        "obligation_status": "open",
        "related_underlying_id": "equity-us-abbv",
        "premium_received_gross": 300.0,
        "premium_basis_remaining": 300.0,
        "market_value": -300.0,
        "market_value_base": -300.0,
        "carrying_value": 300.0,
        "carrying_value_base": 300.0,
        "liability_value": 300.0,
        "liability_value_base": 300.0,
        "fair_value": None,
        "fair_value_coverage_status": "unavailable",
        "valuation_basis": "premium_liability",
        "available_for_trading": False,
        "is_liability": True,
        "performance_eligible": False,
        "risk_eligible": False,
        "coverage_status": "event-liability",
        "open_position_lot_count": 0,
    }

    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        if state is None:
            state = PortfolioCalculationStateModel(portfolio_id=portfolio_id)
            session.add(state)
        state.daily_snapshot_status = "current"
        session.add(
            DerivativeContractRecordModel(
                derivative_contract_id=derivative_contract_id,
                portfolio_id=portfolio_id,
                account_id=account_id,
                contract_name="Short Call Contract",
                contract_type="option",
                currency="USD",
                external_reference="TEST-SHORT-CALL",
                terms_json=deepcopy(derivative_contract["terms"]),
                created_at=calculated_at,
            )
        )
        session.add(
            PortfolioDailySnapshotModel(
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
                coverage_state="complete",
                valuation_coverage_state="complete",
                return_coverage_state="unavailable",
                book_pnl_coverage_state="complete",
                attribution_coverage_state="complete",
                nav=200.0,
                beginning_nav=200.0,
                ending_nav=200.0,
                daily_twr=None,
                cumulative_twr=None,
                drawdown=None,
                snapshot_json={
                    "calculation_version": daily_snapshots.DAILY_SNAPSHOT_CALCULATION_VERSION,
                    "base_currency": "USD",
                    "nav": 200.0,
                    "cash_balance": 0.0,
                    "pending_settlement": 0.0,
                },
                calculated_at=calculated_at,
            )
        )
        session.add_all(
            [
                PortfolioDailyHoldingSnapshotModel(
                    portfolio_id=portfolio_id,
                    as_of_date=as_of_date,
                    account_id=account_id,
                    position_reference_id=derivative_contract_id,
                    instrument_id=None,
                    derivative_contract_id=derivative_contract_id,
                    holding_kind="position",
                    currency="USD",
                    quantity=1.0,
                    cost_basis=500.0,
                    cost_basis_base=500.0,
                    last_price=None,
                    market_value=500.0,
                    market_value_base=500.0,
                    portfolio_weight=None,
                    holding_json=position_payload,
                    calculated_at=calculated_at,
                ),
                PortfolioDailyHoldingSnapshotModel(
                    portfolio_id=portfolio_id,
                    as_of_date=as_of_date,
                    account_id=account_id,
                    position_reference_id=derivative_contract_id,
                    instrument_id=None,
                    derivative_contract_id=derivative_contract_id,
                    holding_kind="option_obligation",
                    currency="USD",
                    quantity=-1.0,
                    cost_basis=None,
                    cost_basis_base=None,
                    last_price=None,
                    market_value=-300.0,
                    market_value_base=-300.0,
                    portfolio_weight=None,
                    holding_json=obligation_payload,
                    calculated_at=calculated_at,
                ),
            ]
        )
        session.flush()
        state.source_market_data_updated_at = daily_snapshots._source_market_data_watermark(
            session, portfolio_id,
        )
        state.source_calculation_inputs_updated_at = daily_snapshots._source_calculation_inputs_watermark(
            session, portfolio_id,
        )
        session.commit()

    monkeypatch.setattr(
        daily_snapshots,
        "ensure_portfolio_daily_snapshots",
        lambda _portfolio_id: None,
    )
    materialized = daily_snapshots.build_materialized_holdings_workspace(
        portfolio_id,
        as_of_date=as_of_date,
    )
    assert materialized is not None
    option_rows = [
        row
        for row in materialized["rows"]
        if row["derivative_contract_id"] == derivative_contract_id
    ]
    assert len(option_rows) == 2
    assert {row["holding_kind"] for row in option_rows} == {
        "position",
        "option_obligation",
    }
    assert all(row["instrument_core"] is None for row in option_rows)
    for row in option_rows:
        row.update(_populated_market_profile())

    monkeypatch.setattr(
        workspace_routes,
        "get_cached_materialized_holdings_workspace",
        lambda *_args, **_kwargs: deepcopy(materialized),
    )
    monkeypatch.setattr(
        workspace_routes,
        "get_registry_instrument_details",
        lambda instrument_ids: {item: None for item in instrument_ids},
    )
    monkeypatch.setattr(
        workspace_routes,
        "calculation_frequency_profile_for_instruments",
        lambda *_args, **_kwargs: {
            "resolved_frequency": "daily",
            "coverage_state": "complete",
        },
    )
    monkeypatch.setattr(
        workspace_routes,
        "_holdings_workspace_has_market_profile",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        workspace_routes,
        "enrich_holdings_forward_risk",
        lambda response, **_kwargs: response,
    )
    monkeypatch.setattr(
        workspace_routes,
        "corporate_action_quality_warnings",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        workspace_routes,
        "instrument_event_task_quality_warnings",
        lambda _portfolio_id: [],
    )

    holdings_response = client.get(
        "/api/workspace/holdings",
        params={
            "portfolio_id": portfolio_id,
            "as_of_date": as_of_date.isoformat(),
            "include_details": True,
        },
    )
    assert holdings_response.status_code == 200
    api_option_rows = [
        row
        for row in holdings_response.json()["rows"]
        if row["derivative_contract_id"] == derivative_contract_id
    ]
    assert len(api_option_rows) == 2
    assert {row["holding_kind"] for row in api_option_rows} == {
        "position",
        "option_obligation",
    }
    assert {row["holding_category"] for row in api_option_rows} == {"derivatives"}
    for row in api_option_rows:
        _assert_empty_market_profile(row)
    api_position = next(
        row for row in api_option_rows if row["holding_kind"] == "position"
    )
    assert api_position["market_value"] == 500.0
    assert api_position["carrying_value"] == 500.0
    assert api_position["valuation_basis"] == "carried_cost"
    api_obligation = next(
        row for row in api_option_rows if row["holding_kind"] == "option_obligation"
    )
    assert api_obligation["open_contract_quantity"] == 1.0
    assert api_obligation["required_underlying_quantity"] == 100.0
    assert api_obligation["obligation_status"] == "open"
    assert api_obligation["related_underlying_id"] == "equity-us-abbv"
    assert api_obligation["premium_basis_remaining"] == 300.0
    assert api_obligation["liability_value"] == 300.0
    assert api_obligation["market_value"] == -300.0
    assert api_obligation["valuation_basis"] == "premium_liability"

    detail_response = client.get(
        "/api/workspace/holdings/position",
        params={
            "portfolio_id": portfolio_id,
            "position_reference_id": derivative_contract_id,
            "as_of_date": as_of_date.isoformat(),
        },
    )
    assert detail_response.status_code == 200
    detail_rows = detail_response.json()["rows"]
    assert {row["holding_kind"] for row in detail_rows} == {
        "position",
        "option_obligation",
    }
    detail_obligation = next(
        row for row in detail_rows if row["holding_kind"] == "option_obligation"
    )
    assert detail_obligation["option_risk"]["underlying_instrument_id"] == "equity-us-abbv"
    assert detail_obligation["option_risk"]["backing"]["kind"] == "portfolio_underlying_shares"
    assert all("instrument_return_1m" not in row for row in detail_rows)
    assert all("price_chart_1m" not in row for row in detail_rows)
    for field_name in (
        "open_contract_quantity",
        "required_underlying_quantity",
        "obligation_status",
        "related_underlying_id",
        "premium_basis_remaining",
        "liability_value",
    ):
        assert detail_obligation[field_name] == api_obligation[field_name]


def test_position_holding_projection_fails_closed_when_snapshot_is_unavailable(client, monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_routes,
        "build_materialized_position_holding_projection",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        workspace_routes,
        "holdings_workspace",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("single-instrument projection must not rebuild the full workspace")
        ),
    )

    response = client.get(
        "/api/workspace/holdings/position",
        params={
            "portfolio_id": "investment-studio",
            "position_reference_id": "equity-us-abbv",
            "as_of_date": "2026-04-12",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Materialized holding projection is unavailable for the requested date."
    )


def test_dynamic_event_holding_rejects_registry_market_profile(
    monkeypatch,
) -> None:
    instrument_id = "option-dynamic-carried"
    instrument_ref = {
        "instrument_id": instrument_id,
        "instrument_name": "Dynamic Carried Option",
        "instrument_type": "option",
        "currency": "USD",
        "identifiers": [],
        "option_contract": {
            "underlying_instrument_id": "equity-us-abbv",
            "option_type": "call",
            "expiry_date": "2026-12-18",
            "strike": "220",
            "contract_multiplier": "100",
            "contract_currency": "USD",
        },
    }
    position = {
        "position_id": instrument_id,
        "instrument_id": instrument_id,
        "instrument_ref": deepcopy(instrument_ref),
        "holding_kind": "position",
        "quantity": 1.0,
        "last_price": None,
        "quote_as_of_date": None,
        "quote_metric_family": None,
        "quote_basis": None,
        "quote_provider": None,
        "quote_status": "event-cost",
        "market_value": 500.0,
        "market_value_base": 500.0,
        "day_change_pct": None,
        "day_change_value": None,
        "day_change_value_base": None,
        "cost_basis_method": "fifo",
        "cost_basis": 500.0,
        "cost_basis_base": 500.0,
        "portfolio_weight": 1.0,
        "coverage_status": "event-cost",
        "account_ids": ["account-us-brokerage"],
        "account_count": 1,
        "open_position_lot_count": 1,
        "carrying_value": 500.0,
        "carrying_value_base": 500.0,
        "fair_value": None,
        "fair_value_coverage_status": "unavailable",
        "valuation_basis": "carried_cost",
        "performance_eligible": False,
        "risk_eligible": False,
    }
    option_contract_id = "option-writer-live-fallback"
    option_contract = {
        "portfolio_id": "portfolio-dynamic-event",
        "account_id": "account-us-brokerage",
        "derivative_contract_id": option_contract_id,
        "contract_name": "ABBV 200 Call",
        "contract_type": "option",
        "currency": "USD",
        "external_reference": None,
        "created_at": "2026-04-01T00:00:00Z",
        "terms": {
            "underlying_instrument_id": "equity-us-abbv",
            "option_type": "call",
            "expiry_date": "2026-04-12",
            "strike": "200",
            "contract_multiplier": "100",
        },
    }
    option_obligation = {
        **deepcopy(position),
        "position_id": None,
        "line_id": f"{option_contract_id}:obligation",
        "position_reference_id": option_contract_id,
        "instrument_id": None,
        "instrument_ref": None,
        "derivative_contract_id": option_contract_id,
        "derivative_contract": option_contract,
        "holding_kind": "option_obligation",
        "quantity": -1.0,
        "open_contract_quantity": 1.0,
        "required_underlying_quantity": 100.0,
    }
    profile_calls: list[str] = []

    monkeypatch.setattr(
        workspace_routes,
        "_require_portfolio",
        lambda *_args, **_kwargs: {
            "portfolio_id": "portfolio-dynamic-event",
            "portfolio_name": "Dynamic Event Portfolio",
            "base_currency": "USD",
            "as_of_date": "2026-04-15",
        },
    )
    monkeypatch.setattr(
        workspace_routes,
        "get_cached_materialized_holdings_workspace",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(workspace_routes, "list_transactions", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(workspace_routes, "list_accounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(workspace_routes, "get_portfolio_risk_policy", lambda *_args: {})
    monkeypatch.setattr(
        workspace_routes,
        "build_position_lots",
        lambda *_args, **_kwargs: [
            {
                "status": "open",
                "instrument_id": instrument_id,
                "acquisition_date": "2026-04-01",
            }
        ],
    )
    monkeypatch.setattr(
        workspace_routes,
        "summarize_position_lots",
        lambda *_args: {"open_position_lot_count": 1},
    )
    monkeypatch.setattr(
        workspace_routes,
        "get_registry_instrument_details",
        lambda instrument_ids: {
            item: deepcopy(instrument_ref) if item == instrument_id else None
            for item in instrument_ids
        },
    )
    monkeypatch.setattr(
        workspace_routes,
        "calculation_frequency_profile_for_instruments",
        lambda *_args, **_kwargs: {
            "resolved_frequency": "daily",
            "coverage_state": "complete",
        },
    )
    monkeypatch.setattr(
        workspace_routes,
        "build_holdings_report",
        lambda *_args, **_kwargs: {
            "base_currency": "USD",
            "positions": [deepcopy(position), deepcopy(option_obligation)],
            "total_market_value_base": 500.0,
            "total_nav_base": 500.0,
            "cash_balance_base": 0.0,
            "pending_settlement_base": 0.0,
        },
    )

    def populated_profile(*_args, instrument_id: str, **_kwargs):
        profile_calls.append(instrument_id)
        return _populated_market_profile()

    monkeypatch.setattr(
        workspace_routes,
        "build_instrument_holdings_market_profile_from_detail",
        populated_profile,
    )
    monkeypatch.setattr(
        workspace_routes,
        "enrich_holdings_forward_risk",
        lambda response, **_kwargs: response,
    )
    monkeypatch.setattr(
        workspace_routes,
        "corporate_action_quality_warnings",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        workspace_routes,
        "instrument_event_task_quality_warnings",
        lambda *_args: [],
    )

    payload = workspace_routes.holdings_workspace(
        portfolio_id="portfolio-dynamic-event",
        as_of_date=date(2026, 4, 15),
        include_details=True,
    )

    assert profile_calls == []
    assert len(payload["rows"]) == 2
    row = payload["rows"][0]
    _assert_empty_market_profile(row)
    assert row["market_value"] == 500.0
    assert row["carrying_value"] == 500.0
    assert row["valuation_basis"] == "carried_cost"
    obligation_row = payload["rows"][1]
    assert obligation_row["line_id"] == f"{option_contract_id}:obligation"
    assert obligation_row["position_reference_id"] == option_contract_id
    assert obligation_row["derivative_contract_id"] == option_contract_id
    assert obligation_row["derivative_contract"] == option_contract


def test_fallback_holdings_reuses_bulk_details_for_frequency_valuation_and_charts(
    client,
    monkeypatch,
) -> None:
    # Measure the fallback holding projection after the required portfolio
    # valuation refresh, which has its own market-data reads.
    daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously("investment-studio")
    original_bulk_loader = workspace_routes.get_registry_instrument_details
    original_performance_loader = performance.get_registry_instrument_detail
    bulk_calls: list[tuple[str, ...]] = []
    singleton_calls: list[str] = []

    def recording_bulk_loader(instrument_ids):
        bulk_calls.append(tuple(instrument_ids))
        return original_bulk_loader(instrument_ids)

    def recording_performance_loader(instrument_id: str):
        singleton_calls.append(instrument_id)
        return original_performance_loader(instrument_id)

    def fail_single_chart_load(*_args, **_kwargs):
        raise AssertionError("holdings chart enrichment must reuse the bulk detail map")

    monkeypatch.setattr(
        workspace_routes,
        "get_cached_materialized_holdings_workspace",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(workspace_routes, "get_registry_instrument_details", recording_bulk_loader)
    monkeypatch.setattr(performance, "get_registry_instrument_detail", recording_performance_loader)
    monkeypatch.setattr(instrument_charts, "get_registry_instrument_detail", fail_single_chart_load)

    response = client.get(
        "/api/workspace/holdings",
        params={
            "portfolio_id": "investment-studio",
            "as_of_date": "2026-04-15",
            "include_details": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    held_instrument_ids = _noncash_instrument_ids(payload)
    assert len(bulk_calls) == 1
    assert set(bulk_calls[0]) == held_instrument_ids
    assert held_instrument_ids.isdisjoint(singleton_calls)
    assert all(row.get("price_chart_6m") is not None for row in payload["rows"])
