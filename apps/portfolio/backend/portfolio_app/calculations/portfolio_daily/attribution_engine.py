"""Pure exact Portfolio Daily group-attribution engine.

The engine derives economic P&L from independently valued group boundaries
and explicit, paired movements.  It never allocates a portfolio residual to
group P&L.  Only the unavoidable precision-50 division roll-up difference is
assigned deterministically and retained as explicit evidence.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from portfolio_app.calculations.numeric import (
    CalculationNumericError,
    calculation_context,
    exact_decimal_negate,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
    require_decimal,
    require_method_decimal,
)
from portfolio_app.calculations.portfolio_daily.attribution_contracts import (
    AttributionAxis,
    AttributionContractError,
    AttributionReasonCode,
    ExactGroupAttribution,
    ExactGroupAttributionSeries,
)
from portfolio_app.calculations.portfolio_daily.ledger_state import (
    DailyLedgerSnapshot,
    LedgerEffect,
    LedgerEffectKind,
    LedgerSeriesResult,
    LedgerStatus,
)
from portfolio_app.calculations.portfolio_daily.sealed_manifest import (
    SealedPortfolioDailyManifest,
)
from portfolio_app.calculations.portfolio_daily.twr import (
    CoverageStatus,
    DailyCalculationStatus,
)
from portfolio_app.calculations.portfolio_daily.valuation_contracts import (
    ExactDailyPortfolioValuation,
    ExactPortfolioValuationSeries,
)


CASH_GROUP_KEY = "__cash__"
CASH_GROUP_LABEL = "Cash and cash equivalents"
UNCLASSIFIED_GROUP_KEY = "__unclassified__"
UNCLASSIFIED_GROUP_LABEL = "Unclassified"

_ZERO = Decimal("0")

_PAIRED_TRADE_EFFECT_KINDS = frozenset(
    {
        LedgerEffectKind.BUY_TRADE,
        LedgerEffectKind.SELL_TRADE,
        LedgerEffectKind.MATURITY_REDEMPTION,
    }
)
_PAIRED_TRANSFER_EFFECT_KINDS = frozenset(
    {
        LedgerEffectKind.CASH_TRANSFER,
        LedgerEffectKind.POSITION_TRANSFER,
        LedgerEffectKind.FX_CONVERSION,
    }
)
_TAGGED_ECONOMIC_EFFECT_KINDS = frozenset(
    {
        LedgerEffectKind.INCOME_RECOGNITION,
        LedgerEffectKind.RETURN_OF_CAPITAL,
        LedgerEffectKind.EXPENSE_RECOGNITION,
    }
)
_INTRA_GROUP_OR_NON_MOVEMENT_EFFECT_KINDS = frozenset(
    {
        LedgerEffectKind.OPENING_CASH,
        LedgerEffectKind.OPENING_POSITION,
        LedgerEffectKind.CASH_SETTLEMENT,
        LedgerEffectKind.EXTERNAL_FLOW,
        LedgerEffectKind.SPLIT,
        LedgerEffectKind.DIVIDEND_REINVESTMENT,
    }
)
if (
    _PAIRED_TRADE_EFFECT_KINDS
    | _PAIRED_TRANSFER_EFFECT_KINDS
    | _TAGGED_ECONOMIC_EFFECT_KINDS
    | _INTRA_GROUP_OR_NON_MOVEMENT_EFFECT_KINDS
) != frozenset(LedgerEffectKind):
    raise RuntimeError(
        "every LedgerEffectKind requires an explicit attribution treatment"
    )


class AttributionEngineError(AttributionContractError):
    """Sealed facts cannot produce a canonical group attribution."""


class _MovementUnavailable(AttributionEngineError):
    pass


@dataclass(frozen=True, slots=True)
class _Component:
    account_id: str
    currency: str
    instrument_id: str | None
    is_cash: bool


@dataclass(frozen=True, slots=True)
class _Taxonomy:
    taxonomy_id: str
    primary_scope: str
    node_labels: Mapping[str, str]
    assignments: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _ReturnPeriod:
    """Reliable anchor and every ledger/valuation day in ``(anchor, end]``."""

    anchor: ExactDailyPortfolioValuation
    snapshots: tuple[DailyLedgerSnapshot, ...]
    days: tuple[ExactDailyPortfolioValuation, ...]
    previous_days: tuple[ExactDailyPortfolioValuation, ...]

    def __post_init__(self) -> None:
        if not self.days or not (
            len(self.snapshots) == len(self.days) == len(self.previous_days)
        ):
            raise AttributionEngineError(
                "return period requires aligned non-empty daily slices"
            )
        dates = tuple(day.as_of_date for day in self.days)
        if dates != tuple(snapshot.state.as_of_date for snapshot in self.snapshots):
            raise AttributionEngineError(
                "return-period ledger and valuation dates must align"
            )
        if any(
            previous.as_of_date >= day.as_of_date
            for previous, day in zip(self.previous_days, self.days, strict=True)
        ):
            raise AttributionEngineError(
                "return-period movement boundaries must be chronological"
            )
        expected_previous_dates = (
            self.anchor.as_of_date,
            *(day.as_of_date for day in self.days[:-1]),
        )
        if tuple(day.as_of_date for day in self.previous_days) != (
            expected_previous_dates
        ):
            raise AttributionEngineError(
                "each return-period movement must use its immediately-prior valuation boundary"
            )


class _ManifestView:
    def __init__(self, manifest: SealedPortfolioDailyManifest) -> None:
        if not isinstance(manifest, SealedPortfolioDailyManifest):
            raise AttributionEngineError(
                "manifest must be a SealedPortfolioDailyManifest"
            )
        rows = manifest.dependencies.rows_by_table
        configs = tuple(rows["portfolio_daily_config_input"])
        if len(configs) != 1:
            raise AttributionEngineError(
                "attribution requires exactly one frozen portfolio config"
            )
        config = configs[0]
        self.portfolio_id = manifest.portfolio_id
        self.base_currency = self._currency(config.get("base_currency"))
        self.account_labels = {
            self._text(row.get("account_id"), "account_id"): self._label(
                row.get("account_name"),
                fallback=self._text(row.get("account_id"), "account_id"),
            )
            for row in rows["portfolio_daily_account_input"]
        }
        self.instrument_labels = {
            self._text(row.get("instrument_id"), "instrument_id"): self._label(
                row.get("instrument_name"),
                fallback=self._text(row.get("instrument_id"), "instrument_id"),
            )
            for row in rows["portfolio_daily_instrument_input"]
        }
        self.fx_rates: dict[tuple[date, str], Decimal] = {}
        for row in rows["portfolio_daily_fx_path"]:
            if row.get("resolution_status") != "resolved":
                continue
            valuation_date = row.get("valuation_date")
            if type(valuation_date) is not date:
                raise AttributionEngineError(
                    "frozen FX path valuation_date must be a date"
                )
            from_currency = self._currency(row.get("from_currency"))
            if self._currency(row.get("to_currency")) != self.base_currency:
                continue
            rate = require_decimal(
                row.get("resolved_rate"),
                field_name="resolved_rate",
            )
            if rate <= 0:
                raise AttributionEngineError("resolved FX rate must be positive")
            key = valuation_date, from_currency
            prior = self.fx_rates.get(key)
            if prior is not None and prior != rate:
                raise AttributionEngineError("duplicate conflicting frozen FX path")
            self.fx_rates[key] = rate
        self.taxonomy = self._taxonomy(config)

    @staticmethod
    def _text(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise AttributionEngineError(f"frozen {field_name} must be canonical text")
        return value

    @staticmethod
    def _label(value: object, *, fallback: str) -> str:
        return value if isinstance(value, str) and value.strip() else fallback

    @staticmethod
    def _currency(value: object) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 3
            or value != value.upper()
            or not value.isascii()
            or not value.isalpha()
        ):
            raise AttributionEngineError(
                "frozen currency must be three uppercase ASCII letters"
            )
        return value

    def _taxonomy(self, config: Mapping[str, object]) -> _Taxonomy | None:
        taxonomy_id = config.get("taxonomy_id")
        if taxonomy_id is None:
            return None
        taxonomy_id = self._text(taxonomy_id, "taxonomy_id")
        canonical = config.get("canonical_config")
        if not isinstance(canonical, Mapping):
            raise AttributionEngineError(
                "configured default taxonomy requires its frozen snapshot"
            )
        snapshot = canonical.get("taxonomy")
        if not isinstance(snapshot, Mapping):
            raise AttributionEngineError(
                "configured default taxonomy snapshot is malformed"
            )
        taxonomies = snapshot.get("taxonomies")
        nodes = snapshot.get("nodes")
        assignments = snapshot.get("assignments")
        if (
            not isinstance(taxonomies, list)
            or not isinstance(nodes, list)
            or not isinstance(assignments, list)
        ):
            raise AttributionEngineError(
                "configured default taxonomy snapshot collections are malformed"
            )
        matching = [
            row
            for row in taxonomies
            if isinstance(row, Mapping) and row.get("taxonomy_id") == taxonomy_id
        ]
        if len(matching) != 1:
            raise AttributionEngineError(
                "configured default taxonomy is absent or duplicated"
            )
        primary_scope = self._text(
            matching[0].get("primary_assignment_scope"),
            "primary_assignment_scope",
        )
        if primary_scope not in {"instrument", "account", "cash_bucket"}:
            raise AttributionEngineError(
                "default taxonomy primary scope is unsupported"
            )
        node_labels: dict[str, str] = {}
        for row in nodes:
            if not isinstance(row, Mapping) or row.get("taxonomy_id") != taxonomy_id:
                continue
            if row.get("status", "active") != "active":
                continue
            node_id = self._text(row.get("taxonomy_node_id"), "taxonomy_node_id")
            if node_id in {CASH_GROUP_KEY, UNCLASSIFIED_GROUP_KEY}:
                raise AttributionEngineError(
                    "taxonomy node collides with a reserved system group"
                )
            if node_id in node_labels:
                raise AttributionEngineError("duplicate taxonomy node")
            node_labels[node_id] = self._label(row.get("node_name"), fallback=node_id)
        assignment_map: dict[str, str] = {}
        for row in assignments:
            if not isinstance(row, Mapping) or row.get("taxonomy_id") != taxonomy_id:
                continue
            if row.get("status", "active") != "active":
                continue
            if row.get("target_scope") != primary_scope:
                continue
            entity_id = self._text(row.get("target_entity_id"), "target_entity_id")
            node_id = self._text(row.get("taxonomy_node_id"), "taxonomy_node_id")
            if node_id not in node_labels:
                raise AttributionEngineError(
                    "active taxonomy assignment references an inactive or missing node"
                )
            if entity_id in assignment_map:
                raise AttributionEngineError(
                    "multiple active default-taxonomy assignments for one entity"
                )
            assignment_map[entity_id] = node_id
        return _Taxonomy(
            taxonomy_id=taxonomy_id,
            primary_scope=primary_scope,
            node_labels=node_labels,
            assignments=assignment_map,
        )

    def group(self, axis: AttributionAxis, component: _Component) -> tuple[str, str]:
        if axis is AttributionAxis.ACCOUNT:
            return (
                component.account_id,
                self.account_labels.get(component.account_id, component.account_id),
            )
        if axis is AttributionAxis.INSTRUMENT:
            if component.is_cash or component.instrument_id is None:
                return CASH_GROUP_KEY, CASH_GROUP_LABEL
            return (
                component.instrument_id,
                self.instrument_labels.get(
                    component.instrument_id,
                    component.instrument_id,
                ),
            )
        if axis is AttributionAxis.CURRENCY:
            return component.currency, component.currency
        taxonomy = self.taxonomy
        if taxonomy is None:
            return (
                (CASH_GROUP_KEY, CASH_GROUP_LABEL)
                if component.is_cash
                else (UNCLASSIFIED_GROUP_KEY, UNCLASSIFIED_GROUP_LABEL)
            )
        if taxonomy.primary_scope == "instrument":
            if component.is_cash or component.instrument_id is None:
                return CASH_GROUP_KEY, CASH_GROUP_LABEL
            entity_id = component.instrument_id
        elif taxonomy.primary_scope == "account":
            entity_id = component.account_id
        else:
            if not component.is_cash:
                return UNCLASSIFIED_GROUP_KEY, UNCLASSIFIED_GROUP_LABEL
            entity_id = component.account_id
        node_id = taxonomy.assignments.get(entity_id)
        if node_id is None:
            return UNCLASSIFIED_GROUP_KEY, UNCLASSIFIED_GROUP_LABEL
        return node_id, taxonomy.node_labels[node_id]

    def fx_rate(self, valuation_date: date, currency: str) -> Decimal | None:
        if currency == self.base_currency:
            return Decimal("1")
        return self.fx_rates.get((valuation_date, currency))


def _divide(left: Decimal, right: Decimal) -> Decimal:
    if right <= 0:
        raise AttributionEngineError(
            "group contribution requires a positive portfolio denominator"
        )
    with calculation_context():
        return left / right


def _add(target: dict[str, Decimal], key: str, value: Decimal) -> None:
    target[key] = exact_decimal_sum((target.get(key, _ZERO), value))


def _component_groups(
    view: _ManifestView,
    day: ExactDailyPortfolioValuation,
    axis: AttributionAxis,
) -> dict[str, str]:
    groups: dict[str, str] = {}
    for holding in day.holdings:
        key, label = view.group(
            axis,
            _Component(
                holding.account_id,
                holding.currency,
                holding.instrument_id,
                False,
            ),
        )
        groups[key] = label
    for balance in day.balances:
        key, label = view.group(
            axis,
            _Component(balance.account_id, balance.currency, None, True),
        )
        groups[key] = label
    return groups


def _effect_groups(
    view: _ManifestView,
    snapshot: DailyLedgerSnapshot,
    axis: AttributionAxis,
) -> dict[str, str]:
    """Return real group identities even when the period cannot be measured."""

    groups: dict[str, str] = {}
    for effect in snapshot.daily_effects:
        if (
            effect.cash_delta_local != 0
            or effect.pending_delta_local != 0
            or effect.external_flow_in_local != 0
            or effect.external_flow_out_local != 0
        ):
            key, label = view.group(
                axis,
                _Component(effect.account_id, effect.currency, None, True),
            )
            groups[key] = label
        if effect.instrument_id is not None:
            key, label = view.group(
                axis,
                _Component(
                    effect.account_id,
                    effect.currency,
                    effect.instrument_id,
                    False,
                ),
            )
            groups[key] = label
    return groups


def _boundary_values(
    view: _ManifestView,
    day: ExactDailyPortfolioValuation,
    axis: AttributionAxis,
) -> tuple[dict[str, Decimal], dict[str, str]]:
    values: dict[str, Decimal] = {}
    groups = _component_groups(view, day, axis)
    for holding in day.holdings:
        if holding.market_value_base is None:
            raise _MovementUnavailable("holding base boundary is unavailable")
        key, _ = view.group(
            axis,
            _Component(
                holding.account_id,
                holding.currency,
                holding.instrument_id,
                False,
            ),
        )
        _add(values, key, holding.market_value_base)
    for balance in day.balances:
        if balance.base_amount is None:
            raise _MovementUnavailable("cash/accrual base boundary is unavailable")
        signed = (
            balance.base_amount
            if balance.nav_sign > 0
            else exact_decimal_negate(balance.base_amount)
        )
        key, _ = view.group(
            axis,
            _Component(balance.account_id, balance.currency, None, True),
        )
        _add(values, key, signed)
    return values, groups


def _day_fx_rate(
    view: _ManifestView,
    day: ExactDailyPortfolioValuation,
    currency: str,
) -> Decimal:
    candidates: list[Decimal] = []
    for holding in day.holdings:
        if holding.currency == currency and holding.fx_rate_to_base is not None:
            candidates.append(holding.fx_rate_to_base)
    for balance in day.balances:
        if balance.currency == currency and balance.fx_rate_to_base is not None:
            candidates.append(balance.fx_rate_to_base)
    manifest_rate = view.fx_rate(day.as_of_date, currency)
    if manifest_rate is not None:
        candidates.append(manifest_rate)
    if not candidates:
        raise _MovementUnavailable(
            f"no frozen base FX measurement for {currency} on {day.as_of_date}"
        )
    rate = candidates[0]
    if any(value != rate for value in candidates[1:]):
        raise AttributionEngineError("valuation components and frozen FX path disagree")
    return rate


def _local_base_value(
    view: _ManifestView,
    day: ExactDailyPortfolioValuation,
    *,
    currency: str,
    magnitude: Decimal,
) -> Decimal:
    if magnitude < 0:
        raise AttributionEngineError("movement magnitude must not be negative")
    return exact_decimal_product(
        magnitude,
        _day_fx_rate(view, day, currency),
    )


def _position_transfer_value(
    day: ExactDailyPortfolioValuation,
    *,
    instrument_id: str,
    quantity: Decimal,
) -> Decimal:
    candidates: list[Decimal] = []
    for holding in day.holdings:
        if holding.instrument_id != instrument_id:
            continue
        if any(
            value is None
            for value in (
                holding.price,
                holding.contract_multiplier,
                holding.price_factor,
                holding.fx_rate_to_base,
            )
        ):
            continue
        assert holding.price is not None
        assert holding.contract_multiplier is not None
        assert holding.price_factor is not None
        assert holding.fx_rate_to_base is not None
        candidates.append(
            exact_decimal_product(
                quantity,
                holding.price,
                holding.contract_multiplier,
                holding.price_factor,
                holding.fx_rate_to_base,
            )
        )
    if not candidates:
        raise _MovementUnavailable(
            f"position transfer has no exact EOD value for {instrument_id}"
        )
    value = candidates[0]
    if any(candidate != value for candidate in candidates[1:]):
        raise AttributionEngineError(
            "same instrument has conflicting exact valuation contracts"
        )
    return value


def _one(
    values: tuple[LedgerEffect, ...],
    predicate: Callable[[LedgerEffect], bool],
    *,
    role: str,
) -> LedgerEffect:
    selected = tuple(value for value in values if predicate(value))
    if len(selected) != 1:
        raise AttributionEngineError(
            f"ledger event requires exactly one {role} attribution leg"
        )
    return selected[0]


def _internal_movements(
    view: _ManifestView,
    snapshot: DailyLedgerSnapshot,
    day: ExactDailyPortfolioValuation,
    previous: ExactDailyPortfolioValuation,
    axis: AttributionAxis,
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, str]]:
    flow_in: dict[str, Decimal] = {}
    flow_out: dict[str, Decimal] = {}
    groups: dict[str, str] = {}

    def pair(
        source: _Component,
        destination: _Component,
        amount: Callable[[], Decimal],
    ) -> None:
        source_key, source_label = view.group(axis, source)
        destination_key, destination_label = view.group(axis, destination)
        groups[source_key] = source_label
        groups[destination_key] = destination_label
        if source_key == destination_key:
            return
        resolved = amount()
        if resolved < 0:
            raise AttributionEngineError(
                "internal movement must be a non-negative magnitude"
            )
        _add(flow_out, source_key, resolved)
        _add(flow_in, destination_key, resolved)

    # Recognition and settlement may legitimately share an event id and an
    # effective date (for example, a T+0 trade or same-day income settlement).
    # Settlement moves one cash component into another cash component and must
    # not suppress the recognition movement used for cross-group attribution.
    by_event_kind: dict[tuple[str, LedgerEffectKind], list[LedgerEffect]] = defaultdict(
        list
    )
    for effect in snapshot.daily_effects:
        by_event_kind[(effect.event_id, effect.kind)].append(effect)
    for event_key in sorted(
        by_event_kind,
        key=lambda value: (value[0], value[1].value),
    ):
        effects = tuple(by_event_kind[event_key])
        kind = event_key[1]
        if kind is LedgerEffectKind.BUY_TRADE:
            cash = _one(
                effects,
                lambda value: value.pending_delta_local < 0,
                role="buy cash",
            )
            position = _one(
                effects,
                lambda value: value.quantity_delta > 0,
                role="buy position",
            )
            assert position.instrument_id is not None
            pair(
                _Component(cash.account_id, cash.currency, None, True),
                _Component(
                    position.account_id,
                    position.currency,
                    position.instrument_id,
                    False,
                ),
                lambda cash=cash: _local_base_value(
                    view,
                    day,
                    currency=cash.currency,
                    magnitude=exact_decimal_negate(cash.pending_delta_local),
                ),
            )
        elif kind in {
            LedgerEffectKind.SELL_TRADE,
            LedgerEffectKind.MATURITY_REDEMPTION,
        }:
            cash = _one(
                effects,
                lambda value: value.pending_delta_local > 0,
                role="disposal cash",
            )
            position = _one(
                effects,
                lambda value: value.quantity_delta < 0,
                role="disposal position",
            )
            assert position.instrument_id is not None
            pair(
                _Component(
                    position.account_id,
                    position.currency,
                    position.instrument_id,
                    False,
                ),
                _Component(cash.account_id, cash.currency, None, True),
                lambda cash=cash: _local_base_value(
                    view,
                    day,
                    currency=cash.currency,
                    magnitude=cash.pending_delta_local,
                ),
            )
        elif kind is LedgerEffectKind.CASH_TRANSFER:
            source = _one(
                effects,
                lambda value: value.cash_delta_local < 0,
                role="cash-transfer source",
            )
            destination = _one(
                effects,
                lambda value: value.cash_delta_local > 0,
                role="cash-transfer destination",
            )
            if (
                source.currency != destination.currency
                or exact_decimal_negate(source.cash_delta_local)
                != destination.cash_delta_local
            ):
                raise AttributionEngineError(
                    "cash-transfer attribution legs do not close locally"
                )
            pair(
                _Component(source.account_id, source.currency, None, True),
                _Component(
                    destination.account_id,
                    destination.currency,
                    None,
                    True,
                ),
                lambda source=source: _local_base_value(
                    view,
                    previous,
                    currency=source.currency,
                    magnitude=exact_decimal_negate(source.cash_delta_local),
                ),
            )
        elif kind is LedgerEffectKind.POSITION_TRANSFER:
            source = _one(
                effects,
                lambda value: value.quantity_delta < 0,
                role="position-transfer source",
            )
            destination = _one(
                effects,
                lambda value: value.quantity_delta > 0,
                role="position-transfer destination",
            )
            if (
                source.instrument_id is None
                or source.instrument_id != destination.instrument_id
                or source.currency != destination.currency
                or exact_decimal_negate(source.quantity_delta)
                != destination.quantity_delta
            ):
                raise AttributionEngineError(
                    "position-transfer attribution legs do not close"
                )
            pair(
                _Component(
                    source.account_id,
                    source.currency,
                    source.instrument_id,
                    False,
                ),
                _Component(
                    destination.account_id,
                    destination.currency,
                    destination.instrument_id,
                    False,
                ),
                lambda source=source: _position_transfer_value(
                    previous,
                    instrument_id=source.instrument_id or "",
                    quantity=exact_decimal_negate(source.quantity_delta),
                ),
            )
        elif kind is LedgerEffectKind.FX_CONVERSION:
            source = _one(
                effects,
                lambda value: value.pending_delta_local < 0,
                role="FX source",
            )
            destination = _one(
                effects,
                lambda value: value.pending_delta_local > 0,
                role="FX destination",
            )
            pair(
                _Component(source.account_id, source.currency, None, True),
                _Component(
                    destination.account_id,
                    destination.currency,
                    None,
                    True,
                ),
                lambda source=source: _local_base_value(
                    view,
                    day,
                    currency=source.currency,
                    magnitude=exact_decimal_negate(source.pending_delta_local),
                ),
            )
        elif kind in {
            LedgerEffectKind.INCOME_RECOGNITION,
            LedgerEffectKind.RETURN_OF_CAPITAL,
            LedgerEffectKind.EXPENSE_RECOGNITION,
        }:
            for effect in effects:
                if effect.pending_delta_local == 0 or effect.instrument_id is None:
                    continue
                cash = _Component(effect.account_id, effect.currency, None, True)
                instrument = _Component(
                    effect.account_id,
                    effect.currency,
                    effect.instrument_id,
                    False,
                )
                if effect.pending_delta_local > 0:
                    source, destination = instrument, cash
                    magnitude = effect.pending_delta_local
                else:
                    source, destination = cash, instrument
                    magnitude = exact_decimal_negate(effect.pending_delta_local)
                pair(
                    source,
                    destination,
                    lambda effect=effect, magnitude=magnitude: _local_base_value(
                        view,
                        day,
                        currency=effect.currency,
                        magnitude=magnitude,
                    ),
                )
        elif kind in {
            LedgerEffectKind.OPENING_CASH,
            LedgerEffectKind.OPENING_POSITION,
            LedgerEffectKind.CASH_SETTLEMENT,
            LedgerEffectKind.EXTERNAL_FLOW,
            LedgerEffectKind.SPLIT,
            LedgerEffectKind.DIVIDEND_REINVESTMENT,
        }:
            continue
        else:  # pragma: no cover - closed enum defense
            raise AttributionEngineError(
                f"unsupported ledger attribution effect {kind.value}"
            )
    if exact_decimal_sum(tuple(flow_in.values())) != exact_decimal_sum(
        tuple(flow_out.values())
    ):
        raise AttributionEngineError("internal group movements do not cancel exactly")
    return flow_in, flow_out, groups


def _external_flows(
    view: _ManifestView,
    snapshot: DailyLedgerSnapshot,
    axis: AttributionAxis,
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, str]]:
    flow_in: dict[str, Decimal] = {}
    flow_out: dict[str, Decimal] = {}
    groups: dict[str, str] = {}
    for effect in snapshot.daily_effects:
        if effect.kind is not LedgerEffectKind.EXTERNAL_FLOW:
            continue
        key, label = view.group(
            axis,
            _Component(effect.account_id, effect.currency, None, True),
        )
        groups[key] = label
        if (
            effect.external_flow_in_base is None
            or effect.external_flow_out_base is None
        ):
            raise _MovementUnavailable("external flow base amount is unavailable")
        if effect.external_flow_in_base:
            _add(flow_in, key, effect.external_flow_in_base)
        if effect.external_flow_out_base:
            _add(flow_out, key, effect.external_flow_out_base)
    return flow_in, flow_out, groups


def _merge_amounts(
    target: dict[str, Decimal],
    values: Mapping[str, Decimal],
) -> None:
    for key, value in values.items():
        _add(target, key, value)


def _period_external_flows(
    view: _ManifestView,
    period: _ReturnPeriod,
    axis: AttributionAxis,
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, str]]:
    flow_in: dict[str, Decimal] = {}
    flow_out: dict[str, Decimal] = {}
    groups: dict[str, str] = {}
    for snapshot in period.snapshots:
        daily_in, daily_out, daily_groups = _external_flows(view, snapshot, axis)
        _merge_amounts(flow_in, daily_in)
        _merge_amounts(flow_out, daily_out)
        groups.update(daily_groups)
    return flow_in, flow_out, groups


def _period_internal_movements(
    view: _ManifestView,
    period: _ReturnPeriod,
    axis: AttributionAxis,
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, str]]:
    flow_in: dict[str, Decimal] = {}
    flow_out: dict[str, Decimal] = {}
    groups: dict[str, str] = {}
    for snapshot, day, previous in zip(
        period.snapshots,
        period.days,
        period.previous_days,
        strict=True,
    ):
        daily_in, daily_out, daily_groups = _internal_movements(
            view,
            snapshot,
            day,
            previous,
            axis,
        )
        _merge_amounts(flow_in, daily_in)
        _merge_amounts(flow_out, daily_out)
        groups.update(daily_groups)
    if exact_decimal_sum(tuple(flow_in.values())) != exact_decimal_sum(
        tuple(flow_out.values())
    ):
        raise AttributionEngineError(
            f"{axis.value} return-period internal movements do not cancel"
        )
    return flow_in, flow_out, groups


def _period_financials(
    period: _ReturnPeriod,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal] | None:
    """Return opening, closing, external in/out and economic P&L.

    Daily book P&L is deliberately accumulated as independent evidence.  Its
    exact telescope must equal the anchor-to-endpoint monetary bridge; this
    prevents a sparse endpoint from silently dropping any gap-day economics.
    """

    endpoint = period.days[-1]
    twr = endpoint.twr
    if (
        twr.status is not DailyCalculationStatus.CALCULATED
        or twr.return_period_start_date != period.anchor.as_of_date
        or twr.return_period_day_count
        != (endpoint.as_of_date - period.anchor.as_of_date).days
        or twr.subperiod_twr_method50 is None
        or period.anchor.closing_nav is None
        or endpoint.closing_nav is None
        or any(
            not day.book_pnl.economic_measured
            or day.book_pnl.economic_pnl is None
            or day.external_flow_in is None
            or day.external_flow_out is None
            for day in period.days
        )
    ):
        return None
    if len(period.days) > 1:
        if (
            any(
                day.external_flow_in != 0 or day.external_flow_out != 0
                for day in period.days[:-1]
            )
            or endpoint.external_flow_in != 0
        ):
            raise AttributionEngineError(
                "multi-day return period contains an unmeasurable pre-endpoint external flow"
            )
    opening = period.anchor.closing_nav
    closing = endpoint.closing_nav
    external_in = exact_decimal_sum(
        tuple(
            day.external_flow_in
            for day in period.days
            if day.external_flow_in is not None
        )
    )
    external_out = exact_decimal_sum(
        tuple(
            day.external_flow_out
            for day in period.days
            if day.external_flow_out is not None
        )
    )
    economic = exact_decimal_sum(
        tuple(
            day.book_pnl.economic_pnl
            for day in period.days
            if day.book_pnl.economic_pnl is not None
        )
    )
    adjusted_beginning = exact_decimal_sum((opening, external_in))
    adjusted_ending = exact_decimal_sum((closing, external_out))
    if (
        twr.adjusted_beginning_value != adjusted_beginning
        or twr.adjusted_ending_value != adjusted_ending
        or exact_decimal_subtract(adjusted_ending, adjusted_beginning) != economic
    ):
        raise AttributionEngineError(
            "return-period TWR and accumulated economic P&L do not share one exact bridge"
        )
    return opening, closing, external_in, external_out, economic


def _unavailable_rows(
    *,
    as_of_date: date,
    axis: AttributionAxis,
    groups: Mapping[str, str],
    reasons: tuple[str, ...],
) -> tuple[ExactGroupAttribution, ...]:
    return tuple(
        ExactGroupAttribution(
            as_of_date=as_of_date,
            axis=axis,
            group_key=key,
            group_label=groups[key],
            measured=False,
            opening_value=None,
            closing_value=None,
            external_flow_in=None,
            external_flow_out=None,
            internal_flow_in=None,
            internal_flow_out=None,
            economic_pnl=None,
            contribution_method50=None,
            contribution_division_adjustment_exact=_ZERO,
            closure_residual_exact=_ZERO,
            coverage_status=CoverageStatus.UNAVAILABLE,
            reason_codes=reasons,
        )
        for key in sorted(groups)
    )


def _axis_rows(
    *,
    view: _ManifestView,
    snapshot: DailyLedgerSnapshot,
    day: ExactDailyPortfolioValuation,
    previous: ExactDailyPortfolioValuation | None,
    period: _ReturnPeriod | None,
    axis: AttributionAxis,
) -> tuple[ExactGroupAttribution, ...]:
    groups = _component_groups(view, day, axis)
    groups.update(_effect_groups(view, snapshot, axis))
    if previous is not None:
        groups.update(_component_groups(view, previous, axis))
    if period is not None:
        groups.update(_component_groups(view, period.anchor, axis))
        for period_snapshot, period_day in zip(
            period.snapshots,
            period.days,
            strict=True,
        ):
            groups.update(_component_groups(view, period_day, axis))
            groups.update(_effect_groups(view, period_snapshot, axis))
    financials = None if period is None else _period_financials(period)
    if financials is None:
        reasons = tuple(
            sorted(
                {
                    *(reason.value for reason in day.twr.reason_codes),
                    AttributionReasonCode.RETURN_PERIOD_UNAVAILABLE.value,
                }
            )
        )
        return _unavailable_rows(
            as_of_date=day.as_of_date,
            axis=axis,
            groups=groups,
            reasons=reasons,
        )
    try:
        assert period is not None
        opening, opening_groups = _boundary_values(view, period.anchor, axis)
        closing, closing_groups = _boundary_values(view, day, axis)
        ext_in, ext_out, external_groups = _period_external_flows(
            view,
            period,
            axis,
        )
        int_in, int_out, internal_groups = _period_internal_movements(
            view,
            period,
            axis,
        )
    except _MovementUnavailable:
        reasons = (AttributionReasonCode.INTERNAL_MOVEMENT_BASE_UNAVAILABLE.value,)
        return _unavailable_rows(
            as_of_date=day.as_of_date,
            axis=axis,
            groups=groups,
            reasons=reasons,
        )
    for source in (
        opening_groups,
        closing_groups,
        external_groups,
        internal_groups,
    ):
        groups.update(source)
    keys = tuple(sorted(groups))
    economic: dict[str, Decimal] = {}
    for key in keys:
        economic[key] = exact_decimal_subtract(
            exact_decimal_sum(
                (
                    closing.get(key, _ZERO),
                    ext_out.get(key, _ZERO),
                    int_out.get(key, _ZERO),
                )
            ),
            exact_decimal_sum(
                (
                    opening.get(key, _ZERO),
                    ext_in.get(key, _ZERO),
                    int_in.get(key, _ZERO),
                )
            ),
        )
    (
        portfolio_opening,
        portfolio_closing,
        portfolio_external_in,
        portfolio_external_out,
        portfolio_economic,
    ) = financials
    assert day.twr.subperiod_twr_method50 is not None
    assert day.twr.adjusted_beginning_value is not None
    denominator = day.twr.adjusted_beginning_value
    aggregate_contract = {
        "opening": (exact_decimal_sum(tuple(opening.values())), portfolio_opening),
        "closing": (exact_decimal_sum(tuple(closing.values())), portfolio_closing),
        "external_flow_in": (
            exact_decimal_sum(tuple(ext_in.values())),
            portfolio_external_in,
        ),
        "external_flow_out": (
            exact_decimal_sum(tuple(ext_out.values())),
            portfolio_external_out,
        ),
        "economic_pnl": (
            exact_decimal_sum(tuple(economic.values())),
            portfolio_economic,
        ),
    }
    for field_name, (actual, expected) in aggregate_contract.items():
        if actual != expected:
            raise AttributionEngineError(
                f"{axis.value} {field_name} does not close to portfolio"
            )
    if exact_decimal_sum(tuple(int_in.values())) != exact_decimal_sum(
        tuple(int_out.values())
    ):
        raise AttributionEngineError(f"{axis.value} internal movements do not close")
    portfolio_return = _divide(portfolio_economic, denominator)
    if portfolio_return != day.twr.subperiod_twr_method50:
        raise AttributionEngineError(
            "portfolio TWR does not equal the common return-period numerator quotient"
        )
    try:
        raw_contributions = {
            key: require_method_decimal(
                _divide(economic[key], denominator),
                field_name=f"{axis.value}[{key}].contribution_method50",
            )
            for key in keys
        }
    except CalculationNumericError:
        # Group attribution is a secondary analytical surface.  A valid
        # portfolio return can coexist with offsetting group numerators whose
        # individual quotients exceed the bounded method50 storage domain.
        # Degrade this complete axis/day slice only; all primary portfolio
        # bridge and input-integrity checks above remain fail-closed.
        return _unavailable_rows(
            as_of_date=day.as_of_date,
            axis=axis,
            groups=groups,
            reasons=(AttributionReasonCode.NUMERIC_DOMAIN_UNAVAILABLE.value,),
        )
    division_residual = exact_decimal_subtract(
        day.twr.subperiod_twr_method50,
        exact_decimal_sum(tuple(raw_contributions.values())),
    )
    division_adjustments = {key: _ZERO for key in keys}
    if division_residual != 0:
        largest = max(value.copy_abs() for value in economic.values())
        recipient = min(key for key in keys if economic[key].copy_abs() == largest)
        division_adjustments[recipient] = division_residual
    if (
        exact_decimal_sum(
            tuple(
                exact_decimal_sum(
                    (raw_contributions[key], division_adjustments[key])
                )
                for key in keys
            )
        )
        != day.twr.subperiod_twr_method50
    ):
        raise AttributionEngineError(
            f"{axis.value} exact contributions do not close to portfolio TWR"
        )
    return tuple(
        ExactGroupAttribution(
            as_of_date=day.as_of_date,
            axis=axis,
            group_key=key,
            group_label=groups[key],
            measured=True,
            opening_value=opening.get(key, _ZERO),
            closing_value=closing.get(key, _ZERO),
            external_flow_in=ext_in.get(key, _ZERO),
            external_flow_out=ext_out.get(key, _ZERO),
            internal_flow_in=int_in.get(key, _ZERO),
            internal_flow_out=int_out.get(key, _ZERO),
            economic_pnl=economic[key],
            contribution_method50=raw_contributions[key],
            contribution_division_adjustment_exact=division_adjustments[key],
            closure_residual_exact=_ZERO,
            coverage_status=CoverageStatus.COMPLETE,
            reason_codes=(),
        )
        for key in keys
    )


def calculate_exact_group_attribution(
    manifest: SealedPortfolioDailyManifest,
    ledger_series: LedgerSeriesResult,
    valuation_series: ExactPortfolioValuationSeries,
) -> ExactGroupAttributionSeries:
    """Calculate all four exact group axes from one sealed Portfolio Daily run."""

    if (
        not isinstance(ledger_series, LedgerSeriesResult)
        or ledger_series.status is not LedgerStatus.SUCCEEDED
    ):
        raise AttributionEngineError(
            "group attribution requires a succeeded ledger series"
        )
    if not isinstance(valuation_series, ExactPortfolioValuationSeries):
        raise AttributionEngineError(
            "valuation_series must be ExactPortfolioValuationSeries"
        )
    if tuple(
        snapshot.state.as_of_date for snapshot in ledger_series.snapshots
    ) != tuple(day.as_of_date for day in valuation_series.days):
        raise AttributionEngineError(
            "ledger and valuation dates must match for attribution"
        )
    view = _ManifestView(manifest)
    rows: list[ExactGroupAttribution] = []
    valuation_by_date = {day.as_of_date: day for day in valuation_series.days}
    valuation_index = {
        day.as_of_date: index for index, day in enumerate(valuation_series.days)
    }
    previous: ExactDailyPortfolioValuation | None = None
    for current_index, (snapshot, day) in enumerate(
        zip(
            ledger_series.snapshots,
            valuation_series.days,
            strict=True,
        )
    ):
        period: _ReturnPeriod | None = None
        if day.twr.status is DailyCalculationStatus.CALCULATED:
            start_date = day.twr.return_period_start_date
            if start_date is None or start_date not in valuation_by_date:
                raise AttributionEngineError(
                    "calculated return endpoint has no valuation anchor"
                )
            anchor_index = valuation_index[start_date]
            if anchor_index >= current_index:
                raise AttributionEngineError(
                    "calculated return endpoint must follow its valuation anchor"
                )
            slice_start = anchor_index + 1
            period = _ReturnPeriod(
                anchor=valuation_series.days[anchor_index],
                snapshots=ledger_series.snapshots[slice_start : current_index + 1],
                days=valuation_series.days[slice_start : current_index + 1],
                previous_days=valuation_series.days[anchor_index:current_index],
            )
        for axis in AttributionAxis:
            rows.extend(
                _axis_rows(
                    view=view,
                    snapshot=snapshot,
                    day=day,
                    previous=previous,
                    period=period,
                    axis=axis,
                )
            )
        previous = day
    return ExactGroupAttributionSeries(
        rows=tuple(
            sorted(
                rows,
                key=lambda row: (
                    row.as_of_date,
                    row.axis.value,
                    row.group_key,
                ),
            )
        )
    )


__all__ = [
    "AttributionEngineError",
    "CASH_GROUP_KEY",
    "CASH_GROUP_LABEL",
    "UNCLASSIFIED_GROUP_KEY",
    "UNCLASSIFIED_GROUP_LABEL",
    "calculate_exact_group_attribution",
]
