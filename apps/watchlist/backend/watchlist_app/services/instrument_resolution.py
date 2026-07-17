from __future__ import annotations

from sqlalchemy.orm import Session

from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument,
)


instrument_repository = SQLAlchemyInstrumentRepository()
read_model_repository = SQLAlchemyReadModelRepository()
LOCAL_DETAIL_INSTRUMENT_TYPES = {"fund", "etf", "equity", "index"}


def _local_detail_view_type(instrument_type: str) -> str | None:
    normalized = instrument_type.strip().lower()
    if normalized == "fund":
        return "fund"
    if normalized in {"etf", "equity", "index"}:
        return "listed"
    return None


def _primary_identifier(shared_record: dict[str, object] | None) -> str | None:
    if not isinstance(shared_record, dict):
        return None
    identifiers = shared_record.get("identifiers")
    if not isinstance(identifiers, list):
        return None
    primary = next(
        (
            item
            for item in identifiers
            if isinstance(item, dict) and bool(item.get("is_primary"))
        ),
        None,
    )
    fallback = next((item for item in identifiers if isinstance(item, dict)), None)
    candidate = primary or fallback
    if not isinstance(candidate, dict):
        return None
    value = str(candidate.get("identifier_value") or "").strip()
    return value or None


def _build_supported_detail_response(
    *,
    requested_instrument_id: str,
    canonical_instrument_id: str,
    instrument_name: str,
    instrument_type: str,
    primary_identifier: str | None,
    detail_view_type: str,
    support_reason: str = "detail_ready",
    corporate_actions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "requested_instrument_id": requested_instrument_id,
        "canonical_instrument_id": canonical_instrument_id,
        "instrument_name": instrument_name,
        "instrument_type": instrument_type,
        "primary_identifier": primary_identifier,
        "detail_view_type": detail_view_type,
        "detail_subject_id": canonical_instrument_id,
        "detail_supported": True,
        "support_reason": support_reason,
        "corporate_actions": corporate_actions or [],
    }


def _build_stub_detail_response(
    *,
    requested_instrument_id: str,
    canonical_instrument_id: str,
    instrument_name: str,
    instrument_type: str,
    primary_identifier: str | None,
    support_reason: str,
    corporate_actions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "requested_instrument_id": requested_instrument_id,
        "canonical_instrument_id": canonical_instrument_id,
        "instrument_name": instrument_name,
        "instrument_type": instrument_type,
        "primary_identifier": primary_identifier,
        "detail_view_type": instrument_type,
        "detail_subject_id": None,
        "detail_supported": False,
        "support_reason": support_reason,
        "corporate_actions": corporate_actions or [],
    }


def resolve_watchlist_instrument(
    session: Session,
    *,
    instrument_id: str,
) -> dict[str, object] | None:
    requested_instrument_id = instrument_id.strip()
    if not requested_instrument_id:
        return None

    local_asset = instrument_repository.get(session, requested_instrument_id)
    watchlist_row = read_model_repository.find_any_watchlist_row_for_asset(
        session,
        requested_instrument_id,
    )

    try:
        shared_record = get_shared_instrument(requested_instrument_id)
    except SharedInstrumentRegistryError:
        if local_asset is not None:
            return _build_supported_detail_response(
                requested_instrument_id=requested_instrument_id,
                canonical_instrument_id=local_asset.instrument_id,
                instrument_name=local_asset.instrument_name,
                instrument_type=local_asset.instrument_type,
                primary_identifier=local_asset.primary_identifier_value,
                detail_view_type=local_asset.detail_view_type,
                support_reason="detail_ready_local_cache",
            )
        if watchlist_row is not None:
            return _build_stub_detail_response(
                requested_instrument_id=requested_instrument_id,
                canonical_instrument_id=watchlist_row.instrument_id,
                instrument_name=watchlist_row.instrument_name,
                instrument_type=watchlist_row.instrument_type,
                primary_identifier=watchlist_row.ticker_or_isin,
                support_reason="shared_registry_unavailable",
            )
        raise

    if shared_record is not None:
        instrument_type = str(shared_record.get("instrument_type") or "other")
        canonical_instrument_id = str(shared_record.get("instrument_id") or requested_instrument_id)
        detail_view_type = _local_detail_view_type(instrument_type)
        if detail_view_type is not None:
            local_asset = instrument_repository.upsert_from_shared_instrument(
                session,
                shared_instrument=shared_record,
                detail_view_type=detail_view_type,
            )
        else:
            local_asset = instrument_repository.get(session, canonical_instrument_id) or local_asset
        if local_asset is not None:
            return _build_supported_detail_response(
                requested_instrument_id=requested_instrument_id,
                canonical_instrument_id=canonical_instrument_id,
                instrument_name=str(shared_record.get("instrument_name") or local_asset.instrument_name),
                instrument_type=instrument_type,
                primary_identifier=_primary_identifier(shared_record) or local_asset.primary_identifier_value,
                detail_view_type=local_asset.detail_view_type,
                corporate_actions=(
                    list(shared_record.get("corporate_actions", []))
                    if isinstance(shared_record.get("corporate_actions"), list)
                    else []
                ),
            )
        cached_row = watchlist_row
        if cached_row is None and canonical_instrument_id != requested_instrument_id:
            cached_row = read_model_repository.find_any_watchlist_row_for_asset(
                session,
                canonical_instrument_id,
            )
        return _build_stub_detail_response(
            requested_instrument_id=requested_instrument_id,
            canonical_instrument_id=canonical_instrument_id,
            instrument_name=str(
                shared_record.get("instrument_name")
                or (cached_row.instrument_name if cached_row is not None else requested_instrument_id)
            ),
            instrument_type=instrument_type,
            primary_identifier=_primary_identifier(shared_record)
            or (cached_row.ticker_or_isin if cached_row is not None else None),
            support_reason="instrument_detail_missing_local_overlay",
            corporate_actions=(
                list(shared_record.get("corporate_actions", []))
                if isinstance(shared_record.get("corporate_actions"), list)
                else []
            ),
        )

    if local_asset is not None:
        return _build_supported_detail_response(
            requested_instrument_id=requested_instrument_id,
            canonical_instrument_id=requested_instrument_id,
            instrument_name=local_asset.instrument_name,
            instrument_type=local_asset.instrument_type,
            primary_identifier=local_asset.primary_identifier_value,
            detail_view_type=local_asset.detail_view_type,
        )
    if watchlist_row is not None:
        return _build_stub_detail_response(
            requested_instrument_id=requested_instrument_id,
            canonical_instrument_id=watchlist_row.instrument_id,
            instrument_name=watchlist_row.instrument_name,
            instrument_type=watchlist_row.instrument_type,
            primary_identifier=watchlist_row.ticker_or_isin,
            support_reason="instrument_detail_missing_local_overlay",
        )
    return None
