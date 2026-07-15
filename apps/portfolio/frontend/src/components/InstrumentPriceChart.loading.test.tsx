import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import InstrumentPriceChart from './InstrumentPriceChart'

describe('Instrument price chart loading contract', () => {
  it('announces loading instead of presenting an empty-data result before the request settles', () => {
    const { container, rerender } = render(
      <InstrumentPriceChart
        chart={null}
        loading
        error={null}
        rangeKey="1y"
        onRangeChange={vi.fn()}
      />,
    )

    expect(container.querySelector('.instrument-price-chart')).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('Loading chart')).toBeInTheDocument()
    expect(screen.queryByText('No data')).not.toBeInTheDocument()
    expect(screen.queryByText('No chart data.')).not.toBeInTheDocument()

    rerender(
      <InstrumentPriceChart
        chart={null}
        loading={false}
        error={null}
        rangeKey="1y"
        onRangeChange={vi.fn()}
      />,
    )

    expect(container.querySelector('.instrument-price-chart')).toHaveAttribute('aria-busy', 'false')
    expect(screen.getByText('No data')).toBeInTheDocument()
    expect(screen.getByText('No chart data.')).toBeInTheDocument()
  })
})
