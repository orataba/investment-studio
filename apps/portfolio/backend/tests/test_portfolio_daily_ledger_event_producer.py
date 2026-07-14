from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, time
from decimal import Decimal, getcontext
import random
from uuid import UUID, uuid4

import pytest

from portfolio_app.calculations.numeric import canonical_sha256_hex
from portfolio_app.calculations.portfolio_daily.constants import (
    ACCOUNT_SCHEMA_VERSION,
    CONFIG_SCHEMA_VERSION,
    CORPORATE_ACTION_SCHEMA_VERSION,
    FX_CONSUMER_POLICY_VERSION,
    FX_RATE_MATH_PRECISION,
    FX_RATE_ROUNDING_MODE,
    FX_RESOLVER_STRATEGY_VERSION,
    INSTRUMENT_SCHEMA_VERSION,
    QUOTE_FRESHNESS_POLICY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
)
from portfolio_app.calculations.portfolio_daily.hashing import canonical_storage_json
from portfolio_app.calculations.portfolio_daily.ledger import (
    BuyEvent,
    CashTransferEvent,
    DividendReinvestmentEvent,
    ExpenseEvent,
    ExternalCashFlowEvent,
    FxConversionEvent,
    IncomeEvent,
    MaturityRedemptionEvent,
    OpeningCashEvent,
    OpeningPositionEvent,
    PositionTransferEvent,
    ReturnOfCapitalEvent,
    SellEvent,
    SplitEvent,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer import (
    build_ledger_events,
    build_transaction_replay_events,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_contracts import (
    LedgerEventBuildError,
    LedgerEventBuildErrorCode,
    LedgerEventDiagnosticCode,
)
from portfolio_app.calculations.portfolio_daily.manifest_repository import (
    DEPENDENCY_NATURAL_KEYS,
)
from portfolio_app.services.transaction_revisions import (
    TransactionFactPayload,
    transaction_payload_hash,
)


pytestmark = pytest.mark.no_database

D = Decimal
MANIFEST_ID = UUID("00000000-0000-0000-0000-000000000101")
RUN_ID = UUID("00000000-0000-0000-0000-000000000102")
CAPTURED_AT = datetime(2026, 7, 14, 12, 1, tzinfo=UTC)
CUTOFF = datetime(2026, 7, 14, 12, tzinfo=UTC)
AS_OF = date(2026, 7, 14)


def _identity(row: dict[str, object]) -> dict[str, object]:
    return {
        "manifest_id": MANIFEST_ID,
        "run_id": RUN_ID,
        "portfolio_id": "portfolio-a",
        "captured_at": CAPTURED_AT,
        **row,
    }


def _config() -> dict[str, object]:
    canonical = canonical_storage_json(
        {"portfolio_id": "portfolio-a", "base_currency": "USD"}
    )
    return _identity(
        {
            "effective_as_of": AS_OF,
            "range_start": AS_OF,
            "knowledge_cutoff_at": CUTOFF,
            "base_currency": "USD",
            "config_schema_version": CONFIG_SCHEMA_VERSION,
            "config_hash": canonical_sha256_hex(canonical),
            "canonical_config": canonical,
        }
    )


def _account(
    account_id: str,
    *,
    account_type: str,
    currency: str,
    default_cash: str | None = None,
    cost_method: str | None = None,
) -> dict[str, object]:
    source = {
        "account_id": account_id,
        "portfolio_id": "portfolio-a",
        "account_name": account_id,
        "account_type": account_type,
        "currency": currency,
        "institution": None,
        "default_settlement_cash_account_id": default_cash,
        "cost_basis_method": cost_method,
        "allowed_instrument_types_json": None,
        "opened_at": date(2020, 1, 1),
        "closed_at": None,
        "status": "active",
    }
    canonical = canonical_storage_json(source)
    return _identity(
        {
            "account_id": account_id,
            "account_name": account_id,
            "account_type": account_type,
            "currency": currency,
            "institution": None,
            "default_settlement_cash_account_id": default_cash,
            "cost_basis_method": cost_method,
            "opened_at": date(2020, 1, 1),
            "closed_at": None,
            "account_status": "active",
            "account_schema_version": ACCOUNT_SCHEMA_VERSION,
            "account_hash": canonical_sha256_hex(canonical),
            "canonical_account": canonical,
        }
    )


def _instrument(*, currency: str = "USD") -> dict[str, object]:
    contract = {
        "price_unit": "per_unit",
        "contract_multiplier": D("1"),
        "price_factor": D("1"),
        "accrual_convention": None,
        "state": "available",
        "reason_codes": [],
        "source": "methodology",
    }
    canonical = canonical_storage_json(
        {
            "instrument_id": "fund-a",
            "instrument_name": "Fund A",
            "instrument_type": "fund",
            "currency": currency,
            "identifiers": [],
            "valuation_contract": contract,
        }
    )
    return _identity(
        {
            "instrument_id": "fund-a",
            "requires_valuation": True,
            "instrument_name": "Fund A",
            "instrument_type": "fund",
            "currency": currency,
            "price_unit": "per_unit",
            "contract_multiplier": D("1"),
            "price_factor": D("1"),
            "valuation_contract_state": "available",
            "valuation_contract_reason_codes": [],
            "valuation_factor_source": "methodology",
            "instrument_schema_version": INSTRUMENT_SCHEMA_VERSION,
            "instrument_hash": canonical_sha256_hex(canonical),
            "canonical_instrument": canonical,
        }
    )


def _instrument_snapshot(*, currency: str = "USD") -> dict[str, object]:
    return {
        "instrument_id": "fund-a",
        "instrument_name": "Fund A",
        "instrument_type": "fund",
        "currency": currency,
    }


def _transaction(
    transaction_id: str,
    transaction_type: str,
    *,
    minute: int,
    account_id: str,
    gross: str,
    instrument: bool = False,
    settlement_cash: str | None = None,
    quantity: str | None = None,
    price: str | None = None,
    counter_amount: str | None = None,
    quoted_fx_rate: str | None = None,
    currency: str = "USD",
    entitlement_date: date | None = None,
    acquisition_date: date | None = None,
    fees: str = "0",
    taxes: str = "0",
    transfer_object_type: str | None = None,
    transfer_group_id: str | None = None,
    counterparty_account_id: str | None = None,
    revision_group_id: str | None = None,
    group_recorded_at: datetime | None = None,
) -> dict[str, object]:
    trade_time = time(9, minute)
    trade_at = datetime(2026, 7, 14, 9, minute, tzinfo=UTC)
    facts = TransactionFactPayload(
        transaction_type=transaction_type,
        trade_date=AS_OF,
        trade_time=trade_time,
        trade_at=trade_at,
        trade_timezone="UTC",
        trade_time_is_estimated=False,
        settlement_date=AS_OF,
        entitlement_date=entitlement_date,
        acquisition_date=acquisition_date,
        account_id=account_id,
        settlement_cash_account_id=settlement_cash,
        instrument_id="fund-a" if instrument else None,
        instrument_snapshot_json=(
            _instrument_snapshot(currency=currency) if instrument else None
        ),
        quantity=None if quantity is None else D(quantity),
        price=None if price is None else D(price),
        gross_amount=D(gross),
        counter_amount=None if counter_amount is None else D(counter_amount),
        quoted_fx_rate=(
            None if quoted_fx_rate is None else D(quoted_fx_rate)
        ),
        consideration_basis=(
            "source_reported"
            if instrument
            and transaction_type
            in {"buy", "sell", "dividend_reinvestment", "opening_balance"}
            else None
        ),
        fees=D(fees),
        taxes=D(taxes),
        currency=currency,
        transfer_scope=(
            "internal_portfolio" if transfer_object_type is not None else None
        ),
        transfer_object_type=transfer_object_type,
        transfer_group_id=transfer_group_id,
        counterparty_account_id=counterparty_account_id,
        note=None,
    )
    revision_id = f"rev-{transaction_id}"
    return _identity(
        {
            "transaction_id": transaction_id,
            "revision_id": revision_id,
            "revision_number": 1,
            "revision_group_id": revision_group_id or f"group-{transaction_id}",
            "group_recorded_at": group_recorded_at
            or datetime(2026, 7, 14, 10, minute, tzinfo=UTC),
            "revision_kind": "create",
            "is_tombstone": False,
            "supersedes_revision_id": None,
            "supersedes_revision_number": None,
            "payload_schema_version": "transaction-revision.v1",
            "payload_hash": transaction_payload_hash(facts),
            **facts.as_record_values(),
            "effective_fx_rate_method50": (
                D(counter_amount) / D(gross)
                if transaction_type == "fx_conversion"
                and counter_amount is not None
                else None
            ),
            "selected_reason_code": "latest_at_knowledge_cutoff",
        }
    )


def _all_transactions() -> list[dict[str, object]]:
    return [
        _transaction("01-open-cash", "opening_balance", minute=1, account_id="cash-usd", gross="100"),
        _transaction(
            "02-open-position",
            "opening_balance",
            minute=2,
            account_id="securities-a",
            gross="10",
            instrument=True,
            quantity="10",
            price="1",
            acquisition_date=AS_OF,
        ),
        _transaction(
            "03-buy",
            "buy",
            minute=3,
            account_id="securities-a",
            gross="10",
            instrument=True,
            settlement_cash="cash-usd",
            quantity="2",
            price="5",
        ),
        _transaction(
            "04-sell",
            "sell",
            minute=4,
            account_id="securities-a",
            gross="5",
            instrument=True,
            settlement_cash="cash-usd",
            quantity="1",
            price="5",
        ),
        _transaction(
            "05-dividend",
            "dividend",
            minute=5,
            account_id="securities-a",
            gross="2",
            instrument=True,
            settlement_cash="cash-usd",
            entitlement_date=AS_OF,
        ),
        _transaction(
            "06-reinvest",
            "dividend_reinvestment",
            minute=6,
            account_id="securities-a",
            gross="2",
            instrument=True,
            quantity="1",
            price="2",
        ),
        _transaction(
            "07-coupon",
            "coupon",
            minute=7,
            account_id="securities-a",
            gross="2",
            instrument=True,
            settlement_cash="cash-usd",
        ),
        _transaction("08-interest", "interest", minute=8, account_id="cash-usd", gross="1"),
        _transaction(
            "09-roc",
            "return_of_capital",
            minute=9,
            account_id="securities-a",
            gross="1",
            instrument=True,
            settlement_cash="cash-usd",
        ),
        _transaction(
            "10-maturity",
            "maturity_redemption",
            minute=10,
            account_id="securities-a",
            gross="5",
            instrument=True,
            settlement_cash="cash-usd",
            quantity="1",
        ),
        _transaction(
            "11-fee",
            "fee",
            minute=11,
            account_id="securities-a",
            gross="1",
            instrument=True,
            settlement_cash="cash-usd",
            entitlement_date=AS_OF,
        ),
        _transaction("12-tax", "tax", minute=12, account_id="cash-usd", gross="1"),
        _transaction("13-deposit", "deposit", minute=13, account_id="cash-usd", gross="10"),
        _transaction("14-withdraw", "withdrawal", minute=14, account_id="cash-usd", gross="3"),
        _transaction(
            "15-fx",
            "fx_conversion",
            minute=15,
            account_id="cash-usd",
            gross="1",
            counter_amount="7",
            quoted_fx_rate="7",
            counterparty_account_id="cash-cny",
        ),
        _transaction(
            "16-transfer-out",
            "transfer_out",
            minute=16,
            account_id="securities-a",
            gross="4",
            instrument=True,
            quantity="4",
            transfer_object_type="position",
            transfer_group_id="transfer-position",
            counterparty_account_id="securities-b",
            revision_group_id="group-transfer-position",
            group_recorded_at=datetime(2026, 7, 14, 10, 16, tzinfo=UTC),
        ),
        _transaction(
            "17-transfer-in",
            "transfer_in",
            minute=16,
            account_id="securities-b",
            gross="4",
            instrument=True,
            quantity="4",
            transfer_object_type="position",
            transfer_group_id="transfer-position",
            counterparty_account_id="securities-a",
            revision_group_id="group-transfer-position",
            group_recorded_at=datetime(2026, 7, 14, 10, 16, tzinfo=UTC),
        ),
    ]


def _unavailable_cny_fx_path() -> dict[str, object]:
    return _identity(
        {
            "fx_path_id": uuid4(),
            "valuation_date": AS_OF,
            "from_currency": "CNY",
            "to_currency": "USD",
            "path_kind": "unavailable",
            "resolution_status": "unavailable",
            "leg_count": 0,
            "resolved_rate": None,
            "rate_derivation_residual_exact": None,
            "selection_policy_version": QUOTE_SELECTION_POLICY_VERSION,
            "consumer_policy_version": FX_CONSUMER_POLICY_VERSION,
            "freshness_policy_version": QUOTE_FRESHNESS_POLICY_VERSION,
            "freshness_mode": "calendar_day_carry_forward",
            "freshness_max_age_days": 5,
            "resolver_strategy_version": FX_RESOLVER_STRATEGY_VERSION,
            "rate_math_precision": FX_RATE_MATH_PRECISION,
            "rate_rounding_mode": FX_RATE_ROUNDING_MODE,
            "coverage_state": "unavailable",
            "reason_codes": ["unavailable_fx_path"],
        }
    )


def _resolved_cny_fx_evidence(
    valuation_date: date,
    *,
    rate: Decimal,
) -> tuple[dict[str, object], dict[str, object]]:
    path_id = UUID("00000000-0000-0000-0000-000000000201")
    path = _identity(
        {
            "fx_path_id": path_id,
            "valuation_date": valuation_date,
            "from_currency": "CNY",
            "to_currency": "USD",
            "path_kind": "direct",
            "resolution_status": "resolved",
            "leg_count": 1,
            "resolved_rate": rate,
            "rate_derivation_residual_exact": D("0"),
            "selection_policy_version": QUOTE_SELECTION_POLICY_VERSION,
            "consumer_policy_version": FX_CONSUMER_POLICY_VERSION,
            "freshness_policy_version": QUOTE_FRESHNESS_POLICY_VERSION,
            "freshness_mode": "calendar_day_carry_forward",
            "freshness_max_age_days": 5,
            "resolver_strategy_version": FX_RESOLVER_STRATEGY_VERSION,
            "rate_math_precision": FX_RATE_MATH_PRECISION,
            "rate_rounding_mode": FX_RATE_ROUNDING_MODE,
            "coverage_state": "complete",
            "reason_codes": [],
        }
    )
    leg = _identity(
        {
            "fx_path_id": path_id,
            "leg_order": 1,
            "from_currency": "CNY",
            "to_currency": "USD",
            "is_inverted": False,
            "leg_resolution_status": "resolved",
            "reason_codes": [],
            "quote_series_id": UUID("00000000-0000-0000-0000-000000000202"),
            "observation_id": UUID("00000000-0000-0000-0000-000000000203"),
            "revision_id": UUID("00000000-0000-0000-0000-000000000204"),
            "revision_number": 1,
            "observation_date": valuation_date,
            "quoted_rate": rate,
            "effective_rate": rate,
            "rate_derivation_residual_exact": D("0"),
            "quote_status": "active",
            "source_published_at": datetime(2026, 7, 14, 10, tzinfo=UTC),
            "ingested_at": datetime(2026, 7, 14, 11, tzinfo=UTC),
            "ingestion_time_state": "observed",
            "payload_hash": "sha256:" + "1" * 64,
            "consumer_policy_version": FX_CONSUMER_POLICY_VERSION,
            "freshness_policy_version": QUOTE_FRESHNESS_POLICY_VERSION,
            "freshness_mode": "calendar_day_carry_forward",
            "freshness_max_age_days": 5,
            "resolver_strategy_version": FX_RESOLVER_STRATEGY_VERSION,
            "rate_math_precision": FX_RATE_MATH_PRECISION,
            "rate_rounding_mode": FX_RATE_ROUNDING_MODE,
        }
    )
    return path, leg


def _dependencies(
    *,
    transactions: list[dict[str, object]] | None = None,
    actions: list[dict[str, object]] | None = None,
) -> dict[str, list[dict[str, object]]]:
    rows: dict[str, list[dict[str, object]]] = {
        table: [] for table in DEPENDENCY_NATURAL_KEYS
    }
    rows["portfolio_daily_config_input"] = [_config()]
    rows["portfolio_daily_account_input"] = [
        _account("cash-usd", account_type="deposit_account", currency="USD"),
        _account("cash-cny", account_type="deposit_account", currency="CNY"),
        _account(
            "securities-a",
            account_type="securities_account",
            currency="USD",
            default_cash="cash-usd",
            cost_method="fifo",
        ),
        _account(
            "securities-b",
            account_type="securities_account",
            currency="USD",
            default_cash="cash-usd",
            cost_method="fifo",
        ),
    ]
    rows["portfolio_daily_instrument_input"] = [_instrument()]
    rows["portfolio_daily_transaction_input"] = (
        _all_transactions() if transactions is None else transactions
    )
    rows["portfolio_daily_corp_action_input"] = actions or []
    rows["portfolio_daily_fx_path"] = [_unavailable_cny_fx_path()]
    return rows


def _action(
    event_id: str,
    *,
    status: str = "confirmed",
    action_type: str = "share_split",
) -> dict[str, object]:
    created_at = datetime(2026, 7, 13, 11, tzinfo=UTC)
    updated_at = datetime(2026, 7, 14, 11, tzinfo=UTC)
    canonical = canonical_storage_json(
        {
            "corporate_action_event_id": event_id,
            "instrument_id": "fund-a",
            "action_type": action_type,
            "announcement_date": None,
            "record_date": None,
            "effective_date": AS_OF,
            "payable_date": None,
            "new_units": D("2"),
            "old_units": D("1"),
            "quantity_rounding": "exact",
            "quantity_precision": 12,
            "cost_basis_treatment": "carry",
            "source": "issuer",
            "external_event_id": f"external-{event_id}",
            "status": status,
            "provenance": {"source": "test"},
            "created_at": created_at,
            "updated_at": updated_at,
        }
    )
    return _identity(
        {
            "corporate_action_event_id": event_id,
            "instrument_id": "fund-a",
            "action_type": action_type,
            "announcement_date": None,
            "record_date": None,
            "effective_date": AS_OF,
            "payable_date": None,
            "new_units": D("2"),
            "old_units": D("1"),
            "quantity_rounding": "exact",
            "quantity_precision": 12,
            "cost_basis_treatment": "carry",
            "source": "issuer",
            "external_event_id": f"external-{event_id}",
            "event_status": status,
            "event_updated_at": updated_at,
            "event_schema_version": CORPORATE_ACTION_SCHEMA_VERSION,
            "event_hash": canonical_sha256_hex(canonical),
            "canonical_event": canonical,
        }
    )


def test_all_current_transaction_types_map_to_exact_events_and_unavailable_fx_keeps_local_leg() -> None:
    result = build_ledger_events(_dependencies())

    assert len(result.events) == 16  # paired transfer consumes two rows once
    assert {type(event) for event in result.events} == {
        OpeningCashEvent,
        OpeningPositionEvent,
        BuyEvent,
        SellEvent,
        ExternalCashFlowEvent,
        IncomeEvent,
        ReturnOfCapitalEvent,
        MaturityRedemptionEvent,
        DividendReinvestmentEvent,
        ExpenseEvent,
        FxConversionEvent,
        PositionTransferEvent,
    }
    transfer = next(
        event
        for event in result.events
        if isinstance(event, PositionTransferEvent)
    )
    assert transfer.declared_local_cost == D("4.00000000")
    assert transfer.lineage.source_record_id == "16-transfer-out"
    assert transfer.counterparty_lineage.source_record_id == "17-transfer-in"
    fx = next(event for event in result.events if isinstance(event, FxConversionEvent))
    assert fx.source_to_base_rate == D("1")
    assert fx.target_to_base_rate is None
    assert fx.target_to_base_lineage is None
    assert [item.code for item in result.diagnostics] == [
        LedgerEventDiagnosticCode.FX_PATH_UNAVAILABLE
    ]


def test_transaction_replay_profile_uses_the_same_exact_event_mapping() -> None:
    sealed_rows = _dependencies(actions=[_action("split-a")])
    transaction_replay_rows = {
        table: deepcopy(sealed_rows[table])
        for table in (
            "portfolio_daily_config_input",
            "portfolio_daily_account_input",
            "portfolio_daily_transaction_input",
            "portfolio_daily_instrument_input",
            "portfolio_daily_corp_action_window",
            "portfolio_daily_corp_action_input",
        )
    }

    sealed = build_ledger_events(sealed_rows)
    prospective = build_transaction_replay_events(transaction_replay_rows)

    assert prospective.events == sealed.events
    assert [diagnostic.code for diagnostic in prospective.diagnostics] == [
        LedgerEventDiagnosticCode.FX_PATH_UNAVAILABLE
    ]


def test_event_amount_bridge_is_independent_of_ambient_decimal_context() -> None:
    transaction = _transaction(
        "precision-buy",
        "buy",
        minute=1,
        account_id="securities-a",
        gross="1.52415788",
        instrument=True,
        settlement_cash="cash-usd",
        quantity="12345.678901",
        price="0.000123456789",
    )

    original_precision = getcontext().prec
    try:
        getcontext().prec = 6
        result = build_ledger_events(_dependencies(transactions=[transaction]))
    finally:
        getcontext().prec = original_precision

    assert len(result.events) == 1
    event = result.events[0]
    assert isinstance(event, BuyEvent)
    assert event.gross_amount == D("1.52415788")


def test_event_sequence_and_diagnostics_are_independent_of_manifest_row_order() -> None:
    baseline = _dependencies(actions=[_action("detected", status="detected")])
    expected = build_ledger_events(baseline)

    shuffled = deepcopy(baseline)
    random.Random(20260714).shuffle(shuffled["portfolio_daily_transaction_input"])
    random.Random(42).shuffle(shuffled["portfolio_daily_account_input"])
    actual = build_ledger_events(shuffled)

    assert actual == expected
    assert tuple(event.sequence for event in actual.events) == tuple(
        range(len(actual.events))
    )
    assert LedgerEventDiagnosticCode.DETECTED_CORPORATE_ACTION_IGNORED in {
        item.code for item in actual.diagnostics
    }


def test_confirmed_exact_split_maps_to_instrument_scope_event() -> None:
    result = build_ledger_events(_dependencies(actions=[_action("split-a")]))
    split = next(event for event in result.events if isinstance(event, SplitEvent))
    assert split.instrument_id == "fund-a"
    assert split.ratio_numerator == D("2")
    assert split.ratio_denominator == D("1")
    assert not hasattr(split, "position_account_id")


def test_opening_position_uses_exact_acquisition_date_fx_and_sealed_lineage() -> None:
    acquisition_date = date(2026, 7, 10)
    rate = D("0.123456789012345678")
    opening = _transaction(
        "opening-cny-position",
        "opening_balance",
        minute=1,
        account_id="securities-a",
        gross="10",
        instrument=True,
        quantity="10",
        price="1",
        currency="CNY",
        acquisition_date=acquisition_date,
    )
    rows = _dependencies(transactions=[opening])
    rows["portfolio_daily_account_input"] = [
        row
        for row in rows["portfolio_daily_account_input"]
        if row["account_id"] != "securities-a"
    ] + [
        _account(
            "securities-a",
            account_type="securities_account",
            currency="CNY",
            default_cash="cash-cny",
            cost_method="fifo",
        )
    ]
    rows["portfolio_daily_instrument_input"] = [_instrument(currency="CNY")]
    path, leg = _resolved_cny_fx_evidence(acquisition_date, rate=rate)
    rows["portfolio_daily_fx_path"] = [path]
    rows["portfolio_daily_fx_leg"] = [leg]

    result = build_ledger_events(rows)

    event = result.events[0]
    assert isinstance(event, OpeningPositionEvent)
    assert event.acquisition_local_to_base_rate == rate
    assert event.acquisition_fx_lineage is not None
    assert event.acquisition_fx_lineage.source_record_id == str(path["fx_path_id"])
    assert event.acquisition_fx_lineage.manifest_fact_key.endswith(
        f"/{acquisition_date.isoformat()}/CNY/USD"
    )
    assert result.diagnostics == ()


def test_fx_path_cannot_publish_a_rounded_product_with_residual() -> None:
    flow = _transaction(
        "cny-deposit",
        "deposit",
        minute=1,
        account_id="cash-cny",
        gross="10",
        currency="CNY",
    )
    rows = _dependencies(transactions=[flow])
    path, leg = _resolved_cny_fx_evidence(AS_OF, rate=D("0.14"))
    path["resolved_rate"] = D("0.140000000000000001")
    path["rate_derivation_residual_exact"] = D("0.000000000000000001")
    rows["portfolio_daily_fx_path"] = [path]
    rows["portfolio_daily_fx_leg"] = [leg]

    with pytest.raises(LedgerEventBuildError) as exc_info:
        build_ledger_events(rows)

    assert exc_info.value.code is LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID
    assert exc_info.value.field_name == "rate_derivation_residual_exact"


def test_fx_consumer_rejects_self_consistent_but_noncanonical_inverse_leg() -> None:
    flow = _transaction(
        "cny-deposit-forged-inverse",
        "deposit",
        minute=1,
        account_id="cash-cny",
        gross="10",
        currency="CNY",
    )
    rows = _dependencies(transactions=[flow])
    path, leg = _resolved_cny_fx_evidence(AS_OF, rate=D("0.15"))
    path["path_kind"] = "inverse"
    leg["is_inverted"] = True
    leg["quoted_rate"] = D("7")
    leg["effective_rate"] = D("0.15")
    leg["rate_derivation_residual_exact"] = D("0.05")
    rows["portfolio_daily_fx_path"] = [path]
    rows["portfolio_daily_fx_leg"] = [leg]

    with pytest.raises(LedgerEventBuildError) as exc_info:
        build_ledger_events(rows)

    assert exc_info.value.code is LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID
    assert exc_info.value.field_name == "effective_rate"


def test_known_direct_fx_path_can_be_unavailable_with_sealed_missing_leg() -> None:
    flow = _transaction(
        "cny-deposit-missing-fx",
        "deposit",
        minute=1,
        account_id="cash-cny",
        gross="10",
        currency="CNY",
    )
    rows = _dependencies(transactions=[flow])
    path, leg = _resolved_cny_fx_evidence(AS_OF, rate=D("0.14"))
    path.update(
        {
            "resolution_status": "unavailable",
            "resolved_rate": None,
            "rate_derivation_residual_exact": None,
            "coverage_state": "unavailable",
            "reason_codes": ["missing_fx_leg", "unavailable_fx_leg"],
        }
    )
    leg.update(
        {
            "leg_resolution_status": "missing",
            "reason_codes": ["missing_observation"],
            "observation_id": None,
            "revision_id": None,
            "revision_number": None,
            "observation_date": None,
            "quoted_rate": None,
            "effective_rate": None,
            "rate_derivation_residual_exact": None,
            "quote_status": None,
                "source_published_at": None,
                "ingested_at": None,
                "ingestion_time_state": None,
                "payload_hash": None,
        }
    )
    rows["portfolio_daily_fx_path"] = [path]
    rows["portfolio_daily_fx_leg"] = [leg]

    result = build_ledger_events(rows)

    assert [item.code for item in result.diagnostics] == [
        LedgerEventDiagnosticCode.FX_PATH_UNAVAILABLE
    ]


def test_corporate_action_typed_columns_must_match_complete_canonical_fact() -> None:
    action = _action("split-mismatch")
    action["external_event_id"] = "different-external-id"

    with pytest.raises(LedgerEventBuildError) as exc_info:
        build_ledger_events(_dependencies(actions=[action]))

    assert exc_info.value.code is LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION
    assert exc_info.value.field_name == "canonical_event"


def test_detected_unsupported_action_is_diagnostic_only() -> None:
    result = build_ledger_events(
        _dependencies(
            actions=[
                _action(
                    "detected-merger",
                    status="detected",
                    action_type="merger",
                )
            ]
        )
    )

    assert not any(isinstance(event, SplitEvent) for event in result.events)
    assert LedgerEventDiagnosticCode.DETECTED_CORPORATE_ACTION_IGNORED in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_non_exact_confirmed_split_fails_closed() -> None:
    action = _action("split-cil")
    action["quantity_rounding"] = "cash_in_lieu"
    canonical = dict(action["canonical_event"])
    canonical["quantity_rounding"] = "cash_in_lieu"
    action["canonical_event"] = canonical
    action["event_hash"] = canonical_sha256_hex(canonical)

    with pytest.raises(LedgerEventBuildError) as exc_info:
        build_ledger_events(_dependencies(actions=[action]))

    assert (
        exc_info.value.code
        is LedgerEventBuildErrorCode.CORPORATE_ACTION_TERMS_UNSUPPORTED
    )


def test_missing_group_recorded_at_is_input_contract_failure() -> None:
    rows = _dependencies()
    del rows["portfolio_daily_transaction_input"][0]["group_recorded_at"]

    with pytest.raises(LedgerEventBuildError) as exc_info:
        build_ledger_events(rows)

    assert exc_info.value.code is LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION
    assert exc_info.value.field_name == "group_recorded_at"


def test_transfer_pair_must_be_exactly_reciprocal() -> None:
    rows = _dependencies()
    inbound = next(
        row
        for row in rows["portfolio_daily_transaction_input"]
        if row["transaction_id"] == "17-transfer-in"
    )
    inbound["gross_amount"] = D("4.00000001")
    inbound["gross_amount_input_scale"] = 8
    facts = TransactionFactPayload(
        **{field: inbound[field] for field in TransactionFactPayload.__dataclass_fields__}
    )
    inbound["payload_hash"] = transaction_payload_hash(facts)

    with pytest.raises(LedgerEventBuildError) as exc_info:
        build_ledger_events(rows)

    assert exc_info.value.code is LedgerEventBuildErrorCode.TRANSFER_PAIR_INVALID


def test_cash_transfer_pair_maps_to_single_cash_event() -> None:
    recorded = datetime(2026, 7, 14, 10, 1, tzinfo=UTC)
    transactions = [
        _transaction(
            "cash-out",
            "transfer_out",
            minute=1,
            account_id="cash-usd",
            gross="5",
            transfer_object_type="cash",
            transfer_group_id="cash-pair",
            counterparty_account_id="cash-usd-2",
            revision_group_id="cash-pair-group",
            group_recorded_at=recorded,
        ),
        _transaction(
            "cash-in",
            "transfer_in",
            minute=1,
            account_id="cash-usd-2",
            gross="5",
            transfer_object_type="cash",
            transfer_group_id="cash-pair",
            counterparty_account_id="cash-usd",
            revision_group_id="cash-pair-group",
            group_recorded_at=recorded,
        ),
    ]
    rows = _dependencies(transactions=transactions)
    rows["portfolio_daily_account_input"].append(
        _account("cash-usd-2", account_type="deposit_account", currency="USD")
    )
    result = build_ledger_events(rows)
    assert len(result.events) == 1
    assert isinstance(result.events[0], CashTransferEvent)
