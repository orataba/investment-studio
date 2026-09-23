"""Module receipts must preserve sparse proposals and their original evidence."""
from copy import deepcopy
from datetime import datetime

import pytest
from jsonschema import Draft202012Validator

from watchlist_app.services import sector_fact_review as review
from watchlist_app.services.research_notebook import ResearchNotebook, retain_notebook, validate_notebook
from watchlist_app.services.sector_review_protocol import expand_review_receipts


CUTOFF = "2026-09-20T00:00:00+00:00"
SOURCE = {"source_id": "original", "source_type": "public_source", "instrument_id": "xlk",
          "published_at": "2026-09-01", "text": "The issuer reports its actual portfolio."}
FIGURE = {"source_id": "computed:observed", "source_type": "computed_metric", "instrument_id": "xlk",
          "as_of": "2026-09-19", "methodology": "Retained common adjusted-price sample",
          "data": {"return_pct": 5.2}}
SOURCES = {item["source_id"]: item for item in (SOURCE, FIGURE)}
PLAN = {"modules": [{"id": "equity-aggregation", "version": "1"},
                    {"id": "market-quantitative", "version": "1"}]}


def module(key="equity-aggregation", **changes):
    return {"key": key, "summary": "Research remains conditional", "analysis": "Compare disclosed exposures.",
            "coverage": "supported", "source_ids": ["original"], **changes}


def proposed(modules):
    return [{"instrument_id": "xlk", "summary": "", "change_kind": "knowledge", "coverage": [],
             "events": [], "themes": [], "reflection": None, "research": {"modules": modules}}]


def accepted():
    return {"reviews": [{"instrument_id": "xlk", "summary": {"decision": "accept"},
        "change_kind": {"decision": "accept"}, "coverage": {"decision": "accept"},
        "decisions": [], "themes": [], "reflection": None, "research": {"decision": "accept"}}]}


def corrected(receipts):
    result = accepted()
    result["reviews"][0]["research"] = {"decision": "correct", "reason": "Check the proposed module revisions",
        "patch": {"modules": receipts}, "omit_fields": []}
    return result


def checked(rows, receipts, *, previous=None):
    schema = review._review_schema(rows, list(SOURCES.values()))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(receipts)
    expanded = expand_review_receipts(rows, receipts)
    dossiers = [{"instrument_id": "xlk", "notebook": previous, "research_plan": PLAN}]
    return review._apply_checks({"reviews": rows}, expanded, list(SOURCES.values()), dossiers)["reviews"][0]["research"]


def test_accept_keeps_sparse_modules_and_independently_bound_figure_sources():
    rows = proposed([module(figure_source_ids=[FIGURE["source_id"]])])
    original = deepcopy(rows)
    delta = checked(rows, accepted())
    assert delta == rows[0]["research"]
    saved = retain_notebook(ResearchNotebook.model_validate(delta), None, SOURCES, "new", CUTOFF,
                            research_plan=PLAN)
    assert {source["source_id"] for source in saved["sources"]} == set(SOURCES)
    assert saved["modules"][0]["figure_source_ids"] == [FIGURE["source_id"]]
    assert rows == original


def test_module_rejection_preserves_old_analysis_and_does_not_advance_its_check_clock():
    old = retain_notebook(ResearchNotebook(modules=[module()]), None, SOURCES, "old", CUTOFF,
                          research_plan=PLAN)
    rows = proposed([{"key": "equity-aggregation", "analysis": "Unsupported replacement"}])
    delta = checked(rows, corrected([{"key": "equity-aggregation", "decision": "reject",
                                     "reason": "The cited material does not support this revision"}]), previous=old)
    result = retain_notebook(ResearchNotebook.model_validate(delta), old, SOURCES, "check",
                             "2026-09-21T00:00:00+00:00", research_plan=PLAN)
    assert result["modules"] == old["modules"]
    assert result["version_id"] == old["version_id"]


def test_corrected_optional_module_omission_keeps_its_prior_field_and_original_history():
    old = retain_notebook(ResearchNotebook(modules=[module(gaps=["Original missing disclosure"])]),
                          None, SOURCES, "old", CUTOFF, research_plan=PLAN)
    before = deepcopy(old)
    rows = proposed([{"key": "equity-aggregation", "summary": "Proposed revision", "gaps": []}])
    delta = checked(rows, corrected([{"key": "equity-aggregation", "decision": "correct",
        "reason": "The disclosure is still missing", "patch": {"summary": "Corrected assessment"},
        "omit_fields": ["gaps"]}]), previous=old)
    assert delta == {"modules": [{"key": "equity-aggregation", "summary": "Corrected assessment"}]}
    saved = retain_notebook(ResearchNotebook.model_validate(delta), old, SOURCES, "corrected",
                            "2026-09-21T00:00:00+00:00", research_plan=PLAN)
    assert saved["modules"][0]["gaps"] == old["modules"][0]["gaps"]
    assert saved["modules"][0]["analysis"] == old["modules"][0]["analysis"]
    assert saved["modules"][0]["versions"] == [{key: value for key, value in old["modules"][0].items() if key != "versions"}]
    assert old == before


@pytest.mark.parametrize("receipts", [
    [],
    [{"key": "equity-aggregation", "decision": "accept"}] * 2,
    [{"key": "invented", "decision": "accept"}],
    [{"key": "equity-aggregation", "decision": "correct", "reason": "Retarget", "patch": {"key": "market-quantitative"}, "omit_fields": []}],
])
def test_module_review_cannot_omit_duplicate_or_retarget_a_proposed_record(receipts):
    with pytest.raises(ValueError):
        expand_review_receipts(proposed([module()]), corrected(receipts))


@pytest.mark.parametrize("figure_source", ["invented", "original"])
def test_corrected_figures_still_require_retained_numerical_sources(figure_source):
    rows = proposed([module(figure_source_ids=[FIGURE["source_id"]])])
    receipt = {"key": "equity-aggregation", "decision": "correct", "reason": "Replace the figure reference",
               "patch": {"figure_source_ids": [figure_source]}, "omit_fields": []}
    with pytest.raises(ValueError, match="未取得|研究图表"):
        checked(rows, corrected([receipt]))


def test_sparse_coverage_recheck_uses_the_bound_previous_modules_sources():
    old = retain_notebook(ResearchNotebook(modules=[module()]), None, SOURCES, "old", CUTOFF,
                          research_plan=PLAN)
    rows = proposed([{"key": "equity-aggregation", "coverage": "supported"}])
    delta = checked(rows, accepted(), previous=old)
    assert delta == rows[0]["research"]


def test_checking_only_the_second_module_does_not_create_a_new_judgment_version():
    old = retain_notebook(ResearchNotebook(modules=[module(), module("market-quantitative")]),
                          None, SOURCES, "old", CUTOFF, research_plan=PLAN)
    rows = proposed([{"key": "market-quantitative"}])
    delta = checked(rows, accepted(), previous=old)
    saved = retain_notebook(ResearchNotebook.model_validate(delta), old, SOURCES, "check",
                            "2026-09-21T00:00:00+00:00", research_plan=PLAN)
    assert saved["version_id"] == old["version_id"]
    assert saved["updated_at"] == old["updated_at"]
    by_key = {item["key"]: item for item in saved["modules"]}
    assert by_key["equity-aggregation"] == old["modules"][0]
    assert by_key["market-quantitative"]["checked_at"] == "2026-09-21T00:00:00+00:00"


def test_sparse_module_review_receives_inherited_originals_and_figures_without_unrelated_module_evidence(monkeypatch):
    unrelated = {**SOURCE, "source_id": "unrelated", "text": "A separate unchanged module's original."}
    sources = {**SOURCES, "unrelated": unrelated}
    old = retain_notebook(ResearchNotebook(modules=[module(figure_source_ids=[FIGURE["source_id"]]),
        module("market-quantitative", source_ids=["unrelated"])]), None, sources, "old", CUTOFF,
        research_plan=PLAN)
    context = {"cutoff": CUTOFF, "research_dossiers": [{"instrument_id": "xlk", "notebook": old}],
               "web_evidence": []}
    from urllib.parse import parse_qs
    reads = []

    def api(run_id, suffix, payload=None):
        assert run_id == "check" and payload is None
        query = parse_qs(suffix.partition("?")[2])
        source_id = query["source_id"][0]
        reads.append(source_id)
        return deepcopy(sources[source_id])

    monkeypatch.setattr(review, "_api_request", api)
    packet = review._evidence_packet(context, proposed([{"key": "equity-aggregation", "summary": "Sparse update"}]), "check")
    assert set(reads) == set(SOURCES)
    assert {source["source_id"] for source in packet["sources"]} == set(SOURCES)
    assert next(source for source in packet["sources"] if source["source_id"] == FIGURE["source_id"])["data"] == FIGURE["data"]
    assert context["research_dossiers"][0]["notebook"] == old


def test_module_version_read_keeps_original_cutoff_while_current_view_shows_the_later_check(monkeypatch):
    from types import SimpleNamespace
    from watchlist_app.services import research_dossier as dossiers

    old = retain_notebook(ResearchNotebook(modules=[module(figure_source_ids=[FIGURE["source_id"]])]),
                          None, SOURCES, "old", CUTOFF, research_plan=PLAN)
    later_cutoff = "2026-09-21T00:00:00+00:00"
    new_plan = {"modules": [{**item, "version": "2"} for item in PLAN["modules"]]}
    quiet = retain_notebook(ResearchNotebook(modules=[{"key": "equity-aggregation"}]), old,
                            SOURCES, "check", later_cutoff, research_plan=new_plan)
    records = [(SimpleNamespace(entry_id=run, context_json={"cutoff": cutoff},
                                completed_at=cutoff, created_at=cutoff), paper)
               for run, cutoff, paper in (("check", later_cutoff, quiet), ("old", CUTOFF, old))]
    monkeypatch.setattr(dossiers, "require_instrument", lambda *_: None)
    monkeypatch.setattr(dossiers, "_research_records", lambda *_: iter(records))
    current, history = dossiers._notebooks(None, "xlk", True)
    assert current["checked_at"] == later_cutoff
    assert current["modules"][0]["method_version"] == "2"
    assert len(history) == 1
    assert history[0]["checked_at"] == CUTOFF
    archived = dossiers.read_dossier_version(None, "xlk", old["modules"][0]["version_id"])
    assert archived["kind"] == "modules"
    assert archived["information_cutoff"] == CUTOFF
    assert archived["value"]["method_version"] == "1"
    assert archived["value"]["checked_at"] == CUTOFF
    assert {source["source_id"] for source in archived["sources"]} == set(SOURCES)


def test_module_evidence_date_cannot_claim_future_knowledge_even_when_a_retained_source_is_valid():
    cutoff = datetime.fromisoformat(CUTOFF)
    current = ResearchNotebook(modules=[module(evidence_as_of="2026-09-20")])
    validate_notebook(current, "xlk", SOURCES, research_plan=PLAN, cutoff=cutoff)
    future = ResearchNotebook(modules=[module(evidence_as_of="2026-09-21")])
    with pytest.raises(ValueError, match="不能晚于本轮研究截止"):
        validate_notebook(future, "xlk", SOURCES, research_plan=PLAN, cutoff=cutoff)


def test_research_refinement_cannot_forge_user_authority_or_change_existing_user_scope():
    from watchlist_app.services.research_dossier import ResearchMandateInput, effective_mandate, plan_for_mandate

    previous = ResearchMandateInput(title="XLK研究", background="实际成份研究", user_constraints=["只研究实际成份"],
        module_focus=[{"module_id": "equity-aggregation", "reason": "用户指定股票成份研究", "selected_by": "user"}]).model_dump(mode="json")
    before = deepcopy(previous)
    proposed = ResearchMandateInput(title="XLK研究", background="补充本期背景", user_constraints=["扩大行业范围"],
        module_focus=[{"module_id": "rates-credit", "reason": "尚待核实的利率敏感性", "selected_by": "user"}])
    effective = effective_mandate(proposed, previous, origin="research")
    assert effective["user_constraints"] == previous["user_constraints"]
    by_key = {item["module_id"]: item for item in effective["module_focus"]}
    assert by_key["equity-aggregation"] == previous["module_focus"][0]
    assert by_key["rates-credit"]["selected_by"] == "research"
    plan = plan_for_mandate({"instrument_type": "etf", "name": "XLK"}, proposed,
                            origin="research", previous_mandate=previous)
    applicable = {item["id"]: item["applicability"] for item in plan["modules"]}
    assert applicable["equity-aggregation"] == "applicable"
    assert applicable["rates-credit"] == "unconfirmed"
    assert "只研究实际成份" in plan["scope"] and "扩大行业范围" not in plan["scope"]
    assert previous == before
