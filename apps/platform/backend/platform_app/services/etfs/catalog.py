from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import case, delete, func, or_, select

from platform_app.db.etf_models import FmpEtfCatalog
from platform_app.db.session import get_session_factory
from platform_app.services.fmp.client import FmpClient
from platform_app.services.fmp.exchanges import (
    FMP_ETF_CATALOG_EXCHANGES,
    canonical_exchange_ticker,
    resolve_exchange,
)


class EtfCatalogEmptyError(RuntimeError):
    pass


def _text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _catalog_row(
    raw: dict[str, object],
    *,
    fmp_exchange_code: str,
) -> dict[str, object] | None:
    query_exchange = FMP_ETF_CATALOG_EXCHANGES[fmp_exchange_code]
    resolved = resolve_exchange(
        raw.get("exchange"),
        raw.get("exchangeShortName"),
        fmp_exchange_code,
    )
    if resolved is None:
        return None
    if fmp_exchange_code == "AMEX":
        if resolved.exchange_code not in {"XASE", "ARCX"}:
            return None
    elif resolved != query_exchange:
        return None
    exchange = resolved
    if raw.get("isEtf") is not True or raw.get("isFund") is True:
        return None
    if raw.get("isActivelyTrading") is False:
        return None
    fmp_symbol = str(raw.get("symbol") or "").strip().upper()
    company_name = str(raw.get("companyName") or raw.get("name") or "").strip()
    if not fmp_symbol or not company_name:
        return None
    return {
        "fmp_symbol": fmp_symbol,
        "exchange_ticker": canonical_exchange_ticker(
            fmp_symbol=fmp_symbol,
            exchange_code=exchange.exchange_code,
        ),
        "company_name": company_name,
        "exchange_code": exchange.exchange_code,
        "market": exchange.market,
        "currency": exchange.currency,
        "country": _text(raw.get("country")),
        "sector": _text(raw.get("sector")),
        "industry": _text(raw.get("industry")),
    }


def sync_etf_catalog(*, client: FmpClient | None = None) -> dict[str, object]:
    fmp = client or FmpClient()
    fetched: dict[str, dict[str, object]] = {}
    exchange_counts: dict[str, int] = {}
    exchange_ticker_owners: dict[str, str] = {}
    for fmp_exchange_code in FMP_ETF_CATALOG_EXCHANGES:
        normalized_rows = [
            normalized
            for raw in fmp.active_etfs(fmp_exchange_code)
            if (normalized := _catalog_row(raw, fmp_exchange_code=fmp_exchange_code))
            is not None
        ]
        if not normalized_rows:
            raise RuntimeError(
                f"FMP returned no supported active ETFs for {fmp_exchange_code}."
            )
        exchange_counts[fmp_exchange_code] = len(normalized_rows)
        for row in normalized_rows:
            fmp_symbol = str(row["fmp_symbol"])
            exchange_ticker = str(row["exchange_ticker"])
            other_symbol = exchange_ticker_owners.get(exchange_ticker)
            if other_symbol is not None and other_symbol != fmp_symbol:
                raise RuntimeError(
                    f"FMP ETF catalog maps {exchange_ticker} to both "
                    f"{other_symbol} and {fmp_symbol}."
                )
            exchange_ticker_owners[exchange_ticker] = fmp_symbol
            fetched[fmp_symbol] = row

    if not fetched:
        raise RuntimeError("FMP active ETF catalog returned no supported ETFs.")

    now = datetime.now(UTC).replace(microsecond=0)
    with get_session_factory()() as session:
        replaced_count = int(
            session.scalar(select(func.count()).select_from(FmpEtfCatalog)) or 0
        )
        session.execute(delete(FmpEtfCatalog))
        session.add_all(
            FmpEtfCatalog(**row, synced_at=now)
            for row in fetched.values()
        )
        session.commit()

    return {
        "active_count": len(fetched),
        "replaced_count": replaced_count,
        "exchange_counts": exchange_counts,
        "synced_at": now.isoformat(),
    }


def get_catalog_etf(fmp_symbol: str) -> dict[str, object] | None:
    normalized_symbol = fmp_symbol.strip().upper()
    if not normalized_symbol:
        return None
    with get_session_factory()() as session:
        item = session.get(FmpEtfCatalog, normalized_symbol)
        if item is None:
            return None
        return _serialize(item)


def search_etf_catalog(query: str, *, limit: int) -> list[dict[str, object]]:
    normalized_query = query.strip()
    if not normalized_query:
        return []
    lowered = normalized_query.lower()
    escaped = (
        normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    pattern = f"%{escaped}%"
    with get_session_factory()() as session:
        catalog_count = session.scalar(
            select(func.count()).select_from(FmpEtfCatalog)
        )
        if not catalog_count:
            raise EtfCatalogEmptyError(
                "The local FMP ETF catalog is empty; run the catalog sync first."
            )
        statement = (
            select(FmpEtfCatalog)
            .where(
                or_(
                    FmpEtfCatalog.fmp_symbol.ilike(pattern, escape="\\"),
                    FmpEtfCatalog.exchange_ticker.ilike(pattern, escape="\\"),
                    FmpEtfCatalog.company_name.ilike(pattern, escape="\\"),
                ),
            )
            .order_by(
                case(
                    (func.lower(FmpEtfCatalog.fmp_symbol) == lowered, 0),
                    (func.lower(FmpEtfCatalog.exchange_ticker) == lowered, 1),
                    (func.lower(FmpEtfCatalog.fmp_symbol).like(f"{lowered}%"), 2),
                    (func.lower(FmpEtfCatalog.exchange_ticker).like(f"{lowered}%"), 3),
                    else_=4,
                ),
                FmpEtfCatalog.company_name,
                FmpEtfCatalog.fmp_symbol,
            )
            .limit(max(1, min(limit, 25)))
        )
        return [_serialize(item) for item in session.scalars(statement).all()]


def _serialize(item: FmpEtfCatalog) -> dict[str, object]:
    return {
        "fmp_symbol": item.fmp_symbol,
        "exchange_ticker": item.exchange_ticker,
        "company_name": item.company_name,
        "exchange_code": item.exchange_code,
        "market": item.market,
        "currency": item.currency,
        "country": item.country,
        "sector": item.sector,
        "industry": item.industry,
    }
