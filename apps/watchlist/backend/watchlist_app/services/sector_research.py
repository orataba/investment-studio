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
from sqlalchemy import Boolean, JSON, String, case, func, or_, select, true
from investment_studio_instrument_core.db_models import Instrument
from investment_studio_instrument_core.listing_contract import (
    MARKET_SCOPE_CALENDARS, MARKET_SCOPE_TIMEZONES, market_scope_for_calendar,
)

from watchlist_app.db.models import InstrumentAttributeValue, InstrumentDetail, WatchlistItem, Watchlist
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.sector_market_data import read_sector_market_data
from watchlist_app.services.sector_estimates import retained_estimate_sources, usable_estimate_change
from watchlist_app.services.research_notebook import ResearchNotebook, research_sources, retain_notebook, validate_notebook
from watchlist_app.services.research_themes import AnalystThemeUpdate
from watchlist_app.services.calculation_frequency import _market_calendar_sessions

TOPIC_ID = "us-sector-daily-review"
INSTRUMENT_TOPIC_PREFIX = "instrument-events:"
EVENT_INSTRUMENT_TYPES = ("equity", "etf", "index", "public_fund", "private_fund", "crypto")
FUND_INSTRUMENT_TYPES = ("public_fund", "private_fund")
# Operational check time for funds without a supported source calendar. This is
# not an investment-market classification or a daily NAV disclosure requirement.
RESEARCH_TIMEZONES = {**MARKET_SCOPE_TIMEZONES, "fund_nav": "Asia/Shanghai", "crypto": "UTC"}
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


def latest_reviews(session, *, completed_only=False, instrument_ids=None):
    return review_states(session, instrument_ids=instrument_ids)["last_completed" if completed_only else "latest"]


def review_states(session, *, instrument_ids=None):
    """Build current and last-published states from one authorized history read."""
    from studio_identity import current_principal
    from watchlist_app.services.research_access import (
        instrument_run_scope, research_context_projection, research_projection_rows, topic_portfolio_ids_by_topic,
    )
    latest, completed, current_research = {}, {}, {}
    requested_ids = set(instrument_ids) if instrument_ids is not None else None
    if requested_ids == set():
        return {"latest": latest, "last_completed": completed}
    principal = current_principal()
    team_id = principal.team_id
    # Status consumers need the published result, not every retained financial table,
    # original text and model transcript from every historical run.
    relation, values = research_context_projection(session, {
        "instrument_ids": JSON, "reviews": JSON, "cutoff": String,
        "sector_run": Boolean, "research_run": Boolean, "recordkeeping_only": Boolean,
    })
    query = select(ResearchEntry.entry_id, ResearchEntry.topic_id, ResearchEntry.status,
        case((ResearchEntry.status == "failed", ResearchEntry.body), else_="").label("body"),
        *(value.label(name) for name, value in values.items()),
    ).select_from(ResearchEntry)
    if relation is not None:
        query = query.join(relation, true())
    query = query.where(ResearchEntry.kind == "analysis",
        True if principal.local_unrestricted else ResearchEntry.team_id == team_id,
        or_(values["sector_run"].is_(True), values["research_run"].is_(True)))
    if requested_ids is not None:
        # Failed/queued checks may not yet contain a review. The saved run scope,
        # rather than the result or today's topic membership, determines inclusion.
        query = query.where(instrument_run_scope(session, sorted(requested_ids), scope=values["instrument_ids"]))
    runs = research_projection_rows(session,
        query.order_by(func.coalesce(ResearchEntry.completed_at, ResearchEntry.created_at).desc()),
        {name: (name,) for name in values})
    topic_ids = {run.topic_id for run in runs if run.instrument_ids}
    topics = session.execute(select(ResearchTopic.topic_id, ResearchTopic.portfolio_id).where(
        ResearchTopic.topic_id.in_(topic_ids),
        True if principal.local_unrestricted else ResearchTopic.team_id == team_id,
    )).all() if topic_ids else []
    # Include all entries, including older notes and risk conversations, when
    # excluding portfolio-bound history from team-level research states.
    portfolio_scopes = topic_portfolio_ids_by_topic(session, topics)
    allowed_topics = {topic_id for topic_id, portfolios in portfolio_scopes.items() if not portfolios}
    for run in runs:
        context = run._mapping
        if not (context.get("sector_run") or context.get("research_run")):
            continue
        published = run.status in {"completed", "draft"}
        if not context.get("instrument_ids") or run.topic_id not in allowed_topics:
            continue
        for iid in context.get("instrument_ids", []):
            if requested_ids is not None and iid not in requested_ids:
                continue
            review = (context.get("reviews") or {}).get(iid, {})
            accepted = published and review.get("status") in {"completed", "limited"}
            # A conversation is not a daily check until it actually publishes research.
            if not context.get("sector_run") and not accepted:
                continue
            if accepted and iid not in current_research and review.get("research"):
                current_research[iid] = review["research"]
            # Restoring citations changes the current notebook, not the fact or
            # time of a research check or the original investment judgment.
            if context.get("recordkeeping_only"):
                continue
            if iid in latest and (not accepted or iid in completed):
                continue
            state = {"run_id": run.entry_id, "status": review.get("status", run.status),
                "checked_at": context.get("cutoff"), "summary": review.get("summary", run.body if run.status == "failed" else ""),
                "view_updated_at": review.get("view_updated_at"),
                "change_kind": review.get("change_kind", "investment" if review.get("summary") else "none"),
                "coverage": review.get("coverage", []), "research": review.get("research"), "reflection": review.get("reflection")}
            latest.setdefault(iid, state)
            if accepted:
                completed.setdefault(iid, state.copy())
    for result in (latest, completed):
        for iid, review in result.items():
            # The versioned analyst view is the only current investment judgment.
            # A report's prose is historical output, not a second current truth.
            # In particular, explicit withdrawal must not resurrect an older report.
            view = (current_research.get(iid) or {}).get("investment_view") or {}
            review["current_summary"] = view.get("direction", "")
            if review["status"] != "failed":
                review["summary"] = view.get("direction", "")
            review["view_updated_at"] = view.get("updated_at")
            review["view_run_id"] = view.get("source_run_id")
            review["current_research"] = current_research.get(iid)
    return {"latest": latest, "last_completed": completed}


def _future(rows, today):
    return [r for r in rows if r["target_period_end"] >= today and
            (r.get("revenue_avg") is not None or r.get("eps_avg") is not None)]


def sector_snapshot(iid, session, *, as_of=None):
    from watchlist_app.services.sector_estimates import estimate_scope
    from watchlist_app.services.research_workbench import ANALYST_FOCUS
    scope = estimate_scope(session, iid)
    if scope is None:
        return None
    raw = read_sector_market_data(session, scope["symbol"], as_of=as_of, instrument_type=scope["instrument_type"])
    if raw is None:
        return None
    today = (as_of or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    stocks = raw["companies"]
    gap_labels = {"no_forward_quarter_estimates": "缺少未来季度预期", "no_forward_annual_estimates": "缺少未来年度预期",
                  "missing_price": "缺少行情", "missing_holdings": "缺少持仓", "missing_etf_info": "缺少ETF资料",
                  "unclassified_holding": "持仓类型待核对"}
    sector = SECTORS.get(iid.upper())
    view = {"instrument_id": iid, "ticker": scope["symbol"], "instrument_type": scope["instrument_type"],
        "sector_name": sector[0] if sector else scope["name"], "company_symbols": [h["holding_symbol"] for h in stocks],
        "price_as_of": (raw["etf"]["latest_price"] or {}).get("date"),
        "holdings_as_of": max((str(h.get("as_of_date") or "") for h in raw["holdings"]), default="") or None,
        "holdings_observed_on": max((str(h.get("snapshot_date") or "") for h in raw["holdings"]), default="") or None,
        "holdings_date_note": "holdings_as_of仅表示原始资料明确披露的持仓日期；holdings_observed_on是本项目采集观察日，不是持仓报告期或首次披露日期。",
        "stock_count": len(stocks), "stock_weight_pct": None if scope["instrument_type"] == "equity" else sum(h.get("weight_percent") or 0 for h in stocks),
        "annual_estimate_count": sum(bool(_future(h["annual_estimates"], today)) for h in stocks),
        "quarterly_estimate_count": sum(bool(_future(h["quarterly_estimates"], today)) for h in stocks),
        "top_holdings": [] if scope["instrument_type"] == "equity" else [{"symbol": h["holding_symbol"], "name": h["holding_name"], "weight_percent": h["weight_percent"]} for h in stocks[:10]],
        "research_focus": sector[1] if sector else ANALYST_FOCUS[scope["instrument_type"]],
        "data_gaps": [f"{g.get('symbol', '')}：{gap_labels.get(g['kind'], g['kind'])}" for g in raw["gaps"]]}
    companies = {}
    for h in stocks:
        p = h["company_profile"] or {}
        fields = ("estimate_period", "target_period_end", "revenue_avg", "eps_avg", "num_analysts_revenue", "num_analysts_eps",
                  "collected_at", "source_dataset", "raw_sha256", "historical_use", "currency", "currency_status", "currency_source", "source_id", "observed_at", "available_at")
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
    sector_scope = bool(ids) and set(ids).issubset(scoped_ids(session))
    if not sector_scope and (len(ids) != 1 or not scoped_ids(session, instrument_id=ids[0])):
        raise ValueError("请选择一个已登记的股票、基金、ETF、指数或加密资产；批量每日检查仅支持美股行业ETF")
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
    computed_metrics = list(context.get("computed_metrics", []))
    gaps = list(context.get("data_gaps", []))
    for asset in assets:
        iid = asset["instrument_id"]
        asset["source_id"] = f"instrument:{run.entry_id}:{iid}"
        asset["snapshot_cutoff"] = cutoff.isoformat()
        if asset.get("performance_evidence") is not None:
            from watchlist_app.services.research_metrics import _evidence
            performance = asset["performance_evidence"]
            computed = _evidence(f"{asset['name']} · 净值与共同样本比较", cutoff,
                {**performance, "status": "available" if performance["available"] else "unavailable"},
                performance["method"], scope="instrument", instrument_id=iid, source_run_id=run.entry_id)
            # Risk-packet IDs are internal to that packet; research publishes the
            # exact retained calculation under a run-bound, attributable source.
            computed["data"]["source_id"] = computed["source_id"]
            computed["data"]["comparisons"] = [{**row, "source_id": computed["source_id"]}
                for row in performance.get("comparisons", [])]
            asset["performance_evidence"] = computed["data"]
            computed_metrics.append(computed)
        ensure_mandate(session, iid)
        dossiers.append(read_dossier(session, iid, actor=run_identity(context)))
        if iid.upper() in SECTORS or (asset.get("analyst_estimate_history") or {}).get("supported"):
            snapshot = sector_snapshot(iid, session, as_of=cutoff)
            if snapshot is None:
                if (asset.get("analyst_estimate_history") or {}).get("supported"):
                    gaps.append(f"{iid.upper()}尚无本项目留存的公司或披露成分对应的分析师预期快照。")
            else:
                _, evidence, companies = snapshot
                inputs.append({**evidence, "source_id": f"sector:{run.entry_id}:{iid}", "snapshot_cutoff": cutoff.isoformat()})
                company_data[iid] = companies
                estimate_evidence = asset.get("analyst_estimate_history") or {}
                if estimate_evidence.get("source_id"):
                    estimates.append(estimate_evidence)
    prior = session.scalars(select(RiskCase).where(RiskCase.instrument_id.in_(added), RiskCase.signal.like("sector:%")))
    context.update(instrument_inputs=[*context.get("instrument_inputs", []), *assets],
        sector_inputs=inputs, sector_company_data=company_data, research_dossiers=dossiers,
        sector_estimate_evidence=estimates, data_gaps=gaps,
        computed_metrics=computed_metrics,
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
            sector_inputs=[], sector_company_data={}, sector_estimate_evidence=[], computed_metrics=[], prior_events=[],
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
    next_watch: str = Field(default="", max_length=1500)
    follow_up: Literal["none", "watch", "resolved"] = "watch"
    analysis_depth: Literal["brief", "analysis"] = "analysis"
    theme_ids: list[str] = Field(default_factory=list)
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


class ResearchReflection(BaseModel):
    status: Literal["reviewed", "insufficient_evidence"]
    summary: str = Field(default="", max_length=2000)
    reviewed_update_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class SectorReview(BaseModel):
    instrument_id: str
    summary: str = Field(default="", max_length=2000)
    change_kind: Literal["none", "knowledge", "investment"] = "none"
    coverage: list[str] = Field(default_factory=list)
    events: list[SectorEvent] = Field(default_factory=list)
    themes: list[AnalystThemeUpdate] = Field(default_factory=list)
    reflection: ResearchReflection | None = None
    research: ResearchNotebook | None = None


class ReviewResult(BaseModel):
    reviews: list[SectorReview]


def draft_payload(result: ReviewResult):
    """Keep research deltas sparse: omitted fields retain the existing knowledge."""
    payload = result.model_dump(mode="json")
    for model, row in zip(result.reviews, payload["reviews"]):
        row["events"] = [event.model_dump(mode="json", exclude_unset=True) for event in model.events]
        if "themes" in model.model_fields_set:
            row["themes"] = [theme.model_dump(mode="json", exclude_unset=True) for theme in model.themes]
        else:
            row.pop("themes", None)
        if "reflection" not in model.model_fields_set:
            row.pop("reflection", None)
        if model.research is not None:
            row["research"] = model.research.model_dump(mode="json", exclude_unset=True)
    return payload


def usable_computed(source: dict, cutoff: datetime, iid: str) -> bool:
    """Only retained application calculations establish a numeric observation, not a cause."""
    if source.get("source_type") != "computed_metric" or source.get("scope") not in {"public_market", "instrument"}:
        return False
    if source.get("scope") == "instrument" and source.get("instrument_id") != iid:
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
              "occurred_at", "observed_at", "received_at", "retrieved_at", "discovered_at", "time_status", "run_cutoff", "pm_binding_note")
    return [{**{key: source.get(key) for key in fields}, **({"source_type": source["source_type"],
                "changes": source["changes"], "current_snapshot": source["current_snapshot"],
                "previous_snapshot": source["previous_snapshot"]} if source.get("source_type") == "analyst_estimate_changes" else {}),
             **({"measurement": {key: source.get("data", {}).get(key) for key in
                    ("current", "previous", "change_pp", "five_session_change_pp", "historical_reference")},
                 "methodology": source.get("methodology")} if source.get("source_type") == "computed_metric" else {})}
            for source in sources]


def event_version_id(case_id: str, revision: int) -> str:
    return f"{case_id}:{revision}"


def _event_follow_up(value):
    return value.get("follow_up") or ("resolved" if value.get("status") == "resolved" or value.get("action") == "resolved"
                                      else "watch" if value.get("trigger_active", True) else "none")


def _snapshot_view(snapshot, *, case_id=None, revision=None, recorded_at=None):
    return {**snapshot, "follow_up": _event_follow_up(snapshot), "analysis_depth": snapshot.get("analysis_depth", "analysis"),
            "theme_ids": snapshot.get("theme_ids") or [],
            "event_version_id": snapshot.get("event_version_id") or (event_version_id(case_id, revision) if case_id and revision else None),
            "recorded_at": snapshot.get("recorded_at") or recorded_at or snapshot.get("discovered_at"),
            "sources": _source_views(snapshot.get("sources") or []), "coverage": snapshot.get("coverage") or []}


def event_record(case):
    evidence = case.evidence_json or {}
    history = [{**entry, "snapshot": _snapshot_view(entry["snapshot"], case_id=case.case_id, revision=index + 1,
                                                     recorded_at=entry.get("at")) if entry.get("snapshot") else None}
               for index, entry in enumerate(case.history_json or [])]
    return {"case_id": case.case_id, "instrument_id": case.instrument_id,
        "event_key": case.signal.removeprefix("sector:"), "title": case.title, "body": case.body,
        "direction": evidence.get("direction", "uncertain"), "information_type": evidence.get("information_type"),
        "confidence": evidence.get("confidence"), "next_watch": evidence.get("next_watch", ""),
        "status": case.status, "trigger_active": case.trigger_active,
        "follow_up": _event_follow_up({"status": case.status, "trigger_active": case.trigger_active, **evidence}),
        "analysis_depth": evidence.get("analysis_depth", "analysis"), "theme_ids": evidence.get("theme_ids") or [],
        "event_version_id": evidence.get("event_version_id") or event_version_id(case.case_id, max(1, len(history))),
        "recorded_at": evidence.get("recorded_at") or evidence.get("progress_at") or
            (history[-1].get("at") if history else None) or (case.created_at.isoformat() if case.created_at else None),
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
    fields = ("direction", "information_type", "confidence", "published_at", "occurred_at", "next_watch")
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
    # Compare the current assessment. Returning to a prior view is a new revision,
    # even when its wording and admissible sources match a historical snapshot.
    previous = [{"status": case.status, "trigger_active": case.trigger_active, **evidence, "body": case.body}]
    return any(" ".join(row["body"].split()) == " ".join(item.body.split())
               and all(row.get(key) == getattr(item, key) for key in fields)
               and _event_follow_up(row) == item.follow_up
               and row.get("analysis_depth", "analysis") == item.analysis_depth
               and set(row.get("theme_ids") or []) == set(item.theme_ids)
               and identities(row.get("sources", [])) == identities(sources) for row in previous)


def _effective_event(item, case):
    values = item.model_dump(mode="json")
    if case is not None:
        previous = event_record(case)
        for field in ("follow_up", "analysis_depth", "theme_ids", "next_watch"):
            if field not in item.model_fields_set:
                values[field] = previous[field]
    if item.action == "resolved":
        if "follow_up" in item.model_fields_set and item.follow_up != "resolved":
            raise ValueError("结束事件的action与follow_up必须一致")
        values["follow_up"] = "resolved"
    if values["follow_up"] == "watch" and not values["next_watch"].strip():
        raise ValueError("持续跟进的事件需要明确下一步观察；无需跟进时使用follow_up=none")
    return SectorEvent.model_validate(values)


def _theme_scope(session, run, review):
    from watchlist_app.services.research_themes import analyst_theme_target, analyst_theme_values
    from watchlist_app.services.research_identity import research_identity
    team_id = research_identity()["team_id"]
    dossier = next((d for d in run.context_json.get("research_dossiers", []) if d["instrument_id"] == review.instrument_id), {})
    themes = {item["theme_id"]: item for item in dossier.get("themes", []) if item.get("team_id", team_id) == team_id}
    keys = [item.theme_key for item in review.themes]
    if len(keys) != len(set(keys)):
        raise ValueError("同一关注主题在本轮重复出现")
    normalized = lambda value: " ".join(value.split()).casefold()
    for update in review.themes:
        previous = analyst_theme_target(session, review.instrument_id, update)
        if previous and previous["theme_id"] not in themes:
            raise ValueError("研究主题在本轮未读取，请基于最新研究档案更新")
        if previous and run.context_json.get("sector_run") and previous["status"] != "active":
            raise ValueError("原本已暂停或结束的主题不能由自动研究更新或恢复")
        if update.theme_key in themes and (not previous or themes[update.theme_key]["theme_id"] != previous["theme_id"]):
            raise ValueError("新主题的本轮别名不能覆盖已有主题标识")
        values = analyst_theme_values(update, previous)
        if not previous and any(normalized(item["title"]) == normalized(values["title"])
                                or normalized(item["question"]) == normalized(values["question"]) for item in themes.values()):
            raise ValueError("已有同名或相同问题的关注主题，请关联原主题而不是重复建立")
        projected = {**(previous or {"theme_id": update.theme_key, "origin": "researcher", "managed_by": "researcher"}),
                     **values, "_closing_in_run": bool(previous and previous["status"] == "active" and values["status"] in {"paused", "closed"})}
        themes[update.theme_key] = projected
        if previous:
            themes[previous["theme_id"]] = projected
    return themes


def _validate_update_reference(session, run, instrument_id, update_id, *, judgment=False):
    from watchlist_app.services.research_activity import resolve_research_update
    value = resolve_research_update(session, instrument_id, update_id)
    if not value:
        raise ValueError("找不到当前标的此前已发布的研究判断记录")
    if judgment and value.get("kind") == "theme":
        raise ValueError("主题建立或状态记录不是事前判断，请关联主题内具体研究判断或事件版本")
    recorded_at = value.get("recorded_at")
    original_cutoff = run.context_json.get("input_snapshot_cutoff", run.context_json["cutoff"])
    if (not recorded_at or datetime.fromisoformat(recorded_at.replace("Z", "+00:00")) > datetime.fromisoformat(original_cutoff)
            or value.get("run_id") == run.entry_id):
        raise ValueError("复盘需要关联本轮开始前已发布的研究判断，不能把事后记录当作事前判断")
    return value


def _validate_research_links(session, run, review, themes):
    from watchlist_app.services.research_notebook import _merge_partial, _forecast_versions
    context = run.context_json
    dossier = next((d for d in context.get("research_dossiers", []) if d["instrument_id"] == review.instrument_id), {})
    notes = {item["note_id"]: item for item in dossier.get("pm_views", [])}
    event_keys = {item.event_key for item in review.events} | {item.signal.removeprefix("sector:") for item in
        session.scalars(select(RiskCase).where(RiskCase.instrument_id == review.instrument_id, RiskCase.signal.like("sector:%")))}

    def check_theme(theme_id, *, retained_event_link=False):
        if theme_id and theme_id not in themes:
            raise ValueError("研究判断关联了本轮未读取的关注主题")
        if (context.get("sector_run") and theme_id and themes[theme_id]["status"] != "active"
                and not themes[theme_id].get("_closing_in_run") and not retained_event_link):
            raise ValueError("已暂停或结束的关注主题不再自动更新")

    if review.research is not None:
        forecast_versions = _forecast_versions(dossier.get("notebook") or {})
        for field in ("questions", "forecasts", "forecast_reviews", "lessons"):
            previous = {item["key"]: item for item in (dossier.get("notebook") or {}).get(field, [])}
            for model in getattr(review.research, field):
                row = _merge_partial(model, previous.get(model.key))
                check_theme(row.get("theme_id"))
                if row.get("event_key") and row["event_key"] not in event_keys:
                    raise ValueError("研究判断关联的事件不属于当前标的")
                if row.get("related_research_update_id"):
                    original = _validate_update_reference(session, run, review.instrument_id, row["related_research_update_id"], judgment=True)
                    check_theme(original["reference"].get("theme_id"))
                if field in {"forecast_reviews", "lessons"} and row.get("forecast_key") and (
                        row["forecast_key"], row.get("forecast_version_id")) not in forecast_versions:
                    raise ValueError("复盘或经验必须关联此前已保存的预测原版本，不能将事后新建预测作为事前记录")
                if field in {"forecast_reviews", "lessons"} and row.get("forecast_key"):
                    check_theme(forecast_versions[(row["forecast_key"], row["forecast_version_id"])].get("theme_id"))
                if field == "forecast_reviews" and not (row.get("forecast_key") or row.get("related_research_update_id")):
                    raise ValueError("复盘需要关联此前已保存的预测版本或研究判断记录")
                if row.get("pm_note_id"):
                    note = notes.get(row["pm_note_id"])
                    revisions = {item["revision_number"] for item in (note or {}).get("versions", [])}
                    if not note or row.get("pm_note_revision") not in revisions:
                        raise ValueError("请关联本轮已读取的投资经理观点及其原始版本")
                    note_theme = (note.get("research_context") or {}).get("theme_id")
                    theme_id = row.get("theme_id")
                    if theme_id and note_theme != themes[theme_id]["theme_id"]:
                        raise ValueError("研究判断的主题与投资经理原观点不一致")
                    check_theme(note_theme)
    for item in review.events:
        case = session.scalar(select(RiskCase).where(RiskCase.instrument_id == review.instrument_id,
                                                     RiskCase.signal == f"sector:{item.event_key}"))
        effective = _effective_event(item, case)
        if len(effective.theme_ids) != len(set(effective.theme_ids)):
            raise ValueError("同一事件的关注主题引用重复")
        prior_theme_ids = set(event_record(case)["theme_ids"]) if case else set()
        for theme_id in effective.theme_ids:
            # Pausing a theme stops its assigned research, not later facts about
            # a shared event. Retain existing references without reopening it.
            canonical = themes.get(theme_id, {}).get("theme_id", theme_id)
            check_theme(theme_id, retained_event_link=canonical in prior_theme_ids)
    if review.reflection is not None:
        for update_id in review.reflection.reviewed_update_ids:
            original = _validate_update_reference(session, run, review.instrument_id, update_id, judgment=True)
            check_theme(original["reference"].get("theme_id"))


def _resolve_theme_aliases(review, aliases):
    for event in review.events:
        if "theme_ids" in event.model_fields_set:
            event.theme_ids = list(dict.fromkeys(aliases.get(value, value) for value in event.theme_ids))
    if review.research:
        for field in ("questions", "forecasts", "forecast_reviews", "lessons"):
            for item in getattr(review.research, field):
                if item.theme_id:
                    item.theme_id = aliases.get(item.theme_id, item.theme_id)


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
        themes = _theme_scope(session, run, review)
        for update in review.themes:
            validate_notebook(ResearchNotebook(source_ids=update.source_ids), review.instrument_id, notebook_evidence)
        _validate_research_links(session, run, review, themes)
        if review.research is not None:
            validate_notebook(review.research, review.instrument_id, notebook_evidence)
        if review.reflection is not None:
            validate_notebook(ResearchNotebook(source_ids=review.reflection.source_ids), review.instrument_id, notebook_evidence)
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
                raise ValueError("数值快照不能确定外部事件发生时间，发生时间必须留空")
            if item.action != "new" and session.scalar(select(RiskCase).where(
                RiskCase.instrument_id == review.instrument_id, RiskCase.signal == f"sector:{item.event_key}"
            )) is None:
                raise ValueError("更新或解除的事件没有原跟进记录")
    return sources, notebook_evidence


def shared_market_coverage_gaps(context, *, instrument_id=None):
    """Report a searched channel's known window, not an absence of market events."""
    def instant(value):
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None

    coverage = context.get("market_coverage") or {}
    window_end = instant(coverage.get("latest_bundle_window_end"))
    if window_end is None:
        return []
    # Later web reads can advance the run cutoff. Only the actual shared-text
    # query cutoffs describe the interval this channel was asked to cover.
    cutoffs = [cutoff for query in context.get("market_queries", [])
               if instrument_id is None or query.get("instrument_id") in {None, instrument_id}
               if (cutoff := instant(query.get("cutoff"))) is not None]
    if not cutoffs or max(cutoffs) <= window_end:
        return []
    cutoff = max(cutoffs)
    received = instant(coverage.get("latest_received_at"))
    receipt = f"，最后接收资料于 {received.isoformat()}" if received is not None else ""
    return [f"本轮已检索的共享资讯包覆盖截至 {window_end.isoformat()}{receipt}；"
            f"从该窗口结束至实际检索截止 {cutoff.isoformat()}，该渠道期间覆盖尚未确认，不能据此认定没有重大新增。"]


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
        if review.research is None and not review.events and not review.themes and review.change_kind != "investment":
            continue
        bound = next((d for d in context.get("research_dossiers", []) if d["instrument_id"] == review.instrument_id), {})
        current = read_dossier(session, review.instrument_id)
        for key in ("notebook", "mandate"):
            before = bound.get(key) or {}
            after = current.get(key) or {}
            if before.get("version_id", before.get("run_id")) != after.get("version_id", after.get("run_id")):
                raise ResearchVersionConflict("研究记录在本轮分析期间已有更新，本轮草稿已保留；请基于最新版本继续研究。")
        theme_links = review.themes or bool(bound.get("themes") and (review.research is not None or review.events))
        if theme_links and {item["theme_id"]: item["revision_number"] for item in bound.get("themes", [])} != {
                item["theme_id"]: item["revision_number"] for item in current.get("themes", [])}:
            raise ResearchVersionConflict("关注主题在本轮分析期间已有更新，本轮草稿已保留；请基于最新主题继续研究。")
        if "prior_events" in context:
            prior_events = {item["event_key"]: item for item in context["prior_events"] if item["instrument_id"] == review.instrument_id}
            current_events = {item["event_key"]: item for item in events_for_instruments(session, [review.instrument_id])}
            for event in review.events:
                before, after = prior_events.get(event.event_key), current_events.get(event.event_key)
                before_id = before.get("event_version_id") or event_version_id(before["case_id"], max(1, len(before.get("history", [])))) if before else None
                if before_id != (after or {}).get("event_version_id"):
                    raise ResearchVersionConflict("事件判断在本轮分析期间已有更新，本轮草稿已保留；请基于最新事件版本继续研究。")
    acquisition_gaps = [gap for e in context.get("web_evidence", []) for gap in e.get("coverage", [])]
    if context.get("sector_run") and not context.get("market_queries") and not any(e.get("operation") == "search" for e in context.get("web_evidence", [])):
        acquisition_gaps.append("本轮未检索共享资讯或补充来源，不能据此认定无重大新增。")
    reviews = {}
    for review in parsed.reviews:
        coverage = list(dict.fromkeys([*review.coverage, *acquisition_gaps,
            *shared_market_coverage_gaps(context, instrument_id=review.instrument_id)]))
        if context.get("sector_run") and review.reflection is None:
            coverage.append("本轮未记录对既有判断与经验的复核，复盘覆盖尚不明确。")
        elif review.reflection and review.reflection.status == "insufficient_evidence":
            coverage.append(review.reflection.summary or "既有判断的复核证据不足，暂不形成结果结论。")
        from watchlist_app.services.research_themes import save_analyst_theme
        aliases, changed_themes, published_themes = {}, False, []
        theme_scope = _theme_scope(session, run, review)
        for update in review.themes:
            previous = theme_scope[update.theme_key]
            saved = save_analyst_theme(session, review.instrument_id, update, provenance={"source_run_id": run.entry_id})
            aliases[update.theme_key] = saved["theme_id"]
            published_themes.append({key: saved[key] for key in ("theme_id", "theme_key", "source_ids")})
            changed_themes = changed_themes or saved["theme_id"] != previous["theme_id"] or saved["revision_number"] != previous.get("revision_number")
        _resolve_theme_aliases(review, aliases)
        changed_events = False
        for item in review.events:
            signal = f"sector:{item.event_key}"
            case = session.scalar(select(RiskCase).where(RiskCase.instrument_id == review.instrument_id, RiskCase.signal == signal).order_by(RiskCase.created_at.desc()))
            item = _effective_event(item, case)
            item_sources = [sources[s] for s in item.source_ids]
            if case is not None and _same_progress(case, item, item_sources) and (item.action != "resolved" or not case.trigger_active):
                continue
            discovered_at = datetime.now(UTC).isoformat()
            action = "new" if case is None else "resolved" if item.follow_up == "resolved" else "updated"
            case_id = case.case_id if case else uuid4().hex
            revision = len(case.history_json or []) + 1 if case else 1
            from watchlist_app.services.market_evidence import source_reference
            snapshot = {**item.model_dump(mode="json"), "action": action, "sources": [source_reference(source) for source in item_sources],
                "coverage": coverage, "discovered_at": discovered_at, "run_id": run.entry_id,
                "checked_at": context["cutoff"], "recorded_at": discovered_at,
                "event_version_id": event_version_id(case_id, revision)}
            first_discovered = ((case.evidence_json or {}).get("discovered_at") or
                                (case.created_at.isoformat() if case.created_at else None)) if case else None
            if case is None:
                case = RiskCase(case_id=case_id, instrument_id=review.instrument_id, signal=signal, status="open", history_json=[], trigger_active=False)
                session.add(case)
            case.title, case.body, case.severity = item.title, item.body, "attention"
            case.evidence_json = {**snapshot, "importance": "high", "discovered_at": first_discovered or discovered_at,
                                  "progress_at": discovered_at}
            factual_date = item.occurred_at or item.published_at
            case.observed_on = date.fromisoformat(factual_date[:10]) if factual_date else None
            case.trigger_active = item.follow_up == "watch" and item.direction in {"risk", "uncertain"}
            case.resolved_at = datetime.now(UTC) if item.follow_up == "resolved" else None
            case.status = "resolved" if item.follow_up == "resolved" else "open" if item.follow_up == "watch" else "recorded"
            case.history_json = [*(case.history_json or []), {"at": discovered_at, "action": action,
                "detail": item.body, "snapshot": {**snapshot, "status": case.status, "trigger_active": case.trigger_active}}]
            changed_events = True
        dossier = next((d for d in context.get("research_dossiers", []) if d["instrument_id"] == review.instrument_id), {})
        notebook = retain_notebook(review.research, dossier.get("notebook"), notebook_evidence, run.entry_id, context["cutoff"]) if review.research is not None else None
        if notebook and review.research.mandate_update is not None:
            from watchlist_app.services.research_dossier import save_mandate
            save_mandate(session, review.instrument_id, review.research.mandate_update, commit=False, origin="research")
        publishes = review.change_kind == "investment"
        # This run's narrative is not carried forward as a parallel investment view.
        summary = review.summary if publishes else ""
        current_view = ((notebook or dossier.get("notebook") or {}).get("investment_view") or {})
        reviews[review.instrument_id] = {"status": "limited" if coverage else "completed", "summary": summary,
            "view_updated_at": current_view.get("updated_at"),
            "view_run_id": current_view.get("source_run_id"),
            "change_kind": "investment" if publishes else "knowledge" if changed_events or changed_themes or notebook and (
                notebook.get("version_id") != (dossier.get("notebook") or {}).get("version_id") or review.research.mandate_update) else "none",
            "coverage": coverage, "market_coverage": context.get("market_coverage"), "research": notebook,
            "themes": published_themes, "reflection": review.reflection.model_dump(mode="json") if review.reflection else None}
    run.context_json = {**context, "reviews": reviews}
    run.body = "\n\n".join(f"{iid.upper()} · {instrument_label(session, iid)}\n{review['summary']}" for iid, review in reviews.items())
    run.status = "completed"
    run.completed_at = datetime.now(UTC)


def _research_market(session, instrument_id):
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        return None
    if instrument.instrument_type == "crypto":
        return "crypto"
    calendar = (instrument.source_settings_json or {}).get("market_calendar") or instrument.exchange_code
    market = market_scope_for_calendar(calendar)
    return market or ("fund_nav" if instrument.instrument_type in FUND_INSTRUMENT_TYPES else None)


def _research_dates(session, ids, now):
    dates = {}
    for iid in ids:
        market = _research_market(session, iid)
        if market is None:
            raise ValueError(f"{iid} 尚未配置支持的交易市场，不能安排自动研究。")
        dates[iid] = now.astimezone(ZoneInfo(RESEARCH_TIMEZONES[market])).date().isoformat()
    return dates


def _research_due(market, now):
    if market is None:
        return False
    local = now.astimezone(ZoneInfo(RESEARCH_TIMEZONES[market]))
    if market == "crypto":
        # UTC daily bars close at midnight, including weekends. Check after that
        # boundary; missing or delayed provider bars remain a data-coverage gap.
        return (local.hour, local.minute) >= (0, 30)
    if (local.hour, local.minute) < (8, 30):
        return False
    if market == "fund_nav":
        return True
    day = local.date()
    return bool(_market_calendar_sessions(MARKET_SCOPE_CALENDARS[market][0], day, day))


def daily_review_groups(session, *, now=None):
    from watchlist_app.services.shared_instrument_registry import list_shared_active_instrument_ids
    registered = set(list_shared_active_instrument_ids(instrument_types=set(EVENT_INSTRUMENT_TYPES)))
    statuses = {}
    for value in session.scalars(select(InstrumentAttributeValue).where(
        InstrumentAttributeValue.attribute_key == "coverage_status", InstrumentAttributeValue.instrument_id.in_(registered))
        .order_by(InstrumentAttributeValue.adopted_at.desc(), InstrumentAttributeValue.instrument_attribute_value_id.desc())):
        statuses.setdefault(value.instrument_id, value.value_json)
    selected = [iid for iid, status in statuses.items() if status in {"Proposed", "Invested"}]
    ids = sorted(session.scalars(select(InstrumentDetail.instrument_id).where(
        InstrumentDetail.is_active.is_(True), InstrumentDetail.instrument_id.in_(selected),
        InstrumentDetail.instrument_type.in_(EVENT_INSTRUMENT_TYPES))))
    now = now or datetime.now(UTC)
    groups = [[iid] for iid in ids if _research_due(_research_market(session, iid), now)]
    reviews = latest_reviews(session, instrument_ids=[iid for group in groups for iid in group])
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
        watchlist_scopes = [{"watchlist_id": iid} for iid in session.scalars(select(Watchlist.watchlist_id)
            .where(Watchlist.watchlist_id != "all-instruments"))]
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
