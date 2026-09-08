from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from math import isfinite
from zipfile import BadZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.cell import Cell
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

from portfolio_app.services.transaction_csv import (
    IMPORT_COLUMNS,
    MAX_CSV_BYTES,
    MAX_CSV_ROWS,
    REQUIRED_COLUMNS,
    transaction_export_rows,
)
from portfolio_app.services.transaction_import import (
    ASSET_TYPE_VALUES,
    TRANSACTION_ACTIONS,
)

TRANSACTION_SHEET_NAME = "Transactions"
INSTRUCTIONS_SHEET_NAME = "Instructions"
FIELD_GUIDE_SHEET_NAME = "Field Guide"
EXAMPLES_SHEET_NAME = "Examples"
LISTS_SHEET_NAME = "Lists"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEMPLATE_INPUT_LAST_ROW = MAX_CSV_ROWS + 1

DATE_COLUMNS = frozenset(
    {
        "trade_date",
        "settlement_date",
        "position_effective_date",
        "entitlement_date",
        "acquisition_date",
        "option_expiry_date",
        "fcn_issue_date",
        "fcn_final_observation_date",
        "fcn_maturity_date",
    }
)
NUMERIC_COLUMNS = frozenset(
    {
        "option_strike",
        "option_contract_multiplier",
        "fcn_notional",
        "fcn_annual_coupon_rate_pct",
        "quantity",
        "price",
        "gross_amount",
        "counter_amount",
        "fx_rate",
        "fees",
        "taxes",
    }
)

HEADER_FILL = PatternFill(fill_type="solid", fgColor="1F2937")
HEADER_FONT = Font(color="FFFFFF", bold=True)
REQUIRED_HEADER_FILL = PatternFill(fill_type="solid", fgColor="B45309")
DROPDOWN_HEADER_FILL = PatternFill(fill_type="solid", fgColor="075985")
TITLE_FILL = PatternFill(fill_type="solid", fgColor="0F766E")
SECTION_FILL = PatternFill(fill_type="solid", fgColor="E2E8F0")
EXAMPLE_FILL = PatternFill(fill_type="solid", fgColor="ECFDF5")
WARNING_FILL = PatternFill(fill_type="solid", fgColor="FEF3C7")
REFERENCE_FILL = PatternFill(fill_type="solid", fgColor="EFF6FF")
INVALID_FILL = PatternFill(fill_type="solid", fgColor="FECACA")
THIN_GRAY_BORDER = Border(bottom=Side(style="thin", color="CBD5E1"))

FEE_CATEGORY_VALUES = (
    "financing_interest", "borrow_fee", "payment_in_lieu",
    "unknown",
    "transaction_cost",
    "management_fee",
    "custody_fee",
    "administration_fee",
    "performance_fee",
    "other",
)
CURRENCY_VALUES = ("USD", "HKD", "CNY", "EUR", "GBP", "CHF")
OPTION_TYPE_VALUES = ("call", "put")

ASSET_TYPE_GUIDANCE = {
    "security": "股票、ETF、公募基金、私募基金及其他 Registry 证券",
    "fcn": "Fixed Coupon Note 合约交易、收益及结束结果",
    "option": "Call/Put 期权长短仓期初、开平仓、到期、现金结算及实物交割",
    "cash": "现金流、换汇、费用、税费和现金账户操作",
}
TRANSACTION_ACTION_GUIDANCE = {
    "short_sell": "股票/ETF 实际卖空；先处置已有多头，差额建立空头",
    "buy_to_cover": "股票/ETF 买回已有空头，不超出待买回数量",
    "short_opening_balance": "期初已存在的股票/ETF 空头；正数量及剩余净账面负债，不重复产生现金；原开仓日填 acquisition_date",
    "physical_long": "期权多头实物行权；option_delivery_json 必填，原子生成股票腿，不另记交付成交",
    "physical_written": "卖出期权实物指派；option_delivery_json 必填，原子生成股票腿，不另记交付成交",
    "buy": "买入；基金对应申购",
    "sell": "卖出；基金对应赎回",
    "dividend": "现金分红",
    "dividend_reinvestment": "红利再投资形成证券份额",
    "return_of_capital": "返还资本",
    "entry": "进入 FCN 合约",
    "early_exit": "FCN 提前退出",
    "coupon": "FCN 票息收入",
    "knock_in_close": "FCN 敲入结果并结束合约",
    "knock_in_observation": "记录已确认敲入；不关闭持仓、不动现金，note 填写观察依据",
    "knock_out_close": "FCN 敲出结果并结束合约",
    "maturity_close": "FCN 正常到期并结束合约",
    "buy_to_open": "期权多头买入开仓",
    "sell_to_close": "期权多头卖出平仓",
    "sell_to_open": "期权空头卖出开仓",
    "buy_to_close": "期权空头买入平仓",
    "expire_long": "期权多头到期作废",
    "cash_settle_long": "期权多头现金结算",
    "expire_written": "卖出期权到期作废",
    "cash_settle_written": "卖出期权现金结算",
    "deposit": "外部资金存入现金账户",
    "withdrawal": "外部资金从现金账户转出",
    "interest": "现金账户利息",
    "fx_conversion": "两个不同币种现金账户之间换汇",
    "fee": "独立费用事实，金额写 gross_amount",
    "tax": "独立税费事实，金额写 gross_amount",
    "transfer_out": "以 account_id 为转出方、对手账户为转入方",
    "transfer_in": "以对手账户为转出方、account_id 为转入方",
    "opening_balance": "Portfolio inception date 已经存在的现金或多头持仓",
    "opening_written": "Portfolio inception date 已存在的期权空头；gross_amount 为剩余账面权利金负债，不再产生现金收入；acquisition_date 为原开仓日期",
}
FEE_CATEGORY_GUIDANCE = {
    "financing_interest": "实际融资利息支出",
    "borrow_fee": "实际证券借券费用",
    "payment_in_lieu": "空头持仓的股息补偿支出，不是股息收入",
    "unknown": "未分类；未填写时的系统默认值",
    "transaction_cost": "与某笔交易直接相关的佣金或手续费",
    "management_fee": "管理费",
    "custody_fee": "托管费",
    "administration_fee": "行政或运营费",
    "performance_fee": "业绩报酬",
    "other": "其他费用",
}

FIELD_GUIDANCE: dict[str, tuple[str, str, str]] = {
    "lot_selections_json": ("指定开仓批次", "可选", "FIFO 卖出、买回或兑付可指定 [{opening_transaction_id, quantity}]，数量之和必须等于本次处置量。文件内可引用 record_reference。留空使用账户成本法。"),
    "record_reference": ("本行引用编号", "可选", "文件内唯一编号，供后续指定批次引用。导出自动保留引用；转仓行代表接收批次。不是券商业务号。"),
    "option_delivery_json": ("期权原子实物交割", "physical_long / physical_written 必填", "一行同时表达期权结果及实际股票腿；填写 stock_account_id、settlement_cash_account_id、fees、fee_category、taxes，以及券商已形成股票空头时的 allow_stock_short。外层 fees/taxes 为零，费用分类留 unknown；两腿共用本行 trade_time。数量和行权价由合约校验，不要另填独立股票成交。"),
    "asset_deliveries_json": ("FCN 实物交付列表", "实物兑付时必填", "每项包含 account_id、instrument_id、quantity、fair_value（总确认价值）、currency、fx_rate_to_contract。delivery_date 为实际到账；取得费用 taxes/fees 使用该腿币种及 settlement_cash_account_id，fee_settlement_date 为扣款日。quantity_fx_rate（证券币/合约币）仅解释股数。gross_amount 仅填实际尾差；note 填交割依据。"),
    "settlement_cashflows_json": ("FCN 末期票息与合约费用", "可选", "每项含 kind（coupon/fee/tax）、cash_account_id、currency、amount、recognition_date、settlement_date。票息使用合约币；费用可用实际其他币种。不能重复录入已有票息或交付腿已资本化税费。"),
    "derivative_additional_terms_json": (
        "补充合约条款 JSON", "新建衍生品合约时选填",
        '期权示例：{"settlement_type":"physical","exercise_style":"american","strike_currency":"USD","terms_reference":"broker-confirmation"}。FCN 可填写 knock_in_observation、knock_out_observation_dates、coupon_payment_dates、coupon_day_count、settlement_type、payoff_description、terms_reference。不确定的条款留空，不推测；基础条款使用专用列。',
    ),
    "asset_type": (
        "资产类型",
        "条件必填",
        "先选择 security、fcn、option 或 cash；交易动作必须属于该资产类型。",
    ),
    "transaction_action": (
        "交易动作",
        "条件必填",
        "按资产类型选择页面同名业务动作；系统会自动转换为内部记账事件。",
    ),
    "trade_date": ("交易日期", "条件必填", "格式 yyyy-mm-dd；所有交易均需填写。"),
    "trade_time": (
        "交易时间",
        "可选",
        "格式 HH:MM；期权实物交割两腿共用此时刻，先买股后交付须填写各自真实时间；未知留空并标记为估算，不改写时间绕过仓位校验。",
    ),
    "settlement_date": (
        "结算日期",
        "可选",
        "格式 yyyy-mm-dd；留空时默认为 trade_date，且不能早于 trade_date；期初余额必须等于 Portfolio inception date。",
    ),
    "position_effective_date": (
        "持仓生效日",
        "可选",
        "买入、卖出、卖空、买回、红利再投资、到期赎回可用；不能早于 trade_date。期权实物交割的股票腿从 trade_date 生效。",
    ),
    "entitlement_date": (
        "权益确认日",
        "可选",
        "分红、红利再投资、票息及资产关联费用/税费可用，且不能晚于 trade_date；Cash 费用/税费留空。返还资本直接以 trade_date 表示权益确认日。",
    ),
    "acquisition_date": (
        "取得日期",
        "可选",
        "证券/FCN/Option 多头、Option 空头及股票/ETF 空头期初的原开仓日；不得晚于 inception date，未填时使用 trade_date；现金期初不填。",
    ),
    "account_id": (
        "交易账户 ID",
        "条件必填",
        "填写已设置的精确账户 ID；账户类别必须与 asset_type 一致。",
    ),
    "counterparty_account_id": (
        "对手账户 ID",
        "换汇或转账必填",
        "FX conversion 填换入现金账户；Transfer 填另一侧账户。",
    ),
    "settlement_cash_account_id": (
        "结算现金账户 ID",
        "按需填写",
        "证券交易、现金结算和部分费用税费需要；可留空让系统使用账户默认结算账户。",
    ),
    "instrument_id": (
        "证券 ID",
        "按需填写",
        "Registry 证券 ID；不可与 derivative_contract_id 同时填写。",
    ),
    "derivative_contract_id": (
        "衍生品合约 ID",
        "按需填写",
        "Portfolio 内的 FCN 或 Option 合约 ID；不可与 instrument_id 同时填写。",
    ),
    "derivative_contract_name": (
        "新合约名称",
        "新合约时必填",
        "仅在首次创建 FCN 或 Option 合约时填写；已有合约留空。",
    ),
    "derivative_contract_external_reference": (
        "新合约外部编号",
        "可选",
        "首次创建合约时可填写外部系统中的合约编号。",
    ),
    "option_underlying_instrument_id": (
        "期权标的证券 ID",
        "新 Option 必填",
        "仅新建 Option 合约时填写 Registry 中的精确证券 ID；该 ID 成为合约与标的的永久关联。",
    ),
    "option_type": (
        "期权类型",
        "新 Option 必填",
        "仅新建 Option 合约时从下拉选择 call 或 put。",
    ),
    "option_expiry_date": (
        "期权到期日",
        "新 Option 必填",
        "仅新建 Option 合约时填写，格式 yyyy-mm-dd。",
    ),
    "option_strike": ("期权行权价", "新 Option 必填", "仅新建 Option 合约时填写正数。"),
    "option_contract_multiplier": (
        "期权乘数",
        "新 Option 必填",
        "仅新建 Option 合约时填写正数，例如 100。",
    ),
    "fcn_notional": ("FCN 每张合约名义本金", "新 FCN 必填", "每张合约名义本金，填正数；总名义本金为此值乘以 quantity，折价或溢价成交金额可与名义本金不同。"),
    "fcn_annual_coupon_rate_pct": (
        "FCN 年化票息 (%)",
        "可选",
        "仅新建 FCN 合约时填写非负百分数，例如 12 表示 12%。",
    ),
    "fcn_issue_date": (
        "FCN 起息日",
        "新 FCN 必填",
        "仅新建 FCN 合约时填写，格式 yyyy-mm-dd。",
    ),
    "fcn_final_observation_date": (
        "FCN 最终观察日",
        "可选",
        "仅新建 FCN 合约时填写；必须在起息日与到期日之间。",
    ),
    "fcn_maturity_date": (
        "FCN 到期日",
        "新 FCN 必填",
        "仅新建 FCN 合约时填写，且不能早于起息日。",
    ),
    "fcn_issuer": ("FCN 发行人", "新 FCN 必填", "仅新建 FCN 合约时填写。"),
    "fcn_counterparty": ("FCN 对手方", "新 FCN 必填", "仅新建 FCN 合约时填写。"),
    "fcn_underlyings_json": (
        "FCN 标的条款 JSON",
        "新 FCN 必填",
        "仅新建 FCN 合约时填写 JSON 数组；参考 Examples 中的格式。",
    ),
    "quantity": (
        "数量",
        "按需填写",
        "输入非负绝对值；方向由交易动作决定。证券为份额，期权为张数，FCN 通常为 1。",
    ),
    "price": (
        "单价",
        "按需填写",
        "输入非负绝对值；买卖必须为正数。期权是每标的单位权利金。",
    ),
    "gross_amount": (
        "交易总额",
        "普通交易必填",
        "输入非负绝对值；方向由交易动作决定。不要把费用或税费混入其中。",
    ),
    "counter_amount": (
        "换入金额",
        "换汇必填",
        "仅 fx_conversion 使用，填写目标币种的非负绝对金额。",
    ),
    "fx_rate": (
        "换汇汇率",
        "换汇必填",
        "仅 fx_conversion 使用；counter_amount 必须等于 gross_amount × fx_rate。",
    ),
    "fees": (
        "附加费用",
        "可选",
        "与该交易直接相关的非负费用；留空按 0 处理。fee/tax 交易本身必须填 0。",
    ),
    "fee_category": (
        "费用类别",
        "可选",
        "从下拉选择；留空按 unknown 处理。非 unknown 需要费用交易或正的 fees。",
    ),
    "taxes": (
        "附加税费",
        "可选",
        "与该交易直接相关的非负税费；留空按 0 处理。fee/tax 交易本身必须填 0。",
    ),
    "currency": (
        "交易币种",
        "条件必填",
        "从下拉选择 USD、HKD、CNY、EUR、GBP 或 CHF；必须与账户和资产的币种规则匹配。",
    ),
    "source_system": (
        "来源系统",
        "建议填写",
        "建议每批使用稳定来源名称；留空时系统使用上传来源默认值。",
    ),
    "external_reference": (
        "外部流水号",
        "建议填写",
        "建议填写来源系统中的唯一流水号；填写时必须同时填写 source_system。",
    ),
    "note": (
        "备注",
        "可选",
        "只写必要的业务说明，不要用备注替代日期、金额、数量或账户等结构化字段。",
    ),
}


def _template_example(
    scenario: str,
    external_reference: str | None = None,
    **values: object,
) -> dict[str, object]:
    row: dict[str, object] = {"scenario": scenario, "currency": "USD", **values}
    asset_type = str(row.get("asset_type") or "").strip().lower()
    transaction_action = str(row.get("transaction_action") or "").strip().lower()
    if transaction_action not in TRANSACTION_ACTIONS.get(asset_type, ()):
        raise ValueError(
            f"Unsupported template example action: {asset_type}/{transaction_action}."
        )
    row["asset_type"] = asset_type
    row["transaction_action"] = transaction_action
    if external_reference is not None:
        row["source_system"] = "template_example"
        row["external_reference"] = external_reference
    return row


TEMPLATE_EXAMPLE_ROWS = (
    _template_example(
        "FCN｜美元本金接港股及港币税费", "FCN-CROSS-CURRENCY-001",
        asset_type="fcn", transaction_action="knock_in_close", trade_date="2026-06-19", settlement_date="2026-06-23",
        account_id="USD_FCN_ACCOUNT_ID", settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_USD_FCN_ID", currency="USD", quantity=1, gross_amount="22.41",
        asset_deliveries_json='[{"account_id":"HKD_SECURITY_ACCOUNT_ID","instrument_id":"HK_FCN_UNDERLYING_ID","quantity":4881,"fair_value":3416700,"currency":"HKD","fx_rate_to_contract":"0.128040973111395647","quantity_fx_rate":"7.81","fractional_quantity":"0.25","fractional_reference_price":700,"delivery_date":"2026-06-23","settlement_cash_account_id":"HKD_CASH_ACCOUNT_ID","taxes":3905,"fee_settlement_date":"2026-06-22"}]',
        settlement_cashflows_json='[{"kind":"coupon","cash_account_id":"USD_CASH_ACCOUNT_ID","currency":"USD","amount":"3333.34","recognition_date":"2026-06-19","settlement_date":"2026-06-23"}]',
        note="示意回单：50万美元本金、800港元接票价，700港元公允确认价；股数FX为7.81港元/美元。税费和末期票息按实际回单填写，已录票息勿重复。无需另造本金换汇或买股。",
    ),
    _template_example("证券｜实际卖空", "SHORT-SALE-001", asset_type="security", transaction_action="short_sell", trade_date="2026-05-04", account_id="USD_SECURITY_ACCOUNT_ID", settlement_cash_account_id="USD_CASH_ACCOUNT_ID", instrument_id="EQUITY_INSTRUMENT_ID", quantity=100, price=100, gross_amount=10000, fees=2, note="券商已确认卖空成交，不是卖出未持有股票的普通多头"),
    _template_example("证券｜买回空头", "SHORT-COVER-001", asset_type="security", transaction_action="buy_to_cover", trade_date="2026-05-05", account_id="USD_SECURITY_ACCOUNT_ID", settlement_cash_account_id="USD_CASH_ACCOUNT_ID", instrument_id="EQUITY_INSTRUMENT_ID", quantity=50, price=90, gross_amount=4500, fees=2),
    _template_example("证券｜期初空头负债", "SHORT-OPENING-001", asset_type="security", transaction_action="short_opening_balance", trade_date="2026-01-02", acquisition_date="2025-12-15", account_id="USD_SECURITY_ACCOUNT_ID", instrument_id="EQUITY_INSTRUMENT_ID", quantity=100, gross_amount=9998, note="原成交净收入形成剩余负债，不新增权利金或现金；trade_date 必须为组合 inception"),
    _template_example("Option｜多头实物行权", "OPTION-PHYSICAL-LONG-001", asset_type="option", transaction_action="physical_long", trade_date="2026-06-19", account_id="USD_OPTION_ACCOUNT_ID", derivative_contract_id="EXISTING_OPTION_ID", quantity=1, gross_amount=0, option_delivery_json='{"stock_account_id":"USD_SECURITY_ACCOUNT_ID","settlement_cash_account_id":"USD_CASH_ACCOUNT_ID","fees":2,"taxes":0,"allow_stock_short":false}', note="按已确认条款原子生成交付腿，不另填股票腿"),
    _template_example("Option｜空头指派并形成股票空头", "OPTION-PHYSICAL-WRITTEN-001", asset_type="option", transaction_action="physical_written", trade_date="2026-06-19", account_id="USD_OPTION_ACCOUNT_ID", derivative_contract_id="EXISTING_WRITTEN_CALL_ID", quantity=1, gross_amount=0, option_delivery_json='{"stock_account_id":"USD_SECURITY_ACCOUNT_ID","settlement_cash_account_id":"USD_CASH_ACCOUNT_ID","fees":2,"taxes":0,"allow_stock_short":true}', note="券商已确认指派形成股票空头；后续实际买回另记 buy_to_cover"),
    _template_example("FCN｜实物收股及碎股现金", "FCN-PHYSICAL-001", asset_type="fcn", transaction_action="knock_in_close", trade_date="2026-06-19", account_id="FCN_ACCOUNT_ID", settlement_cash_account_id="USD_CASH_ACCOUNT_ID", derivative_contract_id="EXISTING_FCN_ID", quantity=1, gross_amount=75, asset_deliveries_json='[{"account_id":"USD_SECURITY_ACCOUNT_ID","instrument_id":"FCN_UNDERLYING_ID","quantity":999,"fair_value":74925,"currency":"USD","fx_rate_to_contract":1}]', note="发行人确认收股 999、单价公允价值 75、碎股现金 75；gross_amount 不包含股票价值"),
    _template_example(
        "现金｜外部入金",
        "CASH-DEPOSIT-001",
        asset_type="cash",
        transaction_action="deposit",
        trade_date="2026-01-02",
        account_id="USD_CASH_ACCOUNT_ID",
        gross_amount=100000,
        note="客户向组合现金账户入金",
    ),
    _template_example(
        "现金｜外部出金",
        "CASH-WITHDRAWAL-001",
        asset_type="cash",
        transaction_action="withdrawal",
        trade_date="2026-01-03",
        account_id="USD_CASH_ACCOUNT_ID",
        gross_amount=5000,
        note="组合向外部账户出金",
    ),
    _template_example(
        "现金｜存款利息",
        "CASH-INTEREST-001",
        asset_type="cash",
        transaction_action="interest",
        trade_date="2026-01-31",
        account_id="USD_CASH_ACCOUNT_ID",
        gross_amount=125.50,
        note="现金账户利息收入",
    ),
    _template_example(
        "现金｜USD 换 CNY",
        "FX-USD-CNY-001",
        asset_type="cash",
        transaction_action="fx_conversion",
        trade_date="2026-02-02",
        settlement_date="2026-02-02",
        account_id="USD_CASH_ACCOUNT_ID",
        counterparty_account_id="CNY_CASH_ACCOUNT_ID",
        gross_amount=10000,
        counter_amount=72000,
        fx_rate=7.2,
        note="卖出 USD、买入 CNY；currency 是转出账户币种",
    ),
    _template_example(
        "运营｜现金账户管理费",
        "CASH-FEE-001",
        asset_type="cash",
        transaction_action="fee",
        trade_date="2026-02-28",
        account_id="USD_CASH_ACCOUNT_ID",
        gross_amount=50,
        fee_category="management_fee",
        note="独立费用事实，金额写 gross_amount，不写 fees",
    ),
    _template_example(
        "运营｜现金账户税费",
        "CASH-TAX-001",
        asset_type="cash",
        transaction_action="tax",
        trade_date="2026-02-28",
        account_id="USD_CASH_ACCOUNT_ID",
        gross_amount=20,
        note="独立税费事实，金额写 gross_amount，不写 taxes",
    ),
    _template_example(
        "期初｜现金余额",
        "OPENING-CASH-001",
        asset_type="cash",
        transaction_action="opening_balance",
        trade_date="2026-01-01",
        account_id="USD_CASH_ACCOUNT_ID",
        gross_amount=250000,
        note="上线日已存在的现金余额",
    ),
    _template_example(
        "内部转移｜现金转出",
        asset_type="cash",
        transaction_action="transfer_out",
        trade_date="2026-03-01",
        settlement_date="2026-03-01",
        account_id="USD_CASH_ACCOUNT_A_ID",
        counterparty_account_id="USD_CASH_ACCOUNT_B_ID",
        gross_amount=25000,
        note="同币种现金账户之间内部转移",
    ),
    _template_example(
        "期初｜证券持仓",
        "OPENING-SECURITY-001",
        asset_type="security",
        transaction_action="opening_balance",
        trade_date="2026-01-01",
        acquisition_date="2025-08-15",
        account_id="SECURITY_ACCOUNT_ID",
        instrument_id="REGISTRY_INSTRUMENT_ID",
        quantity=100,
        price=45,
        gross_amount=4500,
        note="上线日前已持有的证券；acquisition_date 是真实取得日期",
    ),
    _template_example(
        "股票｜USD 股票买入",
        "SECURITY-BUY-001",
        asset_type="security",
        transaction_action="buy",
        trade_date="2026-03-02",
        trade_time="10:30",
        settlement_date="2026-03-04",
        position_effective_date="2026-03-02",
        account_id="USD_SECURITY_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        instrument_id="REGISTRY_USD_EQUITY_ID",
        quantity=100,
        price=50,
        gross_amount=5000,
        fees=5,
        fee_category="transaction_cost",
        note="数量 × 单价 = gross_amount；佣金单独写 fees",
    ),
    _template_example(
        "ETF｜HKD ETF 卖出",
        "ETF-SELL-001",
        asset_type="security",
        transaction_action="sell",
        trade_date="2026-03-03",
        settlement_date="2026-03-05",
        position_effective_date="2026-03-03",
        account_id="HKD_SECURITY_ACCOUNT_ID",
        settlement_cash_account_id="HKD_CASH_ACCOUNT_ID",
        instrument_id="REGISTRY_HKD_ETF_ID",
        quantity=500,
        price=22,
        gross_amount=11000,
        fees=25,
        fee_category="transaction_cost",
        taxes=11,
        currency="HKD",
        note="证券、持仓账户、结算现金账户和交易币种均为 HKD",
    ),
    _template_example(
        "公募基金｜CNY 申购确认",
        "PUBLIC-FUND-BUY-001",
        asset_type="security",
        transaction_action="buy",
        trade_date="2026-03-03",
        settlement_date="2026-03-05",
        position_effective_date="2026-03-05",
        account_id="CNY_SECURITY_ACCOUNT_ID",
        settlement_cash_account_id="CNY_CASH_ACCOUNT_ID",
        instrument_id="REGISTRY_CNY_PUBLIC_FUND_ID",
        quantity=1234.56789,
        price=1.215,
        gross_amount=1500,
        currency="CNY",
        note="trade_date 写申请日，position_effective_date 写基金确认份额生效日",
    ),
    _template_example(
        "私募基金｜CNY 赎回确认",
        "PRIVATE-FUND-SELL-001",
        asset_type="security",
        transaction_action="sell",
        trade_date="2026-03-06",
        settlement_date="2026-03-12",
        position_effective_date="2026-03-10",
        account_id="CNY_SECURITY_ACCOUNT_ID",
        settlement_cash_account_id="CNY_CASH_ACCOUNT_ID",
        instrument_id="REGISTRY_CNY_PRIVATE_FUND_ID",
        quantity=800.12345678,
        price=1.25,
        gross_amount=1000.15432098,
        currency="CNY",
        note="分别记录申请日、份额确认日和资金结算日，保留原始份额与金额精度",
    ),
    _template_example(
        "证券｜卖出或基金赎回",
        "SECURITY-SELL-001",
        asset_type="security",
        transaction_action="sell",
        trade_date="2026-03-10",
        settlement_date="2026-03-12",
        position_effective_date="2026-03-10",
        account_id="SECURITY_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        instrument_id="REGISTRY_INSTRUMENT_ID",
        quantity=40,
        price=55,
        gross_amount=2200,
        fees=4,
        fee_category="transaction_cost",
        taxes=2,
        note="所有金额和数量均填非负绝对值，方向由 sell 决定",
    ),
    _template_example(
        "证券｜现金分红",
        "DIVIDEND-001",
        asset_type="security",
        transaction_action="dividend",
        trade_date="2026-03-20",
        settlement_date="2026-03-22",
        entitlement_date="2026-03-15",
        account_id="SECURITY_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        instrument_id="REGISTRY_INSTRUMENT_ID",
        gross_amount=300,
        taxes=30,
        note="entitlement_date 是权益确认日；不填写 quantity 或 price",
    ),
    _template_example(
        "基金｜红利再投资",
        "DIVIDEND-REINVEST-001",
        asset_type="security",
        transaction_action="dividend_reinvestment",
        trade_date="2026-03-22",
        position_effective_date="2026-03-22",
        entitlement_date="2026-03-15",
        account_id="SECURITY_ACCOUNT_ID",
        instrument_id="REGISTRY_FUND_ID",
        quantity=25,
        price=12,
        gross_amount=300,
        note="形成份额但不经过结算现金账户；fees 和 taxes 必须为 0",
    ),
    _template_example(
        "证券｜返还资本",
        "RETURN-CAPITAL-001",
        asset_type="security",
        transaction_action="return_of_capital",
        trade_date="2026-03-25",
        settlement_date="2026-03-27",
        account_id="SECURITY_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        instrument_id="REGISTRY_INSTRUMENT_ID",
        gross_amount=500,
        note="trade_date 是权益确认日，settlement_date 是到账日；不填写 quantity、price 或 entitlement_date",
    ),
    _template_example(
        "证券｜HKD 独立交易费用",
        "SECURITY-FEE-001",
        asset_type="security",
        transaction_action="fee",
        trade_date="2026-03-25",
        entitlement_date="2026-03-24",
        account_id="HKD_SECURITY_ACCOUNT_ID",
        settlement_cash_account_id="HKD_CASH_ACCOUNT_ID",
        instrument_id="REGISTRY_HKD_EQUITY_ID",
        gross_amount=35,
        fee_category="transaction_cost",
        currency="HKD",
        note="资产关联的独立费用事实；金额写 gross_amount，fees 保持为 0",
    ),
    _template_example(
        "证券｜HKD 独立税费",
        "SECURITY-TAX-001",
        asset_type="security",
        transaction_action="tax",
        trade_date="2026-03-25",
        entitlement_date="2026-03-24",
        account_id="HKD_SECURITY_ACCOUNT_ID",
        settlement_cash_account_id="HKD_CASH_ACCOUNT_ID",
        instrument_id="REGISTRY_HKD_EQUITY_ID",
        gross_amount=18,
        currency="HKD",
        note="资产关联的独立税费事实；金额写 gross_amount，taxes 保持为 0",
    ),
    _template_example(
        "内部转移｜证券仓位转出",
        asset_type="security",
        transaction_action="transfer_out",
        trade_date="2026-04-01",
        settlement_date="2026-04-01",
        account_id="SECURITY_ACCOUNT_A_ID",
        counterparty_account_id="SECURITY_ACCOUNT_B_ID",
        instrument_id="REGISTRY_INSTRUMENT_ID",
        quantity=30,
        gross_amount=1350,
        note="同一证券在两个兼容证券账户之间转仓",
    ),
    _template_example(
        "Option｜新 Call 多头买入开仓",
        "OPTION-LONG-OPEN-001",
        asset_type="option",
        transaction_action="buy_to_open",
        trade_date="2026-04-02",
        settlement_date="2026-04-02",
        account_id="OPTION_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="NEW_LONG_OPTION_ID",
        derivative_contract_name="Example Dec 110 Call",
        derivative_contract_external_reference="BROKER-OPTION-001",
        option_underlying_instrument_id="REGISTRY_UNDERLYING_ID",
        option_type="call",
        option_expiry_date="2026-12-18",
        option_strike=110,
        option_contract_multiplier=100,
        quantity=2,
        price=3,
        gross_amount=600,
        fees=2,
        fee_category="transaction_cost",
        note="首次交易同时定义新合约；2 × 3 × 100 = 600",
    ),
    _template_example(
        "Option｜多头卖出平仓",
        "OPTION-LONG-CLOSE-001",
        asset_type="option",
        transaction_action="sell_to_close",
        trade_date="2026-05-02",
        settlement_date="2026-05-02",
        account_id="OPTION_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_LONG_OPTION_ID",
        quantity=1,
        price=5,
        gross_amount=500,
        fees=2,
        fee_category="transaction_cost",
        note="已有合约只填 derivative_contract_id，不重复合约条款",
    ),
    _template_example(
        "Option｜新 Put 空头卖出开仓",
        "OPTION-SHORT-OPEN-001",
        asset_type="option",
        transaction_action="sell_to_open",
        trade_date="2026-04-05",
        settlement_date="2026-04-05",
        account_id="OPTION_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="NEW_SHORT_OPTION_ID",
        derivative_contract_name="Example Dec 90 Put",
        derivative_contract_external_reference="BROKER-OPTION-002",
        option_underlying_instrument_id="REGISTRY_UNDERLYING_ID",
        option_type="put",
        option_expiry_date="2026-12-18",
        option_strike=90,
        option_contract_multiplier=100,
        quantity=3,
        price=2,
        gross_amount=600,
        fees=2,
        fee_category="transaction_cost",
        note="卖出开仓选择 sell_to_open",
    ),
    _template_example(
        "Option｜空头买入平仓",
        "OPTION-SHORT-CLOSE-001",
        asset_type="option",
        transaction_action="buy_to_close",
        trade_date="2026-05-05",
        settlement_date="2026-05-05",
        account_id="OPTION_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_SHORT_OPTION_ID",
        quantity=1,
        price=1,
        gross_amount=100,
        fees=2,
        fee_category="transaction_cost",
        note="买入平仓选择 buy_to_close",
    ),
    _template_example(
        "Option｜CNY 合约期初多头",
        "OPENING-OPTION-001",
        asset_type="option",
        transaction_action="opening_balance",
        trade_date="2026-01-01",
        acquisition_date="2025-12-15",
        account_id="CNY_OPTION_ACCOUNT_ID",
        derivative_contract_id="NEW_CNY_OPTION_ID",
        derivative_contract_name="Example CNY June 4.50 Call",
        derivative_contract_external_reference="BROKER-OPTION-CNY-001",
        option_underlying_instrument_id="REGISTRY_CNY_UNDERLYING_ID",
        option_type="call",
        option_expiry_date="2026-06-19",
        option_strike=4.5,
        option_contract_multiplier=10000,
        quantity=2,
        price=0.2,
        gross_amount=4000,
        currency="CNY",
        note="上线日前已持有的期权；首次出现时在同一行定义合约条款",
    ),
    _template_example(
        "Option｜期初空头负债",
        "OPENING-WRITTEN-001",
        asset_type="option",
        transaction_action="opening_written",
        trade_date="2026-01-01",
        acquisition_date="2025-12-15",
        account_id="OPTION_ACCOUNT_ID",
        derivative_contract_id="NEW_WRITTEN_OPTION_ID",
        derivative_contract_name="Existing USD Written Put",
        option_underlying_instrument_id="REGISTRY_UNDERLYING_A_ID",
        option_type="put",
        option_expiry_date="2026-06-19",
        option_strike=100,
        option_contract_multiplier=100,
        quantity=2,
        gross_amount=1000,
        derivative_additional_terms_json='{"settlement_type":"physical","exercise_style":"american","strike_currency":"USD","terms_reference":"broker-opening-statement"}',
        note="账面期初负债 1000；原权利金已在期初现金中，不再次记现金收入",
    ),
    _template_example(
        "FCN｜敲入观察，不赎回",
        "FCN-KNOCK-IN-OBSERVATION-001",
        asset_type="fcn",
        transaction_action="knock_in_observation",
        trade_date="2026-06-10",
        account_id="FCN_ACCOUNT_ID",
        derivative_contract_id="EXISTING_FCN_ID",
        gross_amount=0,
        note="发行人通知：本日已触及敲入条件；合约继续存续，票息及最终交割另记",
    ),
    _template_example(
        "Option｜HKD 独立费用",
        "OPTION-FEE-001",
        asset_type="option",
        transaction_action="fee",
        trade_date="2026-05-06",
        entitlement_date="2026-05-05",
        account_id="HKD_OPTION_ACCOUNT_ID",
        settlement_cash_account_id="HKD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_HKD_OPTION_ID",
        gross_amount=20,
        fee_category="transaction_cost",
        currency="HKD",
        note="Option 关联的独立费用事实，不填写 quantity 或 price",
    ),
    _template_example(
        "Option｜HKD 独立税费",
        "OPTION-TAX-001",
        asset_type="option",
        transaction_action="tax",
        trade_date="2026-05-06",
        entitlement_date="2026-05-05",
        account_id="HKD_OPTION_ACCOUNT_ID",
        settlement_cash_account_id="HKD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_HKD_OPTION_ID",
        gross_amount=8,
        currency="HKD",
        note="Option 关联的独立税费事实，不填写 quantity 或 price",
    ),
    _template_example(
        "Option｜多头到期作废",
        "OPTION-LONG-EXPIRY-001",
        asset_type="option",
        transaction_action="expire_long",
        trade_date="2026-12-18",
        account_id="OPTION_ACCOUNT_ID",
        derivative_contract_id="EXISTING_LONG_OPTION_ID",
        quantity=1,
        gross_amount=0,
        note="无现金事件；不填结算现金账户、price、fees 或 taxes",
    ),
    _template_example(
        "Option｜多头现金结算",
        "OPTION-LONG-CASH-001",
        asset_type="option",
        transaction_action="cash_settle_long",
        trade_date="2026-12-18",
        settlement_date="2026-12-18",
        account_id="OPTION_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_LONG_OPTION_ID",
        quantity=1,
        gross_amount=1500,
        note="现金流入；gross_amount 必须为正，不填 price",
    ),
    _template_example(
        "Option｜空头到期作废",
        "OPTION-WRITER-EXPIRY-001",
        asset_type="option",
        transaction_action="expire_written",
        trade_date="2026-12-18",
        account_id="OPTION_ACCOUNT_ID",
        derivative_contract_id="EXISTING_SHORT_OPTION_ID",
        quantity=2,
        gross_amount=0,
        note="无现金事件；不填结算现金账户、price、fees 或 taxes",
    ),
    _template_example(
        "Option｜空头现金结算",
        "OPTION-WRITER-CASH-001",
        asset_type="option",
        transaction_action="cash_settle_written",
        trade_date="2026-12-18",
        settlement_date="2026-12-18",
        account_id="OPTION_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_SHORT_OPTION_ID",
        quantity=2,
        gross_amount=13000,
        note="现金流出；gross_amount 必须为正，不填 price",
    ),
    _template_example(
        "FCN｜新合约进入",
        "FCN-ENTRY-001",
        asset_type="fcn",
        transaction_action="entry",
        trade_date="2026-05-01",
        settlement_date="2026-05-01",
        account_id="FCN_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="NEW_FCN_ID",
        derivative_contract_name="Example 4M FCN",
        derivative_contract_external_reference="BROKER-FCN-001",
        fcn_notional=100000,
        fcn_annual_coupon_rate_pct=12,
        fcn_issue_date="2026-05-01",
        fcn_final_observation_date="2026-08-28",
        fcn_maturity_date="2026-09-01",
        fcn_issuer="EXAMPLE BANK",
        fcn_counterparty="EXAMPLE BROKER",
        fcn_underlyings_json='[{"instrument_id":"REGISTRY_UNDERLYING_A_ID","initial_reference_price":100,"strike_level_pct":100,"knock_in_level_pct":70,"knock_out_level_pct":105,"deliverable":true},{"instrument_id":"REGISTRY_UNDERLYING_B_ID","initial_reference_price":80,"strike_level_pct":100,"knock_in_level_pct":65,"knock_out_level_pct":105,"deliverable":true}]',
        quantity=1,
        price=100000,
        gross_amount=100000,
        note="首次交易同时定义不可变 FCN 条款；underlyings JSON 可包含多个唯一证券 ID",
    ),
    _template_example(
        "FCN｜HKD 合约期初持仓",
        "OPENING-FCN-001",
        asset_type="fcn",
        transaction_action="opening_balance",
        trade_date="2026-01-01",
        acquisition_date="2025-11-03",
        account_id="HKD_FCN_ACCOUNT_ID",
        derivative_contract_id="NEW_HKD_FCN_ID",
        derivative_contract_name="Example HKD 6M FCN",
        derivative_contract_external_reference="BROKER-FCN-HKD-001",
        fcn_notional=500000,
        fcn_annual_coupon_rate_pct=10,
        fcn_issue_date="2025-11-03",
        fcn_final_observation_date="2026-04-29",
        fcn_maturity_date="2026-05-04",
        fcn_issuer="EXAMPLE BANK",
        fcn_counterparty="EXAMPLE BROKER",
        fcn_underlyings_json='[{"instrument_id":"REGISTRY_HKD_UNDERLYING_ID","initial_reference_price":50,"strike_level_pct":100,"knock_in_level_pct":70,"knock_out_level_pct":103,"deliverable":true}]',
        quantity=1,
        price=500000,
        gross_amount=500000,
        currency="HKD",
        note="上线日前已持有的 FCN；acquisition_date 写真实取得日期",
    ),
    _template_example(
        "FCN｜提前退出",
        "FCN-EARLY-EXIT-001",
        asset_type="fcn",
        transaction_action="early_exit",
        trade_date="2026-06-10",
        settlement_date="2026-06-12",
        account_id="FCN_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_FCN_ID",
        quantity=1,
        price=102000,
        gross_amount=102000,
        fees=100,
        fee_category="transaction_cost",
        note="FCN 到期前退出选择 early_exit；数量通常为 1，价款写正数",
    ),
    _template_example(
        "FCN｜独立费用",
        "FCN-FEE-001",
        asset_type="fcn",
        transaction_action="fee",
        trade_date="2026-06-10",
        entitlement_date="2026-06-10",
        account_id="FCN_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_FCN_ID",
        gross_amount=100,
        fee_category="transaction_cost",
        note="FCN 关联的独立费用事实，不填写 quantity 或 price",
    ),
    _template_example(
        "FCN｜独立税费",
        "FCN-TAX-001",
        asset_type="fcn",
        transaction_action="tax",
        trade_date="2026-06-10",
        entitlement_date="2026-06-10",
        account_id="FCN_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_FCN_ID",
        gross_amount=40,
        note="FCN 关联的独立税费事实，不填写 quantity 或 price",
    ),
    _template_example(
        "FCN｜票息收入",
        "FCN-COUPON-001",
        asset_type="fcn",
        transaction_action="coupon",
        trade_date="2026-06-01",
        settlement_date="2026-06-01",
        entitlement_date="2026-06-01",
        account_id="FCN_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_FCN_ID",
        gross_amount=2000,
        note="已有合约只填 derivative_contract_id；不填 quantity 或 price",
    ),
    _template_example(
        "FCN｜正常到期",
        "FCN-MATURITY-001",
        asset_type="fcn",
        transaction_action="maturity_close",
        trade_date="2026-09-01",
        settlement_date="2026-09-01",
        account_id="FCN_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_FCN_ID",
        quantity=1,
        gross_amount=100000,
        note="FCN 正常结束；不填 price",
    ),
    _template_example(
        "FCN｜敲入后实际现金兑付",
        "FCN-KNOCK-IN-001",
        asset_type="fcn",
        transaction_action="knock_in_close",
        trade_date="2026-09-01",
        settlement_date="2026-09-01",
        account_id="FCN_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_FCN_ID",
        quantity=1,
        gross_amount=100000,
        note="仅用于最终观察后、发行人已确认实际发生的现金兑付；实物收股尚不支持，不能伪造现金和独立买股",
    ),
    _template_example(
        "FCN｜敲出结束",
        "FCN-KNOCK-OUT-001",
        asset_type="fcn",
        transaction_action="knock_out_close",
        trade_date="2026-07-15",
        settlement_date="2026-07-17",
        account_id="FCN_ACCOUNT_ID",
        settlement_cash_account_id="USD_CASH_ACCOUNT_ID",
        derivative_contract_id="EXISTING_FCN_ID",
        quantity=1,
        gross_amount=100000,
        note="FCN 敲出结束；实际票息另录 coupon",
    ),
)


def _export_value(column: str, value: object) -> object:
    if value is None or value == "":
        return None
    if column in DATE_COLUMNS and isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    if column == "trade_time" and isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            return value
    if column in NUMERIC_COLUMNS:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return str(value)
    return (
        value
        if isinstance(value, (date, datetime, time, Decimal, int, float))
        else str(value)
    )


def _set_text_cell(cell: Cell, value: str) -> None:
    cell.value = value
    cell.data_type = "s"


def _column_letter(column: str) -> str:
    return get_column_letter(IMPORT_COLUMNS.index(column) + 1)


def _style_transaction_header(
    worksheet,
    *,
    include_field_comments: bool,
) -> None:
    dropdown_columns = {
        "asset_type",
        "transaction_action",
        "option_type",
        "fee_category",
        "currency",
    }
    for column_index, column in enumerate(IMPORT_COLUMNS, start=1):
        cell = worksheet.cell(row=1, column=column_index)
        _set_text_cell(cell, column)
        cell.fill = (
            REQUIRED_HEADER_FILL
            if column in REQUIRED_COLUMNS
            else DROPDOWN_HEADER_FILL
            if column in dropdown_columns
            else HEADER_FILL
        )
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        if include_field_comments:
            label, requiredness, guidance = FIELD_GUIDANCE[column]
            cell.comment = Comment(
                f"{label}\n填写要求：{requiredness}\n{guidance}",
                "Investment Studio",
            )
    worksheet.row_dimensions[1].height = 34
    for index, column in enumerate(IMPORT_COLUMNS, start=1):
        width = max(13, min(34, len(column) + 2))
        worksheet.column_dimensions[get_column_letter(index)].width = width


def _add_validation(
    worksheet,
    *,
    column: str,
    validation_type: str,
    formula1: str,
    formula2: str | None = None,
    operator: str | None = None,
    prompt: str,
    error: str,
) -> None:
    validation = DataValidation(
        type=validation_type,
        formula1=formula1,
        formula2=formula2,
        operator=operator,
        allow_blank=True,
    )
    validation.errorStyle = "stop"
    validation.errorTitle = "填写值不符合模板规则"
    validation.error = error
    validation.promptTitle = FIELD_GUIDANCE[column][0]
    validation.prompt = prompt
    validation.showErrorMessage = True
    validation.showInputMessage = True
    worksheet.add_data_validation(validation)
    column_letter = _column_letter(column)
    validation.add(f"{column_letter}2:{column_letter}{TEMPLATE_INPUT_LAST_ROW}")


def _add_template_validations(worksheet) -> None:
    list_validations = (
        (
            "asset_type",
            "AssetTypeValues",
            "先选择资产类别；交易动作必须属于该类别。",
            "请选择 security、fcn、option 或 cash。",
        ),
        (
            "transaction_action",
            'INDIRECT($A2&"_actions")',
            "选择与资产类别对应的页面业务动作。",
            "请先选择资产类型，再选择该资产类型对应的业务动作。",
        ),
        (
            "option_type",
            "OptionTypeValues",
            "仅新建 Option 合约时使用。",
            "请选择 call 或 put。",
        ),
        (
            "fee_category",
            "FeeCategoryValues",
            "留空时系统使用 unknown。",
            "请选择费用类别下拉列表中的值。",
        ),
        (
            "currency",
            "CurrencyValues",
            "交易币种必须是 USD、HKD、CNY、EUR、GBP 或 CHF。",
            "请选择币种下拉列表中的值。",
        ),
    )
    for column, formula, prompt, error in list_validations:
        _add_validation(
            worksheet,
            column=column,
            validation_type="list",
            formula1=formula,
            prompt=prompt,
            error=error,
        )

    for column in DATE_COLUMNS:
        _add_validation(
            worksheet,
            column=column,
            validation_type="date",
            operator="between",
            formula1="DATE(1900,1,1)",
            formula2="DATE(9999,12,31)",
            prompt="请输入 yyyy-mm-dd 格式的日期，或留空。",
            error="请输入有效日期，格式为 yyyy-mm-dd。",
        )
    _add_validation(
        worksheet,
        column="trade_time",
        validation_type="time",
        operator="between",
        formula1="TIME(0,0,0)",
        formula2="TIME(23,59,0)",
        prompt="请输入 HH:MM 格式的时间，或留空。",
        error="请输入 00:00 至 23:59 之间的时间。",
    )
    for column in NUMERIC_COLUMNS:
        _add_validation(
            worksheet,
            column=column,
            validation_type="decimal",
            operator="greaterThanOrEqual",
            formula1="0",
            prompt="请输入非负数字；金额和数量的方向由交易动作决定。",
            error="请输入大于或等于 0 的数字。",
        )


def _add_template_required_indicators(worksheet) -> None:
    first = _column_letter(IMPORT_COLUMNS[0])
    last = _column_letter(IMPORT_COLUMNS[-1])
    row_has_data = f"COUNTA(${first}2:${last}2)>0"
    indicators = (
        ("asset_type", f'AND({row_has_data},${_column_letter("asset_type")}2="")'),
        (
            "transaction_action",
            f'AND({row_has_data},${_column_letter("transaction_action")}2="")',
        ),
        ("trade_date", f'AND({row_has_data},${_column_letter("trade_date")}2="")'),
        ("currency", f'AND({row_has_data},${_column_letter("currency")}2="")'),
        ("account_id", f'AND({row_has_data},${_column_letter("account_id")}2="")'),
        ("gross_amount", f'AND({row_has_data},${_column_letter("gross_amount")}2="")'),
    )
    for column, formula in indicators:
        column_letter = _column_letter(column)
        worksheet.conditional_formatting.add(
            f"{column_letter}2:{column_letter}{TEMPLATE_INPUT_LAST_ROW}",
            FormulaRule(formula=[formula], fill=INVALID_FILL),
        )


def _render_template_instructions(worksheet) -> None:
    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "A4"
    worksheet.merge_cells("A1:D1")
    title = worksheet["A1"]
    title.value = "交易记录导入模板"
    title.fill = TITLE_FILL
    title.font = Font(color="FFFFFF", bold=True, size=16)
    title.alignment = Alignment(vertical="center")
    worksheet.row_dimensions[1].height = 30

    worksheet.merge_cells("A3:D3")
    section = worksheet["A3"]
    section.value = "填写流程"
    section.fill = SECTION_FILL
    section.font = Font(bold=True, size=12)

    steps = (
        "1. 只在 Transactions 页录入。该页第一行字段名及工作表名称不可修改；Instructions、Field Guide、Examples、Lists 不会导入。",
        "2. 每行是一项已确认的经济事实。先在 asset_type 选择 Security、FCN、Option 或 Cash，再在 transaction_action 选择该资产类别对应的页面业务动作。",
        "3. 账户、证券和衍生品合约均使用系统已经设置好的精确 ID。模板不会携带任何组合、账户、证券或合约资料。",
        "4. 金额、数量、单价、费用和税费一律填写非负绝对值；资金或持仓方向由 transaction_action 决定。gross_amount 不含 fees 和 taxes。",
        "5. Security 填 instrument_id；FCN/Option 填 derivative_contract_id。首次建立 FCN/Option 时，在同一行补充合约条款；已有合约只填合约 ID。",
        "6. 填完后保存为 .xlsx，在系统中先 Import 预览。Excel 下拉和格式校验只防常见输入错误；账户归属、币种、持仓历史和跨字段规则以预览校验结果为准。",
        "7. Option 实物行权或指派不可拆成两行导入；请在 Holdings 的 Option outcome 中处理，系统会按合约条款原子生成并关联 Option 结果与标的股票交付。",
    )
    for row_index, step in enumerate(steps, start=4):
        worksheet.merge_cells(
            start_row=row_index, start_column=1, end_row=row_index, end_column=4
        )
        cell = worksheet.cell(row=row_index, column=1)
        cell.value = step
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.border = THIN_GRAY_BORDER
        worksheet.row_dimensions[row_index].height = 60

    worksheet.merge_cells("A11:D11")
    warning = worksheet["A11"]
    warning.value = "重要规则"
    warning.fill = WARNING_FILL
    warning.font = Font(bold=True, size=12)
    rules = (
        "普通交易：asset_type、transaction_action、trade_date、account_id、gross_amount、currency 为必填；currency 只能是 USD、HKD、CNY、EUR、GBP 或 CHF，并与账户、资产及结算现金账户匹配。Transfer 只填一个方向：Transfer Out 的 account_id 是转出方，Transfer In 的 account_id 是转入方；counterparty_account_id 填另一侧账户，转仓还要填写 instrument_id 和 quantity。",
        "交易方向只由 transaction_action 表达。FCN/Option 的到期、敲入、敲出和现金结算都直接选择该资产对应的交易动作，不需要再填写额外的事件分类字段。",
        "fees、taxes 和 fee_category 可留空，分别按 0、0 和 unknown 处理。所有交易（包括 Transfer）都建议填写 source_system + external_reference；Transfer 的来源身份记录在成对交易的 transfer_out 腿。",
        "不要在 Transactions 页使用公式、宏、合并单元格或负数。上传仅接受 Transactions 页的字面值，最多 5,000 条交易记录。",
        "Option 新合约必须填写 option_underlying_instrument_id。实物行权或指派必须使用系统专用流程，不能用 cash_settle_* 加普通股票买卖来替代。",
    )
    for row_index, rule in enumerate(rules, start=12):
        worksheet.merge_cells(
            start_row=row_index, start_column=1, end_row=row_index, end_column=4
        )
        cell = worksheet.cell(row=row_index, column=1)
        cell.value = rule
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.border = THIN_GRAY_BORDER
        worksheet.row_dimensions[row_index].height = 82

    for column in "ABCD":
        worksheet.column_dimensions[column].width = 20
    worksheet.sheet_properties.pageSetUpPr.fitToPage = True
    worksheet.page_setup.fitToWidth = 1


def _render_field_guide(worksheet) -> None:
    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "A2"
    headers = ("字段名", "中文含义", "填写要求", "填写规则")
    for column_index, header in enumerate(headers, start=1):
        cell = worksheet.cell(row=1, column=column_index)
        cell.value = header
        cell.fill = TITLE_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    for row_index, column in enumerate(IMPORT_COLUMNS, start=2):
        label, requiredness, guidance = FIELD_GUIDANCE[column]
        row_values = (column, label, requiredness, guidance)
        for column_index, value in enumerate(row_values, start=1):
            cell = worksheet.cell(row=row_index, column=column_index)
            _set_text_cell(cell, value)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = THIN_GRAY_BORDER
        if column in REQUIRED_COLUMNS:
            worksheet.cell(row=row_index, column=1).fill = WARNING_FILL
    worksheet.auto_filter.ref = f"A1:D{len(IMPORT_COLUMNS) + 1}"
    worksheet.column_dimensions["A"].width = 35
    worksheet.column_dimensions["B"].width = 20
    worksheet.column_dimensions["C"].width = 18
    worksheet.column_dimensions["D"].width = 82


def _render_examples(worksheet) -> None:
    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "B5"
    headers = ("示例场景", *IMPORT_COLUMNS)
    last_column_letter = get_column_letter(len(headers))
    worksheet.merge_cells(f"A1:{last_column_letter}1")
    title = worksheet["A1"]
    title.value = "资产类型与交易动作示例（仅供参考，不会上传）"
    title.fill = TITLE_FILL
    title.font = Font(color="FFFFFF", bold=True, size=14)
    title.alignment = Alignment(vertical="center")
    worksheet.row_dimensions[1].height = 28
    worksheet.merge_cells(f"A2:{last_column_letter}2")
    notice = worksheet["A2"]
    notice.value = (
        "复制适用示例的标准字段列到 Transactions，再替换所有 *_ID、日期、金额、币种、"
        "source_system、external_reference 和备注。不要复制“示例场景”列，也不要直接上传占位值。"
    )
    notice.fill = WARNING_FILL
    notice.alignment = Alignment(vertical="center", wrap_text=True)
    worksheet.row_dimensions[2].height = 34
    for column_index, header in enumerate(headers, start=1):
        cell = worksheet.cell(row=4, column=column_index)
        _set_text_cell(cell, header)
        cell.fill = TITLE_FILL if column_index == 1 else HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    worksheet.row_dimensions[4].height = 34
    for row_index, row in enumerate(TEMPLATE_EXAMPLE_ROWS, start=5):
        for column_index, column in enumerate(IMPORT_COLUMNS, start=2):
            value = _export_value(column, row.get(column))
            if value is None:
                continue
            cell = worksheet.cell(row=row_index, column=column_index)
            if isinstance(value, str):
                _set_text_cell(cell, value)
            else:
                cell.value = value
            if column in DATE_COLUMNS:
                cell.number_format = "yyyy-mm-dd"
            elif column == "trade_time":
                cell.number_format = "hh:mm"
            elif column in NUMERIC_COLUMNS:
                cell.number_format = "0.########"
        scenario_cell = worksheet.cell(row=row_index, column=1)
        _set_text_cell(scenario_cell, str(row["scenario"]))
        scenario_cell.font = Font(bold=True)
        for column_index in range(1, len(headers) + 1):
            cell = worksheet.cell(row=row_index, column=column_index)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = THIN_GRAY_BORDER
        if row_index % 2 == 1:
            scenario_cell.fill = EXAMPLE_FILL
    worksheet.auto_filter.ref = (
        f"A4:{last_column_letter}{len(TEMPLATE_EXAMPLE_ROWS) + 4}"
    )
    worksheet.column_dimensions["A"].width = 28
    for index, column in enumerate(IMPORT_COLUMNS, start=2):
        worksheet.column_dimensions[get_column_letter(index)].width = max(
            13,
            min(34, len(column) + 2),
        )


def _write_reference_list(
    worksheet,
    start_column: int,
    header: str,
    values: tuple[str, ...],
    guidance: dict[str, str],
) -> None:
    code_column = get_column_letter(start_column)
    description_column = get_column_letter(start_column + 1)
    for column, label in ((code_column, header), (description_column, "说明")):
        cell = worksheet[f"{column}1"]
        cell.value = label
        cell.fill = REFERENCE_FILL
        cell.font = Font(bold=True)
    for row_index, value in enumerate(values, start=2):
        code_cell = worksheet.cell(row=row_index, column=start_column)
        _set_text_cell(code_cell, value)
        description_cell = worksheet.cell(row=row_index, column=start_column + 1)
        _set_text_cell(description_cell, guidance.get(value, ""))
        for cell in (code_cell, description_cell):
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = THIN_GRAY_BORDER
    worksheet.column_dimensions[code_column].width = 34
    worksheet.column_dimensions[description_column].width = 46


def _render_lists(worksheet) -> None:
    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "A2"
    _write_reference_list(
        worksheet,
        1,
        "asset_type",
        ASSET_TYPE_VALUES,
        ASSET_TYPE_GUIDANCE,
    )
    action_columns = {"security": 4, "fcn": 7, "option": 10, "cash": 13}
    for asset_type, start_column in action_columns.items():
        _write_reference_list(
            worksheet,
            start_column,
            f"{asset_type}_actions",
            TRANSACTION_ACTIONS[asset_type],
            TRANSACTION_ACTION_GUIDANCE,
        )
    _write_reference_list(
        worksheet,
        16,
        "fee_category",
        FEE_CATEGORY_VALUES,
        FEE_CATEGORY_GUIDANCE,
    )
    _write_reference_list(
        worksheet,
        19,
        "currency",
        CURRENCY_VALUES,
        {value: "支持的交易币种" for value in CURRENCY_VALUES},
    )
    _write_reference_list(
        worksheet,
        22,
        "option_type",
        OPTION_TYPE_VALUES,
        {"call": "看涨期权", "put": "看跌期权"},
    )
    named_ranges = [
        ("AssetTypeValues", "A", ASSET_TYPE_VALUES),
        ("FeeCategoryValues", "P", FEE_CATEGORY_VALUES),
        ("CurrencyValues", "S", CURRENCY_VALUES),
        ("OptionTypeValues", "V", OPTION_TYPE_VALUES),
    ]
    for asset_type, column_letter in zip(action_columns, ("D", "G", "J", "M"), strict=True):
        named_ranges.append(
            (f"{asset_type}_actions", column_letter, TRANSACTION_ACTIONS[asset_type])
        )
    for name, column_letter, values in named_ranges:
        worksheet.parent.defined_names.add(
            DefinedName(
                name,
                attr_text=(
                    f"'{LISTS_SHEET_NAME}'!${column_letter}$2:"
                    f"${column_letter}${len(values) + 1}"
                ),
            )
        )


def render_transaction_xlsx(records: Iterable[dict[str, object]]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = TRANSACTION_SHEET_NAME
    worksheet.freeze_panes = "A2"
    worksheet.sheet_view.showGridLines = False

    for column_index, column in enumerate(IMPORT_COLUMNS, start=1):
        cell = worksheet.cell(row=1, column=column_index)
        _set_text_cell(cell, column)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    worksheet.row_dimensions[1].height = 24

    rows = transaction_export_rows(records)
    for row_index, row in enumerate(rows, start=2):
        for column_index, column in enumerate(IMPORT_COLUMNS, start=1):
            value = _export_value(column, row.get(column))
            if value is None:
                continue
            cell = worksheet.cell(row=row_index, column=column_index)
            if isinstance(value, str):
                _set_text_cell(cell, value)
            else:
                cell.value = value
            if column in DATE_COLUMNS:
                cell.number_format = "yyyy-mm-dd"
            elif column == "trade_time":
                cell.number_format = "hh:mm"
            elif column in NUMERIC_COLUMNS:
                cell.number_format = "0.########"

    worksheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(IMPORT_COLUMNS))}{max(1, len(rows) + 1)}"
    )
    for index, column in enumerate(IMPORT_COLUMNS, start=1):
        width = max(12, min(34, len(column) + 2))
        worksheet.column_dimensions[get_column_letter(index)].width = width

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def render_transaction_xlsx_template() -> bytes:
    workbook = Workbook()
    instructions = workbook.active
    instructions.title = INSTRUCTIONS_SHEET_NAME
    transactions = workbook.create_sheet(TRANSACTION_SHEET_NAME)
    field_guide = workbook.create_sheet(FIELD_GUIDE_SHEET_NAME)
    examples = workbook.create_sheet(EXAMPLES_SHEET_NAME)
    lists = workbook.create_sheet(LISTS_SHEET_NAME)

    _render_template_instructions(instructions)
    transactions.freeze_panes = "A2"
    transactions.sheet_view.showGridLines = False
    _style_transaction_header(transactions, include_field_comments=True)
    transactions.auto_filter.ref = (
        f"A1:{get_column_letter(len(IMPORT_COLUMNS))}{TEMPLATE_INPUT_LAST_ROW}"
    )
    _add_template_validations(transactions)
    _add_template_required_indicators(transactions)
    _render_field_guide(field_guide)
    _render_examples(examples)
    _render_lists(lists)

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _cell_text(cell: Cell, column: str) -> str:
    if cell.data_type == "f":
        raise ValueError(
            f"{TRANSACTION_SHEET_NAME}!{cell.coordinate} contains a formula; "
            "transaction imports require literal values."
        )
    if cell.data_type == "e":
        raise ValueError(
            f"{TRANSACTION_SHEET_NAME}!{cell.coordinate} contains an Excel error value."
        )
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, datetime):
        if column in DATE_COLUMNS:
            return value.date().isoformat()
        if column == "trade_time":
            return value.time().replace(microsecond=0).isoformat(timespec="minutes")
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.replace(microsecond=0).isoformat(timespec="minutes")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(
                f"{TRANSACTION_SHEET_NAME}!{cell.coordinate} contains a non-finite number."
            )
        return format(Decimal(str(value)), "f")
    return str(value)


def transaction_xlsx_to_csv(xlsx_content: bytes) -> str:
    if not xlsx_content:
        raise ValueError("Excel content is required.")
    if len(xlsx_content) > MAX_CSV_BYTES:
        raise ValueError("Excel content exceeds the 5 MB limit.")
    try:
        workbook = load_workbook(
            BytesIO(xlsx_content),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except (BadZipFile, InvalidFileException, OSError, ValueError) as error:
        raise ValueError("The Excel file is not a valid .xlsx workbook.") from error
    try:
        if TRANSACTION_SHEET_NAME not in workbook.sheetnames:
            raise ValueError(
                f"Excel workbook must contain a '{TRANSACTION_SHEET_NAME}' worksheet."
            )
        worksheet = workbook[TRANSACTION_SHEET_NAME]
        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        header_names: list[str] = []
        for row_index, cells in enumerate(worksheet.iter_rows(), start=1):
            if row_index > MAX_CSV_ROWS + 1:
                raise ValueError("Excel content exceeds the 5,000 row limit.")
            if row_index == 1:
                header_names = [str(cell.value or "").strip() for cell in cells]
            writer.writerow(
                [
                    _cell_text(
                        cell,
                        header_names[column_index]
                        if row_index > 1 and column_index < len(header_names)
                        else "",
                    )
                    for column_index, cell in enumerate(cells)
                ]
            )
        return "\ufeff" + output.getvalue()
    finally:
        workbook.close()
