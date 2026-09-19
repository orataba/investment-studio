from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from portfolio_app.db.models import (
    AnalyticsScopePolicyRecordModel,
    AnalyticsTaxonomySelectionRecordModel,
    PortfolioAnalyticsPolicyStateModel,
    PortfolioRecordModel,
    TargetSetLineRecordModel,
    TargetSetRecordModel,
    TaxonomyAssignmentRecordModel,
    TaxonomyConfigurationRevisionModel,
    TaxonomyNodeRecordModel,
    TaxonomyRecordModel,
)
from portfolio_app.db.session import get_session_factory


ROOT_POLICY_NODE_ID = "__root__"
UNASSIGNED_POLICY_NODE_ID = "__unassigned__"
RESERVED_POLICY_NODE_IDS = frozenset(
    {ROOT_POLICY_NODE_ID, UNASSIGNED_POLICY_NODE_ID}
)


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_policy_version(session, portfolio_id: str) -> int:
    state = session.scalar(
        select(PortfolioAnalyticsPolicyStateModel)
        .where(PortfolioAnalyticsPolicyStateModel.portfolio_id == portfolio_id)
        .with_for_update()
    )
    now = _utc_timestamp()
    if state is None:
        state = PortfolioAnalyticsPolicyStateModel(
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


def analytics_policy_version(portfolio_id: str, *, session=None) -> int:
    if session is not None:
        state = session.get(PortfolioAnalyticsPolicyStateModel, portfolio_id)
        return int(state.current_version) if state is not None else 0
    session_factory = get_session_factory()
    with session_factory() as owned_session:
        return analytics_policy_version(portfolio_id, session=owned_session)


def _serialize_policy(record: AnalyticsScopePolicyRecordModel) -> dict[str, object]:
    return {key: getattr(record, key) for key in (
        "analytics_scope_policy_id", "portfolio_id", "taxonomy_id", "taxonomy_node_id",
        "risk_eligible", "risk_budget_eligible", "performance_scope", "valuation_basis",
        "exclusion_reason", "policy_version", "superseded_by_policy_id", "created_at",
    )}


def list_analytics_scope_policies(portfolio_id: str, *, taxonomy_id: str | None = None) -> list[dict[str, object]]:
    with get_session_factory()() as session:
        statement = select(AnalyticsScopePolicyRecordModel).where(
            AnalyticsScopePolicyRecordModel.portfolio_id == portfolio_id)
        if taxonomy_id:
            statement = statement.where(AnalyticsScopePolicyRecordModel.taxonomy_id == taxonomy_id)
        return [_serialize_policy(record) for record in session.scalars(statement.order_by(
            AnalyticsScopePolicyRecordModel.taxonomy_id,
            AnalyticsScopePolicyRecordModel.taxonomy_node_id,
            AnalyticsScopePolicyRecordModel.policy_version))]


def _validate_policy_node(session, taxonomy_id: str, taxonomy_node_id: str) -> None:
    if taxonomy_node_id in RESERVED_POLICY_NODE_IDS:
        return
    node = session.scalar(select(TaxonomyNodeRecordModel.taxonomy_node_id).where(
        TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
        TaxonomyNodeRecordModel.taxonomy_node_id == taxonomy_node_id))
    if node is None:
        raise ValueError("Analytics scope policy taxonomy node not found.")


def current_analytics_policies_by_node(session, *, portfolio_id: str,
                                     taxonomy_id: str) -> dict[str, AnalyticsScopePolicyRecordModel]:
    records = session.scalars(select(AnalyticsScopePolicyRecordModel).where(
        AnalyticsScopePolicyRecordModel.portfolio_id == portfolio_id,
        AnalyticsScopePolicyRecordModel.taxonomy_id == taxonomy_id,
        AnalyticsScopePolicyRecordModel.superseded_by_policy_id.is_(None)))
    return {record.taxonomy_node_id: record for record in records}


def _create_scope_policy_in_session(session, *, portfolio_id: str, taxonomy_id: str,
        taxonomy_node_id: str, risk_eligible: bool, risk_budget_eligible: bool,
        performance_scope: str, valuation_basis: str, exclusion_reason: str | None,
        analytics_scope_policy_id: str | None = None) -> AnalyticsScopePolicyRecordModel:
    taxonomy = session.get(TaxonomyRecordModel, taxonomy_id)
    if taxonomy is None or taxonomy.portfolio_id != portfolio_id:
        raise ValueError("Taxonomy not found.")
    _validate_policy_node(session, taxonomy_id, taxonomy_node_id)
    if risk_budget_eligible and not risk_eligible:
        raise ValueError("risk_budget_eligible requires risk_eligible.")
    normalized_reason = (exclusion_reason or "").strip() or None
    if (not risk_eligible or performance_scope != "ordinary") and normalized_reason is None:
        raise ValueError("Excluded or non-ordinary policies require exclusion_reason.")
    if taxonomy_node_id in current_analytics_policies_by_node(session, portfolio_id=portfolio_id, taxonomy_id=taxonomy_id):
        raise ValueError("Analytics scope policy already exists; replace the current policy.")
    record = AnalyticsScopePolicyRecordModel(
        analytics_scope_policy_id=analytics_scope_policy_id or f"scope-policy-{uuid4().hex}", portfolio_id=portfolio_id,
        taxonomy_id=taxonomy_id, taxonomy_node_id=taxonomy_node_id,
        risk_eligible=bool(risk_eligible), risk_budget_eligible=bool(risk_budget_eligible),
        performance_scope=performance_scope, valuation_basis=valuation_basis,
        exclusion_reason=normalized_reason, policy_version=_next_policy_version(session, portfolio_id),
        superseded_by_policy_id=None, created_at=_utc_timestamp())
    session.add(record)
    session.flush()
    return record


def replace_analytics_scope_policy(portfolio_id: str, *, taxonomy_id: str,
        taxonomy_node_id: str, risk_eligible: bool, risk_budget_eligible: bool,
        performance_scope: str, valuation_basis: str, exclusion_reason: str | None) -> dict[str, object]:
    with get_session_factory()() as session:
        portfolio = session.scalar(select(PortfolioRecordModel).where(
            PortfolioRecordModel.portfolio_id == portfolio_id).with_for_update())
        if portfolio is None:
            raise ValueError("Portfolio not found.")
        policy_id = f"scope-policy-{uuid4().hex}"
        previous = current_analytics_policies_by_node(session, portfolio_id=portfolio_id,
                                                     taxonomy_id=taxonomy_id).get(taxonomy_node_id)
        if previous is not None:
            previous.superseded_by_policy_id = policy_id
            session.flush()
        record = _create_scope_policy_in_session(session, portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id, taxonomy_node_id=taxonomy_node_id,
            risk_eligible=risk_eligible, risk_budget_eligible=risk_budget_eligible,
            performance_scope=performance_scope, valuation_basis=valuation_basis,
            exclusion_reason=exclusion_reason, analytics_scope_policy_id=policy_id)
        from portfolio_app.services.daily_snapshots import mark_portfolio_daily_snapshots_stale
        mark_portfolio_daily_snapshots_stale(portfolio_id, session=session)
        serialized = _serialize_policy(record)
        session.commit()
        return serialized


def _ensure_default_scope_policies_in_session(session, *, portfolio_id: str, taxonomy_id: str) -> None:
    policies = current_analytics_policies_by_node(session, portfolio_id=portfolio_id, taxonomy_id=taxonomy_id)
    if ROOT_POLICY_NODE_ID not in policies:
        _create_scope_policy_in_session(session, portfolio_id=portfolio_id, taxonomy_id=taxonomy_id,
            taxonomy_node_id=ROOT_POLICY_NODE_ID, risk_eligible=True, risk_budget_eligible=True,
            performance_scope="ordinary", valuation_basis="market", exclusion_reason=None)
    if UNASSIGNED_POLICY_NODE_ID not in policies:
        _create_scope_policy_in_session(session, portfolio_id=portfolio_id, taxonomy_id=taxonomy_id,
            taxonomy_node_id=UNASSIGNED_POLICY_NODE_ID, risk_eligible=False, risk_budget_eligible=False,
            performance_scope="unallocated", valuation_basis="unknown", exclusion_reason="No current taxonomy assignment.")


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
            "planning_enabled": taxonomy.planning_enabled,
            "budgeting_level": taxonomy.budgeting_level,
            "root_default_target_dimension": taxonomy.root_default_target_dimension,
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
                "default_target_dimension": record.default_target_dimension,
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
                "weight_enabled": record.weight_enabled,
                "risk_budget_enabled": record.risk_budget_enabled,
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
                "target_weight": record.target_weight,
                "target_risk_share": record.target_risk_share,
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
        taxonomy_id=taxonomy_id, configuration_version=_next_policy_version(session, portfolio_id),
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
    with get_session_factory()() as session:
        portfolio = session.scalar(select(PortfolioRecordModel).where(
            PortfolioRecordModel.portfolio_id == portfolio_id).with_for_update(read=True))
        result = {key: [] for key in ("taxonomies", "taxonomy_nodes", "taxonomy_assignments", "target_sets", "target_set_lines")}
        taxonomies = session.scalars(select(TaxonomyRecordModel).where(
            TaxonomyRecordModel.portfolio_id == portfolio_id).order_by(TaxonomyRecordModel.taxonomy_id))
        for taxonomy in taxonomies:
            configuration = _taxonomy_configuration_payload(session, taxonomy.taxonomy_id)
            result["taxonomies"].append(configuration["taxonomy"])
            for key in ("taxonomy_nodes", "taxonomy_assignments", "target_sets", "target_set_lines"):
                result[key].extend(configuration[key])
        policies = session.scalars(select(AnalyticsScopePolicyRecordModel).where(
            AnalyticsScopePolicyRecordModel.portfolio_id == portfolio_id).order_by(
                AnalyticsScopePolicyRecordModel.policy_version))
        selections = session.scalars(select(AnalyticsTaxonomySelectionRecordModel).where(
            AnalyticsTaxonomySelectionRecordModel.portfolio_id == portfolio_id).order_by(
                AnalyticsTaxonomySelectionRecordModel.selection_version))
        return {**result,
                "default_planning_taxonomy_id": portfolio.default_planning_taxonomy_id if portfolio else None,
                "analytics_scope_policy_version": analytics_policy_version(portfolio_id, session=session),
                "analytics_scope_policies": [_serialize_policy(row) for row in policies],
                "analytics_taxonomy_selections": [_serialize_taxonomy_selection(row) for row in selections]}



def _serialize_taxonomy_selection(record: AnalyticsTaxonomySelectionRecordModel) -> dict[str, object]:
    return {key: getattr(record, key) for key in ("analytics_taxonomy_selection_id", "portfolio_id", "taxonomy_id",
        "selection_version", "superseded_by_selection_id", "created_at")}


def list_analytics_taxonomy_selections(portfolio_id: str) -> list[dict[str, object]]:
    with get_session_factory()() as session:
        records = session.scalars(select(AnalyticsTaxonomySelectionRecordModel).where(
            AnalyticsTaxonomySelectionRecordModel.portfolio_id == portfolio_id).order_by(
                AnalyticsTaxonomySelectionRecordModel.selection_version))
        return [_serialize_taxonomy_selection(record) for record in records]


def current_analytics_taxonomy_selection_in_session(session, *, portfolio_id: str) -> AnalyticsTaxonomySelectionRecordModel | None:
    return session.scalar(select(AnalyticsTaxonomySelectionRecordModel).where(
        AnalyticsTaxonomySelectionRecordModel.portfolio_id == portfolio_id,
        AnalyticsTaxonomySelectionRecordModel.superseded_by_selection_id.is_(None)))


def set_analytics_taxonomy_selection_in_session(session, *, portfolio_id: str,
        taxonomy_id: str | None) -> AnalyticsTaxonomySelectionRecordModel:
    portfolio = session.get(PortfolioRecordModel, portfolio_id)
    if portfolio is None:
        raise ValueError("Portfolio not found.")
    resolved_taxonomy_id = str(taxonomy_id or "").strip() or None
    if resolved_taxonomy_id is not None:
        taxonomy = session.get(TaxonomyRecordModel, resolved_taxonomy_id)
        if taxonomy is None or taxonomy.portfolio_id != portfolio_id:
            raise ValueError("Planning taxonomy not found.")
        if not taxonomy.planning_enabled or taxonomy.status != "active":
            raise ValueError("Analytics taxonomy must be active and planning-enabled.")
    selection_id = f"taxonomy-selection-{uuid4().hex}"
    previous = current_analytics_taxonomy_selection_in_session(session, portfolio_id=portfolio_id)
    if previous is not None:
        previous.superseded_by_selection_id = selection_id
        session.flush()
    record = AnalyticsTaxonomySelectionRecordModel(
        analytics_taxonomy_selection_id=selection_id, portfolio_id=portfolio_id,
        taxonomy_id=resolved_taxonomy_id, selection_version=_next_policy_version(session, portfolio_id),
        superseded_by_selection_id=None, created_at=_utc_timestamp())
    session.add(record)
    portfolio.default_planning_taxonomy_id = resolved_taxonomy_id
    session.flush()
    return record


def resolve_instrument_analytics_scopes(portfolio_id: str, *, instrument_ids: list[str],
        taxonomy_id: str | None = None) -> dict[str, dict[str, object]]:
    normalized_ids = list(dict.fromkeys(item.strip() for item in instrument_ids if item.strip()))
    with get_session_factory()() as session:
        session.scalar(select(PortfolioRecordModel).where(
            PortfolioRecordModel.portfolio_id == portfolio_id).with_for_update(read=True))
        selection = current_analytics_taxonomy_selection_in_session(session, portfolio_id=portfolio_id) if taxonomy_id is None else None
        resolved_taxonomy_id = taxonomy_id or (selection.taxonomy_id if selection else None)
        configuration = current_taxonomy_configuration_in_session(session, portfolio_id, resolved_taxonomy_id) if resolved_taxonomy_id else None
        if configuration is None or configuration["taxonomy"].get("status") != "active":
            return {instrument_id: _missing_scope_resolution(instrument_id, taxonomy_id=resolved_taxonomy_id,
                    reason="No active current analytics taxonomy is configured.") for instrument_id in normalized_ids}
        policies = current_analytics_policies_by_node(session, portfolio_id=portfolio_id, taxonomy_id=resolved_taxonomy_id)
        return resolve_configuration_analytics_scopes(configuration, policies, instrument_ids=normalized_ids,
            taxonomy_id=resolved_taxonomy_id, selection_version=selection.selection_version if selection else None)


def resolve_configuration_analytics_scopes(
    configuration: dict[str, object],
    policies: dict[str, AnalyticsScopePolicyRecordModel],
    *,
    instrument_ids: list[str],
    taxonomy_id: str,
    selection_version: int | None = None,
) -> dict[str, dict[str, object]]:
    """Resolve one already-selected configuration without reading another clock."""
    assignments = {
        str(record.get("target_entity_id") or ""): record
        for record in list(configuration.get("taxonomy_assignments") or [])
        if isinstance(record, dict)
        and str(record.get("target_scope") or "") == "instrument"
        and str(record.get("status") or "") == "active"
    }
    nodes = {
        str(record.get("taxonomy_node_id") or ""): record
        for record in list(configuration.get("taxonomy_nodes") or [])
        if isinstance(record, dict)
    }
    nodes = {key: value for key, value in nodes.items() if value.get("status") == "active"}
    assignments = {key: value for key, value in assignments.items()
                   if str(value.get("taxonomy_node_id") or "") in nodes}
    results: dict[str, dict[str, object]] = {}
    for instrument_id in instrument_ids:
        assignment = assignments.get(instrument_id)
        assigned_node_id = (
            str(assignment.get("taxonomy_node_id") or "")
            if assignment is not None
            else UNASSIGNED_POLICY_NODE_ID
        )
        candidate_node_id = assigned_node_id
        policy = policies.get(candidate_node_id)
        inherited_from_node_id: str | None = None
        visited: set[str] = set()
        while policy is None and candidate_node_id not in RESERVED_POLICY_NODE_IDS:
            if candidate_node_id in visited:
                break
            visited.add(candidate_node_id)
            node = nodes.get(candidate_node_id)
            candidate_node_id = (
                str(node.get("parent_taxonomy_node_id") or "")
                if node is not None
                else ""
            )
            if not candidate_node_id:
                candidate_node_id = ROOT_POLICY_NODE_ID
            policy = policies.get(candidate_node_id)
            if policy is not None:
                inherited_from_node_id = candidate_node_id
        if policy is None:
            results[instrument_id] = _missing_scope_resolution(
                instrument_id,
                taxonomy_id=taxonomy_id,
                taxonomy_node_id=assigned_node_id,
                reason="No current analytics scope policy resolves for the assignment.",
            )
            continue
        results[instrument_id] = {
            "instrument_id": instrument_id,
            "scope_status": "resolved",
            "taxonomy_id": taxonomy_id,
            "taxonomy_node_id": (
                None
                if assigned_node_id == UNASSIGNED_POLICY_NODE_ID
                else assigned_node_id
            ),
            "resolved_policy_node_id": policy.taxonomy_node_id,
            "inherited_from_node_id": inherited_from_node_id,
            "analytics_scope_policy_id": policy.analytics_scope_policy_id,
            "scope_policy_version": policy.policy_version,
            "configuration_version": configuration.get("configuration_version"),
            "taxonomy_selection_version": selection_version,
            "risk_eligible": policy.risk_eligible,
            "risk_budget_eligible": policy.risk_budget_eligible,
            "performance_scope": policy.performance_scope,
            "valuation_basis_policy": policy.valuation_basis,
            "exclusion_reason": policy.exclusion_reason,
        }
    return results



def _missing_scope_resolution(
    instrument_id: str,
    *,
    taxonomy_id: str | None = None,
    taxonomy_node_id: str | None = None,
    reason: str,
) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "scope_status": "missing",
        "taxonomy_id": taxonomy_id,
        "taxonomy_node_id": taxonomy_node_id,
        "resolved_policy_node_id": None,
        "inherited_from_node_id": None,
        "analytics_scope_policy_id": None,
        "scope_policy_version": None,
        "configuration_version": None,
        "taxonomy_selection_version": None,
        "risk_eligible": False,
        "risk_budget_eligible": False,
        "performance_scope": "unallocated",
        "valuation_basis_policy": "unknown",
        "exclusion_reason": reason,
    }
