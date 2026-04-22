from __future__ import annotations

from sqlalchemy.orm import Session

from watchlist_app.repositories.sqlalchemy.assets import SQLAlchemyAssetRepository
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument,
)


asset_repository = SQLAlchemyAssetRepository()
read_model_repository = SQLAlchemyReadModelRepository()


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
    requested_asset_id: str,
    canonical_asset_id: str,
    asset_name: str,
    asset_type: str,
    primary_identifier: str | None,
    detail_view_type: str,
    support_reason: str = "detail_ready",
) -> dict[str, object]:
    return {
        "requested_asset_id": requested_asset_id,
        "canonical_asset_id": canonical_asset_id,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "primary_identifier": primary_identifier,
        "detail_view_type": detail_view_type,
        "detail_subject_id": canonical_asset_id,
        "detail_supported": True,
        "support_reason": support_reason,
    }


def _build_stub_detail_response(
    *,
    requested_asset_id: str,
    canonical_asset_id: str,
    asset_name: str,
    asset_type: str,
    primary_identifier: str | None,
    support_reason: str,
) -> dict[str, object]:
    return {
        "requested_asset_id": requested_asset_id,
        "canonical_asset_id": canonical_asset_id,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "primary_identifier": primary_identifier,
        "detail_view_type": asset_type,
        "detail_subject_id": None,
        "detail_supported": False,
        "support_reason": support_reason,
    }


def resolve_watchlist_instrument(
    session: Session,
    *,
    asset_id: str,
) -> dict[str, object] | None:
    requested_asset_id = asset_id.strip()
    if not requested_asset_id:
        return None

    local_asset = asset_repository.get(session, requested_asset_id)
    watchlist_row = read_model_repository.find_any_watchlist_row_for_asset(
        session,
        requested_asset_id,
    )

    try:
        shared_record = get_shared_instrument(requested_asset_id)
    except SharedInstrumentRegistryError:
        if local_asset is not None:
            return _build_supported_detail_response(
                requested_asset_id=requested_asset_id,
                canonical_asset_id=local_asset.asset_id,
                asset_name=local_asset.asset_name,
                asset_type=local_asset.asset_type,
                primary_identifier=local_asset.primary_identifier_value,
                detail_view_type=local_asset.detail_view_type,
                support_reason="detail_ready_local_cache",
            )
        if watchlist_row is not None:
            return _build_stub_detail_response(
                requested_asset_id=requested_asset_id,
                canonical_asset_id=watchlist_row.asset_id,
                asset_name=watchlist_row.asset_name,
                asset_type=watchlist_row.asset_type,
                primary_identifier=watchlist_row.ticker_or_isin,
                support_reason="shared_registry_unavailable",
            )
        raise

    if shared_record is not None:
        asset_type = str(shared_record.get("asset_type") or "other")
        canonical_asset_id = str(shared_record.get("asset_id") or requested_asset_id)
        if asset_type == "fund":
            local_asset = asset_repository.upsert_from_shared_instrument(
                session,
                shared_instrument=shared_record,
                detail_view_type=(
                    local_asset.detail_view_type
                    if local_asset is not None and local_asset.detail_view_type
                    else "fund"
                ),
            )
        else:
            local_asset = asset_repository.get(session, canonical_asset_id) or local_asset
        if local_asset is not None:
            return _build_supported_detail_response(
                requested_asset_id=requested_asset_id,
                canonical_asset_id=canonical_asset_id,
                asset_name=str(shared_record.get("asset_name") or local_asset.asset_name),
                asset_type=asset_type,
                primary_identifier=_primary_identifier(shared_record) or local_asset.primary_identifier_value,
                detail_view_type=local_asset.detail_view_type,
            )
        cached_row = watchlist_row
        if cached_row is None and canonical_asset_id != requested_asset_id:
            cached_row = read_model_repository.find_any_watchlist_row_for_asset(
                session,
                canonical_asset_id,
            )
        return _build_stub_detail_response(
            requested_asset_id=requested_asset_id,
            canonical_asset_id=canonical_asset_id,
            asset_name=str(
                shared_record.get("asset_name")
                or (cached_row.asset_name if cached_row is not None else requested_asset_id)
            ),
            asset_type=asset_type,
            primary_identifier=_primary_identifier(shared_record)
            or (cached_row.ticker_or_isin if cached_row is not None else None),
            support_reason="asset_detail_missing_local_overlay",
        )

    if local_asset is not None:
        return _build_supported_detail_response(
            requested_asset_id=requested_asset_id,
            canonical_asset_id=requested_asset_id,
            asset_name=local_asset.asset_name,
            asset_type=local_asset.asset_type,
            primary_identifier=local_asset.primary_identifier_value,
            detail_view_type=local_asset.detail_view_type,
        )
    if watchlist_row is not None:
        return _build_stub_detail_response(
            requested_asset_id=requested_asset_id,
            canonical_asset_id=watchlist_row.asset_id,
            asset_name=watchlist_row.asset_name,
            asset_type=watchlist_row.asset_type,
            primary_identifier=watchlist_row.ticker_or_isin,
            support_reason="asset_detail_missing_local_overlay",
        )
    return None
