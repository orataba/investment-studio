"""Forward-converge Portfolio Daily instrument-universe invalidation.

Revision ID: 20260714_0044
Revises: 20260714_0043

An early deployment stamped 0040 before its instrument-universe subscription
filter was corrected.  Editing that published revision cannot repair an
already-stamped database, so this revision installs the corrected function and
trigger contract explicitly.  Dependency subscriptions are intentionally
monotonic: conservative historical subscriptions remain valid, while archived
universe tombstones can no longer create new instrument/currency dependencies.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "20260714_0044"
down_revision = "20260714_0043"
branch_labels = None
depends_on = None


_LOCK_TIMEOUT = "5s"
_FUNCTION_CONTRACT_SHA256 = (
    "f61bb544bbf0ede4cd492b40dd9f1cdb11de9ecae3ecfebe57df720a06737f2d"
)
_TRIGGER_CONTRACT_SHA256 = (
    "4bdd8e70c14fd00de9e86857444085d66a79b878c6c9401fb004a20c03b01b3e"
)
_TRIGGER_NAMES = {
    "trg_40_pd_trg_instrument_universe_delete",
    "trg_40_pd_trg_instrument_universe_insert",
    "trg_40_pd_trg_instrument_universe_update",
}


def _require_postgresql_contract() -> sa.Connection:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError("20260714_0044 requires PostgreSQL")

    visible_schemas = set(
        connection.scalars(sa.text("SELECT unnest(current_schemas(false))")).all()
    )
    required_path = {"portfolio", "calculation_registry", "instrument_registry"}
    if missing_path := sorted(required_path - visible_schemas):
        raise RuntimeError(
            "0044 requires Alembic's application search_path to survive prior "
            f"revisions; missing={missing_path}."
        )

    required_relations = (
        "portfolio.portfolio_instrument_universe_record",
        "portfolio.portfolio_daily_dependency_subscription",
        "instrument_registry.instrument",
    )
    missing_relations = [
        relation
        for relation in required_relations
        if connection.scalar(
            sa.text("SELECT to_regclass(:relation_name)"),
            {"relation_name": relation},
        )
        is None
    ]
    if missing_relations:
        raise RuntimeError(
            f"0044 is missing required dependency relations: {missing_relations}."
        )
    if connection.scalar(
        sa.text(
            "SELECT to_regprocedure("
            "'portfolio.pd_lock_dependency_invalidation_protocol()')"
        )
    ) is None:
        raise RuntimeError("0044 dependency invalidation protocol function is missing.")

    owner_rows = connection.execute(
        sa.text(
            """
            SELECT namespace.nspname,
                   pg_get_userbyid(namespace.nspowner) AS owner_name
            FROM pg_namespace AS namespace
            WHERE namespace.nspname = ANY(:schema_names)
            """
        ),
        {"schema_names": sorted(required_path)},
    ).mappings().all()
    owners = {str(row["nspname"]): str(row["owner_name"]) for row in owner_rows}
    current_user = str(connection.scalar(sa.text("SELECT current_user")))
    if set(owners) != required_path or any(
        owner != current_user for owner in owners.values()
    ):
        raise RuntimeError(
            "0044 requires all dependency schemas to share the migration owner; "
            f"current_user={current_user!r}, owners={owners!r}."
        )
    return connection


def _lock_cutover(connection: sa.Connection) -> None:
    connection.exec_driver_sql(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    try:
        connection.execute(
            sa.text(
                "SELECT portfolio.pd_lock_dependency_invalidation_protocol()"
            )
        )
        connection.exec_driver_sql(
            "LOCK TABLE portfolio.portfolio_instrument_universe_record, "
            "portfolio.portfolio_daily_dependency_subscription "
            "IN ACCESS EXCLUSIVE MODE"
        )
    except sa.exc.DBAPIError as error:
        raise RuntimeError(
            "0044 could not acquire the dependency-invalidation cutover locks "
            f"within {_LOCK_TIMEOUT}; migration failed closed."
        ) from error


def _replace_instrument_universe_function(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            r"""
CREATE OR REPLACE FUNCTION portfolio.pd_trg_instrument_universe()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_scope_ids varchar[];
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP <> 'DELETE' THEN
                INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                    scope_id, dependency_kind, dependency_key, source_reason_code
                ) SELECT DISTINCT portfolio_id, 'instrument', instrument_id, 'instrument_universe'
                  FROM new_rows WHERE status = 'active' ON CONFLICT DO NOTHING;
                INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                    scope_id, dependency_kind, dependency_key, source_reason_code
                ) SELECT DISTINCT n.portfolio_id, 'currency', i.currency, 'instrument_universe'
                  FROM new_rows n JOIN instrument_registry.instrument i USING (instrument_id)
                  WHERE n.status = 'active'
                  ON CONFLICT DO NOTHING;
            END IF;
            IF TG_OP = 'INSERT' THEN SELECT array_agg(DISTINCT portfolio_id) INTO v_scope_ids FROM new_rows;
            ELSIF TG_OP = 'DELETE' THEN SELECT array_agg(DISTINCT portfolio_id) INTO v_scope_ids FROM old_rows;
            ELSE SELECT array_agg(DISTINCT portfolio_id) INTO v_scope_ids FROM (
                SELECT portfolio_id FROM old_rows UNION SELECT portfolio_id FROM new_rows
            ) s; END IF;
            PERFORM * FROM portfolio.pd_invalidate_scopes(v_scope_ids,
                'portfolio_daily_dependency_changed',
                jsonb_build_object('source_relation','portfolio.portfolio_instrument_universe_record','operation',TG_OP));
            RETURN NULL;
        END; $$;
            """
        )
    )
    connection.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION "
            "portfolio.pd_trg_instrument_universe() FROM PUBLIC"
        )
    )
    current_user = str(connection.scalar(sa.text("SELECT current_user")))
    quoted_owner = connection.dialect.identifier_preparer.quote(current_user)
    connection.exec_driver_sql(
        "ALTER FUNCTION portfolio.pd_trg_instrument_universe() "
        f"OWNER TO {quoted_owner}"
    )


def _replace_instrument_universe_triggers(connection: sa.Connection) -> None:
    table = "portfolio.portfolio_instrument_universe_record"
    for trigger_name in sorted(_TRIGGER_NAMES):
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {trigger_name} ON {table}"
        )
    for event, referencing in (
        ("insert", "REFERENCING NEW TABLE AS new_rows"),
        ("update", "REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows"),
        ("delete", "REFERENCING OLD TABLE AS old_rows"),
    ):
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER trg_40_pd_trg_instrument_universe_{event}
            AFTER {event.upper()} ON {table}
            {referencing}
            FOR EACH STATEMENT EXECUTE FUNCTION
                portfolio.pd_trg_instrument_universe()
            """
        )


def _seed_active_universe_dependencies(connection: sa.Connection) -> None:
    missing_active_instruments = connection.execute(
        sa.text(
            """
            SELECT universe.portfolio_id, universe.instrument_id
            FROM portfolio.portfolio_instrument_universe_record AS universe
            LEFT JOIN instrument_registry.instrument AS instrument
              ON instrument.instrument_id = universe.instrument_id
            WHERE universe.status = 'active'
              AND instrument.instrument_id IS NULL
            ORDER BY universe.portfolio_id, universe.instrument_id
            LIMIT 10
            """
        )
    ).all()
    if missing_active_instruments:
        raise RuntimeError(
            "0044 found active universe dependencies absent from the Instrument "
            f"Registry: {missing_active_instruments!r}."
        )

    connection.execute(
        sa.text(
            """
            INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                scope_id, dependency_kind, dependency_key, source_reason_code
            )
            SELECT DISTINCT universe.portfolio_id, 'instrument',
                            universe.instrument_id, 'migration_0044_active_universe'
            FROM portfolio.portfolio_instrument_universe_record AS universe
            WHERE universe.status = 'active'
            ON CONFLICT DO NOTHING
            """
        )
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                scope_id, dependency_kind, dependency_key, source_reason_code
            )
            SELECT DISTINCT universe.portfolio_id, 'currency',
                            instrument.currency, 'migration_0044_active_universe'
            FROM portfolio.portfolio_instrument_universe_record AS universe
            JOIN instrument_registry.instrument AS instrument
              ON instrument.instrument_id = universe.instrument_id
            WHERE universe.status = 'active'
            ON CONFLICT DO NOTHING
            """
        )
    )


@contextmanager
def _deterministic_deparser_search_path(
    connection: sa.Connection,
) -> Iterator[None]:
    previous = str(
        connection.scalar(sa.text("SELECT current_setting('search_path')"))
    )
    connection.execute(
        sa.text("SELECT set_config('search_path', 'pg_catalog, public', true)")
    )
    try:
        yield
    finally:
        connection.execute(
            sa.text("SELECT set_config('search_path', :search_path, true)"),
            {"search_path": previous},
        )


def _contract_digests(
    connection: sa.Connection,
) -> tuple[str, str, set[str], set[str]]:
    with _deterministic_deparser_search_path(connection):
        function_rows = connection.execute(
            sa.text(
                """
                SELECT procedure.proname,
                       pg_get_function_identity_arguments(procedure.oid)
                           AS identity_arguments,
                       pg_get_functiondef(procedure.oid) AS definition,
                       pg_get_userbyid(procedure.proowner) AS owner_name
                FROM pg_proc AS procedure
                JOIN pg_namespace AS namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = 'portfolio'
                  AND procedure.proname = 'pd_trg_instrument_universe'
                ORDER BY procedure.proname,
                         pg_get_function_identity_arguments(procedure.oid)
                """
            )
        ).mappings().all()
        trigger_rows = connection.execute(
            sa.text(
                """
                SELECT trigger_row.tgname, trigger_row.tgenabled,
                       trigger_row.tgtype, trigger_row.tgdeferrable,
                       trigger_row.tginitdeferred,
                       pg_get_triggerdef(trigger_row.oid, false) AS definition,
                       coalesce(trigger_constraint.condeferrable, false)
                           AS constraint_deferrable,
                       coalesce(trigger_constraint.condeferred, false)
                           AS constraint_initially_deferred
                FROM pg_trigger AS trigger_row
                JOIN pg_class AS relation ON relation.oid = trigger_row.tgrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                LEFT JOIN pg_constraint AS trigger_constraint
                  ON trigger_constraint.oid = trigger_row.tgconstraint
                WHERE namespace.nspname = 'portfolio'
                  AND relation.relname = 'portfolio_instrument_universe_record'
                  AND trigger_row.tgname LIKE
                      'trg_40_pd_trg_instrument_universe_%'
                  AND NOT trigger_row.tgisinternal
                ORDER BY trigger_row.tgname
                """
            )
        ).mappings().all()

    function_digest = hashlib.sha256(
        json.dumps(
            [
                [
                    str(row["proname"]),
                    str(row["identity_arguments"]),
                    str(row["definition"]),
                ]
                for row in function_rows
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    trigger_digest = hashlib.sha256(
        json.dumps(
            [
                [
                    str(row["tgname"]), str(row["tgenabled"]),
                    int(row["tgtype"]), bool(row["tgdeferrable"]),
                    bool(row["tginitdeferred"]), str(row["definition"]),
                    bool(row["constraint_deferrable"]),
                    bool(row["constraint_initially_deferred"]),
                ]
                for row in trigger_rows
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        function_digest,
        trigger_digest,
        {str(row["tgname"]) for row in trigger_rows},
        {str(row["owner_name"]) for row in function_rows},
    )


def _verify_contract(connection: sa.Connection) -> None:
    (
        function_digest,
        trigger_digest,
        trigger_names,
        function_owners,
    ) = _contract_digests(connection)
    if function_digest != _FUNCTION_CONTRACT_SHA256:
        raise RuntimeError(
            "0044 instrument-universe invalidation function does not match the "
            f"exact active-only contract (found digest {function_digest})."
        )
    if trigger_names != _TRIGGER_NAMES or trigger_digest != _TRIGGER_CONTRACT_SHA256:
        raise RuntimeError(
            "0044 instrument-universe invalidation triggers do not match the "
            f"exact enabled statement-transition contract (names={trigger_names!r}, "
            f"digest={trigger_digest})."
        )
    current_user = str(connection.scalar(sa.text("SELECT current_user")))
    if function_owners != {current_user}:
        raise RuntimeError(
            "0044 SECURITY DEFINER function owner does not match the validated "
            f"migration/schema owner: expected={current_user!r}, "
            f"found={function_owners!r}."
        )
    if bool(
        connection.scalar(
            sa.text(
                "SELECT has_function_privilege("
                "'public', 'portfolio.pd_trg_instrument_universe()', 'EXECUTE')"
            )
        )
    ):
        raise RuntimeError("0044 instrument-universe trigger function is public.")

    missing_dependency_count = int(
        connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM portfolio.portfolio_instrument_universe_record AS universe
                JOIN instrument_registry.instrument AS instrument
                  ON instrument.instrument_id = universe.instrument_id
                WHERE universe.status = 'active'
                  AND (
                      NOT EXISTS (
                          SELECT 1
                          FROM portfolio.portfolio_daily_dependency_subscription AS sub
                          WHERE sub.scope_id = universe.portfolio_id
                            AND sub.dependency_kind = 'instrument'
                            AND sub.dependency_key = universe.instrument_id
                      )
                      OR NOT EXISTS (
                          SELECT 1
                          FROM portfolio.portfolio_daily_dependency_subscription AS sub
                          WHERE sub.scope_id = universe.portfolio_id
                            AND sub.dependency_kind = 'currency'
                            AND sub.dependency_key = instrument.currency
                      )
                  )
                """
            )
        )
        or 0
    )
    if missing_dependency_count:
        raise RuntimeError(
            "0044 failed to seed every active instrument-universe dependency "
            f"({missing_dependency_count} rows incomplete)."
        )


def upgrade() -> None:
    connection = _require_postgresql_contract()
    _lock_cutover(connection)
    _replace_instrument_universe_function(connection)
    _replace_instrument_universe_triggers(connection)
    _seed_active_universe_dependencies(connection)
    _verify_contract(connection)


def downgrade() -> None:
    raise RuntimeError(
        "20260714_0044 is intentionally irreversible: archived universe "
        "tombstones must not regain dependency-subscription side effects."
    )
