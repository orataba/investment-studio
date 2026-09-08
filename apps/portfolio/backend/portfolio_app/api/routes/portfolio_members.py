from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, or_
from studio_identity import team_directory

from portfolio_app.db.models import PortfolioRecordModel, PortfolioAccessStateModel, PortfolioMembershipModel, PortfolioAccessAuditModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.portfolio_access import actor, require_access, access_in_session, now, record_access_event

router = APIRouter()


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["manager", "editor", "viewer"]


class RecoveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)


def directory():
    return [member for member in team_directory(actor()) if member.get("status") == "active"]


@router.get("/{portfolio_id}/access")
def portfolio_access(portfolio_id: str):
    return require_access(portfolio_id)


@router.get("/{portfolio_id}/member-candidates")
def member_candidates(portfolio_id: str):
    require_access(portfolio_id, "manager")
    return {"members": directory()}


@router.get("/{portfolio_id}/members")
def members(portfolio_id: str):
    require_access(portfolio_id, "manager")
    with get_session_factory()() as session:
        rows = session.scalars(select(PortfolioMembershipModel).where(PortfolioMembershipModel.portfolio_id == portfolio_id)).all()
        return {"portfolio_id": portfolio_id, "members": [
            {"user_id": row.user_id, "display_name": row.display_name, "role": row.role, "granted_by": row.granted_by, "granted_at": row.granted_at}
            for row in rows]}


def change_member(portfolio_id: str, user_id: str, role: str | None):
    principal = actor()
    active_members = {member["user_id"]: member for member in directory()}
    if role is not None and user_id not in active_members:
        raise HTTPException(422, "只能授权给本团队已激活的成员")
    with get_session_factory()() as session:
        # Serialize manager transfers on the portfolio's access row.
        state = session.scalar(select(PortfolioAccessStateModel).where(PortfolioAccessStateModel.portfolio_id == portfolio_id).with_for_update())
        if access_in_session(session, portfolio_id, principal)["role"] != "manager":
            raise HTTPException(403, "仅当前管理者可以变更组合授权")
        if state is None:
            session.add(PortfolioAccessStateModel(portfolio_id=portfolio_id, team_id=principal.team_id))
        existing = session.get(PortfolioMembershipModel, (portfolio_id, user_id))
        if existing and existing.role == "manager" and role != "manager":
            others = session.scalars(select(PortfolioMembershipModel).where(
                PortfolioMembershipModel.portfolio_id == portfolio_id,
                PortfolioMembershipModel.role == "manager", PortfolioMembershipModel.user_id != user_id)).all()
            if not principal.local_unrestricted and not any(member.user_id in active_members for member in others):
                raise HTTPException(409, "请先授予另一位有效成员管理权限，再移除或降低最后一位管理者")
        previous_role = existing.role if existing else None
        if role is None:
            if existing is None:
                raise HTTPException(404, "组合成员不存在")
            session.delete(existing)
        else:
            if existing is None:
                existing = PortfolioMembershipModel(portfolio_id=portfolio_id, user_id=user_id)
                session.add(existing)
            existing.role = role
            existing.display_name = active_members[user_id]["display_name"]
            existing.granted_by = principal.user_id
            existing.granted_at = now()
        record_access_event(session, portfolio_id, "membership_changed", {"user_id": user_id, "before_role": previous_role, "role": role}, principal)
        session.commit()
    return {"portfolio_id": portfolio_id, "user_id": user_id, "role": role}


@router.put("/{portfolio_id}/members/{user_id}")
def put_member(portfolio_id: str, user_id: str, payload: MemberInput):
    return change_member(portfolio_id, user_id, payload.role)


@router.delete("/{portfolio_id}/members/{user_id}")
def remove_member(portfolio_id: str, user_id: str):
    return change_member(portfolio_id, user_id, None)


@router.post("/{portfolio_id}/recover-access")
def recover_access(portfolio_id: str, payload: RecoveryInput):
    principal = actor()
    if not principal.is_team_owner or principal.kind != "user" or principal.resource_scope:
        raise HTTPException(403, "仅团队拥有者可以恢复组合管理权限")
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(422, "请填写恢复管理权限的原因")
    target = next((item for item in directory() if item["user_id"] == payload.user_id), None)
    if target is None:
        raise HTTPException(422, "管理者必须是本团队已激活的成员")
    with get_session_factory()() as session:
        portfolio = session.scalar(select(PortfolioRecordModel).where(PortfolioRecordModel.portfolio_id == portfolio_id).with_for_update())
        if portfolio is None:
            raise HTTPException(404, "组合不存在")
        state = session.get(PortfolioAccessStateModel, portfolio_id)
        if state and state.team_id != principal.team_id and not principal.local_unrestricted:
            raise HTTPException(404, "组合不存在")
        if state is None:
            session.add(PortfolioAccessStateModel(portfolio_id=portfolio_id, team_id=principal.team_id))
        row = session.get(PortfolioMembershipModel, (portfolio_id, payload.user_id))
        if row is None:
            row = PortfolioMembershipModel(portfolio_id=portfolio_id, user_id=payload.user_id)
            session.add(row)
        row.role = "manager"
        row.display_name = target["display_name"]
        row.granted_at = now()
        row.granted_by = principal.user_id
        record_access_event(session, portfolio_id, "manager_recovered", {"manager_user_id": payload.user_id, "reason": reason}, principal)
        session.commit()
    return {"portfolio_id": portfolio_id, "user_id": payload.user_id, "role": "manager"}


@router.get("/{portfolio_id}/access-audit")
def access_audit(portfolio_id: str):
    require_access(portfolio_id, "manager")
    with get_session_factory()() as session:
        rows = session.scalars(select(PortfolioAccessAuditModel).where(PortfolioAccessAuditModel.portfolio_id == portfolio_id).order_by(PortfolioAccessAuditModel.occurred_at.desc())).all()
        return {"events": [{"event_id": row.event_id, "actor_user_id": row.actor_user_id, "actor_name": row.actor_name, "action": row.action, "details": row.details_json, "occurred_at": row.occurred_at} for row in rows]}


@router.get("/access-recovery")
def recovery_directory():
    principal = actor()
    if not principal.is_team_owner or principal.kind != "user" or principal.resource_scope:
        raise HTTPException(403, "仅团队拥有者可以打开管理权限恢复目录")
    active_members = directory()
    active_ids = {member["user_id"] for member in active_members}
    with get_session_factory()() as session:
        rows = session.execute(select(PortfolioRecordModel.portfolio_id, PortfolioRecordModel.portfolio_name).outerjoin(PortfolioAccessStateModel, PortfolioAccessStateModel.portfolio_id == PortfolioRecordModel.portfolio_id).where(or_(PortfolioAccessStateModel.team_id == principal.team_id, PortfolioAccessStateModel.team_id.is_(None)))).all()
        manager_ids = session.execute(select(PortfolioMembershipModel.portfolio_id, PortfolioMembershipModel.user_id).where(PortfolioMembershipModel.role == "manager")).all()
        managed_ids = {portfolio_id for portfolio_id, user_id in manager_ids if user_id in active_ids}
        result = [{"portfolio_id": row.portfolio_id, "portfolio_name": row.portfolio_name, "has_active_manager": row.portfolio_id in managed_ids} for row in rows]
        record_access_event(session, "*", "recovery_directory_opened", {"portfolio_ids": [row["portfolio_id"] for row in result]}, principal)
        session.commit()
    return {"portfolios": result, "members": [{"user_id": row["user_id"], "display_name": row["display_name"]} for row in active_members]}
