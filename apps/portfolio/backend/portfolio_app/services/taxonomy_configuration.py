from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from portfolio_app.db.models import (
    PortfolioTaxonomyStateModel,
    PortfolioRecordModel,
    TargetSetLineRecordModel,
    TargetSetRecordModel,
    TaxonomyAssignmentRecordModel,
    TaxonomyConfigurationRevisionModel,
    TaxonomyNodeRecordModel,
    TaxonomyRecordModel,
)
from portfolio_app.db.session import get_session_factory


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_configuration_version(session, portfolio_id: str) -> int:
    state = session.scalar(
        select(PortfolioTaxonomyStateModel)
        .where(PortfolioTaxonomyStateModel.portfolio_id == portfolio_id)
        .with_for_update()
    )
    now = _utc_timestamp()
    if state is None:
        state = PortfolioTaxonomyStateModel(
            portfolio_id=portfolio_id,
            current_version=0,
            updated_at=now,
        )
        session.add(state)
        session.flush()
    state.current_version = int(state.current_version) + 1
    state.updated_at = now
    session.flush()
    return int(state.current_version)


def taxonomy_configuration_version(portfolio_id: str, *, session=None) -> int:
    if session is not None:
        state = session.get(PortfolioTaxonomyStateModel, portfolio_id)
        return int(state.current_version) if state is not None else 0
    session_factory = get_session_factory()
    with session_factory() as owned_session:
        return taxonomy_configuration_version(portfolio_id, session=owned_session)


def _taxonomy_configuration_payload(session, taxonomy_id: str) -> dict[str, object]:
    taxonomy = session.get(TaxonomyRecordModel, taxonomy_id)
    if taxonomy is None:
        raise ValueError("Taxonomy not found.")
    nodes = session.scalars(
        select(TaxonomyNodeRecordModel)
        .where(TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id)
        .order_by(
            TaxonomyNodeRecordModel.sort_order,
            TaxonomyNodeRecordModel.taxonomy_node_id,
        )
    ).all()
    assignments = session.scalars(
        select(TaxonomyAssignmentRecordModel)
        .where(TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id)
        .order_by(TaxonomyAssignmentRecordModel.assignment_id)
    ).all()
    target_sets = session.scalars(
        select(TargetSetRecordModel)
        .where(TargetSetRecordModel.taxonomy_id == taxonomy_id)
        .order_by(TargetSetRecordModel.target_set_id)
    ).all()
    target_set_ids = [record.target_set_id for record in target_sets]
    target_lines = (
        session.scalars(
            select(TargetSetLineRecordModel)
            .where(TargetSetLineRecordModel.target_set_id.in_(target_set_ids))
            .order_by(TargetSetLineRecordModel.target_line_id)
        ).all()
        if target_set_ids
        else []
    )
    return {
        "taxonomy": {
            "taxonomy_id": taxonomy.taxonomy_id,
            "portfolio_id": taxonomy.portfolio_id,
            "name": taxonomy.name,
            "taxonomy_type": taxonomy.taxonomy_type,
            "purpose": taxonomy.purpose,
            "primary_assignment_scope": taxonomy.primary_assignment_scope,
            "root_allocation_basis": taxonomy.root_allocation_basis,
            "status": taxonomy.status,
            "source_template_ref": taxonomy.source_template_ref,
        },
        "taxonomy_nodes": [
            {
                "taxonomy_node_id": record.taxonomy_node_id,
                "taxonomy_id": record.taxonomy_id,
                "parent_taxonomy_node_id": record.parent_taxonomy_node_id,
                "node_name": record.node_name,
                "node_code": record.node_code,
                "sort_order": record.sort_order,
                "is_terminal": record.is_terminal,
                "allocation_basis": record.allocation_basis,
                "status": record.status,
            }
            for record in nodes
        ],
        "taxonomy_assignments": [
            {
                "assignment_id": record.assignment_id,
                "taxonomy_id": record.taxonomy_id,
                "target_scope": record.target_scope,
                "target_entity_id": record.target_entity_id,
                "taxonomy_node_id": record.taxonomy_node_id,
                "status": record.status,
            }
            for record in assignments
        ],
        "target_sets": [
            {
                "target_set_id": record.target_set_id,
                "taxonomy_id": record.taxonomy_id,
                "comparator_taxonomy_node_id": record.comparator_taxonomy_node_id,
                "target_set_type": record.target_set_type,
                "name": record.name,
                "status": record.status,
                "notes": record.notes,
            }
            for record in target_sets
        ],
        "target_set_lines": [
            {
                "target_line_id": record.target_line_id,
                "target_set_id": record.target_set_id,
                "taxonomy_node_id": record.taxonomy_node_id,
                "target_member_type": record.target_member_type,
                "target_member_id": record.target_member_id,
                "target_value": record.target_value,
                "notes": record.notes,
            }
            for record in target_lines
        ],
    }


def _record_taxonomy_configuration_revision_in_session(session, *, portfolio_id: str,
        taxonomy_id: str) -> TaxonomyConfigurationRevisionModel:
    """Append an audit snapshot; live rows remain the only current configuration."""
    payload = _taxonomy_configuration_payload(session, taxonomy_id)
    revision_id = f"taxonomy-revision-{uuid4().hex}"
    previous = session.scalar(select(TaxonomyConfigurationRevisionModel).where(
        TaxonomyConfigurationRevisionModel.portfolio_id == portfolio_id,
        TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id,
        TaxonomyConfigurationRevisionModel.superseded_by_revision_id.is_(None)).with_for_update())
    if previous is not None:
        previous.superseded_by_revision_id = revision_id
        session.flush()
    record = TaxonomyConfigurationRevisionModel(
        taxonomy_configuration_revision_id=revision_id, portfolio_id=portfolio_id,
        taxonomy_id=taxonomy_id, configuration_version=_next_configuration_version(session, portfolio_id),
        configuration_json=deepcopy(payload), superseded_by_revision_id=None, created_at=_utc_timestamp())
    session.add(record)
    session.flush()
    return record


def current_taxonomy_configuration_in_session(session, portfolio_id: str,
        taxonomy_id: str) -> dict[str, object] | None:
    """Read current rows. Multi-query callers hold the portfolio read/write lock."""
    taxonomy = session.get(TaxonomyRecordModel, taxonomy_id)
    if taxonomy is None or taxonomy.portfolio_id != portfolio_id:
        return None
    payload = _taxonomy_configuration_payload(session, taxonomy_id)
    revision = session.scalar(select(TaxonomyConfigurationRevisionModel).where(
        TaxonomyConfigurationRevisionModel.portfolio_id == portfolio_id,
        TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id,
        TaxonomyConfigurationRevisionModel.superseded_by_revision_id.is_(None)))
    payload["configuration_version"] = revision.configuration_version if revision else None
    payload["taxonomy_configuration_revision_id"] = revision.taxonomy_configuration_revision_id if revision else None
    return payload


def current_taxonomy_configuration(portfolio_id: str, taxonomy_id: str) -> dict[str, object] | None:
    with get_session_factory()() as session:
        session.scalar(select(PortfolioRecordModel).where(
            PortfolioRecordModel.portfolio_id == portfolio_id).with_for_update(read=True))
        return current_taxonomy_configuration_in_session(session, portfolio_id, taxonomy_id)


def current_taxonomy_catalog(portfolio_id: str) -> dict[str, object]:
    from portfolio_app.services.portfolio_store import list_portfolio_instrument_universe, list_target_set_integrity_issues

    with get_session_factory()() as session:
        portfolio = session.scalar(select(PortfolioRecordModel).where(
            PortfolioRecordModel.portfolio_id == portfolio_id).with_for_update(read=True))
        result = {key: [] for key in ("taxonomies", "taxonomy_nodes", "taxonomy_assignments", "target_sets", "target_set_lines")}
        resolutions = []
        from portfolio_app.services.taxonomy_targets import resolve_taxonomy_targets
        taxonomies = session.scalars(select(TaxonomyRecordModel).where(
            TaxonomyRecordModel.portfolio_id == portfolio_id).order_by(TaxonomyRecordModel.taxonomy_id))
        for taxonomy in taxonomies:
            configuration = _taxonomy_configuration_payload(session, taxonomy.taxonomy_id)
            result["taxonomies"].append(configuration["taxonomy"])
            from portfolio_app.services.research_eligibility import contract_only_instrument_ids
            from portfolio_app.services.valuation_clock import portfolio_valuation_today
            active_target_ids = {target["target_set_id"] for target in configuration["target_sets"] if target["status"] == "active"}
            explicit = [line["target_member_id"] for line in configuration["target_set_lines"]
                        if line["target_member_type"] == "instrument" and line["target_set_id"] in active_target_ids]
            configuration["contract_only_instrument_ids"] = sorted(contract_only_instrument_ids(
                portfolio_id, as_of_date=portfolio_valuation_today(portfolio.valuation_timezone),
                explicitly_selected=explicit, session=session))
            resolutions.append(resolve_taxonomy_targets(configuration))
            for key in ("taxonomy_nodes", "taxonomy_assignments", "target_sets", "target_set_lines"):
                result[key].extend(configuration[key])
        return {
            **result,
            "instrument_universe": list_portfolio_instrument_universe(portfolio_id, _session=session),
            "target_set_integrity_issues": list_target_set_integrity_issues(portfolio_id, _session=session),
            "target_resolution": resolutions,
            "taxonomy_configuration_version": taxonomy_configuration_version(portfolio_id, session=session),
        }
