from __future__ import annotations

from studio_identity import current_principal, issue_delegation, revoke_delegation

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
)

from portfolio_app.api.contracts import (
    DerivativeContractRecord,
    TransactionCaptureAgentContextResponse,
    TransactionCaptureAnalysisCreateRequest,
    TransactionCaptureAnalysisResponse,
    TransactionCaptureAnalysisRevision,
    TransactionCaptureBatchCreateRequest,
    TransactionCaptureBatchListResponse,
    TransactionCaptureBatchRecord,
    TransactionCaptureListResponse,
    TransactionCaptureRecord,
)
from portfolio_app.api.routes.transactions import build_transaction_import_preview
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    list_accounts,
    list_derivative_contracts,
    list_transactions,
)
from portfolio_app.services.transaction_capture_agent import (
    TRANSACTION_CAPTURE_AGENT_INSTRUCTIONS,
    TRANSACTION_CAPTURE_ANALYSIS_SCHEMA_VERSION,
)
from portfolio_app.services.transaction_capture_runner import (
    run_transaction_capture_analysis,
)
from portfolio_app.services.transaction_captures import (
    MAX_TRANSACTION_CAPTURE_BYTES,
    TransactionCaptureAnalysisRunConflictError,
    TransactionCaptureBatchNotFoundError,
    TransactionCaptureNotFoundError,
    create_transaction_capture,
    create_transaction_capture_analysis_revision,
    create_transaction_capture_batch,
    get_transaction_capture,
    get_transaction_capture_batch,
    list_transaction_capture_batches,
    list_transaction_captures,
    queue_transaction_capture_analysis_run,
)


router = APIRouter()


def _capture_batch_or_404(
    portfolio_id: str,
    batch_id: str,
) -> TransactionCaptureBatchRecord:
    record = get_transaction_capture_batch(
        portfolio_id=portfolio_id,
        batch_id=batch_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Screenshot analysis batch not found.")
    return TransactionCaptureBatchRecord.model_validate(record)


@router.post(
    "/{portfolio_id}/transaction-captures",
    response_model=TransactionCaptureRecord,
    status_code=201,
)
async def upload_transaction_capture(
    portfolio_id: str,
    file: UploadFile = File(...),
) -> TransactionCaptureRecord:
    """Persist screenshot evidence without creating transaction facts."""

    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    content = await file.read(MAX_TRANSACTION_CAPTURE_BYTES + 1)
    if len(content) > MAX_TRANSACTION_CAPTURE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "Screenshot exceeds the "
                f"{MAX_TRANSACTION_CAPTURE_BYTES // (1024 * 1024)} MB limit."
            ),
        )
    try:
        record = create_transaction_capture(
            portfolio_id=portfolio_id,
            filename=file.filename,
            content=content,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return TransactionCaptureRecord.model_validate(record)


@router.get(
    "/{portfolio_id}/transaction-captures",
    response_model=TransactionCaptureListResponse,
)
def list_portfolio_transaction_captures(
    portfolio_id: str,
    limit: int = Query(default=10, ge=1, le=100),
) -> TransactionCaptureListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return TransactionCaptureListResponse(
        portfolio_id=portfolio_id,
        captures=[
            TransactionCaptureRecord.model_validate(record)
            for record in list_transaction_captures(
                portfolio_id=portfolio_id,
                limit=limit,
            )
        ],
    )


@router.get(
    "/{portfolio_id}/transaction-captures/{capture_id}",
    response_model=TransactionCaptureRecord,
)
def get_portfolio_transaction_capture(
    portfolio_id: str,
    capture_id: str,
) -> TransactionCaptureRecord:
    record = get_transaction_capture(
        portfolio_id=portfolio_id,
        capture_id=capture_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Transaction screenshot not found.")
    return TransactionCaptureRecord.model_validate(record)


@router.get("/{portfolio_id}/transaction-captures/{capture_id}/image")
def get_portfolio_transaction_capture_image(
    portfolio_id: str,
    capture_id: str,
) -> Response:
    record = get_transaction_capture(
        portfolio_id=portfolio_id,
        capture_id=capture_id,
        include_content=True,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Transaction screenshot not found.")
    return Response(
        content=bytes(record["content"]),
        media_type=str(record["media_type"]),
        headers={
            "Cache-Control": "private, max-age=3600",
            "ETag": f'"{record["content_sha256"]}"',
        },
    )


@router.post(
    "/{portfolio_id}/transaction-capture-batches",
    response_model=TransactionCaptureBatchRecord,
    status_code=201,
)
def create_portfolio_transaction_capture_batch(
    portfolio_id: str,
    payload: TransactionCaptureBatchCreateRequest,
) -> TransactionCaptureBatchRecord:
    """Group one or more screenshots into a single semantic agent task."""

    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    try:
        record = create_transaction_capture_batch(
            portfolio_id=portfolio_id,
            capture_ids=payload.capture_ids,
            purpose=payload.purpose,
        )
    except TransactionCaptureNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return TransactionCaptureBatchRecord.model_validate(record)


@router.get(
    "/{portfolio_id}/transaction-capture-batches",
    response_model=TransactionCaptureBatchListResponse,
)
def list_portfolio_transaction_capture_batches(
    portfolio_id: str,
    limit: int = Query(default=10, ge=1, le=100),
) -> TransactionCaptureBatchListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return TransactionCaptureBatchListResponse(
        portfolio_id=portfolio_id,
        batches=[
            TransactionCaptureBatchRecord.model_validate(record)
            for record in list_transaction_capture_batches(
                portfolio_id=portfolio_id,
                limit=limit,
            )
        ],
    )


@router.get(
    "/{portfolio_id}/transaction-capture-batches/{batch_id}",
    response_model=TransactionCaptureBatchRecord,
)
def get_portfolio_transaction_capture_batch(
    portfolio_id: str,
    batch_id: str,
) -> TransactionCaptureBatchRecord:
    return _capture_batch_or_404(portfolio_id, batch_id)


@router.post(
    "/{portfolio_id}/transaction-capture-batches/{batch_id}/analysis-runs",
    response_model=TransactionCaptureBatchRecord,
    status_code=202,
)
def start_portfolio_transaction_capture_analysis(
    portfolio_id: str,
    batch_id: str,
    background_tasks: BackgroundTasks,
) -> TransactionCaptureBatchRecord:
    """Queue the restricted DeepSeek harness without waiting for model completion."""

    _capture_batch_or_404(portfolio_id, batch_id)
    run_token = issue_delegation(current_principal(), "portfolio", {"kind": "capture", "id": batch_id})
    try:
        batch = queue_transaction_capture_analysis_run(
            portfolio_id=portfolio_id,
            batch_id=batch_id,
        )
    except TransactionCaptureBatchNotFoundError as error:
        revoke_delegation(run_token)
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TransactionCaptureAnalysisRunConflictError as error:
        revoke_delegation(run_token)
        raise HTTPException(status_code=409, detail=str(error)) from error
    background_tasks.add_task(
        run_transaction_capture_analysis,
        portfolio_id=portfolio_id,
        batch_id=batch_id,
        attempt=int(batch["analysis_run_attempt"]),
        run_token=run_token,
    )
    return TransactionCaptureBatchRecord.model_validate(batch)


@router.get(
    "/{portfolio_id}/transaction-capture-batches/{batch_id}/agent-context",
    response_model=TransactionCaptureAgentContextResponse,
)
def get_portfolio_transaction_capture_agent_context(
    portfolio_id: str,
    batch_id: str,
) -> TransactionCaptureAgentContextResponse:
    """Return a compact read-only context for the bound screenshot task."""

    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    batch = _capture_batch_or_404(portfolio_id, batch_id)

    portfolio_context_keys = (
        "portfolio_id",
        "portfolio_name",
        "base_currency",
        "valuation_timezone",
        "inception_date",
        "as_of_date",
    )
    account_context_keys = (
        "account_id",
        "account_name",
        "account_type",
        "account_category",
        "currency",
        "institution",
        "default_settlement_cash_account_id",
        "cost_basis_method",
        "status",
        "opened_at",
        "closed_at",
    )
    return TransactionCaptureAgentContextResponse(
        schema_version=TRANSACTION_CAPTURE_ANALYSIS_SCHEMA_VERSION,
        portfolio_scope_fixed=True,
        batch=batch,
        source_identity={
            "source_system": "portfolio_screenshot_assistant",
            "required_external_reference_format": f"{batch_id}#{{record_index}}",
            "required_external_reference_example": f"{batch_id}#1",
        },
        portfolio={
            key: portfolio.get(key)
            for key in portfolio_context_keys
            if key in portfolio
        },
        accounts=[
            {
                key: account.get(key)
                for key in account_context_keys
                if key in account
            }
            for account in list_accounts(portfolio_id)
        ],
        derivative_contracts=[
            DerivativeContractRecord.model_validate(record).model_dump(mode="json")
            for record in list_derivative_contracts(portfolio_id)
        ],
        instructions=list(TRANSACTION_CAPTURE_AGENT_INSTRUCTIONS),
        submission_schema=TransactionCaptureAnalysisCreateRequest.model_json_schema(),
        commit_tool_exposed=False,
    )


def _analysis_referenced_capture_ids(
    payload: TransactionCaptureAnalysisCreateRequest,
) -> set[str]:
    capture_ids = {document.capture_id for document in payload.analysis.documents}
    for candidate in payload.analysis.candidates:
        capture_ids.update(evidence.capture_id for evidence in candidate.evidence)
        if candidate.account_resolution is not None:
            capture_ids.update(
                evidence.capture_id
                for evidence in candidate.account_resolution.evidence
            )
        for field in candidate.fields:
            capture_ids.update(evidence.capture_id for evidence in field.evidence)
    return capture_ids


@router.post(
    "/{portfolio_id}/transaction-capture-batches/{batch_id}/analysis-revisions",
    response_model=TransactionCaptureAnalysisResponse,
)
def create_portfolio_transaction_capture_analysis_revision(
    portfolio_id: str,
    batch_id: str,
    payload: TransactionCaptureAnalysisCreateRequest,
) -> TransactionCaptureAnalysisResponse:
    """Persist a harness result and run Preview without exposing Commit."""

    if payload.source == "assistant" and (current_principal().resource_scope or {}).get("kind") != "capture":
        raise HTTPException(403, "研究员提交必须使用当前截图任务的专用凭证")
    if payload.source == "human" and current_principal().resource_scope:
        raise HTTPException(403, "模型任务不能冒用人工署名")
    batch = _capture_batch_or_404(portfolio_id, batch_id)
    batch_capture_ids = {capture.capture_id for capture in batch.captures}
    document_capture_ids = {
        document.capture_id for document in payload.analysis.documents
    }
    if document_capture_ids != batch_capture_ids:
        missing = sorted(batch_capture_ids - document_capture_ids)
        extra = sorted(document_capture_ids - batch_capture_ids)
        detail_parts: list[str] = []
        if missing:
            detail_parts.append("missing assessments for " + ", ".join(missing))
        if extra:
            detail_parts.append("unknown screenshots " + ", ".join(extra))
        raise HTTPException(
            status_code=422,
            detail="Every batch screenshot requires one assessment; "
            + "; ".join(detail_parts),
        )

    if payload.transaction_import is not None:
        required_source_system = "portfolio_screenshot_assistant"
        if payload.transaction_import.source_system != required_source_system:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Screenshot analysis transaction proposals must use source_system "
                    f"'{required_source_system}'."
                ),
            )
        invalid_references = [
            record.external_reference
            for record_index, record in enumerate(
                payload.transaction_import.records,
                start=1,
            )
            if record.external_reference != f"{batch_id}#{record_index}"
        ]
        if invalid_references:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Screenshot analysis transaction proposals must use the supplied "
                    "batch source identity '<batch_id>#<record_index>'."
                ),
            )

    allowed_account_ids = {
        str(account["account_id"])
        for account in list_accounts(portfolio_id)
        if account.get("account_id")
    }
    referenced_account_ids: set[str] = set()
    for candidate in payload.analysis.candidates:
        resolution = candidate.account_resolution
        if resolution is None:
            continue
        if resolution.account_id is not None:
            referenced_account_ids.add(resolution.account_id)
        referenced_account_ids.update(resolution.candidate_account_ids)
    unknown_account_ids = sorted(referenced_account_ids - allowed_account_ids)
    if unknown_account_ids:
        raise HTTPException(
            status_code=422,
            detail=(
                "Screenshot analysis may reference accounts only from the current "
                "portfolio context: " + ", ".join(unknown_account_ids)
            ),
        )

    referenced_existing_transaction_ids = {
        transaction_id
        for candidate in payload.analysis.candidates
        for transaction_id in candidate.possible_existing_transaction_ids
    }
    if referenced_existing_transaction_ids:
        current_transaction_ids = {
            str(transaction["transaction_id"])
            for transaction in list_transactions(portfolio_id)
            if transaction.get("transaction_id")
        }
        unknown_transaction_ids = sorted(
            referenced_existing_transaction_ids - current_transaction_ids
        )
        if unknown_transaction_ids:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Screenshot duplicate references may identify transactions only "
                    "from the current portfolio: " + ", ".join(unknown_transaction_ids)
                ),
            )

    preview = None
    transaction_import = None
    if payload.transaction_import is not None:
        preview, _prepared_records = build_transaction_import_preview(
            portfolio_id=portfolio_id,
            request=payload.transaction_import,
        )
        transaction_import = payload.transaction_import.model_dump(
            mode="json",
            exclude_none=False,
        )

    try:
        batch_record, revision_record = create_transaction_capture_analysis_revision(
            portfolio_id=portfolio_id,
            batch_id=batch_id,
            source=payload.source,
            harness=payload.harness,
            provider=payload.provider,
            model=payload.model_name,
            harness_session_id=payload.harness_session_id,
            finish_reason=payload.finish_reason,
            schema_version=payload.schema_version,
            analysis=payload.analysis.model_dump(mode="json", exclude_none=False),
            transaction_import=transaction_import,
            preview=preview.model_dump(mode="json") if preview is not None else None,
            referenced_capture_ids=_analysis_referenced_capture_ids(payload),
        )
    except TransactionCaptureBatchNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return TransactionCaptureAnalysisResponse(
        batch=TransactionCaptureBatchRecord.model_validate(batch_record),
        analysis_revision=TransactionCaptureAnalysisRevision.model_validate(
            revision_record
        ),
        preview=preview,
    )
