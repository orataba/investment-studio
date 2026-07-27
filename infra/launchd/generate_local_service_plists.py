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
    parser.add_argument("--label-prefix", required=True)
    parser.add_argument("--launch-agents-dir", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--env-root", type=Path, required=True)
    parser.add_argument("--refresh-hour", type=int, default=21)
    parser.add_argument("--refresh-minute", type=int, default=0)
    parser.add_argument("--refresh-retry-hour", type=int, default=23)
    parser.add_argument("--refresh-retry-minute", type=int, default=0)
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
    database_url = os.environ.get("PORTFOLIO_OPS_LOCAL_DATABASE_URL", "").strip()
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise SystemExit(
            "PORTFOLIO_OPS_LOCAL_DATABASE_URL must be an explicit PostgreSQL URL"
        )
    if not 0 <= args.refresh_hour <= 23:
        raise SystemExit("--refresh-hour must be between 0 and 23")
    if not 0 <= args.refresh_minute <= 59:
        raise SystemExit("--refresh-minute must be between 0 and 59")
    if not 0 <= args.refresh_retry_hour <= 23:
        raise SystemExit("--refresh-retry-hour must be between 0 and 23")
    if not 0 <= args.refresh_retry_minute <= 59:
        raise SystemExit("--refresh-retry-minute must be between 0 and 59")
    primary_minutes = args.refresh_hour * 60 + args.refresh_minute
    retry_minutes = args.refresh_retry_hour * 60 + args.refresh_retry_minute
    if retry_minutes <= primary_minutes:
        raise SystemExit("The refresh retry time must be later than the primary refresh time")

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
            "StandardOutPath": str(log_dir / f"{service}.log"),
            "StandardErrorPath": str(log_dir / f"{service}.error.log"),
        }
        if service.endswith("-api"):
            payload["EnvironmentVariables"] = {
                "PORTFOLIO_OPS_LOCAL_DATABASE_URL": database_url,
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
        "RunAtLoad": True,
        "KeepAlive": False,
        "StartCalendarInterval": [
            {
                "Hour": args.refresh_hour,
                "Minute": args.refresh_minute,
            },
            {
                "Hour": args.refresh_retry_hour,
                "Minute": args.refresh_retry_minute,
            },
        ],
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "ThrottleInterval": 60,
        "Umask": 0o077,
        "EnvironmentVariables": {
            "PORTFOLIO_OPS_LOCAL_DATABASE_URL": database_url,
            "PORTFOLIO_OPS_LOCAL_REFRESH_HOUR": str(args.refresh_hour),
            "PORTFOLIO_OPS_LOCAL_REFRESH_MINUTE": str(args.refresh_minute),
            "PORTFOLIO_OPS_LOCAL_REFRESH_RETRY_HOUR": str(args.refresh_retry_hour),
            "PORTFOLIO_OPS_LOCAL_REFRESH_RETRY_MINUTE": str(args.refresh_retry_minute),
        },
        "StandardOutPath": str(log_dir / f"{MARKET_DATA_REFRESH_SERVICE}.log"),
        "StandardErrorPath": str(log_dir / f"{MARKET_DATA_REFRESH_SERVICE}.error.log"),
    }
    _write_plist(launch_agents_dir / f"{refresh_label}.plist", refresh_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
