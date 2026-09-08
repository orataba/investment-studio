from __future__ import annotations

from typing import Literal, Mapping
from datetime import date


ResearchLifecycle = Literal["held", "observed", "former"]
ResearchEligibility = Literal["eligible", "pm_review_required"]

RESEARCH_EXECUTION_TARGET_EPSILON = 1e-8
FORMER_PM_REVIEW_EXECUTION_NOTE = (
    "Status: Former · Research eligibility: manual PM review required before execution."
)


def contract_only_instrument_ids(
    portfolio_id: str, *, as_of_date: date, explicitly_selected=(), session=None,
) -> set[str]:
    """FCN classification alone does not admit its underlyings to a stock allocation.

    Use contracts actually transacted by the requested date, not future contracts.
    Retain explicit target members and securities with actual position history or
    a dated manual observation. Expiry alone must not silently admit a security.
    """
    from sqlalchemy import func, select
    from portfolio_app.db.models import (
        DerivativeContractRecordModel, PortfolioInstrumentUniverseRecordModel, TransactionRecordModel,
    )
    if session is None:
        from portfolio_app.db.session import get_session_factory
        with get_session_factory()() as current_session:
            return contract_only_instrument_ids(portfolio_id, as_of_date=as_of_date,
                explicitly_selected=explicitly_selected, session=current_session)
    transactions = session.scalars(select(TransactionRecordModel).where(
        TransactionRecordModel.portfolio_id == portfolio_id,
        func.coalesce(TransactionRecordModel.position_effective_date, TransactionRecordModel.trade_date) <= as_of_date,
    )).all()
    contract_ids = {row.derivative_contract_id for row in transactions if row.derivative_contract_id}
    if not contract_ids:
        return set()
    linked = {str(underlying["instrument_id"]) for contract in session.scalars(
        select(DerivativeContractRecordModel).where(
            DerivativeContractRecordModel.portfolio_id == portfolio_id,
            DerivativeContractRecordModel.derivative_contract_id.in_(contract_ids),
            DerivativeContractRecordModel.contract_type == "fcn",
        )) for underlying in (contract.terms_json or {}).get("underlyings", []) if underlying.get("instrument_id")}
    selected = set(explicitly_selected)
    selected.update(row.instrument_id for row in transactions if row.instrument_id)
    selected.update(str(leg["instrument_id"]) for row in transactions
                    for leg in row.asset_deliveries_json or [] if leg.get("instrument_id"))
    for record in session.scalars(select(PortfolioInstrumentUniverseRecordModel).where(
        PortfolioInstrumentUniverseRecordModel.portfolio_id == portfolio_id,
        PortfolioInstrumentUniverseRecordModel.source == "manual",
        PortfolioInstrumentUniverseRecordModel.status == "active",
    )):
        if str(record.updated_at or "")[:10] <= as_of_date.isoformat():
            selected.add(record.instrument_id)
    return linked - selected


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
