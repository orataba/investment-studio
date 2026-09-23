from copy import deepcopy
from datetime import date

from alembic import command
import pytest
import sqlalchemy as sa

from .test_postgres_schema_reconciliation import postgres_reconciliation_database, _portfolio_config
from .test_postgres_unified_targets_migration import _seed_legacy


pytestmark = pytest.mark.postgresql_integration


def test_explicit_limits_migrate_all_dates_and_deleted_member_references_in_postgres(postgres_reconciliation_database):
    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "20260923_0066")
    engine = sa.create_engine(postgres_reconciliation_database)
    try:
        _seed_legacy(engine)
        command.upgrade(config, "20260923_0067")
        metadata = sa.MetaData(schema="portfolio")
        policies = sa.Table("concentration_policy_revision", metadata, autoload_with=engine)
        revisions = sa.Table("taxonomy_configuration_revision", metadata, autoload_with=engine)
        original = {"rules": [
            {"rule_id": "all-nodes", "scope": "taxonomy", "taxonomy_id": "tree", "entity_id": None, "limit_weight": .4, "enabled": True},
            {"rule_id": "equity-off", "scope": "taxonomy", "taxonomy_id": "tree", "entity_id": "equity", "limit_weight": .2, "enabled": False},
            {"rule_id": "bonds-zero", "scope": "taxonomy", "taxonomy_id": "tree", "entity_id": "bonds", "limit_weight": 0, "enabled": True},
            {"rule_id": "security-zero", "scope": "security", "entity_id": "historic-security", "limit_weight": 0, "enabled": True},
            {"rule_id": "historic-fcn", "scope": "fcn", "entity_id": "historic-fcn", "limit_weight": .1, "enabled": True},
        ], "fcn_allocations": [{"contract_id": "historic-fcn", "method": "equal", "weights": []}]}
        future = deepcopy(original)
        future["rules"][0]["enabled"] = False
        with engine.begin() as connection:
            connection.execute(revisions.insert().values(taxonomy_configuration_revision_id="deleted-node-audit",
                portfolio_id="scalar", taxonomy_id="tree", configuration_version=99, created_at="old",
                superseded_by_revision_id="original", configuration_json={"taxonomy_nodes": [{"taxonomy_node_id": "deleted-node"}]}))
            for revision, effective, settings in [(1, date(2026, 4, 1), original), (2, date(2027, 1, 1), future)]:
                connection.execute(policies.insert().values(portfolio_id="scalar", revision=revision,
                    effective_from=effective, settings_json=settings, created_by="PM", created_at=f"created-{revision}"))
        command.upgrade(config, "20260923_0068")
        with engine.connect() as connection:
            rows = connection.execute(sa.select(policies).order_by(policies.c.revision)).all()
        assert [(row.revision, row.effective_from, row.created_at) for row in rows] == [
            (1, date(2026, 4, 1), "created-1"), (2, date(2027, 1, 1), "created-2")]
        for row, previous in zip(rows, [original, future], strict=True):
            settings = row.settings_json
            assert settings["migration_audit"]["original_settings"] == previous
            assert settings["enabled_taxonomy_ids"] == (["tree"] if row.revision == 1 else [])
            actual = {(item["scope"], item["entity_id"]): item["limit_weight"] for item in settings["limits"]}
            assert actual == {("taxonomy", "bonds"): 0, ("taxonomy", "deleted-node"): .4,
                ("security", "historic-security"): 0, ("fcn", "historic-fcn"): .1}
            assert settings["fcn_allocations"] == original["fcn_allocations"]
    finally:
        engine.dispose()


def test_copied_migrated_policy_requires_backup_without_rewriting_audit_or_identities(postgres_reconciliation_database):
    from portfolio_app.services.portfolio_store import copy_portfolio

    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "20260923_0066")
    engine = sa.create_engine(postgres_reconciliation_database)
    try:
        _seed_legacy(engine)
        command.upgrade(config, "20260923_0067")
        metadata = sa.MetaData(schema="portfolio")
        policies = sa.Table("concentration_policy_revision", metadata, autoload_with=engine)
        taxonomies = sa.Table("taxonomy_record", metadata, autoload_with=engine)
        nodes = sa.Table("taxonomy_node_record", metadata, autoload_with=engine)
        original = {"rules": [
            {"rule_id": "equity", "scope": "taxonomy", "taxonomy_id": "tree", "entity_id": "equity",
             "limit_weight": .4, "enabled": True},
        ], "fcn_allocations": []}
        with engine.begin() as connection:
            connection.execute(policies.insert().values(portfolio_id="scalar", revision=1,
                effective_from=date(2026, 4, 1), settings_json=original, created_by="PM", created_at="original"))
        command.upgrade(config, "20260923_0068")
        copied = copy_portfolio("scalar")
        assert copied is not None
        copied_id = copied["portfolio_id"]

        def read_state():
            with engine.connect() as connection:
                settings = dict(connection.execute(sa.select(policies.c.portfolio_id, policies.c.settings_json)).all())
                tree_ids = set(connection.scalars(sa.select(taxonomies.c.taxonomy_id).where(
                    taxonomies.c.portfolio_id == copied_id)))
                node_ids = set(connection.scalars(sa.select(nodes.c.taxonomy_node_id).where(
                    nodes.c.taxonomy_id.in_(tree_ids))))
                head = connection.scalar(sa.text("SELECT version_num FROM portfolio.alembic_version"))
            return settings, tree_ids, node_ids, head

        before = read_state()
        source_settings = before[0]["scalar"]
        copied_settings = before[0][copied_id]
        assert copied_settings["copied_from_portfolio_id"] == "scalar"
        assert copied_settings["migration_audit"] == source_settings["migration_audit"]
        assert copied_settings["migration_audit"]["original_settings"] == original
        copied_limit = copied_settings["limits"][0]
        assert copied_limit["taxonomy_id"] in before[1]
        assert copied_limit["taxonomy_id"] != "tree"
        assert copied_limit["entity_id"] in before[2]
        assert copied_limit["entity_id"] != "equity"

        with pytest.raises(RuntimeError, match="restore the pre-migration backup"):
            command.downgrade(config, "20260923_0067")
        assert read_state() == before
    finally:
        engine.dispose()
