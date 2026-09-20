import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
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
const exportMocks = vi.hoisted(() => ({ downloadTable: vi.fn() }))

vi.mock('./lib/api', () => apiMocks)
vi.mock('../../../../packages/ui/src/tableExport', () => exportMocks)
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
        total_linked_period_contribution: 0.029,
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
      'investment_studio.portfolio.performance.calculation.views.v1',
      legacyStore,
    )

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    expect(await screen.findByRole('button', { name: /View\s*: Default/ })).toBeInTheDocument()
    const windowToolbar = screen.getByLabelText('Performance period controls').closest('.performance-window-bar')
    expect(windowToolbar).not.toBeNull()
    expect(windowToolbar).not.toHaveClass('transaction-filter-bar')
    expect(within(windowToolbar as HTMLElement).getByLabelText('Start Date')).toBeInTheDocument()
    const calculationToolbar = screen.getByText('Calculation').closest('.performance-calculation-toolbar')
    expect(calculationToolbar).not.toBeNull()
    expect(within(calculationToolbar as HTMLElement).getByRole('button', { name: /Columns/ })).toHaveClass(
      'portfolio-table-toolbar-button',
    )
    expect(calculationToolbar?.querySelector('.holdings-filter-actions')).toBeNull()
    expect(screen.getByRole('columnheader', { name: /Begin Weight/ })).toBeInTheDocument()
    expect(
      window.localStorage.getItem('investment_studio.portfolio.performance.calculation.views.v1'),
    ).toBe(legacyStore)
    expect(apiMocks.savePortfolioTableViewStore).not.toHaveBeenCalled()
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

  it('updates the active Default calculation view from the Columns dialog', async () => {
    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )
    const user = userEvent.setup()

    expect(await screen.findByRole('button', { name: /View\s*: Default$/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Columns' }))
    const dialog = screen.getByRole('dialog', { name: 'Choose calculation columns' })
    const beginWeightField = within(dialog).getByText('Begin Weight').closest('label')!
    await user.click(within(beginWeightField).getByRole('checkbox'))
    await user.click(within(dialog).getByRole('button', { name: 'Update' }))

    expect(screen.getByRole('button', { name: /View\s*: Default$/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Update View' })).not.toBeInTheDocument()
    await waitFor(() => {
      const savedStore = apiMocks.savePortfolioTableViewStore.mock.calls.find(
        ([portfolioId, viewScope]) => portfolioId === '3' && viewScope === 'performance_calculation',
      )?.[2] as { views: Array<{ id: string; state: { columns: string[] } }> } | undefined
      expect(savedStore?.views.find((view) => view.id === 'risk-attribution')?.state.columns).not.toContain('begin_weight')
    })

    apiMocks.savePortfolioTableViewStore.mockClear()
    await user.click(screen.getByRole('button', { name: /Group By\s*: None/ }))
    const groupDialog = screen.getByRole('dialog', { name: 'Group performance calculations' })
    await user.click(within(groupDialog).getByRole('button', { name: 'Currency' }))
    expect(screen.queryByRole('button', { name: 'Update View' })).not.toBeInTheDocument()
    await waitFor(() => {
      const savedStore = apiMocks.savePortfolioTableViewStore.mock.calls.find(
        ([portfolioId, viewScope]) => portfolioId === '3' && viewScope === 'performance_calculation',
      )?.[2] as { views: Array<{ id: string; state: { groupBy: string } }> } | undefined
      expect(savedStore?.views.find((view) => view.id === 'risk-attribution')?.state.groupBy).toBe('currency')
    })
  })

  // Characterization gap: the restored PerformancePage currently has no chart DOM.
  // Keep the shared-window assertion on summary and Calculation until that surface exists.
  it('uses one selected date window for summary and Calculation and keeps ordinary returns at two decimals', async () => {
    const fixture = performanceFixture()
    apiMocks.getPortfolioPerformance.mockResolvedValue({
      ...fixture,
      summary: {
        ...fixture.summary,
        cash_currency_gains: 2,
        instrument_currency_gains: 3,
        pending_settlement_currency_gains: -1,
      },
    })
    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    const periodReturnRow = await screen.findByRole('row', { name: /Total Portfolio Return/ })
    expect(within(periodReturnRow).getByText('+3.02%')).toBeInTheDocument()
    const totalFxPnlRow = screen.getByRole('row', { name: /Total FX Attribution/ })
    expect(within(totalFxPnlRow).getByText('+$4.00')).toBeInTheDocument()
    expect(screen.getByText('Calculation')).toBeInTheDocument()
    const performanceDetails = screen.getByRole('button', { name: /Performance details:/ })
    const calculationDetails = screen.getByRole('button', { name: /Calculation details:/ })
    expect(performanceDetails).toHaveAttribute('aria-label', expect.stringContaining('2026-07-06 to 2026-07-15'))
    expect(calculationDetails).toHaveAttribute('aria-label', expect.stringContaining('2026-07-06 to 2026-07-15'))
    expect(
      await screen.findByRole('row', { name: /Portfolio Total/ }),
    ).toBeInTheDocument()
    const annualizedReturnRow = screen.getByRole('row', { name: /Annualized TWR/ })
    expect(
      within(annualizedReturnRow).getByRole('button', {
        name: 'Annualized TWR availability: Requires ≥ 1 year',
      }),
    ).toBeInTheDocument()
    expect(within(annualizedReturnRow).queryByText('Requires ≥ 1 year')).not.toBeInTheDocument()
    expect(screen.queryByText('Short observed history.')).not.toBeInTheDocument()
    expect(screen.queryByText(/Ordinary Assets Sleeve TWR unavailable/)).not.toBeInTheDocument()
    expect(calculationDetails).toHaveAttribute('aria-label', expect.stringContaining('Daily risk basis'))

    expect(apiMocks.getPortfolioPerformance).toHaveBeenCalledWith('3', windowFilters, expect.any(AbortSignal))
    expect(apiMocks.getPortfolioPerformanceCalculation).toHaveBeenCalledWith('3', windowFilters, expect.any(AbortSignal))
    expect(apiMocks.getPortfolioPerformanceCalculationGroups).toHaveBeenCalledWith('3', {
      ...windowFilters,
      axis: 'instrument',
      taxonomy_id: undefined,
    }, expect.any(AbortSignal))

    expect(screen.queryByText(/publication lineage|rounding audit|internal audit|manifest/i)).not.toBeInTheDocument()
  })

  it('moves carrying-basis methodology into a compact title hint', async () => {
    const fixture = performanceFixture()
    apiMocks.getPortfolioPerformance.mockResolvedValue({
      ...fixture,
      summary: {
        ...fixture.summary,
        performance_basis: 'operational_carrying_basis',
        performance_label: 'Total Portfolio Operational Return',
      },
    })

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    const hint = await screen.findByRole('button', { name: /Performance details:/ })
    expect(hint).toHaveAttribute('aria-label', expect.stringContaining('operational ledger return'))
    expect(screen.queryByText('Operational carrying-basis return.')).not.toBeInTheDocument()
    expect(screen.queryByText('Not a complete fair-value TWR.')).not.toBeInTheDocument()
    expect(hint).toHaveAttribute('aria-label', expect.stringContaining('not a complete fair-value or GIPS-informed TWR'))
    expect(hint).toHaveAttribute('aria-label', expect.stringContaining('Recorded fees and taxes deducted'))
    expect(hint).toHaveAttribute('aria-label', expect.stringContaining('Risk: daily observations'))
    expect(screen.queryByText('Recorded fees and taxes deducted')).not.toBeInTheDocument()
    await userEvent.setup().click(hint)
    expect(screen.getByRole('tooltip')).toHaveTextContent('Recorded fees and taxes deducted')
  })

  it('defaults to linked contributions, keeps arithmetic optional, and exports both with context', async () => {
    const user = userEvent.setup()
    const fixture = performanceFixture()
    const missingWarning = 'Required market data missing: 2026-07-16; stock-a valuation price, FX USD/CNY. Supply the required observation before performance can continue.'
    apiMocks.getPortfolioPerformance.mockResolvedValue({ ...fixture, summary: {
      ...fixture.summary, cumulative_twr: .029, quality_warnings: [missingWarning],
      as_of_clamp_reason: 'required_market_data_missing', effective_end_date: '2026-07-15',
      requested_end_date: '2026-07-16',
    } })
    const calculation = await apiMocks.getPortfolioPerformanceCalculationGroups()
    apiMocks.getPortfolioPerformanceCalculationGroups.mockResolvedValue({
      ...calculation,
      groups: [{
        group_key: 'growth', group_label: 'Growth',
        linked_period_contribution: .02, period_contribution: .021,
        children: [{ parent_group_key: 'growth', item_key: 'asset', item_kind: 'instrument',
          item_label: 'Asset', linked_period_contribution: .02, period_contribution: .021 }],
      }, {
        group_key: 'cash', group_label: 'Cash', children: [],
        linked_period_contribution: .009, period_contribution: .0092,
      }],
    })
    renderPortfolioPage(<PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-16',
      '/portfolios/:portfolioId/performance')
    const warningHint = await screen.findByRole('button', { name: /Data quality warning:/ })
    expect(warningHint.closest('.performance-window-bar')).not.toBeNull()
    await user.click(warningHint)
    expect(within(screen.getByRole('tooltip')).getByText(missingWarning)).toBeVisible()
    await user.keyboard('{Escape}')
    expect(await screen.findByRole('columnheader', { name: /Linked Return Contribution/ })).toBeVisible()
    expect(screen.queryByRole('columnheader', { name: /Arithmetic Return Contribution/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('row', { name: /TWR Linking Difference/ })).not.toBeInTheDocument()
    expect(within(screen.getByRole('row', { name: /^Growth/ })).getByText('+2.00%')).toBeVisible()
    expect(within(screen.getByRole('row', { name: /^Asset/ })).getByText('+2.00%')).toBeVisible()
    expect(screen.getByRole('button', { name: /Calculation details:/ })).toHaveAttribute(
      'aria-label', expect.stringContaining('Linked contributions sum to period TWR; child contributions sum to their parent.'),
    )
    await user.click(screen.getByRole('button', { name: 'Columns' }))
    const dialog = screen.getByRole('dialog', { name: 'Choose calculation columns' })
    await user.click(within(dialog).getByRole('checkbox', { name: 'Arithmetic Return Contribution' }))
    await user.click(within(dialog).getByRole('button', { name: 'Update' }))
    const bridge = await screen.findByRole('row', { name: /TWR Linking Difference/ })
    expect(within(bridge).getByText('-0.12%')).toBeVisible()
    expect(screen.getByRole('columnheader', { name: /Arithmetic Return Contribution/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /Calculation details:/ })).toHaveAttribute(
      'aria-label', expect.stringContaining('Arithmetic contributions + TWR linking difference = period TWR.'),
    )
    expect(screen.queryByText('Relative')).not.toBeInTheDocument()
    const maxDrawdown = screen.getByRole('row', { name: /^Market Risk Max DD/ })
    expect(maxDrawdown.closest('.performance-metric-table-shell')).toHaveTextContent('Risk')
    await user.click(screen.getByRole('button', { name: 'Download' }))
    await user.click(screen.getByRole('menuitem', { name: 'CSV' }))
    const exportedRows = exportMocks.downloadTable.mock.calls[0][1]
    expect(exportedRows).toContainEqual(['Currency', 'USD'])
    expect(exportedRows).toContainEqual(['Period', '2026-07-06 to 2026-07-15'])
    expect(exportedRows).toContainEqual(['Return units', 'Decimal fractions: 0.01 = 1%'])
    expect(exportedRows).toContainEqual(['Fees and taxes', 'Recorded fees and taxes deducted'])
    const header = exportedRows.find((row: unknown[]) => row[0] === 'Line')
    const linkedIndex = header.indexOf('Linked Return Contribution')
    const arithmeticIndex = header.indexOf('Arithmetic Return Contribution')
    const total = exportedRows.find((row: unknown[]) => row[0] === 'Portfolio Total')
    expect(Number(total[linkedIndex])).toBeCloseTo(.029)
    expect(Number(total[arithmeticIndex])).toBeCloseTo(.0302)
    const exportedBridge = exportedRows.find((row: unknown[]) => row[0] === 'TWR Linking Difference')
    expect(exportedBridge[linkedIndex]).toBeNull()
    expect(Number(exportedBridge[arithmeticIndex])).toBeCloseTo(-.0012)
    for (const label of ['Growth', 'Asset']) {
      const row = exportedRows.find((item: unknown[]) => item[0] === label)
      expect(Number(row[linkedIndex])).toBeCloseTo(.02)
      expect(Number(row[arithmeticIndex])).toBeCloseTo(.021)
    }
  })

  it('shows available row risk metrics independently of aggregate metric availability', async () => {
    const response = await apiMocks.getPortfolioPerformanceCalculationGroups()
    apiMocks.getPortfolioPerformanceCalculationGroups.mockResolvedValue({
      ...response,
      groups: [{
        group_key: 'valid-group', group_label: 'Valid group', children: [],
        risk_return_observation_count: 3, annualized_volatility: .1,
        sharpe_ratio: 1.5, correlation_to_portfolio: .75,
        beta_to_portfolio: 1.2, realized_risk_contribution: .25,
      }],
    })
    renderPortfolioPage(<PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance')
    const row = await screen.findByRole('row', { name: /Valid group/ })
    expect(within(row).getByText('+0.75')).toBeVisible()
    expect(within(row).getByText('+25.00%')).toBeVisible()
  })

  it('waits for the default planning taxonomy before loading calculation groups', async () => {
    let resolveTaxonomyCatalog: ((value: object) => void) | undefined
    apiMocks.getPortfolioTableViewStore.mockResolvedValueOnce({
      store: {
        activeViewId: 'risk-attribution',
        views: [
          {
            id: 'risk-attribution',
            name: 'Default',
            state: { groupBy: 'taxonomy' },
          },
        ],
      },
    })
    apiMocks.getPortfolioTaxonomyCatalog.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveTaxonomyCatalog = resolve
      }),
    )

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    await screen.findByRole('row', { name: /Total Portfolio Return/ })
    expect(apiMocks.getPortfolioPerformanceCalculationGroups).not.toHaveBeenCalled()

    await act(async () => {
      resolveTaxonomyCatalog?.({
        portfolio_id: '3',
        default_planning_taxonomy_id: 'tax-planning',
        taxonomies: [
          {
            taxonomy_id: 'tax-planning',
            planning_enabled: true,
            status: 'active',
          },
        ],
        taxonomy_nodes: [],
        taxonomy_assignments: [],
        instrument_universe: [],
        target_sets: [],
        target_set_lines: [],
        target_set_integrity_issues: [],
      })
    })

    await waitFor(() => {
      expect(apiMocks.getPortfolioPerformanceCalculationGroups).toHaveBeenCalledTimes(1)
    })
    expect(apiMocks.getPortfolioPerformanceCalculationGroups).toHaveBeenCalledWith('3', {
      ...windowFilters,
      axis: 'taxonomy',
      taxonomy_id: 'tax-planning',
    }, expect.any(AbortSignal))
  })

  it.each(['price_return', 'unknown'] as const)('shows benchmark differences and relative statistics for a %s price series', async (returnSemantics) => {
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
      return_semantics: returnSemantics,
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

    const benchmarkHint = await screen.findByRole('button', {
      name: /Benchmark comparison:/,
    })
    expect(benchmarkHint).toHaveAttribute(
      'aria-label',
      expect.stringContaining('Comparison uses the selected price'),
    )
    const periodReturnCells = within(screen.getByRole('row', { name: /Total Portfolio Return/ })).getAllByRole('cell')
    expect(periodReturnCells).toHaveLength(3)
    expect(periodReturnCells[1]).not.toHaveTextContent('—')
    expect(periodReturnCells[2]).not.toHaveTextContent('—')
    const trackingErrorCells = within(screen.getByRole('row', { name: /Tracking Error/ })).getAllByRole('cell')
    expect(trackingErrorCells).toHaveLength(1)
    expect(trackingErrorCells[0]).not.toHaveTextContent('—')
  })

  it.each([
    { missingDate: '2026-07-15', allCash: false },
    { missingDate: '2026-07-08', allCash: true },
    { missingDate: null, allCash: true },
  ])('checks all benchmark sessions through the cash tail ($missingDate)', async ({ missingDate, allCash }) => {
    const user = userEvent.setup()
    const performance = performanceFixture()
    performance.daily_series = performance.daily_series.map((point) => ({ ...point,
      market_risk_daily_return: !allCash && point.as_of_date === '2026-07-07' ? 0 : null,
      market_risk_return_observation_eligible: !allCash && point.as_of_date === '2026-07-07',
    }))
    apiMocks.getPortfolioPerformance.mockResolvedValue(performance)
    const instrument = { instrument_id: 'benchmark-1', instrument_name: 'Market Benchmark',
      instrument_type: 'index', currency: 'USD', identifiers: [], latest_market_data: [] }
    apiMocks.getPortfolioInstruments.mockResolvedValue({ portfolio_id: '3', instruments: [instrument] })
    const sessionDates = ['2026-07-06', '2026-07-07', '2026-07-08', '2026-07-09',
      '2026-07-10', '2026-07-13', '2026-07-14', '2026-07-15']
    apiMocks.getPortfolioInstrumentPriceChart.mockResolvedValue({
      portfolio_id: '3', instrument_core: instrument, as_of_date: '2026-07-15', range_key: 'all',
      chart_basis: 'close', return_semantics: 'price_return', currency: 'USD', metric_family: 'price',
      market_session_dates: sessionDates,
      points: sessionDates.map((date, index) => ({ date, value: 100 + index * 3 }))
        .filter((point) => point.date !== missingDate),
      summary: { point_count: 8, change_value: 21, change_pct: .21, high: 121, low: 100 },
    })
    renderPortfolioPage(<PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance')
    await user.type(await screen.findByRole('searchbox', { name: 'Compare benchmark' }), 'Market')
    await user.click(await screen.findByRole('button', { name: /Market Benchmark/ }))
    const hint = await screen.findByRole('button', { name: /Benchmark comparison:/ })
    const returnRow = screen.getByRole('row', { name: /Total Portfolio Return/ })
    if (missingDate) {
      expect(hint).toHaveAttribute('aria-label', expect.stringContaining(`required observations are missing: ${missingDate}`))
      expect(within(returnRow).getAllByRole('cell')).toHaveLength(1)
      expect(screen.getByRole('row', { name: /Tracking Error/ })).toHaveTextContent('—')
    } else {
      expect(within(returnRow).getAllByRole('cell')[1]).toHaveTextContent('+21.00%')
    }
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
      }, expect.any(AbortSignal)),
    )

    await user.click(within(controls).getByRole('button', { name: 'QTD' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformance).toHaveBeenLastCalledWith('3', {
        start_date: '2026-03-31',
        end_date: '2026-05-15',
      }, expect.any(AbortSignal)),
    )

    await user.click(within(controls).getByRole('button', { name: 'YTD' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformanceCalculation).toHaveBeenLastCalledWith('3', {
        start_date: '2025-12-31',
        end_date: '2026-05-15',
      }, expect.any(AbortSignal)),
    )

    await user.click(within(controls).getByRole('button', { name: '1Y' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformanceCalculationGroups).toHaveBeenLastCalledWith('3', {
        start_date: '2025-05-15',
        end_date: '2026-05-15',
        axis: 'instrument',
        taxonomy_id: undefined,
      }, expect.any(AbortSignal)),
    )
  })

  it('waits for the selected performance window before starting its lower calculations', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    await screen.findByRole('row', { name: /Portfolio Total/ })
    apiMocks.getPortfolioPerformanceCalculation.mockClear()
    apiMocks.getPortfolioPerformanceCalculationGroups.mockClear()

    let resolvePerformanceRefresh: ((value: ReturnType<typeof performanceFixture>) => void) | undefined
    apiMocks.getPortfolioPerformance.mockReturnValueOnce(
      new Promise((resolve) => {
        resolvePerformanceRefresh = resolve
      }),
    )

    const controls = screen.getByLabelText('Performance period controls')
    await user.click(within(controls).getByRole('button', { name: 'MTD' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformance).toHaveBeenLastCalledWith('3', {
        start_date: '2026-06-30',
        end_date: '2026-07-15',
      }, expect.any(AbortSignal)),
    )
    expect(apiMocks.getPortfolioPerformanceCalculation).not.toHaveBeenCalled()
    expect(apiMocks.getPortfolioPerformanceCalculationGroups).not.toHaveBeenCalled()

    const refreshedPerformance = performanceFixture()
    await act(async () => {
      resolvePerformanceRefresh?.({
        ...refreshedPerformance,
        summary: {
          ...refreshedPerformance.summary,
          start_date: '2026-06-30',
          end_date: '2026-07-15',
          requested_start_date: '2026-06-30',
          requested_end_date: '2026-07-15',
          effective_start_date: '2026-06-30',
          effective_end_date: '2026-07-15',
        },
      })
    })

    await waitFor(() => {
      expect(apiMocks.getPortfolioPerformanceCalculation).toHaveBeenCalledTimes(1)
      expect(apiMocks.getPortfolioPerformanceCalculationGroups).toHaveBeenCalledTimes(1)
    })
    expect(apiMocks.getPortfolioPerformanceCalculation).toHaveBeenCalledWith('3', {
      start_date: '2026-06-30',
      end_date: '2026-07-15',
    }, expect.any(AbortSignal))
    expect(apiMocks.getPortfolioPerformanceCalculationGroups).toHaveBeenCalledWith('3', {
      start_date: '2026-06-30',
      end_date: '2026-07-15',
      axis: 'instrument',
      taxonomy_id: undefined,
    }, expect.any(AbortSignal))
  })

  it('submits edited dates together without requesting intermediate windows', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )
    await screen.findByRole('row', { name: /Portfolio Total/ })
    apiMocks.getPortfolioPerformance.mockClear()
    apiMocks.getPortfolioPerformanceCalculation.mockClear()
    apiMocks.getPortfolioPerformanceCalculationGroups.mockClear()

    fireEvent.change(screen.getByLabelText('Start Date'), { target: { value: '2026-07-01' } })
    fireEvent.change(screen.getByLabelText('End Date'), { target: { value: '2026-07-14' } })
    expect(apiMocks.getPortfolioPerformance).not.toHaveBeenCalled()
    expect(apiMocks.getPortfolioPerformanceCalculation).not.toHaveBeenCalled()
    expect(apiMocks.getPortfolioPerformanceCalculationGroups).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Apply period' }))
    await waitFor(() => expect(apiMocks.getPortfolioPerformance).toHaveBeenCalledTimes(1))
    expect(apiMocks.getPortfolioPerformance).toHaveBeenCalledWith('3', {
      start_date: '2026-07-01', end_date: '2026-07-14',
    }, expect.any(AbortSignal))
  })

  it('keeps an invalid draft from replacing the applied period', async () => {
    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )
    await screen.findByRole('row', { name: /Portfolio Total/ })
    apiMocks.getPortfolioPerformance.mockClear()
    fireEvent.change(screen.getByLabelText('Start Date'), { target: { value: '2026-07-20' } })

    expect(screen.getByRole('button', { name: 'Apply period' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('Start Date must be on or before End Date.')
    expect(apiMocks.getPortfolioPerformance).not.toHaveBeenCalled()
    expect(screen.getByRole('row', { name: /Portfolio Total/ })).toBeInTheDocument()
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
      expect(apiMocks.getPortfolioPerformance).toHaveBeenLastCalledWith('3', sinceInceptionFilters, expect.any(AbortSignal))
      expect(apiMocks.getPortfolioPerformanceCalculation).toHaveBeenLastCalledWith('3', sinceInceptionFilters, expect.any(AbortSignal))
      expect(apiMocks.getPortfolioPerformanceCalculationGroups).toHaveBeenLastCalledWith('3', {
        ...sinceInceptionFilters,
        axis: 'instrument',
        taxonomy_id: undefined,
      }, expect.any(AbortSignal))
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
      }, expect.any(AbortSignal)),
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
      }, expect.any(AbortSignal)),
    )
    expect(screen.getByLabelText('End Date')).toHaveValue('2026-07-15')

    await user.click(within(controls).getByRole('button', { name: 'Reset' }))
    await waitFor(() =>
      expect(apiMocks.getPortfolioPerformance).toHaveBeenLastCalledWith('3', {
        start_date: '2026-06-15',
        end_date: '2026-07-15',
      }, expect.any(AbortSignal)),
    )
    expect(screen.getByLabelText('Start Date')).toHaveValue('2026-06-15')
    expect(window.localStorage.getItem('investment_studio.portfolio.performance.window.v1')).toBe('{}')
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
        annualized_volatility: .25,
        annualized_downside_volatility: null,
        sharpe_ratio: 1.3,
        sortino_ratio: null,
        pending_settlement_currency_gains: null,
      },
    })

    renderPortfolioPage(
      <PerformancePage />,
      '/portfolios/3/performance?start_date=2026-07-06&end_date=2026-07-15',
      '/portfolios/:portfolioId/performance',
    )

    const irrRow = await screen.findByRole('row', { name: /IRR \/ MWRR/ })
    expect(within(irrRow).getByText('N/A')).toBeInTheDocument()
    const totalFxPnlRow = screen.getByRole('row', { name: /Total FX Attribution/ })
    expect(within(totalFxPnlRow).getByText('—')).toBeInTheDocument()
    expect(
      within(irrRow).getByRole('button', {
        name: 'IRR / MWRR availability: Multiple or non-unique XIRR roots',
      }),
    ).toBeInTheDocument()

    const volatilityRow = screen.getByRole('row', { name: /^Market Risk Volatility/ })
    expect(within(volatilityRow).getByText('—')).toBeVisible()
    const portfolioTotal = await screen.findByRole('row', { name: /Portfolio Total/ })
    expect(portfolioTotal).not.toHaveTextContent('25.00%')
    expect(portfolioTotal).not.toHaveTextContent('1.30')
    expect(
      within(volatilityRow).getByRole('button', {
        name: 'Market Risk Volatility availability: Requires ≥ 2 daily return samples (1 available)',
      }),
    ).toBeInTheDocument()
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
      await screen.findByRole('columnheader', { name: /Pending Settlement FX/ }),
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

    const cutoffHint = await screen.findByRole('button', { name: /Performance data cutoff:/ })
    expect(cutoffHint.closest('.performance-window-bar')).not.toBeNull()
    expect(cutoffHint).toHaveAttribute(
      'aria-label', expect.stringContaining('requested through 2026-07-15; reliable results end on 2026-07-14'),
    )
    expect(screen.getByRole('button', { name: /Performance details:/ })).toHaveAttribute(
      'aria-label',
      expect.stringContaining('2026-07-06 to 2026-07-14'),
    )
    expect(screen.getByRole('button', { name: /Calculation details:/ })).toHaveAttribute(
      'aria-label',
      expect.stringContaining('2026-07-06 to 2026-07-14'),
    )
  })
})
