from __future__ import annotations

import re
from pathlib import Path

from watchlist_app.reference_data.watchlist_fields import current_field_registry


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
WATCHLIST_README = REPOSITORY_ROOT / "apps" / "watchlist" / "README.md"
RETURN_SERIES_CONTRACT = (
    REPOSITORY_ROOT
    / "apps"
    / "watchlist"
    / "docs"
    / "RETURN_SERIES_CONTRACT.md"
)
DATA_MODEL_CONTRACT = (
    REPOSITORY_ROOT
    / "apps"
    / "watchlist"
    / "docs"
    / "FUND_TERMINAL_V2_DATA_MODEL_AND_API.md"
)

SCALAR_RETURN_FIELDS = (
    "return_1w",
    "return_1m",
    "return_3m",
    "return_6m",
    "return_mtd",
    "return_ytd",
    "return_1y",
)
SCALAR_RETURN_LABELS = ("1W", "1M", "3M", "6M", "MTD", "YTD", "1Y")


def test_active_docs_cover_every_materialized_scalar_return_window() -> None:
    readme = WATCHLIST_README.read_text(encoding="utf-8")
    return_contract = RETURN_SERIES_CONTRACT.read_text(encoding="utf-8")
    data_model = DATA_MODEL_CONTRACT.read_text(encoding="utf-8")

    for field_key in SCALAR_RETURN_FIELDS:
        assert field_key in readme
        assert field_key in data_model
    for label in SCALAR_RETURN_LABELS:
        assert label in return_contract

    assert "请求的** `as_of_date`" in return_contract
    assert "不共享业务 helper、read model 或运行时 API" in return_contract


def test_generic_return_field_descriptions_do_not_mislabel_price_return_series() -> None:
    fields_by_key = {
        str(field["field_key"]): field for field in current_field_registry()
    }
    for field_key in SCALAR_RETURN_FIELDS:
        description = str(fields_by_key[field_key]["description"])
        assert "selected calculation series" in description
        assert "total return" not in description.lower()


def test_watchlist_metric_docs_have_no_broken_local_links() -> None:
    active_docs = [
        WATCHLIST_README,
        RETURN_SERIES_CONTRACT,
        DATA_MODEL_CONTRACT,
        REPOSITORY_ROOT
        / "apps"
        / "watchlist"
        / "docs"
        / "CURRENT_SYSTEM_BASELINE.md",
        REPOSITORY_ROOT / "apps" / "watchlist" / "docs" / "INDEX.md",
    ]
    for document in active_docs:
        for raw_target in re.findall(
            r"(?<!!)\[[^\]]+\]\(([^)]+)\)",
            document.read_text(encoding="utf-8"),
        ):
            target = raw_target.strip().strip("<>")
            if (
                not target
                or target.startswith(("#", "http://", "https://", "mailto:"))
            ):
                continue
            local_path = target.split("#", 1)[0]
            assert (
                document.parent / local_path
            ).resolve().exists(), f"{document}: broken local link {target}"
