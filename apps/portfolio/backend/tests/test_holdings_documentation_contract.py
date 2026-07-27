from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
HOLDINGS_PAGE = (
    REPOSITORY_ROOT
    / "apps"
    / "portfolio"
    / "frontend"
    / "src"
    / "pages"
    / "PortfolioHomePage.tsx"
)
HOLDINGS_FIELD_REFERENCE = (
    REPOSITORY_ROOT
    / "apps"
    / "portfolio"
    / "docs"
    / "03_HOLDINGS_FIELD_REFERENCE.md"
)


def _implementation_aggregation_contract() -> dict[str, str]:
    source = HOLDINGS_PAGE.read_text(encoding="utf-8")
    match = re.search(
        r"export const HOLDINGS_GROUP_AGGREGATION_KIND:.*?= \{(?P<body>.*?)^\}",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, "Holdings aggregation contract is missing from PortfolioHomePage.tsx."
    entries = re.findall(
        r"^\s{2}([a-z0-9_]+): '([a-z_]+)',\s*$",
        match.group("body"),
        flags=re.MULTILINE,
    )
    assert entries, "No Holdings aggregation entries were parsed from the frontend contract."
    assert len(entries) == len(dict(entries)), "Holdings aggregation contract contains duplicate keys."
    return dict(entries)


def _documented_aggregation_contract() -> dict[str, str]:
    source = HOLDINGS_FIELD_REFERENCE.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- holdings-column-contract:start -->(?P<body>.*?)"
        r"<!-- holdings-column-contract:end -->",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, "Holdings field reference is missing its machine-readable contract markers."
    entries = re.findall(
        r"^\| `([a-z0-9_]+)` \|.*\| `([a-z_]+)` \|$",
        match.group("body"),
        flags=re.MULTILINE,
    )
    assert entries, "No Holdings field rows were parsed from the field reference."
    assert len(entries) == len(dict(entries)), "Holdings field reference contains duplicate keys."
    return dict(entries)


def _implementation_field_labels() -> dict[str, str]:
    source = HOLDINGS_PAGE.read_text(encoding="utf-8")
    definitions_start = source.find(
        "const HOLDINGS_COLUMN_DEFINITIONS: Record<HoldingsColumnKey, HoldingsColumnDefinition>"
    )
    assert definitions_start >= 0, "Holdings column definitions are missing."
    entries = re.findall(
        r"^\s{2}([a-z0-9_]+): \{\n"
        r"\s{4}key: '([a-z0-9_]+)',\n"
        r"\s{4}label: '([^']+)',",
        source[definitions_start:],
        flags=re.MULTILINE,
    )
    labels: dict[str, str] = {}
    for object_key, field_key, label in entries:
        assert object_key == field_key
        labels[field_key] = label
    assert set(labels) == set(_implementation_aggregation_contract())
    return labels


def _documented_field_labels() -> dict[str, str]:
    source = HOLDINGS_FIELD_REFERENCE.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- holdings-column-contract:start -->(?P<body>.*?)"
        r"<!-- holdings-column-contract:end -->",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    return {
        field_key: label.strip()
        for field_key, label in re.findall(
            r"^\| `([a-z0-9_]+)` \| ([^|]+) \|",
            match.group("body"),
            flags=re.MULTILINE,
        )
    }


def test_every_holdings_column_has_the_documented_grouping_contract() -> None:
    assert _documented_aggregation_contract() == _implementation_aggregation_contract()


def test_every_holdings_column_has_the_exact_documented_ui_label() -> None:
    assert _documented_field_labels() == _implementation_field_labels()


def test_active_docs_do_not_restore_the_obsolete_blank_total_rule() -> None:
    active_docs = [
        REPOSITORY_ROOT / "docs" / "FRONTEND_DESIGN_BASELINE.md",
        REPOSITORY_ROOT / "docs" / "USER_MANUAL.md",
    ]
    content = "\n".join(path.read_text(encoding="utf-8") for path in active_docs)
    obsolete_phrases = [
        "instrument trend return 显示 `—`",
        "instrument trend return 默认显示 `—`",
        "若未来展示 current-weight blended instrument return",
    ]
    for phrase in obsolete_phrases:
        assert phrase not in content


def test_portfolio_metric_docs_have_no_broken_local_links() -> None:
    active_docs = [
        REPOSITORY_ROOT / "apps" / "portfolio" / "README.md",
        REPOSITORY_ROOT
        / "apps"
        / "portfolio"
        / "docs"
        / "01_CALCULATION_SPEC.md",
        REPOSITORY_ROOT
        / "apps"
        / "portfolio"
        / "docs"
        / "02_GIPS_ALIGNMENT.md",
        HOLDINGS_FIELD_REFERENCE,
        REPOSITORY_ROOT / "docs" / "README.md",
        REPOSITORY_ROOT / "docs" / "FRONTEND_DESIGN_BASELINE.md",
        REPOSITORY_ROOT / "docs" / "USER_MANUAL.md",
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
