"""Remove derivative event grouping from transaction facts.

Revision ID: 20260809_0045
Revises: 20260807_0044
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260809_0045"
down_revision = "20260807_0044"
branch_labels = None
depends_on = None


LIFECYCLE_CHECK_NAME = "lifecycle_event_type"
EVENT_GROUP_INDEX = "ix_transaction_record_portfolio_event_group"
RELATED_INSTRUMENT_INDEX = "ix_transaction_record_portfolio_related_instrument"

CURRENT_LIFECYCLE_CHECK = (
    "lifecycle_event_type IS NULL OR lifecycle_event_type IN ("
    "'fcn_knock_in', 'fcn_knock_out', 'fcn_maturity', "
    "'option_long_expiry', 'option_long_exercise', 'option_writer_expiry', "
    "'option_assignment')"
)


def _preflight_fcn_delivery_groups() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT transaction_id, portfolio_id, account_id, currency, "
            "event_group_id, transaction_type, lifecycle_event_type, gross_amount "
            "FROM transaction_record WHERE event_group_id IS NOT NULL"
        )
    ).mappings().all()
    lifecycle_rows = [
        row for row in rows if row["lifecycle_event_type"] == "fcn_physical_settlement"
    ]
    groups: dict[tuple[object, str], list[object]] = {}
    for row in lifecycle_rows:
        event_group_id = str(row["event_group_id"] or "").strip()
        if not event_group_id:
            raise RuntimeError(
                "Cannot migrate an FCN physical settlement without its delivery group."
            )
        groups.setdefault((row["portfolio_id"], event_group_id), []).append(row)

    for (portfolio_id, event_group_id), lifecycle_group in groups.items():
        if len(lifecycle_group) != 1:
            raise RuntimeError(
                "Cannot migrate an FCN delivery group unless it has exactly one "
                f"physical-settlement fact: {portfolio_id}/{event_group_id}."
            )
        delivery_rows = [
            row
            for row in rows
            if row["portfolio_id"] == portfolio_id
            and str(row["event_group_id"] or "").strip() == event_group_id
            and row["transaction_type"] == "buy"
        ]
        if len(delivery_rows) != 1 or float(delivery_rows[0]["gross_amount"] or 0) <= 0:
            raise RuntimeError(
                "Cannot migrate an FCN physical settlement without exactly one "
                f"positive delivered-asset buy: {portfolio_id}/{event_group_id}."
            )
        lifecycle_row = lifecycle_group[0]
        delivery_row = delivery_rows[0]
        if (
            str(lifecycle_row["currency"] or "").strip().upper()
            != str(delivery_row["currency"] or "").strip().upper()
            or lifecycle_row["account_id"] != delivery_row["account_id"]
        ):
            raise RuntimeError(
                "Cannot copy delivered-asset cash amounts across currencies or accounts: "
                f"{portfolio_id}/{event_group_id}."
            )
        has_calculation_state = connection.execute(
            sa.text(
                "SELECT 1 FROM portfolio_calculation_state "
                "WHERE portfolio_id = :portfolio_id"
            ),
            {"portfolio_id": portfolio_id},
        ).first()
        if has_calculation_state is None:
            raise RuntimeError(
                "Cannot invalidate calculated projections because portfolio calculation "
                f"state is missing: {portfolio_id}."
            )


def _convert_fcn_delivery_groups() -> None:
    """Turn each legacy FCN delivery pair into two independent cash facts."""

    connection = op.get_bind()
    lifecycle_rows = connection.execute(
        sa.text(
            "SELECT transaction_id, portfolio_id, event_group_id "
            "FROM transaction_record "
            "WHERE lifecycle_event_type = 'fcn_physical_settlement'"
        )
    ).mappings().all()
    for lifecycle_row in lifecycle_rows:
        event_group_id = str(lifecycle_row["event_group_id"] or "").strip()
        if not event_group_id:
            raise RuntimeError(
                "Cannot migrate an FCN physical settlement without its delivery group."
            )
        delivery_rows = connection.execute(
            sa.text(
                "SELECT gross_amount, source_gross_amount "
                "FROM transaction_record "
                "WHERE portfolio_id = :portfolio_id "
                "AND event_group_id = :event_group_id "
                "AND transaction_type = 'buy'"
            ),
            {
                "portfolio_id": lifecycle_row["portfolio_id"],
                "event_group_id": event_group_id,
            },
        ).mappings().all()
        if len(delivery_rows) != 1 or float(delivery_rows[0]["gross_amount"] or 0) <= 0:
            raise RuntimeError(
                "Cannot migrate an FCN physical settlement without exactly one "
                "positive delivered-asset buy."
            )
        delivery_row = delivery_rows[0]
        source_gross_amount = (
            delivery_row["source_gross_amount"]
            if delivery_row["source_gross_amount"] is not None
            else delivery_row["gross_amount"]
        )
        connection.execute(
            sa.text(
                "UPDATE transaction_record "
                "SET lifecycle_event_type = 'fcn_knock_in', "
                "gross_amount = :gross_amount, "
                "source_gross_amount = :source_gross_amount "
                "WHERE transaction_id = :transaction_id"
            ),
            {
                "gross_amount": delivery_row["gross_amount"],
                "source_gross_amount": source_gross_amount,
                "transaction_id": lifecycle_row["transaction_id"],
            },
        )


def _invalidate_affected_snapshots() -> None:
    """Rebuild projections that were calculated with grouped-event semantics."""

    connection = op.get_bind()
    affected_rows = connection.execute(
        sa.text(
            "SELECT portfolio_id, MIN(trade_date) AS dirty_from "
            "FROM transaction_record "
            "WHERE event_group_id IS NOT NULL "
            "AND lifecycle_event_type IN ("
            "'fcn_knock_in', 'option_long_exercise', 'option_assignment'"
            ") GROUP BY portfolio_id"
        )
    ).mappings().all()
    for affected in affected_rows:
        connection.execute(
            sa.text(
                "UPDATE portfolio_calculation_state SET "
                "daily_snapshot_status = CASE "
                "WHEN daily_snapshot_status = 'running' THEN 'running' "
                "ELSE 'stale' END, "
                "dirty_from = CASE "
                "WHEN daily_snapshot_status IN ('stale', 'running') "
                "AND dirty_from IS NULL THEN NULL "
                "WHEN dirty_from IS NULL OR dirty_from > :dirty_from "
                "THEN :dirty_from ELSE dirty_from END, "
                "error_message = NULL "
                "WHERE portfolio_id = :portfolio_id"
            ),
            {
                "portfolio_id": affected["portfolio_id"],
                "dirty_from": affected["dirty_from"],
            },
        )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    _preflight_fcn_delivery_groups()
    _convert_fcn_delivery_groups()
    _invalidate_affected_snapshots()
    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.drop_index(RELATED_INSTRUMENT_INDEX)
        batch_op.drop_index(EVENT_GROUP_INDEX)
        batch_op.drop_constraint(LIFECYCLE_CHECK_NAME, type_="check")
        batch_op.drop_column("related_instrument_id")
        batch_op.drop_column("event_group_id")
        batch_op.create_check_constraint(
            LIFECYCLE_CHECK_NAME,
            CURRENT_LIFECYCLE_CHECK,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20260809_0045 rewrites financial facts and drops their grouping lineage. "
        "Restore the four-schema pre-migration backup; Alembic downgrade cannot recover "
        "the original lifecycle events, cash amounts, or relationships."
    )
