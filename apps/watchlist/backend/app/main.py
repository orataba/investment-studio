from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.router import api_router
from app.core.settings import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Yungu Watchlist backend with shared instruments, read models, and persistence scaffolding.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
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


@app.get("/watchlists/{watchlist_id}/instruments/{instrument_path:path}")
def watchlist_instrument_detail_page(
    watchlist_id: str,
    instrument_path: str,
) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.frontend_url}/watchlists/{watchlist_id}/instruments/{instrument_path}",
        status_code=307,
    )


@app.get("/instruments")
def instruments_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/instruments", status_code=307)


@app.get("/instruments/{instrument_path:path}")
def instrument_library_page(instrument_path: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.frontend_url}/instruments/{instrument_path}",
        status_code=307,
    )


@app.get("/funds")
def funds_index_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/instruments", status_code=307)


@app.get("/funds/{fund_path:path}")
def funds_page(fund_path: str) -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/instruments", status_code=307)


@app.get("/documents")
def documents_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/documents", status_code=307)


@app.get("/monitoring")
def monitoring_page() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.frontend_url}/monitoring", status_code=307)
