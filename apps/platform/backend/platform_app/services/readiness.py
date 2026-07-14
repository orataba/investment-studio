from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

from platform_app.core.settings import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[5]
INSTRUMENT_REGISTRY_MIGRATION_ROOT = PROJECT_ROOT / "infra" / "instrument_registry"


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
class OutboxReadinessSnapshot:
    fresh_successful_worker: bool
    no_dead_events: bool
    active_event_age_within_limit: bool


@dataclass(frozen=True)
class PlatformReadiness:
    database: ReadinessCheck
    migration_heads: MigrationReadinessCheck
    market_data_outbox_worker: ReadinessCheck
    market_data_outbox_dead_events: ReadinessCheck
    market_data_outbox_event_age: ReadinessCheck

    @property
    def ready(self) -> bool:
        return all(
            check.status == "pass"
            for check in (
                self.database,
                self.migration_heads,
                self.market_data_outbox_worker,
                self.market_data_outbox_dead_events,
                self.market_data_outbox_event_age,
            )
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "checks": {
                "database": self.database.as_dict(),
                "migration_heads": self.migration_heads.as_dict(),
                "market_data_outbox_worker": (self.market_data_outbox_worker.as_dict()),
                "market_data_outbox_dead_events": (
                    self.market_data_outbox_dead_events.as_dict()
                ),
                "market_data_outbox_event_age": (
                    self.market_data_outbox_event_age.as_dict()
                ),
            },
        }


@lru_cache(maxsize=1)
def expected_instrument_registry_migration_head() -> str:
    config = Config(str(INSTRUMENT_REGISTRY_MIGRATION_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str((INSTRUMENT_REGISTRY_MIGRATION_ROOT / "alembic").resolve()),
    )
    source_heads = ScriptDirectory.from_config(config).get_heads()
    if len(source_heads) != 1:
        raise RuntimeError(
            "instrument_registry migration chain must have exactly one source head"
        )
    return source_heads[0]


def _read_instrument_registry_database_heads(
    session: Session,
) -> tuple[str, ...] | None:
    session.execute(text("SELECT 1"))
    relation_exists = session.scalar(
        text("SELECT to_regclass('instrument_registry.alembic_version') IS NOT NULL")
    )
    if relation_exists is not True:
        return None
    versions = session.scalars(
        text('SELECT version_num FROM "instrument_registry".alembic_version')
    ).all()
    return tuple(sorted(str(version) for version in versions))


def _read_outbox_readiness_snapshot(
    session: Session,
    *,
    worker_max_age_seconds: int,
    event_max_age_seconds: int,
) -> OutboxReadinessSnapshot:
    row = (
        session.execute(
            text(
                """
                SELECT
                    EXISTS (
                        SELECT 1
                        FROM instrument_registry.market_data_outbox_worker_heartbeat
                        WHERE worker_state = 'running'
                          AND heartbeat_at >= clock_timestamp() - make_interval(
                              secs => :worker_max_age_seconds
                          )
                          AND last_successful_poll_at IS NOT NULL
                          AND last_successful_poll_at >= clock_timestamp() - make_interval(
                              secs => :worker_max_age_seconds
                          )
                    ) AS fresh_successful_worker,
                    NOT EXISTS (
                        SELECT 1
                        FROM instrument_registry.market_data_outbox_event
                        WHERE status = 'dead'
                    ) AS no_dead_events,
                    NOT EXISTS (
                        SELECT 1
                        FROM instrument_registry.market_data_outbox_event
                        WHERE status IN ('pending', 'processing')
                          AND created_at < clock_timestamp() - make_interval(
                              secs => :event_max_age_seconds
                          )
                    ) AS active_event_age_within_limit
                """
            ),
            {
                "worker_max_age_seconds": worker_max_age_seconds,
                "event_max_age_seconds": event_max_age_seconds,
            },
        )
        .mappings()
        .one()
    )
    return OutboxReadinessSnapshot(
        fresh_successful_worker=row["fresh_successful_worker"] is True,
        no_dead_events=row["no_dead_events"] is True,
        active_event_age_within_limit=(row["active_event_age_within_limit"] is True),
    )


def _failed_platform_readiness(*, database_status: str) -> PlatformReadiness:
    failed = ReadinessCheck("fail")
    return PlatformReadiness(
        database=ReadinessCheck(database_status),
        migration_heads=MigrationReadinessCheck(
            "fail",
            {"instrument_registry": "fail"},
        ),
        market_data_outbox_worker=failed,
        market_data_outbox_dead_events=failed,
        market_data_outbox_event_age=failed,
    )


def check_platform_readiness(
    session: Session,
    settings: Settings,
) -> PlatformReadiness:
    try:
        expected_head = expected_instrument_registry_migration_head()
        actual_heads = _read_instrument_registry_database_heads(session)
    except Exception:
        return _failed_platform_readiness(database_status="fail")

    migration_ready = actual_heads == (expected_head,)
    if not migration_ready:
        return _failed_platform_readiness(database_status="pass")

    try:
        outbox = _read_outbox_readiness_snapshot(
            session,
            worker_max_age_seconds=(
                settings.market_data_outbox_worker_readiness_max_age_seconds
            ),
            event_max_age_seconds=(
                settings.market_data_outbox_event_readiness_max_age_seconds
            ),
        )
    except Exception:
        outbox = OutboxReadinessSnapshot(False, False, False)

    return PlatformReadiness(
        database=ReadinessCheck("pass"),
        migration_heads=MigrationReadinessCheck(
            "pass",
            {"instrument_registry": "pass"},
        ),
        market_data_outbox_worker=ReadinessCheck(
            "pass" if outbox.fresh_successful_worker else "fail"
        ),
        market_data_outbox_dead_events=ReadinessCheck(
            "pass" if outbox.no_dead_events else "fail"
        ),
        market_data_outbox_event_age=ReadinessCheck(
            "pass" if outbox.active_event_age_within_limit else "fail"
        ),
    )


__all__ = [
    "OutboxReadinessSnapshot",
    "PlatformReadiness",
    "check_platform_readiness",
    "expected_instrument_registry_migration_head",
]
