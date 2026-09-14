import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import RollingRiskMetricChart from './RollingRiskMetricChart'

const formatValue = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(1)}%`
const baseProps = { title: 'Volatility', formatValue, emptyLabel: 'No estimate' }

describe('rolling risk observations', () => {
  it('shows one valid estimate rather than presenting it as unavailable', () => {
    const { container } = render(<RollingRiskMetricChart {...baseProps} points={[{ date: '2026-09-10', value: 0.12 }]} />)
    expect(container.querySelector('.portfolio-series-label em')).toHaveTextContent('12.0%')
    expect(screen.getByRole('img', { name: 'Volatility' })).toBeVisible()
    expect(container.querySelector('circle')).not.toBeNull()
    expect(screen.queryByText('No estimate')).not.toBeInTheDocument()
  })

  it('breaks the plotted line at an unavailable window and does not present an older endpoint as current', () => {
    const { container } = render(<RollingRiskMetricChart {...baseProps} points={[
      { date: '2026-09-07', value: 0.1 }, { date: '2026-09-08', value: null },
      { date: '2026-09-09', value: 0.12 }, { date: '2026-09-10', value: null },
    ]} />)
    const path = container.querySelector('.rolling-risk-line')?.getAttribute('d') ?? ''
    expect(path.match(/M /g)).toHaveLength(2)
    expect(path).not.toContain('L ')
    expect(container.querySelector('.portfolio-series-label em')).toHaveTextContent('—')
    expect(container.querySelector('.rolling-risk-endpoint')).toBeNull()
    expect(container.querySelector('.rolling-risk-chart-head')).toHaveTextContent('2026-09-10')
  })

  it('uses only the exact benchmark date instead of carrying its previous risk estimate forward', () => {
    const { container } = render(<RollingRiskMetricChart {...baseProps}
      points={[{ date: '2026-09-09', value: 0.12 }, { date: '2026-09-10', value: 0.1 }]}
      benchmarkLabel="Reference" benchmarkPoints={[{ date: '2026-09-09', value: 0.2 }]} />)
    expect(container.querySelector('.portfolio-series-label-benchmark-row em')).toHaveTextContent('—')
    expect(container.querySelector('.rolling-risk-point-benchmark')).not.toBeNull()
  })

  it('preserves a valid zero risk estimate and leaves wholly unavailable samples empty', () => {
    const { container, rerender } = render(<RollingRiskMetricChart {...baseProps} points={[{ date: '2026-09-10', value: 0 }]} />)
    expect(container.querySelector('.portfolio-series-label em')).toHaveTextContent('0.0%')
    rerender(<RollingRiskMetricChart {...baseProps} points={[{ date: '2026-09-10', value: null }]} />)
    expect(screen.getByText('No estimate')).toBeVisible()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('keeps a long or inactive benchmark from extending the portfolio timeline or scale', () => {
    const points = [{ date: '2026-09-09', value: 0.12 }, { date: '2026-09-10', value: 0.1 }]
    const benchmarkPoints = [{ date: '2020-01-01', value: 100 }, { date: '2026-09-09', value: 0.2 }, { date: '2026-09-11', value: 100 }]
    const { container, rerender } = render(<RollingRiskMetricChart {...baseProps} points={points} benchmarkLabel="Reference" benchmarkPoints={benchmarkPoints} />)
    expect(container).not.toHaveTextContent('2020-01-01')
    expect(container).not.toHaveTextContent('2026-09-11')
    expect(container.querySelector('.rolling-risk-chart-head')).toHaveTextContent('2026-09-10')
    rerender(<RollingRiskMetricChart {...baseProps} points={points} benchmarkPoints={benchmarkPoints} />)
    expect(container.querySelector('.rolling-risk-line-benchmark')).toBeNull()
    expect(container).not.toHaveTextContent('10000.0%')
  })
})
