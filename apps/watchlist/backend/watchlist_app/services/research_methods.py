"""One shared method library; instrument plans select methods, not ticker pages."""
from copy import deepcopy
import json
from pathlib import Path


DATA_ROOT = Path(__file__).resolve().parents[5] / "data" / "research"
SUPPORTED_TYPES = frozenset({"equity", "etf", "index", "crypto", "public_fund", "private_fund"})
TYPE_LABELS = {"equity": "股票", "etf": "ETF", "index": "指数", "crypto": "加密资产现货",
               "public_fund": "公募基金", "private_fund": "私募基金"}


def method_library() -> dict:
    return json.loads((DATA_ROOT / "frameworks.json").read_text(encoding="utf-8"))


def module_ids() -> set[str]:
    return {item["id"] for item in method_library()["frameworks"]}


def _declared_exposures(registration: dict) -> dict[str, str]:
    # Names and narrative benchmarks do not establish current economic exposures.
    aliases = {
        "equity": "equity-aggregation", "equities": "equity-aggregation", "stock": "equity-aggregation",
        "股票型": "equity-aggregation", "股票型基金": "equity-aggregation", "股票": "equity-aggregation",
        "fixed income": "rates-credit", "fixed-income": "rates-credit", "bond": "rates-credit",
        "bonds": "rates-credit", "债券型": "rates-credit", "债券型基金": "rates-credit", "债券": "rates-credit",
        "money market": "rates-credit", "货币市场型": "rates-credit", "货币型": "rates-credit",
        "commodity": "commodity-supply-demand", "commodities": "commodity-supply-demand",
        "商品型": "commodity-supply-demand", "商品": "commodity-supply-demand",
        "cryptocurrency": "crypto-network", "digital assets": "crypto-network", "crypto": "crypto-network",
    }
    result = {}
    for key, label in (("asset_class", "资料中的底层资产类别"), ("fund_type", "登记基金类型")):
        raw = str(registration.get(key) or "").strip()
        selected = aliases.get(raw.casefold())
        if selected:
            result[selected] = f"{label}为“{raw}”；具体敞口和报告期仍须核实。"
    return result


def _taxonomy_candidates(registration: dict) -> dict[str, str]:
    taxonomy = registration.get("taxonomy") or {}
    path = [str(item) for item in taxonomy.get("path_node_ids", [])]
    node = taxonomy.get("assigned_node_id")
    if node and node not in path:
        path.append(str(node))
    labels = " / ".join(taxonomy.get("path_labels") or []) or taxonomy.get("assigned_label")
    groups = {
        "equity-aggregation": ("etf-equity", "index-equity", "fund-public-equity", "fund-private-equity"),
        "rates-credit": ("etf-fixed-income", "index-fixed-income", "fund-public-fixed-income", "fund-private-credit",
                         "fund-private-relative-value-fixed-income", "fund-private-relative-value-convertible",
                         "fund-public-money-market", "etf-cash"),
        "commodity-supply-demand": ("etf-commodity", "index-commodity", "fund-public-commodity"),
    }
    return {module: f"已登记分类为“{labels or node}”，作为研究线索；须以产品文件或披露敞口确认适用方式。"
            for module, prefixes in groups.items()
            if any(item == prefix or item.startswith(prefix + "-") for item in path for prefix in prefixes)}


def build_research_plan(registration: dict, *, user_constraints: list[str] | None = None,
                        module_focus: list[dict] | None = None) -> dict:
    """Resolve current methods; sources certify a local choice, not global identity."""
    kind = registration["instrument_type"]
    name = registration.get("name") or registration.get("identifier") or "本标的"
    scope = f"研究{name}本身的投资价值、主要依据与变化。"
    if kind in {"etf", "index"}:
        scope = f"以{name}实际覆盖的底层资产和编制规则为研究范围；其他公司或资产仅作有明确关系的背景。"
    elif kind in {"public_fund", "private_fund"}:
        scope = f"研究{name}的实际产品、份额与策略；以已取得的净值和披露材料判断，未知持仓、杠杆与对冲保持未知。"
    constraints = [item for item in user_constraints or [] if item.strip()]
    if constraints:
        scope += " 用户明确约束：" + "；".join(constraints)
    basis = [f"登记身份：{TYPE_LABELS.get(kind, kind)}。"]
    taxonomy = registration.get("taxonomy") or {}
    if taxonomy.get("assigned_node_id"):
        labels = taxonomy.get("path_labels") or [taxonomy.get("assigned_label") or taxonomy["assigned_node_id"]]
        basis.append("已登记分类：" + " / ".join(labels) + "；分类不等于实际敞口。")
    if registration.get("benchmark"):
        basis.append(f"登记基准：{registration['benchmark']}；合同基准、用户比较对象与当前持仓分别核实。")
    if registration.get("disclosed_strategy"):
        basis.append("已有人工维护的策略资料；按原材料归属、日期和证据范围使用。")
    chosen = {
        "identity-structure": ("applicable", "先确认研究对象及底层结构。"),
        "pricing-compensation": ("applicable", "按实际对象评估回报前景与风险补偿，数据不足时保留未知。"),
        "market-quantitative": ("applicable", "使用本标的实际价格或净值以及可比较的观察期。"),
        "events-expectations": ("applicable", "只研究能够影响本标的判断的变化和分歧。"),
    }
    if kind == "equity":
        chosen["business-fundamentals"] = ("applicable", "登记对象为公司股票，研究经营、融资与每股价值。")
    elif kind == "crypto":
        chosen["crypto-network"] = ("applicable", "登记对象为加密资产现货，使用其供给与市场机制。")
    if kind in {"etf", "public_fund", "private_fund"}:
        chosen["product-implementation"] = ("applicable", "基金载体的费用、交易或申赎安排影响最终可得回报。")
    if kind in {"public_fund", "private_fund"}:
        chosen["fund-strategy"] = ("applicable", "基金策略与管理能力须结合净值、真实约束及管理人材料研究。")
    gaps = []
    if kind in {"etf", "index", "public_fund", "private_fund"}:
        candidates = _taxonomy_candidates(registration)
        declared = _declared_exposures(registration)
        if not declared:
            for key, reason in candidates.items():
                chosen[key] = ("unconfirmed", reason)
        for key, reason in declared.items():
            chosen[key] = ("unconfirmed" if len(declared) > 1 else "applicable", reason)
            basis.append(reason)
        if len(declared) > 1:
            gaps.append("登记来源给出了不同的底层资产类别，需核对合同与来源口径后确定适用方式。")
        if declared and set(candidates) - set(declared):
            gaps.append("已登记分类与产品资料中的底层类别不一致；按产品资料安排核查，不能把分类当成持仓。")
        if not declared:
            gaps.append("底层资产与实际敞口尚无明确的结构化资料确认；分类仅用于安排核查，不能代替持仓或合同。")
    definitions = method_library()["frameworks"]
    known = {item["id"] for item in definitions}
    for focus in module_focus or []:
        if focus["module_id"] not in known:
            raise ValueError("专属方法补充引用了不存在的研究模块")
        established = bool(focus.get("source_ids")) or focus.get("selected_by") == "user"
        selection = ("applicable" if established else "unconfirmed", "专属方法选择：" + focus["reason"])
        if established or focus["module_id"] not in chosen:
            chosen[focus["module_id"]] = selection
        if established:
            basis.append(f"专属方法选择：{focus['reason']}；该安排不改变登记身份或证明实际敞口。")
    modules = [{**{key: deepcopy(value) for key, value in item.items() if key != "industry_guides"},
                "applicability": chosen[item["id"]][0], "reason": chosen[item["id"]][1]}
               for item in definitions if item["id"] in chosen]
    return {"scope": scope, "basis": basis, "gaps": gaps, "modules": modules}


def analyst_guidance(plan: dict) -> str:
    """All agent entrances use this plan instead of a second prompt map."""
    return "\n".join([plan["scope"], "按研究计划选用相应模块，并读取公共方法正文、专属重点及用户约束。",
        "模块适用不表示资料已覆盖；缺资料不能写成无风险。方法不是原始事实。",
        "本标的研究维度：" + "、".join(item["title"] for item in plan["modules"])])
