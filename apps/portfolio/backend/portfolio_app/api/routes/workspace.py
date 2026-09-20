from fastapi import APIRouter

from portfolio_app.api.financial_read import FinancialReadRoute
from portfolio_app.services.holdings_workspace import (
    holdings_workspace,
    position_holding_projection,
    workspace_summary,
)

router = APIRouter(route_class=FinancialReadRoute)
router.add_api_route("/summary", workspace_summary, methods=["GET"])
router.add_api_route("/holdings", holdings_workspace, methods=["GET"])
router.add_api_route("/holdings/position", position_holding_projection, methods=["GET"])
