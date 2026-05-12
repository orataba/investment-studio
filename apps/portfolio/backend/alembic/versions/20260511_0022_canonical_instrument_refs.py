"""canonicalize instrument references

Revision ID: 20260511_0022
Revises: 20260510_0021
Create Date: 2026-05-11 11:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260511_0022"
down_revision = "20260510_0021"
branch_labels = None
depends_on = None


def _canonicalize_mapping(value: dict[str, object]) -> dict[str, object]:
    canonical: dict[str, object] = {}
    for key, item in value.items():
        if key == "asset_id":
            canonical["instrument_id"] = _canonicalize_value(item)
        elif key == "asset_name":
            canonical["instrument_name"] = _canonicalize_value(item)
        elif key == "asset_type":
            canonical["instrument_type"] = _canonicalize_value(item)
        elif key == "allowed_asset_types":
            canonical["allowed_instrument_types"] = _canonicalize_value(item)
        else:
            canonical[key] = _canonicalize_value(item)
    return canonical


def _canonicalize_value(value: object) -> object:
    if isinstance(value, dict):
        return _canonicalize_mapping(value)
    if isinstance(value, list):
        return [_canonicalize_value(item) for item in value]
    return value


def upgrade() -> None:
    bind = op.get_bind()
    transaction_record = sa.table(
        "transaction_record",
        sa.column("transaction_id", sa.String()),
        sa.column("instrument_ref_json", sa.JSON()),
    )
    research_run_record = sa.table(
        "research_run_record",
        sa.column("research_run_id", sa.String()),
        sa.column("detail_json", sa.JSON()),
    )

    for row in bind.execute(
        sa.select(transaction_record.c.transaction_id, transaction_record.c.instrument_ref_json)
    ).mappings():
        instrument_ref = row["instrument_ref_json"]
        if not isinstance(instrument_ref, dict):
            continue
        canonical_ref = _canonicalize_mapping(instrument_ref)
        if canonical_ref != instrument_ref:
            bind.execute(
                transaction_record.update()
                .where(transaction_record.c.transaction_id == row["transaction_id"])
                .values(instrument_ref_json=canonical_ref)
            )

    for row in bind.execute(
        sa.select(research_run_record.c.research_run_id, research_run_record.c.detail_json)
    ).mappings():
        detail = row["detail_json"]
        if not isinstance(detail, dict):
            continue
        canonical_detail = _canonicalize_mapping(detail)
        if canonical_detail != detail:
            bind.execute(
                research_run_record.update()
                .where(research_run_record.c.research_run_id == row["research_run_id"])
                .values(detail_json=canonical_detail)
            )


def downgrade() -> None:
    pass
