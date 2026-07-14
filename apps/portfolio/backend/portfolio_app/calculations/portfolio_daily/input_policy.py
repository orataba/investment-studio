"""Pure, versioned policies used while freezing Portfolio Daily inputs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

from portfolio_app.calculations.numeric import CalculationNumericError


PER_UNIT_METHODOLOGY_INSTRUMENT_TYPES = frozenset(
    {"equity", "fund", "etf", "exchange_traded_fund"}
)


@dataclass(frozen=True, slots=True)
class InstrumentValuationContract:
    price_unit: str | None
    contract_multiplier: Decimal | None
    price_factor: Decimal | None
    accrual_convention: str | None
    state: str
    reason_codes: tuple[str, ...]
    source: str | None

    @property
    def available(self) -> bool:
        return self.state == "available"


@dataclass(frozen=True, slots=True)
class InstrumentFreshnessPolicy:
    mode: str
    max_age_days: int


def _optional_positive_decimal(
    value: object,
    *,
    field_name: str,
) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float):
        raise CalculationNumericError(f"{field_name} must not be a float")
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise CalculationNumericError(
            f"{field_name} must be a Decimal, integer, or decimal string"
        )
    try:
        resolved = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise CalculationNumericError(
            f"{field_name} must be a valid decimal"
        ) from error
    if not resolved.is_finite() or resolved <= 0:
        raise CalculationNumericError(f"{field_name} must be positive")
    return resolved


def resolve_instrument_valuation_contract(
    *,
    instrument_type: str,
    source_settings: Mapping[str, object] | None,
) -> InstrumentValuationContract:
    """Resolve price-unit semantics without guessing for unsupported assets."""

    normalized_type = str(instrument_type or "").strip().lower()
    if normalized_type in PER_UNIT_METHODOLOGY_INSTRUMENT_TYPES:
        return InstrumentValuationContract(
            price_unit="per_unit",
            contract_multiplier=Decimal("1"),
            price_factor=Decimal("1"),
            accrual_convention=None,
            state="available",
            reason_codes=(),
            source="methodology",
        )

    raw_settings = dict(source_settings or {})
    raw_contract = raw_settings.get("valuation_contract")
    if raw_contract is None:
        contract: Mapping[str, object] = {}
    elif isinstance(raw_contract, Mapping):
        contract = raw_contract
    else:
        raise CalculationNumericError("valuation_contract must be an object")

    multiplier = _optional_positive_decimal(
        contract.get("contract_multiplier"),
        field_name="contract_multiplier",
    )
    price_factor = _optional_positive_decimal(
        contract.get("price_factor"),
        field_name="price_factor",
    )
    raw_price_unit = contract.get("price_unit")
    if raw_price_unit is None:
        price_unit = None
    elif not isinstance(raw_price_unit, str):
        raise CalculationNumericError("price_unit must be a string")
    else:
        price_unit = raw_price_unit.strip()
        if not price_unit or price_unit != raw_price_unit:
            raise CalculationNumericError(
                "price_unit must be a non-empty canonical string"
            )
    raw_accrual = contract.get("accrual_convention")
    accrual_convention = (
        str(raw_accrual).strip() if raw_accrual is not None else None
    )
    if accrual_convention == "":
        accrual_convention = None

    reasons: list[str] = []
    if price_unit is None:
        reasons.append("missing_price_unit")
    if multiplier is None:
        reasons.append("missing_contract_multiplier")
    if price_factor is None:
        reasons.append("missing_price_factor")
    if normalized_type == "bond" and accrual_convention is None:
        reasons.append("missing_accrual_convention")
    if reasons:
        return InstrumentValuationContract(
            price_unit=price_unit,
            contract_multiplier=multiplier,
            price_factor=price_factor,
            accrual_convention=accrual_convention,
            state="unavailable",
            reason_codes=tuple(sorted(reasons)),
            source=None,
        )
    return InstrumentValuationContract(
        price_unit=price_unit,
        contract_multiplier=multiplier,
        price_factor=price_factor,
        accrual_convention=accrual_convention,
        state="available",
        reason_codes=(),
        source="instrument",
    )


def resolve_instrument_freshness_policy(
    *,
    instrument_type: str,
    daily_market_max_age_days: int,
    fund_max_age_days: int,
) -> InstrumentFreshnessPolicy:
    normalized_type = str(instrument_type or "").strip().lower()
    max_age_days = (
        fund_max_age_days if normalized_type == "fund" else daily_market_max_age_days
    )
    if not 0 <= max_age_days <= 366:
        raise ValueError("freshness max age must be between 0 and 366")
    return InstrumentFreshnessPolicy(
        mode=("exact_only" if max_age_days == 0 else "calendar_day_carry_forward"),
        max_age_days=max_age_days,
    )


__all__ = [
    "InstrumentFreshnessPolicy",
    "InstrumentValuationContract",
    "PER_UNIT_METHODOLOGY_INSTRUMENT_TYPES",
    "resolve_instrument_freshness_policy",
    "resolve_instrument_valuation_contract",
]
