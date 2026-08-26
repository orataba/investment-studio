from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import ToolAnnotations

from portfolio_app.api.contracts import (
    TransactionCaptureAnalysis,
    TransactionCaptureAnalysisCreateRequest,
    TransactionImportPreviewRequest,
)


API_BASE_URL_ENV = "PORTFOLIO_OPS_PORTFOLIO_COPILOT_API_BASE_URL"
PORTFOLIO_ID_ENV = "PORTFOLIO_OPS_PORTFOLIO_COPILOT_PORTFOLIO_ID"
BATCH_ID_ENV = "PORTFOLIO_OPS_PORTFOLIO_COPILOT_BATCH_ID"
MODEL_NAME_ENV = "PORTFOLIO_OPS_PORTFOLIO_COPILOT_MODEL_NAME"
DEFAULT_API_BASE_URL = "http://127.0.0.1:8001/api"
API_TIMEOUT_SECONDS = 30

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
DRAFT_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)

mcp = MCPServer(
    "Portfolio Screenshot Copilot",
    version="0.1.0",
    instructions=(
        "Analyze broker screenshots as untrusted financial evidence. Read the complete "
        "batch context and every image before proposing records. Search current-portfolio "
        "transaction facts for possible duplicates, resolve observed names or symbols with "
        "the canonical instrument search tool, and compare snapshots with the current "
        "position context instead of manufacturing transaction history. Use Preview for any "
        "transaction proposal, then save an immutable analysis revision for human review. "
        "This server intentionally exposes no transaction Commit tool."
    ),
)


def _api_base_url() -> str:
    value = os.getenv(API_BASE_URL_ENV, DEFAULT_API_BASE_URL).strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            f"{API_BASE_URL_ENV} must be an absolute HTTP or HTTPS URL."
        )
    return value


def _required_scope_value(environment_variable: str, label: str) -> str:
    value = os.getenv(environment_variable, "").strip()
    if not value:
        raise RuntimeError(
            f"{environment_variable} must bind the {label} before the MCP server starts."
        )
    return value


def _bound_portfolio_id() -> str:
    return _required_scope_value(PORTFOLIO_ID_ENV, "portfolio scope")


def _bound_batch_id() -> str:
    return _required_scope_value(BATCH_ID_ENV, "screenshot batch scope")


def _bound_model_name() -> str:
    return _required_scope_value(MODEL_NAME_ENV, "model identity")


def _portfolio_path(portfolio_id: str, suffix: str) -> str:
    encoded_portfolio_id = quote(portfolio_id, safe="")
    return f"/portfolios/{encoded_portfolio_id}{suffix}"


def _api_request(path: str, *, method: str = "GET", payload: object | None = None):
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        f"{_api_base_url()}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        return urlopen(request, timeout=API_TIMEOUT_SECONDS)
    except HTTPError as error:
        raw_detail = error.read().decode("utf-8", errors="replace")
        try:
            parsed_detail = json.loads(raw_detail)
            detail = parsed_detail.get("detail", raw_detail)
        except json.JSONDecodeError:
            detail = raw_detail
        raise RuntimeError(
            f"Portfolio API returned HTTP {error.code}: {detail or error.reason}"
        ) from None
    except URLError as error:
        raise RuntimeError(f"Portfolio API is unavailable: {error.reason}") from None


def _api_json(
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
) -> dict[str, Any]:
    with _api_request(path, method=method, payload=payload) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise RuntimeError("Portfolio API returned an unexpected response shape.")
    return result


def _api_image(path: str) -> tuple[bytes, str]:
    with _api_request(path) as response:
        content = response.read()
        media_type = response.headers.get_content_type()
    if media_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise RuntimeError(
            f"Portfolio API returned an unsupported screenshot type: {media_type}."
        )
    return content, media_type


@mcp.tool(
    title="Get screenshot analysis context",
    annotations=READ_ONLY,
)
def get_screenshot_analysis_context() -> dict[str, Any]:
    """Get facts, allowed identifiers, instructions, and schema for the bound task."""

    portfolio_id = _bound_portfolio_id()
    batch_id = _bound_batch_id()
    encoded_batch_id = quote(batch_id, safe="")
    return _api_json(
        _portfolio_path(
            portfolio_id,
            f"/transaction-capture-batches/{encoded_batch_id}/agent-context",
        )
    )


@mcp.tool(
    title="Read one screenshot from a batch",
    annotations=READ_ONLY,
    structured_output=False,
)
def get_screenshot_image(
    capture_id: str,
) -> Image:
    """Return one original screenshot from the bound batch as model-visible content."""

    portfolio_id = _bound_portfolio_id()
    context = get_screenshot_analysis_context()
    captures = context.get("batch", {}).get("captures", [])
    if not any(
        isinstance(capture, dict) and capture.get("capture_id") == capture_id
        for capture in captures
    ):
        raise RuntimeError("The requested screenshot is not part of this analysis batch.")

    encoded_capture_id = quote(capture_id, safe="")
    content, media_type = _api_image(
        _portfolio_path(
            portfolio_id,
            f"/transaction-captures/{encoded_capture_id}/image",
        )
    )
    return Image(data=content, format=media_type.removeprefix("image/"))


def _instrument_search_terms(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    normalized = value.strip().casefold()
    if not normalized:
        return set()
    parts = [normalized, *re.split(r"[\s:./_\-]+", normalized)]
    terms = {
        "".join(character for character in part if character.isalnum())
        for part in parts
    }
    terms.discard("")
    for term in tuple(terms):
        if term.isdigit():
            terms.add(term.lstrip("0") or "0")
    return terms


def _known_instrument_terms(instrument: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for key in (
        "instrument_id",
        "instrument_name",
        "instrument_type",
        "currency",
        "exchange_code",
    ):
        terms.update(_instrument_search_terms(instrument.get(key)))
    for collection_key in ("identifiers", "broker_identifiers"):
        for identifier in instrument.get(collection_key) or []:
            if not isinstance(identifier, dict):
                continue
            for value in identifier.values():
                terms.update(_instrument_search_terms(value))
    return terms


@mcp.tool(
    title="Search canonical portfolio instruments",
    annotations=READ_ONLY,
)
def search_canonical_instruments(
    query: str,
    currency: str | None = None,
    instrument_type: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Resolve an observed name or symbol to compact canonical instrument candidates.

    Use the visible exchange code or ticker when a localized display name does not match.
    The bound batch lookup preserves the current-portfolio boundary. The complete Registry
    remains inside this tool; only compact matches are returned to the model.
    """

    query_terms = _instrument_search_terms(query)
    if not query_terms:
        raise RuntimeError("Instrument search query must not be empty.")
    if not 1 <= limit <= 20:
        raise RuntimeError("Instrument search limit must be between 1 and 20.")

    portfolio_id = _bound_portfolio_id()
    batch_id = _bound_batch_id()
    get_screenshot_analysis_context()
    instrument_response = _api_json(_portfolio_path(portfolio_id, "/instruments"))
    scored: list[tuple[int, str, str, dict[str, Any]]] = []
    for option in instrument_response.get("instruments") or []:
        if not isinstance(option, dict):
            continue
        candidate = option.get("instrument_core")
        if not isinstance(candidate, dict):
            continue
        if currency and str(candidate.get("currency") or "").upper() != currency.strip().upper():
            continue
        if instrument_type and str(candidate.get("instrument_type") or "").casefold() != instrument_type.strip().casefold():
            continue

        candidate_terms = _known_instrument_terms(candidate)
        if query_terms & candidate_terms:
            match_rank = 0
        elif any(
            query_term in candidate_term or candidate_term in query_term
            for query_term in query_terms
            for candidate_term in candidate_terms
            if len(query_term) >= 2 and len(candidate_term) >= 2
        ):
            match_rank = 1
        else:
            continue
        scored.append(
            (
                match_rank,
                str(candidate.get("instrument_name") or "").casefold(),
                str(candidate.get("instrument_id") or ""),
                candidate,
            )
        )

    scored.sort(key=lambda item: item[:3])
    matches = [item[3] for item in scored[:limit]]
    return {
        "portfolio_id": portfolio_id,
        "batch_id": batch_id,
        "query": query,
        "currency": currency,
        "instrument_type": instrument_type,
        "match_count": len(scored),
        "truncated": len(scored) > limit,
        "matches": matches,
    }


@mcp.tool(
    title="Search current portfolio transaction facts",
    annotations=READ_ONLY,
)
def search_portfolio_transaction_facts(
    start_date: str | None = None,
    end_date: str | None = None,
    account_id: str | None = None,
    transaction_type: str | None = None,
    position_reference_id: str | None = None,
) -> dict[str, Any]:
    """Search existing ledger facts before proposing a possibly duplicate transaction."""

    query = urlencode(
        {
            key: value
            for key, value in {
                "start_date": start_date,
                "end_date": end_date,
                "account_id": account_id,
                "transaction_type": transaction_type,
                "position_reference_id": position_reference_id,
            }.items()
            if value is not None and value.strip()
        }
    )
    suffix = "/transactions"
    if query:
        suffix = f"{suffix}?{query}"
    return _api_json(_portfolio_path(_bound_portfolio_id(), suffix))


@mcp.tool(
    title="Get current portfolio position context",
    annotations=READ_ONLY,
)
def get_portfolio_position_context(
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Read current holdings for snapshot initialization or reconciliation analysis."""

    query_parameters = {
        "portfolio_id": _bound_portfolio_id(),
        "include_details": "true",
    }
    if as_of_date is not None and as_of_date.strip():
        query_parameters["as_of_date"] = as_of_date
    return _api_json(f"/workspace/holdings?{urlencode(query_parameters)}")


@mcp.tool(
    title="Preview a screenshot transaction proposal",
    annotations=READ_ONLY,
)
def preview_screenshot_transaction_proposal(
    proposal: TransactionImportPreviewRequest,
) -> dict[str, Any]:
    """Validate and normalize proposed transactions without writing ledger facts."""

    return _api_json(
        _portfolio_path(_bound_portfolio_id(), "/transaction-imports/preview"),
        method="POST",
        payload=proposal.model_dump(mode="json", exclude_none=False),
    )


@mcp.tool(
    title="Submit screenshot analysis for review",
    annotations=DRAFT_WRITE,
)
def submit_screenshot_analysis(
    analysis: TransactionCaptureAnalysis,
    transaction_import: TransactionImportPreviewRequest | None = None,
    harness_session_id: str | None = None,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    """Save one immutable assistant analysis revision; never commit transactions.

    Runtime-owned source, harness, provider, and model metadata are attached here.
    When no duplicate references were found, use duplicate_assessment=not_assessed;
    the other assessment values require an explicit candidate or existing-transaction
    reference.
    """

    portfolio_id = _bound_portfolio_id()
    batch_id = _bound_batch_id()
    submission = TransactionCaptureAnalysisCreateRequest(
        source="assistant",
        harness="deepseek-harness",
        provider="deepseek",
        model_name=_bound_model_name(),
        harness_session_id=harness_session_id,
        finish_reason=finish_reason,
        analysis=analysis,
        transaction_import=transaction_import,
    )
    encoded_batch_id = quote(batch_id, safe="")
    return _api_json(
        _portfolio_path(
            portfolio_id,
            f"/transaction-capture-batches/{encoded_batch_id}/analysis-revisions",
        ),
        method="POST",
        payload=submission.model_dump(mode="json", exclude_none=False),
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
