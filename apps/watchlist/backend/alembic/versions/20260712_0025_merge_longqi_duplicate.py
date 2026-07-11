"""Remove the duplicate Longqi instrument from Watchlist read models.

The shared registry archives ``nav-8c76dae71a`` and keeps its identifier as an
alias of canonical ``sh7639``. Watchlist-derived data is intentionally rebuilt
from the canonical, longer history instead of relabelling snapshots calculated
from the duplicate's shorter history.

Revision ID: 20260712_0025
Revises: 20260712_0024
"""

from datetime import UTC, datetime
import json

from alembic import op
import sqlalchemy as sa


revision = "20260712_0025"
down_revision = "20260712_0024"
branch_labels = None
depends_on = None


CANONICAL_ID = "sh7639"
DUPLICATE_ID = "nav-8c76dae71a"


def _table(connection: sa.Connection, table_name: str) -> sa.Table:
    return sa.Table(table_name, sa.MetaData(), autoload_with=connection)


def _record_exists(connection: sa.Connection, table: sa.Table, instrument_id: str) -> bool:
    return bool(
        connection.scalar(
            sa.select(sa.literal(True))
            .select_from(table)
            .where(table.c.instrument_id == instrument_id)
            .limit(1)
        )
    )


def _merge_composite_membership(
    connection: sa.Connection,
    *,
    table_name: str,
    scope_column: str,
) -> None:
    table = _table(connection, table_name)
    scope = table.c[scope_column]
    canonical_scopes = sa.select(scope).where(table.c.instrument_id == CANONICAL_ID)
    connection.execute(
        sa.delete(table).where(
            table.c.instrument_id == DUPLICATE_ID,
            scope.in_(canonical_scopes),
        )
    )
    connection.execute(
        sa.update(table)
        .where(table.c.instrument_id == DUPLICATE_ID)
        .values(instrument_id=CANONICAL_ID)
    )


def _merge_singleton(connection: sa.Connection, table_name: str) -> None:
    table = _table(connection, table_name)
    if not _record_exists(connection, table, DUPLICATE_ID):
        return
    if _record_exists(connection, table, CANONICAL_ID):
        connection.execute(
            sa.delete(table).where(table.c.instrument_id == DUPLICATE_ID)
        )
        return
    connection.execute(
        sa.update(table)
        .where(table.c.instrument_id == DUPLICATE_ID)
        .values(instrument_id=CANONICAL_ID)
    )


def _merge_nav_facts(connection: sa.Connection) -> None:
    table = _table(connection, "nav_fact")
    duplicate_rows = list(
        connection.execute(
            sa.select(
                table.c.nav_fact_id,
                table.c.as_of_date,
                table.c.nav_type,
                table.c.currency,
            ).where(table.c.instrument_id == DUPLICATE_ID)
        ).mappings()
    )
    for row in duplicate_rows:
        canonical_exists = bool(
            connection.scalar(
                sa.select(sa.literal(True))
                .select_from(table)
                .where(
                    table.c.instrument_id == CANONICAL_ID,
                    table.c.as_of_date == row["as_of_date"],
                    table.c.nav_type == row["nav_type"],
                    table.c.currency == row["currency"],
                )
                .limit(1)
            )
        )
        if canonical_exists:
            connection.execute(
                sa.delete(table).where(table.c.nav_fact_id == row["nav_fact_id"])
            )
        else:
            connection.execute(
                sa.update(table)
                .where(table.c.nav_fact_id == row["nav_fact_id"])
                .values(instrument_id=CANONICAL_ID)
            )


def _enqueue_canonical_recalc(connection: sa.Connection) -> None:
    table = _table(connection, "recalc_job")
    has_open_job = bool(
        connection.scalar(
            sa.select(sa.literal(True))
            .select_from(table)
            .where(
                table.c.instrument_id == CANONICAL_ID,
                table.c.job_type == "all",
                table.c.job_status.in_(("queued", "running")),
            )
            .limit(1)
        )
    )
    if has_open_job:
        return
    now = datetime.now(UTC).replace(microsecond=0)
    connection.execute(
        sa.insert(table).values(
            recalc_job_id="job_merge_longqi_sh7639",
            job_type="all",
            instrument_id=CANONICAL_ID,
            trigger_type="instrument_merge",
            trigger_ref_type="canonical_instrument",
            trigger_ref_id=DUPLICATE_ID,
            job_status="queued",
            priority=100,
            dedupe_key=json.dumps(
                {"instrument_id": CANONICAL_ID, "job_type": "all"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            payload_json={
                "instrument_id": CANONICAL_ID,
                "job_type": "all",
                "merged_duplicate_instrument_id": DUPLICATE_ID,
            },
            enqueued_at=now,
            started_at=None,
            heartbeat_at=None,
            lease_token=None,
            finished_at=None,
            error_message=None,
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    instrument_detail = _table(connection, "instrument_detail")
    if not (
        _record_exists(connection, instrument_detail, CANONICAL_ID)
        and _record_exists(connection, instrument_detail, DUPLICATE_ID)
    ):
        return

    # Preserve membership and user-authored metadata, resolving uniqueness
    # collisions in favour of the already-established canonical record.
    _merge_composite_membership(
        connection,
        table_name="watchlist_item",
        scope_column="watchlist_id",
    )
    _merge_composite_membership(
        connection,
        table_name="instrument_taxonomy_assignment",
        scope_column="taxonomy_code",
    )
    _merge_singleton(connection, "instrument_manual_profile")
    attribute_values = _table(connection, "instrument_attribute_value")
    connection.execute(
        sa.update(attribute_values)
        .where(attribute_values.c.instrument_id == DUPLICATE_ID)
        .values(instrument_id=CANONICAL_ID)
    )
    _merge_nav_facts(connection)

    # Derived rows based on the duplicate's shorter NAV history must not be
    # relabelled as canonical history. They are deleted and deterministically
    # rebuilt from SH7639 below.
    for table_name in (
        "watchlist_row_read_model",
        "instrument_summary_read_model",
        "instrument_chart_read_model",
        "instrument_performance_read_model",
        "instrument_risk_read_model",
        "instrument_exposure_read_model",
        "instrument_exposure_holdings_read_model",
        "instrument_rating_read_model",
        "performance_snapshot",
        "risk_snapshot",
        "exposure_analytics_snapshot",
        "instrument_score_snapshot",
        "holding_snapshot",
        "recalc_job",
    ):
        table = _table(connection, table_name)
        connection.execute(
            sa.delete(table).where(table.c.instrument_id == DUPLICATE_ID)
        )

    connection.execute(
        sa.delete(instrument_detail).where(
            instrument_detail.c.instrument_id == DUPLICATE_ID
        )
    )
    _enqueue_canonical_recalc(connection)


def downgrade() -> None:
    # The duplicate was invalid master data and cannot be reconstructed without
    # reintroducing the same double-counting defect.
    pass
