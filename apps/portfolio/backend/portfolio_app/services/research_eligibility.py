from __future__ import annotations

from typing import Literal, Mapping


ResearchLifecycle = Literal["held", "observed", "former"]
ResearchEligibility = Literal["eligible", "pm_review_required"]

RESEARCH_EXECUTION_TARGET_EPSILON = 1e-8
FORMER_PM_REVIEW_EXECUTION_NOTE = (
    "Status: Former · Research eligibility: manual PM review required before execution."
)


def derive_research_lifecycle(
    *,
    holding_state: object,
    transaction_count: object,
) -> ResearchLifecycle:
    """Derive the single portfolio-instrument research lifecycle contract."""

    if str(holding_state or "").strip().lower() == "held":
        return "held"
    try:
        resolved_transaction_count = int(transaction_count or 0)
    except (TypeError, ValueError):
        resolved_transaction_count = 0
    return "former" if resolved_transaction_count > 0 else "observed"


def derive_research_eligibility(
    *,
    lifecycle: ResearchLifecycle,
    pm_approved: object,
) -> ResearchEligibility:
    if lifecycle == "former" and not bool(pm_approved):
        return "pm_review_required"
    return "eligible"


def enrich_instrument_research_state(
    row: Mapping[str, object],
) -> dict[str, object]:
    lifecycle = derive_research_lifecycle(
        holding_state=row.get("holding_state"),
        transaction_count=row.get("transaction_count"),
    )
    pm_approved = bool(row.get("research_pm_approved"))
    return {
        "research_lifecycle": lifecycle,
        "research_eligibility": derive_research_eligibility(
            lifecycle=lifecycle,
            pm_approved=pm_approved,
        ),
        "research_pm_approved": pm_approved,
        "research_pm_approved_at": (
            str(row.get("research_pm_approved_at") or "").strip() or None
        ),
    }
