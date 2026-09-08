"""Resolve real credentials through Home; business authorization stays in each app.

This package deliberately has no database or framework dependency, so the same
client also serves the independent Regime HTTP server and worker processes.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from ipaddress import ip_address
import json
import os
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class IdentityError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Principal:
    user_id: str | None
    display_name: str
    team_id: str
    team_role: Literal["admin", "member", "reader"] = "member"
    kind: Literal["user", "service"] = "user"
    is_team_owner: bool = False
    service_id: str | None = None
    scopes: list[str] = field(default_factory=list)
    session_id: str | None = None
    resource_scope: dict[str, str] | None = None
    local_unrestricted: bool = False
    credential: str = field(default="", repr=False, compare=False)

    def to_dict(self) -> dict:
        return {key: value for key, value in vars(self).items() if key != "credential"}

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


_active: ContextVar[Principal | None] = ContextVar("studio_principal", default=None)
LOCAL_OWNER_CREDENTIAL = "studio-local-owner"


def local_mode() -> bool:
    return os.environ.get("INVESTMENT_STUDIO_AUTH_MODE", "account").strip().lower() == "local"


def _loopback_host(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host or "").is_loopback
    except ValueError:
        return False


def validate_local_request(request) -> None:
    """Local access is tied to the actual peer and browser destination, not headers claiming identity."""
    client = getattr(request, "client", None)
    peer = getattr(client, "host", None) or (client[0] if isinstance(client, tuple) and client else None)
    url = urlsplit(str(request.url))
    if not _loopback_host(peer) or not _loopback_host(url.hostname):
        raise IdentityError(403, "本机免登录仅允许从本机地址访问")
    host = request.headers.get("host")
    origin = request.headers.get("origin")
    if ((host and not _loopback_host(urlsplit("//" + host).hostname))
            or (origin and not _loopback_host(urlsplit(origin).hostname))):
        raise IdentityError(403, "本机免登录不接受外部站点请求")
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and any(not _loopback_host(item.strip()) for item in forwarded.split(",")):
        raise IdentityError(403, "本机免登录不接受远程代理请求")


def current_principal() -> Principal:
    principal = _active.get()
    if principal is None:
        raise IdentityError(401, "请先登录 Investment Studio")
    return principal


@contextmanager
def principal_context(principal: Principal):
    token = _active.set(principal)
    try:
        yield principal
    finally:
        _active.reset(token)


def _auth_url() -> str:
    base = os.environ.get("INVESTMENT_STUDIO_AUTH_URL", "").strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise IdentityError(503, "尚未配置统一账号服务")
    return base


def cookie_name() -> str:
    return os.environ.get("INVESTMENT_STUDIO_AUTH_COOKIE_NAME", "__Secure-yungu_session")


def credential_from_headers(headers) -> tuple[str, bool]:
    authorization = headers.get("authorization")
    if authorization is None:
        authorization = headers.get("Authorization")
    if authorization is not None:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise IdentityError(401, "登录凭证无效")
        return value.strip(), False
    cookies = SimpleCookie()
    try:
        cookies.load(headers.get("cookie") or headers.get("Cookie") or "")
    except CookieError as error:
        raise IdentityError(401, "登录凭证无效") from error
    token = cookies.get(cookie_name())
    if token is None or not token.value:
        raise IdentityError(401, "请先登录 Investment Studio")
    return token.value, True


def validate_origin(request, *, cookie_authenticated: bool, allowed_origins=None):
    if not cookie_authenticated or request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    configured = os.environ.get("INVESTMENT_STUDIO_AUTH_ALLOWED_ORIGINS", "")
    if configured.strip().startswith("["):
        try:
            configured = json.loads(configured)
        except ValueError as error:
            raise IdentityError(503, "账号来源配置无效") from error
    else:
        configured = [item.strip() for item in configured.split(",") if item.strip()]
    own = urlsplit(str(request.url))
    allowed = {f"{own.scheme}://{own.netloc}", *(allowed_origins or []), *configured}
    if not origin or origin not in allowed or origin == "null":
        raise IdentityError(403, "请求来源不受信任，请从 Investment Studio 页面操作")


def _call(path: str, credential: str, payload=None, *, service_token=None):
    headers = {"Authorization": f"Bearer {credential}", "Accept": "application/json"}
    if service_token:
        headers["X-Studio-Service-Token"] = service_token
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(_auth_url() + path, headers=headers,
                      data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except HTTPError as error:
        detail = "账号权限校验失败"
        try:
            value = json.load(error).get("detail")
            if isinstance(value, str):
                detail = value
        except (ValueError, TypeError, AttributeError, OSError):
            pass
        raise IdentityError(error.code if error.code in {401, 403, 404, 409, 422, 503} else 503, detail) from error
    except (URLError, OSError, ValueError) as error:
        raise IdentityError(503, "统一账号服务暂时不可用") from error


def resolve_token(token: str, audience: str) -> Principal:
    if not token:
        raise IdentityError(401, "请先登录 Investment Studio")
    value = _call("/introspect", token, {"audience": audience})
    fields = {key: value[key] for key in Principal.__dataclass_fields__ if key in value and key != "credential"}
    try:
        principal = Principal(**fields, credential=token)
    except (TypeError, KeyError) as error:
        raise IdentityError(503, "账号服务返回的身份不完整") from error
    if (not principal.team_id or principal.team_role not in {"admin", "member", "reader"}
            or principal.kind not in {"user", "service"}
            or (principal.kind == "user" and not principal.user_id)
            or (principal.kind == "service" and not principal.service_id)):
        raise IdentityError(503, "账号服务返回的身份不完整")
    if principal.local_unrestricted and not local_mode():
        raise IdentityError(403, "云端账号模式不接受本机身份")
    return principal


def resolve_request(request, audience: str, *, allowed_origins=None) -> Principal:
    if local_mode() and request.headers.get("authorization") is None and request.headers.get("Authorization") is None:
        cookies = SimpleCookie()
        try:
            cookies.load(request.headers.get("cookie") or request.headers.get("Cookie") or "")
        except CookieError as error:
            raise IdentityError(401, "登录凭证无效") from error
        if cookie_name() not in cookies:
            validate_local_request(request)
            validate_origin(request, cookie_authenticated=True, allowed_origins=allowed_origins)
            principal = resolve_token(LOCAL_OWNER_CREDENTIAL, audience)
            if not principal.local_unrestricted or principal.resource_scope:
                raise IdentityError(503, "统一账号服务未启用本机身份")
            return principal
    token, from_cookie = credential_from_headers(request.headers)
    validate_origin(request, cookie_authenticated=from_cookie, allowed_origins=allowed_origins)
    principal = resolve_token(token, audience)
    if principal.local_unrestricted:
        validate_local_request(request)
    return principal


def principal_headers(principal: Principal | None = None) -> dict[str, str]:
    principal = principal or current_principal()
    if not principal.credential:
        raise IdentityError(401, "缺少可传递的登录凭证")
    return {"Authorization": f"Bearer {principal.credential}"}


def _service_token(*, required=True) -> str | None:
    file = os.environ.get("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN_FILE")
    if file:
        try:
            path = Path(file).expanduser()
            if path.stat().st_mode & 0o077:
                raise IdentityError(503, "服务身份凭证文件权限不符合要求")
            token = path.read_text().strip()
        except OSError as error:
            raise IdentityError(503, "无法读取服务身份凭证") from error
    else:
        token = os.environ.get("INVESTMENT_STUDIO_AUTH_SERVICE_TOKEN", "").strip()
    if not token and required:
        raise IdentityError(503, "尚未配置服务身份")
    return token or None


def service_principal(audience: str) -> Principal:
    principal = resolve_token(_service_token(), audience)
    if principal.kind != "service":
        raise IdentityError(503, "后台任务需要独立服务身份")
    return principal


def issue_delegation(principal: Principal, audience: str, resource_scope: dict[str, str], ttl_seconds=3600) -> str:
    result = _call("/delegations", principal.credential,
                   {"audience": audience, "resource_scope": resource_scope, "ttl_seconds": ttl_seconds},
                   service_token=_service_token(required=False) if principal.resource_scope else None)
    token = result.get("token")
    if not isinstance(token, str) or not token:
        raise IdentityError(503, "账号服务未生成任务凭证")
    return token


def revoke_delegation(token: str, principal: Principal | None = None):
    return _call("/delegations/revoke", principal.credential if principal else token, {"token": token},
                 service_token=_service_token(required=False))


def team_directory(principal: Principal | None = None) -> list[dict]:
    result = _call("/members", (principal or current_principal()).credential)
    if not isinstance(result.get("members"), list):
        raise IdentityError(503, "账号服务未返回团队成员")
    return result["members"]
