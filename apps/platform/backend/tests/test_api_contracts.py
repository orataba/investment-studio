from base64 import b64encode

import pytest
from pydantic import ValidationError

from platform_app.api import contracts


@pytest.mark.parametrize(
    "request_type",
    [
        contracts.PlatformNavImportFileRequest,
        contracts.PlatformNavImportPreviewRequest,
    ],
)
def test_nav_file_requests_enforce_decoded_size_limit(
    request_type: type,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts, "MAX_NAV_IMPORT_BYTES", 4)
    request_kwargs = {
        "status": "complete",
    } if request_type is contracts.PlatformNavImportFileRequest else {}
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
        "cumulative_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
    ],
)
def test_quote_policy_rejects_total_return_basis_for_valuation(
    invalid_basis: str,
) -> None:
    with pytest.raises(ValidationError, match="valuation cannot use total-return quote bases"):
        contracts.PlatformQuoteSelectionPolicyUpdateRequest.model_validate(
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
@pytest.mark.parametrize("invalid_basis", ["cumulative_nav", "accumulated_nav", "cum_nav"])
def test_quote_policy_rejects_cash_cumulative_nav_as_total_return(
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
    with pytest.raises(ValidationError, match="cash-cumulative NAV as total return"):
        contracts.PlatformQuoteSelectionPolicyUpdateRequest.model_validate(
            {"quote_selection_policy": policy}
        )
