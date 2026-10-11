"""Portfolio-private PM judgments. No research, target or transaction side effects."""
from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from investment_studio_instrument_core.db_models import Instrument

from portfolio_app.db.models import (
    PortfolioInvestmentStanceModel as Stance,
    PortfolioInvestmentViewModel as Note,
    PortfolioInvestmentViewRevisionModel as Revision,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.portfolio_access import access_in_session, actor, now, record_access_event


def _access(session, portfolio_id: str, *, write: bool = False):
    principal = actor()
    access = access_in_session(session, portfolio_id, principal)
    if write and (not access["can_edit"] or principal.kind != "user" or not principal.user_id or principal.resource_scope):
        raise HTTPException(403, "投资观点只能由有编辑权限的组合成员保存")
    return principal, access


def _note(session, portfolio_id: str, note_id: str):
    note = session.get(Note, (portfolio_id, note_id))
    if note is None:
        raise HTTPException(404, "投资观点不存在或无权访问")
    return note


def _record(note, revision, principal, access):
    instruments = revision.instrument_refs_json
    return {
        "portfolio_id": note.portfolio_id, "note_id": note.note_id,
        "revision_number": revision.revision_number, "view_date": revision.view_date,
        "title": revision.title, "body": revision.body, "context": revision.context, "source": revision.source,
        "instrument_ids": [item["instrument_id"] for item in instruments], "instruments": instruments,
        "author_user_id": note.author_user_id, "author_name": note.author_name, "created_at": note.created_at,
        "revised_by_user_id": revision.revised_by_user_id, "revised_by_name": revision.revised_by_name,
        "updated_at": revision.updated_at,
        "can_edit": bool(access["can_edit"] and principal.kind == "user" and not principal.resource_scope
            and (access["can_manage"] or note.author_user_id == principal.user_id)),
    }


def _stance(session, portfolio_id, principal, access):
    stance = session.get(Stance, portfolio_id)
    if stance is None or stance.note_id is None:
        return {"current_stance": None, "stance_version": stance.selection_version if stance else 0}
    note = _note(session, portfolio_id, stance.note_id)
    revision = session.get(Revision, (portfolio_id, stance.note_id, stance.revision_number))
    return {"current_stance": {
        "note": _record(note, revision, principal, access),
        "selected_at": stance.selected_at, "selected_by_user_id": stance.selected_by_user_id,
        "selected_by_name": stance.selected_by_name,
    }, "stance_version": stance.selection_version}


def list_investment_views(portfolio_id: str, instrument_id: str | None = None):
    with get_session_factory()() as session:
        principal, access = _access(session, portfolio_id)
        rows = session.execute(select(Note, Revision).join(Revision,
            (Revision.portfolio_id == Note.portfolio_id) & (Revision.note_id == Note.note_id)
            & (Revision.revision_number == Note.latest_revision)).where(Note.portfolio_id == portfolio_id)
            .order_by(Revision.view_date.desc(), Note.created_at.desc(), Note.note_id)).all()
        notes = [_record(note, revision, principal, access) for note, revision in rows]
        if instrument_id:
            notes = [note for note in notes if instrument_id in note["instrument_ids"]]
        return {"portfolio_id": portfolio_id, "notes": notes, **_stance(session, portfolio_id, principal, access)}


def investment_view_revisions(portfolio_id: str, note_id: str):
    with get_session_factory()() as session:
        principal, access = _access(session, portfolio_id)
        note = _note(session, portfolio_id, note_id)
        rows = session.scalars(select(Revision).where(Revision.portfolio_id == portfolio_id,
            Revision.note_id == note_id).order_by(Revision.revision_number.desc())).all()
        return {"portfolio_id": portfolio_id, "note_id": note_id,
            "revisions": [_record(note, revision, principal, access) for revision in rows]}


def investment_view_instruments(portfolio_id: str):
    with get_session_factory()() as session:
        _access(session, portfolio_id)
        rows = session.execute(select(Instrument.instrument_id, Instrument.instrument_name, Instrument.instrument_type)
            .order_by(Instrument.instrument_name, Instrument.instrument_id)).mappings().all()
        return {"portfolio_id": portfolio_id, "instruments": [dict(row) for row in rows]}


def _instrument_refs(session, instrument_ids: list[str]):
    if not instrument_ids:
        return []
    rows = session.execute(select(Instrument.instrument_id, Instrument.instrument_name, Instrument.instrument_type)
        .where(Instrument.instrument_id.in_(instrument_ids))).mappings().all()
    metadata = {row["instrument_id"]: dict(row) for row in rows}
    refs = []
    for instrument_id in instrument_ids:
        instrument = metadata.get(instrument_id)
        if not instrument:
            # Do not resolve local holdings/contracts, or reveal their owning portfolio.
            raise HTTPException(422, "关联资产必须是已登记的共享资产")
        refs.append(instrument)
    return refs


def save_investment_view(portfolio_id: str, values: dict, *, note_id: str | None = None, expected_revision: int | None = None):
    with get_session_factory()() as session:
        principal, access = _access(session, portfolio_id, write=True)
        stamp = now()
        if note_id is None:
            note = Note(portfolio_id=portfolio_id, note_id=str(uuid4()), latest_revision=1,
                author_user_id=principal.user_id, author_name=principal.display_name, created_at=stamp)
            session.add(note)
            session.flush()
            revision_number = 1
        else:
            note = _note(session, portfolio_id, note_id)
            if not access["can_manage"] and note.author_user_id != principal.user_id:
                raise HTTPException(403, "只能更正自己记录的观点；组合管理者可以代为更正并留下作者记录")
            if note.latest_revision != expected_revision:
                raise HTTPException(409, "这条观点已有新版本，请读取最新版本后再更正；你的草稿尚未保存")
            revision_number = expected_revision + 1
            result = session.execute(update(Note).where(Note.portfolio_id == portfolio_id,
                Note.note_id == note_id, Note.latest_revision == expected_revision)
                .values(latest_revision=revision_number))
            if result.rowcount != 1:
                raise HTTPException(409, "这条观点已有新版本，请读取最新版本后再更正；你的草稿尚未保存")
        refs = _instrument_refs(session, values["instrument_ids"])
        revision = Revision(portfolio_id=portfolio_id, note_id=note.note_id, revision_number=revision_number,
            view_date=values["view_date"], title=values["title"], body=values["body"], context=values["context"],
            source=values["source"],
            instrument_refs_json=refs, revised_by_user_id=principal.user_id,
            revised_by_name=principal.display_name, updated_at=stamp)
        session.add(revision)
        record_access_event(session, portfolio_id, "investment_view_created" if note_id is None else "investment_view_corrected",
            {"note_id": note.note_id, "revision_number": revision_number}, principal)
        session.commit()
        return _record(note, revision, principal, access)


def select_investment_stance(portfolio_id: str, *, note_id: str | None, revision_number: int | None, expected_version: int):
    with get_session_factory()() as session:
        principal, access = _access(session, portfolio_id, write=True)
        if note_id is not None:
            _note(session, portfolio_id, note_id)
            revision = session.get(Revision, (portfolio_id, note_id, revision_number))
            if revision is None:
                raise HTTPException(404, "观点版本不存在或无权访问")
            if revision.instrument_refs_json:
                raise HTTPException(422, "当前总体观点须选择未限定关联资产的组合整体判断")
        values = {"note_id": note_id, "revision_number": revision_number,
            "selection_version": expected_version + 1, "selected_at": now(),
            "selected_by_user_id": principal.user_id, "selected_by_name": principal.display_name}
        if expected_version == 0:
            session.add(Stance(portfolio_id=portfolio_id, **values))
        else:
            result = session.execute(update(Stance).where(Stance.portfolio_id == portfolio_id,
                Stance.selection_version == expected_version).values(**values))
            if result.rowcount != 1:
                raise HTTPException(409, "当前总体观点已被更新，请重新读取后再选择")
        record_access_event(session, portfolio_id, "investment_stance_selected",
            {"note_id": note_id, "revision_number": revision_number, "selection_version": expected_version + 1}, principal)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "当前总体观点已被更新，请重新读取后再选择") from error
        return _stance(session, portfolio_id, principal, access)
