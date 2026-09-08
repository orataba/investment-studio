from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from watchlist_app.db.session import get_db_session
from watchlist_app.services import research_dossier as service
from watchlist_app.services.read_models import serialize_payload

router = APIRouter()


class MaterialInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=60000)
    source: str = ""
    published_at: AwareDatetime | date | None = None
    effective_date: date | None = None

    @field_validator("title", "body")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入材料标题和正文")
        return value.strip()


def require_instrument(session, instrument_id):
    try:
        return service.require_instrument(session, instrument_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/research/instruments/{instrument_id}/dossier")
def dossier(instrument_id: str, include_history: bool = False, source_id: str | None = None,
            session: Session = Depends(get_db_session)):
    require_instrument(session, instrument_id)
    try:
        result = service.read_dossier(session, instrument_id, include_history=include_history)
        if source_id:
            from watchlist_app.services.research_notebook import dossier_source
            return dossier_source(result, source_id)
        return result
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.put("/research/instruments/{instrument_id}/dossier/mandate")
def save_mandate(instrument_id: str, request: service.ResearchMandateInput, session: Session = Depends(get_db_session)):
    require_instrument(session, instrument_id)
    try:
        return service.save_mandate(session, instrument_id, request)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/research/instruments/{instrument_id}/dossier/materials", status_code=201)
def add_material(instrument_id: str, request: MaterialInput, session: Session = Depends(get_db_session)):
    require_instrument(session, instrument_id)
    return service.add_material(session, instrument_id, **request.model_dump())


@router.post("/research/instruments/{instrument_id}/dossier/files", status_code=201)
async def upload_material(instrument_id: str, file: UploadFile = File(...), title: str | None = Form(None),
                         source: str | None = Form(None), published_at: AwareDatetime | date | None = Form(None),
                         effective_date: date | None = Form(None), session: Session = Depends(get_db_session)):
    from watchlist_app.api.routes.workbench import _save_uploaded_material
    require_instrument(session, instrument_id)
    topic = service.dossier_topic(session, instrument_id)
    # Keep source as the original-file download link; preserve the supplied attribution separately.
    entry = await _save_uploaded_material(topic, file, session, title=title,
        metadata=serialize_payload({"source": source, "published_at": published_at, "effective_date": effective_date}))
    return service.material_record(entry, instrument_id)


from watchlist_app.services.research_themes import ThemeInput, ThemePatch, save_theme, themes_view


@router.get("/research/instruments/{instrument_id}/themes")
def themes(instrument_id: str, session: Session = Depends(get_db_session)):
    require_instrument(session, instrument_id)
    return themes_view(session, instrument_id)


@router.post("/research/instruments/{instrument_id}/themes", status_code=201)
def create_theme(instrument_id: str, request: ThemeInput, session: Session = Depends(get_db_session)):
    require_instrument(session, instrument_id)
    result = save_theme(session, instrument_id, request)
    session.commit()
    return result


@router.patch("/research/instruments/{instrument_id}/themes/{theme_id}")
def update_theme(instrument_id: str, theme_id: str, request: ThemePatch, session: Session = Depends(get_db_session)):
    require_instrument(session, instrument_id)
    try:
        result = save_theme(session, instrument_id, request, theme_id=theme_id)
        session.commit()
        return result
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/research/members")
def members():
    from studio_identity import current_principal, team_directory
    return team_directory(current_principal())
