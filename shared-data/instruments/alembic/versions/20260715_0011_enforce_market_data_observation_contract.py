"""Enforce the canonical market-data observation and FX identity contract.

Revision ID: 20260715_0011
Revises: 20260715_0010

Revision 0010 serialized the cross-table price-contract checks.  This forward
revision keeps that same per-instrument advisory mutex and closes the remaining
observation-level gaps: every value is positive and finite, every point uses
its instrument's canonical currency, statuses are canonical, and FX rows use
one of the maintained pair identities with ``fx/spot`` semantics.

The revision also materializes every quote-selection role and performs the
one-time promotion of legacy email refresh cursors into
``last_successful_requested_at``.  Runtime code can therefore read both
contracts explicitly without permanently inferring missing policy or cursor
state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
import json

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0011"
down_revision: str | None = "20260715_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MARKET_DATA_TRIGGER_NAME = "trg_instrument_market_data_price_contract"
INSTRUMENT_TRIGGER_NAME = "trg_instrument_type_price_contract"
POSTGRES_MARKET_DATA_FUNCTION = "enforce_instrument_market_data_price_contract"
POSTGRES_INSTRUMENT_FUNCTION = "enforce_instrument_type_price_contract"
ADVISORY_LOCK_SEED = 202607150010

INSTRUMENT_TYPE_CHECK_NAME = "instrument_type_contract"
INSTRUMENT_CURRENCY_CHECK_NAME = "instrument_currency_contract"
MARKET_DATA_STATUS_CHECK_NAME = "market_data_status_contract"

INSTRUMENT_TYPES = (
    "fund",
    "etf",
    "index",
    "bond",
    "equity",
    "cash",
    "fx",
    "other",
)
DATA_STATUSES = ("complete", "partial", "unavailable")
EMAIL_REFRESH_SUCCESS_STATUSES = frozenset({"imported", "no_match", "no_new_data"})
FX_IDENTITIES: dict[str, tuple[str, str]] = {
    "fx-usd-hkd": ("USD", "HKD"),
    "fx-usd-cny": ("USD", "CNY"),
}
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
QUOTE_SELECTION_POLICY_ROLES = (
    "trading",
    "valuation",
    "total_return",
    "chart",
    "reference",
)
QUOTE_SELECTION_POLICY_DEFAULTS: dict[str, dict[str, list[str]]] = {
    "fund": {
        "trading": ["last", "close", "official_nav"],
        "valuation": ["official_nav", "close", "last"],
        "total_return": [
            "total_return_nav",
            "dividend_adjusted_nav",
            "reinvested_nav",
            "adjusted_close",
            "official_nav",
            "close",
        ],
        "chart": [
            "total_return_nav",
            "dividend_adjusted_nav",
            "reinvested_nav",
            "adjusted_close",
            "official_nav",
            "close",
        ],
        "reference": ["official_nav", "close", "last"],
    },
    "etf": {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "equity": {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "index": {
        "trading": ["close", "last"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "bond": {
        "trading": ["clean_price", "dirty_price"],
        "valuation": ["dirty_price", "clean_price"],
        "total_return": ["dirty_price", "clean_price"],
        "chart": ["dirty_price", "clean_price"],
        "reference": ["clean_price", "dirty_price"],
    },
    "cash": {
        "trading": ["par"],
        "valuation": ["par"],
        "total_return": ["par"],
        "chart": ["par"],
        "reference": ["par"],
    },
    "fx": {
        "trading": ["spot"],
        "valuation": ["spot"],
        "total_return": ["spot"],
        "chart": ["spot"],
        "reference": ["spot"],
    },
    "other": {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
}
VALID_QUOTE_BASES = frozenset((*PRICE_QUOTE_BASES, *NAV_QUOTE_BASES, "spot"))
VALUATION_PROHIBITED_BASES = frozenset(
    {
        "adjusted_close",
        "total_return_nav",
        "cumulative_nav",
        "accumulated_nav",
        "cum_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
    }
)
CASH_CUMULATIVE_NAV_BASES = frozenset(
    {"cumulative_nav", "accumulated_nav", "cum_nav"}
)


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _as_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _optional_text(value: object) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


def _normalized_refresh_status(
    refresh_status: object,
    source_settings: object,
) -> dict[str, object]:
    previous = _as_mapping(refresh_status)
    settings = _as_mapping(source_settings)
    status = _optional_text(previous.get("status")) or "idle"
    mode = (
        _optional_text(previous.get("mode"))
        or _optional_text(settings.get("source_mode"))
        or "manual"
    ).lower()
    if mode not in {"manual", "email", "api"}:
        raise RuntimeError(f'Unsupported refresh mode "{mode}" during 0011 normalization.')
    requested_at = _optional_text(previous.get("requested_at"))
    cursor = _optional_text(previous.get("last_successful_requested_at"))
    if (
        cursor is None
        and mode == "email"
        and status.lower() in EMAIL_REFRESH_SUCCESS_STATUSES
        and requested_at is not None
    ):
        cursor = requested_at
    return {
        "status": status,
        "message": str(previous.get("message") or ""),
        "requested_at": requested_at,
        "requested_by": _optional_text(previous.get("requested_by")),
        "mode": mode,
        "last_successful_requested_at": cursor,
    }


def _normalize_refresh_status_rows(connection: sa.Connection) -> None:
    update_statement = sa.text(
        "UPDATE instrument SET refresh_status_json = :refresh_status "
        "WHERE instrument_id = :instrument_id"
    ).bindparams(sa.bindparam("refresh_status", type_=sa.JSON()))
    rows = connection.execute(
        sa.text(
            "SELECT instrument_id, refresh_status_json, source_settings_json "
            "FROM instrument ORDER BY instrument_id"
        )
    ).mappings()
    for row in rows:
        connection.execute(
            update_statement,
            {
                "instrument_id": row["instrument_id"],
                "refresh_status": _normalized_refresh_status(
                    row["refresh_status_json"],
                    row["source_settings_json"],
                ),
            },
        )


def _quote_selection_policy_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("quote_selection_policy is not valid JSON") from error
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("quote_selection_policy must be a JSON object")


def _normalized_quote_selection_policy(
    value: object,
    *,
    instrument_type: str,
) -> dict[str, list[str]]:
    raw_policy = _quote_selection_policy_mapping(value)
    extra_roles = sorted(set(raw_policy).difference(QUOTE_SELECTION_POLICY_ROLES))
    if extra_roles:
        raise ValueError(
            "quote_selection_policy has unsupported role(s): " + ", ".join(extra_roles)
        )

    defaults = QUOTE_SELECTION_POLICY_DEFAULTS[instrument_type]
    normalized: dict[str, list[str]] = {}
    for role in QUOTE_SELECTION_POLICY_ROLES:
        raw_values = raw_policy.get(role)
        if raw_values is None or raw_values == []:
            normalized[role] = list(defaults[role])
            continue
        if not isinstance(raw_values, list):
            raise ValueError(f"quote_selection_policy.{role} must be a list")
        values = [str(item or "").strip() for item in raw_values]
        if any(not item or item not in VALID_QUOTE_BASES for item in values):
            raise ValueError(f"quote_selection_policy.{role} has an unsupported quote basis")
        if len(values) != len(set(values)):
            raise ValueError(f"quote_selection_policy.{role} has duplicate quote bases")
        if "accrued_interest" in values:
            raise ValueError(
                f"quote_selection_policy.{role} contains component-only accrued_interest"
            )
        normalized[role] = values

    invalid_valuation = sorted(
        set(normalized["valuation"]).intersection(VALUATION_PROHIBITED_BASES)
    )
    if invalid_valuation:
        raise ValueError(
            "quote_selection_policy.valuation contains total-return quote bases"
        )
    for role in ("total_return", "chart"):
        if set(normalized[role]).intersection(CASH_CUMULATIVE_NAV_BASES):
            raise ValueError(
                f"quote_selection_policy.{role} contains cash-cumulative NAV"
            )
    return normalized


def _normalize_quote_selection_policy_rows(connection: sa.Connection) -> None:
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
    for row in rows:
        connection.execute(
            update_statement,
            {
                "instrument_id": row["instrument_id"],
                "policy": _normalized_quote_selection_policy(
                    row["quote_selection_policy_json"],
                    instrument_type=str(row["instrument_type"]),
                ),
            },
        )


def _positive_decimal(value: object) -> bool:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed > 0


def _canonical_unit_and_scale(
    instrument_type: str,
    metric_family: str,
) -> tuple[str, Decimal]:
    if instrument_type == "bond" and metric_family == "price":
        return "percent_of_par", Decimal("0.01")
    if instrument_type == "fx":
        return "rate", Decimal("1")
    return "per_unit", Decimal("1")


def _instrument_violation(row: Mapping[str, object]) -> str | None:
    instrument_id = str(row.get("instrument_id") or "").strip()
    instrument_type = str(row.get("instrument_type") or "").strip()
    currency = str(row.get("currency") or "").strip()
    if instrument_type not in INSTRUMENT_TYPES:
        return "unsupported instrument_type"
    if not currency or len(currency) > 8 or currency != currency.upper():
        return "invalid canonical currency"
    fx_identity = FX_IDENTITIES.get(instrument_id)
    if instrument_type == "fx":
        if fx_identity is None:
            return "fx instrument has no maintained identity"
        if currency != fx_identity[1]:
            return "fx instrument currency does not match pair quote currency"
    elif fx_identity is not None:
        return "maintained fx identity is not typed as fx"
    try:
        _normalized_quote_selection_policy(
            row.get("quote_selection_policy_json"),
            instrument_type=instrument_type,
        )
    except ValueError as error:
        return str(error)
    return None


def _market_data_violation(row: Mapping[str, object]) -> str | None:
    instrument_type = str(row.get("instrument_type") or "").strip()
    instrument_currency = str(row.get("instrument_currency") or "").strip()
    metric_family = str(row.get("metric_family") or "").strip()
    quote_basis = str(row.get("quote_basis") or "").strip()
    point_currency = str(row.get("point_currency") or "").strip()
    status = str(row.get("status") or "").strip()
    if metric_family == "price":
        identity_valid = quote_basis in PRICE_QUOTE_BASES
    elif metric_family == "nav":
        identity_valid = quote_basis in NAV_QUOTE_BASES
    else:
        identity_valid = metric_family == "fx" and quote_basis == "spot"
    if not identity_valid:
        return "non-canonical quote identity"
    if quote_basis == "accrued_interest" and instrument_type != "bond":
        return "accrued_interest is not attached to a bond"
    is_fx_quote = metric_family == "fx" or quote_basis == "spot"
    if (instrument_type == "fx") != is_fx_quote:
        return "fx instrument/quote identity mismatch"
    if point_currency != instrument_currency:
        return "point currency does not match instrument currency"
    if status not in DATA_STATUSES:
        return "unsupported market-data status"
    if not _positive_decimal(row.get("value")):
        return "market-data value is not finite and positive"
    expected_unit, expected_scale = _canonical_unit_and_scale(
        instrument_type,
        metric_family,
    )
    if str(row.get("price_unit") or "") != expected_unit:
        return "non-canonical price unit"
    try:
        actual_scale = Decimal(str(row.get("price_scale")))
    except (InvalidOperation, TypeError, ValueError):
        return "invalid price scale"
    if actual_scale != expected_scale:
        return "non-canonical price scale"
    if instrument_type == "fx":
        fx_identity = FX_IDENTITIES.get(str(row.get("instrument_id") or ""))
        if fx_identity is None or instrument_currency != fx_identity[1]:
            return "fx point does not match a maintained pair identity"
    return None


def _preflight_violations(connection: sa.Connection) -> list[str]:
    violations: list[str] = []
    instrument_rows = connection.execute(
        sa.text(
            "SELECT instrument_id, instrument_type, currency, "
            "quote_selection_policy_json "
            "FROM instrument ORDER BY instrument_id"
        )
    ).mappings()
    for row in instrument_rows:
        reason = _instrument_violation(row)
        if reason:
            violations.append(f"instrument={row['instrument_id']!r}: {reason}")
            if len(violations) == 10:
                return violations

    market_rows = connection.execute(
        sa.text(
            """
            SELECT market_data.instrument_market_data_id,
                   market_data.instrument_id,
                   instrument.instrument_type,
                   instrument.currency AS instrument_currency,
                   market_data.metric_family,
                   market_data.quote_basis,
                   market_data.value,
                   market_data.currency AS point_currency,
                   market_data.price_unit,
                   market_data.price_scale,
                   market_data.status
            FROM instrument_market_data AS market_data
            LEFT JOIN instrument
              ON instrument.instrument_id = market_data.instrument_id
            ORDER BY market_data.instrument_market_data_id
            """
        )
    ).mappings()
    for row in market_rows:
        reason = _market_data_violation(row)
        if reason:
            violations.append(
                f"market_data={row['instrument_market_data_id']!r}, "
                f"instrument={row['instrument_id']!r}: {reason}"
            )
            if len(violations) == 10:
                break
    return violations


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
        expected_scale numeric;
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
            (NEW.metric_family = 'price' AND NEW.quote_basis IN ({_quoted(PRICE_QUOTE_BASES)}))
            OR (NEW.metric_family = 'nav' AND NEW.quote_basis IN ({_quoted(NAV_QUOTE_BASES)}))
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
        IF (resolved_instrument_type = 'fx')
           <> (NEW.metric_family = 'fx' AND NEW.quote_basis = 'spot') THEN
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

        IF resolved_instrument_type = 'bond' AND NEW.metric_family = 'price' THEN
            expected_unit := 'percent_of_par';
            expected_scale := 0.01;
        ELSIF resolved_instrument_type = 'fx' THEN
            expected_unit := 'rate';
            expected_scale := 1;
        ELSE
            expected_unit := 'per_unit';
            expected_scale := 1;
        END IF;
        IF NEW.price_unit <> expected_unit OR NEW.price_scale <> expected_scale THEN
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
                      (market_data.metric_family = 'fx' AND market_data.quote_basis = 'spot'))
                  OR (market_data.quote_basis = 'accrued_interest'
                      AND NEW.instrument_type <> 'bond')
                  OR market_data.price_unit <> CASE
                      WHEN NEW.instrument_type = 'bond' AND market_data.metric_family = 'price'
                          THEN 'percent_of_par'
                      WHEN NEW.instrument_type = 'fx' THEN 'rate'
                      ELSE 'per_unit'
                  END
                  OR market_data.price_scale <> CASE
                      WHEN NEW.instrument_type = 'bond' AND market_data.metric_family = 'price'
                          THEN 0.01
                      ELSE 1
                  END
              )
        ) THEN
            RAISE EXCEPTION 'instrument_type update conflicts with existing market-data contracts'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    """


def _postgres_relaxed_market_data_function_sql() -> str:
    """Restore revision 0010 semantics on downgrade."""
    return f"""
    CREATE OR REPLACE FUNCTION {POSTGRES_MARKET_DATA_FUNCTION}()
    RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
    DECLARE resolved_instrument_type text; expected_unit text; expected_scale numeric;
    BEGIN
        {_mutex_sql()}
        SELECT lower(instrument_type) INTO resolved_instrument_type
        FROM instrument WHERE instrument_id = NEW.instrument_id;
        IF resolved_instrument_type IS NULL THEN
            RAISE EXCEPTION 'instrument market-data price contract requires an existing instrument'
                USING ERRCODE = '23514';
        END IF;
        IF NOT (
            (NEW.metric_family = 'price' AND NEW.quote_basis IN ({_quoted(PRICE_QUOTE_BASES)}))
            OR (NEW.metric_family = 'nav' AND NEW.quote_basis IN ({_quoted(NAV_QUOTE_BASES)}))
            OR (NEW.metric_family = 'fx' AND NEW.quote_basis = 'spot')
        ) THEN
            RAISE EXCEPTION 'instrument market-data quote identity is not canonical'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.quote_basis = 'accrued_interest' AND resolved_instrument_type <> 'bond' THEN
            RAISE EXCEPTION 'accrued_interest is valid only for bond price data'
                USING ERRCODE = '23514';
        END IF;
        IF resolved_instrument_type = 'bond' AND NEW.metric_family = 'price' THEN
            expected_unit := 'percent_of_par'; expected_scale := 0.01;
        ELSIF resolved_instrument_type = 'fx' OR NEW.metric_family = 'fx'
              OR NEW.quote_basis = 'spot' THEN
            expected_unit := 'rate'; expected_scale := 1;
        ELSE expected_unit := 'per_unit'; expected_scale := 1;
        END IF;
        IF NEW.price_unit <> expected_unit OR NEW.price_scale <> expected_scale THEN
            RAISE EXCEPTION 'market-data price contract is not canonical for its instrument/quote identity'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END; $$;
    """


def _postgres_relaxed_instrument_function_sql() -> str:
    return f"""
    CREATE OR REPLACE FUNCTION {POSTGRES_INSTRUMENT_FUNCTION}()
    RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
    BEGIN
        {_mutex_sql()}
        IF EXISTS (
            SELECT 1 FROM instrument_market_data AS market_data
            WHERE market_data.instrument_id = NEW.instrument_id
              AND (
                  (market_data.quote_basis = 'accrued_interest'
                   AND lower(NEW.instrument_type) <> 'bond')
                  OR market_data.price_unit <> CASE
                      WHEN lower(NEW.instrument_type) = 'bond'
                           AND market_data.metric_family = 'price' THEN 'percent_of_par'
                      WHEN lower(NEW.instrument_type) = 'fx'
                           OR market_data.metric_family = 'fx'
                           OR market_data.quote_basis = 'spot' THEN 'rate'
                      ELSE 'per_unit' END
                  OR market_data.price_scale <> CASE
                      WHEN lower(NEW.instrument_type) = 'bond'
                           AND market_data.metric_family = 'price' THEN 0.01
                      ELSE 1 END
              )
        ) THEN
            RAISE EXCEPTION 'instrument_type update conflicts with existing market-data price contracts'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END; $$;
    """


def _sqlite_positive_decimal_sql(column: str) -> str:
    """Return a SQLite-only lexical check for a positive finite decimal.

    SQLite's numeric casts accept prefixes such as ``1abc`` and ``1e`` as the
    number 1, so a cast cannot enforce the observation contract.  This checks
    the decimal grammar first and determines positivity from the mantissa,
    without losing very large or very small finite decimals through REAL.
    """

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


def _sqlite_market_data_trigger_sql(*, operation: str, strict: bool) -> str:
    trigger_name = f"{MARKET_DATA_TRIGGER_NAME}_{operation.lower()}"
    extra_contract = ""
    fx_rate_condition = (
        "lower(instrument.instrument_type) = 'fx' "
        "OR NEW.metric_family = 'fx' OR NEW.quote_basis = 'spot'"
    )
    if strict:
        fx_rate_condition = "instrument.instrument_type = 'fx'"
        positive_decimal_sql = _sqlite_positive_decimal_sql("NEW.value")
        extra_contract = f"""
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
        """
    return f"""
    CREATE TRIGGER {trigger_name}
    BEFORE {operation} ON instrument_market_data
    FOR EACH ROW
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM instrument
            WHERE instrument.instrument_id = NEW.instrument_id
              AND (
                  (NEW.metric_family = 'price' AND NEW.quote_basis IN ({_quoted(PRICE_QUOTE_BASES)}))
                  OR (NEW.metric_family = 'nav' AND NEW.quote_basis IN ({_quoted(NAV_QUOTE_BASES)}))
                  OR (NEW.metric_family = 'fx' AND NEW.quote_basis = 'spot')
              )
              AND NOT (NEW.quote_basis = 'accrued_interest'
                       AND lower(instrument.instrument_type) <> 'bond')
              {extra_contract}
              AND NEW.price_unit = CASE
                  WHEN lower(instrument.instrument_type) = 'bond'
                       AND NEW.metric_family = 'price' THEN 'percent_of_par'
                  WHEN {fx_rate_condition} THEN 'rate'
                  ELSE 'per_unit' END
              AND NEW.price_scale = CASE
                  WHEN lower(instrument.instrument_type) = 'bond'
                       AND NEW.metric_family = 'price' THEN 0.01
                  ELSE 1 END
        ) THEN RAISE(ABORT, 'instrument market-data price contract is not canonical') END;
    END
    """


def _sqlite_instrument_trigger_sql(*, operation: str, strict: bool) -> str:
    trigger_name = (
        f"{INSTRUMENT_TRIGGER_NAME}_{operation.lower()}"
        if operation == "INSERT"
        else INSTRUMENT_TRIGGER_NAME
    )
    if not strict:
        return f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE OF instrument_type ON instrument
        FOR EACH ROW
        WHEN lower(OLD.instrument_type) <> lower(NEW.instrument_type)
             AND EXISTS (
                 SELECT 1 FROM instrument_market_data AS market_data
                 WHERE market_data.instrument_id = NEW.instrument_id
                   AND (
                       (market_data.quote_basis = 'accrued_interest'
                        AND lower(NEW.instrument_type) <> 'bond')
                       OR market_data.price_unit <> CASE
                           WHEN lower(NEW.instrument_type) = 'bond'
                                AND market_data.metric_family = 'price' THEN 'percent_of_par'
                           WHEN lower(NEW.instrument_type) = 'fx'
                                OR market_data.metric_family = 'fx'
                                OR market_data.quote_basis = 'spot' THEN 'rate'
                           ELSE 'per_unit' END
                       OR market_data.price_scale <> CASE
                           WHEN lower(NEW.instrument_type) = 'bond'
                                AND market_data.metric_family = 'price' THEN 0.01
                           ELSE 1 END
                   )
             )
        BEGIN
            SELECT RAISE(ABORT, 'instrument_type update conflicts with market-data price contracts');
        END
        """

    update_clause = "UPDATE OF instrument_type, currency" if operation == "UPDATE" else "INSERT"
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
                  (market_data.metric_family = 'fx' AND market_data.quote_basis = 'spot'))
              OR (market_data.quote_basis = 'accrued_interest'
                  AND NEW.instrument_type <> 'bond')
              OR market_data.price_unit <> CASE
                  WHEN NEW.instrument_type = 'bond' AND market_data.metric_family = 'price'
                      THEN 'percent_of_par'
                  WHEN NEW.instrument_type = 'fx' THEN 'rate'
                  ELSE 'per_unit' END
              OR market_data.price_scale <> CASE
                  WHEN NEW.instrument_type = 'bond' AND market_data.metric_family = 'price'
                      THEN 0.01
                  ELSE 1 END
          )
    )
    BEGIN
        SELECT RAISE(ABORT, 'instrument_type update conflicts with canonical market-data contract');
    END
    """


def _drop_contract_triggers(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(f"DROP TRIGGER IF EXISTS {MARKET_DATA_TRIGGER_NAME} ON instrument_market_data")
        )
        connection.execute(
            sa.text(f"DROP TRIGGER IF EXISTS {INSTRUMENT_TRIGGER_NAME} ON instrument")
        )
        return
    if connection.dialect.name == "sqlite":
        for trigger_name in (
            f"{MARKET_DATA_TRIGGER_NAME}_insert",
            f"{MARKET_DATA_TRIGGER_NAME}_update",
            INSTRUMENT_TRIGGER_NAME,
            f"{INSTRUMENT_TRIGGER_NAME}_insert",
        ):
            connection.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger_name}"))


def _create_strict_triggers(connection: sa.Connection) -> None:
    _drop_contract_triggers(connection)
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
        connection.execute(sa.text(_sqlite_market_data_trigger_sql(operation="INSERT", strict=True)))
        connection.execute(sa.text(_sqlite_market_data_trigger_sql(operation="UPDATE", strict=True)))
        connection.execute(sa.text(_sqlite_instrument_trigger_sql(operation="INSERT", strict=True)))
        connection.execute(sa.text(_sqlite_instrument_trigger_sql(operation="UPDATE", strict=True)))


def _restore_0010_triggers(connection: sa.Connection) -> None:
    _drop_contract_triggers(connection)
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text(_postgres_relaxed_market_data_function_sql()))
        connection.execute(sa.text(_postgres_relaxed_instrument_function_sql()))
        connection.execute(
            sa.text(
                f"""
                CREATE TRIGGER {MARKET_DATA_TRIGGER_NAME}
                BEFORE INSERT OR UPDATE OF instrument_id, metric_family, quote_basis,
                    price_unit, price_scale
                ON instrument_market_data
                FOR EACH ROW EXECUTE FUNCTION {POSTGRES_MARKET_DATA_FUNCTION}()
                """
            )
        )
        connection.execute(
            sa.text(
                f"""
                CREATE TRIGGER {INSTRUMENT_TRIGGER_NAME}
                BEFORE UPDATE OF instrument_type ON instrument
                FOR EACH ROW WHEN (OLD.instrument_type IS DISTINCT FROM NEW.instrument_type)
                EXECUTE FUNCTION {POSTGRES_INSTRUMENT_FUNCTION}()
                """
            )
        )
    elif connection.dialect.name == "sqlite":
        connection.execute(sa.text(_sqlite_market_data_trigger_sql(operation="INSERT", strict=False)))
        connection.execute(sa.text(_sqlite_market_data_trigger_sql(operation="UPDATE", strict=False)))
        connection.execute(sa.text(_sqlite_instrument_trigger_sql(operation="UPDATE", strict=False)))


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE instrument_market_data, instrument IN ACCESS EXCLUSIVE MODE")
        )

    violations = _preflight_violations(connection)
    if violations:
        raise RuntimeError(
            "Cannot apply 20260715_0011: existing registry rows violate the canonical "
            "market-data observation contract; no data was changed. Samples: "
            + "; ".join(violations)
        )

    _normalize_quote_selection_policy_rows(connection)
    _normalize_refresh_status_rows(connection)
    _drop_contract_triggers(connection)
    with op.batch_alter_table("instrument") as batch_op:
        batch_op.create_check_constraint(
            INSTRUMENT_TYPE_CHECK_NAME,
            f"instrument_type IN ({_quoted(INSTRUMENT_TYPES)})",
        )
        batch_op.create_check_constraint(
            INSTRUMENT_CURRENCY_CHECK_NAME,
            "currency = upper(trim(currency)) AND length(currency) BETWEEN 1 AND 8",
        )
    with op.batch_alter_table("instrument_market_data") as batch_op:
        batch_op.create_check_constraint(
            MARKET_DATA_STATUS_CHECK_NAME,
            f"status IN ({_quoted(DATA_STATUSES)})",
        )
    _create_strict_triggers(connection)


def downgrade() -> None:
    """Restore the 0010 schema and trigger contract, not pre-0011 JSON values.

    Quote-policy materialization and refresh-cursor promotion are intentional
    one-time data normalization.  Their prior missing/implicit state cannot be
    reconstructed without inventing history, so an exact data rollback must
    restore a backup taken before the 0011 upgrade.
    """

    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE instrument_market_data, instrument IN ACCESS EXCLUSIVE MODE")
        )
    _drop_contract_triggers(connection)
    with op.batch_alter_table("instrument_market_data") as batch_op:
        batch_op.drop_constraint(MARKET_DATA_STATUS_CHECK_NAME, type_="check")
    with op.batch_alter_table("instrument") as batch_op:
        batch_op.drop_constraint(INSTRUMENT_CURRENCY_CHECK_NAME, type_="check")
        batch_op.drop_constraint(INSTRUMENT_TYPE_CHECK_NAME, type_="check")
    _restore_0010_triggers(connection)
