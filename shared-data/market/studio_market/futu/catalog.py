"""Preload recognized Futu listings for the existing local security picker."""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.schema import batches
from studio_market.numeric.store import instant

from .collect import FutuHistoryError, _archive, _symbol_lock
from .listings import load_listings
from .oauth import FutuOAuth
from .quotes import FutuQuoteClient, FutuQuoteError


def current_catalog_snapshot(settings: MarketSettings, *, markets=("hk", "us"),
                             store=None, now: datetime | None = None) -> dict | None:
    """Return today's complete published catalog, or require a fresh sync.

    Release installation may reuse a completed capture. Maintenance still calls
    sync_catalog directly; failed or incomplete captures are never fallbacks.
    """
    required_markets = set(markets)
    if not required_markets or required_markets - {"hk", "us"}:
        raise ValueError("Futu catalog markets must be hk and/or us.")
    now = instant(now or datetime.now(UTC))
    today = now.astimezone(ZoneInfo("Asia/Hong_Kong")).date()
    market = store or NumericStore(settings)
    try:
        with market.engine.connect() as connection:
            batch = connection.execute(select(batches).where(
                batches.c.dataset == "futu_security_catalog",
            ).order_by(batches.c.started_at.desc(), batches.c.id.desc()).limit(1)).mappings().first()
        if (batch is None or batch["status"] != "ready"
                or batch["source"] != "futu:listing-directory" or not batch["published_at"]):
            return None
        details = batch["details"]
        if not isinstance(details, dict) or set(details.get("markets") or []) != required_markets:
            return None
        counts = [details.get(key) for key in ("active_count", "withdrawn_count", "candidate_count")]
        skipped = details.get("skipped")
        if (any(type(value) is not int or value < 0 for value in counts) or counts[0] == 0
                or not isinstance(skipped, dict)
                or any(type(skipped.get(key)) is not int or skipped[key] < 0
                       for key in ("unrecognized", "type_or_identity"))):
            return None
        active_count, withdrawn_count, candidate_count = counts
        if (active_count + withdrawn_count != batch["row_count"]
                or candidate_count != active_count + skipped["unrecognized"] + skipped["type_or_identity"]):
            return None
        parts = details.get("source_parts")
        if not isinstance(parts, list) or not parts:
            return None
        try:
            clocks = [instant(batch["published_at"]), *(instant(part["observed_at"]) for part in parts)]
        except (KeyError, TypeError, ValueError):
            return None
        if any(clock > now or clock.astimezone(ZoneInfo("Asia/Hong_Kong")).date() != today for clock in clocks):
            return None
        captured = market.query("futu_security_catalog", batch_id=batch["id"], versions=True, limit=100000)
        rows = captured["rows"]
        if captured["total"] != batch["row_count"] or len(rows) != captured["total"]:
            return None
        active = {row["symbol"]: row for row in rows if row.get("active") is True}
        if (len(active) != active_count or sum(row.get("active") is False for row in rows) != withdrawn_count
                or len({row["symbol"] for row in rows}) != len(rows)
                or any(row.get("currency_verified") is not True or not row.get("currency")
                       or type(row.get("stock_id")) is not int or row["stock_id"] <= 0
                       or row.get("stock_type") != {"equity": "STOCK", "etf": "ETF"}.get(row.get("instrument_type"), "unsupported")
                       for row in active.values())):
            return None
        current = market.latest("futu_security_catalog", limit=100000)
        current_active = {row["symbol"]: row for row in current["rows"]
                          if row.get("active") is True and row["symbol"][:2].lower() in required_markets}
        if (current["total"] != len(current["rows"]) or current_active.keys() != active.keys()
                or any(row.get("batch_id") != batch["id"] for row in current_active.values())):
            return None
        return {"batch_id": batch["id"], "dataset": "futu_security_catalog", "status": "ready",
                "reused": True, "row_count": batch["row_count"], "active_count": active_count,
                "withdrawn_count": withdrawn_count, "candidate_count": candidate_count, "skipped": skipped}
    finally:
        if store is None:
            market.close()


def sync_catalog(settings: MarketSettings, *, markets=("hk", "us"), store=None, client=None,
                 listing_loader=load_listings) -> dict:
    """Publish only after every directory and Futu batch succeeds.

    Recognition confirms a provider symbol, not account entitlement or complete
    historical bars. Registration performs the actual history acquisition.
    """
    markets = tuple(dict.fromkeys(markets))
    if not markets or set(markets) - {"hk", "us"}:
        raise ValueError("Futu catalog markets must be hk and/or us.")
    market = store or NumericStore(settings)
    oauth = None
    quotes = client
    try:
        if quotes is None:
            oauth = FutuOAuth(settings.futu_config_dir)
            quotes = FutuQuoteClient(oauth.access_token)
        with _symbol_lock(settings, "catalog"):
            details = {"markets": list(markets)}

            def chunks():
                listings, captures = listing_loader(markets=markets)
                reference_parts = _archive(settings, captures)
                candidates = {row["symbol"]: row for row in listings}
                if len(candidates) != len(listings) or not candidates:
                    raise FutuHistoryError("Exchange listing directory is empty or has duplicate identities.")
                confirmed = quotes.basic_info_many(sorted(candidates))
                quote_parts = _archive(settings, confirmed.captures)
                parts = reference_parts + quote_parts
                observed = max(part["observed_at"] for part in parts)
                rows = []
                skipped = {"unrecognized": len(candidates) - len(confirmed.rows), "type_or_identity": 0}
                for basic in confirmed.rows:
                    listing = candidates[basic["symbol"]]
                    if (basic["stock_type"] != listing["futu_stock_type"] or not basic.get("listing_date")
                            or basic.get("state") not in {"NORMAL", None}):
                        skipped["type_or_identity"] += 1
                        continue
                    # Some API versions may include currency. A contradiction with
                    # the listing's currency is never resolved by silently scaling.
                    provider_currency = basic.get("currency")
                    if provider_currency and str(provider_currency).upper() != listing["currency"]:
                        raise FutuHistoryError("Futu and the exchange directory disagree on quote currency.")
                    evidence = [part for part in quote_parts if basic["symbol"] in part["parameters"]["code_list"]]
                    if len(evidence) != 1:
                        raise FutuHistoryError("Futu directory identity does not bind to one provider response.")
                    rows.append({
                        **basic, **listing, "symbol": basic["symbol"],
                        "stock_id": basic["stock_id"], "stock_type": basic["stock_type"],
                        "listing_date": basic["listing_date"],
                        "name": basic.get("name") or listing["name"],
                        "currency_verified": True, "active": True,
                        "coverage_status": "symbol_recognized_history_not_yet_verified",
                        "observed_at": observed, "available_at": observed,
                        "raw_ref": evidence[0]["raw_ref"], "collected_at": evidence[0]["observed_at"],
                    })
                if not rows:
                    raise FutuHistoryError("Futu recognized no eligible listings; the prior catalog was retained.")
                active_symbols = {row["symbol"] for row in rows}
                prior = market.latest("futu_security_catalog", limit=100000)
                if prior["total"] != len(prior["rows"]):
                    raise FutuHistoryError("The retained Futu catalog exceeded the complete-read limit.")
                withdrawn = []
                for old in prior["rows"]:
                    if old["symbol"][:2].lower() not in markets or old["symbol"] in active_symbols or not old.get("active"):
                        continue
                    # Explicit inactive observations also survive bundle replication;
                    # an older catalog cannot resurrect a withdrawn listing.
                    row = {key: value for key, value in old.items() if key not in {
                        "source_id", "batch_id", "row_index", "observed_at", "available_at", "snapshot_at",
                        "_key", "_current_key", "_snapshot_key", "date", "collected_at",
                    }}
                    row.update(active=False, observed_at=observed, available_at=observed,
                               coverage_status="not_in_current_verified_directory", raw_ref=reference_parts[0]["raw_ref"])
                    withdrawn.append(row)
                details.update(source_parts=parts, active_count=len(rows), withdrawn_count=len(withdrawn),
                               candidate_count=len(candidates), skipped=skipped)
                yield rows + withdrawn

            result = market.ingest("futu_security_catalog", chunks(), source="futu:listing-directory", details=details)
            return dict(result, **{key: details[key] for key in
                                   ("active_count", "withdrawn_count", "candidate_count", "skipped")})
    except FutuQuoteError as error:
        _archive(settings, error.captures)
        raise
    finally:
        if client is None and quotes is not None:
            quotes.close()
        if oauth is not None:
            oauth.close()
        if store is None:
            market.close()
