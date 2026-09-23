from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from portfolio_app.db.session import get_engine
from portfolio_app.services.taxonomy_targets import resolve_taxonomy_targets

path = Path(__file__).resolve().parents[1] / "alembic/versions/20260923_0067_unified_taxonomy_targets.py"
spec = importlib.util.spec_from_file_location("unified_targets_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.mark.parametrize("basis,scope", [("weight", None), ("weight", "parent"), ("risk_budget", None), ("risk_budget", "parent")])
def test_migration_preserves_legacy_rounded_ratios_without_modifying_original_rows(basis, scope):
    taxonomies = [{"taxonomy_id": "tree", "root_default_target_dimension": basis}]
    nodes = [{"taxonomy_node_id": "parent", "default_target_dimension": basis}]
    sets = [{"target_set_id": "saa", "taxonomy_id": "tree", "comparator_taxonomy_node_id": scope, "status": "active",
        "weight_enabled": True, "risk_budget_enabled": True}]
    lines = [{"target_line_id": str(i), "target_set_id": "saa", "target_member_type": "instrument", "target_member_id": str(i),
        "target_weight": .3333 if basis == "weight" else .25, "target_risk_share": .3333 if basis == "risk_budget" else .25} for i in range(3)]
    cash_reserve = .0001 if basis == "weight" else .1
    if scope is None:
        lines.append({"target_line_id": "cash", "target_set_id": "saa", "target_member_type": "cash_bucket", "target_member_id": "__cash__", "target_weight": cash_reserve, "target_risk_share": None})
    original = deepcopy(lines)
    converted = migration.convert_target_lines(taxonomies, nodes, sets, lines)
    assert lines == original
    security = [line for line in converted if line["target_member_type"] == "instrument"]
    assert [line["target_value"] for line in security] == pytest.approx([1/3]*3)
    assert sum(line["target_value"] for line in security) == pytest.approx(1)
    if scope is None:
        assert converted[-1]["target_value"] == cash_reserve


@pytest.mark.parametrize("basis", ["weight", "risk_budget"])
@pytest.mark.parametrize("stage", ["saa", "taa"])
def test_migration_does_not_activate_disabled_stage_or_its_cash_reserve(basis, stage):
    taxonomies = [{"taxonomy_id": "tree", "root_default_target_dimension": basis}]
    sets = [{"target_set_id": stage, "taxonomy_id": "tree", "comparator_taxonomy_node_id": None,
        "status": "active", "weight_enabled": basis != "weight", "risk_budget_enabled": basis != "risk_budget"}]
    lines = [{"target_line_id": "security", "target_set_id": stage, "target_member_type": "instrument",
        "target_member_id": "security", "target_weight": .8, "target_risk_share": 1},
        {"target_line_id": "cash", "target_set_id": stage, "target_member_type": "cash_bucket",
        "target_member_id": "__cash__", "target_weight": .2, "target_risk_share": None}]
    original = deepcopy(lines)
    assert migration.convert_target_lines(taxonomies, [], sets, lines) == []
    assert lines == original


@pytest.mark.migration_base_revision("20260923_0066")
def test_sqlite_migration_removes_disabled_stage_and_keeps_original_audit():
    engine = get_engine()
    metadata = sa.MetaData()
    tables = {name: sa.Table(name, metadata, autoload_with=engine) for name in (
        "taxonomy_record", "taxonomy_node_record", "target_set_record", "target_set_line_record")}
    with engine.begin() as connection:
        connection.execute(tables["taxonomy_record"].insert().values(taxonomy_id="tree", portfolio_id="investment-studio",
            name="Tree", taxonomy_type="custom", primary_assignment_scope="instrument", status="active",
            planning_enabled=True, budgeting_level="weight_and_risk_budget", root_default_target_dimension="risk_budget"))
        for node_id in ("equity", "bonds"):
            connection.execute(tables["taxonomy_node_record"].insert().values(taxonomy_node_id=node_id,
                taxonomy_id="tree", node_name=node_id, sort_order=0, is_terminal=True, status="active", default_target_dimension="weight"))
        for stage, enabled in [("saa", True), ("taa", False)]:
            connection.execute(tables["target_set_record"].insert().values(target_set_id=stage, taxonomy_id="tree",
                name=stage, target_set_type=stage, weight_enabled=True, risk_budget_enabled=enabled, status="active"))
            for node_id, value in [("equity", .6), ("bonds", .4)]:
                connection.execute(tables["target_set_line_record"].insert().values(target_line_id=f"{stage}-{node_id}",
                    target_set_id=stage, taxonomy_node_id=node_id, target_member_type="taxonomy_node", target_member_id=node_id,
                    target_weight=value * .8, target_risk_share=value))
            connection.execute(tables["target_set_line_record"].insert().values(target_line_id=f"{stage}-cash",
                target_set_id=stage, target_member_type="cash_bucket", target_member_id="__cash__", target_weight=.2))
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    config.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(config, "20260923_0067")
    revisions = sa.Table("taxonomy_configuration_revision", sa.MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM target_set_record WHERE target_set_id='taa'")) == 0
        current = connection.scalar(sa.select(revisions.c.configuration_json).where(
            revisions.c.taxonomy_id == "tree", revisions.c.superseded_by_revision_id.is_(None)))
        assert connection.scalar(sa.text("PRAGMA foreign_key_check")) is None
    original = current["migration_audit"]["original_configuration"]
    assert len(original["target_set_lines"]) == 6
    assert [row["target_set_id"] for row in current["target_sets"]] == ["saa"]
    root = next(row for row in resolve_taxonomy_targets(current)["scope_targets"] if row["scope_node_id"] is None)
    assert root["saa"]["status"] == "complete"
    assert root["taa"]["inherited"] is True
    assert root["taa"]["rows"] == root["saa"]["rows"]
