"""Publication test inputs representing completed specialist sessions.

Execution, evidence validation and role isolation are exercised separately in
test_research_sections. Publication tests start at the synthesis boundary.
"""
from watchlist_app.services.research_sections import SECTION_ORDER


def completed_sections(run):
    context = run.context_json
    cutoff = context.get("input_snapshot_cutoff") or context["cutoff"]
    results = {iid: {section: {"instrument_id": iid, "section": section,
        "status": "limited", "summary": "固定测试资料范围内已检查。", "source_ids": [],
        "limitations": ["此发布测试仅提供明确列出的固定资料。"], "risks": [], "events": [], "themes": [],
        "attempt": 1, "input_snapshot_cutoff": cutoff} for section in SECTION_ORDER}
        for iid in context["instrument_ids"]}
    run.context_json = {**context, "input_snapshot_cutoff": cutoff, "section_results": results,
        "execution": {**context.get("execution", {}), "research_section": "overview"}}
