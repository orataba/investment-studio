import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PortfolioSecurityDetailPage from './pages/PortfolioSecurityDetailPage'
import { holdingFixture, instrumentFixture } from './test/portfolioFixtures'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const apiMocks = vi.hoisted(() => ({
  getPortfolioInstrumentHoldingProjection: vi.fn(),
  getPortfolioInstrumentPriceChart: vi.fn(),
  getPortfolioPositionLots: vi.fn(),
  getPortfolioTransactions: vi.fn(),
}))

vi.mock('./lib/api', () => apiMocks)
vi.mock('./components/PortfolioWorkspaceLayout', () => ({
  default: ({ children }: { children: unknown }) => children,
}))
vi.mock('./components/InstrumentPriceChart', () => ({
  default: ({
    chart,
    loading,
    onRangeChange,
  }: {
    chart: { points: unknown[] } | null
    loading: boolean
    onRangeChange: (rangeKey: '1m') => void
  }) => (
    <>
      <div data-testid="instrument-price-chart">
        {loading
          ? `Loading detail chart; ${chart?.points.length ?? 0} retained points`
          : `${chart?.points.length ?? 0} detail chart points`}
      </div>
      <button type="button" onClick={() => onRangeChange('1m')}>1M</button>
    </>
  ),
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function compactHoldingProjection() {
  const row = { ...holdingFixture() }
  delete (row as Partial<typeof row>).price_chart_1m
  delete (row as Partial<typeof row>).price_chart_3m
  delete (row as Partial<typeof row>).price_chart_6m
  delete (row as Partial<typeof row>).price_chart_1y
  return row
}

describe('Security Detail lazy-load contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getPortfolioInstrumentHoldingProjection.mockResolvedValue({
      portfolio_id: '3',
      portfolio_name: 'Contract Portfolio',
      base_currency: 'USD',
      as_of_date: '2026-07-15',
      view_label: 'View: Holdings',
      quality_warnings: [],
      row: compactHoldingProjection(),
    })
    apiMocks.getPortfolioInstrumentPriceChart.mockResolvedValue({
      portfolio_id: '3',
      instrument_core: instrumentFixture(),
      as_of_date: '2026-07-15',
      range_key: '1y',
      chart_basis: 'total_return_nav',
      metric_family: 'total_return_nav',
      currency: 'USD',
      points: [
        { date: '2025-07-15', value: 60 },
        { date: '2026-07-15', value: 80 },
      ],
      summary: {
        point_count: 2,
        change_value: 20,
        change_pct: 1 / 3,
        high: 80,
        low: 60,
      },
    })
    apiMocks.getPortfolioPositionLots.mockResolvedValue({
      portfolio_id: '3',
      summary: {
        position_lot_count: 0,
        open_position_lot_count: 0,
        closed_position_lot_count: 0,
        realized_pnl: 0,
      },
      position_lots: [],
    })
    apiMocks.getPortfolioTransactions.mockResolvedValue({
      portfolio_id: '3',
      summary: {
        total_transactions: 0,
        instrument_transactions: 0,
        external_cash_flows: 0,
        opening_balance_records: 0,
      },
      derivation_boundary: {
        ledger_postings: 'derived',
        positions: 'derived',
        holdings: 'derived',
        snapshot: 'derived',
      },
      transactions: [],
    })
  })

  it('fetches one instrument projection and its detail arrays only after the detail route opens', async () => {
    renderPortfolioPage(
      <PortfolioSecurityDetailPage />,
      '/portfolios/3/holdings/asset-1',
      '/portfolios/:portfolioId/holdings/:instrumentId',
    )

    expect(await screen.findByRole('heading', { name: 'Alpha Fund' })).toBeInTheDocument()
    expect(await screen.findByText('2 detail chart points')).toBeInTheDocument()

    expect(apiMocks.getPortfolioInstrumentHoldingProjection).toHaveBeenCalledWith(
      '3',
      'asset-1',
      { as_of_date: undefined },
    )
    await waitFor(() => {
      expect(apiMocks.getPortfolioInstrumentPriceChart).toHaveBeenCalledWith(
        '3',
        'asset-1',
        { as_of_date: '2026-07-15', range: '1y' },
      )
      expect(apiMocks.getPortfolioPositionLots).toHaveBeenCalledWith('3', {
        instrument_id: 'asset-1',
        as_of_date: '2026-07-15',
      })
      expect(apiMocks.getPortfolioTransactions).toHaveBeenCalledWith('3', {
        instrument_id: 'asset-1',
        end_date: '2026-07-15',
      })
    })
  })

  it('clears the old chart while a newly selected range is loading', async () => {
    const user = userEvent.setup()
    const nextChart = deferred<Awaited<ReturnType<typeof apiMocks.getPortfolioInstrumentPriceChart>>>()
    apiMocks.getPortfolioInstrumentPriceChart
      .mockResolvedValueOnce({
        portfolio_id: '3',
        instrument_core: instrumentFixture(),
        as_of_date: '2026-07-15',
        range_key: '1y',
        chart_basis: 'total_return_nav',
        metric_family: 'total_return_nav',
        currency: 'USD',
        points: [
          { date: '2025-07-15', value: 60 },
          { date: '2026-07-15', value: 80 },
        ],
        summary: {
          point_count: 2,
          change_value: 20,
          change_pct: 1 / 3,
          high: 80,
          low: 60,
        },
      })
      .mockReturnValueOnce(nextChart.promise)

    renderPortfolioPage(
      <PortfolioSecurityDetailPage />,
      '/portfolios/3/holdings/asset-1',
      '/portfolios/:portfolioId/holdings/:instrumentId',
    )

    expect(await screen.findByText('2 detail chart points')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '1M' }))

    expect(screen.getByText('Loading detail chart; 0 retained points')).toBeInTheDocument()

    act(() => nextChart.resolve({
      portfolio_id: '3',
      instrument_core: instrumentFixture(),
      as_of_date: '2026-07-15',
      range_key: '1m',
      chart_basis: 'total_return_nav',
      metric_family: 'total_return_nav',
      currency: 'USD',
      points: [{ date: '2026-07-15', value: 80 }],
      summary: {
        point_count: 1,
        change_value: null,
        change_pct: null,
        high: 80,
        low: 80,
      },
    }))
    expect(await screen.findByText('1 detail chart points')).toBeInTheDocument()
  })
})
