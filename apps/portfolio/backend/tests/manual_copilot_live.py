"""Explicit, paid-provider acceptance run; excluded from normal test discovery.

Run: python -m pytest tests/manual_copilot_live.py -s
Only a generated statement from the isolated fixture is sent to the provider.
"""
import os
import socket
import subprocess
import threading
from pathlib import Path
import sys
from decimal import Decimal


def render_statement(path):
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1500, 680), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 25)
    lines = [
        "SCENARIO BROKER - EXECUTED TRADE CONFIRMATION (SYNTHETIC QA)",
        "Account name: AI scenario securities; Currency: USD",
        "Trade date: 2026-01-05; Settlement date: 2026-01-06",
        "Execution ID: QA-ABBV-0001; Exchange: NYSE",
        "BUY 10 shares ABBV / AbbVie Inc. (common stock)",
        "Execution price: USD 150.00 per share",
        "Gross consideration: USD 1,500.00",
        "Commission: USD 2.00; Taxes: USD 0.00",
        "NET CASH DEBIT: USD 1,502.00; Status: FILLED",
        "",
        "Separate order QA-ABBV-0002: BUY 5 ABBV @ 140.00",
        "Status: CANCELLED; Filled quantity: 0; No cash movement",
    ]
    for index, line in enumerate(lines):
        draw.text((30, 25 + index * 48), line, fill="black", font=font)
    image.save(path, format="PNG")


def test_live_synthetic_broker_statement(client, tmp_path):
    import httpx2 as httpx
    import uvicorn
    from portfolio_app.main import app
    from portfolio_app.services.portfolio_store import list_transactions

    fixture = Path(os.environ["PORTFOLIO_QA_STATEMENT_IMAGE"])
    base = "/api/portfolios/investment-studio"
    cash = client.post(base + "/accounts", json={"account_name": "AI scenario cash", "account_category": "cash", "currency": "USD", "institution": "Scenario Broker", "opened_at": "2026-01-02"}).json()["account_id"]
    client.post(base + "/accounts", json={"account_name": "AI scenario securities", "account_category": "security", "currency": "USD", "institution": "Scenario Broker", "default_settlement_cash_account_id": cash, "opened_at": "2026-01-02"}).raise_for_status()
    capture = client.post(base + "/transaction-captures", files={"file": ("synthetic-broker-confirmation.png", fixture.read_bytes(), "image/png")})
    assert capture.status_code == 201, capture.text
    batch = client.post(base + "/transaction-capture-batches", json={"capture_ids": [capture.json()["capture_id"]], "purpose": "transaction_import"})
    assert batch.status_code == 201, batch.text
    batch_id = batch.json()["batch_id"]
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    before = len(list_transactions("investment-studio"))
    environment = {**os.environ, "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_API_BASE_URL": f"http://127.0.0.1:{port}/api", "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_DSH_HOME": str(tmp_path / "dsh"), "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PNPM": "/opt/homebrew/bin/pnpm"}
    # The existing credential file contains only the provider key; its contents
    # are loaded by the normal harness and never copied or printed by this run.
    script = Path(__file__).resolve().parents[1] / "scripts/run_portfolio_copilot_harness.sh"
    try:
        result = subprocess.run([str(script), "investment-studio", batch_id], env=environment, capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, "Harness failed; inspect the isolated harness logs (credentials are not printed)."
        record = httpx.get(f"http://127.0.0.1:{port}{base}/transaction-capture-batches/{batch_id}").json()
        analysis = record.get("latest_analysis")
        assert analysis, record
        proposal = analysis.get("transaction_import") or {}
        records = proposal.get("records") or []
        assert len(records) == 1, "Only the executed trade may be proposed; cancelled orders are not ledger facts."
        trade = records[0]
        assert trade["instrument_id"] == "equity-us-abbv" and trade["transaction_action"] == "buy"
        for field, expected in {"quantity": 10, "price": 150, "gross_amount": 1500, "fees": 2}.items():
            assert Decimal(str(trade[field])) == expected
        assert trade["trade_date"] == "2026-01-05" and trade["settlement_date"] == "2026-01-06"
        assert analysis["preview_error_count"] == 0
        print("LIVE_ANALYSIS", {"model": analysis["model_name"], "records": records, "preview_error_count": analysis["preview_error_count"]})
        assert len(list_transactions("investment-studio")) == before
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    render_statement(sys.argv[1])
