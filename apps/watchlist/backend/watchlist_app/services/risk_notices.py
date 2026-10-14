"""Selected risk notices; evidence and independent case lifecycles stay authoritative."""
from uuid import uuid4

LEVEL_ORDER = {"high": 0, None: 0, "medium": 1, "low": 2}
NOTICE_FIELDS = ("level", "title", "body", "instrument_ids", "holding_ids", "case_ids", "source_ids")


def is_active(case):
    return bool(case.get("trigger_active") and case.get("status") not in {"resolved", "handled"}
                and not (case.get("evidence_json") or {}).get("withdrawn"))


def mandatory_case(case):
    return bool(case.get("trigger_active") and case.get("signal") in {"drawdown_limit", "period_loss"}
                and (case.get("evidence_json") or {}).get("mandatory_notice"))


def deterministic_notice(case):
    evidence = case.get("evidence_json") or {}
    # The deterministic rule owns its fact text and precise current reading.
    # Neither a missing model nor an older accepted notice can suppress it.
    return {"notice_id": f"rule:{case['case_id']}",
        "notice_version": f"{evidence.get("rule_version") or 1}:{evidence.get("trigger_version") or 1}", "level": None,
        "title": case["title"], "body": case.get("body") or "已触发已批准的严重规则，原因分析尚未完成。",
        "instrument_ids": [case["instrument_id"]], "holding_ids": [],
        "case_ids": [case["case_id"]], "source_ids": [], "origin": "deterministic",
        "as_of": str(case.get("observed_on") or ""), "alert_state": "assessment_pending",
        "metric_snapshot_id": evidence.get("metric_snapshot_id"), "rule_version": evidence.get("rule_version"),
        "mandatory": True}


def version_notices(items, *, previous, run_id, reviewed_at, cases, sources=None):
    """Reuse identity for an existing evidence group; retain every published run."""
    def identity(item):
        return tuple(tuple(sorted(item.get(key) or [])) for key in
                     ("instrument_ids", "holding_ids", "case_ids", "source_ids"))
    prior = {identity(item): item for item in previous or []}
    result = []
    for item in items:
        value = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        old = prior.get(identity(value))
        unchanged = old and all(old.get(key) == value.get(key) for key in NOTICE_FIELDS)
        evidence_dates = [str(cases[cid].get("observed_on")) for cid in value.get("case_ids", []) if cases[cid].get("observed_on")]
        for sid in value.get("source_ids", []):
            source = (sources or {}).get(sid) or {}
            observed = source.get("end_date") or source.get("recorded_at") or source.get("as_of")
            if observed:
                evidence_dates.append(str(observed))
        fact_date = old.get("as_of") if unchanged else value.get("as_of") or max(evidence_dates, default=None)
        result.append({**value, "notice_id": old["notice_id"] if old else uuid4().hex,
            "notice_version": old.get("notice_version", 1) if unchanged else int(old.get("notice_version", 1)) + 1 if old else 1,
            "origin": "risk_officer", "run_id": run_id, "as_of": fact_date,
            "reviewed_at": reviewed_at, "alert_state": "assessed",
            "case_versions": {cid: (cases[cid].get("evidence_json") or {}).get("event_version_id")
                              for cid in value.get("case_ids", [])}})
    return sorted(result, key=lambda item: LEVEL_ORDER[item["level"]])


def current_notices(saved, cases, *, scope_available=True):
    """Keep the accepted wording on revisions; drop only explicit terminal facts."""
    if not scope_available:
        return [{**item, "alert_state": "assessment_pending",
                 "status_note": "当前范围资料暂不可用，保留此前已评估提示及原日期；未确认风险解除。"}
                for item in (saved or {}).get("notice_items") or []]
    by_id = {case["case_id"]: case for case in cases}
    result = []
    for item in (saved or {}).get("notice_items") or []:
        ids = item.get("case_ids") or []
        referenced = [by_id[cid] for cid in ids if cid in by_id]
        def relevant(case):
            evidence = case.get("evidence_json") or {}
            return not evidence.get("withdrawn") and (is_active(case)
                or (evidence.get("risk_assessment") or {}).get("status") == "pending")
        # Removal from an authorized current scope does not retain exposure.
        if ids and (len(referenced) != len(ids) or not any(relevant(case) for case in referenced)):
            continue
        resolved_ids = [case["case_id"] for case in referenced if not relevant(case)]
        pending = bool(resolved_ids) or any((case.get("evidence_json") or {}).get("pending_revision") or
            ((case.get("evidence_json") or {}).get("risk_assessment") or {}).get("status") == "pending"
            for case in referenced)
        result.append({**item, "alert_state": "assessment_pending" if pending else item.get("alert_state", "assessed"),
            **({"resolved_case_ids": resolved_ids,
                "status_note": "合写事项中已有部分解除、撤回或处理；保留原评估文字供核对，其余事项仍独立有效，需复核提示。"} if resolved_ids else {})})
    covered = {cid for item in result for cid in item.get("case_ids", [])}
    for case in cases:
        if mandatory_case(case):
            # The live rule fact is included even when an old model item cites
            # this case; a separate expansion must never be needed to see it.
            current = deterministic_notice(case)
            if case["case_id"] in covered:
                for item in result:
                    if case["case_id"] in item.get("case_ids", []):
                        item.setdefault("current_rule_facts", []).append(current)
                        item["mandatory"] = True
            else:
                result.append(current)
    return sorted(result, key=lambda item: (not item.get("mandatory", False), LEVEL_ORDER.get(item.get("level"), 3)))
