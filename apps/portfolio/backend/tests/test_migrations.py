from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.migration_base_revision("20260715_0038")


BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"
RUNTIME_PACKAGE_NAMES = ("app", "portfolio_app")
RECONCILIATION_REVISION = "20260715_0032r"
RECONCILIATION_PARENT = "20260711_0032"
CANONICAL_CASH_REVISION = "20260715_0039"
HOLDING_KIND_IDENTITY_REVISION = "20260806_0043"


RECONCILIATION_INDEXES = (
    (
        "target_set_record",
        "ix_target_set_record_taxonomy_scope_type_effective",
        "ix_target_set_record_taxonomy_scope_type",
        ("taxonomy_id", "comparator_taxonomy_node_id", "target_set_type", "target_set_id"),
    ),
    (
        "portfolio_daily_holding_snapshot",
        "ix_portfolio_daily_holding_asset_date",
        "ix_portfolio_daily_holding_instrument_date",
        ("portfolio_id", "instrument_id", "as_of_date"),
    ),
    (
        "transaction_record",
        "ix_transaction_record_portfolio_asset_trade",
        "ix_transaction_record_portfolio_instrument_trade",
        ("portfolio_id", "instrument_id", "trade_date", "trade_at"),
    ),
)


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_portfolio_migrations_do_not_import_runtime_modules() -> None:
    for migration_path in VERSIONS_DIR.glob("*.py"):
        imported_modules = _imported_modules(migration_path.read_text(encoding="utf-8"))
        offenders = sorted(
            module
            for module in imported_modules
            if any(
                module == package or module.startswith(f"{package}.")
                for package in RUNTIME_PACKAGE_NAMES
            )
        )
        assert not offenders, f"{migration_path.name} imports runtime packages: {offenders}"


def _alembic_config() -> Config:
    from portfolio_app.core.settings import get_settings

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


def _install_sqlite_reconciliation_drift(connection: sa.Connection) -> None:
    for table_name in (
        "taxonomy_record",
        "taxonomy_assignment_record",
        "target_set_record",
    ):
        connection.exec_driver_sql(
            f"ALTER TABLE {table_name} ADD COLUMN effective_from DATE"
        )
        connection.exec_driver_sql(
            f"ALTER TABLE {table_name} ADD COLUMN effective_to DATE"
        )

    connection.exec_driver_sql(
        "DROP INDEX ix_target_set_record_taxonomy_scope_type"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_target_set_record_taxonomy_scope_type_effective "
        "ON target_set_record ("
        "taxonomy_id, comparator_taxonomy_node_id, target_set_type, "
        "effective_from, target_set_id)"
    )
    for table_name, legacy_name, current_name, columns in RECONCILIATION_INDEXES[1:]:
        connection.exec_driver_sql(f"DROP INDEX {current_name}")
        connection.exec_driver_sql(
            f"CREATE INDEX {legacy_name} ON {table_name} ({', '.join(columns)})"
        )


def _assert_reconciled_schema(connection: sa.Connection) -> None:
    inspector = sa.inspect(connection)
    for table_name in (
        "taxonomy_record",
        "taxonomy_assignment_record",
        "target_set_record",
    ):
        column_names = {
            str(column["name"])
            for column in inspector.get_columns(table_name)
        }
        assert "effective_from" not in column_names
        assert "effective_to" not in column_names

    for table_name, legacy_name, current_name, columns in RECONCILIATION_INDEXES:
        indexes = {
            str(index["name"]): index
            for index in inspector.get_indexes(table_name)
            if index.get("name")
        }
        assert legacy_name not in indexes
        assert tuple(indexes[current_name]["column_names"]) == columns
        assert not indexes[current_name]["unique"]

    rebalance_column = next(
        column
        for column in inspector.get_columns("research_settings_record")
        if column["name"] == "backtest_rebalance_frequency"
    )
    assert rebalance_column["default"] is None


def test_schema_reconciliation_repairs_sqlite_drift_and_downgrade_stays_canonical() -> None:
    from portfolio_app.db.session import get_engine

    config = _alembic_config()
    command.downgrade(config, RECONCILIATION_PARENT)
    engine = get_engine()
    with engine.begin() as connection:
        _install_sqlite_reconciliation_drift(connection)

    try:
        command.upgrade(config, RECONCILIATION_REVISION)
        with engine.connect() as connection:
            _assert_reconciled_schema(connection)

        command.downgrade(config, RECONCILIATION_PARENT)
        with engine.connect() as connection:
            _assert_reconciled_schema(connection)
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                RECONCILIATION_PARENT
            )
    finally:
        command.upgrade(config, "head")


def test_schema_reconciliation_rejects_non_null_effective_window() -> None:
    from portfolio_app.db.session import get_engine

    config = _alembic_config()
    command.downgrade(config, RECONCILIATION_PARENT)
    engine = get_engine()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE taxonomy_record ADD COLUMN effective_from DATE"
        )
        connection.exec_driver_sql(
            "ALTER TABLE taxonomy_record ADD COLUMN effective_to DATE"
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO taxonomy_record (
                    taxonomy_id,
                    portfolio_id,
                    name,
                    taxonomy_type,
                    purpose,
                    primary_assignment_scope,
                    planning_enabled,
                    budgeting_level,
                    status,
                    source_template_ref,
                    root_default_target_dimension,
                    effective_from,
                    effective_to
                ) VALUES (
                    'taxonomy-reconciliation-window',
                    'portfolio-ops',
                    'Reconciliation Window',
                    'custom',
                    NULL,
                    'instrument',
                    0,
                    NULL,
                    'active',
                    NULL,
                    'weight',
                    '2026-01-01',
                    NULL
                )
                """
            )
        )

    try:
        with pytest.raises(RuntimeError, match="non-NULL taxonomy effective windows"):
            command.upgrade(config, RECONCILIATION_REVISION)

        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM taxonomy_record "
                    "WHERE effective_from IS NOT NULL"
                )
            ) == 1
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                RECONCILIATION_PARENT
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE taxonomy_record SET effective_from = NULL")
            )
        command.upgrade(config, "head")


def _legacy_cash_risk_tables() -> tuple[sa.TableClause, ...]:
    taxonomy = sa.table(
        "taxonomy_record",
        sa.column("taxonomy_id", sa.String()),
        sa.column("portfolio_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("taxonomy_type", sa.String()),
        sa.column("purpose", sa.String()),
        sa.column("primary_assignment_scope", sa.String()),
        sa.column("planning_enabled", sa.Boolean()),
        sa.column("budgeting_level", sa.String()),
        sa.column("root_default_target_dimension", sa.String()),
        sa.column("status", sa.String()),
        sa.column("source_template_ref", sa.String()),
    )
    node = sa.table(
        "taxonomy_node_record",
        sa.column("taxonomy_node_id", sa.String()),
        sa.column("taxonomy_id", sa.String()),
        sa.column("parent_taxonomy_node_id", sa.String()),
        sa.column("node_name", sa.String()),
        sa.column("node_code", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("is_terminal", sa.Boolean()),
        sa.column("default_target_dimension", sa.String()),
        sa.column("status", sa.String()),
    )
    assignment = sa.table(
        "taxonomy_assignment_record",
        sa.column("assignment_id", sa.String()),
        sa.column("taxonomy_id", sa.String()),
        sa.column("target_scope", sa.String()),
        sa.column("target_entity_id", sa.String()),
        sa.column("taxonomy_node_id", sa.String()),
        sa.column("status", sa.String()),
    )
    target_set = sa.table(
        "target_set_record",
        sa.column("target_set_id", sa.String()),
        sa.column("taxonomy_id", sa.String()),
        sa.column("comparator_taxonomy_node_id", sa.String()),
        sa.column("target_set_type", sa.String()),
        sa.column("name", sa.String()),
        sa.column("weight_enabled", sa.Boolean()),
        sa.column("risk_budget_enabled", sa.Boolean()),
        sa.column("status", sa.String()),
        sa.column("notes", sa.String()),
    )
    target_line = sa.table(
        "target_set_line_record",
        sa.column("target_line_id", sa.String()),
        sa.column("target_set_id", sa.String()),
        sa.column("taxonomy_node_id", sa.String()),
        sa.column("target_member_type", sa.String()),
        sa.column("target_member_id", sa.String()),
        sa.column("target_weight", sa.Float()),
        sa.column("target_risk_share", sa.Float()),
        sa.column("notes", sa.String()),
    )
    return taxonomy, node, assignment, target_set, target_line


def _canonical_cash_migration_module():
    from alembic.script import ScriptDirectory

    revision = ScriptDirectory.from_config(_alembic_config()).get_revision(
        CANONICAL_CASH_REVISION
    )
    assert revision is not None
    return revision.module


def _insert_reserved_cash_taxonomy(
    connection: sa.Connection,
    *,
    taxonomy_id: str,
    node_ids: tuple[str, ...],
) -> None:
    taxonomy, node, _assignment, _target_set, _target_line = _legacy_cash_risk_tables()
    connection.execute(
        sa.insert(taxonomy).values(
            taxonomy_id=taxonomy_id,
            portfolio_id="portfolio-ops",
            name=taxonomy_id,
            taxonomy_type="custom",
            purpose=None,
            primary_assignment_scope="instrument",
            planning_enabled=True,
            budgeting_level="weight",
            root_default_target_dimension="weight",
            status="active",
            source_template_ref=None,
        )
    )
    connection.execute(
        sa.insert(node),
        [
            {
                "taxonomy_node_id": node_id,
                "taxonomy_id": taxonomy_id,
                "parent_taxonomy_node_id": None,
                "node_name": "Cash" if index == 0 else "现金",
                "node_code": "CASH" if index == 0 else None,
                "sort_order": index,
                "is_terminal": True,
                "default_target_dimension": "weight",
                "status": "active",
            }
            for index, node_id in enumerate(node_ids)
        ],
    )


def _insert_legacy_cash_risk_rows(connection: sa.Connection) -> sa.TableClause:
    taxonomy, node, assignment, target_set, target_line = _legacy_cash_risk_tables()
    taxonomy_id = "taxonomy-cash-risk-migration"
    connection.execute(
        sa.insert(taxonomy).values(
            taxonomy_id=taxonomy_id,
            portfolio_id="portfolio-ops",
            name="Cash Risk Migration",
            taxonomy_type="custom",
            purpose=None,
            primary_assignment_scope="instrument",
            planning_enabled=True,
            budgeting_level="weight_and_risk_budget",
            root_default_target_dimension="risk_budget",
            status="active",
            source_template_ref=None,
        )
    )
    connection.execute(
        sa.insert(node),
        [
            {
                "taxonomy_node_id": "node-labelled-cash",
                "taxonomy_id": taxonomy_id,
                "parent_taxonomy_node_id": None,
                "node_name": "Liquidity",
                "node_code": "CASH",
                "sort_order": 0,
                "is_terminal": True,
                "default_target_dimension": "weight",
                "status": "active",
            },
            {
                "taxonomy_node_id": "node-cash-subtree",
                "taxonomy_id": taxonomy_id,
                "parent_taxonomy_node_id": None,
                "node_name": "Reserve",
                "node_code": "RESERVE",
                "sort_order": 1,
                "is_terminal": False,
                "default_target_dimension": "risk_budget",
                "status": "active",
            },
            {
                "taxonomy_node_id": "node-cash-child",
                "taxonomy_id": taxonomy_id,
                "parent_taxonomy_node_id": "node-cash-subtree",
                "node_name": "Operating Cash",
                "node_code": "OPERATING",
                "sort_order": 0,
                "is_terminal": True,
                "default_target_dimension": "weight",
                "status": "active",
            },
            {
                "taxonomy_node_id": "node-mixed-subtree",
                "taxonomy_id": taxonomy_id,
                "parent_taxonomy_node_id": None,
                "node_name": "Mixed",
                "node_code": "MIXED",
                "sort_order": 2,
                "is_terminal": True,
                "default_target_dimension": "risk_budget",
                "status": "active",
            },
        ],
    )
    connection.execute(
        sa.insert(assignment),
        [
            {
                "assignment_id": "assignment-cash-child",
                "taxonomy_id": taxonomy_id,
                "target_scope": "cash_bucket",
                "target_entity_id": "cash-usd-main",
                "taxonomy_node_id": "node-cash-child",
                "status": "active",
            },
            {
                "assignment_id": "assignment-mixed-cash",
                "taxonomy_id": taxonomy_id,
                "target_scope": "cash_bucket",
                "target_entity_id": "cash-usd-main",
                "taxonomy_node_id": "node-mixed-subtree",
                "status": "active",
            },
            {
                "assignment_id": "assignment-mixed-risk",
                "taxonomy_id": taxonomy_id,
                "target_scope": "instrument",
                "target_entity_id": "equity-us-abbv",
                "taxonomy_node_id": "node-mixed-subtree",
                "status": "active",
            },
        ],
    )
    target_sets = [
        ("set-direct-combined", True, True),
        ("set-direct-risk-only", False, True),
        ("set-label-combined", True, True),
        ("set-subtree-risk-only", False, True),
        ("set-mixed-risk-only", False, True),
    ]
    connection.execute(
        sa.insert(target_set),
        [
            {
                "target_set_id": target_set_id,
                "taxonomy_id": taxonomy_id,
                "comparator_taxonomy_node_id": None,
                "target_set_type": "saa",
                "name": target_set_id,
                "weight_enabled": weight_enabled,
                "risk_budget_enabled": risk_budget_enabled,
                "status": "active" if target_set_id == "set-label-combined" else "draft",
                "notes": None,
            }
            for target_set_id, weight_enabled, risk_budget_enabled in target_sets
        ],
    )
    connection.execute(
        sa.insert(target_line),
        [
            {
                "target_line_id": "line-direct-preserve",
                "target_set_id": "set-direct-combined",
                "taxonomy_node_id": None,
                "target_member_type": "cash_bucket",
                "target_member_id": "__cash__",
                "target_weight": 0.2,
                "target_risk_share": 0.0,
                "notes": None,
            },
            {
                "target_line_id": "line-direct-delete",
                "target_set_id": "set-direct-risk-only",
                "taxonomy_node_id": None,
                "target_member_type": "cash_bucket",
                "target_member_id": "__cash__",
                "target_weight": None,
                "target_risk_share": 0.0,
                "notes": None,
            },
            {
                "target_line_id": "line-label-canonical",
                "target_set_id": "set-label-combined",
                "taxonomy_node_id": None,
                "target_member_type": "cash_bucket",
                "target_member_id": "__cash__",
                "target_weight": 0.05,
                "target_risk_share": 0.0,
                "notes": None,
            },
            {
                "target_line_id": "line-label-preserve",
                "target_set_id": "set-label-combined",
                "taxonomy_node_id": "node-labelled-cash",
                "target_member_type": "taxonomy_node",
                "target_member_id": "node-labelled-cash",
                "target_weight": 0.05,
                "target_risk_share": 0.0,
                "notes": None,
            },
            {
                "target_line_id": "line-label-risk-member",
                "target_set_id": "set-label-combined",
                "taxonomy_node_id": "node-mixed-subtree",
                "target_member_type": "taxonomy_node",
                "target_member_id": "node-mixed-subtree",
                "target_weight": 0.9,
                "target_risk_share": 1.0,
                "notes": None,
            },
            {
                "target_line_id": "line-label-subtree-cash",
                "target_set_id": "set-label-combined",
                "taxonomy_node_id": "node-cash-subtree",
                "target_member_type": "taxonomy_node",
                "target_member_id": "node-cash-subtree",
                "target_weight": 0.0,
                "target_risk_share": 0.0,
                "notes": None,
            },
            {
                "target_line_id": "line-subtree-delete",
                "target_set_id": "set-subtree-risk-only",
                "taxonomy_node_id": "node-cash-subtree",
                "target_member_type": "taxonomy_node",
                "target_member_id": "node-cash-subtree",
                "target_weight": None,
                "target_risk_share": 0.0,
                "notes": None,
            },
            {
                "target_line_id": "line-mixed-unchanged",
                "target_set_id": "set-mixed-risk-only",
                "taxonomy_node_id": "node-mixed-subtree",
                "target_member_type": "taxonomy_node",
                "target_member_id": "node-mixed-subtree",
                "target_weight": None,
                "target_risk_share": 1.0,
                "notes": None,
            },
        ],
    )
    return target_line


def test_cash_migrations_canonicalize_weights_and_remove_reserved_nodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from portfolio_app.core.settings import get_settings

    database_url = f"sqlite+pysqlite:///{tmp_path / 'cash-migrations.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = _alembic_config()
    command.upgrade(config, "20260711_0032")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO portfolio_record (
                    portfolio_id,
                    portfolio_name,
                    base_currency,
                    valuation_timezone,
                    valuation_cutoff_policy,
                    as_of_date,
                    nav,
                    day_change_value,
                    day_change_pct,
                    securities_count,
                    sort_order,
                    default_planning_taxonomy_id,
                    risk_policy_json
                ) VALUES (
                    'portfolio-ops',
                    'Portfolio Operations',
                    'USD',
                    'Asia/Shanghai',
                    'market_close',
                    NULL,
                    0,
                    0,
                    0,
                    0,
                    0,
                    NULL,
                    NULL
                )
                """
            )
        )
        target_line = _insert_legacy_cash_risk_rows(connection)

    try:
        command.upgrade(config, "head")

        with engine.connect() as connection:
            rows = {
                str(row["target_line_id"]): dict(row)
                for row in connection.execute(
                    sa.select(
                        target_line.c.target_line_id,
                        target_line.c.taxonomy_node_id,
                        target_line.c.target_member_type,
                        target_line.c.target_member_id,
                        target_line.c.target_weight,
                        target_line.c.target_risk_share,
                    ).where(target_line.c.target_line_id.like("line-%"))
                ).mappings()
        }
        assert rows["line-direct-preserve"]["target_risk_share"] is None
        assert "line-label-preserve" not in rows
        assert rows["line-label-canonical"]["taxonomy_node_id"] is None
        assert rows["line-label-canonical"]["target_member_type"] == "cash_bucket"
        assert rows["line-label-canonical"]["target_member_id"] == "__cash__"
        assert rows["line-label-canonical"]["target_weight"] == pytest.approx(0.1)
        assert rows["line-label-canonical"]["target_risk_share"] is None
        assert rows["line-label-subtree-cash"]["target_risk_share"] is None
        assert "line-direct-delete" not in rows
        assert "line-subtree-delete" not in rows
        assert rows["line-mixed-unchanged"]["target_risk_share"] == pytest.approx(1.0)
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM taxonomy_node_record "
                    "WHERE taxonomy_node_id = 'node-labelled-cash'"
                )
            ) == 0

        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    sa.update(target_line)
                    .where(target_line.c.target_line_id == "line-direct-preserve")
                    .values(target_risk_share=0.0)
                )

        with pytest.raises(RuntimeError, match="Restore the pre-upgrade database backup"):
            command.downgrade(config, "20260711_0032")
        with engine.connect() as connection:
            retained_rows = {
                str(row["target_line_id"]): dict(row)
                for row in connection.execute(
                    sa.select(
                        target_line.c.target_line_id,
                        target_line.c.target_member_type,
                        target_line.c.target_member_id,
                        target_line.c.target_risk_share,
                    ).where(target_line.c.target_line_id.like("line-%"))
                ).mappings()
            }
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == CANONICAL_CASH_REVISION
        assert retained_rows["line-direct-preserve"]["target_risk_share"] is None
        assert "line-label-preserve" not in retained_rows
        assert retained_rows["line-label-canonical"]["target_member_type"] == "cash_bucket"
        assert retained_rows["line-label-canonical"]["target_member_id"] == "__cash__"
        assert retained_rows["line-label-canonical"]["target_risk_share"] is None
        assert retained_rows["line-label-subtree-cash"]["target_risk_share"] is None
        assert "line-direct-delete" not in retained_rows
        assert "line-subtree-delete" not in retained_rows
    finally:
        engine.dispose()


def test_canonical_cash_migration_rejects_nested_reserved_cash_node() -> None:
    from portfolio_app.db.session import get_engine

    engine = get_engine()
    taxonomy, node, _assignment, _target_set, _target_line = _legacy_cash_risk_tables()
    taxonomy_id = "taxonomy-nested-reserved-cash"
    with engine.begin() as connection:
        connection.execute(
            sa.insert(taxonomy).values(
                taxonomy_id=taxonomy_id,
                portfolio_id="portfolio-ops",
                name="Nested Reserved Cash",
                taxonomy_type="custom",
                purpose=None,
                primary_assignment_scope="instrument",
                planning_enabled=True,
                budgeting_level="weight",
                root_default_target_dimension="weight",
                status="active",
                source_template_ref=None,
            )
        )
        connection.execute(
            sa.insert(node),
            [
                {
                    "taxonomy_node_id": "node-cash-parent",
                    "taxonomy_id": taxonomy_id,
                    "parent_taxonomy_node_id": None,
                    "node_name": "Liquidity",
                    "node_code": "LIQUIDITY",
                    "sort_order": 0,
                    "is_terminal": False,
                    "default_target_dimension": "weight",
                    "status": "active",
                },
                {
                    "taxonomy_node_id": "node-nested-reserved-cash",
                    "taxonomy_id": taxonomy_id,
                    "parent_taxonomy_node_id": "node-cash-parent",
                    "node_name": "Cash",
                    "node_code": "CASH",
                    "sort_order": 0,
                    "is_terminal": True,
                    "default_target_dimension": "weight",
                    "status": "active",
                },
            ],
        )

    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="only accepts root Cash nodes"):
            _canonical_cash_migration_module()._upgrade(connection)


def test_canonical_cash_migration_rejects_top_sleeve_bounds_reference() -> None:
    from portfolio_app.db.session import get_engine

    engine = get_engine()
    with engine.begin() as connection:
        research_settings = sa.Table(
            "research_settings_record",
            sa.MetaData(),
            autoload_with=connection,
        )
        _insert_reserved_cash_taxonomy(
            connection,
            taxonomy_id="taxonomy-cash-bounds",
            node_ids=("node-cash-bounds",),
        )
        connection.execute(
            sa.insert(research_settings).values(
                portfolio_id="portfolio-ops",
                as_of_mode="dynamic",
                lookback_days=90,
                calculation_frequency="auto",
                missing_return_policy="strict",
                target_dimension="scope_default",
                capital_mode="unit_notional",
                backtest_rebalance_frequency="1m",
                top_sleeve_weight_bounds_json=[
                    {
                        "taxonomy_node_id": "node-cash-bounds",
                        "min_weight": 0.1,
                        "max_weight": 0.2,
                    }
                ]
            )
        )

    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="Research settings that reference Cash"):
            _canonical_cash_migration_module()._upgrade(connection)
        assert connection.scalar(
            sa.text(
                "SELECT count(*) FROM taxonomy_node_record "
                "WHERE taxonomy_node_id = 'node-cash-bounds'"
            )
        ) == 1


def test_canonical_cash_migration_rejects_reserved_weight_in_risk_only_set() -> None:
    from portfolio_app.db.session import get_engine

    engine = get_engine()
    _taxonomy, _node, _assignment, target_set, target_line = _legacy_cash_risk_tables()
    with engine.begin() as connection:
        _insert_reserved_cash_taxonomy(
            connection,
            taxonomy_id="taxonomy-cash-risk-only",
            node_ids=("node-cash-risk-only",),
        )
        connection.execute(
            sa.insert(target_set).values(
                target_set_id="set-cash-risk-only",
                taxonomy_id="taxonomy-cash-risk-only",
                comparator_taxonomy_node_id=None,
                target_set_type="saa",
                name="Cash Risk Only",
                weight_enabled=False,
                risk_budget_enabled=True,
                status="draft",
                notes=None,
            )
        )
        connection.execute(
            sa.insert(target_line).values(
                target_line_id="line-cash-risk-only",
                target_set_id="set-cash-risk-only",
                taxonomy_node_id="node-cash-risk-only",
                target_member_type="taxonomy_node",
                target_member_id="node-cash-risk-only",
                target_weight=0.2,
                target_risk_share=None,
                notes=None,
            )
        )

    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="non-weight-enabled target set"):
            _canonical_cash_migration_module()._upgrade(connection)
        row = connection.execute(
            sa.select(target_line).where(
                target_line.c.target_line_id == "line-cash-risk-only"
            )
        ).mappings().one()
        assert row["target_member_type"] == "taxonomy_node"
        assert row["target_weight"] == pytest.approx(0.2)


def test_canonical_cash_migration_validates_every_group_before_dml() -> None:
    from portfolio_app.db.session import get_engine

    engine = get_engine()
    _taxonomy, node, _assignment, target_set, target_line = _legacy_cash_risk_tables()
    with engine.begin() as connection:
        _insert_reserved_cash_taxonomy(
            connection,
            taxonomy_id="taxonomy-cash-plan",
            node_ids=("node-cash-plan-a", "node-cash-plan-z"),
        )
        connection.execute(
            sa.insert(target_set),
            [
                {
                    "target_set_id": target_set_id,
                    "taxonomy_id": "taxonomy-cash-plan",
                    "comparator_taxonomy_node_id": None,
                    "target_set_type": "saa",
                    "name": target_set_id,
                    "weight_enabled": True,
                    "risk_budget_enabled": False,
                    "status": "draft",
                    "notes": None,
                }
                for target_set_id in ("set-cash-plan-a", "set-cash-plan-z")
            ],
        )
        connection.execute(
            sa.insert(target_line),
            [
                {
                    "target_line_id": "line-cash-plan-a-canonical",
                    "target_set_id": "set-cash-plan-a",
                    "taxonomy_node_id": None,
                    "target_member_type": "cash_bucket",
                    "target_member_id": "__cash__",
                    "target_weight": 0.1,
                    "target_risk_share": None,
                    "notes": None,
                },
                {
                    "target_line_id": "line-cash-plan-a-reserved",
                    "target_set_id": "set-cash-plan-a",
                    "taxonomy_node_id": "node-cash-plan-a",
                    "target_member_type": "taxonomy_node",
                    "target_member_id": "node-cash-plan-a",
                    "target_weight": 0.2,
                    "target_risk_share": None,
                    "notes": None,
                },
                {
                    "target_line_id": "line-cash-plan-z-canonical",
                    "target_set_id": "set-cash-plan-z",
                    "taxonomy_node_id": None,
                    "target_member_type": "cash_bucket",
                    "target_member_id": "__cash__",
                    "target_weight": 0.3,
                    "target_risk_share": None,
                    "notes": "canonical note",
                },
                {
                    "target_line_id": "line-cash-plan-z-reserved",
                    "target_set_id": "set-cash-plan-z",
                    "taxonomy_node_id": "node-cash-plan-z",
                    "target_member_type": "taxonomy_node",
                    "target_member_id": "node-cash-plan-z",
                    "target_weight": 0.4,
                    "target_risk_share": None,
                    "notes": "reserved note",
                },
            ],
        )

    with engine.begin() as connection:
        before_lines = [
            dict(row)
            for row in connection.execute(
                sa.select(target_line)
                .where(target_line.c.target_line_id.like("line-cash-plan-%"))
                .order_by(target_line.c.target_line_id)
            ).mappings()
        ]
        before_nodes = list(
            connection.scalars(
                sa.select(node.c.taxonomy_node_id)
                .where(node.c.taxonomy_id == "taxonomy-cash-plan")
                .order_by(node.c.taxonomy_node_id)
            )
        )
        with pytest.raises(RuntimeError, match="conflicting target notes"):
            _canonical_cash_migration_module()._upgrade(connection)
        after_lines = [
            dict(row)
            for row in connection.execute(
                sa.select(target_line)
                .where(target_line.c.target_line_id.like("line-cash-plan-%"))
                .order_by(target_line.c.target_line_id)
            ).mappings()
        ]
        after_nodes = list(
            connection.scalars(
                sa.select(node.c.taxonomy_node_id)
                .where(node.c.taxonomy_id == "taxonomy-cash-plan")
                .order_by(node.c.taxonomy_node_id)
            )
        )
        assert after_lines == before_lines
        assert after_nodes == before_nodes


def test_canonical_cash_migration_uses_reserved_keeper_and_preserves_note() -> None:
    from portfolio_app.db.session import get_engine

    engine = get_engine()
    _taxonomy, node, _assignment, target_set, target_line = _legacy_cash_risk_tables()
    with engine.begin() as connection:
        _insert_reserved_cash_taxonomy(
            connection,
            taxonomy_id="taxonomy-cash-no-canonical",
            node_ids=("node-cash-no-canonical-a", "node-cash-no-canonical-b"),
        )
        connection.execute(
            sa.insert(target_set).values(
                target_set_id="set-cash-no-canonical",
                taxonomy_id="taxonomy-cash-no-canonical",
                comparator_taxonomy_node_id=None,
                target_set_type="saa",
                name="Cash Without Canonical Keeper",
                weight_enabled=True,
                risk_budget_enabled=False,
                status="draft",
                notes=None,
            )
        )
        connection.execute(
            sa.insert(target_line),
            [
                {
                    "target_line_id": "line-cash-no-canonical-a",
                    "target_set_id": "set-cash-no-canonical",
                    "taxonomy_node_id": "node-cash-no-canonical-a",
                    "target_member_type": "taxonomy_node",
                    "target_member_id": "node-cash-no-canonical-a",
                    "target_weight": 0.1,
                    "target_risk_share": None,
                    "notes": "Treasury liquidity",
                },
                {
                    "target_line_id": "line-cash-no-canonical-b",
                    "target_set_id": "set-cash-no-canonical",
                    "taxonomy_node_id": "node-cash-no-canonical-b",
                    "target_member_type": "taxonomy_node",
                    "target_member_id": "node-cash-no-canonical-b",
                    "target_weight": 0.2,
                    "target_risk_share": None,
                    "notes": None,
                },
            ],
        )

    with engine.begin() as connection:
        _canonical_cash_migration_module()._upgrade(connection)
        rows = list(
            connection.execute(
                sa.select(target_line).where(
                    target_line.c.target_set_id == "set-cash-no-canonical"
                )
            ).mappings()
        )
        assert len(rows) == 1
        assert rows[0]["target_line_id"] == "line-cash-no-canonical-a"
        assert rows[0]["taxonomy_node_id"] is None
        assert rows[0]["target_member_type"] == "cash_bucket"
        assert rows[0]["target_member_id"] == "__cash__"
        assert rows[0]["target_weight"] == pytest.approx(0.3)
        assert rows[0]["notes"] == "Treasury liquidity"
        assert connection.scalar(
            sa.select(sa.func.count()).select_from(node).where(
                node.c.taxonomy_id == "taxonomy-cash-no-canonical"
            )
        ) == 0


def test_canonical_cash_migration_rejects_invalid_canonical_line() -> None:
    from portfolio_app.db.session import get_engine

    engine = get_engine()
    _taxonomy, _node, _assignment, target_set, target_line = _legacy_cash_risk_tables()
    with engine.begin() as connection:
        _insert_reserved_cash_taxonomy(
            connection,
            taxonomy_id="taxonomy-cash-invalid-canonical",
            node_ids=("node-cash-invalid-canonical",),
        )
        connection.execute(
            sa.insert(target_set).values(
                target_set_id="set-cash-invalid-canonical",
                taxonomy_id="taxonomy-cash-invalid-canonical",
                comparator_taxonomy_node_id=None,
                target_set_type="saa",
                name="Invalid Canonical Cash",
                weight_enabled=True,
                risk_budget_enabled=False,
                status="draft",
                notes=None,
            )
        )
        connection.execute(
            sa.insert(target_line),
            [
                {
                    "target_line_id": "line-cash-invalid-canonical",
                    "target_set_id": "set-cash-invalid-canonical",
                    "taxonomy_node_id": None,
                    "target_member_type": "cash_bucket",
                    "target_member_id": "__cash__",
                    "target_weight": -0.1,
                    "target_risk_share": None,
                    "notes": None,
                },
                {
                    "target_line_id": "line-cash-invalid-reserved",
                    "target_set_id": "set-cash-invalid-canonical",
                    "taxonomy_node_id": "node-cash-invalid-canonical",
                    "target_member_type": "taxonomy_node",
                    "target_member_id": "node-cash-invalid-canonical",
                    "target_weight": 0.2,
                    "target_risk_share": None,
                    "notes": None,
                },
            ],
        )

    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="invalid system cash target lines"):
            _canonical_cash_migration_module()._upgrade(connection)
        assert connection.scalar(
            sa.select(target_line.c.target_weight).where(
                target_line.c.target_line_id == "line-cash-invalid-canonical"
            )
        ) == pytest.approx(-0.1)


def test_canonical_cash_migration_rejects_unrepresentable_merged_weight() -> None:
    from portfolio_app.db.session import get_engine

    engine = get_engine()
    _taxonomy, _node, _assignment, target_set, target_line = _legacy_cash_risk_tables()
    with engine.begin() as connection:
        _insert_reserved_cash_taxonomy(
            connection,
            taxonomy_id="taxonomy-cash-overflow",
            node_ids=("node-cash-overflow",),
        )
        connection.execute(
            sa.insert(target_set).values(
                target_set_id="set-cash-overflow",
                taxonomy_id="taxonomy-cash-overflow",
                comparator_taxonomy_node_id=None,
                target_set_type="saa",
                name="Cash Overflow",
                weight_enabled=True,
                risk_budget_enabled=False,
                status="draft",
                notes=None,
            )
        )
        connection.execute(
            sa.insert(target_line),
            [
                {
                    "target_line_id": "line-cash-overflow-canonical",
                    "target_set_id": "set-cash-overflow",
                    "taxonomy_node_id": None,
                    "target_member_type": "cash_bucket",
                    "target_member_id": "__cash__",
                    "target_weight": 1e308,
                    "target_risk_share": None,
                    "notes": None,
                },
                {
                    "target_line_id": "line-cash-overflow-reserved",
                    "target_set_id": "set-cash-overflow",
                    "taxonomy_node_id": "node-cash-overflow",
                    "target_member_type": "taxonomy_node",
                    "target_member_id": "node-cash-overflow",
                    "target_weight": 1e308,
                    "target_risk_share": None,
                    "notes": None,
                },
            ],
        )

    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="cannot represent the merged target weight"):
            _canonical_cash_migration_module()._upgrade(connection)
        assert connection.scalar(
            sa.select(sa.func.count()).select_from(target_line).where(
                target_line.c.target_set_id == "set-cash-overflow"
            )
        ) == 2


def test_canonical_cash_migration_downgrade_requires_backup_restore() -> None:
    with pytest.raises(RuntimeError, match="Restore the pre-upgrade database backup"):
        _canonical_cash_migration_module().downgrade()


def test_cash_risk_migration_preflight_rejects_invalid_weight_enabled_cash_row() -> None:
    from portfolio_app.db.session import get_engine

    config = _alembic_config()
    command.downgrade(config, "20260711_0032")
    engine = get_engine()
    taxonomy, _node, _assignment, target_set, target_line = _legacy_cash_risk_tables()
    with engine.begin() as connection:
        connection.execute(
            sa.insert(taxonomy).values(
                taxonomy_id="taxonomy-invalid-cash-weight",
                portfolio_id="portfolio-ops",
                name="Invalid Cash Weight",
                taxonomy_type="custom",
                purpose=None,
                primary_assignment_scope="instrument",
                planning_enabled=True,
                budgeting_level="weight_and_risk_budget",
                root_default_target_dimension="weight",
                status="active",
                source_template_ref=None,
            )
        )
        connection.execute(
            sa.insert(target_set).values(
                target_set_id="set-invalid-cash-weight",
                taxonomy_id="taxonomy-invalid-cash-weight",
                comparator_taxonomy_node_id=None,
                target_set_type="saa",
                name="Invalid Cash Weight",
                weight_enabled=True,
                risk_budget_enabled=True,
                status="draft",
                notes=None,
            )
        )
        connection.execute(
            sa.insert(target_line).values(
                target_line_id="line-invalid-cash-weight",
                target_set_id="set-invalid-cash-weight",
                taxonomy_node_id=None,
                target_member_type="cash_bucket",
                target_member_id="__cash__",
                target_weight=None,
                target_risk_share=0.0,
                notes=None,
            )
        )

    try:
        with pytest.raises(RuntimeError, match="without valid non-negative target_weight"):
            command.upgrade(config, "head")

        with engine.connect() as connection:
            unchanged_risk_share = connection.scalar(
                sa.select(target_line.c.target_risk_share).where(
                    target_line.c.target_line_id == "line-invalid-cash-weight"
                )
            )
        assert unchanged_risk_share == pytest.approx(0.0)
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.delete(target_line).where(
                    target_line.c.target_line_id == "line-invalid-cash-weight"
                )
            )
            connection.execute(
                sa.delete(target_set).where(
                    target_set.c.target_set_id == "set-invalid-cash-weight"
                )
            )
            connection.execute(
                sa.delete(taxonomy).where(
                    taxonomy.c.taxonomy_id == "taxonomy-invalid-cash-weight"
                )
            )
        command.upgrade(config, "head")


def test_snapshot_coverage_migration_backfills_legacy_state_and_is_reversible() -> None:
    from portfolio_app.db.session import get_engine

    config = _alembic_config()
    engine = get_engine()
    snapshot = sa.table(
        "portfolio_daily_snapshot",
        sa.column("portfolio_id", sa.String()),
        sa.column("as_of_date", sa.Date()),
        sa.column("coverage_state", sa.String()),
        sa.column("nav", sa.Float()),
        sa.column("beginning_nav", sa.Float()),
        sa.column("ending_nav", sa.Float()),
        sa.column("daily_twr", sa.Float()),
        sa.column("cumulative_twr", sa.Float()),
        sa.column("drawdown", sa.Float()),
        sa.column("snapshot_json", sa.JSON()),
        sa.column("calculated_at", sa.String()),
        sa.column("valuation_coverage_state", sa.String()),
        sa.column("return_coverage_state", sa.String()),
        sa.column("book_pnl_coverage_state", sa.String()),
        sa.column("attribution_coverage_state", sa.String()),
    )
    legacy_date = date(2099, 1, 1)
    command.downgrade(config, "20260715_0033")
    with engine.begin() as connection:
        connection.execute(
            sa.insert(snapshot).values(
                portfolio_id="portfolio-ops",
                as_of_date=legacy_date,
                coverage_state="partial",
                nav=100.0,
                beginning_nav=100.0,
                ending_nav=100.0,
                daily_twr=0.0,
                cumulative_twr=0.0,
                drawdown=0.0,
                snapshot_json={"as_of_date": legacy_date.isoformat(), "coverage_state": "partial"},
                calculated_at="2099-01-01T00:00:00Z",
            )
        )

    try:
        command.upgrade(config, "20260715_0034")
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            column_names = {
                str(column["name"])
                for column in inspector.get_columns("portfolio_daily_snapshot")
            }
            index_names = {
                str(index["name"])
                for index in inspector.get_indexes("portfolio_daily_snapshot")
            }
            migrated_row = connection.execute(
                sa.select(
                    snapshot.c.valuation_coverage_state,
                    snapshot.c.return_coverage_state,
                    snapshot.c.book_pnl_coverage_state,
                    snapshot.c.attribution_coverage_state,
                ).where(
                    snapshot.c.portfolio_id == "portfolio-ops",
                    snapshot.c.as_of_date == legacy_date,
                )
            ).one()

        assert {
            "valuation_coverage_state",
            "return_coverage_state",
            "book_pnl_coverage_state",
            "attribution_coverage_state",
        }.issubset(column_names)
        assert "ix_portfolio_daily_snapshot_portfolio_valuation_coverage" in index_names
        assert tuple(migrated_row) == ("partial", "partial", "partial", "partial")

        command.downgrade(config, "20260715_0033")
        with engine.connect() as connection:
            downgraded_column_names = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns("portfolio_daily_snapshot")
            }
        assert "valuation_coverage_state" not in downgraded_column_names
        assert "return_coverage_state" not in downgraded_column_names
        assert "book_pnl_coverage_state" not in downgraded_column_names
        assert "attribution_coverage_state" not in downgraded_column_names
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                sa.delete(snapshot).where(
                    snapshot.c.portfolio_id == "portfolio-ops",
                    snapshot.c.as_of_date == legacy_date,
                )
            )


def test_fee_category_migration_backfills_unknown_and_is_reversible() -> None:
    from portfolio_app.db.session import get_engine

    config = _alembic_config()
    engine = get_engine()
    command.downgrade(config, "20260715_0034")

    try:
        with engine.connect() as connection:
            before_columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns("transaction_record")
            }
        assert "fee_category" not in before_columns

        command.upgrade(config, "20260715_0035")
        with engine.connect() as connection:
            after_columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns("transaction_record")
            }
            category_counts = connection.execute(
                sa.text(
                    "SELECT fee_category, count(*) "
                    "FROM transaction_record GROUP BY fee_category"
                )
            ).all()
        assert "fee_category" in after_columns
        assert all(category == "unknown" for category, _count in category_counts)

        command.downgrade(config, "20260715_0034")
        with engine.connect() as connection:
            downgraded_columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns("transaction_record")
            }
        assert "fee_category" not in downgraded_columns
    finally:
        command.upgrade(config, "head")


def test_holding_snapshot_migration_uses_holding_kind_as_identity() -> None:
    from portfolio_app.db.session import get_engine

    config = _alembic_config()
    engine = get_engine()
    command.upgrade(config, "20260804_0042")
    with engine.begin() as connection:
        state_count = connection.scalar(
            sa.text(
                "SELECT count(*) FROM portfolio_calculation_state "
                "WHERE portfolio_id = 'portfolio-ops'"
            )
        )
        if int(state_count or 0) == 0:
            connection.execute(
                sa.text(
                    "INSERT INTO portfolio_calculation_state "
                    "(portfolio_id, daily_snapshot_status) "
                    "VALUES ('portfolio-ops', 'current')"
                )
            )
        else:
            connection.execute(
                sa.text(
                    "UPDATE portfolio_calculation_state "
                    "SET daily_snapshot_status = 'current' "
                    "WHERE portfolio_id = 'portfolio-ops'"
                )
            )

    try:
        with engine.connect() as connection:
            before_inspector = sa.inspect(connection)
            before_columns = {
                str(column["name"])
                for column in before_inspector.get_columns(
                    "portfolio_daily_holding_snapshot"
                )
            }
            before_pk = tuple(
                before_inspector.get_pk_constraint(
                    "portfolio_daily_holding_snapshot"
                )["constrained_columns"]
            )
        assert "holding_kind" not in before_columns
        assert before_pk == (
            "portfolio_id",
            "as_of_date",
            "account_id",
            "instrument_id",
        )

        command.upgrade(config, HOLDING_KIND_IDENTITY_REVISION)
        with engine.begin() as connection:
            after_inspector = sa.inspect(connection)
            after_columns = {
                str(column["name"])
                for column in after_inspector.get_columns(
                    "portfolio_daily_holding_snapshot"
                )
            }
            after_pk = tuple(
                after_inspector.get_pk_constraint(
                    "portfolio_daily_holding_snapshot"
                )["constrained_columns"]
            )
            after_foreign_keys = {
                str(item["name"]): item
                for item in after_inspector.get_foreign_keys(
                    "portfolio_daily_holding_snapshot"
                )
            }
            insert_sql = sa.text(
                """
                INSERT INTO portfolio_daily_holding_snapshot (
                    portfolio_id, as_of_date, account_id, instrument_id,
                    holding_kind, currency, quantity, cost_basis,
                    cost_basis_base, last_price, market_value,
                    market_value_base, portfolio_weight, holding_json,
                    calculated_at
                ) VALUES (
                    'portfolio-ops', '2099-02-01', 'broker-us-core',
                    'option-contract-1', :holding_kind, 'USD', 1,
                    NULL, NULL, NULL, NULL, NULL, NULL, '{}',
                    '2099-02-01T00:00:00Z'
                )
                """
            )
            connection.execute(insert_sql, {"holding_kind": "position"})
            connection.execute(
                insert_sql,
                {"holding_kind": "option_obligation"},
            )
            row_count = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM portfolio_daily_holding_snapshot "
                    "WHERE instrument_id = 'option-contract-1'"
                )
            )
            calculation_status = connection.scalar(
                sa.text(
                    "SELECT daily_snapshot_status FROM portfolio_calculation_state "
                    "WHERE portfolio_id = 'portfolio-ops'"
                )
            )

        assert "holding_kind" in after_columns
        assert after_pk == (
            "portfolio_id",
            "as_of_date",
            "account_id",
            "instrument_id",
            "holding_kind",
        )
        assert "fk_portfolio_holding_snapshot_portfolio" in after_foreign_keys
        assert row_count == 2
        assert calculation_status == "stale"

        command.downgrade(config, "20260804_0042")
        with engine.connect() as connection:
            downgraded_inspector = sa.inspect(connection)
            downgraded_columns = {
                str(column["name"])
                for column in downgraded_inspector.get_columns(
                    "portfolio_daily_holding_snapshot"
                )
            }
            downgraded_pk = tuple(
                downgraded_inspector.get_pk_constraint(
                    "portfolio_daily_holding_snapshot"
                )["constrained_columns"]
            )
        assert "holding_kind" not in downgraded_columns
        assert downgraded_pk == before_pk
    finally:
        command.upgrade(config, "head")
