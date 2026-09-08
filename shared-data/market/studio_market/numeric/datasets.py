from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    name: str
    keys: tuple[str, ...]
    symbol: str = "symbol"
    date: str | None = None
    current_keys: tuple[str, ...] = ()
    historical_use: str = "since_capture"
    source_table: str | None = None
    description: str = ""


def D(name, keys, *, symbol="symbol", date=None, current=(), history="since_capture", table=None, description=""):
    return Dataset(name, tuple(keys.split()), symbol, date, tuple(current.split()) if isinstance(current,str) else current, history, table, description or name.replace("_", " "))


DATASETS = {d.name: d for d in [
    D("security_directory", "symbol", current="symbol", table="security_directory_observations"),
    D("delisted_securities", "symbol", date="delisted_date", current="symbol", table="delisted_security_observations"),
    D("symbol_changes", "old_symbol new_symbol event_date", symbol="new_symbol", date="event_date", history="provider_history_with_current_revisions", table="symbol_change_observations"),
    D("regime_market_daily", "series_id date", symbol="series_id", date="date", current="series_id", history="provider_history_with_current_revisions"),
    D("raw_eod_daily", "symbol date", date="date", current="symbol", history="provider_history_with_current_revisions"),
    D("cn_security_directory", "symbol", current="symbol"),
    D("us_eod_daily", "symbol date", date="date", current="symbol", history="provider_history_with_current_revisions", table="us_eod_observations"),
    D("dividends", "symbol ex_date", date="ex_date", history="provider_history_with_current_revisions", table="dividend_observations"),
    D("stock_splits", "symbol event_date", date="event_date", history="provider_history_with_current_revisions", table="split_observations"),
    D("financial_statements", "symbol statement_type period_end fiscal_year fiscal_period", date="period_end", history="provider_history_with_current_revisions", table="financial_statement_observations"),
    D("financial_facts", "symbol statement_type period_end fiscal_year fiscal_period line_item", date="period_end", history="provider_history_with_current_revisions"),
    D("as_reported_statements", "symbol period_end fiscal_year fiscal_period", date="period_end", history="provider_history_with_current_revisions", table="as_reported_statement_observations"),
    D("as_reported_facts", "symbol period_end fiscal_year fiscal_period concept", date="period_end", history="provider_history_with_current_revisions"),
    D("sec_filings", "filing_key", date="filing_date", history="provider_history_with_current_revisions", table="sec_filing_observations"),
    D("company_profiles", "symbol", current="symbol", table="company_profile_observations"),
    D("provider_reference", "symbol section", current="symbol section", description="Registered asset supplementary public reference observations"),
    D("analyst_estimates", "symbol estimate_period target_period_end", date="target_period_end", current="symbol estimate_period target_period_end", table="analyst_estimate_observations"),
    D("analyst_price_targets", "symbol", current="symbol", table="analyst_price_target_observations"),
    D("analyst_rating_consensus", "symbol", current="symbol", table="analyst_rating_consensus_observations"),
    D("analyst_grade_events", "symbol event_date row_content_sha256", date="event_date", history="provider_history_with_current_revisions", table="analyst_grade_event_observations"),
    D("analyst_grade_snapshots", "symbol snapshot_date", date="snapshot_date", history="provider_history_with_current_revisions", table="analyst_grade_snapshot_observations"),
    D("fmp_quant_ratings", "symbol rating_date", date="rating_date", history="provider_history_with_current_revisions", table="fmp_quant_rating_observations"),
    D("earnings_surprises", "symbol event_date", date="event_date", history="provider_history_with_current_revisions", table="earnings_surprise_observations"),
    D("insider_trades", "symbol row_content_sha256", date="filing_date", table="insider_trade_observations"),
    D("institutional_filings", "manager_cik report_date filing_date", symbol="manager_cik", date="report_date", table="institutional_filing_observations"),
    D("index_constituent_events", "index_id event_date row_content_sha256", date="event_date", history="provider_history_with_current_revisions", table="index_constituent_event_observations"),
    D("index_membership_snapshots", "index_id symbol snapshot_date", date="snapshot_date", table="index_membership_snapshot_observations"),
    D("etf_info", "symbol", current="symbol", table="etf_info_observations"),
    D("etf_holdings", "etf_symbol holding_key", symbol="etf_symbol", date="snapshot_date", table="etf_holding_observations"),
    D("etf_disclosures", "etf_symbol report_date holding_key", symbol="etf_symbol", date="report_date", history="provider_history_with_current_revisions", table="etf_disclosure_observations"),
    D("macro_series", "series_id date", symbol="series_id", date="date", current="series_id", history="provider_history_with_current_revisions", table="macro_observations"),
    D("market_series_daily", "series_id date", symbol="series_id", date="date", current="series_id", history="provider_history_with_current_revisions", table="market_series_observations"),
    D("market_series_catalog", "series_id", symbol="series_id", current="series_id", table="market_series_definitions"),
    D("cn_futures_observations", "dataset observation_key snapshot_date", symbol="product_id", date="observation_date", history="provider_history_with_current_revisions", table="cn_futures_observation_versions"),
    D("cn_futures_products", "product_id", symbol="product_id", current="product_id", table="cn_futures_products"),
    D("cn_futures_dataset_catalog", "dataset", symbol="dataset", current="dataset", table="cn_futures_dataset_catalog"),
    D("cn_equity_daily", "symbol date", date="date", current="symbol", history="provider_history_with_current_revisions"),
    D("cn_equity_daily_basic", "symbol date", date="date", current="symbol", history="provider_history_with_current_revisions"),
    D("cn_financials", "symbol statement_type period_end ann_date", date="period_end", history="provider_history_with_current_revisions"),
]}


def dataset(name: str) -> Dataset:
    try:
        return DATASETS[name]
    except KeyError:
        raise ValueError(f"Unknown numeric dataset: {name}") from None
