"""Exact, evidence-closed FX path resolution for ledger-event production."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from portfolio_app.calculations.numeric import (
    canonical_sha256_hex,
    exact_decimal_product,
    exact_decimal_subtract,
)
from portfolio_app.calculations.portfolio_daily.constants import (
    FX_CONSUMER_POLICY_VERSION,
    FX_RATE_MATH_PRECISION,
    FX_RATE_ROUNDING_MODE,
    FX_RESOLVER_STRATEGY_VERSION,
    QUOTE_FRESHNESS_POLICY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
)
from portfolio_app.calculations.portfolio_daily.hashing import (
    MANIFEST_IDENTITY_COLUMNS,
    canonical_storage_json,
)
from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    FactLineage,
    LedgerFactKind,
    quantize_ledger_fact,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_common import (
    FX_LEG_TABLE,
    FX_PATH_TABLE,
    TRANSACTION_TABLE,
    _BuildContext,
    _aware_datetime,
    _boolean,
    _currency,
    _date,
    _exact_decimal,
    _fail,
    _integer,
    _reason_codes,
    _required,
    _text,
    _uuid,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_contracts import (
    LedgerEventBuildDiagnostic,
    LedgerEventBuildErrorCode,
    LedgerEventDiagnosticCode,
)
from portfolio_ops_instrument_core.canonical_fx import effective_fx_leg_rate


_INGESTION_TIME_STATES = frozenset(
    {
        "observed",
        "legacy_series_upper_bound",
        "legacy_instrument_upper_bound",
        "legacy_migration_upper_bound",
    }
)


def _validate_ingestion_time_state(
    row: Mapping[str, object],
    *,
    record_id: str,
) -> None:
    state = _text(
        _required(
            row,
            "ingestion_time_state",
            table=FX_LEG_TABLE,
            record_id=record_id,
        ),
        table=FX_LEG_TABLE,
        record_id=record_id,
        field="ingestion_time_state",
    )
    if state not in _INGESTION_TIME_STATES:
        _fail(
            "FX leg has an invalid ingestion evidence state",
            code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="ingestion_time_state",
        )


class _FxBook:
    def __init__(self, context: _BuildContext) -> None:
        self._context = context
        self._paths: dict[tuple[date, str, str], Mapping[str, object]] = {}
        self._legs: dict[UUID, tuple[Mapping[str, object], ...]] = {}
        mutable_legs: dict[UUID, dict[int, Mapping[str, object]]] = {}
        for row in context.rows[FX_LEG_TABLE]:
            path_id = _uuid(
                _required(row, "fx_path_id", table=FX_LEG_TABLE, record_id="__row__"),
                table=FX_LEG_TABLE,
                record_id="__row__",
                field="fx_path_id",
            )
            order = _integer(
                _required(
                    row,
                    "leg_order",
                    table=FX_LEG_TABLE,
                    record_id=str(path_id),
                ),
                table=FX_LEG_TABLE,
                record_id=str(path_id),
                field="leg_order",
                minimum=1,
            )
            by_order = mutable_legs.setdefault(path_id, {})
            if order in by_order:
                _fail(
                    f"duplicate FX leg order {order}",
                    code=LedgerEventBuildErrorCode.DUPLICATE_NATURAL_KEY,
                    table=FX_LEG_TABLE,
                    record_id=str(path_id),
                    field="leg_order",
                )
            by_order[order] = row
        for path_id, by_order in mutable_legs.items():
            orders = tuple(sorted(by_order))
            if orders != tuple(range(1, len(orders) + 1)):
                _fail(
                    "FX leg orders must be contiguous and one-based",
                    code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                    table=FX_LEG_TABLE,
                    record_id=str(path_id),
                    field="leg_order",
                )
            self._legs[path_id] = tuple(by_order[index] for index in orders)

        seen_path_ids: set[UUID] = set()
        for row in context.rows[FX_PATH_TABLE]:
            path_id = _uuid(
                _required(row, "fx_path_id", table=FX_PATH_TABLE, record_id="__row__"),
                table=FX_PATH_TABLE,
                record_id="__row__",
                field="fx_path_id",
            )
            if path_id in seen_path_ids:
                _fail(
                    f"duplicate fx_path_id {path_id}",
                    code=LedgerEventBuildErrorCode.DUPLICATE_NATURAL_KEY,
                    table=FX_PATH_TABLE,
                    record_id=str(path_id),
                    field="fx_path_id",
                )
            seen_path_ids.add(path_id)
            valuation_date = _date(
                _required(
                    row,
                    "valuation_date",
                    table=FX_PATH_TABLE,
                    record_id=str(path_id),
                ),
                table=FX_PATH_TABLE,
                record_id=str(path_id),
                field="valuation_date",
            )
            from_currency = _currency(
                _required(
                    row,
                    "from_currency",
                    table=FX_PATH_TABLE,
                    record_id=str(path_id),
                ),
                table=FX_PATH_TABLE,
                record_id=str(path_id),
                field="from_currency",
            )
            to_currency = _currency(
                _required(
                    row,
                    "to_currency",
                    table=FX_PATH_TABLE,
                    record_id=str(path_id),
                ),
                table=FX_PATH_TABLE,
                record_id=str(path_id),
                field="to_currency",
            )
            key = (valuation_date, from_currency, to_currency)
            if key in self._paths:
                _fail(
                    f"duplicate FX path natural key {key}",
                    code=LedgerEventBuildErrorCode.DUPLICATE_NATURAL_KEY,
                    table=FX_PATH_TABLE,
                    record_id=str(path_id),
                )
            self._validate_path(row, path_id=path_id)
            self._paths[key] = row
        orphan_ids = set(self._legs) - seen_path_ids
        if orphan_ids:
            orphan = min(orphan_ids, key=str)
            _fail(
                "FX leg references a path absent from the sealed manifest",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=str(orphan),
                field="fx_path_id",
            )

    def _validate_policy(
        self,
        row: Mapping[str, object],
        *,
        table: str,
        record_id: str,
    ) -> None:
        expected = {
            "consumer_policy_version": FX_CONSUMER_POLICY_VERSION,
            "freshness_policy_version": QUOTE_FRESHNESS_POLICY_VERSION,
            "resolver_strategy_version": FX_RESOLVER_STRATEGY_VERSION,
            "rate_rounding_mode": FX_RATE_ROUNDING_MODE,
        }
        if table == FX_PATH_TABLE:
            expected["selection_policy_version"] = QUOTE_SELECTION_POLICY_VERSION
        for field, value in expected.items():
            actual = _text(
                _required(row, field, table=table, record_id=record_id),
                table=table,
                record_id=record_id,
                field=field,
            )
            if actual != value:
                _fail(
                    f"{table}.{field} has unsupported policy version {actual}",
                    code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                    table=table,
                    record_id=record_id,
                    field=field,
                )
        if _integer(
            _required(row, "rate_math_precision", table=table, record_id=record_id),
            table=table,
            record_id=record_id,
            field="rate_math_precision",
            minimum=1,
        ) != FX_RATE_MATH_PRECISION:
            _fail(
                "FX evidence uses an unsupported decimal precision",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=table,
                record_id=record_id,
                field="rate_math_precision",
            )

    def _validate_path(self, row: Mapping[str, object], *, path_id: UUID) -> None:
        record_id = str(path_id)
        self._validate_policy(row, table=FX_PATH_TABLE, record_id=record_id)
        from_currency = _currency(
            _required(row, "from_currency", table=FX_PATH_TABLE, record_id=record_id),
            table=FX_PATH_TABLE,
            record_id=record_id,
            field="from_currency",
        )
        to_currency = _currency(
            _required(row, "to_currency", table=FX_PATH_TABLE, record_id=record_id),
            table=FX_PATH_TABLE,
            record_id=record_id,
            field="to_currency",
        )
        valuation_date = _date(
            _required(row, "valuation_date", table=FX_PATH_TABLE, record_id=record_id),
            table=FX_PATH_TABLE,
            record_id=record_id,
            field="valuation_date",
        )
        kind = _text(
            _required(row, "path_kind", table=FX_PATH_TABLE, record_id=record_id),
            table=FX_PATH_TABLE,
            record_id=record_id,
            field="path_kind",
        )
        status = _text(
            _required(
                row,
                "resolution_status",
                table=FX_PATH_TABLE,
                record_id=record_id,
            ),
            table=FX_PATH_TABLE,
            record_id=record_id,
            field="resolution_status",
        )
        count = _integer(
            _required(row, "leg_count", table=FX_PATH_TABLE, record_id=record_id),
            table=FX_PATH_TABLE,
            record_id=record_id,
            field="leg_count",
            minimum=0,
        )
        reasons = _reason_codes(
            _required(row, "reason_codes", table=FX_PATH_TABLE, record_id=record_id),
            table=FX_PATH_TABLE,
            record_id=record_id,
            field="reason_codes",
        )
        coverage = _text(
            _required(
                row,
                "coverage_state",
                table=FX_PATH_TABLE,
                record_id=record_id,
            ),
            table=FX_PATH_TABLE,
            record_id=record_id,
            field="coverage_state",
        )
        legs = self._legs.get(path_id, ())
        if len(legs) != count:
            _fail(
                "FX path leg_count differs from sealed leg evidence",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_PATH_TABLE,
                record_id=record_id,
                field="leg_count",
            )
        expected_leg_count = {
            "identity": 0,
            "direct": 1,
            "inverse": 1,
            "cross": 2,
            "unavailable": 0,
        }.get(kind)
        if expected_leg_count is None or count != expected_leg_count:
            _fail(
                "FX path kind and leg_count are inconsistent",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_PATH_TABLE,
                record_id=record_id,
                field="leg_count",
            )
        if status == "unavailable":
            if (
                kind == "identity"
                or row.get("resolved_rate") is not None
                or row.get("rate_derivation_residual_exact") is not None
                or coverage != "unavailable"
                or not reasons
            ):
                _fail(
                    "unavailable FX path has inconsistent shape",
                    code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                    table=FX_PATH_TABLE,
                    record_id=record_id,
                )
            if kind == "unavailable":
                if from_currency == to_currency:
                    _fail(
                        "unavailable FX path must have distinct currencies",
                        code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                        table=FX_PATH_TABLE,
                        record_id=record_id,
                    )
                return
            expected_from = from_currency
            unresolved_leg_seen = False
            for leg in legs:
                leg_status = _text(
                    _required(
                        leg,
                        "leg_resolution_status",
                        table=FX_LEG_TABLE,
                        record_id=str(path_id),
                    ),
                    table=FX_LEG_TABLE,
                    record_id=str(path_id),
                    field="leg_resolution_status",
                )
                if leg_status == "resolved":
                    _, expected_from = self._validate_resolved_leg(
                        leg,
                        path_id=path_id,
                        valuation_date=valuation_date,
                        expected_from=expected_from,
                    )
                else:
                    unresolved_leg_seen = True
                    expected_from = self._validate_unavailable_leg(
                        leg,
                        path_id=path_id,
                        valuation_date=valuation_date,
                        expected_from=expected_from,
                    )
            if expected_from != to_currency or (
                kind != "unavailable" and not unresolved_leg_seen
            ):
                _fail(
                    "unavailable FX path leg chain is inconsistent",
                    code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                    table=FX_PATH_TABLE,
                    record_id=record_id,
                    field="resolution_status",
                )
            return
        if status != "resolved" or kind not in {"identity", "direct", "inverse", "cross"}:
            _fail(
                "FX path has unsupported resolution status/kind",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_PATH_TABLE,
                record_id=record_id,
            )
        rate = _exact_decimal(
            _required(
                row,
                "resolved_rate",
                table=FX_PATH_TABLE,
                record_id=record_id,
            ),
            table=FX_PATH_TABLE,
            record_id=record_id,
            field="resolved_rate",
            strictly_positive=True,
        )
        residual = _exact_decimal(
            _required(
                row,
                "rate_derivation_residual_exact",
                table=FX_PATH_TABLE,
                record_id=record_id,
            ),
            table=FX_PATH_TABLE,
            record_id=record_id,
            field="rate_derivation_residual_exact",
        )
        if kind == "identity":
            if (
                from_currency != to_currency
                or count != 0
                or rate != Decimal("1")
                or residual != 0
            ):
                _fail(
                    "identity FX path has inconsistent terms",
                    code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                    table=FX_PATH_TABLE,
                    record_id=record_id,
                )
            return
        if from_currency == to_currency or count == 0:
            _fail(
                "non-identity resolved FX path has inconsistent endpoints/legs",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_PATH_TABLE,
                record_id=record_id,
            )
        effective_rates: list[Decimal] = []
        expected_from = from_currency
        for leg in legs:
            leg_rate, expected_from = self._validate_resolved_leg(
                leg,
                path_id=path_id,
                valuation_date=valuation_date,
                expected_from=expected_from,
            )
            effective_rates.append(leg_rate)
        if expected_from != to_currency:
            _fail(
                "FX leg chain does not terminate at path to_currency",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_PATH_TABLE,
                record_id=record_id,
            )
        exact_product = exact_decimal_product(*effective_rates)
        if rate != exact_product or residual != 0:
            _fail(
                "FX path rate must equal its exact effective-leg product with "
                "zero composition residual",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_PATH_TABLE,
                record_id=record_id,
                field="rate_derivation_residual_exact",
            )
        if coverage not in {"complete", "partial"} or (
            (coverage == "complete") != (not reasons)
        ):
            _fail(
                "resolved FX path coverage/reason shape is inconsistent",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_PATH_TABLE,
                record_id=record_id,
                field="coverage_state",
            )

    def _validate_unavailable_leg(
        self,
        row: Mapping[str, object],
        *,
        path_id: UUID,
        valuation_date: date,
        expected_from: str,
    ) -> str:
        order = _integer(
            _required(row, "leg_order", table=FX_LEG_TABLE, record_id=str(path_id)),
            table=FX_LEG_TABLE,
            record_id=str(path_id),
            field="leg_order",
            minimum=1,
        )
        record_id = f"{path_id}:{order}"
        self._validate_policy(row, table=FX_LEG_TABLE, record_id=record_id)
        status = _text(
            _required(
                row,
                "leg_resolution_status",
                table=FX_LEG_TABLE,
                record_id=record_id,
            ),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="leg_resolution_status",
        )
        reasons = _reason_codes(
            _required(row, "reason_codes", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="reason_codes",
        )
        if status not in {"missing", "rejected"} or not reasons:
            _fail(
                "unavailable FX path contains an invalid leg status",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
                field="leg_resolution_status",
            )
        from_currency = _currency(
            _required(row, "from_currency", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="from_currency",
        )
        to_currency = _currency(
            _required(row, "to_currency", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="to_currency",
        )
        if from_currency != expected_from or from_currency == to_currency:
            _fail(
                "FX leg chain endpoints are inconsistent",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
            )
        _boolean(
            _required(row, "is_inverted", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="is_inverted",
        )
        if (
            row.get("effective_rate") is not None
            or row.get("rate_derivation_residual_exact") is not None
        ):
            _fail(
                "unavailable FX leg must not expose an effective rate",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
                field="effective_rate",
            )

        evidence_fields = (
            "observation_id",
            "revision_id",
            "revision_number",
            "observation_date",
            "quoted_rate",
            "quote_status",
            "source_published_at",
            "ingested_at",
            "ingestion_time_state",
            "payload_hash",
        )
        if status == "missing":
            if any(row.get(field) is not None for field in evidence_fields):
                _fail(
                    "missing FX leg must not expose quote revision evidence",
                    code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                    table=FX_LEG_TABLE,
                    record_id=record_id,
                )
            quote_series_id = row.get("quote_series_id")
            if quote_series_id is not None:
                _uuid(
                    quote_series_id,
                    table=FX_LEG_TABLE,
                    record_id=record_id,
                    field="quote_series_id",
                )
            return to_currency

        for field in ("quote_series_id", "observation_id", "revision_id"):
            _uuid(
                _required(row, field, table=FX_LEG_TABLE, record_id=record_id),
                table=FX_LEG_TABLE,
                record_id=record_id,
                field=field,
            )
        _integer(
            _required(
                row,
                "revision_number",
                table=FX_LEG_TABLE,
                record_id=record_id,
            ),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="revision_number",
            minimum=1,
        )
        observation_date = _date(
            _required(
                row,
                "observation_date",
                table=FX_LEG_TABLE,
                record_id=record_id,
            ),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="observation_date",
        )
        if observation_date > valuation_date:
            _fail(
                "FX leg observation is after its requested valuation date",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
                field="observation_date",
            )
        ingested_at = _aware_datetime(
            _required(row, "ingested_at", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="ingested_at",
        )
        if ingested_at > self._context.knowledge_cutoff_at:
            _fail(
                "FX leg was ingested after the manifest knowledge cutoff",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
                field="ingested_at",
            )
        _validate_ingestion_time_state(row, record_id=record_id)
        quote_status = _text(
            _required(row, "quote_status", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="quote_status",
        )
        quoted_rate = row.get("quoted_rate")
        if quote_status == "withdrawn":
            if quoted_rate is not None:
                _fail(
                    "withdrawn FX leg must not expose a quoted rate",
                    code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                    table=FX_LEG_TABLE,
                    record_id=record_id,
                    field="quoted_rate",
                )
        else:
            _exact_decimal(
                quoted_rate,
                table=FX_LEG_TABLE,
                record_id=record_id,
                field="quoted_rate",
                strictly_positive=True,
            )
        _text(
            _required(row, "payload_hash", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="payload_hash",
        )
        source_published_at = row.get("source_published_at")
        if source_published_at is not None:
            _aware_datetime(
                source_published_at,
                table=FX_LEG_TABLE,
                record_id=record_id,
                field="source_published_at",
            )
        return to_currency

    def _validate_resolved_leg(
        self,
        row: Mapping[str, object],
        *,
        path_id: UUID,
        valuation_date: date,
        expected_from: str,
    ) -> tuple[Decimal, str]:
        order = _integer(
            _required(row, "leg_order", table=FX_LEG_TABLE, record_id=str(path_id)),
            table=FX_LEG_TABLE,
            record_id=str(path_id),
            field="leg_order",
            minimum=1,
        )
        record_id = f"{path_id}:{order}"
        self._validate_policy(row, table=FX_LEG_TABLE, record_id=record_id)
        status = _text(
            _required(
                row,
                "leg_resolution_status",
                table=FX_LEG_TABLE,
                record_id=record_id,
            ),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="leg_resolution_status",
        )
        if status != "resolved" or _reason_codes(
            _required(row, "reason_codes", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="reason_codes",
        ):
            _fail(
                "resolved FX path contains a non-resolved leg",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
            )
        from_currency = _currency(
            _required(row, "from_currency", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="from_currency",
        )
        to_currency = _currency(
            _required(row, "to_currency", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="to_currency",
        )
        if from_currency != expected_from or from_currency == to_currency:
            _fail(
                "FX leg chain endpoints are inconsistent",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
            )
        inverted = _boolean(
            _required(row, "is_inverted", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="is_inverted",
        )
        quoted = _exact_decimal(
            _required(row, "quoted_rate", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="quoted_rate",
            strictly_positive=True,
        )
        effective = _exact_decimal(
            _required(row, "effective_rate", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="effective_rate",
            strictly_positive=True,
        )
        residual = _exact_decimal(
            _required(
                row,
                "rate_derivation_residual_exact",
                table=FX_LEG_TABLE,
                record_id=record_id,
            ),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="rate_derivation_residual_exact",
        )
        expected_effective = effective_fx_leg_rate(
            quoted,
            inverted=inverted,
        )
        if effective != expected_effective:
            _fail(
                "FX leg effective rate does not match the versioned "
                "Decimal50/HALF_EVEN operation",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
                field="effective_rate",
            )
        expected_residual = (
            exact_decimal_subtract(
                exact_decimal_product(effective, quoted), Decimal("1")
            )
            if inverted
            else Decimal("0")
        )
        if residual != expected_residual:
            _fail(
                "FX leg derivation evidence does not close",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
                field="rate_derivation_residual_exact",
            )
        for field in ("quote_series_id", "observation_id", "revision_id"):
            _uuid(
                _required(row, field, table=FX_LEG_TABLE, record_id=record_id),
                table=FX_LEG_TABLE,
                record_id=record_id,
                field=field,
            )
        _integer(
            _required(
                row,
                "revision_number",
                table=FX_LEG_TABLE,
                record_id=record_id,
            ),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="revision_number",
            minimum=1,
        )
        observation_date = _date(
            _required(
                row,
                "observation_date",
                table=FX_LEG_TABLE,
                record_id=record_id,
            ),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="observation_date",
        )
        if observation_date > valuation_date:
            _fail(
                "FX leg observation is after its requested valuation date",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
                field="observation_date",
            )
        ingested_at = _aware_datetime(
            _required(row, "ingested_at", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="ingested_at",
        )
        if ingested_at > self._context.knowledge_cutoff_at:
            _fail(
                "FX leg was ingested after the manifest knowledge cutoff",
                code=LedgerEventBuildErrorCode.FX_EVIDENCE_INVALID,
                table=FX_LEG_TABLE,
                record_id=record_id,
                field="ingested_at",
            )
        _validate_ingestion_time_state(row, record_id=record_id)
        _text(
            _required(row, "quote_status", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="quote_status",
        )
        _text(
            _required(row, "payload_hash", table=FX_LEG_TABLE, record_id=record_id),
            table=FX_LEG_TABLE,
            record_id=record_id,
            field="payload_hash",
        )
        return effective, to_currency

    def resolve(
        self,
        *,
        valuation_date: date,
        from_currency: str,
        consumer_record_id: str,
        field_name: str,
        diagnostics: list[LedgerEventBuildDiagnostic],
    ) -> tuple[Decimal | None, FactLineage | None]:
        if from_currency == self._context.base_currency:
            return Decimal("1"), None
        key = (valuation_date, from_currency, self._context.base_currency)
        row = self._paths.get(key)
        if row is None:
            _fail(
                "sealed manifest is missing the required FX path",
                code=LedgerEventBuildErrorCode.MISSING_REFERENCE,
                table=FX_PATH_TABLE,
                record_id=(
                    f"{valuation_date.isoformat()}:{from_currency}:"
                    f"{self._context.base_currency}"
                ),
                field="resolved_rate",
            )
        path_id = cast(UUID, row["fx_path_id"])
        status = cast(str, row["resolution_status"])
        if status == "unavailable":
            reasons = cast(Sequence[str], row["reason_codes"])
            diagnostics.append(
                LedgerEventBuildDiagnostic(
                    code=LedgerEventDiagnosticCode.FX_PATH_UNAVAILABLE,
                    source_table=TRANSACTION_TABLE,
                    source_record_id=consumer_record_id,
                    field_name=field_name,
                    message="base-currency measurement is unavailable; local ledger remains exact",
                    context=tuple(
                        sorted(
                            (
                                ("from_currency", from_currency),
                                ("to_currency", self._context.base_currency),
                                ("valuation_date", valuation_date.isoformat()),
                                ("reason_codes", ",".join(reasons)),
                            )
                        )
                    ),
                )
            )
            return None, None
        rate = quantize_ledger_fact(
            cast(Decimal, row["resolved_rate"]),
            kind=LedgerFactKind.RATIO,
            field_name="local_to_base_rate",
        )
        evidence = {
            "path": {
                key: value
                for key, value in row.items()
                if key not in MANIFEST_IDENTITY_COLUMNS
            },
            "legs": [
                {
                    key: value
                    for key, value in leg.items()
                    if key not in MANIFEST_IDENTITY_COLUMNS
                }
                for leg in self._legs.get(path_id, ())
            ],
        }
        revision_digest = canonical_sha256_hex(canonical_storage_json(evidence))
        return rate, FactLineage(
            source_record_id=str(path_id),
            source_revision_id=f"sha256:{revision_digest}",
            manifest_fact_key=(
                f"{FX_PATH_TABLE}/{valuation_date.isoformat()}/"
                f"{from_currency}/{self._context.base_currency}"
            ),
        )
