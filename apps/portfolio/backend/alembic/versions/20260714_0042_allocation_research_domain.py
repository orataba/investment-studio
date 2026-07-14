"""Rename the allocation research domain and policy replay contract.

Revision ID: 20260714_0042
Revises: 20260714_0041
Create Date: 2026-07-14

This is an intentionally incompatible domain rename.  The database keeps one
canonical vocabulary; no legacy views, duplicate columns, or aliases remain.
"""

from __future__ import annotations

from alembic import op


revision = "20260714_0042"
down_revision = "20260714_0041"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("20260714_0042 requires PostgreSQL")


def _rename_json_keys(*, reverse: bool) -> None:
    source_prefix = "policy_replay" if reverse else "backtest"
    target_prefix = "backtest" if reverse else "policy_replay"
    op.execute(
        f"""
        UPDATE portfolio.allocation_research_run_record
        SET detail_json = (
            detail_json::jsonb
                - '{source_prefix}'
                - '{source_prefix}_benchmark'
                - '{source_prefix}_relative_metrics'
            || CASE
                WHEN detail_json::jsonb ? '{source_prefix}'
                THEN jsonb_build_object(
                    '{target_prefix}', detail_json::jsonb -> '{source_prefix}'
                )
                ELSE '{{}}'::jsonb
            END
            || CASE
                WHEN detail_json::jsonb ? '{source_prefix}_benchmark'
                THEN jsonb_build_object(
                    '{target_prefix}_benchmark',
                    detail_json::jsonb -> '{source_prefix}_benchmark'
                )
                ELSE '{{}}'::jsonb
            END
            || CASE
                WHEN detail_json::jsonb ? '{source_prefix}_relative_metrics'
                THEN jsonb_build_object(
                    '{target_prefix}_relative_metrics',
                    detail_json::jsonb -> '{source_prefix}_relative_metrics'
                )
                ELSE '{{}}'::jsonb
            END
        )::json
        WHERE detail_json IS NOT NULL
          AND detail_json::jsonb ?| ARRAY[
              '{source_prefix}',
              '{source_prefix}_benchmark',
              '{source_prefix}_relative_metrics'
          ];

        UPDATE portfolio.allocation_research_run_record
        SET request_payload_json = (
            request_payload_json::jsonb
                - '{source_prefix}_rebalance_frequency'
                - '{source_prefix}_benchmark_instrument_id'
            || CASE
                WHEN request_payload_json::jsonb
                    ? '{source_prefix}_rebalance_frequency'
                THEN jsonb_build_object(
                    '{target_prefix}_rebalance_frequency',
                    request_payload_json::jsonb
                        -> '{source_prefix}_rebalance_frequency'
                )
                ELSE '{{}}'::jsonb
            END
            || CASE
                WHEN request_payload_json::jsonb
                    ? '{source_prefix}_benchmark_instrument_id'
                THEN jsonb_build_object(
                    '{target_prefix}_benchmark_instrument_id',
                    request_payload_json::jsonb
                        -> '{source_prefix}_benchmark_instrument_id'
                )
                ELSE '{{}}'::jsonb
            END
        )::json
        WHERE request_payload_json IS NOT NULL
          AND request_payload_json::jsonb ?| ARRAY[
              '{source_prefix}_rebalance_frequency',
              '{source_prefix}_benchmark_instrument_id'
          ];
        """
    )


def _rename_json_semantic_values(*, reverse: bool) -> None:
    source_method = (
        "allocation-policy-replay-metrics.v3.history-gated-arithmetic-sharpe"
        if reverse
        else "research-backtest-metrics.v2.history-gated-arithmetic-sharpe"
    )
    target_method = (
        "research-backtest-metrics.v2.history-gated-arithmetic-sharpe"
        if reverse
        else "allocation-policy-replay-metrics.v3.history-gated-arithmetic-sharpe"
    )
    source_consumer = "policy_replay" if reverse else "policy_backtest"
    target_consumer = "policy_backtest" if reverse else "policy_replay"
    op.execute(
        f"""
        UPDATE portfolio.allocation_research_run_record
        SET detail_json = replace(
                replace(
                    detail_json::text,
                    '{source_method}',
                    '{target_method}'
                ),
                '"{source_consumer}"',
                '"{target_consumer}"'
            )::json
        WHERE detail_json IS NOT NULL;

        UPDATE portfolio.allocation_research_run_record
        SET request_payload_json = replace(
                replace(
                    request_payload_json::text,
                    '{source_method}',
                    '{target_method}'
                ),
                '"{source_consumer}"',
                '"{target_consumer}"'
            )::json
        WHERE request_payload_json IS NOT NULL;
        """
    )


def _normalize_allocation_run_type() -> None:
    """Collapse the superseded backtest template into the canonical run type."""

    op.execute(
        """
        UPDATE portfolio.allocation_research_run_record
        SET job_type = 'target_weight_solve'
        WHERE job_type = 'taxonomy_backtest';
        """
    )


def upgrade() -> None:
    _require_postgresql()
    op.execute(
        """
        ALTER TABLE portfolio.research_settings_record
            RENAME TO allocation_research_settings_record;
        ALTER TABLE portfolio.research_run_record
            RENAME TO allocation_research_run_record;

        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME COLUMN backtest_rebalance_frequency
                TO policy_replay_rebalance_frequency;
        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME COLUMN backtest_benchmark_instrument_id
                TO policy_replay_benchmark_instrument_id;
        ALTER TABLE portfolio.allocation_research_run_record
            RENAME COLUMN research_run_id TO allocation_research_run_id;

        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME CONSTRAINT pk_research_settings_record
                TO pk_allocation_research_settings_record;
        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME CONSTRAINT fk_research_settings_record_portfolio_id_portfolio_record
                TO fk_allocation_research_settings_record_portfolio_id_por_88a5;
        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME CONSTRAINT ck_research_settings_record_ck_research_settings_as_of_mode
                TO ck_allocation_research_settings_record_ck_allocation_re_890c;
        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME CONSTRAINT ck_research_settings_record_ck_research_settings_suppor_e9e2
                TO ck_allocation_research_settings_record_ck_allocation_re_948a;

        ALTER TABLE portfolio.allocation_research_run_record
            RENAME CONSTRAINT pk_research_run_record
                TO pk_allocation_research_run_record;
        ALTER TABLE portfolio.allocation_research_run_record
            RENAME CONSTRAINT fk_research_run_record_portfolio_id_portfolio_record
                TO fk_allocation_research_run_record_portfolio_id_portfolio_record;
        ALTER TABLE portfolio.allocation_research_run_record
            RENAME CONSTRAINT ck_research_run_record_ck_research_run_supported_lookback
                TO ck_allocation_research_run_record_ck_allocation_researc_5f70;
        ALTER INDEX portfolio.ix_research_run_record_portfolio_requested
            RENAME TO ix_allocation_research_run_record_portfolio_requested;
        """
    )
    _normalize_allocation_run_type()
    _rename_json_keys(reverse=False)
    _rename_json_semantic_values(reverse=False)


def downgrade() -> None:
    _require_postgresql()
    _rename_json_semantic_values(reverse=True)
    _rename_json_keys(reverse=True)
    op.execute(
        """
        ALTER INDEX portfolio.ix_allocation_research_run_record_portfolio_requested
            RENAME TO ix_research_run_record_portfolio_requested;
        ALTER TABLE portfolio.allocation_research_run_record
            RENAME CONSTRAINT ck_allocation_research_run_record_ck_allocation_researc_5f70
                TO ck_research_run_record_ck_research_run_supported_lookback;
        ALTER TABLE portfolio.allocation_research_run_record
            RENAME CONSTRAINT fk_allocation_research_run_record_portfolio_id_portfolio_record
                TO fk_research_run_record_portfolio_id_portfolio_record;
        ALTER TABLE portfolio.allocation_research_run_record
            RENAME CONSTRAINT pk_allocation_research_run_record
                TO pk_research_run_record;

        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME CONSTRAINT ck_allocation_research_settings_record_ck_allocation_re_948a
                TO ck_research_settings_record_ck_research_settings_suppor_e9e2;
        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME CONSTRAINT ck_allocation_research_settings_record_ck_allocation_re_890c
                TO ck_research_settings_record_ck_research_settings_as_of_mode;
        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME CONSTRAINT fk_allocation_research_settings_record_portfolio_id_por_88a5
                TO fk_research_settings_record_portfolio_id_portfolio_record;
        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME CONSTRAINT pk_allocation_research_settings_record
                TO pk_research_settings_record;

        ALTER TABLE portfolio.allocation_research_run_record
            RENAME COLUMN allocation_research_run_id TO research_run_id;
        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME COLUMN policy_replay_benchmark_instrument_id
                TO backtest_benchmark_instrument_id;
        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME COLUMN policy_replay_rebalance_frequency
                TO backtest_rebalance_frequency;

        ALTER TABLE portfolio.allocation_research_run_record
            RENAME TO research_run_record;
        ALTER TABLE portfolio.allocation_research_settings_record
            RENAME TO research_settings_record;
        """
    )
