from copy import deepcopy
import asyncio
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from watchlist_app import research_mcp as mcp
from watchlist_app.api.routes import sector_research as routes
from watchlist_app.services import research_quant as quant, quant_sandbox
from watchlist_app.services.research_notebook import ResearchNotebook, research_sources, retain_notebook, validate_notebook


CUTOFF = "2026-09-20T00:00:00+00:00"


def source(**changes):
    return {"source_id": "computed:input", "source_type": "computed_metric", "scope": "instrument",
            "instrument_id": "xlk", "as_of": "2026-09-18T00:00:00+00:00", "methodology": "Observed data, no fills",
            "data": {"points": [{"date": "2026-09-17", "value": 10}, {"date": "2026-09-18", "value": 12}]}, **changes}


def run(**changes):
    return SimpleNamespace(entry_id="run-quant", kind="analysis", status="running", context_json={
        "research_run": True, "cutoff": CUTOFF, "instrument_ids": ["xlk"], "computed_metrics": [source()], **changes})


def request(**changes):
    return quant.QuantAnalysisInput(**{"instrument_id": "xlk", "title": "示例计算", "source_ids": ["computed:input"],
        "code": "result={'summary':'Measured change','metrics':{'change':2}}", "methodology": "End minus start; observed dates only",
        "params": {"horizon": "observed_sample"}, **changes})


def output(**changes):
    return {"summary": "Observed change", "metrics": {"change": 2}, "tables": [{"key": "history", "title": "Observed values",
        "columns": [{"key": "date", "label": "Date"}, {"key": "value", "label": "Value", "unit": "USD"}],
        "rows": [{"date": "2026-09-17", "value": 10}, {"date": "2026-09-18", "value": 12}]}],
        "charts": [{"key": "values", "title": "Observed values", "kind": "line", "table_key": "history", "x_key": "date",
                    "series": [{"key": "value", "label": "Value"}], "x_label": "Date", "y_label": "USD"}], "limitations": [], **changes}


def test_only_authorized_retained_sources_enter_worker_and_all_provenance_survives(monkeypatch):
    record = run(computed_metrics=[source(), source(source_id="computed:other", instrument_id="other")])
    calls = []
    def execute(code, inputs, params):
        calls.append((code, deepcopy(inputs), deepcopy(params)))
        inputs["computed:input"]["data"]["points"][0]["value"] = 999
        return output(), {"backend": "test-isolation", "available": True}
    monkeypatch.setattr(quant_sandbox, "execute", execute)
    assert [item["source_id"] for item in quant.input_catalogue(record.context_json, record.entry_id, "xlk")] == ["computed:input"]
    evidence = quant.execute_analysis(record, request())
    assert set(calls[0][1]) == {"computed:input"}
    assert record.context_json["computed_metrics"][0]["data"]["points"][0]["value"] == 10
    assert evidence["as_of"] == CUTOFF
    assert evidence["source_ids"] == ["computed:input"]
    assert evidence["methodology"]["code"] == request().code
    assert evidence["methodology"]["params"] == request().params
    assert evidence["methodology"]["input_sources"][0]["as_of"] == "2026-09-18T00:00:00+00:00"
    assert evidence["data"]["analysis_kind"] == "python_quant"
    assert evidence["data"]["charts"][0]["table_key"] == "history"
    assert evidence in record.context_json["computed_metrics"]


@pytest.mark.parametrize("ids", [["computed:unknown"], ["computed:input", "computed:input"], ["computed:other"]])
def test_unknown_duplicate_and_other_instrument_inputs_rejected_before_execution(monkeypatch, ids):
    record = run(computed_metrics=[source(), source(source_id="computed:other", instrument_id="other")])
    monkeypatch.setattr(quant_sandbox, "execute", lambda *a: pytest.fail("unauthorized execution"))
    with pytest.raises(ValueError, match="输入"):
        quant.execute_analysis(record, request(source_ids=ids))


def test_future_data_and_methods_never_become_code_inputs(monkeypatch):
    record = run(computed_metrics=[source(as_of="2026-09-21T00:00:00+00:00")])
    assert quant.input_catalogue(record.context_json, record.entry_id, "xlk") == []
    monkeypatch.setattr(quant_sandbox, "execute", lambda *a: pytest.fail("future execution"))
    with pytest.raises(ValueError):
        quant.execute_analysis(record, request())


def test_risk_and_unbound_instrument_are_not_eligible():
    for context in ({"risk_run": True}, {"instrument_ids": ["other"]}):
        with pytest.raises(ValueError):
            quant.execute_analysis(run(**context), request())


def test_failed_or_invalid_code_output_does_not_write_a_source(monkeypatch):
    record = run()
    before = deepcopy(record.context_json)
    monkeypatch.setattr(quant_sandbox, "execute", lambda *a: (output(charts=[{**output()["charts"][0], "table_key": "invented"}]), {}))
    with pytest.raises(ValueError, match="实际留存"):
        quant.execute_analysis(record, request())
    assert record.context_json == before


@pytest.mark.parametrize("change", [
    {"metrics": {"bad": float("nan")}},
    {"html": "<script>bad()</script>"},
    {"charts": [{**output()["charts"][0], "kind": "svg"}]},
    {"charts": [{**output()["charts"][0], "series": [{"key": "missing", "label": "Missing"}]}]},
    {"tables": [{**output()["tables"][0], "rows": [{"date": "2026-09-17"}]}]},
    {"tables": [{**output()["tables"][0], "rows": [{"date": "2026-09-17", "value": "12"}]}]},
])
def test_outputs_reject_nonfinite_invented_columns_missing_cells_and_active_formats(change):
    with pytest.raises(ValueError):
        quant.QuantOutput.model_validate(output(**change))


def test_missing_observations_stay_null_in_tables_and_charts():
    value = output()
    value["tables"][0]["rows"][1]["value"] = None
    assert quant.QuantOutput.model_validate(value).tables[0].rows[1]["value"] is None


def test_actual_code_produces_source_bound_table_and_figure_without_mutating_input():
    capability = quant_sandbox.availability()
    if not capability["available"]:
        pytest.skip(capability["reason"])
    record = run()
    code = '''import pandas as pd
frame = pd.DataFrame(inputs["computed:input"]["data"]["points"])
result = {
    "summary": "The actual observed values changed by two units.",
    "metrics": {"change": float(frame["value"].iloc[-1] - frame["value"].iloc[0])},
    "tables": [{"key": "history", "title": "Observed values",
        "columns": [{"key": "date", "label": "Date"}, {"key": "value", "label": "Value", "unit": "USD"}],
        "rows": frame.to_dict("records")}],
    "charts": [{"key": "values", "title": "Observed values", "kind": "line", "table_key": "history",
        "x_key": "date", "series": [{"key": "value", "label": "Value"}], "x_label": "Date", "y_label": "USD"}],
    "limitations": ["Two observations are descriptive, not a forecast."]
}
inputs["computed:input"]["data"]["points"][0]["value"] = 999
'''
    evidence = quant.execute_analysis(record, request(code=code))
    assert evidence["data"]["metrics"]["change"] == 2
    assert evidence["methodology"]["input_sources"][0]["data"]["points"][0]["value"] == 10
    assert record.context_json["computed_metrics"][0]["data"]["points"][0]["value"] == 10
    assert evidence["data"]["tables"][0]["rows"] == source()["data"]["points"]
    validate_notebook(ResearchNotebook(modules=[{"key": "market-quantitative",
        "figure_source_ids": [evidence["source_id"]]}]), "xlk", research_sources(record.context_json, record.entry_id))


def test_computed_artifact_can_be_published_in_figure_and_reused_from_exact_notebook(monkeypatch):
    record = run()
    monkeypatch.setattr(quant_sandbox, "execute", lambda *a: (output(), {"backend": "test"}))
    evidence = quant.execute_analysis(record, request())
    notebook = ResearchNotebook(modules=[{"key": "pricing", "summary": "A measured observation",
        "source_ids": [evidence["source_id"]], "figure_source_ids": [evidence["source_id"]]}])
    sources = research_sources(record.context_json, record.entry_id)
    validate_notebook(notebook, "xlk", sources)
    retained = retain_notebook(notebook, None, sources, record.entry_id, CUTOFF)
    assert retained["sources"][0] == evidence
    later = run(computed_metrics=[], research_dossiers=[{"instrument_id": "xlk", "notebook": retained}])
    catalogue = quant.input_catalogue(later.context_json, later.entry_id, "xlk")
    assert catalogue[0]["source_id"] == evidence["source_id"]


def test_historical_version_has_its_own_namespace_and_cannot_fall_back_to_same_current_id(monkeypatch):
    historical = source(data={"points": [{"date": "2026-09-17", "value": 7}]}, as_of="2026-09-17T00:00:00+00:00")
    record = run(referenced_research_versions=[{"instrument_id": "xlk", "version_id": "exact-old",
        "information_cutoff": "2026-09-17T10:00:00+00:00", "sources": [historical]}])
    received = []
    def execute(code, inputs, params):
        received.append(deepcopy(inputs))
        return output(), {"backend": "test"}
    monkeypatch.setattr(quant_sandbox, "execute", execute)
    evidence = quant.execute_analysis(record, request(version_id="exact-old"))
    assert received[0]["computed:input"]["data"]["points"][0]["value"] == 7
    assert evidence["methodology"]["input_sources"] == [historical]
    assert evidence["methodology"]["input_version_id"] == "exact-old"
    assert evidence["methodology"]["input_information_cutoff"] == "2026-09-17T10:00:00+00:00"
    assert evidence["as_of"] == CUTOFF  # This calculation was not known on the old date.
    with pytest.raises(ValueError, match="输入"):
        quant.execute_analysis(record, request(version_id="exact-old", source_ids=[evidence["source_id"]]))
    with pytest.raises(ValueError, match="历史版本"):
        quant.execute_analysis(record, request(version_id="unbound-version"))
    assert len(received) == 1


def test_historical_source_future_to_that_version_is_not_used_even_if_known_now(monkeypatch):
    record = run(referenced_research_versions=[{"instrument_id": "xlk", "version_id": "old",
        "information_cutoff": "2026-09-17T10:00:00+00:00", "sources": [source()]}])
    assert quant.input_catalogue(record.context_json, record.entry_id, "xlk", "old") == []
    monkeypatch.setattr(quant_sandbox, "execute", lambda *a: pytest.fail("future historical evidence"))
    with pytest.raises(ValueError, match="输入"):
        quant.execute_analysis(record, request(version_id="old"))


def test_unknown_historical_cutoff_is_not_relabelled_as_current_cutoff(monkeypatch):
    record = run(referenced_research_versions=[{"instrument_id": "xlk", "version_id": "unknown-clock", "sources": [source()]}])
    monkeypatch.setattr(quant_sandbox, "execute", lambda *a: (output(), {}))
    evidence = quant.execute_analysis(record, request(version_id="unknown-clock"))
    assert evidence["methodology"]["input_information_cutoff"] is None
    assert evidence["methodology"]["input_sources"][0]["as_of"] == source()["as_of"]


class Session:
    def __init__(self, record):
        self.record, self.commits = record, 0
    def scalar(self, _):
        return self.record
    def commit(self):
        self.commits += 1


def test_api_ends_run_writes_and_reports_unavailable_explicitly(monkeypatch):
    record = run()
    session = Session(record)
    def unavailable(*_):
        raise quant_sandbox.SandboxUnavailable("test sandbox unavailable")
    monkeypatch.setattr(quant_sandbox, "execute", unavailable)
    with pytest.raises(HTTPException) as error:
        routes.run_quant(record.entry_id, request(), session)
    assert error.value.status_code == 503 and session.commits == 0
    record.status = "completed"
    with pytest.raises(HTTPException) as error:
        routes.run_quant(record.entry_id, request(), session)
    assert error.value.status_code == 409 and session.commits == 0


def test_mcp_execution_and_reads_share_one_retained_source_without_rerun(monkeypatch):
    record = run()
    calls = []
    def execute(*args):
        calls.append(args)
        return output(), {"backend": "test"}
    monkeypatch.setattr(quant_sandbox, "execute", execute)
    def route(suffix, payload=None, **kwargs):
        if suffix == "quant":
            return routes.run_quant(record.entry_id, quant.QuantAnalysisInput(**payload), Session(record))
        assert suffix.startswith("computed-source?")
        return deepcopy(record.context_json["computed_metrics"][-1])
    monkeypatch.setattr(mcp, "request", route)
    receipt = mcp.run_quant_analysis(**request().model_dump())
    assert receipt["source_id"].startswith("computed:")
    data = mcp.read_quant_analysis(receipt["source_id"], section="data", path=["tables", 0, "rows"])
    assert data["data"] == output()["tables"][0]["rows"]
    original = mcp.read_quant_analysis(receipt["source_id"], section="inputs", path=[0, "as_of"])
    assert original["data"] == "2026-09-18T00:00:00+00:00"
    assert len(calls) == 1


def test_large_tables_and_code_inputs_are_readable_under_actual_mcp_wire_budget(monkeypatch):
    record = run()
    value = output()
    value["tables"][0]["rows"] = [{"date": f"observation-{i}", "value": i} for i in range(5000)]
    monkeypatch.setattr(quant_sandbox, "execute", lambda *a: (value, {"backend": "test"}))
    evidence = quant.execute_analysis(record, request())
    monkeypatch.setattr(mcp, "request", lambda *a, **k: deepcopy(evidence))
    reconstructed, offset = [], 0
    while offset is not None:
        wire = asyncio.run(mcp.mcp.call_tool("read_quant_analysis", {
            "source_id": evidence["source_id"], "section": "data", "path": ["tables", 0, "rows"],
            "limit": 5000, "offset": offset}))
        text = wire.content[0].text
        assert len(text.encode()) <= 48000
        page = json.loads(text)
        assert page == wire.structured_content
        assert not page["deferred"]
        reconstructed.extend(page["data"])
        offset = page["next_offset"]
    assert reconstructed == value["tables"][0]["rows"]
