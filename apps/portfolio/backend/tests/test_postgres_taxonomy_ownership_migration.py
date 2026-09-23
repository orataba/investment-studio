"""Retire policy configuration without changing facts or saved research evidence."""
from datetime import date

from alembic import command
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from .test_postgres_schema_reconciliation import postgres_reconciliation_database, _portfolio_config

pytestmark = pytest.mark.postgresql_integration


def test_taxonomy_ownership_migration_preserves_defaults_and_audit_and_rebuilds_derived_data(
    postgres_reconciliation_database,
):
    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "20260920_0065")
    engine = sa.create_engine(postgres_reconciliation_database)
    metadata = sa.MetaData(schema="portfolio")
    tables = {name: sa.Table(name, metadata, autoload_with=engine) for name in (
        "portfolio_record", "taxonomy_record", "analytics_scope_policy_record",
        "analytics_taxonomy_selection_record", "taxonomy_configuration_revision",
        "portfolio_calculation_state", "portfolio_analytics_policy_state",
        "research_run_record", "portfolio_workspace_read_model",
    )}
    saved_request = {"target_configuration_snapshot": {"instrument_analytics_scopes": {
        "security": {"risk_eligible": False, "exclusion_reason": "Original policy"},
    }}}
    saved_result = {"points": [{"as_of_date": "2026-01-02", "nav": 123}], "methodology": "original"}
    configuration = {"taxonomy": {"name": "Original tree"}, "taxonomy_nodes": [{"node_name": "Equity"}]}
    portfolio_defaults = {
        "current": "old", "explicit-none": "fallback", "legacy": "legacy-tree",
        "invalid": "missing", "foreign": "new", "archived": "archived-tree",
        "disabled": "disabled-tree", "empty": None,
    }
    try:
        with engine.begin() as connection:
            for pid, default in portfolio_defaults.items():
                connection.execute(tables["portfolio_record"].insert().values(
                    portfolio_id=pid, portfolio_name=pid, base_currency="USD", valuation_timezone="UTC",
                    valuation_cutoff_policy="close", inception_date=date(2020, 1, 1), as_of_date=date(2026, 9, 17),
                    nav=123, day_change_value=0, day_change_pct=0, securities_count=0, sort_order=0,
                    default_planning_taxonomy_id=default,
                ))
            for tid, pid in [("old", "current"), ("new", "current"), ("fallback", "explicit-none"),
                             ("legacy-tree", "legacy"), ("archived-tree", "archived"),
                             ("disabled-tree", "disabled")]:
                connection.execute(tables["taxonomy_record"].insert().values(
                    taxonomy_id=tid, portfolio_id=pid, name=tid, taxonomy_type="custom",
                    primary_assignment_scope="instrument", planning_enabled=pid != "disabled",
                    root_default_target_dimension="weight", status="archived" if pid == "archived" else "active",
                ))
            for pid, selected in [("current", "new"), ("explicit-none", None), ("invalid", "missing")]:
                connection.execute(tables["analytics_taxonomy_selection_record"].insert().values(
                    analytics_taxonomy_selection_id=f"choice-{pid}", portfolio_id=pid,
                    taxonomy_id=selected, selection_version=40, created_at="original",
                ))
            connection.execute(tables["analytics_scope_policy_record"].insert().values(
                analytics_scope_policy_id="old-policy", portfolio_id="current", taxonomy_id="new",
                taxonomy_node_id="__root__", risk_eligible=False, risk_budget_eligible=False,
                performance_scope="operational_only", valuation_basis="carrying", exclusion_reason="Original policy",
                policy_version=90, created_at="original",
            ))
            connection.execute(tables["taxonomy_configuration_revision"].insert().values(
                taxonomy_configuration_revision_id="saved-revision", portfolio_id="current", taxonomy_id="new",
                configuration_version=60, configuration_json=configuration, created_at="original",
            ))
            connection.execute(tables["portfolio_analytics_policy_state"].insert().values(
                portfolio_id="current", current_version=10, updated_at="original",
            ))
            connection.execute(tables["portfolio_calculation_state"].insert().values(
                portfolio_id="current", daily_snapshot_status="current", dirty_from=date(2026, 9, 1),
                refresh_request_id="original", error_message="old error",
            ))
            connection.execute(tables["research_run_record"].insert().values(
                research_run_id="saved-run", portfolio_id="current", job_type="target_weight_solve", status="completed",
                lookback_days=90, as_of_date=date(2026, 1, 2), detail_json=saved_result, request_payload_json=saved_request,
            ))
            connection.execute(tables["portfolio_workspace_read_model"].insert().values(
                portfolio_id="current", surface="holdings", source_key="old-policy-generation",
                payload_json={"analytics_scope": "old"}, calculated_at="original",
            ))
        command.upgrade(config, "20260923_0066")
        inspector = sa.inspect(engine)
        current_tables = set(inspector.get_table_names(schema="portfolio"))
        assert {"analytics_scope_policy_record", "analytics_taxonomy_selection_record",
                "portfolio_analytics_policy_state"}.isdisjoint(current_tables)
        assert "portfolio_taxonomy_state" in current_tables
        expected_constraints = {
            "pk_portfolio_taxonomy_state",
            "fk_portfolio_taxonomy_state_portfolio_id_portfolio_record",
            "ck_portfolio_taxonomy_state_ck_taxonomy_state_version",
        }
        actual_constraints = {inspector.get_pk_constraint("portfolio_taxonomy_state", schema="portfolio")["name"]}
        actual_constraints.update(item["name"] for item in inspector.get_foreign_keys("portfolio_taxonomy_state", schema="portfolio"))
        actual_constraints.update(item["name"] for item in inspector.get_check_constraints("portfolio_taxonomy_state", schema="portfolio"))
        assert actual_constraints == expected_constraints
        with engine.connect() as connection:
            defaults = dict(connection.execute(sa.text(
                "SELECT portfolio_id, default_planning_taxonomy_id FROM portfolio.portfolio_record"
            )).all())
            assert defaults == {
                "current": "new", "explicit-none": None, "legacy": "legacy-tree", "invalid": None,
                "foreign": None, "archived": None, "disabled": None, "empty": None,
            }
            assert connection.scalar(sa.text(
                "SELECT current_version FROM portfolio.portfolio_taxonomy_state WHERE portfolio_id = 'current'"
            )) == 91
            assert connection.scalar(sa.text("SELECT count(*) FROM portfolio.portfolio_taxonomy_state")) == len(portfolio_defaults)
            assert connection.scalar(sa.text("SELECT count(*) FROM portfolio.portfolio_workspace_read_model")) == 0
            assert connection.execute(sa.text(
                "SELECT configuration_json, configuration_version, created_at FROM portfolio.taxonomy_configuration_revision"
            )).one() == (configuration, 60, "original")
            assert connection.execute(sa.text(
                "SELECT request_payload_json, detail_json FROM portfolio.research_run_record WHERE research_run_id='saved-run'"
            )).one() == (saved_request, saved_result)
            assert connection.execute(sa.text(
                "SELECT nav, as_of_date, inception_date FROM portfolio.portfolio_record WHERE portfolio_id='current'"
            )).one() == (123, date(2026, 9, 17), date(2020, 1, 1))
            refresh_states = connection.execute(sa.text(
                "SELECT daily_snapshot_status, dirty_from, refresh_request_id, error_message FROM portfolio.portfolio_calculation_state"
            )).all()
            assert len(refresh_states) == len(portfolio_defaults)
            assert all(status == "stale" and dirty is None and request != "original" and error is None
                for status, dirty, request, error in refresh_states)
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(sa.text(
                    "UPDATE portfolio.portfolio_taxonomy_state SET current_version = -1 WHERE portfolio_id = 'current'"
                ))
        with pytest.raises(RuntimeError, match="pre-migration backup"):
            command.downgrade(config, "20260920_0065")
    finally:
        engine.dispose()
