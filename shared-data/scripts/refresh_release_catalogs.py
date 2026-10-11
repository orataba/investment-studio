#!/usr/bin/env python3
"""Refresh provider-backed search catalogs required by the release audit."""

from __future__ import annotations

import argparse

from studio_data.services.securities import sync_security_catalogs


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    summary = sync_security_catalogs(prefer_current_futu=True)
    catalogs = dict(summary["catalogs"])
    equity = dict(catalogs["equity"])
    etf = dict(catalogs["etf"])
    futu = dict(catalogs.get("futu") or {})
    futu_status = str(futu.get("status", "not_authorized"))
    if futu.get("reused"):
        futu_status += " (reused today's complete catalog)"
    print(
        "Release security catalog refresh completed: "
        f"{equity['active_count']} equities across "
        f"{len(dict(equity['exchange_counts']))} exchanges; "
        f"{etf['active_count']} ETFs across "
        f"{len(dict(etf['exchange_counts']))} query exchanges; "
        f"Futu: {futu_status}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
