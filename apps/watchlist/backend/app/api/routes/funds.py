from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.contracts import (
    ManualProfileUpsertRequest,
    ManualFundCreateRequest,
    NavSettingsUpsertRequest,
)
from app.db.session import get_db_session
from app.repositories.sqlalchemy.assets import SQLAlchemyAssetRepository
from app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from app.repositories.sqlalchemy.manual_profiles import (
    SQLAlchemyAssetManualProfileRepository,
)
from app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from app.services.canonical_recalc import CanonicalRecalcService
from app.services.read_models import (
    collapse_latest_attribute_values,
    default_fund_chart_payload,
    default_fund_performance_payload,
    default_fund_exposure_holdings_payload,
    default_fund_exposure_summary_payload,
    default_fund_rating_payload,
    default_fund_risk_payload,
    default_fund_summary_payload,
    merge_summary_attributes,
    serialize_payload,
)
from app.services.read_model_freshness import (
    latest_local_market_data_date,
    schedule_asset_refresh_if_stale,
)


router = APIRouter()
read_model_repository = SQLAlchemyReadModelRepository()
attribute_repository = SQLAlchemyInstrumentAttributeRepository()
manual_profile_repository = SQLAlchemyAssetManualProfileRepository()
asset_repository = SQLAlchemyAssetRepository()
canonical_recalc_service = CanonicalRecalcService()


def _schedule_asset_refresh(
    session: Session,
    *,
    asset_id: str,
    trigger_ref_type: str,
) -> None:
    chart_record = read_model_repository.get_chart(session, asset_id)
    source_row = read_model_repository.find_any_watchlist_row_for_asset(session, asset_id)
    schedule_asset_refresh_if_stale(
        asset_id=asset_id,
        local_latest_date=latest_local_market_data_date(
            chart_payload=chart_record.payload_json if chart_record is not None else None,
            fallback_values=((source_row.last_nav_date if source_row is not None else None),),
        ),
        trigger_ref_type=trigger_ref_type,
        trigger_ref_id=asset_id,
    )


def _default_people_payload() -> dict[str, object]:
    return {
        "overview": {},
        "team": [],
        "notes": ["People profile is manually maintained."],
    }


def _default_strategy_payload() -> dict[str, object]:
    return {
        "summary": "",
        "investment_objective": "",
        "process_bullets": [],
        "risk_controls": [],
        "notes": ["Strategy profile is manually maintained."],
    }


def _default_price_payload() -> dict[str, object]:
    return {
        "overview": {
            "total_expense_ratio": None,
            "adjusted_expense_ratio": None,
            "management_fee": None,
            "interest_expense_fees": None,
            "redemption_fee": None,
            "minimum_initial_investment": None,
        },
        "distribution_policy": "",
        "policy_text": "",
        "fee_notes": [],
        "notes": ["Price profile is manually maintained."],
    }


def _default_documents_payload() -> dict[str, object]:
    return {
        "current_documents": [],
        "recent_imports": [],
        "extraction_reviews": [],
        "notes": ["Documents are maintained at the instrument level."],
    }


def _default_research_payload() -> dict[str, object]:
    return {
        "overview": {
            "current_view": "",
            "research_status": "",
            "dd_status": "",
            "odd_status": "",
            "ic_status": "",
            "decision": "",
            "next_review_date": None,
            "primary_analyst": "",
        },
        "thesis": "",
        "conclusions": [],
        "notes": ["Research is maintained at the instrument level."],
    }


def _default_nav_settings_payload() -> dict[str, object]:
    return {
        "nav_basis_preference": "auto",
        "source_mode": "manual",
        "source_email": "",
        "source_location": "Manual upload",
        "source_api_profile": "",
        "default_benchmark_asset_id": None,
        "peer_baseline_asset_ids": [],
    }


def _normalize_nav_settings_payload(payload: dict[str, object] | None) -> dict[str, object]:
    normalized = {
        **_default_nav_settings_payload(),
        **(payload or {}),
    }
    if normalized.get("nav_basis_preference") not in {"auto", "nav_with_dividend", "nav"}:
        normalized["nav_basis_preference"] = "auto"
    if normalized.get("source_mode") not in {"manual", "email", "api"}:
        normalized["source_mode"] = "manual"
    normalized["source_email"] = str(normalized.get("source_email") or "")
    normalized["source_location"] = str(normalized.get("source_location") or "Manual upload")
    normalized["source_api_profile"] = str(normalized.get("source_api_profile") or "")
    normalized["default_benchmark_asset_id"] = (
        str(normalized.get("default_benchmark_asset_id")).strip() or None
        if normalized.get("default_benchmark_asset_id") is not None
        else None
    )
    normalized["peer_baseline_asset_ids"] = [
        str(value).strip()
        for value in (normalized.get("peer_baseline_asset_ids") or [])
        if str(value).strip()
    ]
    return normalized


def _validate_compare_asset_ids(
    session: Session,
    *,
    asset_id: str,
    default_benchmark_asset_id: str | None,
    peer_baseline_asset_ids: list[str],
) -> tuple[str | None, list[str]]:
    benchmark_asset_id = default_benchmark_asset_id
    if benchmark_asset_id == asset_id:
        benchmark_asset_id = None
    if benchmark_asset_id is not None and asset_repository.get(session, benchmark_asset_id) is None:
        raise HTTPException(status_code=404, detail="Default benchmark asset not found")

    seen_peer_ids: set[str] = set()
    validated_peer_ids: list[str] = []
    for peer_asset_id in peer_baseline_asset_ids:
        if peer_asset_id == asset_id or peer_asset_id in seen_peer_ids:
            continue
        if asset_repository.get(session, peer_asset_id) is None:
            raise HTTPException(status_code=404, detail=f"Peer baseline asset not found: {peer_asset_id}")
        seen_peer_ids.add(peer_asset_id)
        validated_peer_ids.append(peer_asset_id)
    return benchmark_asset_id, validated_peer_ids


def _ensure_asset_exists(session: Session, asset_id: str) -> None:
    if asset_repository.get(session, asset_id) is None:
        raise HTTPException(status_code=404, detail="Asset not found")


@router.get("/{asset_id}/summary")
def get_fund_summary(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    _schedule_asset_refresh(session, asset_id=asset_id, trigger_ref_type="instrument_summary_read")
    record = read_model_repository.get_summary(session, asset_id)
    attributes = collapse_latest_attribute_values(
        attribute_repository.get_values_for_asset(session, asset_id)
    )
    payload = (
        serialize_payload(record.payload_json)
        if record is not None
        else default_fund_summary_payload(asset_id, instrument_attributes=attributes)
    )
    return merge_summary_attributes(payload, attributes)


@router.post("/manual")
def create_manual_fund(
    payload: ManualFundCreateRequest,
) -> dict[str, object]:
    _ = payload
    raise HTTPException(
        status_code=410,
        detail=(
            "Manual fund creation is no longer supported in Watchlist. "
            "Create the asset in Platform / Instruments, then add it from the shared registry."
        ),
    )


@router.get("/library")
def list_fund_library(
    session: Session = Depends(get_db_session),
) -> list[dict[str, object]]:
    return asset_repository.list_library(session)


@router.get("/{asset_id}/chart")
def get_fund_chart_data(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    _schedule_asset_refresh(session, asset_id=asset_id, trigger_ref_type="instrument_chart_read")
    record = read_model_repository.get_chart(session, asset_id)
    if record is None:
        return default_fund_chart_payload(asset_id)
    return serialize_payload(record.payload_json)


@router.get("/{asset_id}/performance")
def get_fund_performance_data(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    _schedule_asset_refresh(session, asset_id=asset_id, trigger_ref_type="instrument_performance_read")
    record = read_model_repository.get_performance(session, asset_id)
    if record is None:
        return default_fund_performance_payload()
    return serialize_payload(record.payload_json)


@router.get("/{asset_id}/risk")
def get_fund_risk_data(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    _schedule_asset_refresh(session, asset_id=asset_id, trigger_ref_type="instrument_risk_read")
    record = read_model_repository.get_risk(session, asset_id)
    if record is None:
        return default_fund_risk_payload()
    return serialize_payload(record.payload_json)


@router.get("/{asset_id}/exposure/summary")
def get_fund_exposure_summary_data(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    _schedule_asset_refresh(session, asset_id=asset_id, trigger_ref_type="instrument_exposure_summary_read")
    record = read_model_repository.get_exposure_summary(session, asset_id)
    if record is None:
        return default_fund_exposure_summary_payload()
    return serialize_payload(record.payload_json)


@router.get("/{asset_id}/exposure/holdings")
def get_fund_exposure_holdings_data(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    _schedule_asset_refresh(session, asset_id=asset_id, trigger_ref_type="instrument_exposure_holdings_read")
    record = read_model_repository.get_exposure_holdings(session, asset_id)
    if record is None:
        return default_fund_exposure_holdings_payload()
    return serialize_payload(record.payload_json)


@router.get("/{asset_id}/ratings")
def get_fund_rating_data(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    _schedule_asset_refresh(session, asset_id=asset_id, trigger_ref_type="instrument_ratings_read")
    record = read_model_repository.get_rating(session, asset_id)
    if record is None:
        return default_fund_rating_payload()
    return serialize_payload(record.payload_json)


@router.get("/{asset_id}/people")
def get_fund_people_profile(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.get(session, asset_id)
    payload = record.people_payload_json if record is not None else _default_people_payload()
    return serialize_payload(payload)


@router.put("/{asset_id}/people")
def upsert_fund_people_profile(
    asset_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.upsert(
        session,
        asset_id=asset_id,
        people_payload_json=payload.payload,
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(record.people_payload_json)


@router.get("/{asset_id}/strategy")
def get_fund_strategy_profile(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.get(session, asset_id)
    payload = record.strategy_payload_json if record is not None else _default_strategy_payload()
    return serialize_payload(payload)


@router.put("/{asset_id}/strategy")
def upsert_fund_strategy_profile(
    asset_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.upsert(
        session,
        asset_id=asset_id,
        strategy_payload_json=payload.payload,
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(record.strategy_payload_json)


@router.get("/{asset_id}/price")
def get_fund_price_profile(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.get(session, asset_id)
    payload = record.price_payload_json if record is not None else _default_price_payload()
    return serialize_payload(payload)


@router.put("/{asset_id}/price")
def upsert_fund_price_profile(
    asset_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.upsert(
        session,
        asset_id=asset_id,
        price_payload_json=payload.payload,
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(record.price_payload_json)


@router.get("/{asset_id}/documents")
def get_fund_documents_profile(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.get(session, asset_id)
    payload = record.documents_payload_json if record is not None else _default_documents_payload()
    return serialize_payload(payload)


@router.put("/{asset_id}/documents")
def upsert_fund_documents_profile(
    asset_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.upsert(
        session,
        asset_id=asset_id,
        documents_payload_json=payload.payload,
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(record.documents_payload_json)


@router.get("/{asset_id}/research")
def get_fund_research_profile(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.get(session, asset_id)
    payload = record.research_payload_json if record is not None else _default_research_payload()
    return serialize_payload(payload)


@router.put("/{asset_id}/research")
def upsert_fund_research_profile(
    asset_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.upsert(
        session,
        asset_id=asset_id,
        research_payload_json=payload.payload,
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(record.research_payload_json)


@router.get("/{asset_id}/nav-series")
def get_fund_nav_series(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    return canonical_recalc_service.build_nav_series_payload(session, asset_id=asset_id)


@router.put("/{asset_id}/nav-series")
def upsert_fund_nav_series(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    raise HTTPException(
        status_code=409,
        detail=(
            "Canonical NAV series is now owned by shared data ops. "
            "Use Platform / Instruments to import or edit shared market data."
        ),
    )


@router.get("/{asset_id}/nav-settings")
def get_fund_nav_settings(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.get(session, asset_id)
    return serialize_payload(
        _normalize_nav_settings_payload(record.nav_settings_json if record is not None else None)
    )


@router.put("/{asset_id}/nav-settings")
def upsert_fund_nav_settings(
    asset_id: str,
    payload: NavSettingsUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.get(session, asset_id)
    current_payload = _normalize_nav_settings_payload(
        record.nav_settings_json if record is not None else None
    )

    next_payload = dict(current_payload)
    provided_fields = payload.model_fields_set
    if "nav_basis_preference" in provided_fields:
        next_payload["nav_basis_preference"] = payload.nav_basis_preference
    if "source_mode" in provided_fields:
        next_payload["source_mode"] = payload.source_mode
    if "source_email" in provided_fields:
        next_payload["source_email"] = payload.source_email
    if "source_location" in provided_fields:
        next_payload["source_location"] = payload.source_location
    if "source_api_profile" in provided_fields:
        next_payload["source_api_profile"] = payload.source_api_profile
    if "default_benchmark_asset_id" in provided_fields:
        next_payload["default_benchmark_asset_id"] = payload.default_benchmark_asset_id
    if "peer_baseline_asset_ids" in provided_fields:
        next_payload["peer_baseline_asset_ids"] = payload.peer_baseline_asset_ids or []

    benchmark_asset_id, peer_asset_ids = _validate_compare_asset_ids(
        session,
        asset_id=asset_id,
        default_benchmark_asset_id=(
            str(next_payload.get("default_benchmark_asset_id")).strip() or None
            if next_payload.get("default_benchmark_asset_id") is not None
            else None
        ),
        peer_baseline_asset_ids=[
            str(value).strip()
            for value in (next_payload.get("peer_baseline_asset_ids") or [])
            if str(value).strip()
        ],
    )
    next_payload["default_benchmark_asset_id"] = benchmark_asset_id
    next_payload["peer_baseline_asset_ids"] = peer_asset_ids

    updated_record = manual_profile_repository.upsert(
        session,
        asset_id=asset_id,
        nav_settings_json=_normalize_nav_settings_payload(next_payload),
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(_normalize_nav_settings_payload(updated_record.nav_settings_json))


@router.post("/{asset_id}/nav-refresh")
def trigger_fund_nav_refresh(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    raise HTTPException(
        status_code=409,
        detail=(
            "NAV refresh now runs from shared data ops. "
            "Use Platform / Instruments to trigger the shared market data refresh."
        ),
    )
