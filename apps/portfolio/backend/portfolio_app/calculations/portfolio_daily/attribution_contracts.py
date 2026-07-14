"""Immutable exact contracts for Portfolio Daily group attribution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from portfolio_app.calculations.numeric import (
    CalculationNumericError,
    exact_decimal_subtract,
    exact_decimal_sum,
    require_decimal,
    require_method_decimal,
)
from portfolio_app.calculations.portfolio_daily.twr import CoverageStatus


class AttributionContractError(ValueError):
    """An exact group-attribution value violates its immutable contract."""


class AttributionAxis(StrEnum):
    ACCOUNT = "account"
    INSTRUMENT = "instrument"
    CURRENCY = "currency"
    TAXONOMY = "taxonomy"


class AttributionReasonCode(StrEnum):
    RETURN_PERIOD_UNAVAILABLE = "group_return_period_unavailable"
    INTERNAL_MOVEMENT_BASE_UNAVAILABLE = "group_internal_movement_base_unavailable"
    NUMERIC_DOMAIN_UNAVAILABLE = "group_numeric_domain_unavailable"


def _decimal(value: object, *, field_name: str) -> Decimal:
    try:
        return require_decimal(value, field_name=field_name)
    except CalculationNumericError as exc:
        raise AttributionContractError(str(exc)) from exc


def _optional_decimal(value: object, *, field_name: str) -> Decimal | None:
    return None if value is None else _decimal(value, field_name=field_name)


def _optional_method_decimal(value: object, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    resolved = _decimal(value, field_name=field_name)
    try:
        return require_method_decimal(resolved, field_name=field_name)
    except CalculationNumericError as exc:
        raise AttributionContractError(str(exc)) from exc


def _text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AttributionContractError(f"{field_name} must be canonical text")
    return value


@dataclass(frozen=True, slots=True)
class ExactGroupAttribution:
    """One exact economic group bridge before publication rounding."""

    as_of_date: date
    axis: AttributionAxis
    group_key: str
    group_label: str
    measured: bool
    opening_value: Decimal | None
    closing_value: Decimal | None
    external_flow_in: Decimal | None
    external_flow_out: Decimal | None
    internal_flow_in: Decimal | None
    internal_flow_out: Decimal | None
    economic_pnl: Decimal | None
    contribution_method50: Decimal | None
    contribution_division_adjustment_exact: Decimal
    closure_residual_exact: Decimal
    coverage_status: CoverageStatus
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.as_of_date) is not date:
            raise AttributionContractError("as_of_date must be a date")
        if not isinstance(self.axis, AttributionAxis):
            raise AttributionContractError("axis must be an AttributionAxis")
        _text(self.group_key, field_name="group_key")
        _text(self.group_label, field_name="group_label")
        if type(self.measured) is not bool:
            raise AttributionContractError("measured must be bool")
        values = {
            field_name: _optional_decimal(
                getattr(self, field_name),
                field_name=field_name,
            )
            for field_name in (
                "opening_value",
                "closing_value",
                "external_flow_in",
                "external_flow_out",
                "internal_flow_in",
                "internal_flow_out",
                "economic_pnl",
            )
        }
        values["contribution_method50"] = _optional_method_decimal(
            self.contribution_method50,
            field_name="contribution_method50",
        )
        division_adjustment = _decimal(
            self.contribution_division_adjustment_exact,
            field_name="contribution_division_adjustment_exact",
        )
        residual = _decimal(
            self.closure_residual_exact,
            field_name="closure_residual_exact",
        )
        if not isinstance(self.coverage_status, CoverageStatus):
            raise AttributionContractError(
                "coverage_status must be a CoverageStatus"
            )
        if (
            not isinstance(self.reason_codes, tuple)
            or any(
                not isinstance(reason, str)
                or not reason
                or reason != reason.strip()
                for reason in self.reason_codes
            )
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
        ):
            raise AttributionContractError(
                "reason_codes must be unique canonical sorted strings"
            )
        if self.measured:
            if any(value is None for value in values.values()):
                raise AttributionContractError(
                    "measured group attribution requires every exact value"
                )
            if self.coverage_status is not CoverageStatus.COMPLETE:
                raise AttributionContractError(
                    "measured group attribution must have complete coverage"
                )
            if self.reason_codes:
                raise AttributionContractError(
                    "measured group attribution cannot retain reasons"
                )
            for field_name in (
                "external_flow_in",
                "external_flow_out",
                "internal_flow_in",
                "internal_flow_out",
            ):
                value = values[field_name]
                assert value is not None
                if value < 0:
                    raise AttributionContractError(
                        f"{field_name} must be a non-negative magnitude"
                    )
            assert values["closing_value"] is not None
            assert values["external_flow_out"] is not None
            assert values["internal_flow_out"] is not None
            assert values["opening_value"] is not None
            assert values["external_flow_in"] is not None
            assert values["internal_flow_in"] is not None
            assert values["economic_pnl"] is not None
            expected_residual = exact_decimal_subtract(
                exact_decimal_sum(
                    (
                        values["closing_value"],
                        values["external_flow_out"],
                        values["internal_flow_out"],
                    )
                ),
                exact_decimal_sum(
                    (
                        values["opening_value"],
                        values["external_flow_in"],
                        values["internal_flow_in"],
                        values["economic_pnl"],
                    )
                ),
            )
            if residual != expected_residual or residual != 0:
                raise AttributionContractError(
                    "measured group economic bridge must close at exact zero"
                )
            return
        if any(value is not None for value in values.values()):
            raise AttributionContractError(
                "unavailable group attribution must be null-valued"
            )
        if division_adjustment != 0 or residual != 0:
            raise AttributionContractError(
                "unavailable group attribution cannot retain an adjustment"
            )
        if (
            self.coverage_status is CoverageStatus.COMPLETE
            or not self.reason_codes
        ):
            raise AttributionContractError(
                "unavailable group attribution requires reasons"
            )


@dataclass(frozen=True, slots=True)
class ExactGroupAttributionSeries:
    rows: tuple[ExactGroupAttribution, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.rows, tuple) or any(
            not isinstance(row, ExactGroupAttribution) for row in self.rows
        ):
            raise AttributionContractError(
                "rows must be an immutable ExactGroupAttribution tuple"
            )
        keys = [
            (row.as_of_date, row.axis.value, row.group_key) for row in self.rows
        ]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise AttributionContractError(
                "group attribution rows must be unique and canonically ordered"
            )

    def for_day(
        self,
        as_of_date: date,
    ) -> tuple[ExactGroupAttribution, ...]:
        return tuple(row for row in self.rows if row.as_of_date == as_of_date)


__all__ = [
    "AttributionAxis",
    "AttributionContractError",
    "AttributionReasonCode",
    "ExactGroupAttribution",
    "ExactGroupAttributionSeries",
]
