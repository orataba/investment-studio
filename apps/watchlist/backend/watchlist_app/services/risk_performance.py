"""Retained return evidence for risk review, independently of configured alert lines."""
from dataclasses import dataclass
from calendar import monthrange
from datetime import UTC, date, datetime
from sqlalchemy import select
from sqlalchemy.orm import load_only

from watchlist_app.db.models import InstrumentChartReadModel, InstrumentDetail, InstrumentManualProfile, InstrumentPerformanceReadModel
from watchlist_app.services.canonical_recalc import CanonicalRecalcService, _node_path_labels, _node_path_node_ids, _peer_geographic_exposure, _peer_taxonomy_is_comparable
from watchlist_app.reference_data.instrument_taxonomy import INSTRUMENT_TAXONOMY_CODE
from watchlist_app.services.instrument_resolution import LOCAL_DETAIL_INSTRUMENT_TYPES
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.read_model_freshness import calculation_input_read
from watchlist_app.services.research_workbench import _comparison_window_coverage, _prepare_comparison_calendar, compare_series
from watchlist_app.services.calculation_frequency import source_calendar_date
from watchlist_app.services.return_windows import confirmed_return_points, return_sample_limitation
from watchlist_app.services.shared_instrument_registry import list_shared_active_instrument_ids


def _points(series):
    return {point["date"]: point for point in confirmed_return_points(series)}


def _snapshot_cutoff_limitation(record, cutoff):
    if record is None:
        return None
    for key in ("source_cutoff_at", "last_recalculated_at"):
        stamp = getattr(record, key, None)
        if stamp is not None and (stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)) > cutoff:
            return "当前业绩快照生成或来源更新时间晚于本轮冻结时刻；未保留当时版本，不能截取当前修订值替代"
    series = (record.payload_json or {}).get("research_returns") or {}
    if any(str(point.get("date") or "") > cutoff.date().isoformat() for point in series.get("points") or []):
        return "当前业绩快照含冻结时刻之后的观察值；未保留当时版本，不能截短冒充历史快照"
    return None


def _month_periods(points, series, *, cutoff):
    if not points:
        return []
    calendar = (series.get("observation_schedule") or {}).get("market_calendar")
    today = source_calendar_date(cutoff, calendar)
    daily = (series.get("frequency") or {}).get("resolved_frequency") == "daily"
    ordered = sorted(points)
    positions = {day: index for index, day in enumerate(ordered)}
    observations = [points[day] for day in ordered]
    evidence_by_pair = {}
    for interval in series.get("intervals") or []:
        evidence_by_pair.setdefault((interval.get("start_date"), interval.get("date")), []).append(interval)
    closes = {}
    for day in ordered:
        closes[day[:7]] = day
    months = sorted(closes)
    last = date.fromisoformat(ordered[-1])
    prepared_calendar = (_prepare_comparison_calendar(series, date.fromisoformat(ordered[0]),
        min(last.replace(day=monthrange(last.year, last.month)[1]), today), cutoff) if daily else None)
    rows = []
    for previous, current in zip(months, months[1:]):
        before, after = date.fromisoformat(previous + "-01"), date.fromisoformat(current + "-01")
        if after.year * 12 + after.month - before.year * 12 - before.month != 1:
            continue
        start, end = closes[previous], closes[current]
        requested_start = date.fromisoformat(start) if daily else before.replace(day=monthrange(before.year, before.month)[1])
        requested_end = min(after.replace(day=monthrange(after.year, after.month)[1]), today)
        retained = observations[positions[start]:positions[end] + 1]
        coverage = _comparison_window_coverage(series, retained, requested_start, requested_end, cutoff,
                                               prepared_calendar=prepared_calendar)
        window_series = {**series, "intervals": [interval for before_point, after_point in zip(retained, retained[1:])
            for interval in evidence_by_pair.get((before_point["date"], after_point["date"]), [])]}
        reason = coverage["reason"] or return_sample_limitation(window_series, retained)
        current_month = after.year == today.year and after.month == today.month
        if not reason and not current_month and coverage["not_yet_due_dates"]:
            reason = "月末观察值尚未到披露期限，不能将较早观察值作为完整月末"
        rows.append({"month": current, "start_date": start, "end_date": end,
                     "requested_start": requested_start.isoformat(), "requested_end": requested_end.isoformat(),
                     "window_coverage": coverage,
                     "return_pct": (points[end]["value"] / points[start]["value"] - 1) * 100 if reason is None else None,
                     "status": "unavailable" if reason else "complete", "limitation": reason,
                     "latest_month_to_date": current_month})
    return rows


def _negative_month_count(rows, latest_month):
    count = 0
    previous = date.fromisoformat(latest_month + "-01") if latest_month else None
    for row in reversed([row for row in rows if not row["latest_month_to_date"]]):
        current = date.fromisoformat(row["month"] + "-01")
        if previous is not None and previous.year * 12 + previous.month - current.year * 12 - current.month != 1:
            break
        if row["return_pct"] is None or row["return_pct"] >= 0:
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


@dataclass(frozen=True)
class PerformanceEvidenceContext:
    """Rows retained only for one read and its actual comparison universe."""
    instrument_ids: frozenset[str]
    records: dict[type, dict[str, object]]
    peers: dict[str, tuple]
    cutoff: datetime

    def get(self, model, instrument_id):
        return self.records[model].get(instrument_id)


def performance_context(session, instrument_ids, *, peer_scope=None, cutoff=None):
    with calculation_input_read(session, instrument_ids):
        return _load_performance_context(session, instrument_ids, peer_scope=peer_scope, cutoff=cutoff or datetime.now(UTC))


def _load_performance_context(session, instrument_ids, *, peer_scope=None, cutoff):
    """Batch-load targets and their saved peers, including absent records.

    Holding these rows for the read avoids fetching a shared comparator once per
    target. This is request-scoped data, not a cache across configurations or reads.
    """
    ids = frozenset(instrument_ids)

    def load(model, selected_ids, *columns):
        if not selected_ids:
            return {}
        return {row.instrument_id: row for row in session.scalars(select(model)
            .where(model.instrument_id.in_(sorted(selected_ids)))
            .options(load_only(model.instrument_id, *columns)).execution_options(populate_existing=True))}

    profiles = load(InstrumentManualProfile, ids, InstrumentManualProfile.nav_settings_json)
    scope = peer_scope if peer_scope is not None else peer_context(session) if ids else None
    peers = {iid: _taxonomy_peers(iid, scope) for iid in ids}
    comparison_ids = set()
    for iid in ids:
        profile = profiles.get(iid)
        settings = (profile.nav_settings_json or {}) if profile else {}
        comparison_ids.update(peers[iid][0])
        comparison_ids.update(settings.get("peer_baseline_instrument_ids") or [])
        if baseline := settings.get("default_benchmark_instrument_id"):
            comparison_ids.add(baseline)
    with calculation_input_read(session, comparison_ids - ids):
        records = {
            InstrumentManualProfile: profiles,
            InstrumentChartReadModel: load(InstrumentChartReadModel, ids | comparison_ids,
                InstrumentChartReadModel.payload_json, InstrumentChartReadModel.source_cutoff_at,
                InstrumentChartReadModel.data_freshness_status, InstrumentChartReadModel.last_recalculated_at),
            InstrumentPerformanceReadModel: load(InstrumentPerformanceReadModel, ids,
                InstrumentPerformanceReadModel.payload_json, InstrumentPerformanceReadModel.source_cutoff_at,
                InstrumentPerformanceReadModel.data_freshness_status, InstrumentPerformanceReadModel.last_recalculated_at),
            InstrumentDetail: load(InstrumentDetail, comparison_ids, InstrumentDetail.instrument_name),
        }
        return PerformanceEvidenceContext(ids, records, peers, cutoff)


def performance_evidence(session, instrument_id, *, peer_scope=None, context=None):
    if context is None:
        context = performance_context(session, [instrument_id], peer_scope=peer_scope)
    if instrument_id not in context.instrument_ids:
        raise ValueError("Instrument is outside this performance evidence context.")
    get_record = context.get
    chart = get_record(InstrumentChartReadModel, instrument_id)
    snapshot_limitation = _snapshot_cutoff_limitation(chart, context.cutoff)
    series = (chart.payload_json.get("research_returns") or {}) if chart and not snapshot_limitation else {}
    points = _points(series)
    dates = sorted(points)
    profile = get_record(InstrumentManualProfile, instrument_id)
    settings = (profile.nav_settings_json or {}) if profile else {}
    baseline = settings.get("default_benchmark_instrument_id")
    explicit_peers = settings.get("peer_baseline_instrument_ids") or []
    taxonomy_peers, peer_group, peer_limitation = context.peers[instrument_id]
    peers = [*taxonomy_peers, *explicit_peers]
    limitations = []
    if snapshot_limitation:
        limitations.append(snapshot_limitation)
    if not baseline and not peers:
        limitations.append("未配置基准或同类对照，不能判断跑输同类/基准；组合成员和名称相似不自动构成同类。")
    if len(dates) < 2:
        limitations.append("可用连续收益序列不足，未计算阶段表现。")
    sample_limitation = return_sample_limitation(series, list(points.values()))
    if points and sample_limitation:
        limitations.append(f"完整样本不可计算：{sample_limitation}；月度区间独立核验，不代表全历史完整。")
    frequency = (series.get("frequency") or {}).get("resolved_frequency")
    perf = get_record(InstrumentPerformanceReadModel, instrument_id)
    perf_limitation = _snapshot_cutoff_limitation(perf, context.cutoff)
    if snapshot_limitation or perf_limitation:
        perf = None
        if perf_limitation and perf_limitation not in limitations:
            limitations.append(perf_limitation)
    months = _month_periods(points, series, cutoff=context.cutoff)
    displayed_months = months[-12:]
    completed_months = [row for row in displayed_months if not row["latest_month_to_date"]]
    output = {"source_id": f"risk-performance:{instrument_id}", "available": len(dates) >= 2,
        "currency": series.get("currency"), "frequency": frequency, "return_kind": (series.get("metadata") or {}).get("return_kind"),
        "quote_basis": (series.get("metadata") or {}).get("quote_basis"),
        "source_cutoff_at": chart.source_cutoff_at if chart else None, "freshness": chart.data_freshness_status if chart else "missing",
        "sample_start": dates[0] if dates else None, "sample_end": dates[-1] if dates else None, "observations": len(dates),
        "sample_return_pct": (points[dates[-1]]["value"] / points[dates[0]]["value"] - 1) * 100 if not sample_limitation else None,
        "trailing_returns": (perf.payload_json or {}).get("trailing_returns", []) if perf else [],
        "trailing_returns_metadata": {"freshness": perf.data_freshness_status, "source_cutoff_at": perf.source_cutoff_at,
            "snapshot": (perf.payload_json or {}).get("snapshot_metadata")} if perf else None,
        "monthly_periods": displayed_months,
        "latest_completed_observed_month": completed_months[-1] if completed_months else None,
        "latest_negative_completed_observed_month": next((row for row in reversed(completed_months)
            if row["return_pct"] is not None and row["return_pct"] < 0), None),
        "trailing_negative_completed_observed_months": _negative_month_count(months,
            source_calendar_date(context.cutoff, (series.get("observation_schedule") or {}).get("market_calendar")).strftime("%Y-%m")),
        "peer_group": peer_group, "comparisons": [], "limitations": limitations,
        "method": "选定收益序列的实际观察日；月度区间按冻结时刻核验月末覆盖，缺失应有观察值或无法确认月末端点时不可用。仅冻结时刻所在月列为月内截至观察日；不把过期最后观察月当作当前月。短样本不年化；不凭亏损数值自动设风险等级。"}
    for other_id in dict.fromkeys([*([baseline] if baseline else []), *peers]):
        role = "configured_benchmark" if other_id == baseline else "taxonomy_peer" if other_id in taxonomy_peers else "configured_peer"
        instrument = get_record(InstrumentDetail, other_id)
        other_chart = get_record(InstrumentChartReadModel, other_id)
        other_snapshot_limitation = _snapshot_cutoff_limitation(other_chart, context.cutoff)
        other_series = (other_chart.payload_json.get("research_returns") or {}) if other_chart and not other_snapshot_limitation else {}
        other_points = _points(other_series)
        label = instrument.instrument_name if instrument else other_id
        other_frequency = (other_series.get("frequency") or {}).get("resolved_frequency")
        reason = snapshot_limitation or other_snapshot_limitation
        if not reason and (not frequency or frequency != other_frequency):
            reason = "收益序列频率不同或未确认"
        elif not reason and (not series.get("currency") or series.get("currency") != other_series.get("currency")):
            reason = "币种不同或缺失，未换算"
        elif not reason and (not (series.get("metadata") or {}).get("return_kind") or (series.get("metadata") or {}).get("return_kind") != (other_series.get("metadata") or {}).get("return_kind")):
            reason = "收益口径不同或未确认"
        common = sorted(set(points) & set(other_points))
        if len(common) < 2:
            reason = reason or "共同实际观察日不足"
        if reason:
            limitations.append(f"对照 {label}：{reason}，未计算相对表现。")
            continue
        # Retain the later requested endpoint. An absent last session in one
        # source must not disappear when common observations are intersected.
        comparison_start = max(min(points), min(other_points))
        comparison_end = max(max(points), max(other_points))
        comparison = compare_series({instrument_id: series, other_id: other_series},
            date.fromisoformat(comparison_start), date.fromisoformat(comparison_end),
            instrument_id, other_id, cutoff=context.cutoff)
        # Preserve each complete monthly path. Intersecting observations first
        # would hide a missing release or silently move a month-end anchor.
        target_months = months
        other_months = _month_periods(other_points, other_series, cutoff=context.cutoff)
        other_months_by_period = {(row["start_date"], row["end_date"]): row for row in other_months}
        monthly = []
        for target in target_months:
            other = other_months_by_period.get((target["start_date"], target["end_date"]))
            reason = target["limitation"] or (other["limitation"] if other else "月度实际起止日期不同或缺失")
            monthly.append({**target, "comparison_return_pct": other["return_pct"] if other else None,
                "excess_return_pp": target["return_pct"] - other["return_pct"] if reason is None else None,
                "status": "unavailable" if reason else "complete", "limitation": reason})
        output["comparisons"].append({"source_id": f"risk-comparison:{instrument_id}:{other_id}", "instrument_id": other_id,
            "name": label, "role": role, "configuration_source": "instrument_taxonomy" if role == "taxonomy_peer" else "instrument.nav_settings", "frequency": frequency,
            "comparison": comparison, "monthly_periods": monthly[-12:],
            "note": "仅为当前已登记同类或显式对照的同区间描述，不代表全市场同类排名；各对照的共同样本可能不同，不能合成排名。持续落后须查看多个非重叠区间，不能把重叠滚动窗口视作独立证据。"})
    if peer_limitation:
        limitations.append(peer_limitation)
    return serialize_payload(output)
