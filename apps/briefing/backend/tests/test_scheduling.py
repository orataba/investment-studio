from types import SimpleNamespace
import subprocess

import pytest
from briefing_app import scheduling


def timer(kind, active="active", enabled="enabled", next_run="Thu 2026-10-15 00:30:00 UTC"):
    return (f"Id=investment-studio-briefing-{kind}.timer\nLoadState=loaded\nActiveState={active}\n"
            f"UnitFileState={enabled}\nNextElapseUSecRealtime={next_run}\nLastTriggerUSec=Wed 2026-10-14 00:30:00 UTC")


def test_publisher_reads_actual_activation_and_utc_dates(monkeypatch):
    monkeypatch.setattr(scheduling.platform, "system", lambda: "Linux")
    calls = []
    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(returncode=0, stdout=timer("daily") + "\n\n" + timer("weekly", "inactive", "disabled", "n/a"))
    monkeypatch.setattr(scheduling.subprocess, "run", run)
    result = scheduling.read_schedules("publisher")
    assert result["daily"] == {"state": "scheduled", "next_run_at": "2026-10-15T00:30:00+00:00", "last_trigger_at": "2026-10-14T00:30:00+00:00"}
    assert result["weekly"]["state"] == "disabled" and result["weekly"]["next_run_at"] is None
    assert calls[0][0][:3] == ["systemctl", "--user", "show"]
    assert calls[0][1]["timeout"] == 2 and calls[0][1]["env"]["TZ"] == "UTC"


@pytest.mark.parametrize("output, expected", [
    (timer("daily", "inactive", "enabled"), "stopped"),
    (timer("daily", "active", "disabled"), "scheduled"),
    (timer("daily", next_run="n/a"), "unknown"),
    (timer("daily", next_run="bad-date"), "unknown"),
    ("Id=investment-studio-briefing-daily.timer\nLoadState=not-found", "unknown"),
])
def test_unknown_state_is_never_reported_as_paused(monkeypatch, output, expected):
    monkeypatch.setattr(scheduling.platform, "system", lambda: "Linux")
    monkeypatch.setattr(scheduling.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=output))
    assert scheduling.read_schedules("publisher")["daily"]["state"] == expected


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("systemctl", 2)])
def test_failed_status_read_is_unknown(monkeypatch, error):
    monkeypatch.setattr(scheduling.platform, "system", lambda: "Linux")
    def run(*a, **k):
        raise error
    monkeypatch.setattr(scheduling.subprocess, "run", run)
    assert scheduling.read_schedules("publisher")["daily"]["state"] == "unknown"


@pytest.mark.parametrize("system, role", [("Darwin", "publisher"), ("Linux", "preview")])
def test_preview_does_not_probe_a_cloud_timer(monkeypatch, system, role):
    monkeypatch.setattr(scheduling.platform, "system", lambda: system)
    monkeypatch.setattr(scheduling.subprocess, "run", lambda *a, **k: pytest.fail("must not inspect unmanaged timers"))
    assert scheduling.read_schedules(role)["daily"]["state"] == "not_managed"
