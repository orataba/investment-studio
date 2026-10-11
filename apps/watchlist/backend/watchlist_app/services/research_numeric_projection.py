"""Bounded computed-evidence responses before HTTP transport; exact stored inputs remain readable."""
from watchlist_app.services.research_read_projection import checked_overview, read_page, shape


def project_comparison(metric, *, section="overview", offset=0, limit=100, path=None):
    if "input_series" not in metric:
        raise ValueError("该来源不是已留存的标的共同样本比较。")
    metadata = {key: metric.get(key) for key in ("source_id", "as_of")}
    sections = {"result": metric["data"], "dates": metric["data"].get("dates", []),
                "input_series": metric["input_series"], "evidence": metric}
    if section != "overview":
        return read_page(sections[section], {**metadata, "section": section}, offset=offset, limit=limit, path=path)
    if offset or path:
        raise ValueError("overview不使用offset/path，请读取目录内的section。")
    read = {"tool": "compare_instruments", "source_id": metric["source_id"]}
    return checked_overview({**metadata, "source_type": metric["source_type"], "methodology": metric["methodology"],
        "request": metric.get("request"), "retrieved_at": metric.get("retrieved_at"),
        "result": {key: value for key, value in metric["data"].items() if key != "dates"},
        "sample_dates": {**shape(sections["dates"]), "read": {**read, "section": "dates"}},
        "sections": {key: shape(value) for key, value in sections.items()},
        "next_read": {**read, "section": "result"},
        "read_note": "概览保留完整样本计算的数值；dates是精确共同日期，input_series是当时输入。按需选分区并跟随next_offset和全部deferred.path；始终使用同一source_id，不缩短计算窗口或重新计算。"},
        pageable_fields=[(["result"], {**read, "section": "result"})])



def project_numbers(evidence, *, section="overview", offset=0, limit=20, path=None):
    if "input_series" in evidence:
        raise ValueError("该来源是标的比较，请用compare_instruments及同一source_id续读。")
    metadata = {key: evidence.get(key) for key in ("source_id", "as_of")}
    sections = {"data": evidence.get("data", {}), "sources": evidence.get("sources", []), "evidence": evidence}
    if section != "overview":
        return read_page(sections[section], {**metadata, "section": section}, offset=offset, limit=limit, path=path)
    if offset or path:
        raise ValueError("overview不使用offset/path，请读取目录内的section。")
    read = {"tool": "read_research_numbers", "source_id": evidence["source_id"]}
    result = {key: value for key, value in evidence.items() if key not in {"sources", "source_ids"}}
    data = dict(result.get("data") or {})
    if "series" in data:
        data["series"] = [{**{key: value for key, value in row.items() if key != "points"},
            **({"points": {**shape(row["points"]), "read": {**read, "section": "data", "path": ["series", index, "points"]}}}
               if "points" in row else {})} for index, row in enumerate(data["series"])]
    for key in ("input_points", "history"):
        if key in data:
            data[key] = {**shape(data[key]), "read": {**read, "section": "data", "path": [key]}}
    result["data"] = data
    result.update(sections={key: shape(value) for key, value in sections.items()},
        next_read={**read, "section": "data"},
        read_note="概览的points/history为完整原数据入口，数值计算使用全部请求样本。续读沿next_offset及全部deferred.path保持同一source_id；原始版本在sources/evidence，不以缩短日期区间代替续读。")
    return checked_overview(result, pageable_fields=[
        ([key], {**read, "section": "evidence", "path": [key]})
        for key in ("input_snapshot", "benchmark_snapshot", "methodology") if key in result
    ] + [(["data"], {**read, "section": "data"})])



def project_quant(source, *, section="data", offset=0, limit=30, path=None):
    if (source.get("data") or {}).get("analysis_kind") != "python_quant":
        raise ValueError("该来源不是已留存的 Python 量化产物，请使用它原来的数值读取工具。")
    resolve = None
    if section == "inputs":
        from watchlist_app.services.market_evidence import hydrate_source
        inputs = source["methodology"]["input_sources"]
        # A path into one input must not resolve every other original.
        if path and type(path[0]) is int and 0 <= path[0] < len(inputs):
            value = list(inputs)
            value[path[0]] = hydrate_source(inputs[path[0]], bound_source=True)
        else:
            value = [hydrate_source(item, bound_source=True) for item in inputs]
        def resolve(item, selected):
            # Only actual input-source positions carry trusted references.
            # A quant input may itself be a quant output with its own inputs;
            # never interpret a lookalike locator inside arbitrary result data.
            if (selected and type(selected[0]) is int and (len(selected) - 1) % 3 == 0
                    and all(selected[index:index + 2] == ["methodology", "input_sources"]
                            and type(selected[index + 2]) is int
                            for index in range(1, len(selected), 3))
                    and isinstance(item, dict) and item.get("source_type") == "computed_metric"
                    and item.get("retained_reference") and "data" not in item):
                return hydrate_source(item, bound_source=True)
            return item
    else:
        value = source[section]
    return read_page(value, {"source_id": source["source_id"], "section": section, "as_of": source["as_of"]},
                     offset=offset, limit=limit, path=path, resolve=resolve)
