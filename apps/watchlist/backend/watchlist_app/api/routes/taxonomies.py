from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import TaxonomyAssignmentUpsertRequest
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
from watchlist_app.reference_data.fund_taxonomy import FUND_TAXONOMY_CODE
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.services.fund_taxonomy import (
    build_taxonomy_context,
    merge_taxonomy_attributes,
    taxonomy_tree_payload,
)
from watchlist_app.services.read_models import collapse_latest_attribute_values


router = APIRouter()
instrument_repository = SQLAlchemyInstrumentRepository()
attribute_repository = SQLAlchemyInstrumentAttributeRepository()
read_model_repository = SQLAlchemyReadModelRepository()
taxonomy_repository = SQLAlchemyTaxonomyRepository()
canonical_recalc_service = CanonicalRecalcService()


def _require_taxonomy_asset(session: Session, instrument_id: str):
    instrument = instrument_repository.get(session, instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    if str(instrument.instrument_type or "").strip().lower() not in {"fund", "etf", "index"}:
        raise HTTPException(status_code=400, detail="Taxonomy is only available for fund, ETF, and index instruments.")
    return instrument


def _taxonomy_context_for_asset(
    session: Session,
    *,
    instrument_id: str,
) -> dict[str, object]:
    assignment = taxonomy_repository.get_assignment(session, instrument_id=instrument_id)
    node = (
        taxonomy_repository.get_node(session, node_id=str(assignment.node_id))
        if assignment is not None and assignment.node_id
        else None
    )
    return build_taxonomy_context(node)


def _sync_instrument_context(
    session: Session,
    *,
    instrument_id: str,
    taxonomy_context: dict[str, object],
) -> None:
    raw_attributes = collapse_latest_attribute_values(
        attribute_repository.get_values_for_asset(session, instrument_id)
    )
    read_model_repository.set_attributes_for_asset(
        session,
        instrument_id=instrument_id,
        attributes=merge_taxonomy_attributes(
            taxonomy_context=taxonomy_context,
            instrument_attributes=raw_attributes,
        ),
        touched_at=datetime.now(UTC).replace(microsecond=0),
    )
    summary_record = read_model_repository.get_summary(session, instrument_id)
    if summary_record is not None and isinstance(summary_record.payload_json, dict):
        summary_record.payload_json = {
            **summary_record.payload_json,
            "taxonomy": taxonomy_context,
        }
        session.flush()


@router.get("/fund-taxonomy")
def get_fund_taxonomy_tree(
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return taxonomy_tree_payload(
        taxonomy_repository.list_nodes(
            session,
            taxonomy_code=FUND_TAXONOMY_CODE,
        )
    )


@router.get("/fund-taxonomy/instruments/{instrument_id}")
def get_fund_taxonomy_assignment(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_taxonomy_asset(session, instrument_id)
    return _taxonomy_context_for_asset(session, instrument_id=instrument_id) | {"instrument_id": instrument_id}


@router.put("/fund-taxonomy/instruments/{instrument_id}")
def update_fund_taxonomy_assignment(
    instrument_id: str,
    payload: TaxonomyAssignmentUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_taxonomy_asset(session, instrument_id)
    node_id = str(payload.node_id or "").strip() or None
    if node_id is not None:
        node = taxonomy_repository.get_node(session, node_id=node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="Fund taxonomy node not found")
        if node.taxonomy_code != FUND_TAXONOMY_CODE:
            raise HTTPException(status_code=400, detail="Invalid taxonomy node")
    taxonomy_repository.upsert_assignment(
        session,
        instrument_id=instrument_id,
        taxonomy_code=FUND_TAXONOMY_CODE,
        node_id=node_id,
        source_record_id=str(payload.updated_by or "terminal_ui"),
    )
    taxonomy_context = _taxonomy_context_for_asset(session, instrument_id=instrument_id)
    _sync_instrument_context(session, instrument_id=instrument_id, taxonomy_context=taxonomy_context)
    execution = canonical_recalc_service.execute_recalc(
        session,
        instrument_id=instrument_id,
        job_type="performance",
        trigger_type="taxonomy_assignment",
        trigger_ref_type="instrument_taxonomy_assignment",
        trigger_ref_id=node_id,
    )
    session.commit()
    return taxonomy_context | {
        "instrument_id": instrument_id,
        "updated": True,
        "recalculated": True,
        "execution": execution,
    }
