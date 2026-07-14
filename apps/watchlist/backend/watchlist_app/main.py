from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from watchlist_app.api.router import api_router
from watchlist_app.core.settings import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Portfolio Operations Watchlist backend with shared instruments, read models, and persistence scaffolding.",
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


@app.get("/documents")
def documents_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/documents", status_code=307)


@app.get("/monitoring")
def monitoring_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/monitoring", status_code=307)
