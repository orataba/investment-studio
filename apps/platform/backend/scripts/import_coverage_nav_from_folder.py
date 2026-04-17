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

from app.db.models import Instrument, InstrumentIdentifier  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.services import market_data_ops  # noqa: E402
from app.services.instrument_store import (  # noqa: E402
    _default_lifecycle_state,
    _default_quote_selection_policy,
    _default_refresh_status,
    _default_source_settings,
    replace_nav_history,
)


def _normalize_name(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value.lower())


def _normalize_code(value: str) -> str:
    prefix = re.split(r"[（(]", value, maxsplit=1)[0]
    return re.sub(r"[^0-9A-Z]+", "", prefix.upper())


def _load_coverage_assets() -> dict[str, dict[str, str]]:
    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.execute(
            text(
                """
                SELECT
                    row.asset_id,
                    row.asset_name,
                    row.asset_type,
                    COALESCE(
                        NULLIF(detail.primary_identifier_value, ''),
                        summary.payload_json ->> 'ticker_or_isin',
                        row.asset_id
                    ) AS identifier_value
                FROM watchlist.watchlist_row_read_model AS row
                LEFT JOIN watchlist.asset_detail AS detail
                  ON detail.asset_id = row.asset_id
                LEFT JOIN watchlist.asset_summary_read_model AS summary
                  ON summary.asset_id = row.asset_id
                WHERE row.watchlist_id = 'coverage'
                  AND row.asset_type = 'fund'
                ORDER BY row.asset_name
                """
            )
        ).mappings()
        return {
            str(row["asset_id"]): {
                "asset_id": str(row["asset_id"]),
                "asset_name": str(row["asset_name"]),
                "asset_type": str(row["asset_type"] or "fund"),
                "identifier_value": str(row["identifier_value"] or row["asset_id"]).strip().upper(),
                "currency": "CNY",
            }
            for row in rows
        }


def _resolve_asset_id(
    *,
    file_name: str,
    rows: list[dict[str, object]],
    assets: dict[str, dict[str, str]],
) -> str | None:
    code_index: dict[str, set[str]] = defaultdict(set)
    name_index: dict[str, set[str]] = defaultdict(set)
    for asset_id, asset in assets.items():
        code_index[_normalize_code(asset["identifier_value"])].add(asset_id)
        name_index[_normalize_name(asset["asset_name"])].add(asset_id)

    code_candidates: set[str] = set()
    for row in rows:
        raw_code = str(row.get("asset_code") or "").strip()
        if raw_code:
            code_candidates.update(code_index.get(_normalize_code(raw_code), set()))
    if len(code_candidates) == 1:
        return next(iter(code_candidates))

    name_candidates: set[str] = set()
    for row in rows:
        raw_name = str(row.get("asset_name") or "").strip()
        if not raw_name:
            continue
        normalized_row_name = _normalize_name(raw_name)
        for normalized_asset_name, asset_ids in name_index.items():
            if (
                normalized_row_name == normalized_asset_name
                or normalized_row_name in normalized_asset_name
                or normalized_asset_name in normalized_row_name
            ):
                name_candidates.update(asset_ids)

    if not name_candidates:
        normalized_file_name = _normalize_name(file_name)
        for normalized_asset_name, asset_ids in name_index.items():
            if normalized_asset_name and normalized_asset_name in normalized_file_name:
                name_candidates.update(asset_ids)

    candidates = code_candidates | name_candidates
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _ensure_instrument(asset: dict[str, str]) -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        instrument = session.get(Instrument, asset["asset_id"])
        if instrument is None:
            instrument = Instrument(
                asset_id=asset["asset_id"],
                asset_name=asset["asset_name"],
                asset_type=asset["asset_type"],
                currency=asset["currency"],
                quote_selection_policy_json=_default_quote_selection_policy(asset["asset_type"]),
                source_settings_json=_default_source_settings(),
                refresh_status_json=_default_refresh_status(),
                lifecycle_state_json=_default_lifecycle_state(),
            )
            session.add(instrument)
            session.flush()
        else:
            instrument.asset_name = asset["asset_name"]
            instrument.asset_type = asset["asset_type"]
            instrument.currency = asset["currency"]

        existing_identifier = session.scalar(
            select(InstrumentIdentifier).where(
                InstrumentIdentifier.identifier_value == asset["identifier_value"],
                InstrumentIdentifier.asset_id != asset["asset_id"],
            )
        )
        if existing_identifier is not None:
            raise RuntimeError(
                f'Identifier {asset["identifier_value"]} already belongs to {existing_identifier.asset_id}.'
            )

        identifiers = session.scalars(
            select(InstrumentIdentifier).where(InstrumentIdentifier.asset_id == asset["asset_id"])
        ).all()
        matched = next(
            (
                item
                for item in identifiers
                if item.identifier_value == asset["identifier_value"] and item.identifier_type == "ticker"
            ),
            None,
        )
        if matched is None:
            matched = InstrumentIdentifier(
                asset_id=asset["asset_id"],
                identifier_type="ticker",
                identifier_value=asset["identifier_value"],
                is_primary=True,
            )
            session.add(matched)
        for identifier in identifiers:
            identifier.is_primary = False
        matched.is_primary = True
        session.commit()


def main() -> int:
    assets = _load_coverage_assets()
    matched_rows_by_asset: dict[str, list[dict[str, object]]] = defaultdict(list)
    matched_files_by_asset: dict[str, list[str]] = defaultdict(list)
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

        asset_id = _resolve_asset_id(file_name=path.name, rows=rows, assets=assets)
        if asset_id is None:
            unmatched_files.append(f"{path.name}: no coverage asset match")
            continue

        matched_rows_by_asset[asset_id].extend(rows)
        matched_files_by_asset[asset_id].append(path.name)

    imported_assets: list[tuple[str, int, str, str]] = []
    for asset_id, rows in sorted(matched_rows_by_asset.items()):
        asset = assets[asset_id]
        _ensure_instrument(asset)
        merged_rows = market_data_ops._merge_rows_by_date(rows)
        normalized_status = market_data_ops._normalize_import_status(merged_rows, "complete")
        replace_nav_history(
            asset_id=asset_id,
            rows=merged_rows,
            provider="nav_folder_import",
            point_status=normalized_status,
            refresh_status="imported",
            updated_by="codex_nav_import",
            message=(
                f"Imported {len(merged_rows)} NAV rows from nav folder: "
                + ", ".join(matched_files_by_asset[asset_id])
            ),
            mode="manual",
        )
        imported_assets.append(
            (
                asset_id,
                len(merged_rows),
                str(merged_rows[0]["as_of_date"]),
                str(merged_rows[-1]["as_of_date"]),
            )
        )

    print("Imported assets:")
    for asset_id, row_count, first_nav, last_nav in imported_assets:
        asset = assets[asset_id]
        print(
            f"- {asset_id} | {asset['asset_name']} | rows={row_count} | "
            f"range={first_nav}..{last_nav} | files={', '.join(matched_files_by_asset[asset_id])}"
        )

    if unmatched_files:
        print("Unmatched files:")
        for item in unmatched_files:
            print(f"- {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
