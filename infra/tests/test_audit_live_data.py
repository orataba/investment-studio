from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = REPOSITORY_ROOT / "infra" / "scripts" / "audit_live_data.py"
FLAT_TABLE_RELATIONS = {
    ("instrument_registry", "instrument_market_data"),
    ("instrument_registry", "instrument"),
    ("instrument_registry", "corporate_action_event"),
    ("portfolio", "transaction_record"),
    ("portfolio", "portfolio_daily_snapshot"),
    ("portfolio", "portfolio_daily_holding_snapshot"),
    ("portfolio", "target_set_record"),
    ("portfolio", "target_set_line_record"),
    ("portfolio", "taxonomy_record"),
    ("portfolio", "taxonomy_assignment_record"),
}


@pytest.fixture()
def audit_module() -> ModuleType:
    module_name = "portfolio_ops_test_audit_live_data"
    spec = importlib.util.spec_from_file_location(module_name, AUDIT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_flat_table_profile_accepts_only_final_heads(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_pair = ("20260717_0015", "20260716_0040")
    versions = dict(zip(("instrument_registry", "portfolio"), head_pair, strict=True))
    monkeypatch.setattr(
        audit_module,
        "_version_state",
        lambda _cursor, component: {
            "row_count": 1,
            "version": versions[component],
            "table_present": True,
        },
    )
    monkeypatch.setattr(
        audit_module,
        "_relation_kind",
        lambda _cursor, schema, relation: (
            "r" if (schema, relation) in FLAT_TABLE_RELATIONS else None
        ),
    )

    profile = audit_module._detect_schema_profile(object())

    assert profile.family == "flat-table"
    assert profile.status == "supported"
    assert "final" in profile.reason
    assert audit_module.FINAL_FLAT_TABLE_HEAD_PAIR == head_pair
    assert not hasattr(audit_module, "TEMPORARY_MIGRATION_SOURCE_HEAD_PAIR")
    assert not hasattr(audit_module, "SUPPORTED_FLAT_TABLE_HEAD_PAIRS")
    assert not hasattr(audit_module, "OVERHAUL_HEADS")
    assert not hasattr(audit_module, "_run_overhaul_audit")


def test_audit_contract_names_cover_registry_0015(
    audit_module: ModuleType,
) -> None:
    assert len(audit_module.AUDIT_CHECK_NAMES) == 23
    assert "instrument_quote_policy_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "market_data_price_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "market_data_fx_identity_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "fund_nav_current_projection_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "held_fund_recent_total_return_coverage" in audit_module.AUDIT_CHECK_NAMES
    assert "price_bar_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "cash_cumulative_nav_in_return_policy" not in audit_module.AUDIT_CHECK_NAMES
    assert not hasattr(audit_module, "CASH_CUMULATIVE_NAV_BASES_SQL")
    assert "fx-usd-hkd" in audit_module.MAINTAINED_FX_IDENTITIES_SQL
    assert "fx-usd-cny" in audit_module.MAINTAINED_FX_IDENTITIES_SQL
    assert audit_module.QUOTE_SELECTION_POLICY_ROLES == (
        "trading",
        "valuation",
        "total_return",
        "chart",
        "reference",
    )
    assert (
        audit_module.FUND_NAV_PROJECTION_METHOD_VERSION
        == "fund_nav_reinvestment_projection/v5"
    )
    assert audit_module.FUND_NAV_PROJECTION_METHOD_VERSION_SQL == (
        "'fund_nav_reinvestment_projection/v5'"
    )


def test_migration_source_heads_are_unsupported_after_cutover(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = {
        "instrument_registry": "20260712_0007",
        "portfolio": "20260711_0032",
    }
    monkeypatch.setattr(
        audit_module,
        "_version_state",
        lambda _cursor, component: {
            "row_count": 1,
            "version": versions[component],
            "table_present": True,
        },
    )
    monkeypatch.setattr(
        audit_module,
        "_relation_kind",
        lambda _cursor, schema, relation: (
            "r" if (schema, relation) in FLAT_TABLE_RELATIONS else None
        ),
    )

    profile = audit_module._detect_schema_profile(object())

    assert profile.family == "unsupported"
    assert profile.status == "unsupported"


def test_failed_overhaul_head_and_shape_are_unsupported(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_heads = {
        "instrument_registry": "20260714_0014",
        "portfolio": "20260714_0045",
    }
    monkeypatch.setattr(
        audit_module,
        "_version_state",
        lambda _cursor, component: {
            "row_count": 1,
            "version": failed_heads[component],
            "table_present": True,
        },
    )
    monkeypatch.setattr(
        audit_module,
        "_relation_kind",
        lambda _cursor, schema, relation: (
            "r"
            if (schema, relation) in FLAT_TABLE_RELATIONS
            or (schema, relation) == ("instrument_registry", "quote_series")
            else None
        ),
    )

    profile = audit_module._detect_schema_profile(object())

    assert profile.family == "unsupported"
    assert profile.status == "unsupported"


def test_twr_audit_cte_projects_daily_twr(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = audit_module.SchemaProfile(
        family="flat-table",
        status="supported",
        versions={
            "portfolio": {
                "row_count": 1,
                "version": "20260716_0040",
                "table_present": True,
            }
        },
        reason="test",
        capabilities={},
    )
    queries: list[str] = []

    class FakeContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self

    monkeypatch.setattr(audit_module.psycopg, "connect", lambda *_args, **_kwargs: FakeContext())
    monkeypatch.setattr(audit_module, "_begin_read_only", lambda _cursor: None)
    monkeypatch.setattr(audit_module, "_detect_schema_profile", lambda _cursor: profile)
    monkeypatch.setattr(audit_module, "_column_exists", lambda *_args, **_kwargs: False)

    def capture_scalar(_cursor, query: str):
        queries.append(query)
        return 0

    monkeypatch.setattr(audit_module, "_scalar", capture_scalar)

    database_url = "postgresql+psycopg://" + "user:" + "secret" + "@localhost/audit_db"
    audit_module._run_flat_table_audit(database_url)

    twr_query = next(query for query in queries if "AS recomputed_twr" in query)
    linked_projection = re.search(
        r"WITH linked AS \(\s*SELECT(?P<projection>.*?)"
        r"FROM portfolio\.portfolio_daily_snapshot",
        twr_query,
        flags=re.DOTALL,
    )
    assert linked_projection is not None
    assert re.search(
        r"^\s*daily_twr,\s*$",
        linked_projection.group("projection"),
        flags=re.MULTILINE,
    )
    fund_nav_projection_query = next(
        query
        for query in queries
        if "instrument_registry.fund_nav_current_projection" in query
    )
    assert "fund_nav_reinvestment_projection/v5" in fund_nav_projection_query
    assert "{FUND_NAV_PROJECTION_METHOD_VERSION_SQL}" not in (
        fund_nav_projection_query
    )
    held_fund_coverage_query = next(
        query
        for query in queries
        if "held_fund_recent_total_return_coverage" in query
        or (
            "latest_portfolio_dates" in query
            and "total_return_count * 2 < official_count" in query
        )
    )
    assert "held.reference_date - 120" in held_fund_coverage_query
    assert "latest_official_date - 14" in held_fund_coverage_query


def test_cli_requires_explicit_database_configuration(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PORTFOLIO_OPS_LOCAL_DATABASE_URL", raising=False)
    monkeypatch.delenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL", raising=False)

    with pytest.raises(SystemExit) as error:
        audit_module.main([])

    assert error.value.code == 2
    assert "a database URL is required" in capsys.readouterr().err


def test_environment_database_url_requires_canonical_local_target(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_url = "postgresql+psycopg://platform/database"
    local_url = "postgresql+psycopg://local/database"
    monkeypatch.delenv("PORTFOLIO_OPS_LOCAL_DATABASE_URL", raising=False)
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL", platform_url)
    assert audit_module._environment_database_url() is None

    monkeypatch.setenv("PORTFOLIO_OPS_LOCAL_DATABASE_URL", local_url)
    assert audit_module._environment_database_url() == local_url


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_normalizes_sqlalchemy_url_and_reports_database_name(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
) -> None:
    profile = audit_module.SchemaProfile(
        family="flat-table",
        status="supported",
        versions={},
        reason="test",
        capabilities={},
    )
    checks = [
        audit_module.AuditCheck(
            name="fixture",
            status="pass",
            value=0,
            limit=0,
            detail="fixture",
        )
    ]
    captured_urls: list[str] = []

    def fake_run_audit_report(database_url: str):
        captured_urls.append(database_url)
        return profile, checks

    monkeypatch.setattr(audit_module, "run_audit_report", fake_run_audit_report)
    database_url = "postgresql+psycopg://" + "user:" + "secret" + "@localhost/audit_fixture"
    monkeypatch.setenv("PORTFOLIO_OPS_LOCAL_DATABASE_URL", database_url)
    arguments = []
    if as_json:
        arguments.append("--json")

    assert audit_module.main(arguments) == 0

    output = capsys.readouterr().out
    assert captured_urls == [database_url.replace("postgresql+psycopg://", "postgresql://", 1)]
    if as_json:
        assert json.loads(output)["database_name"] == "audit_fixture"
    else:
        assert "database_name: audit_fixture" in output
