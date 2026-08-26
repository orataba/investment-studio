from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from portfolio_ops_instrument_core.db_models import (
    CorporateActionEvent,
    FundNavAdjustmentFactor,
    FundNavCurrentProjection,
    FundNavEvent,
    FundNavProjectionRun,
    FundNavProjectionRunEvent,
    FundNavProjectionRunReinvestmentEvidence,
    FundNavReinvestmentEvidence,
    Instrument,
    InstrumentBrokerIdentifier,
    InstrumentIdentifier,
    InstrumentMarketData,
    InstrumentPriceBar,
    RegistryMetadata,
)
from portfolio_ops_instrument_core.fx_contract import (
    fx_instrument_identity,
    validate_fx_market_data_contract,
)
from portfolio_ops_instrument_core.models import (
    BrokerIdentifier,
    CorporateActionEvent as CorporateActionEventModel,
    FundNavAdjustmentFactor as FundNavAdjustmentFactorModel,
    FundNavEvent as FundNavEventModel,
    FundNavProjectionRun as FundNavProjectionRunModel,
    FundNavReinvestmentEvidence as FundNavReinvestmentEvidenceModel,
    NavLineage,
    InstrumentCore,
    SourceSettings,
    canonical_price_contract,
    normalize_instrument_type,
    normalize_market_data_currency,
    parse_persisted_price_contract,
    parse_positive_market_data_value,
    validate_nav_history_instrument_type,
    validate_quote_selection_policy_for_instrument_type,
    _quantized_fund_nav_factor,
    deterministic_fund_nav_adjustment_factor_id,
)


DEFAULT_REGISTRY_NAME = "Portfolio Operations Shared Instruments"
EMPTY_STORE: dict[str, object] = {
    "registry_name": DEFAULT_REGISTRY_NAME,
    "instruments": [],
}

SessionFactory = Callable[[], Session]
EMAIL_REFRESH_SUCCESS_STATUSES = frozenset({"imported", "no_match", "no_new_data"})
_SOURCE_SETTING_UNSET = object()
SOURCE_SCHEDULE_DEFAULTS: dict[str, tuple[str, int]] = {
    "public_fund": ("daily", 1),
    "private_fund": ("daily", 1),
    "etf": ("daily", 0),
    "index": ("daily", 0),
    "equity": ("daily", 0),
    "fx": ("daily", 0),
    "cash": ("event_driven", 0),
    "other": ("event_driven", 0),
}
MARKET_CALENDAR_SUFFIXES = {
    ".SH": "XSHG",
    ".SS": "XSHG",
    ".SZ": "XSHE",
    ".HK": "XHKG",
    ".L": "XLON",
    ".DE": "XETR",
    ".PA": "XPAR",
    ".AS": "XAMS",
    ".MI": "XMIL",
    ".SW": "XSWX",
}


class StaleFundNavPublicationError(RuntimeError):
    """The raw snapshot was built from an older registry watermark."""


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def _validated_nav_lineage(
    *,
    metric_family: str,
    quote_basis: str,
    raw_lineage: object,
    status: str | None = None,
) -> NavLineage | None:
    if metric_family != "nav":
        if raw_lineage is not None:
            raise ValueError("nav_lineage is only valid for NAV observations.")
        return None
    if raw_lineage is None:
        raise ValueError(f"{quote_basis} requires nav_lineage.")
    lineage = NavLineage.model_validate(raw_lineage)
    factor_record_id = lineage.evidence.get("factor_record_id")
    has_factor = isinstance(factor_record_id, str) and bool(factor_record_id.strip())
    factor_logical_key = lineage.evidence.get("factor_logical_key")
    has_logical_factor = isinstance(factor_logical_key, str) and bool(
        factor_logical_key.strip()
    )
    if quote_basis == "official_nav":
        if lineage.kind != "provider_explicit":
            raise ValueError("official_nav must use provider_explicit lineage.")
        if has_factor or has_logical_factor:
            raise ValueError("official_nav cannot reference an adjustment factor.")
    elif quote_basis == "total_return_nav":
        normalized_status = str(status or "").strip().lower()
        if normalized_status != "complete":
            raise ValueError(
                "total_return_nav must be complete and factor-backed; "
                "unavailable total return is represented by row absence"
            )
        if not (has_factor or has_logical_factor):
            raise ValueError(
                "complete total_return_nav requires a factor reference"
            )
    return lineage


def _serialized_nav_lineage(
    *,
    kind: object,
    method_version: object,
    anchor_date: object,
    evidence: object,
) -> dict[str, object] | None:
    if kind is None:
        if any(value is not None for value in (method_version, anchor_date, evidence)):
            raise ValueError("Persisted NAV lineage is incomplete.")
        return None
    return NavLineage.model_validate(
        {
            "kind": kind,
            "method_version": method_version,
            "anchor_date": anchor_date,
            "evidence": deepcopy(evidence),
        }
    ).model_dump(mode="json")


def _nav_lineage_columns(
    lineage: NavLineage | None,
) -> dict[str, object | None]:
    if lineage is None:
        return {
            "nav_lineage_kind": None,
            "nav_derivation_method_version": None,
            "nav_derivation_anchor_date": None,
            "nav_lineage_evidence_json": None,
            "fund_nav_adjustment_factor_id": None,
        }
    return {
        "nav_lineage_kind": lineage.kind,
        "nav_derivation_method_version": lineage.method_version,
        "nav_derivation_anchor_date": lineage.anchor_date,
        "nav_lineage_evidence_json": deepcopy(lineage.evidence),
        "fund_nav_adjustment_factor_id": (
            str(lineage.evidence["factor_record_id"]).strip()
            if isinstance(lineage.evidence.get("factor_record_id"), str)
            and str(lineage.evidence["factor_record_id"]).strip()
            else None
        ),
    }


def _parse_utc_iso(value: object) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _next_market_data_watermark(session: Session) -> str:
    metadata_record = session.scalar(
        select(RegistryMetadata)
        .where(RegistryMetadata.registry_key == "shared")
        .with_for_update()
    )
    if metadata_record is None:
        metadata_record = RegistryMetadata(
            registry_key="shared",
            registry_name=DEFAULT_REGISTRY_NAME,
        )
        session.add(metadata_record)
        session.flush()

    candidate = _parse_utc_iso(_utcnow_iso()) or datetime.now(UTC)
    previous = _parse_utc_iso(metadata_record.market_data_updated_at)
    if previous is not None and candidate <= previous:
        candidate = previous + timedelta(microseconds=1)
    watermark = candidate.isoformat(timespec="microseconds").replace("+00:00", "Z")
    metadata_record.market_data_updated_at = watermark
    return watermark


def _next_calculation_input_watermark(current_value: str | None) -> str:
    candidate = _parse_utc_iso(_utcnow_iso()) or datetime.now(UTC)
    previous = _parse_utc_iso(current_value)
    if previous is not None and candidate <= previous:
        candidate = previous + timedelta(microseconds=1)
    return candidate.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _inferred_market_calendar(
    identifiers: Iterable[dict[str, object]] | None,
) -> str | None:
    ordered_identifiers = sorted(
        [identifier for identifier in (identifiers or []) if isinstance(identifier, dict)],
        key=lambda identifier: 0 if bool(identifier.get("is_primary")) else 1,
    )
    for identifier in ordered_identifiers:
        identifier_value = str(identifier.get("identifier_value") or "").strip().upper()
        for suffix, calendar in MARKET_CALENDAR_SUFFIXES.items():
            if identifier_value.endswith(suffix):
                return calendar
    return None


def _default_source_settings(
    *,
    instrument_type: str = "other",
    identifiers: Iterable[dict[str, object]] | None = None,
) -> dict[str, object]:
    normalized_instrument_type = instrument_type.strip().lower()
    expected_frequency, release_lag_days = SOURCE_SCHEDULE_DEFAULTS.get(
        normalized_instrument_type,
        SOURCE_SCHEDULE_DEFAULTS["other"],
    )
    return {
        "source_mode": "manual",
        "source_email": "",
        "source_location": "Shared data ops",
        "source_api_profile": "",
        "source_email_rules": [],
        "expected_frequency": expected_frequency,
        "market_calendar": (
            _inferred_market_calendar(identifiers)
            if expected_frequency != "event_driven"
            else None
        ),
        "release_lag_days": release_lag_days,
        "return_semantics": "unknown",
    }


def _default_refresh_status(source_mode: str = "manual") -> dict[str, object]:
    return {
        "status": "idle",
        "message": "",
        "requested_at": None,
        "requested_by": None,
        "mode": source_mode,
        "last_successful_requested_at": None,
    }


def _updated_refresh_status(
    *,
    previous: dict[str, object],
    status: str,
    message: str,
    updated_by: str | None,
    mode: str,
    requested_at: str,
) -> dict[str, object]:
    normalized_mode = str(mode or "manual").strip().lower() or "manual"
    previous_cursor = str(previous.get("last_successful_requested_at") or "").strip() or None

    last_successful_requested_at = previous_cursor
    if normalized_mode == "email" and status.strip().lower() in EMAIL_REFRESH_SUCCESS_STATUSES:
        last_successful_requested_at = requested_at

    return {
        "status": status,
        "message": message,
        "requested_at": requested_at,
        "requested_by": (updated_by or "platform_ui").strip() or "platform_ui",
        "mode": normalized_mode,
        "last_successful_requested_at": last_successful_requested_at,
    }


def _default_lifecycle_state(
    status: str = "active",
    *,
    changed_at: str | None = None,
    changed_by: str | None = None,
    canonical_instrument_id: str | None = None,
) -> dict[str, object]:
    normalized_status = status if status in {"active", "archived"} else "active"
    state: dict[str, object] = {
        "status": normalized_status,
        "changed_at": changed_at,
        "changed_by": changed_by,
    }
    if canonical_instrument_id:
        state["canonical_instrument_id"] = canonical_instrument_id
    return state


QUOTE_SELECTION_POLICY_DEFAULTS: dict[str, dict[str, list[str]]] = {
    "public_fund": {
        "trading": ["last", "close", "official_nav"],
        "valuation": ["official_nav", "close", "last"],
        "total_return": ["total_return_nav"],
        "chart": ["total_return_nav"],
        "reference": ["official_nav", "close", "last"],
    },
    "private_fund": {
        "trading": ["last", "close", "official_nav"],
        "valuation": ["official_nav", "close", "last"],
        "total_return": ["total_return_nav"],
        "chart": ["total_return_nav"],
        "reference": ["official_nav", "close", "last"],
    },
    "etf": {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "equity": {
        "trading": ["last", "close"],
        # Valuation and transaction accounting must use the unadjusted traded
        # price. Adjusted close is a synthetic total-return series and must
        # never silently substitute for a missing market price.
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "index": {
        "trading": ["close", "last"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "cash": {
        "trading": ["par"],
        "valuation": ["par"],
        "total_return": ["par"],
        "chart": ["par"],
        "reference": ["par"],
    },
    "fx": {
        "trading": ["spot"],
        "valuation": ["spot"],
        "total_return": ["spot"],
        "chart": ["spot"],
        "reference": ["spot"],
    },
    "other": {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
}

VALID_DATA_STATUSES = {"complete", "partial", "unavailable"}


def _validated_market_data_observation(
    *,
    instrument_id: object,
    instrument_type: object,
    instrument_currency: object,
    metric_family: object,
    quote_basis: object,
    point_currency: object,
    value: object,
    status: object,
) -> tuple[str, str, Decimal]:
    normalized_instrument_type = normalize_instrument_type(instrument_type)
    normalized_instrument_currency = normalize_market_data_currency(instrument_currency)
    normalized_point_currency = normalize_market_data_currency(point_currency)
    if normalized_point_currency != normalized_instrument_currency:
        raise ValueError(
            f'Market-data currency "{normalized_point_currency}" does not match '
            f'instrument currency "{normalized_instrument_currency}".'
        )

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in VALID_DATA_STATUSES:
        raise ValueError(f'Unsupported market-data status "{normalized_status}".')

    normalized_metric_family = str(metric_family or "").strip().lower()
    normalized_quote_basis = str(quote_basis or "").strip().lower()
    normalized_value = parse_positive_market_data_value(value)
    validated_fx = validate_fx_market_data_contract(
        instrument_id=instrument_id,
        instrument_type=normalized_instrument_type,
        instrument_currency=normalized_instrument_currency,
        metric_family=normalized_metric_family,
        quote_basis=normalized_quote_basis,
        point_currency=normalized_point_currency,
        value=normalized_value,
        status=normalized_status,
    )
    return (
        normalized_point_currency,
        normalized_status,
        validated_fx.rate if validated_fx is not None else normalized_value,
    )


def _default_quote_selection_policy(instrument_type: str) -> dict[str, object]:
    normalized_instrument_type = normalize_instrument_type(instrument_type)
    return deepcopy(QUOTE_SELECTION_POLICY_DEFAULTS[normalized_instrument_type])


def validate_quote_selection_policy(
    *,
    instrument_type: str,
    quote_selection_policy: dict[str, object],
) -> None:
    validate_quote_selection_policy_for_instrument_type(
        instrument_type=instrument_type,
        quote_selection_policy=quote_selection_policy,
    )


def _normalized_quote_selection_policy(item: dict[str, object]) -> dict[str, object]:
    raw_policy = item.get("quote_selection_policy")
    if not isinstance(raw_policy, dict):
        raise ValueError("quote_selection_policy must be an object.")
    return validate_quote_selection_policy_for_instrument_type(
        instrument_type=str(item.get("instrument_type") or ""),
        quote_selection_policy=raw_policy,
    ).model_dump()


def _sort_market_data(points: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        points,
        key=lambda item: (
            str(item.get("as_of_date") or ""),
            str(item.get("metric_family") or ""),
            str(item.get("quote_basis") or ""),
            str(item.get("currency") or ""),
        ),
    )


def _corporate_action_to_dict(event: CorporateActionEvent) -> dict[str, object]:
    return {
        "corporate_action_event_id": event.corporate_action_event_id,
        "instrument_id": event.instrument_id,
        "action_type": event.action_type,
        "announcement_date": (
            event.announcement_date.isoformat() if event.announcement_date else None
        ),
        "record_date": event.record_date.isoformat() if event.record_date else None,
        "effective_date": event.effective_date.isoformat(),
        "payable_date": event.payable_date.isoformat() if event.payable_date else None,
        "new_units": event.new_units,
        "old_units": event.old_units,
        "quantity_rounding": event.quantity_rounding,
        "quantity_precision": event.quantity_precision,
        "cost_basis_treatment": event.cost_basis_treatment,
        "source": event.source,
        "external_event_id": event.external_event_id,
        "status": event.status,
        "provenance": deepcopy(event.provenance_json or {}),
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def _sort_corporate_actions(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        events,
        key=lambda item: (
            str(item.get("effective_date") or ""),
            str(item.get("action_type") or ""),
            str(item.get("corporate_action_event_id") or ""),
        ),
    )


def _fund_nav_event_to_dict(event: FundNavEvent) -> dict[str, object]:
    return {
        "fund_nav_event_id": event.fund_nav_event_id,
        "fund_nav_action_id": event.fund_nav_action_id,
        "revision_number": event.revision_number,
        "revision_kind": event.revision_kind,
        "supersedes_fund_nav_event_id": event.supersedes_fund_nav_event_id,
        "instrument_id": event.instrument_id,
        "event_type": event.event_type,
        "announcement_date": (
            event.announcement_date.isoformat() if event.announcement_date else None
        ),
        "record_date": event.record_date.isoformat() if event.record_date else None,
        "effective_date": event.effective_date.isoformat(),
        "payable_date": event.payable_date.isoformat() if event.payable_date else None,
        "sequence_order": event.sequence_order,
        "cash_per_unit": (
            _decimal_text(event.cash_per_unit)
            if event.cash_per_unit is not None
            else None
        ),
        "unit_ratio": (
            _decimal_text(event.unit_ratio) if event.unit_ratio is not None else None
        ),
        "evidence_kind": event.evidence_kind,
        "source": event.source,
        "external_event_id": event.external_event_id,
        "provenance": deepcopy(event.provenance_json),
        "recorded_by": event.recorded_by,
        "revision_reason": event.revision_reason,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def _fund_nav_reinvestment_evidence_to_dict(
    evidence: FundNavReinvestmentEvidence,
) -> dict[str, object]:
    return {
        "fund_nav_reinvestment_evidence_id": (
            evidence.fund_nav_reinvestment_evidence_id
        ),
        "instrument_id": evidence.instrument_id,
        "fund_nav_event_id": evidence.fund_nav_event_id,
        "revision_number": evidence.revision_number,
        "revision_kind": evidence.revision_kind,
        "supersedes_fund_nav_reinvestment_evidence_id": (
            evidence.supersedes_fund_nav_reinvestment_evidence_id
        ),
        "reinvestment_nav": _decimal_text(evidence.reinvestment_nav),
        "evidence_kind": evidence.evidence_kind,
        "source": evidence.source,
        "external_evidence_id": evidence.external_evidence_id,
        "provenance": deepcopy(evidence.provenance_json),
        "recorded_by": evidence.recorded_by,
        "revision_reason": evidence.revision_reason,
        "created_at": evidence.created_at,
        "updated_at": evidence.updated_at,
    }


def _current_fund_nav_ledger_views(
    event_revisions: Iterable[dict[str, object]],
    evidence_revisions: Iterable[dict[str, object]],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    ordered_events = sorted(
        event_revisions,
        key=lambda event: (
            str(event["effective_date"]),
            int(event["sequence_order"] or 0),
            str(event["fund_nav_action_id"]),
            int(event["revision_number"]),
            str(event["fund_nav_event_id"]),
        ),
    )
    superseded_event_ids = {
        str(event["supersedes_fund_nav_event_id"])
        for event in ordered_events
        if event.get("supersedes_fund_nav_event_id")
    }
    current_events = [
        event
        for event in ordered_events
        if event["fund_nav_event_id"] not in superseded_event_ids
        and event["revision_kind"] != "cancellation"
    ]
    current_event_ids = {
        str(event["fund_nav_event_id"]) for event in current_events
    }

    ordered_evidence = sorted(
        evidence_revisions,
        key=lambda evidence: (
            str(evidence["fund_nav_event_id"]),
            int(evidence["revision_number"]),
            str(evidence["fund_nav_reinvestment_evidence_id"]),
        ),
    )
    superseded_evidence_ids = {
        str(evidence["supersedes_fund_nav_reinvestment_evidence_id"])
        for evidence in ordered_evidence
        if evidence.get("supersedes_fund_nav_reinvestment_evidence_id")
    }
    current_evidence = [
        evidence
        for evidence in ordered_evidence
        if evidence["fund_nav_event_id"] in current_event_ids
        and evidence["fund_nav_reinvestment_evidence_id"]
        not in superseded_evidence_ids
        and evidence["revision_kind"] != "cancellation"
    ]
    return current_events, ordered_events, current_evidence, ordered_evidence


def _fund_nav_projection_run_to_dict(
    projection_run: FundNavProjectionRun,
) -> dict[str, object]:
    return {
        "fund_nav_projection_run_id": projection_run.fund_nav_projection_run_id,
        "instrument_id": projection_run.instrument_id,
        "input_fingerprint": projection_run.input_fingerprint,
        "source_observation_fingerprint": (
            projection_run.source_observation_fingerprint
        ),
        "projection_kind": projection_run.projection_kind,
        "projection_status": projection_run.projection_status,
        "method_version": projection_run.method_version,
        "anchor_date": (
            projection_run.anchor_date.isoformat()
            if projection_run.anchor_date is not None
            else None
        ),
        "source_provider": projection_run.source_provider,
        "evidence": deepcopy(projection_run.evidence_json),
        "created_by": projection_run.created_by,
        "fund_nav_event_ids": sorted(
            membership.fund_nav_event_id
            for membership in projection_run.event_revisions
        ),
        "fund_nav_reinvestment_evidence_ids": sorted(
            membership.fund_nav_reinvestment_evidence_id
            for membership in projection_run.reinvestment_evidence_revisions
        ),
        "created_at": projection_run.created_at,
    }


def _fund_nav_adjustment_factor_to_dict(
    factor: FundNavAdjustmentFactor,
) -> dict[str, object]:
    return {
        "fund_nav_adjustment_factor_id": factor.fund_nav_adjustment_factor_id,
        "factor_logical_key": factor.factor_logical_key,
        "instrument_id": factor.instrument_id,
        "fund_nav_projection_run_id": factor.fund_nav_projection_run_id,
        "as_of_date": factor.as_of_date.isoformat(),
        "factor_level": _decimal_text(factor.factor_level),
        "factor_kind": factor.factor_kind,
        "fund_nav_event_id": factor.fund_nav_event_id,
        "fund_nav_reinvestment_evidence_id": (
            factor.fund_nav_reinvestment_evidence_id
        ),
        "previous_fund_nav_adjustment_factor_id": (
            factor.previous_fund_nav_adjustment_factor_id
        ),
        "evidence_kind": factor.evidence_kind,
        "method_version": factor.method_version,
        "anchor_date": factor.anchor_date.isoformat(),
        "source_provider": factor.source_provider,
        "evidence": deepcopy(factor.evidence_json),
        "created_at": factor.created_at,
        "updated_at": factor.updated_at,
    }


def _normalized_corporate_actions(item: dict[str, object]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    instrument_id = str(item.get("instrument_id") or "").strip()
    for raw_event in list(item.get("corporate_actions", [])):
        if not isinstance(raw_event, dict):
            continue
        event = dict(raw_event)
        event_id = str(event.get("corporate_action_event_id") or "").strip()
        action_type = str(event.get("action_type") or "").strip()
        effective_date = str(event.get("effective_date") or "").strip()
        new_units = str(event.get("new_units") or "").strip()
        old_units = str(event.get("old_units") or "").strip()
        source = str(event.get("source") or "").strip()
        if not all((event_id, instrument_id, action_type, effective_date, new_units, old_units, source)):
            continue
        normalized.append(
            {
                "corporate_action_event_id": event_id,
                "instrument_id": instrument_id,
                "action_type": action_type,
                "announcement_date": event.get("announcement_date"),
                "record_date": event.get("record_date"),
                "effective_date": effective_date,
                "payable_date": event.get("payable_date"),
                "new_units": new_units,
                "old_units": old_units,
                "quantity_rounding": str(event.get("quantity_rounding") or "exact"),
                "quantity_precision": int(event.get("quantity_precision") or 0),
                "cost_basis_treatment": str(event.get("cost_basis_treatment") or "carry"),
                "source": source,
                "external_event_id": event.get("external_event_id"),
                "status": str(event.get("status") or "confirmed"),
                "provenance": deepcopy(event.get("provenance") or {}),
                "created_at": str(event.get("created_at") or _utcnow_iso()),
                "updated_at": str(event.get("updated_at") or _utcnow_iso()),
            }
        )
    return _sort_corporate_actions(normalized)


def _normalized_market_data(item: dict[str, object]) -> list[dict[str, object]]:
    instrument_id = str(item.get("instrument_id") or "").strip()
    if not instrument_id:
        raise ValueError("Instrument id must not be blank.")
    instrument_currency = normalize_market_data_currency(item.get("currency"))
    instrument_type = normalize_instrument_type(item.get("instrument_type"))
    normalized_points: list[dict[str, object]] = []
    raw_points = item.get("market_data", [])
    if not isinstance(raw_points, list):
        raise ValueError("market_data must be a list.")
    for point_index, raw_point in enumerate(raw_points, start=1):
        if not isinstance(raw_point, dict):
            raise ValueError(f"Market-data row {point_index} must be an object.")
        point = dict(raw_point)
        metric_family = str(point.get("metric_family") or "").strip().lower()
        quote_basis = str(point.get("quote_basis") or "").strip().lower()
        price_unit, price_scale = parse_persisted_price_contract(
            price_unit=point.get("price_unit"),
            price_scale=point.get("price_scale"),
        )
        canonical_unit, canonical_scale = canonical_price_contract(
            instrument_type=instrument_type,
            metric_family=metric_family,
            quote_basis=quote_basis,
        )
        if (price_unit, price_scale) != (canonical_unit, canonical_scale):
            raise ValueError(
                "Persisted market-data price contract does not match its canonical "
                f"instrument identity: {instrument_type}/{metric_family}/{quote_basis}."
            )
        point_currency, point_status, point_value = _validated_market_data_observation(
            instrument_id=instrument_id,
            instrument_type=instrument_type,
            instrument_currency=instrument_currency,
            metric_family=metric_family,
            quote_basis=quote_basis,
            point_currency=point.get("currency"),
            value=point.get("value"),
            status=point.get("status"),
        )
        raw_date = str(point.get("as_of_date") or "").strip()
        try:
            point_date = date.fromisoformat(raw_date)
        except ValueError as error:
            raise ValueError(
                f"Market-data row {point_index} has an invalid as_of_date."
            ) from error
        nav_lineage = _validated_nav_lineage(
            metric_family=metric_family,
            quote_basis=quote_basis,
            raw_lineage=point.get("nav_lineage"),
            status=point_status,
        )
        normalized_points.append(
            {
                "metric_family": metric_family,
                "quote_basis": quote_basis,
                "as_of_date": point_date.isoformat(),
                "value": (
                    _decimal_text(point_value)
                    if instrument_type == "fx"
                    else str(point["value"]).strip()
                ),
                "currency": point_currency,
                "price_unit": price_unit,
                "price_scale": _decimal_text(price_scale),
                "provider": point.get("provider"),
                "status": point_status,
                "nav_lineage": (
                    nav_lineage.model_dump(mode="json")
                    if nav_lineage is not None
                    else None
                ),
            }
        )
    return _sort_market_data(normalized_points)


def _normalize_store(store: dict[str, object]) -> dict[str, object]:
    normalized = {
        "registry_name": str(store.get("registry_name") or DEFAULT_REGISTRY_NAME),
        "instruments": [],
    }
    raw_instruments = store.get("instruments", [])
    if isinstance(raw_instruments, list):
        normalized["instruments"] = list(raw_instruments)
    else:
        normalized["instruments"] = []
    return normalized


def reset_store(
    session_factory: SessionFactory,
    data: dict[str, object] | None = None,
) -> None:
    payload = data if data is not None else EMPTY_STORE
    normalized = _normalize_store(deepcopy(payload))
    with session_factory() as session:
        _save_store_to_db(session, normalized)
        session.commit()


def _serialize_identifier_rows(item: Instrument) -> list[dict[str, object]]:
    return [
        {
            "identifier_type": identifier.identifier_type,
            "identifier_value": identifier.identifier_value,
            "is_primary": bool(identifier.is_primary),
        }
        for identifier in sorted(
            item.identifiers,
            key=lambda identifier: (
                0 if identifier.is_primary else 1,
                identifier.identifier_type,
                identifier.identifier_value,
                identifier.instrument_identifier_id,
            ),
        )
    ]


def _normalized_broker_identifiers(
    item: dict[str, object],
) -> list[dict[str, object]]:
    raw_identifiers = item.get("broker_identifiers", [])
    if not isinstance(raw_identifiers, list):
        raise ValueError("broker_identifiers must be a list.")
    identifiers = [
        BrokerIdentifier.model_validate(raw_identifier)
        for raw_identifier in raw_identifiers
    ]
    seen: set[tuple[str, str, str]] = set()
    primary_count_by_broker: dict[str, int] = {}
    represented_brokers: set[str] = set()
    for identifier in identifiers:
        broker_key = identifier.broker.casefold()
        key = (
            broker_key,
            identifier.identifier_type,
            identifier.identifier_value.casefold(),
        )
        if key in seen:
            raise ValueError("Broker identifiers must be unique.")
        seen.add(key)
        represented_brokers.add(broker_key)
        if identifier.is_primary:
            primary_count_by_broker[broker_key] = (
                primary_count_by_broker.get(broker_key, 0) + 1
            )
    if any(
        primary_count_by_broker.get(broker_key, 0) != 1
        for broker_key in represented_brokers
    ):
        raise ValueError("Each represented broker requires exactly one primary identifier.")
    return [
        {
            **identifier.model_dump(mode="json"),
            "broker": identifier.broker.strip().casefold(),
            "identifier_type": identifier.identifier_type.strip().lower(),
            "identifier_value": identifier.identifier_value.strip().casefold(),
        }
        for identifier in identifiers
    ]


def _validated_instrument_core(
    *,
    instrument_id: str,
    instrument_name: str,
    instrument_type: str,
    currency: str,
    exchange_code: str | None,
    identifiers: list[dict[str, object]],
    broker_identifiers: list[dict[str, object]],
) -> InstrumentCore:
    return InstrumentCore.model_validate(
        {
            "instrument_id": instrument_id,
            "instrument_name": instrument_name,
            "instrument_type": instrument_type,
            "currency": currency,
            "exchange_code": exchange_code,
            "identifiers": identifiers,
            "broker_identifiers": broker_identifiers,
        }
    )


def _serialize_broker_identifier_rows(
    item: Instrument,
) -> list[dict[str, object]]:
    return [
        {
            "broker": identifier.broker,
            "identifier_type": identifier.identifier_type,
            "identifier_value": identifier.identifier_value,
            "is_primary": bool(identifier.is_primary),
        }
        for identifier in sorted(
            item.broker_identifiers,
            key=lambda identifier: (
                identifier.broker.casefold(),
                0 if identifier.is_primary else 1,
                identifier.identifier_type,
                identifier.identifier_value,
                identifier.instrument_broker_identifier_id,
            ),
        )
    ]


def _instrument_to_store_dict(
    item: Instrument,
    *,
    market_data: list[dict[str, object]] | None = None,
    include_fund_nav_ledger: bool = True,
    include_corporate_actions: bool = True,
) -> dict[str, object]:
    identifiers = _serialize_identifier_rows(item)
    broker_identifiers = _serialize_broker_identifier_rows(item)
    resolved_market_data = market_data
    if resolved_market_data is None:
        resolved_market_data = [
            {
                "metric_family": point.metric_family,
                "quote_basis": point.quote_basis,
                "as_of_date": point.as_of_date.isoformat(),
                "value": point.value,
                "currency": point.currency,
                "price_unit": point.price_unit,
                "price_scale": point.price_scale,
                "provider": point.provider,
                "status": point.status,
                "nav_lineage": _serialized_nav_lineage(
                    kind=point.nav_lineage_kind,
                    method_version=point.nav_derivation_method_version,
                    anchor_date=point.nav_derivation_anchor_date,
                    evidence=point.nav_lineage_evidence_json,
                ),
            }
            for point in item.market_data_points
        ]
    corporate_actions = (
        [_corporate_action_to_dict(event) for event in item.corporate_action_events]
        if include_corporate_actions
        else []
    )
    fund_nav_events: list[dict[str, object]] = []
    fund_nav_event_revisions: list[dict[str, object]] = []
    fund_nav_reinvestment_evidence: list[dict[str, object]] = []
    fund_nav_reinvestment_evidence_revisions: list[dict[str, object]] = []
    fund_nav_projection_runs: list[dict[str, object]] = []
    fund_nav_adjustment_factors: list[dict[str, object]] = []
    fund_nav_adjustment_factor_history: list[dict[str, object]] = []
    current_fund_nav_projection_run_id: str | None = None
    if include_fund_nav_ledger:
        (
            fund_nav_events,
            fund_nav_event_revisions,
            fund_nav_reinvestment_evidence,
            fund_nav_reinvestment_evidence_revisions,
        ) = _current_fund_nav_ledger_views(
            (
                _fund_nav_event_to_dict(event)
                for event in item.fund_nav_events
            ),
            (
                _fund_nav_reinvestment_evidence_to_dict(evidence)
                for evidence in item.fund_nav_reinvestment_evidence
            ),
        )
        fund_nav_projection_runs = sorted(
            (
                _fund_nav_projection_run_to_dict(projection_run)
                for projection_run in item.fund_nav_projection_runs
            ),
            key=lambda projection_run: (
                str(projection_run["created_at"]),
                str(projection_run["fund_nav_projection_run_id"]),
            ),
        )
        if item.current_fund_nav_projection is not None:
            current_fund_nav_projection_run_id = (
                item.current_fund_nav_projection.fund_nav_projection_run_id
            )
        fund_nav_adjustment_factor_history = sorted(
            (
                _fund_nav_adjustment_factor_to_dict(factor)
                for factor in item.fund_nav_adjustment_factors
            ),
            key=lambda factor: (
                str(factor["as_of_date"]),
                str(factor["fund_nav_adjustment_factor_id"]),
            ),
        )
        fund_nav_adjustment_factors = [
            factor
            for factor in fund_nav_adjustment_factor_history
            if factor["fund_nav_projection_run_id"]
            == current_fund_nav_projection_run_id
        ]
    _validated_instrument_core(
        instrument_id=item.instrument_id,
        instrument_name=item.instrument_name,
        instrument_type=item.instrument_type,
        currency=item.currency,
        exchange_code=item.exchange_code,
        identifiers=identifiers,
        broker_identifiers=broker_identifiers,
    )
    return {
        "instrument_id": item.instrument_id,
        "instrument_name": item.instrument_name,
        "instrument_type": item.instrument_type,
        "currency": item.currency,
        "exchange_code": item.exchange_code,
        "broker_identifiers": broker_identifiers,
        "identifiers": identifiers,
        "market_data": _sort_market_data(resolved_market_data),
        "corporate_actions": _sort_corporate_actions(corporate_actions),
        "fund_nav_events": fund_nav_events,
        "fund_nav_event_revisions": fund_nav_event_revisions,
        "fund_nav_reinvestment_evidence": fund_nav_reinvestment_evidence,
        "fund_nav_reinvestment_evidence_revisions": (
            fund_nav_reinvestment_evidence_revisions
        ),
        "fund_nav_projection_runs": fund_nav_projection_runs,
        "current_fund_nav_projection_run_id": current_fund_nav_projection_run_id,
        "fund_nav_adjustment_factors": fund_nav_adjustment_factors,
        "fund_nav_adjustment_factor_history": fund_nav_adjustment_factor_history,
        "market_data_updated_at": item.market_data_updated_at,
        "calculation_inputs_updated_at": item.calculation_inputs_updated_at,
        "quote_selection_policy": deepcopy(item.quote_selection_policy_json or {}),
        "source_settings": deepcopy(item.source_settings_json or {}),
        "refresh_status": deepcopy(item.refresh_status_json or {}),
        "lifecycle_state": deepcopy(item.lifecycle_state_json or {}),
    }


def _save_store_to_db(session: Session, data: dict[str, object]) -> None:
    normalized = _normalize_store(data)

    if any(
        session.scalar(select(func.count()).select_from(table))
        for table in (
            FundNavEvent,
            FundNavReinvestmentEvidence,
            FundNavProjectionRun,
            FundNavAdjustmentFactor,
        )
    ):
        raise ValueError(
            "reset_store cannot erase the append-only fund NAV projection ledger"
        )

    session.execute(delete(InstrumentMarketData))
    session.execute(delete(FundNavCurrentProjection))
    session.execute(delete(FundNavAdjustmentFactor))
    session.execute(delete(FundNavProjectionRunReinvestmentEvidence))
    session.execute(delete(FundNavProjectionRunEvent))
    session.execute(delete(FundNavProjectionRun))
    session.execute(delete(FundNavReinvestmentEvidence))
    session.execute(delete(FundNavEvent))
    session.execute(delete(CorporateActionEvent))
    session.execute(delete(InstrumentBrokerIdentifier))
    session.execute(delete(InstrumentIdentifier))
    session.execute(delete(Instrument))

    metadata_record = session.get(RegistryMetadata, "shared")
    reset_watermark = _utcnow_iso()
    if metadata_record is None:
        metadata_record = RegistryMetadata(
            registry_key="shared",
            registry_name=str(normalized.get("registry_name") or DEFAULT_REGISTRY_NAME),
            market_data_updated_at=reset_watermark,
        )
        session.add(metadata_record)
    else:
        metadata_record.registry_name = str(normalized.get("registry_name") or DEFAULT_REGISTRY_NAME)
        metadata_record.market_data_updated_at = reset_watermark

    for raw_item in list(normalized.get("instruments", [])):
        if not isinstance(raw_item, dict):
            raise ValueError("Every instrument payload must be an object.")
        item = dict(raw_item)
        if any(
            item.get(field_name)
            for field_name in (
                "fund_nav_events",
                "fund_nav_event_revisions",
                "fund_nav_reinvestment_evidence",
                "fund_nav_reinvestment_evidence_revisions",
                "fund_nav_projection_runs",
                "fund_nav_adjustment_factors",
                "fund_nav_adjustment_factor_history",
                "current_fund_nav_projection_run_id",
            )
        ):
            raise ValueError(
                "reset_store cannot seed the append-only fund NAV projection ledger"
            )
        instrument_id = str(item.get("instrument_id") or "").strip()
        instrument_name = str(item.get("instrument_name") or "").strip()
        if not instrument_id or not instrument_name:
            raise ValueError("Every instrument requires a non-blank id and name.")
        instrument_type = normalize_instrument_type(item.get("instrument_type"))
        instrument_currency = normalize_market_data_currency(item.get("currency"))
        broker_identifiers = _normalized_broker_identifiers(item)
        _validated_instrument_core(
            instrument_id=instrument_id,
            instrument_name=instrument_name,
            instrument_type=instrument_type,
            currency=instrument_currency,
            exchange_code=(str(item.get("exchange_code") or "").strip().upper() or None),
            identifiers=[
                raw_identifier
                for raw_identifier in list(item.get("identifiers", []))
                if isinstance(raw_identifier, dict)
            ],
            broker_identifiers=broker_identifiers,
        )
        raw_market_data = item.get("market_data", [])
        if isinstance(raw_market_data, list) and any(
            isinstance(raw_point, dict)
            and str(raw_point.get("metric_family") or "").strip().lower() == "nav"
            and str(raw_point.get("quote_basis") or "").strip().lower()
            == "total_return_nav"
            for raw_point in raw_market_data
        ):
            raise ValueError(
                "reset_store cannot seed total_return_nav without an auditable "
                "projection run and adjustment factor; publish it through "
                "publish_fund_nav_history"
            )
        normalized_market_data = _normalized_market_data(item)
        instrument = Instrument(
            instrument_id=instrument_id,
            instrument_name=instrument_name,
            instrument_type=instrument_type,
            currency=instrument_currency,
            exchange_code=(str(item.get("exchange_code") or "").strip().upper() or None),
            quote_selection_policy_json=_normalized_quote_selection_policy(item),
            source_settings_json=_normalized_source_settings(item),
            refresh_status_json=_normalized_refresh_status(item),
            lifecycle_state_json=_normalized_lifecycle_state(item),
            market_data_updated_at=(
                str(item.get("market_data_updated_at")).strip()
                if item.get("market_data_updated_at")
                else (reset_watermark if normalized_market_data else None)
            ),
            calculation_inputs_updated_at=(
                str(item.get("calculation_inputs_updated_at")).strip()
                if item.get("calculation_inputs_updated_at")
                else None
            ),
        )
        session.add(instrument)
        session.flush()

        for raw_identifier in list(item.get("identifiers", [])):
            if not isinstance(raw_identifier, dict):
                raise ValueError(
                    f'Instrument "{instrument.instrument_id}" has a non-object identifier.'
                )
            identifier_value = str(raw_identifier.get("identifier_value") or "").strip()
            identifier_type = str(raw_identifier.get("identifier_type") or "").strip()
            if not identifier_value or not identifier_type:
                raise ValueError(
                    f'Instrument "{instrument.instrument_id}" has an incomplete identifier.'
                )
            session.add(
                InstrumentIdentifier(
                    instrument_id=instrument.instrument_id,
                    identifier_type=identifier_type,
                    identifier_value=identifier_value,
                    is_primary=bool(raw_identifier.get("is_primary")),
                )
            )

        for broker_identifier in broker_identifiers:
            session.add(
                InstrumentBrokerIdentifier(
                    instrument_id=instrument.instrument_id,
                    broker=str(broker_identifier["broker"]),
                    identifier_type=str(broker_identifier["identifier_type"]),
                    identifier_value=str(broker_identifier["identifier_value"]),
                    is_primary=bool(broker_identifier["is_primary"]),
                )
            )

        for raw_point in normalized_market_data:
            try:
                as_of_date = date.fromisoformat(str(raw_point.get("as_of_date") or ""))
            except ValueError as error:
                raise ValueError(
                    f'Instrument "{instrument.instrument_id}" has an invalid market-data date.'
                ) from error
            session.add(
                InstrumentMarketData(
                    instrument_id=instrument.instrument_id,
                    metric_family=str(raw_point.get("metric_family") or "").strip(),
                    quote_basis=str(raw_point.get("quote_basis") or "").strip(),
                    as_of_date=as_of_date,
                    value=str(raw_point["value"]),
                    currency=str(raw_point["currency"]),
                    price_unit=str(raw_point["price_unit"]),
                    price_scale=Decimal(str(raw_point["price_scale"])),
                    provider=(
                        str(raw_point.get("provider")).strip()
                        if raw_point.get("provider") is not None
                        else None
                    ),
                    status=str(raw_point["status"]),
                    **_nav_lineage_columns(
                        _validated_nav_lineage(
                            metric_family=str(raw_point["metric_family"]),
                            quote_basis=str(raw_point["quote_basis"]),
                            raw_lineage=raw_point.get("nav_lineage"),
                            status=str(raw_point["status"]),
                        )
                    ),
                )
            )

        for raw_event in _normalized_corporate_actions(item):
            try:
                effective_date = date.fromisoformat(str(raw_event["effective_date"]))
                announcement_date = (
                    date.fromisoformat(str(raw_event["announcement_date"]))
                    if raw_event.get("announcement_date")
                    else None
                )
                record_date = (
                    date.fromisoformat(str(raw_event["record_date"]))
                    if raw_event.get("record_date")
                    else None
                )
                payable_date = (
                    date.fromisoformat(str(raw_event["payable_date"]))
                    if raw_event.get("payable_date")
                    else None
                )
            except ValueError:
                continue
            session.add(
                CorporateActionEvent(
                    corporate_action_event_id=str(raw_event["corporate_action_event_id"]),
                    instrument_id=instrument.instrument_id,
                    action_type=str(raw_event["action_type"]),
                    announcement_date=announcement_date,
                    record_date=record_date,
                    effective_date=effective_date,
                    payable_date=payable_date,
                    new_units=str(raw_event["new_units"]),
                    old_units=str(raw_event["old_units"]),
                    quantity_rounding=str(raw_event["quantity_rounding"]),
                    quantity_precision=int(raw_event["quantity_precision"]),
                    cost_basis_treatment=str(raw_event["cost_basis_treatment"]),
                    source=str(raw_event["source"]),
                    external_event_id=(
                        str(raw_event["external_event_id"])
                        if raw_event.get("external_event_id")
                        else None
                    ),
                    status=str(raw_event["status"]),
                    provenance_json=deepcopy(raw_event["provenance"]),
                    created_at=str(raw_event["created_at"]),
                    updated_at=str(raw_event["updated_at"]),
                )
            )

def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = slug.strip("-")
    return slug or "instrument"


def _latest_market_data(points: list[dict[str, object]]) -> list[dict[str, object]]:
    latest_by_identity: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    for point in points:
        identity_key = (
            str(point.get("metric_family") or ""),
            str(point.get("quote_basis") or ""),
            str(point.get("currency") or "").strip().upper(),
            str(point.get("price_unit") or "").strip().lower(),
            str(point.get("price_scale") or "").strip(),
        )
        as_of_date = str(point.get("as_of_date") or "")
        current = latest_by_identity.get(identity_key)
        if current is None or as_of_date >= str(current.get("as_of_date") or ""):
            latest_by_identity[identity_key] = point
    return [latest_by_identity[key] for key in sorted(latest_by_identity.keys())]


def _coverage_state(points: list[dict[str, object]]) -> str:
    if not points:
        return "unavailable"
    if all(str(point.get("status") or "") == "unavailable" for point in points):
        return "unavailable"
    if any(str(point.get("status") or "") == "partial" for point in points):
        return "partial"
    if all(str(point.get("status") or "") == "complete" for point in points):
        return "complete"
    return "partial"


def _normalized_source_settings(item: dict[str, object]) -> dict[str, object]:
    source_settings = dict(
        _default_source_settings(
            instrument_type=str(item.get("instrument_type") or "other"),
            identifiers=(
                identifier
                for identifier in list(item.get("identifiers", []))
                if isinstance(identifier, dict)
            ),
        )
    )
    source_settings.update(dict(item.get("source_settings", {})))
    raw_rules = source_settings.get("source_email_rules", [])
    if isinstance(raw_rules, list):
        source_settings["source_email_rules"] = [
            dict(rule) for rule in raw_rules if isinstance(rule, dict)
        ]
    else:
        source_settings["source_email_rules"] = []
    return SourceSettings.model_validate(source_settings).model_dump()


def _normalized_refresh_status(item: dict[str, object]) -> dict[str, object]:
    source_settings = _normalized_source_settings(item)
    refresh_status = dict(_default_refresh_status(str(source_settings.get("source_mode") or "manual")))
    refresh_status.update(dict(item.get("refresh_status", {})))
    refresh_status["mode"] = str(refresh_status.get("mode") or source_settings.get("source_mode") or "manual")
    return refresh_status


def _normalized_lifecycle_state(item: dict[str, object]) -> dict[str, object]:
    raw_lifecycle = item.get("lifecycle_state")
    status = "active"
    changed_at: str | None = None
    changed_by: str | None = None
    canonical_instrument_id: str | None = None

    if isinstance(raw_lifecycle, dict):
        status = str(raw_lifecycle.get("status") or "active").strip().lower() or "active"
        raw_changed_at = raw_lifecycle.get("changed_at")
        raw_changed_by = raw_lifecycle.get("changed_by")
        raw_canonical_id = raw_lifecycle.get("canonical_instrument_id")
        changed_at = str(raw_changed_at).strip() if raw_changed_at else None
        changed_by = str(raw_changed_by).strip() if raw_changed_by else None
        canonical_instrument_id = (
            str(raw_canonical_id).strip() if raw_canonical_id else None
        )
    elif item.get("is_active") is False:
        status = "archived"

    return _default_lifecycle_state(
        status=status,
        changed_at=changed_at,
        changed_by=changed_by,
        canonical_instrument_id=canonical_instrument_id,
    )


def _is_active(item: dict[str, object]) -> bool:
    return str(_normalized_lifecycle_state(item).get("status") or "active") == "active"


def _serialize_record(item: dict[str, object]) -> dict[str, object]:
    market_data = _normalized_market_data(item)
    return {
        "instrument_id": item["instrument_id"],
        "instrument_name": item["instrument_name"],
        "instrument_type": item["instrument_type"],
        "currency": item["currency"],
        "exchange_code": item.get("exchange_code"),
        "broker_identifiers": deepcopy(item.get("broker_identifiers", [])),
        "identifiers": deepcopy(item.get("identifiers", [])),
        "latest_market_data": _latest_market_data(market_data),
        "quote_selection_policy": _normalized_quote_selection_policy(item),
        "coverage_state": _coverage_state(market_data),
        "market_data_updated_at": item.get("market_data_updated_at"),
        "calculation_inputs_updated_at": item.get("calculation_inputs_updated_at"),
        "source_settings": _normalized_source_settings(item),
        "refresh_status": _normalized_refresh_status(item),
        "lifecycle_state": _normalized_lifecycle_state(item),
        "corporate_actions": deepcopy(item.get("corporate_actions", [])),
    }


def _serialize_detail_record(item: dict[str, object]) -> dict[str, object]:
    record = _serialize_record(item)
    record["market_data"] = deepcopy(_normalized_market_data(item))
    for field_name in (
        "fund_nav_events",
        "fund_nav_event_revisions",
        "fund_nav_reinvestment_evidence",
        "fund_nav_reinvestment_evidence_revisions",
        "fund_nav_projection_runs",
        "fund_nav_adjustment_factors",
        "fund_nav_adjustment_factor_history",
    ):
        record[field_name] = deepcopy(item.get(field_name, []))
    record["current_fund_nav_projection_run_id"] = item.get(
        "current_fund_nav_projection_run_id"
    )
    return record


def _instrument_query(
    *,
    include_market_data: bool = True,
    include_fund_nav_ledger: bool = True,
):
    eager_loads = [
        selectinload(Instrument.identifiers),
        selectinload(Instrument.broker_identifiers),
        selectinload(Instrument.corporate_action_events),
    ]
    if include_fund_nav_ledger:
        eager_loads.extend(
            [
                selectinload(Instrument.fund_nav_events),
                selectinload(Instrument.fund_nav_reinvestment_evidence),
                selectinload(Instrument.fund_nav_projection_runs).selectinload(
                    FundNavProjectionRun.event_revisions
                ),
                selectinload(Instrument.fund_nav_projection_runs).selectinload(
                    FundNavProjectionRun.reinvestment_evidence_revisions
                ),
                selectinload(Instrument.current_fund_nav_projection),
                selectinload(Instrument.fund_nav_adjustment_factors),
            ]
        )
    if include_market_data:
        eager_loads.append(selectinload(Instrument.market_data_points))
    return (
        select(Instrument)
        .options(*eager_loads)
    )


def _latest_market_data_for_instruments(
    session: Session,
    instrument_ids: list[str],
) -> dict[str, list[dict[str, object]]]:
    if not instrument_ids:
        return {}

    ranked = (
        select(
            InstrumentMarketData.instrument_id.label("instrument_id"),
            InstrumentMarketData.metric_family.label("metric_family"),
            InstrumentMarketData.quote_basis.label("quote_basis"),
            InstrumentMarketData.as_of_date.label("as_of_date"),
            InstrumentMarketData.value.label("value"),
            InstrumentMarketData.currency.label("currency"),
            InstrumentMarketData.price_unit.label("price_unit"),
            InstrumentMarketData.price_scale.label("price_scale"),
            InstrumentMarketData.provider.label("provider"),
            InstrumentMarketData.status.label("status"),
            InstrumentMarketData.nav_lineage_kind.label("nav_lineage_kind"),
            InstrumentMarketData.nav_derivation_method_version.label(
                "nav_derivation_method_version"
            ),
            InstrumentMarketData.nav_derivation_anchor_date.label(
                "nav_derivation_anchor_date"
            ),
            InstrumentMarketData.nav_lineage_evidence_json.label(
                "nav_lineage_evidence_json"
            ),
            func.row_number()
            .over(
                partition_by=(
                    InstrumentMarketData.instrument_id,
                    InstrumentMarketData.metric_family,
                    InstrumentMarketData.quote_basis,
                    InstrumentMarketData.currency,
                    InstrumentMarketData.price_unit,
                    InstrumentMarketData.price_scale,
                ),
                order_by=(
                    InstrumentMarketData.as_of_date.desc(),
                    InstrumentMarketData.instrument_market_data_id.desc(),
                ),
            )
            .label("row_number"),
        )
        .where(InstrumentMarketData.instrument_id.in_(instrument_ids))
        .subquery()
    )
    rows = session.execute(
        select(ranked).where(ranked.c.row_number == 1)
    ).mappings()
    result: dict[str, list[dict[str, object]]] = {instrument_id: [] for instrument_id in instrument_ids}
    for row in rows:
        result[str(row["instrument_id"])].append(
            {
                "metric_family": row["metric_family"],
                "quote_basis": row["quote_basis"],
                "as_of_date": row["as_of_date"].isoformat(),
                "value": row["value"],
                "currency": row["currency"],
                "price_unit": row["price_unit"],
                "price_scale": row["price_scale"],
                "provider": row["provider"],
                "status": row["status"],
                "nav_lineage": _serialized_nav_lineage(
                    kind=row["nav_lineage_kind"],
                    method_version=row["nav_derivation_method_version"],
                    anchor_date=row["nav_derivation_anchor_date"],
                    evidence=row["nav_lineage_evidence_json"],
                ),
            }
        )
    return result


def list_instruments(
    session_factory: SessionFactory,
    *,
    search: str | None = None,
    instrument_type: str | None = None,
    limit: int | None = None,
    include_inactive: bool = False,
) -> list[dict[str, object]]:
    normalized_search = search.strip().lower() if search else ""
    normalized_instrument_type = instrument_type.strip().lower() if instrument_type else None

    with session_factory() as session:
        statement = _instrument_query(
            include_market_data=False,
            include_fund_nav_ledger=False,
        )
        if not include_inactive:
            lifecycle_status = Instrument.lifecycle_state_json["status"].as_string()
            statement = statement.where(func.coalesce(lifecycle_status, "active") != "archived")
        if normalized_instrument_type:
            statement = statement.where(func.lower(Instrument.instrument_type) == normalized_instrument_type)
        if normalized_search:
            pattern = f"%{normalized_search}%"
            statement = statement.where(
                or_(
                    func.lower(Instrument.instrument_id).like(pattern),
                    func.lower(Instrument.instrument_name).like(pattern),
                    func.lower(Instrument.instrument_type).like(pattern),
                    func.lower(Instrument.currency).like(pattern),
                    Instrument.identifiers.any(
                        or_(
                            func.lower(InstrumentIdentifier.identifier_type).like(pattern),
                            func.lower(InstrumentIdentifier.identifier_value).like(pattern),
                        )
                    ),
                )
            )
        statement = statement.order_by(Instrument.instrument_name, Instrument.instrument_id)
        if limit is not None:
            statement = statement.limit(limit)
        instrument_rows = list(session.scalars(statement).all())
        instrument_ids = [item.instrument_id for item in instrument_rows]
        latest_market_data = _latest_market_data_for_instruments(session, instrument_ids)
        return [
            _serialize_record(
                _instrument_to_store_dict(
                    item,
                    market_data=latest_market_data.get(item.instrument_id, []),
                    include_fund_nav_ledger=False,
                )
            )
            for item in instrument_rows
        ]


def list_active_instrument_ids(
    session_factory: SessionFactory,
    *,
    instrument_types: Iterable[str] | None = None,
) -> list[str]:
    """Return the active registry membership without loading instrument details.

    This intentionally reads only the instrument table. Consumers that merely
    need to detect membership drift should not pay for identifiers, corporate
    actions, or latest-market-data hydration performed by ``list_instruments``.
    """

    normalized_types = {
        str(instrument_type).strip().lower()
        for instrument_type in (instrument_types or [])
        if str(instrument_type).strip()
    }
    if instrument_types is not None and not normalized_types:
        return []

    with session_factory() as session:
        lifecycle_status = Instrument.lifecycle_state_json["status"].as_string()
        statement = select(Instrument.instrument_id).where(
            func.coalesce(lifecycle_status, "active") != "archived"
        )
        if normalized_types:
            statement = statement.where(
                func.lower(Instrument.instrument_type).in_(normalized_types)
            )
        statement = statement.order_by(Instrument.instrument_id)
        return [str(instrument_id) for instrument_id in session.scalars(statement).all()]


def list_instrument_ids_with_nav_history_before(
    session_factory: SessionFactory,
    *,
    instrument_ids: Iterable[str],
    before_date: date,
) -> set[str]:
    """Return instruments whose canonical NAV publication crosses a date boundary."""

    normalized_ids = {
        str(instrument_id).strip()
        for instrument_id in instrument_ids
        if str(instrument_id).strip()
    }
    if not normalized_ids:
        return set()
    with session_factory() as session:
        statement = (
            select(InstrumentMarketData.instrument_id)
            .where(
                InstrumentMarketData.instrument_id.in_(normalized_ids),
                InstrumentMarketData.metric_family == "nav",
                InstrumentMarketData.as_of_date < before_date,
            )
            .distinct()
        )
        return {
            str(instrument_id)
            for instrument_id in session.scalars(statement).all()
        }


def list_stale_current_fund_nav_projections(
    session_factory: SessionFactory,
    *,
    method_version: str,
    include_inactive: bool = False,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    """Return exact current projection rows that predate the deployed method."""

    normalized_method_version = str(method_version or "").strip()
    if not normalized_method_version:
        raise ValueError("method_version is required.")
    normalized_ids = {
        str(instrument_id).strip()
        for instrument_id in (instrument_ids or [])
        if str(instrument_id).strip()
    }
    if instrument_ids is not None and not normalized_ids:
        return []

    with session_factory() as session:
        statement = (
            select(
                FundNavCurrentProjection.instrument_id,
                FundNavProjectionRun.method_version,
                FundNavProjectionRun.source_provider,
            )
            .join(
                FundNavProjectionRun,
                FundNavProjectionRun.fund_nav_projection_run_id
                == FundNavCurrentProjection.fund_nav_projection_run_id,
            )
            .join(
                Instrument,
                Instrument.instrument_id == FundNavCurrentProjection.instrument_id,
            )
            .where(FundNavProjectionRun.method_version != normalized_method_version)
        )
        if not include_inactive:
            lifecycle_status = Instrument.lifecycle_state_json["status"].as_string()
            statement = statement.where(
                func.coalesce(lifecycle_status, "active") != "archived"
            )
        if normalized_ids:
            statement = statement.where(
                FundNavCurrentProjection.instrument_id.in_(normalized_ids)
            )
        rows = session.execute(
            statement.order_by(FundNavCurrentProjection.instrument_id)
        ).all()
        return [
            {
                "instrument_id": str(row.instrument_id),
                "method_version": str(row.method_version),
                "source_provider": str(row.source_provider),
            }
            for row in rows
        ]


def get_instrument(session_factory: SessionFactory, instrument_id: str) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.scalar(
            _instrument_query()
            .where(Instrument.instrument_id == instrument_id)
        )
        if target is None:
            return None
        return _serialize_detail_record(_instrument_to_store_dict(target))


def get_instrument_details(
    session_factory: SessionFactory,
    instrument_ids: list[str] | set[str] | tuple[str, ...],
    *,
    include_fund_nav_ledger: bool = True,
) -> dict[str, dict[str, object] | None]:
    """Load instrument market histories for a set of ids in one registry session.

    The returned mapping includes missing ids with a ``None`` value so callers
    can use it as a complete request-scoped cache without falling back to an
    accidental per-instrument query loop. Fund NAV audit ledgers can be omitted
    for calculation paths that only consume instrument identity and market data.
    """
    normalized_ids: list[str] = []
    for raw_instrument_id in instrument_ids:
        instrument_id = str(raw_instrument_id or "").strip()
        if instrument_id and instrument_id not in normalized_ids:
            normalized_ids.append(instrument_id)
    if not normalized_ids:
        return {}

    with session_factory() as session:
        targets = session.scalars(
            _instrument_query(
                include_fund_nav_ledger=include_fund_nav_ledger,
            ).where(Instrument.instrument_id.in_(normalized_ids))
        ).all()
        details_by_id = {
            target.instrument_id: _serialize_detail_record(
                _instrument_to_store_dict(
                    target,
                    include_fund_nav_ledger=include_fund_nav_ledger,
                )
            )
            for target in targets
        }
    return {
        instrument_id: details_by_id.get(instrument_id)
        for instrument_id in normalized_ids
    }


def get_instrument_event_details(
    session_factory: SessionFactory,
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, dict[str, object] | None]:
    """Load only event ledgers needed by downstream accounting consumers."""

    normalized_ids: list[str] = []
    for raw_instrument_id in instrument_ids:
        instrument_id = str(raw_instrument_id or "").strip()
        if instrument_id and instrument_id not in normalized_ids:
            normalized_ids.append(instrument_id)
    if not normalized_ids:
        return {}

    with session_factory() as session:
        existing_ids = {
            str(instrument_id)
            for instrument_id in session.scalars(
                select(Instrument.instrument_id).where(
                    Instrument.instrument_id.in_(normalized_ids)
                )
            ).all()
        }
        event_rows = list(
            session.scalars(
                select(FundNavEvent).where(
                    FundNavEvent.instrument_id.in_(normalized_ids)
                )
            ).all()
        )
        evidence_rows = list(
            session.scalars(
                select(FundNavReinvestmentEvidence).where(
                    FundNavReinvestmentEvidence.instrument_id.in_(normalized_ids)
                )
            ).all()
        )
        corporate_action_rows = list(
            session.scalars(
                select(CorporateActionEvent).where(
                    CorporateActionEvent.instrument_id.in_(normalized_ids)
                )
            ).all()
        )

    events_by_instrument: dict[str, list[dict[str, object]]] = {}
    for event in event_rows:
        events_by_instrument.setdefault(event.instrument_id, []).append(
            _fund_nav_event_to_dict(event)
        )
    evidence_by_instrument: dict[str, list[dict[str, object]]] = {}
    for evidence in evidence_rows:
        evidence_by_instrument.setdefault(evidence.instrument_id, []).append(
            _fund_nav_reinvestment_evidence_to_dict(evidence)
        )
    actions_by_instrument: dict[str, list[dict[str, object]]] = {}
    for action in corporate_action_rows:
        actions_by_instrument.setdefault(action.instrument_id, []).append(
            _corporate_action_to_dict(action)
        )

    details_by_id: dict[str, dict[str, object]] = {}
    for instrument_id in existing_ids:
        (
            current_events,
            event_revisions,
            current_evidence,
            evidence_revisions,
        ) = _current_fund_nav_ledger_views(
            events_by_instrument.get(instrument_id, []),
            evidence_by_instrument.get(instrument_id, []),
        )
        details_by_id[instrument_id] = {
            "corporate_actions": _sort_corporate_actions(
                actions_by_instrument.get(instrument_id, [])
            ),
            "fund_nav_events": current_events,
            "fund_nav_event_revisions": event_revisions,
            "fund_nav_reinvestment_evidence": current_evidence,
            "fund_nav_reinvestment_evidence_revisions": evidence_revisions,
        }

    return {
        instrument_id: details_by_id.get(instrument_id)
        for instrument_id in normalized_ids
    }


def get_instrument_summaries(
    session_factory: SessionFactory,
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, dict[str, object] | None]:
    """Load bounded summaries without hydrating histories or NAV ledgers."""

    normalized_ids: list[str] = []
    for raw_instrument_id in instrument_ids:
        instrument_id = str(raw_instrument_id or "").strip()
        if instrument_id and instrument_id not in normalized_ids:
            normalized_ids.append(instrument_id)
    if not normalized_ids:
        return {}

    with session_factory() as session:
        targets = session.scalars(
            select(Instrument)
            .options(
                selectinload(Instrument.identifiers),
                selectinload(Instrument.broker_identifiers),
            )
            .where(Instrument.instrument_id.in_(normalized_ids))
        ).all()
        latest_market_data = _latest_market_data_for_instruments(
            session,
            [target.instrument_id for target in targets],
        )
        summaries_by_id: dict[str, dict[str, object]] = {}
        for target in targets:
            summary = _serialize_record(
                _instrument_to_store_dict(
                    target,
                    market_data=latest_market_data.get(target.instrument_id, []),
                    include_fund_nav_ledger=False,
                    include_corporate_actions=False,
                )
            )
            summary.pop("corporate_actions", None)
            summaries_by_id[target.instrument_id] = summary
    return {
        instrument_id: summaries_by_id.get(instrument_id)
        for instrument_id in normalized_ids
    }


def _canonical_fingerprint_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _canonical_fingerprint_value(value[key])
            for key in sorted(value, key=str)
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_fingerprint_value(item) for item in value]
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError(
        f"Projection fingerprint contains unsupported {type(value).__name__}."
    )


def _normalized_revision_ids(values: Iterable[str], *, label: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be an iterable of revision ids.")
    normalized = [str(value or "").strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError(f"{label} contains a blank revision id.")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} contains duplicate revision ids.")
    return sorted(normalized)


def _normalize_fund_nav_projection_input(
    *,
    instrument_id: str,
    event_revisions: Iterable[dict[str, object]],
    reinvestment_evidence_revisions: Iterable[dict[str, object]],
    projection_run: dict[str, object],
    current_fund_nav_event_ids: Iterable[str],
    current_fund_nav_reinvestment_evidence_ids: Iterable[str],
    adjustment_factors: Iterable[dict[str, object]],
) -> tuple[
    list[FundNavEventModel],
    list[FundNavReinvestmentEvidenceModel],
    FundNavProjectionRunModel,
    list[FundNavAdjustmentFactorModel],
    list[str],
    list[str],
]:
    now = _utcnow_iso()
    event_models: list[FundNavEventModel] = []
    event_ids: set[str] = set()
    for index, raw_event in enumerate(event_revisions, start=1):
        if not isinstance(raw_event, dict):
            raise ValueError(f"Fund NAV event revision {index} must be an object.")
        supplied_instrument_id = str(raw_event.get("instrument_id") or "").strip()
        if supplied_instrument_id and supplied_instrument_id != instrument_id:
            raise ValueError(
                f"Fund NAV event revision {index} belongs to another instrument."
            )
        event = FundNavEventModel.model_validate(
            {
                **deepcopy(raw_event),
                "instrument_id": instrument_id,
                "created_at": raw_event.get("created_at") or now,
                "updated_at": raw_event.get("updated_at") or now,
            }
        )
        if event.fund_nav_event_id in event_ids:
            raise ValueError(
                f'Duplicate fund NAV event revision id "{event.fund_nav_event_id}".'
            )
        event_ids.add(event.fund_nav_event_id)
        event_models.append(event)

    evidence_models: list[FundNavReinvestmentEvidenceModel] = []
    evidence_ids: set[str] = set()
    for index, raw_evidence in enumerate(reinvestment_evidence_revisions, start=1):
        if not isinstance(raw_evidence, dict):
            raise ValueError(
                f"Fund NAV reinvestment evidence revision {index} must be an object."
            )
        supplied_instrument_id = str(
            raw_evidence.get("instrument_id") or ""
        ).strip()
        if supplied_instrument_id and supplied_instrument_id != instrument_id:
            raise ValueError(
                f"Fund NAV reinvestment evidence revision {index} belongs to another instrument."
            )
        evidence = FundNavReinvestmentEvidenceModel.model_validate(
            {
                **deepcopy(raw_evidence),
                "instrument_id": instrument_id,
                "created_at": raw_evidence.get("created_at") or now,
                "updated_at": raw_evidence.get("updated_at") or now,
            }
        )
        if evidence.fund_nav_reinvestment_evidence_id in evidence_ids:
            raise ValueError(
                "Duplicate fund NAV reinvestment evidence revision id "
                f'"{evidence.fund_nav_reinvestment_evidence_id}".'
            )
        evidence_ids.add(evidence.fund_nav_reinvestment_evidence_id)
        evidence_models.append(evidence)

    current_event_ids = _normalized_revision_ids(
        current_fund_nav_event_ids,
        label="current_fund_nav_event_ids",
    )
    current_evidence_ids = _normalized_revision_ids(
        current_fund_nav_reinvestment_evidence_ids,
        label="current_fund_nav_reinvestment_evidence_ids",
    )
    if not isinstance(projection_run, dict):
        raise ValueError("projection_run must be an object.")
    allowed_projection_fields = {
        "instrument_id",
        "fund_nav_projection_run_id",
        "input_fingerprint",
        "source_observation_fingerprint",
        "projection_kind",
        "projection_status",
        "method_version",
        "anchor_date",
        "source_provider",
        "evidence",
        "created_by",
        "created_at",
    }
    unexpected = sorted(set(projection_run).difference(allowed_projection_fields))
    if unexpected:
        raise ValueError(
            "projection_run has unsupported fields: " + ", ".join(unexpected)
        )
    supplied_instrument_id = str(projection_run.get("instrument_id") or "").strip()
    if supplied_instrument_id and supplied_instrument_id != instrument_id:
        raise ValueError("projection_run belongs to another instrument.")
    projection_input = {
        "contract": "fund_nav_projection_input/v1",
        "instrument_id": instrument_id,
        "source_observation_fingerprint": str(
            projection_run.get("source_observation_fingerprint") or ""
        ).strip().lower(),
        "projection_kind": str(projection_run.get("projection_kind") or "").strip(),
        "projection_status": str(
            projection_run.get("projection_status") or ""
        ).strip(),
        "method_version": str(projection_run.get("method_version") or "").strip(),
        "anchor_date": projection_run.get("anchor_date"),
        "source_provider": str(projection_run.get("source_provider") or "").strip(),
        "evidence": deepcopy(projection_run.get("evidence")),
        "current_fund_nav_event_ids": current_event_ids,
        "current_fund_nav_reinvestment_evidence_ids": current_evidence_ids,
    }
    canonical_input = json.dumps(
        _canonical_fingerprint_value(projection_input),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    input_fingerprint = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
    supplied_fingerprint = str(projection_run.get("input_fingerprint") or "").strip()
    if supplied_fingerprint and supplied_fingerprint != input_fingerprint:
        raise ValueError("projection_run input_fingerprint is not deterministic.")
    run_id = f"fund-nav-projection-{input_fingerprint}"
    supplied_run_id = str(
        projection_run.get("fund_nav_projection_run_id") or ""
    ).strip()
    if supplied_run_id and supplied_run_id != run_id:
        raise ValueError("projection_run id is not deterministic.")
    run_model = FundNavProjectionRunModel.model_validate(
        {
            **deepcopy(projection_run),
            "instrument_id": instrument_id,
            "input_fingerprint": input_fingerprint,
            "fund_nav_projection_run_id": run_id,
            "source_observation_fingerprint": projection_input[
                "source_observation_fingerprint"
            ],
            "created_at": projection_run.get("created_at") or now,
        }
    )

    raw_factor_payloads = list(adjustment_factors)
    factor_id_by_logical_key: dict[str, str] = {}
    for index, raw_factor in enumerate(raw_factor_payloads, start=1):
        if not isinstance(raw_factor, dict):
            raise ValueError(f"Fund NAV adjustment factor {index} must be an object.")
        logical_key = str(raw_factor.get("factor_logical_key") or "").strip()
        if not logical_key:
            raise ValueError(
                f"Fund NAV adjustment factor {index} requires factor_logical_key."
            )
        if logical_key in factor_id_by_logical_key:
            raise ValueError(f'Duplicate factor_logical_key "{logical_key}".')
        factor_id_by_logical_key[logical_key] = (
            deterministic_fund_nav_adjustment_factor_id(
                fund_nav_projection_run_id=run_id,
                factor_logical_key=logical_key,
            )
        )

    factor_models: list[FundNavAdjustmentFactorModel] = []
    for index, raw_factor in enumerate(raw_factor_payloads, start=1):
        assert isinstance(raw_factor, dict)
        supplied_instrument_id = str(raw_factor.get("instrument_id") or "").strip()
        supplied_run_id = str(
            raw_factor.get("fund_nav_projection_run_id") or ""
        ).strip()
        if supplied_instrument_id and supplied_instrument_id != instrument_id:
            raise ValueError(
                f"Fund NAV adjustment factor {index} belongs to another instrument."
            )
        if supplied_run_id and supplied_run_id != run_id:
            raise ValueError(
                f"Fund NAV adjustment factor {index} belongs to another projection run."
            )
        logical_key = str(raw_factor["factor_logical_key"]).strip()
        factor_id = factor_id_by_logical_key[logical_key]
        supplied_factor_id = str(
            raw_factor.get("fund_nav_adjustment_factor_id") or ""
        ).strip()
        if supplied_factor_id and supplied_factor_id != factor_id:
            raise ValueError(
                f"Fund NAV adjustment factor {index} id is not deterministic."
            )
        previous_logical_key = str(
            raw_factor.get("previous_factor_logical_key") or ""
        ).strip()
        previous_factor_id = (
            factor_id_by_logical_key.get(previous_logical_key)
            if previous_logical_key
            else None
        )
        if previous_logical_key and previous_factor_id is None:
            raise ValueError(
                f'Unknown previous_factor_logical_key "{previous_logical_key}".'
            )
        supplied_previous_id = str(
            raw_factor.get("previous_fund_nav_adjustment_factor_id") or ""
        ).strip()
        if supplied_previous_id and supplied_previous_id != previous_factor_id:
            raise ValueError(
                f"Fund NAV adjustment factor {index} predecessor does not match its logical key."
            )
        normalized_factor = deepcopy(raw_factor)
        normalized_factor.pop("previous_factor_logical_key", None)
        factor = FundNavAdjustmentFactorModel.model_validate(
            {
                **normalized_factor,
                "fund_nav_adjustment_factor_id": factor_id,
                "previous_fund_nav_adjustment_factor_id": previous_factor_id,
                "instrument_id": instrument_id,
                "fund_nav_projection_run_id": run_id,
                "created_at": raw_factor.get("created_at") or now,
                "updated_at": raw_factor.get("updated_at") or now,
            }
        )
        factor_models.append(factor)
    return (
        event_models,
        evidence_models,
        run_model,
        factor_models,
        current_event_ids,
        current_evidence_ids,
    )


def _same_decimal(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is right
    return _quantized_fund_nav_factor(Decimal(str(left))) == _quantized_fund_nav_factor(
        Decimal(str(right))
    )


def _event_revision_values(event: object) -> tuple[object, ...]:
    return (
        getattr(event, "fund_nav_action_id"),
        getattr(event, "revision_number"),
        getattr(event, "revision_kind"),
        getattr(event, "supersedes_fund_nav_event_id"),
        getattr(event, "instrument_id"),
        getattr(event, "event_type"),
        getattr(event, "announcement_date"),
        getattr(event, "record_date"),
        getattr(event, "effective_date"),
        getattr(event, "payable_date"),
        getattr(event, "sequence_order"),
        Decimal(str(getattr(event, "cash_per_unit")))
        if getattr(event, "cash_per_unit") is not None
        else None,
        Decimal(str(getattr(event, "unit_ratio")))
        if getattr(event, "unit_ratio") is not None
        else None,
        getattr(event, "evidence_kind"),
        getattr(event, "source"),
        getattr(event, "external_event_id"),
        deepcopy(
            getattr(event, "provenance_json", getattr(event, "provenance", None))
        ),
        getattr(event, "recorded_by"),
        getattr(event, "revision_reason"),
    )


def _append_fund_nav_event_revisions(
    session: Session,
    *,
    instrument_id: str,
    incoming_revisions: list[FundNavEventModel],
    expected_current_ids: list[str],
) -> tuple[bool, dict[str, FundNavEvent]]:
    records = list(
        session.scalars(
            select(FundNavEvent).where(FundNavEvent.instrument_id == instrument_id)
        ).all()
    )
    by_id = {record.fund_nav_event_id: record for record in records}
    by_action_revision = {
        (record.fund_nav_action_id, record.revision_number): record
        for record in records
    }
    successor_by_predecessor = {
        record.supersedes_fund_nav_event_id: record
        for record in records
        if record.supersedes_fund_nav_event_id is not None
    }
    candidate_by_id: dict[str, FundNavEvent | FundNavEventModel] = dict(by_id)
    candidate_by_id.update(
        {revision.fund_nav_event_id: revision for revision in incoming_revisions}
    )
    expected_heads = [
        candidate_by_id[revision_id]
        for revision_id in expected_current_ids
        if revision_id in candidate_by_id
    ]
    expected_heads_by_date: dict[date, list[FundNavEvent | FundNavEventModel]] = {}
    for record in expected_heads:
        expected_heads_by_date.setdefault(record.effective_date, []).append(record)
    for action_date, same_day_actions in expected_heads_by_date.items():
        if len(same_day_actions) < 2:
            continue
        sequence_orders = [record.sequence_order for record in same_day_actions]
        if any(order is None for order in sequence_orders) or len(
            set(sequence_orders)
        ) != len(sequence_orders):
            raise ValueError(
                "Multiple fund NAV actions on "
                f"{action_date.isoformat()} require unique explicit sequence_order values."
            )
    changed = False
    for incoming in sorted(
        incoming_revisions,
        key=lambda item: (item.fund_nav_action_id, item.revision_number),
    ):
        existing = by_id.get(incoming.fund_nav_event_id)
        if existing is not None:
            if _event_revision_values(existing) != _event_revision_values(incoming):
                raise ValueError(
                    f'Fund NAV event revision "{incoming.fund_nav_event_id}" is immutable.'
                )
            continue
        key = (incoming.fund_nav_action_id, incoming.revision_number)
        if key in by_action_revision:
            raise ValueError(
                "Fund NAV action revision number already belongs to another revision."
            )
        predecessor: FundNavEvent | None = None
        if incoming.supersedes_fund_nav_event_id is not None:
            predecessor = by_id.get(incoming.supersedes_fund_nav_event_id)
            if predecessor is None:
                raise ValueError("Fund NAV event predecessor does not exist.")
            if (
                predecessor.instrument_id != instrument_id
                or predecessor.fund_nav_action_id != incoming.fund_nav_action_id
                or predecessor.event_type != incoming.event_type
                or incoming.revision_number != predecessor.revision_number + 1
            ):
                raise ValueError(
                    "Fund NAV event revisions require the same action/instrument/type and consecutive revision numbers."
                )
            if predecessor.fund_nav_event_id in successor_by_predecessor:
                raise ValueError("Fund NAV event revision chain cannot branch.")
            if incoming.revision_kind == "cancellation":
                inherited_payload = (
                    predecessor.announcement_date,
                    predecessor.record_date,
                    predecessor.effective_date,
                    predecessor.payable_date,
                    predecessor.sequence_order,
                    predecessor.cash_per_unit,
                    predecessor.unit_ratio,
                )
                cancellation_payload = (
                    incoming.announcement_date,
                    incoming.record_date,
                    incoming.effective_date,
                    incoming.payable_date,
                    incoming.sequence_order,
                    incoming.cash_per_unit,
                    incoming.unit_ratio,
                )
                if inherited_payload != cancellation_payload:
                    raise ValueError(
                        "Fund NAV event cancellation must preserve its predecessor payload."
                    )
        record = FundNavEvent(
            fund_nav_event_id=incoming.fund_nav_event_id,
            fund_nav_action_id=incoming.fund_nav_action_id,
            revision_number=incoming.revision_number,
            revision_kind=incoming.revision_kind,
            supersedes_fund_nav_event_id=incoming.supersedes_fund_nav_event_id,
            instrument_id=instrument_id,
            event_type=incoming.event_type,
            announcement_date=incoming.announcement_date,
            record_date=incoming.record_date,
            effective_date=incoming.effective_date,
            payable_date=incoming.payable_date,
            sequence_order=incoming.sequence_order,
            cash_per_unit=incoming.cash_per_unit,
            unit_ratio=incoming.unit_ratio,
            evidence_kind=incoming.evidence_kind,
            source=incoming.source,
            external_event_id=incoming.external_event_id,
            provenance_json=deepcopy(incoming.provenance),
            recorded_by=incoming.recorded_by,
            revision_reason=incoming.revision_reason,
            created_at=incoming.created_at,
            updated_at=incoming.updated_at,
        )
        session.add(record)
        session.flush()
        by_id[record.fund_nav_event_id] = record
        by_action_revision[key] = record
        if predecessor is not None:
            successor_by_predecessor[predecessor.fund_nav_event_id] = record
        changed = True

    heads = {
        record.fund_nav_event_id: record
        for record in by_id.values()
        if record.fund_nav_event_id not in successor_by_predecessor
    }
    active_heads = {
        revision_id: record
        for revision_id, record in heads.items()
        if record.revision_kind != "cancellation"
    }
    if set(expected_current_ids) != set(active_heads):
        raise ValueError(
            "current_fund_nav_event_ids must exactly match the active action heads."
        )
    by_date: dict[date, list[FundNavEvent]] = {}
    for record in active_heads.values():
        by_date.setdefault(record.effective_date, []).append(record)
    for action_date, same_day_actions in by_date.items():
        if len(same_day_actions) < 2:
            continue
        sequence_orders = [record.sequence_order for record in same_day_actions]
        if any(order is None for order in sequence_orders) or len(
            set(sequence_orders)
        ) != len(sequence_orders):
            raise ValueError(
                "Multiple fund NAV actions on "
                f"{action_date.isoformat()} require unique explicit sequence_order values."
            )
    return changed, active_heads


def _reinvestment_evidence_values(evidence: object) -> tuple[object, ...]:
    return (
        getattr(evidence, "instrument_id"),
        getattr(evidence, "fund_nav_event_id"),
        getattr(evidence, "revision_number"),
        getattr(evidence, "revision_kind"),
        getattr(evidence, "supersedes_fund_nav_reinvestment_evidence_id"),
        Decimal(str(getattr(evidence, "reinvestment_nav"))),
        getattr(evidence, "evidence_kind"),
        getattr(evidence, "source"),
        getattr(evidence, "external_evidence_id"),
        deepcopy(
            getattr(evidence, "provenance_json", getattr(evidence, "provenance", None))
        ),
        getattr(evidence, "recorded_by"),
        getattr(evidence, "revision_reason"),
    )


def _append_fund_nav_reinvestment_evidence_revisions(
    session: Session,
    *,
    instrument_id: str,
    incoming_revisions: list[FundNavReinvestmentEvidenceModel],
    active_event_heads: dict[str, FundNavEvent],
    expected_current_ids: list[str],
) -> tuple[bool, dict[str, FundNavReinvestmentEvidence]]:
    records = list(
        session.scalars(
            select(FundNavReinvestmentEvidence).where(
                FundNavReinvestmentEvidence.instrument_id == instrument_id
            )
        ).all()
    )
    by_id = {
        record.fund_nav_reinvestment_evidence_id: record for record in records
    }
    by_event_revision = {
        (record.fund_nav_event_id, record.revision_number): record
        for record in records
    }
    successor_by_predecessor = {
        record.supersedes_fund_nav_reinvestment_evidence_id: record
        for record in records
        if record.supersedes_fund_nav_reinvestment_evidence_id is not None
    }
    changed = False
    for incoming in sorted(
        incoming_revisions,
        key=lambda item: (item.fund_nav_event_id, item.revision_number),
    ):
        existing = by_id.get(incoming.fund_nav_reinvestment_evidence_id)
        if existing is not None:
            if _reinvestment_evidence_values(existing) != _reinvestment_evidence_values(
                incoming
            ):
                raise ValueError(
                    "Fund NAV reinvestment evidence revision "
                    f'"{incoming.fund_nav_reinvestment_evidence_id}" is immutable.'
                )
            continue
        event = session.get(FundNavEvent, incoming.fund_nav_event_id)
        if (
            event is None
            or event.instrument_id != instrument_id
            or event.event_type != "cash_distribution"
        ):
            raise ValueError(
                "Reinvestment evidence must bind a cash-distribution revision for the same fund."
            )
        key = (incoming.fund_nav_event_id, incoming.revision_number)
        if key in by_event_revision:
            raise ValueError(
                "Fund NAV reinvestment evidence revision number is already used."
            )
        predecessor: FundNavReinvestmentEvidence | None = None
        predecessor_id = incoming.supersedes_fund_nav_reinvestment_evidence_id
        if predecessor_id is not None:
            predecessor = by_id.get(predecessor_id)
            if predecessor is None:
                raise ValueError("Reinvestment evidence predecessor does not exist.")
            if (
                predecessor.instrument_id != instrument_id
                or predecessor.fund_nav_event_id != incoming.fund_nav_event_id
                or incoming.revision_number != predecessor.revision_number + 1
            ):
                raise ValueError(
                    "Reinvestment evidence revisions require the same action revision and consecutive revision numbers."
                )
            if predecessor_id in successor_by_predecessor:
                raise ValueError("Reinvestment evidence revision chain cannot branch.")
            if (
                incoming.revision_kind == "cancellation"
                and incoming.reinvestment_nav != predecessor.reinvestment_nav
            ):
                raise ValueError(
                    "Reinvestment evidence cancellation must preserve its predecessor value."
                )
        record = FundNavReinvestmentEvidence(
            fund_nav_reinvestment_evidence_id=(
                incoming.fund_nav_reinvestment_evidence_id
            ),
            instrument_id=instrument_id,
            fund_nav_event_id=incoming.fund_nav_event_id,
            revision_number=incoming.revision_number,
            revision_kind=incoming.revision_kind,
            supersedes_fund_nav_reinvestment_evidence_id=predecessor_id,
            reinvestment_nav=incoming.reinvestment_nav,
            evidence_kind=incoming.evidence_kind,
            source=incoming.source,
            external_evidence_id=incoming.external_evidence_id,
            provenance_json=deepcopy(incoming.provenance),
            recorded_by=incoming.recorded_by,
            revision_reason=incoming.revision_reason,
            created_at=incoming.created_at,
            updated_at=incoming.updated_at,
        )
        session.add(record)
        session.flush()
        by_id[record.fund_nav_reinvestment_evidence_id] = record
        by_event_revision[key] = record
        if predecessor is not None:
            successor_by_predecessor[predecessor.fund_nav_reinvestment_evidence_id] = record
        changed = True

    active_event_ids = set(active_event_heads)
    current_heads = {
        record.fund_nav_reinvestment_evidence_id: record
        for record in by_id.values()
        if record.fund_nav_event_id in active_event_ids
        and record.fund_nav_reinvestment_evidence_id not in successor_by_predecessor
        and record.revision_kind != "cancellation"
    }
    if set(expected_current_ids) != set(current_heads):
        raise ValueError(
            "current_fund_nav_reinvestment_evidence_ids must exactly match current evidence heads for active actions."
        )
    return changed, current_heads


def _projection_run_values(run: object) -> tuple[object, ...]:
    return (
        getattr(run, "instrument_id"),
        getattr(run, "input_fingerprint"),
        getattr(run, "source_observation_fingerprint"),
        getattr(run, "projection_kind"),
        getattr(run, "projection_status"),
        getattr(run, "method_version"),
        getattr(run, "anchor_date"),
        getattr(run, "source_provider"),
        deepcopy(getattr(run, "evidence_json", getattr(run, "evidence", None))),
    )


def _factor_values(factor: object) -> tuple[object, ...]:
    return (
        getattr(factor, "instrument_id"),
        getattr(factor, "factor_logical_key"),
        getattr(factor, "fund_nav_projection_run_id"),
        getattr(factor, "as_of_date"),
        _quantized_fund_nav_factor(Decimal(str(getattr(factor, "factor_level")))),
        getattr(factor, "factor_kind"),
        getattr(factor, "fund_nav_event_id"),
        getattr(factor, "fund_nav_reinvestment_evidence_id"),
        getattr(factor, "previous_fund_nav_adjustment_factor_id"),
        getattr(factor, "evidence_kind"),
        getattr(factor, "method_version"),
        getattr(factor, "anchor_date"),
        getattr(factor, "source_provider"),
        deepcopy(getattr(factor, "evidence_json", getattr(factor, "evidence", None))),
    )


def _persist_fund_nav_projection_run(
    session: Session,
    *,
    instrument_id: str,
    run_model: FundNavProjectionRunModel,
    factor_models: list[FundNavAdjustmentFactorModel],
    active_event_heads: dict[str, FundNavEvent],
    current_evidence_heads: dict[str, FundNavReinvestmentEvidence],
) -> bool:
    run_id = run_model.fund_nav_projection_run_id
    existing_run = session.get(FundNavProjectionRun, run_id)
    changed = existing_run is None
    if existing_run is None:
        existing_run = FundNavProjectionRun(
            fund_nav_projection_run_id=run_id,
            instrument_id=instrument_id,
            input_fingerprint=run_model.input_fingerprint,
            source_observation_fingerprint=run_model.source_observation_fingerprint,
            projection_kind=run_model.projection_kind,
            projection_status=run_model.projection_status,
            method_version=run_model.method_version,
            anchor_date=run_model.anchor_date,
            source_provider=run_model.source_provider,
            evidence_json=deepcopy(run_model.evidence),
            created_by=run_model.created_by,
            created_at=run_model.created_at,
        )
        session.add(existing_run)
        session.flush()
        for event_id in sorted(active_event_heads):
            session.add(
                FundNavProjectionRunEvent(
                    fund_nav_projection_run_id=run_id,
                    fund_nav_event_id=event_id,
                )
            )
        for evidence_id in sorted(current_evidence_heads):
            session.add(
                FundNavProjectionRunReinvestmentEvidence(
                    fund_nav_projection_run_id=run_id,
                    fund_nav_reinvestment_evidence_id=evidence_id,
                )
            )
        session.flush()
    else:
        if _projection_run_values(existing_run) != _projection_run_values(run_model):
            raise ValueError(f'Projection run "{run_id}" is immutable.')
        persisted_event_ids = {
            membership.fund_nav_event_id for membership in existing_run.event_revisions
        }
        persisted_evidence_ids = {
            membership.fund_nav_reinvestment_evidence_id
            for membership in existing_run.reinvestment_evidence_revisions
        }
        if persisted_event_ids != set(active_event_heads) or persisted_evidence_ids != set(
            current_evidence_heads
        ):
            raise ValueError(f'Projection run "{run_id}" input membership is immutable.')

    incoming_by_id = {
        factor.fund_nav_adjustment_factor_id: factor for factor in factor_models
    }
    persisted_factors = list(
        session.scalars(
            select(FundNavAdjustmentFactor).where(
                FundNavAdjustmentFactor.fund_nav_projection_run_id == run_id
            )
        ).all()
    )
    persisted_by_id = {
        factor.fund_nav_adjustment_factor_id: factor for factor in persisted_factors
    }
    if persisted_by_id:
        if set(persisted_by_id) != set(incoming_by_id):
            raise ValueError(f'Projection run "{run_id}" factor set is immutable.')
        for factor_id, incoming in incoming_by_id.items():
            if _factor_values(persisted_by_id[factor_id]) != _factor_values(incoming):
                raise ValueError(f'Fund NAV factor "{factor_id}" is immutable.')
        return changed

    if existing_run is not None and not changed and incoming_by_id:
        raise ValueError(f'Projection run "{run_id}" factor set is immutable.')

    if run_model.projection_status == "unavailable":
        if factor_models:
            raise ValueError("unavailable projection run must not contain factors.")
        return changed
    if not factor_models:
        raise ValueError(
            "complete or partial projection run requires adjustment factors."
        )

    provider_factors = [
        factor for factor in factor_models if factor.factor_kind == "provider_implied"
    ]
    event_factors = [
        factor for factor in factor_models if factor.factor_kind == "event_derived"
    ]
    if run_model.projection_kind == "provider_explicit":
        if event_factors:
            raise ValueError(
                "provider_explicit projection runs require only provider-implied factors."
            )
    elif run_model.projection_kind == "event_derived":
        if provider_factors:
            raise ValueError(
                "event_derived projection runs require only event-derived factors."
            )
    elif not provider_factors or not event_factors:
        raise ValueError(
            "hybrid_reanchored projection runs require both provider-implied and "
            "event-derived factors."
        )

    provider_dates = [factor.as_of_date for factor in provider_factors]
    if len(provider_dates) != len(set(provider_dates)):
        raise ValueError("Projection run has duplicate provider factor dates.")
    expected_run_anchor = min(factor.anchor_date for factor in factor_models)
    if run_model.anchor_date != expected_run_anchor:
        raise ValueError(
            "Projection run anchor_date must equal the earliest factor segment anchor."
        )

    combined_factor_by_id: dict[str, FundNavAdjustmentFactorModel] = dict(incoming_by_id)
    successor_by_previous: dict[str, str] = {}
    for factor in factor_models:
        if factor.method_version != run_model.method_version:
            raise ValueError(
                "Fund NAV factors must use their projection run method version."
            )
        event_id = factor.fund_nav_event_id
        if event_id is None:
            continue
        event = active_event_heads.get(event_id)
        if event is None or factor.as_of_date != event.effective_date:
            raise ValueError("Event-derived factor must bind a current action at its effective date.")
        previous_id = factor.previous_fund_nav_adjustment_factor_id
        if (
            previous_id is None
            or previous_id == factor.fund_nav_adjustment_factor_id
            or previous_id not in combined_factor_by_id
        ):
            raise ValueError("Event-derived factor predecessor must exist in the same run.")
        if previous_id in successor_by_previous:
            raise ValueError("Fund NAV adjustment factor chain cannot branch.")
        previous = combined_factor_by_id[previous_id]
        if previous.as_of_date > factor.as_of_date:
            raise ValueError("Event-derived factor predecessor is not chronological.")
        if previous.anchor_date != factor.anchor_date:
            raise ValueError(
                "Event-derived factor must retain its segment root anchor."
            )
        if event.event_type == "cash_distribution":
            evidence_id = factor.fund_nav_reinvestment_evidence_id
            evidence = current_evidence_heads.get(str(evidence_id or ""))
            if evidence is None or evidence.fund_nav_event_id != event_id:
                raise ValueError(
                    "Cash-distribution factor requires current reinvestment evidence for the same action revision."
                )
            action_multiplier = _quantized_fund_nav_factor(
                Decimal(1) + event.cash_per_unit / evidence.reinvestment_nav
            )
        else:
            if factor.fund_nav_reinvestment_evidence_id is not None:
                raise ValueError("Unit-split factor must not bind reinvestment evidence.")
            action_multiplier = _quantized_fund_nav_factor(event.unit_ratio)
        expected_level = _quantized_fund_nav_factor(
            previous.factor_level * action_multiplier
        )
        if factor.factor_level != expected_level:
            raise ValueError(
                "Event-derived factor level must equal previous level times the action multiplier."
            )
        successor_by_previous[previous_id] = factor.fund_nav_adjustment_factor_id

    roots = [
        factor
        for factor in factor_models
        if factor.previous_fund_nav_adjustment_factor_id is None
    ]
    if not roots:
        raise ValueError("Projection factors require at least one segment root.")
    if any(
        root.factor_kind == "event_derived"
        and root.evidence_kind
        not in {"zero_cash_anchor", "window_normalized_anchor"}
        for root in roots
    ):
        raise ValueError(
            "An event-derived factor segment must start at a canonical anchor."
        )
    for root in roots:
        if root.anchor_date != root.as_of_date:
            raise ValueError(
                "Every provider or event-derived segment root must anchor on its own date."
            )
        if root.evidence_kind == "zero_cash_anchor" and any(
            event.effective_date <= root.as_of_date
            for event in active_event_heads.values()
        ):
            raise ValueError(
                "A zero-cash anchor cannot start after a current fund action."
            )
    if run_model.projection_kind == "event_derived" and len(roots) != 1:
        raise ValueError(
            "event_derived projection runs require one canonical anchor chain."
        )

    visited_factor_ids: set[str] = set()
    ordered_factor_models: list[FundNavAdjustmentFactorModel] = []
    for root in sorted(roots, key=lambda item: (item.as_of_date, item.factor_logical_key)):
        current = root
        while True:
            current_id = current.fund_nav_adjustment_factor_id
            if current_id in visited_factor_ids:
                raise ValueError("Fund NAV adjustment factor chain cannot contain a cycle.")
            visited_factor_ids.add(current_id)
            ordered_factor_models.append(current)
            successor_id = successor_by_previous.get(current_id)
            if successor_id is None:
                break
            current = combined_factor_by_id[successor_id]
    if visited_factor_ids != set(combined_factor_by_id):
        raise ValueError(
            "Every adjustment factor must belong to exactly one rooted segment chain."
        )

    for factor in ordered_factor_models:
        existing_with_id = session.get(
            FundNavAdjustmentFactor, factor.fund_nav_adjustment_factor_id
        )
        if existing_with_id is not None:
            raise ValueError(
                f'Fund NAV factor id "{factor.fund_nav_adjustment_factor_id}" belongs to another run.'
            )
        session.add(
            FundNavAdjustmentFactor(
                fund_nav_adjustment_factor_id=factor.fund_nav_adjustment_factor_id,
                factor_logical_key=factor.factor_logical_key,
                instrument_id=instrument_id,
                fund_nav_projection_run_id=run_id,
                as_of_date=factor.as_of_date,
                factor_level=factor.factor_level,
                factor_kind=factor.factor_kind,
                fund_nav_event_id=factor.fund_nav_event_id,
                fund_nav_reinvestment_evidence_id=(
                    factor.fund_nav_reinvestment_evidence_id
                ),
                previous_fund_nav_adjustment_factor_id=(
                    factor.previous_fund_nav_adjustment_factor_id
                ),
                evidence_kind=factor.evidence_kind,
                method_version=factor.method_version,
                anchor_date=factor.anchor_date,
                source_provider=factor.source_provider,
                evidence_json=deepcopy(factor.evidence),
                created_at=factor.created_at,
                updated_at=factor.updated_at,
            )
        )
    session.flush()
    return True


def list_corporate_actions(
    session_factory: SessionFactory,
    instrument_ids: list[str] | set[str] | tuple[str, ...],
    *,
    include_cancelled: bool = False,
    effective_on_or_before: date | None = None,
) -> list[dict[str, object]]:
    """Return canonical unit-changing events for a bounded instrument set."""

    normalized_ids = sorted(
        {
            str(instrument_id or "").strip()
            for instrument_id in instrument_ids
            if str(instrument_id or "").strip()
        }
    )
    if not normalized_ids:
        return []
    with session_factory() as session:
        statement = select(CorporateActionEvent).where(
            CorporateActionEvent.instrument_id.in_(normalized_ids)
        )
        if not include_cancelled:
            statement = statement.where(CorporateActionEvent.status != "cancelled")
        if effective_on_or_before is not None:
            statement = statement.where(
                CorporateActionEvent.effective_date <= effective_on_or_before
            )
        rows = session.scalars(
            statement.order_by(
                CorporateActionEvent.effective_date,
                CorporateActionEvent.instrument_id,
                CorporateActionEvent.corporate_action_event_id,
            )
        ).all()
        return [_corporate_action_to_dict(event) for event in rows]


def upsert_corporate_action_event(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    action_type: str,
    effective_date: date,
    new_units: str | Decimal,
    old_units: str | Decimal,
    source: str,
    status: str = "confirmed",
    announcement_date: date | None = None,
    record_date: date | None = None,
    payable_date: date | None = None,
    quantity_rounding: str = "exact",
    quantity_precision: int = 0,
    cost_basis_treatment: str = "carry",
    external_event_id: str | None = None,
    provenance: dict[str, object] | None = None,
) -> dict[str, object] | None:
    """Idempotently insert or enrich one canonical corporate-action event.

    The business key deliberately excludes provider so several observations of
    the same effective event cannot be applied more than once by a portfolio.
    Existing issuer-confirmed facts win over later provider inference; provider
    evidence is retained in ``provenance.observations``.
    """

    normalized_instrument_id = instrument_id.strip()
    normalized_source = source.strip()
    try:
        ratio_new = Decimal(str(new_units))
        ratio_old = Decimal(str(old_units))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Corporate-action ratio must contain valid decimals.") from error
    now = _utcnow_iso()
    candidate_payload = {
        "corporate_action_event_id": (
            f"ca-{_slugify(normalized_instrument_id)}-{action_type}-{effective_date.isoformat()}"
        ),
        "instrument_id": normalized_instrument_id,
        "action_type": action_type,
        "announcement_date": announcement_date,
        "record_date": record_date,
        "effective_date": effective_date,
        "payable_date": payable_date,
        "new_units": ratio_new,
        "old_units": ratio_old,
        "quantity_rounding": quantity_rounding,
        "quantity_precision": quantity_precision,
        "cost_basis_treatment": cost_basis_treatment,
        "source": normalized_source,
        "external_event_id": external_event_id,
        "status": status,
        "provenance": deepcopy(provenance or {}),
        "created_at": now,
        "updated_at": now,
    }
    CorporateActionEventModel.model_validate(candidate_payload)

    with session_factory() as session:
        instrument = session.get(Instrument, normalized_instrument_id)
        if instrument is None:
            return None
        changed = False
        event = session.scalar(
            select(CorporateActionEvent).where(
                CorporateActionEvent.instrument_id == normalized_instrument_id,
                CorporateActionEvent.action_type == action_type,
                CorporateActionEvent.effective_date == effective_date,
            )
        )
        incoming_provenance = deepcopy(provenance or {})
        if event is None:
            event = CorporateActionEvent(
                corporate_action_event_id=str(candidate_payload["corporate_action_event_id"]),
                instrument_id=normalized_instrument_id,
                action_type=action_type,
                announcement_date=announcement_date,
                record_date=record_date,
                effective_date=effective_date,
                payable_date=payable_date,
                new_units=_decimal_text(ratio_new),
                old_units=_decimal_text(ratio_old),
                quantity_rounding=quantity_rounding,
                quantity_precision=quantity_precision,
                cost_basis_treatment=cost_basis_treatment,
                source=normalized_source,
                external_event_id=external_event_id,
                status=status,
                provenance_json=incoming_provenance,
                created_at=now,
                updated_at=now,
            )
            session.add(event)
            changed = True
        else:
            existing_ratio = Decimal(event.new_units) / Decimal(event.old_units)
            incoming_ratio = ratio_new / ratio_old
            if abs(existing_ratio - incoming_ratio) > Decimal("0.000000001"):
                raise ValueError(
                    "Corporate-action ratio conflicts with the canonical event "
                    f"for {normalized_instrument_id} on {effective_date.isoformat()}."
                )
            merged_provenance = deepcopy(event.provenance_json or {})
            observations = list(merged_provenance.get("observations", []))
            observation = {
                "source": normalized_source,
                "external_event_id": external_event_id,
                **incoming_provenance,
            }
            if observation not in observations:
                observations.append(observation)
                merged_provenance["observations"] = observations
                event.provenance_json = merged_provenance
                changed = True
            if event.status != "confirmed" and status == "confirmed":
                event.status = "confirmed"
                event.source = normalized_source
                event.external_event_id = external_event_id
                event.announcement_date = announcement_date or event.announcement_date
                event.record_date = record_date or event.record_date
                event.payable_date = payable_date or event.payable_date
                event.quantity_rounding = quantity_rounding
                event.quantity_precision = quantity_precision
                changed = True
            if changed:
                event.updated_at = now

        if changed:
            watermark = _next_market_data_watermark(session)
            instrument.market_data_updated_at = watermark
        session.commit()
    actions = list_corporate_actions(
        session_factory,
        [normalized_instrument_id],
        include_cancelled=True,
    )
    return next(
        (
            action
            for action in actions
            if action["action_type"] == action_type
            and action["effective_date"] == effective_date.isoformat()
        ),
        None,
    )


def find_instrument_by_identifier(
    session_factory: SessionFactory,
    *,
    identifier_value: str,
    identifier_type: str | None = None,
    include_inactive: bool = False,
) -> dict[str, object] | None:
    normalized_value = identifier_value.strip().lower()
    if not normalized_value:
        return None
    normalized_type = identifier_type.strip().lower() if identifier_type else None
    with session_factory() as session:
        identifier_filters = [
            func.lower(InstrumentIdentifier.identifier_value) == normalized_value,
        ]
        if normalized_type:
            identifier_filters.append(func.lower(InstrumentIdentifier.identifier_type) == normalized_type)
        candidates = session.scalars(
            _instrument_query()
            .join(InstrumentIdentifier)
            .where(*identifier_filters)
            .order_by(Instrument.instrument_name, Instrument.instrument_id)
        ).all()
        for candidate in candidates:
            item = _instrument_to_store_dict(candidate)
            if not include_inactive and not _is_active(item):
                continue
            identifiers = list(item.get("identifiers", []))
            for identifier in identifiers:
                current_value = str(identifier.get("identifier_value") or "").strip().lower()
                current_type = str(identifier.get("identifier_type") or "").strip().lower()
                if current_value != normalized_value:
                    continue
                if normalized_type and current_type != normalized_type:
                    continue
                return _serialize_detail_record(item)
        return None


def find_instrument_by_broker_identifier(
    session_factory: SessionFactory,
    *,
    broker: str,
    identifier_type: str,
    identifier_value: str,
    include_inactive: bool = False,
) -> dict[str, object] | None:
    normalized_broker = broker.strip().casefold()
    normalized_type = identifier_type.strip().lower()
    normalized_value = identifier_value.strip().casefold()
    if not normalized_broker or not normalized_type or not normalized_value:
        return None
    with session_factory() as session:
        candidates = session.scalars(
            _instrument_query()
            .join(InstrumentBrokerIdentifier)
            .where(
                func.lower(InstrumentBrokerIdentifier.broker) == normalized_broker,
                func.lower(InstrumentBrokerIdentifier.identifier_type)
                == normalized_type,
                func.lower(InstrumentBrokerIdentifier.identifier_value)
                == normalized_value,
            )
            .order_by(Instrument.instrument_id)
        ).all()
        if not candidates:
            return None
        if len(candidates) > 1:
            raise ValueError(
                "Ambiguous normalized broker identifier; repair registry identity data."
            )
        candidate = candidates[0]
        item = _instrument_to_store_dict(candidate)
        if not include_inactive and not _is_active(item):
            return None
        return _serialize_detail_record(item)


def create_instrument(
    session_factory: SessionFactory,
    *,
    instrument_name: str,
    instrument_type: str,
    currency: str,
    identifiers: list[dict[str, object]],
    exchange_code: str | None = None,
    quote_selection_policy: dict[str, object] | None = None,
    broker_identifiers: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    normalized_name = instrument_name.strip()
    normalized_instrument_type = normalize_instrument_type(instrument_type)
    normalized_currency = normalize_market_data_currency(currency)
    normalized_exchange_code = (
        exchange_code.strip().upper()
        if isinstance(exchange_code, str) and exchange_code.strip()
        else None
    )
    normalized_broker_identifiers = _normalized_broker_identifiers(
        {"broker_identifiers": broker_identifiers or []}
    )
    normalized_identifiers = [
        {
            **identifier,
            "identifier_type": str(identifier.get("identifier_type") or "").strip(),
            "identifier_value": str(identifier.get("identifier_value") or "").strip(),
        }
        for identifier in identifiers
    ]
    if not normalized_name:
        raise ValueError("Instrument name must not be blank.")
    if not normalized_identifiers or any(
        not item["identifier_type"] or not item["identifier_value"]
        for item in normalized_identifiers
    ):
        raise ValueError("Every instrument identifier requires a non-blank type and value.")
    if sum(1 for item in normalized_identifiers if bool(item.get("is_primary"))) != 1:
        raise ValueError("Exactly one instrument identifier must be primary.")

    identifiers = normalized_identifiers
    seen_identifiers: set[tuple[str, str]] = set()
    for identifier in identifiers:
        identifier_value = str(identifier.get("identifier_value") or "").strip()
        identifier_type = str(identifier.get("identifier_type") or "").strip()
        if not identifier_value or not identifier_type:
            continue
        normalized_identifier = (identifier_type.lower(), identifier_value.lower())
        if normalized_identifier in seen_identifiers:
            raise ValueError(f'Duplicate identifier "{identifier_type}:{identifier_value}" in request.')
        seen_identifiers.add(normalized_identifier)

        existing = find_instrument_by_identifier(
            session_factory,
            identifier_value=identifier_value,
            identifier_type=identifier_type,
            include_inactive=True,
        )
        if existing is not None:
            raise ValueError(
                f'Identifier "{identifier_type}:{identifier_value}" already belongs to '
                f'"{existing["instrument_name"]}" ({existing["instrument_id"]}).'
            )

    with session_factory() as session:
        existing_ids = {item for item in session.scalars(select(Instrument.instrument_id)).all()}
        primary_identifier = next(
            (
                str(item.get("identifier_value") or "")
                for item in identifiers
                if bool(item.get("is_primary"))
            ),
            "",
        )
        base_id = _slugify(primary_identifier or instrument_name)
        candidate = base_id
        suffix = 2
        while candidate in existing_ids:
            candidate = f"{base_id}-{suffix}"
            suffix += 1

        _validated_instrument_core(
            instrument_id=candidate,
            instrument_name=normalized_name,
            instrument_type=normalized_instrument_type,
            currency=normalized_currency,
            exchange_code=normalized_exchange_code,
            identifiers=identifiers,
            broker_identifiers=normalized_broker_identifiers,
        )

        fx_identity = fx_instrument_identity(candidate)
        if normalized_instrument_type == "fx":
            if fx_identity is None:
                raise ValueError(
                    f'FX instrument "{candidate}" has no maintained identity.'
                )
            if normalized_currency != fx_identity.quote_currency:
                raise ValueError(
                    f'FX instrument "{candidate}" requires master currency '
                    f'"{fx_identity.quote_currency}".'
                )
        elif fx_identity is not None:
            raise ValueError(
                f'Maintained FX instrument id "{candidate}" must use instrument_type "fx".'
            )

        target_quote_policy = (
            quote_selection_policy
            if quote_selection_policy is not None
            else _default_quote_selection_policy(normalized_instrument_type)
        )
        record = {
            "instrument_id": candidate,
            "instrument_name": normalized_name,
            "instrument_type": normalized_instrument_type,
            "currency": normalized_currency,
            "exchange_code": normalized_exchange_code,
            "broker_identifiers": normalized_broker_identifiers,
            "identifiers": identifiers,
            "market_data": [],
            "quote_selection_policy": _normalized_quote_selection_policy(
                {
                    "instrument_type": normalized_instrument_type,
                    "quote_selection_policy": target_quote_policy,
                }
            ),
            "source_settings": _default_source_settings(
                instrument_type=normalized_instrument_type,
                identifiers=identifiers,
            ),
            "refresh_status": _default_refresh_status(),
            "lifecycle_state": _default_lifecycle_state(),
        }

        metadata_record = session.get(RegistryMetadata, "shared")
        if metadata_record is None:
            session.add(
                RegistryMetadata(
                    registry_key="shared",
                    registry_name=DEFAULT_REGISTRY_NAME,
                )
            )

        session.add(
            Instrument(
                instrument_id=record["instrument_id"],
                instrument_name=record["instrument_name"],
                instrument_type=record["instrument_type"],
                currency=record["currency"],
                exchange_code=record["exchange_code"],
                quote_selection_policy_json=record["quote_selection_policy"],
                source_settings_json=record["source_settings"],
                refresh_status_json=record["refresh_status"],
                lifecycle_state_json=record["lifecycle_state"],
                calculation_inputs_updated_at=_utcnow_iso(),
            )
        )
        for raw_identifier in identifiers:
            identifier_value = str(raw_identifier.get("identifier_value") or "").strip()
            identifier_type = str(raw_identifier.get("identifier_type") or "").strip()
            if not identifier_value or not identifier_type:
                continue
            session.add(
                InstrumentIdentifier(
                    instrument_id=record["instrument_id"],
                    identifier_type=identifier_type,
                    identifier_value=identifier_value,
                    is_primary=bool(raw_identifier.get("is_primary")),
                )
            )
        for broker_identifier in normalized_broker_identifiers:
            session.add(
                InstrumentBrokerIdentifier(
                    instrument_id=record["instrument_id"],
                    broker=str(broker_identifier["broker"]),
                    identifier_type=str(broker_identifier["identifier_type"]),
                    identifier_value=str(broker_identifier["identifier_value"]),
                    is_primary=bool(broker_identifier["is_primary"]),
                )
            )
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise ValueError(
                "Instrument, canonical identifier, or broker identifier conflicts "
                "with an existing registry record."
            ) from error
        return _serialize_record(record)


def ensure_secondary_identifier(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    identifier_type: str,
    identifier_value: str,
) -> dict[str, object] | None:
    normalized_type = identifier_type.strip()
    normalized_value = identifier_value.strip()
    if not normalized_type or not normalized_value:
        raise ValueError("Instrument identifier type and value must not be blank.")

    with session_factory() as session:
        target = session.get(Instrument, instrument_id)
        if target is None:
            return None
        existing = session.scalar(
            select(InstrumentIdentifier).where(
                func.lower(InstrumentIdentifier.identifier_type)
                == normalized_type.casefold(),
                func.lower(InstrumentIdentifier.identifier_value)
                == normalized_value.casefold(),
            )
        )
        if existing is not None:
            if existing.instrument_id != instrument_id:
                raise ValueError(
                    f'Identifier "{normalized_type}:{normalized_value}" already belongs '
                    f'to instrument "{existing.instrument_id}".'
                )
            return _serialize_record(_instrument_to_store_dict(target))

        session.add(
            InstrumentIdentifier(
                instrument_id=instrument_id,
                identifier_type=normalized_type,
                identifier_value=normalized_value,
                is_primary=False,
            )
        )
        target.calculation_inputs_updated_at = _next_calculation_input_watermark(
            target.calculation_inputs_updated_at
        )
        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


def upsert_market_data(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    metric_family: str,
    quote_basis: str,
    as_of_date: date,
    value: str,
    currency: str,
    provider: str | None,
    status: str,
    nav_lineage: dict[str, object] | None = None,
) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.scalar(
            _instrument_query()
            .where(Instrument.instrument_id == instrument_id)
            .with_for_update()
        )
        if target is None:
            return None
        normalized_metric_family = metric_family.strip().lower()
        normalized_quote_basis = quote_basis.strip().lower()
        if normalized_metric_family == "nav":
            raise ValueError(
                "NAV observations must use the audited raw-to-canonical NAV import path."
            )
        derived_price_unit, derived_price_scale = canonical_price_contract(
            instrument_type=target.instrument_type,
            metric_family=normalized_metric_family,
            quote_basis=normalized_quote_basis,
        )
        normalized_currency, normalized_status, numeric_value = (
            _validated_market_data_observation(
                instrument_id=target.instrument_id,
                instrument_type=target.instrument_type,
                instrument_currency=target.currency,
                metric_family=normalized_metric_family,
                quote_basis=normalized_quote_basis,
                point_currency=currency,
                value=value,
                status=status,
            )
        )
        normalized_value = (
            _decimal_text(numeric_value)
            if normalize_instrument_type(target.instrument_type) == "fx"
            else str(value).strip()
        )
        normalized_nav_lineage = _validated_nav_lineage(
            metric_family=normalized_metric_family,
            quote_basis=normalized_quote_basis,
            raw_lineage=nav_lineage,
            status=status,
        )
        lineage_columns = _nav_lineage_columns(normalized_nav_lineage)

        existing = session.scalar(
            select(InstrumentMarketData).where(
                InstrumentMarketData.instrument_id == instrument_id,
                InstrumentMarketData.metric_family == normalized_metric_family,
                InstrumentMarketData.quote_basis == normalized_quote_basis,
                InstrumentMarketData.as_of_date == as_of_date,
                InstrumentMarketData.currency == normalized_currency,
            )
        )
        if existing is None:
            session.add(
                InstrumentMarketData(
                    instrument_id=instrument_id,
                    metric_family=normalized_metric_family,
                    quote_basis=normalized_quote_basis,
                    as_of_date=as_of_date,
                    value=normalized_value,
                    currency=normalized_currency,
                    price_unit=derived_price_unit,
                    price_scale=derived_price_scale,
                    provider=provider,
                    status=normalized_status,
                    **lineage_columns,
                )
            )
        else:
            existing.value = normalized_value
            existing.price_unit = derived_price_unit
            existing.price_scale = derived_price_scale
            existing.provider = provider
            existing.status = normalized_status
            existing.nav_lineage_kind = lineage_columns["nav_lineage_kind"]
            existing.nav_derivation_method_version = lineage_columns[
                "nav_derivation_method_version"
            ]
            existing.nav_derivation_anchor_date = lineage_columns[
                "nav_derivation_anchor_date"
            ]
            existing.nav_lineage_evidence_json = lineage_columns[
                "nav_lineage_evidence_json"
            ]

        target.market_data_updated_at = _next_market_data_watermark(session)

        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


def upsert_market_data_points(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
) -> int | None:
    """Upsert a homogeneous or mixed market-data batch in one transaction.

    Importers should use this path for histories instead of opening a transaction,
    advancing the registry watermark, and reloading the full instrument once per
    observation.
    """
    normalized_rows: list[
        tuple[tuple[str, str, date, str], dict[str, object]]
    ] = []
    seen_keys: set[tuple[str, str, date, str]] = set()
    for row_index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Market-data row {row_index} must be an object.")
        supplied_derived_fields = sorted(
            {"price_unit", "price_scale"}.intersection(row)
        )
        if supplied_derived_fields:
            raise ValueError(
                f"Market-data row {row_index} must not supply derived field(s): "
                + ", ".join(supplied_derived_fields)
                + "."
            )
        raw_date = row.get("as_of_date")
        if isinstance(raw_date, datetime):
            point_date = raw_date.date()
        elif isinstance(raw_date, date):
            point_date = raw_date
        else:
            try:
                point_date = date.fromisoformat(str(raw_date or "").strip())
            except ValueError as error:
                raise ValueError(
                    f"Market-data row {row_index} has an invalid as_of_date."
                ) from error
        metric_family = str(row.get("metric_family") or "").strip().lower()
        quote_basis = str(row.get("quote_basis") or "").strip().lower()
        currency = str(row.get("currency") or "").strip().upper()
        value = row.get("value")
        status = str(row.get("status") or "").strip().lower()
        missing_fields = [
            field_name
            for field_name, field_value in (
                ("metric_family", metric_family),
                ("quote_basis", quote_basis),
                ("currency", currency),
                ("value", value),
                ("status", status),
            )
            if field_value is None
            or (isinstance(field_value, str) and not field_value.strip())
        ]
        if missing_fields:
            raise ValueError(
                f"Market-data row {row_index} is missing required field(s): "
                + ", ".join(missing_fields)
                + "."
            )
        if metric_family == "nav":
            raise ValueError(
                f"Market-data row {row_index}: NAV observations must use the "
                "audited raw-to-canonical NAV import path."
            )
        if len(currency) > 8:
            raise ValueError(
                f"Market-data row {row_index} has an invalid currency."
            )
        try:
            numeric_value = parse_positive_market_data_value(value)
        except ValueError as error:
            raise ValueError(
                f"Market-data row {row_index}: {error}"
            ) from error
        if status not in VALID_DATA_STATUSES:
            raise ValueError(
                f'Market-data row {row_index} has unsupported status "{status}".'
            )
        try:
            nav_lineage = _validated_nav_lineage(
                metric_family=metric_family,
                quote_basis=quote_basis,
                raw_lineage=row.get("nav_lineage"),
                status=status,
            )
        except ValueError as error:
            raise ValueError(f"Market-data row {row_index}: {error}") from error
        key = (metric_family, quote_basis, point_date, currency)
        if key in seen_keys:
            raise ValueError(
                "Duplicate market-data row key for "
                f"{metric_family}/{quote_basis}/{point_date.isoformat()}/{currency}."
            )
        seen_keys.add(key)
        normalized_rows.append(
            (
                key,
                {
                    "value": numeric_value,
                    "value_text": str(value).strip(),
                    "provider": (
                        str(row.get("provider")).strip()
                        if row.get("provider") is not None
                        else None
                    ),
                    "status": status,
                    "nav_lineage": nav_lineage,
                    "row_index": row_index,
                },
            )
        )
    if not normalized_rows:
        return 0

    with session_factory() as session:
        target = session.scalar(
            select(Instrument)
            .where(Instrument.instrument_id == instrument_id)
            .with_for_update()
        )
        if target is None:
            return None
        normalized_by_key: dict[
            tuple[str, str, date, str], dict[str, object]
        ] = {}
        for key, payload in normalized_rows:
            metric_family, quote_basis, _, currency = key
            price_unit, price_scale = canonical_price_contract(
                instrument_type=target.instrument_type,
                metric_family=metric_family,
                quote_basis=quote_basis,
            )
            try:
                normalized_currency, normalized_status, normalized_value = (
                    _validated_market_data_observation(
                        instrument_id=target.instrument_id,
                        instrument_type=target.instrument_type,
                        instrument_currency=target.currency,
                        metric_family=metric_family,
                        quote_basis=quote_basis,
                        point_currency=currency,
                        value=payload["value"],
                        status=payload["status"],
                    )
                )
            except ValueError as error:
                raise ValueError(
                    f'Market-data row {int(payload["row_index"])}: {error}'
                ) from error
            normalized_key = (metric_family, quote_basis, key[2], normalized_currency)
            normalized_by_key[normalized_key] = {
                "value": (
                    _decimal_text(normalized_value)
                    if normalize_instrument_type(target.instrument_type) == "fx"
                    else str(payload["value_text"])
                ),
                "provider": payload["provider"],
                "status": normalized_status,
                "price_unit": price_unit,
                "price_scale": price_scale,
                **_nav_lineage_columns(
                    payload["nav_lineage"]
                    if isinstance(payload["nav_lineage"], NavLineage)
                    else None
                ),
            }
        relevant_dates = sorted({key[2] for key in normalized_by_key})
        existing_by_key = {
            (
                point.metric_family,
                point.quote_basis,
                point.as_of_date,
                point.currency,
            ): point
            for point in session.scalars(
                select(InstrumentMarketData).where(
                    InstrumentMarketData.instrument_id == instrument_id,
                    InstrumentMarketData.as_of_date.in_(relevant_dates),
                )
            ).all()
        }
        changed_count = 0
        for (metric_family, quote_basis, point_date, currency), payload in normalized_by_key.items():
            existing = existing_by_key.get(
                (metric_family, quote_basis, point_date, currency)
            )
            if existing is None:
                changed_count += 1
                session.add(
                    InstrumentMarketData(
                        instrument_id=instrument_id,
                        metric_family=metric_family,
                        quote_basis=quote_basis,
                        as_of_date=point_date,
                        value=str(payload["value"]),
                        currency=currency,
                        price_unit=str(payload["price_unit"]),
                        price_scale=payload["price_scale"],
                        provider=payload["provider"],
                        status=str(payload["status"]),
                        nav_lineage_kind=payload["nav_lineage_kind"],
                        nav_derivation_method_version=payload[
                            "nav_derivation_method_version"
                        ],
                        nav_derivation_anchor_date=payload[
                            "nav_derivation_anchor_date"
                        ],
                        nav_lineage_evidence_json=payload[
                            "nav_lineage_evidence_json"
                        ],
                    )
                )
            else:
                if (
                    existing.value == str(payload["value"])
                    and existing.price_unit == str(payload["price_unit"])
                    and existing.price_scale == payload["price_scale"]
                    and existing.provider == payload["provider"]
                    and existing.status == str(payload["status"])
                    and existing.nav_lineage_kind == payload["nav_lineage_kind"]
                    and existing.nav_derivation_method_version
                    == payload["nav_derivation_method_version"]
                    and existing.nav_derivation_anchor_date
                    == payload["nav_derivation_anchor_date"]
                    and existing.nav_lineage_evidence_json
                    == payload["nav_lineage_evidence_json"]
                ):
                    continue
                changed_count += 1
                existing.value = str(payload["value"])
                existing.price_unit = str(payload["price_unit"])
                existing.price_scale = payload["price_scale"]
                existing.provider = payload["provider"]
                existing.status = str(payload["status"])
                existing.nav_lineage_kind = payload["nav_lineage_kind"]
                existing.nav_derivation_method_version = payload[
                    "nav_derivation_method_version"
                ]
                existing.nav_derivation_anchor_date = payload[
                    "nav_derivation_anchor_date"
                ]
                existing.nav_lineage_evidence_json = payload[
                    "nav_lineage_evidence_json"
                ]
        if changed_count:
            target.market_data_updated_at = _next_market_data_watermark(session)
        session.commit()
    return changed_count


PRICE_BAR_INSTRUMENT_TYPES = frozenset({"etf", "equity", "index"})
PRICE_BAR_STATUSES = frozenset({"complete", "partial"})


def _price_bar_decimal(
    value: object,
    *,
    row_index: int,
    field_name: str,
    allow_zero: bool = False,
    optional: bool = False,
) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if optional:
            return None
        raise ValueError(f"Price-bar row {row_index} is missing {field_name}.")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(
            f"Price-bar row {row_index} has invalid {field_name}."
        ) from error
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        requirement = "non-negative" if allow_zero else "positive"
        raise ValueError(
            f"Price-bar row {row_index} {field_name} must be finite and {requirement}."
        )
    return parsed


def upsert_price_bars(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
) -> int | None:
    """Upsert raw daily OHLCV bars without changing valuation/return series."""

    normalized_rows: list[dict[str, object]] = []
    seen_dates: set[date] = set()
    for row_index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Price-bar row {row_index} must be an object.")
        raw_date = row.get("as_of_date")
        if isinstance(raw_date, datetime):
            point_date = raw_date.date()
        elif isinstance(raw_date, date):
            point_date = raw_date
        else:
            try:
                point_date = date.fromisoformat(str(raw_date or "").strip())
            except ValueError as error:
                raise ValueError(
                    f"Price-bar row {row_index} has an invalid as_of_date."
                ) from error
        if point_date in seen_dates:
            raise ValueError(
                f"Duplicate price-bar row for {point_date.isoformat()}."
            )
        seen_dates.add(point_date)

        open_price = _price_bar_decimal(
            row.get("open"), row_index=row_index, field_name="open"
        )
        high_price = _price_bar_decimal(
            row.get("high"), row_index=row_index, field_name="high"
        )
        low_price = _price_bar_decimal(
            row.get("low"), row_index=row_index, field_name="low"
        )
        close_price = _price_bar_decimal(
            row.get("close"), row_index=row_index, field_name="close"
        )
        assert open_price is not None
        assert high_price is not None
        assert low_price is not None
        assert close_price is not None
        if high_price < max(open_price, close_price) or low_price > min(
            open_price,
            close_price,
        ):
            raise ValueError(
                f"Price-bar row {row_index} violates OHLC high/low ordering."
            )

        previous_close = _price_bar_decimal(
            row.get("previous_close"),
            row_index=row_index,
            field_name="previous_close",
            optional=True,
        )
        volume = _price_bar_decimal(
            row.get("volume"),
            row_index=row_index,
            field_name="volume",
            allow_zero=True,
            optional=True,
        )
        turnover = _price_bar_decimal(
            row.get("turnover"),
            row_index=row_index,
            field_name="turnover",
            allow_zero=True,
            optional=True,
        )
        adjustment_factor = _price_bar_decimal(
            row.get("adjustment_factor"),
            row_index=row_index,
            field_name="adjustment_factor",
            optional=True,
        )
        currency = str(row.get("currency") or "").strip().upper()
        provider = str(row.get("provider") or "").strip()
        status = str(row.get("status") or "").strip().lower()
        volume_unit = (
            str(row.get("volume_unit") or "").strip() or None
            if volume is not None
            else None
        )
        turnover_unit = (
            str(row.get("turnover_unit") or "").strip() or None
            if turnover is not None
            else None
        )
        if not currency or len(currency) > 8:
            raise ValueError(f"Price-bar row {row_index} has an invalid currency.")
        if not provider:
            raise ValueError(f"Price-bar row {row_index} is missing provider.")
        if status not in PRICE_BAR_STATUSES:
            raise ValueError(
                f'Price-bar row {row_index} has unsupported status "{status}".'
            )
        if volume is not None and volume_unit is None:
            raise ValueError(f"Price-bar row {row_index} is missing volume_unit.")
        if turnover is not None and turnover_unit is None:
            raise ValueError(f"Price-bar row {row_index} is missing turnover_unit.")

        normalized_rows.append(
            {
                "as_of_date": point_date,
                "open_price": _decimal_text(open_price),
                "high_price": _decimal_text(high_price),
                "low_price": _decimal_text(low_price),
                "close_price": _decimal_text(close_price),
                "previous_close": (
                    _decimal_text(previous_close)
                    if previous_close is not None
                    else None
                ),
                "volume": _decimal_text(volume) if volume is not None else None,
                "turnover": (
                    _decimal_text(turnover) if turnover is not None else None
                ),
                "adjustment_factor": (
                    _decimal_text(adjustment_factor)
                    if adjustment_factor is not None
                    else None
                ),
                "currency": currency,
                "volume_unit": volume_unit,
                "turnover_unit": turnover_unit,
                "provider": provider,
                "status": status,
            }
        )
    if not normalized_rows:
        return 0

    with session_factory() as session:
        target = session.scalar(
            select(Instrument)
            .where(Instrument.instrument_id == instrument_id)
            .with_for_update()
        )
        if target is None:
            return None
        instrument_type = normalize_instrument_type(target.instrument_type)
        if instrument_type not in PRICE_BAR_INSTRUMENT_TYPES:
            raise ValueError(
                f'Instrument type "{instrument_type}" does not support OHLCV price bars.'
            )
        target_currency = normalize_market_data_currency(target.currency)
        for row_index, row in enumerate(normalized_rows, start=1):
            if row["currency"] != target_currency:
                raise ValueError(
                    f'Price-bar row {row_index} currency "{row["currency"]}" does not '
                    f'match instrument currency "{target_currency}".'
                )

        relevant_dates = [row["as_of_date"] for row in normalized_rows]
        existing_by_date = {
            price_bar.as_of_date: price_bar
            for price_bar in session.scalars(
                select(InstrumentPriceBar).where(
                    InstrumentPriceBar.instrument_id == instrument_id,
                    InstrumentPriceBar.as_of_date.in_(relevant_dates),
                )
            ).all()
        }
        changed_count = 0
        comparable_fields = (
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "previous_close",
            "volume",
            "turnover",
            "adjustment_factor",
            "currency",
            "volume_unit",
            "turnover_unit",
            "provider",
            "status",
        )
        for row in normalized_rows:
            point_date = row["as_of_date"]
            assert isinstance(point_date, date)
            existing = existing_by_date.get(point_date)
            if existing is None:
                changed_count += 1
                session.add(
                    InstrumentPriceBar(
                        instrument_id=instrument_id,
                        **row,
                    )
                )
                continue
            if all(getattr(existing, field) == row[field] for field in comparable_fields):
                continue
            changed_count += 1
            for field in comparable_fields:
                setattr(existing, field, row[field])

        if changed_count:
            target.market_data_updated_at = _next_market_data_watermark(session)
        session.commit()
    return changed_count


def get_price_bars(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    with session_factory() as session:
        statement = select(InstrumentPriceBar).where(
            InstrumentPriceBar.instrument_id == instrument_id
        )
        if start_date is not None:
            statement = statement.where(InstrumentPriceBar.as_of_date >= start_date)
        if end_date is not None:
            statement = statement.where(InstrumentPriceBar.as_of_date <= end_date)
        statement = statement.order_by(InstrumentPriceBar.as_of_date.desc())
        if limit is not None:
            statement = statement.limit(max(1, int(limit)))
        bars = list(reversed(session.scalars(statement).all()))
        return [
            {
                "date": bar.as_of_date.isoformat(),
                "open": bar.open_price,
                "high": bar.high_price,
                "low": bar.low_price,
                "close": bar.close_price,
                "previous_close": bar.previous_close,
                "volume": bar.volume,
                "turnover": bar.turnover,
                "adjustment_factor": bar.adjustment_factor,
                "currency": bar.currency,
                "volume_unit": bar.volume_unit,
                "turnover_unit": bar.turnover_unit,
                "provider": bar.provider,
                "status": bar.status,
            }
            for bar in bars
        ]


def get_price_bar_coverage(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
) -> dict[str, object]:
    """Return compact persisted OHLCV coverage without loading every bar."""

    with session_factory() as session:
        row = session.execute(
            select(
                func.count(InstrumentPriceBar.instrument_price_bar_id),
                func.min(InstrumentPriceBar.as_of_date),
                func.max(InstrumentPriceBar.as_of_date),
                func.count(InstrumentPriceBar.adjustment_factor),
            ).where(InstrumentPriceBar.instrument_id == instrument_id)
        ).one()
    first_date = row[1]
    latest_date = row[2]
    return {
        "row_count": int(row[0] or 0),
        "first_date": first_date.isoformat() if first_date is not None else None,
        "latest_date": latest_date.isoformat() if latest_date is not None else None,
        "adjustment_factor_count": int(row[3] or 0),
    }


def upsert_source_settings(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    source_mode: str,
    source_email: str | None,
    source_location: str | None,
    source_api_profile: str | None,
    source_email_rules: list[dict[str, object]] | None,
    expected_frequency: str | None = None,
    market_calendar: object = _SOURCE_SETTING_UNSET,
    release_lag_days: int | None = None,
    return_semantics: str | None = None,
    source_provider_currency: str | None = None,
    source_price_multiplier: object = _SOURCE_SETTING_UNSET,
) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.get(Instrument, instrument_id)
        if target is None:
            return None

        store_item = _instrument_to_store_dict(target)
        source_settings = _normalized_source_settings(store_item)
        calculation_settings_before = {
            key: source_settings.get(key)
            for key in (
                "source_mode",
                "expected_frequency",
                "market_calendar",
                "release_lag_days",
                "return_semantics",
                "source_provider_currency",
                "source_price_multiplier",
            )
        }
        source_settings["source_mode"] = source_mode
        if source_email is not None:
            source_settings["source_email"] = source_email.strip()
        if source_location is not None:
            source_settings["source_location"] = source_location.strip() or "Shared data ops"
        if source_api_profile is not None:
            source_settings["source_api_profile"] = source_api_profile.strip()
        if source_email_rules is not None:
            source_settings["source_email_rules"] = [
                dict(rule) for rule in source_email_rules if isinstance(rule, dict)
            ]
        if expected_frequency is not None:
            source_settings["expected_frequency"] = expected_frequency
        if market_calendar is not _SOURCE_SETTING_UNSET:
            source_settings["market_calendar"] = market_calendar
        if release_lag_days is not None:
            source_settings["release_lag_days"] = release_lag_days
        if return_semantics is not None:
            if return_semantics != "unknown" and target.instrument_type not in {
                "index",
                "equity",
                "etf",
            }:
                raise ValueError(
                    "Explicit return_semantics is only supported for indexes and listed securities."
                )
            source_settings["return_semantics"] = return_semantics
        if source_provider_currency is not None:
            normalized_provider_currency = source_provider_currency.strip()
            if not normalized_provider_currency:
                raise ValueError("source_provider_currency must not be blank.")
            source_settings["source_provider_currency"] = normalized_provider_currency
        if source_price_multiplier is not _SOURCE_SETTING_UNSET:
            try:
                normalized_multiplier = Decimal(str(source_price_multiplier).strip())
            except (InvalidOperation, TypeError, ValueError) as error:
                raise ValueError(
                    "source_price_multiplier must be a finite positive decimal."
                ) from error
            if not normalized_multiplier.is_finite() or normalized_multiplier <= 0:
                raise ValueError(
                    "source_price_multiplier must be a finite positive decimal."
                )
            source_settings["source_price_multiplier"] = format(
                normalized_multiplier,
                "f",
            )
        source_settings = _normalized_source_settings(
            {
                **store_item,
                "source_settings": source_settings,
            }
        )
        refresh_status = _normalized_refresh_status(store_item)
        refresh_status["mode"] = source_mode
        target.source_settings_json = source_settings
        target.refresh_status_json = refresh_status
        calculation_settings_after = {
            key: source_settings.get(key)
            for key in calculation_settings_before
        }
        if calculation_settings_after != calculation_settings_before:
            target.calculation_inputs_updated_at = _next_calculation_input_watermark(
                target.calculation_inputs_updated_at
            )
        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


def upsert_quote_selection_policy(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    quote_selection_policy: dict[str, object],
) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.get(Instrument, instrument_id)
        if target is None:
            return None

        validate_quote_selection_policy(
            instrument_type=target.instrument_type,
            quote_selection_policy=quote_selection_policy,
        )

        store_item = _instrument_to_store_dict(target)
        previous_policy = _normalized_quote_selection_policy(store_item)
        store_item["quote_selection_policy"] = quote_selection_policy
        next_policy = _normalized_quote_selection_policy(store_item)
        target.quote_selection_policy_json = next_policy
        if next_policy != previous_policy:
            target.calculation_inputs_updated_at = _next_calculation_input_watermark(
                target.calculation_inputs_updated_at
            )
        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


def publish_fund_nav_history(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
    projection_run: dict[str, object],
    current_fund_nav_event_ids: Iterable[str],
    current_fund_nav_reinvestment_evidence_ids: Iterable[str],
    event_revisions: Iterable[dict[str, object]] = (),
    reinvestment_evidence_revisions: Iterable[dict[str, object]] = (),
    adjustment_factors: Iterable[dict[str, object]] = (),
    expected_market_data_updated_at: str | None,
    refresh_status: str,
    updated_by: str | None,
    message: str,
    mode: str | None = None,
) -> dict[str, object] | None:
    normalized_instrument_id = instrument_id.strip()
    if not normalized_instrument_id:
        raise ValueError("Instrument id must not be blank.")
    normalized_updated_by = str(updated_by or "").strip()
    if not normalized_updated_by:
        raise ValueError("updated_by is required for an atomic fund NAV publication.")
    (
        event_models,
        evidence_models,
        run_model,
        factor_models,
        current_event_ids,
        current_evidence_ids,
    ) = _normalize_fund_nav_projection_input(
        instrument_id=normalized_instrument_id,
        event_revisions=event_revisions,
        reinvestment_evidence_revisions=reinvestment_evidence_revisions,
        projection_run=projection_run,
        current_fund_nav_event_ids=current_fund_nav_event_ids,
        current_fund_nav_reinvestment_evidence_ids=(
            current_fund_nav_reinvestment_evidence_ids
        ),
        adjustment_factors=adjustment_factors,
    )
    dirty_from: date | None = None
    publication_changed = False
    with session_factory() as session:
        if session.get_bind().dialect.name == "sqlite":
            # SQLite ignores SELECT ... FOR UPDATE.  Acquire its database write
            # reservation before reading the optimistic watermark so two NAV
            # publishers cannot both validate the same stale snapshot.
            session.execute(text("BEGIN IMMEDIATE"))
        target = session.scalar(
            select(Instrument)
            .where(Instrument.instrument_id == normalized_instrument_id)
            .with_for_update()
        )
        if target is None:
            return None
        if target.market_data_updated_at != expected_market_data_updated_at:
            raise StaleFundNavPublicationError(
                "Fund NAV source snapshot is stale; rebuild from durable raw evidence."
            )
        instrument_type = target.instrument_type.strip().lower()
        validate_nav_history_instrument_type(
            instrument_type=instrument_type,
            instrument_id=normalized_instrument_id,
        )

        old_event_records = list(
            session.scalars(
                select(FundNavEvent).where(
                    FundNavEvent.instrument_id == normalized_instrument_id
                )
            ).all()
        )
        old_superseded_event_ids = {
            record.supersedes_fund_nav_event_id
            for record in old_event_records
            if record.supersedes_fund_nav_event_id is not None
        }
        old_active_events = {
            record.fund_nav_action_id: record
            for record in old_event_records
            if record.fund_nav_event_id not in old_superseded_event_ids
            and record.revision_kind != "cancellation"
        }
        old_evidence_records = list(
            session.scalars(
                select(FundNavReinvestmentEvidence).where(
                    FundNavReinvestmentEvidence.instrument_id
                    == normalized_instrument_id
                )
            ).all()
        )
        old_superseded_evidence_ids = {
            record.supersedes_fund_nav_reinvestment_evidence_id
            for record in old_evidence_records
            if record.supersedes_fund_nav_reinvestment_evidence_id is not None
        }
        old_active_event_ids = {
            record.fund_nav_event_id for record in old_active_events.values()
        }
        old_current_evidence = {
            record.fund_nav_event_id: record
            for record in old_evidence_records
            if record.fund_nav_event_id in old_active_event_ids
            and record.fund_nav_reinvestment_evidence_id
            not in old_superseded_evidence_ids
            and record.revision_kind != "cancellation"
        }

        instrument_currency = normalize_market_data_currency(target.currency)
        normalized_rows: list[
            tuple[date, str, str, str, str, str, NavLineage]
        ] = []
        seen_keys: set[tuple[date, str]] = set()
        for row_index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"NAV row {row_index} must be an object.")
            raw_date = str(row.get("as_of_date") or "").strip()
            if not raw_date:
                raise ValueError(f"NAV row {row_index} requires as_of_date.")
            try:
                point_date = date.fromisoformat(raw_date)
            except ValueError as error:
                raise ValueError(f"NAV row {row_index} has an invalid as_of_date.") from error
            try:
                row_currency = normalize_market_data_currency(row.get("currency"))
            except ValueError as error:
                raise ValueError(f"NAV row {row_index}: {error}") from error
            if row_currency != instrument_currency:
                raise ValueError(
                    f'NAV row {row_index} currency "{row_currency}" does not match '
                    f'instrument currency "{instrument_currency}".'
                )
            value_count = 0
            for row_key, quote_basis in (
                ("nav", "official_nav"),
                ("nav_with_dividend", "total_return_nav"),
            ):
                row_value = row.get(row_key)
                if row_value is None:
                    continue
                value_count += 1
                status_key = (
                    "nav_status"
                    if quote_basis == "official_nav"
                    else "nav_with_dividend_status"
                )
                row_status = str(row.get(status_key) or "").strip().lower()
                if row_status not in VALID_DATA_STATUSES:
                    raise ValueError(
                        f'NAV row {row_index} {row_key} has unsupported status '
                        f'"{row_status}".'
                    )
                try:
                    parse_positive_market_data_value(row_value)
                except ValueError as error:
                    raise ValueError(f"NAV row {row_index} {row_key}: {error}") from error
                key = (point_date, quote_basis)
                if key in seen_keys:
                    raise ValueError(
                        "Duplicate NAV row key for "
                        f"{point_date.isoformat()}/{quote_basis}."
                    )
                seen_keys.add(key)
                raw_lineage: object
                if quote_basis == "official_nav":
                    raw_lineage = row.get("nav_lineage")
                else:
                    raw_lineage = row.get("nav_with_dividend_lineage")
                try:
                    nav_lineage = _validated_nav_lineage(
                        metric_family="nav",
                        quote_basis=quote_basis,
                        raw_lineage=raw_lineage,
                        status=row_status,
                    )
                except ValueError as error:
                    raise ValueError(
                        f"NAV row {row_index} {row_key}: {error}"
                    ) from error
                if nav_lineage is None:
                    raise ValueError(f"NAV row {row_index} {row_key} has no lineage.")
                provider_key = (
                    "nav_source_provider"
                    if quote_basis == "official_nav"
                    else "nav_with_dividend_source_provider"
                )
                point_provider = str(row.get(provider_key) or "").strip()
                if not point_provider:
                    raise ValueError(
                        f"NAV row {row_index} {row_key} has no source provider."
                    )
                normalized_rows.append(
                    (
                        point_date,
                        row_currency,
                        quote_basis,
                        str(row_value).strip(),
                        row_status,
                        point_provider,
                        nav_lineage,
                    )
                )
            if (
                row.get("nav_with_dividend_lineage") is not None
                and row.get("nav_with_dividend") is None
            ):
                raise ValueError(
                    f"NAV row {row_index} has lineage without nav_with_dividend."
                )
            if value_count == 0:
                raise ValueError(f"NAV row {row_index} has no NAV observation.")

        events_changed, active_event_heads = _append_fund_nav_event_revisions(
            session,
            instrument_id=normalized_instrument_id,
            incoming_revisions=event_models,
            expected_current_ids=current_event_ids,
        )
        evidence_changed, current_evidence_heads = (
            _append_fund_nav_reinvestment_evidence_revisions(
                session,
                instrument_id=normalized_instrument_id,
                incoming_revisions=evidence_models,
                active_event_heads=active_event_heads,
                expected_current_ids=current_evidence_ids,
            )
        )
        run_changed = _persist_fund_nav_projection_run(
            session,
            instrument_id=normalized_instrument_id,
            run_model=run_model,
            factor_models=factor_models,
            active_event_heads=active_event_heads,
            current_evidence_heads=current_evidence_heads,
        )

        current_pointer = session.get(
            FundNavCurrentProjection, normalized_instrument_id
        )
        pointer_changed = (
            current_pointer is None
            or current_pointer.fund_nav_projection_run_id
            != run_model.fund_nav_projection_run_id
        )
        if current_pointer is None:
            session.add(
                FundNavCurrentProjection(
                    instrument_id=normalized_instrument_id,
                    fund_nav_projection_run_id=run_model.fund_nav_projection_run_id,
                    updated_at=_utcnow_iso(),
                    updated_by=normalized_updated_by,
                )
            )
        elif pointer_changed:
            current_pointer.fund_nav_projection_run_id = (
                run_model.fund_nav_projection_run_id
            )
            current_pointer.updated_at = _utcnow_iso()
            current_pointer.updated_by = normalized_updated_by
        session.flush()

        factor_by_id = {
            factor.fund_nav_adjustment_factor_id: factor
            for factor in session.scalars(
                select(FundNavAdjustmentFactor).where(
                    FundNavAdjustmentFactor.fund_nav_projection_run_id
                    == run_model.fund_nav_projection_run_id
                )
            ).all()
        }
        factor_by_logical_key = {
            factor.factor_logical_key: factor for factor in factor_by_id.values()
        }
        resolved_rows: list[
            tuple[date, str, str, str, str, str, NavLineage]
        ] = []
        for normalized_row in normalized_rows:
            (
                point_date,
                row_currency,
                quote_basis,
                row_value,
                row_status,
                point_provider,
                nav_lineage,
            ) = normalized_row
            if quote_basis == "total_return_nav" and row_status == "complete":
                logical_key = str(
                    nav_lineage.evidence.get("factor_logical_key") or ""
                ).strip()
                factor = factor_by_logical_key.get(logical_key)
                if factor is None:
                    raise ValueError(
                        "Complete total_return_nav requires a factor_logical_key in the published run."
                    )
                supplied_record_id = str(
                    nav_lineage.evidence.get("factor_record_id") or ""
                ).strip()
                if supplied_record_id and supplied_record_id != (
                    factor.fund_nav_adjustment_factor_id
                ):
                    raise ValueError(
                        "total_return_nav factor_record_id does not match factor_logical_key."
                    )
                resolved_evidence = deepcopy(nav_lineage.evidence)
                resolved_evidence["factor_record_id"] = (
                    factor.fund_nav_adjustment_factor_id
                )
                resolved_evidence["factor_level"] = _decimal_text(
                    factor.factor_level
                )
                nav_lineage = NavLineage.model_validate(
                    {
                        **nav_lineage.model_dump(),
                        "evidence": resolved_evidence,
                    }
                )
            resolved_rows.append(
                (
                    point_date,
                    row_currency,
                    quote_basis,
                    row_value,
                    row_status,
                    point_provider,
                    nav_lineage,
                )
            )
        normalized_rows = resolved_rows
        desired_rows_by_key: dict[
            tuple[date, str, str],
            tuple[str, str, str, dict[str, object]],
        ] = {}
        official_by_date: dict[tuple[date, str], tuple[str, str]] = {}
        referenced_factor_ids: set[str] = set()
        for (
            point_date,
            row_currency,
            quote_basis,
            row_value,
            row_status,
            point_provider,
            nav_lineage,
        ) in normalized_rows:
            lineage_payload = nav_lineage.model_dump(mode="json")
            desired_rows_by_key[(point_date, quote_basis, row_currency)] = (
                str(Decimal(row_value)),
                row_status,
                point_provider,
                lineage_payload,
            )
            if quote_basis == "official_nav":
                official_by_date[(point_date, row_currency)] = (
                    str(Decimal(row_value)),
                    row_status,
                )

        for (
            point_date,
            row_currency,
            quote_basis,
            row_value,
            row_status,
            _point_provider,
            nav_lineage,
        ) in normalized_rows:
            if quote_basis != "total_return_nav" or row_status != "complete":
                continue
            factor_id = str(nav_lineage.evidence.get("factor_record_id") or "").strip()
            factor = factor_by_id.get(factor_id)
            if factor is None or factor.instrument_id != normalized_instrument_id:
                raise ValueError(
                    "Complete total_return_nav must reference a factor in the published current run."
                )
            if nav_lineage.kind == "provider_explicit":
                if (
                    run_model.projection_kind
                    not in {"provider_explicit", "hybrid_reanchored"}
                    or factor.factor_kind != "provider_implied"
                    or factor.as_of_date != point_date
                ):
                    raise ValueError(
                        "Provider-explicit total_return_nav requires a same-date provider-implied factor."
                    )
            else:
                if (
                    run_model.projection_kind
                    not in {"event_derived", "hybrid_reanchored"}
                    or factor.as_of_date > point_date
                    or factor.method_version != nav_lineage.method_version
                    or factor.anchor_date != nav_lineage.anchor_date
                ):
                    raise ValueError(
                        "Derived total_return_nav requires a compatible rooted factor chain."
                    )
                ancestry_ids: list[str] = []
                represented_event_ids: list[str] = []
                ancestry_seen: set[str] = set()
                ancestry_factor = factor
                while True:
                    ancestry_factor_id = (
                        ancestry_factor.fund_nav_adjustment_factor_id
                    )
                    if ancestry_factor_id in ancestry_seen:
                        raise ValueError(
                            "Derived total_return_nav factor ancestry contains a cycle."
                        )
                    ancestry_seen.add(ancestry_factor_id)
                    ancestry_ids.append(ancestry_factor_id)
                    if ancestry_factor.fund_nav_event_id is not None:
                        represented_event_ids.append(
                            ancestry_factor.fund_nav_event_id
                        )
                    previous_factor_id = (
                        ancestry_factor.previous_fund_nav_adjustment_factor_id
                    )
                    if previous_factor_id is None:
                        break
                    previous_factor = factor_by_id.get(previous_factor_id)
                    if previous_factor is None:
                        raise ValueError(
                            "Derived total_return_nav factor ancestry leaves the current run."
                        )
                    ancestry_factor = previous_factor
                root_factor = ancestry_factor
                represented_event_ids.reverse()
                required_events = sorted(
                    (
                        event
                        for event in active_event_heads.values()
                        if root_factor.as_of_date < event.effective_date <= point_date
                    ),
                    key=lambda event: (
                        event.effective_date,
                        event.sequence_order
                        if event.sequence_order is not None
                        else 1,
                    ),
                )
                required_event_ids = [
                    event.fund_nav_event_id for event in required_events
                ]
                if represented_event_ids != required_event_ids:
                    raise ValueError(
                        "Derived total_return_nav factor ancestry must cover every "
                        "current action after its segment root in exact order."
                    )
                referenced_factor_ids.update(ancestry_ids)
            if nav_lineage.kind == "provider_explicit":
                referenced_factor_ids.add(factor.fund_nav_adjustment_factor_id)
            lineage_factor_level = nav_lineage.evidence.get("factor_level")
            if not _same_decimal(lineage_factor_level, factor.factor_level):
                raise ValueError(
                    "total_return_nav lineage factor_level must match its factor record."
                )
            official = official_by_date.get((point_date, row_currency))
            if official is None or official[1] != "complete":
                raise ValueError(
                    "Complete total_return_nav requires same-date complete official_nav."
                )
            expected_total = (
                Decimal(official[0]) * factor.factor_level
            ).quantize(
                Decimal("0.0000000000000001"),
                rounding=ROUND_HALF_UP,
            )
            actual_total = Decimal(row_value)
            if actual_total != expected_total:
                raise ValueError(
                    "total_return_nav must equal official_nav times its adjustment factor."
                )

        complete_unit_dates = {
            point_date
            for (
                point_date,
                _row_currency,
                quote_basis,
                _row_value,
                row_status,
                _point_provider,
                _nav_lineage,
            ) in normalized_rows
            if quote_basis == "official_nav" and row_status == "complete"
        }
        complete_total_dates = {
            point_date
            for (
                point_date,
                _row_currency,
                quote_basis,
                _row_value,
                row_status,
                _point_provider,
                _nav_lineage,
            ) in normalized_rows
            if quote_basis == "total_return_nav" and row_status == "complete"
        }
        missing_total_dates = sorted(complete_unit_dates - complete_total_dates)
        if not complete_total_dates.issubset(complete_unit_dates):
            raise ValueError(
                "Every complete total_return_nav date requires complete official_nav."
            )
        if run_model.projection_status == "complete":
            if not complete_unit_dates or complete_total_dates != complete_unit_dates:
                raise ValueError(
                    "complete projection status requires total_return_nav on every "
                    "complete official_nav date."
                )
        elif run_model.projection_status == "partial":
            if (
                not complete_total_dates
                or not missing_total_dates
                or complete_total_dates == complete_unit_dates
            ):
                raise ValueError(
                    "partial projection status requires a non-empty strict subset "
                    "of complete official_nav dates."
                )
        elif complete_total_dates:
            raise ValueError(
                "unavailable projection status must not publish total_return_nav."
            )
        if run_model.projection_status == "unavailable" and (
            run_model.projection_kind == "hybrid_reanchored"
        ):
            raise ValueError(
                "unavailable projection cannot claim hybrid reanchoring."
            )

        published_evidence_dates = run_model.evidence.get(
            "published_total_return_dates"
        )
        missing_evidence_dates = run_model.evidence.get(
            "missing_total_return_dates"
        )
        expected_published_date_text = [
            item.isoformat() for item in sorted(complete_total_dates)
        ]
        expected_missing_date_text = [
            item.isoformat() for item in missing_total_dates
        ]
        if published_evidence_dates != expected_published_date_text:
            raise ValueError(
                "Projection evidence published_total_return_dates must exactly "
                "match the canonical rows."
            )
        if missing_evidence_dates != expected_missing_date_text:
            raise ValueError(
                "Projection evidence missing_total_return_dates must exactly "
                "match complete unit-NAV gaps."
            )
        if referenced_factor_ids != set(factor_by_id):
            raise ValueError(
                "Every factor in the current projection must be referenced by a "
                "published total-return row or be one of its ancestors."
            )

        old_nav_rows = list(
            session.scalars(
                select(InstrumentMarketData).where(
                    InstrumentMarketData.instrument_id == normalized_instrument_id,
                    InstrumentMarketData.metric_family == "nav",
                    InstrumentMarketData.quote_basis.in_(
                        ("official_nav", "total_return_nav")
                    ),
                )
            ).all()
        )
        old_rows_by_key = {
            (row.as_of_date, row.quote_basis, row.currency): (
                str(Decimal(row.value)),
                row.status,
                str(row.provider or ""),
                _serialized_nav_lineage(
                    kind=row.nav_lineage_kind,
                    method_version=row.nav_derivation_method_version,
                    anchor_date=row.nav_derivation_anchor_date,
                    evidence=row.nav_lineage_evidence_json,
                ),
            )
            for row in old_nav_rows
        }
        changed_market_keys = {
            key
            for key in set(old_rows_by_key).union(desired_rows_by_key)
            if old_rows_by_key.get(key) != desired_rows_by_key.get(key)
        }
        market_rows_changed = bool(changed_market_keys)

        dirty_dates = {key[0] for key in changed_market_keys}
        new_active_events_by_action = {
            record.fund_nav_action_id: record for record in active_event_heads.values()
        }
        for action_id in set(old_active_events).union(new_active_events_by_action):
            old_event = old_active_events.get(action_id)
            new_event = new_active_events_by_action.get(action_id)
            if (
                old_event is None
                or new_event is None
                or old_event.fund_nav_event_id != new_event.fund_nav_event_id
            ):
                if old_event is not None:
                    dirty_dates.add(old_event.effective_date)
                if new_event is not None:
                    dirty_dates.add(new_event.effective_date)
        new_current_evidence_by_event = {
            record.fund_nav_event_id: record
            for record in current_evidence_heads.values()
        }
        for event_id in set(old_current_evidence).union(new_current_evidence_by_event):
            old_evidence = old_current_evidence.get(event_id)
            new_evidence = new_current_evidence_by_event.get(event_id)
            if (
                old_evidence is None
                or new_evidence is None
                or old_evidence.fund_nav_reinvestment_evidence_id
                != new_evidence.fund_nav_reinvestment_evidence_id
            ):
                event = active_event_heads.get(event_id) or next(
                    (
                        item
                        for item in old_active_events.values()
                        if item.fund_nav_event_id == event_id
                    ),
                    None,
                )
                if event is not None:
                    dirty_dates.add(event.effective_date)
        if pointer_changed and not dirty_dates:
            if run_model.anchor_date is not None:
                dirty_dates.add(run_model.anchor_date)
            elif normalized_rows:
                dirty_dates.add(min(item[0] for item in normalized_rows))
        dirty_from = min(dirty_dates) if dirty_dates else None

        if market_rows_changed:
            session.execute(
                delete(InstrumentMarketData).where(
                    InstrumentMarketData.instrument_id == normalized_instrument_id,
                    InstrumentMarketData.metric_family == "nav",
                    InstrumentMarketData.quote_basis == "total_return_nav",
                )
            )
            session.execute(
                delete(InstrumentMarketData).where(
                    InstrumentMarketData.instrument_id == normalized_instrument_id,
                    InstrumentMarketData.metric_family == "nav",
                    InstrumentMarketData.quote_basis == "official_nav",
                )
            )
            session.flush()
            for (
                point_date,
                row_currency,
                quote_basis,
                row_value,
                row_status,
                point_provider,
                nav_lineage,
            ) in sorted(
                normalized_rows,
                key=lambda item: (item[0], 0 if item[2] == "official_nav" else 1),
            ):
                price_unit, price_scale = canonical_price_contract(
                    instrument_type=instrument_type,
                    metric_family="nav",
                    quote_basis=quote_basis,
                )
                session.add(
                    InstrumentMarketData(
                        instrument_id=normalized_instrument_id,
                        metric_family="nav",
                        quote_basis=quote_basis,
                        as_of_date=point_date,
                        value=row_value,
                        currency=row_currency,
                        price_unit=price_unit,
                        price_scale=price_scale,
                        provider=point_provider,
                        status=row_status,
                        **_nav_lineage_columns(nav_lineage),
                    )
                )

        publication_changed = any(
            (
                events_changed,
                evidence_changed,
                run_changed,
                pointer_changed,
                market_rows_changed,
            )
        )
        if publication_changed:
            store_item = _instrument_to_store_dict(target)
            source_settings = _normalized_source_settings(store_item)
            source_mode = str(source_settings.get("source_mode") or "manual")
            target.refresh_status_json = _updated_refresh_status(
                previous=dict(store_item.get("refresh_status", {})),
                status=refresh_status,
                message=message,
                updated_by=normalized_updated_by,
                mode=mode or source_mode,
                requested_at=_utcnow_iso(),
            )
            target.market_data_updated_at = _next_market_data_watermark(session)
        session.commit()
    refreshed = get_instrument(session_factory, normalized_instrument_id)
    if refreshed is None:
        return None
    return {
        "record": refreshed,
        "published_projection_run_id": run_model.fund_nav_projection_run_id,
        "market_data_updated_at": refreshed.get("market_data_updated_at"),
        "dirty_from": dirty_from.isoformat() if dirty_from is not None else None,
        "changed": publication_changed,
    }


def update_refresh_status(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    status: str,
    message: str,
    updated_by: str | None,
    mode: str | None = None,
) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.get(Instrument, instrument_id)
        if target is None:
            return None
        store_item = _instrument_to_store_dict(target)
        configured_source_mode = str(
            _normalized_source_settings(store_item).get("source_mode") or "manual"
        )
        source_mode = mode or configured_source_mode
        target.refresh_status_json = _updated_refresh_status(
            previous=dict(store_item.get("refresh_status", {})),
            status=status,
            message=message,
            updated_by=updated_by,
            mode=source_mode,
            requested_at=_utcnow_iso(),
        )
        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


def set_instrument_lifecycle_state(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    status: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    normalized_status = status.strip().lower()
    if normalized_status not in {"active", "archived"}:
        raise ValueError(f'Unsupported lifecycle status "{status}".')
    with session_factory() as session:
        target = session.get(Instrument, instrument_id)
        if target is None:
            return None
        current_state = _normalized_lifecycle_state(_instrument_to_store_dict(target))
        if current_state.get("status") == normalized_status:
            target.lifecycle_state_json = current_state
        else:
            target.lifecycle_state_json = _default_lifecycle_state(
                status=normalized_status,
                changed_at=_utcnow_iso(),
                changed_by=(updated_by or "platform_ui").strip() or "platform_ui",
            )
        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


def archive_instrument(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    return set_instrument_lifecycle_state(
        session_factory,
        instrument_id=instrument_id,
        status="archived",
        updated_by=updated_by,
    )


def restore_instrument(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    return set_instrument_lifecycle_state(
        session_factory,
        instrument_id=instrument_id,
        status="active",
        updated_by=updated_by,
    )


def instrument_registry_name(session_factory: SessionFactory) -> str:
    with session_factory() as session:
        metadata_record = session.get(RegistryMetadata, "shared")
        if metadata_record is None:
            return DEFAULT_REGISTRY_NAME
        return str(metadata_record.registry_name or DEFAULT_REGISTRY_NAME)
