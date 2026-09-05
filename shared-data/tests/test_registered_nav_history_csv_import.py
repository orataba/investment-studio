from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "import_registered_nav_history_csv.py"
)
SPEC = importlib.util.spec_from_file_location(
    "import_registered_nav_history_csv",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
nav_csv_import = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = nav_csv_import
SPEC.loader.exec_module(nav_csv_import)


def _target(instrument_id: str, name: str) -> object:
    instrument = {
        "instrument_id": instrument_id,
        "instrument_name": name,
        "instrument_type": "public_fund",
        "currency": "CNY",
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": instrument_id.upper()}
        ],
        "source_settings": {"source_mode": "manual"},
        "latest_market_data": [
            {"as_of_date": "2026-06-05", "quote_basis": "official_nav"}
        ],
    }
    return nav_csv_import.ImportTarget(
        instrument_id=instrument_id,
        instrument_name=name,
        source_mode="manual",
        current_latest_date="2026-06-05",
        instrument=instrument,
    )


def _write_csv(path: Path, rows: list[str]) -> None:
    path.write_text(
        "产品代码,产品名称,管理人,净值日期,单位净值,累计净值\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8-sig",
    )


def test_profile_imports_only_exact_registered_codes(tmp_path: Path) -> None:
    path = tmp_path / "history.csv"
    _write_csv(
        path,
        [
            "FUND01,测试基金一号,测试管理人,20260703,1.01,1.02",
            "FUND01,测试基金一号,测试管理人,20260710,1.03,1.04",
            "OTHER,未注册基金,其他管理人,20260710,0,0",
        ],
    )
    target = _target("fund01", "测试基金一号")

    profile = nav_csv_import._profile_csv(
        path=path,
        targets={"fund01": target},
        code_index={"FUND01": {"fund01"}},
    )

    assert profile.total_row_count == 3
    assert profile.product_count == 2
    assert profile.invalid_row_count == 0
    assert profile.matched_row_count == 2
    assert profile.unmatched_product_count == 1
    assert [row["as_of_date"] for row in profile.rows_by_instrument["fund01"]] == [
        "2026-07-03",
        "2026-07-10",
    ]


def test_profile_rejects_code_name_conflict(tmp_path: Path) -> None:
    path = tmp_path / "history.csv"
    _write_csv(
        path,
        ["FUND01,完全不同的基金,测试管理人,20260710,1.03,1.04"],
    )
    target = _target("fund01", "测试基金一号")

    with pytest.raises(ValueError, match="does not match registered name"):
        nav_csv_import._profile_csv(
            path=path,
            targets={"fund01": target},
            code_index={"FUND01": {"fund01"}},
        )


def test_apply_records_raw_evidence_rebuilds_projection_and_notifies_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "history.csv"
    _write_csv(
        path,
        ["FUND01,测试基金一号,测试管理人,20260710,1.03,1.04"],
    )
    target = _target("fund01", "测试基金一号")
    targets = {"fund01": target}
    profile = nav_csv_import._profile_csv(
        path=path,
        targets=targets,
        code_index={"FUND01": {"fund01"}},
    )
    recorded: list[dict[str, object]] = []
    notified: list[dict[str, object]] = []
    monkeypatch.setattr(
        nav_csv_import.market_data_ops,
        "record_raw_nav_observations",
        lambda **kwargs: recorded.append(kwargs),
    )
    monkeypatch.setattr(
        nav_csv_import.market_data_ops,
        "_publish_fund_nav_projection",
        lambda **kwargs: (
            {
                **target.instrument,
                "latest_market_data": [{"as_of_date": "2026-07-10"}],
            },
            SimpleNamespace(
                rows=[{"as_of_date": "2026-07-10"}],
                derived_total_return_count=1,
                explicit_total_return_count=0,
                action_candidates=[],
            ),
        ),
    )
    monkeypatch.setattr(
        nav_csv_import,
        "notify_market_data_downstream_refresh",
        lambda **kwargs: notified.append(kwargs),
    )

    applied = nav_csv_import._apply_import(
        profile=profile,
        targets=targets,
        updated_by="pytest",
    )

    assert applied[0]["latest_market_date"] == "2026-07-10"
    assert recorded[0]["instrument_id"] == "fund01"
    assert recorded[0]["source_ref"].endswith(":product:FUND01")
    assert notified == [
        {
            "instrument_ids": ["fund01"],
            "dirty_from": nav_csv_import.date(2026, 7, 10),
            "raise_on_error": True,
        }
    ]
