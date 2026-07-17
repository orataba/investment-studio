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
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from watchlist_app.repositories.sqlalchemy.manual_profiles import (
    SQLAlchemyInstrumentManualProfileRepository,
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
    local_materialization_source_cutoff,
    local_materialization_version,
    schedule_instrument_refresh_if_stale,
)


router = APIRouter()
settings = get_settings()
read_model_repository = SQLAlchemyReadModelRepository()
taxonomy_repository = SQLAlchemyTaxonomyRepository()
attribute_repository = SQLAlchemyInstrumentAttributeRepository()
manual_profile_repository = SQLAlchemyInstrumentManualProfileRepository()
instrument_repository = SQLAlchemyInstrumentRepository()
canonical_recalc_service = CanonicalRecalcService()


def _safe_file_segment(value: str | None, fallback: str = "file") -> str:
    raw = Path(value or "").name.strip()
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return normalized or fallback


def _instrument_document_dir(instrument_id: str) -> Path:
    return settings.document_storage_root / _safe_file_segment(instrument_id, fallback="instrument")


def _instrument_document_download_url(instrument_id: str, stored_file_name: str) -> str:
    return f"/api/instruments/{instrument_id}/documents/files/{stored_file_name}"


async def _persist_uploaded_document(file: UploadFile, stored_path: Path) -> int:
    temporary_path = stored_path.with_name(f".{stored_path.name}.uploading")
    total_bytes = 0
    try:
        with temporary_path.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > settings.document_upload_max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Uploaded file exceeds the configured "
                            f"{settings.document_upload_max_bytes}-byte limit."
                        ),
                    )
                output.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        temporary_path.replace(stored_path)
        return total_bytes
    finally:
        temporary_path.unlink(missing_ok=True)


def _schedule_instrument_refresh(
    session: Session,
    *,
    instrument_id: str,
    trigger_ref_type: str,
) -> None:
    chart_record = read_model_repository.get_chart(session, instrument_id)
    source_row = read_model_repository.find_any_watchlist_row_for_asset(session, instrument_id)
    schedule_instrument_refresh_if_stale(
        instrument_id=instrument_id,
        local_latest_date=latest_local_market_data_date(
            chart_payload=chart_record.payload_json if chart_record is not None else None,
            fallback_values=((source_row.last_nav_date if source_row is not None else None),),
        ),
        local_source_cutoff_at=local_materialization_source_cutoff(
            chart_record.source_cutoff_at if chart_record is not None else None,
            *(
                (source_row.last_fact_update_at,)
                if source_row is not None
                else ()
            ),
        ),
        local_materialization_version=local_materialization_version(
            getattr(chart_record, "materialization_version", None),
            getattr(source_row, "materialization_version", None),
        ),
        trigger_ref_type=trigger_ref_type,
        trigger_ref_id=instrument_id,
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
        "default_benchmark_instrument_id": None,
        "peer_baseline_instrument_ids": [],
    }


def _normalize_nav_settings_payload(payload: dict[str, object] | None) -> dict[str, object]:
    normalized = {
        **_default_nav_settings_payload(),
        **(payload or {}),
    }
    if normalized.get("nav_basis_preference") not in {"auto", "nav_with_dividend"}:
        normalized["nav_basis_preference"] = "auto"
    normalized["default_benchmark_instrument_id"] = (
        str(normalized.get("default_benchmark_instrument_id")).strip() or None
        if normalized.get("default_benchmark_instrument_id") is not None
        else None
    )
    normalized["peer_baseline_instrument_ids"] = [
        str(value).strip()
        for value in (normalized.get("peer_baseline_instrument_ids") or [])
        if str(value).strip()
    ]
    return normalized


def _validate_compare_instrument_ids(
    session: Session,
    *,
    instrument_id: str,
    default_benchmark_instrument_id: str | None,
    peer_baseline_instrument_ids: list[str],
) -> tuple[str | None, list[str]]:
    benchmark_instrument_id = default_benchmark_instrument_id
    if benchmark_instrument_id == instrument_id:
        benchmark_instrument_id = None
    if benchmark_instrument_id is not None and instrument_repository.get(session, benchmark_instrument_id) is None:
        raise HTTPException(status_code=404, detail="Default benchmark instrument not found")

    seen_peer_ids: set[str] = set()
    validated_peer_ids: list[str] = []
    for peer_instrument_id in peer_baseline_instrument_ids:
        if peer_instrument_id == instrument_id or peer_instrument_id in seen_peer_ids:
            continue
        if instrument_repository.get(session, peer_instrument_id) is None:
            raise HTTPException(status_code=404, detail=f"Peer baseline instrument not found: {peer_instrument_id}")
        seen_peer_ids.add(peer_instrument_id)
        validated_peer_ids.append(peer_instrument_id)
    return benchmark_instrument_id, validated_peer_ids


def _ensure_instrument_exists(session: Session, instrument_id: str) -> None:
    if instrument_repository.get(session, instrument_id) is None:
        raise HTTPException(status_code=404, detail="Instrument not found")


@router.get("/{instrument_id}/summary")
def get_fund_summary(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    _schedule_instrument_refresh(session, instrument_id=instrument_id, trigger_ref_type="instrument_summary_read")
    record = read_model_repository.get_summary(session, instrument_id)
    attributes = collapse_latest_attribute_values(
        attribute_repository.get_values_for_asset(session, instrument_id)
    )
    instrument = instrument_repository.get(session, instrument_id)
    payload = (
        serialize_payload(record.payload_json)
        if record is not None
        else default_fund_summary_payload(instrument_id, instrument_attributes=attributes)
    )
    if record is None and instrument is not None:
        payload["fund_name"] = instrument.instrument_name
        payload["ticker_or_isin"] = instrument.primary_identifier_value or instrument.instrument_id
    assignment = taxonomy_repository.get_assignment(session, instrument_id=instrument_id)
    node = (
        taxonomy_repository.get_node(session, node_id=str(assignment.node_id))
        if assignment is not None and assignment.node_id
        else None
    )
    taxonomy_context = build_taxonomy_context(node)
    merged = merge_summary_attributes(payload, attributes)
    if merged.get("management_firm_name") is None and instrument is not None:
        merged["management_firm_name"] = (
            str(instrument.metadata_json.get("management_firm_name") or "").strip() or None
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
            "Create the instrument in Database Dashboard, then add it from the shared registry."
        ),
    )


@router.get("/library")
def list_fund_library(
    session: Session = Depends(get_db_session),
) -> list[dict[str, object]]:
    return instrument_repository.list_library(session)


@router.get("/{instrument_id}/chart")
def get_fund_chart_data(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    _schedule_instrument_refresh(session, instrument_id=instrument_id, trigger_ref_type="instrument_chart_read")
    record = read_model_repository.get_chart(session, instrument_id)
    if record is None:
        return default_fund_chart_payload(instrument_id)
    return serialize_payload(record.payload_json)


@router.get("/{instrument_id}/performance")
def get_fund_performance_data(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    _schedule_instrument_refresh(session, instrument_id=instrument_id, trigger_ref_type="instrument_performance_read")
    record = read_model_repository.get_performance(session, instrument_id)
    if record is None:
        return default_fund_performance_payload()
    return serialize_payload(record.payload_json)


@router.get("/{instrument_id}/risk")
def get_fund_risk_data(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    _schedule_instrument_refresh(session, instrument_id=instrument_id, trigger_ref_type="instrument_risk_read")
    record = read_model_repository.get_risk(session, instrument_id)
    if record is None:
        return default_fund_risk_payload()
    return serialize_payload(record.payload_json)


@router.get("/{instrument_id}/exposure/summary")
def get_fund_exposure_summary_data(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    _schedule_instrument_refresh(session, instrument_id=instrument_id, trigger_ref_type="instrument_exposure_summary_read")
    record = read_model_repository.get_exposure_summary(session, instrument_id)
    if record is None:
        return default_fund_exposure_summary_payload()
    return serialize_payload(record.payload_json)


@router.get("/{instrument_id}/exposure/holdings")
def get_fund_exposure_holdings_data(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    _schedule_instrument_refresh(session, instrument_id=instrument_id, trigger_ref_type="instrument_exposure_holdings_read")
    record = read_model_repository.get_exposure_holdings(session, instrument_id)
    if record is None:
        return default_fund_exposure_holdings_payload()
    return serialize_payload(record.payload_json)


@router.get("/{instrument_id}/ratings")
def get_fund_rating_data(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    _schedule_instrument_refresh(session, instrument_id=instrument_id, trigger_ref_type="instrument_ratings_read")
    record = read_model_repository.get_rating(session, instrument_id)
    if record is None:
        return default_fund_rating_payload()
    return serialize_payload(record.payload_json)


@router.get("/{instrument_id}/people")
def get_fund_people_profile(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.get(session, instrument_id)
    payload = record.people_payload_json if record is not None else _default_people_payload()
    return serialize_payload(payload)


@router.put("/{instrument_id}/people")
def upsert_fund_people_profile(
    instrument_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.upsert(
        session,
        instrument_id=instrument_id,
        people_payload_json=payload.payload,
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(record.people_payload_json)


@router.get("/{instrument_id}/strategy")
def get_fund_strategy_profile(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.get(session, instrument_id)
    payload = record.strategy_payload_json if record is not None else _default_strategy_payload()
    return serialize_payload(payload)


@router.put("/{instrument_id}/strategy")
def upsert_fund_strategy_profile(
    instrument_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.upsert(
        session,
        instrument_id=instrument_id,
        strategy_payload_json=payload.payload,
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(record.strategy_payload_json)


@router.get("/{instrument_id}/price")
def get_fund_price_profile(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.get(session, instrument_id)
    payload = record.price_payload_json if record is not None else _default_price_payload()
    return serialize_payload(payload)


@router.put("/{instrument_id}/price")
def upsert_fund_price_profile(
    instrument_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.upsert(
        session,
        instrument_id=instrument_id,
        price_payload_json=payload.payload,
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(record.price_payload_json)


@router.get("/{instrument_id}/documents")
def get_fund_documents_profile(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.get(session, instrument_id)
    payload = record.documents_payload_json if record is not None else _default_documents_payload()
    return serialize_payload(payload)


@router.put("/{instrument_id}/documents")
def upsert_fund_documents_profile(
    instrument_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.upsert(
        session,
        instrument_id=instrument_id,
        documents_payload_json=payload.payload,
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(record.documents_payload_json)


@router.post("/{instrument_id}/documents/upload")
async def upload_fund_document(
    instrument_id: str,
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
    _ensure_instrument_exists(session, instrument_id)
    original_file_name = _safe_file_segment(file.filename, fallback="uploaded-document")
    instrument_dir = _instrument_document_dir(instrument_id)
    instrument_dir.mkdir(parents=True, exist_ok=True)
    stored_file_name = f"{uuid4().hex}-{original_file_name}"
    stored_path = instrument_dir / stored_file_name
    file_size = await _persist_uploaded_document(file, stored_path)

    try:
        uploaded_at = datetime.now(timezone.utc).isoformat()
        download_url = _instrument_document_download_url(instrument_id, stored_file_name)
        record = manual_profile_repository.get(session, instrument_id)
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
            "file_size": file_size,
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
            instrument_id=instrument_id,
            documents_payload_json=next_payload,
            updated_by=updated_by,
        )
        session.commit()
    except Exception:
        session.rollback()
        stored_path.unlink(missing_ok=True)
        raise
    return serialize_payload(updated_record.documents_payload_json)


@router.get("/{instrument_id}/documents/files/{stored_file_name}")
def download_fund_document(
    instrument_id: str,
    stored_file_name: str,
    session: Session = Depends(get_db_session),
) -> FileResponse:
    _ensure_instrument_exists(session, instrument_id)
    safe_stored_file_name = _safe_file_segment(stored_file_name)
    stored_path = _instrument_document_dir(instrument_id) / safe_stored_file_name
    if not stored_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found.")
    download_name = safe_stored_file_name.split("-", 1)[1] if "-" in safe_stored_file_name else safe_stored_file_name
    return FileResponse(path=stored_path, filename=download_name)


@router.get("/{instrument_id}/research")
def get_fund_research_profile(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.get(session, instrument_id)
    payload = record.research_payload_json if record is not None else _default_research_payload()
    return serialize_payload(_normalize_research_payload(payload))


@router.put("/{instrument_id}/research")
def upsert_fund_research_profile(
    instrument_id: str,
    payload: ManualProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    normalized_payload = _normalize_research_payload(payload.payload)
    record = manual_profile_repository.upsert(
        session,
        instrument_id=instrument_id,
        research_payload_json=normalized_payload,
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(record.research_payload_json)


@router.get("/{instrument_id}/nav-series")
def get_fund_nav_series(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    return canonical_recalc_service.build_nav_series_payload(session, instrument_id=instrument_id)


@router.put("/{instrument_id}/nav-series")
def upsert_fund_nav_series(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    raise HTTPException(
        status_code=409,
        detail=(
            "Canonical NAV series is now owned by Database Dashboard. "
            "Use Database Dashboard to import or edit shared market data."
        ),
    )


@router.get("/{instrument_id}/nav-settings")
def get_fund_nav_settings(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.get(session, instrument_id)
    return serialize_payload(
        _normalize_nav_settings_payload(record.nav_settings_json if record is not None else None)
    )


@router.put("/{instrument_id}/nav-settings")
def upsert_fund_nav_settings(
    instrument_id: str,
    payload: NavSettingsUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = manual_profile_repository.get(session, instrument_id)
    current_payload = _normalize_nav_settings_payload(
        record.nav_settings_json if record is not None else None
    )

    next_payload = dict(current_payload)
    provided_fields = payload.model_fields_set
    if "nav_basis_preference" in provided_fields:
        next_payload["nav_basis_preference"] = payload.nav_basis_preference
    if "default_benchmark_instrument_id" in provided_fields:
        next_payload["default_benchmark_instrument_id"] = payload.default_benchmark_instrument_id
    if "peer_baseline_instrument_ids" in provided_fields:
        next_payload["peer_baseline_instrument_ids"] = payload.peer_baseline_instrument_ids or []

    benchmark_instrument_id, peer_instrument_ids = _validate_compare_instrument_ids(
        session,
        instrument_id=instrument_id,
        default_benchmark_instrument_id=(
            str(next_payload.get("default_benchmark_instrument_id")).strip() or None
            if next_payload.get("default_benchmark_instrument_id") is not None
            else None
        ),
        peer_baseline_instrument_ids=[
            str(value).strip()
            for value in (next_payload.get("peer_baseline_instrument_ids") or [])
            if str(value).strip()
        ],
    )
    next_payload["default_benchmark_instrument_id"] = benchmark_instrument_id
    next_payload["peer_baseline_instrument_ids"] = peer_instrument_ids

    updated_record = manual_profile_repository.upsert(
        session,
        instrument_id=instrument_id,
        nav_settings_json=_normalize_nav_settings_payload(next_payload),
        updated_by=payload.updated_by,
    )
    session.commit()
    return serialize_payload(_normalize_nav_settings_payload(updated_record.nav_settings_json))


@router.post("/{instrument_id}/nav-refresh")
def trigger_fund_nav_refresh(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    raise HTTPException(
        status_code=409,
        detail=(
            "NAV refresh now runs from Database Dashboard. "
            "Use Database Dashboard to trigger the shared market data refresh."
        ),
    )
