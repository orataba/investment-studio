"""Control existing service groups without coupling their data or dependencies."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import pwd
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
GROUPS = {
    "home": ("home-api", "home-web"),
    "briefing": ("briefing-api", "briefing-web", "briefing-daily", "briefing-weekly"),
    "market": tuple(f"market-{action}" for action in (
        "daily", "weekly", "publish", "sync", "registered-prices-cn", "registered-prices-hk", "registered-prices-us", "registered-prices-eu")),
    "investments": (
        "watchlist-api", "watchlist-web", "portfolio-api", "portfolio-web",
        "market-data-refresh", "cn-market-data-refresh",
        "hk-market-data-refresh", "us-market-data-refresh",
        "cn-hk-reference-data-refresh", "us-reference-data-refresh",
    ),
}


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=check)


def manage(group: str, action: str) -> None:
    mac = platform.system() == "Darwin"
    if group == "regime":
        if mac:
            helper = ROOT / "apps/regime/deploy/launchd/install.sh"
        else:
            # Use the installed release, not an unrelated checkout version.
            values = dict(
                line.split("=", 1)
                for line in Path("/etc/regime-dashboard/runtime.env").read_text().splitlines()
                if "=" in line and not line.startswith("#")
            )
            release = Path(values["REGIME_DASHBOARD_APP_ROOT"])
            if action == "status":
                run([str(release / "deploy.sh"), "status"])
                return
            helper = release / "deploy/systemd/install.sh"
        if action == "restart":
            run([str(helper), "stop"])
            run([str(helper), "start"])
        else:
            run([str(helper), action])
        return

    services = GROUPS[group]
    if mac and group == "briefing":
        services = ("briefing-api", "briefing-web")
    if mac and group == "market":
        services = ("market-sync",)
    scheduled = lambda service: service.endswith("-data-refresh") or service in GROUPS["market"] or service in {"briefing-daily", "briefing-weekly"}
    if mac:
        domain = f"gui/{os.getuid()}"
        for service in services:
            label = f"com.orataba.investment-studio.{service}"
            target = f"{domain}/{label}"
            state_result = subprocess.run(
                ["launchctl", "print", target], capture_output=True, text=True
            )
            loaded = state_result.returncode == 0
            if action == "status":
                state = re.search(r"^\s*state = (.+)$", state_result.stdout, re.MULTILINE)
                status = state.group(1) if loaded and state else "stopped"
                print(f"{service}: {status}", flush=True)
                continue
            if action == "restart" and loaded:
                if not scheduled(service):
                    run(["launchctl", "kickstart", "-k", target])
                continue
            if action == "stop" and loaded:
                run(["launchctl", "bootout", target])
                loaded = False
            if action in {"start", "restart"} and not loaded:
                plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
                run(["launchctl", "enable", target])
                run(["launchctl", "bootstrap", domain, str(plist)])
        return

    units = [f"investment-studio-{name}.service" for name in services]
    for service in services:
        if not scheduled(service):
            continue
        timer = f"investment-studio-{service}.timer"
        # Starting a group enables its schedule, not an unsolicited data refresh.
        if action in {"start", "restart"}:
            units.remove(f"investment-studio-{service}.service")
        units.append(timer)
    command = ["systemctl", "--user", "--no-pager", action, *units]
    if os.geteuid() == 0:
        account = pwd.getpwnam("investment-studio")
        command = [
            "runuser", "-u", account.pw_name, "--", "env",
            f"XDG_RUNTIME_DIR=/run/user/{account.pw_uid}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{account.pw_uid}/bus",
            *command,
        ]
    result = run(command, check=action != "status")
    # systemctl status returns 3 for a deliberately stopped group.
    if action == "status" and result.returncode not in {0, 3}:
        raise SystemExit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "restart", "status"))
    parser.add_argument("group", choices=(*GROUPS, "regime", "all"))
    args = parser.parse_args()
    groups = (*GROUPS, "regime") if args.group == "all" else (args.group,)
    for group in groups:
        print(f"[{group}]", flush=True)
        manage(group, args.action)


if __name__ == "__main__":
    main()
