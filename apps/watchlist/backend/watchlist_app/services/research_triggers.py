"""Cheap information checks; calendar and ordinary numerical refreshes are not research."""
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
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
        channels = sorted(set(query.get("channel_ids") or []))
        key = (terms, tuple(entities), tuple(channels))
        cutoff = query.get("cutoff") or context.get("input_snapshot_cutoff") or context["cutoff"]
        if key not in output or _instant(cutoff) > _instant(output[key]["cutoff"]):
            output[key] = {"instrument_id": instrument_id, "query": terms, "entities": entities,
                           "cutoff": _instant(cutoff).isoformat(),
                           **({"channel_ids": channels} if channels else {}),
                           **({"origin": query["origin"]} if query.get("origin") else {})}
    return list(output.values())


def _monitoring_context(session, instrument_id, context):
    if _queries(context, instrument_id):
        return context
    # A new daily run or a failed/manual task need not search again. Preserve the
    # most recent actual research scope, rather than silently ending monitoring.
    # A private query is itself private information even when it searched public
    # documents. Only shared, non-portfolio research establishes automatic scope.
    from watchlist_app.services.research_access import instrument_run_scope, topic_portfolio_ids
    query = select(ResearchEntry.topic_id, ResearchEntry.entry_id).join(ResearchTopic, ResearchTopic.topic_id == ResearchEntry.topic_id).where(
        ResearchEntry.kind == "analysis", ResearchTopic.visibility == "team", ResearchTopic.portfolio_id.is_(None),
        instrument_run_scope(session, instrument_id),
    ).order_by(ResearchEntry.created_at.desc(), ResearchEntry.entry_id.desc())
    team_id = (context.get("research_actor") or {}).get("team_id")
    if team_id:
        query = query.where(ResearchEntry.team_id == team_id, ResearchTopic.team_id == team_id)
    # A follow-up without its own query scope must not hydrate the whole team's
    # archived inputs. Read only this instrument, stopping at its first usable
    # retained scope; historical portfolio restrictions still apply to the topic.
    with closing(session.execute(query.execution_options(yield_per=1))) as entries:
        for entry in entries:
            topic = session.get(ResearchTopic, entry.topic_id)
            if topic_portfolio_ids(session, topic):
                continue
            from watchlist_app.services.research_run_context import load_run_fields
            prior = load_run_fields(session, entry.entry_id, {
                "sector_run", "research_run", "instrument_ids", "cutoff", "input_snapshot_cutoff",
                "incremental_trigger", "market_queries"}, summary_only=True).context_json
            if (prior.get("sector_run") or prior.get("research_run")) and instrument_id in prior.get("instrument_ids", []):
                if _queries(prior, instrument_id):
                    return prior
    return context


def trigger_dossier(session, instrument_id):
    """Current schedules and exact source dependencies without retained input trees."""
    from watchlist_app.services.research_dossier import _notebooks
    from watchlist_app.services.research_working_set import current_events, working_notebook, source_ids
    from watchlist_app.services.research_themes import theme_summaries
    notebook, _ = _notebooks(session, instrument_id, False, source_metadata_only=True)
    themes = [row for row in theme_summaries(session, instrument_id)["themes"]
              if row["status"] == "active" and row.get("priority") in {"important", "core"}]
    notebook = working_notebook(notebook, {row["theme_id"] for row in themes})
    events = current_events(session, instrument_id)
    wanted = source_ids([notebook, themes, events])
    sources = [*(notebook or {}).get("sources", []),
        *(source for theme in themes for source in theme.get("sources", [])),
        *(source for event in events for source in event["evidence"].get("sources", []))]
    if notebook:
        # A retained source directory also contains former/background evidence.
        # Only references supporting the current working set establish monitoring.
        notebook["sources"] = [row for row in notebook.get("sources", []) if row.get("source_id") in wanted]
    return {"instrument_id": instrument_id, "notebook": notebook, "source_ids": sorted(wanted),
        "themes": themes, "prior_sources": [row for row in sources if row.get("source_id") in wanted]}


def _readable(document):
    return (document.get("content_completeness") in {"full_text", "source_excerpt"}
            and bool((document.get("content_text") or "").strip())
            and document.get("information_type") not in {"ai_summary", "generated_summary"}
            and not (document.get("provenance") or {}).get("ai_generated"))


def _filing_notice(document):
    """An exact provider filing index can prompt reading, never prove its body."""
    return (document.get("channel_id") == "fmp:sec_filings"
            and document.get("information_type") == "regulatory_filing"
            and document.get("content_completeness") == "title_only"
            and (document.get("provenance") or {}).get("content_scope") == "metadata")


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
                                    **({"channel_ids": query["channel_ids"]} if query.get("channel_ids") else {}),
                                    **{clock: since - timedelta(microseconds=1)}, as_of=now, limit=100, offset=offset, include_withdrawn=True)
                for row in page["rows"]:
                    if row["source_id"] in changed or row["source_id"] in read_source_ids:
                        continue
                    arrived = max(_instant(row["received_at"]), _instant(row["observed_at"]))
                    if arrived < since:
                        continue
                    previous = store.read(row["document_id"], as_of=since - timedelta(microseconds=1))
                    withdrawn = row.get("status") in {"withdrawn", "superseded"}
                    notice = _filing_notice(row)
                    if (withdrawn and not previous) or (not withdrawn and not (_readable(row) or notice)):
                        continue
                    if previous and (previous["version_id"] == row["version_id"] or _same_original(previous, row)):
                        continue
                    changed[row["source_id"]] = {
                        key: row.get(key) for key in (
                            "source_id", "document_id", "version_id", "title", "url", "body_sha256",
                            "published_at", "observed_at", "received_at", "content_completeness", "status", "information_type", "channel_id",
                        )
                    } | {"change": "withdrawn_original" if withdrawn else "revised_original" if previous else "new_original",
                         **({"previous_source_id": previous["source_id"]} if previous else {}),
                         **({"evidence_scope": "filing_index", "requires_original_read": True,
                             "note": "监管披露索引发生变化；仅证明披露记录存在，须读取链接原文后分析正文与影响。"} if notice else {}),
                         "matched_query": query}
                offset += len(page["rows"])
                if offset >= page["total"] or not page["rows"]:
                    break
    return list(changed.values())


def _read_originals(context, instrument_id):
    """Current bound references remain dependencies after title/entity corrections."""
    from watchlist_app.services.research_working_set import source_ids
    sources, referenced = [], set()
    for dossier in context.get("research_dossiers", []):
        if dossier.get("instrument_id") != instrument_id:
            continue
        referenced.update(source_ids(dossier))
        notebook = dossier.get("notebook") or {}
        current_refs = source_ids(notebook)
        # prior_sources is the explicitly bound theme/event/notebook working set;
        # an older notebook's source catalogue alone is not a live dependency.
        originals = [*dossier.get("prior_sources", []),
                     *(row for row in notebook.get("sources", []) if row.get("source_id") in current_refs)]
        sources.extend(originals)
    # A known immutable reference can outlive its optional catalogue metadata.
    # Resolve that exact ID in TextStore; missing metadata never proves unreferenced.
    return {**{source_id: {"source_id": source_id} for source_id in referenced if source_id.startswith("text:")},
            **{row["source_id"]: row for row in sources if str(row.get("source_id", "")).startswith("text:")}}


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
        if (not latest or latest["source_id"] == source_id
                or (consumed_at is not None
                    and max(_instant(latest["received_at"]), _instant(latest["observed_at"])) <= consumed_at)
                or _same_original(original, latest)):
            continue
        withdrawn = latest.get("status") in {"withdrawn", "superseded"}
        if not withdrawn and not _readable(latest):
            continue
        previous_ids = {*changed.get(latest["source_id"], {}).get("previous_source_ids", []), source_id}
        changed[latest["source_id"]] = {key: latest.get(key) for key in (
            "source_id", "document_id", "version_id", "title", "url", "body_sha256",
            "published_at", "observed_at", "received_at", "content_completeness", "status",
        )} | {"change": "withdrawn_original" if withdrawn else "revised_original",
              "previous_source_id": source_id, "previous_source_ids": sorted(previous_ids), "dependency": "previously_read_original"}
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


def _numeric_changes(session, instrument_id, since, now):
    """Same-period consensus revisions and changed retained financial facts.

    Batch clocks alone are not information. The existing monitor owns retry and
    applied deduplication; these identities refer to immutable numeric versions.
    """
    from watchlist_app.services.research_fmp_scope import build_fmp_research_scope
    from watchlist_app.services.sector_estimates import read_estimate_evidence
    from watchlist_app.services.sector_market_data import all_rows, numeric_store
    if since >= now:
        return []
    scope = next((row for row in build_fmp_research_scope(session, as_of=now,
        instrument_ids=[instrument_id])["instruments"] if row["instrument_id"] == instrument_id), None)
    if scope is None or not scope["companies"]:
        return []
    evidence = read_estimate_evidence(session, instrument_id, as_of=now, since=since, company_scope=scope)
    changes = []
    for row in evidence["changes"]:
        identity = [row[key] for key in ("symbol", "frequency", "target_period_end", "metric")]
        changes.append({"key": "estimate:" + ":".join(identity), "version_id": row["current_source_id"],
            "change": "consensus_revision", "source_ids": [row["previous_source_id"], row["current_source_id"]],
            "title": f"{row['symbol']} {row['target_period_end']} {row['metric']} 同口径预期变化",
            **row})
    symbols = [row["symbol"] for row in scope["companies"]]
    rows = all_rows(numeric_store(), "financial_statements", symbols=symbols, as_of=now, versions=True)
    def available(row):
        return max(_instant(row["observed_at"]), _instant(row.get("available_at") or row["observed_at"]))
    def winner_rank(row):
        return _instant(row["observed_at"]), row["batch_id"], row["row_index"]
    # Match NumericStore's visible winner, not arrival order. At one availability
    # clock, consider the highest-ranked version first; other simultaneously
    # available versions were never the current fact at any research cutoff.
    rows.sort(key=winner_rank, reverse=True)
    rows.sort(key=available)
    previous = {}
    for row in rows:
        identity = tuple(row[key] for key in ("symbol", "statement_type", "period_end", "fiscal_year", "fiscal_period"))
        before = previous.get(identity)
        if before is not None and winner_rank(row) <= winner_rank(before):
            continue
        version = row.get("statement_content_sha256")
        if (available(row) > since and version
                and (before is None or version != before.get("statement_content_sha256"))):
            changes.append({"key": "financial:" + ":".join(map(str, identity)), "version_id": row["source_id"],
                "change": "revised_financial_statement" if before else "new_financial_statement",
                "title": f"{row['symbol']} {row['period_end']} {row['statement_type']} 财报事实版本更新",
                "source_ids": [source["source_id"] for source in (before, row) if source],
                "symbol": row["symbol"], "statement_type": row["statement_type"],
                "period_end": row["period_end"], "fiscal_year": row["fiscal_year"], "fiscal_period": row["fiscal_period"],
                "observed_at": row["observed_at"], "available_at": row.get("available_at"), "filing_date": row.get("filing_date"),
                "accepted_at": row.get("accepted_at"), "statement_content_sha256": version,
                "previous_statement_content_sha256": before.get("statement_content_sha256") if before else None})
        previous[identity] = row
    return changes


def _due_items(session, instrument_id, context, now):
    from watchlist_app.services.sector_research import _research_market, RESEARCH_TIMEZONES
    market = _research_market(session, instrument_id)
    zone = ZoneInfo(RESEARCH_TIMEZONES[market]) if market else None
    dossier = next((item for item in context.get("research_dossiers", []) if item.get("instrument_id") == instrument_id), {})
    notebook = dossier.get("notebook") or {}
    inactive_themes = {theme["theme_id"] for theme in dossier.get("themes", []) if theme.get("status") != "active"}
    due = []
    today = now.astimezone(zone or UTC).date().isoformat()
    from watchlist_app.services.research_working_set import current_events
    for event in current_events(session, instrument_id):
        if (event["follow_up"] != "watch" or not event.get("follow_up_until")
                or event["follow_up_until"] > today):
            continue
        due.append({"kind": "event_follow_up_due", "key": event["event_key"], "case_id": event["case_id"],
            "event_version_id": event["event_version_id"], "due_at": event["follow_up_until"], "title": event["title"],
            "pinned": event["follow_up_pinned"], "next_observation_on": event["next_observation_on"],
            "risk_still_active": event["trigger_active"],
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
                crossed = zone is not None and date.fromisoformat(str(value)) <= now.astimezone(zone).date()
            else:
                crossed = _instant(value) <= now
            if crossed:
                due.append({"kind": kind, "key": item["key"], "due_at": str(value),
                            "version_id": item.get("version_id"),
                            "theme_id": item.get("theme_id"),
                            "title": item.get("claim") or item.get("title"),
                            "note": "已到约定检查时间；不代表事件已经发生或预测已经得到验证。"})
    return due


def research_trigger(session, instrument_id, prior_context, *, now):
    """Scan current inputs, returning a cursor even when there is no new work.

    ResearchMonitorState owns pending/attempted/applied deduplication. This scan
    does not equate a successful source check with an applied model result.
    """
    now = _instant(now)
    previous = _previous_trigger(prior_context, instrument_id).get("coverage_cursor", {})
    since = max(_instant(prior_context["cutoff"]), _instant(_attempted_at(previous) or prior_context["cutoff"]))
    from investment_studio_instrument_core.db_models import Instrument
    instrument = session.get(Instrument, instrument_id)
    private = instrument is not None and instrument.instrument_type == "private_fund"
    monitoring = prior_context if private else _monitoring_context(session, instrument_id, prior_context)
    queries = _queries(monitoring, instrument_id)
    from watchlist_app.services.research_fmp_scope import build_fmp_research_scope, fixed_document_queries, FIXED_INPUT_ORIGIN
    # Holding-derived entities are recomputed, never inherited as permanent extras.
    queries = [query for query in queries if query.get("origin") != FIXED_INPUT_ORIGIN]
    if private:
        # Contracts, manager materials and bound-source corrections are checked
        # below. A private fund's name is not a generic daily-news mandate.
        queries = []
    else:
        scope = build_fmp_research_scope(session, as_of=now, instrument_ids=[instrument_id])
        queries.extend(fixed_document_queries(scope, cutoff=prior_context.get("input_snapshot_cutoff") or prior_context["cutoff"]))
    consumed = [cursor for cursor in (_attempted_at(previous),
        _attempted_at(_previous_trigger(monitoring, instrument_id).get("coverage_cursor", {}))) if cursor]
    read_source_ids = {row["source_id"] for context in (monitoring, prior_context)
                       for row in context.get("market_text_sources", [])}
    read_source_ids.update(row["source_id"] for context in (monitoring, prior_context)
                           for capture in context.get("web_evidence", [])
                           for row in capture.get("sources", []) if row.get("source_id"))
    read_source_ids.update(previous.get("boundary_source_ids", []))
    documents = _new_text(text_store(), queries, max(consumed, key=_instant) if consumed else None, now, read_source_ids) if queries else []
    originals = _read_originals({"research_dossiers": prior_context.get("research_dossiers", [])}, instrument_id)
    if originals:
        # The run cutoff can advance through an unrelated fetch after this source
        # was read. Only a prior dependency scan advances this cursor; reading
        # the latest version for one theme does not settle other old dependents.
        dependency_cursor = max((_instant(cursor) for cursor in consumed), default=None)
        revisions = _revised_originals(text_store(), originals, dependency_cursor, now)
        documents = list({row["source_id"]: row for row in [*documents, *revisions]}.values())
    risk_changes = _risk_changes(session, instrument_id, since, now)
    numeric_since = _instant(previous.get("numeric_cutoff") or prior_context.get("last_successful_numeric_cutoff")
        or prior_context.get("input_snapshot_cutoff") or prior_context.get("last_successful_review_cutoff") or prior_context["cutoff"])
    numeric_changes = [] if private else _numeric_changes(session, instrument_id, numeric_since, now)
    due = _due_items(session, instrument_id, prior_context, now)
    materials, registered_materials = [], previous.get("registered_materials")
    product_material = previous.get("product_material")
    from watchlist_app.services.research_dossier import material_index
    from watchlist_app.services.research_identity import research_identity
    catalogue = material_index(session, instrument_id, actor=research_identity())
    for row in catalogue:
        if row.get("entry_id") and (stamp := row.get("updated_at") or row.get("recorded_at")) and since < _instant(stamp) <= now:
            materials.append(row)
    current_documents = [row for row in catalogue if not row.get("entry_id")]
    if registered_materials is not None and current_documents != registered_materials:
        materials.append({"key": "registered_material_directory", "title": "登记材料目录发生变化",
                          "version_id": sha256(json.dumps(current_documents, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                          "documents": current_documents})
    registered_materials = current_documents
    from watchlist_app.db.models import InstrumentManualProfile
    manual = session.get(InstrumentManualProfile, instrument_id)
    current_product = {name: getattr(manual, field) if manual else None for name, field in (
        ("people", "people_payload_json"), ("strategy", "strategy_payload_json"),
        ("terms_and_fees", "price_payload_json"), ("nav_settings", "nav_settings_json"))}
    if product_material is not None and current_product != product_material:
        materials.append({"key": "product_material", "title": "登记的管理人、策略或产品机制发生变化",
            "version_id": sha256(json.dumps(current_product, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "changed_fields": [key for key, value in current_product.items() if value != product_material.get(key)],
            "previous": product_material, "current": current_product,
            "note": "人工登记资料变化是待核实输入，不代表已有独立事实确认。"})
    product_material = current_product
    dossier = next((row for row in prior_context.get("research_dossiers", []) if row["instrument_id"] == instrument_id), {})
    pending_themes = [{"theme_id": row["theme_id"], "title": row["title"], "reference": row.get("reference"),
                       "baseline_requested_at": row.get("baseline_requested_at") or row.get("created_at")}
        for row in dossier.get("themes", []) if row["status"] == "active" and row.get("baseline_status") == "pending"]
    reasons = []
    if pending_themes:
        reasons.append({"kind": "theme_baseline", "reason": "新建重点主题需要主动补充研究基线。", "themes": pending_themes})
    if documents:
        reasons.append({"kind": "new_research_sources", "reason": "已研究范围出现新的、实质修订或撤回的来源资料。", "sources": documents})
    if numeric_changes:
        reasons.append({"kind": "financial_input_change", "reason": "固定公司范围内出现同口径预期修订或新的财报事实版本；核实投资影响。", "items": numeric_changes})
    if risk_changes:
        reasons.append({"kind": "quantitative_risk_change", "reason": "已有复核规则出现新触发或实质变化。", "cases": risk_changes})
    if materials:
        reasons.append({"kind": "material_arrival", "reason": "授权材料或登记资料有新增、修订；按实际内容核查其研究影响。", "materials": materials})
    if due:
        reasons.append({"kind": "research_check_due", "reason": "已绑定的研究观察到达检查时间。", "items": due})
    return {"instrument_id": instrument_id, "reasons": reasons, "coverage_cursor": {
        "attempted_at": now.isoformat(), "last_successful_review_cutoff": prior_context.get("last_successful_review_cutoff"),
        "previous_cutoff": since.isoformat(), "market_queries": queries, "numeric_cutoff": now.isoformat(),
        "registered_materials": registered_materials,
        "product_material": product_material,
        "boundary_source_ids": [row["source_id"] for row in documents
            if max(_instant(row["received_at"]), _instant(row["observed_at"])) == now],
    }}
