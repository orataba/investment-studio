from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from home_api.core.settings import Settings, get_settings
from home_api.services.auth import (
    issue_session,
    read_private_text,
    validate_session,
    verify_password,
)


router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


def _auth_material(settings: Settings) -> tuple[str, str, str]:
    username = (settings.auth_username or "").strip()
    if not username:
        raise ValueError("Authentication username is not configured.")
    password_hash = read_private_text(
        settings.auth_password_hash_file,
        "Authentication password hash file",
    )
    session_secret = read_private_text(
        settings.auth_session_secret_file,
        "Authentication session secret file",
    )
    if len(session_secret) < 32:
        raise ValueError("Authentication session secret must contain at least 32 characters.")
    return username, password_hash, session_secret


def _authenticated_username(
    settings: Settings,
    cookies: dict[str, str],
) -> str | None:
    username, _, session_secret = _auth_material(settings)
    token = cookies.get(settings.auth_cookie_name)
    if validate_session(token, session_secret, username):
        return username
    return None


@router.post("/login")
def login(payload: LoginRequest, response: Response) -> dict[str, object]:
    settings = get_settings()
    try:
        username, password_hash, session_secret = _auth_material(settings)
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        ) from error

    if not hmac.compare_digest(payload.username, username) or not verify_password(
        payload.password,
        password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    response.set_cookie(
        key=settings.auth_cookie_name,
        value=issue_session(username, session_secret, settings.auth_session_ttl_seconds),
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
        domain=settings.auth_cookie_domain,
    )
    return {"authenticated": True, "username": username}


@router.get("/session")
def session(
    request: Request,
) -> dict[str, object]:
    settings = get_settings()
    try:
        username = _authenticated_username(settings, request.cookies)
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        ) from error
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return {"authenticated": True, "username": username}


@router.get("/check", status_code=status.HTTP_204_NO_CONTENT)
def check(request: Request) -> Response:
    settings = get_settings()
    try:
        authenticated = _authenticated_username(settings, request.cookies)
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        ) from error
    if authenticated is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    settings = get_settings()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        domain=settings.auth_cookie_domain,
        secure=True,
        httponly=True,
        samesite="strict",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
