from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from app.repositories.sqlalchemy.manual_profiles import (
    SQLAlchemyAssetManualProfileRepository,
)
from app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from app.repositories.sqlalchemy.watchlists import SQLAlchemyWatchlistRepository
from app.services.read_models import (
    collapse_latest_attribute_values,
    default_fund_exposure_summary_payload,
    default_fund_performance_payload,
    default_fund_risk_payload,
    default_fund_summary_payload,
    execute_watchlist_query,
    merge_summary_attributes,
    serialize_payload,
)


def _to_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _format_number(value: object, digits: int = 2) -> str:
    numeric = _to_float(value)
    if numeric is None:
        return "—"
    return f"{numeric:,.{digits}f}"


def _format_percent(value: object, digits: int = 2) -> str:
    numeric = _to_float(value)
    if numeric is None:
        return "—"
    scaled = numeric if abs(numeric) > 2 else numeric * 100
    return f"{scaled:.{digits}f}%"


def _format_date(value: object) -> str:
    if isinstance(value, str) and value:
        return value
    return "—"


def _pick_best_row(
    rows: Sequence[dict[str, object]],
    field: str,
    *,
    reverse: bool,
) -> dict[str, object] | None:
    candidates = [row for row in rows if _to_float(row.get(field)) is not None]
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: float(row[field]), reverse=reverse)[0]


def _pick_stale_rows(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    stale_statuses = {"stale", "pending_recalc", "partial"}
    return [row for row in rows if str(row.get("data_freshness_status") or "") in stale_statuses]


def _mean_field(rows: Sequence[dict[str, object]], field: str) -> float | None:
    values = [_to_float(row.get(field)) for row in rows]
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return mean(valid)


def _list_join(values: Sequence[str]) -> str:
    return "、".join(value for value in values if value) or "—"


def _default_people_payload() -> dict[str, object]:
    return {"overview": {}, "team": [], "notes": []}


def _default_strategy_payload() -> dict[str, object]:
    return {
        "summary": "",
        "investment_objective": "",
        "process_bullets": [],
        "risk_controls": [],
        "notes": [],
    }


def _default_price_payload() -> dict[str, object]:
    return {
        "overview": {},
        "distribution_policy": "",
        "policy_text": "",
        "fee_notes": [],
        "notes": [],
    }


def _default_documents_payload() -> dict[str, object]:
    return {
        "current_documents": [],
        "recent_imports": [],
        "extraction_reviews": [],
        "notes": [],
    }


def _default_research_payload() -> dict[str, object]:
    return {
        "overview": {},
        "thesis": "",
        "conclusions": [],
        "notes": [],
    }


class StubCopilotProvider:
    provider_name = "stub"

    def __init__(self) -> None:
        settings = get_settings()
        self.model_name = (
            settings.copilot_openai_model
            if settings.copilot_provider == "openai"
            else "gpt-5.4-ready-stub"
        )

    def answer_watchlist(
        self,
        *,
        watchlist_name: str,
        question: str,
        query_result: dict[str, object],
        group_by: str | None,
        view_name: str | None,
    ) -> dict[str, object]:
        rows = list(query_result.get("rows") or [])
        total_rows = int(query_result.get("total_rows") or 0)
        stale_rows = _pick_stale_rows(rows)
        best_return_row = _pick_best_row(rows, "return_1y", reverse=True)
        worst_drawdown_row = _pick_best_row(rows, "max_drawdown", reverse=False)
        highest_rating_row = _pick_best_row(rows, "overall_rating", reverse=True)
        avg_one_year = _mean_field(rows, "return_1y")
        groups = list(query_result.get("groups") or [])
        largest_group = (
            sorted(groups, key=lambda item: int(item.get("row_count") or 0), reverse=True)[0]
            if groups
            else None
        )

        focus = "整体概览"
        if any(keyword in question for keyword in ("更新", "净值", "stale", "fresh")):
            focus = "数据更新"
        elif any(keyword in question for keyword in ("风险", "回撤", "波动", "drawdown")):
            focus = "风险"
        elif any(keyword in question for keyword in ("评级", "观点", "rating")):
            focus = "评级"

        answer_lines = [
            f"基于当前 Watchlist「{watchlist_name}」的 {total_rows} 条可见记录，我先按{focus}给出摘要。",
            f"当前视图是「{view_name or '当前视图'}」；{f'按 {group_by} 分组，最大组是 {largest_group.get('group_value')}（{largest_group.get('row_count')} 条）。' if group_by and group_by != 'none' and largest_group else '当前未分组。'}",
            f"数据新鲜度方面，需优先关注 {len(stale_rows)} 条记录；最近需要核查的对象包括 {_list_join([str(row.get('ticker_or_isin') or row.get('asset_name') or '') for row in stale_rows[:3]])}。",
            f"表现上，1Y 平均回报约 {_format_percent(avg_one_year)}；最好的是 {best_return_row.get('ticker_or_isin') if best_return_row else '—'}（{_format_percent(best_return_row.get('return_1y')) if best_return_row else '—'}）。",
            f"风险上，最大回撤最深的是 {worst_drawdown_row.get('ticker_or_isin') if worst_drawdown_row else '—'}（{_format_percent(worst_drawdown_row.get('max_drawdown')) if worst_drawdown_row else '—'}）；当前最高内部评分的是 {highest_rating_row.get('ticker_or_isin') if highest_rating_row else '—'}（{_format_number(highest_rating_row.get('overall_rating'), 0) if highest_rating_row else '—'}）。",
        ]

        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "mode": "grounded_stub",
            "answer": "\n".join(answer_lines),
            "suggestions": [
                "找出需要优先更新净值的产品",
                "按评级和回撤总结当前名单",
                "解释当前名单里最强和最弱的 1Y 表现",
            ],
            "citations": [
                {
                    "label": "Current view",
                    "ref_type": "watchlist_view",
                    "ref_id": view_name or watchlist_name,
                    "note": f"{total_rows} visible rows after current view/filter logic.",
                },
                {
                    "label": "Freshness scan",
                    "ref_type": "watchlist_rows",
                    "ref_id": watchlist_name,
                    "note": f"{len(stale_rows)} rows flagged as stale/pending.",
                },
                {
                    "label": "Top 1Y performer",
                    "ref_type": "instrument",
                    "ref_id": (
                        str(
                            best_return_row.get("asset_id")
                        )
                        if best_return_row
                        else None
                    ),
                    "note": (
                        f"{best_return_row.get('ticker_or_isin')} 1Y {_format_percent(best_return_row.get('return_1y'))}"
                        if best_return_row
                        else None
                    ),
                },
            ],
            "context_summary": {
                "entity_type": "watchlist",
                "watchlist_name": watchlist_name,
                "visible_rows": total_rows,
                "group_count": len(groups),
                "stale_rows": len(stale_rows),
                "active_view": view_name,
            },
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }

    def answer_fund(
        self,
        *,
        asset_id: str,
        question: str,
        active_tab: str | None,
        summary: dict[str, object],
        exposure: dict[str, object],
        performance: dict[str, object],
        risk: dict[str, object],
        people: dict[str, object],
        strategy: dict[str, object],
        price: dict[str, object],
        documents: dict[str, object],
        research: dict[str, object],
        nav_series: dict[str, object],
    ) -> dict[str, object]:
        fund_name = str(summary.get("fund_name") or asset_id.upper())
        ticker = str(summary.get("ticker_or_isin") or asset_id.upper())
        active_tab = (active_tab or "quote").lower()
        nav_rows = list(nav_series.get("rows") or [])
        latest_nav = nav_rows[-1] if nav_rows else {}
        latest_nav_date = latest_nav.get("date") if isinstance(latest_nav, dict) else None
        one_year = None
        trailing_returns = list(performance.get("trailing_returns") or [])
        for row in trailing_returns:
            window = str(row.get("window") or row.get("label") or "").lower()
            if window in {"1y", "1-year", "1 year"}:
                one_year = row
                break
        risk_metrics = list(risk.get("risk_metrics") or [])
        metric_map = {str(item.get("metric") or "").lower(): item for item in risk_metrics}
        team_rows = list(people.get("team") or [])
        document_rows = list(documents.get("current_documents") or [])
        conclusion_rows = list(research.get("conclusions") or [])
        price_overview = price.get("overview") if isinstance(price.get("overview"), dict) else {}

        if active_tab == "performance":
            body = [
                f"Performance 视角下，{ticker} 目前 1Y 回报是 {_format_percent(one_year.get('investment') if one_year else None)}，3Y 年化约 {_format_percent(next((row.get('investment') for row in trailing_returns if str(row.get('window') or '').lower() in {'3y', '3-year', '3 year'}), None))}。",
                f"当前类别排名是 {performance.get('ranking', {}).get('quartile') if isinstance(performance.get('ranking'), dict) else '—'}Q / {performance.get('ranking', {}).get('percentile') if isinstance(performance.get('ranking'), dict) else '—'} pct。",
                "如果你要继续，我适合进一步解释：年度回报断点、相对类别/基准的偏离、以及最近一段表现变化。",
            ]
        elif active_tab == "risk":
            body = [
                f"Risk 视角下，最大回撤约 {_format_percent(risk.get('drawdown_summary', {}).get('maximum') if isinstance(risk.get('drawdown_summary'), dict) else None)}，波动率约 {_format_number(metric_map.get('standard_deviation', {}).get('investment'))}，Sharpe 约 {_format_number(metric_map.get('sharpe_ratio', {}).get('investment'))}.",
                f"当前 freshness 状态为 {summary.get('freshness', {}).get('data_freshness_status') if isinstance(summary.get('freshness'), dict) else '—'}，我建议把风险读数和最近净值更新一起看。",
            ]
        elif active_tab == "exposure":
            holdings_summary = exposure.get("holdings_summary") if isinstance(exposure.get("holdings_summary"), dict) else {}
            style_box = exposure.get("style_box") if isinstance(exposure.get("style_box"), dict) else {}
            body = [
                f"Exposure 视角下，当前披露持仓数约 {holdings_summary.get('total_holdings') or '—'}，Top 10 集中度约 {_format_percent(holdings_summary.get('top10_concentration'))}。",
                f"组合久期约 {_format_number(style_box.get('weighted_duration'))}，YTW 约 {_format_percent(style_box.get('yield_to_worst'))}。",
            ]
        elif active_tab == "price":
            body = [
                f"Price 视角下，Total Expense Ratio 是 {_format_number(price_overview.get('total_expense_ratio'))}，Adjusted Expense Ratio 是 {_format_number(price_overview.get('adjusted_expense_ratio'))}。",
                f"Distribution Policy 当前记录为 {price.get('distribution_policy') or '—'}；如果你要，我可以继续把 policy 文本压缩成投资者可读摘要。",
            ]
        elif active_tab == "people":
            body = [
                f"People 视角下，当前记录的管理团队人数是 {len(team_rows)}，核心成员包括 {_list_join([str(item.get('name') or '') for item in team_rows[:3]])}。",
                "如果要继续，我更适合帮你起草团队稳定性点评，或者指出当前人物资料缺口。",
            ]
        elif active_tab == "strategy":
            body = [
                f"Strategy 视角下，当前 investment objective 是：{strategy.get('investment_objective') or '未填写'}",
                f"核心 thesis 是：{strategy.get('summary') or '未填写'}",
            ]
        elif active_tab == "documents":
            body = [
                f"Documents 视角下，当前 adopted documents 有 {len(document_rows)} 份，最近 extraction reviews 有 {len(documents.get('extraction_reviews') or [])} 条。",
                "如果要继续，我可以帮你指出哪些文档还没有被 research 结论引用。",
            ]
        elif active_tab == "research":
            body = [
                f"Research 视角下，当前记录的结论有 {len(conclusion_rows)} 条，Thesis {'已填写' if research.get('thesis') else '尚未填写'}。",
                "如果要继续，我可以把 Documents、Quote、Monitoring 一起压成一版研究摘要。",
            ]
        elif active_tab == "monitoring":
            freshness = summary.get("freshness") if isinstance(summary.get("freshness"), dict) else {}
            body = [
                f"Monitoring 视角下，Freshness 状态是 {freshness.get('data_freshness_status') or '—'}，最近一次成功快照时间是 {_format_date(freshness.get('last_successful_snapshot_at'))}。",
                "如果要继续，我适合帮你把这只产品的待办按优先级排出来。",
            ]
        else:
            body = [
                f"{fund_name}（{ticker}）当前最新净值日期是 {_format_date(latest_nav_date)}，研究主口径是 {nav_series.get('nav_basis_preference') or 'auto'}。",
                f"1Y 回报约 {_format_percent(one_year.get('investment') if one_year else None)}，最大回撤约 {_format_percent(risk.get('drawdown_summary', {}).get('maximum') if isinstance(risk.get('drawdown_summary'), dict) else None)}。",
                f"当前 adopted documents {len(document_rows)} 份、research conclusions {len(conclusion_rows)} 条、管理团队记录 {len(team_rows)} 人。",
            ]

        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "mode": "grounded_stub",
            "answer": "\n".join(body),
            "suggestions": [
                "总结这只产品最近的净值和更新情况",
                "用 Documents + Research 起草一段中文投资摘要",
                "解释这只产品的主要风险点",
            ],
            "citations": [
                {
                    "label": "Instrument summary",
                    "ref_type": "fund",
                    "ref_id": asset_id,
                    "note": f"{fund_name} / {ticker}",
                },
                {
                    "label": "NAV history",
                    "ref_type": "nav_series",
                    "ref_id": asset_id,
                    "note": f"{len(nav_rows)} rows available; latest {_format_date(latest_nav_date)}.",
                },
                {
                    "label": "Research profile",
                    "ref_type": "research",
                    "ref_id": asset_id,
                    "note": f"{len(conclusion_rows)} conclusions, {len(document_rows)} adopted documents.",
                },
            ],
            "context_summary": {
                "entity_type": "instrument",
                "asset_id": asset_id,
                "fund_name": fund_name,
                "active_tab": active_tab,
                "nav_rows": len(nav_rows),
                "document_count": len(document_rows),
                "conclusion_count": len(conclusion_rows),
            },
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
class CopilotService:
    def __init__(self) -> None:
        self.provider = StubCopilotProvider()
        self.read_model_repository = SQLAlchemyReadModelRepository()
        self.watchlist_repository = SQLAlchemyWatchlistRepository()
        self.attribute_repository = SQLAlchemyInstrumentAttributeRepository()
        self.manual_profile_repository = SQLAlchemyAssetManualProfileRepository()

    def chat_watchlist(
        self,
        session: Session,
        *,
        watchlist_id: str,
        question: str,
        view_id: str | None,
        selected_fields: list[str],
        filters: dict[str, list[object]],
        advanced_filters: dict[str, object] | None,
        sort: list[dict[str, str]],
        group_by: str | None,
    ) -> dict[str, object]:
        watchlist = self.watchlist_repository.get(session, watchlist_id)
        if watchlist is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Watchlist not found")

        rows = self.read_model_repository.list_watchlist_rows(session, watchlist_id)
        view = (
            self.read_model_repository.get_view(
                session,
                watchlist_id=watchlist_id,
                view_id=view_id,
            )
            if view_id
            else None
        )
        analysis_fields = [
            "asset_name",
            "ticker_or_isin",
            "category_name",
            "overall_rating",
            "analyst_stance",
            "return_1y",
            "max_drawdown",
            "data_freshness_status",
            "last_nav_date",
        ]
        merged_fields = list(dict.fromkeys([*selected_fields, *analysis_fields]))
        query_result = execute_watchlist_query(
            rows=rows,
            payload={
                "selected_fields": merged_fields,
                "filters": filters,
                "advanced_filters": advanced_filters,
                "sort": sort,
                "group_by": group_by,
                "pagination": {"page": 1, "page_size": 200},
            },
            view=view,
        )
        return self.provider.answer_watchlist(
            watchlist_name=watchlist.name,
            question=question,
            query_result=query_result,
            group_by=group_by,
            view_name=view.name if view is not None else None,
        )

    def chat_fund(
        self,
        session: Session,
        *,
        asset_id: str,
        question: str,
        active_tab: str | None,
    ) -> dict[str, object]:
        summary_record = self.read_model_repository.get_summary(session, asset_id)
        performance_record = self.read_model_repository.get_performance(session, asset_id)
        risk_record = self.read_model_repository.get_risk(session, asset_id)
        exposure_record = self.read_model_repository.get_exposure_summary(session, asset_id)
        manual_profile = self.manual_profile_repository.get(session, asset_id)
        attributes = collapse_latest_attribute_values(
            self.attribute_repository.get_values_for_asset(session, asset_id)
        )
        summary_payload = (
            serialize_payload(summary_record.payload_json)
            if summary_record is not None
            else default_fund_summary_payload(asset_id, instrument_attributes=attributes)
        )
        summary_payload = merge_summary_attributes(summary_payload, attributes)
        performance_payload = (
            serialize_payload(performance_record.payload_json)
            if performance_record is not None
            else default_fund_performance_payload()
        )
        exposure_payload = (
            serialize_payload(exposure_record.payload_json)
            if exposure_record is not None
            else default_fund_exposure_summary_payload()
        )
        risk_payload = (
            serialize_payload(risk_record.payload_json)
            if risk_record is not None
            else default_fund_risk_payload()
        )
        nav_settings = (
            serialize_payload(manual_profile.nav_settings_json)
            if manual_profile is not None
            else {}
        )
        nav_rows = []
        chart_record = self.read_model_repository.get_chart(session, asset_id)
        if chart_record is not None:
            chart_payload = serialize_payload(chart_record.payload_json)
            primary_series = next(
                (series for series in chart_payload.get("series", []) if isinstance(series, dict)),
                None,
            )
            if isinstance(primary_series, dict):
                nav_rows = list(primary_series.get("points") or [])
        documents_payload = (
            serialize_payload(manual_profile.documents_payload_json)
            if manual_profile is not None
            else _default_documents_payload()
        )
        research_payload = (
            serialize_payload(manual_profile.research_payload_json)
            if manual_profile is not None
            else _default_research_payload()
        )
        people_payload = (
            serialize_payload(manual_profile.people_payload_json)
            if manual_profile is not None
            else _default_people_payload()
        )
        strategy_payload = (
            serialize_payload(manual_profile.strategy_payload_json)
            if manual_profile is not None
            else _default_strategy_payload()
        )
        price_payload = (
            serialize_payload(manual_profile.price_payload_json)
            if manual_profile is not None
            else _default_price_payload()
        )
        return self.provider.answer_fund(
            asset_id=asset_id,
            question=question,
            active_tab=active_tab,
            summary=summary_payload,
            exposure=exposure_payload,
            performance=performance_payload,
            risk=risk_payload,
            people=people_payload,
            strategy=strategy_payload,
            price=price_payload,
            documents=documents_payload,
            research=research_payload,
            nav_series={
                "rows": nav_rows,
                "nav_basis_preference": nav_settings.get("nav_basis_preference"),
            },
        )
