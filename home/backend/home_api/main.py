from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from home_api.api.router import api_router
from home_api.api.routes.apps import list_apps
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

@app.middleware("http")
async def protect_auth_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/auth/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


app.include_router(api_router, prefix="/api")


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url=settings.frontend_url, status_code=307)


def workspace_redirect(app_id: str) -> RedirectResponse:
    card = next((card for card in list_apps().apps if card.app_id == app_id), None)
    if card is None:
        raise HTTPException(404, "此工作区未配置。")
    return RedirectResponse(url=card.url, status_code=307)


@app.get("/watchlist")
def watchlist_page() -> RedirectResponse:
    return workspace_redirect("watchlist")


@app.get("/portfolio")
def portfolio_page() -> RedirectResponse:
    return workspace_redirect("portfolio")
