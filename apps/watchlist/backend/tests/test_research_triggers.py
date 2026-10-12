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


@pytest.fixture(autouse=True)
def no_registered_materials(monkeypatch):
    monkeypatch.setattr("watchlist_app.services.research_dossier.material_index", lambda *args, **kwargs: [])


def capture(store, body, *, observed=1, received=1, url="https://example.com/gold"):
    return store.capture_public_source({
        "source_id": "fixture", "url": url, "title": "Gold market disclosure", "text": body,
        "retrieved_at": stamp(observed).isoformat(), "published_at": "2020-01-01",
    }, received_at=stamp(received))


def session(cases=(), calendar="XNYS"):
    return SimpleNamespace(scalars=lambda statement: (row for row in cases),
        execute=lambda statement: ((row, len(row.history_json)) for row in cases if row.signal.startswith("sector:")) if statement.column_descriptions[0].get("entity") is triggers.RiskCase else (row for row in ()),
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
        get=lambda model, iid: SimpleNamespace(instrument_type="etf", source_settings_json={"market_calendar": calendar}, exchange_code=calendar) if model.__name__ == "Instrument" else None)


def context(**extra):
    return {"cutoff": stamp(2).isoformat(), "instrument_ids": ["gold"], "market_queries": [
        {"instrument_id": "gold", "query": "Gold", "entities": [], "published_after": "2026-09-01T00:00:00Z"},
    ], **extra}


def bound_dossier(instrument_id, sources):
    return {"instrument_id": instrument_id, "prior_sources": [], "notebook": {
        "investment_view": {"source_ids": [row["source_id"] for row in sources]}, "sources": sources}}


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
    assert triggers.research_trigger(session(), "gold", followup, now=stamp(5))["reasons"] == []
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
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(3, 20))["reasons"] == []
    # An attempted followup consumes the scanned arrivals, even if its model fails before searching.
    followup = context(cutoff=stamp(3, 20).isoformat(), market_queries=[], incremental_trigger=result)
    assert triggers.research_trigger(session(), "gold", followup, now=stamp(3, 21))["reasons"] == []


def test_same_body_recapture_is_quiet_but_changed_original_triggers(store):
    first = capture(store, "Gold reserve disclosure")
    repeated = capture(store, "Gold reserve disclosure", observed=3, received=3)
    assert repeated["source_id"] == first["source_id"]
    assert triggers.research_trigger(session(), "gold", context(), now=stamp(4))["reasons"] == []
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


@pytest.mark.parametrize("status", ["active", "withdrawn"])
def test_read_original_revision_triggers_even_after_old_terms_and_entities_disappear(store, status):
    from hashlib import sha256
    first = capture(store, "Gold reserve disclosure")
    with store.engine.begin() as connection:
        revised = {key: value for key, value in first.items() if key not in {"source_id", "received_at", "content_text", "raw_path"}}
        revised.update(version_id="corrected", title="Correction notice", status=status,
                       body_sha256=sha256(b"The earlier disclosure is invalid.").hexdigest(),
                       observed_at=stamp(3).isoformat(), entities=[], event_ids=[])
        store._write_document(connection, revised, "The earlier disclosure is invalid.", stamp(3))
    assert store.search("Gold", as_of=stamp(4), include_withdrawn=True)["total"] == 0
    prior = context(market_text_sources=[{"source_id": first["source_id"], "document_id": first["document_id"]}],
                    research_dossiers=[bound_dossier("gold", [first])])
    result = triggers.research_trigger(session(), "gold", prior, now=stamp(4))
    source, = result["reasons"][0]["sources"]
    assert source["change"] == ("withdrawn_original" if status == "withdrawn" else "revised_original")
    assert source["previous_source_id"] == first["source_id"]
    assert source["dependency"] == "previously_read_original"
    assert store.read(first["source_id"], as_of=stamp(4))["content_text"] == "Gold reserve disclosure"
    followup = {**prior, "cutoff": stamp(4).isoformat(), "incremental_trigger": result}
    assert triggers.research_trigger(session(), "gold", followup, now=stamp(5))["reasons"] == []


def test_only_current_bound_original_tracks_revision_without_query_or_scope_broadcast(store):
    first = capture(store, "Gold reserve disclosure")
    capture(store, "Gold revised reserve disclosure", observed=3, received=3)
    prior = context(market_queries=[], market_text_sources=[{"source_id": first["source_id"]}])
    # An earlier fetch alone does not create a standing automatic monitor.
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4))["reasons"] == []
    prior["research_dossiers"] = [bound_dossier("gold", [first])]
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4))["reasons"][0]["sources"]
    prior["instrument_ids"] = ["gold", "xlk"]
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4))["reasons"][0]["sources"]
    assert triggers.research_trigger(session(), "xlk", prior, now=stamp(4))["reasons"] == []
    prior["research_dossiers"] = []
    prior["reviews"] = {"gold": {"reflection": {"source_ids": [first["source_id"]]}}}
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4))["reasons"] == []


def test_unrelated_later_fetch_cutoff_does_not_consume_an_unread_correction(store):
    first = capture(store, "Gold reserve disclosure")
    latest = capture(store, "Gold revised reserve disclosure", observed=3, received=3)
    prior = context(cutoff=stamp(4).isoformat(), input_snapshot_cutoff=stamp(2).isoformat(),
                    market_text_sources=[{"source_id": first["source_id"]}], research_dossiers=[bound_dossier("gold", [first])])
    result = triggers.research_trigger(session(), "gold", prior, now=stamp(5))
    assert result["reasons"][0]["sources"][0]["previous_source_id"] == first["source_id"]
    prior["market_text_sources"].append({"source_id": latest["source_id"]})
    # Reading a new version somewhere does not establish that every current
    # theme/event still depending on the old version has been reassessed.
    changes = triggers.research_trigger(session(), "gold", prior, now=stamp(5))["reasons"][0]["sources"]
    assert changes[0]["previous_source_ids"] == [first["source_id"]]
    followup = {**prior, "incremental_trigger": result}
    assert triggers.research_trigger(session(), "gold", followup, now=stamp(5))["reasons"] == []


def test_correction_preserves_all_old_dependencies_when_another_theme_already_uses_latest(store):
    first = capture(store, "Gold original reserve disclosure")
    second = capture(store, "Gold corrected reserve disclosure", observed=2, received=2)
    latest = capture(store, "Gold final reserve disclosure", observed=3, received=3)
    changed = triggers._revised_originals(store,
        {row["source_id"]: row for row in (first, second, latest)}, None, stamp(4))
    assert len(changed) == 1 and changed[0]["source_id"] == latest["source_id"]
    assert changed[0]["previous_source_ids"] == sorted([first["source_id"], second["source_id"]])
    from watchlist_app.services.research_working_set import build_working_set
    working_set = build_working_set({"instrument_id": "gold", "themes": [
        {"theme_id": "a", "source_version_id": "a:1", "status": "active", "priority": "important", "source_ids": [first["source_id"]]},
        {"theme_id": "b", "source_version_id": "b:1", "status": "active", "priority": "important", "source_ids": [second["source_id"]]},
        {"theme_id": "c", "source_version_id": "c:1", "status": "active", "priority": "important", "source_ids": [latest["source_id"]]},
    ], "notebook": {}}, [], {"cutoff": stamp(4).isoformat(), "scheduled": True, "last_successful_review_cutoff": stamp(2).isoformat(),
        "incremental_trigger": {"gold": {"reasons": [{"kind": "new_research_sources", "sources": changed}]}}})
    assert set(working_set["affected_theme_ids"]) == {"a", "b"}


@pytest.mark.parametrize("correction", [{"title": "Correction notice"}, {"published_at": "2019-01-01"},
                                       {"information_type": "rumor"}])
def test_original_metadata_correction_is_not_mistaken_for_identical_body_recapture(store, correction):
    first = capture(store, "Gold reserve disclosure")
    with store.engine.begin() as connection:
        revised = {key: value for key, value in first.items() if key not in {"source_id", "received_at", "content_text", "raw_path"}}
        revised.update(version_id="metadata-correction", observed_at=stamp(3).isoformat(),
                       entities=[], event_ids=[], **correction)
        store._write_document(connection, revised, first["content_text"], stamp(3))
    prior = context(market_text_sources=[{"source_id": first["source_id"]}])
    result = triggers.research_trigger(session(), "gold", prior, now=stamp(4))
    assert result["reasons"][0]["sources"][0]["change"] == "revised_original"


def test_unscoped_batch_and_other_instrument_queries_are_not_broadcast(store):
    capture(store, "Gold reserve disclosure", observed=3, received=3)
    prior = context(instrument_ids=["gold", "xlk"], market_queries=[
        {"query": "Gold", "entities": []}, {"instrument_id": "xlk", "query": "Gold", "entities": []},
        {"instrument_id": "gold", "query": "", "entities": []},
    ])
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4))["reasons"] == []
    prior["market_queries"].append({"instrument_id": "gold", "query": "Gold", "entities": []})
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4))["reasons"]


@pytest.mark.parametrize("private_scope", ["personal", "portfolio", "historical_portfolio", "other_team"])
def test_automatic_monitoring_never_inherits_private_or_portfolio_query_scope(client, private_scope):
    from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
    from watchlist_app.db.session import get_session_factory

    with get_session_factory()() as database:
        shared = ResearchTopic(topic_id="shared-monitor", title="Shared research", instrument_ids=["gold"], visibility="team")
        excluded = ResearchTopic(topic_id="private-monitor", title="Private research", instrument_ids=["gold"],
            visibility="private" if private_scope == "personal" else "team",
            portfolio_id="p-private" if private_scope == "portfolio" else None,
            team_id="other" if private_scope == "other_team" else "default")
        database.add_all([shared, excluded])
        database.flush()
        public_context = context(sector_run=True, research_actor={"team_id": "default"})
        secret_context = context(research_run=True, market_queries=[{
            "instrument_id": "gold", "query": "Private position and investment discussion", "entities": [],
        }])
        database.add_all([
            ResearchEntry(entry_id="public-query", topic_id=shared.topic_id, title="Public query", kind="analysis",
                created_at=stamp(1), context_json=public_context),
            ResearchEntry(entry_id="private-query", topic_id=excluded.topic_id, title="Private query", kind="analysis",
                created_at=stamp(3), context_json=secret_context, team_id=excluded.team_id),
        ])
        if private_scope == "historical_portfolio":
            database.add(ResearchEntry(entry_id="old-portfolio-query", topic_id=excluded.topic_id, title="Old portfolio",
                kind="analysis", created_at=stamp(1), context_json={"portfolio_id": "p-private"}))
        database.commit()
        current = context(market_queries=[], research_actor={"team_id": "default"})
        actual = triggers._monitoring_context(database, "gold", current)
        assert actual == public_context
        assert triggers._queries(actual, "gold")[0]["query"] == "Gold"


def test_future_import_and_ai_summary_are_not_new_original_evidence(store):
    capture(store, "Gold future disclosure", observed=3, received=5)
    assert triggers.research_trigger(session(), "gold", context(), now=stamp(4))["reasons"] == []
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
    assert triggers.research_trigger(session([case]), "gold", context(market_queries=[]), now=stamp(4))["reasons"] == []
    case.history_json.append({"at": stamp(3).isoformat(), "action": "updated", "detail": "Week loss rule also breached"})
    result = triggers.research_trigger(session([case]), "gold", context(market_queries=[]), now=stamp(4))
    assert result["reasons"][0]["kind"] == "quantitative_risk_change"
    assert result["reasons"][0]["cases"][0]["case_id"] == "risk1"
    followup = context(cutoff=stamp(4).isoformat(), market_queries=[], incremental_trigger={"gold": result})
    assert triggers.research_trigger(session([case]), "gold", followup, now=stamp(5))["reasons"] == []


def test_explicit_schedule_crossing_is_due_not_predicted_event_confirmation(store):
    prior = context(cutoff=stamp(3, 12).isoformat(), market_queries=[], research_dossiers=[{
        "instrument_id": "gold", "notebook": {"catalysts": [{"key": "statement", "title": "Statement", "status": "scheduled",
            "scheduled_at": "2026-09-03T10:00:00-04:00"}], "forecasts": [{"key": "inflation", "claim": "Inflation slows", "horizon": "later", "review_on": "2026-09-04"}]},
    }])
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(3, 13))["reasons"] == []
    result = triggers.research_trigger(session(), "gold", prior, now=stamp(3, 15))
    assert result["reasons"][0]["items"][0]["kind"] == "scheduled_event_check"
    assert "不代表事件已经发生" in result["reasons"][0]["items"][0]["note"]
    followup = {**prior, "cutoff": stamp(3, 15).isoformat(), "incremental_trigger": result}
    # The cheap scan keeps this due item until lifecycle resolution; the durable
    # monitor, not an attempted timestamp in a run, suppresses duplicate model work.
    assert triggers.research_trigger(session(), "gold", followup, now=stamp(3, 20))["reasons"][0]["items"] == result["reasons"][0]["items"]
    next_day = triggers.research_trigger(session(), "gold", followup, now=stamp(4, 12))
    assert next_day["reasons"][0]["items"][0]["kind"] == "forecast_review"


def test_natural_language_horizon_is_not_parsed_into_an_invented_deadline(store):
    prior = context(market_queries=[], research_dossiers=[{"instrument_id": "gold", "notebook": {
        "forecasts": [{"key": "view", "claim": "Gold higher", "horizon": "a few weeks", "status": "active"}],
    }}])
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(7))["reasons"] == []


@pytest.mark.parametrize("theme_status", ["paused", "closed"])
def test_inactive_theme_forecast_does_not_restart_its_research_when_due(store, theme_status):
    forecast = {"key": "theme-forecast", "theme_id": "theme", "claim": "仍需观察", "status": "active",
        "review_on": "2026-09-03T10:00:00-04:00"}
    dossier = {"instrument_id": "gold", "themes": [{"theme_id": "theme", "status": theme_status}],
        "notebook": {"forecasts": [forecast]}}
    prior = context(cutoff=stamp(3, 12).isoformat(), market_queries=[], research_dossiers=[dossier])
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(3, 15))["reasons"] == []

    # The shared instrument can still require unrelated research at the same time.
    dossier["notebook"]["forecasts"].append({**forecast, "key": "global-forecast", "theme_id": None})
    result = triggers.research_trigger(session(), "gold", prior, now=stamp(3, 15))
    assert [item["key"] for item in result["reasons"][0]["items"]] == ["global-forecast"]

    dossier["themes"][0]["status"] = "active"
    result = triggers.research_trigger(session(), "gold", prior, now=stamp(3, 15))
    assert {item["key"] for item in result["reasons"][0]["items"]} == {"theme-forecast", "global-forecast"}


def test_fund_observation_date_uses_the_same_nav_check_clock_as_daily_research(store):
    fund_session = session()
    fund_session.get = lambda model, iid: SimpleNamespace(instrument_type="private_fund", source_settings_json={}, exchange_code=None) if model.__name__ == "Instrument" else None
    prior = {"cutoff": "2026-09-02T15:00:00+00:00", "instrument_ids": ["private-fund"],
        "research_dossiers": [{"instrument_id": "private-fund", "notebook": {"forecasts": [
            {"key": "review", "claim": "复核月度净值", "review_on": "2026-09-03", "status": "active"}]}}]}
    result = triggers.research_trigger(fund_session, "private-fund", prior, now=datetime(2026, 9, 2, 17, tzinfo=UTC))
    assert result["reasons"][0]["items"][0]["kind"] == "forecast_review"
    assert "不代表事件已经发生" in result["reasons"][0]["items"][0]["note"]


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
            currency="USD", exchange_code="XNYS", quote_selection_policy_json={"total_return": ["adjusted_close"]}, source_settings_json={"market_calendar": "XNYS"},
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
        # A failed attempt retains pending work but is not scheduled forever.
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
        assert {item["source_id"] for item in sources} == {source["source_id"], new_source["source_id"]}
        assert following.context_json["incremental_trigger"]["trigger-gold"]["coverage_cursor"]["market_queries"][0]["cutoff"] == stamp(3, 12).isoformat()


def test_theme_scan_retains_pending_baseline_until_current_lifecycle_changes(store):
    theme = {"theme_id": "theme-credit", "title": "信用传导", "status": "active", "baseline_status": "pending",
             "created_at": stamp(1).isoformat(), "baseline_requested_at": stamp(1).isoformat()}
    prior = context(research_dossiers=[{"instrument_id": "gold", "themes": [theme]}], attempted_theme_baselines={})
    result = triggers.research_trigger(session(), "gold", prior, now=stamp(4))
    assert result["reasons"][0]["kind"] == "theme_baseline"
    prior["attempted_theme_baselines"] = {theme["theme_id"]: theme["baseline_requested_at"]}
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4))["reasons"] == result["reasons"]
    theme["baseline_requested_at"] = stamp(3).isoformat()
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4))["reasons"][0]["kind"] == "theme_baseline"
    theme["status"] = "paused"
    assert triggers.research_trigger(session(), "gold", prior, now=stamp(4))["reasons"] == []


@pytest.mark.parametrize("dependency", ["unreferenced", "notebook", "important_theme", "watched_event", "unresolved_risk"])
@pytest.mark.parametrize("metadata_present", [True, False])
def test_monitor_source_catalog_is_not_a_dependency_but_current_references_are(store, monkeypatch, dependency, metadata_present):
    """A different product may be valid comparison evidence, but merely retained is not watched."""
    from watchlist_app.services import research_dossier, research_themes, research_working_set
    old = capture(store, "Peer fund prior facts", url="https://example.com/other-fund")
    new = capture(store, "Peer fund corrected facts", observed=3, received=3, url="https://example.com/other-fund")
    catalogue = [old] if metadata_present else []
    notebook = {"version_id": "current", "sources": catalogue, "investment_view": {"source_ids": []}}
    themes, events = [], []
    if dependency == "notebook":
        notebook["investment_view"]["source_ids"] = [old["source_id"]]
    elif dependency == "important_theme":
        themes = [{"theme_id": "important", "status": "active", "priority": "important", "source_ids": [old["source_id"]], "sources": catalogue}]
    elif dependency in {"watched_event", "unresolved_risk"}:
        evidence = {"source_ids": [old["source_id"]], "sources": catalogue}
        if dependency == "unresolved_risk":
            evidence = {"source_ids": [], "sources": [], "last_assessed_snapshot": {
                "source_ids": [old["source_id"]], "sources": catalogue}}
        events = [{"case_id": "event", "follow_up": "watch" if dependency == "watched_event" else "none",
                   "trigger_active": dependency == "unresolved_risk", "evidence": evidence}]
    monkeypatch.setattr(research_dossier, "_notebooks", lambda *args, **kwargs: (notebook, []))
    monkeypatch.setattr(research_themes, "theme_summaries", lambda *args, **kwargs: {"themes": themes})
    monkeypatch.setattr(research_working_set, "current_events", lambda *args: events)
    dossier = triggers.trigger_dossier(None, "private-fund")
    originals = triggers._read_originals({"research_dossiers": [dossier]}, "private-fund")
    revisions = triggers._revised_originals(store, originals, None, stamp(4))
    if dependency == "unreferenced":
        assert dossier["notebook"]["sources"] == []
        assert originals == {} and revisions == []
    else:
        assert old["source_id"] in originals
        assert [(r["source_id"], r["previous_source_ids"]) for r in revisions] == [(new["source_id"], [old["source_id"]])]
    # Filtering a monitor projection must not mutate the retained notebook.
    assert notebook["sources"] == catalogue


def test_retained_notebook_directory_does_not_reintroduce_unreferenced_originals(store):
    old = capture(store, "Unreferenced peer fund fact", url="https://example.com/unreferenced")
    current = capture(store, "Current comparison", url="https://example.com/comparison")
    event = capture(store, "Unresolved risk original", url="https://example.com/risk")
    dossier = {"instrument_id": "gold", "notebook": {"sources": [old, current],
        "investment_view": {"source_ids": [current["source_id"]]}}, "prior_sources": [event]}
    assert set(triggers._read_originals({"research_dossiers": [dossier]}, "gold")) == {current["source_id"], event["source_id"]}
