from __future__ import annotations

import base64
from collections.abc import Callable, Iterable, Iterator
from datetime import date, datetime
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
import imaplib
import hashlib
import re
from typing import Any

from .types import (
    AttachmentPayload,
    FolderIdentity,
    MessageHeader,
    RejectedAttachment,
)


_UID_PATTERN = re.compile(rb"(?:^|[\s(])UID\s+(\d+)(?=[\s)]|$)", re.IGNORECASE)
_LIST_PATTERN = re.compile(rb"^\([^)]*\)\s+(?:\"[^\"]*\"|NIL)\s+(.+)$")


class ImapClientError(RuntimeError):
    pass


class ImapFolderError(ImapClientError):
    pass


def _status_ok(status: object) -> bool:
    if isinstance(status, bytes):
        return status.upper() == b"OK"
    return str(status or "").upper() == "OK"


def _modified_utf7_encode(value: str) -> bytes:
    result = bytearray()
    pending: list[str] = []

    def flush_pending() -> None:
        if not pending:
            return
        encoded = base64.b64encode("".join(pending).encode("utf-16be"))
        result.extend(b"&" + encoded.rstrip(b"=").replace(b"/", b",") + b"-")
        pending.clear()

    for character in value:
        codepoint = ord(character)
        if 0x20 <= codepoint <= 0x7E:
            flush_pending()
            result.extend(b"&-" if character == "&" else character.encode("ascii"))
        else:
            pending.append(character)
    flush_pending()
    return bytes(result)


def _modified_utf7_decode(value: bytes) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index : index + 1] != b"&":
            next_ampersand = value.find(b"&", index)
            end = len(value) if next_ampersand < 0 else next_ampersand
            output.append(value[index:end].decode("ascii"))
            index = end
            continue
        end = value.find(b"-", index)
        if end < 0:
            raise ImapClientError("Invalid modified UTF-7 mailbox name.")
        payload = value[index + 1 : end]
        if not payload:
            output.append("&")
        else:
            normalized = payload.replace(b",", b"/")
            normalized += b"=" * (-len(normalized) % 4)
            output.append(base64.b64decode(normalized).decode("utf-16be"))
        index = end + 1
    return "".join(output)


def _unquote_mailbox_name(value: bytes) -> bytes:
    value = value.strip()
    if len(value) >= 2 and value[:1] == b'"' and value[-1:] == b'"':
        return value[1:-1].replace(b'\\"', b'"').replace(b"\\\\", b"\\")
    return value


def _chunked(values: list[int], size: int) -> Iterator[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _format_uid_set(values: Iterable[int]) -> str:
    ordered = sorted(set(int(value) for value in values if int(value) > 0))
    if not ordered:
        raise ValueError("An IMAP UID set must not be empty.")
    ranges: list[str] = []
    start = previous = ordered[0]
    for uid in ordered[1:]:
        if uid == previous + 1:
            previous = uid
            continue
        ranges.append(str(start) if start == previous else f"{start}:{previous}")
        start = previous = uid
    ranges.append(str(start) if start == previous else f"{start}:{previous}")
    return ",".join(ranges)


class ImapClient:
    """One authenticated IMAP connection with exact-folder and batch-UID semantics."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._mailbox: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None
        self._selected_folder: str | None = None
        self._selected_identity: FolderIdentity | None = None
        self._listed_folders: dict[str, bytes] | None = None

    @property
    def mailbox_key(self) -> str:
        username = str(self.settings.email_imap_username or "").strip().lower()
        host = str(self.settings.email_imap_host or "").strip().lower()
        return hashlib.sha256(f"{host}\0{username}".encode("utf-8")).hexdigest()

    def connect(self) -> None:
        if self._mailbox is not None:
            return
        mailbox_class = (
            imaplib.IMAP4_SSL if self.settings.email_imap_use_ssl else imaplib.IMAP4
        )
        mailbox = mailbox_class(
            self.settings.email_imap_host,
            self.settings.email_imap_port,
            timeout=self.settings.email_imap_timeout_seconds,
        )
        try:
            status, _ = mailbox.login(
                self.settings.email_imap_username,
                self.settings.email_imap_password,
            )
            if not _status_ok(status):
                raise ImapClientError("IMAP login failed.")
        except Exception:
            self._close_mailbox(mailbox)
            raise
        self._mailbox = mailbox

    @staticmethod
    def _close_mailbox(mailbox: object) -> None:
        shutdown = getattr(mailbox, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
                return
            except Exception:
                pass
        logout = getattr(mailbox, "logout", None)
        if callable(logout):
            try:
                logout()
            except Exception:
                pass

    def list_folders(self) -> dict[str, bytes]:
        self.connect()
        if self._listed_folders is not None:
            return dict(self._listed_folders)
        assert self._mailbox is not None
        status, response = self._mailbox.list()
        if not _status_ok(status):
            raise ImapClientError("Unable to list IMAP folders.")
        folders: dict[str, bytes] = {}
        for raw_item in response or []:
            if not isinstance(raw_item, (bytes, bytearray)):
                continue
            match = _LIST_PATTERN.match(bytes(raw_item))
            if match is None:
                continue
            wire_name = _unquote_mailbox_name(match.group(1))
            folders[_modified_utf7_decode(wire_name)] = wire_name
        self._listed_folders = folders
        return dict(folders)

    def select_folder(self, folder_name: str) -> FolderIdentity:
        self.connect()
        folders = self.list_folders()
        if folder_name not in folders:
            raise ImapFolderError(f'Configured IMAP folder "{folder_name}" does not exist.')
        assert self._mailbox is not None
        wire_name = folders[folder_name]
        try:
            status, _ = self._mailbox.select(
                wire_name,
                readonly=not self.settings.email_imap_mark_seen,
            )
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, EOFError):
            self._discard_connection()
            self.connect()
            folders = self.list_folders()
            if folder_name not in folders:
                raise ImapFolderError(
                    f'Configured IMAP folder "{folder_name}" disappeared after reconnect.'
                )
            assert self._mailbox is not None
            status, _ = self._mailbox.select(
                folders[folder_name],
                readonly=not self.settings.email_imap_mark_seen,
            )
        if not _status_ok(status):
            self._selected_folder = None
            raise ImapFolderError(f'Unable to select configured IMAP folder "{folder_name}".')
        self._selected_folder = folder_name
        uid_validity = self._response_integer("UIDVALIDITY")
        uid_next = self._response_integer("UIDNEXT")
        identity = FolderIdentity(
            mailbox_key=self.mailbox_key,
            folder_name=folder_name,
            uid_validity=str(uid_validity),
            uid_next=uid_next,
        )
        self._selected_identity = identity
        return identity

    def _response_integer(self, key: str) -> int:
        assert self._mailbox is not None
        response_code, values = self._mailbox.response(key)
        if response_code is None or not values:
            raise ImapClientError(f"IMAP server did not provide {key} after SELECT.")
        raw = values[-1]
        if isinstance(raw, bytes):
            text = raw.decode("ascii", errors="strict")
        else:
            text = str(raw)
        match = re.search(r"\d+", text)
        if match is None:
            raise ImapClientError(f"IMAP server returned an invalid {key} value.")
        return int(match.group(0))

    def discover_uids(
        self,
        *,
        first_uid: int,
        uid_next: int,
        full_history: bool,
        history_start_date: date,
    ) -> list[int]:
        if self._mailbox is None or self._selected_folder is None:
            raise ImapClientError("An IMAP folder must be selected before UID discovery.")
        if full_history:
            criteria: tuple[object, ...] = (
                "SINCE",
                history_start_date.strftime("%d-%b-%Y"),
            )
        elif first_uid > 1:
            if first_uid >= uid_next:
                return []
            criteria = ("UID", f"{first_uid}:{uid_next - 1}")
        else:
            criteria = ("SINCE", history_start_date.strftime("%d-%b-%Y"))
        status, response = self._uid("search", None, *criteria)
        if not _status_ok(status):
            raise ImapClientError("Unable to discover IMAP message UIDs.")
        raw_uids = response[0] if response and response[0] else b""
        return sorted({int(raw) for raw in raw_uids.split() if raw})

    def fetch_headers(
        self,
        uids: Iterable[int],
        *,
        chunk_size: int,
        on_batch: Callable[[], None] | None = None,
        allow_missing: bool = False,
    ) -> list[MessageHeader]:
        requested = sorted(set(int(uid) for uid in uids if int(uid) > 0))
        headers: list[MessageHeader] = []
        for chunk in _chunked(requested, max(1, chunk_size)):
            response = self._fetch_uid_set(
                chunk,
                "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE SUBJECT FROM)])",
                allow_missing=allow_missing,
            )
            for uid, payload in response:
                message = BytesParser(policy=policy.default).parsebytes(payload)
                sent_at: datetime | None = None
                raw_date = str(message.get("date") or "").strip()
                if raw_date:
                    try:
                        sent_at = parsedate_to_datetime(raw_date)
                    except (TypeError, ValueError, OverflowError):
                        sent_at = None
                headers.append(
                    MessageHeader(
                        uid=uid,
                        message_id=str(message.get("message-id") or "").strip(),
                        sent_at=sent_at,
                        subject=str(message.get("subject") or ""),
                        sender_email=parseaddr(str(message.get("from") or ""))[1]
                        .strip()
                        .lower(),
                    )
                )
            if on_batch is not None:
                on_batch()
        return sorted(headers, key=lambda header: header.uid)

    def fetch_messages(
        self,
        uids: Iterable[int],
        *,
        chunk_size: int,
        on_batch: Callable[[], None] | None = None,
        allow_missing: bool = False,
    ) -> dict[int, bytes]:
        requested = sorted(set(int(uid) for uid in uids if int(uid) > 0))
        messages: dict[int, bytes] = {}
        for chunk in _chunked(requested, max(1, chunk_size)):
            messages.update(
                dict(
                    self._fetch_uid_set(
                        chunk,
                        "(UID BODY.PEEK[])",
                        allow_missing=allow_missing,
                    )
                )
            )
            if on_batch is not None:
                on_batch()
        return messages

    def _fetch_uid_set(
        self,
        uids: list[int],
        request: str,
        *,
        allow_missing: bool = False,
    ) -> list[tuple[int, bytes]]:
        if self._mailbox is None or self._selected_folder is None:
            raise ImapClientError("An IMAP folder must be selected before FETCH.")
        status, response = self._uid("fetch", _format_uid_set(uids), request)
        if not _status_ok(status):
            raise ImapClientError("IMAP batch FETCH failed.")
        payloads: list[tuple[int, bytes]] = []
        for item in response or []:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            metadata, payload = item[0], item[1]
            if not isinstance(metadata, (bytes, bytearray)) or not isinstance(
                payload, (bytes, bytearray)
            ):
                continue
            match = _UID_PATTERN.search(bytes(metadata))
            if match is None:
                raise ImapClientError("IMAP FETCH response omitted the requested UID.")
            payloads.append((int(match.group(1)), bytes(payload)))
        returned_uids = [uid for uid, _ in payloads]
        duplicate_uids = sorted(
            uid for uid in set(returned_uids) if returned_uids.count(uid) > 1
        )
        if duplicate_uids:
            raise ImapClientError(
                "IMAP FETCH duplicated UIDs: "
                + ", ".join(str(uid) for uid in duplicate_uids)
            )
        unexpected = sorted(set(returned_uids) - set(uids))
        if unexpected:
            raise ImapClientError(
                "IMAP FETCH returned unexpected UIDs: "
                + ", ".join(str(uid) for uid in unexpected)
            )
        missing = sorted(set(uids) - set(returned_uids))
        if missing and not allow_missing:
            raise ImapClientError(
                "IMAP FETCH omitted UIDs: " + ", ".join(str(uid) for uid in missing)
            )
        return payloads

    def _uid(self, command: str, *args: object):
        if self._mailbox is None:
            raise ImapClientError("IMAP is not connected.")
        try:
            return self._mailbox.uid(command, *args)
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, EOFError):
            expected_identity = self._selected_identity
            if expected_identity is None:
                raise
            self._discard_connection()
            recovered_identity = self.select_folder(expected_identity.folder_name)
            if recovered_identity.uid_validity != expected_identity.uid_validity:
                raise ImapClientError(
                    "IMAP UIDVALIDITY changed during retry; the folder scan must restart."
                )
            assert self._mailbox is not None
            return self._mailbox.uid(command, *args)

    def _discard_connection(self) -> None:
        mailbox = self._mailbox
        self._mailbox = None
        self._selected_folder = None
        self._selected_identity = None
        self._listed_folders = None
        if mailbox is not None:
            self._close_mailbox(mailbox)

    @staticmethod
    def extract_attachments(
        raw_message: bytes,
        *,
        max_bytes: int | None = None,
    ) -> tuple[list[AttachmentPayload], list[RejectedAttachment]]:
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        attachments: list[AttachmentPayload] = []
        rejected: list[RejectedAttachment] = []
        for ordinal, part in enumerate(message.walk(), start=1):
            filename = str(part.get_filename() or "").strip()
            if not filename and part.get_content_disposition() != "attachment":
                continue
            transfer_encoding = str(
                part.get("content-transfer-encoding") or ""
            ).strip().lower()
            encoded_payload = part.get_payload(decode=False)
            if max_bytes is not None and transfer_encoding == "base64":
                if isinstance(encoded_payload, str):
                    compact_size = len("".join(encoded_payload.split()))
                elif isinstance(encoded_payload, bytes):
                    compact_size = len(b"".join(encoded_payload.split()))
                else:
                    compact_size = 0
                if compact_size and (compact_size // 4) * 3 - 2 > max_bytes:
                    rejected.append(
                        RejectedAttachment(
                            ordinal=ordinal,
                            part_id=str(ordinal),
                            filename=filename or "attachment",
                            media_type=part.get_content_type(),
                            reason=f"attachment exceeds configured {max_bytes}-byte limit",
                        )
                    )
                    continue
            payload = part.get_payload(decode=True) or b""
            if not payload:
                continue
            if max_bytes is not None and len(payload) > max_bytes:
                rejected.append(
                    RejectedAttachment(
                        ordinal=ordinal,
                        part_id=str(ordinal),
                        filename=filename or "attachment",
                        media_type=part.get_content_type(),
                        reason=f"attachment exceeds configured {max_bytes}-byte limit",
                    )
                )
                continue
            attachments.append(
                AttachmentPayload(
                    ordinal=ordinal,
                    part_id=str(ordinal),
                    filename=filename or "attachment",
                    media_type=part.get_content_type(),
                    payload=bytes(payload),
                )
            )
        return attachments, rejected

    def close(self) -> None:
        mailbox = self._mailbox
        selected_folder = self._selected_folder
        self._mailbox = None
        self._selected_folder = None
        self._selected_identity = None
        self._listed_folders = None
        if mailbox is None:
            return
        if selected_folder is not None:
            try:
                mailbox.close()
            except Exception:
                pass
        self._close_mailbox(mailbox)

    def __enter__(self) -> "ImapClient":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()
