import { fireEvent, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ResearchSleeveCharts from './components/ResearchSleeveCharts'
import type { PortfolioResearchBacktestSleevePointRecord as SleevePoint } from './lib/api'
import { buildSleeveChartSeries, contiguousChartSegments, divergingSleeveStacks, sleeveChartPoints } from './lib/researchSleeveCharts'
import { renderPortfolioPage } from './test/renderPortfolioPage'

const sleeve = (id: string, value: number | null, label = id) => ({ top_sleeve_id: id, top_sleeve_label: label, value })
const weights: SleevePoint[] = [
  { date: '2025-12-31', sleeves: [sleeve('growth', 1.2, 'Growth'), sleeve('__cash__', -0.2, 'Cash')] },
  { date: '2026-01-02', sleeves: [sleeve('growth', 1.1, 'Growth'), sleeve('__cash__', -0.1, 'Cash')] },
  { date: '2026-03-31', sleeves: [sleeve('growth', 0.9, 'Growth'), sleeve('__cash__', 0.1, 'Cash')] },
]
const contributions: SleevePoint[] = [
  { date: '2025-12-31', sleeves: [sleeve('__cash__', -0.001, 'Cash'), sleeve('growth', 0.01, 'Growth')] },
  { date: '2026-01-02', sleeves: [sleeve('growth', 0.02, 'Growth'), sleeve('__cash__', -0.002, 'Cash')] },
  { date: '2026-03-31', sleeves: [sleeve('__cash__', -0.003, 'Cash'), sleeve('growth', 0.03, 'Growth')] },
]

function renderCharts(weightPoints = weights, contributionPoints = contributions) {
  return renderPortfolioPage(<ResearchSleeveCharts weightPoints={weightPoints} contributionPoints={contributionPoints} />, '/portfolios/3/research', '/portfolios/:portfolioId/research')
}

describe('research sleeve chart data', () => {
  it('stacks signed capital on both sides of zero without normalizing or mutating weights', () => {
    const before = JSON.stringify(weights)
    const series = buildSleeveChartSeries(weights, contributions)
    const normalized = sleeveChartPoints(weights, series)
    const result = divergingSleeveStacks(normalized, series)
    expect(result.min).toBe(-0.2)
    expect(result.max).toBe(1.2)
    const cash = result.stacks.find((item) => item.key === '__cash__')!
    expect(cash.negative[0]).toMatchObject({ lower: -0.2, upper: 0 })
    expect(cash.positive[cash.positive.length - 1]).toMatchObject({ lower: 0.9, upper: 1 })
    expect(cash.negative[cash.negative.length - 1]).toMatchObject({ lower: 0, upper: 0 })
    const crossing = cash.positive[2]!
    expect(crossing.time).toBe((normalized[1].time + normalized[2].time) / 2)
    expect(crossing.upper - crossing.lower).toBe(0)
    expect(cash.negative[2]!.upper - cash.negative[2]!.lower).toBe(0)
    expect(normalized[0].values.get('growth')).toBe(1.2)
    expect(JSON.stringify(weights)).toBe(before)
  })

  it('keeps explicit unknowns as gaps while preserving replay’s omitted-zero contract', () => {
    const points = [
      { date: '2026-01-01', sleeves: [sleeve('a', 1), sleeve('b', 0)] },
      { date: '2026-01-02', sleeves: [sleeve('a', null), sleeve('b', 0.2)] },
      { date: '2026-01-03', sleeves: [sleeve('a', 1)] },
    ]
    const series = buildSleeveChartSeries(points)
    const normalized = sleeveChartPoints(points, series)
    expect(normalized[1].values.get('a')).toBeNull()
    expect(normalized[2].values.get('b')).toBe(0)
    const result = divergingSleeveStacks(normalized, series)
    expect(result.stacks.every((item) => item.positive[1] === null && item.negative[1] === null)).toBe(true)
    expect(contiguousChartSegments(result.stacks[0].positive)).toHaveLength(2)
  })
})

describe('research sleeve charts', () => {
  it('renders negative weights, percentage axes and dates including years', () => {
    renderCharts()
    const chart = screen.getByRole('img', { name: 'Top-Level Weights · Backtest' })
    expect(chart.querySelector('[data-sleeve-key="__cash__"] path[data-stack-sign="negative"]')).not.toBeNull()
    expect(within(chart).getByText('2025-12-31')).toBeInTheDocument()
    expect(within(chart).getByText('2026-03-31')).toBeInTheDocument()
    expect([...chart.querySelectorAll('text')].some((item) => /^-.*%$/.test(item.textContent ?? ''))).toBe(true)
    expect([...chart.querySelectorAll('text')].some((item) => item.textContent?.endsWith('%') && Number.parseFloat(item.textContent) > 100)).toBe(true)
    expect(chart).toHaveAttribute('tabindex', '0')
  })

  it('shows every sleeve and shares its color across differently ordered weight and contribution data', () => {
    const sleeves = Array.from({ length: 8 }, (_, index) => sleeve(`node-${index}`, (index + 1) / 100, `Sleeve ${index + 1}`))
    renderCharts([{ date: '2026-01-01', sleeves }, { date: '2026-02-01', sleeves }], [
      { date: '2026-01-01', sleeves: [...sleeves].reverse() },
      { date: '2026-02-01', sleeves: [...sleeves].reverse() },
    ])
    const regions = screen.getAllByRole('region')
    const legends = regions.map((region) => within(region).getByLabelText('Legend'))
    expect(legends).toHaveLength(2)
    for (const legend of legends) expect(legend.children).toHaveLength(8)
    for (const item of sleeves) {
      const first = legends[0].querySelector(`[data-sleeve-key="${item.top_sleeve_id}"] i`) as HTMLElement
      const second = legends[1].querySelector(`[data-sleeve-key="${item.top_sleeve_id}"] i`) as HTMLElement
      expect(first.style.backgroundColor).toBe(second.style.backgroundColor)
    }
    expect(within(legends[0]).getByText('Sleeve 8')).toBeInTheDocument()
  })

  it('reads the original cumulative contribution on keyboard and pointer interaction', () => {
    renderCharts()
    const region = screen.getByRole('region', { name: 'Top-Level Return Contributions' })
    const chart = within(region).getByRole('img')
    expect(within(region).getByText('Growth 3.00%')).toBeInTheDocument()
    expect(within(region).queryByText('Growth 6.00%')).not.toBeInTheDocument()
    fireEvent.focus(chart)
    fireEvent.keyDown(chart, { key: 'ArrowLeft' })
    expect(within(region).getByText('Growth 2.00%')).toBeInTheDocument()
    expect(region.querySelector('time')).toHaveAttribute('datetime', '2026-01-02')
    fireEvent.keyDown(chart, { key: 'Home' })
    expect(within(region).getByText('Cash -0.10%')).toBeInTheDocument()
    fireEvent.keyDown(chart, { key: 'End' })
    expect(within(region).getByText('Growth 3.00%')).toBeInTheDocument()
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 720, bottom: 280, width: 720, height: 280, toJSON: () => ({}) })
    fireEvent.pointerMove(chart, { clientX: 62 })
    expect(within(region).getByText('Growth 1.00%')).toBeInTheDocument()
    fireEvent.click(within(region).getByRole('button', { name: /^Backtest basis:/ }))
    expect(screen.getByRole('tooltip')).toHaveTextContent('Cumulative return contributions')
    expect(screen.getByRole('tooltip')).toHaveTextContent('initial simulated NAV')
  })

  it('does not connect an explicitly missing contribution or show it as zero', () => {
    const points = [
      { date: '2026-01-01', sleeves: [sleeve('growth', 0.01, 'Growth')] },
      { date: '2026-01-02', sleeves: [sleeve('growth', null, 'Growth')] },
      { date: '2026-01-03', sleeves: [sleeve('growth', 0.03, 'Growth')] },
    ]
    renderCharts(weights, points)
    const region = screen.getByRole('region', { name: 'Top-Level Return Contributions' })
    const chart = within(region).getByRole('img')
    expect(chart.querySelectorAll('[data-sleeve-key="growth"] path')).toHaveLength(2)
    fireEvent.keyDown(chart, { key: 'ArrowLeft' })
    expect(within(region).getByText('Growth —')).toBeInTheDocument()
  })

  it('keeps unavailable charts empty rather than synthesizing percentages', () => {
    renderCharts([], [{ date: '2026-01-01', sleeves: [sleeve('growth', null, 'Growth')] }])
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getAllByText('No chart data.')).toHaveLength(2)
  })
})
