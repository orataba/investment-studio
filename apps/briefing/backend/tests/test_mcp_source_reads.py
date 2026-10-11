"""A citation is not evidence delivery; every stage must obtain all cited pages."""
from copy import deepcopy

import pytest

from briefing_app import mcp as tools
from briefing_app.contracts import ReportDraft


@pytest.fixture
def source_tools(monkeypatch):
    monkeypatch.setenv("INVESTMENT_STUDIO_BRIEFING_REPORT_ID", "report-one")
    monkeypatch.setenv("INVESTMENT_STUDIO_BRIEFING_HARNESS_MODE", "write")
    monkeypatch.setattr(tools, "_source_reads", {})
    sources = {"text:one": {"source_id": "text:one", "content_text": "前" * 8000 + "完整限定条件"},
               "market:one": {"source_id": "market:one", "return_pct": None, "coverage": "missing_session"}}
    submissions = []

    def request(suffix, payload=None):
        if suffix.startswith("sources/"):
            from urllib.parse import unquote
            return deepcopy(sources[unquote(suffix.removeprefix("sources/"))])
        submissions.append((suffix, payload))
        return {"accepted": True}

    monkeypatch.setattr(tools, "request", request)
    return sources, submissions


def draft(*source_ids):
    return ReportDraft.model_validate({"report_type": "daily", "sections": [{"kind": "takeaway_section", "title": "重点信息", "groups": [
        {"title": "宏观", "items": [{"title": "有条件的判断", "summary": "保留来源限定条件。", "analysis": "证据不足处保持未确认。", "source_ids": list(source_ids)}]},
        {"title": "微观", "items": []}]}]})


def test_citing_an_unread_source_or_only_its_first_page_cannot_submit(source_tools):
    _, submissions = source_tools
    report = draft("text:one")
    with pytest.raises(ValueError, match="尚未完整取得.*text:one"):
        tools.submit_briefing_report(report)
    page = tools.read_briefing_source("text:one")
    assert page["next_offset"] == 8000
    with pytest.raises(ValueError, match="尚未完整取得"):
        tools.submit_briefing_report(report)
    assert submissions == []
    tools.read_briefing_source("text:one", offset=8000)
    assert tools.submit_briefing_report(report) == {"accepted": True}
    assert submissions[0][0] == "draft?mode=write"


def test_skipped_middle_page_is_not_covered_by_a_last_page_read(source_tools):
    sources, submissions = source_tools
    sources["text:one"]["content_text"] = "原文" * 8500
    tools.read_briefing_source("text:one", offset=16000)
    tools.read_briefing_source("text:one", offset=0)
    tools.read_briefing_source("text:one", offset=17000)
    with pytest.raises(ValueError, match="尚未完整取得"):
        tools.submit_briefing_report(draft("text:one"))
    assert submissions == []
    tools.read_briefing_source("text:one", offset=8000)
    assert tools.submit_briefing_report(draft("text:one"))["accepted"]


def test_independent_review_and_other_reports_cannot_reuse_write_stage_reads(source_tools, monkeypatch):
    tools.read_briefing_source("market:one")
    assert tools.submit_briefing_report(draft("market:one"))["accepted"]
    monkeypatch.setenv("INVESTMENT_STUDIO_BRIEFING_HARNESS_MODE", "review")
    with pytest.raises(ValueError, match="尚未完整取得"):
        tools.submit_briefing_report(draft("market:one"))
    tools.read_briefing_source("market:one")
    assert tools.submit_briefing_report(draft("market:one"))["accepted"]
    monkeypatch.setenv("INVESTMENT_STUDIO_BRIEFING_REPORT_ID", "other-report")
    with pytest.raises(ValueError, match="尚未完整取得"):
        tools.submit_briefing_report(draft("market:one"))


def test_failed_read_does_not_create_a_receipt(source_tools, monkeypatch):
    def unavailable(*args, **kwargs):
        raise TimeoutError("Bound source unavailable")
    monkeypatch.setattr(tools, "request", unavailable)
    with pytest.raises(TimeoutError):
        tools.read_briefing_source("text:one")
    with pytest.raises(ValueError, match="尚未完整取得"):
        tools.submit_briefing_report(draft("text:one"))


def test_unavailable_or_excerpt_original_stays_explicit_and_requires_a_read(source_tools):
    sources, _ = source_tools
    sources["text:one"].update(content_text="", content_completeness="unavailable")
    page = tools.read_briefing_source("text:one")
    assert page["content_completeness"] == "unavailable" and page["text_length"] == 0
    # A receipt confirms returned evidence only, never full publisher coverage.
    assert tools.submit_briefing_report(draft("text:one"))["accepted"]
