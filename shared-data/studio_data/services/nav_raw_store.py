from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import json
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from studio_data.db.email_models import FundNavRawObservation
from studio_data.db.session import get_session_factory


RAW_NAV_SOURCE_KINDS = frozenset(
    {
        "api_observation",
        "legacy_registry_snapshot",
        "manual_import",
    }
)
RAW_NAV_STATUSES = frozenset({"complete", "partial", "unavailable"})


def record_raw_nav_observations(
    *,
    instrument_id: str,
    rows: Iterable[dict[str, object]],
    source_kind: str,
    source_ref: str,
    provider: str,
    status: str,
    evidence: dict[str, object] | None = None,
) -> None:
    """Upsert non-canonical source observations before canonical publication."""
    normalized_rows = list(rows)
    if not normalized_rows:
        return
    normalized_source_kind = source_kind.strip().lower()
    normalized_source_ref = source_ref.strip()
    normalized_provider = provider.strip()
    normalized_status = status.strip().lower()
    if normalized_source_kind not in RAW_NAV_SOURCE_KINDS:
        raise ValueError(f'Unsupported raw NAV source kind "{source_kind}".')
    if not normalized_source_ref:
        raise ValueError("Raw NAV source_ref is required.")
    if not normalized_provider:
        raise ValueError("Raw NAV provider is required.")
    if normalized_status not in RAW_NAV_STATUSES:
        raise ValueError(f'Unsupported raw NAV status "{status}".')
    with get_session_factory()() as session:
        for row_index, row in enumerate(normalized_rows, start=1):
            raw_date = str(row.get("as_of_date") or "").strip()
            currency = str(row.get("currency") or "").strip().upper()
            if not raw_date or not currency:
                raise ValueError(
                    f"Raw NAV row {row_index} requires as_of_date and currency."
                )
            try:
                as_of_date = date.fromisoformat(raw_date)
            except ValueError as error:
                raise ValueError(
                    f"Raw NAV row {row_index} has an invalid as_of_date."
                ) from error
            observation_evidence = {
                **dict(evidence or {}),
                **dict(row.get("_total_return_evidence") or {}),
            }
            unit_nav_value = _optional_text(row.get("nav"))
            cash_cumulative_nav_value = _optional_text(
                row.get("cash_cumulative_nav")
            )
            observed_total_return_nav_value = (
                _optional_text(row.get("nav_with_dividend"))
                if str(row.get("_total_return_method") or "").strip()
                in {"", "provider_explicit"}
                else None
            )
            unit_nav_status = _value_status(
                row,
                key="_nav_status",
                fallback=normalized_status,
                present=unit_nav_value is not None,
            )
            cash_cumulative_nav_status = _value_status(
                row,
                key="_cash_cumulative_nav_status",
                fallback=normalized_status,
                present=cash_cumulative_nav_value is not None,
            )
            observed_total_return_nav_status = _value_status(
                row,
                key="_nav_with_dividend_status",
                fallback=normalized_status,
                present=observed_total_return_nav_value is not None,
            )
            unit_nav_provider = _value_provider(
                row,
                key="_nav_source_provider",
                fallback=normalized_provider,
                present=unit_nav_value is not None,
            )
            cash_cumulative_provider = _value_provider(
                row,
                key="_cash_cumulative_nav_source_provider",
                fallback=normalized_provider,
                present=cash_cumulative_nav_value is not None,
            )
            total_return_provider = _value_provider(
                row,
                key="_nav_with_dividend_source_provider",
                fallback=normalized_provider,
                present=observed_total_return_nav_value is not None,
            )
            row_fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "as_of_date": raw_date,
                        "currency": currency,
                        "unit_nav": unit_nav_value,
                        "cash_cumulative_nav": cash_cumulative_nav_value,
                        "observed_total_return_nav": (
                            observed_total_return_nav_value
                        ),
                        "unit_nav_provider": unit_nav_provider,
                        "cash_cumulative_provider": cash_cumulative_provider,
                        "total_return_provider": total_return_provider,
                        "unit_nav_status": unit_nav_status,
                        "cash_cumulative_nav_status": (
                            cash_cumulative_nav_status
                        ),
                        "observed_total_return_nav_status": (
                            observed_total_return_nav_status
                        ),
                        "evidence": observation_evidence,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            effective_source_ref = (
                normalized_source_ref
                if normalized_source_kind == "legacy_registry_snapshot"
                else f"{normalized_source_ref}:row:{row_fingerprint}"
            )
            if len(effective_source_ref) > 512:
                source_ref_fingerprint = hashlib.sha256(
                    normalized_source_ref.encode("utf-8")
                ).hexdigest()
                effective_source_ref = (
                    f"sha256:{source_ref_fingerprint}:row:{row_fingerprint}"
                )
            record = FundNavRawObservation(
                instrument_id=instrument_id,
                as_of_date=as_of_date,
                currency=currency,
                source_kind=normalized_source_kind,
                source_ref=effective_source_ref,
                unit_nav_value=unit_nav_value,
                cash_cumulative_nav_value=cash_cumulative_nav_value,
                observed_total_return_nav_value=observed_total_return_nav_value,
                total_return_semantics=(
                "provider_explicit"
                if observed_total_return_nav_value is not None
                else "absent"
                ),
                unit_nav_status=unit_nav_status,
                cash_cumulative_nav_status=cash_cumulative_nav_status,
                observed_total_return_nav_status=(
                    observed_total_return_nav_status
                ),
                unit_nav_source_provider=unit_nav_provider,
                cash_cumulative_source_provider=cash_cumulative_provider,
                total_return_source_provider=total_return_provider,
                evidence_json=observation_evidence,
                captured_at=datetime.now(UTC),
            )
            try:
                # The unique source identity is the idempotency key. A savepoint
                # turns concurrent exact replays into a successful no-op on both
                # PostgreSQL and SQLite without a racy select-before-insert.
                with session.begin_nested():
                    session.add(record)
                    session.flush()
            except IntegrityError:
                continue
        session.commit()


def list_raw_nav_observations(*, instrument_id: str) -> list[dict[str, object]]:
    with get_session_factory()() as session:
        records = session.scalars(
            select(FundNavRawObservation)
            .where(FundNavRawObservation.instrument_id == instrument_id)
            .order_by(
                FundNavRawObservation.as_of_date,
                FundNavRawObservation.fund_nav_raw_observation_id,
            )
        )
        rows: list[dict[str, object]] = []
        for record in records:
            row: dict[str, object] = {
                "as_of_date": record.as_of_date.isoformat(),
                "currency": record.currency,
                "nav": record.unit_nav_value,
                "cash_cumulative_nav": record.cash_cumulative_nav_value,
                "_raw_observation_id": record.fund_nav_raw_observation_id,
                "_raw_captured_at": record.captured_at.isoformat(),
                "_raw_source_kind": record.source_kind,
                "_raw_source_ref": record.source_ref,
                "_raw_source_provider": (
                    record.unit_nav_source_provider
                    or record.cash_cumulative_source_provider
                    or record.total_return_source_provider
                    or ""
                ),
                "_nav_source_provider": record.unit_nav_source_provider or "",
                "_cash_cumulative_nav_source_provider": (
                    record.cash_cumulative_source_provider or ""
                ),
                "_nav_with_dividend_source_provider": (
                    record.total_return_source_provider or ""
                ),
                "_nav_status": record.unit_nav_status or "",
                "_cash_cumulative_nav_status": (
                    record.cash_cumulative_nav_status or ""
                ),
                "_nav_with_dividend_status": (
                    record.observed_total_return_nav_status or ""
                ),
            }
            if record.total_return_semantics == "provider_explicit":
                row["nav_with_dividend"] = record.observed_total_return_nav_value
                row["_total_return_method"] = "provider_explicit"
                row["_total_return_source_field"] = str(
                    dict(record.evidence_json or {}).get("source_field")
                    or "nav_with_dividend"
                )
            rows.append(row)
        return rows


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _value_status(
    row: dict[str, object],
    *,
    key: str,
    fallback: str,
    present: bool,
) -> str | None:
    if not present:
        return None
    status = str(row.get(key) or fallback).strip().lower()
    if status not in RAW_NAV_STATUSES:
        raise ValueError(f'Unsupported raw NAV value status "{status}".')
    return status


def _value_provider(
    row: dict[str, object],
    *,
    key: str,
    fallback: str,
    present: bool,
) -> str | None:
    if not present:
        return None
    provider = str(row.get(key) or fallback).strip()
    if not provider:
        raise ValueError("Raw NAV value provider is required.")
    return provider
