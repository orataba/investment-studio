"""Exact numeric and canonicalization contract for published calculations.

Finite accounting facts use the context-independent ``exact_decimal_*``
helpers.  Method-owned analytics use the isolated precision-50 HALF_EVEN
helpers, including every wealth-chain step.  Callers use
:func:`quantize_decimal` only at a versioned fact or publication boundary.

Canonical JSON encodes ``Decimal`` values as strings.  This is deliberate:
JSON numbers cannot carry the exact decimal representation required by the
calculation publication contract.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    FloatOperation,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    localcontext,
)
from hashlib import sha256
import json
from types import MappingProxyType
from typing import TypeAlias


CALCULATION_DECIMAL_PRECISION = 50
CALCULATION_DECIMAL_ROUNDING = ROUND_HALF_EVEN

# Portfolio Daily method values have the logical domain NUMERIC(132,100): 32
# integer digits and 100 fractional digits.  PostgreSQL stores these columns as
# physical unbounded NUMERIC plus explicit CHECKs because a NUMERIC(p,s) typmod
# would round before a CHECK can reject excess fractional digits.  This matches
# the integer capacity of the published NUMERIC(50,18) rate boundary.  Scale
# 100 is a technical replay guard, not a claim that business displays need 100
# decimal places: it can retain all 50 method digits when the leading digit is
# as small as 1E-50, without adding a second storage-rounding rule.  The exact
# product/difference evidence has logical domain NUMERIC(232,200).  Values
# outside either logical domain fail closed at both application and DB edges.
METHOD_DECIMAL_STORAGE_PRECISION = 132
METHOD_DECIMAL_STORAGE_SCALE = 100
METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION = 232
METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE = 200

# Portfolio Daily persists semantic decimal domains with physical unbounded
# PostgreSQL NUMERIC plus fail-closed exact-fit checks.  These are storage
# capacity guards, not additional business rounding boundaries: values outside
# a domain are rejected rather than quantized.  Raw excluded/rejected provider
# observations remain unbounded and are gated only if they are adopted.
SOURCE_PRICE_STORAGE_PRECISION = 38
SOURCE_PRICE_STORAGE_SCALE = 12
SOURCE_RATE_STORAGE_PRECISION = 38
SOURCE_RATE_STORAGE_SCALE = 18
EXACT_QUANTITY_STORAGE_PRECISION = 50
EXACT_QUANTITY_STORAGE_SCALE = 12
DERIVED_RATE_STORAGE_PRECISION = METHOD_DECIMAL_STORAGE_PRECISION
DERIVED_RATE_STORAGE_SCALE = METHOD_DECIMAL_STORAGE_SCALE
ACCOUNTING_EVIDENCE_STORAGE_PRECISION = 242
ACCOUNTING_EVIDENCE_STORAGE_SCALE = 200
METHOD_EVIDENCE_STORAGE_PRECISION = METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION
METHOD_EVIDENCE_STORAGE_SCALE = METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE

QUANTITY_SCALE = 12
PRICE_SCALE = 12
AMOUNT_SCALE = 8
FEE_SCALE = 8
TAX_SCALE = 8
FX_RATE_SCALE = 18
RATIO_SCALE = 18

FACT_FIELD_SCALES: Mapping[str, int] = MappingProxyType(
    {
        "quantity": QUANTITY_SCALE,
        "price": PRICE_SCALE,
        "amount": AMOUNT_SCALE,
        "fee": FEE_SCALE,
        "tax": TAX_SCALE,
        "fx_rate": FX_RATE_SCALE,
    }
)

# Canonical Portfolio Daily output-schema field names.  This is intentionally
# explicit rather than suffix-based: a new financial field cannot silently
# inherit a scale merely because its name happens to end in ``_value`` or
# ``_rate``.  Adding a field is therefore an output-schema version decision.
PUBLICATION_FIELD_SCALES: Mapping[str, int] = MappingProxyType(
    {
        "nav": AMOUNT_SCALE,
        "beginning_nav": AMOUNT_SCALE,
        "ending_nav": AMOUNT_SCALE,
        "pending_settlement": AMOUNT_SCALE,
        "position_market_value": AMOUNT_SCALE,
        "market_value": AMOUNT_SCALE,
        "market_value_base": AMOUNT_SCALE,
        "open_cost_basis": AMOUNT_SCALE,
        "cost_basis": AMOUNT_SCALE,
        "cost_basis_base": AMOUNT_SCALE,
        "absolute_change": AMOUNT_SCALE,
        "delta": AMOUNT_SCALE,
        "realized_pnl": AMOUNT_SCALE,
        "realized_pnl_base": AMOUNT_SCALE,
        "unrealized_pnl": AMOUNT_SCALE,
        "unrealized_pnl_base": AMOUNT_SCALE,
        "total_pnl": AMOUNT_SCALE,
        "total_pnl_base": AMOUNT_SCALE,
        "cash_balance": AMOUNT_SCALE,
        "cash_balance_base": AMOUNT_SCALE,
        "external_flow_in": AMOUNT_SCALE,
        "external_flow_out": AMOUNT_SCALE,
        "net_external_flow": AMOUNT_SCALE,
        "income": AMOUNT_SCALE,
        "income_base": AMOUNT_SCALE,
        "income_cash_amount": AMOUNT_SCALE,
        "expense_cash_amount": AMOUNT_SCALE,
        "fees": AMOUNT_SCALE,
        "fees_base": AMOUNT_SCALE,
        "taxes": AMOUNT_SCALE,
        "taxes_base": AMOUNT_SCALE,
        "cash_currency_gains": AMOUNT_SCALE,
        "instrument_currency_gains": AMOUNT_SCALE,
        "return_of_capital_amount": AMOUNT_SCALE,
        "external_cash_in": AMOUNT_SCALE,
        "external_cash_out": AMOUNT_SCALE,
        "net_external_inflow": AMOUNT_SCALE,
        "beginning_value": AMOUNT_SCALE,
        "ending_value": AMOUNT_SCALE,
        "capital_flow_in": AMOUNT_SCALE,
        "capital_flow_out": AMOUNT_SCALE,
        "period_pnl": AMOUNT_SCALE,
        "opening_nav": AMOUNT_SCALE,
        "closing_nav": AMOUNT_SCALE,
        "settled_cash": AMOUNT_SCALE,
        "pending_receivable": AMOUNT_SCALE,
        "pending_payable": AMOUNT_SCALE,
        "accrual_receivable": AMOUNT_SCALE,
        "accrual_payable": AMOUNT_SCALE,
        "economic_pnl": AMOUNT_SCALE,
        "realized_pnl_daily": AMOUNT_SCALE,
        "unrealized_pnl_beginning": AMOUNT_SCALE,
        "unrealized_pnl_ending": AMOUNT_SCALE,
        "unrealized_pnl_change": AMOUNT_SCALE,
        "gross_income_daily": AMOUNT_SCALE,
        "return_of_capital_daily": AMOUNT_SCALE,
        "capitalized_fee_daily": AMOUNT_SCALE,
        "capitalized_tax_daily": AMOUNT_SCALE,
        "expensed_fee_daily": AMOUNT_SCALE,
        "expensed_tax_daily": AMOUNT_SCALE,
        "disposal_fee_in_realized_daily": AMOUNT_SCALE,
        "disposal_tax_in_realized_daily": AMOUNT_SCALE,
        "local_price_effect_daily": AMOUNT_SCALE,
        "position_fx_effect_daily": AMOUNT_SCALE,
        "cash_fx_effect_daily": AMOUNT_SCALE,
        "pending_fx_effect_daily": AMOUNT_SCALE,
        "accrual_fx_effect_daily": AMOUNT_SCALE,
        "fx_conversion_effect_daily": AMOUNT_SCALE,
        "reliable_anchor_nav": AMOUNT_SCALE,
        "historical_base_cost": AMOUNT_SCALE,
        "released_local_cost": AMOUNT_SCALE,
        "released_historical_base_cost": AMOUNT_SCALE,
        "allocated_local_net_proceeds": AMOUNT_SCALE,
        "allocated_base_net_proceeds": AMOUNT_SCALE,
        "rounding_adjustment_base": AMOUNT_SCALE,
        "nav_rounding_adjustment": AMOUNT_SCALE,
        "pnl_rounding_adjustment": AMOUNT_SCALE,
        "pnl_component_rounding_adjustment": AMOUNT_SCALE,
        "position_attribution_rounding_adjustment": AMOUNT_SCALE,
        "quantity": QUANTITY_SCALE,
        "price": PRICE_SCALE,
        "last_price": PRICE_SCALE,
        "fx_rate": FX_RATE_SCALE,
        "subperiod_twr_published": RATIO_SCALE,
        "cumulative_twr_published": RATIO_SCALE,
        "drawdown_published": RATIO_SCALE,
        "weight": RATIO_SCALE,
        "portfolio_weight": RATIO_SCALE,
        "daily_contribution": RATIO_SCALE,
        "wealth_index_published": RATIO_SCALE,
        "peak_wealth_index_published": RATIO_SCALE,
    }
)

CanonicalJsonScalar: TypeAlias = None | bool | int | str
CanonicalJsonValue: TypeAlias = (
    CanonicalJsonScalar | list["CanonicalJsonValue"] | dict[str, "CanonicalJsonValue"]
)


class CalculationNumericError(ValueError):
    """A value violates the exact calculation publication contract."""


class BalancedRoundingError(CalculationNumericError):
    """Exact rows cannot be balanced under the one-quantum constraint."""


@dataclass(frozen=True, slots=True)
class BalancedRoundingInputRow:
    natural_key: tuple[str, ...]
    exact_value: Decimal

    def __post_init__(self) -> None:
        if (
            not isinstance(self.natural_key, tuple)
            or not self.natural_key
            or any(not isinstance(part, str) or not part for part in self.natural_key)
        ):
            raise BalancedRoundingError(
                "natural_key must be a non-empty tuple of non-empty strings"
            )
        require_decimal(self.exact_value, field_name="exact_value")


@dataclass(frozen=True, slots=True)
class BalancedRoundingOutputRow:
    natural_key: tuple[str, ...]
    exact_value: Decimal
    independently_rounded_value: Decimal
    published_value: Decimal
    rounding_adjustment: Decimal
    discarded_remainder: Decimal


@dataclass(frozen=True, slots=True)
class BalancedRoundingResult:
    scale: int
    quantum: Decimal
    exact_aggregate: Decimal
    target_aggregate: Decimal
    rows: tuple[BalancedRoundingOutputRow, ...]

    def canonical_payload(self) -> dict[str, object]:
        """Return hash input including every explicit balancing adjustment."""

        return {
            "scale": self.scale,
            "exact_aggregate": self.exact_aggregate,
            "target_aggregate": self.target_aggregate,
            "rows": [
                {
                    "natural_key": list(row.natural_key),
                    "published_value": row.published_value,
                    "rounding_adjustment": row.rounding_adjustment,
                }
                for row in self.rows
            ],
        }


def _build_context() -> Context:
    context = Context(
        prec=CALCULATION_DECIMAL_PRECISION,
        rounding=CALCULATION_DECIMAL_ROUNDING,
    )
    required_traps = {
        FloatOperation,
        InvalidOperation,
        DivisionByZero,
        Overflow,
    }
    for signal in context.traps:
        context.traps[signal] = signal in required_traps
    context.clear_flags()
    return context


_CALCULATION_CONTEXT = _build_context()


@contextmanager
def calculation_context() -> Iterator[Context]:
    """Yield a fresh, isolated context for exact financial arithmetic.

    The template is never exposed, so flags and caller mutations cannot leak
    into another run.  The process-wide decimal context is also left intact.
    """

    with localcontext(_CALCULATION_CONTEXT) as context:
        context.clear_flags()
        yield context


def _method_result(value: Decimal, *, field_name: str) -> Decimal:
    resolved = require_decimal(value, field_name=field_name)
    return resolved.copy_abs() if resolved.is_zero() else resolved


def method_decimal_add(left: Decimal, right: Decimal) -> Decimal:
    """Add two method operands at precision 50, independent of ambient state."""

    resolved_left = require_decimal(left, field_name="method_add_left")
    resolved_right = require_decimal(right, field_name="method_add_right")
    with calculation_context() as context:
        result = context.add(resolved_left, resolved_right)
    return _method_result(result, field_name="method_add_result")


def method_decimal_subtract(left: Decimal, right: Decimal) -> Decimal:
    """Subtract two method operands at precision 50 HALF_EVEN."""

    resolved_left = require_decimal(left, field_name="method_subtract_left")
    resolved_right = require_decimal(right, field_name="method_subtract_right")
    with calculation_context() as context:
        result = context.subtract(resolved_left, resolved_right)
    return _method_result(result, field_name="method_subtract_result")


def method_decimal_multiply(left: Decimal, right: Decimal) -> Decimal:
    """Multiply two method operands at precision 50 HALF_EVEN."""

    resolved_left = require_decimal(left, field_name="method_multiply_left")
    resolved_right = require_decimal(right, field_name="method_multiply_right")
    with calculation_context() as context:
        result = context.multiply(resolved_left, resolved_right)
    return _method_result(result, field_name="method_multiply_result")


def method_decimal_round(value: Decimal) -> Decimal:
    """Round one finite value to the methodology's 50 significant digits."""

    resolved = require_decimal(value, field_name="method_round_value")
    with calculation_context() as context:
        result = context.plus(resolved)
    return _method_result(result, field_name="method_round_result")


def method_decimal_divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Divide two method operands at precision 50 HALF_EVEN."""

    resolved_numerator = require_decimal(
        numerator,
        field_name="method_divide_numerator",
    )
    resolved_denominator = require_decimal(
        denominator,
        field_name="method_divide_denominator",
    )
    with calculation_context() as context:
        result = context.divide(resolved_numerator, resolved_denominator)
    return _method_result(result, field_name="method_divide_result")


def require_exact_numeric_typmod(
    value: Decimal,
    *,
    precision: int,
    scale: int,
    field_name: str = "value",
) -> Decimal:
    """Require that ``value`` fits a NUMERIC typmod without database rounding.

    This is a fail-closed persistence check, not another rounding boundary.
    Trailing decimal zeroes are removed for the representability test because
    PostgreSQL may restore the declared display scale on readback.
    """

    resolved = require_decimal(value, field_name=field_name)
    resolved_precision = _validate_scale(precision)
    resolved_scale = _validate_scale(scale)
    if resolved_precision <= resolved_scale:
        raise CalculationNumericError(
            "precision must exceed scale for a signed financial method value"
        )
    if resolved.is_zero():
        return resolved.copy_abs()
    sign, digits, exponent = resolved.as_tuple()
    del sign
    significant_digits = list(digits)
    while significant_digits and significant_digits[-1] == 0:
        significant_digits.pop()
        exponent += 1
    digit_count = len(significant_digits)
    fractional_digits = max(-exponent, 0)
    integer_digits = max(digit_count + exponent, 0)
    if (
        fractional_digits > resolved_scale
        or integer_digits > resolved_precision - resolved_scale
    ):
        raise CalculationNumericError(
            f"{field_name} cannot be stored exactly as "
            f"NUMERIC({resolved_precision},{resolved_scale})"
        )
    return resolved


def require_method_decimal(
    value: Decimal,
    *,
    field_name: str = "value",
) -> Decimal:
    """Require one canonical method50 value inside the storage domain."""

    resolved = require_exact_numeric_typmod(
        value,
        precision=METHOD_DECIMAL_STORAGE_PRECISION,
        scale=METHOD_DECIMAL_STORAGE_SCALE,
        field_name=field_name,
    )
    if method_decimal_round(resolved) != resolved:
        raise CalculationNumericError(
            f"{field_name} exceeds {CALCULATION_DECIMAL_PRECISION} "
            "significant method digits"
        )
    return resolved


def _validate_scale(scale: object) -> int:
    if isinstance(scale, bool) or not isinstance(scale, int):
        raise CalculationNumericError("scale must be a non-negative integer")
    if scale < 0:
        raise CalculationNumericError("scale must be a non-negative integer")
    return scale


def require_decimal(value: object, *, field_name: str = "value") -> Decimal:
    """Return a finite, non-negative-zero Decimal or fail closed.

    Integers and strings are intentionally not coerced here.  Requiring the
    caller to construct ``Decimal`` at the ingestion edge prevents accidental
    acceptance of a binary float through a generic conversion path.
    """

    if isinstance(value, float):
        raise CalculationNumericError(f"{field_name} must not be a float")
    if not isinstance(value, Decimal):
        raise CalculationNumericError(f"{field_name} must be a Decimal")
    if not value.is_finite():
        raise CalculationNumericError(f"{field_name} must be finite")
    if value.is_zero() and value.is_signed():
        raise CalculationNumericError(f"{field_name} must not be negative zero")
    return value


def _decimal_coefficient_and_exponent(value: Decimal) -> tuple[int, int]:
    """Return a finite Decimal as an integer coefficient and base-ten exponent."""

    sign, digits, exponent = value.as_tuple()
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    return (-coefficient if sign else coefficient), exponent


def _arithmetic_decimal(value: object, *, field_name: str) -> Decimal:
    """Validate an arithmetic operand and canonicalize internally-created -0."""

    if isinstance(value, Decimal) and value.is_finite() and value.is_zero():
        return value.copy_abs()
    return require_decimal(value, field_name=field_name)


def _decimal_from_coefficient(coefficient: int, exponent: int) -> Decimal:
    """Build a Decimal without consulting the ambient Decimal context."""

    digits = tuple(int(character) for character in str(abs(coefficient)))
    return Decimal((1 if coefficient < 0 else 0, digits, exponent))


def exact_decimal_product(*values: Decimal) -> Decimal:
    """Multiply finite base-ten values exactly, independent of context.

    Multiplication of finite decimals is terminating.  Applying the generic
    precision-50 context to it would discard real digits when, for example, a
    precision-50 lot allocation is translated by an 18-decimal FX rate.  The
    explicit calculation context remains the policy for non-terminating
    operations such as division; finite multiplication never needs rounding.
    """

    coefficient = 1
    exponent = 0
    for index, value in enumerate(values):
        resolved = _arithmetic_decimal(
            value,
            field_name=f"product_value_{index}",
        )
        item_coefficient, item_exponent = _decimal_coefficient_and_exponent(resolved)
        coefficient *= item_coefficient
        exponent += item_exponent
    return _decimal_from_coefficient(coefficient, exponent)


def exact_decimal_sum(values: tuple[Decimal, ...]) -> Decimal:
    """Add finite base-ten values exactly by aligning their exponents."""

    if not isinstance(values, tuple):
        raise CalculationNumericError("exact sum values must be an immutable tuple")
    if not values:
        return Decimal("0")
    resolved = tuple(
        _arithmetic_decimal(value, field_name=f"sum_value_{index}")
        for index, value in enumerate(values)
    )
    parts = tuple(_decimal_coefficient_and_exponent(value) for value in resolved)
    exponent = min(item_exponent for _, item_exponent in parts)
    coefficient = sum(
        item_coefficient * (10 ** (item_exponent - exponent))
        for item_coefficient, item_exponent in parts
    )
    return _decimal_from_coefficient(coefficient, exponent)


def exact_decimal_negate(value: Decimal) -> Decimal:
    """Negate a finite decimal without ambient-context rounding or signed zero."""

    resolved = _arithmetic_decimal(value, field_name="negate_value")
    coefficient, exponent = _decimal_coefficient_and_exponent(resolved)
    return _decimal_from_coefficient(-coefficient, exponent)


def exact_decimal_subtract(left: Decimal, right: Decimal) -> Decimal:
    """Subtract finite decimals exactly without creating signed zero."""

    resolved_left = _arithmetic_decimal(left, field_name="subtract_left")
    resolved_right = _arithmetic_decimal(right, field_name="subtract_right")
    left_coefficient, left_exponent = _decimal_coefficient_and_exponent(resolved_left)
    right_coefficient, right_exponent = _decimal_coefficient_and_exponent(
        resolved_right
    )
    exponent = min(left_exponent, right_exponent)
    coefficient = left_coefficient * (
        10 ** (left_exponent - exponent)
    ) - right_coefficient * (10 ** (right_exponent - exponent))
    return _decimal_from_coefficient(coefficient, exponent)


def quantum_for_scale(*, scale: int) -> Decimal:
    """Return the exact base-10 quantum for an explicit field scale."""

    resolved_scale = _validate_scale(scale)
    # Tuple construction is exact and context-independent; ``scaleb`` would
    # otherwise consult the process-wide decimal context before we enter the
    # calculation context.
    return Decimal((0, (1,), -resolved_scale))


def quantize_decimal(
    value: Decimal,
    *,
    scale: int,
    field_name: str = "value",
) -> Decimal:
    """HALF_EVEN-quantize a Decimal at an explicit publication boundary."""

    resolved = require_decimal(value, field_name=field_name)
    quantum = quantum_for_scale(scale=scale)
    try:
        with calculation_context():
            quantized = resolved.quantize(quantum)
    except (InvalidOperation, Overflow) as exc:
        raise CalculationNumericError(
            f"{field_name} cannot be represented at scale {scale} "
            f"with precision {CALCULATION_DECIMAL_PRECISION}"
        ) from exc
    # A small negative number can legitimately round to zero.  Published zero
    # has one representation and never carries a negative sign.
    return quantized.copy_abs() if quantized.is_zero() else quantized


def canonical_decimal(value: Decimal, *, field_name: str = "value") -> str:
    """Return the one plain-string representation of an exact Decimal.

    Scientific notation and insignificant fractional zeroes are removed.
    The sign of non-zero values is retained.
    """

    resolved = require_decimal(value, field_name=field_name)
    rendered = format(resolved, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def canonical_scaled_decimal(
    value: Decimal,
    *,
    scale: int,
    field_name: str = "value",
) -> str:
    """Quantize at an explicit boundary and return its canonical string."""

    return canonical_decimal(
        quantize_decimal(value, scale=scale, field_name=field_name),
        field_name=field_name,
    )


def balanced_round_rows(
    rows: tuple[BalancedRoundingInputRow, ...],
    *,
    exact_aggregate: Decimal,
    scale: int,
) -> BalancedRoundingResult:
    """HALF_EVEN round rows and deterministically balance to the aggregate.

    Independent rounding happens first.  Any aggregate difference is allocated
    by discarded remainder and then by stable natural key.  No row can receive
    more than one signed quantum, and exact row values must already close to
    the supplied exact aggregate; rounding never hides an accounting break.
    """

    if not isinstance(rows, tuple):
        raise BalancedRoundingError("rows must be an immutable tuple")
    if any(not isinstance(row, BalancedRoundingInputRow) for row in rows):
        raise BalancedRoundingError("rows must contain BalancedRoundingInputRow values")
    keys = [row.natural_key for row in rows]
    if len(set(keys)) != len(keys):
        raise BalancedRoundingError("natural keys must be unique")
    aggregate = require_decimal(
        exact_aggregate,
        field_name="exact_aggregate",
    )
    resolved_scale = _validate_scale(scale)
    quantum = quantum_for_scale(scale=resolved_scale)
    row_exact_total = exact_decimal_sum(tuple(row.exact_value for row in rows))
    if row_exact_total != aggregate:
        raise BalancedRoundingError("exact rows must close exactly to exact_aggregate")

    target = quantize_decimal(
        aggregate,
        scale=resolved_scale,
        field_name="exact_aggregate",
    )
    independent: list[tuple[BalancedRoundingInputRow, Decimal, Decimal]] = []
    for row in rows:
        rounded = quantize_decimal(
            row.exact_value,
            scale=resolved_scale,
            field_name=f"row[{row.natural_key!r}]",
        )
        independent.append(
            (row, rounded, exact_decimal_subtract(row.exact_value, rounded))
        )
    independent_total = exact_decimal_sum(
        tuple(rounded for _, rounded, _ in independent)
    )
    residual = exact_decimal_subtract(target, independent_total)
    with calculation_context():
        adjustment_units = residual / quantum

    integral_units = adjustment_units.to_integral_value()
    if adjustment_units != integral_units:
        raise BalancedRoundingError(
            "rounding residual is not an integral number of quanta"
        )
    required_count = abs(int(integral_units))
    if required_count > len(independent):
        raise BalancedRoundingError(
            "aggregate cannot be balanced with at most one quantum per row"
        )

    direction = 1 if adjustment_units > 0 else -1 if adjustment_units < 0 else 0
    if direction > 0:
        ranked = sorted(
            independent,
            key=lambda item: (item[2].copy_negate(), item[0].natural_key),
        )
    elif direction < 0:
        ranked = sorted(
            independent,
            key=lambda item: (item[2], item[0].natural_key),
        )
    else:
        ranked = []
    selected = {row.natural_key for row, _, _ in ranked[:required_count]}

    output_rows: list[BalancedRoundingOutputRow] = []
    for row, rounded, remainder in independent:
        adjustment = (
            exact_decimal_product(Decimal(direction), quantum)
            if row.natural_key in selected
            else Decimal("0")
        )
        output_rows.append(
            BalancedRoundingOutputRow(
                natural_key=row.natural_key,
                exact_value=row.exact_value,
                independently_rounded_value=rounded,
                published_value=exact_decimal_sum((rounded, adjustment)),
                rounding_adjustment=adjustment,
                discarded_remainder=remainder,
            )
        )
    output_rows.sort(key=lambda row: row.natural_key)
    published_total = exact_decimal_sum(
        tuple(row.published_value for row in output_rows)
    )
    if published_total != target or any(
        row.rounding_adjustment.copy_abs() > quantum for row in output_rows
    ):
        raise BalancedRoundingError("balanced rounding invariant failed")
    return BalancedRoundingResult(
        scale=resolved_scale,
        quantum=quantum,
        exact_aggregate=aggregate,
        target_aggregate=target,
        rows=tuple(output_rows),
    )


def balanced_rounding_sha256_hex(result: BalancedRoundingResult) -> str:
    """Hash published values together with explicit rounding adjustments."""

    if not isinstance(result, BalancedRoundingResult):
        raise BalancedRoundingError("result must be a BalancedRoundingResult")
    return canonical_sha256_hex(result.canonical_payload())


def _canonicalize_json(value: object, *, path: str) -> CanonicalJsonValue:
    if value is None or isinstance(value, str) or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return canonical_decimal(value, field_name=path)
    if isinstance(value, float):
        raise CalculationNumericError(f"{path} must not contain a float")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise CalculationNumericError(f"{path} object keys must be strings")
        return {
            key: _canonicalize_json(value[key], path=f"{path}.{key}")
            for key in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [
            _canonicalize_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise CalculationNumericError(
        f"{path} contains unsupported canonical JSON type {type(value).__name__}"
    )


def canonical_json_value(value: object) -> CanonicalJsonValue:
    """Normalize supported input into the canonical JSON value model."""

    return _canonicalize_json(value, path="$")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize canonical JSON as deterministic UTF-8 bytes."""

    normalized = canonical_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256_hex(value: object) -> str:
    """Return the 64-character lowercase digest stored by the registry."""

    return sha256(canonical_json_bytes(value)).hexdigest()


def canonical_hash(value: object) -> str:
    """Return a display/audit SHA-256 envelope for canonical JSON.

    Registry ``CHAR(64)`` columns must use :func:`canonical_sha256_hex` rather
    than this prefixed representation.
    """

    return f"sha256:{canonical_sha256_hex(value)}"


def _validate_order_by(order_by: Sequence[str]) -> tuple[str, ...]:
    if isinstance(order_by, (str, bytes)):
        raise CalculationNumericError("order_by must be a sequence of field names")
    resolved = tuple(order_by)
    if not resolved:
        raise CalculationNumericError("order_by must contain at least one field")
    if any(not isinstance(field, str) or not field for field in resolved):
        raise CalculationNumericError("order_by fields must be non-empty strings")
    if len(set(resolved)) != len(resolved):
        raise CalculationNumericError("order_by fields must be unique")
    return resolved


def canonicalize_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    order_by: Sequence[str],
) -> tuple[dict[str, CanonicalJsonValue], ...]:
    """Canonicalize an unordered row set using an explicit unique key.

    Database iteration order is never part of a publication hash.  Each row
    must contain the complete ``order_by`` key and duplicate canonical keys are
    rejected instead of being stabilized by an accidental secondary order.
    """

    resolved_order = _validate_order_by(order_by)
    keyed_rows: list[tuple[bytes, dict[str, CanonicalJsonValue]]] = []
    seen_keys: set[bytes] = set()

    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise CalculationNumericError(f"rows[{index}] must be an object")
        normalized = _canonicalize_json(row, path=f"$[{index}]")
        if not isinstance(normalized, dict):  # narrowed by Mapping above
            raise AssertionError("canonical row must remain an object")
        missing = [field for field in resolved_order if field not in normalized]
        if missing:
            raise CalculationNumericError(
                f"rows[{index}] is missing order fields: {', '.join(missing)}"
            )
        key = canonical_json_bytes([normalized[field] for field in resolved_order])
        if key in seen_keys:
            raise CalculationNumericError(
                f"rows[{index}] duplicates canonical order key {key.decode('utf-8')}"
            )
        seen_keys.add(key)
        keyed_rows.append((key, normalized))

    keyed_rows.sort(key=lambda item: item[0])
    return tuple(row for _, row in keyed_rows)


def canonical_rows_json_bytes(
    rows: Sequence[Mapping[str, object]],
    *,
    order_by: Sequence[str],
) -> bytes:
    """Serialize an unordered financial row set with deterministic ordering."""

    return canonical_json_bytes(canonicalize_rows(rows, order_by=order_by))


def canonical_rows_hash(
    rows: Sequence[Mapping[str, object]],
    *,
    order_by: Sequence[str],
) -> str:
    """Hash an unordered financial row set with deterministic ordering."""

    return f"sha256:{canonical_rows_sha256_hex(rows, order_by=order_by)}"


def canonical_rows_sha256_hex(
    rows: Sequence[Mapping[str, object]],
    *,
    order_by: Sequence[str],
) -> str:
    """Return a registry-compatible digest for an unordered financial row set."""

    return sha256(canonical_rows_json_bytes(rows, order_by=order_by)).hexdigest()


__all__ = [
    "ACCOUNTING_EVIDENCE_STORAGE_PRECISION",
    "ACCOUNTING_EVIDENCE_STORAGE_SCALE",
    "AMOUNT_SCALE",
    "BalancedRoundingError",
    "BalancedRoundingInputRow",
    "BalancedRoundingOutputRow",
    "BalancedRoundingResult",
    "CALCULATION_DECIMAL_PRECISION",
    "CALCULATION_DECIMAL_ROUNDING",
    "CalculationNumericError",
    "FACT_FIELD_SCALES",
    "FEE_SCALE",
    "FX_RATE_SCALE",
    "EXACT_QUANTITY_STORAGE_PRECISION",
    "EXACT_QUANTITY_STORAGE_SCALE",
    "PRICE_SCALE",
    "PUBLICATION_FIELD_SCALES",
    "QUANTITY_SCALE",
    "RATIO_SCALE",
    "TAX_SCALE",
    "METHOD_DECIMAL_STORAGE_PRECISION",
    "METHOD_DECIMAL_STORAGE_SCALE",
    "METHOD_EVIDENCE_STORAGE_PRECISION",
    "METHOD_EVIDENCE_STORAGE_SCALE",
    "DERIVED_RATE_STORAGE_PRECISION",
    "DERIVED_RATE_STORAGE_SCALE",
    "METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION",
    "METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE",
    "SOURCE_PRICE_STORAGE_PRECISION",
    "SOURCE_PRICE_STORAGE_SCALE",
    "SOURCE_RATE_STORAGE_PRECISION",
    "SOURCE_RATE_STORAGE_SCALE",
    "calculation_context",
    "balanced_round_rows",
    "balanced_rounding_sha256_hex",
    "canonical_decimal",
    "canonical_hash",
    "canonical_json_bytes",
    "canonical_json_value",
    "canonical_rows_hash",
    "canonical_rows_json_bytes",
    "canonical_rows_sha256_hex",
    "canonical_scaled_decimal",
    "canonical_sha256_hex",
    "canonicalize_rows",
    "exact_decimal_product",
    "exact_decimal_negate",
    "exact_decimal_subtract",
    "exact_decimal_sum",
    "method_decimal_add",
    "method_decimal_divide",
    "method_decimal_multiply",
    "method_decimal_round",
    "method_decimal_subtract",
    "quantize_decimal",
    "quantum_for_scale",
    "require_decimal",
    "require_exact_numeric_typmod",
    "require_method_decimal",
]
