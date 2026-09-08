from datetime import date

from fastapi import APIRouter

from portfolio_app.api.concentration_contracts import ConcentrationSettingsUpdate
from portfolio_app.api.financial_read import FinancialReadRoute
from portfolio_app.services.concentration import read_portfolio_concentration
from portfolio_app.services.concentration_settings import read_concentration_settings, save_concentration_settings

router = APIRouter(route_class=FinancialReadRoute)
settings_router = APIRouter()


@settings_router.get("/{portfolio_id}/concentration/settings")
def concentration_settings(portfolio_id: str):
    return read_concentration_settings(portfolio_id)


@settings_router.put("/{portfolio_id}/concentration/settings")
def update_concentration_settings(portfolio_id: str, payload: ConcentrationSettingsUpdate):
    return save_concentration_settings(portfolio_id, payload)


@router.get("/{portfolio_id}/concentration")
def concentration(portfolio_id: str, as_of_date: date | None = None):
    return read_portfolio_concentration(portfolio_id, as_of_date=as_of_date)


router.include_router(settings_router)
