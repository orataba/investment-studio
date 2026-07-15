"""Split daily snapshot coverage into independent reliability dimensions.

Revision ID: 20260715_0034
Revises: 20260715_0033

Existing rows are conservatively backfilled from the legacy aggregate
``coverage_state``.  The next materialized refresh recomputes each dimension
independently under the new calculation version.  ``coverage_state`` remains
the explicit aggregate operational-health dimension; valuation and return
consumers use their dedicated dimensions.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0034"
down_revision: str | None = "20260715_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = "portfolio_daily_snapshot"
INDEX_NAME = "ix_portfolio_daily_snapshot_portfolio_valuation_coverage"
COVERAGE_COLUMNS = (
    "valuation_coverage_state",
    "return_coverage_state",
    "book_pnl_coverage_state",
    "attribution_coverage_state",
)
VALID_COVERAGE_STATES = ("complete", "partial", "unavailable")


daily_snapshot = sa.table(
    TABLE_NAME,
    sa.column("coverage_state", sa.String()),
    *(sa.column(column_name, sa.String()) for column_name in COVERAGE_COLUMNS),
)
calculation_state = sa.table(
    "portfolio_calculation_state",
    sa.column("daily_snapshot_status", sa.String()),
    sa.column("error_message", sa.String()),
)


def upgrade() -> None:
    connection = op.get_bind()
    invalid_states = [
        str(value)
        for value in connection.scalars(
            sa.select(daily_snapshot.c.coverage_state)
            .distinct()
            .where(
                sa.or_(
                    daily_snapshot.c.coverage_state.is_(None),
                    daily_snapshot.c.coverage_state.not_in(VALID_COVERAGE_STATES),
                )
            )
        )
    ]
    if invalid_states:
        raise RuntimeError(
            "Snapshot coverage migration found invalid legacy coverage_state values: "
            + ", ".join(sorted(invalid_states))
        )

    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column_name in COVERAGE_COLUMNS:
            batch_op.add_column(
                sa.Column(
                    column_name,
                    sa.String(),
                    nullable=False,
                    server_default="unavailable",
                )
            )
        batch_op.create_index(
            INDEX_NAME,
            ["portfolio_id", "valuation_coverage_state", "as_of_date"],
            unique=False,
        )

    connection.execute(
        sa.update(daily_snapshot).values(
            **{
                column_name: daily_snapshot.c.coverage_state
                for column_name in COVERAGE_COLUMNS
            }
        )
    )

    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column_name in COVERAGE_COLUMNS:
            batch_op.alter_column(column_name, server_default=None)
    connection.execute(
        sa.update(calculation_state).values(
            daily_snapshot_status="stale",
            error_message=None,
        )
    )


def downgrade() -> None:
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        batch_op.drop_index(INDEX_NAME)
        for column_name in reversed(COVERAGE_COLUMNS):
            batch_op.drop_column(column_name)
    op.get_bind().execute(
        sa.update(calculation_state).values(
            daily_snapshot_status="stale",
            error_message=None,
        )
    )
