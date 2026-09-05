from __future__ import annotations

import re
import sys
import hashlib
import json
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
NAV_DIR = PROJECT_ROOT / "nav"

sys.path.insert(0, str(BACKEND_ROOT))

from studio_data.services import market_data_ops  # noqa: E402
from studio_data.services.instrument_store import get_instrument, list_instruments  # noqa: E402
from studio_data.services.downstream_notifications import notify_market_data_downstream_refresh  # noqa: E402


def _normalize_name(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value.lower())


def _normalize_code(value: str) -> str:
    prefix = re.split(r"[（(]", value, maxsplit=1)[0]
    return re.sub(r"[^0-9A-Z]+", "", prefix.upper())


def _load_registered_funds() -> dict[str, dict[str, str]]:
    rows = [
        *list_instruments(instrument_type="public_fund", limit=None),
        *list_instruments(instrument_type="private_fund", limit=None),
    ]
    instruments: dict[str, dict[str, str]] = {}
    for row in rows:
        identifiers = [
            item for item in list(row.get("identifiers") or []) if isinstance(item, dict)
        ]
        primary = next((item for item in identifiers if item.get("is_primary")), None)
        identifier = primary or (identifiers[0] if identifiers else None)
        instrument_id = str(row["instrument_id"])
        instruments[instrument_id] = {
            "instrument_id": instrument_id,
            "instrument_name": str(row["instrument_name"]),
            "instrument_type": str(row["instrument_type"]),
            "identifier_value": str(
                (identifier or {}).get("identifier_value") or instrument_id
            ).strip().upper(),
            "currency": str(row["currency"]),
        }
    return instruments


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


def main() -> int:
    instruments = _load_registered_funds()
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
            unmatched_files.append(f"{path.name}: no registered fund match")
            continue

        matched_rows_by_instrument[instrument_id].extend(rows)
        matched_files_by_instrument[instrument_id].append(path.name)

    imported_instruments: list[tuple[str, int, str, str]] = []
    for instrument_id, rows in sorted(matched_rows_by_instrument.items()):
        instrument = instruments[instrument_id]
        registry_instrument = get_instrument(instrument_id)
        if registry_instrument is None:
            raise RuntimeError(f'Instrument "{instrument_id}" disappeared during import.')
        merged_rows = market_data_ops._normalize_nav_rows_for_instrument(
            instrument=registry_instrument,
            rows=rows,
        )
        source_files = sorted(matched_files_by_instrument[instrument_id])
        row_count = len(merged_rows)
        source_revision = hashlib.sha256(
            json.dumps(
                {"files": source_files, "rows": merged_rows},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        market_data_ops.record_raw_nav_observations(
            instrument_id=instrument_id,
            rows=merged_rows,
            provider="nav_folder_import",
            source_kind="manual_import",
            source_ref=f"registered-fund-folder:{source_revision}",
            status="complete",
            evidence={"file_names": source_files},
        )
        market_data_ops._publish_fund_nav_projection(
            instrument_id=instrument_id,
            load_source_rows=lambda current_instrument, current_id=instrument_id: (
                market_data_ops._load_all_durable_nav_source_rows(
                    instrument_id=current_id,
                    instrument=current_instrument,
                )
            ),
            source_provider="nav_folder_import",
            refresh_status="imported",
            updated_by="codex_nav_import",
            message_factory=lambda publication, files=source_files, row_count=row_count: (
                f"Imported {row_count} NAV rows from nav folder: "
                + ", ".join(files)
                + f"; rebuilt {len(publication.rows)} canonical dates and found "
                + f"{len(publication.action_candidates)} action signal(s)."
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
