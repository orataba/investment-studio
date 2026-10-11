"""Read the data owner's public listing catalog through the canonical database.

Only catalog and registry identity columns are read. Provider calls and registry
writes remain owned by the explicit data-owner registration command.
"""
import re

from sqlalchemy import JSON, and_, case, column, func, or_, select, table

from investment_studio_instrument_core.db_models import Instrument, InstrumentIdentifier
from investment_studio_instrument_core.fmp_listing import exchange_by_code


class SecurityDirectoryError(ValueError):
    pass


def _query_code(query: str) -> str:
    normalized = query.upper()
    if re.fullmatch(r"HK\.[0-9]{1,5}", normalized):
        return f"{int(normalized[3:]):04d}.HK"
    if re.fullmatch(r"[0-9]{1,5}\.HK", normalized):
        return f"{int(normalized[:-3]):04d}.HK"
    if normalized.startswith("US."):
        return normalized[3:]
    return query


def _futu_catalog(session):
    # This is the existing public current projection, not an app credential or
    # network path. Raw bars, token files and historical Parquet are never read.
    return table("current", column("dataset"), column("payload", JSON),
                 schema="market_data" if session.bind.dialect.name == "postgresql" else None)


def _futu_entry(row):
    return row["instrument_type"], dict(
        row, catalog_provider="futu", catalog_symbol=row["symbol"], company_name=row["name"],
        country=row.get("country"), sector=row.get("sector"), industry=row.get("industry"),
    )


def _futu_rows(session, query: str, limit: int, instrument_types):
    catalog = _futu_catalog(session)
    payload = catalog.c.payload
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    fields = [payload[name].as_string() for name in ("symbol", "exchange_ticker", "name", "sc_name", "tc_name")]
    statement = select(payload).where(
        catalog.c.dataset == "futu_security_catalog",
        payload["active"].as_boolean().is_(True),
        payload["instrument_type"].as_string().in_(instrument_types),
        or_(*(field.ilike(f"%{escaped}%", escape="\\") for field in fields)),
    ).order_by(case((func.lower(payload["exchange_ticker"].as_string()) == query.lower(), 0), else_=1),
               payload["name"].as_string(), payload["symbol"].as_string()).limit(limit)
    return [dict(row) for row in session.scalars(statement)]


def _fmp_catalog(session, instrument_type: str):
    # SQLite fixtures use the same table contract without PostgreSQL schemas.
    return table(
        f"fmp_{instrument_type}_catalog",
        *(column(name) for name in (
            "fmp_symbol", "exchange_ticker", "company_name", "exchange_code",
            "currency", "country", "sector", "industry",
        )),
        schema="data_ingestion" if session.bind.dialect.name == "postgresql" else None,
    )


def _catalog_rows(session, instrument_type: str, query: str, limit: int):
    catalog = _fmp_catalog(session, instrument_type)
    if session.scalar(select(catalog.c.fmp_symbol).limit(1)) is None:
        return [], f"The local FMP {instrument_type} catalog is empty; run the catalog sync first."
    lowered = query.lower()
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    rows = session.execute(select(catalog).where(or_(
        catalog.c.fmp_symbol.ilike(pattern, escape="\\"),
        catalog.c.exchange_ticker.ilike(pattern, escape="\\"),
        catalog.c.company_name.ilike(pattern, escape="\\"),
    )).order_by(case(
        (func.lower(catalog.c.fmp_symbol) == lowered, 0),
        (func.lower(catalog.c.exchange_ticker) == lowered, 1),
        (func.lower(catalog.c.fmp_symbol).like(f"{lowered}%"), 2),
        (func.lower(catalog.c.exchange_ticker).like(f"{lowered}%"), 3),
        else_=4,
    ), catalog.c.company_name, catalog.c.fmp_symbol).limit(limit)).mappings().all()
    return rows, None


def _search_rank(item: dict, query: str) -> tuple:
    normalized = query.casefold()
    symbol, catalog_symbol, name = (
        str(item[key] or "").casefold() for key in ("symbol", "catalog_symbol", "name")
    )
    return (
        0 if normalized in {symbol, catalog_symbol} else 1,
        0 if symbol.startswith(normalized) or catalog_symbol.startswith(normalized) else 1,
        0 if normalized in name else 1,
        name, item["instrument_type"], symbol,
    )


def _complete_registered_sources(session, results: list[dict], query: str) -> None:
    """Complete discovery hits from the registered source, in bounded batches.

    Names and search limits belong to discovery. A registered provider binding
    must survive even when only another catalog's alias entered that result set.
    """
    present = {(item["existing_instrument_id"], item["catalog_provider"]) for item in results}
    missing = {
        item["existing_instrument_id"]: item for item in results
        if item["existing_instrument_id"] and item["_selected_provider"] in {"fmp", "futu"}
        and (item["existing_instrument_id"], item["_selected_provider"]) not in present
    }
    if not missing:
        return
    bindings = {instrument_id: set() for instrument_id in missing}
    for instrument_id, token in session.execute(select(
        InstrumentIdentifier.instrument_id, InstrumentIdentifier.identifier_value,
    ).where(
        InstrumentIdentifier.instrument_id.in_(missing),
        func.lower(InstrumentIdentifier.identifier_type) == "provider_symbol",
    )):
        prefix, separator, symbol = token.partition(":")
        if separator and prefix.lower() == missing[instrument_id]["_selected_provider"]:
            bindings[instrument_id].add(symbol.upper())
    requested = {}
    for instrument_id, symbols in bindings.items():
        if len(symbols) != 1:
            raise SecurityDirectoryError("Registered source requires one unambiguous provider symbol.")
        item = missing[instrument_id]
        key = (item["_selected_provider"], item["instrument_type"], next(iter(symbols)))
        requested[key] = instrument_id

    entries = []
    for kind in {key[1] for key in requested if key[0] == "fmp"}:
        symbols = [key[2].lower() for key in requested if key[:2] == ("fmp", kind)]
        catalog = _fmp_catalog(session, kind)
        entries.extend((kind, dict(row, catalog_provider="fmp", catalog_symbol=row["fmp_symbol"]))
                       for row in session.execute(select(catalog).where(
                           func.lower(catalog.c.fmp_symbol).in_(symbols))).mappings())
    symbols = [key[2].lower() for key in requested if key[0] == "futu"]
    if symbols:
        catalog = _futu_catalog(session)
        entries.extend(_futu_entry(row) for row in session.scalars(select(catalog.c.payload).where(
            catalog.c.dataset == "futu_security_catalog",
            catalog.c.payload["active"].as_boolean().is_(True),
            func.lower(catalog.c.payload["symbol"].as_string()).in_(symbols),
        )))
    completed = set()
    for kind, row in entries:
        key = (row["catalog_provider"], kind, row["catalog_symbol"].upper())
        instrument_id = requested.get(key)
        if instrument_id is None or instrument_id in completed:
            raise SecurityDirectoryError("Registered source catalog identity is inconsistent or duplicated.")
        item = missing[instrument_id]
        if (row["exchange_code"], row["exchange_ticker"].upper()) != (item["exchange_code"], item["symbol"].upper()):
            raise SecurityDirectoryError("Registered source catalog conflicts with the listing identity.")
        completed.add(instrument_id)
        results.append({**item,
            "catalog_provider": row["catalog_provider"], "catalog_symbol": row["catalog_symbol"],
            "name": row["company_name"], "country": row.get("country"),
            "sector": row.get("sector"), "industry": row.get("industry"),
            "_match_rank": _search_rank(item, query),
        })
    if completed != set(missing):
        raise SecurityDirectoryError("Registered source listing is missing from its active local catalog.")


def search_directory(session_factory, query: str, limit: int = 10, *, instrument_types=("equity", "etf")):
    query = query.strip()
    if not query:
        raise ValueError("Security search query must not be blank.")
    query = _query_code(query)
    limit = max(1, min(limit, 25))
    errors, catalog_rows = {}, []
    with session_factory() as session:
        for instrument_type in instrument_types:
            if instrument_type not in {"equity", "etf"}:
                raise ValueError("Unsupported directory instrument type")
            rows, error = _catalog_rows(session, instrument_type, query, limit)
            if error:
                errors[instrument_type] = error
            catalog_rows.extend((instrument_type, dict(row, catalog_provider="fmp", catalog_symbol=row["fmp_symbol"])) for row in rows)
        for row in _futu_rows(session, query, limit, instrument_types):
            catalog_rows.append(_futu_entry(row))
        identifiers = {}
        if catalog_rows:
            provider_tokens = {f"{row['catalog_provider']}:{row['catalog_symbol']}".lower() for _, row in catalog_rows}
            ticker_tokens = {str(row["exchange_ticker"]).lower() for _, row in catalog_rows}
            matches = session.execute(select(
                InstrumentIdentifier.identifier_type,
                InstrumentIdentifier.identifier_value,
                Instrument.instrument_id, Instrument.instrument_type,
                Instrument.lifecycle_state_json, Instrument.source_settings_json,
                Instrument.currency, Instrument.exchange_code,
            ).join(Instrument, Instrument.instrument_id == InstrumentIdentifier.instrument_id).where(or_(
                and_(func.lower(InstrumentIdentifier.identifier_type) == "provider_symbol",
                     func.lower(InstrumentIdentifier.identifier_value).in_(provider_tokens)),
                and_(func.lower(InstrumentIdentifier.identifier_type) == "exchange_ticker",
                     func.lower(InstrumentIdentifier.identifier_value).in_(ticker_tokens)),
            )).order_by(Instrument.instrument_name, Instrument.instrument_id))
            for kind, value, instrument_id, instrument_type, lifecycle, source_settings, currency, exchange_code in matches:
                key = (kind.lower(), value.lower())
                identity = (instrument_id, instrument_type, lifecycle, source_settings, currency, exchange_code)
                if key in identifiers and identifiers[key][0] != instrument_id:
                    raise SecurityDirectoryError(f"Identifier {kind}:{value} belongs to multiple registered instruments.")
                identifiers[key] = identity

        results = []
        for instrument_type, row in catalog_rows:
            exchange = exchange_by_code(row["exchange_code"])
            if exchange is None:
                raise SecurityDirectoryError(f"Local {row['catalog_provider']} catalog has unsupported exchange {row['exchange_code']}.")
            existing_id, archived, selected_provider = None, False, None
            currency = row["currency"]
            currency_verified = row.get("currency_verified", not exchange.requires_profile_currency)
            for key in (("provider_symbol", f"{row['catalog_provider']}:{row['catalog_symbol']}".lower()),
                        ("exchange_ticker", row["exchange_ticker"].lower())):
                existing = identifiers.get(key)
                if existing is None:
                    continue
                matched_id, registered_type, lifecycle, source_settings, registered_currency, registered_exchange = existing
                if existing_id is not None and existing_id != matched_id:
                    raise SecurityDirectoryError("Provider symbol and exchange ticker identify different registered instruments.")
                existing_id = matched_id
                selected_provider = (source_settings or {}).get("source_api_profile")
                if registered_type != instrument_type:
                    raise SecurityDirectoryError(f"Identifier {key[0]}:{key[1]} belongs to a non-{instrument_type} instrument.")
                if registered_exchange != exchange.exchange_code:
                    raise SecurityDirectoryError(f"Identifier {key[0]}:{key[1]} conflicts with the registered exchange.")
                # Registry currency was verified when the listing was created.
                # A provider catalog's generic exchange currency cannot replace
                # that canonical counter identity in a selectable search result.
                currency, currency_verified = registered_currency, True
                archived = str((lifecycle or {}).get("status") or "active") != "active"
            if archived:
                continue
            results.append({
                "instrument_type": instrument_type,
                "symbol": row["exchange_ticker"], "catalog_provider": row["catalog_provider"],
                "catalog_symbol": row["catalog_symbol"], "name": row["company_name"],
                "exchange_code": exchange.exchange_code, "exchange_label": exchange.label,
                "market": exchange.market, "currency": currency,
                "currency_verified": currency_verified,
                "country": row["country"], "sector": row["sector"], "industry": row["industry"],
                "existing_instrument_id": existing_id,
                "_selected_provider": selected_provider,
            })
        _complete_registered_sources(session, results, query)
    # One listing produces one result. Once registered, retain its chosen
    # provider even if another catalog later adds the same listing.
    groups = {}
    for item in results:
        key = (item["exchange_code"], item["symbol"].upper())
        groups.setdefault(key, []).append(item)
    unique = []
    for candidates in groups.values():
        # HKEX distinguishes HKD, USD and RMB counters. A generic FMP exchange
        # currency must not override the exact counter's verified currency.
        verified_futu = next((item for item in candidates if item["catalog_provider"] == "futu" and item["currency_verified"]), None)
        currency_conflict = verified_futu is not None and any(item["currency"] != verified_futu["currency"] for item in candidates)

        def priority(item):
            if item["catalog_provider"] == item["_selected_provider"]:
                return 0
            if currency_conflict:
                return 1 if item["catalog_provider"] == "futu" else 2
            return 1 if item["catalog_provider"] == "fmp" else 2

        unique.append(min(candidates, key=priority))
    ranked = sorted(unique, key=lambda item: item.get("_match_rank", _search_rank(item, query)))[:limit]
    for item in ranked:
        item.pop("_selected_provider")
        item.pop("_match_rank", None)
    return ranked, errors
