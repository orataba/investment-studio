from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from studio_identity import LOCAL_OWNER_CREDENTIAL

from home_api.core.settings import get_settings
from home_api.db.models import AuditEvent, Delegation, Membership, OneTimeToken, ServiceCredential, SessionRecord, Team, User, now
from home_api.services.auth import new_token, token_hash

AUDIENCES = {"home", "identity", "watchlist", "portfolio", "briefing", "regime"}


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def audit(db: Session, action: str, actor: str | None = None, target: str | None = None, **detail) -> None:
    db.add(AuditEvent(action=action, actor_user_id=actor, target_id=target, detail=detail))


def user_principal(db: Session, user_id: str) -> dict:
    row = db.execute(
        select(User, Membership, Team)
        .join(Membership, Membership.user_id == User.id)
        .join(Team, Team.id == Membership.team_id)
        .where(User.id == user_id, User.active.is_(True))
    ).one_or_none()
    if row is None:
        raise HTTPException(401, "账号或团队成员资格无效。")
    user, membership, team = row
    return {"kind": "user", "user_id": user.id, "username": user.username, "display_name": user.display_name,
            "team_id": team.id, "team_name": team.name, "team_role": membership.role,
            "is_team_owner": team.owner_user_id == user.id, "scopes": []}


def _active(credential) -> None:
    if not credential or credential.revoked_at or utc(credential.expires_at) <= now():
        raise HTTPException(401, "登录或任务凭证已失效。")


def resolve_record(db: Session, kind: str, identifier: str, audience: str | None = None) -> tuple[dict, object]:
    if kind == "local":
        if get_settings().auth_mode != "local":
            raise HTTPException(401, "云端账号模式不接受本机身份。")
        team = db.get(Team, "default")
        if not team or team.owner_user_id != identifier:
            raise HTTPException(401, "本机拥有者身份已失效。")
        principal = {**user_principal(db, identifier), "team_role": "admin", "local_unrestricted": True}
        record = db.get(User, identifier)
    elif kind == "session":
        record = db.get(SessionRecord, identifier)
        _active(record)
        principal = user_principal(db, record.user_id)
        principal["session_id"] = record.id
    elif kind == "service":
        record = db.get(ServiceCredential, identifier)
        _active(record)
        if audience and audience not in record.audiences:
            raise HTTPException(403, "服务凭证不允许访问此应用。")
        if not db.get(Team, record.team_id):
            raise HTTPException(401, "团队不存在。")
        principal = {"kind": "service", "user_id": None, "display_name": record.display_name,
                     "service_id": record.service_id, "team_id": record.team_id, "team_role": "reader",
                     "is_team_owner": False, "scopes": record.scopes}
    elif kind == "delegation":
        record = db.get(Delegation, identifier)
        _active(record)
        if audience and audience != record.audience:
            raise HTTPException(403, "任务凭证不允许访问此应用。")
        principal, _ = resolve_record(db, record.parent_kind, record.parent_id)
        principal = {**principal, "resource_scope": record.resource_scope}
    else:
        raise HTTPException(401, "无效凭证。")
    return principal, record


def resolve_token(db: Session, token: str | None, audience: str | None = None) -> tuple[dict, str, object]:
    if not token:
        raise HTTPException(401, "请先登录。")
    if token == LOCAL_OWNER_CREDENTIAL:
        if get_settings().auth_mode != "local":
            raise HTTPException(401, "云端账号模式不接受本机身份。")
        team = db.get(Team, "default")
        if not team:
            raise HTTPException(503, "请先初始化本机团队拥有者。")
        principal, record = resolve_record(db, "local", team.owner_user_id, audience)
        return principal, "local", record
    digest = token_hash(token)
    for kind, model in (("session", SessionRecord), ("service", ServiceCredential), ("delegation", Delegation)):
        record = db.scalar(select(model).where(model.token_hash == digest))
        if record:
            principal, record = resolve_record(db, kind, record.id, audience)
            return principal, kind, record
    raise HTTPException(401, "无效凭证。")


def new_session(db: Session, user_id: str, ttl_seconds: int) -> str:
    token = new_token()
    db.add(SessionRecord(user_id=user_id, token_hash=token_hash(token), expires_at=now() + timedelta(seconds=ttl_seconds)))
    return token


def lock_user(db: Session, user_id: str) -> User | None:
    """Lock before changing account credentials; lock Team first if also needed.

    Session/token issuance and revocation share this account lock. Refresh any
    user read during authorization before checking passwords or changing state.
    """
    return db.scalar(select(User).where(User.id == user_id).with_for_update()
                     .execution_options(populate_existing=True))


def revoke_sessions(db: Session, user_id: str) -> None:
    db.execute(update(SessionRecord).where(SessionRecord.user_id == user_id, SessionRecord.revoked_at.is_(None)).values(revoked_at=now()))


def one_time_token(db: Session, user_id: str, purpose: str) -> str:
    revoke_one_time_tokens(db, user_id)
    token = new_token()
    db.add(OneTimeToken(user_id=user_id, purpose=purpose, token_hash=token_hash(token), expires_at=now() + timedelta(hours=24)))
    return token


def revoke_one_time_tokens(db: Session, user_id: str) -> None:
    db.execute(update(OneTimeToken).where(OneTimeToken.user_id == user_id, OneTimeToken.consumed_at.is_(None)).values(consumed_at=now()))
