"""Freeze Portfolio, account, transaction, instrument, and action inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Mapping, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_app.calculations.numeric import (
    CalculationNumericError,
    canonical_hash,
    canonical_sha256_hex,
    exact_decimal_product,
    exact_decimal_subtract,
    method_decimal_divide,
    require_exact_numeric_typmod,
    require_method_decimal,
)
from portfolio_app.calculations.portfolio_daily.constants import (
    ACCOUNT_SCHEMA_VERSION,
    CONFIG_SCHEMA_VERSION,
    CORPORATE_ACTION_POLICY_VERSION,
    CORPORATE_ACTION_SCHEMA_VERSION,
    DEFAULT_VALUATION_CUTOFF_LOCAL_TIME,
    FX_CONSUMER_POLICY_VERSION,
    INSTRUMENT_SCHEMA_VERSION,
    QUOTE_FRESHNESS_POLICY_VERSION,
    QUOTE_RESOLVER_STRATEGY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
    VALUATION_CALENDAR_ID,
    VALUATION_CALENDAR_VERSION,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_account_input,
    portfolio_daily_config_input,
    portfolio_daily_corp_action_input,
    portfolio_daily_corp_action_window,
    portfolio_daily_instrument_input,
    portfolio_daily_transaction_input,
)
from portfolio_app.calculations.portfolio_daily.hashing import canonical_storage_json
from portfolio_app.calculations.portfolio_daily.input_policy import (
    InstrumentFreshnessPolicy,
    resolve_instrument_freshness_policy,
    resolve_instrument_valuation_contract,
)
from portfolio_app.db.models import (
    AccountRecordModel,
    PortfolioRecordModel,
    TaxonomyAssignmentRecordModel,
    TaxonomyNodeRecordModel,
    TaxonomyRecordModel,
    TransactionCurrentModel,
    TransactionRevisionGroupRecordModel,
    TransactionRevisionRecordModel,
)
from portfolio_ops_calculation_core.lifecycle import CapturingRun
from portfolio_ops_instrument_core.db_models import (
    CorporateActionEvent,
    Instrument,
    InstrumentIdentifier,
)
from portfolio_ops_instrument_core.quote_resolver import (
    canonical_quote_selection_policy_revision,
)


class ManifestCaptureError(RuntimeError):
    pass


_TRANSACTION_DERIVED_EVIDENCE_FIELDS = frozenset(
    {
        "consideration_evidence_state",
        "consideration_evidence_reason_codes",
        "consideration_terms_difference_exact",
        "fx_evidence_state",
        "fx_evidence_reason_codes",
        "effective_fx_rate_method50",
        "quoted_terms_difference_exact",
    }
)


@dataclass(frozen=True, slots=True)
class PortfolioDailyCapturePolicy:
    daily_market_max_age_days: int
    fund_max_age_days: int
    fx_max_age_days: int

    def __post_init__(self) -> None:
        for value in (
            self.daily_market_max_age_days,
            self.fund_max_age_days,
            self.fx_max_age_days,
        ):
            if isinstance(value, bool) or not 0 <= value <= 366:
                raise ValueError("capture freshness limits must be between 0 and 366")


@dataclass(frozen=True, slots=True)
class CommonCaptureResult:
    rows_by_table: Mapping[str, list[dict[str, object]]]
    portfolio_id: str
    base_currency: str
    valuation_timezone: str
    valuation_cutoff_policy: str
    knowledge_cutoff_at: datetime
    range_start: date
    range_end: date
    instrument_ids_for_valuation: tuple[str, ...]
    instrument_freshness: Mapping[str, InstrumentFreshnessPolicy]
    currencies: tuple[str, ...]
    acquisition_dates: tuple[date, ...]
    fx_max_age_days: int


@dataclass(frozen=True, slots=True)
class TransactionReplayCaptureResult:
    rows_by_table: Mapping[str, list[dict[str, object]]]
    portfolio_id: str
    effective_as_of: date
    range_start: date
    knowledge_cutoff_at: datetime


def _json_object(value: object, *, field_name: str) -> dict[str, object]:
    canonical = canonical_storage_json(value)
    if not isinstance(canonical, dict):
        raise ManifestCaptureError(f"{field_name} must canonicalize to an object")
    return cast(dict[str, object], canonical)


def _aware_timestamp(value: object, *, field_name: str) -> datetime:
    """Parse a source timestamp without weakening the manifest cutoff boundary."""

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value == value.strip() and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ManifestCaptureError(
                f"{field_name} must be an ISO-8601 timestamp"
            ) from exc
    else:
        raise ManifestCaptureError(f"{field_name} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ManifestCaptureError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(UTC)


def _portfolio_row(session: Session, portfolio_id: str) -> dict[str, object]:
    table = PortfolioRecordModel.__table__
    columns = (
        table.c.portfolio_id,
        table.c.base_currency,
        table.c.valuation_timezone,
        table.c.valuation_cutoff_policy,
        table.c.operating_profile,
        table.c.default_planning_taxonomy_id,
    )
    row = session.execute(
        select(*columns)
        .where(table.c.portfolio_id == portfolio_id)
        .with_for_update(read=True)
    ).mappings().one_or_none()
    if row is None:
        raise ManifestCaptureError(f"portfolio not found: {portfolio_id}")
    return dict(row)


def _account_rows(session: Session, portfolio_id: str) -> list[dict[str, object]]:
    table = AccountRecordModel.__table__
    columns = tuple(table.c[column] for column in (
        "account_id",
        "portfolio_id",
        "account_name",
        "account_type",
        "currency",
        "institution",
        "default_settlement_cash_account_id",
        "cost_basis_method",
        "allowed_instrument_types_json",
        "opened_at",
        "closed_at",
        "status",
    ))
    return [
        dict(row)
        for row in session.execute(
            select(*columns)
            .where(table.c.portfolio_id == portfolio_id)
            .order_by(table.c.account_id)
        ).mappings()
    ]


def _latest_transaction_rows(
    session: Session,
    *,
    portfolio_id: str,
    knowledge_cutoff_at: object,
) -> list[dict[str, object]]:
    revision = TransactionRevisionRecordModel.__table__
    group = TransactionRevisionGroupRecordModel.__table__
    source_rows = session.execute(
        select(*revision.c, group.c.recorded_at.label("group_recorded_at"))
        .select_from(
            revision.join(
                group,
                (group.c.portfolio_id == revision.c.portfolio_id)
                & (group.c.revision_group_id == revision.c.revision_group_id),
            )
        )
        .where(
            revision.c.portfolio_id == portfolio_id,
            group.c.recorded_at <= knowledge_cutoff_at,
        )
        .order_by(
            revision.c.transaction_id,
            revision.c.revision_number.desc(),
            revision.c.revision_id.desc(),
        )
    ).mappings()
    latest: list[dict[str, object]] = []
    seen: set[str] = set()
    input_columns = {
        column.name
        for column in portfolio_daily_transaction_input.c
        if column.name not in {
            "manifest_id",
            "run_id",
            "portfolio_id",
            "captured_at",
            *_TRANSACTION_DERIVED_EVIDENCE_FIELDS,
        }
    }
    input_columns.remove("selected_reason_code")
    for source_row in source_rows:
        transaction_id = str(source_row["transaction_id"])
        if transaction_id in seen:
            continue
        seen.add(transaction_id)
        row = {column: source_row[column] for column in input_columns}
        row["portfolio_id"] = portfolio_id
        row["selected_reason_code"] = "latest_at_knowledge_cutoff"
        latest.append(row)
    return latest


def _current_transaction_rows(
    session: Session,
    *,
    portfolio_id: str,
) -> list[dict[str, object]]:
    """Read the prospective current set in O(current facts), not O(revisions)."""

    current = TransactionCurrentModel.__table__
    revision = TransactionRevisionRecordModel.__table__
    source_rows = session.execute(
        select(
            current,
            revision.c.supersedes_revision_id,
            revision.c.supersedes_revision_number,
        )
        .select_from(
            current.join(
                revision,
                (revision.c.portfolio_id == current.c.portfolio_id)
                & (revision.c.transaction_id == current.c.transaction_id)
                & (revision.c.revision_id == current.c.current_revision_id),
            )
        )
        .where(current.c.portfolio_id == portfolio_id)
        .order_by(current.c.transaction_id)
    ).mappings()
    input_columns = tuple(
        column.name
        for column in portfolio_daily_transaction_input.c
        if column.name
        not in {
            "manifest_id",
            "run_id",
            "portfolio_id",
            "captured_at",
            "selected_reason_code",
            *_TRANSACTION_DERIVED_EVIDENCE_FIELDS,
        }
    )
    aliases = {
        "revision_id": "current_revision_id",
        "revision_number": "current_revision_number",
        "group_recorded_at": "recorded_at",
    }
    rows: list[dict[str, object]] = []
    for source_row in source_rows:
        row = {
            column: (
                False
                if column == "is_tombstone"
                else source_row[aliases.get(column, column)]
            )
            for column in input_columns
        }
        row["portfolio_id"] = portfolio_id
        row["selected_reason_code"] = "latest_at_knowledge_cutoff"
        rows.append(row)
    return rows


def _populate_transaction_evidence(
    transaction_rows: list[dict[str, object]],
    instrument_rows: list[dict[str, object]],
) -> None:
    """Attach deterministic terms evidence without changing source facts."""

    instruments = {
        str(row["instrument_id"]): row
        for row in instrument_rows
    }
    for row in transaction_rows:
        basis = row.get("consideration_basis")
        if basis is None:
            row["consideration_evidence_state"] = "not_applicable"
            row["consideration_evidence_reason_codes"] = []
            row["consideration_terms_difference_exact"] = None
        else:
            price = row.get("price")
            quantity = row.get("quantity")
            gross_amount = row.get("gross_amount")
            instrument_id = row.get("instrument_id")
            instrument = (
                instruments.get(str(instrument_id))
                if instrument_id is not None
                else None
            )
            if price is None:
                row["consideration_evidence_state"] = "unavailable"
                row["consideration_evidence_reason_codes"] = [
                    "price_not_reported"
                ]
                row["consideration_terms_difference_exact"] = None
            elif (
                not isinstance(quantity, Decimal)
                or not isinstance(gross_amount, Decimal)
                or instrument is None
                or instrument.get("valuation_contract_state") != "available"
                or not isinstance(instrument.get("contract_multiplier"), Decimal)
                or not isinstance(instrument.get("price_factor"), Decimal)
            ):
                row["consideration_evidence_state"] = "unavailable"
                row["consideration_evidence_reason_codes"] = [
                    "instrument_contract_unavailable"
                ]
                row["consideration_terms_difference_exact"] = None
            else:
                multiplier = cast(Decimal, instrument["contract_multiplier"])
                price_factor = cast(Decimal, instrument["price_factor"])
                calculated = exact_decimal_product(
                    quantity,
                    cast(Decimal, price),
                    multiplier,
                    price_factor,
                )
                difference = exact_decimal_subtract(gross_amount, calculated)
                try:
                    require_exact_numeric_typmod(
                        difference,
                        precision=84,
                        scale=26,
                        field_name="consideration_terms_difference_exact",
                    )
                except CalculationNumericError as exc:
                    raise ManifestCaptureError(
                        "transaction consideration evidence exceeds its exact domain"
                    ) from exc
                if basis == "exact_quantity_price" and difference != 0:
                    raise ManifestCaptureError(
                        "exact_quantity_price transaction does not close exactly"
                    )
                row["consideration_evidence_state"] = "complete"
                row["consideration_evidence_reason_codes"] = []
                row["consideration_terms_difference_exact"] = difference

        if row.get("transaction_type") != "fx_conversion":
            row["fx_evidence_state"] = "not_applicable"
            row["fx_evidence_reason_codes"] = []
            row["effective_fx_rate_method50"] = None
            row["quoted_terms_difference_exact"] = None
            continue
        gross_amount = row.get("gross_amount")
        counter_amount = row.get("counter_amount")
        if (
            not isinstance(gross_amount, Decimal)
            or gross_amount <= 0
            or not isinstance(counter_amount, Decimal)
            or counter_amount <= 0
        ):
            row["fx_evidence_state"] = "unavailable"
            row["fx_evidence_reason_codes"] = ["invalid_actual_fx_amounts"]
            row["effective_fx_rate_method50"] = None
            row["quoted_terms_difference_exact"] = None
            continue
        try:
            effective_rate = require_method_decimal(
                method_decimal_divide(counter_amount, gross_amount),
                field_name="effective_fx_rate_method50",
            )
        except CalculationNumericError:
            row["fx_evidence_state"] = "unavailable"
            row["fx_evidence_reason_codes"] = [
                "effective_fx_rate_out_of_domain"
            ]
            row["effective_fx_rate_method50"] = None
            row["quoted_terms_difference_exact"] = None
            continue
        row["effective_fx_rate_method50"] = effective_rate
        quoted_rate = row.get("quoted_fx_rate")
        if quoted_rate is None:
            row["fx_evidence_state"] = "partial"
            row["fx_evidence_reason_codes"] = [
                "quoted_fx_rate_not_reported"
            ]
            row["quoted_terms_difference_exact"] = None
            continue
        if not isinstance(quoted_rate, Decimal):
            raise ManifestCaptureError("quoted_fx_rate must be a Decimal")
        quoted_difference = exact_decimal_subtract(
            counter_amount,
            exact_decimal_product(gross_amount, quoted_rate),
        )
        try:
            require_exact_numeric_typmod(
                quoted_difference,
                precision=84,
                scale=26,
                field_name="quoted_terms_difference_exact",
            )
        except CalculationNumericError as exc:
            raise ManifestCaptureError(
                "transaction FX quote evidence exceeds its exact domain"
            ) from exc
        row["fx_evidence_state"] = "complete"
        row["fx_evidence_reason_codes"] = []
        row["quoted_terms_difference_exact"] = quoted_difference


def _taxonomy_snapshot(
    session: Session,
    *,
    portfolio_id: str,
) -> tuple[dict[str, object], str | None]:
    taxonomy = TaxonomyRecordModel.__table__
    node = TaxonomyNodeRecordModel.__table__
    assignment = TaxonomyAssignmentRecordModel.__table__
    taxonomy_rows = [
        dict(row)
        for row in session.execute(
            select(taxonomy)
            .where(taxonomy.c.portfolio_id == portfolio_id)
            .order_by(taxonomy.c.taxonomy_id)
        ).mappings()
    ]
    taxonomy_ids = [str(row["taxonomy_id"]) for row in taxonomy_rows]
    node_rows = (
        [
            dict(row)
            for row in session.execute(
                select(node)
                .where(node.c.taxonomy_id.in_(taxonomy_ids))
                .order_by(node.c.taxonomy_id, node.c.sort_order, node.c.taxonomy_node_id)
            ).mappings()
        ]
        if taxonomy_ids
        else []
    )
    assignment_rows = (
        [
            dict(row)
            for row in session.execute(
                select(assignment)
                .where(assignment.c.taxonomy_id.in_(taxonomy_ids))
                .order_by(
                    assignment.c.taxonomy_id,
                    assignment.c.target_scope,
                    assignment.c.target_entity_id,
                    assignment.c.assignment_id,
                )
            ).mappings()
        ]
        if taxonomy_ids
        else []
    )
    snapshot = _json_object(
        {
            "taxonomies": taxonomy_rows,
            "nodes": node_rows,
            "assignments": assignment_rows,
        },
        field_name="taxonomy_snapshot",
    )
    version = canonical_sha256_hex(snapshot) if taxonomy_rows else None
    return snapshot, version


def _instrument_rows(
    session: Session,
    *,
    portfolio_id: str,
    instrument_ids: set[str],
    valuation_instrument_ids: set[str],
    policy: PortfolioDailyCapturePolicy,
) -> tuple[
    list[dict[str, object]],
    dict[str, InstrumentFreshnessPolicy],
    dict[str, str],
]:
    if not instrument_ids:
        return [], {}, {}
    instruments = {
        str(item.instrument_id): item
        for item in session.scalars(
            select(Instrument)
            .where(Instrument.instrument_id.in_(sorted(instrument_ids)))
            .order_by(Instrument.instrument_id)
        )
    }
    missing = sorted(instrument_ids - set(instruments))
    if missing:
        raise ManifestCaptureError(
            "canonical instruments are missing: " + ", ".join(missing)
        )
    identifiers_by_instrument: dict[str, list[dict[str, object]]] = {
        instrument_id: [] for instrument_id in instrument_ids
    }
    for identifier in session.scalars(
        select(InstrumentIdentifier)
        .where(InstrumentIdentifier.instrument_id.in_(sorted(instrument_ids)))
        .order_by(
            InstrumentIdentifier.instrument_id,
            InstrumentIdentifier.identifier_type,
            InstrumentIdentifier.identifier_value,
        )
    ):
        identifiers_by_instrument[str(identifier.instrument_id)].append(
            {
                "identifier_type": identifier.identifier_type,
                "identifier_value": identifier.identifier_value,
                "is_primary": identifier.is_primary,
            }
        )

    rows: list[dict[str, object]] = []
    freshness_by_instrument: dict[str, InstrumentFreshnessPolicy] = {}
    revisions_by_instrument: dict[str, str] = {}
    for instrument_id in sorted(instrument_ids):
        instrument = instruments[instrument_id]
        instrument_type = str(instrument.instrument_type).strip().lower()
        freshness = resolve_instrument_freshness_policy(
            instrument_type=instrument_type,
            daily_market_max_age_days=policy.daily_market_max_age_days,
            fund_max_age_days=policy.fund_max_age_days,
        )
        try:
            contract = resolve_instrument_valuation_contract(
                instrument_type=instrument_type,
                source_settings=instrument.source_settings_json,
            )
        except CalculationNumericError as exc:
            raise ManifestCaptureError(
                f"instrument {instrument_id} has an invalid valuation contract: {exc}"
            ) from exc
        policy_revision = canonical_quote_selection_policy_revision(
            instrument.quote_selection_policy_json
        )
        canonical_instrument = _json_object(
            {
                "instrument_id": instrument_id,
                "instrument_name": instrument.instrument_name,
                "instrument_type": instrument_type,
                "currency": str(instrument.currency).upper(),
                "identifiers": identifiers_by_instrument[instrument_id],
                "quote_selection_policy": instrument.quote_selection_policy_json,
                "quote_selection_policy_version": QUOTE_SELECTION_POLICY_VERSION,
                "quote_selection_policy_revision": policy_revision,
                "valuation_contract": {
                    "price_unit": contract.price_unit,
                    "contract_multiplier": contract.contract_multiplier,
                    "price_factor": contract.price_factor,
                    "accrual_convention": contract.accrual_convention,
                    "state": contract.state,
                    "reason_codes": contract.reason_codes,
                    "source": contract.source,
                },
                "freshness_policy": {
                    "version": QUOTE_FRESHNESS_POLICY_VERSION,
                    "mode": freshness.mode,
                    "max_age_days": freshness.max_age_days,
                },
            },
            field_name=f"instrument[{instrument_id}]",
        )
        rows.append(
            {
                "portfolio_id": portfolio_id,
                "instrument_id": instrument_id,
                "requires_valuation": instrument_id in valuation_instrument_ids,
                "instrument_name": instrument.instrument_name,
                "instrument_type": instrument_type,
                "currency": str(instrument.currency).upper(),
                "price_unit": contract.price_unit,
                "contract_multiplier": contract.contract_multiplier,
                "accrual_convention": contract.accrual_convention,
                "price_factor": contract.price_factor,
                "valuation_contract_state": contract.state,
                "valuation_contract_reason_codes": list(contract.reason_codes),
                "valuation_factor_source": contract.source,
                "quote_policy_version": QUOTE_SELECTION_POLICY_VERSION,
                "quote_selection_policy_revision": policy_revision,
                "freshness_policy_version": QUOTE_FRESHNESS_POLICY_VERSION,
                "freshness_mode": freshness.mode,
                "freshness_max_age_days": freshness.max_age_days,
                "resolver_strategy_version": QUOTE_RESOLVER_STRATEGY_VERSION,
                "instrument_schema_version": INSTRUMENT_SCHEMA_VERSION,
                "instrument_hash": canonical_sha256_hex(canonical_instrument),
                "canonical_instrument": canonical_instrument,
            }
        )
        freshness_by_instrument[instrument_id] = freshness
        revisions_by_instrument[instrument_id] = policy_revision
    return rows, freshness_by_instrument, revisions_by_instrument


def _transaction_replay_instrument_rows(
    session: Session,
    *,
    portfolio_id: str,
    instrument_ids: set[str],
) -> list[dict[str, object]]:
    """Capture only immutable identity and exact valuation-contract terms."""

    if not instrument_ids:
        return []
    instruments = {
        str(item.instrument_id): item
        for item in session.scalars(
            select(Instrument)
            .where(Instrument.instrument_id.in_(sorted(instrument_ids)))
            .order_by(Instrument.instrument_id)
        )
    }
    missing = sorted(instrument_ids - set(instruments))
    if missing:
        raise ManifestCaptureError(
            "canonical instruments are missing: " + ", ".join(missing)
        )
    rows: list[dict[str, object]] = []
    for instrument_id in sorted(instrument_ids):
        instrument = instruments[instrument_id]
        instrument_type = str(instrument.instrument_type).strip().lower()
        contract = resolve_instrument_valuation_contract(
            instrument_type=instrument_type,
            source_settings=instrument.source_settings_json,
        )
        canonical_instrument = _json_object(
            {
                "instrument_id": instrument_id,
                "instrument_type": instrument_type,
                "currency": str(instrument.currency).upper(),
                "valuation_contract": {
                    "price_unit": contract.price_unit,
                    "contract_multiplier": contract.contract_multiplier,
                    "price_factor": contract.price_factor,
                    "state": contract.state,
                    "reason_codes": contract.reason_codes,
                    "source": contract.source,
                },
            },
            field_name=f"transaction_replay_instrument[{instrument_id}]",
        )
        rows.append(
            {
                "portfolio_id": portfolio_id,
                "instrument_id": instrument_id,
                "requires_valuation": True,
                "instrument_type": instrument_type,
                "currency": str(instrument.currency).upper(),
                "price_unit": contract.price_unit,
                "contract_multiplier": contract.contract_multiplier,
                "price_factor": contract.price_factor,
                "valuation_contract_state": contract.state,
                "valuation_contract_reason_codes": list(contract.reason_codes),
                "valuation_factor_source": contract.source,
                "instrument_schema_version": INSTRUMENT_SCHEMA_VERSION,
                "instrument_hash": canonical_sha256_hex(canonical_instrument),
                "canonical_instrument": canonical_instrument,
            }
        )
    return rows


def _corporate_action_rows(
    session: Session,
    *,
    portfolio_id: str,
    instrument_ids: set[str],
    range_start: date,
    range_end: date,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    selection_revision = canonical_hash(
        {
            "policy_version": CORPORATE_ACTION_POLICY_VERSION,
            "included_statuses": ["confirmed"],
            "supported_action_types": ["share_split"],
        }
    )
    events_by_instrument: dict[str, list[CorporateActionEvent]] = {
        instrument_id: [] for instrument_id in instrument_ids
    }
    if instrument_ids:
        for event in session.scalars(
            select(CorporateActionEvent)
            .where(
                CorporateActionEvent.instrument_id.in_(sorted(instrument_ids)),
                CorporateActionEvent.effective_date >= range_start,
                CorporateActionEvent.effective_date <= range_end,
            )
            .order_by(
                CorporateActionEvent.instrument_id,
                CorporateActionEvent.effective_date,
                CorporateActionEvent.corporate_action_event_id,
            )
        ):
            events_by_instrument[str(event.instrument_id)].append(event)

    window_rows: list[dict[str, object]] = []
    event_rows: list[dict[str, object]] = []
    for instrument_id in sorted(instrument_ids):
        events = events_by_instrument[instrument_id]
        window_rows.append(
            {
                "portfolio_id": portfolio_id,
                "instrument_id": instrument_id,
                "window_from": range_start,
                "window_to": range_end,
                "selection_policy_version": CORPORATE_ACTION_POLICY_VERSION,
                "selection_policy_revision": selection_revision,
                "consumer_policy_version": CORPORATE_ACTION_POLICY_VERSION,
                "freshness_policy_version": CORPORATE_ACTION_POLICY_VERSION,
                "freshness_mode": "transaction_snapshot",
                "freshness_max_age_days": 0,
                "resolver_strategy_version": CORPORATE_ACTION_POLICY_VERSION,
                "expected_event_count": len(events),
                "captured_event_count": len(events),
                "coverage_state": "complete",
                "reason_codes": [],
            }
        )
        for event in events:
            event_updated_at = _aware_timestamp(
                event.updated_at,
                field_name=(
                    f"corporate_action[{event.corporate_action_event_id}].updated_at"
                ),
            )
            canonical_event = _json_object(
                {
                    "corporate_action_event_id": event.corporate_action_event_id,
                    "instrument_id": event.instrument_id,
                    "action_type": event.action_type,
                    "announcement_date": event.announcement_date,
                    "record_date": event.record_date,
                    "effective_date": event.effective_date,
                    "payable_date": event.payable_date,
                    "new_units": event.new_units,
                    "old_units": event.old_units,
                    "quantity_rounding": event.quantity_rounding,
                    "quantity_precision": event.quantity_precision,
                    "cost_basis_treatment": event.cost_basis_treatment,
                    "source": event.source,
                    "external_event_id": event.external_event_id,
                    "status": event.status,
                    "provenance": event.provenance_json,
                    "created_at": event.created_at,
                    "updated_at": event.updated_at,
                },
                field_name=f"corporate_action[{event.corporate_action_event_id}]",
            )
            event_rows.append(
                {
                    "portfolio_id": portfolio_id,
                    "corporate_action_event_id": event.corporate_action_event_id,
                    "instrument_id": instrument_id,
                    "action_type": event.action_type,
                    "announcement_date": event.announcement_date,
                    "record_date": event.record_date,
                    "effective_date": event.effective_date,
                    "payable_date": event.payable_date,
                    "new_units": Decimal(event.new_units),
                    "old_units": Decimal(event.old_units),
                    "quantity_rounding": event.quantity_rounding,
                    "quantity_precision": event.quantity_precision,
                    "cost_basis_treatment": event.cost_basis_treatment,
                    "source": event.source,
                    "external_event_id": event.external_event_id,
                    "event_status": event.status,
                    "event_updated_at": event_updated_at,
                    "event_schema_version": CORPORATE_ACTION_SCHEMA_VERSION,
                    "event_hash": canonical_sha256_hex(canonical_event),
                    "canonical_event": canonical_event,
                }
            )
    return window_rows, event_rows


def _capture_common_dependencies(
    session: Session,
    *,
    portfolio_id: str,
    effective_as_of: date,
    knowledge_cutoff_at: datetime,
    policy: PortfolioDailyCapturePolicy,
) -> CommonCaptureResult:
    portfolio = _portfolio_row(session, portfolio_id)
    accounts = _account_rows(session, portfolio_id)
    if not accounts:
        raise ManifestCaptureError("Portfolio Daily requires at least one account")
    transaction_rows = _latest_transaction_rows(
        session,
        portfolio_id=portfolio_id,
        knowledge_cutoff_at=knowledge_cutoff_at,
    )
    range_end = effective_as_of
    in_range_transactions = [
        row
        for row in transaction_rows
        if not row["is_tombstone"]
        and row["trade_date"] is not None
        and cast(date, row["trade_date"]) <= range_end
    ]
    range_start = min(
        (cast(date, row["trade_date"]) for row in in_range_transactions),
        default=range_end,
    )
    all_instrument_ids = {
        str(row["instrument_id"])
        for row in transaction_rows
        if not row["is_tombstone"] and row["instrument_id"] is not None
    }
    valuation_instrument_ids = {
        str(row["instrument_id"])
        for row in in_range_transactions
        if row["instrument_id"] is not None
    }
    instrument_rows, instrument_freshness, policy_revisions = _instrument_rows(
        session,
        portfolio_id=portfolio_id,
        instrument_ids=all_instrument_ids,
        valuation_instrument_ids=valuation_instrument_ids,
        policy=policy,
    )
    _populate_transaction_evidence(transaction_rows, instrument_rows)
    taxonomy_snapshot, taxonomy_version = _taxonomy_snapshot(
        session,
        portfolio_id=portfolio_id,
    )
    portfolio_snapshot = _json_object(
        {
            "portfolio_id": portfolio_id,
            "base_currency": str(portfolio["base_currency"]).upper(),
            "valuation_timezone": portfolio["valuation_timezone"],
            "valuation_cutoff_policy": portfolio["valuation_cutoff_policy"],
            "operating_profile": portfolio["operating_profile"],
            "default_planning_taxonomy_id": portfolio[
                "default_planning_taxonomy_id"
            ],
            "taxonomy": taxonomy_snapshot,
            "quote_policy_revisions": policy_revisions,
        },
        field_name="portfolio_config",
    )
    aggregate_quote_revision = canonical_hash(
        {"instrument_quote_policy_revisions": policy_revisions}
    )
    config_row = {
        "portfolio_id": portfolio_id,
        "effective_as_of": range_end,
        "range_start": range_start,
        "knowledge_cutoff_at": knowledge_cutoff_at,
        "base_currency": str(portfolio["base_currency"]).upper(),
        "valuation_timezone": portfolio["valuation_timezone"],
        "valuation_cutoff_local_time": DEFAULT_VALUATION_CUTOFF_LOCAL_TIME,
        "valuation_cutoff_policy": portfolio["valuation_cutoff_policy"],
        "valuation_calendar_id": VALUATION_CALENDAR_ID,
        "valuation_calendar_version": VALUATION_CALENDAR_VERSION,
        "quote_policy_version": QUOTE_SELECTION_POLICY_VERSION,
        "quote_selection_policy_revision": aggregate_quote_revision,
        "freshness_policy_version": QUOTE_FRESHNESS_POLICY_VERSION,
        "freshness_mode": "per_instrument",
        "freshness_max_age_days": max(
            policy.daily_market_max_age_days,
            policy.fund_max_age_days,
            policy.fx_max_age_days,
        ),
        "quote_resolver_strategy_version": QUOTE_RESOLVER_STRATEGY_VERSION,
        "fx_policy_version": FX_CONSUMER_POLICY_VERSION,
        "corporate_action_policy_version": CORPORATE_ACTION_POLICY_VERSION,
        "taxonomy_id": portfolio["default_planning_taxonomy_id"],
        "taxonomy_version": taxonomy_version,
        "benchmark_id": None,
        "operating_profile": portfolio["operating_profile"],
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "config_hash": canonical_sha256_hex(portfolio_snapshot),
        "canonical_config": portfolio_snapshot,
    }
    account_input_rows: list[dict[str, object]] = []
    for account in accounts:
        canonical_account = _json_object(account, field_name="account")
        account_input_rows.append(
            {
                "portfolio_id": portfolio_id,
                "account_id": account["account_id"],
                "account_name": account["account_name"],
                "account_type": account["account_type"],
                "currency": str(account["currency"]).upper(),
                "institution": account["institution"],
                "default_settlement_cash_account_id": account[
                    "default_settlement_cash_account_id"
                ],
                "cost_basis_method": account["cost_basis_method"],
                "opened_at": account["opened_at"],
                "closed_at": account["closed_at"],
                "account_status": account["status"],
                "account_schema_version": ACCOUNT_SCHEMA_VERSION,
                "account_hash": canonical_sha256_hex(canonical_account),
                "canonical_account": canonical_account,
            }
        )
    corp_windows, corp_events = _corporate_action_rows(
        session,
        portfolio_id=portfolio_id,
        instrument_ids=all_instrument_ids,
        range_start=range_start,
        range_end=range_end,
    )
    currencies = {
        str(account["currency"]).upper() for account in accounts
    } | {
        str(row["currency"]).upper()
        for row in transaction_rows
        if row["currency"] is not None
    } | {
        str(row["currency"]).upper() for row in instrument_rows
    }
    currencies.add(str(portfolio["base_currency"]).upper())
    # USD is the maintained canonical FX pivot.  Capturing its path explicitly
    # makes the sealed currency universe self-contained even when every
    # portfolio fact is currently denominated in another currency.
    currencies.add("USD")
    acquisition_dates = {
        cast(date, row["acquisition_date"])
        for row in transaction_rows
        if not row["is_tombstone"]
        and row["acquisition_date"] is not None
        and cast(date, row["acquisition_date"]) <= range_end
    }
    rows_by_table = {
        portfolio_daily_config_input.name: [config_row],
        portfolio_daily_account_input.name: account_input_rows,
        portfolio_daily_transaction_input.name: transaction_rows,
        portfolio_daily_instrument_input.name: instrument_rows,
        portfolio_daily_corp_action_window.name: corp_windows,
        portfolio_daily_corp_action_input.name: corp_events,
    }
    return CommonCaptureResult(
        rows_by_table=rows_by_table,
        portfolio_id=portfolio_id,
        base_currency=str(portfolio["base_currency"]).upper(),
        valuation_timezone=str(portfolio["valuation_timezone"]),
        valuation_cutoff_policy=str(portfolio["valuation_cutoff_policy"]),
        knowledge_cutoff_at=knowledge_cutoff_at,
        range_start=range_start,
        range_end=range_end,
        instrument_ids_for_valuation=tuple(sorted(valuation_instrument_ids)),
        instrument_freshness={
            instrument_id: instrument_freshness[instrument_id]
            for instrument_id in sorted(valuation_instrument_ids)
        },
        currencies=tuple(sorted(currencies)),
        acquisition_dates=tuple(sorted(acquisition_dates)),
        fx_max_age_days=policy.fx_max_age_days,
    )


def capture_common_dependencies(
    session: Session,
    *,
    capturing: CapturingRun,
    policy: PortfolioDailyCapturePolicy,
) -> CommonCaptureResult:
    return _capture_common_dependencies(
        session,
        portfolio_id=capturing.request.scope.scope_id,
        effective_as_of=capturing.request.effective_as_of,
        knowledge_cutoff_at=capturing.cutoff_at,
        policy=policy,
    )


def capture_transaction_replay_dependencies(
    session: Session,
    *,
    portfolio_id: str,
    effective_as_of: date,
    knowledge_cutoff_at: datetime,
) -> TransactionReplayCaptureResult:
    """Capture current facts for a pre-commit local-book replay.

    This deliberately excludes taxonomy, quotes, FX, and freshness policy.
    Mutation admissibility is a local-currency books-and-records invariant and
    must not depend on unrelated research or market-data configuration.
    """

    if type(effective_as_of) is not date:
        raise ManifestCaptureError(
            "transaction replay effective_as_of must be a date"
        )
    cutoff = _aware_timestamp(
        knowledge_cutoff_at,
        field_name="transaction replay knowledge_cutoff_at",
    )
    normalized_portfolio_id = portfolio_id.strip()
    if not normalized_portfolio_id:
        raise ManifestCaptureError(
            "transaction replay portfolio_id must not be blank"
        )
    portfolio = _portfolio_row(session, normalized_portfolio_id)
    accounts = _account_rows(session, normalized_portfolio_id)
    if not accounts:
        raise ManifestCaptureError(
            "transaction replay requires at least one account"
        )
    transaction_rows = _current_transaction_rows(
        session,
        portfolio_id=normalized_portfolio_id,
    )
    live_rows = [
        row
        for row in transaction_rows
        if not row["is_tombstone"]
        and row["trade_date"] is not None
        and cast(date, row["trade_date"]) <= effective_as_of
    ]
    range_start = min(
        (cast(date, row["trade_date"]) for row in live_rows),
        default=effective_as_of,
    )
    instrument_ids = {
        str(row["instrument_id"])
        for row in live_rows
        if row["instrument_id"] is not None
    }
    instrument_rows = _transaction_replay_instrument_rows(
        session,
        portfolio_id=normalized_portfolio_id,
        instrument_ids=instrument_ids,
    )
    _populate_transaction_evidence(transaction_rows, instrument_rows)
    account_input_rows: list[dict[str, object]] = []
    for account in accounts:
        canonical_account = _json_object(
            account,
            field_name=f"transaction_replay_account[{account['account_id']}]",
        )
        account_input_rows.append(
            {
                "portfolio_id": normalized_portfolio_id,
                "account_id": account["account_id"],
                "account_type": account["account_type"],
                "currency": str(account["currency"]).upper(),
                "default_settlement_cash_account_id": account[
                    "default_settlement_cash_account_id"
                ],
                "cost_basis_method": account["cost_basis_method"],
                "opened_at": account["opened_at"],
                "closed_at": account["closed_at"],
                "account_status": account["status"],
                "account_schema_version": ACCOUNT_SCHEMA_VERSION,
                "account_hash": canonical_sha256_hex(canonical_account),
                "canonical_account": canonical_account,
            }
        )
    canonical_config = _json_object(
        {
            "portfolio_id": normalized_portfolio_id,
            "base_currency": str(portfolio["base_currency"]).upper(),
        },
        field_name="transaction_replay_config",
    )
    config_row = {
        "portfolio_id": normalized_portfolio_id,
        "effective_as_of": effective_as_of,
        "range_start": range_start,
        "knowledge_cutoff_at": cutoff,
        "base_currency": str(portfolio["base_currency"]).upper(),
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "config_hash": canonical_sha256_hex(canonical_config),
        "canonical_config": canonical_config,
    }
    corp_windows, corp_events = _corporate_action_rows(
        session,
        portfolio_id=normalized_portfolio_id,
        instrument_ids=instrument_ids,
        range_start=range_start,
        range_end=effective_as_of,
    )
    return TransactionReplayCaptureResult(
        rows_by_table={
            portfolio_daily_config_input.name: [config_row],
            portfolio_daily_account_input.name: account_input_rows,
            portfolio_daily_transaction_input.name: transaction_rows,
            portfolio_daily_instrument_input.name: instrument_rows,
            portfolio_daily_corp_action_window.name: corp_windows,
            portfolio_daily_corp_action_input.name: corp_events,
        },
        portfolio_id=normalized_portfolio_id,
        effective_as_of=effective_as_of,
        range_start=range_start,
        knowledge_cutoff_at=cutoff,
    )


__all__ = [
    "CommonCaptureResult",
    "ManifestCaptureError",
    "PortfolioDailyCapturePolicy",
    "TransactionReplayCaptureResult",
    "capture_common_dependencies",
    "capture_transaction_replay_dependencies",
]
