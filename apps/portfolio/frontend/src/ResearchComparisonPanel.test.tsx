import { fireEvent, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ResearchComparisonPanel from './components/ResearchComparisonPanel'
import type { PortfolioResearchCurrentContextRecord } from './lib/api'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const context = {
  portfolio_inception_date: '2026-07-01', performance_start_date: '2026-07-01', performance_end_date: '2026-07-15',
  performance_coverage_state: 'complete', performance_valuation_basis: 'market_value',
  chart_points: [{ date: '2026-06-30', value: 1, is_start_anchor: true }, { date: '2026-07-01', value: 1.01 }, { date: '2026-07-15', value: 1.05 }],
} as PortfolioResearchCurrentContextRecord

function renderComparison(overrides: Partial<React.ComponentProps<typeof ResearchComparisonPanel>> = {}) {
  return renderPortfolioPage(<ResearchComparisonPanel context={context} points={context.chart_points} endDate="2026-07-15" benchmarkPoints={[]} benchmarkLoading={false} benchmarkError={null} currentTargets {...overrides} />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
}

describe('research comparison presentation', () => {
  it('retains the existing benchmark BOD value at an explicitly shared inception anchor', () => {
    renderComparison({ benchmarkPoints: [{ date: '2026-06-30', value: 100 }, { date: '2026-07-01', value: 102 }, { date: '2026-07-15', value: 110 }] })
    const metricTable = screen.getByRole('table', { name: '' })
    expect(within(metricTable).getByRole('row', { name: /Period Return/ })).toHaveTextContent('10.00%')
    expect(within(metricTable).getByRole('row', { name: /Common Window/ })).toHaveTextContent('2026-07-01 – 2026-07-15')
    expect(screen.queryByText(/Benchmark does not cover/)).not.toBeInTheDocument()
    const chart = screen.getByRole('img', { name: /Actual portfolio, backtest/ })
    fireEvent.keyDown(chart, { key: 'ArrowLeft' })
    fireEvent.keyDown(chart, { key: 'ArrowLeft' })
    expect(screen.getByText('2026-07-01 · Opening anchor')).toBeInTheDocument()
  })

  it('discloses a complete prefix ending before the run cutoff and a selected scope', () => {
    renderComparison({ context: { ...context, performance_end_date: '2026-07-01', chart_points: context.chart_points.slice(0, 2) }, selectedScope: 'Equity' })
    expect(screen.getByText(/Available actual returns: 2026-07-01 to 2026-07-01/)).toBeInTheDocument()
    expect(screen.getByText(/Backtest covers “Equity”/)).toBeInTheDocument()
  })

  it('clips unmarked archived pre-inception data and keeps monthly returns in one comparison surface', () => {
    renderComparison({ points: [{ date: '2025-12-01', value: 1 }, { date: '2026-07-01', value: 2 }, { date: '2026-07-15', value: 2.2 }] })
    expect(screen.queryByText('2025-12-01')).not.toBeInTheDocument()
    expect(screen.getAllByText('Actual vs Backtest')).toHaveLength(1)
    expect(screen.getByRole('table', { name: 'Monthly return matrix' })).toBeInTheDocument()
    const matrix = screen.getByRole('table', { name: 'Monthly return matrix' })
    expect(within(matrix).getAllByRole('columnheader')).toHaveLength(15)
    expect(within(matrix).getByRole('columnheader', { name: 'Year / YTD' })).toBeInTheDocument()
    expect(within(matrix).getByRole('row', { name: /2026 Actual/ })).toBeInTheDocument()
    expect(within(matrix).getByRole('row', { name: /Backtest/ })).toBeInTheDocument()
    expect(within(matrix).getByRole('row', { name: /Difference/ })).toBeInTheDocument()
    expect(screen.queryByText('Archived Historical Backtest')).not.toBeInTheDocument()
  })
  it('explains the actual inception holdings boundary and exposes unavailable initial valuations', () => {
    renderComparison({ points: [], initialState: { source: 'portfolio_inception_eod_holdings', status: 'unavailable', as_of_date: '2026-07-01', unavailable_reason: 'Initial valuation is incomplete.' } as NonNullable<React.ComponentProps<typeof ResearchComparisonPanel>['initialState']> })
    expect(screen.getByText('Actual inception holdings cannot be reliably valued; the backtest is unavailable.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^Return basis:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('actual inception end-of-day holdings and cash')
    expect(screen.getByRole('tooltip')).toHaveTextContent('Insufficient risk data delays the first rebalance')
  })

})
