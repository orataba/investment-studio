"""Split fair-value NAV coverage from book-P&L coverage.

Revision ID: 20260713_0034
Revises: 20260713_0033

The superseded ``coverage_state`` mixed valuation, cost-basis, realised-P&L,
FX-attribution, and return-linking failures.  Those meanings cannot be
reconstructed reliably from the old value.  Existing materialised rows are
therefore invalidated fail-closed and the normal rebuild pipeline must derive
the two new coverage contracts from source facts.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0034"
down_revision = "20260713_0033"
branch_labels = None
depends_on = None


_REBUILD_REASON_JSON = '["coverage_contract_rebuild_required"]'


def _json_server_default(connection: sa.Connection, value: str) -> sa.TextClause:
    if connection.dialect.name == "postgresql":
        return sa.text(f"'{value}'::json")
    return sa.text(f"'{value}'")


def _rewrite_payloads_for_rebuild(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                """
                UPDATE portfolio_daily_snapshot
                SET snapshot_json =
                    (
                        snapshot_json::jsonb - 'coverage_state'
                        || jsonb_build_object(
                        'nav_coverage_state', 'unavailable',
                        'nav_coverage_reason_codes',
                            jsonb_build_array('coverage_contract_rebuild_required'),
                        'book_pnl_coverage_state', 'unavailable',
                        'book_pnl_coverage_reason_codes',
                            jsonb_build_array('coverage_contract_rebuild_required'),
                        'calculation_version', 'invalidated-by-20260713-0034'
                        )
                    )::json
                """
            )
        )
        connection.execute(
            sa.text(
                """
                UPDATE portfolio_daily_contribution_slice
                SET slice_json =
                    (
                        slice_json::jsonb - 'coverage_state'
                        || jsonb_build_object(
                        'nav_coverage_state', 'unavailable',
                        'nav_coverage_reason_codes',
                            jsonb_build_array('coverage_contract_rebuild_required'),
                        'book_pnl_coverage_state', 'unavailable',
                        'book_pnl_coverage_reason_codes',
                            jsonb_build_array('coverage_contract_rebuild_required')
                        )
                    )::json
                """
            )
        )
        return

    connection.execute(
        sa.text(
            """
            UPDATE portfolio_daily_snapshot
            SET snapshot_json = json_set(
                json_remove(snapshot_json, '$.coverage_state'),
                '$.nav_coverage_state', 'unavailable',
                '$.nav_coverage_reason_codes',
                    json('["coverage_contract_rebuild_required"]'),
                '$.book_pnl_coverage_state', 'unavailable',
                '$.book_pnl_coverage_reason_codes',
                    json('["coverage_contract_rebuild_required"]'),
                '$.calculation_version', 'invalidated-by-20260713-0034'
            )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE portfolio_daily_contribution_slice
            SET slice_json = json_set(
                json_remove(slice_json, '$.coverage_state'),
                '$.nav_coverage_state', 'unavailable',
                '$.nav_coverage_reason_codes',
                    json('["coverage_contract_rebuild_required"]'),
                '$.book_pnl_coverage_state', 'unavailable',
                '$.book_pnl_coverage_reason_codes',
                    json('["coverage_contract_rebuild_required"]')
            )
            """
        )
    )


def _split_table_coverage(
    connection: sa.Connection,
    *,
    table_name: str,
    old_index_name: str | None = None,
    new_index_name: str | None = None,
) -> None:
    reason_default = _json_server_default(connection, _REBUILD_REASON_JSON)
    with op.batch_alter_table(table_name) as batch_op:
        if old_index_name is not None:
            batch_op.drop_index(old_index_name)
        batch_op.alter_column(
            "coverage_state",
            new_column_name="nav_coverage_state",
            existing_type=sa.String(),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column(
                "nav_coverage_reason_codes",
                sa.JSON(),
                nullable=False,
                server_default=reason_default,
            )
        )
        batch_op.add_column(
            sa.Column(
                "book_pnl_coverage_state",
                sa.String(),
                nullable=False,
                server_default="unavailable",
            )
        )
        batch_op.add_column(
            sa.Column(
                "book_pnl_coverage_reason_codes",
                sa.JSON(),
                nullable=False,
                server_default=reason_default,
            )
        )

    reason_sql = (
        f"'{_REBUILD_REASON_JSON}'::json"
        if connection.dialect.name == "postgresql"
        else f"json('{_REBUILD_REASON_JSON}')"
    )
    connection.execute(
        sa.text(
            f"""
            UPDATE {table_name}
            SET nav_coverage_state = 'unavailable',
                nav_coverage_reason_codes = {reason_sql},
                book_pnl_coverage_state = 'unavailable',
                book_pnl_coverage_reason_codes = {reason_sql}
            """
        )
    )

    with op.batch_alter_table(table_name) as batch_op:
        batch_op.alter_column(
            "nav_coverage_reason_codes",
            existing_type=sa.JSON(),
            existing_nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "book_pnl_coverage_state",
            existing_type=sa.String(),
            existing_nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "book_pnl_coverage_reason_codes",
            existing_type=sa.JSON(),
            existing_nullable=False,
            server_default=None,
        )
        batch_op.create_check_constraint(
            f"ck_{table_name}_nav_coverage_state",
            "nav_coverage_state IN ('complete', 'partial', 'unavailable')",
        )
        batch_op.create_check_constraint(
            f"ck_{table_name}_book_pnl_coverage_state",
            "book_pnl_coverage_state IN ('complete', 'partial', 'unavailable')",
        )
        if new_index_name is not None:
            batch_op.create_index(
                new_index_name,
                ["portfolio_id", "nav_coverage_state", "as_of_date"],
                unique=False,
            )


def upgrade() -> None:
    connection = op.get_bind()
    _split_table_coverage(
        connection,
        table_name="portfolio_daily_snapshot",
        old_index_name="ix_portfolio_daily_snapshot_portfolio_coverage",
        new_index_name="ix_portfolio_daily_snapshot_portfolio_nav_coverage",
    )
    _split_table_coverage(
        connection,
        table_name="portfolio_daily_contribution_slice",
    )
    _rewrite_payloads_for_rebuild(connection)
    connection.execute(
        sa.text(
            """
            UPDATE portfolio_calculation_state
            SET daily_snapshot_status = 'stale',
                dirty_from = NULL,
                refresh_request_id = 'coverage-contract-20260713-0034',
                refresh_started_at = NULL,
                refresh_completed_at = NULL,
                error_message = NULL
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0034 is intentionally irreversible: the superseded mixed "
        "coverage contract cannot be reconstructed safely."
    )
