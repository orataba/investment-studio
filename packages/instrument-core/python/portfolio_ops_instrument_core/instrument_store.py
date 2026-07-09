from __future__ import annotations

import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from portfolio_ops_instrument_core.db_models import (
    Instrument,
    InstrumentIdentifier,
    InstrumentMarketData,
    RegistryMetadata,
)


DEFAULT_REGISTRY_NAME = "Portfolio Operations Shared Instruments"
EMPTY_STORE: dict[str, object] = {
    "registry_name": DEFAULT_REGISTRY_NAME,
    "instruments": [],
}

SessionFactory = Callable[[], Session]


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    }


def _default_lifecycle_state(
    status: str = "active",
    *,
    changed_at: str | None = None,
    changed_by: str | None = None,
) -> dict[str, object]:
    normalized_status = status if status in {"active", "archived"} else "active"
    return {
        "status": normalized_status,
        "changed_at": changed_at,
        "changed_by": changed_by,
    }


QUOTE_SELECTION_POLICY_DEFAULTS: dict[str, dict[str, list[str]]] = {
    "fund": {
        "trading": ["last", "close", "official_nav"],
        "valuation": ["official_nav", "close", "last"],
        "total_return": [
            "total_return_nav",
            "cumulative_nav",
            "accumulated_nav",
            "cum_nav",
            "dividend_adjusted_nav",
            "reinvested_nav",
            "adjusted_close",
            "official_nav",
            "close",
        ],
        "chart": [
            "total_return_nav",
            "cumulative_nav",
            "accumulated_nav",
            "cum_nav",
            "dividend_adjusted_nav",
            "reinvested_nav",
            "adjusted_close",
            "official_nav",
            "close",
        ],
        "reference": ["official_nav", "close", "last"],
    },
    "equity": {
        "trading": ["last", "close"],
        "valuation": ["close", "adjusted_close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    },
    "index": {
        "trading": ["close", "last"],
        "valuation": ["close", "adjusted_close", "last"],
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


def _instrument_to_store_dict(item: Instrument) -> dict[str, object]:
    identifiers = [
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
    market_data = _sort_market_data(
        [
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
    )
    return {
        "instrument_id": item.instrument_id,
        "instrument_name": item.instrument_name,
        "instrument_type": item.instrument_type,
        "currency": item.currency,
        "identifiers": identifiers,
        "market_data": market_data,
        "quote_selection_policy": deepcopy(item.quote_selection_policy_json or {}),
        "source_settings": deepcopy(item.source_settings_json or {}),
        "refresh_status": deepcopy(item.refresh_status_json or {}),
        "lifecycle_state": deepcopy(item.lifecycle_state_json or {}),
    }


def _save_store_to_db(session: Session, data: dict[str, object]) -> None:
    normalized = _normalize_store(data)

    session.execute(delete(InstrumentMarketData))
    session.execute(delete(InstrumentIdentifier))
    session.execute(delete(Instrument))

    metadata_record = session.get(RegistryMetadata, "shared")
    if metadata_record is None:
        metadata_record = RegistryMetadata(
            registry_key="shared",
            registry_name=str(normalized.get("registry_name") or DEFAULT_REGISTRY_NAME),
        )
        session.add(metadata_record)
    else:
        metadata_record.registry_name = str(normalized.get("registry_name") or DEFAULT_REGISTRY_NAME)

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

    if isinstance(raw_lifecycle, dict):
        status = str(raw_lifecycle.get("status") or "active").strip().lower() or "active"
        raw_changed_at = raw_lifecycle.get("changed_at")
        raw_changed_by = raw_lifecycle.get("changed_by")
        changed_at = str(raw_changed_at).strip() if raw_changed_at else None
        changed_by = str(raw_changed_by).strip() if raw_changed_by else None
    elif item.get("is_active") is False:
        status = "archived"

    return _default_lifecycle_state(
        status=status,
        changed_at=changed_at,
        changed_by=changed_by,
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
        "source_settings": _normalized_source_settings(item),
        "refresh_status": _normalized_refresh_status(item),
        "lifecycle_state": _normalized_lifecycle_state(item),
    }


def _serialize_detail_record(item: dict[str, object]) -> dict[str, object]:
    record = _serialize_record(item)
    record["market_data"] = deepcopy(_normalized_market_data(item))
    return record


def _matches_instrument_search(item: dict[str, object], normalized_search: str) -> bool:
    if not normalized_search:
        return True

    haystack_parts = [
        str(item.get("instrument_id") or ""),
        str(item.get("instrument_name") or ""),
        str(item.get("instrument_type") or ""),
        str(item.get("currency") or ""),
    ]
    for identifier in item.get("identifiers", []):
        if not isinstance(identifier, dict):
            continue
        haystack_parts.append(str(identifier.get("identifier_type") or ""))
        haystack_parts.append(str(identifier.get("identifier_value") or ""))

    haystack = " ".join(part.strip().lower() for part in haystack_parts if str(part).strip())
    return normalized_search in haystack


def _instrument_query():
    return (
        select(Instrument)
        .options(
            selectinload(Instrument.identifiers),
            selectinload(Instrument.market_data_points),
        )
    )


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
        instruments = [
            _instrument_to_store_dict(item)
            for item in session.scalars(
                _instrument_query().order_by(Instrument.instrument_name, Instrument.instrument_id)
            ).all()
        ]

    filtered = []
    for item in instruments:
        if not include_inactive and not _is_active(item):
            continue
        current_instrument_type = str(item.get("instrument_type") or "").strip().lower()
        if normalized_instrument_type and current_instrument_type != normalized_instrument_type:
            continue
        if normalized_search and not _matches_instrument_search(item, normalized_search):
            continue
        filtered.append(item)

    if limit is not None:
        filtered = filtered[:limit]
    return [_serialize_record(item) for item in filtered]


def get_instrument(session_factory: SessionFactory, instrument_id: str) -> dict[str, object] | None:
    with session_factory() as session:
        target = session.scalar(_instrument_query().where(Instrument.instrument_id == instrument_id))
        if target is None:
            return None
        return _serialize_detail_record(_instrument_to_store_dict(target))


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
) -> dict[str, object]:
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

        record = {
            "instrument_id": candidate,
            "instrument_name": instrument_name.strip(),
            "instrument_type": instrument_type,
            "currency": currency.strip().upper(),
            "identifiers": identifiers,
            "market_data": [],
            "quote_selection_policy": _default_quote_selection_policy(instrument_type),
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
        session.commit()
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
        target = session.scalar(_instrument_query().where(Instrument.instrument_id == instrument_id))
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

        session.commit()
    refreshed = get_instrument(session_factory, instrument_id)
    return _serialize_record(refreshed) if refreshed is not None else None


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
        target = session.get(Instrument, instrument_id)
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

        source_settings = _normalized_source_settings(_instrument_to_store_dict(target))
        target.refresh_status_json = {
            "status": refresh_status,
            "message": message,
            "requested_at": _utcnow_iso(),
            "requested_by": (updated_by or "platform_ui").strip() or "platform_ui",
            "mode": mode or str(source_settings.get("source_mode") or "manual"),
        }
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
        source_mode = mode or str(
            _normalized_source_settings(_instrument_to_store_dict(target)).get("source_mode") or "manual"
        )
        target.refresh_status_json = {
            "status": status,
            "message": message,
            "requested_at": _utcnow_iso(),
            "requested_by": (updated_by or "platform_ui").strip() or "platform_ui",
            "mode": source_mode,
        }
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
