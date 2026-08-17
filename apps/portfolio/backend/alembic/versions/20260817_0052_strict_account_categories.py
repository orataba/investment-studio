"""Make Cash, Security, FCN, and Option first-class account categories.

Revision ID: 20260817_0052
Revises: 20260816_0051
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260817_0052"
down_revision: str | None = "20260816_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SECURITY_TYPES = {"equity", "etf", "fund", "other"}


def _scope_categories(raw_scope: object, account_id: str) -> set[str]:
    if raw_scope is None:
        return set()
    if not isinstance(raw_scope, list):
        raise RuntimeError(
            f"Account {account_id} has a non-array allowed_instrument_types_json value."
        )
    categories: set[str] = set()
    for raw_value in raw_scope:
        instrument_type = str(raw_value or "").strip().lower()
        if not instrument_type:
            continue
        if instrument_type in _SECURITY_TYPES:
            categories.add("security")
        elif instrument_type in {"fcn", "option"}:
            categories.add(instrument_type)
        else:
            raise RuntimeError(
                f"Account {account_id} has unsupported instrument type {instrument_type!r}."
            )
    return categories


def _next_account_id(
    existing_ids: set[str],
    source_account_id: str,
    account_category: str,
) -> str:
    base = f"{source_account_id}-{account_category}"
    candidate = base
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    existing_ids.add(candidate)
    return candidate


def _account_copy_values(
    row: Mapping[str, object],
    *,
    account_id: str,
    account_category: str,
) -> dict[str, object]:
    return {
        "account_id": account_id,
        "portfolio_id": row["portfolio_id"],
        "account_name": f"{row['account_name']} · {account_category.upper()}",
        "account_type": "securities_account",
        "account_category": account_category,
        "currency": row["currency"],
        "institution": row["institution"],
        "default_settlement_cash_account_id": row[
            "default_settlement_cash_account_id"
        ],
        "cost_basis_method": row["cost_basis_method"],
        "allowed_instrument_types_json": None,
        "opened_at": row["opened_at"],
        "closed_at": row["closed_at"],
        "status": row["status"],
    }


def _invalidate_account_snapshots(
    connection: sa.engine.Connection,
    portfolio_ids: set[str],
) -> None:
    if not portfolio_ids:
        return
    for portfolio_id in sorted(portfolio_ids):
        first_date = connection.execute(
            sa.text(
                "SELECT min(candidate_date) FROM ("
                " SELECT min(trade_date) AS candidate_date"
                " FROM transaction_record WHERE portfolio_id = :portfolio_id"
                " UNION ALL"
                " SELECT min(opened_at) AS candidate_date"
                " FROM account_record WHERE portfolio_id = :portfolio_id"
                ") dates"
            ),
            {"portfolio_id": portfolio_id},
        ).scalar_one_or_none()
        connection.execute(
            sa.text(
                "DELETE FROM portfolio_daily_contribution_slice "
                "WHERE portfolio_id = :portfolio_id"
            ),
            {"portfolio_id": portfolio_id},
        )
        connection.execute(
            sa.text(
                "DELETE FROM portfolio_daily_holding_snapshot "
                "WHERE portfolio_id = :portfolio_id"
            ),
            {"portfolio_id": portfolio_id},
        )
        connection.execute(
            sa.text(
                "DELETE FROM portfolio_daily_snapshot WHERE portfolio_id = :portfolio_id"
            ),
            {"portfolio_id": portfolio_id},
        )
        connection.execute(
            sa.text(
                "UPDATE portfolio_calculation_state "
                "SET daily_snapshot_status = 'stale', "
                "    dirty_from = :dirty_from, "
                "    refreshed_from = NULL, "
                "    refreshed_to = NULL, "
                "    refreshed_at = NULL, "
                "    error_message = NULL "
                "WHERE portfolio_id = :portfolio_id"
            ),
            {"portfolio_id": portfolio_id, "dirty_from": first_date},
        )


def _validate_cash_mappings(connection: sa.engine.Connection) -> None:
    invalid_rows = connection.execute(
        sa.text(
            "SELECT holding.account_id "
            "FROM account_record holding "
            "LEFT JOIN account_record cash "
            "  ON cash.portfolio_id = holding.portfolio_id "
            " AND cash.account_id = holding.default_settlement_cash_account_id "
            "WHERE holding.account_category IN ('security', 'fcn', 'option') "
            "  AND (cash.account_id IS NULL "
            "       OR cash.account_category <> 'cash' "
            "       OR upper(cash.currency) <> upper(holding.currency)) "
            "ORDER BY holding.account_id"
        )
    ).scalars().all()
    if invalid_rows:
        raise RuntimeError(
            "Holding accounts require a same-currency Cash settlement account: "
            + ", ".join(str(value) for value in invalid_rows[:20])
        )


def upgrade() -> None:
    connection = op.get_bind()
    with op.batch_alter_table("account_record") as batch_op:
        batch_op.add_column(sa.Column("account_category", sa.String(), nullable=True))

    accounts = sa.table(
        "account_record",
        sa.column("account_id", sa.String()),
        sa.column("portfolio_id", sa.String()),
        sa.column("account_name", sa.String()),
        sa.column("account_type", sa.String()),
        sa.column("account_category", sa.String()),
        sa.column("currency", sa.String()),
        sa.column("institution", sa.String()),
        sa.column("default_settlement_cash_account_id", sa.String()),
        sa.column("cost_basis_method", sa.String()),
        sa.column("allowed_instrument_types_json", sa.JSON()),
        sa.column("opened_at", sa.Date()),
        sa.column("closed_at", sa.Date()),
        sa.column("status", sa.String()),
    )
    contracts = sa.table(
        "derivative_contract_record",
        sa.column("portfolio_id", sa.String()),
        sa.column("derivative_contract_id", sa.String()),
        sa.column("account_id", sa.String()),
        sa.column("contract_type", sa.String()),
    )
    transactions = sa.table(
        "transaction_record",
        sa.column("portfolio_id", sa.String()),
        sa.column("account_id", sa.String()),
        sa.column("counterparty_account_id", sa.String()),
        sa.column("instrument_id", sa.String()),
        sa.column("derivative_contract_id", sa.String()),
    )

    account_rows = connection.execute(sa.select(accounts)).mappings().all()
    existing_ids = {str(row["account_id"]) for row in account_rows}
    split_portfolios: set[str] = set()

    for row in account_rows:
        account_id = str(row["account_id"])
        portfolio_id = str(row["portfolio_id"])
        account_type = str(row["account_type"] or "").strip().lower()
        if account_type == "deposit_account":
            if _scope_categories(row["allowed_instrument_types_json"], account_id):
                raise RuntimeError(
                    f"Cash account {account_id} must not carry an instrument scope."
                )
            connection.execute(
                sa.update(accounts)
                .where(accounts.c.account_id == account_id)
                .values(account_category="cash")
            )
            continue
        if account_type != "securities_account":
            raise RuntimeError(
                f"Account {account_id} has unsupported account type {account_type!r}."
            )

        categories = _scope_categories(
            row["allowed_instrument_types_json"], account_id
        )
        security_fact = connection.execute(
            sa.select(sa.literal(1))
            .select_from(transactions)
            .where(
                transactions.c.portfolio_id == portfolio_id,
                sa.or_(
                    transactions.c.account_id == account_id,
                    transactions.c.counterparty_account_id == account_id,
                ),
                transactions.c.instrument_id.is_not(None),
            )
            .limit(1)
        ).first()
        if security_fact is not None:
            categories.add("security")
        contract_types = {
            str(value).strip().lower()
            for value in connection.execute(
                sa.select(contracts.c.contract_type).where(
                    contracts.c.portfolio_id == portfolio_id,
                    contracts.c.account_id == account_id,
                )
            ).scalars()
        }
        if not contract_types.issubset({"fcn", "option"}):
            raise RuntimeError(f"Account {account_id} has an unsupported derivative contract.")
        categories.update(contract_types)
        if not categories:
            categories.add("security")

        primary_category = next(
            category
            for category in ("security", "fcn", "option")
            if category in categories
        )
        connection.execute(
            sa.update(accounts)
            .where(accounts.c.account_id == account_id)
            .values(account_category=primary_category)
        )

        for category in ("security", "fcn", "option"):
            if category not in categories or category == primary_category:
                continue
            new_account_id = _next_account_id(existing_ids, account_id, category)
            connection.execute(
                sa.insert(accounts).values(
                    **_account_copy_values(
                        row,
                        account_id=new_account_id,
                        account_category=category,
                    )
                )
            )
            split_portfolios.add(portfolio_id)
            if category not in {"fcn", "option"}:
                continue
            moved_contract_ids = connection.execute(
                sa.select(contracts.c.derivative_contract_id).where(
                    contracts.c.portfolio_id == portfolio_id,
                    contracts.c.account_id == account_id,
                    contracts.c.contract_type == category,
                )
            ).scalars().all()
            if not moved_contract_ids:
                continue
            connection.execute(
                sa.update(contracts)
                .where(
                    contracts.c.portfolio_id == portfolio_id,
                    contracts.c.account_id == account_id,
                    contracts.c.contract_type == category,
                )
                .values(account_id=new_account_id)
            )
            connection.execute(
                sa.update(transactions)
                .where(
                    transactions.c.portfolio_id == portfolio_id,
                    transactions.c.derivative_contract_id.in_(moved_contract_ids),
                    transactions.c.account_id == account_id,
                )
                .values(account_id=new_account_id)
            )
            connection.execute(
                sa.update(transactions)
                .where(
                    transactions.c.portfolio_id == portfolio_id,
                    transactions.c.derivative_contract_id.in_(moved_contract_ids),
                    transactions.c.counterparty_account_id == account_id,
                )
                .values(counterparty_account_id=new_account_id)
            )

    _validate_cash_mappings(connection)
    _invalidate_account_snapshots(connection, split_portfolios)

    with op.batch_alter_table("account_record") as batch_op:
        batch_op.alter_column(
            "account_category",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "account_category",
            "account_category IN ('cash', 'security', 'fcn', 'option')",
        )
        batch_op.create_check_constraint(
            "account_type_category",
            "(account_type = 'deposit_account' AND account_category = 'cash') OR "
            "(account_type = 'securities_account' "
            "AND account_category IN ('security', 'fcn', 'option'))",
        )
        batch_op.create_check_constraint(
            "account_settlement_contract",
            "(account_category = 'cash' "
            "AND default_settlement_cash_account_id IS NULL "
            "AND cost_basis_method IS NULL) OR "
            "(account_category IN ('security', 'fcn', 'option') "
            "AND default_settlement_cash_account_id IS NOT NULL "
            "AND cost_basis_method IN ('fifo', 'moving_average'))",
        )
        batch_op.create_foreign_key(
            "fk_account_default_settlement_cash_account",
            "account_record",
            ["portfolio_id", "default_settlement_cash_account_id"],
            ["portfolio_id", "account_id"],
            ondelete="RESTRICT",
        )
        batch_op.drop_column("allowed_instrument_types_json")
        batch_op.drop_index("ix_account_record_portfolio_type_currency")
        batch_op.create_index(
            "ix_account_record_portfolio_category_currency",
            ["portfolio_id", "account_category", "currency"],
            unique=False,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20260817_0052 splits mixed holding accounts and removes the old "
        "instrument-scope field. Restore the pre-migration database backup instead "
        "of downgrading."
    )
