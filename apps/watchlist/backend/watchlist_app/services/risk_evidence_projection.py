"""Decision inputs and explicit original-evidence references for a frozen risk run.

Calculations retain their results, conventions, coverage and limitations. Reading
all input observations is not a prerequisite for acknowledging a calculated
result. Originals remain in the same immutable snapshot and can be inspected
when a claim needs them; a reference never represents original evidence read.
"""
import hashlib
import json
from watchlist_app.services.research_read_projection import shape

COMPUTATION_INPUTS = frozenset({
    "input_snapshot", "benchmark_snapshot", "input_series", "input_points", "input_sources",
    "sources", "source_ids", "points", "history",
})


def risk_snapshot_projection(snapshot, side="current"):
    if snapshot is None or snapshot.get("evidence_projection") == "risk_decision_v1":
        return snapshot

    def reference(value, path):
        # Selectors differ across current/previous snapshots. Content identity
        # lets readers distinguish a changed original from a different address,
        # without reloading the original merely to compare decision pages.
        digest = hashlib.sha256()
        for part in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":")).iterencode(value):
            digest.update(part.encode())
        return {"inline": False, **shape(value), "content_sha256": digest.hexdigest(),
            "read": {"tool": "read_risk_evidence", "snapshot": side, "path": path},
            "note": "本轮冻结的原始计算输入/来源；按论证需要读取，不要求逐点复算已提供的指标。未读取不能声称已核实原文。"}

    def visit(value, path, computation=False):
        if isinstance(value, list):
            return [visit(item, [*path, index], computation) for index, item in enumerate(value)]
        if not isinstance(value, dict):
            return value
        # Existing projections are idempotent and keep their original selector.
        if value.get("inline") is False and (value.get("read") or {}).get("tool") == "read_risk_evidence":
            return value
        bound_source = (len(path) >= 2 and isinstance(path[-1], int)
            and path[-2] in {"sources", "source_views", "input_sources"} and "data" not in path)
        if bound_source and value.get("source_type") == "computed_metric" and isinstance(value.get("retained_reference"), dict):
            return {**value, "read": {"tool": "read_risk_evidence", "snapshot": side, "path": path},
                "evidence_note": "计算原件按本轮绑定的来源版本读取；引用本身不代表已读取结果或原始输入。"}
        computation = (computation or value.get("source_type") == "computed_metric"
            or (isinstance(value.get("methodology"), dict) and "input_sources" in value["methodology"]))
        result = {}
        for key, item in value.items():
            child = [*path, key]
            if (key == "history_json" or (computation and key in COMPUTATION_INPUTS)
                    or (key in {"text", "content_text"} and value.get("source_id"))):
                result[key] = reference(item, child) if item else item
            else:
                result[key] = visit(item, child, computation)
        return result
    return {**visit(snapshot, []), "evidence_projection": "risk_decision_v1"}
