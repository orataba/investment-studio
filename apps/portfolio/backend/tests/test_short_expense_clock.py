"""A same-day short charge must remain calculable through the daily NAV kernel."""
from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import ledger, performance
from tests.test_ledger_review_repairs import ACCOUNTS, fact


@pytest.mark.parametrize("kind", ["fee", "tax"])
def test_same_day_short_charge_reconciles_snapshot_cash_liability_and_nav(monkeypatch, kind):
    detail = dict(instrument_id="short-stock", instrument_name="Short stock", instrument_type="equity", currency="USD",
        quote_selection_policy={"valuation": ["close"], "total_return": ["adjusted_close"]},
        source_settings={"expected_frequency": "daily", "market_calendar": "XNYS"},
        market_data=[dict(metric_family="price", quote_basis=basis, as_of_date="2026-01-05", value=100,
                          currency="USD", price_unit="per_unit", price_scale=1, status="complete")
                     for basis in ("close", "adjusted_close")])
    for module in (ledger, performance):
        monkeypatch.setattr(module, "get_registry_instrument_detail", lambda key: deepcopy(detail))
        monkeypatch.setattr(module, "list_registry_corporate_actions", lambda *args, **kw: [])
    monkeypatch.setattr(performance, "get_shared_fx_rates", lambda: {"rates": [], "maintained_pairs": [], "supported_currencies": ["USD"]})
    facts = [fact(1, "opening_balance", "2026-01-05", 10000, account="cash", trade_at="2026-01-05T08:00:00Z"),
             fact(2, "short_sell", "2026-01-05", 10000, 100, settlement_cash_account_id="cash"),
             fact(3, kind, "2026-01-05", 15, trade_at="2026-01-05T11:00:00Z", settlement_cash_account_id="cash")]
    for row in facts[1:]:
        row.update(instrument_id="short-stock", instrument_ref={key: detail[key] for key in ("instrument_id", "instrument_name", "instrument_type", "currency")})
    snapshots = performance.build_daily_portfolio_snapshots(
        {"portfolio_id": "audit", "base_currency": "USD", "inception_date": "2026-01-05",
         "valuation_timezone": "Asia/Shanghai", "valuation_cutoff_policy": "latest_complete_eod"},
        ACCOUNTS, facts, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
    assert len(snapshots) == 1
    assert snapshots[0]["nav"] == pytest.approx(9985)
    lots = ledger.build_position_lots("audit", ACCOUNTS, facts, as_of_date=date(2026, 1, 5), resolve_pricing=False, corporate_actions=[])
    assert lots[0]["remaining_quantity"] == -100
    assert lots[0]["expense_cash_amount"] == 15
    postings = ledger.derive_ledger_postings("audit", facts, corporate_actions=[])
    assert sum(row.get("cash_amount_delta") or 0 for row in postings) == 19985
