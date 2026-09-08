"""Price rebuild obligations derived from immutable public capture metadata."""
from datetime import date, datetime, time, timedelta, timezone

import duckdb
from sqlalchemy import select

from .schema import batches
from .store import cutoff_instant, instant


def pending_revisions(store, *, dataset: str, symbols, end: date) -> dict[str, str]:
    """Acknowledge only the exact request completed by a full-history capture.

    Future announced actions become due on their effective day. A concurrent
    newer action cannot be cleared by an older price run, and raw/adjusted price
    histories retain independent completion receipts.
    """
    wanted = set(symbols)
    if not wanted:
        return {}
    families = {"dividends", "stock_splits", dataset}
    query = select(batches.c.id, batches.c.dataset, batches.c.details, batches.c.started_at).where(
        batches.c.status == "ready", batches.c.dataset.in_(families),
        (batches.c.details["price_revision_requests"].as_string().is_not(None)) |
        (batches.c.details["price_revision_completed"].as_string().is_not(None)),
    )
    requests, completed = {}, set()
    with store.engine.connect() as connection:
        for capture in connection.execute(query).mappings():
            details = capture["details"]
            observed = instant(details.get("observed_at") or capture["started_at"])
            for request in details.get("price_revision_requests", []):
                symbol, effective = request["symbol"], date.fromisoformat(request["effective_date"])
                if symbol not in wanted or effective > end:
                    continue
                identity = request.get("request_id") or f"{capture['id']}:{effective.isoformat()}"
                # A future announcement comes due after an earlier completed run
                # even when that run observed a later correction to an old event.
                request_observed = instant(request.get("observed_at") or observed)
                due = max(request_observed, datetime.combine(effective, time.max, timezone.utc))
                rank = (due, request_observed, identity)
                if symbol not in requests or rank > requests[symbol][0]:
                    requests[symbol] = (rank, identity)
            if capture["dataset"] == dataset:
                completed.update(details.get("price_revision_completed", {}).items())
    return {symbol: identity for symbol, (_rank, identity) in requests.items()
            if (symbol, identity) not in completed}


def _versions(store, dataset, **filters):
    offset = 0
    while True:
        result = store.query(dataset, versions=True, limit=100000, offset=offset, **filters)
        yield from result["rows"]
        offset += len(result["rows"])
        if offset >= result["total"]:
            break


def history_start(store, dataset, symbol):
    """A rebuild must also refresh retained prices before the usual 2000 start."""
    earlier = store.query(dataset, symbols=[symbol], end="2000-01-02", limit=1)
    if not earlier["total"]:
        return date(2000, 1, 3)
    oldest = store.query(dataset, symbols=[symbol], end="2000-01-02", limit=1,
                         offset=earlier["total"] - 1)["rows"][0]
    return date.fromisoformat(oldest["date"])


def _completed_history(captures, request, end, start):
    """Require a contiguous successful full-history request, not a latest quote."""
    cursor, evidence, count, completed = None, [], 0, []
    for capture in sorted(captures, key=lambda r: (instant(r["details"].get("observed_at") or r["started_at"]), instant(r["started_at"]), r["id"])):
        details = capture["details"]
        params = details.get("parameters", {})
        if params.get("symbol") != request["symbol"] or not params.get("from") or not params.get("to"):
            continue
        if instant(details.get("observed_at") or capture["started_at"]) < instant(request["observed_at"]):
            continue
        begin, stop = date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
        if begin <= start:
            cursor, evidence, count = begin, [], 0
        if begin != cursor:
            continue
        evidence.append(capture["id"])
        count += capture["row_count"]
        cursor = stop + timedelta(days=1)
        if stop >= end and count:
            completed = list(evidence)
    return completed


def _covers_retained_dates(store, dataset, symbol, end, evidence):
    paths = store._paths(dataset, end=end.isoformat())
    if not paths:
        return False
    with duckdb.connect(":memory:") as connection:
        connection.from_parquet(paths, union_by_name=True).create_view("facts")
        missing = connection.execute("""SELECT date FROM facts WHERE symbol=? AND date<=?::DATE
            EXCEPT SELECT date FROM facts WHERE symbol=? AND date<=?::DATE
            AND batch_id IN (SELECT unnest(?)) LIMIT 1""",
            [symbol, end.isoformat(), symbol, end.isoformat(), evidence]).fetchone()
    return missing is None


def recover_legacy_revisions(store, *, since, end: date, symbols, apply=False):
    """Recover interrupted old collectors from their immutable action versions.

    The explicit incident start bounds recovery. Empty receipt batches retain
    original source IDs and clocks; no historical row or published batch changes.
    Existing contiguous full captures can acknowledge a request with their exact
    batch evidence. Preview is read-only and never calls a provider.
    """
    since = cutoff_instant(since)
    wanted = sorted(set(symbols))
    if not wanted:
        raise ValueError("Price recovery requires an explicit symbol universe")
    with store.engine.connect() as connection:
        catalog = [dict(row) for row in connection.execute(select(batches).where(
            batches.c.status == "ready", batches.c.dataset.in_(
                ["dividends", "stock_splits", "us_eod_daily", "raw_eod_daily"]))).mappings()]
    known = {request.get("request_id") for capture in catalog
             for request in capture["details"].get("price_revision_requests", [])}
    completed = {(capture["dataset"], symbol, identity) for capture in catalog
                 for symbol, identity in capture["details"].get("price_revision_completed", {}).items()}
    recovered = []
    for dataset, fields in [("dividends", ("dividend", "adjusted_dividend")),
                            ("stock_splits", ("numerator", "denominator")),
                            ("raw_eod_daily", ("adjusted_close",))]:
        legacy = {capture["id"] for capture in catalog if capture["dataset"] == dataset
                  and "price_revision_requests" not in capture["details"]
                  and instant(capture["details"].get("observed_at") or capture["started_at"]) >= since
                  and (dataset != "raw_eod_daily" or
                       capture["details"].get("parameters", {}).get("from", "") > "2000-01-03")}
        if not legacy:
            continue
        # All earlier versions establish whether an incident capture changed a
        # fact; repeated identical responses must not create new obligations.
        filters = {"symbols": wanted}
        if dataset == "raw_eod_daily":
            # Only the old short captures can have lost a mismatch-triggered
            # rebuild. Avoid loading unrelated decades of price history.
            intervals = [c["details"]["parameters"] for c in catalog if c["id"] in legacy]
            filters.update(start=min(p["from"] for p in intervals), end=max(p["to"] for p in intervals))
        rows = sorted(_versions(store, dataset, **filters),
                      key=lambda row: (instant(row["observed_at"]), row["batch_id"], row["row_index"]))
        previous = {}
        for row in rows:
            key = (row["symbol"], row["date"])
            value = tuple(row.get(field) for field in fields)
            changed = (key in previous and previous[key] != value) if dataset == "raw_eod_daily" else (key not in previous or previous[key] != value)
            previous[key] = value
            if changed and row["batch_id"] in legacy and instant(row["observed_at"]) >= since:
                recovered.append({"dataset": dataset, "symbol": row["symbol"], "effective_date": row["date"],
                                  "request_id": row["source_id"], "source_id": row["source_id"],
                                  "observed_at": row["observed_at"]})
    # One current full rebuild covers all due changes to a symbol. Retain future
    # requests individually so later effective actions still become due.
    latest = {}
    for request in recovered:
        effective = date.fromisoformat(request["effective_date"])
        scope = (request["dataset"], request["symbol"], "due" if effective <= end else effective.isoformat())
        observed = instant(request["observed_at"])
        rank = (max(observed, datetime.combine(effective, time.max, timezone.utc)), observed, request["request_id"])
        if scope not in latest or rank > latest[scope][0]:
            latest[scope] = (rank, request)
    requests = [value[1] for value in latest.values()]
    results, writes, starts = [], [], {}
    for request in sorted(requests, key=lambda r: (r["symbol"], r["effective_date"], r["request_id"])):
        item = {**request, "already_recorded": request["request_id"] in known, "completion_evidence": {}}
        if date.fromisoformat(request["effective_date"]) <= end:
            for dataset in (("raw_eod_daily",) if request["dataset"] == "raw_eod_daily" else ("us_eod_daily", "raw_eod_daily")):
                key = (dataset, request["symbol"], request["request_id"])
                scope = (dataset, request["symbol"])
                if scope not in starts:
                    starts[scope] = history_start(store, dataset, request["symbol"])
                evidence = _completed_history([c for c in catalog if c["dataset"] == dataset], request, end, starts[scope])
                if key in completed:
                    item["completion_evidence"][dataset] = "recorded"
                elif evidence and _covers_retained_dates(store, dataset, request["symbol"], end, evidence):
                    item["completion_evidence"][dataset] = evidence
        results.append(item)
        if apply:
            if request["request_id"] not in known:
                writes.append(store.ingest(request["dataset"], [], source="price_revision_recovery", details={
                    "price_revision_requests": [{k: v for k, v in request.items() if k != "dataset"}],
                    "recovery_since": since.isoformat()}))
            for dataset, evidence in item["completion_evidence"].items():
                if evidence != "recorded":
                    writes.append(store.ingest(dataset, [], source="price_revision_recovery", details={
                        "price_revision_completed": {request["symbol"]: request["request_id"]},
                        "price_revision_source_batches": evidence}))
    return {"status": "ready", "mode": "apply" if apply else "preview", "since": since.isoformat(),
            "end": end.isoformat(), "requests": results, "receipt_batches": writes}
