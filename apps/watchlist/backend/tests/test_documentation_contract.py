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
    / "DATA_MODEL_AND_API.md"
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
        assert field_key in data_model
    for label in SCALAR_RETURN_LABELS:
        assert label in return_contract

    assert "./docs/DATA_MODEL_AND_API.md" in readme
    assert "./docs/RETURN_SERIES_CONTRACT.md" in readme
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


def test_documented_watchlist_api_routes_exist(client) -> None:
    schema = client.get("/openapi.json").json()
    actual_routes = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
    }
    documented_routes: set[tuple[str, str]] = set()
    for code_span in re.findall(
        r"`([^`]*?/api/[^`]*)`",
        DATA_MODEL_CONTRACT.read_text(encoding="utf-8"),
    ):
        if "[...]" in code_span:
            continue
        path_match = re.search(r"(/api/[^ ]+)", code_span)
        assert path_match is not None
        path = path_match.group(1).rstrip(".,;；。")
        methods = re.findall(
            r"\b(GET|POST|PUT|PATCH|DELETE)\b",
            code_span[: path_match.start()],
        )
        documented_routes.update((method, path) for method in methods)

    assert documented_routes
    assert documented_routes <= actual_routes, sorted(documented_routes - actual_routes)


def test_watchlist_metric_docs_have_no_broken_local_links() -> None:
    active_docs = [
        WATCHLIST_README,
        RETURN_SERIES_CONTRACT,
        DATA_MODEL_CONTRACT,
        REPOSITORY_ROOT
        / "apps"
        / "watchlist"
        / "docs"
        / "ASSET_DETAIL_ARCHITECTURE.md",
        REPOSITORY_ROOT
        / "apps"
        / "watchlist"
        / "docs"
        / "FUND_PRODUCT_FRAMEWORK.md",
        REPOSITORY_ROOT
        / "apps"
        / "watchlist"
        / "docs"
        / "FUND_QUALITATIVE_RESEARCH_FRAMEWORK.md",
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
