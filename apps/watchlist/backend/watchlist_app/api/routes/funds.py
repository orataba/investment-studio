from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import (
    ManualProfileUpsertRequest,
    ManualFundCreateRequest,
    NavSettingsUpsertRequest,
)
from watchlist_app.core.settings import get_settings
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.assets import SQLAlchemyAssetRepository
from watchlist_app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from watchlist_app.repositories.sqlalchemy.manual_profiles import (
    SQLAlchemyAssetManualProfileRepository,
)
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.services.fund_taxonomy import (
    build_taxonomy_context,
    merge_taxonomy_into_summary,
)
from watchlist_app.services.read_models import (
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
from watchlist_app.services.read_model_freshness import (
    latest_local_market_data_date,
    schedule_asset_refresh_if_stale,
)


router = APIRouter()
settings = get_settings()
read_model_repository = SQLAlchemyReadModelRepository()
taxonomy_repository = SQLAlchemyTaxonomyRepository()
attribute_repository = SQLAlchemyInstrumentAttributeRepository()
manual_profile_repository = SQLAlchemyAssetManualProfileRepository()
asset_repository = SQLAlchemyAssetRepository()
canonical_recalc_service = CanonicalRecalcService()


def _safe_file_segment(value: str | None, fallback: str = "file") -> str:
    raw = Path(value or "").name.strip()
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return normalized or fallback


def _asset_document_dir(asset_id: str) -> Path:
    return settings.document_storage_root / _safe_file_segment(asset_id, fallback="asset")


def _asset_document_download_url(asset_id: str, stored_file_name: str) -> str:
    return f"/api/instruments/{asset_id}/documents/files/{stored_file_name}"


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
            "research_view": "",
            "dd_status": "",
            "odd_status": "",
            "ic_status": "",
            "decision": "",
            "next_review_date": None,
            "primary_analyst": "",
        },
        "manual_rating": None,
        "timeline_notes": [],
    }


def _normalize_manual_rating(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return max(1, min(5, value))


def _normalize_research_payload(payload: dict[str, Any] | None) -> dict[str, object]:
    defaults = _default_research_payload()
    source = payload or {}
    overview_source = source.get("overview")
    default_overview = defaults["overview"]
    overview_values = overview_source if isinstance(overview_source, dict) else {}
    overview = {
        key: overview_values.get(key, value)
        for key, value in default_overview.items()
    }

    timeline_notes = source.get("timeline_notes")

    return {
        "overview": overview,
        "manual_rating": _normalize_manual_rating(source.get("manual_rating")),
        "timeline_notes": timeline_notes if isinstance(timeline_notes, list) else [],
    }


def _default_nav_settings_payload() -> dict[str, object]:
    return {
        "nav_basis_preference": "auto",
        "default_benchmark_asset_id": None,
        "peer_baseline_asset_ids": [],
    }


def _normalize_nav_settings_payload(payload: dict[str, object] | None) -> dict[str, object]:
    normalized = {
        **_default_nav_settings_payload(),
        **(payload or {}),
    }
    if normalized.get("nav_basis_preference") not in {"auto", "nav_with_dividend"}:
        normalized["nav_basis_preference"] = "auto"
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
    assignment = taxonomy_repository.get_assignment(session, asset_id=asset_id)
    node = (
        taxonomy_repository.get_node(session, node_id=str(assignment.node_id))
        if assignment is not None and assignment.node_id
        else None
    )
    taxonomy_context = build_taxonomy_context(node)
    merged = merge_summary_attributes(payload, attributes)
    asset = asset_repository.get(session, asset_id)
    if merged.get("management_firm_name") is None and asset is not None:
        merged["management_firm_name"] = (
            str(asset.metadata_json.get("management_firm_name") or "").strip() or None
        )
    return merge_taxonomy_into_summary(merged, taxonomy_context)


@router.post("/manual")
def create_manual_fund(
    payload: ManualFundCreateRequest,
) -> dict[str, object]:
    _ = payload
    raise HTTPException(
        status_code=410,
        detail=(
            "Manual fund creation is no longer supported in Watchlist. "
            "Create the asset in Database Dashboard, then add it from the shared registry."
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


@router.post("/{asset_id}/documents/upload")
async def upload_fund_document(
    asset_id: str,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    document_type: str | None = Form(None),
    as_of_date: str | None = Form(None),
    source: str | None = Form(None),
    status: str | None = Form(None),
    version_label: str | None = Form(None),
    notes: str | None = Form(None),
    updated_by: str | None = Form(None),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    original_file_name = _safe_file_segment(file.filename, fallback="uploaded-document")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    asset_dir = _asset_document_dir(asset_id)
    asset_dir.mkdir(parents=True, exist_ok=True)
    stored_file_name = f"{uuid4().hex}-{original_file_name}"
    stored_path = asset_dir / stored_file_name
    stored_path.write_bytes(content)

    uploaded_at = datetime.now(timezone.utc).isoformat()
    download_url = _asset_document_download_url(asset_id, stored_file_name)
    record = manual_profile_repository.get(session, asset_id)
    payload = serialize_payload(record.documents_payload_json) if record is not None else _default_documents_payload()

    current_documents = list(payload.get("current_documents") or [])
    recent_imports = list(payload.get("recent_imports") or [])
    extraction_reviews = list(payload.get("extraction_reviews") or [])
    profile_notes = list(payload.get("notes") or [])

    document_row = {
        "title": (title or "").strip() or original_file_name,
        "document_type": (document_type or "").strip(),
        "as_of_date": (as_of_date or "").strip() or None,
        "source": (source or "").strip() or "manual_upload",
        "status": (status or "").strip() or "uploaded",
        "version_label": (version_label or "").strip(),
        "file_name": original_file_name,
        "stored_file_name": stored_file_name,
        "download_url": download_url,
        "file_size": len(content),
        "content_type": file.content_type or "",
        "uploaded_at": uploaded_at,
        "notes": (notes or "").strip(),
    }
    current_documents.insert(0, document_row)
    recent_imports.insert(
        0,
        {
            "import_type": "upload",
            "received_at": uploaded_at,
            "source": document_row["source"],
            "status": document_row["status"],
            "file_name": original_file_name,
        },
    )

    next_payload: dict[str, object] = {
        "current_documents": current_documents,
        "recent_imports": recent_imports,
        "extraction_reviews": extraction_reviews,
        "notes": profile_notes,
    }
    updated_record = manual_profile_repository.upsert(
        session,
        asset_id=asset_id,
        documents_payload_json=next_payload,
        updated_by=updated_by,
    )
    session.commit()
    return serialize_payload(updated_record.documents_payload_json)


@router.get("/{asset_id}/documents/files/{stored_file_name}")
def download_fund_document(
    asset_id: str,
    stored_file_name: str,
    session: Session = Depends(get_db_session),
) -> FileResponse:
    _ensure_asset_exists(session, asset_id)
    safe_stored_file_name = _safe_file_segment(stored_file_name)
    stored_path = _asset_document_dir(asset_id) / safe_stored_file_name
    if not stored_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found.")
    download_name = safe_stored_file_name.split("-", 1)[1] if "-" in safe_stored_file_name else safe_stored_file_name
    return FileResponse(path=stored_path, filename=download_name)


@router.get("/{asset_id}/research")
def get_fund_research_profile(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    record = manual_profile_repository.get(session, asset_id)
    payload = record.research_payload_json if record is not None else _default_research_payload()
    return serialize_payload(_normalize_research_payload(payload))


@router.put("/{asset_id}/research")
def upsert_fund_research_profile(
    asset_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_asset_exists(session, asset_id)
    normalized_payload = _normalize_research_payload(payload.payload)
    record = manual_profile_repository.upsert(
        session,
        asset_id=asset_id,
        research_payload_json=normalized_payload,
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
            "Canonical NAV series is now owned by Database Dashboard. "
            "Use Database Dashboard to import or edit shared market data."
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
            "NAV refresh now runs from Database Dashboard. "
            "Use Database Dashboard to trigger the shared market data refresh."
        ),
    )
