import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
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
      <MemoryRouter
        initialEntries={['/portfolios/3/holdings']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
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
    expect(screen.getByText('Trend: Total Return NAV · Complete · 250 observations')).toBeInTheDocument()
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

    expect(screen.getByRole('columnheader', { name: /1W Return/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^MTD/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^YTD/ })).toBeInTheDocument()

    const holdingRow = screen.getByRole('cell', { name: /Alpha Fund/ }).closest('tr')
    expect(holdingRow).not.toBeNull()
    expect(within(holdingRow!).getByText('+1.23%')).toBeInTheDocument()
    expect(within(holdingRow!).getByText('+2.34%')).toBeInTheDocument()

    const ytdHeader = screen.getByRole('columnheader', { name: /^YTD/ })
    const ytdColumnIndex = Array.from(ytdHeader.parentElement!.children).indexOf(ytdHeader)
    expect(holdingRow!.children[ytdColumnIndex]).toHaveTextContent('—')

    const refreshedTotalRow = screen.getByText('Portfolio Total (USD)').closest('tr')
    expect(refreshedTotalRow).not.toBeNull()
    expect(refreshedTotalRow).not.toHaveTextContent(/TWR/i)
    for (const columnKey of [
      'instrument_return_1w',
      'instrument_return_mtd',
      'instrument_return_ytd',
    ]) {
      const totalCell = refreshedTotalRow!.querySelector(`[data-column-key="${columnKey}"]`)
      expect(totalCell).not.toBeNull()
      expect(totalCell).toHaveTextContent('—')
      expect(totalCell).toHaveAttribute(
        'title',
        'Portfolio return is reported as TWR on Overview and Performance.',
      )
    }
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
