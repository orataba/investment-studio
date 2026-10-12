"""Asset research boundaries shared by input selection and the reading surface.

Registry facts determine identity; a label or product name never establishes
holdings, leverage, strategy, or a disclosure frequency.
"""
from copy import deepcopy


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
