from fastapi import Request
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from portfolio_app.services.daily_snapshots import (
    PortfolioCalculationPending,
    portfolio_financial_read_generation,
)


class FinancialReadRoute(APIRoute):
    """Publish financial GET responses only when their source generation is stable."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def financial_read(request: Request):
            portfolio_id = request.path_params.get("portfolio_id") or request.query_params.get("portfolio_id")
            if request.method != "GET" or not portfolio_id:
                return await handler(request)
            before = await run_in_threadpool(portfolio_financial_read_generation, str(portfolio_id))
            response = await handler(request)
            if response.status_code < 400 and before is not None:
                after = await run_in_threadpool(portfolio_financial_read_generation, str(portfolio_id))
                if before != after:
                    raise PortfolioCalculationPending(str(portfolio_id), status="changed")
            return response

        return financial_read
