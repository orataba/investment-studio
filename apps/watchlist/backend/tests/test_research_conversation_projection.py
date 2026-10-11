"""Conversation polling keeps message bodies, with exact evidence fetched on demand."""
from copy import deepcopy
from dataclasses import replace
import json

from studio_identity import principal_context

from .test_account_research_access import accounts, as_user, create_topic
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_run_evidence import retain_computed_source


def test_conversation_receipts_are_small_and_all_originals_remain_readable(accounts):
    client, principals, _ = accounts
    as_user(client, "alice")
    topic_id = create_topic(client, instrument_ids=["fund-us-agg"])
    huge = "原始观察，保留但不随状态轮询传输。" * 50000
    source = {"source_id": "comparison:new", "source_type": "computed_metric", "scope": "public_market",
        "title": "共同样本比較", "data": {"value": 0.123, "method": "common sample"},
        "input_series": {"fund-us-agg": [{"original": huge}]},
        "request": {"instrument_ids": ["fund-us-agg"]}, "retrieved_at": "2026-10-01T00:00:00Z"}
    quant = {"source_id": "computed:python", "source_type": "computed_metric", "scope": "instrument",
        "instrument_id": "fund-us-agg", "title": "Retained Python calculation",
        "data": {"analysis_kind": "python_quant", "metrics": {"residual": -0.0}, "tables": []},
        "methodology": {"code": "result = retained_calculation(inputs)", "input_sources": [{"source_id": "comparison:new"}]},
        "retrieved_at": "2026-10-01T00:01:00Z"}
    receipts = [
        {"source_id": "portfolio:old", "tool": "portfolio", "retrieved_at": "2026-09-01", "result": {"holdings": [], "original": huge}},
        {"source_id": "instruments:old", "tool": "instruments", "retrieved_at": "2026-09-01", "request": {"instrument_ids": ["fund-us-agg"]}, "result_storage": "instrument_inputs"},
        {"source_id": "dossier:old", "tool": "dossier", "retrieved_at": "2026-09-01", "request": {"instrument_ids": ["fund-us-agg"], "source_id": "requested"}, "result_storage": "research_dossiers"},
        {"source_id": "dossier:computed", "tool": "dossier", "retrieved_at": "2026-09-01", "request": {"instrument_ids": ["fund-us-agg"], "source_id": "comparison:new"}, "result_storage": "research_dossiers"},
    ]
    body = "完整回答与反证。" * 1000
    context = {"research_actor": principals["alice"].to_dict(), "execution": {"attempt": 1}, "cutoff": "2026-10-01",
        "instrument_ids": ["fund-us-agg"], "research_publication": {"status": "published", "instrument_ids": ["fund-us-agg"]},
        "user_records": [{"kind": "theme", "id": "theme-one"}], "page_context": {"research_reference": {"kind": "risk", "id": "risk-one"}},
        "tool_evidence": receipts, "instrument_inputs": [{"instrument_id": "fund-us-agg", "original": huge}],
        "research_dossiers": [{"instrument_id": "fund-us-agg", "materials": [{"source_id": "requested", "body": "Requested original"}, {"source_id": "unread", "body": "Unread original"}],
            "prior_sources": [{"source_id": "comparison:new", "source_type": "computed_metric", "scope": "public_market",
                "retained_reference": {"run_id": "projection-run", "path": ["computed_metrics", "*"]}}]}],
        "unrelated_archive": huge}
    with get_session_factory()() as session:
        run = ResearchEntry(entry_id="projection-run", topic_id=topic_id, title="问题", kind="analysis", status="running", body=body, context_json=deepcopy(context))
        session.add(run)
        session.flush()
        with principal_context(replace(principals["alice"], resource_scope={"kind": "run", "id": "projection-run", "attempt": "1"})):
            retain_computed_source(session, run, source)
            retain_computed_source(session, run, quant)
        session.commit()
    material = client.post(f"/api/research/topics/{topic_id}/files", files={"file": ("notes.txt", b"Original attachment", "text/plain")}).json()
    response = client.get(f"/api/research/topics/{topic_id}")
    assert response.status_code == 200, response.text
    entries = {entry["entry_id"]: entry for entry in response.json()["entries"]}
    assert entries[material["entry_id"]]["context_json"] == material["context_json"]
    assert entries["projection-run"]["body"] == body
    projected = entries["projection-run"]["context_json"]
    assert projected["research_publication"] == context["research_publication"]
    assert projected["user_records"] == context["user_records"]
    assert projected["page_context"] == context["page_context"]
    assert {row["source_id"] for row in projected["tool_evidence"]} == {"portfolio:old", "instruments:old", "dossier:old", "dossier:computed", "comparison:new", "computed:python"}
    assert next(row for row in projected["tool_evidence"] if row["source_id"] == "computed:python")["tool"] == "quant"
    assert "retained_calculation" not in response.text
    assert all("result" not in row for row in projected["tool_evidence"])
    assert "instrument_inputs" not in projected and "research_dossiers" not in projected and "unrelated_archive" not in projected
    assert len(response.content) < len(body.encode()) + 10000
    base = "/api/research/runs/projection-run/evidence"
    assert client.get(base, params={"source_id": "portfolio:old"}).json() == receipts[0]
    assert client.get(base, params={"source_id": "instruments:old"}).json()["result"]["retained_snapshot"] == context["instrument_inputs"]
    dossier = client.get(base, params={"source_id": "dossier:old"}).json()["result"]
    assert dossier["retained_source"]["body"] == "Requested original" and "Unread original" not in json.dumps(dossier)
    actual = client.get(base, params={"source_id": "comparison:new"}).json()["result"]["retained_snapshot"][0]
    assert all(actual[key] == value for key, value in source.items())
    response = client.get(base, params={"source_id": "computed:python"})
    assert response.status_code == 200, response.text
    assert response.json()["tool"] == "quant"
    actual_quant = response.json()["result"]["retained_snapshot"][0]
    assert all(actual_quant[key] == value for key, value in quant.items())
    assert actual_quant["retained_reference"]["run_id"] == "projection-run"
    assert '"residual":-0.0' in response.text
    restored = client.get(base, params={"source_id": "dossier:computed"}).json()["result"]["retained_source"]
    assert all(restored[key] == value for key, value in source.items())
    assert client.get(base, params={"source_id": "outside"}).status_code == 404
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "projection-run").context_json == context
    for source_id in ("comparison:new", "computed:python"):
        assert as_user(client, "bob").get(base, params={"source_id": source_id}).status_code == 404
    principals["wrong-run"] = replace(principals["alice"], resource_scope={"kind": "run", "id": "another-run", "attempt": "1"})
    for source_id in ("comparison:new", "computed:python"):
        assert as_user(client, "wrong-run").get(base, params={"source_id": source_id}).status_code == 403
    principals["run"] = replace(principals["alice"], resource_scope={"kind": "run", "id": "projection-run", "attempt": "1"})
    for source_id in ("comparison:new", "computed:python"):
        assert as_user(client, "run").get(base, params={"source_id": source_id}).status_code == 200
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "projection-run")
        run.context_json = {**run.context_json, "execution": {"attempt": 2}}
        session.commit()
    for source_id in ("comparison:new", "computed:python"):
        assert client.get(base, params={"source_id": source_id}).status_code == 409


def test_quant_receipt_rechecks_portfolio_access_and_cannot_select_another_run(accounts):
    client, principals, grants = accounts
    as_user(client, "alice")
    topic_id = create_topic(client, portfolio_id="A", instrument_ids=["fund-us-agg"])
    with get_session_factory()() as session:
        for run_id, source_id in (("portfolio-quant-run", "computed:owned"), ("other-quant-run", "computed:elsewhere")):
            run = ResearchEntry(entry_id=run_id, topic_id=topic_id, title="Private calculation", kind="analysis", status="running",
                context_json={"research_actor": principals["alice"].to_dict(), "portfolio_id": "A", "execution": {"attempt": 1}})
            session.add(run)
            session.flush()
            with principal_context(replace(principals["alice"], resource_scope={"kind": "run", "id": run_id, "attempt": "1"})):
                retain_computed_source(session, run, {"source_id": source_id, "source_type": "computed_metric",
                    "data": {"analysis_kind": "python_quant", "metrics": {"value": 12}}})
        session.commit()
    path = "/api/research/runs/portfolio-quant-run/evidence"
    assert client.get(path, params={"source_id": "computed:owned"}).status_code == 200
    assert client.get(path, params={"source_id": "computed:elsewhere"}).status_code == 404
    grants["alice"].clear()
    assert client.get(path, params={"source_id": "computed:owned"}).status_code == 404
    assert client.get(f"/api/research/topics/{topic_id}").status_code == 404
