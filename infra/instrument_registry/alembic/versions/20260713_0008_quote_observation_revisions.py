"""Replace flat market data with canonical quote series and append-only revisions.

Revision ID: 20260713_0008
Revises: 20260712_0007
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid5

from alembic import op
import sqlalchemy as sa


revision = "20260713_0008"
down_revision = "20260712_0007"
branch_labels = None
depends_on = None


# Frozen persisted-identity contract. Migrations must not import runtime code.
QUOTE_SERIES_NAMESPACE = UUID("0c8c59ee-8dd1-5cc7-b079-b8f0888b0a06")
QUOTE_OBSERVATION_NAMESPACE = UUID("eda93e14-59b7-5073-be9a-151fac3992c5")
QUOTE_REVISION_NAMESPACE = UUID("ca0c3ef2-3a5e-5cd8-be26-5ce75056af7f")
SOURCE_OBSERVATION_STATUSES = frozenset({"complete", "partial", "rejected"})
BACKFILL_BATCH_SIZE = 5_000
VALID_QUOTE_BASES: dict[str, str] = {
    "last": "price",
    "close": "price",
    "adjusted_close": "price",
    "official_nav": "nav",
    "total_return_nav": "nav",
    "cumulative_nav": "nav",
    "accumulated_nav": "nav",
    "cum_nav": "nav",
    "dividend_adjusted_nav": "nav",
    "reinvested_nav": "nav",
    "spot": "fx",
    "clean_price": "price",
    "dirty_price": "price",
    "par": "price",
}
QUOTE_VALUE_TYPE = sa.Numeric(asdecimal=True).with_variant(sa.Text(), "sqlite")


legacy_market_data = sa.table(
    "instrument_market_data",
    sa.column("instrument_market_data_id", sa.Integer()),
    sa.column("instrument_id", sa.String()),
    sa.column("metric_family", sa.String()),
    sa.column("quote_basis", sa.String()),
    sa.column("as_of_date", sa.Date()),
    sa.column("value", sa.Text()),
    sa.column("currency", sa.String()),
    sa.column("provider", sa.String()),
    sa.column("status", sa.String()),
)
instrument = sa.table(
    "instrument",
    sa.column("instrument_id", sa.String()),
    sa.column("market_data_updated_at", sa.String()),
)
quote_series = sa.table(
    "quote_series",
    sa.column("quote_series_id", sa.Uuid(as_uuid=False)),
    sa.column("instrument_id", sa.String()),
    sa.column("metric_family", sa.String()),
    sa.column("quote_basis", sa.String()),
    sa.column("currency", sa.String()),
    sa.column("data_updated_at", sa.String()),
)
quote_observation = sa.table(
    "quote_observation",
    sa.column("observation_id", sa.Uuid(as_uuid=False)),
    sa.column("quote_series_id", sa.Uuid(as_uuid=False)),
    sa.column("as_of_date", sa.Date()),
)
quote_observation_revision = sa.table(
    "quote_observation_revision",
    sa.column("revision_id", sa.Uuid(as_uuid=False)),
    sa.column("observation_id", sa.Uuid(as_uuid=False)),
    sa.column("revision_number", sa.Integer()),
    sa.column("value", QUOTE_VALUE_TYPE),
    sa.column("source_ref", sa.Text()),
    sa.column("status", sa.String()),
    sa.column("source_published_at", sa.DateTime(timezone=True)),
    sa.column("ingested_at", sa.DateTime(timezone=True)),
    sa.column("payload_hash", sa.String()),
    sa.column("is_current", sa.Boolean()),
    sa.column("superseded_at", sa.DateTime(timezone=True)),
)


def _canonical_decimal_text(value: object) -> str:
    try:
        resolved = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise RuntimeError(f"Legacy quote value is not a decimal: {value!r}") from error
    if not resolved.is_finite():
        raise RuntimeError(f"Legacy quote value is not finite: {value!r}")
    if resolved == 0:
        return "0"
    fixed = format(resolved, "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed


def _series_identity(row: sa.RowMapping) -> tuple[str, str, str, str]:
    return (
        str(row["instrument_id"]).strip(),
        str(row["metric_family"]).strip().lower(),
        str(row["quote_basis"]).strip().lower(),
        str(row["currency"]).strip().upper(),
    )


def _series_id(identity: tuple[str, str, str, str]) -> str:
    return str(uuid5(QUOTE_SERIES_NAMESPACE, "\x1f".join(identity)))


def _point_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _observation_id(series_id: str, point_date: date) -> str:
    return str(
        uuid5(
            QUOTE_OBSERVATION_NAMESPACE,
            f"{series_id}\x1f{point_date.isoformat()}",
        )
    )


def _revision_id(observation_id: str, revision_number: int) -> str:
    return str(
        uuid5(
            QUOTE_REVISION_NAMESPACE,
            f"{observation_id}\x1f{revision_number}",
        )
    )


def _source_ref(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _payload_hash(*, value: str, source_ref: str | None, status: str) -> str:
    payload = {
        "kind": "observation",
        "source_published_at": None,
        "source_ref": source_ref,
        "status": status,
        "value": value,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _execute_batches(connection: sa.Connection, table: sa.TableClause, rows: list[dict[str, object]]) -> None:
    if rows:
        connection.execute(sa.insert(table), rows)
        rows.clear()


def _preflight_legacy_market_data(connection: sa.Connection) -> None:
    status_rows = connection.execute(
        sa.select(
            sa.func.lower(legacy_market_data.c.status).label("status"),
            sa.func.count().label("row_count"),
        ).group_by(sa.func.lower(legacy_market_data.c.status))
    ).mappings()
    unsupported = {
        str(row["status"]): int(row["row_count"])
        for row in status_rows
        if row["status"] not in SOURCE_OBSERVATION_STATUSES
    }
    if unsupported:
        raise RuntimeError(
            "Legacy market data contains statuses that cannot become source "
            "observations. Resolve/remove them before upgrade (unavailable means "
            f"no observation and must not be persisted): {unsupported}."
        )

    normalized_instrument = sa.func.trim(
        sa.func.coalesce(legacy_market_data.c.instrument_id, "")
    )
    normalized_metric = sa.func.lower(
        sa.func.trim(sa.func.coalesce(legacy_market_data.c.metric_family, ""))
    )
    normalized_basis = sa.func.lower(
        sa.func.trim(sa.func.coalesce(legacy_market_data.c.quote_basis, ""))
    )
    normalized_currency = sa.func.upper(
        sa.func.trim(sa.func.coalesce(legacy_market_data.c.currency, ""))
    )
    valid_identity = sa.and_(
        normalized_instrument != "",
        normalized_metric != "",
        normalized_basis != "",
        normalized_currency != "",
        sa.func.length(normalized_currency) <= 8,
        legacy_market_data.c.as_of_date.is_not(None),
        sa.exists(
            sa.select(instrument.c.instrument_id).where(
                instrument.c.instrument_id == normalized_instrument
            )
        ),
        sa.or_(
            *[
                sa.and_(
                    normalized_metric == metric_family,
                    normalized_basis == quote_basis,
                )
                for quote_basis, metric_family in VALID_QUOTE_BASES.items()
            ]
        ),
    )
    invalid_identity = connection.execute(
        sa.select(
            legacy_market_data.c.instrument_market_data_id,
            legacy_market_data.c.instrument_id,
            legacy_market_data.c.metric_family,
            legacy_market_data.c.quote_basis,
            legacy_market_data.c.currency,
            legacy_market_data.c.as_of_date,
        )
        .where(sa.not_(valid_identity))
        .limit(1)
    ).mappings().first()
    if invalid_identity is not None:
        raise RuntimeError(
            "Legacy market data has a blank or unsupported canonical quote "
            f"identity: {dict(invalid_identity)}. Resolve it before upgrade."
        )

    value_result = connection.execute(
        sa.select(
            legacy_market_data.c.instrument_market_data_id,
            legacy_market_data.c.value,
        ).order_by(legacy_market_data.c.instrument_market_data_id)
    ).mappings()
    while value_rows := value_result.fetchmany(BACKFILL_BATCH_SIZE):
        for row in value_rows:
            value = _canonical_decimal_text(row["value"])
            if Decimal(value) <= 0:
                raise RuntimeError(
                    "Legacy quote values must be positive; found "
                    f"instrument_market_data_id={row['instrument_market_data_id']} "
                    f"value={row['value']!r}."
                )

    duplicate_identity = connection.execute(
        sa.select(
            normalized_instrument.label("instrument_id"),
            normalized_metric.label("metric_family"),
            normalized_basis.label("quote_basis"),
            normalized_currency.label("currency"),
            legacy_market_data.c.as_of_date.label("as_of_date"),
            sa.func.count().label("row_count"),
        )
        .group_by(
            normalized_instrument,
            normalized_metric,
            normalized_basis,
            normalized_currency,
            legacy_market_data.c.as_of_date,
        )
        .having(sa.func.count() > 1)
        .limit(1)
    ).mappings().first()
    if duplicate_identity is not None:
        raise RuntimeError(
            "Legacy market data collides after canonical identity normalization: "
            f"{dict(duplicate_identity)}. Resolve the duplicate before upgrade."
        )


def _backfill_legacy_market_data(connection: sa.Connection) -> None:
    allowed_statuses = sorted(SOURCE_OBSERVATION_STATUSES)
    series_statement = (
        sa.select(
            legacy_market_data.c.instrument_id,
            legacy_market_data.c.metric_family,
            legacy_market_data.c.quote_basis,
            legacy_market_data.c.currency,
            instrument.c.market_data_updated_at,
        )
        .join(
            instrument,
            instrument.c.instrument_id == legacy_market_data.c.instrument_id,
        )
        .where(sa.func.lower(legacy_market_data.c.status).in_(allowed_statuses))
        .distinct()
        .order_by(
            legacy_market_data.c.instrument_id,
            legacy_market_data.c.metric_family,
            legacy_market_data.c.quote_basis,
            legacy_market_data.c.currency,
        )
    )
    series_ids: dict[tuple[str, str, str, str], str] = {}
    series_batch: list[dict[str, object]] = []
    series_result = connection.execute(series_statement).mappings()
    while rows := series_result.fetchmany(BACKFILL_BATCH_SIZE):
        for row in rows:
            identity = _series_identity(row)
            if identity in series_ids:
                continue
            resolved_series_id = _series_id(identity)
            series_ids[identity] = resolved_series_id
            series_batch.append(
                {
                    "quote_series_id": resolved_series_id,
                    "instrument_id": identity[0],
                    "metric_family": identity[1],
                    "quote_basis": identity[2],
                    "currency": identity[3],
                    "data_updated_at": row["market_data_updated_at"],
                }
            )
        _execute_batches(connection, quote_series, series_batch)

    point_statement = (
        sa.select(
            legacy_market_data.c.instrument_id,
            legacy_market_data.c.metric_family,
            legacy_market_data.c.quote_basis,
            legacy_market_data.c.as_of_date,
            legacy_market_data.c.value,
            legacy_market_data.c.currency,
            legacy_market_data.c.provider,
            legacy_market_data.c.status,
        )
        .where(sa.func.lower(legacy_market_data.c.status).in_(allowed_statuses))
        .order_by(legacy_market_data.c.instrument_market_data_id)
    )
    observation_batch: list[dict[str, object]] = []
    revision_batch: list[dict[str, object]] = []
    point_result = connection.execute(point_statement).mappings()
    while rows := point_result.fetchmany(BACKFILL_BATCH_SIZE):
        for row in rows:
            identity = _series_identity(row)
            resolved_series_id = series_ids[identity]
            resolved_date = _point_date(row["as_of_date"])
            resolved_observation_id = _observation_id(
                resolved_series_id,
                resolved_date,
            )
            value = _canonical_decimal_text(row["value"])
            source_ref = _source_ref(row["provider"])
            status = str(row["status"]).strip().lower()
            observation_batch.append(
                {
                    "observation_id": resolved_observation_id,
                    "quote_series_id": resolved_series_id,
                    "as_of_date": resolved_date,
                }
            )
            revision_batch.append(
                {
                    "revision_id": _revision_id(resolved_observation_id, 1),
                    "observation_id": resolved_observation_id,
                    "revision_number": 1,
                    "value": (
                        value
                        if connection.dialect.name == "sqlite"
                        else Decimal(value)
                    ),
                    "source_ref": source_ref,
                    "status": status,
                    # Legacy rows have no trustworthy ingestion/publication clock.
                    "source_published_at": None,
                    "ingested_at": None,
                    "payload_hash": _payload_hash(
                        value=value,
                        source_ref=source_ref,
                        status=status,
                    ),
                    "is_current": True,
                    "superseded_at": None,
                }
            )
        _execute_batches(connection, quote_observation, observation_batch)
        _execute_batches(connection, quote_observation_revision, revision_batch)

    expected_points = int(
        connection.scalar(
            sa.select(sa.func.count())
            .select_from(legacy_market_data)
            .where(sa.func.lower(legacy_market_data.c.status).in_(allowed_statuses))
        )
        or 0
    )
    expected_series = len(series_ids)
    actual_series = int(connection.scalar(sa.select(sa.func.count()).select_from(quote_series)) or 0)
    actual_observations = int(
        connection.scalar(sa.select(sa.func.count()).select_from(quote_observation)) or 0
    )
    actual_revisions = int(
        connection.scalar(sa.select(sa.func.count()).select_from(quote_observation_revision)) or 0
    )
    invalid_revisions = int(
        connection.scalar(
            sa.select(sa.func.count())
            .select_from(quote_observation_revision)
            .where(
                sa.or_(
                    quote_observation_revision.c.revision_number != 1,
                    quote_observation_revision.c.is_current.is_(False),
                    quote_observation_revision.c.payload_hash.is_(None),
                    quote_observation_revision.c.ingested_at.is_not(None),
                    quote_observation_revision.c.source_published_at.is_not(None),
                )
            )
        )
        or 0
    )
    if (
        actual_series != expected_series
        or actual_observations != expected_points
        or actual_revisions != expected_points
        or invalid_revisions
    ):
        raise RuntimeError(
            "Quote revision backfill invariant failed: "
            f"series={actual_series}/{expected_series}, "
            f"observations={actual_observations}/{expected_points}, "
            f"revisions={actual_revisions}/{expected_points}, "
            f"invalid_revisions={invalid_revisions}."
        )


def _create_revision_immutability_guards(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                CREATE FUNCTION guard_quote_observation_revision_immutable()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        RAISE EXCEPTION 'quote_revision_immutable_violation: delete is forbidden'
                            USING ERRCODE = '55000';
                    END IF;

                    IF OLD.is_current
                       AND NOT NEW.is_current
                       AND OLD.superseded_at IS NULL
                       AND NEW.superseded_at IS NOT NULL
                       AND NEW.revision_id IS NOT DISTINCT FROM OLD.revision_id
                       AND NEW.observation_id IS NOT DISTINCT FROM OLD.observation_id
                       AND NEW.revision_number IS NOT DISTINCT FROM OLD.revision_number
                       AND NEW.value IS NOT DISTINCT FROM OLD.value
                       AND NEW.source_ref IS NOT DISTINCT FROM OLD.source_ref
                       AND NEW.status IS NOT DISTINCT FROM OLD.status
                       AND NEW.source_published_at IS NOT DISTINCT FROM OLD.source_published_at
                       AND NEW.ingested_at IS NOT DISTINCT FROM OLD.ingested_at
                       AND NEW.payload_hash IS NOT DISTINCT FROM OLD.payload_hash
                    THEN
                        RETURN NEW;
                    END IF;

                    RAISE EXCEPTION 'quote_revision_immutable_violation: only current lifecycle closure is allowed'
                        USING ERRCODE = '55000';
                END;
                $$
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_observation_revision_immutable
                BEFORE UPDATE OR DELETE ON quote_observation_revision
                FOR EACH ROW
                EXECUTE FUNCTION guard_quote_observation_revision_immutable()
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE FUNCTION guard_quote_observation_revision_insert_sequence()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                DECLARE
                    previous_revision_number integer;
                    current_revision_count integer;
                BEGIN
                    EXECUTE format(
                        'SELECT MAX(revision_number), '
                        'COUNT(*) FILTER (WHERE is_current) '
                        'FROM %I.%I WHERE observation_id = $1',
                        TG_TABLE_SCHEMA,
                        TG_TABLE_NAME
                    )
                    INTO previous_revision_number, current_revision_count
                    USING NEW.observation_id;

                    IF NOT NEW.is_current OR NEW.superseded_at IS NOT NULL THEN
                        RAISE EXCEPTION 'quote_revision_insert_violation: new revision must be current'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NEW.revision_number IS DISTINCT FROM
                       COALESCE(previous_revision_number + 1, 1) THEN
                        RAISE EXCEPTION 'quote_revision_insert_violation: revision_number must be the next sequence'
                            USING ERRCODE = '55000';
                    END IF;
                    IF previous_revision_number IS NOT NULL
                       AND current_revision_count <> 0 THEN
                        RAISE EXCEPTION 'quote_revision_insert_violation: prior current must be closed first'
                            USING ERRCODE = '55000';
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
                CREATE TRIGGER trg_quote_observation_revision_insert_sequence
                BEFORE INSERT ON quote_observation_revision
                FOR EACH ROW
                EXECUTE FUNCTION guard_quote_observation_revision_insert_sequence()
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE FUNCTION guard_quote_observation_revision_current_cardinality()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                DECLARE
                    current_revision_count integer;
                BEGIN
                    EXECUTE format(
                        'SELECT COUNT(*) FILTER (WHERE is_current) '
                        'FROM %I.%I WHERE observation_id = $1',
                        TG_TABLE_SCHEMA,
                        TG_TABLE_NAME
                    )
                    INTO current_revision_count
                    USING NEW.observation_id;
                    IF current_revision_count <> 1 THEN
                        RAISE EXCEPTION 'quote_revision_cardinality_violation: observation must have exactly one current revision'
                            USING ERRCODE = '55000';
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
                CREATE CONSTRAINT TRIGGER trg_quote_observation_revision_current_cardinality
                AFTER INSERT OR UPDATE ON quote_observation_revision
                DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW
                EXECUTE FUNCTION guard_quote_observation_revision_current_cardinality()
                """
            )
        )
        return

    if connection.dialect.name == "sqlite":
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_observation_revision_update_guard
                BEFORE UPDATE ON quote_observation_revision
                FOR EACH ROW
                WHEN NOT (
                    OLD.is_current
                    AND NOT NEW.is_current
                    AND OLD.superseded_at IS NULL
                    AND NEW.superseded_at IS NOT NULL
                    AND NEW.revision_id IS OLD.revision_id
                    AND NEW.observation_id IS OLD.observation_id
                    AND NEW.revision_number IS OLD.revision_number
                    AND NEW.value IS OLD.value
                    AND NEW.source_ref IS OLD.source_ref
                    AND NEW.status IS OLD.status
                    AND NEW.source_published_at IS OLD.source_published_at
                    AND NEW.ingested_at IS OLD.ingested_at
                    AND NEW.payload_hash IS OLD.payload_hash
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'quote_revision_immutable_violation: only current lifecycle closure is allowed'
                    );
                END
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_observation_revision_insert_sequence
                BEFORE INSERT ON quote_observation_revision
                FOR EACH ROW
                WHEN (
                    NOT NEW.is_current
                    OR NEW.superseded_at IS NOT NULL
                    OR NEW.revision_number <> COALESCE(
                        (
                            SELECT MAX(revision_number) + 1
                            FROM quote_observation_revision
                            WHERE observation_id = NEW.observation_id
                        ),
                        1
                    )
                    OR (
                        EXISTS (
                            SELECT 1
                            FROM quote_observation_revision
                            WHERE observation_id = NEW.observation_id
                        )
                        AND EXISTS (
                            SELECT 1
                            FROM quote_observation_revision
                            WHERE observation_id = NEW.observation_id
                              AND is_current
                        )
                    )
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'quote_revision_insert_violation: revision must be next/current after prior closure'
                    );
                END
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_quote_observation_revision_delete_guard
                BEFORE DELETE ON quote_observation_revision
                FOR EACH ROW
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'quote_revision_immutable_violation: delete is forbidden'
                    );
                END
                """
            )
        )


def upgrade() -> None:
    connection = op.get_bind()
    _preflight_legacy_market_data(connection)
    op.create_table(
        "quote_series",
        sa.Column("quote_series_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("metric_family", sa.String(), nullable=False),
        sa.Column("quote_basis", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("data_updated_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "((metric_family = 'price' AND quote_basis IN "
            "('last', 'close', 'adjusted_close', 'clean_price', 'dirty_price', 'par')) "
            "OR (metric_family = 'nav' AND quote_basis IN "
            "('official_nav', 'total_return_nav', 'cumulative_nav', "
            "'accumulated_nav', 'cum_nav', 'dividend_adjusted_nav', 'reinvested_nav')) "
            "OR (metric_family = 'fx' AND quote_basis = 'spot'))",
            name="metric_basis",
        ),
        sa.CheckConstraint(
            "length(trim(currency)) BETWEEN 1 AND 8 "
            "AND currency = upper(trim(currency))",
            name="currency",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.instrument_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("quote_series_id"),
        sa.UniqueConstraint(
            "instrument_id",
            "metric_family",
            "quote_basis",
            "currency",
            name="uq_quote_series_instrument_metric_basis_currency",
        ),
    )
    op.create_table(
        "quote_observation",
        sa.Column("observation_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("quote_series_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["quote_series_id"],
            ["quote_series.quote_series_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("observation_id"),
        sa.UniqueConstraint(
            "quote_series_id",
            "as_of_date",
            name="uq_quote_observation_series_date",
        ),
    )
    op.create_table(
        "quote_observation_revision",
        sa.Column("revision_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("observation_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("value", QUOTE_VALUE_TYPE, nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "revision_number > 0",
            name="revision_number_positive",
        ),
        sa.CheckConstraint(
            "(is_current AND superseded_at IS NULL) OR "
            "((NOT is_current) AND superseded_at IS NOT NULL)",
            name="current_state",
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'rejected', 'withdrawn')",
            name="status",
        ),
        sa.CheckConstraint(
            "(status = 'withdrawn' AND value IS NULL) OR "
            "(status <> 'withdrawn' AND value IS NOT NULL "
            "AND CAST(value AS NUMERIC) > 0)",
            name="value_status",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["quote_observation.observation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("revision_id"),
        sa.UniqueConstraint(
            "observation_id",
            "revision_number",
            name="uq_quote_observation_revision_number",
        ),
    )

    _backfill_legacy_market_data(connection)

    op.create_index(
        "uq_quote_observation_revision_current",
        "quote_observation_revision",
        ["observation_id"],
        unique=True,
        sqlite_where=sa.text("is_current"),
        postgresql_where=sa.text("is_current"),
    )
    _create_revision_immutability_guards(connection)
    op.drop_table("instrument_market_data")


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0008 is intentionally irreversible: flattening quote revisions "
        "would destroy superseded and withdrawn audit history. Restore from a "
        "pre-upgrade backup instead."
    )
