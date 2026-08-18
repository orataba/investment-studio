from __future__ import annotations

from collections.abc import Sequence

from watchlist_app.reference_data.instrument_taxonomy import (
    INSTRUMENT_TAXONOMY_CODE,
    INSTRUMENT_TAXONOMY_DERIVED_KEYS,
    INSTRUMENT_TAXONOMY_MAX_LEVELS,
)


SUPPORTED_TAXONOMY_INSTRUMENT_TYPES = (
    "public_fund",
    "private_fund",
    "etf",
    "equity",
    "index",
)


def taxonomy_node_supports_instrument(*, instrument_type: str, node: object) -> bool:
    normalized_instrument_type = str(instrument_type or "").strip().lower()
    node_type = str(_node_value(node, "instrument_type") or "").strip().lower()
    return node_type == normalized_instrument_type


def _node_value(node: object, key: str) -> object:
    if isinstance(node, dict):
        return node.get(key)
    return getattr(node, key, None)


def build_taxonomy_context(
    node: object | None,
) -> dict[str, object]:
    if node is None:
        return {
            "taxonomy_code": INSTRUMENT_TAXONOMY_CODE,
            "assigned_node_id": None,
            "assigned_label": None,
            "path_labels": [],
            "path_node_ids": [],
            "depth": 0,
            "derived_values": {},
        }

    path_labels = [
        str(item)
        for item in (_node_value(node, "path_labels_json") or [])
        if str(item).strip()
    ]
    path_node_ids = [
        str(item)
        for item in (_node_value(node, "path_node_ids_json") or [])
        if str(item).strip()
    ]
    assigned_label = str(_node_value(node, "label") or "").strip() or None
    assigned_node_id = str(_node_value(node, "node_id") or "").strip() or None

    derived_values: dict[str, str] = {}
    if path_labels:
        derived_values["instrument_taxonomy_leaf"] = path_labels[-1]
        derived_values["instrument_taxonomy_path"] = " / ".join(path_labels)
    for level, label in enumerate(path_labels, start=1):
        if level > INSTRUMENT_TAXONOMY_MAX_LEVELS:
            break
        derived_values[f"instrument_taxonomy_level_{level}"] = label
    return {
        "taxonomy_code": INSTRUMENT_TAXONOMY_CODE,
        "assigned_node_id": assigned_node_id,
        "assigned_label": assigned_label,
        "path_labels": path_labels,
        "path_node_ids": path_node_ids,
        "depth": len(path_labels),
        "derived_values": derived_values,
    }


def merge_taxonomy_attributes(
    *,
    taxonomy_context: dict[str, object] | None,
    instrument_attributes: dict[str, object] | None,
) -> dict[str, object]:
    merged = {
        key: value
        for key, value in (instrument_attributes or {}).items()
        if key not in INSTRUMENT_TAXONOMY_DERIVED_KEYS
    }
    derived_values = (
        taxonomy_context.get("derived_values")
        if isinstance(taxonomy_context, dict)
        else None
    )
    if isinstance(derived_values, dict):
        for key, value in derived_values.items():
            if key in INSTRUMENT_TAXONOMY_DERIVED_KEYS and value is not None:
                merged[str(key)] = value
    return merged


def merge_taxonomy_into_summary(
    payload: dict[str, object],
    taxonomy_context: dict[str, object],
) -> dict[str, object]:
    merged = dict(payload)
    merged["taxonomy"] = taxonomy_context
    return merged


def taxonomy_tree_payload(
    nodes: Sequence[object],
) -> dict[str, object]:
    serialized_nodes = []
    max_depth = 0
    for node in nodes:
        path_labels = [
            str(item)
            for item in getattr(node, "path_labels_json", None) or []
            if str(item).strip()
        ]
        max_depth = max(max_depth, len(path_labels))
        serialized_nodes.append(
            {
                "node_id": str(node.node_id),
                "label": str(node.label),
                "instrument_type": str(node.instrument_type),
                "parent_node_id": str(node.parent_node_id) if node.parent_node_id else None,
                "level_index": int(node.level_index),
                "display_order": int(node.display_order),
                "is_leaf": bool(node.is_leaf),
                "path_labels": path_labels,
                "path_node_ids": [
                    str(item)
                    for item in (node.path_node_ids_json or [])
                    if str(item).strip()
                ],
            }
        )
    return {
        "taxonomy_code": INSTRUMENT_TAXONOMY_CODE,
        "instrument_types": list(SUPPORTED_TAXONOMY_INSTRUMENT_TYPES),
        "max_depth": max_depth,
        "nodes": serialized_nodes,
    }
