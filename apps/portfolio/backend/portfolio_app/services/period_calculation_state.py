"""Published accounting inputs for arbitrary performance periods.

Prices and FX are selected by the daily valuation kernel. Readers consume
those selections and replay only the period's lot events, never market history.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select

from portfolio_app.db.models import PortfolioDailySnapshotModel
from portfolio_app.db.session import get_session_factory


LOT_FIELDS = (
    "opened_by_transaction_id", "account_id", "position_reference_id",
    "instrument_id", "instrument_ref", "derivative_contract_id",
    "derivative_contract", "currency", "remaining_quantity", "position_side", "_fifo_order",
)


def boundary_lots(position_lots):
    result = {"long": [], "short": []}
    for source in position_lots:
        if source.get("status") != "open":
            continue
        side = "short" if source.get("position_side") == "short" else "long"
        lot = {key: deepcopy(source.get(key)) for key in LOT_FIELDS}
        lot["remaining_quantity"] = abs(float(source.get("remaining_quantity") or 0))
        if lot["remaining_quantity"] > 1e-9:
            result[side].append(lot)
    return result


@dataclass
class PeriodCalculationInputs:
    snapshots: list[dict[str, object]]
    states: dict[date, dict[str, object]]
    lot_events: dict[str, list[dict[str, object]]] = field(default_factory=lambda: {"long": [], "short": []})
    corporate_actions: list[dict[str, object]] = field(default_factory=list)
    option_events: list[dict[str, object]] = field(default_factory=list)
    cash_components: list[dict[str, object]] = field(default_factory=list)
    risk_instrument_ids: list[str] = field(default_factory=list)
    effective_start_date: date | None = None
    effective_end_date: date | None = None

    def snapshot(self, day):
        return next((item for item in self.snapshots if item.get("as_of_date") == day), None)

    def boundary_snapshot(self, day, *, close):
        if close:
            return self.snapshot(day)
        return self.states[day]["bod_snapshot"]

    def seed_lots(self, day, side, *, opening_only=False):
        key = "opening_lots" if opening_only else "open_lots"
        return deepcopy((self.states.get(day, {}).get(key) or {}).get(side, []))

    def valuation_point(self, instrument_id, day):
        return (self.states[day].get("valuation_points") or {}).get(instrument_id)

    def convert(self, amount, *, day, currency):
        resolution = (self.states[day].get("fx_rates") or {}).get(currency)
        if resolution is None:
            return None, False
        return amount * float(resolution["rate"]), bool(resolution.get("stale"))

    def transaction_buckets(self, *, start_date, end_date, include_start_date):
        result = {key: 0.0 for key in (
            "deposits", "withdrawals", "earnings", "fees", "taxes", "return_of_capital_amount",
        )}
        result.update(coverage_complete=True, stale_fx_flag=False)
        for component in self.cash_components:
            day = date.fromisoformat(str(component["as_of_date"]))
            if day > end_date or (day < start_date if include_start_date else day <= start_date):
                continue
            value = component.get("value")
            if value is None:
                result["coverage_complete"] = False
            else:
                result[str(component["field"])] += float(value)
                result["stale_fx_flag"] = result["stale_fx_flag"] or bool(component.get("stale"))
        result["net_external_inflow"] = result["deposits"] - result["withdrawals"]
        return result


def load_period_calculation_inputs(portfolio_id, *, start_date=None, end_date=None, prebuilt_snapshots=None):
    from portfolio_app.services import daily_snapshots, return_chain

    daily_snapshots.ensure_portfolio_daily_snapshots(portfolio_id)
    snapshots = prebuilt_snapshots if prebuilt_snapshots is not None else daily_snapshots.list_materialized_daily_snapshots(
        portfolio_id, end_date=end_date, ensure_current=False,
    )
    window = return_chain.resolve_reliable_snapshot_window(
        snapshots, requested_start_date=start_date, requested_end_date=end_date,
        default_end_date=max((item["as_of_date"] for item in snapshots), default=None),
    )
    first, last = window.get("effective_start_date"), window.get("effective_end_date")
    if first is None or last is None:
        # Empty contribution periods retain the existing risk-profile contract:
        # no start boundary, with observations assessed through today. This
        # includes instruments disposed before the requested (empty) period.
        # The terminal missing-valuation marker has no calculation state;
        # read the published NAV prefix without queuing the same data gap again.
        model = PortfolioDailySnapshotModel
        with get_session_factory()() as session:
            event_ids = session.scalars(select(
                model.calculation_state_json["risk_instrument_ids"],
            ).where(
                model.portfolio_id == portfolio_id, model.as_of_date <= date.today(),
                model.nav.is_not(None),
            )).all()
            end_lots = session.scalar(select(
                model.calculation_state_json["open_lots"],
            ).where(
                model.portfolio_id == portfolio_id, model.as_of_date <= date.today(),
                model.nav.is_not(None),
            ).order_by(model.as_of_date.desc()).limit(1))
        if any(ids is None for ids in event_ids):
            daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations([portfolio_id])
            raise daily_snapshots.PortfolioCalculationPending(portfolio_id, status="stale")
        risk_ids = {str(instrument_id) for ids in event_ids for instrument_id in ids}
        for lots in (end_lots or {}).values():
            risk_ids.update(str(lot["instrument_id"]) for lot in lots if lot.get("instrument_id"))
        return PeriodCalculationInputs(snapshots, {}, risk_instrument_ids=sorted(risk_ids))
    boundary_dates = {first, first - timedelta(days=1), last}
    model = PortfolioDailySnapshotModel
    with get_session_factory()() as session:
        states = {
            day: state for day, state in session.execute(
                select(model.as_of_date, model.calculation_state_json).where(
                    model.portfolio_id == portfolio_id, model.as_of_date.in_(boundary_dates),
                )
            )
        }
        # JSON projection keeps the daily open-lot arrays out of range reads.
        events = session.execute(select(
            model.as_of_date,
            model.calculation_state_json["lot_events"],
            model.calculation_state_json["corporate_actions"],
            model.calculation_state_json["option_events"],
            model.calculation_state_json["cash_components"],
            model.calculation_state_json["risk_instrument_ids"],
        ).where(
            model.portfolio_id == portfolio_id,
            model.as_of_date >= first, model.as_of_date <= last,
        ).order_by(model.as_of_date)).all()
    if any(not isinstance(state, dict) for state in states.values()) or first not in states or last not in states:
        daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations([portfolio_id])
        raise daily_snapshots.PortfolioCalculationPending(portfolio_id, status="stale")
    inputs = PeriodCalculationInputs(snapshots, states, effective_start_date=first, effective_end_date=last)
    risk_ids = set()
    for day in (first, last):
        for lots in (states[day].get("open_lots") or {}).values():
            risk_ids.update(str(lot["instrument_id"]) for lot in lots if lot.get("instrument_id"))
    for day, lots, actions, options, components, event_instrument_ids in events:
        if lots is None:
            daily_snapshots.enqueue_selected_portfolio_daily_snapshot_recalculations([portfolio_id])
            raise daily_snapshots.PortfolioCalculationPending(portfolio_id, status="stale")
        for side in ("long", "short"):
            inputs.lot_events[side].extend((lots or {}).get(side, []))
        inputs.corporate_actions.extend(actions or [])
        inputs.option_events.extend(options or [])
        inputs.cash_components.extend(components or [])
        risk_ids.update(event_instrument_ids or [])
    inputs.risk_instrument_ids = sorted(risk_ids)
    return inputs
