"""Source-bound event reviews and daily sector checks in the existing workbench."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
import logging
import json
import re
from threading import Event, Thread
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator
from typing import Literal
from sqlalchemy import func, select
from investment_studio_instrument_core.db_models import Instrument
from investment_studio_instrument_core.listing_contract import (
    MARKET_SCOPE_CALENDARS, MARKET_SCOPE_TIMEZONES, market_scope_for_calendar,
)

from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail, WatchlistItem, Watchlist
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.sector_market_data import read_sector_market_data
from watchlist_app.services.sector_estimates import read_estimate_evidence, retained_estimate_sources, usable_estimate_change
from watchlist_app.services.research_notebook import ResearchNotebook, research_sources, retain_notebook, validate_notebook
from watchlist_app.services.calculation_frequency import _market_calendar_sessions

TOPIC_ID = "us-sector-daily-review"
INSTRUMENT_TOPIC_PREFIX = "instrument-events:"
EVENT_INSTRUMENT_TYPES = ("equity", "etf", "index", "public_fund", "private_fund")
SECTORS = {
    "XLB": ("原材料", ["拆分矿业、化工和包装的供需、库存与定价能力。", "原料价格变化对售价、成本和利润的影响可能相反。"]),
    "XLC": ("通信服务", ["区分广告平台、内容娱乐和电信公司的经营驱动。", "关注广告需求、用户变现、内容投入、资本开支与监管变化。"]),
    "XLE": ("能源", ["连接原油与天然气供需、库存、政策和运输中断。", "区分生产商、炼化与油服的利润传导，结合资本开支和现金回报。"]),
    "XLF": ("金融", ["分别分析银行净息差与信用损失、保险承保和资管资金流。", "关注融资条件、存款成本、监管和资本回报，避免将所有金融公司视为银行。"]),
    "XLI": ("工业", ["跟踪订单、资本开支、交付、积压订单兑现和利润率。", "区分航空航天、国防、运输与通用工业的周期和政策暴露。"]),
    "XLK": ("信息技术", ["连接AI投入、供应商收入、客户现金流和投资回报。", "区分半导体、硬件、软件的预期、竞争与政策约束；需求验证公司未必是ETF成分股。"]),
    "XLP": ("必需消费", ["拆分销量、价格、产品组合、原料成本和渠道库存。", "关注消费者降级、零售竞争、外汇与防御性估值是否已充分反映。"]),
    "XLRE": ("房地产", ["按物业类型分析入住率、租金、租约、净经营收入与再融资。", "REIT普通EPS可失真；缺少FFO/AFFO与债务到期数据时明确证据缺口。"]),
    "XLU": ("公用事业", ["连接电力需求、资本开支、监管准许回报与融资需求。", "分析燃料成本传导、利率、信用与项目执行；负荷增长未必立即增加股东回报。"]),
    "XLV": ("医疗保健", ["区分制药、器械、医疗服务和支付方。", "关注试验与审批、专利、支付政策、医疗利用率及成本，不将单一药物事件外推全行业。"]),
    "XLY": ("可选消费", ["连接实际收入、信用、促销、库存和消费者可选支出。", "拆分零售、汽车、旅游和平台业务；结合融资、竞争与资本回报判断方向。"]),
}


class ReviewInProgress(ValueError):
    pass


class ResearchVersionConflict(ValueError):
    pass


def scoped_ids(session, instrument_id=None, watchlist_id=None):
    query = select(InstrumentDetail.instrument_id).where(InstrumentDetail.is_active.is_(True))
    if instrument_id:
        query = query.where(InstrumentDetail.instrument_id == instrument_id,
                            InstrumentDetail.instrument_type.in_(EVENT_INSTRUMENT_TYPES))
    else:
        # The list-level endpoint remains the eleven US sector ETFs.
        query = query.where(InstrumentDetail.instrument_type == "etf",
                            InstrumentDetail.instrument_id.in_([s.lower() for s in SECTORS]))
    if watchlist_id:
        query = query.where(InstrumentDetail.instrument_id.in_(
            select(WatchlistItem.instrument_id).where(WatchlistItem.watchlist_id == watchlist_id)))
    return sorted(session.scalars(query))


def instrument_label(session, iid):
    if iid.upper() in SECTORS:
        return SECTORS[iid.upper()][0]
    return session.get(InstrumentDetail, iid).instrument_name


def latest_reviews(session, *, completed_only=False):
    from studio_identity import current_principal
    from watchlist_app.services.research_access import topic_portfolio_ids
    result, current_views = {}, {}
    principal = current_principal()
    team_id = principal.team_id
    runs = session.scalars(select(ResearchEntry).where(ResearchEntry.kind == "analysis", True if principal.local_unrestricted else ResearchEntry.team_id == team_id)
                          .order_by(func.coalesce(ResearchEntry.completed_at, ResearchEntry.created_at).desc()))
    for run in runs:
        context = run.context_json or {}
        topic = session.get(ResearchTopic, run.topic_id)
        if not topic or (not principal.local_unrestricted and topic.team_id != team_id) or topic_portfolio_ids(session, topic):
            continue
        if not (context.get("sector_run") or context.get("research_run")):
            continue
        published = run.status in {"completed", "draft"}
        for iid in context.get("instrument_ids", []):
            review = context.get("reviews", {}).get(iid, {})
            accepted = published and review.get("status") in {"completed", "limited"}
            # A conversation is not a daily check until it actually publishes research.
            if not context.get("sector_run") and not accepted:
                continue
            if accepted and iid not in current_views and review.get("summary"):
                current_views[iid] = {"summary": review["summary"],
                    "view_updated_at": review.get("view_updated_at", context.get("cutoff")),
                    "view_run_id": review.get("view_run_id", run.entry_id)}
            if iid in result or (completed_only and not accepted):
                continue
            result[iid] = {"run_id": run.entry_id, "status": review.get("status", run.status),
                "checked_at": context.get("cutoff"), "summary": review.get("summary", run.body if run.status == "failed" else ""),
                "view_updated_at": review.get("view_updated_at"),
                "change_kind": review.get("change_kind", "investment" if review.get("summary") else "none"),
                "coverage": review.get("coverage", []), "research": review.get("research")}
    for iid, review in result.items():
        view = current_views.get(iid, {})
        review["current_summary"] = view.get("summary", "")
        if review["status"] != "failed":
            review["summary"] = view.get("summary", "")
        review["view_updated_at"] = view.get("view_updated_at")
        review["view_run_id"] = view.get("view_run_id")
    return result


def _future(rows, today):
    return [r for r in rows if r["target_period_end"] >= today and
            (r.get("revenue_avg") is not None or r.get("eps_avg") is not None)]


def sector_snapshot(iid, session, *, as_of=None):
    raw = read_sector_market_data(session, iid.upper(), as_of=as_of)
    if raw is None:
        return None
    today = (as_of or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    stocks = [h for h in raw["holdings"] if h["holding_type"] == "equity"]
    gap_labels = {"no_forward_quarter_estimates": "缺少未来季度预期", "no_forward_annual_estimates": "缺少未来年度预期",
                  "missing_price": "缺少行情", "missing_holdings": "缺少持仓", "missing_etf_info": "缺少ETF资料",
                  "unclassified_holding": "持仓类型待核对"}
    view = {"instrument_id": iid, "ticker": iid.upper(), "sector_name": SECTORS[iid.upper()][0],
        "price_as_of": (raw["etf"]["latest_price"] or {}).get("date"),
        "holdings_as_of": max((str(h.get("as_of_date") or "") for h in raw["holdings"]), default="") or None,
        "holdings_observed_on": max((str(h.get("snapshot_date") or "") for h in raw["holdings"]), default="") or None,
        "holdings_date_note": "holdings_as_of仅表示原始资料明确披露的持仓日期；holdings_observed_on是本项目采集观察日，不是持仓报告期或首次披露日期。",
        "stock_count": len(stocks), "stock_weight_pct": sum(h.get("weight_percent") or 0 for h in stocks),
        "annual_estimate_count": sum(bool(_future(h["annual_estimates"], today)) for h in stocks),
        "quarterly_estimate_count": sum(bool(_future(h["quarterly_estimates"], today)) for h in stocks),
        "top_holdings": [{"symbol": h["holding_symbol"], "name": h["holding_name"], "weight_percent": h["weight_percent"]} for h in stocks[:10]],
        "research_focus": SECTORS[iid.upper()][1],
        "data_gaps": [f"{g.get('symbol', '')}：{gap_labels.get(g['kind'], g['kind'])}" for g in raw["gaps"]]}
    companies = {}
    for h in stocks:
        p = h["company_profile"] or {}
        fields = ("estimate_period", "target_period_end", "revenue_avg", "eps_avg", "num_analysts_revenue", "num_analysts_eps",
                  "collected_at", "source_dataset", "raw_sha256", "historical_use", "currency", "currency_status")
        companies[h["holding_symbol"]] = {"symbol": h["holding_symbol"], "name": h["holding_name"], "weight_percent": h["weight_percent"],
            "industry": p.get("industry"), "description": p.get("description"), "profile_collected_at": p.get("collected_at"),
            "reporting_currency": p.get("reporting_currency"), "reporting_currency_source": p.get("reporting_currency_source"),
            "quote_currency": p.get("currency"), "holdings_collected_at": h.get("collected_at"),
            "annual_estimates": [{k:r.get(k) for k in fields} for r in h["annual_estimates"]],
            "quarterly_estimates": [{k:r.get(k) for k in fields} for r in h["quarterly_estimates"]],
            "latest_price": {k:(h["latest_price"] or {}).get(k) for k in ("date", "close", "adjusted_close")}}
    evidence = {**view, "source": raw["source"], "dataset_status": raw["dataset_status"],
        "holdings": [{k:h.get(k) for k in ("holding_symbol", "holding_name", "holding_type", "weight_percent", "as_of_date", "snapshot_date", "provider_updated_at", "collected_at")} for h in raw["holdings"]],
        "leading_companies": [{**companies[h["holding_symbol"]],
            "annual_estimates": _future(companies[h["holding_symbol"]]["annual_estimates"], today)[:2],
            "quarterly_estimates": _future(companies[h["holding_symbol"]]["quarterly_estimates"], today)[:2]} for h in stocks[:5]]}
    return view, evidence, companies


def begin_run(session, ids, *, scheduled=False):
    from watchlist_app.services.research_identity import research_identity
    ids = list(dict.fromkeys(ids))
    if any((asset := session.get(InstrumentDetail, iid)) is not None
           and asset.instrument_type in {"public_fund", "private_fund"} for iid in ids):
        raise ValueError("普通公募和私募暂缓主动深研，已有档案与研究记录仍可查阅。")
    sector_scope = bool(ids) and set(ids).issubset(scoped_ids(session))
    if not sector_scope and (len(ids) != 1 or not scoped_ids(session, instrument_id=ids[0])):
        raise ValueError("请选择一个已登记的股票、基金、ETF或指数；批量每日检查仅支持美股行业ETF")
    # Batch and single-instrument jobs publish into the same dossiers and cases.
    # Lock the shared instrument rows before checking either kind of active job.
    list(session.scalars(select(InstrumentDetail).where(InstrumentDetail.instrument_id.in_(ids))
                        .order_by(InstrumentDetail.instrument_id).with_for_update()))
    for active in session.scalars(select(ResearchEntry).where(
            ResearchEntry.kind == "analysis", ResearchEntry.status.in_(["queued", "running"]))):
        context = active.context_json or {}
        if context.get("sector_run") and set(ids).intersection(context.get("instrument_ids", [])):
            if set(ids).issubset(context["instrument_ids"]):
                return active, False
            raise ReviewInProgress("部分标的的研究追踪正在运行，请完成后再检查当前范围")
    topic_id = TOPIC_ID if len(ids) > 1 else f"{INSTRUMENT_TOPIC_PREFIX}{ids[0]}"
    title = "美股行业ETF每日观察" if len(ids) > 1 else f"{instrument_label(session, ids[0])} · 研究追踪"
    topic = session.get(ResearchTopic, topic_id)
    if topic is None:
        topic = ResearchTopic(topic_id=topic_id, title=title, question="风险、机会与重要不确定性", instrument_ids=ids, status="active", conclusion="", visibility="team")
        session.add(topic)
        session.flush()
    session.refresh(topic, with_for_update=True)
    latest = session.scalar(select(ResearchEntry).where(ResearchEntry.topic_id == topic_id).order_by(ResearchEntry.created_at.desc()))
    if latest and latest.status in {"queued", "running"}:
        if not scheduled and not set(ids).issubset(latest.context_json.get("instrument_ids", [])):
            raise ReviewInProgress("其他行业检查正在运行，请稍后再检查当前范围")
        return latest, False
    cutoff = datetime.now(UTC)
    incremental_trigger = {}
    if scheduled:
        research_dates = _research_dates(session, ids, cutoff)
        # Existing batch checks still count for each sector's daily attempt.
        daily_topics = [topic_id, TOPIC_ID] if sector_scope else [topic_id]
        for prior in session.scalars(select(ResearchEntry).where(ResearchEntry.topic_id.in_(daily_topics)).order_by(ResearchEntry.created_at.desc())):
            if not prior.context_json.get("sector_run"):
                continue
            checked = datetime.fromisoformat(prior.context_json["cutoff"])
            if _research_dates(session, ids, checked) != research_dates:
                continue
            if set(ids).issubset(prior.context_json.get("instrument_ids", [])):
                from watchlist_app.services.research_triggers import research_trigger
                from watchlist_app.services.research_dossier import read_dossier
                # A later conversation may have added a forecast or observation date.
                trigger_context = {**prior.context_json, "reviews": {},
                    "research_dossiers": [read_dossier(session, iid) for iid in ids]}
                incremental_trigger = {iid: trigger for iid in ids if (
                    trigger := research_trigger(session, iid, trigger_context, now=cutoff))}
                if not incremental_trigger:
                    return prior, False
                break
    run = ResearchEntry(entry_id=uuid4().hex, topic_id=topic_id, kind="analysis", title="行业ETF每日检查" if sector_scope else title,
        body="", source="Investment Studio 标的资料 / DeepSeek", created_at=cutoff,
        status="queued", context_json={"sector_run": True, "event_scope": "daily_sector" if sector_scope else "instrument",
        "instrument_ids": ids, "cutoff": cutoff.isoformat(), "scheduled": scheduled,
        "research_actor": research_identity(),
        "incremental_trigger": incremental_trigger,
        "web_evidence": [], "reviews": {}})
    session.add(run)
    session.commit()
    return run, True


def bind_research_instruments(session, run, ids):
    """Both entrances retain the same instrument inputs, dossier versions and originals."""
    from watchlist_app.services.research_workbench import instrument_evidence
    from watchlist_app.services.research_dossier import ensure_mandate, read_dossier
    from watchlist_app.services.research_identity import run_identity
    context = dict(run.context_json)
    known = {row["instrument_id"] for row in context.get("catalogue", [])}
    if not set(ids).issubset(known):
        raise ValueError("请使用本轮目录中的标的编码")
    bound = {row["instrument_id"] for row in context.get("instrument_inputs", [])}
    added = [iid for iid in dict.fromkeys(ids) if iid not in bound]
    if not added:
        return
    cutoff = datetime.now(UTC)
    assets = instrument_evidence(session, added, include_dossier=False, as_of=cutoff)["assets"]
    inputs = list(context.get("sector_inputs", []))
    company_data = dict(context.get("sector_company_data", {}))
    dossiers = list(context.get("research_dossiers", []))
    estimates = list(context.get("sector_estimate_evidence", []))
    gaps = list(context.get("data_gaps", []))
    for asset in assets:
        iid = asset["instrument_id"]
        asset["source_id"] = f"instrument:{run.entry_id}:{iid}"
        asset["snapshot_cutoff"] = cutoff.isoformat()
        ensure_mandate(session, iid)
        dossiers.append(read_dossier(session, iid, actor=run_identity(context)))
        if iid.upper() in SECTORS:
            snapshot = sector_snapshot(iid, session, as_of=cutoff)
            if snapshot is None:
                gaps.append(f"{iid.upper()}尚无本项目留存的成分公司与分析师预期快照。")
            else:
                _, evidence, companies = snapshot
                inputs.append({**evidence, "source_id": f"sector:{run.entry_id}:{iid}", "snapshot_cutoff": cutoff.isoformat()})
                company_data[iid] = companies
                estimates.append(read_estimate_evidence(session, iid, as_of=cutoff))
    prior = session.scalars(select(RiskCase).where(RiskCase.instrument_id.in_(added), RiskCase.signal.like("sector:%")))
    context.update(instrument_inputs=[*context.get("instrument_inputs", []), *assets],
        sector_inputs=inputs, sector_company_data=company_data, research_dossiers=dossiers,
        sector_estimate_evidence=estimates, data_gaps=gaps,
        prior_events=[*context.get("prior_events", []), *[{**event_record(c), "evidence": c.evidence_json} for c in prior]])
    # Automatic runs retain their requested publication scope; comparisons may read peers.
    if not context.get("sector_run"):
        context["instrument_ids"] = list(dict.fromkeys([*context.get("instrument_ids", []), *added]))
    # A first read can occur later in a conversation. Its inputs retain their own clock;
    # the run's knowledge horizon advances without relabeling earlier snapshots.
    context["cutoff"] = datetime.now(UTC).isoformat()
    run.context_json = context
    session.flush()


def prepare_run(run_id):
    from watchlist_app.services.research_workbench import catalogue
    from watchlist_app.services.market_evidence import text_store
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        context = dict(run.context_json)
        cutoff = datetime.now(UTC).isoformat()
        context.update(research_run=True, run_id=run_id, cutoff=cutoff, input_snapshot_cutoff=cutoff,
            catalogue=catalogue(session), instrument_inputs=[], research_dossiers=[],
            sector_inputs=[], sector_company_data={}, sector_estimate_evidence=[], prior_events=[],
            tool_evidence=[], web_evidence=[], market_text_sources=[], market_queries=[],
            market_coverage=text_store().get_coverage())
        run.context_json = context
        bind_research_instruments(session, run, context.get("instrument_ids", []))
        session.commit()


class SectorEvent(BaseModel):
    event_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    action: Literal["new", "updated", "resolved"]
    direction: Literal["risk", "opportunity", "uncertain"]
    title: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=1, max_length=5000)
    next_watch: str = Field(min_length=1, max_length=1500)
    confidence: Literal["confirmed", "reported", "unverified"]
    information_type: Literal["fact", "opinion", "rumor"]
    recording_type: Literal["new", "update", "backfill"]
    published_at: str | None = None
    occurred_at: str | None = None
    source_ids: list[str] = Field(min_length=1)

    @field_validator("published_at", "occurred_at")
    @classmethod
    def retain_time_precision(cls, value):
        if value is None:
            return None
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("具体时刻必须包含时区；只有日期时保留日期，未知时返回null")
        return parsed.astimezone(UTC).isoformat()


class SectorReview(BaseModel):
    instrument_id: str
    summary: str = Field(default="", max_length=2000)
    change_kind: Literal["none", "knowledge", "investment"] = "none"
    coverage: list[str] = Field(default_factory=list)
    events: list[SectorEvent] = Field(default_factory=list)
    research: ResearchNotebook | None = None


class ReviewResult(BaseModel):
    reviews: list[SectorReview]


def draft_payload(result: ReviewResult):
    """Keep research deltas sparse: omitted fields retain the existing knowledge."""
    payload = result.model_dump(mode="json")
    for model, row in zip(result.reviews, payload["reviews"]):
        if model.research is not None:
            row["research"] = model.research.model_dump(mode="json", exclude_unset=True)
    return payload


def usable_computed(source: dict, cutoff: datetime, iid: str) -> bool:
    """Only retained application calculations establish a numeric observation, not a cause."""
    if source.get("source_type") != "computed_metric" or source.get("scope") != "public_market":
        return False
    if source.get("instrument_id") not in {None, iid} or not source.get("as_of"):
        return False
    observed = datetime.fromisoformat(source["as_of"].replace("Z", "+00:00"))
    data = source.get("data") or {}
    return observed <= cutoff and (data.get("status") == "available" or any(
        row.get("observations", 0) > 1 for row in data.get("series", [])) or bool(data.get("rows")))


def usable_original(source: dict, cutoff: datetime) -> bool:
    text = source.get("text")
    if not isinstance(text, str) or not text.strip() or source.get("time_status") == "future":
        return False
    published = source.get("published_at")
    if published:
        published = SectorEvent.retain_time_precision(published)
        if (date.fromisoformat(published) > cutoff.date() if len(published) == 10
                else datetime.fromisoformat(published) > cutoff):
            return False
    # An explicit opening compiler byline dates the digest, not its underlying facts.
    opening = [line.strip() for line in text.splitlines() if line.strip()][:5]
    return not any(re.search(r"(?:^|[·|])\s*Compiled by [^\n·|]*\bEngine\b", line, re.I) for line in opening)


def _source_views(sources):
    fields = ("source_id", "document_id", "version_id", "url", "title", "source_type", "as_of", "published_at", "published_at_raw",
              "occurred_at", "observed_at", "received_at", "retrieved_at", "discovered_at", "time_status")
    return [{**{key: source.get(key) for key in fields}, **({"source_type": source["source_type"],
                "changes": source["changes"], "current_snapshot": source["current_snapshot"],
                "previous_snapshot": source["previous_snapshot"]} if source.get("source_type") == "analyst_estimate_changes" else {}),
             **({"measurement": {key: source.get("data", {}).get(key) for key in
                    ("current", "previous", "change_pp", "five_session_change_pp", "historical_reference")},
                 "methodology": source.get("methodology")} if source.get("source_type") == "computed_metric" else {})}
            for source in sources]


def _snapshot_view(snapshot):
    return {**snapshot, "sources": _source_views(snapshot.get("sources") or []), "coverage": snapshot.get("coverage") or []}


def event_record(case):
    evidence = case.evidence_json or {}
    history = [{**entry, "snapshot": _snapshot_view(entry["snapshot"]) if entry.get("snapshot") else None}
               for entry in case.history_json or []]
    return {"case_id": case.case_id, "instrument_id": case.instrument_id,
        "event_key": case.signal.removeprefix("sector:"), "title": case.title, "body": case.body,
        "direction": evidence.get("direction", "uncertain"), "information_type": evidence.get("information_type"),
        "confidence": evidence.get("confidence"), "next_watch": evidence.get("next_watch", ""),
        "status": case.status, "trigger_active": case.trigger_active,
        "withdrawn": bool(evidence.get("withdrawn")), "withdrawal_reason": evidence.get("withdrawal_reason"),
        "withdrawn_at": evidence.get("withdrawn_at"),
        "published_at": evidence.get("published_at"), "occurred_at": evidence.get("occurred_at"),
        "discovered_at": evidence.get("discovered_at") or (case.created_at.isoformat() if case.created_at else None),
        "updated_at": evidence.get("progress_at") or (case.updated_at.isoformat() if case.updated_at else None),
        "recording_type": evidence.get("recording_type"),
        "sources": _source_views(evidence.get("sources") or []), "coverage": evidence.get("coverage") or [],
        "history": history}


def events_for_instruments(session, ids):
    cases = session.scalars(select(RiskCase).where(RiskCase.instrument_id.in_(ids), RiskCase.signal.like("sector:%"))
                           .order_by(RiskCase.updated_at.desc()))
    return [event_record(case) for case in cases]


def _same_progress(case, item, sources):
    evidence = case.evidence_json or {}
    fields = ("direction", "information_type", "confidence", "published_at", "occurred_at")
    # Fetch IDs and collection times change every run; the cited publication does not.
    def identities(rows):
        publications = {(row.get("document_id") or row.get("url"), row.get("version_id") or row.get("published_at"))
                        for row in rows if row.get("url") or row.get("document_id")}
        estimate_points = {("estimate", change["symbol"], change["frequency"], change["target_period_end"],
            change["metric"], change["currency"], change["previous_value"], change["current_value"],
            change["previous_collected_at"], change["current_collected_at"])
            for row in rows if row.get("source_type") == "analyst_estimate_changes" for change in row["changes"]}
        def observations(value):
            if isinstance(value, list):
                return [observations(item) for item in value]
            if isinstance(value, dict):
                return {key: observations(item) for key, item in value.items() if key not in {
                    "source_id", "batch_id", "observed_at", "available_at", "availability_precision", "input_points", "limitations"}}
            return value
        computed = {("computed", row.get("instrument_id"),
                     json.dumps(observations(row.get("data", {})), sort_keys=True, ensure_ascii=False))
                    for row in rows if row.get("source_type") == "computed_metric"}
        return publications | estimate_points | computed
    previous = [{**evidence, "body": case.body},
                *(entry["snapshot"] for entry in case.history_json or [] if entry.get("snapshot"))]
    return any(" ".join(row["body"].split()) == " ".join(item.body.split())
               and all(row.get(key) == getattr(item, key) for key in fields)
               and identities(row.get("sources", [])) == identities(sources) for row in previous)


def validate_result(session, run, parsed: ReviewResult):
    """Check the full draft against retained evidence without publishing research or events."""
    context = run.context_json
    from studio_identity import current_principal
    principal = current_principal()
    topic = session.get(ResearchTopic, run.topic_id)
    from watchlist_app.services.research_access import require_team_publication_scope
    require_team_publication_scope(session, run)
    if not principal.local_unrestricted and principal.kind == "user" and principal.team_role == "reader":
        raise ValueError("只读成员可以个人讨论，不能发布团队研究")
    requested = [r.instrument_id for r in parsed.reviews]
    if topic and topic.visibility == "private" and not set(requested).issubset(context.get("team_publication_instructions", {})):
        raise ValueError("个人对话不会自动发布团队研究；请先取得当前用户明确的保存指令")
    scope = set(context["instrument_ids"])
    if len(requested) != len(set(requested)) or not set(requested).issubset(scope):
        raise ValueError("研究更新重复或超出本轮绑定的标的范围")
    if context.get("sector_run") and set(requested) != scope:
        raise ValueError("事件检查结果未覆盖本轮全部标的")
    sources = {s["source_id"]: s for evidence in context.get("web_evidence", []) for s in evidence.get("sources", [])}
    for iid, companies in context.get("sector_company_data", {}).items():
        for symbol, company in companies.items():
            sources[f"fmp:{run.entry_id}:{iid}:{symbol}"] = {"source_id": f"fmp:{run.entry_id}:{iid}:{symbol}",
                "title": f"FMP · {symbol} 公司资料与分析师预期", "time_status": "background", "company": company}
    sources.update(retained_estimate_sources(context))
    notebook_evidence = research_sources(context, run.entry_id)
    sources.update({sid: source for sid, source in notebook_evidence.items() if source.get("source_type") in {"public_source", "computed_metric"}})
    cutoff = datetime.fromisoformat(context["cutoff"])
    seen = set()
    # Validate the full reply before changing any persistent event.
    for review in parsed.reviews:
        if review.research is not None:
            validate_notebook(review.research, review.instrument_id, notebook_evidence)
            dossier = next((d for d in context.get("research_dossiers", []) if d["instrument_id"] == review.instrument_id), {})
            themes = {item["theme_id"]: item for item in dossier.get("themes", [])}
            notes = {item["note_id"]: item for item in dossier.get("pm_views", [])}
            for question in review.research.questions:
                if question.theme_id and question.theme_id not in themes:
                    raise ValueError("研究判断关联了本轮未读取的关注主题")
                if context.get("sector_run") and question.theme_id and themes[question.theme_id]["status"] != "active":
                    raise ValueError("已暂停或结束的关注主题不再自动更新")
                if question.pm_note_id:
                    note = notes.get(question.pm_note_id)
                    revisions = {item["revision_number"] for item in (note or {}).get("versions", [])}
                    if not note or question.pm_note_revision not in revisions:
                        raise ValueError("请关联本轮已读取的投资经理观点及其原始版本")
                    note_theme = (note.get("research_context") or {}).get("theme_id")
                    if question.theme_id and note_theme != question.theme_id:
                        raise ValueError("研究判断的主题与投资经理原观点不一致")
                    if context.get("sector_run") and note_theme and themes.get(note_theme, {}).get("status") != "active":
                        raise ValueError("已暂停或结束主题下的观点不再自动复核")
        for item in review.events:
            key = (review.instrument_id, item.event_key)
            if key in seen:
                raise ValueError("同一事件在本轮重复出现")
            seen.add(key)
            if any(s not in sources for s in item.source_ids):
                raise ValueError("事件引用了未取得的来源")
            originals = [sources[s] for s in item.source_ids if usable_original(sources[s], cutoff)]
            estimate_changes = [sources[s] for s in item.source_ids if usable_estimate_change(sources[s], cutoff, review.instrument_id)]
            computed = [sources[s] for s in item.source_ids if usable_computed(sources[s], cutoff, review.instrument_id)]
            if not originals and not estimate_changes and not computed:
                raise ValueError("事件缺少截至检查时可核对的原文或可比较预期变动；未来首发或自动汇编不能独立支撑事件")
            if item.published_at is not None and item.published_at not in {
                SectorEvent.retain_time_precision(source.get("published_at")) for source in originals
            }:
                raise ValueError("事件发布时间必须来自已引用原文，不得以收录时间代替")
            if not originals and item.occurred_at is not None:
                raise ValueError("预期快照只能证明两次采集之间发生变化，发生时间必须留空")
            if item.action != "new" and session.scalar(select(RiskCase).where(
                RiskCase.instrument_id == review.instrument_id, RiskCase.signal == f"sector:{item.event_key}"
            )) is None:
                raise ValueError("更新或解除的事件没有原跟进记录")
    return sources, notebook_evidence


def apply_result(session, run, reply):
    # The independent reviewer emits one complete structured document.
    parsed = ReviewResult.model_validate_json(reply)
    sources, notebook_evidence = validate_result(session, run, parsed)
    context = run.context_json
    # Both entrances can update the same instrument while a model is running.
    # Publish only against the versions actually studied; preserve a stale draft for review.
    from watchlist_app.services.research_dossier import read_dossier
    ids = [review.instrument_id for review in parsed.reviews]
    list(session.scalars(select(InstrumentDetail).where(InstrumentDetail.instrument_id.in_(ids))
                        .order_by(InstrumentDetail.instrument_id).with_for_update()))
    for review in parsed.reviews:
        if review.research is None and not review.events and review.change_kind != "investment":
            continue
        bound = next((d for d in context.get("research_dossiers", []) if d["instrument_id"] == review.instrument_id), {})
        current = read_dossier(session, review.instrument_id)
        for key in ("notebook", "mandate"):
            before = bound.get(key) or {}
            after = current.get(key) or {}
            if before.get("version_id", before.get("run_id")) != after.get("version_id", after.get("run_id")):
                raise ResearchVersionConflict("研究记录在本轮分析期间已有更新，本轮草稿已保留；请基于最新版本继续研究。")
    previous_reviews = latest_reviews(session, completed_only=True)
    acquisition_gaps = [gap for e in context.get("web_evidence", []) for gap in e.get("coverage", [])]
    if context.get("sector_run") and not context.get("market_queries") and not any(e.get("operation") == "search" for e in context.get("web_evidence", [])):
        acquisition_gaps.append("本轮未检索共享资讯或补充来源，不能据此认定无重大新增。")
    reviews = {}
    for review in parsed.reviews:
        coverage = list(dict.fromkeys([*review.coverage, *acquisition_gaps]))
        changed_events = False
        for item in review.events:
            signal = f"sector:{item.event_key}"
            case = session.scalar(select(RiskCase).where(RiskCase.instrument_id == review.instrument_id, RiskCase.signal == signal).order_by(RiskCase.created_at.desc()))
            item_sources = [sources[s] for s in item.source_ids]
            if case is not None and _same_progress(case, item, item_sources) and (item.action != "resolved" or not case.trigger_active):
                continue
            discovered_at = datetime.now(UTC).isoformat()
            action = "new" if case is None else "resolved" if item.action == "resolved" else "updated"
            from watchlist_app.services.market_evidence import source_reference
            snapshot = {**item.model_dump(mode="json"), "action": action, "sources": [source_reference(source) for source in item_sources],
                "coverage": coverage, "discovered_at": discovered_at, "run_id": run.entry_id,
                "checked_at": context["cutoff"]}
            first_discovered = ((case.evidence_json or {}).get("discovered_at") or
                                (case.created_at.isoformat() if case.created_at else None)) if case else None
            if case is None:
                case = RiskCase(case_id=uuid4().hex, instrument_id=review.instrument_id, signal=signal, status="open", history_json=[], trigger_active=True)
                session.add(case)
            case.title, case.body, case.severity = item.title, item.body, "attention"
            case.evidence_json = {**snapshot, "importance": "high", "discovered_at": first_discovered or discovered_at,
                                  "progress_at": discovered_at}
            factual_date = item.occurred_at or item.published_at
            case.observed_on = date.fromisoformat(factual_date[:10]) if factual_date else None
            case.trigger_active = item.action != "resolved"
            case.resolved_at = datetime.now(UTC) if item.action == "resolved" else None
            if item.action != "resolved":
                case.status = "open"
            else:
                case.status = "resolved"
            case.history_json = [*(case.history_json or []), {"at": discovered_at, "action": action,
                "detail": item.body, "snapshot": {**snapshot, "status": case.status, "trigger_active": case.trigger_active}}]
            changed_events = True
        dossier = next((d for d in context.get("research_dossiers", []) if d["instrument_id"] == review.instrument_id), {})
        notebook = retain_notebook(review.research, dossier.get("notebook"), notebook_evidence, run.entry_id, context["cutoff"]) if review.research is not None else None
        if notebook and review.research.mandate_update is not None:
            from watchlist_app.services.research_dossier import save_mandate
            save_mandate(session, review.instrument_id, review.research.mandate_update, commit=False, origin="research")
        prior = previous_reviews.get(review.instrument_id) or {}
        publishes = review.change_kind == "investment" or changed_events
        # An explicit quiet/knowledge update cannot turn a paraphrase into a new PM article.
        summary = review.summary if publishes and review.summary.strip() else prior.get("summary", "")
        changed = publishes and bool(review.summary.strip()) and review.summary != prior.get("summary")
        reviews[review.instrument_id] = {"status": "limited" if coverage else "completed", "summary": summary,
            "view_updated_at": context["cutoff"] if changed else prior.get("view_updated_at"),
            "view_run_id": run.entry_id if changed else prior.get("view_run_id"),
            "change_kind": "investment" if publishes else "knowledge" if notebook and (
                notebook.get("version_id") != (dossier.get("notebook") or {}).get("version_id") or review.research.mandate_update) else "none",
            "coverage": coverage, "market_coverage": context.get("market_coverage"), "research": notebook}
    run.context_json = {**context, "reviews": reviews}
    run.body = "\n\n".join(f"{iid.upper()} · {instrument_label(session, iid)}\n{review['summary']}" for iid, review in reviews.items())
    run.status = "completed"
    run.completed_at = datetime.now(UTC)


def _research_market(session, instrument_id):
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        return None
    if instrument.instrument_type == "private_fund":
        return "cn"
    calendar = (instrument.source_settings_json or {}).get("market_calendar") or instrument.exchange_code
    return market_scope_for_calendar(calendar)


def _research_dates(session, ids, now):
    dates = {}
    for iid in ids:
        market = _research_market(session, iid)
        if market is None:
            raise ValueError(f"{iid} 尚未配置支持的交易市场，不能安排自动研究。")
        dates[iid] = now.astimezone(ZoneInfo(MARKET_SCOPE_TIMEZONES[market])).date().isoformat()
    return dates


def _research_due(market, now):
    if market is None:
        return False
    local = now.astimezone(ZoneInfo(MARKET_SCOPE_TIMEZONES[market]))
    if (local.hour, local.minute) < (8, 30):
        return False
    day = local.date()
    return bool(_market_calendar_sessions(MARKET_SCOPE_CALENDARS[market][0], day, day))


def daily_review_groups(session, *, now=None):
    from watchlist_app.services.shared_instrument_registry import list_shared_active_instrument_ids
    registered = set(list_shared_active_instrument_ids(instrument_types={"equity", "etf", "index"}))
    statuses = {}
    for value in session.scalars(select(InstrumentAttributeValue).where(
        InstrumentAttributeValue.attribute_key == "coverage_status", InstrumentAttributeValue.instrument_id.in_(registered))
        .order_by(InstrumentAttributeValue.adopted_at.desc(), InstrumentAttributeValue.instrument_attribute_value_id.desc())):
        statuses.setdefault(value.instrument_id, value.value_json)
    selected = [iid for iid, status in statuses.items() if status in {"Proposed", "Invested"}]
    ids = sorted(session.scalars(select(InstrumentDetail.instrument_id).where(
        InstrumentDetail.is_active.is_(True), InstrumentDetail.instrument_id.in_(selected),
        InstrumentDetail.instrument_type.in_(("equity", "etf", "index")))))
    now = now or datetime.now(UTC)
    groups = [[iid] for iid in ids if _research_due(_research_market(session, iid), now)]
    reviews = latest_reviews(session)
    # Resume the least recently attempted work first, including after a restart or date change.
    return sorted(groups, key=lambda group: min((reviews.get(iid) or {}).get("checked_at") or "" for iid in group))


def run_daily_reviews(stop):
    from studio_identity import principal_context, service_principal
    with principal_context(service_principal("watchlist")):
        _run_daily_reviews(stop)


def _run_daily_reviews(stop):
    from watchlist_app.services.research_runner import run_analysis
    from watchlist_app.services.research_workbench import portfolio_options
    from watchlist_app.services.risk_officer import begin_run as begin_risk_run, read_snapshot as read_risk_snapshot
    with get_session_factory()() as session:
        groups = daily_review_groups(session)
        if not groups:
            return
        research_dates = _research_dates(session, [iid for ids in groups for iid in ids], datetime.now(UTC))
        watchlist_scopes = [{"watchlist_id": iid} for iid in session.scalars(select(Watchlist.watchlist_id))]
    risk_scopes = [{"portfolio_id": p["portfolio_id"]} for p in portfolio_options().get("portfolios", [])] + watchlist_scopes

    def review_group(ids):
        from studio_identity import principal_context, service_principal
        with principal_context(service_principal("watchlist")):
            return review_group_authenticated(ids)

    def review_group_authenticated(ids):
        if stop.is_set():
            return False
        try:
            with get_session_factory()() as session:
                run, created = begin_run(session, ids, scheduled=True)
                run_id, status = run.entry_id, run.status
            if created or status == "queued":
                run_analysis(run_id)
                with get_session_factory()() as session:
                    status = session.get(ResearchEntry, run_id).status
            return status not in {"queued", "running"}
        except Exception:
            logging.getLogger(__name__).exception("Daily research failed for %s", ids)
            return False

    remaining = list(groups)
    pending_ids = set()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="daily-research") as pool:
        for scope in risk_scopes:
            if stop.is_set():
                return
            try:
                with get_session_factory()() as session:
                    member_ids = set(read_risk_snapshot(session, **scope)["instrument_ids"])
                scope_dates = {iid: day for iid, day in research_dates.items() if iid in member_ids}
                if not scope_dates:
                    continue
                # Finish this scope's members before the officer reads their reports.
                selected = [ids for ids in remaining if member_ids.intersection(ids)]
                for ids, finished in zip(selected, pool.map(review_group, selected)):
                    remaining.remove(ids)
                    if not finished:
                        pending_ids.update(ids)
                if stop.is_set():
                    return
                if pending_ids.intersection(member_ids):
                    continue
                with get_session_factory()() as session:
                    run, created = begin_risk_run(session, **scope, scheduled_dates=scope_dates)
                    run_id, status = run.entry_id, run.status
                if created or status == "queued":
                    run_analysis(run_id)
            except Exception:
                logging.getLogger(__name__).exception("Daily risk assessment failed for %s", scope)
        list(pool.map(review_group, remaining))


def start_sector_worker():
    """Review proposed/invested instruments with four workers and retained daily deduplication."""
    stop = Event()
    def work():
        while not stop.is_set():
            try:
                run_daily_reviews(stop)
            except Exception:
                logging.getLogger(__name__).exception("Daily research scope could not be read")
            stop.wait(60)
    thread = Thread(target=work, name="daily-research", daemon=True)
    thread.start()
    return thread, stop
