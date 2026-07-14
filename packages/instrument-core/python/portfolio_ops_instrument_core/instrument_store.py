from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from portfolio_ops_instrument_core.db_models import (
    CorporateActionEvent,
    Instrument,
    InstrumentIdentifier,
    MarketDataOutboxEvent,
    MarketDataOutboxWorkerHeartbeat,
    QuoteObservation,
    QuoteObservationRevision,
    QuoteSeries,
    RegistryMetadata,
)
from portfolio_ops_instrument_core.models import (
    CASH_CUMULATIVE_NAV_BASES,
    CorporateActionEvent as CorporateActionEventModel,
    TOTAL_RETURN_QUOTE_BASES,
    VALUATION_PROHIBITED_TOTAL_RETURN_BASES,
)
from portfolio_ops_instrument_core.quote_revisions import (
    QUOTE_REVISION_PAYLOAD_SCHEMA_V2,
    SOURCE_OBSERVATION_STATUSES,
    USABLE_CURRENT_STATUS,
    WITHDRAWN_STATUS,
    canonical_timestamp,
    make_quote_observation_id,
    make_quote_revision_id,
    make_quote_series_id,
    normalize_optional_timestamp,
    normalize_revision_status,
    normalize_source_ref,
    quote_revision_payload_hash,
    quote_numeric_evidence,
    VALID_METRIC_FAMILIES,
    VALID_QUOTE_BASES,
)
from portfolio_ops_instrument_core.quote_resolver import (
    QUOTE_SELECTION_POLICY_VERSION,
    canonical_quote_selection_policy_revision,
)


DEFAULT_REGISTRY_NAME = "Portfolio Operations Shared Instruments"
EMPTY_STORE: dict[str, object] = {
    "registry_name": DEFAULT_REGISTRY_NAME,
    "instruments": [],
}

SessionFactory = Callable[[], Session]
EMAIL_REFRESH_SUCCESS_STATUSES = frozenset({"imported", "no_match", "no_new_data"})
_CURRENCY_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")


def _normalize_required_currency(value: object, *, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} requires an explicit three-letter currency code.")
    normalized = value.strip().upper()
    if not _CURRENCY_CODE_PATTERN.fullmatch(normalized):
        raise ValueError(f"{context} requires an explicit three-letter currency code.")
    return normalized


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _decimal_text(value: Decimal) -> str:
    fixed = format(value, "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed


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


def _default_source_settings() -> dict[str, object]:
    return {
        "source_mode": "manual",
        "source_email": "",
        "source_location": "Shared data ops",
        "source_api_profile": "",
        "source_email_rules": [],
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
    previous_mode_fallback: str,
) -> dict[str, object]:
    normalized_mode = str(mode or "manual").strip().lower() or "manual"
    previous_cursor = (
        str(previous.get("last_successful_requested_at") or "").strip() or None
    )
    if previous_cursor is None:
        previous_status = str(previous.get("status") or "").strip().lower()
        previous_mode = (
            str(previous.get("mode") or previous_mode_fallback).strip().lower()
        )
        previous_requested_at = str(previous.get("requested_at") or "").strip() or None
        if (
            previous_mode == "email"
            and previous_status in EMAIL_REFRESH_SUCCESS_STATUSES
            and previous_requested_at is not None
        ):
            previous_cursor = previous_requested_at

    last_successful_requested_at = previous_cursor
    if (
        normalized_mode == "email"
        and status.strip().lower() in EMAIL_REFRESH_SUCCESS_STATUSES
    ):
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
    "fund": {
        "trading": ["last", "close", "official_nav"],
        "valuation": ["official_nav", "close", "last"],
        "total_return": [
            "total_return_nav",
            "dividend_adjusted_nav",
            "reinvested_nav",
            "adjusted_close",
        ],
        "chart": [
            "total_return_nav",
            "dividend_adjusted_nav",
            "reinvested_nav",
            "adjusted_close",
            "official_nav",
            "close",
        ],
        "reference": ["official_nav", "close", "last"],
    },
    "etf": {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "equity": {
        "trading": ["last", "close"],
        # Valuation and transaction accounting must use the unadjusted traded
        # price. Adjusted close is a synthetic total-return series and must
        # never silently substitute for a missing market price.
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "index": {
        "trading": ["close", "last"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "bond": {
        "trading": ["clean_price", "dirty_price"],
        "valuation": ["dirty_price", "clean_price"],
        "total_return": [],
        "chart": ["dirty_price", "clean_price"],
        "reference": ["clean_price", "dirty_price"],
    },
    "cash": {
        "trading": ["par"],
        "valuation": ["par"],
        "total_return": [],
        "chart": ["par"],
        "reference": ["par"],
    },
    "fx": {
        "trading": ["spot"],
        "valuation": ["spot"],
        "total_return": [],
        "chart": ["spot"],
        "reference": ["spot"],
    },
    "other": {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
}


def _default_quote_selection_policy(instrument_type: str) -> dict[str, object]:
    normalized_instrument_type = (
        instrument_type
        if instrument_type in QUOTE_SELECTION_POLICY_DEFAULTS
        else "other"
    )
    return deepcopy(QUOTE_SELECTION_POLICY_DEFAULTS[normalized_instrument_type])


def validate_quote_selection_policy(quote_selection_policy: dict[str, object]) -> None:
    for role in ("trading", "valuation", "total_return", "chart", "reference"):
        if role not in quote_selection_policy:
            raise ValueError(
                f"quote_selection_policy must explicitly define role {role}."
            )
        raw_values = quote_selection_policy.get(role)
        if not isinstance(raw_values, list):
            raise ValueError(f"quote_selection_policy.{role} must be a list.")
        normalized_values = [str(value or "").strip().lower() for value in raw_values]
        unsupported = sorted(
            basis for basis in normalized_values if basis not in VALID_QUOTE_BASES
        )
        if unsupported:
            raise ValueError(
                f"quote_selection_policy.{role} contains unsupported bases: "
                + ", ".join(unsupported)
            )
        if len(normalized_values) != len(set(normalized_values)):
            raise ValueError(
                f"quote_selection_policy.{role} must not contain duplicate bases."
            )
    raw_valuation = quote_selection_policy.get("valuation", [])
    if not isinstance(raw_valuation, list):
        raise ValueError("quote_selection_policy.valuation must be a list.")
    invalid = sorted(
        {
            str(value or "").strip()
            for value in raw_valuation
            if str(value or "").strip() in VALUATION_PROHIBITED_TOTAL_RETURN_BASES
        }
    )
    if invalid:
        raise ValueError(
            "Valuation policy cannot use total-return quote bases: "
            + ", ".join(invalid)
            + ". Use an unadjusted trading/valuation quote such as close, last, or official_nav."
        )
    raw_total_return = quote_selection_policy.get("total_return", [])
    if not isinstance(raw_total_return, list):
        raise ValueError("quote_selection_policy.total_return must be a list.")
    invalid_total_return = sorted(
        {
            str(value or "").strip()
            for value in raw_total_return
            if str(value or "").strip() not in TOTAL_RETURN_QUOTE_BASES
        }
    )
    if invalid_total_return:
        raise ValueError(
            "Total-return policy requires a true total-return quote basis: "
            + ", ".join(invalid_total_return)
            + ". Price/NAV trend bases belong in chart, not total_return."
        )
    for role in ("total_return", "chart"):
        raw_values = quote_selection_policy.get(role, [])
        if not isinstance(raw_values, list):
            raise ValueError(f"quote_selection_policy.{role} must be a list.")
        invalid_cumulative = sorted(
            {
                str(value or "").strip()
                for value in raw_values
                if str(value or "").strip() in CASH_CUMULATIVE_NAV_BASES
            }
        )
        if invalid_cumulative:
            raise ValueError(
                f"{role} policy cannot use cash-cumulative NAV as total return: "
                + ", ".join(invalid_cumulative)
                + ". Use a true total-return basis; ordinary price/NAV trends belong in chart."
            )


def _normalized_quote_selection_policy(item: dict[str, object]) -> dict[str, object]:
    instrument_type = str(item.get("instrument_type") or "other")
    policy = _default_quote_selection_policy(instrument_type)
    raw_policy = item.get("quote_selection_policy", {})
    if not isinstance(raw_policy, dict):
        return policy

    for role in policy:
        if role not in raw_policy:
            continue
        raw_values = raw_policy.get(role)
        if not isinstance(raw_values, list):
            raise ValueError(f"quote_selection_policy.{role} must be a list.")
        normalized_values: list[str] = []
        for raw_value in raw_values:
            quote_basis = str(raw_value or "").strip().lower()
            normalized_values.append(quote_basis)
        policy[role] = normalized_values
    validate_quote_selection_policy(policy)
    return policy


def _sort_market_data(points: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        points,
        key=lambda item: (
            str(item.get("as_of_date") or ""),
            str(item.get("metric_family") or ""),
            str(item.get("quote_basis") or ""),
            str(item.get("currency") or ""),
            str(item.get("quote_series_id") or ""),
        ),
    )


def _sort_corporate_actions(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        events,
        key=lambda item: (
            str(item.get("effective_date") or ""),
            str(item.get("action_type") or ""),
            str(item.get("corporate_action_event_id") or ""),
        ),
    )


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
        if not all(
            (
                event_id,
                instrument_id,
                action_type,
                effective_date,
                new_units,
                old_units,
                source,
            )
        ):
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
                "cost_basis_treatment": str(
                    event.get("cost_basis_treatment") or "carry"
                ),
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
    instrument_id = str(item.get("instrument_id") or "<unknown>").strip()
    instrument_currency = _normalize_required_currency(
        item.get("currency"),
        context=f"Instrument '{instrument_id}'",
    )
    normalized_points: list[dict[str, object]] = []
    for raw_point in list(item.get("market_data", [])):
        point = dict(raw_point)
        if "provider" in point:
            raise ValueError(
                "market-data provider was removed; use source_ref for quote provenance"
            )
        metric_family = str(point.get("metric_family") or "").strip()
        quote_basis = str(point.get("quote_basis") or "").strip()
        if metric_family not in VALID_METRIC_FAMILIES:
            continue
        if quote_basis not in VALID_QUOTE_BASES:
            continue
        if VALID_QUOTE_BASES[quote_basis] != metric_family:
            continue
        try:
            status = normalize_revision_status(point.get("status"))
            numeric_evidence = (
                None
                if status == WITHDRAWN_STATUS
                else quote_numeric_evidence(point.get("value"))
            )
            value = numeric_evidence.value if numeric_evidence is not None else None
            if value is not None and Decimal(value) <= 0:
                raise ValueError("quote value must be positive")
            stored_scale = point.get("value_input_scale")
            stored_scale_state = point.get("numeric_scale_state")
            stored_payload_version = point.get("payload_schema_version")
            if numeric_evidence is None:
                value_input_scale = None
                numeric_scale_state = None
            elif stored_scale is not None and stored_scale_state is not None:
                value_input_scale = int(stored_scale)
                numeric_scale_state = str(stored_scale_state)
                if value_input_scale < 0 or numeric_scale_state not in {
                    "declared",
                    "binary_inferred",
                    "legacy_inferred",
                }:
                    raise ValueError("invalid persisted quote numeric evidence")
            else:
                value_input_scale = numeric_evidence.value_input_scale
                numeric_scale_state = numeric_evidence.numeric_scale_state
            payload_schema_version = int(
                stored_payload_version or QUOTE_REVISION_PAYLOAD_SCHEMA_V2
            )
            if payload_schema_version not in {1, 2}:
                raise ValueError("invalid quote payload schema version")
        except ValueError:
            continue
        normalized_points.append(
            {
                "quote_series_id": point.get("quote_series_id"),
                "observation_id": point.get("observation_id"),
                "revision_id": point.get("revision_id"),
                "revision_number": point.get("revision_number"),
                "metric_family": metric_family,
                "quote_basis": quote_basis,
                "as_of_date": str(point.get("as_of_date") or ""),
                "value": value,
                "value_input_scale": value_input_scale,
                "numeric_scale_state": numeric_scale_state,
                "payload_schema_version": payload_schema_version,
                "currency": _normalize_required_currency(
                    point.get("currency") or instrument_currency,
                    context=f"Instrument '{instrument_id}' market-data point",
                ),
                "source_ref": normalize_source_ref(point.get("source_ref")),
                "status": status,
                "source_published_at": canonical_timestamp(
                    point.get("source_published_at")
                ),
                "ingested_at": canonical_timestamp(point.get("ingested_at")),
                "payload_hash": point.get("payload_hash"),
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
    """Destructively replace the whole registry for tests/bootstrap only.

    Runtime write paths must use the granular commands below; this boundary
    intentionally discards durable outbox delivery and worker-heartbeat state.
    """

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


def _instrument_to_store_dict(
    item: Instrument,
    *,
    market_data: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    identifiers = _serialize_identifier_rows(item)
    resolved_market_data = market_data or []
    corporate_actions = [
        {
            "corporate_action_event_id": event.corporate_action_event_id,
            "instrument_id": event.instrument_id,
            "action_type": event.action_type,
            "announcement_date": event.announcement_date.isoformat()
            if event.announcement_date
            else None,
            "record_date": event.record_date.isoformat() if event.record_date else None,
            "effective_date": event.effective_date.isoformat(),
            "payable_date": event.payable_date.isoformat()
            if event.payable_date
            else None,
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
        for event in item.corporate_action_events
    ]
    return {
        "instrument_id": item.instrument_id,
        "instrument_name": item.instrument_name,
        "instrument_type": item.instrument_type,
        "currency": item.currency,
        "identifiers": identifiers,
        "market_data": _sort_market_data(resolved_market_data),
        "corporate_actions": _sort_corporate_actions(corporate_actions),
        "market_data_updated_at": item.market_data_updated_at,
        "quote_selection_policy": deepcopy(item.quote_selection_policy_json or {}),
        "source_settings": deepcopy(item.source_settings_json or {}),
        "refresh_status": deepcopy(item.refresh_status_json or {}),
        "lifecycle_state": deepcopy(item.lifecycle_state_json or {}),
    }


def _save_store_to_db(session: Session, data: dict[str, object]) -> None:
    normalized = _normalize_store(data)

    if session.get_bind().dialect.name == "postgresql":
        # The controlled full-reset boundary must bypass immutable revision row
        # triggers. All registry-owned dependents are truncated together; new
        # seed revisions then create fresh outbox events through the normal
        # trigger. Callers must run this before installing cross-schema facts.
        session.execute(
            text(
                """
                TRUNCATE TABLE
                    market_data_outbox_worker_heartbeat,
                    market_data_outbox_event,
                    corporate_action_event,
                    quote_observation_revision,
                    quote_observation,
                    quote_series,
                    instrument_identifier,
                    instrument
                RESTART IDENTITY
                """
            )
        )
    else:
        session.execute(delete(MarketDataOutboxWorkerHeartbeat))
        session.execute(delete(MarketDataOutboxEvent))
        session.execute(delete(CorporateActionEvent))
        session.execute(delete(QuoteObservationRevision))
        session.execute(delete(QuoteObservation))
        session.execute(delete(QuoteSeries))
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
        metadata_record.registry_name = str(
            normalized.get("registry_name") or DEFAULT_REGISTRY_NAME
        )
        metadata_record.market_data_updated_at = reset_watermark

    for raw_item in list(normalized.get("instruments", [])):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        instrument = Instrument(
            instrument_id=str(item.get("instrument_id") or "").strip(),
            instrument_name=str(item.get("instrument_name") or "").strip(),
            instrument_type=str(item.get("instrument_type") or "").strip() or "other",
            currency=_normalize_required_currency(
                item.get("currency"),
                context=(
                    "Instrument "
                    f"'{str(item.get('instrument_id') or '<unknown>').strip()}'"
                ),
            ),
            quote_selection_policy_json=_normalized_quote_selection_policy(item),
            source_settings_json=_normalized_source_settings(item),
            refresh_status_json=_normalized_refresh_status(item),
            lifecycle_state_json=_normalized_lifecycle_state(item),
            market_data_updated_at=(
                str(item.get("market_data_updated_at")).strip()
                if item.get("market_data_updated_at")
                else (reset_watermark if _normalized_market_data(item) else None)
            ),
        )
        if not instrument.instrument_id or not instrument.instrument_name:
            continue
        session.add(instrument)
        session.flush()

        for raw_identifier in list(item.get("identifiers", [])):
            if not isinstance(raw_identifier, dict):
                continue
            identifier_value = str(raw_identifier.get("identifier_value") or "").strip()
            identifier_type = str(raw_identifier.get("identifier_type") or "").strip()
            if not identifier_value or not identifier_type:
                continue
            session.add(
                InstrumentIdentifier(
                    instrument_id=instrument.instrument_id,
                    identifier_type=identifier_type,
                    identifier_value=identifier_value,
                    is_primary=bool(raw_identifier.get("is_primary")),
                )
            )

        normalized_points: dict[tuple[str, str, date, str], dict[str, object]] = {}
        for raw_point in _normalized_market_data(item):
            try:
                point_date = date.fromisoformat(str(raw_point.get("as_of_date") or ""))
            except ValueError:
                continue
            key = (
                str(raw_point.get("metric_family") or "").strip(),
                str(raw_point.get("quote_basis") or "").strip(),
                point_date,
                _normalize_required_currency(
                    raw_point.get("currency") or instrument.currency,
                    context=f"Instrument '{instrument.instrument_id}' market-data point",
                ),
            )
            normalized_points[key] = raw_point

        quote_series_by_key: dict[tuple[str, str, str], QuoteSeries] = {}
        for (
            metric_family,
            quote_basis,
            point_date,
            currency,
        ), raw_point in normalized_points.items():
            series_key = (metric_family, quote_basis, currency)
            quote_series = quote_series_by_key.get(series_key)
            if quote_series is None:
                series_id = make_quote_series_id(
                    instrument_id=instrument.instrument_id,
                    metric_family=metric_family,
                    quote_basis=quote_basis,
                    currency=currency,
                )
                quote_series = QuoteSeries(
                    quote_series_id=series_id,
                    instrument_id=instrument.instrument_id,
                    metric_family=metric_family,
                    quote_basis=quote_basis,
                    currency=currency,
                    data_updated_at=instrument.market_data_updated_at,
                )
                quote_series_by_key[series_key] = quote_series
                session.add(quote_series)

            observation_id = make_quote_observation_id(
                quote_series_id=quote_series.quote_series_id,
                as_of_date=point_date,
            )
            status = normalize_revision_status(raw_point.get("status"))
            numeric_evidence = (
                None
                if status == WITHDRAWN_STATUS
                else quote_numeric_evidence(raw_point.get("value"))
            )
            # A destructive reset is a new v2 ingestion event.  It may copy an
            # exact v2 representation, but legacy-inferred metadata cannot be
            # promoted to declared evidence merely by replaying a fixture.
            if (
                numeric_evidence is not None
                and raw_point.get("payload_schema_version") == 2
                and raw_point.get("numeric_scale_state")
                in {"declared", "binary_inferred"}
            ):
                numeric_evidence = type(numeric_evidence)(
                    value=numeric_evidence.value,
                    value_input_scale=int(raw_point["value_input_scale"]),
                    numeric_scale_state=str(raw_point["numeric_scale_state"]),
                )
            value = numeric_evidence.value if numeric_evidence is not None else None
            source_ref = normalize_source_ref(raw_point.get("source_ref"))
            source_published_at = normalize_optional_timestamp(
                raw_point.get("source_published_at")
            )
            session.add(
                QuoteObservation(
                    observation_id=observation_id,
                    quote_series_id=quote_series.quote_series_id,
                    as_of_date=point_date,
                )
            )
            session.add(
                QuoteObservationRevision(
                    revision_id=make_quote_revision_id(
                        observation_id=observation_id,
                        revision_number=1,
                    ),
                    observation_id=observation_id,
                    revision_number=1,
                    value=value,
                    value_input_scale=(
                        numeric_evidence.value_input_scale
                        if numeric_evidence is not None
                        else None
                    ),
                    numeric_scale_state=(
                        numeric_evidence.numeric_scale_state
                        if numeric_evidence is not None
                        else None
                    ),
                    payload_schema_version=QUOTE_REVISION_PAYLOAD_SCHEMA_V2,
                    source_ref=source_ref,
                    status=status,
                    source_published_at=source_published_at,
                    # Reset/bootstrap payloads have no trustworthy ingestion event.
                    ingested_at=None,
                    payload_hash=quote_revision_payload_hash(
                        value=value,
                        source_ref=source_ref,
                        status=status,
                        source_published_at=source_published_at,
                        payload_schema_version=QUOTE_REVISION_PAYLOAD_SCHEMA_V2,
                        value_input_scale=(
                            numeric_evidence.value_input_scale
                            if numeric_evidence is not None
                            else None
                        ),
                        numeric_scale_state=(
                            numeric_evidence.numeric_scale_state
                            if numeric_evidence is not None
                            else None
                        ),
                    ),
                    is_current=True,
                    superseded_at=None,
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
                    corporate_action_event_id=str(
                        raw_event["corporate_action_event_id"]
                    ),
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
    latest_by_series: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for point in points:
        series_key = (
            str(point.get("quote_series_id") or ""),
            str(point.get("metric_family") or ""),
            str(point.get("quote_basis") or ""),
            str(point.get("currency") or ""),
        )
        as_of_date = str(point.get("as_of_date") or "")
        current = latest_by_series.get(series_key)
        if current is None or as_of_date >= str(current.get("as_of_date") or ""):
            latest_by_series[series_key] = point
    return [latest_by_series[key] for key in sorted(latest_by_series.keys())]


def _coverage_state(points: list[dict[str, object]]) -> str:
    if not points:
        return "unavailable"
    if any(str(point.get("status") or "") == "partial" for point in points):
        return "partial"
    if all(str(point.get("status") or "") == "complete" for point in points):
        return "complete"
    return "partial"


def _normalized_source_settings(item: dict[str, object]) -> dict[str, object]:
    source_settings = dict(_default_source_settings())
    source_settings.update(dict(item.get("source_settings", {})))
    raw_rules = source_settings.get("source_email_rules", [])
    if isinstance(raw_rules, list):
        source_settings["source_email_rules"] = [
            dict(rule) for rule in raw_rules if isinstance(rule, dict)
        ]
    else:
        source_settings["source_email_rules"] = []
    return source_settings


def _normalized_refresh_status(item: dict[str, object]) -> dict[str, object]:
    source_settings = _normalized_source_settings(item)
    refresh_status = dict(
        _default_refresh_status(str(source_settings.get("source_mode") or "manual"))
    )
    refresh_status.update(dict(item.get("refresh_status", {})))
    refresh_status["mode"] = str(
        refresh_status.get("mode") or source_settings.get("source_mode") or "manual"
    )
    return refresh_status


def _normalized_lifecycle_state(item: dict[str, object]) -> dict[str, object]:
    raw_lifecycle = item.get("lifecycle_state")
    status = "active"
    changed_at: str | None = None
    changed_by: str | None = None
    canonical_instrument_id: str | None = None

    if isinstance(raw_lifecycle, dict):
        status = (
            str(raw_lifecycle.get("status") or "active").strip().lower() or "active"
        )
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
    market_data = [
        point
        for point in _normalized_market_data(item)
        if point.get("status") == USABLE_CURRENT_STATUS
        and point.get("value") is not None
    ]
    quote_selection_policy = _normalized_quote_selection_policy(item)
    return {
        "instrument_id": item["instrument_id"],
        "instrument_name": item["instrument_name"],
        "instrument_type": item["instrument_type"],
        "currency": item["currency"],
        "identifiers": deepcopy(item.get("identifiers", [])),
        "latest_market_data": _latest_market_data(market_data),
        "quote_selection_policy": quote_selection_policy,
        "quote_selection_policy_version": QUOTE_SELECTION_POLICY_VERSION,
        "quote_selection_policy_revision": canonical_quote_selection_policy_revision(
            quote_selection_policy
        ),
        "coverage_state": _coverage_state(market_data),
        "market_data_updated_at": item.get("market_data_updated_at"),
        "source_settings": _normalized_source_settings(item),
        "refresh_status": _normalized_refresh_status(item),
        "lifecycle_state": _normalized_lifecycle_state(item),
        "corporate_actions": deepcopy(item.get("corporate_actions", [])),
    }


def _serialize_detail_record(item: dict[str, object]) -> dict[str, object]:
    record = _serialize_record(item)
    record["market_data"] = deepcopy(
        [
            point
            for point in _normalized_market_data(item)
            if point.get("status") == USABLE_CURRENT_STATUS
            and point.get("value") is not None
        ]
    )
    return record


def _instrument_query(*, include_market_data: bool = True):
    del include_market_data
    eager_loads = [
        selectinload(Instrument.identifiers),
        selectinload(Instrument.corporate_action_events),
    ]
    return select(Instrument).options(*eager_loads)


def _serialize_market_data_row(row: Any) -> dict[str, object]:
    return {
        "quote_series_id": str(row["quote_series_id"]),
        "observation_id": str(row["observation_id"]),
        "revision_id": str(row["revision_id"]),
        "revision_number": int(row["revision_number"]),
        "metric_family": str(row["metric_family"]),
        "quote_basis": str(row["quote_basis"]),
        "as_of_date": row["as_of_date"].isoformat(),
        "value": str(row["value"]),
        "value_input_scale": int(row["value_input_scale"]),
        "numeric_scale_state": str(row["numeric_scale_state"]),
        "payload_schema_version": int(row["payload_schema_version"]),
        "currency": str(row["currency"]),
        "source_ref": row["source_ref"],
        "status": str(row["status"]),
        "source_published_at": canonical_timestamp(row["source_published_at"]),
        "ingested_at": canonical_timestamp(row["ingested_at"]),
        "payload_hash": str(row["payload_hash"]),
    }


def _market_data_for_instruments(
    session: Session,
    instrument_ids: list[str],
    *,
    latest_only: bool,
) -> dict[str, list[dict[str, object]]]:
    if not instrument_ids:
        return {}

    current_points = (
        select(
            QuoteSeries.instrument_id.label("instrument_id"),
            QuoteSeries.quote_series_id.label("quote_series_id"),
            QuoteSeries.metric_family.label("metric_family"),
            QuoteSeries.quote_basis.label("quote_basis"),
            QuoteSeries.currency.label("currency"),
            QuoteObservation.observation_id.label("observation_id"),
            QuoteObservation.as_of_date.label("as_of_date"),
            QuoteObservationRevision.revision_id.label("revision_id"),
            QuoteObservationRevision.revision_number.label("revision_number"),
            QuoteObservationRevision.value.label("value"),
            QuoteObservationRevision.value_input_scale.label("value_input_scale"),
            QuoteObservationRevision.numeric_scale_state.label("numeric_scale_state"),
            QuoteObservationRevision.payload_schema_version.label(
                "payload_schema_version"
            ),
            QuoteObservationRevision.source_ref.label("source_ref"),
            QuoteObservationRevision.status.label("status"),
            QuoteObservationRevision.source_published_at.label("source_published_at"),
            QuoteObservationRevision.ingested_at.label("ingested_at"),
            QuoteObservationRevision.payload_hash.label("payload_hash"),
        )
        .join(
            QuoteObservation,
            QuoteObservation.quote_series_id == QuoteSeries.quote_series_id,
        )
        .join(
            QuoteObservationRevision,
            QuoteObservationRevision.observation_id == QuoteObservation.observation_id,
        )
        .where(
            QuoteSeries.instrument_id.in_(instrument_ids),
            QuoteObservationRevision.is_current.is_(True),
            QuoteObservationRevision.status == USABLE_CURRENT_STATUS,
            QuoteObservationRevision.value.is_not(None),
        )
    )
    statement = current_points
    if latest_only:
        ranked = current_points.add_columns(
            func.row_number()
            .over(
                partition_by=QuoteSeries.quote_series_id,
                order_by=(
                    QuoteObservation.as_of_date.desc(),
                    QuoteObservationRevision.revision_number.desc(),
                ),
            )
            .label("row_number"),
        ).subquery()
        statement = select(ranked).where(ranked.c.row_number == 1)
    rows = session.execute(statement).mappings()
    result: dict[str, list[dict[str, object]]] = {
        instrument_id: [] for instrument_id in instrument_ids
    }
    for row in rows:
        result[str(row["instrument_id"])].append(_serialize_market_data_row(row))
    for points in result.values():
        points[:] = _sort_market_data(points)
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
    normalized_instrument_type = (
        instrument_type.strip().lower() if instrument_type else None
    )

    with session_factory() as session:
        statement = _instrument_query(include_market_data=False)
        if not include_inactive:
            lifecycle_status = Instrument.lifecycle_state_json["status"].as_string()
            statement = statement.where(
                func.coalesce(lifecycle_status, "active") != "archived"
            )
        if normalized_instrument_type:
            statement = statement.where(
                func.lower(Instrument.instrument_type) == normalized_instrument_type
            )
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
                            func.lower(InstrumentIdentifier.identifier_type).like(
                                pattern
                            ),
                            func.lower(InstrumentIdentifier.identifier_value).like(
                                pattern
                            ),
                        )
                    ),
                )
            )
        statement = statement.order_by(
            Instrument.instrument_name, Instrument.instrument_id
        )
        if limit is not None:
            statement = statement.limit(limit)
        instrument_rows = list(session.scalars(statement).all())
        instrument_ids = [item.instrument_id for item in instrument_rows]
        latest_market_data = _market_data_for_instruments(
            session,
            instrument_ids,
            latest_only=True,
        )
        return [
            _serialize_record(
                _instrument_to_store_dict(
                    item,
                    market_data=latest_market_data.get(item.instrument_id, []),
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
        return [
            str(instrument_id) for instrument_id in session.scalars(statement).all()
        ]


def get_instrument(
    session_factory: SessionFactory, instrument_id: str
) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.scalar(
            _instrument_query().where(Instrument.instrument_id == instrument_id)
        )
        if target is None:
            return None
        market_data = _market_data_for_instruments(
            session,
            [target.instrument_id],
            latest_only=False,
        )
        return _serialize_detail_record(
            _instrument_to_store_dict(
                target,
                market_data=market_data.get(target.instrument_id, []),
            )
        )


def instrument_exists(session_factory: SessionFactory, instrument_id: str) -> bool:
    normalized_id = instrument_id.strip()
    if not normalized_id:
        return False
    with session_factory() as session:
        return (
            session.scalar(
                select(Instrument.instrument_id).where(
                    Instrument.instrument_id == normalized_id
                )
            )
            is not None
        )


def get_instrument_details(
    session_factory: SessionFactory,
    instrument_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, dict[str, object] | None]:
    """Load full instrument records for a set of ids in one registry session.

    The returned mapping includes missing ids with a ``None`` value so callers
    can use it as a complete request-scoped cache without falling back to an
    accidental per-instrument query loop.
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
            _instrument_query().where(Instrument.instrument_id.in_(normalized_ids))
        ).all()
        market_data = _market_data_for_instruments(
            session,
            [target.instrument_id for target in targets],
            latest_only=False,
        )
        details_by_id = {
            target.instrument_id: _serialize_detail_record(
                _instrument_to_store_dict(
                    target,
                    market_data=market_data.get(target.instrument_id, []),
                )
            )
            for target in targets
        }
    return {
        instrument_id: details_by_id.get(instrument_id)
        for instrument_id in normalized_ids
    }


def list_quote_observation_revisions(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    quote_series_id: str | None = None,
    as_of_date: date | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    """Return the immutable revision audit trail for one instrument."""
    with session_factory() as session:
        statement = (
            select(
                QuoteSeries.instrument_id.label("instrument_id"),
                QuoteSeries.quote_series_id.label("quote_series_id"),
                QuoteSeries.metric_family.label("metric_family"),
                QuoteSeries.quote_basis.label("quote_basis"),
                QuoteSeries.currency.label("currency"),
                QuoteObservation.observation_id.label("observation_id"),
                QuoteObservation.as_of_date.label("as_of_date"),
                QuoteObservationRevision.revision_id.label("revision_id"),
                QuoteObservationRevision.revision_number.label("revision_number"),
                QuoteObservationRevision.value.label("value"),
                QuoteObservationRevision.value_input_scale.label("value_input_scale"),
                QuoteObservationRevision.numeric_scale_state.label(
                    "numeric_scale_state"
                ),
                QuoteObservationRevision.payload_schema_version.label(
                    "payload_schema_version"
                ),
                QuoteObservationRevision.source_ref.label("source_ref"),
                QuoteObservationRevision.status.label("status"),
                QuoteObservationRevision.source_published_at.label(
                    "source_published_at"
                ),
                QuoteObservationRevision.ingested_at.label("ingested_at"),
                QuoteObservationRevision.payload_hash.label("payload_hash"),
                QuoteObservationRevision.is_current.label("is_current"),
                QuoteObservationRevision.superseded_at.label("superseded_at"),
            )
            .join(
                QuoteObservation,
                QuoteObservation.quote_series_id == QuoteSeries.quote_series_id,
            )
            .join(
                QuoteObservationRevision,
                QuoteObservationRevision.observation_id
                == QuoteObservation.observation_id,
            )
            .where(QuoteSeries.instrument_id == instrument_id)
        )
        if quote_series_id:
            statement = statement.where(
                QuoteSeries.quote_series_id == quote_series_id.strip()
            )
        if as_of_date is not None:
            statement = statement.where(QuoteObservation.as_of_date == as_of_date)
        statement = statement.order_by(
            QuoteObservation.as_of_date.desc(),
            QuoteObservationRevision.revision_number.desc(),
            QuoteSeries.quote_series_id,
        )
        if limit is not None:
            statement = statement.limit(max(int(limit), 0))
        rows = session.execute(statement).mappings()
        return [
            {
                "instrument_id": str(row["instrument_id"]),
                "quote_series_id": str(row["quote_series_id"]),
                "metric_family": str(row["metric_family"]),
                "quote_basis": str(row["quote_basis"]),
                "currency": str(row["currency"]),
                "observation_id": str(row["observation_id"]),
                "as_of_date": row["as_of_date"].isoformat(),
                "revision_id": str(row["revision_id"]),
                "revision_number": int(row["revision_number"]),
                "value": row["value"],
                "value_input_scale": (
                    int(row["value_input_scale"])
                    if row["value_input_scale"] is not None
                    else None
                ),
                "numeric_scale_state": row["numeric_scale_state"],
                "payload_schema_version": int(row["payload_schema_version"]),
                "source_ref": row["source_ref"],
                "status": str(row["status"]),
                "source_published_at": canonical_timestamp(row["source_published_at"]),
                "ingested_at": canonical_timestamp(row["ingested_at"]),
                "payload_hash": str(row["payload_hash"]),
                "is_current": bool(row["is_current"]),
                "superseded_at": canonical_timestamp(row["superseded_at"]),
            }
            for row in rows
        ]


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
        return [
            {
                "corporate_action_event_id": event.corporate_action_event_id,
                "instrument_id": event.instrument_id,
                "action_type": event.action_type,
                "announcement_date": event.announcement_date.isoformat()
                if event.announcement_date
                else None,
                "record_date": event.record_date.isoformat()
                if event.record_date
                else None,
                "effective_date": event.effective_date.isoformat(),
                "payable_date": event.payable_date.isoformat()
                if event.payable_date
                else None,
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
            for event in rows
        ]


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
        raise ValueError(
            "Corporate-action ratio must contain valid decimals."
        ) from error
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
                corporate_action_event_id=str(
                    candidate_payload["corporate_action_event_id"]
                ),
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
            identifier_filters.append(
                func.lower(InstrumentIdentifier.identifier_type) == normalized_type
            )
        candidates = session.scalars(
            _instrument_query()
            .join(InstrumentIdentifier)
            .where(*identifier_filters)
            .order_by(Instrument.instrument_name, Instrument.instrument_id)
        ).all()
        for candidate in candidates:
            market_data = _market_data_for_instruments(
                session,
                [candidate.instrument_id],
                latest_only=False,
            )
            item = _instrument_to_store_dict(
                candidate,
                market_data=market_data.get(candidate.instrument_id, []),
            )
            if not include_inactive and not _is_active(item):
                continue
            identifiers = list(item.get("identifiers", []))
            for identifier in identifiers:
                current_value = (
                    str(identifier.get("identifier_value") or "").strip().lower()
                )
                current_type = (
                    str(identifier.get("identifier_type") or "").strip().lower()
                )
                if current_value != normalized_value:
                    continue
                if normalized_type and current_type != normalized_type:
                    continue
                return _serialize_detail_record(item)
        return None


def create_instrument(
    session_factory: SessionFactory,
    *,
    instrument_name: str,
    instrument_type: str,
    currency: str,
    identifiers: list[dict[str, object]],
    quote_selection_policy: dict[str, object] | None = None,
) -> dict[str, object]:
    normalized_name = instrument_name.strip()
    normalized_currency = _normalize_required_currency(
        currency,
        context="New instrument",
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
        raise ValueError(
            "Every instrument identifier requires a non-blank type and value."
        )
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
            raise ValueError(
                f'Duplicate identifier "{identifier_type}:{identifier_value}" in request.'
            )
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
        existing_ids = {
            item for item in session.scalars(select(Instrument.instrument_id)).all()
        }
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

        target_quote_policy = quote_selection_policy or _default_quote_selection_policy(
            instrument_type
        )
        validate_quote_selection_policy(target_quote_policy)
        record = {
            "instrument_id": candidate,
            "instrument_name": normalized_name,
            "instrument_type": instrument_type,
            "currency": normalized_currency,
            "identifiers": identifiers,
            "market_data": [],
            "quote_selection_policy": _normalized_quote_selection_policy(
                {
                    "instrument_type": instrument_type,
                    "quote_selection_policy": target_quote_policy,
                }
            ),
            "source_settings": _default_source_settings(),
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
                quote_selection_policy_json=record["quote_selection_policy"],
                source_settings_json=record["source_settings"],
                refresh_status_json=record["refresh_status"],
                lifecycle_state_json=record["lifecycle_state"],
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
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise ValueError(
                "Instrument identifiers or generated id conflict with an existing instrument."
            ) from error
        return _serialize_record(record)


MarketDataKey = tuple[str, str, date, str]


def _normalize_market_data_upserts(
    rows: Iterable[dict[str, object]],
    *,
    allow_withdrawn: bool = False,
) -> dict[MarketDataKey, dict[str, object]]:
    raw_rows = list(rows)
    if not raw_rows:
        raise ValueError("market-data batch must contain at least one row")
    normalized_by_key: dict[MarketDataKey, dict[str, object]] = {}
    for row_number, row in enumerate(raw_rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"market-data row {row_number} must be an object")
        if "provider" in row:
            raise ValueError(
                "market-data provider was removed; use source_ref for quote provenance"
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
                    f"market-data row {row_number} has an invalid as_of_date"
                ) from error
        metric_family = str(row.get("metric_family") or "").strip().lower()
        quote_basis = str(row.get("quote_basis") or "").strip().lower()
        currency = _normalize_required_currency(
            row.get("currency"),
            context=f"Market-data row {row_number}",
        )
        if (
            metric_family not in VALID_METRIC_FAMILIES
            or quote_basis not in VALID_QUOTE_BASES
            or VALID_QUOTE_BASES[quote_basis] != metric_family
        ):
            raise ValueError(
                f"market-data row {row_number} has an invalid canonical quote identity"
            )
        status = normalize_revision_status(row.get("status"))
        if status == WITHDRAWN_STATUS and not allow_withdrawn:
            raise ValueError(
                "withdrawn revisions may only be created by replacement workflows"
            )
        if status != WITHDRAWN_STATUS and status not in SOURCE_OBSERVATION_STATUSES:
            raise ValueError(f'unsupported source observation status "{status}"')
        numeric_evidence = (
            None
            if status == WITHDRAWN_STATUS
            else quote_numeric_evidence(
                row.get("value"),
                value_input_scale=row.get("value_input_scale"),
                numeric_scale_state=row.get("numeric_scale_state"),
            )
        )
        value = numeric_evidence.value if numeric_evidence is not None else None
        if value is not None and Decimal(value) <= 0:
            raise ValueError("quote value must be positive")
        source_ref = normalize_source_ref(row.get("source_ref"))
        source_published_at = normalize_optional_timestamp(
            row.get("source_published_at")
        )
        key = (metric_family, quote_basis, point_date, currency)
        if key in normalized_by_key:
            raise ValueError(
                "market-data batch contains a duplicate canonical observation: "
                f"{metric_family}/{quote_basis}/{point_date.isoformat()}/{currency}"
            )
        normalized_by_key[key] = {
            "value": value,
            "value_input_scale": (
                numeric_evidence.value_input_scale
                if numeric_evidence is not None
                else None
            ),
            "numeric_scale_state": (
                numeric_evidence.numeric_scale_state
                if numeric_evidence is not None
                else None
            ),
            "payload_schema_version": QUOTE_REVISION_PAYLOAD_SCHEMA_V2,
            "source_ref": source_ref,
            "status": status,
            "source_published_at": source_published_at,
            "payload_hash": quote_revision_payload_hash(
                value=value,
                source_ref=source_ref,
                status=status,
                source_published_at=source_published_at,
                payload_schema_version=QUOTE_REVISION_PAYLOAD_SCHEMA_V2,
                value_input_scale=(
                    numeric_evidence.value_input_scale
                    if numeric_evidence is not None
                    else None
                ),
                numeric_scale_state=(
                    numeric_evidence.numeric_scale_state
                    if numeric_evidence is not None
                    else None
                ),
            ),
        }
    return normalized_by_key


def _append_market_data_revisions(
    session: Session,
    *,
    target: Instrument,
    normalized_by_key: dict[MarketDataKey, dict[str, object]],
    ingested_at: datetime,
) -> int:
    if not normalized_by_key:
        return 0

    series_by_key = {
        (series.metric_family, series.quote_basis, series.currency): series
        for series in session.scalars(
            select(QuoteSeries).where(QuoteSeries.instrument_id == target.instrument_id)
        ).all()
    }
    for metric_family, quote_basis, _, currency in normalized_by_key:
        series_key = (metric_family, quote_basis, currency)
        if series_key in series_by_key:
            continue
        series = QuoteSeries(
            quote_series_id=make_quote_series_id(
                instrument_id=target.instrument_id,
                metric_family=metric_family,
                quote_basis=quote_basis,
                currency=currency,
            ),
            instrument_id=target.instrument_id,
            metric_family=metric_family,
            quote_basis=quote_basis,
            currency=currency,
            data_updated_at=None,
        )
        series_by_key[series_key] = series
        session.add(series)
    session.flush()

    desired_observation_keys = {
        (
            series_by_key[(metric_family, quote_basis, currency)].quote_series_id,
            point_date,
        )
        for metric_family, quote_basis, point_date, currency in normalized_by_key
    }
    relevant_series_ids = sorted({key[0] for key in desired_observation_keys})
    relevant_dates = sorted({key[1] for key in desired_observation_keys})
    observations_by_key = {
        (observation.quote_series_id, observation.as_of_date): observation
        for observation in session.scalars(
            select(QuoteObservation).where(
                QuoteObservation.quote_series_id.in_(relevant_series_ids),
                QuoteObservation.as_of_date.in_(relevant_dates),
            )
        ).all()
        if (observation.quote_series_id, observation.as_of_date)
        in desired_observation_keys
    }
    for series_id, point_date in desired_observation_keys:
        observation_key = (series_id, point_date)
        if observation_key in observations_by_key:
            continue
        observation = QuoteObservation(
            observation_id=make_quote_observation_id(
                quote_series_id=series_id,
                as_of_date=point_date,
            ),
            quote_series_id=series_id,
            as_of_date=point_date,
        )
        observations_by_key[observation_key] = observation
        session.add(observation)
    session.flush()

    observation_ids = sorted(
        observation.observation_id for observation in observations_by_key.values()
    )
    current_by_observation_id = {
        revision.observation_id: revision
        for revision in session.scalars(
            select(QuoteObservationRevision).where(
                QuoteObservationRevision.observation_id.in_(observation_ids),
                QuoteObservationRevision.is_current.is_(True),
            )
        ).all()
    }
    max_revision_by_observation_id = {
        str(observation_id): int(max_revision_number)
        for observation_id, max_revision_number in session.execute(
            select(
                QuoteObservationRevision.observation_id,
                func.max(QuoteObservationRevision.revision_number),
            )
            .where(QuoteObservationRevision.observation_id.in_(observation_ids))
            .group_by(QuoteObservationRevision.observation_id)
        ).all()
    }

    changed_count = 0
    changed_series: dict[str, QuoteSeries] = {}
    pending_revisions: list[QuoteObservationRevision] = []
    for (
        metric_family,
        quote_basis,
        point_date,
        currency,
    ), payload in normalized_by_key.items():
        series = series_by_key[(metric_family, quote_basis, currency)]
        observation = observations_by_key[(series.quote_series_id, point_date)]
        current = current_by_observation_id.get(observation.observation_id)
        if current is not None and current.payload_hash == payload["payload_hash"]:
            continue
        if current is not None:
            # Payload/provenance is immutable. Only revision lifecycle is closed.
            current.is_current = False
            current.superseded_at = ingested_at
        revision_number = (
            max_revision_by_observation_id.get(observation.observation_id, 0) + 1
        )
        max_revision_by_observation_id[observation.observation_id] = revision_number
        pending_revisions.append(
            QuoteObservationRevision(
                revision_id=make_quote_revision_id(
                    observation_id=observation.observation_id,
                    revision_number=revision_number,
                ),
                observation_id=observation.observation_id,
                revision_number=revision_number,
                value=payload["value"],
                value_input_scale=payload["value_input_scale"],
                numeric_scale_state=payload["numeric_scale_state"],
                payload_schema_version=int(payload["payload_schema_version"]),
                source_ref=payload["source_ref"],
                status=str(payload["status"]),
                source_published_at=payload["source_published_at"],
                ingested_at=ingested_at,
                payload_hash=str(payload["payload_hash"]),
                is_current=True,
                superseded_at=None,
            )
        )
        changed_count += 1
        changed_series[series.quote_series_id] = series

    if changed_count:
        # Close all prior currents before inserting replacements so the partial
        # unique current index is never transiently violated.
        session.flush()
        session.add_all(pending_revisions)
        watermark = _next_market_data_watermark(session)
        target.market_data_updated_at = watermark
        for series in changed_series.values():
            series.data_updated_at = watermark
    return changed_count


def upsert_market_data(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    metric_family: str,
    quote_basis: str,
    as_of_date: date,
    value: object,
    currency: str,
    source_ref: str | None,
    status: str,
    source_published_at: datetime | str | None = None,
) -> dict[str, object] | None:
    normalized_by_key = _normalize_market_data_upserts(
        [
            {
                "metric_family": metric_family,
                "quote_basis": quote_basis,
                "as_of_date": as_of_date,
                "value": value,
                "currency": currency,
                "source_ref": source_ref,
                "status": status,
                "source_published_at": source_published_at,
            }
        ]
    )
    if not normalized_by_key:
        raise ValueError("market-data point is invalid")
    with session_factory() as session:
        target = session.scalar(
            select(Instrument)
            .where(Instrument.instrument_id == instrument_id)
            .with_for_update()
        )
        if target is None:
            return None
        _append_market_data_revisions(
            session,
            target=target,
            normalized_by_key=normalized_by_key,
            ingested_at=_utcnow(),
        )
        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


def upsert_market_data_points(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
) -> int | None:
    """Append changed source observations in one instrument-serialized transaction."""
    normalized_by_key = _normalize_market_data_upserts(rows)
    with session_factory() as session:
        target = session.scalar(
            select(Instrument)
            .where(Instrument.instrument_id == instrument_id)
            .with_for_update()
        )
        if target is None:
            return None
        changed_count = _append_market_data_revisions(
            session,
            target=target,
            normalized_by_key=normalized_by_key,
            ingested_at=_utcnow(),
        )
        session.commit()
    return changed_count


def upsert_source_settings(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    source_mode: str,
    source_email: str | None,
    source_location: str | None,
    source_api_profile: str | None,
    source_email_rules: list[dict[str, object]] | None,
) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.get(Instrument, instrument_id)
        if target is None:
            return None

        store_item = _instrument_to_store_dict(target)
        source_settings = _normalized_source_settings(store_item)
        source_settings["source_mode"] = source_mode
        if source_email is not None:
            source_settings["source_email"] = source_email.strip()
        if source_location is not None:
            source_settings["source_location"] = (
                source_location.strip() or "Shared data ops"
            )
        if source_api_profile is not None:
            source_settings["source_api_profile"] = source_api_profile.strip()
        if source_email_rules is not None:
            source_settings["source_email_rules"] = [
                dict(rule) for rule in source_email_rules if isinstance(rule, dict)
            ]
        refresh_status = _normalized_refresh_status(store_item)
        refresh_status["mode"] = source_mode
        target.source_settings_json = source_settings
        target.refresh_status_json = refresh_status
        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


def upsert_quote_selection_policy(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    quote_selection_policy: dict[str, object],
) -> dict[str, object] | None:
    validate_quote_selection_policy(quote_selection_policy)
    with session_factory() as session:
        target = session.get(Instrument, instrument_id)
        if target is None:
            return None

        current_store_item = _instrument_to_store_dict(target)
        current_policy = _normalized_quote_selection_policy(current_store_item)
        incoming_store_item = dict(current_store_item)
        incoming_store_item["quote_selection_policy"] = quote_selection_policy
        incoming_policy = _normalized_quote_selection_policy(incoming_store_item)
        if canonical_quote_selection_policy_revision(
            current_policy
        ) != canonical_quote_selection_policy_revision(incoming_policy):
            target.quote_selection_policy_json = incoming_policy
            target.market_data_updated_at = _next_market_data_watermark(session)
        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


def replace_nav_history(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
    source_ref: str | None,
    point_status: str,
    refresh_status: str,
    updated_by: str | None,
    message: str,
    mode: str | None = None,
    replace_all: bool = False,
) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.scalar(
            select(Instrument)
            .where(Instrument.instrument_id == instrument_id)
            .with_for_update()
        )
        if target is None:
            return None

        default_currency = target.currency.upper()
        point_source_ref = normalize_source_ref(source_ref)
        if not rows:
            raise ValueError("NAV replacement batch must contain at least one row")
        source_rows: list[dict[str, object]] = []
        for row_number, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"NAV replacement row {row_number} must be an object")
            raw_date = str(row.get("as_of_date") or "").strip()
            if not raw_date:
                raise ValueError(
                    f"NAV replacement row {row_number} is missing as_of_date"
                )
            try:
                point_date = date.fromisoformat(raw_date)
            except ValueError as error:
                raise ValueError(
                    f"NAV replacement row {row_number} has an invalid as_of_date"
                ) from error
            row_currency = _normalize_required_currency(
                row.get("currency") or default_currency,
                context=f"NAV replacement row {row_number}",
            )
            row_observation_count = 0
            for row_key, quote_basis in (
                ("nav", "official_nav"),
                ("cumulative_nav", "cumulative_nav"),
                ("nav_with_dividend", "total_return_nav"),
            ):
                row_value = row.get(row_key)
                if row_value is None:
                    continue
                row_observation_count += 1
                source_rows.append(
                    {
                        "metric_family": "nav",
                        "quote_basis": quote_basis,
                        "as_of_date": point_date,
                        "value": row_value,
                        "value_input_scale": row.get(f"{row_key}_input_scale"),
                        "numeric_scale_state": row.get(
                            f"{row_key}_numeric_scale_state"
                        ),
                        "currency": row_currency,
                        "source_ref": point_source_ref,
                        "status": point_status,
                        "source_published_at": row.get("source_published_at"),
                    }
                )
            if row_observation_count == 0:
                raise ValueError(
                    f"NAV replacement row {row_number} contains no NAV observation"
                )

        normalized_by_key = _normalize_market_data_upserts(source_rows)
        replacement_dates = {key[2] for key in normalized_by_key}
        nav_replace_bases = frozenset(
            {
                "official_nav",
                "total_return_nav",
                "cumulative_nav",
                "accumulated_nav",
                "cum_nav",
                "dividend_adjusted_nav",
                "reinvested_nav",
            }
        )
        existing_current_rows = session.execute(
            select(
                QuoteSeries.metric_family,
                QuoteSeries.quote_basis,
                QuoteObservation.as_of_date,
                QuoteSeries.currency,
            )
            .join(
                QuoteObservation,
                QuoteObservation.quote_series_id == QuoteSeries.quote_series_id,
            )
            .join(
                QuoteObservationRevision,
                QuoteObservationRevision.observation_id
                == QuoteObservation.observation_id,
            )
            .where(
                QuoteSeries.instrument_id == instrument_id,
                QuoteSeries.metric_family == "nav",
                QuoteSeries.quote_basis.in_(nav_replace_bases),
                QuoteObservationRevision.is_current.is_(True),
                QuoteObservationRevision.status != WITHDRAWN_STATUS,
            )
        ).all()
        incoming_keys = set(normalized_by_key)
        for metric_family, quote_basis, point_date, currency in existing_current_rows:
            existing_key: MarketDataKey = (
                str(metric_family),
                str(quote_basis),
                point_date,
                str(currency),
            )
            in_replacement_scope = replace_all or point_date in replacement_dates
            if not in_replacement_scope or existing_key in incoming_keys:
                continue
            normalized_by_key.update(
                _normalize_market_data_upserts(
                    [
                        {
                            "metric_family": metric_family,
                            "quote_basis": quote_basis,
                            "as_of_date": point_date,
                            "value": None,
                            "currency": currency,
                            "source_ref": point_source_ref,
                            "status": WITHDRAWN_STATUS,
                        }
                    ],
                    allow_withdrawn=True,
                )
            )

        _append_market_data_revisions(
            session,
            target=target,
            normalized_by_key=normalized_by_key,
            ingested_at=_utcnow(),
        )

        store_item = _instrument_to_store_dict(target)
        source_settings = _normalized_source_settings(store_item)
        source_mode = str(source_settings.get("source_mode") or "manual")
        requested_at = _utcnow_iso()
        target.refresh_status_json = _updated_refresh_status(
            previous=dict(store_item.get("refresh_status", {})),
            status=refresh_status,
            message=message,
            updated_by=updated_by,
            mode=mode or source_mode,
            requested_at=requested_at,
            previous_mode_fallback=source_mode,
        )
        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


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
            previous_mode_fallback=configured_source_mode,
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
