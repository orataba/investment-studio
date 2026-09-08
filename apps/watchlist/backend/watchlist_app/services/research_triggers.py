"""Supplement a daily review only for new information in its actual research scope."""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from investment_studio_instrument_core.db_models import Instrument
from investment_studio_instrument_core.listing_contract import MARKET_SCOPE_TIMEZONES, market_scope_for_calendar

from watchlist_app.db.models.workbench import ResearchEntry, RiskCase
from watchlist_app.services.market_evidence import text_store


def _instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("Research trigger requires a knowledge timestamp")
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)


def _previous_trigger(context, instrument_id):
    trigger = context.get("incremental_trigger") or {}
    return trigger if trigger.get("instrument_id") == instrument_id else trigger.get(instrument_id, {})


def _queries(context, instrument_id):
    previous = _previous_trigger(context, instrument_id).get("coverage_cursor", {})
    queries = [*previous.get("market_queries", []), *context.get("market_queries", [])]
    output = {}
    for query in queries:
        assigned = query.get("instrument_id")
        if assigned != instrument_id and not (assigned is None and context.get("instrument_ids") == [instrument_id]):
            continue
        terms, entities = (query.get("query") or "").strip(), sorted(set(query.get("entities") or []))
        if not terms and not entities:
            continue  # A corpus-wide browse is not an asset-specific monitoring mandate.
        key = (terms, tuple(entities))
        cutoff = query.get("cutoff") or context.get("input_snapshot_cutoff") or context["cutoff"]
        if key not in output or _instant(cutoff) > _instant(output[key]["cutoff"]):
            output[key] = {"instrument_id": instrument_id, "query": terms, "entities": entities,
                           "cutoff": _instant(cutoff).isoformat()}
    return list(output.values())


def _monitoring_context(session, instrument_id, context):
    if _queries(context, instrument_id):
        return context
    # A new daily run or a failed/manual task need not search again. Preserve the
    # most recent actual research scope, rather than silently ending monitoring.
    for entry in session.scalars(select(ResearchEntry).where(ResearchEntry.kind == "analysis")
                                 .order_by(ResearchEntry.created_at.desc())):
        prior = getattr(entry, "context_json", None) or {}
        if (prior.get("sector_run") or prior.get("research_run")) and instrument_id in prior.get("instrument_ids", []):
            if _queries(prior, instrument_id):
                return prior
    return context


def _readable(document):
    return (document.get("content_completeness") in {"full_text", "source_excerpt"}
            and bool((document.get("content_text") or "").strip())
            and document.get("information_type") not in {"ai_summary", "generated_summary"}
            and not (document.get("provenance") or {}).get("ai_generated"))


def _new_text(store, queries, consumed_at, now, read_source_ids):
    changed = {}
    for query in queries:
        # A later web fetch advances the run cutoff, not this query's actual coverage.
        since = _instant(query["cutoff"])
        if consumed_at is not None:
            since = max(since, _instant(consumed_at))
        if since >= now:
            continue
        # The two clocks form a union: a late import can have an old observed_at.
        for clock in ("received_after", "observed_after"):
            offset = 0
            while True:
                page = store.search(query["query"], entities=query["entities"] or None,
                                    **{clock: since}, as_of=now, limit=100, offset=offset, include_withdrawn=True)
                for row in page["rows"]:
                    if row["source_id"] in changed or row["source_id"] in read_source_ids:
                        continue
                    if max(_instant(row["received_at"]), _instant(row["observed_at"])) <= since:
                        continue
                    previous = store.read(row["document_id"], as_of=since)
                    withdrawn = row.get("status") in {"withdrawn", "superseded"}
                    if (withdrawn and not previous) or (not withdrawn and not _readable(row)):
                        continue
                    if previous and (previous["version_id"] == row["version_id"]
                                     or (previous.get("body_sha256") == row.get("body_sha256")
                                         and previous.get("status") == row.get("status"))):
                        continue
                    changed[row["source_id"]] = {
                        key: row.get(key) for key in (
                            "source_id", "document_id", "version_id", "title", "url", "body_sha256",
                            "published_at", "observed_at", "received_at", "content_completeness", "status",
                        )
                    } | {"change": "withdrawn_original" if withdrawn else "revised_original" if previous else "new_original",
                         "matched_query": query}
                offset += len(page["rows"])
                if offset >= page["total"] or not page["rows"]:
                    break
    return list(changed.values())


def _risk_changes(session, instrument_id, since, now):
    cases = session.scalars(select(RiskCase).where(
        RiskCase.instrument_id == instrument_id,
        RiskCase.signal.in_(["drawdown_limit", "period_loss"]),
        RiskCase.trigger_active.is_(True),
    ))
    changes = []
    for case in cases:
        # Daily numerical refreshes deliberately do not create these history events.
        events = [event for event in (case.history_json or [])
                  if event.get("action") in {"triggered", "updated"}
                  and since < _instant(event["at"]) <= now]
        if events:
            changes.append({"case_id": case.case_id, "signal": case.signal, "title": case.title,
                            "observed_on": case.observed_on.isoformat() if case.observed_on else None,
                            "events": events, "evidence": case.evidence_json})
    return changes


def _due_items(session, instrument_id, context, since, now):
    instrument = session.get(Instrument, instrument_id)
    calendar = ((instrument.source_settings_json or {}).get("market_calendar") or instrument.exchange_code) if instrument else None
    market = market_scope_for_calendar(calendar) if calendar else None
    zone = ZoneInfo(MARKET_SCOPE_TIMEZONES[market]) if market else None
    review = (context.get("reviews") or {}).get(instrument_id) or {}
    dossier = next((item for item in context.get("research_dossiers", []) if item.get("instrument_id") == instrument_id), {})
    notebook = review.get("research") or dossier.get("notebook") or {}
    due = []
    for kind, items, field, status in (
        ("forecast_review", notebook.get("forecasts", []), "review_on", "active"),
        ("scheduled_event_check", notebook.get("catalysts", []), "scheduled_at", "scheduled"),
    ):
        for item in items:
            value = item.get(field)
            if not value or item.get("status", status) != status:
                continue
            if len(str(value)) == 10:
                # Do not invent a source timezone or an intraday release time from a date.
                crossed = zone is not None and since.astimezone(zone).date() < date.fromisoformat(str(value)) <= now.astimezone(zone).date()
            else:
                crossed = since < _instant(value) <= now
            if crossed:
                due.append({"kind": kind, "key": item["key"], "due_at": str(value),
                            "title": item.get("claim") or item.get("title"),
                            "note": "已到约定检查时间；不代表事件已经发生或预测已经得到验证。"})
    return due


def research_trigger(session, instrument_id, prior_context, *, now):
    """Return a reason and consumed information cursor, without writing or launching work.

    Save the result as context.incremental_trigger (or map it by instrument_id for a batch).
    An attempted supplementary run consumes this cursor even if the model later fails;
    only subsequently arriving information can trigger another same-day attempt.
    """
    now = _instant(now)
    previous = _previous_trigger(prior_context, instrument_id).get("coverage_cursor", {})
    since = max(_instant(prior_context["cutoff"]), _instant(previous.get("checked_at") or prior_context["cutoff"]))
    if since >= now:
        return None
    monitoring = _monitoring_context(session, instrument_id, prior_context)
    queries = _queries(monitoring, instrument_id)
    consumed = [cursor for cursor in (previous.get("checked_at"),
        _previous_trigger(monitoring, instrument_id).get("coverage_cursor", {}).get("checked_at")) if cursor]
    read_source_ids = {row["source_id"] for context in (monitoring, prior_context)
                       for row in context.get("market_text_sources", [])}
    read_source_ids.update(row["source_id"] for context in (monitoring, prior_context)
                           for capture in context.get("web_evidence", [])
                           for row in capture.get("sources", []) if row.get("source_id"))
    documents = _new_text(text_store(), queries, max(consumed, key=_instant) if consumed else None, now, read_source_ids) if queries else []
    risk_changes = _risk_changes(session, instrument_id, since, now)
    due = _due_items(session, instrument_id, prior_context, since, now)
    reasons = []
    if documents:
        reasons.append({"kind": "new_research_sources", "reason": "已研究范围出现新的、实质修订或撤回的来源资料。", "sources": documents})
    if risk_changes:
        reasons.append({"kind": "quantitative_risk_change", "reason": "已有复核规则出现新触发或实质变化。", "cases": risk_changes})
    if due:
        reasons.append({"kind": "research_check_due", "reason": "已绑定的研究观察到达检查时间。", "items": due})
    if not reasons:
        return None
    return {"instrument_id": instrument_id, "reasons": reasons, "coverage_cursor": {
        "checked_at": now.isoformat(), "previous_cutoff": since.isoformat(), "market_queries": queries,
    }}
