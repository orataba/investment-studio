import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PerformancePage from './pages/PerformancePage'
import { performanceFixture, workspaceSummaryFixture } from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  getWorkspaceSummaryForPortfolio: vi.fn(),
  getPortfolioPerformance: vi.fn(),
  getPortfolioPerformanceCalculation: vi.fn(),
  getPortfolioPerformanceCalculationGroups: vi.fn(),
  getPortfolioTableViewStore: vi.fn(),
  savePortfolioTableViewStore: vi.fn(),
  getPortfolioTaxonomyCatalog: vi.fn(),
  getPortfolioInstruments: vi.fn(),
  getPortfolioInstrumentPriceChart: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))

const windowFilters = {
  start_date: '2026-07-06',
  end_date: '2026-07-15',
}

describe('Performance rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())
    apiMocks.getPortfolioPerformance.mockResolvedValue(performanceFixture())
    apiMocks.getPortfolioPerformanceCalculation.mockResolvedValue({
      portfolio_id: '3',
      base_currency: 'USD',
      valuation_timezone: 'Asia/Shanghai',
      valuation_cutoff_policy: 'close',
      summary: {
        start_date: '2026-07-06',
        end_date: '2026-07-15',
        coverage_state: 'complete',
        stale_price_flag: false,
        stale_fx_flag: false,
        initial_value: 970,
        final_value: 1000,
        delta: 30,
        capital_gains: 30,
        realized_capital_gains: 10,
        unrealized_capital_gains: 20,
        earnings: 0,
        fees: 0,
        taxes: 0,
        cash_currency_gains: 0,
        pending_settlement_currency_gains: -0.51,
        instrument_currency_gains: 0,
        deposits: 0,
        withdrawals: 0,
        net_external_inflow: 0,
      },
      lines: [],
    })
    apiMocks.getPortfolioPerformanceCalculationGroups.mockResolvedValue({
      portfolio_id: '3',
      base_currency: 'USD',
      valuation_timezone: 'Asia/Shanghai',
      valuation_cutoff_policy: 'close',
      summary: {
        axis: 'instrument',
        taxonomy_id: null,
        group_key: null,
        group_label: null,
        start_date: '2026-07-06',
        end_date: '2026-07-15',
        group_count: 0,
        total_initial_value: 970,
        total_final_value: 1000,
        total_delta: 30,
        total_residual_delta: 0,
        total_pnl: 30,
        total_period_contribution: 0.0302,
        contribution_residual: 0,
        risk_calculation_frequency: 'daily',
        risk_frequency_status_label: 'Daily risk basis',
        risk_return_observation_count: 0,
        risk_annualization_periods_per_year: null,
        annualized_volatility: null,
        sharpe_ratio: null,
      },
      groups: [],
    })
    apiMocks.getPortfolioTableViewStore.mockResolvedValue({ store: null })
    apiMocks.savePortfolioTableViewStore.mockResolvedValue({})
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({
      portfolio_id: '3',
      default_planning_taxonomy_id: null,
      taxonomies: [],
      taxonomy_nodes: [],
      taxonomy_assignments: [],
      instrument_universe: [],
      target_sets: [],
      target_set_lines: [],
      target_set_integrity_issues: [],
    })
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [] })
  })

  it('uses canonical defaults for an empty backend store and ignores legacy browser views', async () => {
    const legacyStore = JSON.stringify({
      activeViewId: 'full-calculation',
      views: [],
    })
    window.localStorage.setItem(
      'portfolio_ops.portfolio.performance.calculation.views.v1',
      legacyStore,
    )

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    expect(await screen.findByRole('button', { name: /View\s*: Default/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Begin Weight/ })).toBeInTheDocument()
    expect(
      window.localStorage.getItem('portfolio_ops.portfolio.performance.calculation.views.v1'),
    ).toBe(legacyStore)
  })

  it('marks table views unavailable when the backend view store cannot be loaded', async () => {
    apiMocks.getPortfolioTableViewStore.mockRejectedValueOnce(new Error('View store offline.'))

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('View store offline.')
    expect(screen.getByText('Table views unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /View\s*:/ })).not.toBeInTheDocument()
  })

  // Characterization gap: the restored PerformancePage currently has no chart DOM.
  // Keep the shared-window assertion on summary and Calculation until that surface exists.
  it('uses one selected date window for summary and Calculation and keeps ordinary returns at two decimals', async () => {
    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    const periodReturnRow = await screen.findByRole('row', { name: /Total Portfolio Return/ })
    expect(within(periodReturnRow).getByText('+3.02%')).toBeInTheDocument()
    expect(screen.getByText('Calculation')).toBeInTheDocument()
    expect(screen.getAllByText(/2026-07-06 to 2026-07-15/)).toHaveLength(2)
    expect(
      await screen.findByRole('row', { name: /Portfolio Total/ }),
    ).toBeInTheDocument()

    expect(apiMocks.getPortfolioPerformance).toHaveBeenCalledWith('3', windowFilters)
    expect(apiMocks.getPortfolioPerformanceCalculation).toHaveBeenCalledWith('3', windowFilters)
    expect(apiMocks.getPortfolioPerformanceCalculationGroups).toHaveBeenCalledWith('3', {
      ...windowFilters,
      axis: 'instrument',
      taxonomy_id: undefined,
    })

    expect(screen.queryByText(/publication lineage|rounding audit|internal audit|manifest/i)).not.toBeInTheDocument()
  })

  it('shows benchmark differences and relative statistics for a confirmed price index', async () => {
    const user = userEvent.setup()
    apiMocks.getPortfolioInstruments.mockResolvedValue({
      portfolio_id: '3',
      instruments: [
        {
          instrument_id: 'benchmark-1',
          instrument_name: 'Market Benchmark',
          instrument_type: 'index',
          currency: 'USD',
          identifiers: [{ identifier_type: 'ticker', identifier_value: 'MKT', is_primary: true }],
          latest_market_data: [],
          coverage_state: 'complete',
        },
      ],
    })
    apiMocks.getPortfolioInstrumentPriceChart.mockResolvedValue({
      portfolio_id: '3',
      instrument_core: {
        instrument_id: 'benchmark-1',
        instrument_name: 'Market Benchmark',
        instrument_type: 'index',
        currency: 'USD',
        identifiers: [{ identifier_type: 'ticker', identifier_value: 'MKT', is_primary: true }],
      },
      as_of_date: '2026-07-15',
      range_key: 'all',
      chart_basis: 'close',
      return_semantics: 'price_return',
      metric_family: 'price',
      currency: 'USD',
      points: Array.from({ length: 11 }, (_, index) => ({
        date: `2026-07-${String(index + 5).padStart(2, '0')}`,
        value: 100 + index,
      })),
      summary: {
        point_count: 11,
        change_value: 10,
        change_pct: 0.1,
        high: 110,
        low: 100,
      },
    })

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    const benchmarkSearch = await screen.findByRole('searchbox', { name: 'Compare benchmark' })
    await user.type(benchmarkSearch, 'Market')
    await user.click(await screen.findByRole('button', { name: /Market Benchmark/ }))

    expect(await screen.findByText('Manual comparator · price return')).toBeInTheDocument()
    expect(await screen.findByText(/Benchmark uses close with confirmed price-return semantics/)).toHaveTextContent(
      /benchmark and relative metrics are shown/,
    )
    const periodReturnCells = within(screen.getByRole('row', { name: /Total Portfolio Return/ })).getAllByRole('cell')
    expect(periodReturnCells).toHaveLength(3)
    expect(periodReturnCells[1]).not.toHaveTextContent('—')
    expect(periodReturnCells[2]).not.toHaveTextContent('—')
    const trackingErrorCells = within(screen.getByRole('row', { name: /Tracking Error/ })).getAllByRole('cell')
    expect(trackingErrorCells[0]).not.toHaveTextContent('—')
  })

  it('renders the period controls and derives calendar presets from the selected end boundary', async () => {
    const user = userEvent.setup()
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(
      workspaceSummaryFixture({ as_of_date: '2026-05-15' }),
    )

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance',
      '/portfolios/:portfolioId/performance',
    )

    const controls = await screen.findByLabelText('Performance period controls')
    for (const label of ['Latest', 'Reset', 'MTD', 'QTD', 'YTD', '1Y', 'SI']) {
      expect(within(controls).getByRole('button', { name: label })).toBeInTheDocument()
    }

    await user.click(within(controls).getByRole('button', { name: 'MTD' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformance).toHaveBeenLastCalledWith('3', {
        start_date: '2026-04-30',
        end_date: '2026-05-15',
      }),
    )

    await user.click(within(controls).getByRole('button', { name: 'QTD' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformance).toHaveBeenLastCalledWith('3', {
        start_date: '2026-03-31',
        end_date: '2026-05-15',
      }),
    )

    await user.click(within(controls).getByRole('button', { name: 'YTD' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformanceCalculation).toHaveBeenLastCalledWith('3', {
        start_date: '2025-12-31',
        end_date: '2026-05-15',
      }),
    )

    await user.click(within(controls).getByRole('button', { name: '1Y' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformanceCalculationGroups).toHaveBeenLastCalledWith('3', {
        start_date: '2025-05-15',
        end_date: '2026-05-15',
        axis: 'instrument',
        taxonomy_id: undefined,
      }),
    )
  })

  it('lets the backend resolve SI inception and keeps one unguessed window across all reports', async () => {
    const user = userEvent.setup()
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(
      workspaceSummaryFixture({ as_of_date: '2026-05-15' }),
    )

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance',
      '/portfolios/:portfolioId/performance',
    )

    const controls = await screen.findByLabelText('Performance period controls')
    const sinceInceptionButton = within(controls).getByRole('button', { name: 'SI' })
    await user.click(sinceInceptionButton)

    const sinceInceptionFilters = { end_date: '2026-05-15' }
    await waitFor(() => {
      expect(apiMocks.getPortfolioPerformance).toHaveBeenLastCalledWith('3', sinceInceptionFilters)
      expect(apiMocks.getPortfolioPerformanceCalculation).toHaveBeenLastCalledWith('3', sinceInceptionFilters)
      expect(apiMocks.getPortfolioPerformanceCalculationGroups).toHaveBeenLastCalledWith('3', {
        ...sinceInceptionFilters,
        axis: 'instrument',
        taxonomy_id: undefined,
      })
    })
    expect(sinceInceptionButton).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Start Date')).toHaveValue('')
  })

  it('uses a clamped prior-year calendar date for the 1Y shortcut', async () => {
    const user = userEvent.setup()
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(
      workspaceSummaryFixture({ as_of_date: '2024-02-29' }),
    )

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance',
      '/portfolios/:portfolioId/performance',
    )

    const controls = await screen.findByLabelText('Performance period controls')
    await user.click(within(controls).getByRole('button', { name: '1Y' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformance).toHaveBeenLastCalledWith('3', {
        start_date: '2023-02-28',
        end_date: '2024-02-29',
      }),
    )
  })

  it('moves a historical window to Latest and Reset restores the existing default window', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-01-01&end_date=2026-05-01',
      '/portfolios/:portfolioId/performance',
    )

    const controls = await screen.findByLabelText('Performance period controls')
    await user.click(within(controls).getByRole('button', { name: 'Latest' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformance).toHaveBeenLastCalledWith('3', {
        start_date: '2026-01-01',
        end_date: '2026-07-15',
      }),
    )
    expect(screen.getByLabelText('End Date')).toHaveValue('2026-07-15')

    await user.click(within(controls).getByRole('button', { name: 'Reset' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformance).toHaveBeenLastCalledWith('3', {
        start_date: '2026-06-15',
        end_date: '2026-07-15',
      }),
    )
    expect(screen.getByLabelText('Start Date')).toHaveValue('2026-06-15')
    expect(window.localStorage.getItem('portfolio_ops.portfolio.performance.window.v1')).toBe('{}')
  })

  it('renders fail-closed XIRR and insufficient-risk-sample reasons', async () => {
    const fixture = performanceFixture()
    apiMocks.getPortfolioPerformance.mockResolvedValue({
      ...fixture,
      summary: {
        ...fixture.summary,
        annualization_eligible: true,
        annualization_unavailable_reason: null,
        irr: null,
        mwror: null,
        irr_solver_status: 'multiple_roots_or_non_unique',
        irr_unavailable_reason: 'multiple_roots_or_non_unique',
        risk_minimum_sample_count: 2,
        risk_sample_count: 1,
        risk_result_status: 'insufficient_samples',
        risk_unavailable_reason: 'insufficient_return_samples',
        annualized_volatility: null,
        annualized_downside_volatility: null,
        sharpe_ratio: null,
        sortino_ratio: null,
      },
    })

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    const irrRow = await screen.findByRole('row', { name: /IRR \/ MWRR/ })
    expect(within(irrRow).getByText('N/A')).toBeInTheDocument()
    expect(within(irrRow).getByText('Multiple or non-unique XIRR roots')).toBeInTheDocument()

    const volatilityRow = screen.getByRole('row', { name: /^Market Risk Volatility/ })
    expect(within(volatilityRow).getByText('Requires ≥ 2 daily return samples (1 available)')).toBeInTheDocument()
  })

  it('shows pending-settlement monetary FX as its own attribution column', async () => {
    apiMocks.getPortfolioTableViewStore.mockResolvedValue({
      store: { activeViewId: 'full-calculation', views: [] },
    })

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    expect(
      await screen.findByRole('columnheader', { name: /Pending Settlement Monetary FX/ }),
    ).toBeInTheDocument()
    const portfolioTotal = await screen.findByRole('row', { name: /Portfolio Total/ })
    expect(within(portfolioTotal).getByText('-$0.51')).toBeInTheDocument()
  })

  it('explains fail-closed pending-settlement FX coverage', async () => {
    apiMocks.getPortfolioPerformanceCalculation.mockResolvedValue({
      portfolio_id: '3',
      base_currency: 'USD',
      valuation_timezone: 'Asia/Shanghai',
      valuation_cutoff_policy: 'close',
      summary: {
        start_date: '2026-07-06',
        end_date: '2026-07-15',
        coverage_state: 'partial',
        stale_price_flag: false,
        stale_fx_flag: false,
        initial_value: 970,
        final_value: 1000,
        delta: 30,
        capital_gains: null,
        realized_capital_gains: null,
        unrealized_capital_gains: null,
        earnings: 0,
        fees: 0,
        taxes: 0,
        cash_currency_gains: 0,
        pending_settlement_currency_gains: null,
        instrument_currency_gains: 0,
        deposits: 0,
        withdrawals: 0,
        net_external_inflow: 0,
      },
      lines: [],
    })

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    expect(await screen.findByText(/Pending settlement monetary FX is unavailable/)).toHaveTextContent(
      /reliable FX boundary or complete attribution coverage is missing \(partial\)/,
    )
  })

  it('surfaces a reliable-end clamp and uses the effective window in the rendered period', async () => {
    const fixture = performanceFixture()
    apiMocks.getPortfolioPerformance.mockResolvedValue({
      ...fixture,
      summary: {
        ...fixture.summary,
        end_date: '2026-07-14',
        requested_start_date: '2026-07-06',
        requested_end_date: '2026-07-15',
        effective_start_date: '2026-07-06',
        effective_end_date: '2026-07-14',
        as_of_clamp_reason: 'requested_end_after_latest_reliable_endpoint',
      },
      daily_series: fixture.daily_series.filter((point) => point.as_of_date <= '2026-07-14'),
    })

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    expect(await screen.findByText(/Performance requested through/i)).toHaveTextContent(
      /requested through 2026-07-15; reliable results end on 2026-07-14/i,
    )
    expect(screen.getAllByText(/2026-07-06 to 2026-07-14/)).toHaveLength(2)
  })
})
