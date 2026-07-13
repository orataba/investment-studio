from __future__ import annotations

from datetime import date, datetime
from typing import Mapping

from portfolio_app.services.performance import PerformanceDataIntegrityError


MIN_ANNUALIZED_RETURN_HISTORY_DAYS = 365
HISTORY_WINDOW_UNAVAILABLE = "performance_history_window_unavailable"
ANNUALIZED_RETURN_HISTORY_BELOW_MINIMUM = (
    "annualized_return_history_below_minimum"
)
_HISTORY_GATED_SUMMARY_FIELDS = (
    "annualized_twr",
    "irr",
    "mwror",
    "calmar_ratio",
)


def _optional_date(value: object, *, field_name: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            try:
                return date.fromisoformat(normalized)
            except ValueError as error:
                raise PerformanceDataIntegrityError(
                    f"Performance summary {field_name} must be an ISO date."
                ) from error
    raise PerformanceDataIntegrityError(
        f"Performance summary {field_name} must be a date or null."
    )


def _non_negative_count(
    summary: Mapping[str, object],
    field_name: str,
) -> int:
    value = summary.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PerformanceDataIntegrityError(
            f"Performance summary {field_name} must be a non-negative integer."
        )
    return value


def build_performance_history_reliability(
    summary: Mapping[str, object],
) -> dict[str, object]:
    start_date = _optional_date(summary.get("start_date"), field_name="start_date")
    end_date = _optional_date(summary.get("end_date"), field_name="end_date")
    snapshot_count = _non_negative_count(summary, "snapshot_count")
    return_observation_count = _non_negative_count(
        summary,
        "return_observation_count",
    )
    risk_return_observation_count = _non_negative_count(
        summary,
        "risk_return_observation_count",
    )

    elapsed_days: int | None = None
    calendar_span_days: int | None = None
    reasons: list[str] = []
    if start_date is None or end_date is None:
        reasons.append(HISTORY_WINDOW_UNAVAILABLE)
    elif end_date < start_date:
        raise PerformanceDataIntegrityError(
            "Performance summary end_date must be on or after start_date."
        )
    else:
        elapsed_days = (end_date - start_date).days
        calendar_span_days = elapsed_days + 1
        if elapsed_days < MIN_ANNUALIZED_RETURN_HISTORY_DAYS:
            reasons.append(ANNUALIZED_RETURN_HISTORY_BELOW_MINIMUM)

    annualized_return_eligible = not reasons
    period_label = (
        f"{start_date.isoformat()} to {end_date.isoformat()}"
        if start_date is not None and end_date is not None
        else "Observed period unavailable"
    )
    sample_parts = [
        period_label,
        (
            f"{calendar_span_days} calendar days"
            if calendar_span_days is not None
            else None
        ),
        f"{snapshot_count} snapshots",
        f"{return_observation_count} return observations",
        f"{risk_return_observation_count} risk observations",
    ]
    if annualized_return_eligible:
        annualization_message = None
    elif HISTORY_WINDOW_UNAVAILABLE in reasons:
        annualization_message = (
            "Observed performance history is unavailable. Annualized TWR, "
            "IRR / MWRR, and Calmar Ratio are withheld."
        )
    else:
        annualization_message = (
            "Insufficient history: annualized TWR, IRR / MWRR, and Calmar "
            "Ratio require at least one year. Period TWR remains the primary "
            "return."
        )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "elapsed_days": elapsed_days,
        "calendar_span_days": calendar_span_days,
        "minimum_history_days": MIN_ANNUALIZED_RETURN_HISTORY_DAYS,
        "annualized_return_eligible": annualized_return_eligible,
        "annualized_return_reason_codes": reasons,
        "sample_label": " · ".join(
            part for part in sample_parts if part is not None
        ),
        "annualization_message": annualization_message,
    }


def apply_performance_history_reliability(
    summary: Mapping[str, object],
) -> dict[str, object]:
    """Publish annualized return metrics only when the observed window qualifies."""
    payload = dict(summary)
    reliability = build_performance_history_reliability(summary)
    payload["history_reliability"] = reliability
    if not reliability["annualized_return_eligible"]:
        for field_name in _HISTORY_GATED_SUMMARY_FIELDS:
            payload[field_name] = None
    return payload


__all__ = [
    "ANNUALIZED_RETURN_HISTORY_BELOW_MINIMUM",
    "HISTORY_WINDOW_UNAVAILABLE",
    "MIN_ANNUALIZED_RETURN_HISTORY_DAYS",
    "apply_performance_history_reliability",
    "build_performance_history_reliability",
]
