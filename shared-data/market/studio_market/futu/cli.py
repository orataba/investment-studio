"""Local, quote-only Futu authorization and public-price maintenance."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
import webbrowser

from studio_market.config import MarketSettings
from studio_market.numeric.store import serializable

from .oauth import FutuOAuth


def main(argv=None):
    parser = argparse.ArgumentParser(prog="studio-market futu")
    parser.add_argument("--config-dir", type=Path, default=Path(os.environ.get(
        "INVESTMENT_STUDIO_MARKET_FUTU_CONFIG_DIR",
        "~/.config/orataba/secrets/investment-studio/futu",
    )).expanduser())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show authorization state without exposing credentials")
    setup = commands.add_parser("setup", help="Register a local OAuth public client with PKCE")
    setup.add_argument("--port", type=int, default=60355)
    login = commands.add_parser("login", help="Authorize only quote:read in your own browser")
    login.add_argument("--port", type=int, default=60355)
    login.add_argument("--timeout", type=int, default=300)
    login.add_argument("--no-browser", action="store_true")
    commands.add_parser("refresh", help="Refresh the existing quote-only authorization")
    catalog = commands.add_parser("catalog-sync", help="Preload local HK/US coverage for the normal security search")
    catalog.add_argument("--markets", default="hk,us")
    catalog.add_argument("--database-url")
    catalog.add_argument("--data-root")
    collect = commands.add_parser("collect", help="Archive a symbol's unadjusted daily history")
    collect.add_argument("symbol", help="Exact Futu symbol, e.g. HK.02822 or US.SPY")
    collect.add_argument("--start", required=True, type=date.fromisoformat)
    collect.add_argument("--end", required=True, type=date.fromisoformat)
    collect.add_argument("--database-url")
    collect.add_argument("--data-root")
    args = parser.parse_args(argv)
    oauth = None
    try:
        oauth = FutuOAuth(args.config_dir)
        if args.command == "status":
            result = oauth.status()
        elif args.command == "setup":
            result = oauth.setup(port=args.port)
        elif args.command == "login":
            if not oauth.status()["configured"]:
                oauth.setup(port=args.port)

            def open_authorization(url):
                print("请在富途授权页只选择行情读取（quote:read）；不要勾选账户或交易权限。", flush=True)
                print(url, flush=True)
                if not args.no_browser:
                    webbrowser.open(url)

            result = oauth.authorize(on_authorization_url=open_authorization, timeout_seconds=args.timeout)
        elif args.command == "refresh":
            result = oauth.refresh()
        else:
            from .collect import collect_history

            settings = MarketSettings.from_environment(args.database_url, args.data_root)
            # Both manual collection and the scheduled collector resolve the
            # same external configuration directory; tokens never enter data.
            from dataclasses import replace

            settings = replace(settings, futu_config_dir=args.config_dir)
            if args.command == "catalog-sync":
                from .catalog import sync_catalog

                result = sync_catalog(settings, markets=tuple(args.markets.split(",")))
            else:
                result = collect_history(settings, args.symbol, args.start, args.end)
        print(json.dumps(serializable(result), ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        from .collect import FutuHistoryError
        from .quotes import FutuQuoteError

        # Provider/library exception text can contain request headers. Only
        # errors owned and sanitized by this integration may cross the CLI.
        from .oauth import FutuOAuthError

        safe = isinstance(error, (FutuOAuthError, FutuQuoteError, FutuHistoryError))
        print(json.dumps({"status": "failed", "error": str(error) if safe else type(error).__name__}, ensure_ascii=False))
        return 1
    finally:
        if oauth is not None:
            oauth.close()


if __name__ == "__main__":
    raise SystemExit(main())
