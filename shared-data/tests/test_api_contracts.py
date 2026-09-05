from base64 import b64encode

import pytest
from pydantic import ValidationError

from studio_data import contracts


@pytest.mark.parametrize(
    "request_type",
    [
        contracts.StudioNavImportFileRequest,
        contracts.StudioNavImportPreviewRequest,
    ],
)
def test_nav_file_requests_enforce_decoded_size_limit(
    request_type: type,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts, "MAX_NAV_IMPORT_BYTES", 4)
    request_kwargs = {
        "status": "complete",
    } if request_type is contracts.StudioNavImportFileRequest else {}
    payload = request_type(
        file_name="nav.csv",
        file_content_base64=b64encode(b"12345").decode("ascii"),
        **request_kwargs,
    )

    with pytest.raises(ValueError, match="exceeds the 4-byte limit"):
        payload.decoded_bytes()


@pytest.mark.parametrize(
    "invalid_basis",
    [
        "adjusted_close",
        "total_return_nav",
    ],
)
def test_quote_policy_rejects_total_return_basis_for_valuation(
    invalid_basis: str,
) -> None:
    with pytest.raises(ValidationError, match="valuation cannot use total-return quote bases"):
        contracts.StudioQuoteSelectionPolicyUpdateRequest.model_validate(
            {
                "quote_selection_policy": {
                    "trading": ["close"],
                    "valuation": [invalid_basis],
                    "total_return": [invalid_basis],
                    "chart": [invalid_basis],
                    "reference": ["close"],
                }
            }
        )


@pytest.mark.parametrize("role", ["total_return", "chart"])
@pytest.mark.parametrize(
    "invalid_basis",
    [
        "cumulative_nav",
        "accumulated_nav",
        "cum_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
    ],
)
def test_quote_policy_rejects_retired_nav_aliases(
    role: str,
    invalid_basis: str,
) -> None:
    policy = {
        "trading": ["official_nav"],
        "valuation": ["official_nav"],
        "total_return": ["total_return_nav"],
        "chart": ["total_return_nav"],
        "reference": ["official_nav"],
    }
    policy[role] = [invalid_basis]
    with pytest.raises(ValidationError, match="Input should be"):
        contracts.StudioQuoteSelectionPolicyUpdateRequest.model_validate(
            {"quote_selection_policy": policy}
        )


def _cash_action_payload() -> dict[str, object]:
    return {
        "event_type": "cash_distribution",
        "effective_date": "2026-06-30",
        "cash_per_unit": "0.05",
        "evidence_kind": "provider_notice",
        "source": "manager notice 2026-06-30",
        "provenance": {"notice_sha256": "abc"},
    }


def _reinvestment_evidence_payload() -> dict[str, object]:
    return {
        "reinvestment_nav": "1.20",
        "evidence_kind": "provider_notice",
        "source": "manager notice 2026-06-30",
        "provenance": {"notice_sha256": "abc"},
    }


def _mutation_audit() -> dict[str, object]:
    return {
        "client_mutation_id": "mutation-1",
        "recorded_by": "data-operations-admin",
        "revision_reason": "Confirmed against the signed manager notice.",
    }


def test_fund_nav_action_create_separates_action_from_reinvestment_evidence() -> None:
    request = contracts.StudioFundNavActionCreateRequest.model_validate(
        {
            **_mutation_audit(),
            "action": _cash_action_payload(),
            "reinvestment_evidence": _reinvestment_evidence_payload(),
        }
    )

    assert request.action.cash_per_unit is not None
    assert request.reinvestment_evidence is not None
    assert request.reinvestment_evidence.reinvestment_nav > 0

    with pytest.raises(ValidationError, match="recorded_by"):
        contracts.StudioFundNavActionCreateRequest.model_validate(
            {
                "client_mutation_id": "mutation-1",
                "revision_reason": "Missing administrator identity.",
                "action": _cash_action_payload(),
            }
        )


def test_fund_nav_revision_requests_are_append_only_and_explicit() -> None:
    correction = contracts.StudioFundNavActionRevisionRequest.model_validate(
        {
            **_mutation_audit(),
            "predecessor_fund_nav_event_id": "event-v1",
            "revision_kind": "correction",
            "action": {**_cash_action_payload(), "cash_per_unit": "0.06"},
        }
    )
    assert correction.predecessor_fund_nav_event_id == "event-v1"

    with pytest.raises(ValidationError, match="action is required"):
        contracts.StudioFundNavActionRevisionRequest.model_validate(
            {
                **_mutation_audit(),
                "predecessor_fund_nav_event_id": "event-v1",
                "revision_kind": "correction",
            }
        )

    with pytest.raises(ValidationError, match="forbids replacement fields"):
        contracts.StudioFundNavActionRevisionRequest.model_validate(
            {
                **_mutation_audit(),
                "predecessor_fund_nav_event_id": "event-v1",
                "revision_kind": "cancellation",
                "action": _cash_action_payload(),
            }
        )


def test_fund_nav_evidence_cancellation_cannot_rewrite_the_predecessor() -> None:
    request = contracts.StudioFundNavReinvestmentEvidenceRevisionRequest.model_validate(
        {
            **_mutation_audit(),
            "predecessor_fund_nav_reinvestment_evidence_id": "evidence-v1",
            "revision_kind": "cancellation",
        }
    )
    assert request.evidence is None

    with pytest.raises(ValidationError, match="forbids replacement fields"):
        contracts.StudioFundNavReinvestmentEvidenceRevisionRequest.model_validate(
            {
                **_mutation_audit(),
                "predecessor_fund_nav_reinvestment_evidence_id": "evidence-v1",
                "revision_kind": "cancellation",
                "evidence": _reinvestment_evidence_payload(),
            }
        )


def test_candidate_rejection_requires_an_audited_decision_actor() -> None:
    with pytest.raises(ValidationError, match="decision_by"):
        contracts.StudioFundNavActionCandidateRejectRequest.model_validate(
            {"reason": "Not a distribution."}
        )
