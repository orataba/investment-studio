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
    assert len(peer_reads) == 1
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
    monkeypatch.setattr(service, "external_json", lambda *args: context)
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
    monkeypatch.setattr(research_mcp, "request", lambda _: {
        "risk_run": True, "cutoff": "2026-09-08", "risk_inputs": snapshot})
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
    monkeypatch.setattr(route, "run_analysis", lambda run_id, token: calls.append(run_id))
    assert client.post("/api/risk/review/runs", json={"portfolio_id": "p1", "weights": {"risk-a": 1}}).status_code == 422
    assert client.post("/api/risk/review/runs", json={"watchlist_id": "risk-list", "instrument_id": "risk-a"}).status_code == 422
    response = client.post("/api/risk/review/runs", json={"instrument_id": "risk-a"})
    assert response.status_code == 202, response.text
    assert calls == [response.json()["run_id"]]
    assert response.json()["status"] == "queued"
    again = client.post("/api/risk/review/runs", json={"instrument_id": "risk-a"})
    assert again.json() == response.json() and len(calls) == 1


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
