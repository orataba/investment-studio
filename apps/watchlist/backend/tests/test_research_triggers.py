from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from studio_market.text import TextStore
from studio_market.text.schema import metadata

from watchlist_app.services import research_triggers as triggers


def stamp(day, hour=12):
    return datetime(2026, 9, day, hour, tzinfo=UTC)


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = TextStore(database_url="sqlite://", data_root=tmp_path)
    metadata.create_all(value.engine)
    monkeypatch.setattr(triggers, "text_store", lambda: value)
    yield value
    value.close()


def capture(store, body, *, observed=1, received=1, url="https://example.com/gold"):
    return store.capture_public_source({
        "source_id": "fixture", "url": url, "title": "Gold market disclosure", "text": body,
        "retrieved_at": stamp(observed).isoformat(), "published_at": "2020-01-01",
    }, received_at=stamp(received))


def session(cases=(), calendar="XNYS"):
    return SimpleNamespace(scalars=lambda statement: cases,
        get=lambda model, iid: SimpleNamespace(source_settings_json={"market_calendar": calendar}, exchange_code=calendar))


def context(**extra):
    return {"cutoff": stamp(2).isoformat(), "instrument_ids": ["gold"], "market_queries": [
        {"instrument_id": "gold", "query": "Gold", "entities": [], "published_after": "2026-09-01T00:00:00Z"},
    ], **extra}


def test_late_import_old_publication_triggers_once_and_preserves_monitor_scope(store):
    document = capture(store, "Gold reserve disclosure", observed=1, received=3)
    prior = context()
    result = triggers.research_trigger(session(), "gold", prior, now=stamp(4))
    sources = result["reasons"][0]["sources"]
    assert sources[0]["source_id"] == document["source_id"]
    assert sources[0]["published_at"] == "2020-01-01"
    assert sources[0]["change"] == "new_original"
    assert result["coverage_cursor"]["market_queries"][0]["query"] == "Gold"
    # Failed/no-search followup still consumes the already-attempted source batch.
    followup = {"cutoff": stamp(4).isoformat(), "instrument_ids": ["gold"], "market_queries": [], "incremental_trigger": result}
    assert triggers.research_trigger(session(), "gold", followup, now=stamp(5)) is None
    capture(store, "Gold subsequent disclosure", observed=5, received=5, url="https://example.com/gold-2")
    assert triggers.research_trigger(session(), "gold", followup, now=stamp(6))["reasons"][0]["kind"] == "new_research_sources"


def test_query_coverage_clock_is_not_advanced_by_later_unrelated_web_fetch(store):
    source = store.capture_public_source({"source_id": "fixture", "url": "https://example.com/gold-update",
        "title": "Gold reserve update", "text": "Gold reserve purchase increased", "published_at": "2026-09-03",
        "retrieved_at": stamp(3, 14).isoformat()}, received_at=stamp(3, 14))
    prior = context(cutoff=stamp(3, 18).isoformat(), input_snapshot_cutoff=stamp(3, 10).isoformat(), market_queries=[{
        "instrument_id": "gold", "query": "Gold", "entities": [], "cutoff": stamp(3, 12).isoformat(),
    }])
    result = triggers.research_trigger(session(), "gold", prior, now=stamp(3, 20))
    assert result["reasons"][0]["sources"][0]["source_id"] == source["source_id"]
    assert result["coverage_cursor"]["market_queries"][0]["cutoff"] == stamp(3, 12).isoformat()
    # If this exact version was subsequently read, it is already part of the run evidence.
    prior["market_text_sources"] = [{"source_id": source["source_id"]}]
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(3, 20)) is None
    # An attempted followup consumes the scanned arrivals, even if its model fails before searching.
    followup = context(cutoff=stamp(3, 20).isoformat(), market_queries=[], incremental_trigger=result)
    assert triggers.research_trigger(session(), "gold", followup, now=stamp(3, 21)) is None


def test_same_body_recapture_is_quiet_but_changed_original_triggers(store):
    first = capture(store, "Gold reserve disclosure")
    repeated = capture(store, "Gold reserve disclosure", observed=3, received=3)
    assert repeated["source_id"] == first["source_id"]
    assert triggers.research_trigger(session(), "gold", context(), now=stamp(4)) is None
    amended = capture(store, "Gold reserve disclosure with revised amount", observed=4, received=4)
    result = triggers.research_trigger(session(), "gold", context(), now=stamp(5))
    assert result["reasons"][0]["sources"][0]["source_id"] == amended["source_id"]
    assert result["reasons"][0]["sources"][0]["change"] == "revised_original"


def test_withdrawal_of_a_previously_available_source_requires_reassessment(store):
    prior_source = capture(store, "Gold reserve disclosure")
    with store.engine.begin() as connection:
        record = {key: value for key, value in prior_source.items() if key not in {"source_id", "received_at", "content_text", "raw_path"}}
        record.update(version_id="withdrawal", status="withdrawn", observed_at=stamp(3).isoformat(), entities=[], event_ids=[])
        store._write_document(connection, record, "Gold reserve disclosure", stamp(3))
    result = triggers.research_trigger(session(), "gold", context(), now=stamp(4))
    assert result["reasons"][0]["sources"][0]["change"] == "withdrawn_original"
    assert result["reasons"][0]["sources"][0]["document_id"] == prior_source["document_id"]


def test_unscoped_batch_and_other_instrument_queries_are_not_broadcast(store):
    capture(store, "Gold reserve disclosure", observed=3, received=3)
    prior = context(instrument_ids=["gold", "xlk"], market_queries=[
        {"query": "Gold", "entities": []}, {"instrument_id": "xlk", "query": "Gold", "entities": []},
        {"instrument_id": "gold", "query": "", "entities": []},
    ])
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4)) is None
    prior["market_queries"].append({"instrument_id": "gold", "query": "Gold", "entities": []})
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4)) is not None


def test_future_import_and_ai_summary_are_not_new_original_evidence(store):
    capture(store, "Gold future disclosure", observed=3, received=5)
    assert triggers.research_trigger(session(), "gold", context(), now=stamp(4)) is None
    assert not triggers._readable({"content_completeness": "full_text", "content_text": "Gold AI summary", "information_type": "ai_summary"})
    assert not triggers._readable({"content_completeness": "title_only", "content_text": "Gold title"})


def test_source_check_failure_is_not_reported_as_no_change_or_consumed(store, monkeypatch):
    prior = context()

    def unavailable(*args, **kwargs):
        raise OSError("Source store unavailable")

    monkeypatch.setattr(store, "search", unavailable)
    with pytest.raises(OSError, match="Source store unavailable"):
        triggers.research_trigger(session(), "gold", prior, now=stamp(4))
    assert "incremental_trigger" not in prior
    assert prior["cutoff"] == stamp(2).isoformat()


def test_numeric_daily_changes_do_not_trigger_without_domain_history_event(store):
    case = SimpleNamespace(case_id="risk1", signal="period_loss", title="Loss limit", observed_on=date(2026, 9, 3),
        evidence_json={"periods": [{"period": "day", "return_pct": -4, "limit_pct": 3}]},
        history_json=[{"at": stamp(1).isoformat(), "action": "triggered", "detail": "Prior known risk"}])
    assert triggers.research_trigger(session([case]), "gold", context(market_queries=[]), now=stamp(4)) is None
    case.history_json.append({"at": stamp(3).isoformat(), "action": "updated", "detail": "Week loss rule also breached"})
    result = triggers.research_trigger(session([case]), "gold", context(market_queries=[]), now=stamp(4))
    assert result["reasons"][0]["kind"] == "quantitative_risk_change"
    assert result["reasons"][0]["cases"][0]["case_id"] == "risk1"
    followup = context(cutoff=stamp(4).isoformat(), market_queries=[], incremental_trigger={"gold": result})
    assert triggers.research_trigger(session([case]), "gold", followup, now=stamp(5)) is None


def test_explicit_schedule_crossing_is_due_not_predicted_event_confirmation(store):
    prior = context(cutoff=stamp(3, 12).isoformat(), market_queries=[], research_dossiers=[{
        "instrument_id": "gold", "notebook": {"catalysts": [{"key": "statement", "title": "Statement", "status": "scheduled",
            "scheduled_at": "2026-09-03T10:00:00-04:00"}], "forecasts": [{"key": "inflation", "claim": "Inflation slows", "horizon": "later", "review_on": "2026-09-04"}]},
    }])
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(3, 13)) is None
    result = triggers.research_trigger(session(), "gold", prior, now=stamp(3, 15))
    assert result["reasons"][0]["items"][0]["kind"] == "scheduled_event_check"
    assert "不代表事件已经发生" in result["reasons"][0]["items"][0]["note"]
    followup = {**prior, "cutoff": stamp(3, 15).isoformat(), "incremental_trigger": result}
    assert triggers.research_trigger(session(), "gold", followup, now=stamp(3, 20)) is None
    next_day = triggers.research_trigger(session(), "gold", followup, now=stamp(4, 12))
    assert next_day["reasons"][0]["items"][0]["kind"] == "forecast_review"


def test_natural_language_horizon_is_not_parsed_into_an_invented_deadline(store):
    prior = context(market_queries=[], research_dossiers=[{"instrument_id": "gold", "notebook": {
        "forecasts": [{"key": "view", "claim": "Gold higher", "horizon": "a few weeks", "status": "active"}],
    }}])
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(7)) is None


def test_scheduled_run_reopens_for_same_day_source_and_does_not_retry_consumed_trigger(client, store, monkeypatch):
    from investment_studio_instrument_core.db_models import Instrument
    from watchlist_app.db.models import InstrumentDetail
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services import sector_research as service

    class Clock:
        current = stamp(3, 12)

        @classmethod
        def now(cls, tz):
            return cls.current.astimezone(tz)

        fromisoformat = datetime.fromisoformat

    monkeypatch.setattr(service, "datetime", Clock)
    with get_session_factory()() as database:
        database.add(Instrument(instrument_id="trigger-gold", instrument_name="Gold ETF", instrument_type="etf",
            currency="USD", exchange_code="XNYS", quote_selection_policy_json={}, source_settings_json={"market_calendar": "XNYS"},
            lifecycle_state_json={"status": "active"}))
        database.add(InstrumentDetail(instrument_id="trigger-gold", instrument_name="Gold ETF", instrument_type="etf",
            detail_view_type="etf", metadata_json={}))
        database.commit()
        first, created = service.begin_run(database, ["trigger-gold"], scheduled=True)
        assert created
        first.status = "completed"
        first.created_at = Clock.current
        first.context_json = {**first.context_json, "market_queries": [{"instrument_id": "trigger-gold", "query": "Gold",
            "entities": [], "cutoff": Clock.current.isoformat()}]}
        database.commit()
        source = store.capture_public_source({"source_id": "fixture", "url": "https://example.com/gold-intraday",
            "title": "Gold intraday update", "text": "Gold new reserve disclosure", "published_at": "2026-09-03",
            "retrieved_at": stamp(3, 14).isoformat()}, received_at=stamp(3, 14))
        Clock.current = stamp(3, 15)
        second, created = service.begin_run(database, ["trigger-gold"], scheduled=True)
        assert created and second.entry_id != first.entry_id
        saved = second.context_json["incremental_trigger"]["trigger-gold"]
        assert saved["reasons"][0]["sources"][0]["source_id"] == source["source_id"]
        same, created = service.begin_run(database, ["trigger-gold"], scheduled=True)
        assert not created and same.entry_id == second.entry_id
        # A failed attempt retains the consumed cursor. It must not schedule forever.
        second.status = "failed"
        second.created_at = Clock.current
        database.commit()
        Clock.current = stamp(3, 16)
        same, created = service.begin_run(database, ["trigger-gold"], scheduled=True)
        assert not created and same.entry_id == second.entry_id
        # A later manually started task can fail before searching. It must not erase
        # the established source scope or forget the earlier consumed source cursor.
        manual, created = service.begin_run(database, ["trigger-gold"], scheduled=False)
        assert created and not manual.context_json.get("market_queries")
        manual.status = "failed"
        database.commit()
        Clock.current = stamp(3, 17)
        same, created = service.begin_run(database, ["trigger-gold"], scheduled=True)
        assert not created and same.entry_id == manual.entry_id
        new_source = store.capture_public_source({"source_id": "fixture", "url": "https://example.com/gold-later",
            "title": "Gold later update", "text": "Gold another reserve disclosure", "published_at": "2026-09-03",
            "retrieved_at": stamp(3, 17).isoformat()}, received_at=stamp(3, 17))
        Clock.current = stamp(3, 18)
        following, created = service.begin_run(database, ["trigger-gold"], scheduled=True)
        assert created and following.entry_id != manual.entry_id
        sources = following.context_json["incremental_trigger"]["trigger-gold"]["reasons"][0]["sources"]
        assert [item["source_id"] for item in sources] == [new_source["source_id"]]
        assert following.context_json["incremental_trigger"]["trigger-gold"]["coverage_cursor"]["market_queries"][0]["cutoff"] == stamp(3, 12).isoformat()
