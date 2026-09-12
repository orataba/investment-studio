"""Principal allocation and direct holding concentration; never derivative pricing.

Every FCN contributes its remaining principal once to a taxonomy. Equal splitting
is an explicit management convention, not a worst-of loss allocation. Options,
cash and settlement balances stay in NAV but not in these exposure numerators.
"""
from copy import deepcopy
from datetime import date
from math import isfinite
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select

from portfolio_app.db.models import PortfolioDailyHoldingSnapshotModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.concentration_settings import empty_settings, read_concentration_settings


class ConcentrationUnavailable(HTTPException):
    def __init__(self):
        super().__init__(503, "Account-level holdings are unavailable for concentration. Refresh holdings before retrying.")


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if isfinite(result) else None


def _row(entity_id, name, *, depth=0, parent=None):
    return {"entity_id": entity_id, "name": name, "depth": depth, "parent_entity_id": parent,
            "security_exposure_base": 0.0, "fcn_exposure_base": 0.0, "sources": [], "coverage": [], "_missing": False}


def _add(row, source, kind):
    row["sources"].append(deepcopy(source))
    value = number(source.get("amount_base"))
    if value is None:
        row["_missing"] = True
        row["coverage"].append(f"Missing exposure: {source['title']}.")
    else:
        row[f"{kind}_exposure_base"] += value


def _finish(row, nav, rules, scope, taxonomy_id):
    exact = next((r for r in rules if r["scope"] == scope and r.get("taxonomy_id") == taxonomy_id and r.get("entity_id") == row["entity_id"]), None)
    default = next((r for r in rules if r["scope"] == scope and r.get("taxonomy_id") == taxonomy_id and r.get("entity_id") is None), None)
    # A disabled scope default is the master switch; retain overrides for re-enabling.
    rule = default if default is not None and not default.get("enabled", True) else exact or default
    # An unknown classification may span many nodes; its sum cannot prove that
    # any one real category has breached the default category limit.
    if scope == "taxonomy" and row["entity_id"] == f"unassigned:{taxonomy_id}":
        rule = None
    active = rule is not None and rule.get("enabled", True)
    watch = number(rule.get("watch_weight")) if active else None
    limit = number(rule.get("limit_weight")) if active else None
    amount = row["security_exposure_base"] + row["fcn_exposure_base"]
    missing = row.pop("_missing")
    known_weight = amount / nav if nav is not None and nav > 0 else None
    weight = known_weight if not missing else None
    # Missing positive exposures cannot reverse an already demonstrated breach.
    if known_weight is not None and limit is not None and known_weight >= limit:
        status = "breached"
    elif missing or known_weight is None:
        status = "unavailable"
    elif watch is None and limit is None:
        status = "unconfigured"
    elif watch is not None and known_weight >= watch:
        status = "watch"
    else:
        status = "within"
    row.update(exposure_base=None if missing else amount, known_exposure_base=amount,
               weight=weight, lower_bound_weight=known_weight if missing else None,
               watch_weight=watch, limit_weight=limit, status=status,
               headroom_weight=limit - weight if limit is not None and weight is not None else None,
               rule_id=rule["rule_id"] if rule else None,
               coverage=list(dict.fromkeys(row["coverage"])))
    return row


def _scope(scope, name, rows, nav, rules, *, taxonomy_id=None, coverage=None):
    finalized = [_finish(row, nav, rules, scope, taxonomy_id) for row in rows]
    if scope != "taxonomy":
        finalized.sort(key=lambda row: row["known_exposure_base"], reverse=True)
    status = "unavailable" if nav is None or nav <= 0 else "partial" if any(row["coverage"] for row in finalized) or coverage else "complete"
    return {"scope": scope, "taxonomy_id": taxonomy_id, "name": name, "rows": finalized, "status": status, "coverage": coverage or []}


def project_portfolio_concentration(workspace: dict, catalog: dict, settings: dict | None = None, *, holding_rows: list[dict] | None = None) -> dict:
    pid = workspace["portfolio_id"]
    settings = settings or empty_settings(pid)
    nav = number((workspace.get("totals") or {}).get("nav"))
    base_currency = workspace.get("base_currency")
    rules = settings.get("rules", [])
    allocations = {item["contract_id"]: item for item in settings.get("fcn_allocations", [])}
    enriched = {(row.get("position_reference_id") or row.get("derivative_contract_id"), row.get("holding_kind", "position")): row for row in workspace.get("rows", [])}
    security_rows, fcn_rows, allocated_sources, sources, fcn_contracts = {}, {}, [], [], []
    issues = []
    excluded_options = 0
    input_rows = holding_rows if holding_rows is not None else workspace.get("rows", [])
    if holding_rows == [] and any(number(row.get("quantity")) != 0 for row in workspace.get("rows", [])):
        raise ConcentrationUnavailable()
    for index, original in enumerate(input_rows):
        row = {**enriched.get((original.get("position_reference_id") or original.get("derivative_contract_id"), original.get("holding_kind", "position")), {}), **original}
        core = row.get("instrument_core") or row.get("instrument_ref") or {}
        kind = row.get("holding_kind", "position")
        quantity = number(row.get("quantity"))
        if quantity == 0:
            continue
        contract = row.get("derivative_contract") or {}
        if kind == "option_obligation" or contract.get("contract_type") == "option":
            excluded_options += 1
            continue
        if kind == "position" and core.get("instrument_type") not in {"cash", "fx", "index"}:
            iid = core.get("instrument_id") or row.get("instrument_id")
            if not iid or str(iid).startswith("cash:"):
                continue
            name = core.get("instrument_name") or iid
            value = number(row.get("market_value_base"))
            source = {"source_id": f"concentration:{pid}:security:{iid}:{row.get('account_id') or index}",
                      "source_type": "portfolio_concentration", "portfolio_id": pid, "end_date": workspace.get("as_of_date"),
                      "title": name, "instrument_id": iid, "amount_base": abs(value) if value is not None else None,
                      "account_id": row.get("account_id"), "quote_as_of_date": row.get("quote_as_of_date"),
                      "detail_path": f"/portfolios/{quote(pid, safe='')}/holdings/{quote(iid, safe='')}"}
            _add(security_rows.setdefault(iid, _row(iid, name)), source, "security")
            allocated_sources.append((iid, source, "security"))
            sources.append(source)
        elif kind == "derivative_contract" and contract.get("contract_type") == "fcn":
            cid = row.get("derivative_contract_id") or contract.get("derivative_contract_id")
            if not cid:
                issues.append("An FCN holding has no contract reference.")
                continue
            name = contract.get("contract_name") or cid
            terms = contract.get("terms") or {}
            notional = number(terms.get("notional"))
            rate = 1.0 if contract.get("currency") == base_currency else number(row.get("fx_rate_to_base"))
            if rate is not None and (rate <= 0 or row.get("fx_rate_stale")):
                rate = None
            amount = quantity * notional * rate if quantity is not None and quantity > 0 and notional is not None and notional > 0 and rate is not None else None
            source = {"source_id": f"concentration:{pid}:fcn:{cid}:{row.get('account_id') or index}",
                      "source_type": "portfolio_concentration", "portfolio_id": pid, "end_date": workspace.get("as_of_date"),
                      "title": name, "contract_id": cid, "amount_base": amount,
                      "fx_rate_as_of_date": row.get("fx_rate_as_of_date"),
                      "detail_path": f"/portfolios/{quote(pid, safe='')}/holdings/{quote(cid, safe='')}"}
            _add(fcn_rows.setdefault(cid, _row(cid, name)), source, "fcn")
            sources.append(source)
            underlyings = terms.get("underlyings") or []
            ids = [item.get("instrument_id") for item in underlyings]
            allocation = allocations.get(cid, {"contract_id": cid, "method": "equal", "weights": []})
            quoted = {item.get("instrument_id"): item for item in (row.get("fcn_risk") or {}).get("underlyings", [])}
            if not any(item["contract_id"] == cid for item in fcn_contracts):
                fcn_contracts.append({"contract_id": cid, "name": name, "underlyings": [
                    {"instrument_id": iid, "name": quoted.get(iid, {}).get("instrument_name") or quoted.get(iid, {}).get("name")
                     or (catalog.get("instrument_names") or {}).get(iid) or iid}
                    for iid in ids if iid], "allocation": deepcopy(allocation)})
            if not ids or any(not iid for iid in ids) or len(ids) != len(set(ids)):
                issues.append(f"FCN linked securities are missing or ambiguous: {name}.")
                allocated_sources.append((None, source, "fcn"))
                continue
            weights = ({iid: 1 / len(ids) for iid in ids} if allocation["method"] == "equal"
                       else {item["instrument_id"]: number(item["weight"]) for item in allocation["weights"]})
            if set(weights) != set(ids) or any(value is None or value < 0 for value in weights.values()) or abs(sum(weights.values()) - 1) > 1e-9:
                issues.append(f"FCN allocation does not match its linked securities: {name}.")
                allocated_sources.append((None, source, "fcn"))
                continue
            for iid, weight in weights.items():
                if weight == 0:
                    continue
                piece = {**source, "source_id": f"{source['source_id']}:{iid}", "instrument_id": iid,
                         "allocation_weight": weight, "allocation_method": allocation["method"],
                         "amount_base": amount * weight if amount is not None else None}
                allocated_sources.append((iid, piece, "fcn"))
                sources.append(piece)
    scopes = [
        _scope("security", "Direct securities", list(security_rows.values()), nav, rules),
        _scope("fcn", "FCN contracts", list(fcn_rows.values()), nav, rules),
    ]
    for taxonomy in catalog.get("taxonomies", []):
        if taxonomy.get("status", "active") != "active":
            continue
        tid = taxonomy["taxonomy_id"]
        nodes = {node["taxonomy_node_id"]: node for node in catalog.get("taxonomy_nodes", []) if node["taxonomy_id"] == tid and node.get("status", "active") == "active"}
        assignments = {}
        for item in catalog.get("taxonomy_assignments", []):
            if item["taxonomy_id"] == tid and item.get("target_scope") == "instrument" and item.get("status", "active") == "active":
                assignments.setdefault(item["target_entity_id"], []).append(item["taxonomy_node_id"])
        groups = {nid: _row(nid, node["node_name"], parent=node.get("parent_taxonomy_node_id")) for nid, node in nodes.items()}
        unassigned = _row(f"unassigned:{tid}", "Unclassified")
        limitations = list((catalog.get("concentration_taxonomy_limitations") or {}).get(tid, []))
        paths = {}
        for nid in nodes:
            path, current = [], nid
            while current:
                if current in path or current not in nodes:
                    path = []
                    limitations.append("Taxonomy hierarchy is incomplete or cyclic.")
                    break
                path.append(current)
                current = nodes[current].get("parent_taxonomy_node_id")
            paths[nid] = path
            groups[nid]["depth"] = max(len(path) - 1, 0)
        for iid, source, kind in allocated_sources:
            matches = assignments.get(iid, [])
            path = paths.get(matches[0], []) if len(matches) == 1 else []
            if not path:
                _add(unassigned, source, kind)
                continue
            for nid in path:
                _add(groups[nid], source, kind)
        if unassigned["sources"]:
            limitations.append("Unclassified exposure is retained; classified amounts may be lower bounds.")
            for group in groups.values():
                group["_missing"] = True
                group["coverage"].append("Unclassified exposure may also belong to this group.")
        for group in groups.values():
            if limitations and not unassigned["sources"]:
                group["_missing"] = True
                group["coverage"].extend(limitations)
        def sort_key(nid):
            return tuple((nodes[item].get("sort_order", 0), nodes[item]["node_name"]) for item in reversed(paths[nid]))
        ordered = [groups[nid] for nid in sorted(groups, key=sort_key)]
        if unassigned["sources"]:
            ordered.append(unassigned)
        scopes.append(_scope("taxonomy", taxonomy["name"], ordered, nav, rules, taxonomy_id=tid, coverage=limitations))
        scopes[-1]["taxonomy_configuration"] = (catalog.get("concentration_taxonomy_configurations") or {}).get(tid)
    coverage = [
        "Gross direct security market value and remaining FCN principal / current portfolio NAV; options, cash and settlements are excluded from numerators.",
        "FCN principal is allocated once per taxonomy. Equal allocation is a management convention, not a loss allocation or risk diversification claim.",
        "Direct security limits exclude FCN underlying allocations. FCN limits use remaining contracts × principal per contract, not carrying cost.",
        "NAV retains the portfolio operating-book valuation basis; FCNs and options have no daily fair-value coverage.",
    ]
    if nav is None or nav <= 0:
        issues.append("A positive portfolio NAV is required for concentration weights.")
    source_id = f"portfolio-concentration:{pid}:{workspace.get('as_of_date')}:{settings['revision']}"
    return {"portfolio_id": pid, "as_of_date": workspace.get("as_of_date"), "base_currency": base_currency,
            "nav": nav, "weight_basis": "portfolio_nav", "valuation_basis": "operating_book", "source_id": source_id,
            "status": "unavailable" if nav is None or nav <= 0 else "partial" if issues or any(scope["status"] != "complete" for scope in scopes) else "complete",
            "settings_revision": settings["revision"], "settings_effective_from": settings.get("effective_from"),
            "scopes": scopes, "fcn_contracts": fcn_contracts, "coverage": [*coverage, *issues], "excluded_option_positions": excluded_options,
            "sources": [{"source_id": source_id, "title": "Portfolio concentration and configured limits", "portfolio_id": pid,
                         "source_type": "portfolio_concentration", "start_date": workspace.get("as_of_date"), "end_date": workspace.get("as_of_date"),
                         "currency": base_currency, "detail_path": f"/portfolios/{quote(pid, safe='')}/risk"}, *sources]}


def read_portfolio_concentration(portfolio_id: str, *, as_of_date: date | None = None, workspace: dict | None = None) -> dict:
    from portfolio_app.api.routes.workspace import _resolve_holdings_request
    from portfolio_app.api.routes.taxonomies import get_portfolio_taxonomies
    from portfolio_app.services.analytics_scope import taxonomy_configuration_as_of_in_session
    from portfolio_app.services.workspace_cache import get_cached_materialized_holdings_workspace
    from portfolio_app.services.instrument_registry import InstrumentRegistryError, get_registry_instrument_summaries
    if workspace is None:
        _, effective_date = _resolve_holdings_request(portfolio_id, as_of_date)
        workspace = get_cached_materialized_holdings_workspace(portfolio_id, as_of_date=effective_date)
        if workspace is None:
            raise ConcentrationUnavailable()
    effective_date = date.fromisoformat(workspace["as_of_date"])
    if workspace.get("portfolio_id") != portfolio_id:
        raise ValueError("Concentration workspace belongs to another portfolio.")
    if as_of_date is not None and effective_date != as_of_date:
        raise ValueError("Concentration workspace date differs from the requested date.")
    settings = read_concentration_settings(portfolio_id, as_of_date=effective_date)
    catalog = get_portfolio_taxonomies(portfolio_id, include_market_profile=False).model_dump(mode="json")
    with get_session_factory()() as session:
        records = list(session.scalars(select(PortfolioDailyHoldingSnapshotModel).where(
            PortfolioDailyHoldingSnapshotModel.portfolio_id == portfolio_id,
            PortfolioDailyHoldingSnapshotModel.as_of_date == effective_date)))
        # Preserve each account's gross security exposure before workspace netting.
        rows = [{**deepcopy(record.holding_json), "account_id": record.account_id,
                 "position_reference_id": record.position_reference_id, "holding_kind": record.holding_kind,
                 "instrument_id": record.instrument_id, "derivative_contract_id": record.derivative_contract_id,
                 "quantity": record.quantity, "market_value_base": record.market_value_base} for record in records]
        dated_taxonomies, dated_nodes, dated_assignments, limitations, configurations = [], [], [], {}, {}
        for taxonomy in catalog["taxonomies"]:
            tid = taxonomy["taxonomy_id"]
            configuration = taxonomy_configuration_as_of_in_session(session, portfolio_id, tid, effective_date)
            if configuration is None:
                dated_taxonomies.append(taxonomy)
                limitations[tid] = ["No classification configuration is effective on the holding date."]
            else:
                dated_taxonomies.append(configuration.get("taxonomy") or taxonomy)
                configurations[tid] = {key: configuration.get(key) for key in ("taxonomy_configuration_revision_id", "configuration_version", "effective_from", "effective_to")}
                dated_nodes.extend(configuration.get("taxonomy_nodes", []))
                dated_assignments.extend(configuration.get("taxonomy_assignments", []))
        catalog = {**catalog, "taxonomies": dated_taxonomies, "taxonomy_nodes": dated_nodes, "taxonomy_assignments": dated_assignments,
                   "concentration_taxonomy_limitations": limitations, "concentration_taxonomy_configurations": configurations}
    # Concentration uses dated account holdings and contract principal, not return
    # histories or market-risk analytics. Only linked-security display names may
    # need current registry metadata; never replace saved quotes, FX or terms.
    linked_ids, names = set(), {}
    for row in [*workspace.get("rows", []), *rows]:
        contract = row.get("derivative_contract") or {}
        if contract.get("contract_type") != "fcn":
            continue
        linked_ids.update(item["instrument_id"] for item in (contract.get("terms") or {}).get("underlyings", [])
                          if item.get("instrument_id"))
        for item in (row.get("fcn_risk") or {}).get("underlyings", []):
            name = item.get("instrument_name") or item.get("name")
            if item.get("instrument_id") and name:
                names.setdefault(item["instrument_id"], name)
    if missing_names := linked_ids - names.keys():
        try:
            summaries = get_registry_instrument_summaries(missing_names)
        except InstrumentRegistryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        names.update({iid: item["instrument_name"] for iid, item in summaries.items()
                      if item and item.get("instrument_name")})
    catalog["instrument_names"] = names
    return project_portfolio_concentration(workspace, catalog, settings, holding_rows=rows)
