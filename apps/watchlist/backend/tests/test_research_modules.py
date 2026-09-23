"""The new reading surface shares durable, source-bound analytical modules."""
from copy import deepcopy

import pytest

from watchlist_app.services.research_notebook import (
    ResearchNotebook, notebook_current_view, notebook_source_ids, retain_notebook, validate_notebook,
)


CUTOFF = "2026-09-22T00:00:00+00:00"
PLAN = {"modules": [{"id": "pricing-compensation", "version": "1"},
                     {"id": "fund-strategy", "version": "1"}]}
SOURCE = {"source_id": "computed:observed", "instrument_id": "fund", "source_type": "computed_metric",
          "as_of": "2026-09-21", "methodology": "Common observed NAV sample", "data": {"return_pct": 5.2}}
SOURCES = {SOURCE["source_id"]: SOURCE}


def module(**changes):
    return {"key": "pricing-compensation", "summary": "可比条件仍需核实", "analysis": "净值点位不表示价格便宜。",
            "coverage": "partial", "source_ids": [SOURCE["source_id"]], **changes}


def test_module_sparse_updates_keep_other_analysis_and_evidence_with_distinct_clocks():
    first = retain_notebook(ResearchNotebook(modules=[module(), module(key="fund-strategy", summary="策略待验证")]),
                            None, SOURCES, "first", CUTOFF, research_plan=PLAN)
    first_unchanged = deepcopy(first)
    next_cutoff = "2026-09-23T00:00:00+00:00"
    checked = retain_notebook(ResearchNotebook(modules=[{"key": "pricing-compensation"}]), first,
                              SOURCES, "checked", next_cutoff, research_plan=PLAN)
    by_key = {item["key"]: item for item in checked["modules"]}
    assert by_key["pricing-compensation"]["updated_at"] == first["modules"][0]["updated_at"]
    assert by_key["pricing-compensation"]["checked_at"] == next_cutoff
    assert by_key["pricing-compensation"]["source_ids"] == [SOURCE["source_id"]]
    assert by_key["fund-strategy"] == first["modules"][1]
    assert checked["version_id"] == first["version_id"]
    assert first == first_unchanged
    revised = retain_notebook(ResearchNotebook(modules=[{"key": "pricing-compensation", "summary": "新的条件判断",
                              "gaps": ["缺少最新持仓"]}]), checked, SOURCES, "revised", next_cutoff, research_plan=PLAN)
    current = revised["modules"][0]
    assert current["version_id"] == "revised:modules:pricing-compensation"
    assert current["versions"][0]["summary"] == first["modules"][0]["summary"]
    assert current["analysis"] == first["modules"][0]["analysis"]
    assert revised["version_id"] == "revised"


def test_method_recheck_does_not_rewrite_judgment_or_claim_unchecked_modules_were_reviewed():
    first = retain_notebook(ResearchNotebook(modules=[module(), module(key="fund-strategy")]), None,
                            SOURCES, "first", CUTOFF, research_plan=PLAN)
    plan = {"modules": [{"id": item["id"], "version": "2"} for item in PLAN["modules"]]}
    result = retain_notebook(ResearchNotebook(modules=[{"key": "pricing-compensation"}]), first,
                            SOURCES, "check", CUTOFF, research_plan=plan)
    assert result["modules"][0]["method_version"] == "2"
    assert result["modules"][0]["updated_at"] == first["modules"][0]["updated_at"]
    assert result["modules"][1]["method_version"] == "1"


def test_modules_use_applicable_method_ids_and_only_bound_numerical_figures():
    paper = ResearchNotebook(modules=[module(figure_source_ids=[SOURCE["source_id"]])])
    validate_notebook(paper, "fund", SOURCES, research_plan=PLAN)
    assert notebook_source_ids(paper) == {SOURCE["source_id"]}
    with pytest.raises(ValueError, match="方法范围"):
        validate_notebook(paper, "fund", SOURCES, research_plan={"modules": []})
    with pytest.raises(ValueError, match="重复"):
        validate_notebook(ResearchNotebook(modules=[module(), module()]), "fund", SOURCES)
    with pytest.raises(ValueError, match="资料充分"):
        validate_notebook(ResearchNotebook(modules=[module(coverage="supported", source_ids=[])]), "fund", SOURCES)
    original = {"source_id": "original", "source_type": "public_source", "text": "An actual disclosure."}
    with pytest.raises(ValueError, match="研究图表"):
        validate_notebook(ResearchNotebook(modules=[module(figure_source_ids=["original"])]), "fund", {**SOURCES, "original": original})
    with pytest.raises(ValueError, match="未取得"):
        validate_notebook(ResearchNotebook(modules=[module(figure_source_ids=["invented"])]), "fund", SOURCES)
    with pytest.raises(ValueError, match="其他标的"):
        validate_notebook(paper, "another-fund", SOURCES)


def test_historical_prose_is_readable_without_backdating_modular_research_or_mutating_originals():
    old = {"fundamental_view": "原经营判断", "valuation_view": "原定价判断", "version_id": "old",
           "updated_at": "2026-09-01T01:00:00+00:00", "source_ids": [SOURCE["source_id"]]}
    before = deepcopy(old)
    decoded = notebook_current_view(old)
    assert decoded["modules"] == []
    assert decoded["questions"] == decoded["key_drivers"] == decoded["next_research"] == []
    assert decoded["important_changes"] == decoded["facts"] == decoded["sources"] == []
    assert decoded["prior_analysis"]["fundamental_view"] == old["fundamental_view"]
    assert decoded["prior_analysis"]["updated_at"] == old["updated_at"]
    assert "fundamental_view" not in decoded and "valuation_view" not in decoded
    assert old == before
    assert notebook_current_view(decoded) == decoded
    current = retain_notebook(ResearchNotebook(modules=[module()]), old, SOURCES, "new", CUTOFF, research_plan=PLAN)
    assert current["prior_analysis"] == decoded["prior_analysis"]
    assert current["schema_version"] == 2 and "fundamental_view" not in current
    with pytest.raises(ValueError, match="Extra inputs"):
        ResearchNotebook(fundamental_view="No parallel writer")
