#!/usr/bin/env python3
"""Read-only integrity audit for the local Portfolio Operations database."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

import psycopg


DEFAULT_DATABASE_URL = (
    "postgresql://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops"
)

EXPECTED_MIGRATION_HEADS = {
    "instrument_registry": "20260714_0013",
    "calculation_registry": "20260714_0001",
    "portfolio": "20260714_0042",
    "watchlist": "20260714_0034",
}

TRANSACTION_PAYLOAD_SCHEMA_VERSION = "transaction-revision.v1"
TRANSACTION_FACT_FIELDS = (
    "transaction_type",
    "trade_date",
    "trade_time",
    "trade_at",
    "trade_timezone",
    "trade_time_is_estimated",
    "settlement_date",
    "account_id",
    "gross_amount",
    "fees",
    "taxes",
    "currency",
    "entitlement_date",
    "acquisition_date",
    "settlement_cash_account_id",
    "instrument_id",
    "instrument_snapshot_json",
    "quantity",
    "price",
    "counter_amount",
    "quoted_fx_rate",
    "consideration_basis",
    "numeric_scale_state",
    "quantity_input_scale",
    "price_input_scale",
    "gross_amount_input_scale",
    "counter_amount_input_scale",
    "quoted_fx_rate_input_scale",
    "fees_input_scale",
    "taxes_input_scale",
    "transfer_scope",
    "transfer_object_type",
    "transfer_group_id",
    "counterparty_account_id",
    "note",
)
TRANSACTION_DECIMAL_SCALES = {
    "quantity": 12,
    "price": 12,
    "gross_amount": 8,
    "counter_amount": 8,
    "quoted_fx_rate": 18,
    "fees": 8,
    "taxes": 8,
}
INTERNAL_TRANSFER_TYPES = frozenset({"transfer_in", "transfer_out"})
INTERNAL_TRANSFER_MIRRORED_FIELDS = (
    "trade_date",
    "trade_time",
    "trade_at",
    "trade_timezone",
    "trade_time_is_estimated",
    "settlement_date",
    "entitlement_date",
    "acquisition_date",
    "settlement_cash_account_id",
    "instrument_id",
    "instrument_snapshot_json",
    "quantity",
    "price",
    "gross_amount",
    "counter_amount",
    "quoted_fx_rate",
    "fees",
    "taxes",
    "consideration_basis",
    "numeric_scale_state",
    "quantity_input_scale",
    "price_input_scale",
    "gross_amount_input_scale",
    "counter_amount_input_scale",
    "quoted_fx_rate_input_scale",
    "fees_input_scale",
    "taxes_input_scale",
    "currency",
    "transfer_scope",
    "transfer_object_type",
    "note",
)


WATCHLIST_BOUNDED_READ_MODEL_LINEAGE_QUERY = """
    WITH materialized_resolution AS (
        SELECT
            'chart' AS read_model,
            instrument_id,
            'current' AS resolution_scope,
            payload_json::jsonb -> 'resolution' AS resolution
        FROM watchlist.instrument_chart_read_model

        UNION ALL

        SELECT
            'summary',
            instrument_id,
            'current',
            payload_json::jsonb -> 'quote_resolution'
        FROM watchlist.instrument_summary_read_model

        UNION ALL

        SELECT
            'performance',
            instrument_id,
            'current',
            payload_json::jsonb -> 'quote_resolution'
        FROM watchlist.instrument_performance_read_model

        UNION ALL

        SELECT
            'performance',
            instrument_id,
            'historical',
            payload_json::jsonb -> 'historical_quote_resolution'
        FROM watchlist.instrument_performance_read_model

        UNION ALL

        SELECT
            'risk',
            instrument_id,
            'current',
            payload_json::jsonb -> 'quote_resolution'
        FROM watchlist.instrument_risk_read_model

        UNION ALL

        SELECT
            'risk',
            instrument_id,
            'historical',
            payload_json::jsonb -> 'historical_quote_resolution'
        FROM watchlist.instrument_risk_read_model
    )
    SELECT count(*)
    FROM materialized_resolution
    WHERE resolution IS NOT NULL
      AND resolution <> 'null'::jsonb
      AND (
          jsonb_typeof(resolution) <> 'object'
          OR resolution ->> 'schema_version'
              IS DISTINCT FROM 'watchlist_quote_resolution_summary.v1'
          OR jsonb_typeof(resolution -> 'calculation_dependency') <> 'object'
          OR coalesce(
              resolution -> 'calculation_dependency' ->> 'fingerprint',
              ''
          ) = ''
          OR jsonb_typeof(
              resolution -> 'calculation_dependency' -> 'revision_count'
          ) <> 'number'
          OR jsonb_typeof(
              resolution -> 'calculation_dependency'
                  -> 'excluded_revision_count'
          ) <> 'number'
          OR jsonb_typeof(resolution -> 'consumer_dependency') <> 'object'
          OR coalesce(
              resolution -> 'consumer_dependency' ->> 'fingerprint',
              ''
          ) = ''
          OR jsonb_path_exists(resolution, '$.**.observations')
          OR jsonb_path_exists(resolution, '$.**.points')
          OR jsonb_path_exists(
              resolution,
              '$.**.calculation_dependency.revision_ids'
          )
          OR jsonb_path_exists(
              resolution,
              '$.**.calculation_dependency.payload_hashes'
          )
          OR jsonb_path_exists(
              resolution,
              '$.**.calculation_dependency.excluded_revision_ids'
          )
          OR jsonb_path_exists(
              resolution,
              '$.**.calculation_dependency.excluded_payload_hashes'
          )
      )
"""


PORTFOLIO_ALLOCATION_POLICY_REPLAY_METRICS_CONTRACT_QUERY = """
    WITH persisted_metrics AS (
        SELECT
            run.allocation_research_run_id,
            candidate.metric_path,
            candidate.metrics
        FROM portfolio.allocation_research_run_record AS run
        CROSS JOIN LATERAL (
            VALUES
                (
                    'policy_replay.metrics',
                    run.detail_json::jsonb #> '{policy_replay,metrics}'
                ),
                (
                    'policy_replay_benchmark.metrics',
                    run.detail_json::jsonb #> '{policy_replay_benchmark,metrics}'
                ),
                (
                    'policy_replay_relative_metrics',
                    run.detail_json::jsonb -> 'policy_replay_relative_metrics'
                )
        ) AS candidate(metric_path, metrics)
        WHERE candidate.metrics IS NOT NULL
          AND candidate.metrics <> 'null'::jsonb
    ), metric_parts AS (
        SELECT
            allocation_research_run_id,
            metric_path,
            metrics,
            metrics -> 'history_reliability' AS history
        FROM persisted_metrics
    ), typed_history AS (
        SELECT
            allocation_research_run_id,
            metric_path,
            metrics,
            history,
            CASE
                WHEN jsonb_typeof(history -> 'start_date') = 'string'
                 AND pg_input_is_valid(history ->> 'start_date', 'date')
                THEN (history ->> 'start_date')::date
                ELSE NULL
            END AS history_start_date,
            CASE
                WHEN jsonb_typeof(history -> 'end_date') = 'string'
                 AND pg_input_is_valid(history ->> 'end_date', 'date')
                THEN (history ->> 'end_date')::date
                ELSE NULL
            END AS history_end_date,
            CASE
                WHEN jsonb_typeof(history -> 'elapsed_days') = 'number'
                 AND history ->> 'elapsed_days' ~ '^(0|[1-9][0-9]*)$'
                THEN (history ->> 'elapsed_days')::numeric
                ELSE NULL
            END AS history_elapsed_days,
            CASE
                WHEN jsonb_typeof(history -> 'calendar_span_days') = 'number'
                 AND history ->> 'calendar_span_days' ~ '^[1-9][0-9]*$'
                THEN (history ->> 'calendar_span_days')::numeric
                ELSE NULL
            END AS history_calendar_span_days
        FROM metric_parts
    )
    SELECT count(*)
    FROM typed_history
    WHERE jsonb_typeof(metrics) IS DISTINCT FROM 'object'
       OR metrics ->> 'method_version' IS DISTINCT FROM
          'allocation-policy-replay-metrics.v3.history-gated-arithmetic-sharpe'
       OR jsonb_typeof(history) IS DISTINCT FROM 'object'
       OR (history ?& ARRAY[
              'start_date',
              'end_date',
              'elapsed_days',
              'calendar_span_days',
              'minimum_history_days',
              'annualized_return_eligible',
              'annualized_return_reason_codes',
              'sample_label',
              'annualization_message'
          ]) IS NOT TRUE
       OR EXISTS (
              SELECT 1
              FROM jsonb_object_keys(
                  CASE
                      WHEN jsonb_typeof(history) = 'object'
                      THEN history
                      ELSE '{}'::jsonb
                  END
              ) AS history_key(field_name)
              WHERE history_key.field_name <> ALL (ARRAY[
                  'start_date',
                  'end_date',
                  'elapsed_days',
                  'calendar_span_days',
                  'minimum_history_days',
                  'annualized_return_eligible',
                  'annualized_return_reason_codes',
                  'sample_label',
                  'annualization_message'
              ]::text[])
          )
       OR (
              history -> 'start_date' = 'null'::jsonb
              OR (
                  jsonb_typeof(history -> 'start_date') = 'string'
                  AND pg_input_is_valid(history ->> 'start_date', 'date')
              )
          ) IS NOT TRUE
       OR (
              history -> 'end_date' = 'null'::jsonb
              OR (
                  jsonb_typeof(history -> 'end_date') = 'string'
                  AND pg_input_is_valid(history ->> 'end_date', 'date')
              )
          ) IS NOT TRUE
       OR (
              history -> 'elapsed_days' = 'null'::jsonb
              OR (
                  jsonb_typeof(history -> 'elapsed_days') = 'number'
                  AND history ->> 'elapsed_days' ~ '^(0|[1-9][0-9]*)$'
              )
          ) IS NOT TRUE
       OR (
              history -> 'calendar_span_days' = 'null'::jsonb
              OR (
                  jsonb_typeof(history -> 'calendar_span_days') = 'number'
                  AND history ->> 'calendar_span_days' ~ '^[1-9][0-9]*$'
              )
          ) IS NOT TRUE
       OR jsonb_typeof(history -> 'minimum_history_days')
          IS DISTINCT FROM 'number'
       OR history ->> 'minimum_history_days' IS DISTINCT FROM '365'
       OR jsonb_typeof(history -> 'annualized_return_eligible')
          IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(history -> 'annualized_return_reason_codes')
          IS DISTINCT FROM 'array'
       OR jsonb_typeof(history -> 'sample_label') IS DISTINCT FROM 'string'
       OR btrim(coalesce(history ->> 'sample_label', '')) = ''
       OR (
              history -> 'annualization_message' = 'null'::jsonb
              OR jsonb_typeof(history -> 'annualization_message') = 'string'
          ) IS NOT TRUE
       OR metrics -> 'start_date' IS DISTINCT FROM history -> 'start_date'
       OR metrics -> 'end_date' IS DISTINCT FROM history -> 'end_date'
       OR (metrics ? 'annualized_return') IS NOT TRUE
       OR (
              jsonb_typeof(metrics -> 'annualized_return')
              = ANY (ARRAY['number', 'null']::text[])
          ) IS NOT TRUE
       OR (metrics ? 'calmar_ratio') IS NOT TRUE
       OR (
              jsonb_typeof(metrics -> 'calmar_ratio')
              = ANY (ARRAY['number', 'null']::text[])
          ) IS NOT TRUE
       OR CASE
              WHEN history_start_date IS NULL OR history_end_date IS NULL
              THEN
                  history_elapsed_days IS NOT NULL
                  OR history_calendar_span_days IS NOT NULL
                  OR history -> 'annualized_return_eligible'
                     IS DISTINCT FROM 'false'::jsonb
                  OR history -> 'annualized_return_reason_codes'
                     IS DISTINCT FROM
                     '["performance_history_window_unavailable"]'::jsonb
                  OR jsonb_typeof(history -> 'annualization_message')
                     IS DISTINCT FROM 'string'
                  OR btrim(coalesce(
                      history ->> 'annualization_message', ''
                  )) = ''
              ELSE
                  history_end_date < history_start_date
                  OR history_elapsed_days IS DISTINCT FROM
                     (history_end_date - history_start_date)::numeric
                  OR history_calendar_span_days IS DISTINCT FROM
                     (history_end_date - history_start_date + 1)::numeric
                  OR history -> 'annualized_return_eligible'
                     IS DISTINCT FROM to_jsonb(
                         (history_end_date - history_start_date) >= 365
                     )
                  OR history -> 'annualized_return_reason_codes'
                     IS DISTINCT FROM CASE
                         WHEN (history_end_date - history_start_date) >= 365
                         THEN '[]'::jsonb
                         ELSE '["annualized_return_history_below_minimum"]'::jsonb
                     END
                  OR CASE
                         WHEN (history_end_date - history_start_date) >= 365
                         THEN history -> 'annualization_message'
                              IS DISTINCT FROM 'null'::jsonb
                         ELSE
                             jsonb_typeof(
                                 history -> 'annualization_message'
                             ) IS DISTINCT FROM 'string'
                             OR btrim(coalesce(
                                 history ->> 'annualization_message', ''
                             )) = ''
                     END
          END
       OR (
              history -> 'annualized_return_eligible' = 'false'::jsonb
              AND (
                  metrics -> 'annualized_return'
                      IS DISTINCT FROM 'null'::jsonb
                  OR metrics -> 'calmar_ratio'
                      IS DISTINCT FROM 'null'::jsonb
              )
          )
"""


PORTFOLIO_RUNTIME_OBJECTS_QUERY = """
    WITH expected(relation_name, relation_kind) AS (
        VALUES
            ('transaction_identity_record', 'r'::"char"),
            ('transaction_revision_group_record', 'r'::"char"),
            ('transaction_revision_record', 'r'::"char"),
            ('transaction_current', 'v'::"char"),
            ('portfolio_daily_config_input', 'r'::"char"),
            ('portfolio_daily_account_input', 'r'::"char"),
            ('portfolio_daily_transaction_input', 'r'::"char"),
            ('portfolio_daily_instrument_input', 'r'::"char"),
            ('portfolio_daily_corp_action_window', 'r'::"char"),
            ('portfolio_daily_corp_action_input', 'r'::"char"),
            ('portfolio_daily_quote_window', 'r'::"char"),
            ('portfolio_daily_quote_candidate', 'r'::"char"),
            ('portfolio_daily_fx_path', 'r'::"char"),
            ('portfolio_daily_fx_leg', 'r'::"char"),
            ('portfolio_daily_prior_publication', 'r'::"char"),
            ('portfolio_daily_run_output', 'r'::"char"),
            ('portfolio_daily_snapshot_output', 'r'::"char"),
            ('portfolio_daily_holding_output', 'r'::"char"),
            ('portfolio_daily_balance_output', 'r'::"char"),
            ('portfolio_daily_lot_output', 'r'::"char"),
            ('portfolio_daily_lot_disposition_output', 'r'::"char"),
            ('portfolio_daily_contribution_output', 'r'::"char"),
            ('portfolio_daily_dependency_subscription', 'r'::"char")
    ), actual AS (
        SELECT relation.relname AS relation_name, relation.relkind AS relation_kind
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'portfolio'
    ), violations AS (
        SELECT expected.relation_name
        FROM expected
        LEFT JOIN actual USING (relation_name)
        WHERE actual.relation_name IS NULL
           OR actual.relation_kind <> expected.relation_kind

        UNION ALL

        SELECT relation.relname
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'portfolio'
          AND relation.relname IN (
              'transaction_record',
              'transaction_record_legacy_0036',
              'portfolio_daily_snapshot',
              'portfolio_daily_holding_snapshot',
              'portfolio_daily_contribution_slice',
              'portfolio_calculation_state'
          )

        UNION ALL

        SELECT table_name
        FROM information_schema.views
        WHERE table_schema = 'portfolio'
          AND table_name = 'transaction_current'
          AND is_updatable <> 'NO'

        UNION ALL

        SELECT expected_constraint.constraint_name
        FROM (
            VALUES
                ('ck_transaction_revision_group_record_actor_source'),
                ('ck_transaction_revision_group_record_actor_source_type')
        ) AS expected_constraint(constraint_name)
        LEFT JOIN pg_constraint constraint_record
          ON constraint_record.conrelid =
             'portfolio.transaction_revision_group_record'::regclass
         AND constraint_record.conname = expected_constraint.constraint_name
        WHERE constraint_record.oid IS NULL
           OR constraint_record.contype <> 'c'
           OR NOT constraint_record.convalidated
    )
    SELECT count(*) FROM violations
"""


PORTFOLIO_TRANSACTION_HEAD_PROJECTION_QUERY = """
    WITH ranked_revision AS (
        SELECT
            revision.*,
            row_number() OVER (
                PARTITION BY revision.portfolio_id, revision.transaction_id
                ORDER BY revision.revision_number DESC
            ) AS head_rank
        FROM portfolio.transaction_revision_record revision
    ), latest AS (
        SELECT * FROM ranked_revision WHERE head_rank = 1
    ), ledger_counts AS (
        SELECT
            (
                SELECT count(*)
                FROM portfolio.transaction_identity_record
            ) AS identity_count,
            (
                SELECT count(*)
                FROM (
                    SELECT portfolio_id, transaction_id
                    FROM portfolio.transaction_revision_record
                    GROUP BY portfolio_id, transaction_id
                ) revision_chain
            ) AS revision_chain_count,
            (
                SELECT count(*)
                FROM portfolio.transaction_current
            ) AS current_count,
            (
                SELECT count(*)
                FROM latest
                WHERE NOT is_tombstone
            ) AS latest_live_count
    ), count_violations AS (
        SELECT 'ledger-count-contract' AS transaction_id
        FROM ledger_counts
        WHERE identity_count <> revision_chain_count
           OR current_count <> latest_live_count
    ), identity_violations AS (
        SELECT identity.transaction_id
        FROM portfolio.transaction_identity_record identity
        LEFT JOIN latest
          ON latest.portfolio_id = identity.portfolio_id
         AND latest.transaction_id = identity.transaction_id
        LEFT JOIN portfolio.transaction_revision_group_record revision_group
          ON revision_group.portfolio_id = latest.portfolio_id
         AND revision_group.revision_group_id = latest.revision_group_id
        LEFT JOIN portfolio.transaction_current current_fact
          ON current_fact.portfolio_id = identity.portfolio_id
         AND current_fact.transaction_id = identity.transaction_id
        WHERE latest.revision_id IS NULL
           OR (latest.is_tombstone AND current_fact.transaction_id IS NOT NULL)
           OR (
                NOT latest.is_tombstone
                AND (
                    current_fact.transaction_id IS NULL
                    OR current_fact.current_revision_id <> latest.revision_id
                    OR current_fact.current_revision_number <> latest.revision_number
                    OR current_fact.revision_group_id <> latest.revision_group_id
                    OR current_fact.revision_kind <> latest.revision_kind
                    OR current_fact.payload_schema_version
                       <> latest.payload_schema_version
                    OR current_fact.payload_hash <> latest.payload_hash
                    OR current_fact.created_at IS DISTINCT FROM identity.created_at
                    OR current_fact.created_by IS DISTINCT FROM identity.created_by
                    OR current_fact.source_kind
                       IS DISTINCT FROM revision_group.source_kind
                    OR current_fact.change_reason
                       IS DISTINCT FROM revision_group.change_reason
                    OR current_fact.actor_type
                       IS DISTINCT FROM revision_group.actor_type
                    OR current_fact.actor_id
                       IS DISTINCT FROM revision_group.actor_id
                    OR current_fact.actor_display_name
                       IS DISTINCT FROM revision_group.actor_display_name
                    OR current_fact.actor_source
                       IS DISTINCT FROM revision_group.actor_source
                    OR current_fact.recorded_at
                       IS DISTINCT FROM revision_group.recorded_at
                    OR (
                        to_jsonb(current_fact) - ARRAY[
                            'transaction_id', 'portfolio_id',
                            'current_revision_id', 'current_revision_number',
                            'revision_group_id', 'revision_kind',
                            'payload_schema_version', 'payload_hash',
                            'created_at', 'created_by', 'source_kind',
                            'change_reason', 'actor_type', 'actor_id',
                            'actor_display_name', 'actor_source', 'recorded_at'
                        ]::text[]
                    ) IS DISTINCT FROM (
                        to_jsonb(latest) - ARRAY[
                            'revision_id', 'portfolio_id', 'transaction_id',
                            'revision_number', 'revision_group_id',
                            'revision_kind', 'is_tombstone',
                            'supersedes_revision_id',
                            'supersedes_revision_number',
                            'payload_schema_version', 'payload_hash', 'head_rank'
                        ]::text[]
                    )
                )
           )
    ), current_violations AS (
        SELECT current_fact.transaction_id
        FROM portfolio.transaction_current current_fact
        LEFT JOIN portfolio.transaction_identity_record identity
          ON identity.portfolio_id = current_fact.portfolio_id
         AND identity.transaction_id = current_fact.transaction_id
        LEFT JOIN latest
          ON latest.portfolio_id = current_fact.portfolio_id
         AND latest.transaction_id = current_fact.transaction_id
        WHERE identity.transaction_id IS NULL
           OR latest.revision_id IS NULL
           OR latest.is_tombstone
           OR latest.revision_id <> current_fact.current_revision_id
           OR latest.revision_number <> current_fact.current_revision_number
    ), duplicate_current AS (
        SELECT transaction_id
        FROM portfolio.transaction_current
        GROUP BY portfolio_id, transaction_id
        HAVING count(*) <> 1
    )
    SELECT count(*)
    FROM (
        SELECT transaction_id FROM count_violations
        UNION ALL
        SELECT transaction_id FROM identity_violations
        UNION ALL
        SELECT transaction_id FROM current_violations
        UNION ALL
        SELECT transaction_id FROM duplicate_current
    ) violations
"""


PORTFOLIO_TRANSACTION_REVISION_CHAIN_QUERY = """
    WITH chain_summary AS (
        SELECT
            identity.portfolio_id,
            identity.transaction_id,
            count(revision.revision_id) AS revision_count,
            count(DISTINCT revision.revision_number) AS distinct_revision_count,
            min(revision.revision_number) AS first_revision_number,
            max(revision.revision_number) AS latest_revision_number
        FROM portfolio.transaction_identity_record identity
        LEFT JOIN portfolio.transaction_revision_record revision
          ON revision.portfolio_id = identity.portfolio_id
         AND revision.transaction_id = identity.transaction_id
        GROUP BY identity.portfolio_id, identity.transaction_id
    ), violations AS (
        SELECT transaction_id
        FROM chain_summary
        WHERE revision_count = 0
           OR first_revision_number <> 1
           OR latest_revision_number <> revision_count
           OR distinct_revision_count <> revision_count

        UNION ALL

        SELECT revision.transaction_id
        FROM portfolio.transaction_revision_record revision
        WHERE revision.revision_number = 1
          AND (
              revision.revision_kind NOT IN ('baseline', 'create')
              OR revision.is_tombstone
              OR revision.supersedes_revision_id IS NOT NULL
              OR revision.supersedes_revision_number IS NOT NULL
          )

        UNION ALL

        SELECT tombstone.transaction_id
        FROM portfolio.transaction_revision_record tombstone
        JOIN portfolio.transaction_revision_record newer
          ON newer.portfolio_id = tombstone.portfolio_id
         AND newer.transaction_id = tombstone.transaction_id
         AND newer.revision_number > tombstone.revision_number
        WHERE tombstone.is_tombstone

        UNION ALL

        SELECT revision.transaction_id
        FROM portfolio.transaction_revision_record revision
        JOIN portfolio.transaction_revision_group_record revision_group
          ON revision_group.portfolio_id = revision.portfolio_id
         AND revision_group.revision_group_id = revision.revision_group_id
        JOIN portfolio.transaction_revision_record predecessor
          ON predecessor.portfolio_id = revision.portfolio_id
         AND predecessor.transaction_id = revision.transaction_id
         AND predecessor.revision_number = revision.revision_number - 1
        JOIN portfolio.transaction_revision_group_record predecessor_group
          ON predecessor_group.portfolio_id = predecessor.portfolio_id
         AND predecessor_group.revision_group_id = predecessor.revision_group_id
        WHERE revision.revision_number > 1
          AND revision_group.recorded_at < predecessor_group.recorded_at
    )
    SELECT count(*) FROM violations
"""


PORTFOLIO_TRANSACTION_SUPERSEDES_QUERY = """
    SELECT count(*)
    FROM portfolio.transaction_revision_record revision
    LEFT JOIN portfolio.transaction_revision_record predecessor
      ON predecessor.portfolio_id = revision.portfolio_id
     AND predecessor.transaction_id = revision.transaction_id
     AND predecessor.revision_number = revision.supersedes_revision_number
     AND predecessor.revision_id = revision.supersedes_revision_id
    WHERE (
            revision.revision_number = 1
            AND (
                revision.supersedes_revision_id IS NOT NULL
                OR revision.supersedes_revision_number IS NOT NULL
            )
          )
       OR (
            revision.revision_number > 1
            AND (
                revision.revision_kind NOT IN ('amend', 'delete')
                OR revision.supersedes_revision_number
                   <> revision.revision_number - 1
                OR predecessor.revision_id IS NULL
                OR predecessor.is_tombstone
            )
          )
"""


PORTFOLIO_TRANSACTION_AUDIT_METADATA_QUERY = """
    WITH violations AS (
        SELECT revision_group.revision_group_id
        FROM portfolio.transaction_revision_group_record revision_group
        LEFT JOIN portfolio.transaction_revision_record revision
          ON revision.portfolio_id = revision_group.portfolio_id
         AND revision.revision_group_id = revision_group.revision_group_id
        WHERE revision.revision_id IS NULL
           OR revision_group.source_kind NOT IN (
               'manual', 'import', 'reconciliation', 'migration', 'system'
           )
           OR btrim(revision_group.change_reason) = ''
           OR char_length(revision_group.change_reason) > 500
           OR revision_group.actor_type NOT IN ('user', 'service', 'migration')
           OR btrim(revision_group.actor_id) = ''
           OR char_length(revision_group.actor_id) > 128
           OR btrim(revision_group.actor_display_name) = ''
           OR char_length(revision_group.actor_display_name) > 128
           OR btrim(revision_group.actor_source) = ''
           OR char_length(revision_group.actor_source) > 128
           OR revision_group.actor_source NOT IN (
               'client_asserted', 'authenticated_principal',
               'trusted_service', 'migration'
           )
           OR NOT (
                  (
                      revision_group.actor_type = 'user'
                      AND revision_group.actor_source IN (
                          'client_asserted', 'authenticated_principal'
                      )
                  )
               OR (
                      revision_group.actor_type = 'service'
                      AND revision_group.actor_source = 'trusted_service'
                  )
               OR (
                      revision_group.actor_type = 'migration'
                      AND revision_group.actor_source = 'migration'
                  )
           )
           OR revision_group.recorded_at IS NULL
           OR (
               revision_group.actor_source = 'client_asserted'
               AND revision_group.actor_type <> 'user'
           )
           OR (
               revision_group.source_kind = 'migration'
               AND revision_group.actor_type <> 'migration'
           )
    )
    SELECT count(*) FROM violations
"""


PORTFOLIO_TRANSACTION_PAYLOAD_QUERY = """
    WITH violations AS (
        SELECT revision.revision_id
        FROM portfolio.transaction_revision_record revision
        LEFT JOIN portfolio.transaction_revision_record predecessor
          ON predecessor.portfolio_id = revision.portfolio_id
         AND predecessor.transaction_id = revision.transaction_id
         AND predecessor.revision_number = revision.revision_number - 1
        WHERE revision.payload_schema_version <> 'transaction-revision.v1'
           OR revision.payload_hash !~ '^sha256:[0-9a-f]{64}$'
           OR revision.is_tombstone <> (revision.revision_kind = 'delete')
           OR (
                revision.is_tombstone
                AND revision.payload_hash <>
                    'sha256:bc5787018813b8e0562f74da4098be5dc135994b08a078955af74b9d11b7ef91'
           )
           OR (
                revision.is_tombstone
                AND EXISTS (
                    SELECT 1
                    FROM jsonb_each(
                        to_jsonb(revision) - ARRAY[
                            'revision_id', 'portfolio_id', 'transaction_id',
                            'revision_number', 'revision_group_id',
                            'revision_kind', 'is_tombstone',
                            'supersedes_revision_id',
                            'supersedes_revision_number',
                            'payload_schema_version', 'payload_hash'
                        ]::text[]
                    ) fact(field_name, field_value)
                    WHERE fact.field_value <> 'null'::jsonb
                )
           )
           OR (
                NOT revision.is_tombstone
                AND (
                    revision.transaction_type IS NULL
                    OR revision.trade_date IS NULL
                    OR revision.trade_time IS NULL
                    OR revision.trade_at IS NULL
                    OR btrim(revision.trade_timezone) = ''
                    OR revision.trade_time_is_estimated IS NULL
                    OR revision.settlement_date IS NULL
                    OR btrim(revision.account_id) = ''
                    OR revision.gross_amount IS NULL
                    OR revision.fees IS NULL
                    OR revision.taxes IS NULL
                    OR revision.numeric_scale_state IS NULL
                    OR revision.gross_amount_input_scale IS NULL
                    OR revision.fees_input_scale IS NULL
                    OR revision.taxes_input_scale IS NULL
                    OR revision.currency IS NULL
                )
           )
           OR (
                revision.instrument_snapshot_json IS NOT NULL
                AND (
                    json_typeof(revision.instrument_snapshot_json) <> 'object'
                    OR revision.instrument_snapshot_json ->> 'instrument_id'
                       IS DISTINCT FROM revision.instrument_id
                )
           )
           OR (
                (revision.instrument_id IS NULL)
                <> (revision.instrument_snapshot_json IS NULL)
           )
           OR revision.consideration_basis NOT IN (
                'exact_quantity_price', 'source_reported'
           )
           OR (
                (
                    revision.transaction_type IN (
                        'buy', 'sell', 'dividend_reinvestment'
                    )
                    OR (
                        revision.transaction_type = 'opening_balance'
                        AND revision.instrument_id IS NOT NULL
                    )
                )
                IS DISTINCT FROM (revision.consideration_basis IS NOT NULL)
           )
           OR (
                revision.revision_kind = 'amend'
                AND predecessor.payload_hash = revision.payload_hash
           )
    )
    SELECT count(*) FROM violations
"""


PORTFOLIO_TRANSACTION_DECIMAL_COLUMNS_QUERY = """
    WITH expected(table_name, column_name, max_scale, integer_digits) AS (
        VALUES
            ('transaction_revision_record', 'quantity', 12, 26),
            ('transaction_revision_record', 'price', 12, 26),
            ('transaction_revision_record', 'gross_amount', 8, 30),
            ('transaction_revision_record', 'counter_amount', 8, 30),
            ('transaction_revision_record', 'quoted_fx_rate', 18, 20),
            ('transaction_revision_record', 'fees', 8, 30),
            ('transaction_revision_record', 'taxes', 8, 30),
            ('transaction_current', 'quantity', 12, 26),
            ('transaction_current', 'price', 12, 26),
            ('transaction_current', 'gross_amount', 8, 30),
            ('transaction_current', 'counter_amount', 8, 30),
            ('transaction_current', 'quoted_fx_rate', 18, 20),
            ('transaction_current', 'fees', 8, 30),
            ('transaction_current', 'taxes', 8, 30)
    ), actual AS (
        SELECT table_name, column_name, data_type, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = 'portfolio'
          AND table_name IN (
              'transaction_revision_record', 'transaction_current'
          )
    )
    , schema_violations AS (
        SELECT expected.table_name, expected.column_name
        FROM expected
        LEFT JOIN actual USING (table_name, column_name)
        WHERE actual.column_name IS NULL
           OR actual.data_type <> 'numeric'
           OR actual.numeric_precision IS NOT NULL
           OR actual.numeric_scale IS NOT NULL
    ), fact_violations AS (
        SELECT revision.revision_id::text AS violation_id
        FROM portfolio.transaction_revision_record revision
        WHERE NOT revision.is_tombstone
          AND (
              revision.numeric_scale_state NOT IN ('declared', 'legacy_inferred')
              OR (revision.quantity IS NULL) IS DISTINCT FROM (revision.quantity_input_scale IS NULL)
              OR (revision.price IS NULL) IS DISTINCT FROM (revision.price_input_scale IS NULL)
              OR (revision.gross_amount IS NULL) IS DISTINCT FROM (revision.gross_amount_input_scale IS NULL)
              OR (revision.counter_amount IS NULL) IS DISTINCT FROM (revision.counter_amount_input_scale IS NULL)
              OR (revision.quoted_fx_rate IS NULL) IS DISTINCT FROM (revision.quoted_fx_rate_input_scale IS NULL)
              OR (revision.fees IS NULL) IS DISTINCT FROM (revision.fees_input_scale IS NULL)
              OR (revision.taxes IS NULL) IS DISTINCT FROM (revision.taxes_input_scale IS NULL)
              OR (revision.quantity IS NOT NULL AND (
                    revision.quantity_input_scale NOT BETWEEN 0 AND 12
                    OR revision.quantity <> trunc(revision.quantity, 12)
                    OR revision.quantity <> trunc(revision.quantity, revision.quantity_input_scale)
                    OR abs(revision.quantity) >= 1e26))
              OR (revision.price IS NOT NULL AND (
                    revision.price_input_scale NOT BETWEEN 0 AND 12
                    OR revision.price <> trunc(revision.price, 12)
                    OR revision.price <> trunc(revision.price, revision.price_input_scale)
                    OR abs(revision.price) >= 1e26))
              OR (revision.gross_amount IS NOT NULL AND (
                    revision.gross_amount_input_scale NOT BETWEEN 0 AND 8
                    OR revision.gross_amount <> trunc(revision.gross_amount, 8)
                    OR revision.gross_amount <> trunc(revision.gross_amount, revision.gross_amount_input_scale)
                    OR abs(revision.gross_amount) >= 1e30))
              OR (revision.counter_amount IS NOT NULL AND (
                    revision.counter_amount_input_scale NOT BETWEEN 0 AND 8
                    OR revision.counter_amount <> trunc(revision.counter_amount, 8)
                    OR revision.counter_amount <> trunc(revision.counter_amount, revision.counter_amount_input_scale)
                    OR abs(revision.counter_amount) >= 1e30))
              OR (revision.quoted_fx_rate IS NOT NULL AND (
                    revision.quoted_fx_rate_input_scale NOT BETWEEN 0 AND 18
                    OR revision.quoted_fx_rate <> trunc(revision.quoted_fx_rate, 18)
                    OR revision.quoted_fx_rate <> trunc(revision.quoted_fx_rate, revision.quoted_fx_rate_input_scale)
                    OR abs(revision.quoted_fx_rate) >= 1e20))
              OR (revision.fees IS NOT NULL AND (
                    revision.fees_input_scale NOT BETWEEN 0 AND 8
                    OR revision.fees <> trunc(revision.fees, 8)
                    OR revision.fees <> trunc(revision.fees, revision.fees_input_scale)
                    OR abs(revision.fees) >= 1e30))
              OR (revision.taxes IS NOT NULL AND (
                    revision.taxes_input_scale NOT BETWEEN 0 AND 8
                    OR revision.taxes <> trunc(revision.taxes, 8)
                    OR revision.taxes <> trunc(revision.taxes, revision.taxes_input_scale)
                    OR abs(revision.taxes) >= 1e30))
          )
    )
    SELECT
        (SELECT count(*) FROM schema_violations)
        + (SELECT count(*) FROM fact_violations)
"""


PORTFOLIO_TRANSACTION_INSTRUMENT_FK_QUERY = """
    WITH instrument_foreign_keys AS (
        SELECT
            constraint_record.conname,
            constraint_record.confrelid,
            constraint_record.confdeltype,
            ARRAY(
                SELECT attribute.attname::text
                FROM unnest(constraint_record.conkey)
                    WITH ORDINALITY AS key(attnum, position)
                JOIN pg_attribute attribute
                  ON attribute.attrelid = constraint_record.conrelid
                 AND attribute.attnum = key.attnum
                ORDER BY key.position
            ) AS local_columns,
            ARRAY(
                SELECT attribute.attname::text
                FROM unnest(constraint_record.confkey)
                    WITH ORDINALITY AS key(attnum, position)
                JOIN pg_attribute attribute
                  ON attribute.attrelid = constraint_record.confrelid
                 AND attribute.attnum = key.attnum
                ORDER BY key.position
            ) AS referenced_columns
        FROM pg_constraint constraint_record
        WHERE constraint_record.conrelid =
              'portfolio.transaction_revision_record'::regclass
          AND constraint_record.contype = 'f'
          AND ARRAY(
              SELECT attribute.attname::text
              FROM unnest(constraint_record.conkey) AS key(attnum)
              JOIN pg_attribute attribute
                ON attribute.attrelid = constraint_record.conrelid
               AND attribute.attnum = key.attnum
          ) = ARRAY['instrument_id']::text[]
    )
    SELECT CASE
        WHEN count(*) = 1
         AND bool_and(
             conname =
                 'fk_transaction_revision_record_instrument_id_instrument'
             AND confrelid = 'instrument_registry.instrument'::regclass
             AND confdeltype = 'r'
             AND local_columns = ARRAY['instrument_id']::text[]
             AND referenced_columns = ARRAY['instrument_id']::text[]
         )
        THEN 0
        ELSE 1
    END
    FROM instrument_foreign_keys
"""


PORTFOLIO_TRANSACTION_APPEND_ONLY_TRIGGER_QUERY = """
    WITH expected(trigger_name, table_name, trigger_type, function_name) AS (
        VALUES
            (
                'trg_transaction_identity_record_append_only',
                'transaction_identity_record', 27,
                'reject_transaction_ledger_mutation'
            ),
            (
                'trg_transaction_identity_record_truncate_forbidden',
                'transaction_identity_record', 34,
                'reject_transaction_ledger_mutation'
            ),
            (
                'trg_transaction_revision_group_record_append_only',
                'transaction_revision_group_record', 27,
                'reject_transaction_ledger_mutation'
            ),
            (
                'trg_transaction_revision_group_record_truncate_forbidden',
                'transaction_revision_group_record', 34,
                'reject_transaction_ledger_mutation'
            ),
            (
                'trg_transaction_revision_record_append_only',
                'transaction_revision_record', 27,
                'reject_transaction_ledger_mutation'
            ),
            (
                'trg_transaction_revision_record_truncate_forbidden',
                'transaction_revision_record', 34,
                'reject_transaction_ledger_mutation'
            ),
            (
                'trg_transaction_revision_record_transition',
                'transaction_revision_record', 7,
                'validate_transaction_revision_insert'
            )
    ), actual AS (
        SELECT
            trigger.tgname AS trigger_name,
            relation.relname AS table_name,
            trigger.tgtype::integer AS trigger_type,
            trigger.tgenabled,
            function.proname AS function_name,
            function_namespace.nspname AS function_schema
        FROM pg_trigger trigger
        JOIN pg_class relation ON relation.oid = trigger.tgrelid
        JOIN pg_namespace relation_namespace
          ON relation_namespace.oid = relation.relnamespace
        JOIN pg_proc function ON function.oid = trigger.tgfoid
        JOIN pg_namespace function_namespace
          ON function_namespace.oid = function.pronamespace
        WHERE relation_namespace.nspname = 'portfolio'
          AND NOT trigger.tgisinternal
    )
    SELECT count(*)
    FROM expected
    LEFT JOIN actual USING (trigger_name, table_name)
    WHERE actual.trigger_name IS NULL
       OR actual.trigger_type <> expected.trigger_type
       OR actual.tgenabled <> 'O'
       OR actual.function_name <> expected.function_name
       OR actual.function_schema <> 'portfolio'
"""


PORTFOLIO_TRANSACTION_0037_DATABASE_AUTHORITY_QUERY = """
    WITH expected_function(
        function_name, argument_signature, return_type, language_name, volatility
    ) AS (
        VALUES
            (
                'transaction_json_number_v1',
                'text'::regtype::oid::text,
                'text'::regtype::oid,
                'plpgsql', 'i'::"char"
            ),
            (
                'canonical_transaction_json_v1',
                'json'::regtype::oid::text,
                'text'::regtype::oid,
                'plpgsql', 'i'::"char"
            ),
            (
                'canonical_transaction_revision_payload_v1',
                'portfolio.transaction_revision_record'::regtype::oid::text,
                'text'::regtype::oid,
                'plpgsql', 'i'::"char"
            ),
            (
                'transaction_revision_payload_hash_v1',
                'portfolio.transaction_revision_record'::regtype::oid::text,
                'text'::regtype::oid,
                'sql', 'i'::"char"
            ),
            (
                'validate_transaction_revision_payload_insert_v1',
                ''::text,
                'trigger'::regtype::oid,
                'plpgsql', 'v'::"char"
            ),
            (
                'assert_internal_transfer_group_v1',
                'text'::regtype::oid::text || ' ' || 'text'::regtype::oid::text,
                'void'::regtype::oid,
                'plpgsql', 'v'::"char"
            ),
            (
                'validate_internal_transfer_revision_deferred_v1',
                ''::text,
                'trigger'::regtype::oid,
                'plpgsql', 'v'::"char"
            )
    ), actual_function AS (
        SELECT
            function.proname AS function_name,
            function.proargtypes::text AS argument_signature,
            function.prorettype AS return_type,
            language.lanname AS language_name,
            function.provolatile AS volatility,
            function.prosecdef AS security_definer
        FROM pg_proc function
        JOIN pg_namespace namespace ON namespace.oid = function.pronamespace
        JOIN pg_language language ON language.oid = function.prolang
        WHERE namespace.nspname = 'portfolio'
          AND function.proname IN (
              SELECT expected.function_name FROM expected_function expected
          )
    ), function_violations AS (
        SELECT expected.function_name AS object_name
        FROM expected_function expected
        LEFT JOIN actual_function actual
          ON actual.function_name = expected.function_name
         AND actual.argument_signature = expected.argument_signature
        WHERE actual.function_name IS NULL
           OR actual.return_type <> expected.return_type
           OR actual.language_name <> expected.language_name
           OR actual.volatility <> expected.volatility
           OR actual.security_definer
    ), expected_trigger(
        trigger_name, trigger_type, function_name,
        is_constraint, is_deferrable, is_initially_deferred
    ) AS (
        VALUES
            (
                'trg_transaction_revision_record_payload_v1', 7,
                'validate_transaction_revision_payload_insert_v1',
                false, false, false
            ),
            (
                'trg_transaction_revision_record_transfer_v1', 5,
                'validate_internal_transfer_revision_deferred_v1',
                true, true, true
            )
    ), actual_trigger AS (
        SELECT
            trigger.tgname AS trigger_name,
            trigger.tgtype::integer AS trigger_type,
            trigger.tgenabled,
            trigger.tgconstraint <> 0 AS is_constraint,
            trigger.tgdeferrable AS is_deferrable,
            trigger.tginitdeferred AS is_initially_deferred,
            function.proname AS function_name,
            function_namespace.nspname AS function_schema
        FROM pg_trigger trigger
        JOIN pg_class relation ON relation.oid = trigger.tgrelid
        JOIN pg_namespace relation_namespace
          ON relation_namespace.oid = relation.relnamespace
        JOIN pg_proc function ON function.oid = trigger.tgfoid
        JOIN pg_namespace function_namespace
          ON function_namespace.oid = function.pronamespace
        WHERE relation_namespace.nspname = 'portfolio'
          AND relation.relname = 'transaction_revision_record'
          AND NOT trigger.tgisinternal
    ), trigger_violations AS (
        SELECT expected.trigger_name AS object_name
        FROM expected_trigger expected
        LEFT JOIN actual_trigger actual USING (trigger_name)
        WHERE actual.trigger_name IS NULL
           OR actual.trigger_type <> expected.trigger_type
           OR actual.tgenabled <> 'O'
           OR actual.function_name <> expected.function_name
           OR actual.function_schema <> 'portfolio'
           OR actual.is_constraint <> expected.is_constraint
           OR actual.is_deferrable <> expected.is_deferrable
           OR actual.is_initially_deferred <> expected.is_initially_deferred
    )
    SELECT count(*)
    FROM (
        SELECT object_name FROM function_violations
        UNION ALL
        SELECT object_name FROM trigger_violations
    ) violations
"""


PORTFOLIO_TRANSACTION_REVISION_AUDIT_QUERY = """
    SELECT
        revision_id,
        portfolio_id,
        transaction_id,
        revision_number,
        revision_group_id,
        revision_kind,
        is_tombstone,
        payload_schema_version,
        payload_hash,
        transaction_type,
        trade_date,
        trade_time,
        trade_at,
        trade_timezone,
        trade_time_is_estimated,
        settlement_date,
        account_id,
        gross_amount,
        fees,
        taxes,
        currency,
        entitlement_date,
        acquisition_date,
        settlement_cash_account_id,
        instrument_id,
        instrument_snapshot_json,
        quantity,
        price,
        counter_amount,
        quoted_fx_rate,
        consideration_basis,
        numeric_scale_state,
        quantity_input_scale,
        price_input_scale,
        gross_amount_input_scale,
        counter_amount_input_scale,
        quoted_fx_rate_input_scale,
        fees_input_scale,
        taxes_input_scale,
        transfer_scope,
        transfer_object_type,
        transfer_group_id,
        counterparty_account_id,
        note
    FROM portfolio.transaction_revision_record
    ORDER BY portfolio_id, transaction_id, revision_number
"""


PORTFOLIO_TRANSACTION_CURRENT_TRANSFER_AUDIT_QUERY = """
    SELECT
        portfolio_id,
        transaction_id,
        revision_group_id,
        revision_kind,
        transaction_type,
        trade_date,
        trade_time,
        trade_at,
        trade_timezone,
        trade_time_is_estimated,
        settlement_date,
        account_id,
        gross_amount,
        fees,
        taxes,
        currency,
        entitlement_date,
        acquisition_date,
        settlement_cash_account_id,
        instrument_id,
        instrument_snapshot_json,
        quantity,
        price,
        counter_amount,
        quoted_fx_rate,
        consideration_basis,
        numeric_scale_state,
        quantity_input_scale,
        price_input_scale,
        gross_amount_input_scale,
        counter_amount_input_scale,
        quoted_fx_rate_input_scale,
        fees_input_scale,
        taxes_input_scale,
        transfer_scope,
        transfer_object_type,
        transfer_group_id,
        counterparty_account_id,
        note
    FROM portfolio.transaction_current
    WHERE transaction_type IN ('transfer_in', 'transfer_out')
       OR transfer_group_id IS NOT NULL
    ORDER BY portfolio_id, transfer_group_id, transaction_id
"""


@dataclass(frozen=True)
class AuditCheck:
    name: str
    status: str
    value: Any
    limit: Any
    detail: str


def _skip_check(*, name: str, detail: str) -> AuditCheck:
    return AuditCheck(
        name=name,
        status="skip",
        value=None,
        limit=None,
        detail=detail,
    )


def _schema_capability_gate(
    cursor: psycopg.Cursor[Any],
) -> tuple[list[AuditCheck], bool]:
    checks: list[AuditCheck] = []
    cursor.execute(
        """
        WITH required(component, relation_name) AS (
            VALUES
                ('calculation_registry', 'calculation_registry.alembic_version'),
                ('instrument_registry', 'instrument_registry.alembic_version'),
                ('portfolio', 'portfolio.alembic_version'),
                ('watchlist', 'watchlist.alembic_version')
        )
        SELECT component, relation_name, to_regclass(relation_name)
        FROM required
        ORDER BY component
        """
    )
    missing_version_tables = [
        str(relation_name)
        for _component, relation_name, relation_oid in cursor.fetchall()
        if relation_oid is None
    ]
    checks.append(
        AuditCheck(
            name="database_migration_version_tables",
            status="pass" if not missing_version_tables else "fail",
            value=missing_version_tables,
            limit=[],
            detail=(
                "All managed Alembic version tables must exist before any "
                "schema-dependent integrity query runs."
            ),
        )
    )
    if missing_version_tables:
        checks.extend(
            (
                _skip_check(
                    name="database_migration_heads",
                    detail=(
                        "Migration heads were not read because one or more managed "
                        "Alembic version tables are absent."
                    ),
                ),
                _skip_check(
                    name="portfolio_runtime_required_objects",
                    detail=(
                        "The 0037 transaction-ledger capability check requires all "
                        "managed migration version tables."
                    ),
                ),
                _skip_check(
                    name="portfolio_transaction_0037_database_authority",
                    detail=(
                        "The 0037 hash and deferred-transfer database objects require "
                        "all managed migration version tables."
                    ),
                ),
                _skip_check(
                    name="database_schema_dependent_checks",
                    detail=(
                        "Schema-dependent checks were skipped after the migration "
                        "capability gate failed."
                    ),
                ),
            )
        )
        return checks, False

    cursor.execute(
        """
        SELECT 'instrument_registry' AS component,
               count(*)::integer AS row_count,
               min(version_num)::text AS version_num
        FROM instrument_registry.alembic_version

        UNION ALL

        SELECT 'calculation_registry', count(*)::integer, min(version_num)::text
        FROM calculation_registry.alembic_version

        UNION ALL

        SELECT 'portfolio', count(*)::integer, min(version_num)::text
        FROM portfolio.alembic_version

        UNION ALL

        SELECT 'watchlist', count(*)::integer, min(version_num)::text
        FROM watchlist.alembic_version

        ORDER BY component
        """
    )
    actual_heads = {
        str(component): {
            "row_count": int(row_count),
            "version": None if version_num is None else str(version_num),
        }
        for component, row_count, version_num in cursor.fetchall()
    }
    head_mismatches = {
        component: actual_heads.get(component)
        for component, expected_version in EXPECTED_MIGRATION_HEADS.items()
        if actual_heads.get(component)
        != {"row_count": 1, "version": expected_version}
    }
    checks.append(
        AuditCheck(
            name="database_migration_heads",
            status="pass" if not head_mismatches else "fail",
            value=actual_heads,
            limit={
                component: {"row_count": 1, "version": version}
                for component, version in EXPECTED_MIGRATION_HEADS.items()
            },
            detail=(
                "Every managed schema must be at the exact application migration "
                "head before schema-dependent checks run."
            ),
        )
    )
    if head_mismatches:
        checks.extend(
            (
                _skip_check(
                    name="portfolio_runtime_required_objects",
                    detail=(
                        "The 0037 transaction-ledger capability check was skipped "
                        "because one or more managed schemas are at another head."
                    ),
                ),
                _skip_check(
                    name="portfolio_transaction_0037_database_authority",
                    detail=(
                        "The 0037 hash and deferred-transfer database-object check was "
                        "skipped because one or more schemas are at another head."
                    ),
                ),
                _skip_check(
                    name="database_schema_dependent_checks",
                    detail=(
                        "Schema-dependent checks were skipped after the exact-head "
                        "gate failed."
                    ),
                ),
            )
        )
        return checks, False

    ledger_objects = _count_check(
        cursor,
        name="portfolio_runtime_required_objects",
        query=PORTFOLIO_RUNTIME_OBJECTS_QUERY,
        detail=(
            "Portfolio requires the append-only transaction ledger, exact sealed "
            "Portfolio Daily inputs/outputs, dependency subscriptions, and no legacy "
            "mutable materialization tables."
        ),
    )
    checks.append(ledger_objects)
    if ledger_objects.status != "pass":
        checks.extend(
            (
                _skip_check(
                    name="portfolio_transaction_0037_database_authority",
                    detail=(
                        "The 0037 hash and deferred-transfer database-object check "
                        "requires the complete base ledger object set."
                    ),
                ),
                _skip_check(
                    name="database_schema_dependent_checks",
                    detail=(
                        "Schema-dependent checks were skipped because the exact 0037 "
                        "transaction-ledger object set is incomplete."
                    ),
                ),
            )
        )
        return checks, False
    database_authority = _count_check(
        cursor,
        name="portfolio_transaction_0037_database_authority",
        query=PORTFOLIO_TRANSACTION_0037_DATABASE_AUTHORITY_QUERY,
        detail=(
            "The 0037 PostgreSQL ledger requires exact canonical-hash functions, "
            "an enabled insert guard, and an initially-deferred transfer-pair constraint."
        ),
    )
    checks.append(database_authority)
    if database_authority.status != "pass":
        checks.append(
            _skip_check(
                name="database_schema_dependent_checks",
                detail=(
                    "Schema-dependent checks were skipped because the 0037 database "
                    "authority functions or triggers are incomplete."
                ),
            )
        )
        return checks, False
    return checks, True


def _mapping_rows(
    cursor: psycopg.Cursor[Any],
    query: str,
) -> list[dict[str, Any]]:
    cursor.execute(query)
    raw_rows = cursor.fetchall()
    if not raw_rows:
        return []
    if isinstance(raw_rows[0], Mapping):
        return [dict(row) for row in raw_rows]
    description = cursor.description or ()
    column_names = []
    for column in description:
        column_name = getattr(column, "name", None)
        if column_name is None:
            column_name = column[0]
        column_names.append(str(column_name))
    return [
        dict(zip(column_names, row, strict=True))
        for row in raw_rows
    ]


def _canonical_transaction_scalar(value: object, *, field_name: str) -> object:
    if isinstance(value, Decimal):
        if field_name not in TRANSACTION_DECIMAL_SCALES:
            raise ValueError(f"unexpected Decimal transaction field: {field_name}")
        if not value.is_finite():
            raise ValueError(f"{field_name} must be finite")
        if value.is_zero():
            return "0"
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        if value.tzinfo is not None:
            raise ValueError(f"{field_name} must be timezone-naive")
        return value.isoformat(timespec="microseconds")
    if value is None or isinstance(value, (str, bool, int, float, list, dict)):
        return value
    raise ValueError(f"cannot canonicalize transaction field: {field_name}")


def _recompute_transaction_payload_hash(revision: Mapping[str, object]) -> str:
    if bool(revision.get("is_tombstone")):
        body: dict[str, object] = {
            "payload_schema_version": TRANSACTION_PAYLOAD_SCHEMA_VERSION,
            "is_tombstone": True,
            "facts": None,
        }
    else:
        body = {
            "payload_schema_version": TRANSACTION_PAYLOAD_SCHEMA_VERSION,
            "is_tombstone": False,
            "facts": {
                field_name: _canonical_transaction_scalar(
                    revision.get(field_name),
                    field_name=field_name,
                )
                for field_name in TRANSACTION_FACT_FIELDS
            },
        }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _transaction_payload_hash_recomputation_check(
    revisions: Sequence[Mapping[str, object]],
) -> AuditCheck:
    violations = 0
    for revision in revisions:
        try:
            recomputed_hash = _recompute_transaction_payload_hash(revision)
        except (TypeError, ValueError):
            violations += 1
            continue
        if revision.get("payload_hash") != recomputed_hash:
            violations += 1
    return AuditCheck(
        name="portfolio_transaction_revision_payload_hash_recomputation",
        status="pass" if violations == 0 else "fail",
        value=violations,
        limit=0,
        detail=(
            "Every stored revision hash must equal an independent SHA-256 "
            "recomputation from persisted facts under the runtime canonical payload rules."
        ),
    )


def _transfer_group_key(
    row: Mapping[str, object],
) -> tuple[tuple[str, str], bool]:
    portfolio_id = str(row.get("portfolio_id") or "").strip()
    transaction_id = str(row.get("transaction_id") or "").strip()
    raw_group_id = row.get("transfer_group_id")
    group_id = str(raw_group_id or "").strip()
    if not group_id:
        return (portfolio_id, f"<missing>:{transaction_id}"), True
    return (portfolio_id, group_id), raw_group_id != group_id


def _transfer_pair_is_invalid(
    legs: Sequence[Mapping[str, object]],
    *,
    require_initial_revision: bool,
) -> bool:
    if len(legs) != 2:
        return True
    if len({str(leg.get("transaction_id") or "") for leg in legs}) != 2:
        return True
    if {leg.get("transaction_type") for leg in legs} != INTERNAL_TRANSFER_TYPES:
        return True
    if require_initial_revision:
        if any(
            leg.get("revision_number") != 1
            or leg.get("revision_kind") not in {"baseline", "create"}
            or bool(leg.get("is_tombstone"))
            for leg in legs
        ):
            return True
        if len({leg.get("revision_group_id") for leg in legs}) != 1:
            return True

    outbound = next(
        leg for leg in legs if leg.get("transaction_type") == "transfer_out"
    )
    inbound = next(
        leg for leg in legs if leg.get("transaction_type") == "transfer_in"
    )
    if (
        outbound.get("counterparty_account_id") != inbound.get("account_id")
        or inbound.get("counterparty_account_id") != outbound.get("account_id")
    ):
        return True
    return any(
        outbound.get(field_name) != inbound.get(field_name)
        for field_name in INTERNAL_TRANSFER_MIRRORED_FIELDS
    )


def _transaction_current_transfer_check(
    rows: Sequence[Mapping[str, object]],
) -> AuditCheck:
    groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    invalid_groups: set[tuple[str, str]] = set()
    for row in rows:
        if (
            row.get("transaction_type") not in INTERNAL_TRANSFER_TYPES
            and row.get("transfer_group_id") is None
        ):
            continue
        key, invalid_group_id = _transfer_group_key(row)
        groups.setdefault(key, []).append(row)
        if invalid_group_id:
            invalid_groups.add(key)
    for key, legs in groups.items():
        if _transfer_pair_is_invalid(legs, require_initial_revision=False):
            invalid_groups.add(key)
    return AuditCheck(
        name="portfolio_internal_transfer_current_integrity",
        status="pass" if not invalid_groups else "fail",
        value=len(invalid_groups),
        limit=0,
        detail=(
            "Every current internal-transfer group must contain exactly one in leg "
            "and one out leg with reciprocal accounts and identical mirrored facts."
        ),
    )


def _transaction_history_transfer_check(
    revisions: Sequence[Mapping[str, object]],
) -> AuditCheck:
    groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    invalid_groups: set[tuple[str, str]] = set()
    histories: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for revision in revisions:
        identity_key = (
            str(revision.get("portfolio_id") or "").strip(),
            str(revision.get("transaction_id") or "").strip(),
        )
        histories.setdefault(identity_key, []).append(revision)
        if (
            revision.get("transaction_type") not in INTERNAL_TRANSFER_TYPES
            and revision.get("transfer_group_id") is None
        ):
            continue
        group_key, invalid_group_id = _transfer_group_key(revision)
        groups.setdefault(group_key, []).append(revision)
        if invalid_group_id:
            invalid_groups.add(group_key)

    for group_key, legs in groups.items():
        if _transfer_pair_is_invalid(legs, require_initial_revision=True):
            invalid_groups.add(group_key)
            continue
        transaction_ids = {
            str(leg.get("transaction_id") or "").strip()
            for leg in legs
        }
        heads: list[Mapping[str, object]] = []
        invalid_history = False
        for transaction_id in transaction_ids:
            history = sorted(
                histories.get((group_key[0], transaction_id), ()),
                key=lambda item: int(item.get("revision_number") or 0),
            )
            if len(history) not in {1, 2}:
                invalid_history = True
                break
            if any(
                item.get("revision_kind") != "delete"
                or not bool(item.get("is_tombstone"))
                for item in history[1:]
            ):
                invalid_history = True
                break
            heads.append(history[-1])
        if invalid_history or len(heads) != 2:
            invalid_groups.add(group_key)
            continue
        tombstone_states = {bool(head.get("is_tombstone")) for head in heads}
        if len(tombstone_states) != 1:
            invalid_groups.add(group_key)
            continue
        if True in tombstone_states and len(
            {head.get("revision_group_id") for head in heads}
        ) != 1:
            invalid_groups.add(group_key)

    return AuditCheck(
        name="portfolio_internal_transfer_history_integrity",
        status="pass" if not invalid_groups else "fail",
        value=len(invalid_groups),
        limit=0,
        detail=(
            "Every historical internal-transfer group must be created once as one "
            "atomic mirrored pair, never reused or amended, and deleted only as one pair."
        ),
    )


def _database_url() -> str:
    raw_url = (
        os.getenv("PORTFOLIO_OPS_LOCAL_DATABASE_URL")
        or os.getenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )
    return raw_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _scalar(cursor: psycopg.Cursor[Any], query: str) -> Any:
    cursor.execute(query)
    row = cursor.fetchone()
    return None if row is None else row[0]


def _count_check(
    cursor: psycopg.Cursor[Any],
    *,
    name: str,
    query: str,
    detail: str,
    warning_only: bool = False,
) -> AuditCheck:
    value = int(_scalar(cursor, query) or 0)
    return AuditCheck(
        name=name,
        status="pass" if value == 0 else ("warning" if warning_only else "fail"),
        value=value,
        limit=0,
        detail=detail,
    )


def run_audit(database_url: str) -> list[AuditCheck]:
    checks: list[AuditCheck] = []
    with psycopg.connect(database_url, autocommit=False) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("SET LOCAL lock_timeout = '3s'")
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
            gate_checks, schema_ready = _schema_capability_gate(cursor)
            checks.extend(gate_checks)
            if not schema_ready:
                return checks
            checks.append(
                _count_check(
                    cursor,
                    name="database_runtime_cluster_privileges",
                    query="""
                        SELECT count(*)
                        FROM pg_roles
                        WHERE rolname = current_user
                          AND (
                              NOT rolcanlogin
                              OR rolsuper
                              OR rolcreatedb
                              OR rolcreaterole
                              OR rolreplication
                              OR rolbypassrls
                          )
                    """,
                    detail=(
                        "The application/migration role must be login-enabled but have "
                        "no cluster-wide superuser, database, role, replication, or "
                        "row-security bypass privileges."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="instrument_currency_validity",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument
                        WHERE currency !~ '^[A-Z]{3}$'
                    """,
                    detail=(
                        "Every canonical instrument must have an explicit "
                        "three-letter uppercase currency identity."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="database_project_ownership",
                    query="""
                        SELECT count(*)
                        FROM (
                            SELECT 'database' AS object_kind, current_database() AS object_name,
                                   owner.rolname AS owner_name
                            FROM pg_database database
                            JOIN pg_roles owner ON owner.oid = database.datdba
                            WHERE database.datname = current_database()

                            UNION ALL

                            SELECT 'schema', required.schema_name, owner.rolname
                            FROM (
                                VALUES
                                    ('calculation_registry'),
                                    ('instrument_registry'),
                                    ('portfolio'),
                                    ('watchlist')
                            ) AS required(schema_name)
                            LEFT JOIN pg_namespace namespace
                              ON namespace.nspname = required.schema_name
                            LEFT JOIN pg_roles owner ON owner.oid = namespace.nspowner
                        ) ownership
                        WHERE owner_name IS DISTINCT FROM current_user
                    """,
                    detail=(
                        "The project database and all four managed schemas must be "
                        "owned by the restricted application/migration role."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="database_project_object_ownership",
                    query="""
                        SELECT count(*)
                        FROM pg_class object
                        JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
                        JOIN pg_roles owner ON owner.oid = object.relowner
                        WHERE namespace.nspname IN (
                            'instrument_registry', 'calculation_registry',
                            'portfolio', 'watchlist'
                        )
                          AND object.relkind IN ('r', 'p', 'v', 'm', 'S')
                          AND owner.rolname <> current_user
                    """,
                    detail=(
                        "Managed tables, partitions, views, materialized views, and "
                        "sequences must not retain an obsolete administrator owner."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_uncurried_aum_surface",
                    query="""
                        SELECT count(*)
                        FROM (
                            SELECT 'read_model_column' AS violation
                            WHERE EXISTS (
                                SELECT 1
                                FROM information_schema.columns
                                WHERE table_schema = 'watchlist'
                                  AND table_name = 'watchlist_row_read_model'
                                  AND column_name = 'aum'
                            )

                            UNION ALL

                            SELECT 'field_registry:' || field_key
                            FROM watchlist.field_registry
                            WHERE field_key = 'aum'
                               OR source_metric_code =
                                  'watchlist_row_read_model.aum'

                            UNION ALL

                            SELECT 'view_column:' || watchlist_view_id
                            FROM watchlist.watchlist_view_column
                            WHERE field_key = 'aum'

                            UNION ALL

                            SELECT 'view_config:' || watchlist_view_id
                            FROM watchlist.watchlist_view
                            WHERE default_group_by = 'aum'
                               OR default_filters_json::jsonb ? 'aum'
                               OR jsonb_path_exists(
                                    default_sort_json::jsonb,
                                    '$[*] ? (@.field == "aum")'
                               )
                               OR jsonb_path_exists(
                                    default_advanced_filter_json::jsonb,
                                    '$.** ? (@.field == "aum")'
                               )
                        ) violations
                    """,
                    detail=(
                        "Watchlist must not expose the removed amount-only AUM field "
                        "through schema, field metadata, or persisted view settings."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_materialized_quote_lineage_is_bounded",
                    query=WATCHLIST_BOUNDED_READ_MODEL_LINEAGE_QUERY,
                    detail=(
                        "Watchlist chart, summary, performance, and risk read-model "
                        "quote resolutions may persist bounded counts, date ranges, "
                        "quality states, and dependency fingerprints, but not point/"
                        "observation collections or per-revision id/hash arrays."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_market_data_watermark_schema",
                    query="""
                        WITH expected(table_name) AS (
                            VALUES
                                ('instrument_summary_read_model'),
                                ('instrument_chart_read_model'),
                                ('instrument_performance_read_model'),
                                ('instrument_risk_read_model'),
                                ('performance_snapshot'),
                                ('risk_snapshot'),
                                ('watchlist_row_read_model')
                        ), missing_or_invalid AS (
                            SELECT expected.table_name
                            FROM expected
                            LEFT JOIN information_schema.columns column_record
                              ON column_record.table_schema = 'watchlist'
                             AND column_record.table_name = expected.table_name
                             AND column_record.column_name =
                                 'market_data_input_watermark_at'
                            WHERE column_record.column_name IS NULL
                               OR column_record.data_type <>
                                  'timestamp with time zone'
                               OR column_record.is_nullable <> 'YES'
                        ), obsolete_columns AS (
                            SELECT table_name
                            FROM information_schema.columns
                            WHERE table_schema = 'watchlist'
                              AND column_name IN (
                                  'source_cutoff_at', 'last_fact_update_at'
                              )
                        ), duplicate_fact_store AS (
                            SELECT 'nav_fact' AS table_name
                            WHERE to_regclass('watchlist.nav_fact') IS NOT NULL
                        )
                        SELECT count(*)
                        FROM (
                            SELECT table_name FROM missing_or_invalid
                            UNION ALL
                            SELECT table_name FROM obsolete_columns
                            UNION ALL
                            SELECT table_name FROM duplicate_fact_store
                        ) violations
                    """,
                    detail=(
                        "Watchlist read models and analytics snapshots must retain the "
                        "canonical nullable market-data input watermark; obsolete "
                        "watermark names and the duplicate NavFact store must be absent."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_current_snapshot_pair_integrity",
                    query="""
                        WITH performance AS (
                            SELECT *
                            FROM watchlist.performance_snapshot
                            WHERE is_current
                        ), risk AS (
                            SELECT *
                            FROM watchlist.risk_snapshot
                            WHERE is_current
                        )
                        SELECT count(*)
                        FROM performance
                        FULL JOIN risk USING (instrument_id)
                        WHERE performance.snapshot_id IS NULL
                           OR risk.snapshot_id IS NULL
                           OR performance.as_of_date <> risk.as_of_date
                           OR performance.market_data_input_watermark_at
                                IS DISTINCT FROM
                              risk.market_data_input_watermark_at
                           OR performance.market_data_input_watermark_at IS NULL
                           OR performance.calculated_at IS DISTINCT FROM
                              risk.calculated_at
                           OR coalesce(performance.methodology_version, '') = ''
                           OR coalesce(risk.methodology_version, '') = ''
                    """,
                    detail=(
                        "Current Watchlist performance and risk snapshots are one atomic "
                        "pair: both sides, as-of date, canonical watermark, and calculated-at "
                        "must align exactly."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_row_current_metric_integrity",
                    query="""
                        WITH performance AS (
                            SELECT *
                            FROM watchlist.performance_snapshot
                            WHERE is_current
                        ), risk AS (
                            SELECT *
                            FROM watchlist.risk_snapshot
                            WHERE is_current
                        )
                        SELECT count(*)
                        FROM watchlist.watchlist_row_read_model row_model
                        LEFT JOIN performance USING (instrument_id)
                        LEFT JOIN risk USING (instrument_id)
                        LEFT JOIN watchlist.instrument_performance_read_model
                            performance_model USING (instrument_id)
                        WHERE row_model.last_successful_snapshot_at IS DISTINCT FROM
                              CASE
                                  WHEN performance.snapshot_id IS NOT NULL
                                   AND risk.snapshot_id IS NOT NULL
                                   AND performance.as_of_date = risk.as_of_date
                                   AND performance.market_data_input_watermark_at
                                       IS NOT DISTINCT FROM
                                       risk.market_data_input_watermark_at
                                   AND performance.market_data_input_watermark_at
                                       IS NOT NULL
                                   AND performance.calculated_at IS NOT DISTINCT FROM
                                       risk.calculated_at
                                  THEN least(
                                      performance.calculated_at,
                                      risk.calculated_at
                                  )
                                  ELSE NULL
                              END
                           OR (
                                coalesce(
                                    performance_model.payload_json
                                        -> 'calculation_state'
                                        ->> 'current_endpoint_state',
                                    'unavailable'
                                ) <> 'resolved'
                                AND (
                                    row_model.return_ytd IS NOT NULL
                                    OR row_model.return_1w IS NOT NULL
                                    OR row_model.return_mtd IS NOT NULL
                                    OR row_model.return_1m IS NOT NULL
                                    OR row_model.return_1y IS NOT NULL
                                    OR row_model.annualized_return IS NOT NULL
                                    OR row_model.return_3y IS NOT NULL
                                    OR row_model.return_5y IS NOT NULL
                                    OR row_model.max_drawdown IS NOT NULL
                                    OR row_model.volatility IS NOT NULL
                                    OR row_model.sharpe_ratio IS NOT NULL
                                    OR row_model.attributes_json::jsonb ?| ARRAY[
                                        'peer_overall_percentile',
                                        'peer_return_percentile',
                                        'peer_risk_percentile',
                                        'peer_risk_adjusted_percentile',
                                        'peer_sample_count',
                                        'peer_group',
                                        'peer_return_1w_percentile',
                                        'peer_return_1m_percentile',
                                        'peer_return_ytd_percentile',
                                        'peer_return_1y_percentile',
                                        'peer_return_3y_percentile',
                                        'peer_return_5y_percentile',
                                        'peer_annualized_return_percentile',
                                        'peer_volatility_percentile',
                                        'peer_max_drawdown_percentile',
                                        'peer_sharpe_percentile',
                                        'peer_calmar_percentile'
                                    ]
                                )
                              )
                    """,
                    detail=(
                        "Watchlist rows must derive last-success from an aligned pair and "
                        "must clear current metrics and peer attributes whenever the current "
                        "total-return endpoint is not resolved."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_read_model_domain_freshness",
                    query="""
                        SELECT count(*)
                        FROM watchlist.instrument_summary_read_model summary
                        FULL JOIN watchlist.instrument_performance_read_model performance
                          USING (instrument_id)
                        FULL JOIN watchlist.instrument_risk_read_model risk
                          USING (instrument_id)
                        FULL JOIN watchlist.instrument_chart_read_model chart
                          USING (instrument_id)
                        WHERE summary.instrument_id IS NULL
                           OR performance.instrument_id IS NULL
                           OR risk.instrument_id IS NULL
                           OR chart.instrument_id IS NULL
                           OR summary.data_freshness_status IS DISTINCT FROM
                              performance.data_freshness_status
                           OR summary.data_freshness_status IS DISTINCT FROM
                              risk.data_freshness_status
                           OR CASE coalesce(
                                chart.payload_json -> 'calculation_state'
                                    ->> 'current_endpoint_state',
                                'unavailable'
                              )
                                  WHEN 'resolved' THEN
                                      chart.data_freshness_status NOT IN (
                                          'fresh', 'partial'
                                      )
                                  WHEN 'stale' THEN
                                      chart.data_freshness_status <> 'stale'
                                  WHEN 'partial' THEN
                                      chart.data_freshness_status <> 'partial'
                                  ELSE
                                      chart.data_freshness_status <> 'unavailable'
                              END
                    """,
                    detail=(
                        "Summary/performance/risk share the total-return domain state, "
                        "while chart freshness must follow its own chart endpoint rather "
                        "than inherit total-return freshness."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_peer_cohort_lineage",
                    query="""
                        WITH peer_models AS (
                            SELECT
                                instrument_id,
                                payload_json -> 'peer_comparison' AS peer,
                                payload_json -> 'ranking' AS ranking
                            FROM watchlist.instrument_performance_read_model
                        ), malformed AS (
                            SELECT instrument_id
                            FROM peer_models
                            WHERE peer ->> 'status' = 'ready'
                              AND (
                                  peer -> 'cohort' ->> 'coverage_status'
                                      <> 'qualified'
                                  OR peer -> 'cohort' ->> 'source_fingerprint'
                                      !~ '^sha256:[0-9a-f]{64}$'
                                  OR json_typeof(
                                      peer -> 'cohort' -> 'member_instrument_ids'
                                  ) IS DISTINCT FROM 'array'
                                  OR json_array_length(
                                      coalesce(
                                          peer -> 'cohort'
                                              -> 'member_instrument_ids',
                                          '[]'::json
                                      )
                                  )
                                      < 2
                                  OR json_typeof(peer -> 'metrics')
                                      IS DISTINCT FROM 'array'
                                  OR json_array_length(peer -> 'metrics') = 0
                                  OR json_typeof(ranking)
                                      IS DISTINCT FROM 'object'
                              )
                            UNION ALL
                            SELECT instrument_id
                            FROM peer_models
                            WHERE peer ->> 'status' = 'cohort_stale'
                              AND (
                                  json_array_length(
                                      coalesce(peer -> 'metrics', '[]'::json)
                                  ) <> 0
                                  OR coalesce(json_typeof(ranking), 'null')
                                      <> 'null'
                                  OR peer -> 'cohort' ->> 'coverage_status'
                                      <> 'stale'
                              )
                        ), split_generation AS (
                            SELECT min(instrument_id) AS instrument_id
                            FROM peer_models
                            WHERE peer ->> 'status' = 'ready'
                            GROUP BY
                                peer ->> 'peer_node_id',
                                peer -> 'cohort' ->> 'as_of_date',
                                peer -> 'cohort' ->> 'resolved_frequency',
                                peer -> 'cohort'
                                    ->> 'performance_methodology_version',
                                peer -> 'cohort'
                                    ->> 'risk_methodology_version'
                            HAVING count(DISTINCT (
                                peer -> 'cohort' ->> 'source_fingerprint'
                            )) > 1
                        )
                        SELECT count(*)
                        FROM (
                            SELECT instrument_id FROM malformed
                            UNION ALL
                            SELECT instrument_id FROM split_generation
                        ) violations
                    """,
                    detail=(
                        "Ready peer ranks require a qualified, fingerprinted cohort; "
                        "stale cohorts must expose no metrics, and one cohort identity "
                        "cannot have split source generations."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_schema_convergence",
                    query="""
                        SELECT count(*)
                        FROM (
                            SELECT table_name AS violation
                            FROM information_schema.tables
                            WHERE table_schema = 'portfolio'
                              AND table_name IN (
                                  'portfolio_daily_snapshot',
                                  'portfolio_daily_holding_snapshot',
                                  'portfolio_daily_contribution_slice',
                                  'portfolio_calculation_state',
                                  'research_settings_record',
                                  'research_run_record'
                              )

                            UNION ALL

                            SELECT table_name || '.' || column_name AS violation
                            FROM information_schema.columns
                            WHERE table_schema = 'portfolio'
                              AND table_name IN (
                                  'taxonomy_record',
                                  'taxonomy_assignment_record',
                                  'target_set_record'
                              )
                              AND column_name IN ('effective_from', 'effective_to')

                            UNION ALL

                            SELECT indexname
                            FROM pg_indexes
                            WHERE schemaname = 'portfolio'
                              AND indexname IN (
                                  'ix_portfolio_daily_holding_asset_date',
                                  'ix_target_set_record_taxonomy_scope_type_effective',
                                  'uq_taxonomy_assignment_active_target'
                              )

                            UNION ALL

                            SELECT table_name || '.' || column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'portfolio'
                              AND table_name = 'allocation_research_settings_record'
                              AND column_name = 'policy_replay_rebalance_frequency'
                              AND column_default IS NOT NULL
                        ) violations
                    """,
                    detail=(
                        "Portfolio must contain only the canonical current-state columns, "
                        "object names, and application-owned defaults."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_canonical_index_definitions",
                    query="""
                        WITH expected (
                            index_name, table_name, is_unique, column_names,
                            predicate_expression
                        ) AS (
                            VALUES
                                (
                                    'ix_pd_holding_portfolio_date',
                                    'portfolio_daily_holding_output', false,
                                    ARRAY['portfolio_id', 'as_of_date']::text[],
                                    NULL
                                ),
                                (
                                    'ix_transaction_revision_portfolio_trade',
                                    'transaction_revision_record', false,
                                    ARRAY[
                                        'portfolio_id', 'trade_date', 'trade_at',
                                        'transaction_id'
                                    ]::text[],
                                    'is_tombstoneisfalse'
                                ),
                                (
                                    'ix_transaction_revision_portfolio_account_trade',
                                    'transaction_revision_record', false,
                                    ARRAY[
                                        'portfolio_id', 'account_id',
                                        'trade_date', 'trade_at'
                                    ]::text[],
                                    'is_tombstoneisfalse'
                                ),
                                (
                                    'ix_transaction_revision_portfolio_type_trade',
                                    'transaction_revision_record', false,
                                    ARRAY[
                                        'portfolio_id', 'transaction_type',
                                        'trade_date', 'trade_at'
                                    ]::text[],
                                    'is_tombstoneisfalse'
                                ),
                                (
                                    'ix_transaction_revision_portfolio_instrument_trade',
                                    'transaction_revision_record', false,
                                    ARRAY[
                                        'portfolio_id', 'instrument_id',
                                        'trade_date', 'trade_at'
                                    ]::text[],
                                    'is_tombstoneisfalse'
                                ),
                                (
                                    'ix_transaction_revision_portfolio_counterparty_trade',
                                    'transaction_revision_record', false,
                                    ARRAY[
                                        'portfolio_id', 'counterparty_account_id',
                                        'trade_date', 'trade_at'
                                    ]::text[],
                                    'is_tombstoneisfalse'
                                ),
                                (
                                    'ix_target_set_record_taxonomy_scope_type',
                                    'target_set_record', false,
                                    ARRAY[
                                        'taxonomy_id', 'comparator_taxonomy_node_id',
                                        'target_set_type', 'target_set_id'
                                    ]::text[],
                                    NULL
                                ),
                                (
                                    'uq_taxonomy_assignment_target',
                                    'taxonomy_assignment_record', true,
                                    ARRAY[
                                        'taxonomy_id', 'target_scope',
                                        'target_entity_id'
                                    ]::text[],
                                    NULL
                                )
                        ), actual AS (
                            SELECT
                                index_object.relname AS index_name,
                                table_object.relname AS table_name,
                                index_record.indisunique AS is_unique,
                                ARRAY(
                                    SELECT attribute.attname::text
                                    FROM unnest(index_record.indkey)
                                        WITH ORDINALITY AS key(attnum, position)
                                    JOIN pg_attribute attribute
                                      ON attribute.attrelid = table_object.oid
                                     AND attribute.attnum = key.attnum
                                    WHERE key.attnum > 0
                                    ORDER BY key.position
                                ) AS column_names,
                                CASE
                                    WHEN index_record.indpred IS NULL THEN NULL
                                    ELSE regexp_replace(
                                        lower(pg_get_expr(
                                            index_record.indpred,
                                            index_record.indrelid
                                        )),
                                        '[()[:space:]]', '', 'g'
                                    )
                                END AS predicate_expression
                            FROM pg_index index_record
                            JOIN pg_class index_object
                              ON index_object.oid = index_record.indexrelid
                            JOIN pg_class table_object
                              ON table_object.oid = index_record.indrelid
                            JOIN pg_namespace namespace
                              ON namespace.oid = table_object.relnamespace
                            WHERE namespace.nspname = 'portfolio'
                        ), invalid_simple AS (
                            SELECT expected.index_name
                            FROM expected
                            LEFT JOIN actual USING (index_name)
                            WHERE actual.index_name IS NULL
                               OR actual.table_name <> expected.table_name
                               OR actual.is_unique <> expected.is_unique
                               OR actual.column_names <> expected.column_names
                               OR actual.predicate_expression
                                  IS DISTINCT FROM expected.predicate_expression
                        ), invalid_target_scope AS (
                            SELECT 'uq_target_set_active_scope' AS index_name
                            WHERE NOT EXISTS (
                                SELECT 1
                                FROM pg_indexes
                                WHERE schemaname = 'portfolio'
                                  AND tablename = 'target_set_record'
                                  AND indexname = 'uq_target_set_active_scope'
                                  AND indexdef ILIKE '%CREATE UNIQUE INDEX%'
                                  AND indexdef ILIKE '%taxonomy_id%'
                                  AND indexdef ILIKE '%COALESCE(comparator_taxonomy_node_id%'
                                  AND indexdef ILIKE '%target_set_type%'
                                  AND indexdef ILIKE '%WHERE%'
                                  AND indexdef ILIKE '%status%'
                                  AND indexdef ILIKE '%active%'
                            )
                        )
                        SELECT count(*)
                        FROM (
                            SELECT index_name FROM invalid_simple
                            UNION ALL
                            SELECT index_name FROM invalid_target_scope
                        ) invalid_indexes
                    """,
                    detail=(
                        "Portfolio canonical indexes must have the exact column order, "
                        "uniqueness, expression, and active-scope predicate."
                    ),
                )
            )
            transaction_revisions = _mapping_rows(
                cursor,
                PORTFOLIO_TRANSACTION_REVISION_AUDIT_QUERY,
            )
            checks.append(
                _transaction_payload_hash_recomputation_check(
                    transaction_revisions
                )
            )
            checks.append(
                _transaction_history_transfer_check(transaction_revisions)
            )
            checks.append(
                _transaction_current_transfer_check(
                    _mapping_rows(
                        cursor,
                        PORTFOLIO_TRANSACTION_CURRENT_TRANSFER_AUDIT_QUERY,
                    )
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_transaction_head_projection",
                    query=PORTFOLIO_TRANSACTION_HEAD_PROJECTION_QUERY,
                    detail=(
                        "Every transaction identity must have one latest revision; only a "
                        "latest non-tombstone revision may appear in transaction_current, "
                        "and that projection must exactly match its identity, group, and facts."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_transaction_revision_chain",
                    query=PORTFOLIO_TRANSACTION_REVISION_CHAIN_QUERY,
                    detail=(
                        "Revision numbers must be gapless from one, start with baseline/create, "
                        "never continue after a tombstone, and have nondecreasing recorded time."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_transaction_supersedes_chain",
                    query=PORTFOLIO_TRANSACTION_SUPERSEDES_QUERY,
                    detail=(
                        "Every amendment or deletion must name the exact immediately preceding "
                        "non-tombstone revision; an initial revision must supersede nothing."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_transaction_actor_reason_integrity",
                    query=PORTFOLIO_TRANSACTION_AUDIT_METADATA_QUERY,
                    detail=(
                        "Every used revision group requires bounded, non-blank actor provenance, "
                        "change reason, source kind, and server-recorded time."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_transaction_revision_payload_integrity",
                    query=PORTFOLIO_TRANSACTION_PAYLOAD_QUERY,
                    detail=(
                        "Revision payloads require the v1 schema, canonical SHA-256 shape, "
                        "complete live facts, exact empty tombstones, coherent instrument "
                        "snapshots, and a changed hash for amendments."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_transaction_decimal_column_contract",
                    query=PORTFOLIO_TRANSACTION_DECIMAL_COLUMNS_QUERY,
                    detail=(
                        "Revision storage and the current-fact view must expose raw NUMERIC "
                        "columns; each fact must satisfy its logical 38-digit domain and its "
                        "declared input-scale evidence without implicit rounding."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_transaction_append_only_triggers",
                    query=PORTFOLIO_TRANSACTION_APPEND_ONLY_TRIGGER_QUERY,
                    detail=(
                        "All three ledger tables require enabled UPDATE/DELETE/TRUNCATE guards, "
                        "and revision inserts require the enabled chain-transition trigger."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_allocation_policy_replay_metrics_contract",
                    query=PORTFOLIO_ALLOCATION_POLICY_REPLAY_METRICS_CONTRACT_QUERY,
                    detail=(
                        "Every persisted Allocation Policy Replay, benchmark, and relative "
                        "metric object must use the exact v3 methodology, carry a "
                        "strict and boundary-consistent 365-day history-reliability "
                        "contract, and withhold annualized return and Calmar Ratio "
                        "whenever that history is ineligible."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="external_cash_value_date_integrity",
                    query="""
                        SELECT count(*)
                        FROM portfolio.transaction_current
                        WHERE transaction_type IN ('deposit', 'withdrawal')
                          AND (
                              settlement_date IS NULL
                              OR trade_date IS NULL
                              OR settlement_date < trade_date
                          )
                    """,
                    detail=(
                        "External contributions and withdrawals require an explicit "
                        "cash value date on or after their instruction/trade date; "
                        "performance must never infer or silently omit that boundary."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_transaction_revision_instrument_foreign_key",
                    query=PORTFOLIO_TRANSACTION_INSTRUMENT_FK_QUERY,
                    detail=(
                        "transaction_revision_record.instrument_id must have exactly one named, "
                        "RESTRICT foreign key to canonical instrument identity."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="quote_series_identity_validity",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.quote_series
                        WHERE btrim(instrument_id) = ''
                           OR currency !~ '^[A-Z]{3}$'
                           OR NOT (
                               (metric_family = 'price' AND quote_basis IN (
                                   'last', 'close', 'adjusted_close',
                                   'clean_price', 'dirty_price', 'par'
                               ))
                               OR (metric_family = 'nav' AND quote_basis IN (
                                   'official_nav', 'total_return_nav',
                                   'cumulative_nav', 'accumulated_nav', 'cum_nav',
                                   'dividend_adjusted_nav', 'reinvested_nav'
                               ))
                               OR (metric_family = 'fx' AND quote_basis = 'spot')
                           )
                    """,
                    detail=(
                        "Every quote series must have one valid metric/basis mapping and "
                        "a normalized currency identity."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="market_data_invalid_values",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.quote_series series
                        JOIN instrument_registry.quote_observation observation
                          USING (quote_series_id)
                        JOIN instrument_registry.quote_observation_revision revision
                          USING (observation_id)
                        WHERE revision.is_current
                          AND revision.status <> 'withdrawn'
                          AND (
                              revision.value IS NULL
                              OR revision.value::text IN ('NaN', 'Infinity', '-Infinity')
                              OR revision.value <= 0
                          )
                    """,
                    detail="Price, NAV, and FX observations must be numeric and positive.",
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="market_data_currency_mismatch",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.quote_series series
                        JOIN instrument_registry.instrument instrument USING (instrument_id)
                        WHERE series.metric_family <> 'fx'
                          AND series.currency <> instrument.currency
                    """,
                    detail="Non-FX observations must use the instrument currency.",
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="quote_revision_lifecycle_validity",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.quote_observation_revision
                        WHERE revision_number <= 0
                           OR status NOT IN ('complete', 'partial', 'rejected', 'withdrawn')
                           OR (is_current AND superseded_at IS NOT NULL)
                           OR ((NOT is_current) AND superseded_at IS NULL)
                           OR (status = 'withdrawn' AND value IS NOT NULL)
                           OR (status <> 'withdrawn' AND value IS NULL)
                           OR payload_hash !~ '^sha256:[0-9a-f]{64}$'
                           OR (revision_number > 1 AND ingested_at IS NULL)
                    """,
                    detail=(
                        "Quote revisions must have valid state/value lifecycle, payload "
                        "identity, and a real ingestion timestamp after the legacy backfill."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="quote_revision_sequence_continuity",
                    query="""
                        SELECT count(*)
                        FROM (
                            SELECT observation_id
                            FROM instrument_registry.quote_observation_revision
                            GROUP BY observation_id
                            HAVING min(revision_number) <> 1
                                OR max(revision_number) <> count(*)
                        ) invalid_sequences
                    """,
                    detail=(
                        "Each observation revision chain must start at one and remain "
                        "gap-free."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="market_data_current_revision_cardinality",
                    query="""
                        SELECT count(*)
                        FROM (
                            SELECT observation.observation_id
                            FROM instrument_registry.quote_observation observation
                            LEFT JOIN instrument_registry.quote_observation_revision revision
                              ON revision.observation_id = observation.observation_id
                             AND revision.is_current
                            GROUP BY observation.observation_id
                            HAVING count(revision.revision_id) <> 1
                        ) invalid_observations
                    """,
                    detail="Every quote observation must have exactly one current revision.",
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="quote_selection_policy_shape_validity",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument instrument
                        WHERE EXISTS (
                            SELECT 1
                            FROM unnest(ARRAY[
                                'trading', 'valuation', 'total_return',
                                'chart', 'reference'
                            ]) AS roles(role_name)
                            WHERE coalesce(
                                json_typeof(
                                    instrument.quote_selection_policy_json
                                        -> roles.role_name
                                ),
                                'missing'
                            ) <> 'array'
                               OR (
                                   SELECT count(*)
                                   FROM json_array_elements_text(
                                       CASE
                                           WHEN json_typeof(
                                               instrument.quote_selection_policy_json
                                                   -> roles.role_name
                                           ) = 'array'
                                           THEN instrument.quote_selection_policy_json
                                               -> roles.role_name
                                           ELSE '[]'::json
                                       END
                                   ) basis(value)
                               ) <> (
                                   SELECT count(DISTINCT basis.value)
                                   FROM json_array_elements_text(
                                       CASE
                                           WHEN json_typeof(
                                               instrument.quote_selection_policy_json
                                                   -> roles.role_name
                                           ) = 'array'
                                           THEN instrument.quote_selection_policy_json
                                               -> roles.role_name
                                           ELSE '[]'::json
                                       END
                                   ) basis(value)
                               )
                        )
                    """,
                    detail=(
                        "Every quote-selection role must be a present array with no "
                        "duplicate basis entries."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="valuation_policy_total_return_basis",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument instrument
                        WHERE coalesce(instrument.lifecycle_state_json ->> 'status', 'active') = 'active'
                          AND EXISTS (
                              SELECT 1
                              FROM json_array_elements_text(
                                  CASE
                                      WHEN json_typeof(
                                          instrument.quote_selection_policy_json
                                              -> 'valuation'
                                      ) = 'array'
                                      THEN instrument.quote_selection_policy_json
                                          -> 'valuation'
                                      ELSE '[]'::json
                                  END
                              ) basis(value)
                              WHERE basis.value IN (
                                  'adjusted_close',
                                  'adjusted_nav',
                                  'adjusted_price',
                                  'accum_nav',
                                  'accumulated_nav',
                                  'cum_nav',
                                  'cumulative_nav',
                                  'dividend_adjusted_nav',
                                  'nav_with_dividend',
                                  'reinvested_nav',
                                  'split_adjusted_close',
                                  'total_return_nav',
                                  'total_return_price'
                              )
                          )
                    """,
                    detail=(
                        "Ledger valuation policies must use unadjusted tradable/NAV bases; "
                        "total-return bases would double-count distributions or share actions."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="canonical_fx_reference_identity",
                    query="""
                        WITH expected(
                            instrument_id, quote_currency, ticker
                        ) AS (
                            VALUES
                                ('fx-usd-hkd', 'HKD', 'USDHKD'),
                                ('fx-usd-cny', 'CNY', 'USDCNY')
                        )
                        SELECT count(*)
                        FROM expected
                        LEFT JOIN instrument_registry.instrument instrument
                          USING (instrument_id)
                        WHERE instrument.instrument_id IS NULL
                           OR instrument.instrument_type IS DISTINCT FROM 'fx'
                           OR instrument.currency IS DISTINCT FROM expected.quote_currency
                           OR (instrument.lifecycle_state_json ->> 'status')
                              IS DISTINCT FROM 'active'
                           OR instrument.quote_selection_policy_json::jsonb
                              IS DISTINCT FROM
                              '{
                                "trading": ["spot"],
                                "valuation": ["spot"],
                                "total_return": [],
                                "chart": ["spot"],
                                "reference": ["spot"]
                              }'::jsonb
                           OR NOT EXISTS (
                                SELECT 1
                                FROM instrument_registry.instrument_identifier identifier
                                WHERE identifier.instrument_id = expected.instrument_id
                                  AND identifier.identifier_type = 'ticker'
                                  AND identifier.identifier_value = expected.ticker
                                  AND identifier.is_primary
                           )
                    """,
                    detail=(
                        "The maintained USD/HKD and USD/CNY reference identities must "
                        "always exist with exact type, quote currency, active lifecycle, "
                        "strict role policy, and primary ticker. The migration does not "
                        "invent a market observation."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="canonical_fx_required_series_readiness",
                    query="""
                        WITH portfolio_currency AS (
                            SELECT portfolio_id, upper(base_currency) AS local_currency
                            FROM portfolio.portfolio_record
                            UNION
                            SELECT portfolio_id, upper(currency)
                            FROM portfolio.account_record
                            UNION
                            SELECT portfolio_id, upper(currency)
                            FROM portfolio.transaction_current
                            UNION
                            SELECT
                                universe.portfolio_id,
                                upper(instrument.currency)
                            FROM portfolio.portfolio_instrument_universe_record universe
                            JOIN instrument_registry.instrument instrument
                              USING (instrument_id)
                        ),
                        cross_currency AS (
                            SELECT DISTINCT
                                upper(portfolio.base_currency) AS base_currency,
                                currency.local_currency
                            FROM portfolio_currency currency
                            JOIN portfolio.portfolio_record portfolio
                              USING (portfolio_id)
                            WHERE currency.local_currency IN ('USD', 'HKD', 'CNY')
                              AND upper(portfolio.base_currency) IN ('USD', 'HKD', 'CNY')
                              AND currency.local_currency <> upper(portfolio.base_currency)
                        ),
                        required_leg(instrument_id, quote_currency) AS (
                            SELECT DISTINCT 'fx-usd-hkd', 'HKD'
                            FROM cross_currency
                            WHERE 'HKD' IN (base_currency, local_currency)
                            UNION
                            SELECT DISTINCT 'fx-usd-cny', 'CNY'
                            FROM cross_currency
                            WHERE 'CNY' IN (base_currency, local_currency)
                        )
                        SELECT count(*)
                        FROM required_leg required
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM instrument_registry.quote_series series
                            JOIN instrument_registry.quote_observation observation
                              USING (quote_series_id)
                            JOIN instrument_registry.quote_observation_revision revision
                              USING (observation_id)
                            WHERE series.instrument_id = required.instrument_id
                              AND series.metric_family = 'fx'
                              AND series.quote_basis = 'spot'
                              AND series.currency = required.quote_currency
                              AND revision.is_current
                              AND revision.status = 'complete'
                              AND revision.value IS NOT NULL
                        )
                    """,
                    detail=(
                        "Every USD pivot leg actually required by persisted Portfolio "
                        "accounts, transactions, holdings, or instrument universe must "
                        "have at least one adopted complete canonical spot observation. "
                        "Unused currencies do not block a single-currency installation."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="quote_selection_policy_basis_validity",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument instrument
                        WHERE EXISTS (
                            SELECT 1
                            FROM unnest(ARRAY[
                                'trading', 'valuation', 'total_return',
                                'chart', 'reference'
                            ]) AS roles(role_name)
                            CROSS JOIN LATERAL json_array_elements_text(
                                CASE
                                    WHEN json_typeof(
                                        instrument.quote_selection_policy_json
                                            -> roles.role_name
                                    ) = 'array'
                                    THEN instrument.quote_selection_policy_json
                                        -> roles.role_name
                                    ELSE '[]'::json
                                END
                            ) AS basis(value)
                            WHERE basis.value NOT IN (
                                'last', 'close', 'adjusted_close',
                                'official_nav', 'total_return_nav',
                                'cumulative_nav', 'accumulated_nav', 'cum_nav',
                                'dividend_adjusted_nav', 'reinvested_nav',
                                'spot', 'clean_price', 'dirty_price', 'par'
                            )
                        )
                    """,
                    detail=(
                        "Every quote-selection candidate must be a supported canonical "
                        "basis; unknown aliases cannot enter a role policy."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="cash_cumulative_nav_in_return_policy",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument instrument
                        WHERE coalesce(instrument.lifecycle_state_json ->> 'status', 'active') = 'active'
                          AND EXISTS (
                              SELECT 1
                              FROM unnest(ARRAY['total_return', 'chart'])
                                  AS roles(role_name)
                              CROSS JOIN LATERAL json_array_elements_text(
                                  CASE
                                      WHEN json_typeof(
                                          instrument.quote_selection_policy_json
                                              -> roles.role_name
                                      ) = 'array'
                                      THEN instrument.quote_selection_policy_json
                                          -> roles.role_name
                                      ELSE '[]'::json
                                  END
                              ) basis(value)
                              WHERE basis.value IN (
                                  'accum_nav',
                                  'accumulated_nav',
                                  'cum_nav',
                                  'cumulative_nav'
                              )
                          )
                    """,
                    detail=(
                        "Cash cumulative NAV is a disclosure value, not a dividend-reinvested "
                        "total-return series, and must not be selected for return/risk or "
                        "total-return chart analytics."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="non_total_return_basis_in_return_policy",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument instrument
                        WHERE coalesce(
                                instrument.lifecycle_state_json ->> 'status',
                                'active'
                              ) = 'active'
                          AND EXISTS (
                              SELECT 1
                              FROM json_array_elements_text(
                                  CASE
                                      WHEN json_typeof(
                                          instrument.quote_selection_policy_json
                                              -> 'total_return'
                                      ) = 'array'
                                      THEN instrument.quote_selection_policy_json
                                          -> 'total_return'
                                      ELSE '[]'::json
                                  END
                              ) basis(value)
                              WHERE basis.value NOT IN (
                                  'adjusted_close',
                                  'total_return_nav',
                                  'dividend_adjusted_nav',
                                  'reinvested_nav'
                              )
                          )
                    """,
                    detail=(
                        "The total_return role may only select an explicitly "
                        "distribution-adjusted/reinvested basis; raw NAV, close, spot, "
                        "and par belong to other roles."
                    ),
                )
            )

            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_daily_current_publication",
                    query="""
                        SELECT count(*)
                        FROM portfolio.portfolio_record portfolio
                        LEFT JOIN calculation_registry.calculation_current_publication current
                          ON current.calculation_kind = 'portfolio_daily'
                         AND current.scope_kind = 'portfolio'
                         AND current.scope_id = portfolio.portfolio_id
                        LEFT JOIN calculation_registry.calculation_publication publication
                          ON publication.publication_id = current.publication_id
                         AND publication.calculation_kind = current.calculation_kind
                         AND publication.scope_kind = current.scope_kind
                         AND publication.scope_id = current.scope_id
                        LEFT JOIN calculation_registry.calculation_run run
                          ON run.run_id = publication.run_id
                        LEFT JOIN calculation_registry.calculation_scope_generation generation
                          ON generation.calculation_kind = current.calculation_kind
                         AND generation.scope_kind = current.scope_kind
                         AND generation.scope_id = current.scope_id
                        LEFT JOIN portfolio.portfolio_daily_run_output output
                          ON output.run_id = publication.run_id
                         AND output.output_fencing_token = publication.published_fencing_token
                         AND output.portfolio_id = portfolio.portfolio_id
                        WHERE current.publication_id IS NULL
                           OR publication.publication_id IS NULL
                           OR run.run_id IS NULL
                           OR generation.scope_id IS NULL
                           OR output.run_id IS NULL
                           OR run.status <> 'published'
                           OR run.captured_generation <> generation.generation
                           OR run.published_output_hash IS DISTINCT FROM publication.canonical_output_hash
                           OR output.canonical_output_hash IS DISTINCT FROM publication.canonical_output_hash
                           OR publication.output_schema_version IS DISTINCT FROM run.output_schema_version
                           OR output.output_schema_version IS DISTINCT FROM run.output_schema_version
                           OR output.closure_status <> 'passed'
                           OR output.ledger_balance_residual_exact <> 0
                           OR output.nav_bridge_residual_exact <> 0
                           OR output.pnl_residual_exact <> 0
                           OR output.twr_residual_exact <> 0
                           OR output.lot_residual_exact <> 0
                    """,
                    detail=(
                        "Every portfolio must point to an immutable published run for "
                        "its current generation, with one hash-consistent, closure-passed "
                        "Portfolio Daily run output."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_daily_publication_output_counts",
                    query="""
                        WITH current_output AS (
                            SELECT output.*
                            FROM calculation_registry.calculation_current_publication current
                            JOIN calculation_registry.calculation_publication publication
                              ON publication.publication_id = current.publication_id
                             AND publication.calculation_kind = current.calculation_kind
                             AND publication.scope_kind = current.scope_kind
                             AND publication.scope_id = current.scope_id
                            JOIN portfolio.portfolio_daily_run_output output
                              ON output.run_id = publication.run_id
                             AND output.output_fencing_token = publication.published_fencing_token
                             AND output.portfolio_id = current.scope_id
                            WHERE current.calculation_kind = 'portfolio_daily'
                              AND current.scope_kind = 'portfolio'
                        )
                        SELECT count(*)
                        FROM current_output output
                        WHERE output.snapshot_count <> (
                                SELECT count(*)
                                FROM portfolio.portfolio_daily_snapshot_output row
                                WHERE row.run_id = output.run_id
                                  AND row.output_fencing_token = output.output_fencing_token
                                  AND row.portfolio_id = output.portfolio_id
                              )
                           OR output.holding_count <> (
                                SELECT count(*)
                                FROM portfolio.portfolio_daily_holding_output row
                                WHERE row.run_id = output.run_id
                                  AND row.output_fencing_token = output.output_fencing_token
                                  AND row.portfolio_id = output.portfolio_id
                              )
                           OR output.balance_count <> (
                                SELECT count(*)
                                FROM portfolio.portfolio_daily_balance_output row
                                WHERE row.run_id = output.run_id
                                  AND row.output_fencing_token = output.output_fencing_token
                                  AND row.portfolio_id = output.portfolio_id
                              )
                           OR output.lot_count <> (
                                SELECT count(*)
                                FROM portfolio.portfolio_daily_lot_output row
                                WHERE row.run_id = output.run_id
                                  AND row.output_fencing_token = output.output_fencing_token
                                  AND row.portfolio_id = output.portfolio_id
                              )
                           OR output.lot_disposition_count <> (
                                SELECT count(*)
                                FROM portfolio.portfolio_daily_lot_disposition_output row
                                WHERE row.run_id = output.run_id
                                  AND row.output_fencing_token = output.output_fencing_token
                                  AND row.portfolio_id = output.portfolio_id
                              )
                           OR output.contribution_count <> (
                                SELECT count(*)
                                FROM portfolio.portfolio_daily_contribution_output row
                                WHERE row.run_id = output.run_id
                                  AND row.output_fencing_token = output.output_fencing_token
                                  AND row.portfolio_id = output.portfolio_id
                              )
                    """,
                    detail=(
                        "The sealed current run's declared row counts must exactly match "
                        "every immutable Portfolio Daily output table."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_daily_method50_return_evidence",
                    query="""
                        SELECT count(*)
                        FROM calculation_registry.calculation_current_publication current
                        JOIN calculation_registry.calculation_publication publication
                          ON publication.publication_id = current.publication_id
                         AND publication.calculation_kind = current.calculation_kind
                         AND publication.scope_kind = current.scope_kind
                         AND publication.scope_id = current.scope_id
                        JOIN portfolio.portfolio_daily_snapshot_output snapshot
                          ON snapshot.run_id = publication.run_id
                         AND snapshot.output_fencing_token = publication.published_fencing_token
                         AND snapshot.portfolio_id = current.scope_id
                        WHERE current.calculation_kind = 'portfolio_daily'
                          AND current.scope_kind = 'portfolio'
                          AND (
                            snapshot.subperiod_twr_method50 < -1
                            OR (snapshot.subperiod_twr_method50 IS NULL)
                                <> (snapshot.subperiod_twr_published IS NULL)
                            OR (snapshot.subperiod_twr_method50 IS NULL)
                                <> (snapshot.wealth_chain_rounding_adjustment_exact IS NULL)
                            OR (snapshot.cumulative_twr_method50 IS NULL)
                                <> (snapshot.cumulative_twr_published IS NULL)
                            OR (snapshot.wealth_index_method50 IS NULL)
                                <> (snapshot.wealth_index_published IS NULL)
                            OR (snapshot.peak_wealth_index_method50 IS NULL)
                                <> (snapshot.peak_wealth_index_published IS NULL)
                            OR (snapshot.drawdown_method50 IS NULL)
                                <> (snapshot.drawdown_published IS NULL)
                            OR (snapshot.cumulative_twr_method50 IS NULL)
                                <> (snapshot.wealth_index_method50 IS NULL)
                            OR (snapshot.wealth_index_method50 IS NULL)
                                <> (snapshot.peak_wealth_index_method50 IS NULL)
                            OR (snapshot.peak_wealth_index_method50 IS NULL)
                                <> (snapshot.drawdown_method50 IS NULL)
                            OR (
                                snapshot.subperiod_twr_method50 IS NOT NULL
                                AND (
                                    snapshot.subperiod_twr_method50
                                        <> calculation_registry.round_significant_half_even(
                                            snapshot.subperiod_twr_method50,
                                            50
                                        )
                                    OR snapshot.subperiod_twr_published IS DISTINCT FROM
                                        calculation_registry.round_half_even(
                                            snapshot.subperiod_twr_method50,
                                            18
                                        )
                                )
                            )
                            OR (
                                snapshot.cumulative_twr_method50 IS NOT NULL
                                AND (
                                    snapshot.wealth_index_method50 IS NULL
                                    OR snapshot.peak_wealth_index_method50 IS NULL
                                    OR snapshot.drawdown_method50 IS NULL
                                    OR snapshot.wealth_index_method50 < 0
                                    OR snapshot.peak_wealth_index_method50 <= 0
                                    OR snapshot.peak_wealth_index_method50
                                        < snapshot.wealth_index_method50
                                    OR snapshot.wealth_index_method50
                                        <> calculation_registry.round_significant_half_even(
                                            snapshot.wealth_index_method50,
                                            50
                                        )
                                    OR snapshot.peak_wealth_index_method50
                                        <> calculation_registry.round_significant_half_even(
                                            snapshot.peak_wealth_index_method50,
                                            50
                                        )
                                    OR snapshot.cumulative_twr_method50
                                        <> calculation_registry.round_significant_half_even(
                                            snapshot.wealth_index_method50 - 1,
                                            50
                                        )
                                    OR snapshot.drawdown_method50
                                        <> calculation_registry.round_significant_half_even(
                                            calculation_registry.round_significant_half_even(
                                                snapshot.wealth_index_method50
                                                    - snapshot.peak_wealth_index_method50,
                                                50
                                            ) / snapshot.peak_wealth_index_method50,
                                            50
                                        )
                                    OR snapshot.cumulative_twr_published IS DISTINCT FROM
                                        calculation_registry.round_half_even(
                                            snapshot.cumulative_twr_method50,
                                            18
                                        )
                                    OR snapshot.wealth_index_published IS DISTINCT FROM
                                        calculation_registry.round_half_even(
                                            snapshot.wealth_index_method50,
                                            18
                                        )
                                    OR snapshot.peak_wealth_index_published IS DISTINCT FROM
                                        calculation_registry.round_half_even(
                                            snapshot.peak_wealth_index_method50,
                                            18
                                        )
                                    OR snapshot.drawdown_published IS DISTINCT FROM
                                        calculation_registry.round_half_even(
                                            snapshot.drawdown_method50,
                                            18
                                        )
                                )
                            )
                            OR (
                                NOT (
                                    (
                                        snapshot.calculation_status = 'calculated'
                                        AND snapshot.return_chain_status = 'active'
                                        AND snapshot.subperiod_twr_method50 IS NOT NULL
                                        AND snapshot.cumulative_twr_method50 IS NOT NULL
                                        AND snapshot.wealth_index_method50 IS NOT NULL
                                        AND snapshot.peak_wealth_index_method50 IS NOT NULL
                                        AND snapshot.drawdown_method50 IS NOT NULL
                                        AND snapshot.return_period_start_date IS NOT NULL
                                        AND snapshot.return_period_end_date IS NOT NULL
                                        AND snapshot.return_period_day_count IS NOT NULL
                                    ) OR (
                                        snapshot.calculation_status = 'reanchored'
                                        AND snapshot.return_chain_status = 'reanchor'
                                        AND snapshot.subperiod_twr_method50 IS NULL
                                        AND snapshot.wealth_chain_rounding_adjustment_exact IS NULL
                                        AND snapshot.cumulative_twr_method50 IS NOT NULL
                                        AND snapshot.cumulative_twr_method50 = 0
                                        AND snapshot.wealth_index_method50 IS NOT NULL
                                        AND snapshot.wealth_index_method50 = 1
                                        AND snapshot.peak_wealth_index_method50 IS NOT NULL
                                        AND snapshot.peak_wealth_index_method50 = 1
                                        AND snapshot.drawdown_method50 IS NOT NULL
                                        AND snapshot.drawdown_method50 = 0
                                        AND snapshot.reliable_anchor_date IS NOT NULL
                                        AND snapshot.reliable_anchor_nav_exact IS NOT NULL
                                        AND snapshot.reliable_anchor_nav IS NOT NULL
                                        AND snapshot.return_period_start_date IS NULL
                                        AND snapshot.return_period_end_date IS NULL
                                        AND snapshot.return_period_day_count IS NULL
                                    ) OR (
                                        snapshot.calculation_status = 'broken'
                                        AND snapshot.return_chain_status = 'broken'
                                        AND snapshot.subperiod_twr_method50 IS NULL
                                        AND snapshot.cumulative_twr_method50 IS NULL
                                        AND snapshot.wealth_index_method50 IS NULL
                                        AND snapshot.peak_wealth_index_method50 IS NULL
                                        AND snapshot.drawdown_method50 IS NULL
                                        AND snapshot.wealth_chain_rounding_adjustment_exact IS NULL
                                        AND snapshot.return_period_start_date IS NULL
                                        AND snapshot.return_period_end_date IS NULL
                                        AND snapshot.return_period_day_count IS NULL
                                    ) OR (
                                        snapshot.calculation_status
                                            = 'no_new_valuation'
                                        AND snapshot.return_chain_status = 'no_new_valuation'
                                        AND snapshot.subperiod_twr_method50 IS NULL
                                        AND snapshot.wealth_chain_rounding_adjustment_exact IS NULL
                                        AND snapshot.return_period_start_date IS NULL
                                        AND snapshot.return_period_end_date IS NULL
                                        AND snapshot.return_period_day_count IS NULL
                                        AND (
                                            (
                                                snapshot.reliable_anchor_date IS NULL
                                                AND snapshot.reliable_anchor_nav_exact IS NULL
                                                AND snapshot.reliable_anchor_nav IS NULL
                                                AND snapshot.cumulative_twr_method50 IS NULL
                                            ) OR (
                                                snapshot.reliable_anchor_date IS NOT NULL
                                                AND snapshot.reliable_anchor_nav_exact IS NOT NULL
                                                AND snapshot.reliable_anchor_nav IS NOT NULL
                                                AND snapshot.cumulative_twr_method50 IS NOT NULL
                                                AND snapshot.wealth_index_method50 > 0
                                            )
                                        )
                                    )
                                )
                            )
                          )
                    """,
                    detail=(
                        "Current method50 TWR evidence must stay inside the simple-return "
                        "domain and close method/published values, wealth-chain shape, "
                        "peak wealth, cumulative return and drawdown without binary-float "
                        "recomputation."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="taxonomy_target_sum",
                    query="""
                        WITH target_totals AS (
                            SELECT
                                target_set.target_set_id,
                                target_set.weight_enabled,
                                target_set.risk_budget_enabled,
                                sum(line.target_weight) AS weight_total,
                                sum(line.target_risk_share) AS risk_total
                            FROM portfolio.target_set_record target_set
                            LEFT JOIN portfolio.target_set_line_record line
                                USING (target_set_id)
                            WHERE target_set.status = 'active'
                            GROUP BY
                                target_set.target_set_id,
                                target_set.weight_enabled,
                                target_set.risk_budget_enabled
                        )
                        SELECT count(*)
                        FROM target_totals
                        WHERE (weight_enabled AND abs(coalesce(weight_total, 0) - 1) > 1e-8)
                           OR (risk_budget_enabled AND abs(coalesce(risk_total, 0) - 1) > 1e-8)
                    """,
                    detail="Each enabled target dimension must sum to 100% within its scope.",
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="taxonomy_negative_targets",
                    query="""
                        SELECT count(*)
                        FROM portfolio.target_set_line_record
                        WHERE target_weight < 0 OR target_risk_share < 0
                    """,
                    detail="Long-only planning targets may not contain negative shares.",
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="taxonomy_active_assignment_uniqueness",
                    query="""
                        SELECT count(*)
                        FROM (
                            SELECT taxonomy_id, target_scope, target_entity_id
                            FROM portfolio.taxonomy_assignment_record
                            GROUP BY taxonomy_id, target_scope, target_entity_id
                            HAVING count(*) > 1
                        ) duplicate_assignments
                    """,
                    detail=(
                        "The current-state taxonomy model permits exactly one mutable "
                        "assignment row per entity and taxonomy across all statuses."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="taxonomy_active_target_set_uniqueness",
                    query="""
                        SELECT count(*)
                        FROM (
                            SELECT
                                taxonomy_id,
                                coalesce(comparator_taxonomy_node_id, ''),
                                target_set_type
                            FROM portfolio.target_set_record
                            WHERE status = 'active'
                            GROUP BY
                                taxonomy_id,
                                coalesce(comparator_taxonomy_node_id, ''),
                                target_set_type
                            HAVING count(*) > 1
                        ) duplicate_target_sets
                    """,
                    detail=(
                        "Only one active TargetSet may govern each "
                        "taxonomy/comparator/type scope."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="current_unassigned_taxonomy_holdings",
                    query="""
                        WITH current_holdings AS (
                            SELECT DISTINCT
                                holding.portfolio_id,
                                holding.instrument_id
                            FROM calculation_registry.calculation_current_publication current
                            JOIN calculation_registry.calculation_publication publication
                              ON publication.publication_id = current.publication_id
                             AND publication.calculation_kind = current.calculation_kind
                             AND publication.scope_kind = current.scope_kind
                             AND publication.scope_id = current.scope_id
                            JOIN portfolio.portfolio_daily_holding_output holding
                              ON holding.run_id = publication.run_id
                             AND holding.output_fencing_token = publication.published_fencing_token
                             AND holding.portfolio_id = current.scope_id
                            WHERE current.calculation_kind = 'portfolio_daily'
                              AND current.scope_kind = 'portfolio'
                              AND holding.as_of_date = (
                                SELECT max(snapshot.as_of_date)
                                FROM portfolio.portfolio_daily_snapshot_output snapshot
                                WHERE snapshot.run_id = publication.run_id
                                  AND snapshot.output_fencing_token = publication.published_fencing_token
                                  AND snapshot.portfolio_id = current.scope_id
                              )
                              AND holding.quantity_exact <> 0
                        ), tracked_taxonomies AS (
                            SELECT taxonomy_id, portfolio_id
                            FROM portfolio.taxonomy_record
                            WHERE primary_assignment_scope = 'instrument'
                              AND status = 'active'
                        )
                        SELECT count(*)
                        FROM current_holdings holding
                        JOIN tracked_taxonomies taxonomy USING (portfolio_id)
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM portfolio.taxonomy_assignment_record assignment
                            WHERE assignment.taxonomy_id = taxonomy.taxonomy_id
                              AND assignment.target_scope = 'instrument'
                              AND assignment.target_entity_id = holding.instrument_id
                              AND assignment.status = 'active'
                        )
                    """,
                    detail=(
                        "Every current non-cash holding in any active instrument-scoped "
                        "taxonomy must have an active assignment. ETF holdings receive no "
                        "instrument-type exemption; portfolio-level exceptions must never "
                        "be inferred from their holdings."
                    ),
                )
            )

            checks.append(
                _count_check(
                    cursor,
                    name="corporate_action_event_integrity",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.corporate_action_event event
                        WHERE event.action_type <> 'share_split'
                           OR event.status NOT IN ('detected', 'confirmed', 'cancelled')
                           OR event.new_units !~ '^[0-9]+([.][0-9]+)?$'
                           OR event.old_units !~ '^[0-9]+([.][0-9]+)?$'
                           OR event.new_units::numeric <= 0
                           OR event.old_units::numeric <= 0
                           OR event.new_units::numeric = event.old_units::numeric
                           OR event.record_date > event.effective_date
                           OR event.quantity_rounding NOT IN (
                               'exact', 'truncate', 'round_half_up', 'cash_in_lieu'
                           )
                    """,
                    detail=(
                        "Corporate-action ratios, dates, status, and fractional-unit treatment "
                        "must be valid before an event can reach a portfolio ledger."
                    ),
                )
            )

            candidate_cte = """
                WITH adopted_quotes AS (
                    SELECT
                        series.instrument_id,
                        series.metric_family,
                        series.quote_basis,
                        series.currency,
                        observation.as_of_date,
                        revision.value
                    FROM instrument_registry.quote_series series
                    JOIN instrument_registry.quote_observation observation
                      USING (quote_series_id)
                    JOIN instrument_registry.quote_observation_revision revision
                      USING (observation_id)
                    WHERE revision.is_current
                      AND revision.status = 'complete'
                      AND revision.value IS NOT NULL
                ), first_trade AS (
                    SELECT transaction.instrument_id, min(transaction.trade_date) AS first_trade_date
                    FROM portfolio.transaction_current transaction
                    JOIN instrument_registry.instrument instrument
                      ON instrument.instrument_id = transaction.instrument_id
                    WHERE instrument.instrument_type IN ('etf', 'equity')
                    GROUP BY transaction.instrument_id
                ), quote_pairs AS (
                    SELECT
                        close_quote.instrument_id,
                        close_quote.as_of_date,
                        close_quote.value::numeric AS close_value,
                        adjusted_quote.value::numeric AS adjusted_value
                    FROM adopted_quotes close_quote
                    JOIN adopted_quotes adjusted_quote
                      ON adjusted_quote.instrument_id = close_quote.instrument_id
                     AND adjusted_quote.as_of_date = close_quote.as_of_date
                     AND adjusted_quote.currency = close_quote.currency
                     AND adjusted_quote.quote_basis = 'adjusted_close'
                    WHERE close_quote.quote_basis = 'close'
                ), changes AS (
                    SELECT
                        instrument_id,
                        as_of_date,
                        close_value,
                        adjusted_value,
                        lag(close_value) OVER (
                            PARTITION BY instrument_id ORDER BY as_of_date
                        ) AS previous_close,
                        lag(adjusted_value) OVER (
                            PARTITION BY instrument_id ORDER BY as_of_date
                        ) AS previous_adjusted,
                        lag(adjusted_value / nullif(close_value, 0)) OVER (
                            PARTITION BY instrument_id ORDER BY as_of_date
                        ) AS previous_adjustment_ratio
                    FROM quote_pairs
                ), candidates AS (
                    SELECT change.instrument_id, change.as_of_date
                    FROM changes change
                    JOIN first_trade USING (instrument_id)
                    WHERE change.as_of_date >= first_trade.first_trade_date
                      AND change.previous_close IS NOT NULL
                      AND change.previous_adjusted IS NOT NULL
                      AND change.previous_adjustment_ratio IS NOT NULL
                      AND abs(
                          (change.adjusted_value / nullif(change.close_value, 0))
                          / nullif(change.previous_adjustment_ratio, 0) - 1
                      ) >= 0.20
                      AND abs(change.close_value / nullif(change.previous_close, 0) - 1) >= 0.15
                      AND abs(change.adjusted_value / nullif(change.previous_adjusted, 0) - 1) <= 0.25
                )
            """
            confirmed_covered_count = int(
                _scalar(
                    cursor,
                    candidate_cte
                    + """
                        SELECT count(*)
                        FROM candidates candidate
                        WHERE EXISTS (
                            SELECT 1
                            FROM instrument_registry.corporate_action_event event
                            WHERE event.instrument_id = candidate.instrument_id
                              AND event.effective_date = candidate.as_of_date
                              AND event.action_type = 'share_split'
                              AND event.status = 'confirmed'
                        )
                    """,
                )
                or 0
            )
            checks.append(
                AuditCheck(
                    name="held_confirmed_share_split_events_covered",
                    status="pass",
                    value=confirmed_covered_count,
                    limit="informational",
                    detail=(
                        "Source factor/price discontinuities matched to issuer/exchange/CSD-confirmed "
                        "share events are covered by effective-date quantity and carry-cost ledger logic."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="held_detected_or_uncovered_share_adjustments",
                    query=candidate_cte
                    + """
                        SELECT count(*)
                        FROM candidates candidate
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM instrument_registry.corporate_action_event event
                            WHERE event.instrument_id = candidate.instrument_id
                              AND event.effective_date = candidate.as_of_date
                              AND event.action_type = 'share_split'
                              AND event.status = 'confirmed'
                        )
                    """,
                    detail=(
                        "A large adjusted/raw factor change plus an inverse raw-price move can be a "
                        "cash distribution, share split, consolidation, or another adjustment. It is "
                        "never posted automatically; issuer/exchange/CSD evidence must confirm ratio, "
                        "record/effective dates, and fractional-unit treatment."
                    ),
                    warning_only=True,
                )
            )

            checks.append(
                _count_check(
                    cursor,
                    name="listed_total_return_coverage",
                    query="""
                        WITH close_quotes AS (
                            SELECT
                                series.instrument_id,
                                series.currency,
                                observation.as_of_date,
                                observation.observation_id
                            FROM instrument_registry.quote_series series
                            JOIN instrument_registry.quote_observation observation
                              USING (quote_series_id)
                            JOIN instrument_registry.quote_observation_revision revision
                              USING (observation_id)
                            WHERE series.metric_family = 'price'
                              AND series.quote_basis = 'close'
                              AND revision.is_current
                              AND revision.status = 'complete'
                              AND revision.value IS NOT NULL
                        ),
                        adjusted_quotes AS (
                            SELECT
                                series.instrument_id,
                                series.currency,
                                observation.as_of_date,
                                observation.observation_id
                            FROM instrument_registry.quote_series series
                            JOIN instrument_registry.quote_observation observation
                              USING (quote_series_id)
                            JOIN instrument_registry.quote_observation_revision revision
                              USING (observation_id)
                            WHERE series.metric_family = 'price'
                              AND series.quote_basis = 'adjusted_close'
                              AND revision.is_current
                              AND revision.status = 'complete'
                              AND revision.value IS NOT NULL
                        )
                        SELECT count(DISTINCT instrument.instrument_id)
                        FROM instrument_registry.instrument instrument
                        JOIN close_quotes close_quote
                          ON close_quote.instrument_id = instrument.instrument_id
                        LEFT JOIN adjusted_quotes adjusted_quote
                          ON adjusted_quote.instrument_id = close_quote.instrument_id
                         AND adjusted_quote.as_of_date = close_quote.as_of_date
                         AND adjusted_quote.currency = close_quote.currency
                        WHERE instrument.instrument_type IN ('etf', 'equity')
                          AND (instrument.lifecycle_state_json ->> 'status') = 'active'
                          AND adjusted_quote.observation_id IS NULL
                    """,
                    detail=(
                        "Every complete listed-security close needs a same-date adjusted_close "
                        "before total-return risk and performance are considered complete."
                    ),
                    warning_only=True,
                )
            )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=_database_url())
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Return non-zero for warnings as well as failed integrity checks.",
    )
    args = parser.parse_args()

    try:
        checks = run_audit(args.database_url)
    except psycopg.Error as error:
        checks = [
            AuditCheck(
                name="database_audit_execution",
                status="fail",
                value={
                    "error_type": type(error).__name__,
                    "sqlstate": getattr(error, "sqlstate", None),
                    "message": str(error),
                },
                limit={"error": None},
                detail=(
                    "The read-only audit could not complete a database query; "
                    "the database is not safe to release."
                ),
            ),
            _skip_check(
                name="database_remaining_checks",
                detail=(
                    "Remaining checks were skipped after a database execution failure."
                ),
            ),
        ]
    failed = [check for check in checks if check.status == "fail"]
    warnings = [check for check in checks if check.status == "warning"]
    skipped = [check for check in checks if check.status == "skip"]
    payload = {
        "status": "failed" if failed else ("warning" if warnings else "passed"),
        "failed_count": len(failed),
        "warning_count": len(warnings),
        "skipped_count": len(skipped),
        "checks": [asdict(check) for check in checks],
    }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        for check in checks:
            print(
                f"{check.status.upper():7} {check.name}: "
                f"value={check.value!r} limit={check.limit!r} — {check.detail}"
            )
        print(
            f"Result: {payload['status']} "
            f"({len(checks)} checks, {len(failed)} failed, "
            f"{len(warnings)} warnings, {len(skipped)} skipped)"
        )

    if failed or (args.fail_on_warning and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
