from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from platform_app.api.routes import dashboard
from platform_app.db.base import Base
from platform_app.db.email_models import (
    EmailAttachmentArtifact,
    EmailAttachmentParse,
    EmailAttachmentRouteContext,
    EmailFolderCursor,
    EmailMessageAttachment,
    EmailMessageOccurrence,
    EmailNavCandidate,
    EmailNavCandidateRoute,
)
from platform_app.main import app
from platform_app.services.email_ingestion.monitoring import build_email_nav_inventory


def _session_factory(tmp_path: Path) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'monitoring.db'}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _folder(
    session: Session,
    name: str,
    *,
    committed_uid: int,
    uid_next: int,
    error: str | None = None,
) -> EmailFolderCursor:
    cursor = EmailFolderCursor(
        mailbox_key=f"secret-account-key-{name}",
        folder_name=name,
        uid_validity=101,
        last_committed_uid=committed_uid,
        last_observed_uid_next=uid_next,
        last_scan_started_at=datetime(2026, 7, 16, 1, tzinfo=UTC),
        last_scan_succeeded_at=(
            None if error else datetime(2026, 7, 16, 1, 2, tzinfo=UTC)
        ),
        last_error_at=(datetime(2026, 7, 16, 1, 3, tzinfo=UTC) if error else None),
        last_error_code=("FolderScanError" if error else None),
        last_error_message=error,
    )
    session.add(cursor)
    session.flush()
    return cursor


def _route(
    session: Session,
    *,
    folder: EmailFolderCursor,
    uid: int,
    code: str | None,
    name: str | None,
    nav_date: date | None,
    routing_status: str,
    validation_status: str,
    reason: str | None,
    imported: bool = False,
    attachment_name: str | None = None,
) -> EmailNavCandidateRoute:
    occurrence = EmailMessageOccurrence(
        email_folder_cursor_id=folder.email_folder_cursor_id,
        uid_validity=101,
        message_uid=uid,
        acquisition_status="materialized",
        acquisition_attempt_count=1,
        sender_address="private@example.com",
        subject="sensitive subject that must not be exposed",
    )
    artifact = EmailAttachmentArtifact(
        content_sha256=f"{uid:064x}",
        payload=f"secret attachment payload {uid}".encode(),
        byte_size=len(f"secret attachment payload {uid}".encode()),
    )
    session.add_all([occurrence, artifact])
    session.flush()
    attachment = EmailMessageAttachment(
        email_message_occurrence_id=occurrence.email_message_occurrence_id,
        email_attachment_artifact_id=artifact.email_attachment_artifact_id,
        part_index="1",
        file_name=attachment_name or f"nav-{uid}.xlsx",
    )
    parse = EmailAttachmentParse(
        email_attachment_artifact_id=artifact.email_attachment_artifact_id,
        parser_profile="fund_nav",
        parser_version="test-v1",
        source_format="xlsx",
        status="succeeded",
        attempt_count=1,
    )
    session.add_all([attachment, parse])
    session.flush()
    candidate = EmailNavCandidate(
        email_attachment_parse_id=parse.email_attachment_parse_id,
        row_ordinal=0,
        instrument_code=code,
        instrument_name=name,
        normalized_instrument_code=code.upper() if code else None,
        normalized_instrument_name=name.casefold() if name else None,
        as_of_date=nav_date,
        unit_nav_value="1.23",
    )
    context = EmailAttachmentRouteContext(
        email_message_attachment_id=attachment.email_message_attachment_id,
        email_attachment_parse_id=parse.email_attachment_parse_id,
        routing_context_json=[],
    )
    session.add_all([candidate, context])
    session.flush()
    route = EmailNavCandidateRoute(
        email_nav_candidate_id=candidate.email_nav_candidate_id,
        email_attachment_route_context_id=context.email_attachment_route_context_id,
        routing_status=routing_status,
        validation_status=validation_status,
        instrument_id="matched-fund" if routing_status == "matched" else None,
        rejection_reason=reason,
        imported_at=(datetime(2026, 7, 16, 2, tzinfo=UTC) if imported else None),
    )
    session.add(route)
    session.flush()
    return route


def _parse_failure(
    session: Session,
    *,
    folder: EmailFolderCursor,
    uid: int,
    status: str,
) -> None:
    occurrence = EmailMessageOccurrence(
        email_folder_cursor_id=folder.email_folder_cursor_id,
        uid_validity=101,
        message_uid=uid,
        acquisition_status="materialized",
        acquisition_attempt_count=1,
    )
    payload = f"failed parse {uid}".encode()
    artifact = EmailAttachmentArtifact(
        content_sha256=f"{uid:064x}",
        payload=payload,
        byte_size=len(payload),
    )
    session.add_all([occurrence, artifact])
    session.flush()
    attachment = EmailMessageAttachment(
        email_message_occurrence_id=occurrence.email_message_occurrence_id,
        email_attachment_artifact_id=artifact.email_attachment_artifact_id,
        part_index="1",
        file_name=f"failed-{uid}.csv",
    )
    parse = EmailAttachmentParse(
        email_attachment_artifact_id=artifact.email_attachment_artifact_id,
        parser_profile="fund_nav",
        parser_version="test-v1",
        source_format="csv",
        status=status,
        attempt_count=3,
        last_error_code="ParseError",
        last_error_message=f"could not parse attachment {uid}",
    )
    session.add_all([attachment, parse])


def test_email_nav_inventory_is_bounded_aggregated_and_excludes_sensitive_data(
    tmp_path: Path,
) -> None:
    _, factory = _session_factory(tmp_path)
    with factory() as session:
        inbox = _folder(
            session,
            "INBOX",
            committed_uid=10,
            uid_next=15,
            error="temporary folder scan failure password=hunter2 ops@example.com",
        )
        archive = _folder(session, "云谷3号", committed_uid=20, uid_next=21)
        _route(
            session,
            folder=inbox,
            uid=1,
            code="alpha-1",
            name="Alpha Fund",
            nav_date=date(2026, 7, 1),
            routing_status="unmatched",
            validation_status="pending",
            reason="no exact Registry identity",
        )
        _route(
            session,
            folder=archive,
            uid=2,
            code="ALPHA-1",
            name="Alpha Fund",
            nav_date=date(2026, 7, 8),
            routing_status="ambiguous",
            validation_status="pending",
            reason="multiple exact identities",
            attachment_name="alpha-latest.xlsx",
        )
        _route(
            session,
            folder=inbox,
            uid=3,
            code="bad-1",
            name="Invalid Fund",
            nav_date=None,
            routing_status="unmatched",
            validation_status="rejected",
            reason="NAV candidate lacks a valid ISO date or NAV value.",
        )
        _route(
            session,
            folder=inbox,
            uid=4,
            code="matched-1",
            name="Matched Fund",
            nav_date=date(2026, 7, 10),
            routing_status="matched",
            validation_status="valid",
            reason=None,
        )
        retryable_message = EmailMessageOccurrence(
            email_folder_cursor_id=inbox.email_folder_cursor_id,
            uid_validity=101,
            message_uid=30,
            acquisition_status="retryable",
            acquisition_attempt_count=2,
            last_error_code="FetchError",
            last_error_message="temporary fetch failure",
        )
        dead_message = EmailMessageOccurrence(
            email_folder_cursor_id=inbox.email_folder_cursor_id,
            uid_validity=101,
            message_uid=31,
            acquisition_status="dead_letter",
            acquisition_attempt_count=5,
            last_error_code="FetchError",
            last_error_message="permanent fetch failure",
        )
        session.add_all([retryable_message, dead_message])
        _parse_failure(session, folder=archive, uid=40, status="retryable")
        _parse_failure(session, folder=archive, uid=41, status="dead_letter")
        _parse_failure(session, folder=archive, uid=42, status="unsupported")
        session.commit()

    result = build_email_nav_inventory(factory, limit=1)

    assert result["limit"] == 1
    assert result["automatic_fund_creation"] is False
    assert result["counts"] == {
        "candidate_routes": 4,
        "matched_unimported": 1,
        "unresolved_identity_count": 2,
        "unresolved_occurrence_count": 3,
        "folder_count": 2,
        "total_uid_lag": 4,
        "folders_with_lag": 1,
        "folders_with_error": 1,
        "message_retryable": 1,
        "message_dead_letter": 1,
        "parse_retryable": 1,
        "parse_dead_letter": 1,
        "parse_unsupported": 1,
    }
    assert result["routing_statuses"] == {
        "ambiguous": 1,
        "matched": 1,
        "unmatched": 2,
    }
    assert len(result["unresolved_identities"]) == 1
    alpha = result["unresolved_identities"][0]
    assert alpha["instrument_code"] == "alpha-1"
    assert alpha["instrument_name"] == "Alpha Fund"
    assert alpha["first_nav_date"] == "2026-07-01"
    assert alpha["latest_nav_date"] == "2026-07-08"
    assert alpha["occurrence_count"] == 2
    assert alpha["routing_statuses"] == {
        "unmatched": 1,
        "ambiguous": 1,
        "rejected": 0,
    }
    assert alpha["example"] == {
        "folder_name": "云谷3号",
        "attachment_name": "alpha-latest.xlsx",
        "reason": "multiple exact identities",
    }
    assert len(result["folders"]) == 1
    assert result["folders"][0]["folder_name"] == "INBOX"
    assert result["folders"][0]["uid_lag"] == 4
    assert len(result["message_failures"]) == 1
    assert result["message_failures"][0]["status"] == "dead_letter"
    assert len(result["parse_failures"]) == 1
    assert result["parse_failures"][0]["status"] == "dead_letter"

    serialized = repr(result)
    assert "secret-account-key" not in serialized
    assert "private@example.com" not in serialized
    assert "sensitive subject" not in serialized
    assert "secret attachment payload" not in serialized
    assert "hunter2" not in serialized
    assert "ops@example.com" not in serialized
    assert "password=[redacted]" in serialized
    assert "[redacted-email]" in serialized


def test_email_nav_inventory_query_count_does_not_grow_per_identity(tmp_path: Path) -> None:
    engine, factory = _session_factory(tmp_path)
    with factory() as session:
        folder = _folder(session, "INBOX", committed_uid=1, uid_next=2)
        _route(
            session,
            folder=folder,
            uid=1,
            code="fund-1",
            name="Fund 1",
            nav_date=date(2026, 7, 1),
            routing_status="unmatched",
            validation_status="pending",
            reason="unmatched",
        )
        session.commit()

    calls = 0

    def count_query(*args: object, **kwargs: object) -> None:
        nonlocal calls
        del args, kwargs
        calls += 1

    event.listen(engine, "before_cursor_execute", count_query)
    build_email_nav_inventory(factory, limit=100)
    one_identity_calls = calls

    with factory() as session:
        folder = session.scalar(select(EmailFolderCursor))
        assert folder is not None
        for uid in range(2, 12):
            _route(
                session,
                folder=folder,
                uid=uid,
                code=f"fund-{uid}",
                name=f"Fund {uid}",
                nav_date=date(2026, 7, uid),
                routing_status="unmatched",
                validation_status="pending",
                reason="unmatched",
            )
        session.commit()

    calls = 0
    build_email_nav_inventory(factory, limit=100)
    assert calls == one_identity_calls


def test_email_nav_inventory_endpoint_enforces_limit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: list[int] = []

    def fake_inventory(session_factory, *, limit: int):  # type: ignore[no-untyped-def]
        del session_factory
        captured.append(limit)
        return {"limit": limit, "counts": {}}

    monkeypatch.setattr(dashboard, "get_session_factory", lambda: object())
    monkeypatch.setattr(dashboard, "build_email_nav_inventory", fake_inventory)
    client = TestClient(app)

    assert client.get("/api/dashboard/email-nav-inventory?limit=500").json() == {
        "limit": 500,
        "counts": {},
    }
    assert captured == [500]
    assert client.get("/api/dashboard/email-nav-inventory?limit=501").status_code == 422
