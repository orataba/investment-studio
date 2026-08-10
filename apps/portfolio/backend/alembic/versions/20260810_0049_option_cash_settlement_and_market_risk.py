"""Normalize option outcomes to expiry or cash settlement.

Revision ID: 20260810_0049
Revises: 20260810_0048
"""

from __future__ import annotations

from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_0049"
down_revision: str | None = "20260810_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LIFECYCLE_CHECK_NAME = "lifecycle_event_type"
LIFECYCLE_CHECK = (
    "lifecycle_event_type IS NULL OR lifecycle_event_type IN ("
    "'fcn_knock_in', 'fcn_knock_out', 'fcn_maturity', "
    "'option_long_expiry', 'option_long_cash_settlement', "
    "'option_writer_expiry', 'option_writer_cash_settlement')"
)


def _preflight_legacy_option_events(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT transaction_id FROM transaction_record "
            "WHERE lifecycle_event_type IN "
            "('option_long_exercise', 'option_assignment') "
            "ORDER BY transaction_id"
        )
    ).mappings().all()
    legacy_ids = [str(row["transaction_id"]) for row in rows]
    if legacy_ids:
        raise RuntimeError(
            "Option cash-settlement migration does not infer settlement facts "
            "from legacy exercise or assignment rows. Remove these ambiguous "
            "rows before upgrading, then recreate the reviewed cash-settlement "
            "facts and any independent physical-delivery stock trades after "
            "the new lifecycle values are available: "
            + ", ".join(legacy_ids[:20])
        )


def _preflight_ambiguous_option_closures(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT txn.transaction_id "
            "FROM transaction_record txn "
            "JOIN derivative_contract_record contract "
            "  ON contract.portfolio_id = txn.portfolio_id "
            " AND contract.derivative_contract_id = "
            "     txn.derivative_contract_id "
            "WHERE contract.contract_type = 'option' "
            "  AND ("
            "      (txn.transaction_type = 'maturity_redemption' "
            "       AND coalesce(txn.lifecycle_event_type, '') "
            "           <> 'option_long_expiry') "
            "   OR (txn.transaction_type = 'lifecycle_event' "
            "       AND coalesce(txn.lifecycle_event_type, '') "
            "           <> 'option_writer_expiry') "
            "   OR (txn.lifecycle_event_type = 'option_long_expiry' "
            "       AND txn.transaction_type <> 'maturity_redemption') "
            "   OR (txn.lifecycle_event_type = 'option_writer_expiry' "
            "       AND txn.transaction_type <> 'lifecycle_event')"
            "  ) "
            "ORDER BY txn.transaction_id"
        )
    ).mappings().all()
    ambiguous_ids = [str(row["transaction_id"]) for row in rows]
    if ambiguous_ids:
        raise RuntimeError(
            "Option cash-settlement migration requires every retained option "
            "closure to be an explicit long or writer expiry. Remove ambiguous "
            "closures before upgrading and recreate reviewed cash settlements "
            "afterward: "
            + ", ".join(ambiguous_ids[:20])
        )


def _strip_option_settlement_terms(connection: sa.Connection) -> None:
    contracts = sa.table(
        "derivative_contract_record",
        sa.column("portfolio_id", sa.String()),
        sa.column("derivative_contract_id", sa.String()),
        sa.column("contract_type", sa.String()),
        sa.column("terms_json", sa.JSON()),
    )
    rows = connection.execute(
        sa.select(
            contracts.c.portfolio_id,
            contracts.c.derivative_contract_id,
            contracts.c.terms_json,
        ).where(contracts.c.contract_type == "option")
    ).mappings()
    for row in rows:
        terms = row["terms_json"]
        if isinstance(terms, str):
            try:
                terms = json.loads(terms)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "Option contract terms_json is not valid JSON: "
                    f"{row['portfolio_id']}/{row['derivative_contract_id']}."
                ) from error
        if not isinstance(terms, dict):
            raise RuntimeError(
                "Option contract terms_json must be an object: "
                f"{row['portfolio_id']}/{row['derivative_contract_id']}."
            )
        normalized_terms = dict(terms)
        if "settlement_type" not in normalized_terms:
            continue
        normalized_terms.pop("settlement_type")
        connection.execute(
            sa.update(contracts)
            .where(
                contracts.c.portfolio_id == row["portfolio_id"],
                contracts.c.derivative_contract_id
                == row["derivative_contract_id"],
            )
            .values(terms_json=normalized_terms)
        )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    _preflight_legacy_option_events(connection)
    _preflight_ambiguous_option_closures(connection)
    _strip_option_settlement_terms(connection)

    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.drop_constraint(LIFECYCLE_CHECK_NAME, type_="check")
        batch_op.create_check_constraint(
            LIFECYCLE_CHECK_NAME,
            LIFECYCLE_CHECK,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20260810_0049 removes option settlement mode and normalizes "
        "lifecycle facts. Restore the pre-migration database backup instead."
    )
