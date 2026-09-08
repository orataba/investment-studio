"""Portfolio authorization is independent of a member's team role."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from studio_identity import Principal, current_principal

from portfolio_app.db.models import (
    PortfolioRecordModel, PortfolioAccessStateModel, PortfolioMembershipModel,
    PortfolioAccessAuditModel, PortfolioUserPreferenceModel,
)
from portfolio_app.db.session import get_session_factory

PortfolioRole = Literal["manager", "editor", "viewer"]
ROLE_LEVEL = {"viewer": 1, "editor": 2, "manager": 3}


def now() -> str:
    return datetime.now(UTC).isoformat()


def actor() -> Principal:
    principal = current_principal()
    if principal is None:
        raise HTTPException(401, "请先登录")
    return principal


def _scope_allows(principal: Principal, portfolio_id: str) -> bool:
    scope = principal.resource_scope
    if not scope:
        return True
    # Capture runs are resolved against their stored parent by the API boundary.
    if scope.get("kind") == "capture":
        from portfolio_app.db.models import TransactionCaptureBatchModel
        with get_session_factory()() as session:
            batch = session.get(TransactionCaptureBatchModel, scope.get("id"))
            return batch is not None and batch.portfolio_id == portfolio_id
    return scope.get("kind") == "portfolio" and scope.get("id") == portfolio_id


def access_in_session(session: Session, portfolio_id: str, principal: Principal) -> dict:
    state = session.get(PortfolioAccessStateModel, portfolio_id)
    if not _scope_allows(principal, portfolio_id):
        raise HTTPException(404, "组合不存在或无权访问")
    role = None
    if principal.local_unrestricted:
        if session.get(PortfolioRecordModel, portfolio_id) is None:
            raise HTTPException(404, "组合不存在")
        role = "manager"
    elif state is None or state.team_id != principal.team_id:
        raise HTTPException(404, "组合不存在或无权访问")
    elif principal.kind == "user" and principal.user_id:
        member = session.get(PortfolioMembershipModel, (portfolio_id, principal.user_id))
        role = member.role if member else None
    elif principal.kind == "service" and "portfolio:maintain" in principal.scopes:
        # Maintenance may read materialization inputs; it can never edit ledger facts.
        role = "viewer"
    elif principal.kind == "service" and "portfolio:read" in principal.scopes:
        # Explicit team-wide read grant for unattended portfolio risk research.
        role = "viewer"
    if role is None:
        raise HTTPException(404, "组合不存在或无权访问")
    return {"portfolio_id": portfolio_id, "team_id": state.team_id if state else principal.team_id, "user_id": principal.user_id, "role": role,
            "local_unrestricted": principal.local_unrestricted,
            "can_read": True, "can_edit": ROLE_LEVEL[role] >= 2,
            "can_manage": role == "manager"}


def require_access(portfolio_id: str, role: PortfolioRole = "viewer", principal: Principal | None = None) -> dict:
    principal = principal or actor()
    with get_session_factory()() as session:
        access = access_in_session(session, portfolio_id, principal)
    if ROLE_LEVEL[access["role"]] < ROLE_LEVEL[role]:
        raise HTTPException(403, "当前账号没有此组合的操作权限")
    return access


def visible_ids(principal: Principal | None = None) -> list[str]:
    principal = principal or actor()
    with get_session_factory()() as session:
        if principal.local_unrestricted:
            return [portfolio_id for portfolio_id in session.scalars(select(PortfolioRecordModel.portfolio_id)) if _scope_allows(principal, portfolio_id)]
        query = select(PortfolioAccessStateModel.portfolio_id).where(PortfolioAccessStateModel.team_id == principal.team_id)
        if principal.kind == "user":
            query = query.join(PortfolioMembershipModel, PortfolioMembershipModel.portfolio_id == PortfolioAccessStateModel.portfolio_id).where(PortfolioMembershipModel.user_id == principal.user_id)
        elif "portfolio:maintain" not in principal.scopes and "portfolio:read" not in principal.scopes:
            return []
        return [portfolio_id for portfolio_id in session.scalars(query) if _scope_allows(principal, portfolio_id)]


def record_access_event(session: Session, portfolio_id: str, action: str, details: dict, principal: Principal) -> None:
    session.add(PortfolioAccessAuditModel(
        event_id=str(uuid4()), portfolio_id=portfolio_id, actor_user_id=principal.user_id,
        actor_name=principal.display_name, action=action, details_json=details, occurred_at=now(),
    ))


def initialize_access(session: Session, portfolio_id: str, principal: Principal) -> None:
    if principal.kind != "user" or not principal.user_id:
        raise HTTPException(403, "组合必须由团队成员创建")
    session.add(PortfolioAccessStateModel(portfolio_id=portfolio_id, team_id=principal.team_id))
    session.add(PortfolioMembershipModel(portfolio_id=portfolio_id, user_id=principal.user_id,
        display_name=principal.display_name, role="manager", granted_by=principal.user_id, granted_at=now()))
    record_access_event(session, portfolio_id, "created", {"manager_user_id": principal.user_id}, principal)


def get_preference(key: str, principal: Principal | None = None) -> dict | None:
    principal = principal or actor()
    if not principal.user_id:
        return None
    with get_session_factory()() as session:
        row = session.get(PortfolioUserPreferenceModel, (principal.user_id, key))
        return row.value_json if row else None


def save_preference(key: str, value: dict, principal: Principal | None = None) -> dict:
    principal = principal or actor()
    if not principal.user_id or principal.kind != "user":
        raise HTTPException(403, "个人设置仅供登录成员使用")
    with get_session_factory()() as session:
        row = session.get(PortfolioUserPreferenceModel, (principal.user_id, key))
        if row is None:
            row = PortfolioUserPreferenceModel(user_id=principal.user_id, preference_key=key)
            session.add(row)
        row.value_json = value
        row.updated_at = now()
        session.commit()
    return value
