from datetime import UTC, date, datetime

from watchlist_app.db.models import InstrumentDetail, Watchlist, WatchlistItem
from watchlist_app.db.models.workbench import RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import risk_officer as service


def test_portfolio_reads_same_research_case_and_sources_only_while_held(client, monkeypatch):
    source = {
        "source_id": "baba-disclosure",
        "title": "已留存的公司公告",
        "url": "https://example.com/baba-disclosure",
        "published_at": "2026-09-06T09:00:00+08:00",
        "retrieved_at": "2026-09-06T10:00:00+08:00",
    }
    body = "研究员已保存的判断：关注政策对阿里业务的具体影响，等待公司披露。"
    with get_session_factory()() as session:
        session.add(Watchlist(watchlist_id="research-list", name="标的研究", owner_type="user", owner_id="test"))
        for iid in ("baba", "sold-equity"):
            session.add(InstrumentDetail(instrument_id=iid, instrument_type="equity", detail_view_type="equity", instrument_name=iid, metadata_json={}))
        session.flush()
        for iid in ("baba", "sold-equity"):
            session.add(WatchlistItem(watchlist_id="research-list", instrument_id=iid, added_at=datetime.now(UTC)))
        for case_id, iid, direction, status, active in [
            ("shared-baba-policy", "baba", "risk", "open", True),
            ("baba-opportunity", "baba", "opportunity", "open", True),
            ("baba-resolved", "baba", "risk", "resolved", False),
            ("baba-handled", "baba", "risk", "handled", True),
            ("sold-risk", "sold-equity", "risk", "open", True),
        ]:
            session.add(RiskCase(case_id=case_id, instrument_id=iid, signal=f"sector:{case_id}", title=case_id,
                                 body=body, severity="attention", status=status, trigger_active=active,
                                 observed_on=date(2026, 9, 6), evidence_json={"direction": direction, "sources": [source]}))
        session.commit()

    holdings = {
        "portfolio_id": "portfolio-a",
        "portfolio_name": "持有阿里的组合",
        "as_of_date": "2026-09-06",
        "base_currency": "USD",
        "totals": {"nav": 1000},
        "rows": [
            {"instrument_core": {"instrument_id": "baba"}, "quantity": 2, "market_value_base": 200, "risk_eligible": False},
            {"instrument_core": {"instrument_id": "sold-equity"}, "quantity": 0, "market_value_base": 0},
        ],
    }
    monkeypatch.setattr(service, "external_json", lambda *args: {"workspace": holdings})
    with get_session_factory()() as session:
        watchlist = service.read_snapshot(session, watchlist_id="research-list")
        original = next(case for case in watchlist["research"] if case["case_id"] == "shared-baba-policy")
        assert {case["case_id"] for case in watchlist["research"]} == {"shared-baba-policy", "sold-risk"}

        portfolio = service.read_snapshot(session, portfolio_id="portfolio-a")
        assert portfolio["instrument_ids"] == ["baba"]
        assert portfolio["research"] == [original]
        assert portfolio["research"][0]["body"] == body
        assert portfolio["research"][0]["evidence_json"]["sources"] == [source]

        session.get(RiskCase, original["case_id"]).body = "同一条研究风险已补充公司后续披露。"
        session.commit()
        updated = service.read_snapshot(session, portfolio_id="portfolio-a")["research"]
        assert updated[0]["case_id"] == original["case_id"]
        assert updated[0]["body"] == "同一条研究风险已补充公司后续披露。"

        holdings["rows"][0]["quantity"] = 0
        after_exit = service.read_snapshot(session, portfolio_id="portfolio-a")
        assert after_exit["instrument_ids"] == []
        assert after_exit["research"] == []
