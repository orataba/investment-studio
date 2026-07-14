"""Install exact, sealed Portfolio Daily input and output storage.

Revision ID: 20260714_0039
Revises: 20260713_0038

This is intentionally a breaking PostgreSQL-only migration.  The legacy float
materialisations are not authoritative and are discarded rather than copied.
Method50 and its exact rounding evidence use physical unbounded ``numeric``
columns plus fail-closed CHECK constraints.  A ``numeric(p,s)`` typmod is not
safe here because PostgreSQL rounds to its scale before evaluating CHECKs.
"""

from __future__ import annotations

from alembic import op


revision = "20260714_0039"
down_revision = "20260713_0038"
branch_labels = None
depends_on = None


DEPENDENCY_TABLES = (
    "portfolio_daily_config_input",
    "portfolio_daily_account_input",
    "portfolio_daily_transaction_input",
    "portfolio_daily_instrument_input",
    "portfolio_daily_corp_action_window",
    "portfolio_daily_corp_action_input",
    "portfolio_daily_quote_window",
    "portfolio_daily_quote_candidate",
    "portfolio_daily_fx_path",
    "portfolio_daily_fx_leg",
    "portfolio_daily_prior_publication",
)

OUTPUT_TABLES = (
    "portfolio_daily_run_output",
    "portfolio_daily_snapshot_output",
    "portfolio_daily_holding_output",
    "portfolio_daily_balance_output",
    "portfolio_daily_lot_output",
    "portfolio_daily_lot_disposition_output",
    "portfolio_daily_contribution_output",
)


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("20260714_0039 requires PostgreSQL")


def _logical_numeric_domain(
    column_name: str,
    *,
    precision: int,
    scale: int,
) -> str:
    """CHECK equivalent to NUMERIC(p,s), without typmod pre-rounding."""

    integer_digits = precision - scale
    return (
        f"({column_name} IS NULL OR ("
        f"{column_name}::text NOT IN ('NaN', 'Infinity', '-Infinity') "
        f"AND abs({column_name}) < 1e{integer_digits} "
        f"AND {column_name} = trunc({column_name}, {scale})))"
    )


def _all_logical_numeric_domains(
    column_names: tuple[str, ...],
    *,
    precision: int,
    scale: int,
) -> str:
    return " AND ".join(
        _logical_numeric_domain(
            column_name,
            precision=precision,
            scale=scale,
        )
        for column_name in column_names
    )


def _add_logical_numeric_domain(
    table_name: str,
    constraint_name: str,
    column_names: tuple[str, ...],
    *,
    precision: int,
    scale: int,
) -> None:
    condition = _all_logical_numeric_domains(
        column_names,
        precision=precision,
        scale=scale,
    )
    op.execute(
        f"ALTER TABLE portfolio.{table_name} "
        f"ADD CONSTRAINT {constraint_name} CHECK ({condition})"
    )


def _drop_legacy_materialisations() -> None:
    op.execute("DROP TABLE portfolio.portfolio_daily_contribution_slice")
    op.execute("DROP TABLE portfolio.portfolio_daily_holding_snapshot")
    op.execute("DROP TABLE portfolio.portfolio_daily_snapshot")
    op.execute("DROP TABLE portfolio.portfolio_calculation_state")
    for column in (
        "as_of_date",
        "nav",
        "day_change_value",
        "day_change_pct",
        "securities_count",
    ):
        op.execute(f"ALTER TABLE portfolio.portfolio_record DROP COLUMN {column}")
    op.execute(
        """
        ALTER TABLE portfolio.portfolio_record
            ADD COLUMN lifecycle_status varchar(16) NOT NULL DEFAULT 'active',
            ADD CONSTRAINT ck_portfolio_record_lifecycle_status
                CHECK (lifecycle_status IN ('active', 'archived'));
        WITH normalized_active_order AS (
            SELECT portfolio_id,
                   row_number() OVER (
                       ORDER BY sort_order, portfolio_id
                   ) - 1 AS normalized_sort_order
            FROM portfolio.portfolio_record
            WHERE lifecycle_status = 'active'
        )
        UPDATE portfolio.portfolio_record AS portfolio
        SET sort_order = normalized.normalized_sort_order
        FROM normalized_active_order AS normalized
        WHERE portfolio.portfolio_id = normalized.portfolio_id;
        CREATE UNIQUE INDEX uq_portfolio_record_active_sort_order
            ON portfolio.portfolio_record(sort_order)
            WHERE lifecycle_status = 'active';
        """
    )


def _create_dependency_tables() -> None:
    op.execute(
        """
        CREATE TABLE portfolio.portfolio_daily_config_input (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            effective_as_of date NOT NULL,
            range_start date NOT NULL,
            knowledge_cutoff_at timestamptz NOT NULL,
            base_currency varchar(3) NOT NULL,
            valuation_timezone varchar(64) NOT NULL,
            valuation_cutoff_local_time time NOT NULL,
            valuation_cutoff_policy varchar(64) NOT NULL,
            valuation_calendar_id varchar(128) NOT NULL,
            valuation_calendar_version varchar(64) NOT NULL,
            quote_policy_version varchar(64) NOT NULL,
            quote_selection_policy_revision varchar(128) NOT NULL,
            freshness_policy_version varchar(64) NOT NULL,
            freshness_mode varchar(32) NOT NULL,
            freshness_max_age_days integer NOT NULL,
            quote_resolver_strategy_version varchar(64) NOT NULL,
            fx_policy_version varchar(64) NOT NULL,
            corporate_action_policy_version varchar(64) NOT NULL,
            taxonomy_id varchar(255),
            taxonomy_version varchar(64),
            benchmark_id varchar(255),
            operating_profile varchar(64) NOT NULL,
            config_schema_version varchar(64) NOT NULL,
            config_hash char(64) NOT NULL,
            canonical_config jsonb NOT NULL,
            CONSTRAINT pk_pd_config_input PRIMARY KEY (manifest_id),
            CONSTRAINT uq_pd_config_manifest_run UNIQUE (manifest_id, run_id),
            CONSTRAINT fk_pd_config_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_config_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_config_currency CHECK (base_currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_pd_config_hash CHECK (config_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_pd_config_snapshot CHECK (jsonb_typeof(canonical_config) = 'object'),
            CONSTRAINT ck_pd_config_freshness CHECK (freshness_max_age_days >= 0),
            CONSTRAINT ck_pd_config_range CHECK (range_start <= effective_as_of),
            CONSTRAINT ck_pd_config_quote_selection_revision CHECK (
                quote_selection_policy_revision ~ '^sha256:[0-9a-f]{64}$'
            )
        );
        COMMENT ON COLUMN portfolio.portfolio_daily_config_input.knowledge_cutoff_at IS
            'Knowledge-time cutoff for facts eligible for this manifest; not the economic valuation boundary.';

        CREATE TABLE portfolio.portfolio_daily_account_input (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            account_id varchar(255) NOT NULL,
            account_name varchar(255) NOT NULL,
            account_type varchar(64) NOT NULL,
            currency varchar(3) NOT NULL,
            institution varchar(255),
            default_settlement_cash_account_id varchar(255),
            cost_basis_method varchar(32),
            opened_at date,
            closed_at date,
            account_status varchar(32) NOT NULL,
            account_schema_version varchar(64) NOT NULL,
            account_hash char(64) NOT NULL,
            canonical_account jsonb NOT NULL,
            CONSTRAINT pk_pd_account_input PRIMARY KEY (manifest_id, account_id),
            CONSTRAINT fk_pd_account_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_account_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_account_current_identity FOREIGN KEY (portfolio_id, account_id)
                REFERENCES portfolio.account_record(portfolio_id, account_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_account_currency CHECK (currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_pd_account_hash CHECK (account_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_pd_account_snapshot CHECK (jsonb_typeof(canonical_account) = 'object'),
            CONSTRAINT ck_pd_account_dates CHECK (closed_at IS NULL OR opened_at IS NULL OR closed_at >= opened_at),
            CONSTRAINT ck_pd_account_cost_method CHECK (
                cost_basis_method IS NOT NULL OR account_type IN
                    ('cash', 'settlement_cash', 'deposit', 'deposit_account', 'bank', 'custody_cash')
            )
        );

        CREATE TABLE portfolio.portfolio_daily_transaction_input (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            transaction_id varchar(255) NOT NULL,
            revision_id varchar(255) NOT NULL,
            revision_number integer NOT NULL,
            revision_group_id varchar(255) NOT NULL,
            group_recorded_at timestamptz NOT NULL,
            revision_kind varchar(16) NOT NULL,
            is_tombstone boolean NOT NULL,
            supersedes_revision_id varchar(255),
            supersedes_revision_number integer,
            payload_schema_version varchar(64) NOT NULL,
            payload_hash varchar(71) NOT NULL,
            transaction_type varchar(64),
            trade_date date,
            trade_time time,
            trade_at timestamptz,
            trade_timezone varchar(64),
            trade_time_is_estimated boolean,
            settlement_date date,
            entitlement_date date,
            acquisition_date date,
            account_id varchar(255),
            settlement_cash_account_id varchar(255),
            instrument_id varchar(255),
            instrument_snapshot_json jsonb,
            quantity numeric,
            price numeric,
            gross_amount numeric,
            counter_amount numeric,
            quoted_fx_rate numeric,
            fees numeric,
            taxes numeric,
            consideration_basis varchar(32),
            numeric_scale_state varchar(24),
            quantity_input_scale integer,
            price_input_scale integer,
            gross_amount_input_scale integer,
            counter_amount_input_scale integer,
            quoted_fx_rate_input_scale integer,
            fees_input_scale integer,
            taxes_input_scale integer,
            consideration_evidence_state varchar(16) NOT NULL,
            consideration_evidence_reason_codes varchar(64)[] NOT NULL,
            consideration_terms_difference_exact numeric,
            fx_evidence_state varchar(16) NOT NULL,
            fx_evidence_reason_codes varchar(64)[] NOT NULL,
            effective_fx_rate_method50 numeric,
            quoted_terms_difference_exact numeric,
            currency varchar(3),
            transfer_scope varchar(64),
            transfer_object_type varchar(32),
            transfer_group_id varchar(255),
            counterparty_account_id varchar(255),
            note text,
            selected_reason_code varchar(64) NOT NULL,
            CONSTRAINT pk_pd_transaction_input PRIMARY KEY (manifest_id, transaction_id),
            CONSTRAINT fk_pd_transaction_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_transaction_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_transaction_exact_revision
                FOREIGN KEY (portfolio_id, transaction_id, revision_number, revision_id)
                REFERENCES portfolio.transaction_revision_record(
                    portfolio_id, transaction_id, revision_number, revision_id
                ) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_transaction_revision CHECK (revision_number > 0),
            CONSTRAINT ck_pd_transaction_revision_chain CHECK (
                (revision_number = 1 AND supersedes_revision_id IS NULL
                    AND supersedes_revision_number IS NULL)
                OR (revision_number > 1 AND supersedes_revision_id IS NOT NULL
                    AND supersedes_revision_number = revision_number - 1)
            ),
            CONSTRAINT ck_pd_transaction_hash CHECK (payload_hash ~ '^sha256:[0-9a-f]{64}$'),
            CONSTRAINT ck_pd_transaction_scale_state CHECK (
                numeric_scale_state IS NULL
                OR numeric_scale_state IN ('declared', 'legacy_inferred')
            ),
            CONSTRAINT ck_pd_transaction_consideration_basis CHECK (
                consideration_basis IS NULL
                OR consideration_basis IN ('exact_quantity_price', 'source_reported')
            ),
            CONSTRAINT ck_pd_transaction_input_scale_pairing CHECK (
                ((quantity IS NULL AND quantity_input_scale IS NULL)
                    OR (quantity IS NOT NULL AND quantity_input_scale IS NOT NULL AND quantity_input_scale BETWEEN 0 AND 12))
                AND ((price IS NULL AND price_input_scale IS NULL)
                    OR (price IS NOT NULL AND price_input_scale IS NOT NULL AND price_input_scale BETWEEN 0 AND 12))
                AND ((gross_amount IS NULL AND gross_amount_input_scale IS NULL)
                    OR (gross_amount IS NOT NULL AND gross_amount_input_scale IS NOT NULL AND gross_amount_input_scale BETWEEN 0 AND 8))
                AND ((counter_amount IS NULL AND counter_amount_input_scale IS NULL)
                    OR (counter_amount IS NOT NULL AND counter_amount_input_scale IS NOT NULL AND counter_amount_input_scale BETWEEN 0 AND 8))
                AND ((quoted_fx_rate IS NULL AND quoted_fx_rate_input_scale IS NULL)
                    OR (quoted_fx_rate IS NOT NULL AND quoted_fx_rate_input_scale IS NOT NULL AND quoted_fx_rate_input_scale BETWEEN 0 AND 18))
                AND ((fees IS NULL AND fees_input_scale IS NULL)
                    OR (fees IS NOT NULL AND fees_input_scale IS NOT NULL AND fees_input_scale BETWEEN 0 AND 8))
                AND ((taxes IS NULL AND taxes_input_scale IS NULL)
                    OR (taxes IS NOT NULL AND taxes_input_scale IS NOT NULL AND taxes_input_scale BETWEEN 0 AND 8))
            ),
            CONSTRAINT ck_pd_transaction_consideration_evidence CHECK (
                (consideration_evidence_state = 'complete'
                    AND consideration_terms_difference_exact IS NOT NULL
                    AND cardinality(consideration_evidence_reason_codes) = 0)
                OR (consideration_evidence_state = 'unavailable'
                    AND consideration_terms_difference_exact IS NULL
                    AND cardinality(consideration_evidence_reason_codes) > 0)
                OR (consideration_evidence_state = 'not_applicable'
                    AND consideration_terms_difference_exact IS NULL
                    AND cardinality(consideration_evidence_reason_codes) = 0)
            ),
            CONSTRAINT ck_pd_transaction_basis_evidence CHECK (
                (consideration_basis = 'exact_quantity_price'
                    AND consideration_evidence_state = 'complete'
                    AND consideration_terms_difference_exact = 0)
                OR (consideration_basis = 'source_reported'
                    AND consideration_evidence_state IN ('complete', 'unavailable'))
                OR (consideration_basis IS NULL
                    AND consideration_evidence_state = 'not_applicable')
            ),
            CONSTRAINT ck_pd_transaction_fx_evidence CHECK (
                (fx_evidence_state = 'complete'
                    AND effective_fx_rate_method50 IS NOT NULL
                    AND quoted_terms_difference_exact IS NOT NULL
                    AND cardinality(fx_evidence_reason_codes) = 0)
                OR (fx_evidence_state = 'partial'
                    AND effective_fx_rate_method50 IS NOT NULL
                    AND quoted_terms_difference_exact IS NULL
                    AND cardinality(fx_evidence_reason_codes) > 0)
                OR (fx_evidence_state = 'unavailable'
                    AND effective_fx_rate_method50 IS NULL
                    AND quoted_terms_difference_exact IS NULL
                    AND cardinality(fx_evidence_reason_codes) > 0)
                OR (fx_evidence_state = 'not_applicable'
                    AND effective_fx_rate_method50 IS NULL
                    AND quoted_terms_difference_exact IS NULL
                    AND cardinality(fx_evidence_reason_codes) = 0)
            ),
            CONSTRAINT ck_pd_transaction_fx_evidence_applicability CHECK (
                (transaction_type = 'fx_conversion'
                    AND fx_evidence_state IN ('complete', 'partial', 'unavailable'))
                OR (transaction_type IS DISTINCT FROM 'fx_conversion'
                    AND fx_evidence_state = 'not_applicable')
            ),
            CONSTRAINT ck_pd_transaction_fx_evidence_arithmetic CHECK (
                transaction_type IS DISTINCT FROM 'fx_conversion'
                OR fx_evidence_state = 'unavailable'
                OR (gross_amount > 0 AND counter_amount > 0
                    AND effective_fx_rate_method50 =
                        calculation_registry.round_significant_half_even(
                            counter_amount / gross_amount, 50
                        )
                    AND ((quoted_fx_rate IS NULL
                            AND fx_evidence_state = 'partial')
                        OR (quoted_fx_rate IS NOT NULL
                            AND fx_evidence_state = 'complete'
                            AND quoted_terms_difference_exact =
                                counter_amount - gross_amount * quoted_fx_rate)))
            )
        );

        CREATE TABLE portfolio.portfolio_daily_instrument_input (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            instrument_id varchar(255) NOT NULL,
            requires_valuation boolean NOT NULL,
            instrument_name varchar(500) NOT NULL,
            instrument_type varchar(64) NOT NULL,
            currency varchar(3) NOT NULL,
            price_unit varchar(64),
            contract_multiplier numeric,
            accrual_convention varchar(64),
            price_factor numeric,
            valuation_contract_state varchar(16) NOT NULL,
            valuation_contract_reason_codes varchar(64)[] NOT NULL,
            valuation_factor_source varchar(32),
            quote_policy_version varchar(64) NOT NULL,
            quote_selection_policy_revision varchar(128) NOT NULL,
            freshness_policy_version varchar(64) NOT NULL,
            freshness_mode varchar(32) NOT NULL,
            freshness_max_age_days integer NOT NULL,
            resolver_strategy_version varchar(64) NOT NULL,
            instrument_schema_version varchar(64) NOT NULL,
            instrument_hash char(64) NOT NULL,
            canonical_instrument jsonb NOT NULL,
            CONSTRAINT pk_pd_instrument_input PRIMARY KEY (manifest_id, instrument_id),
            CONSTRAINT fk_pd_instrument_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_instrument_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_instrument_currency CHECK (currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_pd_instrument_contract CHECK (
                (valuation_contract_state = 'available' AND price_unit IS NOT NULL
                    AND btrim(price_unit) <> '' AND price_unit = btrim(price_unit)
                    AND contract_multiplier > 0
                    AND price_factor > 0
                    AND contract_multiplier::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND price_factor::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND cardinality(valuation_contract_reason_codes) = 0
                    AND valuation_factor_source IS NOT NULL)
                OR (valuation_contract_state = 'unavailable'
                    AND (price_unit IS NULL OR contract_multiplier IS NULL OR price_factor IS NULL)
                    AND cardinality(valuation_contract_reason_codes) > 0)
            ),
            CONSTRAINT ck_pd_instrument_factor_source CHECK (
                valuation_factor_source IS NULL
                OR valuation_factor_source IN ('instrument', 'methodology')
            ),
            CONSTRAINT ck_pd_instrument_methodology_factor CHECK (
                valuation_factor_source <> 'methodology'
                OR instrument_type IN ('equity', 'fund', 'etf', 'exchange_traded_fund')
            ),
            CONSTRAINT ck_pd_instrument_freshness CHECK (freshness_max_age_days >= 0),
            CONSTRAINT ck_pd_instrument_quote_selection_revision CHECK (
                quote_selection_policy_revision ~ '^sha256:[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_pd_instrument_contract_reasons CHECK (
                cardinality(valuation_contract_reason_codes) = 0
                OR valuation_contract_state = 'unavailable'
            ),
            CONSTRAINT ck_pd_instrument_hash CHECK (instrument_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_pd_instrument_snapshot CHECK (jsonb_typeof(canonical_instrument) = 'object')
        );

        CREATE TABLE portfolio.portfolio_daily_corp_action_window (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            instrument_id varchar(255) NOT NULL,
            window_from date NOT NULL,
            window_to date NOT NULL,
            selection_policy_version varchar(64) NOT NULL,
            selection_policy_revision varchar(128) NOT NULL,
            consumer_policy_version varchar(64) NOT NULL,
            freshness_policy_version varchar(64) NOT NULL,
            freshness_mode varchar(32) NOT NULL,
            freshness_max_age_days integer NOT NULL,
            resolver_strategy_version varchar(64) NOT NULL,
            expected_event_count integer NOT NULL,
            captured_event_count integer NOT NULL,
            coverage_state varchar(16) NOT NULL,
            reason_codes varchar(64)[] NOT NULL DEFAULT ARRAY[]::varchar(64)[],
            CONSTRAINT pk_pd_corp_window PRIMARY KEY (manifest_id, instrument_id),
            CONSTRAINT fk_pd_corp_window_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_corp_window_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_corp_window_dates CHECK (window_from <= window_to),
            CONSTRAINT ck_pd_corp_window_counts CHECK (expected_event_count >= 0 AND captured_event_count >= 0),
            CONSTRAINT ck_pd_corp_window_freshness CHECK (freshness_max_age_days >= 0),
            CONSTRAINT ck_pd_corp_window_selection_revision CHECK (
                selection_policy_revision ~ '^sha256:[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_pd_corp_window_coverage CHECK (coverage_state IN ('complete', 'partial', 'unavailable')),
            CONSTRAINT ck_pd_corp_window_reasons CHECK (
                (coverage_state = 'complete' AND cardinality(reason_codes) = 0)
                OR (coverage_state <> 'complete' AND cardinality(reason_codes) > 0)
            )
        );

        CREATE TABLE portfolio.portfolio_daily_corp_action_input (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            corporate_action_event_id varchar(255) NOT NULL,
            instrument_id varchar(255) NOT NULL,
            action_type varchar(64) NOT NULL,
            announcement_date date,
            record_date date,
            effective_date date NOT NULL,
            payable_date date,
            new_units numeric NOT NULL,
            old_units numeric NOT NULL,
            quantity_rounding varchar(32) NOT NULL,
            quantity_precision integer NOT NULL,
            cost_basis_treatment varchar(32) NOT NULL,
            source varchar(255) NOT NULL,
            external_event_id varchar(255),
            event_status varchar(32) NOT NULL,
            event_updated_at timestamptz NOT NULL,
            event_schema_version varchar(64) NOT NULL,
            event_hash char(64) NOT NULL,
            canonical_event jsonb NOT NULL,
            CONSTRAINT pk_pd_corp_input PRIMARY KEY (manifest_id, corporate_action_event_id),
            CONSTRAINT fk_pd_corp_input_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_corp_input_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_corp_input_window FOREIGN KEY (manifest_id, instrument_id)
                REFERENCES portfolio.portfolio_daily_corp_action_window(manifest_id, instrument_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_corp_input_ratio CHECK (
                new_units > 0 AND old_units > 0
                AND new_units::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND old_units::text NOT IN ('NaN', 'Infinity', '-Infinity')
            ),
            CONSTRAINT ck_pd_corp_input_precision CHECK (quantity_precision BETWEEN 0 AND 12),
            CONSTRAINT ck_pd_corp_input_hash CHECK (event_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_pd_corp_input_snapshot CHECK (jsonb_typeof(canonical_event) = 'object')
        );
        """
    )

    op.execute(
        """
        CREATE TABLE portfolio.portfolio_daily_quote_window (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            quote_window_id uuid NOT NULL,
            instrument_id varchar(255) NOT NULL,
            quote_role varchar(32) NOT NULL,
            valuation_date date NOT NULL,
            quote_currency varchar(3) NOT NULL,
            window_start_at timestamptz NOT NULL,
            window_end_at timestamptz NOT NULL,
            selection_policy_version varchar(64) NOT NULL,
            selection_policy_revision varchar(128) NOT NULL,
            consumer_policy_version varchar(64) NOT NULL,
            freshness_policy_version varchar(64) NOT NULL,
            freshness_mode varchar(32) NOT NULL,
            freshness_max_age_days integer NOT NULL,
            resolver_strategy_version varchar(64) NOT NULL,
            freshness_limit_seconds bigint NOT NULL,
            candidate_count integer NOT NULL,
            adopted_count integer NOT NULL,
            selection_status varchar(16) NOT NULL,
            coverage_state varchar(16) NOT NULL,
            reason_codes varchar(64)[] NOT NULL DEFAULT ARRAY[]::varchar(64)[],
            CONSTRAINT pk_pd_quote_window PRIMARY KEY (manifest_id, quote_window_id),
            CONSTRAINT uq_pd_quote_window_role UNIQUE (manifest_id, instrument_id, quote_role, valuation_date),
            CONSTRAINT fk_pd_quote_window_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_quote_window_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_quote_window_currency CHECK (quote_currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_pd_quote_window_role CHECK (quote_role = 'valuation'),
            CONSTRAINT ck_pd_quote_window_bounds CHECK (window_start_at <= window_end_at),
            CONSTRAINT ck_pd_quote_window_counts CHECK (freshness_limit_seconds >= 0 AND freshness_max_age_days >= 0 AND candidate_count >= 0),
            CONSTRAINT ck_pd_quote_window_adopted CHECK (adopted_count IN (0, 1) AND adopted_count <= candidate_count),
            CONSTRAINT ck_pd_quote_window_status CHECK (selection_status IN ('selected', 'unavailable')),
            CONSTRAINT ck_pd_quote_window_selection_revision CHECK (
                selection_policy_revision ~ '^sha256:[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_pd_quote_window_coverage CHECK (coverage_state IN ('complete', 'partial', 'unavailable')),
            CONSTRAINT ck_pd_quote_window_reasons CHECK (
                (coverage_state = 'complete' AND cardinality(reason_codes) = 0)
                OR (coverage_state <> 'complete' AND cardinality(reason_codes) > 0)
            )
        );

        CREATE TABLE portfolio.portfolio_daily_quote_candidate (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            quote_window_id uuid NOT NULL,
            candidate_rank integer NOT NULL,
            quote_series_id uuid NOT NULL,
            observation_id uuid NOT NULL,
            revision_id uuid NOT NULL,
            revision_number integer NOT NULL,
            observation_date date NOT NULL,
            quote_value numeric,
            quote_status varchar(16) NOT NULL,
            source_published_at timestamptz,
            ingested_at timestamptz,
            payload_hash varchar(128) NOT NULL,
            decision varchar(16) NOT NULL,
            decision_reason_code varchar(64) NOT NULL,
            CONSTRAINT pk_pd_quote_candidate PRIMARY KEY (manifest_id, quote_window_id, candidate_rank),
            CONSTRAINT uq_pd_quote_candidate_revision UNIQUE (manifest_id, quote_window_id, revision_id),
            CONSTRAINT fk_pd_quote_candidate_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_quote_candidate_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_quote_candidate_window FOREIGN KEY (manifest_id, quote_window_id)
                REFERENCES portfolio.portfolio_daily_quote_window(manifest_id, quote_window_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_quote_candidate_series FOREIGN KEY (quote_series_id)
                REFERENCES instrument_registry.quote_series(quote_series_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_quote_candidate_observation FOREIGN KEY (observation_id)
                REFERENCES instrument_registry.quote_observation(observation_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_quote_candidate_exact_revision FOREIGN KEY (revision_id)
                REFERENCES instrument_registry.quote_observation_revision(revision_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_quote_candidate_ranks CHECK (candidate_rank > 0 AND revision_number > 0),
            CONSTRAINT ck_pd_quote_candidate_decision CHECK (decision IN ('adopted', 'excluded')),
            CONSTRAINT ck_pd_quote_candidate_reason CHECK (
                btrim(decision_reason_code) <> ''
                AND decision_reason_code = btrim(decision_reason_code)
            ),
            CONSTRAINT ck_pd_quote_candidate_value CHECK (
                (quote_status = 'withdrawn' AND quote_value IS NULL)
                OR (quote_status <> 'withdrawn' AND quote_value > 0
                    AND quote_value::text NOT IN ('NaN', 'Infinity', '-Infinity'))
            )
        );

        CREATE TABLE portfolio.portfolio_daily_fx_path (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            fx_path_id uuid NOT NULL,
            valuation_date date NOT NULL,
            from_currency varchar(3) NOT NULL,
            to_currency varchar(3) NOT NULL,
            path_kind varchar(16) NOT NULL,
            resolution_status varchar(16) NOT NULL,
            leg_count integer NOT NULL,
            resolved_rate numeric,
            rate_derivation_residual_exact numeric,
            selection_policy_version varchar(64) NOT NULL,
            consumer_policy_version varchar(64) NOT NULL,
            freshness_policy_version varchar(64) NOT NULL,
            freshness_mode varchar(32) NOT NULL,
            freshness_max_age_days integer NOT NULL,
            resolver_strategy_version varchar(64) NOT NULL,
            rate_math_precision integer NOT NULL,
            rate_rounding_mode varchar(32) NOT NULL,
            coverage_state varchar(16) NOT NULL,
            reason_codes varchar(64)[] NOT NULL DEFAULT ARRAY[]::varchar(64)[],
            CONSTRAINT pk_pd_fx_path PRIMARY KEY (manifest_id, fx_path_id),
            CONSTRAINT uq_pd_fx_path_pair UNIQUE (manifest_id, valuation_date, from_currency, to_currency),
            CONSTRAINT fk_pd_fx_path_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_fx_path_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_fx_path_currency CHECK (from_currency ~ '^[A-Z]{3}$' AND to_currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_pd_fx_path_kind CHECK (path_kind IN ('identity', 'direct', 'inverse', 'cross', 'unavailable')),
            CONSTRAINT ck_pd_fx_path_resolution_status CHECK (
                resolution_status IN ('resolved', 'unavailable')
            ),
            CONSTRAINT ck_pd_fx_path_shape CHECK (
                (path_kind = 'identity' AND leg_count = 0
                    AND resolution_status = 'resolved')
                OR (path_kind = 'unavailable' AND leg_count = 0
                    AND resolution_status = 'unavailable')
                OR (path_kind IN ('direct', 'inverse') AND leg_count = 1)
                OR (path_kind = 'cross' AND leg_count = 2)
            ),
            CONSTRAINT ck_pd_fx_path_legs CHECK (leg_count >= 0),
            CONSTRAINT ck_pd_fx_path_freshness CHECK (freshness_max_age_days >= 0),
            CONSTRAINT ck_pd_fx_path_decimal_context CHECK (
                rate_math_precision = 50 AND rate_rounding_mode = 'ROUND_HALF_EVEN'
            ),
            CONSTRAINT ck_pd_fx_path_rate CHECK (
                (resolution_status = 'unavailable' AND resolved_rate IS NULL
                    AND rate_derivation_residual_exact IS NULL)
                OR (resolution_status = 'resolved' AND path_kind <> 'unavailable'
                    AND resolved_rate IS NOT NULL
                    AND rate_derivation_residual_exact = 0
                    AND resolved_rate > 0
                    AND resolved_rate::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    )
            ),
            CONSTRAINT ck_pd_fx_path_coverage CHECK (coverage_state IN ('complete', 'partial', 'unavailable')),
            CONSTRAINT ck_pd_fx_path_reasons CHECK (
                (coverage_state = 'complete' AND cardinality(reason_codes) = 0)
                OR (coverage_state <> 'complete' AND cardinality(reason_codes) > 0)
            )
        );

        CREATE TABLE portfolio.portfolio_daily_fx_leg (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            fx_path_id uuid NOT NULL,
            leg_order integer NOT NULL,
            from_currency varchar(3) NOT NULL,
            to_currency varchar(3) NOT NULL,
            is_inverted boolean NOT NULL,
            leg_resolution_status varchar(16) NOT NULL,
            reason_codes varchar(64)[] NOT NULL,
            quote_series_id uuid,
            observation_id uuid,
            revision_id uuid,
            revision_number integer,
            observation_date date,
            quoted_rate numeric,
            effective_rate numeric,
            rate_derivation_residual_exact numeric,
            quote_status varchar(16),
            source_published_at timestamptz,
            ingested_at timestamptz,
            payload_hash varchar(128),
            consumer_policy_version varchar(64) NOT NULL,
            freshness_policy_version varchar(64) NOT NULL,
            freshness_mode varchar(32) NOT NULL,
            freshness_max_age_days integer NOT NULL,
            resolver_strategy_version varchar(64) NOT NULL,
            rate_math_precision integer NOT NULL,
            rate_rounding_mode varchar(32) NOT NULL,
            CONSTRAINT pk_pd_fx_leg PRIMARY KEY (manifest_id, fx_path_id, leg_order),
            CONSTRAINT fk_pd_fx_leg_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_fx_leg_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_fx_leg_path FOREIGN KEY (manifest_id, fx_path_id)
                REFERENCES portfolio.portfolio_daily_fx_path(manifest_id, fx_path_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_fx_leg_exact_revision FOREIGN KEY (revision_id)
                REFERENCES instrument_registry.quote_observation_revision(revision_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_fx_leg_order CHECK (leg_order > 0),
            CONSTRAINT ck_pd_fx_leg_resolution_status CHECK (
                leg_resolution_status IN ('resolved', 'missing', 'rejected')
            ),
            CONSTRAINT ck_pd_fx_leg_reasons CHECK (
                (leg_resolution_status = 'resolved') = (cardinality(reason_codes) = 0)
            ),
            CONSTRAINT ck_pd_fx_leg_evidence_shape CHECK (
                (leg_resolution_status = 'missing'
                    AND observation_id IS NULL AND revision_id IS NULL
                    AND revision_number IS NULL AND observation_date IS NULL
                    AND quoted_rate IS NULL AND effective_rate IS NULL
                    AND rate_derivation_residual_exact IS NULL
                    AND quote_status IS NULL AND source_published_at IS NULL
                    AND ingested_at IS NULL AND payload_hash IS NULL)
                OR (leg_resolution_status = 'rejected'
                    AND quote_series_id IS NOT NULL AND observation_id IS NOT NULL
                    AND revision_id IS NOT NULL AND revision_number IS NOT NULL
                    AND revision_number > 0 AND observation_date IS NOT NULL
                    AND quote_status IS NOT NULL
                    AND ((quote_status = 'withdrawn' AND quoted_rate IS NULL)
                        OR (quote_status <> 'withdrawn' AND quoted_rate IS NOT NULL
                            AND quoted_rate > 0
                            AND quoted_rate::text
                                NOT IN ('NaN', 'Infinity', '-Infinity')))
                    AND ingested_at IS NOT NULL
                    AND payload_hash IS NOT NULL AND effective_rate IS NULL
                    AND rate_derivation_residual_exact IS NULL)
                OR (leg_resolution_status = 'resolved'
                    AND quote_series_id IS NOT NULL AND observation_id IS NOT NULL
                    AND revision_id IS NOT NULL AND revision_number IS NOT NULL
                    AND revision_number > 0 AND observation_date IS NOT NULL
                    AND quoted_rate IS NOT NULL AND effective_rate IS NOT NULL
                    AND rate_derivation_residual_exact IS NOT NULL
                    AND quote_status IS NOT NULL AND quote_status <> 'withdrawn'
                    AND ingested_at IS NOT NULL
                    AND payload_hash IS NOT NULL AND quoted_rate > 0
                    AND effective_rate > 0
                    AND quoted_rate::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND effective_rate::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND rate_derivation_residual_exact::text
                        NOT IN ('NaN', 'Infinity', '-Infinity'))
            ),
            CONSTRAINT ck_pd_fx_leg_freshness CHECK (freshness_max_age_days >= 0),
            CONSTRAINT ck_pd_fx_leg_decimal_context CHECK (
                rate_math_precision = 50 AND rate_rounding_mode = 'ROUND_HALF_EVEN'
            ),
            CONSTRAINT ck_pd_fx_leg_derivation CHECK (
                leg_resolution_status <> 'resolved'
                OR ((NOT is_inverted AND rate_derivation_residual_exact = 0
                        AND effective_rate = quoted_rate)
                    OR (is_inverted AND rate_derivation_residual_exact
                        = effective_rate * quoted_rate - 1
                        AND effective_rate
                            = calculation_registry.divide_significant_half_even(
                                1, quoted_rate, rate_math_precision
                            )))
            ),
            CONSTRAINT ck_pd_fx_leg_currency CHECK (from_currency ~ '^[A-Z]{3}$' AND to_currency ~ '^[A-Z]{3}$')
        );

        CREATE TABLE portfolio.portfolio_daily_prior_publication (
            manifest_id uuid NOT NULL,
            run_id uuid NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            publication_id uuid NOT NULL,
            published_run_id uuid NOT NULL,
            published_fencing_token bigint NOT NULL,
            output_schema_version varchar(64) NOT NULL,
            canonical_output_hash char(64) NOT NULL,
            range_start date NOT NULL,
            range_end date NOT NULL,
            CONSTRAINT pk_pd_prior_publication PRIMARY KEY (manifest_id),
            CONSTRAINT fk_pd_prior_manifest_run FOREIGN KEY (manifest_id, run_id)
                REFERENCES calculation_registry.calculation_input_manifest(manifest_id, run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_prior_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_prior_publication FOREIGN KEY (publication_id)
                REFERENCES calculation_registry.calculation_publication(publication_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_prior_token CHECK (published_fencing_token > 0),
            CONSTRAINT ck_pd_prior_hash CHECK (canonical_output_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_pd_prior_range CHECK (range_start <= range_end)
        );
        """
    )


def _create_output_tables() -> None:
    op.execute(
        """
        CREATE TABLE portfolio.portfolio_daily_run_output (
            run_id uuid NOT NULL,
            output_fencing_token bigint NOT NULL,
            worker_id varchar(255) NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            calculated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            range_start date NOT NULL,
            range_end date NOT NULL,
            methodology_version varchar(128) NOT NULL,
            input_schema_version varchar(64) NOT NULL,
            output_schema_version varchar(64) NOT NULL,
            closure_status varchar(16) NOT NULL,
            canonical_output_hash char(64),
            snapshot_count bigint NOT NULL,
            measured_nav_count bigint NOT NULL,
            holding_count bigint NOT NULL,
            balance_count bigint NOT NULL,
            lot_count bigint NOT NULL,
            lot_disposition_count bigint NOT NULL,
            contribution_count bigint NOT NULL,
            unavailable_component_count bigint NOT NULL,
            ledger_balance_residual_exact numeric NOT NULL,
            nav_bridge_residual_exact numeric NOT NULL,
            pnl_residual_exact numeric NOT NULL,
            twr_residual_exact numeric NOT NULL,
            lot_residual_exact numeric NOT NULL,
            rounding_adjustment_base numeric(50,8) NOT NULL,
            coverage_state varchar(16) NOT NULL,
            reason_codes varchar(64)[] NOT NULL DEFAULT ARRAY[]::varchar(64)[],
            CONSTRAINT pk_pd_run_output PRIMARY KEY (run_id, output_fencing_token),
            CONSTRAINT fk_pd_run_output_run FOREIGN KEY (run_id)
                REFERENCES calculation_registry.calculation_run(run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_run_output_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_run_output_token CHECK (output_fencing_token > 0),
            CONSTRAINT ck_pd_run_output_range CHECK (range_start <= range_end),
            CONSTRAINT ck_pd_run_output_closure CHECK (closure_status IN ('passed', 'failed')),
            CONSTRAINT ck_pd_run_output_hash CHECK (canonical_output_hash IS NULL OR canonical_output_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_pd_run_output_counts CHECK (
                snapshot_count >= 0 AND measured_nav_count >= 0
                AND measured_nav_count <= snapshot_count AND holding_count >= 0
                AND balance_count >= 0 AND lot_count >= 0
                AND lot_disposition_count >= 0 AND contribution_count >= 0
                AND unavailable_component_count >= 0
            ),
            CONSTRAINT ck_pd_run_output_finite CHECK (
                ledger_balance_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND nav_bridge_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND pnl_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND twr_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND lot_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
            ),
            CONSTRAINT ck_pd_run_output_passed CHECK (
                closure_status = 'failed' OR (
                    canonical_output_hash IS NOT NULL AND snapshot_count > 0
                    AND ledger_balance_residual_exact = 0
                    AND nav_bridge_residual_exact = 0
                    AND pnl_residual_exact = 0
                    AND twr_residual_exact = 0
                    AND lot_residual_exact = 0
                )
            ),
            CONSTRAINT ck_pd_run_output_coverage CHECK (
                coverage_state IN ('complete', 'partial', 'unavailable')
            ),
            CONSTRAINT ck_pd_run_output_reasons CHECK (
                (coverage_state = 'complete' AND cardinality(reason_codes) = 0)
                OR (coverage_state <> 'complete' AND cardinality(reason_codes) > 0)
            )
        );

        CREATE TABLE portfolio.portfolio_daily_snapshot_output (
            run_id uuid NOT NULL,
            output_fencing_token bigint NOT NULL,
            worker_id varchar(255) NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            calculated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            as_of_date date NOT NULL,
            base_currency varchar(3) NOT NULL,
            measured_nav boolean NOT NULL,
            measured_position_market_value boolean NOT NULL,
            measured_book_pnl boolean NOT NULL,
            measured_return boolean NOT NULL,
            measured_external_flows boolean NOT NULL,
            unavailable_component_count integer NOT NULL,
            opening_nav numeric(50,8),
            closing_nav numeric(50,8),
            position_market_value numeric(50,8),
            settled_cash numeric(50,8),
            pending_receivable numeric(50,8),
            pending_payable numeric(50,8),
            accrual_receivable numeric(50,8),
            accrual_payable numeric(50,8),
            external_flow_in numeric(50,8),
            external_flow_out numeric(50,8),
            economic_pnl numeric(50,8),
            realized_pnl_daily numeric(50,8),
            unrealized_pnl_beginning numeric(50,8),
            unrealized_pnl_ending numeric(50,8),
            unrealized_pnl_change numeric(50,8),
            gross_income_daily numeric(50,8),
            return_of_capital_daily numeric(50,8),
            capitalized_fee_daily numeric(50,8),
            capitalized_tax_daily numeric(50,8),
            expensed_fee_daily numeric(50,8),
            expensed_tax_daily numeric(50,8),
            disposal_fee_in_realized_daily numeric(50,8),
            disposal_tax_in_realized_daily numeric(50,8),
            local_price_effect_daily numeric(50,8),
            position_fx_effect_daily numeric(50,8),
            position_attribution_residual_exact numeric,
            position_attribution_rounding_adjustment numeric(50,8) NOT NULL,
            cash_fx_effect_daily numeric(50,8),
            pending_fx_effect_daily numeric(50,8),
            accrual_fx_effect_daily numeric(50,8),
            fx_conversion_effect_daily numeric(50,8),
            pnl_component_rounding_adjustment numeric(50,8) NOT NULL,
            subperiod_twr_method50 numeric,
            subperiod_twr_published numeric(50,18),
            cumulative_twr_method50 numeric,
            cumulative_twr_published numeric(50,18),
            wealth_index_method50 numeric,
            wealth_index_published numeric(50,18),
            peak_wealth_index_method50 numeric,
            peak_wealth_index_published numeric(50,18),
            drawdown_method50 numeric,
            drawdown_published numeric(50,18),
            wealth_chain_rounding_adjustment_exact numeric,
            reliable_anchor_date date,
            reliable_anchor_nav_exact numeric,
            reliable_anchor_nav numeric(50,8),
            return_period_start_date date,
            return_period_end_date date,
            return_period_day_count integer,
            calculation_status varchar(32) NOT NULL,
            return_chain_status varchar(32) NOT NULL,
            nav_rounding_adjustment numeric(50,8) NOT NULL,
            pnl_rounding_adjustment numeric(50,8) NOT NULL,
            nav_coverage_state varchar(16) NOT NULL,
            nav_reason_codes varchar(64)[] NOT NULL,
            book_pnl_coverage_state varchar(16) NOT NULL,
            book_pnl_reason_codes varchar(64)[] NOT NULL,
            return_coverage_state varchar(16) NOT NULL,
            return_reason_codes varchar(64)[] NOT NULL,
            flow_coverage_state varchar(16) NOT NULL,
            flow_reason_codes varchar(64)[] NOT NULL,
            position_attribution_coverage_state varchar(16) NOT NULL,
            position_attribution_reason_codes varchar(64)[] NOT NULL,
            valuation_endpoint_status varchar(24) NOT NULL,
            valuation_reason_codes varchar(64)[] NOT NULL,
            CONSTRAINT pk_pd_snapshot_output PRIMARY KEY (run_id, output_fencing_token, as_of_date),
            CONSTRAINT fk_pd_snapshot_output_run FOREIGN KEY (run_id)
                REFERENCES calculation_registry.calculation_run(run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_snapshot_output_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_snapshot_token CHECK (output_fencing_token > 0),
            CONSTRAINT ck_pd_snapshot_currency CHECK (base_currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_pd_snapshot_unavailable CHECK (unavailable_component_count >= 0),
            CONSTRAINT ck_pd_snapshot_flows CHECK (
                (external_flow_in IS NULL OR external_flow_in >= 0)
                AND (external_flow_out IS NULL OR external_flow_out >= 0)
                AND measured_external_flows
                    = (external_flow_in IS NOT NULL AND external_flow_out IS NOT NULL)
            ),
            CONSTRAINT ck_pd_snapshot_book_pnl_dependencies CHECK (
                NOT measured_book_pnl OR (measured_nav AND measured_external_flows)
            ),
            CONSTRAINT ck_pd_snapshot_return_dependencies CHECK (
                NOT measured_return OR (measured_nav AND measured_external_flows)
            ),
            CONSTRAINT ck_pd_snapshot_rounding CHECK (
                abs(nav_rounding_adjustment) <= 0.00000004
                AND abs(pnl_rounding_adjustment) <= 0.00000003
                AND abs(pnl_component_rounding_adjustment) <= 0.00000005
                AND abs(position_attribution_rounding_adjustment) <= 0.00000004
            ),
            CONSTRAINT ck_pd_snapshot_calc_status CHECK (
                calculation_status IN ('no_new_valuation', 'calculated', 'broken', 'reanchored')
            ),
            CONSTRAINT ck_pd_snapshot_return_chain CHECK (
                return_chain_status IN ('active', 'no_new_valuation', 'broken', 'reanchor')
            ),
            CONSTRAINT ck_pd_snapshot_subperiod_method50 CHECK (
                subperiod_twr_method50 IS NULL OR subperiod_twr_method50 >= -1
            ),
            CONSTRAINT ck_pd_snapshot_drawdown_method50 CHECK (
                drawdown_method50 IS NULL
                OR (drawdown_method50 >= -1 AND drawdown_method50 <= 0)
            ),
            CONSTRAINT ck_pd_snapshot_drawdown_published CHECK (
                drawdown_published IS NULL
                OR (drawdown_published >= -1 AND drawdown_published <= 0)
            ),
            CONSTRAINT ck_pd_snapshot_anchor CHECK (
                (reliable_anchor_date IS NULL) = (reliable_anchor_nav_exact IS NULL)
                AND (reliable_anchor_nav_exact IS NULL) = (reliable_anchor_nav IS NULL)
                AND (reliable_anchor_nav IS NULL
                    OR reliable_anchor_nav = calculation_registry.round_half_even(reliable_anchor_nav_exact, 8))
            ),
            CONSTRAINT ck_pd_snapshot_return_evidence_finite CHECK (
                (reliable_anchor_nav_exact IS NULL OR reliable_anchor_nav_exact::text NOT IN ('NaN', 'Infinity', '-Infinity'))
                AND (subperiod_twr_method50 IS NULL OR subperiod_twr_method50::text NOT IN ('NaN', 'Infinity', '-Infinity'))
                AND (cumulative_twr_method50 IS NULL OR cumulative_twr_method50::text NOT IN ('NaN', 'Infinity', '-Infinity'))
                AND (wealth_index_method50 IS NULL OR wealth_index_method50::text NOT IN ('NaN', 'Infinity', '-Infinity'))
                AND (peak_wealth_index_method50 IS NULL OR peak_wealth_index_method50::text NOT IN ('NaN', 'Infinity', '-Infinity'))
                AND (drawdown_method50 IS NULL OR drawdown_method50::text NOT IN ('NaN', 'Infinity', '-Infinity'))
                AND (wealth_chain_rounding_adjustment_exact IS NULL OR wealth_chain_rounding_adjustment_exact::text NOT IN ('NaN', 'Infinity', '-Infinity'))
            ),
            CONSTRAINT ck_pd_snapshot_return_method_storage_domain CHECK (
                (subperiod_twr_method50 IS NULL OR (
                    abs(subperiod_twr_method50) < 1e32
                    AND subperiod_twr_method50 = trunc(subperiod_twr_method50, 100)
                ))
                AND (cumulative_twr_method50 IS NULL OR (
                    abs(cumulative_twr_method50) < 1e32
                    AND cumulative_twr_method50 = trunc(cumulative_twr_method50, 100)
                ))
                AND (wealth_index_method50 IS NULL OR (
                    abs(wealth_index_method50) < 1e32
                    AND wealth_index_method50 = trunc(wealth_index_method50, 100)
                ))
                AND (peak_wealth_index_method50 IS NULL OR (
                    abs(peak_wealth_index_method50) < 1e32
                    AND peak_wealth_index_method50 = trunc(peak_wealth_index_method50, 100)
                ))
                AND (drawdown_method50 IS NULL OR (
                    abs(drawdown_method50) < 1e32
                    AND drawdown_method50 = trunc(drawdown_method50, 100)
                ))
            ),
            CONSTRAINT ck_pd_snapshot_return_adjustment_storage_domain CHECK (
                wealth_chain_rounding_adjustment_exact IS NULL OR (
                    abs(wealth_chain_rounding_adjustment_exact) < 1e32
                    AND wealth_chain_rounding_adjustment_exact
                        = trunc(wealth_chain_rounding_adjustment_exact, 200)
                )
            ),
            CONSTRAINT ck_pd_snapshot_return_evidence_shape CHECK (
                (cumulative_twr_method50 IS NULL) = (cumulative_twr_published IS NULL)
                AND (wealth_index_method50 IS NULL) = (wealth_index_published IS NULL)
                AND (peak_wealth_index_method50 IS NULL) = (peak_wealth_index_published IS NULL)
                AND (drawdown_method50 IS NULL) = (drawdown_published IS NULL)
                AND (cumulative_twr_method50 IS NULL) = (wealth_index_method50 IS NULL)
                AND (wealth_index_method50 IS NULL) = (peak_wealth_index_method50 IS NULL)
                AND (peak_wealth_index_method50 IS NULL) = (drawdown_method50 IS NULL)
            ),
            CONSTRAINT ck_pd_snapshot_return_method_chain CHECK (
                cumulative_twr_method50 IS NULL OR (
                    wealth_index_method50 >= 0
                    AND peak_wealth_index_method50 > 0
                    AND peak_wealth_index_method50 >= wealth_index_method50
                    AND cumulative_twr_method50
                        = calculation_registry.round_significant_half_even(
                            wealth_index_method50 - 1,
                            50
                        )
                    AND drawdown_method50
                        = calculation_registry.divide_significant_half_even(
                            calculation_registry.round_significant_half_even(
                                wealth_index_method50 - peak_wealth_index_method50,
                                50
                            ),
                            peak_wealth_index_method50,
                            50
                        )
                )
            ),
            CONSTRAINT ck_pd_snapshot_return_method_precision CHECK (
                (subperiod_twr_method50 IS NULL OR subperiod_twr_method50
                    = calculation_registry.round_significant_half_even(subperiod_twr_method50, 50))
                AND (cumulative_twr_method50 IS NULL OR cumulative_twr_method50
                    = calculation_registry.round_significant_half_even(cumulative_twr_method50, 50))
                AND (wealth_index_method50 IS NULL OR wealth_index_method50
                    = calculation_registry.round_significant_half_even(wealth_index_method50, 50))
                AND (peak_wealth_index_method50 IS NULL OR peak_wealth_index_method50
                    = calculation_registry.round_significant_half_even(peak_wealth_index_method50, 50))
                AND (drawdown_method50 IS NULL OR drawdown_method50
                    = calculation_registry.round_significant_half_even(drawdown_method50, 50))
            ),
            CONSTRAINT ck_pd_snapshot_subperiod_evidence_shape CHECK (
                (subperiod_twr_method50 IS NULL) = (subperiod_twr_published IS NULL)
                AND (subperiod_twr_method50 IS NULL)
                    = (wealth_chain_rounding_adjustment_exact IS NULL)
            ),
            CONSTRAINT ck_pd_snapshot_subperiod_published CHECK (
                subperiod_twr_published IS NULL OR subperiod_twr_published
                    = calculation_registry.round_half_even(subperiod_twr_method50, 18)
            ),
            CONSTRAINT ck_pd_snapshot_cumulative_published CHECK (
                cumulative_twr_published IS NULL OR cumulative_twr_published
                    = calculation_registry.round_half_even(cumulative_twr_method50, 18)
            ),
            CONSTRAINT ck_pd_snapshot_wealth_published CHECK (
                wealth_index_published IS NULL OR wealth_index_published
                    = calculation_registry.round_half_even(wealth_index_method50, 18)
            ),
            CONSTRAINT ck_pd_snapshot_peak_wealth_published CHECK (
                peak_wealth_index_published IS NULL OR peak_wealth_index_published
                    = calculation_registry.round_half_even(peak_wealth_index_method50, 18)
            ),
            CONSTRAINT ck_pd_snapshot_drawdown_publication_rounding CHECK (
                drawdown_published IS NULL OR drawdown_published
                    = calculation_registry.round_half_even(drawdown_method50, 18)
            ),
            CONSTRAINT ck_pd_snapshot_return_status_shape CHECK (
                (calculation_status = 'calculated' AND return_chain_status = 'active')
                OR (calculation_status = 'reanchored' AND return_chain_status = 'reanchor')
                OR (calculation_status = 'broken' AND return_chain_status = 'broken')
                OR (calculation_status = 'no_new_valuation'
                    AND return_chain_status = 'no_new_valuation')
            ),
            CONSTRAINT ck_pd_snapshot_reanchor_values CHECK (
                calculation_status <> 'reanchored' OR (
                    subperiod_twr_method50 IS NULL
                    AND wealth_chain_rounding_adjustment_exact IS NULL
                    AND cumulative_twr_method50 IS NOT NULL
                    AND cumulative_twr_method50 = 0
                    AND wealth_index_method50 IS NOT NULL
                    AND wealth_index_method50 = 1
                    AND peak_wealth_index_method50 IS NOT NULL
                    AND peak_wealth_index_method50 = 1
                    AND drawdown_method50 IS NOT NULL
                    AND drawdown_method50 = 0
                    AND reliable_anchor_date IS NOT NULL
                    AND reliable_anchor_nav_exact IS NOT NULL
                    AND reliable_anchor_nav IS NOT NULL
                )
            ),
            CONSTRAINT ck_pd_snapshot_broken_values CHECK (
                calculation_status <> 'broken' OR (
                    subperiod_twr_method50 IS NULL
                    AND cumulative_twr_method50 IS NULL
                    AND wealth_index_method50 IS NULL
                    AND peak_wealth_index_method50 IS NULL
                    AND drawdown_method50 IS NULL
                    AND wealth_chain_rounding_adjustment_exact IS NULL
                )
            ),
            CONSTRAINT ck_pd_snapshot_no_new_values CHECK (
                calculation_status <> 'no_new_valuation'
                OR (
                    subperiod_twr_method50 IS NULL
                    AND wealth_chain_rounding_adjustment_exact IS NULL
                    AND return_period_start_date IS NULL
                    AND return_period_end_date IS NULL
                    AND return_period_day_count IS NULL
                    AND (
                        (
                            reliable_anchor_date IS NULL
                            AND reliable_anchor_nav_exact IS NULL
                            AND reliable_anchor_nav IS NULL
                            AND cumulative_twr_method50 IS NULL
                            AND wealth_index_method50 IS NULL
                            AND peak_wealth_index_method50 IS NULL
                            AND drawdown_method50 IS NULL
                        ) OR (
                            reliable_anchor_date IS NOT NULL
                            AND reliable_anchor_nav_exact IS NOT NULL
                            AND reliable_anchor_nav IS NOT NULL
                            AND cumulative_twr_method50 IS NOT NULL
                            AND wealth_index_method50 IS NOT NULL
                            AND wealth_index_method50 > 0
                            AND peak_wealth_index_method50 IS NOT NULL
                            AND drawdown_method50 IS NOT NULL
                        )
                    )
                )
            ),
            CONSTRAINT ck_pd_snapshot_return_period CHECK (
                (return_period_start_date IS NULL) = (return_period_end_date IS NULL)
                AND (return_period_start_date IS NULL) = (return_period_day_count IS NULL)
                AND ((calculation_status = 'calculated')
                    = (return_period_start_date IS NOT NULL))
                AND (return_period_day_count IS NULL OR (
                    return_period_day_count > 0
                    AND return_period_start_date < return_period_end_date
                    AND return_period_end_date = as_of_date
                ))
            ),
            CONSTRAINT ck_pd_snapshot_measured_nav CHECK (
                (measured_nav AND measured_position_market_value
                    AND closing_nav IS NOT NULL
                    AND settled_cash IS NOT NULL AND pending_receivable IS NOT NULL
                    AND pending_payable IS NOT NULL AND accrual_receivable IS NOT NULL
                    AND accrual_payable IS NOT NULL)
                OR (NOT measured_nav AND closing_nav IS NULL)
            ),
            CONSTRAINT ck_pd_snapshot_measured_position CHECK (
                (measured_position_market_value AND position_market_value IS NOT NULL)
                OR (NOT measured_position_market_value AND position_market_value IS NULL)
            ),
            CONSTRAINT ck_pd_snapshot_nav_bridge CHECK (
                NOT measured_nav OR closing_nav = position_market_value + settled_cash + pending_receivable
                    - pending_payable + accrual_receivable - accrual_payable
                    + nav_rounding_adjustment
            ),
            CONSTRAINT ck_pd_snapshot_measured_pnl CHECK (
                NOT measured_book_pnl OR (economic_pnl IS NOT NULL
                    AND opening_nav IS NOT NULL AND closing_nav IS NOT NULL
                    AND realized_pnl_daily IS NOT NULL
                    AND unrealized_pnl_beginning IS NOT NULL
                    AND unrealized_pnl_ending IS NOT NULL
                    AND unrealized_pnl_change = unrealized_pnl_ending - unrealized_pnl_beginning
                    AND gross_income_daily IS NOT NULL
                    AND expensed_fee_daily IS NOT NULL AND expensed_tax_daily IS NOT NULL
                    AND cash_fx_effect_daily IS NOT NULL
                    AND pending_fx_effect_daily IS NOT NULL
                    AND accrual_fx_effect_daily IS NOT NULL
                    AND fx_conversion_effect_daily IS NOT NULL)
            ),
            CONSTRAINT ck_pd_snapshot_pnl_components CHECK (
                NOT measured_book_pnl OR economic_pnl = realized_pnl_daily
                    + unrealized_pnl_change + gross_income_daily
                    - expensed_fee_daily - expensed_tax_daily
                    + cash_fx_effect_daily + pending_fx_effect_daily
                    + accrual_fx_effect_daily + fx_conversion_effect_daily
                    + pnl_component_rounding_adjustment
            ),
            CONSTRAINT ck_pd_snapshot_position_attribution CHECK (
                (position_attribution_coverage_state = 'complete'
                    AND position_attribution_residual_exact IS NOT NULL
                    AND position_attribution_residual_exact = 0
                    AND local_price_effect_daily IS NOT NULL
                    AND position_fx_effect_daily IS NOT NULL
                    AND return_of_capital_daily IS NOT NULL
                    AND capitalized_fee_daily IS NOT NULL
                    AND capitalized_tax_daily IS NOT NULL
                    AND disposal_fee_in_realized_daily IS NOT NULL
                    AND disposal_tax_in_realized_daily IS NOT NULL
                    AND realized_pnl_daily + unrealized_pnl_change
                        = local_price_effect_daily + position_fx_effect_daily
                            + return_of_capital_daily - capitalized_fee_daily
                            - capitalized_tax_daily - disposal_fee_in_realized_daily
                            - disposal_tax_in_realized_daily
                            + position_attribution_rounding_adjustment)
                OR (position_attribution_coverage_state <> 'complete'
                    AND position_attribution_residual_exact IS NULL)
            ),
            CONSTRAINT ck_pd_snapshot_pnl_bridge CHECK (
                NOT (measured_nav AND measured_book_pnl)
                OR closing_nav + external_flow_out = opening_nav + external_flow_in
                    + economic_pnl + pnl_rounding_adjustment
            ),
            CONSTRAINT ck_pd_snapshot_measured_return CHECK (
                (measured_return AND subperiod_twr_method50 IS NOT NULL
                    AND subperiod_twr_published IS NOT NULL
                    AND wealth_chain_rounding_adjustment_exact IS NOT NULL
                    AND cumulative_twr_method50 IS NOT NULL
                    AND cumulative_twr_published IS NOT NULL
                    AND wealth_index_method50 IS NOT NULL
                    AND wealth_index_published IS NOT NULL
                    AND peak_wealth_index_method50 IS NOT NULL
                    AND peak_wealth_index_published IS NOT NULL
                    AND drawdown_method50 IS NOT NULL
                    AND drawdown_published IS NOT NULL
                    AND calculation_status = 'calculated')
                OR (NOT measured_return AND subperiod_twr_method50 IS NULL
                    AND subperiod_twr_published IS NULL
                    AND wealth_chain_rounding_adjustment_exact IS NULL
                    AND calculation_status <> 'calculated')
            ),
            CONSTRAINT ck_pd_snapshot_coverage CHECK (
                nav_coverage_state IN ('complete', 'partial', 'unavailable')
                AND book_pnl_coverage_state IN ('complete', 'partial', 'unavailable')
                AND return_coverage_state IN ('complete', 'partial', 'unavailable')
                AND flow_coverage_state IN ('complete', 'partial', 'unavailable')
                AND position_attribution_coverage_state IN ('complete', 'partial', 'unavailable')
            ),
            CONSTRAINT ck_pd_snapshot_reasons CHECK (
                ((nav_coverage_state = 'complete') = (cardinality(nav_reason_codes) = 0))
                AND ((book_pnl_coverage_state = 'complete') = (cardinality(book_pnl_reason_codes) = 0))
                AND ((return_coverage_state = 'complete') = (cardinality(return_reason_codes) = 0))
                AND ((flow_coverage_state = 'complete') = (cardinality(flow_reason_codes) = 0))
                AND ((position_attribution_coverage_state = 'complete')
                    = (cardinality(position_attribution_reason_codes) = 0))
            ),
            CONSTRAINT ck_pd_snapshot_measured_coverage CHECK (
                measured_nav = (nav_coverage_state = 'complete')
                AND measured_book_pnl = (book_pnl_coverage_state = 'complete')
                AND measured_return = (return_coverage_state = 'complete')
                AND measured_external_flows = (flow_coverage_state = 'complete')
                AND (flow_coverage_state <> 'partial'
                    OR ((external_flow_in IS NULL) <> (external_flow_out IS NULL)))
                AND (flow_coverage_state <> 'unavailable'
                    OR (external_flow_in IS NULL AND external_flow_out IS NULL))
            ),
            CONSTRAINT ck_pd_snapshot_valuation_status CHECK (
                valuation_endpoint_status IN ('fresh', 'carry_forward', 'stale', 'unavailable')
            ),
            CONSTRAINT ck_pd_snapshot_valuation_reasons CHECK (
                (valuation_endpoint_status = 'fresh' AND cardinality(valuation_reason_codes) = 0)
                OR (valuation_endpoint_status <> 'fresh' AND cardinality(valuation_reason_codes) > 0)
            )
        );

        CREATE TABLE portfolio.portfolio_daily_holding_output (
            run_id uuid NOT NULL,
            output_fencing_token bigint NOT NULL,
            worker_id varchar(255) NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            calculated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            as_of_date date NOT NULL,
            account_id varchar(255) NOT NULL,
            instrument_id varchar(255) NOT NULL,
            currency varchar(3) NOT NULL,
            quantity_exact numeric NOT NULL,
            quantity numeric(50,12) NOT NULL,
            measured_price boolean NOT NULL,
            measured_market_value boolean NOT NULL,
            measured_book_pnl boolean NOT NULL,
            unavailable_component_count integer NOT NULL,
            adopted_price_exact numeric,
            price numeric(50,12),
            contract_multiplier_exact numeric,
            contract_multiplier numeric(50,12),
            price_factor_exact numeric,
            price_factor numeric(50,12),
            adopted_fx_rate_exact numeric,
            fx_rate_to_base numeric(50,18),
            market_value_local_exact numeric,
            market_value_base_exact numeric,
            market_value_local numeric(50,8),
            market_value_base numeric(50,8),
            cost_basis_local numeric(50,8),
            cost_basis_base numeric(50,8),
            economic_pnl_daily_base numeric(50,8),
            realized_pnl_daily_base numeric(50,8),
            unrealized_pnl_beginning_base numeric(50,8),
            unrealized_pnl_ending_base numeric(50,8),
            unrealized_pnl_change_base numeric(50,8),
            gross_income_daily_base numeric(50,8),
            return_of_capital_daily_base numeric(50,8),
            capitalized_fee_daily_base numeric(50,8),
            capitalized_tax_daily_base numeric(50,8),
            expensed_fee_daily_base numeric(50,8),
            expensed_tax_daily_base numeric(50,8),
            disposal_fee_in_realized_daily_base numeric(50,8),
            disposal_tax_in_realized_daily_base numeric(50,8),
            local_price_effect_daily_base numeric(50,8),
            position_fx_effect_daily_base numeric(50,8),
            fx_conversion_effect_daily_base numeric(50,8),
            pnl_component_rounding_adjustment_base numeric(50,8) NOT NULL,
            position_attribution_residual_exact numeric,
            position_attribution_rounding_adjustment_base numeric(50,8) NOT NULL,
            portfolio_weight numeric(50,18),
            return_contribution numeric(50,18),
            local_rounding_adjustment numeric(50,8) NOT NULL,
            base_rounding_adjustment numeric(50,8) NOT NULL,
            valuation_coverage_state varchar(16) NOT NULL,
            valuation_coverage_reason_codes varchar(64)[] NOT NULL,
            book_pnl_coverage_state varchar(16) NOT NULL,
            book_pnl_reason_codes varchar(64)[] NOT NULL,
            position_attribution_coverage_state varchar(16) NOT NULL,
            position_attribution_reason_codes varchar(64)[] NOT NULL,
            valuation_endpoint_status varchar(24) NOT NULL,
            valuation_reason_codes varchar(64)[] NOT NULL,
            CONSTRAINT pk_pd_holding_output PRIMARY KEY (run_id, output_fencing_token, as_of_date, account_id, instrument_id),
            CONSTRAINT fk_pd_holding_output_run FOREIGN KEY (run_id)
                REFERENCES calculation_registry.calculation_run(run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_holding_output_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_holding_token CHECK (output_fencing_token > 0),
            CONSTRAINT ck_pd_holding_currency CHECK (currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_pd_holding_unavailable CHECK (unavailable_component_count >= 0),
            CONSTRAINT ck_pd_holding_quantity CHECK (
                quantity_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND quantity = calculation_registry.round_half_even(quantity_exact, 12)
            ),
            CONSTRAINT ck_pd_holding_rounding CHECK (
                abs(local_rounding_adjustment) <= 0.00000001
                AND abs(base_rounding_adjustment) <= 0.00000001
            ),
            CONSTRAINT ck_pd_holding_pnl_rounding CHECK (
                abs(pnl_component_rounding_adjustment_base) <= 0.00000001
                AND abs(position_attribution_rounding_adjustment_base) <= 0.00000001
            ),
            CONSTRAINT ck_pd_holding_measured_price CHECK (
                (measured_price AND adopted_price_exact > 0
                    AND adopted_price_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND price = calculation_registry.round_half_even(adopted_price_exact, 12))
                OR (NOT measured_price AND adopted_price_exact IS NULL AND price IS NULL)
            ),
            CONSTRAINT ck_pd_holding_measured_value CHECK (
                (measured_market_value AND measured_price
                    AND contract_multiplier_exact IS NOT NULL
                    AND price_factor_exact IS NOT NULL
                    AND adopted_fx_rate_exact IS NOT NULL
                    AND market_value_local_exact IS NOT NULL
                    AND market_value_base_exact IS NOT NULL
                    AND market_value_local IS NOT NULL AND market_value_base IS NOT NULL
                    AND contract_multiplier_exact > 0 AND price_factor_exact > 0
                    AND adopted_fx_rate_exact > 0
                    AND market_value_local_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND market_value_base_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND market_value_local_exact = quantity_exact * adopted_price_exact
                        * contract_multiplier_exact * price_factor_exact
                    AND market_value_base_exact = market_value_local_exact * adopted_fx_rate_exact
                    AND market_value_local = calculation_registry.round_half_even(market_value_local_exact, 8) + local_rounding_adjustment
                    AND market_value_base = calculation_registry.round_half_even(market_value_base_exact, 8) + base_rounding_adjustment)
                OR (NOT measured_market_value AND market_value_local_exact IS NULL
                    AND market_value_base_exact IS NULL AND market_value_local IS NULL
                    AND market_value_base IS NULL)
            ),
            CONSTRAINT ck_pd_holding_multiplier CHECK (
                (contract_multiplier_exact IS NULL AND contract_multiplier IS NULL)
                OR (contract_multiplier_exact IS NOT NULL AND contract_multiplier IS NOT NULL
                    AND contract_multiplier_exact > 0
                    AND contract_multiplier_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND contract_multiplier = calculation_registry.round_half_even(contract_multiplier_exact, 12))
            ),
            CONSTRAINT ck_pd_holding_price_factor CHECK (
                (price_factor_exact IS NULL AND price_factor IS NULL)
                OR (price_factor_exact IS NOT NULL AND price_factor IS NOT NULL
                    AND price_factor_exact > 0
                    AND price_factor_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND price_factor = calculation_registry.round_half_even(price_factor_exact, 12))
            ),
            CONSTRAINT ck_pd_holding_fx_rate CHECK (
                (adopted_fx_rate_exact IS NULL AND fx_rate_to_base IS NULL)
                OR (adopted_fx_rate_exact IS NOT NULL AND fx_rate_to_base IS NOT NULL
                    AND adopted_fx_rate_exact > 0
                    AND adopted_fx_rate_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND fx_rate_to_base = calculation_registry.round_half_even(adopted_fx_rate_exact, 18))
            ),
            CONSTRAINT ck_pd_holding_measured_pnl CHECK (
                NOT measured_book_pnl OR (cost_basis_local IS NOT NULL
                    AND cost_basis_base IS NOT NULL
                    AND economic_pnl_daily_base IS NOT NULL
                    AND realized_pnl_daily_base IS NOT NULL
                    AND unrealized_pnl_beginning_base IS NOT NULL
                    AND unrealized_pnl_ending_base IS NOT NULL
                    AND unrealized_pnl_change_base = unrealized_pnl_ending_base - unrealized_pnl_beginning_base
                    AND gross_income_daily_base IS NOT NULL
                    AND expensed_fee_daily_base IS NOT NULL
                    AND expensed_tax_daily_base IS NOT NULL
                    AND fx_conversion_effect_daily_base IS NOT NULL)
            ),
            CONSTRAINT ck_pd_holding_pnl_components CHECK (
                NOT measured_book_pnl OR economic_pnl_daily_base
                    = realized_pnl_daily_base + unrealized_pnl_change_base
                        + gross_income_daily_base - expensed_fee_daily_base
                        - expensed_tax_daily_base + fx_conversion_effect_daily_base
                        + pnl_component_rounding_adjustment_base
            ),
            CONSTRAINT ck_pd_holding_position_attribution CHECK (
                (position_attribution_coverage_state = 'complete'
                    AND position_attribution_residual_exact IS NOT NULL
                    AND position_attribution_residual_exact = 0
                    AND local_price_effect_daily_base IS NOT NULL
                    AND position_fx_effect_daily_base IS NOT NULL
                    AND return_of_capital_daily_base IS NOT NULL
                    AND capitalized_fee_daily_base IS NOT NULL
                    AND capitalized_tax_daily_base IS NOT NULL
                    AND disposal_fee_in_realized_daily_base IS NOT NULL
                    AND disposal_tax_in_realized_daily_base IS NOT NULL
                    AND realized_pnl_daily_base + unrealized_pnl_change_base
                        = local_price_effect_daily_base + position_fx_effect_daily_base
                            + return_of_capital_daily_base - capitalized_fee_daily_base
                            - capitalized_tax_daily_base
                            - disposal_fee_in_realized_daily_base
                            - disposal_tax_in_realized_daily_base
                            + position_attribution_rounding_adjustment_base)
                OR (position_attribution_coverage_state <> 'complete'
                    AND position_attribution_residual_exact IS NULL)
            ),
            CONSTRAINT ck_pd_holding_coverage CHECK (
                valuation_coverage_state IN ('complete', 'partial', 'unavailable')
                AND book_pnl_coverage_state IN ('complete', 'partial', 'unavailable')
                AND position_attribution_coverage_state IN ('complete', 'partial', 'unavailable')
            ),
            CONSTRAINT ck_pd_holding_reasons CHECK (
                ((valuation_coverage_state = 'complete')
                    = (cardinality(valuation_coverage_reason_codes) = 0))
                AND ((book_pnl_coverage_state = 'complete')
                    = (cardinality(book_pnl_reason_codes) = 0))
                AND ((position_attribution_coverage_state = 'complete')
                    = (cardinality(position_attribution_reason_codes) = 0))
            ),
            CONSTRAINT ck_pd_holding_measured_coverage CHECK (
                measured_market_value = (valuation_coverage_state = 'complete')
                AND measured_book_pnl = (book_pnl_coverage_state = 'complete')
            ),
            CONSTRAINT ck_pd_holding_valuation_status CHECK (
                valuation_endpoint_status IN ('fresh', 'carry_forward', 'stale', 'unavailable')
            ),
            CONSTRAINT ck_pd_holding_valuation_reasons CHECK (
                (valuation_endpoint_status = 'fresh' AND cardinality(valuation_reason_codes) = 0)
                OR (valuation_endpoint_status <> 'fresh' AND cardinality(valuation_reason_codes) > 0)
            )
        );
        """
    )


def _seed_scope_generations() -> None:
    # Existing portfolios predate the calculation registry.  INSERT is allowed
    # by the registry guard; an existing generation is never overwritten.
    op.execute(
        """
        INSERT INTO calculation_registry.calculation_scope_generation (
            calculation_kind, scope_kind, scope_id, generation
        )
        SELECT 'portfolio_daily', 'portfolio', portfolio_id, 0
        FROM portfolio.portfolio_record
        ON CONFLICT (calculation_kind, scope_kind, scope_id) DO NOTHING
        """
    )


def _create_common_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION portfolio.pd_reason_codes_are_canonical(p_codes varchar[])
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT p_codes IS NOT NULL
               AND NOT EXISTS (
                    SELECT 1 FROM unnest(p_codes) AS code
                    WHERE code IS NULL OR btrim(code) = '' OR code <> btrim(code)
               )
               AND p_codes = COALESCE(
                    (SELECT array_agg(code ORDER BY code COLLATE "C")
                     FROM (SELECT DISTINCT code FROM unnest(p_codes) AS code) AS d),
                    ARRAY[]::varchar[]
               )
        $$;

        CREATE FUNCTION portfolio.pd_reject_truncate()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'portfolio_daily_truncate_forbidden: %.%',
                TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_dependency_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_run record;
            v_manifest_run_id uuid;
        BEGIN
            SELECT calculation_kind, scope_kind, scope_id
            INTO v_run
            FROM calculation_registry.calculation_run
            WHERE run_id = NEW.run_id
            FOR KEY SHARE;
            SELECT run_id INTO v_manifest_run_id
            FROM calculation_registry.calculation_input_manifest
            WHERE manifest_id = NEW.manifest_id
            FOR KEY SHARE;
            IF NOT FOUND OR v_manifest_run_id IS DISTINCT FROM NEW.run_id
               OR v_run.calculation_kind IS DISTINCT FROM 'portfolio_daily'
               OR v_run.scope_kind IS DISTINCT FROM 'portfolio'
               OR v_run.scope_id IS DISTINCT FROM NEW.portfolio_id THEN
                RAISE EXCEPTION 'portfolio_daily_dependency_scope_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_common_reasons()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT portfolio.pd_reason_codes_are_canonical(NEW.reason_codes) THEN
                RAISE EXCEPTION 'portfolio_daily_reason_codes_not_canonical'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_instrument_reasons()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT portfolio.pd_reason_codes_are_canonical(
                    NEW.valuation_contract_reason_codes
               ) THEN
                RAISE EXCEPTION 'portfolio_daily_reason_codes_not_canonical'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_snapshot_reasons()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT portfolio.pd_reason_codes_are_canonical(NEW.nav_reason_codes)
               OR NOT portfolio.pd_reason_codes_are_canonical(NEW.book_pnl_reason_codes)
               OR NOT portfolio.pd_reason_codes_are_canonical(NEW.return_reason_codes)
               OR NOT portfolio.pd_reason_codes_are_canonical(NEW.flow_reason_codes)
               OR NOT portfolio.pd_reason_codes_are_canonical(
                    NEW.position_attribution_reason_codes
               )
               OR NOT portfolio.pd_reason_codes_are_canonical(NEW.valuation_reason_codes) THEN
                RAISE EXCEPTION 'portfolio_daily_reason_codes_not_canonical'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_holding_reasons()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT portfolio.pd_reason_codes_are_canonical(NEW.valuation_coverage_reason_codes)
               OR NOT portfolio.pd_reason_codes_are_canonical(NEW.book_pnl_reason_codes)
               OR NOT portfolio.pd_reason_codes_are_canonical(
                    NEW.position_attribution_reason_codes
               )
               OR NOT portfolio.pd_reason_codes_are_canonical(NEW.valuation_reason_codes) THEN
                RAISE EXCEPTION 'portfolio_daily_reason_codes_not_canonical'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_lot_reasons()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT portfolio.pd_reason_codes_are_canonical(
                    NEW.base_cost_reason_codes
               ) THEN
                RAISE EXCEPTION 'portfolio_daily_reason_codes_not_canonical'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_lot_disposition_reasons()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT portfolio.pd_reason_codes_are_canonical(
                    NEW.base_pnl_reason_codes
               ) THEN
                RAISE EXCEPTION 'portfolio_daily_reason_codes_not_canonical'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_reject_output_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'portfolio_daily_output_immutable: %.%',
                TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_output_finite()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF to_jsonb(NEW)::text ~ '"(NaN|Infinity|-Infinity)"' THEN
                RAISE EXCEPTION 'portfolio_daily_output_nonfinite_numeric'
                    USING ERRCODE = '22003';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_lot_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_manifest_id uuid;
            v_source record;
            v_custody record;
            v_source_found boolean;
            v_custody_found boolean;
        BEGIN
            SELECT manifest_id INTO v_manifest_id
            FROM calculation_registry.calculation_run
            WHERE run_id = NEW.run_id
            FOR KEY SHARE;
            SELECT transaction_type, trade_date, acquisition_date, account_id,
                   instrument_id, currency, is_tombstone
            INTO v_source
            FROM portfolio.portfolio_daily_transaction_input
            WHERE manifest_id = v_manifest_id
              AND transaction_id = NEW.source_transaction_id
              AND revision_id = NEW.source_revision_id
              AND revision_number = NEW.source_revision_number;
            v_source_found := FOUND;
            SELECT transaction_type, account_id, instrument_id, currency,
                   transfer_scope, transfer_object_type, is_tombstone
            INTO v_custody
            FROM portfolio.portfolio_daily_transaction_input
            WHERE manifest_id = v_manifest_id
              AND transaction_id = NEW.custody_transaction_id
              AND revision_id = NEW.custody_revision_id
              AND revision_number = NEW.custody_revision_number;
            v_custody_found := FOUND;
            IF NOT v_source_found OR v_source.is_tombstone
               OR v_source.transaction_type NOT IN (
                    'buy', 'dividend_reinvestment', 'opening_balance', 'transfer_in'
               )
               OR v_source.instrument_id IS DISTINCT FROM NEW.instrument_id
               OR v_source.currency IS DISTINCT FROM NEW.currency
               OR COALESCE(v_source.acquisition_date, v_source.trade_date)
                    IS DISTINCT FROM NEW.acquisition_date
               OR NOT v_custody_found OR v_custody.is_tombstone
               OR v_custody.account_id IS DISTINCT FROM NEW.account_id
               OR v_custody.instrument_id IS DISTINCT FROM NEW.instrument_id
               OR v_custody.currency IS DISTINCT FROM NEW.currency
               OR NOT (
                    (NEW.custody_transaction_id = NEW.source_transaction_id
                        AND NEW.custody_revision_id = NEW.source_revision_id
                        AND NEW.custody_revision_number = NEW.source_revision_number)
                    OR (v_custody.transaction_type = 'transfer_in'
                        AND v_custody.transfer_scope = 'internal_portfolio'
                        AND v_custody.transfer_object_type = 'position')
               ) THEN
                RAISE EXCEPTION 'portfolio_daily_lot_source_lineage_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_lot_disposition_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_manifest_id uuid;
            v_acquisition record;
            v_custody record;
            v_disposition record;
            v_acquisition_found boolean;
            v_custody_found boolean;
            v_disposition_found boolean;
        BEGIN
            SELECT manifest_id INTO v_manifest_id
            FROM calculation_registry.calculation_run
            WHERE run_id = NEW.run_id
            FOR KEY SHARE;
            SELECT transaction_type, account_id, instrument_id, currency,
                   is_tombstone
            INTO v_acquisition
            FROM portfolio.portfolio_daily_transaction_input
            WHERE manifest_id = v_manifest_id
              AND transaction_id = NEW.acquisition_transaction_id
              AND revision_id = NEW.acquisition_revision_id
              AND revision_number = NEW.acquisition_revision_number;
            v_acquisition_found := FOUND;
            SELECT transaction_type, account_id, instrument_id, currency,
                   transfer_scope, transfer_object_type, is_tombstone
            INTO v_custody
            FROM portfolio.portfolio_daily_transaction_input
            WHERE manifest_id = v_manifest_id
              AND transaction_id = NEW.custody_transaction_id
              AND revision_id = NEW.custody_revision_id
              AND revision_number = NEW.custody_revision_number;
            v_custody_found := FOUND;
            SELECT transaction_type, trade_date, account_id, instrument_id,
                   currency, is_tombstone
            INTO v_disposition
            FROM portfolio.portfolio_daily_transaction_input
            WHERE manifest_id = v_manifest_id
              AND transaction_id = NEW.disposition_transaction_id
              AND revision_id = NEW.disposition_revision_id
              AND revision_number = NEW.disposition_revision_number;
            v_disposition_found := FOUND;
            IF NOT v_acquisition_found OR v_acquisition.is_tombstone
               OR v_acquisition.transaction_type NOT IN (
                    'buy', 'dividend_reinvestment', 'opening_balance', 'transfer_in'
               )
               OR v_acquisition.instrument_id IS DISTINCT FROM NEW.instrument_id
               OR v_acquisition.currency IS DISTINCT FROM NEW.currency
               OR NOT v_custody_found OR v_custody.is_tombstone
               OR v_custody.account_id IS DISTINCT FROM NEW.account_id
               OR v_custody.instrument_id IS DISTINCT FROM NEW.instrument_id
               OR v_custody.currency IS DISTINCT FROM NEW.currency
               OR NOT (
                    (NEW.custody_transaction_id = NEW.acquisition_transaction_id
                        AND NEW.custody_revision_id = NEW.acquisition_revision_id
                        AND NEW.custody_revision_number = NEW.acquisition_revision_number)
                    OR (v_custody.transaction_type = 'transfer_in'
                        AND v_custody.transfer_scope = 'internal_portfolio'
                        AND v_custody.transfer_object_type = 'position')
               )
               OR NOT v_disposition_found OR v_disposition.is_tombstone
               OR v_disposition.account_id IS DISTINCT FROM NEW.account_id
               OR v_disposition.instrument_id IS DISTINCT FROM NEW.instrument_id
               OR v_disposition.currency IS DISTINCT FROM NEW.currency
               OR v_disposition.trade_date IS DISTINCT FROM NEW.disposition_date
               OR v_disposition.transaction_type IS DISTINCT FROM (
                    CASE NEW.disposition_kind
                        WHEN 'sale' THEN 'sell'
                        WHEN 'maturity' THEN 'maturity_redemption'
                        WHEN 'transfer_out' THEN 'transfer_out'
                    END
               ) THEN
                RAISE EXCEPTION 'portfolio_daily_lot_disposition_lineage_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


def _create_lineage_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION portfolio.pd_guard_transaction_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_source record;
            v_mismatch_fields text[];
        BEGIN
            SELECT tr.revision_group_id, rg.recorded_at AS group_recorded_at,
                   tr.revision_kind, tr.is_tombstone,
                   tr.supersedes_revision_id, tr.supersedes_revision_number,
                   tr.payload_schema_version, tr.payload_hash, tr.transaction_type,
                   tr.trade_date, tr.trade_time, tr.trade_at, tr.trade_timezone,
                   tr.trade_time_is_estimated, tr.settlement_date, tr.entitlement_date,
                   tr.acquisition_date, tr.account_id, tr.settlement_cash_account_id,
                   tr.instrument_id,
                   tr.instrument_snapshot_json::jsonb AS instrument_snapshot_json,
                   tr.quantity, tr.price, tr.gross_amount, tr.counter_amount,
                   tr.quoted_fx_rate, tr.fees, tr.taxes,
                   tr.consideration_basis, tr.numeric_scale_state,
                   tr.quantity_input_scale, tr.price_input_scale,
                   tr.gross_amount_input_scale, tr.counter_amount_input_scale,
                   tr.quoted_fx_rate_input_scale, tr.fees_input_scale,
                   tr.taxes_input_scale, tr.currency, tr.transfer_scope,
                   tr.transfer_object_type, tr.transfer_group_id,
                   tr.counterparty_account_id, tr.note
            INTO v_source
            FROM portfolio.transaction_revision_record AS tr
            JOIN portfolio.transaction_revision_group_record AS rg
              ON rg.portfolio_id = tr.portfolio_id
             AND rg.revision_group_id = tr.revision_group_id
            WHERE tr.portfolio_id = NEW.portfolio_id
              AND tr.transaction_id = NEW.transaction_id
              AND tr.revision_number = NEW.revision_number
              AND tr.revision_id = NEW.revision_id
            FOR KEY SHARE OF tr, rg;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'portfolio_daily_transaction_revision_missing'
                    USING ERRCODE = '23503',
                          DETAIL = format(
                              'portfolio_id=%s transaction_id=%s revision_id=%s revision_number=%s',
                              NEW.portfolio_id, NEW.transaction_id,
                              NEW.revision_id, NEW.revision_number
                          );
            END IF;
            v_mismatch_fields := array_remove(ARRAY[
                CASE WHEN v_source.revision_group_id IS DISTINCT FROM NEW.revision_group_id THEN 'revision_group_id' END,
                CASE WHEN v_source.group_recorded_at IS DISTINCT FROM NEW.group_recorded_at THEN 'group_recorded_at' END,
                CASE WHEN v_source.revision_kind IS DISTINCT FROM NEW.revision_kind THEN 'revision_kind' END,
                CASE WHEN v_source.is_tombstone IS DISTINCT FROM NEW.is_tombstone THEN 'is_tombstone' END,
                CASE WHEN v_source.supersedes_revision_id IS DISTINCT FROM NEW.supersedes_revision_id THEN 'supersedes_revision_id' END,
                CASE WHEN v_source.supersedes_revision_number IS DISTINCT FROM NEW.supersedes_revision_number THEN 'supersedes_revision_number' END,
                CASE WHEN v_source.payload_schema_version IS DISTINCT FROM NEW.payload_schema_version THEN 'payload_schema_version' END,
                CASE WHEN v_source.payload_hash IS DISTINCT FROM NEW.payload_hash THEN 'payload_hash' END,
                CASE WHEN v_source.transaction_type IS DISTINCT FROM NEW.transaction_type THEN 'transaction_type' END,
                CASE WHEN v_source.trade_date IS DISTINCT FROM NEW.trade_date THEN 'trade_date' END,
                CASE WHEN v_source.trade_time IS DISTINCT FROM NEW.trade_time THEN 'trade_time' END,
                CASE WHEN v_source.trade_at IS DISTINCT FROM NEW.trade_at THEN 'trade_at' END,
                CASE WHEN v_source.trade_timezone IS DISTINCT FROM NEW.trade_timezone THEN 'trade_timezone' END,
                CASE WHEN v_source.trade_time_is_estimated IS DISTINCT FROM NEW.trade_time_is_estimated THEN 'trade_time_is_estimated' END,
                CASE WHEN v_source.settlement_date IS DISTINCT FROM NEW.settlement_date THEN 'settlement_date' END,
                CASE WHEN v_source.entitlement_date IS DISTINCT FROM NEW.entitlement_date THEN 'entitlement_date' END,
                CASE WHEN v_source.acquisition_date IS DISTINCT FROM NEW.acquisition_date THEN 'acquisition_date' END,
                CASE WHEN v_source.account_id IS DISTINCT FROM NEW.account_id THEN 'account_id' END,
                CASE WHEN v_source.settlement_cash_account_id IS DISTINCT FROM NEW.settlement_cash_account_id THEN 'settlement_cash_account_id' END,
                CASE WHEN v_source.instrument_id IS DISTINCT FROM NEW.instrument_id THEN 'instrument_id' END,
                CASE WHEN v_source.instrument_snapshot_json IS DISTINCT FROM NEW.instrument_snapshot_json THEN 'instrument_snapshot_json' END,
                CASE WHEN v_source.quantity IS DISTINCT FROM NEW.quantity THEN 'quantity' END,
                CASE WHEN v_source.price IS DISTINCT FROM NEW.price THEN 'price' END,
                CASE WHEN v_source.gross_amount IS DISTINCT FROM NEW.gross_amount THEN 'gross_amount' END,
                CASE WHEN v_source.counter_amount IS DISTINCT FROM NEW.counter_amount THEN 'counter_amount' END,
                CASE WHEN v_source.quoted_fx_rate IS DISTINCT FROM NEW.quoted_fx_rate THEN 'quoted_fx_rate' END,
                CASE WHEN v_source.fees IS DISTINCT FROM NEW.fees THEN 'fees' END,
                CASE WHEN v_source.taxes IS DISTINCT FROM NEW.taxes THEN 'taxes' END,
                CASE WHEN v_source.consideration_basis IS DISTINCT FROM NEW.consideration_basis THEN 'consideration_basis' END,
                CASE WHEN v_source.numeric_scale_state IS DISTINCT FROM NEW.numeric_scale_state THEN 'numeric_scale_state' END,
                CASE WHEN v_source.quantity_input_scale IS DISTINCT FROM NEW.quantity_input_scale THEN 'quantity_input_scale' END,
                CASE WHEN v_source.price_input_scale IS DISTINCT FROM NEW.price_input_scale THEN 'price_input_scale' END,
                CASE WHEN v_source.gross_amount_input_scale IS DISTINCT FROM NEW.gross_amount_input_scale THEN 'gross_amount_input_scale' END,
                CASE WHEN v_source.counter_amount_input_scale IS DISTINCT FROM NEW.counter_amount_input_scale THEN 'counter_amount_input_scale' END,
                CASE WHEN v_source.quoted_fx_rate_input_scale IS DISTINCT FROM NEW.quoted_fx_rate_input_scale THEN 'quoted_fx_rate_input_scale' END,
                CASE WHEN v_source.fees_input_scale IS DISTINCT FROM NEW.fees_input_scale THEN 'fees_input_scale' END,
                CASE WHEN v_source.taxes_input_scale IS DISTINCT FROM NEW.taxes_input_scale THEN 'taxes_input_scale' END,
                CASE WHEN v_source.currency IS DISTINCT FROM NEW.currency THEN 'currency' END,
                CASE WHEN v_source.transfer_scope IS DISTINCT FROM NEW.transfer_scope THEN 'transfer_scope' END,
                CASE WHEN v_source.transfer_object_type IS DISTINCT FROM NEW.transfer_object_type THEN 'transfer_object_type' END,
                CASE WHEN v_source.transfer_group_id IS DISTINCT FROM NEW.transfer_group_id THEN 'transfer_group_id' END,
                CASE WHEN v_source.counterparty_account_id IS DISTINCT FROM NEW.counterparty_account_id THEN 'counterparty_account_id' END,
                CASE WHEN v_source.note IS DISTINCT FROM NEW.note THEN 'note' END
            ]::text[], NULL);
            IF cardinality(v_mismatch_fields) > 0 THEN
                RAISE EXCEPTION 'portfolio_daily_transaction_revision_mismatch'
                    USING ERRCODE = '23514',
                          DETAIL = format(
                              'revision_id=%s fields=%s',
                              NEW.revision_id,
                              array_to_string(v_mismatch_fields, ',')
                          );
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_quote_candidate_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_source record;
        BEGIN
            SELECT qs.quote_series_id, qo.observation_id, qo.as_of_date,
                   qr.revision_id, qr.revision_number, qr.value, qr.status,
                   qr.source_published_at, qr.ingested_at, qr.payload_hash
            INTO v_source
            FROM instrument_registry.quote_observation_revision AS qr
            JOIN instrument_registry.quote_observation AS qo
              ON qo.observation_id = qr.observation_id
            JOIN instrument_registry.quote_series AS qs
              ON qs.quote_series_id = qo.quote_series_id
            WHERE qr.revision_id = NEW.revision_id
            FOR KEY SHARE OF qr, qo, qs;
            IF NOT FOUND OR ROW(
                    v_source.quote_series_id, v_source.observation_id,
                    v_source.as_of_date, v_source.revision_number, v_source.value,
                    v_source.status, v_source.source_published_at,
                    v_source.ingested_at, v_source.payload_hash
                ) IS DISTINCT FROM ROW(
                    NEW.quote_series_id, NEW.observation_id,
                    NEW.observation_date, NEW.revision_number, NEW.quote_value,
                    NEW.quote_status, NEW.source_published_at,
                    NEW.ingested_at, NEW.payload_hash
                ) THEN
                RAISE EXCEPTION 'portfolio_daily_quote_revision_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_corp_action_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_source record;
        BEGIN
            SELECT action_type, announcement_date, record_date, effective_date,
                   payable_date, new_units::numeric AS new_units,
                   old_units::numeric AS old_units, quantity_rounding,
                   quantity_precision, cost_basis_treatment, source,
                   external_event_id, status,
                   updated_at::timestamptz AS event_updated_at,
                   jsonb_build_object(
                       'corporate_action_event_id', corporate_action_event_id,
                       'instrument_id', instrument_id,
                       'action_type', action_type,
                       'announcement_date', announcement_date,
                       'record_date', record_date,
                       'effective_date', effective_date,
                       'payable_date', payable_date,
                       'new_units', new_units,
                       'old_units', old_units,
                       'quantity_rounding', quantity_rounding,
                       'quantity_precision', quantity_precision,
                       'cost_basis_treatment', cost_basis_treatment,
                       'source', source,
                       'external_event_id', external_event_id,
                       'status', status,
                       'provenance', provenance_json::jsonb,
                       'created_at', created_at,
                       'updated_at', updated_at
                   ) AS canonical_event
            INTO v_source
            FROM instrument_registry.corporate_action_event
            WHERE corporate_action_event_id = NEW.corporate_action_event_id
              AND instrument_id = NEW.instrument_id
            FOR KEY SHARE;
            IF NOT FOUND OR ROW(
                    v_source.action_type, v_source.announcement_date,
                    v_source.record_date, v_source.effective_date,
                    v_source.payable_date, v_source.new_units,
                    v_source.old_units, v_source.quantity_rounding,
                    v_source.quantity_precision, v_source.cost_basis_treatment,
                    v_source.source, v_source.external_event_id,
                    v_source.status, v_source.event_updated_at,
                    v_source.canonical_event
                ) IS DISTINCT FROM ROW(
                    NEW.action_type, NEW.announcement_date, NEW.record_date,
                    NEW.effective_date, NEW.payable_date, NEW.new_units,
                    NEW.old_units, NEW.quantity_rounding,
                    NEW.quantity_precision, NEW.cost_basis_treatment,
                    NEW.source, NEW.external_event_id, NEW.event_status,
                    NEW.event_updated_at, NEW.canonical_event
                ) THEN
                RAISE EXCEPTION 'portfolio_daily_corporate_action_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_guard_fx_leg_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_source record;
        BEGIN
            IF NEW.leg_resolution_status = 'missing' THEN
                RETURN NEW;
            END IF;
            SELECT qs.quote_series_id, qo.observation_id, qo.as_of_date,
                   qr.revision_number, qr.value, qr.status,
                   qr.source_published_at, qr.ingested_at, qr.payload_hash
            INTO v_source
            FROM instrument_registry.quote_observation_revision AS qr
            JOIN instrument_registry.quote_observation AS qo
              ON qo.observation_id = qr.observation_id
            JOIN instrument_registry.quote_series AS qs
              ON qs.quote_series_id = qo.quote_series_id
            WHERE qr.revision_id = NEW.revision_id
            FOR KEY SHARE OF qr, qo, qs;
            IF NOT FOUND OR ROW(
                    v_source.quote_series_id, v_source.observation_id,
                    v_source.as_of_date, v_source.revision_number, v_source.value,
                    v_source.status, v_source.source_published_at,
                    v_source.ingested_at, v_source.payload_hash
                ) IS DISTINCT FROM ROW(
                    NEW.quote_series_id, NEW.observation_id,
                    NEW.observation_date, NEW.revision_number, NEW.quoted_rate,
                    NEW.quote_status, NEW.source_published_at,
                    NEW.ingested_at, NEW.payload_hash
                ) THEN
                RAISE EXCEPTION 'portfolio_daily_fx_leg_revision_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.leg_resolution_status = 'resolved'
               AND ((NOT NEW.is_inverted
                        AND NEW.effective_rate IS DISTINCT FROM NEW.quoted_rate)
                    OR (NEW.is_inverted
                        AND NEW.effective_rate IS DISTINCT FROM
                            calculation_registry.divide_significant_half_even(
                                1,
                                NEW.quoted_rate,
                                NEW.rate_math_precision
                            ))
                    OR NEW.rate_derivation_residual_exact IS DISTINCT FROM
                       (CASE WHEN NEW.is_inverted
                             THEN NEW.effective_rate * NEW.quoted_rate - 1
                             ELSE 0 END)) THEN
                RAISE EXCEPTION 'portfolio_daily_fx_leg_effective_rate_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


def _create_remaining_output_tables() -> None:
    op.execute(
        """
        CREATE TABLE portfolio.portfolio_daily_balance_output (
            run_id uuid NOT NULL,
            output_fencing_token bigint NOT NULL,
            worker_id varchar(255) NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            calculated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            as_of_date date NOT NULL,
            account_id varchar(255) NOT NULL,
            component_type varchar(32) NOT NULL,
            component_key varchar(255) NOT NULL,
            currency varchar(3) NOT NULL,
            measured_base_amount boolean NOT NULL,
            local_amount numeric(50,8) NOT NULL,
            adopted_fx_rate_exact numeric,
            fx_rate_to_base numeric(50,18),
            base_amount_exact numeric,
            base_amount numeric(50,8),
            base_rounding_adjustment numeric(50,8) NOT NULL,
            coverage_state varchar(16) NOT NULL,
            reason_codes varchar(64)[] NOT NULL DEFAULT ARRAY[]::varchar(64)[],
            CONSTRAINT pk_pd_balance_output PRIMARY KEY (
                run_id, output_fencing_token, as_of_date, account_id,
                component_type, component_key, currency
            ),
            CONSTRAINT fk_pd_balance_output_run FOREIGN KEY (run_id)
                REFERENCES calculation_registry.calculation_run(run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_balance_output_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_balance_token CHECK (output_fencing_token > 0),
            CONSTRAINT ck_pd_balance_component CHECK (
                component_type IN ('settled_cash', 'pending_receivable', 'pending_payable',
                    'income_accrual', 'fee_accrual', 'tax_accrual', 'other_accrual')
            ),
            CONSTRAINT ck_pd_balance_currency CHECK (currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_pd_balance_rounding CHECK (abs(base_rounding_adjustment) <= 0.00000001),
            CONSTRAINT ck_pd_balance_measured CHECK (
                (measured_base_amount
                    AND adopted_fx_rate_exact IS NOT NULL
                    AND fx_rate_to_base IS NOT NULL
                    AND base_amount_exact IS NOT NULL AND base_amount IS NOT NULL
                    AND adopted_fx_rate_exact > 0
                    AND adopted_fx_rate_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND fx_rate_to_base = calculation_registry.round_half_even(adopted_fx_rate_exact, 18)
                    AND base_amount_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND base_amount_exact = local_amount * adopted_fx_rate_exact
                    AND base_amount = calculation_registry.round_half_even(base_amount_exact, 8) + base_rounding_adjustment)
                OR (NOT measured_base_amount AND adopted_fx_rate_exact IS NULL
                    AND fx_rate_to_base IS NULL AND base_amount_exact IS NULL
                    AND base_amount IS NULL AND base_rounding_adjustment = 0)
            ),
            CONSTRAINT ck_pd_balance_coverage CHECK (
                coverage_state IN ('complete', 'partial', 'unavailable')
            ),
            CONSTRAINT ck_pd_balance_reasons CHECK (
                (coverage_state = 'complete' AND cardinality(reason_codes) = 0)
                OR (coverage_state <> 'complete' AND cardinality(reason_codes) > 0)
            ),
            CONSTRAINT ck_pd_balance_measured_coverage CHECK (
                measured_base_amount = (coverage_state = 'complete')
            )
        );

        CREATE TABLE portfolio.portfolio_daily_lot_output (
            run_id uuid NOT NULL,
            output_fencing_token bigint NOT NULL,
            worker_id varchar(255) NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            calculated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            as_of_date date NOT NULL,
            account_id varchar(255) NOT NULL,
            instrument_id varchar(255) NOT NULL,
            lot_id varchar(255) NOT NULL,
            source_transaction_id varchar(255) NOT NULL,
            source_revision_id varchar(255) NOT NULL,
            source_revision_number integer NOT NULL,
            custody_transaction_id varchar(255) NOT NULL,
            custody_revision_id varchar(255) NOT NULL,
            custody_revision_number integer NOT NULL,
            acquisition_date date NOT NULL,
            currency varchar(3) NOT NULL,
            open_quantity_exact numeric NOT NULL,
            open_quantity numeric(50,12) NOT NULL,
            measured_base_cost boolean NOT NULL,
            acquisition_fx_rate_exact numeric,
            acquisition_fx_rate numeric(50,18),
            cost_basis_local_exact numeric NOT NULL,
            cost_basis_local numeric(50,8) NOT NULL,
            unit_cost_local numeric(50,12) NOT NULL,
            unit_cost_local_rounding_residual_exact numeric NOT NULL,
            cost_basis_base_exact numeric,
            cost_basis_base numeric(50,8),
            unit_cost_base numeric(50,12),
            unit_cost_base_rounding_residual_exact numeric,
            local_cost_rounding_adjustment numeric(50,8) NOT NULL,
            base_cost_rounding_adjustment numeric(50,8),
            base_cost_coverage_state varchar(16) NOT NULL,
            base_cost_reason_codes varchar(64)[] NOT NULL,
            CONSTRAINT pk_pd_lot_output PRIMARY KEY (
                run_id, output_fencing_token, as_of_date, account_id, instrument_id, lot_id
            ),
            CONSTRAINT fk_pd_lot_output_run FOREIGN KEY (run_id)
                REFERENCES calculation_registry.calculation_run(run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_lot_output_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_lot_token CHECK (output_fencing_token > 0),
            CONSTRAINT ck_pd_lot_currency CHECK (currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_pd_lot_source_revision CHECK (
                source_revision_number > 0 AND custody_revision_number > 0
            ),
            CONSTRAINT ck_pd_lot_quantity CHECK (
                open_quantity_exact > 0
                AND open_quantity_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND open_quantity = calculation_registry.round_half_even(open_quantity_exact, 12)
            ),
            CONSTRAINT ck_pd_lot_unit_cost_local CHECK (
                unit_cost_local >= 0
                AND unit_cost_local_rounding_residual_exact::text
                    NOT IN ('NaN', 'Infinity', '-Infinity')
                AND unit_cost_local_rounding_residual_exact
                    = cost_basis_local_exact - open_quantity_exact * unit_cost_local
            ),
            CONSTRAINT ck_pd_lot_cost_bridge_local CHECK (
                cost_basis_local_exact >= 0
                AND cost_basis_local_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND cost_basis_local = calculation_registry.round_half_even(cost_basis_local_exact, 8)
                    + local_cost_rounding_adjustment
            ),
            CONSTRAINT ck_pd_lot_local_rounding CHECK (
                abs(local_cost_rounding_adjustment) <= 0.00000001
            ),
            CONSTRAINT ck_pd_lot_base_coverage CHECK (
                base_cost_coverage_state IN ('complete', 'unavailable')
                AND measured_base_cost = (base_cost_coverage_state = 'complete')
                AND ((base_cost_coverage_state = 'complete')
                    = (cardinality(base_cost_reason_codes) = 0))
            ),
            CONSTRAINT ck_pd_lot_base_cost CHECK (
                ((acquisition_fx_rate_exact IS NULL AND acquisition_fx_rate IS NULL)
                    OR (acquisition_fx_rate_exact IS NOT NULL
                        AND acquisition_fx_rate IS NOT NULL
                        AND acquisition_fx_rate_exact > 0
                        AND acquisition_fx_rate_exact::text
                            NOT IN ('NaN', 'Infinity', '-Infinity')
                        AND acquisition_fx_rate = calculation_registry.round_half_even(acquisition_fx_rate_exact, 18)))
                AND ((measured_base_cost
                    AND cost_basis_base_exact IS NOT NULL
                    AND cost_basis_base IS NOT NULL
                    AND unit_cost_base IS NOT NULL
                    AND unit_cost_base_rounding_residual_exact IS NOT NULL
                    AND base_cost_rounding_adjustment IS NOT NULL
                    AND cost_basis_base_exact >= 0
                    AND cost_basis_base_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND unit_cost_base >= 0
                    AND unit_cost_base_rounding_residual_exact::text
                        NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND unit_cost_base_rounding_residual_exact
                        = cost_basis_base_exact - open_quantity_exact * unit_cost_base
                    AND cost_basis_base = calculation_registry.round_half_even(cost_basis_base_exact, 8)
                        + base_cost_rounding_adjustment
                    AND abs(base_cost_rounding_adjustment) <= 0.00000001)
                OR (NOT measured_base_cost
                    AND acquisition_fx_rate_exact IS NULL AND acquisition_fx_rate IS NULL
                    AND cost_basis_base_exact IS NULL AND cost_basis_base IS NULL
                    AND unit_cost_base IS NULL
                    AND unit_cost_base_rounding_residual_exact IS NULL
                    AND base_cost_rounding_adjustment IS NULL))
            )
        );

        CREATE TABLE portfolio.portfolio_daily_lot_disposition_output (
            run_id uuid NOT NULL,
            output_fencing_token bigint NOT NULL,
            worker_id varchar(255) NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            calculated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            as_of_date date NOT NULL,
            account_id varchar(255) NOT NULL,
            instrument_id varchar(255) NOT NULL,
            lot_id varchar(255) NOT NULL,
            acquisition_transaction_id varchar(255) NOT NULL,
            acquisition_revision_id varchar(255) NOT NULL,
            acquisition_revision_number integer NOT NULL,
            custody_transaction_id varchar(255) NOT NULL,
            custody_revision_id varchar(255) NOT NULL,
            custody_revision_number integer NOT NULL,
            match_sequence integer NOT NULL,
            disposition_transaction_id varchar(255) NOT NULL,
            disposition_revision_id varchar(255) NOT NULL,
            disposition_revision_number integer NOT NULL,
            disposition_date date NOT NULL,
            disposition_kind varchar(32) NOT NULL,
            matching_method varchar(32) NOT NULL,
            matching_policy_version varchar(64) NOT NULL,
            currency varchar(3) NOT NULL,
            disposed_quantity_exact numeric NOT NULL,
            disposed_quantity numeric(50,12) NOT NULL,
            proceeds_local_exact numeric NOT NULL,
            proceeds_local numeric(50,8) NOT NULL,
            allocated_cost_local_exact numeric NOT NULL,
            allocated_cost_local numeric(50,8) NOT NULL,
            realized_pnl_local_exact numeric NOT NULL,
            realized_pnl_local numeric(50,8) NOT NULL,
            proceeds_local_rounding_adjustment numeric(50,8) NOT NULL,
            allocated_cost_local_rounding_adjustment numeric(50,8) NOT NULL,
            realized_pnl_local_rounding_adjustment numeric(50,8) NOT NULL,
            measured_base_pnl boolean NOT NULL,
            disposition_fx_rate_exact numeric,
            disposition_fx_rate numeric(50,18),
            proceeds_base_exact numeric,
            proceeds_base numeric(50,8),
            allocated_cost_base_exact numeric,
            allocated_cost_base numeric(50,8),
            realized_pnl_base_exact numeric,
            realized_pnl_base numeric(50,8),
            proceeds_base_rounding_adjustment numeric(50,8),
            allocated_cost_base_rounding_adjustment numeric(50,8),
            realized_pnl_base_rounding_adjustment numeric(50,8),
            base_pnl_coverage_state varchar(16) NOT NULL,
            base_pnl_reason_codes varchar(64)[] NOT NULL,
            CONSTRAINT pk_pd_lot_disposition_output PRIMARY KEY (
                run_id, output_fencing_token, as_of_date, account_id,
                instrument_id, lot_id, disposition_transaction_id, match_sequence
            ),
            CONSTRAINT fk_pd_lot_disposition_run FOREIGN KEY (run_id)
                REFERENCES calculation_registry.calculation_run(run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_lot_disposition_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_lot_disposition_token CHECK (output_fencing_token > 0),
            CONSTRAINT ck_pd_lot_disposition_currency CHECK (currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_pd_lot_disposition_sequence_date CHECK (
                match_sequence > 0 AND acquisition_revision_number > 0
                AND custody_revision_number > 0 AND disposition_revision_number > 0
                AND disposition_date <= as_of_date
            ),
            CONSTRAINT ck_pd_lot_disposition_kind CHECK (
                disposition_kind IN ('sale', 'maturity', 'transfer_out')
            ),
            CONSTRAINT ck_pd_lot_disposition_matching CHECK (
                matching_method IN ('fifo', 'moving_average', 'specific_identification')
            ),
            CONSTRAINT ck_pd_lot_disposition_quantity CHECK (
                disposed_quantity_exact > 0
                AND disposed_quantity_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND disposed_quantity = calculation_registry.round_half_even(disposed_quantity_exact, 12)
            ),
            CONSTRAINT ck_pd_lot_disposition_local_exact CHECK (
                proceeds_local_exact >= 0 AND allocated_cost_local_exact >= 0
                AND proceeds_local_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND allocated_cost_local_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND realized_pnl_local_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND realized_pnl_local_exact = proceeds_local_exact - allocated_cost_local_exact
            ),
            CONSTRAINT ck_pd_lot_disposition_local_published CHECK (
                proceeds_local = calculation_registry.round_half_even(proceeds_local_exact, 8)
                    + proceeds_local_rounding_adjustment
                AND allocated_cost_local = calculation_registry.round_half_even(allocated_cost_local_exact, 8)
                    + allocated_cost_local_rounding_adjustment
                AND realized_pnl_local = calculation_registry.round_half_even(realized_pnl_local_exact, 8)
                    + realized_pnl_local_rounding_adjustment
                AND realized_pnl_local = proceeds_local - allocated_cost_local
                AND abs(proceeds_local_rounding_adjustment) <= 0.00000001
                AND abs(allocated_cost_local_rounding_adjustment) <= 0.00000001
                AND abs(realized_pnl_local_rounding_adjustment) <= 0.00000003
            ),
            CONSTRAINT ck_pd_lot_disposition_base_coverage CHECK (
                base_pnl_coverage_state IN ('complete', 'unavailable')
                AND measured_base_pnl = (base_pnl_coverage_state = 'complete')
                AND ((base_pnl_coverage_state = 'complete')
                    = (cardinality(base_pnl_reason_codes) = 0))
            ),
            CONSTRAINT ck_pd_lot_disposition_base_pnl CHECK (
                (measured_base_pnl
                    AND disposition_fx_rate_exact IS NOT NULL
                    AND disposition_fx_rate IS NOT NULL
                    AND proceeds_base_exact IS NOT NULL AND proceeds_base IS NOT NULL
                    AND allocated_cost_base_exact IS NOT NULL
                    AND allocated_cost_base IS NOT NULL
                    AND realized_pnl_base_exact IS NOT NULL
                    AND realized_pnl_base IS NOT NULL
                    AND proceeds_base_rounding_adjustment IS NOT NULL
                    AND allocated_cost_base_rounding_adjustment IS NOT NULL
                    AND realized_pnl_base_rounding_adjustment IS NOT NULL
                    AND disposition_fx_rate_exact > 0
                    AND disposition_fx_rate_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND disposition_fx_rate = calculation_registry.round_half_even(disposition_fx_rate_exact, 18)
                    AND proceeds_base_exact = proceeds_local_exact * disposition_fx_rate_exact
                    AND allocated_cost_base_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND realized_pnl_base_exact = proceeds_base_exact - allocated_cost_base_exact
                    AND proceeds_base = calculation_registry.round_half_even(proceeds_base_exact, 8)
                        + proceeds_base_rounding_adjustment
                    AND allocated_cost_base = calculation_registry.round_half_even(allocated_cost_base_exact, 8)
                        + allocated_cost_base_rounding_adjustment
                    AND realized_pnl_base = calculation_registry.round_half_even(realized_pnl_base_exact, 8)
                        + realized_pnl_base_rounding_adjustment
                    AND realized_pnl_base = proceeds_base - allocated_cost_base
                    AND abs(proceeds_base_rounding_adjustment) <= 0.00000001
                    AND abs(allocated_cost_base_rounding_adjustment) <= 0.00000001
                    AND abs(realized_pnl_base_rounding_adjustment) <= 0.00000003)
                OR (NOT measured_base_pnl
                    AND disposition_fx_rate_exact IS NULL AND disposition_fx_rate IS NULL
                    AND proceeds_base_exact IS NULL AND proceeds_base IS NULL
                    AND allocated_cost_base_exact IS NULL AND allocated_cost_base IS NULL
                    AND realized_pnl_base_exact IS NULL AND realized_pnl_base IS NULL
                    AND proceeds_base_rounding_adjustment IS NULL
                    AND allocated_cost_base_rounding_adjustment IS NULL
                    AND realized_pnl_base_rounding_adjustment IS NULL)
            )
        );

        CREATE TABLE portfolio.portfolio_daily_contribution_output (
            run_id uuid NOT NULL,
            output_fencing_token bigint NOT NULL,
            worker_id varchar(255) NOT NULL,
            portfolio_id varchar(255) NOT NULL,
            calculated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            as_of_date date NOT NULL,
            axis varchar(32) NOT NULL,
            group_key varchar(255) NOT NULL,
            group_label varchar(500) NOT NULL,
            measured boolean NOT NULL,
            opening_nav_exact numeric,
            opening_nav numeric(50,8),
            opening_nav_rounding_adjustment numeric(50,8),
            closing_nav_exact numeric,
            closing_nav numeric(50,8),
            closing_nav_rounding_adjustment numeric(50,8),
            external_flow_in_exact numeric,
            external_flow_in numeric(50,8),
            external_flow_in_rounding_adjustment numeric(50,8),
            external_flow_out_exact numeric,
            external_flow_out numeric(50,8),
            external_flow_out_rounding_adjustment numeric(50,8),
            internal_flow_in_exact numeric,
            internal_flow_in numeric(50,8),
            internal_flow_in_rounding_adjustment numeric(50,8),
            internal_flow_out_exact numeric,
            internal_flow_out numeric(50,8),
            internal_flow_out_rounding_adjustment numeric(50,8),
            economic_pnl_exact numeric,
            economic_pnl numeric(50,8),
            economic_pnl_rounding_adjustment numeric(50,8),
            contribution_method50 numeric,
            contribution_published numeric(50,18),
            contribution_division_adjustment_exact numeric NOT NULL,
            contribution_rounding_adjustment numeric(50,18),
            closure_residual_exact numeric NOT NULL,
            rounding_adjustment_base numeric(50,8) NOT NULL,
            coverage_state varchar(16) NOT NULL,
            reason_codes varchar(64)[] NOT NULL DEFAULT ARRAY[]::varchar(64)[],
            CONSTRAINT pk_pd_contribution_output PRIMARY KEY (
                run_id, output_fencing_token, as_of_date, axis, group_key
            ),
            CONSTRAINT fk_pd_contribution_output_run FOREIGN KEY (run_id)
                REFERENCES calculation_registry.calculation_run(run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_pd_contribution_output_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE RESTRICT,
            CONSTRAINT ck_pd_contrib_token CHECK (output_fencing_token > 0),
            CONSTRAINT ck_pd_contrib_axis CHECK (axis IN ('portfolio', 'account', 'instrument', 'currency', 'taxonomy')),
            CONSTRAINT ck_pd_contrib_flows CHECK (
                (external_flow_in_exact IS NULL OR external_flow_in_exact >= 0)
                AND (external_flow_out_exact IS NULL OR external_flow_out_exact >= 0)
                AND (internal_flow_in_exact IS NULL OR internal_flow_in_exact >= 0)
                AND (internal_flow_out_exact IS NULL OR internal_flow_out_exact >= 0)
                AND (external_flow_in IS NULL OR external_flow_in >= 0)
                AND (external_flow_out IS NULL OR external_flow_out >= 0)
                AND (internal_flow_in IS NULL OR internal_flow_in >= 0)
                AND (internal_flow_out IS NULL OR internal_flow_out >= 0)
            ),
            CONSTRAINT ck_pd_contrib_rounding CHECK (
                abs(rounding_adjustment_base) <= 0.00000011
            ),
            CONSTRAINT ck_pd_contrib_closure CHECK (
                closure_residual_exact = 0
                AND contribution_division_adjustment_exact::text
                    NOT IN ('NaN', 'Infinity', '-Infinity')
            ),
            CONSTRAINT ck_pd_contrib_method_storage_domain CHECK (
                contribution_method50 IS NULL OR (
                    contribution_method50::text
                        NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND abs(contribution_method50) < 1e32
                    AND contribution_method50 = trunc(contribution_method50, 100)
                    AND contribution_method50
                        = calculation_registry.round_significant_half_even(
                            contribution_method50,
                            50
                        )
                )
            ),
            CONSTRAINT ck_pd_contrib_bridge CHECK (
                (measured
                    AND opening_nav_exact IS NOT NULL AND closing_nav_exact IS NOT NULL
                    AND external_flow_in_exact IS NOT NULL
                    AND external_flow_out_exact IS NOT NULL
                    AND internal_flow_in_exact IS NOT NULL
                    AND internal_flow_out_exact IS NOT NULL
                    AND economic_pnl_exact IS NOT NULL AND contribution_method50 IS NOT NULL
                    AND opening_nav IS NOT NULL AND closing_nav IS NOT NULL
                    AND economic_pnl IS NOT NULL AND contribution_published IS NOT NULL
                    AND external_flow_in IS NOT NULL AND external_flow_out IS NOT NULL
                    AND internal_flow_in IS NOT NULL AND internal_flow_out IS NOT NULL
                    AND opening_nav_rounding_adjustment IS NOT NULL
                    AND closing_nav_rounding_adjustment IS NOT NULL
                    AND external_flow_in_rounding_adjustment IS NOT NULL
                    AND external_flow_out_rounding_adjustment IS NOT NULL
                    AND internal_flow_in_rounding_adjustment IS NOT NULL
                    AND internal_flow_out_rounding_adjustment IS NOT NULL
                    AND economic_pnl_rounding_adjustment IS NOT NULL
                    AND contribution_rounding_adjustment IS NOT NULL
                    AND closing_nav_exact + external_flow_out_exact + internal_flow_out_exact
                        = opening_nav_exact + external_flow_in_exact + internal_flow_in_exact
                            + economic_pnl_exact
                    AND opening_nav = calculation_registry.round_half_even(opening_nav_exact, 8)
                        + opening_nav_rounding_adjustment
                    AND closing_nav = calculation_registry.round_half_even(closing_nav_exact, 8)
                        + closing_nav_rounding_adjustment
                    AND external_flow_in = calculation_registry.round_half_even(external_flow_in_exact, 8)
                        + external_flow_in_rounding_adjustment
                    AND external_flow_out = calculation_registry.round_half_even(external_flow_out_exact, 8)
                        + external_flow_out_rounding_adjustment
                    AND internal_flow_in = calculation_registry.round_half_even(internal_flow_in_exact, 8)
                        + internal_flow_in_rounding_adjustment
                    AND internal_flow_out = calculation_registry.round_half_even(internal_flow_out_exact, 8)
                        + internal_flow_out_rounding_adjustment
                    AND economic_pnl = calculation_registry.round_half_even(economic_pnl_exact, 8)
                        + economic_pnl_rounding_adjustment
                    AND contribution_published = calculation_registry.round_half_even(
                        contribution_method50 + contribution_division_adjustment_exact,
                        18
                    )
                        + contribution_rounding_adjustment
                    AND abs(opening_nav_rounding_adjustment) <= 0.00000001
                    AND abs(closing_nav_rounding_adjustment) <= 0.00000001
                    AND abs(external_flow_in_rounding_adjustment) <= 0.00000001
                    AND abs(external_flow_out_rounding_adjustment) <= 0.00000001
                    AND abs(internal_flow_in_rounding_adjustment) <= 0.00000001
                    AND abs(internal_flow_out_rounding_adjustment) <= 0.00000001
                    AND abs(economic_pnl_rounding_adjustment) <= 0.00000001
                    AND abs(contribution_rounding_adjustment) <= 0.000000000000000001
                    AND closing_nav + external_flow_out + internal_flow_out
                        = opening_nav + external_flow_in + internal_flow_in
                            + economic_pnl + rounding_adjustment_base)
                OR (NOT measured
                    AND opening_nav_exact IS NULL AND opening_nav IS NULL
                    AND opening_nav_rounding_adjustment IS NULL
                    AND closing_nav_exact IS NULL AND closing_nav IS NULL
                    AND closing_nav_rounding_adjustment IS NULL
                    AND external_flow_in_exact IS NULL AND external_flow_in IS NULL
                    AND external_flow_in_rounding_adjustment IS NULL
                    AND external_flow_out_exact IS NULL AND external_flow_out IS NULL
                    AND external_flow_out_rounding_adjustment IS NULL
                    AND internal_flow_in_exact IS NULL AND internal_flow_in IS NULL
                    AND internal_flow_in_rounding_adjustment IS NULL
                    AND internal_flow_out_exact IS NULL AND internal_flow_out IS NULL
                    AND internal_flow_out_rounding_adjustment IS NULL
                    AND economic_pnl_exact IS NULL AND economic_pnl IS NULL
                    AND economic_pnl_rounding_adjustment IS NULL
                    AND contribution_method50 IS NULL AND contribution_published IS NULL
                    AND contribution_rounding_adjustment IS NULL
                    AND contribution_division_adjustment_exact = 0
                    AND rounding_adjustment_base = 0)
            ),
            CONSTRAINT ck_pd_contrib_coverage CHECK (
                coverage_state IN ('complete', 'partial', 'unavailable')
            ),
            CONSTRAINT ck_pd_contrib_reasons CHECK (
                (coverage_state = 'complete' AND cardinality(reason_codes) = 0)
                OR (coverage_state <> 'complete' AND cardinality(reason_codes) > 0)
            )
        );

        CREATE INDEX ix_pd_snapshot_portfolio_date
            ON portfolio.portfolio_daily_snapshot_output(portfolio_id, as_of_date);
        CREATE INDEX ix_pd_holding_portfolio_date
            ON portfolio.portfolio_daily_holding_output(portfolio_id, as_of_date);
        CREATE INDEX ix_pd_balance_portfolio_date
            ON portfolio.portfolio_daily_balance_output(portfolio_id, as_of_date);
        CREATE INDEX ix_pd_lot_portfolio_date
            ON portfolio.portfolio_daily_lot_output(portfolio_id, as_of_date);
        CREATE INDEX ix_pd_lot_disposition_portfolio_date
            ON portfolio.portfolio_daily_lot_disposition_output(portfolio_id, as_of_date);
        CREATE INDEX ix_pd_contrib_portfolio_date
            ON portfolio.portfolio_daily_contribution_output(portfolio_id, as_of_date);
        """
    )


def _create_manifest_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION portfolio.pd_validate_manifest_seal()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_run record;
            v_config record;
            v_counts jsonb;
            v_config_count bigint;
            v_account_count bigint;
            v_transaction_count bigint;
            v_instrument_count bigint;
            v_corp_window_count bigint;
            v_corp_input_count bigint;
            v_quote_window_count bigint;
            v_quote_candidate_count bigint;
            v_fx_path_count bigint;
            v_fx_leg_count bigint;
            v_prior_count bigint;
            v_path record;
            v_leg record;
            v_rate numeric;
            v_currency varchar(3);
            v_seen integer;
            v_all_legs_resolved boolean;
        BEGIN
            IF OLD.status <> 'building' OR NEW.status <> 'sealed' THEN
                RETURN NEW;
            END IF;

            SELECT calculation_kind, scope_kind, scope_id, effective_as_of,
                   cutoff_at, methodology_version, input_schema_version,
                   output_schema_version
            INTO v_run
            FROM calculation_registry.calculation_run
            WHERE run_id = NEW.run_id
            FOR UPDATE;
            IF NOT FOUND OR v_run.calculation_kind <> 'portfolio_daily'
               OR v_run.scope_kind <> 'portfolio'
               OR NEW.schema_version <> 'portfolio-daily-input.v1'
               OR v_run.input_schema_version <> 'portfolio-daily-input.v1'
               OR v_run.output_schema_version <> 'portfolio-daily-output.v1'
               OR v_run.methodology_version <> 'portfolio-daily.exact.v1' THEN
                RAISE EXCEPTION 'portfolio_daily_manifest_contract_mismatch'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO v_config_count
            FROM portfolio.portfolio_daily_config_input WHERE manifest_id = NEW.manifest_id;
            SELECT count(*) INTO v_account_count
            FROM portfolio.portfolio_daily_account_input WHERE manifest_id = NEW.manifest_id;
            SELECT count(*) INTO v_transaction_count
            FROM portfolio.portfolio_daily_transaction_input WHERE manifest_id = NEW.manifest_id;
            SELECT count(*) INTO v_instrument_count
            FROM portfolio.portfolio_daily_instrument_input WHERE manifest_id = NEW.manifest_id;
            SELECT count(*) INTO v_corp_window_count
            FROM portfolio.portfolio_daily_corp_action_window WHERE manifest_id = NEW.manifest_id;
            SELECT count(*) INTO v_corp_input_count
            FROM portfolio.portfolio_daily_corp_action_input WHERE manifest_id = NEW.manifest_id;
            SELECT count(*) INTO v_quote_window_count
            FROM portfolio.portfolio_daily_quote_window WHERE manifest_id = NEW.manifest_id;
            SELECT count(*) INTO v_quote_candidate_count
            FROM portfolio.portfolio_daily_quote_candidate WHERE manifest_id = NEW.manifest_id;
            SELECT count(*) INTO v_fx_path_count
            FROM portfolio.portfolio_daily_fx_path WHERE manifest_id = NEW.manifest_id;
            SELECT count(*) INTO v_fx_leg_count
            FROM portfolio.portfolio_daily_fx_leg WHERE manifest_id = NEW.manifest_id;
            SELECT count(*) INTO v_prior_count
            FROM portfolio.portfolio_daily_prior_publication WHERE manifest_id = NEW.manifest_id;

            v_counts := jsonb_build_object(
                'portfolio_config', v_config_count,
                'account', v_account_count,
                'transaction_revision', v_transaction_count,
                'instrument_snapshot', v_instrument_count,
                'corporate_action_window', v_corp_window_count,
                'corporate_action_snapshot', v_corp_input_count,
                'quote_window', v_quote_window_count,
                'quote_candidate', v_quote_candidate_count,
                'fx_path', v_fx_path_count,
                'fx_leg', v_fx_leg_count,
                'prior_publication', v_prior_count
            );
            IF NEW.dependency_counts IS DISTINCT FROM v_counts THEN
                RAISE EXCEPTION 'portfolio_daily_dependency_counts_mismatch: expected=%, actual=%',
                    NEW.dependency_counts, v_counts USING ERRCODE = '23514';
            END IF;
            IF v_config_count <> 1 OR v_account_count < 1 OR v_prior_count <> 0 THEN
                RAISE EXCEPTION 'portfolio_daily_required_dependency_cardinality'
                    USING ERRCODE = '23514';
            END IF;

            SELECT * INTO v_config
            FROM portfolio.portfolio_daily_config_input
            WHERE manifest_id = NEW.manifest_id;
            IF v_config.run_id <> NEW.run_id
               OR v_config.portfolio_id <> v_run.scope_id
               OR v_config.effective_as_of <> v_run.effective_as_of
               OR v_config.range_start > v_config.effective_as_of
               OR v_config.knowledge_cutoff_at <> v_run.cutoff_at
               OR v_config.config_schema_version <> 'portfolio-daily-config.v1' THEN
                RAISE EXCEPTION 'portfolio_daily_config_run_mismatch'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM portfolio.portfolio_daily_instrument_input AS ii
                CROSS JOIN LATERAL generate_series(
                    v_config.range_start,
                    v_config.effective_as_of,
                    interval '1 day'
                ) AS required_day
                LEFT JOIN portfolio.portfolio_daily_quote_window AS qw
                  ON qw.manifest_id = ii.manifest_id
                 AND qw.instrument_id = ii.instrument_id
                 AND qw.quote_role = 'valuation'
                 AND qw.valuation_date = required_day::date
                WHERE ii.manifest_id = NEW.manifest_id
                  AND ii.requires_valuation
                  AND qw.quote_window_id IS NULL
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_required_quote_window_missing'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                WITH required_currency AS (
                    SELECT ai.currency
                    FROM portfolio.portfolio_daily_account_input AS ai
                    WHERE ai.manifest_id = NEW.manifest_id
                    UNION
                    SELECT ti.currency
                    FROM portfolio.portfolio_daily_transaction_input AS ti
                    WHERE ti.manifest_id = NEW.manifest_id
                      AND ti.currency IS NOT NULL
                    UNION
                    SELECT ii.currency
                    FROM portfolio.portfolio_daily_instrument_input AS ii
                    WHERE ii.manifest_id = NEW.manifest_id
                    UNION
                    SELECT v_config.base_currency
                ), required_date AS (
                    SELECT required_day::date AS valuation_date
                    FROM generate_series(
                        v_config.range_start,
                        v_config.effective_as_of,
                        interval '1 day'
                    ) AS required_day
                    UNION
                    SELECT ti.acquisition_date
                    FROM portfolio.portfolio_daily_transaction_input AS ti
                    WHERE ti.manifest_id = NEW.manifest_id
                      AND NOT ti.is_tombstone
                      AND ti.acquisition_date IS NOT NULL
                      AND ti.acquisition_date <= v_config.effective_as_of
                ), required_path AS (
                    SELECT currency, required_date.valuation_date
                    FROM required_currency
                    CROSS JOIN required_date
                )
                SELECT 1
                FROM required_path AS required
                LEFT JOIN portfolio.portfolio_daily_fx_path AS fp
                  ON fp.manifest_id = NEW.manifest_id
                 AND fp.valuation_date = required.valuation_date
                 AND fp.from_currency = required.currency
                 AND fp.to_currency = v_config.base_currency
                WHERE fp.fx_path_id IS NULL
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_required_fx_path_missing'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM portfolio.portfolio_daily_transaction_input AS ti
                JOIN portfolio.transaction_revision_record AS tr
                  ON tr.portfolio_id = ti.portfolio_id
                 AND tr.transaction_id = ti.transaction_id
                 AND tr.revision_number = ti.revision_number
                 AND tr.revision_id = ti.revision_id
                LEFT JOIN portfolio.portfolio_daily_account_input AS ai
                  ON ai.manifest_id = ti.manifest_id
                 AND ai.account_id = tr.account_id
                LEFT JOIN portfolio.portfolio_daily_account_input AS sai
                  ON sai.manifest_id = ti.manifest_id
                 AND sai.account_id = tr.settlement_cash_account_id
                LEFT JOIN portfolio.portfolio_daily_account_input AS cai
                  ON cai.manifest_id = ti.manifest_id
                 AND cai.account_id = tr.counterparty_account_id
                LEFT JOIN portfolio.portfolio_daily_instrument_input AS ii
                  ON ii.manifest_id = ti.manifest_id
                 AND ii.instrument_id = tr.instrument_id
                WHERE ti.manifest_id = NEW.manifest_id
                  AND ((tr.account_id IS NOT NULL AND ai.account_id IS NULL)
                    OR (tr.settlement_cash_account_id IS NOT NULL
                        AND sai.account_id IS NULL)
                    OR (tr.counterparty_account_id IS NOT NULL
                        AND cai.account_id IS NULL)
                    OR (tr.instrument_id IS NOT NULL AND ii.instrument_id IS NULL))
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_transaction_reference_not_snapshotted'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                WITH eligible AS (
                    SELECT tr.transaction_id, max(tr.revision_number) AS revision_number
                    FROM portfolio.transaction_revision_record AS tr
                    JOIN portfolio.transaction_revision_group_record AS rg
                      ON rg.portfolio_id = tr.portfolio_id
                     AND rg.revision_group_id = tr.revision_group_id
                    WHERE tr.portfolio_id = v_run.scope_id
                      AND rg.recorded_at <= v_run.cutoff_at
                    GROUP BY tr.transaction_id
                ), captured AS (
                    SELECT transaction_id, revision_number
                    FROM portfolio.portfolio_daily_transaction_input
                    WHERE manifest_id = NEW.manifest_id
                )
                SELECT 1
                FROM eligible AS e
                FULL JOIN captured AS c
                  ON c.transaction_id = e.transaction_id
                WHERE e.transaction_id IS NULL OR c.transaction_id IS NULL
                   OR e.revision_number <> c.revision_number
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_transaction_cutoff_selection_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM portfolio.portfolio_daily_instrument_input AS ii
                LEFT JOIN portfolio.portfolio_daily_corp_action_window AS cw
                  ON cw.manifest_id = ii.manifest_id
                 AND cw.instrument_id = ii.instrument_id
                WHERE ii.manifest_id = NEW.manifest_id
                  AND cw.instrument_id IS NULL
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_corporate_action_window_missing'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM portfolio.portfolio_daily_corp_action_window AS cw
                WHERE cw.manifest_id = NEW.manifest_id
                  AND (cw.captured_event_count <> (
                        SELECT count(*)
                        FROM portfolio.portfolio_daily_corp_action_input AS ci
                        WHERE ci.manifest_id = cw.manifest_id
                          AND ci.instrument_id = cw.instrument_id
                    ) OR cw.captured_event_count > cw.expected_event_count
                    OR (cw.coverage_state = 'complete'
                        AND cw.captured_event_count <> cw.expected_event_count))
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_corporate_action_window_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM portfolio.portfolio_daily_corp_action_input AS ci
                WHERE ci.manifest_id = NEW.manifest_id
                  AND ci.event_updated_at > v_run.cutoff_at
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_corporate_action_after_knowledge_cutoff'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM portfolio.portfolio_daily_quote_window AS qw
                WHERE qw.manifest_id = NEW.manifest_id
                  AND (qw.candidate_count <> (
                        SELECT count(*)
                        FROM portfolio.portfolio_daily_quote_candidate AS qc
                        WHERE qc.manifest_id = qw.manifest_id
                          AND qc.quote_window_id = qw.quote_window_id
                    ) OR qw.adopted_count <> (
                        SELECT count(*)
                        FROM portfolio.portfolio_daily_quote_candidate AS qc
                        WHERE qc.manifest_id = qw.manifest_id
                          AND qc.quote_window_id = qw.quote_window_id
                          AND qc.decision = 'adopted'
                    ) OR (qw.selection_status = 'selected' AND qw.adopted_count <> 1)
                    OR (qw.selection_status = 'unavailable' AND qw.adopted_count <> 0))
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_quote_window_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM portfolio.portfolio_daily_quote_window AS qw
                LEFT JOIN portfolio.portfolio_daily_instrument_input AS ii
                  ON ii.manifest_id = qw.manifest_id
                 AND ii.instrument_id = qw.instrument_id
                WHERE qw.manifest_id = NEW.manifest_id
                  AND (ii.instrument_id IS NULL OR ROW(
                        qw.selection_policy_version,
                        qw.selection_policy_revision,
                        qw.freshness_policy_version,
                        qw.freshness_mode,
                        qw.freshness_max_age_days,
                        qw.resolver_strategy_version
                      ) IS DISTINCT FROM ROW(
                        ii.quote_policy_version,
                        ii.quote_selection_policy_revision,
                        ii.freshness_policy_version,
                        ii.freshness_mode,
                        ii.freshness_max_age_days,
                        ii.resolver_strategy_version
                      ))
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_quote_policy_snapshot_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM portfolio.portfolio_daily_quote_candidate
                WHERE manifest_id = NEW.manifest_id
                  AND (ingested_at IS NULL OR ingested_at > v_run.cutoff_at)
                UNION ALL
                SELECT 1
                FROM portfolio.portfolio_daily_fx_leg
                WHERE manifest_id = NEW.manifest_id
                  AND leg_resolution_status <> 'missing'
                  AND (ingested_at IS NULL OR ingested_at > v_run.cutoff_at)
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_quote_after_knowledge_cutoff'
                    USING ERRCODE = '23514';
            END IF;

            FOR v_path IN
                SELECT * FROM portfolio.portfolio_daily_fx_path
                WHERE manifest_id = NEW.manifest_id
            LOOP
                SELECT count(*) INTO v_seen
                FROM portfolio.portfolio_daily_fx_leg
                WHERE manifest_id = v_path.manifest_id
                  AND fx_path_id = v_path.fx_path_id;
                IF v_seen <> v_path.leg_count
                   OR (v_path.path_kind = 'identity' AND
                       (v_path.from_currency <> v_path.to_currency
                        OR v_path.resolution_status <> 'resolved'
                        OR v_path.leg_count <> 0 OR v_path.resolved_rate <> 1
                        OR v_path.rate_derivation_residual_exact <> 0))
                   OR (v_path.path_kind = 'unavailable' AND
                       (v_path.resolution_status <> 'unavailable'
                        OR v_path.leg_count <> 0 OR v_path.resolved_rate IS NOT NULL
                        OR v_path.rate_derivation_residual_exact IS NOT NULL))
                   OR (v_path.path_kind IN ('direct', 'inverse') AND v_path.leg_count <> 1)
                   OR (v_path.path_kind = 'cross' AND v_path.leg_count < 2) THEN
                    RAISE EXCEPTION 'portfolio_daily_fx_path_shape_mismatch'
                        USING ERRCODE = '23514';
                END IF;
                IF v_path.leg_count > 0 THEN
                    v_rate := 1;
                    v_currency := v_path.from_currency;
                    v_seen := 0;
                    v_all_legs_resolved := true;
                    FOR v_leg IN
                        SELECT * FROM portfolio.portfolio_daily_fx_leg
                        WHERE manifest_id = v_path.manifest_id
                          AND fx_path_id = v_path.fx_path_id
                        ORDER BY leg_order
                    LOOP
                        v_seen := v_seen + 1;
                        IF v_leg.leg_order <> v_seen
                           OR v_leg.from_currency <> v_currency
                           OR ROW(
                                v_leg.consumer_policy_version,
                                v_leg.freshness_policy_version,
                                v_leg.freshness_mode,
                                v_leg.freshness_max_age_days,
                                v_leg.resolver_strategy_version,
                                v_leg.rate_math_precision,
                                v_leg.rate_rounding_mode
                              ) IS DISTINCT FROM ROW(
                                v_path.consumer_policy_version,
                                v_path.freshness_policy_version,
                                v_path.freshness_mode,
                                v_path.freshness_max_age_days,
                                v_path.resolver_strategy_version,
                                v_path.rate_math_precision,
                                v_path.rate_rounding_mode
                              ) THEN
                            RAISE EXCEPTION 'portfolio_daily_fx_path_not_contiguous'
                                USING ERRCODE = '23514';
                        END IF;
                        v_currency := v_leg.to_currency;
                        IF v_leg.leg_resolution_status = 'resolved' THEN
                            v_rate := v_rate * v_leg.effective_rate;
                        ELSE
                            v_all_legs_resolved := false;
                        END IF;
                    END LOOP;
                    IF v_currency <> v_path.to_currency THEN
                        RAISE EXCEPTION 'portfolio_daily_fx_path_rate_mismatch'
                            USING ERRCODE = '23514';
                    END IF;
                    IF v_path.resolution_status = 'resolved'
                       AND (NOT v_all_legs_resolved
                            OR v_path.resolved_rate IS DISTINCT FROM v_rate
                            OR v_path.rate_derivation_residual_exact IS DISTINCT FROM 0)
                    THEN
                        RAISE EXCEPTION 'portfolio_daily_fx_path_rate_mismatch'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
            END LOOP;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_00_pd_manifest_seal
        BEFORE UPDATE ON calculation_registry.calculation_input_manifest
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_validate_manifest_seal();
        """
    )


def _create_output_and_publication_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION portfolio.pd_guard_output_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_attempt record;
            v_run record;
        BEGIN
            FOR v_attempt IN
                SELECT DISTINCT run_id, output_fencing_token, worker_id, portfolio_id
                FROM inserted_rows
            LOOP
                PERFORM calculation_registry.assert_run_output_writable(
                    v_attempt.run_id,
                    v_attempt.output_fencing_token,
                    v_attempt.worker_id
                );
                SELECT calculation_kind, scope_kind, scope_id,
                       methodology_version, input_schema_version,
                       output_schema_version
                INTO v_run
                FROM calculation_registry.calculation_run
                WHERE run_id = v_attempt.run_id
                FOR KEY SHARE;
                IF v_run.calculation_kind IS DISTINCT FROM 'portfolio_daily'
                   OR v_run.scope_kind IS DISTINCT FROM 'portfolio'
                   OR v_run.scope_id IS DISTINCT FROM v_attempt.portfolio_id
                   OR v_run.methodology_version IS DISTINCT FROM 'portfolio-daily.exact.v1'
                   OR v_run.input_schema_version IS DISTINCT FROM 'portfolio-daily-input.v1'
                   OR v_run.output_schema_version IS DISTINCT FROM 'portfolio-daily-output.v1' THEN
                    RAISE EXCEPTION 'portfolio_daily_output_run_contract_mismatch'
                        USING ERRCODE = '23514';
                END IF;
            END LOOP;
            RETURN NULL;
        END;
        $$;

        CREATE FUNCTION portfolio.pd_validate_publication()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_run record;
            v_summary record;
            v_snapshot_count bigint;
            v_measured_nav_count bigint;
            v_holding_count bigint;
            v_balance_count bigint;
            v_lot_count bigint;
            v_lot_disposition_count bigint;
            v_contribution_count bigint;
            v_unavailable_count bigint;
            v_range_start date;
        BEGIN
            IF NEW.calculation_kind <> 'portfolio_daily' THEN
                RETURN NEW;
            END IF;
            SELECT effective_as_of, methodology_version, input_schema_version,
                   output_schema_version, scope_kind, scope_id
            INTO v_run
            FROM calculation_registry.calculation_run
            WHERE run_id = NEW.run_id
            FOR KEY SHARE;
            SELECT range_start INTO v_range_start
            FROM portfolio.portfolio_daily_config_input
            WHERE manifest_id = NEW.manifest_id;
            SELECT * INTO v_summary
            FROM portfolio.portfolio_daily_run_output
            WHERE run_id = NEW.run_id
              AND output_fencing_token = NEW.published_fencing_token;
            IF NOT FOUND OR v_summary.closure_status <> 'passed'
               OR v_summary.canonical_output_hash IS DISTINCT FROM NEW.canonical_output_hash
               OR v_summary.portfolio_id IS DISTINCT FROM NEW.scope_id
               OR v_summary.output_schema_version IS DISTINCT FROM NEW.output_schema_version
               OR v_summary.output_schema_version <> 'portfolio-daily-output.v1'
               OR v_summary.input_schema_version IS DISTINCT FROM v_run.input_schema_version
               OR v_summary.methodology_version IS DISTINCT FROM v_run.methodology_version
               OR v_summary.range_start IS DISTINCT FROM v_range_start
               OR v_summary.range_end IS DISTINCT FROM v_run.effective_as_of
               OR v_run.scope_kind <> 'portfolio' OR v_run.scope_id <> NEW.scope_id THEN
                RAISE EXCEPTION 'portfolio_daily_publication_summary_mismatch'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*), count(*) FILTER (WHERE measured_nav),
                   COALESCE(sum(unavailable_component_count), 0)
            INTO v_snapshot_count, v_measured_nav_count, v_unavailable_count
            FROM portfolio.portfolio_daily_snapshot_output
            WHERE run_id = NEW.run_id
              AND output_fencing_token = NEW.published_fencing_token;
            SELECT count(*) INTO v_holding_count
            FROM portfolio.portfolio_daily_holding_output
            WHERE run_id = NEW.run_id
              AND output_fencing_token = NEW.published_fencing_token;
            SELECT v_unavailable_count + COALESCE(sum(unavailable_component_count), 0)
            INTO v_unavailable_count
            FROM portfolio.portfolio_daily_holding_output
            WHERE run_id = NEW.run_id
              AND output_fencing_token = NEW.published_fencing_token;
            SELECT count(*), v_unavailable_count + count(*) FILTER (WHERE NOT measured_base_amount)
            INTO v_balance_count, v_unavailable_count
            FROM portfolio.portfolio_daily_balance_output
            WHERE run_id = NEW.run_id
              AND output_fencing_token = NEW.published_fencing_token;
            SELECT count(*), v_unavailable_count
                    + count(*) FILTER (WHERE NOT measured_base_cost)
            INTO v_lot_count, v_unavailable_count
            FROM portfolio.portfolio_daily_lot_output
            WHERE run_id = NEW.run_id
              AND output_fencing_token = NEW.published_fencing_token;
            SELECT count(*), v_unavailable_count
                    + count(*) FILTER (WHERE NOT measured_base_pnl)
            INTO v_lot_disposition_count, v_unavailable_count
            FROM portfolio.portfolio_daily_lot_disposition_output
            WHERE run_id = NEW.run_id
              AND output_fencing_token = NEW.published_fencing_token;
            SELECT count(*), v_unavailable_count + count(*) FILTER (WHERE NOT measured)
            INTO v_contribution_count, v_unavailable_count
            FROM portfolio.portfolio_daily_contribution_output
            WHERE run_id = NEW.run_id
              AND output_fencing_token = NEW.published_fencing_token;

            IF ROW(
                    v_summary.snapshot_count, v_summary.measured_nav_count,
                    v_summary.holding_count, v_summary.balance_count,
                    v_summary.lot_count, v_summary.lot_disposition_count,
                    v_summary.contribution_count,
                    v_summary.unavailable_component_count
                ) IS DISTINCT FROM ROW(
                    v_snapshot_count, v_measured_nav_count,
                    v_holding_count, v_balance_count,
                    v_lot_count, v_lot_disposition_count,
                    v_contribution_count, v_unavailable_count
                ) THEN
                RAISE EXCEPTION 'portfolio_daily_publication_output_counts_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM (
                    SELECT portfolio_id, as_of_date
                    FROM portfolio.portfolio_daily_snapshot_output
                    WHERE run_id = NEW.run_id AND output_fencing_token = NEW.published_fencing_token
                    UNION ALL
                    SELECT portfolio_id, as_of_date
                    FROM portfolio.portfolio_daily_holding_output
                    WHERE run_id = NEW.run_id AND output_fencing_token = NEW.published_fencing_token
                    UNION ALL
                    SELECT portfolio_id, as_of_date
                    FROM portfolio.portfolio_daily_balance_output
                    WHERE run_id = NEW.run_id AND output_fencing_token = NEW.published_fencing_token
                    UNION ALL
                    SELECT portfolio_id, as_of_date
                    FROM portfolio.portfolio_daily_lot_output
                    WHERE run_id = NEW.run_id AND output_fencing_token = NEW.published_fencing_token
                    UNION ALL
                    SELECT portfolio_id, as_of_date
                    FROM portfolio.portfolio_daily_lot_disposition_output
                    WHERE run_id = NEW.run_id AND output_fencing_token = NEW.published_fencing_token
                    UNION ALL
                    SELECT portfolio_id, as_of_date
                    FROM portfolio.portfolio_daily_contribution_output
                    WHERE run_id = NEW.run_id AND output_fencing_token = NEW.published_fencing_token
                ) AS output_row
                WHERE output_row.portfolio_id <> NEW.scope_id
                   OR output_row.as_of_date < v_summary.range_start
                   OR output_row.as_of_date > v_summary.range_end
            ) THEN
                RAISE EXCEPTION 'portfolio_daily_publication_output_scope_or_range_mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_00_pd_publication_guard
        BEFORE INSERT ON calculation_registry.calculation_publication
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_validate_publication();
        """
    )


def _install_semantic_numeric_domains() -> None:
    """Install fail-closed logical domains over physical unbounded NUMERIC."""

    domains = (
        (
            "portfolio_daily_transaction_input",
            "ck_portfolio_daily_transaction_input_quantity_price_domain",
            ("quantity", "price"),
            38,
            12,
        ),
        (
            "portfolio_daily_transaction_input",
            "ck_portfolio_daily_transaction_input_amount_domain",
            ("gross_amount", "counter_amount", "fees", "taxes"),
            38,
            8,
        ),
        (
            "portfolio_daily_transaction_input",
            "ck_portfolio_daily_transaction_input_quoted_fx_rate_domain",
            ("quoted_fx_rate",),
            38,
            18,
        ),
        (
            "portfolio_daily_transaction_input",
            "ck_portfolio_daily_transaction_input_terms_evidence_domain",
            (
                "consideration_terms_difference_exact",
                "quoted_terms_difference_exact",
            ),
            84,
            26,
        ),
        (
            "portfolio_daily_transaction_input",
            "ck_portfolio_daily_transaction_input_effective_fx_rate_domain",
            ("effective_fx_rate_method50",),
            132,
            100,
        ),
        (
            "portfolio_daily_instrument_input",
            "ck_portfolio_daily_instrument_input_exact_qty_domain",
            ("contract_multiplier", "price_factor"),
            50,
            12,
        ),
        (
            "portfolio_daily_corp_action_input",
            "ck_portfolio_daily_corp_action_input_source_rate_domain",
            ("new_units", "old_units"),
            38,
            18,
        ),
        (
            "portfolio_daily_fx_path",
            "ck_portfolio_daily_fx_path_derived_rate_domain",
            ("resolved_rate",),
            132,
            100,
        ),
        (
            "portfolio_daily_fx_path",
            "ck_portfolio_daily_fx_path_method_ev_domain",
            ("rate_derivation_residual_exact",),
            232,
            200,
        ),
        (
            "portfolio_daily_fx_leg",
            "ck_portfolio_daily_fx_leg_derived_rate_domain",
            ("effective_rate",),
            132,
            100,
        ),
        (
            "portfolio_daily_fx_leg",
            "ck_portfolio_daily_fx_leg_method_ev_domain",
            ("rate_derivation_residual_exact",),
            232,
            200,
        ),
        (
            "portfolio_daily_run_output",
            "ck_portfolio_daily_run_output_acct_ev_domain",
            (
                "ledger_balance_residual_exact",
                "nav_bridge_residual_exact",
                "pnl_residual_exact",
                "lot_residual_exact",
            ),
            242,
            200,
        ),
        (
            "portfolio_daily_run_output",
            "ck_portfolio_daily_run_output_method_ev_domain",
            ("twr_residual_exact",),
            232,
            200,
        ),
        (
            "portfolio_daily_snapshot_output",
            "ck_portfolio_daily_snapshot_output_acct_ev_domain",
            (
                "position_attribution_residual_exact",
                "reliable_anchor_nav_exact",
            ),
            242,
            200,
        ),
        (
            "portfolio_daily_holding_output",
            "ck_portfolio_daily_holding_output_source_price_domain",
            ("adopted_price_exact",),
            38,
            12,
        ),
        (
            "portfolio_daily_holding_output",
            "ck_portfolio_daily_holding_output_exact_qty_domain",
            (
                "quantity_exact",
                "contract_multiplier_exact",
                "price_factor_exact",
            ),
            50,
            12,
        ),
        (
            "portfolio_daily_holding_output",
            "ck_portfolio_daily_holding_output_derived_rate_domain",
            ("adopted_fx_rate_exact",),
            132,
            100,
        ),
        (
            "portfolio_daily_holding_output",
            "ck_portfolio_daily_holding_output_acct_ev_domain",
            (
                "market_value_local_exact",
                "market_value_base_exact",
                "position_attribution_residual_exact",
            ),
            242,
            200,
        ),
        (
            "portfolio_daily_balance_output",
            "ck_portfolio_daily_balance_output_derived_rate_domain",
            ("adopted_fx_rate_exact",),
            132,
            100,
        ),
        (
            "portfolio_daily_balance_output",
            "ck_portfolio_daily_balance_output_acct_ev_domain",
            ("base_amount_exact",),
            242,
            200,
        ),
        (
            "portfolio_daily_lot_output",
            "ck_portfolio_daily_lot_output_exact_qty_domain",
            ("open_quantity_exact",),
            50,
            12,
        ),
        (
            "portfolio_daily_lot_output",
            "ck_portfolio_daily_lot_output_derived_rate_domain",
            ("acquisition_fx_rate_exact",),
            132,
            100,
        ),
        (
            "portfolio_daily_lot_output",
            "ck_portfolio_daily_lot_output_acct_ev_domain",
            (
                "cost_basis_local_exact",
                "unit_cost_local_rounding_residual_exact",
                "cost_basis_base_exact",
                "unit_cost_base_rounding_residual_exact",
            ),
            242,
            200,
        ),
        (
            "portfolio_daily_lot_disposition_output",
            "ck_portfolio_daily_lot_disposition_output_exact_qty_domain",
            ("disposed_quantity_exact",),
            50,
            12,
        ),
        (
            "portfolio_daily_lot_disposition_output",
            "ck_portfolio_daily_lot_disposition_output_derived_rate_domain",
            ("disposition_fx_rate_exact",),
            132,
            100,
        ),
        (
            "portfolio_daily_lot_disposition_output",
            "ck_portfolio_daily_lot_disposition_output_acct_ev_domain",
            (
                "proceeds_local_exact",
                "allocated_cost_local_exact",
                "realized_pnl_local_exact",
                "proceeds_base_exact",
                "allocated_cost_base_exact",
                "realized_pnl_base_exact",
            ),
            242,
            200,
        ),
        (
            "portfolio_daily_contribution_output",
            "ck_portfolio_daily_contribution_output_acct_ev_domain",
            (
                "opening_nav_exact",
                "closing_nav_exact",
                "external_flow_in_exact",
                "external_flow_out_exact",
                "internal_flow_in_exact",
                "internal_flow_out_exact",
                "economic_pnl_exact",
                "closure_residual_exact",
            ),
            242,
            200,
        ),
        (
            "portfolio_daily_contribution_output",
            "ck_portfolio_daily_contribution_output_method_ev_domain",
            ("contribution_division_adjustment_exact",),
            232,
            200,
        ),
    )
    for table_name, constraint_name, columns, precision, scale in domains:
        _add_logical_numeric_domain(
            table_name,
            constraint_name,
            columns,
            precision=precision,
            scale=scale,
        )

    op.execute(
        "ALTER TABLE portfolio.portfolio_daily_transaction_input "
        "ADD CONSTRAINT ck_portfolio_daily_transaction_input_effective_fx_rate_precision "
        "CHECK (effective_fx_rate_method50 IS NULL OR "
        "effective_fx_rate_method50 = calculation_registry."
        "round_significant_half_even(effective_fx_rate_method50, 50))"
    )

    op.execute(
        "ALTER TABLE portfolio.portfolio_daily_fx_leg "
        "ADD CONSTRAINT ck_portfolio_daily_fx_leg_effective_rate_precision "
        "CHECK (effective_rate IS NULL OR effective_rate = "
        "calculation_registry.round_significant_half_even(effective_rate, 50))"
    )

    adopted_price_domain = _logical_numeric_domain(
        "quote_value",
        precision=38,
        scale=12,
    )
    op.execute(
        "ALTER TABLE portfolio.portfolio_daily_quote_candidate "
        "ADD CONSTRAINT ck_portfolio_daily_quote_candidate_adopted_price_domain "
        "CHECK (decision <> 'adopted' OR "
        f"(quote_value IS NOT NULL AND {adopted_price_domain}))"
    )
    resolved_rate_domain = _logical_numeric_domain(
        "quoted_rate",
        precision=38,
        scale=18,
    )
    op.execute(
        "ALTER TABLE portfolio.portfolio_daily_fx_leg "
        "ADD CONSTRAINT ck_portfolio_daily_fx_leg_resolved_rate_domain "
        "CHECK (leg_resolution_status <> 'resolved' OR "
        f"(quoted_rate IS NOT NULL AND {resolved_rate_domain}))"
    )


def _install_table_guards() -> None:
    for table_name in DEPENDENCY_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_00_pd_dependency_scope
            BEFORE INSERT OR UPDATE ON portfolio.{table_name}
            FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_dependency_scope();
            CREATE TRIGGER trg_10_pd_dependency_mutation
            BEFORE INSERT OR UPDATE OR DELETE ON portfolio.{table_name}
            FOR EACH ROW EXECUTE FUNCTION
                calculation_registry.guard_manifest_dependency_mutation();
            CREATE TRIGGER trg_90_pd_dependency_truncate
            BEFORE TRUNCATE ON portfolio.{table_name}
            FOR EACH STATEMENT EXECUTE FUNCTION portfolio.pd_reject_truncate();
            """
        )

    for table_name in (
        "portfolio_daily_corp_action_window",
        "portfolio_daily_quote_window",
        "portfolio_daily_fx_path",
        "portfolio_daily_fx_leg",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_20_pd_dependency_reasons
            BEFORE INSERT OR UPDATE ON portfolio.{table_name}
            FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_common_reasons();
            """
        )

    op.execute(
        """
        CREATE TRIGGER trg_20_pd_instrument_reasons
        BEFORE INSERT OR UPDATE ON portfolio.portfolio_daily_instrument_input
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_instrument_reasons();
        CREATE TRIGGER trg_20_pd_transaction_lineage
        BEFORE INSERT OR UPDATE ON portfolio.portfolio_daily_transaction_input
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_transaction_lineage();
        CREATE TRIGGER trg_20_pd_corp_action_lineage
        BEFORE INSERT OR UPDATE ON portfolio.portfolio_daily_corp_action_input
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_corp_action_lineage();
        CREATE TRIGGER trg_20_pd_quote_lineage
        BEFORE INSERT OR UPDATE ON portfolio.portfolio_daily_quote_candidate
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_quote_candidate_lineage();
        CREATE TRIGGER trg_20_pd_fx_leg_lineage
        BEFORE INSERT OR UPDATE ON portfolio.portfolio_daily_fx_leg
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_fx_leg_lineage();
        """
    )

    for table_name in OUTPUT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_05_pd_output_finite
            BEFORE INSERT ON portfolio.{table_name}
            FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_output_finite();
            CREATE TRIGGER trg_10_pd_output_insert
            AFTER INSERT ON portfolio.{table_name}
            REFERENCING NEW TABLE AS inserted_rows
            FOR EACH STATEMENT EXECUTE FUNCTION portfolio.pd_guard_output_insert();
            CREATE TRIGGER trg_80_pd_output_immutable
            BEFORE UPDATE OR DELETE ON portfolio.{table_name}
            FOR EACH ROW EXECUTE FUNCTION portfolio.pd_reject_output_mutation();
            CREATE TRIGGER trg_90_pd_output_truncate
            BEFORE TRUNCATE ON portfolio.{table_name}
            FOR EACH STATEMENT EXECUTE FUNCTION portfolio.pd_reject_truncate();
            """
        )

    for table_name in (
        "portfolio_daily_run_output",
        "portfolio_daily_balance_output",
        "portfolio_daily_contribution_output",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_00_pd_output_reasons
            BEFORE INSERT ON portfolio.{table_name}
            FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_common_reasons();
            """
        )
    op.execute(
        """
        CREATE TRIGGER trg_00_pd_snapshot_reasons
        BEFORE INSERT ON portfolio.portfolio_daily_snapshot_output
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_snapshot_reasons();
        CREATE TRIGGER trg_00_pd_holding_reasons
        BEFORE INSERT ON portfolio.portfolio_daily_holding_output
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_holding_reasons();
        CREATE TRIGGER trg_00_pd_lot_reasons
        BEFORE INSERT ON portfolio.portfolio_daily_lot_output
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_lot_reasons();
        CREATE TRIGGER trg_00_pd_lot_disposition_reasons
        BEFORE INSERT ON portfolio.portfolio_daily_lot_disposition_output
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_lot_disposition_reasons();
        CREATE TRIGGER trg_02_pd_lot_lineage
        BEFORE INSERT ON portfolio.portfolio_daily_lot_output
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_lot_lineage();
        CREATE TRIGGER trg_02_pd_lot_disposition_lineage
        BEFORE INSERT ON portfolio.portfolio_daily_lot_disposition_output
        FOR EACH ROW EXECUTE FUNCTION portfolio.pd_guard_lot_disposition_lineage();
        """
    )


def _drop_exact_storage() -> None:
    op.execute(
        """
        DROP TRIGGER trg_00_pd_publication_guard
            ON calculation_registry.calculation_publication;
        DROP TRIGGER trg_00_pd_manifest_seal
            ON calculation_registry.calculation_input_manifest;
        """
    )
    for table_name in reversed(OUTPUT_TABLES):
        op.execute(f"DROP TABLE portfolio.{table_name}")
    for table_name in reversed(DEPENDENCY_TABLES):
        op.execute(f"DROP TABLE portfolio.{table_name}")
    for function_signature in (
        "pd_validate_publication()",
        "pd_guard_output_insert()",
        "pd_validate_manifest_seal()",
        "pd_guard_fx_leg_lineage()",
        "pd_guard_quote_candidate_lineage()",
        "pd_guard_corp_action_lineage()",
        "pd_guard_transaction_lineage()",
        "pd_guard_lot_disposition_lineage()",
        "pd_guard_lot_lineage()",
        "pd_guard_output_finite()",
        "pd_reject_output_mutation()",
        "pd_guard_holding_reasons()",
        "pd_guard_lot_disposition_reasons()",
        "pd_guard_lot_reasons()",
        "pd_guard_snapshot_reasons()",
        "pd_guard_instrument_reasons()",
        "pd_guard_common_reasons()",
        "pd_guard_dependency_scope()",
        "pd_reject_truncate()",
        "pd_reason_codes_are_canonical(varchar[])",
    ):
        op.execute(f"DROP FUNCTION portfolio.{function_signature}")
    op.execute(
        """
        DROP INDEX portfolio.uq_portfolio_record_active_sort_order;
        ALTER TABLE portfolio.portfolio_record
            DROP CONSTRAINT ck_portfolio_record_lifecycle_status,
            DROP COLUMN lifecycle_status;
        """
    )


def _restore_empty_legacy_structures() -> None:
    # This is a structural downgrade only.  Exact outputs cannot be represented
    # by the old float schema, so no NAV, read model, state, or publication is
    # fabricated.
    op.execute(
        """
        ALTER TABLE portfolio.portfolio_record
            ADD COLUMN as_of_date date,
            ADD COLUMN nav double precision,
            ADD COLUMN day_change_value double precision,
            ADD COLUMN day_change_pct double precision,
            ADD COLUMN securities_count integer NOT NULL DEFAULT 0;
        ALTER TABLE portfolio.portfolio_record
            ALTER COLUMN securities_count DROP DEFAULT;

        CREATE TABLE portfolio.portfolio_daily_snapshot (
            portfolio_id varchar NOT NULL,
            as_of_date date NOT NULL,
            nav_coverage_state varchar NOT NULL,
            nav_coverage_reason_codes json NOT NULL,
            book_pnl_coverage_state varchar NOT NULL,
            book_pnl_coverage_reason_codes json NOT NULL,
            nav double precision,
            beginning_nav double precision,
            ending_nav double precision,
            daily_twr double precision,
            cumulative_twr double precision,
            drawdown double precision,
            snapshot_json json NOT NULL,
            calculated_at varchar NOT NULL,
            CONSTRAINT pk_portfolio_daily_snapshot PRIMARY KEY (portfolio_id, as_of_date),
            CONSTRAINT fk_legacy_snapshot_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE CASCADE,
            CONSTRAINT ck_portfolio_daily_snapshot_nav_coverage_state
                CHECK (nav_coverage_state IN ('complete', 'partial', 'unavailable')),
            CONSTRAINT ck_portfolio_daily_snapshot_book_pnl_coverage_state
                CHECK (book_pnl_coverage_state IN ('complete', 'partial', 'unavailable'))
        );
        CREATE INDEX ix_portfolio_daily_snapshot_portfolio_nav_coverage
            ON portfolio.portfolio_daily_snapshot(
                portfolio_id, nav_coverage_state, as_of_date
            );

        CREATE TABLE portfolio.portfolio_daily_holding_snapshot (
            portfolio_id varchar NOT NULL,
            as_of_date date NOT NULL,
            account_id varchar NOT NULL,
            instrument_id varchar NOT NULL,
            currency varchar NOT NULL,
            quantity double precision NOT NULL,
            cost_basis double precision,
            cost_basis_base double precision,
            last_price double precision,
            market_value double precision,
            market_value_base double precision,
            portfolio_weight double precision,
            holding_json json NOT NULL,
            calculated_at varchar NOT NULL,
            CONSTRAINT pk_portfolio_daily_holding_snapshot PRIMARY KEY (
                portfolio_id, as_of_date, account_id, instrument_id
            ),
            CONSTRAINT fk_legacy_holding_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE CASCADE
        );
        CREATE INDEX ix_portfolio_daily_holding_portfolio_date
            ON portfolio.portfolio_daily_holding_snapshot(portfolio_id, as_of_date);
        CREATE INDEX ix_portfolio_daily_holding_instrument_date
            ON portfolio.portfolio_daily_holding_snapshot(
                portfolio_id, instrument_id, as_of_date
            );
        CREATE INDEX ix_portfolio_daily_holding_account_date
            ON portfolio.portfolio_daily_holding_snapshot(
                portfolio_id, account_id, as_of_date
            );

        CREATE TABLE portfolio.portfolio_daily_contribution_slice (
            portfolio_id varchar NOT NULL,
            as_of_date date NOT NULL,
            axis varchar NOT NULL,
            group_key varchar NOT NULL,
            group_label varchar NOT NULL,
            nav_coverage_state varchar NOT NULL,
            nav_coverage_reason_codes json NOT NULL,
            book_pnl_coverage_state varchar NOT NULL,
            book_pnl_coverage_reason_codes json NOT NULL,
            total_pnl double precision,
            daily_contribution double precision,
            slice_json json NOT NULL,
            calculated_at varchar NOT NULL,
            CONSTRAINT pk_portfolio_daily_contribution_slice PRIMARY KEY (
                portfolio_id, as_of_date, axis, group_key
            ),
            CONSTRAINT fk_legacy_contribution_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE CASCADE,
            CONSTRAINT ck_portfolio_daily_contribution_slice_nav_coverage_state
                CHECK (nav_coverage_state IN ('complete', 'partial', 'unavailable')),
            CONSTRAINT ck_portfolio_daily_contribution_slice_book_pnl_coverage_state
                CHECK (book_pnl_coverage_state IN ('complete', 'partial', 'unavailable'))
        );
        CREATE INDEX ix_portfolio_daily_contribution_axis_date
            ON portfolio.portfolio_daily_contribution_slice(
                portfolio_id, axis, as_of_date
            );
        CREATE INDEX ix_portfolio_daily_contribution_group_date
            ON portfolio.portfolio_daily_contribution_slice(
                portfolio_id, axis, group_key, as_of_date
            );

        CREATE TABLE portfolio.portfolio_calculation_state (
            portfolio_id varchar NOT NULL,
            daily_snapshot_status varchar NOT NULL DEFAULT 'stale',
            dirty_from date,
            refreshed_from date,
            refreshed_to date,
            refreshed_at varchar,
            refresh_request_id varchar,
            refresh_started_at varchar,
            refresh_completed_at varchar,
            source_market_data_updated_at varchar,
            error_message varchar,
            CONSTRAINT pk_portfolio_calculation_state PRIMARY KEY (portfolio_id),
            CONSTRAINT fk_legacy_calc_state_portfolio FOREIGN KEY (portfolio_id)
                REFERENCES portfolio.portfolio_record(portfolio_id) ON DELETE CASCADE
        );
        """
    )


def upgrade() -> None:
    _require_postgresql()
    _drop_legacy_materialisations()
    _create_dependency_tables()
    _create_output_tables()
    _create_remaining_output_tables()
    _install_semantic_numeric_domains()
    _seed_scope_generations()
    _create_common_functions()
    _create_lineage_functions()
    _create_manifest_guard()
    _create_output_and_publication_guards()
    _install_table_guards()


def downgrade() -> None:
    _require_postgresql()
    _drop_exact_storage()
    _restore_empty_legacy_structures()
    # Scope-generation rows are intentionally retained.  The registry forbids
    # deletion and a row may already be referenced by a calculation run.
