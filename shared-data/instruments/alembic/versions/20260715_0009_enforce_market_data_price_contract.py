"""Enforce the canonical market-data quote and price contract.

Revision ID: 20260715_0009
Revises: 20260715_0008

Revision 0008 introduced and populated ``price_unit`` / ``price_scale`` as an
additive rollout.  This forward hardening revision first classifies every
existing row.  Rows with both contract columns NULL are backfilled only when
their quote identity and instrument type make the result deterministic;
partial or conflicting contracts fail closed before DDL is changed.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0009"
down_revision: str | None = "20260715_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Alembic applies the repository naming convention around these logical names.
# The physical names are therefore ``ck_instrument_market_data_<name>``.
QUOTE_IDENTITY_CHECK_NAME = "quote_identity_contract"
UNIT_SCALE_CHECK_NAME = "price_unit_scale_contract"
MARKET_DATA_TRIGGER_NAME = "trg_instrument_market_data_price_contract"
INSTRUMENT_TRIGGER_NAME = "trg_instrument_type_price_contract"
POSTGRES_MARKET_DATA_FUNCTION = "enforce_instrument_market_data_price_contract"
POSTGRES_INSTRUMENT_FUNCTION = "enforce_instrument_type_price_contract"

PRICE_QUOTE_BASES = (
    "last",
    "close",
    "adjusted_close",
    "clean_price",
    "dirty_price",
    "par",
    "accrued_interest",
)
NAV_QUOTE_BASES = (
    "official_nav",
    "total_return_nav",
    "cumulative_nav",
    "accumulated_nav",
    "cum_nav",
    "dividend_adjusted_nav",
    "reinvested_nav",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


QUOTE_IDENTITY_SQL = f"""
(
    (metric_family = 'price' AND quote_basis IN ({_quoted(PRICE_QUOTE_BASES)}))
    OR (metric_family = 'nav' AND quote_basis IN ({_quoted(NAV_QUOTE_BASES)}))
    OR (metric_family = 'fx' AND quote_basis = 'spot')
)
""".strip()

UNIT_SCALE_SQL = """
(
    (price_unit = 'per_unit' AND price_scale = 1)
    OR (price_unit = 'percent_of_par' AND price_scale = 0.01)
    OR (price_unit = 'rate' AND price_scale = 1)
)
""".strip()

_JOINED_QUOTE_IDENTITY_SQL = f"""
(
    (market_data.metric_family = 'price'
     AND market_data.quote_basis IN ({_quoted(PRICE_QUOTE_BASES)}))
    OR (market_data.metric_family = 'nav'
        AND market_data.quote_basis IN ({_quoted(NAV_QUOTE_BASES)}))
    OR (market_data.metric_family = 'fx' AND market_data.quote_basis = 'spot')
)
""".strip()

_EXPECTED_UNIT_SQL = """
CASE
    WHEN lower(instrument.instrument_type) = 'bond'
         AND market_data.metric_family = 'price'
        THEN 'percent_of_par'
    WHEN lower(instrument.instrument_type) = 'fx'
         OR market_data.metric_family = 'fx'
         OR market_data.quote_basis = 'spot'
        THEN 'rate'
    ELSE 'per_unit'
END
""".strip()

_EXPECTED_SCALE_SQL = """
CASE
    WHEN lower(instrument.instrument_type) = 'bond'
         AND market_data.metric_family = 'price'
        THEN 0.01
    ELSE 1
END
""".strip()

_PREFLIGHT_VIOLATION_SQL = f"""
instrument.instrument_id IS NULL
OR NOT {_JOINED_QUOTE_IDENTITY_SQL}
OR (
    market_data.quote_basis = 'accrued_interest'
    AND lower(instrument.instrument_type) <> 'bond'
)
OR ((market_data.price_unit IS NULL) <> (market_data.price_scale IS NULL))
OR (
    market_data.price_unit IS NOT NULL
    AND market_data.price_scale IS NOT NULL
    AND (
        market_data.price_unit <> {_EXPECTED_UNIT_SQL}
        OR market_data.price_scale <> {_EXPECTED_SCALE_SQL}
    )
)
""".strip()


def _preflight_rows(connection: sa.Connection) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in connection.execute(
            sa.text(
                f"""
                SELECT
                    market_data.instrument_market_data_id,
                    market_data.instrument_id,
                    instrument.instrument_type,
                    market_data.metric_family,
                    market_data.quote_basis,
                    market_data.price_unit,
                    market_data.price_scale
                FROM instrument_market_data AS market_data
                LEFT JOIN instrument
                  ON instrument.instrument_id = market_data.instrument_id
                WHERE {_PREFLIGHT_VIOLATION_SQL}
                ORDER BY market_data.instrument_market_data_id
                LIMIT 10
                """
            )
        ).mappings()
    ]


def _format_preflight_rows(rows: list[dict[str, object]]) -> str:
    return "; ".join(
        (
            f"id={row.get('instrument_market_data_id')}, "
            f"instrument={row.get('instrument_id')!r}, "
            f"instrument_type={row.get('instrument_type')!r}, "
            f"quote={row.get('metric_family')!r}/{row.get('quote_basis')!r}, "
            f"contract={row.get('price_unit')!r}/{row.get('price_scale')!r}"
        )
        for row in rows
    )


def _backfill_safe_null_contracts(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            UPDATE instrument_market_data
            SET
                price_unit = CASE
                    WHEN metric_family = 'price'
                         AND EXISTS (
                             SELECT 1
                             FROM instrument
                             WHERE instrument.instrument_id = instrument_market_data.instrument_id
                               AND lower(instrument.instrument_type) = 'bond'
                         )
                        THEN 'percent_of_par'
                    WHEN metric_family = 'fx'
                         OR quote_basis = 'spot'
                         OR EXISTS (
                             SELECT 1
                             FROM instrument
                             WHERE instrument.instrument_id = instrument_market_data.instrument_id
                               AND lower(instrument.instrument_type) = 'fx'
                         )
                        THEN 'rate'
                    ELSE 'per_unit'
                END,
                price_scale = CASE
                    WHEN metric_family = 'price'
                         AND EXISTS (
                             SELECT 1
                             FROM instrument
                             WHERE instrument.instrument_id = instrument_market_data.instrument_id
                               AND lower(instrument.instrument_type) = 'bond'
                         )
                        THEN 0.01
                    ELSE 1
                END
            WHERE price_unit IS NULL AND price_scale IS NULL
            """
        )
    )


def _postgres_market_data_trigger_sql() -> str:
    return f"""
    CREATE FUNCTION {POSTGRES_MARKET_DATA_FUNCTION}()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
    DECLARE
        resolved_instrument_type text;
        expected_unit text;
        expected_scale numeric;
    BEGIN
        SELECT lower(instrument_type)
        INTO resolved_instrument_type
        FROM instrument
        WHERE instrument_id = NEW.instrument_id;

        IF resolved_instrument_type IS NULL THEN
            RAISE EXCEPTION 'instrument market-data price contract requires an existing instrument'
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

        IF NEW.quote_basis = 'accrued_interest'
           AND resolved_instrument_type <> 'bond' THEN
            RAISE EXCEPTION 'accrued_interest is valid only for bond price data'
                USING ERRCODE = '23514';
        END IF;

        IF resolved_instrument_type = 'bond' AND NEW.metric_family = 'price' THEN
            expected_unit := 'percent_of_par';
            expected_scale := 0.01;
        ELSIF resolved_instrument_type = 'fx'
              OR NEW.metric_family = 'fx'
              OR NEW.quote_basis = 'spot' THEN
            expected_unit := 'rate';
            expected_scale := 1;
        ELSE
            expected_unit := 'per_unit';
            expected_scale := 1;
        END IF;

        IF NEW.price_unit <> expected_unit OR NEW.price_scale <> expected_scale THEN
            RAISE EXCEPTION
                'market-data price contract is not canonical for its instrument/quote identity'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;

    CREATE TRIGGER {MARKET_DATA_TRIGGER_NAME}
    BEFORE INSERT OR UPDATE OF instrument_id, metric_family, quote_basis, price_unit, price_scale
    ON instrument_market_data
    FOR EACH ROW
    EXECUTE FUNCTION {POSTGRES_MARKET_DATA_FUNCTION}();
    """


def _postgres_instrument_trigger_sql() -> str:
    return f"""
    CREATE FUNCTION {POSTGRES_INSTRUMENT_FUNCTION}()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM instrument_market_data AS market_data
            WHERE market_data.instrument_id = NEW.instrument_id
              AND (
                  (
                      market_data.quote_basis = 'accrued_interest'
                      AND lower(NEW.instrument_type) <> 'bond'
                  )
                  OR market_data.price_unit <> CASE
                      WHEN lower(NEW.instrument_type) = 'bond'
                           AND market_data.metric_family = 'price'
                          THEN 'percent_of_par'
                      WHEN lower(NEW.instrument_type) = 'fx'
                           OR market_data.metric_family = 'fx'
                           OR market_data.quote_basis = 'spot'
                          THEN 'rate'
                      ELSE 'per_unit'
                  END
                  OR market_data.price_scale <> CASE
                      WHEN lower(NEW.instrument_type) = 'bond'
                           AND market_data.metric_family = 'price'
                          THEN 0.01
                      ELSE 1
                  END
              )
        ) THEN
            RAISE EXCEPTION
                'instrument_type update conflicts with existing market-data price contracts'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;

    CREATE TRIGGER {INSTRUMENT_TRIGGER_NAME}
    BEFORE UPDATE OF instrument_type
    ON instrument
    FOR EACH ROW
    WHEN (OLD.instrument_type IS DISTINCT FROM NEW.instrument_type)
    EXECUTE FUNCTION {POSTGRES_INSTRUMENT_FUNCTION}();
    """


def _sqlite_market_data_trigger_sql(*, operation: str) -> str:
    trigger_name = f"{MARKET_DATA_TRIGGER_NAME}_{operation.lower()}"
    return f"""
    CREATE TRIGGER {trigger_name}
    BEFORE {operation} ON instrument_market_data
    FOR EACH ROW
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1
            FROM instrument
            WHERE instrument.instrument_id = NEW.instrument_id
              AND (
                  (NEW.metric_family = 'price'
                   AND NEW.quote_basis IN ({_quoted(PRICE_QUOTE_BASES)}))
                  OR (NEW.metric_family = 'nav'
                      AND NEW.quote_basis IN ({_quoted(NAV_QUOTE_BASES)}))
                  OR (NEW.metric_family = 'fx' AND NEW.quote_basis = 'spot')
              )
              AND NOT (
                  NEW.quote_basis = 'accrued_interest'
                  AND lower(instrument.instrument_type) <> 'bond'
              )
              AND NEW.price_unit = CASE
                  WHEN lower(instrument.instrument_type) = 'bond'
                       AND NEW.metric_family = 'price'
                      THEN 'percent_of_par'
                  WHEN lower(instrument.instrument_type) = 'fx'
                       OR NEW.metric_family = 'fx'
                       OR NEW.quote_basis = 'spot'
                      THEN 'rate'
                  ELSE 'per_unit'
              END
              AND NEW.price_scale = CASE
                  WHEN lower(instrument.instrument_type) = 'bond'
                       AND NEW.metric_family = 'price'
                      THEN 0.01
                  ELSE 1
              END
        ) THEN RAISE(ABORT, 'instrument market-data price contract is not canonical') END;
    END
    """


def _sqlite_instrument_trigger_sql() -> str:
    return f"""
    CREATE TRIGGER {INSTRUMENT_TRIGGER_NAME}
    BEFORE UPDATE OF instrument_type ON instrument
    FOR EACH ROW
    WHEN lower(OLD.instrument_type) <> lower(NEW.instrument_type)
         AND EXISTS (
             SELECT 1
             FROM instrument_market_data AS market_data
             WHERE market_data.instrument_id = NEW.instrument_id
               AND (
                   (
                       market_data.quote_basis = 'accrued_interest'
                       AND lower(NEW.instrument_type) <> 'bond'
                   )
                   OR market_data.price_unit <> CASE
                       WHEN lower(NEW.instrument_type) = 'bond'
                            AND market_data.metric_family = 'price'
                           THEN 'percent_of_par'
                       WHEN lower(NEW.instrument_type) = 'fx'
                            OR market_data.metric_family = 'fx'
                            OR market_data.quote_basis = 'spot'
                           THEN 'rate'
                       ELSE 'per_unit'
                   END
                   OR market_data.price_scale <> CASE
                       WHEN lower(NEW.instrument_type) = 'bond'
                            AND market_data.metric_family = 'price'
                           THEN 0.01
                       ELSE 1
                   END
               )
         )
    BEGIN
        SELECT RAISE(ABORT, 'instrument_type update conflicts with market-data price contracts');
    END
    """


def _create_cross_table_contract_triggers(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        op.execute(sa.text(_postgres_market_data_trigger_sql()))
        op.execute(sa.text(_postgres_instrument_trigger_sql()))
        return
    if connection.dialect.name == "sqlite":
        op.execute(sa.text(_sqlite_market_data_trigger_sql(operation="INSERT")))
        op.execute(sa.text(_sqlite_market_data_trigger_sql(operation="UPDATE")))
        op.execute(sa.text(_sqlite_instrument_trigger_sql()))


def _drop_cross_table_contract_triggers(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {INSTRUMENT_TRIGGER_NAME} ON instrument"))
        op.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS {MARKET_DATA_TRIGGER_NAME} "
                "ON instrument_market_data"
            )
        )
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {POSTGRES_INSTRUMENT_FUNCTION}()"))
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {POSTGRES_MARKET_DATA_FUNCTION}()"))
        return
    if connection.dialect.name == "sqlite":
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {INSTRUMENT_TRIGGER_NAME}"))
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {MARKET_DATA_TRIGGER_NAME}_insert"))
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {MARKET_DATA_TRIGGER_NAME}_update"))


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE instrument_market_data, instrument IN ACCESS EXCLUSIVE MODE")
        )

    invalid_rows = _preflight_rows(connection)
    if invalid_rows:
        raise RuntimeError(
            "Cannot apply 20260715_0009: existing market-data rows violate the "
            "canonical quote/price contract; no data was changed. Samples: "
            + _format_preflight_rows(invalid_rows)
        )

    _backfill_safe_null_contracts(connection)
    invalid_rows = _preflight_rows(connection)
    if invalid_rows:
        raise RuntimeError(
            "Cannot apply 20260715_0009: deterministic price-contract backfill "
            "did not produce canonical rows. Samples: "
            + _format_preflight_rows(invalid_rows)
        )

    remaining_nulls = connection.scalar(
        sa.text(
            "SELECT count(*) FROM instrument_market_data "
            "WHERE price_unit IS NULL OR price_scale IS NULL"
        )
    )
    if int(remaining_nulls or 0) != 0:
        raise RuntimeError(
            "Cannot apply 20260715_0009: price_unit/price_scale still contain NULL values."
        )

    with op.batch_alter_table("instrument_market_data") as batch_op:
        batch_op.alter_column(
            "price_unit",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.alter_column(
            "price_scale",
            existing_type=sa.Numeric(28, 12),
            nullable=False,
        )
        batch_op.create_check_constraint(QUOTE_IDENTITY_CHECK_NAME, QUOTE_IDENTITY_SQL)
        batch_op.create_check_constraint(UNIT_SCALE_CHECK_NAME, UNIT_SCALE_SQL)

    _create_cross_table_contract_triggers(connection)


def downgrade() -> None:
    connection = op.get_bind()
    _drop_cross_table_contract_triggers(connection)
    with op.batch_alter_table("instrument_market_data") as batch_op:
        batch_op.drop_constraint(UNIT_SCALE_CHECK_NAME, type_="check")
        batch_op.drop_constraint(QUOTE_IDENTITY_CHECK_NAME, type_="check")
        batch_op.alter_column(
            "price_scale",
            existing_type=sa.Numeric(28, 12),
            nullable=True,
        )
        batch_op.alter_column(
            "price_unit",
            existing_type=sa.String(),
            nullable=True,
        )
