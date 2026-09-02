from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.services import ledger, research_solver, valuation_fx


def _fx_detail(
    instrument_id: str,
    quote_currency: str,
    points: list[tuple[str, float]],
) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "instrument_name": instrument_id,
        "instrument_type": "fx",
        "currency": quote_currency,
        "quote_selection_policy": {"valuation": ["spot"]},
        "market_data": [
            {
                "instrument_id": instrument_id,
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": point_date,
                "value": str(value),
                "currency": quote_currency,
                "price_unit": "rate",
                "price_scale": 1.0,
                "provider": "valuation-fx-golden",
                "status": "complete",
            }
            for point_date, value in points
        ],
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" hkd ", "HKD"),
        (None, ""),
        ("", ""),
        (123, "123"),
    ],
)
def test_currency_normalization_never_invents_a_missing_currency(value, expected):
    assert valuation_fx.normalized_currency(value) == expected


def test_required_currency_rejects_missing_values() -> None:
    assert valuation_fx.required_currency(" hkd ") == "HKD"
    with pytest.raises(ValueError, match="transaction currency is required"):
        valuation_fx.required_currency(None, field_name="transaction currency")


@pytest.mark.parametrize(
    ("quantity", "last_price", "price_scale", "expected"),
    [
        (10.0, 12.5, None, 125.0),
        (10.0, 12.5, 1.0, 125.0),
        (10.0, None, None, None),
        (10.0, 12.5, 0.0, None),
        (10.0, 12.5, float("inf"), None),
    ],
)
def test_position_market_value_golden(
    quantity,
    last_price,
    price_scale,
    expected,
):
    kwargs = {
        "quantity": quantity,
        "last_price": last_price,
        "price_scale": price_scale,
    }
    assert valuation_fx.position_market_value(**kwargs) == expected


def test_direct_instrument_map_golden():
    payload = {
        "rates": [
            {
                "source_kind": "direct",
                "base_currency": " usd ",
                "quote_currency": "hkd",
                "instrument_id": " fx-usd-hkd ",
            },
            {
                "source_kind": "derived",
                "base_currency": "USD",
                "quote_currency": "CNY",
                "instrument_id": "ignored-derived",
            },
            {
                "source_kind": "direct",
                "base_currency": "USD",
                "quote_currency": "CNY",
                "instrument_id": "fx-usd-hkd",
            },
            {
                "source_kind": "direct",
                "base_currency": "USD",
                "quote_currency": "HKD",
                "instrument_id": "fx-unknown",
            },
            {"source_kind": "direct", "base_currency": "", "quote_currency": "EUR"},
            "invalid-row",
        ]
    }
    expected = {("USD", "HKD"): "fx-usd-hkd"}

    assert valuation_fx.fx_direct_instrument_map(payload) == expected


def test_fx_posting_rejects_a_missing_target_account_currency() -> None:
    transaction = {
        "transaction_id": "fx-missing-target-currency",
        "transaction_sequence": 1,
        "portfolio_id": "portfolio-currency-contract",
        "transaction_type": "fx_conversion",
        "trade_date": "2026-01-02",
        "settlement_date": "2026-01-02",
        "account_id": "cash-usd",
        "counterparty_account_id": "cash-hkd",
        "gross_amount": 100.0,
        "counter_amount": 780.0,
        "currency": "USD",
    }

    with pytest.raises(ValueError, match="currency for account 'cash-hkd' is required"):
        ledger.derive_ledger_postings(
            "portfolio-currency-contract",
            [transaction],
            account_currency_map={"cash-usd": "USD"},
        )


def test_current_ledger_rejects_non_complete_fx_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "supported_currencies": ["USD", "HKD", "CNY", "EUR", "GBP", "CHF"],
        "rates": [
            {
                "base_currency": "USD",
                "quote_currency": "HKD",
                "rate": "7.8",
                "source_kind": "direct",
                "instrument_id": "fx-usd-hkd",
                "status": "partial",
            },
            {
                "base_currency": "HKD",
                "quote_currency": "USD",
                "rate": "0.1282",
                "source_kind": "inverse",
                "instrument_id": "fx-usd-hkd",
                "status": "unavailable",
            },
            {
                "base_currency": "HKD",
                "quote_currency": "CNY",
                "rate": "0.9231",
                "source_kind": "cross",
                "instrument_id": None,
                "status": "unavailable",
            },
        ],
    }
    monkeypatch.setattr(ledger, "get_platform_fx_rates", lambda: payload)

    rate_map = ledger.resolve_fx_rate_map()

    assert rate_map == {
        ("USD", "USD"): 1.0,
        ("HKD", "HKD"): 1.0,
        ("CNY", "CNY"): 1.0,
        ("EUR", "EUR"): 1.0,
        ("GBP", "GBP"): 1.0,
        ("CHF", "CHF"): 1.0,
    }


@pytest.mark.parametrize("status", ["partial", "unavailable"])
def test_non_complete_fx_observation_is_unavailable_to_valuation_consumers(
    status: str,
) -> None:
    detail = _fx_detail(
        "fx-usd-hkd",
        "HKD",
        [("2026-01-02", 7.8)],
    )
    detail["market_data"][0]["status"] = status

    resolved = valuation_fx.resolve_fx_rate_on(
        as_of_date=date(2026, 1, 2),
        base_currency="USD",
        quote_currency="HKD",
        direct_instruments={("USD", "HKD"): "fx-usd-hkd"},
        instrument_detail_cache={"fx-usd-hkd": detail},
        instrument_detail_loader=lambda _instrument_id: None,
    )

    assert resolved is None


def test_latest_partial_fx_keeps_historical_topology_across_ledger_and_research(
    monkeypatch,
) -> None:
    fx_instrument_id = "fx-usd-cny"
    payload = {
        "supported_currencies": ["USD", "CNY"],
        "rates": [
            {
                "source_kind": "direct",
                "base_currency": "USD",
                "quote_currency": "CNY",
                "instrument_id": fx_instrument_id,
                "rate": "7.3",
                "as_of_date": "2026-01-03",
                "status": "partial",
            }
        ],
    }
    historical_detail = _fx_detail(
        fx_instrument_id,
        "CNY",
        [("2026-01-02", 7.2)],
    )
    expected_map = {("USD", "CNY"): fx_instrument_id}

    monkeypatch.setattr(ledger, "get_platform_fx_rates", lambda: payload)
    monkeypatch.setattr(
        ledger,
        "get_registry_instrument_detail",
        lambda instrument_id: historical_detail if instrument_id == fx_instrument_id else None,
    )
    monkeypatch.setattr(
        ledger,
        "derive_ledger_postings",
        lambda *_args, **_kwargs: [
            {
                "account_id": "cash-cny",
                "transaction_id": "deposit-cny",
                "cash_amount_delta": 720.0,
                "effective_date": "2026-01-02",
            }
        ],
    )
    ledger_workspace = ledger.build_account_workspace(
        "portfolio-fx-topology",
        [
            {
                "account_id": "cash-cny",
                "account_name": "CNY Cash",
                "account_type": "deposit_account",
                "currency": "CNY",
            }
        ],
        [],
        base_currency="USD",
        as_of_date=date(2026, 1, 2),
    )

    monkeypatch.setattr(
        research_solver,
        "get_portfolio",
        lambda _portfolio_id: {"portfolio_id": "portfolio-fx-topology", "base_currency": "USD"},
    )
    monkeypatch.setattr(
        research_solver,
        "taxonomy_configuration_as_of",
        lambda _portfolio_id, _taxonomy_id, _as_of_date: {
            "taxonomy": {
                "taxonomy_id": "taxonomy-fx-topology",
                "name": "Planning",
                "root_default_target_dimension": "weight",
                "primary_assignment_scope": "instrument",
                "planning_enabled": True,
                "status": "active",
            },
            "taxonomy_nodes": [],
            "taxonomy_assignments": [],
            "target_sets": [],
            "target_set_lines": [],
        },
    )
    monkeypatch.setattr(research_solver, "list_accounts", lambda _portfolio_id: [])
    monkeypatch.setattr(research_solver, "get_platform_fx_rates", lambda: payload)
    research_state = research_solver._build_taxonomy_state(
        "portfolio-fx-topology",
        planning_taxonomy_id="taxonomy-fx-topology",
        as_of_date=date(2026, 1, 2),
        instrument_detail_cache={fx_instrument_id: historical_detail},
    )

    assert valuation_fx.fx_direct_instrument_map(payload) == expected_map
    assert research_state.direct_fx_instruments == expected_map
    assert ledger_workspace["accounts"][0]["derived_cash_balance_base"] == pytest.approx(100.0)
    assert research_solver._convert_price_to_base(
        research_state,
        point_date=date(2026, 1, 2),
        value=720.0,
        point_currency="CNY",
    ) == pytest.approx(100.0)


def test_direct_inverse_and_pivot_fx_resolution_golden():
    details = {
        "fx-usd-hkd": _fx_detail(
            "fx-usd-hkd",
            "HKD",
            [("2026-01-02", 7.8), ("2026-01-04", 7.9)],
        ),
        "fx-usd-cny": _fx_detail(
            "fx-usd-cny",
            "CNY",
            [("2026-01-02", 7.2), ("2026-01-03", 7.3)],
        ),
    }
    direct_instruments = {
        ("USD", "HKD"): "fx-usd-hkd",
        ("USD", "CNY"): "fx-usd-cny",
    }
    loaded: list[str] = []

    def loader(instrument_id: str):
        loaded.append(instrument_id)
        return details.get(instrument_id)

    cache: dict[str, dict[str, object] | None] = {}
    common = {
        "as_of_date": date(2026, 1, 5),
        "direct_instruments": direct_instruments,
    }

    direct = valuation_fx.resolve_fx_rate_on(
        **common,
        base_currency=" usd ",
        quote_currency="hkd",
        instrument_detail_cache=cache,
        instrument_detail_loader=loader,
    )
    assert direct == {
        "rate": 7.9,
        "as_of_date": date(2026, 1, 4),
        "status": "complete",
        "stale": True,
        "source_instrument_ids": ["fx-usd-hkd"],
    }

    inverse = valuation_fx.resolve_fx_rate_on(
        **common,
        base_currency="HKD",
        quote_currency="USD",
        instrument_detail_cache=cache,
        instrument_detail_loader=loader,
    )
    assert inverse is not None
    assert inverse["rate"] == pytest.approx(1 / 7.9)
    assert inverse["as_of_date"] == date(2026, 1, 4)
    assert inverse["stale"] is True
    assert inverse["source_instrument_ids"] == ["fx-usd-hkd"]

    pivot = valuation_fx.resolve_fx_rate_on(
        **common,
        base_currency="HKD",
        quote_currency="CNY",
        instrument_detail_cache=cache,
        instrument_detail_loader=loader,
    )
    assert pivot is not None
    assert pivot["rate"] == pytest.approx(7.3 / 7.9)
    assert pivot["as_of_date"] == date(2026, 1, 3)
    assert pivot["status"] == "complete"
    assert pivot["stale"] is True
    assert pivot["source_instrument_ids"] == ["fx-usd-cny", "fx-usd-hkd"]

    assert valuation_fx.resolve_fx_rate_on(
        **common,
        base_currency="USD",
        quote_currency="USD",
        instrument_detail_cache=cache,
        instrument_detail_loader=loader,
    )
    assert valuation_fx.resolve_fx_rate_on(
        **common,
        base_currency="USD",
        quote_currency="EUR",
        instrument_detail_cache=cache,
        instrument_detail_loader=loader,
    ) is None

    assert loaded.count("fx-usd-hkd") == 1
    assert loaded.count("fx-usd-cny") == 1


def test_previous_fx_and_conversion_golden():
    details = {
        "fx-usd-hkd": _fx_detail(
            "fx-usd-hkd",
            "HKD",
            [("2026-01-02", 7.8), ("2026-01-04", 7.9)],
        ),
        "fx-usd-cny": _fx_detail(
            "fx-usd-cny",
            "CNY",
            [("2026-01-02", 7.2), ("2026-01-03", 7.3)],
        ),
    }
    direct_instruments = {
        ("USD", "HKD"): "fx-usd-hkd",
        ("USD", "CNY"): "fx-usd-cny",
    }

    def loader(instrument_id: str):
        return details.get(instrument_id)

    cache: dict[str, dict[str, object] | None] = {}

    previous = valuation_fx.resolve_previous_fx_rate_before(
        before_date=date(2026, 1, 4),
        base_currency="HKD",
        quote_currency="CNY",
        direct_instruments=direct_instruments,
        instrument_detail_cache=cache,
        instrument_detail_loader=loader,
    )
    assert previous is not None
    assert previous["rate"] == pytest.approx(7.3 / 7.8)
    assert previous["as_of_date"] == date(2026, 1, 2)
    assert previous["stale"] is False

    converted = valuation_fx.convert_amount_on(
        790.0,
        as_of_date=date(2026, 1, 5),
        from_currency="HKD",
        to_currency="USD",
        direct_fx_instruments=direct_instruments,
        instrument_detail_cache=cache,
        instrument_detail_loader=loader,
    )
    assert converted[0] == pytest.approx(100.0)
    assert converted[1] is True

    assert valuation_fx.convert_amount_on(
        None,
        as_of_date=date(2026, 1, 5),
        from_currency="HKD",
        to_currency="USD",
        direct_fx_instruments=direct_instruments,
        instrument_detail_cache=cache,
        instrument_detail_loader=loader,
    ) == (None, False)
