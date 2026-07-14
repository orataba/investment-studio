from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from portfolio_app.api.contracts import (
    PortfolioOperatingProfile,
    PortfolioRiskPolicyRecord,
    PortfolioRiskPolicyUpdateRequest,
    SupportedCurrency,
)
from portfolio_app.calculations.numeric import canonical_decimal
from portfolio_app.calculations.portfolio_daily.published_repository import (
    PortfolioDailyCurrentPublicationChanged,
    PortfolioDailyPublishedReadIntegrityError,
    read_current_portfolio_daily_latest_summary,
)
from portfolio_app.db.session import get_db_session
from portfolio_app.services.portfolio_store import (
    archive_portfolio,
    copy_portfolio,
    create_portfolio,
    list_portfolios,
    reorder_portfolios,
    restore_portfolio,
    update_portfolio_operating_profile,
)
from portfolio_app.services.risk_model import get_portfolio_risk_policy, update_portfolio_risk_policy


router = APIRouter()


class PortfolioCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_currency: SupportedCurrency
    operating_profile: PortfolioOperatingProfile

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Portfolio name is required.")
        return normalized


class PortfolioReorderRequest(BaseModel):
    portfolio_ids: list[str] = Field(default_factory=list)


class PortfolioUpdateRequest(BaseModel):
    operating_profile: PortfolioOperatingProfile


def _portfolio_entry(
    session: Session,
    record: dict[str, object],
) -> dict[str, object]:
    portfolio_id = str(record["portfolio_id"])
    try:
        published = read_current_portfolio_daily_latest_summary(
            session,
            portfolio_id=portfolio_id,
        )
    except PortfolioDailyCurrentPublicationChanged as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "publication_changed_retry",
                "portfolio_id": portfolio_id,
                "expected_publication_id": str(error.expected_publication_id),
                "actual_publication_id": (
                    str(error.actual_publication_id)
                    if error.actual_publication_id is not None
                    else None
                ),
            },
        ) from error
    except PortfolioDailyPublishedReadIntegrityError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "published_calculation_integrity_error",
                "portfolio_id": portfolio_id,
            },
        ) from error

    if published is None:
        return {
            **record,
            "calculation_status": "not_ready",
            "as_of_date": None,
            "nav": None,
            "economic_pnl": None,
            "subperiod_twr_method50": None,
            "subperiod_twr_published": None,
            "holding_count": 0,
            "publication": None,
        }

    snapshot = published.snapshot
    metadata = published.metadata
    return {
        **record,
        "calculation_status": (
            "stale" if metadata.stale else "published"
        ),
        "as_of_date": (
            snapshot.as_of_date.isoformat() if snapshot is not None else None
        ),
        "nav": (
            canonical_decimal(snapshot.closing_nav, field_name="closing_nav")
            if snapshot is not None and snapshot.closing_nav is not None
            else None
        ),
        "economic_pnl": (
            canonical_decimal(snapshot.economic_pnl, field_name="economic_pnl")
            if snapshot is not None and snapshot.economic_pnl is not None
            else None
        ),
        "subperiod_twr_method50": (
            canonical_decimal(
                snapshot.subperiod_twr_method50,
                field_name="subperiod_twr_method50",
            )
            if snapshot is not None and snapshot.subperiod_twr_method50 is not None
            else None
        ),
        "subperiod_twr_published": (
            canonical_decimal(
                snapshot.subperiod_twr_published,
                field_name="subperiod_twr_published",
            )
            if snapshot is not None and snapshot.subperiod_twr_published is not None
            else None
        ),
        "holding_count": metadata.holding_count,
        "publication": {
            "publication_id": str(metadata.publication_id),
            "run_id": str(metadata.run_id),
            "manifest_id": str(metadata.manifest_id),
            "methodology_version": metadata.methodology_version,
            "published_at": metadata.published_at.isoformat(),
            "captured_generation": metadata.captured_generation,
            "current_generation": metadata.current_generation,
            "stale": metadata.stale,
            "pending": metadata.pending,
            "pending_generation": metadata.pending_generation,
        },
    }


@router.get("")
def list_portfolio_records(
    include_archived: bool = False,
    session: Session = Depends(get_db_session),
) -> list[dict[str, object]]:
    return [
        _portfolio_entry(session, record)
        for record in list_portfolios(include_archived=include_archived)
    ]


@router.post("")
def create_portfolio_record(payload: PortfolioCreateRequest) -> dict[str, object]:
    return create_portfolio(
        payload.name,
        base_currency=payload.base_currency,
        operating_profile=payload.operating_profile,
    )


@router.post("/reorder")
def reorder_portfolio_records(payload: PortfolioReorderRequest) -> list[dict[str, object]]:
    try:
        return reorder_portfolios(payload.portfolio_ids)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/{portfolio_id}/copy")
def copy_portfolio_record(portfolio_id: str) -> dict[str, object]:
    record = copy_portfolio(portfolio_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return record


@router.patch("/{portfolio_id}")
def update_portfolio_record(
    portfolio_id: str,
    payload: PortfolioUpdateRequest,
) -> dict[str, object]:
    record = update_portfolio_operating_profile(
        portfolio_id,
        operating_profile=payload.operating_profile,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return record


@router.get("/{portfolio_id}/risk-policy", response_model=PortfolioRiskPolicyRecord)
def get_portfolio_production_risk_policy(portfolio_id: str) -> PortfolioRiskPolicyRecord:
    policy = get_portfolio_risk_policy(portfolio_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return PortfolioRiskPolicyRecord.model_validate(policy)


@router.put("/{portfolio_id}/risk-policy", response_model=PortfolioRiskPolicyRecord)
def update_portfolio_production_risk_policy(
    portfolio_id: str,
    payload: PortfolioRiskPolicyUpdateRequest,
) -> PortfolioRiskPolicyRecord:
    try:
        policy = update_portfolio_risk_policy(portfolio_id, payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if policy is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return PortfolioRiskPolicyRecord.model_validate(policy)


@router.post("/{portfolio_id}/archive")
def archive_portfolio_record(portfolio_id: str) -> dict[str, object]:
    try:
        record = archive_portfolio(portfolio_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return record


@router.post("/{portfolio_id}/restore")
def restore_portfolio_record(portfolio_id: str) -> dict[str, object]:
    record = restore_portfolio(portfolio_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return record
