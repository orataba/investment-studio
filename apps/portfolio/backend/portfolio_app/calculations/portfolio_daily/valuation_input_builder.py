"""Build the immutable valuation book from a verified sealed manifest.

The adapter is intentionally strict.  It does not resolve prices or FX again,
and it does not reinterpret an incomplete capture.  Instead it verifies the
typed decisions frozen in the manifest, reconstructs exact market-data
lineage, and fails before valuation if the evidence does not close.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from portfolio_app.calculations.portfolio_daily.ledger_contracts import FactLineage
from portfolio_app.calculations.portfolio_daily.sealed_manifest import (
    SealedPortfolioDailyManifest,
)
from portfolio_app.calculations.portfolio_daily.valuation_contracts import (
    DailyValuationBook,
    FxValuationFact,
    InstrumentValuationFact,
    MarketFactStatus,
    QuoteValuationFact,
    ValuationReasonCode,
    exact_decimal_product,
    exact_decimal_subtract,
)
from portfolio_ops_instrument_core.canonical_fx import effective_fx_leg_rate


CONFIG_TABLE = "portfolio_daily_config_input"
ACCOUNT_TABLE = "portfolio_daily_account_input"
TRANSACTION_TABLE = "portfolio_daily_transaction_input"
INSTRUMENT_TABLE = "portfolio_daily_instrument_input"
QUOTE_WINDOW_TABLE = "portfolio_daily_quote_window"
QUOTE_CANDIDATE_TABLE = "portfolio_daily_quote_candidate"
FX_PATH_TABLE = "portfolio_daily_fx_path"
FX_LEG_TABLE = "portfolio_daily_fx_leg"


class ValuationInputBuildError(RuntimeError):
    """A sealed valuation decision is internally inconsistent."""


def _fail(message: str) -> None:
    raise ValuationInputBuildError(message)


def _required(row: Mapping[str, object], field: str, *, source: str) -> object:
    if field not in row:
        _fail(f"{source}.{field} is absent")
    return row[field]


def _text(value: object, *, field: str, source: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"{source}.{field} must be a non-empty canonical string")
    return cast(str, value)


def _currency(value: object, *, field: str, source: str) -> str:
    resolved = _text(value, field=field, source=source)
    if (
        len(resolved) != 3
        or resolved != resolved.upper()
        or not resolved.isascii()
        or not resolved.isalpha()
    ):
        _fail(f"{source}.{field} must be a three-letter uppercase currency")
    return resolved


def _date(value: object, *, field: str, source: str) -> date:
    if type(value) is not date:
        _fail(f"{source}.{field} must be a date")
    return cast(date, value)


def _decimal(
    value: object,
    *,
    field: str,
    source: str,
    positive: bool = False,
) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        _fail(f"{source}.{field} must be a finite Decimal")
    resolved = cast(Decimal, value)
    if positive and resolved <= 0:
        _fail(f"{source}.{field} must be positive")
    return resolved


def _integer(
    value: object,
    *,
    field: str,
    source: str,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"{source}.{field} must be an integer >= {minimum}")
    return cast(int, value)


def _boolean(value: object, *, field: str, source: str) -> bool:
    if type(value) is not bool:
        _fail(f"{source}.{field} must be boolean")
    return cast(bool, value)


def _uuid_text(value: object, *, field: str, source: str) -> str:
    if not isinstance(value, UUID):
        _fail(f"{source}.{field} must be a UUID")
    return str(value)


def _reasons(value: object, *, field: str, source: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item or item != item.strip()
        for item in value
    ):
        _fail(f"{source}.{field} must be a canonical string array")
    resolved = tuple(cast(Sequence[str], value))
    if resolved != tuple(sorted(set(resolved))):
        _fail(f"{source}.{field} must be sorted and unique")
    return resolved


def _calendar_dates(start: date, end: date) -> tuple[date, ...]:
    if end < start:
        _fail("valuation range is inverted")
    return tuple(date.fromordinal(day) for day in range(start.toordinal(), end.toordinal() + 1))


def _one_config(manifest: SealedPortfolioDailyManifest) -> tuple[date, date, str]:
    rows = manifest.dependencies.rows_by_table[CONFIG_TABLE]
    if len(rows) != 1:
        _fail("a sealed Portfolio Daily manifest must contain exactly one config row")
    row = rows[0]
    source = CONFIG_TABLE
    start = _date(_required(row, "range_start", source=source), field="range_start", source=source)
    end = _date(
        _required(row, "effective_as_of", source=source),
        field="effective_as_of",
        source=source,
    )
    if end != manifest.effective_as_of:
        _fail("config effective_as_of differs from the sealed run")
    base_currency = _currency(
        _required(row, "base_currency", source=source),
        field="base_currency",
        source=source,
    )
    return start, end, base_currency


def _instrument_facts(
    rows: Sequence[Mapping[str, object]],
) -> tuple[tuple[InstrumentValuationFact, ...], frozenset[str]]:
    facts: list[InstrumentValuationFact] = []
    required_quotes: set[str] = set()
    seen: set[str] = set()
    for row in rows:
        source = INSTRUMENT_TABLE
        instrument_id = _text(
            _required(row, "instrument_id", source=source),
            field="instrument_id",
            source=source,
        )
        if instrument_id in seen:
            _fail(f"duplicate instrument fact {instrument_id}")
        seen.add(instrument_id)
        requires_valuation = _boolean(
            _required(row, "requires_valuation", source=source),
            field="requires_valuation",
            source=source,
        )
        if requires_valuation:
            required_quotes.add(instrument_id)
        state = _text(
            _required(row, "valuation_contract_state", source=source),
            field="valuation_contract_state",
            source=source,
        )
        reasons = _reasons(
            _required(row, "valuation_contract_reason_codes", source=source),
            field="valuation_contract_reason_codes",
            source=source,
        )
        available = state == "available"
        if state not in {"available", "unavailable"}:
            _fail(f"unsupported valuation contract state {state}")
        price_unit = row.get("price_unit")
        multiplier = row.get("contract_multiplier")
        factor = row.get("price_factor")
        if available:
            price_unit = _text(price_unit, field="price_unit", source=source)
            multiplier = _decimal(
                multiplier,
                field="contract_multiplier",
                source=source,
                positive=True,
            )
            factor = _decimal(
                factor,
                field="price_factor",
                source=source,
                positive=True,
            )
            if reasons:
                _fail(f"available instrument {instrument_id} has reason codes")
        else:
            price_unit = None if price_unit is None else _text(
                price_unit, field="price_unit", source=source
            )
            multiplier = None if multiplier is None else _decimal(
                multiplier, field="contract_multiplier", source=source, positive=True
            )
            factor = None if factor is None else _decimal(
                factor, field="price_factor", source=source, positive=True
            )
            if not reasons:
                _fail(f"unavailable instrument {instrument_id} has no reason code")
        facts.append(
            InstrumentValuationFact(
                instrument_id=instrument_id,
                currency=_currency(
                    _required(row, "currency", source=source),
                    field="currency",
                    source=source,
                ),
                price_unit=cast(str | None, price_unit),
                contract_multiplier=cast(Decimal | None, multiplier),
                price_factor=cast(Decimal | None, factor),
                available=available,
                reason_codes=reasons,
            )
        )
    facts.sort(key=lambda value: value.instrument_id)
    return tuple(facts), frozenset(required_quotes)


def _quote_facts(
    *,
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
    required_instruments: frozenset[str],
    valuation_dates: tuple[date, ...],
) -> tuple[QuoteValuationFact, ...]:
    candidates: dict[UUID, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows_by_table[QUOTE_CANDIDATE_TABLE]:
        source = QUOTE_CANDIDATE_TABLE
        window_id_value = _required(row, "quote_window_id", source=source)
        if not isinstance(window_id_value, UUID):
            _fail(f"{source}.quote_window_id must be a UUID")
        candidates[window_id_value].append(row)

    facts: list[QuoteValuationFact] = []
    seen: set[tuple[date, str]] = set()
    seen_window_ids: set[UUID] = set()
    for window in rows_by_table[QUOTE_WINDOW_TABLE]:
        source = QUOTE_WINDOW_TABLE
        window_id_value = _required(window, "quote_window_id", source=source)
        if not isinstance(window_id_value, UUID):
            _fail(f"{source}.quote_window_id must be a UUID")
        window_id = window_id_value
        if window_id in seen_window_ids:
            _fail(f"duplicate quote window {window_id}")
        seen_window_ids.add(window_id)
        instrument_id = _text(
            _required(window, "instrument_id", source=source),
            field="instrument_id",
            source=source,
        )
        valuation_date = _date(
            _required(window, "valuation_date", source=source),
            field="valuation_date",
            source=source,
        )
        key = (valuation_date, instrument_id)
        if key in seen:
            _fail(f"duplicate quote valuation fact {key}")
        seen.add(key)
        window_candidates = sorted(
            candidates.get(window_id, ()),
            key=lambda row: _integer(
                _required(row, "candidate_rank", source=QUOTE_CANDIDATE_TABLE),
                field="candidate_rank",
                source=QUOTE_CANDIDATE_TABLE,
                minimum=1,
            ),
        )
        candidate_count = _integer(
            _required(window, "candidate_count", source=source),
            field="candidate_count",
            source=source,
        )
        adopted_count = _integer(
            _required(window, "adopted_count", source=source),
            field="adopted_count",
            source=source,
        )
        if candidate_count != len(window_candidates):
            _fail(f"quote window {window_id} candidate count does not close")
        adopted = [
            row
            for row in window_candidates
            if row.get("decision") == "adopted"
        ]
        if adopted_count != len(adopted) or adopted_count not in {0, 1}:
            _fail(f"quote window {window_id} adopted count does not close")
        selection = _text(
            _required(window, "selection_status", source=source),
            field="selection_status",
            source=source,
        )
        currency = _currency(
            _required(window, "quote_currency", source=source),
            field="quote_currency",
            source=source,
        )
        reasons = _reasons(
            _required(window, "reason_codes", source=source),
            field="reason_codes",
            source=source,
        )
        if selection == "unavailable":
            if adopted or not reasons:
                _fail(f"unavailable quote window {window_id} has invalid evidence")
            facts.append(
                QuoteValuationFact(
                    as_of_date=valuation_date,
                    instrument_id=instrument_id,
                    currency=currency,
                    status=MarketFactStatus.UNAVAILABLE,
                    price=None,
                    lineage=None,
                    reason_codes=reasons,
                )
            )
            continue
        if selection != "selected" or len(adopted) != 1 or reasons:
            _fail(f"selected quote window {window_id} has invalid evidence")
        candidate = adopted[0]
        candidate_source = f"{QUOTE_CANDIDATE_TABLE}[{window_id}]"
        decision_reason = _text(
            _required(candidate, "decision_reason_code", source=candidate_source),
            field="decision_reason_code",
            source=candidate_source,
        )
        observation_date = _date(
            _required(candidate, "observation_date", source=candidate_source),
            field="observation_date",
            source=candidate_source,
        )
        if decision_reason == "adopted_exact":
            if observation_date != valuation_date:
                _fail(f"exact quote {window_id} is not from the valuation date")
            status = MarketFactStatus.FRESH
            fact_reasons: tuple[str, ...] = ()
        elif decision_reason == "adopted_carry_forward":
            if observation_date >= valuation_date:
                _fail(f"carried quote {window_id} is not older than the valuation date")
            status = MarketFactStatus.CARRY_FORWARD
            fact_reasons = (ValuationReasonCode.MARKET_DATA_QUOTE_CARRIED.value,)
        else:
            _fail(f"adopted quote {window_id} has unsupported decision reason")
        rank = _integer(
            _required(candidate, "candidate_rank", source=candidate_source),
            field="candidate_rank",
            source=candidate_source,
            minimum=1,
        )
        revision_id = _uuid_text(
            _required(candidate, "revision_id", source=candidate_source),
            field="revision_id",
            source=candidate_source,
        )
        facts.append(
            QuoteValuationFact(
                as_of_date=valuation_date,
                instrument_id=instrument_id,
                currency=currency,
                status=status,
                price=_decimal(
                    _required(candidate, "quote_value", source=candidate_source),
                    field="quote_value",
                    source=candidate_source,
                    positive=True,
                ),
                lineage=FactLineage(
                    source_record_id=_uuid_text(
                        _required(candidate, "observation_id", source=candidate_source),
                        field="observation_id",
                        source=candidate_source,
                    ),
                    source_revision_id=revision_id,
                    manifest_fact_key=(
                        f"{QUOTE_CANDIDATE_TABLE}/{window_id}/{rank}/{revision_id}"
                    ),
                ),
                reason_codes=fact_reasons,
            )
        )
    orphaned = set(candidates) - seen_window_ids
    if orphaned:
        _fail(f"quote candidates reference absent windows: {sorted(map(str, orphaned))}")
    expected = {
        (valuation_date, instrument_id)
        for valuation_date in valuation_dates
        for instrument_id in required_instruments
    }
    if seen != expected:
        _fail(
            "quote valuation grid mismatch: missing="
            f"{sorted(expected - seen)}, unexpected={sorted(seen - expected)}"
        )
    facts.sort(key=lambda value: (value.as_of_date, value.instrument_id))
    return tuple(facts)


def _fx_facts(
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
) -> tuple[FxValuationFact, ...]:
    legs: dict[UUID, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows_by_table[FX_LEG_TABLE]:
        path_id_value = _required(row, "fx_path_id", source=FX_LEG_TABLE)
        if not isinstance(path_id_value, UUID):
            _fail(f"{FX_LEG_TABLE}.fx_path_id must be a UUID")
        legs[path_id_value].append(row)

    facts: list[FxValuationFact] = []
    seen_path_ids: set[UUID] = set()
    seen_keys: set[tuple[date, str, str]] = set()
    for path in rows_by_table[FX_PATH_TABLE]:
        source = FX_PATH_TABLE
        path_id_value = _required(path, "fx_path_id", source=source)
        if not isinstance(path_id_value, UUID):
            _fail(f"{source}.fx_path_id must be a UUID")
        path_id = path_id_value
        if path_id in seen_path_ids:
            _fail(f"duplicate FX path {path_id}")
        seen_path_ids.add(path_id)
        valuation_date = _date(
            _required(path, "valuation_date", source=source),
            field="valuation_date",
            source=source,
        )
        from_currency = _currency(
            _required(path, "from_currency", source=source),
            field="from_currency",
            source=source,
        )
        to_currency = _currency(
            _required(path, "to_currency", source=source),
            field="to_currency",
            source=source,
        )
        key = (valuation_date, from_currency, to_currency)
        if key in seen_keys:
            _fail(f"duplicate FX valuation fact {key}")
        seen_keys.add(key)
        path_legs = sorted(
            legs.get(path_id, ()),
            key=lambda row: _integer(
                _required(row, "leg_order", source=FX_LEG_TABLE),
                field="leg_order",
                source=FX_LEG_TABLE,
                minimum=1,
            ),
        )
        leg_count = _integer(
            _required(path, "leg_count", source=source),
            field="leg_count",
            source=source,
        )
        if leg_count != len(path_legs):
            _fail(f"FX path {path_id} leg count does not close")
        resolution = _text(
            _required(path, "resolution_status", source=source),
            field="resolution_status",
            source=source,
        )
        path_kind = _text(
            _required(path, "path_kind", source=source),
            field="path_kind",
            source=source,
        )
        path_reasons = _reasons(
            _required(path, "reason_codes", source=source),
            field="reason_codes",
            source=source,
        )
        if resolution == "unavailable":
            if path.get("resolved_rate") is not None or not path_reasons:
                _fail(f"unavailable FX path {path_id} has invalid evidence")
            facts.append(
                FxValuationFact(
                    as_of_date=valuation_date,
                    from_currency=from_currency,
                    to_currency=to_currency,
                    status=MarketFactStatus.UNAVAILABLE,
                    rate=None,
                    lineages=(),
                    reason_codes=path_reasons,
                )
            )
            continue
        if resolution != "resolved":
            _fail(f"FX path {path_id} has unsupported resolution status")
        rate = _decimal(
            _required(path, "resolved_rate", source=source),
            field="resolved_rate",
            source=source,
            positive=True,
        )
        if path_kind not in {"identity", "direct", "inverse", "cross"}:
            _fail(f"resolved FX path {path_id} has unsupported path kind")
        if path_kind == "identity":
            if from_currency != to_currency or path_legs or rate != Decimal("1"):
                _fail(f"identity FX path {path_id} is malformed")
        elif from_currency == to_currency or not path_legs:
            _fail(f"non-identity FX path {path_id} is malformed")
        elif path_kind in {"direct", "inverse"} and len(path_legs) != 1:
            _fail(f"single-market FX path {path_id} must contain one leg")
        elif path_kind == "cross" and len(path_legs) < 2:
            _fail(f"cross FX path {path_id} must contain at least two legs")

        effective_rates: list[Decimal] = []
        lineages: list[FactLineage] = []
        carried = bool(path_reasons)
        expected_from_currency = from_currency
        for leg in path_legs:
            leg_source = f"{FX_LEG_TABLE}[{path_id}]"
            order = _integer(
                _required(leg, "leg_order", source=leg_source),
                field="leg_order",
                source=leg_source,
                minimum=1,
            )
            if leg.get("leg_resolution_status") != "resolved":
                _fail(f"resolved FX path {path_id} contains an unresolved leg")
            if _reasons(
                _required(leg, "reason_codes", source=leg_source),
                field="reason_codes",
                source=leg_source,
            ):
                _fail(f"resolved FX leg {path_id}/{order} has reason codes")
            leg_from_currency = _currency(
                _required(leg, "from_currency", source=leg_source),
                field="from_currency",
                source=leg_source,
            )
            leg_to_currency = _currency(
                _required(leg, "to_currency", source=leg_source),
                field="to_currency",
                source=leg_source,
            )
            if leg_from_currency != expected_from_currency:
                _fail(f"FX path {path_id} currency chain is discontinuous")
            expected_from_currency = leg_to_currency
            quoted = _decimal(
                _required(leg, "quoted_rate", source=leg_source),
                field="quoted_rate",
                source=leg_source,
                positive=True,
            )
            effective = _decimal(
                _required(leg, "effective_rate", source=leg_source),
                field="effective_rate",
                source=leg_source,
                positive=True,
            )
            residual = _decimal(
                _required(leg, "rate_derivation_residual_exact", source=leg_source),
                field="rate_derivation_residual_exact",
                source=leg_source,
            )
            inverted = _boolean(
                _required(leg, "is_inverted", source=leg_source),
                field="is_inverted",
                source=leg_source,
            )
            expected_effective = effective_fx_leg_rate(
                quoted,
                inverted=inverted,
            )
            if effective != expected_effective:
                _fail(
                    f"FX leg {path_id}/{order} effective rate does not match "
                    "the versioned Decimal50/HALF_EVEN operation"
                )
            expected_leg_residual = (
                exact_decimal_subtract(
                    exact_decimal_product(effective, quoted), Decimal("1")
                )
                if inverted
                else exact_decimal_subtract(effective, quoted)
            )
            if residual != expected_leg_residual:
                _fail(f"FX leg {path_id}/{order} derivation residual does not close")
            observation_date = _date(
                _required(leg, "observation_date", source=leg_source),
                field="observation_date",
                source=leg_source,
            )
            if observation_date > valuation_date:
                _fail(f"FX leg {path_id}/{order} is from the future")
            carried = carried or observation_date < valuation_date
            revision_id = _uuid_text(
                _required(leg, "revision_id", source=leg_source),
                field="revision_id",
                source=leg_source,
            )
            lineages.append(
                FactLineage(
                    source_record_id=_uuid_text(
                        _required(leg, "observation_id", source=leg_source),
                        field="observation_id",
                        source=leg_source,
                    ),
                    source_revision_id=revision_id,
                    manifest_fact_key=f"{FX_LEG_TABLE}/{path_id}/{order}/{revision_id}",
                )
            )
            effective_rates.append(effective)
        if expected_from_currency != to_currency:
            _fail(f"FX path {path_id} does not terminate in its target currency")
        exact_path_product = exact_decimal_product(
            *(tuple(effective_rates) or (Decimal("1"),))
        )
        path_residual = _decimal(
            _required(path, "rate_derivation_residual_exact", source=source),
            field="rate_derivation_residual_exact",
            source=source,
        )
        if rate != exact_path_product or path_residual != 0:
            _fail(
                f"FX path {path_id} rate must equal its exact effective-leg "
                "product with zero composition residual"
            )
        status = MarketFactStatus.CARRY_FORWARD if carried else MarketFactStatus.FRESH
        fact_reasons = (
            tuple(
                sorted(
                    set(path_reasons).union(
                        {ValuationReasonCode.FX_PATH_CARRIED.value}
                    )
                )
            )
            if carried
            else ()
        )
        facts.append(
            FxValuationFact(
                as_of_date=valuation_date,
                from_currency=from_currency,
                to_currency=to_currency,
                status=status,
                rate=rate,
                lineages=tuple(
                    sorted(lineages, key=lambda value: value.manifest_fact_key)
                ),
                reason_codes=fact_reasons,
            )
        )
    orphaned = set(legs) - seen_path_ids
    if orphaned:
        _fail(f"FX legs reference absent paths: {sorted(map(str, orphaned))}")
    facts.sort(
        key=lambda value: (value.as_of_date, value.from_currency, value.to_currency)
    )
    return tuple(facts)


def _required_daily_fx_keys(
    *,
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
    valuation_dates: tuple[date, ...],
    base_currency: str,
) -> set[tuple[date, str, str]]:
    currencies: set[str] = set()
    for table_name in (ACCOUNT_TABLE, INSTRUMENT_TABLE):
        for row in rows_by_table[table_name]:
            if row.get("currency") is not None:
                currencies.add(
                    _currency(
                        row["currency"],
                        field="currency",
                        source=table_name,
                    )
                )
    for row in rows_by_table[TRANSACTION_TABLE]:
        if row.get("is_tombstone") is True or row.get("currency") is None:
            continue
        currencies.add(
            _currency(
                row["currency"],
                field="currency",
                source=TRANSACTION_TABLE,
            )
        )
    return {
        (valuation_date, currency, base_currency)
        for valuation_date in valuation_dates
        for currency in currencies
    }


def build_daily_valuation_book(
    manifest: SealedPortfolioDailyManifest,
) -> DailyValuationBook:
    """Verify and translate frozen market decisions without resolving anew."""

    if not isinstance(manifest, SealedPortfolioDailyManifest):
        raise ValuationInputBuildError(
            "valuation input must come from a verified sealed manifest"
        )
    start, end, base_currency = _one_config(manifest)
    valuation_dates = _calendar_dates(start, end)
    instruments, required_quotes = _instrument_facts(
        manifest.dependencies.rows_by_table[INSTRUMENT_TABLE]
    )
    fx_rates = _fx_facts(manifest.dependencies.rows_by_table)
    actual_daily_fx = {
        (fact.as_of_date, fact.from_currency, fact.to_currency)
        for fact in fx_rates
        if start <= fact.as_of_date <= end
    }
    expected_daily_fx = _required_daily_fx_keys(
        rows_by_table=manifest.dependencies.rows_by_table,
        valuation_dates=valuation_dates,
        base_currency=base_currency,
    )
    if actual_daily_fx != expected_daily_fx:
        _fail(
            "daily FX valuation grid mismatch: missing="
            f"{sorted(expected_daily_fx - actual_daily_fx)}, "
            f"unexpected={sorted(actual_daily_fx - expected_daily_fx)}"
        )
    return DailyValuationBook(
        instruments=instruments,
        quotes=_quote_facts(
            rows_by_table=manifest.dependencies.rows_by_table,
            required_instruments=required_quotes,
            valuation_dates=valuation_dates,
        ),
        fx_rates=fx_rates,
    )


__all__ = [
    "ValuationInputBuildError",
    "build_daily_valuation_book",
]
