"""Complete, bounded projections of one risk run's immutable evidence.

Shared by the delivery endpoint and scope/read-completeness checks. No I/O.
"""
import json


def _risk_case_brief(case):
    evidence = case.get("evidence_json") or {}
    return {**{key: value for key, value in case.items() if key not in {"history_json", "evidence_json"}},
        "evidence_json": {key: value for key, value in evidence.items() if key not in {"body", "title", "sources"}},
        "sources": [{key: value for key, value in source.items() if key != "text"}
                    for source in evidence.get("sources", [])]}


def _risk_instrument_overview(item):
    if item is None:
        return None
    result = {**item}
    if item.get("research_context"):
        result["research_context"] = {key: value for key, value in item["research_context"].items() if key != "records"}
    performance = item.get("performance_evidence")
    if not performance:
        return result
    return {**result, "performance_evidence": {
        **{key: value for key, value in performance.items() if key != "comparisons"},
        "comparison_count": len(performance.get("comparisons", [])),
    }}


def _risk_comparison_brief(comparison):
    # The full, pair-specific common dates stay in the bound snapshot. Returning
    # them for every peer repeats years of dates before the risk reports arrive.
    values = comparison.get("comparison") or {}
    return {**comparison, "comparison": {key: value for key, value in values.items() if key != "dates"},
        "sample_dates_count": len(values.get("dates", []))}


class _RiskToolSizeError(ValueError):
    """A complete bound record cannot fit this tool's result envelope."""


def _risk_instrument_page(build, count):
    while True:
        packet = build(count)
        if len(json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()) <= 48000:
            return packet
        if count <= 1:
            raise _RiskToolSizeError("单条风控记录超过工具返回上限，无法完整读取；未截断记录，也不能将未读证据视为没有风险。")
        count -= 1


def _risk_instrument_packet(context, instrument_id, *, section="overview", offset=0, comparison_source_id=None):
    if not context.get("risk_run") or instrument_id not in context["risk_inputs"]["instrument_ids"]:
        raise ValueError("只能读取本次风控范围内的标的。")
    if section not in {"overview", "cases", "comparisons", "sample_dates", "research_context"}:
        raise ValueError("请选择有效的标的风控资料分区。")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("风控分页offset必须是非负整数。")
    if (section == "sample_dates") != bool(comparison_source_id):
        raise ValueError("comparison_source_id仅用于sample_dates分区，且必须提供已返回的同类比较来源。")

    categories = ("research", "quantitative", "coverage")
    snapshots = (context["risk_inputs"], context.get("prior_inputs"))
    instruments = [next((item for item in (snapshot or {}).get("instruments", [])
                         if item["instrument_id"] == instrument_id), None) for snapshot in snapshots]
    case_maps = [{case["case_id"]: (category, case) for category in categories
                  for case in (snapshot or {}).get(category, []) if case["instrument_id"] == instrument_id}
                 for snapshot in snapshots]
    comparison_maps = [{row["source_id"]: row for row in ((item or {}).get("performance_evidence") or {}).get("comparisons", [])}
                       for item in instruments]
    judgment_maps = [{row["source_id"]: row for row in ((item or {}).get("research_context") or {}).get("records", [])}
                    for item in instruments]
    counts = [{**{category: sum(value[0] == category for value in cases.values()) for category in categories},
               "comparisons": len(comparisons)} for cases, comparisons in zip(case_maps, comparison_maps)]
    base = {"instrument_id": instrument_id, "cutoff": context["cutoff"], "section": section,
            "current_counts": counts[0], "previous_counts": counts[1],
            "research_context_counts": {"current": len(judgment_maps[0]), "previous": len(judgment_maps[1])}}

    def packet(current, previous, *, total, end):
        return {**base, "current": current,
            "previous": {"unchanged": True} if previous == current else previous,
            "offset": offset, "next_offset": end if end < total else None, "total": total,
            "read_note": "按overview计数，前后均为0的分区可跳过；有内容的cases/research_context从offset=0读至next_offset=null。cases包含完整风险事项；research_context保留PM档案、观点原文、当前问题与预测，不能当作已核实事实；comparisons按需读完各页后依各自共同样本比较，不合成排名。sample_dates按comparison_source_id另读实际共同日期。"}

    if section == "overview":
        if offset:
            raise ValueError("overview不使用分页，请从其他分区读取后续证据。")
        current = {"instrument": _risk_instrument_overview(instruments[0])}
        previous = {"instrument": _risk_instrument_overview(instruments[1])} if snapshots[1] else None
        return _risk_instrument_page(lambda _: packet(current, previous, total=1, end=1), 1)

    if section == "sample_dates":
        if not any(comparison_source_id in rows for rows in comparison_maps):
            raise ValueError("同类比较来源不在本次绑定的前后快照中。")
        samples = [((rows.get(comparison_source_id) or {}).get("comparison") or {}).get("dates", []) for rows in comparison_maps]
        total = max(map(len, samples))
        if offset > total:
            raise ValueError("风控分页超出本次绑定快照的范围。")
        def sample_page(count):
            current, previous = [{"comparison_source_id": comparison_source_id,
                "dates": sample[offset:offset + count], "total_dates": len(sample)} for sample in samples]
            return packet(current, previous if snapshots[1] else None, total=total, end=offset + count)
        return _risk_instrument_page(sample_page, min(500, total - offset))

    maps = case_maps if section == "cases" else judgment_maps if section == "research_context" else comparison_maps
    keys = list(dict.fromkeys([*maps[0], *maps[1]]))
    if offset > len(keys):
        raise ValueError("风控分页超出本次绑定快照的范围。")
    def rows_page(count):
        selected = keys[offset:offset + count]
        values = []
        for rows in maps:
            if section == "cases":
                values.append({category: [_risk_case_brief(rows[key][1]) for key in selected
                                         if key in rows and rows[key][0] == category] for category in categories})
            elif section == "research_context":
                values.append({"records": [rows[key] for key in selected if key in rows]})
            else:
                values.append({"comparisons": [_risk_comparison_brief(rows[key]) for key in selected if key in rows]})
        result = packet(values[0], values[1] if snapshots[1] else None, total=len(keys), end=offset + count)
        if section == "comparisons" and snapshots[1]:
            changed_samples = [key for key in selected if key in maps[0] and key in maps[1]
                and (maps[0][key].get("comparison") or {}).get("dates") != (maps[1][key].get("comparison") or {}).get("dates")]
            if changed_samples:
                result["changed_sample_source_ids"] = changed_samples
                result["previous"] = values[1]
        return result
    return _risk_instrument_page(rows_page, min(20, len(keys) - offset))


def _risk_overview_pages(context):
    """Advertise independent bound page offsets, avoiding serial model rounds."""
    pages, offset = [], 0
    while offset < len(context["risk_inputs"]["instrument_ids"]):
        try:
            page = _risk_overview_page(context, offset)
        except _RiskToolSizeError:
            # A small-envelope single read may still fit. Even if it does not,
            # preserve the scope index and make the unread item explicit.
            pages.append({"tool": "read_risk_instrument", "instrument_id": context["risk_inputs"]["instrument_ids"][offset],
                          "section": "overview", "instrument_count": 1})
            offset += 1
            continue
        pages.append({"tool": "read_risk_instruments", "offset": offset, "instrument_count": len(page["instruments"])})
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    return pages


def _risk_overview_page(context, offset):
    if not context.get("risk_run"):
        raise ValueError("仅风控研判可读取本次绑定的标的风控概览。")
    ids = context["risk_inputs"]["instrument_ids"]
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > len(ids):
        raise ValueError("风控分页超出本次绑定快照的范围。")

    def page(items):
        end = offset + len(items)
        return {"cutoff": context["cutoff"], "section": "instrument_overviews", "instruments": items,
                "offset": offset, "next_offset": end if end < len(ids) else None, "total": len(ids),
                "next_read": "按范围索引的全部计划offset读取概览，已知页可并行；没有计划时跟随next_offset，不必逐标的重读overview。再以read_risk_instrument读取前后计数非零的cases及research_context全部页。比较按研判需要另读，未读不代表没有风险。"}

    items = []
    for instrument_id in ids[offset:]:
        try:
            item = _risk_instrument_packet(context, instrument_id)
        except _RiskToolSizeError:
            if items:
                break
            raise
        candidate = page([*items, item])
        if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode()) > 48000:
            if not items:
                # The individual tool has less envelope overhead and remains
                # available at this explicit boundary; never skip the asset.
                raise _RiskToolSizeError("单个风控概览超过批量页上限；请用read_risk_instrument逐项读取当前及后续标的，未截断或跳过任何标的。")
            break
        items.append(item)
    return page(items)


def required_detail_reads(context):
    """Enumerate exact nonempty pages, including previous-only evidence.

    An oversized record remains an explicit required read, rather than making
    the scope index disappear or treating inaccessible evidence as empty.
    """
    required = []
    for instrument_id in context['risk_inputs']['instrument_ids']:
        for section in ('cases', 'research_context'):
            offset = 0
            while True:
                read = {'tool': 'read_risk_instrument', 'instrument_id': instrument_id,
                        'section': section, 'offset': offset}
                try:
                    packet = _risk_instrument_packet(context, instrument_id, section=section, offset=offset)
                except _RiskToolSizeError:
                    required.append(read)
                    break
                if packet['total']:
                    required.append(read)
                if packet['next_offset'] is None:
                    break
                offset = packet['next_offset']
    return required


def project_risk_read(context, *, section, instrument_id=None, offset=0, comparison_source_id=None):
    if section == 'instrument_overviews':
        if instrument_id is not None or comparison_source_id is not None:
            raise ValueError('批量概览仅接受offset，不接受单标的或比较来源。')
        return _risk_overview_page(context, offset)
    return _risk_instrument_packet(context, instrument_id, section=section, offset=offset,
                                   comparison_source_id=comparison_source_id)


def delivered_page_keys(packet):
    if packet['section'] == 'instrument_overviews':
        return [[item['instrument_id'], 'overview', 0] for item in packet['instruments']]
    if packet['section'] in ('overview', 'cases', 'research_context'):
        return [[packet['instrument_id'], packet['section'], packet['offset']]]
    return []


def missing_required_reads(context):
    delivered = {tuple(page) for page in context.get('risk_delivered_pages', [])}
    required = [{'tool': 'read_risk_instrument', 'instrument_id': instrument_id, 'section': 'overview', 'offset': 0}
                for instrument_id in context['risk_inputs']['instrument_ids']]
    required.extend(required_detail_reads(context))
    return [read for read in required
            if (read['instrument_id'], read['section'], read['offset']) not in delivered]
