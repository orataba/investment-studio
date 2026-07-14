"""Enforce canonical transaction hashes and atomic internal transfers in PostgreSQL.

The application service remains the portable write contract for SQLite.  On
PostgreSQL, where the production ledger lives, this revision also makes the
database authoritative for the two cross-row audit invariants that ordinary
row checks cannot express:

* every persisted payload hash is derived from the stored v1 facts; and
* an internal transfer exists or is deleted as one immutable, reciprocal pair.

Revision ID: 20260713_0037
Revises: 20260713_0036
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0037"
down_revision = "20260713_0036"
branch_labels = None
depends_on = None


def _postgresql_schema_parts() -> tuple[str, str]:
    schema = op.get_context().opts.get("version_table_schema")
    schema_prefix = f'"{schema}".' if schema else ""
    row_type = f'{schema_prefix}transaction_revision_record'
    return schema_prefix, row_type


def _install_postgresql_hash_contract(connection: sa.Connection) -> None:
    schema_prefix, row_type = _postgresql_schema_parts()
    number_function = f"{schema_prefix}transaction_json_number_v1"
    json_function = f"{schema_prefix}canonical_transaction_json_v1"
    payload_function = f"{schema_prefix}canonical_transaction_revision_payload_v1"
    hash_function = f"{schema_prefix}transaction_revision_payload_hash_v1"
    trigger_function = f"{schema_prefix}validate_transaction_revision_payload_insert_v1"

    # The JSON column preserves numeric tokens emitted by Python's encoder.
    # Keep that token verbatim: converting it through PostgreSQL float8 can
    # choose a different (also round-trippable) shortest representation and
    # would therefore change the Python canonical digest.  Validation below
    # still excludes non-finite float spellings that the runtime rejects.
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {number_function}(raw_number text)
            RETURNS text
            LANGUAGE plpgsql
            IMMUTABLE
            STRICT
            AS $function$
            DECLARE
                normalized text := btrim(raw_number);
                rendered text;
            BEGIN
                IF normalized ~ '[.eE]' THEN
                    rendered := lower((normalized::double precision)::text);
                    IF rendered IN ('infinity', '-infinity', 'nan') THEN
                        RAISE EXCEPTION 'transaction JSON contains a non-finite number'
                            USING ERRCODE = '22023';
                    END IF;
                END IF;
                RETURN normalized;
            END;
            $function$
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {json_function}(input_value json)
            RETURNS text
            LANGUAGE plpgsql
            IMMUTABLE
            STRICT
            AS $function$
            DECLARE
                value_type text := json_typeof(input_value);
                rendered text;
            BEGIN
                CASE value_type
                    WHEN 'object' THEN
                        SELECT '{{' || coalesce(string_agg(
                            to_json(item.key)::text || ':'
                                || {json_function}(item.value),
                            ',' ORDER BY item.key COLLATE "C"
                        ), '') || '}}'
                        INTO rendered
                        FROM json_each(input_value) AS item;
                        RETURN rendered;
                    WHEN 'array' THEN
                        SELECT '[' || coalesce(string_agg(
                            {json_function}(item.value),
                            ',' ORDER BY item.ordinality
                        ), '') || ']'
                        INTO rendered
                        FROM json_array_elements(input_value)
                            WITH ORDINALITY AS item(value, ordinality);
                        RETURN rendered;
                    WHEN 'string' THEN
                        RETURN to_json(input_value #>> '{{}}')::text;
                    WHEN 'number' THEN
                        RETURN {number_function}(input_value::text);
                    WHEN 'boolean' THEN
                        RETURN lower(btrim(input_value::text));
                    WHEN 'null' THEN
                        RETURN 'null';
                    ELSE
                        RAISE EXCEPTION 'unsupported transaction JSON type: %', value_type
                            USING ERRCODE = '22023';
                END CASE;
            END;
            $function$
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {payload_function}(row_value {row_type})
            RETURNS text
            LANGUAGE plpgsql
            IMMUTABLE
            STRICT
            AS $function$
            DECLARE
                facts json;
                envelope json;
            BEGIN
                IF row_value.is_tombstone THEN
                    facts := 'null'::json;
                ELSE
                    facts := json_build_object(
                        'transaction_type', row_value.transaction_type,
                        'trade_date', to_char(row_value.trade_date, 'YYYY-MM-DD'),
                        'trade_time', to_char(row_value.trade_time, 'HH24:MI:SS.US'),
                        'trade_at', to_char(
                            row_value.trade_at AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                        ),
                        'trade_timezone', row_value.trade_timezone,
                        'trade_time_is_estimated', row_value.trade_time_is_estimated,
                        'settlement_date', to_char(row_value.settlement_date, 'YYYY-MM-DD'),
                        'entitlement_date', CASE
                            WHEN row_value.entitlement_date IS NULL THEN NULL
                            ELSE to_char(row_value.entitlement_date, 'YYYY-MM-DD')
                        END,
                        'acquisition_date', CASE
                            WHEN row_value.acquisition_date IS NULL THEN NULL
                            ELSE to_char(row_value.acquisition_date, 'YYYY-MM-DD')
                        END,
                        'account_id', row_value.account_id,
                        'settlement_cash_account_id', row_value.settlement_cash_account_id,
                        'instrument_id', row_value.instrument_id,
                        'instrument_snapshot_json', row_value.instrument_snapshot_json,
                        -- Value and representation precision are separate
                        -- facts. trim_scale yields the canonical numeric value;
                        -- *_input_scale preserves the source representation.
                        'quantity', trim_scale(row_value.quantity)::text,
                        'price', trim_scale(row_value.price)::text,
                        'gross_amount', trim_scale(row_value.gross_amount)::text,
                        'counter_amount', trim_scale(row_value.counter_amount)::text,
                        'quoted_fx_rate', trim_scale(row_value.quoted_fx_rate)::text,
                        'fees', trim_scale(row_value.fees)::text,
                        'taxes', trim_scale(row_value.taxes)::text,
                        'consideration_basis', row_value.consideration_basis,
                        'numeric_scale_state', row_value.numeric_scale_state,
                        'quantity_input_scale', row_value.quantity_input_scale,
                        'price_input_scale', row_value.price_input_scale,
                        'gross_amount_input_scale', row_value.gross_amount_input_scale,
                        'counter_amount_input_scale', row_value.counter_amount_input_scale,
                        'quoted_fx_rate_input_scale', row_value.quoted_fx_rate_input_scale,
                        'fees_input_scale', row_value.fees_input_scale,
                        'taxes_input_scale', row_value.taxes_input_scale,
                        'currency', row_value.currency,
                        'transfer_scope', row_value.transfer_scope,
                        'transfer_object_type', row_value.transfer_object_type,
                        'transfer_group_id', row_value.transfer_group_id,
                        'counterparty_account_id', row_value.counterparty_account_id,
                        'note', row_value.note
                    );
                END IF;

                envelope := json_build_object(
                    'payload_schema_version', row_value.payload_schema_version,
                    'is_tombstone', row_value.is_tombstone,
                    'facts', facts
                );
                RETURN {json_function}(envelope);
            END;
            $function$
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {hash_function}(row_value {row_type})
            RETURNS text
            LANGUAGE sql
            IMMUTABLE
            STRICT
            AS $function$
                SELECT 'sha256:' || encode(
                    sha256(convert_to({payload_function}(row_value), 'UTF8')),
                    'hex'
                )
            $function$
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {trigger_function}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            DECLARE
                expected_hash text;
                predecessor_type text;
                predecessor_transfer_group_id text;
                touched_transfer_group_id text;
                touches_internal_transfer boolean := false;
            BEGIN
                expected_hash := {hash_function}(NEW);
                IF NEW.payload_hash IS DISTINCT FROM expected_hash THEN
                    RAISE EXCEPTION
                        'transaction revision payload_hash does not match canonical v1 facts'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'transaction_revision_payload_hash_v1';
                END IF;

                IF NEW.revision_number > 1 THEN
                    SELECT
                        predecessor.transaction_type,
                        predecessor.transfer_group_id
                    INTO predecessor_type, predecessor_transfer_group_id
                    FROM {schema_prefix}transaction_revision_record AS predecessor
                    WHERE predecessor.portfolio_id = NEW.portfolio_id
                      AND predecessor.transaction_id = NEW.transaction_id
                      AND predecessor.revision_number = NEW.supersedes_revision_number
                      AND predecessor.revision_id = NEW.supersedes_revision_id;
                END IF;

                touches_internal_transfer :=
                    NEW.transaction_type IN ('transfer_in', 'transfer_out')
                    OR predecessor_type IN ('transfer_in', 'transfer_out');
                IF touches_internal_transfer THEN
                    touched_transfer_group_id := coalesce(
                        NEW.transfer_group_id,
                        predecessor_transfer_group_id
                    );
                    -- Identity inserts hold a foreign-key key-share lock on the
                    -- portfolio row, so upgrading that row to FOR UPDATE here
                    -- could deadlock two direct writers.  A transaction-scoped
                    -- advisory lock serializes precisely the portfolio/group
                    -- history without participating in that row-lock graph.
                    PERFORM pg_advisory_xact_lock(hashtextextended(
                        NEW.portfolio_id || chr(31) || touched_transfer_group_id,
                        370037
                    ));
                END IF;

                IF NEW.revision_kind = 'amend' THEN
                    IF NEW.transaction_type IN ('transfer_in', 'transfer_out')
                       OR predecessor_type IN ('transfer_in', 'transfer_out') THEN
                        RAISE EXCEPTION
                            'internal transfer legs cannot be amended; '
                            'delete and recreate the atomic pair'
                            USING ERRCODE = '23514',
                                  CONSTRAINT = 'transaction_revision_transfer_amend_forbidden';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $function$
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_transaction_revision_record_payload_v1
            BEFORE INSERT ON {schema_prefix}transaction_revision_record
            FOR EACH ROW EXECUTE FUNCTION {trigger_function}()
            """
        )
    )


def _install_postgresql_transfer_contract(connection: sa.Connection) -> None:
    schema_prefix, row_type = _postgresql_schema_parts()
    assert_function = f"{schema_prefix}assert_internal_transfer_group_v1"
    deferred_function = f"{schema_prefix}validate_internal_transfer_revision_deferred_v1"

    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {assert_function}(
                checked_portfolio_id text,
                checked_transfer_group_id text
            )
            RETURNS void
            LANGUAGE plpgsql
            AS $function$
            DECLARE
                transfer_row_count integer;
                outbound_count integer;
                inbound_count integer;
                latest_count integer;
                latest_tombstone_count integer;
                latest_group_count integer;
                outbound {row_type}%ROWTYPE;
                inbound {row_type}%ROWTYPE;
            BEGIN
                IF checked_transfer_group_id IS NULL
                   OR length(btrim(checked_transfer_group_id)) = 0 THEN
                    RAISE EXCEPTION 'internal transfer group id must not be blank'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'transaction_revision_transfer_group_nonblank';
                END IF;

                SELECT
                    count(*),
                    count(*) FILTER (WHERE transaction_type = 'transfer_out'),
                    count(*) FILTER (WHERE transaction_type = 'transfer_in')
                INTO transfer_row_count, outbound_count, inbound_count
                FROM {schema_prefix}transaction_revision_record
                WHERE portfolio_id = checked_portfolio_id
                  AND transfer_group_id = checked_transfer_group_id;

                -- Transfer facts are immutable, so exactly two fact-bearing
                -- history rows simultaneously proves pair cardinality and
                -- prevents reuse of an old group after its pair is deleted.
                IF transfer_row_count <> 2 OR outbound_count <> 1 OR inbound_count <> 1 THEN
                    RAISE EXCEPTION
                        'internal transfer group % must have exactly one historical '
                        'in leg and one out leg',
                        checked_transfer_group_id
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'transaction_revision_transfer_pair_cardinality';
                END IF;

                SELECT * INTO STRICT outbound
                FROM {schema_prefix}transaction_revision_record
                WHERE portfolio_id = checked_portfolio_id
                  AND transfer_group_id = checked_transfer_group_id
                  AND transaction_type = 'transfer_out';
                SELECT * INTO STRICT inbound
                FROM {schema_prefix}transaction_revision_record
                WHERE portfolio_id = checked_portfolio_id
                  AND transfer_group_id = checked_transfer_group_id
                  AND transaction_type = 'transfer_in';

                IF outbound.revision_number <> 1 OR inbound.revision_number <> 1
                   OR outbound.revision_kind NOT IN ('baseline', 'create')
                   OR inbound.revision_kind NOT IN ('baseline', 'create')
                   OR outbound.revision_group_id IS DISTINCT FROM inbound.revision_group_id THEN
                    RAISE EXCEPTION
                        'internal transfer group % must be created by one initial revision group',
                        checked_transfer_group_id
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'transaction_revision_transfer_create_atomic';
                END IF;

                IF outbound.counterparty_account_id IS DISTINCT FROM inbound.account_id
                   OR inbound.counterparty_account_id IS DISTINCT FROM outbound.account_id THEN
                    RAISE EXCEPTION
                        'internal transfer group % account links are not reciprocal',
                        checked_transfer_group_id
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'transaction_revision_transfer_reciprocal';
                END IF;

                IF outbound.trade_date IS DISTINCT FROM inbound.trade_date
                   OR outbound.trade_time IS DISTINCT FROM inbound.trade_time
                   OR outbound.trade_at IS DISTINCT FROM inbound.trade_at
                   OR outbound.trade_timezone IS DISTINCT FROM inbound.trade_timezone
                   OR outbound.trade_time_is_estimated
                        IS DISTINCT FROM inbound.trade_time_is_estimated
                   OR outbound.settlement_date IS DISTINCT FROM inbound.settlement_date
                   OR outbound.entitlement_date IS DISTINCT FROM inbound.entitlement_date
                   OR outbound.acquisition_date IS DISTINCT FROM inbound.acquisition_date
                   OR outbound.settlement_cash_account_id
                        IS DISTINCT FROM inbound.settlement_cash_account_id
                   OR outbound.instrument_id IS DISTINCT FROM inbound.instrument_id
                   OR outbound.instrument_snapshot_json::jsonb
                        IS DISTINCT FROM inbound.instrument_snapshot_json::jsonb
                   OR outbound.quantity IS DISTINCT FROM inbound.quantity
                   OR outbound.price IS DISTINCT FROM inbound.price
                   OR outbound.gross_amount IS DISTINCT FROM inbound.gross_amount
                   OR outbound.counter_amount IS DISTINCT FROM inbound.counter_amount
                   OR outbound.quoted_fx_rate IS DISTINCT FROM inbound.quoted_fx_rate
                   OR outbound.fees IS DISTINCT FROM inbound.fees
                   OR outbound.taxes IS DISTINCT FROM inbound.taxes
                   OR outbound.consideration_basis IS DISTINCT FROM inbound.consideration_basis
                   OR outbound.numeric_scale_state IS DISTINCT FROM inbound.numeric_scale_state
                   OR outbound.quantity_input_scale IS DISTINCT FROM inbound.quantity_input_scale
                   OR outbound.price_input_scale IS DISTINCT FROM inbound.price_input_scale
                   OR outbound.gross_amount_input_scale IS DISTINCT FROM inbound.gross_amount_input_scale
                   OR outbound.counter_amount_input_scale IS DISTINCT FROM inbound.counter_amount_input_scale
                   OR outbound.quoted_fx_rate_input_scale IS DISTINCT FROM inbound.quoted_fx_rate_input_scale
                   OR outbound.fees_input_scale IS DISTINCT FROM inbound.fees_input_scale
                   OR outbound.taxes_input_scale IS DISTINCT FROM inbound.taxes_input_scale
                   OR outbound.currency IS DISTINCT FROM inbound.currency
                   OR outbound.transfer_scope IS DISTINCT FROM inbound.transfer_scope
                   OR outbound.transfer_object_type IS DISTINCT FROM inbound.transfer_object_type
                   OR outbound.note IS DISTINCT FROM inbound.note THEN
                    RAISE EXCEPTION
                        'internal transfer group % has non-mirrored facts',
                        checked_transfer_group_id
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'transaction_revision_transfer_mirrored_facts';
                END IF;

                WITH latest AS (
                    SELECT DISTINCT ON (revision.transaction_id)
                        revision.transaction_id,
                        revision.revision_id,
                        revision.revision_number,
                        revision.revision_group_id,
                        revision.revision_kind,
                        revision.is_tombstone
                    FROM {schema_prefix}transaction_revision_record AS revision
                    WHERE revision.portfolio_id = checked_portfolio_id
                      AND revision.transaction_id IN (
                          outbound.transaction_id,
                          inbound.transaction_id
                      )
                    ORDER BY revision.transaction_id, revision.revision_number DESC
                )
                SELECT
                    count(*),
                    count(*) FILTER (WHERE is_tombstone),
                    count(DISTINCT revision_group_id)
                INTO latest_count, latest_tombstone_count, latest_group_count
                FROM latest;

                IF latest_count <> 2 OR latest_tombstone_count NOT IN (0, 2) THEN
                    RAISE EXCEPTION
                        'internal transfer group % must be live or deleted as a complete pair',
                        checked_transfer_group_id
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'transaction_revision_transfer_lifecycle_atomic';
                END IF;

                IF latest_tombstone_count = 0 THEN
                    IF EXISTS (
                        SELECT 1
                        FROM {schema_prefix}transaction_revision_record AS revision
                        WHERE revision.portfolio_id = checked_portfolio_id
                          AND revision.transaction_id IN (
                              outbound.transaction_id,
                              inbound.transaction_id
                          )
                          AND revision.revision_number > 1
                    ) THEN
                        RAISE EXCEPTION
                            'internal transfer group % contains a forbidden amendment',
                            checked_transfer_group_id
                            USING ERRCODE = '23514',
                                  CONSTRAINT = 'transaction_revision_transfer_amend_forbidden';
                    END IF;
                    RETURN;
                END IF;

                IF latest_group_count <> 1 OR EXISTS (
                    WITH latest AS (
                        SELECT DISTINCT ON (revision.transaction_id)
                            revision.transaction_id,
                            revision.revision_number,
                            revision.revision_kind,
                            revision.is_tombstone
                        FROM {schema_prefix}transaction_revision_record AS revision
                        WHERE revision.portfolio_id = checked_portfolio_id
                          AND revision.transaction_id IN (
                              outbound.transaction_id,
                              inbound.transaction_id
                          )
                        ORDER BY revision.transaction_id, revision.revision_number DESC
                    )
                    SELECT 1 FROM latest
                    WHERE NOT is_tombstone
                       OR revision_kind <> 'delete'
                       OR revision_number <> 2
                ) THEN
                    RAISE EXCEPTION
                        'internal transfer group % must be deleted by one atomic revision group',
                        checked_transfer_group_id
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'transaction_revision_transfer_delete_atomic';
                END IF;
            END;
            $function$
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {deferred_function}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            DECLARE
                checked_transfer_group_id text;
            BEGIN
                checked_transfer_group_id := NEW.transfer_group_id;
                IF checked_transfer_group_id IS NULL AND NEW.revision_number > 1 THEN
                    SELECT predecessor.transfer_group_id
                    INTO checked_transfer_group_id
                    FROM {schema_prefix}transaction_revision_record AS predecessor
                    WHERE predecessor.portfolio_id = NEW.portfolio_id
                      AND predecessor.transaction_id = NEW.transaction_id
                      AND predecessor.revision_number = NEW.supersedes_revision_number
                      AND predecessor.revision_id = NEW.supersedes_revision_id;
                END IF;
                IF checked_transfer_group_id IS NOT NULL THEN
                    PERFORM {assert_function}(
                        NEW.portfolio_id,
                        checked_transfer_group_id
                    );
                END IF;
                RETURN NEW;
            END;
            $function$
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE CONSTRAINT TRIGGER trg_transaction_revision_record_transfer_v1
            AFTER INSERT ON {schema_prefix}transaction_revision_record
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION {deferred_function}()
            """
        )
    )


def _verify_existing_postgresql_ledger(connection: sa.Connection) -> None:
    schema_prefix, _row_type = _postgresql_schema_parts()
    hash_function = f"{schema_prefix}transaction_revision_payload_hash_v1"
    assert_function = f"{schema_prefix}assert_internal_transfer_group_v1"

    mismatches = connection.execute(
        sa.text(
            f"""
            SELECT revision.revision_id
            FROM {schema_prefix}transaction_revision_record AS revision
            WHERE revision.payload_hash IS DISTINCT FROM {hash_function}(revision)
            ORDER BY revision.revision_id
            LIMIT 10
            """
        )
    ).scalars().all()
    if mismatches:
        raise RuntimeError(
            "0037 found transaction revisions whose stored payload hash does not "
            f"match canonical v1 facts: {', '.join(str(item) for item in mismatches)}"
        )

    connection.execute(
        sa.text(
            f"""
            SELECT {assert_function}(portfolio_id, transfer_group_id)
            FROM (
                SELECT DISTINCT portfolio_id, transfer_group_id
                FROM {schema_prefix}transaction_revision_record
                WHERE transfer_group_id IS NOT NULL
            ) AS existing_transfer_group
            """
        )
    ).all()


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        # SQLite remains a deterministic local/test backend.  Its transaction
        # service already validates pair batches; PostgreSQL is the production
        # authority for commit-deferred cross-row constraints.
        return

    _install_postgresql_hash_contract(connection)
    _install_postgresql_transfer_contract(connection)
    _verify_existing_postgresql_ledger(connection)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    schema_prefix, row_type = _postgresql_schema_parts()
    connection.execute(
        sa.text(
            f"DROP TRIGGER trg_transaction_revision_record_transfer_v1 "
            f"ON {schema_prefix}transaction_revision_record"
        )
    )
    connection.execute(
        sa.text(
            f"DROP TRIGGER trg_transaction_revision_record_payload_v1 "
            f"ON {schema_prefix}transaction_revision_record"
        )
    )
    connection.execute(
        sa.text(f"DROP FUNCTION {schema_prefix}validate_internal_transfer_revision_deferred_v1()")
    )
    connection.execute(
        sa.text(f"DROP FUNCTION {schema_prefix}assert_internal_transfer_group_v1(text, text)")
    )
    connection.execute(
        sa.text(f"DROP FUNCTION {schema_prefix}validate_transaction_revision_payload_insert_v1()")
    )
    connection.execute(
        sa.text(f"DROP FUNCTION {schema_prefix}transaction_revision_payload_hash_v1({row_type})")
    )
    connection.execute(
        sa.text(
            f"DROP FUNCTION {schema_prefix}canonical_transaction_revision_payload_v1({row_type})"
        )
    )
    connection.execute(
        sa.text(f"DROP FUNCTION {schema_prefix}canonical_transaction_json_v1(json)")
    )
    connection.execute(
        sa.text(f"DROP FUNCTION {schema_prefix}transaction_json_number_v1(text)")
    )
