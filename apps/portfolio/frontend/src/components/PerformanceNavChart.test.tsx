import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import PerformanceNavChart, { type PerformanceNavChartPoint } from './PerformanceNavChart'

function renderOverviewChart(
  twrPoints: PerformanceNavChartPoint[],
  benchmarkPoints: PerformanceNavChartPoint[] = [],
) {
  const valuePoints = twrPoints.map((point, index) => ({
    date: point.date,
    value: 1_000 + index * 10,
  }))

  return render(
    <PerformanceNavChart
      points={valuePoints}
      twrPoints={twrPoints}
      currency="USD"
      variant="overview"
      benchmarkPoints={benchmarkPoints}
      benchmarkLabel={benchmarkPoints.length ? 'Benchmark' : null}
    />,
  )
}

describe('PerformanceNavChart Overview TWR boundaries', () => {
  it('rebases the first visible TWR point to 100', () => {
    renderOverviewChart([
      { date: '2026-07-01', value: 90 },
      { date: '2026-07-15', value: 99 },
    ])

    expect(screen.getByText('Period TWR +10.00%')).toBeInTheDocument()
    expect(
      within(screen.getByRole('img', { name: 'TWR Return trend' })).getByText('110.00'),
    ).toBeInTheDocument()
    expect(screen.getByText('Based on TWR · Max 0.00%')).toBeInTheDocument()
  })

  it('rebases a zoomed window independently to 100', () => {
    renderOverviewChart([
      { date: '2026-05-31', value: 100 },
      { date: '2026-06-30', value: 110 },
      { date: '2026-07-01', value: 99 },
      { date: '2026-07-15', value: 108.9 },
    ])

    expect(screen.getByText('Period TWR +8.90%')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('slider', { name: 'Portfolio chart zoom start date' }), {
      target: { value: '2' },
    })

    expect(screen.getByText('Period TWR +10.00%')).toBeInTheDocument()
    expect(
      within(screen.getByRole('img', { name: 'TWR Return trend' })).getByText('110.00'),
    ).toBeInTheDocument()
    expect(screen.getByText('Based on TWR · Max 0.00%')).toBeInTheDocument()
  })

  it('rebases the benchmark first visible point to 100', () => {
    renderOverviewChart(
      [
        { date: '2026-07-01', value: 110 },
        { date: '2026-07-15', value: 121 },
      ],
      [
        { date: '2026-06-30', value: 200 },
        { date: '2026-07-01', value: 220 },
        { date: '2026-07-15', value: 242 },
      ],
    )

    expect(screen.getByText('Period +10.00%')).toBeInTheDocument()
  })

  it('renders each short-window date tick once', () => {
    const { container } = renderOverviewChart([
      { date: '2026-07-10', value: 100 },
      { date: '2026-07-11', value: 101 },
      { date: '2026-07-12', value: 102 },
    ])

    const dateLabels = Array.from(
      container.querySelectorAll('.portfolio-nav-x-axis-label'),
      (element) => element.textContent,
    )
    expect(dateLabels).toHaveLength(3)
    expect(new Set(dateLabels).size).toBe(dateLabels.length)
  })
})
