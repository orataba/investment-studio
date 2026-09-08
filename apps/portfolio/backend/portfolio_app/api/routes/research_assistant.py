"""Same-origin access to Watchlist-owned personal research conversations."""
from collections.abc import Iterable
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from starlette.responses import Response
from studio_identity import principal_headers

from portfolio_app.core.settings import get_settings


router = APIRouter()


def _forward(
    path: str,
    method: str = "GET",
    *,
    data: bytes | Iterable[bytes] | None = None,
    content_type: str = "application/json",
    content_length: int | None = None,
) -> Response:
    headers = {"Content-Type": content_type, **principal_headers()}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    request = Request(
        get_settings().watchlist_api_url.rstrip("/") + "/research" + path,
        method=method, headers=headers, data=data,
    )
    try:
        with urlopen(request, timeout=30) as upstream:
            body, status, response_headers = upstream.read(), upstream.status, upstream.headers
    except HTTPError as error:
        # Keep downstream validation, topic ownership and portfolio ACL errors.
        body, status, response_headers = error.read(), error.code, error.headers
    except (URLError, TimeoutError) as error:
        raise HTTPException(503, "暂时无法连接研究助手，请稍后重试") from error
    return Response(
        body, status_code=status,
        headers={key: response_headers[key] for key in ("Content-Type", "Content-Disposition") if key in response_headers},
    )


def _json(path: str, method: str, payload: dict) -> Response:
    return _forward(path, method, data=json.dumps(payload).encode())


@router.get("/catalogue")
def catalogue():
    return _forward("/catalogue")


@router.get("/connections")
def connections():
    return _forward("/connections")


@router.get("/topics")
def topics(instrument_id: str | None = None):
    query = "?" + urlencode({"instrument_id": instrument_id}) if instrument_id is not None else ""
    return _forward("/topics" + query)


@router.post("/topics", status_code=201)
def create_topic(payload: dict):
    return _json("/topics", "POST", payload)


@router.get("/topics/{topic_id}")
def topic(topic_id: str):
    return _forward("/topics/" + quote(topic_id, safe=""))


@router.put("/topics/{topic_id}")
def update_topic(topic_id: str, payload: dict):
    return _json("/topics/" + quote(topic_id, safe=""), "PUT", payload)


@router.post("/topics/{topic_id}/analysis", status_code=202)
def analyze(topic_id: str, payload: dict):
    return _json("/topics/" + quote(topic_id, safe="") + "/analysis", "POST", payload)


@router.post("/topics/{topic_id}/files")
def upload(topic_id: str, file: UploadFile = File(...)):
    # UploadFile is already spooled by Starlette. Iterate it so the proxy does
    # not create another full in-memory copy; Watchlist owns the upload limit.
    boundary = "studio-assistant-" + uuid4().hex
    filename = (file.filename or "document").replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")
    prefix = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    file.file.seek(0, 2)
    length = file.file.tell()
    file.file.seek(0)

    def chunks():
        yield prefix
        while chunk := file.file.read(64 * 1024):
            yield chunk
        yield suffix

    return _forward(
        "/topics/" + quote(topic_id, safe="") + "/files", "POST",
        data=chunks(), content_type=f"multipart/form-data; boundary={boundary}",
        content_length=len(prefix) + length + len(suffix),
    )


@router.get("/entries/{entry_id}/file")
def download(entry_id: str):
    return _forward("/entries/" + quote(entry_id, safe="") + "/file")
