from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from portfolio_app.api.router import api_router
from portfolio_app.core.settings import get_settings
from portfolio_app.services.daily_snapshot_worker import (
    start_daily_snapshot_recalculation_worker,
    stop_daily_snapshot_recalculation_worker,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.daily_snapshot_worker_enabled:
        start_daily_snapshot_recalculation_worker(
            poll_seconds=settings.daily_snapshot_worker_poll_seconds,
            reconciliation_batch_size=(
                settings.daily_snapshot_worker_reconciliation_batch_size
            ),
        )
    try:
        yield
    finally:
        if settings.daily_snapshot_worker_enabled:
            stop_daily_snapshot_recalculation_worker(
                timeout_seconds=settings.daily_snapshot_worker_shutdown_seconds,
            )


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Portfolio management backend.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")

if settings.research_enabled:
    from portfolio_app.api.routes import research

    app.include_router(research.router, prefix="/api/portfolios", tags=["research"])


@app.get("/api/capabilities")
def capabilities() -> dict[str, bool]:
    return {"research_enabled": settings.research_enabled}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/portfolios")
def portfolios_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/portfolios/{portfolio_id}")
def portfolio_page(portfolio_id: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.frontend_url}/portfolios/{portfolio_id}/holdings",
        status_code=307,
    )


@app.get("/portfolios/{portfolio_id}/{section_path:path}")
def portfolio_section_page(portfolio_id: str, section_path: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.frontend_url}/portfolios/{portfolio_id}/{section_path}",
        status_code=307,
    )


@app.get("/holdings")
def holdings_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/snapshot")
def snapshot_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/accounts")
def accounts_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/performance")
def performance_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/transactions")
def transactions_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/research")
def research_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/taxonomies")
def taxonomies_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/x-ray")
def xray_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/stock-intersection")
def stock_intersection_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)


@app.get("/risk")
def risk_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/portfolios", status_code=307)
