"""Apply the user's explicit recordkeeping instructions, separate from AI research publication."""
from copy import deepcopy
from datetime import UTC, datetime
import re
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from watchlist_app.api.contracts import InstrumentResearchNoteInput
from watchlist_app.repositories.sqlalchemy.research import SQLAlchemyInstrumentResearchRepository
from watchlist_app.services.research_identity import run_identity
from watchlist_app.services.research_themes import ThemeInput, ThemePatch, save_theme


class UserCommandBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str
    source_quote: str = Field(min_length=1)


class ManageThemeCommand(UserCommandBase):
    action: Literal["manage_theme"]
    theme_id: str | None = None
    theme: ThemeInput | ThemePatch


class RecordViewCommand(UserCommandBase):
    action: Literal["record_view"]
    note: InstrumentResearchNoteInput


class PublishResearchCommand(UserCommandBase):
    action: Literal["publish_research"]


UserCommand = Annotated[ManageThemeCommand | RecordViewCommand | PublishResearchCommand, Field(discriminator="action")]


def _require_instruction(run, command):
    context = run.context_json or {}
    if (run.kind != "analysis" or not context.get("research_run")
            or context.get("sector_run") or context.get("risk_run")):
        raise ValueError("只有研究助手对话可以执行投资经理的记录指令")
    if run.status not in {"queued", "running"}:
        raise ValueError("本轮对话已经结束")
    scope = set(context.get("instrument_ids", [])) | {row["instrument_id"] for row in context.get("catalogue", [])}
    if command.instrument_id not in scope:
        raise ValueError("记录标的不在本轮研究目录")
    quote = command.source_quote.strip()
    if not quote or quote not in run.title:
        raise ValueError("请引用当前用户消息中的真实记录指令，不能引用资料或助手回答")
    verbs = r"保存|记录|记下|记为|记到|留存|存下|存到|写入|写到|save|record"
    if command.action == "manage_theme":
        verbs += r"|建立|创建|新增|添加|设立|新建|暂停|结束|关闭|恢复|调整|修改|更新|持续关注|持续跟踪|create|pause|close|resume|update"
    # Check the real message prefix too: citing just '保存' from '不要保存' cannot
    # remove the user's negation. Semantic interpretation of examples and whose
    # view it is remains part of the researcher's explicit tool instructions.
    quote_start = run.title.index(quote)
    negative = r"(?:不要|不用|不必|别|暂不|先不|无需|不需要|不想|不允许|禁止)|\b(?:do not|don't|never|not to)\b"
    instructions = []
    for match in re.finditer(verbs, quote, re.IGNORECASE):
        prefix = run.title[:quote_start + match.start()]
        clause = re.split(r"[。！？.!?\n，,；;]", prefix)[-1]
        if not re.search(negative, clause, re.IGNORECASE) and not re.search(r"如何|怎么|是否需要|要不要", clause):
            instructions.append(match)
    if not instructions:
        raise ValueError("当前消息没有明确的保存或主题管理指令；讨论和假设不能自动记为投资经理观点")
    return quote


def apply_user_command(session, run, command):
    quote = _require_instruction(run, command)
    request = command.model_dump(mode="json")
    receipts = list((run.context_json or {}).get("user_records", []))
    prior = next((row for row in receipts if row.get("request") == request), None)
    if prior is not None:
        return {key: value for key, value in prior.items() if key != "request"}

    from watchlist_app.services.research_access import require_team_write, require_entry_access
    require_entry_access(session, run, tool_write=True)
    require_team_write()
    from watchlist_app.services.research_access import require_team_publication_scope
    require_team_publication_scope(session, run)
    actor = run_identity(run.context_json)
    provenance = {"recorded_via": "assistant", "source_run_id": run.entry_id, "source_quote": quote}
    if isinstance(command, ManageThemeCommand):
        payload = (ThemePatch if command.theme_id else ThemeInput).model_validate(
            command.theme.model_dump(exclude_unset=True))
        saved = save_theme(session, command.instrument_id, payload, theme_id=command.theme_id,
                           actor=actor, provenance=provenance)
        receipt = {"kind": "theme", "id": saved["theme_id"], "title": saved["title"],
                   "theme_status": saved["status"], "revision_number": saved["revision_number"]}
    elif isinstance(command, PublishResearchCommand):
        run.context_json = {**run.context_json, "team_publication_instructions": {
            **run.context_json.get("team_publication_instructions", {}), command.instrument_id: quote}}
        receipt = {"kind": "research_publication", "id": run.entry_id, "title": "本轮团队研究更新已授权"}
    else:
        from watchlist_app.services.research_views import prepare_note_values
        from watchlist_app.services.risk_workbench import refresh_risk_cases
        bound = next((row for row in run.context_json.get("research_dossiers", [])
                      if row["instrument_id"] == command.instrument_id), {})
        theme_id = command.note.research_context.theme_id if command.note.research_context else None
        theme = next((row for row in bound.get("themes", []) if row["theme_id"] == theme_id), {})
        notebook = bound.get("notebook") or {}
        refs = command.note.research_context.source_ids if command.note.research_context else []
        bound_sources = []
        if refs:
            from watchlist_app.services.research_notebook import research_sources, validate_notebook, ResearchNotebook
            from watchlist_app.services.market_evidence import source_reference
            available = research_sources(run.context_json, run.entry_id)
            validate_notebook(ResearchNotebook(source_ids=refs), command.instrument_id,
                              available)
            bound_sources = [deepcopy(source_reference(available[sid])) for sid in dict.fromkeys(refs)]
        note_provenance = {**provenance, "sources": bound_sources, "information_cutoff": run.context_json.get("cutoff"), "research_snapshot": {
            "notebook_version_id": notebook.get("version_id", notebook.get("run_id")),
            "mandate_version_id": (bound.get("mandate") or {}).get("version_id"),
            "theme_revision": theme.get("revision_number"),
        }}
        values = prepare_note_values(session, command.instrument_id, command.note, actor=actor, provenance=note_provenance)
        saved = SQLAlchemyInstrumentResearchRepository().create_note(session, instrument_id=command.instrument_id,
            note_id=uuid4().hex, values=values, updated_by=actor["user_id"], author_user_id=actor["user_id"], team_id=actor["team_id"])
        refresh_risk_cases(session, [command.instrument_id])
        receipt = {"kind": "investment_view", "id": saved.note_id, "title": saved.title,
                   "revision_number": saved.revision_number}
    receipt.update(status="saved", instrument_id=command.instrument_id, author_user_id=actor["user_id"],
                   author=actor["display_name"], recorded_at=datetime.now(UTC).isoformat(), **provenance)
    context = {**run.context_json, "user_records": [*receipts, {**receipt, "request": request}]}
    if any(row["instrument_id"] == command.instrument_id for row in context.get("research_dossiers", [])):
        from watchlist_app.services.research_dossier import read_dossier
        current = read_dossier(session, command.instrument_id, actor=actor)
        # The new user records are available in the same conversation; original
        # research/evidence snapshots and their clocks stay as first read.
        context["research_dossiers"] = [{**row, "themes": current["themes"], "pm_views": current["pm_views"]}
            if row["instrument_id"] == command.instrument_id else row for row in context["research_dossiers"]]
    run.context_json = context
    session.flush()
    return receipt
