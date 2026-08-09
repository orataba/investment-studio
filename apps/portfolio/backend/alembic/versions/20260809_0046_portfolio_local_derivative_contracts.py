"""Store FCN and option contracts inside Portfolio.

The migration is intentionally a clean cut. It refuses legacy derivative
instruments instead of manufacturing local contracts from incomplete Registry
records.

Revision ID: 20260809_0046
Revises: 20260809_0045
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260809_0046"
down_revision = "20260809_0045"
branch_labels = None
depends_on = None


def _is_legacy_derivative_reference(value: object) -> bool:
    return (
        isinstance(value, dict)
        and str(value.get("instrument_type") or "").strip().lower()
        in {"fcn", "option"}
    )


def _preflight(connection: sa.Connection) -> None:
    transaction_rows = connection.execute(
        sa.text(
            "SELECT transaction_id, instrument_ref_json FROM transaction_record "
            "WHERE instrument_id IS NOT NULL"
        )
    ).mappings()
    legacy_transaction_ids = [
        str(row["transaction_id"])
        for row in transaction_rows
        if _is_legacy_derivative_reference(row["instrument_ref_json"])
    ]
    holding_rows = connection.execute(
        sa.text(
            "SELECT portfolio_id, as_of_date, account_id, instrument_id, "
            "holding_json FROM portfolio_daily_holding_snapshot"
        )
    ).mappings()
    legacy_holding_ids = [
        (
            f"{row['portfolio_id']}/{row['as_of_date']}/"
            f"{row['account_id']}/{row['instrument_id']}"
        )
        for row in holding_rows
        if _is_legacy_derivative_reference(
            (row["holding_json"] or {}).get("instrument_ref")
            if isinstance(row["holding_json"], dict)
            else None
        )
    ]
    if legacy_transaction_ids or legacy_holding_ids:
        details = ", ".join(
            [
                *(f"transaction:{value}" for value in legacy_transaction_ids[:10]),
                *(f"holding:{value}" for value in legacy_holding_ids[:10]),
            ]
        )
        raise RuntimeError(
            "Portfolio-local derivative migration requires zero legacy "
            "Registry-backed FCN/option facts. Resolve these rows first: "
            + details
        )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    _preflight(connection)

    with op.batch_alter_table("account_record") as batch_op:
        batch_op.create_unique_constraint(
            "uq_account_record_portfolio_account_id",
            ["portfolio_id", "account_id"],
        )

    op.create_table(
        "derivative_contract_record",
        sa.Column("derivative_contract_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("contract_name", sa.String(), nullable=False),
        sa.Column("contract_type", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("external_reference", sa.String(length=200)),
        sa.Column("terms_json", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "contract_type IN ('fcn', 'option')",
            name=op.f("ck_derivative_contract_record_contract_type"),
        ),
        sa.CheckConstraint(
            "currency = upper(trim(currency)) AND length(currency) BETWEEN 1 AND 8",
            name=op.f("ck_derivative_contract_record_currency"),
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            name=op.f(
                "fk_derivative_contract_record_portfolio_id_portfolio_record"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id", "account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_derivative_contract_portfolio_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "portfolio_id",
            "derivative_contract_id",
            name=op.f("pk_derivative_contract_record"),
        ),
        sa.UniqueConstraint(
            "portfolio_id",
            "external_reference",
            name="uq_derivative_contract_external_reference",
        ),
    )
    op.create_index(
        "ix_derivative_contract_portfolio_account_type",
        "derivative_contract_record",
        ["portfolio_id", "account_id", "contract_type"],
    )

    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.add_column(
            sa.Column("derivative_contract_id", sa.String(), nullable=True)
        )
        batch_op.create_check_constraint(
            "single_asset_reference",
            "NOT (instrument_id IS NOT NULL AND derivative_contract_id IS NOT NULL)",
        )
        batch_op.create_foreign_key(
            "fk_transaction_derivative_contract_portfolio",
            "derivative_contract_record",
            ["portfolio_id", "derivative_contract_id"],
            ["portfolio_id", "derivative_contract_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_transaction_record_portfolio_derivative_contract_trade",
            [
                "portfolio_id",
                "derivative_contract_id",
                "trade_date",
                "trade_at",
            ],
        )

    with op.batch_alter_table("portfolio_daily_holding_snapshot") as batch_op:
        batch_op.add_column(
            sa.Column("position_reference_id", sa.String(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("derivative_contract_id", sa.String(), nullable=True)
        )
    op.execute(
        "UPDATE portfolio_daily_holding_snapshot "
        "SET position_reference_id = instrument_id"
    )
    with op.batch_alter_table("portfolio_daily_holding_snapshot") as batch_op:
        batch_op.drop_constraint(
            op.f("pk_portfolio_daily_holding_snapshot"),
            type_="primary",
        )
        batch_op.alter_column(
            "position_reference_id",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.alter_column(
            "instrument_id",
            existing_type=sa.String(),
            nullable=True,
        )
        batch_op.create_primary_key(
            op.f("pk_portfolio_daily_holding_snapshot"),
            [
                "portfolio_id",
                "as_of_date",
                "account_id",
                "position_reference_id",
                "holding_kind",
            ],
        )
        batch_op.create_check_constraint(
            "single_asset_reference",
            "(instrument_id IS NOT NULL) <> "
            "(derivative_contract_id IS NOT NULL)",
        )
        batch_op.create_foreign_key(
            "fk_daily_holding_derivative_contract_portfolio",
            "derivative_contract_record",
            ["portfolio_id", "derivative_contract_id"],
            ["portfolio_id", "derivative_contract_id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            "ix_portfolio_daily_holding_derivative_date",
            ["portfolio_id", "derivative_contract_id", "as_of_date"],
        )


def downgrade() -> None:
    raise RuntimeError(
        "Portfolio-local derivative contracts are canonical financial facts. "
        "Restore the pre-migration database backup instead of downgrading."
    )
