"""Add the durable market-data transactional outbox.

Revision ID: 20260714_0013
Revises: 20260714_0012
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0013"
down_revision = "20260714_0012"
branch_labels = None
depends_on = None


OUTBOX_EVENT_TYPE = "watchlist_market_data_refresh_requested"
WATCHLIST_INSTRUMENT_TYPES = "'fund', 'etf', 'index'"


def _create_enqueue_trigger(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        op.execute(
            sa.text(
                f"""
                CREATE FUNCTION enqueue_market_data_outbox_event()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                DECLARE
                    resolved_instrument_id text;
                    resolved_instrument_type text;
                BEGIN
                    EXECUTE format(
                        'SELECT series.instrument_id, instrument.instrument_type '
                        'FROM %I.quote_observation AS observation '
                        'JOIN %I.quote_series AS series '
                        '  ON series.quote_series_id = observation.quote_series_id '
                        'JOIN %I.instrument AS instrument '
                        '  ON instrument.instrument_id = series.instrument_id '
                        'WHERE observation.observation_id = $1',
                        TG_TABLE_SCHEMA,
                        TG_TABLE_SCHEMA,
                        TG_TABLE_SCHEMA
                    )
                    INTO resolved_instrument_id, resolved_instrument_type
                    USING NEW.observation_id;

                    IF resolved_instrument_id IS NULL THEN
                        RAISE EXCEPTION 'market_data_outbox_lineage_missing: quote revision instrument cannot be resolved'
                            USING ERRCODE = '23503';
                    END IF;

                    IF lower(resolved_instrument_type) IN (
                        {WATCHLIST_INSTRUMENT_TYPES}
                    ) THEN
                        EXECUTE format(
                            'INSERT INTO %I.market_data_outbox_event ('
                            'event_id, event_type, instrument_id, quote_revision_id, '
                            'source_kind, source_version, '
                            'status, attempt_count, max_attempts, available_at, '
                            'created_at, updated_at'
                            ') VALUES ('
                            '$1, $2, $3, $1, '
                            '''quote_revision'', $1::text, '
                            '$4, 0, 8, CURRENT_TIMESTAMP, '
                            'CURRENT_TIMESTAMP, CURRENT_TIMESTAMP'
                            ')',
                            TG_TABLE_SCHEMA
                        )
                        USING
                            NEW.revision_id,
                            '{OUTBOX_EVENT_TYPE}',
                            resolved_instrument_id,
                            'pending';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_revision_market_data_outbox
                AFTER INSERT ON quote_observation_revision
                FOR EACH ROW
                EXECUTE FUNCTION enqueue_market_data_outbox_event()
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                CREATE FUNCTION enqueue_quote_policy_market_data_outbox_event()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                DECLARE
                    policy_event_id uuid;
                    policy_source_version text;
                BEGIN
                    IF lower(NEW.instrument_type) NOT IN (
                        {WATCHLIST_INSTRUMENT_TYPES}
                    ) OR OLD.quote_selection_policy_json::jsonb
                        IS NOT DISTINCT FROM NEW.quote_selection_policy_json::jsonb
                    THEN
                        RETURN NEW;
                    END IF;

                    policy_event_id := gen_random_uuid();
                    policy_source_version := COALESCE(
                        NULLIF(btrim(NEW.market_data_updated_at), ''),
                        policy_event_id::text
                    );
                    EXECUTE format(
                        'INSERT INTO %I.market_data_outbox_event ('
                        'event_id, event_type, instrument_id, quote_revision_id, '
                        'source_kind, source_version, '
                        'status, attempt_count, max_attempts, available_at, '
                        'created_at, updated_at'
                        ') VALUES ('
                        '$1, $2, $3, NULL, '
                        '''quote_selection_policy'', $4, '
                        '$5, 0, 8, CURRENT_TIMESTAMP, '
                        'CURRENT_TIMESTAMP, CURRENT_TIMESTAMP'
                        ')',
                        TG_TABLE_SCHEMA
                    )
                    USING
                        policy_event_id,
                        '{OUTBOX_EVENT_TYPE}',
                        NEW.instrument_id,
                        policy_source_version,
                        'pending';
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_policy_market_data_outbox
                AFTER UPDATE OF quote_selection_policy_json ON instrument
                FOR EACH ROW
                EXECUTE FUNCTION enqueue_quote_policy_market_data_outbox_event()
                """
            )
        )
        return

    if connection.dialect.name == "sqlite":
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_quote_revision_market_data_outbox
                AFTER INSERT ON quote_observation_revision
                FOR EACH ROW
                BEGIN
                    INSERT INTO market_data_outbox_event (
                        event_id,
                        event_type,
                        instrument_id,
                        quote_revision_id,
                        source_kind,
                        source_version,
                        status,
                        attempt_count,
                        max_attempts,
                        available_at,
                        created_at,
                        updated_at
                    )
                    SELECT
                        NEW.revision_id,
                        '{OUTBOX_EVENT_TYPE}',
                        series.instrument_id,
                        NEW.revision_id,
                        'quote_revision',
                        NEW.revision_id,
                        'pending',
                        0,
                        8,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    FROM quote_observation AS observation
                    JOIN quote_series AS series
                      ON series.quote_series_id = observation.quote_series_id
                    JOIN instrument AS instrument
                      ON instrument.instrument_id = series.instrument_id
                    WHERE observation.observation_id = NEW.observation_id
                      AND lower(instrument.instrument_type) IN (
                          {WATCHLIST_INSTRUMENT_TYPES}
                      );
                END
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_quote_policy_market_data_outbox
                AFTER UPDATE OF quote_selection_policy_json ON instrument
                FOR EACH ROW
                WHEN lower(NEW.instrument_type) IN (
                    {WATCHLIST_INSTRUMENT_TYPES}
                ) AND (
                    json_extract(OLD.quote_selection_policy_json, '$.trading')
                        IS NOT json_extract(
                            NEW.quote_selection_policy_json, '$.trading'
                        )
                    OR json_extract(
                        OLD.quote_selection_policy_json, '$.valuation'
                    ) IS NOT json_extract(
                        NEW.quote_selection_policy_json, '$.valuation'
                    )
                    OR json_extract(
                        OLD.quote_selection_policy_json, '$.total_return'
                    ) IS NOT json_extract(
                        NEW.quote_selection_policy_json, '$.total_return'
                    )
                    OR json_extract(OLD.quote_selection_policy_json, '$.chart')
                        IS NOT json_extract(
                            NEW.quote_selection_policy_json, '$.chart'
                        )
                    OR json_extract(
                        OLD.quote_selection_policy_json, '$.reference'
                    ) IS NOT json_extract(
                        NEW.quote_selection_policy_json, '$.reference'
                    )
                )
                BEGIN
                    INSERT INTO market_data_outbox_event (
                        event_id,
                        event_type,
                        instrument_id,
                        quote_revision_id,
                        source_kind,
                        source_version,
                        status,
                        attempt_count,
                        max_attempts,
                        available_at,
                        created_at,
                        updated_at
                    ) VALUES (
                        lower(hex(randomblob(16))),
                        '{OUTBOX_EVENT_TYPE}',
                        NEW.instrument_id,
                        NULL,
                        'quote_selection_policy',
                        COALESCE(
                            NULLIF(trim(NEW.market_data_updated_at), ''),
                            'policy-event:' || lower(hex(randomblob(16)))
                        ),
                        'pending',
                        0,
                        8,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    );
                END
                """
            )
        )


def upgrade() -> None:
    op.create_table(
        "market_data_outbox_event",
        sa.Column("event_id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "instrument_id",
            sa.String(),
            sa.ForeignKey("instrument.instrument_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "quote_revision_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey(
                "quote_observation_revision.revision_id",
                ondelete="RESTRICT",
            ),
            nullable=True,
            unique=True,
        ),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_version", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("8"),
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lease_owner", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "event_type = 'watchlist_market_data_refresh_requested'",
            name=op.f("ck_market_data_outbox_event_event_type"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'delivered', 'dead')",
            name=op.f("ck_market_data_outbox_event_status"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name=op.f("ck_market_data_outbox_event_attempts"),
        ),
        sa.CheckConstraint(
            "((source_kind = 'quote_revision' AND quote_revision_id IS NOT NULL) OR "
            "(source_kind = 'quote_selection_policy' AND quote_revision_id IS NULL)) "
            "AND TRIM(source_version) <> ''",
            name=op.f("ck_market_data_outbox_event_source_lineage"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND delivered_at IS NULL "
            "AND dead_at IS NULL) OR "
            "(status = 'processing' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND delivered_at IS NULL "
            "AND dead_at IS NULL) OR "
            "(status = 'delivered' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND delivered_at IS NOT NULL "
            "AND dead_at IS NULL) OR "
            "(status = 'dead' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND delivered_at IS NULL "
            "AND dead_at IS NOT NULL)",
            name=op.f("ck_market_data_outbox_event_lifecycle"),
        ),
        sa.UniqueConstraint(
            "instrument_id",
            "source_kind",
            "source_version",
            name=op.f("uq_market_data_outbox_event_source_lineage"),
        ),
    )
    op.create_index(
        "ix_market_data_outbox_event_claim",
        "market_data_outbox_event",
        ["status", "available_at", "created_at", "event_id"],
    )
    op.create_index(
        "ix_market_data_outbox_event_expired_lease",
        "market_data_outbox_event",
        ["lease_expires_at", "event_id"],
        postgresql_where=sa.text("status = 'processing'"),
        sqlite_where=sa.text("status = 'processing'"),
    )

    op.create_table(
        "market_data_outbox_worker_heartbeat",
        sa.Column("worker_id", sa.String(), primary_key=True),
        sa.Column("hostname", sa.String(), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("worker_state", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_successful_poll_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_poll_error", sa.String(length=4000), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "process_id > 0",
            name=op.f("ck_market_data_outbox_worker_heartbeat_process_id"),
        ),
        sa.CheckConstraint(
            "worker_state IN ('running', 'stopped')",
            name=op.f("ck_market_data_outbox_worker_heartbeat_state"),
        ),
        sa.CheckConstraint(
            "(worker_state = 'running' AND stopped_at IS NULL) OR "
            "(worker_state = 'stopped' AND stopped_at IS NOT NULL)",
            name=op.f("ck_market_data_outbox_worker_heartbeat_lifecycle"),
        ),
    )
    op.create_index(
        "ix_market_data_outbox_worker_heartbeat_state",
        "market_data_outbox_worker_heartbeat",
        ["worker_state", "heartbeat_at", "last_successful_poll_at"],
    )

    _create_enqueue_trigger(op.get_bind())


def downgrade() -> None:
    raise RuntimeError(
        "20260714_0013 is irreversible: dropping the transactional outbox "
        "would discard durable downstream-delivery state."
    )
