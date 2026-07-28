import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import OverviewPage, {
  buildPortfolioReturnMetrics,
  trailingAnnualizedVolatility,
} from './pages/OverviewPage'
import {
  dailyPerformancePoint,
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
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))

describe('Overview rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getWorkspaceSummaryForPortfolio.mockResolvedValue(workspaceSummaryFixture())
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
      default_planning_taxonomy_id: null,
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
    })
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
    const warning =
      'Corporate action review required: equity-1 effective 2026-07-01 remains unconfirmed; confirm issuer evidence.'
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({ quality_warnings: [warning] }),
    )
    const performance = performanceFixture()
    apiMocks.getPortfolioPerformance.mockResolvedValue({
      ...performance,
      summary: { ...performance.summary, quality_warnings: [warning] },
    })

    renderPortfolioPage(
      <OverviewPage />,
      '/portfolios/3/overview',
      '/portfolios/:portfolioId/overview',
    )

    const warningText = await screen.findByText(warning)
    expect(warningText.closest('[role="status"]')).not.toBeNull()
    expect(screen.getAllByText(warning)).toHaveLength(1)
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
    expect(within(portfolioMetrics).getByRole('row', { name: 'Cash Weight —' })).toBeInTheDocument()
    expect(within(portfolioMetrics).getByRole('row', { name: 'Top 5 Weight —' })).toBeInTheDocument()
    expect(screen.getByText('Sleeve allocation unavailable.')).toBeInTheDocument()
    expect(screen.getByText('Top holdings allocation unavailable.')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: 'Strategy sleeve allocation' })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('img', { name: 'Top holdings ranked by current weight' }),
    ).not.toBeInTheDocument()
  })

  it('labels a fully covered price-only benchmark as exploratory instead of formal excess performance', async () => {
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

    expect(await screen.findByText('Manual comparator · exploratory')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(
      /Portfolio-relative differences and relative statistics are withheld/,
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
    await user.click(await screen.findByRole('button', { name: /Market Benchmark/ }))

    expect(await screen.findByText('Manual comparator · price return')).toBeInTheDocument()
    expect(await screen.findByText(/Benchmark uses close with confirmed price-return semantics/)).toHaveTextContent(
      /benchmark and relative metrics are shown/,
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
      'Exploratory BM —',
    )
  })
})
