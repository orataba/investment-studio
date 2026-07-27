from __future__ import annotations

from dataclasses import dataclass
from datetime import date


MINIMUM_ANNUALIZATION_YEARS = 1.0
MISSING_PERIOD_REASON = "valid_measurement_period_required"
SHORT_PERIOD_REASON = "measurement_period_shorter_than_one_year"


@dataclass(frozen=True)
class AnnualizationEligibility:
    eligible: bool
    years: float | None
    unavailable_reason: str | None


def actual_year_fraction(start_date: date, end_date: date) -> float:
    """Return a calendar-anniversary Actual/Actual year fraction.

    Exact calendar anniversaries are exact whole years, including intervals
    containing a leap day.  The remaining stub is divided by the actual number
    of days to the next anniversary.  This keeps a rolling 1Y interval eligible
    and prevents a 365-day non-leap year from being mislabeled as shorter than
    one year by an ACT/365.25 threshold.
    """

    if end_date < start_date:
        return -actual_year_fraction(end_date, start_date)

    def anniversary(years: int) -> date:
        target_year = start_date.year + years
        try:
            return start_date.replace(year=target_year)
        except ValueError:
            # A 29 February anniversary lands on the last valid day of
            # February in a non-leap target year.
            return date(target_year, 2, 28)

    whole_years = max(end_date.year - start_date.year, 0)
    while whole_years > 0 and anniversary(whole_years) > end_date:
        whole_years -= 1
    current_anniversary = anniversary(whole_years)
    if current_anniversary == end_date:
        return float(whole_years)
    next_anniversary = anniversary(whole_years + 1)
    stub_days = (end_date - current_anniversary).days
    anniversary_days = (next_anniversary - current_anniversary).days
    return float(whole_years) + (stub_days / anniversary_days)


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
