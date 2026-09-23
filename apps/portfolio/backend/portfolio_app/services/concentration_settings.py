"""Dated limits belong to the portfolio, not to a browser's selected grouping."""
from copy import deepcopy
from datetime import UTC, date, datetime

from fastapi import HTTPException
from sqlalchemy import func, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from portfolio_app.api.concentration_contracts import ConcentrationSettingsUpdate
from portfolio_app.db.models import (
    ConcentrationPolicyRevisionModel, DerivativeContractRecordModel,
    PortfolioRecordModel, TaxonomyNodeRecordModel, TaxonomyRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.portfolio_access import actor


def empty_settings(portfolio_id: str) -> dict:
    return {"portfolio_id": portfolio_id, "revision": 0, "latest_revision": 0, "effective_from": None,
            "enabled_taxonomy_ids": [], "limits": [], "fcn_allocations": []}


def serialize_settings(row: ConcentrationPolicyRevisionModel) -> dict:
    settings = row.settings_json
    return {"portfolio_id": row.portfolio_id, "revision": row.revision, "latest_revision": row.revision,
            "effective_from": row.effective_from.isoformat(),
            **{key: deepcopy(settings.get(key, [])) for key in ("enabled_taxonomy_ids", "limits", "fcn_allocations")}}


def read_concentration_settings(portfolio_id: str, *, as_of_date: date | None = None) -> dict:
    with get_session_factory()() as session:
        statement = select(ConcentrationPolicyRevisionModel).where(ConcentrationPolicyRevisionModel.portfolio_id == portfolio_id)
        if as_of_date is not None:
            statement = statement.where(ConcentrationPolicyRevisionModel.effective_from <= as_of_date).order_by(
                ConcentrationPolicyRevisionModel.effective_from.desc(), ConcentrationPolicyRevisionModel.revision.desc())
        else:
            statement = statement.order_by(ConcentrationPolicyRevisionModel.revision.desc())
        selected = aliased(ConcentrationPolicyRevisionModel, statement.limit(1).subquery())
        latest = select(func.max(ConcentrationPolicyRevisionModel.revision)).where(
            ConcentrationPolicyRevisionModel.portfolio_id == portfolio_id).scalar_subquery()
        # Selected-date content and the optimistic token must come from one
        # database snapshot, including dates before the first policy exists.
        selected_row = session.execute(select(selected, latest).select_from(PortfolioRecordModel)
            .outerjoin(selected, true()).where(PortfolioRecordModel.portfolio_id == portfolio_id)).first()
        if selected_row is None:
            raise HTTPException(404, "Portfolio not found.")
        row, latest_revision = selected_row
        result = serialize_settings(row) if row else empty_settings(portfolio_id)
        result["latest_revision"] = latest_revision or 0
        return result


def save_concentration_settings(portfolio_id: str, request: ConcentrationSettingsUpdate) -> dict:
    with get_session_factory()() as session:
        try:
            result = save_concentration_settings_in_session(session, portfolio_id, request)
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "Concentration settings changed. Reload before saving.") from error
        return result


def save_concentration_settings_in_session(session: Session, portfolio_id: str, request: ConcentrationSettingsUpdate) -> dict:
    """Join an existing configuration transaction without committing or invalidating research."""
    # The portfolio lock also serializes two first saves, before a revision row exists.
    portfolio = session.scalar(select(PortfolioRecordModel).where(
        PortfolioRecordModel.portfolio_id == portfolio_id).with_for_update())
    if portfolio is None:
        raise HTTPException(404, "Portfolio not found.")
    latest = session.scalar(select(ConcentrationPolicyRevisionModel).where(
        ConcentrationPolicyRevisionModel.portfolio_id == portfolio_id).order_by(ConcentrationPolicyRevisionModel.revision.desc()).limit(1))
    revision = latest.revision if latest else 0
    if request.expected_revision != revision:
        raise HTTPException(409, "Concentration settings changed. Reload before saving.")
    taxonomies = {row.taxonomy_id: row for row in session.scalars(select(TaxonomyRecordModel).where(TaxonomyRecordModel.portfolio_id == portfolio_id))}
    nodes = {row.taxonomy_node_id: row for row in session.scalars(select(TaxonomyNodeRecordModel).where(TaxonomyNodeRecordModel.taxonomy_id.in_(taxonomies)))}
    contracts = {row.derivative_contract_id: row for row in session.scalars(select(DerivativeContractRecordModel).where(DerivativeContractRecordModel.portfolio_id == portfolio_id))}
    # Full-document edits start from the displayed date, which may precede a
    # scheduled future revision. Archived references are compared to that same
    # document, while concurrency still compares against the latest write.
    selected = session.scalar(select(ConcentrationPolicyRevisionModel).where(
        ConcentrationPolicyRevisionModel.portfolio_id == portfolio_id,
        ConcentrationPolicyRevisionModel.effective_from <= request.effective_from).order_by(
            ConcentrationPolicyRevisionModel.effective_from.desc(), ConcentrationPolicyRevisionModel.revision.desc()).limit(1))
    previous = selected.settings_json if selected else {}
    saved_limits = {(item["scope"], item.get("taxonomy_id"), item["entity_id"]): item for item in previous.get("limits", [])}
    saved_allocations = {item["contract_id"]: item for item in previous.get("fcn_allocations", [])}
    for taxonomy_id in request.enabled_taxonomy_ids:
        if taxonomy_id in previous.get("enabled_taxonomy_ids", []):
            continue
        if taxonomy_id not in taxonomies or taxonomies[taxonomy_id].status != "active":
            raise HTTPException(422, "Select an active taxonomy belonging to this portfolio.")
    for limit in request.limits:
        if limit.limit_weight is None:
            continue
        # Retaining an archived reference must not block unrelated edits. Changing
        # that reference still requires an active member belonging to the portfolio.
        if limit.model_dump(mode="json") == saved_limits.get((limit.scope, limit.taxonomy_id, limit.entity_id)):
            continue
        if limit.scope == "taxonomy":
            if limit.taxonomy_id not in taxonomies or taxonomies[limit.taxonomy_id].status != "active":
                raise HTTPException(422, "Select an active taxonomy belonging to this portfolio.")
            if limit.entity_id not in nodes or nodes[limit.entity_id].taxonomy_id != limit.taxonomy_id or nodes[limit.entity_id].status != "active":
                raise HTTPException(422, "The concentration node must belong to the selected taxonomy.")
        elif limit.scope == "fcn":
            if limit.entity_id not in contracts or contracts[limit.entity_id].contract_type != "fcn":
                raise HTTPException(422, "The FCN limit must reference a contract in this portfolio.")
        else:
            from portfolio_app.services.instrument_registry import get_registry_instrument
            instrument = get_registry_instrument(limit.entity_id)
            if not instrument or instrument.get("instrument_type") in {"cash", "fx", "index"}:
                raise HTTPException(422, "Select a supported security for the direct holding limit.")
    for allocation in request.fcn_allocations:
        if allocation.model_dump(mode="json") == saved_allocations.get(allocation.contract_id):
            continue
        contract = contracts.get(allocation.contract_id)
        if not contract or contract.contract_type != "fcn":
            raise HTTPException(422, "FCN allocation must reference a contract in this portfolio.")
        underlying_ids = {item["instrument_id"] for item in contract.terms_json.get("underlyings", [])}
        if allocation.method == "custom" and {row.instrument_id for row in allocation.weights} != underlying_ids:
            raise HTTPException(422, "Custom allocation must include every linked underlying, including explicit zero weights.")
    settings = request.model_dump(mode="json", exclude={"expected_revision", "effective_from"})
    settings["limits"] = [item for item in settings["limits"] if item["limit_weight"] is not None]
    settings["schema_version"] = 2
    principal = actor()
    row = ConcentrationPolicyRevisionModel(
        portfolio_id=portfolio_id, revision=revision + 1, effective_from=request.effective_from,
        settings_json=settings, created_by=str(principal.user_id or principal.display_name), created_at=datetime.now(UTC).isoformat(),
    )
    session.add(row)
    session.flush()
    return serialize_settings(row)
