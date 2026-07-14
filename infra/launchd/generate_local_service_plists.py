from __future__ import annotations

import argparse
import os
import plistlib
import tempfile
from pathlib import Path


APP_SERVICES = (
    "platform-api",
    "watchlist-api",
    "portfolio-api",
    "platform-web",
    "watchlist-web",
    "portfolio-web",
)
MARKET_DATA_REFRESH_SERVICE = "market-data-refresh"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Portfolio Operations user LaunchAgent plists.")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--python-bin", required=True)
    parser.add_argument("--node-bin", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--label-prefix", required=True)
    parser.add_argument("--launch-agents-dir", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--env-root", type=Path, required=True)
    parser.add_argument("--refresh-hour", type=int, default=21)
    parser.add_argument("--refresh-minute", type=int, default=0)
    return parser.parse_args()


def _write_plist(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            plistlib.dump(payload, output, sort_keys=False)
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    args = _parse_args()
    if not 0 <= args.refresh_hour <= 23:
        raise SystemExit("--refresh-hour must be between 0 and 23")
    if not 0 <= args.refresh_minute <= 59:
        raise SystemExit("--refresh-minute must be between 0 and 59")

    project_root = args.project_root.resolve()
    launch_agents_dir = args.launch_agents_dir.expanduser().resolve()
    log_dir = args.log_dir.expanduser().resolve()
    env_root = args.env_root.expanduser().resolve()
    service_runner = str(project_root / "infra" / "launchd" / "run_local_service.sh")
    refresh_runner = str(project_root / "infra" / "launchd" / "run_market_data_refresh.sh")

    for service in APP_SERVICES:
        label = f"{args.label_prefix}.{service}"
        payload: dict[str, object] = {
            "Label": label,
            "ProgramArguments": [
                service_runner,
                service,
                str(project_root),
                args.python_bin,
                args.node_bin,
                str(env_root),
            ],
            "WorkingDirectory": str(project_root),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Interactive",
            "ThrottleInterval": 5,
            "Umask": 0o077,
            "EnvironmentVariables": {
                "PORTFOLIO_OPS_LOCAL_DATABASE_URL": args.database_url,
            },
            "StandardOutPath": str(log_dir / f"{service}.log"),
            "StandardErrorPath": str(log_dir / f"{service}.error.log"),
        }
        _write_plist(launch_agents_dir / f"{label}.plist", payload)

    refresh_label = f"{args.label_prefix}.{MARKET_DATA_REFRESH_SERVICE}"
    refresh_payload: dict[str, object] = {
        "Label": refresh_label,
        "ProgramArguments": [
            refresh_runner,
            str(project_root),
            args.python_bin,
            str(project_root / "var" / "market-data-refresh.lock"),
            str(env_root),
        ],
        "WorkingDirectory": str(project_root / "apps" / "platform" / "backend"),
        "RunAtLoad": False,
        "KeepAlive": False,
        "StartCalendarInterval": {
            "Hour": args.refresh_hour,
            "Minute": args.refresh_minute,
        },
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "ThrottleInterval": 60,
        "Umask": 0o077,
        "EnvironmentVariables": {
            "PORTFOLIO_OPS_LOCAL_DATABASE_URL": args.database_url,
        },
        "StandardOutPath": str(log_dir / f"{MARKET_DATA_REFRESH_SERVICE}.log"),
        "StandardErrorPath": str(log_dir / f"{MARKET_DATA_REFRESH_SERVICE}.error.log"),
    }
    _write_plist(launch_agents_dir / f"{refresh_label}.plist", refresh_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
