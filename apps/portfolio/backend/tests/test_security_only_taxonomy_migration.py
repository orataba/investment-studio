from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError


pytestmark = pytest.mark.migration_base_revision("20260810_0047")
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _config() -> Config:
    from portfolio_app.core.settings import get_settings

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(BACKEND_ROOT / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


def _taxonomy_values(taxonomy_id: str, scope: str) -> dict[str, object]:
    return {
        "taxonomy_id": taxonomy_id,
        "portfolio_id": "investment-studio",
        "name": taxonomy_id,
        "taxonomy_type": "custom",
        "purpose": None,
        "primary_assignment_scope": scope,
        "planning_enabled": True,
        "budgeting_level": "weight",
        "root_default_target_dimension": "weight",
        "status": "active",
        "source_template_ref": None,
    }


def test_security_only_taxonomy_migration_rejects_legacy_scope_and_enforces_head() -> None:
    from portfolio_app.db.session import get_engine

    engine = get_engine()
    config = _config()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO taxonomy_record ("
                "taxonomy_id, portfolio_id, name, taxonomy_type, purpose, "
                "primary_assignment_scope, planning_enabled, budgeting_level, "
                "root_default_target_dimension, status, source_template_ref"
                ") VALUES ("
                ":taxonomy_id, :portfolio_id, :name, :taxonomy_type, :purpose, "
                ":primary_assignment_scope, :planning_enabled, :budgeting_level, "
                ":root_default_target_dimension, :status, :source_template_ref"
                ")"
            ),
            _taxonomy_values("legacy-account-scope", "account"),
        )

    with pytest.raises(RuntimeError, match="Security-only taxonomy migration"):
        command.upgrade(config, "head")

    with engine.begin() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260810_0047"
        )
        connection.execute(
            sa.text(
                "DELETE FROM taxonomy_record "
                "WHERE taxonomy_id = 'legacy-account-scope'"
            )
        )

        connection.execute(
            sa.text(
                "INSERT INTO taxonomy_record ("
                "taxonomy_id, portfolio_id, name, taxonomy_type, purpose, "
                "primary_assignment_scope, planning_enabled, budgeting_level, "
                "root_default_target_dimension, status, source_template_ref"
                ") VALUES ("
                ":taxonomy_id, :portfolio_id, :name, :taxonomy_type, :purpose, "
                ":primary_assignment_scope, :planning_enabled, :budgeting_level, "
                ":root_default_target_dimension, :status, :source_template_ref"
                ")"
            ),
            _taxonomy_values("legacy-target-scope", "instrument"),
        )
        connection.execute(
            sa.text(
                "INSERT INTO target_set_record ("
                "target_set_id, taxonomy_id, comparator_taxonomy_node_id, "
                "target_set_type, name, weight_enabled, risk_budget_enabled, "
                "status, notes"
                ") VALUES ("
                "'legacy-targets', 'legacy-target-scope', NULL, 'saa', "
                "'Legacy Targets', 1, 1, 'active', NULL"
                ")"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO target_set_line_record ("
                "target_line_id, target_set_id, taxonomy_node_id, "
                "target_member_type, target_member_id, target_weight, "
                "target_risk_share, notes"
                ") VALUES ("
                "'legacy-account-target', 'legacy-targets', NULL, "
                "'account', 'broker-us-core', 0.1, 0.1, NULL"
                ")"
            )
        )

    with pytest.raises(RuntimeError, match="Security-only taxonomy migration"):
        command.upgrade(config, "head")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "DELETE FROM target_set_line_record "
                "WHERE target_line_id = 'legacy-account-target'"
            )
        )
        connection.execute(
            sa.text("DELETE FROM target_set_record WHERE target_set_id = 'legacy-targets'")
        )
        connection.execute(
            sa.text("DELETE FROM taxonomy_record WHERE taxonomy_id = 'legacy-target-scope'")
        )

    command.upgrade(config, "head")

    # Legacy fixtures above exercise the 0047 schema. Once upgraded, use the
    # current models to verify those security-only constraints still hold.
    from portfolio_app.db.models import (
        TargetSetLineRecordModel,
        TargetSetRecordModel,
        TaxonomyAssignmentRecordModel,
        TaxonomyNodeRecordModel,
        TaxonomyRecordModel,
    )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.insert(TaxonomyRecordModel).values(
                taxonomy_id="rejected-cash-scope", portfolio_id="investment-studio",
                name="Rejected Cash Scope", taxonomy_type="custom",
                primary_assignment_scope="cash_bucket", root_allocation_basis="weight",
                status="active",
            )
        )

    with engine.begin() as connection:
        connection.execute(
            sa.insert(TaxonomyRecordModel).values(
                taxonomy_id="security-scope", portfolio_id="investment-studio",
                name="Security Scope", taxonomy_type="custom",
                primary_assignment_scope="instrument", root_allocation_basis="weight",
                status="active",
            )
        )
        connection.execute(
            sa.insert(TaxonomyNodeRecordModel).values(
                taxonomy_node_id="security-node", taxonomy_id="security-scope",
                node_name="Security", sort_order=0, is_terminal=True,
                allocation_basis="weight", status="active",
            )
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.insert(TaxonomyAssignmentRecordModel).values(
                assignment_id="rejected-account-assignment", taxonomy_id="security-scope",
                target_scope="account", target_entity_id="broker-us-core",
                taxonomy_node_id="security-node", status="active",
            )
        )

    with engine.begin() as connection:
        connection.execute(
            sa.insert(TargetSetRecordModel).values(
                target_set_id="security-targets", taxonomy_id="security-scope",
                target_set_type="saa", name="Security Targets", status="active",
            )
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.insert(TargetSetLineRecordModel).values(
                target_line_id="rejected-account-target", target_set_id="security-targets",
                target_member_type="account", target_member_id="broker-us-core",
                target_value=0.1,
            )
        )

    with pytest.raises(RuntimeError, match="(?i)restore the pre-migration.*backup"):
        command.downgrade(config, "20260810_0047")
