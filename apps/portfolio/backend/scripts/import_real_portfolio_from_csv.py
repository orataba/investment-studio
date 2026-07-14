from __future__ import annotations

# Direct execution bootstraps the backend package path before application imports.
# ruff: noqa: E402

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from sqlalchemy import func, select, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from portfolio_app.core.operating_profiles import SUPPORTED_PORTFOLIO_OPERATING_PROFILES
from portfolio_app.db.models import AccountRecordModel, PortfolioRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.portfolio_store import _allocate_transaction_ids, resolve_trade_timing
from portfolio_app.services.transaction_revisions import (
    CreateTransactionRevision,
    TransactionFactPayload,
    TransactionRevisionContext,
    append_transaction_revision_batch_unchecked,
)
from portfolio_app.services.transaction_command_validator import (
    validate_prospective_transaction_history,
)

PRICE_DISPLAY_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True)
class ParsedTradeRow:
    instrument_name: str
    instrument_type: str
    trade_date: date
    transaction_type: str
    quantity: Decimal
    display_price: Decimal
    gross_amount: Decimal


@dataclass(frozen=True)
class InstrumentRef:
    instrument_id: str
    instrument_name: str
    instrument_type: str
    currency: str
    identifiers: list[dict[str, object]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import a real portfolio from a CSV trade blotter.")
    parser.add_argument("--csv-path", type=Path, required=True)
    parser.add_argument("--portfolio-id", required=True)
    parser.add_argument("--portfolio-name")
    parser.add_argument(
        "--operating-profile",
        choices=SUPPORTED_PORTFOLIO_OPERATING_PROFILES,
        required=True,
    )
    return parser.parse_args()


def parse_decimal(raw_value: str) -> Decimal:
    normalized = raw_value.strip().replace(",", "")
    try:
        return Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError(f"Invalid decimal value: {raw_value!r}") from error


def normalize_instrument_name(raw_value: str) -> str:
    normalized = raw_value.strip()
    normalized = normalized.replace("（", "(").replace("）", ")")
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"([A-Za-z])类$", r"\1", normalized)
    return normalized


def load_trade_rows(csv_path: Path) -> list[ParsedTradeRow]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[ParsedTradeRow] = []
        for raw_row in reader:
            if raw_row is None:
                continue
            instrument_name = str(raw_row.get("instrument") or "").strip()
            if not instrument_name:
                continue
            trade_type = str(raw_row.get("transaction") or "").strip().lower()
            if trade_type != "buy":
                raise ValueError(f"Only buy rows are supported for this importer. Got: {trade_type!r}")
            rows.append(
                ParsedTradeRow(
                    instrument_name=instrument_name,
                    instrument_type=str(raw_row.get("instrument type") or "").strip().lower(),
                    trade_date=datetime.strptime(
                        str(raw_row.get("date") or "").strip(),
                        "%Y/%m/%d",
                    ).date(),
                    transaction_type=trade_type,
                    quantity=parse_decimal(str(raw_row.get("份额") or "")),
                    display_price=parse_decimal(str(raw_row.get("单位价格") or "")),
                    gross_amount=parse_decimal(str(raw_row.get("总额") or "")),
                )
            )
            if rows[-1].quantity <= 0:
                raise ValueError(f"Trade quantity must be positive: {rows[-1].instrument_name}")
            if rows[-1].gross_amount <= 0:
                raise ValueError(f"Trade gross amount must be positive: {rows[-1].instrument_name}")
    if not rows:
        raise ValueError(f"No trade rows found in {csv_path}")
    return rows


def load_shared_instruments(session) -> dict[str, InstrumentRef]:
    rows = session.execute(
        text(
            """
            select
              instrument.instrument_id,
              instrument.instrument_name,
              instrument.instrument_type,
              instrument.currency,
              coalesce(
                json_agg(
                  json_build_object(
                    'identifier_type', identifier.identifier_type,
                    'identifier_value', identifier.identifier_value,
                    'is_primary', identifier.is_primary
                  )
                  order by identifier.is_primary desc, identifier.identifier_type, identifier.identifier_value
                ) filter (where identifier.instrument_identifier_id is not null),
                '[]'::json
              ) as identifiers
            from instrument_registry.instrument as instrument
            left join instrument_registry.instrument_identifier as identifier
              on identifier.instrument_id = instrument.instrument_id
            group by instrument.instrument_id, instrument.instrument_name, instrument.instrument_type, instrument.currency
            """
        )
    ).mappings()

    lookup: dict[str, InstrumentRef] = {}
    for row in rows:
        instrument = InstrumentRef(
            instrument_id=str(row["instrument_id"]),
            instrument_name=str(row["instrument_name"]),
            instrument_type=str(row["instrument_type"]),
            currency=str(row["currency"]),
            identifiers=list(row["identifiers"] or []),
        )
        lookup[instrument.instrument_name] = instrument
        lookup[normalize_instrument_name(instrument.instrument_name)] = instrument
    return lookup


def resolve_instrument(row: ParsedTradeRow, instrument_lookup: dict[str, InstrumentRef]) -> InstrumentRef:
    candidates = [
        row.instrument_name,
        normalize_instrument_name(row.instrument_name),
    ]
    for candidate in candidates:
        instrument = instrument_lookup.get(candidate)
        if instrument is not None:
            return instrument
    raise ValueError(f"Instrument not found in shared registry: {row.instrument_name}")


def transaction_revision_command(
    *,
    transaction_id: str,
    transaction_type: str,
    trade_date: date,
    trade_time: str,
    settlement_date: date,
    account_id: str,
    settlement_cash_account_id: str | None,
    instrument_id: str | None,
    instrument_ref: dict[str, object] | None,
    quantity: Decimal | None,
    price: Decimal | None,
    gross_amount: Decimal,
    currency: str,
    note: str | None,
    created_at: datetime,
) -> CreateTransactionRevision:
    resolved_timing = resolve_trade_timing(trade_date=trade_date, trade_time=trade_time)
    facts = TransactionFactPayload(
        transaction_type=transaction_type,
        trade_date=trade_date,
        trade_time=datetime.strptime(str(resolved_timing["trade_time"]), "%H:%M").time(),
        trade_at=datetime.fromisoformat(
            str(resolved_timing["trade_at"]).replace("Z", "+00:00")
        ),
        trade_timezone=str(resolved_timing["trade_timezone"]),
        trade_time_is_estimated=bool(resolved_timing["trade_time_is_estimated"]),
        settlement_date=settlement_date,
        entitlement_date=None,
        account_id=account_id,
        settlement_cash_account_id=settlement_cash_account_id,
        instrument_id=instrument_id,
        instrument_snapshot_json=instrument_ref,
        quantity=quantity,
        price=price,
        gross_amount=gross_amount,
        counter_amount=None,
        quoted_fx_rate=None,
        consideration_basis=(
            "source_reported"
            if instrument_id is not None
            and transaction_type in {"buy", "sell", "dividend_reinvestment", "opening_balance"}
            else None
        ),
        fees=Decimal("0"),
        taxes=Decimal("0"),
        currency=currency,
        transfer_scope=None,
        transfer_object_type=None,
        transfer_group_id=None,
        counterparty_account_id=None,
        note=note,
    )
    return CreateTransactionRevision(
        transaction_id=transaction_id,
        facts=facts,
        created_at=created_at,
    )


def main() -> None:
    args = parse_args()
    csv_path = args.csv_path.expanduser().resolve()
    portfolio_name = str(args.portfolio_name or args.portfolio_id).strip()
    if not portfolio_name:
        raise ValueError("portfolio_name must not be empty.")
    rows = load_trade_rows(csv_path)

    session_factory = get_session_factory()
    with session_factory() as session:
        existing_portfolio = session.get(PortfolioRecordModel, args.portfolio_id)
        if existing_portfolio is not None:
            raise ValueError(f"Portfolio id already exists: {args.portfolio_id}")
        duplicate_name = session.scalar(
            select(PortfolioRecordModel).where(PortfolioRecordModel.portfolio_name == portfolio_name)
        )
        if duplicate_name is not None:
            raise ValueError(f"Portfolio name already exists: {portfolio_name}")

        instrument_lookup = load_shared_instruments(session)
        max_sort_order = session.scalar(select(func.max(PortfolioRecordModel.sort_order)))
        next_sort_order = int(max_sort_order or -1) + 1

        portfolio_record = PortfolioRecordModel(
            portfolio_id=args.portfolio_id,
            portfolio_name=portfolio_name,
            base_currency="CNY",
            operating_profile=args.operating_profile,
            valuation_timezone="Asia/Shanghai",
            valuation_cutoff_policy="latest_complete_eod",
            sort_order=next_sort_order,
        )
        session.add(portfolio_record)

        cash_account_id = f"cash-{args.portfolio_id}-cny-main"
        securities_account_id = f"broker-{args.portfolio_id}-cny-main"
        existing_account_ids = set(session.scalars(select(AccountRecordModel.account_id)).all())
        if cash_account_id in existing_account_ids or securities_account_id in existing_account_ids:
            raise ValueError("Derived account ids already exist. Choose a different portfolio_id.")

        cash_account = AccountRecordModel(
            account_id=cash_account_id,
            portfolio_id=args.portfolio_id,
            account_name=f"{portfolio_name}资金账户",
            account_type="deposit_account",
            currency="CNY",
            institution="Imported from CSV",
            default_settlement_cash_account_id=None,
            cost_basis_method=None,
            allowed_instrument_types_json=None,
            opened_at=min(row.trade_date for row in rows),
            closed_at=None,
            status="active",
        )
        securities_account = AccountRecordModel(
            account_id=securities_account_id,
            portfolio_id=args.portfolio_id,
            account_name=f"{portfolio_name}证券账户",
            account_type="securities_account",
            currency="CNY",
            institution="Imported from CSV",
            default_settlement_cash_account_id=cash_account_id,
            cost_basis_method="fifo",
            allowed_instrument_types_json=["fund", "etf"],
            opened_at=min(row.trade_date for row in rows),
            closed_at=None,
            status="active",
        )
        session.add(cash_account)
        session.add(securities_account)

        transaction_ids = iter(_allocate_transaction_ids(session, len(rows) + 1))
        current_created_at = datetime.now(UTC).replace(microsecond=0)
        revision_commands: list[CreateTransactionRevision] = []
        imported_trade_date = min(row.trade_date for row in rows)
        total_gross_amount = sum(row.gross_amount for row in rows)

        session.flush()
        deposit_command = transaction_revision_command(
            transaction_id=next(transaction_ids),
            transaction_type="deposit",
            trade_date=imported_trade_date,
            trade_time="09:00",
            settlement_date=imported_trade_date,
            account_id=cash_account_id,
            settlement_cash_account_id=None,
            instrument_id=None,
            instrument_ref=None,
            quantity=None,
            price=None,
            gross_amount=total_gross_amount,
            currency="CNY",
            note=f"Imported funding from {csv_path.name}.",
            created_at=current_created_at,
        )
        revision_commands.append(deposit_command)
        for index, row in enumerate(rows, start=1):
            instrument = resolve_instrument(row, instrument_lookup)
            if row.instrument_type and row.instrument_type != instrument.instrument_type:
                raise ValueError(
                    f"Imported trade instrument type mismatch for {row.instrument_name}: "
                    f"csv={row.instrument_type}, registry={instrument.instrument_type}"
                )
            if instrument.currency != "CNY":
                raise ValueError(
                    f"Imported trade currency mismatch for {row.instrument_name}: expected CNY, got {instrument.currency}"
                )
            rounded_price = (row.gross_amount / row.quantity).quantize(
                PRICE_DISPLAY_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            csv_display_price = row.display_price.quantize(
                PRICE_DISPLAY_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            if csv_display_price != rounded_price:
                raise ValueError(
                    f"Imported trade price mismatch for {row.instrument_name}: "
                    f"csv={csv_display_price}, derived={rounded_price}"
                )
            current_created_at = current_created_at + timedelta(seconds=1)
            note = (
                f"Imported from {csv_path.name}; CSV display price={row.display_price}; "
                f"authoritative quantity/gross_amount preserved."
            )
            transaction_command = transaction_revision_command(
                transaction_id=next(transaction_ids),
                transaction_type=row.transaction_type,
                trade_date=row.trade_date,
                trade_time=f"09:{9 + index:02d}",
                settlement_date=row.trade_date,
                account_id=securities_account_id,
                settlement_cash_account_id=cash_account_id,
                instrument_id=instrument.instrument_id,
                instrument_ref={
                    "instrument_id": instrument.instrument_id,
                    "instrument_name": instrument.instrument_name,
                    "instrument_type": instrument.instrument_type,
                    "currency": instrument.currency,
                    "identifiers": instrument.identifiers,
                },
                quantity=row.quantity,
                price=rounded_price,
                gross_amount=row.gross_amount,
                currency=instrument.currency,
                note=note,
                created_at=current_created_at,
            )
            revision_commands.append(transaction_command)

        append_transaction_revision_batch_unchecked(
            session,
            portfolio_id=args.portfolio_id,
            context=TransactionRevisionContext(
                source_kind="import",
                change_reason=f"Imported verified trade blotter {csv_path.name}",
                actor_type="service",
                actor_id="service:csv-import",
                actor_display_name="CSV portfolio importer",
                actor_source="trusted_service",
                source_ref=str(csv_path),
            ),
            mutations=revision_commands,
        )
        validate_prospective_transaction_history(
            session,
            portfolio_id=args.portfolio_id,
        )
        session.commit()

    print(f"Imported portfolio {args.portfolio_id} ({portfolio_name}) from {csv_path}")
    print(f"Trade date: {imported_trade_date.isoformat()}")
    print(f"Transactions imported: {len(rows) + 1}")
    print(f"Seed cash: {float(total_gross_amount):,.2f} CNY")
    print("Portfolio Daily recomputation was requested by the committed fact changes.")
    print("NAV, daily change, and holdings become available only after worker publication.")


if __name__ == "__main__":
    main()
