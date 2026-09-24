// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import ResearchQuantFigure from './ResearchQuantFigure'
import type { SavedResearchSource } from '../lib/researchDossierApi'
afterEach(cleanup)
const source = (): SavedResearchSource => ({ source_id: 'computed:quant', source_type: 'computed_metric', data: { analysis_kind: 'python_quant', summary: '使用留存共同样本。', tables: [{ key: 'observed', title: '实际观测', columns: [{ key: 'date', label: '日期' }, { key: 'return', label: '累计收益', unit: '%' }], rows: [{ date: '2026-01-01', return: 10 }, { date: '2026-01-02', return: 11 }, { date: '2026-01-11', return: 12 }] }], charts: [{ key: 'returns', title: '留存收益路径', kind: 'line', table_key: 'observed', x_key: 'date', series: [{ key: 'return', label: '累计收益' }], y_label: '%' }] } })

it('plots retained values on proportional dates rather than compressing missing days', () => {
  const { container } = render(<ResearchQuantFigure source={source()} />)
  expect(screen.getByRole('img', { name: '留存收益路径' })).toBeTruthy()
  expect(screen.getByRole('region', { name: '留存收益路径' }).getAttribute('tabindex')).toBe('0')
  expect(screen.getByRole('img', { name: '留存收益路径' }).closest('.research-quant-plot')).toBeTruthy()
  const circles = [...container.querySelectorAll('circle')]
  const x = circles.map(circle => Number(circle.getAttribute('cx')))
  expect((x[1] - x[0]) / (x[2] - x[0])).toBeCloseTo(0.1)
  expect(circles[1].textContent).toContain('2026-01-02 · 累计收益: 11')
  expect([...container.querySelectorAll('svg text')].map(tick => tick.textContent).filter(text => text?.startsWith('2026-')))
    .toEqual(['2026-01-01', '2026-01-11'])
  expect(screen.getByText('实际观测 · 3 条观测')).toBeTruthy()
})

it('leaves a break for a missing value and never creates a zero observation', () => {
  const saved = source(); saved.data!.tables![0].rows[1].return = null
  const { container } = render(<ResearchQuantFigure source={saved} />)
  expect(container.querySelectorAll('circle')).toHaveLength(2)
  expect(container.querySelectorAll('polyline')).toHaveLength(2)
})

it('does not draw data when a chart references a missing retained column', () => {
  const saved = source(); saved.data!.charts![0].series = [{ key: 'invented', label: '不存在的指标' }]
  render(<ResearchQuantFigure source={saved} />)
  expect(screen.queryByRole('img')).toBeNull()
  expect(screen.getByText('图表引用的列不在留存数据中。')).toBeTruthy()
})

it('labels every financial category and preserves negative bars on the same zero axis', () => {
  const saved = source()
  saved.data!.tables![0].rows = [{ date: '经营现金流', return: 22.945 }, { date: '资本开支', return: 67.678 }, { date: '自由现金流', return: -44.670 }]
  saved.data!.charts![0].kind = 'bar'
  const { container } = render(<ResearchQuantFigure source={saved} />)
  const labels = [...container.querySelectorAll('svg text')].map(node => node.textContent)
  expect(labels).toEqual(expect.arrayContaining(['经营现金流', '资本开支', '自由现金流']))
  const bars = [...container.querySelectorAll('rect')]
  expect(bars).toHaveLength(3)
  expect(bars[2].textContent).toContain('-44.67')
  const zeroAxis = container.querySelector('line[stroke="#97a6b5"]')!
  expect(Number(bars[2].getAttribute('y'))).toBe(Number(zeroAxis.getAttribute('y1')))
  expect(Number(bars[2].getAttribute('height'))).toBeGreaterThan(0)
})

it('retains small nonzero results in the table, metrics, tooltips and chart scale', () => {
  const saved = source()
  saved.data!.metrics = { variance: 0.00000125 }
  saved.data!.tables![0].rows = [{ date: '2026-01-01', return: 0.00000125 }, { date: '2026-01-02', return: -0.0000005 }]
  const { container } = render(<ResearchQuantFigure source={saved} />)
  expect(container.querySelector('.research-quant-metrics dd')?.textContent).toBe('0.00000125')
  fireEvent.click(screen.getByRole('button', { name: '实际观测 · 2 条观测' }))
  expect(screen.getByRole('dialog').querySelector('tbody')?.textContent).toContain('-0.0000005')
  expect(container.querySelector('circle title')?.textContent).toContain('0.00000125')
  expect([...container.querySelectorAll('svg text')].map(tick => tick.textContent)).toEqual(expect.arrayContaining(['-1.00e-6', '1.00e-6', '2.00e-6']))
  expect(container.querySelector('svg text')?.textContent).toContain('e-')
})

it('draws an all-zero bar series on a finite axis without inventing nonzero observations', () => {
  const saved = source(); saved.data!.charts![0].kind = 'bar'
  saved.data!.tables![0].rows = [{ date: '2026-01-01', return: 0 }, { date: '2026-01-02', return: 0 }]
  const { container } = render(<ResearchQuantFigure source={saved} />)
  expect(container.querySelector('svg')?.outerHTML).not.toMatch(/NaN|Infinity/)
  const bars = [...container.querySelectorAll('rect')]
  expect(bars).toHaveLength(2)
  expect(bars.every(bar => Number(bar.getAttribute('height')) === 0)).toBe(true)
  expect(bars[0].textContent).toContain('累计收益: 0')
})
