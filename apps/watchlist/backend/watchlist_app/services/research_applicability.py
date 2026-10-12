"""Asset research boundaries shared by input selection and the reading surface.

Registry facts determine identity; a label or product name never establishes
holdings, leverage, strategy, or a disclosure frequency.
"""
from copy import deepcopy


DAILY_PRICE_INSTRUMENT_TYPES = frozenset({"equity", "etf", "index", "crypto"})


def investment_research_supported(registration):
    """The current research product covers equities; other assets keep risk/chat."""
    kind = registration["instrument_type"]
    if kind == "equity":
        return True
    if kind in {"private_fund", "crypto"}:
        return False
    asset = str(registration.get("asset_class") or "").strip().lower()
    node = str((registration.get("taxonomy") or {}).get("assigned_node_id") or "")
    if asset:
        return asset in {"equity", "equities", "stock", "stocks", "股票", "权益"}
    if kind == "public_fund":
        return node.startswith("fund-public-equity") or node in {"fund-public-allocation-equity-biased", "fund-public-fof-equity"}
    # ETF/index are admitted unless their registered classification identifies
    # another asset class. Their actual holdings still require original evidence.
    return kind in {"etf", "index"} and not any(value in node for value in (
        "fixed-income", "bond", "commodity", "currency", "crypto", "alternative", "money-market",
        "cash", "multi-asset", "tactical"))


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
        "research_enabled": investment_research_supported(registration),
        "risk_enabled": True,
        "assistant_enabled": True,
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
        "section_order": ["overview", "recent_updates", "themes", "quantitative"],
        "limitations": ["分类和名称不证明实际敞口；未知策略、杠杆、持仓及披露频率保持未知。"],
    }
