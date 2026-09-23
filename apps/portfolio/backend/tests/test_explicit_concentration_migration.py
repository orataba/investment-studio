from copy import deepcopy
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from portfolio_app.db.session import get_engine
from portfolio_app.services.concentration_settings import read_concentration_settings

pytestmark = pytest.mark.migration_base_revision("20260923_0067")


def config():
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(get_engine().url))
    return cfg


def policies():
    return sa.Table("concentration_policy_revision", sa.MetaData(), autoload_with=get_engine())


def test_every_dated_revision_is_expanded_and_original_policy_is_retained():
    table = policies()
    with get_engine().begin() as connection:
        pid = connection.scalar(sa.text("SELECT portfolio_id FROM portfolio_record LIMIT 1"))
        tid, nid = "industry", "active-node"
        taxonomy = sa.Table("taxonomy_record", sa.MetaData(), autoload_with=connection)
        node = sa.Table("taxonomy_node_record", sa.MetaData(), autoload_with=connection)
        connection.execute(taxonomy.insert().values(taxonomy_id=tid, portfolio_id=pid, name="Industry", taxonomy_type="custom", primary_assignment_scope="instrument", root_allocation_basis="weight", status="active"))
        connection.execute(node.insert().values(taxonomy_node_id=nid, taxonomy_id=tid, node_name="Active", sort_order=0, is_terminal=True, allocation_basis="weight", status="active"))
        history = sa.Table("taxonomy_configuration_revision", sa.MetaData(), autoload_with=connection)
        connection.execute(history.insert().values(taxonomy_configuration_revision_id="historical-config", portfolio_id=pid, taxonomy_id=tid,
            configuration_version=1, created_at="historic", configuration_json={"taxonomy_nodes": [{"taxonomy_node_id": "deleted-node", "taxonomy_id": tid}]}))
        before_transactions = connection.execute(sa.text("SELECT portfolio_id, transaction_id, quantity FROM transaction_record ORDER BY portfolio_id, transaction_id")).all()
        old = {"rules": [
            {"rule_id": "security-common", "scope": "security", "entity_id": None, "limit_weight": .2, "watch_weight": .1, "enabled": True},
            {"rule_id": "security-zero", "scope": "security", "entity_id": "equity-us-abbv", "limit_weight": 0, "enabled": True},
            {"rule_id": "security-off", "scope": "security", "entity_id": "fund-us-agg", "limit_weight": .1, "enabled": False},
            {"rule_id": "category-common", "scope": "taxonomy", "taxonomy_id": tid, "entity_id": None, "limit_weight": .4, "enabled": True},
            {"rule_id": "category-off", "scope": "taxonomy", "taxonomy_id": tid, "entity_id": nid, "limit_weight": .1, "enabled": False},
        ], "fcn_allocations": [{"contract_id": "historic-fcn", "method": "equal", "weights": []}]}
        future = deepcopy(old)
        future["rules"][0]["limit_weight"] = .3
        for revision, effective, payload in [(1, date(2026, 4, 1), old), (2, date(2027, 1, 1), future)]:
            connection.execute(table.insert().values(portfolio_id=pid, revision=revision, effective_from=effective,
                settings_json=payload, created_by="PM", created_at=f"revision-{revision}"))
    command.upgrade(config(), "20260923_0068")
    with get_engine().connect() as connection:
        converted = connection.execute(sa.select(table).where(table.c.portfolio_id == pid).order_by(table.c.revision)).all()
        assert connection.execute(sa.text("SELECT portfolio_id, transaction_id, quantity FROM transaction_record ORDER BY portfolio_id, transaction_id")).all() == before_transactions
    for index, row in enumerate(converted):
        settings = row.settings_json
        assert row.created_by == "PM" and row.created_at == f"revision-{index + 1}"
        assert settings["migration_audit"]["original_settings"] == [old, future][index]
        assert settings["schema_version"] == 2 and "rules" not in settings
        assert settings["enabled_taxonomy_ids"] == [tid]
        limits = {(item["scope"], item["entity_id"]): item["limit_weight"] for item in settings["limits"]}
        assert limits[("security", "equity-us-abbv")] == 0
        assert ("security", "fund-us-agg") not in limits
        assert ("taxonomy", nid) not in limits
        assert limits[("taxonomy", "deleted-node")] == .4
        assert settings["fcn_allocations"] == old["fcn_allocations"]
        assert all(set(item) == {"scope", "taxonomy_id", "entity_id", "limit_weight"} for item in settings["limits"])
    displayed = read_concentration_settings(pid, as_of_date=date(2026, 4, 15))
    assert displayed["revision"] == 1 and displayed["latest_revision"] == 2
    assert "migration_audit" not in displayed
    command.downgrade(config(), "20260923_0067")
    with get_engine().connect() as connection:
        restored = connection.execute(sa.select(table.c.settings_json).where(table.c.portfolio_id == pid).order_by(table.c.revision)).scalars().all()
    assert restored == [old, future]


def test_new_native_revision_cannot_be_downgraded_into_inherited_rules():
    command.upgrade(config(), "20260923_0068")
    table = policies()
    with get_engine().begin() as connection:
        pid = connection.scalar(sa.text("SELECT portfolio_id FROM portfolio_record LIMIT 1"))
        connection.execute(table.insert().values(portfolio_id=pid, revision=1, effective_from=date(2026, 4, 1),
            settings_json={"schema_version": 2, "enabled_taxonomy_ids": [], "limits": [], "fcn_allocations": []}, created_by="PM", created_at="now"))
    with pytest.raises(RuntimeError, match="restore the pre-migration backup"):
        command.downgrade(config(), "20260923_0067")
