"""Supplement a daily review only for new information in its actual research scope."""
from contextlib import closing
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
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


def _attempted_at(cursor):
    # Old cursors called this checked_at, although they were saved before the
    # model ran. Preserve their deduplication semantics, never claim success.
    return cursor.get("attempted_at") or cursor.get("checked_at")


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
    # A private query is itself private information even when it searched public
    # documents. Only shared, non-portfolio research establishes automatic scope.
    from watchlist_app.services.research_access import instrument_run_scope, topic_portfolio_ids
    query = select(ResearchEntry).join(ResearchTopic, ResearchTopic.topic_id == ResearchEntry.topic_id).where(
        ResearchEntry.kind == "analysis", ResearchTopic.visibility == "team", ResearchTopic.portfolio_id.is_(None),
        instrument_run_scope(session, instrument_id),
    ).order_by(ResearchEntry.created_at.desc(), ResearchEntry.entry_id.desc())
    team_id = (context.get("research_actor") or {}).get("team_id")
    if team_id:
        query = query.where(ResearchEntry.team_id == team_id, ResearchTopic.team_id == team_id)
    # A follow-up without its own query scope must not hydrate the whole team's
    # archived inputs. Read only this instrument, stopping at its first usable
    # retained scope; historical portfolio restrictions still apply to the topic.
    with closing(session.scalars(query.execution_options(yield_per=1))) as entries:
        for entry in entries:
            prior = getattr(entry, "context_json", None) or {}
            if (prior.get("sector_run") or prior.get("research_run")) and instrument_id in prior.get("instrument_ids", []):
                topic = session.get(ResearchTopic, entry.topic_id)
                if (prior.get("portfolio_id") or (prior.get("risk_scope") or {}).get("portfolio_id")
                        or topic_portfolio_ids(session, topic)):
                    continue
                if _queries(prior, instrument_id):
                    return prior
    return context


def _readable(document):
    return (document.get("content_completeness") in {"full_text", "source_excerpt"}
            and bool((document.get("content_text") or "").strip())
            and document.get("information_type") not in {"ai_summary", "generated_summary"}
            and not (document.get("provenance") or {}).get("ai_generated"))


def _same_original(left, right):
    # A correction to headline, occurrence/publication clock or fact/rumor
    # attribution can change the evidence even while its body stays identical.
    return all(left.get(key) == right.get(key) for key in (
        "body_sha256", "status", "title", "published_at", "occurred_at", "information_type"))


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
                    if previous and (previous["version_id"] == row["version_id"] or _same_original(previous, row)):
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


def _read_originals(context, instrument_id):
    """A cited document remains a dependency after its title/entities are corrected."""
    from watchlist_app.services.research_notebook import notebook_source_ids
    review = (context.get("reviews") or {}).get(instrument_id) or {}
    cited = notebook_source_ids(review.get("research") or {})
    cited.update((review.get("reflection") or {}).get("source_ids", []))
    for row in [*review.get("events", []), *review.get("themes", [])]:
        cited.update(row.get("source_ids", []))
    sources = []
    for dossier in context.get("research_dossiers", []):
        if dossier.get("instrument_id") != instrument_id:
            continue
        originals = [*dossier.get("prior_sources", []), *(dossier.get("notebook") or {}).get("sources", [])]
        sources.extend(originals)
        cited.update(row.get("source_id") for row in originals)
    sources.extend((review.get("research") or {}).get("sources", []))
    for row in [*context.get("market_text_sources", []),
                *(row for capture in context.get("web_evidence", []) if capture.get("operation") == "fetch"
                  for row in capture.get("sources", []))]:
        if (context.get("instrument_ids") == [instrument_id] or row.get("instrument_id") == instrument_id
                or row.get("source_id") in cited):
            sources.append(row)
    return {row["source_id"]: row for row in sources
            if str(row.get("source_id", "")).startswith("text:")}


def _revised_originals(store, sources, consumed_at, now):
    changed = {}
    latest_by_document = {}
    for source_id in sources:
        original = store.read(source_id, as_of=now)
        if not original:
            continue
        document_id = original["document_id"]
        if document_id not in latest_by_document:
            latest_by_document[document_id] = store.read(document_id, as_of=now)
        latest = latest_by_document[document_id]
        if (not latest or latest["source_id"] in sources
                or (consumed_at is not None
                    and max(_instant(latest["received_at"]), _instant(latest["observed_at"])) <= consumed_at)
                or _same_original(original, latest)):
            continue
        withdrawn = latest.get("status") in {"withdrawn", "superseded"}
        if not withdrawn and not _readable(latest):
            continue
        changed[latest["source_id"]] = {key: latest.get(key) for key in (
            "source_id", "document_id", "version_id", "title", "url", "body_sha256",
            "published_at", "observed_at", "received_at", "content_completeness", "status",
        )} | {"change": "withdrawn_original" if withdrawn else "revised_original",
              "previous_source_id": source_id, "dependency": "previously_read_original"}
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


def numeric_monitor_inputs(session, instrument_id):
    """Compare retained business inputs, never collection IDs or refresh clocks."""
    from watchlist_app.db.models import InstrumentChartReadModel, InstrumentExposureHoldingsReadModel
    chart_payload = session.scalar(select(InstrumentChartReadModel.payload_json).where(
        InstrumentChartReadModel.instrument_id == instrument_id)) or {}
    chart = chart_payload.get("research_returns") or {}
    holdings = session.scalar(select(InstrumentExposureHoldingsReadModel.payload_json).where(
        InstrumentExposureHoldingsReadModel.instrument_id == instrument_id)) or {}
    metadata = chart.get("metadata") or {}
    return {"series": {"points": [{key: point.get(key) for key in ("date", "value", "status")}
                for point in (chart.get("points") or [])[-51:]],
            "currency": chart.get("currency"), "frequency": chart_payload.get("frequency"),
            "status": chart_payload.get("return_series_status"),
            "metadata": {key: metadata.get(key) for key in ("currency", "return_kind", "frequency", "calculation_version", "quote_basis")}},
        "holdings": {"rows": sorted([{key: row.get(key) for key in (
                "holding_name", "holding_type", "portfolio_weight", "currency")}
                for row in holdings.get("rows") or []], key=lambda row: (str(row["holding_name"]), str(row["holding_type"]))),
            "as_of_date": (holdings.get("snapshot_metadata") or {}).get("as_of_date")}}


def _due_items(session, instrument_id, context, since, now):
    from watchlist_app.services.sector_research import _research_market, RESEARCH_TIMEZONES
    market = _research_market(session, instrument_id)
    zone = ZoneInfo(RESEARCH_TIMEZONES[market]) if market else None
    review = (context.get("reviews") or {}).get(instrument_id) or {}
    dossier = next((item for item in context.get("research_dossiers", []) if item.get("instrument_id") == instrument_id), {})
    notebook = review.get("research") or dossier.get("notebook") or {}
    inactive_themes = {theme["theme_id"] for theme in dossier.get("themes", []) if theme.get("status") != "active"}
    due = []
    from watchlist_app.services.sector_research import event_record
    attempted = _previous_trigger(context, instrument_id).get("coverage_cursor", {}).get("due_event_versions", {})
    attempted_at = _attempted_at(_previous_trigger(context, instrument_id).get("coverage_cursor", {}))
    attempted_today = bool(attempted_at and _instant(attempted_at).astimezone(zone or UTC).date() == now.astimezone(zone or UTC).date())
    today = now.astimezone(zone or UTC).date().isoformat()
    for case in session.scalars(select(RiskCase).where(RiskCase.instrument_id == instrument_id, RiskCase.signal.like("sector:%"))):
        if not case.signal.startswith("sector:"):
            continue
        event = event_record(case)
        if (event["follow_up"] != "watch" or not event.get("follow_up_until")
                or event["follow_up_until"] > today or (attempted_today and attempted.get(case.case_id) == event["event_version_id"])):
            continue
        due.append({"kind": "event_follow_up_due", "key": event["event_key"], "case_id": case.case_id,
            "event_version_id": event["event_version_id"], "due_at": event["follow_up_until"], "title": event["title"],
            "pinned": event["follow_up_pinned"], "next_observation_on": event["next_observation_on"],
            "risk_still_active": case.trigger_active,
            "note": "到期待复核；完成收尾或明确延期，资料不可用时保留跟进，不能自动解除风险。"})
    for kind, items, field, status in (
        ("forecast_review", notebook.get("forecasts", []), "review_on", "active"),
        ("scheduled_event_check", notebook.get("catalysts", []), "scheduled_at", "scheduled"),
    ):
        for item in items:
            value = item.get(field)
            if not value or item.get("status", status) != status or item.get("theme_id") in inactive_themes:
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
    This is an attempt cursor, not a successful coverage receipt. A failed run is
    recovered separately with a bounded retry; it must not create another fresh
    job for the same information on every scheduler pass.
    """
    now = _instant(now)
    previous = _previous_trigger(prior_context, instrument_id).get("coverage_cursor", {})
    since = max(_instant(prior_context["cutoff"]), _instant(_attempted_at(previous) or prior_context["cutoff"]))
    if since >= now:
        return None
    monitoring = _monitoring_context(session, instrument_id, prior_context)
    queries = _queries(monitoring, instrument_id)
    consumed = [cursor for cursor in (_attempted_at(previous),
        _attempted_at(_previous_trigger(monitoring, instrument_id).get("coverage_cursor", {}))) if cursor]
    read_source_ids = {row["source_id"] for context in (monitoring, prior_context)
                       for row in context.get("market_text_sources", [])}
    read_source_ids.update(row["source_id"] for context in (monitoring, prior_context)
                           for capture in context.get("web_evidence", [])
                           for row in capture.get("sources", []) if row.get("source_id"))
    documents = _new_text(text_store(), queries, max(consumed, key=_instant) if consumed else None, now, read_source_ids) if queries else []
    originals = {**_read_originals(monitoring, instrument_id), **_read_originals(prior_context, instrument_id)}
    if originals:
        # The run cutoff can advance through an unrelated fetch after this source
        # was read. Only a prior dependency check or a read of the latest version
        # consumes this correction; a later run timestamp does not prove review.
        dependency_cursor = max((_instant(cursor) for cursor in consumed), default=None)
        revisions = _revised_originals(text_store(), originals, dependency_cursor, now)
        documents = list({row["source_id"]: row for row in [*documents, *revisions]}.values())
    risk_changes = _risk_changes(session, instrument_id, since, now)
    prior_numerical = prior_context.get("numeric_monitor_inputs", {}).get(instrument_id)
    numerical_changed = prior_numerical is not None and prior_numerical != numeric_monitor_inputs(session, instrument_id)
    due = _due_items(session, instrument_id, prior_context, since, now)
    attempted = prior_context.get("attempted_theme_baselines", {})
    dossier = next((row for row in prior_context.get("research_dossiers", []) if row["instrument_id"] == instrument_id), {})
    pending_themes = [{"theme_id": row["theme_id"], "title": row["title"], "reference": row.get("reference")}
        for row in dossier.get("themes", []) if row["status"] == "active" and row.get("baseline_status") == "pending"
        and attempted.get(row["theme_id"]) != (row.get("baseline_requested_at") or row.get("created_at"))]
    reasons = []
    if pending_themes:
        reasons.append({"kind": "theme_baseline", "reason": "新建重点主题需要主动补充研究基线。", "themes": pending_themes})
    if documents:
        reasons.append({"kind": "new_research_sources", "reason": "已研究范围出现新的、实质修订或撤回的来源资料。", "sources": documents})
    if risk_changes:
        reasons.append({"kind": "quantitative_risk_change", "reason": "已有复核规则出现新触发或实质变化。", "cases": risk_changes})
    if numerical_changed:
        reasons.append({"kind": "quantitative_inputs_changed", "reason": "留存价格或实际披露持仓发生变化，需要更新适用量化观察。"})
    if due:
        reasons.append({"kind": "research_check_due", "reason": "已绑定的研究观察到达检查时间。", "items": due})
    if not reasons:
        return None
    return {"instrument_id": instrument_id, "reasons": reasons, "coverage_cursor": {
        "attempted_at": now.isoformat(), "last_successful_review_cutoff": prior_context.get("last_successful_review_cutoff"),
        "previous_cutoff": since.isoformat(), "market_queries": queries,
        "due_event_versions": {row["case_id"]: row["event_version_id"] for row in due if row.get("case_id")},
    }}
