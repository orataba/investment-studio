from __future__ import annotations

from hashlib import sha256

from pydantic import ValidationError
import pytest

from portfolio_app.api.contracts import (
    TransactionCaptureAccountResolution,
    TransactionCaptureCandidate,
)



@pytest.fixture(autouse=True)
def capture_identity_contract(monkeypatch):
    from studio_identity import Principal
    from portfolio_app.api.routes import transaction_captures
    from portfolio_app.services import transaction_capture_runner
    monkeypatch.setattr(transaction_captures, "issue_delegation", lambda principal, audience, resource_scope: "test-run:" + resource_scope["id"])
    monkeypatch.setattr(transaction_captures, "revoke_delegation", lambda token: None)
    monkeypatch.setattr(transaction_capture_runner, "revoke_delegation", lambda token: None)
    monkeypatch.setattr(transaction_capture_runner, "resolve_token", lambda token, audience: Principal("test-manager", "Test Manager", "default", resource_scope={"kind": "capture", "id": token.removeprefix("test-run:")}))

PNG_SCREENSHOT = b"\x89PNG\r\n\x1a\nportfolio-screenshot-fixture"
PNG_SCREENSHOT_OVERLAP = b"\x89PNG\r\n\x1a\nportfolio-overlap-fixture"


def _upload_capture(
    client,
    *,
    portfolio_id: str = "investment-studio",
    content: bytes = PNG_SCREENSHOT,
    filename: str = "broker-fill.png",
):
    response = client.post(
        f"/api/portfolios/{portfolio_id}/transaction-captures",
        files={"file": (filename, content, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_batch(
    client,
    capture_ids: list[str],
    purpose: str = "auto",
    *,
    portfolio_id: str = "investment-studio",
):
    response = client.post(
        f"/api/portfolios/{portfolio_id}/transaction-capture-batches",
        json={"capture_ids": capture_ids, "purpose": purpose},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _observed_field(capture_id: str, name: str, value: object):
    return {
        "name": name,
        "value": value,
        "status": "observed",
        "evidence": [
            {
                "capture_id": capture_id,
                "visible_text": f"{name}: {value}",
            }
        ],
    }


def _resolved_account(account_id: str, capture_id: str):
    return {
        "status": "resolved",
        "account_id": account_id,
        "candidate_account_ids": [account_id],
        "observed_account_hint": "visible broker account",
        "evidence": [{"capture_id": capture_id, "visible_text": "Account hint"}],
        "note": "Matched inside the fixed current portfolio context.",
    }


def test_screenshot_upload_is_content_addressed_evidence(client) -> None:
    capture = _upload_capture(client)
    assert capture["media_type"] == "image/png"
    assert capture["byte_size"] == len(PNG_SCREENSHOT)
    assert capture["content_sha256"] == sha256(PNG_SCREENSHOT).hexdigest()

    replay = _upload_capture(client)
    assert replay["capture_id"] == capture["capture_id"]

    listing = client.get("/api/portfolios/investment-studio/transaction-captures")
    assert listing.status_code == 200, listing.text
    assert [item["capture_id"] for item in listing.json()["captures"]] == [
        capture["capture_id"]
    ]

    detail = client.get(
        f"/api/portfolios/investment-studio/transaction-captures/{capture['capture_id']}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json() == capture

    image = client.get(
        f"/api/portfolios/investment-studio/transaction-captures/{capture['capture_id']}/image"
    )
    assert image.status_code == 200, image.text
    assert image.content == PNG_SCREENSHOT
    assert image.headers["content-type"] == "image/png"
    assert image.headers["etag"] == f'"{capture["content_sha256"]}"'


def test_screenshot_upload_rejects_non_raster_content(client) -> None:
    response = client.post(
        "/api/portfolios/investment-studio/transaction-captures",
        files={
            "file": (
                "unsafe.svg",
                b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
                "image/svg+xml",
            )
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Screenshot must be a PNG, JPEG, or WebP image."
    )


def test_deleting_portfolio_removes_its_screenshot_evidence(client) -> None:
    created = client.post(
        "/api/portfolios",
        json={
            "name": "Screenshot deletion fixture",
            "base_currency": "USD",
            "inception_date": "2026-01-02",
        },
    )
    assert created.status_code == 200, created.text
    portfolio_id = created.json()["portfolio_id"]
    capture = _upload_capture(client, portfolio_id=portfolio_id)
    _create_batch(client, [capture["capture_id"]], portfolio_id=portfolio_id)

    deleted = client.delete(f"/api/portfolios/{portfolio_id}")

    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"portfolio_id": portfolio_id, "deleted": True}


def test_multiple_screenshots_form_one_reusable_agent_batch(client) -> None:
    first = _upload_capture(client)
    second = _upload_capture(
        client,
        content=PNG_SCREENSHOT_OVERLAP,
        filename="broker-fill-overlap.png",
    )
    batch = _create_batch(client, [first["capture_id"], second["capture_id"]])
    assert batch["purpose"] == "auto"
    assert batch["status"] == "ready"
    assert batch["capture_count"] == 2
    assert batch["latest_analysis_revision"] == 0
    assert [capture["capture_id"] for capture in batch["captures"]] == [
        first["capture_id"],
        second["capture_id"],
    ]

    replay = _create_batch(client, [second["capture_id"], first["capture_id"]])
    assert replay["batch_id"] == batch["batch_id"]

    listing = client.get(
        "/api/portfolios/investment-studio/transaction-capture-batches"
    )
    assert listing.status_code == 200, listing.text
    assert [item["batch_id"] for item in listing.json()["batches"]] == [
        batch["batch_id"]
    ]

    context = client.get(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/agent-context",
        headers={"X-Test-Capture-Batch": batch["batch_id"]},
    )
    assert context.status_code == 200, context.text
    payload = context.json()
    assert payload["schema_version"] == "portfolio.transaction-capture-analysis.v2"
    assert payload["portfolio_scope_fixed"] is True
    assert payload["commit_tool_exposed"] is False
    assert payload["source_identity"] == {
        "source_system": "portfolio_screenshot_assistant",
        "required_external_reference_format": f"{batch['batch_id']}#{{record_index}}",
        "required_external_reference_example": f"{batch['batch_id']}#1",
    }
    assert payload["submission_schema"]["title"] == (
        "TransactionCaptureAnalysisCreateRequest"
    )
    assert any("complete screenshot batch" in item for item in payload["instructions"])
    assert any("fixed by the user's open workspace" in item for item in payload["instructions"])
    assert any("underlying_instrument_id" in item for item in payload["instructions"])
    assert any("physical option exercise or assignment" in item.lower() and "option_delivery" in item for item in payload["instructions"])
    account = next(
        item for item in payload["accounts"] if item["account_id"] == "broker-us-core"
    )
    assert account["account_category"] == "security"
    assert account["institution"] == "Interactive Brokers"
    assert account["default_settlement_cash_account_id"] == "cash-usd-main"
    assert isinstance(payload["derivative_contracts"], list)
    assert "known_instruments" not in payload


def test_ambiguous_capture_account_requires_multiple_candidates() -> None:
    with pytest.raises(
        ValidationError,
        match="Ambiguous account assignments require at least two candidates",
    ):
        TransactionCaptureAccountResolution.model_validate(
            {
                "status": "ambiguous",
                "candidate_account_ids": ["broker-us-core"],
            }
        )


def test_unresolved_capture_duplicate_cannot_map_to_preview_record() -> None:
    with pytest.raises(
        ValidationError,
        match="A possible duplicate must be resolved as distinct before Preview",
    ):
        TransactionCaptureCandidate.model_validate(
            {
                "candidate_id": "candidate-2",
                "candidate_kind": "transaction",
                "account_resolution": {
                    "status": "resolved",
                    "account_id": "broker-us-core",
                    "candidate_account_ids": ["broker-us-core"],
                },
                "fields": [
                    {
                        "name": "trade_date",
                        "value": "2026-08-24",
                        "status": "inferred",
                    }
                ],
                "proposed_transaction_record_index": 2,
                "possible_duplicate_of": ["candidate-1"],
                "duplicate_assessment": "uncertain",
            }
        )


def test_screenshot_analysis_run_is_queued_once(client, monkeypatch) -> None:
    from portfolio_app.api.routes import transaction_captures as capture_routes

    capture = _upload_capture(client)
    batch = _create_batch(client, [capture["capture_id"]])
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        capture_routes,
        "run_transaction_capture_analysis",
        lambda **kwargs: calls.append(kwargs),
    )

    response = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-runs"
    )
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["analysis_run_status"] == "queued"
    assert queued["analysis_run_attempt"] == 1
    assert calls == [
        {
            "portfolio_id": "investment-studio",
            "batch_id": batch["batch_id"],
            "attempt": 1,
            "run_token": "test-run:" + batch["batch_id"],
        }
    ]

    conflict = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-runs"
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == (
        "Screenshot analysis is already running for this batch."
    )


@pytest.mark.parametrize("interrupted_status", ["queued", "running"])
def test_interrupted_screenshot_analysis_can_be_retried(
    client,
    monkeypatch,
    interrupted_status: str,
) -> None:
    from portfolio_app.api.routes import transaction_captures as capture_routes
    from portfolio_app.db.models import TransactionCaptureBatchModel
    from portfolio_app.db.session import get_session_factory
    from portfolio_app.services.transaction_captures import (
        mark_transaction_capture_analysis_run_started,
        queue_transaction_capture_analysis_run,
    )

    capture = _upload_capture(client)
    batch = _create_batch(client, [capture["capture_id"]])
    queued = queue_transaction_capture_analysis_run(
        portfolio_id="investment-studio",
        batch_id=batch["batch_id"],
    )
    assert queued["analysis_run_attempt"] == 1
    if interrupted_status == "running":
        started, _revision = mark_transaction_capture_analysis_run_started(
            portfolio_id="investment-studio",
            batch_id=batch["batch_id"],
            attempt=1,
        )
        assert started is True

    session_factory = get_session_factory()
    with session_factory() as session:
        stored = session.get(TransactionCaptureBatchModel, batch["batch_id"])
        assert stored is not None
        assert stored.analysis_run_status == interrupted_status
        stored.updated_at = "2020-01-01T00:00:00Z"
        if interrupted_status == "running":
            stored.analysis_run_started_at = "2020-01-01T00:00:00Z"
        session.commit()

    detail = client.get(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["analysis_run_status"] == "failed"
    assert detail.json()["analysis_run_error"] == (
        "The previous analysis was interrupted. Retry the screenshot analysis."
    )

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        capture_routes,
        "run_transaction_capture_analysis",
        lambda **kwargs: calls.append(kwargs),
    )
    retried = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-runs"
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["analysis_run_status"] == "queued"
    assert retried.json()["analysis_run_attempt"] == 2
    assert calls == [
        {
            "portfolio_id": "investment-studio",
            "batch_id": batch["batch_id"],
            "attempt": 2,
            "run_token": "test-run:" + batch["batch_id"],
        }
    ]


def test_completed_revision_recovers_a_run_interrupted_before_status_update(client) -> None:
    from portfolio_app.db.models import TransactionCaptureBatchModel
    from portfolio_app.db.session import get_session_factory
    from portfolio_app.services.transaction_captures import (
        mark_transaction_capture_analysis_run_started,
        queue_transaction_capture_analysis_run,
    )

    capture = _upload_capture(client)
    batch = _create_batch(client, [capture["capture_id"]])
    queued = queue_transaction_capture_analysis_run(
        portfolio_id="investment-studio",
        batch_id=batch["batch_id"],
    )
    started, _revision = mark_transaction_capture_analysis_run_started(
        portfolio_id="investment-studio",
        batch_id=batch["batch_id"],
        attempt=queued["analysis_run_attempt"],
    )
    assert started is True

    revision = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "assistant",
            "harness": "deepseek-harness",
            "provider": "deepseek",
            "model_name": "vision-model",
            "finish_reason": "completed",
            "analysis": {
                "summary": "The screenshot contains no transaction proposal.",
                "documents": [
                    {
                        "capture_id": capture["capture_id"],
                        "document_kind": "unknown",
                    }
                ],
                "candidates": [],
                "questions": [],
            },
            "transaction_import": None,
        },
        headers={"X-Test-Capture-Batch": batch["batch_id"]},
    )
    assert revision.status_code == 200, revision.text
    assert revision.json()["batch"]["analysis_run_status"] == "succeeded"

    session_factory = get_session_factory()
    with session_factory() as session:
        stored = session.get(TransactionCaptureBatchModel, batch["batch_id"])
        assert stored is not None
        assert stored.analysis_run_status == "running"

    detail = client.get(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["analysis_run_status"] == "succeeded"
    assert detail.json()["analysis_run_error"] is None


def test_screenshot_analysis_runner_marks_missing_revision_as_failed(
    client,
    monkeypatch,
) -> None:
    from portfolio_app.services import transaction_capture_runner
    from portfolio_app.services.transaction_captures import (
        queue_transaction_capture_analysis_run,
    )

    capture = _upload_capture(client)
    batch = _create_batch(client, [capture["capture_id"]])
    queued = queue_transaction_capture_analysis_run(
        portfolio_id="investment-studio",
        batch_id=batch["batch_id"],
    )
    monkeypatch.setattr(
        transaction_capture_runner,
        "_run_harness_process",
        lambda **_kwargs: (0, False),
    )

    transaction_capture_runner.run_transaction_capture_analysis(
        portfolio_id="investment-studio",
        batch_id=batch["batch_id"],
        attempt=queued["analysis_run_attempt"],
        run_token="test-run:" + batch["batch_id"],
    )

    detail = client.get(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}"
    )
    assert detail.status_code == 200, detail.text
    result = detail.json()
    assert result["analysis_run_status"] == "failed"
    assert result["analysis_run_started_at"] is not None
    assert result["analysis_run_completed_at"] is not None
    assert result["analysis_run_error"] == (
        "The agent finished without producing a review revision."
    )


@pytest.mark.parametrize(
    ("source", "harness", "expected_status"),
    [
        ("assistant", "deepseek-harness", "succeeded"),
        ("human", None, "failed"),
    ],
)
def test_screenshot_analysis_runner_requires_an_agent_revision(
    client,
    monkeypatch,
    source: str,
    harness: str | None,
    expected_status: str,
) -> None:
    from portfolio_app.services import transaction_capture_runner
    from portfolio_app.services.transaction_captures import (
        create_transaction_capture_analysis_revision,
        queue_transaction_capture_analysis_run,
    )

    capture = _upload_capture(client)
    batch = _create_batch(client, [capture["capture_id"]])
    queued = queue_transaction_capture_analysis_run(
        portfolio_id="investment-studio",
        batch_id=batch["batch_id"],
    )

    def create_revision(**_kwargs) -> tuple[int, bool]:
        create_transaction_capture_analysis_revision(
            portfolio_id="investment-studio",
            batch_id=batch["batch_id"],
            source=source,
            harness=harness,
            provider="deepseek" if source == "assistant" else None,
            model="vision-model" if source == "assistant" else None,
            harness_session_id=None,
            finish_reason="test",
            schema_version="portfolio.transaction-capture-analysis.v2",
            analysis={
                "summary": "Test revision.",
                "documents": [
                    {
                        "capture_id": capture["capture_id"],
                        "document_kind": "unknown",
                    }
                ],
                "candidates": [],
                "questions": [],
            },
            transaction_import=None,
            preview=None,
            referenced_capture_ids={capture["capture_id"]},
        )
        return 0, False

    monkeypatch.setattr(
        transaction_capture_runner,
        "_run_harness_process",
        create_revision,
    )

    transaction_capture_runner.run_transaction_capture_analysis(
        portfolio_id="investment-studio",
        batch_id=batch["batch_id"],
        attempt=queued["analysis_run_attempt"],
        run_token="test-run:" + batch["batch_id"],
    )

    detail = client.get(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["analysis_run_status"] == expected_status


def test_agent_analysis_is_batch_scoped_preview_and_never_auto_commits(client) -> None:
    first = _upload_capture(client)
    second = _upload_capture(
        client,
        content=PNG_SCREENSHOT_OVERLAP,
        filename="broker-fill-overlap.png",
    )
    batch = _create_batch(client, [first["capture_id"], second["capture_id"]])
    before = client.get(
        "/api/portfolios/investment-studio/transactions"
    ).json()["summary"]["total_transactions"]
    transaction_import = {
        "source_system": "wrong-screenshot-source",
        "records": [
            {
                "external_reference": "not-the-batch-source-identity",
                "asset_type": "cash",
                "transaction_action": "deposit",
                "trade_date": "2026-05-01",
                "account_id": "cash-usd-main",
                "gross_amount": "1000.25",
                "currency": "USD",
            }
        ],
    }
    analysis = {
        "summary": "Two overlapping broker screenshots support one cash transaction candidate.",
        "documents": [
            {
                "capture_id": first["capture_id"],
                "document_kind": "trade_activity",
            },
            {
                "capture_id": second["capture_id"],
                "document_kind": "trade_activity",
            },
        ],
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "candidate_kind": "transaction",
                "account_resolution": _resolved_account(
                    "cash-usd-main",
                    first["capture_id"],
                ),
                "fields": [
                    _observed_field(first["capture_id"], "trade_date", "2026-05-01"),
                    _observed_field(second["capture_id"], "gross_amount", "1000.25"),
                ],
                "evidence": [
                    {"capture_id": first["capture_id"]},
                    {"capture_id": second["capture_id"]},
                ],
                "proposed_transaction_record_index": 1,
            }
        ],
        "questions": [],
    }
    response = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "assistant",
            "harness": "deepseek-harness",
            "provider": "deepseek",
            "model_name": "deepseek-v4-flash-vision-exp",
            "harness_session_id": "session-test",
            "finish_reason": "completed",
            "analysis": analysis,
            "transaction_import": transaction_import,
        },
        headers={"X-Test-Capture-Batch": batch["batch_id"]},
    )
    assert response.status_code == 422
    assert "must use source_system" in response.json()["detail"]

    transaction_import["source_system"] = "portfolio_screenshot_assistant"
    response = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "assistant",
            "harness": "deepseek-harness",
            "provider": "deepseek",
            "model_name": "deepseek-v4-flash-vision-exp",
            "analysis": analysis,
            "transaction_import": transaction_import,
        },
        headers={"X-Test-Capture-Batch": batch["batch_id"]},
    )
    assert response.status_code == 422
    assert "batch source identity" in response.json()["detail"]

    transaction_import["records"][0]["external_reference"] = f"{batch['batch_id']}#1"
    response = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "assistant",
            "harness": "deepseek-harness",
            "provider": "deepseek",
            "model_name": "deepseek-v4-flash-vision-exp",
            "harness_session_id": "session-test",
            "finish_reason": "completed",
            "analysis": analysis,
            "transaction_import": transaction_import,
        },
        headers={"X-Test-Capture-Batch": batch["batch_id"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["batch"]["status"] == "review_required"
    assert result["batch"]["latest_analysis_revision"] == 1
    assert result["analysis_revision"]["harness"] == "deepseek-harness"
    assert result["analysis_revision"]["harness_session_id"] == "session-test"
    assert result["preview"]["valid_count"] == 1
    assert result["preview"]["error_count"] == 0
    assert (
        client.get("/api/portfolios/investment-studio/transactions")
        .json()["summary"]["total_transactions"]
        == before
    )


def test_capture_batch_derives_recorded_state_from_persisted_source_identity(client) -> None:
    capture = _upload_capture(client)
    batch = _create_batch(client, [capture["capture_id"]], purpose="transaction_import")
    external_reference = f"{batch['batch_id']}#1"
    transaction_import = {
        "source_system": "portfolio_screenshot_assistant",
        "records": [
            {
                "external_reference": external_reference,
                "asset_type": "cash",
                "transaction_action": "deposit",
                "trade_date": "2026-05-01",
                "account_id": "cash-usd-main",
                "gross_amount": "1000.25",
                "currency": "USD",
            }
        ],
    }
    revision = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "human",
            "finish_reason": "human_review_confirmed",
            "analysis": {
                "summary": "Human review confirmed one USD cash deposit.",
                "documents": [
                    {
                        "capture_id": capture["capture_id"],
                        "document_kind": "trade_confirmation",
                    }
                ],
                "candidates": [
                    {
                        "candidate_id": "cash-deposit-1",
                        "candidate_kind": "transaction",
                        "account_resolution": _resolved_account(
                            "cash-usd-main",
                            capture["capture_id"],
                        ),
                        "fields": [
                            _observed_field(
                                capture["capture_id"],
                                "gross_amount",
                                "1000.25",
                            )
                        ],
                        "proposed_transaction_record_index": 1,
                    }
                ],
                "questions": [],
            },
            "transaction_import": transaction_import,
        },
    )
    assert revision.status_code == 200, revision.text
    revision_payload = revision.json()
    assert revision_payload["batch"]["ledger_status"] == "unrecorded"
    assert revision_payload["batch"]["recorded_transaction_ids"] == []
    assert revision_payload["preview"]["error_count"] == 0

    commit = client.post(
        "/api/portfolios/investment-studio/transaction-imports/commit",
        headers={"Idempotency-Key": "capture-ledger-state-1"},
        json={
            **transaction_import,
            "preview_digest": revision_payload["preview"]["preview_digest"],
        },
    )
    assert commit.status_code == 200, commit.text
    created_transaction_id = commit.json()["transactions"][0]["transaction_id"]

    detail = client.get(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["ledger_status"] == "recorded"
    assert detail.json()["recorded_transaction_ids"] == [created_transaction_id]

    listing = client.get(
        "/api/portfolios/investment-studio/transaction-capture-batches"
    )
    listed_batch = next(
        item
        for item in listing.json()["batches"]
        if item["batch_id"] == batch["batch_id"]
    )
    assert listed_batch["ledger_status"] == "recorded"
    assert listed_batch["recorded_transaction_ids"] == [created_transaction_id]


def test_position_snapshot_analysis_does_not_require_fake_transactions(client) -> None:
    capture = _upload_capture(client)
    batch = _create_batch(
        client,
        [capture["capture_id"]],
        purpose="portfolio_initialization",
    )
    response = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "assistant",
            "harness": "codex-app-server",
            "provider": "deepseek",
            "model_name": "deepseek-v4-flash-vision-exp",
            "analysis": {
                "summary": "The screenshot is a position snapshot, not transaction history.",
                "documents": [
                    {
                        "capture_id": capture["capture_id"],
                        "document_kind": "position_snapshot",
                    }
                ],
                "candidates": [
                    {
                        "candidate_id": "position-1",
                        "candidate_kind": "position_snapshot",
                        "account_resolution": _resolved_account(
                            "broker-us-core",
                            capture["capture_id"],
                        ),
                        "fields": [
                            _observed_field(capture["capture_id"], "quantity", 100)
                        ],
                    }
                ],
                "questions": ["Confirm the snapshot date before initialization."],
            },
        },
        headers={"X-Test-Capture-Batch": batch["batch_id"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["analysis_revision"]["transaction_import"] is None
    assert result["analysis_revision"]["preview_digest"] is None
    assert result["preview"] is None


def test_existing_transaction_match_is_preserved_without_a_duplicate_proposal(client) -> None:
    existing_transactions = client.get(
        "/api/portfolios/investment-studio/transactions"
    ).json()["transactions"]
    existing_transaction_id = existing_transactions[0]["transaction_id"]
    capture = _upload_capture(client)
    batch = _create_batch(client, [capture["capture_id"]])

    response = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "human",
            "analysis": {
                "summary": "The visible confirmation appears to match an existing ledger fact.",
                "documents": [
                    {
                        "capture_id": capture["capture_id"],
                        "document_kind": "trade_confirmation",
                    }
                ],
                "candidates": [
                    {
                        "candidate_id": "trade-existing-match",
                        "candidate_kind": "transaction",
                        "account_resolution": _resolved_account(
                            "broker-us-core",
                            capture["capture_id"],
                        ),
                        "fields": [
                            _observed_field(capture["capture_id"], "currency", "USD")
                        ],
                        "possible_existing_transaction_ids": [existing_transaction_id],
                        "duplicate_assessment": "same_record",
                    }
                ],
                "questions": [],
            },
        },
    )

    assert response.status_code == 200, response.text
    candidate = response.json()["analysis_revision"]["analysis"]["candidates"][0]
    assert candidate["possible_existing_transaction_ids"] == [existing_transaction_id]
    assert candidate["duplicate_assessment"] == "same_record"
    assert response.json()["preview"] is None


def test_ambiguous_account_assignment_stays_reviewable_without_a_proposal(client) -> None:
    capture = _upload_capture(client)
    batch = _create_batch(client, [capture["capture_id"]])
    response = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "assistant",
            "harness": "deepseek-harness",
            "provider": "deepseek",
            "model_name": "vision-shadow",
            "analysis": {
                "summary": "The screenshot supports a trade but not one unique USD account.",
                "documents": [
                    {
                        "capture_id": capture["capture_id"],
                        "document_kind": "trade_confirmation",
                    }
                ],
                "candidates": [
                    {
                        "candidate_id": "trade-ambiguous-account",
                        "candidate_kind": "transaction",
                        "account_resolution": {
                            "status": "ambiguous",
                            "candidate_account_ids": [
                                "broker-us-core",
                                "broker-us-income",
                            ],
                            "observed_account_hint": "USD brokerage account",
                            "evidence": [{"capture_id": capture["capture_id"]}],
                            "note": "Both current-portfolio accounts remain plausible.",
                        },
                        "fields": [
                            _observed_field(capture["capture_id"], "currency", "USD")
                        ],
                    }
                ],
                "questions": ["Which USD securities account should receive this trade?"],
            },
        },
        headers={"X-Test-Capture-Batch": batch["batch_id"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    resolution = result["analysis_revision"]["analysis"]["candidates"][0][
        "account_resolution"
    ]
    assert resolution["status"] == "ambiguous"
    assert result["preview"] is None


def test_analysis_rejects_accounts_outside_the_current_portfolio(client) -> None:
    capture = _upload_capture(client)
    batch = _create_batch(client, [capture["capture_id"]])
    response = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "human",
            "analysis": {
                "summary": "Invalid cross-portfolio account assignment.",
                "documents": [
                    {
                        "capture_id": capture["capture_id"],
                        "document_kind": "trade_confirmation",
                    }
                ],
                "candidates": [
                    {
                        "candidate_id": "trade-wrong-portfolio",
                        "candidate_kind": "transaction",
                        "account_resolution": {
                            "status": "resolved",
                            "account_id": "another-portfolio-account",
                            "candidate_account_ids": ["another-portfolio-account"],
                        },
                        "fields": [
                            _observed_field(capture["capture_id"], "currency", "USD")
                        ],
                    }
                ],
            },
        },
    )
    assert response.status_code == 422
    assert "current portfolio context" in response.json()["detail"]


def test_analysis_rejects_evidence_outside_the_batch(client) -> None:
    included = _upload_capture(client)
    outside = _upload_capture(
        client,
        content=PNG_SCREENSHOT_OVERLAP,
        filename="outside.png",
    )
    batch = _create_batch(client, [included["capture_id"]])
    response = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "human",
            "analysis": {
                "summary": "Invalid cross-batch evidence.",
                "documents": [
                    {
                        "capture_id": included["capture_id"],
                        "document_kind": "unknown",
                    }
                ],
                "candidates": [
                    {
                        "candidate_id": "unknown-1",
                        "candidate_kind": "unknown",
                        "fields": [
                            _observed_field(outside["capture_id"], "text", "unknown")
                        ],
                    }
                ],
            },
        },
    )
    assert response.status_code == 422
    assert "outside this batch" in response.json()["detail"]


def test_analysis_rejects_account_evidence_outside_the_batch(client) -> None:
    included = _upload_capture(client)
    outside = _upload_capture(
        client,
        content=PNG_SCREENSHOT_OVERLAP,
        filename="outside-account.png",
    )
    batch = _create_batch(client, [included["capture_id"]])
    account_resolution = _resolved_account(
        "broker-us-core",
        outside["capture_id"],
    )

    response = client.post(
        "/api/portfolios/investment-studio/transaction-capture-batches/"
        f"{batch['batch_id']}/analysis-revisions",
        json={
            "source": "human",
            "analysis": {
                "summary": "Invalid cross-batch account evidence.",
                "documents": [
                    {
                        "capture_id": included["capture_id"],
                        "document_kind": "trade_confirmation",
                    }
                ],
                "candidates": [
                    {
                        "candidate_id": "transaction-1",
                        "candidate_kind": "transaction",
                        "account_resolution": account_resolution,
                        "fields": [
                            _observed_field(
                                included["capture_id"],
                                "currency",
                                "USD",
                            )
                        ],
                    }
                ],
            },
        },
    )

    assert response.status_code == 422
    assert "outside this batch" in response.json()["detail"]
