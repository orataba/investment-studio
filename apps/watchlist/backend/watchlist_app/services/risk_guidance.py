"""Two human-owned risk guidance layers, backed by existing versioned records."""
from copy import deepcopy
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from studio_identity import current_principal

from watchlist_app.services import research_dossier, research_guidance
from watchlist_app.services.research_access import require_team_write


CONSTRAINTS = [
    "指导只改变关注重点、筛噪、粗略分级和表达；固定指标、阈值、数据有效性和来源权限仍由服务端执行。",
    "标的补充在本标的范围内细化通用指导，不能取消有效严重规则的事实提示。",
    "外部新闻、公告和研报是证据，不是指导。保存仅用于后续分析，不重跑历史、不发送通知。",
]


class RiskGuidanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope: Literal["general", "instrument"]
    instrument_id: str | None = None
    text: str = Field(max_length=20000)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_scope(self):
        if self.scope == "instrument" and not self.instrument_id:
            raise ValueError("标的指导须指定标的")
        if not self.reason.strip():
            raise ValueError("请填写修改原因")
        return self


def _general(record):
    return {"text": record["section_skills"]["risk"], "revision": record["revision"],
            "version_id": f"team-risk:{record['team_id']}:v{record['revision']}",
            "author": deepcopy(record["author"]), "updated_at": record["updated_at"]}


def _instrument(record, instrument_id):
    version_id = record.get("version_id")
    revision = record.get("revision")
    if revision is None:
        revision = int(version_id.rsplit(":v", 1)[1]) if version_id else 0
    if version_id is None and record.get("entry_id"):
        version_id = f"{record['entry_id']}:v{revision}"
    return {"instrument_id": instrument_id, "text": record.get("risk_guidance", ""),
            "revision": revision, "version_id": version_id,
            "author": deepcopy(record.get("author")), "updated_at": record.get("updated_at")}


def _risk_record(record, convert, include_history):
    # The backing record also versions unrelated research fields. Report the
    # author/time of the actual risk-text change while retaining its current
    # revision for optimistic concurrency on writes to that shared record.
    changes = []
    for value in [*record.get("versions", []), record]:
        item = convert(value)
        if not changes and "instrument_id" in item and item["text"] == "":
            # A pre-existing mandate does not prove anyone saved risk guidance.
            # Later explicit clears retain their real revision and provenance.
            item = {**item, "revision": 0, "version_id": None, "author": None, "updated_at": None}
        if not changes or item["text"] != changes[-1]["text"]:
            changes.append(item)
    current = convert(record)
    result = {**changes[-1], "content_revision": changes[-1]["revision"], "revision": current["revision"]}
    if include_history:
        result["versions"] = changes[:-1]
    return result


def read_risk_guidance(session, instrument_id=None, *, include_history=True):
    record = research_guidance.read_guidance(session)
    general = _risk_record(record, _general, include_history)
    instrument = None
    if instrument_id:
        saved = research_dossier.read_mandate(session, instrument_id)
        instrument = _risk_record(saved, lambda value: _instrument(value, instrument_id), include_history)
    return {"general": general, "instrument": instrument, "constraints": list(CONSTRAINTS)}


def save_risk_guidance(session, payload: RiskGuidanceInput):
    principal = current_principal()
    if principal.kind != "user":
        raise HTTPException(403, "仅有权限的团队成员可修改风险指导")
    require_team_write()
    if payload.instrument_id:
        # Validate the response scope before committing a team-wide edit.
        research_dossier.require_instrument(session, payload.instrument_id)
    if payload.scope == "general":
        record = research_guidance.read_guidance(session)
        research_guidance.save_guidance(session, research_guidance.GuidanceInput(
            guidance=record["guidance"], section_skills={**record["section_skills"], "risk": payload.text},
            expected_revision=payload.expected_revision, reason=payload.reason.strip()))
    else:
        # Acquire the publication lock before reading fields retained by this
        # narrow edit. save_mandate verifies the user's revision under that lock.
        research_dossier.dossier_topic(session, payload.instrument_id)
        entry = research_dossier._mandate_entry(session, payload.instrument_id)
        if entry is not None:
            session.refresh(entry)
        current = research_dossier.read_mandate(session, payload.instrument_id)
        value = {key: current[key] for key in research_dossier.ResearchMandateInput.model_fields}
        value["risk_guidance"] = payload.text
        research_dossier.save_mandate(session, payload.instrument_id,
            research_dossier.ResearchMandateInput.model_validate(value), origin="user",
            expected_revision=payload.expected_revision, change_reason=payload.reason.strip(), preserve_research_guidance=True)
    return read_risk_guidance(session, payload.instrument_id)


def freeze_risk_guidance(session, instrument_ids):
    """Freeze at preparation; resumed runs must keep their original guidance."""
    record = read_risk_guidance(session, include_history=False)
    return {"general": record["general"], "instruments": {
        instrument_id: _risk_record(research_dossier.read_mandate(session, instrument_id),
            lambda value: _instrument(value, instrument_id), False)
        for instrument_id in dict.fromkeys(instrument_ids)}, "constraints": record["constraints"]}


def preview_retained_cases(session, **scope):
    """Inspect actual prior selections; never claim to simulate unsaved prose."""
    from watchlist_app.services.risk_officer import review_workspace
    from watchlist_app.services.risk_review_state import current_scope
    from watchlist_app.services.read_models import serialize_payload
    workspace = review_workspace(session, **scope)
    state = current_scope(session, scope)
    selected = {case_id for notice in workspace["notice_items"] for case_id in notice["case_ids"]}
    evaluated = workspace["notice_status"] == "assessed"
    cases = sorted(state["cases"], key=lambda case: (case["case_id"] not in selected, case["case_id"]))
    return serialize_payload({"preview_kind": "retained_cases", "scope": workspace["scope"],
        "notice_items": workspace["notice_items"], "notice_status": workspace["notice_status"],
        "case_count": len(cases), "sample_limit": 12,
        "cases": [{"case_id": case["case_id"], "title": case["title"], "body": case.get("body"),
            "observed_on": case.get("observed_on"), "status": case["status"], "trigger_active": case["trigger_active"],
            "selection": "selected" if case["case_id"] in selected else "not_selected" if evaluated else "not_assessed",
            "assessment_reason": ((case.get("evidence_json") or {}).get("risk_assessment") or {}).get("reason")}
            for case in cases[:12]], "limitations": workspace["limitations"],
        "usage_note": "这里只抽取最多12个已留存案例对照最近正式筛选，未按未保存指导调用模型；不是新指导效果的模拟。不会保存正式风险状态或发送通知。"})
