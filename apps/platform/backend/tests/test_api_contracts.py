from base64 import b64encode

import pytest
from pydantic import ValidationError

from portfolio_ops_instrument_core.models import MarketDataPoint

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
    payload = request_type(
        file_name="nav.csv",
        file_content_base64=b64encode(b"12345").decode("ascii"),
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


def test_market_data_point_contract_requires_canonical_revision_identity() -> None:
    point = contracts.PlatformMarketDataPoint.model_validate(
        {
            "quote_series_id": "series-1",
            "observation_id": "observation-1",
            "revision_id": "revision-1",
            "revision_number": 2,
            "metric_family": "nav",
            "quote_basis": "official_nav",
            "as_of_date": "2026-07-10",
            "value": "100.00",
            "currency": "USD",
            "source_ref": "issuer-file",
            "status": "complete",
            "source_published_at": "2026-07-11T00:00:00Z",
            "ingested_at": None,
            "payload_hash": "sha256:abc",
        }
    )
    assert point.quote_series_id == "series-1"
    assert point.source_ref == "issuer-file"
    assert point.revision_number == 2


def test_currency_contract_normalizes_ingress_once_and_rejects_non_iso_shape() -> None:
    request = contracts.PlatformMarketDataUpsertRequest.model_validate(
        {
            "metric_family": "nav",
            "quote_basis": "official_nav",
            "as_of_date": "2026-07-10",
            "value": "100",
            "currency": " cny ",
        }
    )
    assert request.currency == "CNY"

    for invalid_currency in ("", "CN", "USDT", "C1Y"):
        with pytest.raises(ValidationError):
            contracts.PlatformMarketDataUpsertRequest.model_validate(
                {
                    "metric_family": "nav",
                    "quote_basis": "official_nav",
                    "as_of_date": "2026-07-10",
                    "value": "100",
                    "currency": invalid_currency,
                }
            )


def test_platform_write_contract_rejects_removed_provider_field() -> None:
    with pytest.raises(ValidationError, match="provider"):
        contracts.PlatformMarketDataUpsertRequest.model_validate(
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-07-10",
                "value": "100",
                "currency": "USD",
                "provider": "removed-field",
            }
        )


def test_core_market_data_point_rejects_removed_provider_field() -> None:
    with pytest.raises(ValidationError, match="provider"):
        MarketDataPoint.model_validate(
            {
                "quote_series_id": "series-1",
                "observation_id": "observation-1",
                "revision_id": "revision-1",
                "revision_number": 1,
                "instrument_id": "fund-1",
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-07-10",
                "value": "100",
                "currency": "USD",
                "source_ref": "issuer-file",
                "provider": "removed-field",
                "status": "complete",
                "payload_hash": "sha256:abc",
            }
        )


@pytest.mark.parametrize("status", ["unavailable", "withdrawn"])
def test_market_data_upsert_contract_rejects_non_source_status(status: str) -> None:
    with pytest.raises(ValidationError):
        contracts.PlatformMarketDataUpsertRequest.model_validate(
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-07-10",
                "value": "100",
                "currency": "USD",
                "status": status,
            }
        )
