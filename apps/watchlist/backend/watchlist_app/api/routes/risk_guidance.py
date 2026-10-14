from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from watchlist_app.db.session import get_db_session
from watchlist_app.services import risk_guidance as service
from watchlist_app.services.research_dossier import ResearchGuidanceConflict

router = APIRouter()


def _call(operation, *args):
    try:
        return operation(*args)
    except ResearchGuidanceConflict as error:
        raise HTTPException(409, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/risk/guidance")
def read_guidance(instrument_id: str | None = None, session: Session = Depends(get_db_session)):
    return _call(service.read_risk_guidance, session, instrument_id)


@router.put("/risk/guidance")
def save_guidance(request: service.RiskGuidanceInput, session: Session = Depends(get_db_session)):
    return _call(service.save_risk_guidance, session, request)


@router.get("/risk/guidance/preview")
def preview_guidance(instrument_id: str | None = None, watchlist_id: str | None = None,
                     portfolio_id: str | None = None, session: Session = Depends(get_db_session)):
    try:
        return service.preview_retained_cases(session, instrument_id=instrument_id,
            watchlist_id=watchlist_id, portfolio_id=portfolio_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
