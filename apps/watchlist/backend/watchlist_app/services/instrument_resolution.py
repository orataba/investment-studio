from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.watchlists import InstrumentTaxonomyAssignment, InstrumentTaxonomyNode
from watchlist_app.reference_data.instrument_taxonomy import INSTRUMENT_TAXONOMY_CODE
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
    "crypto",
}
EQUITY_MARKET_DEFAULT_TAXONOMY_NODES = {
    "XNAS": "equity-us-unclassified",
    "XNYS": "equity-us-unclassified",
    "XASE": "equity-us-unclassified",
    "ARCX": "equity-us-unclassified",
    "BATS": "equity-us-unclassified",
    "XHKG": "equity-hk-unclassified",
    "XSHG": "equity-cn-a-unclassified",
    "XSHE": "equity-cn-a-unclassified",
    "XLON": "equity-eu-unclassified",
    "XETR": "equity-eu-unclassified",
    "XPAR": "equity-eu-unclassified",
    "XAMS": "equity-eu-unclassified",
    "XMIL": "equity-eu-unclassified",
    "XSWX": "equity-eu-unclassified",
}


def equity_default_taxonomy_node(instrument: object) -> str | None:
    metadata = getattr(instrument, "metadata_json", None)
    if not isinstance(metadata, dict):
        return None
    exchange_code = str(metadata.get("exchange_code") or "").strip().upper()
    return EQUITY_MARKET_DEFAULT_TAXONOMY_NODES.get(exchange_code)


def local_detail_view_type(instrument_type: str) -> str | None:
    normalized = instrument_type.strip().lower()
    return normalized if normalized in LOCAL_DETAIL_INSTRUMENT_TYPES else None


def _default_taxonomy_assignment(shared_record: dict[str, object]) -> tuple[str, str] | None:
    instrument_type = str(shared_record.get("instrument_type") or "other").strip().lower()
    if instrument_type == "equity":
        exchange_code = str(shared_record.get("exchange_code") or "").strip().upper()
        node_id = EQUITY_MARKET_DEFAULT_TAXONOMY_NODES.get(exchange_code)
        if node_id is None:
            raise ValueError(f"Unsupported Registry equity exchange_code: {exchange_code or 'missing'}")
        return node_id, f"registry_market:{exchange_code}"
    if instrument_type == "crypto":
        return "crypto-native", "registry_type:crypto"
    return None


def sync_local_instruments(
    session: Session,
    shared_records: list[dict[str, object]],
) -> list[InstrumentDetail]:
    """Reconcile current identities in one batch, writing only actual changes."""
    supported = [
        record for record in shared_records
        if local_detail_view_type(str(record.get("instrument_type") or "")) is not None
    ]
    if not supported:
        return []
    instrument_ids = [str(record["instrument_id"]) for record in supported]
    # Keep strong references throughout the batch: Session.get in the canonical
    # upsert then reuses the identity map instead of issuing one SELECT per row.
    local_records = list(session.scalars(select(InstrumentDetail).where(
        InstrumentDetail.instrument_id.in_(instrument_ids),
    )))
    defaults = {
        str(record["instrument_id"]): assignment
        for record in supported
        if (assignment := _default_taxonomy_assignment(record)) is not None
    }
    assigned_ids: set[str] = set()
    default_node_ids: set[str] = set()
    if defaults:
        assigned_ids = set(session.scalars(select(InstrumentTaxonomyAssignment.instrument_id).where(
            InstrumentTaxonomyAssignment.instrument_id.in_(defaults),
            InstrumentTaxonomyAssignment.taxonomy_code == INSTRUMENT_TAXONOMY_CODE,
        )))
        default_node_ids = set(session.scalars(select(InstrumentTaxonomyNode.node_id).where(
            InstrumentTaxonomyNode.node_id.in_({value[0] for value in defaults.values()}),
        )))
    synchronized: list[InstrumentDetail] = []
    for shared_record in supported:
        instrument_id = str(shared_record["instrument_id"])
        instrument_type = str(shared_record["instrument_type"]).strip().lower()
        default = defaults.get(instrument_id)
        if default is not None and default[0] not in default_node_ids:
            raise ValueError(f"Watchlist {instrument_type} taxonomy node is missing: {default[0]}")
        local_instrument = instrument_repository.upsert_from_shared_instrument(
            session,
            shared_instrument=shared_record,
            detail_view_type=instrument_type,
        )
        if default is not None and instrument_id not in assigned_ids:
            taxonomy_repository.upsert_assignment(
                session,
                instrument_id=instrument_id,
                node_id=default[0],
                source_record_id=default[1],
            )
            assigned_ids.add(instrument_id)
        synchronized.append(local_instrument)
    del local_records
    return synchronized


def sync_local_instrument(
    session: Session,
    shared_record: dict[str, object],
):
    synchronized = sync_local_instruments(session, [shared_record])
    return synchronized[0] if synchronized else None


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


def _build_unsupported_detail_response(
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
        return _build_unsupported_detail_response(
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
