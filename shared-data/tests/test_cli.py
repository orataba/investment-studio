from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace

import pytest


from studio_data import cli
from studio_data.contracts import StudioMarketDataUpsertRequest


def test_write_requires_apply_and_preserves_typed_validation(monkeypatch, capsys):
    calls = []

    def write(payload: StudioMarketDataUpsertRequest):
        calls.append(payload)
        return {"saved": True}

    monkeypatch.setitem(cli.COMMANDS, ("quotes", "set"), (write, True))
    payload = {
        "metric_family": "price",
        "quote_basis": "close",
        "as_of_date": "2026-09-04",
        "value": "100",
        "currency": "USD",
        "status": "complete",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["data", "quotes", "set", "--input", "-"]) == 0
    assert calls == []
    assert json.loads(capsys.readouterr().out)["executed"] is False
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["data", "quotes", "set", "--input", "-", "--apply"]) == 0
    assert calls[0].value == 100


def test_nav_import_without_apply_previews_local_file(monkeypatch, tmp_path, capsys):
    file = tmp_path / "nav.csv"
    file.write_text("date,nav\n2026-09-04,1.05\n")
    calls = []
    monkeypatch.setattr(
        cli.instruments,
        "preview_instrument_nav_history",
        lambda instrument_id, payload: (
            calls.append((instrument_id, payload.decoded_bytes())) or {"row_count": 1}
        ),
    )
    assert cli.main(["data", "nav", "import", "fund-1", str(file)]) == 0
    assert calls == [("fund-1", file.read_bytes())]
    assert json.loads(capsys.readouterr().out)["row_count"] == 1




def test_csv_command_forwards_options_without_mutating_process_arguments(monkeypatch):
    before = sys.argv
    observed = []
    monkeypatch.setattr(cli.importlib, "import_module", lambda _: SimpleNamespace(
        main=lambda: observed.append(sys.argv[1:]) or 0,
    ))
    options = ["--source-mode", "email", "nav.csv", "--apply"]
    assert cli.main(["data", "nav", "import-csv", *options]) == 0
    assert observed == [options]
    assert sys.argv is before


def test_catalog_help_does_not_refresh_data(monkeypatch, capsys):
    from scripts import refresh_release_catalogs

    def unexpected_refresh():
        pytest.fail("--help must never refresh provider catalogs")

    monkeypatch.setattr(refresh_release_catalogs, "sync_security_catalogs", unexpected_refresh)
    with pytest.raises(SystemExit) as result:
        cli.main(["data", "jobs", "catalogs", "--help"])
    assert result.value.code == 0
    assert "Refresh provider-backed search catalogs" in capsys.readouterr().out


@pytest.mark.parametrize("group,action,result,exit_code", [
    ("reference", "refresh", {"section_errors": {"profile": "Unavailable"}}, 1),
    ("reference", "refresh", {"section_errors": {}}, 0),
    ("reference", "show", None, 2),
    ("refresh", "instrument", {"refresh_status": {"status": "failed"}}, 1),
    ("refresh", "batch", {"results": [{"status": "blocked"}]}, 1),
    ("refresh", "batch", {"results": [{"status": "refreshed"}]}, 0),
])
def test_cli_reports_execution_failure_in_exit_status(monkeypatch, capsys, group, action, result, exit_code):
    writes = action != "show"
    monkeypatch.setitem(cli.COMMANDS, (group, action), (lambda: result, writes))
    assert cli.main(["data", group, action, *(["--apply"] if writes else [])]) == exit_code
    assert capsys.readouterr().out


@pytest.mark.parametrize("arguments", [
    ["instruments", "list", "--limit", "0"],
    ["instruments", "list", "--search", " "],
    ["securities", "search", "AAPL", "--limit", "26"],
    ["securities", "search", " "],
])
def test_cli_preserves_search_input_bounds(arguments, capsys):
    assert cli.main(["data", *arguments]) == 2
    assert "error" in json.loads(capsys.readouterr().out)


def test_data_status_reports_all_issues_and_provider_readiness(monkeypatch):
    monkeypatch.setattr(cli.status, "get_settings", lambda: SimpleNamespace(
        email_sync_enabled=True, email_sync_ready=True, datahub_ready=True, fmp_ready=True,
    ))
    monkeypatch.setattr(cli.status, "instrument_registry_name", lambda: "Instrument Data")
    monkeypatch.setattr(cli.status, "list_instruments", lambda **_: [
        {"instrument_id": f"asset-{index}", "instrument_type": "index"} for index in range(13)
    ])
    result = cli.status.get_data_status()
    assert result["registry_name"] == "Instrument Data"
    assert len(result["problems"]) == result["counts"]["missing_market_data"] == 13
    assert result["sync_readiness"] == {"email": True, "tushare": True, "fmp": True}
