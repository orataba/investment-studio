from io import BytesIO
import json
from urllib.error import HTTPError, URLError

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.testclient import TestClient
import pytest
from studio_identity import Principal

from portfolio_app.api.authorization import authenticated_principal, portfolio_request_context
from portfolio_app.api.routes import research_assistant


class Upstream(BytesIO):
    def __init__(self, body=b"{}", *, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}


@pytest.fixture
def assistant_client():
    app = FastAPI()
    app.include_router(research_assistant.router, prefix="/api/research-assistant", dependencies=[Depends(portfolio_request_context)])
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        "viewer", "Portfolio Viewer", "default", team_role="reader", credential="viewer-session",
    )
    with TestClient(app) as client:
        yield client


def test_viewer_can_use_personal_conversation_with_original_identity_and_statuses(monkeypatch, assistant_client):
    calls = []

    def upstream(request, timeout):
        calls.append(request)
        status = 202 if request.full_url.endswith("/analysis") else 201 if request.method == "POST" else 200
        return Upstream(b'{"topic_id":"personal-topic"}', status=status)

    monkeypatch.setattr(research_assistant, "urlopen", upstream)
    for path in ("/catalogue", "/connections", "/topics", "/topics/personal-topic"):
        assert assistant_client.get("/api/research-assistant" + path).status_code == 200
        assert calls[-1].full_url.endswith("/api/research" + path)
        assert calls[-1].get_header("Authorization") == "Bearer viewer-session"
    assert assistant_client.get("/api/research-assistant/topics", params={"instrument_id": "HK/SMIC"}).status_code == 200
    assert calls[-1].full_url.endswith("/research/topics?instrument_id=HK%2FSMIC")

    creation = {"title": "My private conversation", "instrument_ids": [], "portfolio_id": "p"}
    assert assistant_client.post("/api/research-assistant/topics", json=creation).status_code == 201
    assert json.loads(calls[-1].data) == creation
    assert assistant_client.put("/api/research-assistant/topics/personal-topic", json=creation).status_code == 200
    question = {"question": "Review my holdings", "page_context": {"portfolio_id": "p", "tab": "holdings", "currency": "HKD"}}
    assert assistant_client.post("/api/research-assistant/topics/personal-topic/analysis", json=question).status_code == 202
    assert json.loads(calls[-1].data) == question
    assert calls[-1].get_header("Authorization") == "Bearer viewer-session"


def test_multipart_preserves_filename_and_bytes_and_download_stays_same_origin(monkeypatch, assistant_client):
    downstream = FastAPI()

    @downstream.post("/api/research/topics/personal-topic/files")
    def receive(file: UploadFile = File(...)):
        return {"filename": file.filename, "content": file.file.read().hex()}

    payload = b"\x00\xffAttachment contents\r\n"
    disposition = "attachment; filename*=utf-8''%E5%9B%9E%E5%8D%95.pdf"

    def upstream(request, timeout):
        assert request.get_header("Authorization") == "Bearer viewer-session"
        if request.method == "GET":
            assert request.full_url.endswith("/research/entries/file-entry/file")
            return Upstream(payload, headers={"Content-Type": "application/pdf", "Content-Disposition": disposition})
        assert not isinstance(request.data, bytes)
        body = b"".join(request.data)
        assert len(body) == int(request.get_header("Content-length"))
        with TestClient(downstream) as receiver:
            result = receiver.post("/api/research/topics/personal-topic/files", content=body, headers={"Content-Type": request.get_header("Content-type")})
        return Upstream(result.content, status=result.status_code)

    monkeypatch.setattr(research_assistant, "urlopen", upstream)
    upload = assistant_client.post("/api/research-assistant/topics/personal-topic/files", files={"file": ("回单.pdf", payload, "application/pdf")})
    assert upload.status_code == 200
    assert upload.json() == {"filename": "回单.pdf", "content": payload.hex()}
    download = assistant_client.get("/api/research-assistant/entries/file-entry/file")
    assert download.content == payload
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-disposition"] == disposition


@pytest.mark.parametrize("status,detail", [(403, "当前用户无权访问此对话"), (422, "所选组合当前不可访问")])
def test_downstream_ownership_and_validation_errors_are_preserved(monkeypatch, assistant_client, status, detail):
    def denied(request, timeout):
        raise HTTPError(request.full_url, status, "Rejected", {"Content-Type": "application/json"}, BytesIO(json.dumps({"detail": detail}).encode()))
    monkeypatch.setattr(research_assistant, "urlopen", denied)
    response = assistant_client.post("/api/research-assistant/topics", json={"title": "question", "portfolio_id": "other"})
    assert response.status_code == status
    assert response.json() == {"detail": detail}


def test_unavailable_service_is_reported_without_retry(monkeypatch, assistant_client):
    calls = []
    def unavailable(request, timeout):
        calls.append(request)
        raise URLError("offline")
    monkeypatch.setattr(research_assistant, "urlopen", unavailable)
    response = assistant_client.get("/api/research-assistant/connections")
    assert response.status_code == 503
    assert response.json()["detail"] == "暂时无法连接研究助手，请稍后重试"
    assert len(calls) == 1


@pytest.mark.parametrize("principal", [
    Principal(None, "Research Service", "default", kind="service", scopes=["watchlist:research"], credential="service-token"),
    Principal("viewer", "Delegated viewer", "default", resource_scope={"kind": "portfolio", "id": "p"}, credential="delegation"),
])
def test_service_and_bound_model_credentials_cannot_access_personal_conversations(monkeypatch, assistant_client, principal):
    assistant_client.app.dependency_overrides[authenticated_principal] = lambda: principal
    def forbidden_upstream(*args, **kwargs):
        pytest.fail("Personal conversation must reject service/delegation before forwarding")
    monkeypatch.setattr(research_assistant, "urlopen", forbidden_upstream)
    assert assistant_client.get("/api/research-assistant/topics").status_code == 403
    assert assistant_client.post("/api/research-assistant/topics", json={"title": "question"}).status_code == 403


def test_anonymous_request_is_rejected_and_bridge_does_not_expose_research_tools(assistant_client):
    def anonymous():
        raise HTTPException(401, "请先登录 Investment Studio")
    assistant_client.app.dependency_overrides[authenticated_principal] = anonymous
    assert assistant_client.get("/api/research-assistant/topics").status_code == 401
    assert assistant_client.post("/api/research-assistant/runs/task/tools", json={}).status_code == 404
