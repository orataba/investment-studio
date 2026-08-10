"""Remove bonds and bond-only quotes from the shared Registry.

Bonds are not shared market-data instruments in this product. This migration
refuses to guess how any existing bond facts should be represented elsewhere.

Revision ID: 20260810_0023
Revises: 20260809_0022
"""

from __future__ import annotations

from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_0023"
down_revision: str | None = "20260809_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INSTRUMENT_TYPES = (
    "fund",
    "etf",
    "index",
    "equity",
    "cash",
    "fx",
    "other",
)
PRICE_QUOTE_BASES = ("last", "close", "adjusted_close", "par")
NAV_QUOTE_BASES = ("official_nav", "total_return_nav")
DATA_STATUSES = ("complete", "partial", "unavailable")
REMOVED_QUOTE_BASES = {"clean_price", "dirty_price", "accrued_interest"}
QUOTE_SELECTION_POLICY_ROLES = (
    "trading",
    "valuation",
    "total_return",
    "chart",
    "reference",
)

MARKET_DATA_TRIGGER_NAME = "trg_instrument_market_data_price_contract"
INSTRUMENT_TRIGGER_NAME = "trg_instrument_type_price_contract"
POSTGRES_MARKET_DATA_FUNCTION = "enforce_instrument_market_data_price_contract"
POSTGRES_INSTRUMENT_FUNCTION = "enforce_instrument_type_price_contract"
ADVISORY_LOCK_SEED = 202607150010


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _policy(value: object) -> dict[str, object] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _preflight(connection: sa.Connection) -> None:
    bond_ids = connection.execute(
        sa.text(
            "SELECT instrument_id FROM instrument "
            "WHERE lower(instrument_type) = 'bond' "
            "ORDER BY instrument_id LIMIT 10"
        )
    ).scalars().all()
    market_data_ids = connection.execute(
        sa.text(
            "SELECT instrument_market_data_id FROM instrument_market_data "
            "WHERE quote_basis IN ('clean_price', 'dirty_price', 'accrued_interest') "
            "OR price_unit = 'percent_of_par' "
            "ORDER BY instrument_market_data_id LIMIT 10"
        )
    ).scalars().all()
    policy_ids: list[str] = []
    for row in connection.execute(
        sa.text(
            "SELECT instrument_id, quote_selection_policy_json FROM instrument "
            "ORDER BY instrument_id"
        )
    ).mappings():
        policy = _policy(row["quote_selection_policy_json"])
        if policy is None:
            continue
        if any(
            any(
                str(item or "").strip().lower() in REMOVED_QUOTE_BASES
                for item in policy.get(role, [])
            )
            for role in QUOTE_SELECTION_POLICY_ROLES
            if isinstance(policy.get(role), list)
        ):
            policy_ids.append(str(row["instrument_id"]))
            if len(policy_ids) >= 10:
                break

    if bond_ids or market_data_ids or policy_ids:
        details = ", ".join(
            [
                *(f"bond:{value}" for value in bond_ids),
                *(f"market_data:{value}" for value in market_data_ids),
                *(f"policy:{value}" for value in policy_ids),
            ]
        )
        raise RuntimeError(
            "Bond Registry removal requires zero bond instruments and zero "
            f"bond-only quote facts. Resolve these rows first: {details}"
        )


def _sqlite_trigger_definitions(
    connection: sa.Connection,
) -> list[tuple[str, str]]:
    if connection.dialect.name != "sqlite":
        return []
    return [
        (str(row.name), str(row.sql))
        for row in connection.execute(
            sa.text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND sql IS NOT NULL "
                "ORDER BY name"
            )
        )
    ]


def _contract_trigger_names() -> set[str]:
    return {
        f"{MARKET_DATA_TRIGGER_NAME}_insert",
        f"{MARKET_DATA_TRIGGER_NAME}_update",
        INSTRUMENT_TRIGGER_NAME,
        f"{INSTRUMENT_TRIGGER_NAME}_insert",
    }


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
    elif connection.dialect.name == "sqlite":
        for name in _contract_trigger_names():
            connection.execute(sa.text(f'DROP TRIGGER IF EXISTS "{name}"'))


def _drop_sqlite_triggers(
    connection: sa.Connection,
    definitions: list[tuple[str, str]],
) -> None:
    for name, _sql in definitions:
        quoted_name = '"' + name.replace('"', '""') + '"'
        connection.execute(sa.text(f"DROP TRIGGER IF EXISTS {quoted_name}"))


def _restore_sqlite_triggers(
    connection: sa.Connection,
    definitions: list[tuple[str, str]],
) -> None:
    contract_names = _contract_trigger_names()
    for name, sql in definitions:
        if name not in contract_names:
            connection.execute(sa.text(sql))


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
            (NEW.instrument_id = 'fx-usd-hkd' AND resolved_instrument_currency = 'HKD')
            OR (NEW.instrument_id = 'fx-usd-cny' AND resolved_instrument_currency = 'CNY')
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
                (NEW.instrument_id = 'fx-usd-hkd' AND NEW.currency = 'HKD')
                OR (NEW.instrument_id = 'fx-usd-cny' AND NEW.currency = 'CNY')
            ) THEN
                RAISE EXCEPTION 'fx instrument has no maintained pair identity'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.instrument_id IN ('fx-usd-hkd', 'fx-usd-cny') THEN
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
                  OR (NEW.instrument_id = 'fx-usd-hkd' AND instrument.currency = 'HKD')
                  OR (NEW.instrument_id = 'fx-usd-cny' AND instrument.currency = 'CNY')
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
                f"""
                CREATE TRIGGER {MARKET_DATA_TRIGGER_NAME}
                BEFORE INSERT OR UPDATE OF instrument_id, metric_family, quote_basis,
                    price_unit, price_scale, value, currency, status
                ON instrument_market_data
                FOR EACH ROW EXECUTE FUNCTION {POSTGRES_MARKET_DATA_FUNCTION}()
                """
            )
        )
        connection.execute(
            sa.text(
                f"""
                CREATE TRIGGER {INSTRUMENT_TRIGGER_NAME}
                BEFORE INSERT OR UPDATE OF instrument_type, currency
                ON instrument
                FOR EACH ROW EXECUTE FUNCTION {POSTGRES_INSTRUMENT_FUNCTION}()
                """
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
                "LOCK TABLE instrument_market_data, instrument IN ACCESS EXCLUSIVE MODE"
            )
        )
    _preflight(connection)

    trigger_definitions = _sqlite_trigger_definitions(connection)
    _drop_contract_triggers(connection)
    _drop_sqlite_triggers(connection, trigger_definitions)

    with op.batch_alter_table(
        "instrument",
        recreate="always" if connection.dialect.name == "sqlite" else "auto",
    ) as batch_op:
        batch_op.drop_constraint("instrument_type_contract", type_="check")
        batch_op.create_check_constraint(
            "instrument_type_contract",
            f"instrument_type IN ({_quoted(INSTRUMENT_TYPES)})",
        )

    with op.batch_alter_table(
        "instrument_market_data",
        recreate="always" if connection.dialect.name == "sqlite" else "auto",
    ) as batch_op:
        batch_op.drop_constraint("quote_identity_contract", type_="check")
        batch_op.drop_constraint("price_unit_scale_contract", type_="check")
        batch_op.create_check_constraint(
            "quote_identity_contract",
            "(metric_family = 'price' AND quote_basis IN "
            f"({_quoted(PRICE_QUOTE_BASES)})) OR "
            "(metric_family = 'nav' AND quote_basis IN "
            f"({_quoted(NAV_QUOTE_BASES)})) OR "
            "(metric_family = 'fx' AND quote_basis = 'spot')",
        )
        batch_op.create_check_constraint(
            "price_unit_scale_contract",
            "(price_unit = 'per_unit' AND price_scale = 1) OR "
            "(price_unit = 'rate' AND price_scale = 1)",
        )

    _restore_sqlite_triggers(connection, trigger_definitions)
    _create_contract_triggers(connection)


def downgrade() -> None:
    raise RuntimeError(
        "Bonds are no longer shared Registry instruments; restore the "
        "pre-migration database backup instead of recreating that model."
    )
