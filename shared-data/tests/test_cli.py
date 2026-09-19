from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import textwrap
import sys
from types import SimpleNamespace

import pytest


from studio_data import cli
from studio_data.commands import instruments, status
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
        instruments,
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
    monkeypatch.setattr(status, "get_settings", lambda: SimpleNamespace(
        email_sync_enabled=True, email_sync_ready=True, datahub_ready=True, fmp_ready=True,
    ))
    monkeypatch.setattr(status, "instrument_registry_name", lambda: "Instrument Data")
    monkeypatch.setattr(status, "list_instruments", lambda **_: [
        {"instrument_id": f"asset-{index}", "instrument_type": "index"} for index in range(13)
    ])
    result = status.get_data_status()
    assert result["registry_name"] == "Instrument Data"
    assert len(result["problems"]) == result["counts"]["missing_market_data"] == 13
    assert result["sync_readiness"] == {"email": True, "tushare": True, "fmp": True}


def test_cli_loads_only_the_selected_command_and_preserves_discovery(capsys, monkeypatch):
    loaded = []

    def search(q: str, limit: int = 10):
        return {"query": q, "limit": limit}

    def load(module):
        loaded.append(module)
        assert module == "studio_data.commands.security_search"
        return SimpleNamespace(search_security_records=search)

    monkeypatch.setattr(cli.importlib, "import_module", load)
    for arguments in (["data", "--help"], ["data", "nav", "--help"]):
        with pytest.raises(SystemExit) as exit_status:
            cli.main(arguments)
        assert exit_status.value.code == 0
        assert "import-csv" in capsys.readouterr().out or arguments == ["data", "--help"]
    assert loaded == []
    assert cli.main(["data", "securities", "search", "--limit", "12", "--", "GOOGL"]) == 0
    assert loaded == ["studio_data.commands.security_search"]
    assert json.loads(capsys.readouterr().out) == {"query": "GOOGL", "limit": 12}


def test_write_reports_committed_data_when_downstream_acknowledgement_fails(monkeypatch, capsys):
    from studio_data.services.downstream_notifications import (
        DownstreamRefreshError, DownstreamRefreshResult, DownstreamRequestFailure,
    )

    def write():
        raise DownstreamRefreshError(DownstreamRefreshResult(
            request_count=1,
            failures=(DownstreamRequestFailure("http://portfolio/api/refresh", "timeout"),),
        ))

    monkeypatch.setitem(cli.COMMANDS, ("securities", "add"), (write, True))
    assert cli.main(["data", "securities", "add", "--apply"]) == 3
    assert "Data saved" in json.loads(capsys.readouterr().out)["error"]


def test_cold_security_search_reads_current_catalog_without_loading_refresh_stack(tmp_path):
    # A fresh process is essential: the rest of this suite exercises writers and
    # has already imported calendars/collectors. Guard dependencies, not a flaky
    # machine-specific latency threshold.
    program = r'''
import contextlib
import importlib.abc
import io
import json
import sys

blocked = (
    "pandas", "exchange_calendars", "openpyxl", "xlrd", "studio_market.numeric",
    "studio_data.commands.instruments", "studio_data.commands.fx_rates",
    "studio_data.commands.status", "studio_data.commands.securities", "studio_identity",
    "studio_data.services.downstream_notifications", "studio_data.services.market_data_ops",
    "studio_data.services.instrument_reference", "studio_data.services.fmp.eod",
    "studio_data.services.fmp.eod_capture", "studio_data.services.fmp.fx",
)
class ReadDependenciesOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("Search imported a refresh dependency: " + fullname)
sys.meta_path.insert(0, ReadDependenciesOnly())

from studio_data.cli import main
from studio_data.db.base import Base
from studio_data.db.equity_models import FmpEquityCatalog
from studio_data.db.etf_models import FmpEtfCatalog
from studio_data.db.session import get_engine, get_session_factory
from investment_studio_instrument_core.db_models import InstrumentRegistryBase
from studio_data.services.instrument_store import create_instrument
import requests

def no_network(*args, **kwargs):
    raise AssertionError("Local directory search must not call the provider")
requests.Session.request = no_network
engine = get_engine()
Base.metadata.create_all(engine)
InstrumentRegistryBase.metadata.create_all(engine)
with get_session_factory()() as session:
    session.add(FmpEquityCatalog(fmp_symbol="GOOGL", exchange_ticker="GOOGL",
        company_name="Alphabet Inc.", exchange_code="XNAS", market="US", currency="USD"))
    session.add(FmpEtfCatalog(fmp_symbol="GGLL", exchange_ticker="GGLL",
        company_name="GOOGL Bull ETF", exchange_code="XNAS", market="US", currency="USD"))
    session.commit()
registered = create_instrument(instrument_name="Alphabet Inc.", instrument_type="equity",
    currency="USD", exchange_code="XNAS", identifiers=[
        {"identifier_type":"exchange_ticker", "identifier_value":"GOOGL", "is_primary":True},
        {"identifier_type":"provider_symbol", "identifier_value":"fmp:GOOGL", "is_primary":False}])

def search():
    with contextlib.redirect_stdout(io.StringIO()) as captured:
        code = main(["data", "securities", "search", "GOOGL", "--limit", "12"])
    assert code == 0
    return json.loads(captured.getvalue())

result = search()
assert result["catalog_errors"] == {}
assert [row["symbol"] for row in result["results"]] == ["GOOGL", "GGLL"]
assert result["results"][0]["existing_instrument_id"] == registered["instrument_id"]
assert result["results"][0]["exchange_code"] == "XNAS"
assert result["results"][1]["existing_instrument_id"] is None
with get_session_factory()() as session:
    session.get(FmpEquityCatalog, "GOOGL").company_name = "Alphabet Updated"
    session.commit()
assert search()["results"][0]["name"] == "Alphabet Updated"
print("Cold search uses current local catalog and only read dependencies.")
'''
    workspace = Path(__file__).resolve().parents[2]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("INVESTMENT_STUDIO_")}
    environment.update({
        "PYTHONPATH": os.pathsep.join((str(workspace / "shared-data"),
                                      str(workspace / "shared-data/instruments/python"))),
        "INVESTMENT_STUDIO_DATA_DATABASE_URL": f"sqlite+pysqlite:///{tmp_path / 'catalog.db'}",
        "INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA": "",
        "INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA": "",
    })
    completed = subprocess.run([sys.executable, "-c", textwrap.dedent(program)],
                               env=environment, text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
