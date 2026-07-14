"""PostgreSQL storage contract for exact Portfolio Daily calculations.

This metadata is deliberately independent from the legacy application ORM.  It
describes sealed, typed inputs and attempt-isolated immutable outputs; it is not
used as a mutable read model.  JSONB columns below preserve canonical source
snapshots only.  Authoritative financial amounts, bounded precision-50 method
values, and exact rounding evidence are all typed ``NUMERIC``.  No cumulative
decimal prefix is stored as text.
"""

from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from portfolio_app.calculations.numeric import (
    ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
    ACCOUNTING_EVIDENCE_STORAGE_SCALE,
    EXACT_QUANTITY_STORAGE_PRECISION,
    EXACT_QUANTITY_STORAGE_SCALE,
    METHOD_DECIMAL_STORAGE_PRECISION,
    METHOD_DECIMAL_STORAGE_SCALE,
    METHOD_EVIDENCE_STORAGE_PRECISION,
    METHOD_EVIDENCE_STORAGE_SCALE,
    DERIVED_RATE_STORAGE_PRECISION,
    DERIVED_RATE_STORAGE_SCALE,
    METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION,
    METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE,
    SOURCE_PRICE_STORAGE_PRECISION,
    SOURCE_PRICE_STORAGE_SCALE,
    SOURCE_RATE_STORAGE_PRECISION,
    SOURCE_RATE_STORAGE_SCALE,
)


PORTFOLIO_SCHEMA = "portfolio"
CALCULATION_SCHEMA = "calculation_registry"
INSTRUMENT_SCHEMA = "instrument_registry"

# Publication values have fixed decimal scales and enough integer headroom for
# portfolio aggregation.  Every pre-publication semantic domain is physically
# unbounded PostgreSQL NUMERIC with an explicit exact-fit CHECK: a NUMERIC(p,s)
# typmod rounds before CHECK evaluation and therefore cannot fail closed.
MONEY = Numeric(50, 8)
QUANTITY = Numeric(50, 12)
PRICE = Numeric(50, 12)
RATE = Numeric(50, 18)
METHOD = Numeric()
METHOD_ROUNDING_ADJUSTMENT = Numeric()
SOURCE_PRICE = Numeric()
SOURCE_RATE = Numeric()
EXACT_QUANTITY = Numeric()
DERIVED_RATE_EVIDENCE = Numeric()
ACCOUNTING_EVIDENCE = Numeric()
METHOD_EVIDENCE = Numeric()
RAW_CANONICAL_NUMERIC = Numeric()
REASON_CODES = ARRAY(String(64), dimensions=1)


def _logical_numeric_domain(
    column_name: str,
    *,
    precision: int,
    scale: int,
) -> str:
    """Return a CHECK equivalent to NUMERIC(p,s), without typmod rounding."""

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


portfolio_daily_metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def _manifest_fk(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["manifest_id", "run_id"],
        [
            f"{CALCULATION_SCHEMA}.calculation_input_manifest.manifest_id",
            f"{CALCULATION_SCHEMA}.calculation_input_manifest.run_id",
        ],
        name=name,
        ondelete="RESTRICT",
    )


def _run_fk(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["run_id"],
        [f"{CALCULATION_SCHEMA}.calculation_run.run_id"],
        name=name,
        ondelete="RESTRICT",
    )


def _portfolio_fk(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["portfolio_id"],
        [f"{PORTFOLIO_SCHEMA}.portfolio_record.portfolio_id"],
        name=name,
        ondelete="RESTRICT",
    )


def _dependency_identity() -> tuple[Column[object], ...]:
    return (
        Column("manifest_id", UUID(as_uuid=True), nullable=False),
        Column("run_id", UUID(as_uuid=True), nullable=False),
        Column("portfolio_id", String(255), nullable=False),
        Column(
            "captured_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("clock_timestamp()"),
        ),
    )


def _output_identity() -> tuple[Column[object], ...]:
    return (
        Column("run_id", UUID(as_uuid=True), nullable=False),
        Column("output_fencing_token", BigInteger, nullable=False),
        Column("worker_id", String(255), nullable=False),
        Column("portfolio_id", String(255), nullable=False),
        Column(
            "calculated_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("clock_timestamp()"),
        ),
    )


def _coverage_columns() -> tuple[Column[object], ...]:
    return (
        Column("coverage_state", String(16), nullable=False),
        Column(
            "reason_codes",
            REASON_CODES,
            nullable=False,
            server_default=text("ARRAY[]::varchar(64)[]"),
        ),
    )


def _coverage_constraints() -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint(
            "coverage_state IN ('complete', 'partial', 'unavailable')",
            name="coverage_state",
        ),
        CheckConstraint(
            "(coverage_state = 'complete' AND cardinality(reason_codes) = 0) "
            "OR (coverage_state <> 'complete' AND cardinality(reason_codes) > 0)",
            name="coverage_reasons",
        ),
    )


portfolio_daily_config_input = Table(
    "portfolio_daily_config_input",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("effective_as_of", Date, nullable=False),
    Column("range_start", Date, nullable=False),
    Column(
        "knowledge_cutoff_at",
        DateTime(timezone=True),
        nullable=False,
        comment="Knowledge-time cutoff for facts eligible for this manifest; not the economic valuation boundary.",
    ),
    Column("base_currency", String(3), nullable=False),
    Column("valuation_timezone", String(64), nullable=False),
    Column("valuation_cutoff_local_time", Time(timezone=False), nullable=False),
    Column("valuation_cutoff_policy", String(64), nullable=False),
    Column("valuation_calendar_id", String(128), nullable=False),
    Column("valuation_calendar_version", String(64), nullable=False),
    Column("quote_policy_version", String(64), nullable=False),
    Column("quote_selection_policy_revision", String(128), nullable=False),
    Column("freshness_policy_version", String(64), nullable=False),
    Column("freshness_mode", String(32), nullable=False),
    Column("freshness_max_age_days", Integer, nullable=False),
    Column("quote_resolver_strategy_version", String(64), nullable=False),
    Column("fx_policy_version", String(64), nullable=False),
    Column("corporate_action_policy_version", String(64), nullable=False),
    Column("taxonomy_id", String(255)),
    Column("taxonomy_version", String(64)),
    Column("benchmark_id", String(255)),
    Column("operating_profile", String(64), nullable=False),
    Column("config_schema_version", String(64), nullable=False),
    Column("config_hash", CHAR(64), nullable=False),
    Column("canonical_config", JSONB, nullable=False),
    PrimaryKeyConstraint("manifest_id", name="pk_pd_config_input"),
    UniqueConstraint("manifest_id", "run_id", name="uq_pd_config_manifest_run"),
    _manifest_fk("fk_pd_config_manifest_run"),
    _portfolio_fk("fk_pd_config_portfolio"),
    CheckConstraint("base_currency ~ '^[A-Z]{3}$'", name="currency"),
    CheckConstraint("config_hash ~ '^[0-9a-f]{64}$'", name="hash"),
    CheckConstraint("jsonb_typeof(canonical_config) = 'object'", name="snapshot"),
    CheckConstraint("freshness_max_age_days >= 0", name="freshness"),
    CheckConstraint("range_start <= effective_as_of", name="range"),
    CheckConstraint(
        "quote_selection_policy_revision ~ '^sha256:[0-9a-f]{64}$'",
        name="quote_selection_revision",
    ),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_account_input = Table(
    "portfolio_daily_account_input",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("account_id", String(255), nullable=False),
    Column("account_name", String(255), nullable=False),
    Column("account_type", String(64), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("institution", String(255)),
    Column("default_settlement_cash_account_id", String(255)),
    Column("cost_basis_method", String(32)),
    Column("opened_at", Date),
    Column("closed_at", Date),
    Column("account_status", String(32), nullable=False),
    Column("account_schema_version", String(64), nullable=False),
    Column("account_hash", CHAR(64), nullable=False),
    Column("canonical_account", JSONB, nullable=False),
    PrimaryKeyConstraint("manifest_id", "account_id", name="pk_pd_account_input"),
    _manifest_fk("fk_pd_account_manifest_run"),
    _portfolio_fk("fk_pd_account_portfolio"),
    ForeignKeyConstraint(
        ["portfolio_id", "account_id"],
        [
            f"{PORTFOLIO_SCHEMA}.account_record.portfolio_id",
            f"{PORTFOLIO_SCHEMA}.account_record.account_id",
        ],
        name="fk_pd_account_current_identity",
        ondelete="RESTRICT",
    ),
    CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
    CheckConstraint("account_hash ~ '^[0-9a-f]{64}$'", name="hash"),
    CheckConstraint("jsonb_typeof(canonical_account) = 'object'", name="snapshot"),
    CheckConstraint(
        "closed_at IS NULL OR opened_at IS NULL OR closed_at >= opened_at", name="dates"
    ),
    CheckConstraint(
        "cost_basis_method IS NOT NULL OR account_type IN "
        "('cash', 'settlement_cash', 'deposit', 'deposit_account', 'bank', 'custody_cash')",
        name="cost_method",
    ),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_transaction_input = Table(
    "portfolio_daily_transaction_input",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("transaction_id", String(255), nullable=False),
    Column("revision_id", String(255), nullable=False),
    Column("revision_number", Integer, nullable=False),
    Column("revision_group_id", String(255), nullable=False),
    Column("group_recorded_at", DateTime(timezone=True), nullable=False),
    Column("revision_kind", String(16), nullable=False),
    Column("is_tombstone", Boolean, nullable=False),
    Column("supersedes_revision_id", String(255)),
    Column("supersedes_revision_number", Integer),
    Column("payload_schema_version", String(64), nullable=False),
    Column("payload_hash", String(71), nullable=False),
    Column("transaction_type", String(64)),
    Column("trade_date", Date),
    Column("trade_time", Time(timezone=False)),
    Column("trade_at", DateTime(timezone=True)),
    Column("trade_timezone", String(64)),
    Column("trade_time_is_estimated", Boolean),
    Column("settlement_date", Date),
    Column("entitlement_date", Date),
    Column("acquisition_date", Date),
    Column("account_id", String(255)),
    Column("settlement_cash_account_id", String(255)),
    Column("instrument_id", String(255)),
    # A cash transaction has no instrument snapshot.  PostgreSQL JSONB maps
    # Python None to JSON null by default, which is distinct from the source
    # ledger's SQL NULL and therefore violates exact lineage.  Preserve SQL
    # NULL explicitly at this typed fact boundary.
    Column("instrument_snapshot_json", JSONB(none_as_null=True)),
    Column("quantity", RAW_CANONICAL_NUMERIC),
    Column("price", RAW_CANONICAL_NUMERIC),
    Column("gross_amount", RAW_CANONICAL_NUMERIC),
    Column("counter_amount", RAW_CANONICAL_NUMERIC),
    Column("quoted_fx_rate", RAW_CANONICAL_NUMERIC),
    Column("fees", RAW_CANONICAL_NUMERIC),
    Column("taxes", RAW_CANONICAL_NUMERIC),
    Column("consideration_basis", String(32)),
    Column("numeric_scale_state", String(24)),
    Column("quantity_input_scale", Integer),
    Column("price_input_scale", Integer),
    Column("gross_amount_input_scale", Integer),
    Column("counter_amount_input_scale", Integer),
    Column("quoted_fx_rate_input_scale", Integer),
    Column("fees_input_scale", Integer),
    Column("taxes_input_scale", Integer),
    Column("consideration_evidence_state", String(16), nullable=False),
    Column("consideration_evidence_reason_codes", REASON_CODES, nullable=False),
    Column("consideration_terms_difference_exact", RAW_CANONICAL_NUMERIC),
    Column("fx_evidence_state", String(16), nullable=False),
    Column("fx_evidence_reason_codes", REASON_CODES, nullable=False),
    Column("effective_fx_rate_method50", METHOD),
    Column("quoted_terms_difference_exact", RAW_CANONICAL_NUMERIC),
    Column("currency", String(3)),
    Column("transfer_scope", String(64)),
    Column("transfer_object_type", String(32)),
    Column("transfer_group_id", String(255)),
    Column("counterparty_account_id", String(255)),
    Column("note", Text),
    Column("selected_reason_code", String(64), nullable=False),
    PrimaryKeyConstraint(
        "manifest_id", "transaction_id", name="pk_pd_transaction_input"
    ),
    _manifest_fk("fk_pd_transaction_manifest_run"),
    _portfolio_fk("fk_pd_transaction_portfolio"),
    ForeignKeyConstraint(
        ["portfolio_id", "transaction_id", "revision_number", "revision_id"],
        [
            f"{PORTFOLIO_SCHEMA}.transaction_revision_record.portfolio_id",
            f"{PORTFOLIO_SCHEMA}.transaction_revision_record.transaction_id",
            f"{PORTFOLIO_SCHEMA}.transaction_revision_record.revision_number",
            f"{PORTFOLIO_SCHEMA}.transaction_revision_record.revision_id",
        ],
        name="fk_pd_transaction_exact_revision",
        ondelete="RESTRICT",
    ),
    CheckConstraint("revision_number > 0", name="revision_number"),
    CheckConstraint(
        "(revision_number = 1 AND supersedes_revision_id IS NULL AND supersedes_revision_number IS NULL) OR (revision_number > 1 AND supersedes_revision_id IS NOT NULL AND supersedes_revision_number = revision_number - 1)",
        name="revision_chain",
    ),
    CheckConstraint("payload_hash ~ '^sha256:[0-9a-f]{64}$'", name="payload_hash"),
    CheckConstraint(
        "numeric_scale_state IS NULL OR numeric_scale_state IN "
        "('declared', 'legacy_inferred')",
        name="numeric_scale_state",
    ),
    CheckConstraint(
        "consideration_basis IS NULL OR consideration_basis IN "
        "('exact_quantity_price', 'source_reported')",
        name="consideration_basis",
    ),
    CheckConstraint(
        "((quantity IS NULL AND quantity_input_scale IS NULL) OR "
        "(quantity IS NOT NULL AND quantity_input_scale IS NOT NULL AND quantity_input_scale BETWEEN 0 AND 12)) "
        "AND ((price IS NULL AND price_input_scale IS NULL) OR "
        "(price IS NOT NULL AND price_input_scale IS NOT NULL AND price_input_scale BETWEEN 0 AND 12)) "
        "AND ((gross_amount IS NULL AND gross_amount_input_scale IS NULL) OR "
        "(gross_amount IS NOT NULL AND gross_amount_input_scale IS NOT NULL AND gross_amount_input_scale BETWEEN 0 AND 8)) "
        "AND ((counter_amount IS NULL AND counter_amount_input_scale IS NULL) OR "
        "(counter_amount IS NOT NULL AND counter_amount_input_scale IS NOT NULL AND counter_amount_input_scale BETWEEN 0 AND 8)) "
        "AND ((quoted_fx_rate IS NULL AND quoted_fx_rate_input_scale IS NULL) OR "
        "(quoted_fx_rate IS NOT NULL AND quoted_fx_rate_input_scale IS NOT NULL AND quoted_fx_rate_input_scale BETWEEN 0 AND 18)) "
        "AND ((fees IS NULL AND fees_input_scale IS NULL) OR "
        "(fees IS NOT NULL AND fees_input_scale IS NOT NULL AND fees_input_scale BETWEEN 0 AND 8)) "
        "AND ((taxes IS NULL AND taxes_input_scale IS NULL) OR "
        "(taxes IS NOT NULL AND taxes_input_scale IS NOT NULL AND taxes_input_scale BETWEEN 0 AND 8))",
        name="input_scale_pairing",
    ),
    CheckConstraint(
        "(consideration_evidence_state = 'complete' "
        "AND consideration_terms_difference_exact IS NOT NULL "
        "AND cardinality(consideration_evidence_reason_codes) = 0) OR "
        "(consideration_evidence_state = 'unavailable' "
        "AND consideration_terms_difference_exact IS NULL "
        "AND cardinality(consideration_evidence_reason_codes) > 0) OR "
        "(consideration_evidence_state = 'not_applicable' "
        "AND consideration_terms_difference_exact IS NULL "
        "AND cardinality(consideration_evidence_reason_codes) = 0)",
        name="consideration_evidence",
    ),
    CheckConstraint(
        "(consideration_basis = 'exact_quantity_price' "
        "AND consideration_evidence_state = 'complete' "
        "AND consideration_terms_difference_exact = 0) OR "
        "(consideration_basis = 'source_reported' "
        "AND consideration_evidence_state IN ('complete', 'unavailable')) OR "
        "(consideration_basis IS NULL "
        "AND consideration_evidence_state = 'not_applicable')",
        name="consideration_basis_evidence",
    ),
    CheckConstraint(
        "(fx_evidence_state = 'complete' "
        "AND effective_fx_rate_method50 IS NOT NULL "
        "AND quoted_terms_difference_exact IS NOT NULL "
        "AND cardinality(fx_evidence_reason_codes) = 0) OR "
        "(fx_evidence_state = 'partial' "
        "AND effective_fx_rate_method50 IS NOT NULL "
        "AND quoted_terms_difference_exact IS NULL "
        "AND cardinality(fx_evidence_reason_codes) > 0) OR "
        "(fx_evidence_state = 'unavailable' "
        "AND effective_fx_rate_method50 IS NULL "
        "AND quoted_terms_difference_exact IS NULL "
        "AND cardinality(fx_evidence_reason_codes) > 0) OR "
        "(fx_evidence_state = 'not_applicable' "
        "AND effective_fx_rate_method50 IS NULL "
        "AND quoted_terms_difference_exact IS NULL "
        "AND cardinality(fx_evidence_reason_codes) = 0)",
        name="fx_evidence",
    ),
    CheckConstraint(
        "(transaction_type = 'fx_conversion' "
        "AND fx_evidence_state IN ('complete', 'partial', 'unavailable')) OR "
        "(transaction_type IS DISTINCT FROM 'fx_conversion' "
        "AND fx_evidence_state = 'not_applicable')",
        name="fx_evidence_applicability",
    ),
    CheckConstraint(
        "transaction_type IS DISTINCT FROM 'fx_conversion' "
        "OR fx_evidence_state = 'unavailable' "
        "OR (gross_amount > 0 AND counter_amount > 0 "
        "AND effective_fx_rate_method50 = calculation_registry."
        "round_significant_half_even(counter_amount / gross_amount, 50) "
        "AND ((quoted_fx_rate IS NULL AND fx_evidence_state = 'partial') "
        "OR (quoted_fx_rate IS NOT NULL AND fx_evidence_state = 'complete' "
        "AND quoted_terms_difference_exact = "
        "counter_amount - gross_amount * quoted_fx_rate)))",
        name="fx_evidence_arithmetic",
    ),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_instrument_input = Table(
    "portfolio_daily_instrument_input",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("instrument_id", String(255), nullable=False),
    Column("requires_valuation", Boolean, nullable=False),
    Column("instrument_name", String(500), nullable=False),
    Column("instrument_type", String(64), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("price_unit", String(64)),
    Column("contract_multiplier", EXACT_QUANTITY),
    Column("accrual_convention", String(64)),
    Column("price_factor", EXACT_QUANTITY),
    Column("valuation_contract_state", String(16), nullable=False),
    Column("valuation_contract_reason_codes", REASON_CODES, nullable=False),
    Column("valuation_factor_source", String(32)),
    Column("quote_policy_version", String(64), nullable=False),
    Column("quote_selection_policy_revision", String(128), nullable=False),
    Column("freshness_policy_version", String(64), nullable=False),
    Column("freshness_mode", String(32), nullable=False),
    Column("freshness_max_age_days", Integer, nullable=False),
    Column("resolver_strategy_version", String(64), nullable=False),
    Column("instrument_schema_version", String(64), nullable=False),
    Column("instrument_hash", CHAR(64), nullable=False),
    Column("canonical_instrument", JSONB, nullable=False),
    PrimaryKeyConstraint("manifest_id", "instrument_id", name="pk_pd_instrument_input"),
    _manifest_fk("fk_pd_instrument_manifest_run"),
    _portfolio_fk("fk_pd_instrument_portfolio"),
    CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
    CheckConstraint(
        "(valuation_contract_state = 'available' AND price_unit IS NOT NULL "
        "AND btrim(price_unit) <> '' AND price_unit = btrim(price_unit) "
        "AND contract_multiplier > 0 "
        "AND price_factor > 0 AND cardinality(valuation_contract_reason_codes) = 0 "
        "AND contract_multiplier::text NOT IN ('NaN', 'Infinity', '-Infinity') "
        "AND price_factor::text NOT IN ('NaN', 'Infinity', '-Infinity') "
        "AND valuation_factor_source IS NOT NULL) OR "
        "(valuation_contract_state = 'unavailable' AND "
        "(price_unit IS NULL OR contract_multiplier IS NULL OR price_factor IS NULL) AND "
        "cardinality(valuation_contract_reason_codes) > 0)",
        name="valuation_contract",
    ),
    CheckConstraint(
        "valuation_factor_source IS NULL OR valuation_factor_source IN "
        "('instrument', 'methodology')",
        name="factor_source",
    ),
    CheckConstraint(
        "valuation_factor_source <> 'methodology' OR instrument_type IN "
        "('equity', 'fund', 'etf', 'exchange_traded_fund')",
        name="methodology_factor",
    ),
    CheckConstraint("instrument_hash ~ '^[0-9a-f]{64}$'", name="hash"),
    CheckConstraint("jsonb_typeof(canonical_instrument) = 'object'", name="snapshot"),
    CheckConstraint("freshness_max_age_days >= 0", name="freshness"),
    CheckConstraint(
        "quote_selection_policy_revision ~ '^sha256:[0-9a-f]{64}$'",
        name="quote_selection_revision",
    ),
    CheckConstraint(
        "cardinality(valuation_contract_reason_codes) = 0 OR valuation_contract_state = 'unavailable'",
        name="contract_reasons",
    ),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_corp_action_window = Table(
    "portfolio_daily_corp_action_window",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("instrument_id", String(255), nullable=False),
    Column("window_from", Date, nullable=False),
    Column("window_to", Date, nullable=False),
    Column("selection_policy_version", String(64), nullable=False),
    Column("selection_policy_revision", String(128), nullable=False),
    Column("consumer_policy_version", String(64), nullable=False),
    Column("freshness_policy_version", String(64), nullable=False),
    Column("freshness_mode", String(32), nullable=False),
    Column("freshness_max_age_days", Integer, nullable=False),
    Column("resolver_strategy_version", String(64), nullable=False),
    Column("expected_event_count", Integer, nullable=False),
    Column("captured_event_count", Integer, nullable=False),
    *_coverage_columns(),
    PrimaryKeyConstraint("manifest_id", "instrument_id", name="pk_pd_corp_window"),
    _manifest_fk("fk_pd_corp_window_manifest_run"),
    _portfolio_fk("fk_pd_corp_window_portfolio"),
    CheckConstraint("window_from <= window_to", name="dates"),
    CheckConstraint(
        "expected_event_count >= 0 AND captured_event_count >= 0", name="counts"
    ),
    CheckConstraint("freshness_max_age_days >= 0", name="freshness"),
    CheckConstraint(
        "selection_policy_revision ~ '^sha256:[0-9a-f]{64}$'", name="selection_revision"
    ),
    *_coverage_constraints(),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_corp_action_input = Table(
    "portfolio_daily_corp_action_input",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("corporate_action_event_id", String(255), nullable=False),
    Column("instrument_id", String(255), nullable=False),
    Column("action_type", String(64), nullable=False),
    Column("announcement_date", Date),
    Column("record_date", Date),
    Column("effective_date", Date, nullable=False),
    Column("payable_date", Date),
    Column("new_units", SOURCE_RATE, nullable=False),
    Column("old_units", SOURCE_RATE, nullable=False),
    Column("quantity_rounding", String(32), nullable=False),
    Column("quantity_precision", Integer, nullable=False),
    Column("cost_basis_treatment", String(32), nullable=False),
    Column("source", String(255), nullable=False),
    Column("external_event_id", String(255)),
    Column("event_status", String(32), nullable=False),
    Column("event_updated_at", DateTime(timezone=True), nullable=False),
    Column("event_schema_version", String(64), nullable=False),
    Column("event_hash", CHAR(64), nullable=False),
    Column("canonical_event", JSONB, nullable=False),
    PrimaryKeyConstraint(
        "manifest_id", "corporate_action_event_id", name="pk_pd_corp_input"
    ),
    _manifest_fk("fk_pd_corp_input_manifest_run"),
    _portfolio_fk("fk_pd_corp_input_portfolio"),
    ForeignKeyConstraint(
        ["manifest_id", "instrument_id"],
        [
            f"{PORTFOLIO_SCHEMA}.portfolio_daily_corp_action_window.manifest_id",
            f"{PORTFOLIO_SCHEMA}.portfolio_daily_corp_action_window.instrument_id",
        ],
        name="fk_pd_corp_input_window",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "new_units > 0 AND old_units > 0 AND new_units::text NOT IN ('NaN', 'Infinity', '-Infinity') AND old_units::text NOT IN ('NaN', 'Infinity', '-Infinity')",
        name="ratio",
    ),
    CheckConstraint(
        "quantity_precision >= 0 AND quantity_precision <= 12", name="precision"
    ),
    CheckConstraint("event_hash ~ '^[0-9a-f]{64}$'", name="hash"),
    CheckConstraint("jsonb_typeof(canonical_event) = 'object'", name="snapshot"),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_quote_window = Table(
    "portfolio_daily_quote_window",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("quote_window_id", UUID(as_uuid=True), nullable=False),
    Column("instrument_id", String(255), nullable=False),
    Column("quote_role", String(32), nullable=False),
    Column("valuation_date", Date, nullable=False),
    Column("quote_currency", String(3), nullable=False),
    Column("window_start_at", DateTime(timezone=True), nullable=False),
    Column("window_end_at", DateTime(timezone=True), nullable=False),
    Column("selection_policy_version", String(64), nullable=False),
    Column("selection_policy_revision", String(128), nullable=False),
    Column("consumer_policy_version", String(64), nullable=False),
    Column("freshness_policy_version", String(64), nullable=False),
    Column("freshness_mode", String(32), nullable=False),
    Column("freshness_max_age_days", Integer, nullable=False),
    Column("resolver_strategy_version", String(64), nullable=False),
    Column("freshness_limit_seconds", BigInteger, nullable=False),
    Column("candidate_count", Integer, nullable=False),
    Column("adopted_count", Integer, nullable=False),
    Column("selection_status", String(16), nullable=False),
    *_coverage_columns(),
    PrimaryKeyConstraint("manifest_id", "quote_window_id", name="pk_pd_quote_window"),
    UniqueConstraint(
        "manifest_id",
        "instrument_id",
        "quote_role",
        "valuation_date",
        name="uq_pd_quote_window_role",
    ),
    _manifest_fk("fk_pd_quote_window_manifest_run"),
    _portfolio_fk("fk_pd_quote_window_portfolio"),
    CheckConstraint("quote_currency ~ '^[A-Z]{3}$'", name="currency"),
    CheckConstraint("quote_role = 'valuation'", name="role"),
    CheckConstraint("window_start_at <= window_end_at", name="bounds"),
    CheckConstraint(
        "freshness_limit_seconds >= 0 AND freshness_max_age_days >= 0 AND candidate_count >= 0",
        name="counts",
    ),
    CheckConstraint(
        "adopted_count IN (0, 1) AND adopted_count <= candidate_count", name="adopted"
    ),
    CheckConstraint("selection_status IN ('selected', 'unavailable')", name="status"),
    CheckConstraint(
        "selection_policy_revision ~ '^sha256:[0-9a-f]{64}$'", name="selection_revision"
    ),
    *_coverage_constraints(),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_quote_candidate = Table(
    "portfolio_daily_quote_candidate",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("quote_window_id", UUID(as_uuid=True), nullable=False),
    Column("candidate_rank", Integer, nullable=False),
    Column("quote_series_id", UUID(as_uuid=True), nullable=False),
    Column("observation_id", UUID(as_uuid=True), nullable=False),
    Column("revision_id", UUID(as_uuid=True), nullable=False),
    Column("revision_number", Integer, nullable=False),
    Column("observation_date", Date, nullable=False),
    Column("quote_value", RAW_CANONICAL_NUMERIC),
    Column("quote_status", String(16), nullable=False),
    Column("source_published_at", DateTime(timezone=True)),
    Column("ingested_at", DateTime(timezone=True)),
    Column("payload_hash", String(128), nullable=False),
    Column("decision", String(16), nullable=False),
    Column("decision_reason_code", String(64), nullable=False),
    PrimaryKeyConstraint(
        "manifest_id", "quote_window_id", "candidate_rank", name="pk_pd_quote_candidate"
    ),
    UniqueConstraint(
        "manifest_id",
        "quote_window_id",
        "revision_id",
        name="uq_pd_quote_candidate_revision",
    ),
    _manifest_fk("fk_pd_quote_candidate_manifest_run"),
    _portfolio_fk("fk_pd_quote_candidate_portfolio"),
    ForeignKeyConstraint(
        ["manifest_id", "quote_window_id"],
        [
            f"{PORTFOLIO_SCHEMA}.portfolio_daily_quote_window.manifest_id",
            f"{PORTFOLIO_SCHEMA}.portfolio_daily_quote_window.quote_window_id",
        ],
        name="fk_pd_quote_candidate_window",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["quote_series_id"],
        [f"{INSTRUMENT_SCHEMA}.quote_series.quote_series_id"],
        name="fk_pd_quote_candidate_series",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["observation_id"],
        [f"{INSTRUMENT_SCHEMA}.quote_observation.observation_id"],
        name="fk_pd_quote_candidate_observation",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["revision_id"],
        [f"{INSTRUMENT_SCHEMA}.quote_observation_revision.revision_id"],
        name="fk_pd_quote_candidate_exact_revision",
        ondelete="RESTRICT",
    ),
    CheckConstraint("candidate_rank > 0 AND revision_number > 0", name="ranks"),
    CheckConstraint("decision IN ('adopted', 'excluded')", name="decision"),
    CheckConstraint(
        "btrim(decision_reason_code) <> '' AND decision_reason_code = btrim(decision_reason_code)",
        name="decision_reason",
    ),
    CheckConstraint(
        "(quote_status = 'withdrawn' AND quote_value IS NULL) OR (quote_status <> 'withdrawn' AND quote_value > 0 AND quote_value::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
        name="value",
    ),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_fx_path = Table(
    "portfolio_daily_fx_path",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("fx_path_id", UUID(as_uuid=True), nullable=False),
    Column("valuation_date", Date, nullable=False),
    Column("from_currency", String(3), nullable=False),
    Column("to_currency", String(3), nullable=False),
    Column("path_kind", String(16), nullable=False),
    Column("resolution_status", String(16), nullable=False),
    Column("leg_count", Integer, nullable=False),
    Column("resolved_rate", DERIVED_RATE_EVIDENCE),
    Column("rate_derivation_residual_exact", METHOD_EVIDENCE),
    Column("selection_policy_version", String(64), nullable=False),
    Column("consumer_policy_version", String(64), nullable=False),
    Column("freshness_policy_version", String(64), nullable=False),
    Column("freshness_mode", String(32), nullable=False),
    Column("freshness_max_age_days", Integer, nullable=False),
    Column("resolver_strategy_version", String(64), nullable=False),
    Column("rate_math_precision", Integer, nullable=False),
    Column("rate_rounding_mode", String(32), nullable=False),
    *_coverage_columns(),
    PrimaryKeyConstraint("manifest_id", "fx_path_id", name="pk_pd_fx_path"),
    UniqueConstraint(
        "manifest_id",
        "valuation_date",
        "from_currency",
        "to_currency",
        name="uq_pd_fx_path_pair",
    ),
    _manifest_fk("fk_pd_fx_path_manifest_run"),
    _portfolio_fk("fk_pd_fx_path_portfolio"),
    CheckConstraint(
        "from_currency ~ '^[A-Z]{3}$' AND to_currency ~ '^[A-Z]{3}$'", name="currency"
    ),
    CheckConstraint(
        "path_kind IN ('identity', 'direct', 'inverse', 'cross', 'unavailable')",
        name="kind",
    ),
    CheckConstraint(
        "resolution_status IN ('resolved', 'unavailable')", name="resolution_status"
    ),
    CheckConstraint(
        "(path_kind = 'identity' AND leg_count = 0 AND resolution_status = 'resolved') OR (path_kind = 'unavailable' AND leg_count = 0 AND resolution_status = 'unavailable') OR (path_kind IN ('direct', 'inverse') AND leg_count = 1) OR (path_kind = 'cross' AND leg_count = 2)",
        name="shape",
    ),
    CheckConstraint("leg_count >= 0", name="leg_count"),
    CheckConstraint("freshness_max_age_days >= 0", name="freshness"),
    CheckConstraint(
        "rate_math_precision = 50 AND rate_rounding_mode = 'ROUND_HALF_EVEN'",
        name="decimal_context",
    ),
    CheckConstraint(
        "(resolution_status = 'unavailable' AND resolved_rate IS NULL AND rate_derivation_residual_exact IS NULL) OR (resolution_status = 'resolved' AND path_kind <> 'unavailable' AND resolved_rate IS NOT NULL AND rate_derivation_residual_exact = 0 AND resolved_rate > 0 AND resolved_rate::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
        name="rate",
    ),
    *_coverage_constraints(),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_fx_leg = Table(
    "portfolio_daily_fx_leg",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("fx_path_id", UUID(as_uuid=True), nullable=False),
    Column("leg_order", Integer, nullable=False),
    Column("from_currency", String(3), nullable=False),
    Column("to_currency", String(3), nullable=False),
    Column("is_inverted", Boolean, nullable=False),
    Column("leg_resolution_status", String(16), nullable=False),
    Column("reason_codes", REASON_CODES, nullable=False),
    Column("quote_series_id", UUID(as_uuid=True)),
    Column("observation_id", UUID(as_uuid=True)),
    Column("revision_id", UUID(as_uuid=True)),
    Column("revision_number", Integer),
    Column("observation_date", Date),
    Column("quoted_rate", RAW_CANONICAL_NUMERIC),
    Column("effective_rate", DERIVED_RATE_EVIDENCE),
    Column("rate_derivation_residual_exact", METHOD_EVIDENCE),
    Column("quote_status", String(16)),
    Column("source_published_at", DateTime(timezone=True)),
    Column("ingested_at", DateTime(timezone=True)),
    Column("payload_hash", String(128)),
    Column("consumer_policy_version", String(64), nullable=False),
    Column("freshness_policy_version", String(64), nullable=False),
    Column("freshness_mode", String(32), nullable=False),
    Column("freshness_max_age_days", Integer, nullable=False),
    Column("resolver_strategy_version", String(64), nullable=False),
    Column("rate_math_precision", Integer, nullable=False),
    Column("rate_rounding_mode", String(32), nullable=False),
    PrimaryKeyConstraint("manifest_id", "fx_path_id", "leg_order", name="pk_pd_fx_leg"),
    _manifest_fk("fk_pd_fx_leg_manifest_run"),
    _portfolio_fk("fk_pd_fx_leg_portfolio"),
    ForeignKeyConstraint(
        ["manifest_id", "fx_path_id"],
        [
            f"{PORTFOLIO_SCHEMA}.portfolio_daily_fx_path.manifest_id",
            f"{PORTFOLIO_SCHEMA}.portfolio_daily_fx_path.fx_path_id",
        ],
        name="fk_pd_fx_leg_path",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["revision_id"],
        [f"{INSTRUMENT_SCHEMA}.quote_observation_revision.revision_id"],
        name="fk_pd_fx_leg_exact_revision",
        ondelete="RESTRICT",
    ),
    CheckConstraint("leg_order > 0", name="order"),
    CheckConstraint(
        "leg_resolution_status IN ('resolved', 'missing', 'rejected')",
        name="resolution_status",
    ),
    CheckConstraint(
        "(leg_resolution_status = 'resolved') = (cardinality(reason_codes) = 0)",
        name="reasons",
    ),
    CheckConstraint(
        "(leg_resolution_status = 'missing' AND observation_id IS NULL AND revision_id IS NULL AND revision_number IS NULL AND observation_date IS NULL AND quoted_rate IS NULL AND effective_rate IS NULL AND rate_derivation_residual_exact IS NULL AND quote_status IS NULL AND source_published_at IS NULL AND ingested_at IS NULL AND payload_hash IS NULL) OR (leg_resolution_status = 'rejected' AND quote_series_id IS NOT NULL AND observation_id IS NOT NULL AND revision_id IS NOT NULL AND revision_number IS NOT NULL AND revision_number > 0 AND observation_date IS NOT NULL AND ((quote_status = 'withdrawn' AND quoted_rate IS NULL) OR (quote_status <> 'withdrawn' AND quoted_rate IS NOT NULL AND quoted_rate > 0 AND quoted_rate::text NOT IN ('NaN', 'Infinity', '-Infinity'))) AND quote_status IS NOT NULL AND ingested_at IS NOT NULL AND payload_hash IS NOT NULL AND effective_rate IS NULL AND rate_derivation_residual_exact IS NULL) OR (leg_resolution_status = 'resolved' AND quote_series_id IS NOT NULL AND observation_id IS NOT NULL AND revision_id IS NOT NULL AND revision_number IS NOT NULL AND revision_number > 0 AND observation_date IS NOT NULL AND quoted_rate IS NOT NULL AND effective_rate IS NOT NULL AND rate_derivation_residual_exact IS NOT NULL AND quote_status IS NOT NULL AND quote_status <> 'withdrawn' AND ingested_at IS NOT NULL AND payload_hash IS NOT NULL AND quoted_rate > 0 AND effective_rate > 0 AND quoted_rate::text NOT IN ('NaN', 'Infinity', '-Infinity') AND effective_rate::text NOT IN ('NaN', 'Infinity', '-Infinity') AND rate_derivation_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
        name="evidence_shape",
    ),
    CheckConstraint("freshness_max_age_days >= 0", name="freshness"),
    CheckConstraint(
        "rate_math_precision = 50 AND rate_rounding_mode = 'ROUND_HALF_EVEN'",
        name="decimal_context",
    ),
    CheckConstraint(
        "leg_resolution_status <> 'resolved' OR ((NOT is_inverted AND rate_derivation_residual_exact = 0 AND effective_rate = quoted_rate) OR (is_inverted AND effective_rate = calculation_registry.divide_significant_half_even(1, quoted_rate, rate_math_precision) AND rate_derivation_residual_exact = effective_rate * quoted_rate - 1))",
        name="derivation",
    ),
    CheckConstraint(
        "from_currency ~ '^[A-Z]{3}$' AND to_currency ~ '^[A-Z]{3}$'", name="currency"
    ),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_prior_publication = Table(
    "portfolio_daily_prior_publication",
    portfolio_daily_metadata,
    *_dependency_identity(),
    Column("publication_id", UUID(as_uuid=True), nullable=False),
    Column("published_run_id", UUID(as_uuid=True), nullable=False),
    Column("published_fencing_token", BigInteger, nullable=False),
    Column("output_schema_version", String(64), nullable=False),
    Column("canonical_output_hash", CHAR(64), nullable=False),
    Column("range_start", Date, nullable=False),
    Column("range_end", Date, nullable=False),
    PrimaryKeyConstraint("manifest_id", name="pk_pd_prior_publication"),
    _manifest_fk("fk_pd_prior_manifest_run"),
    _portfolio_fk("fk_pd_prior_portfolio"),
    ForeignKeyConstraint(
        ["publication_id"],
        [f"{CALCULATION_SCHEMA}.calculation_publication.publication_id"],
        name="fk_pd_prior_publication",
        ondelete="RESTRICT",
    ),
    CheckConstraint("published_fencing_token > 0", name="token"),
    CheckConstraint("canonical_output_hash ~ '^[0-9a-f]{64}$'", name="hash"),
    CheckConstraint("range_start <= range_end", name="range"),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_run_output = Table(
    "portfolio_daily_run_output",
    portfolio_daily_metadata,
    *_output_identity(),
    Column("range_start", Date, nullable=False),
    Column("range_end", Date, nullable=False),
    Column("methodology_version", String(128), nullable=False),
    Column("input_schema_version", String(64), nullable=False),
    Column("output_schema_version", String(64), nullable=False),
    Column("closure_status", String(16), nullable=False),
    Column("canonical_output_hash", CHAR(64)),
    Column("snapshot_count", BigInteger, nullable=False),
    Column("measured_nav_count", BigInteger, nullable=False),
    Column("holding_count", BigInteger, nullable=False),
    Column("balance_count", BigInteger, nullable=False),
    Column("lot_count", BigInteger, nullable=False),
    Column("lot_disposition_count", BigInteger, nullable=False),
    Column("contribution_count", BigInteger, nullable=False),
    Column("unavailable_component_count", BigInteger, nullable=False),
    Column("ledger_balance_residual_exact", ACCOUNTING_EVIDENCE, nullable=False),
    Column("nav_bridge_residual_exact", ACCOUNTING_EVIDENCE, nullable=False),
    Column("pnl_residual_exact", ACCOUNTING_EVIDENCE, nullable=False),
    Column("twr_residual_exact", METHOD_EVIDENCE, nullable=False),
    Column("lot_residual_exact", ACCOUNTING_EVIDENCE, nullable=False),
    Column("rounding_adjustment_base", MONEY, nullable=False),
    *_coverage_columns(),
    PrimaryKeyConstraint("run_id", "output_fencing_token", name="pk_pd_run_output"),
    _run_fk("fk_pd_run_output_run"),
    _portfolio_fk("fk_pd_run_output_portfolio"),
    CheckConstraint("output_fencing_token > 0", name="token"),
    CheckConstraint("range_start <= range_end", name="range"),
    CheckConstraint("closure_status IN ('passed', 'failed')", name="closure"),
    CheckConstraint(
        "canonical_output_hash IS NULL OR canonical_output_hash ~ '^[0-9a-f]{64}$'",
        name="hash",
    ),
    CheckConstraint(
        "snapshot_count >= 0 AND measured_nav_count >= 0 AND measured_nav_count <= snapshot_count AND holding_count >= 0 AND balance_count >= 0 AND lot_count >= 0 AND lot_disposition_count >= 0 AND contribution_count >= 0 AND unavailable_component_count >= 0",
        name="counts",
    ),
    CheckConstraint(
        "ledger_balance_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND nav_bridge_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND pnl_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND twr_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND lot_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')",
        name="finite_residuals",
    ),
    CheckConstraint(
        "(closure_status = 'passed' AND canonical_output_hash IS NOT NULL AND snapshot_count > 0 AND ledger_balance_residual_exact = 0 AND nav_bridge_residual_exact = 0 AND pnl_residual_exact = 0 AND twr_residual_exact = 0 AND lot_residual_exact = 0) OR closure_status = 'failed'",
        name="passed",
    ),
    *_coverage_constraints(),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_snapshot_output = Table(
    "portfolio_daily_snapshot_output",
    portfolio_daily_metadata,
    *_output_identity(),
    Column("as_of_date", Date, nullable=False),
    Column("base_currency", String(3), nullable=False),
    Column("measured_nav", Boolean, nullable=False),
    Column("measured_position_market_value", Boolean, nullable=False),
    Column("measured_book_pnl", Boolean, nullable=False),
    Column("measured_return", Boolean, nullable=False),
    Column("measured_external_flows", Boolean, nullable=False),
    Column("unavailable_component_count", Integer, nullable=False),
    Column("opening_nav", MONEY),
    Column("closing_nav", MONEY),
    Column("position_market_value", MONEY),
    Column("settled_cash", MONEY),
    Column("pending_receivable", MONEY),
    Column("pending_payable", MONEY),
    Column("accrual_receivable", MONEY),
    Column("accrual_payable", MONEY),
    Column("external_flow_in", MONEY),
    Column("external_flow_out", MONEY),
    Column("economic_pnl", MONEY),
    Column("realized_pnl_daily", MONEY),
    Column("unrealized_pnl_beginning", MONEY),
    Column("unrealized_pnl_ending", MONEY),
    Column("unrealized_pnl_change", MONEY),
    Column("gross_income_daily", MONEY),
    Column("return_of_capital_daily", MONEY),
    Column("capitalized_fee_daily", MONEY),
    Column("capitalized_tax_daily", MONEY),
    Column("expensed_fee_daily", MONEY),
    Column("expensed_tax_daily", MONEY),
    Column("disposal_fee_in_realized_daily", MONEY),
    Column("disposal_tax_in_realized_daily", MONEY),
    Column("local_price_effect_daily", MONEY),
    Column("position_fx_effect_daily", MONEY),
    Column("position_attribution_residual_exact", ACCOUNTING_EVIDENCE),
    Column("position_attribution_rounding_adjustment", MONEY, nullable=False),
    Column("cash_fx_effect_daily", MONEY),
    Column("pending_fx_effect_daily", MONEY),
    Column("accrual_fx_effect_daily", MONEY),
    Column("fx_conversion_effect_daily", MONEY),
    Column("pnl_component_rounding_adjustment", MONEY, nullable=False),
    Column("subperiod_twr_method50", METHOD),
    Column("subperiod_twr_published", RATE),
    Column("cumulative_twr_method50", METHOD),
    Column("cumulative_twr_published", RATE),
    Column("wealth_index_method50", METHOD),
    Column("wealth_index_published", RATE),
    Column("peak_wealth_index_method50", METHOD),
    Column("peak_wealth_index_published", RATE),
    Column("drawdown_method50", METHOD),
    Column("drawdown_published", RATE),
    Column(
        "wealth_chain_rounding_adjustment_exact",
        METHOD_ROUNDING_ADJUSTMENT,
    ),
    Column("reliable_anchor_date", Date),
    Column("reliable_anchor_nav_exact", ACCOUNTING_EVIDENCE),
    Column("reliable_anchor_nav", MONEY),
    Column("return_period_start_date", Date),
    Column("return_period_end_date", Date),
    Column("return_period_day_count", Integer),
    Column("calculation_status", String(32), nullable=False),
    Column("return_chain_status", String(32), nullable=False),
    Column("nav_rounding_adjustment", MONEY, nullable=False),
    Column("pnl_rounding_adjustment", MONEY, nullable=False),
    Column("nav_coverage_state", String(16), nullable=False),
    Column("nav_reason_codes", REASON_CODES, nullable=False),
    Column("book_pnl_coverage_state", String(16), nullable=False),
    Column("book_pnl_reason_codes", REASON_CODES, nullable=False),
    Column("return_coverage_state", String(16), nullable=False),
    Column("return_reason_codes", REASON_CODES, nullable=False),
    Column("flow_coverage_state", String(16), nullable=False),
    Column("flow_reason_codes", REASON_CODES, nullable=False),
    Column("position_attribution_coverage_state", String(16), nullable=False),
    Column("position_attribution_reason_codes", REASON_CODES, nullable=False),
    Column("valuation_endpoint_status", String(24), nullable=False),
    Column("valuation_reason_codes", REASON_CODES, nullable=False),
    PrimaryKeyConstraint(
        "run_id", "output_fencing_token", "as_of_date", name="pk_pd_snapshot_output"
    ),
    _run_fk("fk_pd_snapshot_output_run"),
    _portfolio_fk("fk_pd_snapshot_output_portfolio"),
    CheckConstraint("output_fencing_token > 0", name="token"),
    CheckConstraint("base_currency ~ '^[A-Z]{3}$'", name="currency"),
    CheckConstraint("unavailable_component_count >= 0", name="unavailable_count"),
    CheckConstraint(
        "(external_flow_in IS NULL OR external_flow_in >= 0) AND (external_flow_out IS NULL OR external_flow_out >= 0) AND measured_external_flows = (external_flow_in IS NOT NULL AND external_flow_out IS NOT NULL)",
        name="flows",
    ),
    CheckConstraint(
        "NOT measured_book_pnl OR (measured_nav AND measured_external_flows)",
        name="book_pnl_dependencies",
    ),
    CheckConstraint(
        "NOT measured_return OR (measured_nav AND measured_external_flows)",
        name="return_dependencies",
    ),
    CheckConstraint(
        "abs(nav_rounding_adjustment) <= 0.00000004 AND abs(pnl_rounding_adjustment) <= 0.00000003 AND abs(pnl_component_rounding_adjustment) <= 0.00000005 AND abs(position_attribution_rounding_adjustment) <= 0.00000004",
        name="rounding_bounds",
    ),
    CheckConstraint(
        "return_chain_status IN ('active', 'no_new_valuation', 'broken', 'reanchor')",
        name="return_chain",
    ),
    CheckConstraint(
        "calculation_status IN ('no_new_valuation', 'calculated', 'broken', 'reanchored')",
        name="calculation_status",
    ),
    CheckConstraint(
        "subperiod_twr_method50 IS NULL OR subperiod_twr_method50 >= -1",
        name="subperiod_method50",
    ),
    CheckConstraint(
        "drawdown_method50 IS NULL OR (drawdown_method50 >= -1 AND drawdown_method50 <= 0)",
        name="drawdown_method50",
    ),
    CheckConstraint(
        "drawdown_published IS NULL OR (drawdown_published >= -1 AND drawdown_published <= 0)",
        name="drawdown_published",
    ),
    CheckConstraint(
        "(reliable_anchor_date IS NULL) = (reliable_anchor_nav_exact IS NULL) AND (reliable_anchor_nav_exact IS NULL) = (reliable_anchor_nav IS NULL) AND (reliable_anchor_nav IS NULL OR reliable_anchor_nav = calculation_registry.round_half_even(reliable_anchor_nav_exact, 8))",
        name="anchor",
    ),
    CheckConstraint(
        "(reliable_anchor_nav_exact IS NULL OR reliable_anchor_nav_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')) AND (subperiod_twr_method50 IS NULL OR subperiod_twr_method50::text NOT IN ('NaN', 'Infinity', '-Infinity')) AND (cumulative_twr_method50 IS NULL OR cumulative_twr_method50::text NOT IN ('NaN', 'Infinity', '-Infinity')) AND (wealth_index_method50 IS NULL OR wealth_index_method50::text NOT IN ('NaN', 'Infinity', '-Infinity')) AND (peak_wealth_index_method50 IS NULL OR peak_wealth_index_method50::text NOT IN ('NaN', 'Infinity', '-Infinity')) AND (drawdown_method50 IS NULL OR drawdown_method50::text NOT IN ('NaN', 'Infinity', '-Infinity')) AND (wealth_chain_rounding_adjustment_exact IS NULL OR wealth_chain_rounding_adjustment_exact::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
        name="return_evidence_finite",
    ),
    CheckConstraint(
        _all_logical_numeric_domains(
            (
                "subperiod_twr_method50",
                "cumulative_twr_method50",
                "wealth_index_method50",
                "peak_wealth_index_method50",
                "drawdown_method50",
            ),
            precision=METHOD_DECIMAL_STORAGE_PRECISION,
            scale=METHOD_DECIMAL_STORAGE_SCALE,
        ),
        name="return_method_storage_domain",
    ),
    CheckConstraint(
        _logical_numeric_domain(
            "wealth_chain_rounding_adjustment_exact",
            precision=METHOD_ROUNDING_ADJUSTMENT_STORAGE_PRECISION,
            scale=METHOD_ROUNDING_ADJUSTMENT_STORAGE_SCALE,
        ),
        name="return_adjustment_storage_domain",
    ),
    CheckConstraint(
        "(cumulative_twr_method50 IS NULL) = (cumulative_twr_published IS NULL) AND (wealth_index_method50 IS NULL) = (wealth_index_published IS NULL) AND (peak_wealth_index_method50 IS NULL) = (peak_wealth_index_published IS NULL) AND (drawdown_method50 IS NULL) = (drawdown_published IS NULL) AND (cumulative_twr_method50 IS NULL) = (wealth_index_method50 IS NULL) AND (wealth_index_method50 IS NULL) = (peak_wealth_index_method50 IS NULL) AND (peak_wealth_index_method50 IS NULL) = (drawdown_method50 IS NULL)",
        name="return_evidence_shape",
    ),
    CheckConstraint(
        "(subperiod_twr_method50 IS NULL OR subperiod_twr_method50 = calculation_registry.round_significant_half_even(subperiod_twr_method50, 50)) AND (cumulative_twr_method50 IS NULL OR cumulative_twr_method50 = calculation_registry.round_significant_half_even(cumulative_twr_method50, 50)) AND (wealth_index_method50 IS NULL OR wealth_index_method50 = calculation_registry.round_significant_half_even(wealth_index_method50, 50)) AND (peak_wealth_index_method50 IS NULL OR peak_wealth_index_method50 = calculation_registry.round_significant_half_even(peak_wealth_index_method50, 50)) AND (drawdown_method50 IS NULL OR drawdown_method50 = calculation_registry.round_significant_half_even(drawdown_method50, 50))",
        name="return_method_precision",
    ),
    CheckConstraint(
        "cumulative_twr_method50 IS NULL OR (wealth_index_method50 >= 0 AND peak_wealth_index_method50 > 0 AND peak_wealth_index_method50 >= wealth_index_method50 AND cumulative_twr_method50 = calculation_registry.round_significant_half_even(wealth_index_method50 - 1, 50) AND drawdown_method50 = calculation_registry.divide_significant_half_even(calculation_registry.round_significant_half_even(wealth_index_method50 - peak_wealth_index_method50, 50), peak_wealth_index_method50, 50))",
        name="return_method_chain",
    ),
    CheckConstraint(
        "(subperiod_twr_method50 IS NULL) = (subperiod_twr_published IS NULL) AND (subperiod_twr_method50 IS NULL) = (wealth_chain_rounding_adjustment_exact IS NULL)",
        name="subperiod_evidence_shape",
    ),
    CheckConstraint(
        "subperiod_twr_published IS NULL OR subperiod_twr_published = calculation_registry.round_half_even(subperiod_twr_method50, 18)",
        name="subperiod_published",
    ),
    CheckConstraint(
        "cumulative_twr_published IS NULL OR cumulative_twr_published = calculation_registry.round_half_even(cumulative_twr_method50, 18)",
        name="cumulative_published",
    ),
    CheckConstraint(
        "wealth_index_published IS NULL OR wealth_index_published = calculation_registry.round_half_even(wealth_index_method50, 18)",
        name="wealth_published",
    ),
    CheckConstraint(
        "peak_wealth_index_published IS NULL OR peak_wealth_index_published = calculation_registry.round_half_even(peak_wealth_index_method50, 18)",
        name="peak_wealth_published",
    ),
    CheckConstraint(
        "drawdown_published IS NULL OR drawdown_published = calculation_registry.round_half_even(drawdown_method50, 18)",
        name="drawdown_publication_rounding",
    ),
    CheckConstraint(
        "(calculation_status = 'calculated' AND return_chain_status = 'active') OR (calculation_status = 'reanchored' AND return_chain_status = 'reanchor') OR (calculation_status = 'broken' AND return_chain_status = 'broken') OR (calculation_status = 'no_new_valuation' AND return_chain_status = 'no_new_valuation')",
        name="return_status_shape",
    ),
    CheckConstraint(
        "calculation_status <> 'reanchored' OR (subperiod_twr_method50 IS NULL AND wealth_chain_rounding_adjustment_exact IS NULL AND cumulative_twr_method50 IS NOT NULL AND cumulative_twr_method50 = 0 AND wealth_index_method50 IS NOT NULL AND wealth_index_method50 = 1 AND peak_wealth_index_method50 IS NOT NULL AND peak_wealth_index_method50 = 1 AND drawdown_method50 IS NOT NULL AND drawdown_method50 = 0 AND reliable_anchor_date IS NOT NULL AND reliable_anchor_nav_exact IS NOT NULL AND reliable_anchor_nav IS NOT NULL)",
        name="reanchor_values",
    ),
    CheckConstraint(
        "calculation_status <> 'broken' OR (subperiod_twr_method50 IS NULL AND cumulative_twr_method50 IS NULL AND wealth_index_method50 IS NULL AND peak_wealth_index_method50 IS NULL AND drawdown_method50 IS NULL AND wealth_chain_rounding_adjustment_exact IS NULL)",
        name="broken_values",
    ),
    CheckConstraint(
        "calculation_status <> 'no_new_valuation' OR (subperiod_twr_method50 IS NULL AND wealth_chain_rounding_adjustment_exact IS NULL AND return_period_start_date IS NULL AND return_period_end_date IS NULL AND return_period_day_count IS NULL AND ((reliable_anchor_date IS NULL AND reliable_anchor_nav_exact IS NULL AND reliable_anchor_nav IS NULL AND cumulative_twr_method50 IS NULL AND wealth_index_method50 IS NULL AND peak_wealth_index_method50 IS NULL AND drawdown_method50 IS NULL) OR (reliable_anchor_date IS NOT NULL AND reliable_anchor_nav_exact IS NOT NULL AND reliable_anchor_nav IS NOT NULL AND cumulative_twr_method50 IS NOT NULL AND wealth_index_method50 IS NOT NULL AND wealth_index_method50 > 0 AND peak_wealth_index_method50 IS NOT NULL AND drawdown_method50 IS NOT NULL)))",
        name="no_new_values",
    ),
    CheckConstraint(
        "(return_period_start_date IS NULL) = (return_period_end_date IS NULL) AND (return_period_start_date IS NULL) = (return_period_day_count IS NULL) AND ((calculation_status = 'calculated') = (return_period_start_date IS NOT NULL)) AND (return_period_day_count IS NULL OR (return_period_day_count > 0 AND return_period_start_date < return_period_end_date AND return_period_end_date = as_of_date))",
        name="return_period",
    ),
    CheckConstraint(
        "(measured_nav AND measured_position_market_value AND closing_nav IS NOT NULL AND settled_cash IS NOT NULL AND pending_receivable IS NOT NULL AND pending_payable IS NOT NULL AND accrual_receivable IS NOT NULL AND accrual_payable IS NOT NULL) OR (NOT measured_nav AND closing_nav IS NULL)",
        name="measured_nav",
    ),
    CheckConstraint(
        "(measured_position_market_value AND position_market_value IS NOT NULL) OR (NOT measured_position_market_value AND position_market_value IS NULL)",
        name="measured_position",
    ),
    CheckConstraint(
        "NOT measured_nav OR closing_nav = position_market_value + settled_cash + pending_receivable - pending_payable + accrual_receivable - accrual_payable + nav_rounding_adjustment",
        name="nav_bridge",
    ),
    CheckConstraint(
        "NOT measured_book_pnl OR (economic_pnl IS NOT NULL AND opening_nav IS NOT NULL AND closing_nav IS NOT NULL AND realized_pnl_daily IS NOT NULL AND unrealized_pnl_beginning IS NOT NULL AND unrealized_pnl_ending IS NOT NULL AND unrealized_pnl_change = unrealized_pnl_ending - unrealized_pnl_beginning AND gross_income_daily IS NOT NULL AND expensed_fee_daily IS NOT NULL AND expensed_tax_daily IS NOT NULL AND cash_fx_effect_daily IS NOT NULL AND pending_fx_effect_daily IS NOT NULL AND accrual_fx_effect_daily IS NOT NULL AND fx_conversion_effect_daily IS NOT NULL)",
        name="measured_pnl",
    ),
    CheckConstraint(
        "NOT measured_book_pnl OR economic_pnl = realized_pnl_daily + unrealized_pnl_change + gross_income_daily - expensed_fee_daily - expensed_tax_daily + cash_fx_effect_daily + pending_fx_effect_daily + accrual_fx_effect_daily + fx_conversion_effect_daily + pnl_component_rounding_adjustment",
        name="pnl_components",
    ),
    CheckConstraint(
        "(position_attribution_coverage_state = 'complete' AND position_attribution_residual_exact IS NOT NULL AND position_attribution_residual_exact = 0 AND local_price_effect_daily IS NOT NULL AND position_fx_effect_daily IS NOT NULL AND return_of_capital_daily IS NOT NULL AND capitalized_fee_daily IS NOT NULL AND capitalized_tax_daily IS NOT NULL AND disposal_fee_in_realized_daily IS NOT NULL AND disposal_tax_in_realized_daily IS NOT NULL AND realized_pnl_daily + unrealized_pnl_change = local_price_effect_daily + position_fx_effect_daily + return_of_capital_daily - capitalized_fee_daily - capitalized_tax_daily - disposal_fee_in_realized_daily - disposal_tax_in_realized_daily + position_attribution_rounding_adjustment) OR (position_attribution_coverage_state <> 'complete' AND position_attribution_residual_exact IS NULL)",
        name="position_attribution",
    ),
    CheckConstraint(
        "NOT (measured_nav AND measured_book_pnl) OR closing_nav + external_flow_out = opening_nav + external_flow_in + economic_pnl + pnl_rounding_adjustment",
        name="pnl_bridge",
    ),
    CheckConstraint(
        "(measured_return AND subperiod_twr_method50 IS NOT NULL AND subperiod_twr_published IS NOT NULL AND wealth_chain_rounding_adjustment_exact IS NOT NULL AND cumulative_twr_method50 IS NOT NULL AND cumulative_twr_published IS NOT NULL AND wealth_index_method50 IS NOT NULL AND wealth_index_published IS NOT NULL AND peak_wealth_index_method50 IS NOT NULL AND peak_wealth_index_published IS NOT NULL AND drawdown_method50 IS NOT NULL AND drawdown_published IS NOT NULL AND calculation_status = 'calculated') OR (NOT measured_return AND subperiod_twr_method50 IS NULL AND subperiod_twr_published IS NULL AND wealth_chain_rounding_adjustment_exact IS NULL AND calculation_status <> 'calculated')",
        name="measured_return",
    ),
    CheckConstraint(
        "nav_coverage_state IN ('complete', 'partial', 'unavailable') AND book_pnl_coverage_state IN ('complete', 'partial', 'unavailable') AND return_coverage_state IN ('complete', 'partial', 'unavailable') AND flow_coverage_state IN ('complete', 'partial', 'unavailable') AND position_attribution_coverage_state IN ('complete', 'partial', 'unavailable')",
        name="coverage_states",
    ),
    CheckConstraint(
        "((nav_coverage_state = 'complete') = (cardinality(nav_reason_codes) = 0)) AND ((book_pnl_coverage_state = 'complete') = (cardinality(book_pnl_reason_codes) = 0)) AND ((return_coverage_state = 'complete') = (cardinality(return_reason_codes) = 0)) AND ((flow_coverage_state = 'complete') = (cardinality(flow_reason_codes) = 0)) AND ((position_attribution_coverage_state = 'complete') = (cardinality(position_attribution_reason_codes) = 0))",
        name="coverage_reasons",
    ),
    CheckConstraint(
        "measured_nav = (nav_coverage_state = 'complete') AND measured_book_pnl = (book_pnl_coverage_state = 'complete') AND measured_return = (return_coverage_state = 'complete') AND measured_external_flows = (flow_coverage_state = 'complete') AND (flow_coverage_state <> 'partial' OR ((external_flow_in IS NULL) <> (external_flow_out IS NULL))) AND (flow_coverage_state <> 'unavailable' OR (external_flow_in IS NULL AND external_flow_out IS NULL))",
        name="measured_coverage",
    ),
    CheckConstraint(
        "valuation_endpoint_status IN ('fresh', 'carry_forward', 'stale', 'unavailable')",
        name="valuation_status",
    ),
    CheckConstraint(
        "(valuation_endpoint_status = 'fresh' AND cardinality(valuation_reason_codes) = 0) OR (valuation_endpoint_status <> 'fresh' AND cardinality(valuation_reason_codes) > 0)",
        name="valuation_reasons",
    ),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_holding_output = Table(
    "portfolio_daily_holding_output",
    portfolio_daily_metadata,
    *_output_identity(),
    Column("as_of_date", Date, nullable=False),
    Column("account_id", String(255), nullable=False),
    Column("instrument_id", String(255), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("quantity_exact", EXACT_QUANTITY, nullable=False),
    Column("quantity", QUANTITY, nullable=False),
    Column("measured_price", Boolean, nullable=False),
    Column("measured_market_value", Boolean, nullable=False),
    Column("measured_book_pnl", Boolean, nullable=False),
    Column("unavailable_component_count", Integer, nullable=False),
    Column("adopted_price_exact", SOURCE_PRICE),
    Column("price", PRICE),
    Column("contract_multiplier_exact", EXACT_QUANTITY),
    Column("contract_multiplier", QUANTITY),
    Column("price_factor_exact", EXACT_QUANTITY),
    Column("price_factor", QUANTITY),
    Column("adopted_fx_rate_exact", DERIVED_RATE_EVIDENCE),
    Column("fx_rate_to_base", RATE),
    Column("market_value_local_exact", ACCOUNTING_EVIDENCE),
    Column("market_value_base_exact", ACCOUNTING_EVIDENCE),
    Column("market_value_local", MONEY),
    Column("market_value_base", MONEY),
    Column("cost_basis_local", MONEY),
    Column("cost_basis_base", MONEY),
    Column("economic_pnl_daily_base", MONEY),
    Column("realized_pnl_daily_base", MONEY),
    Column("unrealized_pnl_beginning_base", MONEY),
    Column("unrealized_pnl_ending_base", MONEY),
    Column("unrealized_pnl_change_base", MONEY),
    Column("gross_income_daily_base", MONEY),
    Column("return_of_capital_daily_base", MONEY),
    Column("capitalized_fee_daily_base", MONEY),
    Column("capitalized_tax_daily_base", MONEY),
    Column("expensed_fee_daily_base", MONEY),
    Column("expensed_tax_daily_base", MONEY),
    Column("disposal_fee_in_realized_daily_base", MONEY),
    Column("disposal_tax_in_realized_daily_base", MONEY),
    Column("local_price_effect_daily_base", MONEY),
    Column("position_fx_effect_daily_base", MONEY),
    Column("fx_conversion_effect_daily_base", MONEY),
    Column("pnl_component_rounding_adjustment_base", MONEY, nullable=False),
    Column("position_attribution_residual_exact", ACCOUNTING_EVIDENCE),
    Column("position_attribution_rounding_adjustment_base", MONEY, nullable=False),
    Column("portfolio_weight", RATE),
    Column("return_contribution", RATE),
    Column("local_rounding_adjustment", MONEY, nullable=False),
    Column("base_rounding_adjustment", MONEY, nullable=False),
    Column("valuation_coverage_state", String(16), nullable=False),
    Column("valuation_coverage_reason_codes", REASON_CODES, nullable=False),
    Column("book_pnl_coverage_state", String(16), nullable=False),
    Column("book_pnl_reason_codes", REASON_CODES, nullable=False),
    Column("position_attribution_coverage_state", String(16), nullable=False),
    Column("position_attribution_reason_codes", REASON_CODES, nullable=False),
    Column("valuation_endpoint_status", String(24), nullable=False),
    Column("valuation_reason_codes", REASON_CODES, nullable=False),
    PrimaryKeyConstraint(
        "run_id",
        "output_fencing_token",
        "as_of_date",
        "account_id",
        "instrument_id",
        name="pk_pd_holding_output",
    ),
    _run_fk("fk_pd_holding_output_run"),
    _portfolio_fk("fk_pd_holding_output_portfolio"),
    CheckConstraint("output_fencing_token > 0", name="token"),
    CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
    CheckConstraint("unavailable_component_count >= 0", name="unavailable_count"),
    CheckConstraint(
        "quantity_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND quantity = calculation_registry.round_half_even(quantity_exact, 12)",
        name="quantity",
    ),
    CheckConstraint(
        "abs(local_rounding_adjustment) <= 0.00000001 AND abs(base_rounding_adjustment) <= 0.00000001",
        name="rounding_bounds",
    ),
    CheckConstraint(
        "abs(pnl_component_rounding_adjustment_base) <= 0.00000001 AND abs(position_attribution_rounding_adjustment_base) <= 0.00000001",
        name="pnl_rounding_bound",
    ),
    CheckConstraint(
        "(measured_price AND adopted_price_exact IS NOT NULL AND price IS NOT NULL AND adopted_price_exact > 0 AND adopted_price_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND price = calculation_registry.round_half_even(adopted_price_exact, 12)) OR (NOT measured_price AND adopted_price_exact IS NULL AND price IS NULL)",
        name="measured_price",
    ),
    CheckConstraint(
        "(measured_market_value AND measured_price AND contract_multiplier_exact IS NOT NULL AND price_factor_exact IS NOT NULL AND adopted_fx_rate_exact IS NOT NULL AND market_value_local_exact IS NOT NULL AND market_value_base_exact IS NOT NULL AND market_value_local IS NOT NULL AND market_value_base IS NOT NULL AND contract_multiplier_exact > 0 AND price_factor_exact > 0 AND adopted_fx_rate_exact > 0 AND market_value_local_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND market_value_base_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND market_value_local_exact = quantity_exact * adopted_price_exact * contract_multiplier_exact * price_factor_exact AND market_value_base_exact = market_value_local_exact * adopted_fx_rate_exact AND market_value_local = calculation_registry.round_half_even(market_value_local_exact, 8) + local_rounding_adjustment AND market_value_base = calculation_registry.round_half_even(market_value_base_exact, 8) + base_rounding_adjustment) OR (NOT measured_market_value AND market_value_local_exact IS NULL AND market_value_base_exact IS NULL AND market_value_local IS NULL AND market_value_base IS NULL)",
        name="measured_value",
    ),
    CheckConstraint(
        "(contract_multiplier_exact IS NULL AND contract_multiplier IS NULL) OR (contract_multiplier_exact IS NOT NULL AND contract_multiplier IS NOT NULL AND contract_multiplier_exact > 0 AND contract_multiplier_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND contract_multiplier = calculation_registry.round_half_even(contract_multiplier_exact, 12))",
        name="multiplier",
    ),
    CheckConstraint(
        "(price_factor_exact IS NULL AND price_factor IS NULL) OR (price_factor_exact IS NOT NULL AND price_factor IS NOT NULL AND price_factor_exact > 0 AND price_factor_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND price_factor = calculation_registry.round_half_even(price_factor_exact, 12))",
        name="price_factor",
    ),
    CheckConstraint(
        "(adopted_fx_rate_exact IS NULL AND fx_rate_to_base IS NULL) OR (adopted_fx_rate_exact IS NOT NULL AND fx_rate_to_base IS NOT NULL AND adopted_fx_rate_exact > 0 AND adopted_fx_rate_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND fx_rate_to_base = calculation_registry.round_half_even(adopted_fx_rate_exact, 18))",
        name="fx_rate",
    ),
    CheckConstraint(
        "NOT measured_book_pnl OR (cost_basis_local IS NOT NULL AND cost_basis_base IS NOT NULL AND economic_pnl_daily_base IS NOT NULL AND realized_pnl_daily_base IS NOT NULL AND unrealized_pnl_beginning_base IS NOT NULL AND unrealized_pnl_ending_base IS NOT NULL AND unrealized_pnl_change_base = unrealized_pnl_ending_base - unrealized_pnl_beginning_base AND gross_income_daily_base IS NOT NULL AND expensed_fee_daily_base IS NOT NULL AND expensed_tax_daily_base IS NOT NULL AND fx_conversion_effect_daily_base IS NOT NULL)",
        name="measured_pnl",
    ),
    CheckConstraint(
        "NOT measured_book_pnl OR economic_pnl_daily_base = realized_pnl_daily_base + unrealized_pnl_change_base + gross_income_daily_base - expensed_fee_daily_base - expensed_tax_daily_base + fx_conversion_effect_daily_base + pnl_component_rounding_adjustment_base",
        name="pnl_components",
    ),
    CheckConstraint(
        "(position_attribution_coverage_state = 'complete' AND position_attribution_residual_exact IS NOT NULL AND position_attribution_residual_exact = 0 AND local_price_effect_daily_base IS NOT NULL AND position_fx_effect_daily_base IS NOT NULL AND return_of_capital_daily_base IS NOT NULL AND capitalized_fee_daily_base IS NOT NULL AND capitalized_tax_daily_base IS NOT NULL AND disposal_fee_in_realized_daily_base IS NOT NULL AND disposal_tax_in_realized_daily_base IS NOT NULL AND realized_pnl_daily_base + unrealized_pnl_change_base = local_price_effect_daily_base + position_fx_effect_daily_base + return_of_capital_daily_base - capitalized_fee_daily_base - capitalized_tax_daily_base - disposal_fee_in_realized_daily_base - disposal_tax_in_realized_daily_base + position_attribution_rounding_adjustment_base) OR (position_attribution_coverage_state <> 'complete' AND position_attribution_residual_exact IS NULL)",
        name="position_attribution",
    ),
    CheckConstraint(
        "valuation_coverage_state IN ('complete', 'partial', 'unavailable') AND book_pnl_coverage_state IN ('complete', 'partial', 'unavailable') AND position_attribution_coverage_state IN ('complete', 'partial', 'unavailable')",
        name="coverage_states",
    ),
    CheckConstraint(
        "((valuation_coverage_state = 'complete') = (cardinality(valuation_coverage_reason_codes) = 0)) AND ((book_pnl_coverage_state = 'complete') = (cardinality(book_pnl_reason_codes) = 0)) AND ((position_attribution_coverage_state = 'complete') = (cardinality(position_attribution_reason_codes) = 0))",
        name="coverage_reasons",
    ),
    CheckConstraint(
        "measured_market_value = (valuation_coverage_state = 'complete') AND measured_book_pnl = (book_pnl_coverage_state = 'complete')",
        name="measured_coverage",
    ),
    CheckConstraint(
        "valuation_endpoint_status IN ('fresh', 'carry_forward', 'stale', 'unavailable')",
        name="valuation_status",
    ),
    CheckConstraint(
        "(valuation_endpoint_status = 'fresh' AND cardinality(valuation_reason_codes) = 0) OR (valuation_endpoint_status <> 'fresh' AND cardinality(valuation_reason_codes) > 0)",
        name="valuation_reasons",
    ),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_balance_output = Table(
    "portfolio_daily_balance_output",
    portfolio_daily_metadata,
    *_output_identity(),
    Column("as_of_date", Date, nullable=False),
    Column("account_id", String(255), nullable=False),
    Column("component_type", String(32), nullable=False),
    Column("component_key", String(255), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("measured_base_amount", Boolean, nullable=False),
    Column("local_amount", MONEY, nullable=False),
    Column("adopted_fx_rate_exact", DERIVED_RATE_EVIDENCE),
    Column("fx_rate_to_base", RATE),
    Column("base_amount_exact", ACCOUNTING_EVIDENCE),
    Column("base_amount", MONEY),
    Column("base_rounding_adjustment", MONEY, nullable=False),
    *_coverage_columns(),
    PrimaryKeyConstraint(
        "run_id",
        "output_fencing_token",
        "as_of_date",
        "account_id",
        "component_type",
        "component_key",
        "currency",
        name="pk_pd_balance_output",
    ),
    _run_fk("fk_pd_balance_output_run"),
    _portfolio_fk("fk_pd_balance_output_portfolio"),
    CheckConstraint("output_fencing_token > 0", name="token"),
    CheckConstraint(
        "component_type IN ('settled_cash', 'pending_receivable', 'pending_payable', 'income_accrual', 'fee_accrual', 'tax_accrual', 'other_accrual')",
        name="component",
    ),
    CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
    CheckConstraint(
        "abs(base_rounding_adjustment) <= 0.00000001", name="rounding_bound"
    ),
    CheckConstraint(
        "(measured_base_amount AND adopted_fx_rate_exact IS NOT NULL AND fx_rate_to_base IS NOT NULL AND base_amount_exact IS NOT NULL AND base_amount IS NOT NULL AND adopted_fx_rate_exact > 0 AND adopted_fx_rate_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND fx_rate_to_base = calculation_registry.round_half_even(adopted_fx_rate_exact, 18) AND base_amount_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND base_amount_exact = local_amount * adopted_fx_rate_exact AND base_amount = calculation_registry.round_half_even(base_amount_exact, 8) + base_rounding_adjustment) OR (NOT measured_base_amount AND adopted_fx_rate_exact IS NULL AND fx_rate_to_base IS NULL AND base_amount_exact IS NULL AND base_amount IS NULL AND base_rounding_adjustment = 0)",
        name="measured_base",
    ),
    CheckConstraint(
        "measured_base_amount = (coverage_state = 'complete')", name="measured_coverage"
    ),
    *_coverage_constraints(),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_lot_output = Table(
    "portfolio_daily_lot_output",
    portfolio_daily_metadata,
    *_output_identity(),
    Column("as_of_date", Date, nullable=False),
    Column("account_id", String(255), nullable=False),
    Column("instrument_id", String(255), nullable=False),
    Column("lot_id", String(255), nullable=False),
    Column("source_transaction_id", String(255), nullable=False),
    Column("source_revision_id", String(255), nullable=False),
    Column("source_revision_number", Integer, nullable=False),
    Column("custody_transaction_id", String(255), nullable=False),
    Column("custody_revision_id", String(255), nullable=False),
    Column("custody_revision_number", Integer, nullable=False),
    Column("acquisition_date", Date, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("open_quantity_exact", EXACT_QUANTITY, nullable=False),
    Column("open_quantity", QUANTITY, nullable=False),
    Column("measured_base_cost", Boolean, nullable=False),
    Column("acquisition_fx_rate_exact", DERIVED_RATE_EVIDENCE),
    Column("acquisition_fx_rate", RATE),
    Column("cost_basis_local_exact", ACCOUNTING_EVIDENCE, nullable=False),
    Column("cost_basis_local", MONEY, nullable=False),
    Column("unit_cost_local", PRICE, nullable=False),
    Column(
        "unit_cost_local_rounding_residual_exact", ACCOUNTING_EVIDENCE, nullable=False
    ),
    Column("cost_basis_base_exact", ACCOUNTING_EVIDENCE),
    Column("cost_basis_base", MONEY),
    Column("unit_cost_base", PRICE),
    Column("unit_cost_base_rounding_residual_exact", ACCOUNTING_EVIDENCE),
    Column("local_cost_rounding_adjustment", MONEY, nullable=False),
    Column("base_cost_rounding_adjustment", MONEY),
    Column("base_cost_coverage_state", String(16), nullable=False),
    Column("base_cost_reason_codes", REASON_CODES, nullable=False),
    PrimaryKeyConstraint(
        "run_id",
        "output_fencing_token",
        "as_of_date",
        "account_id",
        "instrument_id",
        "lot_id",
        name="pk_pd_lot_output",
    ),
    _run_fk("fk_pd_lot_output_run"),
    _portfolio_fk("fk_pd_lot_output_portfolio"),
    CheckConstraint("output_fencing_token > 0", name="token"),
    CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
    CheckConstraint(
        "open_quantity_exact > 0 AND open_quantity_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND open_quantity = calculation_registry.round_half_even(open_quantity_exact, 12)",
        name="quantity",
    ),
    CheckConstraint(
        "source_revision_number > 0 AND custody_revision_number > 0",
        name="source_revision",
    ),
    CheckConstraint(
        "unit_cost_local >= 0 AND unit_cost_local_rounding_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND unit_cost_local_rounding_residual_exact = cost_basis_local_exact - open_quantity_exact * unit_cost_local",
        name="unit_cost_local",
    ),
    CheckConstraint(
        "cost_basis_local_exact >= 0 AND cost_basis_local_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND cost_basis_local = calculation_registry.round_half_even(cost_basis_local_exact, 8) + local_cost_rounding_adjustment",
        name="cost_bridge_local",
    ),
    CheckConstraint(
        "abs(local_cost_rounding_adjustment) <= 0.00000001", name="local_rounding_bound"
    ),
    CheckConstraint(
        "base_cost_coverage_state IN ('complete', 'unavailable') AND measured_base_cost = (base_cost_coverage_state = 'complete') AND ((base_cost_coverage_state = 'complete') = (cardinality(base_cost_reason_codes) = 0))",
        name="base_coverage",
    ),
    CheckConstraint(
        "((acquisition_fx_rate_exact IS NULL AND acquisition_fx_rate IS NULL) OR (acquisition_fx_rate_exact IS NOT NULL AND acquisition_fx_rate IS NOT NULL AND acquisition_fx_rate_exact > 0 AND acquisition_fx_rate_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND acquisition_fx_rate = calculation_registry.round_half_even(acquisition_fx_rate_exact, 18))) AND ((measured_base_cost AND cost_basis_base_exact IS NOT NULL AND cost_basis_base IS NOT NULL AND unit_cost_base IS NOT NULL AND unit_cost_base_rounding_residual_exact IS NOT NULL AND base_cost_rounding_adjustment IS NOT NULL AND cost_basis_base_exact >= 0 AND cost_basis_base_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND unit_cost_base >= 0 AND unit_cost_base_rounding_residual_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND unit_cost_base_rounding_residual_exact = cost_basis_base_exact - open_quantity_exact * unit_cost_base AND cost_basis_base = calculation_registry.round_half_even(cost_basis_base_exact, 8) + base_cost_rounding_adjustment AND abs(base_cost_rounding_adjustment) <= 0.00000001) OR (NOT measured_base_cost AND acquisition_fx_rate_exact IS NULL AND acquisition_fx_rate IS NULL AND cost_basis_base_exact IS NULL AND cost_basis_base IS NULL AND unit_cost_base IS NULL AND unit_cost_base_rounding_residual_exact IS NULL AND base_cost_rounding_adjustment IS NULL))",
        name="base_cost",
    ),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_lot_disposition_output = Table(
    "portfolio_daily_lot_disposition_output",
    portfolio_daily_metadata,
    *_output_identity(),
    Column("as_of_date", Date, nullable=False),
    Column("account_id", String(255), nullable=False),
    Column("instrument_id", String(255), nullable=False),
    Column("lot_id", String(255), nullable=False),
    Column("acquisition_transaction_id", String(255), nullable=False),
    Column("acquisition_revision_id", String(255), nullable=False),
    Column("acquisition_revision_number", Integer, nullable=False),
    Column("custody_transaction_id", String(255), nullable=False),
    Column("custody_revision_id", String(255), nullable=False),
    Column("custody_revision_number", Integer, nullable=False),
    Column("match_sequence", Integer, nullable=False),
    Column("disposition_transaction_id", String(255), nullable=False),
    Column("disposition_revision_id", String(255), nullable=False),
    Column("disposition_revision_number", Integer, nullable=False),
    Column("disposition_date", Date, nullable=False),
    Column("disposition_kind", String(32), nullable=False),
    Column("matching_method", String(32), nullable=False),
    Column("matching_policy_version", String(64), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("disposed_quantity_exact", EXACT_QUANTITY, nullable=False),
    Column("disposed_quantity", QUANTITY, nullable=False),
    Column("proceeds_local_exact", ACCOUNTING_EVIDENCE, nullable=False),
    Column("proceeds_local", MONEY, nullable=False),
    Column("allocated_cost_local_exact", ACCOUNTING_EVIDENCE, nullable=False),
    Column("allocated_cost_local", MONEY, nullable=False),
    Column("realized_pnl_local_exact", ACCOUNTING_EVIDENCE, nullable=False),
    Column("realized_pnl_local", MONEY, nullable=False),
    Column("proceeds_local_rounding_adjustment", MONEY, nullable=False),
    Column("allocated_cost_local_rounding_adjustment", MONEY, nullable=False),
    Column("realized_pnl_local_rounding_adjustment", MONEY, nullable=False),
    Column("measured_base_pnl", Boolean, nullable=False),
    Column("disposition_fx_rate_exact", DERIVED_RATE_EVIDENCE),
    Column("disposition_fx_rate", RATE),
    Column("proceeds_base_exact", ACCOUNTING_EVIDENCE),
    Column("proceeds_base", MONEY),
    Column("allocated_cost_base_exact", ACCOUNTING_EVIDENCE),
    Column("allocated_cost_base", MONEY),
    Column("realized_pnl_base_exact", ACCOUNTING_EVIDENCE),
    Column("realized_pnl_base", MONEY),
    Column("proceeds_base_rounding_adjustment", MONEY),
    Column("allocated_cost_base_rounding_adjustment", MONEY),
    Column("realized_pnl_base_rounding_adjustment", MONEY),
    Column("base_pnl_coverage_state", String(16), nullable=False),
    Column("base_pnl_reason_codes", REASON_CODES, nullable=False),
    PrimaryKeyConstraint(
        "run_id",
        "output_fencing_token",
        "as_of_date",
        "account_id",
        "instrument_id",
        "lot_id",
        "disposition_transaction_id",
        "match_sequence",
        name="pk_pd_lot_disposition_output",
    ),
    _run_fk("fk_pd_lot_disposition_run"),
    _portfolio_fk("fk_pd_lot_disposition_portfolio"),
    CheckConstraint("output_fencing_token > 0", name="token"),
    CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
    CheckConstraint(
        "match_sequence > 0 AND acquisition_revision_number > 0 AND custody_revision_number > 0 AND disposition_revision_number > 0 AND disposition_date <= as_of_date",
        name="sequence_date",
    ),
    CheckConstraint(
        "disposition_kind IN ('sale', 'maturity', 'transfer_out')", name="kind"
    ),
    CheckConstraint(
        "matching_method IN ('fifo', 'moving_average', 'specific_identification')",
        name="matching_method",
    ),
    CheckConstraint(
        "disposed_quantity_exact > 0 AND disposed_quantity_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND disposed_quantity = calculation_registry.round_half_even(disposed_quantity_exact, 12)",
        name="quantity",
    ),
    CheckConstraint(
        "proceeds_local_exact >= 0 AND allocated_cost_local_exact >= 0 AND proceeds_local_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND allocated_cost_local_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND realized_pnl_local_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND realized_pnl_local_exact = proceeds_local_exact - allocated_cost_local_exact",
        name="local_exact",
    ),
    CheckConstraint(
        "proceeds_local = calculation_registry.round_half_even(proceeds_local_exact, 8) + proceeds_local_rounding_adjustment AND allocated_cost_local = calculation_registry.round_half_even(allocated_cost_local_exact, 8) + allocated_cost_local_rounding_adjustment AND realized_pnl_local = calculation_registry.round_half_even(realized_pnl_local_exact, 8) + realized_pnl_local_rounding_adjustment AND realized_pnl_local = proceeds_local - allocated_cost_local AND abs(proceeds_local_rounding_adjustment) <= 0.00000001 AND abs(allocated_cost_local_rounding_adjustment) <= 0.00000001 AND abs(realized_pnl_local_rounding_adjustment) <= 0.00000003",
        name="local_published",
    ),
    CheckConstraint(
        "base_pnl_coverage_state IN ('complete', 'unavailable') AND measured_base_pnl = (base_pnl_coverage_state = 'complete') AND ((base_pnl_coverage_state = 'complete') = (cardinality(base_pnl_reason_codes) = 0))",
        name="base_coverage",
    ),
    CheckConstraint(
        "(measured_base_pnl AND disposition_fx_rate_exact > 0 AND disposition_fx_rate_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND disposition_fx_rate = calculation_registry.round_half_even(disposition_fx_rate_exact, 18) AND proceeds_base_exact = proceeds_local_exact * disposition_fx_rate_exact AND allocated_cost_base_exact::text NOT IN ('NaN', 'Infinity', '-Infinity') AND realized_pnl_base_exact = proceeds_base_exact - allocated_cost_base_exact AND proceeds_base = calculation_registry.round_half_even(proceeds_base_exact, 8) + proceeds_base_rounding_adjustment AND allocated_cost_base = calculation_registry.round_half_even(allocated_cost_base_exact, 8) + allocated_cost_base_rounding_adjustment AND realized_pnl_base = calculation_registry.round_half_even(realized_pnl_base_exact, 8) + realized_pnl_base_rounding_adjustment AND realized_pnl_base = proceeds_base - allocated_cost_base AND abs(proceeds_base_rounding_adjustment) <= 0.00000001 AND abs(allocated_cost_base_rounding_adjustment) <= 0.00000001 AND abs(realized_pnl_base_rounding_adjustment) <= 0.00000003) OR (NOT measured_base_pnl AND disposition_fx_rate_exact IS NULL AND disposition_fx_rate IS NULL AND proceeds_base_exact IS NULL AND proceeds_base IS NULL AND allocated_cost_base_exact IS NULL AND allocated_cost_base IS NULL AND realized_pnl_base_exact IS NULL AND realized_pnl_base IS NULL AND proceeds_base_rounding_adjustment IS NULL AND allocated_cost_base_rounding_adjustment IS NULL AND realized_pnl_base_rounding_adjustment IS NULL)",
        name="base_pnl",
    ),
    schema=PORTFOLIO_SCHEMA,
)


portfolio_daily_contribution_output = Table(
    "portfolio_daily_contribution_output",
    portfolio_daily_metadata,
    *_output_identity(),
    Column("as_of_date", Date, nullable=False),
    Column("axis", String(32), nullable=False),
    Column("group_key", String(255), nullable=False),
    Column("group_label", String(500), nullable=False),
    Column("measured", Boolean, nullable=False),
    Column("opening_nav_exact", ACCOUNTING_EVIDENCE),
    Column("opening_nav", MONEY),
    Column("opening_nav_rounding_adjustment", MONEY),
    Column("closing_nav_exact", ACCOUNTING_EVIDENCE),
    Column("closing_nav", MONEY),
    Column("closing_nav_rounding_adjustment", MONEY),
    Column("external_flow_in_exact", ACCOUNTING_EVIDENCE),
    Column("external_flow_in", MONEY),
    Column("external_flow_in_rounding_adjustment", MONEY),
    Column("external_flow_out_exact", ACCOUNTING_EVIDENCE),
    Column("external_flow_out", MONEY),
    Column("external_flow_out_rounding_adjustment", MONEY),
    Column("internal_flow_in_exact", ACCOUNTING_EVIDENCE),
    Column("internal_flow_in", MONEY),
    Column("internal_flow_in_rounding_adjustment", MONEY),
    Column("internal_flow_out_exact", ACCOUNTING_EVIDENCE),
    Column("internal_flow_out", MONEY),
    Column("internal_flow_out_rounding_adjustment", MONEY),
    Column("economic_pnl_exact", ACCOUNTING_EVIDENCE),
    Column("economic_pnl", MONEY),
    Column("economic_pnl_rounding_adjustment", MONEY),
    Column("contribution_method50", METHOD),
    Column("contribution_published", RATE),
    Column("contribution_division_adjustment_exact", METHOD_EVIDENCE, nullable=False),
    Column("contribution_rounding_adjustment", RATE),
    Column("closure_residual_exact", ACCOUNTING_EVIDENCE, nullable=False),
    Column("rounding_adjustment_base", MONEY, nullable=False),
    *_coverage_columns(),
    PrimaryKeyConstraint(
        "run_id",
        "output_fencing_token",
        "as_of_date",
        "axis",
        "group_key",
        name="pk_pd_contribution_output",
    ),
    _run_fk("fk_pd_contribution_output_run"),
    _portfolio_fk("fk_pd_contribution_output_portfolio"),
    CheckConstraint("output_fencing_token > 0", name="token"),
    CheckConstraint(
        "axis IN ('portfolio', 'account', 'instrument', 'currency', 'taxonomy')",
        name="axis",
    ),
    CheckConstraint(
        "(external_flow_in_exact IS NULL OR external_flow_in_exact >= 0) AND (external_flow_out_exact IS NULL OR external_flow_out_exact >= 0) AND (internal_flow_in_exact IS NULL OR internal_flow_in_exact >= 0) AND (internal_flow_out_exact IS NULL OR internal_flow_out_exact >= 0) AND (external_flow_in IS NULL OR external_flow_in >= 0) AND (external_flow_out IS NULL OR external_flow_out >= 0) AND (internal_flow_in IS NULL OR internal_flow_in >= 0) AND (internal_flow_out IS NULL OR internal_flow_out >= 0)",
        name="flows",
    ),
    CheckConstraint(
        "abs(rounding_adjustment_base) <= 0.00000011", name="rounding_bound"
    ),
    CheckConstraint(
        "closure_residual_exact = 0 AND contribution_division_adjustment_exact::text NOT IN ('NaN', 'Infinity', '-Infinity')",
        name="closure",
    ),
    CheckConstraint(
        _logical_numeric_domain(
            "contribution_method50",
            precision=METHOD_DECIMAL_STORAGE_PRECISION,
            scale=METHOD_DECIMAL_STORAGE_SCALE,
        )
        + " AND (contribution_method50 IS NULL OR (contribution_method50::text NOT IN ('NaN', 'Infinity', '-Infinity') AND contribution_method50 = calculation_registry.round_significant_half_even(contribution_method50, 50)))",
        name="method_storage_domain",
    ),
    CheckConstraint(
        "(measured AND opening_nav_exact IS NOT NULL AND closing_nav_exact IS NOT NULL AND external_flow_in_exact IS NOT NULL AND external_flow_out_exact IS NOT NULL AND internal_flow_in_exact IS NOT NULL AND internal_flow_out_exact IS NOT NULL AND economic_pnl_exact IS NOT NULL AND contribution_method50 IS NOT NULL AND opening_nav IS NOT NULL AND closing_nav IS NOT NULL AND external_flow_in IS NOT NULL AND external_flow_out IS NOT NULL AND internal_flow_in IS NOT NULL AND internal_flow_out IS NOT NULL AND economic_pnl IS NOT NULL AND contribution_published IS NOT NULL AND opening_nav_rounding_adjustment IS NOT NULL AND closing_nav_rounding_adjustment IS NOT NULL AND external_flow_in_rounding_adjustment IS NOT NULL AND external_flow_out_rounding_adjustment IS NOT NULL AND internal_flow_in_rounding_adjustment IS NOT NULL AND internal_flow_out_rounding_adjustment IS NOT NULL AND economic_pnl_rounding_adjustment IS NOT NULL AND contribution_rounding_adjustment IS NOT NULL AND closing_nav_exact + external_flow_out_exact + internal_flow_out_exact = opening_nav_exact + external_flow_in_exact + internal_flow_in_exact + economic_pnl_exact AND opening_nav = calculation_registry.round_half_even(opening_nav_exact, 8) + opening_nav_rounding_adjustment AND closing_nav = calculation_registry.round_half_even(closing_nav_exact, 8) + closing_nav_rounding_adjustment AND external_flow_in = calculation_registry.round_half_even(external_flow_in_exact, 8) + external_flow_in_rounding_adjustment AND external_flow_out = calculation_registry.round_half_even(external_flow_out_exact, 8) + external_flow_out_rounding_adjustment AND internal_flow_in = calculation_registry.round_half_even(internal_flow_in_exact, 8) + internal_flow_in_rounding_adjustment AND internal_flow_out = calculation_registry.round_half_even(internal_flow_out_exact, 8) + internal_flow_out_rounding_adjustment AND economic_pnl = calculation_registry.round_half_even(economic_pnl_exact, 8) + economic_pnl_rounding_adjustment AND contribution_published = calculation_registry.round_half_even(contribution_method50 + contribution_division_adjustment_exact, 18) + contribution_rounding_adjustment AND abs(opening_nav_rounding_adjustment) <= 0.00000001 AND abs(closing_nav_rounding_adjustment) <= 0.00000001 AND abs(external_flow_in_rounding_adjustment) <= 0.00000001 AND abs(external_flow_out_rounding_adjustment) <= 0.00000001 AND abs(internal_flow_in_rounding_adjustment) <= 0.00000001 AND abs(internal_flow_out_rounding_adjustment) <= 0.00000001 AND abs(economic_pnl_rounding_adjustment) <= 0.00000001 AND abs(contribution_rounding_adjustment) <= 0.000000000000000001 AND closing_nav + external_flow_out + internal_flow_out = opening_nav + external_flow_in + internal_flow_in + economic_pnl + rounding_adjustment_base) OR (NOT measured AND opening_nav_exact IS NULL AND opening_nav IS NULL AND opening_nav_rounding_adjustment IS NULL AND closing_nav_exact IS NULL AND closing_nav IS NULL AND closing_nav_rounding_adjustment IS NULL AND external_flow_in_exact IS NULL AND external_flow_in IS NULL AND external_flow_in_rounding_adjustment IS NULL AND external_flow_out_exact IS NULL AND external_flow_out IS NULL AND external_flow_out_rounding_adjustment IS NULL AND internal_flow_in_exact IS NULL AND internal_flow_in IS NULL AND internal_flow_in_rounding_adjustment IS NULL AND internal_flow_out_exact IS NULL AND internal_flow_out IS NULL AND internal_flow_out_rounding_adjustment IS NULL AND economic_pnl_exact IS NULL AND economic_pnl IS NULL AND economic_pnl_rounding_adjustment IS NULL AND contribution_method50 IS NULL AND contribution_published IS NULL AND contribution_rounding_adjustment IS NULL AND contribution_division_adjustment_exact = 0 AND rounding_adjustment_base = 0)",
        name="bridge",
    ),
    *_coverage_constraints(),
    schema=PORTFOLIO_SCHEMA,
)


def _append_logical_domain_constraint(
    table: Table,
    *,
    name: str,
    column_names: tuple[str, ...],
    precision: int,
    scale: int,
) -> None:
    table.append_constraint(
        CheckConstraint(
            _all_logical_numeric_domains(
                column_names,
                precision=precision,
                scale=scale,
            ),
            name=name,
        )
    )


# Source/adopted/evidence columns deliberately use physical unbounded NUMERIC.
# These constraints are the logical type system and must remain aligned with
# the application persistence-boundary maps in manifest_repository.py and
# output_repository.py.
_append_logical_domain_constraint(
    portfolio_daily_instrument_input,
    name="exact_qty_domain",
    column_names=("contract_multiplier", "price_factor"),
    precision=EXACT_QUANTITY_STORAGE_PRECISION,
    scale=EXACT_QUANTITY_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_corp_action_input,
    name="source_rate_domain",
    column_names=("new_units", "old_units"),
    precision=SOURCE_RATE_STORAGE_PRECISION,
    scale=SOURCE_RATE_STORAGE_SCALE,
)
portfolio_daily_quote_candidate.append_constraint(
    CheckConstraint(
        "decision <> 'adopted' OR (quote_value IS NOT NULL AND "
        + _logical_numeric_domain(
            "quote_value",
            precision=SOURCE_PRICE_STORAGE_PRECISION,
            scale=SOURCE_PRICE_STORAGE_SCALE,
        )
        + ")",
        name="adopted_price_domain",
    )
)
_append_logical_domain_constraint(
    portfolio_daily_fx_path,
    name="derived_rate_domain",
    column_names=("resolved_rate",),
    precision=DERIVED_RATE_STORAGE_PRECISION,
    scale=DERIVED_RATE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_fx_path,
    name="method_ev_domain",
    column_names=("rate_derivation_residual_exact",),
    precision=METHOD_EVIDENCE_STORAGE_PRECISION,
    scale=METHOD_EVIDENCE_STORAGE_SCALE,
)
portfolio_daily_fx_leg.append_constraint(
    CheckConstraint(
        "leg_resolution_status <> 'resolved' OR (quoted_rate IS NOT NULL AND "
        + _logical_numeric_domain(
            "quoted_rate",
            precision=SOURCE_RATE_STORAGE_PRECISION,
            scale=SOURCE_RATE_STORAGE_SCALE,
        )
        + ")",
        name="resolved_rate_domain",
    )
)
_append_logical_domain_constraint(
    portfolio_daily_fx_leg,
    name="derived_rate_domain",
    column_names=("effective_rate",),
    precision=DERIVED_RATE_STORAGE_PRECISION,
    scale=DERIVED_RATE_STORAGE_SCALE,
)
portfolio_daily_fx_leg.append_constraint(
    CheckConstraint(
        "effective_rate IS NULL OR effective_rate = "
        "calculation_registry.round_significant_half_even(effective_rate, 50)",
        name="effective_rate_precision",
    )
)
_append_logical_domain_constraint(
    portfolio_daily_fx_leg,
    name="method_ev_domain",
    column_names=("rate_derivation_residual_exact",),
    precision=METHOD_EVIDENCE_STORAGE_PRECISION,
    scale=METHOD_EVIDENCE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_run_output,
    name="acct_ev_domain",
    column_names=(
        "ledger_balance_residual_exact",
        "nav_bridge_residual_exact",
        "pnl_residual_exact",
        "lot_residual_exact",
    ),
    precision=ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
    scale=ACCOUNTING_EVIDENCE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_run_output,
    name="method_ev_domain",
    column_names=("twr_residual_exact",),
    precision=METHOD_EVIDENCE_STORAGE_PRECISION,
    scale=METHOD_EVIDENCE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_snapshot_output,
    name="acct_ev_domain",
    column_names=(
        "position_attribution_residual_exact",
        "reliable_anchor_nav_exact",
    ),
    precision=ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
    scale=ACCOUNTING_EVIDENCE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_holding_output,
    name="source_price_domain",
    column_names=("adopted_price_exact",),
    precision=SOURCE_PRICE_STORAGE_PRECISION,
    scale=SOURCE_PRICE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_holding_output,
    name="exact_qty_domain",
    column_names=(
        "quantity_exact",
        "contract_multiplier_exact",
        "price_factor_exact",
    ),
    precision=EXACT_QUANTITY_STORAGE_PRECISION,
    scale=EXACT_QUANTITY_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_holding_output,
    name="derived_rate_domain",
    column_names=("adopted_fx_rate_exact",),
    precision=DERIVED_RATE_STORAGE_PRECISION,
    scale=DERIVED_RATE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_holding_output,
    name="acct_ev_domain",
    column_names=(
        "market_value_local_exact",
        "market_value_base_exact",
        "position_attribution_residual_exact",
    ),
    precision=ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
    scale=ACCOUNTING_EVIDENCE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_balance_output,
    name="derived_rate_domain",
    column_names=("adopted_fx_rate_exact",),
    precision=DERIVED_RATE_STORAGE_PRECISION,
    scale=DERIVED_RATE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_balance_output,
    name="acct_ev_domain",
    column_names=("base_amount_exact",),
    precision=ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
    scale=ACCOUNTING_EVIDENCE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_lot_output,
    name="exact_qty_domain",
    column_names=("open_quantity_exact",),
    precision=EXACT_QUANTITY_STORAGE_PRECISION,
    scale=EXACT_QUANTITY_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_lot_output,
    name="derived_rate_domain",
    column_names=("acquisition_fx_rate_exact",),
    precision=DERIVED_RATE_STORAGE_PRECISION,
    scale=DERIVED_RATE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_lot_output,
    name="acct_ev_domain",
    column_names=(
        "cost_basis_local_exact",
        "unit_cost_local_rounding_residual_exact",
        "cost_basis_base_exact",
        "unit_cost_base_rounding_residual_exact",
    ),
    precision=ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
    scale=ACCOUNTING_EVIDENCE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_lot_disposition_output,
    name="exact_qty_domain",
    column_names=("disposed_quantity_exact",),
    precision=EXACT_QUANTITY_STORAGE_PRECISION,
    scale=EXACT_QUANTITY_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_lot_disposition_output,
    name="derived_rate_domain",
    column_names=("disposition_fx_rate_exact",),
    precision=DERIVED_RATE_STORAGE_PRECISION,
    scale=DERIVED_RATE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_lot_disposition_output,
    name="acct_ev_domain",
    column_names=(
        "proceeds_local_exact",
        "allocated_cost_local_exact",
        "realized_pnl_local_exact",
        "proceeds_base_exact",
        "allocated_cost_base_exact",
        "realized_pnl_base_exact",
    ),
    precision=ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
    scale=ACCOUNTING_EVIDENCE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_contribution_output,
    name="acct_ev_domain",
    column_names=(
        "opening_nav_exact",
        "closing_nav_exact",
        "external_flow_in_exact",
        "external_flow_out_exact",
        "internal_flow_in_exact",
        "internal_flow_out_exact",
        "economic_pnl_exact",
        "closure_residual_exact",
    ),
    precision=ACCOUNTING_EVIDENCE_STORAGE_PRECISION,
    scale=ACCOUNTING_EVIDENCE_STORAGE_SCALE,
)
_append_logical_domain_constraint(
    portfolio_daily_contribution_output,
    name="method_ev_domain",
    column_names=("contribution_division_adjustment_exact",),
    precision=METHOD_EVIDENCE_STORAGE_PRECISION,
    scale=METHOD_EVIDENCE_STORAGE_SCALE,
)

_append_logical_domain_constraint(
    portfolio_daily_transaction_input,
    name="quantity_price_domain",
    column_names=("quantity", "price"),
    precision=38,
    scale=12,
)
_append_logical_domain_constraint(
    portfolio_daily_transaction_input,
    name="amount_domain",
    column_names=("gross_amount", "counter_amount", "fees", "taxes"),
    precision=38,
    scale=8,
)
_append_logical_domain_constraint(
    portfolio_daily_transaction_input,
    name="quoted_fx_rate_domain",
    column_names=("quoted_fx_rate",),
    precision=38,
    scale=18,
)
_append_logical_domain_constraint(
    portfolio_daily_transaction_input,
    name="terms_evidence_domain",
    column_names=(
        "consideration_terms_difference_exact",
        "quoted_terms_difference_exact",
    ),
    precision=84,
    scale=26,
)
_append_logical_domain_constraint(
    portfolio_daily_transaction_input,
    name="effective_fx_rate_domain",
    column_names=("effective_fx_rate_method50",),
    precision=METHOD_DECIMAL_STORAGE_PRECISION,
    scale=METHOD_DECIMAL_STORAGE_SCALE,
)
portfolio_daily_transaction_input.append_constraint(
    CheckConstraint(
        "effective_fx_rate_method50 IS NULL OR "
        "effective_fx_rate_method50 = calculation_registry."
        "round_significant_half_even(effective_fx_rate_method50, 50)",
        name="effective_fx_rate_precision",
    )
)


DEPENDENCY_TABLES = (
    portfolio_daily_config_input,
    portfolio_daily_account_input,
    portfolio_daily_transaction_input,
    portfolio_daily_instrument_input,
    portfolio_daily_corp_action_window,
    portfolio_daily_corp_action_input,
    portfolio_daily_quote_window,
    portfolio_daily_quote_candidate,
    portfolio_daily_fx_path,
    portfolio_daily_fx_leg,
    portfolio_daily_prior_publication,
)

OUTPUT_TABLES = (
    portfolio_daily_run_output,
    portfolio_daily_snapshot_output,
    portfolio_daily_holding_output,
    portfolio_daily_balance_output,
    portfolio_daily_lot_output,
    portfolio_daily_lot_disposition_output,
    portfolio_daily_contribution_output,
)

ALL_TABLES = DEPENDENCY_TABLES + OUTPUT_TABLES


# Publication and read paths always filter by the attempt prefix.  These
# additional indexes keep current-publication portfolio/date reads efficient.
Index(
    "ix_pd_snapshot_portfolio_date",
    portfolio_daily_snapshot_output.c.portfolio_id,
    portfolio_daily_snapshot_output.c.as_of_date,
)
Index(
    "ix_pd_holding_portfolio_date",
    portfolio_daily_holding_output.c.portfolio_id,
    portfolio_daily_holding_output.c.as_of_date,
)
Index(
    "ix_pd_balance_portfolio_date",
    portfolio_daily_balance_output.c.portfolio_id,
    portfolio_daily_balance_output.c.as_of_date,
)
Index(
    "ix_pd_lot_portfolio_date",
    portfolio_daily_lot_output.c.portfolio_id,
    portfolio_daily_lot_output.c.as_of_date,
)
Index(
    "ix_pd_lot_disposition_portfolio_date",
    portfolio_daily_lot_disposition_output.c.portfolio_id,
    portfolio_daily_lot_disposition_output.c.as_of_date,
)
Index(
    "ix_pd_contrib_portfolio_date",
    portfolio_daily_contribution_output.c.portfolio_id,
    portfolio_daily_contribution_output.c.as_of_date,
)


__all__ = [
    "ALL_TABLES",
    "DEPENDENCY_TABLES",
    "METHOD",
    "METHOD_ROUNDING_ADJUSTMENT",
    "OUTPUT_TABLES",
    "portfolio_daily_metadata",
    *(table.name for table in ALL_TABLES),
]
