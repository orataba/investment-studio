from contextlib import asynccontextmanager
from threading import Event

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from studio_identity import IdentityError, principal_context, resolve_request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from watchlist_app.api.router import api_router
from watchlist_app.core.settings import get_settings
from watchlist_app.services.recalc_worker import start_recalc_worker

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    from watchlist_app.services.research_runner import harness_available, interrupt_incomplete_runs
    interrupt_incomplete_runs()
    worker_thread = None
    worker_stop_event: Event | None = None
    if settings.recalc_worker_enabled:
        worker_thread, worker_stop_event = start_recalc_worker()
    sector_thread, sector_stop = None, None
    if harness_available():
        from watchlist_app.services.sector_research import start_sector_worker
        sector_thread, sector_stop = start_sector_worker()
    try:
        yield
    finally:
        if sector_stop is not None:
            sector_stop.set()
        if sector_thread is not None:
            sector_thread.join(timeout=settings.recalc_worker_shutdown_timeout_seconds)
        if worker_stop_event is not None:
            worker_stop_event.set()
        if worker_thread is not None:
            worker_thread.join(timeout=settings.recalc_worker_shutdown_timeout_seconds)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Investment Studio Watchlist backend with shared instruments, read models, and persistence scaffolding.",
    lifespan=lifespan,
)

@app.middleware("http")
async def studio_identity_boundary(request: Request, call_next):
    if request.url.path in {"/api/health", "/health"} or not request.url.path.startswith("/api/") or request.method == "OPTIONS":
        return await call_next(request)
    try:
        principal = await run_in_threadpool(resolve_request, request, audience="watchlist", allowed_origins=settings.cors_origins)
        with principal_context(principal):
            from watchlist_app.db.session import get_session_factory
            from watchlist_app.services.research_access import enforce_request
            def authorize():
                with get_session_factory()() as session:
                    enforce_request(request, session)
            await run_in_threadpool(authorize)
            response = await call_next(request)
            response.headers["Cache-Control"] = "private, no-store"
            return response
    except (IdentityError, HTTPException) as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)


@app.get("/api/identity")
def identity():
    from watchlist_app.services.research_identity import research_identity
    return research_identity()

# Keep CORS outside identity so trusted frontends can handle expired-session errors.
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
