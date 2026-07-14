from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest

from portfolio_app.calculations.portfolio_daily.db_models import DEPENDENCY_TABLES
from portfolio_app.calculations.portfolio_daily.manifest_repository import (
    ManifestDependencies,
)
from portfolio_app.calculations.portfolio_daily.sealed_manifest import (
    SealedPortfolioDailyManifest,
)
from portfolio_app.calculations.portfolio_daily.valuation_contracts import (
    MarketFactStatus,
)
from portfolio_app.calculations.portfolio_daily.valuation_input_builder import (
    ValuationInputBuildError,
    build_daily_valuation_book,
)


pytestmark = pytest.mark.no_database


def _manifest(
    *,
    quote_windows: list[dict[str, object]],
    quote_candidates: list[dict[str, object]],
    fx_paths: list[dict[str, object]],
    fx_legs: list[dict[str, object]],
    instrument_available: bool = True,
) -> SealedPortfolioDailyManifest:
    rows: dict[str, list[dict[str, object]]] = {
        table.name: [] for table in DEPENDENCY_TABLES
    }
    rows["portfolio_daily_config_input"] = [
        {
            "range_start": date(2026, 7, 13),
            "effective_as_of": date(2026, 7, 14),
            "base_currency": "USD",
        }
    ]
    rows["portfolio_daily_instrument_input"] = [
        {
            "instrument_id": "fund-cny",
            "requires_valuation": True,
            "currency": "CNY",
            "price_unit": "per_share" if instrument_available else None,
            "contract_multiplier": Decimal("1") if instrument_available else None,
            "price_factor": Decimal("1") if instrument_available else None,
            "valuation_contract_state": (
                "available" if instrument_available else "unavailable"
            ),
            "valuation_contract_reason_codes": (
                [] if instrument_available else ["instrument_terms_missing"]
            ),
        }
    ]
    rows["portfolio_daily_quote_window"] = quote_windows
    rows["portfolio_daily_quote_candidate"] = quote_candidates
    rows["portfolio_daily_fx_path"] = fx_paths
    rows["portfolio_daily_fx_leg"] = fx_legs
    dependencies = ManifestDependencies(rows)
    return SealedPortfolioDailyManifest(
        run_id=uuid4(),
        manifest_id=uuid4(),
        portfolio_id="portfolio-exact",
        effective_as_of=date(2026, 7, 14),
        cutoff_at=datetime(2026, 7, 14, 16, tzinfo=UTC),
        captured_generation=7,
        canonical_manifest_hash="a" * 64,
        dependency_counts=MappingProxyType(dependencies.counts),
        dependencies=dependencies,
    )


def _quote_rows() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    first_window = UUID("00000000-0000-0000-0000-000000000101")
    second_window = UUID("00000000-0000-0000-0000-000000000102")
    observation = UUID("00000000-0000-0000-0000-000000000201")
    first_revision = UUID("00000000-0000-0000-0000-000000000301")
    second_revision = UUID("00000000-0000-0000-0000-000000000302")
    windows = [
        {
            "quote_window_id": first_window,
            "instrument_id": "fund-cny",
            "valuation_date": date(2026, 7, 13),
            "quote_currency": "CNY",
            "candidate_count": 1,
            "adopted_count": 1,
            "selection_status": "selected",
            "reason_codes": [],
        },
        {
            "quote_window_id": second_window,
            "instrument_id": "fund-cny",
            "valuation_date": date(2026, 7, 14),
            "quote_currency": "CNY",
            "candidate_count": 1,
            "adopted_count": 1,
            "selection_status": "selected",
            "reason_codes": [],
        },
    ]
    candidates = [
        {
            "quote_window_id": first_window,
            "candidate_rank": 1,
            "observation_id": observation,
            "revision_id": first_revision,
            "observation_date": date(2026, 7, 13),
            "quote_value": Decimal("1.2345678901234567890123456789"),
            "decision": "adopted",
            "decision_reason_code": "adopted_exact",
        },
        {
            "quote_window_id": second_window,
            "candidate_rank": 1,
            "observation_id": observation,
            "revision_id": second_revision,
            "observation_date": date(2026, 7, 13),
            "quote_value": Decimal("1.2345678901234567890123456789"),
            "decision": "adopted",
            "decision_reason_code": "adopted_carry_forward",
        },
    ]
    return windows, candidates


def _fx_rows() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    paths: list[dict[str, object]] = []
    legs: list[dict[str, object]] = []
    for offset, valuation_date in enumerate(
        (date(2026, 7, 13), date(2026, 7, 14)), start=1
    ):
        path_id = UUID(f"00000000-0000-0000-0000-{400 + offset:012d}")
        observation_id = UUID(f"00000000-0000-0000-0000-{500 + offset:012d}")
        revision_id = UUID(f"00000000-0000-0000-0000-{600 + offset:012d}")
        paths.append(
            {
                "fx_path_id": path_id,
                "valuation_date": valuation_date,
                "from_currency": "CNY",
                "to_currency": "USD",
                "path_kind": "direct",
                "resolution_status": "resolved",
                "leg_count": 1,
                "resolved_rate": Decimal("0.14"),
                "rate_derivation_residual_exact": Decimal("0"),
                "reason_codes": [],
            }
        )
        legs.append(
            {
                "fx_path_id": path_id,
                "leg_order": 1,
                "leg_resolution_status": "resolved",
                "reason_codes": [],
                "from_currency": "CNY",
                "to_currency": "USD",
                "is_inverted": False,
                "observation_id": observation_id,
                "revision_id": revision_id,
                "observation_date": date(2026, 7, 13),
                "quoted_rate": Decimal("0.14"),
                "effective_rate": Decimal("0.14"),
                "rate_derivation_residual_exact": Decimal("0"),
            }
        )
    return paths, legs


def test_builds_exact_fresh_and_carried_market_facts_with_revision_lineage() -> None:
    quote_windows, quote_candidates = _quote_rows()
    fx_paths, fx_legs = _fx_rows()

    book = build_daily_valuation_book(
        _manifest(
            quote_windows=quote_windows,
            quote_candidates=quote_candidates,
            fx_paths=fx_paths,
            fx_legs=fx_legs,
        )
    )

    assert [fact.status for fact in book.quotes] == [
        MarketFactStatus.FRESH,
        MarketFactStatus.CARRY_FORWARD,
    ]
    assert book.quotes[0].price == Decimal("1.2345678901234567890123456789")
    assert book.quotes[0].lineage is not None
    assert book.quotes[0].lineage.source_revision_id.endswith("0301")
    assert book.quotes[1].reason_codes == ("market_data_quote_carried",)
    assert [fact.status for fact in book.fx_rates] == [
        MarketFactStatus.FRESH,
        MarketFactStatus.CARRY_FORWARD,
    ]
    assert book.fx_rates[1].reason_codes == ("fx_path_carried",)
    assert len(book.fx_rates[0].lineages) == 1


def test_rejects_selected_quote_when_candidate_count_does_not_close() -> None:
    quote_windows, quote_candidates = _quote_rows()
    quote_windows[0]["candidate_count"] = 2
    fx_paths, fx_legs = _fx_rows()

    with pytest.raises(ValuationInputBuildError, match="candidate count"):
        build_daily_valuation_book(
            _manifest(
                quote_windows=quote_windows,
                quote_candidates=quote_candidates,
                fx_paths=fx_paths,
                fx_legs=fx_legs,
            )
        )


def test_rejects_fx_path_when_frozen_derivation_residual_does_not_close() -> None:
    quote_windows, quote_candidates = _quote_rows()
    fx_paths, fx_legs = _fx_rows()
    fx_paths[1]["rate_derivation_residual_exact"] = Decimal("0.000000000000000001")

    with pytest.raises(ValuationInputBuildError, match="path .* residual"):
        build_daily_valuation_book(
            _manifest(
                quote_windows=quote_windows,
                quote_candidates=quote_candidates,
                fx_paths=fx_paths,
                fx_legs=fx_legs,
            )
        )


def test_rejects_fx_path_when_nonzero_residual_explains_rounded_composition() -> None:
    quote_windows, quote_candidates = _quote_rows()
    fx_paths, fx_legs = _fx_rows()
    fx_paths[1]["resolved_rate"] = Decimal("0.140000000000000001")
    fx_paths[1]["rate_derivation_residual_exact"] = Decimal(
        "0.000000000000000001"
    )

    with pytest.raises(ValuationInputBuildError, match="exact effective-leg product"):
        build_daily_valuation_book(
            _manifest(
                quote_windows=quote_windows,
                quote_candidates=quote_candidates,
                fx_paths=fx_paths,
                fx_legs=fx_legs,
            )
        )


def test_rejects_self_consistent_but_noncanonical_inverse_fx_leg() -> None:
    quote_windows, quote_candidates = _quote_rows()
    fx_paths, fx_legs = _fx_rows()
    fx_paths[1]["path_kind"] = "inverse"
    fx_paths[1]["resolved_rate"] = Decimal("0.15")
    fx_legs[1]["is_inverted"] = True
    fx_legs[1]["quoted_rate"] = Decimal("7")
    fx_legs[1]["effective_rate"] = Decimal("0.15")
    fx_legs[1]["rate_derivation_residual_exact"] = Decimal("0.05")

    with pytest.raises(
        ValuationInputBuildError,
        match="versioned Decimal50/HALF_EVEN operation",
    ):
        build_daily_valuation_book(
            _manifest(
                quote_windows=quote_windows,
                quote_candidates=quote_candidates,
                fx_paths=fx_paths,
                fx_legs=fx_legs,
            )
        )


def test_rejects_missing_daily_quote_instead_of_carrying_outside_manifest() -> None:
    quote_windows, quote_candidates = _quote_rows()
    quote_windows.pop()
    quote_candidates.pop()
    fx_paths, fx_legs = _fx_rows()

    with pytest.raises(ValuationInputBuildError, match="quote valuation grid"):
        build_daily_valuation_book(
            _manifest(
                quote_windows=quote_windows,
                quote_candidates=quote_candidates,
                fx_paths=fx_paths,
                fx_legs=fx_legs,
            )
        )


def test_rejects_discontinuous_fx_currency_chain() -> None:
    quote_windows, quote_candidates = _quote_rows()
    fx_paths, fx_legs = _fx_rows()
    fx_legs[0]["from_currency"] = "EUR"

    with pytest.raises(ValuationInputBuildError, match="currency chain"):
        build_daily_valuation_book(
            _manifest(
                quote_windows=quote_windows,
                quote_candidates=quote_candidates,
                fx_paths=fx_paths,
                fx_legs=fx_legs,
            )
        )


def test_rejects_missing_daily_fx_path_instead_of_treating_currency_as_base() -> None:
    quote_windows, quote_candidates = _quote_rows()
    fx_paths, fx_legs = _fx_rows()
    removed_path = fx_paths.pop()
    fx_legs[:] = [
        row for row in fx_legs if row["fx_path_id"] != removed_path["fx_path_id"]
    ]

    with pytest.raises(ValuationInputBuildError, match="daily FX valuation grid"):
        build_daily_valuation_book(
            _manifest(
                quote_windows=quote_windows,
                quote_candidates=quote_candidates,
                fx_paths=fx_paths,
                fx_legs=fx_legs,
            )
        )
