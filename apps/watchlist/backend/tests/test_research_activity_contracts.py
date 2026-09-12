from datetime import UTC, datetime

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_activity import research_activity, resolve_research_update

from .test_research_activity import activity_client, event, publish


def test_historical_update_route_keeps_original_judgment_without_later_withdrawal_metadata(client):
    instrument_id = "fund-us-agg"
    original_at = "2026-09-01T00:00:00+00:00"
    later_at = "2026-09-09T00:00:00+00:00"
    original = {"title": "最初披露", "body": "当时仍需核实后续影响", "recorded_at": original_at,
        "follow_up": "watch", "next_watch": "核实后续披露", "event_version_id": "dated-event:1"}
    later = {**original, "title": "后续更正", "body": "新证据改变了判断", "recorded_at": later_at,
        "event_version_id": "dated-event:2", "follow_up": "resolved"}
    with get_session_factory()() as session:
        topic = ResearchTopic(topic_id="historical-update-read", title="研究原记录", instrument_ids=[instrument_id],
            visibility="team")
        session.add(topic)
        session.flush()
        session.add(ResearchEntry(entry_id="historical-update-run", topic_id=topic.topic_id, kind="analysis",
            title="按当时资料复核", status="completed", context_json={"research_run": True,
                "instrument_ids": [instrument_id], "cutoff": "2026-09-05T00:00:00+00:00",
                "research_dossiers": [{"instrument_id": instrument_id}]}))
        session.add(RiskCase(case_id="dated-event", instrument_id=instrument_id, signal="sector:dated-event",
            title=later["title"], body=later["body"], status="resolved", trigger_active=False,
            created_at=datetime(2026, 9, 1, tzinfo=UTC), evidence_json={**later, "withdrawn": True,
                "withdrawal_reason": "later-only-withdrawal-reason", "withdrawn_at": later_at},
            history_json=[{"at": original_at, "action": "new", "snapshot": original},
                          {"at": later_at, "action": "updated", "snapshot": later}]))
        session.commit()

    path = f"/api/research/runs/historical-update-run/dossier/{instrument_id}"
    response = client.get(path, params={"update_id": "event:dated-event:1"})
    assert response.status_code == 200, response.text
    record = response.json()["value"]
    assert record["title"] == original["title"] and record["body"] == original["body"]
    assert record["recorded_at"] == original_at
    assert record["reference"]["event_version_id"] == "dated-event:1"
    assert not {"withdrawn", "withdrawal_reason", "withdrawn_at", "superseded"}.intersection(record)
    assert "later-only-withdrawal-reason" not in response.text
    assert "新证据改变了判断" not in response.text
    assert client.get(path, params={"update_id": "event:dated-event:2"}).status_code == 404


def test_forecast_review_and_lesson_inherit_original_version_theme_after_forecast_changes(activity_client):
    client = activity_client
    themes = [client.post("/api/research/instruments/xlk/themes", json={"title": title, "question": title}).json()
              for title in ("原融资判断", "新的经营问题")]
    original_theme, later_theme = [row["theme_id"] for row in themes]
    with get_session_factory()() as session:
        publish(session, research={"forecasts": [{"key": "capacity", "theme_id": original_theme,
            "claim": "融资可能缓解产能约束", "horizon": "下一次经营披露", "source_ids": ["original"]}]})
        original = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "forecast")
        version_id = original["reference"]["forecast_version_id"]
        publish(session, research={"forecasts": [{"key": "capacity", "theme_id": later_theme,
            "claim": "新的判断转向资金使用效率", "horizon": "下一季度", "source_ids": ["original"]}]})
        publish(session, research={
            "forecast_reviews": [{"key": "capacity-review", "forecast_key": "capacity", "forecast_version_id": version_id,
                "outcome": "旧判断仍未得到充分验证", "source_ids": ["original"]}],
            "lessons": [{"key": "capacity-lesson", "forecast_key": "capacity", "forecast_version_id": version_id,
                "lesson": "融资完成不等于产能约束已经解除", "source_ids": ["original"]}],
        })
        updates = research_activity(session, "xlk")["updates"]
        reflected = [row for row in updates if row["kind"] in {"review", "lesson"}]
        assert {row["kind"] for row in reflected} == {"review", "lesson"}
        for row in reflected:
            assert row["related_update_id"] == original["update_id"]
            assert row["theme_ids"] == [original_theme]
        retained = resolve_research_update(session, "xlk", original["update_id"])
        assert retained["body"] == original["body"] and retained["theme_ids"] == [original_theme]
        assert retained["reference"]["forecast_version_id"] == version_id
    projected = {row["theme_id"]: row for row in client.get("/api/research/instruments/xlk/themes").json()["themes"]}
    expected_ids = {row["update_id"] for row in reflected}
    assert expected_ids.issubset({row["update_id"] for row in projected[original_theme]["updates"]})
    assert expected_ids.isdisjoint({row["update_id"] for row in projected[later_theme]["updates"]})


def test_review_inherits_theme_through_pm_opinion_without_retargeting_old_event(activity_client):
    client = activity_client
    themes = [client.post("/api/research/instruments/xlk/themes", json={"title": title, "question": title}).json()
              for title in ("融资条款的最初影响", "后续资金运用")]
    original_theme, later_theme = [row["theme_id"] for row in themes]
    with get_session_factory()() as session:
        publish(session, events=[event(theme_ids=[original_theme])])
        original = next(row for row in research_activity(session, "xlk")["updates"] if row["kind"] == "event")
    reference = {key: value for key, value in original["reference"].items() if key != "instrument_id"}
    response = client.post("/api/instruments/xlk/research/notes", json={"note": {
        "note_date": "2026-09-09", "title": "我对最初融资条款的判断", "body": "原条款只能证明资金来源得到补充",
        "research_context": reference}})
    assert response.status_code == 200, response.text
    note = response.json()["notes"][0]
    opinion_id = f"opinion:{note['note_id']}:{note['revision_number']}"
    with get_session_factory()() as session:
        publish(session, events=[event(action="updated", theme_ids=[later_theme], body="新的披露转向资金使用效果")])
        publish(session, research={"forecast_reviews": [{"key": "pm-financing-review",
            "related_research_update_id": opinion_id, "outcome": "PM当时的判断仍需经营数据验证", "source_ids": ["original"]}]})
        updates = research_activity(session, "xlk")["updates"]
        reviewed = next(row for row in updates if row["kind"] == "review")
        opinion = resolve_research_update(session, "xlk", opinion_id)
        retained_event = resolve_research_update(session, "xlk", original["update_id"])
        assert reviewed["related_update_id"] == opinion_id and reviewed["theme_ids"] == [original_theme]
        assert opinion["related_update_id"] == original["update_id"] and opinion["theme_ids"] == [original_theme]
        assert opinion["reference"]["event_version_id"] == original["reference"]["event_version_id"]
        assert retained_event["body"] == original["body"] and retained_event["theme_ids"] == [original_theme]
    projected = {row["theme_id"]: row for row in client.get("/api/research/instruments/xlk/themes").json()["themes"]}
    expected_ids = {original["update_id"], opinion_id, reviewed["update_id"]}
    assert expected_ids.issubset({row["update_id"] for row in projected[original_theme]["updates"]})
    assert expected_ids.isdisjoint({row["update_id"] for row in projected[later_theme]["updates"]})
