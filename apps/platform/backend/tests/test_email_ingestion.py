from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import imaplib
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from platform_app.db.base import Base
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
from platform_app.services.email_ingestion import pipeline
from platform_app.services.email_ingestion.imap_client import (
    ImapClient,
    ImapClientError,
    ImapFolderError,
)
from platform_app.services.email_ingestion.repository import (
    HISTORY_START_MESSAGE_REASON_CODE,
    MAX_PARSE_ATTEMPTS,
    EmailIngestionAlreadyRunningError,
    EmailIngestionRepository,
    StaleEmailIngestionLeaseError,
    history_start_message_reason,
)
from platform_app.services.email_ingestion.types import (
    AttachmentPayload,
    FolderIdentity,
    MessageHeader,
)


@pytest.fixture
def email_session_factory(
    tmp_path: Path,
) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'email-ingestion.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _settings(*folders: str, attachment_max_bytes: int = 1024 * 1024) -> Any:
    return SimpleNamespace(
        email_sync_enabled=True,
        email_sync_ready=True,
        email_imap_folders=list(folders),
        email_history_start_date=date(2025, 12, 26),
        email_header_fetch_batch_size=2,
        email_message_fetch_batch_size=2,
        email_attachment_max_bytes=attachment_max_bytes,
        email_ingestion_lease_seconds=1800,
    )


def _fund(
    instrument_id: str,
    name: str,
    *,
    code: str | None = None,
    rules: list[dict[str, object]] | None = None,
    source: str = "manual",
) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "instrument_name": name,
        "instrument_type": "fund",
        "currency": "CNY",
        "identifiers": (
            [{"identifier_type": "manager_code", "identifier_value": code}]
            if code
            else []
        ),
        "source_settings": {
            "source": source,
            "source_email_rules": list(rules or []),
        },
        "lifecycle_state": {"status": "active"},
    }


def _header(
    uid: int,
    *,
    subject: str = "净值更新",
    sender: str = "nav@example.com",
) -> MessageHeader:
    return MessageHeader(
        uid=uid,
        message_id=f"<message-{uid}@example.com>",
        sent_at=datetime(2026, 7, 15, 9, uid % 60, tzinfo=UTC),
        subject=subject,
        sender_email=sender,
    )


def _message(*attachments: tuple[str, bytes]) -> bytes:
    message = EmailMessage()
    message["From"] = "nav@example.com"
    message["To"] = "ops@example.com"
    message["Subject"] = "净值更新"
    message.set_content("NAV attachments")
    for filename, payload in attachments:
        message.add_attachment(
            payload,
            maintype="application",
            subtype="octet-stream",
            filename=filename,
        )
    return message.as_bytes()


def _fake_pipeline_client(
    folders: dict[str, dict[str, object]],
    calls: dict[str, list[object]],
):
    class FakeImapClient:
        def __init__(self, settings: Any) -> None:
            self.settings = settings
            self.selected_folder = ""

        @property
        def mailbox_key(self) -> str:
            return "mailbox-key"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            del exc_type, exc, traceback

        def select_folder(self, folder_name: str) -> FolderIdentity:
            calls.setdefault("select", []).append(folder_name)
            spec = folders.get(folder_name)
            if spec is None or spec.get("select_error"):
                raise ImapFolderError(f'Configured IMAP folder "{folder_name}" does not exist.')
            self.selected_folder = folder_name
            headers = dict(spec.get("headers", {}))
            uid_next = int(spec.get("uid_next") or (max(headers, default=0) + 1))
            return FolderIdentity(
                mailbox_key="mailbox-key",
                folder_name=folder_name,
                uid_validity=str(spec.get("uid_validity") or 100),
                uid_next=uid_next,
            )

        def discover_uids(
            self,
            *,
            first_uid: int,
            uid_next: int,
            full_history: bool,
            history_start_date: date,
        ) -> list[int]:
            calls.setdefault("discover", []).append(
                (
                    self.selected_folder,
                    first_uid,
                    uid_next,
                    full_history,
                    history_start_date,
                )
            )
            headers = dict(folders[self.selected_folder].get("headers", {}))
            available_uids = list(
                folders[self.selected_folder].get("discovered_uids", headers)
            )
            if full_history or first_uid <= 1:
                return sorted(
                    uid
                    for uid in available_uids
                    if uid not in headers
                    or headers[uid].sent_at is None
                    or headers[uid].sent_at.date() >= history_start_date
                )
            return sorted(
                uid for uid in available_uids if first_uid <= uid < uid_next
            )

        def fetch_headers(
            self,
            uids: list[int],
            *,
            chunk_size: int,
            on_batch=None,
            allow_missing: bool = False,
        ) -> list[MessageHeader]:
            calls.setdefault("headers", []).append(
                (self.selected_folder, tuple(uids), chunk_size)
            )
            headers = dict(folders[self.selected_folder].get("headers", {}))
            missing = [uid for uid in uids if uid not in headers]
            if missing and not allow_missing:
                raise ImapClientError(
                    "IMAP FETCH omitted UIDs: "
                    + ", ".join(str(uid) for uid in missing)
                )
            result = [headers[uid] for uid in uids if uid in headers]
            if on_batch is not None:
                on_batch()
            return result

        def fetch_messages(
            self,
            uids: list[int],
            *,
            chunk_size: int,
            on_batch=None,
            allow_missing: bool = False,
        ) -> dict[int, bytes]:
            calls.setdefault("messages", []).append(
                (self.selected_folder, tuple(uids), chunk_size)
            )
            configured_failure = int(
                folders[self.selected_folder].get("message_fetch_error_on_call", 0)
                or 0
            )
            folder_fetch_count = sum(
                1
                for folder_name, _, _ in calls["messages"]
                if folder_name == self.selected_folder
            )
            if configured_failure and folder_fetch_count == configured_failure:
                raise ImapClientError("injected message batch failure")
            messages = dict(folders[self.selected_folder].get("messages", {}))
            missing = [uid for uid in uids if uid not in messages]
            if missing and not allow_missing:
                raise ImapClientError(
                    "IMAP FETCH omitted UIDs: "
                    + ", ".join(str(uid) for uid in missing)
                )
            result = {uid: messages[uid] for uid in uids if uid in messages}
            if on_batch is not None:
                on_batch()
            return result

        extract_attachments = staticmethod(ImapClient.extract_attachments)

    return FakeImapClient


def _run_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_factory: sessionmaker[Session],
    settings: Any,
    folders: dict[str, dict[str, object]],
    instruments: list[dict[str, object]],
    parse_attachment,
    calls: dict[str, list[object]] | None = None,
    full_history: bool = False,
):
    captured_calls = calls if calls is not None else {}
    monkeypatch.setattr(pipeline, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        pipeline,
        "ImapClient",
        _fake_pipeline_client(folders, captured_calls),
    )
    result = pipeline.ingest_email_nav(
        settings=settings,
        instruments=instruments,
        parse_attachment=parse_attachment,
        extract_attachment_text=lambda filename, payload: payload.decode(
            "utf-8", errors="ignore"
        ),
        full_history=full_history,
    )
    return result, captured_calls


def test_configured_folders_and_select_are_exact_without_inbox_fallback() -> None:
    settings = SimpleNamespace(
        email_imap_folders=["云谷3号"],
        email_imap_host="imap.example.com",
        email_imap_username="ops@example.com",
    )
    assert pipeline._configured_folders(settings) == ["云谷3号"]

    class Mailbox:
        select_calls: list[bytes] = []

        def select(self, folder: bytes, readonly: bool):
            self.select_calls.append(folder)
            return "OK", [b"0"]

    client = ImapClient(settings)
    client._mailbox = Mailbox()  # type: ignore[assignment]
    client._listed_folders = {"INBOX": b"INBOX"}
    with pytest.raises(ImapFolderError, match="does not exist"):
        client.select_folder("云谷3号")
    assert client._mailbox.select_calls == []  # type: ignore[union-attr]


def test_uidvalidity_generation_reset_preserves_old_occurrence_audit(
    email_session_factory: sessionmaker[Session],
) -> None:
    repository = EmailIngestionRepository(email_session_factory)
    generation_one = FolderIdentity("mailbox", "INBOX", "101", 10)
    lease_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox",
        lease_seconds=60,
    )
    state = repository.start_folder_scan(generation_one, lease_token=lease_token)
    assert state.first_uid == 1
    assert state.generation_changed is False
    repository.record_headers(generation_one, [_header(3)])
    repository.finish_folder_scan(
        generation_one,
        lease_token=lease_token,
        committed_uid=9,
    )
    assert repository.start_folder_scan(
        generation_one,
        lease_token=lease_token,
    ).first_uid == 10

    generation_two = FolderIdentity("mailbox", "INBOX", "202", 4)
    reset = repository.start_folder_scan(generation_two, lease_token=lease_token)
    assert reset.first_uid == 1
    assert reset.generation_changed is True

    with email_session_factory() as session:
        cursor = session.scalar(select(EmailFolderCursor))
        assert cursor is not None
        assert cursor.uid_validity == 202
        assert cursor.last_committed_uid == 0
        occurrences = list(session.scalars(select(EmailMessageOccurrence)))
        assert [(item.uid_validity, item.message_uid) for item in occurrences] == [
            (101, 3)
        ]


def test_mailbox_lease_rejects_overlap_recovers_after_expiry_and_fences_old_run(
    email_session_factory: sessionmaker[Session],
) -> None:
    repository = EmailIngestionRepository(email_session_factory)
    identity = FolderIdentity("mailbox", "INBOX", "101", 10)
    first_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox",
        lease_seconds=60,
    )
    repository.start_folder_scan(identity, lease_token=first_token)

    with pytest.raises(EmailIngestionAlreadyRunningError):
        repository.acquire_mailbox_lease(
            mailbox_key="mailbox",
            lease_seconds=60,
        )

    with email_session_factory() as session:
        lease = session.get(EmailMailboxIngestionLease, "mailbox")
        assert lease is not None
        lease.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            seconds=1
        )
        session.commit()

    replacement_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox",
        lease_seconds=60,
    )
    replacement_identity = FolderIdentity("mailbox", "INBOX", "202", 4)
    repository.start_folder_scan(
        replacement_identity,
        lease_token=replacement_token,
    )
    with pytest.raises(StaleEmailIngestionLeaseError):
        repository.finish_folder_scan(
            identity,
            lease_token=first_token,
            committed_uid=9,
        )

    with email_session_factory() as session:
        cursor = session.scalar(select(EmailFolderCursor))
        assert cursor is not None
        assert cursor.uid_validity == 202
        assert cursor.last_committed_uid == 0

    repository.finish_folder_scan(
        replacement_identity,
        lease_token=replacement_token,
        committed_uid=3,
    )
    assert repository.release_mailbox_lease(
        mailbox_key="mailbox",
        lease_token=first_token,
    ) is False
    assert repository.release_mailbox_lease(
        mailbox_key="mailbox",
        lease_token=replacement_token,
    ) is True


def test_busy_mailbox_fails_before_any_imap_folder_scan(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    repository = EmailIngestionRepository(email_session_factory)
    lease_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_seconds=60,
    )
    calls: dict[str, list[object]] = {}
    monkeypatch.setattr(pipeline, "get_session_factory", lambda: email_session_factory)
    monkeypatch.setattr(
        pipeline,
        "ImapClient",
        _fake_pipeline_client({"INBOX": {}}, calls),
    )

    with pytest.raises(pipeline.EmailIngestionBusyError):
        pipeline.ingest_email_nav(
            settings=_settings("INBOX"),
            instruments=[],
            parse_attachment=lambda filename, payload, profile: [],
            extract_attachment_text=lambda filename, payload: "",
        )

    assert calls.get("select", []) == []
    repository.release_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_token=lease_token,
    )


def test_imap_fetch_batches_headers_messages_and_full_history_uses_start_date() -> None:
    class Mailbox:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def uid(self, *args: object):
            self.calls.append(args)
            if args[0] == "search":
                return "OK", [b"1 2 3"]
            uid_set = str(args[1])
            request = str(args[2])
            response = []
            uids: list[int] = []
            for uid_token in uid_set.split(","):
                if ":" in uid_token:
                    start, end = (int(value) for value in uid_token.split(":"))
                    uids.extend(range(start, end + 1))
                else:
                    uids.append(int(uid_token))
            for uid in uids:
                if "HEADER.FIELDS" in request:
                    payload = (
                        f"Message-ID: <{uid}@example.com>\r\n"
                        f"Date: Wed, 15 Jul 2026 09:0{uid}:00 +0800\r\n"
                        f"Subject: NAV {uid}\r\n"
                        "From: Sender <sender@example.com>\r\n\r\n"
                    ).encode()
                else:
                    payload = _message((f"{uid}.csv", f"row-{uid}".encode()))
                response.append((f"1 (UID {uid} BODY[] {{{len(payload)}}}".encode(), payload))
            return "OK", response

    settings = SimpleNamespace(
        email_imap_host="imap.example.com",
        email_imap_username="ops@example.com",
    )
    mailbox = Mailbox()
    client = ImapClient(settings)
    client._mailbox = mailbox  # type: ignore[assignment]
    client._selected_folder = "INBOX"
    client._selected_identity = FolderIdentity("mailbox", "INBOX", "1", 100)

    headers = client.fetch_headers(range(1, 6), chunk_size=2)
    messages = client.fetch_messages(range(1, 6), chunk_size=3)
    discovered = client.discover_uids(
        first_uid=99,
        uid_next=100,
        full_history=True,
        history_start_date=date(2025, 12, 26),
    )

    assert [header.uid for header in headers] == [1, 2, 3, 4, 5]
    assert sorted(messages) == [1, 2, 3, 4, 5]
    fetch_calls = [call for call in mailbox.calls if call[0] == "fetch"]
    assert [call[1] for call in fetch_calls] == ["1:2", "3:4", "5", "1:3", "4:5"]
    assert mailbox.calls[-1] == ("search", None, "SINCE", "26-Dec-2025")
    assert discovered == [1, 2, 3]


def test_imap_fetch_rejects_missing_duplicate_and_unexpected_uids() -> None:
    class Mailbox:
        def __init__(self, response: list[tuple[bytes, bytes]]) -> None:
            self.response = response

        def uid(self, *args: object):
            del args
            return "OK", self.response

    settings = SimpleNamespace(
        email_imap_host="imap.example.com",
        email_imap_username="ops@example.com",
    )

    def client_for(response: list[tuple[bytes, bytes]]) -> ImapClient:
        client = ImapClient(settings)
        client._mailbox = Mailbox(response)  # type: ignore[assignment]
        client._selected_folder = "INBOX"
        return client

    with pytest.raises(ImapClientError, match="omitted UIDs: 2"):
        client_for([(b"1 (UID 1 BODY[] {1}", b"a")]).fetch_messages(
            [1, 2], chunk_size=2
        )
    with pytest.raises(ImapClientError, match="duplicated UIDs: 1"):
        client_for(
            [(b"1 (UID 1 BODY[] {1}", b"a"), (b"2 (UID 1 BODY[] {1}", b"b")]
        ).fetch_messages([1], chunk_size=2)
    with pytest.raises(ImapClientError, match="unexpected UIDs: 2"):
        client_for(
            [(b"1 (UID 1 BODY[] {1}", b"a"), (b"2 (UID 2 BODY[] {1}", b"b")]
        ).fetch_messages([1], chunk_size=2)


def test_content_hash_parse_is_deduplicated_but_routes_are_occurrence_specific(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    shared_payload = b"same identity-less NAV table"
    folders = {
        "fund-a": {
            "headers": {1: _header(1, sender="a@example.com")},
            "messages": {1: _message(("nav.csv", shared_payload))},
        },
        "fund-b": {
            "headers": {2: _header(2, sender="b@example.com")},
            "messages": {2: _message(("nav.csv", shared_payload))},
        },
    }
    instruments = [
        _fund(
            "fund-a",
            "基金甲",
            rules=[{"sender_equals": ["a@example.com"]}],
            source="email",
        ),
        _fund(
            "fund-b",
            "基金乙",
            rules=[{"sender_equals": ["b@example.com"]}],
            source="email",
        ),
    ]
    parse_calls: list[tuple[str, str]] = []

    def parse_attachment(filename: str, payload: bytes, profile: str):
        del payload
        parse_calls.append((filename, profile))
        return [{"as_of_date": "2026-07-14", "nav": "1.2345"}]

    result, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("fund-a", "fund-b"),
        folders=folders,
        instruments=instruments,
        parse_attachment=parse_attachment,
    )

    assert parse_calls == [("nav.csv", "generic_nav_table")]
    assert set(result.batches) == {"fund-a", "fund-b"}
    assert result.batches["fund-a"].rows[0]["_email_folder"] == "fund-a"
    assert result.batches["fund-b"].rows[0]["_email_folder"] == "fund-b"
    assert (
        result.batches["fund-a"].rows[0]["_email_candidate_route_id"]
        != result.batches["fund-b"].rows[0]["_email_candidate_route_id"]
    )

    with email_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(EmailAttachmentArtifact)) == 1
        assert session.scalar(select(func.count()).select_from(EmailAttachmentParse)) == 1
        assert session.scalar(select(func.count()).select_from(EmailNavCandidate)) == 1
        assert session.scalar(select(func.count()).select_from(EmailMessageAttachment)) == 2
        assert session.scalar(select(func.count()).select_from(EmailAttachmentRouteContext)) == 2
        routes = list(
            session.scalars(
                select(EmailNavCandidateRoute).order_by(
                    EmailNavCandidateRoute.email_nav_candidate_route_id
                )
            )
        )
        assert [(route.instrument_id, route.routing_status) for route in routes] == [
            ("fund-a", "matched"),
            ("fund-b", "matched"),
        ]

    replay, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("fund-a", "fund-b"),
        folders=folders,
        instruments=instruments,
        parse_attachment=parse_attachment,
    )
    assert parse_calls == [("nav.csv", "generic_nav_table")]
    assert set(replay.batches) == {"fund-a", "fund-b"}

    repository = EmailIngestionRepository(email_session_factory)
    assert repository.matched_nav_history("fund-a")[0]["_email_folder"] == "fund-a"
    assert repository.matched_nav_history("fund-b")[0]["_email_folder"] == "fund-b"
    repository.mark_imported(
        [
            route_id
            for batch in replay.batches.values()
            for route_id in batch.route_ids
        ]
    )
    acknowledged, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("fund-a", "fund-b"),
        folders=folders,
        instruments=instruments,
        parse_attachment=parse_attachment,
    )
    assert acknowledged.batches == {}


def test_materialized_uid_occurrence_rejects_changed_rfc822_content(
    email_session_factory: sessionmaker[Session],
) -> None:
    repository = EmailIngestionRepository(email_session_factory)
    identity = FolderIdentity("mailbox", "INBOX", "1", 2)
    lease_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox",
        lease_seconds=60,
    )
    repository.start_folder_scan(identity, lease_token=lease_token)
    header = _header(1)
    repository.record_headers(identity, [header])
    repository.materialize_message(
        identity=identity,
        header=header,
        raw_message=_message(("nav.csv", b"first")),
        attachments=[],
    )

    with pytest.raises(RuntimeError, match="changed RFC822 content"):
        repository.materialize_message(
            identity=identity,
            header=header,
            raw_message=_message(("nav.csv", b"second")),
            attachments=[],
        )


def test_discovery_routes_exact_manual_watchlist_fund_without_email_rules(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {7: _header(7, subject="周度 NAV")},
            "messages": {7: _message(("watchlist.csv", b"watchlist"))},
        }
    }

    result, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[_fund("watch-fund", "观察基金", code="WATCH001")],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2026-07-11",
                "instrument_code": "WATCH001",
                "instrument_name": "观察基金",
                "nav": "0.998",
            }
        ],
    )

    assert list(result.batches) == ["watch-fund"]
    assert result.batches["watch-fund"].rows[0]["nav"] == "0.998"


def test_new_registry_fund_reconciles_previously_unmatched_durable_evidence(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {19: _header(19, subject="未注册基金净值更新")},
            "messages": {19: _message(("new-fund.csv", b"new fund"))},
            "uid_next": 20,
        }
    }
    parse_calls: list[str] = []

    def parse_attachment(filename: str, payload: bytes, profile: str):
        del payload, profile
        parse_calls.append(filename)
        return [
            {
                "as_of_date": "2026-07-11",
                "instrument_code": "NEW001",
                "instrument_name": "新注册基金",
                "nav": "1.008",
            },
            {
                "as_of_date": "not-a-date",
                "instrument_code": "NEW001",
                "instrument_name": "新注册基金",
                "nav": "1.009",
            },
        ]

    first, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[],
        parse_attachment=parse_attachment,
    )
    assert first.batches == {}
    assert first.unmatched_candidates == 1
    assert first.invalid_candidates == 1

    reconciled, calls = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[_fund("new-fund", "新注册基金", code="NEW001")],
        parse_attachment=parse_attachment,
    )

    assert parse_calls == ["new-fund.csv"]
    assert all(not uids for _, uids, _ in calls.get("messages", []))
    assert list(reconciled.batches) == ["new-fund"]
    assert reconciled.batches["new-fund"].rows[0]["nav"] == "1.008"
    with email_session_factory() as session:
        routes = list(
            session.scalars(
                select(EmailNavCandidateRoute).order_by(
                    EmailNavCandidateRoute.email_nav_candidate_route_id
                )
            )
        )
        assert [route.routing_status for route in routes] == ["matched", "unmatched"]
        assert [route.validation_status for route in routes] == ["valid", "rejected"]
        assert [route.instrument_id for route in routes] == ["new-fund", None]


def test_discovery_uses_active_fund_identity_in_plain_subject_without_relaxing_routing(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {17: _header(17, subject="WATCH001 2026-07-11 日报")},
            "messages": {17: _message(("watchlist.csv", b"watchlist"))},
        }
    }

    result, calls = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[_fund("watch-fund", "观察基金", code="WATCH001")],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2026-07-11",
                "instrument_code": "WATCH001",
                "nav": "0.998",
            }
        ],
    )

    assert calls["messages"] == [("INBOX", (17,), 2)]
    assert list(result.batches) == ["watch-fund"]


def test_negative_only_row_selector_cannot_override_an_unknown_row_identity(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {18: _header(18, sender="rules@example.com")},
            "messages": {18: _message(("nav.csv", b"identityless"))},
        }
    }
    result, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[
            _fund(
                "target",
                "目标基金",
                rules=[
                    {
                        "sender_equals": ["rules@example.com"],
                        "row_name_excludes": ["排除基金"],
                    }
                ],
            )
        ],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2026-07-11",
                "instrument_name": "其他基金",
                "nav": "1.001",
            }
        ],
    )

    assert result.batches == {}
    assert result.unmatched_candidates == 1


def test_ambiguous_unmatched_and_invalid_candidates_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {8: _header(8)},
            "messages": {8: _message(("candidates.csv", b"candidate rows"))},
        }
    }
    rows = [
        {"as_of_date": "2026-07-10", "instrument_code": "DUP", "nav": "1.0"},
        {"as_of_date": "2026-07-10", "instrument_code": "UNKNOWN", "nav": "1.0"},
        {"as_of_date": "July 10", "instrument_code": "VALID", "nav": "1.0"},
        {"as_of_date": "2026-07-10", "instrument_code": "VALID", "nav": "NaN"},
    ]
    result, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[
            _fund("dup-a", "重名甲", code="DUP"),
            _fund("dup-b", "重名乙", code="DUP"),
            _fund("valid", "有效基金", code="VALID"),
        ],
        parse_attachment=lambda filename, payload, profile: rows,
    )

    assert result.batches == {}
    assert result.ambiguous_candidates == 1
    assert result.unmatched_candidates == 1
    assert result.invalid_candidates == 2
    with email_session_factory() as session:
        routes = list(
            session.scalars(
                select(EmailNavCandidateRoute).order_by(
                    EmailNavCandidateRoute.email_nav_candidate_route_id
                )
            )
        )
        assert [route.routing_status for route in routes] == [
            "ambiguous",
            "unmatched",
            "unmatched",
            "unmatched",
        ]
        assert [route.validation_status for route in routes] == [
            "pending",
            "pending",
            "rejected",
            "rejected",
        ]


def test_nav_observations_before_history_start_date_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    result, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders={
            "INBOX": {
                "headers": {1: _header(1)},
                "messages": {1: _message(("history.csv", b"history"))},
            }
        },
        instruments=[_fund("bounded-fund", "范围基金", code="BOUNDED001")],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2025-12-25",
                "instrument_code": "BOUNDED001",
                "nav": "0.999",
            },
            {
                "as_of_date": "2025-12-26",
                "instrument_code": "BOUNDED001",
                "nav": "1.001",
            },
        ],
    )

    assert result.out_of_scope_candidates == 1
    assert [row["as_of_date"] for row in result.batches["bounded-fund"].rows] == [
        "2025-12-26"
    ]
    with email_session_factory() as session:
        routes = list(
            session.scalars(
                select(EmailNavCandidateRoute).order_by(
                    EmailNavCandidateRoute.email_nav_candidate_route_id
                )
            )
        )
        assert [route.routing_status for route in routes] == [
            "unmatched",
            "matched",
        ]
        assert [route.validation_status for route in routes] == [
            "rejected",
            "valid",
        ]
        assert routes[0].rejection_reason == (
            "NAV observation predates configured email history start date "
            "2025-12-26."
        )


def test_widened_history_start_reopens_a_scope_rejected_nav_route(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {1: _header(1)},
            "messages": {1: _message(("year-end.csv", b"year-end"))},
            "uid_next": 2,
        }
    }
    settings = _settings("INBOX")
    settings.email_history_start_date = date(2025, 12, 31)
    first, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=settings,
        folders=folders,
        instruments=[_fund("weekly-fund", "周频基金", code="WEEKLY001")],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2025-12-26",
                "instrument_code": "WEEKLY001",
                "nav": "1.001",
            }
        ],
    )

    assert first.batches == {}
    assert first.out_of_scope_candidates == 1

    settings.email_history_start_date = date(2025, 12, 26)
    second, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=settings,
        folders=folders,
        instruments=[_fund("weekly-fund", "周频基金", code="WEEKLY001")],
        parse_attachment=lambda filename, payload, profile: [],
    )

    assert [row["as_of_date"] for row in second.batches["weekly-fund"].rows] == [
        "2025-12-26"
    ]
    with email_session_factory() as session:
        route = session.scalar(select(EmailNavCandidateRoute))
        assert route is not None
        assert route.routing_status == "matched"
        assert route.validation_status == "valid"
        assert route.rejection_reason is None


def test_attachment_limit_skips_oversize_payload_before_storage_and_parse(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {9: _header(9)},
            "messages": {
                9: _message(
                    ("too-large.csv", b"x" * 64),
                    ("small.csv", b"small"),
                    ("unsupported.pdf", b"pdf"),
                )
            },
        }
    }
    parsed_files: list[str] = []

    def parse_attachment(filename: str, payload: bytes, profile: str):
        del payload, profile
        parsed_files.append(filename)
        return [
            {
                "as_of_date": "2026-07-14",
                "instrument_code": "LIMIT",
                "nav": "1.0",
            }
        ]

    result, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX", attachment_max_bytes=8),
        folders=folders,
        instruments=[_fund("limit", "限额基金", code="LIMIT")],
        parse_attachment=parse_attachment,
    )

    assert parsed_files == ["small.csv"]
    assert result.folders[0].stored_attachments == 2
    assert result.folders[0].failed_messages == 1
    with email_session_factory() as session:
        artifacts = list(
            session.scalars(
                select(EmailAttachmentArtifact).order_by(
                    EmailAttachmentArtifact.email_attachment_artifact_id
                )
            )
        )
        assert [artifact.byte_size for artifact in artifacts] == [5, 3]
        unsupported = session.scalar(
            select(EmailAttachmentParse).where(
                EmailAttachmentParse.status == "unsupported"
            )
        )
        assert unsupported is not None
        assert unsupported.source_format == "pdf"
        assert unsupported.last_error_code == "UnsupportedAttachmentType"
        occurrence = session.scalar(select(EmailMessageOccurrence))
        assert occurrence is not None
        assert occurrence.acquisition_status == "dead_letter"
        assert occurrence.last_error_code == "AttachmentRejected"
        assert "too-large.csv" in str(occurrence.last_error_message)


def test_cursor_advances_after_parse_failure_and_restart_retries_without_refetch(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {11: _header(11)},
            "messages": {11: _message(("retry.csv", b"retry payload"))},
            "uid_next": 12,
        }
    }
    calls: dict[str, list[object]] = {}
    attempts = 0

    def parse_attachment(filename: str, payload: bytes, profile: str):
        nonlocal attempts
        del filename, payload, profile
        attempts += 1
        if attempts == 1:
            raise ValueError("temporary parser failure")
        return [
            {
                "as_of_date": "2026-07-14",
                "nav": "1.1",
            }
        ]

    retry_fund = _fund(
        "retry",
        "重试基金",
        rules=[{"sender_equals": ["nav@example.com"]}],
        source="email",
    )

    first, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[retry_fund],
        parse_attachment=parse_attachment,
        calls=calls,
    )
    assert first.batches == {}
    with email_session_factory() as session:
        cursor = session.scalar(select(EmailFolderCursor))
        parse = session.scalar(select(EmailAttachmentParse))
        occurrence = session.scalar(select(EmailMessageOccurrence))
        assert cursor is not None and cursor.last_committed_uid == 11
        assert occurrence is not None and occurrence.acquisition_status == "materialized"
        assert parse is not None and parse.status == "retryable"
        parse.next_retry_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()

    second, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[retry_fund],
        parse_attachment=parse_attachment,
        calls=calls,
    )

    assert attempts == 2
    assert list(second.batches) == ["retry"]
    message_fetches = [call for call in calls["messages"] if call[1]]
    assert message_fetches == [("INBOX", (11,), 2)]
    assert calls["discover"][-1][1] == 12


def test_restart_reconciles_parse_committed_before_candidate_routing(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {21: _header(21)},
            "messages": {21: _message(("recovery.csv", b"recovery"))},
            "uid_next": 22,
        }
    }
    fund = _fund(
        "recovery-fund",
        "恢复基金",
        rules=[{"sender_equals": ["nav@example.com"]}],
        source="email",
    )
    calls: dict[str, list[object]] = {}
    parse_calls = 0

    def parse_attachment(filename: str, payload: bytes, profile: str):
        nonlocal parse_calls
        del filename, payload, profile
        parse_calls += 1
        return [{"as_of_date": "2026-07-15", "nav": "1.2"}]

    route_pending_parse = pipeline._route_pending_parse
    monkeypatch.setattr(pipeline, "_route_pending_parse", lambda **kwargs: None)
    first, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[fund],
        parse_attachment=parse_attachment,
        calls=calls,
    )
    assert first.batches == {}
    repository = EmailIngestionRepository(email_session_factory)
    parse_ids = repository.parse_ids_requiring_routing()
    assert len(parse_ids) == 1

    monkeypatch.setattr(pipeline, "_route_pending_parse", route_pending_parse)
    second, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[fund],
        parse_attachment=parse_attachment,
        calls=calls,
    )

    assert parse_calls == 1
    assert list(second.batches) == ["recovery-fund"]
    assert repository.parse_ids_requiring_routing() == []
    message_fetches = [call for call in calls["messages"] if call[1]]
    assert message_fetches == [("INBOX", (21,), 2)]


def test_expunged_retry_message_does_not_block_later_uids(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    repository = EmailIngestionRepository(email_session_factory)
    original_identity = FolderIdentity("mailbox-key", "INBOX", "100", 12)
    lease_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_seconds=60,
    )
    repository.start_folder_scan(original_identity, lease_token=lease_token)
    repository.record_headers(original_identity, [_header(11)])
    repository.mark_message_failed(
        identity=original_identity,
        uid=11,
        error=OSError("connection lost before materialization"),
    )
    repository.finish_folder_scan(
        original_identity,
        lease_token=lease_token,
        committed_uid=11,
    )
    repository.release_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_token=lease_token,
    )
    with email_session_factory() as session:
        retry = session.scalar(
            select(EmailMessageOccurrence).where(
                EmailMessageOccurrence.message_uid == 11
            )
        )
        assert retry is not None
        retry.next_retry_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            seconds=1
        )
        session.commit()

    result, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders={
            "INBOX": {
                "headers": {12: _header(12)},
                "messages": {12: _message(("new.csv", b"new"))},
                "uid_next": 13,
                "uid_validity": 100,
            }
        },
        instruments=[_fund("new-fund", "新基金", code="NEW001")],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2026-07-15",
                "instrument_code": "NEW001",
                "nav": "1.002",
            }
        ],
    )

    assert list(result.batches) == ["new-fund"]
    assert result.folders[0].failed_messages == 1
    with email_session_factory() as session:
        occurrences = {
            occurrence.message_uid: occurrence
            for occurrence in session.scalars(select(EmailMessageOccurrence))
        }
        assert occurrences[11].acquisition_status == "dead_letter"
        assert occurrences[11].last_error_code == "MessageExpunged"
        assert occurrences[12].acquisition_status == "materialized"
        cursor = session.scalar(select(EmailFolderCursor))
        assert cursor is not None and cursor.last_committed_uid == 12


def test_expunged_new_uid_is_recorded_without_restarting_the_folder(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    result, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders={
            "INBOX": {
                "discovered_uids": [11, 12],
                "headers": {12: _header(12)},
                "messages": {12: _message(("new.csv", b"new"))},
                "uid_next": 13,
                "uid_validity": 100,
            }
        },
        instruments=[_fund("new-fund", "新基金", code="NEW001")],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2026-07-15",
                "instrument_code": "NEW001",
                "nav": "1.002",
            }
        ],
    )

    assert list(result.batches) == ["new-fund"]
    assert result.folders[0].failed_messages == 1
    with email_session_factory() as session:
        occurrences = {
            occurrence.message_uid: occurrence
            for occurrence in session.scalars(select(EmailMessageOccurrence))
        }
        assert occurrences[11].acquisition_status == "dead_letter"
        assert occurrences[11].last_error_code == "MessageExpunged"
        assert occurrences[12].acquisition_status == "materialized"
        cursor = session.scalar(select(EmailFolderCursor))
        assert cursor is not None and cursor.last_committed_uid == 12


def test_saved_discovered_header_resumes_without_another_header_fetch(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    repository = EmailIngestionRepository(email_session_factory)
    identity = FolderIdentity("mailbox-key", "INBOX", "100", 2)
    lease_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_seconds=60,
    )
    repository.start_folder_scan(identity, lease_token=lease_token)
    repository.record_headers(identity, [_header(1)])
    repository.release_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_token=lease_token,
    )

    result, calls = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders={
            "INBOX": {
                "headers": {1: _header(1)},
                "messages": {1: _message(("resume.csv", b"resume"))},
                "uid_next": 2,
                "uid_validity": 100,
            }
        },
        instruments=[_fund("resume-fund", "恢复基金", code="RESUME001")],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2026-07-15",
                "instrument_code": "RESUME001",
                "nav": "1.001",
            }
        ],
    )

    assert list(result.batches) == ["resume-fund"]
    assert calls.get("headers", []) == []
    assert calls["messages"] == [("INBOX", (1,), 2)]


def test_history_start_date_excludes_an_older_durable_header(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    older_header = MessageHeader(
        uid=1,
        message_id="<older@example.com>",
        sent_at=datetime(2025, 12, 25, 12, tzinfo=UTC),
        subject="历史净值",
        sender_email="nav@example.com",
    )
    cutoff_header = MessageHeader(
        uid=2,
        message_id="<cutoff@example.com>",
        sent_at=datetime(2025, 12, 26, 0, tzinfo=UTC),
        subject="净值更新",
        sender_email="nav@example.com",
    )
    repository = EmailIngestionRepository(email_session_factory)
    identity = FolderIdentity("mailbox-key", "INBOX", "100", 3)
    lease_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_seconds=60,
    )
    repository.start_folder_scan(identity, lease_token=lease_token)
    repository.record_headers(identity, [older_header])
    repository.release_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_token=lease_token,
    )

    result, calls = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders={
            "INBOX": {
                "headers": {1: older_header, 2: cutoff_header},
                "messages": {2: _message(("cutoff.csv", b"cutoff"))},
                "uid_next": 3,
                "uid_validity": 100,
            }
        },
        instruments=[_fund("cutoff-fund", "边界基金", code="CUTOFF001")],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2025-12-26",
                "instrument_code": "CUTOFF001",
                "nav": "1.001",
            }
        ],
        full_history=True,
    )

    assert list(result.batches) == ["cutoff-fund"]
    assert calls["messages"] == [("INBOX", (2,), 2)]
    assert result.folders[0].ignored_messages == 1
    with email_session_factory() as session:
        occurrences = {
            occurrence.message_uid: occurrence
            for occurrence in session.scalars(select(EmailMessageOccurrence))
        }
        assert occurrences[1].acquisition_status == "ignored"
        assert occurrences[1].last_error_code == "BeforeHistoryStartDate"
        assert occurrences[2].acquisition_status == "materialized"


def test_widened_history_start_reopens_a_scope_ignored_header(
    email_session_factory: sessionmaker[Session],
) -> None:
    repository = EmailIngestionRepository(email_session_factory)
    identity = FolderIdentity("mailbox-key", "INBOX", "100", 2)
    header = MessageHeader(
        uid=1,
        message_id="<year-end@example.com>",
        sent_at=datetime(2025, 12, 27, 9, tzinfo=UTC),
        subject="周频净值",
        sender_email="nav@example.com",
    )
    lease_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_seconds=60,
    )
    repository.start_folder_scan(identity, lease_token=lease_token)
    repository.record_headers(identity, [header])
    repository.mark_messages_ignored(
        identity,
        [1],
        reason_code=HISTORY_START_MESSAGE_REASON_CODE,
        reason_message=history_start_message_reason(date(2025, 12, 31)),
    )

    reopened, excluded = repository.reconcile_message_history_start(
        identity,
        date(2025, 12, 26),
    )
    repository.release_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_token=lease_token,
    )

    assert (reopened, excluded) == (1, 0)
    assert [item.uid for item in repository.due_message_headers(identity)] == [1]


def test_message_batches_persist_before_later_network_failure_and_resume(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {uid: _header(uid) for uid in range(1, 6)},
            "messages": {
                uid: _message((f"{uid}.csv", f"row-{uid}".encode()))
                for uid in range(1, 6)
            },
            "uid_next": 6,
            "uid_validity": 100,
            "message_fetch_error_on_call": 2,
        }
    }
    instruments = [_fund("stream-fund", "流式基金", code="STREAM001")]

    def parse_attachment(filename: str, payload: bytes, profile: str):
        del payload, profile
        return [
            {
                "as_of_date": f"2026-07-{10 + int(filename.split('.')[0]):02d}",
                "instrument_code": "STREAM001",
                "nav": "1.001",
            }
        ]

    first, first_calls = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=instruments,
        parse_attachment=parse_attachment,
    )

    assert first.folders[0].error == "ImapClientError: injected message batch failure"
    assert first_calls["messages"] == [
        ("INBOX", (1, 2), 2),
        ("INBOX", (3, 4), 2),
    ]
    with email_session_factory() as session:
        statuses = {
            occurrence.message_uid: occurrence.acquisition_status
            for occurrence in session.scalars(select(EmailMessageOccurrence))
        }
    assert statuses == {
        1: "materialized",
        2: "materialized",
        3: "discovered",
        4: "discovered",
        5: "discovered",
    }

    folders["INBOX"].pop("message_fetch_error_on_call")
    second, second_calls = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=instruments,
        parse_attachment=parse_attachment,
    )

    assert not second.folders[0].error
    assert second_calls.get("headers", []) == []
    assert second_calls["messages"] == [
        ("INBOX", (3, 4), 2),
        ("INBOX", (5,), 2),
    ]
    with email_session_factory() as session:
        assert set(
            session.scalars(
                select(EmailMessageOccurrence.acquisition_status)
            )
        ) == {"materialized"}


def test_full_history_materializes_newest_messages_first(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    result, calls = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders={
            "INBOX": {
                "headers": {uid: _header(uid) for uid in range(1, 6)},
                "messages": {
                    uid: _message((f"{uid}.csv", f"row-{uid}".encode()))
                    for uid in range(1, 6)
                },
                "uid_next": 6,
                "uid_validity": 100,
            }
        },
        instruments=[_fund("history-fund", "历史基金", code="HISTORY001")],
        parse_attachment=lambda filename, payload, profile: [],
        full_history=True,
    )

    assert not result.folders[0].error
    assert calls["messages"] == [
        ("INBOX", (5, 4), 2),
        ("INBOX", (3, 2), 2),
        ("INBOX", (1,), 2),
    ]


def test_parse_lease_is_exclusive_stale_workers_are_ignored_and_failures_dead_letter(
    email_session_factory: sessionmaker[Session],
) -> None:
    repository = EmailIngestionRepository(email_session_factory)
    identity = FolderIdentity("mailbox", "INBOX", "1", 2)
    lease_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox",
        lease_seconds=60,
    )
    repository.start_folder_scan(identity, lease_token=lease_token)
    header = _header(1)
    repository.record_headers(identity, [header])
    artifact = repository.materialize_message(
        identity=identity,
        header=header,
        raw_message=_message(("lease.csv", b"lease")),
        attachments=[
            AttachmentPayload(
                ordinal=1,
                part_id="1",
                filename="lease.csv",
                media_type="text/csv",
                payload=b"lease",
            )
        ],
    )[0]
    work = repository.prepare_parse(
        artifact_id=artifact.artifact_id,
        parser_profile="generic_nav_table",
        source_format="csv",
        metadata={"file_name": "lease.csv"},
    )
    assert work is not None and work.disposition == "leased" and work.lease_token
    concurrent = repository.prepare_parse(
        artifact_id=artifact.artifact_id,
        parser_profile="generic_nav_table",
        source_format="csv",
    )
    assert concurrent is not None and concurrent.disposition == "pending"

    with email_session_factory() as session:
        parse = session.get(EmailAttachmentParse, work.parse_id)
        assert parse is not None
        parse.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()
    replacement = repository.due_parse_work()
    assert len(replacement) == 1
    current = replacement[0]
    assert current.lease_token and current.lease_token != work.lease_token
    assert repository.fail_parse(
        parse_id=work.parse_id,
        lease_token=work.lease_token,
        error=RuntimeError("stale"),
    ) is False

    for attempt in range(2, MAX_PARSE_ATTEMPTS + 1):
        assert repository.fail_parse(
            parse_id=current.parse_id,
            lease_token=current.lease_token or "",
            error=ValueError(f"failure {attempt}"),
        ) is True
        with email_session_factory() as session:
            parse = session.get(EmailAttachmentParse, current.parse_id)
            assert parse is not None
            if attempt == MAX_PARSE_ATTEMPTS:
                assert parse.status == "dead_letter"
                assert parse.next_retry_at is None
                break
            assert parse.status == "retryable"
            parse.next_retry_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
                seconds=1
            )
            session.commit()
        next_work = repository.due_parse_work()
        assert len(next_work) == 1
        current = next_work[0]

    terminal = repository.prepare_parse(
        artifact_id=artifact.artifact_id,
        parser_profile="generic_nav_table",
        source_format="csv",
    )
    assert terminal is not None and terminal.disposition == "terminal"


def test_label_parser_upgrade_replays_artifact_supersedes_old_route_and_rebuilds_target(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    repository = EmailIngestionRepository(email_session_factory)
    identity = FolderIdentity("mailbox-key", "INBOX", "100", 2)
    lease_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_seconds=60,
    )
    repository.start_folder_scan(identity, lease_token=lease_token)
    header = _header(1, sender="legacy@example.com")
    repository.record_headers(identity, [header])
    artifact = repository.materialize_message(
        identity=identity,
        header=header,
        raw_message=_message(("SBMM07_估值表_20260626.xls", b"snapshot")),
        attachments=[
            AttachmentPayload(
                ordinal=1,
                part_id="1",
                filename="SBMM07_估值表_20260626.xls",
                media_type="application/vnd.ms-excel",
                payload=b"snapshot",
            )
        ],
    )[0]
    repository.finish_folder_scan(
        identity,
        lease_token=lease_token,
        committed_uid=1,
    )
    repository.release_mailbox_lease(
        mailbox_key="mailbox-key",
        lease_token=lease_token,
    )

    with email_session_factory() as session:
        prior_parse = EmailAttachmentParse(
            email_attachment_artifact_id=artifact.artifact_id,
            parser_profile="label_nav_snapshot",
            parser_version="fund-nav-v2",
            source_format="xls",
            status="succeeded",
            completed_at=datetime.now(UTC),
            parser_metadata_json={"file_name": "SBMM07_估值表_20260626.xls"},
        )
        session.add(prior_parse)
        session.flush()
        prior_candidate = EmailNavCandidate(
            email_attachment_parse_id=prior_parse.email_attachment_parse_id,
            row_ordinal=0,
            as_of_date=date(2026, 6, 26),
            unit_nav_value="1.0484",
            raw_row_json={"as_of_date": "2026-06-26", "nav": "1.0484"},
        )
        prior_context = EmailAttachmentRouteContext(
            email_message_attachment_id=artifact.message_attachment_id,
            email_attachment_parse_id=prior_parse.email_attachment_parse_id,
            routing_context_json=[
                {
                    "instrument_id": "strategy-parent",
                    "rule_fingerprint": "legacy-rule",
                    "rule": {"sender_equals": ["legacy@example.com"]},
                }
            ],
        )
        session.add_all([prior_candidate, prior_context])
        session.flush()
        prior_route = EmailNavCandidateRoute(
            email_nav_candidate_id=prior_candidate.email_nav_candidate_id,
            email_attachment_route_context_id=(
                prior_context.email_attachment_route_context_id
            ),
            routing_status="matched",
            validation_status="valid",
            instrument_id="strategy-parent",
            routing_rule_fingerprint="legacy-rule",
            imported_at=datetime.now(UTC),
        )
        session.add(prior_route)
        session.commit()
        prior_route_id = prior_route.email_nav_candidate_route_id

    result, calls = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders={
            "INBOX": {
                "headers": {},
                "messages": {},
                "uid_next": 2,
                "uid_validity": 100,
            }
        },
        instruments=[
            _fund(
                "strategy-parent",
                "云谷策略精选1号",
                rules=[{"sender_equals": ["legacy@example.com"]}],
                source="email",
            ),
            _fund(
                "sbmm07",
                "国泰君安期货CTA因子组合3号集合资产管理计划",
                code="SBMM07",
                source="email",
            ),
        ],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2026-06-26",
                "instrument_code": "SBMM07",
                "instrument_name": "国泰君安期货CTA因子组合3号集合资产管理计划",
                "nav": "1.0484",
            }
        ],
    )

    assert calls.get("messages", []) == []
    assert result.rebuild_instrument_ids == {"strategy-parent"}
    assert set(result.batches) == {"strategy-parent", "sbmm07"}
    assert result.batches["strategy-parent"].route_ids == []
    assert len(result.batches["sbmm07"].route_ids) == 1
    with email_session_factory() as session:
        versions = list(
            session.scalars(
                select(EmailAttachmentParse.parser_version)
                .where(
                    EmailAttachmentParse.parser_profile == "label_nav_snapshot"
                )
                .order_by(EmailAttachmentParse.parser_version)
            )
        )
        assert versions == ["fund-nav-v2", "label-nav-v3"]
        retired = session.get(EmailNavCandidateRoute, prior_route_id)
        assert retired is not None
        assert retired.routing_status == "unmatched"
        assert retired.validation_status == "rejected"
        assert retired.instrument_id is None
        assert retired.imported_at is not None
        assert retired.rejection_reason is not None
        assert retired.rejection_reason.startswith(
            "Superseded by parser label_nav_snapshot@label-nav-v3"
        )
        current_routes = list(
            session.scalars(
                select(EmailNavCandidateRoute)
                .join(EmailNavCandidate)
                .join(EmailAttachmentParse)
                .where(EmailAttachmentParse.parser_version == "label-nav-v3")
            )
        )
        assert [route.instrument_id for route in current_routes] == ["sbmm07"]
        assert [route.routing_status for route in current_routes] == ["matched"]
    assert repository.prepare_parser_upgrades(
        parser_profile="label_nav_snapshot"
    ) == []


def test_folder_failure_is_isolated_and_next_exact_folder_completes(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "missing": {"select_error": True},
        "working": {
            "headers": {3: _header(3)},
            "messages": {3: _message(("working.csv", b"working"))},
        },
    }
    result, calls = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("missing", "working"),
        folders=folders,
        instruments=[_fund("working", "工作基金", code="WORK")],
        parse_attachment=lambda filename, payload, profile: [
            {
                "as_of_date": "2026-07-14",
                "instrument_code": "WORK",
                "nav": "1.0",
            }
        ],
    )

    assert calls["select"] == ["missing", "working"]
    assert result.folders[0].error.startswith("ImapFolderError:")
    assert result.folders[1].error == ""
    assert list(result.batches) == ["working"]


def test_explicit_attachment_exclusion_does_not_fall_back_to_generic_parser(
    monkeypatch: pytest.MonkeyPatch,
    email_session_factory: sessionmaker[Session],
) -> None:
    folders = {
        "INBOX": {
            "headers": {4: _header(4, sender="rules@example.com")},
            "messages": {4: _message(("ignore-me.csv", b"ignored"))},
        }
    }
    parse_calls: list[str] = []
    result, _ = _run_pipeline(
        monkeypatch,
        session_factory=email_session_factory,
        settings=_settings("INBOX"),
        folders=folders,
        instruments=[
            _fund(
                "ruled",
                "规则基金",
                rules=[
                    {
                        "sender_equals": ["rules@example.com"],
                        "attachment_name_excludes": ["ignore-me"],
                    }
                ],
                source="email",
            )
        ],
        parse_attachment=lambda filename, payload, profile: parse_calls.append(filename)
        or [],
    )

    assert parse_calls == []
    assert result.batches == {}


def test_removed_ambiguous_cumulative_key_is_rejected(
    email_session_factory: sessionmaker[Session],
) -> None:
    repository = EmailIngestionRepository(email_session_factory)
    identity = FolderIdentity("mailbox", "INBOX", "1", 2)
    lease_token = repository.acquire_mailbox_lease(
        mailbox_key="mailbox",
        lease_seconds=60,
    )
    repository.start_folder_scan(identity, lease_token=lease_token)
    header = _header(1)
    repository.record_headers(identity, [header])
    artifact = repository.materialize_message(
        identity=identity,
        header=header,
        raw_message=_message(("contract.csv", b"contract")),
        attachments=[
            AttachmentPayload(1, "1", "contract.csv", "text/csv", b"contract")
        ],
    )[0]
    work = repository.prepare_parse(
        artifact_id=artifact.artifact_id,
        parser_profile="generic_nav_table",
        source_format="csv",
    )
    assert work is not None and work.lease_token
    with pytest.raises(ValueError, match="cash_cumulative_nav"):
        repository.complete_parse(
            parse_id=work.parse_id,
            lease_token=work.lease_token,
            rows=[
                {
                    "as_of_date": "2026-07-14",
                    "nav": "1.0",
                    "cumulative_nav": "1.1",
                }
            ],
        )
