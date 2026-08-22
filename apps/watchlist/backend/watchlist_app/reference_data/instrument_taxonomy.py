from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any


INSTRUMENT_TAXONOMY_CODE = "instrument_taxonomy"
INSTRUMENT_TAXONOMY_LABEL = "Instrument Taxonomy"
INSTRUMENT_TAXONOMY_MAX_LEVELS = 7
INSTRUMENT_TAXONOMY_DERIVED_KEYS = {
    "instrument_taxonomy_leaf",
    "instrument_taxonomy_path",
    *{
        f"instrument_taxonomy_level_{level}"
        for level in range(1, INSTRUMENT_TAXONOMY_MAX_LEVELS + 1)
    },
}


_PUBLIC_FUND_TAXONOMY = [
    {
        "node_id": "fund-public-equity",
        "label": "股票型",
        "children": [
            {"node_id": "fund-public-equity-standard", "label": "标准股票型"},
            {"node_id": "fund-public-equity-indexed", "label": "指数股票型"},
        ],
    },
    {
        "node_id": "fund-public-hybrid",
        "label": "混合型",
        "children": [
            {"node_id": "fund-public-hybrid-equity-biased", "label": "偏股型"},
            {"node_id": "fund-public-hybrid-flexible", "label": "灵活配置型"},
            {"node_id": "fund-public-hybrid-balanced", "label": "股债平衡型"},
            {"node_id": "fund-public-hybrid-bond-biased", "label": "偏债型"},
            {"node_id": "fund-public-hybrid-strategy", "label": "策略型"},
        ],
    },
    {
        "node_id": "fund-public-bond",
        "label": "债券型",
        "children": [
            {"node_id": "fund-public-bond-pure", "label": "纯债型"},
            {"node_id": "fund-public-bond-ordinary", "label": "普通债券型"},
            {"node_id": "fund-public-bond-convertible", "label": "可转债型"},
            {"node_id": "fund-public-bond-indexed", "label": "指数债券型"},
            {"node_id": "fund-public-bond-cd", "label": "同业存单型"},
        ],
    },
    {
        "node_id": "fund-public-qdii",
        "label": "QDII",
        "children": [
            {"node_id": "fund-public-qdii-reit", "label": "QDII房地产信托"},
            {"node_id": "fund-public-qdii-equity", "label": "QDII股票型"},
            {"node_id": "fund-public-qdii-hybrid", "label": "QDII混合型"},
            {"node_id": "fund-public-qdii-commodity", "label": "QDII商品型"},
            {"node_id": "fund-public-qdii-bond", "label": "QDII债券型"},
        ],
    },
    {
        "node_id": "fund-public-commodity",
        "label": "商品型",
        "children": [
            {"node_id": "fund-public-commodity-precious-metals", "label": "贵金属基金"},
            {"node_id": "fund-public-commodity-other", "label": "其他商品基金"},
        ],
    },
    {"node_id": "fund-public-reits", "label": "REITS"},
    {
        "node_id": "fund-public-fof",
        "label": "FOF",
        "children": [
            {"node_id": "fund-public-fof-equity", "label": "股票型FOF"},
            {"node_id": "fund-public-fof-bond", "label": "债券型FOF"},
            {"node_id": "fund-public-fof-hybrid", "label": "混合型FOF"},
            {"node_id": "fund-public-fof-pension", "label": "养老目标FOF"},
        ],
    },
    {"node_id": "fund-public-other", "label": "其他"},
]


_PRIVATE_FUND_TAXONOMY = [
    {
        "node_id": "fund-private-equity",
        "label": "股票策略",
        "children": [
            {
                "node_id": "fund-private-equity-discretionary-long",
                "label": "主观多头",
                "children": [
                    {
                        "node_id": "fund-private-equity-discretionary-stock-picking",
                        "label": "主观选股",
                    },
                    {
                        "node_id": "fund-private-equity-discretionary-private-placement",
                        "label": "定增打新",
                    },
                ],
            },
            {
                "node_id": "fund-private-equity-quant-long",
                "label": "量化多头",
                "children": [
                    {"node_id": "fund-private-equity-quant-long-300", "label": "300指增"},
                    {"node_id": "fund-private-equity-quant-long-500", "label": "500指增"},
                    {"node_id": "fund-private-equity-quant-long-1000", "label": "1000指增"},
                    {"node_id": "fund-private-equity-quant-long-2000", "label": "2000指增"},
                    {"node_id": "fund-private-equity-quant-long-dividend", "label": "红利指增"},
                    {
                        "node_id": "fund-private-equity-quant-long-stock-selection",
                        "label": "量化选股",
                    },
                    {
                        "node_id": "fund-private-equity-quant-long-other-enhanced",
                        "label": "其他指增",
                    },
                ],
            },
            {"node_id": "fund-private-equity-long-short", "label": "股票多空"},
            {"node_id": "fund-private-equity-market-neutral", "label": "股票市场中性"},
        ],
    },
    {
        "node_id": "fund-private-bond",
        "label": "债券策略",
        "children": [
            {"node_id": "fund-private-bond-pure", "label": "纯债策略"},
            {"node_id": "fund-private-bond-enhanced", "label": "债券增强"},
            {"node_id": "fund-private-bond-composite", "label": "债券复合"},
            {"node_id": "fund-private-bond-convertible-trading", "label": "转债交易"},
        ],
    },
    {
        "node_id": "fund-private-futures-derivatives",
        "label": "期货及衍生品策略",
        "children": [
            {
                "node_id": "fund-private-futures-derivatives-cta-discretionary",
                "label": "主观CTA",
                "children": [
                    {
                        "node_id": "fund-private-futures-derivatives-cta-discretionary-trend",
                        "label": "主观趋势",
                    },
                    {
                        "node_id": "fund-private-futures-derivatives-cta-discretionary-arbitrage",
                        "label": "主观套利",
                    },
                    {
                        "node_id": "fund-private-futures-derivatives-cta-discretionary-multi",
                        "label": "主观多策略",
                    },
                ],
            },
            {
                "node_id": "fund-private-futures-derivatives-cta-quant",
                "label": "量化CTA",
                "children": [
                    {
                        "node_id": "fund-private-futures-derivatives-cta-quant-trend",
                        "label": "量化趋势",
                    },
                    {
                        "node_id": "fund-private-futures-derivatives-cta-quant-arbitrage",
                        "label": "量化套利",
                    },
                    {
                        "node_id": "fund-private-futures-derivatives-cta-quant-multi",
                        "label": "量化多策略",
                    },
                ],
            },
            {"node_id": "fund-private-futures-derivatives-options", "label": "期权策略"},
            {"node_id": "fund-private-futures-derivatives-other", "label": "其他衍生品策略"},
        ],
    },
    {
        "node_id": "fund-private-multi-asset",
        "label": "多资产策略",
        "children": [
            {"node_id": "fund-private-multi-asset-macro", "label": "宏观策略"},
            {"node_id": "fund-private-multi-asset-arbitrage", "label": "套利策略"},
            {"node_id": "fund-private-multi-asset-composite", "label": "复合策略"},
        ],
    },
    {
        "node_id": "fund-private-fund-of-funds",
        "label": "组合基金",
        "children": [
            {"node_id": "fund-private-fund-of-funds-fof", "label": "FOF"},
            {"node_id": "fund-private-fund-of-funds-mom", "label": "MOM"},
        ],
    },
    {"node_id": "fund-private-other", "label": "其他"},
]


def _typed_roots(
    nodes: list[dict[str, Any]],
    instrument_type: str,
) -> list[dict[str, Any]]:
    return [
        {**deepcopy(node), "instrument_type": instrument_type}
        for node in nodes
    ]


INSTRUMENT_TAXONOMY_TREE = [
    *_typed_roots(_PUBLIC_FUND_TAXONOMY, "public_fund"),
    *_typed_roots(_PRIVATE_FUND_TAXONOMY, "private_fund"),
    {
        "node_id": "etf-equity",
        "label": "权益",
        "instrument_type": "etf",
        "children": [
            {"node_id": "etf-equity-index", "label": "宽基"},
            {"node_id": "etf-equity-sector-theme", "label": "行业主题"},
            {"node_id": "etf-equity-strategy", "label": "策略 / Smart Beta"},
            {"node_id": "etf-equity-cross-border", "label": "跨境"},
        ],
    },
    {
        "node_id": "etf-fixed-income",
        "label": "固定收益",
        "instrument_type": "etf",
        "children": [
            {"node_id": "etf-fixed-income-government", "label": "利率债"},
            {"node_id": "etf-fixed-income-credit", "label": "信用债"},
            {"node_id": "etf-fixed-income-convertible", "label": "可转债"},
            {"node_id": "etf-fixed-income-cash", "label": "现金管理"},
        ],
    },
    {
        "node_id": "etf-commodity",
        "label": "商品",
        "instrument_type": "etf",
        "children": [
            {"node_id": "etf-commodity-precious-metals", "label": "贵金属"},
            {"node_id": "etf-commodity-other", "label": "其他商品"},
        ],
    },
    {"node_id": "etf-multi-asset", "label": "多资产", "instrument_type": "etf"},
    {"node_id": "etf-other", "label": "其他", "instrument_type": "etf"},
    {
        "node_id": "equity-market-us",
        "label": "美股",
        "instrument_type": "equity",
        "children": [
            {"node_id": "equity-exchange-xnas", "label": "NASDAQ"},
            {"node_id": "equity-exchange-xnys", "label": "NYSE"},
            {"node_id": "equity-exchange-xase", "label": "NYSE American"},
        ],
    },
    {
        "node_id": "equity-market-hk",
        "label": "港股",
        "instrument_type": "equity",
        "children": [
            {"node_id": "equity-exchange-xhkg", "label": "HKEX"},
        ],
    },
    {
        "node_id": "equity-market-cn-a",
        "label": "A股",
        "instrument_type": "equity",
        "children": [
            {"node_id": "equity-exchange-xshg", "label": "上交所"},
            {"node_id": "equity-exchange-xshe", "label": "深交所"},
        ],
    },
    {
        "node_id": "equity-market-eu",
        "label": "欧洲股市",
        "instrument_type": "equity",
        "children": [
            {"node_id": "equity-exchange-xlon", "label": "London Stock Exchange"},
            {"node_id": "equity-exchange-xetr", "label": "Xetra"},
            {"node_id": "equity-exchange-xpar", "label": "Euronext Paris"},
            {"node_id": "equity-exchange-xams", "label": "Euronext Amsterdam"},
            {"node_id": "equity-exchange-xmil", "label": "Borsa Italiana"},
            {"node_id": "equity-exchange-xswx", "label": "SIX Swiss Exchange"},
        ],
    },
    {"node_id": "index-broad-market", "label": "宽基", "instrument_type": "index"},
    {"node_id": "index-sector-theme", "label": "行业主题", "instrument_type": "index"},
    {"node_id": "index-strategy", "label": "策略", "instrument_type": "index"},
    {"node_id": "index-fixed-income", "label": "固定收益", "instrument_type": "index"},
    {"node_id": "index-commodity", "label": "商品", "instrument_type": "index"},
    {"node_id": "index-multi-asset", "label": "多资产", "instrument_type": "index"},
    {"node_id": "index-other", "label": "其他", "instrument_type": "index"},
]


def _flatten_nodes(
    nodes: Iterable[dict[str, Any]],
    *,
    parent_node_id: str | None,
    level_index: int,
    path_labels: list[str],
    path_node_ids: list[str],
    inherited_instrument_type: str | None = None,
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for display_order, node in enumerate(nodes, start=1):
        label = str(node["label"])
        node_id = str(node["node_id"])
        children = [dict(item) for item in node.get("children", []) if isinstance(item, dict)]
        next_path_labels = [*path_labels, label]
        next_path_node_ids = [*path_node_ids, node_id]
        instrument_type = str(node.get("instrument_type") or inherited_instrument_type or "")
        if not instrument_type:
            raise ValueError(f'Taxonomy root node "{node_id}" requires an instrument_type')
        flattened.append(
            {
                "node_id": node_id,
                "taxonomy_code": INSTRUMENT_TAXONOMY_CODE,
                "taxonomy_label": INSTRUMENT_TAXONOMY_LABEL,
                "instrument_type": instrument_type,
                "label": label,
                "parent_node_id": parent_node_id,
                "level_index": level_index,
                "display_order": int(node.get("display_order") or display_order),
                "is_leaf": not children,
                "path_labels_json": next_path_labels,
                "path_node_ids_json": next_path_node_ids,
            }
        )
        flattened.extend(
            _flatten_nodes(
                children,
                parent_node_id=node_id,
                level_index=level_index + 1,
                path_labels=next_path_labels,
                path_node_ids=next_path_node_ids,
                inherited_instrument_type=instrument_type,
            )
        )
    return flattened


def instrument_taxonomy_nodes() -> list[dict[str, Any]]:
    return _flatten_nodes(
        deepcopy(INSTRUMENT_TAXONOMY_TREE),
        parent_node_id=None,
        level_index=1,
        path_labels=[],
        path_node_ids=[],
        inherited_instrument_type=None,
    )
