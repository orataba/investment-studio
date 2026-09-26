"""Current risk-review versions, without preparing another model evidence corpus."""
from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import Boolean, select, true
from studio_identity import current_principal

from watchlist_app.db.models import (
    InstrumentChartReadModel, InstrumentDetail, InstrumentManualProfile,
    InstrumentPerformanceReadModel, InstrumentRiskReadModel, Watchlist, WatchlistItem,
)
from watchlist_app.db.models.research import InstrumentInvestmentStance, InstrumentResearchNote, InstrumentResearchProfile
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskReviewRule
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_workbench import catalogue
from watchlist_app.services.risk_workspace_projection import case_summary_rows


def current_scope(session, scope):
    """Only the directory, current cases and dates used by the browser."""
    from watchlist_app.services.risk_officer import external_json, normalize_scope, selected_cases
    scope = normalize_scope(**scope)
    key, identifier = next(iter(scope.items()))
    kind = key.removesuffix("_id")
    available, limitations, portfolio = True, [], None
    if kind == "portfolio":
        try:
            portfolio = external_json("portfolio", f"/portfolios/{quote(identifier, safe='')}/risk-context/version")
            if portfolio.get("portfolio_id") != identifier:
                raise ValueError("组合持仓响应与请求范围不一致。")
            available = bool(portfolio.get("available") and portfolio.get("version"))
            ids = sorted(set(portfolio.get("instrument_ids", []))) if available else []
            name = portfolio.get("name") or identifier
            limitations.extend(portfolio.get("limitations", []))
        except (OSError, ValueError) as error:
            ids, name, available = [], identifier, False
            limitations.append(f"当前组合持仓读取未完成，不能确认涉及敞口：{type(error).__name__}。")
    elif kind == "watchlist":
        record = session.get(Watchlist, identifier)
        if record is None:
            raise ValueError("所选观察列表不存在。")
        ids = sorted(session.scalars(select(WatchlistItem.instrument_id).where(WatchlistItem.watchlist_id == identifier)))
        name = record.name
        limitations.append("这是观察列表成员的风险汇总，不代表实际持仓，未计算组合权重或风险贡献。")
    else:
        name = session.scalar(select(InstrumentDetail.instrument_name).where(InstrumentDetail.instrument_id == identifier))
        if name is None:
            raise ValueError("所选标的不存在。")
        ids = [identifier]
    instruments = sorted(catalogue(session, ids), key=lambda item: item["instrument_id"])
    cases = case_summary_rows(session, set(ids))
    categories = selected_cases(cases)
    missing = sorted(set(ids) - {item["instrument_id"] for item in instruments})
    if missing:
        limitations.append("以下范围内标的尚无已接入的风险资料：" + "、".join(missing))
    if not ids and available and kind != "portfolio":
        limitations.append("当前范围没有可汇总的标的；没有记录不能视为没有风险。")
    dates = [str(item["as_of_date"]) for item in instruments if item.get("as_of_date")]
    dates.extend(str(case["observed_on"]) for rows in categories.values() for case in rows if case.get("observed_on"))
    if portfolio and portfolio.get("as_of_date"):
        dates.append(portfolio["as_of_date"])
    return {"scope": {"kind": kind, "id": identifier, "name": name}, "scope_available": available,
        "instrument_ids": ids, "instruments": instruments, "cases": cases, "portfolio": portfolio,
        "input_as_of": max(dates, default=None), "counts": {key: len(rows) for key, rows in categories.items()},
        "holdings": (portfolio or {}).get("holdings", []), "limitations": limitations}


def input_version(session, state):
    """Bind existing source revisions, never treat an unversioned input as unchanged.

    This is a conservative dependency version: republishing a dependency can
    require a review even if its values happen to match. It is stored on the
    existing risk run, not maintained as a separate cache or current truth.
    """
    if not state["scope_available"]:
        return None
    from watchlist_app.services.risk_performance import peer_context, _taxonomy_peers
    from watchlist_app.services.research_access import (
        instrument_run_scope, research_context_projection, topic_portfolio_ids_by_topic,
    )
    from investment_studio_instrument_core.db_models import Instrument
    from types import SimpleNamespace
    from watchlist_app.services.sector_research import RESEARCH_TIMEZONES, research_market_for_instrument
    ids = [item["instrument_id"] for item in state["instruments"]]
    principal = current_principal()

    def rows(model, columns, selected=ids, *conditions):
        query = select(*columns).where(model.instrument_id.in_(selected), *conditions)
        return sorted([dict(row._mapping) for row in session.execute(query)], key=lambda row: str(row))

    profiles = rows(InstrumentManualProfile, [InstrumentManualProfile.instrument_id, InstrumentManualProfile.nav_settings_json])
    peer_scope = peer_context(session) if ids else None
    peer_groups = {iid: _taxonomy_peers(iid, peer_scope)[1] for iid in ids}
    comparison_ids = set(ids)
    for group in peer_groups.values():
        comparison_ids.update(group["instrument_ids"])
    for profile in profiles:
        settings = profile["nav_settings_json"] or {}
        comparison_ids.update(settings.get("peer_baseline_instrument_ids") or [])
        if settings.get("default_benchmark_instrument_id"):
            comparison_ids.add(settings["default_benchmark_instrument_id"])
    versions = {}
    for model, selected in ((InstrumentChartReadModel, comparison_ids), (InstrumentPerformanceReadModel, ids), (InstrumentRiskReadModel, ids)):
        versions[model.__tablename__] = rows(model, [model.instrument_id, model.last_recalculated_at,
            model.source_cutoff_at, model.data_freshness_status], selected)
        if any(row["last_recalculated_at"] is None for row in versions[model.__tablename__]):
            return None
    # Absent rows remain explicit through the scoped IDs plus the row lists;
    # later insertion/removal changes the version, rather than looking fresh.
    versions["comparisons"] = rows(InstrumentDetail, [InstrumentDetail.instrument_id, InstrumentDetail.instrument_name], comparison_ids)
    versions["rules"] = rows(RiskReviewRule, list(RiskReviewRule.__table__.columns))
    versions["pm_profiles"] = rows(InstrumentResearchProfile, [InstrumentResearchProfile.instrument_id,
        InstrumentResearchProfile.revision_number, InstrumentResearchProfile.updated_at])
    versions["pm_notes"] = rows(InstrumentResearchNote, [InstrumentResearchNote.instrument_id, InstrumentResearchNote.note_id,
        InstrumentResearchNote.revision_number, InstrumentResearchNote.updated_at, InstrumentResearchNote.completed_at,
        InstrumentResearchNote.deleted_at], ids, true() if principal.local_unrestricted else InstrumentResearchNote.team_id == principal.team_id)
    versions["stances"] = rows(InstrumentInvestmentStance, list(InstrumentInvestmentStance.__table__.columns), ids,
        InstrumentInvestmentStance.team_id == principal.team_id)
    relation, flags = research_context_projection(session, {"sector_run": Boolean, "research_run": Boolean})
    query = select(ResearchEntry.entry_id, ResearchEntry.topic_id, ResearchEntry.updated_at, ResearchEntry.status,
        ResearchEntry.completed_at).select_from(ResearchEntry)
    if relation is not None:
        query = query.join(relation, true())
    query = query.where(ResearchEntry.kind == "analysis", instrument_run_scope(session, ids),
        flags["sector_run"].is_(True) | flags["research_run"].is_(True),
        true() if principal.local_unrestricted else ResearchEntry.team_id == principal.team_id)
    research = list(session.execute(query)) if ids else []
    topics = session.execute(select(ResearchTopic.topic_id, ResearchTopic.portfolio_id).where(
        ResearchTopic.topic_id.in_({row.topic_id for row in research}),
        true() if principal.local_unrestricted else ResearchTopic.team_id == principal.team_id)).all()
    allowed = {topic for topic, portfolios in topic_portfolio_ids_by_topic(session, topics).items() if not portfolios}
    versions["research"] = sorted([dict(row._mapping) for row in research if row.topic_id in allowed], key=lambda row: row["entry_id"])
    versions["themes"] = [dict(row._mapping) for row in session.execute(select(ResearchEntry.entry_id,
        ResearchEntry.updated_at, ResearchEntry.status).where(ResearchEntry.kind == "note",
        ResearchEntry.topic_id.in_([f"dossier:{iid}" for iid in ids]),
        true() if principal.local_unrestricted else ResearchEntry.team_id == principal.team_id).order_by(ResearchEntry.entry_id))]
    markets = {row.instrument_id: research_market_for_instrument(SimpleNamespace(**dict(row._mapping)))
        for row in session.execute(select(Instrument.instrument_id, Instrument.instrument_type,
            Instrument.exchange_code, Instrument.source_settings_json).where(Instrument.instrument_id.in_(ids)))}
    return serialize_payload({"contract": 1, "scope": state["scope"], "instrument_ids": state["instrument_ids"],
        "catalogue": state["instruments"], "cases": sorted(state["cases"], key=lambda row: row["case_id"]),
        "portfolio": (state["portfolio"] or {}).get("version"),
        "peer_groups": peer_groups, "comparison_settings": profiles, "versions": versions,
        # Forecast due-state changes at its market midnight even without a write.
        "review_days": {iid: {"market": markets.get(iid), "day":
            datetime.now(UTC).astimezone(ZoneInfo(RESEARCH_TIMEZONES[markets[iid]])).date().isoformat()
            if markets.get(iid) else None} for iid in ids}})
