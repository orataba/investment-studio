from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from portfolio_ops_instrument_core.db_models import (
    CorporateActionEvent,
    Instrument,
    InstrumentIdentifier,
    InstrumentMarketData,
    RegistryMetadata,
)
from portfolio_ops_instrument_core.models import (
    CASH_CUMULATIVE_NAV_BASES,
    CorporateActionEvent as CorporateActionEventModel,
    VALUATION_PROHIBITED_TOTAL_RETURN_BASES,
)


DEFAULT_REGISTRY_NAME = "Portfolio Operations Shared Instruments"
EMPTY_STORE: dict[str, object] = {
    "registry_name": DEFAULT_REGISTRY_NAME,
    "instruments": [],
}

SessionFactory = Callable[[], Session]
EMAIL_REFRESH_SUCCESS_STATUSES = frozenset({"imported", "no_match", "no_new_data"})


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


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
    previous_cursor = str(previous.get("last_successful_requested_at") or "").strip() or None
    if previous_cursor is None:
        previous_status = str(previous.get("status") or "").strip().lower()
        previous_mode = str(previous.get("mode") or previous_mode_fallback).strip().lower()
        previous_requested_at = str(previous.get("requested_at") or "").strip() or None
        if (
            previous_mode == "email"
            and previous_status in EMAIL_REFRESH_SUCCESS_STATUSES
            and previous_requested_at is not None
        ):
            previous_cursor = previous_requested_at

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
    "fund": {
        "trading": ["last", "close", "official_nav"],
        "valuation": ["official_nav", "close", "last"],
        "total_return": [
            "total_return_nav",
            "dividend_adjusted_nav",
            "reinvested_nav",
            "adjusted_close",
            "official_nav",
            "close",
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
    "bond": {
        "trading": ["clean_price", "dirty_price"],
        "valuation": ["dirty_price", "clean_price"],
        "total_return": ["dirty_price", "clean_price"],
        "chart": ["dirty_price", "clean_price"],
        "reference": ["clean_price", "dirty_price"],
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

VALID_METRIC_FAMILIES = {"price", "nav", "fx"}
VALID_QUOTE_BASES = {
    "last": "price",
    "close": "price",
    "adjusted_close": "price",
    "official_nav": "nav",
    "total_return_nav": "nav",
    "cumulative_nav": "nav",
    "accumulated_nav": "nav",
    "cum_nav": "nav",
    "dividend_adjusted_nav": "nav",
    "reinvested_nav": "nav",
    "spot": "fx",
    "clean_price": "price",
    "dirty_price": "price",
    "par": "price",
}


def _default_quote_selection_policy(instrument_type: str) -> dict[str, object]:
    normalized_instrument_type = instrument_type if instrument_type in QUOTE_SELECTION_POLICY_DEFAULTS else "other"
    return deepcopy(QUOTE_SELECTION_POLICY_DEFAULTS[normalized_instrument_type])


def validate_quote_selection_policy(quote_selection_policy: dict[str, object]) -> None:
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
                + ". Use total_return_nav/dividend_adjusted_nav, or explicitly fall back to official_nav as an ordinary price-return series."
            )


def _normalized_quote_selection_policy(item: dict[str, object]) -> dict[str, object]:
    instrument_type = str(item.get("instrument_type") or "other")
    policy = _default_quote_selection_policy(instrument_type)
    raw_policy = item.get("quote_selection_policy", {})
    if not isinstance(raw_policy, dict):
        return policy

    for role, fallback_bases in policy.items():
        raw_values = raw_policy.get(role)
        if not isinstance(raw_values, list):
            continue
        normalized_values: list[str] = []
        for raw_value in raw_values:
            quote_basis = str(raw_value or "").strip()
            if quote_basis in VALID_QUOTE_BASES and quote_basis not in normalized_values:
                normalized_values.append(quote_basis)
        if normalized_values:
            policy[role] = normalized_values
        else:
            policy[role] = list(fallback_bases)
    return policy


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
    instrument_currency = str(item.get("currency") or "USD").strip().upper() or "USD"
    normalized_points: list[dict[str, object]] = []
    for raw_point in list(item.get("market_data", [])):
        point = dict(raw_point)
        metric_family = str(point.get("metric_family") or "").strip()
        quote_basis = str(point.get("quote_basis") or "").strip()
        if metric_family not in VALID_METRIC_FAMILIES:
            continue
        if quote_basis not in VALID_QUOTE_BASES:
            continue
        if VALID_QUOTE_BASES[quote_basis] != metric_family:
            continue
        normalized_points.append(
            {
                "metric_family": metric_family,
                "quote_basis": quote_basis,
                "as_of_date": str(point.get("as_of_date") or ""),
                "value": "" if point.get("value") is None else str(point.get("value")),
                "currency": str(point.get("currency") or instrument_currency).strip().upper() or instrument_currency,
                "provider": point.get("provider"),
                "status": str(point.get("status") or "complete"),
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


def _instrument_to_store_dict(
    item: Instrument,
    *,
    market_data: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    identifiers = _serialize_identifier_rows(item)
    resolved_market_data = market_data
    if resolved_market_data is None:
        resolved_market_data = [
            {
                "metric_family": point.metric_family,
                "quote_basis": point.quote_basis,
                "as_of_date": point.as_of_date.isoformat(),
                "value": point.value,
                "currency": point.currency,
                "provider": point.provider,
                "status": point.status,
            }
            for point in item.market_data_points
        ]
    corporate_actions = [
        {
            "corporate_action_event_id": event.corporate_action_event_id,
            "instrument_id": event.instrument_id,
            "action_type": event.action_type,
            "announcement_date": event.announcement_date.isoformat() if event.announcement_date else None,
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

    session.execute(delete(CorporateActionEvent))
    session.execute(delete(InstrumentMarketData))
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
            continue
        item = dict(raw_item)
        instrument = Instrument(
            instrument_id=str(item.get("instrument_id") or "").strip(),
            instrument_name=str(item.get("instrument_name") or "").strip(),
            instrument_type=str(item.get("instrument_type") or "").strip() or "other",
            currency=str(item.get("currency") or "USD").strip().upper() or "USD",
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

        for raw_point in _normalized_market_data(item):
            try:
                as_of_date = date.fromisoformat(str(raw_point.get("as_of_date") or ""))
            except ValueError:
                continue
            session.add(
                InstrumentMarketData(
                    instrument_id=instrument.instrument_id,
                    metric_family=str(raw_point.get("metric_family") or "").strip(),
                    quote_basis=str(raw_point.get("quote_basis") or "").strip(),
                    as_of_date=as_of_date,
                    value=str(raw_point.get("value") or ""),
                    currency=str(raw_point.get("currency") or instrument.currency).strip().upper()
                    or instrument.currency,
                    provider=(
                        str(raw_point.get("provider")).strip()
                        if raw_point.get("provider") is not None
                        else None
                    ),
                    status=str(raw_point.get("status") or "complete").strip() or "complete",
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
    latest_by_basis: dict[tuple[str, str], dict[str, object]] = {}
    for point in points:
        basis_key = (
            str(point.get("metric_family") or ""),
            str(point.get("quote_basis") or ""),
        )
        as_of_date = str(point.get("as_of_date") or "")
        current = latest_by_basis.get(basis_key)
        if current is None or as_of_date >= str(current.get("as_of_date") or ""):
            latest_by_basis[basis_key] = point
    return [latest_by_basis[key] for key in sorted(latest_by_basis.keys())]


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
        "identifiers": deepcopy(item.get("identifiers", [])),
        "latest_market_data": _latest_market_data(market_data),
        "quote_selection_policy": _normalized_quote_selection_policy(item),
        "coverage_state": _coverage_state(market_data),
        "market_data_updated_at": item.get("market_data_updated_at"),
        "source_settings": _normalized_source_settings(item),
        "refresh_status": _normalized_refresh_status(item),
        "lifecycle_state": _normalized_lifecycle_state(item),
        "corporate_actions": deepcopy(item.get("corporate_actions", [])),
    }


def _serialize_detail_record(item: dict[str, object]) -> dict[str, object]:
    record = _serialize_record(item)
    record["market_data"] = deepcopy(_normalized_market_data(item))
    return record


def _instrument_query(*, include_market_data: bool = True):
    eager_loads = [
        selectinload(Instrument.identifiers),
        selectinload(Instrument.corporate_action_events),
    ]
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
            InstrumentMarketData.provider.label("provider"),
            InstrumentMarketData.status.label("status"),
            func.row_number()
            .over(
                partition_by=(
                    InstrumentMarketData.instrument_id,
                    InstrumentMarketData.metric_family,
                    InstrumentMarketData.quote_basis,
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
                "provider": row["provider"],
                "status": row["status"],
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
        statement = _instrument_query(include_market_data=False)
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
        details_by_id = {
            target.instrument_id: _serialize_detail_record(_instrument_to_store_dict(target))
            for target in targets
        }
    return {
        instrument_id: details_by_id.get(instrument_id)
        for instrument_id in normalized_ids
    }


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
                "announcement_date": event.announcement_date.isoformat() if event.announcement_date else None,
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
    normalized_currency = currency.strip().upper()
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
    if not normalized_currency:
        raise ValueError("Instrument currency must not be blank.")
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
            raise ValueError("Instrument identifiers or generated id conflict with an existing instrument.") from error
        return _serialize_record(record)


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
) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.scalar(
            _instrument_query()
            .where(Instrument.instrument_id == instrument_id)
            .with_for_update()
        )
        if target is None:
            return None

        existing = session.scalar(
            select(InstrumentMarketData).where(
                InstrumentMarketData.instrument_id == instrument_id,
                InstrumentMarketData.metric_family == metric_family,
                InstrumentMarketData.quote_basis == quote_basis,
                InstrumentMarketData.as_of_date == as_of_date,
                InstrumentMarketData.currency == currency.upper(),
            )
        )
        if existing is None:
            session.add(
                InstrumentMarketData(
                    instrument_id=instrument_id,
                    metric_family=metric_family,
                    quote_basis=quote_basis,
                    as_of_date=as_of_date,
                    value=value,
                    currency=currency.upper(),
                    provider=provider,
                    status=status,
                )
            )
        else:
            existing.value = value
            existing.provider = provider
            existing.status = status

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
    normalized_by_key: dict[
        tuple[str, str, date, str], dict[str, object]
    ] = {}
    for row in rows:
        raw_date = row.get("as_of_date")
        if isinstance(raw_date, datetime):
            point_date = raw_date.date()
        elif isinstance(raw_date, date):
            point_date = raw_date
        else:
            try:
                point_date = date.fromisoformat(str(raw_date or "").strip())
            except ValueError:
                continue
        metric_family = str(row.get("metric_family") or "").strip()
        quote_basis = str(row.get("quote_basis") or "").strip()
        currency = str(row.get("currency") or "").strip().upper()
        value = row.get("value")
        if not metric_family or not quote_basis or not currency or value is None:
            continue
        normalized_by_key[(metric_family, quote_basis, point_date, currency)] = {
            "value": str(value),
            "provider": (
                str(row.get("provider")).strip()
                if row.get("provider") is not None
                else None
            ),
            "status": str(row.get("status") or "complete").strip() or "complete",
        }
    if not normalized_by_key:
        return 0

    with session_factory() as session:
        target = session.scalar(
            select(Instrument)
            .where(Instrument.instrument_id == instrument_id)
            .with_for_update()
        )
        if target is None:
            return None
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
                        provider=payload["provider"],
                        status=str(payload["status"]),
                    )
                )
            else:
                if (
                    existing.value == str(payload["value"])
                    and existing.provider == payload["provider"]
                    and existing.status == str(payload["status"])
                ):
                    continue
                changed_count += 1
                existing.value = str(payload["value"])
                existing.provider = payload["provider"]
                existing.status = str(payload["status"])
        if changed_count:
            target.market_data_updated_at = _next_market_data_watermark(session)
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
            source_settings["source_location"] = source_location.strip() or "Shared data ops"
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

        store_item = _instrument_to_store_dict(target)
        store_item["quote_selection_policy"] = quote_selection_policy
        target.quote_selection_policy_json = _normalized_quote_selection_policy(store_item)
        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


def replace_nav_history(
    session_factory: SessionFactory,
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
    provider: str | None,
    point_status: str,
    refresh_status: str,
    updated_by: str | None,
    message: str,
    mode: str | None = None,
) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.scalar(
            select(Instrument)
            .where(Instrument.instrument_id == instrument_id)
            .with_for_update()
        )
        if target is None:
            return None

        replaced_dates: set[date] = set()
        for row in rows:
            raw_date = str(row.get("as_of_date") or "").strip()
            if not raw_date:
                continue
            try:
                point_date = date.fromisoformat(raw_date)
            except ValueError:
                continue
            replaced_dates.add(point_date)

        if replaced_dates:
            session.execute(
                delete(InstrumentMarketData).where(
                    InstrumentMarketData.instrument_id == instrument_id,
                    InstrumentMarketData.metric_family == "nav",
                    InstrumentMarketData.quote_basis.in_(
                        [
                            "official_nav",
                            "total_return_nav",
                            "cumulative_nav",
                            "accumulated_nav",
                            "cum_nav",
                            "dividend_adjusted_nav",
                            "reinvested_nav",
                        ]
                    ),
                    InstrumentMarketData.as_of_date.in_(sorted(replaced_dates)),
                )
            )

        default_currency = target.currency.upper()
        point_provider = (provider or "shared_nav_import").strip() or "shared_nav_import"
        for row in rows:
            raw_date = str(row.get("as_of_date") or "").strip()
            if not raw_date:
                continue
            try:
                point_date = date.fromisoformat(raw_date)
            except ValueError:
                continue
            row_currency = str(row.get("currency") or default_currency).strip().upper() or default_currency
            for row_key, quote_basis in (
                ("nav", "official_nav"),
                ("cumulative_nav", "cumulative_nav"),
                ("nav_with_dividend", "total_return_nav"),
            ):
                row_value = row.get(row_key)
                if row_value is None:
                    continue
                session.add(
                    InstrumentMarketData(
                        instrument_id=instrument_id,
                        metric_family="nav",
                        quote_basis=quote_basis,
                        as_of_date=point_date,
                        value=str(row_value),
                        currency=row_currency,
                        provider=point_provider,
                        status=point_status,
                    )
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
        target.market_data_updated_at = _next_market_data_watermark(session)
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
