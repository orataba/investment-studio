"""Local, quote-only OAuth for the official Futu REST API.

OAuth responses never enter the public market-data archive. Credentials live in
an owner-only directory outside the checkout, following Studio's secret-file
convention. The authorization and refresh responses must explicitly confirm the
exact quote:read scope, even though OAuth permits an unchanged scope to be omitted.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
from typing import Callable, Iterator
from urllib.parse import parse_qs, urlencode, urlsplit

import requests


FUTU_API_ORIGIN = "https://webapi.futunn.com"
QUOTE_SCOPE = "quote:read"
DEFAULT_CONFIG_DIR = Path.home() / ".config/orataba/secrets/investment-studio/futu"
_REPOSITORY_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents
     if (parent / "docs/DEVELOPER_GUIDE.md").is_file()),
    None,
)


class FutuOAuthError(RuntimeError):
    """Sanitized OAuth or credential-storage error, safe for CLI diagnostics."""


def _scope(value: object) -> str:
    if not isinstance(value, str) or set(value.split()) != {QUOTE_SCOPE}:
        raise FutuOAuthError(
            "Futu authorization must explicitly grant only quote:read; "
            "missing or additional scopes are rejected."
        )
    return QUOTE_SCOPE


def _credential(value: object) -> str:
    if (not isinstance(value, str) or not value or not value.isascii()
            or any(char.isspace() or not char.isprintable() for char in value)):
        raise FutuOAuthError("Futu OAuth returned an invalid credential field.")
    return value


class _CallbackServer(HTTPServer):
    # Permit a fresh authorization after cancellation while the old TCP
    # connection is in TIME_WAIT. An active listener still owns the port.
    allow_reuse_address = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(1.0)
        return connection, address

    def handle_error(self, request, client_address):
        # Base HTTPServer prints tracebacks, which could expose callback codes.
        pass


class FutuOAuth:
    """Manage a single quote-only grant without returning tokens from CLI actions.

    setup() registers only if no client is saved. authorize() gives its URL to
    the caller and waits for a loopback callback. status() is local and creates
    no files. Only access_token() returns a secret, for the quote transport.
    """

    def __init__(
        self,
        config_dir: str | Path = DEFAULT_CONFIG_DIR,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 20.0,
        clock: Callable[[], float] = time.time,
    ):
        path = Path(config_dir).expanduser()
        if not path.is_absolute() or path.is_symlink():
            raise FutuOAuthError("Futu credentials require an absolute external directory, not a symlink.")
        self.config_dir = path.resolve()
        if _REPOSITORY_ROOT and self.config_dir.is_relative_to(_REPOSITORY_ROOT):
            raise FutuOAuthError("Futu credentials must be stored outside the repository.")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("OAuth request timeout must be positive.")
        self._session = session if session is not None else requests.Session()
        self._owns_session = session is None
        if self._owns_session:
            # Do not send OAuth credentials through environment-selected proxies
            # or attach unrelated .netrc credentials to registration requests.
            self._session.trust_env = False
        self._timeout = timeout_seconds
        self._clock = clock
        self._last_access_token: str | None = None

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> FutuOAuth:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _directory(self, *, create: bool = False) -> bool:
        if create:
            missing = []
            path = self.config_dir
            while not path.exists():
                missing.append(path)
                path = path.parent
            for path in reversed(missing):
                try:
                    path.mkdir(mode=0o700)
                except FileExistsError:
                    pass
        try:
            metadata = self.config_dir.lstat()
        except FileNotFoundError:
            return False
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700):
            raise FutuOAuthError("Futu credential directory must be owner-owned with mode 0700.")
        return True

    @staticmethod
    def _check_file(fd: int) -> None:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1):
            raise FutuOAuthError("Futu credential files must be private regular files with mode 0600.")

    @contextmanager
    def _locked(self, *, create: bool = False) -> Iterator[None]:
        if not self._directory(create=create):
            raise FutuOAuthError("Futu OAuth is not configured; run OAuth setup first.")
        try:
            fd = os.open(self.config_dir / ".oauth.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise FutuOAuthError("Could not open the private Futu OAuth lock.") from None
        try:
            self._check_file(fd)
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._directory()
            yield
        finally:
            os.close(fd)

    def _read(self, name: str) -> dict | None:
        try:
            fd = os.open(self.config_dir / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError:
            raise FutuOAuthError("Could not read a private Futu OAuth file.") from None
        try:
            self._check_file(fd)
            with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as source:
                payload = json.load(source)
        except (ValueError, UnicodeError):
            raise FutuOAuthError("A Futu OAuth file contains invalid JSON.") from None
        finally:
            os.close(fd)
        if not isinstance(payload, dict):
            raise FutuOAuthError("A Futu OAuth file has an invalid structure.")
        return payload

    def _write(self, name: str, payload: dict) -> None:
        self._directory()
        # Refuse to overwrite unsafe existing files, including symlinks.
        self._read(name)
        fd, temporary = tempfile.mkstemp(prefix=".oauth-", dir=self.config_dir)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as destination:
                json.dump(payload, destination, separators=(",", ":"))
                destination.write("\n")
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, self.config_dir / name)
            directory_fd = os.open(self.config_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _post(self, path: str, *, json_body: dict | None = None, data: dict | None = None) -> dict:
        if path not in {"/oauth2/register", "/oauth2/token"}:
            raise FutuOAuthError("Unsupported Futu OAuth endpoint.")
        try:
            response = self._session.post(
                FUTU_API_ORIGIN + path,
                json=json_body,
                data=data,
                headers={"Accept": "application/json"},
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise FutuOAuthError("Futu OAuth network request failed; no credentials were logged.") from None
        try:
            if not 200 <= response.status_code < 300:
                raise FutuOAuthError(f"Futu OAuth request failed with HTTP {response.status_code}.")
            try:
                payload = response.json()
            except ValueError:
                raise FutuOAuthError("Futu OAuth returned invalid JSON.") from None
        finally:
            response.close()
        if not isinstance(payload, dict):
            raise FutuOAuthError("Futu OAuth returned an invalid response structure.")
        if "error" in payload:
            # error_description, ret_msg and response bodies are untrusted and
            # may contain tokens, so never attach them to errors or diagnostics.
            raise FutuOAuthError("Futu OAuth rejected the request; authorization may be required again.")
        return payload

    def _client(self, *, required: bool = True) -> dict | None:
        client = self._read("client.json")
        if client is None:
            if required:
                raise FutuOAuthError("Futu OAuth is not configured; run OAuth setup first.")
            return None
        _credential(client.get("client_id"))
        _scope(client.get("scope"))
        self._callback_port(client.get("redirect_uri"))
        return client

    @staticmethod
    def _callback_port(uri: object) -> int:
        try:
            parsed = urlsplit(uri if isinstance(uri, str) else "")
            port = parsed.port
        except ValueError:
            port = None
        if not port or uri != f"http://127.0.0.1:{port}/callback":
            raise FutuOAuthError("Futu callback must use the registered IPv4 loopback URI.")
        return port

    def _tokens(self, *, required: bool = True) -> dict | None:
        tokens = self._read("tokens.json")
        if tokens is None:
            if required:
                raise FutuOAuthError("Futu OAuth requires browser authorization.")
            return None
        _scope(tokens.get("scope"))
        _credential(tokens.get("access_token"))
        _credential(tokens.get("refresh_token"))
        expiry = tokens.get("expires_at")
        if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                or not math.isfinite(expiry) or expiry <= 0):
            raise FutuOAuthError("Futu OAuth token expiry is invalid.")
        return tokens

    def _save_tokens(self, payload: dict, *, previous_refresh_token: str | None = None) -> dict:
        _scope(payload.get("scope"))
        if not isinstance(payload.get("token_type"), str) or payload["token_type"].lower() != "bearer":
            raise FutuOAuthError("Futu OAuth requires Bearer tokens.")
        lifetime = payload.get("expires_in")
        if isinstance(lifetime, bool) or not isinstance(lifetime, int) or lifetime <= 0:
            raise FutuOAuthError("Futu OAuth returned an invalid token lifetime.")
        try:
            expiry = self._clock() + lifetime
            valid_expiry = math.isfinite(expiry) and expiry > 0
        except OverflowError:
            valid_expiry = False
        if not valid_expiry:
            raise FutuOAuthError("Futu OAuth returned an invalid token lifetime.")
        tokens = {
            "access_token": _credential(payload.get("access_token")),
            "refresh_token": _credential(payload.get("refresh_token", previous_refresh_token)),
            "scope": QUOTE_SCOPE,
            "expires_at": expiry,
        }
        self._write("tokens.json", tokens)
        return tokens

    def setup(self, *, port: int = 60355) -> dict:
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError("OAuth callback port must be between 1024 and 65535.")
        redirect_uri = f"http://127.0.0.1:{port}/callback"
        with self._locked(create=True):
            existing = self._client(required=False)
            if existing:
                if existing["redirect_uri"] != redirect_uri:
                    raise FutuOAuthError("The saved Futu OAuth client uses a different callback port.")
            else:
                payload = self._post("/oauth2/register", json_body={
                    "redirect_uris": [redirect_uri],
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "client_name": "Investment Studio quote-only market data",
                    "scope": QUOTE_SCOPE,
                })
                if payload.get("redirect_uris") != [redirect_uri]:
                    raise FutuOAuthError("Futu OAuth registration did not confirm the loopback callback.")
                self._write("client.json", {
                    "client_id": _credential(payload.get("client_id")),
                    "redirect_uri": redirect_uri,
                    "scope": QUOTE_SCOPE,
                })
        return self.status()

    def status(self) -> dict:
        """Return local metadata; authenticated does not attest live server access."""
        result = {"configured": False, "authenticated": False, "scope": None,
                  "expires_at": None, "needs_refresh": False}
        if not self._directory():
            return result
        if self._client(required=False) is None:
            return result
        result["configured"] = True
        tokens = self._tokens(required=False)
        if tokens is not None:
            result.update({
                "authenticated": True,
                "scope": QUOTE_SCOPE,
                "expires_at": datetime.fromtimestamp(tokens["expires_at"], timezone.utc).isoformat(),
                "needs_refresh": self._clock() >= tokens["expires_at"],
            })
        return result

    def authorize(
        self,
        *,
        on_authorization_url: Callable[[str], object],
        timeout_seconds: float = 300.0,
    ) -> dict:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("OAuth authorization timeout must be positive.")
        if not self._directory():
            raise FutuOAuthError("Futu OAuth is not configured; run OAuth setup first.")
        client = self._client()
        port = self._callback_port(client["redirect_uri"])
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
        callback: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                try:
                    params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=10)
                except ValueError:
                    params = {}
                incoming_state = params.get("state", [""])
                valid_state = (len(incoming_state) == 1 and secrets.compare_digest(
                    incoming_state[0].encode("utf-8"), state.encode("ascii")))
                codes = params.get("code", [])
                valid = (parsed.path == "/callback" and not parsed.scheme and not parsed.netloc
                         and valid_state and not callback)
                if valid and len(codes) == 1 and codes[0] and "error" not in params:
                    callback["code"] = codes[0]
                    status, message = 200, "Authorization received. Return to Investment Studio."
                elif valid and "error" in params and not codes:
                    callback["error"] = "denied"
                    status, message = 400, "Authorization was not completed. Return to Investment Studio."
                else:
                    status, message = 400, "Invalid authorization callback."
                body = message.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

        try:
            server = _CallbackServer(("127.0.0.1", port), Handler)
        except OSError:
            raise FutuOAuthError("Could not bind the registered Futu OAuth loopback callback port.") from None
        with server:
            url = FUTU_API_ORIGIN + "/oauth2/authorize/confirm?" + urlencode({
                "client_id": client["client_id"],
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "redirect_uri": client["redirect_uri"],
                "response_type": "code",
                "state": state,
                "scope": QUOTE_SCOPE,
            })
            deadline = time.monotonic() + timeout_seconds
            on_authorization_url(url)
            while not callback and time.monotonic() < deadline:
                server.timeout = min(0.25, max(0.0, deadline - time.monotonic()))
                server.handle_request()
        if not callback:
            raise FutuOAuthError("Futu OAuth authorization timed out; start authorization again.")
        if "error" in callback:
            raise FutuOAuthError("Futu OAuth authorization was denied or cancelled.")
        with self._locked():
            if self._client() != client:
                raise FutuOAuthError("Futu OAuth client changed during authorization; start again.")
            payload = self._post("/oauth2/token", data={
                "grant_type": "authorization_code",
                "code": callback["code"],
                "client_id": client["client_id"],
                "redirect_uri": client["redirect_uri"],
                "code_verifier": verifier,
            })
            self._save_tokens(payload)
        return self.status()

    def access_token(self, *, force_refresh: bool = False) -> str:
        """For the quote transport only. Never log or archive the return value."""
        with self._locked():
            client = self._client()
            tokens = self._tokens()
            changed = (self._last_access_token is not None
                       and tokens["access_token"] != self._last_access_token)
            if self._clock() >= tokens["expires_at"] or (force_refresh and not changed):
                payload = self._post("/oauth2/token", data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": client["client_id"],
                    "scope": QUOTE_SCOPE,
                })
                tokens = self._save_tokens(payload, previous_refresh_token=tokens["refresh_token"])
            self._last_access_token = tokens["access_token"]
            return tokens["access_token"]

    def refresh(self) -> dict:
        self.access_token(force_refresh=True)
        return self.status()
