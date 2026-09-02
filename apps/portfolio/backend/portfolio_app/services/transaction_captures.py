from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from portfolio_app.core.settings import get_settings
from portfolio_app.db.models import (
    TransactionCaptureAnalysisRevisionModel,
    TransactionCaptureBatchItemModel,
    TransactionCaptureBatchModel,
    TransactionCaptureRecordModel,
    TransactionRecordModel,
)
from portfolio_app.db.session import get_session_factory


MAX_TRANSACTION_CAPTURE_BYTES = 12 * 1024 * 1024
MAX_TRANSACTION_CAPTURE_BATCH_IMAGES = 10


class TransactionCaptureNotFoundError(LookupError):
    pass


class TransactionCaptureBatchNotFoundError(LookupError):
    pass


class TransactionCaptureAnalysisRunConflictError(RuntimeError):
    pass


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _effective_analysis_run_state(
    row: TransactionCaptureBatchModel,
    *,
    latest_analysis: TransactionCaptureAnalysisRevisionModel | None,
    now: datetime | None = None,
) -> tuple[str, str | None]:
    status = row.analysis_run_status
    if status not in {"queued", "running"}:
        return status, row.analysis_run_error

    started_at = _parse_utc_timestamp(row.analysis_run_started_at)
    latest_created_at = _parse_utc_timestamp(
        latest_analysis.created_at if latest_analysis is not None else None
    )
    if (
        started_at is not None
        and latest_created_at is not None
        and latest_created_at >= started_at
        and latest_analysis is not None
        and latest_analysis.source == "assistant"
        and latest_analysis.harness == "deepseek-harness"
    ):
        return "succeeded", None

    activity_at = started_at or _parse_utc_timestamp(row.updated_at)
    if activity_at is None:
        return status, row.analysis_run_error
    timeout_seconds = get_settings().copilot_analysis_timeout_seconds
    current_time = now or datetime.now(UTC)
    if (current_time - activity_at).total_seconds() < timeout_seconds:
        return status, row.analysis_run_error
    return (
        "failed",
        "The previous analysis was interrupted. Retry the screenshot analysis.",
    )


def _detect_image_media_type(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_transaction_capture_image(content: bytes) -> str:
    if not content:
        raise ValueError("Screenshot file is empty.")
    if len(content) > MAX_TRANSACTION_CAPTURE_BYTES:
        raise ValueError(
            f"Screenshot exceeds the {MAX_TRANSACTION_CAPTURE_BYTES // (1024 * 1024)} MB limit."
        )
    media_type = _detect_image_media_type(content)
    if media_type is None:
        raise ValueError("Screenshot must be a PNG, JPEG, or WebP image.")
    return media_type


def _safe_filename(filename: str | None, media_type: str) -> str:
    suffixes = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    raw_name = Path(str(filename or "").replace("\\", "/")).name
    normalized = "".join(character for character in raw_name if character.isprintable()).strip()
    return (normalized or f"screenshot{suffixes[media_type]}")[:255]


def _serialize_capture(row: TransactionCaptureRecordModel) -> dict[str, object]:
    return {
        "capture_id": row.capture_id,
        "portfolio_id": row.portfolio_id,
        "original_filename": row.original_filename,
        "media_type": row.media_type,
        "byte_size": row.byte_size,
        "content_sha256": row.content_sha256,
        "created_at": row.created_at,
    }


def _serialize_analysis(
    row: TransactionCaptureAnalysisRevisionModel,
) -> dict[str, object]:
    return {
        "batch_id": row.batch_id,
        "revision": row.revision,
        "source": row.source,
        "harness": row.harness,
        "provider": row.provider,
        "model_name": row.model,
        "harness_session_id": row.harness_session_id,
        "finish_reason": row.finish_reason,
        "schema_version": row.schema_version,
        "analysis": row.analysis_json,
        "transaction_import": row.transaction_import_json,
        "preview_digest": row.preview_digest,
        "preview_error_count": row.preview_error_count,
        "created_at": row.created_at,
    }


def _serialize_batch(
    session,
    row: TransactionCaptureBatchModel,
    *,
    latest_analysis: TransactionCaptureAnalysisRevisionModel | None,
) -> dict[str, object]:
    ordered_items = sorted(row.items, key=lambda item: item.ordinal)
    analysis_run_status, analysis_run_error = _effective_analysis_run_state(
        row,
        latest_analysis=latest_analysis,
    )
    proposed_references: list[str] = []
    transaction_import = (
        latest_analysis.transaction_import_json
        if latest_analysis is not None
        else None
    )
    if isinstance(transaction_import, dict):
        records = transaction_import.get("records")
        if isinstance(records, list):
            proposed_references = [
                str(record.get("external_reference") or "").strip()
                for record in records
                if isinstance(record, dict)
                and str(record.get("external_reference") or "").strip()
            ]

    recorded_rows = session.execute(
        select(
            TransactionRecordModel.transaction_id,
            TransactionRecordModel.external_reference,
        ).where(
            TransactionRecordModel.portfolio_id == row.portfolio_id,
            TransactionRecordModel.source_system == "portfolio_screenshot_assistant",
            TransactionRecordModel.external_reference.like(f"{row.batch_id}#%"),
        )
    ).all()
    recorded_by_reference = {
        str(external_reference): str(transaction_id)
        for transaction_id, external_reference in recorded_rows
        if external_reference
    }
    recorded_transaction_ids = [
        recorded_by_reference[reference]
        for reference in proposed_references
        if reference in recorded_by_reference
    ]
    recorded_transaction_ids.extend(
        transaction_id
        for reference, transaction_id in sorted(recorded_by_reference.items())
        if reference not in proposed_references
    )
    matched_reference_count = sum(
        1 for reference in proposed_references if reference in recorded_by_reference
    )
    if not proposed_references:
        ledger_status = "recorded" if recorded_by_reference else "no_proposal"
    elif matched_reference_count == len(proposed_references):
        ledger_status = "recorded"
    elif recorded_by_reference:
        ledger_status = "partially_recorded"
    else:
        ledger_status = "unrecorded"
    return {
        "batch_id": row.batch_id,
        "portfolio_id": row.portfolio_id,
        "purpose": row.purpose,
        "status": row.status,
        "capture_count": row.capture_count,
        "latest_analysis_revision": row.latest_analysis_revision,
        "analysis_run_status": analysis_run_status,
        "analysis_run_attempt": row.analysis_run_attempt,
        "analysis_run_started_at": row.analysis_run_started_at,
        "analysis_run_completed_at": row.analysis_run_completed_at,
        "analysis_run_error": analysis_run_error,
        "captures": [_serialize_capture(item.capture) for item in ordered_items],
        "latest_analysis": (
            _serialize_analysis(latest_analysis) if latest_analysis is not None else None
        ),
        "ledger_status": ledger_status,
        "recorded_transaction_ids": recorded_transaction_ids,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _batch_query():
    return select(TransactionCaptureBatchModel).options(
        selectinload(TransactionCaptureBatchModel.items).selectinload(
            TransactionCaptureBatchItemModel.capture
        )
    )


def _latest_analysis(
    session,
    batch: TransactionCaptureBatchModel,
) -> TransactionCaptureAnalysisRevisionModel | None:
    if batch.latest_analysis_revision < 1:
        return None
    return session.get(
        TransactionCaptureAnalysisRevisionModel,
        (batch.batch_id, batch.latest_analysis_revision),
    )


def create_transaction_capture(
    *,
    portfolio_id: str,
    filename: str | None,
    content: bytes,
) -> dict[str, object]:
    media_type = validate_transaction_capture_image(content)
    content_sha256 = sha256(content).hexdigest()
    capture_id = "capture-" + uuid5(
        NAMESPACE_URL,
        f"{portfolio_id}:transaction-screenshot:{content_sha256}",
    ).hex
    session_factory = get_session_factory()
    with session_factory() as session:
        existing = session.scalar(
            select(TransactionCaptureRecordModel).where(
                TransactionCaptureRecordModel.portfolio_id == portfolio_id,
                TransactionCaptureRecordModel.content_sha256 == content_sha256,
            )
        )
        if existing is not None:
            return _serialize_capture(existing)

        row = TransactionCaptureRecordModel(
            capture_id=capture_id,
            portfolio_id=portfolio_id,
            original_filename=_safe_filename(filename, media_type),
            media_type=media_type,
            byte_size=len(content),
            content_sha256=content_sha256,
            content=content,
            created_at=_utc_timestamp(),
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(TransactionCaptureRecordModel).where(
                    TransactionCaptureRecordModel.portfolio_id == portfolio_id,
                    TransactionCaptureRecordModel.content_sha256 == content_sha256,
                )
            )
            if existing is None:
                raise
            return _serialize_capture(existing)
        return _serialize_capture(row)


def list_transaction_captures(
    *,
    portfolio_id: str,
    limit: int,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.scalars(
            select(TransactionCaptureRecordModel)
            .where(TransactionCaptureRecordModel.portfolio_id == portfolio_id)
            .order_by(
                TransactionCaptureRecordModel.created_at.desc(),
                TransactionCaptureRecordModel.capture_id.desc(),
            )
            .limit(limit)
        ).all()
        return [_serialize_capture(row) for row in rows]


def get_transaction_capture(
    *,
    portfolio_id: str,
    capture_id: str,
    include_content: bool = False,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        row = session.scalar(
            select(TransactionCaptureRecordModel).where(
                TransactionCaptureRecordModel.portfolio_id == portfolio_id,
                TransactionCaptureRecordModel.capture_id == capture_id,
            )
        )
        if row is None:
            return None
        result = _serialize_capture(row)
        if include_content:
            result["content"] = row.content
        return result


def _normalized_capture_ids(capture_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for capture_id in capture_ids:
        value = str(capture_id).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    if not normalized:
        raise ValueError("At least one screenshot is required for an analysis batch.")
    if len(normalized) > MAX_TRANSACTION_CAPTURE_BATCH_IMAGES:
        raise ValueError(
            "An analysis batch supports at most "
            f"{MAX_TRANSACTION_CAPTURE_BATCH_IMAGES} screenshots."
        )
    return normalized


def _batch_content_key(*, purpose: str, capture_ids: list[str]) -> str:
    canonical = json.dumps(
        {"purpose": purpose, "capture_ids": sorted(capture_ids)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def create_transaction_capture_batch(
    *,
    portfolio_id: str,
    capture_ids: list[str],
    purpose: str,
) -> dict[str, object]:
    normalized_ids = _normalized_capture_ids(capture_ids)
    content_key = _batch_content_key(purpose=purpose, capture_ids=normalized_ids)
    batch_id = "capture-batch-" + uuid5(
        NAMESPACE_URL,
        f"{portfolio_id}:transaction-capture-batch:{content_key}",
    ).hex
    session_factory = get_session_factory()
    with session_factory() as session:
        existing = session.scalar(
            _batch_query().where(
                TransactionCaptureBatchModel.portfolio_id == portfolio_id,
                TransactionCaptureBatchModel.content_key == content_key,
            )
        )
        if existing is not None:
            return _serialize_batch(
                session,
                existing,
                latest_analysis=_latest_analysis(session, existing),
            )

        captures = session.scalars(
            select(TransactionCaptureRecordModel).where(
                TransactionCaptureRecordModel.portfolio_id == portfolio_id,
                TransactionCaptureRecordModel.capture_id.in_(normalized_ids),
            )
        ).all()
        capture_lookup = {capture.capture_id: capture for capture in captures}
        missing = [capture_id for capture_id in normalized_ids if capture_id not in capture_lookup]
        if missing:
            raise TransactionCaptureNotFoundError(
                "Screenshot evidence was not found in this portfolio: " + ", ".join(missing)
            )

        timestamp = _utc_timestamp()
        row = TransactionCaptureBatchModel(
            batch_id=batch_id,
            portfolio_id=portfolio_id,
            purpose=purpose,
            status="ready",
            content_key=content_key,
            capture_count=len(normalized_ids),
            latest_analysis_revision=0,
            analysis_run_status="idle",
            analysis_run_attempt=0,
            created_at=timestamp,
            updated_at=timestamp,
        )
        row.items = [
            TransactionCaptureBatchItemModel(
                batch_id=batch_id,
                capture_id=capture_id,
                ordinal=ordinal,
                capture=capture_lookup[capture_id],
            )
            for ordinal, capture_id in enumerate(normalized_ids, start=1)
        ]
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                _batch_query().where(
                    TransactionCaptureBatchModel.portfolio_id == portfolio_id,
                    TransactionCaptureBatchModel.content_key == content_key,
                )
            )
            if existing is None:
                raise
            return _serialize_batch(
                session,
                existing,
                latest_analysis=_latest_analysis(session, existing),
            )
        return _serialize_batch(session, row, latest_analysis=None)


def list_transaction_capture_batches(
    *,
    portfolio_id: str,
    limit: int,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.scalars(
            _batch_query()
            .where(TransactionCaptureBatchModel.portfolio_id == portfolio_id)
            .order_by(
                TransactionCaptureBatchModel.created_at.desc(),
                TransactionCaptureBatchModel.batch_id.desc(),
            )
            .limit(limit)
        ).all()
        return [
            _serialize_batch(
                session,
                row,
                latest_analysis=_latest_analysis(session, row),
            )
            for row in rows
        ]


def get_transaction_capture_batch(
    *,
    portfolio_id: str,
    batch_id: str,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        row = session.scalar(
            _batch_query().where(
                TransactionCaptureBatchModel.portfolio_id == portfolio_id,
                TransactionCaptureBatchModel.batch_id == batch_id,
            )
        )
        if row is None:
            return None
        return _serialize_batch(
            session,
            row,
            latest_analysis=_latest_analysis(session, row),
        )


def has_transaction_capture_agent_revision(
    *,
    portfolio_id: str,
    batch_id: str,
    after_revision: int,
) -> bool:
    session_factory = get_session_factory()
    with session_factory() as session:
        revision = session.scalar(
            select(TransactionCaptureAnalysisRevisionModel.revision)
            .join(TransactionCaptureAnalysisRevisionModel.batch)
            .where(
                TransactionCaptureBatchModel.portfolio_id == portfolio_id,
                TransactionCaptureAnalysisRevisionModel.batch_id == batch_id,
                TransactionCaptureAnalysisRevisionModel.revision > after_revision,
                TransactionCaptureAnalysisRevisionModel.source == "assistant",
                TransactionCaptureAnalysisRevisionModel.harness == "deepseek-harness",
            )
            .limit(1)
        )
        return revision is not None


def queue_transaction_capture_analysis_run(
    *,
    portfolio_id: str,
    batch_id: str,
) -> dict[str, object]:
    session_factory = get_session_factory()
    with session_factory() as session:
        batch = session.scalar(
            _batch_query()
            .where(
                TransactionCaptureBatchModel.portfolio_id == portfolio_id,
                TransactionCaptureBatchModel.batch_id == batch_id,
            )
            .with_for_update()
        )
        if batch is None:
            raise TransactionCaptureBatchNotFoundError(
                "Screenshot analysis batch not found."
            )
        latest_analysis = _latest_analysis(session, batch)
        analysis_run_status, _analysis_run_error = _effective_analysis_run_state(
            batch,
            latest_analysis=latest_analysis,
        )
        if analysis_run_status in {"queued", "running"}:
            raise TransactionCaptureAnalysisRunConflictError(
                "Screenshot analysis is already running for this batch."
            )
        timestamp = _utc_timestamp()
        batch.analysis_run_status = "queued"
        batch.analysis_run_attempt += 1
        batch.analysis_run_started_at = None
        batch.analysis_run_completed_at = None
        batch.analysis_run_error = None
        batch.updated_at = timestamp
        session.commit()
        return _serialize_batch(
            session,
            batch,
            latest_analysis=latest_analysis,
        )


def mark_transaction_capture_analysis_run_started(
    *,
    portfolio_id: str,
    batch_id: str,
    attempt: int,
) -> tuple[bool, int]:
    session_factory = get_session_factory()
    with session_factory() as session:
        batch = session.scalar(
            _batch_query()
            .where(
                TransactionCaptureBatchModel.portfolio_id == portfolio_id,
                TransactionCaptureBatchModel.batch_id == batch_id,
            )
            .with_for_update()
        )
        if batch is None:
            return False, 0
        if (
            batch.analysis_run_attempt != attempt
            or batch.analysis_run_status != "queued"
        ):
            return False, batch.latest_analysis_revision
        timestamp = _utc_timestamp()
        batch.analysis_run_status = "running"
        batch.analysis_run_started_at = timestamp
        batch.analysis_run_completed_at = None
        batch.analysis_run_error = None
        batch.updated_at = timestamp
        starting_revision = batch.latest_analysis_revision
        session.commit()
        return True, starting_revision


def finish_transaction_capture_analysis_run(
    *,
    portfolio_id: str,
    batch_id: str,
    attempt: int,
    succeeded: bool,
    error: str | None = None,
) -> bool:
    session_factory = get_session_factory()
    with session_factory() as session:
        batch = session.scalar(
            _batch_query()
            .where(
                TransactionCaptureBatchModel.portfolio_id == portfolio_id,
                TransactionCaptureBatchModel.batch_id == batch_id,
            )
            .with_for_update()
        )
        if batch is None or batch.analysis_run_attempt != attempt:
            return False
        timestamp = _utc_timestamp()
        batch.analysis_run_status = "succeeded" if succeeded else "failed"
        batch.analysis_run_completed_at = timestamp
        batch.analysis_run_error = None if succeeded else str(error or "Analysis failed.")[:1_000]
        batch.updated_at = timestamp
        session.commit()
        return True


def create_transaction_capture_analysis_revision(
    *,
    portfolio_id: str,
    batch_id: str,
    source: str,
    harness: str | None,
    provider: str | None,
    model: str | None,
    harness_session_id: str | None,
    finish_reason: str | None,
    schema_version: str,
    analysis: dict[str, object],
    transaction_import: dict[str, object] | None,
    preview: dict[str, object] | None,
    referenced_capture_ids: set[str],
) -> tuple[dict[str, object], dict[str, object]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        batch = session.scalar(
            _batch_query()
            .where(
                TransactionCaptureBatchModel.portfolio_id == portfolio_id,
                TransactionCaptureBatchModel.batch_id == batch_id,
            )
            .with_for_update()
        )
        if batch is None:
            raise TransactionCaptureBatchNotFoundError("Screenshot analysis batch not found.")

        batch_capture_ids = {item.capture_id for item in batch.items}
        invalid_capture_ids = sorted(referenced_capture_ids - batch_capture_ids)
        if invalid_capture_ids:
            raise ValueError(
                "Analysis evidence references screenshots outside this batch: "
                + ", ".join(invalid_capture_ids)
            )

        revision = batch.latest_analysis_revision + 1
        timestamp = _utc_timestamp()
        analysis_row = TransactionCaptureAnalysisRevisionModel(
            batch_id=batch.batch_id,
            revision=revision,
            source=source,
            harness=harness,
            provider=provider,
            model=model,
            harness_session_id=harness_session_id,
            finish_reason=finish_reason,
            schema_version=schema_version,
            analysis_json=analysis,
            transaction_import_json=transaction_import,
            preview_digest=str(preview["preview_digest"]) if preview is not None else None,
            preview_error_count=int(preview["error_count"]) if preview is not None else None,
            preview_json=preview,
            created_at=timestamp,
        )
        batch.status = "review_required"
        batch.latest_analysis_revision = revision
        batch.updated_at = timestamp
        session.add(analysis_row)
        session.commit()
        return (
            _serialize_batch(session, batch, latest_analysis=analysis_row),
            _serialize_analysis(analysis_row),
        )
