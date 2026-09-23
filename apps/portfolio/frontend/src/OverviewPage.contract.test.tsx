import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import OverviewPage, {
  buildPortfolioReturnMetrics,
  trailingAnnualizedVolatility,
} from './pages/OverviewPage'
import {
  dailyPerformancePoint,
  fcnContractFixture,
  holdingFixture,
  holdingsWorkspaceFixture,
  instrumentFixture,
  performanceFixture,
  workspaceSummaryFixture,
} from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  getWorkspaceSummaryForPortfolio: vi.fn(),
  getHoldingsWorkspace: vi.fn(),
  getPortfolioTaxonomyCatalog: vi.fn(),
  getPortfolioPerformance: vi.fn(),
  getPortfolioInstruments: vi.fn(),
  getPortfolioInstrumentPriceChart: vi.fn(),
  getConcentration: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./lib/concentrationApi', () => ({ getConcentration: apiMocks.getConcentration }))
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))

describe('Overview rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.removeItem('investment_studio.portfolio.overview.taxonomy.3')
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())
    apiMocks.getConcentration.mockResolvedValue({ scopes: [] })
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            last_price: null,
            coverage_status: 'Action required: official NAV is missing for 2026-07-15',
          }),
        ],
      }),
    )
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({
      portfolio_id: '3',
      taxonomy_configuration_version: 0,
      target_resolution: [],
      taxonomies: [],
      taxonomy_nodes: [],
      taxonomy_assignments: [],
      instrument_universe: [],
      target_sets: [],
      target_set_lines: [],
      target_set_integrity_issues: [],
    })
    apiMocks.getPortfolioPerformance.mockResolvedValue(performanceFixture())
    apiMocks.getPortfolioInstruments.mockResolvedValue({ instruments: [] })
  })

  it('loads holdings and performance without waiting for classification and reports its failure locally', async () => {
    let rejectTaxonomy!: (error: Error) => void
    apiMocks.getPortfolioTaxonomyCatalog.mockReturnValue(new Promise((_resolve, reject) => { rejectTaxonomy = reject }))
    renderPortfolioPage(<OverviewPage />, '/portfolios/3/overview', '/portfolios/:portfolioId/overview')
    await waitFor(() => expect(apiMocks.getPortfolioPerformance).toHaveBeenCalledWith(
      '3', { end_date: '2026-07-15' }, expect.any(AbortSignal),
    ))
    expect(screen.getByText('Top Holdings')).toBeInTheDocument()
    await act(async () => { rejectTaxonomy(new Error('Classification is unavailable')) })
    expect(screen.getByText('Classification is unavailable')).toBeInTheDocument()
    expect(screen.getByText('Top Holdings')).toBeInTheDocument()
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(1)
    expect(apiMocks.getPortfolioPerformance).toHaveBeenCalledTimes(1)
  })

  it('shows and persists the selected classification without changing portfolio values', async () => {
    apiMocks.getPortfolioTaxonomyCatalog.mockResolvedValue({ portfolio_id: '3', taxonomy_configuration_version: 0,
      taxonomies: ['strategy', 'industry'].map((id) => ({ taxonomy_id: id, name: id === 'strategy' ? 'Strategy' : 'Industry', status: 'active' })),
      taxonomy_nodes: ['strategy', 'industry'].map((id) => ({ taxonomy_id: id, taxonomy_node_id: `${id}-leaf`, parent_taxonomy_node_id: null, node_name: id === 'strategy' ? 'Growth Strategy' : 'Technology', status: 'active' })),
      taxonomy_assignments: ['strategy', 'industry'].map((id) => ({ assignment_id: `${id}-assignment`, taxonomy_id: id, target_scope: 'instrument', target_entity_id: 'asset-1', taxonomy_node_id: `${id}-leaf`, status: 'active' })),
      target_resolution: [], target_sets: [], target_set_lines: [], instrument_universe: [], target_set_integrity_issues: [],
    })
    const user = userEvent.setup()
    renderPortfolioPage(<OverviewPage />, '/portfolios/3/overview', '/portfolios/:portfolioId/overview')
    const selector = await screen.findByRole('combobox', { name: 'Overview taxonomy' })
    const card = screen.getByText('Strategy Sleeves').closest('section')!
    expect(card).toHaveTextContent('Growth Strategy')
    const reads = apiMocks.getHoldingsWorkspace.mock.calls.length
    await user.selectOptions(selector, 'industry')
    expect(card).toHaveTextContent('Technology')
    expect(card).not.toHaveTextContent('Growth Strategy')
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledTimes(reads)
    expect(localStorage.getItem('investment_studio.portfolio.overview.taxonomy.3')).toBe('industry')
  })

  it('includes a funded-segment start when it is exactly the rolling return boundary', () => {
    const points = Array.from({ length: 8 }, (_, index) => {
      const day = String(index + 23).padStart(2, '0')
      const point = dailyPerformancePoint(
        `2026-06-${day}`,
        110,
        index === 0 ? 0.1 : 0,
        0.1,
      )
      return index === 0
        ? {
            ...point,
            beginning_nav: 0,
            ending_nav: 110,
            external_cash_in: 100,
            net_external_inflow: 100,
          }
        : point
    })

    expect(buildPortfolioReturnMetrics(points, '2026-06-30').oneWeek).toBeCloseTo(0.1)
  })

  it('uses a natural-month anchor for overview volatility', () => {
    const returns = [
      { date: '2026-02-28', value: 0 },
      { date: '2026-03-01', value: 0.1 },
      ...Array.from({ length: 30 }, (_, index) => ({
        date: `2026-03-${String(index + 2).padStart(2, '0')}`,
        value: 0,
      })),
    ]

    expect(
      trailingAnnualizedVolatility(returns, new Date(2026, 2, 31), 1),
    ).toBeGreaterThan(0)
  })

  it('withholds overview volatility when the latest return is stale', () => {
    const returns = [
      { date: '2026-06-28', value: 0 },
      ...Array.from({ length: 22 }, (_, index) => ({
        date: `2026-07-${String(index + 1).padStart(2, '0')}`,
        value: index % 2 === 0 ? 0.01 : -0.005,
      })),
    ]

    expect(
      trailingAnnualizedVolatility(returns, new Date(2026, 6, 28), 1),
    ).toBeNull()
  })

  it('withholds overview volatility when the return count is too sparse', () => {
    const returns = [
      { date: '2026-06-28', value: 0 },
      { date: '2026-07-14', value: 0.01 },
      { date: '2026-07-28', value: -0.005 },
    ]

    expect(
      trailingAnnualizedVolatility(returns, new Date(2026, 6, 28), 1),
    ).toBeNull()
  })

  it('renders the TWR chart, matching period return, range controls, and an actionable holding warning', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    expect(await screen.findByRole('img', { name: 'TWR Return trend' })).toBeInTheDocument()
    expect(screen.getAllByRole('slider', { name: /Portfolio chart zoom/ })).toHaveLength(2)
    expect(screen.getAllByText('+3.02%').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('Since Inception')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Portfolio TWR drawdown' })).toBeInTheDocument()
    const metricPanel = screen.getByRole('complementary', { name: 'Portfolio overview key metrics' })
    expect(within(metricPanel).getByRole('row', { name: 'MTD —' })).toBeInTheDocument()
    expect(within(metricPanel).getByRole('row', { name: 'YTD —' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Data Columns' }))
    const columnsDialog = screen.getByRole('dialog', { name: 'Choose top holdings columns' })
    await user.click(within(columnsDialog).getByRole('checkbox', { name: 'Coverage' }))
    await user.click(within(columnsDialog).getByRole('button', { name: 'Update' }))
    expect(
      screen.getByText('Action required: official NAV is missing for 2026-07-15'),
    ).toBeInTheDocument()

    expect(apiMocks.getPortfolioPerformance).toHaveBeenCalledWith('3', {
      end_date: '2026-07-15',
    }, expect.any(AbortSignal))
  })

  it('moves the operational return explanation beside its metric heading', async () => {
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
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    const hint = await screen.findByRole('button', { name: /Operational performance basis:/ })
    expect(hint).toHaveAttribute('aria-label', expect.stringContaining('NAV reconciliation'))
    expect(screen.queryByText('Operational carrying-basis return.')).not.toBeInTheDocument()
  })

  it('keeps carried holdings unavailable in top-holding market analytics', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:fcn-1',
            position_reference_id: 'fcn-1',
            derivative_contract_id: 'fcn-1',
            holding_category: 'derivatives',
            instrument_core: null,
            derivative_contract: fcnContractFixture({
              derivative_contract_id: 'fcn-1',
              contract_name: 'Carried FCN',
            }),
            market_value: 500,
            market_value_base: 500,
            cost_basis: 500,
            cost_basis_base: 500,
            carrying_value: 500,
            carrying_value_base: 500,
            valuation_basis: 'carried_cost',
            coverage_status: 'event-cost',
            performance_eligible: false,
            risk_eligible: false,
            day_change_pct: 0,
            day_change_value: 0,
            day_change_value_base: 0,
          }),
        ],
      }),
    )

    renderPortfolioPage(
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    const detailSection = (await screen.findByText('Top Holdings Detail')).closest('section')
    expect(detailSection).not.toBeNull()
    const holdingName = within(detailSection!).getByText('Carried FCN')
    const holdingRow = holdingName.closest('tr')
    expect(holdingRow).not.toBeNull()
    expect(within(holdingRow!).getAllByText('N/A').length).toBeGreaterThanOrEqual(5)
    expect(within(holdingRow!).queryByText('$0.00')).not.toBeInTheDocument()
    expect(within(holdingRow!).queryByText('0.00%')).not.toBeInTheDocument()
    expect(screen.getAllByText('Derivatives').length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText('Unassigned')).not.toBeInTheDocument()
  })

  it('separates taxonomy coverage from classified sleeve allocation', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [holdingFixture({ allocation: 0.8, market_value_base: 800 })],
      }),
    )

    renderPortfolioPage(
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    const sleeveTitle = await screen.findByText('Strategy Sleeves')
    const sleeveSection = sleeveTitle.closest('section')
    expect(sleeveSection).not.toBeNull()
    expect(within(sleeveSection!).getByText('No classified sleeves.')).toBeInTheDocument()
    expect(within(sleeveSection!).getByText('Unassigned')).toBeInTheDocument()
    expect(within(sleeveSection!).getByText('80.00%')).toBeInTheDocument()
    expect(within(sleeveSection!).queryByText('Allocated')).not.toBeInTheDocument()
  })

  it('summarizes signed asset mix on the full portfolio denominator', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:security-1',
            market_value: 800,
            market_value_base: 800,
            day_change_value: 8,
            day_change_value_base: 8,
            allocation: 0.8,
            forward_risk_share: 1,
            forward_risk_status: 'ok',
          }),
          holdingFixture({
            line_id: 'holding:fcn-1',
            position_reference_id: 'fcn-1',
            derivative_contract_id: 'fcn-1',
            holding_category: 'derivatives',
            instrument_core: null,
            derivative_contract: fcnContractFixture({
              derivative_contract_id: 'fcn-1',
              contract_name: 'Carried FCN',
            }),
            market_value: -100,
            market_value_base: -100,
            carrying_value: -100,
            carrying_value_base: -100,
            allocation: -0.1,
            valuation_basis: 'carried_cost',
            coverage_status: 'event-cost',
            day_change_value: 0,
            day_change_value_base: 0,
            forward_risk_share: null,
            forward_risk_status: 'excluded',
            performance_eligible: false,
            risk_eligible: false,
          }),
          holdingFixture({
            line_id: 'cash:usd',
            holding_category: 'cash_and_settlement',
            instrument_core: instrumentFixture({
              instrument_id: 'cash-usd',
              instrument_name: 'USD Cash',
              instrument_type: 'cash',
            }),
            market_value: 300,
            market_value_base: 300,
            day_change_pct: 0,
            day_change_value: 0,
            day_change_value_base: 0,
            cost_basis: null,
            cost_basis_base: null,
            allocation: 0.3,
            coverage_status: 'cash',
            forward_risk_share: 0,
            forward_contribution_to_variance: 0,
            forward_annualized_volatility: 0,
            forward_risk_status: 'modeled_zero',
            performance_eligible: false,
            risk_eligible: false,
          }),
        ],
        totals: {
          nav: 1_000,
          market_value: 1_000,
          day_change_pct: null,
          day_change_value: null,
          cost_basis: 700,
          allocation: 1,
        },
      }),
    )

    renderPortfolioPage(
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    const table = await screen.findByRole('table', { name: 'Asset mix summary' })
    const securitiesRow = within(table).getByRole('row', { name: /Securities/ })
    expect(within(securitiesRow).getByText('$800.00')).toBeInTheDocument()
    expect(within(securitiesRow).getByText('80.00%')).toBeInTheDocument()
    expect(within(securitiesRow).getByText('+$8.00')).toBeInTheDocument()
    expect(within(securitiesRow).getByText('100.00%')).toBeInTheDocument()
    expect(within(securitiesRow).getByText('Complete')).toBeInTheDocument()

    const fcnRow = within(table).getByRole('row', { name: /FCN/ })
    expect(within(fcnRow).getByText('-$100.00')).toBeInTheDocument()
    expect(within(fcnRow).getByText('-10.00%')).toBeInTheDocument()
    expect(within(fcnRow).getAllByText('N/A')).toHaveLength(2)
    expect(within(fcnRow).getByText('Event carrying')).toBeInTheDocument()

    const optionsRow = within(table).getByRole('row', { name: /Options/ })
    expect(within(optionsRow).getByText('$0.00')).toBeInTheDocument()
    expect(within(optionsRow).getByText('0.00%')).toBeInTheDocument()
    expect(within(optionsRow).getByText('No holdings')).toBeInTheDocument()
    expect(within(table).queryByRole('row', { name: /Derivatives/ })).not.toBeInTheDocument()

    const cashRow = within(table).getByRole('row', { name: /Cash & Settlement/ })
    expect(within(cashRow).getByText('$300.00')).toBeInTheDocument()
    expect(within(cashRow).getByText('30.00%')).toBeInTheDocument()
    expect(within(cashRow).getByText('$0.00')).toBeInTheDocument()
    expect(within(cashRow).getByText('0.00%')).toBeInTheDocument()
    expect(within(cashRow).getByText('Complete')).toBeInTheDocument()

    const totalRow = within(table).getByRole('row', { name: /Portfolio Total/ })
    expect(within(totalRow).getByText('$1,000.00')).toBeInTheDocument()
    expect(within(totalRow).getAllByText('100.00%')).toHaveLength(2)
    expect(within(totalRow).getByText('N/A')).toBeInTheDocument()
    expect(within(totalRow).getByText('NAV reconciled')).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: /Asset mix by signed portfolio weight/ }),
    ).toHaveAccessibleName(
      'Asset mix by signed portfolio weight: Securities 80.00%, FCN -10.00%, Options 0.00%, Cash & Settlement 30.00%',
    )
  })

  it.each([
    {
      scenario: 'a null return inside the seven-day window',
      dailySeries: performanceFixture().daily_series.map((point) =>
        point.as_of_date === '2026-07-12'
          ? { ...point, daily_twr: null, return_observation_eligible: false }
          : point,
      ),
    },
    {
      scenario: 'a missing observation inside the seven-day window',
      dailySeries: performanceFixture().daily_series.filter(
        (point) => point.as_of_date !== '2026-07-12',
      ),
    },
    {
      scenario: 'history shorter than the full seven-day window',
      dailySeries: performanceFixture().daily_series.filter(
        (point) => point.as_of_date >= '2026-07-10',
      ),
    },
  ])('renders 1W as unavailable for $scenario', async ({ dailySeries }) => {
    const performance = performanceFixture()
    apiMocks.getPortfolioPerformance.mockResolvedValue({
      ...performance,
      daily_series: dailySeries,
    })

    renderPortfolioPage(
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    const metricPanel = await screen.findByRole('complementary', {
      name: 'Portfolio overview key metrics',
    })
    expect(within(metricPanel).getByRole('row', { name: '1W Return —' })).toBeInTheDocument()
  })

  it('renders and deduplicates shared actionable quality warnings', async () => {
    const user = userEvent.setup()
    const warning =
      'Corporate action review required: equity-1 effective 2026-07-01 remains unconfirmed; confirm issuer evidence.'
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({ quality_warnings: [warning] }),
    )
    const performance = performanceFixture()
    apiMocks.getPortfolioPerformance.mockResolvedValue({
      ...performance,
      summary: {
        ...performance.summary,
        quality_warnings: [warning],
        effective_end_date: '2026-07-14',
        as_of_clamp_reason: 'requested_end_after_latest_reliable_endpoint',
      },
    })

    renderPortfolioPage(
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    const warningHint = await screen.findByRole('button', { name: /Data quality warning/ })
    expect(warningHint).toHaveAttribute('aria-label', expect.stringContaining(warning))
    expect(warningHint.closest('.overview-chart-controls')).not.toBeNull()
    const cutoffHint = await screen.findByRole('button', { name: /Performance data cutoff:/ })
    expect(cutoffHint.closest('.overview-chart-controls')).not.toBeNull()
    expect(cutoffHint).toHaveAttribute('aria-label', expect.stringContaining('Performance shown through 2026-07-14'))
    expect(screen.queryByText(warning)).not.toBeInTheDocument()
    await user.click(warningHint)
    expect(within(screen.getByRole('tooltip')).getAllByText(warning)).toHaveLength(1)
  })

  it('does not treat a foreign local value as base currency when FX conversion is missing', async () => {
    const user = userEvent.setup()
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:cny-local',
            instrument_core: instrumentFixture({
              instrument_id: 'cny-local',
              instrument_name: 'CNY Local Only',
              currency: 'CNY',
            }),
            market_value: 1_000,
            market_value_base: null,
            day_change_pct: 0.05,
            day_change_value: 50,
            day_change_value_base: null,
            cost_basis: 900,
            cost_basis_base: null,
            allocation: null,
            coverage_status: 'unpriced',
          }),
          holdingFixture({
            line_id: 'holding:usd-converted',
            instrument_core: instrumentFixture({
              instrument_id: 'usd-converted',
              instrument_name: 'USD Converted',
            }),
            market_value: 100,
            market_value_base: 100,
            cost_basis: 90,
            cost_basis_base: 90,
            allocation: null,
          }),
        ],
      }),
    )

    renderPortfolioPage(
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    await screen.findByText('CNY Local Only')
    await user.click(screen.getByRole('button', { name: 'Data Columns' }))
    const columnsDialog = screen.getByRole('dialog', { name: 'Choose top holdings columns' })
    await user.click(within(columnsDialog).getByRole('checkbox', { name: 'Day Change' }))
    await user.click(within(columnsDialog).getByRole('button', { name: 'Update' }))
    const detailSection = screen.getByText('Top Holdings Detail').closest('section')
    expect(detailSection).not.toBeNull()
    const detailRows = within(detailSection!).getAllByRole('row').slice(1)
    expect(detailRows[0]).toHaveTextContent('USD Converted')
    const cnyRow = detailRows.find((row) => row.textContent?.includes('CNY Local Only'))
    expect(cnyRow).toBeDefined()
    expect(within(cnyRow!).getByText('CN¥1,000.00')).toBeInTheDocument()
    expect(cnyRow).not.toHaveTextContent('$1,000.00')
    expect(cnyRow).toHaveTextContent('+CN¥50.00 (+5.00%)')
    expect(cnyRow).not.toHaveTextContent('+$50.00')

    const portfolioMetrics = screen.getByRole('complementary', { name: 'Portfolio overview key metrics' })
    expect(within(portfolioMetrics).getByRole('row', { name: 'Top 5 Weight —' })).toBeInTheDocument()
    const assetMixTable = screen.getByRole('table', { name: 'Asset mix summary' })
    expect(within(assetMixTable).getByRole('row', { name: /Securities/ })).toHaveTextContent(
      '—',
    )
    expect(screen.getByText('Asset mix unavailable.')).toBeInTheDocument()
    expect(screen.getByText('Sleeve allocation unavailable.')).toBeInTheDocument()
    expect(screen.getByText('Top holdings allocation unavailable.')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: 'Strategy sleeve allocation' })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('img', { name: 'Top holdings ranked by current weight' }),
    ).not.toBeInTheDocument()
  })

  it('discloses a benchmark with unconfirmed distribution treatment while displaying its metrics', async () => {
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
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    const benchmarkSearch = await screen.findByRole('searchbox', { name: 'Compare benchmark' })
    await user.type(benchmarkSearch, 'Market')
    await user.click(await screen.findByRole('button', { name: /Market Benchmark/ }))

    const benchmarkHint = await screen.findByRole('button', {
      name: /Benchmark comparison: Exploratory comparator/,
    })
    expect(benchmarkHint).toHaveAttribute(
      'aria-label',
      expect.stringContaining('Distribution treatment is unconfirmed'),
    )
    const metricPanel = screen.getByRole('complementary', { name: 'Portfolio overview key metrics' })
    expect(within(metricPanel).getAllByText(/Exploratory BM/).length).toBeGreaterThan(0)
    expect(metricPanel).not.toHaveTextContent(/excess/i)
  })

  it('labels a confirmed price index distinctly and shows its normalized benchmark metrics', async () => {
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
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    const benchmarkSearch = await screen.findByRole('searchbox', { name: 'Compare benchmark' })
    await user.type(benchmarkSearch, 'Market')
    const benchmarkOption = await screen.findByRole('button', { name: /Market Benchmark/ })
    await user.tab()
    await user.tab()
    expect(benchmarkOption).toHaveFocus()
    await user.keyboard('{Enter}')

    const benchmarkHint = await screen.findByRole('button', {
      name: /Benchmark comparison: Price-return comparator/,
    })
    expect(benchmarkHint).toHaveAttribute(
      'aria-label',
      expect.stringContaining('Comparison uses the selected price-return series'),
    )
    const metricPanel = screen.getByRole('complementary', { name: 'Portfolio overview key metrics' })
    expect(within(metricPanel).getAllByText(/Price BM/).length).toBeGreaterThan(0)
    expect(within(metricPanel).queryByText(/Exploratory BM/)).not.toBeInTheDocument()
  })

  it.each([
    { boundary: 'exact seven-day anchor', missingDay: 8 },
    { boundary: 'required end boundary', missingDay: 15 },
  ])('withholds benchmark 1W when the $boundary is unavailable', async ({ missingDay }) => {
    const performance = performanceFixture()
    const missingDate = `2026-07-${String(missingDay).padStart(2, '0')}`
    const benchmarkDays = Array.from({ length: 11 }, (_, index) => index + 5).filter(
      (day) => day !== missingDay,
    )
    apiMocks.getPortfolioPerformance.mockResolvedValue({
      ...performance,
      daily_series: performance.daily_series.map((point) =>
        point.as_of_date === missingDate
          ? {
              ...point,
              daily_twr: null,
              return_observation_eligible: false,
              return_coverage_state: 'unavailable',
            }
          : point,
      ),
    })
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
      metric_family: 'price',
      currency: 'USD',
      points: benchmarkDays.map((day) => ({
        date: `2026-07-${String(day).padStart(2, '0')}`,
        value: 100 + day,
      })),
      summary: {
        point_count: benchmarkDays.length,
        change_value: 10,
        change_pct: 0.1,
        high: 115,
        low: 105,
      },
    })

    const user = userEvent.setup()
    renderPortfolioPage(
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )
    const benchmarkSearch = await screen.findByRole('searchbox', { name: 'Compare benchmark' })
    await user.type(benchmarkSearch, 'Market')
    await user.click(await screen.findByRole('button', { name: /Market Benchmark/ }))

    const metricPanel = screen.getByRole('complementary', {
      name: 'Portfolio overview key metrics',
    })
    expect(within(metricPanel).getByRole('row', { name: /1W Return/ })).toHaveTextContent(
      'BM unavailable',
    )
  })
})
