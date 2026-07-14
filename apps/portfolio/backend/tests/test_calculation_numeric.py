from __future__ import annotations

from decimal import (
    Decimal,
    DivisionByZero,
    FloatOperation,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    getcontext,
)
from dataclasses import replace
from itertools import permutations
import re

import pytest

from portfolio_app.calculations.numeric import (
    AMOUNT_SCALE,
    BalancedRoundingError,
    BalancedRoundingInputRow,
    CALCULATION_DECIMAL_PRECISION,
    FACT_FIELD_SCALES,
    FEE_SCALE,
    FX_RATE_SCALE,
    METHOD_DECIMAL_STORAGE_PRECISION,
    METHOD_DECIMAL_STORAGE_SCALE,
    METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION,
    METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE,
    PRICE_SCALE,
    PUBLICATION_FIELD_SCALES,
    QUANTITY_SCALE,
    RATIO_SCALE,
    TAX_SCALE,
    CalculationNumericError,
    calculation_context,
    balanced_round_rows,
    balanced_rounding_sha256_hex,
    canonical_decimal,
    canonical_hash,
    canonical_json_bytes,
    canonical_rows_hash,
    canonical_rows_json_bytes,
    canonical_rows_sha256_hex,
    canonical_scaled_decimal,
    canonical_sha256_hex,
    canonicalize_rows,
    exact_decimal_negate,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
    method_decimal_add,
    method_decimal_divide,
    method_decimal_multiply,
    quantize_decimal,
    quantum_for_scale,
    require_decimal,
    require_exact_numeric_typmod,
    require_method_decimal,
)


pytestmark = pytest.mark.no_database


def test_calculation_context_has_exact_contract_and_is_isolated() -> None:
    process_precision = getcontext().prec

    with calculation_context() as context:
        assert context.prec == 50 == CALCULATION_DECIMAL_PRECISION
        assert context.rounding == ROUND_HALF_EVEN
        assert {
            signal for signal, enabled in context.traps.items() if enabled
        } == {FloatOperation, InvalidOperation, DivisionByZero, Overflow}
        context.prec = 7

    with calculation_context() as next_context:
        assert next_context.prec == 50
        assert not any(next_context.flags.values())

    assert getcontext().prec == process_precision


def test_finite_decimal_arithmetic_is_exact_under_hostile_ambient_context() -> None:
    left = Decimal("123456789012345678901234567890.12345678901234567890")
    right = Decimal("0.123456789012345678")
    expected_product = Decimal(
        "15241578753238836639231825663.92318256639231825663907940987639079420"
    )
    expected_sum = Decimal(
        "123456789012345678901234567890.24691357802469135690"
    )

    original_precision = getcontext().prec
    try:
        getcontext().prec = 6
        assert exact_decimal_product(left, right) == expected_product
        assert exact_decimal_sum((left, right)) == expected_sum
        assert exact_decimal_subtract(expected_sum, right) == left
        assert exact_decimal_negate(left) == Decimal(
            "-123456789012345678901234567890.12345678901234567890"
        )
    finally:
        getcontext().prec = original_precision


def test_exact_arithmetic_canonicalizes_internally_created_signed_zero() -> None:
    zero = Decimal("0.000")
    assert exact_decimal_negate(zero) == Decimal("0.000")
    assert not exact_decimal_negate(zero).is_signed()
    assert exact_decimal_sum((zero, -zero)) == Decimal("0.000")
    assert not exact_decimal_subtract(zero, zero).is_signed()


@pytest.mark.parametrize(
    ("expected_error", "operation"),
    [
        (FloatOperation, lambda: Decimal(1.1)),
        (InvalidOperation, lambda: Decimal("0") / Decimal("0")),
        (DivisionByZero, lambda: Decimal("1") / Decimal("0")),
        (Overflow, lambda: Decimal("1e999999") * Decimal("10")),
    ],
)
def test_calculation_context_traps_unsafe_arithmetic(
    expected_error: type[BaseException],
    operation,
) -> None:
    with pytest.raises(expected_error):
        with calculation_context():
            operation()


@pytest.mark.parametrize("value", [0.1, 1, "1", True, None])
def test_decimal_boundary_never_coerces_other_types(value: object) -> None:
    with pytest.raises(CalculationNumericError):
        require_decimal(value, field_name="nav")


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("-0"),
        Decimal("-0.000"),
    ],
)
def test_decimal_boundary_rejects_non_finite_and_negative_zero(value: Decimal) -> None:
    with pytest.raises(CalculationNumericError):
        canonical_decimal(value, field_name="nav")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0"), "0"),
        (Decimal("0E+20"), "0"),
        (Decimal("1.230000000000"), "1.23"),
        (Decimal("-123.4500"), "-123.45"),
        (Decimal("1E+12"), "1000000000000"),
        (Decimal("1E-18"), "0.000000000000000001"),
        (
            Decimal("999999999999999999999999999999.1234567800"),
            "999999999999999999999999999999.12345678",
        ),
    ],
)
def test_canonical_decimal_is_plain_and_unique(value: Decimal, expected: str) -> None:
    assert canonical_decimal(value) == expected
    assert "E" not in expected and "e" not in expected


def test_method50_arithmetic_is_bounded_and_ambient_context_independent() -> None:
    original_precision = getcontext().prec
    try:
        getcontext().prec = 6
        quotient = method_decimal_divide(Decimal("1"), Decimal("30"))
        factor = method_decimal_add(Decimal("1"), quotient)
        wealth = method_decimal_multiply(Decimal("1"), factor)
    finally:
        getcontext().prec = original_precision

    assert quotient == Decimal("0.033333333333333333333333333333333333333333333333333")
    assert len(quotient.as_tuple().digits) == 50
    assert quotient.as_tuple().exponent == -51
    assert wealth == factor


def test_method_and_rounding_evidence_typmods_are_exact_bounds() -> None:
    assert (METHOD_DECIMAL_STORAGE_PRECISION, METHOD_DECIMAL_STORAGE_SCALE) == (
        132,
        100,
    )
    assert (
        METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION,
        METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE,
    ) == (232, 200)
    assert require_exact_numeric_typmod(
        Decimal("1E-100"),
        precision=132,
        scale=100,
    ) == Decimal("1E-100")
    assert require_method_decimal(Decimal("9" * 32)) == Decimal("9" * 32)
    assert require_exact_numeric_typmod(
        Decimal("1E-200"),
        precision=232,
        scale=200,
    ) == Decimal("1E-200")
    with pytest.raises(CalculationNumericError, match="cannot be stored exactly"):
        require_exact_numeric_typmod(
            Decimal("1E-101"),
            precision=132,
            scale=100,
        )
    with pytest.raises(CalculationNumericError, match="cannot be stored exactly"):
        require_exact_numeric_typmod(
            Decimal("1E+32"),
            precision=132,
            scale=100,
        )
    with pytest.raises(CalculationNumericError, match="cannot be stored exactly"):
        require_exact_numeric_typmod(
            Decimal("1E-201"),
            precision=232,
            scale=200,
        )
    with pytest.raises(CalculationNumericError, match="cannot be stored exactly"):
        require_method_decimal(
            Decimal(
                "1.2345678901234567890123456789012345678901234567890E-100"
            )
        )


def test_method_storage_rejects_more_than_fifty_significant_digits() -> None:
    valid = Decimal("1.1234567890123456789012345678901234567890123456789")
    invalid = Decimal("1.12345678901234567890123456789012345678901234567891")

    assert require_method_decimal(valid) == valid
    with pytest.raises(CalculationNumericError, match="significant method digits"):
        require_method_decimal(invalid)


def test_published_fact_scales_are_explicit_and_immutable() -> None:
    assert {
        "quantity": QUANTITY_SCALE,
        "price": PRICE_SCALE,
        "amount": AMOUNT_SCALE,
        "fee": FEE_SCALE,
        "tax": TAX_SCALE,
        "fx_rate": FX_RATE_SCALE,
    } == {
        "quantity": 12,
        "price": 12,
        "amount": 8,
        "fee": 8,
        "tax": 8,
        "fx_rate": 18,
    } == dict(FACT_FIELD_SCALES)

    with pytest.raises(TypeError):
        FACT_FIELD_SCALES["amount"] = 2  # type: ignore[index]


def test_publication_field_scales_are_complete_explicit_and_immutable() -> None:
    amount_fields = {
        "nav",
        "beginning_nav",
        "ending_nav",
        "pending_settlement",
        "position_market_value",
        "market_value",
        "market_value_base",
        "open_cost_basis",
        "cost_basis",
        "cost_basis_base",
        "absolute_change",
        "delta",
        "realized_pnl",
        "realized_pnl_base",
        "unrealized_pnl",
        "unrealized_pnl_base",
        "total_pnl",
        "total_pnl_base",
        "cash_balance",
        "cash_balance_base",
        "external_flow_in",
        "external_flow_out",
        "net_external_flow",
        "income",
        "income_base",
        "income_cash_amount",
        "expense_cash_amount",
        "fees",
        "fees_base",
        "taxes",
        "taxes_base",
        "cash_currency_gains",
        "instrument_currency_gains",
        "return_of_capital_amount",
        "external_cash_in",
        "external_cash_out",
        "net_external_inflow",
        "beginning_value",
        "ending_value",
        "capital_flow_in",
        "capital_flow_out",
        "period_pnl",
        "opening_nav",
        "closing_nav",
        "settled_cash",
        "pending_receivable",
        "pending_payable",
        "accrual_receivable",
        "accrual_payable",
        "economic_pnl",
        "realized_pnl_daily",
        "unrealized_pnl_beginning",
        "unrealized_pnl_ending",
        "unrealized_pnl_change",
        "gross_income_daily",
        "return_of_capital_daily",
        "capitalized_fee_daily",
        "capitalized_tax_daily",
        "expensed_fee_daily",
        "expensed_tax_daily",
        "disposal_fee_in_realized_daily",
        "disposal_tax_in_realized_daily",
        "local_price_effect_daily",
        "position_fx_effect_daily",
        "cash_fx_effect_daily",
        "pending_fx_effect_daily",
        "accrual_fx_effect_daily",
        "fx_conversion_effect_daily",
        "reliable_anchor_nav",
        "historical_base_cost",
        "released_local_cost",
        "released_historical_base_cost",
        "allocated_local_net_proceeds",
        "allocated_base_net_proceeds",
        "rounding_adjustment_base",
        "nav_rounding_adjustment",
        "pnl_rounding_adjustment",
        "pnl_component_rounding_adjustment",
        "position_attribution_rounding_adjustment",
    }
    quantity_fields = {"quantity"}
    price_fields = {"price", "last_price"}
    fx_fields = {"fx_rate"}
    ratio_fields = {
        "subperiod_twr_published",
        "cumulative_twr_published",
        "drawdown_published",
        "weight",
        "portfolio_weight",
        "daily_contribution",
        "wealth_index_published",
        "peak_wealth_index_published",
    }

    assert set(PUBLICATION_FIELD_SCALES) == (
        amount_fields | quantity_fields | price_fields | fx_fields | ratio_fields
    )
    assert {PUBLICATION_FIELD_SCALES[field] for field in amount_fields} == {
        AMOUNT_SCALE
    } == {8}
    assert PUBLICATION_FIELD_SCALES["quantity"] == QUANTITY_SCALE == 12
    assert {PUBLICATION_FIELD_SCALES[field] for field in price_fields} == {
        PRICE_SCALE
    } == {12}
    assert PUBLICATION_FIELD_SCALES["fx_rate"] == FX_RATE_SCALE == 18
    assert {PUBLICATION_FIELD_SCALES[field] for field in ratio_fields} == {
        RATIO_SCALE
    } == {18}

    with pytest.raises(TypeError):
        PUBLICATION_FIELD_SCALES["nav"] = 2  # type: ignore[index]


@pytest.mark.parametrize("scale", [-1, 1.0, True, "8"])
def test_quantum_rejects_implicit_or_invalid_scale(scale: object) -> None:
    with pytest.raises(CalculationNumericError):
        quantum_for_scale(scale=scale)  # type: ignore[arg-type]


def test_half_even_rounding_is_applied_only_at_explicit_boundary() -> None:
    assert quantize_decimal(Decimal("2.345"), scale=2) == Decimal("2.34")
    assert quantize_decimal(Decimal("2.355"), scale=2) == Decimal("2.36")
    assert quantize_decimal(Decimal("-2.345"), scale=2) == Decimal("-2.34")
    assert quantize_decimal(Decimal("-2.355"), scale=2) == Decimal("-2.36")
    assert canonical_scaled_decimal(Decimal("2.355"), scale=2) == "2.36"


def test_boundary_scale_contract_covers_financial_fact_types() -> None:
    assert quantize_decimal(
        Decimal("1.1234567890125"), scale=QUANTITY_SCALE
    ) == Decimal("1.123456789012")
    assert quantize_decimal(
        Decimal("1.1234567890135"), scale=PRICE_SCALE
    ) == Decimal("1.123456789014")
    assert quantize_decimal(
        Decimal("123.123456785"), scale=AMOUNT_SCALE
    ) == Decimal("123.12345678")
    assert quantize_decimal(
        Decimal("123.123456795"), scale=FEE_SCALE
    ) == Decimal("123.12345680")
    assert quantize_decimal(
        Decimal("0.000000005"), scale=TAX_SCALE
    ) == Decimal("0E-8")
    assert quantize_decimal(
        Decimal("1.0000000000000000005"), scale=FX_RATE_SCALE
    ) == Decimal("1.000000000000000000")


def test_rounding_a_negative_tiny_value_publishes_positive_zero() -> None:
    rounded = quantize_decimal(Decimal("-0.000000001"), scale=AMOUNT_SCALE)
    assert rounded == Decimal("0E-8")
    assert not rounded.is_signed()
    assert canonical_decimal(rounded) == "0"


def test_internal_arithmetic_uses_fifty_digits_without_implicit_quantize() -> None:
    with calculation_context():
        accumulated = sum((Decimal("0.1") for _ in range(1_000)), Decimal("0"))
        repeating = Decimal("1") / Decimal("7")
        fractional_quantity = Decimal("0.333333333333") * Decimal("3")
        large_market_value = (
            Decimal("999999999999999999999999999999.12345678")
            * Decimal("1.000000000000000001")
        )

    assert accumulated == Decimal("100.0")
    assert repeating == Decimal(
        "0.14285714285714285714285714285714285714285714285714"
    )
    assert fractional_quantity == Decimal("0.999999999999")
    assert large_market_value == Decimal(
        "1000000000000000000999999999999.1234567799999999991"
    )


def test_explicit_boundary_fails_when_value_exceeds_context_precision() -> None:
    with pytest.raises(CalculationNumericError, match="precision 50"):
        quantize_decimal(Decimal("1" + "0" * 50), scale=8, field_name="nav")


def test_canonical_json_and_hash_match_independent_golden_values() -> None:
    payload = {
        "unicode": "均成 CTA",
        "methodology_version": "portfolio-daily-v1",
        "manifest": {
            "ratio": Decimal("0.000000000000000001"),
            "nav": Decimal("1234567890.1200"),
            "generation": 7,
        },
        "cutoff": "2026-07-14T08:00:00Z",
    }
    expected = (
        b'{"cutoff":"2026-07-14T08:00:00Z","manifest":{"generation":7,'
        b'"nav":"1234567890.12","ratio":"0.000000000000000001"},'
        b'"methodology_version":"portfolio-daily-v1","unicode":"\xe5\x9d\x87\xe6\x88\x90 CTA"}'
    )

    assert canonical_json_bytes(payload) == expected
    expected_hex = "76a616881cbf7dc1a55afbf407f3683b33f67686e2e03d74ebe69bafa760e46b"
    assert canonical_sha256_hex(payload) == expected_hex
    assert re.fullmatch(r"[0-9a-f]{64}", canonical_sha256_hex(payload))
    assert canonical_hash(payload) == f"sha256:{expected_hex}"


@pytest.mark.parametrize(
    "payload",
    [
        {"value": 0.1},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": Decimal("NaN")},
        {"value": Decimal("Infinity")},
        {"value": Decimal("-0")},
        {1: "non-string-key"},
        {"unsupported": {"set"}},
    ],
)
def test_canonical_json_fails_closed_on_unsafe_values(payload: object) -> None:
    with pytest.raises(CalculationNumericError):
        canonical_json_bytes(payload)


def test_json_array_order_remains_semantic() -> None:
    first = {"fx_path": ["USD", "HKD", "CNY"]}
    second = {"fx_path": ["USD", "CNY", "HKD"]}
    assert canonical_hash(first) != canonical_hash(second)


def test_row_set_is_canonical_for_every_input_permutation() -> None:
    rows = [
        {
            "as_of": "2026-07-14",
            "instrument_id": "fund-b",
            "market_value": Decimal("20.00000000"),
        },
        {
            "as_of": "2026-07-13",
            "instrument_id": "fund-a",
            "market_value": Decimal("10.50000000"),
        },
        {
            "as_of": "2026-07-14",
            "instrument_id": "fund-a",
            "market_value": Decimal("11.25000000"),
        },
    ]
    expected = (
        b'[{"as_of":"2026-07-13","instrument_id":"fund-a",'
        b'"market_value":"10.5"},{"as_of":"2026-07-14",'
        b'"instrument_id":"fund-a","market_value":"11.25"},'
        b'{"as_of":"2026-07-14","instrument_id":"fund-b",'
        b'"market_value":"20"}]'
    )
    hashes = {
        canonical_rows_hash(candidate, order_by=("as_of", "instrument_id"))
        for candidate in permutations(rows)
    }
    registry_hashes = {
        canonical_rows_sha256_hex(
            candidate,
            order_by=("as_of", "instrument_id"),
        )
        for candidate in permutations(rows)
    }

    assert hashes == {
        "sha256:dc30a286e5795d5b8cef8c71c7f0ef1b62e8bcdfd00a29e9018d045f9d17d02f"
    }
    assert registry_hashes == {
        "dc30a286e5795d5b8cef8c71c7f0ef1b62e8bcdfd00a29e9018d045f9d17d02f"
    }
    assert canonical_rows_json_bytes(
        rows, order_by=("as_of", "instrument_id")
    ) == expected


def test_row_canonicalization_normalizes_mapping_and_decimal_representation() -> None:
    first = [{"id": "a", "nav": Decimal("1.2300"), "meta": {"z": 2, "a": 1}}]
    second = [{"meta": {"a": 1, "z": 2}, "nav": Decimal("1.23"), "id": "a"}]
    assert canonical_rows_hash(first, order_by=("id",)) == canonical_rows_hash(
        second, order_by=("id",)
    )


@pytest.mark.parametrize(
    ("rows", "order_by", "message"),
    [
        ([{"id": "a"}], (), "at least one"),
        ([{"id": "a"}], ("id", "id"), "unique"),
        ([{"nav": Decimal("1")}], ("id",), "missing order fields"),
        ([{"id": "a"}, {"id": "a"}], ("id",), "duplicates"),
    ],
)
def test_row_canonicalization_rejects_ambiguous_ordering(
    rows: list[dict[str, object]],
    order_by: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(CalculationNumericError, match=message):
        canonicalize_rows(rows, order_by=order_by)


def test_every_manifest_identity_dependency_changes_hash() -> None:
    base = {
        "revision_id": "revision-7",
        "policy_hash": "sha256:policy-1",
        "config_hash": "sha256:config-1",
        "cutoff": "2026-07-14T08:00:00Z",
        "value": Decimal("100.00000000"),
    }
    base_hash = canonical_hash(base)

    for field, changed_value in {
        "revision_id": "revision-8",
        "policy_hash": "sha256:policy-2",
        "config_hash": "sha256:config-2",
        "cutoff": "2026-07-14T09:00:00Z",
    }.items():
        changed = {**base, field: changed_value}
        assert canonical_hash(changed) != base_hash


def test_trailing_decimal_zeroes_do_not_change_output_hash() -> None:
    assert canonical_hash({"nav": Decimal("1.2300")}) == canonical_hash(
        {"nav": Decimal("1.23")}
    )


def test_balanced_rounding_allocates_positive_residual_by_remainder_then_key() -> None:
    result = balanced_round_rows(
        (
            BalancedRoundingInputRow(("c",), Decimal("0.333")),
            BalancedRoundingInputRow(("a",), Decimal("0.333")),
            BalancedRoundingInputRow(("b",), Decimal("0.334")),
        ),
        exact_aggregate=Decimal("1.000"),
        scale=2,
    )
    rows = {row.natural_key: row for row in result.rows}
    assert result.target_aggregate == Decimal("1.00")
    assert rows[("b",)].published_value == Decimal("0.34")
    assert rows[("b",)].rounding_adjustment == Decimal("0.01")
    assert rows[("a",)].published_value == Decimal("0.33")
    assert sum((row.published_value for row in result.rows), Decimal("0")) == Decimal(
        "1.00"
    )


def test_balanced_rounding_negative_residual_uses_stable_natural_key_tie_break() -> None:
    result = balanced_round_rows(
        tuple(
            BalancedRoundingInputRow((key,), Decimal("-0.333"))
            for key in ("c", "b", "a")
        ),
        exact_aggregate=Decimal("-0.999"),
        scale=2,
    )
    rows = {row.natural_key: row for row in result.rows}
    assert rows[("a",)].rounding_adjustment == Decimal("-0.01")
    assert rows[("a",)].published_value == Decimal("-0.34")
    assert sum((row.published_value for row in result.rows), Decimal("0")) == Decimal(
        "-1.00"
    )


def test_balanced_rounding_is_identical_for_every_input_permutation() -> None:
    source = (
        BalancedRoundingInputRow(("a",), Decimal("1.004")),
        BalancedRoundingInputRow(("b",), Decimal("2.005")),
        BalancedRoundingInputRow(("c",), Decimal("3.006")),
    )
    payloads = {
        canonical_json_bytes(
            balanced_round_rows(
                candidate,
                exact_aggregate=Decimal("6.015"),
                scale=2,
            ).canonical_payload()
        )
        for candidate in permutations(source)
    }
    assert len(payloads) == 1
    result = balanced_round_rows(
        source,
        exact_aggregate=Decimal("6.015"),
        scale=2,
    )
    adjusted = [row for row in result.rows if row.rounding_adjustment != 0]
    assert [row.natural_key for row in adjusted] == [("b",)]


def test_balanced_rounding_never_hides_exact_rollup_break() -> None:
    with pytest.raises(BalancedRoundingError, match="close exactly"):
        balanced_round_rows(
            (
                BalancedRoundingInputRow(("a",), Decimal("0.333")),
                BalancedRoundingInputRow(("b",), Decimal("0.333")),
            ),
            exact_aggregate=Decimal("1"),
            scale=2,
        )
    with pytest.raises(BalancedRoundingError, match="unique"):
        balanced_round_rows(
            (
                BalancedRoundingInputRow(("a",), Decimal("0.5")),
                BalancedRoundingInputRow(("a",), Decimal("0.5")),
            ),
            exact_aggregate=Decimal("1"),
            scale=2,
        )


def test_balanced_rounding_hash_commits_published_value_and_adjustment() -> None:
    result = balanced_round_rows(
        (
            BalancedRoundingInputRow(("a",), Decimal("0.333")),
            BalancedRoundingInputRow(("b",), Decimal("0.334")),
            BalancedRoundingInputRow(("c",), Decimal("0.333")),
        ),
        exact_aggregate=Decimal("1"),
        scale=2,
    )
    original_hash = balanced_rounding_sha256_hex(result)
    first = result.rows[0]
    tampered = replace(
        result,
        rows=(
            replace(
                first,
                published_value=first.published_value + Decimal("0.01"),
                rounding_adjustment=first.rounding_adjustment + Decimal("0.01"),
            ),
            *result.rows[1:],
        ),
    )
    assert re.fullmatch(r"[0-9a-f]{64}", original_hash)
    assert balanced_rounding_sha256_hex(tampered) != original_hash
