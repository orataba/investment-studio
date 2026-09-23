// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
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

it('retains small nonzero results in the table, metrics, tooltips and chart scale', () => {
  const saved = source()
  saved.data!.metrics = { variance: 0.00000125 }
  saved.data!.tables![0].rows = [{ date: '2026-01-01', return: 0.00000125 }, { date: '2026-01-02', return: -0.0000005 }]
  const { container } = render(<ResearchQuantFigure source={saved} />)
  expect(container.querySelector('.research-quant-metrics dd')?.textContent).toBe('0.00000125')
  expect(container.querySelector('tbody')?.textContent).toContain('-0.0000005')
  expect(container.querySelector('circle title')?.textContent).toContain('0.00000125')
  expect([...container.querySelectorAll('svg text')].slice(0, 5).map(tick => tick.textContent)).not.toContain('0')
  expect(container.querySelector('svg text')?.textContent).toContain('e-')
})
