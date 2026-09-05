from __future__ import annotations
from datetime import date
from typing import Never
from studio_data.contracts import (
    StudioBulkRefreshRequest,
    StudioBulkRefreshResponse,
    StudioFundNavActionCandidate,
    StudioFundNavActionCandidateRejectRequest,
    StudioFundNavActionCreateRequest,
    StudioFundNavActionRevisionRequest,
    StudioFundNavMutationResponse,
    StudioFundNavReinvestmentEvidenceCreateRequest,
    StudioFundNavReinvestmentEvidenceRevisionRequest,
    StudioInstrumentCreateRequest,
    StudioInstrumentDetail,
    StudioLifecycleTransitionRequest,
    StudioInstrumentRecord,
    StudioInstrumentsResponse,
    StudioMarketDataUpsertRequest,
    StudioNavImportFileRequest,
    StudioNavImportPreviewRequest,
    StudioNavImportPreviewResponse,
    StudioNavImportRequest,
    StudioQuoteSelectionPolicyUpdateRequest,
    StudioRefreshTriggerRequest,
    StudioSourceSettingsUpdateRequest,
)
from studio_data.services.fund_nav_action_candidates import (
    FundNavActionCandidateConflictError,
    FundNavActionCandidateNotFoundError,
)
from studio_data.services.fund_nav_actions import (
    FundNavAdminConflictError,
    confirm_fund_nav_action_candidate,
    create_fund_nav_action,
    create_fund_nav_reinvestment_evidence,
    list_fund_nav_action_candidates,
    reject_fund_nav_action_candidate,
    resume_fund_nav_action_candidate_confirmation,
    revise_fund_nav_action,
    revise_fund_nav_reinvestment_evidence,
)
from studio_data.services.market_data_ops import (
    import_nav_file,
    import_nav_text,
    preview_nav_import,
    refresh_market_data,
    refresh_market_data_batch,
)
from studio_data.services.instrument_store import (
    StaleFundNavPublicationError,
    archive_instrument,
    create_instrument,
    find_instrument_by_broker_identifier,
    find_instrument_by_identifier,
    get_instrument,
    instrument_registry_name,
    list_instruments,
    restore_instrument,
    upsert_market_data,
    upsert_quote_selection_policy,
    upsert_source_settings,
)
from studio_data.services.downstream_notifications import (
    notify_market_data_downstream_refresh,
)


def list_instrument_records(
    search: str | None = None,
    instrument_type: str | None = None,
    limit: int | None = None,
    include_inactive: bool = False,
) -> StudioInstrumentsResponse:
    if limit is not None and not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if search is not None and not search.strip():
        raise ValueError("search must not be empty")
    return StudioInstrumentsResponse(
        registry_name=instrument_registry_name(),
        instruments=list_instruments(
            search=search,
            instrument_type=instrument_type,
            limit=limit,
            include_inactive=include_inactive,
        ),
    )


def resolve_instrument_record(
    identifier_value: str,
    identifier_type: str | None = None,
    include_inactive: bool = False,
) -> StudioInstrumentDetail:
    record = find_instrument_by_identifier(
        identifier_value=identifier_value,
        identifier_type=identifier_type,
        include_inactive=include_inactive,
    )
    if record is None:
        raise ValueError("Instrument not found")
    return StudioInstrumentDetail.model_validate(record)


def resolve_instrument_by_broker_identity(
    broker: str,
    identifier_type: str,
    identifier_value: str,
    include_inactive: bool = False,
) -> StudioInstrumentDetail:
    record = find_instrument_by_broker_identifier(
        broker=broker,
        identifier_type=identifier_type,
        identifier_value=identifier_value,
        include_inactive=include_inactive,
    )
    if record is None:
        raise ValueError("Instrument not found")
    return StudioInstrumentDetail.model_validate(record)


def refresh_instrument_market_data_batch(
    payload: StudioBulkRefreshRequest,
) -> StudioBulkRefreshResponse:
    response = refresh_market_data_batch(
        source=payload.source,
        updated_by=payload.updated_by,
        full_history=payload.full_history,
        include_inactive=payload.include_inactive,
    )
    refreshed_ids = [
        item["instrument_id"]
        for item in response["results"]
        if item.get("status") in {"imported", "refreshed"}
    ]
    if refreshed_ids:
        notify_market_data_downstream_refresh(
            instrument_ids=refreshed_ids, raise_on_error=True
        )
    return StudioBulkRefreshResponse.model_validate(response)


def create_instrument_record(
    payload: StudioInstrumentCreateRequest,
) -> StudioInstrumentRecord:
    if payload.instrument_type in {"equity", "etf"}:
        raise ValueError(
            "Use investment-studio data securities search/add to register stocks and ETFs from FMP."
        )
    record = create_instrument(
        instrument_name=payload.instrument_name,
        instrument_type=payload.instrument_type,
        currency=payload.currency,
        identifiers=[item.model_dump() for item in payload.identifiers],
        quote_selection_policy=payload.quote_selection_policy.model_dump()
        if payload.quote_selection_policy is not None
        else None,
        broker_identifiers=[
            item.model_dump(mode="json") for item in payload.broker_identifiers
        ],
    )
    return StudioInstrumentRecord.model_validate(record)


def update_instrument_quote_selection_policy(
    instrument_id: str, payload: StudioQuoteSelectionPolicyUpdateRequest
) -> StudioInstrumentRecord:
    record = upsert_quote_selection_policy(
        instrument_id=instrument_id,
        quote_selection_policy=payload.quote_selection_policy.model_dump(),
    )
    if record is None:
        raise ValueError("Instrument not found")
    notify_market_data_downstream_refresh(
        instrument_ids=[instrument_id],
        refresh_all_portfolios=instrument_id.startswith("fx-"),
        raise_on_error=True,
    )
    return StudioInstrumentRecord.model_validate(record)


def get_instrument_record(instrument_id: str) -> StudioInstrumentDetail:
    record = get_instrument(instrument_id)
    if record is None:
        raise ValueError("Instrument not found")
    return StudioInstrumentDetail.model_validate(record)


def list_instrument_nav_action_candidates(
    instrument_id: str, include_history: bool = False
) -> list[StudioFundNavActionCandidate]:
    records = list_fund_nav_action_candidates(
        instrument_id=instrument_id, include_history=include_history
    )
    if records is None:
        raise ValueError("Instrument not found")
    return [StudioFundNavActionCandidate.model_validate(item) for item in records]


def reject_instrument_nav_action_candidate(
    instrument_id: str,
    candidate_id: str,
    payload: StudioFundNavActionCandidateRejectRequest,
) -> StudioFundNavActionCandidate:
    try:
        record = reject_fund_nav_action_candidate(
            instrument_id=instrument_id,
            candidate_id=candidate_id,
            reason=payload.reason,
            decision_by=payload.decision_by,
        )
    except FundNavActionCandidateNotFoundError as error:
        raise ValueError("NAV action candidate not found") from error
    except (FundNavActionCandidateConflictError, ValueError) as error:
        raise ValueError(str(error)) from error
    if record is None:
        raise ValueError("Instrument not found")
    return StudioFundNavActionCandidate.model_validate(record)


def _fund_nav_mutation_response(
    *, instrument_id: str, result: dict[str, object] | None
) -> StudioFundNavMutationResponse:
    if result is None:
        raise ValueError("Instrument not found")
    if bool(result.get("changed")):
        raw_dirty_from = result.get("dirty_from")
        dirty_from = date.fromisoformat(str(raw_dirty_from)) if raw_dirty_from else None
        notify_market_data_downstream_refresh(
            instrument_ids=[instrument_id], dirty_from=dirty_from, raise_on_error=True
        )
    return StudioFundNavMutationResponse.model_validate(result)


def _raise_fund_nav_mutation_error(error: Exception) -> Never:
    if isinstance(error, FundNavActionCandidateNotFoundError):
        raise ValueError("NAV action candidate not found") from error
    raise ValueError(str(error)) from error


def create_instrument_fund_nav_action(
    instrument_id: str, payload: StudioFundNavActionCreateRequest
) -> StudioFundNavMutationResponse:
    try:
        result = create_fund_nav_action(
            instrument_id=instrument_id,
            action_payload=payload.action.model_dump(mode="json", exclude_none=True),
            reinvestment_evidence_payload=payload.reinvestment_evidence.model_dump(
                mode="json", exclude_none=True
            )
            if payload.reinvestment_evidence is not None
            else None,
            client_mutation_id=payload.client_mutation_id,
            recorded_by=payload.recorded_by,
            revision_reason=payload.revision_reason,
        )
    except (
        FundNavAdminConflictError,
        StaleFundNavPublicationError,
        ValueError,
    ) as error:
        _raise_fund_nav_mutation_error(error)
    return _fund_nav_mutation_response(instrument_id=instrument_id, result=result)


def revise_instrument_fund_nav_action(
    instrument_id: str, action_id: str, payload: StudioFundNavActionRevisionRequest
) -> StudioFundNavMutationResponse:
    try:
        result = revise_fund_nav_action(
            instrument_id=instrument_id,
            action_id=action_id,
            predecessor_fund_nav_event_id=payload.predecessor_fund_nav_event_id,
            revision_kind=payload.revision_kind,
            action_payload=payload.action.model_dump(mode="json", exclude_none=True)
            if payload.action is not None
            else None,
            reinvestment_evidence_payload=payload.reinvestment_evidence.model_dump(
                mode="json", exclude_none=True
            )
            if payload.reinvestment_evidence is not None
            else None,
            client_mutation_id=payload.client_mutation_id,
            recorded_by=payload.recorded_by,
            revision_reason=payload.revision_reason,
        )
    except (
        FundNavAdminConflictError,
        StaleFundNavPublicationError,
        ValueError,
    ) as error:
        _raise_fund_nav_mutation_error(error)
    return _fund_nav_mutation_response(instrument_id=instrument_id, result=result)


def create_instrument_fund_nav_reinvestment_evidence(
    instrument_id: str,
    fund_nav_event_id: str,
    payload: StudioFundNavReinvestmentEvidenceCreateRequest,
) -> StudioFundNavMutationResponse:
    try:
        result = create_fund_nav_reinvestment_evidence(
            instrument_id=instrument_id,
            fund_nav_event_id=fund_nav_event_id,
            evidence_payload=payload.evidence.model_dump(
                mode="json", exclude_none=True
            ),
            client_mutation_id=payload.client_mutation_id,
            recorded_by=payload.recorded_by,
            revision_reason=payload.revision_reason,
        )
    except (
        FundNavAdminConflictError,
        StaleFundNavPublicationError,
        ValueError,
    ) as error:
        _raise_fund_nav_mutation_error(error)
    return _fund_nav_mutation_response(instrument_id=instrument_id, result=result)


def revise_instrument_fund_nav_reinvestment_evidence(
    instrument_id: str,
    evidence_id: str,
    payload: StudioFundNavReinvestmentEvidenceRevisionRequest,
) -> StudioFundNavMutationResponse:
    if evidence_id != payload.predecessor_fund_nav_reinvestment_evidence_id:
        raise ValueError("Evidence path id must match the optimistic predecessor id.")
    try:
        result = revise_fund_nav_reinvestment_evidence(
            instrument_id=instrument_id,
            predecessor_fund_nav_reinvestment_evidence_id=payload.predecessor_fund_nav_reinvestment_evidence_id,
            revision_kind=payload.revision_kind,
            evidence_payload=payload.evidence.model_dump(mode="json", exclude_none=True)
            if payload.evidence is not None
            else None,
            client_mutation_id=payload.client_mutation_id,
            recorded_by=payload.recorded_by,
            revision_reason=payload.revision_reason,
        )
    except (
        FundNavAdminConflictError,
        StaleFundNavPublicationError,
        ValueError,
    ) as error:
        _raise_fund_nav_mutation_error(error)
    return _fund_nav_mutation_response(instrument_id=instrument_id, result=result)


def confirm_instrument_nav_action_candidate(
    instrument_id: str, candidate_id: str, payload: StudioFundNavActionCreateRequest
) -> StudioFundNavMutationResponse:
    try:
        result = confirm_fund_nav_action_candidate(
            instrument_id=instrument_id,
            candidate_id=candidate_id,
            action_payload=payload.action.model_dump(mode="json", exclude_none=True),
            reinvestment_evidence_payload=payload.reinvestment_evidence.model_dump(
                mode="json", exclude_none=True
            )
            if payload.reinvestment_evidence is not None
            else None,
            client_mutation_id=payload.client_mutation_id,
            recorded_by=payload.recorded_by,
            revision_reason=payload.revision_reason,
        )
    except (
        FundNavActionCandidateNotFoundError,
        FundNavActionCandidateConflictError,
        FundNavAdminConflictError,
        StaleFundNavPublicationError,
        ValueError,
    ) as error:
        _raise_fund_nav_mutation_error(error)
    return _fund_nav_mutation_response(instrument_id=instrument_id, result=result)


def resume_instrument_nav_action_candidate_confirmation(
    instrument_id: str, candidate_id: str
) -> StudioFundNavMutationResponse:
    try:
        result = resume_fund_nav_action_candidate_confirmation(
            instrument_id=instrument_id, candidate_id=candidate_id
        )
    except (
        FundNavActionCandidateNotFoundError,
        FundNavActionCandidateConflictError,
        FundNavAdminConflictError,
        StaleFundNavPublicationError,
        ValueError,
    ) as error:
        _raise_fund_nav_mutation_error(error)
    return _fund_nav_mutation_response(instrument_id=instrument_id, result=result)


def upsert_instrument_market_data(
    instrument_id: str, payload: StudioMarketDataUpsertRequest
) -> StudioInstrumentRecord:
    record = upsert_market_data(
        instrument_id=instrument_id,
        metric_family=payload.metric_family,
        quote_basis=payload.quote_basis,
        as_of_date=payload.as_of_date,
        value=str(payload.value),
        currency=payload.currency,
        provider=payload.provider,
        status=payload.status,
        nav_lineage=payload.nav_lineage.model_dump(mode="json")
        if payload.nav_lineage is not None
        else None,
    )
    if record is None:
        raise ValueError("Instrument not found")
    notify_market_data_downstream_refresh(
        instrument_ids=[instrument_id],
        dirty_from=payload.as_of_date,
        refresh_all_portfolios=payload.metric_family.strip().lower() == "fx"
        or instrument_id.startswith("fx-"),
        raise_on_error=True,
    )
    return StudioInstrumentRecord.model_validate(record)


def update_instrument_source_settings(
    instrument_id: str, payload: StudioSourceSettingsUpdateRequest
) -> StudioInstrumentRecord:
    optional_semantics: dict[str, object] = {}
    if payload.expected_frequency is not None:
        optional_semantics["expected_frequency"] = payload.expected_frequency
    if "market_calendar" in payload.model_fields_set:
        optional_semantics["market_calendar"] = payload.market_calendar
    if payload.release_lag_days is not None:
        optional_semantics["release_lag_days"] = payload.release_lag_days
    if payload.return_semantics is not None:
        optional_semantics["return_semantics"] = payload.return_semantics
    record = upsert_source_settings(
        instrument_id=instrument_id,
        source_mode=payload.source_mode,
        source_email=payload.source_email,
        source_location=payload.source_location,
        source_api_profile=payload.source_api_profile,
        source_email_rules=[
            item.model_dump() for item in payload.source_email_rules
        ]
        if payload.source_email_rules is not None
        else None,
        **optional_semantics,
    )
    if record is None:
        raise ValueError("Instrument not found")
    notify_market_data_downstream_refresh(
        instrument_ids=[instrument_id],
        refresh_all_portfolios=instrument_id.startswith("fx-"),
        raise_on_error=True,
    )
    return StudioInstrumentRecord.model_validate(record)


def refresh_instrument_market_data(
    instrument_id: str, payload: StudioRefreshTriggerRequest
) -> StudioInstrumentRecord:
    record = refresh_market_data(
        instrument_id=instrument_id,
        updated_by=payload.updated_by,
        full_history=payload.full_history,
        source=payload.source,
    )
    if record is None:
        raise ValueError("Instrument not found")
    notify_market_data_downstream_refresh(
        instrument_ids=[instrument_id],
        refresh_all_portfolios=instrument_id.startswith("fx-"),
        raise_on_error=True,
    )
    return StudioInstrumentRecord.model_validate(record)


def archive_instrument_record(
    instrument_id: str, payload: StudioLifecycleTransitionRequest
) -> StudioInstrumentRecord:
    record = archive_instrument(
        instrument_id=instrument_id, updated_by=payload.updated_by
    )
    if record is None:
        raise ValueError("Instrument not found")
    return StudioInstrumentRecord.model_validate(record)


def restore_instrument_record(
    instrument_id: str, payload: StudioLifecycleTransitionRequest
) -> StudioInstrumentRecord:
    record = restore_instrument(
        instrument_id=instrument_id, updated_by=payload.updated_by
    )
    if record is None:
        raise ValueError("Instrument not found")
    return StudioInstrumentRecord.model_validate(record)


def import_instrument_nav_history(
    instrument_id: str, payload: StudioNavImportRequest
) -> StudioInstrumentRecord:
    record = import_nav_text(
        instrument_id=instrument_id,
        raw_text=payload.raw_text,
        provider=payload.provider,
        status=payload.status,
        updated_by=payload.updated_by,
    )
    if record is None:
        raise ValueError("Instrument not found")
    notify_market_data_downstream_refresh(
        instrument_ids=[instrument_id], raise_on_error=True
    )
    return StudioInstrumentRecord.model_validate(record)


def preview_instrument_nav_history(
    instrument_id: str, payload: StudioNavImportPreviewRequest
) -> StudioNavImportPreviewResponse:
    rows = preview_nav_import(
        instrument_id=instrument_id,
        raw_text=payload.raw_text,
        file_name=payload.file_name,
        file_bytes=payload.decoded_bytes(),
    )
    if rows is None:
        raise ValueError("Instrument not found")
    return StudioNavImportPreviewResponse(row_count=len(rows), rows=rows)


def import_instrument_nav_history_file(
    instrument_id: str, payload: StudioNavImportFileRequest
) -> StudioInstrumentRecord:
    record = import_nav_file(
        instrument_id=instrument_id,
        file_name=payload.file_name,
        file_bytes=payload.decoded_bytes(),
        provider=payload.provider,
        status=payload.status,
        updated_by=payload.updated_by,
    )
    if record is None:
        raise ValueError("Instrument not found")
    notify_market_data_downstream_refresh(
        instrument_ids=[instrument_id], raise_on_error=True
    )
    return StudioInstrumentRecord.model_validate(record)
