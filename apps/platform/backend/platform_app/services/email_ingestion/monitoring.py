from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from hashlib import sha256

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from platform_app.db.email_models import (
    EmailAttachmentParse,
    EmailAttachmentRouteContext,
    EmailFolderCursor,
    EmailMessageAttachment,
    EmailMessageOccurrence,
    EmailNavCandidate,
    EmailNavCandidateRoute,
)


MAX_INVENTORY_LIMIT = 500
MAX_OPERATIONAL_EXAMPLES = 100
_UNRESOLVED_ROUTING_STATUSES = ("unmatched", "ambiguous")
_FAILURE_STATUSES = ("retryable", "dead_letter")


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _safe_text(value: object, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized[:limit] or None


def _safe_error_text(value: object, *, limit: int = 500) -> str | None:
    normalized = _safe_text(value, limit=4000)
    if normalized is None:
        return None
    normalized = re.sub(
        r"(?i)\b(password|passwd|token|api[_-]?key|secret)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        normalized,
    )
    normalized = re.sub(
        r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@",
        r"\1[redacted]@",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[redacted-email]",
        normalized,
    )
    return normalized[:limit] or None


def _identity_id(code: str | None, name: str | None) -> str:
    payload = f"{code or ''}\x00{name or ''}".encode("utf-8")
    return sha256(payload).hexdigest()[:20]


def _candidate_route_join(statement):  # type: ignore[no-untyped-def]
    return (
        statement.join(
            EmailNavCandidate,
            EmailNavCandidate.email_nav_candidate_id
            == EmailNavCandidateRoute.email_nav_candidate_id,
        )
        .join(
            EmailAttachmentRouteContext,
            EmailAttachmentRouteContext.email_attachment_route_context_id
            == EmailNavCandidateRoute.email_attachment_route_context_id,
        )
        .join(
            EmailMessageAttachment,
            EmailMessageAttachment.email_message_attachment_id
            == EmailAttachmentRouteContext.email_message_attachment_id,
        )
        .join(
            EmailMessageOccurrence,
            EmailMessageOccurrence.email_message_occurrence_id
            == EmailMessageAttachment.email_message_occurrence_id,
        )
        .join(
            EmailFolderCursor,
            EmailFolderCursor.email_folder_cursor_id
            == EmailMessageOccurrence.email_folder_cursor_id,
        )
    )


def _unresolved_identity_read_model(
    session: Session,
    *,
    limit: int,
) -> tuple[dict[str, int], list[dict[str, object]]]:
    identity_code = func.coalesce(
        EmailNavCandidate.normalized_instrument_code,
        EmailNavCandidate.instrument_code,
        "",
    )
    identity_name = func.coalesce(
        EmailNavCandidate.normalized_instrument_name,
        EmailNavCandidate.instrument_name,
        "",
    )
    unresolved = or_(
        EmailNavCandidateRoute.routing_status.in_(_UNRESOLVED_ROUTING_STATUSES),
        EmailNavCandidateRoute.validation_status == "rejected",
    )
    base = _candidate_route_join(
        select(
            EmailNavCandidateRoute.email_nav_candidate_route_id.label("route_id"),
            identity_code.label("identity_code"),
            identity_name.label("identity_name"),
            EmailNavCandidate.instrument_code.label("instrument_code"),
            EmailNavCandidate.instrument_name.label("instrument_name"),
            EmailNavCandidate.as_of_date.label("as_of_date"),
            EmailNavCandidateRoute.routing_status.label("routing_status"),
            EmailNavCandidateRoute.validation_status.label("validation_status"),
            EmailNavCandidateRoute.rejection_reason.label("reason"),
            EmailFolderCursor.folder_name.label("folder_name"),
            EmailMessageAttachment.file_name.label("attachment_name"),
        )
    ).where(unresolved).cte("unresolved_email_nav_routes")

    aggregate = (
        select(
            base.c.identity_code,
            base.c.identity_name,
            func.max(base.c.instrument_code).label("instrument_code"),
            func.max(base.c.instrument_name).label("instrument_name"),
            func.min(base.c.as_of_date).label("first_nav_date"),
            func.max(base.c.as_of_date).label("latest_nav_date"),
            func.count(base.c.route_id).label("occurrence_count"),
            func.sum(case((base.c.routing_status == "unmatched", 1), else_=0)).label(
                "unmatched_count"
            ),
            func.sum(case((base.c.routing_status == "ambiguous", 1), else_=0)).label(
                "ambiguous_count"
            ),
            func.sum(case((base.c.validation_status == "rejected", 1), else_=0)).label(
                "rejected_count"
            ),
        )
        .group_by(base.c.identity_code, base.c.identity_name)
        .cte("unresolved_email_nav_identities")
    )
    rank = func.row_number().over(
        partition_by=(base.c.identity_code, base.c.identity_name),
        order_by=(
            case((base.c.as_of_date.is_(None), 1), else_=0),
            base.c.as_of_date.desc(),
            base.c.route_id.desc(),
        ),
    )
    examples = select(
        base.c.identity_code,
        base.c.identity_name,
        base.c.folder_name,
        base.c.attachment_name,
        base.c.reason,
        rank.label("example_rank"),
    ).cte("unresolved_email_nav_examples")

    summary_row = session.execute(
        select(
            func.count().label("identity_count"),
            func.coalesce(func.sum(aggregate.c.occurrence_count), 0).label(
                "occurrence_count"
            ),
        ).select_from(aggregate)
    ).one()
    statement = (
        select(aggregate, examples.c.folder_name, examples.c.attachment_name, examples.c.reason)
        .join(
            examples,
            (examples.c.identity_code == aggregate.c.identity_code)
            & (examples.c.identity_name == aggregate.c.identity_name)
            & (examples.c.example_rank == 1),
        )
        .order_by(
            case((aggregate.c.latest_nav_date.is_(None), 1), else_=0),
            aggregate.c.latest_nav_date.desc(),
            aggregate.c.identity_code,
            aggregate.c.identity_name,
        )
        .limit(limit)
    )

    identities: list[dict[str, object]] = []
    for row in session.execute(statement).mappings():
        code = _safe_text(row["instrument_code"], limit=256)
        name = _safe_text(row["instrument_name"], limit=500)
        identities.append(
            {
                "identity_id": _identity_id(
                    _safe_text(row["identity_code"], limit=256),
                    _safe_text(row["identity_name"], limit=500),
                ),
                "instrument_code": code,
                "instrument_name": name,
                "first_nav_date": _iso(row["first_nav_date"]),
                "latest_nav_date": _iso(row["latest_nav_date"]),
                "occurrence_count": int(row["occurrence_count"]),
                "routing_statuses": {
                    "unmatched": int(row["unmatched_count"] or 0),
                    "ambiguous": int(row["ambiguous_count"] or 0),
                    "rejected": int(row["rejected_count"] or 0),
                },
                "example": {
                    "folder_name": _safe_text(row["folder_name"], limit=512),
                    "attachment_name": _safe_text(row["attachment_name"], limit=500),
                    "reason": _safe_error_text(row["reason"]),
                },
            }
        )
    return (
        {
            "unresolved_identity_count": int(summary_row.identity_count or 0),
            "unresolved_occurrence_count": int(summary_row.occurrence_count or 0),
        },
        identities,
    )


def _route_counts(session: Session) -> tuple[int, dict[str, int], dict[str, int]]:
    grouped = session.execute(
        select(
            EmailNavCandidateRoute.routing_status,
            EmailNavCandidateRoute.validation_status,
            func.count().label("count"),
        ).group_by(
            EmailNavCandidateRoute.routing_status,
            EmailNavCandidateRoute.validation_status,
        )
    ).all()
    routing: defaultdict[str, int] = defaultdict(int)
    validation: defaultdict[str, int] = defaultdict(int)
    total = 0
    for routing_status, validation_status, count in grouped:
        count_value = int(count)
        routing[str(routing_status)] += count_value
        validation[str(validation_status)] += count_value
        total += count_value
    return total, dict(sorted(routing.items())), dict(sorted(validation.items()))


def _folder_read_model(
    session: Session,
    *,
    limit: int,
) -> tuple[dict[str, int], list[dict[str, object]]]:
    uid_lag = case(
        (EmailFolderCursor.last_observed_uid_next.is_(None), 0),
        (
            EmailFolderCursor.last_observed_uid_next - 1
            > EmailFolderCursor.last_committed_uid,
            EmailFolderCursor.last_observed_uid_next
            - 1
            - EmailFolderCursor.last_committed_uid,
        ),
        else_=0,
    )
    summary = session.execute(
        select(
            func.count().label("folder_count"),
            func.coalesce(func.sum(uid_lag), 0).label("total_uid_lag"),
            func.coalesce(func.sum(case((uid_lag > 0, 1), else_=0)), 0).label(
                "folders_with_lag"
            ),
            func.coalesce(
                func.sum(
                    case((EmailFolderCursor.last_error_at.is_not(None), 1), else_=0)
                ),
                0,
            ).label("folders_with_error"),
        )
    ).one()
    rows = session.execute(
        select(EmailFolderCursor, uid_lag.label("uid_lag"))
        .order_by(
            case((EmailFolderCursor.last_error_at.is_not(None), 0), else_=1),
            uid_lag.desc(),
            EmailFolderCursor.folder_name,
        )
        .limit(limit)
    ).all()
    folders = [
        {
            "folder_id": _identity_id(cursor.mailbox_key, cursor.folder_name),
            "folder_name": _safe_text(cursor.folder_name, limit=512),
            "last_committed_uid": int(cursor.last_committed_uid),
            "last_observed_uid_next": (
                int(cursor.last_observed_uid_next)
                if cursor.last_observed_uid_next is not None
                else None
            ),
            "uid_lag": int(lag or 0),
            "last_scan_started_at": _iso(cursor.last_scan_started_at),
            "last_scan_succeeded_at": _iso(cursor.last_scan_succeeded_at),
            "error": (
                {
                    "at": _iso(cursor.last_error_at),
                    "code": _safe_text(cursor.last_error_code, limit=64),
                    "reason": _safe_error_text(cursor.last_error_message),
                }
                if cursor.last_error_at is not None
                else None
            ),
        }
        for cursor, lag in rows
    ]
    return (
        {
            "folder_count": int(summary.folder_count or 0),
            "total_uid_lag": int(summary.total_uid_lag or 0),
            "folders_with_lag": int(summary.folders_with_lag or 0),
            "folders_with_error": int(summary.folders_with_error or 0),
        },
        folders,
    )


def _failure_read_model(
    session: Session,
    *,
    example_limit: int,
) -> tuple[dict[str, int], list[dict[str, object]], list[dict[str, object]]]:
    message_counts = {
        str(status): int(count)
        for status, count in session.execute(
            select(
                EmailMessageOccurrence.acquisition_status,
                func.count().label("count"),
            )
            .where(EmailMessageOccurrence.acquisition_status.in_(_FAILURE_STATUSES))
            .group_by(EmailMessageOccurrence.acquisition_status)
        ).all()
    }
    parse_counts = {
        str(status): int(count)
        for status, count in session.execute(
            select(EmailAttachmentParse.status, func.count().label("count"))
            .where(EmailAttachmentParse.status.in_((*_FAILURE_STATUSES, "unsupported")))
            .group_by(EmailAttachmentParse.status)
        ).all()
    }
    message_rows = session.execute(
        select(EmailMessageOccurrence, EmailFolderCursor.folder_name)
        .join(
            EmailFolderCursor,
            EmailFolderCursor.email_folder_cursor_id
            == EmailMessageOccurrence.email_folder_cursor_id,
        )
        .where(EmailMessageOccurrence.acquisition_status.in_(_FAILURE_STATUSES))
        .order_by(
            case((EmailMessageOccurrence.acquisition_status == "dead_letter", 0), else_=1),
            EmailMessageOccurrence.email_message_occurrence_id.desc(),
        )
        .limit(example_limit)
    ).all()

    first_attachment = (
        select(
            EmailMessageAttachment.email_attachment_artifact_id.label("artifact_id"),
            func.min(EmailMessageAttachment.email_message_attachment_id).label(
                "message_attachment_id"
            ),
        )
        .group_by(EmailMessageAttachment.email_attachment_artifact_id)
        .cte("first_message_attachment_for_artifact")
    )
    parse_rows = session.execute(
        select(
            EmailAttachmentParse,
            EmailMessageAttachment.file_name,
            EmailFolderCursor.folder_name,
        )
        .outerjoin(
            first_attachment,
            first_attachment.c.artifact_id
            == EmailAttachmentParse.email_attachment_artifact_id,
        )
        .outerjoin(
            EmailMessageAttachment,
            EmailMessageAttachment.email_message_attachment_id
            == first_attachment.c.message_attachment_id,
        )
        .outerjoin(
            EmailMessageOccurrence,
            EmailMessageOccurrence.email_message_occurrence_id
            == EmailMessageAttachment.email_message_occurrence_id,
        )
        .outerjoin(
            EmailFolderCursor,
            EmailFolderCursor.email_folder_cursor_id
            == EmailMessageOccurrence.email_folder_cursor_id,
        )
        .where(EmailAttachmentParse.status.in_((*_FAILURE_STATUSES, "unsupported")))
        .order_by(
            case((EmailAttachmentParse.status == "dead_letter", 0), else_=1),
            EmailAttachmentParse.email_attachment_parse_id.desc(),
        )
        .limit(example_limit)
    ).all()

    return (
        {
            "message_retryable": message_counts.get("retryable", 0),
            "message_dead_letter": message_counts.get("dead_letter", 0),
            "parse_retryable": parse_counts.get("retryable", 0),
            "parse_dead_letter": parse_counts.get("dead_letter", 0),
            "parse_unsupported": parse_counts.get("unsupported", 0),
        },
        [
            {
                "failure_id": f"message-{occurrence.email_message_occurrence_id}",
                "status": occurrence.acquisition_status,
                "folder_name": _safe_text(folder_name, limit=512),
                "attempt_count": int(occurrence.acquisition_attempt_count),
                "next_retry_at": _iso(occurrence.next_retry_at),
                "error_code": _safe_text(occurrence.last_error_code, limit=64),
                "reason": _safe_error_text(occurrence.last_error_message),
            }
            for occurrence, folder_name in message_rows
        ],
        [
            {
                "failure_id": f"parse-{parse.email_attachment_parse_id}",
                "status": parse.status,
                "folder_name": _safe_text(folder_name, limit=512),
                "attachment_name": _safe_text(file_name, limit=500),
                "source_format": _safe_text(parse.source_format, limit=16),
                "attempt_count": int(parse.attempt_count),
                "next_retry_at": _iso(parse.next_retry_at),
                "error_code": _safe_text(parse.last_error_code, limit=64),
                "reason": _safe_error_text(parse.last_error_message),
            }
            for parse, file_name, folder_name in parse_rows
        ],
    )


def build_email_nav_inventory(
    session_factory: sessionmaker[Session],
    *,
    limit: int = 100,
) -> dict[str, object]:
    """Build a bounded read model solely from durable email-ingestion metadata.

    The monitor never loads message bodies, attachment payloads, raw parsed rows,
    mailbox account keys, or credentials, and it never invokes IMAP.
    """
    normalized_limit = max(1, min(int(limit), MAX_INVENTORY_LIMIT))
    example_limit = min(normalized_limit, MAX_OPERATIONAL_EXAMPLES)
    with session_factory() as session:
        unresolved_counts, unresolved_identities = _unresolved_identity_read_model(
            session,
            limit=normalized_limit,
        )
        route_count, routing_statuses, validation_statuses = _route_counts(session)
        matched_unimported_count = int(
            session.scalar(
                select(func.count())
                .select_from(EmailNavCandidateRoute)
                .where(
                    EmailNavCandidateRoute.routing_status == "matched",
                    EmailNavCandidateRoute.validation_status == "valid",
                    EmailNavCandidateRoute.instrument_id.is_not(None),
                    EmailNavCandidateRoute.imported_at.is_(None),
                )
            )
            or 0
        )
        folder_counts, folders = _folder_read_model(
            session,
            limit=normalized_limit,
        )
        failure_counts, message_failures, parse_failures = _failure_read_model(
            session,
            example_limit=example_limit,
        )

    return {
        "limit": normalized_limit,
        "counts": {
            "candidate_routes": route_count,
            "matched_unimported": matched_unimported_count,
            **unresolved_counts,
            **folder_counts,
            **failure_counts,
        },
        "routing_statuses": routing_statuses,
        "validation_statuses": validation_statuses,
        "unresolved_identities": unresolved_identities,
        "folders": folders,
        "message_failures": message_failures,
        "parse_failures": parse_failures,
        "automatic_fund_creation": False,
    }
