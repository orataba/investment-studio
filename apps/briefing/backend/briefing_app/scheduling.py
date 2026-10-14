"""Read the publisher's existing timers; never change their activation state."""
from datetime import UTC, datetime
import os
import platform
import subprocess


TIMERS = {kind: f"investment-studio-briefing-{kind}.timer" for kind in ("daily", "weekly")}


def _timestamp(value):
    if not value or value == "n/a":
        return None
    try:
        return datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return None


def read_schedules(edition_role):
    state = "not_managed" if edition_role != "publisher" or platform.system() != "Linux" else "unknown"
    result = {kind: {"state": state, "next_run_at": None, "last_trigger_at": None} for kind in TIMERS}
    if state == "not_managed":
        return result
    try:
        response = subprocess.run(
            ["systemctl", "--user", "show", *TIMERS.values(),
             "--property=Id,LoadState,ActiveState,UnitFileState,NextElapseUSecRealtime,LastTriggerUSec"],
            capture_output=True, text=True, timeout=2, check=False,
            env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return result
    if response.returncode:
        return result
    by_unit = {}
    for block in response.stdout.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        by_unit[fields.get("Id")] = fields
    for kind, unit in TIMERS.items():
        fields = by_unit.get(unit, {})
        if fields.get("LoadState") != "loaded":
            continue
        active, enabled = fields.get("ActiveState"), fields.get("UnitFileState")
        next_run = _timestamp(fields.get("NextElapseUSecRealtime"))
        if active == "active":
            state = "scheduled" if next_run else "unknown"
        elif active in {"inactive", "failed"}:
            state = "disabled" if enabled in {"disabled", "masked"} else "stopped"
        else:
            state = "unknown"
        result[kind] = {"state": state, "next_run_at": next_run if state == "scheduled" else None,
                        "last_trigger_at": _timestamp(fields.get("LastTriggerUSec"))}
    return result
