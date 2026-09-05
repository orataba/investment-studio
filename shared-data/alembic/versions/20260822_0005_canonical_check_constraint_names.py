"""Canonicalize Platform CHECK constraint names.

Revision ID: 20260822_0005
Revises: 20260818_0004
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_0005"
down_revision: str | None = "20260818_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CANONICAL_CHECK_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "email_mailbox_ingestion_lease": (
        "ck_email_mailbox_ingestion_lease_lease_state_contract",
    ),
    "fmp_equity_catalog": (
        "ck_fmp_equity_catalog_exchange_code_contract",
        "ck_fmp_equity_catalog_currency_contract",
    ),
    "fmp_etf_catalog": (
        "ck_fmp_etf_catalog_exchange_code_contract",
        "ck_fmp_etf_catalog_currency_contract",
    ),
    "fund_nav_raw_observation": (
        "ck_fund_nav_raw_observation_total_return_semantics",
        "ck_fund_nav_raw_observation_has_value",
        "ck_fund_nav_raw_observation_unit_nav_status",
        "ck_fund_nav_raw_observation_cash_cumulative_nav_status",
        "ck_fund_nav_raw_observation_total_return_nav_status",
    ),
    "fund_nav_action_candidate": (
        "ck_fund_nav_action_candidate_candidate_type_contract",
        "ck_fund_nav_action_candidate_ordered_interval",
        "ck_fund_nav_action_candidate_candidate_delta_contract",
        "ck_fund_nav_action_candidate_positive_measurement_uncertainty",
        "ck_fund_nav_action_candidate_status_contract",
        "ck_fund_nav_action_candidate_resolution_contract",
        "ck_fund_nav_action_candidate_rejection_contract",
        "ck_fund_nav_action_candidate_decision_audit_contract",
        "ck_fund_nav_action_candidate_confirmation_intent_contract",
        "ck_fund_nav_action_candidate_identity_contract",
        "ck_fund_nav_action_candidate_evidence_contract",
    ),
}


def _legacy_constraint_name(
    *,
    bind: sa.engine.Connection,
    table_name: str,
    canonical_name: str,
) -> str:
    duplicated_name = f"ck_{table_name}_{canonical_name}"
    return bind.dialect.identifier_preparer.truncate_and_render_constraint_name(
        op.f(duplicated_name)
    )


def _rename_constraint(
    *,
    table_name: str,
    old_name: str,
    new_name: str,
    schema: str | None,
) -> None:
    preparer = op.get_bind().dialect.identifier_preparer
    qualified_table = preparer.quote(table_name)
    if schema:
        qualified_table = f"{preparer.quote(schema)}.{qualified_table}"
    op.execute(
        sa.text(
            f"ALTER TABLE {qualified_table} "
            f"RENAME CONSTRAINT {preparer.quote(old_name)} TO {preparer.quote(new_name)}"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    schema = op.get_context().version_table_schema
    inspector = sa.inspect(bind)

    for table_name, canonical_names in CANONICAL_CHECK_CONSTRAINTS.items():
        existing_names = {
            str(item["name"])
            for item in inspector.get_check_constraints(table_name, schema=schema)
            if item.get("name")
        }
        for canonical_name in canonical_names:
            if canonical_name in existing_names:
                continue
            legacy_name = _legacy_constraint_name(
                bind=bind,
                table_name=table_name,
                canonical_name=canonical_name,
            )
            if legacy_name not in existing_names:
                raise RuntimeError(
                    f"Missing CHECK constraint {canonical_name!r} on {table_name!r}."
                )
            if bind.dialect.name != "postgresql":
                raise RuntimeError(
                    "Renaming legacy Platform CHECK constraints requires PostgreSQL."
                )
            _rename_constraint(
                table_name=table_name,
                old_name=legacy_name,
                new_name=canonical_name,
                schema=schema,
            )
            existing_names.remove(legacy_name)
            existing_names.add(canonical_name)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    schema = op.get_context().version_table_schema
    inspector = sa.inspect(bind)
    for table_name, canonical_names in CANONICAL_CHECK_CONSTRAINTS.items():
        existing_names = {
            str(item["name"])
            for item in inspector.get_check_constraints(table_name, schema=schema)
            if item.get("name")
        }
        for canonical_name in canonical_names:
            if canonical_name not in existing_names:
                continue
            legacy_name = _legacy_constraint_name(
                bind=bind,
                table_name=table_name,
                canonical_name=canonical_name,
            )
            _rename_constraint(
                table_name=table_name,
                old_name=canonical_name,
                new_name=legacy_name,
                schema=schema,
            )
            existing_names.remove(canonical_name)
            existing_names.add(legacy_name)
