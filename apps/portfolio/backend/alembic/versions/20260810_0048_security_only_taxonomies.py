"""Limit portfolio taxonomies to Registry securities.

Revision ID: 20260810_0048
Revises: 20260810_0047
"""

from __future__ import annotations

from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_0048"
down_revision: str | None = "20260810_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TARGET_MEMBER_TYPES = (
    "taxonomy_node",
    "instrument",
    "cash_bucket",
    "derivative_bucket",
)


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _preflight(connection: sa.Connection) -> None:
    taxonomy_ids = connection.execute(
        sa.text(
            "SELECT taxonomy_id FROM taxonomy_record "
            "WHERE primary_assignment_scope != 'instrument' "
            "ORDER BY taxonomy_id LIMIT 10"
        )
    ).scalars().all()
    assignment_ids = connection.execute(
        sa.text(
            "SELECT assignment_id FROM taxonomy_assignment_record "
            "WHERE target_scope != 'instrument' "
            "ORDER BY assignment_id LIMIT 10"
        )
    ).scalars().all()
    target_line_ids = connection.execute(
        sa.text(
            "SELECT target_line_id FROM target_set_line_record "
            f"WHERE target_member_type NOT IN ({_quoted(TARGET_MEMBER_TYPES)}) "
            "ORDER BY target_line_id LIMIT 10"
        )
    ).scalars().all()
    revision_ids: list[str] = []
    revision_rows = connection.execute(
        sa.text(
            "SELECT taxonomy_configuration_revision_id, configuration_json "
            "FROM taxonomy_configuration_revision"
        )
    ).mappings()
    for row in revision_rows:
        configuration = row["configuration_json"]
        if isinstance(configuration, str):
            try:
                configuration = json.loads(configuration)
            except json.JSONDecodeError:
                configuration = None
        if not isinstance(configuration, dict):
            continue
        taxonomy = configuration.get("taxonomy")
        assignments = configuration.get("taxonomy_assignments")
        target_lines = configuration.get("target_set_lines")
        has_legacy_scope = (
            isinstance(taxonomy, dict)
            and str(taxonomy.get("primary_assignment_scope") or "instrument")
            != "instrument"
        ) or any(
            isinstance(assignment, dict)
            and str(assignment.get("target_scope") or "instrument") != "instrument"
            for assignment in assignments or []
        ) or any(
            isinstance(target_line, dict)
            and str(target_line.get("target_member_type") or "taxonomy_node")
            not in TARGET_MEMBER_TYPES
            for target_line in target_lines or []
        )
        if has_legacy_scope:
            revision_ids.append(str(row["taxonomy_configuration_revision_id"]))
            if len(revision_ids) >= 10:
                break

    if taxonomy_ids or assignment_ids or target_line_ids or revision_ids:
        details = ", ".join(
            [
                *(f"taxonomy:{value}" for value in taxonomy_ids),
                *(f"assignment:{value}" for value in assignment_ids),
                *(f"target_line:{value}" for value in target_line_ids),
                *(f"revision:{value}" for value in revision_ids),
            ]
        )
        raise RuntimeError(
            "Security-only taxonomy migration requires zero account or cash-bucket "
            f"taxonomy records. Resolve these rows first: {details}"
        )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    _preflight(connection)

    with op.batch_alter_table("taxonomy_record") as batch_op:
        batch_op.create_check_constraint(
            "ck_taxonomy_record_security_scope",
            "primary_assignment_scope = 'instrument'",
        )
    with op.batch_alter_table("taxonomy_assignment_record") as batch_op:
        batch_op.create_check_constraint(
            "ck_taxonomy_assignment_security_scope",
            "target_scope = 'instrument'",
        )
    with op.batch_alter_table("target_set_line_record") as batch_op:
        batch_op.create_check_constraint(
            "ck_target_set_line_member_type",
            "target_member_type IN "
            f"({_quoted(TARGET_MEMBER_TYPES)})",
        )


def downgrade() -> None:
    raise RuntimeError(
        "Portfolio taxonomies classify Registry securities only. Restore the "
        "pre-migration database backup instead of re-enabling legacy scopes."
    )
