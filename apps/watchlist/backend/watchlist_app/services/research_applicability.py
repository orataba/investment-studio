"""Asset research boundaries shared by input selection and the reading surface.

Registry facts determine identity; a label or product name never establishes
holdings, leverage, strategy, or a disclosure frequency.
"""
from copy import deepcopy


DAILY_PRICE_INSTRUMENT_TYPES = frozenset({"equity", "etf", "index", "crypto"})


def numeric_tool_capabilities(instrument_type):
    return {"tool": "read_research_numbers", "observations": {"supported": True,
        "note": "按资产类型和实际可用样本返回固定观察；不足时明确缺口，不构成因果证明。"},
        "event_reaction": {"supported": instrument_type in DAILY_PRICE_INSTRUMENT_TYPES,
            "note": "仅equity/etf/index/crypto的已核实事件日期与实际日频价格；公募/私募净值不适用，改用observations的真实净值区间且不能归因于单日事件。"}}


def research_applicability(registration: dict) -> dict:
    kind = registration["instrument_type"]
    private = kind == "private_fund"
    channels = ["bound_source_revision", "material_arrival", "tracked_condition"]
    channels += (["manager", "terms", "subscriptions_redemptions", "valuation_operations"]
                 if kind in {"private_fund", "public_fund"} else ["public_disclosure"])
    return {
        "schema_version": 1,
        "instrument_type": kind,
        "underlying": {
            "asset_class": registration.get("asset_class"),
            "strategy": deepcopy(registration.get("disclosed_strategy")),
            "status": "documented" if registration.get("asset_class") or registration.get("disclosed_strategy") else "unknown",
        },
        "mechanisms": {key: deepcopy(registration.get(key)) for key in
            ("currency", "benchmark", "investment_type", "fund_type", "nav_settings")},
        "monitoring": {"generic_news": not private, "numeric_change_requires_research": False,
                       "material_channels": channels},
        "section_order": (["themes", "strategy", "materials", "quantitative", "events"] if private
                          else ["themes", "events", "quantitative", "materials"] if kind == "public_fund"
                          else ["events", "themes", "quantitative"]),
        "limitations": ["分类和名称不证明实际敞口；未知策略、杠杆、持仓及披露频率保持未知。"],
    }
