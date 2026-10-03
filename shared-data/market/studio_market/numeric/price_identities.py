"""Reviewed security lifecycles and coherent provider price generations.

A ticker is a routing alias, not a permanent security identity. These ordinary
numeric facts retain the evidence and capture clock of a lifecycle decision;
they never rewrite the immutable prices that caused a conflict.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import re
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import select

from .schema import batches
from .store import cutoff_instant, instant


DATASET = "price_series_identities"
FIELDS = ("symbol", "security_id", "provider_symbol", "history_start", "history_end",
          "symbol_start", "symbol_end", "status", "reason", "source_refs")


class PriceIdentityUnavailable(ValueError):
    def __init__(self, symbol, reason, *, identity_source_id=None):
        self.details = {"reason": reason, "symbol": symbol,
                        "identity_source_id": identity_source_id}
        super().__init__(f"Price history identity is unavailable for {symbol}: {reason}")


def _symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^_/-]{0,99}", value):
        raise ValueError("Price identity symbols must be explicit uppercase provider symbols")
    return value


def _day(value):
    return date.fromisoformat(str(value)).isoformat() if value is not None else None


def _source_ref(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Price identity evidence requires nonempty source references")
    if (re.fullmatch(r"numeric:[0-9a-fA-F-]{36}:\d+", value)
            or re.fullmatch(r"numeric/raw/[a-z_]+/[0-9a-f]{2}/[0-9a-f]{64}\.gz", value)):
        return value
    parsed = urlsplit(value)
    forbidden = {"apikey", "api_key", "key", "token", "access_token", "signature"}
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
            or any(key.lower() in forbidden for key, _ in parse_qsl(parsed.query))):
        raise ValueError("Price identity evidence must use credential-free HTTPS or numeric source references")
    return value


def validate_identity(record):
    if not isinstance(record, dict) or set(record) - set(FIELDS):
        raise ValueError("Unknown price identity fields")
    result = {key: record.get(key) for key in FIELDS}
    result["symbol"] = _symbol(result["symbol"])
    if result["status"] not in {"verified", "blocked"}:
        raise ValueError("Price identity status must be verified or blocked")
    if result["provider_symbol"] is not None:
        result["provider_symbol"] = _symbol(result["provider_symbol"])
    for key in ("history_start", "history_end", "symbol_start", "symbol_end"):
        result[key] = _day(result[key])
    if result["status"] == "verified" and not all(result[key] for key in ("security_id", "provider_symbol", "history_start")):
        raise ValueError("Verified price identities require security_id, provider_symbol and history_start")
    if result["security_id"] is not None and (not isinstance(result["security_id"], str) or not result["security_id"].strip()):
        raise ValueError("security_id must identify an explicit security, not an empty value")
    if not isinstance(result["reason"], str) or not result["reason"].strip():
        raise ValueError("Price identity decisions require a reason")
    for begin, end in (("history_start", "history_end"), ("symbol_start", "symbol_end")):
        if result[begin] and result[end] and result[begin] > result[end]:
            raise ValueError(f"{begin} must not follow {end}")
    if result["history_start"] and result["symbol_start"] and result["history_start"] > result["symbol_start"]:
        raise ValueError("Security history cannot start after its reviewed symbol interval")
    if not isinstance(result["source_refs"], list) or not result["source_refs"]:
        raise ValueError("Price identity decisions require source_refs")
    result["source_refs"] = sorted(set(_source_ref(ref) for ref in result["source_refs"]))
    return result


def price_identities(store, *, symbols=None, as_of=None):
    """Return only identity facts visible at the caller's information clock."""
    result = store.latest(DATASET, symbols=symbols, as_of=as_of, limit=100000)
    if result["total"] > len(result["rows"]):
        raise ValueError("Price identity catalog exceeds the explicit read bound")
    return {row["symbol"]: row for row in result["rows"]}


def record_price_identities(store, records, *, apply=False, observed_at=None, source="reviewed_price_identity"):
    """Validate/preview a maintenance input; apply appends one ordinary batch."""
    if isinstance(records, dict):
        if set(records) != {"identities"}:
            raise ValueError("Price identity input requires only an identities array")
        records = records["identities"]
    if not isinstance(records, list) or not records:
        raise ValueError("Price identity input must be a nonempty array")
    records = [validate_identity(row) for row in records]
    if len({row["symbol"] for row in records}) != len(records):
        raise ValueError("A price identity input must not repeat a symbol")
    existing = price_identities(store, symbols=[row["symbol"] for row in records])
    changed = [row for row in records if row != {key: existing.get(row["symbol"], {}).get(key) for key in FIELDS}]
    result = {"applied": False, "unchanged": len(records) - len(changed), "identities": changed}
    if apply and changed:
        capture = observed_at or datetime.now(timezone.utc)
        result.update(store.ingest(DATASET, [changed], source=source, observed_at=capture,
                                   details={"observed_at": capture, "identity_decision": True,
                                            "source_parts": [{"identity_evidence_raw_ref": ref}
                                                for ref in sorted({ref for row in changed for ref in row['source_refs']
                                                                   if ref.startswith('numeric/raw/')})]}))
        result["applied"] = True
    return result


def price_capture_bases(store, dataset, identities, *, as_of=None):
    """Latest complete capture for each exact, currently reviewed identity fact."""
    if not identities:
        return {}
    cutoff = cutoff_instant(as_of) if as_of is not None else None
    query = select(batches).where(batches.c.dataset == dataset, batches.c.status == "ready",
                                  batches.c.row_count > 0,
                                  batches.c.details["price_series_capture"].as_string().is_not(None))
    found = {}
    with store.engine.connect() as connection:
        for batch in connection.execute(query).mappings():
            observed = instant(batch["details"].get("observed_at") or batch["started_at"])
            if cutoff and observed > cutoff:
                continue
            for symbol, proof in batch["details"].get("price_series_capture", {}).items():
                identity = identities.get(symbol)
                if (not identity or identity["status"] != "verified"
                        or proof.get("identity_source_id") != identity["source_id"]
                        or proof.get("security_id") != identity["security_id"]
                        or proof.get("provider_symbol") != identity["provider_symbol"]
                        or proof.get("history_start") != identity["history_start"]):
                    continue
                end = proof.get("history_end")
                if not end or end < identity["history_start"]:
                    continue
                rank = (observed, batch["id"])
                if symbol not in found or rank > found[symbol][0]:
                    found[symbol] = (rank, dict(batch))
    return {symbol: value[1] for symbol, value in found.items()}


def action_in_identity(identity, *, source_symbol, effective_date):
    """Actions on expired aliases do not belong to a reused ticker's security."""
    if identity["status"] != "verified":
        return False
    day = effective_date.isoformat() if isinstance(effective_date, date) else str(effective_date)
    start = identity.get("history_start")
    end = identity.get("history_end")
    if (start and day < start) or (end and day > end):
        return False
    if source_symbol == identity["symbol"]:
        if identity.get("symbol_start") and day < identity["symbol_start"]:
            return False
        if identity.get("symbol_end") and day > identity["symbol_end"]:
            return False
    return source_symbol in {identity["symbol"], identity["provider_symbol"]}


def guard_reference_changes(store, dataset, rows, *, observed_at):
    """Quarantine newly observed identity changes before publishing references.

    Repeated observations of an unchanged event do not undo a reviewed repair.
    An issuer's ordinary name change is not used as proof of a new security.
    """
    if not rows or dataset not in {"company_profiles", "symbol_changes", "delisted_securities", "security_directory"}:
        return None
    symbols = sorted({row["symbol"] for row in rows if row.get("symbol")})
    if dataset == "symbol_changes":
        symbols = sorted({row[key] for row in rows for key in ("old_symbol", "new_symbol")})
        prior = store.query(dataset, limit=100000)
        if prior["total"] > len(prior["rows"]):
            raise ValueError("Symbol change catalog exceeds the explicit read bound")
        seen = {(row["old_symbol"], row["new_symbol"], str(row["event_date"])) for row in prior["rows"]}
        events = [(row, "provider_symbol_changed", (row["old_symbol"], row["new_symbol"])) for row in rows
                  if (row["old_symbol"], row["new_symbol"], str(row["event_date"])) not in seen]
    else:
        prior = {row["symbol"]: row for row in store.latest(dataset, symbols=symbols, limit=100000)["rows"]}
        other = "company_profiles" if dataset == "delisted_securities" else "delisted_securities"
        related = {row["symbol"]: row for row in store.latest(other, symbols=symbols, limit=100000)["rows"]}
        events = []
        for row in rows:
            symbol = row["symbol"]
            old, counterpart = prior.get(symbol, {}), related.get(symbol, {})
            if dataset == "company_profiles" and any(old.get(key) and row.get(key) and old[key] != row[key]
                                                       for key in ("isin", "cusip", "cik")):
                events.append((row, "security_identifier_changed", (symbol,)))
            elif dataset == "delisted_securities":
                if counterpart.get("is_actively_trading") and str(old.get("delisted_date")) != str(row.get("delisted_date")):
                    events.append((row, "active_and_delisted_identity_conflict", (symbol,)))
            elif (row.get("is_actively_trading") and counterpart.get("delisted_date")
                  and old.get("is_actively_trading") is not True):
                events.append((row, "active_and_delisted_identity_conflict", (symbol,)))
    if not events:
        return None
    existing = price_identities(store, symbols=symbols)
    blocked = {}
    for row, reason, affected in events:
        for symbol in affected:
            previous = existing.get(symbol, {})
            if previous.get('status') == 'verified' and row['raw_ref'] in previous.get('source_refs', []):
                continue
            candidate = {key: previous.get(key) for key in FIELDS}
            candidate.update(symbol=symbol, status="blocked", reason=reason,
                             source_refs=sorted(set(previous.get("source_refs", []) + [row["raw_ref"]])))
            blocked[symbol] = candidate
    if not blocked:
        return None
    return record_price_identities(store, list(blocked.values()), apply=True, observed_at=observed_at,
                                   source="provider_identity_conflict")
