import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'

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
  it('includes funded inception BOD return but uses an EOD boundary after zooming', () => {
    render(<PerformanceNavChart currency="CNY" variant="overview" includeStartDateReturn points={[]}
      twrPoints={[{ date: '2026-07-01', value: 101.9301 }, { date: '2026-08-01', value: 102 }, { date: '2026-09-30', value: 102.5862 }]} />)
    expect(screen.getByText('Period TWR +2.59%')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('slider', { name: 'Portfolio chart zoom start date' }), { target: { value: '1' } })
    expect(screen.getByText('Period TWR +0.57%')).toBeInTheDocument()
  })

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

describe('PerformanceNavChart responsive reading', () => {
  it('leaves chart settings open when Escape closes a newer dialog', async () => {
    function NestedSettings() {
      const [nested, setNested] = useState(false)
      return <>
        <PerformanceNavChart currency="USD" variant="overview" points={[]}
          twrPoints={[{ date: '2026-10-02', value: 100 }, { date: '2026-10-03', value: 99 }]} />
        <button onClick={() => setNested(true)}>Open newer dialog</button>
        <ConfirmDialog open={nested} title="Newer dialog" description="Nested focus check"
          confirmLabel="Done" onConfirm={() => setNested(false)} onCancel={() => setNested(false)} />
      </>
    }
    const user = userEvent.setup()
    render(<NestedSettings />)
    await user.click(screen.getByRole('button', { name: 'Chart settings' }))
    // Open programmatically, as an overlay action would, without an outside pointer dismissal.
    fireEvent.click(screen.getByRole('button', { name: 'Open newer dialog' }))
    await waitFor(() => expect(screen.getByRole('alertdialog', { name: 'Newer dialog' })).toContainElement(document.activeElement as HTMLElement))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('alertdialog', { name: 'Newer dialog' })).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Chart settings' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Chart settings' })).not.toBeInTheDocument()
  })

  it.each(['trigger', 'setting'])('closes settings with Escape from the %s and restores trigger focus', async (target) => {
    const user = userEvent.setup()
    renderOverviewChart([{ date: '2026-10-02', value: 100 }, { date: '2026-10-03', value: 99.57 }])
    const trigger = screen.getByRole('button', { name: 'Chart settings' })
    await user.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'Chart settings' })
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement))
    if (target === 'trigger') trigger.focus()
    else await user.click(within(dialog).getByRole('button', { name: 'Line' }))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Chart settings' })).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('Portfolio TWR Drawdown')).toBeInTheDocument()
    expect(screen.getByText('Based on TWR · Max -0.43%')).toBeInTheDocument()
  })

  it('measures the chart when a TWR-only series arrives after an empty render', () => {
    const observe = vi.fn()
    vi.stubGlobal('ResizeObserver', class {
      callback: ResizeObserverCallback
      constructor(callback: ResizeObserverCallback) { this.callback = callback }
      observe(element: Element) {
        observe(element)
        this.callback([{ contentRect: { width: 440 } } as ResizeObserverEntry], this as unknown as ResizeObserver)
      }
      disconnect() {}
    })
    try {
      const { rerender } = render(<PerformanceNavChart currency="USD" variant="overview" points={[]} twrPoints={[]} />)
      expect(observe).not.toHaveBeenCalled()
      rerender(<PerformanceNavChart currency="USD" variant="overview" points={[]} twrPoints={[
        { date: '2026-01-01', value: 100 }, { date: '2026-01-02', value: 101 },
        { date: '2026-01-03', value: 102 }, { date: '2026-01-10', value: 103 },
      ]} />)
      const chart = screen.getByRole('img', { name: 'TWR Return trend' })
      expect(chart).toHaveAttribute('viewBox', '0 0 440 318')
      expect(chart.querySelectorAll('.portfolio-nav-x-axis-label')).toHaveLength(4)
      vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 440 } as DOMRect)
      fireEvent.mouseMove(chart, { clientX: 58 + (440 - 58 - 18) * 0.45 })
      const tooltip = chart.parentElement!.querySelector('.portfolio-nav-chart-tooltip')!
      expect(tooltip).toHaveClass('portfolio-nav-chart-tooltip-compact')
      expect(tooltip).toHaveTextContent('2026-01-03')
      expect(tooltip).toHaveStyle({ left: '8px' })
    } finally { vi.unstubAllGlobals() }
  })

  it('keeps Value close-to-close and funded-day TWR explanations distinct', () => {
    render(<PerformanceNavChart currency="USD" variant="overview" includeStartDateReturn
      points={[{ date: '2026-07-01', value: 101930.10 }, { date: '2026-09-30', value: 102586.20 }]}
      twrPoints={[{ date: '2026-07-01', value: 101.9301 }, { date: '2026-09-30', value: 102.5862 }]} />)
    expect(screen.getByText('Period TWR +2.59%')).toBeInTheDocument()
    expect(screen.getByText('Includes the first funded day')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Chart settings' }))
    fireEvent.click(screen.getByRole('button', { name: 'Value' }))
    expect(screen.getByText('Period Value +0.64%')).toBeInTheDocument()
    expect(screen.getByText('First plotted close to selected close; includes cash flows')).toBeInTheDocument()
    expect(screen.queryByText('Includes the first funded day')).not.toBeInTheDocument()
  })
})
