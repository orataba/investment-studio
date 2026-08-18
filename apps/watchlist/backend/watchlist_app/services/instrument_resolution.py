from __future__ import annotations

from sqlalchemy.orm import Session

from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
from watchlist_app.services.shared_instrument_registry import get_shared_instrument


instrument_repository = SQLAlchemyInstrumentRepository()
taxonomy_repository = SQLAlchemyTaxonomyRepository()
LOCAL_DETAIL_INSTRUMENT_TYPES = {
    "public_fund",
    "private_fund",
    "etf",
    "equity",
    "index",
}
EQUITY_EXCHANGE_TAXONOMY_NODES = {
    "XNAS": "equity-exchange-xnas",
    "XNYS": "equity-exchange-xnys",
    "XASE": "equity-exchange-xase",
    "XHKG": "equity-exchange-xhkg",
    "XSHG": "equity-exchange-xshg",
    "XSHE": "equity-exchange-xshe",
}


def equity_exchange_taxonomy_node(instrument: object) -> str | None:
    metadata = getattr(instrument, "metadata_json", None)
    if not isinstance(metadata, dict):
        return None
    exchange_code = str(metadata.get("exchange_code") or "").strip().upper()
    return EQUITY_EXCHANGE_TAXONOMY_NODES.get(exchange_code)


def local_detail_view_type(instrument_type: str) -> str | None:
    normalized = instrument_type.strip().lower()
    return normalized if normalized in LOCAL_DETAIL_INSTRUMENT_TYPES else None


def sync_local_instrument(
    session: Session,
    shared_record: dict[str, object],
):
    instrument_type = str(shared_record.get("instrument_type") or "other").strip().lower()
    detail_view_type = local_detail_view_type(instrument_type)
    if detail_view_type is None:
        return None
    local_instrument = instrument_repository.upsert_from_shared_instrument(
        session,
        shared_instrument=shared_record,
        detail_view_type=detail_view_type,
    )
    if instrument_type == "equity":
        exchange_code = str(shared_record.get("exchange_code") or "").strip().upper()
        node_id = EQUITY_EXCHANGE_TAXONOMY_NODES.get(exchange_code)
        if node_id is None:
            raise ValueError(f"Unsupported Registry equity exchange_code: {exchange_code or 'missing'}")
        if taxonomy_repository.get_node(session, node_id=node_id) is None:
            raise ValueError(f"Watchlist exchange taxonomy node is missing: {node_id}")
        taxonomy_repository.upsert_assignment(
            session,
            instrument_id=local_instrument.instrument_id,
            node_id=node_id,
            source_record_id=f"registry_exchange:{exchange_code}",
        )
    return local_instrument


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

    shared_record = get_shared_instrument(requested_instrument_id)
    if shared_record is None:
        return None

    instrument_type = str(shared_record.get("instrument_type") or "other")
    canonical_instrument_id = str(shared_record.get("instrument_id") or requested_instrument_id)
    detail_view_type = local_detail_view_type(instrument_type)
    corporate_actions = (
        list(shared_record.get("corporate_actions", []))
        if isinstance(shared_record.get("corporate_actions"), list)
        else []
    )
    if detail_view_type is None:
        return _build_stub_detail_response(
            requested_instrument_id=requested_instrument_id,
            canonical_instrument_id=canonical_instrument_id,
            instrument_name=str(shared_record.get("instrument_name") or requested_instrument_id),
            instrument_type=instrument_type,
            primary_identifier=_primary_identifier(shared_record),
            support_reason="instrument_type_not_supported",
            corporate_actions=corporate_actions,
        )

    local_instrument = sync_local_instrument(session, shared_record)
    if local_instrument is None:
        raise RuntimeError("Supported Watchlist instrument failed to materialize")
    return _build_supported_detail_response(
        requested_instrument_id=requested_instrument_id,
        canonical_instrument_id=canonical_instrument_id,
        instrument_name=str(shared_record.get("instrument_name") or local_instrument.instrument_name),
        instrument_type=instrument_type,
        primary_identifier=_primary_identifier(shared_record) or local_instrument.primary_identifier_value,
        detail_view_type=local_instrument.detail_view_type,
        corporate_actions=corporate_actions,
    )
