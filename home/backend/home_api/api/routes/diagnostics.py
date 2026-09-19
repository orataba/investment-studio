"""Authenticated, bounded browser timings; no messages, bodies or credentials."""
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from studio_runtime import emit

from home_api.api.routes.auth import Db, check_origin, current

router = APIRouter()


class BrowserEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["navigation", "request", "error"]
    path: str = Field(max_length=250, pattern=r"^/[^?#\r\n]*$")
    duration_ms: float | None = Field(default=None, ge=0, le=86_400_000)
    ttfb_ms: float | None = Field(default=None, ge=0, le=86_400_000)
    transfer_bytes: int | None = Field(default=None, ge=0)
    status: int | None = Field(default=None, ge=0, le=599)
    request_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    error_type: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.]+$")
    line: int | None = Field(default=None, ge=0)


class BrowserEvents(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: Literal["home", "portfolio", "watchlist", "briefing"]
    events: list[BrowserEvent] = Field(min_length=1, max_length=20)


@router.post("/events", status_code=204)
def browser_events(payload: BrowserEvents, request: Request, db: Db):
    check_origin(request)
    principal = current(db, request)
    for event in payload.events:
        data = event.model_dump(exclude_none=True)
        # The client ID identifies the originating API request, not this upload.
        client_request_id = data.pop("request_id", None)
        emit("browser_event", app=payload.app, user_id=principal["user_id"],
             client_request_id=client_request_id, **data)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
