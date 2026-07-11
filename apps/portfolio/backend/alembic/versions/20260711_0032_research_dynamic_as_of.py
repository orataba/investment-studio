"""Make current Research follow the latest complete portfolio date.

Revision ID: 20260711_0032
Revises: 20260711_0031

The legacy settings row persisted a resolved date even when the user intended
"current" Research.  That silently froze weights, prices, and risk inputs.
Existing rows are migrated to dynamic mode; historical analysis now requires
an explicit pinned mode.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260711_0032"
down_revision: str | None = "20260711_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sync_etf_denormalized_references() -> None:
    """Keep Portfolio account scopes and stored refs valid after ETF typing."""

    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if not inspector.has_table("instrument"):
        return

    instrument = sa.table(
        "instrument",
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_type", sa.String()),
    )
    etf_ids = {
        str(instrument_id)
        for instrument_id in connection.execute(
            sa.select(instrument.c.instrument_id).where(instrument.c.instrument_type == "etf")
        ).scalars()
    }
    if not etf_ids:
        return

    account = sa.table(
        "account_record",
        sa.column("account_id", sa.String()),
        sa.column("allowed_instrument_types_json", sa.JSON()),
    )
    for account_id, raw_types in connection.execute(
        sa.select(account.c.account_id, account.c.allowed_instrument_types_json)
    ):
        if not isinstance(raw_types, list) or "fund" not in raw_types or "etf" in raw_types:
            continue
        connection.execute(
            sa.update(account)
            .where(account.c.account_id == account_id)
            .values(allowed_instrument_types_json=[*raw_types, "etf"])
        )

    for table_name, key_name in (
        ("transaction_record", "transaction_id"),
        ("portfolio_instrument_universe_record", "portfolio_id"),
    ):
        if not inspector.has_table(table_name):
            continue
        table = sa.table(
            table_name,
            sa.column(key_name, sa.String()),
            sa.column("instrument_id", sa.String()),
            sa.column("instrument_ref_json", sa.JSON()),
        )
        rows = connection.execute(
            sa.select(table.c[key_name], table.c.instrument_id, table.c.instrument_ref_json).where(
                table.c.instrument_id.in_(etf_ids)
            )
        ).all()
        for row_key, _instrument_id, raw_ref in rows:
            if not isinstance(raw_ref, dict) or raw_ref.get("instrument_type") == "etf":
                continue
            updated_ref = dict(raw_ref)
            updated_ref["instrument_type"] = "etf"
            # Universe rows use a composite key.  instrument_id is included in
            # the predicate so the update remains single-row for that table.
            connection.execute(
                sa.update(table)
                .where(table.c[key_name] == row_key)
                .where(table.c.instrument_id == _instrument_id)
                .values(instrument_ref_json=updated_ref)
            )


def upgrade() -> None:
    op.add_column(
        "research_settings_record",
        sa.Column(
            "as_of_mode",
            sa.String(),
            nullable=False,
            server_default="dynamic",
        ),
    )
    # There was no explicit historical/pinned mode before this migration, so
    # carrying the stored date forward would preserve the silent-staleness bug.
    op.execute("UPDATE research_settings_record SET as_of_date = NULL, as_of_mode = 'dynamic'")
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.create_check_constraint(
            "ck_research_settings_as_of_mode",
            "as_of_mode IN ('dynamic', 'pinned')",
        )
    _sync_etf_denormalized_references()


def downgrade() -> None:
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.drop_constraint(
            "ck_research_settings_as_of_mode",
            type_="check",
        )
    op.drop_column("research_settings_record", "as_of_mode")
