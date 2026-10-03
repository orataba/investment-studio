import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from watchlist_app import research_mcp
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_workbench, research_runner
from watchlist_app.services.research_portfolio_projection import account_summary, read_portfolio_evidence
from .test_account_research_access import accounts, as_user


def accounts_workspace():
    return {"portfolio_id": "portfolio-a", "base_currency": "USD", "summary": {"account_count": 4},
        "accounts": [{"account": {"account_id": identifier, "account_name": name, "currency": currency,
                                    "account_type": "deposit_account", "account_category": "cash", "status": "active"},
                      "derived_cash_balance": balance, "pending_settlement": pending}
                     for identifier, name, currency, balance, pending in [
                         ("usd", "Operating cash", "USD", 1000, 0), ("hkd", "HKD reserve", "HKD", 3900, 0),
                         ("debt", "HKD financing", "HKD", -3900, 0), ("empty", "Unused EUR", "EUR", 0, 0)]]}


def test_native_accounts_keep_each_signed_balance_and_distinguish_zero_missing_and_netting():
    summary = account_summary(accounts_workspace(), as_of_date="2026-10-02")
    assert summary["status"] == "complete" and summary["total_rows"] == summary["returned_rows"] == 4
    assert [row["settled_cash"] for row in summary["rows"]] == [1000, 3900, -3900, 0]
    assert all(row["as_of_date"] == "2026-10-02" and not row["missing_fields"] for row in summary["rows"])
    currencies = {row["currency"]: row for row in summary["currency_balances"]}
    assert currencies["HKD"]["net_monetary_balance"] == "0"
    assert currencies["HKD"]["gross_monetary_balance"] == "7800"
    assert currencies["HKD"]["netting_status"] == "fully_offset"
    assert currencies["EUR"]["netting_status"] == "zero_balances"
    assert currencies["USD"]["net_monetary_balance"] == "1000"
    incomplete = accounts_workspace()
    incomplete["accounts"][2]["derived_cash_balance"] = None
    incomplete["accounts"][0]["pending_settlement"] = None
    summary = account_summary(incomplete, as_of_date="2026-10-02")
    assert summary["status"] == "partial"
    assert summary["rows"][2]["settled_cash"] is None and summary["rows"][2]["balance_status"] == "unavailable"
    assert summary["rows"][2]["missing_fields"] == ["settled_cash"]
    assert summary["rows"][0]["missing_fields"] == ["pending_settlement"]
    hkd = next(row for row in summary["currency_balances"] if row["currency"] == "HKD")
    assert hkd["net_settled_cash"] is None and hkd["gross_settled_cash"] is None
    assert hkd["netting_status"] == "unavailable"


def test_large_risk_and_many_accounts_have_explicit_pages_without_mutating_retained_facts():
    summary = account_summary(accounts_workspace(), as_of_date="2026-10-02")
    summary["rows"] = [dict(summary["rows"][0], account_id=str(index), account_name="Long account " * 20)
                       for index in range(400)]
    summary.update(total_rows=400, returned_rows=400)
    evidence = {"source_id": "portfolio:large", "retrieved_at": "2026-10-03T01:00:00Z", "result": {
        "portfolio_id": "portfolio-a", "account_summary": summary, "totals": {"nav": None},
        "forward_risk": {"status": "unavailable", "errors": ["Missing source " * 10000]}}}
    before = deepcopy(evidence)
    overview = read_portfolio_evidence(evidence)
    assert len(json.dumps(overview, ensure_ascii=False).encode()) < 48000
    assert overview["account_summary"]["total_rows"] == 400
    assert overview["account_summary"]["rows"]["read"]["section"] == "accounts"
    assert evidence == before


def wire(tool, **arguments):
    output = asyncio.run(research_mcp.mcp.call_tool(tool, arguments))
    assert len(output.content[0].text.encode()) <= 48000
    assert json.loads(output.content[0].text) == output.structured_content
    return output.structured_content


def test_tool_pages_keep_one_source_account_scope_and_complete_histories(client, monkeypatch):
    calls = []
    history = [{"date": f"observation-{index}", "value": index / 1000, "source": "original"} for index in range(6000)]
    workspace = {"portfolio_id": "portfolio-a", "portfolio_name": "Test portfolio", "base_currency": "USD",
        "as_of_date": "2026-10-02", "totals": {"nav": 1000},
        "rows": [{"holding_id": "hkd-cash", "history": history}],
        "forward_risk": {"status": "unavailable", "errors": ["Forward RC requires positive finite portfolio variance."],
                         "coverage": {"observations": 78, "status": "complete"}}}

    def external(service, path):
        assert service == "portfolio"
        parsed = urlsplit(path)
        calls.append(path)
        if parsed.path.endswith("/access"):
            return {"role": "reader"}
        if parsed.path == "/capabilities":
            return {"research_enabled": True}
        if parsed.path == "/portfolios":
            return [{"portfolio_id": "portfolio-a", "portfolio_name": "Test portfolio"}]
        if parsed.path == "/workspace/holdings":
            assert parse_qs(parsed.query)["portfolio_id"] == ["portfolio-a"]
            return deepcopy(workspace)
        assert parsed.path == "/portfolios/portfolio-a/accounts/workspace"
        assert parse_qs(parsed.query) == {"as_of_date": ["2026-10-02"], "include_valuation": ["false"]}
        return accounts_workspace()

    monkeypatch.setattr(research_workbench, "external_json", external)
    monkeypatch.setattr(research_runner, "harness_available", lambda: True)
    monkeypatch.setattr(research_runner, "run_analysis", lambda *args, **kwargs: None)
    topic = client.post("/api/research/topics", json={"title": "Accounts", "portfolio_id": "portfolio-a"}).json()
    run = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json={"question": "List every balance"}).json()
    run_id = run["entry_id"]

    def request(suffix, payload):
        response = client.post(f"/api/research/runs/{run_id}/{suffix}", json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(research_mcp, "request", request)
    overview = wire("read_portfolio_holdings")
    assert overview["account_summary"]["total_rows"] == 4
    assert [row["settled_cash"] for row in overview["account_summary"]["rows"]] == [1000, 3900, -3900, 0]
    assert overview["totals"]["nav"] == 1000
    assert overview["forward_risk"] == workspace["forward_risk"]
    assert "history" not in json.dumps(overview)
    source_id = overview["source_id"]
    risk = wire("read_portfolio_holdings", source_id=source_id, section="risk")["data"]
    assert risk["forward_risk"] == workspace["forward_risk"]
    assert risk["risk_context"] is None and "只在原风险页面读取" in risk["scope_note"]
    assert wire("read_portfolio_holdings")["source_id"] == source_id
    recovered, offset = [], 0
    while offset is not None:
        page = wire("read_portfolio_holdings", source_id=source_id, section="workspace",
                    path=["rows", 0, "history"], offset=offset, limit=100)
        recovered.extend(page["data"])
        offset = page["next_offset"]
    assert recovered == history
    assert len([path for path in calls if path.startswith("/workspace/holdings?")]) == 1
    assert len([path for path in calls if "/accounts/workspace?" in path]) == 1
    for parameters in [{"source_id": "portfolio:another-run"}, {"source_id": source_id, "path": ["unbound"]}]:
        response = client.post(f"/api/research/runs/{run_id}/read", json={"resource": "portfolio", "section": "workspace", **parameters})
        assert response.status_code == 422
    with get_session_factory()() as session:
        saved = session.get(ResearchEntry, run_id)
        original = deepcopy(saved.context_json)
        assert len(original["tool_evidence"]) == 1
        assert original["tool_evidence"][0]["result"]["rows"][0]["history"] == history
        saved.status = "draft"
        session.commit()
    page = wire("read_portfolio_holdings", source_id=source_id, section="accounts")
    assert page["total"] == 4
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, run_id).context_json == original
    assert client.post(f"/api/research/runs/{run_id}/tools", json={"tool": "portfolio"}).status_code == 409


@pytest.mark.parametrize("section,offset,path", [("accounts", 0, None), ("overview", 1, None), ("workspace", 0, ["rows"])])
def test_continuation_requires_a_returned_source_before_reading(section, offset, path, monkeypatch):
    monkeypatch.setattr(research_mcp, "request", lambda *_args: pytest.fail("Must not start another live read"))
    with pytest.raises(ValueError, match="source_id"):
        research_mcp.read_portfolio_holdings(section=section, offset=offset, path=path)


def test_account_read_failure_does_not_erase_nav_or_other_facts(monkeypatch):
    from watchlist_app.services.research_errors import ResearchInputUnavailable
    def external(_service, path):
        if path.startswith("/workspace/holdings?"):
            return {"as_of_date": "2026-10-02", "base_currency": "USD", "totals": {"nav": 1000}, "rows": []}
        raise ResearchInputUnavailable("Account ledger unavailable")
    monkeypatch.setattr(research_workbench, "external_json", external)
    result = research_workbench.portfolio_page_evidence("portfolio-a", None)
    assert result["totals"]["nav"] == 1000
    assert result["account_summary"]["status"] == "unavailable"
    assert result["account_summary"]["total_rows"] is None
    assert result["account_summary"]["rows"] == []


def test_absent_retained_accounts_are_unknown_not_an_empty_confirmed_ledger():
    evidence = {"source_id": "portfolio:unavailable", "retrieved_at": "2026-10-03T00:00:00Z", "result": {
        "portfolio_id": "A", "available": False, "reason": "Portfolio read unavailable"}}
    overview = read_portfolio_evidence(evidence)
    assert overview["account_summary"] is None
    assert read_portfolio_evidence(evidence, section="accounts")["data"] is None


def retained_portfolio_run(client, *, flags=None):
    summary = account_summary(accounts_workspace(), as_of_date="2026-10-02")
    evidence = {"source_id": "portfolio:current", "tool": "portfolio", "retrieved_at": "2026-10-03T00:00:00Z",
        "result": {"portfolio_id": "A", "as_of_date": "2026-10-02", "account_summary": summary}}
    context = {"portfolio_id": "A", "research_actor": {"kind": "user", "user_id": "alice"},
               "tool_evidence": [evidence, {**evidence, "source_id": "portfolio:foreign",
                   "result": {**evidence["result"], "portfolio_id": "B"}}], **(flags or {})}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="account-pages", title="Private portfolio", visibility="private",
                                  created_by_user_id="alice", portfolio_id="A"))
        session.flush()
        session.add(ResearchEntry(entry_id="account-run", topic_id="account-pages", kind="analysis",
                                  title="Account facts", status="draft", context_json=context))
        session.commit()
    return context


def test_every_saved_account_page_rechecks_private_actor_scope_and_current_portfolio_grant(accounts):
    client, principals, grants = accounts
    retained_portfolio_run(client)
    principals["run-A"] = replace(principals["alice"], credential="run-A", resource_scope={"kind": "run", "id": "account-run"})
    payload = {"resource": "portfolio", "source_id": "portfolio:current", "section": "accounts", "limit": 1}
    path = "/api/research/runs/account-run/read"
    as_user(client, "run-A")
    for offset, balance in enumerate([1000, 3900, -3900, 0]):
        response = client.post(path, json={**payload, "offset": offset})
        assert response.status_code == 200, response.text
        assert response.json()["data"][0]["settled_cash"] == balance
    # Even another retained receipt cannot expand this run's linked portfolio.
    assert client.post(path, json={**payload, "source_id": "portfolio:foreign"}).status_code == 422
    assert client.post(path.replace("account-run", "other-run"), json=payload).status_code == 403
    as_user(client, "bob")
    assert client.get("/api/research/runs/account-run/context").status_code == 404
    principals["forged-run"] = replace(principals["bob"], credential="forged-run", resource_scope={"kind": "run", "id": "account-run"})
    assert as_user(client, "forged-run").post(path, json=payload).status_code == 403
    as_user(client, "run-A")
    grants["alice"].clear()
    denied = client.post(path, json={**payload, "offset": 1})
    assert denied.status_code == 404
    assert "HKD reserve" not in denied.text
    principals.pop("run-A")
    assert client.post(path, json=payload).status_code == 401


@pytest.mark.parametrize("flags", [{"portfolio_id": None}, {"risk_run": True}, {"sector_run": True}])
def test_portfolio_pages_cannot_escape_into_unlinked_or_automatic_research(accounts, flags):
    client, principals, _ = accounts
    retained_portfolio_run(client, flags=flags)
    principals["run-A"] = replace(principals["alice"], credential="run-A", resource_scope={"kind": "run", "id": "account-run"})
    response = as_user(client, "run-A").post("/api/research/runs/account-run/read",
        json={"resource": "portfolio", "source_id": "portfolio:current", "section": "accounts"})
    assert response.status_code == 422


def test_summary_keeps_each_debt_and_pending_cash_without_treating_unknown_currency_as_zero():
    workspace = accounts_workspace()
    debt = workspace["accounts"][2]
    debt["derived_cash_balance"] = "-2000.125"
    debt["pending_settlement"] = "-1899.875"
    workspace["accounts"].append({"account": {**debt["account"], "account_id": "debt-two", "account_name": "Second financing"},
                                   "derived_cash_balance": "-0.25", "pending_settlement": "0.25"})
    workspace["summary"]["account_count"] = 5
    summary = account_summary(workspace, as_of_date="2026-10-02")
    assert summary["total_rows"] == summary["returned_rows"] == 5
    hkd = next(row for row in summary["currency_balances"] if row["currency"] == "HKD")
    assert hkd["netting_status"] == "fully_offset"
    assert hkd["net_monetary_balance"] == "0.000"
    assert hkd["gross_monetary_balance"] == "7800.500"
    workspace["accounts"][-1]["account"]["currency"] = None
    missing = account_summary(workspace, as_of_date="2026-10-02")
    assert missing["status"] == "partial" and missing["rows"][-1]["missing_fields"] == ["currency"]
    unknown = next(row for row in missing["currency_balances"] if row["currency"] is None)
    assert unknown["netting_status"] == "unavailable" and unknown["net_monetary_balance"] is None
