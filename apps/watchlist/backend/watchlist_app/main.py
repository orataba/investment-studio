from contextlib import asynccontextmanager
from threading import Event

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from watchlist_app.api.router import api_router
from watchlist_app.core.settings import get_settings
from watchlist_app.services.recalc_worker import start_recalc_worker

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    worker_thread = None
    worker_stop_event: Event | None = None
    if settings.recalc_worker_enabled:
        worker_thread, worker_stop_event = start_recalc_worker()
    try:
        yield
    finally:
        if worker_stop_event is not None:
            worker_stop_event.set()
        if worker_thread is not None:
            worker_thread.join(timeout=settings.recalc_worker_shutdown_timeout_seconds)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Portfolio Operations Watchlist backend with shared instruments, read models, and persistence scaffolding.",
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


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/watchlists", status_code=307)


@app.get("/watchlists")
def watchlists_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/watchlists", status_code=307)


@app.get("/watchlists/{watchlist_id}")
def watchlist_detail_page(watchlist_id: str) -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/watchlists/{watchlist_id}", status_code=307)


@app.get("/instruments/{instrument_path:path}")
def instrument_detail_page(instrument_path: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.frontend_url}/instruments/{instrument_path}",
        status_code=307,
    )


@app.get("/monitoring")
def monitoring_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/monitoring", status_code=307)
