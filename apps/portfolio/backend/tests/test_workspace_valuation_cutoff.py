from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.api.routes import workspace
from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioDailySnapshotModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import daily_snapshots, ledger, performance, portfolio_store
from scripts import refresh_release_snapshots


@pytest.fixture
def blocked_portfolio(monkeypatch, client):
    def create(*, has_priced_prefix: bool):
        pid = "workspace-gap"
        ref = dict(instrument_id="gap-equity", instrument_name="Gap Equity", instrument_type="equity", exchange_code="XNYS", currency="USD")
        prices = {"2026-08-05": 120}
        if has_priced_prefix:
            prices["2026-08-03"] = 100
        detail = dict(**ref, source_settings={"market_calendar": "XNYS", "expected_frequency": "daily"},
                      quote_selection_policy={role: ["close"] for role in ("valuation", "trading", "chart", "reference", "total_return")},
                      market_data=[dict(metric_family="price", quote_basis="close", as_of_date=day, value=str(value), currency="USD", price_unit="per_unit", price_scale=1, status="complete") for day, value in prices.items()])
        accounts = [dict(account_id="gap-cash", account_type="deposit_account", account_category="cash", currency="USD"),
                    dict(account_id="gap-broker", account_type="securities_account", account_category="security", currency="USD", cost_basis_method="fifo", default_settlement_cash_account_id="gap-cash")]
        accounts = [dict(**account, portfolio_id=pid, account_name=account["account_id"], institution="Scenario", opened_at="2026-08-03", status="active") for account in accounts]

        def fact(n, kind, day, amount, *, quantity=None):
            return dict(transaction_id=f"gap-{n}", transaction_sequence=n, portfolio_id=pid, transaction_type=kind,
                        trade_date=day, settlement_date=day, account_id="gap-broker" if quantity else "gap-cash",
                        settlement_cash_account_id="gap-cash" if kind == "buy" else None,
                        instrument_id=ref["instrument_id"] if quantity else None, instrument_ref=ref if quantity else None,
                        quantity=quantity, price=100 if quantity else None, gross_amount=amount, fees=0, taxes=0, currency="USD", created_at=day + "T12:00:00Z")

        facts = ([fact(1, "opening_balance", "2026-08-03", 1000), fact(2, "buy", "2026-08-03", 1000, quantity=10)]
                 if has_priced_prefix else [fact(1, "opening_balance", "2026-08-03", 1000, quantity=10)])
        facts.append(fact(3, "deposit", "2026-08-05", 1000))
        portfolio_store.reset_store(dict(portfolios=[dict(portfolio_id=pid, portfolio_name="Workspace gap", base_currency="USD", inception_date="2026-08-03", as_of_date="2026-08-05", valuation_timezone="America/New_York", valuation_cutoff_policy="latest_complete_eod", sort_order=0)], accounts=accounts, transactions=facts, taxonomies=[], taxonomy_nodes=[], taxonomy_assignments=[]))
        for module in (ledger, performance):
            monkeypatch.setattr(module, "get_shared_fx_rates", lambda: {"rates": []})
            monkeypatch.setattr(module, "get_registry_instrument_detail", lambda key: deepcopy(detail) if key == ref["instrument_id"] else None)
            monkeypatch.setattr(module, "list_registry_corporate_actions", lambda *a, **kw: [])
        for module in (ledger, workspace):
            monkeypatch.setattr(module, "get_registry_instrument_details", lambda keys: {key: deepcopy(detail) if key == ref["instrument_id"] else None for key in keys})
        monkeypatch.setattr(performance, "instrument_event_task_quality_warnings", lambda *a, **kw: [])
        daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(pid, end_date=date(2026, 8, 5))
        with get_session_factory()() as session:
            assert session.get(PortfolioCalculationStateModel, pid).daily_snapshot_status == "failed"
        return pid
    return create


def test_workspace_and_live_summary_use_valid_prefix_after_quote_gap(blocked_portfolio, client):
    pid = blocked_portfolio(has_priced_prefix=True)
    summary = client.get("/api/workspace/summary", params={"portfolio_id": pid})
    assert summary.status_code == 200
    assert summary.json()["as_of_date"] == "2026-08-03"
    assert summary.json()["nav"] == 1000
    holdings = client.get("/api/workspace/holdings", params={"portfolio_id": pid})
    assert holdings.status_code == 200
    assert holdings.json()["as_of_date"] == "2026-08-03"
    assert holdings.json()["totals"]["nav"] == 1000
    assert portfolio_store.get_portfolio_live_summary(pid)["nav"] == 1000


@pytest.mark.parametrize("has_priced_prefix", [True, False])
def test_holdings_cannot_rebuild_beyond_recorded_valuation_gap(blocked_portfolio, client, has_priced_prefix):
    pid = blocked_portfolio(has_priced_prefix=has_priced_prefix)
    response = client.get("/api/workspace/holdings", params={"portfolio_id": pid, "as_of_date": "2026-08-05"})
    assert response.status_code == 409
    assert "Required market data missing" in response.json()["detail"]


def test_workspace_without_valid_anchor_does_not_use_later_live_quote(blocked_portfolio, client):
    pid = blocked_portfolio(has_priced_prefix=False)
    summary = client.get("/api/workspace/summary", params={"portfolio_id": pid})
    assert summary.status_code == 200
    assert summary.json()["nav"] is None
    assert portfolio_store.get_portfolio_live_summary(pid)["nav"] is None
    holdings = client.get("/api/workspace/holdings", params={"portfolio_id": pid})
    assert holdings.status_code == 409


def test_release_accepts_current_published_prefix_and_reports_its_gap(blocked_portfolio, monkeypatch, capsys):
    pid = blocked_portfolio(has_priced_prefix=True)
    monkeypatch.setattr(
        refresh_release_snapshots, "_run_portfolio_daily_snapshot_recalculation_synchronously",
        lambda *_args: pytest.fail("Unchanged published data gap must not be retried"),
    )
    assert refresh_release_snapshots.main() == 0
    output = capsys.readouterr().out
    assert f"{pid}: reliable_through=2026-08-03, blocked_from=2026-08-04" in output
    assert "gap-equity valuation price" in output
    assert "1 valuation blocked" in output
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, pid)
        assert state.daily_snapshot_status == "failed"
        assert state.refreshed_to == date(2026, 8, 3)


@pytest.mark.parametrize("unpublished_reason", ["exception", "stale", "source_changed", "old_version"])
def test_release_rejects_failed_or_outdated_generation(blocked_portfolio, monkeypatch, unpublished_reason):
    pid = blocked_portfolio(has_priced_prefix=True)
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, pid)
        if unpublished_reason == "exception":
            state.error_message = "Accounting calculation crashed"
        elif unpublished_reason == "stale":
            state.daily_snapshot_status = "stale"
        elif unpublished_reason == "source_changed":
            state.source_market_data_updated_at = "2099-01-01T00:00:00Z"
        else:
            terminal = session.get(PortfolioDailySnapshotModel, (pid, date(2026, 8, 4)))
            terminal.snapshot_json = {**terminal.snapshot_json, "calculation_version": "outdated"}
        session.commit()
    # A refresh that did not publish current inputs must still fail the release.
    monkeypatch.setattr(
        refresh_release_snapshots, "_run_portfolio_daily_snapshot_recalculation_synchronously",
        lambda *_args: None,
    )
    with pytest.raises(RuntimeError, match=f"unpublished portfolios: {pid}"):
        refresh_release_snapshots.main()
