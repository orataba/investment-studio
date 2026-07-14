from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POSTGRES_URL = (
    "postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/portfolio_ops"
)

pytestmark = pytest.mark.postgresql_integration


def _admin_url(database_url: str) -> str:
    return make_url(database_url).set(database="postgres").render_as_string(
        hide_password=False
    )


def _drop_test_database(admin_engine: Engine, database_name: str) -> None:
    """Wait out service-owned autovacuum without requiring pg_signal_backend."""

    deadline = monotonic() + 10.0
    while True:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            return
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "55006":
                raise
            if monotonic() >= deadline:
                raise RuntimeError(
                    "calculation registry test database still has active backends "
                    f"after engine disposal: database={database_name!r}"
                ) from error
            sleep(0.05)


@pytest.fixture
def migrated_registry(monkeypatch: pytest.MonkeyPatch) -> Engine:
    base_url = os.getenv("PORTFOLIO_OPS_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL)
    database_name = f"portfolio_ops_calculation_registry_{uuid4().hex[:10]}"
    database_url = make_url(base_url).set(database=database_name).render_as_string(
        hide_password=False
    )
    admin_engine = create_engine(_admin_url(base_url), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except Exception as error:
        pytest.fail(
            "PostgreSQL calculation-registry fixture requires a reachable "
            f"CREATEDB test role: {error}"
        )

    engine: Engine | None = None
    try:
        monkeypatch.setenv("PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE", database_name)
        monkeypatch.setenv(
            "PORTFOLIO_OPS_CALCULATION_REGISTRY_ALEMBIC_DATABASE_URL", database_url
        )
        config = Config(str(WORKSPACE_ROOT / "infra/calculation_registry/alembic.ini"))
        config.set_main_option(
            "script_location",
            str(WORKSPACE_ROOT / "infra/calculation_registry/alembic"),
        )
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        admin_engine.dispose()
        cleanup_engine = create_engine(_admin_url(base_url), isolation_level="AUTOCOMMIT")
        try:
            _drop_test_database(cleanup_engine, database_name)
        finally:
            cleanup_engine.dispose()


def _create_capturing_run(connection, *, suffix: str) -> tuple[str, str]:
    run_id = str(uuid4())
    manifest_id = str(uuid4())
    dedupe_key = (suffix * 64)[:64]
    connection.execute(
        text(
            """
            INSERT INTO calculation_registry.calculation_scope_generation (
                calculation_kind, scope_kind, scope_id, generation
            ) VALUES ('portfolio_daily', 'portfolio', :scope_id, 0)
            """
        ),
        {"scope_id": f"portfolio-{suffix}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO calculation_registry.calculation_run (
                run_id, calculation_kind, scope_kind, scope_id,
                requested_as_of, effective_as_of, timezone,
                methodology_version, input_schema_version, output_schema_version,
                captured_generation, dedupe_key, requested_by
            ) VALUES (
                :run_id, 'portfolio_daily', 'portfolio', :scope_id,
                :as_of, :as_of, 'Asia/Shanghai',
                'portfolio-daily-v1', 'portfolio-daily-input-v1',
                'portfolio-daily-output-v1', 0, :dedupe_key, 'postgres-test'
            )
            """
        ),
        {
            "run_id": run_id,
            "scope_id": f"portfolio-{suffix}",
            "as_of": date(2026, 7, 14),
            "dedupe_key": dedupe_key,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO calculation_registry.calculation_input_manifest (
                manifest_id, run_id, captured_generation, schema_version
            ) VALUES (:manifest_id, :run_id, 0, 'portfolio-daily-input-v1')
            """
        ),
        {"manifest_id": manifest_id, "run_id": run_id},
    )
    return run_id, manifest_id


def test_round_half_even_matches_positive_and_negative_financial_ties(
    migrated_registry: Engine,
) -> None:
    with migrated_registry.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                    calculation_registry.round_half_even(1.005::numeric, 2),
                    calculation_registry.round_half_even(1.015::numeric, 2),
                    calculation_registry.round_half_even(-1.005::numeric, 2),
                    calculation_registry.round_half_even(-1.015::numeric, 2),
                    calculation_registry.round_half_even(1.006::numeric, 2),
                    calculation_registry.round_half_even(
                        0.1234567890123456785::numeric, 18
                    )
                """
            )
        ).one()
    assert tuple(row) == (
        Decimal("1.00"),
        Decimal("1.02"),
        Decimal("-1.00"),
        Decimal("-1.02"),
        Decimal("1.01"),
        Decimal("0.123456789012345678"),
    )


def test_numeric_rounding_matches_decimal_at_financial_and_method_scales(
    migrated_registry: Engine,
) -> None:
    fixed_cases = (
        (Decimal("1.0333333333333333335"), 18),
        (Decimal("-1.0333333333333333335"), 18),
        (Decimal("123456789012345678901234567890.123456785"), 8),
        (Decimal("1.000000000000000000000000000000000000000000000000005"), 50),
        (Decimal("5E-101"), 100),
    )
    significant_cases = (
        Decimal("1.234567890123456789012345678901234567890123456789055"),
        Decimal("-1.234567890123456789012345678901234567890123456789055"),
        Decimal("0.000000000000000000000000000000000000000000000000001234567890123456789"),
        Decimal("99999999999999999999999999999999.555"),
        Decimal("-0." + ("9" * 51)),
    )

    with migrated_registry.connect() as connection:
        fixed_actual = tuple(
            connection.execute(
                text(
                    "SELECT calculation_registry.round_half_even(:value, :scale)"
                ),
                {"value": value, "scale": scale},
            ).scalar_one()
            for value, scale in fixed_cases
        )
        significant_actual = tuple(
            connection.execute(
                text(
                    "SELECT calculation_registry.round_significant_half_even("
                    ":value, 50)"
                ),
                {"value": value},
            ).scalar_one()
            for value in significant_cases
        )

    fixed_expected: list[Decimal] = []
    for value, scale in fixed_cases:
        with localcontext() as context:
            context.prec = max(len(value.as_tuple().digits), scale + 40)
            context.rounding = ROUND_HALF_EVEN
            fixed_expected.append(value.quantize(Decimal((0, (1,), -scale))))
    significant_expected: list[Decimal] = []
    for value in significant_cases:
        with localcontext() as context:
            context.prec = 50
            context.rounding = ROUND_HALF_EVEN
            significant_expected.append(+value)

    assert fixed_actual == tuple(fixed_expected)
    assert significant_actual == tuple(significant_expected)


def test_significant_division_matches_decimal_without_postgres_division_rounding(
    migrated_registry: Engine,
) -> None:
    cases = (
        (Decimal("1"), Decimal("7"), 50),
        (Decimal("1"), Decimal("7.000000000000000001"), 50),
        (Decimal("-1"), Decimal("7"), 50),
        (Decimal("1"), Decimal("-7"), 50),
        (Decimal("1"), Decimal("8"), 2),
        (Decimal("3"), Decimal("8"), 2),
        (Decimal("999999999999999999"), Decimal("1000000000000000000"), 50),
    )

    with migrated_registry.connect() as connection:
        actual = tuple(
            connection.execute(
                text(
                    "SELECT calculation_registry.divide_significant_half_even("
                    ":numerator, :denominator, :digits)"
                ),
                {
                    "numerator": numerator,
                    "denominator": denominator,
                    "digits": digits,
                },
            ).scalar_one()
            for numerator, denominator, digits in cases
        )
        definition = connection.scalar(
            text(
                "SELECT pg_get_functiondef("
                "'calculation_registry.divide_significant_half_even("
                "numeric,numeric,integer)'::regprocedure)"
            )
        )

    expected: list[Decimal] = []
    for numerator, denominator, digits in cases:
        with localcontext() as context:
            context.prec = digits
            context.rounding = ROUND_HALF_EVEN
            expected.append(context.divide(numerator, denominator))

    assert actual == tuple(expected)
    assert actual[0] == Decimal(
        "0.14285714285714285714285714285714285714285714285714"
    )
    assert actual[4:6] == (Decimal("0.12"), Decimal("0.38"))
    assert definition is not None
    assert "div(v_scaled_numerator, v_denominator)" in definition
    assert "mod(v_scaled_numerator, v_denominator)" in definition
    assert " / " not in definition


def test_run_knowledge_cutoff_is_database_transaction_timestamp(
    migrated_registry: Engine,
) -> None:
    with migrated_registry.begin() as connection:
        run_id, _ = _create_capturing_run(connection, suffix="c")
        cutoff_at, transaction_at = connection.execute(
            text(
                """
                SELECT cutoff_at, transaction_timestamp()
                FROM calculation_registry.calculation_run
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id},
        ).one()
        assert cutoff_at == transaction_at

    with pytest.raises(
        DBAPIError,
        match="calculation_run_cutoff_must_equal_transaction_timestamp",
    ):
        with migrated_registry.begin() as connection:
            scope_id = "portfolio-forged-cutoff"
            connection.execute(
                text(
                    """
                    INSERT INTO calculation_registry.calculation_scope_generation (
                        calculation_kind, scope_kind, scope_id, generation
                    ) VALUES ('portfolio_daily', 'portfolio', :scope_id, 0)
                    """
                ),
                {"scope_id": scope_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO calculation_registry.calculation_run (
                        calculation_kind, scope_kind, scope_id,
                        requested_as_of, effective_as_of, cutoff_at, timezone,
                        methodology_version, input_schema_version,
                        output_schema_version, captured_generation, dedupe_key,
                        requested_by
                    ) VALUES (
                        'portfolio_daily', 'portfolio', :scope_id,
                        DATE '2026-07-14', DATE '2026-07-14',
                        transaction_timestamp() - interval '1 microsecond',
                        'Asia/Shanghai', 'portfolio-daily-v1',
                        'portfolio-daily-input-v1', 'portfolio-daily-output-v1',
                        0, :dedupe_key, 'postgres-test'
                    )
                    """
                ),
                {"scope_id": scope_id, "dedupe_key": "f" * 64},
            )


def _seal_and_queue(
    connection,
    *,
    run_id: str,
    manifest_id: str,
    manifest_hash: str,
    dedupe_key: str,
) -> str:
    connection.execute(
        text(
            """
            UPDATE calculation_registry.calculation_input_manifest
            SET status = 'sealed',
                canonical_manifest_hash = :manifest_hash,
                dependency_counts = '{"transaction": 1}'::jsonb,
                sealed_at = clock_timestamp()
            WHERE manifest_id = :manifest_id
            """
        ),
        {"manifest_hash": manifest_hash, "manifest_id": manifest_id},
    )
    connection.execute(
        text(
            """
            UPDATE calculation_registry.calculation_run
            SET status = 'queued', manifest_id = :manifest_id
            WHERE run_id = :run_id
            """
        ),
        {"manifest_id": manifest_id, "run_id": run_id},
    )
    job_id = str(uuid4())
    connection.execute(
        text(
            """
            INSERT INTO calculation_registry.calculation_job (
                job_id, run_id, dedupe_key
            ) VALUES (:job_id, :run_id, :dedupe_key)
            """
        ),
        {"job_id": job_id, "run_id": run_id, "dedupe_key": dedupe_key},
    )
    return job_id


def test_fenced_job_is_required_before_atomic_publication(
    migrated_registry: Engine,
) -> None:
    dedupe_key = "a" * 64
    manifest_hash = "b" * 64
    output_hash = "c" * 64
    with migrated_registry.begin() as connection:
        run_id, manifest_id = _create_capturing_run(connection, suffix="a")
        job_id = _seal_and_queue(
            connection,
            run_id=run_id,
            manifest_id=manifest_id,
            manifest_hash=manifest_hash,
            dedupe_key=dedupe_key,
        )
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_job
                SET status = 'leased', attempt = 1, fencing_token = 1,
                    lease_owner = 'worker-a', heartbeat_at = clock_timestamp(),
                    lease_expires_at = clock_timestamp() + interval '5 minutes'
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id},
        )

    with pytest.raises(DBAPIError, match="calculation_job_run_state_mismatch"):
        with migrated_registry.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_job
                    SET status = 'succeeded', lease_owner = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        completed_at = clock_timestamp()
                    WHERE job_id = :job_id
                    """
                ),
                {"job_id": job_id},
            )

    with migrated_registry.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_run
                SET status = 'running', started_at = clock_timestamp()
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        connection.execute(
            text(
                "SELECT calculation_registry.assert_run_output_writable"
                "(:run_id, 1, 'worker-a')"
            ),
            {"run_id": run_id},
        )

    with pytest.raises(DBAPIError, match="calculation_output_stale_fence"):
        with migrated_registry.begin() as connection:
            connection.execute(
                text(
                    "SELECT calculation_registry.assert_run_output_writable"
                    "(:run_id, 0, 'worker-a')"
                ),
                {"run_id": run_id},
            )

    publication_id = str(uuid4())
    with migrated_registry.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_job
                SET status = 'succeeded', lease_owner = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    completed_at = clock_timestamp()
                WHERE job_id = :job_id
                  AND lease_owner = 'worker-a' AND fencing_token = 1
                """
            ),
            {"job_id": job_id},
        )
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_run
                SET status = 'succeeded', completed_at = clock_timestamp()
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO calculation_registry.calculation_publication (
                    publication_id, run_id, manifest_id, calculation_kind,
                    scope_kind, scope_id, output_schema_version,
                    published_fencing_token, canonical_output_hash
                ) VALUES (
                    :publication_id, :run_id, :manifest_id, 'portfolio_daily',
                    'portfolio', 'portfolio-a', 'portfolio-daily-output-v1',
                    1, :output_hash
                )
                """
            ),
            {
                "publication_id": publication_id,
                "run_id": run_id,
                "manifest_id": manifest_id,
                "output_hash": output_hash,
            },
        )
        job = connection.execute(
            text(
                """
                SELECT status, lease_owner, lease_expires_at, heartbeat_at,
                       attempt, fencing_token
                FROM calculation_registry.calculation_job
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id},
        ).one()
        assert job == ("succeeded", None, None, None, 1, 1)

    with pytest.raises(
        DBAPIError,
        match="calculation_current_publication_incomplete_atomic_publish",
    ):
        with migrated_registry.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO calculation_registry.calculation_current_publication (
                        calculation_kind, scope_kind, scope_id, publication_id
                    ) VALUES (
                        'portfolio_daily', 'portfolio', 'portfolio-a', :publication_id
                    )
                    """
                ),
                {"publication_id": publication_id},
            )

    with migrated_registry.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO calculation_registry.calculation_current_publication (
                    calculation_kind, scope_kind, scope_id, publication_id
                ) VALUES ('portfolio_daily', 'portfolio', 'portfolio-a', :publication_id)
                """
            ),
            {"publication_id": publication_id},
        )
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_run
                SET status = 'published', published_output_hash = :output_hash
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id, "output_hash": output_hash},
        )

    with pytest.raises(DBAPIError, match="calculation_publication_immutable"):
        with migrated_registry.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_publication
                    SET canonical_output_hash = :other_hash
                    WHERE publication_id = :publication_id
                    """
                ),
                {"other_hash": "d" * 64, "publication_id": publication_id},
            )


def test_dependency_mutation_serializes_with_manifest_seal(
    migrated_registry: Engine,
) -> None:
    with migrated_registry.begin() as connection:
        run_id, manifest_id = _create_capturing_run(connection, suffix="e")
        connection.execute(text("CREATE SCHEMA producer_test"))
        connection.execute(
            text(
                """
                CREATE TABLE producer_test.typed_dependency (
                    dependency_id uuid PRIMARY KEY,
                    manifest_id uuid NOT NULL REFERENCES
                        calculation_registry.calculation_input_manifest(manifest_id)
                        ON DELETE RESTRICT,
                    payload_hash char(64) NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TRIGGER trg_typed_dependency_manifest
                BEFORE INSERT OR UPDATE OR DELETE
                ON producer_test.typed_dependency
                FOR EACH ROW EXECUTE FUNCTION
                    calculation_registry.guard_manifest_dependency_mutation()
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TRIGGER trg_typed_dependency_truncate
                BEFORE TRUNCATE ON producer_test.typed_dependency
                FOR EACH STATEMENT EXECUTE FUNCTION
                    calculation_registry.reject_truncate()
                """
            )
        )

    first = migrated_registry.connect()
    second = migrated_registry.connect()
    first_transaction = first.begin()
    try:
        first.execute(
            text(
                """
                INSERT INTO producer_test.typed_dependency (
                    dependency_id, manifest_id, payload_hash
                ) VALUES (:dependency_id, :manifest_id, :payload_hash)
                """
            ),
            {
                "dependency_id": str(uuid4()),
                "manifest_id": manifest_id,
                "payload_hash": "f" * 64,
            },
        )

        second_transaction = second.begin()
        try:
            second.execute(text("SET LOCAL lock_timeout = '200ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                second.execute(
                    text(
                        """
                        UPDATE calculation_registry.calculation_input_manifest
                        SET status = 'sealed',
                            canonical_manifest_hash = :manifest_hash,
                            dependency_counts = '{"typed_dependency": 1}'::jsonb,
                            sealed_at = clock_timestamp()
                        WHERE manifest_id = :manifest_id
                        """
                    ),
                    {"manifest_hash": "1" * 64, "manifest_id": manifest_id},
                )
        finally:
            second_transaction.rollback()

        first_transaction.commit()
    finally:
        if first_transaction.is_active:
            first_transaction.rollback()
        first.close()
        second.close()

    with migrated_registry.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_input_manifest
                SET status = 'sealed', canonical_manifest_hash = :manifest_hash,
                    dependency_counts = '{"typed_dependency": 1}'::jsonb,
                    sealed_at = clock_timestamp()
                WHERE manifest_id = :manifest_id
                """
            ),
            {"manifest_hash": "1" * 64, "manifest_id": manifest_id},
        )

    with pytest.raises(DBAPIError, match="calculation_manifest_not_building"):
        with migrated_registry.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO producer_test.typed_dependency (
                        dependency_id, manifest_id, payload_hash
                    ) VALUES (:dependency_id, :manifest_id, :payload_hash)
                    """
                ),
                {
                    "dependency_id": str(uuid4()),
                    "manifest_id": manifest_id,
                    "payload_hash": "2" * 64,
                },
            )

    with pytest.raises(DBAPIError, match="truncate_forbidden"):
        with migrated_registry.begin() as connection:
            connection.execute(text("TRUNCATE producer_test.typed_dependency"))

    with migrated_registry.begin() as connection:
        _, second_manifest_id = _create_capturing_run(connection, suffix="3")

    sealer = migrated_registry.connect()
    inserter = migrated_registry.connect()
    seal_transaction = sealer.begin()
    try:
        sealer.execute(
            text("SELECT calculation_registry.assert_manifest_building(:manifest_id)"),
            {"manifest_id": second_manifest_id},
        )
        insert_transaction = inserter.begin()
        try:
            inserter.execute(text("SET LOCAL lock_timeout = '200ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                inserter.execute(
                    text(
                        """
                        INSERT INTO producer_test.typed_dependency (
                            dependency_id, manifest_id, payload_hash
                        ) VALUES (:dependency_id, :manifest_id, :payload_hash)
                        """
                    ),
                    {
                        "dependency_id": str(uuid4()),
                        "manifest_id": second_manifest_id,
                        "payload_hash": "3" * 64,
                    },
                )
        finally:
            insert_transaction.rollback()

        sealer.execute(
            text(
                """
                UPDATE calculation_registry.calculation_input_manifest
                SET status = 'sealed', canonical_manifest_hash = :manifest_hash,
                    dependency_counts = '{}'::jsonb,
                    sealed_at = clock_timestamp()
                WHERE manifest_id = :manifest_id
                """
            ),
            {"manifest_hash": "4" * 64, "manifest_id": second_manifest_id},
        )
        seal_transaction.commit()
    finally:
        if seal_transaction.is_active:
            seal_transaction.rollback()
        sealer.close()
        inserter.close()

    with pytest.raises(DBAPIError, match="calculation_manifest_not_building"):
        with migrated_registry.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO producer_test.typed_dependency (
                        dependency_id, manifest_id, payload_hash
                    ) VALUES (:dependency_id, :manifest_id, :payload_hash)
                    """
                ),
                {
                    "dependency_id": str(uuid4()),
                    "manifest_id": second_manifest_id,
                    "payload_hash": "5" * 64,
                },
            )


def test_partial_output_attempt_isolated_by_published_fence(
    migrated_registry: Engine,
) -> None:
    dedupe_key = "6" * 64
    output_hash = "7" * 64
    with migrated_registry.begin() as connection:
        run_id, manifest_id = _create_capturing_run(connection, suffix="6")
        job_id = _seal_and_queue(
            connection,
            run_id=run_id,
            manifest_id=manifest_id,
            manifest_hash="8" * 64,
            dedupe_key=dedupe_key,
        )
        connection.execute(text("CREATE SCHEMA producer_output_test"))
        connection.execute(
            text(
                """
                CREATE TABLE producer_output_test.daily_output (
                    run_id uuid NOT NULL REFERENCES
                        calculation_registry.calculation_run(run_id)
                        ON DELETE RESTRICT,
                    output_fencing_token bigint NOT NULL,
                    worker_id varchar(255) NOT NULL,
                    output_key varchar(64) NOT NULL,
                    exact_value numeric(38, 18) NOT NULL,
                    PRIMARY KEY (run_id, output_fencing_token, output_key)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE FUNCTION producer_output_test.guard_output_insert()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    PERFORM calculation_registry.assert_run_output_writable(
                        NEW.run_id, NEW.output_fencing_token, NEW.worker_id
                    );
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TRIGGER trg_daily_output_insert
                BEFORE INSERT ON producer_output_test.daily_output
                FOR EACH ROW EXECUTE FUNCTION
                    producer_output_test.guard_output_insert()
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_job
                SET status = 'leased', attempt = 1, fencing_token = 1,
                    lease_owner = 'worker-old', heartbeat_at = clock_timestamp(),
                    lease_expires_at = clock_timestamp() + interval '500 milliseconds'
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id},
        )
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_run
                SET status = 'running', started_at = clock_timestamp()
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO producer_output_test.daily_output (
                    run_id, output_fencing_token, worker_id, output_key, exact_value
                ) VALUES (:run_id, 1, 'worker-old', 'portfolio_nav', 100.000000000000000000)
                """
            ),
            {"run_id": run_id},
        )

    with pytest.raises(DBAPIError, match="invalid_heartbeat_or_takeover"):
        with migrated_registry.begin() as connection:
            connection.execute(text("SELECT pg_sleep(0.6)"))
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_job
                    SET heartbeat_at = clock_timestamp(),
                        lease_expires_at = clock_timestamp() + interval '5 minutes'
                    WHERE job_id = :job_id AND status = 'leased'
                    """
                ),
                {"job_id": job_id},
            )

    with migrated_registry.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_job
                SET attempt = 2, fencing_token = 2, lease_owner = 'worker-new',
                    heartbeat_at = clock_timestamp(),
                    lease_expires_at = clock_timestamp() + interval '5 minutes'
                WHERE job_id = :job_id AND status = 'leased'
                """
            ),
            {"job_id": job_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO producer_output_test.daily_output (
                    run_id, output_fencing_token, worker_id, output_key, exact_value
                ) VALUES (:run_id, 2, 'worker-new', 'portfolio_nav', 101.000000000000000000)
                """
            ),
            {"run_id": run_id},
        )
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_job
                SET status = 'succeeded', lease_owner = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    completed_at = clock_timestamp()
                WHERE job_id = :job_id
                  AND lease_owner = 'worker-new' AND fencing_token = 2
                """
            ),
            {"job_id": job_id},
        )
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_run
                SET status = 'succeeded', completed_at = clock_timestamp()
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id},
        )

    with pytest.raises(DBAPIError, match="requires_succeeded_job"):
        with migrated_registry.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO calculation_registry.calculation_publication (
                        run_id, manifest_id, calculation_kind, scope_kind, scope_id,
                        output_schema_version, published_fencing_token,
                        canonical_output_hash
                    ) VALUES (
                        :run_id, :manifest_id, 'portfolio_daily', 'portfolio',
                        'portfolio-6', 'portfolio-daily-output-v1', 1, :output_hash
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "manifest_id": manifest_id,
                    "output_hash": output_hash,
                },
            )

    publication_id = str(uuid4())
    with migrated_registry.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO calculation_registry.calculation_publication (
                    publication_id, run_id, manifest_id, calculation_kind,
                    scope_kind, scope_id, output_schema_version,
                    published_fencing_token, canonical_output_hash
                ) VALUES (
                    :publication_id, :run_id, :manifest_id, 'portfolio_daily',
                    'portfolio', 'portfolio-6', 'portfolio-daily-output-v1',
                    2, :output_hash
                )
                """
            ),
            {
                "publication_id": publication_id,
                "run_id": run_id,
                "manifest_id": manifest_id,
                "output_hash": output_hash,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO calculation_registry.calculation_current_publication (
                    calculation_kind, scope_kind, scope_id, publication_id
                ) VALUES ('portfolio_daily', 'portfolio', 'portfolio-6', :publication_id)
                """
            ),
            {"publication_id": publication_id},
        )
        connection.execute(
            text(
                """
                UPDATE calculation_registry.calculation_run
                SET status = 'published', published_output_hash = :output_hash
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id, "output_hash": output_hash},
        )

    with migrated_registry.connect() as connection:
        raw_attempts = connection.execute(
            text(
                """
                SELECT output_fencing_token, exact_value
                FROM producer_output_test.daily_output
                WHERE run_id = :run_id
                ORDER BY output_fencing_token
                """
            ),
            {"run_id": run_id},
        ).all()
        published = connection.execute(
            text(
                """
                SELECT o.output_fencing_token, o.exact_value
                FROM calculation_registry.calculation_current_publication AS cp
                JOIN calculation_registry.calculation_publication AS p
                  ON p.publication_id = cp.publication_id
                JOIN producer_output_test.daily_output AS o
                  ON o.run_id = p.run_id
                 AND o.output_fencing_token = p.published_fencing_token
                WHERE cp.calculation_kind = 'portfolio_daily'
                  AND cp.scope_kind = 'portfolio'
                  AND cp.scope_id = 'portfolio-6'
                """
            )
        ).all()
        assert [row.output_fencing_token for row in raw_attempts] == [1, 2]
        assert [(row.output_fencing_token, str(row.exact_value)) for row in published] == [
            (2, "101.000000000000000000")
        ]
