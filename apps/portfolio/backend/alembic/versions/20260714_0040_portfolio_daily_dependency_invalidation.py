"""Install exact Portfolio Daily dependency subscriptions and invalidation.

Revision ID: 20260714_0040
Revises: 20260714_0039

This migration is PostgreSQL-only.  It deliberately uses statement transition
tables and a transaction advisory lock; there is no SQLite or polling fallback.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0040"
down_revision = "20260714_0039"
branch_labels = None
depends_on = None


SOURCE_TRIGGERS: tuple[tuple[str, str, str, str], ...] = (
    ("portfolio", "portfolio_record", "insert", "pd_trg_portfolio_record"),
    ("portfolio", "portfolio_record", "update", "pd_trg_portfolio_record"),
    ("portfolio", "account_record", "insert", "pd_trg_account_record"),
    ("portfolio", "account_record", "update", "pd_trg_account_record"),
    ("portfolio", "account_record", "delete", "pd_trg_account_record"),
    ("portfolio", "transaction_revision_record", "insert", "pd_trg_transaction_revision"),
    ("portfolio", "portfolio_instrument_universe_record", "insert", "pd_trg_instrument_universe"),
    ("portfolio", "portfolio_instrument_universe_record", "update", "pd_trg_instrument_universe"),
    ("portfolio", "portfolio_instrument_universe_record", "delete", "pd_trg_instrument_universe"),
    ("portfolio", "taxonomy_record", "insert", "pd_trg_taxonomy_record"),
    ("portfolio", "taxonomy_record", "update", "pd_trg_taxonomy_record"),
    ("portfolio", "taxonomy_record", "delete", "pd_trg_taxonomy_record"),
    ("portfolio", "taxonomy_node_record", "insert", "pd_trg_taxonomy_child"),
    ("portfolio", "taxonomy_node_record", "update", "pd_trg_taxonomy_child"),
    ("portfolio", "taxonomy_node_record", "delete", "pd_trg_taxonomy_child"),
    ("portfolio", "taxonomy_assignment_record", "insert", "pd_trg_taxonomy_assignment"),
    ("portfolio", "taxonomy_assignment_record", "update", "pd_trg_taxonomy_assignment"),
    ("portfolio", "taxonomy_assignment_record", "delete", "pd_trg_taxonomy_assignment"),
    ("instrument_registry", "instrument", "insert", "pd_trg_instrument"),
    ("instrument_registry", "instrument", "update", "pd_trg_instrument"),
    ("instrument_registry", "instrument", "delete", "pd_trg_instrument"),
    ("instrument_registry", "instrument_identifier", "insert", "pd_trg_instrument_identifier"),
    ("instrument_registry", "instrument_identifier", "update", "pd_trg_instrument_identifier"),
    ("instrument_registry", "instrument_identifier", "delete", "pd_trg_instrument_identifier"),
    ("instrument_registry", "quote_series", "insert", "pd_trg_quote_series"),
    ("instrument_registry", "quote_series", "update", "pd_trg_quote_series"),
    ("instrument_registry", "quote_series", "delete", "pd_trg_quote_series"),
    ("instrument_registry", "quote_observation", "insert", "pd_trg_quote_observation"),
    ("instrument_registry", "quote_observation", "update", "pd_trg_quote_observation"),
    ("instrument_registry", "quote_observation", "delete", "pd_trg_quote_observation"),
    ("instrument_registry", "quote_observation_revision", "insert", "pd_trg_quote_revision"),
    ("instrument_registry", "quote_observation_revision", "update", "pd_trg_quote_revision"),
    ("instrument_registry", "corporate_action_event", "insert", "pd_trg_corporate_action"),
    ("instrument_registry", "corporate_action_event", "update", "pd_trg_corporate_action"),
    ("instrument_registry", "corporate_action_event", "delete", "pd_trg_corporate_action"),
)


def _require_postgresql_and_shared_owner() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError("20260714_0040 requires PostgreSQL")
    rows = connection.execute(
        sa.text(
            """
            SELECT n.nspname, pg_get_userbyid(n.nspowner) AS owner_name
            FROM pg_namespace AS n
            WHERE n.nspname = ANY(:schemas)
            """
        ),
        {"schemas": ["portfolio", "instrument_registry", "calculation_registry", "watchlist"]},
    ).mappings()
    owners = {str(row["nspname"]): str(row["owner_name"]) for row in rows}
    required = {"portfolio", "instrument_registry", "calculation_registry"}
    if missing := sorted(required.difference(owners)):
        raise RuntimeError(f"0040 missing required schemas: {missing}")
    current_user = str(connection.scalar(sa.text("SELECT current_user")))
    if any(owners[name] != current_user for name in required):
        raise RuntimeError(
            "0040 requires portfolio, instrument_registry and calculation_registry "
            f"to share migration owner {current_user!r}; actual={owners!r}"
        )
    if "watchlist" in owners and owners["watchlist"] != current_user:
        raise RuntimeError(
            "0040 requires all present application schemas to share one owner; "
            f"actual={owners!r}"
        )


def _create_subscription_storage() -> None:
    op.execute(
        """
        CREATE TABLE portfolio.portfolio_daily_dependency_subscription (
            calculation_kind varchar(64) NOT NULL DEFAULT 'portfolio_daily',
            scope_kind varchar(64) NOT NULL DEFAULT 'portfolio',
            scope_id varchar(255) NOT NULL,
            dependency_kind varchar(32) NOT NULL,
            dependency_key varchar(255) NOT NULL,
            subscribed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            source_reason_code varchar(64) NOT NULL,
            CONSTRAINT pk_pd_dependency_subscription PRIMARY KEY (
                calculation_kind, scope_kind, scope_id,
                dependency_kind, dependency_key
            ),
            CONSTRAINT fk_pd_dependency_subscription_scope FOREIGN KEY (
                calculation_kind, scope_kind, scope_id
            ) REFERENCES calculation_registry.calculation_scope_generation (
                calculation_kind, scope_kind, scope_id
            ) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_dependency_subscription_portfolio FOREIGN KEY (scope_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_dependency_subscription_scope CHECK (
                calculation_kind = 'portfolio_daily' AND scope_kind = 'portfolio'
            ),
            CONSTRAINT ck_pd_dependency_subscription_kind CHECK (
                dependency_kind IN (
                    'portfolio_config', 'transaction', 'account', 'taxonomy',
                    'instrument', 'currency', 'fx_market'
                )
            ),
            CONSTRAINT ck_pd_dependency_subscription_key CHECK (
                btrim(dependency_key) <> '' AND dependency_key = btrim(dependency_key)
                AND (
                    (dependency_kind = 'portfolio_config' AND dependency_key = scope_id)
                    OR dependency_kind IN ('transaction', 'account', 'taxonomy', 'instrument')
                    OR (dependency_kind = 'currency' AND dependency_key ~ '^[A-Z]{3}$')
                    OR (dependency_kind = 'fx_market' AND dependency_key = '*')
                )
            ),
            CONSTRAINT ck_pd_dependency_subscription_reason CHECK (
                btrim(source_reason_code) <> ''
                AND source_reason_code = btrim(source_reason_code)
            )
        );
        CREATE INDEX ix_pd_dependency_subscription_lookup
            ON portfolio.portfolio_daily_dependency_subscription (
                dependency_kind, dependency_key, scope_id
            );
        """
    )


def _create_protocol_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION portfolio.pd_lock_dependency_invalidation_protocol()
        RETURNS void
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT pg_advisory_xact_lock(
                hashtextextended('portfolio_daily.dependency_invalidation.v1', 0)
            )
        $$;

        CREATE FUNCTION portfolio.pd_recompute_intent_dedupe_key(
            p_scope_id varchar, p_generation bigint
        ) RETURNS char(64)
        LANGUAGE sql
        IMMUTABLE STRICT PARALLEL SAFE
        SET search_path = pg_catalog
        AS $$
            SELECT encode(sha256(
                int8send(octet_length(convert_to('calculation-intent-dedupe.v1', 'UTF8'))::bigint)
                    || convert_to('calculation-intent-dedupe.v1', 'UTF8')
                || int8send(octet_length(convert_to('portfolio_daily', 'UTF8'))::bigint)
                    || convert_to('portfolio_daily', 'UTF8')
                || int8send(octet_length(convert_to('portfolio', 'UTF8'))::bigint)
                    || convert_to('portfolio', 'UTF8')
                || int8send(octet_length(convert_to(p_scope_id, 'UTF8'))::bigint)
                    || convert_to(p_scope_id, 'UTF8')
                || int8send(octet_length(convert_to(p_generation::text, 'UTF8'))::bigint)
                    || convert_to(p_generation::text, 'UTF8')
            ), 'hex')::char(64)
        $$;

        CREATE FUNCTION portfolio.pd_guard_dependency_subscription()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'portfolio_daily_dependency_subscription_immutable'
                    USING ERRCODE = '55000';
            END IF;
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF NEW.dependency_kind = 'transaction' AND NOT EXISTS (
                SELECT 1 FROM portfolio.transaction_identity_record
                WHERE portfolio_id = NEW.scope_id
                  AND transaction_id = NEW.dependency_key
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_transaction_subscription_target_missing'
                    USING ERRCODE = '23503';
            ELSIF NEW.dependency_kind = 'account' AND NOT EXISTS (
                SELECT 1 FROM portfolio.account_record
                WHERE portfolio_id = NEW.scope_id AND account_id = NEW.dependency_key
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_account_subscription_target_missing'
                    USING ERRCODE = '23503';
            ELSIF NEW.dependency_kind = 'taxonomy' AND NOT EXISTS (
                SELECT 1 FROM portfolio.taxonomy_record
                WHERE portfolio_id = NEW.scope_id AND taxonomy_id = NEW.dependency_key
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_taxonomy_subscription_target_missing'
                    USING ERRCODE = '23503';
            ELSIF NEW.dependency_kind = 'instrument' AND NOT EXISTS (
                SELECT 1 FROM instrument_registry.instrument
                WHERE instrument_id = NEW.dependency_key
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_instrument_subscription_target_missing'
                    USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_reject_dependency_subscription_truncate()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
            RAISE EXCEPTION 'portfolio_daily_dependency_subscription_truncate_forbidden'
                USING ERRCODE = '55000';
        END;
        $$;

        CREATE FUNCTION portfolio.pd_invalidate_scopes(
            p_scope_ids varchar[], p_reason_code varchar, p_reason_context jsonb
        ) RETURNS TABLE(scope_id varchar, generation bigint, intent_id uuid)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            v_scope_id varchar(255);
            v_generation bigint;
            v_intent_id uuid;
            v_source_transaction_id text := pg_current_xact_id()::text;
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF p_reason_code IS NULL OR p_reason_code = ''
               OR p_reason_code <> btrim(p_reason_code)
               OR length(p_reason_code) > 64 THEN
                RAISE EXCEPTION 'portfolio_daily_invalidation_reason_invalid'
                    USING ERRCODE = '22023';
            END IF;
            IF p_reason_context IS NULL OR jsonb_typeof(p_reason_context) <> 'object' THEN
                RAISE EXCEPTION 'portfolio_daily_invalidation_context_invalid'
                    USING ERRCODE = '22023';
            END IF;
            FOR v_scope_id IN
                SELECT DISTINCT btrim(value)::varchar(255)
                FROM unnest(COALESCE(p_scope_ids, ARRAY[]::varchar[])) AS u(value)
                WHERE value IS NOT NULL AND btrim(value) <> ''
                ORDER BY 1
            LOOP
                -- A generation represents one atomic committed fact state,
                -- not the number of SQL statements used to write it.  A
                -- prior trigger in this transaction has already inserted an
                -- intent visible to this transaction only.  Reuse that exact
                -- generation/intent so multi-table writes do not create a
                -- chain of immediately superseded work.
                SELECT intent.requested_generation, intent.intent_id
                  INTO v_generation, v_intent_id
                FROM calculation_registry.calculation_scope_generation AS generation
                JOIN calculation_registry.calculation_recompute_intent AS intent
                  ON intent.calculation_kind = generation.calculation_kind
                 AND intent.scope_kind = generation.scope_kind
                 AND intent.scope_id = generation.scope_id
                 AND intent.requested_generation = generation.generation
                WHERE generation.calculation_kind = 'portfolio_daily'
                  AND generation.scope_kind = 'portfolio'
                  AND generation.scope_id = v_scope_id
                  AND intent.status = 'pending'
                  AND intent.reason_context ->> 'source_transaction_id'
                        = v_source_transaction_id;
                IF FOUND THEN
                    scope_id := v_scope_id;
                    generation := v_generation;
                    intent_id := v_intent_id;
                    RETURN NEXT;
                    CONTINUE;
                END IF;
                UPDATE calculation_registry.calculation_scope_generation AS sg
                SET generation = sg.generation + 1,
                    updated_at = clock_timestamp()
                WHERE sg.calculation_kind = 'portfolio_daily'
                  AND sg.scope_kind = 'portfolio'
                  AND sg.scope_id = v_scope_id
                RETURNING sg.generation INTO v_generation;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'portfolio_daily_scope_generation_missing: %', v_scope_id
                        USING ERRCODE = '40001';
                END IF;
                v_intent_id := gen_random_uuid();
                INSERT INTO calculation_registry.calculation_recompute_intent (
                    intent_id, calculation_kind, scope_kind, scope_id,
                    requested_generation, dedupe_key, reason_code, reason_context
                ) VALUES (
                    v_intent_id, 'portfolio_daily', 'portfolio', v_scope_id,
                    v_generation,
                    portfolio.pd_recompute_intent_dedupe_key(v_scope_id, v_generation),
                    p_reason_code,
                    p_reason_context || jsonb_build_object(
                        'scope_id', v_scope_id,
                        'source_transaction_id', v_source_transaction_id
                    )
                );
                scope_id := v_scope_id;
                generation := v_generation;
                intent_id := v_intent_id;
                RETURN NEXT;
            END LOOP;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_invalidate_instrument_dependencies(
            p_instrument_ids varchar[], p_include_fx_market boolean,
            p_reason_context jsonb
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE v_scope_ids varchar[];
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            SELECT array_agg(DISTINCT s.scope_id ORDER BY s.scope_id)
            INTO v_scope_ids
            FROM portfolio.portfolio_daily_dependency_subscription AS s
            WHERE (s.dependency_kind = 'instrument'
                   AND s.dependency_key = ANY(COALESCE(p_instrument_ids, ARRAY[]::varchar[])))
               OR (COALESCE(p_include_fx_market, false)
                   AND s.dependency_kind = 'fx_market' AND s.dependency_key = '*');
            PERFORM * FROM portfolio.pd_invalidate_scopes(
                v_scope_ids, 'portfolio_daily_dependency_changed', p_reason_context
            );
        END;
        $$;

        CREATE FUNCTION portfolio.pd_subscribe_dependency_and_invalidate(
            p_scope_id varchar, p_dependency_kind varchar, p_dependency_key varchar,
            p_reason_code varchar, p_reason_context jsonb
        ) RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE v_generation bigint;
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                scope_id, dependency_kind, dependency_key, source_reason_code
            ) VALUES (
                p_scope_id, p_dependency_kind, p_dependency_key, p_reason_code
            ) ON CONFLICT DO NOTHING;
            SELECT i.generation INTO STRICT v_generation
            FROM portfolio.pd_invalidate_scopes(
                ARRAY[p_scope_id], p_reason_code, p_reason_context
            ) AS i;
            RETURN v_generation;
        END;
        $$;

        CREATE TRIGGER trg_pd_dependency_subscription_guard
        BEFORE INSERT OR UPDATE OR DELETE
        ON portfolio.portfolio_daily_dependency_subscription
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_dependency_subscription();
        CREATE TRIGGER trg_pd_dependency_subscription_truncate
        BEFORE TRUNCATE ON portfolio.portfolio_daily_dependency_subscription
        FOR EACH STATEMENT
        EXECUTE FUNCTION portfolio.pd_reject_dependency_subscription_truncate();
        """
    )


def _seed_subscriptions() -> None:
    op.execute(
        """
        SELECT portfolio.pd_lock_dependency_invalidation_protocol();
        INSERT INTO calculation_registry.calculation_scope_generation (
            calculation_kind, scope_kind, scope_id, generation
        ) SELECT 'portfolio_daily', 'portfolio', portfolio_id, 0
          FROM portfolio.portfolio_record
        ON CONFLICT DO NOTHING;

        -- 0039 deliberately removes every legacy materialized snapshot.  A
        -- pre-existing portfolio therefore needs one durable generation-zero
        -- request even when no fact changes after deployment; otherwise it
        -- can remain calculation_not_ready indefinitely.  The registry guard
        -- verifies that requested_generation is the scope's current value in
        -- this same migration transaction.  ON CONFLICT preserves any request
        -- already created for that exact scope generation.
        INSERT INTO calculation_registry.calculation_recompute_intent (
            intent_id, calculation_kind, scope_kind, scope_id,
            requested_generation, dedupe_key, reason_code, reason_context
        )
        SELECT gen_random_uuid(), 'portfolio_daily', 'portfolio',
               generation.scope_id, generation.generation,
               portfolio.pd_recompute_intent_dedupe_key(
                   generation.scope_id, generation.generation
               ),
               'portfolio_daily_migration_seed',
               jsonb_build_object(
                   'migration_revision', '20260714_0040',
                   'source_relation', 'portfolio.portfolio_record'
               )
        FROM calculation_registry.calculation_scope_generation AS generation
        JOIN portfolio.portfolio_record AS portfolio
          ON portfolio.portfolio_id = generation.scope_id
        WHERE generation.calculation_kind = 'portfolio_daily'
          AND generation.scope_kind = 'portfolio'
        ON CONFLICT DO NOTHING;

        INSERT INTO portfolio.portfolio_daily_dependency_subscription (
            scope_id, dependency_kind, dependency_key, source_reason_code
        )
        SELECT portfolio_id, 'portfolio_config', portfolio_id, 'migration_seed'
        FROM portfolio.portfolio_record
        UNION ALL
        SELECT portfolio_id, 'fx_market', '*', 'migration_seed'
        FROM portfolio.portfolio_record
        UNION ALL
        SELECT portfolio_id, 'account', account_id, 'migration_seed'
        FROM portfolio.account_record
        UNION ALL
        SELECT portfolio_id, 'transaction', transaction_id, 'migration_seed'
        FROM portfolio.transaction_identity_record
        UNION ALL
        SELECT portfolio_id, 'taxonomy', taxonomy_id, 'migration_seed'
        FROM portfolio.taxonomy_record
        ON CONFLICT DO NOTHING;

        INSERT INTO portfolio.portfolio_daily_dependency_subscription (
            scope_id, dependency_kind, dependency_key, source_reason_code
        )
        -- Archived universe rows are retained historical tombstones, not live
        -- calculation dependencies.  Transactions still subscribe their own
        -- instruments independently, including instruments used historically.
        SELECT DISTINCT portfolio_id, 'instrument', instrument_id, 'migration_seed'
        FROM (
            SELECT portfolio_id, instrument_id
            FROM portfolio.portfolio_instrument_universe_record
            WHERE instrument_id IS NOT NULL AND status = 'active'
            UNION
            SELECT portfolio_id, instrument_id
            FROM portfolio.transaction_revision_record
            WHERE instrument_id IS NOT NULL
            UNION
            SELECT tr.portfolio_id, ta.target_entity_id
            FROM portfolio.taxonomy_assignment_record AS ta
            JOIN portfolio.taxonomy_record AS tr USING (taxonomy_id)
            WHERE ta.target_scope = 'instrument' AND ta.status = 'active'
        ) AS dependencies
        ON CONFLICT DO NOTHING;

        INSERT INTO portfolio.portfolio_daily_dependency_subscription (
            scope_id, dependency_kind, dependency_key, source_reason_code
        )
        SELECT DISTINCT portfolio_id, 'currency', currency, 'migration_seed'
        FROM (
            SELECT portfolio_id, base_currency AS currency
            FROM portfolio.portfolio_record
            UNION SELECT portfolio_id, currency FROM portfolio.account_record
            UNION SELECT portfolio_id, currency
                  FROM portfolio.transaction_revision_record WHERE currency IS NOT NULL
            UNION SELECT u.portfolio_id, i.currency
                  FROM portfolio.portfolio_instrument_universe_record AS u
                  JOIN instrument_registry.instrument AS i USING (instrument_id)
                  WHERE u.status = 'active'
        ) AS currencies
        WHERE currency IS NOT NULL
        ON CONFLICT DO NOTHING;
        """
    )


def _create_source_trigger_functions() -> None:
    op.execute(
        r"""
        CREATE FUNCTION portfolio.pd_trg_portfolio_record()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_scope_ids varchar[];
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP = 'INSERT' THEN
                INSERT INTO calculation_registry.calculation_scope_generation (
                    calculation_kind, scope_kind, scope_id, generation
                ) SELECT 'portfolio_daily', 'portfolio', portfolio_id, 0 FROM new_rows
                ON CONFLICT DO NOTHING;
                INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                    scope_id, dependency_kind, dependency_key, source_reason_code
                ) SELECT portfolio_id, kind, key, 'portfolio_record'
                  FROM new_rows CROSS JOIN LATERAL (VALUES
                    ('portfolio_config'::varchar, portfolio_id),
                    ('fx_market'::varchar, '*'::varchar),
                    ('currency'::varchar, base_currency)
                  ) AS d(kind, key) ON CONFLICT DO NOTHING;
                SELECT array_agg(DISTINCT portfolio_id ORDER BY portfolio_id)
                  INTO v_scope_ids FROM new_rows;
                -- Creation establishes generation zero and subscriptions.  It
                -- does not invalidate a prior result because no prior scope
                -- exists; the first run therefore captures generation zero.
                RETURN NULL;
            ELSE
                SELECT array_agg(DISTINCT new_row.portfolio_id ORDER BY new_row.portfolio_id)
                  INTO v_scope_ids
                  FROM new_rows AS new_row
                  JOIN old_rows AS old_row USING (portfolio_id)
                 WHERE ROW(
                           new_row.base_currency,
                           new_row.valuation_timezone,
                           new_row.valuation_cutoff_policy,
                           new_row.default_planning_taxonomy_id
                       ) IS DISTINCT FROM ROW(
                           old_row.base_currency,
                           old_row.valuation_timezone,
                           old_row.valuation_cutoff_policy,
                           old_row.default_planning_taxonomy_id
                       );
                -- Portfolio name, display ordering, and active/archive lifecycle
                -- do not change any financial result and must not create false
                -- generations or recomputation work.
                IF coalesce(cardinality(v_scope_ids), 0) = 0 THEN
                    RETURN NULL;
                END IF;
                INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                    scope_id, dependency_kind, dependency_key, source_reason_code
                ) SELECT portfolio_id, 'currency', base_currency, 'portfolio_record'
                  FROM new_rows
                 WHERE portfolio_id = ANY(v_scope_ids)
                ON CONFLICT DO NOTHING;
            END IF;
            PERFORM * FROM portfolio.pd_invalidate_scopes(
                v_scope_ids, 'portfolio_daily_dependency_changed',
                jsonb_build_object('source_relation','portfolio.portfolio_record','operation',TG_OP)
            );
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_account_record()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_scope_ids varchar[];
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP <> 'DELETE' THEN
                INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                    scope_id, dependency_kind, dependency_key, source_reason_code
                ) SELECT portfolio_id, 'account', account_id, 'account_record'
                  FROM new_rows ON CONFLICT DO NOTHING;
                INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                    scope_id, dependency_kind, dependency_key, source_reason_code
                ) SELECT portfolio_id, 'currency', currency, 'account_record'
                  FROM new_rows ON CONFLICT DO NOTHING;
            END IF;
            IF TG_OP = 'INSERT' THEN SELECT array_agg(DISTINCT portfolio_id) INTO v_scope_ids FROM new_rows;
            ELSIF TG_OP = 'DELETE' THEN SELECT array_agg(DISTINCT portfolio_id) INTO v_scope_ids FROM old_rows;
            ELSE SELECT array_agg(DISTINCT portfolio_id) INTO v_scope_ids FROM (
                SELECT portfolio_id FROM old_rows UNION SELECT portfolio_id FROM new_rows
            ) s; END IF;
            PERFORM * FROM portfolio.pd_invalidate_scopes(v_scope_ids,
                'portfolio_daily_dependency_changed',
                jsonb_build_object('source_relation','portfolio.account_record','operation',TG_OP));
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_transaction_revision()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_scope_ids varchar[];
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                scope_id, dependency_kind, dependency_key, source_reason_code
            ) SELECT DISTINCT portfolio_id, 'transaction', transaction_id, 'transaction_revision'
              FROM new_rows ON CONFLICT DO NOTHING;
            INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                scope_id, dependency_kind, dependency_key, source_reason_code
            ) SELECT DISTINCT portfolio_id, 'instrument', instrument_id, 'transaction_revision'
              FROM new_rows WHERE instrument_id IS NOT NULL ON CONFLICT DO NOTHING;
            INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                scope_id, dependency_kind, dependency_key, source_reason_code
            ) SELECT DISTINCT portfolio_id, 'currency', currency, 'transaction_revision'
              FROM new_rows WHERE currency IS NOT NULL ON CONFLICT DO NOTHING;
            SELECT array_agg(DISTINCT portfolio_id ORDER BY portfolio_id)
              INTO v_scope_ids FROM new_rows;
            PERFORM * FROM portfolio.pd_invalidate_scopes(v_scope_ids,
                'portfolio_daily_dependency_changed',
                jsonb_build_object('source_relation','portfolio.transaction_current','operation','REVISION_INSERT'));
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_instrument_universe()
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

        CREATE FUNCTION portfolio.pd_trg_taxonomy_record()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_scope_ids varchar[];
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP <> 'DELETE' THEN
                INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                    scope_id, dependency_kind, dependency_key, source_reason_code
                ) SELECT portfolio_id, 'taxonomy', taxonomy_id, 'taxonomy_record'
                  FROM new_rows ON CONFLICT DO NOTHING;
            END IF;
            IF TG_OP = 'INSERT' THEN SELECT array_agg(DISTINCT portfolio_id) INTO v_scope_ids FROM new_rows;
            ELSIF TG_OP = 'DELETE' THEN SELECT array_agg(DISTINCT portfolio_id) INTO v_scope_ids FROM old_rows;
            ELSE SELECT array_agg(DISTINCT portfolio_id) INTO v_scope_ids FROM (
                SELECT portfolio_id FROM old_rows UNION SELECT portfolio_id FROM new_rows
            ) s; END IF;
            PERFORM * FROM portfolio.pd_invalidate_scopes(v_scope_ids,
                'portfolio_daily_dependency_changed',
                jsonb_build_object('source_relation','portfolio.taxonomy_record','operation',TG_OP));
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_taxonomy_child()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_scope_ids varchar[];
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP = 'INSERT' THEN
                SELECT array_agg(DISTINCT s.scope_id) INTO v_scope_ids FROM new_rows r
                JOIN portfolio.portfolio_daily_dependency_subscription s
                  ON s.dependency_kind='taxonomy' AND s.dependency_key=r.taxonomy_id;
            ELSIF TG_OP = 'DELETE' THEN
                SELECT array_agg(DISTINCT s.scope_id) INTO v_scope_ids FROM old_rows r
                JOIN portfolio.portfolio_daily_dependency_subscription s
                  ON s.dependency_kind='taxonomy' AND s.dependency_key=r.taxonomy_id;
            ELSE
                SELECT array_agg(DISTINCT s.scope_id) INTO v_scope_ids FROM (
                    SELECT taxonomy_id FROM old_rows UNION SELECT taxonomy_id FROM new_rows
                ) r JOIN portfolio.portfolio_daily_dependency_subscription s
                  ON s.dependency_kind='taxonomy' AND s.dependency_key=r.taxonomy_id;
            END IF;
            PERFORM * FROM portfolio.pd_invalidate_scopes(v_scope_ids,
                'portfolio_daily_dependency_changed',
                jsonb_build_object('source_relation','portfolio.taxonomy_node_record','operation',TG_OP));
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_taxonomy_assignment()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_scope_ids varchar[];
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP <> 'DELETE' THEN
                INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                    scope_id, dependency_kind, dependency_key, source_reason_code
                ) SELECT DISTINCT s.scope_id, 'instrument', r.target_entity_id, 'taxonomy_assignment'
                  FROM new_rows r JOIN portfolio.portfolio_daily_dependency_subscription s
                    ON s.dependency_kind='taxonomy' AND s.dependency_key=r.taxonomy_id
                  WHERE r.target_scope='instrument' AND r.status='active'
                  ON CONFLICT DO NOTHING;
            END IF;
            IF TG_OP = 'INSERT' THEN
                SELECT array_agg(DISTINCT s.scope_id) INTO v_scope_ids FROM new_rows r
                JOIN portfolio.portfolio_daily_dependency_subscription s
                  ON s.dependency_kind='taxonomy' AND s.dependency_key=r.taxonomy_id;
            ELSIF TG_OP = 'DELETE' THEN
                SELECT array_agg(DISTINCT s.scope_id) INTO v_scope_ids FROM old_rows r
                JOIN portfolio.portfolio_daily_dependency_subscription s
                  ON s.dependency_kind='taxonomy' AND s.dependency_key=r.taxonomy_id;
            ELSE
                SELECT array_agg(DISTINCT s.scope_id) INTO v_scope_ids FROM (
                    SELECT taxonomy_id FROM old_rows UNION SELECT taxonomy_id FROM new_rows
                ) r JOIN portfolio.portfolio_daily_dependency_subscription s
                  ON s.dependency_kind='taxonomy' AND s.dependency_key=r.taxonomy_id;
            END IF;
            PERFORM * FROM portfolio.pd_invalidate_scopes(v_scope_ids,
                'portfolio_daily_dependency_changed',
                jsonb_build_object('source_relation','portfolio.taxonomy_assignment_record','operation',TG_OP));
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_instrument()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_ids varchar[]; v_fx boolean;
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP = 'INSERT' THEN SELECT array_agg(DISTINCT instrument_id), bool_or(instrument_type='fx') INTO v_ids,v_fx FROM new_rows;
            ELSIF TG_OP = 'DELETE' THEN SELECT array_agg(DISTINCT instrument_id), bool_or(instrument_type='fx') INTO v_ids,v_fx FROM old_rows;
            ELSE SELECT array_agg(DISTINCT instrument_id), bool_or(instrument_type='fx') INTO v_ids,v_fx FROM (
                SELECT instrument_id,instrument_type FROM old_rows UNION SELECT instrument_id,instrument_type FROM new_rows
            ) r; END IF;
            PERFORM portfolio.pd_invalidate_instrument_dependencies(v_ids,v_fx,
                jsonb_build_object('source_relation','instrument_registry.instrument','operation',TG_OP));
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_instrument_identifier()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_ids varchar[];
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP = 'INSERT' THEN
                SELECT array_agg(DISTINCT instrument_id ORDER BY instrument_id)
                  INTO v_ids FROM new_rows;
            ELSIF TG_OP = 'DELETE' THEN
                SELECT array_agg(DISTINCT instrument_id ORDER BY instrument_id)
                  INTO v_ids FROM old_rows;
            ELSE
                SELECT array_agg(DISTINCT instrument_id ORDER BY instrument_id)
                  INTO v_ids FROM (
                    SELECT instrument_id FROM old_rows
                    UNION SELECT instrument_id FROM new_rows
                ) r;
            END IF;
            PERFORM portfolio.pd_invalidate_instrument_dependencies(v_ids,false,
                jsonb_build_object('source_relation','instrument_registry.instrument_identifier','operation',TG_OP));
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_quote_series()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_ids varchar[]; v_fx boolean;
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP = 'INSERT' THEN
                SELECT array_agg(DISTINCT instrument_id ORDER BY instrument_id),
                       bool_or(metric_family='fx')
                  INTO v_ids,v_fx FROM new_rows;
            ELSIF TG_OP = 'DELETE' THEN
                SELECT array_agg(DISTINCT instrument_id ORDER BY instrument_id),
                       bool_or(metric_family='fx')
                  INTO v_ids,v_fx FROM old_rows;
            ELSE
                SELECT array_agg(DISTINCT instrument_id ORDER BY instrument_id),
                       bool_or(metric_family='fx')
                  INTO v_ids,v_fx FROM (
                    SELECT instrument_id,metric_family FROM old_rows
                    UNION SELECT instrument_id,metric_family FROM new_rows
                ) r;
            END IF;
            PERFORM portfolio.pd_invalidate_instrument_dependencies(v_ids,v_fx,
                jsonb_build_object('source_relation','instrument_registry.quote_series','operation',TG_OP));
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_quote_observation()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_ids varchar[]; v_fx boolean;
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP = 'INSERT' THEN
                SELECT array_agg(DISTINCT s.instrument_id ORDER BY s.instrument_id),
                       bool_or(s.metric_family='fx')
                  INTO v_ids,v_fx
                  FROM new_rows r
                  JOIN instrument_registry.quote_series s USING (quote_series_id);
            ELSIF TG_OP = 'DELETE' THEN
                SELECT array_agg(DISTINCT s.instrument_id ORDER BY s.instrument_id),
                       bool_or(s.metric_family='fx')
                  INTO v_ids,v_fx
                  FROM old_rows r
                  JOIN instrument_registry.quote_series s USING (quote_series_id);
            ELSE
                SELECT array_agg(DISTINCT s.instrument_id ORDER BY s.instrument_id),
                       bool_or(s.metric_family='fx')
                  INTO v_ids,v_fx
                  FROM (
                    SELECT quote_series_id FROM old_rows
                    UNION SELECT quote_series_id FROM new_rows
                  ) r
                  JOIN instrument_registry.quote_series s USING (quote_series_id);
            END IF;
            PERFORM portfolio.pd_invalidate_instrument_dependencies(v_ids,v_fx,
                jsonb_build_object('source_relation','instrument_registry.quote_observation','operation',TG_OP));
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_quote_revision()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_ids varchar[]; v_fx boolean;
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP = 'INSERT' THEN
                SELECT array_agg(DISTINCT s.instrument_id), bool_or(s.metric_family='fx') INTO v_ids,v_fx
                FROM new_rows r JOIN instrument_registry.quote_observation o USING (observation_id)
                JOIN instrument_registry.quote_series s USING (quote_series_id);
            ELSE
                SELECT array_agg(DISTINCT s.instrument_id), bool_or(s.metric_family='fx') INTO v_ids,v_fx
                FROM (SELECT observation_id FROM old_rows UNION SELECT observation_id FROM new_rows) r
                JOIN instrument_registry.quote_observation o USING (observation_id)
                JOIN instrument_registry.quote_series s USING (quote_series_id);
            END IF;
            PERFORM portfolio.pd_invalidate_instrument_dependencies(v_ids,v_fx,
                jsonb_build_object('source_relation','instrument_registry.quote_observation_revision','operation',TG_OP));
            RETURN NULL;
        END; $$;

        CREATE FUNCTION portfolio.pd_trg_corporate_action()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_ids varchar[];
        BEGIN
            PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
            IF TG_OP = 'INSERT' THEN SELECT array_agg(DISTINCT instrument_id) INTO v_ids FROM new_rows;
            ELSIF TG_OP = 'DELETE' THEN SELECT array_agg(DISTINCT instrument_id) INTO v_ids FROM old_rows;
            ELSE SELECT array_agg(DISTINCT instrument_id) INTO v_ids FROM (
                SELECT instrument_id FROM old_rows UNION SELECT instrument_id FROM new_rows
            ) r; END IF;
            PERFORM portfolio.pd_invalidate_instrument_dependencies(v_ids,false,
                jsonb_build_object('source_relation','instrument_registry.corporate_action_event','operation',TG_OP));
            RETURN NULL;
        END; $$;
        """
    )


def _install_source_triggers() -> None:
    for schema, table, event, function in SOURCE_TRIGGERS:
        name = f"trg_40_{function}_{event}"
        referencing = {
            "insert": "REFERENCING NEW TABLE AS new_rows",
            "update": "REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows",
            "delete": "REFERENCING OLD TABLE AS old_rows",
        }[event]
        op.execute(
            f"""
            CREATE TRIGGER {name}
            AFTER {event.upper()} ON {schema}.{table}
            {referencing}
            FOR EACH STATEMENT EXECUTE FUNCTION portfolio.{function}()
            """
        )


def _harden_and_validate() -> None:
    functions = (
        "pd_lock_dependency_invalidation_protocol()",
        "pd_recompute_intent_dedupe_key(character varying,bigint)",
        "pd_guard_dependency_subscription()",
        "pd_reject_dependency_subscription_truncate()",
        "pd_invalidate_scopes(character varying[],character varying,jsonb)",
        "pd_invalidate_instrument_dependencies(character varying[],boolean,jsonb)",
        "pd_subscribe_dependency_and_invalidate(character varying,character varying,character varying,character varying,jsonb)",
        "pd_trg_portfolio_record()",
        "pd_trg_account_record()",
        "pd_trg_transaction_revision()",
        "pd_trg_instrument_universe()",
        "pd_trg_taxonomy_record()",
        "pd_trg_taxonomy_child()",
        "pd_trg_taxonomy_assignment()",
        "pd_trg_instrument()",
        "pd_trg_instrument_identifier()",
        "pd_trg_quote_series()",
        "pd_trg_quote_observation()",
        "pd_trg_quote_revision()",
        "pd_trg_corporate_action()",
    )
    op.execute("REVOKE ALL ON portfolio.portfolio_daily_dependency_subscription FROM PUBLIC")
    for signature in functions:
        op.execute(f"REVOKE ALL ON FUNCTION portfolio.{signature} FROM PUBLIC")
    op.execute(
        """
        DO $$
        DECLARE v_missing bigint;
        BEGIN
            IF to_regclass('portfolio.portfolio_daily_dependency_subscription') IS NULL THEN
                RAISE EXCEPTION '0040 subscription table missing';
            END IF;
            SELECT count(*) INTO v_missing
            FROM portfolio.portfolio_record p
            WHERE NOT EXISTS (
                SELECT 1 FROM portfolio.portfolio_daily_dependency_subscription s
                WHERE s.scope_id=p.portfolio_id AND s.dependency_kind='portfolio_config'
                  AND s.dependency_key=p.portfolio_id
            ) OR NOT EXISTS (
                SELECT 1 FROM portfolio.portfolio_daily_dependency_subscription s
                WHERE s.scope_id=p.portfolio_id AND s.dependency_kind='fx_market'
                  AND s.dependency_key='*'
            );
            IF v_missing <> 0 THEN
                RAISE EXCEPTION '0040 scope seed incomplete: %', v_missing;
            END IF;
            SELECT count(*) INTO v_missing
            FROM portfolio.portfolio_record AS p
            JOIN calculation_registry.calculation_scope_generation AS generation
              ON generation.calculation_kind = 'portfolio_daily'
             AND generation.scope_kind = 'portfolio'
             AND generation.scope_id = p.portfolio_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM calculation_registry.calculation_recompute_intent AS intent
                WHERE intent.calculation_kind = generation.calculation_kind
                  AND intent.scope_kind = generation.scope_kind
                  AND intent.scope_id = generation.scope_id
                  AND intent.requested_generation = generation.generation
                  AND intent.status IN ('pending', 'materialized')
            );
            IF v_missing <> 0 THEN
                RAISE EXCEPTION '0040 initial recompute intent seed incomplete: %',
                    v_missing;
            END IF;
            IF has_table_privilege('public',
                'portfolio.portfolio_daily_dependency_subscription','INSERT,UPDATE,DELETE,TRUNCATE') THEN
                RAISE EXCEPTION '0040 subscription public DML privilege leak';
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    _require_postgresql_and_shared_owner()
    _create_subscription_storage()
    _create_protocol_functions()
    _seed_subscriptions()
    _create_source_trigger_functions()
    _install_source_triggers()
    _harden_and_validate()


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("20260714_0040 requires PostgreSQL")
    for schema, table, event, function in reversed(SOURCE_TRIGGERS):
        op.execute(
            f"DROP TRIGGER trg_40_{function}_{event} ON {schema}.{table}"
        )
    for function in (
        "pd_trg_corporate_action()",
        "pd_trg_quote_revision()",
        "pd_trg_quote_observation()",
        "pd_trg_quote_series()",
        "pd_trg_instrument_identifier()",
        "pd_trg_instrument()",
        "pd_trg_taxonomy_assignment()",
        "pd_trg_taxonomy_child()",
        "pd_trg_taxonomy_record()",
        "pd_trg_instrument_universe()",
        "pd_trg_transaction_revision()",
        "pd_trg_account_record()",
        "pd_trg_portfolio_record()",
        "pd_subscribe_dependency_and_invalidate(character varying,character varying,character varying,character varying,jsonb)",
        "pd_invalidate_instrument_dependencies(character varying[],boolean,jsonb)",
        "pd_invalidate_scopes(character varying[],character varying,jsonb)",
    ):
        op.execute(f"DROP FUNCTION portfolio.{function}")
    op.execute("DROP TRIGGER trg_pd_dependency_subscription_guard ON portfolio.portfolio_daily_dependency_subscription")
    op.execute("DROP TRIGGER trg_pd_dependency_subscription_truncate ON portfolio.portfolio_daily_dependency_subscription")
    for function in (
        "pd_reject_dependency_subscription_truncate()",
        "pd_guard_dependency_subscription()",
        "pd_recompute_intent_dedupe_key(character varying,bigint)",
        "pd_lock_dependency_invalidation_protocol()",
    ):
        op.execute(f"DROP FUNCTION portfolio.{function}")
    op.execute("DROP TABLE portfolio.portfolio_daily_dependency_subscription")
