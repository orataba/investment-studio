"""First baseline cannot silently succeed with only a run summary."""
from copy import deepcopy

import pytest

from .test_sector_submission import submission
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory


def initialize(rid, notebook=None, requested=True):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, rid)
        run.context_json = {**run.context_json, "initialization": {"requested": requested},
            "research_dossiers": [{"instrument_id": "xlk", "notebook": notebook}]}
        session.commit()


def test_empty_baseline_rejects_summary_only_before_saving_and_accepts_limited_view(client, submission):
    rid, draft = submission
    initialize(rid)
    draft["reviews"][0]["research"] = None
    response = client.post(f"/api/research/runs/{rid}/sector-draft", json=draft)
    assert response.status_code == 422 and "investment_view.direction" in response.json()["detail"]
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, rid)
        assert "submitted_draft" not in run.context_json and run.status == "queued"
    draft["reviews"][0]["research"] = {"investment_view": {"direction": "等待实际资本回报披露后再评估。",
        "coverage_status": "limited", "coverage_note": "已读投入披露，尚无实际回报原件。", "source_ids": ["original"]}}
    assert client.post(f"/api/research/runs/{rid}/sector-draft", json=draft).status_code == 200


def test_notebook_with_modules_but_no_view_still_requires_a_baseline(client, submission):
    rid, draft = submission
    initialize(rid, notebook={"modules": [{"key": "existing", "summary": "已保留模块"}]})
    response = client.post(f"/api/research/runs/{rid}/sector-draft", json=draft)
    assert response.status_code == 422 and "首次研究基线" in response.json()["detail"]


@pytest.mark.parametrize("previous", [{"direction": "  "}, {"direction": "暂时未知", "coverage_status": "not_established"}])
def test_unestablished_view_must_be_explicit_but_need_not_invent_a_judgment(client, submission, previous):
    rid, draft = submission
    initialize(rid, notebook={"investment_view": previous})
    invalid = deepcopy(draft)
    invalid["reviews"][0]["research"] = {"investment_view": {"direction": "  "}}
    assert client.post(f"/api/research/runs/{rid}/sector-draft", json=invalid).status_code == 422
    draft["reviews"][0]["research"] = {"investment_view": {"direction": "现有资料不足以形成投资判断。",
        "coverage_status": "not_established", "coverage_note": "当前没有回报兑现或估值证据。",
        "next_check": "取得正式回报披露后再判断。", "source_ids": ["original"]}}
    assert client.post(f"/api/research/runs/{rid}/sector-draft", json=draft).status_code == 200


@pytest.mark.parametrize("requested", [False, True])
def test_existing_effective_view_allows_quiet_or_sparse_omission(client, submission, requested):
    rid, draft = submission
    initialize(rid, notebook={"investment_view": {"direction": "维持观察", "coverage_status": "limited",
        "source_ids": ["original"]}}, requested=requested)
    draft["reviews"][0]["research"] = None
    assert client.post(f"/api/research/runs/{rid}/sector-draft", json=draft).status_code == 200
