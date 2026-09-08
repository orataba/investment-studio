"""Dated limits belong to the portfolio, not to a browser's selected grouping."""
from copy import deepcopy
from datetime import UTC, date, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from portfolio_app.api.concentration_contracts import ConcentrationSettingsUpdate
from portfolio_app.db.models import (
    ConcentrationPolicyRevisionModel, DerivativeContractRecordModel,
    PortfolioRecordModel, TaxonomyNodeRecordModel, TaxonomyRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.portfolio_access import actor


def empty_settings(portfolio_id: str) -> dict:
    return {"portfolio_id": portfolio_id, "revision": 0, "effective_from": None, "rules": [], "fcn_allocations": []}


def serialize_settings(row: ConcentrationPolicyRevisionModel) -> dict:
    return {"portfolio_id": row.portfolio_id, "revision": row.revision,
            "effective_from": row.effective_from.isoformat(), **deepcopy(row.settings_json)}


def read_concentration_settings(portfolio_id: str, *, as_of_date: date | None = None) -> dict:
    with get_session_factory()() as session:
        if session.get(PortfolioRecordModel, portfolio_id) is None:
            raise HTTPException(404, "Portfolio not found.")
        statement = select(ConcentrationPolicyRevisionModel).where(ConcentrationPolicyRevisionModel.portfolio_id == portfolio_id)
        if as_of_date is not None:
            statement = statement.where(ConcentrationPolicyRevisionModel.effective_from <= as_of_date).order_by(
                ConcentrationPolicyRevisionModel.effective_from.desc(), ConcentrationPolicyRevisionModel.revision.desc())
        else:
            statement = statement.order_by(ConcentrationPolicyRevisionModel.revision.desc())
        row = session.scalar(statement.limit(1))
        return serialize_settings(row) if row else empty_settings(portfolio_id)


def save_concentration_settings(portfolio_id: str, request: ConcentrationSettingsUpdate) -> dict:
    with get_session_factory()() as session:
        # Serialize the initial insert as well as later updates; a revision check
        # protects edits opened by two users without replacing either one's work.
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
        previous = latest.settings_json if latest else {}
        saved_rules = {item["rule_id"]: item for item in previous.get("rules", [])}
        saved_allocations = {item["contract_id"]: item for item in previous.get("fcn_allocations", [])}
        for rule in request.rules:
            # Archiving a taxonomy must not block unrelated edits to this full
            # settings document. Retained historical references are immutable here.
            if rule.model_dump(mode="json") == saved_rules.get(rule.rule_id):
                continue
            if rule.scope == "taxonomy":
                if rule.taxonomy_id not in taxonomies or taxonomies[rule.taxonomy_id].status != "active":
                    raise HTTPException(422, "Select an active taxonomy belonging to this portfolio.")
                if rule.entity_id is not None and (rule.entity_id not in nodes or nodes[rule.entity_id].taxonomy_id != rule.taxonomy_id or nodes[rule.entity_id].status != "active"):
                    raise HTTPException(422, "The concentration node must belong to the selected taxonomy.")
            elif rule.scope == "fcn" and rule.entity_id is not None:
                if rule.entity_id not in contracts or contracts[rule.entity_id].contract_type != "fcn":
                    raise HTTPException(422, "The FCN limit must reference a contract in this portfolio.")
            elif rule.scope == "security" and rule.entity_id is not None:
                from portfolio_app.services.instrument_registry import get_registry_instrument
                instrument = get_registry_instrument(rule.entity_id)
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
        principal = actor()
        row = ConcentrationPolicyRevisionModel(
            portfolio_id=portfolio_id, revision=revision + 1, effective_from=request.effective_from,
            settings_json=request.model_dump(mode="json", exclude={"expected_revision", "effective_from"}),
            created_by=str(principal.user_id or principal.display_name), created_at=datetime.now(UTC).isoformat(),
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "Concentration settings changed. Reload before saving.") from error
        return serialize_settings(row)
