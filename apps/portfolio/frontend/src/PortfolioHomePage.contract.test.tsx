import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfolioHomePage from './pages/PortfolioHomePage'
import { holdingFixture, holdingsWorkspaceFixture } from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  getHoldingsWorkspace: vi.fn(),
  getPortfolioTaxonomyCatalog: vi.fn(),
  getPortfolioTableViewStore: vi.fn(),
  savePortfolioTableViewStore: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function HoldingsRouteHarness() {
  const navigate = useNavigate()
  return (
    <>
      <button type="button" onClick={() => navigate('/portfolios/4/holdings')}>
        Switch portfolio
      </button>
      <Routes>
        <Route path="/portfolios/:portfolioId/holdings" element={<PortfolioHomePage />} />
      </Routes>
    </>
  )
}

describe('Holdings rendered page contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        quality_warnings: [
          'Alpha Fund YTD return is unavailable because the 2025-12-31 start anchor is missing.',
        ],
        rows: [holdingFixture({ instrument_return_ytd: null })],
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
    apiMocks.getPortfolioTableViewStore.mockResolvedValue({ store: null })
    apiMocks.savePortfolioTableViewStore.mockResolvedValue({})
  })

  it('uses canonical defaults for an empty backend store and ignores legacy browser views', async () => {
    const legacyStore = JSON.stringify({
      activeViewId: 'return-risk',
      views: [],
    })
    window.localStorage.setItem('portfolio_ops.portfolio.holdings.views.v4', legacyStore)

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    expect(await screen.findByRole('button', { name: /View\s*: Default/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sort Quote: ascending' })).toBeInTheDocument()
    expect(window.localStorage.getItem('portfolio_ops.portfolio.holdings.views.v4')).toBe(legacyStore)
  })

  it('marks table views unavailable when the backend view store cannot be loaded', async () => {
    apiMocks.getPortfolioTableViewStore.mockRejectedValueOnce(new Error('View store offline.'))

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('View store offline.')
    expect(screen.getByText('Table views unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /View\s*:/ })).not.toBeInTheDocument()
  })

  it('upgrades the persisted Return & Risk system view with instrument 1M return', async () => {
    apiMocks.getPortfolioTableViewStore.mockResolvedValueOnce({
      store: {
        activeViewId: 'return-risk',
        views: [
          {
            id: 'return-risk',
            name: 'Return & Risk',
            readonly: true,
            state: {
              columns: [
                'instrument',
                'instrument_return_1w',
                'instrument_return_mtd',
                'unrealized_pct',
              ],
              columnWidths: {},
              groupBy: 'none',
              sortField: 'unrealized_pct',
              sortDirection: 'desc',
            },
          },
        ],
      },
    })

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    expect(await screen.findByRole('button', { name: /View\s*: Return & Risk/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /1M Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Unrealized Return/ })).toBeInTheDocument()
  })

  it('does not render the prior portfolio workspace while the next portfolio is loading', async () => {
    const nextWorkspace = deferred<ReturnType<typeof holdingsWorkspaceFixture>>()
    apiMocks.getHoldingsWorkspace
      .mockResolvedValueOnce(holdingsWorkspaceFixture())
      .mockReturnValueOnce(nextWorkspace.promise)
    apiMocks.getPortfolioTaxonomyCatalog.mockImplementation((portfolioId: string) =>
      Promise.resolve({
        portfolio_id: portfolioId,
        default_planning_taxonomy_id: null,
        taxonomies: [],
        taxonomy_nodes: [],
        taxonomy_assignments: [],
        instrument_universe: [],
        target_sets: [],
        target_set_lines: [],
        target_set_integrity_issues: [],
      }),
    )

    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/portfolios/3/holdings']}>
        <HoldingsRouteHarness />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('cell', { name: /Alpha Fund/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Switch portfolio' }))

    expect(screen.queryByRole('cell', { name: /Alpha Fund/ })).not.toBeInTheDocument()

    act(() =>
      nextWorkspace.resolve(
        holdingsWorkspaceFixture({
          portfolio_id: '4',
          rows: [
            holdingFixture({
              instrument_core: {
                ...holdingFixture().instrument_core,
                instrument_id: 'asset-4',
                instrument_name: 'Beta Fund',
              },
            }),
          ],
        }),
      ),
    )
    expect(await screen.findByRole('cell', { name: /Beta Fund/ })).toBeInTheDocument()
  })

  it('renders the default trend, explains a legitimate unavailable return, and preserves total-row semantics', async () => {
    const user = userEvent.setup()
    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    expect(await screen.findByRole('cell', { name: /Alpha Fund/ })).toBeInTheDocument()
    expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledWith('3', {
      as_of_date: undefined,
    })
    expect(
      screen.getByText(
        'Trend: Dividend-Reinvested Total Return NAV · Complete · 250 observations',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Policy-preferred trend basis selected.')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Chart 6M/ })).toBeInTheDocument()
    const unavailableReason = screen.getByText(
      'Alpha Fund YTD return is unavailable because the 2025-12-31 start anchor is missing.',
    )
    expect(unavailableReason.closest('[role="status"]')).not.toBeNull()
    const totalLabel = screen.getByText('Portfolio Total (USD)')
    const totalRow = totalLabel.closest('tr')
    expect(totalRow).not.toBeNull()
    expect(within(totalRow!).getByText('$800.00')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))

    await waitFor(() => {
      expect(apiMocks.getHoldingsWorkspace).toHaveBeenCalledWith('3', {
        as_of_date: undefined,
        include_details: true,
      })
    })
    expect(screen.getByRole('columnheader', { name: /1W Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /1M Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /3M Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /6M Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Holding Since/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^MTD/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^YTD/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^1Y Resize/ })).toBeInTheDocument()

    const holdingRow = screen.getByRole('cell', { name: /Alpha Fund/ }).closest('tr')
    expect(holdingRow).not.toBeNull()
    expect(within(holdingRow!).getByText('+1.23%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('2026-06-23')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+3.45%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+4.56%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+6.78%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+2.34%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+10.00%')).toBeInTheDocument()

    const ytdHeader = screen.getByRole('columnheader', { name: /^YTD/ })
    const ytdColumnIndex = Array.from(ytdHeader.parentElement!.children).indexOf(ytdHeader)
    expect(holdingRow!.children[ytdColumnIndex]).toHaveTextContent('—')

    const refreshedTotalRow = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(refreshedTotalRow).not.toBeNull()
    expect(refreshedTotalRow).not.toHaveTextContent(/TWR/i)
    const expectedTotalReturns = {
      instrument_return_1w: '+1.23%',
      instrument_return_1m: '+3.45%',
      instrument_return_3m: '+4.56%',
      instrument_return_6m: '+6.78%',
      instrument_return_mtd: '+2.34%',
      instrument_return_ytd: '—',
      instrument_return_1y: '+10.00%',
    }
    for (const [columnKey, expectedReturn] of Object.entries(expectedTotalReturns)) {
      const totalCell = refreshedTotalRow!.querySelector(`[data-column-key="${columnKey}"]`)
      expect(totalCell).not.toBeNull()
      expect(totalCell).toHaveTextContent(expectedReturn)
      expect(totalCell).toHaveAttribute(
        'title',
        'Current-holdings basket market return using as-of market-value weights; it may include distributions and is neither book unrealized P&L nor historical portfolio TWR.',
      )
    }
  })

  it('excludes cash from the unrealized-return denominator', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValueOnce(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            quantity: 10,
            last_price: 12,
            market_value: 120,
            market_value_base: 120,
            cost_basis: 100,
            cost_basis_base: 100,
            allocation: 0.6,
          }),
          holdingFixture({
            line_id: 'cash:USD',
            instrument_core: {
              instrument_id: 'cash:USD',
              instrument_name: 'USD Cash',
              instrument_type: 'cash',
              currency: 'USD',
              identifiers: [],
            },
            quantity: 80,
            last_price: 1,
            market_value: 80,
            market_value_base: 80,
            cost_basis_method: null,
            cost_basis: null,
            cost_basis_base: null,
            allocation: 0.4,
            day_change_pct: 0,
            day_change_value: 0,
            day_change_value_base: 0,
            price_chart_1m: [],
            price_chart_3m: [],
            price_chart_6m: [],
            price_chart_1y: [],
            coverage_status: 'cash',
            account_ids: ['cash-account'],
            account_count: 1,
            open_position_lot_count: 0,
          }),
        ],
        totals: {
          market_value: 200,
          day_change_pct: 0,
          day_change_value: 0,
          cost_basis: 100,
          allocation: 1,
        },
      }),
    )

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    const totalRow = (await screen.findByText('Portfolio Total (USD)')).closest('tr')
    expect(totalRow).not.toBeNull()
    expect(totalRow!.querySelector('[data-column-key="unrealized_pct"]')).toHaveTextContent('+20.00%')
  })

  it('uses current base-market-value weights for group and total instrument returns', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:fund-a',
            instrument_core: {
              instrument_id: 'fund-a',
              instrument_name: 'Fund A',
              instrument_type: 'fund',
              currency: 'USD',
              identifiers: [{ identifier_type: 'ticker', identifier_value: 'FUNDA', is_primary: true }],
            },
            market_value: 750,
            market_value_base: 750,
            cost_basis: 750,
            cost_basis_base: 750,
            allocation: 0.75,
            instrument_return_1m: 0.10,
          }),
          holdingFixture({
            line_id: 'holding:fund-b',
            instrument_core: {
              instrument_id: 'fund-b',
              instrument_name: 'Fund B',
              instrument_type: 'fund',
              currency: 'USD',
              identifiers: [{ identifier_type: 'ticker', identifier_value: 'FUNDB', is_primary: true }],
            },
            market_value: 250,
            market_value_base: 250,
            cost_basis: 250,
            cost_basis_base: 250,
            allocation: 0.25,
            instrument_return_1m: -0.10,
          }),
        ],
        totals: {
          market_value: 1000,
          day_change_pct: 0.01,
          day_change_value: 10,
          cost_basis: 1000,
          allocation: 1,
        },
      }),
    )
    const user = userEvent.setup()

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await screen.findByRole('cell', { name: /Fund A/ })
    await user.click(screen.getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))
    await user.click(screen.getByRole('button', { name: /Group By\s*: None/ }))
    await user.click(screen.getByRole('button', { name: 'Instrument Type' }))

    const groupRow = document.querySelector('.holdings-group-row')
    const totalRow = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(groupRow).not.toBeNull()
    expect(totalRow).not.toBeNull()
    expect(groupRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('+5.00%')
    expect(totalRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('+5.00%')
    expect(groupRow!.querySelector('[data-column-key="instrument_holding_max_drawdown"]')?.textContent?.trim()).toBe('')
    expect(totalRow!.querySelector('[data-column-key="instrument_holding_max_drawdown"]')?.textContent?.trim()).toBe('')
    expect(groupRow!.querySelector('[data-column-key="holding_date"]')?.textContent?.trim()).toBe('')
    expect(totalRow!.querySelector('[data-column-key="holding_date"]')?.textContent?.trim()).toBe('')
    expect(groupRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveAttribute(
      'data-aggregation-kind',
      'current_weight_return',
    )
    expect(groupRow!.querySelector('[data-column-key="instrument_holding_max_drawdown"]')).toHaveAttribute(
      'data-aggregation-kind',
      'none',
    )
  })

  it('withholds grouped local-currency returns when the current basket spans currencies', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:usd-fund',
            instrument_core: {
              instrument_id: 'usd-fund',
              instrument_name: 'USD Fund',
              instrument_type: 'fund',
              currency: 'USD',
              identifiers: [],
            },
            market_value: 500,
            market_value_base: 500,
            cost_basis: 500,
            cost_basis_base: 500,
            allocation: 0.5,
            instrument_return_1m: 0.1,
          }),
          holdingFixture({
            line_id: 'holding:cny-fund',
            instrument_core: {
              instrument_id: 'cny-fund',
              instrument_name: 'CNY Fund',
              instrument_type: 'fund',
              currency: 'CNY',
              identifiers: [],
            },
            market_value: 3_500,
            market_value_base: 500,
            cost_basis: 3_500,
            cost_basis_base: 500,
            allocation: 0.5,
            instrument_return_1m: 0.1,
          }),
        ],
        totals: {
          market_value: 1000,
          day_change_pct: 0,
          day_change_value: 0,
          cost_basis: 1000,
          allocation: 1,
        },
      }),
    )
    const user = userEvent.setup()

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await screen.findByRole('cell', { name: /CNY Fund/ })
    await user.click(screen.getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))
    await user.click(screen.getByRole('button', { name: /Group By\s*: None/ }))
    await user.click(screen.getByRole('button', { name: 'Instrument Type' }))

    const groupRow = document.querySelector('.holdings-group-row')
    const totalRow = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(groupRow).not.toBeNull()
    expect(totalRow).not.toBeNull()
    expect(groupRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('—')
    expect(totalRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('—')
  })

  it('withholds a current-basket return when any material current holding lacks the window', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            line_id: 'holding:covered',
            market_value: 900,
            market_value_base: 900,
            cost_basis: 900,
            cost_basis_base: 900,
            allocation: 0.9,
            instrument_return_1m: 0.10,
          }),
          holdingFixture({
            line_id: 'holding:missing',
            instrument_core: {
              instrument_id: 'fund-missing',
              instrument_name: 'Missing Return Fund',
              instrument_type: 'fund',
              currency: 'USD',
              identifiers: [],
            },
            market_value: 100,
            market_value_base: 100,
            cost_basis: 100,
            cost_basis_base: 100,
            allocation: 0.1,
            instrument_return_1m: null,
          }),
        ],
        totals: {
          market_value: 1000,
          day_change_pct: 0.01,
          day_change_value: 10,
          cost_basis: 1000,
          allocation: 1,
        },
      }),
    )
    const user = userEvent.setup()

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    await screen.findByRole('cell', { name: /Missing Return Fund/ })
    await user.click(screen.getByRole('button', { name: /View\s*: Default/ }))
    await user.click(screen.getByRole('option', { name: 'Return & Risk' }))

    const totalRow = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(totalRow).not.toBeNull()
    expect(totalRow!.querySelector('[data-column-key="instrument_return_1m"]')).toHaveTextContent('—')
  })

  it('does not substitute a chart date when the valuation quote date is unavailable', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValueOnce(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            quote_as_of_date: null,
            price_chart_6m: [
              { date: '2026-06-15', value: 75 },
              { date: '2026-07-15', value: 80 },
            ],
          }),
        ],
      }),
    )

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    const holdingRow = (await screen.findByRole('cell', { name: /Alpha Fund/ })).closest('tr')
    const quoteDateHeader = screen.getByRole('columnheader', { name: /Quote Date/ })
    const quoteDateColumnIndex = Array.from(quoteDateHeader.parentElement!.children).indexOf(quoteDateHeader)
    expect(holdingRow!.children[quoteDateColumnIndex]).toHaveTextContent('—')
    expect(holdingRow!.children[quoteDateColumnIndex]).not.toHaveTextContent('2026-07-15')
  })

  it('discloses alternate-basis selection and partial history instead of hiding the trend semantics', async () => {
    apiMocks.getHoldingsWorkspace.mockResolvedValue(
      holdingsWorkspaceFixture({
        rows: [
          holdingFixture({
            instrument_trend_basis: 'close',
            instrument_trend_coverage: {
              state: 'partial',
              observation_count: 40,
              start_date: '2026-05-01',
              end_date: '2026-07-15',
              available_return_windows: ['1w', 'mtd'],
            },
            instrument_trend_reason: 'selected_more_complete_alternate_series',
          }),
        ],
      }),
    )

    renderPortfolioPage(
      <PortfolioHomePage />,
      '/portfolios/3/holdings',
      '/portfolios/:portfolioId/holdings',
    )

    expect(await screen.findByText('Trend: Close · Partial · 40 observations')).toBeInTheDocument()
    expect(
      screen.getByText('Using Close because it provides more complete history than the preferred basis.'),
    ).toBeInTheDocument()
  })
})
