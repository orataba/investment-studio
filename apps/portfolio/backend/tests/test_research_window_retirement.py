"""Retired preferences cannot affect current research or rewrite old evidence."""
from copy import deepcopy
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.api.contracts import ResearchSettingsRecord, ResearchSettingsUpdateRequest
from portfolio_app.db.models import ResearchRunRecordModel
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services import research


RETIRED_COLUMNS = {
    "backtest_walk_forward_training_months", "backtest_walk_forward_test_months",
    "backtest_robustness_scenarios_json",
}
RETIRED_SETTINGS = {column.removesuffix("_json") for column in RETIRED_COLUMNS}
SAVED_SCENARIOS = [{
    "scenario_id": "saved-friction", "label": "Saved friction",
    "cash_yield_annual": 0.0, "commission_bps": 4.0, "tax_bps": 20.0,
    "slippage_bps": 10.0, "implementation_delay_days": 2,
}]
SAVED_REQUEST = {
    "backtest_walk_forward_training_months": 18,
    "backtest_walk_forward_test_months": 3,
    "backtest_robustness_scenarios": SAVED_SCENARIOS,
}
SAVED_WINDOW = {
    "validation_method": "rolling_temporal_holdout",
    "parameter_selection": "fixed_current_targets",
    "parameter_optimization": False,
    "available": True,
    "training_months": 18,
    "test_months": 3,
    "windows": [{
        "training_start_date": "2024-07-01", "training_end_date": "2025-12-31",
        "test_start_date": "2026-01-01", "test_end_date": "2026-03-31",
        "configuration_versions_used": [4], "available": True,
        "points": [{"date": "2025-12-31", "value": 1.0}, {"date": "2026-03-31", "value": 1.05}],
    }],
    "oos_points": [{"date": "2025-12-31", "value": 1.0}, {"date": "2026-03-31", "value": 1.05}],
}


def test_current_settings_publish_only_effective_simulation_controls(client):
    assert RETIRED_SETTINGS.isdisjoint(ResearchSettingsRecord.model_fields)
    assert RETIRED_SETTINGS.isdisjoint(ResearchSettingsUpdateRequest.model_fields)
    response = client.put("/api/portfolios/investment-studio/research/settings", json={
        "lookback_days": 30, "backtest_rebalance_frequency": "3m",
        "backtest_commission_bps": 4, "backtest_implementation_delay_days": 2,
    })
    assert response.status_code == 200, response.text
    settings = response.json()
    assert RETIRED_SETTINGS.isdisjoint(settings)
    assert settings["lookback_days"] == 30
    assert settings["backtest_rebalance_frequency"] == "3m"
    assert settings["backtest_commission_bps"] == 4
    assert settings["backtest_implementation_delay_days"] == 2
    workbench = client.get("/api/portfolios/investment-studio/research/workbench")
    assert workbench.status_code == 200, workbench.text
    assert RETIRED_SETTINGS.isdisjoint(workbench.json()["settings"])


def test_saved_windows_remain_readable_without_rewriting_run_json(client):
    detail = {"backtest": {"walk_forward": deepcopy(SAVED_WINDOW)}}
    with get_session_factory()() as session:
        session.add(ResearchRunRecordModel(
            research_run_id="saved-display-windows", portfolio_id="investment-studio",
            job_type="target_weight_solve", status="completed", lookback_days=30,
            requested_at="2026-04-15T10:00:00Z", as_of_date=date(2026, 4, 15),
            detail_json=deepcopy(detail), request_payload_json=deepcopy(SAVED_REQUEST), artifacts_json=[],
        ))
        session.commit()
    response = client.get("/api/portfolios/investment-studio/research/runs/saved-display-windows")
    assert response.status_code == 200, response.text
    saved = response.json()["detail"]["backtest"]["walk_forward"]
    assert saved["training_months"] == 18 and saved["test_months"] == 3
    for points, original in (
        (saved["windows"][0]["points"], SAVED_WINDOW["windows"][0]["points"]),
        (saved["oos_points"], SAVED_WINDOW["oos_points"]),
    ):
        assert [(point["date"], point["value"]) for point in points] == [
            (point["date"], point["value"]) for point in original
        ]
    with get_session_factory()() as session:
        run = session.get(ResearchRunRecordModel, "saved-display-windows")
        assert run.detail_json == detail
        assert run.request_payload_json == SAVED_REQUEST


def test_retired_preferences_do_not_make_unchanged_financial_results_stale():
    settings = {"planning_taxonomy_id": "planning", "as_of_mode": "pinned", "as_of_date": "2026-04-15"}
    request = {
        **settings, **SAVED_REQUEST, "lookback_days": 90, "calculation_frequency": "daily",
        "missing_return_policy": "strict", "frozen_taxonomy_node_ids": [], "top_sleeve_weight_bounds": [],
        "solver_version": research.RESEARCH_TARGET_SOLVER_VERSION,
        "planning_state_fingerprint_version": research.RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION,
        "planning_state_fingerprint": "sha256:unchanged-financial-inputs",
    }
    run = ResearchRunRecordModel(
        research_run_id="still-current", portfolio_id="investment-studio", status="completed",
        as_of_date=date(2026, 4, 15), request_payload_json=deepcopy(request),
    )
    assert research._research_run_reliability(
        run, latest_portfolio_as_of_date=date(2026, 4, 15), settings_payload=settings,
        production_risk_model={}, latest_transaction_date=None,
        current_planning_state_fingerprint=request["planning_state_fingerprint"],
    ) == ("current", [])
    assert run.request_payload_json == request


def _verify_migration_round_trip(engine, config, *, schema=None):
    metadata = sa.MetaData(schema=schema)
    portfolios, settings, runs = [sa.Table(name, metadata, autoload_with=engine) for name in (
        "portfolio_record", "research_settings_record", "research_run_record",
    )]
    with engine.begin() as connection:
        connection.execute(portfolios.insert().values(
            portfolio_id="window-retirement", portfolio_name="Window retirement", base_currency="USD",
            valuation_timezone="UTC", valuation_cutoff_policy="close", inception_date=date(2020, 1, 1),
            nav=100, securities_count=0, sort_order=0, day_change_value=0, day_change_pct=0,
        ))
        connection.execute(settings.insert().values(
            portfolio_id="window-retirement", as_of_mode="dynamic", lookback_days=90,
            calculation_frequency="daily", missing_return_policy="strict", capital_mode="unit_notional",
            backtest_rebalance_frequency="1m", backtest_cash_yield_annual=.03,
            backtest_commission_bps=4, backtest_tax_bps=11, backtest_slippage_bps=7,
            backtest_implementation_delay_days=2,
            backtest_walk_forward_training_months=18, backtest_walk_forward_test_months=3,
            backtest_robustness_scenarios_json=SAVED_SCENARIOS,
        ))
        connection.execute(runs.insert().values(
            research_run_id="window-retirement-archive", portfolio_id="window-retirement",
            job_type="target_weight_solve", status="completed", lookback_days=90,
            as_of_date=date(2026, 4, 15), request_payload_json=SAVED_REQUEST,
            detail_json={"backtest": {
                "walk_forward": SAVED_WINDOW,
                "robustness_results": [{**SAVED_SCENARIOS[0], "total_cost": .012, "warnings": []}],
            }},
        ))

    def preserved_facts():
        current_settings = sa.Table("research_settings_record", sa.MetaData(schema=schema), autoload_with=engine)
        with engine.connect() as connection:
            return (
                connection.execute(sa.select(portfolios).order_by(portfolios.c.portfolio_id)).all(),
                connection.execute(sa.select(
                    runs.c.research_run_id, sa.cast(runs.c.request_payload_json, sa.Text),
                    sa.cast(runs.c.detail_json, sa.Text),
                ).order_by(runs.c.research_run_id)).all(),
                connection.execute(sa.select(*[
                    column for column in current_settings.c if column.name not in RETIRED_COLUMNS
                ]).order_by(current_settings.c.portfolio_id)).all(),
            )

    before = preserved_facts()
    command.upgrade(config, "20260924_0069")
    inspector = sa.inspect(engine)
    assert RETIRED_COLUMNS.isdisjoint(column["name"] for column in inspector.get_columns("research_settings_record", schema=schema))
    assert not any("walk_forward" in check["name"] for check in inspector.get_check_constraints("research_settings_record", schema=schema))
    assert preserved_facts() == before

    command.downgrade(config, "20260923_0068")
    restored = sa.Table("research_settings_record", sa.MetaData(schema=schema), autoload_with=engine)
    with engine.connect() as connection:
        assert connection.execute(sa.select(
            restored.c.backtest_walk_forward_training_months, restored.c.backtest_walk_forward_test_months,
            restored.c.backtest_robustness_scenarios_json,
        ).where(restored.c.portfolio_id == "window-retirement")).one() == (24, 6, None)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(restored.update().where(restored.c.portfolio_id == "window-retirement").values(backtest_walk_forward_test_months=0))
    assert preserved_facts() == before
    command.upgrade(config, "20260924_0069")
    assert preserved_facts() == before


@pytest.mark.migration_base_revision("20260923_0068")
def test_sqlite_removes_only_retired_preferences_and_preserves_archives():
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", str(get_engine().url))
    _verify_migration_round_trip(get_engine(), config)
