from __future__ import annotations

from dataclasses import dataclass
from datetime import date


ACT_365_25_DAYS_PER_YEAR = 365.25
MINIMUM_ANNUALIZATION_YEARS = 1.0
MISSING_PERIOD_REASON = "valid_measurement_period_required"
SHORT_PERIOD_REASON = "measurement_period_shorter_than_one_year"


@dataclass(frozen=True)
class AnnualizationEligibility:
    eligible: bool
    years: float | None
    unavailable_reason: str | None


def actual_year_fraction(start_date: date, end_date: date) -> float:
    """Return an ACT/365.25 year fraction for an ordered date interval."""

    return (end_date - start_date).days / ACT_365_25_DAYS_PER_YEAR


def annualization_eligibility(
    start_date: date | None,
    end_date: date | None,
) -> AnnualizationEligibility:
    """Provide the canonical backend gate for annualized return disclosures."""

    if start_date is None or end_date is None or end_date < start_date:
        return AnnualizationEligibility(
            eligible=False,
            years=None,
            unavailable_reason=MISSING_PERIOD_REASON,
        )

    years = actual_year_fraction(start_date, end_date)
    if years < MINIMUM_ANNUALIZATION_YEARS:
        return AnnualizationEligibility(
            eligible=False,
            years=years,
            unavailable_reason=SHORT_PERIOD_REASON,
        )

    return AnnualizationEligibility(
        eligible=True,
        years=years,
        unavailable_reason=None,
    )
