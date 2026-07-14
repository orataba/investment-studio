from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

from portfolio_app.core.settings import Settings
from portfolio_ops_calculation_core import LifecycleRepository


PORTFOLIO_DAILY_CALCULATION_KIND = "portfolio_daily"
PROJECT_ROOT = Path(__file__).resolve().parents[5]
_MIGRATION_ROOTS = {
    "portfolio": PROJECT_ROOT / "apps" / "portfolio" / "backend",
    "calculation_registry": PROJECT_ROOT / "infra" / "calculation_registry",
    "instrument_registry": PROJECT_ROOT / "infra" / "instrument_registry",
}


@dataclass(frozen=True)
class ReadinessCheck:
    status: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status}


@dataclass(frozen=True)
class MigrationReadinessCheck:
    status: str
    components: Mapping[str, str]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "components": dict(self.components),
        }


@dataclass(frozen=True)
class PortfolioReadiness:
    database: ReadinessCheck
    migration_heads: MigrationReadinessCheck
    portfolio_daily_worker: ReadinessCheck

    @property
    def ready(self) -> bool:
        return (
            self.database.status == "pass"
            and self.migration_heads.status == "pass"
            and self.portfolio_daily_worker.status == "pass"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "checks": {
                "database": self.database.as_dict(),
                "migration_heads": self.migration_heads.as_dict(),
                "portfolio_daily_worker": self.portfolio_daily_worker.as_dict(),
            },
        }


@lru_cache(maxsize=1)
def expected_migration_heads() -> Mapping[str, str]:
    heads: dict[str, str] = {}
    for component, migration_root in _MIGRATION_ROOTS.items():
        config = Config(str(migration_root / "alembic.ini"))
        config.set_main_option(
            "script_location",
            str((migration_root / "alembic").resolve()),
        )
        source_heads = ScriptDirectory.from_config(config).get_heads()
        if len(source_heads) != 1:
            raise RuntimeError(
                f"{component} migration chain must have exactly one source head"
            )
        heads[component] = source_heads[0]
    return heads


def _read_database_heads(
    session: Session,
    *,
    components: tuple[str, ...],
) -> Mapping[str, tuple[str, ...] | None]:
    session.execute(text("SELECT 1"))
    heads: dict[str, tuple[str, ...] | None] = {}
    for component in components:
        relation_name = f"{component}.alembic_version"
        relation_exists = session.scalar(
            text("SELECT to_regclass(:relation_name) IS NOT NULL"),
            {"relation_name": relation_name},
        )
        if relation_exists is not True:
            heads[component] = None
            continue
        versions = session.scalars(
            text(f'SELECT version_num FROM "{component}".alembic_version')
        ).all()
        heads[component] = tuple(sorted(str(version) for version in versions))
    return heads


def check_portfolio_readiness(
    session: Session,
    settings: Settings,
) -> PortfolioReadiness:
    expected_heads: Mapping[str, str]
    try:
        expected_heads = expected_migration_heads()
        actual_heads = _read_database_heads(
            session,
            components=tuple(expected_heads),
        )
    except Exception:
        failed_components = {
            component: "fail" for component in _MIGRATION_ROOTS
        }
        return PortfolioReadiness(
            database=ReadinessCheck("fail"),
            migration_heads=MigrationReadinessCheck(
                "fail",
                failed_components,
            ),
            portfolio_daily_worker=ReadinessCheck("fail"),
        )

    component_statuses = {
        component: (
            "pass"
            if actual_heads.get(component) == (expected_head,)
            else "fail"
        )
        for component, expected_head in expected_heads.items()
    }
    migrations_ready = all(
        status == "pass" for status in component_statuses.values()
    )

    try:
        worker_ready = LifecycleRepository.has_fresh_worker(
            session,
            calculation_kind=PORTFOLIO_DAILY_CALCULATION_KIND,
            max_age=timedelta(
                seconds=settings.calculation_worker_readiness_max_age_seconds
            ),
        )
    except Exception:
        worker_ready = False

    return PortfolioReadiness(
        database=ReadinessCheck("pass"),
        migration_heads=MigrationReadinessCheck(
            "pass" if migrations_ready else "fail",
            component_statuses,
        ),
        portfolio_daily_worker=ReadinessCheck(
            "pass" if worker_ready else "fail"
        ),
    )


__all__ = [
    "PORTFOLIO_DAILY_CALCULATION_KIND",
    "PortfolioReadiness",
    "check_portfolio_readiness",
    "expected_migration_heads",
]
