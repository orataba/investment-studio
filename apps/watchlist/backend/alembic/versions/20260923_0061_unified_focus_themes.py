"""Unify continuing research under a bounded, versioned focus-theme list.

This migration adds organization, never rewrites the information available to an
old judgment. Original runs and event snapshots stay intact; the current notebook
gets an explicit new organization revision. No model is invoked.
"""
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "20260923_0061"
down_revision = "20260920_0060"
branch_labels = None
depends_on = None


def migrate_focus_themes(connection):
    metadata = sa.MetaData()
    entries = sa.Table("research_entry", metadata, autoload_with=connection)
    topics = sa.Table("research_topic", metadata, autoload_with=connection)
    cases = sa.Table("risk_case", metadata, autoload_with=connection)
    now = datetime.now(UTC)
    stamp = now.isoformat()
    all_topics = {row["topic_id"]: dict(row) for row in connection.execute(
        sa.select(topics.c.topic_id, topics.c.portfolio_id, topics.c.visibility)).mappings()}
    # Historical conversations could change their current portfolio binding.
    # Every retained entry still owns its original privacy scope, including
    # evidence and risk runs that are not notebook candidates below. Read JSON
    # directly so PostgreSQL NUL escapes in unrelated evidence stay untouched.
    portfolio_topics = {identifier for identifier, topic in all_topics.items() if topic.get("portfolio_id")}
    with connection.execute(sa.select(entries.c.topic_id, entries.c.context_json)
                            .execution_options(yield_per=10)) as records:
        for topic_id, context in records:
            context = context or {}
            if context.get("portfolio_id") or (context.get("risk_scope") or {}).get("portfolio_id"):
                portfolio_topics.add(topic_id)
    all_cases = [dict(row) for row in connection.execute(
        sa.select(cases).where(cases.c.signal.startswith("sector:"))).mappings()]
    themes, notebooks = {}, {}

    def clock(row):
        return str(row.get("completed_at") or row.get("updated_at") or row.get("created_at") or "")

    # Candidate notes may contain themes; only published team analyses may own
    # a notebook. Do not JSON-extract flags here: retained PostgreSQL json can
    # contain a NUL escape in an unrelated source. Check exact roles below.
    candidates = sa.select(entries).outerjoin(topics, entries.c.topic_id == topics.c.topic_id).where(sa.or_(
        entries.c.kind == "note",
        sa.and_(entries.c.kind == "analysis", entries.c.status.in_(["completed", "draft"]),
                sa.or_(topics.c.portfolio_id.is_(None), topics.c.portfolio_id == ""), topics.c.visibility == "team"),
    )).order_by(sa.func.coalesce(entries.c.completed_at, entries.c.updated_at, entries.c.created_at))
    with connection.execute(candidates.execution_options(yield_per=10)) as records:
        for record in records.mappings():
            row = dict(record)
            context = row["context_json"] or {}
            team = row.get("team_id") or "default"
            topic = all_topics.get(row["topic_id"], {})
            if row["topic_id"] in portfolio_topics or topic.get("visibility") != "team":
                continue
            if context.get("role") == "research_theme":
                themes.setdefault((context["instrument_id"], team), []).append(row)
            if (row["kind"] != "analysis" or row["status"] not in {"completed", "draft"}
                    or topic.get("portfolio_id") or topic.get("visibility") != "team"
                    or not (context.get("sector_run") or context.get("research_run"))):
                continue
            for iid, review in context.get("reviews", {}).items():
                # Match the retained notebook reader: a mutable topic's current
                # instrument list cannot authorize an unbound review, and an
                # unpublished sector draft cannot become a published notebook.
                if (iid not in context.get("instrument_ids", []) or (not context.get("research_run")
                        and (row["status"] != "completed" or row["topic_id"] not in {
                            "us-sector-daily-review", f"instrument-events:{iid}"}))):
                    continue
                if review.get("status") in {"completed", "limited"} and isinstance(review.get("research"), dict) and review["research"]:
                    # Keep the latest notebook, not its full tool transcript,
                    # captured sources and every other instrument's run context.
                    retained_run = {key: row[key] for key in ("entry_id", "topic_id", "created_at", "completed_at")}
                    retained_run["context_json"] = {"cutoff": context.get("cutoff")}
                    notebooks[(iid, team)] = (retained_run, review["research"])
    record = row = context = None

    groups = set(themes) | set(notebooks)
    for case in all_cases:
        if case.get("status") == "open" and not (case.get("evidence_json") or {}).get("theme_ids"):
            groups.add((case["instrument_id"], "default"))

    for iid, team in sorted(groups):
        existing = themes.get((iid, team), [])
        dossier_id = f"dossier:{iid}"
        if dossier_id not in all_topics:
            values = dict(topic_id=dossier_id, team_id=team, visibility="team", title=f"{iid} · 研究档案",
                question="原始材料与资料沿革", instrument_ids=[iid], status="active", conclusion="", created_at=now, updated_at=now)
            connection.execute(topics.insert().values(**values))
            all_topics[dossier_id] = values
        occupied = 0
        # Existing assignments have precedence over newly organized legacy work.
        for row in sorted(existing, key=clock, reverse=True):
            context = deepcopy(row["context_json"])
            original = {**deepcopy(context), "theme_id": row["entry_id"], "instrument_id": iid,
                "title": row["title"], "question": row["body"], "status": context.get("theme_status", "active"),
                "created_at": str(row["created_at"]), "updated_at": str(row["updated_at"]),
                "author_user_id": row.get("author_user_id"), "responsible_user_id": row.get("responsible_user_id"),
                "team_id": team}
            original.pop("versions", None)
            context["versions"] = [*context.get("versions", []), original]
            context["revision_number"] = context.get("revision_number", 1) + 1
            context.update(kind=context.get("kind", "fundamental"), priority=context.get("priority", "important"),
                priority_reason=context.get("priority_reason", "沿用既有研究主题；重要性由下一轮统一复核。"),
                pinned=False, managed_by="researcher", theme_key=context.get("theme_key") or f"theme-{row['entry_id']}",
                synthesis=context.get("synthesis", ""), latest_development=context.get("latest_development", ""),
                next_check=context.get("next_check", ""), figure_source_ids=context.get("figure_source_ids", []),
                baseline_status="ready" if context.get("synthesis") else "pending", last_reviewed_at=None)
            if context.get("theme_status") == "active":
                if occupied >= 10:
                    context.update(theme_status="paused", close_reason="统一重点跟踪最多10项；本历史主题暂候下一轮研究员评估，不表示原问题已解决。")
                else:
                    occupied += 1
            context.update(updated_by="系统", updated_by_role="system", recorded_via="migration")
            context["migration_origin"] = {"revision": revision, "recorded_at": stamp, "note": "人工来源不自动固定；原作者与历次版本保留。"}
            connection.execute(entries.update().where(entries.c.entry_id == row["entry_id"]).values(context_json=context, updated_at=now))

        prior_run, prior_notebook = notebooks.get((iid, team), (None, None))
        notebook = deepcopy(prior_notebook) if prior_notebook else None
        candidates = {}
        event_rows = {case["signal"].removeprefix("sector:"): case for case in all_cases
                      if case["instrument_id"] == iid and team == "default"}
        for key, case in event_rows.items():
            evidence = case.get("evidence_json") or {}
            if (evidence.get("follow_up") or ("watch" if case["status"] == "open" else "none")) == "watch" and not evidence.get("theme_ids"):
                candidates[f"event:{key}"] = {"event": case, "items": []}
        for field, active_field, active_value in (("questions", "tracking_status", "active"),
                                                ("forecasts", "status", "active"), ("catalysts", "status", "scheduled")):
            for item in (notebook or {}).get(field, []):
                if item.get("theme_id") or item.get(active_field, active_value) != active_value:
                    continue
                event_key = item.get("event_key")
                linked = event_rows.get(event_key)
                linked_themes = (linked or {}).get("evidence_json", {}).get("theme_ids", [])
                if linked_themes:
                    item["theme_id"] = linked_themes[0]
                    continue
                group = f"event:{event_key}" if event_key else f"{field}:{item['key']}"
                candidate = candidates.setdefault(group, {"event": linked, "items": []})
                candidate["items"].append((field, item))
        for identity, candidate in candidates.items():
            event = candidate["event"]
            items = candidate["items"]
            first = items[0][1] if items else {}
            title = (event or {}).get("title") or first.get("question") or first.get("claim") or first.get("title") or "历史研究事项"
            assessment = first.get("assessment") or (event or {}).get("body") or first.get("claim") or first.get("relevance") or ""
            theme_id = uuid4().hex
            active = occupied < 10
            occupied += int(active)
            evidence = (event or {}).get("evidence_json") or {}
            refs = list(dict.fromkeys([*evidence.get("source_ids", []), *(sid for _, item in items for sid in item.get("source_ids", []))]))
            available = {source["source_id"]: source for source in [*(notebook or {}).get("sources", []), *evidence.get("sources", [])]}
            context = {"role": "research_theme", "instrument_id": iid, "author": "研究员", "origin": "researcher",
                "managed_by": "researcher", "theme_key": f"theme-{theme_id}", "theme_status": "active" if active else "paused",
                "kind": "event" if event else "fundamental", "priority": "important", "pinned": False,
                "priority_reason": "沿用历史未完成研究事项；实际重要性由下一轮统一复核。",
                "background": "由既有持续跟进记录整理，保留原判断、日期和依据；本次仅调整组织方式。",
                "synthesis": assessment, "latest_development": "历史持续跟进已纳入统一主题。",
                "next_check": first.get("next_check") or evidence.get("next_watch", ""),
                "source_ids": refs, "sources": [available[sid] for sid in refs if sid in available], "figure_source_ids": [],
                "revision_number": 1, "versions": [], "baseline_status": "pending", "last_reviewed_at": None,
                "close_reason": "" if active else "统一重点跟踪最多10项；待研究员评估优先级，不表示原问题已解决。",
                "migration_origin": {"revision": revision, "original_key": identity, "recorded_at": stamp}}
            if event:
                context["reference"] = {"event_case_id": event["case_id"], "event_version_id": evidence.get("event_version_id")}
            connection.execute(entries.insert().values(entry_id=theme_id, topic_id=dossier_id, team_id=team,
                kind="note", title=title[:300], body=(first.get("question") or title)[:4000], source="",
                status="recorded", context_json=context, created_at=now, updated_at=now))
            for _, item in items:
                item["theme_id"] = theme_id
            if event:
                retained_history = list(event.get("history_json") or [])
                if not any(row.get("snapshot") for row in retained_history):
                    original_time = evidence.get("recorded_at") or evidence.get("progress_at") or str(event["created_at"])
                    retained_history.append({"at": original_time, "action": "retained", "detail": "保留迁移前既有事件版本。",
                        "snapshot": {**deepcopy(evidence), "title": event["title"], "body": event["body"],
                            "status": event["status"], "trigger_active": event["trigger_active"],
                            "recorded_at": original_time, "event_version_id": evidence.get("event_version_id") or f"{event['case_id']}:1"}})
                snapshot = {**evidence, "theme_ids": [theme_id], "recorded_at": stamp,
                    "event_version_id": f"{event['case_id']}:{len(retained_history) + 1}",
                    "recorded_by": "系统", "recorded_by_role": "system"}
                history = [*retained_history, {"at": stamp, "action": "organized",
                    "detail": "持续跟进纳入统一重点主题；原事实和研究判断未改写。",
                    "snapshot": {**snapshot, "title": event["title"], "body": event["body"],
                                 "status": event["status"], "trigger_active": event["trigger_active"]}}]
                connection.execute(cases.update().where(cases.c.case_id == event["case_id"]).values(evidence_json=snapshot, history_json=history))
        if notebook is not None and notebook != prior_notebook:
            run_id = uuid4().hex
            organization = {"organized_at": stamp, "reason": "历史持续跟进纳入统一重点主题，未改变投资判断。", "updates": {}}
            for field in ("questions", "forecasts", "catalysts"):
                originals = {item["key"]: item for item in prior_notebook.get(field, [])}
                for item in notebook.get(field, []):
                    original = originals[item["key"]]
                    if item == original:
                        continue
                    original_version = original.get("version_id") or f"{prior_run['entry_id']}:{field}:{item['key']}"
                    organization["updates"][f"{field}:{item['key']}"] = {
                        "source_update_id": f"research:{original_version}",
                        "original_recorded_at": original.get("updated_at") or str(prior_run.get("completed_at") or prior_run["created_at"])}
                    # Organization is an explicit new version; the original
                    # judgment's identity and date remain inspectable.
                    item["versions"] = [*original.get("versions", []), {key: value for key, value in original.items() if key != "versions"}]
                    item.update(version_id=f"{run_id}:{field}:{item['key']}", updated_at=stamp)
            notebook.update(version_id=run_id, run_id=run_id, updated_at=stamp,
                checked_at=notebook.get("checked_at") or prior_run["context_json"].get("cutoff"),
                important_changes=["历史持续跟进已整理为统一重点主题；原判断和来源版本保留。"])
            context = {"research_run": True, "sector_run": True, "recordkeeping_only": True,
                "instrument_ids": [iid], "cutoff": stamp, "migration": revision, "organization_revision": organization,
                "reviews": {iid: {"status": "completed", "change_kind": "knowledge", "research": notebook,
                    "summary": "", "coverage": [], "themes": []}}}
            connection.execute(entries.insert().values(entry_id=run_id, topic_id=prior_run["topic_id"], team_id=team,
                kind="analysis", title="持续研究主题整合", body="仅调整研究组织，未生成新的投资判断。", source="系统迁移",
                status="completed", context_json=context, created_at=now, updated_at=now, completed_at=now))


def upgrade():
    migrate_focus_themes(op.get_bind())


def downgrade():
    raise RuntimeError("重点主题整合保留了真实后续研究；不可通过降级删除研究历史。请从升级前备份恢复。")
