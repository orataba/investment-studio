"""Provider field meaning survives the frozen instrument and Python boundaries."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

from studio_data.services import instrument_reference
from studio_identity import current_principal

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_quant, research_tool_projection, shared_instrument_registry
from watchlist_app.services.research_run_evidence import quant_source_catalogue, selected_quant_sources


def test_collected_holdings_contract_reaches_frozen_reads_and_exact_quant_input(client, monkeypatch):
    iid, run_id, cutoff = "000940-of", "reference-contract-run", "2026-10-14T00:00:00+00:00"
    instrument = {"instrument_id": iid, "instrument_type": "public_fund",
        "source_settings": {"source_api_profile": "tushare"},
        "identifiers": [{"identifier_type": "provider_symbol", "identifier_value": "tushare:000940.OF"}]}
    holdings = [{"ts_code": "000940.OF", "symbol": "600001.SH", "mkv": "123456.78",
        "amount": "1000", "stk_mkv_ratio": "5.20", "ann_date": "20260831", "end_date": "20260630"}]
    monkeypatch.setattr(instrument_reference, "get_instrument", lambda _: instrument)
    monkeypatch.setattr(instrument_reference, "_datahub_rows", lambda **kwargs:
        deepcopy(holdings) if kwargs["api_name"] == "fund_portfolio" else [{"name": "Fund"}])
    collected = instrument_reference.get_instrument_reference_data(iid)
    original = deepcopy(collected)
    monkeypatch.setattr(shared_instrument_registry, "get_shared_instrument", lambda _: instrument)
    monkeypatch.setattr(shared_instrument_registry, "get_session_factory", lambda: lambda: nullcontext(
        SimpleNamespace(scalar=lambda _: SimpleNamespace(value_json=collected))))
    reference = shared_instrument_registry.get_shared_reference_data(iid, as_of=datetime(2026, 10, 14, tzinfo=UTC))
    assert reference == original
    sid = f"instrument:{run_id}:{iid}"
    asset = {"instrument_id": iid, "name": "Fund", "instrument_type": "public_fund",
        "source_id": sid, "snapshot_cutoff": cutoff, "reference_data": reference}
    context = {"run_id": run_id, "cutoff": cutoff, "instrument_ids": [iid],
        "catalogue": [{"instrument_id": iid}], "instrument_inputs": [asset],
        "research_dossiers": [{"instrument_id": iid}]}
    contract = original["source"]["field_semantics"]["holdings"]
    overview = research_tool_projection.read_research_instrument(lambda _: context, iid)
    page = research_tool_projection.read_research_instrument(lambda _: context, iid, section="holdings")
    assert overview["instrument_inputs"][0]["reference_data"]["source"]["field_semantics"]["holdings"] == contract
    assert page["reference_data"]["source"]["field_semantics"]["holdings"] == contract
    assert page["reference_data"]["data"] == holdings
    assert page["reference_data"]["fetched_at"] == original["fetched_at"]

    actor = current_principal()
    with get_session_factory()() as session:
        topic = ResearchTopic(topic_id="reference-contract-topic", title="Contract test", team_id=actor.team_id,
            visibility="private", created_by_user_id=actor.user_id)
        session.add(topic)
        session.flush()
        run = ResearchEntry(entry_id=run_id, topic_id=topic.topic_id, team_id=actor.team_id,
            title="Contract test", kind="analysis", status="running", context_json=deepcopy(context))
        session.add(run)
        session.flush()
        assert sid in {source["source_id"] for source in quant_source_catalogue(session, run, iid)}
        selected, bound_cutoff = selected_quant_sources(session, run, iid, [sid])
        received = []

        def execute(_code, inputs, _params):
            received.append(deepcopy(inputs))
            return {"summary": "Contract retained"}, {"available": True, "backend": "test"}

        monkeypatch.setattr(research_quant.quant_sandbox, "execute", execute)
        evidence = research_quant.execute_analysis(run, research_quant.QuantAnalysisInput(
            instrument_id=iid, title="Contract test", source_ids=[sid], code="result = {}",
            methodology="Check exact retained field units and dates."), selected_sources=selected, input_cutoff=bound_cutoff)
        assert received[0][sid]["snapshot"]["reference_data"] == original
        assert evidence["methodology"]["input_sources"][0]["snapshot"]["reference_data"] == original
        assert collected == original
        session.rollback()
