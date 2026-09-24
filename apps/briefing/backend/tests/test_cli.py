from io import BytesIO
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from briefing_app import cli


REPORT = {"report_id": "report-one", "report_type": "daily", "report_date": "2026-09-24", "version": 1, "status": "running"}


def response(value):
    return BytesIO(json.dumps(value).encode())


def scheduled_cli(monkeypatch, outcomes):
    calls = []
    monkeypatch.setattr(cli.sys, "argv", ["briefing", "daily", "--scheduled"])
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(edition_role="publisher", api_base_url="http://briefing/api/briefing"))
    monkeypatch.setattr(cli, "service_principal", lambda _: object())
    monkeypatch.setattr(cli, "principal_headers", lambda _: {"Authorization": "Bearer private-test-value"})
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    results = iter([{"edition_role": "publisher"}, REPORT, *outcomes])

    def open_request(request, **kwargs):
        calls.append(request)
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return response(result)

    monkeypatch.setattr(cli, "urlopen", open_request)
    return calls


@pytest.mark.parametrize("transient", [
    HTTPError("http://briefing/status", code, "temporarily unavailable", {}, None) for code in (502, 503, 504)
] + [URLError("connection refused"), TimeoutError(), ConnectionResetError()])
def test_status_recovers_without_resubmitting_the_report(monkeypatch, capsys, transient):
    calls = scheduled_cli(monkeypatch, [transient, {**REPORT, "status": "completed"}])
    cli.main()
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "completed"
    assert [request.get_method() for request in calls] == ["GET", "POST", "GET", "GET"]
    assert calls[2].full_url == calls[3].full_url == "http://briefing/api/briefing/reports/report-one/status"
    assert "private-test-value" not in output.err


@pytest.mark.parametrize("code", [401, 403, 404, 422, 500])
def test_status_deterministic_errors_are_not_retried(monkeypatch, code):
    calls = scheduled_cli(monkeypatch, [HTTPError("http://briefing/status", code, "failed", {}, None)])
    with pytest.raises(HTTPError) as error:
        cli.main()
    assert error.value.code == code
    assert len(calls) == 3


def test_status_retry_does_not_reset_the_original_deadline(monkeypatch):
    calls = scheduled_cli(monkeypatch, [HTTPError("http://briefing/status", 503, "failed", {}, None)])
    clock = iter([0, 1, 3901])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))
    with pytest.raises(SystemExit, match="65 minutes; inspect retained report report-one"):
        cli.main()
    assert len(calls) == 3


def test_recovered_terminal_failure_stays_failed(monkeypatch):
    calls = scheduled_cli(monkeypatch, [URLError("connection refused"), {**REPORT, "status": "failed", "error": "Review failed"}])
    with pytest.raises(SystemExit, match="Review failed"):
        cli.main()
    assert sum(request.get_method() == "POST" for request in calls) == 1


def test_submission_transport_failure_is_not_retried(monkeypatch):
    calls = []
    scheduled_cli(monkeypatch, [])

    def open_request(request, **kwargs):
        calls.append(request)
        if request.get_method() == "POST":
            raise URLError("response lost")
        return response({"edition_role": "publisher"})

    monkeypatch.setattr(cli, "urlopen", open_request)
    with pytest.raises(URLError):
        cli.main()
    assert [request.get_method() for request in calls] == ["GET", "POST"]
