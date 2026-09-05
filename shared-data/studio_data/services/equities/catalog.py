from __future__ import annotations

from datetime import UTC, datetime
import re

from sqlalchemy import case, delete, func, or_, select

from studio_data.db.equity_models import FmpEquityCatalog
from studio_data.db.session import get_session_factory
from studio_data.services.fmp.client import FmpClient
from studio_data.services.fmp.exchanges import (
    FMP_EQUITY_CATALOG_EXCHANGES,
    canonical_exchange_ticker,
    resolve_exchange,
)


class EquityCatalogEmptyError(RuntimeError):
    pass


_CHINA_A_SHARE_SYMBOL_PATTERNS = {
    "SHH": re.compile(r"^(?!900)\d{6}\.SS$"),
    "SHZ": re.compile(r"^(?!200)\d{6}\.SZ$"),
}


def _text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _catalog_row(
    raw: dict[str, object],
    *,
    fmp_exchange_code: str,
) -> dict[str, object] | None:
    exchange = FMP_EQUITY_CATALOG_EXCHANGES[fmp_exchange_code]
    resolved = resolve_exchange(
        raw.get("exchangeShortName"),
        raw.get("exchange"),
        fmp_exchange_code,
    )
    if resolved != exchange:
        return None
    if raw.get("isEtf") is True or raw.get("isFund") is True:
        return None
    if raw.get("isActivelyTrading") is False:
        return None
    fmp_symbol = str(raw.get("symbol") or "").strip().upper()
    company_name = str(raw.get("companyName") or raw.get("name") or "").strip()
    if not fmp_symbol or not company_name:
        return None
    a_share_pattern = _CHINA_A_SHARE_SYMBOL_PATTERNS.get(fmp_exchange_code)
    if a_share_pattern is not None and a_share_pattern.fullmatch(fmp_symbol) is None:
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


def sync_equity_catalog(*, client: FmpClient | None = None) -> dict[str, object]:
    fmp = client or FmpClient()
    fetched: dict[str, dict[str, object]] = {}
    exchange_counts: dict[str, int] = {}
    exchange_ticker_owners: dict[str, str] = {}
    for fmp_exchange_code in FMP_EQUITY_CATALOG_EXCHANGES:
        normalized_rows = [
            normalized
            for raw in fmp.active_equities(fmp_exchange_code)
            if (normalized := _catalog_row(raw, fmp_exchange_code=fmp_exchange_code))
            is not None
        ]
        if not normalized_rows:
            raise RuntimeError(
                f"FMP returned no supported active stocks for {fmp_exchange_code}."
            )
        exchange_counts[fmp_exchange_code] = len(normalized_rows)
        for row in normalized_rows:
            fmp_symbol = str(row["fmp_symbol"])
            exchange_ticker = str(row["exchange_ticker"])
            other_symbol = exchange_ticker_owners.get(exchange_ticker)
            if other_symbol is not None and other_symbol != fmp_symbol:
                raise RuntimeError(
                    f"FMP catalog maps {exchange_ticker} to both {other_symbol} and {fmp_symbol}."
                )
            exchange_ticker_owners[exchange_ticker] = fmp_symbol
            fetched[fmp_symbol] = row

    if not fetched:
        raise RuntimeError("FMP active equity catalog returned no supported stocks.")

    now = datetime.now(UTC).replace(microsecond=0)
    with get_session_factory()() as session:
        replaced_count = int(
            session.scalar(select(func.count()).select_from(FmpEquityCatalog)) or 0
        )
        session.execute(delete(FmpEquityCatalog))
        session.add_all(
            FmpEquityCatalog(**row, synced_at=now)
            for row in fetched.values()
        )
        session.commit()

    return {
        "active_count": len(fetched),
        "replaced_count": replaced_count,
        "exchange_counts": exchange_counts,
        "synced_at": now.isoformat(),
    }


def get_catalog_equity(fmp_symbol: str) -> dict[str, object] | None:
    normalized_symbol = fmp_symbol.strip().upper()
    if not normalized_symbol:
        return None
    with get_session_factory()() as session:
        item = session.get(FmpEquityCatalog, normalized_symbol)
        if item is None:
            return None
        return _serialize(item)


def search_equity_catalog(query: str, *, limit: int) -> list[dict[str, object]]:
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
            select(func.count()).select_from(FmpEquityCatalog)
        )
        if not catalog_count:
            raise EquityCatalogEmptyError(
                "The local FMP equity catalog is empty; run the catalog sync first."
            )
        statement = (
            select(FmpEquityCatalog)
            .where(
                or_(
                    FmpEquityCatalog.fmp_symbol.ilike(pattern, escape="\\"),
                    FmpEquityCatalog.exchange_ticker.ilike(pattern, escape="\\"),
                    FmpEquityCatalog.company_name.ilike(pattern, escape="\\"),
                ),
            )
            .order_by(
                case(
                    (func.lower(FmpEquityCatalog.fmp_symbol) == lowered, 0),
                    (func.lower(FmpEquityCatalog.exchange_ticker) == lowered, 1),
                    (func.lower(FmpEquityCatalog.fmp_symbol).like(f"{lowered}%"), 2),
                    (func.lower(FmpEquityCatalog.exchange_ticker).like(f"{lowered}%"), 3),
                    else_=4,
                ),
                FmpEquityCatalog.company_name,
                FmpEquityCatalog.fmp_symbol,
            )
            .limit(max(1, min(limit, 25)))
        )
        return [_serialize(item) for item in session.scalars(statement).all()]


def _serialize(item: FmpEquityCatalog) -> dict[str, object]:
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
