from __future__ import annotations

from decimal import Decimal

import pytest

from portfolio_ops_instrument_core.quote_revisions import (
    QUOTE_REVISION_PAYLOAD_SCHEMA_V2,
    quote_numeric_evidence,
    quote_revision_payload_hash,
)


def test_declared_decimal_scale_is_separate_from_canonical_numeric_value() -> None:
    four_places = quote_numeric_evidence("1.2300")
    two_places = quote_numeric_evidence(Decimal("1.23"))

    assert four_places.value == two_places.value == "1.23"
    assert four_places.value_input_scale == 4
    assert two_places.value_input_scale == 2
    assert four_places.numeric_scale_state == "declared"
    assert two_places.numeric_scale_state == "declared"

    four_place_hash = quote_revision_payload_hash(
        value=four_places.value,
        value_input_scale=four_places.value_input_scale,
        numeric_scale_state=four_places.numeric_scale_state,
        payload_schema_version=QUOTE_REVISION_PAYLOAD_SCHEMA_V2,
        source_ref="issuer-statement",
        status="complete",
    )
    two_place_hash = quote_revision_payload_hash(
        value=two_places.value,
        value_input_scale=two_places.value_input_scale,
        numeric_scale_state=two_places.numeric_scale_state,
        payload_schema_version=QUOTE_REVISION_PAYLOAD_SCHEMA_V2,
        source_ref="issuer-statement",
        status="complete",
    )
    assert four_place_hash != two_place_hash
    assert four_place_hash == quote_revision_payload_hash(
        value=four_places.value,
        value_input_scale=4,
        numeric_scale_state="declared",
        payload_schema_version=2,
        source_ref="issuer-statement",
        status="complete",
    )


def test_binary_float_uses_shortest_decimal_without_claiming_declared_scale() -> None:
    evidence = quote_numeric_evidence(1.23)

    assert evidence.value == "1.23"
    assert evidence.value_input_scale == 2
    assert evidence.numeric_scale_state == "binary_inferred"
    with pytest.raises(ValueError, match="cannot be marked declared"):
        quote_numeric_evidence(
            1.23,
            value_input_scale=2,
            numeric_scale_state="declared",
        )


@pytest.mark.parametrize("invalid_scale", [True, 1.5, "01", "1.5"])
def test_explicit_input_scale_never_truncates_or_coerces(
    invalid_scale: object,
) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        quote_numeric_evidence(
            Decimal("1.23"),
            value_input_scale=invalid_scale,
            numeric_scale_state="declared",
        )

    assert (
        quote_numeric_evidence(
            Decimal("1.2300"),
            value_input_scale="4",
            numeric_scale_state="declared",
        ).value_input_scale
        == 4
    )


def test_v1_payload_hash_contract_is_unchanged_and_withdrawal_has_no_scale() -> None:
    assert (
        quote_revision_payload_hash(
            value="1.2300",
            source_ref="issuer",
            status="complete",
        )
        == "sha256:52f30658966b6eb76d0f7a119b0acf661ed18d0ea62d9c1b66aeb680e3a3f22e"
    )

    withdrawn_hash = quote_revision_payload_hash(
        value=None,
        value_input_scale=None,
        numeric_scale_state=None,
        payload_schema_version=2,
        source_ref="issuer",
        status="withdrawn",
    )
    assert withdrawn_hash.startswith("sha256:")
    with pytest.raises(ValueError, match="must not carry numeric evidence"):
        quote_revision_payload_hash(
            value=None,
            value_input_scale=4,
            numeric_scale_state="declared",
            payload_schema_version=2,
            source_ref="issuer",
            status="withdrawn",
        )
