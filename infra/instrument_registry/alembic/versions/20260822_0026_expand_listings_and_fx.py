"""Add European listing identities and maintained European FX pairs.

Revision ID: 20260822_0026
Revises: 20260818_0025
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_0026"
down_revision: str | None = "20260818_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LISTED_INSTRUMENT_TYPES = ("equity", "etf")
SUPPORTED_LISTING_EXCHANGES = (
    "XNAS",
    "XNYS",
    "XASE",
    "BATS",
    "XHKG",
    "XSHG",
    "XSHE",
    "XLON",
    "XETR",
    "XPAR",
    "XAMS",
    "XMIL",
    "XSWX",
)
EXCHANGE_BY_SUFFIX = {
    ".SH": "XSHG",
    ".SS": "XSHG",
    ".SZ": "XSHE",
    ".HK": "XHKG",
    ".L": "XLON",
    ".DE": "XETR",
    ".PA": "XPAR",
    ".AS": "XAMS",
    ".MI": "XMIL",
    ".SW": "XSWX",
    "-SH": "XSHG",
    "-SZ": "XSHE",
    "-HK": "XHKG",
}
FX_IDENTITIES = (
    ("fx-usd-hkd", "USD", "HKD"),
    ("fx-usd-cny", "USD", "CNY"),
    ("fx-usd-eur", "USD", "EUR"),
    ("fx-usd-gbp", "USD", "GBP"),
    ("fx-usd-chf", "USD", "CHF"),
)
NEW_FX_IDENTITIES = FX_IDENTITIES[2:]
PRICE_QUOTE_BASES = ("last", "close", "adjusted_close", "par")
NAV_QUOTE_BASES = ("official_nav", "total_return_nav")
DATA_STATUSES = ("complete", "partial", "unavailable")
MARKET_DATA_TRIGGER_NAME = "trg_instrument_market_data_price_contract"
INSTRUMENT_TRIGGER_NAME = "trg_instrument_type_price_contract"
POSTGRES_MARKET_DATA_FUNCTION = "enforce_instrument_market_data_price_contract"
POSTGRES_INSTRUMENT_FUNCTION = "enforce_instrument_type_price_contract"
ADVISORY_LOCK_SEED = 202607150010


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _watermark() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _sqlite_trigger_definitions(connection: sa.Connection) -> list[tuple[str, str]]:
    if connection.dialect.name != "sqlite":
        return []
    return [
        (str(row.name), str(row.sql))
        for row in connection.execute(
            sa.text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND sql IS NOT NULL ORDER BY name"
            )
        )
    ]


def _drop_sqlite_triggers(
    connection: sa.Connection,
    definitions: list[tuple[str, str]],
) -> None:
    for name, _sql in definitions:
        quoted_name = '"' + name.replace('"', '""') + '"'
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {quoted_name}")


def _restore_noncontract_sqlite_triggers(
    connection: sa.Connection,
    definitions: list[tuple[str, str]],
) -> None:
    contract_names = {
        f"{MARKET_DATA_TRIGGER_NAME}_insert",
        f"{MARKET_DATA_TRIGGER_NAME}_update",
        f"{INSTRUMENT_TRIGGER_NAME}_insert",
        INSTRUMENT_TRIGGER_NAME,
    }
    for name, sql in definitions:
        if name not in contract_names:
            connection.exec_driver_sql(sql)


def _drop_contract_triggers(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS {MARKET_DATA_TRIGGER_NAME} "
                "ON instrument_market_data"
            )
        )
        connection.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS {INSTRUMENT_TRIGGER_NAME} ON instrument"
            )
        )


def _exchange_from_identifiers(values: Sequence[object]) -> str | None:
    for raw_value in values:
        value = str(raw_value or "").strip().upper()
        if value.startswith("FMP:"):
            value = value.removeprefix("FMP:")
        for suffix, exchange_code in EXCHANGE_BY_SUFFIX.items():
            if value.endswith(suffix):
                return exchange_code
    return None


def _migrate_listing_identities(connection: sa.Connection, changed_at: str) -> None:
    rows = list(
        connection.execute(
            sa.text(
                "SELECT instrument_id, instrument_type, exchange_code, "
                "source_settings_json FROM instrument "
                "WHERE instrument_type IN ('equity', 'etf') ORDER BY instrument_id"
            )
        ).mappings()
    )
    for row in rows:
        instrument_id = str(row["instrument_id"])
        identifier_values = list(
            connection.execute(
                sa.text(
                    "SELECT identifier_value FROM instrument_identifier "
                    "WHERE instrument_id = :instrument_id "
                    "ORDER BY is_primary DESC, instrument_identifier_id"
                ),
                {"instrument_id": instrument_id},
            ).scalars()
        )
        settings = _json_object(row["source_settings_json"])
        derived_exchange = _exchange_from_identifiers([*identifier_values, instrument_id])
        current_exchange = str(row["exchange_code"] or "").strip().upper()
        settings_exchange = str(settings.get("market_calendar") or "").strip().upper()
        exchange_code = derived_exchange or current_exchange or settings_exchange
        if exchange_code not in SUPPORTED_LISTING_EXCHANGES:
            raise RuntimeError(
                f"Listed instrument {instrument_id} has no supported exchange identity."
            )
        settings["market_calendar"] = exchange_code
        connection.execute(
            sa.text(
                "UPDATE instrument SET exchange_code = :exchange_code, "
                "source_settings_json = :settings, "
                "calculation_inputs_updated_at = :changed_at "
                "WHERE instrument_id = :instrument_id"
            ),
            {
                "instrument_id": instrument_id,
                "exchange_code": exchange_code,
                "settings": json.dumps(settings, ensure_ascii=False),
                "changed_at": changed_at,
            },
        )


def _insert_new_fx_instruments(connection: sa.Connection, changed_at: str) -> None:
    instrument = sa.table(
        "instrument",
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_name", sa.String()),
        sa.column("instrument_type", sa.String()),
        sa.column("currency", sa.String()),
        sa.column("exchange_code", sa.String()),
        sa.column("quote_selection_policy_json", sa.JSON()),
        sa.column("source_settings_json", sa.JSON()),
        sa.column("refresh_status_json", sa.JSON()),
        sa.column("lifecycle_state_json", sa.JSON()),
        sa.column("market_data_updated_at", sa.String()),
        sa.column("calculation_inputs_updated_at", sa.String()),
    )
    identifier = sa.table(
        "instrument_identifier",
        sa.column("instrument_id", sa.String()),
        sa.column("identifier_type", sa.String()),
        sa.column("identifier_value", sa.String()),
        sa.column("is_primary", sa.Boolean()),
    )
    quote_policy = {
        role: ["spot"]
        for role in ("trading", "valuation", "total_return", "chart", "reference")
    }
    for instrument_id, base_currency, quote_currency in NEW_FX_IDENTITIES:
        if connection.execute(
            sa.text(
                "SELECT 1 FROM instrument WHERE instrument_id = :instrument_id"
            ),
            {"instrument_id": instrument_id},
        ).scalar_one_or_none() is not None:
            raise RuntimeError(f"FX instrument already exists unexpectedly: {instrument_id}")
        symbol = base_currency + quote_currency
        conflicts = list(
            connection.execute(
                sa.text(
                    "SELECT identifier_value FROM instrument_identifier "
                    "WHERE (identifier_type = 'internal' "
                    "AND identifier_value = :instrument_id) "
                    "OR (identifier_type = 'ticker' AND identifier_value = :symbol)"
                ),
                {"instrument_id": instrument_id, "symbol": symbol},
            ).scalars()
        )
        if conflicts:
            raise RuntimeError(
                f"FX identifiers for {instrument_id} already belong to another instrument."
            )
        connection.execute(
            sa.insert(instrument).values(
                instrument_id=instrument_id,
                instrument_name=f"{base_currency}/{quote_currency} Spot",
                instrument_type="fx",
                currency=quote_currency,
                exchange_code=None,
                quote_selection_policy_json=quote_policy,
                source_settings_json={
                    "source_mode": "api",
                    "source_email": "",
                    "source_location": "FMP API",
                    "source_api_profile": "fmp",
                    "source_email_rules": [],
                    "expected_frequency": "daily",
                    "market_calendar": None,
                    "release_lag_days": 0,
                    "return_semantics": "price_return",
                },
                refresh_status_json={
                    "status": "idle",
                    "message": "Awaiting first FMP FX EOD refresh.",
                    "requested_at": None,
                    "requested_by": None,
                    "mode": "api",
                    "last_successful_requested_at": None,
                },
                lifecycle_state_json={
                    "status": "active",
                    "changed_at": changed_at,
                    "changed_by": "migration:20260822_0026",
                    "canonical_instrument_id": None,
                },
                market_data_updated_at=None,
                calculation_inputs_updated_at=changed_at,
            )
        )
        connection.execute(
            sa.insert(identifier),
            (
                {
                    "instrument_id": instrument_id,
                    "identifier_type": "internal",
                    "identifier_value": instrument_id,
                    "is_primary": True,
                },
                {
                    "instrument_id": instrument_id,
                    "identifier_type": "ticker",
                    "identifier_value": symbol,
                    "is_primary": False,
                },
            ),
        )


def _replace_exchange_constraint(connection: sa.Connection) -> None:
    recreate = "always" if connection.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("instrument", recreate=recreate) as batch_op:
        batch_op.drop_constraint("instrument_exchange_contract", type_="check")


def _add_exchange_constraint(connection: sa.Connection) -> None:
    recreate = "always" if connection.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("instrument", recreate=recreate) as batch_op:
        batch_op.create_check_constraint(
            "instrument_exchange_contract",
            "((instrument_type IN "
            f"({_quoted(LISTED_INSTRUMENT_TYPES)}) AND exchange_code IS NOT NULL "
            "AND exchange_code IN "
            f"({_quoted(SUPPORTED_LISTING_EXCHANGES)})) OR "
            f"(instrument_type NOT IN ({_quoted(LISTED_INSTRUMENT_TYPES)}) "
            "AND exchange_code IS NULL))",
        )


def _fx_market_data_predicate() -> str:
    return "\n            OR ".join(
        f"(NEW.instrument_id = '{instrument_id}' "
        f"AND resolved_instrument_currency = '{quote_currency}')"
        for instrument_id, _base_currency, quote_currency in FX_IDENTITIES
    )


def _fx_new_instrument_predicate() -> str:
    return "\n                OR ".join(
        f"(NEW.instrument_id = '{instrument_id}' AND NEW.currency = '{quote_currency}')"
        for instrument_id, _base_currency, quote_currency in FX_IDENTITIES
    )


def _fx_sqlite_point_predicate() -> str:
    return "\n                  OR ".join(
        f"(NEW.instrument_id = '{instrument_id}' "
        f"AND instrument.currency = '{quote_currency}')"
        for instrument_id, _base_currency, quote_currency in FX_IDENTITIES
    )


def _mutex_sql() -> str:
    return f"""
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(
                TG_TABLE_SCHEMA || ':' || NEW.instrument_id,
                {ADVISORY_LOCK_SEED}
            )
        );
    """


def _postgres_market_data_function_sql() -> str:
    return f"""
    CREATE OR REPLACE FUNCTION {POSTGRES_MARKET_DATA_FUNCTION}()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
    DECLARE
        resolved_instrument_type text;
        resolved_instrument_currency text;
        parsed_value numeric;
        expected_unit text;
    BEGIN
        {_mutex_sql()}

        SELECT instrument_type, currency
        INTO resolved_instrument_type, resolved_instrument_currency
        FROM instrument
        WHERE instrument_id = NEW.instrument_id;

        IF resolved_instrument_type IS NULL THEN
            RAISE EXCEPTION 'instrument market-data contract requires an existing instrument'
                USING ERRCODE = '23514';
        END IF;
        IF NOT (
            (NEW.metric_family = 'price'
             AND NEW.quote_basis IN ({_quoted(PRICE_QUOTE_BASES)}))
            OR (NEW.metric_family = 'nav'
                AND NEW.quote_basis IN ({_quoted(NAV_QUOTE_BASES)}))
            OR (NEW.metric_family = 'fx' AND NEW.quote_basis = 'spot')
        ) THEN
            RAISE EXCEPTION 'instrument market-data quote identity is not canonical'
                USING ERRCODE = '23514';
        END IF;
        IF (resolved_instrument_type = 'fx') <>
           (NEW.metric_family = 'fx' AND NEW.quote_basis = 'spot') THEN
            RAISE EXCEPTION 'FX market data requires a maintained fx instrument with fx/spot identity'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.currency <> resolved_instrument_currency THEN
            RAISE EXCEPTION 'market-data currency must match instrument currency'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status NOT IN ({_quoted(DATA_STATUSES)}) THEN
            RAISE EXCEPTION 'market-data status is not canonical'
                USING ERRCODE = '23514';
        END IF;
        IF trim(NEW.value) !~ '^[+]?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?$' THEN
            RAISE EXCEPTION 'market-data value must be a finite positive decimal'
                USING ERRCODE = '23514';
        END IF;
        BEGIN
            parsed_value := trim(NEW.value)::numeric;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'market-data value must be a finite positive decimal'
                USING ERRCODE = '23514';
        END;
        IF parsed_value <= 0 THEN
            RAISE EXCEPTION 'market-data value must be a finite positive decimal'
                USING ERRCODE = '23514';
        END IF;
        IF resolved_instrument_type = 'fx' AND NOT (
            {_fx_market_data_predicate()}
        ) THEN
            RAISE EXCEPTION 'FX market data does not match a maintained pair identity'
                USING ERRCODE = '23514';
        END IF;

        expected_unit := CASE
            WHEN resolved_instrument_type = 'fx' THEN 'rate'
            ELSE 'per_unit'
        END;
        IF NEW.price_unit <> expected_unit OR NEW.price_scale <> 1 THEN
            RAISE EXCEPTION 'market-data price contract is not canonical for its instrument/quote identity'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """


def _postgres_instrument_function_sql() -> str:
    maintained_ids = _quoted([item[0] for item in FX_IDENTITIES])
    return f"""
    CREATE OR REPLACE FUNCTION {POSTGRES_INSTRUMENT_FUNCTION}()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
    BEGIN
        {_mutex_sql()}

        IF NEW.instrument_type = 'fx' THEN
            IF NOT (
                {_fx_new_instrument_predicate()}
            ) THEN
                RAISE EXCEPTION 'fx instrument has no maintained pair identity'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.instrument_id IN ({maintained_ids}) THEN
            RAISE EXCEPTION 'maintained FX instrument id must retain fx identity'
                USING ERRCODE = '23514';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM instrument_market_data AS market_data
            WHERE market_data.instrument_id = NEW.instrument_id
              AND (
                  market_data.currency <> NEW.currency
                  OR ((NEW.instrument_type = 'fx') <>
                      (market_data.metric_family = 'fx'
                       AND market_data.quote_basis = 'spot'))
                  OR market_data.price_unit <> CASE
                      WHEN NEW.instrument_type = 'fx' THEN 'rate'
                      ELSE 'per_unit'
                  END
                  OR market_data.price_scale <> 1
              )
        ) THEN
            RAISE EXCEPTION 'instrument_type update conflicts with existing market-data contracts'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """


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


def _sqlite_market_data_trigger_sql(operation: str) -> str:
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
                      AND NEW.quote_basis IN ({_quoted(NAV_QUOTE_BASES)}))
                  OR (NEW.metric_family = 'fx' AND NEW.quote_basis = 'spot')
              )
              AND NEW.currency = instrument.currency
              AND NEW.status IN ({_quoted(DATA_STATUSES)})
              AND ({positive_decimal_sql})
              AND ((instrument.instrument_type = 'fx') =
                   (NEW.metric_family = 'fx' AND NEW.quote_basis = 'spot'))
              AND (
                  instrument.instrument_type <> 'fx'
                  OR {_fx_sqlite_point_predicate()}
              )
              AND NEW.price_unit = CASE
                  WHEN instrument.instrument_type = 'fx' THEN 'rate'
                  ELSE 'per_unit'
              END
              AND NEW.price_scale = 1
        ) THEN RAISE(ABORT, 'instrument market-data price contract is not canonical') END;
    END
    """


def _sqlite_instrument_trigger_sql(operation: str) -> str:
    trigger_name = (
        f"{INSTRUMENT_TRIGGER_NAME}_{operation.lower()}"
        if operation == "INSERT"
        else INSTRUMENT_TRIGGER_NAME
    )
    update_clause = (
        "UPDATE OF instrument_type, currency" if operation == "UPDATE" else "INSERT"
    )
    maintained_ids = _quoted([item[0] for item in FX_IDENTITIES])
    return f"""
    CREATE TRIGGER {trigger_name}
    BEFORE {update_clause} ON instrument
    FOR EACH ROW
    WHEN NOT (
        (
            NEW.instrument_type = 'fx'
            AND ({_fx_new_instrument_predicate()})
        )
        OR (
            NEW.instrument_type <> 'fx'
            AND NEW.instrument_id NOT IN ({maintained_ids})
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
              OR market_data.price_unit <> CASE
                  WHEN NEW.instrument_type = 'fx' THEN 'rate'
                  ELSE 'per_unit'
              END
              OR market_data.price_scale <> 1
          )
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'instrument_type update conflicts with canonical market-data contract'
        );
    END
    """


def _create_contract_triggers(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text(_postgres_market_data_function_sql()))
        connection.execute(sa.text(_postgres_instrument_function_sql()))
        connection.execute(
            sa.text(
                f"CREATE TRIGGER {MARKET_DATA_TRIGGER_NAME} "
                "BEFORE INSERT OR UPDATE OF instrument_id, metric_family, quote_basis, "
                "price_unit, price_scale, value, currency, status "
                "ON instrument_market_data FOR EACH ROW EXECUTE FUNCTION "
                f"{POSTGRES_MARKET_DATA_FUNCTION}()"
            )
        )
        connection.execute(
            sa.text(
                f"CREATE TRIGGER {INSTRUMENT_TRIGGER_NAME} "
                "BEFORE INSERT OR UPDATE OF instrument_type, currency "
                "ON instrument FOR EACH ROW EXECUTE FUNCTION "
                f"{POSTGRES_INSTRUMENT_FUNCTION}()"
            )
        )
    elif connection.dialect.name == "sqlite":
        for operation in ("INSERT", "UPDATE"):
            connection.execute(sa.text(_sqlite_market_data_trigger_sql(operation)))
            connection.execute(sa.text(_sqlite_instrument_trigger_sql(operation)))


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
        connection.execute(
            sa.text(
                "LOCK TABLE instrument_market_data, instrument_identifier, "
                "instrument IN ACCESS EXCLUSIVE MODE"
            )
        )

    sqlite_triggers = _sqlite_trigger_definitions(connection)
    _drop_contract_triggers(connection)
    _drop_sqlite_triggers(connection, sqlite_triggers)
    _replace_exchange_constraint(connection)

    changed_at = _watermark()
    _migrate_listing_identities(connection, changed_at)
    _insert_new_fx_instruments(connection, changed_at)
    _add_exchange_constraint(connection)

    _restore_noncontract_sqlite_triggers(connection, sqlite_triggers)
    _create_contract_triggers(connection)


def downgrade() -> None:
    raise RuntimeError(
        "European listing and FX identities are production data; restore the "
        "pre-migration database backup instead of deleting them."
    )
