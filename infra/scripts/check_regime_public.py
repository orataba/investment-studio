#!/usr/bin/env python3
"""Check Regime's staged Nginx files or its anonymous public read surface."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class CheckError(Exception):
    pass


class PageAssets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: dict[str, str] = {}
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "script" and values.get("src"):
            self.assets[values["src"]] = "javascript"
        if tag == "link" and "stylesheet" in (values.get("rel") or "").split():
            if values.get("href"):
                self.assets[values["href"]] = "css"


def page_assets(html: str, *, dashboard: bool) -> dict[str, str]:
    page = PageAssets()
    page.feed(html)
    if dashboard and not {"dashboard-title", "latest-grid"}.issubset(page.ids):
        raise CheckError("root HTML is not the Regime dashboard")
    if not {"javascript", "css"}.issubset(page.assets.values()):
        raise CheckError("Regime HTML must reference JavaScript and CSS")
    for asset in page.assets:
        asset_path(asset)
    return page.assets


def asset_path(url: str) -> PurePosixPath:
    parsed = urlsplit(url)
    path = PurePosixPath(unquote(parsed.path))
    if (
        parsed.scheme or parsed.netloc or parsed.fragment
        or not parsed.path.startswith("/static/")
        or ".." in path.parts or "\\" in str(path)
        or len(path.parts) < 3
    ):
        raise CheckError("Regime assets must use local /static/ paths")
    return PurePosixPath(*path.parts[2:])


def check_staged(release_root: Path, nginx_user: str) -> dict:
    static_root = release_root / "deploy" / "regime-ui"
    files = {static_root / "index.html", static_root / "audit.html"}
    for name in ("index.html", "audit.html"):
        assets = page_assets(
            (static_root / name).read_text(encoding="utf-8"), dashboard=name == "index.html"
        )
        files.update(static_root / asset_path(asset) for asset in assets)
    try:
        worker = pwd.getpwnam(nginx_user)
    except KeyError as exc:
        raise CheckError(f"unknown Nginx worker user: {nginx_user}") from exc
    if os.geteuid() != worker.pw_uid and os.geteuid() != 0:
        raise CheckError("run preflight as root or as the actual Nginx worker user")
    for path in sorted(files):
        if not path.is_file() or path.stat().st_size == 0:
            raise CheckError(f"missing or empty public file: {path}")
        # Use the worker's real access rights, including traversal of every parent
        # and any symlink target. Checking mode bits as root misses this failure.
        command = ["test", "-r", str(path)]
        if os.geteuid() != worker.pw_uid:
            command = ["runuser", "-u", nginx_user, "--", *command]
        result = subprocess.run(command, capture_output=True, check=False, timeout=10)
        if result.returncode:
            raise CheckError(f"Nginx worker {nginx_user} cannot read public file: {path}")
    return {"check": "staged_files", "nginx_user": nginx_user, "readable_files": len(files)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicReader:
    def __init__(self, origin: str, timeout: float) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
        ):
            raise CheckError("--url must be a public HTTP(S) origin without credentials or a path")
        self.origin = origin.rstrip("/")
        self.timeout = timeout
        # No cookies, proxy credentials or automatic redirects: a login page must
        # never satisfy a static-file or data readiness check.
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def get(self, path: str):
        request = Request(self.origin + path, headers={"Cache-Control": "no-cache"})
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def content(self, path: str, accepted: set[str]) -> bytes:
        status, headers, body = self.get(path)
        if status != 200:
            raise CheckError(f"{path}: expected HTTP 200, got {status}")
        mime = headers.get_content_type()
        if mime not in accepted or not body.strip():
            raise CheckError(f"{path}: empty response or wrong Content-Type ({mime})")
        return body

    def json(self, path: str) -> dict:
        data = json.loads(self.content(path, {"application/json"}))
        if not isinstance(data, dict):
            raise CheckError(f"{path}: expected a JSON object")
        return data

    def check_audit_boundary(self) -> None:
        status, headers, _ = self.get("/static/audit.html")
        target = urljoin(self.origin, headers.get("Location", ""))
        if status != 302 or target != self.origin + "/audit":
            raise CheckError("/static/audit.html must redirect to the protected /audit route")
        status, headers, _ = self.get("/audit")
        if status in {401, 403}:
            return
        target = urlsplit(urljoin(self.origin, headers.get("Location", "")))
        if status == 302 and target.scheme in {"http", "https"} and target.path == "/login":
            return
        raise CheckError("/audit must require authentication for an anonymous request")


def check_public(origin: str, timeout: float) -> dict:
    reader = PublicReader(origin, timeout)
    html = reader.content("/", {"text/html"}).decode("utf-8")
    assets = page_assets(html, dashboard=True)
    for path, kind in assets.items():
        accepted = {"text/css"} if kind == "css" else {"application/javascript", "text/javascript"}
        reader.content(path, accepted)
    reader.check_audit_boundary()
    health = reader.json("/api/health")
    if health.get("ok") is not True or health.get("status") not in {"healthy", "degraded"}:
        raise CheckError("/api/health: Regime data service is unavailable")
    markets = reader.json("/api/markets")
    default = markets.get("default")
    available = markets.get("markets")
    if (
        not isinstance(default, str) or not default or not isinstance(available, list)
        or not any(isinstance(market, dict) and market.get("id") == default for market in available)
    ):
        raise CheckError("/api/markets: missing default market in the market list")
    latest = reader.json("/api/latest?" + urlencode({"market": default}))
    rows = latest.get("latest")
    if not isinstance(rows, list) or not rows or latest.get("row_count") != len(rows):
        raise CheckError("/api/latest: missing or inconsistent latest rows")
    return {
        "check": "public_http", "assets": len(assets), "audit_requires_auth": True,
        "health": health["status"], "all_markets_fresh": health.get("all_markets_fresh"),
        "default_market": default, "latest_rows": len(rows), "latest_date": latest.get("latest_date"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--release-root", type=Path)
    mode.add_argument("--url")
    parser.add_argument("--nginx-user", help="actual Nginx worker account; required for staged checks")
    parser.add_argument("--timeout", type=float, default=10, help="seconds per HTTP request (default: 10)")
    args = parser.parse_args(argv)
    if args.release_root and not args.nginx_user:
        parser.error("--release-root requires --nginx-user")
    if args.url and args.nginx_user:
        parser.error("--nginx-user applies only to --release-root")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        result = (
            check_staged(args.release_root.absolute(), args.nginx_user)
            if args.release_root else check_public(args.url, args.timeout)
        )
    except (CheckError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Regime readiness failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
