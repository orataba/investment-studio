from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal


AcquisitionStatus = Literal[
    "discovered",
    "materialized",
    "ignored",
    "retryable",
    "dead_letter",
]
ParseStatus = Literal[
    "pending",
    "processing",
    "succeeded",
    "retryable",
    "dead_letter",
    "unsupported",
]
RoutingStatus = Literal["pending", "matched", "unmatched", "ambiguous"]


@dataclass(frozen=True)
class FolderIdentity:
    mailbox_key: str
    folder_name: str
    uid_validity: str
    uid_next: int


@dataclass(frozen=True)
class MessageHeader:
    uid: int
    message_id: str
    sent_at: datetime | None
    subject: str
    sender_email: str


@dataclass(frozen=True)
class AttachmentPayload:
    ordinal: int
    part_id: str
    filename: str
    media_type: str
    payload: bytes


@dataclass(frozen=True)
class RejectedAttachment:
    ordinal: int
    part_id: str
    filename: str
    media_type: str
    reason: str


@dataclass(frozen=True)
class RouteTarget:
    instrument_id: str
    instrument_name: str
    currency: str
    identifiers: frozenset[str]
    rules: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class ParsedNavCandidate:
    row_ordinal: int
    instrument_code: str
    instrument_name: str
    as_of_date: date | None
    unit_nav: Decimal | None
    cash_cumulative_nav: Decimal | None
    explicit_total_return_nav: Decimal | None
    currency: str
    raw_row: dict[str, object]


@dataclass
class InstrumentIngestionBatch:
    instrument_id: str
    currency: str
    rows: list[dict[str, object]] = field(default_factory=list)
    attachment_names: list[str] = field(default_factory=list)
    route_ids: list[int] = field(default_factory=list)


@dataclass
class FolderIngestionSummary:
    folder_name: str
    uid_validity: str = ""
    discovered_messages: int = 0
    fetched_messages: int = 0
    stored_attachments: int = 0
    parsed_attachments: int = 0
    ignored_messages: int = 0
    failed_messages: int = 0
    error: str = ""


@dataclass
class EmailIngestionResult:
    batches: dict[str, InstrumentIngestionBatch] = field(default_factory=dict)
    rebuild_instrument_ids: set[str] = field(default_factory=set)
    folders: list[FolderIngestionSummary] = field(default_factory=list)
    unmatched_candidates: int = 0
    ambiguous_candidates: int = 0
    invalid_candidates: int = 0
    out_of_scope_candidates: int = 0

    @property
    def failed_folders(self) -> list[FolderIngestionSummary]:
        return [folder for folder in self.folders if folder.error]
