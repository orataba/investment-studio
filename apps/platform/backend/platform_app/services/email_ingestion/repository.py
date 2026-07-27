from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
from typing import Literal
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from platform_app.db.email_models import (
    EmailAttachmentArtifact,
    EmailAttachmentParse,
    EmailAttachmentRouteContext,
    EmailFolderCursor,
    EmailMailboxIngestionLease,
    EmailMessageAttachment,
    EmailMessageOccurrence,
    EmailNavCandidate,
    EmailNavCandidateRoute,
)

from .rules import normalize_text_token
from .types import AttachmentPayload, FolderIdentity, MessageHeader


PARSER_VERSION = "fund-nav-v2"
PARSER_PROFILE_VERSIONS = {
    "generic_nav_table": "fund-nav-v3",
    "label_nav_snapshot": "label-nav-v3",
    "ta_virtual_performance_ledger_initial_nav": "ta-ledger-initial-nav-v1",
}
MAX_PARSE_ATTEMPTS = 5
HISTORY_START_MESSAGE_REASON_CODE = "BeforeHistoryStartDate"
HISTORY_START_NAV_REASON_PREFIX = (
    "NAV observation predates configured email history start date "
)


def history_start_message_reason(history_start_date: date) -> str:
    return (
        "Message predates configured email history start date "
        f"{history_start_date.isoformat()}."
    )


def history_start_nav_reason(history_start_date: date) -> str:
    return f"{HISTORY_START_NAV_REASON_PREFIX}{history_start_date.isoformat()}."


def parser_version_for_profile(parser_profile: str) -> str:
    normalized_profile = parser_profile.strip()
    return PARSER_PROFILE_VERSIONS.get(normalized_profile, PARSER_VERSION)


class EmailIngestionAlreadyRunningError(RuntimeError):
    pass


class StaleEmailIngestionLeaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class CursorState:
    cursor_id: int
    first_uid: int
    generation_changed: bool


@dataclass(frozen=True)
class ArtifactWork:
    artifact_id: int
    message_attachment_id: int
    part_id: str
    filename: str
    media_type: str
    content_sha256: str


@dataclass(frozen=True)
class ParseWork:
    parse_id: int
    artifact_id: int
    parser_profile: str
    source_format: str
    disposition: Literal["leased", "cached", "pending", "terminal"]
    lease_token: str | None
    payload: bytes
    metadata: dict[str, object]


@dataclass(frozen=True)
class CandidateRouteWork:
    route_id: int
    parsed_candidate_id: int
    instrument_id: str | None
    row: dict[str, object]
    routing_contexts: tuple[dict[str, object], ...]
    folder_name: str
    message_uid: int
    sent_at: datetime | None
    attachment_name: str


@dataclass(frozen=True)
class CandidateRouteDecision:
    route_id: int
    status: Literal["matched", "ambiguous", "unmatched"]
    instrument_id: str | None
    rule_fingerprint: str | None
    reason: str | None = None
    validation_status: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _is_after(value: datetime | None, boundary: datetime) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        return value > boundary.replace(tzinfo=None)
    return value > boundary


def _json_value(value: object) -> object:
    if isinstance(value, (datetime, Decimal)):
        return str(value)
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _candidate_as_row(candidate: EmailNavCandidate) -> dict[str, object]:
    row: dict[str, object] = {
        **dict(candidate.raw_row_json or {}),
        "as_of_date": candidate.as_of_date.isoformat() if candidate.as_of_date else "",
        "instrument_code": candidate.instrument_code or "",
        "instrument_name": candidate.instrument_name or "",
        "nav": candidate.unit_nav_value,
        "cash_cumulative_nav": candidate.reported_cash_cumulative_nav_value,
        "nav_with_dividend": candidate.reported_reinvested_nav_value,
        "frequency": candidate.reported_frequency or "",
    }
    for value_field in ("nav", "cash_cumulative_nav", "nav_with_dividend"):
        if row.get(value_field) is not None and row.get(value_field) != "":
            row[f"_{value_field}_status"] = "complete"
    return row


class EmailIngestionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def acquire_mailbox_lease(
        self,
        *,
        mailbox_key: str,
        lease_seconds: int,
    ) -> str:
        """Atomically claim the one acquisition lane for a physical mailbox."""
        now = _utcnow()
        lease_token = uuid4().hex
        lease_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
        try:
            with self.session_factory() as session:
                session.add(
                    EmailMailboxIngestionLease(
                        mailbox_key=mailbox_key,
                        lease_token=lease_token,
                        lease_acquired_at=now,
                        lease_expires_at=lease_expires_at,
                    )
                )
                session.commit()
                return lease_token
        except IntegrityError:
            pass

        with self.session_factory() as session:
            result = session.execute(
                update(EmailMailboxIngestionLease)
                .where(
                    EmailMailboxIngestionLease.mailbox_key == mailbox_key,
                    (
                        EmailMailboxIngestionLease.lease_token.is_(None)
                        | (EmailMailboxIngestionLease.lease_expires_at.is_(None))
                        | (EmailMailboxIngestionLease.lease_expires_at <= now)
                    ),
                )
                .values(
                    lease_token=lease_token,
                    lease_acquired_at=now,
                    lease_expires_at=lease_expires_at,
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise EmailIngestionAlreadyRunningError(
                    "Another email NAV ingestion run owns the mailbox lease."
                )
            session.commit()
            return lease_token

    def renew_mailbox_lease(
        self,
        *,
        mailbox_key: str,
        lease_token: str,
        lease_seconds: int,
    ) -> None:
        now = _utcnow()
        with self.session_factory() as session:
            result = session.execute(
                update(EmailMailboxIngestionLease)
                .where(
                    EmailMailboxIngestionLease.mailbox_key == mailbox_key,
                    EmailMailboxIngestionLease.lease_token == lease_token,
                    EmailMailboxIngestionLease.lease_expires_at > now,
                )
                .values(
                    lease_expires_at=now
                    + timedelta(seconds=max(1, int(lease_seconds)))
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise StaleEmailIngestionLeaseError(
                    "Email NAV ingestion mailbox lease expired or was superseded."
                )
            session.commit()

    def release_mailbox_lease(
        self,
        *,
        mailbox_key: str,
        lease_token: str,
    ) -> bool:
        with self.session_factory() as session:
            result = session.execute(
                update(EmailMailboxIngestionLease)
                .where(
                    EmailMailboxIngestionLease.mailbox_key == mailbox_key,
                    EmailMailboxIngestionLease.lease_token == lease_token,
                )
                .values(
                    lease_token=None,
                    lease_acquired_at=None,
                    lease_expires_at=None,
                )
            )
            session.commit()
            return result.rowcount == 1

    def start_folder_scan(
        self,
        identity: FolderIdentity,
        *,
        lease_token: str,
    ) -> CursorState:
        now = _utcnow()
        with self.session_factory() as session:
            self._require_mailbox_lease(
                session,
                mailbox_key=identity.mailbox_key,
                lease_token=lease_token,
                now=now,
            )
            cursor = session.scalar(
                select(EmailFolderCursor)
                .where(
                    EmailFolderCursor.mailbox_key == identity.mailbox_key,
                    EmailFolderCursor.folder_name == identity.folder_name,
                )
                .with_for_update()
            )
            generation_changed = False
            if cursor is None:
                cursor = EmailFolderCursor(
                    mailbox_key=identity.mailbox_key,
                    folder_name=identity.folder_name,
                    uid_validity=int(identity.uid_validity),
                    last_committed_uid=0,
                )
                session.add(cursor)
                session.flush()
            elif cursor.uid_validity != int(identity.uid_validity):
                generation_changed = cursor.uid_validity is not None
                cursor.uid_validity = int(identity.uid_validity)
                cursor.last_committed_uid = 0
            cursor.last_observed_uid_next = identity.uid_next
            cursor.last_scan_started_at = now
            cursor.last_error_at = None
            cursor.last_error_code = None
            cursor.last_error_message = None
            session.commit()
            return CursorState(
                cursor_id=cursor.email_folder_cursor_id,
                first_uid=int(cursor.last_committed_uid) + 1,
                generation_changed=generation_changed,
            )

    def record_headers(
        self,
        identity: FolderIdentity,
        headers: Iterable[MessageHeader],
    ) -> dict[int, int]:
        by_uid = {header.uid: header for header in headers}
        if not by_uid:
            return {}
        with self.session_factory() as session:
            cursor = self._folder_cursor(session, identity)
            existing = {
                occurrence.message_uid: occurrence
                for occurrence in session.scalars(
                    select(EmailMessageOccurrence).where(
                        EmailMessageOccurrence.email_folder_cursor_id
                        == cursor.email_folder_cursor_id,
                        EmailMessageOccurrence.uid_validity == int(identity.uid_validity),
                        EmailMessageOccurrence.message_uid.in_(sorted(by_uid)),
                    )
                )
            }
            for uid, header in by_uid.items():
                occurrence = existing.get(uid)
                if occurrence is None:
                    occurrence = EmailMessageOccurrence(
                        email_folder_cursor_id=cursor.email_folder_cursor_id,
                        uid_validity=int(identity.uid_validity),
                        message_uid=uid,
                    )
                    session.add(occurrence)
                    existing[uid] = occurrence
                occurrence.rfc_message_id = header.message_id or None
                occurrence.sender_address = header.sender_email or None
                occurrence.subject = header.subject or None
                occurrence.sent_at = header.sent_at
            session.flush()
            result = {
                uid: occurrence.email_message_occurrence_id
                for uid, occurrence in existing.items()
            }
            session.commit()
            return result

    def existing_message_uids(self, identity: FolderIdentity) -> set[int]:
        with self.session_factory() as session:
            cursor = self._folder_cursor(session, identity)
            return set(
                session.scalars(
                    select(EmailMessageOccurrence.message_uid).where(
                        EmailMessageOccurrence.email_folder_cursor_id
                        == cursor.email_folder_cursor_id,
                        EmailMessageOccurrence.uid_validity
                        == int(identity.uid_validity),
                    )
                )
            )

    def due_message_headers(self, identity: FolderIdentity) -> list[MessageHeader]:
        """Load durable headers whose message acquisition still needs work."""
        now = _utcnow()
        with self.session_factory() as session:
            cursor = self._folder_cursor(session, identity)
            records = session.scalars(
                select(EmailMessageOccurrence)
                .where(
                    EmailMessageOccurrence.email_folder_cursor_id
                    == cursor.email_folder_cursor_id,
                    EmailMessageOccurrence.uid_validity
                    == int(identity.uid_validity),
                    or_(
                        EmailMessageOccurrence.acquisition_status == "discovered",
                        and_(
                            EmailMessageOccurrence.acquisition_status == "retryable",
                            or_(
                                EmailMessageOccurrence.next_retry_at.is_(None),
                                EmailMessageOccurrence.next_retry_at <= now,
                            ),
                        ),
                    ),
                )
                .order_by(EmailMessageOccurrence.message_uid)
            )
            return [
                MessageHeader(
                    uid=int(record.message_uid),
                    message_id=record.rfc_message_id or "",
                    sent_at=record.sent_at,
                    subject=record.subject or "",
                    sender_email=(record.sender_address or "").strip().lower(),
                )
                for record in records
            ]

    def reconcile_message_history_start(
        self,
        identity: FolderIdentity,
        history_start_date: date,
    ) -> tuple[int, int]:
        """Reopen or exclude durable headers when the configured scope changes."""
        with self.session_factory() as session:
            cursor = self._folder_cursor(session, identity)
            records = list(
                session.scalars(
                    select(EmailMessageOccurrence).where(
                        EmailMessageOccurrence.email_folder_cursor_id
                        == cursor.email_folder_cursor_id,
                        EmailMessageOccurrence.uid_validity
                        == int(identity.uid_validity),
                        or_(
                            EmailMessageOccurrence.acquisition_status.in_(
                                ("discovered", "retryable")
                            ),
                            and_(
                                EmailMessageOccurrence.acquisition_status == "ignored",
                                EmailMessageOccurrence.last_error_code
                                == HISTORY_START_MESSAGE_REASON_CODE,
                            ),
                        ),
                    )
                )
            )
            reopened = 0
            excluded = 0
            for record in records:
                in_scope = (
                    record.sent_at is None
                    or record.sent_at.date() >= history_start_date
                )
                if (
                    in_scope
                    and record.acquisition_status == "ignored"
                    and record.last_error_code == HISTORY_START_MESSAGE_REASON_CODE
                ):
                    record.acquisition_status = "discovered"
                    record.next_retry_at = None
                    record.last_error_code = None
                    record.last_error_message = None
                    reopened += 1
                elif not in_scope and record.acquisition_status != "ignored":
                    record.acquisition_status = "ignored"
                    record.next_retry_at = None
                    record.last_error_code = HISTORY_START_MESSAGE_REASON_CODE
                    record.last_error_message = history_start_message_reason(
                        history_start_date
                    )
                    excluded += 1
            session.commit()
            return reopened, excluded

    def mark_messages_ignored(
        self,
        identity: FolderIdentity,
        uids: Iterable[int],
        *,
        reason_code: str | None = None,
        reason_message: str | None = None,
    ) -> None:
        normalized = sorted(set(int(uid) for uid in uids))
        if not normalized:
            return
        with self.session_factory() as session:
            cursor = self._folder_cursor(session, identity)
            records = session.scalars(
                select(EmailMessageOccurrence).where(
                    EmailMessageOccurrence.email_folder_cursor_id
                    == cursor.email_folder_cursor_id,
                    EmailMessageOccurrence.uid_validity == int(identity.uid_validity),
                    EmailMessageOccurrence.message_uid.in_(normalized),
                )
            )
            for record in records:
                record.acquisition_status = "ignored"
                record.next_retry_at = None
                record.last_error_code = reason_code[:64] if reason_code else None
                record.last_error_message = (
                    reason_message[:4000] if reason_message else None
                )
            session.commit()

    def mark_messages_expunged(
        self,
        identity: FolderIdentity,
        uids: Iterable[int],
    ) -> None:
        """Terminally record messages confirmed absent before durable materialization."""
        normalized = sorted(set(int(uid) for uid in uids))
        if not normalized:
            return
        with self.session_factory() as session:
            cursor = self._folder_cursor(session, identity)
            records = {
                record.message_uid: record
                for record in session.scalars(
                    select(EmailMessageOccurrence).where(
                        EmailMessageOccurrence.email_folder_cursor_id
                        == cursor.email_folder_cursor_id,
                        EmailMessageOccurrence.uid_validity
                        == int(identity.uid_validity),
                        EmailMessageOccurrence.message_uid.in_(normalized),
                    )
                )
            }
            for uid in normalized:
                record = records.get(uid)
                if record is None:
                    record = EmailMessageOccurrence(
                        email_folder_cursor_id=cursor.email_folder_cursor_id,
                        uid_validity=int(identity.uid_validity),
                        message_uid=uid,
                        acquisition_status="dead_letter",
                    )
                    session.add(record)
                elif record.acquisition_status == "materialized":
                    continue
                record.acquisition_status = "dead_letter"
                record.next_retry_at = None
                record.last_error_code = "MessageExpunged"
                record.last_error_message = (
                    "IMAP message was expunged before durable materialization."
                )
            session.commit()

    def materialize_message(
        self,
        *,
        identity: FolderIdentity,
        header: MessageHeader,
        raw_message: bytes,
        attachments: Iterable[AttachmentPayload],
    ) -> list[ArtifactWork]:
        message_sha256 = hashlib.sha256(raw_message).hexdigest()
        with self.session_factory() as session:
            cursor = self._folder_cursor(session, identity)
            occurrence = session.scalar(
                select(EmailMessageOccurrence)
                .where(
                    EmailMessageOccurrence.email_folder_cursor_id
                    == cursor.email_folder_cursor_id,
                    EmailMessageOccurrence.uid_validity == int(identity.uid_validity),
                    EmailMessageOccurrence.message_uid == header.uid,
                )
                .with_for_update()
            )
            if occurrence is None:
                raise RuntimeError(f"Email occurrence {header.uid} was not discovered first.")
            if (
                occurrence.message_sha256 is not None
                and occurrence.message_sha256 != message_sha256
            ):
                raise RuntimeError(
                    "An immutable IMAP UID occurrence changed RFC822 content."
                )
            occurrence.acquisition_attempt_count += 1
            occurrence.rfc_message_id = header.message_id or None
            occurrence.message_sha256 = message_sha256
            occurrence.sender_address = header.sender_email or None
            occurrence.subject = header.subject or None
            occurrence.sent_at = header.sent_at
            occurrence.rfc822_size = len(raw_message)
            work: list[ArtifactWork] = []
            for attachment in attachments:
                content_sha256 = hashlib.sha256(attachment.payload).hexdigest()
                artifact = session.scalar(
                    select(EmailAttachmentArtifact).where(
                        EmailAttachmentArtifact.content_sha256 == content_sha256
                    )
                )
                if artifact is None:
                    artifact = EmailAttachmentArtifact(
                        content_sha256=content_sha256,
                        payload=attachment.payload,
                        byte_size=len(attachment.payload),
                        media_type=attachment.media_type or None,
                    )
                    session.add(artifact)
                    session.flush()
                link = session.scalar(
                    select(EmailMessageAttachment).where(
                        EmailMessageAttachment.email_message_occurrence_id
                        == occurrence.email_message_occurrence_id,
                        EmailMessageAttachment.part_index == attachment.part_id,
                    )
                )
                if link is None:
                    link = EmailMessageAttachment(
                        email_message_occurrence_id=occurrence.email_message_occurrence_id,
                        email_attachment_artifact_id=artifact.email_attachment_artifact_id,
                        part_index=attachment.part_id,
                        file_name=attachment.filename,
                    )
                    session.add(link)
                    session.flush()
                elif (
                    link.email_attachment_artifact_id
                    != artifact.email_attachment_artifact_id
                ):
                    raise RuntimeError(
                        "An immutable IMAP attachment part changed content."
                    )
                work.append(
                    ArtifactWork(
                        artifact_id=artifact.email_attachment_artifact_id,
                        message_attachment_id=link.email_message_attachment_id,
                        part_id=attachment.part_id,
                        filename=attachment.filename,
                        media_type=attachment.media_type,
                        content_sha256=content_sha256,
                    )
                )
            occurrence.acquisition_status = "materialized"
            occurrence.materialized_at = _utcnow()
            occurrence.next_retry_at = None
            occurrence.last_error_code = None
            occurrence.last_error_message = None
            session.commit()
            return work

    def mark_message_failed(
        self,
        *,
        identity: FolderIdentity,
        uid: int,
        error: Exception,
    ) -> None:
        with self.session_factory() as session:
            cursor = self._folder_cursor(session, identity)
            occurrence = session.scalar(
                select(EmailMessageOccurrence)
                .where(
                    EmailMessageOccurrence.email_folder_cursor_id
                    == cursor.email_folder_cursor_id,
                    EmailMessageOccurrence.uid_validity == int(identity.uid_validity),
                    EmailMessageOccurrence.message_uid == uid,
                )
                .with_for_update()
            )
            if occurrence is None:
                return
            if occurrence.acquisition_status != "materialized":
                occurrence.acquisition_attempt_count += 1
            dead_letter = occurrence.acquisition_attempt_count >= MAX_PARSE_ATTEMPTS
            occurrence.acquisition_status = "dead_letter" if dead_letter else "retryable"
            occurrence.next_retry_at = (
                None
                if dead_letter
                else _utcnow() + timedelta(minutes=2 ** occurrence.acquisition_attempt_count)
            )
            occurrence.last_error_code = type(error).__name__[:64]
            occurrence.last_error_message = str(error)[:4000]
            session.commit()

    def mark_message_attachment_rejections(
        self,
        *,
        identity: FolderIdentity,
        uid: int,
        reasons: Iterable[str],
    ) -> None:
        normalized_reasons = [
            str(reason).strip()
            for reason in reasons
            if str(reason).strip()
        ]
        if not normalized_reasons:
            return
        with self.session_factory() as session:
            cursor = self._folder_cursor(session, identity)
            occurrence = session.scalar(
                select(EmailMessageOccurrence)
                .where(
                    EmailMessageOccurrence.email_folder_cursor_id
                    == cursor.email_folder_cursor_id,
                    EmailMessageOccurrence.uid_validity == int(identity.uid_validity),
                    EmailMessageOccurrence.message_uid == uid,
                )
                .with_for_update()
            )
            if occurrence is None:
                return
            occurrence.acquisition_status = "dead_letter"
            occurrence.next_retry_at = None
            occurrence.last_error_code = "AttachmentRejected"
            occurrence.last_error_message = "; ".join(normalized_reasons)[:4000]
            session.commit()

    def prepare_parse(
        self,
        *,
        artifact_id: int,
        parser_profile: str,
        source_format: str,
        metadata: dict[str, object] | None = None,
    ) -> ParseWork | None:
        now = _utcnow()
        parser_version = parser_version_for_profile(parser_profile)
        with self.session_factory() as session:
            artifact = session.get(EmailAttachmentArtifact, artifact_id)
            if artifact is None:
                return None
            parse = session.scalar(
                select(EmailAttachmentParse)
                .where(
                    EmailAttachmentParse.email_attachment_artifact_id == artifact_id,
                    EmailAttachmentParse.parser_profile == parser_profile,
                    EmailAttachmentParse.parser_version == parser_version,
                    EmailAttachmentParse.source_format == source_format,
                )
                .with_for_update()
            )
            if parse is not None and parse.status == "succeeded":
                return ParseWork(
                    parse_id=parse.email_attachment_parse_id,
                    artifact_id=artifact_id,
                    parser_profile=parser_profile,
                    source_format=source_format,
                    disposition="cached",
                    lease_token=None,
                    payload=b"",
                    metadata=dict(parse.parser_metadata_json or {}),
                )
            if parse is not None and parse.status in ("dead_letter", "unsupported"):
                return ParseWork(
                    parse_id=parse.email_attachment_parse_id,
                    artifact_id=artifact_id,
                    parser_profile=parser_profile,
                    source_format=source_format,
                    disposition="terminal",
                    lease_token=None,
                    payload=b"",
                    metadata=dict(parse.parser_metadata_json or {}),
                )
            if (
                parse is not None
                and parse.status == "processing"
                and (
                    parse.lease_expires_at is None
                    or _is_after(parse.lease_expires_at, now)
                )
            ):
                return ParseWork(
                    parse_id=parse.email_attachment_parse_id,
                    artifact_id=artifact_id,
                    parser_profile=parser_profile,
                    source_format=source_format,
                    disposition="pending",
                    lease_token=None,
                    payload=b"",
                    metadata=dict(parse.parser_metadata_json or {}),
                )
            if parse is not None and _is_after(parse.next_retry_at, now):
                return ParseWork(
                    parse_id=parse.email_attachment_parse_id,
                    artifact_id=artifact_id,
                    parser_profile=parser_profile,
                    source_format=source_format,
                    disposition="pending",
                    lease_token=None,
                    payload=b"",
                    metadata=dict(parse.parser_metadata_json or {}),
                )
            if parse is None:
                parse = EmailAttachmentParse(
                    email_attachment_artifact_id=artifact_id,
                    parser_profile=parser_profile,
                    parser_version=parser_version,
                    source_format=source_format,
                )
                session.add(parse)
                session.flush()
            parse.status = "processing"
            parse.attempt_count += 1
            parse.started_at = now
            lease_token = uuid4().hex
            parse.lease_token = lease_token
            parse.lease_expires_at = now + timedelta(minutes=5)
            parse.parser_metadata_json = dict(metadata or {})
            session.commit()
            return ParseWork(
                parse_id=parse.email_attachment_parse_id,
                artifact_id=artifact_id,
                parser_profile=parser_profile,
                source_format=source_format,
                disposition="leased",
                lease_token=lease_token,
                payload=bytes(artifact.payload),
                metadata=dict(parse.parser_metadata_json or {}),
            )

    def prepare_parser_upgrades(
        self,
        *,
        parser_profile: str,
        prior_statuses: tuple[str, ...] = ("succeeded",),
    ) -> list[int]:
        """Create current-version jobs from immutable artifacts and contexts.

        Parser upgrades never download mail again and never mutate an older parse.
        The current parse receives occurrence-specific routing contexts so it can
        be routed independently before the older candidate routes are retired.
        """
        current_version = parser_version_for_profile(parser_profile)
        with self.session_factory() as session:
            prior_parses = list(
                session.scalars(
                    select(EmailAttachmentParse)
                    .where(
                        EmailAttachmentParse.parser_profile == parser_profile,
                        EmailAttachmentParse.parser_version != current_version,
                        EmailAttachmentParse.status.in_(prior_statuses),
                        EmailAttachmentParse.route_contexts.any(),
                    )
                    .order_by(EmailAttachmentParse.email_attachment_parse_id)
                )
            )
            current_parse_ids: set[int] = set()
            for prior_parse in prior_parses:
                requires_action = False
                current_parse = session.scalar(
                    select(EmailAttachmentParse).where(
                        EmailAttachmentParse.email_attachment_artifact_id
                        == prior_parse.email_attachment_artifact_id,
                        EmailAttachmentParse.parser_profile == parser_profile,
                        EmailAttachmentParse.parser_version == current_version,
                        EmailAttachmentParse.source_format == prior_parse.source_format,
                    )
                )
                if current_parse is None:
                    requires_action = True
                    metadata = dict(prior_parse.parser_metadata_json or {})
                    metadata["upgraded_from_parser_version"] = (
                        prior_parse.parser_version
                    )
                    current_parse = EmailAttachmentParse(
                        email_attachment_artifact_id=(
                            prior_parse.email_attachment_artifact_id
                        ),
                        parser_profile=parser_profile,
                        parser_version=current_version,
                        source_format=prior_parse.source_format,
                        status="pending",
                        parser_metadata_json=_json_value(metadata),
                    )
                    session.add(current_parse)
                    session.flush()
                elif current_parse.status in (
                    "pending",
                    "processing",
                    "retryable",
                ):
                    requires_action = True
                existing_attachment_ids = set(
                    session.scalars(
                        select(
                            EmailAttachmentRouteContext.email_message_attachment_id
                        ).where(
                            EmailAttachmentRouteContext.email_attachment_parse_id
                            == current_parse.email_attachment_parse_id
                        )
                    )
                )
                for prior_context in session.scalars(
                    select(EmailAttachmentRouteContext).where(
                        EmailAttachmentRouteContext.email_attachment_parse_id
                        == prior_parse.email_attachment_parse_id
                    )
                ):
                    if (
                        prior_context.email_message_attachment_id
                        in existing_attachment_ids
                    ):
                        continue
                    requires_action = True
                    session.add(
                        EmailAttachmentRouteContext(
                            email_message_attachment_id=(
                                prior_context.email_message_attachment_id
                            ),
                            email_attachment_parse_id=(
                                current_parse.email_attachment_parse_id
                            ),
                            routing_context_json=_json_value(
                                list(prior_context.routing_context_json or [])
                            ),
                        )
                    )
                    existing_attachment_ids.add(
                        prior_context.email_message_attachment_id
                    )
                if current_parse.status == "succeeded":
                    unsuperseded_route_id = session.scalar(
                        select(
                            EmailNavCandidateRoute.email_nav_candidate_route_id
                        )
                        .join(
                            EmailNavCandidate,
                            EmailNavCandidate.email_nav_candidate_id
                            == EmailNavCandidateRoute.email_nav_candidate_id,
                        )
                        .join(
                            EmailAttachmentRouteContext,
                            EmailAttachmentRouteContext.email_attachment_route_context_id
                            == EmailNavCandidateRoute.email_attachment_route_context_id,
                        )
                        .where(
                            EmailNavCandidate.email_attachment_parse_id
                            == prior_parse.email_attachment_parse_id,
                            EmailAttachmentRouteContext.email_attachment_parse_id
                            == prior_parse.email_attachment_parse_id,
                            or_(
                                EmailNavCandidateRoute.validation_status
                                != "rejected",
                                EmailNavCandidateRoute.rejection_reason.is_(None),
                                ~EmailNavCandidateRoute.rejection_reason.like(
                                    "Superseded by parser %"
                                ),
                            ),
                        )
                        .limit(1)
                    )
                    requires_action = requires_action or (
                        unsuperseded_route_id is not None
                    )
                if requires_action:
                    current_parse_ids.add(
                        current_parse.email_attachment_parse_id
                    )
            session.commit()
            return sorted(current_parse_ids)

    def supersede_prior_parse_routes(self, *, parse_id: int) -> set[str]:
        """Retire older routes only after their replacement parse succeeded.

        ``imported_at`` is retained as audit evidence that an older route once
        reached publication. Returned instrument ids must be rebuilt from the
        remaining current evidence so a corrected parser can also remove data.
        """
        with self.session_factory() as session:
            current_parse = session.get(EmailAttachmentParse, parse_id)
            if (
                current_parse is None
                or current_parse.status != "succeeded"
                or current_parse.parser_version
                != parser_version_for_profile(current_parse.parser_profile)
            ):
                return set()
            current_attachment_ids = list(
                session.scalars(
                    select(
                        EmailAttachmentRouteContext.email_message_attachment_id
                    ).where(
                        EmailAttachmentRouteContext.email_attachment_parse_id
                        == parse_id
                    )
                )
            )
            if not current_attachment_ids:
                return set()

            prior_routes = list(
                session.scalars(
                    select(EmailNavCandidateRoute)
                    .join(
                        EmailNavCandidate,
                        EmailNavCandidate.email_nav_candidate_id
                        == EmailNavCandidateRoute.email_nav_candidate_id,
                    )
                    .join(
                        EmailAttachmentParse,
                        EmailAttachmentParse.email_attachment_parse_id
                        == EmailNavCandidate.email_attachment_parse_id,
                    )
                    .join(
                        EmailAttachmentRouteContext,
                        EmailAttachmentRouteContext.email_attachment_route_context_id
                        == EmailNavCandidateRoute.email_attachment_route_context_id,
                    )
                    .where(
                        EmailAttachmentParse.email_attachment_artifact_id
                        == current_parse.email_attachment_artifact_id,
                        EmailAttachmentParse.parser_profile
                        == current_parse.parser_profile,
                        EmailAttachmentParse.source_format
                        == current_parse.source_format,
                        EmailAttachmentParse.parser_version
                        != current_parse.parser_version,
                        EmailAttachmentRouteContext.email_attachment_parse_id
                        == EmailAttachmentParse.email_attachment_parse_id,
                        EmailAttachmentRouteContext.email_message_attachment_id.in_(
                            current_attachment_ids
                        ),
                        or_(
                            EmailNavCandidateRoute.validation_status != "rejected",
                            EmailNavCandidateRoute.rejection_reason.is_(None),
                            ~EmailNavCandidateRoute.rejection_reason.like(
                                "Superseded by parser %"
                            ),
                        ),
                    )
                    .with_for_update()
                )
            )
            affected_instrument_ids = {
                str(route.instrument_id)
                for route in prior_routes
                if route.routing_status == "matched"
                and route.validation_status == "valid"
                and route.instrument_id
            }
            for route in prior_routes:
                prior_target = str(route.instrument_id or "").strip()
                route.routing_status = "unmatched"
                route.validation_status = "rejected"
                route.instrument_id = None
                route.routing_rule_fingerprint = None
                route.rejection_reason = (
                    "Superseded by parser "
                    f"{current_parse.parser_profile}@{current_parse.parser_version}"
                    + (f"; prior target={prior_target}." if prior_target else ".")
                )
            session.commit()
            return affected_instrument_ids

    def record_unsupported_attachment(
        self,
        *,
        artifact_id: int,
        source_format: str,
        metadata: dict[str, object],
        reason: str,
    ) -> None:
        """Persist an unparsed candidate attachment so coverage gaps stay visible."""
        normalized_format = (source_format.strip().lower() or "unknown")[:16]
        with self.session_factory() as session:
            parse = session.scalar(
                select(EmailAttachmentParse)
                .where(
                    EmailAttachmentParse.email_attachment_artifact_id == artifact_id,
                    EmailAttachmentParse.parser_profile == "unsupported_attachment",
                    EmailAttachmentParse.parser_version == PARSER_VERSION,
                    EmailAttachmentParse.source_format == normalized_format,
                )
                .with_for_update()
            )
            if parse is None:
                parse = EmailAttachmentParse(
                    email_attachment_artifact_id=artifact_id,
                    parser_profile="unsupported_attachment",
                    parser_version=PARSER_VERSION,
                    source_format=normalized_format,
                )
                session.add(parse)
            parse.status = "unsupported"
            parse.parser_metadata_json = _json_value(metadata)
            parse.next_retry_at = None
            parse.lease_token = None
            parse.lease_expires_at = None
            parse.last_error_code = "UnsupportedAttachmentType"
            parse.last_error_message = reason[:4000]
            session.commit()

    def due_parse_work(self, *, limit: int = 100) -> list[ParseWork]:
        now = _utcnow()
        with self.session_factory() as session:
            parses = list(
                session.scalars(
                    select(EmailAttachmentParse)
                    .where(
                        (
                            EmailAttachmentParse.status.in_(("pending", "retryable"))
                            & (
                                EmailAttachmentParse.next_retry_at.is_(None)
                                | (EmailAttachmentParse.next_retry_at <= now)
                            )
                        )
                        | (
                            (EmailAttachmentParse.status == "processing")
                            & (
                                EmailAttachmentParse.lease_expires_at.is_(None)
                                | (EmailAttachmentParse.lease_expires_at <= now)
                            )
                        )
                    )
                    .order_by(EmailAttachmentParse.email_attachment_parse_id)
                    .limit(max(1, limit))
                    .with_for_update(skip_locked=True)
                )
            )
            work: list[ParseWork] = []
            for parse in parses:
                artifact = session.get(
                    EmailAttachmentArtifact,
                    parse.email_attachment_artifact_id,
                )
                if artifact is None:
                    continue
                parse.status = "processing"
                parse.attempt_count += 1
                parse.started_at = now
                lease_token = uuid4().hex
                parse.lease_token = lease_token
                parse.lease_expires_at = now + timedelta(minutes=5)
                work.append(
                    ParseWork(
                        parse_id=parse.email_attachment_parse_id,
                        artifact_id=artifact.email_attachment_artifact_id,
                        parser_profile=parse.parser_profile,
                        source_format=parse.source_format,
                        disposition="leased",
                        lease_token=lease_token,
                        payload=bytes(artifact.payload),
                        metadata=dict(parse.parser_metadata_json or {}),
                    )
                )
            session.commit()
            return work

    def complete_parse(
        self,
        *,
        parse_id: int,
        lease_token: str,
        rows: list[dict[str, object]],
    ) -> list[tuple[int, dict[str, object]]]:
        now = _utcnow()
        with self.session_factory() as session:
            parse = session.scalar(
                select(EmailAttachmentParse)
                .where(
                    EmailAttachmentParse.email_attachment_parse_id == parse_id,
                    EmailAttachmentParse.status == "processing",
                    EmailAttachmentParse.lease_token == lease_token,
                )
                .options(selectinload(EmailAttachmentParse.nav_candidates))
                .with_for_update()
            )
            if parse is None:
                raise RuntimeError(
                    f"Email attachment parse {parse_id} lease is no longer active."
                )
            for candidate in list(parse.nav_candidates):
                session.delete(candidate)
            session.flush()
            result: list[tuple[int, dict[str, object]]] = []
            for row_ordinal, row in enumerate(rows):
                if "cumulative_nav" in row:
                    raise ValueError(
                        "NAV parser emitted removed key 'cumulative_nav'; use "
                        "'cash_cumulative_nav'."
                    )
                candidate = EmailNavCandidate(
                    email_attachment_parse_id=parse_id,
                    row_ordinal=row_ordinal,
                    instrument_code=str(row.get("instrument_code") or "").strip() or None,
                    instrument_name=str(row.get("instrument_name") or "").strip() or None,
                    normalized_instrument_code=(
                        str(row.get("instrument_code") or "").strip().upper() or None
                    ),
                    normalized_instrument_name=(
                        normalize_text_token(row.get("instrument_name")) or None
                    ),
                    as_of_date=_coerce_date(row.get("as_of_date")),
                    unit_nav_value=_optional_text(row.get("nav")),
                    reported_cash_cumulative_nav_value=_optional_text(
                        row.get("cash_cumulative_nav")
                    ),
                    reported_reinvested_nav_value=_optional_text(
                        row.get("nav_with_dividend")
                    ),
                    reported_frequency=_optional_text(row.get("frequency")),
                    raw_row_json=_json_value(row),
                )
                session.add(candidate)
                session.flush()
                result.append((candidate.email_nav_candidate_id, _candidate_as_row(candidate)))
            parse.status = "succeeded" if result else "unsupported"
            parse.completed_at = now
            parse.next_retry_at = None
            parse.lease_token = None
            parse.lease_expires_at = None
            parse.last_error_code = None if result else "NoNavRows"
            parse.last_error_message = (
                None
                if result
                else "Attachment parser produced no NAV observations."
            )
            session.commit()
            return result

    def register_route_context(
        self,
        *,
        parse_id: int,
        message_attachment_id: int,
        routing_contexts: Iterable[dict[str, object]],
    ) -> int:
        normalized_contexts = [
            dict(context)
            for context in routing_contexts
            if isinstance(context, dict)
        ]
        with self.session_factory() as session:
            context = session.scalar(
                select(EmailAttachmentRouteContext)
                .where(
                    EmailAttachmentRouteContext.email_message_attachment_id
                    == message_attachment_id,
                    EmailAttachmentRouteContext.email_attachment_parse_id == parse_id,
                )
                .with_for_update()
            )
            if context is None:
                context = EmailAttachmentRouteContext(
                    email_message_attachment_id=message_attachment_id,
                    email_attachment_parse_id=parse_id,
                    routing_context_json=_json_value(normalized_contexts),
                )
                session.add(context)
                session.flush()
            elif list(context.routing_context_json or []) != normalized_contexts:
                context.routing_context_json = _json_value(normalized_contexts)
                for route in session.scalars(
                    select(EmailNavCandidateRoute).where(
                        EmailNavCandidateRoute.email_attachment_route_context_id
                        == context.email_attachment_route_context_id
                    )
                ):
                    route.routing_status = "pending"
                    route.validation_status = "pending"
                    route.instrument_id = None
                    route.routing_rule_fingerprint = None
                    route.rejection_reason = None
                    route.imported_at = None
            context_id = context.email_attachment_route_context_id
            session.commit()
            return context_id

    def pending_candidate_routes(self, parse_id: int) -> list[CandidateRouteWork]:
        """Materialize and lease-free route work for every occurrence of a parse."""
        with self.session_factory() as session:
            contexts = list(
                session.scalars(
                    select(EmailAttachmentRouteContext).where(
                        EmailAttachmentRouteContext.email_attachment_parse_id == parse_id
                    )
                )
            )
            candidates = list(
                session.scalars(
                    select(EmailNavCandidate).where(
                        EmailNavCandidate.email_attachment_parse_id == parse_id
                    )
                )
            )
            if not contexts or not candidates:
                return []
            context_ids = [item.email_attachment_route_context_id for item in contexts]
            parsed_candidate_ids = [
                item.email_nav_candidate_id for item in candidates
            ]
            existing = {
                (
                    route.email_nav_candidate_id,
                    route.email_attachment_route_context_id,
                )
                for route in session.scalars(
                    select(EmailNavCandidateRoute).where(
                        EmailNavCandidateRoute.email_nav_candidate_id.in_(
                            parsed_candidate_ids
                        ),
                        EmailNavCandidateRoute.email_attachment_route_context_id.in_(
                            context_ids
                        ),
                    )
                )
            }
            for context in contexts:
                for candidate in candidates:
                    key = (
                        candidate.email_nav_candidate_id,
                        context.email_attachment_route_context_id,
                    )
                    if key in existing:
                        continue
                    session.add(
                        EmailNavCandidateRoute(
                            email_nav_candidate_id=key[0],
                            email_attachment_route_context_id=key[1],
                        )
                    )
            session.flush()
            statement = (
                select(
                    EmailNavCandidateRoute,
                    EmailNavCandidate,
                    EmailAttachmentRouteContext,
                    EmailMessageAttachment,
                    EmailMessageOccurrence,
                    EmailFolderCursor,
                )
                .join(
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
                .where(
                    EmailAttachmentRouteContext.email_attachment_parse_id == parse_id,
                    EmailNavCandidateRoute.routing_status == "pending",
                )
                .order_by(EmailNavCandidateRoute.email_nav_candidate_route_id)
            )
            work = [
                CandidateRouteWork(
                    route_id=route.email_nav_candidate_route_id,
                    parsed_candidate_id=candidate.email_nav_candidate_id,
                    instrument_id=route.instrument_id,
                    row=_candidate_as_row(candidate),
                    routing_contexts=tuple(
                        dict(item)
                        for item in list(context.routing_context_json or [])
                        if isinstance(item, dict)
                    ),
                    folder_name=folder.folder_name,
                    message_uid=int(occurrence.message_uid),
                    sent_at=occurrence.sent_at,
                    attachment_name=attachment.file_name or "attachment",
                )
                for route, candidate, context, attachment, occurrence, folder in session.execute(
                    statement
                ).all()
            ]
            session.commit()
            return work

    def parse_ids_requiring_routing(self, *, limit: int = 1000) -> list[int]:
        """Find successful parses left between durable parse and route commits."""
        current_version_predicates = [
            and_(
                EmailAttachmentParse.parser_profile == profile,
                EmailAttachmentParse.parser_version == version,
            )
            for profile, version in PARSER_PROFILE_VERSIONS.items()
        ]
        current_version_predicates.append(
            and_(
                ~EmailAttachmentParse.parser_profile.in_(
                    tuple(PARSER_PROFILE_VERSIONS)
                ),
                EmailAttachmentParse.parser_version == PARSER_VERSION,
            )
        )
        with self.session_factory() as session:
            statement = (
                select(EmailAttachmentParse.email_attachment_parse_id)
                .join(
                    EmailNavCandidate,
                    EmailNavCandidate.email_attachment_parse_id
                    == EmailAttachmentParse.email_attachment_parse_id,
                )
                .join(
                    EmailAttachmentRouteContext,
                    EmailAttachmentRouteContext.email_attachment_parse_id
                    == EmailAttachmentParse.email_attachment_parse_id,
                )
                .outerjoin(
                    EmailNavCandidateRoute,
                    and_(
                        EmailNavCandidateRoute.email_nav_candidate_id
                        == EmailNavCandidate.email_nav_candidate_id,
                        EmailNavCandidateRoute.email_attachment_route_context_id
                        == EmailAttachmentRouteContext.email_attachment_route_context_id,
                    ),
                )
                .where(
                    EmailAttachmentParse.status == "succeeded",
                    or_(*current_version_predicates),
                    or_(
                        EmailNavCandidateRoute.email_nav_candidate_route_id.is_(None),
                        EmailNavCandidateRoute.routing_status == "pending",
                    ),
                )
                .distinct()
                .order_by(EmailAttachmentParse.email_attachment_parse_id)
                .limit(max(1, int(limit)))
            )
            return [int(parse_id) for parse_id in session.scalars(statement)]

    def requeue_unmatched_exact_identities(
        self,
        *,
        normalized_codes: Iterable[str],
        normalized_names: Iterable[str],
    ) -> int:
        """Reconcile durable unmatched evidence after Registry identities change.

        Rejected rows are intentionally excluded: registration must never turn an
        invalid date or NAV value into publishable evidence. Ambiguous rows also
        stay in the review queue until their Registry conflict is resolved.
        """
        codes = sorted(
            {
                str(value).strip()
                for value in normalized_codes
                if str(value).strip()
            }
        )
        names = sorted(
            {
                str(value).strip()
                for value in normalized_names
                if str(value).strip()
            }
        )
        identity_predicates = []
        if codes:
            identity_predicates.append(
                EmailNavCandidate.normalized_instrument_code.in_(codes)
            )
        if names:
            identity_predicates.append(
                EmailNavCandidate.normalized_instrument_name.in_(names)
            )
        if not identity_predicates:
            return 0

        candidate_ids = select(EmailNavCandidate.email_nav_candidate_id).where(
            or_(*identity_predicates)
        )
        with self.session_factory() as session:
            result = session.execute(
                update(EmailNavCandidateRoute)
                .where(
                    EmailNavCandidateRoute.routing_status == "unmatched",
                    EmailNavCandidateRoute.validation_status == "pending",
                    EmailNavCandidateRoute.email_nav_candidate_id.in_(candidate_ids),
                )
                .values(
                    routing_status="pending",
                    instrument_id=None,
                    routing_rule_fingerprint=None,
                    rejection_reason=None,
                    imported_at=None,
                )
            )
            session.commit()
            return int(result.rowcount or 0)

    def reconcile_candidate_route_history_start(
        self,
        history_start_date: date,
    ) -> tuple[int, int]:
        """Reopen widened scope and reject unimported NAV evidence before it."""
        candidate_ids = select(EmailNavCandidate.email_nav_candidate_id).where(
            EmailNavCandidate.as_of_date < history_start_date
        )
        reopened_candidate_ids = select(
            EmailNavCandidate.email_nav_candidate_id
        ).where(EmailNavCandidate.as_of_date >= history_start_date)
        with self.session_factory() as session:
            reopened_result = session.execute(
                update(EmailNavCandidateRoute)
                .where(
                    EmailNavCandidateRoute.imported_at.is_(None),
                    EmailNavCandidateRoute.validation_status == "rejected",
                    EmailNavCandidateRoute.rejection_reason.like(
                        f"{HISTORY_START_NAV_REASON_PREFIX}%"
                    ),
                    EmailNavCandidateRoute.email_nav_candidate_id.in_(
                        reopened_candidate_ids
                    ),
                )
                .values(
                    routing_status="pending",
                    validation_status="pending",
                    instrument_id=None,
                    routing_rule_fingerprint=None,
                    rejection_reason=None,
                )
            )
            excluded_result = session.execute(
                update(EmailNavCandidateRoute)
                .where(
                    EmailNavCandidateRoute.imported_at.is_(None),
                    EmailNavCandidateRoute.validation_status != "rejected",
                    EmailNavCandidateRoute.email_nav_candidate_id.in_(candidate_ids),
                )
                .values(
                    routing_status="unmatched",
                    validation_status="rejected",
                    instrument_id=None,
                    routing_rule_fingerprint=None,
                    rejection_reason=history_start_nav_reason(history_start_date),
                )
            )
            session.commit()
            return (
                int(reopened_result.rowcount or 0),
                int(excluded_result.rowcount or 0),
            )

    def unimported_matched_routes(self) -> list[CandidateRouteWork]:
        """Durable outbox of exact-routed observations awaiting publication."""
        with self.session_factory() as session:
            statement = (
                select(
                    EmailNavCandidateRoute,
                    EmailNavCandidate,
                    EmailAttachmentRouteContext,
                    EmailMessageAttachment,
                    EmailMessageOccurrence,
                    EmailFolderCursor,
                )
                .join(
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
                .where(
                    EmailNavCandidateRoute.routing_status == "matched",
                    EmailNavCandidateRoute.validation_status == "valid",
                    EmailNavCandidateRoute.imported_at.is_(None),
                    EmailNavCandidateRoute.instrument_id.is_not(None),
                )
                .order_by(EmailNavCandidateRoute.email_nav_candidate_route_id)
            )
            return [
                CandidateRouteWork(
                    route_id=route.email_nav_candidate_route_id,
                    parsed_candidate_id=candidate.email_nav_candidate_id,
                    instrument_id=route.instrument_id,
                    row=_candidate_as_row(candidate),
                    routing_contexts=tuple(
                        dict(item)
                        for item in list(context.routing_context_json or [])
                        if isinstance(item, dict)
                    ),
                    folder_name=folder.folder_name,
                    message_uid=int(occurrence.message_uid),
                    sent_at=occurrence.sent_at,
                    attachment_name=attachment.file_name or "attachment",
                )
                for route, candidate, context, attachment, occurrence, folder in session.execute(
                    statement
                ).all()
            ]

    def fail_parse(
        self,
        *,
        parse_id: int,
        lease_token: str,
        error: Exception,
    ) -> bool:
        with self.session_factory() as session:
            parse = session.scalar(
                select(EmailAttachmentParse)
                .where(
                    EmailAttachmentParse.email_attachment_parse_id == parse_id,
                    EmailAttachmentParse.status == "processing",
                    EmailAttachmentParse.lease_token == lease_token,
                )
                .with_for_update()
            )
            if parse is None:
                return False
            dead_letter = parse.attempt_count >= MAX_PARSE_ATTEMPTS
            parse.status = "dead_letter" if dead_letter else "retryable"
            parse.next_retry_at = (
                None
                if dead_letter
                else _utcnow() + timedelta(minutes=2 ** parse.attempt_count)
            )
            parse.lease_token = None
            parse.lease_expires_at = None
            parse.last_error_code = type(error).__name__[:64]
            parse.last_error_message = str(error)[:4000]
            session.commit()
            return True

    def route_candidate(
        self,
        *,
        route_id: int,
        status: str,
        instrument_id: str | None,
        rule_fingerprint: str | None,
        reason: str | None = None,
        validation_status: str | None = None,
    ) -> None:
        self.route_candidates(
            [
                CandidateRouteDecision(
                    route_id=route_id,
                    status=status,
                    instrument_id=instrument_id,
                    rule_fingerprint=rule_fingerprint,
                    reason=reason,
                    validation_status=validation_status,
                )
            ]
        )

    def route_candidates(
        self,
        decisions: Iterable[CandidateRouteDecision],
    ) -> None:
        """Commit one parse's routing decisions atomically in one transaction."""
        normalized = list(decisions)
        if not normalized:
            return
        route_ids = [int(decision.route_id) for decision in normalized]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("Duplicate email candidate route decision.")
        for decision in normalized:
            if decision.status == "matched" and not str(
                decision.instrument_id or ""
            ).strip():
                raise ValueError("A matched email candidate route requires an instrument.")

        with self.session_factory() as session:
            routes = {
                route.email_nav_candidate_route_id: route
                for route in session.scalars(
                    select(EmailNavCandidateRoute).where(
                        EmailNavCandidateRoute.email_nav_candidate_route_id.in_(route_ids)
                    )
                )
            }
            missing = sorted(set(route_ids) - set(routes))
            if missing:
                raise RuntimeError(
                    f"Email candidate routes disappeared before commit: {missing}."
                )
            for decision in normalized:
                route = routes[decision.route_id]
                route.routing_status = decision.status
                route.instrument_id = (
                    decision.instrument_id
                    if decision.status == "matched"
                    else None
                )
                route.routing_rule_fingerprint = decision.rule_fingerprint
                route.validation_status = decision.validation_status or (
                    "valid" if decision.status == "matched" else "pending"
                )
                route.rejection_reason = decision.reason
            session.commit()

    def mark_imported(self, route_ids: Iterable[int]) -> None:
        """Mark occurrence-specific route ids published by the canonical writer."""
        normalized = sorted(set(int(route_id) for route_id in route_ids))
        if not normalized:
            return
        with self.session_factory() as session:
            for route in session.scalars(
                select(EmailNavCandidateRoute).where(
                    EmailNavCandidateRoute.email_nav_candidate_route_id.in_(normalized)
                )
            ):
                route.imported_at = _utcnow()
            session.commit()

    def matched_nav_history(self, instrument_id: str) -> list[dict[str, object]]:
        """Return all exact-routed raw evidence for deterministic chain rebuilds."""
        with self.session_factory() as session:
            statement = (
                select(
                    EmailNavCandidateRoute,
                    EmailNavCandidate,
                    EmailAttachmentRouteContext,
                    EmailMessageAttachment,
                    EmailMessageOccurrence,
                    EmailFolderCursor,
                )
                .join(
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
                .where(
                    EmailNavCandidateRoute.instrument_id == instrument_id,
                    EmailNavCandidateRoute.routing_status == "matched",
                    EmailNavCandidateRoute.validation_status == "valid",
                    EmailNavCandidate.as_of_date.is_not(None),
                )
                .order_by(
                    EmailNavCandidate.as_of_date,
                    EmailMessageOccurrence.sent_at,
                    EmailMessageOccurrence.message_uid,
                    EmailNavCandidateRoute.email_nav_candidate_route_id,
                )
            )
            return [
                {
                    **_candidate_as_row(candidate),
                    "_email_candidate_route_id": route.email_nav_candidate_route_id,
                    "_email_parsed_candidate_id": candidate.email_nav_candidate_id,
                    "_email_folder": folder.folder_name,
                    "_email_uid": int(occurrence.message_uid),
                    "_email_sent_at": _datetime_text(occurrence.sent_at),
                    "_email_attachment_name": attachment.file_name or "attachment",
                }
                for route, candidate, context, attachment, occurrence, folder in session.execute(
                    statement
                ).all()
            ]

    def finish_folder_scan(
        self,
        identity: FolderIdentity,
        *,
        lease_token: str,
        committed_uid: int,
    ) -> None:
        if committed_uid < 0 or committed_uid >= identity.uid_next:
            raise ValueError("Committed IMAP UID must be below the selected UIDNEXT.")
        with self.session_factory() as session:
            self._require_mailbox_lease(
                session,
                mailbox_key=identity.mailbox_key,
                lease_token=lease_token,
                now=_utcnow(),
            )
            cursor = self._folder_cursor(session, identity, for_update=True)
            if cursor.uid_validity != int(identity.uid_validity):
                raise StaleEmailIngestionLeaseError(
                    "Email folder generation changed before cursor commit."
                )
            cursor.last_committed_uid = max(cursor.last_committed_uid, committed_uid)
            cursor.last_observed_uid_next = identity.uid_next
            cursor.last_scan_succeeded_at = _utcnow()
            cursor.last_error_at = None
            cursor.last_error_code = None
            cursor.last_error_message = None
            session.commit()

    def fail_folder_scan(
        self,
        identity: FolderIdentity,
        *,
        lease_token: str,
        error: Exception,
    ) -> bool:
        with self.session_factory() as session:
            try:
                self._require_mailbox_lease(
                    session,
                    mailbox_key=identity.mailbox_key,
                    lease_token=lease_token,
                    now=_utcnow(),
                )
            except StaleEmailIngestionLeaseError:
                session.rollback()
                return False
            cursor = self._folder_cursor(session, identity, for_update=True)
            if cursor.uid_validity != int(identity.uid_validity):
                session.rollback()
                return False
            cursor.last_error_at = _utcnow()
            cursor.last_error_code = type(error).__name__[:64]
            cursor.last_error_message = str(error)[:4000]
            session.commit()
            return True

    @staticmethod
    def _require_mailbox_lease(
        session: Session,
        *,
        mailbox_key: str,
        lease_token: str,
        now: datetime,
    ) -> EmailMailboxIngestionLease:
        lease = session.scalar(
            select(EmailMailboxIngestionLease)
            .where(EmailMailboxIngestionLease.mailbox_key == mailbox_key)
            .with_for_update()
        )
        if (
            lease is None
            or lease.lease_token != lease_token
            or not _is_after(lease.lease_expires_at, now)
        ):
            raise StaleEmailIngestionLeaseError(
                "Email NAV ingestion mailbox lease expired or was superseded."
            )
        return lease

    @staticmethod
    def _folder_cursor(
        session: Session,
        identity: FolderIdentity,
        *,
        for_update: bool = False,
    ) -> EmailFolderCursor:
        statement = select(EmailFolderCursor).where(
            EmailFolderCursor.mailbox_key == identity.mailbox_key,
            EmailFolderCursor.folder_name == identity.folder_name,
        )
        if for_update:
            statement = statement.with_for_update()
        cursor = session.scalar(statement)
        if cursor is None:
            raise RuntimeError(
                f'Email folder cursor for "{identity.folder_name}" was not initialized.'
            )
        return cursor


def _coerce_date(value: object):
    from datetime import date

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _datetime_text(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is not None:
        return value.astimezone(UTC).isoformat()
    return value.isoformat()
