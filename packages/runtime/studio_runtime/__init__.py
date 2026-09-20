"""Small, data-free diagnostics: no credentials, request bodies or SQL values."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import re
import time
import traceback
from uuid import uuid4


@dataclass
class Timing:
    request_id: str
    sql_count: int = 0
    sql_ms: float = 0
    spans: dict[str, float] = field(default_factory=dict)


_timing: ContextVar[Timing | None] = ContextVar("studio_timing", default=None)
_logger = logging.getLogger("studio.diagnostics")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.propagate = False


def emit(event: str, **fields) -> None:
    timing = _timing.get()
    _logger.info(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                             "event": event, "request_id": timing.request_id if timing else None,
                             **fields}, ensure_ascii=False, separators=(",", ":")))


def _error_fields(error: BaseException) -> dict:
    # Exception messages (e.g. SQL bind parameters) can contain private inputs.
    return {"error_type": type(error).__name__, "frames": [
        {"file": frame.filename.rsplit("/", 1)[-1], "line": frame.lineno, "function": frame.name}
        for frame in traceback.extract_tb(error.__traceback__)[-12:]
    ]}


@contextmanager
def operation(name: str, **identifiers):
    """Time a named boundary. Callers supply IDs/status only, never payloads."""
    started = time.perf_counter()
    token = _timing.set(Timing(uuid4().hex)) if _timing.get() is None else None
    timing = _timing.get()
    initial_count, initial_ms = timing.sql_count, timing.sql_ms
    error_fields = {}
    emit("operation_started", operation=name, **identifiers)
    try:
        yield
    except BaseException as error:
        error_fields = _error_fields(error)
        raise
    finally:
        duration = round((time.perf_counter() - started) * 1000, 2)
        timing = _timing.get()
        if timing:
            timing.spans[name] = timing.spans.get(name, 0) + duration
        emit("operation_finished", operation=name, duration_ms=duration,
             outcome="error" if error_fields else "ok",
             sql_count=timing.sql_count - initial_count,
             sql_ms=round(timing.sql_ms - initial_ms, 2), **identifiers, **error_fields)
        if token is not None:
            _timing.reset(token)


def _observe_sql() -> None:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    if event.contains(Engine, "before_cursor_execute", _sql_start):
        return
    event.listen(Engine, "before_cursor_execute", _sql_start)
    event.listen(Engine, "after_cursor_execute", _sql_end)
    event.listen(Engine, "handle_error", _sql_error)


def _sql_start(_conn, _cursor, _statement, _parameters, context, _many):
    context._studio_started = time.perf_counter() if _timing.get() else None


def _sql_end(_conn, _cursor, _statement, _parameters, context, _many):
    timing = _timing.get()
    started = getattr(context, "_studio_started", None)
    if timing and started is not None:
        timing.sql_count += 1
        timing.sql_ms += (time.perf_counter() - started) * 1000
        context._studio_started = None


def _sql_error(context):
    if context.execution_context is not None:
        _sql_end(None, None, None, None, context.execution_context, False)


class RequestDiagnostics:
    """ASGI timings cover auth, handlers, streaming and failed requests."""
    def __init__(self, app, service: str):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        supplied_id = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
        request_id = supplied_id if re.fullmatch(r"[a-zA-Z0-9_-]{8,64}", supplied_id) else uuid4().hex
        timing = Timing(request_id)
        token = _timing.set(timing)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status, response_bytes, error_fields = 500, 0, {}

        async def measured_send(message):
            nonlocal status, response_bytes
            if message["type"] == "http.response.start":
                status = message["status"]
                duration = (time.perf_counter() - started) * 1000
                response_headers = list(message.get("headers", []))
                response_headers += [(b"x-request-id", request_id.encode()),
                                     (b"server-timing", f'app;dur={duration:.2f}, db;dur={timing.sql_ms:.2f}'.encode())]
                message = {**message, "headers": response_headers}
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, measured_send)
        except BaseException as error:
            error_fields = _error_fields(error)
            if isinstance(error, Exception):
                # ASGI servers log exceptions even after sending our generic 500.
                # Suppress the original message/chain there as well: SQL and
                # provider exceptions often embed bind values or credentials.
                raise RuntimeError(f"Request failed; diagnostic request_id={request_id}") from None
            raise
        finally:
            route = scope.get("route")
            emit("http_request", service=self.service, method=scope["method"],
                 route=getattr(route, "path", "unmatched"), status=status,
                 duration_ms=round((time.perf_counter() - started) * 1000, 2),
                 response_bytes=response_bytes, sql_count=timing.sql_count,
                 sql_ms=round(timing.sql_ms, 2), spans=timing.spans, **error_fields)
            _timing.reset(token)


def install_diagnostics(app, service: str) -> None:
    # Uvicorn's default access formatter includes raw query strings; our
    # route-template events replace it for every configured Studio process.
    logging.getLogger("uvicorn.access").disabled = True
    app.add_middleware(RequestDiagnostics, service=service)

    async def unexpected_error(request, _error):
        from starlette.responses import JSONResponse
        request_id = getattr(request.state, "request_id", uuid4().hex)
        return JSONResponse(status_code=500,
                            content={"detail": "服务暂时无法完成请求，请稍后重试。", "request_id": request_id},
                            headers={"X-Request-ID": request_id, "Cache-Control": "no-store"})

    app.add_exception_handler(Exception, unexpected_error)


# Operations also run in release and maintenance CLIs, without an ASGI app.
# Module initialization installs the observers once before callers can start
# concurrent operations; queries outside a timing context remain unmeasured.
_observe_sql()
