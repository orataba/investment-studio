"""Human wording corrections and personal in-app receipts, without risk-state writes."""
from datetime import UTC, datetime
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select
from studio_identity import current_principal

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.db.models.watchlists import WatchlistUserSettings
from watchlist_app.services.research_access import require_entry_access, require_portfolio, require_team_write
from watchlist_app.services.risk_notices import deterministic_notice, mandatory_case


def _run_notice(session, run_id, notice_id, *, lock=False):
    run = session.get(ResearchEntry, run_id, with_for_update=lock, populate_existing=lock)
    require_entry_access(session, run)
    if run.status != "completed" or not run.context_json.get("risk_run"):
        raise HTTPException(409, "只能操作已完成风险提示。")
    notice = next((item for item in run.context_json.get("risk_notice_items") or []
                   if item["notice_id"] == notice_id), None)
    if notice is None:
        raise HTTPException(404, "风险提示不存在。")
    return run, notice


def correct_notice(session, run_id, notice_id, request):
    principal = current_principal()
    if principal.kind != "user" or not principal.user_id:
        raise HTTPException(403, "人工更正须由有权限的登录用户操作。")
    run, original = _run_notice(session, run_id, notice_id, lock=True)
    scope = run.context_json["risk_scope"]
    if scope.get("portfolio_id"):
        access = require_portfolio(scope["portfolio_id"])
        if not principal.local_unrestricted and not access.get("can_edit", False):
            raise HTTPException(403, "当前组合权限不能更正共享风险提示。")
    else:
        require_team_write()
    session.get(ResearchTopic, run.topic_id, with_for_update=True, populate_existing=True)
    latest = session.scalar(select(ResearchEntry.entry_id).where(ResearchEntry.topic_id == run.topic_id,
        ResearchEntry.kind == "analysis", ResearchEntry.status == "completed")
        .order_by(ResearchEntry.created_at.desc()).limit(1))
    if latest != run_id or str(original["notice_version"]) != str(request.expected_notice_version):
        raise HTTPException(409, "风险提示版本已有更新，请重新读取后更正。")
    now = datetime.now(UTC).isoformat()
    changed = {**original, "level": request.level, "title": request.title, "body": request.body,
        "notice_version": int(original["notice_version"]) + 1, "edited_at": now,
        "edited_by": principal.display_name or principal.user_id}
    audit = {"notice_id": notice_id, "at": now, "author_user_id": principal.user_id,
        "author": principal.display_name, "reason": request.reason, "before": original, "after": changed}
    context = deepcopy(run.context_json)
    context["risk_notice_items"] = [changed if item["notice_id"] == notice_id else item
                                    for item in context["risk_notice_items"]]
    context["result"]["notice_items"] = context["risk_notice_items"]
    context["risk_notice_corrections"] = [*context.get("risk_notice_corrections", []), audit]
    run.context_json = context
    session.commit()
    return changed


def _receipt_notice(session, request):
    if request.notice_id.startswith("rule:"):
        case = session.get(RiskCase, request.notice_id.removeprefix("rule:"))
        value = {key: getattr(case, key) for key in ("case_id", "instrument_id", "signal", "title", "body",
            "trigger_active", "status", "observed_on", "evidence_json", "history_json")} if case else None
        if not value or value["signal"] not in {"drawdown_limit", "period_loss"} or not mandatory_case(value):
            raise HTTPException(404, "当前严重规则提示不存在。")
        return deterministic_notice(value)
    if not request.run_id:
        raise HTTPException(422, "模型提示回执须绑定原运行。")
    notice = _run_notice(session, request.run_id, request.notice_id)[1]
    from watchlist_app.services.risk_workspace_projection import case_summary_rows
    from watchlist_app.services.risk_notices import current_notices
    cases = case_summary_rows(session, set(notice.get("instrument_ids") or []))
    projected = current_notices({"notice_items": [notice]}, cases)
    return next((item for item in projected if item["notice_id"] == notice["notice_id"]), notice)


def save_receipt(session, request):
    principal = current_principal()
    if principal.kind != "user" or not principal.user_id:
        raise HTTPException(403, "通知回执与静默设置须属于当前登录用户。")
    notice = _receipt_notice(session, request)
    version = str(notice["notice_version"])
    if str(request.notice_version) != version:
        raise HTTPException(409, "提示已有新版本，请先读取新事实。")
    current_rules = {item["notice_id"]: str(item["notice_version"]) for item in notice.get("current_rule_facts", [])}
    if request.rule_versions != current_rules:
        raise HTTPException(409, "严重规则事实已有变化，请读取当前版本后记录回执。")
    if request.action == "mute" and (request.muted_until is None or request.muted_until.tzinfo is None
            or request.muted_until <= datetime.now(UTC)):
        raise HTTPException(422, "静默截止时间须为带时区的未来时间。")
    # The user-settings row may not yet exist. An atomic insert followed by a
    # row lock prevents a first receipt from racing another settings writer.
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    session.execute(insert(WatchlistUserSettings).values(user_id=principal.user_id,
        ordered_watchlist_ids=[], risk_notice_preferences_json={}).on_conflict_do_nothing(index_elements=["user_id"]))
    settings = session.get(WatchlistUserSettings, principal.user_id, with_for_update=True, populate_existing=True)
    preferences = deepcopy(settings.risk_notice_preferences_json or {})
    notices = preferences.setdefault("receipts", {})
    updated = {}
    for notice_id, selected_version in [(request.notice_id, version), *current_rules.items()]:
        versions = notices.setdefault(notice_id, {})
        receipt = dict(versions.get(selected_version) or {})
        if request.action == "read":
            receipt["read_at"] = datetime.now(UTC).isoformat()
        else:
            receipt["muted_until"] = request.muted_until.isoformat() if request.action == "mute" else None
        receipt["updated_at"] = datetime.now(UTC).isoformat()
        versions[selected_version] = receipt
        if notice_id == request.notice_id:
            updated = receipt
    settings.risk_notice_preferences_json = preferences
    session.commit()
    return {"notice_id": request.notice_id, "notice_version": notice["notice_version"], **updated}


def apply_receipts(session, notices):
    principal = current_principal()
    settings = session.get(WatchlistUserSettings, principal.user_id) if principal.user_id else None
    receipts = (settings.risk_notice_preferences_json or {}).get("receipts", {}) if settings else {}
    now = datetime.now(UTC)
    def state(item):
        receipt = (receipts.get(item.get("notice_id")) or {}).get(str(item.get("notice_version"))) or {}
        muted_until = receipt.get("muted_until")
        muted = bool(muted_until and datetime.fromisoformat(muted_until) > now)
        return {**receipt, "muted": muted, "unread": not receipt.get("read_at") and not muted}
    result = []
    for notice in notices:
        # New severe episodes cannot inherit a read/silence receipt attached to
        # the older model wording. Both version sets are bound on user writes.
        receipt = state(notice)
        facts = [{**fact, "receipt": state(fact)} for fact in notice.get("current_rule_facts", [])]
        if any(fact["receipt"]["unread"] for fact in facts):
            receipt = {**receipt, "unread": True, "muted": False}
        result.append({**notice, "receipt": receipt, **({"current_rule_facts": facts} if facts else {})})
    return result
