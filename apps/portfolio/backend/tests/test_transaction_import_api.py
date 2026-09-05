from __future__ import annotations

import json
from pathlib import Path
import re
from typing import get_args

from portfolio_app.api.contracts import (
    TransactionImportAction,
    TransactionImportPreviewRequest,
)
from portfolio_app.services.transaction_import import TRANSACTION_ACTIONS


def _create_derivative_account(client, *, category: str) -> str:
    response = client.post(
        "/api/portfolios/investment-studio/accounts",
        json={
            "account_name": f"JSON {category.upper()} Account",
            "account_category": category,
            "currency": "USD",
            "institution": "Test Broker",
            "default_settlement_cash_account_id": "cash-usd-main",
            "cost_basis_method": "fifo",
            "opened_at": "2026-01-02",
            "status": "active",
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["account_id"])


def _cash_deposit_payload(*, reference: str = "SCREENSHOT-001#1") -> dict[str, object]:
    return {
        "source_system": "trade_screenshot_parser",
        "records": [
            {
                "external_reference": reference,
                "asset_type": "cash",
                "transaction_action": "deposit",
                "trade_date": "2026-05-01",
                "account_id": "cash-usd-main",
                "gross_amount": "1000.25",
                "currency": "USD",
            }
        ],
    }


def test_json_import_preview_commit_and_idempotent_replay(client) -> None:
    payload = _cash_deposit_payload()
    preview_response = client.post(
        "/api/portfolios/investment-studio/transaction-imports/preview",
        json=payload,
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["row_count"] == 1
    assert preview["valid_count"] == 1
    assert preview["error_count"] == 0
    assert preview["warnings"] == []
    assert preview["rows"][0]["external_reference"] == "SCREENSHOT-001#1"
    assert preview["rows"][0]["transaction"]["transaction_type"] == "deposit"
    assert (
        preview["rows"][0]["transaction"]["source_system"]
        == "trade_screenshot_parser"
    )

    commit_payload = {**payload, "preview_digest": preview["preview_digest"]}
    first_commit = client.post(
        "/api/portfolios/investment-studio/transaction-imports/commit",
        headers={"Idempotency-Key": "screenshot-batch-001"},
        json=commit_payload,
    )
    assert first_commit.status_code == 200, first_commit.text
    created = first_commit.json()
    assert created["created_count"] == 1
    assert created["transactions"][0]["source_system"] == "trade_screenshot_parser"
    assert created["transactions"][0]["external_reference"] == "SCREENSHOT-001#1"

    replay = client.post(
        "/api/portfolios/investment-studio/transaction-imports/commit",
        headers={"Idempotency-Key": "screenshot-batch-001"},
        json=commit_payload,
    )
    assert replay.status_code == 200, replay.text
    assert (
        replay.json()["transactions"][0]["transaction_id"]
        == created["transactions"][0]["transaction_id"]
    )


def test_json_import_requires_fresh_preview_and_unique_source_identity(client) -> None:
    payload = _cash_deposit_payload(reference="SCREENSHOT-002#1")
    preview = client.post(
        "/api/portfolios/investment-studio/transaction-imports/preview",
        json=payload,
    ).json()

    changed_payload = _cash_deposit_payload(reference="SCREENSHOT-002#1")
    changed_payload["records"][0]["gross_amount"] = "999.00"
    stale_commit = client.post(
        "/api/portfolios/investment-studio/transaction-imports/commit",
        headers={"Idempotency-Key": "screenshot-batch-002"},
        json={**changed_payload, "preview_digest": preview["preview_digest"]},
    )
    assert stale_commit.status_code == 409
    assert "changed after preview" in stale_commit.json()["detail"]

    duplicate_payload = {
        "source_system": "trade_screenshot_parser",
        "records": [payload["records"][0], payload["records"][0]],
    }
    duplicate_preview = client.post(
        "/api/portfolios/investment-studio/transaction-imports/preview",
        json=duplicate_payload,
    )
    assert duplicate_preview.status_code == 200
    assert duplicate_preview.json()["error_count"] == 1
    assert duplicate_preview.json()["batch_errors"] == [
        "Import batch repeats source_system/external_reference identities: "
        "trade_screenshot_parser/SCREENSHOT-002#1."
    ]


def test_json_import_rejects_invalid_asset_action_pair_in_preview(client) -> None:
    response = client.post(
        "/api/portfolios/investment-studio/transaction-imports/preview",
        json={
            "source_system": "trade_screenshot_parser",
            "records": [
                {
                    "external_reference": "SCREENSHOT-003#1",
                    "asset_type": "security",
                    "transaction_action": "deposit",
                    "trade_date": "2026-05-01",
                    "account_id": "broker-us-core",
                    "instrument_id": "equity-us-abbv",
                    "gross_amount": "1000",
                    "currency": "USD",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["valid_count"] == 0
    assert preview["error_count"] == 1
    assert "not supported for asset_type 'security'" in preview["rows"][0]["errors"][0]


def test_json_import_accepts_mixed_security_option_fcn_and_cash_batch(client) -> None:
    option_account_id = _create_derivative_account(client, category="option")
    fcn_account_id = _create_derivative_account(client, category="fcn")
    payload = {
        "source_system": "trade_screenshot_parser",
        "records": [
            {
                "external_reference": "SCREENSHOT-MIXED#cash",
                "asset_type": "cash",
                "transaction_action": "deposit",
                "trade_date": "2026-05-01",
                "account_id": "cash-usd-main",
                "gross_amount": "200000",
                "currency": "USD",
            },
            {
                "external_reference": "SCREENSHOT-MIXED#security",
                "asset_type": "security",
                "transaction_action": "buy",
                "trade_date": "2026-05-01",
                "settlement_date": "2026-05-03",
                "account_id": "broker-us-core",
                "settlement_cash_account_id": "cash-usd-main",
                "instrument_id": "equity-us-abbv",
                "quantity": "2",
                "price": "200",
                "gross_amount": "400",
                "fees": "1",
                "fee_category": "transaction_cost",
                "currency": "USD",
            },
            {
                "external_reference": "SCREENSHOT-MIXED#option",
                "asset_type": "option",
                "transaction_action": "sell_to_open",
                "trade_date": "2026-05-01",
                "settlement_date": "2026-05-01",
                "account_id": option_account_id,
                "settlement_cash_account_id": "cash-usd-main",
                "derivative_contract_id": "option-json-call-001",
                "derivative_contract": {
                    "derivative_contract_id": "option-json-call-001",
                    "contract_name": "ABBV Dec 220 Call",
                    "contract_type": "option",
                    "external_reference": "BROKER-OPTION-001",
                    "terms": {
                        "underlying_instrument_id": "equity-us-abbv",
                        "option_type": "call",
                        "expiry_date": "2026-12-18",
                        "strike": "220",
                        "contract_multiplier": "100",
                    },
                },
                "quantity": "1",
                "price": "5",
                "gross_amount": "500",
                "currency": "USD",
            },
            {
                "external_reference": "SCREENSHOT-MIXED#fcn",
                "asset_type": "fcn",
                "transaction_action": "entry",
                "trade_date": "2026-05-01",
                "settlement_date": "2026-05-01",
                "account_id": fcn_account_id,
                "settlement_cash_account_id": "cash-usd-main",
                "derivative_contract_id": "fcn-json-001",
                "derivative_contract": {
                    "derivative_contract_id": "fcn-json-001",
                    "contract_name": "ABBV FCN",
                    "contract_type": "fcn",
                    "external_reference": "BROKER-FCN-001",
                    "terms": {
                        "notional": "100000",
                        "annual_coupon_rate_pct": "12",
                        "issue_date": "2026-05-01",
                        "final_observation_date": "2026-08-28",
                        "maturity_date": "2026-09-01",
                        "issuer": "Demo Bank",
                        "counterparty": "Demo Broker",
                        "underlyings": [
                            {
                                "instrument_id": "equity-us-abbv",
                                "initial_reference_price": "200",
                                "strike_level_pct": "100",
                                "knock_in_level_pct": "70",
                                "deliverable": True,
                            }
                        ],
                    },
                },
                "quantity": "1",
                "price": "100000",
                "gross_amount": "100000",
                "currency": "USD",
            },
        ],
    }

    preview_response = client.post(
        "/api/portfolios/investment-studio/transaction-imports/preview",
        json=payload,
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["row_count"] == 4
    assert preview["valid_count"] == 4, preview
    assert preview["error_count"] == 0, preview

    commit = client.post(
        "/api/portfolios/investment-studio/transaction-imports/commit",
        headers={"Idempotency-Key": "screenshot-mixed-batch"},
        json={**payload, "preview_digest": preview["preview_digest"]},
    )
    assert commit.status_code == 200, commit.text
    created = commit.json()["transactions"]
    assert commit.json()["created_count"] == 4
    assert {row["asset_subtype"] for row in created} >= {"fcn", "option"}
    assert {row["transaction_type"] for row in created} >= {
        "buy",
        "deposit",
        "option_write",
    }


def test_json_import_cash_transfer_creates_one_atomic_pair(client) -> None:
    payload = {
        "source_system": "trade_screenshot_parser",
        "records": [
            {
                "external_reference": "SCREENSHOT-TRANSFER#1",
                "asset_type": "cash",
                "transaction_action": "transfer_out",
                "trade_date": "2026-05-01",
                "account_id": "cash-usd-main",
                "counterparty_account_id": "cash-usd-reserve",
                "gross_amount": "250",
                "currency": "USD",
            }
        ],
    }
    preview = client.post(
        "/api/portfolios/investment-studio/transaction-imports/preview",
        json=payload,
    ).json()
    assert preview["error_count"] == 0, preview

    commit = client.post(
        "/api/portfolios/investment-studio/transaction-imports/commit",
        headers={"Idempotency-Key": "screenshot-transfer-batch"},
        json={**payload, "preview_digest": preview["preview_digest"]},
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["created_count"] == 2
    by_type = {
        row["transaction_type"]: row for row in commit.json()["transactions"]
    }
    assert by_type["transfer_out"]["external_reference"] == "SCREENSHOT-TRANSFER#1"
    assert by_type["transfer_in"]["external_reference"] is None


def test_json_import_contract_is_published_in_openapi(client) -> None:
    schema = client.get("/openapi.json").json()
    preview = schema["paths"][
        "/api/portfolios/{portfolio_id}/transaction-imports/preview"
    ]["post"]
    commit = schema["paths"][
        "/api/portfolios/{portfolio_id}/transaction-imports/commit"
    ]["post"]
    assert preview["requestBody"]["required"] is True
    assert any(
        parameter["name"] == "Idempotency-Key" and parameter["required"] is True
        for parameter in commit["parameters"]
    )


def test_documented_transaction_api_routes_exist_in_openapi(client) -> None:
    workspace_root = Path(__file__).resolve().parents[4]
    sources = [
        workspace_root / "docs" / "TRANSACTION_IMPORT_API_GUIDE.md",
        workspace_root / "docs" / "PORTFOLIO_COPILOT_HARNESS.md",
    ]
    schema = client.get("/openapi.json").json()
    actual_routes = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
    }
    documented_routes: set[tuple[str, str]] = set()
    for source in sources:
        content = source.read_text(encoding="utf-8")
        for code_span in re.findall(r"`([^`]*?/api/[^`]*)`", content):
            if "$" in code_span:
                continue
            path_match = re.search(
                r"(/api/[A-Za-z0-9_/{}/.\-]+)",
                code_span,
            )
            assert path_match is not None
            path = path_match.group(1).rstrip(".,;；。")
            methods = re.findall(
                r"\b(GET|POST|PUT|PATCH|DELETE)\b",
                code_span[: path_match.start()],
            )
            documented_routes.update((method, path) for method in methods)
        documented_routes.update(
            (method, path.rstrip(".,;；。"))
            for method, path in re.findall(
                r"\b(GET|POST|PUT|PATCH|DELETE) (/api/[A-Za-z0-9_/{}/.\-]+)",
                content,
            )
        )

    assert documented_routes
    assert documented_routes <= actual_routes, sorted(documented_routes - actual_routes)


def test_documented_json_example_and_action_enum_match_the_live_contract() -> None:
    workspace_root = Path(__file__).resolve().parents[4]
    example = json.loads(
        (
            workspace_root
            / "docs"
            / "examples"
            / "transaction_import_api_preview.json"
        ).read_text(encoding="utf-8")
    )
    request = TransactionImportPreviewRequest.model_validate(example)
    assert len(request.records) == 4
    assert set(get_args(TransactionImportAction)) == {
        action
        for actions in TRANSACTION_ACTIONS.values()
        for action in actions
    }
