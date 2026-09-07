"""Read-only PM projection of the existing, enriched holdings workspace.

No pricing, barrier-path reconstruction, ledger changes or Watchlist membership
is involved. Contract terms and dated quotes remain separate from conditional
settlement mechanics and from actual lifecycle records.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from urllib.parse import quote


def _pick(row: dict, *keys: str) -> dict:
    return {key: deepcopy(row.get(key)) for key in keys}


def _option(row: dict, terms: dict, coverage: list[str]) -> tuple[dict, list[dict]]:
    risk = row.get("option_risk") or {}
    underlying = {
        "instrument_id": terms.get("underlying_instrument_id"),
        "instrument_name": risk.get("underlying_name"),
        "currency": risk.get("underlying_quote_currency"),
        "spot": risk.get("underlying_spot"),
        "quote_as_of_date": risk.get("underlying_quote_as_of_date"),
        "quote_status": risk.get("underlying_quote_status"),
    }
    written = row.get("holding_kind") == "option_obligation"
    strike_currency = terms.get("strike_currency")
    quote_currency = underlying["currency"]
    comparable = bool(strike_currency and strike_currency == quote_currency)
    if not strike_currency:
        coverage.append("合约未明确执行价币种；工作区推定的币种仅保留为参考，未据此确认价内程度或结算金额。")
    elif not comparable:
        coverage.append("执行价与底层报价币种不同或报价币种未知；未直接比较价格，也未推算跨币种结算。")
    if not terms.get("settlement_type"):
        coverage.append("缺少现金或实物结算条款，不能将执行价名义额直接列为现金支付或接票义务。")
    if not terms.get("exercise_style"):
        coverage.append("缺少行权方式，不能据到期日排除提前行权或被指派。")
    result = {
        "side": "written" if written else "long",
        "option_type": terms.get("option_type"),
        "open_contract_quantity": row.get("open_contract_quantity"),
        "underlying_quantity_reference": row.get("required_underlying_quantity"),
        "strike_notional_reference": row.get("strike_notional"),
        "workspace_strike_currency_reference": row.get("strike_currency"),
        "strike_currency": strike_currency,
        "settlement_type": terms.get("settlement_type"),
        "days_to_expiry": risk.get("days_to_expiry"),
        "obligation_status": row.get("obligation_status"),
        "moneyness_ratio": risk.get("moneyness_pct") if comparable else None,
        "intrinsic_value_per_share": risk.get("intrinsic_value_per_share") if comparable else None,
        "conditional_physical_settlement": None,
    }
    # All amounts describe a fully exercised/assigned position, not a forecast
    # or today's payable. In particular, cash-settled options do not deliver stock.
    quantity = row.get("required_underlying_quantity")
    strike = terms.get("strike")
    if terms.get("settlement_type") == "physical" and quantity is not None and strike is not None and strike_currency:
        receives_shares = (terms.get("option_type") == "call") != written
        result["conditional_physical_settlement"] = {
            "condition": "仅在全部当前未平仓合约实际行权或被指派、按实物交割时成立；不是预计发生概率或当前应付款。",
            "role": "assignment_obligation" if written else "exercise_right",
            "underlying_instrument_id": underlying["instrument_id"],
            "shares_in": quantity if receives_shares else 0,
            "shares_out": 0 if receives_shares else quantity,
            "cash_out": float(Decimal(str(quantity)) * Decimal(str(strike))) if receives_shares else 0,
            "cash_in": 0 if receives_shares else float(Decimal(str(quantity)) * Decimal(str(strike))),
            "cash_currency": strike_currency,
            "fees_and_taxes_included": False,
        }
    elif terms.get("settlement_type") == "cash":
        coverage.append("现金结算金额取决于合约结算公式和结算价；没有将全额执行价名义额当作应付现金。")
    if written:
        coverage.append("卖出期权账面权利金负债不是回购价格或最大损失；现金及股票参考余额不代表已划拨保证金或已确认备兑。")
    return result, [underlying]


def _fcn(row: dict, terms: dict, as_of_date: str, coverage: list[str]) -> tuple[dict, list[dict]]:
    risk = row.get("fcn_risk") or {}
    quoted = {item["instrument_id"]: item for item in risk.get("underlyings", [])}
    underlyings = []
    for term in terms.get("underlyings", []):
        item = deepcopy(quoted.get(term.get("instrument_id"), {}))
        item["instrument_id"] = term.get("instrument_id")
        item["terms"] = deepcopy(term)
        underlyings.append(item)
    missing = [key for key in ("final_observation_date", "knock_in_observation", "settlement_type", "payoff_description") if not terms.get(key)]
    if missing:
        coverage.append("FCN 关键条款缺失：" + "、".join(missing) + "；不能确认最终接票条件或交付数量。")
    if any(item.get("missing_terms") for item in underlyings):
        coverage.append("部分底层初始价或执行价/障碍比例缺失；对应距离不能计算。")
    coverage.append("当前报价越过障碍不等于已确认敲入、敲出或确定接票；生命周期状态只沿用已登记事件。")
    coverage.append("notional 是原合约名义本金，未重建剩余本金、实际交付标的或交付股数；账面价值不等于最大损失。")
    result = {
        "original_contract_notional": terms.get("notional"),
        "lifecycle_status": risk.get("lifecycle_status"),
        "current_risk_state": risk.get("risk_state"),
        "delivery_buffer_underlying_instrument_id": risk.get("delivery_buffer_underlying_instrument_id"),
        "next_recorded_knock_out_observation_date": min((str(value) for value in terms.get("knock_out_observation_dates") or [] if str(value) >= as_of_date), default=None),
        "next_recorded_coupon_payment_date": min((str(value) for value in terms.get("coupon_payment_dates") or [] if str(value) >= as_of_date), default=None),
    }
    return result, underlyings


def project_derivative_risk(workspace: dict) -> dict:
    """Project open holding rows; a registered contract alone is not exposure."""
    portfolio_id = str(workspace["portfolio_id"])
    as_of_date = str(workspace.get("as_of_date") or "")
    positions, sources = [], []
    for row in workspace.get("rows", []):
        contract = row.get("derivative_contract") or {}
        kind = contract.get("contract_type")
        if kind not in {"fcn", "option"} or row.get("holding_kind") not in {"derivative_contract", "option_obligation"}:
            continue
        holding_id = str(row.get("derivative_contract_id") or contract.get("derivative_contract_id") or row["position_reference_id"])
        terms = deepcopy(contract.get("terms") or {})
        coverage: list[str] = []
        if kind == "option":
            analysis, underlyings = _option(row, terms, coverage)
        else:
            analysis, underlyings = _fcn(row, terms, as_of_date, coverage)
        if any(item.get("spot") is None or item.get("quote_status") != "complete" for item in underlyings):
            coverage.append("部分底层有效报价缺失，不能完整判断当前价格相对条款的位置。")
        if any(item.get("quote_as_of_date") and str(item["quote_as_of_date"]) < as_of_date for item in underlyings):
            coverage.append("部分底层报价早于持仓日期；必须按各报价日期解释，不能称为当日即时价格。")
        if row.get("fair_value_coverage_status") != "complete":
            coverage.append("没有完整衍生品公允价值；保留原账面估值口径，不能据此认定当前清算损失。")
        detail_path = f"/portfolios/{quote(portfolio_id, safe='')}/holdings/{quote(holding_id, safe='')}"
        source_id = f"portfolio-derivative:{portfolio_id}:{holding_id}"
        name = contract.get("contract_name") or holding_id
        positions.append({
            "holding_id": holding_id,
            "source_id": source_id,
            "name": name,
            "detail_path": detail_path,
            "contract_type": kind,
            "contract_currency": contract.get("currency"),
            "account_ids": deepcopy(row.get("account_ids") or []),
            "terms": terms,
            "underlyings": underlyings,
            "position": _pick(row, "line_id", "position_reference_id", "holding_kind", "quantity", "market_value", "market_value_base", "allocation", "valuation_basis", "fair_value", "fair_value_coverage_status", "cost_basis", "cost_basis_base", "is_liability", "risk_eligible", "premium_basis_remaining", "liability_value", "fx_rate_as_of_date", "fx_rate_stale"),
            kind: analysis,
            "coverage": coverage,
        })
        sources.append({
            "source_id": source_id,
            "source_type": "portfolio_derivative",
            "holding_id": holding_id,
            "holding_ids": [holding_id],
            "instrument_ids": [item["instrument_id"] for item in underlyings if item.get("instrument_id")],
            "title": f"{name} · 持仓及合约条款",
            "detail_path": detail_path,
            "start_date": as_of_date or None,
            "end_date": as_of_date or None,
            "currency": contract.get("currency"),
        })
    underlying_ids = {item["instrument_id"] for position in positions for item in position["underlyings"] if item.get("instrument_id")}
    cash, underlying_positions = [], []
    for row in workspace.get("rows", []) if positions else []:
        core = row.get("instrument_core") or {}
        if row.get("holding_kind") == "settled_cash":
            cash.append({**_pick(row, "line_id", "account_ids", "market_value", "market_value_base", "cash_purpose", "available_for_trading", "collateral_reference", "financing_liability", "quote_as_of_date"), "currency": core.get("currency")})
        elif row.get("holding_kind") == "position" and core.get("instrument_id") in underlying_ids:
            underlying_positions.append({**_pick(row, "position_reference_id", "account_ids", "quantity", "available_for_trading", "collateral_reference"), "instrument_id": core["instrument_id"]})
    return {
        "portfolio_id": portfolio_id,
        "as_of_date": as_of_date or None,
        "base_currency": workspace.get("base_currency"),
        "positions": positions,
        "resources": {"cash": cash, "underlying_positions": underlying_positions},
        "sources": sources,
        "coverage": [
            "仅包含当前工作区的持仓/未平仓义务；单纯登记的历史合约不代表当前敞口。",
            "allocation 与价格距离/价内程度为小数比率（0.1 表示 10%）；合约 level_pct/coupon_rate_pct 仍为百分数原义。",
            "现金、直接股票参考余额只列一次，保留账户、用途与融资信息；不确认跨账户可调拨、保证金可用性或净额结算。",
            "条件交割按各合约单列，不将名义本金、执行价名义额或底层股数作为直接持仓市值占比或风险贡献。",
            "未计算行权/敲入概率、Greeks、未来波动率或新衍生品定价。",
        ],
    }
