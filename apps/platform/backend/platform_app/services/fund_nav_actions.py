from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging

from portfolio_ops_instrument_core.models import (
    FUND_INSTRUMENT_TYPES,
    FundNavEvent,
    FundNavReinvestmentEvidence,
)

from platform_app.db.session import get_session_factory
from platform_app.services.fund_nav_action_candidates import (
    FundNavActionCandidateConflictError,
    FundNavActionCandidateNotFoundError,
    FundNavActionCandidateRepository,
)
from platform_app.services.instrument_store import (
    StaleFundNavPublicationError,
    get_instrument,
    publish_fund_nav_history,
)
from platform_app.services.market_data_ops import (
    _build_fund_nav_publication,
    _load_all_durable_nav_source_rows,
)


_MAX_AUDIT_TEXT_LENGTH = 4096
_MAX_CLIENT_MUTATION_ID_LENGTH = 200
_MAX_PUBLISH_ATTEMPTS = 3


logger = logging.getLogger(__name__)

_ACTION_SNAPSHOT_FIELDS = frozenset(
    {
        "event_type",
        "announcement_date",
        "record_date",
        "effective_date",
        "payable_date",
        "sequence_order",
        "cash_per_unit",
        "unit_ratio",
        "evidence_kind",
        "source",
        "external_event_id",
        "provenance",
    }
)
_EVIDENCE_SNAPSHOT_FIELDS = frozenset(
    {
        "reinvestment_nav",
        "evidence_kind",
        "source",
        "external_evidence_id",
        "provenance",
    }
)


class FundNavAdminConflictError(ValueError):
    """An immutable revision or optimistic predecessor conflicts with history."""


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _required_text(
    value: object,
    *,
    field_name: str,
    max_length: int = _MAX_AUDIT_TEXT_LENGTH,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required.")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} is too long.")
    return normalized


def _audit_fields(
    *,
    client_mutation_id: str,
    recorded_by: str,
    revision_reason: str,
) -> tuple[str, str, str]:
    return (
        _required_text(
            client_mutation_id,
            field_name="client_mutation_id",
            max_length=_MAX_CLIENT_MUTATION_ID_LENGTH,
        ),
        _required_text(recorded_by, field_name="recorded_by"),
        _required_text(revision_reason, field_name="revision_reason"),
    )


def _deterministic_id(prefix: str, *parts: object) -> str:
    identity = "\0".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _event_id_for_client_mutation(
    *,
    instrument_id: str,
    client_mutation_id: str,
) -> str:
    return _deterministic_id(
        "fund-nav-event",
        instrument_id,
        "admin-client-mutation",
        client_mutation_id,
    )


def _evidence_id_for_client_mutation(
    *,
    instrument_id: str,
    client_mutation_id: str,
) -> str:
    return _deterministic_id(
        "fund-nav-reinvestment-evidence",
        instrument_id,
        "admin-client-mutation",
        client_mutation_id,
    )


def _json_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot_payload(
    payload: Mapping[str, object] | None,
    *,
    field_name: str,
    allowed_fields: frozenset[str],
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{field_name} must be an object.")
    normalized = deepcopy(dict(payload))
    unexpected = sorted(set(normalized).difference(allowed_fields))
    if unexpected:
        raise ValueError(
            f"{field_name} has unsupported fields: " + ", ".join(unexpected)
        )
    return normalized


def _dict_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _event_revision_history(instrument: Mapping[str, object]) -> list[dict[str, object]]:
    return _dict_items(instrument.get("fund_nav_event_revisions"))


def _current_events(instrument: Mapping[str, object]) -> list[dict[str, object]]:
    return _dict_items(instrument.get("fund_nav_events"))


def _evidence_revision_history(
    instrument: Mapping[str, object],
) -> list[dict[str, object]]:
    return _dict_items(instrument.get("fund_nav_reinvestment_evidence_revisions"))


def _current_evidence(instrument: Mapping[str, object]) -> list[dict[str, object]]:
    return _dict_items(instrument.get("fund_nav_reinvestment_evidence"))


def _find_by_id(
    rows: list[dict[str, object]],
    *,
    id_field: str,
    target_id: str,
) -> dict[str, object] | None:
    return next(
        (row for row in rows if str(row.get(id_field) or "") == target_id),
        None,
    )


def _require_fund(instrument: Mapping[str, object]) -> None:
    if str(instrument.get("instrument_type") or "").strip().lower() not in FUND_INSTRUMENT_TYPES:
        raise ValueError("Fund NAV actions require a public or private fund instrument.")


def _event_model(
    *,
    instrument_id: str,
    fund_nav_event_id: str,
    fund_nav_action_id: str,
    revision_number: int,
    revision_kind: str,
    supersedes_fund_nav_event_id: str | None,
    snapshot: Mapping[str, object],
    recorded_by: str,
    revision_reason: str,
) -> FundNavEvent:
    now = _utcnow_iso()
    return FundNavEvent.model_validate(
        {
            **deepcopy(dict(snapshot)),
            "fund_nav_event_id": fund_nav_event_id,
            "fund_nav_action_id": fund_nav_action_id,
            "revision_number": revision_number,
            "revision_kind": revision_kind,
            "supersedes_fund_nav_event_id": supersedes_fund_nav_event_id,
            "instrument_id": instrument_id,
            "recorded_by": recorded_by,
            "revision_reason": revision_reason,
            "created_at": now,
            "updated_at": now,
        }
    )


def _evidence_model(
    *,
    instrument_id: str,
    evidence_id: str,
    fund_nav_event_id: str,
    revision_number: int,
    revision_kind: str,
    supersedes_evidence_id: str | None,
    snapshot: Mapping[str, object],
    recorded_by: str,
    revision_reason: str,
) -> FundNavReinvestmentEvidence:
    now = _utcnow_iso()
    return FundNavReinvestmentEvidence.model_validate(
        {
            **deepcopy(dict(snapshot)),
            "fund_nav_reinvestment_evidence_id": evidence_id,
            "instrument_id": instrument_id,
            "fund_nav_event_id": fund_nav_event_id,
            "revision_number": revision_number,
            "revision_kind": revision_kind,
            "supersedes_fund_nav_reinvestment_evidence_id": (
                supersedes_evidence_id
            ),
            "recorded_by": recorded_by,
            "revision_reason": revision_reason,
            "created_at": now,
            "updated_at": now,
        }
    )


def _model_without_timestamps(model: object) -> dict[str, object]:
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    payload.pop("created_at", None)
    payload.pop("updated_at", None)
    return payload


def _assert_event_replay(
    persisted: Mapping[str, object],
    incoming: FundNavEvent,
) -> None:
    persisted_model = FundNavEvent.model_validate(dict(persisted))
    if _model_without_timestamps(persisted_model) != _model_without_timestamps(
        incoming
    ):
        raise FundNavAdminConflictError(
            "client_mutation_id is already bound to a different fund NAV action revision."
        )


def _assert_evidence_replay(
    persisted: Mapping[str, object],
    incoming: FundNavReinvestmentEvidence,
) -> None:
    persisted_model = FundNavReinvestmentEvidence.model_validate(dict(persisted))
    if _model_without_timestamps(persisted_model) != _model_without_timestamps(
        incoming
    ):
        raise FundNavAdminConflictError(
            "client_mutation_id is already bound to different reinvestment evidence."
        )


def _event_snapshot_from_revision(revision: Mapping[str, object]) -> dict[str, object]:
    return {field: deepcopy(revision.get(field)) for field in _ACTION_SNAPSHOT_FIELDS}


def _evidence_snapshot_from_revision(
    revision: Mapping[str, object],
) -> dict[str, object]:
    return {
        field: deepcopy(revision.get(field)) for field in _EVIDENCE_SNAPSHOT_FIELDS
    }


@dataclass(frozen=True)
class _MutationPlan:
    builder_instrument: dict[str, object]
    event_revisions: tuple[dict[str, object], ...] = ()
    evidence_revisions: tuple[dict[str, object], ...] = ()
    result_event_id: str | None = None
    result_evidence_id: str | None = None


def _view_with_mutation(
    *,
    instrument: Mapping[str, object],
    event: FundNavEvent | None = None,
    evidence: FundNavReinvestmentEvidence | None = None,
) -> dict[str, object]:
    view = deepcopy(dict(instrument))
    events = _current_events(instrument)
    evidences = _current_evidence(instrument)
    if event is not None:
        events = [
            item
            for item in events
            if str(item.get("fund_nav_action_id") or "")
            != event.fund_nav_action_id
        ]
        if event.revision_kind != "cancellation":
            events.append(event.model_dump(mode="json"))
        active_event_ids = {
            str(item.get("fund_nav_event_id") or "") for item in events
        }
        # Evidence is attached to an exact event revision. A corrected action
        # therefore starts without evidence unless this same mutation supplies
        # an explicit new evidence snapshot.
        evidences = [
            item
            for item in evidences
            if str(item.get("fund_nav_event_id") or "") in active_event_ids
        ]
    if evidence is not None:
        evidences = [
            item
            for item in evidences
            if str(item.get("fund_nav_event_id") or "")
            != evidence.fund_nav_event_id
        ]
        if evidence.revision_kind != "cancellation":
            evidences.append(evidence.model_dump(mode="json"))
    view["fund_nav_events"] = events
    view["fund_nav_reinvestment_evidence"] = evidences
    return view


def _persisted_revision(
    result: Mapping[str, object],
    *,
    collection: str,
    id_field: str,
    target_id: str | None,
) -> dict[str, object] | None:
    if target_id is None:
        return None
    record = result.get("record")
    if not isinstance(record, Mapping):
        return None
    return _find_by_id(
        _dict_items(record.get(collection)),
        id_field=id_field,
        target_id=target_id,
    )


def _publish_admin_mutation(
    *,
    instrument_id: str,
    recorded_by: str,
    source_provider: str,
    message: str,
    build_plan: Callable[[dict[str, object]], _MutationPlan],
    before_registry_publish: Callable[[], None] | None = None,
    before_candidate_projection: (
        Callable[[dict[str, object]], Mapping[str, object] | None] | None
    ) = None,
) -> dict[str, object] | None:
    for _attempt in range(_MAX_PUBLISH_ATTEMPTS):
        instrument = get_instrument(instrument_id)
        if instrument is None:
            return None
        _require_fund(instrument)
        plan = build_plan(instrument)
        publication = _build_fund_nav_publication(
            instrument_id=instrument_id,
            rows=_load_all_durable_nav_source_rows(
                instrument_id=instrument_id,
                instrument=instrument,
            ),
            source_provider=source_provider,
            instrument=plan.builder_instrument,
        )
        if before_registry_publish is not None:
            before_registry_publish()
        try:
            persisted = publish_fund_nav_history(
                instrument_id=instrument_id,
                rows=publication.rows,
                projection_run={
                    **deepcopy(publication.projection_run),
                    "created_by": recorded_by,
                },
                current_fund_nav_event_ids=(
                    publication.current_fund_nav_event_ids
                ),
                current_fund_nav_reinvestment_evidence_ids=(
                    publication.current_fund_nav_reinvestment_evidence_ids
                ),
                event_revisions=plan.event_revisions,
                reinvestment_evidence_revisions=plan.evidence_revisions,
                adjustment_factors=publication.adjustment_factors,
                expected_market_data_updated_at=(
                    str(instrument["market_data_updated_at"])
                    if instrument.get("market_data_updated_at") is not None
                    else None
                ),
                refresh_status="imported",
                updated_by=recorded_by,
                message=message,
                mode="manual",
            )
        except StaleFundNavPublicationError:
            continue
        if persisted is None:
            return None
        result = dict(persisted)
        result["fund_nav_event"] = _persisted_revision(
            result,
            collection="fund_nav_event_revisions",
            id_field="fund_nav_event_id",
            target_id=plan.result_event_id,
        )
        result["fund_nav_reinvestment_evidence"] = _persisted_revision(
            result,
            collection="fund_nav_reinvestment_evidence_revisions",
            id_field="fund_nav_reinvestment_evidence_id",
            target_id=plan.result_evidence_id,
        )
        operational_warnings: list[str] = []
        if before_candidate_projection is not None:
            try:
                extra_result = before_candidate_projection(result)
            except Exception:
                logger.exception(
                    "Registry fund NAV mutation committed, but the reserved "
                    "candidate decision could not be completed for %s.",
                    instrument_id,
                )
                result["candidate_confirmation_status"] = "confirming"
                operational_warnings.append(
                    "The Registry mutation committed, but the candidate decision "
                    "remains durably reserved and must be resumed."
                )
            else:
                if extra_result is not None:
                    result.update(dict(extra_result))
        # A candidate-confirm callback must commit the terminal resolution
        # before the rebuilt projection supersedes signals that disappeared.
        # If the process stops between these operations, replay is safe: the
        # Registry publication and terminal decision are both idempotent.
        try:
            FundNavActionCandidateRepository(
                get_session_factory()
            ).project_current(
                instrument_id=instrument_id,
                candidates=publication.action_candidates,
            )
        except Exception:
            logger.exception(
                "Registry fund NAV mutation committed, but the derived candidate "
                "projection could not be synchronized for %s.",
                instrument_id,
            )
            result["candidate_projection_synchronized"] = False
            operational_warnings.append(
                "The Registry mutation committed, but the derived candidate view "
                "is awaiting reconciliation."
            )
        else:
            result["candidate_projection_synchronized"] = True
        result["operational_warnings"] = operational_warnings
        return result
    raise StaleFundNavPublicationError(
        f'Fund NAV admin mutation for "{instrument_id}" remained stale.'
    )


def _original_action_plan(
    *,
    instrument: dict[str, object],
    instrument_id: str,
    action_id: str,
    event_id: str,
    action_payload: Mapping[str, object],
    evidence_id: str,
    evidence_payload: Mapping[str, object] | None,
    recorded_by: str,
    revision_reason: str,
) -> _MutationPlan:
    event = _event_model(
        instrument_id=instrument_id,
        fund_nav_event_id=event_id,
        fund_nav_action_id=action_id,
        revision_number=1,
        revision_kind="original",
        supersedes_fund_nav_event_id=None,
        snapshot=_snapshot_payload(
            action_payload,
            field_name="action",
            allowed_fields=_ACTION_SNAPSHOT_FIELDS,
        ),
        recorded_by=recorded_by,
        revision_reason=revision_reason,
    )
    existing = _find_by_id(
        _event_revision_history(instrument),
        id_field="fund_nav_event_id",
        target_id=event_id,
    )
    if existing is not None:
        _assert_event_replay(existing, event)
    is_replay = existing is not None
    conflicting_action = next(
        (
            item
            for item in _event_revision_history(instrument)
            if str(item.get("fund_nav_action_id") or "") == action_id
            and int(item.get("revision_number") or 0) == 1
            and str(item.get("fund_nav_event_id") or "") != event_id
        ),
        None,
    )
    if conflicting_action is not None:
        raise FundNavAdminConflictError(
            "client_mutation_id is already bound to an existing fund NAV action."
        )

    existing_evidence = _find_by_id(
        _evidence_revision_history(instrument),
        id_field="fund_nav_reinvestment_evidence_id",
        target_id=evidence_id,
    )
    evidence: FundNavReinvestmentEvidence | None = None
    if evidence_payload is not None:
        if event.event_type != "cash_distribution":
            raise ValueError(
                "Reinvestment evidence is only valid for a cash distribution."
            )
        evidence = _evidence_model(
            instrument_id=instrument_id,
            evidence_id=evidence_id,
            fund_nav_event_id=event_id,
            revision_number=1,
            revision_kind="original",
            supersedes_evidence_id=None,
            snapshot=_snapshot_payload(
                evidence_payload,
                field_name="reinvestment_evidence",
                allowed_fields=_EVIDENCE_SNAPSHOT_FIELDS,
            ),
            recorded_by=recorded_by,
            revision_reason=revision_reason,
        )
        if existing_evidence is not None:
            _assert_evidence_replay(existing_evidence, evidence)
        elif is_replay:
            raise FundNavAdminConflictError(
                "client_mutation_id was already applied without this reinvestment evidence."
            )
    elif existing_evidence is not None:
        raise FundNavAdminConflictError(
            "client_mutation_id was already applied with reinvestment evidence."
        )

    view = (
        deepcopy(instrument)
        if is_replay
        else _view_with_mutation(
            instrument=instrument,
            event=event,
            evidence=evidence,
        )
    )
    return _MutationPlan(
        builder_instrument=view,
        event_revisions=(event.model_dump(mode="json"),),
        evidence_revisions=(
            (evidence.model_dump(mode="json"),) if evidence is not None else ()
        ),
        result_event_id=event_id,
        result_evidence_id=evidence_id if evidence is not None else None,
    )


def create_fund_nav_action(
    *,
    instrument_id: str,
    action_payload: Mapping[str, object],
    reinvestment_evidence_payload: Mapping[str, object] | None,
    client_mutation_id: str,
    recorded_by: str,
    revision_reason: str,
) -> dict[str, object] | None:
    mutation_id, actor, reason = _audit_fields(
        client_mutation_id=client_mutation_id,
        recorded_by=recorded_by,
        revision_reason=revision_reason,
    )
    action_id = _deterministic_id("fund-nav-action", instrument_id, mutation_id)
    event_id = _event_id_for_client_mutation(
        instrument_id=instrument_id,
        client_mutation_id=mutation_id,
    )
    evidence_id = _evidence_id_for_client_mutation(
        instrument_id=instrument_id,
        client_mutation_id=mutation_id,
    )
    return _publish_admin_mutation(
        instrument_id=instrument_id,
        recorded_by=actor,
        source_provider="admin_fund_nav_action",
        message=f"Created fund NAV action {action_id} and rebuilt its NAV projection.",
        build_plan=lambda instrument: _original_action_plan(
            instrument=instrument,
            instrument_id=instrument_id,
            action_id=action_id,
            event_id=event_id,
            action_payload=action_payload,
            evidence_id=evidence_id,
            evidence_payload=reinvestment_evidence_payload,
            recorded_by=actor,
            revision_reason=reason,
        ),
    )


def revise_fund_nav_action(
    *,
    instrument_id: str,
    action_id: str,
    predecessor_fund_nav_event_id: str,
    revision_kind: str,
    action_payload: Mapping[str, object] | None,
    reinvestment_evidence_payload: Mapping[str, object] | None,
    client_mutation_id: str,
    recorded_by: str,
    revision_reason: str,
) -> dict[str, object] | None:
    mutation_id, actor, reason = _audit_fields(
        client_mutation_id=client_mutation_id,
        recorded_by=recorded_by,
        revision_reason=revision_reason,
    )
    normalized_action_id = _required_text(action_id, field_name="action_id")
    predecessor_id = _required_text(
        predecessor_fund_nav_event_id,
        field_name="predecessor_fund_nav_event_id",
    )
    normalized_kind = str(revision_kind or "").strip().lower()
    if normalized_kind not in {"correction", "cancellation"}:
        raise ValueError("revision_kind must be correction or cancellation.")
    if normalized_kind == "correction" and action_payload is None:
        raise ValueError("A correction requires a complete action snapshot.")
    if normalized_kind == "cancellation" and action_payload is not None:
        raise ValueError("An action cancellation must not include an action snapshot.")
    if normalized_kind == "cancellation" and reinvestment_evidence_payload is not None:
        raise ValueError("A cancelled action cannot publish reinvestment evidence.")

    def build_plan(instrument: dict[str, object]) -> _MutationPlan:
        predecessor = _find_by_id(
            _event_revision_history(instrument),
            id_field="fund_nav_event_id",
            target_id=predecessor_id,
        )
        if predecessor is None:
            raise FundNavAdminConflictError("The action predecessor does not exist.")
        if str(predecessor.get("fund_nav_action_id") or "") != normalized_action_id:
            raise FundNavAdminConflictError(
                "The predecessor does not belong to the requested action."
            )
        revision_number = int(predecessor.get("revision_number") or 0) + 1
        event_id = _event_id_for_client_mutation(
            instrument_id=instrument_id,
            client_mutation_id=mutation_id,
        )
        if event_id == predecessor_id:
            raise FundNavAdminConflictError(
                "client_mutation_id is already bound to a different fund NAV action revision."
            )
        snapshot = (
            _snapshot_payload(
                action_payload,
                field_name="action",
                allowed_fields=_ACTION_SNAPSHOT_FIELDS,
            )
            if normalized_kind == "correction"
            else _event_snapshot_from_revision(predecessor)
        )
        event = _event_model(
            instrument_id=instrument_id,
            fund_nav_event_id=event_id,
            fund_nav_action_id=normalized_action_id,
            revision_number=revision_number,
            revision_kind=normalized_kind,
            supersedes_fund_nav_event_id=predecessor_id,
            snapshot=snapshot,
            recorded_by=actor,
            revision_reason=reason,
        )
        existing = _find_by_id(
            _event_revision_history(instrument),
            id_field="fund_nav_event_id",
            target_id=event_id,
        )
        if existing is not None:
            _assert_event_replay(existing, event)
        else:
            current_predecessor = _find_by_id(
                _current_events(instrument),
                id_field="fund_nav_event_id",
                target_id=predecessor_id,
            )
            if current_predecessor is None:
                raise FundNavAdminConflictError(
                    "The action predecessor is stale; reload the current revision."
                )
        is_replay = existing is not None

        evidence_id = _evidence_id_for_client_mutation(
            instrument_id=instrument_id,
            client_mutation_id=mutation_id,
        )
        existing_evidence = _find_by_id(
            _evidence_revision_history(instrument),
            id_field="fund_nav_reinvestment_evidence_id",
            target_id=evidence_id,
        )
        evidence: FundNavReinvestmentEvidence | None = None
        if reinvestment_evidence_payload is not None:
            if event.event_type != "cash_distribution":
                raise ValueError(
                    "Reinvestment evidence is only valid for a cash distribution."
                )
            evidence = _evidence_model(
                instrument_id=instrument_id,
                evidence_id=evidence_id,
                fund_nav_event_id=event_id,
                revision_number=1,
                revision_kind="original",
                supersedes_evidence_id=None,
                snapshot=_snapshot_payload(
                    reinvestment_evidence_payload,
                    field_name="reinvestment_evidence",
                    allowed_fields=_EVIDENCE_SNAPSHOT_FIELDS,
                ),
                recorded_by=actor,
                revision_reason=reason,
            )
            if existing_evidence is not None:
                _assert_evidence_replay(existing_evidence, evidence)
            elif is_replay:
                raise FundNavAdminConflictError(
                    "client_mutation_id was already applied without this reinvestment evidence."
                )
        elif existing_evidence is not None:
            raise FundNavAdminConflictError(
                "client_mutation_id was already applied with reinvestment evidence."
            )

        return _MutationPlan(
            builder_instrument=(
                deepcopy(instrument)
                if is_replay
                else _view_with_mutation(
                    instrument=instrument,
                    event=event,
                    evidence=evidence,
                )
            ),
            event_revisions=(event.model_dump(mode="json"),),
            evidence_revisions=(
                (evidence.model_dump(mode="json"),)
                if evidence is not None
                else ()
            ),
            result_event_id=event_id,
            result_evidence_id=evidence_id,
        )

    return _publish_admin_mutation(
        instrument_id=instrument_id,
        recorded_by=actor,
        source_provider="admin_fund_nav_action_revision",
        message=(
            f"Appended {normalized_kind} revision to fund NAV action "
            f"{normalized_action_id} and rebuilt its NAV projection."
        ),
        build_plan=build_plan,
    )


def create_fund_nav_reinvestment_evidence(
    *,
    instrument_id: str,
    fund_nav_event_id: str,
    evidence_payload: Mapping[str, object],
    client_mutation_id: str,
    recorded_by: str,
    revision_reason: str,
) -> dict[str, object] | None:
    mutation_id, actor, reason = _audit_fields(
        client_mutation_id=client_mutation_id,
        recorded_by=recorded_by,
        revision_reason=revision_reason,
    )
    event_id = _required_text(fund_nav_event_id, field_name="fund_nav_event_id")
    evidence_id = _evidence_id_for_client_mutation(
        instrument_id=instrument_id,
        client_mutation_id=mutation_id,
    )
    mutation_event_id = _event_id_for_client_mutation(
        instrument_id=instrument_id,
        client_mutation_id=mutation_id,
    )

    def build_plan(instrument: dict[str, object]) -> _MutationPlan:
        if _find_by_id(
            _event_revision_history(instrument),
            id_field="fund_nav_event_id",
            target_id=mutation_event_id,
        ) is not None:
            raise FundNavAdminConflictError(
                "client_mutation_id is already bound to a fund NAV action mutation."
            )
        existing = _find_by_id(
            _evidence_revision_history(instrument),
            id_field="fund_nav_reinvestment_evidence_id",
            target_id=evidence_id,
        )
        if existing is not None:
            evidence = _evidence_model(
                instrument_id=instrument_id,
                evidence_id=evidence_id,
                fund_nav_event_id=event_id,
                revision_number=1,
                revision_kind="original",
                supersedes_evidence_id=None,
                snapshot=_snapshot_payload(
                    evidence_payload,
                    field_name="evidence",
                    allowed_fields=_EVIDENCE_SNAPSHOT_FIELDS,
                ),
                recorded_by=actor,
                revision_reason=reason,
            )
            _assert_evidence_replay(existing, evidence)
            return _MutationPlan(
                builder_instrument=deepcopy(instrument),
                evidence_revisions=(evidence.model_dump(mode="json"),),
                result_evidence_id=evidence_id,
            )
        event = _find_by_id(
            _current_events(instrument),
            id_field="fund_nav_event_id",
            target_id=event_id,
        )
        if event is None:
            raise FundNavAdminConflictError(
                "Reinvestment evidence requires the current action revision."
            )
        if str(event.get("event_type") or "") != "cash_distribution":
            raise ValueError(
                "Reinvestment evidence is only valid for a cash distribution."
            )
        existing_for_event = next(
            (
                item
                for item in _current_evidence(instrument)
                if str(item.get("fund_nav_event_id") or "") == event_id
                and str(item.get("fund_nav_reinvestment_evidence_id") or "")
                != evidence_id
            ),
            None,
        )
        if existing_for_event is not None:
            raise FundNavAdminConflictError(
                "The action revision already has current reinvestment evidence; append a correction instead."
            )
        evidence = _evidence_model(
            instrument_id=instrument_id,
            evidence_id=evidence_id,
            fund_nav_event_id=event_id,
            revision_number=1,
            revision_kind="original",
            supersedes_evidence_id=None,
            snapshot=_snapshot_payload(
                evidence_payload,
                field_name="evidence",
                allowed_fields=_EVIDENCE_SNAPSHOT_FIELDS,
            ),
            recorded_by=actor,
            revision_reason=reason,
        )
        return _MutationPlan(
            builder_instrument=_view_with_mutation(
                instrument=instrument,
                evidence=evidence,
            ),
            evidence_revisions=(evidence.model_dump(mode="json"),),
            result_evidence_id=evidence_id,
        )

    return _publish_admin_mutation(
        instrument_id=instrument_id,
        recorded_by=actor,
        source_provider="admin_fund_nav_reinvestment_evidence",
        message=(
            f"Added reinvestment evidence {evidence_id} and rebuilt the fund NAV projection."
        ),
        build_plan=build_plan,
    )


def revise_fund_nav_reinvestment_evidence(
    *,
    instrument_id: str,
    predecessor_fund_nav_reinvestment_evidence_id: str,
    revision_kind: str,
    evidence_payload: Mapping[str, object] | None,
    client_mutation_id: str,
    recorded_by: str,
    revision_reason: str,
) -> dict[str, object] | None:
    mutation_id, actor, reason = _audit_fields(
        client_mutation_id=client_mutation_id,
        recorded_by=recorded_by,
        revision_reason=revision_reason,
    )
    predecessor_id = _required_text(
        predecessor_fund_nav_reinvestment_evidence_id,
        field_name="predecessor_fund_nav_reinvestment_evidence_id",
    )
    normalized_kind = str(revision_kind or "").strip().lower()
    if normalized_kind not in {"correction", "cancellation"}:
        raise ValueError("revision_kind must be correction or cancellation.")
    if normalized_kind == "correction" and evidence_payload is None:
        raise ValueError("An evidence correction requires a complete snapshot.")
    if normalized_kind == "cancellation" and evidence_payload is not None:
        raise ValueError("An evidence cancellation must not include a snapshot.")

    def build_plan(instrument: dict[str, object]) -> _MutationPlan:
        predecessor = _find_by_id(
            _evidence_revision_history(instrument),
            id_field="fund_nav_reinvestment_evidence_id",
            target_id=predecessor_id,
        )
        if predecessor is None:
            raise FundNavAdminConflictError("The evidence predecessor does not exist.")
        event_id = str(predecessor.get("fund_nav_event_id") or "")
        revision_number = int(predecessor.get("revision_number") or 0) + 1
        evidence_id = _evidence_id_for_client_mutation(
            instrument_id=instrument_id,
            client_mutation_id=mutation_id,
        )
        if evidence_id == predecessor_id:
            raise FundNavAdminConflictError(
                "client_mutation_id is already bound to different reinvestment evidence."
            )
        mutation_event_id = _event_id_for_client_mutation(
            instrument_id=instrument_id,
            client_mutation_id=mutation_id,
        )
        if _find_by_id(
            _event_revision_history(instrument),
            id_field="fund_nav_event_id",
            target_id=mutation_event_id,
        ) is not None:
            raise FundNavAdminConflictError(
                "client_mutation_id is already bound to a fund NAV action mutation."
            )
        snapshot = (
            _snapshot_payload(
                evidence_payload,
                field_name="evidence",
                allowed_fields=_EVIDENCE_SNAPSHOT_FIELDS,
            )
            if normalized_kind == "correction"
            else _evidence_snapshot_from_revision(predecessor)
        )
        evidence = _evidence_model(
            instrument_id=instrument_id,
            evidence_id=evidence_id,
            fund_nav_event_id=event_id,
            revision_number=revision_number,
            revision_kind=normalized_kind,
            supersedes_evidence_id=predecessor_id,
            snapshot=snapshot,
            recorded_by=actor,
            revision_reason=reason,
        )
        existing = _find_by_id(
            _evidence_revision_history(instrument),
            id_field="fund_nav_reinvestment_evidence_id",
            target_id=evidence_id,
        )
        if existing is not None:
            _assert_evidence_replay(existing, evidence)
        else:
            current_predecessor = _find_by_id(
                _current_evidence(instrument),
                id_field="fund_nav_reinvestment_evidence_id",
                target_id=predecessor_id,
            )
            if current_predecessor is None:
                raise FundNavAdminConflictError(
                    "The evidence predecessor is stale; reload the current revision."
                )
        is_replay = existing is not None
        return _MutationPlan(
            builder_instrument=(
                deepcopy(instrument)
                if is_replay
                else _view_with_mutation(
                    instrument=instrument,
                    evidence=evidence,
                )
            ),
            evidence_revisions=(evidence.model_dump(mode="json"),),
            result_evidence_id=evidence_id,
        )

    return _publish_admin_mutation(
        instrument_id=instrument_id,
        recorded_by=actor,
        source_provider="admin_fund_nav_reinvestment_evidence_revision",
        message=(
            f"Appended {normalized_kind} revision to reinvestment evidence "
            f"{predecessor_id} and rebuilt the fund NAV projection."
        ),
        build_plan=build_plan,
    )


def _candidate_for_instrument(
    repository: FundNavActionCandidateRepository,
    *,
    instrument_id: str,
    candidate_id: str,
) -> dict[str, object]:
    candidate = next(
        (
            item
            for item in repository.list_history(instrument_id=instrument_id)
            if item["fund_nav_action_candidate_id"] == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise FundNavActionCandidateNotFoundError(candidate_id)
    return candidate


def list_fund_nav_action_candidates(
    *,
    instrument_id: str,
    include_history: bool,
) -> list[dict[str, object]] | None:
    if get_instrument(instrument_id) is None:
        return None
    repository = FundNavActionCandidateRepository(get_session_factory())
    if include_history:
        return repository.list_history(instrument_id=instrument_id)
    return repository.list_reviewable(instrument_id=instrument_id)


def reject_fund_nav_action_candidate(
    *,
    instrument_id: str,
    candidate_id: str,
    reason: str,
    decision_by: str,
) -> dict[str, object] | None:
    if get_instrument(instrument_id) is None:
        return None
    repository = FundNavActionCandidateRepository(get_session_factory())
    _candidate_for_instrument(
        repository,
        instrument_id=instrument_id,
        candidate_id=candidate_id,
    )
    return repository.reject(
        fund_nav_action_candidate_id=candidate_id,
        reason=reason,
        decision_by=decision_by,
    )


def _candidate_confirmation_intent(
    *,
    instrument_id: str,
    action_id: str,
    event_id: str,
    evidence_id: str,
    action_payload: Mapping[str, object],
    reinvestment_evidence_payload: Mapping[str, object] | None,
    client_mutation_id: str,
    recorded_by: str,
    revision_reason: str,
) -> tuple[dict[str, object], str]:
    event = _event_model(
        instrument_id=instrument_id,
        fund_nav_event_id=event_id,
        fund_nav_action_id=action_id,
        revision_number=1,
        revision_kind="original",
        supersedes_fund_nav_event_id=None,
        snapshot=_snapshot_payload(
            action_payload,
            field_name="action",
            allowed_fields=_ACTION_SNAPSHOT_FIELDS,
        ),
        recorded_by=recorded_by,
        revision_reason=revision_reason,
    )
    normalized_event = event.model_dump(mode="json")
    normalized_action = {
        field: deepcopy(normalized_event.get(field))
        for field in sorted(_ACTION_SNAPSHOT_FIELDS)
    }
    normalized_evidence: dict[str, object] | None = None
    if reinvestment_evidence_payload is not None:
        if event.event_type != "cash_distribution":
            raise ValueError(
                "Reinvestment evidence is only valid for a cash distribution."
            )
        evidence = _evidence_model(
            instrument_id=instrument_id,
            evidence_id=evidence_id,
            fund_nav_event_id=event_id,
            revision_number=1,
            revision_kind="original",
            supersedes_evidence_id=None,
            snapshot=_snapshot_payload(
                reinvestment_evidence_payload,
                field_name="reinvestment_evidence",
                allowed_fields=_EVIDENCE_SNAPSHOT_FIELDS,
            ),
            recorded_by=recorded_by,
            revision_reason=revision_reason,
        )
        normalized_evidence_model = evidence.model_dump(mode="json")
        normalized_evidence = {
            field: deepcopy(normalized_evidence_model.get(field))
            for field in sorted(_EVIDENCE_SNAPSHOT_FIELDS)
        }
    request: dict[str, object] = {
        "action": normalized_action,
        "reinvestment_evidence": normalized_evidence,
        "client_mutation_id": client_mutation_id,
        "recorded_by": recorded_by,
        "revision_reason": revision_reason,
    }
    return request, _json_fingerprint(request)


def confirm_fund_nav_action_candidate(
    *,
    instrument_id: str,
    candidate_id: str,
    action_payload: Mapping[str, object],
    reinvestment_evidence_payload: Mapping[str, object] | None,
    client_mutation_id: str,
    recorded_by: str,
    revision_reason: str,
) -> dict[str, object] | None:
    mutation_id, actor, reason = _audit_fields(
        client_mutation_id=client_mutation_id,
        recorded_by=recorded_by,
        revision_reason=revision_reason,
    )
    repository = FundNavActionCandidateRepository(get_session_factory())
    if get_instrument(instrument_id) is None:
        return None
    _candidate_for_instrument(
        repository,
        instrument_id=instrument_id,
        candidate_id=candidate_id,
    )
    # Bind the Registry action identity to the durable candidate, not to a UI
    # retry token. This prevents a partial Platform decision write from ever
    # creating a second action when the confirm request is replayed.
    action_id = _deterministic_id(
        "fund-nav-action", instrument_id, "candidate", candidate_id
    )
    event_id = _deterministic_id(
        "fund-nav-event", instrument_id, action_id, 1, candidate_id
    )
    evidence_id = _deterministic_id(
        "fund-nav-reinvestment-evidence",
        instrument_id,
        event_id,
        1,
        candidate_id,
    )
    confirmation_request, request_fingerprint = _candidate_confirmation_intent(
        instrument_id=instrument_id,
        action_id=action_id,
        event_id=event_id,
        evidence_id=evidence_id,
        action_payload=action_payload,
        reinvestment_evidence_payload=reinvestment_evidence_payload,
        client_mutation_id=mutation_id,
        recorded_by=actor,
        revision_reason=reason,
    )
    reservation: dict[str, dict[str, object]] = {}

    def reserve_confirmation() -> None:
        reservation["candidate"] = repository.reserve_confirmation(
            fund_nav_action_candidate_id=candidate_id,
            confirmed_fund_nav_event_id=event_id,
            decision_by=actor,
            client_mutation_id=mutation_id,
            request_fingerprint=request_fingerprint,
            confirmation_request=confirmation_request,
        )

    result = _publish_admin_mutation(
        instrument_id=instrument_id,
        recorded_by=actor,
        source_provider="confirmed_fund_nav_action_candidate",
        message=(
            f"Confirmed fund NAV action candidate {candidate_id} and rebuilt its NAV projection."
        ),
        before_registry_publish=reserve_confirmation,
        build_plan=lambda instrument: _original_action_plan(
            instrument=instrument,
            instrument_id=instrument_id,
            action_id=action_id,
            event_id=event_id,
            action_payload=action_payload,
            evidence_id=evidence_id,
            evidence_payload=reinvestment_evidence_payload,
            recorded_by=actor,
            revision_reason=reason,
        ),
        before_candidate_projection=lambda _result: {
            "candidate": repository.resolve(
                fund_nav_action_candidate_id=candidate_id,
                confirmed_fund_nav_event_id=event_id,
                decision_by=actor,
                client_mutation_id=mutation_id,
                request_fingerprint=request_fingerprint,
            )
        },
    )
    if result is None:
        return None
    result["client_mutation_id"] = mutation_id
    if not isinstance(result.get("candidate"), Mapping):
        result["candidate"] = reservation.get("candidate")
    candidate_result = result.get("candidate")
    if isinstance(candidate_result, Mapping):
        result["candidate_confirmation_status"] = str(
            candidate_result.get("status") or "confirming"
        )
    return result


def resume_fund_nav_action_candidate_confirmation(
    *,
    instrument_id: str,
    candidate_id: str,
) -> dict[str, object] | None:
    if get_instrument(instrument_id) is None:
        return None
    repository = FundNavActionCandidateRepository(get_session_factory())
    candidate = _candidate_for_instrument(
        repository,
        instrument_id=instrument_id,
        candidate_id=candidate_id,
    )
    if candidate.get("status") not in {"confirming", "resolved"}:
        raise FundNavActionCandidateConflictError(
            f'Fund NAV action candidate "{candidate_id}" has no confirmation to resume.'
        )
    request = repository.get_confirmation_request(
        fund_nav_action_candidate_id=candidate_id,
    )
    action_payload = request.get("action")
    evidence_payload = request.get("reinvestment_evidence")
    if not isinstance(action_payload, Mapping):
        raise FundNavActionCandidateConflictError(
            "The reserved candidate confirmation has no valid action snapshot."
        )
    if evidence_payload is not None and not isinstance(evidence_payload, Mapping):
        raise FundNavActionCandidateConflictError(
            "The reserved candidate confirmation has invalid evidence."
        )
    return confirm_fund_nav_action_candidate(
        instrument_id=instrument_id,
        candidate_id=candidate_id,
        action_payload=action_payload,
        reinvestment_evidence_payload=evidence_payload,
        client_mutation_id=_required_text(
            request.get("client_mutation_id"),
            field_name="client_mutation_id",
            max_length=_MAX_CLIENT_MUTATION_ID_LENGTH,
        ),
        recorded_by=_required_text(
            request.get("recorded_by"),
            field_name="recorded_by",
        ),
        revision_reason=_required_text(
            request.get("revision_reason"),
            field_name="revision_reason",
        ),
    )
