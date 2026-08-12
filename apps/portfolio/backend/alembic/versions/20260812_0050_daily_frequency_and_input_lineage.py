"""Use daily portfolio frequency and complete calculation-input lineage.

Revision ID: 20260812_0050
Revises: 20260810_0049
"""

from __future__ import annotations

from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_0050"
down_revision: str | None = "20260810_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize_legacy_portfolio_frequency(connection: sa.Connection) -> None:
    research_settings = sa.table(
        "research_settings_record",
        sa.column("portfolio_id", sa.String()),
        sa.column("calculation_frequency", sa.String()),
    )
    connection.execute(
        sa.update(research_settings)
        .where(research_settings.c.calculation_frequency != "daily")
        .values(calculation_frequency="daily")
    )

    portfolios = sa.table(
        "portfolio_record",
        sa.column("portfolio_id", sa.String()),
        sa.column("risk_policy_json", sa.JSON()),
    )
    for row in connection.execute(
        sa.select(portfolios.c.portfolio_id, portfolios.c.risk_policy_json)
    ).mappings():
        raw_policy = row["risk_policy_json"]
        if isinstance(raw_policy, str):
            try:
                raw_policy = json.loads(raw_policy)
            except json.JSONDecodeError:
                continue
        if not isinstance(raw_policy, dict):
            continue
        policy = dict(raw_policy)
        policy["calculation_frequency"] = "daily"
        connection.execute(
            sa.update(portfolios)
            .where(portfolios.c.portfolio_id == row["portfolio_id"])
            .values(risk_policy_json=policy)
        )


def _preflight_transaction_accounts(connection: sa.Connection) -> None:
    checks = (
        ("account_id", "main account"),
        ("settlement_cash_account_id", "settlement cash account"),
        ("counterparty_account_id", "counterparty account"),
    )
    for column_name, label in checks:
        rows = connection.execute(
            sa.text(
                "SELECT txn.transaction_id "
                "FROM transaction_record txn "
                "LEFT JOIN account_record account "
                "  ON account.portfolio_id = txn.portfolio_id "
                f" AND account.account_id = txn.{column_name} "
                f"WHERE txn.{column_name} IS NOT NULL "
                "  AND account.account_id IS NULL "
                "ORDER BY txn.transaction_id"
            )
        ).scalars().all()
        if rows:
            raise RuntimeError(
                f"Cannot add the transaction {label} foreign key while orphan facts exist: "
                + ", ".join(str(value) for value in rows[:20])
            )


def upgrade() -> None:
    connection = op.get_bind()
    _normalize_legacy_portfolio_frequency(connection)
    _preflight_transaction_accounts(connection)

    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.alter_column(
            "calculation_frequency",
            existing_type=sa.String(),
            nullable=False,
            server_default="daily",
        )
        batch_op.create_check_constraint(
            "calculation_frequency",
            "calculation_frequency = 'daily'",
        )

    with op.batch_alter_table("portfolio_calculation_state") as batch_op:
        batch_op.add_column(
            sa.Column("source_calculation_inputs_updated_at", sa.String(), nullable=True)
        )

    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.create_foreign_key(
            "fk_transaction_portfolio_account",
            "account_record",
            ["portfolio_id", "account_id"],
            ["portfolio_id", "account_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_transaction_portfolio_settlement_cash_account",
            "account_record",
            ["portfolio_id", "settlement_cash_account_id"],
            ["portfolio_id", "account_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_transaction_portfolio_counterparty_account",
            "account_record",
            ["portfolio_id", "counterparty_account_id"],
            ["portfolio_id", "account_id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20260812_0050 removes non-daily portfolio policy state and adds "
        "financial-fact integrity constraints. Restore the pre-migration database backup "
        "instead of downgrading."
    )
