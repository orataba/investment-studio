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


def build_research_plan(registration: dict, *, user_constraints: list[str] | None = None,
                        module_focus: list[dict] | None = None, guidance: str = "") -> dict:
    """Bind the question and optional references, never assign a fixed coverage grid."""
    kind = registration["instrument_type"]
    name = registration.get("name") or registration.get("identifier") or "本标的"
    scope = f"围绕{name}的实际研究问题、回报机制、证据与反证开展研究。"
    if kind in {"etf", "index"}:
        scope += " 以实际覆盖的底层资产和编制规则确定范围；其他公司或资产须说明具体关联。"
    elif kind in {"public_fund", "private_fund"}:
        scope += " 区分实际产品、份额与策略，以已取得的净值和披露材料判断；未知持仓、杠杆与对冲保持未知。"
    constraints = [item for item in user_constraints or [] if item.strip()]
    if constraints:
        scope += " 用户明确约束：" + "；".join(constraints)
    basis = [f"登记身份：{TYPE_LABELS.get(kind, kind)}；身份与分类不自动指定研究章节。"]
    taxonomy = registration.get("taxonomy") or {}
    labels = taxonomy.get("path_labels") or [taxonomy.get("assigned_label") or taxonomy.get("assigned_node_id")]
    if any(labels):
        basis.append("已登记分类：" + " / ".join(str(label) for label in labels if label) + "；分类不是实际敞口的证明。")
    for key, label in (("benchmark", "登记基准"), ("asset_class", "资料中的底层类别"),
                       ("fund_type", "登记基金类型")):
        if registration.get(key):
            basis.append(f"{label}：{registration[key]}；按原材料日期和实际范围核实。")
    if registration.get("disclosed_strategy"):
        basis.append("已有人工维护的策略资料；保留原作者、日期和证据范围。")
    gaps = []
    if kind in {"etf", "index", "public_fund", "private_fund"} and not (
            registration.get("asset_class") or registration.get("fund_type")):
        gaps.append("底层资产与实际敞口尚无明确资料确认；不能由名称或分类推断持仓、杠杆与对冲。")
    library = {item["id"]: item for item in method_library()["frameworks"]}
    references = []
    for focus in module_focus or []:
        identifier = focus["module_id"]
        definition = library.get(identifier)
        # A locally proposed method is valid without registration in the shared
        # library. Only actual shared references carry a library version.
        reference = ({key: deepcopy(value) for key, value in definition.items() if key != "industry_guides"}
                     if definition else {"id": identifier, "title": identifier, "version": None})
        reference.update(reason=focus["reason"], applicability="optional",
                         source_ids=list(focus.get("source_ids") or []),
                         reference_kind="shared_method" if definition else "local_method")
        references.append(reference)
    return {"mode": "question_driven", "scope": scope, "guidance": guidance,
            "basis": basis, "gaps": gaps, "modules": references,
            "method_note": "方法库是可选参考；可提出目录外方法与分析标题，不要求固定模块或逐项完成。方法选择不是已核实事实。"}


def analyst_guidance(plan: dict) -> str:
    """All entrances share the same human instructions and research boundaries."""
    return "\n".join([plan["scope"],
        *(["用户研究指导：" + plan["guidance"]] if plan.get("guidance") else []),
        "从实际问题选择证据和分析方式；公共方法仅供参考，可自定义研究结构，不必补齐章节。",
        "保留来源、日期、金融口径、反证和限制；未读取或资料不足不能写成没有风险。"])
