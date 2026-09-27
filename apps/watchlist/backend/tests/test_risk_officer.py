from datetime import UTC, date, datetime

import pytest
from watchlist_app.db.models import InstrumentDetail, InstrumentChartReadModel, InstrumentManualProfile, InstrumentRiskReadModel, Watchlist, WatchlistItem
from watchlist_app.db.models.workbench import ResearchEntry, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import risk_officer as service


@pytest.fixture(autouse=True)
def portfolio_access_fixture(monkeypatch):
    # Domain tests below isolate snapshot semantics; actual ACL/revocation uses the account route suite.
    from watchlist_app.services import research_access
    monkeypatch.setattr(research_access, "require_portfolio", lambda pid: {"portfolio_id": pid, "role": "reader"})


def risk_request(context):
    from watchlist_app.services.risk_read_projection import project_risk_read
    return lambda suffix, payload=None: project_risk_read(context, **payload) if suffix == "risk-read" else context


def seed(client):
    with get_session_factory()() as session:
        session.add(Watchlist(watchlist_id="risk-list", name="范围列表", owner_type="user", owner_id="test"))
        for iid in ("risk-a", "risk-b", "outside"):
            session.add(InstrumentDetail(instrument_id=iid, instrument_type="etf", detail_view_type="etf", instrument_name=iid, metadata_json={}))
        session.flush()
        for iid in ("risk-a", "risk-b"):
            session.add(WatchlistItem(watchlist_id="risk-list", instrument_id=iid, added_at=datetime.now(UTC)))
        entries = [
            ("research", "risk-a", "sector:policy", "attention", "open", True, "risk"),
            ("uncertain", "risk-b", "sector:rumor", "attention", "investigating", True, "uncertain"),
            ("price", "risk-a", "period_loss", "attention", "open", True, None),
            ("coverage", "risk-b", "coverage", "coverage", "open", True, None),
            ("opportunity", "risk-b", "sector:upside", "attention", "open", True, "opportunity"),
            ("handled", "risk-a", "sector:handled", "attention", "handled", True, "risk"),
            ("resolved", "risk-a", "sector:resolved", "attention", "resolved", False, "risk"),
            ("outside", "outside", "period_loss", "attention", "open", True, None),
        ]
        for cid, iid, signal, severity, status, active, direction in entries:
            session.add(RiskCase(case_id=cid, instrument_id=iid, signal=signal, title=cid, body="已留存事实", severity=severity,
                status=status, trigger_active=active, observed_on=date(2026, 9, 6), evidence_json={"direction": direction}))
        session.commit()


def test_review_get_uses_versions_and_keeps_unversioned_legacy_report(client, monkeypatch):
    from watchlist_app.services import risk_review_state
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id="risk-a")
        run_id = run.entry_id
    service.prepare_run(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        service.apply_result(session, run, reply())
        session.commit()
        monkeypatch.setattr(service, "read_snapshot", lambda *a, **k: pytest.fail("GET must not prepare model evidence"))
        monkeypatch.setattr(service, "performance_context", lambda *a, **k: pytest.fail("GET must not recalculate comparisons"))
        current = service.review_workspace(session, instrument_id="risk-a")
        assert current["latest_completed"]["stale"] is False
        assert current["counts"] == {"research": 1, "quantitative": 1, "coverage": 0}
        run.context_json = {key: value for key, value in run.context_json.items()
                            if key not in {"risk_input_version", "risk_review_view"}}
        session.commit()
        monkeypatch.setattr(risk_review_state, "input_version", lambda *a: pytest.fail("Legacy has no comparable version"))
        legacy = service.review_workspace(session, instrument_id="risk-a")
        assert legacy["latest_completed"]["stale"] is None
        assert legacy["latest_completed"]["summary"] == current["latest_completed"]["summary"]
        assert legacy["latest_completed"]["evidence_sources"] == current["latest_completed"]["evidence_sources"]


def test_review_versions_detect_same_day_market_pm_risk_and_research_changes(client):
    from datetime import timedelta
    from watchlist_app.db.models.research import InstrumentResearchProfile, InstrumentResearchNote
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services.risk_review_state import current_scope, input_version
    seed(client)
    now = datetime(2026, 9, 20, tzinfo=UTC)
    with get_session_factory()() as session:
        session.add(InstrumentChartReadModel(instrument_id="risk-a", payload_json={"research_returns": {}},
            data_freshness_status="fresh", last_recalculated_at=now, source_cutoff_at=now))
        session.add(InstrumentResearchProfile(instrument_id="risk-a", current_view="原PM判断"))
        session.add(InstrumentResearchNote(instrument_id="risk-a", note_id="pm-risk", note_date=now.date(),
            note_type="risk", title="PM风险观点", body="原文", created_at=now, updated_at=now))
        session.add(ResearchTopic(topic_id="risk-source", title="研究", instrument_ids=["risk-a"], visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="risk-source-run", topic_id="risk-source", kind="analysis", title="研究",
            status="completed", context_json={"sector_run": True, "instrument_ids": ["risk-a"]}))
        session.commit()
        def version():
            return input_version(session, current_scope(session, {"instrument_id": "risk-a"}))
        prior = version()
        assert prior is not None and prior == version()
        chart = session.get(InstrumentChartReadModel, "risk-a")
        chart.payload_json = {"research_returns": {"points": [{"date": "2026-09-20", "value": 102}]}}
        chart.last_recalculated_at = now + timedelta(minutes=1)  # Same valuation date, corrected source generation.
        session.commit()
        assert version() != prior
        prior = version()
        profile = session.get(InstrumentResearchProfile, "risk-a")
        profile.current_view, profile.revision_number = "改变判断", profile.revision_number + 1
        session.commit()
        assert version() != prior
        prior = version()
        note = session.get(InstrumentResearchNote, ("risk-a", "pm-risk"))
        note.completed_at = now
        session.commit()
        assert version() != prior
        prior = version()
        case = session.get(RiskCase, "research")
        case.status = "handled"  # PM handling must not disappear behind a same-day market key.
        session.commit()
        assert version() != prior
        prior = version()
        source = session.get(ResearchEntry, "risk-source-run")
        source.status = "failed"
        session.commit()
        assert version() != prior
        chart.last_recalculated_at = None
        session.commit()
        assert version() is None  # Missing version is unknown, never unchanged.


def test_review_version_keeps_current_stance_and_comparator_generations(client):
    from datetime import timedelta
    from watchlist_app.db.models.research import InstrumentInvestmentStance
    from watchlist_app.services.risk_review_state import current_scope, input_version
    seed(client)
    now = datetime(2026, 9, 20, tzinfo=UTC)
    with get_session_factory()() as session:
        session.add(InstrumentManualProfile(instrument_id="risk-a", updated_at=now,
            nav_settings_json={"default_benchmark_instrument_id": "outside"}))
        session.add(InstrumentChartReadModel(instrument_id="outside", payload_json={}, data_freshness_status="fresh",
            last_recalculated_at=now, source_cutoff_at=now))
        session.commit()
        def version():
            return input_version(session, current_scope(session, {"instrument_id": "risk-a"}))
        prior = version()
        comparator = session.get(InstrumentChartReadModel, "outside")
        comparator.last_recalculated_at = now + timedelta(minutes=1)
        session.commit()
        assert version() != prior
        prior = version()
        session.add(InstrumentInvestmentStance(selection_id="stance-cleared", instrument_id="risk-a", team_id="default",
            selected_at=now, selected_by="test", selected_by_name="Test"))
        session.commit()
        assert version() != prior


def test_snapshot_does_not_bind_version_across_concurrent_input_update(client, monkeypatch):
    from watchlist_app.services import risk_review_state
    seed(client)
    original = service.read_snapshot
    def changed(session, **scope):
        snapshot = original(session, **scope)
        session.get(RiskCase, "research").status = "handled"
        session.flush()
        return snapshot
    monkeypatch.setattr(service, "read_snapshot", changed)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id="risk-a")
        run_id = run.entry_id
    service.prepare_run(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert run.context_json["risk_input_version"] is None
        assert run.context_json["risk_inputs"]["research"][0]["status"] == "open"


@pytest.mark.parametrize("assessment_status", ["active", "resolved", "pending"])
def test_own_assessment_does_not_expire_new_report_but_later_pm_change_does(client, assessment_status):
    seed(client)
    dates = {"risk-a": "2026-09-08"}
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id="risk-a", scheduled_dates=dates)
        run_id = run.entry_id
    service.prepare_run(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        event = next(row for row in run.context_json["risk_inputs"]["research"] if row["case_id"] == "research")
        service.apply_result(session, run, {**reply(), "case_assessments": [{"case_id": "research",
            "event_version_id": event["evidence_json"]["event_version_id"], "status": assessment_status, "reason": "依据本轮证据"}]})
        session.commit()
        assert service.review_workspace(session, instrument_id="risk-a")["latest_completed"]["stale"] is False
        same, created = service.begin_run(session, instrument_id="risk-a", scheduled_dates=dates)
        assert not created and same.entry_id == run.entry_id
        session.get(RiskCase, "research").status = "handled"
        session.commit()
        assert service.review_workspace(session, instrument_id="risk-a")["latest_completed"]["stale"] is True
        updated, created = service.begin_run(session, instrument_id="risk-a", scheduled_dates=dates)
        assert created and updated.entry_id != run.entry_id


def test_own_assessment_does_not_hide_pm_update_during_model_run(client):
    from watchlist_app.db.models.research import InstrumentResearchProfile
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id="risk-a")
        run_id = run.entry_id
    service.prepare_run(run_id)
    with get_session_factory()() as session:
        session.add(InstrumentResearchProfile(instrument_id="risk-a", current_view="模型运行期间新录入的PM判断"))
        session.commit()
        run = session.get(ResearchEntry, run_id)
        event = next(row for row in run.context_json["risk_inputs"]["research"] if row["case_id"] == "research")
        service.apply_result(session, run, {**reply(), "case_assessments": [{"case_id": "research",
            "event_version_id": event["evidence_json"]["event_version_id"], "status": "active", "reason": "依据本轮证据"}]})
        session.commit()
        assert service.review_workspace(session, instrument_id="risk-a")["latest_completed"]["stale"] is True


def test_review_version_tracks_theme_lifecycle_and_market_midnight(client, monkeypatch):
    from datetime import timedelta
    from investment_studio_instrument_core.db_models import Instrument
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services import risk_review_state
    seed(client)
    current = [datetime(2026, 9, 25, 15, 59, tzinfo=UTC)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz) if tz else current[0]
    monkeypatch.setattr(risk_review_state, "datetime", Clock)
    with get_session_factory()() as session:
        session.add(Instrument(instrument_id="risk-a", instrument_name="基金", instrument_type="public_fund", currency="CNY",
            quote_selection_policy_json={}, source_settings_json={}, refresh_status_json={}, lifecycle_state_json={"status": "active"}))
        session.add(ResearchTopic(topic_id="dossier:risk-a", title="底稿", instrument_ids=["risk-a"], visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="version-theme", topic_id="dossier:risk-a", kind="note", title="主题", status="active",
            context_json={"role": "research_theme", "instrument_id": "risk-a"}))
        session.commit()
        def version():
            return risk_review_state.input_version(session, risk_review_state.current_scope(session, {"instrument_id": "risk-a"}))
        prior = version()
        session.get(ResearchEntry, "version-theme").status = "closed"
        session.commit()
        assert version() != prior
        prior = version()
        current[0] += timedelta(minutes=2)  # Shanghai date crosses midnight; UTC date does not.
        assert version() != prior
        assert version()["review_days"]["risk-a"]["day"] == "2026-09-26"


def reply(**priority):
    return {"summary": "现有证据需要进一步核查共同影响。", "priorities": [{"title": "共同风险", "analysis": "依据已留存事项复核。",
        "instrument_ids": ["risk-a"], "case_ids": ["research"], "next_watch": "等待原文披露。", **priority}], "limitations": []}


def test_scope_read_separates_inputs_without_model_or_new_run(client, monkeypatch):
    seed(client)
    from watchlist_app.services.canonical_recalc import CanonicalRecalcService
    peer_reads = []
    original_peer_context = service.peer_context
    def read_peers(session):
        peer_reads.append(session)
        return original_peer_context(session)
    monkeypatch.setattr(service, "peer_context", read_peers)
    monkeypatch.setattr(CanonicalRecalcService, "_peer_comparison_context", lambda *a, **k: pytest.fail("Risk reads classification only, without full ranking snapshots"))
    monkeypatch.setattr(CanonicalRecalcService, "current_peer_comparison", lambda *a, **k: pytest.fail("Risk must not compute the UI ranking"))
    monkeypatch.setattr(service, "begin_run", lambda *a, **k: pytest.fail("GET must not queue a model"))
    response = client.get("/api/risk/review?watchlist_id=risk-list")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["counts"] == {"research": 2, "quantitative": 1, "coverage": 1}
    assert {row["instrument_id"] for row in payload["instruments"]} == {"risk-a", "risk-b"}
    assert not peer_reads  # Opening an unreviewed scope does not build model inputs.
    assert payload["latest_run"] is None and payload["latest_completed"] is None
    assert "不代表实际持仓" in payload["limitations"][0]
    assert client.get("/api/risk/review").status_code == 422
    assert client.get("/api/risk/review?watchlist_id=risk-list&instrument_id=risk-a").status_code == 422


def test_prepared_snapshot_and_result_scope_and_staleness(client):
    seed(client)
    with get_session_factory()() as session:
        run, created = service.begin_run(session, watchlist_id="risk-list")
        run_id = run.entry_id
        assert created and run.context_json["risk_run"]
        assert not service.begin_run(session, watchlist_id="risk-list")[1]
    service.prepare_run(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        inputs = run.context_json["risk_inputs"]
        assert {c["case_id"] for c in inputs["research"]} == {"research", "uncertain"}
        assert inputs["portfolio"] is None
        with pytest.raises(ValueError, match="范围快照"):
            service.apply_result(session, run, reply(instrument_ids=["outside"]))
        with pytest.raises(ValueError, match="范围快照"):
            service.apply_result(session, run, reply(case_ids=["handled"]))
        with pytest.raises(ValueError, match="不一致"):
            service.apply_result(session, run, reply(case_ids=["uncertain"]))
        service.apply_result(session, run, reply())
        session.commit()
        assert not service.review_workspace(session, watchlist_id="risk-list")["latest_completed"]["stale"]
        case = session.get(RiskCase, "research")
        case.body = "新的原文改变了已留存风险判断。"
        session.commit()
        assert service.review_workspace(session, watchlist_id="risk-list")["latest_completed"]["stale"]
        next_run, _ = service.begin_run(session, watchlist_id="risk-list", scheduled_dates={"risk-a": "2026-09-08"})
        next_run.status, next_run.body = "failed", "本次运行未完成。"
        session.commit()
        state = service.review_workspace(session, watchlist_id="risk-list")
        assert state["latest_completed"]["run_id"] == run_id
        assert state["latest_run"]["status"] == "failed"
        assert not service.begin_run(session, watchlist_id="risk-list", scheduled_dates={"risk-a": "2026-09-08"})[1]


def test_portfolio_uses_real_holdings_nav_and_preserves_unknown_values(client, monkeypatch):
    seed(client)
    payload = {"portfolio_id": "p1", "portfolio_name": "真实组合", "as_of_date": "2026-09-05", "base_currency": "CNY", "totals": {"nav": 1000},
        "rows": [{"instrument_core": {"instrument_id": iid}, "quantity": quantity, "market_value_base": value, "risk_eligible": False}
                 for iid, quantity, value in [("risk-a", 2, 100), ("risk-a", 3, 150), ("outside", 0, 10000)]],
        "forward_risk": {"status": "limited", "missing_instrument_ids": ["risk-a"]}, "quality_warnings": ["估值披露滞后"]}
    calls = []
    payload["rows"][0].update(cost_basis_base=150, unrealized_pnl_base=-50, unrealized_return_base=-1 / 3, instrument_holding_start_date="2026-06-26")
    def actual(service_name, path):
        calls.append((service_name, path))
        return {"workspace": payload}
    monkeypatch.setattr(service, "external_json", actual)
    with get_session_factory()() as session:
        snapshot = service.read_snapshot(session, portfolio_id="p1")
        assert calls == [("portfolio", "/portfolios/p1/risk-context")]
        assert snapshot["instrument_ids"] == ["risk-a"]
        portfolio = snapshot["portfolio"]
        assert portfolio["affected_market_value_base"] == 250
        assert portfolio["affected_nav_pct"] == 25
        assert portfolio["positions"][0]["unrealized_pnl_base"] == -50
        assert portfolio["positions"][0]["unrealized_return_pct"] == pytest.approx(-100 / 3)
        assert portfolio["positions"][0]["market_value_nav_pct"] == 10
        assert "不是风险贡献" in portfolio["exposure_note"]
        assert portfolio["quality_warnings"] == ["估值披露滞后"]
        payload["rows"][0]["market_value_base"] = None
        portfolio = service.read_snapshot(session, portfolio_id="p1")["portfolio"]
        assert portfolio["affected_market_value_base"] is None and portfolio["affected_nav_pct"] is None


def test_unavailable_portfolio_keeps_failed_input_and_does_not_become_empty_success(client, monkeypatch):
    monkeypatch.setattr(service, "external_json", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, portfolio_id="p1")
        run_id = run.entry_id
    with pytest.raises(ValueError, match="组合持仓读取未完成"):
        service.prepare_run(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert run.context_json["risk_inputs"]["scope_available"] is False
        assert service.review_workspace(session, portfolio_id="p1")["available"] is False


def test_portfolio_modules_and_local_contracts_have_bound_sources_and_underlying_reports(client, monkeypatch):
    seed(client)
    context = {
        "workspace": {"portfolio_id": "p1", "portfolio_name": "含衍生品的组合", "as_of_date": "2026-09-05", "base_currency": "CNY",
            "totals": {"nav": 1000}, "rows": [
                {"derivative_contract_id": "option-1", "instrument_core": {"instrument_id": "local-option", "instrument_type": "option"},
                 "holding_kind": "option_obligation", "quantity": 1, "market_value_base": -10},
                {"instrument_core": {"instrument_id": "cash:CNY", "instrument_type": "cash"},
                 "holding_kind": "settled_cash", "quantity": 100, "market_value_base": 100}]},
        "holdings": [{"holding_id": "option-1", "name": "认沽合约", "detail_path": "/portfolios/p1/holdings/option-1"}],
        "derivatives": {"positions": [{"holding_id": "option-1", "underlyings": [{"instrument_id": "risk-a"}]}]},
        "sources": [
            {"source_id": "portfolio-risk:p1:metrics", "portfolio_id": "p1", "title": "分类风险贡献", "detail_path": "/portfolios/p1/risk"},
            {"source_id": "portfolio-derivative:p1:option-1", "portfolio_id": "p1", "holding_id": "option-1", "title": "认沽合约条款"}],
        "limitations": ["尚未配置允许偏离带。"]}
    monkeypatch.setattr(service, "external_json", lambda name, path: ({
        "portfolio_id": "p1", "name": "含衍生品的组合", "as_of_date": "2026-09-05", "available": True,
        "version": "retained-generation", "instrument_ids": ["risk-a"], "holdings": context["holdings"], "limitations": []}
        if path.endswith("/version") else context))
    with get_session_factory()() as session:
        snapshot = service.read_snapshot(session, portfolio_id="p1")
        assert snapshot["instrument_ids"] == ["risk-a"]  # Actual underlying, not a fake Watchlist contract or cash asset.
        assert [case["case_id"] for case in snapshot["research"]] == ["research"]
        assert snapshot["portfolio"]["affected_market_value_base"] == 0  # Underlying is not held outright.
        assert snapshot["portfolio"]["affected_derivative_holding_ids"] == ["option-1"]
        run, _ = service.begin_run(session, portfolio_id="p1")
        run.context_json = {**run.context_json, "risk_inputs": snapshot}
        service.apply_result(session, run, reply(instrument_ids=[], case_ids=[], source_ids=["portfolio-risk:p1:metrics"]))
        service.apply_result(session, run, reply(instrument_ids=[], holding_ids=["option-1"], case_ids=[], source_ids=["portfolio-derivative:p1:option-1"]))
        for changes, error in [
            ({"instrument_ids": [], "case_ids": [], "holding_ids": ["outside"], "source_ids": ["portfolio-risk:p1:metrics"]}, "范围快照"),
            ({"instrument_ids": [], "case_ids": [], "source_ids": ["portfolio-derivative:p1:option-1"]}, "合约来源"),
            ({"instrument_ids": [], "case_ids": ["research"], "source_ids": ["portfolio-risk:p1:metrics"]}, "涉及标的不一致"),
        ]:
            with pytest.raises(ValueError, match=error):
                service.apply_result(session, run, reply(**changes))
        session.commit()
        view = service.review_workspace(session, portfolio_id="p1")
        assert view["holdings"] == context["holdings"]
        assert view["latest_completed"]["priorities"][0]["holding_ids"] == ["option-1"]
        assert view["latest_completed"]["evidence_sources"]["portfolio-risk:p1:metrics"]["detail_path"] == "/portfolios/p1/risk"


def test_new_portfolio_modules_and_sources_survive_risk_snapshot_and_validation(client, monkeypatch):
    from types import SimpleNamespace
    from watchlist_app import research_mcp
    seed(client)
    sources = [{"source_id": source_id, "portfolio_id": "p1", "detail_path": "/portfolios/p1/risk"}
               for source_id in ("concentration:p1:1", "tail:p1:.95", "portfolio-risk:p1:targets:industry", "portfolio-risk:p1:targets:country")]
    sources[0]["source_type"] = "portfolio_concentration"
    context = {
        "portfolio_id": "p1", "as_of_date": "2026-09-08",
        "workspace": {"portfolio_id": "p1", "portfolio_name": "组合", "as_of_date": "2026-09-08",
            "base_currency": "CNY", "totals": {"nav": 1000}, "rows": [
                {"position_reference_id": "risk-a", "holding_kind": "position", "quantity": 1,
                 "market_value_base": 600, "instrument_core": {"instrument_id": "risk-a", "instrument_type": "etf"}}]},
        "holdings": [{"holding_id": "risk-a"}],
        "concentration": {"status": "partial", "settings_revision": 3, "source_id": sources[0]["source_id"], "sources": [sources[0]], "scopes": [
            {"taxonomy_id": tid, "rows": [{"weight": None, "lower_bound_weight": .4, "status": "breached"}]}
            for tid in ("industry", "country")]},
        "tail_risk": {"status": "unavailable", "var_amount": None, "confidence": .99, "sources": [sources[1]],
                      "tail_effective_observations": .9, "coverage_status": "partial", "excluded_gross_nav_fraction": .4},
        "targets_by_taxonomy": [{"taxonomy_id": tid, "source_id": f"portfolio-risk:p1:targets:{tid}", "rows": [{"dimension": "weight", "breach": None}]}
                                for tid in ("industry", "country")],
        "sources": sources,
    }
    calls = []
    monkeypatch.setattr(service, "external_json", lambda name, path: calls.append((name, path)) or context)
    with get_session_factory()() as session:
        snapshot = service.read_snapshot(session, portfolio_id="p1")
    assert calls == [("portfolio", "/portfolios/p1/risk-context")]
    for section in ("concentration", "tail_risk", "targets_by_taxonomy"):
        assert snapshot["portfolio"]["risk_context"][section] == context[section]
    assert set(source["source_id"] for source in sources).issubset(service.evidence_sources(snapshot))
    monkeypatch.setattr(research_mcp, "request", risk_request({
        "risk_run": True, "cutoff": "2026-09-08", "risk_inputs": snapshot}))
    assert research_mcp.read_portfolio_risk("concentration")["sources"] == [sources[0]]
    assert research_mcp.read_portfolio_risk("tail_risk")["sources"] == [sources[1]]
    target_packet = research_mcp.read_portfolio_risk("targets_by_taxonomy")
    assert target_packet["sources"] == sources[2:]
    assert len(target_packet["current"]) == 2
    run = SimpleNamespace(context_json={"risk_inputs": snapshot})
    for source in sources:
        review = service.RiskReview.model_validate(reply(instrument_ids=[], case_ids=[], source_ids=[source["source_id"]]))
        service.validate_result(run, review)
    with pytest.raises(ValueError, match="范围快照"):
        service.validate_result(run, service.RiskReview.model_validate(reply(instrument_ids=["industry"], case_ids=[], source_ids=[sources[0]["source_id"]])))
    with pytest.raises(ValueError, match="范围快照以外"):
        service.validate_result(run, service.RiskReview.model_validate(reply(instrument_ids=[], case_ids=[], source_ids=["tail:other:.95"])))


def test_post_only_accepts_scope_and_queues_existing_runner(client, monkeypatch):
    from watchlist_app.api.routes import risk_officer as route
    seed(client)
    calls = []
    monkeypatch.setattr(route, "harness_available", lambda: True)
    monkeypatch.setattr(route, "run_analysis", lambda run_id, token, issuer: calls.append(run_id))
    assert client.post("/api/risk/review/runs", json={"portfolio_id": "p1", "weights": {"risk-a": 1}}).status_code == 422
    assert client.post("/api/risk/review/runs", json={"watchlist_id": "risk-list", "instrument_id": "risk-a"}).status_code == 422
    response = client.post("/api/risk/review/runs", json={"instrument_id": "risk-a"})
    assert response.status_code == 202, response.text
    assert calls == [response.json()["run_id"]]
    assert response.json()["status"] == "queued"
    again = client.post("/api/risk/review/runs", json={"instrument_id": "risk-a"})
    assert again.json() == response.json() and len(calls) == 1


def read_required_pages(client, run_id):
    from watchlist_app.services.risk_read_projection import missing_required_reads
    with get_session_factory()() as session:
        reads = missing_required_reads(session.get(ResearchEntry, run_id).context_json)
    for read in reads:
        response = client.post(f"/api/research/runs/{run_id}/risk-read", json={key: value for key, value in read.items() if key != "tool"})
        assert response.status_code == 200, response.text


def test_large_risk_page_requires_actual_continuations_before_submission(client):
    import copy
    from watchlist_app.services.risk_read_projection import missing_required_reads
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id="risk-a")
        rid = run.entry_id
    service.prepare_run(rid)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, rid)
        context = copy.deepcopy(run.context_json)
        case = context["risk_inputs"]["research"][0]
        case["evidence_json"]["source_views"] = [{"source_id": "retained-source", "view": "原始完整来源及反证。" * 7000}]
        context["prior_inputs"] = copy.deepcopy(context["risk_inputs"])
        context["prior_inputs"]["research"][0]["body"] = "上次完整风险判断。" * 7000
        run.context_json, run.status = context, "running"
        immutable = {key: copy.deepcopy(context.get(key)) for key in ("risk_inputs", "prior_inputs", "cutoff")}
        prior_case = copy.deepcopy(session.get(RiskCase, "research").evidence_json)
        session.commit()
    # All original indexes/pages are delivered, but the large row is only a directory.
    read_required_pages(client, rid)
    response = client.post(f"/api/research/runs/{rid}/risk-draft", json=reply())
    assert response.status_code == 422
    remaining = response.json()["detail"]["missing_reads"]
    assert remaining and all(read.get("page_offset") is not None for read in remaining)
    with get_session_factory()() as session:
        delivered_before = copy.deepcopy(session.get(ResearchEntry, rid).context_json["risk_delivered_pages"])
    for invalid in (
        {"instrument_id": "risk-a", "section": "cases", "page_offset": 0, "path": ["outside"]},
        {"instrument_id": "outside", "section": "cases", "page_offset": 0, "path": []},
        {"instrument_id": "risk-a", "section": "cases", "page_offset": 0, "path": [], "offset": 9999},
        {"instrument_id": "risk-a", "section": "cases", "path": ["current"]},
    ):
        assert client.post(f"/api/research/runs/{rid}/risk-read", json=invalid).status_code == 422
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, rid).context_json["risk_delivered_pages"] == delivered_before
    calls = 0
    while remaining:
        for read in remaining:
            response = client.post(f"/api/research/runs/{rid}/risk-read",
                                  json={key: value for key, value in read.items() if key != "tool"})
            assert response.status_code == 200, response.text
            assert len(response.content) <= 48000
            calls += 1
        assert calls < 100
        with get_session_factory()() as session:
            context = session.get(ResearchEntry, rid).context_json
            remaining = missing_required_reads(context)
            assert "submitted_risk_review" not in context
            assert {key: context.get(key) for key in immutable} == immutable
        if remaining:
            assert client.post(f"/api/research/runs/{rid}/risk-draft", json=reply()).status_code == 422
    assert calls > 3
    assert client.post(f"/api/research/runs/{rid}/risk-draft", json=reply()).status_code == 200
    with get_session_factory()() as session:
        saved = session.get(ResearchEntry, rid)
        assert saved.status == "running" and "result" not in saved.context_json
        assert saved.context_json["submitted_risk_review"] == service.RiskReview.model_validate(reply()).model_dump(mode="json")
        assert session.get(RiskCase, "research").evidence_json == prior_case


def test_structured_submission_preserves_quotes_and_runner_uses_it_without_console_json(client, monkeypatch):
    from watchlist_app.services import research_runner
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id="risk-a")
        rid = run.entry_id
    payload = reply(analysis='分类为 "量化套利"，尚不能确认策略是否以对冲为目标。')
    assert client.post(f"/api/research/runs/{rid}/risk-draft", json=payload).status_code == 409

    class Process:
        returncode = 0
        def communicate(self, timeout):
            assert timeout == 1800
            read_required_pages(client, rid)
            response = client.post(f"/api/research/runs/{rid}/risk-draft", json=payload)
            assert response.status_code == 200, response.text
            with get_session_factory()() as session:
                pending = session.get(ResearchEntry, rid)
                assert pending.status == "running" and "result" not in pending.context_json
                assert pending.context_json["submitted_risk_review"]["priorities"][0]["analysis"] == payload["priorities"][0]["analysis"]
            return "", ""  # No second JSON serialization or prose reply is needed.
    monkeypatch.setattr(research_runner.subprocess, "Popen", lambda *a, **k: Process())
    research_runner.run_analysis(rid)
    with get_session_factory()() as session:
        saved = session.get(ResearchEntry, rid)
        assert saved.status == "completed"
        assert saved.context_json["result"]["priorities"][0]["analysis"] == payload["priorities"][0]["analysis"]
    assert client.post(f"/api/research/runs/{rid}/risk-draft", json=payload).status_code == 409


def test_structured_submission_rejects_scope_then_accepts_correction(client):
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id="risk-a")
        run.status = "running"
        run.context_json = {**run.context_json, "risk_inputs": service.read_snapshot(session, instrument_id="risk-a")}
        session.commit()
        rid = run.entry_id
    assert client.post(f"/api/research/runs/{rid}/risk-draft", json=reply(instrument_ids=["outside"])).status_code == 422
    with get_session_factory()() as session:
        assert "submitted_risk_review" not in session.get(ResearchEntry, rid).context_json
    read_required_pages(client, rid)
    assert client.post(f"/api/research/runs/{rid}/risk-draft", json=reply()).status_code == 200


def chart_series(points, currency="CNY", frequency="daily"):
    return {"research_returns": {"currency": currency, "metadata": {"return_kind": "total_return", "return_series_status": "complete", "quote_basis": "total_return_nav"},
        "frequency": {"resolved_frequency": frequency}, "points": [{"date": day, "value": value} for day, value in points]}}


def add_loss_series(session):
    session.add(InstrumentChartReadModel(instrument_id="risk-a", data_freshness_status="stale", payload_json=chart_series([
        ("2026-06-26", 2.6655), ("2026-06-30", 2.7565), ("2026-07-31", 2.6306), ("2026-08-31", 2.5756), ("2026-09-03", 2.548)])))
    session.commit()


def test_performance_without_trigger_or_peers_still_supports_independent_priority(client):
    seed(client)
    with get_session_factory()() as session:
        for cid in ("research", "price"):
            session.get(RiskCase, cid).status = "handled"
        add_loss_series(session)
        snapshot = service.read_snapshot(session, instrument_id="risk-a")
        assert snapshot["quantitative"] == [] and snapshot["research"] == []
        performance = snapshot["instruments"][0]["performance_evidence"]
        assert performance["sample_return_pct"] == pytest.approx(-4.4081785781)
        assert performance["trailing_negative_completed_observed_months"] == 2
        assert [row["latest_month_to_date"] for row in performance["monthly_periods"]] == [False, False, True]
        assert performance["monthly_periods"][0]["return_pct"] == pytest.approx((2.6306 / 2.7565 - 1) * 100)
        assert performance["comparisons"] == [] and "未配置基准" in performance["limitations"][0]
        run, _ = service.begin_run(session, instrument_id="risk-a")
        run.context_json = {**run.context_json, "risk_inputs": snapshot}
        with pytest.raises(ValueError, match="业绩或持仓来源"):
            service.apply_result(session, run, reply(case_ids=[], source_ids=["invented-benchmark"]))
        with pytest.raises(ValueError, match="独立研判需要引用"):
            service.apply_result(session, run, reply(case_ids=[]))
        service.apply_result(session, run, reply(case_ids=[], source_ids=[performance["source_id"]]))
        session.commit()
        saved = service.review_workspace(session, instrument_id="risk-a")["latest_completed"]
        assert saved["priorities"][0]["case_ids"] == []
        assert saved["evidence_sources"][performance["source_id"]]["start_date"] == "2026-06-26"


def test_explicit_comparison_uses_common_dates_and_rejects_frequency_or_currency_mismatch(client):
    seed(client)
    with get_session_factory()() as session:
        add_loss_series(session)
        other = InstrumentChartReadModel(instrument_id="risk-b", data_freshness_status="current", payload_json=chart_series([
            ("2026-06-30", 1), ("2026-07-31", 1.02), ("2026-08-31", 1.05), ("2026-09-03", 1.04)]))
        session.add(other)
        session.add(InstrumentManualProfile(instrument_id="risk-a", updated_at=datetime.now(UTC), nav_settings_json={"default_benchmark_instrument_id": "risk-b"}))
        session.commit()
        evidence = service.performance_evidence(session, "risk-a")
        compare = evidence["comparisons"][0]
        assert compare["role"] == "configured_benchmark"
        assert compare["comparison"]["sample_start"] == "2026-06-30"
        assert compare["comparison"]["observations"] == 4
        row = next(row for row in compare["comparison"]["rows"] if row["instrument_id"] == "risk-a")
        assert row["excess_return_pp"] == pytest.approx((2.548 / 2.7565 - 1) * 100 - 4)
        assert all(row["excess_return_pp"] < 0 for row in compare["monthly_periods"])
        series = other.payload_json["research_returns"]
        other.payload_json = {"research_returns": {**series, "currency": "USD"}}
        session.commit()
        evidence = service.performance_evidence(session, "risk-a")
        assert not evidence["comparisons"] and "币种不同" in evidence["limitations"][0]
        other.payload_json = {"research_returns": {**series, "frequency": {"resolved_frequency": "weekly"}}}
        session.commit()
        evidence = service.performance_evidence(session, "risk-a")
        assert not evidence["comparisons"] and "频率不同" in evidence["limitations"][0]


def test_same_day_completed_review_reruns_only_after_real_input_change(client):
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id="risk-a", scheduled_dates={"risk-a": "2026-09-08"})
        run.context_json = {**run.context_json, "risk_inputs": service.read_snapshot(session, instrument_id="risk-a")}
        service.apply_result(session, run, reply())
        session.commit()
        assert not service.begin_run(session, instrument_id="risk-a", scheduled_dates={"risk-a": "2026-09-08"})[1]
        session.get(RiskCase, "research").body = "今天的新原文改变了既有研究判断。"
        session.commit()
        updated, created = service.begin_run(session, instrument_id="risk-a", scheduled_dates={"risk-a": "2026-09-08"})
        assert created and updated.entry_id != run.entry_id
        assert not service.begin_run(session, instrument_id="risk-a", scheduled_dates={"risk-a": "2026-09-08"})[1]


def test_scheduled_risk_failure_uses_member_research_days_instead_of_beijing_date(client):
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, watchlist_id="risk-list",
            scheduled_dates={"risk-a": "2026-09-08", "risk-b": "2026-09-08"})
        run.status = "failed"
        session.commit()
        # One market may leave the due scope after its local midnight.
        same, created = service.begin_run(session, watchlist_id="risk-list", scheduled_dates={"risk-a": "2026-09-08"})
        assert not created and same.entry_id == run.entry_id
        next_day, created = service.begin_run(session, watchlist_id="risk-list",
            scheduled_dates={"risk-a": "2026-09-09", "risk-b": "2026-09-08"})
        assert created and next_day.entry_id != run.entry_id


def test_risk_input_keeps_longest_underwater_duration_separate_from_max_drawdown_episode(client):
    seed(client)
    with get_session_factory()() as session:
        raw = {"drawdown_summary": {"maximum": -9, "peak_date": "2026-06-25", "valley_date": "2026-07-30", "max_duration_months": 10}}
        record = InstrumentRiskReadModel(instrument_id="risk-a", data_freshness_status="current", payload_json=raw)
        session.add(record)
        session.commit()
        summary = service.read_snapshot(session, instrument_id="risk-a")["instruments"][0]["risk"]["drawdown_summary"]
        assert summary["longest_underwater_period_months"] == 10
        assert "max_duration_months" not in summary
        assert summary["peak_date"] == "2026-06-25" and summary["valley_date"] == "2026-07-30"
        assert "全样本统计" in summary["duration_note"]
        assert record.payload_json == raw and record.payload_json["drawdown_summary"]["max_duration_months"] == 10


@pytest.mark.parametrize("instrument_type", ["equity", "etf", "index", "public_fund", "private_fund", "crypto"])
def test_risk_reads_latest_research_coverage_and_prior_judgment_for_every_supported_type(client, monkeypatch, instrument_type):
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app import research_mcp
    seed(client)
    with get_session_factory()() as session:
        asset = session.get(InstrumentDetail, "risk-a")
        asset.instrument_type = asset.detail_view_type = instrument_type
        session.add(ResearchTopic(topic_id="retained-research", title="标的研究", instrument_ids=["risk-a"], visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="last-published", topic_id="retained-research", kind="analysis", title="研究",
            status="completed", created_at=datetime(2026, 9, 8, tzinfo=UTC), context_json={"sector_run": True,
                "instrument_ids": ["risk-a"], "cutoff": "2026-09-08T00:00:00+00:00", "reviews": {"risk-a": {
                    "status": "completed", "summary": "中期判断有条件成立", "coverage": [],
                    "research": {"investment_view": {"direction": "中期判断有条件成立", "risk": "需求修复仍待验证", "source_ids": [],
                        "updated_at": "2026-09-08T00:00:00+00:00", "source_run_id": "last-published",
                        "versions": [{"risk": "旧版本，不应重复注入"}]}}}}}))
        session.add(ResearchEntry(entry_id="later-quiet-check", topic_id="retained-research", kind="analysis", title="检查",
            status="completed", created_at=datetime(2026, 9, 8, 12, tzinfo=UTC), context_json={"sector_run": True,
                "instrument_ids": ["risk-a"], "cutoff": "2026-09-08T12:00:00+00:00", "reviews": {"risk-a": {
                    "status": "completed", "summary": "", "coverage": [], "research": None}}}))
        session.add(ResearchEntry(entry_id="latest-failed", topic_id="retained-research", kind="analysis", title="研究",
            status="failed", body="本次资料获取失败", created_at=datetime(2026, 9, 9, tzinfo=UTC), context_json={
                "sector_run": True, "instrument_ids": ["risk-a"], "cutoff": "2026-09-09T00:00:00+00:00"}))
        session.commit()

        snapshot = service.read_snapshot(session, instrument_id="risk-a")

    tracking = snapshot["instruments"][0]["research_tracking"]
    assert tracking["latest_check"]["status"] == "failed"
    assert tracking["latest_check"]["checked_at"] == "2026-09-09T00:00:00+00:00"
    assert tracking["current_judgment"]["summary"] == "中期判断有条件成立"
    assert tracking["current_judgment"]["view_updated_at"] == "2026-09-08T00:00:00+00:00"
    assert tracking["current_judgment"]["investment_view"] == {"direction": "中期判断有条件成立", "risk": "需求修复仍待验证",
        "source_ids": [], "updated_at": "2026-09-08T00:00:00+00:00", "source_run_id": "last-published"}
    assert "不是独立原始证据" in tracking["note"]
    assert any("最近一次研究未完成" in message for message in snapshot["limitations"])
    assert {case["case_id"] for case in snapshot["research"]} == {"research"}
    assert {case["case_id"] for case in snapshot["coverage"]} == set()  # A research gap is not a newly invented risk case.
    monkeypatch.setattr(research_mcp, "request", risk_request({"risk_run": True,
        "risk_inputs": snapshot, "cutoff": "2026-09-09T00:00:00+00:00"}))
    assert research_mcp.read_risk_instrument("risk-a")["current"]["instrument"]["research_tracking"] == tracking


def test_risk_binds_attributed_pm_views_active_questions_and_due_forecasts(client, monkeypatch):
    from watchlist_app.db.models.research import InstrumentResearchNote, InstrumentResearchProfile
    from watchlist_app.services import sector_research
    from watchlist_app import research_mcp
    seed(client)
    instant = datetime(2026, 9, 13, 2, tzinfo=UTC)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(service, "datetime", Clock)
    notebook = {"investment_view": None, "questions": [
        {"key": "demand", "question": "需求是否改善", "assessment": "暂时支持", "status": "supported", "tracking_status": "active",
         "evidence_for": ["订单改善"], "evidence_against": ["现金回收未改善"], "next_check": "核对现金流", "pm_note_id": "pm", "pm_note_revision": 3,
         "updated_at": "2026-09-12T08:00:00+00:00", "versions": [{"assessment": "旧判断"}]},
        {"key": "closed", "tracking_status": "closed"},
        {"key": "paused", "tracking_status": "paused"},
        {"key": "inactive-theme", "tracking_status": "active", "theme_id": "closed-theme"}],
        "forecasts": [
            {"key": "due", "claim": "需求恢复", "status": "active", "review_on": "2026-09-13", "invalidation": "订单重新下降"},
            {"key": "future", "claim": "长期验证", "status": "active", "review_on": "2026-10-01"},
            {"key": "condition", "claim": "等待公告", "status": "active", "observation_condition": "财报披露"},
            {"key": "withdrawn", "status": "withdrawn"},
            {"key": "closed-theme", "status": "active", "theme_id": "closed-theme"}]}
    completed = {"current_research": notebook, "summary": "旧摘要不可复活", "current_summary": "旧摘要不可复活"}
    def scoped_states(_, *, instrument_ids):
        assert instrument_ids == ["risk-a"]
        return {"latest": {"risk-a": {"status": "limited", "coverage": ["当前原文覆盖不足"]}},
                "last_completed": {"risk-a": completed}}
    monkeypatch.setattr(sector_research, "review_states", scoped_states)
    with get_session_factory()() as session:
        from investment_studio_instrument_core.db_models import Instrument
        from watchlist_app.db.models.workbench import ResearchTopic
        session.add(Instrument(instrument_id="risk-a", instrument_name="Risk A", instrument_type="etf", currency="CNY",
            exchange_code="XSHG", quote_selection_policy_json={}, source_settings_json={}))
        session.add(ResearchTopic(topic_id="dossier:risk-a", title="Research", instrument_ids=["risk-a"], visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="closed-theme", topic_id="dossier:risk-a", kind="note", title="Closed theme",
            context_json={"role": "research_theme", "instrument_id": "risk-a", "author": "PM", "theme_status": "closed"}))
        session.add(InstrumentResearchProfile(instrument_id="risk-a", research_stage="watching",
            thesis="订单变现支持中期收益", current_view="仍需等待现金流验证", disconfirming_evidence="应收继续增长会推翻逻辑",
            key_risks="回款及赎回流动性", monitoring_plan="检查下一季回款与赎回窗口", time_horizon="两个季度",
            portfolio_role="潜在收益来源，尚未配置", primary_analyst="研究负责人", updated_by="profile-editor",
            revision_number=4, created_at=instant, updated_at=instant))
        for note_id, extras in [
            ("pm", {"author": "投资经理甲", "author_user_id": "pm-one", "revision_number": 3}),
            ("unknown", {"author": "", "author_user_id": None}),
            ("other-team", {"team_id": "another-team"}),
            ("completed", {"completed_at": instant}),
            ("deleted", {"deleted_at": instant}),
            ("inactive-theme", {"research_context": {"theme_id": "closed-theme"}}),
        ]:
            values = {"instrument_id": "risk-a", "note_id": note_id, "note_date": date(2026, 9, 1), "note_type": "thesis_update",
                "title": "原始主张", "summary": "PM自己的不确定判断", "body": "如果订单改善，我倾向继续持有；现金流是反证。",
                "author": "投资经理乙", "team_id": "default", "created_at": instant, "updated_at": instant, **extras}
            session.add(InstrumentResearchNote(**values))
        session.commit()
        snapshot = service.read_snapshot(session, instrument_id="risk-a")
        assert snapshot == service.read_snapshot(session, instrument_id="risk-a")
        instrument = snapshot["instruments"][0]
        assert instrument["research_tracking"]["current_judgment"] is None
        assert instrument["research_tracking"]["latest_check"]["coverage"] == ["当前原文覆盖不足"]
        records = instrument["research_context"]["records"]
        profile = next(row for row in records if row["kind"] == "pm_profile")
        assert profile["source_id"] == "risk-pm-profile:risk-a:4"
        assert profile["value"]["current_view"] == "仍需等待现金流验证"
        assert profile["value"]["thesis"] == "订单变现支持中期收益"
        assert profile["value"]["disconfirming_evidence"] == "应收继续增长会推翻逻辑"
        assert profile["value"]["monitoring_plan"] == "检查下一季回款与赎回窗口"
        assert profile["value"]["time_horizon"] == "两个季度"
        profile_source = service.evidence_sources(snapshot)[profile["source_id"]]
        assert profile_source["author"] is None and profile_source["author_user_id"] is None
        assert profile_source["primary_analyst"] == "研究负责人" and profile_source["updated_by"] == "profile-editor"
        assert profile_source["revision_number"] == 4
        assert profile_source["recorded_at"] == profile["value"]["updated_at"]
        assert datetime.fromisoformat(profile_source["recorded_at"]).replace(tzinfo=UTC) == instant
        pm = next(row for row in records if row["kind"] == "pm_view" and row["value"]["note_id"] == "pm")
        assert pm["value"]["body"] == "如果订单改善，我倾向继续持有；现金流是反证。"
        assert pm["value"]["author"] == "投资经理甲" and pm["value"]["revision_number"] == 3
        assert {row["value"]["note_id"] for row in records if row["kind"] == "pm_view"} == {"pm", "unknown"}
        assert next(row["value"] for row in records if row["source_id"].startswith("risk-pm:risk-a:unknown:"))["author_user_id"] is None
        question = next(row["value"] for row in records if row["kind"] == "active_question")
        assert question["key"] == "demand" and question["evidence_against"] == ["现金回收未改善"]
        assert question["next_check"] == "核对现金流" and "versions" not in question
        assert {row["value"]["key"]: row["value"]["review_status"] for row in records if row["kind"] == "forecast_check"} == {
            "due": "due", "future": "scheduled", "condition": "condition_based"}
        evidence = service.evidence_sources(snapshot)[pm["source_id"]]
        assert evidence["author"] == "投资经理甲" and evidence["verification_status"] == "retained_judgment_not_independent_fact"
        run = ResearchEntry(context_json={"risk_inputs": snapshot})
        service.validate_result(run, service.RiskReview.model_validate(reply(case_ids=[], source_ids=[pm["source_id"]])))
        service.validate_result(run, service.RiskReview.model_validate(reply(case_ids=[], source_ids=[profile["source_id"]])))
        stored_profile = session.get(InstrumentResearchProfile, "risk-a")
        stored_profile.disconfirming_evidence, stored_profile.revision_number = "新反证条件", 5
        session.commit()
        assert service.read_snapshot(session, instrument_id="risk-a") != snapshot
        assert profile["value"]["disconfirming_evidence"] == "应收继续增长会推翻逻辑"
        note = session.get(InstrumentResearchNote, ("risk-a", "pm"))
        note.body, note.revision_number = "后续观点", 4
        session.commit()
        assert service.read_snapshot(session, instrument_id="risk-a") != snapshot
        assert pm["value"]["body"] != note.body  # The already bound evidence cannot drift.

    monkeypatch.setattr(research_mcp, "request", risk_request({"risk_run": True, "risk_inputs": snapshot, "cutoff": instant.isoformat()}))
    overview = research_mcp.read_risk_instrument("risk-a")
    assert "records" not in overview["current"]["instrument"]["research_context"]
    packet = research_mcp.read_risk_instrument("risk-a", section="research_context")
    assert packet["current"]["records"] == records
    assert packet["next_offset"] is None


def test_risk_pm_note_keeps_full_judgment_without_embedding_bound_original_corpus(client, monkeypatch):
    import asyncio
    import json
    from watchlist_app import research_mcp
    from watchlist_app.db.models.research import InstrumentResearchNote
    seed(client)
    sources = [
        {"source_id": "article", "source_type": "public_source", "document_id": "document-one", "version_id": "version-two",
         "title": "经营公告", "url": "https://example.com/article", "published_at": "2026-09-01", "observed_at": "2026-09-02T00:00:00Z",
         "body_sha256": "fixture-body-hash", "text": "公告原文" * 30000},
        {"source_id": "financials:fixture", "source_type": "company_snapshot", "instrument_id": "risk-a", "collected_at": "2026-09-02T00:00:00Z",
         "run_cutoff": "2026-09-02T12:00:00Z", "retrieved_at": "2026-09-03T00:00:00Z", "source_run_id": "financial-run",
         "period_end": "2026-06-30", "statement_type": "cash_flow", "company": {"statements": [{"detail": "完整财务数据" * 30000}]}}
    ]
    assert len(json.dumps(sources, ensure_ascii=False).encode()) > 50000
    with get_session_factory()() as session:
        note = InstrumentResearchNote(instrument_id="risk-a", note_id="source-heavy-pm", note_date=date(2026, 9, 3),
            note_type="thesis_update", title="当前主张", summary="仍待验证", body="原始主张、逻辑及反证" * 100,
            author="原作者", team_id="default", created_at=datetime(2026, 9, 3, tzinfo=UTC), updated_at=datetime(2026, 9, 3, tzinfo=UTC),
            research_context={"information_cutoff": "2026-09-03T00:00:00Z",
                "source_ids": ["article", "financials:fixture"], "sources": sources})
        session.add(note)
        session.commit()
        snapshot = service.read_snapshot(session, instrument_id="risk-a")
        record = snapshot["instruments"][0]["research_context"]["records"][0]
        assert record["value"]["body"] == note.body
        references = record["value"]["research_context"]["sources"]
        assert references[0]["version_id"] == "version-two" and references[0]["published_at"] == "2026-09-01"
        assert references[0]["body_sha256"] == "fixture-body-hash"
        assert references[1]["collected_at"] == "2026-09-02T00:00:00Z" and references[1]["period_end"] == "2026-06-30"
        assert references[1]["source_id"] == "financials:fixture" and references[1]["source_type"] == "company_snapshot"
        assert references[1]["run_cutoff"] == "2026-09-02T12:00:00Z" and references[1]["retrieved_at"] == "2026-09-03T00:00:00Z"
        assert references[1]["source_run_id"] == "financial-run"
        assert "text" not in references[0] and "company" not in references[1]
        assert note.research_context["sources"] == sources  # Only the risk projection is compacted.
    monkeypatch.setattr(research_mcp, "request", risk_request({"risk_run": True, "risk_inputs": snapshot, "cutoff": "2026-09-13T00:00:00Z"}))
    result = asyncio.run(research_mcp.mcp.call_tool("read_risk_instrument", {"instrument_id": "risk-a", "section": "research_context"}))
    assert len(result.content[0].text.encode()) <= 48000
    assert result.structured_content["current"]["records"] == [record]
    assert result.structured_content["next_offset"] is None


def test_new_limited_check_changes_risk_inputs_without_new_event_and_keeps_the_gap(client):
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services.risk_review_state import current_scope, input_version
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id="risk-a", scheduled_dates={"risk-a": "2026-09-09"})
        run.context_json = {**run.context_json, "risk_inputs": service.read_snapshot(session, instrument_id="risk-a"),
            "risk_input_version": input_version(session, current_scope(session, {"instrument_id": "risk-a"}))}
        service.apply_result(session, run, reply())
        session.add(ResearchTopic(topic_id="limited-research", title="研究", instrument_ids=["risk-a"], visibility="team"))
        session.flush()
        receipt = {"status": "insufficient_evidence", "summary": "只取得截至9月8日的资讯", "reviewed_update_ids": []}
        session.add(ResearchEntry(entry_id="limited-check", topic_id="limited-research", kind="analysis", title="检查",
            status="completed", created_at=datetime(2026, 9, 9, tzinfo=UTC), context_json={"sector_run": True,
                "instrument_ids": ["risk-a"], "cutoff": "2026-09-09T00:00:00+00:00", "reviews": {"risk-a": {
                    "status": "limited", "summary": "", "coverage": ["资讯尚未覆盖9月9日"], "reflection": receipt}}}))
        session.commit()

        state = service.review_workspace(session, instrument_id="risk-a")
        snapshot = service.read_snapshot(session, instrument_id="risk-a")
        updated, created = service.begin_run(session, instrument_id="risk-a", scheduled_dates={"risk-a": "2026-09-09"})

        assert state["latest_completed"]["stale"]
        assert created and updated.entry_id != run.entry_id
        assert snapshot["instruments"][0]["research_tracking"]["latest_check"]["reflection"] == receipt
        assert any("覆盖不足" in message for message in snapshot["limitations"])
        assert any("资讯尚未覆盖9月9日" in message for message in snapshot["limitations"])


def test_missing_research_and_normal_nav_disclosure_lag_remain_coverage_context(client):
    seed(client)
    with get_session_factory()() as session:
        session.get(InstrumentDetail, "risk-a").instrument_type = "private_fund"
        session.commit()
        snapshot = service.read_snapshot(session, instrument_id="risk-a")
        tracking = snapshot["instruments"][0]["research_tracking"]
        assert tracking["latest_check"] is None and tracking["current_judgment"] is None
        assert "正常净值披露滞后不等于研究失败" in tracking["note"]
        assert any("研究覆盖尚未确认" in message for message in snapshot["limitations"])
        assert {case["case_id"] for case in snapshot["research"]} == {"research"}


def test_scheduled_transient_risk_failure_requeues_same_authorized_run(client):
    from datetime import timedelta
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id='risk-a', scheduled_dates={'risk-a': '2026-09-25'})
        original_id = run.entry_id
        run.status, run.completed_at = 'failed', datetime.now(UTC) - timedelta(minutes=2)
        run.context_json = {**run.context_json, 'runtime_error': {'type': 'ProviderRateLimit', 'retryable': True},
                           'execution': {'attempt': 1}}
        session.commit()
        recovered, created = service.begin_run(session, instrument_id='risk-a', scheduled_dates={'risk-a': '2026-09-25'})
        assert created and recovered.entry_id == original_id and recovered.status == 'queued'
        assert recovered.context_json['execution']['resume']
        assert len(recovered.context_json['execution']['failures']) == 1


@pytest.mark.parametrize('subject', ['user', 'service'])
def test_live_role_or_service_scope_revocation_prevents_risk_publication(client, monkeypatch, subject):
    from dataclasses import replace
    from types import SimpleNamespace
    from studio_identity import Principal, principal_context
    from watchlist_app.services import research_runner as runner
    seed(client)
    original = (Principal('operator', 'Operator', 'default', credential='task-token') if subject == 'user' else
                Principal(None, 'Research service', 'default', kind='service', service_id='watchlist',
                          scopes=['watchlist:research'], credential='task-token'))
    with principal_context(original), get_session_factory()() as session:
        case = session.get(RiskCase, 'research')
        case.evidence_json = {**case.evidence_json, 'event_version_id': 'research:1', 'risk_assessment': {'status': 'pending'}}
        session.commit()
        run, _ = service.begin_run(session, instrument_id='risk-a')
        run_id = run.entry_id
    delegated = replace(original, resource_scope={'kind': 'run', 'id': run_id})
    revoked = replace(delegated, team_role='reader') if subject == 'user' else replace(delegated, scopes=[])
    finished = []
    monkeypatch.setattr(runner, 'resolve_token', lambda *args: revoked if finished else delegated)
    monkeypatch.setattr(runner, 'revoke_delegation', lambda *args: None)
    def launch(*args, **kwargs):
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            run.context_json = {**run.context_json, 'submitted_risk_review': {**reply(), 'case_assessments': [{
                'case_id': 'research', 'event_version_id': 'research:1', 'status': 'resolved', 'reason': '草稿拟结束风险'}]}}
            session.commit()
        finished.append(True)
        return SimpleNamespace(communicate=lambda **kwargs: ('', ''), returncode=0)
    monkeypatch.setattr(runner.subprocess, 'Popen', launch)
    runner.run_analysis(run_id, token='task-token')
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        case = session.get(RiskCase, 'research')
        assert run.status == 'failed' and run.context_json['runtime_error']['type'] == 'AuthorizationUnavailable'
        assert run.context_json['runtime_error']['status_code'] == 403
        assert case.trigger_active and case.status == 'open'
        assert case.evidence_json['risk_assessment'] == {'status': 'pending'}
        assert 'result' not in run.context_json


@pytest.mark.parametrize("initially_active", [True, False])
def test_identical_pending_assessments_across_scopes_do_not_republish_or_requeue(client, initially_active):
    from copy import deepcopy
    seed(client)
    dates = {"risk-a": "2026-09-27"}
    if not initially_active:
        with get_session_factory()() as session:
            case = session.get(RiskCase, "research")
            case.trigger_active, case.status = False, "recorded"
            case.evidence_json = {**case.evidence_json, "risk_assessment": {"status": "pending"}}
            session.commit()

    def assess(scope, reason="当前仍无新增证据。"):
        with get_session_factory()() as session:
            run, created = service.begin_run(session, **scope, scheduled_dates=dates)
            assert created
            run_id = run.entry_id
        service.prepare_run(run_id)
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            event = next(item for item in run.context_json["risk_inputs"]["research"] if item["case_id"] == "research")
            service.apply_result(session, run, {"summary": "等待更多证据。", "priorities": [], "limitations": [], "case_assessments": [{
                "case_id": "research", "event_version_id": event["evidence_json"]["event_version_id"],
                "status": "pending", "reason": reason}]})
            session.commit()
        return run_id

    first_id = assess({"instrument_id": "risk-a"})
    with get_session_factory()() as session:
        case = session.get(RiskCase, "research")
        original = deepcopy((case.evidence_json, case.history_json, case.updated_at))
        assert case.evidence_json["event_version_id"] == "research:1"
    second_id = assess({"watchlist_id": "risk-list"})
    with get_session_factory()() as session:
        case = session.get(RiskCase, "research")
        assert (case.evidence_json, case.history_json, case.updated_at) == original
        second = session.get(ResearchEntry, second_id)
        assert second.status == "completed" and second.completed_at
        assert second.context_json["result"]["case_assessments"][0]["reason"] == "当前仍无新增证据。"
        for scope, expected in (({"instrument_id": "risk-a"}, first_id), ({"watchlist_id": "risk-list"}, second_id)):
            run, created = service.begin_run(session, **scope, scheduled_dates=dates)
            assert not created and run.entry_id == expected
        case.status = "handled"  # Real PM work must still invalidate the retained inputs.
        session.commit()
        run, created = service.begin_run(session, instrument_id="risk-a", scheduled_dates=dates)
        assert created and run.entry_id != first_id


def test_revised_assessment_reason_remains_a_real_shared_case_update(client):
    seed(client)
    def assess(scope, reason):
        with get_session_factory()() as session:
            run, _ = service.begin_run(session, **scope, scheduled_dates={"risk-a": "2026-09-27"})
            run_id = run.entry_id
        service.prepare_run(run_id)
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            event = next(item for item in run.context_json["risk_inputs"]["research"] if item["case_id"] == "research")
            service.apply_result(session, run, {"summary": reason, "priorities": [], "limitations": [], "case_assessments": [{
                "case_id": "research", "event_version_id": event["evidence_json"]["event_version_id"],
                "status": "pending", "reason": reason}]})
            session.commit()
        return run_id
    first_id = assess({"instrument_id": "risk-a"}, "等待披露。")
    assess({"watchlist_id": "risk-list"}, "最新披露不足以确认价格影响，继续核查。")
    with get_session_factory()() as session:
        case = session.get(RiskCase, "research")
        assert len(case.history_json) == 2
        assert case.evidence_json["event_version_id"] == "research:1"
        run, created = service.begin_run(session, instrument_id="risk-a", scheduled_dates={"risk-a": "2026-09-27"})
        assert created and run.entry_id != first_id


@pytest.mark.parametrize('outcome', ['active', 'resolved', 'dismissed', 'pending'])
def test_pending_research_referrals_require_individual_assessment_before_completion(client, outcome):
    from copy import deepcopy
    from types import SimpleNamespace
    seed(client)
    with get_session_factory()() as session:
        for case_id, status in [('research', 'recorded'), ('uncertain', 'handled')]:
            case = session.get(RiskCase, case_id)
            case.status, case.trigger_active = status, False
            case.evidence_json = {**case.evidence_json, 'risk_assessment': {'status': 'pending'}}
        session.add(RiskCase(case_id='old-active', instrument_id='risk-a', signal='sector:old', title='既有活跃风险',
            severity='attention', status='open', trigger_active=True, evidence_json={'direction': 'risk'}))
        session.commit()
        run, _ = service.begin_run(session, watchlist_id='risk-list')
        run.status = 'running'
        session.commit()
        run_id = run.entry_id
    service.prepare_run(run_id)
    read_required_pages(client, run_id)
    response = client.post(f'/api/research/runs/{run_id}/risk-draft', json=reply())
    assert response.status_code == 422, response.text
    assert 'case_assessments' in response.text and 'research, uncertain' in response.text
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert run.status == 'running' and 'submitted_risk_review' not in run.context_json
        snapshot = run.context_json['risk_inputs']
        pending = [case for case in snapshot['research'] if case['case_id'] in {'research', 'uncertain'}]
        assessments = [{'case_id': case['case_id'], 'event_version_id': case['evidence_json']['event_version_id'],
            'status': outcome, 'reason': '依据本轮原始资料作出独立风险判断；证据不足时保留待确认。'} for case in pending]
        # Portfolio private judgment must not take ownership of shared referrals.
        private_snapshot = {**deepcopy(snapshot), 'scope': {'kind': 'portfolio', 'id': 'private'}}
        private = SimpleNamespace(context_json={'risk_inputs': private_snapshot})
        service.validate_result(private, service.RiskReview.model_validate(reply()))
        with pytest.raises(ValueError, match='组合私有'):
            service.validate_result(private, service.RiskReview.model_validate({**reply(), 'case_assessments': assessments}))
    partial = client.post(f'/api/research/runs/{run_id}/risk-draft', json={**reply(), 'case_assessments': assessments[:1]})
    assert partial.status_code == 422 and assessments[1]['case_id'] in partial.text
    payload = {**reply(), 'case_assessments': assessments}
    response = client.post(f'/api/research/runs/{run_id}/risk-draft', json=payload)
    assert response.status_code == 200, response.text
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        service.apply_result(session, run, run.context_json['submitted_risk_review'])
        session.commit()
        assert run.status == 'completed'
        for case_id in ('research', 'uncertain'):
            case = session.get(RiskCase, case_id)
            assert case.evidence_json['risk_assessment']['status'] == outcome
            assert case.evidence_json['risk_assessment']['reason']
        assert session.get(RiskCase, 'old-active').evidence_json == {'direction': 'risk'}
        assert session.get(RiskCase, 'price').history_json == []


def test_legacy_completed_report_without_pending_referral_receipt_is_requeued_once(client):
    seed(client)
    dates = {'risk-a': '2026-09-27'}
    with get_session_factory()() as session:
        case = session.get(RiskCase, 'research')
        case.evidence_json = {**case.evidence_json, 'risk_assessment': {'status': 'pending'}}
        session.commit()
        run, _ = service.begin_run(session, instrument_id='risk-a', scheduled_dates=dates)
        run_id = run.entry_id
    service.prepare_run(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        run.status = 'completed'
        run.context_json = {**run.context_json, 'result': reply()}  # Accepted by the former optional contract.
        session.commit()
        replacement, created = service.begin_run(session, instrument_id='risk-a', scheduled_dates=dates)
        assert created and replacement.entry_id != run_id
        same, created = service.begin_run(session, instrument_id='risk-a', scheduled_dates=dates)
        assert not created and same.entry_id == replacement.entry_id
