"""Make private-fund NAV semantics canonical and delete untrusted returns.

Revision ID: 20260715_0012
Revises: 20260715_0011

The registry now has exactly two NAV quote identities:

* ``official_nav`` is the published unit NAV used for valuation.
* ``total_return_nav`` is a dividend-reinvested, time-weighted NAV.

Cash-cumulative values (unit NAV plus historical cash distributions) are not a
return index and are removed rather than retained under an ambiguous alias.
Fund return and chart policies are also made exact: absent a trusted
``total_return_nav``, the result is unavailable instead of falling back to unit
NAV or a traded price.

Before any destructive cleanup, the migration verifies the Platform-owned
``legacy_nav_snapshot_v1`` manifest and every source NAV observation against
``fund_nav_raw_observation``.  The data cleanup is intentionally irreversible
inside the Registry; retained raw evidence is the source for later, explicit
re-derivation.  The migration is intentionally irreversible: a downgrade
would destroy audit history and still could not recreate deleted observations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0012"
down_revision: str | None = "20260715_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


QUOTE_IDENTITY_CHECK_NAME = "quote_identity_contract"
NAV_LINEAGE_CHECK_NAME = "nav_lineage_contract"
MARKET_DATA_TRIGGER_NAME = "trg_instrument_market_data_price_contract"
INSTRUMENT_TRIGGER_NAME = "trg_instrument_type_price_contract"
FUND_NAV_EVENT_TRIGGER_NAME = "trg_fund_nav_event_instrument_contract"
FUND_NAV_EVIDENCE_TRIGGER_NAME = "trg_fund_nav_reinvestment_evidence_contract"
FUND_NAV_RUN_TRIGGER_NAME = "trg_fund_nav_projection_run_contract"
FUND_NAV_RUN_EVENT_TRIGGER_NAME = "trg_fund_nav_projection_run_event_contract"
FUND_NAV_RUN_EVIDENCE_TRIGGER_NAME = (
    "trg_fund_nav_projection_run_reinvestment_evidence_contract"
)
FUND_NAV_FACTOR_TRIGGER_NAME = "trg_fund_nav_factor_event_contract"
FUND_NAV_CURRENT_TRIGGER_NAME = "trg_fund_nav_current_projection_contract"
FUND_NAV_INSTRUMENT_TRIGGER_NAME = "trg_instrument_fund_nav_contract"
FUND_NAV_MARKET_DATA_TRIGGER_NAME = "trg_fund_nav_market_data_factor_contract"
FUND_NAV_MARKET_DATA_FACTOR_FK_NAME = (
    "fk_instrument_market_data_fund_nav_adjustment_factor_id_factor"
)
POSTGRES_FUND_NAV_EVENT_FUNCTION = "enforce_fund_nav_event_instrument_contract"
POSTGRES_FUND_NAV_EVIDENCE_FUNCTION = "enforce_fund_nav_reinvestment_evidence_contract"
POSTGRES_FUND_NAV_RUN_FUNCTION = "enforce_fund_nav_projection_run_contract"
POSTGRES_FUND_NAV_RUN_EVENT_FUNCTION = "enforce_fund_nav_projection_run_event_contract"
POSTGRES_FUND_NAV_RUN_EVIDENCE_FUNCTION = (
    "enforce_fund_nav_projection_run_reinvestment_evidence_contract"
)
POSTGRES_FUND_NAV_FACTOR_FUNCTION = "enforce_fund_nav_factor_event_contract"
POSTGRES_FUND_NAV_CURRENT_FUNCTION = "enforce_fund_nav_current_projection_contract"
POSTGRES_FUND_NAV_INSTRUMENT_FUNCTION = "enforce_instrument_fund_nav_contract"
POSTGRES_FUND_NAV_MARKET_DATA_FUNCTION = "enforce_fund_nav_market_data_factor_contract"
LEGACY_SNAPSHOT_METADATA_KEY = "legacy_nav_snapshot_v1"
LEGACY_SNAPSHOT_SOURCE_KIND = "legacy_registry_snapshot"
LEGACY_SNAPSHOT_SOURCE_REF = "instrument_registry@20260715_0011"

PRICE_QUOTE_BASES = (
    "last",
    "close",
    "adjusted_close",
    "clean_price",
    "dirty_price",
    "par",
    "accrued_interest",
)
CANONICAL_NAV_QUOTE_BASES = ("official_nav", "total_return_nav")
LEGACY_NAV_QUOTE_BASES = (
    *CANONICAL_NAV_QUOTE_BASES,
    "cumulative_nav",
    "accumulated_nav",
    "cum_nav",
    "dividend_adjusted_nav",
    "reinvested_nav",
)
REMOVED_NAV_QUOTE_BASES = tuple(
    basis
    for basis in LEGACY_NAV_QUOTE_BASES
    if basis not in CANONICAL_NAV_QUOTE_BASES
)
QUOTE_SELECTION_POLICY_ROLES = (
    "trading",
    "valuation",
    "total_return",
    "chart",
    "reference",
)
EXACT_FUND_NAV_DECIMAL = sa.Numeric(38, 18).with_variant(sa.Text(), "sqlite")

def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _quote_identity_sql(nav_quote_bases: Sequence[str]) -> str:
    return f"""
    (
        (metric_family = 'price' AND quote_basis IN ({_quoted(PRICE_QUOTE_BASES)}))
        OR (metric_family = 'nav' AND quote_basis IN ({_quoted(nav_quote_bases)}))
        OR (metric_family = 'fx' AND quote_basis = 'spot')
    )
    """.strip()


NAV_LINEAGE_SQL = """
(
    metric_family <> 'nav'
    AND nav_lineage_kind IS NULL
    AND nav_derivation_method_version IS NULL
    AND nav_derivation_anchor_date IS NULL
    AND nav_lineage_evidence_json IS NULL
    AND fund_nav_adjustment_factor_id IS NULL
)
OR
(
    metric_family = 'nav'
    AND nav_lineage_evidence_json IS NOT NULL
    AND length(trim(CAST(nav_lineage_evidence_json AS TEXT))) > 2
    AND lower(trim(CAST(nav_lineage_evidence_json AS TEXT)))
        NOT IN ('{}', 'null')
    AND (
        (
            quote_basis = 'official_nav'
            AND nav_lineage_kind = 'provider_explicit'
            AND nav_derivation_method_version IS NULL
            AND nav_derivation_anchor_date IS NULL
            AND fund_nav_adjustment_factor_id IS NULL
        )
        OR
        (
            quote_basis = 'total_return_nav'
            AND nav_lineage_kind = 'provider_explicit'
            AND status = 'complete'
            AND nav_derivation_method_version IS NULL
            AND nav_derivation_anchor_date IS NULL
            AND fund_nav_adjustment_factor_id IS NOT NULL
        )
        OR
        (
            quote_basis = 'total_return_nav'
            AND nav_lineage_kind = 'derived_dividend_reinvestment'
            AND status = 'complete'
            AND length(trim(nav_derivation_method_version)) > 0
            AND nav_derivation_anchor_date IS NOT NULL
            AND fund_nav_adjustment_factor_id IS NOT NULL
        )
    )
)
""".strip()


def _json_mapping(value: object, *, context: str) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{context} is not valid JSON.") from error
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError(f"{context} is not a JSON object.")


def _assert_legacy_nav_snapshot_is_complete(connection: sa.Connection) -> None:
    """Fail before DDL unless Platform retained every legacy NAV value."""

    source_schema = None
    if connection.dialect.name == "postgresql":
        source_schema = connection.scalar(sa.text("SELECT current_schema()"))
        if not isinstance(source_schema, str) or not source_schema.strip():
            raise RuntimeError(
                "Cannot determine the target Registry schema for 20260715_0012."
            )
    platform_schema = "platform" if connection.dialect.name == "postgresql" else None
    metadata = sa.MetaData()
    source = sa.Table(
        "instrument_market_data",
        metadata,
        schema=source_schema,
        autoload_with=connection,
    )
    source_rows = connection.execute(
        sa.select(
            source.c.instrument_id,
            source.c.as_of_date,
            source.c.currency,
            source.c.quote_basis,
            source.c.value,
            source.c.provider,
            source.c.status,
        )
        .where(source.c.metric_family == "nav")
        .order_by(
            source.c.instrument_id,
            source.c.as_of_date,
            source.c.currency,
            source.c.quote_basis,
        )
    ).mappings().all()
    if not source_rows:
        return

    inspector = sa.inspect(connection)
    required_tables = ("platform_metadata", "fund_nav_raw_observation")
    missing_tables = [
        table_name
        for table_name in required_tables
        if not inspector.has_table(table_name, schema=platform_schema)
    ]
    if missing_tables:
        raise RuntimeError(
            "Cannot apply 20260715_0012 before the Platform legacy NAV snapshot; "
            "missing table(s): " + ", ".join(missing_tables) + "."
        )

    platform_metadata = sa.Table(
        "platform_metadata",
        metadata,
        schema=platform_schema,
        autoload_with=connection,
    )
    raw = sa.Table(
        "fund_nav_raw_observation",
        metadata,
        schema=platform_schema,
        autoload_with=connection,
    )
    manifest_value = connection.scalar(
        sa.select(platform_metadata.c.value_json).where(
            platform_metadata.c.metadata_key == LEGACY_SNAPSHOT_METADATA_KEY
        )
    )
    manifest = _json_mapping(
        manifest_value,
        context="Platform legacy_nav_snapshot_v1 manifest",
    )
    if (
        manifest.get("status") != "complete"
        or manifest.get("source_registry_revision") != "20260715_0011"
        or not str(manifest.get("captured_at") or "").strip()
    ):
        raise RuntimeError(
            "Cannot apply 20260715_0012: Platform legacy NAV manifest is incomplete."
        )
    try:
        manifest_source_count = int(manifest["source_row_count"])
        manifest_observation_count = int(manifest["observation_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(
            "Cannot apply 20260715_0012: Platform legacy NAV manifest counts are invalid."
        ) from error
    if manifest_source_count != len(source_rows):
        raise RuntimeError(
            "Cannot apply 20260715_0012: legacy NAV source row count changed after "
            "the Platform snapshot."
        )

    raw_rows = connection.execute(
        sa.select(raw).where(
            raw.c.source_kind == LEGACY_SNAPSHOT_SOURCE_KIND,
            raw.c.source_ref == LEGACY_SNAPSHOT_SOURCE_REF,
        )
    ).mappings().all()
    expected_keys = {
        (row["instrument_id"], row["as_of_date"], row["currency"])
        for row in source_rows
    }
    if (
        manifest_observation_count != len(raw_rows)
        or len(raw_rows) != len(expected_keys)
    ):
        raise RuntimeError(
            "Cannot apply 20260715_0012: Platform legacy NAV snapshot coverage "
            "count is incomplete."
        )
    raw_by_key = {
        (row["instrument_id"], row["as_of_date"], row["currency"]): row
        for row in raw_rows
    }
    if set(raw_by_key) != expected_keys:
        raise RuntimeError(
            "Cannot apply 20260715_0012: Platform legacy NAV snapshot identities "
            "do not match the Registry."
        )

    basis_columns = {
        "official_nav": (
            "unit_nav_value",
            "unit_nav_source_provider",
            "unit_nav_status",
        ),
        "cumulative_nav": (
            "cash_cumulative_nav_value",
            "cash_cumulative_source_provider",
            "cash_cumulative_nav_status",
        ),
        "accumulated_nav": (
            "cash_cumulative_nav_value",
            "cash_cumulative_source_provider",
            "cash_cumulative_nav_status",
        ),
        "cum_nav": (
            "cash_cumulative_nav_value",
            "cash_cumulative_source_provider",
            "cash_cumulative_nav_status",
        ),
        "total_return_nav": (
            "observed_total_return_nav_value",
            "total_return_source_provider",
            "observed_total_return_nav_status",
        ),
        "dividend_adjusted_nav": (
            "observed_total_return_nav_value",
            "total_return_source_provider",
            "observed_total_return_nav_status",
        ),
        "reinvested_nav": (
            "observed_total_return_nav_value",
            "total_return_source_provider",
            "observed_total_return_nav_status",
        ),
    }
    total_return_keys: set[tuple[object, object, object]] = set()
    for source_row in source_rows:
        quote_basis = str(source_row["quote_basis"])
        raw_columns = basis_columns.get(quote_basis)
        if raw_columns is None:
            raise RuntimeError(
                f'Cannot snapshot unsupported legacy NAV basis "{quote_basis}".'
            )
        key = (
            source_row["instrument_id"],
            source_row["as_of_date"],
            source_row["currency"],
        )
        raw_row = raw_by_key[key]
        value_column, provider_column, status_column = raw_columns
        if (
            raw_row[value_column] != source_row["value"]
            or raw_row[provider_column] != source_row["provider"]
            or raw_row[status_column] != source_row["status"]
        ):
            raise RuntimeError(
                "Cannot apply 20260715_0012: Platform raw evidence does not "
                f'exactly cover {key[0]}/{key[1]}/{quote_basis}.'
            )
        if quote_basis in {
            "total_return_nav",
            "dividend_adjusted_nav",
            "reinvested_nav",
        }:
            total_return_keys.add(key)
    if any(
        (row["instrument_id"], row["as_of_date"], row["currency"])
        in total_return_keys
        and row["total_return_semantics"] != "legacy_unverified"
        for row in raw_rows
    ):
        raise RuntimeError(
            "Cannot apply 20260715_0012: legacy total-return evidence was not "
            "marked unverified."
        )


def _as_policy_mapping(value: object, *, instrument_id: str) -> dict[str, object]:
    return _json_mapping(
        value,
        context=f'Instrument "{instrument_id}" quote-selection policy',
    )


def _normalize_quote_selection_policies(connection: sa.Connection) -> None:
    """Remove retired aliases without inventing replacement price semantics."""

    update_statement = sa.text(
        "UPDATE instrument SET quote_selection_policy_json = :policy "
        "WHERE instrument_id = :instrument_id"
    ).bindparams(sa.bindparam("policy", type_=sa.JSON()))
    rows = connection.execute(
        sa.text(
            "SELECT instrument_id, instrument_type, quote_selection_policy_json "
            "FROM instrument ORDER BY instrument_id"
        )
    ).mappings()
    removed = set(REMOVED_NAV_QUOTE_BASES)
    for row in rows:
        instrument_id = str(row["instrument_id"])
        instrument_type = str(row["instrument_type"] or "").strip().lower()
        policy = _as_policy_mapping(
            row["quote_selection_policy_json"],
            instrument_id=instrument_id,
        )
        if set(policy) != set(QUOTE_SELECTION_POLICY_ROLES):
            raise RuntimeError(
                f'Instrument "{instrument_id}" has an incomplete quote-selection policy.'
            )
        normalized: dict[str, list[str]] = {}
        for role in QUOTE_SELECTION_POLICY_ROLES:
            raw_values = policy[role]
            if not isinstance(raw_values, list) or not raw_values:
                raise RuntimeError(
                    f'Instrument "{instrument_id}" has an invalid {role} policy.'
                )
            values = [str(value or "").strip() for value in raw_values]
            if any(not value for value in values):
                raise RuntimeError(
                    f'Instrument "{instrument_id}" has a blank {role} quote basis.'
                )
            filtered = [value for value in values if value not in removed]
            if instrument_type == "fund" and role in {"total_return", "chart"}:
                filtered = ["total_return_nav"]
            if not filtered:
                raise RuntimeError(
                    f'Instrument "{instrument_id}" {role} policy has no canonical basis.'
                )
            normalized[role] = filtered
        connection.execute(
            update_statement,
            {"instrument_id": instrument_id, "policy": normalized},
        )


def _delete_noncanonical_nav_data(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            "DELETE FROM instrument_market_data "
            "WHERE metric_family = 'nav' AND quote_basis IN :quote_bases"
        ).bindparams(sa.bindparam("quote_bases", expanding=True)),
        {
            "quote_bases": [
                *REMOVED_NAV_QUOTE_BASES,
                "total_return_nav",
            ]
        },
    )


def _backfill_official_nav_lineage(connection: sa.Connection) -> None:
    statement = sa.text(
        "UPDATE instrument_market_data "
        "SET nav_lineage_kind = 'provider_explicit', "
        "nav_lineage_evidence_json = :evidence "
        "WHERE metric_family = 'nav' AND quote_basis = 'official_nav'"
    ).bindparams(sa.bindparam("evidence", type_=sa.JSON()))
    connection.execute(
        statement,
        {
            "evidence": {
                "classification": "legacy_official_nav_quote_basis",
                "raw_snapshot_ref": LEGACY_SNAPSHOT_SOURCE_REF,
            }
        },
    )


def _sqlite_positive_decimal_sql(column: str) -> str:
    value = f"lower(trim({column}))"
    exponent_position = f"instr({value}, 'e')"
    mantissa = (
        f"(CASE WHEN {exponent_position} > 0 "
        f"THEN substr({value}, 1, {exponent_position} - 1) ELSE {value} END)"
    )
    exponent = f"substr({value}, {exponent_position} + 1)"
    unsigned_mantissa = (
        f"(CASE WHEN substr({mantissa}, 1, 1) IN ('+', '-') "
        f"THEN substr({mantissa}, 2) ELSE {mantissa} END)"
    )
    unsigned_exponent = (
        f"(CASE WHEN substr({exponent}, 1, 1) IN ('+', '-') "
        f"THEN substr({exponent}, 2) ELSE {exponent} END)"
    )
    return f"""
        {value} <> ''
        AND {value} NOT GLOB '*[^0-9e.+-]*'
        AND length({value}) - length(replace({value}, 'e', '')) <= 1
        AND {unsigned_mantissa} <> ''
        AND {unsigned_mantissa} GLOB '*[0-9]*'
        AND {unsigned_mantissa} NOT GLOB '*[^0-9.]*'
        AND length({unsigned_mantissa})
            - length(replace({unsigned_mantissa}, '.', '')) <= 1
        AND substr({mantissa}, 1, 1) <> '-'
        AND replace(replace({unsigned_mantissa}, '.', ''), '0', '') <> ''
        AND (
            {exponent_position} = 0
            OR (
                {unsigned_exponent} <> ''
                AND {unsigned_exponent} GLOB '*[0-9]*'
                AND {unsigned_exponent} NOT GLOB '*[^0-9]*'
            )
        )
    """.strip()


def _sqlite_exact_fund_nav_decimal_sql(column: str) -> str:
    """Validate the canonical fixed-point NUMERIC(38, 18) text form."""

    value = f"trim({column})"
    dot_position = f"instr({value}, '.')"
    integer_part = (
        f"(CASE WHEN {dot_position} > 0 "
        f"THEN substr({value}, 1, {dot_position} - 1) ELSE {value} END)"
    )
    fractional_part = (
        f"(CASE WHEN {dot_position} > 0 "
        f"THEN substr({value}, {dot_position} + 1) ELSE '' END)"
    )
    return f"""
        {value} <> ''
        AND {value} NOT GLOB '*[^0-9.]*'
        AND length({value}) - length(replace({value}, '.', '')) <= 1
        AND {integer_part} <> ''
        AND {integer_part} NOT GLOB '*[^0-9]*'
        AND length({integer_part}) <= 20
        AND (
            {dot_position} = 0
            OR (
                {fractional_part} <> ''
                AND {fractional_part} NOT GLOB '*[^0-9]*'
                AND length({fractional_part}) <= 18
            )
        )
        AND replace(replace({value}, '.', ''), '0', '') <> ''
    """.strip()


def _sqlite_market_data_trigger_sql(
    *,
    operation: str,
    nav_quote_bases: Sequence[str],
) -> str:
    trigger_name = f"{MARKET_DATA_TRIGGER_NAME}_{operation.lower()}"
    positive_decimal_sql = _sqlite_positive_decimal_sql("NEW.value")
    return f"""
    CREATE TRIGGER {trigger_name}
    BEFORE {operation} ON instrument_market_data
    FOR EACH ROW
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM instrument
            WHERE instrument.instrument_id = NEW.instrument_id
              AND (
                  (NEW.metric_family = 'price'
                   AND NEW.quote_basis IN ({_quoted(PRICE_QUOTE_BASES)}))
                  OR (NEW.metric_family = 'nav'
                      AND NEW.quote_basis IN ({_quoted(nav_quote_bases)}))
                  OR (NEW.metric_family = 'fx' AND NEW.quote_basis = 'spot')
              )
              AND NOT (NEW.quote_basis = 'accrued_interest'
                       AND instrument.instrument_type <> 'bond')
              AND NEW.currency = instrument.currency
              AND NEW.status IN ('complete', 'partial', 'unavailable')
              AND ({positive_decimal_sql})
              AND ((instrument.instrument_type = 'fx') =
                   (NEW.metric_family = 'fx' AND NEW.quote_basis = 'spot'))
              AND (
                  instrument.instrument_type <> 'fx'
                  OR (NEW.instrument_id = 'fx-usd-hkd' AND instrument.currency = 'HKD')
                  OR (NEW.instrument_id = 'fx-usd-cny' AND instrument.currency = 'CNY')
              )
              AND NEW.price_unit = CASE
                  WHEN instrument.instrument_type = 'bond'
                       AND NEW.metric_family = 'price' THEN 'percent_of_par'
                  WHEN instrument.instrument_type = 'fx' THEN 'rate'
                  ELSE 'per_unit' END
              AND NEW.price_scale = CASE
                  WHEN instrument.instrument_type = 'bond'
                       AND NEW.metric_family = 'price' THEN 0.01
                  ELSE 1 END
        ) THEN RAISE(ABORT, 'instrument market-data price contract is not canonical') END;
    END
    """


def _sqlite_instrument_trigger_sql(*, operation: str) -> str:
    trigger_name = (
        f"{INSTRUMENT_TRIGGER_NAME}_{operation.lower()}"
        if operation == "INSERT"
        else INSTRUMENT_TRIGGER_NAME
    )
    update_clause = (
        "UPDATE OF instrument_type, currency" if operation == "UPDATE" else "INSERT"
    )
    return f"""
    CREATE TRIGGER {trigger_name}
    BEFORE {update_clause} ON instrument
    FOR EACH ROW
    WHEN NOT (
        (
            NEW.instrument_type = 'fx'
            AND ((NEW.instrument_id = 'fx-usd-hkd' AND NEW.currency = 'HKD')
                 OR (NEW.instrument_id = 'fx-usd-cny' AND NEW.currency = 'CNY'))
        )
        OR (
            NEW.instrument_type <> 'fx'
            AND NEW.instrument_id NOT IN ('fx-usd-hkd', 'fx-usd-cny')
        )
    )
    OR EXISTS (
        SELECT 1 FROM instrument_market_data AS market_data
        WHERE market_data.instrument_id = NEW.instrument_id
          AND (
              market_data.currency <> NEW.currency
              OR ((NEW.instrument_type = 'fx') <>
                  (market_data.metric_family = 'fx'
                   AND market_data.quote_basis = 'spot'))
              OR (market_data.quote_basis = 'accrued_interest'
                  AND NEW.instrument_type <> 'bond')
              OR market_data.price_unit <> CASE
                  WHEN NEW.instrument_type = 'bond'
                       AND market_data.metric_family = 'price'
                      THEN 'percent_of_par'
                  WHEN NEW.instrument_type = 'fx' THEN 'rate'
                  ELSE 'per_unit' END
              OR market_data.price_scale <> CASE
                  WHEN NEW.instrument_type = 'bond'
                       AND market_data.metric_family = 'price' THEN 0.01
                  ELSE 1 END
          )
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'instrument_type update conflicts with canonical market-data contract'
        );
    END
    """


def _drop_sqlite_contract_triggers(connection: sa.Connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    for trigger_name in (
        f"{MARKET_DATA_TRIGGER_NAME}_insert",
        f"{MARKET_DATA_TRIGGER_NAME}_update",
        INSTRUMENT_TRIGGER_NAME,
        f"{INSTRUMENT_TRIGGER_NAME}_insert",
    ):
        connection.execute(
            sa.text(f"DROP TRIGGER IF EXISTS {trigger_name}")
        )


def _create_sqlite_contract_triggers(
    connection: sa.Connection,
    *,
    nav_quote_bases: Sequence[str],
) -> None:
    if connection.dialect.name != "sqlite":
        return
    _drop_sqlite_contract_triggers(connection)
    for operation in ("INSERT", "UPDATE"):
        connection.execute(
            sa.text(
                _sqlite_market_data_trigger_sql(
                    operation=operation,
                    nav_quote_bases=nav_quote_bases,
                )
            )
        )
        connection.execute(
            sa.text(_sqlite_instrument_trigger_sql(operation=operation))
        )


def _create_fund_nav_ledger_tables() -> None:
    op.create_table(
        "fund_nav_event",
        sa.Column("fund_nav_event_id", sa.String(), nullable=False),
        sa.Column("fund_nav_action_id", sa.String(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("revision_kind", sa.String(), nullable=False),
        sa.Column("supersedes_fund_nav_event_id", sa.String(), nullable=True),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("announcement_date", sa.Date(), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("payable_date", sa.Date(), nullable=True),
        sa.Column("sequence_order", sa.Integer(), nullable=True),
        sa.Column("cash_per_unit", EXACT_FUND_NAV_DECIMAL, nullable=True),
        sa.Column("unit_ratio", EXACT_FUND_NAV_DECIMAL, nullable=True),
        sa.Column("evidence_kind", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_event_id", sa.String(), nullable=True),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("recorded_by", sa.String(), nullable=False),
        sa.Column("revision_reason", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('cash_distribution', 'unit_split')",
            name=op.f("ck_fund_nav_event_fund_nav_event_type_contract"),
        ),
        sa.CheckConstraint(
            "revision_kind IN ('original', 'correction', 'cancellation')",
            name=op.f("ck_fund_nav_event_fund_nav_event_revision_kind_contract"),
        ),
        sa.CheckConstraint(
            "(revision_kind = 'original' AND revision_number = 1 "
            "AND supersedes_fund_nav_event_id IS NULL) OR "
            "(revision_kind IN ('correction', 'cancellation') "
            "AND revision_number > 1 "
            "AND supersedes_fund_nav_event_id IS NOT NULL)",
            name=op.f("ck_fund_nav_event_fund_nav_event_revision_contract"),
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('provider_notice', 'manual_verified')",
            name=op.f("ck_fund_nav_event_fund_nav_event_evidence_contract"),
        ),
        sa.CheckConstraint(
            "record_date IS NULL OR record_date <= effective_date",
            name=op.f("ck_fund_nav_event_fund_nav_event_date_contract"),
        ),
        sa.CheckConstraint(
            "sequence_order IS NULL OR sequence_order >= 1",
            name=op.f("ck_fund_nav_event_fund_nav_event_sequence_contract"),
        ),
        sa.CheckConstraint(
            "(event_type = 'cash_distribution' "
            "AND CAST(cash_per_unit AS NUMERIC) > 0 "
            "AND unit_ratio IS NULL) OR "
            "(event_type = 'unit_split' "
            "AND CAST(unit_ratio AS NUMERIC) > 0 "
            "AND CAST(unit_ratio AS NUMERIC) <> 1 "
            "AND cash_per_unit IS NULL)",
            name=op.f("ck_fund_nav_event_fund_nav_event_payload_contract"),
        ),
        sa.CheckConstraint(
            "length(trim(fund_nav_event_id)) > 0 "
            "AND length(trim(fund_nav_action_id)) > 0 "
            "AND length(trim(source)) > 0 "
            "AND (external_event_id IS NULL "
            "OR length(trim(external_event_id)) > 0) "
            "AND length(trim(recorded_by)) > 0 "
            "AND length(trim(revision_reason)) > 0 "
            "AND length(trim(created_at)) > 0 "
            "AND length(trim(updated_at)) > 0 "
            "AND length(trim(CAST(provenance_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(provenance_json AS TEXT))) "
            "NOT IN ('{}', 'null')",
            name=op.f("ck_fund_nav_event_fund_nav_event_audit_contract"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.instrument_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_fund_nav_event_id"],
            ["fund_nav_event.fund_nav_event_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("fund_nav_event_id"),
        sa.UniqueConstraint(
            "fund_nav_action_id",
            "revision_number",
            name="uq_fund_nav_event_action_revision",
        ),
        sa.UniqueConstraint(
            "supersedes_fund_nav_event_id",
            name="uq_fund_nav_event_supersedes",
        ),
    )
    op.create_index(
        "ix_fund_nav_event_instrument_effective_date",
        "fund_nav_event",
        ["instrument_id", "effective_date"],
        unique=False,
    )

    op.create_table(
        "fund_nav_reinvestment_evidence",
        sa.Column("fund_nav_reinvestment_evidence_id", sa.String(), nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("fund_nav_event_id", sa.String(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("revision_kind", sa.String(), nullable=False),
        sa.Column(
            "supersedes_fund_nav_reinvestment_evidence_id",
            sa.String(),
            nullable=True,
        ),
        sa.Column("reinvestment_nav", EXACT_FUND_NAV_DECIMAL, nullable=False),
        sa.Column("evidence_kind", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_evidence_id", sa.String(), nullable=True),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("recorded_by", sa.String(), nullable=False),
        sa.Column("revision_reason", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "revision_kind IN ('original', 'correction', 'cancellation') "
            "AND ((revision_kind = 'original' AND revision_number = 1 "
            "AND supersedes_fund_nav_reinvestment_evidence_id IS NULL) OR "
            "(revision_kind IN ('correction', 'cancellation') "
            "AND revision_number > 1 "
            "AND supersedes_fund_nav_reinvestment_evidence_id IS NOT NULL))",
            name=op.f(
                "ck_fund_nav_reinvestment_evidence_"
                "fund_nav_reinvestment_evidence_revision_contract"
            ),
        ),
        sa.CheckConstraint(
            "CAST(reinvestment_nav AS NUMERIC) > 0",
            name=op.f(
                "ck_fund_nav_reinvestment_evidence_"
                "fund_nav_reinvestment_evidence_value_contract"
            ),
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('provider_notice', 'manual_verified')",
            name=op.f(
                "ck_fund_nav_reinvestment_evidence_"
                "fund_nav_reinvestment_evidence_kind_contract"
            ),
        ),
        sa.CheckConstraint(
            "length(trim(fund_nav_reinvestment_evidence_id)) > 0 "
            "AND length(trim(source)) > 0 "
            "AND (external_evidence_id IS NULL "
            "OR length(trim(external_evidence_id)) > 0) "
            "AND length(trim(recorded_by)) > 0 "
            "AND length(trim(revision_reason)) > 0 "
            "AND length(trim(created_at)) > 0 "
            "AND length(trim(updated_at)) > 0 "
            "AND length(trim(CAST(provenance_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(provenance_json AS TEXT))) "
            "NOT IN ('{}', 'null')",
            name=op.f(
                "ck_fund_nav_reinvestment_evidence_"
                "fund_nav_reinvestment_evidence_audit_contract"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.instrument_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fund_nav_event_id"],
            ["fund_nav_event.fund_nav_event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_fund_nav_reinvestment_evidence_id"],
            [
                "fund_nav_reinvestment_evidence."
                "fund_nav_reinvestment_evidence_id"
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("fund_nav_reinvestment_evidence_id"),
        sa.UniqueConstraint(
            "fund_nav_event_id",
            "revision_number",
            name="uq_fund_nav_reinvestment_evidence_event_revision",
        ),
        sa.UniqueConstraint(
            "supersedes_fund_nav_reinvestment_evidence_id",
            name="uq_fund_nav_reinvestment_evidence_supersedes",
        ),
    )
    op.create_index(
        "ix_fund_nav_reinvestment_evidence_instrument_event",
        "fund_nav_reinvestment_evidence",
        ["instrument_id", "fund_nav_event_id"],
        unique=False,
    )

    op.create_table(
        "fund_nav_projection_run",
        sa.Column("fund_nav_projection_run_id", sa.String(), nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "source_observation_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("projection_kind", sa.String(), nullable=False),
        sa.Column("projection_status", sa.String(), nullable=False),
        sa.Column("method_version", sa.String(), nullable=False),
        sa.Column("anchor_date", sa.Date(), nullable=True),
        sa.Column("source_provider", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "fund_nav_projection_run_id = "
            "'fund-nav-projection-' || input_fingerprint "
            "AND length(input_fingerprint) = 64 "
            "AND input_fingerprint = lower(input_fingerprint) "
            "AND length(source_observation_fingerprint) = 64 "
            "AND source_observation_fingerprint = "
            "lower(source_observation_fingerprint) "
            "AND projection_kind IN ('provider_explicit', 'event_derived', "
            "'hybrid_reanchored') "
            "AND projection_status IN ('complete', 'partial', 'unavailable') "
            "AND ((projection_status IN ('complete', 'partial') "
            "AND anchor_date IS NOT NULL) OR "
            "(projection_status = 'unavailable' "
            "AND anchor_date IS NULL "
            "AND projection_kind <> 'hybrid_reanchored')) "
            "AND length(trim(method_version)) > 0 "
            "AND length(trim(source_provider)) > 0 "
            "AND length(trim(created_by)) > 0 "
            "AND length(trim(created_at)) > 0 "
            "AND length(trim(CAST(evidence_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(evidence_json AS TEXT))) "
            "NOT IN ('{}', 'null')",
            name=op.f(
                "ck_fund_nav_projection_run_"
                "fund_nav_projection_run_audit_contract"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.instrument_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("fund_nav_projection_run_id"),
        sa.UniqueConstraint(
            "instrument_id",
            "input_fingerprint",
            name="uq_fund_nav_projection_run_input",
        ),
    )

    op.create_table(
        "fund_nav_projection_run_event",
        sa.Column("fund_nav_projection_run_id", sa.String(), nullable=False),
        sa.Column("fund_nav_event_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["fund_nav_projection_run_id"],
            ["fund_nav_projection_run.fund_nav_projection_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fund_nav_event_id"],
            ["fund_nav_event.fund_nav_event_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "fund_nav_projection_run_id",
            "fund_nav_event_id",
        ),
    )
    op.create_table(
        "fund_nav_projection_run_reinvestment_evidence",
        sa.Column("fund_nav_projection_run_id", sa.String(), nullable=False),
        sa.Column(
            "fund_nav_reinvestment_evidence_id",
            sa.String(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["fund_nav_projection_run_id"],
            ["fund_nav_projection_run.fund_nav_projection_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fund_nav_reinvestment_evidence_id"],
            [
                "fund_nav_reinvestment_evidence."
                "fund_nav_reinvestment_evidence_id"
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "fund_nav_projection_run_id",
            "fund_nav_reinvestment_evidence_id",
        ),
    )

    op.create_table(
        "fund_nav_adjustment_factor",
        sa.Column("fund_nav_adjustment_factor_id", sa.String(), nullable=False),
        sa.Column("factor_logical_key", sa.String(), nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("fund_nav_projection_run_id", sa.String(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("factor_level", EXACT_FUND_NAV_DECIMAL, nullable=False),
        sa.Column("factor_kind", sa.String(), nullable=False),
        sa.Column("fund_nav_event_id", sa.String(), nullable=True),
        sa.Column(
            "fund_nav_reinvestment_evidence_id",
            sa.String(),
            nullable=True,
        ),
        sa.Column(
            "previous_fund_nav_adjustment_factor_id",
            sa.String(),
            nullable=True,
        ),
        sa.Column("evidence_kind", sa.String(), nullable=False),
        sa.Column("method_version", sa.String(), nullable=False),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("source_provider", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "CAST(factor_level AS NUMERIC) > 0",
            name=op.f(
                "ck_fund_nav_adjustment_factor_"
                "fund_nav_adjustment_factor_value_contract"
            ),
        ),
        sa.CheckConstraint(
            "factor_kind IN ('provider_implied', 'event_derived')",
            name=op.f(
                "ck_fund_nav_adjustment_factor_"
                "fund_nav_adjustment_factor_kind_contract"
            ),
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('provider_total_return', "
            "'fund_nav_event', 'zero_cash_anchor', "
            "'window_normalized_anchor')",
            name=op.f(
                "ck_fund_nav_adjustment_factor_"
                "fund_nav_adjustment_factor_evidence_contract"
            ),
        ),
        sa.CheckConstraint(
            "length(trim(method_version)) > 0 "
            "AND length(trim(source_provider)) > 0 "
            "AND anchor_date <= as_of_date",
            name=op.f(
                "ck_fund_nav_adjustment_factor_"
                "fund_nav_adjustment_factor_lineage_contract"
            ),
        ),
        sa.CheckConstraint(
            "(factor_kind = 'provider_implied' "
            "AND fund_nav_event_id IS NULL "
            "AND fund_nav_reinvestment_evidence_id IS NULL "
            "AND previous_fund_nav_adjustment_factor_id IS NULL "
            "AND evidence_kind = 'provider_total_return' "
            "AND anchor_date = as_of_date) OR "
            "(factor_kind = 'event_derived' AND ("
            "(evidence_kind IN ('zero_cash_anchor', "
            "'window_normalized_anchor') "
            "AND fund_nav_event_id IS NULL "
            "AND fund_nav_reinvestment_evidence_id IS NULL "
            "AND previous_fund_nav_adjustment_factor_id IS NULL "
            "AND CAST(factor_level AS NUMERIC) = 1 "
            "AND anchor_date = as_of_date) OR "
            "(evidence_kind = 'fund_nav_event' "
            "AND fund_nav_event_id IS NOT NULL "
            "AND previous_fund_nav_adjustment_factor_id IS NOT NULL)))",
            name=op.f(
                "ck_fund_nav_adjustment_factor_"
                "fund_nav_adjustment_factor_source_contract"
            ),
        ),
        sa.CheckConstraint(
            "length(trim(fund_nav_adjustment_factor_id)) > 0 "
            "AND length(trim(factor_logical_key)) > 0 "
            "AND length(trim(created_at)) > 0 "
            "AND length(trim(updated_at)) > 0 "
            "AND length(trim(CAST(evidence_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(evidence_json AS TEXT))) "
            "NOT IN ('{}', 'null')",
            name=op.f(
                "ck_fund_nav_adjustment_factor_"
                "fund_nav_adjustment_factor_audit_contract"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.instrument_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fund_nav_projection_run_id"],
            ["fund_nav_projection_run.fund_nav_projection_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fund_nav_event_id"],
            ["fund_nav_event.fund_nav_event_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fund_nav_reinvestment_evidence_id"],
            [
                "fund_nav_reinvestment_evidence."
                "fund_nav_reinvestment_evidence_id"
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_fund_nav_adjustment_factor_id"],
            [
                "fund_nav_adjustment_factor."
                "fund_nav_adjustment_factor_id"
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("fund_nav_adjustment_factor_id"),
        sa.UniqueConstraint(
            "fund_nav_projection_run_id",
            "factor_logical_key",
            name="uq_fund_nav_adjustment_factor_run_logical_key",
        ),
        sa.UniqueConstraint(
            "fund_nav_projection_run_id",
            "fund_nav_event_id",
            name="uq_fund_nav_adjustment_factor_run_event",
        ),
        sa.UniqueConstraint(
            "fund_nav_projection_run_id",
            "previous_fund_nav_adjustment_factor_id",
            name="uq_fund_nav_adjustment_factor_run_previous",
        ),
    )
    op.create_index(
        "ix_fund_nav_adjustment_factor_instrument_date",
        "fund_nav_adjustment_factor",
        ["instrument_id", "as_of_date"],
        unique=False,
    )
    op.create_index(
        "uq_fund_nav_adjustment_factor_run_anchor",
        "fund_nav_adjustment_factor",
        ["fund_nav_projection_run_id"],
        unique=True,
        sqlite_where=sa.text(
            "evidence_kind IN ('zero_cash_anchor', 'window_normalized_anchor')"
        ),
        postgresql_where=sa.text(
            "evidence_kind IN ('zero_cash_anchor', 'window_normalized_anchor')"
        ),
    )
    op.create_index(
        "uq_fund_nav_adjustment_factor_provider_run_date",
        "fund_nav_adjustment_factor",
        ["fund_nav_projection_run_id", "as_of_date"],
        unique=True,
        sqlite_where=sa.text("factor_kind = 'provider_implied'"),
        postgresql_where=sa.text("factor_kind = 'provider_implied'"),
    )

    op.create_table(
        "fund_nav_current_projection",
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("fund_nav_projection_run_id", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(trim(updated_at)) > 0 "
            "AND length(trim(updated_by)) > 0",
            name=op.f(
                "ck_fund_nav_current_projection_"
                "fund_nav_current_projection_audit_contract"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.instrument_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fund_nav_projection_run_id"],
            ["fund_nav_projection_run.fund_nav_projection_run_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("instrument_id"),
        sa.UniqueConstraint(
            "fund_nav_projection_run_id",
            name="uq_fund_nav_current_projection_run",
        ),
    )


def _drop_fund_nav_ledger_triggers(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        for trigger_name, table_name in (
            (FUND_NAV_EVENT_TRIGGER_NAME, "fund_nav_event"),
            (FUND_NAV_EVIDENCE_TRIGGER_NAME, "fund_nav_reinvestment_evidence"),
            (FUND_NAV_RUN_TRIGGER_NAME, "fund_nav_projection_run"),
            (FUND_NAV_RUN_EVENT_TRIGGER_NAME, "fund_nav_projection_run_event"),
            (
                FUND_NAV_RUN_EVIDENCE_TRIGGER_NAME,
                "fund_nav_projection_run_reinvestment_evidence",
            ),
            (FUND_NAV_FACTOR_TRIGGER_NAME, "fund_nav_adjustment_factor"),
            (FUND_NAV_CURRENT_TRIGGER_NAME, "fund_nav_current_projection"),
            (FUND_NAV_MARKET_DATA_TRIGGER_NAME, "instrument_market_data"),
            (FUND_NAV_INSTRUMENT_TRIGGER_NAME, "instrument"),
        ):
            connection.execute(
                sa.text(
                    f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}"
                )
            )
        for function_name in (
            POSTGRES_FUND_NAV_EVENT_FUNCTION,
            POSTGRES_FUND_NAV_EVIDENCE_FUNCTION,
            POSTGRES_FUND_NAV_RUN_FUNCTION,
            POSTGRES_FUND_NAV_RUN_EVENT_FUNCTION,
            POSTGRES_FUND_NAV_RUN_EVIDENCE_FUNCTION,
            POSTGRES_FUND_NAV_FACTOR_FUNCTION,
            POSTGRES_FUND_NAV_CURRENT_FUNCTION,
            POSTGRES_FUND_NAV_MARKET_DATA_FUNCTION,
            POSTGRES_FUND_NAV_INSTRUMENT_FUNCTION,
        ):
            connection.execute(
                sa.text(f"DROP FUNCTION IF EXISTS {function_name}()")
            )
    elif connection.dialect.name == "sqlite":
        for trigger_name in (
            f"{FUND_NAV_EVENT_TRIGGER_NAME}_insert",
            f"{FUND_NAV_EVENT_TRIGGER_NAME}_update",
            f"{FUND_NAV_EVENT_TRIGGER_NAME}_delete",
            f"{FUND_NAV_EVIDENCE_TRIGGER_NAME}_insert",
            f"{FUND_NAV_EVIDENCE_TRIGGER_NAME}_update",
            f"{FUND_NAV_EVIDENCE_TRIGGER_NAME}_delete",
            f"{FUND_NAV_RUN_TRIGGER_NAME}_insert",
            f"{FUND_NAV_RUN_TRIGGER_NAME}_update",
            f"{FUND_NAV_RUN_TRIGGER_NAME}_delete",
            f"{FUND_NAV_RUN_EVENT_TRIGGER_NAME}_insert",
            f"{FUND_NAV_RUN_EVENT_TRIGGER_NAME}_update",
            f"{FUND_NAV_RUN_EVENT_TRIGGER_NAME}_delete",
            f"{FUND_NAV_RUN_EVIDENCE_TRIGGER_NAME}_insert",
            f"{FUND_NAV_RUN_EVIDENCE_TRIGGER_NAME}_update",
            f"{FUND_NAV_RUN_EVIDENCE_TRIGGER_NAME}_delete",
            f"{FUND_NAV_FACTOR_TRIGGER_NAME}_insert",
            f"{FUND_NAV_FACTOR_TRIGGER_NAME}_update",
            f"{FUND_NAV_FACTOR_TRIGGER_NAME}_delete",
            f"{FUND_NAV_CURRENT_TRIGGER_NAME}_insert",
            f"{FUND_NAV_CURRENT_TRIGGER_NAME}_update",
            f"{FUND_NAV_CURRENT_TRIGGER_NAME}_delete",
            f"{FUND_NAV_MARKET_DATA_TRIGGER_NAME}_insert",
            f"{FUND_NAV_MARKET_DATA_TRIGGER_NAME}_update",
            f"{FUND_NAV_MARKET_DATA_TRIGGER_NAME}_official_update",
            f"{FUND_NAV_MARKET_DATA_TRIGGER_NAME}_official_delete",
            FUND_NAV_INSTRUMENT_TRIGGER_NAME,
        ):
            connection.execute(
                sa.text(f"DROP TRIGGER IF EXISTS {trigger_name}")
            )


def _create_fund_nav_ledger_triggers(connection: sa.Connection) -> None:
    _drop_fund_nav_ledger_triggers(connection)
    if connection.dialect.name == "postgresql":
        _create_postgres_fund_nav_ledger_triggers(connection)
    elif connection.dialect.name == "sqlite":
        _create_sqlite_fund_nav_ledger_triggers(connection)


def _sqlite_guard(
    connection: sa.Connection,
    *,
    name: str,
    table: str,
    operation: str,
    violation: str,
    message: str,
) -> None:
    connection.execute(
        sa.text(
            f"""
            CREATE TRIGGER {name}_{operation.lower()}
            BEFORE {operation} ON {table}
            FOR EACH ROW WHEN {violation}
            BEGIN SELECT RAISE(ABORT, '{message}'); END
            """
        )
    )


def _create_postgres_fund_nav_ledger_triggers(
    connection: sa.Connection,
) -> None:
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {POSTGRES_FUND_NAV_EVENT_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            DECLARE predecessor fund_nav_event%ROWTYPE;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'fund NAV event revisions cannot be deleted'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'fund NAV event revisions are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF jsonb_typeof(NEW.provenance_json::jsonb) <> 'object'
                   OR NEW.provenance_json::jsonb = '{{}}'::jsonb THEN
                    RAISE EXCEPTION 'fund NAV event provenance must be an object'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM instrument
                    WHERE instrument_id = NEW.instrument_id
                      AND instrument_type = 'fund'
                ) THEN
                    RAISE EXCEPTION 'fund NAV event requires a fund'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.supersedes_fund_nav_event_id IS NOT NULL THEN
                    SELECT * INTO predecessor FROM fund_nav_event
                    WHERE fund_nav_event_id =
                          NEW.supersedes_fund_nav_event_id;
                    IF NOT FOUND
                       OR predecessor.instrument_id <> NEW.instrument_id
                       OR predecessor.fund_nav_action_id <>
                          NEW.fund_nav_action_id
                       OR predecessor.event_type <> NEW.event_type
                       OR NEW.revision_number <>
                          predecessor.revision_number + 1 THEN
                        RAISE EXCEPTION 'invalid fund NAV event revision chain'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.revision_kind = 'cancellation' AND NOT (
                        NEW.announcement_date IS NOT DISTINCT FROM
                            predecessor.announcement_date
                        AND NEW.record_date IS NOT DISTINCT FROM
                            predecessor.record_date
                        AND NEW.effective_date = predecessor.effective_date
                        AND NEW.payable_date IS NOT DISTINCT FROM
                            predecessor.payable_date
                        AND NEW.sequence_order IS NOT DISTINCT FROM
                            predecessor.sequence_order
                        AND NEW.cash_per_unit IS NOT DISTINCT FROM
                            predecessor.cash_per_unit
                        AND NEW.unit_ratio IS NOT DISTINCT FROM
                            predecessor.unit_ratio
                    ) THEN
                        RAISE EXCEPTION 'cancellation changed action payload'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                IF NEW.revision_kind <> 'cancellation' AND EXISTS (
                    SELECT 1 FROM fund_nav_event AS other
                    WHERE other.instrument_id = NEW.instrument_id
                      AND other.fund_nav_action_id <> NEW.fund_nav_action_id
                      AND other.effective_date = NEW.effective_date
                      AND other.revision_kind <> 'cancellation'
                      AND NOT EXISTS (
                          SELECT 1 FROM fund_nav_event AS successor
                          WHERE successor.supersedes_fund_nav_event_id =
                                other.fund_nav_event_id
                      )
                      AND (
                          NEW.sequence_order IS NULL
                          OR other.sequence_order IS NULL
                          OR other.sequence_order = NEW.sequence_order
                      )
                ) THEN
                    RAISE EXCEPTION 'same-day actions require unique sequence'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$;
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {POSTGRES_FUND_NAV_EVIDENCE_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            DECLARE predecessor fund_nav_reinvestment_evidence%ROWTYPE;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'reinvestment evidence cannot be deleted'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'reinvestment evidence is immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF jsonb_typeof(NEW.provenance_json::jsonb) <> 'object'
                   OR NEW.provenance_json::jsonb = '{{}}'::jsonb THEN
                    RAISE EXCEPTION 'reinvestment provenance must be an object'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM fund_nav_event AS event
                    JOIN instrument ON instrument.instrument_id =
                         event.instrument_id
                    WHERE event.fund_nav_event_id = NEW.fund_nav_event_id
                      AND event.instrument_id = NEW.instrument_id
                      AND event.event_type = 'cash_distribution'
                      AND instrument.instrument_type = 'fund'
                ) THEN
                    RAISE EXCEPTION 'reinvestment evidence event mismatch'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.supersedes_fund_nav_reinvestment_evidence_id
                   IS NOT NULL THEN
                    SELECT * INTO predecessor
                    FROM fund_nav_reinvestment_evidence
                    WHERE fund_nav_reinvestment_evidence_id =
                          NEW.supersedes_fund_nav_reinvestment_evidence_id;
                    IF NOT FOUND
                       OR predecessor.instrument_id <> NEW.instrument_id
                       OR predecessor.fund_nav_event_id <>
                          NEW.fund_nav_event_id
                       OR NEW.revision_number <>
                          predecessor.revision_number + 1 THEN
                        RAISE EXCEPTION 'invalid evidence revision chain'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.revision_kind = 'cancellation'
                       AND NEW.reinvestment_nav <>
                           predecessor.reinvestment_nav THEN
                        RAISE EXCEPTION 'evidence cancellation changed value'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                RETURN NEW;
            END; $$;
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {POSTGRES_FUND_NAV_RUN_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'projection runs cannot be deleted'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'projection runs are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF jsonb_typeof(NEW.evidence_json::jsonb) <> 'object'
                   OR NEW.evidence_json::jsonb = '{{}}'::jsonb THEN
                    RAISE EXCEPTION 'projection evidence must be an object'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM instrument
                    WHERE instrument_id = NEW.instrument_id
                      AND instrument_type = 'fund'
                ) OR NEW.fund_nav_projection_run_id <>
                     'fund-nav-projection-' || NEW.input_fingerprint OR (
                    NEW.projection_status = 'unavailable' AND (
                        COALESCE(jsonb_typeof(NEW.evidence_json::jsonb ->
                                     'unavailable_reason'), 'null') <> 'string'
                        OR length(trim(NEW.evidence_json::jsonb ->>
                                       'unavailable_reason')) = 0
                    )
                ) THEN
                    RAISE EXCEPTION 'invalid deterministic projection run'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$;
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {POSTGRES_FUND_NAV_RUN_EVENT_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'run membership cannot be deleted'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'run membership is immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM fund_nav_projection_run AS run
                    JOIN fund_nav_event AS event
                      ON event.fund_nav_event_id = NEW.fund_nav_event_id
                    WHERE run.fund_nav_projection_run_id =
                          NEW.fund_nav_projection_run_id
                      AND run.instrument_id = event.instrument_id
                      AND event.revision_kind <> 'cancellation'
                      AND NOT EXISTS (
                          SELECT 1 FROM fund_nav_event AS successor
                          WHERE successor.supersedes_fund_nav_event_id =
                                event.fund_nav_event_id
                      )
                ) THEN
                    RAISE EXCEPTION 'run event revision is not current'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$;
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {POSTGRES_FUND_NAV_RUN_EVIDENCE_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'run membership cannot be deleted'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'run membership is immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM fund_nav_projection_run AS run
                    JOIN fund_nav_reinvestment_evidence AS evidence
                      ON evidence.fund_nav_reinvestment_evidence_id =
                         NEW.fund_nav_reinvestment_evidence_id
                    JOIN fund_nav_projection_run_event AS run_event
                      ON run_event.fund_nav_projection_run_id =
                         run.fund_nav_projection_run_id
                     AND run_event.fund_nav_event_id =
                         evidence.fund_nav_event_id
                    WHERE run.fund_nav_projection_run_id =
                          NEW.fund_nav_projection_run_id
                      AND run.instrument_id = evidence.instrument_id
                      AND evidence.revision_kind <> 'cancellation'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM fund_nav_reinvestment_evidence AS successor
                          WHERE successor.
                                supersedes_fund_nav_reinvestment_evidence_id =
                                evidence.fund_nav_reinvestment_evidence_id
                      )
                ) THEN
                    RAISE EXCEPTION 'run evidence revision is not current'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$;
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {POSTGRES_FUND_NAV_FACTOR_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'fund NAV factors cannot be deleted'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'fund NAV factors are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF jsonb_typeof(NEW.evidence_json::jsonb) <> 'object'
                   OR NEW.evidence_json::jsonb = '{{}}'::jsonb THEN
                    RAISE EXCEPTION 'factor evidence must be an object'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM fund_nav_projection_run AS run
                    WHERE run.fund_nav_projection_run_id =
                          NEW.fund_nav_projection_run_id
                      AND run.instrument_id = NEW.instrument_id
                      AND run.projection_status IN ('complete', 'partial')
                      AND run.method_version = NEW.method_version
                      AND run.anchor_date <= NEW.anchor_date
                      AND (
                          (run.projection_kind = 'provider_explicit'
                           AND NEW.factor_kind = 'provider_implied') OR
                          (run.projection_kind = 'event_derived'
                           AND NEW.factor_kind = 'event_derived') OR
                          (run.projection_kind = 'hybrid_reanchored'
                           AND NEW.factor_kind IN
                               ('provider_implied', 'event_derived'))
                      )
                ) THEN
                    RAISE EXCEPTION 'factor projection run mismatch'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.evidence_kind = 'zero_cash_anchor' AND EXISTS (
                    SELECT 1
                    FROM fund_nav_projection_run_event AS member
                    JOIN fund_nav_event AS event
                      ON event.fund_nav_event_id = member.fund_nav_event_id
                    WHERE member.fund_nav_projection_run_id =
                          NEW.fund_nav_projection_run_id
                      AND event.effective_date <= NEW.as_of_date
                ) THEN
                    RAISE EXCEPTION 'zero-cash anchor skips a current action'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.evidence_kind = 'fund_nav_event' AND NOT EXISTS (
                    SELECT 1
                    FROM fund_nav_projection_run_event AS member
                    JOIN fund_nav_event AS event
                      ON event.fund_nav_event_id = member.fund_nav_event_id
                    JOIN fund_nav_adjustment_factor AS previous
                      ON previous.fund_nav_adjustment_factor_id =
                         NEW.previous_fund_nav_adjustment_factor_id
                    LEFT JOIN fund_nav_event AS previous_event
                      ON previous_event.fund_nav_event_id =
                         previous.fund_nav_event_id
                    LEFT JOIN fund_nav_reinvestment_evidence AS evidence
                      ON evidence.fund_nav_reinvestment_evidence_id =
                         NEW.fund_nav_reinvestment_evidence_id
                    LEFT JOIN
                         fund_nav_projection_run_reinvestment_evidence AS
                         evidence_member
                      ON evidence_member.fund_nav_projection_run_id =
                         member.fund_nav_projection_run_id
                     AND evidence_member.
                         fund_nav_reinvestment_evidence_id =
                         evidence.fund_nav_reinvestment_evidence_id
                    WHERE member.fund_nav_projection_run_id =
                          NEW.fund_nav_projection_run_id
                      AND member.fund_nav_event_id = NEW.fund_nav_event_id
                      AND event.instrument_id = NEW.instrument_id
                      AND event.effective_date = NEW.as_of_date
                      AND previous.fund_nav_projection_run_id =
                          NEW.fund_nav_projection_run_id
                      AND previous.anchor_date = NEW.anchor_date
                      AND (
                          (previous.fund_nav_event_id IS NULL
                           AND event.effective_date > previous.as_of_date)
                          OR
                          (previous.fund_nav_event_id IS NOT NULL AND (
                              event.effective_date >
                                  previous_event.effective_date
                              OR (
                                  event.effective_date =
                                      previous_event.effective_date
                                  AND COALESCE(event.sequence_order, 1) >
                                      COALESCE(
                                          previous_event.sequence_order,
                                          1
                                      )
                              )
                          ))
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM fund_nav_projection_run_event AS skipped_member
                          JOIN fund_nav_event AS skipped
                            ON skipped.fund_nav_event_id =
                               skipped_member.fund_nav_event_id
                          WHERE skipped_member.fund_nav_projection_run_id =
                                NEW.fund_nav_projection_run_id
                            AND (
                                (previous.fund_nav_event_id IS NULL
                                 AND skipped.effective_date >
                                     previous.as_of_date)
                                OR
                                (previous.fund_nav_event_id IS NOT NULL AND (
                                    skipped.effective_date >
                                        previous_event.effective_date
                                    OR (
                                        skipped.effective_date =
                                            previous_event.effective_date
                                        AND COALESCE(
                                            skipped.sequence_order,
                                            1
                                        ) > COALESCE(
                                            previous_event.sequence_order,
                                            1
                                        )
                                    )
                                ))
                            )
                            AND (
                                skipped.effective_date < event.effective_date
                                OR (
                                    skipped.effective_date =
                                        event.effective_date
                                    AND COALESCE(skipped.sequence_order, 1) <
                                        COALESCE(event.sequence_order, 1)
                                )
                            )
                      )
                      AND (
                          (event.event_type = 'cash_distribution'
                           AND evidence_member.
                               fund_nav_reinvestment_evidence_id IS NOT NULL
                           AND evidence.fund_nav_event_id =
                               event.fund_nav_event_id
                           AND NEW.factor_level = ROUND(
                               previous.factor_level *
                               (1 + event.cash_per_unit /
                                    evidence.reinvestment_nav), 18
                           )) OR
                          (event.event_type = 'unit_split'
                           AND NEW.fund_nav_reinvestment_evidence_id IS NULL
                           AND NEW.factor_level = ROUND(
                               previous.factor_level * event.unit_ratio, 18
                           ))
                      )
                ) THEN
                    RAISE EXCEPTION 'event factor lineage is not canonical'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$;
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {POSTGRES_FUND_NAV_CURRENT_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'current projection pointer cannot be deleted'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM fund_nav_projection_run
                    WHERE fund_nav_projection_run_id =
                          NEW.fund_nav_projection_run_id
                      AND instrument_id = NEW.instrument_id
                ) OR EXISTS (
                    SELECT 1
                    FROM fund_nav_projection_run AS run
                    WHERE run.fund_nav_projection_run_id =
                          NEW.fund_nav_projection_run_id
                      AND run.projection_status IN ('complete', 'partial')
                      AND (
                          run.anchor_date IS DISTINCT FROM (
                              SELECT MIN(factor.anchor_date)
                              FROM fund_nav_adjustment_factor AS factor
                              WHERE factor.fund_nav_projection_run_id =
                                    run.fund_nav_projection_run_id
                          ) OR
                          (
                              run.projection_kind = 'provider_explicit'
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM fund_nav_adjustment_factor AS factor
                                  WHERE factor.fund_nav_projection_run_id =
                                        run.fund_nav_projection_run_id
                                    AND factor.factor_kind = 'provider_implied'
                              )
                          ) OR (
                              run.projection_kind = 'event_derived'
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM fund_nav_adjustment_factor AS anchor
                                  WHERE anchor.fund_nav_projection_run_id =
                                        run.fund_nav_projection_run_id
                                    AND anchor.evidence_kind IN (
                                        'zero_cash_anchor',
                                        'window_normalized_anchor'
                                    )
                              )
                          ) OR (
                              run.projection_kind = 'hybrid_reanchored'
                              AND (
                                  NOT EXISTS (
                                      SELECT 1
                                      FROM fund_nav_adjustment_factor AS factor
                                      WHERE factor.fund_nav_projection_run_id =
                                            run.fund_nav_projection_run_id
                                        AND factor.factor_kind =
                                            'provider_implied'
                                  ) OR NOT EXISTS (
                                      SELECT 1
                                      FROM fund_nav_adjustment_factor AS factor
                                      WHERE factor.fund_nav_projection_run_id =
                                            run.fund_nav_projection_run_id
                                        AND factor.factor_kind = 'event_derived'
                                  )
                              )
                          )
                      )
                ) OR EXISTS (
                    SELECT 1 FROM fund_nav_event AS event
                    WHERE event.instrument_id = NEW.instrument_id
                      AND event.revision_kind <> 'cancellation'
                      AND NOT EXISTS (
                          SELECT 1 FROM fund_nav_event AS successor
                          WHERE successor.supersedes_fund_nav_event_id =
                                event.fund_nav_event_id
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM fund_nav_projection_run_event AS member
                          WHERE member.fund_nav_projection_run_id =
                                NEW.fund_nav_projection_run_id
                            AND member.fund_nav_event_id = event.fund_nav_event_id
                      )
                ) OR EXISTS (
                    SELECT 1 FROM fund_nav_reinvestment_evidence AS evidence
                    JOIN fund_nav_projection_run_event AS event_member
                      ON event_member.fund_nav_projection_run_id =
                         NEW.fund_nav_projection_run_id
                     AND event_member.fund_nav_event_id =
                         evidence.fund_nav_event_id
                    WHERE evidence.instrument_id = NEW.instrument_id
                      AND evidence.revision_kind <> 'cancellation'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM fund_nav_reinvestment_evidence AS successor
                          WHERE successor.
                                supersedes_fund_nav_reinvestment_evidence_id =
                                evidence.fund_nav_reinvestment_evidence_id
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM fund_nav_projection_run_reinvestment_evidence AS member
                          WHERE member.fund_nav_projection_run_id =
                                NEW.fund_nav_projection_run_id
                            AND member.fund_nav_reinvestment_evidence_id =
                                evidence.fund_nav_reinvestment_evidence_id
                      )
                ) THEN
                    RAISE EXCEPTION 'current projection instrument mismatch'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$;
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {POSTGRES_FUND_NAV_MARKET_DATA_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    IF OLD.metric_family = 'nav'
                       AND OLD.quote_basis = 'official_nav'
                       AND EXISTS (
                           SELECT 1 FROM instrument_market_data AS total
                           WHERE total.instrument_id = OLD.instrument_id
                             AND total.metric_family = 'nav'
                             AND total.quote_basis = 'total_return_nav'
                             AND total.as_of_date = OLD.as_of_date
                             AND total.currency = OLD.currency
                             AND total.status = 'complete'
                       ) THEN
                        RAISE EXCEPTION 'official NAV is referenced'
                            USING ERRCODE = '23503';
                    END IF;
                    RETURN OLD;
                END IF;
                IF TG_OP = 'UPDATE'
                   AND OLD.metric_family = 'nav'
                   AND OLD.quote_basis = 'official_nav'
                   AND NEW IS DISTINCT FROM OLD
                   AND EXISTS (
                       SELECT 1 FROM instrument_market_data AS total
                       WHERE total.instrument_id = OLD.instrument_id
                         AND total.metric_family = 'nav'
                         AND total.quote_basis = 'total_return_nav'
                         AND total.as_of_date = OLD.as_of_date
                         AND total.currency = OLD.currency
                         AND total.status = 'complete'
                   ) THEN
                    RAISE EXCEPTION 'referenced official NAV is immutable'
                        USING ERRCODE = '23503';
                END IF;
                IF NEW.metric_family = 'nav'
                   AND NEW.quote_basis = 'total_return_nav'
                   AND NEW.status = 'complete'
                   AND NOT EXISTS (
                       SELECT 1
                       FROM fund_nav_current_projection AS current
                       JOIN fund_nav_adjustment_factor AS factor
                         ON factor.fund_nav_projection_run_id =
                            current.fund_nav_projection_run_id
                       JOIN instrument_market_data AS unit_nav
                         ON unit_nav.instrument_id = NEW.instrument_id
                        AND unit_nav.metric_family = 'nav'
                        AND unit_nav.quote_basis = 'official_nav'
                        AND unit_nav.as_of_date = NEW.as_of_date
                        AND unit_nav.currency = NEW.currency
                        AND unit_nav.status = 'complete'
                       WHERE current.instrument_id = NEW.instrument_id
                         AND factor.fund_nav_adjustment_factor_id =
                             NEW.fund_nav_adjustment_factor_id
                         AND NEW.nav_lineage_evidence_json->>'factor_record_id' =
                             factor.fund_nav_adjustment_factor_id
                         AND NEW.nav_lineage_evidence_json->>'factor_logical_key' =
                             factor.factor_logical_key
                         AND CAST(
                             NEW.nav_lineage_evidence_json->>'factor_level'
                             AS NUMERIC
                         ) = factor.factor_level
                         AND CAST(NEW.value AS NUMERIC) = ROUND(
                             CAST(unit_nav.value AS NUMERIC) *
                             factor.factor_level, 16
                         )
                         AND (
                             (NEW.nav_lineage_kind = 'provider_explicit'
                              AND factor.factor_kind = 'provider_implied'
                              AND factor.as_of_date = NEW.as_of_date) OR
                             (NEW.nav_lineage_kind =
                                  'derived_dividend_reinvestment'
                              AND factor.as_of_date <= NEW.as_of_date
                              AND factor.method_version =
                                  NEW.nav_derivation_method_version
                              AND factor.anchor_date =
                                  NEW.nav_derivation_anchor_date
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM fund_nav_adjustment_factor AS successor
                                  WHERE successor.
                                        previous_fund_nav_adjustment_factor_id =
                                        factor.fund_nav_adjustment_factor_id
                                    AND successor.as_of_date <= NEW.as_of_date
                              )
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM fund_nav_projection_run_event AS member
                                  JOIN fund_nav_event AS event
                                    ON event.fund_nav_event_id =
                                       member.fund_nav_event_id
                                  WHERE member.fund_nav_projection_run_id =
                                        current.fund_nav_projection_run_id
                                    AND event.effective_date >
                                        factor.as_of_date
                                    AND event.effective_date <= NEW.as_of_date
                              ))
                         )
                   ) THEN
                    RAISE EXCEPTION 'total-return NAV factor is not canonical'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$;
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {POSTGRES_FUND_NAV_INSTRUMENT_FUNCTION}()
            RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            BEGIN
                IF NEW.instrument_type <> 'fund' AND (
                    EXISTS (
                        SELECT 1 FROM fund_nav_event
                        WHERE instrument_id = OLD.instrument_id
                    ) OR EXISTS (
                        SELECT 1 FROM fund_nav_projection_run
                        WHERE instrument_id = OLD.instrument_id
                    )
                ) THEN
                    RAISE EXCEPTION 'fund NAV ledger requires a fund'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$;
            """
        )
    )
    for trigger_name, table_name, function_name in (
        (FUND_NAV_EVENT_TRIGGER_NAME, "fund_nav_event", POSTGRES_FUND_NAV_EVENT_FUNCTION),
        (
            FUND_NAV_EVIDENCE_TRIGGER_NAME,
            "fund_nav_reinvestment_evidence",
            POSTGRES_FUND_NAV_EVIDENCE_FUNCTION,
        ),
        (FUND_NAV_RUN_TRIGGER_NAME, "fund_nav_projection_run", POSTGRES_FUND_NAV_RUN_FUNCTION),
        (
            FUND_NAV_RUN_EVENT_TRIGGER_NAME,
            "fund_nav_projection_run_event",
            POSTGRES_FUND_NAV_RUN_EVENT_FUNCTION,
        ),
        (
            FUND_NAV_RUN_EVIDENCE_TRIGGER_NAME,
            "fund_nav_projection_run_reinvestment_evidence",
            POSTGRES_FUND_NAV_RUN_EVIDENCE_FUNCTION,
        ),
        (
            FUND_NAV_FACTOR_TRIGGER_NAME,
            "fund_nav_adjustment_factor",
            POSTGRES_FUND_NAV_FACTOR_FUNCTION,
        ),
        (
            FUND_NAV_CURRENT_TRIGGER_NAME,
            "fund_nav_current_projection",
            POSTGRES_FUND_NAV_CURRENT_FUNCTION,
        ),
    ):
        connection.execute(
            sa.text(
                f"CREATE TRIGGER {trigger_name} BEFORE INSERT OR UPDATE OR DELETE "
                f"ON {table_name} FOR EACH ROW EXECUTE FUNCTION "
                f"{function_name}()"
            )
        )
    connection.execute(
        sa.text(
            f"CREATE TRIGGER {FUND_NAV_MARKET_DATA_TRIGGER_NAME} "
            "BEFORE INSERT OR UPDATE OR DELETE ON instrument_market_data "
            "FOR EACH ROW EXECUTE FUNCTION "
            f"{POSTGRES_FUND_NAV_MARKET_DATA_FUNCTION}()"
        )
    )
    connection.execute(
        sa.text(
            f"CREATE TRIGGER {FUND_NAV_INSTRUMENT_TRIGGER_NAME} "
            "BEFORE UPDATE OF instrument_type ON instrument "
            "FOR EACH ROW EXECUTE FUNCTION "
            f"{POSTGRES_FUND_NAV_INSTRUMENT_FUNCTION}()"
        )
    )


def _create_sqlite_fund_nav_ledger_triggers(
    connection: sa.Connection,
) -> None:
    cash_decimal_is_positive = _sqlite_exact_fund_nav_decimal_sql(
        "NEW.cash_per_unit"
    )
    ratio_decimal_is_positive = _sqlite_exact_fund_nav_decimal_sql(
        "NEW.unit_ratio"
    )
    reinvestment_decimal_is_positive = _sqlite_exact_fund_nav_decimal_sql(
        "NEW.reinvestment_nav"
    )
    factor_decimal_is_positive = _sqlite_exact_fund_nav_decimal_sql(
        "NEW.factor_level"
    )
    event_violation = f"""
        NOT EXISTS (
            SELECT 1 FROM instrument
            WHERE instrument_id = NEW.instrument_id
              AND instrument_type = 'fund'
        )
        OR json_type(NEW.provenance_json) <> 'object'
        OR NOT EXISTS (SELECT 1 FROM json_each(NEW.provenance_json))
        OR (
            NEW.event_type = 'cash_distribution'
            AND NOT ({cash_decimal_is_positive})
        )
        OR (
            NEW.event_type = 'unit_split'
            AND NOT ({ratio_decimal_is_positive})
        )
        OR (
            NEW.supersedes_fund_nav_event_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM fund_nav_event AS predecessor
                WHERE predecessor.fund_nav_event_id =
                      NEW.supersedes_fund_nav_event_id
                  AND predecessor.instrument_id = NEW.instrument_id
                  AND predecessor.fund_nav_action_id = NEW.fund_nav_action_id
                  AND predecessor.event_type = NEW.event_type
                  AND NEW.revision_number = predecessor.revision_number + 1
                  AND (
                      NEW.revision_kind <> 'cancellation'
                      OR (
                          NEW.announcement_date IS predecessor.announcement_date
                          AND NEW.record_date IS predecessor.record_date
                          AND NEW.effective_date = predecessor.effective_date
                          AND NEW.payable_date IS predecessor.payable_date
                          AND NEW.sequence_order IS predecessor.sequence_order
                          AND NEW.cash_per_unit IS predecessor.cash_per_unit
                          AND NEW.unit_ratio IS predecessor.unit_ratio
                      )
                  )
            )
        )
        OR (
            NEW.revision_kind <> 'cancellation'
            AND EXISTS (
                SELECT 1 FROM fund_nav_event AS other
                WHERE other.instrument_id = NEW.instrument_id
                  AND other.fund_nav_action_id <> NEW.fund_nav_action_id
                  AND other.effective_date = NEW.effective_date
                  AND other.revision_kind <> 'cancellation'
                  AND NOT EXISTS (
                      SELECT 1 FROM fund_nav_event AS successor
                      WHERE successor.supersedes_fund_nav_event_id =
                            other.fund_nav_event_id
                  )
                  AND (
                      NEW.sequence_order IS NULL
                      OR other.sequence_order IS NULL
                      OR other.sequence_order = NEW.sequence_order
                  )
            )
        )
    """
    evidence_violation = f"""
        NOT EXISTS (
            SELECT 1 FROM fund_nav_event AS event
            JOIN instrument
              ON instrument.instrument_id = event.instrument_id
            WHERE event.fund_nav_event_id = NEW.fund_nav_event_id
              AND event.instrument_id = NEW.instrument_id
              AND event.event_type = 'cash_distribution'
              AND instrument.instrument_type = 'fund'
        )
        OR json_type(NEW.provenance_json) <> 'object'
        OR NOT EXISTS (SELECT 1 FROM json_each(NEW.provenance_json))
        OR NOT ({reinvestment_decimal_is_positive})
        OR (
            NEW.supersedes_fund_nav_reinvestment_evidence_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM fund_nav_reinvestment_evidence AS predecessor
                WHERE predecessor.fund_nav_reinvestment_evidence_id =
                      NEW.supersedes_fund_nav_reinvestment_evidence_id
                  AND predecessor.instrument_id = NEW.instrument_id
                  AND predecessor.fund_nav_event_id = NEW.fund_nav_event_id
                  AND NEW.revision_number = predecessor.revision_number + 1
                  AND (
                      NEW.revision_kind <> 'cancellation'
                      OR NEW.reinvestment_nav = predecessor.reinvestment_nav
                  )
            )
        )
    """
    run_violation = """
        NEW.fund_nav_projection_run_id <>
            'fund-nav-projection-' || NEW.input_fingerprint
        OR NOT EXISTS (
            SELECT 1 FROM instrument
            WHERE instrument_id = NEW.instrument_id
              AND instrument_type = 'fund'
        )
        OR json_type(NEW.evidence_json) <> 'object'
        OR NOT EXISTS (SELECT 1 FROM json_each(NEW.evidence_json))
        OR (
            NEW.projection_status = 'unavailable'
            AND (
                typeof(json_extract(
                    NEW.evidence_json,
                    '$.unavailable_reason'
                )) <> 'text'
                OR length(trim(json_extract(
                    NEW.evidence_json,
                    '$.unavailable_reason'
                ))) = 0
            )
        )
    """
    run_event_violation = """
        NOT EXISTS (
            SELECT 1 FROM fund_nav_projection_run AS run
            JOIN fund_nav_event AS event
              ON event.fund_nav_event_id = NEW.fund_nav_event_id
            WHERE run.fund_nav_projection_run_id =
                  NEW.fund_nav_projection_run_id
              AND run.instrument_id = event.instrument_id
              AND event.revision_kind <> 'cancellation'
              AND NOT EXISTS (
                  SELECT 1 FROM fund_nav_event AS successor
                  WHERE successor.supersedes_fund_nav_event_id =
                        event.fund_nav_event_id
              )
        )
    """
    run_evidence_violation = """
        NOT EXISTS (
            SELECT 1 FROM fund_nav_projection_run AS run
            JOIN fund_nav_reinvestment_evidence AS evidence
              ON evidence.fund_nav_reinvestment_evidence_id =
                 NEW.fund_nav_reinvestment_evidence_id
            JOIN fund_nav_projection_run_event AS run_event
              ON run_event.fund_nav_projection_run_id =
                 run.fund_nav_projection_run_id
             AND run_event.fund_nav_event_id = evidence.fund_nav_event_id
            WHERE run.fund_nav_projection_run_id =
                  NEW.fund_nav_projection_run_id
              AND run.instrument_id = evidence.instrument_id
              AND evidence.revision_kind <> 'cancellation'
              AND NOT EXISTS (
                  SELECT 1
                  FROM fund_nav_reinvestment_evidence AS successor
                  WHERE successor.supersedes_fund_nav_reinvestment_evidence_id =
                        evidence.fund_nav_reinvestment_evidence_id
              )
        )
    """
    factor_violation = f"""
        NOT EXISTS (
            SELECT 1 FROM fund_nav_projection_run AS run
            WHERE run.fund_nav_projection_run_id =
                  NEW.fund_nav_projection_run_id
              AND run.instrument_id = NEW.instrument_id
              AND run.projection_status IN ('complete', 'partial')
              AND run.method_version = NEW.method_version
              AND run.anchor_date <= NEW.anchor_date
              AND (
                  (run.projection_kind = 'provider_explicit'
                   AND NEW.factor_kind = 'provider_implied')
                  OR
                  (run.projection_kind = 'event_derived'
                   AND NEW.factor_kind = 'event_derived')
                  OR
                  (run.projection_kind = 'hybrid_reanchored'
                   AND NEW.factor_kind IN
                       ('provider_implied', 'event_derived'))
              )
        )
        OR json_type(NEW.evidence_json) <> 'object'
        OR NOT EXISTS (SELECT 1 FROM json_each(NEW.evidence_json))
        OR NOT ({factor_decimal_is_positive})
        OR (
            NEW.evidence_kind = 'zero_cash_anchor'
            AND EXISTS (
                SELECT 1
                FROM fund_nav_projection_run_event AS member
                JOIN fund_nav_event AS event
                  ON event.fund_nav_event_id = member.fund_nav_event_id
                WHERE member.fund_nav_projection_run_id =
                      NEW.fund_nav_projection_run_id
                  AND event.effective_date <= NEW.as_of_date
            )
        )
        OR (
            NEW.evidence_kind = 'fund_nav_event'
            AND NOT EXISTS (
                SELECT 1
                FROM fund_nav_projection_run_event AS member
                JOIN fund_nav_event AS event
                  ON event.fund_nav_event_id = member.fund_nav_event_id
                JOIN fund_nav_adjustment_factor AS previous
                  ON previous.fund_nav_adjustment_factor_id =
                     NEW.previous_fund_nav_adjustment_factor_id
                LEFT JOIN fund_nav_event AS previous_event
                  ON previous_event.fund_nav_event_id =
                     previous.fund_nav_event_id
                LEFT JOIN fund_nav_reinvestment_evidence AS evidence
                  ON evidence.fund_nav_reinvestment_evidence_id =
                     NEW.fund_nav_reinvestment_evidence_id
                LEFT JOIN
                     fund_nav_projection_run_reinvestment_evidence AS
                     evidence_member
                  ON evidence_member.fund_nav_projection_run_id =
                     member.fund_nav_projection_run_id
                 AND evidence_member.fund_nav_reinvestment_evidence_id =
                     evidence.fund_nav_reinvestment_evidence_id
                WHERE member.fund_nav_projection_run_id =
                      NEW.fund_nav_projection_run_id
                  AND member.fund_nav_event_id = NEW.fund_nav_event_id
                  AND event.instrument_id = NEW.instrument_id
                  AND event.effective_date = NEW.as_of_date
                  AND previous.fund_nav_projection_run_id =
                      NEW.fund_nav_projection_run_id
                  AND previous.anchor_date = NEW.anchor_date
                  AND (
                      (previous.fund_nav_event_id IS NULL
                       AND event.effective_date > previous.as_of_date)
                      OR
                      (previous.fund_nav_event_id IS NOT NULL AND (
                          event.effective_date > previous_event.effective_date
                          OR (
                              event.effective_date = previous_event.effective_date
                              AND COALESCE(event.sequence_order, 1) >
                                  COALESCE(previous_event.sequence_order, 1)
                          )
                      ))
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM fund_nav_projection_run_event AS skipped_member
                      JOIN fund_nav_event AS skipped
                        ON skipped.fund_nav_event_id =
                           skipped_member.fund_nav_event_id
                      WHERE skipped_member.fund_nav_projection_run_id =
                            NEW.fund_nav_projection_run_id
                        AND (
                            (previous.fund_nav_event_id IS NULL
                             AND skipped.effective_date > previous.as_of_date)
                            OR
                            (previous.fund_nav_event_id IS NOT NULL AND (
                                skipped.effective_date >
                                    previous_event.effective_date
                                OR (
                                    skipped.effective_date =
                                        previous_event.effective_date
                                    AND COALESCE(skipped.sequence_order, 1) >
                                        COALESCE(
                                            previous_event.sequence_order,
                                            1
                                        )
                                )
                            ))
                        )
                        AND (
                            skipped.effective_date < event.effective_date
                            OR (
                                skipped.effective_date = event.effective_date
                                AND COALESCE(skipped.sequence_order, 1) <
                                    COALESCE(event.sequence_order, 1)
                            )
                        )
                  )
                  AND (
                      (
                          event.event_type = 'cash_distribution'
                          AND evidence_member.
                              fund_nav_reinvestment_evidence_id IS NOT NULL
                          AND evidence.fund_nav_event_id = event.fund_nav_event_id
                          AND abs(
                              CAST(NEW.factor_level AS REAL) -
                              CAST(previous.factor_level AS REAL) *
                              (1 + CAST(event.cash_per_unit AS REAL) /
                                  CAST(evidence.reinvestment_nav AS REAL))
                          ) <= 0.000000000000005
                      )
                      OR
                      (
                          event.event_type = 'unit_split'
                          AND NEW.fund_nav_reinvestment_evidence_id IS NULL
                          AND abs(
                              CAST(NEW.factor_level AS REAL) -
                              CAST(previous.factor_level AS REAL) *
                              CAST(event.unit_ratio AS REAL)
                          ) <= 0.000000000000005
                      )
                  )
            )
        )
    """
    current_violation = """
        NOT EXISTS (
            SELECT 1 FROM fund_nav_projection_run
            WHERE fund_nav_projection_run_id =
                  NEW.fund_nav_projection_run_id
              AND instrument_id = NEW.instrument_id
        )
        OR EXISTS (
            SELECT 1
            FROM fund_nav_projection_run AS run
            WHERE run.fund_nav_projection_run_id =
                  NEW.fund_nav_projection_run_id
              AND run.projection_status IN ('complete', 'partial')
              AND (
                  run.anchor_date IS NOT (
                      SELECT MIN(factor.anchor_date)
                      FROM fund_nav_adjustment_factor AS factor
                      WHERE factor.fund_nav_projection_run_id =
                            run.fund_nav_projection_run_id
                  ) OR
                  (
                      run.projection_kind = 'provider_explicit'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM fund_nav_adjustment_factor AS factor
                          WHERE factor.fund_nav_projection_run_id =
                                run.fund_nav_projection_run_id
                            AND factor.factor_kind = 'provider_implied'
                      )
                  ) OR (
                      run.projection_kind = 'event_derived'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM fund_nav_adjustment_factor AS anchor
                          WHERE anchor.fund_nav_projection_run_id =
                                run.fund_nav_projection_run_id
                            AND anchor.evidence_kind IN (
                                'zero_cash_anchor',
                                'window_normalized_anchor'
                            )
                      )
                  ) OR (
                      run.projection_kind = 'hybrid_reanchored'
                      AND (
                          NOT EXISTS (
                              SELECT 1
                              FROM fund_nav_adjustment_factor AS factor
                              WHERE factor.fund_nav_projection_run_id =
                                    run.fund_nav_projection_run_id
                                AND factor.factor_kind = 'provider_implied'
                          ) OR NOT EXISTS (
                              SELECT 1
                              FROM fund_nav_adjustment_factor AS factor
                              WHERE factor.fund_nav_projection_run_id =
                                    run.fund_nav_projection_run_id
                                AND factor.factor_kind = 'event_derived'
                          )
                      )
                  )
              )
        )
        OR EXISTS (
            SELECT 1 FROM fund_nav_event AS event
            WHERE event.instrument_id = NEW.instrument_id
              AND event.revision_kind <> 'cancellation'
              AND NOT EXISTS (
                  SELECT 1 FROM fund_nav_event AS successor
                  WHERE successor.supersedes_fund_nav_event_id =
                        event.fund_nav_event_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM fund_nav_projection_run_event AS member
                  WHERE member.fund_nav_projection_run_id =
                        NEW.fund_nav_projection_run_id
                    AND member.fund_nav_event_id = event.fund_nav_event_id
              )
        )
        OR EXISTS (
            SELECT 1 FROM fund_nav_reinvestment_evidence AS evidence
            JOIN fund_nav_projection_run_event AS event_member
              ON event_member.fund_nav_projection_run_id =
                 NEW.fund_nav_projection_run_id
             AND event_member.fund_nav_event_id = evidence.fund_nav_event_id
            WHERE evidence.instrument_id = NEW.instrument_id
              AND evidence.revision_kind <> 'cancellation'
              AND NOT EXISTS (
                  SELECT 1
                  FROM fund_nav_reinvestment_evidence AS successor
                  WHERE successor.supersedes_fund_nav_reinvestment_evidence_id =
                        evidence.fund_nav_reinvestment_evidence_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM fund_nav_projection_run_reinvestment_evidence AS member
                  WHERE member.fund_nav_projection_run_id =
                        NEW.fund_nav_projection_run_id
                    AND member.fund_nav_reinvestment_evidence_id =
                        evidence.fund_nav_reinvestment_evidence_id
              )
        )
    """
    guards = (
        (FUND_NAV_EVENT_TRIGGER_NAME, "fund_nav_event", event_violation),
        (
            FUND_NAV_EVIDENCE_TRIGGER_NAME,
            "fund_nav_reinvestment_evidence",
            evidence_violation,
        ),
        (FUND_NAV_RUN_TRIGGER_NAME, "fund_nav_projection_run", run_violation),
        (
            FUND_NAV_RUN_EVENT_TRIGGER_NAME,
            "fund_nav_projection_run_event",
            run_event_violation,
        ),
        (
            FUND_NAV_RUN_EVIDENCE_TRIGGER_NAME,
            "fund_nav_projection_run_reinvestment_evidence",
            run_evidence_violation,
        ),
        (
            FUND_NAV_FACTOR_TRIGGER_NAME,
            "fund_nav_adjustment_factor",
            factor_violation,
        ),
        (
            FUND_NAV_CURRENT_TRIGGER_NAME,
            "fund_nav_current_projection",
            current_violation,
        ),
    )
    for name, table, violation in guards:
        _sqlite_guard(
            connection,
            name=name,
            table=table,
            operation="INSERT",
            violation=violation,
            message="immutable fund NAV projection contract violation",
        )
        _sqlite_guard(
            connection,
            name=name,
            table=table,
            operation="DELETE",
            violation="1",
            message="append-only fund NAV projection records cannot be deleted",
        )
        _sqlite_guard(
            connection,
            name=name,
            table=table,
            operation="UPDATE",
            violation=current_violation if name == FUND_NAV_CURRENT_TRIGGER_NAME else "1",
            message="immutable fund NAV projection contract violation",
        )

    total_violation = """
        NEW.metric_family = 'nav'
        AND NEW.quote_basis = 'total_return_nav'
        AND NEW.status = 'complete'
        AND NOT EXISTS (
            SELECT 1
            FROM fund_nav_current_projection AS current
            JOIN fund_nav_adjustment_factor AS factor
              ON factor.fund_nav_projection_run_id =
                 current.fund_nav_projection_run_id
            JOIN instrument_market_data AS unit_nav
              ON unit_nav.instrument_id = NEW.instrument_id
             AND unit_nav.metric_family = 'nav'
             AND unit_nav.quote_basis = 'official_nav'
             AND unit_nav.as_of_date = NEW.as_of_date
             AND unit_nav.currency = NEW.currency
             AND unit_nav.status = 'complete'
            WHERE current.instrument_id = NEW.instrument_id
              AND factor.fund_nav_adjustment_factor_id =
                  NEW.fund_nav_adjustment_factor_id
              AND json_extract(
                  NEW.nav_lineage_evidence_json,
                  '$.factor_record_id'
              ) = factor.fund_nav_adjustment_factor_id
              AND json_extract(
                  NEW.nav_lineage_evidence_json,
                  '$.factor_logical_key'
              ) = factor.factor_logical_key
              AND abs(
                  CAST(json_extract(
                      NEW.nav_lineage_evidence_json,
                      '$.factor_level'
                  ) AS REAL) - CAST(factor.factor_level AS REAL)
              ) <= 0.000000000000005
              AND ROUND(CAST(NEW.value AS REAL), 12) = ROUND(
                  CAST(unit_nav.value AS REAL) *
                  CAST(factor.factor_level AS REAL),
                  12
              )
              AND (
                  (
                      NEW.nav_lineage_kind = 'provider_explicit'
                      AND factor.factor_kind = 'provider_implied'
                      AND factor.as_of_date = NEW.as_of_date
                  )
                  OR
                  (
                      NEW.nav_lineage_kind =
                          'derived_dividend_reinvestment'
                      AND factor.as_of_date <= NEW.as_of_date
                      AND factor.method_version =
                          NEW.nav_derivation_method_version
                      AND factor.anchor_date =
                          NEW.nav_derivation_anchor_date
                      AND NOT EXISTS (
                          SELECT 1
                          FROM fund_nav_adjustment_factor AS successor
                          WHERE successor.
                                previous_fund_nav_adjustment_factor_id =
                                factor.fund_nav_adjustment_factor_id
                            AND successor.as_of_date <= NEW.as_of_date
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM fund_nav_projection_run_event AS member
                          JOIN fund_nav_event AS event
                            ON event.fund_nav_event_id =
                               member.fund_nav_event_id
                          WHERE member.fund_nav_projection_run_id =
                                current.fund_nav_projection_run_id
                            AND event.effective_date > factor.as_of_date
                            AND event.effective_date <= NEW.as_of_date
                      )
                  )
              )
        )
    """
    for operation in ("INSERT", "UPDATE"):
        _sqlite_guard(
            connection,
            name=FUND_NAV_MARKET_DATA_TRIGGER_NAME,
            table="instrument_market_data",
            operation=operation,
            violation=total_violation,
            message="total-return NAV factor is not canonical",
        )
    for operation in ("UPDATE", "DELETE"):
        connection.execute(
            sa.text(
                f"""
                CREATE TRIGGER {FUND_NAV_MARKET_DATA_TRIGGER_NAME}_official_{operation.lower()}
                BEFORE {operation} ON instrument_market_data
                FOR EACH ROW
                WHEN OLD.metric_family = 'nav'
                 AND OLD.quote_basis = 'official_nav'
                 AND EXISTS (
                     SELECT 1 FROM instrument_market_data AS total
                     WHERE total.instrument_id = OLD.instrument_id
                       AND total.metric_family = 'nav'
                       AND total.quote_basis = 'total_return_nav'
                       AND total.as_of_date = OLD.as_of_date
                       AND total.currency = OLD.currency
                       AND total.status = 'complete'
                 )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'official NAV is referenced by total-return NAV'
                    );
                END
                """
            )
        )
    connection.execute(
        sa.text(
            f"""
            CREATE TRIGGER {FUND_NAV_INSTRUMENT_TRIGGER_NAME}
            BEFORE UPDATE OF instrument_type ON instrument
            FOR EACH ROW
            WHEN NEW.instrument_type <> 'fund' AND (
                EXISTS (
                    SELECT 1 FROM fund_nav_event
                    WHERE instrument_id = OLD.instrument_id
                )
                OR EXISTS (
                    SELECT 1 FROM fund_nav_projection_run
                    WHERE instrument_id = OLD.instrument_id
                )
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'fund NAV projection ledger requires a fund'
                );
            END
            """
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE instrument_market_data, instrument IN ACCESS EXCLUSIVE MODE"
            )
        )

    _assert_legacy_nav_snapshot_is_complete(connection)
    op.add_column(
        "instrument_market_data",
        sa.Column("nav_lineage_kind", sa.String(), nullable=True),
    )
    op.add_column(
        "instrument_market_data",
        sa.Column("nav_derivation_method_version", sa.String(), nullable=True),
    )
    op.add_column(
        "instrument_market_data",
        sa.Column("nav_derivation_anchor_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "instrument_market_data",
        sa.Column(
            "nav_lineage_evidence_json",
            sa.JSON(none_as_null=True),
            nullable=True,
        ),
    )
    op.add_column(
        "instrument_market_data",
        sa.Column("fund_nav_adjustment_factor_id", sa.String(), nullable=True),
    )
    _normalize_quote_selection_policies(connection)
    _delete_noncanonical_nav_data(connection)
    _backfill_official_nav_lineage(connection)
    _drop_sqlite_contract_triggers(connection)
    _create_fund_nav_ledger_tables()
    with op.batch_alter_table("instrument_market_data") as batch_op:
        batch_op.drop_constraint(QUOTE_IDENTITY_CHECK_NAME, type_="check")
        batch_op.create_check_constraint(
            QUOTE_IDENTITY_CHECK_NAME,
            _quote_identity_sql(CANONICAL_NAV_QUOTE_BASES),
        )
        batch_op.create_check_constraint(
            NAV_LINEAGE_CHECK_NAME,
            NAV_LINEAGE_SQL,
        )
        batch_op.create_foreign_key(
            FUND_NAV_MARKET_DATA_FACTOR_FK_NAME,
            "fund_nav_adjustment_factor",
            ["fund_nav_adjustment_factor_id"],
            ["fund_nav_adjustment_factor_id"],
            ondelete="RESTRICT",
        )
    _create_fund_nav_ledger_triggers(connection)
    _create_sqlite_contract_triggers(
        connection,
        nav_quote_bases=CANONICAL_NAV_QUOTE_BASES,
    )


def downgrade() -> None:
    raise RuntimeError(
        "20260715_0012 is intentionally irreversible: canonical NAV cleanup "
        "and immutable projection history must not be destructively downgraded."
    )
