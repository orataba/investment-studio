import json
from datetime import date

import pytest
from sqlalchemy import select

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.research import InstrumentResearchNote
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory


@pytest.fixture
def conversation(client):
    question = "建立一个持续关注主题：美元信用与黄金。把黄金中期偏多记为我的观点。暂停这个主题。记录这次复盘。"
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="gold-command", instrument_type="etf", detail_view_type="etf",
                                    instrument_name="黄金", metadata_json={}))
        session.add(ResearchTopic(topic_id="user-conversation", title="黄金讨论", instrument_ids=["gold-command"], created_by_user_id="pm-original"))
        session.flush()
        session.add(ResearchEntry(entry_id="user-command-run", topic_id="user-conversation", kind="analysis",
            title=question, status="running", context_json={"research_run": True, "question": question,
                "research_actor": {"user_id": "pm-original", "display_name": "原投资经理", "mode": "account", "team_id": "default", "team_role": "member"},
                "instrument_ids": ["gold-command"], "catalogue": [{"instrument_id": "gold-command"}],
                "cutoff": "2026-09-08T00:00:00+00:00", "research_dossiers": [{"instrument_id": "gold-command",
                    "notebook": {"version_id": "old-notebook"}, "mandate": {"version_id": "old-mandate"},
                    "materials": [{"source_id": "original-as-read"}], "themes": [], "pm_views": []}]}))
        session.commit()
    return "/api/research/runs/user-command-run/user-command"


def theme_command(**changes):
    return {"action": "manage_theme", "instrument_id": "gold-command",
            "source_quote": "建立一个持续关注主题：美元信用与黄金。",
            "theme": {"title": "美元信用与黄金", "question": "长债收益率上升是否改变黄金定价机制？"}, **changes}


def view_command(**changes):
    return {"action": "record_view", "instrument_id": "gold-command", "source_quote": "把黄金中期偏多记为我的观点。",
            "note": {"note_date": date.today().isoformat(), "title": "黄金中期偏多", "summary": "中期偏多，短期未判断。"}, **changes}


def test_user_commands_link_theme_and_pm_view_with_server_identity_and_same_run_dedupe(client, conversation, monkeypatch):
    response = client.post(conversation, json=theme_command())
    assert response.status_code == 200, response.text
    theme = response.json()
    assert theme["kind"] == "theme" and theme["author_user_id"] == "pm-original"
    assert client.post(conversation, json=theme_command()).json() == theme

    command = view_command()
    command["note"].update(author="模型不能指定作者", research_context={"theme_id": theme["id"], "horizon": "中期"})
    response = client.post(conversation, json=command)
    assert response.status_code == 200, response.text

    receipt = response.json()
    assert client.post(conversation, json=command).json() == receipt
    assert receipt["kind"] == "investment_view" and receipt["author"] == "原投资经理"
    version = client.get("/api/research/runs/user-command-run/dossier/gold-command",
                         params={"version_id": f"pm:{receipt['id']}:1"})
    assert version.status_code == 200, version.text
    assert version.json()["information_cutoff"] == "2026-09-08T00:00:00+00:00"
    with get_session_factory()() as session:
        notes = list(session.scalars(select(InstrumentResearchNote)))
        assert len(notes) == 1
        saved = notes[0]
        assert saved.author_user_id == "pm-original" and saved.author == "原投资经理"
        assert saved.research_context["author_role"] == "user"
        assert saved.research_context["source_quote"] == command["source_quote"]
        assert saved.research_context["recorded_via"] == "assistant"
        assert saved.research_context["information_cutoff"] == "2026-09-08T00:00:00+00:00"
        assert saved.research_context["research_snapshot"] == {
            "notebook_version_id": "old-notebook", "mandate_version_id": "old-mandate", "theme_revision": 1}
        run = session.get(ResearchEntry, "user-command-run")
        assert len(run.context_json["user_records"]) == 2
        bound = run.context_json["research_dossiers"][0]
        assert bound["themes"][0]["theme_id"] == theme["id"]
        assert bound["pm_views"][0]["note_id"] == receipt["id"]
        assert bound["notebook"] == {"version_id": "old-notebook"}
        assert bound["materials"] == [{"source_id": "original-as-read"}]
        assert run.context_json["cutoff"] == "2026-09-08T00:00:00+00:00"


def test_user_theme_can_pause_and_review_appends_to_original_pm_version(client, conversation):
    theme = client.post(conversation, json=theme_command()).json()
    original = view_command()
    original["note"]["research_context"] = {"theme_id": theme["id"]}
    note = client.post(conversation, json=original).json()
    response = client.post(conversation, json=theme_command(theme_id=theme["id"],
        source_quote="暂停这个主题。", theme={"status": "paused"}))
    assert response.status_code == 200 and response.json()["theme_status"] == "paused"
    review = view_command(source_quote="记录这次复盘。")
    review["note"].update(title="黄金观点复盘", note_type="review", research_context={
        "theme_id": theme["id"], "relationship": "review", "related_note_id": note["id"], "related_revision": 1,
        "outcome": "方向符合", "mechanism_assessment": "尚不能证明信用因素主导", "limitations": "单次案例"})
    response = client.post(conversation, json=review)
    assert response.status_code == 200, response.text
    assert response.json()["id"] != note["id"]
    with get_session_factory()() as session:
        notes = list(session.scalars(select(InstrumentResearchNote)))
        assert len(notes) == 2
        assert next(row for row in notes if row.note_id == note["id"]).revision_number == 1
        theme_entry = session.get(ResearchEntry, theme["id"])
        assert len(theme_entry.context_json["versions"]) == 1


@pytest.mark.parametrize("mode", ["sector_run", "risk_run"])
def test_automatic_research_cannot_write_user_records(client, conversation, mode):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "user-command-run")
        run.context_json = {**run.context_json, mode: True}
        session.commit()
    response = client.post(conversation, json=view_command())
    assert response.status_code == 422 and "只有研究助手对话" in response.json()["detail"]


@pytest.mark.parametrize("question,quote", [
    ("黄金是不是可能上涨？", "黄金是不是可能上涨？"),
    ("你觉得这个观点应该如何记录？", "记录？"),
    ("不要保存这个观点。", "不要保存这个观点。"),
    ("不要保存这个观点。", "保存这个观点。"),
    ("不要把这个假设保存成我的观点。", "保存成我的观点。"),
    ("别替我把讨论记录到团队档案。", "记录到团队档案。"),
    ("Do not automatically record this as my view.", "record this as my view."),
    ("先讨论黄金。", "把黄金中期偏多记为我的观点。"),
])
def test_no_save_instruction_or_invented_quote_is_rejected(client, conversation, question, quote):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "user-command-run")
        run.title = question
        run.context_json = {**run.context_json, "question": question,
            "history": [{"question": "把黄金中期偏多记为我的观点。"}]}
        session.commit()
    assert client.post(conversation, json=view_command(source_quote=quote)).status_code == 422
    with get_session_factory()() as session:
        assert list(session.scalars(select(InstrumentResearchNote))) == []


def test_wrong_scope_or_model_owned_metadata_is_rejected(client, conversation):
    assert client.post(conversation, json=view_command(instrument_id="outside")).status_code == 422
    assert client.post(conversation, json={**view_command(), "author_user_id": "someone-else"}).status_code == 422
    command = view_command()
    command["note"]["research_context"] = {"author_role": "user", "source_run_id": "invented"}
    assert client.post(conversation, json=command).status_code == 422


def test_pm_opinion_may_be_unverified_but_cited_source_must_be_real_and_in_scope(client, conversation):
    command = view_command()
    command["note"]["research_context"] = {"background": "根据当时观察提出待验证判断", "source_ids": ["invented"]}
    assert client.post(conversation, json=command).status_code == 422
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "user-command-run")
        run.context_json = {**run.context_json, "instrument_inputs": [
            {"instrument_id": "gold-command", "name": "黄金", "currency": "USD"},
            {"instrument_id": "another", "name": "其他标的", "currency": "USD"}]}
        session.commit()
    command["note"]["research_context"]["source_ids"] = ["instrument:user-command-run:another"]
    assert client.post(conversation, json=command).status_code == 422
    command["note"]["research_context"]["source_ids"] = ["instrument:user-command-run:gold-command"]
    response = client.post(conversation, json=command)
    assert response.status_code == 200, response.text


def test_mcp_user_command_tools_accept_objects_and_json(monkeypatch):
    from watchlist_app import research_mcp
    calls = []
    monkeypatch.setattr(research_mcp, "request", lambda suffix, payload: calls.append((suffix, payload)) or {"status": "saved"})
    theme = theme_command()
    note = view_command()
    assert research_mcp.manage_research_theme(theme["instrument_id"], theme["source_quote"], json.dumps(theme["theme"]))["status"] == "saved"
    assert research_mcp.record_investment_view(note["instrument_id"], note["source_quote"], note["note"])["status"] == "saved"
    assert calls == [("user-command", {**theme, "theme_id": None}), ("user-command", note)]


def test_pm_sources_are_frozen_with_the_view_and_reusable_through_the_dossier(client, conversation):
    from watchlist_app.services.research_dossier import read_dossier, read_dossier_version
    from watchlist_app.services.research_notebook import research_sources, dossier_source
    from watchlist_app.services.research_activity import research_activity
    sid = "instrument:user-command-run:gold-command"
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "user-command-run")
        run.context_json = {**run.context_json, "instrument_inputs": [{"instrument_id": "gold-command", "name": "黄金", "currency": "USD", "observed_value": 100}]}
        session.commit()
    command = view_command()
    command["note"]["research_context"] = {"background": "根据当时取得的数据形成判断", "source_ids": [sid]}
    saved = client.post(conversation, json=command)
    assert saved.status_code == 200, saved.text
    version_id = f"pm:{saved.json()['id']}:1"
    with get_session_factory()() as session:
        before = read_dossier_version(session, "gold-command", version_id)
        assert before["sources"][0]["snapshot"]["observed_value"] == 100
        run = session.get(ResearchEntry, "user-command-run")
        run.context_json = {**run.context_json, "instrument_inputs": [{"instrument_id": "gold-command", "name": "黄金", "currency": "USD", "observed_value": 200}]}
        session.commit()
        after = read_dossier_version(session, "gold-command", version_id)
        assert after == before
        dossier = read_dossier(session, "gold-command")
        assert dossier_source(dossier, sid)["snapshot"]["observed_value"] == 100
        assert research_sources({"cutoff": "2026-09-09T00:00:00+00:00", "research_dossiers": [dossier]}, "later")[sid]["snapshot"]["observed_value"] == 100
        opinion = next(row for row in research_activity(session, "gold-command")["updates"] if row["kind"] == "opinion")
        assert [row["source_id"] for row in opinion["sources"]] == [sid]
    read = client.get("/api/research/instruments/gold-command/dossier", params={"version_id": version_id, "source_id": sid})
    assert read.status_code == 200 and read.json()["snapshot"]["observed_value"] == 100
    assert client.get("/api/research/instruments/gold-command/dossier", params={"version_id": version_id, "source_id": "uncited"}).status_code == 422


def test_editing_a_pm_view_does_not_rebind_unchanged_material_ids(client, conversation):
    material = client.post("/api/research/instruments/gold-command/dossier/materials", json={
        "title": "原始资料", "body": "保存观点时取得的原始说明", "source": "管理人", "published_at": "2026-09-01"}).json()
    payload = {"note": {"note_date": date.today().isoformat(), "title": "独立投资观点", "body": "当时判断",
                        "research_context": {"background": "根据当时已取得的管理人说明形成判断", "source_ids": [material["source_id"]]}}}
    response = client.post("/api/instruments/gold-command/research/notes", json=payload)
    assert response.status_code == 200, response.text
    note = response.json()["notes"][0]
    with get_session_factory()() as session:
        entry = session.get(ResearchEntry, material["entry_id"])
        entry.body = "后来更改的说明"
        session.commit()
    payload["note"]["body"] = "更正措辞，原依据不变"
    response = client.put(f"/api/instruments/gold-command/research/notes/{note['note_id']}", json=payload)
    assert response.status_code == 200, response.text
    changed = response.json()["notes"][0]
    assert changed["research_context"]["sources"][0]["body"] == "保存观点时取得的原始说明"
    for revision in (1, 2):
        original = client.get("/api/research/instruments/gold-command/dossier", params={
            "version_id": f"pm:{note['note_id']}:{revision}", "source_id": material["source_id"]})
        assert original.status_code == 200 and original.json()["body"] == "保存观点时取得的原始说明"
