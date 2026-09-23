"""The scalar-target cutover preserves original facts and rejects ambiguous inputs."""
from datetime import date

from alembic import command
import pytest
import sqlalchemy as sa

from .test_postgres_schema_reconciliation import postgres_reconciliation_database, _portfolio_config

pytestmark = pytest.mark.postgresql_integration


def _seed_legacy(engine, *, ambiguous=False):
    metadata = sa.MetaData(schema="portfolio")
    tables = {name: sa.Table(name, metadata, autoload_with=engine) for name in (
        "portfolio_record", "taxonomy_record", "taxonomy_node_record", "target_set_record",
        "target_set_line_record", "taxonomy_configuration_revision", "research_run_record")}
    with engine.begin() as c:
        c.execute(tables["portfolio_record"].insert().values(portfolio_id="scalar", portfolio_name="Scalar",
            base_currency="USD", valuation_timezone="UTC", valuation_cutoff_policy="close", inception_date=date(2020,1,1),
            default_planning_taxonomy_id="tree", nav=123, securities_count=0, sort_order=0, day_change_value=0, day_change_pct=0))
        c.execute(tables["taxonomy_record"].insert().values(taxonomy_id="tree", portfolio_id="scalar", name="Tree",
            taxonomy_type="custom", primary_assignment_scope="instrument", planning_enabled=True,
            budgeting_level="weight_and_risk_budget", root_default_target_dimension="weight", status="active"))
        for nid in ("equity", "bonds"):
            c.execute(tables["taxonomy_node_record"].insert().values(taxonomy_node_id=nid, taxonomy_id="tree",
                node_name=nid, sort_order=0, is_terminal=True, default_target_dimension="risk_budget", status="active"))
        c.execute(tables["target_set_record"].insert().values(target_set_id="saa", taxonomy_id="tree", name="SAA",
            target_set_type="saa", weight_enabled=True, risk_budget_enabled=True, status="active"))
        for nid, weight, risk in [("equity", None if ambiguous else .4, .6), ("bonds", .2, .4)]:
            c.execute(tables["target_set_line_record"].insert().values(target_line_id=nid, target_set_id="saa",
                taxonomy_node_id=nid, target_member_type="taxonomy_node", target_member_id=nid,
                target_weight=weight, target_risk_share=risk))
        for kind, mid, weight in [("cash_bucket", "__cash__", .1), ("derivative_bucket", "__derivatives__", .3)]:
            c.execute(tables["target_set_line_record"].insert().values(target_line_id=mid, target_set_id="saa",
                target_member_type=kind, target_member_id=mid, target_weight=weight))
        c.execute(tables["taxonomy_configuration_revision"].insert().values(taxonomy_configuration_revision_id="original",
            portfolio_id="scalar", taxonomy_id="tree", configuration_version=1, configuration_json={"old": "audit"}, created_at="old"))
        c.execute(sa.text("INSERT INTO portfolio.portfolio_taxonomy_state (portfolio_id,current_version,updated_at) VALUES ('scalar',1,'old')"))
        c.execute(tables["research_run_record"].insert().values(research_run_id="saved", portfolio_id="scalar",
            job_type="target_weight_solve", status="completed", lookback_days=90, as_of_date=date(2026,1,2),
            detail_json={"target_weight": .4, "solver_version": "original"}, request_payload_json={"original": True}))


def test_unified_targets_migration_preserves_cash_relative_weights_and_audits(postgres_reconciliation_database):
    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "20260923_0066")
    engine = sa.create_engine(postgres_reconciliation_database)
    try:
        _seed_legacy(engine)
        command.upgrade(config, "head")
        inspector = sa.inspect(engine)
        expected_absent = {
            "portfolio_record": {"default_planning_taxonomy_id"},
            "taxonomy_record": {"planning_enabled", "budgeting_level", "root_default_target_dimension"},
            "taxonomy_node_record": {"default_target_dimension"},
            "target_set_record": {"weight_enabled", "risk_budget_enabled"},
            "target_set_line_record": {"target_weight", "target_risk_share"},
            "research_settings_record": {"target_dimension"},
        }
        for table, absent in expected_absent.items():
            assert absent.isdisjoint(col["name"] for col in inspector.get_columns(table, schema="portfolio"))
        with engine.connect() as c:
            values = dict(c.execute(sa.text("SELECT target_member_id, target_value FROM portfolio.target_set_line_record")).all())
            assert values == pytest.approx({"equity": 2/3, "bonds": 1/3, "__cash__": .1})
            assert c.scalar(sa.text("SELECT nav FROM portfolio.portfolio_record WHERE portfolio_id='scalar'")) == 123
            assert c.scalar(sa.text("SELECT configuration_json FROM portfolio.taxonomy_configuration_revision WHERE taxonomy_configuration_revision_id='original'")) == {"old": "audit"}
            current = c.scalar(sa.text("SELECT configuration_json FROM portfolio.taxonomy_configuration_revision WHERE taxonomy_id='tree' AND superseded_by_revision_id IS NULL"))
            original_lines = current["migration_audit"]["original_configuration"]["target_set_lines"]
            assert next(x for x in original_lines if x["target_line_id"] == "equity")["target_weight"] == .4
            assert next(x for x in original_lines if x["target_line_id"] == "__derivatives__")["target_weight"] == .3
            assert current["taxonomy"]["root_allocation_basis"] == "weight"
            assert c.scalar(sa.text("SELECT detail_json FROM portfolio.research_run_record WHERE research_run_id='saved'")) == {"target_weight": .4, "solver_version": "original"}
            assert c.scalar(sa.text("SELECT current_version FROM portfolio.portfolio_taxonomy_state WHERE portfolio_id='scalar'")) == 2
    finally:
        engine.dispose()


def test_ambiguous_selected_basis_fails_before_schema_or_data_changes(postgres_reconciliation_database):
    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "20260923_0066")
    engine = sa.create_engine(postgres_reconciliation_database)
    try:
        _seed_legacy(engine, ambiguous=True)
        with pytest.raises(ValueError, match="configured weight value is missing"):
            command.upgrade(config, "20260923_0067")
        columns = {col["name"] for col in sa.inspect(engine).get_columns("target_set_line_record", schema="portfolio")}
        assert "target_weight" in columns and "target_value" not in columns
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT target_risk_share FROM portfolio.target_set_line_record WHERE target_line_id='equity'")) == .6
            assert c.scalar(sa.text("SELECT version_num FROM portfolio.alembic_version")) == "20260923_0066"
    finally:
        engine.dispose()


def test_zero_security_vector_does_not_silently_become_invalid(postgres_reconciliation_database):
    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "20260923_0066")
    engine = sa.create_engine(postgres_reconciliation_database)
    try:
        _seed_legacy(engine)
        with engine.begin() as c:
            c.execute(sa.text("UPDATE portfolio.target_set_line_record SET target_weight=0 WHERE target_member_type='taxonomy_node'"))
            c.execute(sa.text("UPDATE portfolio.target_set_line_record SET target_weight=.8 WHERE target_member_type='cash_bucket'"))
            c.execute(sa.text("UPDATE portfolio.target_set_line_record SET target_weight=.2 WHERE target_member_type='derivative_bucket'"))
        with pytest.raises(ValueError, match="zero security weights"):
            command.upgrade(config, "20260923_0067")
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT version_num FROM portfolio.alembic_version")) == "20260923_0066"
    finally:
        engine.dispose()


def test_disabled_taa_is_absent_after_migration_and_original_values_remain_auditable(postgres_reconciliation_database):
    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "20260923_0066")
    engine = sa.create_engine(postgres_reconciliation_database)
    try:
        _seed_legacy(engine)
        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO portfolio.target_set_record "
                "(target_set_id,taxonomy_id,name,target_set_type,weight_enabled,risk_budget_enabled,status) "
                "VALUES ('taa','tree','TAA','taa',false,true,'active')"))
            c.execute(sa.text("INSERT INTO portfolio.target_set_line_record "
                "(target_line_id,target_set_id,taxonomy_node_id,target_member_type,target_member_id,target_weight,target_risk_share) "
                "VALUES ('taa-equity','taa','equity','taxonomy_node','equity',.6,.8), "
                "('taa-bonds','taa','bonds','taxonomy_node','bonds',.2,.2), "
                "('taa-cash','taa',NULL,'cash_bucket','__cash__',.2,NULL)"))
        command.upgrade(config, "20260923_0067")
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT count(*) FROM portfolio.target_set_record WHERE target_set_id='taa'")) == 0
            current = c.scalar(sa.text("SELECT configuration_json FROM portfolio.taxonomy_configuration_revision "
                "WHERE taxonomy_id='tree' AND superseded_by_revision_id IS NULL"))
            assert [row["target_set_id"] for row in current["target_sets"]] == ["saa"]
            audit = current["migration_audit"]["original_configuration"]
            assert next(row for row in audit["target_sets"] if row["target_set_id"] == "taa")["weight_enabled"] is False
            assert next(row for row in audit["target_set_lines"] if row["target_line_id"] == "taa-cash")["target_weight"] == .2
        from portfolio_app.services.taxonomy_targets import resolve_taxonomy_targets
        root = next(row for row in resolve_taxonomy_targets(current)["scope_targets"] if row["scope_node_id"] is None)
        assert root["taa"]["inherited"] is True
        assert root["taa"]["rows"] == root["saa"]["rows"]
    finally:
        engine.dispose()
