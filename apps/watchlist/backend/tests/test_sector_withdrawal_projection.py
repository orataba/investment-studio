from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.services.sector_research import event_record


def test_withdrawal_projection_retains_reason_original_sources_and_history():
    source = {"source_id": "original", "title": "原始材料", "url": "https://example.com/original", "published_at": "2026-09-01"}
    snapshot = {"title": "旧稿", "body": "未通过核证的旧判断", "sources": [source]}
    evidence = {"withdrawn": True, "withdrawal_reason": "时间与事件均未获原文支持。", "withdrawn_at": "2026-09-07T02:00:00+08:00", "sources": [source]}
    case = RiskCase(case_id="withdrawn-case", instrument_id="asset", signal="sector:old-claim", title=snapshot["title"],
                    body=snapshot["body"], status="handled", trigger_active=False, resolved_at=None, evidence_json=evidence,
                    history_json=[{"at": evidence["withdrawn_at"], "action": "withdrawn", "detail": evidence["withdrawal_reason"], "snapshot": snapshot}])
    record = event_record(case)
    assert record["withdrawn"] is True
    assert record["withdrawal_reason"] == evidence["withdrawal_reason"]
    assert record["withdrawn_at"] == evidence["withdrawn_at"]
    assert record["status"] == "handled" and not record["trigger_active"]
    assert record["body"] == snapshot["body"]
    assert record["sources"][0]["url"] == source["url"]
    assert record["history"][0]["action"] == "withdrawn"
    assert record["history"][0]["snapshot"]["body"] == snapshot["body"]
    assert record["history"][0]["snapshot"]["sources"] == record["sources"]
    assert case.resolved_at is None
