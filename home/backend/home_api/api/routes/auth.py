from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
import pyotp
from sqlalchemy import select
from sqlalchemy.orm import Session
from studio_identity import IdentityError, LOCAL_OWNER_CREDENTIAL, validate_local_request

from home_api.core.settings import get_settings
from home_api.db.models import Delegation, Membership, OneTimeToken, ServiceCredential, Team, User, now
from home_api.db.session import get_db
from home_api.services.auth import hash_password, new_token, read_private_text, token_hash, verify_password
from home_api.services.identity import AUDIENCES, audit, lock_user, new_session, one_time_token, resolve_token, revoke_one_time_tokens, revoke_sessions, user_principal, utc

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(Input):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    otp: str | None = Field(default=None, max_length=10)


class PasswordRequest(Input):
    current_password: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=12, max_length=256)
    otp: str | None = Field(default=None, max_length=10)


class ConfirmRequest(Input):
    password: str = Field(min_length=1, max_length=256)
    otp: str | None = Field(default=None, max_length=10)


class ProfileRequest(Input):
    display_name: str = Field(min_length=1, max_length=200)

    @field_validator("display_name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("显示名不能为空。")
        return value.strip()


class InviteRequest(ProfileRequest):
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.@+-]+$")
    role: Literal["admin", "member", "reader"] = "member"


class MemberPatch(Input):
    role: Literal["admin", "member", "reader"] | None = None
    active: bool | None = None


class ActivateRequest(Input):
    token: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=12, max_length=256)


class IntrospectRequest(Input):
    audience: str = Field(min_length=1, max_length=80)


class ResourceScope(Input):
    kind: Literal["run", "portfolio", "capture", "report"]
    id: str = Field(min_length=1, max_length=200)


class DelegationRequest(IntrospectRequest):
    resource_scope: ResourceScope
    ttl_seconds: int = Field(default=3600, ge=60, le=24 * 3600)


class RevokeRequest(Input):
    token: str = Field(min_length=1, max_length=256)


class TransferRequest(ConfirmRequest):
    user_id: str = Field(min_length=1, max_length=36)


def bearer(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "登录凭证格式无效。")
    return token.strip()


def request_token(request: Request) -> str | None:
    return bearer(request) or request.cookies.get(get_settings().auth_cookie_name)


def local_request(request: Request) -> bool:
    if get_settings().auth_mode != "local":
        return False
    try:
        validate_local_request(request)
    except IdentityError as error:
        raise HTTPException(error.status_code, error.detail) from error
    return True


def resolve_request_identity(db: Session, request: Request, audience: str | None = None, *, bearer_only=False):
    local = local_request(request)
    token = bearer(request) if bearer_only else request_token(request)
    has_cookie = not bearer_only and get_settings().auth_cookie_name in request.cookies
    return resolve_token(db, token or (LOCAL_OWNER_CREDENTIAL if local and not has_cookie else None), audience)


def check_origin(request: Request) -> None:
    local_request(request)
    if bearer(request):
        return
    settings = get_settings()
    origins = {value.rstrip("/") for value in settings.cors_origins}
    for value in [settings.frontend_url, *settings.app_urls.values()]:
        url = urlsplit(value)
        if url.scheme and url.netloc:
            origins.add(f"{url.scheme}://{url.netloc}")
    if request.headers.get("origin", "").rstrip("/") not in origins:
        raise HTTPException(403, "写入请求来源无效。请从工作台页面操作。")


def current(db: Session, request: Request, admin: bool = False) -> dict:
    principal, kind, _ = resolve_request_identity(db, request, "home")
    if kind not in {"session", "local"} or principal["kind"] != "user":
        raise HTTPException(403, "此操作需要本人登录会话。")
    if admin and principal["team_role"] != "admin":
        raise HTTPException(403, "需要团队管理员权限。")
    if admin:
        enforce_mfa(db, principal)
    return principal


def enforce_mfa(db: Session, principal: dict) -> None:
    if (not principal.get("local_unrestricted") and get_settings().auth_require_admin_totp
            and principal["kind"] == "user" and principal["team_role"] == "admin"):
        if not db.get(User, principal["user_id"]).totp_secret:
            raise HTTPException(403, "管理员需先在账号设置启用二步验证。")


def cipher() -> Fernet:
    try:
        return Fernet(read_private_text(get_settings().auth_totp_key_file, "TOTP encryption key").encode())
    except (ValueError, OSError) as error:
        raise HTTPException(503, "二步验证尚未配置。") from error


def consume_otp(user: User, otp: str | None, secret: str | None = None) -> bool:
    encrypted = secret or user.totp_secret
    if not encrypted:
        return True
    totp = pyotp.TOTP(cipher().decrypt(encrypted.encode()).decode())
    step = int(now().timestamp()) // totp.interval
    # A TOTP code is accepted once, including during sensitive account changes.
    for candidate in (step, step - 1, step + 1):
        if user.totp_last_step is not None and candidate <= user.totp_last_step:
            continue
        if totp.verify(otp or "", for_time=candidate * totp.interval):
            user.totp_last_step = candidate
            return True
    return False


def confirm_user(db: Session, principal: dict, password: str, otp: str | None) -> User:
    user = lock_user(db, principal["user_id"])
    if not user or not user.active or not verify_password(password, user.password_hash) or not consume_otp(user, otp):
        raise HTTPException(401, "密码或验证码不正确。")
    return user


def session_body(db: Session, principal: dict) -> dict:
    user = db.get(User, principal["user_id"])
    return {"authenticated": True, **principal, "mfa_enabled": bool(user.totp_secret),
            "mfa_required": bool(not principal.get("local_unrestricted") and get_settings().auth_require_admin_totp
                                 and principal["team_role"] == "admin" and not user.totp_secret)}


def set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(key=settings.auth_cookie_name, value=token, max_age=settings.auth_session_ttl_seconds,
                        httponly=True, secure=settings.auth_cookie_secure, samesite="strict", path="/", domain=settings.auth_cookie_domain)


def clear_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(key=settings.auth_cookie_name, path="/", domain=settings.auth_cookie_domain,
                           secure=settings.auth_cookie_secure, httponly=True, samesite="strict")


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Db) -> dict:
    check_origin(request)
    user = db.scalar(select(User).where(User.username == payload.username.strip().casefold()).with_for_update())
    if user and user.locked_until and utc(user.locked_until) > now():
        raise HTTPException(429, "登录尝试过多，请稍后重试。")
    if not user or not user.active or not verify_password(payload.password, user.password_hash) or not consume_otp(user, payload.otp):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= 5:
                user.locked_until = now() + timedelta(minutes=15)
                user.failed_logins = 0
            audit(db, "login_failed", target=user.id)
            db.commit()
        raise HTTPException(401, "用户名、密码或二步验证码不正确。")
    principal = user_principal(db, user.id)
    user.failed_logins = 0
    user.locked_until = None
    token = new_session(db, user.id, get_settings().auth_session_ttl_seconds)
    audit(db, "login", user.id, user.id)
    db.commit()
    set_cookie(response, token)
    return session_body(db, principal)


@router.get("/session")
def session(request: Request, db: Db) -> dict:
    return session_body(db, current(db, request))


@router.get("/check", status_code=204)
def check(request: Request, db: Db) -> Response:
    current(db, request)
    return Response(status_code=204)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Db) -> Response:
    check_origin(request)
    principal, kind, record = resolve_token(db, request_token(request), "home")
    if kind != "session":
        raise HTTPException(403, "需要登录会话。")
    lock_user(db, principal["user_id"])
    record.revoked_at = now()
    audit(db, "logout", principal["user_id"], record.id)
    db.commit()
    clear_cookie(response)
    response.status_code = 204
    return response


@router.post("/logout-all", status_code=204)
def logout_all(request: Request, response: Response, db: Db) -> Response:
    check_origin(request)
    principal = current(db, request)
    lock_user(db, principal["user_id"])
    revoke_sessions(db, principal["user_id"])
    audit(db, "logout_all", principal["user_id"], principal["user_id"])
    db.commit()
    clear_cookie(response)
    response.status_code = 204
    return response


@router.patch("/profile")
def profile(payload: ProfileRequest, request: Request, db: Db) -> dict:
    check_origin(request)
    principal = current(db, request)
    db.get(User, principal["user_id"]).display_name = payload.display_name
    audit(db, "profile_changed", principal["user_id"], principal["user_id"])
    db.commit()
    return session_body(db, current(db, request))


@router.post("/password", status_code=204)
def password(payload: PasswordRequest, request: Request, response: Response, db: Db) -> Response:
    check_origin(request)
    principal = current(db, request)
    user = confirm_user(db, principal, payload.current_password, payload.otp)
    user.password_hash = hash_password(payload.password)
    revoke_sessions(db, user.id)
    revoke_one_time_tokens(db, user.id)
    audit(db, "password_changed", user.id, user.id)
    db.commit()
    clear_cookie(response)
    response.status_code = 204
    return response


@router.get("/members")
def members(request: Request, db: Db) -> dict:
    # Minimal team directory is available to scoped tasks for author / recipient validation.
    principal, _, _ = resolve_request_identity(db, request)
    team = db.get(Team, principal["team_id"])
    rows = db.execute(select(User, Membership).join(Membership, Membership.user_id == User.id).where(Membership.team_id == team.id).order_by(User.display_name)).all()
    return {"members": [{"user_id": user.id, "username": user.username, "display_name": user.display_name,
                         "role": member.role, "team_role": member.role, "active": user.active,
                         "status": "active" if user.active and user.password_hash else ("invited" if user.active else "inactive"),
                         "is_team_owner": team.owner_user_id == user.id} for user, member in rows]}


def activation_result(token: str) -> dict:
    return {"activation_url": f"{get_settings().frontend_url.rstrip('/')}/activate#token={token}", "expires_in_seconds": 86400}


@router.post("/members", status_code=201)
def invite(payload: InviteRequest, request: Request, db: Db) -> dict:
    check_origin(request)
    principal = current(db, request, admin=True)
    username = payload.username.casefold()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, "该用户名已存在。")
    user = User(username=username, display_name=payload.display_name)
    db.add(user)
    db.flush()
    db.add(Membership(team_id=principal["team_id"], user_id=user.id, role=payload.role))
    token = one_time_token(db, user.id, "activate")
    audit(db, "member_invited", principal["user_id"], user.id, role=payload.role)
    db.commit()
    return {"user_id": user.id, **activation_result(token)}


def team_member(db: Session, principal: dict, user_id: str) -> tuple[User, Membership]:
    team = db.scalar(select(Team).where(Team.id == principal["team_id"]).with_for_update()
                     .execution_options(populate_existing=True))
    member = db.get(Membership, (team.id, user_id))
    if not member:
        raise HTTPException(404, "找不到团队成员。")
    return lock_user(db, user_id), member


@router.patch("/members/{user_id}")
def update_member(user_id: str, payload: MemberPatch, request: Request, db: Db) -> dict:
    check_origin(request)
    principal = current(db, request, admin=True)
    user, member = team_member(db, principal, user_id)
    team = db.get(Team, principal["team_id"])
    if user.id == team.owner_user_id and (payload.active is False or payload.role not in (None, "admin")):
        raise HTTPException(409, "请先完成团队拥有者交接。")
    if payload.role is not None:
        member.role = payload.role
    if payload.active is not None:
        user.active = payload.active
        if not user.active:
            revoke_sessions(db, user.id)
            revoke_one_time_tokens(db, user.id)
    audit(db, "member_changed", principal["user_id"], user.id, **payload.model_dump(exclude_unset=True))
    db.commit()
    return {"user_id": user.id, "role": member.role, "active": user.active}


@router.post("/members/{user_id}/reset")
def reset_member(user_id: str, request: Request, db: Db) -> dict:
    check_origin(request)
    principal = current(db, request, admin=True)
    user, _ = team_member(db, principal, user_id)
    if not user.active:
        raise HTTPException(409, "请先恢复该账号。")
    if db.get(Team, principal["team_id"]).owner_user_id == user.id and principal["user_id"] != user.id:
        raise HTTPException(403, "其他管理员不能重置团队拥有者的密码。")
    revoke_sessions(db, user.id)
    token = one_time_token(db, user.id, "reset")
    audit(db, "password_reset_requested", principal["user_id"], user.id)
    db.commit()
    return activation_result(token)


@router.post("/activate", status_code=204)
def activate(payload: ActivateRequest, request: Request, db: Db) -> Response:
    check_origin(request)
    digest = token_hash(payload.token)
    user_id = db.scalar(select(OneTimeToken.user_id).where(OneTimeToken.token_hash == digest))
    if not user_id:
        raise HTTPException(400, "链接已失效，请联系管理员重新生成。")
    # Locate the account without locking the token. Every credential mutation
    # locks the user first, then rechecks the token after any concurrent change.
    user = lock_user(db, user_id)
    record = db.scalar(select(OneTimeToken).where(OneTimeToken.token_hash == digest).with_for_update()
                       .execution_options(populate_existing=True))
    if not record or record.consumed_at or utc(record.expires_at) <= now():
        raise HTTPException(400, "链接已失效，请联系管理员重新生成。")
    if not user or not user.active:
        raise HTTPException(400, "账号已停用。")
    user.password_hash = hash_password(payload.password)
    user.failed_logins = 0
    user.locked_until = None
    revoke_sessions(db, user.id)
    revoke_one_time_tokens(db, user.id)
    audit(db, "password_set", user.id, user.id, purpose=record.purpose)
    db.commit()
    return Response(status_code=204)


@router.post("/team/transfer")
def transfer(payload: TransferRequest, request: Request, db: Db) -> dict:
    check_origin(request)
    principal = current(db, request, admin=True)
    if not principal["is_team_owner"]:
        raise HTTPException(403, "只有团队拥有者可以交接。")
    user, member = team_member(db, principal, payload.user_id)
    if db.get(Team, principal["team_id"]).owner_user_id != principal["user_id"]:
        raise HTTPException(409, "团队拥有者已变更，请刷新页面后重新操作。")
    confirm_user(db, principal, payload.password, payload.otp)
    if not user.active or not user.password_hash:
        raise HTTPException(409, "接任者必须是已启用的团队成员。")
    member.role = "admin"
    db.get(Team, principal["team_id"]).owner_user_id = user.id
    audit(db, "team_owner_transferred", principal["user_id"], user.id)
    db.commit()
    return {"owner_user_id": user.id}


@router.post("/mfa/setup")
def setup_mfa(payload: ConfirmRequest, request: Request, db: Db) -> dict:
    check_origin(request)
    principal = current(db, request)
    user = confirm_user(db, principal, payload.password, payload.otp)
    if user.totp_secret:
        raise HTTPException(409, "二步验证已启用。")
    secret = pyotp.random_base32()
    user.totp_pending_secret = cipher().encrypt(secret.encode()).decode()
    db.commit()
    return {"secret": secret, "provisioning_uri": pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="Investment Studio")}


@router.post("/mfa/confirm", status_code=204)
def confirm_mfa(payload: ConfirmRequest, request: Request, db: Db) -> Response:
    check_origin(request)
    principal = current(db, request)
    user = lock_user(db, principal["user_id"])
    if not user or not user.active or not verify_password(payload.password, user.password_hash) or not user.totp_pending_secret or not consume_otp(user, payload.otp, user.totp_pending_secret):
        raise HTTPException(400, "密码或二步验证码不正确。")
    user.totp_secret = user.totp_pending_secret
    user.totp_pending_secret = None
    revoke_sessions(db, user.id)
    audit(db, "mfa_enabled", user.id, user.id)
    db.commit()
    return Response(status_code=204)


@router.post("/mfa/remove", status_code=204)
def remove_mfa(payload: ConfirmRequest, request: Request, db: Db) -> Response:
    check_origin(request)
    principal = current(db, request)
    if get_settings().auth_require_admin_totp and principal["team_role"] == "admin":
        raise HTTPException(409, "此部署要求管理员保留二步验证。")
    user = confirm_user(db, principal, payload.password, payload.otp)
    user.totp_secret = None
    user.totp_pending_secret = None
    user.totp_last_step = None
    revoke_sessions(db, user.id)
    audit(db, "mfa_removed", user.id, user.id)
    db.commit()
    return Response(status_code=204)


@router.post("/introspect")
def introspect(payload: IntrospectRequest, request: Request, db: Db) -> dict:
    if payload.audience not in AUDIENCES:
        raise HTTPException(400, "未知应用。")
    principal, _, _ = resolve_request_identity(db, request, payload.audience, bearer_only=True)
    if payload.audience not in {"home", "identity"}:
        enforce_mfa(db, principal)
    return principal


@router.post("/delegations", status_code=201)
def delegate(payload: DelegationRequest, request: Request, db: Db) -> dict:
    if payload.audience not in AUDIENCES:
        raise HTTPException(400, "未知应用。")
    principal, parent_kind, parent = resolve_request_identity(db, request, bearer_only=True)
    enforce_mfa(db, principal)
    service_token = request.headers.get("x-studio-service-token")
    service_delegate = False
    if service_token:
        service, kind, _ = resolve_token(db, service_token, "identity")
        service_delegate = kind == "service" and service["team_id"] == principal["team_id"] and "identity:delegate" in service["scopes"]
        if not service_delegate:
            raise HTTPException(403, "服务没有任务委托权限。")
    if parent_kind == "delegation" and not service_delegate:
        raise HTTPException(403, "任务凭证不能自行扩大或重新委托权限。")
    if parent_kind == "service" and payload.audience not in parent.audiences:
        raise HTTPException(403, "服务没有目标应用权限。")
    # Backend-mediated regrant retains the subject and original revocation chain.
    if parent_kind == "delegation" and service_delegate:
        root = parent
        while isinstance(root, Delegation):
            _, _, root = resolve_token_by_record(db, root.parent_kind, root.parent_id)
        if isinstance(root, ServiceCredential) and payload.audience not in root.audiences:
            raise HTTPException(403, "原服务身份没有目标应用权限。")
    token = new_token()
    expires_at = now() + timedelta(seconds=payload.ttl_seconds)
    if parent_kind != "local":
        expires_at = min(expires_at, utc(parent.expires_at))
    record = Delegation(token_hash=token_hash(token), parent_kind=parent_kind, parent_id=parent.id,
                        audience=payload.audience, resource_scope=payload.resource_scope.model_dump(), expires_at=expires_at)
    db.add(record)
    db.flush()
    audit(db, "delegation_created", principal["user_id"], record.id, audience=payload.audience, resource_scope=record.resource_scope)
    db.commit()
    return {"token": token, "expires_at": expires_at.isoformat()}


def resolve_token_by_record(db: Session, kind: str, identifier: str):
    from home_api.services.identity import resolve_record
    principal, record = resolve_record(db, kind, identifier)
    return principal, kind, record


@router.post("/delegations/revoke", status_code=204)
def revoke_delegation(payload: RevokeRequest, request: Request, db: Db) -> Response:
    principal, kind, caller = resolve_request_identity(db, request, bearer_only=True)
    target = db.scalar(select(Delegation).where(Delegation.token_hash == token_hash(payload.token)))
    if not target:
        raise HTTPException(404, "任务凭证不存在。")
    subject, _, _ = resolve_token_by_record(db, target.parent_kind, target.parent_id)
    same_subject = kind in {"session", "local"} and subject["user_id"] == principal["user_id"]
    same_service = kind == "service" and principal["team_id"] == subject["team_id"] and "identity:delegate" in principal["scopes"]
    if not (same_subject or same_service or (kind == "delegation" and caller.id == target.id)):
        raise HTTPException(403, "不能撤销其他人的任务凭证。")
    target.revoked_at = now()
    audit(db, "delegation_revoked", principal["user_id"], target.id)
    db.commit()
    return Response(status_code=204)
