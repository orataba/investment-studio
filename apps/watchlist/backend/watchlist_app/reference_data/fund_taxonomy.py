from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any


FUND_TAXONOMY_CODE = "fund_taxonomy"
FUND_TAXONOMY_LABEL = "Fund Taxonomy"
FUND_TAXONOMY_ASSET_TYPE = "fund"
FUND_TAXONOMY_MAX_LEVELS = 6
FUND_TAXONOMY_DERIVED_KEYS = {
    "fund_regime",
    "fund_taxonomy_leaf",
    "fund_taxonomy_path",
    *{
        f"fund_taxonomy_level_{level}"
        for level in range(1, FUND_TAXONOMY_MAX_LEVELS + 1)
    },
}


FUND_TAXONOMY_TREE = [
    {
        "node_id": "fund-public",
        "label": "公募",
        "children": [
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
        ],
    },
    {
        "node_id": "fund-private",
        "label": "私募",
        "children": [
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
        ],
    },
]


def _flatten_nodes(
    nodes: Iterable[dict[str, Any]],
    *,
    parent_node_id: str | None,
    level_index: int,
    path_labels: list[str],
    path_node_ids: list[str],
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for display_order, node in enumerate(nodes, start=1):
        label = str(node["label"])
        node_id = str(node["node_id"])
        children = [dict(item) for item in node.get("children", []) if isinstance(item, dict)]
        next_path_labels = [*path_labels, label]
        next_path_node_ids = [*path_node_ids, node_id]
        flattened.append(
            {
                "node_id": node_id,
                "taxonomy_code": FUND_TAXONOMY_CODE,
                "taxonomy_label": FUND_TAXONOMY_LABEL,
                "instrument_type": FUND_TAXONOMY_ASSET_TYPE,
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
            )
        )
    return flattened


def fund_taxonomy_nodes() -> list[dict[str, Any]]:
    return _flatten_nodes(
        deepcopy(FUND_TAXONOMY_TREE),
        parent_node_id=None,
        level_index=1,
        path_labels=[],
        path_node_ids=[],
    )
