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

from watchlist_app.core.settings import Settings
from watchlist_app.repositories.sqlalchemy.recalc_jobs import (
    SQLAlchemyRecalcJobRepository,
)
from watchlist_app.repositories.sqlalchemy.recalc_invalidations import (
    SQLAlchemyRecalcInvalidationRepository,
)
from watchlist_app.repositories.sqlalchemy.recalc_workers import (
    SQLAlchemyRecalcWorkerRepository,
)


PROJECT_ROOT = Path(__file__).resolve().parents[5]
_MIGRATION_ROOTS = {
    "watchlist": PROJECT_ROOT / "apps" / "watchlist" / "backend",
    "instrument_registry": PROJECT_ROOT / "infra" / "instrument_registry",
}
recalc_worker_repository = SQLAlchemyRecalcWorkerRepository()
recalc_job_repository = SQLAlchemyRecalcJobRepository()
recalc_invalidation_repository = SQLAlchemyRecalcInvalidationRepository()


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
class WatchlistReadiness:
    database: ReadinessCheck
    migration_heads: MigrationReadinessCheck
    recalc_worker: ReadinessCheck
    recalc_source_event_dead_letters: ReadinessCheck
    recalc_invalidation_serviceability: ReadinessCheck

    @property
    def ready(self) -> bool:
        return (
            self.database.status == "pass"
            and self.migration_heads.status == "pass"
            and self.recalc_worker.status == "pass"
            and self.recalc_source_event_dead_letters.status == "pass"
            and self.recalc_invalidation_serviceability.status == "pass"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "checks": {
                "database": self.database.as_dict(),
                "migration_heads": self.migration_heads.as_dict(),
                "recalc_worker": self.recalc_worker.as_dict(),
                "recalc_source_event_dead_letters": (
                    self.recalc_source_event_dead_letters.as_dict()
                ),
                "recalc_invalidation_serviceability": (
                    self.recalc_invalidation_serviceability.as_dict()
                ),
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


def check_watchlist_readiness(
    session: Session,
    settings: Settings,
) -> WatchlistReadiness:
    try:
        expected_heads = expected_migration_heads()
        actual_heads = _read_database_heads(
            session,
            components=tuple(expected_heads),
        )
    except Exception:
        return WatchlistReadiness(
            database=ReadinessCheck("fail"),
            migration_heads=MigrationReadinessCheck(
                "fail",
                {component: "fail" for component in _MIGRATION_ROOTS},
            ),
            recalc_worker=ReadinessCheck("fail"),
            recalc_source_event_dead_letters=ReadinessCheck("fail"),
            recalc_invalidation_serviceability=ReadinessCheck("fail"),
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
        worker_ready = recalc_worker_repository.has_fresh_worker(
            session,
            max_age=timedelta(
                seconds=settings.recalc_worker_readiness_max_age_seconds
            ),
        )
    except Exception:
        worker_ready = False

    try:
        no_source_event_dead_letters = (
            not recalc_job_repository.has_terminal_source_event_failure(session)
        )
    except Exception:
        no_source_event_dead_letters = False

    try:
        invalidations_serviceable = (
            not recalc_invalidation_repository.has_unserviceable_pending_invalidation(
                session
            )
        )
    except Exception:
        invalidations_serviceable = False

    return WatchlistReadiness(
        database=ReadinessCheck("pass"),
        migration_heads=MigrationReadinessCheck(
            "pass" if migrations_ready else "fail",
            component_statuses,
        ),
        recalc_worker=ReadinessCheck("pass" if worker_ready else "fail"),
        recalc_source_event_dead_letters=ReadinessCheck(
            "pass" if no_source_event_dead_letters else "fail"
        ),
        recalc_invalidation_serviceability=ReadinessCheck(
            "pass" if invalidations_serviceable else "fail"
        ),
    )


__all__ = [
    "WatchlistReadiness",
    "check_watchlist_readiness",
    "expected_migration_heads",
]
