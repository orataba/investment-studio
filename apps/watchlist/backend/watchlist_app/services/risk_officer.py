"""A scoped risk officer reads retained research, quantitative triggers and holdings."""
from datetime import UTC, date, datetime
import math
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from watchlist_app.db.models import InstrumentDetail, Watchlist, WatchlistItem
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_workbench import external_json
from watchlist_app.services.risk_performance import peer_context, performance_evidence

TOPIC_PREFIX = "risk-officer:"
SCOPE_KEYS = ("watchlist_id", "instrument_id", "portfolio_id")
QUANTITATIVE_SIGNALS = {"drawdown_limit", "period_loss"}


def normalize_scope(**values):
    selected = [(key, value.strip()) for key, value in values.items() if key in SCOPE_KEYS and isinstance(value, str) and value.strip()]
    if len(selected) != 1:
        raise ValueError("请选择一个完整列表、组合或标的作为风险研判范围。")
    return dict(selected)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _portfolio_snapshot(portfolio_id):
    context = external_json("portfolio", f"/portfolios/{quote(portfolio_id, safe='')}/risk-context")
    holdings = context["workspace"]
    if str(holdings.get("portfolio_id")) != portfolio_id:
        raise ValueError("组合持仓响应与请求范围不一致。")
    positions = []
    for row in holdings.get("rows", []):
        if row.get("quantity") == 0:
            continue
        core = row.get("instrument_core") or {}
        positions.append({"instrument_id": core.get("instrument_id"), "name": core.get("instrument_name"),
            "holding_id": row.get("derivative_contract_id") or row.get("position_reference_id"),
            "instrument_type": core.get("instrument_type"), "holding_kind": row.get("holding_kind"),
            "quantity": row.get("quantity"), "market_value_base": _number(row.get("market_value_base")),
            "currency": core.get("currency"), "quote_as_of_date": row.get("quote_as_of_date"),
            "cost_basis_base": _number(row.get("cost_basis_base")), "unrealized_pnl_base": _number(row.get("unrealized_pnl_base")),
            "unrealized_return_pct": _number(row.get("unrealized_return_base")) * 100 if _number(row.get("unrealized_return_base")) is not None else None,
            "holding_start_date": row.get("instrument_holding_start_date"),
            "risk_eligible": row.get("risk_eligible")})
    nav = _number((holdings.get("totals") or {}).get("nav"))
    for position in positions:
        value = position["market_value_base"]
        position["market_value_nav_pct"] = value / nav * 100 if value is not None and nav is not None and nav > 0 else None
    return {"available": True, "source_id": f"risk-holdings:{portfolio_id}", "portfolio_id": portfolio_id, "name": holdings.get("portfolio_name") or portfolio_id,
        "as_of_date": holdings.get("as_of_date"), "base_currency": holdings.get("base_currency"),
        "nav": nav, "positions": positions,
        "forward_risk": holdings.get("forward_risk"), "quality_warnings": holdings.get("quality_warnings", []),
        "risk_context": {key: value for key, value in context.items() if key != "workspace"},
        "exposure_note": "市值与市值占净值是持仓敞口描述，不是风险贡献；有正负持仓时净额可能抵消。"}


def _research_context(session, instrument_id, notebook):
    """Bind current team judgments, without hydrating the historical source corpus."""
    from studio_identity import current_principal
    from watchlist_app.api.routes.research import _serialize_note, _serialize_profile, research_repository
    from watchlist_app.services.research_identity import research_identity
    from watchlist_app.services.research_themes import theme_index
    from watchlist_app.services.sector_research import _research_market, RESEARCH_TIMEZONES

    actor = research_identity()
    inactive = {theme["theme_id"] for theme in theme_index(session, instrument_id, actor=actor)
                if theme["status"] != "active"}
    market = _research_market(session, instrument_id)
    zone = ZoneInfo(RESEARCH_TIMEZONES[market]) if market else None
    review_day = datetime.now(UTC).astimezone(zone).date() if zone else None
    records = []
    profile = research_repository.get_profile(session, instrument_id)
    if profile is not None:
        records.append({"kind": "pm_profile", "source_id": f"risk-pm-profile:{instrument_id}:{profile.revision_number}",
            "instrument_id": instrument_id, "value": _serialize_profile(profile),
            "note": "标的PM档案的完整当前版本，含当前观点、假设、风险、反证、期限和监测计划；保留研究阶段，不推定仍建议持有。primary_analyst是负责人、updated_by是维护人，均不等于原观点作者；此档案未保存独立作者归属，不能补推。"})
    for note in research_repository.list_notes(session, instrument_id):
        if (not current_principal().local_unrestricted and note.team_id != actor["team_id"]
                or note.completed_at is not None or (note.research_context or {}).get("theme_id") in inactive):
            continue
        value = _serialize_note(note)
        context = value.get("research_context") or {}
        if "sources" in context:
            # A PM note binds originals for provenance, but risk reads that PM's
            # judgment here; embedded financial tables and article bodies are not
            # a second risk evidence corpus or independently verified facts.
            reference_fields = {"source_id", "source_type", "title", "url", "instrument_id", "instrument_ids",
                "document_id", "version_id", "status", "content_hash", "body_sha256", "published_at", "occurred_at",
                "observed_at", "received_at", "retrieved_at", "discovered_at", "collected_at", "recorded_at",
                "as_of", "as_of_date", "information_cutoff", "run_cutoff", "source_run_id", "period_end", "statement_type", "period_type",
                "time_status", "verification_status", "provider", "symbol", "currency", "unit"}
            value["research_context"] = {**context,
                "sources": [{key: source[key] for key in reference_fields if key in source}
                            for source in context["sources"]],
                "sources_note": "这是PM原记录引用的来源索引；保留版本与原日期，不在此嵌入正文或财务数据，也不表示本轮已读取、核实这些原文。"}
        records.append({"kind": "pm_view", "source_id": f"risk-pm:{instrument_id}:{note.note_id}:{note.revision_number}",
            "instrument_id": instrument_id, "value": value,
            "note": "投资经理当前保存版本的原文与归属；未知作者保持未知，未由本轮重新核实。"})
    for field, kind in (("questions", "active_question"), ("forecasts", "forecast_check")):
        for item in notebook.get(field, []):
            if item.get("theme_id") in inactive:
                continue
            if field == "questions" and item.get("tracking_status", "active") != "active":
                continue
            if field == "forecasts" and item.get("status", "active") != "active":
                continue
            value = {key: value for key, value in item.items() if key not in {"versions", "sources"}}
            if field == "forecasts":
                scheduled = date.fromisoformat(str(item["review_on"])) if item.get("review_on") else None
                value["review_status"] = ("condition_based" if scheduled is None else "date_basis_unknown" if review_day is None
                                          else "due" if scheduled <= review_day else "scheduled")
            identity = item.get("version_id") or f"{item['key']}:{item.get('source_run_id') or notebook.get('source_run_id') or 'retained'}"
            records.append({"kind": kind, "source_id": f"risk-{kind}:{instrument_id}:{identity}",
                "instrument_id": instrument_id, "value": value,
                "note": "研究员保存的判断、反证和下一检查条件；到期只要求复核，不表示预测已经兑现或错误。"})
    return {"records": records,
        "counts": {kind: sum(row["kind"] == kind for row in records) for kind in ("pm_profile", "pm_view", "active_question", "forecast_check")},
        "note": "绑定标的PM档案当前版本、当前团队可见且未结束的PM观点及有效主题内继续跟踪的问题/预测；历史版本、已完成观点、暂停/结束问题和撤回预测不在当前清单。保留原文、已有归属、原日期与来源引用；这些是待检验判断，不是独立事实。预测复核日期按标的市场时区判断，市场未知时不推定到期。"}


def read_snapshot(session, **scope):
    from watchlist_app.api.routes.workbench import risk_workspace
    from watchlist_app.services.sector_research import review_states
    scope = normalize_scope(**scope)
    key, identifier = next(iter(scope.items()))
    kind = key.removesuffix("_id")
    limitations = []
    portfolio = None
    available = True
    if kind == "portfolio":
        try:
            portfolio = _portfolio_snapshot(identifier)
            derivatives = portfolio["risk_context"].get("derivatives", {}).get("positions", [])
            contract_ids = {row["holding_id"] for row in derivatives}
            shared_positions = [p for p in portfolio["positions"] if p["holding_id"] not in contract_ids
                                and p["holding_kind"] != "settled_cash" and p["instrument_type"] != "cash"]
            ids = sorted({p["instrument_id"] for p in shared_positions if p["instrument_id"]}
                         | {row["instrument_id"] for position in derivatives for row in position.get("underlyings", []) if row.get("instrument_id")})
            name = portfolio["name"]
            if any(not p["instrument_id"] for p in shared_positions):
                limitations.append("部分实际持仓未关联标的，尚不能与标的风险记录匹配。")
            limitations.extend(portfolio["risk_context"].get("limitations", []))
            if portfolio["forward_risk"] is None:
                limitations.append("组合接口未提供可用的聚合风险指标；没有根据市值权重推算风险贡献。")
        except (OSError, ValueError) as error:
            ids, name, available = [], identifier, False
            portfolio = {"available": False, "portfolio_id": identifier}
            limitations.append(f"当前组合持仓读取未完成，不能确认涉及敞口：{type(error).__name__}。")
    elif kind == "watchlist":
        record = session.get(Watchlist, identifier)
        if record is None:
            raise ValueError("所选观察列表不存在。")
        name = record.name
        ids = sorted(session.scalars(select(WatchlistItem.instrument_id).where(WatchlistItem.watchlist_id == identifier)))
        limitations.append("这是观察列表成员的风险汇总，不代表实际持仓，未计算组合权重或风险贡献。")
    else:
        record = session.get(InstrumentDetail, identifier)
        if record is None:
            raise ValueError("所选标的不存在。")
        ids, name = [identifier], record.instrument_name
    workspace = risk_workspace(instrument_ids=",".join(ids), session=session)
    instruments = sorted(workspace["instruments"], key=lambda item: item["instrument_id"])
    states = review_states(session, instrument_ids=[item["instrument_id"] for item in instruments])
    latest_research, completed_research = states["latest"], states["last_completed"]
    peer_scope = peer_context(session) if instruments else None
    for item in instruments:
        iid = item["instrument_id"]
        latest, completed = latest_research.get(iid), completed_research.get(iid)
        notebook = (completed or {}).get("current_research") or {}
        view = notebook.get("investment_view")
        item["research_tracking"] = {
            "latest_check": {key: latest.get(key) for key in (
                "run_id", "status", "checked_at", "coverage", "reflection")} if latest else None,
            "current_judgment": {"summary": view.get("direction", ""),
                "view_updated_at": view.get("updated_at"), "view_run_id": view.get("source_run_id"),
                "investment_view": {key: value for key, value in view.items() if key != "versions"}}
                if view else None,
            "note": "研究员已保存的判断与复核状态，供风险分析衔接；不是独立原始证据，也不表示本轮重新核实。资料覆盖不足本身不是投资风险，正常净值披露滞后不等于研究失败。",
        }
        item["research_context"] = _research_context(session, iid, notebook)
        status = (latest or {}).get("status")
        gap = {None: "尚无已留存的研究检查，研究覆盖尚未确认",
            "queued": "本次研究仍在等待，尚未完成新的检查",
            "running": "本次研究尚未完成，沿用此前已保存的判断",
            "failed": "最近一次研究未完成，不能视为已核实当前变化",
            "limited": "最近一次研究覆盖不足，不能视为已完成全面核实"}.get(status)
        if gap:
            limitations.append(f"{item['name']}：{gap}；当前研判仍可使用已留存数值与风险事项。")
        for coverage_note in (latest or {}).get("coverage", []):
            limitations.append(f"{item['name']}研究覆盖：{coverage_note}")
        risk = item.get("risk")
        if risk and risk.get("drawdown_summary"):
            summary = dict(risk["drawdown_summary"])
            if "max_duration_months" in summary:
                summary["longest_underwater_period_months"] = summary.pop("max_duration_months")
            summary["duration_note"] = "最长水下期是全样本统计，不是 peak_date 至 valley_date 的最大回撤区间时长，也不是当前回撤持续时间。"
            item["risk"] = {**risk, "drawdown_summary": summary}
        item["performance_evidence"] = performance_evidence(session, item["instrument_id"], peer_scope=peer_scope)
    cases = sorted((case for case in workspace["cases"] if case["trigger_active"]
                    and case["status"] not in {"handled", "resolved"}
                    and (case.get("evidence_json") or {}).get("direction") != "opportunity"
                    and case["severity"] in {"attention", "coverage"}), key=lambda item: item["case_id"])
    research = [case for case in cases if case["severity"] == "attention" and case["signal"] not in QUANTITATIVE_SIGNALS]
    quantitative = [case for case in cases if case["severity"] == "attention" and case["signal"] in QUANTITATIVE_SIGNALS]
    coverage = [case for case in cases if case["severity"] == "coverage"]
    missing = sorted(set(ids) - {item["instrument_id"] for item in instruments})
    if missing:
        limitations.append("以下范围内标的尚无已接入的风险资料：" + "、".join(missing))
    if not ids and available and kind != "portfolio":
        limitations.append("当前范围没有可汇总的标的；没有记录不能视为没有风险。")
    dates = [str(item["as_of_date"]) for item in instruments if item.get("as_of_date")]
    dates += [str(case["observed_on"]) for case in cases if case.get("observed_on")]
    if portfolio and portfolio.get("as_of_date"):
        dates.append(str(portfolio["as_of_date"]))
    if portfolio and portfolio.get("available"):
        affected = {case["instrument_id"] for case in research + quantitative}
        rows = [row for row in portfolio["positions"] if row["instrument_id"] in affected]
        amount = sum(row["market_value_base"] for row in rows) if all(row["market_value_base"] is not None for row in rows) else None
        nav = portfolio["nav"]
        portfolio.update(affected_instrument_ids=sorted(affected), affected_market_value_base=amount,
                         affected_nav_pct=amount / nav * 100 if amount is not None and nav is not None and nav > 0 else None,
                         affected_derivative_holding_ids=[row["holding_id"] for row in portfolio["risk_context"].get("derivatives", {}).get("positions", [])
                             if any(item.get("instrument_id") in affected for item in row.get("underlyings", []))],
                         affected_scope_note="仅统计现有风险事项关联的持仓；为零不代表没有业绩问题，应同时检查全部持仓的真实损益、回撤和相对表现。")
        if amount is None or nav is None or nav <= 0:
            limitations.append("相关持仓市值或有效净值不足，未计算涉及敞口占比。")
    return serialize_payload({"scope": {"kind": kind, "id": identifier, "name": name}, "scope_available": available,
        "instrument_ids": ids, "input_as_of": max(dates, default=None), "instruments": instruments,
        "research": research, "quantitative": quantitative, "coverage": coverage,
        "portfolio": portfolio, "limitations": limitations})


def _topic_id(scope):
    key, value = next(iter(normalize_scope(**scope).items()))
    return f"{TOPIC_PREFIX}{key.removesuffix('_id')}:{value}"


def begin_run(session, *, instrument_id=None, watchlist_id=None, portfolio_id=None, scheduled_dates=None):
    from watchlist_app.services.research_identity import research_identity
    from watchlist_app.services.research_access import require_portfolio, require_team_write
    if portfolio_id:
        require_portfolio(portfolio_id)
    else:
        require_team_write()
    scope = normalize_scope(instrument_id=instrument_id, watchlist_id=watchlist_id, portfolio_id=portfolio_id)
    topic_id = _topic_id(scope)
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy import text
        # A portfolio/watchlist scope has no shared instrument row to lock, and
        # its topic may not exist yet. Serialize its first creation and enqueue.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('watchlist-risk-officer'), hashtext(:scope))"),
                        {"scope": topic_id})
    topic = session.get(ResearchTopic, topic_id)
    if topic is None:
        topic = ResearchTopic(topic_id=topic_id, title="风险研判", question="综合当前范围的风险、关联与下一步", instrument_ids=[], portfolio_id=portfolio_id, visibility="team")
        session.add(topic)
        session.flush()
    session.refresh(topic, with_for_update=True)
    previous = session.scalar(select(ResearchEntry).where(ResearchEntry.topic_id == topic_id).order_by(ResearchEntry.created_at.desc()))
    now = datetime.now(UTC)
    if previous:
        if previous.status in {"queued", "running"}:
            return previous, False
        same_days = scheduled_dates and all((previous.context_json.get("research_dates") or {}).get(iid) == day
                                           for iid, day in scheduled_dates.items())
        if same_days and (previous.status == "failed" or
                (previous.status == "completed" and previous.context_json.get("risk_inputs") == read_snapshot(session, **scope))):
            return previous, False
    run = ResearchEntry(entry_id=uuid4().hex, topic_id=topic_id, kind="analysis", title="风险研判", source="已留存风险与组合持仓", status="queued", body="", created_at=now,
        context_json={"research_actor": research_identity(), "risk_run": True, "risk_scope": scope, "cutoff": now.isoformat(),
                      "scheduled": scheduled_dates is not None, "research_dates": scheduled_dates or {}})
    session.add(run)
    session.commit()
    return run, True


def prepare_run(run_id):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        if not run or not run.context_json.get("risk_run"):
            raise ValueError("风险研判记录不存在。")
        snapshot = read_snapshot(session, **run.context_json["risk_scope"])
        prior = session.scalar(select(ResearchEntry).where(ResearchEntry.topic_id == run.topic_id,
            ResearchEntry.status == "completed", ResearchEntry.entry_id != run.entry_id).order_by(ResearchEntry.created_at.desc()))
        topic = session.get(ResearchTopic, run.topic_id)
        topic.instrument_ids = snapshot["instrument_ids"]
        run.context_json = {**run.context_json, "risk_inputs": snapshot, "prepared_at": datetime.now(UTC).isoformat(),
            "prior_inputs": (prior.context_json or {}).get("risk_inputs") if prior else None,
            "catalogue": [{"instrument_id": item["instrument_id"], "name": item["name"]} for item in snapshot["instruments"]]}
        session.commit()
        if not snapshot["scope_available"]:
            raise ValueError("；".join(snapshot["limitations"]))


class Priority(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    analysis: str = Field(min_length=1, max_length=6000)
    instrument_ids: list[str] = Field(default_factory=list)
    holding_ids: list[str] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    next_watch: str = Field(min_length=1, max_length=2000)


class RiskReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=8000)
    priorities: list[Priority]
    limitations: list[str]


def validate_result(run, result: RiskReview):
    snapshot = run.context_json["risk_inputs"]
    ids = set(snapshot["instrument_ids"])
    cases = {case["case_id"]: case for key in ("research", "quantitative", "coverage") for case in snapshot[key]}
    sources = evidence_sources(snapshot)
    portfolio = snapshot.get("portfolio") or {}
    holdings = {row["holding_id"] for row in portfolio.get("risk_context", {}).get("holdings", [])}
    for priority in result.priorities:
        if (not set(priority.instrument_ids).issubset(ids) or not set(priority.case_ids).issubset(cases)
                or not set(priority.holding_ids).issubset(holdings)):
            raise ValueError("研判引用了当前范围快照以外的标的或风险事项。")
        if any(cases[case_id]["instrument_id"] not in priority.instrument_ids for case_id in priority.case_ids):
            raise ValueError("引用风险事项与研判涉及标的不一致。")
        if not priority.case_ids and not priority.source_ids:
            raise ValueError("独立研判需要引用本次真实业绩或持仓来源。")
        if not set(priority.source_ids).issubset(sources):
            raise ValueError("研判引用了当前范围快照以外的业绩或持仓来源。")
        if any(sources[source_id].get("instrument_id") and sources[source_id]["instrument_id"] not in priority.instrument_ids for source_id in priority.source_ids):
            raise ValueError("引用业绩来源与研判涉及标的不一致。")
        for source_id in priority.source_ids:
            source = sources[source_id]
            holding_ids = source.get("holding_ids", [])
            if source.get("holding_id"):
                holding_ids = [*holding_ids, source["holding_id"]]
            if not set(holding_ids).issubset(priority.holding_ids):
                raise ValueError("引用合约来源与研判涉及持仓不一致。")
        if not priority.instrument_ids and not priority.holding_ids:
            if snapshot["scope"]["kind"] != "portfolio" or not any(
                    sources[source_id].get("portfolio_id") == snapshot["scope"]["id"]
                    for source_id in priority.source_ids):
                raise ValueError("组合整体研判需要引用本组合的风险或持仓来源。")


def apply_result(session, run, payload):
    result = RiskReview.model_validate(payload)
    validate_result(run, result)
    snapshot = run.context_json["risk_inputs"]
    saved = result.model_dump()
    saved["limitations"] = list(dict.fromkeys([*snapshot["limitations"], *saved["limitations"]]))
    run.context_json = {**run.context_json, "result": saved}
    run.body = saved["summary"]
    run.status = "completed"
    run.completed_at = datetime.now(UTC)


def evidence_sources(snapshot):
    sources = {}
    for item in snapshot["instruments"]:
        for record in (item.get("research_context") or {}).get("records", []):
            value = record["value"]
            sources[record["source_id"]] = {"source_type": record["kind"], "instrument_id": item["instrument_id"],
                "title": "标的PM档案" if record["kind"] == "pm_profile" else value.get("title") or value.get("question") or value.get("claim"),
                "author": value.get("author") if record["kind"] in {"pm_profile", "pm_view"} else "研究员",
                "author_user_id": value.get("author_user_id"), "recorded_at": value.get("updated_at") or value.get("created_at"),
                **({"primary_analyst": value.get("primary_analyst"), "updated_by": value.get("updated_by")}
                   if record["kind"] == "pm_profile" else {}),
                "note_id": value.get("note_id"), "revision_number": value.get("revision_number"),
                "version_id": value.get("version_id"), "verification_status": "retained_judgment_not_independent_fact"}
        evidence = item.get("performance_evidence")
        if not evidence:
            continue
        sources[evidence["source_id"]] = {"title": f"{item['name']} · 已留存业绩", "instrument_id": item["instrument_id"],
            "start_date": evidence["sample_start"], "end_date": evidence["sample_end"], "currency": evidence["currency"], "frequency": evidence["frequency"]}
        for comparison in evidence["comparisons"]:
            sources[comparison["source_id"]] = {"title": f"{item['name']} / {comparison['name']} · 已配置对照", "instrument_id": item["instrument_id"],
                "start_date": comparison["comparison"]["sample_start"], "end_date": comparison["comparison"]["sample_end"],
                "currency": comparison["comparison"].get("currency"), "frequency": comparison["frequency"]}
    portfolio = snapshot.get("portfolio")
    if portfolio and portfolio.get("source_id"):
        sources[portfolio["source_id"]] = {"title": f"{portfolio['name']} · 实际持仓与损益", "end_date": portfolio["as_of_date"], "currency": portfolio["base_currency"],
            "portfolio_id": portfolio["portfolio_id"], "detail_path": f"/portfolios/{quote(portfolio['portfolio_id'], safe='')}/holdings"}
        for source in portfolio.get("risk_context", {}).get("sources", []):
            sources[source["source_id"]] = source
    return sources


def review_workspace(session, **scope):
    from watchlist_app.services.research_runner import harness_available
    if scope.get("portfolio_id"):
        from watchlist_app.services.research_access import require_portfolio
        require_portfolio(scope["portfolio_id"])
    snapshot = read_snapshot(session, **scope)
    runs = list(session.scalars(select(ResearchEntry).where(ResearchEntry.topic_id == _topic_id(scope)).order_by(ResearchEntry.created_at.desc())))
    latest = runs[0] if runs else None
    completed = next((run for run in runs if run.status == "completed" and (run.context_json or {}).get("result")), None)
    saved = None
    if completed:
        old_inputs = completed.context_json["risk_inputs"]
        saved = {**completed.context_json["result"], "run_id": completed.entry_id, "status": completed.status,
            "completed_at": completed.completed_at, "input_as_of": old_inputs["input_as_of"], "stale": old_inputs != snapshot,
            "evidence_sources": evidence_sources(old_inputs)}
        if completed.context_json.get("review_corrections"):
            saved["review_note"] = "本次内容经 Codex 依据留存数据复核修订，原模型稿及修订原因已保留。"
    return serialize_payload({"available": harness_available() and snapshot["scope_available"], "scope": snapshot["scope"],
        "input_as_of": snapshot["input_as_of"], "counts": {key: len(snapshot[key]) for key in ("research", "quantitative", "coverage")},
        "instruments": [{key: item.get(key) for key in ("instrument_id", "name", "as_of_date")} for item in snapshot["instruments"]],
        "holdings": ((snapshot.get("portfolio") or {}).get("risk_context") or {}).get("holdings", []),
        "limitations": snapshot["limitations"], "latest_completed": saved,
        "latest_run": {"run_id": latest.entry_id, "status": latest.status, "created_at": latest.created_at,
                       "completed_at": latest.completed_at, "message": latest.body} if latest else None})
