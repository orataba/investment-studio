from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select

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
    return {
        "analytics_scope_policy_id": record.analytics_scope_policy_id,
        "portfolio_id": record.portfolio_id,
        "taxonomy_id": record.taxonomy_id,
        "taxonomy_node_id": record.taxonomy_node_id,
        "risk_eligible": record.risk_eligible,
        "risk_budget_eligible": record.risk_budget_eligible,
        "performance_scope": record.performance_scope,
        "valuation_basis": record.valuation_basis,
        "exclusion_reason": record.exclusion_reason,
        "effective_from": record.effective_from.isoformat(),
        "effective_to": (
            record.effective_to.isoformat() if record.effective_to is not None else None
        ),
        "policy_version": record.policy_version,
        "superseded_by_policy_id": record.superseded_by_policy_id,
        "created_at": record.created_at,
    }


def list_analytics_scope_policies(
    portfolio_id: str,
    *,
    taxonomy_id: str | None = None,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        statement = select(AnalyticsScopePolicyRecordModel).where(
            AnalyticsScopePolicyRecordModel.portfolio_id == portfolio_id
        )
        if taxonomy_id:
            statement = statement.where(
                AnalyticsScopePolicyRecordModel.taxonomy_id == taxonomy_id
            )
        records = session.scalars(
            statement.order_by(
                AnalyticsScopePolicyRecordModel.taxonomy_id,
                AnalyticsScopePolicyRecordModel.taxonomy_node_id,
                AnalyticsScopePolicyRecordModel.effective_from,
                AnalyticsScopePolicyRecordModel.policy_version,
            )
        ).all()
        return [_serialize_policy(record) for record in records]


def _validate_policy_node(session, taxonomy_id: str, taxonomy_node_id: str) -> None:
    if taxonomy_node_id in RESERVED_POLICY_NODE_IDS:
        return
    node = session.scalar(
        select(TaxonomyNodeRecordModel.taxonomy_node_id).where(
            TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id,
            TaxonomyNodeRecordModel.taxonomy_node_id == taxonomy_node_id,
        )
    )
    if node is None:
        raise ValueError("Analytics scope policy taxonomy node not found.")


def _overlapping_policy(
    session,
    *,
    portfolio_id: str,
    taxonomy_id: str,
    taxonomy_node_id: str,
    effective_from: date,
    effective_to: date | None,
) -> AnalyticsScopePolicyRecordModel | None:
    upper_bound = effective_to or date.max
    return session.scalar(
        select(AnalyticsScopePolicyRecordModel).where(
            AnalyticsScopePolicyRecordModel.portfolio_id == portfolio_id,
            AnalyticsScopePolicyRecordModel.taxonomy_id == taxonomy_id,
            AnalyticsScopePolicyRecordModel.taxonomy_node_id == taxonomy_node_id,
            AnalyticsScopePolicyRecordModel.superseded_by_policy_id.is_(None),
            AnalyticsScopePolicyRecordModel.effective_from <= upper_bound,
            or_(
                AnalyticsScopePolicyRecordModel.effective_to.is_(None),
                AnalyticsScopePolicyRecordModel.effective_to >= effective_from,
            ),
        )
    )


def _create_scope_policy_in_session(
    session,
    *,
    portfolio_id: str,
    taxonomy_id: str,
    taxonomy_node_id: str,
    risk_eligible: bool,
    risk_budget_eligible: bool,
    performance_scope: str,
    valuation_basis: str,
    exclusion_reason: str | None,
    effective_from: date,
    effective_to: date | None,
) -> AnalyticsScopePolicyRecordModel:
    taxonomy = session.scalar(
        select(TaxonomyRecordModel).where(
            TaxonomyRecordModel.portfolio_id == portfolio_id,
            TaxonomyRecordModel.taxonomy_id == taxonomy_id,
        )
    )
    if taxonomy is None:
        raise ValueError("Taxonomy not found.")
    _validate_policy_node(session, taxonomy_id, taxonomy_node_id)
    if risk_budget_eligible and not risk_eligible:
        raise ValueError("risk_budget_eligible requires risk_eligible.")
    if effective_to is not None and effective_to < effective_from:
        raise ValueError("effective_to must not precede effective_from.")
    normalized_reason = (exclusion_reason or "").strip() or None
    if (not risk_eligible or performance_scope != "ordinary") and normalized_reason is None:
        raise ValueError("Excluded or non-ordinary policies require exclusion_reason.")
    if _overlapping_policy(
        session,
        portfolio_id=portfolio_id,
        taxonomy_id=taxonomy_id,
        taxonomy_node_id=taxonomy_node_id,
        effective_from=effective_from,
        effective_to=effective_to,
    ) is not None:
        raise ValueError("Analytics scope policy effective ranges may not overlap.")

    version = _next_policy_version(session, portfolio_id)
    record = AnalyticsScopePolicyRecordModel(
        analytics_scope_policy_id=f"scope-policy-{uuid4().hex}",
        portfolio_id=portfolio_id,
        taxonomy_id=taxonomy_id,
        taxonomy_node_id=taxonomy_node_id,
        risk_eligible=bool(risk_eligible),
        risk_budget_eligible=bool(risk_budget_eligible),
        performance_scope=performance_scope,
        valuation_basis=valuation_basis,
        exclusion_reason=normalized_reason,
        effective_from=effective_from,
        effective_to=effective_to,
        policy_version=version,
        superseded_by_policy_id=None,
        created_at=_utc_timestamp(),
    )
    session.add(record)
    session.flush()
    return record


def replace_analytics_scope_policy(
    portfolio_id: str,
    *,
    taxonomy_id: str,
    taxonomy_node_id: str,
    risk_eligible: bool,
    risk_budget_eligible: bool,
    performance_scope: str,
    valuation_basis: str,
    exclusion_reason: str | None,
    effective_from: date,
    effective_to: date | None,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Taxonomy not found.")
        _validate_policy_node(session, taxonomy_id, taxonomy_node_id)
        if risk_budget_eligible and not risk_eligible:
            raise ValueError("risk_budget_eligible requires risk_eligible.")
        if effective_to is not None and effective_to < effective_from:
            raise ValueError("effective_to must not precede effective_from.")
        normalized_reason = (exclusion_reason or "").strip() or None
        if (
            not risk_eligible or performance_scope != "ordinary"
        ) and normalized_reason is None:
            raise ValueError(
                "Excluded or non-ordinary policies require exclusion_reason."
            )

        active_records = session.scalars(
            select(AnalyticsScopePolicyRecordModel)
            .where(
                AnalyticsScopePolicyRecordModel.portfolio_id == portfolio_id,
                AnalyticsScopePolicyRecordModel.taxonomy_id == taxonomy_id,
                AnalyticsScopePolicyRecordModel.taxonomy_node_id
                == taxonomy_node_id,
                AnalyticsScopePolicyRecordModel.superseded_by_policy_id.is_(None),
            )
            .order_by(
                AnalyticsScopePolicyRecordModel.effective_from,
                AnalyticsScopePolicyRecordModel.policy_version,
            )
            .with_for_update()
        ).all()
        latest = active_records[-1] if active_records else None
        if latest is not None and effective_from < latest.effective_from:
            raise ValueError(
                "Analytics scope policy changes must be appended in effective-date order."
            )

        policy_id = f"scope-policy-{uuid4().hex}"
        resolved_effective_to = effective_to
        if latest is not None and latest.effective_from == effective_from:
            latest.superseded_by_policy_id = policy_id
            if resolved_effective_to is None:
                resolved_effective_to = latest.effective_to
        elif latest is not None and (
            latest.effective_to is None or latest.effective_to >= effective_from
        ):
            latest.effective_to = effective_from - timedelta(days=1)

        version = _next_policy_version(session, portfolio_id)
        record = AnalyticsScopePolicyRecordModel(
            analytics_scope_policy_id=policy_id,
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
            taxonomy_node_id=taxonomy_node_id,
            risk_eligible=bool(risk_eligible),
            risk_budget_eligible=bool(risk_budget_eligible),
            performance_scope=performance_scope,
            valuation_basis=valuation_basis,
            exclusion_reason=normalized_reason,
            effective_from=effective_from,
            effective_to=resolved_effective_to,
            policy_version=version,
            superseded_by_policy_id=None,
            created_at=_utc_timestamp(),
        )
        session.add(record)
        session.flush()

        from portfolio_app.services.daily_snapshots import (
            mark_portfolio_daily_snapshots_stale,
        )

        mark_portfolio_daily_snapshots_stale(
            portfolio_id,
            dirty_from=effective_from,
            session=session,
        )
        serialized = _serialize_policy(record)
        session.commit()
        return serialized


def _ensure_default_scope_policies_in_session(
    session,
    *,
    portfolio_id: str,
    taxonomy_id: str,
    effective_from: date,
) -> None:
    existing_policy_nodes = set(
        session.scalars(
            select(AnalyticsScopePolicyRecordModel.taxonomy_node_id).where(
                AnalyticsScopePolicyRecordModel.portfolio_id == portfolio_id,
                AnalyticsScopePolicyRecordModel.taxonomy_id == taxonomy_id,
                AnalyticsScopePolicyRecordModel.taxonomy_node_id.in_(
                    RESERVED_POLICY_NODE_IDS
                ),
            )
        ).all()
    )
    if ROOT_POLICY_NODE_ID not in existing_policy_nodes:
        _create_scope_policy_in_session(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
            taxonomy_node_id=ROOT_POLICY_NODE_ID,
            risk_eligible=True,
            risk_budget_eligible=True,
            performance_scope="ordinary",
            valuation_basis="market",
            exclusion_reason=None,
            effective_from=effective_from,
            effective_to=None,
        )
    if UNASSIGNED_POLICY_NODE_ID not in existing_policy_nodes:
        _create_scope_policy_in_session(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=taxonomy_id,
            taxonomy_node_id=UNASSIGNED_POLICY_NODE_ID,
            risk_eligible=False,
            risk_budget_eligible=False,
            performance_scope="unallocated",
            valuation_basis="unknown",
            exclusion_reason="No effective taxonomy assignment.",
            effective_from=effective_from,
            effective_to=None,
        )


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


def _record_taxonomy_configuration_revision_in_session(
    session,
    *,
    portfolio_id: str,
    taxonomy_id: str,
    effective_from: date,
) -> TaxonomyConfigurationRevisionModel:
    payload = _taxonomy_configuration_payload(session, taxonomy_id)
    active_records = session.scalars(
        select(TaxonomyConfigurationRevisionModel)
        .where(
            TaxonomyConfigurationRevisionModel.portfolio_id == portfolio_id,
            TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id,
            TaxonomyConfigurationRevisionModel.superseded_by_revision_id.is_(None),
        )
        .order_by(
            TaxonomyConfigurationRevisionModel.effective_from,
            TaxonomyConfigurationRevisionModel.configuration_version,
        )
        .with_for_update()
    ).all()
    latest = active_records[-1] if active_records else None
    if latest is not None and effective_from < latest.effective_from:
        raise ValueError(
            "Taxonomy configuration changes must be appended in effective-date order."
        )

    revision_id = f"taxonomy-revision-{uuid4().hex}"
    version = _next_policy_version(session, portfolio_id)
    now = _utc_timestamp()
    resolved_effective_to: date | None = None
    if latest is not None and latest.effective_from == effective_from:
        latest.superseded_by_revision_id = revision_id
        resolved_effective_to = latest.effective_to
    elif latest is not None and (
        latest.effective_to is None or latest.effective_to >= effective_from
    ):
        latest.effective_to = effective_from - timedelta(days=1)
    record = TaxonomyConfigurationRevisionModel(
        taxonomy_configuration_revision_id=revision_id,
        portfolio_id=portfolio_id,
        taxonomy_id=taxonomy_id,
        effective_from=effective_from,
        effective_to=resolved_effective_to,
        configuration_version=version,
        configuration_json=deepcopy(payload),
        superseded_by_revision_id=None,
        created_at=now,
    )
    session.add(record)
    session.flush()
    return record


def taxonomy_configuration_as_of_in_session(
    session,
    portfolio_id: str,
    taxonomy_id: str,
    as_of_date: date,
) -> dict[str, object] | None:
    record = session.scalar(
        select(TaxonomyConfigurationRevisionModel)
        .where(
            TaxonomyConfigurationRevisionModel.portfolio_id == portfolio_id,
            TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id,
            TaxonomyConfigurationRevisionModel.superseded_by_revision_id.is_(None),
            TaxonomyConfigurationRevisionModel.effective_from <= as_of_date,
            or_(
                TaxonomyConfigurationRevisionModel.effective_to.is_(None),
                TaxonomyConfigurationRevisionModel.effective_to >= as_of_date,
            ),
        )
        .order_by(TaxonomyConfigurationRevisionModel.configuration_version.desc())
        .limit(1)
    )
    if record is None:
        return None
    payload = deepcopy(record.configuration_json)
    payload["configuration_version"] = record.configuration_version
    payload["effective_from"] = record.effective_from.isoformat()
    payload["effective_to"] = (
        record.effective_to.isoformat() if record.effective_to is not None else None
    )
    payload["taxonomy_configuration_revision_id"] = (
        record.taxonomy_configuration_revision_id
    )
    return payload


def taxonomy_configuration_as_of(
    portfolio_id: str,
    taxonomy_id: str,
    as_of_date: date,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        return taxonomy_configuration_as_of_in_session(
            session,
            portfolio_id,
            taxonomy_id,
            as_of_date,
        )


def taxonomy_configuration_revisions_through(
    portfolio_id: str,
    taxonomy_id: str,
    end_date: date,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        records = session.scalars(
            select(TaxonomyConfigurationRevisionModel)
            .where(
                TaxonomyConfigurationRevisionModel.portfolio_id == portfolio_id,
                TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id,
                TaxonomyConfigurationRevisionModel.superseded_by_revision_id.is_(
                    None
                ),
                TaxonomyConfigurationRevisionModel.effective_from <= end_date,
            )
            .order_by(
                TaxonomyConfigurationRevisionModel.effective_from,
                TaxonomyConfigurationRevisionModel.configuration_version,
            )
        ).all()
        revisions: list[dict[str, object]] = []
        for record in records:
            payload = deepcopy(record.configuration_json)
            payload.update(
                {
                    "taxonomy_configuration_revision_id": (
                        record.taxonomy_configuration_revision_id
                    ),
                    "configuration_version": record.configuration_version,
                    "effective_from": record.effective_from.isoformat(),
                    "effective_to": (
                        record.effective_to.isoformat()
                        if record.effective_to is not None
                        else None
                    ),
                }
            )
            revisions.append(payload)
        return revisions


def _serialize_taxonomy_selection(
    record: AnalyticsTaxonomySelectionRecordModel,
) -> dict[str, object]:
    return {
        "analytics_taxonomy_selection_id": record.analytics_taxonomy_selection_id,
        "portfolio_id": record.portfolio_id,
        "taxonomy_id": record.taxonomy_id,
        "effective_from": record.effective_from.isoformat(),
        "effective_to": (
            record.effective_to.isoformat() if record.effective_to is not None else None
        ),
        "selection_version": record.selection_version,
        "superseded_by_selection_id": record.superseded_by_selection_id,
        "created_at": record.created_at,
    }


def list_analytics_taxonomy_selections(
    portfolio_id: str,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        records = session.scalars(
            select(AnalyticsTaxonomySelectionRecordModel)
            .where(
                AnalyticsTaxonomySelectionRecordModel.portfolio_id == portfolio_id
            )
            .order_by(
                AnalyticsTaxonomySelectionRecordModel.effective_from,
                AnalyticsTaxonomySelectionRecordModel.selection_version,
            )
        ).all()
        return [_serialize_taxonomy_selection(record) for record in records]


def analytics_taxonomy_selection_as_of_in_session(
    session,
    *,
    portfolio_id: str,
    as_of_date: date,
) -> AnalyticsTaxonomySelectionRecordModel | None:
    return session.scalar(
        select(AnalyticsTaxonomySelectionRecordModel)
        .where(
            AnalyticsTaxonomySelectionRecordModel.portfolio_id == portfolio_id,
            AnalyticsTaxonomySelectionRecordModel.superseded_by_selection_id.is_(
                None
            ),
            AnalyticsTaxonomySelectionRecordModel.effective_from <= as_of_date,
            or_(
                AnalyticsTaxonomySelectionRecordModel.effective_to.is_(None),
                AnalyticsTaxonomySelectionRecordModel.effective_to >= as_of_date,
            ),
        )
        .order_by(
            AnalyticsTaxonomySelectionRecordModel.selection_version.desc()
        )
        .limit(1)
    )


def set_analytics_taxonomy_selection_in_session(
    session,
    *,
    portfolio_id: str,
    taxonomy_id: str | None,
    effective_from: date,
) -> AnalyticsTaxonomySelectionRecordModel:
    portfolio = session.get(PortfolioRecordModel, portfolio_id)
    if portfolio is None:
        raise ValueError("Portfolio not found.")
    resolved_taxonomy_id = str(taxonomy_id or "").strip() or None
    if resolved_taxonomy_id is not None:
        taxonomy = session.scalar(
            select(TaxonomyRecordModel).where(
                TaxonomyRecordModel.portfolio_id == portfolio_id,
                TaxonomyRecordModel.taxonomy_id == resolved_taxonomy_id,
            )
        )
        if taxonomy is None:
            raise ValueError("Planning taxonomy not found.")
        if not taxonomy.planning_enabled or taxonomy.status != "active":
            raise ValueError("Analytics taxonomy must be active and planning-enabled.")
        if taxonomy.primary_assignment_scope != "instrument":
            raise ValueError("Analytics taxonomy must use instrument assignment scope.")

    active_records = session.scalars(
        select(AnalyticsTaxonomySelectionRecordModel)
        .where(
            AnalyticsTaxonomySelectionRecordModel.portfolio_id == portfolio_id,
            AnalyticsTaxonomySelectionRecordModel.superseded_by_selection_id.is_(
                None
            ),
        )
        .order_by(
            AnalyticsTaxonomySelectionRecordModel.effective_from,
            AnalyticsTaxonomySelectionRecordModel.selection_version,
        )
        .with_for_update()
    ).all()
    latest = active_records[-1] if active_records else None
    if latest is not None and effective_from < latest.effective_from:
        raise ValueError(
            "Analytics taxonomy selections must be appended in effective-date order."
        )

    selection_id = f"taxonomy-selection-{uuid4().hex}"
    effective_to: date | None = None
    if latest is not None and latest.effective_from == effective_from:
        latest.superseded_by_selection_id = selection_id
        effective_to = latest.effective_to
    elif latest is not None and (
        latest.effective_to is None or latest.effective_to >= effective_from
    ):
        latest.effective_to = effective_from - timedelta(days=1)

    record = AnalyticsTaxonomySelectionRecordModel(
        analytics_taxonomy_selection_id=selection_id,
        portfolio_id=portfolio_id,
        taxonomy_id=resolved_taxonomy_id,
        effective_from=effective_from,
        effective_to=effective_to,
        selection_version=_next_policy_version(session, portfolio_id),
        superseded_by_selection_id=None,
        created_at=_utc_timestamp(),
    )
    session.add(record)
    session.flush()

    current_date = portfolio.as_of_date or date.today()
    current_selection = analytics_taxonomy_selection_as_of_in_session(
        session,
        portfolio_id=portfolio_id,
        as_of_date=current_date,
    )
    portfolio.default_planning_taxonomy_id = (
        current_selection.taxonomy_id if current_selection is not None else None
    )
    session.flush()
    return record


def _effective_policies_by_node(
    session,
    *,
    portfolio_id: str,
    taxonomy_id: str,
    as_of_date: date,
) -> dict[str, AnalyticsScopePolicyRecordModel]:
    records = session.scalars(
        select(AnalyticsScopePolicyRecordModel).where(
            AnalyticsScopePolicyRecordModel.portfolio_id == portfolio_id,
            AnalyticsScopePolicyRecordModel.taxonomy_id == taxonomy_id,
            AnalyticsScopePolicyRecordModel.superseded_by_policy_id.is_(None),
            AnalyticsScopePolicyRecordModel.effective_from <= as_of_date,
            or_(
                AnalyticsScopePolicyRecordModel.effective_to.is_(None),
                AnalyticsScopePolicyRecordModel.effective_to >= as_of_date,
            ),
        )
    ).all()
    return {record.taxonomy_node_id: record for record in records}


def resolve_instrument_analytics_scopes(
    portfolio_id: str,
    *,
    as_of_date: date,
    instrument_ids: list[str],
    taxonomy_id: str | None = None,
) -> dict[str, dict[str, object]]:
    normalized_ids = list(dict.fromkeys(item.strip() for item in instrument_ids if item.strip()))
    session_factory = get_session_factory()
    with session_factory() as session:
        selection = None
        if taxonomy_id is None:
            selection = analytics_taxonomy_selection_as_of_in_session(
                session,
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
            )
        resolved_taxonomy_id = taxonomy_id or (
            selection.taxonomy_id if selection is not None else None
        )
        if not resolved_taxonomy_id:
            return {
                instrument_id: _missing_scope_resolution(
                    instrument_id,
                    reason=(
                        "No effective analytics taxonomy selection is configured."
                        if selection is None
                        else "The effective analytics taxonomy selection is explicitly unassigned."
                    ),
                )
                for instrument_id in normalized_ids
            }
        configuration = taxonomy_configuration_as_of_in_session(
            session,
            portfolio_id,
            resolved_taxonomy_id,
            as_of_date,
        )
        if configuration is None:
            return {
                instrument_id: _missing_scope_resolution(
                    instrument_id,
                    taxonomy_id=resolved_taxonomy_id,
                    reason="No effective taxonomy configuration revision exists.",
                )
                for instrument_id in normalized_ids
            }
        taxonomy_payload = (
            configuration.get("taxonomy")
            if isinstance(configuration.get("taxonomy"), dict)
            else {}
        )
        if str(taxonomy_payload.get("status") or "") != "active":
            return {
                instrument_id: _missing_scope_resolution(
                    instrument_id,
                    taxonomy_id=resolved_taxonomy_id,
                    reason="The effective taxonomy configuration is inactive or deleted.",
                )
                for instrument_id in normalized_ids
            }

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
        policies = _effective_policies_by_node(
            session,
            portfolio_id=portfolio_id,
            taxonomy_id=resolved_taxonomy_id,
            as_of_date=as_of_date,
        )
        results: dict[str, dict[str, object]] = {}
        for instrument_id in normalized_ids:
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
                    taxonomy_id=resolved_taxonomy_id,
                    taxonomy_node_id=assigned_node_id,
                    reason="No effective analytics scope policy resolves for the assignment.",
                )
                continue
            results[instrument_id] = {
                "instrument_id": instrument_id,
                "scope_status": "resolved",
                "taxonomy_id": resolved_taxonomy_id,
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
                "taxonomy_selection_version": (
                    selection.selection_version if selection is not None else None
                ),
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
