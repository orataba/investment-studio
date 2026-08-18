from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[2]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "instrument-core" / "python"))

from platform_app.services import market_data_ops  # noqa: E402
from platform_app.services.downstream_notifications import (  # noqa: E402
    notify_market_data_downstream_refresh,
)
from platform_app.services.instrument_store import list_instruments  # noqa: E402
from portfolio_ops_instrument_core import FUND_INSTRUMENT_TYPES  # noqa: E402


REQUIRED_COLUMNS = (
    "产品代码",
    "产品名称",
    "管理人",
    "净值日期",
    "单位净值",
    "累计净值",
)


@dataclass(frozen=True)
class ImportTarget:
    instrument_id: str
    instrument_name: str
    source_mode: str
    current_latest_date: str | None
    instrument: dict[str, object]


@dataclass(frozen=True)
class CsvProfile:
    source_file: Path
    source_sha256: str
    total_row_count: int
    product_count: int
    invalid_row_count: int
    matched_row_count: int
    unmatched_product_count: int
    rows_by_instrument: dict[str, list[dict[str, object]]]
    product_code_by_instrument: dict[str, str]
    product_name_by_instrument: dict[str, str]
    manager_by_instrument: dict[str, str]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or import a multi-product NAV CSV for already-registered funds. "
            "No instruments are created."
        )
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument(
        "--source-mode",
        default="manual",
        choices=("manual", "email", "api"),
        help="Only registered funds with this configured source mode are eligible.",
    )
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist raw evidence, rebuild canonical NAV, and notify downstream apps.",
    )
    parser.add_argument("--updated-by", default="registered-nav-csv-import")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _normalize_code(value: object) -> str:
    return re.sub(r"[^0-9A-Z]+", "", str(value or "").strip().upper())


def _normalize_name(value: object) -> str:
    return re.sub(
        r"[^0-9a-zA-Z\u4e00-\u9fff]+",
        "",
        str(value or "").strip().lower(),
    )


def _latest_market_date(instrument: dict[str, object]) -> str | None:
    dates = [
        str(point.get("as_of_date"))
        for point in list(instrument.get("latest_market_data", []))
        if isinstance(point, dict) and point.get("as_of_date")
    ]
    return max(dates) if dates else None


def _instrument_codes(instrument: dict[str, object]) -> set[str]:
    source_settings = dict(instrument.get("source_settings", {}))
    raw_values: list[object] = [
        instrument.get("instrument_id"),
        instrument.get("ticker_or_isin"),
        source_settings.get("source_product_code"),
        source_settings.get("source_raw_product_code"),
    ]
    raw_values.extend(
        item.get("identifier_value")
        for item in list(instrument.get("identifiers", []))
        if isinstance(item, dict)
    )
    return {normalized for value in raw_values if (normalized := _normalize_code(value))}


def _load_targets(
    *,
    source_mode: str,
    include_inactive: bool,
) -> tuple[dict[str, ImportTarget], dict[str, set[str]]]:
    targets: dict[str, ImportTarget] = {}
    code_index: dict[str, set[str]] = defaultdict(set)
    for instrument in list_instruments(include_inactive=include_inactive):
        if str(instrument.get("instrument_type") or "") not in FUND_INSTRUMENT_TYPES:
            continue
        configured_source = str(
            dict(instrument.get("source_settings", {})).get("source_mode")
            or "manual"
        ).strip().lower()
        if configured_source != source_mode:
            continue
        instrument_id = str(instrument.get("instrument_id") or "").strip()
        if not instrument_id:
            continue
        target = ImportTarget(
            instrument_id=instrument_id,
            instrument_name=str(instrument.get("instrument_name") or "").strip(),
            source_mode=configured_source,
            current_latest_date=_latest_market_date(instrument),
            instrument=instrument,
        )
        targets[instrument_id] = target
        for code in _instrument_codes(instrument):
            code_index[code].add(instrument_id)
    return targets, code_index


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _names_agree(source_name: str, target_name: str) -> bool:
    source = _normalize_name(source_name)
    target = _normalize_name(target_name)
    return bool(source and target) and (
        source == target or source in target or target in source
    )


def _profile_csv(
    *,
    path: Path,
    targets: dict[str, ImportTarget],
    code_index: dict[str, set[str]],
) -> CsvProfile:
    rows_by_instrument_date: dict[str, dict[date, dict[str, object]]] = defaultdict(dict)
    matched_codes_by_instrument: dict[str, set[str]] = defaultdict(set)
    product_names: dict[str, str] = {}
    product_managers: dict[str, str] = {}
    all_product_codes: set[str] = set()
    matched_product_codes: set[str] = set()
    invalid_row_count = 0
    matched_row_count = 0
    total_row_count = 0
    identity_errors: list[str] = []

    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fieldnames = tuple(reader.fieldnames or ())
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing_columns:
            raise ValueError(
                "NAV CSV is missing required columns: " + ", ".join(missing_columns)
            )
        for row_number, raw_row in enumerate(reader, start=2):
            total_row_count += 1
            product_code = _normalize_code(raw_row.get("产品代码"))
            product_name = str(raw_row.get("产品名称") or "").strip()
            manager = str(raw_row.get("管理人") or "").strip()
            if product_code:
                all_product_codes.add(product_code)
                product_names.setdefault(product_code, product_name)
                product_managers.setdefault(product_code, manager)

            instrument_ids = code_index.get(product_code, set())
            if not instrument_ids:
                continue
            if len(instrument_ids) != 1:
                identity_errors.append(
                    f"row {row_number}: product code {product_code} matches "
                    f"multiple instruments {sorted(instrument_ids)}"
                )
                continue
            instrument_id = next(iter(instrument_ids))
            target = targets[instrument_id]
            if not _names_agree(product_name, target.instrument_name):
                identity_errors.append(
                    f"row {row_number}: product code {product_code} name "
                    f"{product_name!r} does not match registered name "
                    f"{target.instrument_name!r}"
                )
                continue

            parsed_date = market_data_ops._parse_nav_date(raw_row.get("净值日期"))
            unit_nav = market_data_ops._parse_nav_decimal(raw_row.get("单位净值"))
            cash_cumulative_nav = market_data_ops._parse_nav_decimal(
                raw_row.get("累计净值")
            )
            if parsed_date is None or (
                unit_nav is None and cash_cumulative_nav is None
            ):
                invalid_row_count += 1
                continue

            normalized_row: dict[str, object] = {
                "as_of_date": parsed_date.isoformat(),
                "instrument_code": product_code,
                "instrument_name": product_name,
            }
            if unit_nav is not None:
                normalized_row["nav"] = unit_nav
            if cash_cumulative_nav is not None:
                normalized_row["cash_cumulative_nav"] = cash_cumulative_nav

            existing = rows_by_instrument_date[instrument_id].get(parsed_date)
            if existing is not None and existing != normalized_row:
                raise ValueError(
                    f"Conflicting NAV rows for {product_code} on {parsed_date.isoformat()}."
                )
            if existing is None:
                rows_by_instrument_date[instrument_id][parsed_date] = normalized_row
                matched_row_count += 1
            matched_product_codes.add(product_code)
            matched_codes_by_instrument[instrument_id].add(product_code)

    if identity_errors:
        raise ValueError("; ".join(identity_errors[:10]))
    multi_code_targets = {
        instrument_id: sorted(codes)
        for instrument_id, codes in matched_codes_by_instrument.items()
        if len(codes) != 1
    }
    if multi_code_targets:
        raise ValueError(
            "One registered instrument matched multiple CSV product codes: "
            + json.dumps(multi_code_targets, ensure_ascii=False, sort_keys=True)
        )
    if invalid_row_count:
        matched_invalid = [
            code
            for code in matched_product_codes
            if not rows_by_instrument_date[
                next(iter(code_index[code]))
            ]
        ]
        if matched_invalid:
            raise ValueError(
                "Matched products contained no valid NAV rows: "
                + ", ".join(sorted(matched_invalid))
            )

    product_code_by_instrument = {
        instrument_id: next(iter(codes))
        for instrument_id, codes in matched_codes_by_instrument.items()
    }
    return CsvProfile(
        source_file=path,
        source_sha256=_sha256_file(path),
        total_row_count=total_row_count,
        product_count=len(all_product_codes),
        invalid_row_count=invalid_row_count,
        matched_row_count=matched_row_count,
        unmatched_product_count=len(all_product_codes - matched_product_codes),
        rows_by_instrument={
            instrument_id: [
                row for _row_date, row in sorted(rows_by_date.items())
            ]
            for instrument_id, rows_by_date in rows_by_instrument_date.items()
        },
        product_code_by_instrument=product_code_by_instrument,
        product_name_by_instrument={
            instrument_id: product_names[product_code]
            for instrument_id, product_code in product_code_by_instrument.items()
        },
        manager_by_instrument={
            instrument_id: product_managers[product_code]
            for instrument_id, product_code in product_code_by_instrument.items()
        },
    )


def _report(
    *,
    profile: CsvProfile,
    targets: dict[str, ImportTarget],
    applied: Iterable[dict[str, object]] = (),
) -> dict[str, object]:
    matched: list[dict[str, object]] = []
    for instrument_id, rows in sorted(profile.rows_by_instrument.items()):
        target = targets[instrument_id]
        csv_latest = str(rows[-1]["as_of_date"])
        matched.append(
            {
                "instrument_id": instrument_id,
                "instrument_name": target.instrument_name,
                "product_code": profile.product_code_by_instrument[instrument_id],
                "row_count": len(rows),
                "csv_first_date": str(rows[0]["as_of_date"]),
                "csv_latest_date": csv_latest,
                "current_latest_date": target.current_latest_date,
                "advances_latest_date": (
                    target.current_latest_date is None
                    or csv_latest > target.current_latest_date
                ),
            }
        )
    matched_ids = set(profile.rows_by_instrument)
    return {
        "source_file": str(profile.source_file),
        "source_sha256": profile.source_sha256,
        "total_row_count": profile.total_row_count,
        "product_count": profile.product_count,
        "invalid_row_count": profile.invalid_row_count,
        "matched_row_count": profile.matched_row_count,
        "matched_instrument_count": len(matched),
        "advancing_instrument_count": sum(
            bool(item["advances_latest_date"]) for item in matched
        ),
        "unmatched_csv_product_count": profile.unmatched_product_count,
        "registered_target_count": len(targets),
        "registered_targets_without_csv_match": [
            {
                "instrument_id": target.instrument_id,
                "instrument_name": target.instrument_name,
                "codes": sorted(_instrument_codes(target.instrument)),
            }
            for target in sorted(targets.values(), key=lambda item: item.instrument_id)
            if target.instrument_id not in matched_ids
        ],
        "matched": matched,
        "applied": list(applied),
    }


def _apply_import(
    *,
    profile: CsvProfile,
    targets: dict[str, ImportTarget],
    updated_by: str,
) -> list[dict[str, object]]:
    provider = f"product_nav_history_csv:{profile.source_file.name}"
    applied: list[dict[str, object]] = []
    dirty_dates: list[date] = []
    for index, instrument_id in enumerate(sorted(profile.rows_by_instrument), start=1):
        target = targets[instrument_id]
        product_code = profile.product_code_by_instrument[instrument_id]
        rows = market_data_ops._merge_rows_by_date(
            market_data_ops._apply_nav_row_currency(
                rows=profile.rows_by_instrument[instrument_id],
                instrument_currency=target.instrument.get("currency"),
            )
        )
        print(
            f"Importing {index}/{len(profile.rows_by_instrument)} "
            f"{instrument_id} {target.instrument_name} rows={len(rows)}",
            file=sys.stderr,
            flush=True,
        )
        market_data_ops.record_raw_nav_observations(
            instrument_id=instrument_id,
            rows=rows,
            source_kind="manual_import",
            source_ref=(
                f"file:{profile.source_sha256}:product:{product_code}"
            ),
            provider=provider,
            status="complete",
            evidence={
                "file_name": profile.source_file.name,
                "file_sha256": profile.source_sha256,
                "product_code": product_code,
                "product_name": profile.product_name_by_instrument[instrument_id],
                "manager": profile.manager_by_instrument[instrument_id],
            },
        )
        record, publication = market_data_ops._publish_fund_nav_projection(
            instrument_id=instrument_id,
            load_source_rows=lambda current_instrument, current_id=instrument_id: (
                market_data_ops._load_all_durable_nav_source_rows(
                    instrument_id=current_id,
                    instrument=current_instrument,
                )
            ),
            source_provider=provider,
            refresh_status="imported",
            updated_by=updated_by,
            message_factory=lambda current_publication, row_count=len(rows), code=product_code: (
                f"Imported {row_count} registered NAV CSV rows for {code}; rebuilt "
                f"{len(current_publication.rows)} canonical dates and found "
                f"{len(current_publication.action_candidates)} action signal(s)."
            ),
            mode="manual",
        )
        first_date = market_data_ops._parse_nav_date(rows[0].get("as_of_date"))
        if first_date is not None:
            dirty_dates.append(first_date)
        applied.append(
            {
                "instrument_id": instrument_id,
                "product_code": product_code,
                "source_row_count": len(rows),
                "canonical_row_count": len(publication.rows),
                "derived_total_return_count": publication.derived_total_return_count,
                "explicit_total_return_count": publication.explicit_total_return_count,
                "action_candidate_count": len(publication.action_candidates),
                "latest_market_date": _latest_market_date(record),
            }
        )

    if applied:
        notify_market_data_downstream_refresh(
            instrument_ids=[str(item["instrument_id"]) for item in applied],
            dirty_from=min(dirty_dates) if dirty_dates else None,
            raise_on_error=True,
        )
    return applied


def main() -> int:
    args = _parse_args()
    source_path = args.csv_path.expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit(f"NAV CSV does not exist: {source_path}")
    targets, code_index = _load_targets(
        source_mode=args.source_mode,
        include_inactive=args.include_inactive,
    )
    profile = _profile_csv(
        path=source_path,
        targets=targets,
        code_index=code_index,
    )
    applied = (
        _apply_import(
            profile=profile,
            targets=targets,
            updated_by=args.updated_by,
        )
        if args.apply
        else []
    )
    report = _report(profile=profile, targets=targets, applied=applied)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    else:
        mode = "applied" if args.apply else "preview"
        print(
            f"{mode}: matched {report['matched_instrument_count']} registered funds, "
            f"{report['advancing_instrument_count']} advance their latest date, "
            f"rows={report['matched_row_count']}, invalid={report['invalid_row_count']}."
        )
        for item in list(report["registered_targets_without_csv_match"]):
            print(
                f"unmatched registered fund: {item['instrument_id']} | "
                f"{item['instrument_name']} | codes={','.join(item['codes'])}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
