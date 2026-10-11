from sqlalchemy import func, select
from studio_identity import Principal

from portfolio_app.db.models import (
    PortfolioInvestmentViewModel, PortfolioInvestmentViewRevisionModel,
    PortfolioMembershipModel, PortfolioCalculationStateModel, ResearchRunRecordModel,
    TargetSetRecordModel, TransactionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from tests.test_portfolio_accounts import secured, headers  # noqa: F401


def _path(portfolio_id="investment-studio"):
    return f"/api/portfolios/{portfolio_id}/investment-views"


def _draft(**changes):
    return {"view_date": "2026-10-08", "title": "流动性与风险预算", "body": "保留现金，等待风险补偿改善。",
        "context": "依据 2026-10-07 的组合资产配置和风险分析；此处只记录判断。", "instrument_ids": [], **changes}


def _create(client, portfolio_id="investment-studio", **changes):
    response = client.post(_path(portfolio_id), json=_draft(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def _select(client, note, version=0, **kwargs):
    return client.put(f"{_path(note['portfolio_id'])}/current-stance", json={
        "note_id": note["note_id"], "revision_number": note["revision_number"], "expected_version": version}, **kwargs)


def test_append_and_correction_keep_selected_revision_until_explicit_selection(raw_client):
    assert raw_client.get(_path()).json() == {"portfolio_id": "investment-studio", "notes": [], "current_stance": None, "stance_version": 0}
    note = _create(raw_client, source="2026-10-07 research: daily simple returns; conclusion retained here")
    assert note["author_user_id"] == "test-manager"
    assert _select(raw_client, note).status_code == 200
    newer = _create(raw_client, body="新的判断不是自动生效的总体观点。")
    corrected = raw_client.put(f"{_path()}/{note['note_id']}", json=_draft(body="更正措辞。", expected_revision=1))
    assert corrected.status_code == 200
    assert corrected.json()["revision_number"] == 2
    timeline = raw_client.get(_path()).json()
    assert len(timeline["notes"]) == 2
    assert timeline["current_stance"]["note"]["body"] == note["body"]
    assert timeline["current_stance"]["note"]["revision_number"] == 1
    revisions = raw_client.get(f"{_path()}/{note['note_id']}/revisions").json()["revisions"]
    assert [row["revision_number"] for row in revisions] == [2, 1]
    assert revisions[1]["body"] == note["body"]
    assert revisions[1]["source"] == note["source"]
    assert _select(raw_client, corrected.json(), 1).json()["current_stance"]["note"]["revision_number"] == 2
    assert newer["note_id"] != note["note_id"]


def test_stale_correction_and_stance_are_conflicts_without_overwrite(raw_client):
    note = _create(raw_client)
    path = f"{_path()}/{note['note_id']}"
    assert raw_client.put(path, json=_draft(body="winner", expected_revision=1)).status_code == 200
    assert raw_client.put(path, json=_draft(body="loser", expected_revision=1)).status_code == 409
    assert _select(raw_client, note).status_code == 200
    assert _select(raw_client, note).status_code == 409
    assert raw_client.put(f"{_path()}/current-stance", json={"note_id": None, "revision_number": None, "expected_version": 0}).status_code == 409
    cleared = raw_client.put(f"{_path()}/current-stance", json={"note_id": None, "revision_number": None, "expected_version": 1})
    assert cleared.json() == {"current_stance": None, "stance_version": 2}
    assert raw_client.get(path + "/revisions").json()["revisions"][0]["body"] == "winner"


def test_shared_assets_can_be_linked_without_holdings_and_filter_one_timeline(raw_client):
    portfolio = raw_client.post("/api/portfolios", json={"name": "未建仓", "base_currency": "USD", "inception_date": "2026-10-01"}).json()
    portfolio_id = portfolio["portfolio_id"]
    available = raw_client.get(f"{_path(portfolio_id)}/instruments").json()["instruments"]
    ids = [row["instrument_id"] for row in available[:2]]
    assert len(ids) == 2
    note = _create(raw_client, portfolio_id, instrument_ids=ids)
    assert note["instrument_ids"] == ids
    assert note["instruments"] == available[:2]
    for instrument_id in ids:
        response = raw_client.get(_path(portfolio_id), params={"instrument_id": instrument_id})
        assert [row["note_id"] for row in response.json()["notes"]] == [note["note_id"]]
    assert raw_client.get(_path(portfolio_id), params={"instrument_id": "unrelated"}).json()["notes"] == []
    assert _select(raw_client, note).status_code == 422
    # A corrected association changes the same timeline record; its historical association remains inspectable.
    assert raw_client.put(f"{_path(portfolio_id)}/{note['note_id']}", json=_draft(expected_revision=1, instrument_ids=[])).status_code == 200
    assert raw_client.get(f"{_path(portfolio_id)}/{note['note_id']}/revisions").json()["revisions"][1]["instrument_ids"] == ids


def test_invalid_shared_identity_and_forged_author_do_not_save(raw_client):
    for invalid in ["other-portfolio-private-option", "not-registered"]:
        assert raw_client.post(_path(), json=_draft(instrument_ids=[invalid])).status_code == 422
    assert raw_client.post(_path(), json=_draft(author_user_id="somebody-else")).status_code == 422
    assert raw_client.post(_path(), json=_draft(context="   ")).status_code == 422
    assert raw_client.post(_path(), json=_draft(instrument_ids=["x", "x"])).status_code == 422
    assert raw_client.get(_path()).json()["notes"] == []


def test_views_have_no_transaction_target_research_or_calculation_side_effects(raw_client):
    def state():
        with get_session_factory()() as session:
            return {
                "rows": [session.scalar(select(func.count()).select_from(model)) for model in (TransactionRecordModel, TargetSetRecordModel, ResearchRunRecordModel)],
                "calculation": [dict(row) for row in session.execute(select(PortfolioCalculationStateModel.__table__)).mappings()],
            }
    before = state()
    note = _create(raw_client)
    assert _select(raw_client, note).status_code == 200
    assert before == state()


def test_portfolio_acl_and_revision_ids_cannot_cross_portfolios(secured):
    client, _, a, b = secured
    note_a = client.post(_path(a), json=_draft(), headers=headers()).json()
    note_b = client.post(_path(b), json=_draft(body="private"), headers=headers("bob")).json()
    for user in ("owner", "other"):
        assert client.get(_path(a), headers=headers(user)).status_code == 404
        assert client.get(f"{_path(a)}/instruments", headers=headers(user)).status_code == 404
    assert client.get(_path(a), headers=headers("bob")).status_code == 200
    assert client.post(_path(a), json=_draft(), headers=headers("bob")).status_code == 403
    assert _select(client, note_a, **{"headers": headers("bob")}).status_code == 403
    assert client.put(f"{_path(a)}/{note_a['note_id']}", json=_draft(expected_revision=1), headers=headers("bob")).status_code == 403
    assert client.get(f"{_path(a)}/{note_b['note_id']}/revisions", headers=headers()).status_code == 404
    cross_select = client.put(f"{_path(a)}/current-stance", json={"note_id": note_b["note_id"], "revision_number": 1, "expected_version": 0}, headers=headers())
    assert cross_select.status_code == 404
    cross_edit = client.put(f"{_path(a)}/{note_b['note_id']}", json=_draft(expected_revision=1), headers=headers())
    assert cross_edit.status_code == 404
    assert client.get(_path(b), headers=headers()).status_code == 404


def test_editor_may_append_but_only_correct_own_views_and_manager_audits_correction(secured):
    client, _, a, _ = secured
    with get_session_factory()() as session:
        session.get(PortfolioMembershipModel, (a, "bob")).role = "editor"
        session.commit()
    alice_note = client.post(_path(a), json=_draft(), headers=headers()).json()
    bob_note = client.post(_path(a), json=_draft(body="乙的判断"), headers=headers("bob")).json()
    assert bob_note["author_user_id"] == "bob"
    assert client.put(f"{_path(a)}/{alice_note['note_id']}", json=_draft(expected_revision=1), headers=headers("bob")).status_code == 403
    response = client.put(f"{_path(a)}/{bob_note['note_id']}", json=_draft(expected_revision=1, body="更正"), headers=headers())
    assert response.status_code == 200
    assert response.json()["author_user_id"] == "bob"
    assert response.json()["revised_by_user_id"] == "alice"
    assert client.get(_path(a), headers=headers("bob")).json()["notes"][0]["author_user_id"] == "bob"


def test_delegated_and_service_research_cannot_write_views(secured):
    client, people, a, _ = secured
    people["delegate"] = Principal("alice", "甲经理", "default", resource_scope={"kind": "portfolio", "id": a})
    for user in ("delegate", "maintenance"):
        response = client.post(_path(a), json=_draft(), headers=headers(user))
        assert response.status_code == 403
    assert client.get(_path(a), headers=headers("delegate")).status_code == 200


def test_failing_shared_identity_validation_rolls_back_correction(raw_client):
    note = _create(raw_client)
    response = raw_client.put(f"{_path()}/{note['note_id']}", json=_draft(expected_revision=1, instrument_ids=["private-contract"]))
    assert response.status_code == 422
    with get_session_factory()() as session:
        assert session.get(PortfolioInvestmentViewModel, ("investment-studio", note["note_id"])).latest_revision == 1
        assert session.scalar(select(func.count()).select_from(PortfolioInvestmentViewRevisionModel)) == 1
