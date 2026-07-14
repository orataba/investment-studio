from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_DOWN, ROUND_UP, getcontext, localcontext
from uuid import uuid4

import pytest

from portfolio_app.calculations.portfolio_daily.attribution_engine import (
    CASH_GROUP_KEY,
    calculate_exact_group_attribution,
)
from portfolio_app.calculations.portfolio_daily.db_models import DEPENDENCY_TABLES
from portfolio_app.calculations.portfolio_daily.ledger import (
    BuyEvent,
    CashTransferEvent,
    CostBasisMethod,
    ExpenseEvent,
    ExpenseKind,
    ExternalCashFlowEvent,
    ExternalFlowKind,
    ExternalFlowTiming,
    FactLineage,
    FxConversionEvent,
    FxRateConvention,
    IncomeEvent,
    IncomeKind,
    OpeningCashEvent,
    PositionTransferEvent,
    ReturnOfCapitalEvent,
    SellEvent,
    replay_ledger_series,
)
from portfolio_app.calculations.portfolio_daily.manifest_repository import (
    ManifestDependencies,
)
from portfolio_app.calculations.portfolio_daily.output_builder import (
    PortfolioDailyOutputBuildError,
    _group_contribution_rows,
    _portfolio_contribution_row,
    _validate_contribution_publication,
)
from portfolio_app.calculations.portfolio_daily.sealed_manifest import (
    SealedPortfolioDailyManifest,
)
from portfolio_app.calculations.portfolio_daily.valuation_contracts import (
    DailyValuationBook,
    FxValuationFact,
    InstrumentValuationFact,
    MarketFactStatus,
    QuoteValuationFact,
    exact_decimal_negate,
    exact_decimal_sum,
    exact_decimal_subtract,
)
from portfolio_app.calculations.portfolio_daily.valuation_engine import (
    calculate_exact_portfolio_valuation,
)


pytestmark = pytest.mark.no_database
D = Decimal
START = date(2026, 7, 1)
PORTFOLIO_ID = "attribution-golden"


def _effective_contribution(row) -> Decimal:
    assert row.contribution_method50 is not None
    return exact_decimal_sum(
        (
            row.contribution_method50,
            row.contribution_division_adjustment_exact,
        )
    )


def _lineage(key: str) -> FactLineage:
    return FactLineage(
        source_record_id=f"record-{key}",
        source_revision_id=f"revision-{key}",
        manifest_fact_key=f"fact/{key}",
    )


def _manifest(
    *,
    end: date,
    accounts: tuple[str, ...],
    instruments: tuple[str, ...] = (),
    fx_rates: tuple[tuple[date, str, Decimal], ...] = (),
    taxonomy: bool = False,
) -> SealedPortfolioDailyManifest:
    rows: dict[str, tuple[dict[str, object], ...]] = {
        table.name: () for table in DEPENDENCY_TABLES
    }
    taxonomy_id = "tax-default" if taxonomy else None
    taxonomy_snapshot: dict[str, object] = {
        "taxonomies": (
            [
                {
                    "taxonomy_id": "tax-default",
                    "primary_assignment_scope": "instrument",
                }
            ]
            if taxonomy
            else []
        ),
        "nodes": (
            [
                {
                    "taxonomy_id": "tax-default",
                    "taxonomy_node_id": "node-risk",
                    "node_name": "Risk Assets",
                    "status": "active",
                }
            ]
            if taxonomy
            else []
        ),
        "assignments": (
            [
                {
                    "taxonomy_id": "tax-default",
                    "target_scope": "instrument",
                    "target_entity_id": "fund",
                    "taxonomy_node_id": "node-risk",
                    "status": "active",
                }
            ]
            if taxonomy
            else []
        ),
    }
    rows["portfolio_daily_config_input"] = (
        {
            "portfolio_id": PORTFOLIO_ID,
            "range_start": START,
            "effective_as_of": end,
            "base_currency": "CNY",
            "taxonomy_id": taxonomy_id,
            "canonical_config": {"taxonomy": taxonomy_snapshot},
        },
    )
    rows["portfolio_daily_account_input"] = tuple(
        {"account_id": account_id, "account_name": account_id.upper()}
        for account_id in accounts
    )
    rows["portfolio_daily_instrument_input"] = tuple(
        {"instrument_id": instrument_id, "instrument_name": instrument_id.upper()}
        for instrument_id in instruments
    )
    rows["portfolio_daily_fx_path"] = tuple(
        {
            "fx_path_id": f"path-{as_of.isoformat()}-{currency}",
            "valuation_date": as_of,
            "from_currency": currency,
            "to_currency": "CNY",
            "resolution_status": "resolved",
            "resolved_rate": rate,
        }
        for as_of, currency, rate in fx_rates
    )
    dependencies = ManifestDependencies(rows)
    return SealedPortfolioDailyManifest(
        run_id=uuid4(),
        manifest_id=uuid4(),
        portfolio_id=PORTFOLIO_ID,
        effective_as_of=end,
        cutoff_at=datetime(2026, 7, 10, tzinfo=UTC),
        captured_generation=1,
        canonical_manifest_hash="0" * 64,
        dependency_counts=dependencies.counts,
        dependencies=dependencies,
    )


def _instrument() -> InstrumentValuationFact:
    return InstrumentValuationFact(
        instrument_id="fund",
        currency="CNY",
        price_unit="currency_per_unit",
        contract_multiplier=D("1"),
        price_factor=D("1"),
        available=True,
    )


def _quote(day: int, price: str) -> QuoteValuationFact:
    return QuoteValuationFact(
        as_of_date=date(2026, 7, day),
        instrument_id="fund",
        currency="CNY",
        status=MarketFactStatus.FRESH,
        price=D(price),
        lineage=_lineage(f"quote-{day}"),
        reason_codes=(),
    )


def _rows(series, *, day: int, axis: str):
    return tuple(
        row
        for row in series.rows
        if row.as_of_date == date(2026, 7, day) and row.axis.value == axis
    )


def _by_key(rows):
    return {row.group_key: row for row in rows}


def test_trade_settlement_cash_and_position_transfers_close_every_axis_exactly() -> (
    None
):
    events = (
        OpeningCashEvent(
            event_id="opening",
            sequence=0,
            lineage=_lineage("opening"),
            effective_date=START,
            account_id="cash-a",
            currency="CNY",
            amount=D("1000"),
        ),
        BuyEvent(
            event_id="buy",
            sequence=1,
            lineage=_lineage("buy"),
            trade_date=date(2026, 7, 2),
            settlement_date=date(2026, 7, 3),
            position_account_id="pos-a",
            cash_account_id="cash-a",
            instrument_id="fund",
            currency="CNY",
            base_currency="CNY",
            quantity=D("10"),
            price=D("50"),
            contract_multiplier=D("1"),
            price_factor=D("1"),
            price_unit="currency_per_unit",
            gross_amount=D("500"),
            consideration_basis="exact_quantity_price",
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
            cost_basis_method=CostBasisMethod.FIFO,
        ),
        CashTransferEvent(
            event_id="cash-transfer",
            sequence=2,
            lineage=_lineage("cash-transfer-source"),
            effective_date=date(2026, 7, 3),
            source_account_id="cash-a",
            destination_account_id="cash-b",
            currency="CNY",
            amount=D("100"),
            counterparty_lineage=_lineage("cash-transfer-destination"),
        ),
        PositionTransferEvent(
            event_id="position-transfer",
            sequence=3,
            lineage=_lineage("position-transfer-source"),
            effective_date=date(2026, 7, 3),
            source_account_id="pos-a",
            destination_account_id="pos-b",
            instrument_id="fund",
            currency="CNY",
            quantity=D("5"),
            declared_local_cost=D("250"),
            counterparty_lineage=_lineage("position-transfer-destination"),
        ),
        SellEvent(
            event_id="sell",
            sequence=4,
            lineage=_lineage("sell"),
            trade_date=date(2026, 7, 4),
            settlement_date=date(2026, 7, 5),
            position_account_id="pos-b",
            cash_account_id="cash-b",
            instrument_id="fund",
            currency="CNY",
            base_currency="CNY",
            quantity=D("2"),
            price=D("70"),
            contract_multiplier=D("1"),
            price_factor=D("1"),
            price_unit="currency_per_unit",
            gross_amount=D("140"),
            consideration_basis="exact_quantity_price",
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
        ),
    )
    ledger = replay_ledger_series(
        events,
        start_date=START,
        end_date=date(2026, 7, 4),
    )
    valuation = calculate_exact_portfolio_valuation(
        ledger,
        DailyValuationBook(
            instruments=(_instrument(),),
            quotes=tuple(
                _quote(day, price)
                for day, price in ((1, "50"), (2, "55"), (3, "60"), (4, "70"))
            ),
            fx_rates=(),
        ),
        base_currency="CNY",
    )
    ambient = getcontext().copy()
    try:
        getcontext().prec = 6
        attribution = calculate_exact_group_attribution(
            _manifest(
                end=date(2026, 7, 4),
                accounts=("cash-a", "cash-b", "pos-a", "pos-b"),
                instruments=("fund",),
                taxonomy=True,
            ),
            ledger,
            valuation,
        )
    finally:
        getcontext().prec = ambient.prec
        getcontext().rounding = ambient.rounding

    first_instrument = _rows(attribution, day=1, axis="instrument")
    assert {row.group_key for row in first_instrument} == {CASH_GROUP_KEY}
    assert all(not row.measured for row in first_instrument)
    assert all(row.group_key != "__unavailable__" for row in attribution.rows)

    day_two = _by_key(_rows(attribution, day=2, axis="instrument"))
    assert day_two[CASH_GROUP_KEY].opening_value == D("1000")
    assert day_two[CASH_GROUP_KEY].closing_value == D("500")
    assert day_two[CASH_GROUP_KEY].internal_flow_out == D("500")
    assert day_two[CASH_GROUP_KEY].economic_pnl == 0
    assert day_two["fund"].closing_value == D("550")
    assert day_two["fund"].internal_flow_in == D("500")
    assert day_two["fund"].economic_pnl == D("50")

    day_three_accounts = _by_key(_rows(attribution, day=3, axis="account"))
    assert day_three_accounts["cash-a"].internal_flow_out == D("100")
    assert day_three_accounts["cash-b"].internal_flow_in == D("100")
    # Transfer phase precedes EOD valuation, so its exact internal movement is
    # the prior closing boundary value (5 units * 55), not the new EOD value.
    assert day_three_accounts["pos-a"].internal_flow_out == D("275")
    assert day_three_accounts["pos-a"].economic_pnl == D("25")
    assert day_three_accounts["pos-b"].internal_flow_in == D("275")
    assert day_three_accounts["pos-b"].economic_pnl == D("25")

    day_four_instrument = _by_key(_rows(attribution, day=4, axis="instrument"))
    assert day_four_instrument["fund"].internal_flow_out == D("140")
    assert day_four_instrument["fund"].economic_pnl == D("100")
    assert day_four_instrument[CASH_GROUP_KEY].internal_flow_in == D("140")
    assert day_four_instrument[CASH_GROUP_KEY].economic_pnl == 0

    taxonomy_rows = _by_key(_rows(attribution, day=4, axis="taxonomy"))
    assert set(taxonomy_rows) == {CASH_GROUP_KEY, "node-risk"}
    assert taxonomy_rows["node-risk"].economic_pnl == D("100")

    for day in range(2, 5):
        portfolio_day = valuation.days[day - 1]
        assert portfolio_day.book_pnl.economic_pnl is not None
        assert portfolio_day.twr.subperiod_twr_method50 is not None
        for axis in ("account", "instrument", "currency", "taxonomy"):
            rows = _rows(attribution, day=day, axis=axis)
            assert rows and all(row.measured for row in rows)
            assert (
                exact_decimal_sum(
                    tuple(
                        row.economic_pnl for row in rows if row.economic_pnl is not None
                    )
                )
                == portfolio_day.book_pnl.economic_pnl
            )
            assert (
                exact_decimal_sum(
                    tuple(_effective_contribution(row) for row in rows)
                )
                == portfolio_day.twr.subperiod_twr_method50
            )
            assert exact_decimal_sum(
                tuple(
                    row.internal_flow_in
                    for row in rows
                    if row.internal_flow_in is not None
                )
            ) == exact_decimal_sum(
                tuple(
                    row.internal_flow_out
                    for row in rows
                    if row.internal_flow_out is not None
                )
            )


def test_same_day_trade_recognition_and_settlement_remain_exactly_attributable() -> (
    None
):
    ledger = replay_ledger_series(
        (
            OpeningCashEvent(
                event_id="opening",
                sequence=0,
                lineage=_lineage("same-day-opening"),
                effective_date=START,
                account_id="cash",
                currency="CNY",
                amount=D("100"),
            ),
            BuyEvent(
                event_id="same-day-buy",
                sequence=1,
                lineage=_lineage("same-day-buy"),
                trade_date=date(2026, 7, 2),
                settlement_date=date(2026, 7, 2),
                position_account_id="position",
                cash_account_id="cash",
                instrument_id="fund",
                currency="CNY",
                base_currency="CNY",
                quantity=D("1"),
                price=D("50"),
                contract_multiplier=D("1"),
                price_factor=D("1"),
                price_unit="currency_per_unit",
                gross_amount=D("50"),
                consideration_basis="exact_quantity_price",
                local_to_base_rate=D("1"),
                local_to_base_lineage=None,
                cost_basis_method=CostBasisMethod.FIFO,
            ),
        ),
        start_date=START,
        end_date=date(2026, 7, 2),
    )
    valuation = calculate_exact_portfolio_valuation(
        ledger,
        DailyValuationBook(
            instruments=(_instrument(),),
            quotes=(_quote(2, "55"),),
            fx_rates=(),
        ),
        base_currency="CNY",
    )
    attribution = calculate_exact_group_attribution(
        _manifest(
            end=date(2026, 7, 2),
            accounts=("cash", "position"),
            instruments=("fund",),
        ),
        ledger,
        valuation,
    )

    instrument = _by_key(_rows(attribution, day=2, axis="instrument"))
    assert instrument[CASH_GROUP_KEY].opening_value == D("100")
    assert instrument[CASH_GROUP_KEY].closing_value == D("50")
    assert instrument[CASH_GROUP_KEY].internal_flow_out == D("50")
    assert instrument[CASH_GROUP_KEY].economic_pnl == 0
    assert instrument["fund"].closing_value == D("55")
    assert instrument["fund"].internal_flow_in == D("50")
    assert instrument["fund"].economic_pnl == D("5")
    assert (
        exact_decimal_sum(
            tuple(_effective_contribution(row) for row in instrument.values())
        )
        == valuation.days[1].twr.subperiod_twr_method50
    )


def test_fx_conversion_uses_equal_internal_base_movement_and_assigns_terms_pnl() -> (
    None
):
    conversion = FxConversionEvent(
        event_id="fx-conversion",
        sequence=1,
        lineage=_lineage("fx-conversion"),
        trade_date=date(2026, 7, 2),
        settlement_date=date(2026, 7, 3),
        source_account_id="usd-cash",
        target_account_id="cny-cash",
        source_currency="USD",
        target_currency="CNY",
        source_amount=D("10"),
        target_amount=D("71"),
        effective_fx_rate=D("7.1"),
        rate_convention=FxRateConvention.TARGET_PER_SOURCE,
        base_currency="CNY",
        source_to_base_rate=D("7"),
        target_to_base_rate=D("1"),
        source_to_base_lineage=_lineage("source-fx"),
        target_to_base_lineage=None,
    )
    ledger = replay_ledger_series(
        (
            OpeningCashEvent(
                event_id="opening-usd",
                sequence=0,
                lineage=_lineage("opening-usd"),
                effective_date=START,
                account_id="usd-cash",
                currency="USD",
                amount=D("100"),
            ),
            conversion,
        ),
        start_date=START,
        end_date=date(2026, 7, 2),
    )
    valuation = calculate_exact_portfolio_valuation(
        ledger,
        DailyValuationBook(
            instruments=(),
            quotes=(),
            fx_rates=tuple(
                FxValuationFact(
                    as_of_date=date(2026, 7, day),
                    from_currency="USD",
                    to_currency="CNY",
                    status=MarketFactStatus.FRESH,
                    rate=D("7"),
                    lineages=(_lineage(f"usd-fx-{day}"),),
                    reason_codes=(),
                )
                for day in (1, 2)
            ),
        ),
        base_currency="CNY",
    )
    attribution = calculate_exact_group_attribution(
        _manifest(
            end=date(2026, 7, 2),
            accounts=("usd-cash", "cny-cash"),
            fx_rates=(
                (date(2026, 7, 1), "USD", D("7")),
                (date(2026, 7, 2), "USD", D("7")),
            ),
        ),
        ledger,
        valuation,
    )
    currency = _by_key(_rows(attribution, day=2, axis="currency"))
    assert currency["USD"].opening_value == D("700")
    assert currency["USD"].closing_value == D("630")
    assert currency["USD"].internal_flow_out == D("70")
    assert currency["USD"].economic_pnl == 0
    assert currency["CNY"].closing_value == D("71")
    assert currency["CNY"].internal_flow_in == D("70")
    assert currency["CNY"].economic_pnl == D("1")
    assert (
        exact_decimal_sum(
            tuple(
                row.economic_pnl
                for row in currency.values()
                if row.economic_pnl is not None
            )
        )
        == valuation.days[1].book_pnl.economic_pnl
    )


def test_income_expense_and_return_of_capital_are_owned_by_instrument_not_cash() -> (
    None
):
    buy = BuyEvent(
        event_id="buy",
        sequence=1,
        lineage=_lineage("buy"),
        trade_date=START,
        settlement_date=START,
        position_account_id="position",
        cash_account_id="cash",
        instrument_id="fund",
        currency="CNY",
        base_currency="CNY",
        quantity=D("10"),
        price=D("10"),
        contract_multiplier=D("1"),
        price_factor=D("1"),
        price_unit="currency_per_unit",
        gross_amount=D("100"),
        consideration_basis="exact_quantity_price",
        local_to_base_rate=D("1"),
        local_to_base_lineage=None,
        cost_basis_method=CostBasisMethod.FIFO,
    )
    events = (
        OpeningCashEvent(
            event_id="opening",
            sequence=0,
            lineage=_lineage("opening"),
            effective_date=START,
            account_id="cash",
            currency="CNY",
            amount=D("1000"),
        ),
        buy,
        IncomeEvent(
            event_id="income",
            sequence=2,
            lineage=_lineage("income"),
            recognition_date=date(2026, 7, 2),
            settlement_date=date(2026, 7, 3),
            cash_account_id="cash",
            instrument_id="fund",
            currency="CNY",
            base_currency="CNY",
            kind=IncomeKind.DIVIDEND,
            gross_amount=D("10"),
            fees=D("2"),
            taxes=D("1"),
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
        ),
        ExpenseEvent(
            event_id="expense",
            sequence=3,
            lineage=_lineage("expense"),
            recognition_date=date(2026, 7, 2),
            settlement_date=date(2026, 7, 3),
            cash_account_id="cash",
            instrument_id="fund",
            currency="CNY",
            base_currency="CNY",
            kind=ExpenseKind.FEE,
            amount=D("3"),
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
        ),
        ReturnOfCapitalEvent(
            event_id="roc",
            sequence=4,
            lineage=_lineage("roc"),
            recognition_date=date(2026, 7, 2),
            settlement_date=date(2026, 7, 3),
            position_account_id="position",
            cash_account_id="cash",
            instrument_id="fund",
            currency="CNY",
            base_currency="CNY",
            gross_amount=D("20"),
            fees=D("1"),
            taxes=D("1"),
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
        ),
    )
    ledger = replay_ledger_series(
        events,
        start_date=START,
        end_date=date(2026, 7, 2),
    )
    valuation = calculate_exact_portfolio_valuation(
        ledger,
        DailyValuationBook(
            instruments=(_instrument(),),
            quotes=(_quote(1, "10"), _quote(2, "10")),
            fx_rates=(),
        ),
        base_currency="CNY",
    )
    attribution = calculate_exact_group_attribution(
        _manifest(
            end=date(2026, 7, 2),
            accounts=("cash", "position"),
            instruments=("fund",),
            taxonomy=True,
        ),
        ledger,
        valuation,
    )
    instrument = _by_key(_rows(attribution, day=2, axis="instrument"))
    assert instrument[CASH_GROUP_KEY].closing_value == D("922")
    assert instrument[CASH_GROUP_KEY].internal_flow_in == D("30")
    assert instrument[CASH_GROUP_KEY].internal_flow_out == D("8")
    assert instrument[CASH_GROUP_KEY].economic_pnl == 0
    assert instrument["fund"].closing_value == D("100")
    assert instrument["fund"].internal_flow_in == D("8")
    assert instrument["fund"].internal_flow_out == D("30")
    assert instrument["fund"].economic_pnl == D("22")
    assert valuation.days[1].book_pnl.economic_pnl == D("22")


def test_external_flow_is_attributed_without_becoming_group_pnl() -> None:
    ledger = replay_ledger_series(
        (
            OpeningCashEvent(
                event_id="opening",
                sequence=0,
                lineage=_lineage("opening"),
                effective_date=START,
                account_id="cash-a",
                currency="CNY",
                amount=D("100"),
            ),
            ExternalCashFlowEvent(
                event_id="deposit",
                sequence=1,
                lineage=_lineage("deposit"),
                value_date=date(2026, 7, 2),
                account_id="cash-b",
                currency="CNY",
                base_currency="CNY",
                kind=ExternalFlowKind.DEPOSIT,
                timing=ExternalFlowTiming.BEGINNING_OF_DAY,
                amount=D("50"),
                local_to_base_rate=D("1"),
                local_to_base_lineage=None,
            ),
        ),
        start_date=START,
        end_date=date(2026, 7, 2),
    )
    valuation = calculate_exact_portfolio_valuation(
        ledger,
        DailyValuationBook(instruments=(), quotes=(), fx_rates=()),
        base_currency="CNY",
    )
    attribution = calculate_exact_group_attribution(
        _manifest(
            end=date(2026, 7, 2),
            accounts=("cash-a", "cash-b"),
        ),
        ledger,
        valuation,
    )
    accounts = _by_key(_rows(attribution, day=2, axis="account"))
    assert accounts["cash-b"].opening_value == 0
    assert accounts["cash-b"].closing_value == D("50")
    assert accounts["cash-b"].external_flow_in == D("50")
    assert accounts["cash-b"].economic_pnl == 0
    assert (
        exact_decimal_sum(
            tuple(
                row.external_flow_in
                for row in accounts.values()
                if row.external_flow_in is not None
            )
        )
        == valuation.days[1].external_flow_in
    )


def test_precision50_group_division_residual_is_explicit_and_exactly_closes() -> None:
    def buy(*, event_id: str, sequence: int, instrument_id: str) -> BuyEvent:
        return BuyEvent(
            event_id=event_id,
            sequence=sequence,
            lineage=_lineage(event_id),
            trade_date=date(2026, 7, 1),
            settlement_date=date(2026, 7, 1),
            position_account_id=f"pos-{instrument_id}",
            cash_account_id="cash-a",
            instrument_id=instrument_id,
            currency="CNY",
            base_currency="CNY",
            quantity=D("1"),
            price=D("1"),
            contract_multiplier=D("1"),
            price_factor=D("1"),
            price_unit="currency_per_unit",
            gross_amount=D("1"),
            consideration_basis="exact_quantity_price",
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
            cost_basis_method=CostBasisMethod.FIFO,
        )

    events = (
        OpeningCashEvent(
            event_id="opening",
            sequence=0,
            lineage=_lineage("opening"),
            effective_date=START,
            account_id="cash-a",
            currency="CNY",
            amount=D("3"),
        ),
        buy(event_id="buy-a", sequence=1, instrument_id="fund-a"),
        buy(event_id="buy-b", sequence=2, instrument_id="fund-b"),
    )
    ledger = replay_ledger_series(
        events,
        start_date=START,
        end_date=date(2026, 7, 2),
    )
    valuation = calculate_exact_portfolio_valuation(
        ledger,
        DailyValuationBook(
            instruments=tuple(
                InstrumentValuationFact(
                    instrument_id=instrument_id,
                    currency="CNY",
                    price_unit="currency_per_unit",
                    contract_multiplier=D("1"),
                    price_factor=D("1"),
                    available=True,
                )
                for instrument_id in ("fund-a", "fund-b")
            ),
            quotes=tuple(
                QuoteValuationFact(
                    as_of_date=date(2026, 7, day),
                    instrument_id=instrument_id,
                    currency="CNY",
                    status=MarketFactStatus.FRESH,
                    price=D(price),
                    lineage=_lineage(f"quote-{instrument_id}-{day}"),
                    reason_codes=(),
                )
                for day, price in ((1, "1"), (2, "2"))
                for instrument_id in ("fund-a", "fund-b")
            ),
            fx_rates=(),
        ),
        base_currency="CNY",
    )
    attribution = calculate_exact_group_attribution(
        _manifest(
            end=date(2026, 7, 2),
            accounts=("cash-a", "pos-fund-a", "pos-fund-b"),
            instruments=("fund-a", "fund-b"),
        ),
        ledger,
        valuation,
    )
    rows = _rows(attribution, day=2, axis="instrument")
    assert (
        exact_decimal_sum(
            tuple(_effective_contribution(row) for row in rows)
        )
        == valuation.days[1].twr.subperiod_twr_method50
    )
    raw_adjustment = exact_decimal_sum(
        tuple(row.contribution_division_adjustment_exact for row in rows)
    )
    assert raw_adjustment != 0
    assert sum(row.contribution_division_adjustment_exact != 0 for row in rows) == 1
    assert all(row.closure_residual_exact == 0 for row in rows)


def _offsetting_group_numeric_domain_case(*, group_pnl: Decimal):
    """Build a valid near-zero NAV with large, exactly offsetting group P&L."""

    instrument_ids = ("fund-a", "fund-b")
    opening_prices = {
        "fund-a": D("1000000000000000000000000"),
        "fund-b": D("1000000000000000000000001"),
    }

    def buy(instrument_id: str, sequence: int) -> BuyEvent:
        price = opening_prices[instrument_id]
        return BuyEvent(
            event_id=f"domain-buy-{instrument_id}",
            sequence=sequence,
            lineage=_lineage(f"domain-buy-{instrument_id}"),
            trade_date=START,
            settlement_date=START,
            position_account_id=f"pos-{instrument_id}",
            cash_account_id="cash",
            instrument_id=instrument_id,
            currency="CNY",
            base_currency="CNY",
            quantity=D("1"),
            price=price,
            contract_multiplier=D("1"),
            price_factor=D("1"),
            price_unit="currency_per_unit",
            gross_amount=price,
            consideration_basis="exact_quantity_price",
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
            cost_basis_method=CostBasisMethod.FIFO,
        )

    ledger = replay_ledger_series(
        (
            OpeningCashEvent(
                event_id="domain-opening",
                sequence=0,
                lineage=_lineage("domain-opening"),
                effective_date=START,
                account_id="cash",
                currency="CNY",
                amount=D("0.00000001"),
            ),
            buy("fund-a", 1),
            buy("fund-b", 2),
        ),
        start_date=START,
        end_date=date(2026, 7, 2),
    )
    closing_prices = {
        "fund-a": exact_decimal_sum((opening_prices["fund-a"], group_pnl)),
        "fund-b": exact_decimal_subtract(opening_prices["fund-b"], group_pnl),
    }
    valuation = calculate_exact_portfolio_valuation(
        ledger,
        DailyValuationBook(
            instruments=tuple(
                InstrumentValuationFact(
                    instrument_id=instrument_id,
                    currency="CNY",
                    price_unit="currency_per_unit",
                    contract_multiplier=D("1"),
                    price_factor=D("1"),
                    available=True,
                )
                for instrument_id in instrument_ids
            ),
            quotes=tuple(
                QuoteValuationFact(
                    as_of_date=date(2026, 7, day),
                    instrument_id=instrument_id,
                    currency="CNY",
                    status=MarketFactStatus.FRESH,
                    price=(
                        opening_prices[instrument_id]
                        if day == 1
                        else closing_prices[instrument_id]
                    ),
                    lineage=_lineage(f"domain-quote-{instrument_id}-{day}"),
                    reason_codes=(),
                )
                for day in (1, 2)
                for instrument_id in instrument_ids
            ),
            fx_rates=(),
        ),
        base_currency="CNY",
    )
    manifest = _manifest(
        end=date(2026, 7, 2),
        accounts=("cash", "pos-fund-a", "pos-fund-b"),
        instruments=instrument_ids,
    )
    return (
        manifest,
        ledger,
        valuation,
        calculate_exact_group_attribution(manifest, ledger, valuation),
    )


def test_group_contribution_at_32_integer_digit_boundary_remains_measured() -> None:
    group_pnl = D("999999999999999999999999.99999999")
    _, _, valuation, attribution = _offsetting_group_numeric_domain_case(
        group_pnl=group_pnl
    )

    endpoint = valuation.days[1]
    assert endpoint.closing_nav == D("0.00000001")
    assert endpoint.twr.subperiod_twr_method50 == 0
    expected = D("99999999999999999999999999999999")
    instruments = _by_key(_rows(attribution, day=2, axis="instrument"))
    assert all(row.measured for row in instruments.values())
    assert instruments["fund-a"].contribution_method50 == expected
    assert (
        instruments["fund-b"].contribution_method50
        == exact_decimal_negate(expected)
    )
    assert all(
        row.measured
        for axis in ("account", "instrument", "currency", "taxonomy")
        for row in _rows(attribution, day=2, axis=axis)
    )


def test_group_contribution_outside_method_domain_unavailable_per_axis_only() -> None:
    _, _, valuation, attribution = _offsetting_group_numeric_domain_case(
        group_pnl=D("1000000000000000000000000")
    )

    endpoint = valuation.days[1]
    assert endpoint.closing_nav == D("0.00000001")
    assert endpoint.twr.subperiod_twr_method50 == 0
    assert endpoint.twr.cumulative_twr_method50 == 0
    for axis in ("account", "instrument"):
        rows = _rows(attribution, day=2, axis=axis)
        assert rows and all(not row.measured for row in rows)
        assert all(
            row.reason_codes == ("group_numeric_domain_unavailable",)
            for row in rows
        )
        assert all(row.contribution_method50 is None for row in rows)
    for axis in ("currency", "taxonomy"):
        rows = _rows(attribution, day=2, axis=axis)
        assert rows and all(row.measured for row in rows)

    published = [
        _portfolio_contribution_row(
            portfolio_id=PORTFOLIO_ID,
            day=endpoint,
            anchor=valuation.days[0],
            period_days=(endpoint,),
        )
    ]
    for axis in ("account", "instrument", "currency", "taxonomy"):
        published.extend(
            _group_contribution_rows(
                portfolio_id=PORTFOLIO_ID,
                rows=_rows(attribution, day=2, axis=axis),
            )
        )
    _validate_contribution_publication(published)

    corrupted = [dict(row) for row in published]
    next(row for row in corrupted if row["axis"] == "account")["measured"] = True
    with pytest.raises(
        PortfolioDailyOutputBuildError,
        match="account cannot mix measured and unavailable groups",
    ):
        _validate_contribution_publication(corrupted)


def _mixed_frequency_case(*, gap_external_flow: bool):
    def instrument(instrument_id: str) -> InstrumentValuationFact:
        return InstrumentValuationFact(
            instrument_id=instrument_id,
            currency="CNY",
            price_unit="currency_per_unit",
            contract_multiplier=D("1"),
            price_factor=D("1"),
            available=True,
        )

    def quote(
        instrument_id: str,
        day: int,
        price: str,
        status: MarketFactStatus,
    ) -> QuoteValuationFact:
        return QuoteValuationFact(
            as_of_date=date(2026, 7, day),
            instrument_id=instrument_id,
            currency="CNY",
            status=status,
            price=D(price),
            lineage=_lineage(f"mixed-{instrument_id}-{day}"),
            reason_codes=(
                ("monthly_fund_nav_carried",)
                if status is MarketFactStatus.CARRY_FORWARD
                else ()
            ),
        )

    events = [
        OpeningCashEvent(
            event_id="mixed-opening",
            sequence=0,
            lineage=_lineage("mixed-opening"),
            effective_date=START,
            account_id="cash-a",
            currency="CNY",
            amount=D("2000"),
        ),
        BuyEvent(
            event_id="mixed-buy-fund",
            sequence=1,
            lineage=_lineage("mixed-buy-fund"),
            trade_date=START,
            settlement_date=START,
            position_account_id="pos-fund-a",
            cash_account_id="cash-a",
            instrument_id="fund",
            currency="CNY",
            base_currency="CNY",
            quantity=D("10"),
            price=D("50"),
            contract_multiplier=D("1"),
            price_factor=D("1"),
            price_unit="currency_per_unit",
            gross_amount=D("500"),
            consideration_basis="exact_quantity_price",
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
            cost_basis_method=CostBasisMethod.FIFO,
        ),
        BuyEvent(
            event_id="mixed-buy-etf-opening",
            sequence=2,
            lineage=_lineage("mixed-buy-etf-opening"),
            trade_date=START,
            settlement_date=START,
            position_account_id="pos-etf",
            cash_account_id="cash-a",
            instrument_id="etf",
            currency="CNY",
            base_currency="CNY",
            quantity=D("20"),
            price=D("10"),
            contract_multiplier=D("1"),
            price_factor=D("1"),
            price_unit="currency_per_unit",
            gross_amount=D("200"),
            consideration_basis="exact_quantity_price",
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
            cost_basis_method=CostBasisMethod.FIFO,
        ),
        BuyEvent(
            event_id="mixed-gap-buy-etf",
            sequence=3,
            lineage=_lineage("mixed-gap-buy-etf"),
            trade_date=date(2026, 7, 2),
            settlement_date=date(2026, 7, 2),
            position_account_id="pos-etf",
            cash_account_id="cash-a",
            instrument_id="etf",
            currency="CNY",
            base_currency="CNY",
            quantity=D("5"),
            price=D("12"),
            contract_multiplier=D("1"),
            price_factor=D("1"),
            price_unit="currency_per_unit",
            gross_amount=D("60"),
            consideration_basis="exact_quantity_price",
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
            cost_basis_method=CostBasisMethod.FIFO,
        ),
        IncomeEvent(
            event_id="mixed-gap-dividend",
            sequence=4,
            lineage=_lineage("mixed-gap-dividend"),
            recognition_date=date(2026, 7, 2),
            settlement_date=date(2026, 7, 3),
            cash_account_id="cash-a",
            instrument_id="fund",
            currency="CNY",
            base_currency="CNY",
            kind=IncomeKind.DIVIDEND,
            gross_amount=D("12"),
            fees=D("2"),
            taxes=D("1"),
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
        ),
        SellEvent(
            event_id="mixed-gap-sell-etf",
            sequence=5,
            lineage=_lineage("mixed-gap-sell-etf"),
            trade_date=date(2026, 7, 3),
            settlement_date=date(2026, 7, 3),
            position_account_id="pos-etf",
            cash_account_id="cash-a",
            instrument_id="etf",
            currency="CNY",
            base_currency="CNY",
            quantity=D("4"),
            price=D("13"),
            contract_multiplier=D("1"),
            price_factor=D("1"),
            price_unit="currency_per_unit",
            gross_amount=D("52"),
            consideration_basis="exact_quantity_price",
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
        ),
        CashTransferEvent(
            event_id="mixed-gap-cash-transfer",
            sequence=6,
            lineage=_lineage("mixed-gap-cash-transfer-source"),
            effective_date=date(2026, 7, 3),
            source_account_id="cash-a",
            destination_account_id="cash-b",
            currency="CNY",
            amount=D("100"),
            counterparty_lineage=_lineage("mixed-gap-cash-transfer-destination"),
        ),
        PositionTransferEvent(
            event_id="mixed-gap-position-transfer",
            sequence=7,
            lineage=_lineage("mixed-gap-position-transfer-source"),
            effective_date=date(2026, 7, 3),
            source_account_id="pos-fund-a",
            destination_account_id="pos-fund-b",
            instrument_id="fund",
            currency="CNY",
            quantity=D("2"),
            declared_local_cost=D("100"),
            counterparty_lineage=_lineage("mixed-gap-position-transfer-destination"),
        ),
        ExpenseEvent(
            event_id="mixed-gap-fee",
            sequence=8,
            lineage=_lineage("mixed-gap-fee"),
            recognition_date=date(2026, 7, 3),
            settlement_date=date(2026, 7, 3),
            cash_account_id="cash-a",
            instrument_id="fund",
            currency="CNY",
            base_currency="CNY",
            kind=ExpenseKind.FEE,
            amount=D("3"),
            local_to_base_rate=D("1"),
            local_to_base_lineage=None,
        ),
    ]
    if gap_external_flow:
        events.append(
            ExternalCashFlowEvent(
                event_id="mixed-gap-deposit",
                sequence=9,
                lineage=_lineage("mixed-gap-deposit"),
                value_date=date(2026, 7, 2),
                account_id="cash-b",
                currency="CNY",
                base_currency="CNY",
                kind=ExternalFlowKind.DEPOSIT,
                timing=ExternalFlowTiming.BEGINNING_OF_DAY,
                amount=D("25"),
                local_to_base_rate=D("1"),
                local_to_base_lineage=None,
            )
        )
    else:
        events.append(
            ExternalCashFlowEvent(
                event_id="mixed-endpoint-withdrawal",
                sequence=9,
                lineage=_lineage("mixed-endpoint-withdrawal"),
                value_date=date(2026, 7, 4),
                account_id="cash-b",
                currency="CNY",
                base_currency="CNY",
                kind=ExternalFlowKind.WITHDRAWAL,
                timing=ExternalFlowTiming.END_OF_DAY,
                amount=D("25"),
                local_to_base_rate=D("1"),
                local_to_base_lineage=None,
            )
        )
    ledger = replay_ledger_series(
        tuple(events),
        start_date=START,
        end_date=date(2026, 7, 6),
    )
    valuation = calculate_exact_portfolio_valuation(
        ledger,
        DailyValuationBook(
            instruments=(instrument("etf"), instrument("fund")),
            quotes=(
                quote("etf", 1, "10", MarketFactStatus.FRESH),
                quote("fund", 1, "50", MarketFactStatus.FRESH),
                quote("etf", 2, "12", MarketFactStatus.FRESH),
                quote("fund", 2, "50", MarketFactStatus.CARRY_FORWARD),
                quote("etf", 3, "13", MarketFactStatus.FRESH),
                quote("fund", 3, "50", MarketFactStatus.CARRY_FORWARD),
                quote("etf", 4, "14", MarketFactStatus.FRESH),
                quote("fund", 4, "55", MarketFactStatus.FRESH),
                quote("etf", 5, "15", MarketFactStatus.FRESH),
                quote("fund", 5, "55", MarketFactStatus.CARRY_FORWARD),
                quote("etf", 6, "16", MarketFactStatus.FRESH),
                quote("fund", 6, "56", MarketFactStatus.FRESH),
            ),
            fx_rates=(),
        ),
        base_currency="CNY",
    )
    manifest = _manifest(
        end=date(2026, 7, 6),
        accounts=("cash-a", "cash-b", "pos-etf", "pos-fund-a", "pos-fund-b"),
        instruments=("etf", "fund"),
        taxonomy=True,
    )
    return manifest, ledger, valuation


def test_mixed_frequency_gap_uses_one_anchor_window_for_every_axis() -> None:
    manifest, ledger, valuation = _mixed_frequency_case(gap_external_flow=False)
    assert [day.twr.status.value for day in valuation.days] == [
        "reanchored",
        "no_new_valuation",
        "no_new_valuation",
        "calculated",
        "no_new_valuation",
        "calculated",
    ]
    endpoint = valuation.days[3]
    assert endpoint.twr.return_period_start_date == START
    assert endpoint.twr.return_period_day_count == 3
    assert endpoint.external_flow_out == D("25")

    with localcontext() as context:
        context.prec = 7
        context.rounding = ROUND_DOWN
        low_precision = calculate_exact_group_attribution(
            manifest,
            ledger,
            valuation,
        )
    with localcontext() as context:
        context.prec = 80
        context.rounding = ROUND_UP
        high_precision = calculate_exact_group_attribution(
            manifest,
            ledger,
            valuation,
        )
    assert low_precision == high_precision
    attribution = low_precision

    period_economic_pnl = exact_decimal_sum(
        tuple(
            day.book_pnl.economic_pnl
            for day in valuation.days[1:4]
            if day.book_pnl.economic_pnl is not None
        )
    )
    for axis in ("account", "instrument", "currency", "taxonomy"):
        rows = _rows(attribution, day=4, axis=axis)
        assert rows and all(row.measured for row in rows)
        assert (
            exact_decimal_sum(
                tuple(
                    row.opening_value for row in rows if row.opening_value is not None
                )
            )
            == valuation.days[0].closing_nav
        )
        assert (
            exact_decimal_sum(
                tuple(
                    row.closing_value for row in rows if row.closing_value is not None
                )
            )
            == endpoint.closing_nav
        )
        assert (
            exact_decimal_sum(
                tuple(
                    row.external_flow_in
                    for row in rows
                    if row.external_flow_in is not None
                )
            )
            == 0
        )
        assert exact_decimal_sum(
            tuple(
                row.external_flow_out
                for row in rows
                if row.external_flow_out is not None
            )
        ) == D("25")
        assert (
            exact_decimal_sum(
                tuple(row.economic_pnl for row in rows if row.economic_pnl is not None)
            )
            == period_economic_pnl
        )
        assert (
            exact_decimal_sum(
                tuple(_effective_contribution(row) for row in rows)
            )
            == endpoint.twr.subperiod_twr_method50
        )
        assert exact_decimal_sum(
            tuple(
                row.internal_flow_in for row in rows if row.internal_flow_in is not None
            )
        ) == exact_decimal_sum(
            tuple(
                row.internal_flow_out
                for row in rows
                if row.internal_flow_out is not None
            )
        )
        assert all(row.closure_residual_exact == 0 for row in rows)

    instruments = _by_key(_rows(attribution, day=4, axis="instrument"))
    assert instruments["etf"].internal_flow_in == D("60")
    assert instruments["etf"].internal_flow_out == D("52")
    assert instruments["fund"].internal_flow_in == D("6")
    assert instruments["fund"].internal_flow_out == D("12")
    accounts = _by_key(_rows(attribution, day=4, axis="account"))
    assert accounts["pos-fund-a"].internal_flow_out == D("100")
    assert accounts["pos-fund-b"].internal_flow_in == D("100")
    assert accounts["pos-fund-b"].opening_value == 0

    second_endpoint = valuation.days[5]
    assert second_endpoint.twr.return_period_start_date == date(2026, 7, 4)
    assert second_endpoint.twr.return_period_day_count == 2
    for axis in ("account", "instrument", "currency", "taxonomy"):
        first = _by_key(_rows(attribution, day=4, axis=axis))
        second = _by_key(_rows(attribution, day=6, axis=axis))
        assert first and second and all(row.measured for row in second.values())
        for key in set(first) | set(second):
            assert (first[key].closing_value if key in first else D("0")) == (
                second[key].opening_value if key in second else D("0")
            )
        assert (
            exact_decimal_sum(
                tuple(_effective_contribution(row) for row in second.values())
            )
            == second_endpoint.twr.subperiod_twr_method50
        )

    published = [
        _portfolio_contribution_row(
            portfolio_id=PORTFOLIO_ID,
            day=endpoint,
            anchor=valuation.days[0],
            period_days=valuation.days[1:4],
        )
    ]
    for axis in ("account", "instrument", "currency", "taxonomy"):
        published.extend(
            _group_contribution_rows(
                portfolio_id=PORTFOLIO_ID,
                rows=_rows(attribution, day=4, axis=axis),
            )
        )
    _validate_contribution_publication(published)


def test_external_flow_inside_mixed_frequency_gap_breaks_attribution() -> None:
    manifest, ledger, valuation = _mixed_frequency_case(gap_external_flow=True)
    assert valuation.days[1].twr.status.value == "broken"
    assert {reason.value for reason in valuation.days[1].twr.reason_codes} == {
        "carry_forward_valuation",
        "gap_external_flow",
    }
    assert valuation.days[3].twr.status.value == "reanchored"
    assert valuation.days[-1].twr.status.value == "calculated"

    attribution = calculate_exact_group_attribution(manifest, ledger, valuation)
    for axis in ("account", "instrument", "currency", "taxonomy"):
        rows = _rows(attribution, day=4, axis=axis)
        assert rows and all(not row.measured for row in rows)
        assert all(
            "group_return_period_unavailable" in row.reason_codes for row in rows
        )
