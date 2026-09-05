from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math
import re


_CANDIDATE_TYPES = frozenset(
    {"cash_distribution_signal", "cash_balance_discontinuity"}
)
_PAYLOAD_FIELDS = frozenset(
    {
        "fund_nav_action_candidate_id",
        "candidate_type",
        "interval_start_date",
        "interval_end_date",
        "observed_cash_balance_before",
        "observed_cash_balance_after",
        "observed_cash_delta",
        "expected_cash_balance",
        "measurement_uncertainty",
        "status",
        "source_provider",
        "source_revision",
        "source_evidence",
    }
)
_SOURCE_EVIDENCE_FIELDS = frozenset(
    {
        "interval_start_date",
        "interval_end_date",
        "cash_balance_before",
        "cash_balance_after",
        "expected_cash_balance",
        "measurement_uncertainty",
        "unit_nav",
        "cash_cumulative_nav",
        "unit_provider",
        "cash_provider",
        "unit_evidence",
        "cash_evidence",
        "confirmed_event_ids",
    }
)
_CANDIDATE_ID_PATTERN = re.compile(
    r"fund-nav-action-candidate-[0-9a-f]{64}\Z"
)
_SOURCE_REVISION_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_NUMERIC_INTEGER_LIMIT = Decimal("1e20")
_MAX_DECIMAL_SCALE = 18
_MAX_INSTRUMENT_ID_LENGTH = 1024
_MAX_SOURCE_PROVIDER_LENGTH = 1024


@dataclass(frozen=True, slots=True)
class NormalizedFundNavActionCandidate:
    candidate_id: str
    candidate_type: str
    interval_start_date: date
    interval_end_date: date
    observed_cash_balance_before: Decimal
    observed_cash_balance_after: Decimal
    observed_cash_delta: Decimal
    expected_cash_balance: Decimal | None
    measurement_uncertainty: Decimal
    source_provider: str
    source_revision: str
    source_evidence: dict[str, object]

    @property
    def logical_slot(self) -> tuple[str, date, date]:
        return (
            self.candidate_type,
            self.interval_start_date,
            self.interval_end_date,
        )

    @property
    def source_identity(self) -> tuple[str, date, date, str]:
        return (*self.logical_slot, self.source_revision)


def normalize_fund_nav_instrument_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("Fund NAV action candidate instrument_id is required.")
    if len(normalized) > _MAX_INSTRUMENT_ID_LENGTH:
        raise ValueError("Fund NAV action candidate instrument_id is too long.")
    return normalized


def normalize_fund_nav_action_candidate_id(value: object) -> str:
    normalized = str(value or "")
    if _CANDIDATE_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "Fund NAV action candidate id must be the deterministic builder id."
        )
    return normalized


def _normalize_date(value: object, *, field_name: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{field_name} must be an ISO calendar date, not a datetime.")
    if isinstance(value, date):
        return value
    normalized = str(value or "")
    if _ISO_DATE_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be an ISO calendar date.")
    try:
        return date.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO calendar date.") from error


def _normalize_decimal(value: object, *, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite decimal.")
    try:
        normalized = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a finite decimal.") from error
    if not normalized.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal.")
    normalized = Decimal(0) if normalized == 0 else normalized.normalize()
    if normalized.as_tuple().exponent < -_MAX_DECIMAL_SCALE:
        raise ValueError(f"{field_name} exceeds the supported 18-decimal scale.")
    if abs(normalized) >= _NUMERIC_INTEGER_LIMIT:
        raise ValueError(f"{field_name} exceeds the supported numeric range.")
    return normalized


def _normalize_optional_decimal(
    value: object,
    *,
    field_name: str,
) -> Decimal | None:
    if value is None:
        return None
    return _normalize_decimal(value, field_name=field_name)


def _validate_json_value(value: object, *, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError(f"{path} contains a non-finite JSON number.")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string JSON object key.")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} contains a non-JSON value.")


def _normalize_source_evidence(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not value:
        raise ValueError("source_evidence must be a non-empty JSON object.")
    _validate_json_value(value, path="source_evidence")
    try:
        normalized = json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as error:
        raise ValueError("source_evidence must be a non-empty JSON object.") from error
    if not isinstance(normalized, dict) or not normalized:
        raise ValueError("source_evidence must be a non-empty JSON object.")
    return normalized


def _validate_source_evidence_contract(
    *,
    source_evidence: dict[str, object],
    interval_start: date,
    interval_end: date,
    cash_before: Decimal,
    cash_after: Decimal,
    expected_cash_balance: Decimal | None,
    measurement_uncertainty: Decimal,
    source_provider: str,
) -> None:
    if set(source_evidence) != _SOURCE_EVIDENCE_FIELDS:
        raise ValueError(
            "source_evidence does not match the current action-candidate builder "
            "contract."
        )
    if source_evidence["interval_start_date"] != interval_start.isoformat():
        raise ValueError("source_evidence interval_start_date is inconsistent.")
    if source_evidence["interval_end_date"] != interval_end.isoformat():
        raise ValueError("source_evidence interval_end_date is inconsistent.")
    if _normalize_decimal(
        source_evidence["cash_balance_before"],
        field_name="source_evidence.cash_balance_before",
    ) != cash_before:
        raise ValueError("source_evidence cash_balance_before is inconsistent.")
    if _normalize_decimal(
        source_evidence["cash_balance_after"],
        field_name="source_evidence.cash_balance_after",
    ) != cash_after:
        raise ValueError("source_evidence cash_balance_after is inconsistent.")
    if _normalize_optional_decimal(
        source_evidence["expected_cash_balance"],
        field_name="source_evidence.expected_cash_balance",
    ) != expected_cash_balance:
        raise ValueError("source_evidence expected_cash_balance is inconsistent.")
    if _normalize_decimal(
        source_evidence["measurement_uncertainty"],
        field_name="source_evidence.measurement_uncertainty",
    ) != measurement_uncertainty:
        raise ValueError("source_evidence measurement_uncertainty is inconsistent.")
    if str(source_evidence["cash_provider"] or "").strip() != source_provider:
        raise ValueError("source_evidence cash_provider is inconsistent.")
    if not str(source_evidence["unit_provider"] or "").strip():
        raise ValueError("source_evidence unit_provider is required.")
    if not isinstance(source_evidence["unit_evidence"], dict):
        raise ValueError("source_evidence unit_evidence must be an object.")
    if not isinstance(source_evidence["cash_evidence"], dict):
        raise ValueError("source_evidence cash_evidence must be an object.")
    confirmed_event_ids = source_evidence["confirmed_event_ids"]
    if not isinstance(confirmed_event_ids, list) or any(
        not isinstance(event_id, str) or not event_id.strip()
        for event_id in confirmed_event_ids
    ):
        raise ValueError("source_evidence confirmed_event_ids must be a string list.")


def _source_evidence_revision(source_evidence: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            source_evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def expected_fund_nav_action_candidate_id(
    *,
    instrument_id: str,
    candidate: NormalizedFundNavActionCandidate,
) -> str:
    identity = "\\0".join(
        (
            instrument_id,
            candidate.candidate_type,
            str(candidate.interval_start_date),
            str(candidate.interval_end_date),
            candidate.source_revision,
        )
    )
    return "fund-nav-action-candidate-" + hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()


def _normalize_candidate(
    raw_candidate: Mapping[str, object],
    *,
    row_number: int,
) -> NormalizedFundNavActionCandidate:
    actual_fields = set(raw_candidate)
    missing_fields = sorted(_PAYLOAD_FIELDS - actual_fields)
    unknown_fields = sorted(actual_fields - _PAYLOAD_FIELDS)
    if missing_fields or unknown_fields:
        details: list[str] = []
        if missing_fields:
            details.append(f"missing {', '.join(missing_fields)}")
        if unknown_fields:
            details.append(f"unknown {', '.join(unknown_fields)}")
        raise ValueError(
            f"Fund NAV action candidate row {row_number} has invalid fields: "
            + "; ".join(details)
            + "."
        )
    if raw_candidate["status"] != "open":
        raise ValueError(
            f"Fund NAV action candidate row {row_number} must enter as open."
        )

    candidate_type = str(raw_candidate["candidate_type"] or "")
    if candidate_type not in _CANDIDATE_TYPES:
        raise ValueError(
            f"Fund NAV action candidate row {row_number} has an unsupported type."
        )
    interval_start = _normalize_date(
        raw_candidate["interval_start_date"],
        field_name="interval_start_date",
    )
    interval_end = _normalize_date(
        raw_candidate["interval_end_date"],
        field_name="interval_end_date",
    )
    if interval_start >= interval_end:
        raise ValueError(
            f"Fund NAV action candidate row {row_number} requires an ordered interval."
        )

    cash_before = _normalize_decimal(
        raw_candidate["observed_cash_balance_before"],
        field_name="observed_cash_balance_before",
    )
    cash_after = _normalize_decimal(
        raw_candidate["observed_cash_balance_after"],
        field_name="observed_cash_balance_after",
    )
    cash_delta = _normalize_decimal(
        raw_candidate["observed_cash_delta"],
        field_name="observed_cash_delta",
    )
    with localcontext() as context:
        context.prec = 76
        calculated_delta = cash_after - cash_before
    if cash_delta != calculated_delta:
        raise ValueError(
            f"Fund NAV action candidate row {row_number} has an inconsistent cash delta."
        )
    if candidate_type == "cash_distribution_signal" and cash_delta <= 0:
        raise ValueError(
            f"Fund NAV action candidate row {row_number} distribution delta must be positive."
        )
    if candidate_type == "cash_balance_discontinuity" and cash_delta > 0:
        raise ValueError(
            f"Fund NAV action candidate row {row_number} discontinuity delta cannot be positive."
        )

    uncertainty = _normalize_decimal(
        raw_candidate["measurement_uncertainty"],
        field_name="measurement_uncertainty",
    )
    if uncertainty <= 0:
        raise ValueError(
            f"Fund NAV action candidate row {row_number} uncertainty must be positive."
        )
    source_provider = str(raw_candidate["source_provider"] or "").strip()
    if not source_provider:
        raise ValueError(
            f"Fund NAV action candidate row {row_number} requires source_provider."
        )
    if len(source_provider) > _MAX_SOURCE_PROVIDER_LENGTH:
        raise ValueError(
            f"Fund NAV action candidate row {row_number} source_provider is too long."
        )
    source_revision = str(raw_candidate["source_revision"] or "")
    if _SOURCE_REVISION_PATTERN.fullmatch(source_revision) is None:
        raise ValueError(
            f"Fund NAV action candidate row {row_number} has an invalid source_revision."
        )

    expected_cash_balance = _normalize_optional_decimal(
        raw_candidate["expected_cash_balance"],
        field_name="expected_cash_balance",
    )
    source_evidence = _normalize_source_evidence(
        raw_candidate["source_evidence"]
    )
    _validate_source_evidence_contract(
        source_evidence=source_evidence,
        interval_start=interval_start,
        interval_end=interval_end,
        cash_before=cash_before,
        cash_after=cash_after,
        expected_cash_balance=expected_cash_balance,
        measurement_uncertainty=uncertainty,
        source_provider=source_provider,
    )
    if _source_evidence_revision(source_evidence) != source_revision:
        raise ValueError(
            f"Fund NAV action candidate row {row_number} source_revision does not "
            "match source_evidence."
        )
    return NormalizedFundNavActionCandidate(
        candidate_id=normalize_fund_nav_action_candidate_id(
            raw_candidate["fund_nav_action_candidate_id"]
        ),
        candidate_type=candidate_type,
        interval_start_date=interval_start,
        interval_end_date=interval_end,
        observed_cash_balance_before=cash_before,
        observed_cash_balance_after=cash_after,
        observed_cash_delta=cash_delta,
        expected_cash_balance=expected_cash_balance,
        measurement_uncertainty=uncertainty,
        source_provider=source_provider,
        source_revision=source_revision,
        source_evidence=source_evidence,
    )


def normalize_fund_nav_action_candidate_projection(
    candidates: Iterable[Mapping[str, object]],
) -> list[NormalizedFundNavActionCandidate]:
    normalized: list[NormalizedFundNavActionCandidate] = []
    candidate_ids: set[str] = set()
    logical_slots: set[tuple[str, date, date]] = set()
    for row_number, raw_candidate in enumerate(candidates, start=1):
        if not isinstance(raw_candidate, Mapping):
            raise ValueError(
                f"Fund NAV action candidate row {row_number} must be an object."
            )
        candidate = _normalize_candidate(raw_candidate, row_number=row_number)
        if candidate.candidate_id in candidate_ids:
            raise ValueError(
                f'Duplicate fund NAV action candidate id "{candidate.candidate_id}".'
            )
        if candidate.logical_slot in logical_slots:
            raise ValueError(
                "A current fund NAV action projection contains duplicate logical slots."
            )
        candidate_ids.add(candidate.candidate_id)
        logical_slots.add(candidate.logical_slot)
        normalized.append(candidate)
    return normalized
