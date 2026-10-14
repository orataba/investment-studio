"""Team business guidance and role-specific projections of frozen instructions."""
from copy import deepcopy
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from studio_identity import current_principal

from watchlist_app.db.models.research_guidance import ResearchGuidance
from watchlist_app.services.research_skills import SECTION_SKILLS


class TeamSectionSkills(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overview: str = Field(default=SECTION_SKILLS["overview"], max_length=20000)
    recent_updates: str = Field(default=SECTION_SKILLS["recent_updates"], max_length=20000)
    themes: str = Field(default=SECTION_SKILLS["themes"], max_length=20000)
    quantitative: str = Field(default=SECTION_SKILLS["quantitative"], max_length=20000)
    risk: str = Field(default="关注对当前投资判断和实际组合敞口有意义的风险，区分已经确认、尚待核实与数据不足；说明影响路径、优先级与下一观察条件。", max_length=20000)
    review: str = Field(default="优先核对会改变投资判断的事实、数字、日期与因果前提，检查反证、覆盖限制及请求范围内的前后一致性；保留有证据支持的判断与限定条件。", max_length=20000)


class GuidanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    guidance: str = Field(max_length=60000)
    section_skills: TeamSectionSkills
    expected_revision: int = Field(ge=0)


def _record(row, team_id, *, include_history=True):
    if row is None:
        return {"team_id": team_id, "revision": 0, "guidance": "",
                "section_skills": TeamSectionSkills().model_dump(), "author": {"origin": "initial"},
                "updated_at": None, **({"versions": []} if include_history else {})}
    return {"team_id": team_id, "revision": row.revision, "guidance": row.guidance,
            "section_skills": deepcopy(row.section_skills), "author": deepcopy(row.author),
            "updated_at": row.updated_at.isoformat(),
            **({"versions": deepcopy(row.versions)} if include_history else {})}


def read_guidance(session, *, team_id=None, include_history=True):
    team_id = team_id or current_principal().team_id
    return _record(session.get(ResearchGuidance, team_id), team_id, include_history=include_history)


def save_guidance(session, payload):
    principal = current_principal()
    if principal.kind == "service" or (not principal.local_unrestricted and principal.team_role not in {"admin", "member"}):
        raise HTTPException(403, "仅团队可写成员可修改研究指导")
    team_id = principal.team_id
    row = session.scalar(select(ResearchGuidance).where(ResearchGuidance.team_id == team_id).with_for_update())
    current = _record(row, team_id, include_history=False)
    if current["revision"] != payload.expected_revision:
        raise HTTPException(409, "研究指导已由其他成员更新，请重新读取后保存")
    value = {"guidance": payload.guidance, "section_skills": payload.section_skills.model_dump()}
    if all(current[key] == value[key] for key in value):
        return read_guidance(session)
    values = {**value, "revision": current["revision"] + 1,
              "author": {"origin": "user", "user_id": principal.user_id, "display_name": principal.display_name},
              "updated_at": datetime.now(UTC), "versions": [*(row.versions if row else []), current]}
    if row is None:
        try:
            with session.begin_nested():
                session.add(ResearchGuidance(team_id=team_id, **values))
                session.flush()
        except IntegrityError as error:
            raise HTTPException(409, "研究指导已由其他成员创建，请重新读取后保存") from error
    else:
        result = session.execute(update(ResearchGuidance).where(ResearchGuidance.team_id == team_id,
            ResearchGuidance.revision == payload.expected_revision).values(**values))
        if result.rowcount != 1:
            raise HTTPException(409, "研究指导已由其他成员更新，请重新读取后保存")
    session.commit()
    return read_guidance(session)


def freeze_guidance(session, context):
    """Called at preparation only. Resume/review never consult mutable settings."""
    return read_guidance(session, team_id=context["research_actor"]["team_id"], include_history=False)


def role_guidance(context, role):
    saved = context.get("research_guidance")
    if saved is None:
        return None
    return {"team_id": saved["team_id"], "revision": saved["revision"], "role": role,
            "guidance": saved["guidance"], "skill": saved["section_skills"][role],
            "author": deepcopy(saved["author"]), "updated_at": saved["updated_at"],
            "usage_note": "用户业务指导仅定义研究方法、重点与表达，不是事实来源，不改变固定证据、权限、时间或独立审核合同。"}
