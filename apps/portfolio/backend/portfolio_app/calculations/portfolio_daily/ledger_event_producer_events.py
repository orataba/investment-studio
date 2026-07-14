"""Deterministic mapping from validated facts to exact ledger events."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from dataclasses import replace
from typing import Protocol, cast

from portfolio_app.calculations.numeric import canonical_decimal
from portfolio_app.calculations.portfolio_daily.constants import (
    CORPORATE_ACTION_SCHEMA_VERSION,
)
from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    BuyEvent,
    CashTransferEvent,
    DividendReinvestmentEvent,
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
    LedgerContractError,
    LedgerEvent,
    MaturityRedemptionEvent,
    OpeningCashEvent,
    OpeningPositionEvent,
    PositionTransferEvent,
    ReturnOfCapitalEvent,
    SellEvent,
    SplitEvent,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_common import (
    CORPORATE_ACTION_TABLE,
    TRANSACTION_TABLE,
    RowsByTable,
    _BuildContext,
    _CASH_ACCOUNT_TYPES,
    _Draft,
    _Transaction,
    _aware_datetime,
    _date,
    _exact_decimal,
    _fail,
    _integer,
    _json_object,
    _optional_date,
    _optional_text,
    _required,
    _text,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_contracts import (
    LedgerEventBuildDiagnostic,
    LedgerEventBuildError,
    LedgerEventBuildErrorCode,
    LedgerEventBuildResult,
    LedgerEventDiagnosticCode,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_fx import _FxBook
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_manifest import (
    _build_context,
    _build_transaction_replay_context,
    _hash_matches,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_transactions import (
    _settlement_cash_account,
    _validate_transactions,
)
from portfolio_app.calculations.portfolio_daily.manifest_repository import (
    ManifestDependencies,
)


class _FxResolver(Protocol):
    def resolve(
        self,
        *,
        valuation_date: date,
        from_currency: str,
        consumer_record_id: str,
        field_name: str,
        diagnostics: list[LedgerEventBuildDiagnostic],
    ) -> tuple[Decimal | None, FactLineage | None]: ...


class _LocalBookFxResolver:
    """Resolve only identity FX; non-base measurement stays unavailable."""

    def __init__(self, context: _BuildContext) -> None:
        self._base_currency = context.base_currency

    def resolve(
        self,
        *,
        valuation_date: date,
        from_currency: str,
        consumer_record_id: str,
        field_name: str,
        diagnostics: list[LedgerEventBuildDiagnostic],
    ) -> tuple[Decimal | None, FactLineage | None]:
        if from_currency == self._base_currency:
            return Decimal("1"), None
        diagnostics.append(
            LedgerEventBuildDiagnostic(
                code=LedgerEventDiagnosticCode.FX_PATH_UNAVAILABLE,
                source_table=TRANSACTION_TABLE,
                source_record_id=consumer_record_id,
                field_name=field_name,
                message=(
                    "pre-commit transaction validation intentionally replays "
                    "the exact local book without market FX"
                ),
                context=tuple(
                    sorted(
                        (
                            ("from_currency", from_currency),
                            (
                                "reason_codes",
                                "transaction_validation_local_book_only",
                            ),
                            ("to_currency", self._base_currency),
                            ("valuation_date", valuation_date.isoformat()),
                        )
                    )
                ),
            )
        )
        return None, None


def _event_id(transaction: _Transaction) -> str:
    return f"transaction:{transaction.transaction_id}:{transaction.revision_id}"


def _draft_from_event(
    event: LedgerEvent,
    *,
    order_key: tuple[datetime, datetime, str, str, str],
) -> _Draft:
    return _Draft(
        order_key=order_key,
        event_id=event.event_id,
        build=lambda sequence, prototype=event: replace(
            prototype,
            sequence=sequence,
        ),
    )


def _event_fx(
    fx_book: _FxResolver,
    transaction: _Transaction,
    *,
    valuation_date: date,
    diagnostics: list[LedgerEventBuildDiagnostic],
    field_name: str = "local_to_base_rate",
) -> tuple[Decimal | None, FactLineage | None]:
    return fx_book.resolve(
        valuation_date=valuation_date,
        from_currency=transaction.currency,
        consumer_record_id=transaction.transaction_id,
        field_name=field_name,
        diagnostics=diagnostics,
    )


def _transaction_draft(
    context: _BuildContext,
    fx_book: _FxResolver,
    transaction: _Transaction,
    diagnostics: list[LedgerEventBuildDiagnostic],
) -> _Draft:
    event_id = _event_id(transaction)
    lineage = transaction.lineage
    transaction_type = transaction.transaction_type
    if transaction_type == "opening_balance":
        if transaction.instrument_id is None:
            event: LedgerEvent = OpeningCashEvent(
                event_id=event_id,
                sequence=0,
                lineage=lineage,
                effective_date=transaction.trade_date,
                account_id=transaction.account_id,
                currency=transaction.currency,
                amount=transaction.gross_amount,
            )
        else:
            assert transaction.quantity is not None
            assert transaction.acquisition_date is not None
            account = context.accounts[transaction.account_id]
            assert account.cost_basis_method is not None
            rate, fx_lineage = fx_book.resolve(
                valuation_date=transaction.acquisition_date,
                from_currency=transaction.currency,
                consumer_record_id=transaction.transaction_id,
                field_name="acquisition_local_to_base_rate",
                diagnostics=diagnostics,
            )
            event = OpeningPositionEvent(
                event_id=event_id,
                sequence=0,
                lineage=lineage,
                effective_date=transaction.trade_date,
                acquisition_date=transaction.acquisition_date,
                account_id=transaction.account_id,
                instrument_id=transaction.instrument_id,
                currency=transaction.currency,
                base_currency=context.base_currency,
                quantity=transaction.quantity,
                local_cost=transaction.gross_amount,
                acquisition_local_to_base_rate=rate,
                acquisition_fx_lineage=fx_lineage,
                cost_basis_method=account.cost_basis_method,
            )
        return _draft_from_event(event, order_key=transaction.order_key)

    if transaction_type in {"buy", "sell"}:
        assert transaction.instrument_id is not None
        assert transaction.quantity is not None
        assert transaction.consideration_basis is not None
        instrument = context.instruments[transaction.instrument_id]
        assert instrument.price_unit is not None
        assert instrument.contract_multiplier is not None
        assert instrument.price_factor is not None
        cash = _settlement_cash_account(context, transaction)
        rate, fx_lineage = _event_fx(
            fx_book,
            transaction,
            valuation_date=transaction.trade_date,
            diagnostics=diagnostics,
        )
        common = {
            "event_id": event_id,
            "sequence": 0,
            "lineage": lineage,
            "trade_date": transaction.trade_date,
            "settlement_date": transaction.settlement_date,
            "position_account_id": transaction.account_id,
            "cash_account_id": cash.account_id,
            "instrument_id": transaction.instrument_id,
            "currency": transaction.currency,
            "base_currency": context.base_currency,
            "quantity": transaction.quantity,
            "price": transaction.price,
            "contract_multiplier": instrument.contract_multiplier,
            "price_factor": instrument.price_factor,
            "price_unit": instrument.price_unit,
            "gross_amount": transaction.gross_amount,
            "consideration_basis": transaction.consideration_basis,
            "local_to_base_rate": rate,
            "local_to_base_lineage": fx_lineage,
            "fees": transaction.fees,
            "taxes": transaction.taxes,
        }
        if transaction_type == "buy":
            account = context.accounts[transaction.account_id]
            assert account.cost_basis_method is not None
            event = BuyEvent(
                **common,
                cost_basis_method=account.cost_basis_method,
            )
        else:
            event = SellEvent(**common)
        return _draft_from_event(event, order_key=transaction.order_key)

    if transaction_type in {"deposit", "withdrawal"}:
        rate, fx_lineage = _event_fx(
            fx_book,
            transaction,
            valuation_date=transaction.settlement_date,
            diagnostics=diagnostics,
        )
        kind = (
            ExternalFlowKind.DEPOSIT
            if transaction_type == "deposit"
            else ExternalFlowKind.WITHDRAWAL
        )
        event = ExternalCashFlowEvent(
            event_id=event_id,
            sequence=0,
            lineage=lineage,
            value_date=transaction.settlement_date,
            account_id=transaction.account_id,
            currency=transaction.currency,
            base_currency=context.base_currency,
            kind=kind,
            timing=(
                ExternalFlowTiming.BEGINNING_OF_DAY
                if kind is ExternalFlowKind.DEPOSIT
                else ExternalFlowTiming.END_OF_DAY
            ),
            amount=transaction.gross_amount,
            local_to_base_rate=rate,
            local_to_base_lineage=fx_lineage,
        )
        return _draft_from_event(event, order_key=transaction.order_key)

    if transaction_type in {"dividend", "coupon", "interest"}:
        recognition = transaction.entitlement_date or transaction.trade_date
        rate, fx_lineage = _event_fx(
            fx_book,
            transaction,
            valuation_date=recognition,
            diagnostics=diagnostics,
        )
        if transaction_type == "interest":
            cash_account_id = transaction.account_id
            instrument_id = None
            kind = IncomeKind.INTEREST
        else:
            cash_account_id = _settlement_cash_account(
                context,
                transaction,
            ).account_id
            instrument_id = transaction.instrument_id
            kind = (
                IncomeKind.DIVIDEND
                if transaction_type == "dividend"
                else IncomeKind.COUPON
            )
        event = IncomeEvent(
            event_id=event_id,
            sequence=0,
            lineage=lineage,
            recognition_date=recognition,
            settlement_date=transaction.settlement_date,
            cash_account_id=cash_account_id,
            currency=transaction.currency,
            base_currency=context.base_currency,
            kind=kind,
            gross_amount=transaction.gross_amount,
            local_to_base_rate=rate,
            local_to_base_lineage=fx_lineage,
            instrument_id=instrument_id,
            fees=transaction.fees,
            taxes=transaction.taxes,
        )
        return _draft_from_event(event, order_key=transaction.order_key)

    if transaction_type == "return_of_capital":
        assert transaction.instrument_id is not None
        rate, fx_lineage = _event_fx(
            fx_book,
            transaction,
            valuation_date=transaction.trade_date,
            diagnostics=diagnostics,
        )
        event = ReturnOfCapitalEvent(
            event_id=event_id,
            sequence=0,
            lineage=lineage,
            recognition_date=transaction.trade_date,
            settlement_date=transaction.settlement_date,
            position_account_id=transaction.account_id,
            cash_account_id=_settlement_cash_account(
                context,
                transaction,
            ).account_id,
            instrument_id=transaction.instrument_id,
            currency=transaction.currency,
            base_currency=context.base_currency,
            gross_amount=transaction.gross_amount,
            local_to_base_rate=rate,
            local_to_base_lineage=fx_lineage,
            fees=transaction.fees,
            taxes=transaction.taxes,
        )
        return _draft_from_event(event, order_key=transaction.order_key)

    if transaction_type == "maturity_redemption":
        assert transaction.instrument_id is not None
        assert transaction.quantity is not None
        rate, fx_lineage = _event_fx(
            fx_book,
            transaction,
            valuation_date=transaction.trade_date,
            diagnostics=diagnostics,
        )
        event = MaturityRedemptionEvent(
            event_id=event_id,
            sequence=0,
            lineage=lineage,
            recognition_date=transaction.trade_date,
            settlement_date=transaction.settlement_date,
            position_account_id=transaction.account_id,
            cash_account_id=_settlement_cash_account(
                context,
                transaction,
            ).account_id,
            instrument_id=transaction.instrument_id,
            currency=transaction.currency,
            base_currency=context.base_currency,
            quantity=transaction.quantity,
            gross_amount=transaction.gross_amount,
            local_to_base_rate=rate,
            local_to_base_lineage=fx_lineage,
            fees=transaction.fees,
            taxes=transaction.taxes,
        )
        return _draft_from_event(event, order_key=transaction.order_key)

    if transaction_type == "dividend_reinvestment":
        assert transaction.instrument_id is not None
        assert transaction.quantity is not None
        assert transaction.consideration_basis is not None
        instrument = context.instruments[transaction.instrument_id]
        assert instrument.price_unit is not None
        assert instrument.contract_multiplier is not None
        assert instrument.price_factor is not None
        account = context.accounts[transaction.account_id]
        assert account.cost_basis_method is not None
        rate, fx_lineage = _event_fx(
            fx_book,
            transaction,
            valuation_date=transaction.trade_date,
            diagnostics=diagnostics,
        )
        event = DividendReinvestmentEvent(
            event_id=event_id,
            sequence=0,
            lineage=lineage,
            recognition_date=transaction.trade_date,
            position_account_id=transaction.account_id,
            instrument_id=transaction.instrument_id,
            currency=transaction.currency,
            base_currency=context.base_currency,
            gross_income=transaction.gross_amount,
            quantity=transaction.quantity,
            price=transaction.price,
            contract_multiplier=instrument.contract_multiplier,
            price_factor=instrument.price_factor,
            price_unit=instrument.price_unit,
            consideration_basis=transaction.consideration_basis,
            local_to_base_rate=rate,
            local_to_base_lineage=fx_lineage,
            cost_basis_method=account.cost_basis_method,
        )
        return _draft_from_event(event, order_key=transaction.order_key)

    if transaction_type in {"fee", "tax"}:
        recognition = transaction.entitlement_date or transaction.trade_date
        account = context.accounts[transaction.account_id]
        cash_account_id = (
            account.account_id
            if account.account_type in _CASH_ACCOUNT_TYPES
            else _settlement_cash_account(context, transaction).account_id
        )
        rate, fx_lineage = _event_fx(
            fx_book,
            transaction,
            valuation_date=recognition,
            diagnostics=diagnostics,
        )
        event = ExpenseEvent(
            event_id=event_id,
            sequence=0,
            lineage=lineage,
            recognition_date=recognition,
            settlement_date=transaction.settlement_date,
            cash_account_id=cash_account_id,
            currency=transaction.currency,
            base_currency=context.base_currency,
            kind=(
                ExpenseKind.FEE
                if transaction_type == "fee"
                else ExpenseKind.TAX
            ),
            amount=transaction.gross_amount,
            local_to_base_rate=rate,
            local_to_base_lineage=fx_lineage,
            instrument_id=transaction.instrument_id,
        )
        return _draft_from_event(event, order_key=transaction.order_key)

    if transaction_type == "fx_conversion":
        assert transaction.counterparty_account_id is not None
        assert transaction.counter_amount is not None
        assert transaction.effective_fx_rate is not None
        target = context.accounts[transaction.counterparty_account_id]
        source_rate, source_lineage = fx_book.resolve(
            valuation_date=transaction.trade_date,
            from_currency=transaction.currency,
            consumer_record_id=transaction.transaction_id,
            field_name="source_to_base_rate",
            diagnostics=diagnostics,
        )
        target_rate, target_lineage = fx_book.resolve(
            valuation_date=transaction.trade_date,
            from_currency=target.currency,
            consumer_record_id=transaction.transaction_id,
            field_name="target_to_base_rate",
            diagnostics=diagnostics,
        )
        event = FxConversionEvent(
            event_id=event_id,
            sequence=0,
            lineage=lineage,
            trade_date=transaction.trade_date,
            settlement_date=transaction.settlement_date,
            source_account_id=transaction.account_id,
            target_account_id=target.account_id,
            source_currency=transaction.currency,
            target_currency=target.currency,
            source_amount=transaction.gross_amount,
            target_amount=transaction.counter_amount,
            effective_fx_rate=transaction.effective_fx_rate,
            rate_convention=FxRateConvention.TARGET_PER_SOURCE,
            base_currency=context.base_currency,
            source_to_base_rate=source_rate,
            target_to_base_rate=target_rate,
            source_to_base_lineage=source_lineage,
            target_to_base_lineage=target_lineage,
        )
        return _draft_from_event(event, order_key=transaction.order_key)

    _fail(
        f"transaction type {transaction_type} must be built through its paired producer",
        code=LedgerEventBuildErrorCode.LEDGER_EVENT_CONTRACT_VIOLATION,
        table=TRANSACTION_TABLE,
        record_id=transaction.transaction_id,
        field="transaction_type",
    )


def _transfer_draft(
    context: _BuildContext,
    outbound: _Transaction,
    inbound: _Transaction,
) -> _Draft:
    group_id = outbound.transfer_group_id
    assert group_id is not None
    compared_fields = (
        "revision_group_id",
        "group_recorded_at",
        "trade_date",
        "trade_at",
        "settlement_date",
        "currency",
        "transfer_scope",
        "transfer_object_type",
        "instrument_id",
        "quantity",
        "gross_amount",
    )
    mismatches = tuple(
        field
        for field in compared_fields
        if getattr(outbound, field) != getattr(inbound, field)
    )
    if mismatches:
        _fail(
            "paired transfer legs differ at: " + ", ".join(mismatches),
            code=LedgerEventBuildErrorCode.TRANSFER_PAIR_INVALID,
            table=TRANSACTION_TABLE,
            record_id=group_id,
            field=mismatches[0],
        )
    if (
        outbound.counterparty_account_id != inbound.account_id
        or inbound.counterparty_account_id != outbound.account_id
        or outbound.account_id == inbound.account_id
    ):
        _fail(
            "paired transfer counterparties are not reciprocal",
            code=LedgerEventBuildErrorCode.TRANSFER_PAIR_INVALID,
            table=TRANSACTION_TABLE,
            record_id=group_id,
            field="counterparty_account_id",
        )
    event_id = (
        f"transfer:{group_id}:{outbound.transaction_id}:{outbound.revision_id}:"
        f"{inbound.transaction_id}:{inbound.revision_id}"
    )
    if outbound.transfer_object_type == "cash":
        event: LedgerEvent = CashTransferEvent(
            event_id=event_id,
            sequence=0,
            lineage=outbound.lineage,
            counterparty_lineage=inbound.lineage,
            effective_date=outbound.settlement_date,
            source_account_id=outbound.account_id,
            destination_account_id=inbound.account_id,
            currency=outbound.currency,
            amount=outbound.gross_amount,
        )
    elif outbound.transfer_object_type == "position":
        assert outbound.instrument_id is not None
        assert outbound.quantity is not None
        source = context.accounts[outbound.account_id]
        destination = context.accounts[inbound.account_id]
        if source.cost_basis_method is not destination.cost_basis_method:
            _fail(
                "position transfer cannot change cost-basis method",
                code=LedgerEventBuildErrorCode.TRANSFER_PAIR_INVALID,
                table=TRANSACTION_TABLE,
                record_id=group_id,
                field="counterparty_account_id",
            )
        event = PositionTransferEvent(
            event_id=event_id,
            sequence=0,
            lineage=outbound.lineage,
            counterparty_lineage=inbound.lineage,
            effective_date=outbound.trade_date,
            source_account_id=outbound.account_id,
            destination_account_id=inbound.account_id,
            instrument_id=outbound.instrument_id,
            currency=outbound.currency,
            quantity=outbound.quantity,
            declared_local_cost=outbound.gross_amount,
        )
    else:  # pragma: no cover - validated closed value
        raise AssertionError(outbound.transfer_object_type)
    return _draft_from_event(
        event,
        order_key=min(outbound.order_key, inbound.order_key),
    )


def _transaction_drafts(
    context: _BuildContext,
    fx_book: _FxResolver,
    transactions: tuple[_Transaction, ...],
    diagnostics: list[LedgerEventBuildDiagnostic],
) -> list[_Draft]:
    drafts: list[_Draft] = []
    transfers_by_group: dict[str, list[_Transaction]] = {}
    for transaction in transactions:
        if transaction.transaction_type in {"transfer_in", "transfer_out"}:
            assert transaction.transfer_group_id is not None
            transfers_by_group.setdefault(transaction.transfer_group_id, []).append(
                transaction
            )
        else:
            drafts.append(
                _transaction_draft(
                    context,
                    fx_book,
                    transaction,
                    diagnostics,
                )
            )
    for group_id in sorted(transfers_by_group):
        group = transfers_by_group[group_id]
        outbound = [item for item in group if item.transaction_type == "transfer_out"]
        inbound = [item for item in group if item.transaction_type == "transfer_in"]
        if len(group) != 2 or len(outbound) != 1 or len(inbound) != 1:
            _fail(
                "internal transfer group must contain exactly one out and one in revision",
                code=LedgerEventBuildErrorCode.TRANSFER_PAIR_INVALID,
                table=TRANSACTION_TABLE,
                record_id=group_id,
                field="transfer_group_id",
            )
        drafts.append(_transfer_draft(context, outbound[0], inbound[0]))
    return drafts


def _canonical_action_column_value(field: str, value: object) -> object:
    if isinstance(value, Decimal):
        return canonical_decimal(value, field_name=field)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
    if isinstance(value, date):
        return value.isoformat()
    return value


def _canonical_action_datetime(
    value: object,
    *,
    event_id: str,
    field: str,
) -> datetime:
    text = _text(
        value,
        table=CORPORATE_ACTION_TABLE,
        record_id=event_id,
        field=f"canonical_event.{field}",
    )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail(
            f"canonical corporate action {field} is not an ISO datetime",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="canonical_event",
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(
            f"canonical corporate action {field} must be timezone-aware",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="canonical_event",
        )
    normalized = parsed.astimezone(UTC)
    if text != _canonical_action_column_value(field, normalized):
        _fail(
            f"canonical corporate action {field} is not canonical UTC",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="canonical_event",
        )
    return normalized


def _corporate_action_drafts(
    context: _BuildContext,
    transactions: tuple[_Transaction, ...],
    diagnostics: list[LedgerEventBuildDiagnostic],
) -> list[_Draft]:
    drafts: list[_Draft] = []
    seen_ids: set[str] = set()
    seen_confirmed_keys: set[tuple[str, date]] = set()
    for row in sorted(
        context.rows[CORPORATE_ACTION_TABLE],
        key=lambda item: (
            str(item.get("effective_date")),
            str(item.get("instrument_id")),
            str(item.get("corporate_action_event_id")),
        ),
    ):
        event_id = _text(
            _required(
                row,
                "corporate_action_event_id",
                table=CORPORATE_ACTION_TABLE,
                record_id="__row__",
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id="__row__",
            field="corporate_action_event_id",
        )
        if event_id in seen_ids:
            _fail(
                f"duplicate corporate_action_event_id {event_id}",
                code=LedgerEventBuildErrorCode.DUPLICATE_NATURAL_KEY,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="corporate_action_event_id",
            )
        seen_ids.add(event_id)
        schema = _text(
            _required(
                row,
                "event_schema_version",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="event_schema_version",
        )
        if schema != CORPORATE_ACTION_SCHEMA_VERSION:
            _fail(
                f"unsupported corporate-action schema {schema}",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="event_schema_version",
            )
        canonical = _json_object(
            _required(
                row,
                "canonical_event",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="canonical_event",
        )
        _hash_matches(
            value=_required(
                row,
                "event_hash",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            expected_payload=canonical,
            prefixed=False,
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="event_hash",
        )
        instrument_id = _text(
            _required(
                row,
                "instrument_id",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="instrument_id",
        )
        instrument = context.instruments.get(instrument_id)
        if instrument is None:
            _fail(
                "corporate action instrument is absent from sealed manifest",
                code=LedgerEventBuildErrorCode.MISSING_REFERENCE,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="instrument_id",
            )
        action_type = _text(
            _required(
                row,
                "action_type",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="action_type",
        )
        effective_date = _date(
            _required(
                row,
                "effective_date",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="effective_date",
        )
        announcement_date = _optional_date(
            _required(
                row,
                "announcement_date",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="announcement_date",
        )
        record_date = _optional_date(
            _required(
                row,
                "record_date",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="record_date",
        )
        payable_date = _optional_date(
            _required(
                row,
                "payable_date",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="payable_date",
        )
        if record_date is not None and record_date > effective_date:
            _fail(
                "corporate-action record_date follows effective_date",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="record_date",
            )
        updated_at = _aware_datetime(
            _required(
                row,
                "event_updated_at",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="event_updated_at",
        )
        if updated_at > context.knowledge_cutoff_at:
            _fail(
                "corporate action was updated after manifest knowledge cutoff",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="event_updated_at",
            )
        new_units = _exact_decimal(
            _required(
                row,
                "new_units",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="new_units",
            strictly_positive=True,
        )
        old_units = _exact_decimal(
            _required(
                row,
                "old_units",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="old_units",
            strictly_positive=True,
        )
        quantity_rounding = _text(
            _required(
                row,
                "quantity_rounding",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="quantity_rounding",
        )
        precision = _integer(
            _required(
                row,
                "quantity_precision",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="quantity_precision",
            minimum=0,
        )
        if precision > 12:
            _fail(
                "corporate-action quantity_precision exceeds ledger scale",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="quantity_precision",
            )
        treatment = _text(
            _required(
                row,
                "cost_basis_treatment",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="cost_basis_treatment",
        )
        source = _text(
            _required(
                row,
                "source",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="source",
        )
        external_event_id = _optional_text(
            _required(
                row,
                "external_event_id",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="external_event_id",
        )
        status = _text(
            _required(
                row,
                "event_status",
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
            ),
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="event_status",
        )
        if status not in {"detected", "confirmed", "cancelled"}:
            _fail(
                f"unsupported corporate-action status {status}",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="event_status",
            )
        canonical_fields = {
            "corporate_action_event_id",
            "instrument_id",
            "action_type",
            "announcement_date",
            "record_date",
            "effective_date",
            "payable_date",
            "new_units",
            "old_units",
            "quantity_rounding",
            "quantity_precision",
            "cost_basis_treatment",
            "source",
            "external_event_id",
            "status",
            "provenance",
            "created_at",
            "updated_at",
        }
        if set(canonical) != canonical_fields:
            _fail(
                "canonical corporate action has an unsupported field set",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="canonical_event",
            )
        _json_object(
            canonical["provenance"],
            table=CORPORATE_ACTION_TABLE,
            record_id=event_id,
            field="canonical_event.provenance",
        )
        created_at = _canonical_action_datetime(
            canonical["created_at"],
            event_id=event_id,
            field="created_at",
        )
        canonical_updated_at = _canonical_action_datetime(
            canonical["updated_at"],
            event_id=event_id,
            field="updated_at",
        )
        if created_at > canonical_updated_at:
            _fail(
                "canonical corporate action created_at follows updated_at",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="canonical_event",
            )
        typed_to_canonical = {
            "corporate_action_event_id": event_id,
            "instrument_id": instrument_id,
            "action_type": action_type,
            "announcement_date": announcement_date,
            "record_date": record_date,
            "effective_date": effective_date,
            "payable_date": payable_date,
            "new_units": new_units,
            "old_units": old_units,
            "quantity_rounding": quantity_rounding,
            "quantity_precision": precision,
            "cost_basis_treatment": treatment,
            "source": source,
            "external_event_id": external_event_id,
            "status": status,
            "updated_at": updated_at,
        }
        for key, value in typed_to_canonical.items():
            if canonical.get(key) != _canonical_action_column_value(key, value):
                _fail(
                    f"canonical corporate action differs at {key}",
                    code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                    table=CORPORATE_ACTION_TABLE,
                    record_id=event_id,
                    field="canonical_event",
                )
        if canonical_updated_at != updated_at:
            _fail(
                "canonical corporate action updated_at differs from typed column",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="event_updated_at",
            )
        if status == "detected":
            diagnostics.append(
                LedgerEventBuildDiagnostic(
                    code=LedgerEventDiagnosticCode.DETECTED_CORPORATE_ACTION_IGNORED,
                    source_table=CORPORATE_ACTION_TABLE,
                    source_record_id=event_id,
                    field_name="event_status",
                    message="detected corporate action is review-only and cannot change the ledger",
                    context=(("instrument_id", instrument_id),),
                )
            )
            continue
        if status == "cancelled":
            diagnostics.append(
                LedgerEventBuildDiagnostic(
                    code=LedgerEventDiagnosticCode.CANCELLED_CORPORATE_ACTION_IGNORED,
                    source_table=CORPORATE_ACTION_TABLE,
                    source_record_id=event_id,
                    field_name="event_status",
                    message="cancelled corporate action cannot change the ledger",
                    context=(("instrument_id", instrument_id),),
                )
            )
            continue
        if action_type != "share_split":
            _fail(
                f"unsupported confirmed corporate action type {action_type}",
                code=LedgerEventBuildErrorCode.CORPORATE_ACTION_TERMS_UNSUPPORTED,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="action_type",
            )
        if (
            quantity_rounding != "exact"
            or treatment != "carry"
            or new_units == old_units
        ):
            _fail(
                "confirmed split requires exact units, carry cost basis, and a non-unit ratio; "
                "fractional rounding/cash-in-lieu terms are unsupported",
                code=LedgerEventBuildErrorCode.CORPORATE_ACTION_TERMS_UNSUPPORTED,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field=(
                    "quantity_rounding"
                    if quantity_rounding != "exact"
                    else "cost_basis_treatment"
                    if treatment != "carry"
                    else "new_units"
                ),
            )
        if not context.range_start <= effective_date <= context.effective_as_of:
            _fail(
                "confirmed corporate action lies outside its sealed calculation range",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="effective_date",
            )
        if record_date is not None and any(
            transaction.instrument_id == instrument_id
            and record_date < transaction.trade_date < effective_date
            for transaction in transactions
        ):
            _fail(
                "trades between record-date EOD and effective-date BOD require explicit due-bill terms",
                code=LedgerEventBuildErrorCode.CORPORATE_ACTION_TERMS_UNSUPPORTED,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="record_date",
            )
        confirmed_key = (instrument_id, effective_date)
        if confirmed_key in seen_confirmed_keys:
            _fail(
                "multiple confirmed splits share instrument/effective_date",
                code=LedgerEventBuildErrorCode.DUPLICATE_NATURAL_KEY,
                table=CORPORATE_ACTION_TABLE,
                record_id=event_id,
                field="effective_date",
            )
        seen_confirmed_keys.add(confirmed_key)
        event_hash = cast(str, row["event_hash"])
        event = SplitEvent(
            event_id=f"corporate-action:{event_id}:{event_hash}",
            sequence=0,
            lineage=FactLineage(
                source_record_id=event_id,
                source_revision_id=event_hash,
                manifest_fact_key=f"{CORPORATE_ACTION_TABLE}/{event_id}",
            ),
            effective_date=effective_date,
            instrument_id=instrument_id,
            currency=instrument.currency,
            base_currency=context.base_currency,
            ratio_numerator=new_units,
            ratio_denominator=old_units,
        )
        drafts.append(
            _draft_from_event(
                event,
                order_key=(
                    datetime.combine(effective_date, time.min, tzinfo=UTC),
                    updated_at,
                    "corporate_action",
                    event_id,
                    event_hash,
                ),
            )
        )
    return drafts


def _diagnostic_key(
    diagnostic: LedgerEventBuildDiagnostic,
) -> tuple[str, str, str, str, str, tuple[tuple[str, str], ...]]:
    return (
        diagnostic.source_table,
        diagnostic.source_record_id,
        diagnostic.code.value,
        diagnostic.field_name or "",
        diagnostic.message,
        diagnostic.context,
    )


def _build_events(
    context: _BuildContext,
    *,
    fx_resolver: _FxResolver,
) -> LedgerEventBuildResult:
    transactions, diagnostics = _validate_transactions(context)
    drafts = _transaction_drafts(
        context,
        fx_resolver,
        transactions,
        diagnostics,
    )
    drafts.extend(
        _corporate_action_drafts(
            context,
            transactions,
            diagnostics,
        )
    )
    ordered = sorted(drafts, key=lambda item: (item.order_key, item.event_id))
    event_ids = [item.event_id for item in ordered]
    if len(set(event_ids)) != len(event_ids):
        duplicate = min(
            event_id
            for event_id in set(event_ids)
            if event_ids.count(event_id) > 1
        )
        _fail(
            f"duplicate derived ledger event_id {duplicate}",
            code=LedgerEventBuildErrorCode.DUPLICATE_NATURAL_KEY,
            table="__ledger_event__",
            record_id=duplicate,
            field="event_id",
        )
    events = tuple(
        draft.build(sequence)
        for sequence, draft in enumerate(ordered)
    )
    return LedgerEventBuildResult(
        events=events,
        diagnostics=tuple(sorted(diagnostics, key=_diagnostic_key)),
    )


def build_ledger_events(
    dependencies: ManifestDependencies | RowsByTable,
) -> LedgerEventBuildResult:
    """Build deterministic exact events from a complete sealed manifest.

    An explicitly unavailable canonical FX path is not a build failure: the
    local-currency event remains usable and the affected base dimension is
    marked through a structured diagnostic.  Missing path rows, malformed
    evidence, unsupported accounting terms, and schema drift all fail closed.
    """

    try:
        context = _build_context(dependencies)
        return _build_events(
            context,
            fx_resolver=_FxBook(context),
        )
    except LedgerEventBuildError:
        raise
    except LedgerContractError as exc:
        _fail(
            str(exc),
            code=LedgerEventBuildErrorCode.LEDGER_EVENT_CONTRACT_VIOLATION,
            table="__ledger_event__",
            record_id=exc.event_id or "__build__",
        )


def build_transaction_replay_events(
    common_rows: RowsByTable,
) -> LedgerEventBuildResult:
    """Map prospective current facts through the production event semantics."""

    try:
        context = _build_transaction_replay_context(common_rows)
        return _build_events(
            context,
            fx_resolver=_LocalBookFxResolver(context),
        )
    except LedgerEventBuildError:
        raise
    except LedgerContractError as exc:
        _fail(
            str(exc),
            code=LedgerEventBuildErrorCode.LEDGER_EVENT_CONTRACT_VIOLATION,
            table="__ledger_event__",
            record_id=exc.event_id or "__build__",
        )


__all__ = [
    "build_ledger_events",
    "build_transaction_replay_events",
]
