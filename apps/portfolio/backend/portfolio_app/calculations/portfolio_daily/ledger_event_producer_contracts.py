"""Immutable result and diagnostic contracts for manifest-to-ledger production."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from portfolio_app.calculations.portfolio_daily.ledger_contracts import LedgerEvent


class LedgerEventBuildErrorCode(StrEnum):
    INPUT_CONTRACT_VIOLATION = "input_contract_violation"
    DUPLICATE_NATURAL_KEY = "duplicate_natural_key"
    HASH_MISMATCH = "hash_mismatch"
    MISSING_REFERENCE = "missing_reference"
    ACCOUNT_CONTRACT_VIOLATION = "account_contract_violation"
    INSTRUMENT_CONTRACT_UNAVAILABLE = "instrument_contract_unavailable"
    FX_EVIDENCE_INVALID = "fx_evidence_invalid"
    TRANSFER_PAIR_INVALID = "transfer_pair_invalid"
    CORPORATE_ACTION_TERMS_UNSUPPORTED = (
        "corporate_action_terms_unsupported"
    )
    LEDGER_EVENT_CONTRACT_VIOLATION = "ledger_event_contract_violation"


class LedgerEventDiagnosticCode(StrEnum):
    TOMBSTONE_IGNORED = "tombstone_ignored"
    FUTURE_TRANSACTION_IGNORED = "future_transaction_ignored"
    FX_PATH_UNAVAILABLE = "fx_path_unavailable"
    DETECTED_CORPORATE_ACTION_IGNORED = (
        "detected_corporate_action_ignored"
    )
    CANCELLED_CORPORATE_ACTION_IGNORED = (
        "cancelled_corporate_action_ignored"
    )


@dataclass(frozen=True, slots=True)
class LedgerEventBuildDiagnostic:
    code: LedgerEventDiagnosticCode
    source_table: str
    source_record_id: str
    field_name: str | None
    message: str
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, LedgerEventDiagnosticCode):
            raise TypeError("code must be a LedgerEventDiagnosticCode")
        for name, value in (
            ("source_table", self.source_table),
            ("source_record_id", self.source_record_id),
            ("message", self.message),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be a non-empty canonical string")
        if self.field_name is not None and (
            not isinstance(self.field_name, str)
            or not self.field_name
            or self.field_name != self.field_name.strip()
        ):
            raise ValueError("field_name must be null or a canonical string")
        if (
            not isinstance(self.context, tuple)
            or any(
                not isinstance(item, tuple)
                or len(item) != 2
                or any(not isinstance(part, str) or not part for part in item)
                for item in self.context
            )
            or tuple(sorted(self.context)) != self.context
        ):
            raise ValueError("context must be a sorted tuple of string pairs")


class LedgerEventBuildError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: LedgerEventBuildErrorCode,
        source_table: str,
        source_record_id: str,
        field_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.source_table = source_table
        self.source_record_id = source_record_id
        self.field_name = field_name


@dataclass(frozen=True, slots=True)
class LedgerEventBuildResult:
    events: tuple[LedgerEvent, ...]
    diagnostics: tuple[LedgerEventBuildDiagnostic, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple):
            raise TypeError("events must be an immutable tuple")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LedgerEventBuildDiagnostic)
            for item in self.diagnostics
        ):
            raise TypeError("diagnostics must be an immutable diagnostic tuple")


__all__ = [
    "LedgerEventBuildDiagnostic",
    "LedgerEventBuildError",
    "LedgerEventBuildErrorCode",
    "LedgerEventBuildResult",
    "LedgerEventDiagnosticCode",
]
