import type {
  HoldingsWorkspaceResponse,
  PortfolioDailyPerformancePoint,
  PortfolioHoldingRow,
  PortfolioPerformanceResponse,
  PortfolioWorkspaceSummary,
} from '../lib/api'
import type { InstrumentCore } from '../../../../../packages/instrument-core/ts/src'

export function instrumentFixture(overrides: Partial<InstrumentCore> = {}): InstrumentCore {
  return {
    instrument_id: 'asset-1',
    instrument_name: 'Alpha Fund',
    instrument_type: 'fund',
    currency: 'USD',
    identifiers: [{ identifier_type: 'ticker', identifier_value: 'ALPHA', is_primary: true }],
    ...overrides,
  }
}

export function holdingFixture(overrides: Partial<PortfolioHoldingRow> = {}): PortfolioHoldingRow {
  return {
    line_id: 'holding:asset-1',
    instrument_core: instrumentFixture(),
    quantity: 10,
    last_price: 80,
    quote_as_of_date: '2026-07-15',
    quote_basis: 'official_nav',
    quote_provider: 'fixture',
    quote_status: 'complete',
    market_value: 800,
    market_value_base: 800,
    day_change_pct: 0.01,
    day_change_value: 8,
    day_change_value_base: 8,
    cost_basis_method: 'fifo',
    cost_basis: 700,
    cost_basis_base: 700,
    allocation: 0.8,
    price_chart_1m: [
      { date: '2026-06-15', value: 75 },
      { date: '2026-07-15', value: 80 },
    ],
    price_chart_3m: [
      { date: '2026-04-15', value: 70 },
      { date: '2026-07-15', value: 80 },
    ],
    price_chart_6m: [
      { date: '2026-01-15', value: 65 },
      { date: '2026-07-15', value: 80 },
    ],
    price_chart_1y: [
      { date: '2025-07-15', value: 60 },
      { date: '2026-07-15', value: 80 },
    ],
    instrument_trend_as_of_date: '2026-07-15',
    instrument_trend_basis: 'total_return_nav',
    instrument_trend_coverage: {
      state: 'complete',
      observation_count: 250,
      start_date: '2025-07-14',
      end_date: '2026-07-15',
      available_return_windows: ['1w', '1m', '3m', '6m', 'mtd', 'ytd', '1y'],
    },
    instrument_trend_reason: 'selected_policy_series',
    instrument_trend_split_adjusted: false,
    instrument_risk_frequency: 'daily',
    instrument_return_1w: 0.0123,
    instrument_return_1m: 0.0345,
    instrument_return_3m: 0.0456,
    instrument_return_6m: 0.0678,
    instrument_return_mtd: 0.0234,
    instrument_return_ytd: 0.0567,
    instrument_return_1y: 0.1,
    instrument_volatility_1m: 0.08,
    instrument_volatility_3m: 0.09,
    instrument_volatility_6m: 0.1,
    instrument_volatility_1y: 0.11,
    instrument_current_drawdown: -0.02,
    instrument_max_drawdown: -0.08,
    instrument_holding_max_drawdown: -0.05,
    instrument_holding_start_date: '2026-06-23',
    forward_risk_share: 1,
    coverage_status: 'price-nav-fx',
    account_ids: ['account-1'],
    account_count: 1,
    open_position_lot_count: 1,
    ...overrides,
  }
}

export function holdingsWorkspaceFixture(
  overrides: Partial<HoldingsWorkspaceResponse> = {},
): HoldingsWorkspaceResponse {
  return {
    portfolio_id: '3',
    portfolio_name: 'Contract Portfolio',
    base_currency: 'USD',
    as_of_date: '2026-07-15',
    view_label: 'View: Holdings',
    coverage_note: 'Complete fixture coverage.',
    quality_warnings: [],
    risk_basis: {
      requested_frequency: 'daily',
      resolved_frequency: 'daily',
      default_frequency: 'daily',
      source_frequency_counts: { daily: 1 },
      status_label: 'Daily risk basis',
    },
    summary_cards: [],
    rows: [holdingFixture()],
    totals: {
      market_value: 800,
      day_change_pct: 0.01,
      day_change_value: 8,
      cost_basis: 700,
      allocation: 0.8,
    },
    ...overrides,
  }
}

export function workspaceSummaryFixture(
  overrides: Partial<PortfolioWorkspaceSummary> = {},
): PortfolioWorkspaceSummary {
  return {
    portfolio_id: '3',
    portfolio_name: 'Contract Portfolio',
    base_currency: 'USD',
    as_of_date: '2026-07-15',
    nav: 1000,
    day_change_value: 8,
    day_change_pct: 0.008,
    toolbar_label: 'View: Portfolio Overview',
    badges: [],
    sections: [],
    ...overrides,
  }
}

export function dailyPerformancePoint(
  date: string,
  endingNav: number,
  dailyTwr: number | null,
  cumulativeTwr: number | null,
): PortfolioDailyPerformancePoint {
  return {
    as_of_date: date,
    coverage_state: 'complete',
    valuation_coverage_state: 'complete',
    return_coverage_state: 'complete',
    book_pnl_coverage_state: 'complete',
    attribution_coverage_state: 'complete',
    return_chain_continuous: dailyTwr != null,
    stale_price_flag: false,
    stale_fx_flag: false,
    market_observation_count: 1,
    return_observation_eligible: dailyTwr != null,
    beginning_nav: endingNav,
    ending_nav: endingNav,
    pending_settlement: 0,
    realized_pnl: 0,
    unrealized_pnl: 0,
    income_cash_amount: 0,
    expense_cash_amount: 0,
    cash_currency_gains: 0,
    instrument_currency_gains: 0,
    return_of_capital_amount: 0,
    total_pnl: 0,
    external_cash_in: 0,
    external_cash_out: 0,
    net_external_inflow: 0,
    absolute_change: 0,
    delta: 0,
    daily_twr: dailyTwr,
    cumulative_twr: cumulativeTwr,
    drawdown: 0,
  }
}

export function performanceFixture(
  overrides: Partial<PortfolioPerformanceResponse> = {},
): PortfolioPerformanceResponse {
  const dailySeries = Array.from({ length: 10 }, (_, index) => {
    const day = String(index + 6).padStart(2, '0')
    const finalPoint = index === 9
    return dailyPerformancePoint(
      `2026-07-${day}`,
      finalPoint ? 1000 : 970 + index * 3,
      index === 0 ? null : finalPoint ? 0.0302 : 0,
      finalPoint ? 0.0302 : 0,
    )
  })
  return {
    portfolio_id: '3',
    base_currency: 'USD',
    valuation_timezone: 'Asia/Shanghai',
    valuation_cutoff_policy: 'close',
    summary: {
      start_date: '2026-07-06',
      end_date: '2026-07-15',
      coverage_state: 'complete',
      valuation_coverage_state: 'complete',
      return_coverage_state: 'complete',
      book_pnl_coverage_state: 'complete',
      attribution_coverage_state: 'complete',
      snapshot_count: 10,
      return_observation_count: 9,
      risk_return_observation_count: 9,
      risk_annualization_periods_per_year: 252,
      risk_calculation_frequency: 'daily',
      risk_minimum_sample_count: 2,
      risk_sample_count: 9,
      risk_result_status: 'available',
      risk_unavailable_reason: null,
      latest_complete_as_of_date: '2026-07-15',
      start_nav: 970,
      end_nav: 1000,
      external_cash_in: 0,
      external_cash_out: 0,
      net_external_inflow: 0,
      cumulative_twr: 0.0302,
      annualization_eligible: false,
      annualization_years: 9 / 365.25,
      annualization_unavailable_reason: 'measurement_period_shorter_than_one_year',
      annualized_twr: null,
      irr: null,
      mwror: null,
      irr_solver_status: null,
      irr_unavailable_reason: 'measurement_period_shorter_than_one_year',
      absolute_change: 30,
      delta: 30,
      realized_pnl: 10,
      unrealized_pnl: 20,
      income_cash_amount: 0,
      expense_cash_amount: 0,
      cash_currency_gains: 0,
      instrument_currency_gains: 0,
      return_of_capital_amount: 0,
      total_pnl: 30,
      mean_daily_return: 0.015,
      annualized_return_from_daily_mean: 3.78,
      annualized_volatility: 0.1,
      annualized_downside_volatility: 0.08,
      sharpe_ratio: 1.2,
      sortino_ratio: 1.5,
      current_drawdown: -0.01,
      max_drawdown: -0.03,
      max_drawdown_days: 2,
      drawdown_duration_days: 1,
      quality_warnings: [],
    },
    daily_series: dailySeries,
    ...overrides,
  }
}
