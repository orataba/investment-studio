from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from home_api.api.router import api_router
from home_api.core.settings import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Investment Studio login and workspace discovery.",
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
    return RedirectResponse(url=settings.frontend_url, status_code=307)


@app.get("/watchlist")
def watchlist_page() -> RedirectResponse:
    return RedirectResponse(url=settings.watchlist_url, status_code=307)


@app.get("/portfolio")
def portfolio_page() -> RedirectResponse:
    return RedirectResponse(url=settings.portfolio_url, status_code=307)
