"""Read the risk model and dated holdings against the current classification and targets."""
from datetime import date
from math import isfinite, sqrt
from urllib.parse import quote

from sqlalchemy import select

from portfolio_app.db.models import PortfolioDailySnapshotModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.taxonomy_targets import resolve_taxonomy_targets
from portfolio_app.services.risk_model import (
    _daily_mark_to_last_return_matrix, _return_series_with_periods,
    _row_key, estimate_covariance,
)


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def _holding_id(row):
    return row.get("derivative_contract_id") or row.get("position_reference_id")


def _groups(workspace, catalog, taxonomy_id=None):
    if taxonomy_id is None:
        active = [row["taxonomy_id"] for row in catalog.get("taxonomies", []) if row.get("status", "active") == "active"]
        taxonomy_id = active[0] if len(active) == 1 else None
    nodes = {row["taxonomy_node_id"]: row for row in catalog.get("taxonomy_nodes", [])
             if row["taxonomy_id"] == taxonomy_id and row["status"] == "active"}
    assignments = {}
    for item in catalog.get("taxonomy_assignments", []):
        if item["taxonomy_id"] == taxonomy_id and item["target_scope"] == "instrument" and item["status"] == "active":
            assignments.setdefault(item["target_entity_id"], []).append(item["taxonomy_node_id"])
    limitations = [] if taxonomy_id else ["需明确选择分类，不能在多个分类中推定风险分组或目标。"]
    nav = _number((workspace.get("totals") or {}).get("nav"))
    if nav is None or nav <= 0:
        limitations.append("组合净值无效，未计算分类市值权重。")
    weights_available = not limitations
    groups, member_rows = {}, {}
    target_coverage_errors = []
    for row in workspace.get("rows", []):
        core = row.get("instrument_core") or {}
        category = row.get("holding_category")
        value = _number(row.get("market_value_base"))
        zero_exposure = (
            row.get("forward_risk_status") == "no_exposure"
            and value is not None and abs(value) <= 1e-9
        )
        ancestor_ids = []
        if category == "derivatives":
            key, name = "derivative_bucket:__derivatives__", "衍生品"
        elif category == "cash_and_settlement":
            key, name = "cash_bucket:__cash__", "现金与待结算"
        else:
            matches = assignments.get(core.get("instrument_id"), [])
            if len(matches) > 1:
                limitations.append(f"{core.get('instrument_name')}有多个有效分类归属，无法选择。")
                weights_available = False
                continue
            node = nodes.get(matches[0]) if matches else None
            seen = set()
            while node and node.get("parent_taxonomy_node_id"):
                ancestor_ids.append(node["taxonomy_node_id"])
                if node["taxonomy_node_id"] in seen:
                    limitations.append("分类层级存在循环，未继续汇总。")
                    weights_available = False
                    node = None
                    break
                seen.add(node["taxonomy_node_id"])
                node = nodes.get(node["parent_taxonomy_node_id"])
            key, name = (node["taxonomy_node_id"], node["node_name"]) if node else (f"unassigned:{taxonomy_id}", "未分类")
            if node:
                ancestor_ids.append(node["taxonomy_node_id"])
            elif row.get("risk_eligible"):
                target_coverage_errors.append("存在未分类风险敞口；不能将部分分类重新归一后比较全组合风险预算。")
            if not row.get("risk_eligible") and abs(_number(row.get("market_value_base")) or 0.0) > 1e-12:
                target_coverage_errors.append("证券目标范围内存在模型未覆盖的实际敞口，未计算全组合预算偏移。")
            member_keys = [*ancestor_ids, f"instrument:{core.get('instrument_id')}"]
            for member_key in member_keys:
                member = member_rows.setdefault(member_key, {"group_id": member_key, "risk_share": 0.0,
                    "name": nodes.get(member_key, {}).get("node_name") or core.get("instrument_name") or member_key,
                    "risk_status": "modeled"})
                share = 0.0 if zero_exposure else _number(row.get("forward_risk_share"))
                if not row.get("risk_eligible") or (row.get("forward_risk_status") != "ok" and not zero_exposure) or share is None:
                    member["risk_status"] = "unavailable"
                else:
                    member["risk_share"] += share
        group = groups.setdefault(key, {"group_id": key, "name": name, "instrument_ids": [], "holding_ids": [],
            "market_value_base": 0.0, "weight": None, "risk_share": 0.0, "contribution_to_variance": 0.0,
            "risk_budget_share": 0.0, "_has_risk_member": False})
        iid, hid = core.get("instrument_id"), _holding_id(row)
        if iid and iid not in group["instrument_ids"]:
            group["instrument_ids"].append(iid)
        if hid and hid not in group["holding_ids"]:
            group["holding_ids"].append(hid)
        if value is None:
            group["market_value_base"] = None
            weights_available = False
            limitations.append(f"{core.get('instrument_name') or hid}缺少本币市值。")
        elif group["market_value_base"] is not None:
            group["market_value_base"] += value
        if row.get("risk_eligible"):
            group["_has_risk_member"] = True
            share, contribution = _number(row.get("forward_risk_share")), _number(row.get("forward_contribution_to_variance"))
            if zero_exposure:
                share, contribution = 0.0, 0.0
            if (row.get("forward_risk_status") != "ok" and not zero_exposure) or share is None or contribution is None:
                limitations.append(f"{core.get('instrument_name') or hid}的生产风险贡献不可用。")
            else:
                group["risk_share"] += share
                group["contribution_to_variance"] += contribution
    risk = workspace.get("forward_risk") or {}
    risk_ready = risk.get("status") == "ok" and not limitations
    for group in groups.values():
        group["weight"] = group["market_value_base"] / nav if group["market_value_base"] is not None and nav is not None and nav > 0 else None
        has_risk_member = group.pop("_has_risk_member")
        group["risk_status"] = "outside_model" if not has_risk_member else "modeled" if risk_ready else "unavailable"
        if not risk_ready or not has_risk_member:
            group["risk_share"] = group["contribution_to_variance"] = group["risk_budget_share"] = None
        else:
            group["risk_budget_share"] = group["risk_share"]
    for member in member_rows.values():
        if not risk_ready or member["risk_status"] != "modeled":
            member["risk_share"] = None
    return {"status": "ok" if risk_ready else "limited", "weights_available": weights_available, "taxonomy_id": taxonomy_id,
        "member_rows": list(member_rows.values()), "target_comparison_available": risk_ready and not target_coverage_errors,
        "target_coverage_errors": list(dict.fromkeys(target_coverage_errors)),
        "rows": sorted(groups.values(), key=lambda row: abs(row["risk_share"] or 0), reverse=True), "limitations": limitations}


def _targets(catalog, groups):
    taxonomy_id = groups["taxonomy_id"]
    taxonomy = next((row for row in catalog.get("taxonomies", []) if row["taxonomy_id"] == taxonomy_id), None)
    if taxonomy is None:
        return {"status": "unavailable", "rows": [], "limitations": ["需明确选择分类后比较风险预算。"]}
    resolution = next((row for row in catalog.get("target_resolution", []) if row["taxonomy_id"] == taxonomy_id), None)
    if resolution is None:
        resolution = resolve_taxonomy_targets({**catalog, "taxonomy": taxonomy})
    current_by_key = {row["group_id"]: row for row in groups["member_rows"]}
    rows = []
    limitations = ["偏移采用同一生产风险模型的全组合RC分母，不重新归一分类子集；配置预算与模型求解值分别保留。",
                  "目标是配置参照；未配置偏离阈值，目标差不自动构成越限。",
                  *groups["target_coverage_errors"], *resolution["errors"]]
    comparable = groups["target_comparison_available"]
    for member in resolution["member_targets"]:
        if member["member_type"] == "cash_bucket":
            continue
        key = member["member_id"] if member["member_type"] == "taxonomy_node" else f"instrument:{member['member_id']}"
        group = current_by_key.get(key)
        # An absent holding has zero current exposure. A held but unmodeled
        # member has unavailable RC, never an invented zero contribution.
        current = (group["risk_share"] if group else 0.0) if comparable else None
        for stage, prefix in (("saa", "strategic"), ("taa", "tactical")):
            target = member[f"{prefix}_global_risk_target"]
            if target is None:
                continue
            rows.append({"target_set_id": member[f"{prefix}_target_set_id"], "target_set_type": stage,
                         "source_stage": member[f"{prefix}_source"], "scope_node_id": member["scope_node_id"],
                         "group_id": key, "name": group["name"] if group else member["label"],
                         "dimension": "risk_budget", "risk_attribution_scope": "portfolio",
                         "current": current, "target": target,
                         "gap_pp": (current - target) * 100 if current is not None else None,
                         "breach": None, "threshold_status": "not_configured"})
    if not rows:
        limitations.append("当前分类没有可由连续风险预算或单一风险子成员推导的全组合RC目标；Weight分叉以下留空。")
    return {"status": "available" if rows else "unavailable", "rows": rows, "limitations": list(dict.fromkeys(limitations))}


def _correlations(workspace):
    risk = workspace.get("forward_risk") or {}
    if risk.get("status") != "ok":
        return {"status": "unavailable", "pairs": [], "limitations": risk.get("errors") or ["生产风险模型不可用。"]}
    model = risk["risk_model"]
    active = []
    for index, row in enumerate(workspace["rows"]):
        if row.get("forward_risk_status") == "ok":
            series, starts = _return_series_with_periods(row)
            active.append((_row_key(row, index), row, series, starts))
    if len(active) < 2:
        return {"status": "unavailable", "pairs": [], "limitations": ["少于两个可建模持仓，无法比较相关性。"]}
    try:
        returns, _ = _daily_mark_to_last_return_matrix(active)
        covariance = estimate_covariance(returns, model_id=model["covariance_model_id"], lookback_days=model["lookback_days"],
            parameters=model["parameters"], missing_return_policy=model["missing_return_policy"],
            calculation_frequency=model["resolved_calculation_frequency"], as_of_date=date.fromisoformat(workspace["as_of_date"]))
        pairs = []
        for index, (left_key, left, *_rest) in enumerate(active):
            for right_key, right, *_rest in active[index + 1:]:
                denominator = float(covariance.loc[left_key, left_key] * covariance.loc[right_key, right_key])
                correlation = float(covariance.loc[left_key, right_key]) / sqrt(denominator) if denominator > 0 else None
                pairs.append({"holding_ids": [_holding_id(left), _holding_id(right)],
                    "instrument_ids": [(left.get("instrument_core") or {}).get("instrument_id"), (right.get("instrument_core") or {}).get("instrument_id")],
                    "correlation": correlation})
        return {"status": "ok", "basis": "生产协方差模型隐含相关性，使用与当前RC相同的窗口和收益对齐；不是浏览器自选窗口的样本相关矩阵。",
            "pairs": pairs, "limitations": []}
    except ValueError as error:
        return {"status": "unavailable", "pairs": [], "limitations": [str(error)]}


def project_portfolio_risk(workspace, catalog, previous=None, *, previous_error=None, taxonomy_id=None):
    pid, current_date = workspace["portfolio_id"], workspace["as_of_date"]
    groups, correlations = _groups(workspace, catalog, taxonomy_id), _correlations(workspace)
    risk = workspace.get("forward_risk") or {}
    comparison = {"status": "unavailable", "previous_as_of_date": previous.get("as_of_date") if previous else None,
        "current_as_of_date": current_date, "risk_group_changes": [], "correlation_changes": [],
        "method": "按当前风险模型与当前保存分类重算真实历史持仓；不是当时已保存的PIT风险快照。相邻滚动窗口高度重叠，变动不自动表示统计显著或突然异常。",
        "limitations": [previous_error] if previous_error else []}
    if previous:
        old_risk = previous.get("forward_risk") or {}
        comparable = (previous["as_of_date"] < current_date and workspace["base_currency"] == previous["base_currency"]
            and risk.get("status") == old_risk.get("status") == "ok"
            and risk.get("risk_model") == old_risk.get("risk_model"))
        if comparable:
            old_groups, old_correlations = _groups(previous, catalog, groups["taxonomy_id"]), _correlations(previous)
            if groups["status"] == old_groups["status"] == "ok":
                old_by_id = {row["group_id"]: row for row in old_groups["rows"]}
                current_by_id = {row["group_id"]: row for row in groups["rows"]}
                for group_id in dict.fromkeys([*current_by_id, *old_by_id]):
                    current, old = current_by_id.get(group_id), old_by_id.get(group_id)
                    row = current or old
                    current_share = current["risk_share"] if current else 0.0
                    previous_share = old["risk_share"] if old else 0.0
                    if current_share is None or previous_share is None:
                        continue
                    comparison["risk_group_changes"].append({"group_id": group_id, "name": row["name"],
                        "current": current_share, "previous": previous_share,
                        "change_pp": (current_share - previous_share) * 100,
                        "instrument_ids": row["instrument_ids"], "holding_ids": row["holding_ids"]})
            if correlations["status"] == old_correlations["status"] == "ok":
                old_pairs = {tuple(sorted(row["holding_ids"])): row for row in old_correlations["pairs"]}
                for row in correlations["pairs"]:
                    old = old_pairs.get(tuple(sorted(row["holding_ids"])))
                    if old and row["correlation"] is not None and old["correlation"] is not None:
                        comparison["correlation_changes"].append({**row, "previous": old["correlation"], "change": row["correlation"] - old["correlation"]})
            comparison["status"] = "available" if comparison["risk_group_changes"] or comparison["correlation_changes"] else "unavailable"
            comparison["previous_coverage"] = old_risk.get("coverage")
        else:
            comparison["limitations"].append("前后日期、币种、模型窗口或配置口径不可比，未计算风险突增。")
    elif not previous_error:
        comparison["limitations"].append("没有更早的实际持仓快照，无法判断RC或相关性变化。")
    from portfolio_app.services.portfolio_risk_derivatives import project_derivative_risk
    derivatives = project_derivative_risk(workspace)
    path = f"/portfolios/{quote(pid, safe='')}/risk"
    window_start = (risk.get("coverage") or {}).get("window_start_date")
    frequency = (risk.get("risk_model") or {}).get("resolved_calculation_frequency")
    sources = [{"source_id": f"portfolio-risk:{pid}:{key}", "title": title, "portfolio_id": pid,
        "start_date": start_date, "end_date": current_date, "date_basis": date_basis,
        "currency": workspace["base_currency"], "frequency": source_frequency, "detail_path": path}
        for key, title, start_date, source_frequency, date_basis in [
            ("metrics", "组合生产风险与分类风险贡献", window_start, frequency, "当前风险模型收益窗口"),
            ("targets", "已保存配置目标与当前偏离", None, None, "目标与分类采用当前保存配置；估值与风险观察保留持仓截至日"),
            ("comparison", "历史持仓按同模型重算的风险变化", comparison["previous_as_of_date"], frequency, "两次实际持仓截至日；各自风险窗口按同一模型滚动重算"),
            ("correlations", "生产风险模型隐含相关性", window_start, frequency, "当前风险模型收益窗口"),
        ]]
    for source in sources:
        if source["source_id"] == f"portfolio-risk:{pid}:targets":
            source["end_date"] = current_date
            source["valuation_as_of_date"] = current_date
    fields = ["instrument_core", "quantity", "market_value_base", "cost_basis_base", "unrealized_pnl_base", "unrealized_return_base",
        "instrument_holding_start_date", "quote_as_of_date", "risk_eligible", "holding_kind", "derivative_contract_id", "position_reference_id"]
    compact = {key: workspace.get(key) for key in ["portfolio_id", "portfolio_name", "as_of_date", "base_currency", "totals", "risk_basis", "forward_risk", "quality_warnings"]}
    compact["rows"] = [{**{key: row.get(key) for key in fields}, "holding_id": _holding_id(row),
        "detail_path": f"/portfolios/{quote(pid, safe='')}/holdings/{quote(str(_holding_id(row)), safe='')}" if _holding_id(row) else None} for row in workspace["rows"]]
    holdings = {row["holding_id"]: {"holding_id": row["holding_id"],
        "name": (row.get("instrument_core") or {}).get("instrument_name") or row["holding_id"],
        "instrument_id": (row.get("instrument_core") or {}).get("instrument_id"), "detail_path": row["detail_path"]}
        for row in compact["rows"] if row["holding_id"] and row.get("holding_kind") == "position"}
    for position in derivatives["positions"]:
        holdings[position["holding_id"]] = {key: position[key] for key in ["holding_id", "name", "detail_path"]}
    derivative_sources = [{**source, "portfolio_id": pid, "date_basis": "当前持仓及合约条款截至日；底层行情日期分别保留在报价中"}
        for source in derivatives["sources"]]
    return {"portfolio_id": pid, "name": workspace.get("portfolio_name"), "as_of_date": current_date,
        "base_currency": workspace["base_currency"], "source_id": f"portfolio-risk:{pid}", "workspace": compact,
        "holdings": list(holdings.values()),
        "portfolio_metrics": {"forward_risk": risk, "risk_basis": workspace.get("risk_basis"), "groups": groups, "correlations": correlations,
            "coverage_note": "risk_basis记录全样本收益路径及频率覆盖，gap_instrument_ids等缺口仍然有效；forward_risk只代表其明确窗口内可建模持仓的生产RC。当前窗口覆盖完整不能推断全样本路径或全组合所有风险完整。"},
        "targets": _targets(catalog, groups), "comparisons": comparison, "derivatives": derivatives, "sources": [*sources, *derivative_sources],
        "limitations": ["尚未配置RC变化或相关性变化的报警阈值；仅提供真实变化供研判。"]}


def read_portfolio_risk_context(portfolio_id: str, *, as_of_date: date | None = None):
    from fastapi import HTTPException
    from portfolio_app.services.holdings_workspace import holdings_workspace
    from portfolio_app.api.routes.taxonomies import get_portfolio_taxonomies
    from portfolio_app.services.daily_snapshots import PortfolioCalculationUnavailable
    workspace = holdings_workspace(portfolio_id=portfolio_id, as_of_date=as_of_date, include_details=True)
    catalog = get_portfolio_taxonomies(portfolio_id, include_market_profile=False).model_dump(mode="json")
    with get_session_factory()() as session:
        previous_date = session.scalar(select(PortfolioDailySnapshotModel.as_of_date).where(
            PortfolioDailySnapshotModel.portfolio_id == portfolio_id,
            PortfolioDailySnapshotModel.as_of_date < date.fromisoformat(workspace["as_of_date"]))
            .order_by(PortfolioDailySnapshotModel.as_of_date.desc()).limit(1))
    previous, error = None, None
    if previous_date:
        try:
            previous = holdings_workspace(portfolio_id=portfolio_id, as_of_date=previous_date, include_details=True)
        except (HTTPException, PortfolioCalculationUnavailable, ValueError) as exc:
            error = f"历史持仓风险重算暂不可用：{getattr(exc, 'detail', str(exc))}"
    result = project_portfolio_risk(workspace, catalog, previous, previous_error=error)
    from portfolio_app.services.concentration import ConcentrationUnavailable, read_portfolio_concentration
    from portfolio_app.services.tail_risk import read_portfolio_tail_risk
    try:
        concentration = read_portfolio_concentration(portfolio_id, workspace=workspace)
    except ConcentrationUnavailable as unavailable:
        # A missing account-level slice is a known coverage state. Preserve the
        # other valid risk modules, while giving DSH no invented concentration.
        source_id = f"portfolio-concentration:{portfolio_id}:{workspace['as_of_date']}:unavailable"
        concentration = {"portfolio_id": portfolio_id, "as_of_date": workspace["as_of_date"], "status": "unavailable",
            "source_id": source_id, "base_currency": workspace["base_currency"], "nav": workspace.get("totals", {}).get("nav"),
            "weight_basis": "portfolio_nav", "scopes": [], "coverage": [unavailable.detail],
            "sources": [{"source_id": source_id, "source_type": "portfolio_concentration", "portfolio_id": portfolio_id,
                "title": "Concentration unavailable: account-level holdings missing", "end_date": workspace["as_of_date"],
                "detail_path": f"/portfolios/{quote(portfolio_id, safe='')}/holdings?view=concentration"}]}
    tail_risk = read_portfolio_tail_risk(portfolio_id, workspace=workspace)
    result["concentration"] = concentration
    result["tail_risk"] = tail_risk
    result["sources"].extend(concentration["sources"])
    result["sources"].extend(tail_risk.get("sources", []))
    # A browser grouping is not a monitoring preference. Read all configured
    # target classifications, keeping the same production risk observations.
    result["targets_by_taxonomy"] = []
    result["portfolio_metrics"]["groups_by_taxonomy"] = []
    for taxonomy in catalog.get("taxonomies", []):
        if taxonomy.get("status", "active") != "active":
            continue
        target_source_id = f"portfolio-risk:{portfolio_id}:targets:{taxonomy['taxonomy_id']}"
        taxonomy_groups = _groups(workspace, catalog, taxonomy["taxonomy_id"])
        result["portfolio_metrics"]["groups_by_taxonomy"].append({"name": taxonomy["name"], **taxonomy_groups})
        result["targets_by_taxonomy"].append({"taxonomy_id": taxonomy["taxonomy_id"], "name": taxonomy["name"], "source_id": target_source_id,
            **_targets(catalog, taxonomy_groups)})
        result["sources"].append({"source_id": target_source_id, "source_type": "portfolio_taxonomy_targets", "portfolio_id": portfolio_id,
            "taxonomy_id": taxonomy["taxonomy_id"], "title": f"{taxonomy['name']} targets", "start_date": None,
            "end_date": workspace["as_of_date"], "valuation_as_of_date": workspace["as_of_date"], "date_basis": "当前保存目标；日期为对应持仓估值截至日", "detail_path": f"/portfolios/{quote(portfolio_id, safe='')}/risk"})
    return result
