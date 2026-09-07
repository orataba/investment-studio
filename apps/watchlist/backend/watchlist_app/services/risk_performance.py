"""Retained return evidence for risk review, independently of configured alert lines."""
from datetime import date
import math
from sqlalchemy import select

from watchlist_app.db.models import InstrumentChartReadModel, InstrumentDetail, InstrumentManualProfile, InstrumentPerformanceReadModel
from watchlist_app.services.canonical_recalc import CanonicalRecalcService, _node_path_labels, _node_path_node_ids, _peer_geographic_exposure, _peer_taxonomy_is_comparable
from watchlist_app.reference_data.instrument_taxonomy import INSTRUMENT_TAXONOMY_CODE
from watchlist_app.services.instrument_resolution import LOCAL_DETAIL_INSTRUMENT_TYPES
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_workbench import compare_series
from watchlist_app.services.shared_instrument_registry import list_shared_active_instrument_ids


def _points(series):
    metadata = series.get("metadata") or {}
    if metadata.get("return_series_status") not in {"ready", "complete", "partial"} or metadata.get("return_segment_breaks"):
        return {}
    return {point["date"]: float(point["value"]) for point in series.get("points", [])
            if isinstance(point.get("value"), (int, float)) and not isinstance(point["value"], bool)
            and math.isfinite(point["value"]) and point["value"] > 0}


def _month_periods(points):
    closes = {}
    for day in sorted(points):
        closes[day[:7]] = day
    months = sorted(closes)
    rows = []
    for previous, current in zip(months, months[1:]):
        before, after = date.fromisoformat(previous + "-01"), date.fromisoformat(current + "-01")
        if after.year * 12 + after.month - before.year * 12 - before.month != 1:
            continue
        start, end = closes[previous], closes[current]
        rows.append({"month": current, "start_date": start, "end_date": end,
                     "return_pct": (points[end] / points[start] - 1) * 100,
                     "latest_month_to_date": current == months[-1]})
    return rows


def _negative_month_count(rows):
    count = 0
    previous = None
    for row in reversed([row for row in rows if not row["latest_month_to_date"]]):
        current = date.fromisoformat(row["month"] + "-01")
        if previous is not None and previous.year * 12 + previous.month - current.year * 12 - current.month != 1:
            break
        if row["return_pct"] >= 0:
            break
        count += 1
        previous = current
    return count


def peer_context(session):
    """Load the saved peer classification once, without market data or UI rankings."""
    service = CanonicalRecalcService()
    nodes = service.taxonomy_repository.list_nodes(session, taxonomy_code=INSTRUMENT_TAXONOMY_CODE)
    node_by_id = {node.node_id: node for node in nodes}
    active = set(list_shared_active_instrument_ids(instrument_types=LOCAL_DETAIL_INSTRUMENT_TYPES))
    active.intersection_update(session.scalars(select(InstrumentDetail.instrument_id).where(
        InstrumentDetail.is_active.is_(True), InstrumentDetail.instrument_type.in_(LOCAL_DETAIL_INSTRUMENT_TYPES))))
    assignments = service.taxonomy_repository.list_assignments(session, taxonomy_code=INSTRUMENT_TAXONOMY_CODE)
    assigned = {row.instrument_id: node_by_id[row.node_id] for row in assignments
        if row.instrument_id in active and row.node_id in node_by_id}
    attributes = {}
    for row in service.attribute_repository.get_values_for_assets(session, sorted(active)):
        attributes.setdefault(row.instrument_id, {}).setdefault(row.attribute_key, row.value_json)
    return {"node_by_id": node_by_id, "assigned_node_by_asset": assigned, "attributes_by_asset": attributes}


def _taxonomy_peers(instrument_id, context):
    node = context["assigned_node_by_asset"].get(instrument_id)
    path = _node_path_node_ids(node)
    geography = _peer_geographic_exposure(node, context["attributes_by_asset"].get(instrument_id))
    status = ("missing_taxonomy" if not node or not path else "taxonomy_not_comparable" if not _peer_taxonomy_is_comparable(node)
              else "missing_peer_dimension" if geography is None else "ready")
    node_id = path[-1] if status == "ready" else None
    peers = sorted(iid for iid, node in context["assigned_node_by_asset"].items()
        if iid != instrument_id and node_id and node.node_id == node_id
        and _peer_geographic_exposure(node, context["attributes_by_asset"].get(iid)) == geography)
    metadata = {"configuration_source": "instrument_taxonomy", "scope_status": status,
        "taxonomy_code": INSTRUMENT_TAXONOMY_CODE, "assigned_node_id": node.node_id if path else None,
        "peer_node_id": node_id, "peer_path": _node_path_labels(context["node_by_id"].get(node_id) or node) if node_id else [],
        "peer_dimensions": {"primary_geographic_exposure": geography} if node_id else {},
        "instrument_ids": peers}
    reason = {
        "missing_taxonomy": "未保存同类分类，不能确定基金页的同类范围。",
        "taxonomy_not_comparable": "当前分类不是可比较的同类叶节点。",
        "missing_peer_dimension": "当前分类缺少地域维度，不能确定基金页的同类范围。",
    }.get(status)
    if not reason and not peers:
        reason = "当前分类及地域没有其他有效登记同类。"
    return peers, metadata, reason


def performance_evidence(session, instrument_id, *, peer_scope=None):
    chart = session.get(InstrumentChartReadModel, instrument_id)
    series = (chart.payload_json.get("research_returns") or {}) if chart else {}
    points = _points(series)
    dates = sorted(points)
    profile = session.get(InstrumentManualProfile, instrument_id)
    settings = (profile.nav_settings_json or {}) if profile else {}
    baseline = settings.get("default_benchmark_instrument_id")
    explicit_peers = settings.get("peer_baseline_instrument_ids") or []
    taxonomy_peers, peer_group, peer_limitation = _taxonomy_peers(instrument_id, peer_scope if peer_scope is not None else peer_context(session))
    peers = [*taxonomy_peers, *explicit_peers]
    limitations = []
    if not baseline and not peers:
        limitations.append("未配置基准或同类对照，不能判断跑输同类/基准；组合成员和名称相似不自动构成同类。")
    if len(dates) < 2:
        limitations.append("可用连续收益序列不足，未计算阶段表现。")
    frequency = (series.get("frequency") or {}).get("resolved_frequency")
    perf = session.get(InstrumentPerformanceReadModel, instrument_id)
    months = _month_periods(points)
    output = {"source_id": f"risk-performance:{instrument_id}", "available": len(dates) >= 2,
        "currency": series.get("currency"), "frequency": frequency, "return_kind": (series.get("metadata") or {}).get("return_kind"),
        "quote_basis": (series.get("metadata") or {}).get("quote_basis"),
        "source_cutoff_at": chart.source_cutoff_at if chart else None, "freshness": chart.data_freshness_status if chart else "missing",
        "sample_start": dates[0] if dates else None, "sample_end": dates[-1] if dates else None, "observations": len(dates),
        "sample_return_pct": (points[dates[-1]] / points[dates[0]] - 1) * 100 if len(dates) >= 2 else None,
        "trailing_returns": (perf.payload_json or {}).get("trailing_returns", []) if perf else [],
        "trailing_returns_metadata": {"freshness": perf.data_freshness_status, "source_cutoff_at": perf.source_cutoff_at,
            "snapshot": (perf.payload_json or {}).get("snapshot_metadata")} if perf else None,
        "monthly_periods": months[-12:], "trailing_negative_completed_observed_months": _negative_month_count(months),
        "peer_group": peer_group, "comparisons": [], "limitations": limitations,
        "method": "选定收益序列的实际观察日；非重叠月度区间以上月末观察值为起点。最新月单列为截至最新观察日，不能当作完整月。短样本不年化；不凭亏损数值自动设风险等级。"}
    for other_id in dict.fromkeys([*([baseline] if baseline else []), *peers]):
        role = "configured_benchmark" if other_id == baseline else "taxonomy_peer" if other_id in taxonomy_peers else "configured_peer"
        instrument = session.get(InstrumentDetail, other_id)
        other_chart = session.get(InstrumentChartReadModel, other_id)
        other_series = (other_chart.payload_json.get("research_returns") or {}) if other_chart else {}
        other_points = _points(other_series)
        label = instrument.instrument_name if instrument else other_id
        other_frequency = (other_series.get("frequency") or {}).get("resolved_frequency")
        reason = None
        if not frequency or frequency != other_frequency:
            reason = "收益序列频率不同或未确认"
        elif not series.get("currency") or series.get("currency") != other_series.get("currency"):
            reason = "币种不同或缺失，未换算"
        elif not (series.get("metadata") or {}).get("return_kind") or (series.get("metadata") or {}).get("return_kind") != (other_series.get("metadata") or {}).get("return_kind"):
            reason = "收益口径不同或未确认"
        common = sorted(set(points) & set(other_points))
        if len(common) < 2:
            reason = reason or "共同实际观察日不足"
        if reason:
            limitations.append(f"对照 {label}：{reason}，未计算相对表现。")
            continue
        comparison = compare_series({instrument_id: series, other_id: other_series}, date.fromisoformat(common[0]), date.fromisoformat(common[-1]), instrument_id, other_id)
        target_months = _month_periods({day: points[day] for day in common})
        other_months = _month_periods({day: other_points[day] for day in common})
        monthly = [{**target, "comparison_return_pct": other["return_pct"], "excess_return_pp": target["return_pct"] - other["return_pct"]}
                   for target, other in zip(target_months, other_months)]
        output["comparisons"].append({"source_id": f"risk-comparison:{instrument_id}:{other_id}", "instrument_id": other_id,
            "name": label, "role": role, "configuration_source": "instrument_taxonomy" if role == "taxonomy_peer" else "instrument.nav_settings", "frequency": frequency,
            "comparison": comparison, "monthly_periods": monthly[-12:],
            "note": "仅为当前已登记同类或显式对照的同区间描述，不代表全市场同类排名；各对照的共同样本可能不同，不能合成排名。持续落后须查看多个非重叠区间，不能把重叠滚动窗口视作独立证据。"})
    if peer_limitation:
        limitations.append(peer_limitation)
    return serialize_payload(output)
