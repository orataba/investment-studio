"""Serialize cross-table price-contract writes by instrument.

Revision ID: 20260715_0010
Revises: 20260715_0009

Revision 0009 installed PostgreSQL triggers that validate the relationship
between ``instrument.instrument_type`` and each market-data price contract.
Those checks were correct for a single transaction, but two concurrent writers
could each validate an older committed snapshot and leave a conflicting pair.

This revision keeps the same contract and trigger names while making both
trigger functions acquire the same transaction-scoped advisory mutex before
their cross-table read.  The mutex is keyed by registry schema and instrument
ID, so unrelated schemas and instruments remain independent.  Each trigger
acquires exactly one mutex and always does so before reading the other table;
PostgreSQL may still abort one of two multi-row transactions that visit several
instruments in opposite orders, but it cannot commit a cross-table contract
violation.

Downgrade restores the revision-0009 trigger function bodies without the
mutex.  It does not remove or relax revision 0009's populated price fields,
NOT NULL columns, check constraints, triggers, or canonical data contract.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0010"
down_revision: str | None = "20260715_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


POSTGRES_MARKET_DATA_FUNCTION = "enforce_instrument_market_data_price_contract"
POSTGRES_INSTRUMENT_FUNCTION = "enforce_instrument_type_price_contract"
ADVISORY_LOCK_SEED = 202607150010

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
OR market_data.price_unit <> {_EXPECTED_UNIT_SQL}
OR market_data.price_scale <> {_EXPECTED_SCALE_SQL}
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


def _mutex_sql(*, guarded: bool) -> str:
    if not guarded:
        return ""
    return f"""
        -- Both contract triggers take this mutex before reading the other table.
        -- A hash collision only serializes unrelated instruments; it cannot weaken
        -- the invariant.
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(
                TG_TABLE_SCHEMA || ':' || NEW.instrument_id,
                {ADVISORY_LOCK_SEED}
            )
        );
    """


def _postgres_market_data_function_sql(*, guarded: bool) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION {POSTGRES_MARKET_DATA_FUNCTION}()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
    DECLARE
        resolved_instrument_type text;
        expected_unit text;
        expected_scale numeric;
    BEGIN
        {_mutex_sql(guarded=guarded)}

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
    """


def _postgres_instrument_function_sql(*, guarded: bool) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION {POSTGRES_INSTRUMENT_FUNCTION}()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
    BEGIN
        {_mutex_sql(guarded=guarded)}

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
    """


def _replace_postgres_functions(connection: sa.Connection, *, guarded: bool) -> None:
    connection.execute(sa.text(_postgres_market_data_function_sql(guarded=guarded)))
    connection.execute(sa.text(_postgres_instrument_function_sql(guarded=guarded)))


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        # SQLite serializes writers at the database level, so the 0009 triggers
        # already cannot observe the PostgreSQL write-skew fixed here.
        return

    connection.execute(
        sa.text("LOCK TABLE instrument_market_data, instrument IN ACCESS EXCLUSIVE MODE")
    )
    invalid_rows = _preflight_rows(connection)
    if invalid_rows:
        raise RuntimeError(
            "Cannot apply 20260715_0010: concurrent price-contract drift already "
            "exists; no trigger functions were changed. Samples: "
            + _format_preflight_rows(invalid_rows)
        )
    _replace_postgres_functions(connection, guarded=True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    connection.execute(
        sa.text("LOCK TABLE instrument_market_data, instrument IN ACCESS EXCLUSIVE MODE")
    )
    _replace_postgres_functions(connection, guarded=False)
