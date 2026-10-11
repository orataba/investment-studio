"""Append computations without decoding or rewriting old multi-MiB run originals."""
import json

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from studio_identity import Principal, principal_context

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services import research_run_evidence as evidence

from .test_postgres_instrument_registry_constraints import postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


def test_selective_computation_append_and_legacy_reference_read(postgres_watchlist_env):
    cutoff, iid = "2026-10-01T00:00:00+00:00", "evidence-fixture"
    old = {"source_id": "computed:old", "source_type": "computed_metric", "instrument_id": iid,
        "as_of": cutoff, "data": {"value": 10}, "methodology": "Observed source"}
    context = {"research_run": True, "cutoff": cutoff, "instrument_ids": [iid],
        "execution": {"attempt": 1}, "research_actor": {"kind": "user", "user_id": "evidence-owner"},
        "computed_metrics": [old], "instrument_inputs": [{"instrument_id": iid, "name": "Fixture", "snapshot_cutoff": cutoff}],
        "sector_company_data": {iid: {"ACME": {"revenue": 42}}},
        "research_dossiers": [{"instrument_id": iid, "notebook": {"sources": []}}],
        "referenced_research_versions": [{"version_id": "old-version", "instrument_id": iid,
            "information_cutoff": cutoff, "sources": [{**old, "data": {"value": 3}}]}],
        "large_original": "DO_NOT_HYDRATE_FULL_RUN" * 100000 + "\x00"}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="evidence-topic", title="Evidence", visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="evidence-run", topic_id="evidence-topic", kind="analysis",
            status="running", title="Evidence", context_json=context))
        session.commit()

    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert "DO_NOT_HYDRATE_FULL_RUN" not in value
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public"})
    sql = []
    def record(_connection, _cursor, statement, *_args):
        sql.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    principal = Principal("evidence-owner", "Owner", "default", resource_scope={"kind": "run", "id": "evidence-run", "attempt": "1"})
    try:
        with principal_context(principal), Session(engine) as session:
            run = evidence.locked_computation_run(session, "evidence-run")
            selected, selected_cutoff = evidence.selected_quant_sources(session, run, iid, ["computed:old"])
            assert selected["computed:old"]["data"]["value"] == 10 and selected_cutoff == cutoff
            historical, _ = evidence.selected_quant_sources(session, run, iid, ["computed:old"], "old-version")
            ref = evidence.computed_reference(historical["computed:old"])
            assert evidence.resolve_reference(ref, session=session)["data"]["value"] == 3
            assert evidence.bound_input(session, run.entry_id, iid)["snapshot_cutoff"] == cutoff
            saved = evidence.retain_computed_source(session, run, {**old, "source_id": "computed:new", "data": {"value": 20}})
            session.commit()
            assert evidence.read_computed_source(session, run.entry_id, saved["source_id"])["data"]["value"] == 20
            catalogue = evidence.quant_source_catalogue(session, run, iid)
            assert {"computed:old", "computed:new", f"instrument:evidence-run:{iid}", f"fmp:evidence-run:{iid}:ACME"}.issubset({row["source_id"] for row in catalogue})
            assert not any("UPDATE research_entry" in statement for statement in sql)
    finally:
        engine.dispose()
    with get_session_factory()() as session:
        assert session.scalar(select(ResearchEntry.context_json).where(ResearchEntry.entry_id == "evidence-run")) == context


def test_selected_legacy_source_with_nul_is_exact(postgres_watchlist_env):
    old = {"source_id": "computed:nul", "source_type": "computed_metric", "data": {"text": "exact\x00literal\ufffd"}}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="evidence-nul", title="Evidence", visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="evidence-nul", topic_id="evidence-nul", kind="analysis", title="Exact original",
            context_json={"computed_metrics": [old], "unrelated": "retained"}))
        session.commit()
        actual = evidence.read_computed_source(session, "evidence-nul", old["source_id"])
        assert actual["data"] == old["data"]
        assert session.scalar(select(ResearchEntry.context_json).where(ResearchEntry.entry_id == "evidence-nul"))["computed_metrics"] == [old]


def test_figure_sql_projection_never_decodes_large_input_trees(postgres_watchlist_env):
    """Both storage generations select outputs before Python JSON decoding."""
    from watchlist_app.db.models.research_run_evidence import ResearchRunEvidence
    from datetime import UTC, datetime
    marker = "RAW_INPUT_MUST_STAY_IN_DATABASE"
    original = {"source_id": "computed:figure", "source_type": "computed_metric", "instrument_id": "fixture",
        "title": "Observed output", "data": {"analysis_kind": "watchlist_observations", "metrics": {"value": 5},
            "tables": [], "charts": [], "series": [{"series_id": "x", "observations": 2,
                "first": {"date": "2026-09-28", "value": 5}, "points": [marker] * 10000}],
            "observations": {"volume": {"current": {"date": "2026-09-29"}, "previous": {"date": "2026-09-28"}},
                "ewma": {"input_points": [marker] * 10000}}, "input_points": [marker] * 10000},
        "methodology": {"description": "Observed endpoints", "input_sources": [{"text": marker * 10000}]},
        "input_snapshot": {"text": marker * 10000 + "\x00"}, "input_series": [marker] * 10000}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="figure-projection", title="Figure", visibility="team"))
        session.flush()
        session.add_all([ResearchEntry(entry_id=f"figure-{kind}", topic_id="figure-projection", title="Figure",
            kind="analysis", context_json={"computed_metrics": [original] if kind == "legacy" else [],
                "unrelated": marker * 10000 + "\x00"}) for kind in ("legacy", "retained")])
        session.flush()
        session.add(ResearchRunEvidence(run_id="figure-retained", source_id=original["source_id"], attempt=1,
            source_json=original, index_json={}, recorded_at=datetime.now(UTC)))
        session.commit()

    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert marker not in value, "A figure must not decode the original input tree"
        return json.loads(value)

    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public"})
    try:
        with principal_context(Principal("figure-reader", "Reader", "default")), Session(engine) as session:
            for kind in ("legacy", "retained"):
                ref = evidence.computed_reference(original, run_id=f"figure-{kind}")
                domain = evidence.resolve_reference(ref, session=session, projection="classification")
                assert domain["observation_domain"] == "market"
                assert "metrics" not in domain["data"]
                figure = evidence.resolve_reference(ref, session=session, projection="figure")
                assert figure["data"]["metrics"] == {"value": 5}
                assert figure["data"]["series"][0]["observations"] == 2
                assert "points" not in figure["data"]["series"][0]
                assert figure["data"]["observation_clocks"]["volume"]["current_date"] == "2026-09-29"
                assert figure["methodology"] == {"description": "Observed endpoints"}
    finally:
        engine.dispose()
