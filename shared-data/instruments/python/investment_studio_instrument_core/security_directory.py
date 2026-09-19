"""Read the data owner's public listing catalog through the canonical database.

Only catalog and registry identity columns are read. Provider calls and registry
writes remain owned by the explicit data-owner registration command.
"""
from sqlalchemy import and_, case, column, func, or_, select, table

from investment_studio_instrument_core.db_models import Instrument, InstrumentIdentifier
from investment_studio_instrument_core.fmp_listing import exchange_by_code


class SecurityDirectoryError(ValueError):
    pass


def _catalog_rows(session, instrument_type: str, query: str, limit: int):
    # SQLite fixtures use the same table contract without PostgreSQL schemas.
    catalog = table(
        f"fmp_{instrument_type}_catalog",
        *(column(name) for name in (
            "fmp_symbol", "exchange_ticker", "company_name", "exchange_code",
            "currency", "country", "sector", "industry",
        )),
        schema="data_ingestion" if session.bind.dialect.name == "postgresql" else None,
    )
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


def search_directory(session_factory, query: str, limit: int = 10, *, instrument_types=("equity", "etf")):
    query = query.strip()
    if not query:
        raise ValueError("Security search query must not be blank.")
    limit = max(1, min(limit, 25))
    errors, catalog_rows = {}, []
    with session_factory() as session:
        for instrument_type in instrument_types:
            if instrument_type not in {"equity", "etf"}:
                raise ValueError("Unsupported directory instrument type")
            rows, error = _catalog_rows(session, instrument_type, query, limit)
            if error:
                errors[instrument_type] = error
            catalog_rows.extend((instrument_type, row) for row in rows)
        identifiers = {}
        if catalog_rows:
            provider_tokens = {f"fmp:{row['fmp_symbol']}".lower() for _, row in catalog_rows}
            ticker_tokens = {str(row["exchange_ticker"]).lower() for _, row in catalog_rows}
            matches = session.execute(select(
                InstrumentIdentifier.identifier_type,
                InstrumentIdentifier.identifier_value,
                Instrument.instrument_id, Instrument.instrument_type,
                Instrument.lifecycle_state_json,
            ).join(Instrument, Instrument.instrument_id == InstrumentIdentifier.instrument_id).where(or_(
                and_(func.lower(InstrumentIdentifier.identifier_type) == "provider_symbol",
                     func.lower(InstrumentIdentifier.identifier_value).in_(provider_tokens)),
                and_(func.lower(InstrumentIdentifier.identifier_type) == "exchange_ticker",
                     func.lower(InstrumentIdentifier.identifier_value).in_(ticker_tokens)),
            )).order_by(Instrument.instrument_name, Instrument.instrument_id))
            for kind, value, instrument_id, instrument_type, lifecycle in matches:
                identifiers.setdefault((kind.lower(), value.lower()), (instrument_id, instrument_type, lifecycle))

        results = []
        for instrument_type, row in catalog_rows:
            exchange = exchange_by_code(row["exchange_code"])
            if exchange is None:
                raise SecurityDirectoryError(f"Local FMP catalog has unsupported exchange {row['exchange_code']}.")
            existing_id, archived = None, False
            for key in (("provider_symbol", f"fmp:{row['fmp_symbol']}".lower()),
                        ("exchange_ticker", row["exchange_ticker"].lower())):
                existing = identifiers.get(key)
                if existing is None:
                    continue
                existing_id, registered_type, lifecycle = existing
                if registered_type != instrument_type:
                    raise SecurityDirectoryError(f"Identifier {key[0]}:{key[1]} belongs to a non-{instrument_type} instrument.")
                archived = str((lifecycle or {}).get("status") or "active") != "active"
                break
            if archived:
                continue
            results.append({
                "instrument_type": instrument_type,
                "symbol": row["exchange_ticker"], "catalog_provider": "fmp",
                "catalog_symbol": row["fmp_symbol"], "name": row["company_name"],
                "exchange_code": exchange.exchange_code, "exchange_label": exchange.label,
                "market": exchange.market, "currency": row["currency"],
                "currency_verified": not exchange.requires_profile_currency,
                "country": row["country"], "sector": row["sector"], "industry": row["industry"],
                "existing_instrument_id": existing_id,
            })
    return sorted(results, key=lambda item: _search_rank(item, query))[:limit], errors
