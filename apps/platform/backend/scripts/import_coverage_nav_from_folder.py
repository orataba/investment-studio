from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select, text


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
NAV_DIR = PROJECT_ROOT / "nav"

sys.path.insert(0, str(BACKEND_ROOT))

from platform_app.db.models import Instrument, InstrumentIdentifier  # noqa: E402
from platform_app.db.session import get_session_factory  # noqa: E402
from platform_app.services import market_data_ops  # noqa: E402
from platform_app.services.instrument_store import replace_nav_history  # noqa: E402
from portfolio_ops_instrument_core.instrument_store import (  # noqa: E402
    _default_lifecycle_state,
    _default_quote_selection_policy,
    _default_refresh_status,
    _default_source_settings,
)
from platform_app.services.downstream_notifications import notify_market_data_downstream_refresh  # noqa: E402


def _normalize_name(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value.lower())


def _normalize_code(value: str) -> str:
    prefix = re.split(r"[（(]", value, maxsplit=1)[0]
    return re.sub(r"[^0-9A-Z]+", "", prefix.upper())


def _load_coverage_instruments() -> dict[str, dict[str, str]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.execute(
            text(
                """
                SELECT
                    row.instrument_id,
                    row.instrument_name,
                    row.instrument_type,
                    COALESCE(
                        NULLIF(detail.primary_identifier_value, ''),
                        NULLIF(row.ticker_or_isin, ''),
                        summary.payload_json ->> 'ticker_or_isin',
                        row.instrument_id
                    ) AS identifier_value
                FROM watchlist.watchlist_row_read_model AS row
                LEFT JOIN watchlist.instrument_detail AS detail
                  ON detail.instrument_id = row.instrument_id
                LEFT JOIN watchlist.instrument_summary_read_model AS summary
                  ON summary.instrument_id = row.instrument_id
                WHERE row.watchlist_id IN ('coverage', 'all-coverage')
                  AND row.instrument_type = 'fund'
                ORDER BY row.instrument_name
                """
            )
        ).mappings()
        return {
            str(row["instrument_id"]): {
                "instrument_id": str(row["instrument_id"]),
                "instrument_name": str(row["instrument_name"]),
                "instrument_type": str(row["instrument_type"] or "fund"),
                "identifier_value": str(row["identifier_value"] or row["instrument_id"]).strip().upper(),
                "currency": "CNY",
            }
            for row in rows
        }


def _resolve_instrument_id(
    *,
    file_name: str,
    rows: list[dict[str, object]],
    instruments: dict[str, dict[str, str]],
) -> str | None:
    code_index: dict[str, set[str]] = defaultdict(set)
    name_index: dict[str, set[str]] = defaultdict(set)
    for instrument_id, instrument in instruments.items():
        code_index[_normalize_code(instrument["identifier_value"])].add(instrument_id)
        name_index[_normalize_name(instrument["instrument_name"])].add(instrument_id)

    code_candidates: set[str] = set()
    for row in rows:
        raw_code = str(row.get("instrument_code") or "").strip()
        if raw_code:
            code_candidates.update(code_index.get(_normalize_code(raw_code), set()))
    if len(code_candidates) == 1:
        return next(iter(code_candidates))

    name_candidates: set[str] = set()
    for row in rows:
        raw_name = str(row.get("instrument_name") or "").strip()
        if not raw_name:
            continue
        normalized_row_name = _normalize_name(raw_name)
        for normalized_instrument_name, instrument_ids in name_index.items():
            if (
                normalized_row_name == normalized_instrument_name
                or normalized_row_name in normalized_instrument_name
                or normalized_instrument_name in normalized_row_name
            ):
                name_candidates.update(instrument_ids)

    if not name_candidates:
        normalized_file_name = _normalize_name(file_name)
        for normalized_instrument_name, instrument_ids in name_index.items():
            if normalized_instrument_name and normalized_instrument_name in normalized_file_name:
                name_candidates.update(instrument_ids)

    candidates = code_candidates | name_candidates
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _ensure_instrument(instrument_data: dict[str, str]) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        record = session.get(Instrument, instrument_data["instrument_id"])
        if record is None:
            record = Instrument(
                instrument_id=instrument_data["instrument_id"],
                instrument_name=instrument_data["instrument_name"],
                instrument_type=instrument_data["instrument_type"],
                currency=instrument_data["currency"],
                quote_selection_policy_json=_default_quote_selection_policy(instrument_data["instrument_type"]),
                source_settings_json=_default_source_settings(),
                refresh_status_json=_default_refresh_status(),
                lifecycle_state_json=_default_lifecycle_state(),
            )
            session.add(record)
            session.flush()
        else:
            record.instrument_name = instrument_data["instrument_name"]
            record.instrument_type = instrument_data["instrument_type"]
            record.currency = instrument_data["currency"]

        existing_identifier = session.scalar(
            select(InstrumentIdentifier).where(
                InstrumentIdentifier.identifier_value == instrument_data["identifier_value"],
                InstrumentIdentifier.instrument_id != instrument_data["instrument_id"],
            )
        )
        if existing_identifier is not None:
            raise RuntimeError(
                f'Identifier {instrument_data["identifier_value"]} already belongs to {existing_identifier.instrument_id}.'
            )

        identifiers = session.scalars(
            select(InstrumentIdentifier).where(InstrumentIdentifier.instrument_id == instrument_data["instrument_id"])
        ).all()
        matched = next(
            (
                item
                for item in identifiers
                if item.identifier_value == instrument_data["identifier_value"] and item.identifier_type == "ticker"
            ),
            None,
        )
        if matched is None:
            matched = InstrumentIdentifier(
                instrument_id=instrument_data["instrument_id"],
                identifier_type="ticker",
                identifier_value=instrument_data["identifier_value"],
                is_primary=True,
            )
            session.add(matched)
        for identifier in identifiers:
            identifier.is_primary = False
        matched.is_primary = True
        session.commit()


def main() -> int:
    instruments = _load_coverage_instruments()
    matched_rows_by_instrument: dict[str, list[dict[str, object]]] = defaultdict(list)
    matched_files_by_instrument: dict[str, list[str]] = defaultdict(list)
    unmatched_files: list[str] = []

    for path in sorted(NAV_DIR.iterdir()):
        if not path.is_file():
            continue
        rows = market_data_ops._parse_nav_rows_from_attachment(
            attachment_name=path.name,
            attachment_bytes=path.read_bytes(),
            parser_profile="generic_nav_table",
        )
        if not rows:
            rows = market_data_ops._parse_nav_rows_from_attachment(
                attachment_name=path.name,
                attachment_bytes=path.read_bytes(),
                parser_profile="label_nav_snapshot",
            )
        if not rows:
            unmatched_files.append(f"{path.name}: no rows parsed")
            continue

        instrument_id = _resolve_instrument_id(file_name=path.name, rows=rows, instruments=instruments)
        if instrument_id is None:
            unmatched_files.append(f"{path.name}: no coverage instrument match")
            continue

        matched_rows_by_instrument[instrument_id].extend(rows)
        matched_files_by_instrument[instrument_id].append(path.name)

    imported_instruments: list[tuple[str, int, str, str]] = []
    for instrument_id, rows in sorted(matched_rows_by_instrument.items()):
        instrument = instruments[instrument_id]
        _ensure_instrument(instrument)
        merged_rows = market_data_ops._merge_rows_by_date(rows)
        normalized_status = market_data_ops._normalize_import_status(merged_rows, "complete")
        replace_nav_history(
            instrument_id=instrument_id,
            rows=merged_rows,
            source_ref="nav_folder_import",
            point_status=normalized_status,
            refresh_status="imported",
            updated_by="codex_nav_import",
            message=(
                f"Imported {len(merged_rows)} NAV rows from nav folder: "
                + ", ".join(matched_files_by_instrument[instrument_id])
            ),
            mode="manual",
        )
        imported_instruments.append(
            (
                instrument_id,
                len(merged_rows),
                str(merged_rows[0]["as_of_date"]),
                str(merged_rows[-1]["as_of_date"]),
            )
        )

    imported_dirty_dates = [
        parsed
        for parsed in (
            market_data_ops._parse_nav_date(first_nav)
            for _, _, first_nav, _ in imported_instruments
        )
        if parsed is not None
    ]
    if imported_instruments:
        notify_market_data_downstream_refresh(
            instrument_ids=[instrument_id for instrument_id, _, _, _ in imported_instruments],
            dirty_from=min(imported_dirty_dates) if imported_dirty_dates else None,
        )

    print("Imported instruments:")
    for instrument_id, row_count, first_nav, last_nav in imported_instruments:
        instrument = instruments[instrument_id]
        print(
            f"- {instrument_id} | {instrument['instrument_name']} | rows={row_count} | "
            f"range={first_nav}..{last_nav} | files={', '.join(matched_files_by_instrument[instrument_id])}"
        )

    if unmatched_files:
        print("Unmatched files:")
        for item in unmatched_files:
            print(f"- {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
