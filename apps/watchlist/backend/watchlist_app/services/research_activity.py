"""One read projection of published research, shared by instrument and theme timelines.

The event history, dated notebooks and attributed PM revisions remain the owners of
their records. This module neither persists a second journal nor invokes a model.
"""
from copy import deepcopy
from datetime import UTC, datetime

from studio_identity import current_principal

from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_identity import research_identity


def _time(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat()
    if not value:
        return ""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC).isoformat()


def _details(**values):
    return [{"label": label, "text": "\n".join(value) if isinstance(value, list) else str(value)}
            for label, value in values.items() if value]


def _base(iid, identifier, kind, title, body, recorded_at, **values):
    return {"update_id": identifier, "instrument_id": iid, "kind": kind, "title": title, "body": body,
            "recorded_at": _time(recorded_at), "theme_ids": [], "sources": [],
            "author": "研究员", "author_role": "researcher", "details": [],
            "reference": {"instrument_id": iid, "research_update_id": identifier}, **values}


def _event_updates(session, iid):
    from watchlist_app.services.sector_research import events_for_instruments
    updates = []
    for event in events_for_instruments(session, [iid]):
        snapshots = [(index, row["snapshot"], row.get("at"), row.get("action"))
                     for index, row in enumerate(event.get("history", []), 1) if row.get("snapshot")]
        if not snapshots:
            snapshots = [(0, event, event.get("updated_at") or event.get("discovered_at"), "current")]
        for revision, row, recorded_at, change in snapshots:
            version_id = row.get("event_version_id") or f"{event['case_id']}:{revision}"
            identifier = f"event:{version_id}"
            follow_up = row.get("follow_up") or ("resolved" if row.get("status") == "resolved" else
                         "watch" if row.get("trigger_active", event["trigger_active"]) else "none")
            update = _base(iid, identifier, "event", row.get("title") or event["title"], row.get("body", ""),
                row.get("recorded_at") or recorded_at, theme_ids=row.get("theme_ids", []),
                author=row.get("recorded_by") or "研究员", author_role=row.get("recorded_by_role") or "researcher",
                sources=row.get("sources", []), occurred_at=row.get("occurred_at"), published_at=row.get("published_at"),
                next_check=row.get("next_watch", ""), follow_up=follow_up,
                analysis_depth=row.get("analysis_depth", "analysis"), direction=row.get("direction", "uncertain"),
                information_type=row.get("information_type"),
                confidence=row.get("confidence"), change=change, event_key=event["event_key"],
                run_id=row.get("run_id"), withdrawn=event.get("withdrawn", False),
                withdrawal_reason=event.get("withdrawal_reason"), withdrawn_at=event.get("withdrawn_at"),
                superseded=revision != snapshots[-1][0],
                details=_details(**{"资料限制": row.get("coverage")}))
            update["reference"].update(event_case_id=event["case_id"], event_version_id=version_id)
            updates.append(update)
    return updates


def question_progress_value(question):
    return {key: question.get(key) for key in ("key", "assessment", "next_check", "status", "source_ids",
        "evidence_for", "evidence_against", "pm_note_id", "pm_note_revision")}


def _notebook_updates(session, iid, events, *, theme_progress=None, actor=None):
    from watchlist_app.services.research_dossier import _research_records
    from watchlist_app.services.sector_research import _source_views
    updates, previous, chains = [], {}, {}
    definitions = {
        "questions": ("question", "question", "assessment"),
        "forecasts": ("forecast", "claim", "claim"),
        "forecast_reviews": ("review", "outcome", "outcome"),
        "lessons": ("lesson", "lesson", "lesson"),
        "catalysts": ("schedule", "title", "relevance"),
        "investment_view": ("judgment", "direction", "direction"),
    }
    metadata = {"version_id", "versions", "created_at", "updated_at", "source_run_id", "publication"}
    progress_last = {}
    for run, notebook in _research_records(session, iid, oldest_first=True):
        if theme_progress is not None and (current_principal().local_unrestricted or run.team_id == actor["team_id"]):
            for question in notebook.get("questions", []):
                theme_id = question.get("theme_id")
                if not theme_id:
                    continue
                value = question_progress_value(question)
                identity = (theme_id, question["key"])
                if progress_last.get(identity) == value:
                    continue
                progress_last[identity] = value
                theme_progress.setdefault(theme_id, []).append({**value, "run_id": run.entry_id,
                    "recorded_at": run.context_json.get("cutoff"), "author_role": "researcher"})
        sources = {source["source_id"]: source for source in notebook.get("sources", [])}
        correction = (run.context_json.get("citation_correction") or {}) if run.context_json.get("recordkeeping_only") else {}
        organization = (run.context_json.get("organization_revision") or {}) if run.context_json.get("recordkeeping_only") else {}
        for field, (kind, title_key, body_key) in definitions.items():
            if field == "investment_view" and not notebook.get(field):
                chain = (field, "investment-view")
                if chain in chains:
                    chains[chain]["superseded"] = True
                previous.pop(chain, None)
                continue
            rows = ([{"key": "investment-view", **notebook[field]}] if notebook.get(field) else []) if field == "investment_view" else notebook.get(field, [])
            for row in rows:
                chain = (field, row["key"])
                semantic = {key: value for key, value in row.items() if key not in metadata}
                if kind == "question":
                    # Older evidence judgments did not record a stop-tracking decision.
                    semantic.setdefault("tracking_status", "active")
                    semantic.setdefault("tracking_reason", "")
                if kind == "lesson":
                    semantic.setdefault("status", "active")
                    semantic.setdefault("withdrawal_reason", "")
                if previous.get(chain) == semantic:
                    continue
                previous[chain] = deepcopy(semantic)
                identifier = f"research:{row.get('version_id') or f'{run.entry_id}:{field}:{row["key"]}'}"
                recorded_at = row.get("updated_at") or run.completed_at or run.created_at
                title = row.get(title_key) or {"review": "研究复盘", "lesson": "研究经验"}.get(kind, "研究更新")
                if kind in {"review", "lesson", "forecast", "judgment"}:
                    title = {"review": "研究复盘", "lesson": "研究经验", "forecast": "可验证的判断", "judgment": "投资研究判断更新"}[kind]
                update = _base(iid, identifier, kind, title, row.get(body_key, ""), recorded_at,
                    author=(row.get("publication") or {}).get("display_name", "研究员"),
                    theme_ids=[row["theme_id"]] if row.get("theme_id") else [],
                    sources=_source_views([sources[sid] for sid in row.get("source_ids", []) if sid in sources]),
                    next_check=row.get("next_check") or (row.get("observation_condition") or row.get("horizon", "") if kind == "forecast" else ""), run_id=run.entry_id,
                    status=row.get("status"), scheduled_at=row.get("scheduled_at"),
                    tracking_status=row.get("tracking_status"), tracking_reason=row.get("tracking_reason", ""),
                    withdrawal_reason=row.get("withdrawal_reason", ""),
                    change="updated" if chain in chains else "new",
                    details=_details(**{"支持依据": row.get("evidence_for"), "反向证据": row.get("evidence_against"),
                        "机制复核": row.get("mechanism_assessment"), "其他解释": row.get("alternative_explanations"),
                        "适用条件": row.get("applicability"), "适用限制": row.get("limitations"),
                        "判断期限": row.get("horizon"), "观察条件": row.get("observation_condition"),
                        "失效条件": row.get("invalidation"), "关键假设": row.get("assumptions"),
                        "投资吸引力": row.get("attractiveness"), "风险判断": row.get("risk"), "判断把握": row.get("conviction"),
                        "日程结果": row.get("outcome") if kind == "schedule" else None}))
                update["reference"]["notebook_version_id"] = notebook.get("version_id", run.entry_id)
                corrected = correction.get("updates", {}).get(f"{field}:{row['key']}")
                if corrected:
                    update.update(change="citation_corrected", author="系统", author_role="system",
                        recorded_at=_time(correction["corrected_at"]),
                        citation_correction={**{key: value for key, value in correction.items() if key != "updates"}, **corrected})
                organized = organization.get("updates", {}).get(f"{field}:{row['key']}")
                if organized:
                    update.update(change="organized", author="系统", author_role="system",
                        recorded_at=_time(organization["organized_at"]),
                        organization_revision={**{key: value for key, value in organization.items() if key != "updates"}, **organized})
                if row.get("theme_id"):
                    update["reference"]["theme_id"] = row["theme_id"]
                if row.get("pm_note_id"):
                    update["reference"].update(pm_note_id=row["pm_note_id"], pm_note_revision=row.get("pm_note_revision"))
                if kind == "forecast" and row.get("version_id"):
                    update["reference"].update(forecast_key=row["key"], forecast_version_id=row["version_id"])
                if kind == "judgment" and row.get("version_id"):
                    update["reference"]["investment_view_version_id"] = row["version_id"]
                if row.get("event_key"):
                    linked = [event for event in events if event["event_key"] == row["event_key"]
                              and event["recorded_at"] <= update["recorded_at"]]
                    if linked:
                        event = max(linked, key=lambda item: item["recorded_at"])
                        update["theme_ids"] = list(dict.fromkeys([*update["theme_ids"], *event["theme_ids"]]))
                        update["reference"].update({key: event["reference"][key] for key in ("event_case_id", "event_version_id")})
                if row.get("related_research_update_id"):
                    update["related_update_id"] = row["related_research_update_id"]
                elif kind in {"review", "lesson"} and row.get("forecast_version_id"):
                    update["related_update_id"] = f"research:{row['forecast_version_id']}"
                if chain in chains:
                    chains[chain]["superseded"] = True
                chains[chain] = update
                updates.append(update)
    return updates


def _theme_updates(session, iid, actor):
    from watchlist_app.services.research_themes import theme_index
    from watchlist_app.services.sector_research import _source_views
    updates = []
    for theme in theme_index(session, iid, actor=actor):
        versions = [*theme.get("versions", []), theme]
        for index, row in enumerate(versions, 1):
            revision = row.get("revision_number", index)
            identifier = f"theme:{theme['theme_id']}:{revision}"
            origin = row.get("updated_by_role") or row.get("managed_by") or row.get("origin", "user")
            update = _base(iid, identifier, "theme", row.get("title", theme["title"]), row.get("question", ""),
                row.get("updated_at") or row.get("created_at") or theme["created_at"], theme_ids=[theme["theme_id"]],
                author=((row.get("publication") or {}).get("display_name", "研究员") if origin == "researcher"
                        else row.get("updated_by") or row.get("author") or theme.get("author") or "未标注作者"),
                author_role=origin if origin in {"researcher", "system"} else "user", change="new" if index == 1 else row.get("status"),
                details=_details(**{"主题背景": row.get("background"), "当前认识": row.get("synthesis"),
                    "最新进展": row.get("latest_development"), "下一检查": row.get("next_check"),
                    "重要原因": row.get("priority_reason"), "结束原因": row.get("close_reason")}),
                superseded=index != len(versions), run_id=row.get("source_run_id"), sources=_source_views(row.get("sources", [])))
            update["reference"].update(theme_id=theme["theme_id"], theme_version_id=identifier)
            updates.append(update)
    return updates


def _opinion_updates(session, iid, actor):
    from watchlist_app.repositories.sqlalchemy.research import SQLAlchemyInstrumentResearchRepository
    from watchlist_app.services.research_views import note_sources
    from watchlist_app.services.sector_research import _source_views
    repository = SQLAlchemyInstrumentResearchRepository()
    notes = {row.note_id: row for row in repository.list_notes(session, iid)
             if current_principal().local_unrestricted or row.team_id == actor["team_id"]}
    updates = []
    for row in repository.list_note_revisions(session, iid):
        if row.note_id not in notes or (not current_principal().local_unrestricted and row.team_id != actor["team_id"]):
            continue
        context = row.research_context or {}
        sources = note_sources(session, iid, context)
        identifier = f"opinion:{row.note_id}:{row.revision_number}"
        update = _base(iid, identifier, "opinion", row.title, row.body or row.summary, row.recorded_at,
            author=row.author or "未标注作者", author_role="user", theme_ids=[context["theme_id"]] if context.get("theme_id") else [],
            change=context.get("relationship", "initial"), run_id=context.get("source_run_id"),
            sources=_source_views(sources),
            superseded=row.revision_number != notes[row.note_id].revision_number,
            details=_details(**{"判断期限": context.get("horizon"), "复核条件": context.get("verification"),
                "复盘结果": context.get("outcome"), "机制复核": context.get("mechanism_assessment"),
                "经验": context.get("lesson"), "适用条件": context.get("applicability")}))
        update["reference"].update(pm_note_id=row.note_id, pm_note_revision=row.revision_number)
        for key in ("theme_id", "event_case_id", "event_version_id"):
            if context.get(key):
                update["reference"][key] = context[key]
        if context.get("research_update_id"):
            update["related_update_id"] = context["research_update_id"]
        updates.append(update)
    return updates


def research_activity(session, instrument_id, *, actor=None, include_recent_events=False, include_theme_progress=False):
    actor = actor or research_identity()
    events = _event_updates(session, instrument_id)
    theme_progress = {} if include_theme_progress else None
    updates = [*events, *_notebook_updates(session, instrument_id, events, theme_progress=theme_progress, actor=actor),
               *_theme_updates(session, instrument_id, actor), *_opinion_updates(session, instrument_id, actor)]
    by_id = {row["update_id"]: row for row in updates}
    for row in sorted(updates, key=lambda row: row["recorded_at"]):
        linked = by_id.get(row.get("related_update_id"))
        if linked:
            row["theme_ids"] = list(dict.fromkeys([*row["theme_ids"], *linked["theme_ids"]]))
            # Carry a dedicated research assignment through its original judgment.
            # Shared events only contribute theme_ids, not an exclusive assignment.
            if linked["reference"].get("theme_id") and not row["reference"].get("theme_id"):
                row["reference"]["theme_id"] = linked["reference"]["theme_id"]
    result = {"instrument_id": instrument_id,
        "updates": sorted(by_id.values(), key=lambda row: (row["recorded_at"], row["update_id"]), reverse=True)}
    if theme_progress is not None:
        result["theme_progress"] = theme_progress
    if include_recent_events:
        result["recent_events"] = [row for row in result["updates"] if row["kind"] == "event"
            and not row.get("superseded") and not row.get("withdrawn") and row.get("follow_up") == "none"]
    return serialize_payload(result)


def review_receipts(session, instrument_id):
    """Only a published receipt for an exact judgment proves that it was checked."""
    from sqlalchemy import JSON, String, func, select, true
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
    from watchlist_app.services.research_access import research_context_projection, research_projection_rows, topic_portfolio_ids_by_topic
    principal = current_principal()
    relation, context = research_context_projection(session, {"cutoff": String, "reviews": JSON})
    review = context["reviews"][instrument_id]
    query = select(ResearchEntry.topic_id, ResearchEntry.completed_at,
        context["cutoff"].label("cutoff"), review["reflection"].label("reflection")).select_from(ResearchEntry)
    if relation is not None:
        query = query.join(relation, true())
    rows = research_projection_rows(session, query.where(
            ResearchEntry.kind == "analysis", ResearchEntry.status.in_(["completed", "draft"]),
            review["status"].as_string().in_(["completed", "limited"]),
            True if principal.local_unrestricted else ResearchEntry.team_id == principal.team_id,
        ).order_by(func.coalesce(ResearchEntry.completed_at, ResearchEntry.created_at).desc(),
                   ResearchEntry.created_at.desc(), ResearchEntry.entry_id.desc()),
        {"cutoff": ("cutoff",), "reflection": ("reviews", instrument_id, "reflection")})
    topics = list(session.scalars(select(ResearchTopic).where(ResearchTopic.topic_id.in_({row.topic_id for row in rows}),
        True if principal.local_unrestricted else ResearchTopic.team_id == principal.team_id))) if rows else []
    allowed = {topic_id for topic_id, portfolios in topic_portfolio_ids_by_topic(session, topics).items() if not portfolios}
    receipts = {}
    for row in rows:
        topic_id, reflection = row.topic_id, row.reflection
        if not isinstance(reflection, dict) or topic_id not in allowed:
            continue
        for identifier in reflection.get("reviewed_update_ids", []):
            receipts.setdefault(identifier, {"last_reviewed_at": _time(row.completed_at or row.cutoff),
                "last_review_status": reflection.get("status"),
                "last_review_summary": reflection.get("summary", "")})
    return receipts


def judgment_changed_at(update):
    return (update.get("citation_correction") or update.get("organization_revision") or {}).get("original_recorded_at") or update["recorded_at"]


def judgment_review_receipt(update, receipts):
    original = (update.get("citation_correction") or update.get("organization_revision") or {}).get("source_update_id")
    return receipts.get(update["update_id"]) or receipts.get(original, {})


def resolve_research_update(session, instrument_id, update_id, *, actor=None):
    result = next((row for row in research_activity(session, instrument_id, actor=actor)["updates"]
                   if row["update_id"] == update_id), None)
    if result is None:
        raise ValueError("找不到当前标的已发布的研究更新")
    return result


def resolve_event_reference(session, instrument_id, case_id, version_id):
    if not case_id or not version_id:
        raise ValueError("引用事件须同时指定事件和当时的版本")
    result = next((row for row in _event_updates(session, instrument_id)
                   if row["reference"]["event_case_id"] == case_id and row["reference"]["event_version_id"] == version_id), None)
    if result is None:
        raise ValueError("找不到当前标的的事件版本")
    return result


def review_agenda(session, instrument_id, notebook, pm_views, *, actor=None, themes=None, activity=None):
    """Point to existing judgments; these are questions to check, not new evidence."""
    from watchlist_app.services.research_themes import theme_index
    inactive_themes = {theme["theme_id"] for theme in (themes if themes is not None else theme_index(session, instrument_id, actor=actor))
                       if theme["status"] != "active"}
    updates = (activity if activity is not None else research_activity(session, instrument_id, actor=actor))["updates"]
    assignments = {row["update_id"]: row["reference"].get("theme_id") for row in updates}
    current = [row for row in updates
               if not row.get("superseded") and not row.get("withdrawn")
               and not (row["kind"] in {"question", "forecast", "lesson", "schedule"}
                        and row["reference"].get("theme_id") in inactive_themes)]
    notebook = notebook or {}
    forecasts = {f"research:{row.get('version_id')}": row for row in notebook.get("forecasts", [])}
    from watchlist_app.services.research_themes import themes_view
    active_themes = [row for row in (themes if themes is not None else themes_view(session, instrument_id, actor=actor)["themes"])
                     if row["status"] == "active"]
    return {"focus_themes": active_themes, "focus_policy": {
            "target_count": 5, "active_limit": 10,
            "instruction": "每轮逐一回顾所有重点主题、当时判断、时间线与相关投资观点；决定保留、更新、合并替换或关闭。无变化只提交主题标识作为复核回执。人工创建不表示固定，仅pinned主题的核心与生命周期保留人工控制。"},
        "pending_events": [{"update_id": row["update_id"], "title": row["title"],
                "next_check": row.get("next_check"), "reference": row["reference"]}
            for row in current if row["kind"] == "event" and row.get("follow_up") == "watch"
            and any(identifier not in inactive_themes for identifier in row.get("theme_ids", []))],
        "active_forecasts": [{"update_id": row["update_id"], **{key: forecast.get(key)
                for key in ("key", "version_id", "claim", "horizon", "observation_condition", "review_on", "invalidation")}}
            for row in current if row["kind"] == "forecast" and (forecast := forecasts.get(row["update_id"], {})).get("status") == "active"],
        "tracked_questions": [{"update_id": row["update_id"], "question": row["title"], "assessment": row["body"],
                "status": row.get("status"), "tracking_status": "active", "next_check": row.get("next_check"),
                "reference": row["reference"]}
            for row in current if row["kind"] == "question" and (row.get("tracking_status") or "active") == "active"],
        "current_judgment": [{"update_id": row["update_id"], "reference": row["reference"],
                "assessment": row["body"], **{key: (notebook.get("investment_view") or {}).get(key)
                    for key in ("horizon", "risk", "assumptions", "invalidation", "next_check")}}
            for row in current if row["kind"] == "judgment"],
        "scheduled_catalysts": [{"update_id": row["update_id"], "reference": row["reference"],
                "title": row["title"], "scheduled_at": row.get("scheduled_at"), "relevance": row["body"],
                "next_check": row.get("next_check")}
            for row in current if row["kind"] == "schedule" and row.get("status") == "scheduled"],
        "existing_lessons": [{"update_id": row["update_id"], "lesson": row["body"], "conditions": row.get("details", []), "reference": row["reference"]}
            for row in current if row["kind"] == "lesson" and row.get("status") != "withdrawn"],
        "pm_views": [{"update_id": f"opinion:{row['note_id']}:{row['revision_number']}",
                       "title": row["title"], "author": row.get("author"), "follow_up_date": row.get("follow_up_date")}
                     for row in pm_views if assignments.get(f"opinion:{row['note_id']}:{row['revision_number']}",
                         (row.get("research_context") or {}).get("theme_id")) not in inactive_themes],
        "instruction": "每轮自动研究检查相关未决判断和待跟进事项；新证据、反证、结果或观察期限触发必要复盘。到期但资料不足不是判断错误。原判断及已有经验不是事实依据，逐项对照新原文；没有实质变化不生成复盘文章。"}
