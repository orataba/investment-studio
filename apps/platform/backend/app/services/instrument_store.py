from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.db.models import Instrument, InstrumentIdentifier, InstrumentMarketData, RegistryMetadata
from app.db.session import get_session_factory
from app.services.watchlist_recalc import schedule_watchlist_recalc

DEFAULT_REGISTRY_NAME = "Yungu Shared Instruments"
EMPTY_STORE: dict[str, object] = {
    "registry_name": DEFAULT_REGISTRY_NAME,
    "instruments": [],
}


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
        "total_return": ["total_return_nav", "adjusted_close", "official_nav", "close"],
        "chart": ["total_return_nav", "adjusted_close", "official_nav", "close"],
        "reference": ["official_nav", "close", "last"],
    },
    "equity": {
        "trading": ["last", "close"],
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
    "spot": "fx",
    "clean_price": "price",
    "dirty_price": "price",
    "par": "price",
}


def _default_quote_selection_policy(asset_type: str) -> dict[str, object]:
    normalized_asset_type = asset_type if asset_type in QUOTE_SELECTION_POLICY_DEFAULTS else "other"
    return deepcopy(QUOTE_SELECTION_POLICY_DEFAULTS[normalized_asset_type])


def _normalized_quote_selection_policy(item: dict[str, object]) -> dict[str, object]:
    asset_type = str(item.get("asset_type") or "other")
    policy = _default_quote_selection_policy(asset_type)
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


def _normalized_market_data(item: dict[str, object]) -> list[dict[str, object]]:
    asset_currency = str(item.get("currency") or "USD").strip().upper() or "USD"
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
                "currency": str(point.get("currency") or asset_currency).strip().upper() or asset_currency,
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


def reset_store(data: dict[str, object] | None = None) -> None:
    payload = data if data is not None else EMPTY_STORE
    normalized = _normalize_store(deepcopy(payload))
    session_factory = get_session_factory()
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
        "asset_id": item.asset_id,
        "asset_name": item.asset_name,
        "asset_type": item.asset_type,
        "currency": item.currency,
        "identifiers": identifiers,
        "market_data": market_data,
        "quote_selection_policy": deepcopy(item.quote_selection_policy_json or {}),
        "source_settings": deepcopy(item.source_settings_json or {}),
        "refresh_status": deepcopy(item.refresh_status_json or {}),
        "lifecycle_state": deepcopy(item.lifecycle_state_json or {}),
    }


def _load_store_from_db(session) -> dict[str, object]:
    metadata_record = session.get(RegistryMetadata, "shared")
    instruments = session.scalars(
        select(Instrument)
        .options(
            selectinload(Instrument.identifiers),
            selectinload(Instrument.market_data_points),
        )
        .order_by(Instrument.asset_id)
    ).all()
    return {
        "registry_name": (
            metadata_record.registry_name
            if metadata_record is not None
            else DEFAULT_REGISTRY_NAME
        ),
        "instruments": [_instrument_to_store_dict(item) for item in instruments],
    }


def _save_store_to_db(session, data: dict[str, object]) -> None:
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
            asset_id=str(item.get("asset_id") or "").strip(),
            asset_name=str(item.get("asset_name") or "").strip(),
            asset_type=str(item.get("asset_type") or "").strip() or "other",
            currency=str(item.get("currency") or "USD").strip().upper() or "USD",
            quote_selection_policy_json=_normalized_quote_selection_policy(item),
            source_settings_json=_normalized_source_settings(item),
            refresh_status_json=_normalized_refresh_status(item),
            lifecycle_state_json=_normalized_lifecycle_state(item),
        )
        if not instrument.asset_id or not instrument.asset_name:
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
                    asset_id=instrument.asset_id,
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
                    asset_id=instrument.asset_id,
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
    return slug or "asset"


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
    return [
        latest_by_basis[key]
        for key in sorted(latest_by_basis.keys())
    ]


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
        "asset_id": item["asset_id"],
        "asset_name": item["asset_name"],
        "asset_type": item["asset_type"],
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


def _find_instrument(instruments: list[dict[str, object]], asset_id: str) -> dict[str, object] | None:
    return next((item for item in instruments if item.get("asset_id") == asset_id), None)


def _matches_instrument_search(item: dict[str, object], normalized_search: str) -> bool:
    if not normalized_search:
        return True

    haystack_parts = [
        str(item.get("asset_id") or ""),
        str(item.get("asset_name") or ""),
        str(item.get("asset_type") or ""),
        str(item.get("currency") or ""),
    ]
    for identifier in item.get("identifiers", []):
        if not isinstance(identifier, dict):
            continue
        haystack_parts.append(str(identifier.get("identifier_type") or ""))
        haystack_parts.append(str(identifier.get("identifier_value") or ""))

    haystack = " ".join(part.strip().lower() for part in haystack_parts if str(part).strip())
    return normalized_search in haystack


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


def list_instruments(
    *,
    search: str | None = None,
    asset_type: str | None = None,
    limit: int | None = None,
    include_inactive: bool = False,
) -> list[dict[str, object]]:
    session_factory = get_session_factory()
    normalized_search = search.strip().lower() if search else ""
    normalized_asset_type = asset_type.strip().lower() if asset_type else None

    with session_factory() as session:
        instruments = [
            _instrument_to_store_dict(item)
            for item in session.scalars(
                select(Instrument)
                .options(
                    selectinload(Instrument.identifiers),
                    selectinload(Instrument.market_data_points),
                )
                .order_by(Instrument.asset_name, Instrument.asset_id)
            ).all()
        ]

    filtered = []
    for item in instruments:
        if not include_inactive and not _is_active(item):
            continue
        current_asset_type = str(item.get("asset_type") or "").strip().lower()
        if normalized_asset_type and current_asset_type != normalized_asset_type:
            continue
        if normalized_search and not _matches_instrument_search(item, normalized_search):
            continue
        filtered.append(item)

    if limit is not None:
        filtered = filtered[:limit]
    return [_serialize_record(item) for item in filtered]


def get_instrument(asset_id: str) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        target = session.scalar(
            select(Instrument)
            .options(
                selectinload(Instrument.identifiers),
                selectinload(Instrument.market_data_points),
            )
            .where(Instrument.asset_id == asset_id)
        )
        if target is None:
            return None
        return _serialize_detail_record(_instrument_to_store_dict(target))


def find_instrument_by_identifier(
    *,
    identifier_value: str,
    identifier_type: str | None = None,
    include_inactive: bool = False,
) -> dict[str, object] | None:
    normalized_value = identifier_value.strip().lower()
    if not normalized_value:
        return None
    normalized_type = identifier_type.strip().lower() if identifier_type else None
    session_factory = get_session_factory()
    with session_factory() as session:
        candidates = session.scalars(
            select(Instrument)
            .join(InstrumentIdentifier)
            .options(
                selectinload(Instrument.identifiers),
                selectinload(Instrument.market_data_points),
            )
            .where(InstrumentIdentifier.identifier_value == identifier_value.strip())
            .order_by(Instrument.asset_name, Instrument.asset_id)
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
    *,
    asset_name: str,
    asset_type: str,
    currency: str,
    identifiers: list[dict[str, object]],
) -> dict[str, object]:
    seen_identifier_values: set[str] = set()
    for identifier in identifiers:
        identifier_value = str(identifier.get("identifier_value") or "").strip()
        if not identifier_value:
            continue
        normalized_value = identifier_value.lower()
        if normalized_value in seen_identifier_values:
            raise ValueError(f'Duplicate identifier "{identifier_value}" in request.')
        seen_identifier_values.add(normalized_value)

        existing = find_instrument_by_identifier(
            identifier_value=identifier_value,
            include_inactive=True,
        )
        if existing is not None:
            raise ValueError(
                f'Identifier "{identifier_value}" already belongs to "{existing["asset_name"]}" ({existing["asset_id"]}).'
            )

    session_factory = get_session_factory()
    with session_factory() as session:
        existing_ids = {
            item
            for item in session.scalars(select(Instrument.asset_id)).all()
        }
        primary_identifier = next(
            (
                str(item.get("identifier_value") or "")
                for item in identifiers
                if bool(item.get("is_primary"))
            ),
            "",
        )
        base_id = _slugify(primary_identifier or asset_name)
        candidate = base_id
        suffix = 2
        while candidate in existing_ids:
            candidate = f"{base_id}-{suffix}"
            suffix += 1

        record = {
            "asset_id": candidate,
            "asset_name": asset_name.strip(),
            "asset_type": asset_type,
            "currency": currency.strip().upper(),
            "identifiers": identifiers,
            "market_data": [],
            "quote_selection_policy": _default_quote_selection_policy(asset_type),
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
                asset_id=record["asset_id"],
                asset_name=record["asset_name"],
                asset_type=record["asset_type"],
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
                    asset_id=record["asset_id"],
                    identifier_type=identifier_type,
                    identifier_value=identifier_value,
                    is_primary=bool(raw_identifier.get("is_primary")),
                )
            )
        session.commit()
        return _serialize_record(record)


def upsert_market_data(
    *,
    asset_id: str,
    metric_family: str,
    quote_basis: str,
    as_of_date: date,
    value: str,
    currency: str,
    provider: str | None,
    status: str,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        target = session.scalar(
            select(Instrument)
            .options(
                selectinload(Instrument.identifiers),
                selectinload(Instrument.market_data_points),
            )
            .where(Instrument.asset_id == asset_id)
        )
        if target is None:
            return None

        existing = session.scalar(
            select(InstrumentMarketData).where(
                InstrumentMarketData.asset_id == asset_id,
                InstrumentMarketData.metric_family == metric_family,
                InstrumentMarketData.quote_basis == quote_basis,
                InstrumentMarketData.as_of_date == as_of_date,
                InstrumentMarketData.currency == currency.upper(),
            )
        )
        if existing is None:
            session.add(
                InstrumentMarketData(
                    asset_id=asset_id,
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
        schedule_watchlist_recalc(
            asset_id=asset_id,
            trigger_ref_type="instrument_market_data_upsert",
            trigger_ref_id=f"{metric_family}:{quote_basis}:{as_of_date.isoformat()}",
        )
        refreshed = get_instrument(asset_id)
        return _serialize_record(refreshed) if refreshed is not None else None


def upsert_source_settings(
    *,
    asset_id: str,
    source_mode: str,
    source_email: str | None,
    source_location: str | None,
    source_api_profile: str | None,
    source_email_rules: list[dict[str, object]] | None,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        target = session.get(Instrument, asset_id)
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
        refreshed = get_instrument(asset_id)
        return _serialize_record(refreshed) if refreshed is not None else None


def replace_nav_history(
    *,
    asset_id: str,
    rows: list[dict[str, object]],
    provider: str | None,
    point_status: str,
    refresh_status: str,
    updated_by: str | None,
    message: str,
    mode: str | None = None,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        target = session.get(Instrument, asset_id)
        if target is None:
            return None

        replaced_dates: set[date] = set()
        latest_point_date: date | None = None
        for row in rows:
            raw_date = str(row.get("as_of_date") or "").strip()
            if not raw_date:
                continue
            try:
                point_date = date.fromisoformat(raw_date)
            except ValueError:
                continue
            replaced_dates.add(point_date)
            latest_point_date = max(latest_point_date, point_date) if latest_point_date else point_date

        if replaced_dates:
            session.execute(
                delete(InstrumentMarketData).where(
                    InstrumentMarketData.asset_id == asset_id,
                    InstrumentMarketData.metric_family == "nav",
                    InstrumentMarketData.quote_basis.in_(["official_nav", "total_return_nav"]),
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
                        asset_id=asset_id,
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
        if latest_point_date is not None:
            schedule_watchlist_recalc(
                asset_id=asset_id,
                trigger_ref_type="instrument_nav_history_replace",
                trigger_ref_id=latest_point_date.isoformat(),
            )
        refreshed = get_instrument(asset_id)
        return _serialize_record(refreshed) if refreshed is not None else None


def update_refresh_status(
    *,
    asset_id: str,
    status: str,
    message: str,
    updated_by: str | None,
    mode: str | None = None,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        target = session.get(Instrument, asset_id)
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
        refreshed = get_instrument(asset_id)
        return _serialize_record(refreshed) if refreshed is not None else None


def set_instrument_lifecycle_state(
    *,
    asset_id: str,
    status: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    session_factory = get_session_factory()
    normalized_status = status.strip().lower()
    if normalized_status not in {"active", "archived"}:
        raise ValueError(f'Unsupported lifecycle status "{status}".')
    with session_factory() as session:
        target = session.get(Instrument, asset_id)
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
        refreshed = get_instrument(asset_id)
        return _serialize_record(refreshed) if refreshed is not None else None


def archive_instrument(
    *,
    asset_id: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    return set_instrument_lifecycle_state(
        asset_id=asset_id,
        status="archived",
        updated_by=updated_by,
    )


def restore_instrument(
    *,
    asset_id: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    return set_instrument_lifecycle_state(
        asset_id=asset_id,
        status="active",
        updated_by=updated_by,
    )


def instrument_registry_name() -> str:
    session_factory = get_session_factory()
    with session_factory() as session:
        metadata_record = session.get(RegistryMetadata, "shared")
        if metadata_record is None:
            return DEFAULT_REGISTRY_NAME
        return str(metadata_record.registry_name or DEFAULT_REGISTRY_NAME)
