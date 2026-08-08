from __future__ import annotations

from datetime import date
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from typing import Annotated, Never

from platform_app.api.contracts import (
    PlatformBulkRefreshRequest,
    PlatformBulkRefreshResponse,
    PlatformFundNavActionCandidate,
    PlatformFundNavActionCandidateRejectRequest,
    PlatformFundNavActionCreateRequest,
    PlatformFundNavActionRevisionRequest,
    PlatformFundNavMutationResponse,
    PlatformFundNavReinvestmentEvidenceCreateRequest,
    PlatformFundNavReinvestmentEvidenceRevisionRequest,
    PlatformDerivativeContractMetadataUpdateRequest,
    PlatformInstrumentCreateRequest,
    PlatformInstrumentDetail,
    PlatformLifecycleTransitionRequest,
    PlatformInstrumentRecord,
    PlatformInstrumentsResponse,
    PlatformMarketDataUpsertRequest,
    PlatformNavImportFileRequest,
    PlatformNavImportPreviewRequest,
    PlatformNavImportPreviewResponse,
    PlatformNavImportRequest,
    PlatformQuoteSelectionPolicyUpdateRequest,
    PlatformRefreshTriggerRequest,
    PlatformSourceSettingsUpdateRequest,
)
from platform_app.services.fund_nav_action_candidates import (
    FundNavActionCandidateConflictError,
    FundNavActionCandidateNotFoundError,
)
from platform_app.services.fund_nav_actions import (
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
from platform_app.services.market_data_ops import (
    import_nav_file,
    import_nav_text,
    preview_nav_import,
    refresh_market_data,
    refresh_market_data_batch,
)
from platform_app.services.instrument_store import (
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
    upsert_derivative_contract_metadata,
    upsert_quote_selection_policy,
    upsert_source_settings,
)
from platform_app.services.downstream_notifications import queue_market_data_downstream_refresh


router = APIRouter()


@router.get("", response_model=PlatformInstrumentsResponse)
def list_instrument_records(
    search: Annotated[str | None, Query(min_length=1)] = None,
    instrument_type: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    include_inactive: bool = False,
) -> PlatformInstrumentsResponse:
    return PlatformInstrumentsResponse(
        registry_name=instrument_registry_name(),
        instruments=list_instruments(
            search=search,
            instrument_type=instrument_type,
            limit=limit,
            include_inactive=include_inactive,
        ),
    )


@router.get("/resolve", response_model=PlatformInstrumentDetail)
def resolve_instrument_record(
    identifier_value: Annotated[str, Query(min_length=1)],
    identifier_type: str | None = None,
    include_inactive: bool = False,
) -> PlatformInstrumentDetail:
    record = find_instrument_by_identifier(
        identifier_value=identifier_value,
        identifier_type=identifier_type,
        include_inactive=include_inactive,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentDetail.model_validate(record)


@router.get("/resolve-broker", response_model=PlatformInstrumentDetail)
def resolve_instrument_by_broker_identity(
    broker: Annotated[str, Query(min_length=1)],
    identifier_type: Annotated[str, Query(min_length=1)],
    identifier_value: Annotated[str, Query(min_length=1)],
    include_inactive: bool = False,
) -> PlatformInstrumentDetail:
    record = find_instrument_by_broker_identifier(
        broker=broker,
        identifier_type=identifier_type,
        identifier_value=identifier_value,
        include_inactive=include_inactive,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentDetail.model_validate(record)


@router.post("/refresh", response_model=PlatformBulkRefreshResponse)
def refresh_instrument_market_data_batch(
    payload: PlatformBulkRefreshRequest,
    background_tasks: BackgroundTasks,
) -> PlatformBulkRefreshResponse:
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
        queue_market_data_downstream_refresh(
            background_tasks,
            instrument_ids=refreshed_ids,
        )
    return PlatformBulkRefreshResponse.model_validate(response)


@router.post("", response_model=PlatformInstrumentRecord)
def create_instrument_record(
    payload: PlatformInstrumentCreateRequest,
) -> PlatformInstrumentRecord:
    try:
        record = create_instrument(
            instrument_name=payload.instrument_name,
            instrument_type=payload.instrument_type,
            currency=payload.currency,
            identifiers=[item.model_dump() for item in payload.identifiers],
            quote_selection_policy=(
                payload.quote_selection_policy.model_dump()
                if payload.quote_selection_policy is not None
                else None
            ),
            option_contract=(
                payload.option_contract.model_dump(mode="json")
                if payload.option_contract is not None
                else None
            ),
            fcn_contract=(
                payload.fcn_contract.model_dump(mode="json")
                if payload.fcn_contract is not None
                else None
            ),
            broker_identifiers=[
                item.model_dump(mode="json") for item in payload.broker_identifiers
            ],
            corporate_action_adjustment_policy=(
                payload.corporate_action_adjustment_policy.model_dump(mode="json")
                if payload.corporate_action_adjustment_policy is not None
                else None
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return PlatformInstrumentRecord.model_validate(record)


@router.put(
    "/{instrument_id}/derivative-contract-metadata",
    response_model=PlatformInstrumentDetail,
)
def update_derivative_contract_metadata(
    instrument_id: str,
    payload: PlatformDerivativeContractMetadataUpdateRequest,
) -> PlatformInstrumentDetail:
    try:
        record = upsert_derivative_contract_metadata(
            instrument_id=instrument_id,
            fcn_contract=(
                payload.fcn_contract.model_dump(mode="json")
                if payload.fcn_contract is not None
                else None
            ),
            broker_identifiers=[
                item.model_dump(mode="json") for item in payload.broker_identifiers
            ],
            corporate_action_adjustment_policy=(
                payload.corporate_action_adjustment_policy.model_dump(mode="json")
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentDetail.model_validate(record)


@router.put(
    "/{instrument_id}/quote-selection-policy",
    response_model=PlatformInstrumentRecord,
)
def update_instrument_quote_selection_policy(
    instrument_id: str,
    payload: PlatformQuoteSelectionPolicyUpdateRequest,
) -> PlatformInstrumentRecord:
    try:
        record = upsert_quote_selection_policy(
            instrument_id=instrument_id,
            quote_selection_policy=payload.quote_selection_policy.model_dump(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentRecord.model_validate(record)


@router.get("/{instrument_id}", response_model=PlatformInstrumentDetail)
def get_instrument_record(instrument_id: str) -> PlatformInstrumentDetail:
    record = get_instrument(instrument_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentDetail.model_validate(record)


@router.get(
    "/{instrument_id}/nav-action-candidates",
    response_model=list[PlatformFundNavActionCandidate],
)
def list_instrument_nav_action_candidates(
    instrument_id: str,
    include_history: bool = False,
) -> list[PlatformFundNavActionCandidate]:
    records = list_fund_nav_action_candidates(
        instrument_id=instrument_id,
        include_history=include_history,
    )
    if records is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return [PlatformFundNavActionCandidate.model_validate(item) for item in records]


@router.post(
    "/{instrument_id}/nav-action-candidates/{candidate_id}/reject",
    response_model=PlatformFundNavActionCandidate,
)
def reject_instrument_nav_action_candidate(
    instrument_id: str,
    candidate_id: str,
    payload: PlatformFundNavActionCandidateRejectRequest,
) -> PlatformFundNavActionCandidate:
    try:
        record = reject_fund_nav_action_candidate(
            instrument_id=instrument_id,
            candidate_id=candidate_id,
            reason=payload.reason,
            decision_by=payload.decision_by,
        )
    except FundNavActionCandidateNotFoundError as error:
        raise HTTPException(status_code=404, detail="NAV action candidate not found") from error
    except (FundNavActionCandidateConflictError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformFundNavActionCandidate.model_validate(record)


def _fund_nav_mutation_response(
    *,
    instrument_id: str,
    result: dict[str, object] | None,
    background_tasks: BackgroundTasks,
) -> PlatformFundNavMutationResponse:
    if result is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    if bool(result.get("changed")):
        raw_dirty_from = result.get("dirty_from")
        dirty_from = (
            date.fromisoformat(str(raw_dirty_from))
            if raw_dirty_from
            else None
        )
        queue_market_data_downstream_refresh(
            background_tasks,
            instrument_ids=[instrument_id],
            dirty_from=dirty_from,
        )
    return PlatformFundNavMutationResponse.model_validate(result)


def _raise_fund_nav_mutation_error(error: Exception) -> Never:
    if isinstance(error, FundNavActionCandidateNotFoundError):
        raise HTTPException(
            status_code=404,
            detail="NAV action candidate not found",
        ) from error
    if isinstance(
        error,
        (
            FundNavActionCandidateConflictError,
            FundNavAdminConflictError,
            StaleFundNavPublicationError,
        ),
    ):
        raise HTTPException(status_code=409, detail=str(error)) from error
    raise HTTPException(status_code=400, detail=str(error)) from error


@router.post(
    "/{instrument_id}/fund-nav-actions",
    response_model=PlatformFundNavMutationResponse,
)
def create_instrument_fund_nav_action(
    instrument_id: str,
    payload: PlatformFundNavActionCreateRequest,
    background_tasks: BackgroundTasks,
) -> PlatformFundNavMutationResponse:
    try:
        result = create_fund_nav_action(
            instrument_id=instrument_id,
            action_payload=payload.action.model_dump(mode="json", exclude_none=True),
            reinvestment_evidence_payload=(
                payload.reinvestment_evidence.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                if payload.reinvestment_evidence is not None
                else None
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
    return _fund_nav_mutation_response(
        instrument_id=instrument_id,
        result=result,
        background_tasks=background_tasks,
    )


@router.post(
    "/{instrument_id}/fund-nav-actions/{action_id}/revisions",
    response_model=PlatformFundNavMutationResponse,
)
def revise_instrument_fund_nav_action(
    instrument_id: str,
    action_id: str,
    payload: PlatformFundNavActionRevisionRequest,
    background_tasks: BackgroundTasks,
) -> PlatformFundNavMutationResponse:
    try:
        result = revise_fund_nav_action(
            instrument_id=instrument_id,
            action_id=action_id,
            predecessor_fund_nav_event_id=payload.predecessor_fund_nav_event_id,
            revision_kind=payload.revision_kind,
            action_payload=(
                payload.action.model_dump(mode="json", exclude_none=True)
                if payload.action is not None
                else None
            ),
            reinvestment_evidence_payload=(
                payload.reinvestment_evidence.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                if payload.reinvestment_evidence is not None
                else None
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
    return _fund_nav_mutation_response(
        instrument_id=instrument_id,
        result=result,
        background_tasks=background_tasks,
    )


@router.post(
    "/{instrument_id}/fund-nav-events/{fund_nav_event_id}/reinvestment-evidence",
    response_model=PlatformFundNavMutationResponse,
)
def create_instrument_fund_nav_reinvestment_evidence(
    instrument_id: str,
    fund_nav_event_id: str,
    payload: PlatformFundNavReinvestmentEvidenceCreateRequest,
    background_tasks: BackgroundTasks,
) -> PlatformFundNavMutationResponse:
    try:
        result = create_fund_nav_reinvestment_evidence(
            instrument_id=instrument_id,
            fund_nav_event_id=fund_nav_event_id,
            evidence_payload=payload.evidence.model_dump(
                mode="json",
                exclude_none=True,
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
    return _fund_nav_mutation_response(
        instrument_id=instrument_id,
        result=result,
        background_tasks=background_tasks,
    )


@router.post(
    "/{instrument_id}/fund-nav-reinvestment-evidence/{evidence_id}/revisions",
    response_model=PlatformFundNavMutationResponse,
)
def revise_instrument_fund_nav_reinvestment_evidence(
    instrument_id: str,
    evidence_id: str,
    payload: PlatformFundNavReinvestmentEvidenceRevisionRequest,
    background_tasks: BackgroundTasks,
) -> PlatformFundNavMutationResponse:
    if evidence_id != payload.predecessor_fund_nav_reinvestment_evidence_id:
        raise HTTPException(
            status_code=409,
            detail="Evidence path id must match the optimistic predecessor id.",
        )
    try:
        result = revise_fund_nav_reinvestment_evidence(
            instrument_id=instrument_id,
            predecessor_fund_nav_reinvestment_evidence_id=(
                payload.predecessor_fund_nav_reinvestment_evidence_id
            ),
            revision_kind=payload.revision_kind,
            evidence_payload=(
                payload.evidence.model_dump(mode="json", exclude_none=True)
                if payload.evidence is not None
                else None
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
    return _fund_nav_mutation_response(
        instrument_id=instrument_id,
        result=result,
        background_tasks=background_tasks,
    )


@router.post(
    "/{instrument_id}/nav-action-candidates/{candidate_id}/confirm",
    response_model=PlatformFundNavMutationResponse,
)
def confirm_instrument_nav_action_candidate(
    instrument_id: str,
    candidate_id: str,
    payload: PlatformFundNavActionCreateRequest,
    background_tasks: BackgroundTasks,
) -> PlatformFundNavMutationResponse:
    try:
        result = confirm_fund_nav_action_candidate(
            instrument_id=instrument_id,
            candidate_id=candidate_id,
            action_payload=payload.action.model_dump(mode="json", exclude_none=True),
            reinvestment_evidence_payload=(
                payload.reinvestment_evidence.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                if payload.reinvestment_evidence is not None
                else None
            ),
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
    return _fund_nav_mutation_response(
        instrument_id=instrument_id,
        result=result,
        background_tasks=background_tasks,
    )


@router.post(
    "/{instrument_id}/nav-action-candidates/{candidate_id}/resume-confirmation",
    response_model=PlatformFundNavMutationResponse,
)
def resume_instrument_nav_action_candidate_confirmation(
    instrument_id: str,
    candidate_id: str,
    background_tasks: BackgroundTasks,
) -> PlatformFundNavMutationResponse:
    try:
        result = resume_fund_nav_action_candidate_confirmation(
            instrument_id=instrument_id,
            candidate_id=candidate_id,
        )
    except (
        FundNavActionCandidateNotFoundError,
        FundNavActionCandidateConflictError,
        FundNavAdminConflictError,
        StaleFundNavPublicationError,
        ValueError,
    ) as error:
        _raise_fund_nav_mutation_error(error)
    return _fund_nav_mutation_response(
        instrument_id=instrument_id,
        result=result,
        background_tasks=background_tasks,
    )


@router.post("/{instrument_id}/market-data", response_model=PlatformInstrumentRecord)
def upsert_instrument_market_data(
    instrument_id: str,
    payload: PlatformMarketDataUpsertRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    try:
        record = upsert_market_data(
            instrument_id=instrument_id,
            metric_family=payload.metric_family,
            quote_basis=payload.quote_basis,
            as_of_date=payload.as_of_date,
            value=str(payload.value),
            currency=payload.currency,
            provider=payload.provider,
            status=payload.status,
            nav_lineage=(
                payload.nav_lineage.model_dump(mode="json")
                if payload.nav_lineage is not None
                else None
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_market_data_downstream_refresh(
        background_tasks,
        instrument_ids=[instrument_id],
        dirty_from=payload.as_of_date,
        refresh_all_portfolios=payload.metric_family.strip().lower() == "fx" or instrument_id.startswith("fx-"),
    )
    return PlatformInstrumentRecord.model_validate(record)


@router.put("/{instrument_id}/source-settings", response_model=PlatformInstrumentRecord)
def update_instrument_source_settings(
    instrument_id: str,
    payload: PlatformSourceSettingsUpdateRequest,
) -> PlatformInstrumentRecord:
    optional_semantics: dict[str, object] = {}
    if payload.expected_frequency is not None:
        optional_semantics["expected_frequency"] = payload.expected_frequency
    if "market_calendar" in payload.model_fields_set:
        optional_semantics["market_calendar"] = payload.market_calendar
    if payload.release_lag_days is not None:
        optional_semantics["release_lag_days"] = payload.release_lag_days
    if payload.return_semantics is not None:
        optional_semantics["return_semantics"] = payload.return_semantics
    try:
        record = upsert_source_settings(
            instrument_id=instrument_id,
            source_mode=payload.source_mode,
            source_email=payload.source_email,
            source_location=payload.source_location,
            source_api_profile=payload.source_api_profile,
            source_email_rules=(
                [item.model_dump() for item in payload.source_email_rules]
                if payload.source_email_rules is not None
                else None
            ),
            **optional_semantics,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{instrument_id}/refresh", response_model=PlatformInstrumentRecord)
def refresh_instrument_market_data(
    instrument_id: str,
    payload: PlatformRefreshTriggerRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    record = refresh_market_data(
        instrument_id=instrument_id,
        updated_by=payload.updated_by,
        full_history=payload.full_history,
        source=payload.source,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_market_data_downstream_refresh(
        background_tasks,
        instrument_ids=[instrument_id],
        refresh_all_portfolios=instrument_id.startswith("fx-"),
    )
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{instrument_id}/archive", response_model=PlatformInstrumentRecord)
def archive_instrument_record(
    instrument_id: str,
    payload: PlatformLifecycleTransitionRequest,
) -> PlatformInstrumentRecord:
    record = archive_instrument(instrument_id=instrument_id, updated_by=payload.updated_by)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{instrument_id}/restore", response_model=PlatformInstrumentRecord)
def restore_instrument_record(
    instrument_id: str,
    payload: PlatformLifecycleTransitionRequest,
) -> PlatformInstrumentRecord:
    record = restore_instrument(instrument_id=instrument_id, updated_by=payload.updated_by)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{instrument_id}/nav-import", response_model=PlatformInstrumentRecord)
def import_instrument_nav_history(
    instrument_id: str,
    payload: PlatformNavImportRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    try:
        record = import_nav_text(
            instrument_id=instrument_id,
            raw_text=payload.raw_text,
            provider=payload.provider,
            status=payload.status,
            updated_by=payload.updated_by,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_market_data_downstream_refresh(background_tasks, instrument_ids=[instrument_id])
    return PlatformInstrumentRecord.model_validate(record)


@router.post(
    "/{instrument_id}/nav-import/preview",
    response_model=PlatformNavImportPreviewResponse,
)
def preview_instrument_nav_history(
    instrument_id: str,
    payload: PlatformNavImportPreviewRequest,
) -> PlatformNavImportPreviewResponse:
    try:
        rows = preview_nav_import(
            instrument_id=instrument_id,
            raw_text=payload.raw_text,
            file_name=payload.file_name,
            file_bytes=payload.decoded_bytes(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if rows is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformNavImportPreviewResponse(
        row_count=len(rows),
        rows=rows,
    )


@router.post("/{instrument_id}/nav-import/file", response_model=PlatformInstrumentRecord)
def import_instrument_nav_history_file(
    instrument_id: str,
    payload: PlatformNavImportFileRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    try:
        record = import_nav_file(
            instrument_id=instrument_id,
            file_name=payload.file_name,
            file_bytes=payload.decoded_bytes(),
            provider=payload.provider,
            status=payload.status,
            updated_by=payload.updated_by,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_market_data_downstream_refresh(background_tasks, instrument_ids=[instrument_id])
    return PlatformInstrumentRecord.model_validate(record)
