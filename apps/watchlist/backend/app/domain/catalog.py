from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from itertools import count


FIELD_CATEGORIES = [
    {"category_code": "general", "label": "General", "display_order": 1},
    {"category_code": "basics", "label": "Basics", "display_order": 2},
    {"category_code": "fees_terms", "label": "Fees & Terms", "display_order": 3},
    {
        "category_code": "performance_risk",
        "label": "Performance & Risk",
        "display_order": 4,
    },
    {"category_code": "exposure", "label": "Exposure", "display_order": 5},
    {
        "category_code": "ratings_analysis",
        "label": "Ratings & Analysis",
        "display_order": 6,
    },
    {"category_code": "monitoring", "label": "Monitoring", "display_order": 7},
    {
        "category_code": "custom_attributes",
        "label": "Custom Attributes",
        "display_order": 8,
    },
]

INSTRUMENT_ATTRIBUTE_DEFINITIONS = [
    {
        "attribute_key": "coverage_status",
        "label": "Coverage Status",
        "description": "Research status such as invested, focus, or watch.",
        "data_type": "single_select",
        "options": ["Invested", "Focus", "Watch", "Archived"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": True,
    },
    {
        "attribute_key": "focus_bucket",
        "label": "Focus Bucket",
        "description": "Internal attention bucket for near-term review priority.",
        "data_type": "single_select",
        "options": ["Tier 1", "Tier 2", "Tier 3"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "is_invested",
        "label": "Currently Invested",
        "description": "Whether the instrument is currently in the invested book.",
        "data_type": "boolean",
        "options": [],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "fund_regime",
        "label": "基金属性",
        "description": "公募还是私募，用于先分产品身份。",
        "data_type": "single_select",
        "options": ["公募", "私募"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "fund_vehicle",
        "label": "产品形态",
        "description": "ETF、场外开放式或资管计划等载体形态。",
        "data_type": "single_select",
        "options": ["ETF", "场外开放式", "LOF/场内开放式", "封闭式", "集合资管计划", "其他"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "strategy_family",
        "label": "策略大类",
        "description": "基金的主策略身份，用于第一层快速筛选。",
        "data_type": "single_select",
        "options": ["主动权益", "被动指数", "股票对冲", "CTA", "套利", "多策略", "债券策略", "宏观", "FOF/MOM", "期权/波动率", "其他"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "strategy_subtype",
        "label": "策略细分",
        "description": "更具体的实现类型，可多选。",
        "data_type": "multi_select",
        "options": [
            "市场中性",
            "指数增强",
            "股票多头",
            "量化选股",
            "宽基指数",
            "行业主题指数",
            "债券指数",
            "QDII",
            "商品CTA",
            "金融CTA",
            "趋势跟踪",
            "短周期CTA",
            "跨期套利",
            "跨品种套利",
            "统计套利",
            "期权套利",
            "可转债套利",
            "可转债策略",
            "宏观择时",
            "信用债",
            "复合多策略",
        ],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "implementation_style",
        "label": "实现方式",
        "description": "主观、量化或混合实现。",
        "data_type": "single_select",
        "options": ["主观", "量化", "主观+量化", "被动规则"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "trading_universe",
        "label": "交易市场/品种",
        "description": "主要交易的底层市场与工具，可多选。",
        "data_type": "multi_select",
        "options": ["A股", "港股", "美股", "股指期货", "国债期货", "商品期货", "期权", "可转债", "债券"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "alpha_source",
        "label": "主要收益来源",
        "description": "基金最核心的收益引擎，可多选。",
        "data_type": "multi_select",
        "options": [
            "选股Alpha",
            "趋势跟踪",
            "期限结构",
            "价差收敛",
            "波动率交易",
            "事件驱动",
            "贝塔增强",
            "指数复制",
            "主题/风格暴露",
            "高频/微观结构",
            "宏观择时",
            "Carry/票息",
        ],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "volatility_bucket",
        "label": "波动分层",
        "description": "产品的典型波动水平。",
        "data_type": "single_select",
        "options": ["低波", "中波", "高波"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "drawdown_control",
        "label": "回撤控制",
        "description": "产品在压力阶段的回撤管理能力。",
        "data_type": "single_select",
        "options": ["强", "中", "弱"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "equity_correlation_bucket",
        "label": "权益相关性",
        "description": "与权益市场的典型相关性水平。",
        "data_type": "single_select",
        "options": ["低相关", "中相关", "高相关", "负相关/危机对冲"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "preferred_regime",
        "label": "适配环境",
        "description": "更容易发挥优势的市场环境，可多选。",
        "data_type": "multi_select",
        "options": ["低波震荡", "高波趋势", "流动性宽松", "流动性收紧", "单边上涨", "单边下跌", "商品趋势", "股指震荡"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "weak_regime",
        "label": "脆弱环境",
        "description": "更容易失效或放大风险的环境，可多选。",
        "data_type": "multi_select",
        "options": ["高波反转", "流动性冲击", "无趋势震荡", "权益急跌后V形", "商品快速切换", "基差异常/挤仓"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "style_stability",
        "label": "风格稳定性",
        "description": "策略暴露和执行风格是否稳定。",
        "data_type": "single_select",
        "options": ["高", "中", "低"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
    {
        "attribute_key": "transparency_quality",
        "label": "研究透明度",
        "description": "产品对外信息披露与可研究性质量。",
        "data_type": "single_select",
        "options": ["高", "中", "低"],
        "is_groupable": True,
        "is_filterable": True,
        "is_view_column": True,
        "default_visible": False,
    },
]

FIELD_REGISTRY = [
    {
        "field_key": "asset_name",
        "label": "Name",
        "description": "Canonical instrument display name from the shared registry.",
        "category_code": "general",
        "data_type": "string",
        "formatter_code": "text",
        "sort_mode": "alpha",
        "filter_mode": "text",
        "group_mode": "discrete",
        "asset_scope_json": [],
        "product_scope_json": [],
        "availability_rule_json": {},
        "source_domain": "shared",
        "source_metric_code": "instrument.asset_name",
        "default_width": 320,
        "default_visible": True,
    },
    {
        "field_key": "asset_type",
        "label": "Asset Type",
        "description": "Top-level instrument type resolved from shared identity.",
        "category_code": "general",
        "data_type": "string",
        "formatter_code": "text",
        "sort_mode": "alpha",
        "filter_mode": "multi_select",
        "group_mode": "discrete",
        "asset_scope_json": [],
        "product_scope_json": [],
        "availability_rule_json": {},
        "source_domain": "shared",
        "source_metric_code": "instrument.asset_type",
        "default_width": 140,
        "default_visible": False,
    },
    {
        "field_key": "ticker_or_isin",
        "label": "Ticker / ISIN",
        "description": "Primary public identifier for the row.",
        "category_code": "general",
        "data_type": "string",
        "formatter_code": "identifier",
        "sort_mode": "alpha",
        "filter_mode": "text",
        "group_mode": "none",
        "asset_scope_json": [],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["primary_identifier"]},
        "source_domain": "shared",
        "source_metric_code": "instrument.identifier.primary",
        "default_width": 140,
        "default_visible": True,
    },
    {
        "field_key": "latest_quote",
        "label": "Latest Quote",
        "description": "Most recent point from the active quote series used by the detail quote view.",
        "category_code": "general",
        "data_type": "number",
        "formatter_code": "decimal",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "bucket",
        "asset_scope_json": [],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["asset_chart_read_model"]},
        "source_domain": "read_model",
        "source_metric_code": "asset_chart_read_model.series.latest_quote",
        "default_width": 130,
        "default_visible": False,
    },
    {
        "field_key": "latest_quote_date",
        "label": "Quote Date",
        "description": "As-of date for the most recent active quote series point.",
        "category_code": "general",
        "data_type": "date",
        "formatter_code": "date",
        "sort_mode": "date",
        "filter_mode": "date_range",
        "group_mode": "bucket",
        "asset_scope_json": [],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["asset_chart_read_model"]},
        "source_domain": "read_model",
        "source_metric_code": "asset_chart_read_model.series.latest_quote_date",
        "default_width": 140,
        "default_visible": False,
    },
    {
        "field_key": "management_firm_name",
        "label": "Management Firm",
        "description": "Current manager / advisor firm.",
        "category_code": "basics",
        "data_type": "string",
        "formatter_code": "text",
        "sort_mode": "alpha",
        "filter_mode": "multi_select",
        "group_mode": "discrete",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["management_firm"]},
        "source_domain": "master",
        "source_metric_code": "management_firm.name",
        "default_width": 220,
        "default_visible": False,
    },
    {
        "field_key": "category_name",
        "label": "Category",
        "description": "Peer group/category used for ranking and comparison.",
        "category_code": "basics",
        "data_type": "string",
        "formatter_code": "text",
        "sort_mode": "alpha",
        "filter_mode": "multi_select",
        "group_mode": "discrete",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["peer_group"]},
        "source_domain": "master",
        "source_metric_code": "peer_group.display_name",
        "default_width": 240,
        "default_visible": True,
    },
    {
        "field_key": "asset_class",
        "label": "Asset Class",
        "description": "Top-level asset class for group by and filtering.",
        "category_code": "basics",
        "data_type": "string",
        "formatter_code": "text",
        "sort_mode": "alpha",
        "filter_mode": "multi_select",
        "group_mode": "discrete",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["fund_product"]},
        "source_domain": "master",
        "source_metric_code": "fund_product.fund_type",
        "default_width": 160,
        "default_visible": False,
    },
    {
        "field_key": "aum",
        "label": "AUM",
        "description": "Current assets under management from canonical facts.",
        "category_code": "basics",
        "data_type": "number",
        "formatter_code": "currency_compact",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "bucket",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["aum_fact"]},
        "source_domain": "facts",
        "source_metric_code": "aum_fact.value",
        "default_width": 140,
        "default_visible": True,
    },
    {
        "field_key": "price_chart_1m",
        "label": "Price Chart",
        "description": "1-month NAV spark chart from the current chart read model.",
        "category_code": "performance_risk",
        "data_type": "sparkline",
        "formatter_code": "sparkline",
        "sort_mode": "none",
        "filter_mode": "none",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["fund_chart_read_model"]},
        "source_domain": "read_model",
        "source_metric_code": "fund_chart_read_model.series",
        "default_width": 140,
        "default_visible": False,
    },
    {
        "field_key": "overall_rating",
        "label": "Overall Rating",
        "description": "Internal score-based rating derived from the current methodology.",
        "category_code": "ratings_analysis",
        "data_type": "integer",
        "formatter_code": "stars",
        "sort_mode": "numeric",
        "filter_mode": "multi_select",
        "group_mode": "discrete",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["fund_score_snapshot"]},
        "source_domain": "score",
        "source_metric_code": "fund_score_snapshot.overall_rating",
        "default_width": 140,
        "default_visible": True,
    },
    {
        "field_key": "analyst_stance",
        "label": "Analyst Stance",
        "description": "Current house view from the score snapshot.",
        "category_code": "ratings_analysis",
        "data_type": "string",
        "formatter_code": "badge",
        "sort_mode": "alpha",
        "filter_mode": "multi_select",
        "group_mode": "discrete",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["fund_score_snapshot"]},
        "source_domain": "score",
        "source_metric_code": "fund_score_snapshot.analyst_stance",
        "default_width": 160,
        "default_visible": True,
    },
    {
        "field_key": "return_ytd",
        "label": "Total Return (YTD)",
        "description": "Derived from canonical NAV facts.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["performance_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "performance_snapshot.return_ytd",
        "default_width": 150,
        "default_visible": True,
    },
    {
        "field_key": "return_1w",
        "label": "Total Return (1W)",
        "description": "1-week total return.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["performance_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "performance_snapshot.return_1w",
        "default_width": 150,
        "default_visible": True,
    },
    {
        "field_key": "return_1y",
        "label": "Total Return (1Y)",
        "description": "1-year annualized total return.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["performance_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "performance_snapshot.return_1y",
        "default_width": 150,
        "default_visible": True,
    },
    {
        "field_key": "return_3y",
        "label": "Total Return (3Y)",
        "description": "3-year annualized total return.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["performance_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "performance_snapshot.return_3y_annualized",
        "default_width": 150,
        "default_visible": True,
    },
    {
        "field_key": "return_5y",
        "label": "Total Return (5Y)",
        "description": "5-year annualized total return.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["performance_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "performance_snapshot.return_5y_annualized",
        "default_width": 150,
        "default_visible": True,
    },
    {
        "field_key": "max_drawdown",
        "label": "Max Drawdown",
        "description": "Maximum drawdown from the active performance snapshot.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["performance_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "performance_snapshot.max_drawdown",
        "default_width": 150,
        "default_visible": False,
    },
    {
        "field_key": "volatility",
        "label": "Volatility",
        "description": "Risk snapshot volatility for the selected window.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["risk_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "risk_snapshot.volatility",
        "default_width": 140,
        "default_visible": False,
    },
    {
        "field_key": "sharpe_ratio",
        "label": "Sharpe Ratio",
        "description": "Current risk-adjusted return metric.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "decimal",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["risk_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "risk_snapshot.sharpe_ratio",
        "default_width": 140,
        "default_visible": False,
    },
    {
        "field_key": "duration",
        "label": "Duration",
        "description": "Weighted effective duration derived from exposure analytics.",
        "category_code": "exposure",
        "data_type": "number",
        "formatter_code": "decimal",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "bucket",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["exposure_analytics_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "exposure_analytics_snapshot.weighted_duration",
        "default_width": 140,
        "default_visible": True,
    },
    {
        "field_key": "avg_credit_rating",
        "label": "Avg Credit Rating",
        "description": "Exposure-weighted average credit quality.",
        "category_code": "exposure",
        "data_type": "string",
        "formatter_code": "text",
        "sort_mode": "alpha",
        "filter_mode": "multi_select",
        "group_mode": "discrete",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["exposure_analytics_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "exposure_analytics_snapshot.avg_credit_rating",
        "default_width": 150,
        "default_visible": True,
    },
    {
        "field_key": "yield_to_worst",
        "label": "Yield To Worst",
        "description": "Exposure-weighted yield to worst derived from holdings analytics.",
        "category_code": "exposure",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "bucket",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["exposure_analytics_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "exposure_analytics_snapshot.weighted_yield_to_worst",
        "default_width": 150,
        "default_visible": False,
    },
    {
        "field_key": "last_nav_date",
        "label": "Last NAV Date",
        "description": "Most recent canonical NAV fact adopted for the fund.",
        "category_code": "monitoring",
        "data_type": "date",
        "formatter_code": "date",
        "sort_mode": "date",
        "filter_mode": "date_range",
        "group_mode": "bucket",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["nav_fact"]},
        "source_domain": "facts",
        "source_metric_code": "nav_fact.as_of_date",
        "default_width": 150,
        "default_visible": False,
    },
    {
        "field_key": "exposure_updated_at",
        "label": "Holdings Updated At",
        "description": "Latest holdings statement adoption time.",
        "category_code": "monitoring",
        "data_type": "datetime",
        "formatter_code": "datetime",
        "sort_mode": "date",
        "filter_mode": "date_range",
        "group_mode": "bucket",
        "asset_scope_json": ["fund"],
        "product_scope_json": ["mutual_fund", "cef", "etf"],
        "availability_rule_json": {"requires": ["holding_snapshot"]},
        "source_domain": "facts",
        "source_metric_code": "holding_snapshot.source_cutoff_at",
        "default_width": 180,
        "default_visible": False,
    },
    {
        "field_key": "data_freshness_status",
        "label": "Freshness",
        "description": "System freshness status derived from the current read model.",
        "category_code": "monitoring",
        "data_type": "string",
        "formatter_code": "status",
        "sort_mode": "alpha",
        "filter_mode": "multi_select",
        "group_mode": "discrete",
        "asset_scope_json": [],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["watchlist_row_read_model"]},
        "source_domain": "read_model",
        "source_metric_code": "watchlist_row_read_model.data_freshness_status",
        "default_width": 140,
        "default_visible": True,
    },
]

WATCHLISTS = [
    {
        "watchlist_id": "coverage",
        "name": "Coverage",
        "description": "Primary fixed income fund coverage universe.",
        "item_count": 128,
        "owner_type": "team",
        "owner_id": "investment-team",
        "default_view_id": "overview",
    },
    {
        "watchlist_id": "focus",
        "name": "Focus",
        "description": "Short list for current committee review.",
        "item_count": 24,
        "owner_type": "team",
        "owner_id": "investment-team",
        "default_view_id": "ratings",
    },
]

WATCHLIST_VIEWS = {
    "coverage": [
        {
            "view_id": "overview",
            "name": "Overview",
            "kind": "system",
            "default_group_by": "none",
            "default_sort": [{"field": "overall_rating", "direction": "desc"}],
            "default_advanced_filters": {
                "type": "group",
                "logic": "and",
                "conditions": [
                    {
                        "type": "rule",
                        "field": "attr.coverage_status",
                        "operator": "not_in",
                        "value": ["Archived"],
                    }
                ],
            },
            "columns": [
                "ticker_or_isin",
                "asset_name",
                "price_chart_1m",
                "attr.coverage_status",
                "overall_rating",
                "analyst_stance",
                "category_name",
                "aum",
                "return_1w",
                "return_1y",
                "return_3y",
                "duration",
                "avg_credit_rating",
                "data_freshness_status",
            ],
        },
        {
            "view_id": "performance",
            "name": "Performance",
            "kind": "system",
            "default_group_by": "category_name",
            "default_sort": [{"field": "return_1y", "direction": "desc"}],
            "columns": [
                "asset_name",
                "category_name",
                "return_ytd",
                "return_1w",
                "return_1y",
                "return_3y",
                "return_5y",
                "max_drawdown",
                "volatility",
                "data_freshness_status",
            ],
        },
        {
            "view_id": "ratings",
            "name": "Ratings",
            "kind": "system",
            "default_group_by": "analyst_stance",
            "default_sort": [{"field": "overall_rating", "direction": "desc"}],
            "default_advanced_filters": None,
            "columns": [
                "asset_name",
                "attr.coverage_status",
                "overall_rating",
                "analyst_stance",
                "category_name",
                "aum",
                "data_freshness_status",
            ],
        },
        {
            "view_id": "exposure",
            "name": "Exposure",
            "kind": "system",
            "default_group_by": "asset_class",
            "default_sort": [{"field": "duration", "direction": "desc"}],
            "default_advanced_filters": None,
            "columns": [
                "asset_name",
                "attr.focus_bucket",
                "category_name",
                "duration",
                "avg_credit_rating",
                "exposure_updated_at",
                "data_freshness_status",
            ],
        },
    ],
    "focus": [
        {
            "view_id": "ratings",
            "name": "Ratings",
            "kind": "system",
            "default_group_by": "none",
            "default_sort": [{"field": "overall_rating", "direction": "desc"}],
            "default_advanced_filters": None,
            "columns": [
                "asset_name",
                "attr.coverage_status",
                "overall_rating",
                "analyst_stance",
                "category_name",
                "data_freshness_status",
            ],
        },
        {
            "view_id": "risk",
            "name": "Risk",
            "kind": "system",
            "default_group_by": "category_name",
            "default_sort": [{"field": "volatility", "direction": "desc"}],
            "default_advanced_filters": None,
            "columns": [
                "asset_name",
                "attr.focus_bucket",
                "category_name",
                "volatility",
                "sharpe_ratio",
                "max_drawdown",
                "data_freshness_status",
            ],
        },
    ],
}

WATCHLIST_ROWS = [
    {
        "watchlist_id": "coverage",
        "fund_id": "fax",
        "asset_name": "abrdn Asia-Pacific Income Fund Inc",
        "share_class": "Listed",
        "ticker_or_isin": "FAX",
        "management_firm_name": "abrdn",
        "category_name": "Emerging Markets Bond",
        "asset_class": "Fixed Income",
        "fund_type": "cef",
        "domicile": "US",
        "overall_rating": 4,
        "analyst_stance": "Positive",
        "aum": 658050000,
        "return_ytd": 0.15,
        "return_1y": 10.85,
        "return_3y": 5.53,
        "return_5y": -0.91,
        "max_drawdown": -20.25,
        "volatility": 9.37,
        "sharpe_ratio": 0.86,
        "duration": 5.55,
        "yield_to_worst": 7.17,
        "avg_credit_rating": "BB+",
        "attributes": {
            "coverage_status": "Invested",
            "focus_bucket": "Tier 1",
            "is_invested": True,
        },
        "exposure_updated_at": "2026-01-31T00:00:00Z",
        "last_nav_date": "2026-04-10",
        "last_recalculated_at": "2026-04-13T00:21:00Z",
        "data_freshness_status": "fresh",
        "staleness_reason": None,
    },
    {
        "watchlist_id": "coverage",
        "fund_id": "cloz",
        "asset_name": "Eldridge BBB-B CLO ETF",
        "share_class": "ETF",
        "ticker_or_isin": "CLOZ",
        "management_firm_name": "Eldridge",
        "category_name": "Securitized Bond - Focused",
        "asset_class": "Fixed Income",
        "fund_type": "etf",
        "domicile": "US",
        "overall_rating": 3,
        "analyst_stance": "Watch",
        "aum": 588020000,
        "return_ytd": -0.95,
        "return_1y": 7.43,
        "return_3y": 9.99,
        "return_5y": None,
        "max_drawdown": -6.48,
        "volatility": 5.86,
        "sharpe_ratio": 0.34,
        "duration": 3.92,
        "yield_to_worst": 6.11,
        "avg_credit_rating": "BBB",
        "attributes": {
            "coverage_status": "Watch",
            "focus_bucket": "Tier 2",
            "is_invested": False,
        },
        "exposure_updated_at": "2026-03-31T00:00:00Z",
        "last_nav_date": "2026-04-10",
        "last_recalculated_at": "2026-04-12T15:05:00Z",
        "data_freshness_status": "pending_recalc",
        "staleness_reason": "Holdings statement adopted; exposure analytics rerun pending.",
    },
    {
        "watchlist_id": "focus",
        "fund_id": "new-asia-income",
        "asset_name": "New Asia Income Opportunities Fund",
        "share_class": "A",
        "ticker_or_isin": "US000000001",
        "management_firm_name": "North Harbor",
        "category_name": "Asia Credit",
        "asset_class": "Fixed Income",
        "fund_type": "mutual_fund",
        "domicile": "HK",
        "overall_rating": 5,
        "analyst_stance": "High Conviction",
        "aum": 1240000000,
        "return_ytd": 1.92,
        "return_1y": 14.12,
        "return_3y": 8.87,
        "return_5y": 6.11,
        "max_drawdown": -9.72,
        "volatility": 8.14,
        "sharpe_ratio": 1.02,
        "duration": 4.88,
        "yield_to_worst": 5.44,
        "avg_credit_rating": "A-",
        "attributes": {
            "coverage_status": "Focus",
            "focus_bucket": "Tier 1",
            "is_invested": True,
        },
        "exposure_updated_at": "2026-04-01T00:00:00Z",
        "last_nav_date": "2026-04-11",
        "last_recalculated_at": "2026-04-13T00:30:00Z",
        "data_freshness_status": "fresh",
        "staleness_reason": None,
    },
]

FUND_SUMMARIES = {
    "fax": {
        "fund_id": "fax",
        "fund_name": "abrdn Asia-Pacific Income Fund Inc",
        "ticker_or_isin": "FAX",
        "rating_as_of": "2026-03-31",
        "category_name": "Emerging Markets Bond",
        "overall_rating": 4,
        "analyst_stance": "Positive",
        "instrument_attributes": {
            "coverage_status": "Invested",
            "focus_bucket": "Tier 1",
            "is_invested": True,
        },
        "key_stats": [
            {"label": "AUM", "value": "$658.05M"},
            {"label": "Last NAV Date", "value": "2026-04-10"},
            {"label": "Holdings Updated", "value": "2026-01-31"},
            {"label": "Freshness", "value": "Fresh"},
        ],
        "freshness": {
            "data_freshness_status": "fresh",
            "last_fact_update_at": "2026-04-11T06:00:00Z",
            "last_recalculated_at": "2026-04-13T00:21:00Z",
            "last_successful_snapshot_at": "2026-04-13T00:21:00Z",
            "staleness_reason": None,
        },
        "quick_monitoring_items": [
            "NAV facts current through 2026-04-10.",
            "Exposure analytics based on the 2026-01 valuation statement.",
            "No unresolved freshness failures in the current read models.",
        ],
        "tabs": [
            "summary",
            "chart",
            "performance",
            "risk",
            "price",
            "exposure",
            "people",
            "strategy",
            "documents",
            "research",
            "monitoring",
        ],
    }
}

FUND_MANUAL_PROFILES = {
    "fax": {
        "people": {
            "overview": {
                "inception_date": "1986-04-24",
                "number_of_managers": 4,
                "longest_tenure_years": 12.8,
                "average_tenure_years": 6.1,
                "advisor": "abrdn Asia Limited",
                "sub_advisor": "abrdn Investments Limited",
            },
            "team": [
                {"name": "Kenneth Akintewe", "role": "Lead PM", "start_date": "2013-06-30"},
                {"name": "Tai Lin-Yian", "role": "Portfolio Manager", "start_date": "2021-10-31"},
                {"name": "Adam McCabe", "role": "Portfolio Manager", "start_date": "2021-10-31"},
            ],
            "notes": [
                "People information is manually maintained by the investment team.",
                "Manager timeline and biographies should remain editable rather than OCR-derived.",
            ],
        },
        "strategy": {
            "summary": "The fund seeks current income and may also achieve incidental capital appreciation by investing primarily in Asia-Pacific debt securities.",
            "investment_objective": "Current income first, incidental capital appreciation second.",
            "process_bullets": [
                "Focus on Asia-Pacific fixed income opportunities across sovereign and corporate issuers.",
                "Use bottom-up credit selection with top-down regional risk control.",
                "Portfolio construction should stay editable by analysts and PMs.",
            ],
            "risk_controls": [
                "Duration and credit quality are monitored but should not be hard-coded to a single methodology yet.",
                "Dividend and distribution treatment must be reflected in the chosen NAV basis.",
            ],
            "notes": [
                "Strategy text is manually curated.",
            ],
        },
        "price": {
            "overview": {
                "total_expense_ratio": 4.22,
                "adjusted_expense_ratio": 4.22,
                "management_fee": 0.65,
                "interest_expense_fees": 0.0,
                "redemption_fee": 0.0,
                "minimum_initial_investment": None,
            },
            "distribution_policy": "Monthly distribution policy with managed payout considerations.",
            "policy_text": "The manager is entitled to a fee payable monthly by the fund. Breakpoints and share-class level expenses should remain manually editable by the research team.",
            "fee_notes": [
                "Expense data is manually maintained and may reflect the latest shareholder report.",
                "Leverage-linked or financing-related fees should be reviewed separately from headline TER.",
            ],
            "notes": [
                "Price tab is maintained manually and should not depend on OCR.",
            ],
        },
        "documents": {
            "current_documents": [
                {
                    "title": "Annual Report",
                    "document_type": "report",
                    "as_of_date": "2025-12-31",
                    "source": "Manager Website",
                    "status": "Adopted",
                    "version_label": "FY2025",
                },
                {
                    "title": "Monthly Factsheet",
                    "document_type": "factsheet",
                    "as_of_date": "2026-03-31",
                    "source": "Email Attachment",
                    "status": "Adopted",
                    "version_label": "Mar 2026",
                },
            ],
            "recent_imports": [
                {
                    "import_type": "NAV",
                    "received_at": "2026-04-11T06:00:00Z",
                    "source": "Daily NAV attachments",
                    "status": "Completed",
                    "file_name": "fax_nav_20260410.xlsx",
                },
                {
                    "import_type": "Valuation Statement",
                    "received_at": "2026-02-02T09:12:00Z",
                    "source": "Operations Mailbox",
                    "status": "Completed",
                    "file_name": "fax_valuation_20260131.pdf",
                },
            ],
            "extraction_reviews": [
                {
                    "document_title": "Monthly Factsheet",
                    "extract_type": "Factsheet Review",
                    "status": "Adopted",
                    "adopted_version": "Mar 2026",
                    "updated_at": "2026-04-01T10:15:00Z",
                },
                {
                    "document_title": "Annual Report",
                    "extract_type": "Narrative Review",
                    "status": "Pending Refresh",
                    "adopted_version": "FY2025",
                    "updated_at": "2026-03-20T15:30:00Z",
                },
            ],
            "notes": [
                "Documents should remain instrument-level and support adopted versus latest versions.",
                "NAV imports and valuation statements will later connect to automated extraction and adoption flows.",
            ],
        },
        "research": {
            "overview": {
                "current_view": "Positive",
                "research_status": "Active",
                "dd_status": "In Progress",
                "odd_status": "Monitoring",
                "ic_status": "Pre-IC",
                "decision": "Watch for upgrade",
                "next_review_date": "2026-05-15",
                "primary_analyst": "Asia Credit Team",
            },
            "thesis": "The fund offers differentiated access to Asia-Pacific debt with income support, but its discount dynamics, leverage profile, and manager execution need continuous review against category peers.",
            "conclusions": [
                {
                    "conclusion": "Income profile remains competitive versus peers.",
                    "evidence_ref": "Monthly factsheet Mar 2026",
                    "status": "Current",
                },
                {
                    "conclusion": "Discount volatility and leverage still require regular monitoring.",
                    "evidence_ref": "Annual report FY2025",
                    "status": "Watch",
                },
            ],
            "notes": [
                "Research tab should stay concise and decision-oriented rather than replicate the old workflow OS.",
                "Detailed DD / ODD artifacts can be linked later without changing this page structure.",
            ],
        },
        "nav_settings": {
            "nav_basis_preference": "auto",
            "source_mode": "manual",
            "source_email": "",
            "source_location": "Manual upload",
            "source_api_profile": "",
            "default_benchmark_asset_id": None,
            "peer_baseline_asset_ids": [],
        },
    }
}

FUND_CHARTS = {
    "fax": {
        "fund_id": "fax",
        "base_series_type": "nav",
        "currency": "USD",
        "date_range": {"start": "2023-04-10", "end": "2026-04-10"},
        "series": [
            {
                "name": "FAX NAV",
                "points": [
                    {"date": "2023-04-10", "value": 10.0},
                    {"date": "2024-04-10", "value": 10.88},
                    {"date": "2025-04-10", "value": 11.42},
                    {"date": "2026-04-10", "value": 11.75318},
                ],
            }
        ],
        "available_compare_targets": ["category_nav", "benchmark_index"],
    }
}

FUND_PERFORMANCE = {
    "fax": {
        "growth_chart_series": [
            {"name": "Investment (NAV)", "value": 12094},
            {"name": "Category (NAV)", "value": 16004},
            {"name": "Index (Price)", "value": 14806},
        ],
        "annual_returns": [
            {"year": 2023, "investment_nav": 10.55, "category_nav": 14.80, "index_price": 9.00},
            {"year": 2024, "investment_nav": 0.93, "category_nav": 8.15, "index_price": 4.34},
            {"year": 2025, "investment_nav": 9.98, "category_nav": 23.11, "index_price": 10.88},
        ],
        "trailing_returns": [
            {"window": "1M", "investment_nav": -1.17, "category_nav": 0.37, "index_nav": -0.30},
            {"window": "YTD", "investment_nav": 0.15, "category_nav": 2.84, "index_nav": 0.23},
            {"window": "1Y", "investment_nav": 10.85, "category_nav": 26.57, "index_nav": 10.59},
            {"window": "3Y", "investment_nav": 5.53, "category_nav": 14.82, "index_nav": 7.11},
        ],
        "ranking": {
            "quartile": 4,
            "percentile": 100,
            "sample_count": 6,
            "category_name": "Emerging Markets Bond",
        },
        "snapshot_metadata": {
            "as_of_date": "2026-04-10",
            "methodology_version": "performance/v1",
            "source_cutoff_at": "2026-04-11T06:00:00Z",
        },
    }
}

FUND_RISK = {
    "fax": {
        "risk_overview": {
            "exposure_risk_score": 36,
            "risk_level": "Moderate",
            "risk_vs_category": "below_average",
            "return_vs_category": "low",
        },
        "scatter_points": [
            {"name": "FAX", "return": 5.53, "volatility": 8.89},
            {"name": "Category", "return": 14.82, "volatility": 9.37},
            {"name": "Index", "return": 7.11, "volatility": 5.86},
        ],
        "risk_metrics": [
            {"metric": "Alpha", "investment": 1.44, "category": 9.33, "index": 3.00},
            {"metric": "Beta", "investment": 1.33, "category": 1.15, "index": 0.97},
            {"metric": "Sharpe Ratio", "investment": 0.00, "category": 0.86, "index": 0.34},
            {"metric": "Standard Deviation", "investment": 8.89, "category": 9.37, "index": 5.86},
        ],
        "drawdown_summary": {
            "maximum": -7.60,
            "peak_date": "2023-08-01",
            "valley_date": "2023-10-31",
            "max_duration_months": 3,
        },
        "risk_structure": {
            "rows": [],
        },
        "current_watch": {
            "overall_level": "Normal",
            "rows": [],
            "note": None,
        },
        "change_monitor": {
            "rows": [],
            "note": None,
        },
        "snapshot_metadata": {
            "as_of_date": "2026-04-10",
            "methodology_version": "risk/v1",
            "source_cutoff_at": "2026-04-11T06:00:00Z",
        },
    }
}

FUND_EXPOSURE_SUMMARIES = {
    "fax": {
        "allocation_blocks": {
            "asset_allocation": [
                {"name": "Fixed Income", "investment": 97.40, "category": 104.68, "index": 100.00},
                {"name": "Cash", "investment": 2.60, "category": 8.05, "index": 0.00},
            ],
            "bond_breakdown": [
                {"name": "BBB", "investment": 43.90, "category": 28.83},
                {"name": "BB", "investment": 17.00, "category": 27.72},
                {"name": "A", "investment": 17.20, "category": 8.64},
            ],
        },
        "style_box": {
            "style_box_code": "FI-MOD-LTD",
            "avg_credit_rating": "BBB",
            "weighted_duration": 5.55,
            "weighted_maturity": 9.54,
            "yield_to_maturity": 7.17,
        },
        "liquidity_leverage": {
            "market_value": 618000000,
            "leverage_ratio": 39.30,
            "avg_daily_shares_traded": 171380,
            "avg_daily_value_traded": 2630000,
        },
        "valuation_statistics": {
            "current_price": 14.97,
            "current_nav": 15.94,
            "three_year_low_price": 13.98,
            "three_year_high_price": 17.55,
        },
        "holdings_summary": {
            "total_holdings": 229,
            "bond_holdings": 224,
            "other_holdings": 4,
            "top10_concentration": 17,
            "reported_turnover": 34.00,
        },
        "snapshot_metadata": {
            "as_of_date": "2026-01-31",
            "methodology_version": "exposure/v1",
            "source_cutoff_at": "2026-02-28T00:00:00Z",
        },
    }
}

FUND_EXPOSURE_HOLDINGS = {
    "fax": [
        {
            "holding_name": "Mexico (United Mexican States) 8.5%",
            "security_identifier": "MXGOV-2029-8.5",
            "issuer_name": "United Mexican States",
            "issuer_type": "Sovereign",
            "portfolio_weight": 2.49,
            "market_value": 27257606,
            "quantity": 32067890,
            "currency": "MXN",
            "market_price": 102.11,
            "share_change_pct": 0.00,
            "maturity_date": "2029-05-31",
            "coupon_rate": 8.50,
            "credit_rating": "BBB",
            "effective_duration": 2.84,
            "modified_duration": 2.71,
            "yield_to_worst": 7.91,
            "sector": "Government",
            "country_code": "MX",
        },
        {
            "holding_name": "China (People's Republic Of) 1.67%",
            "security_identifier": "CNGOV-2035-1.67",
            "issuer_name": "People's Republic of China",
            "issuer_type": "Sovereign",
            "portfolio_weight": 2.28,
            "market_value": 24992103,
            "quantity": 40100200,
            "currency": "CNY",
            "market_price": 91.42,
            "share_change_pct": 100.00,
            "maturity_date": "2035-05-25",
            "coupon_rate": 1.67,
            "credit_rating": "A+",
            "effective_duration": 7.41,
            "modified_duration": 7.18,
            "yield_to_worst": 2.44,
            "sector": "Government",
            "country_code": "CN",
        },
        {
            "holding_name": "Hutchison Whampoa Finance (CI) Limited 7.5%",
            "security_identifier": "HWF-CI-2027-7.5",
            "issuer_name": "Hutchison Whampoa Finance (CI) Limited",
            "issuer_type": "Corporate",
            "portfolio_weight": 1.49,
            "market_value": 16262878,
            "quantity": 15400000,
            "currency": "USD",
            "market_price": 105.61,
            "share_change_pct": 0.00,
            "maturity_date": "2027-08-01",
            "coupon_rate": 7.50,
            "credit_rating": "BB+",
            "effective_duration": 1.26,
            "modified_duration": 1.19,
            "yield_to_worst": 6.37,
            "sector": "Corporate",
            "country_code": "KY",
        },
    ]
}

FUND_RATINGS = {
    "fax": {
        "overall_rating": 4,
        "overall_score": 78,
        "analyst_stance": "Positive",
        "methodology_version": "house-rating/v1",
        "dimension_scores": [
            {"dimension_code": "people", "score": 82, "confidence_score": 0.78},
            {"dimension_code": "process", "score": 76, "confidence_score": 0.69},
            {"dimension_code": "exposure", "score": 74, "confidence_score": 0.72},
            {"dimension_code": "risk", "score": 61, "confidence_score": 0.75},
            {"dimension_code": "price", "score": 55, "confidence_score": 0.82},
        ],
        "override_info": {
            "has_override": False,
            "approved_by": None,
            "approved_at": None,
        },
    }
}

RECALC_JOBS = [
    {
        "recalc_job_id": "job_0001",
        "job_type": "performance",
        "fund_id": "fax",
        "trigger_type": "fact_adopted",
        "trigger_ref_type": "nav_fact",
        "trigger_ref_id": "nav_2026_04_10",
        "job_status": "completed",
        "priority": 80,
        "dedupe_key": "performance:fax:2026-04-10",
        "payload_json": {"as_of_date": "2026-04-10"},
        "enqueued_at": "2026-04-13T00:12:00Z",
        "started_at": "2026-04-13T00:12:05Z",
        "finished_at": "2026-04-13T00:12:08Z",
        "error_message": None,
    },
    {
        "recalc_job_id": "job_0002",
        "job_type": "exposure",
        "fund_id": "cloz",
        "trigger_type": "fact_adopted",
        "trigger_ref_type": "holding_snapshot",
        "trigger_ref_id": "holding_cloz_2026_03_31",
        "job_status": "queued",
        "priority": 90,
        "dedupe_key": "exposure:cloz:holding_cloz_2026_03_31",
        "payload_json": {"as_of_date": "2026-03-31"},
        "enqueued_at": "2026-04-12T15:05:00Z",
        "started_at": None,
        "finished_at": None,
        "error_message": None,
    },
]

_job_counter = count(3)


def _deepcopy(data: object) -> object:
    return deepcopy(data)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _attribute_field_key(attribute_key: str) -> str:
    return f"attr.{attribute_key}"


def _build_attribute_field_definition(
    definition: dict[str, object],
) -> dict[str, object]:
    data_type = str(definition["data_type"])
    filter_mode = "multi_select"
    group_mode = "discrete"
    formatter_code = "text"
    if data_type == "boolean":
        formatter_code = "boolean"
    elif data_type == "number":
        formatter_code = "decimal"
        filter_mode = "range"
        group_mode = "bucket"
    elif data_type == "date":
        formatter_code = "date"
        filter_mode = "date_range"
        group_mode = "bucket"
    elif data_type == "multi_select":
        formatter_code = "tags"
    return {
        "field_key": _attribute_field_key(str(definition["attribute_key"])),
        "label": definition["label"],
        "description": definition.get("description"),
        "category_code": "custom_attributes",
        "data_type": data_type,
        "formatter_code": formatter_code,
        "sort_mode": "alpha" if data_type not in {"number", "date"} else "numeric",
        "filter_mode": filter_mode,
        "group_mode": group_mode if definition.get("is_groupable", True) else "none",
        "asset_scope_json": ["fund"],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["instrument_attribute_value"]},
        "source_domain": "custom_attribute",
        "source_metric_code": f"instrument_attribute_value.{definition['attribute_key']}",
        "default_width": 160,
        "default_visible": bool(definition.get("default_visible", False)),
        "options": _deepcopy(definition.get("options", [])),
        "attribute_key": definition["attribute_key"],
    }


def _current_field_registry() -> list[dict[str, object]]:
    fields = _deepcopy(FIELD_REGISTRY)
    fields.extend(
        _build_attribute_field_definition(item)
        for item in INSTRUMENT_ATTRIBUTE_DEFINITIONS
        if item.get("is_view_column", True)
    )
    return fields


def _resolve_field_value(row: dict[str, object], field: str) -> object:
    if field.startswith("attr."):
        attribute_key = field.split(".", 1)[1]
        return row.get("attributes", {}).get(attribute_key)
    return row.get(field)


def _normalize_string(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return " ".join(str(value).replace("_", " ").lower().split())


def _matches_operator(
    left: object,
    operator: str,
    right: object,
) -> bool:
    if operator == "exists":
        return left is not None
    if left is None:
        return False
    if operator == "eq":
        if isinstance(left, str) or isinstance(right, str):
            return _normalize_string(left) == _normalize_string(right)
        return left == right
    if operator == "neq":
        if isinstance(left, str) or isinstance(right, str):
            return _normalize_string(left) != _normalize_string(right)
        return left != right
    if operator == "contains":
        if isinstance(left, list):
            return right in left
        return _normalize_string(right) in _normalize_string(left)
    if operator == "in":
        values = right if isinstance(right, list) else [right]
        return any(_matches_operator(left, "eq", item) for item in values)
    if operator == "not_in":
        values = right if isinstance(right, list) else [right]
        return all(_matches_operator(left, "neq", item) for item in values)
    if operator == "gte":
        return left >= right
    if operator == "lte":
        return left <= right
    if operator == "gt":
        return left > right
    if operator == "lt":
        return left < right
    return False


def _matches_advanced_filter(
    row: dict[str, object],
    node: dict[str, object] | None,
) -> bool:
    if not node:
        return True
    node_type = node.get("type")
    if node_type == "rule":
        field = str(node.get("field"))
        operator = str(node.get("operator"))
        value = node.get("value")
        return _matches_operator(_resolve_field_value(row, field), operator, value)
    logic = str(node.get("logic", "and")).lower()
    conditions = node.get("conditions", [])
    results = [
        _matches_advanced_filter(row, condition)
        for condition in conditions
        if isinstance(condition, dict)
    ]
    if not results:
        return True
    if logic == "or":
        return any(results)
    return all(results)


def list_instrument_attribute_definitions() -> list[dict[str, object]]:
    return _deepcopy(INSTRUMENT_ATTRIBUTE_DEFINITIONS)


def create_instrument_attribute_definition(
    payload: dict[str, object],
) -> dict[str, object]:
    attribute_key = str(payload["attribute_key"]).strip().lower().replace(" ", "_")
    record = {
        "attribute_key": attribute_key,
        "label": payload["label"],
        "description": payload.get("description"),
        "data_type": payload["data_type"],
        "options": payload.get("options", []),
        "is_groupable": payload.get("is_groupable", True),
        "is_filterable": payload.get("is_filterable", True),
        "is_view_column": payload.get("is_view_column", True),
        "default_visible": payload.get("default_visible", False),
    }
    INSTRUMENT_ATTRIBUTE_DEFINITIONS.append(record)
    return _deepcopy(record)


def get_fund_attributes(fund_id: str) -> dict[str, object]:
    row = next((item for item in WATCHLIST_ROWS if item["fund_id"] == fund_id), None)
    values = row.get("attributes", {}) if row else {}
    return {
        "fund_id": fund_id,
        "definitions": list_instrument_attribute_definitions(),
        "values": _deepcopy(values),
    }


def upsert_fund_attributes(fund_id: str, values: dict[str, object]) -> dict[str, object]:
    updated = False
    for row in WATCHLIST_ROWS:
        if row["fund_id"] == fund_id:
            row.setdefault("attributes", {}).update(values)
            updated = True
    summary = FUND_SUMMARIES.get(fund_id)
    if summary is not None:
        summary.setdefault("instrument_attributes", {}).update(values)
    return {
        "fund_id": fund_id,
        "updated": updated,
        "values": get_fund_attributes(fund_id)["values"],
    }


def list_watchlists() -> list[dict[str, object]]:
    return _deepcopy(WATCHLISTS)


def _raise_legacy_watchlist_state_error(operation: str) -> None:
    raise RuntimeError(
        "Legacy domain.catalog watchlist state is disabled. "
        f"Use the database-backed watchlist services instead: {operation}."
    )


def create_watchlist(
    name: str,
    description: str | None,
    watchlist_id: str | None = None,
) -> dict[str, object]:
    _raise_legacy_watchlist_state_error("create_watchlist")


def get_watchlist(watchlist_id: str) -> dict[str, object]:
    _raise_legacy_watchlist_state_error("get_watchlist")


def list_watchlist_views(watchlist_id: str) -> list[dict[str, object]]:
    _raise_legacy_watchlist_state_error("list_watchlist_views")


def create_watchlist_view(
    watchlist_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    _raise_legacy_watchlist_state_error("create_watchlist_view")


def add_watchlist_items(watchlist_id: str, asset_ids: list[str]) -> dict[str, object]:
    _raise_legacy_watchlist_state_error("add_watchlist_items")


def list_field_registry(
    category: str | None = None,
    product_type: str | None = None,
    search: str | None = None,
) -> dict[str, object]:
    fields = _current_field_registry()
    if category:
        fields = [item for item in fields if item["category_code"] == category]
    if product_type:
        fields = [
            item
            for item in fields
            if not item["product_scope_json"] or product_type in item["product_scope_json"]
        ]
    if search:
        needle = search.lower()
        fields = [
            item
            for item in fields
            if needle in item["label"].lower() or needle in item["field_key"].lower()
        ]
    return {
        "categories": _deepcopy(FIELD_CATEGORIES),
        "fields": fields,
        "total_fields": len(fields),
    }


def query_watchlist_rows(payload: dict[str, object]) -> dict[str, object]:
    _raise_legacy_watchlist_state_error("query_watchlist_rows")


def _fund_or_default(store: dict[str, object], fund_id: str, default: object) -> object:
    return _deepcopy(store.get(fund_id, default))


def get_fund_summary(fund_id: str) -> dict[str, object]:
    default = {
        "fund_id": fund_id,
        "fund_name": "Sample Fund",
        "ticker_or_isin": fund_id.upper(),
        "rating_as_of": "2026-04-10",
        "category_name": "Unclassified",
        "overall_rating": None,
        "analyst_stance": "Unrated",
        "instrument_attributes": {},
        "key_stats": [],
        "freshness": {
            "data_freshness_status": "unavailable",
            "last_fact_update_at": None,
            "last_recalculated_at": None,
            "last_successful_snapshot_at": None,
            "staleness_reason": "No read model materialized yet.",
        },
        "quick_monitoring_items": [],
        "tabs": ["summary"],
    }
    return _fund_or_default(FUND_SUMMARIES, fund_id, default)


def get_fund_chart(fund_id: str) -> dict[str, object]:
    default = {
        "fund_id": fund_id,
        "base_series_type": "nav",
        "currency": "USD",
        "date_range": None,
        "series": [],
        "available_compare_targets": [],
    }
    return _fund_or_default(FUND_CHARTS, fund_id, default)


def get_fund_performance(fund_id: str) -> dict[str, object]:
    return _fund_or_default(
        FUND_PERFORMANCE,
        fund_id,
        {
            "growth_chart_series": [],
            "annual_returns": [],
            "trailing_returns": [],
            "ranking": None,
            "snapshot_metadata": None,
        },
    )


def get_fund_risk(fund_id: str) -> dict[str, object]:
    return _fund_or_default(
        FUND_RISK,
        fund_id,
        {
            "risk_overview": None,
            "scatter_points": [],
            "risk_metrics": [],
            "drawdown_summary": None,
            "risk_structure": {"rows": []},
            "current_watch": {"overall_level": None, "rows": [], "note": None},
            "change_monitor": {"rows": [], "note": None},
            "snapshot_metadata": None,
        },
    )


def get_fund_exposure_summary(fund_id: str) -> dict[str, object]:
    return _fund_or_default(
        FUND_EXPOSURE_SUMMARIES,
        fund_id,
        {
            "allocation_blocks": {},
            "style_box": None,
            "liquidity_leverage": None,
            "valuation_statistics": None,
            "holdings_summary": None,
            "snapshot_metadata": None,
        },
    )


def get_fund_exposure_holdings(fund_id: str) -> dict[str, object]:
    holdings = _deepcopy(FUND_EXPOSURE_HOLDINGS.get(fund_id, []))
    return {
        "rows": holdings,
        "page": 1,
        "page_size": len(holdings),
        "total_rows": len(holdings),
    }


def get_fund_ratings(fund_id: str) -> dict[str, object]:
    return _fund_or_default(
        FUND_RATINGS,
        fund_id,
        {
            "overall_rating": None,
            "overall_score": None,
            "analyst_stance": "Unrated",
            "methodology_version": "house-rating/v1",
            "dimension_scores": [],
            "override_info": None,
        },
    )


def list_recalc_jobs() -> list[dict[str, object]]:
    jobs = sorted(
        RECALC_JOBS,
        key=lambda item: item["enqueued_at"],
        reverse=True,
    )
    return _deepcopy(jobs)


def get_recalc_job(job_id: str) -> dict[str, object]:
    job = next(
        (item for item in RECALC_JOBS if item["recalc_job_id"] == job_id),
        None,
    )
    return _deepcopy(job or {})


def enqueue_recalc_job(fund_id: str, job_type: str) -> dict[str, object]:
    job_id = f"job_{next(_job_counter):04d}"
    now_iso = _now_iso()
    record = {
        "recalc_job_id": job_id,
        "job_type": job_type,
        "fund_id": fund_id,
        "trigger_type": "manual_api",
        "trigger_ref_type": "api_request",
        "trigger_ref_id": None,
        "job_status": "queued",
        "priority": 100 if job_type == "all" else 85,
        "dedupe_key": f"{job_type}:{fund_id}:{now_iso}",
        "payload_json": {"requested_at": now_iso},
        "enqueued_at": now_iso,
        "started_at": None,
        "finished_at": None,
        "error_message": None,
    }
    RECALC_JOBS.append(record)
    return _deepcopy(record)
