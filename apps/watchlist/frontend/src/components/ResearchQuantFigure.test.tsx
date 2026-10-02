// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import ResearchQuantFigure from './ResearchQuantFigure'
import { EvidenceFigure } from './ResearchModules'
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
  const zeroAxis = container.querySelector('line.research-quant-zero-axis')!
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

it('formats explicitly percentage-valued metrics without rounding unknown units or replacing missing values', () => {
  const saved = source()
  saved.data!.metrics = { return: 12.34567, breadth_pct: 51.45678, change_pp: -1.23456, variance: 0.00000125, correlation: 0.1234567890123456, missing: null }
  const { container } = render(<ResearchQuantFigure source={saved} />)
  const values = [...container.querySelectorAll('.research-quant-metrics dd')].map(item => item.textContent)
  expect(values).toEqual(['12.35%', '51.46%', '-1.23 个百分点', '0.00000125', '0.1234567890123456', '—'])
  expect(container.querySelector('.research-quant-metrics dt')?.textContent).toBe('累计收益')
})

it('formats only the defined watchlist Top 10 correlation metrics to three decimals', () => {
  const saved = source()
  saved.data!.analysis_kind = 'watchlist_observations'
  saved.data!.metrics = { '股票Top 10平均相关性（63交易日）': 0.2664975773, 'Top 10平均相关性（63交易日）': 0.2664975773,
    correlation: 0.2664975773, variance: 0.00000125 }
  const { container, rerender } = render(<ResearchQuantFigure source={saved} />)
  const values = () => [...container.querySelectorAll('.research-quant-metrics dd')].map(item => item.textContent)
  expect(values()).toEqual(['0.266', '0.266', '0.2664975773', '0.00000125'])
  rerender(<ResearchQuantFigure source={{ ...saved, data: { ...saved.data, analysis_kind: 'python_quant' } }} />)
  expect(values()).toEqual(['0.2664975773', '0.2664975773', '0.2664975773', '0.00000125'])
  expect(saved.data!.metrics['股票Top 10平均相关性（63交易日）']).toBe(0.2664975773)
})

it('presents retained EWMA observations compactly and keeps exact values and method in evidence', () => {
  const saved: SavedResearchSource = { source_id: 'computed:ewma', source_type: 'computed_metric', title: '价格波动研究',
    methodology: { metric: 'ewma_volatility', half_life_sessions: 21, annualization: 252 },
    data: { current: { date: '2026-01-11', volatility_pct: 22.12345678 }, previous: { date: '2026-01-10', volatility_pct: 21.98765432 }, change_pp: 0.13580246, five_session_change_pp: null,
      history: [{ date: '2026-01-01', volatility_pct: 18 }, { date: '2026-01-02', volatility_pct: null }, { date: '2026-01-11', volatility_pct: 22.12345678 }], limitations: ['尚无足够观察计算五次变化。'] } }
  const { container } = render(<EvidenceFigure source={saved} />)
  expect([...container.querySelectorAll('.research-quant-metrics dd')].map(item => item.textContent)).toEqual(['22.12%2026-01-11', '21.99%2026-01-10', '+0.14个百分点', '未取得'])
  expect(screen.queryByText(/EWMA 半衰期/)).toBeNull()
  expect(screen.getByText(saved.data!.limitations![0])).toBeTruthy()
  expect(screen.getByRole('img', { name: '年化波动率' })).toBeTruthy()
  expect(container.querySelectorAll('circle')).toHaveLength(2)
  expect(container.querySelectorAll('polyline')).toHaveLength(2)
  expect(container.querySelector('circle title')?.textContent).toContain('2026-01-01')
  fireEvent.click(screen.getByRole('button', { name: '计算口径与输入依据' }))
  const evidence = screen.getByRole('dialog')
  expect(within(evidence).getByText('EWMA 半衰期 21 个交易观察')).toBeTruthy()
  const original = within(evidence).getByText('完整计算记录与输入依据').closest('details')!
  original.open = true
  fireEvent(original, new Event('toggle'))
  expect(evidence.querySelector('pre')?.textContent).toContain('22.12345678')
})

it('keeps unavailable EWMA observations empty and retains their limitation', () => {
  const saved: SavedResearchSource = { source_id: 'computed:unavailable', source_type: 'computed_metric', methodology: { metric: 'ewma_volatility' }, data: { status: 'unavailable', current: null, previous: null, change_pp: null, five_session_change_pp: null, history: [], limitations: ['交易观察不足。'] } }
  const { container } = render(<EvidenceFigure source={saved} />)
  expect(screen.getAllByText('未取得')).toHaveLength(4)
  expect(screen.getByText('交易观察不足。')).toBeTruthy()
  expect(container.querySelector('svg')).toBeNull()
  expect(container.querySelector('.research-quant-metrics')?.textContent).not.toContain('0.00')
})

it('keeps a common-sample comparison focused on the chart and opens precise supporting records on demand', () => {
  const method = '共同实际观察日；不填充缺失值；不年化。'
  const saved: SavedResearchSource = { source_id: 'computed:comparison', source_type: 'computed_metric', title: '已登记标的共同观察区间比较', methodology: method,
    data: { sample_start: '2026-01-01', sample_end: '2026-09-25', observations: 182, currency: 'USD', method,
      rows: [{ instrument_id: 'xlk', return_pct: 12.345678, max_drawdown_pct: -6.123456, correlation_to_target: 1, excess_return_pp: null },
        { instrument_id: 'spy', return_pct: -2.345678, max_drawdown_pct: -8.123456, correlation_to_target: 0.87654321, excess_return_pp: 0.123456 }],
      limitations: ['仅覆盖已登记标的，不代表全市场排名。', '历史相关性不代表危机对冲。'] } }
  const { container } = render(<EvidenceFigure source={saved} />)
  expect(screen.getByRole('img', { name: '共同样本区间收益对比' })).toBeTruthy()
  expect(screen.getByText('共同样本 2026-01-01 至 2026-09-25 · 实际观察数 182 · USD')).toBeTruthy()
  expect(screen.getByText('+12.35%')).toBeTruthy()
  expect(screen.getByText('-2.35%')).toBeTruthy()
  expect(screen.queryByRole('table')).toBeNull()
  expect(screen.queryByText(method)).toBeNull()
  expect(screen.queryByText(saved.data!.limitations![0])).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '计算口径与输入依据' }))
  const evidence = screen.getByRole('dialog')
  expect(within(evidence).getAllByText(method)).toHaveLength(1)
  expect(within(evidence).getByText('-6.12%')).toBeTruthy()
  expect(within(evidence).getByText('0.877')).toBeTruthy()
  expect(within(evidence).getByText('0.12 个百分点')).toBeTruthy()
  expect(within(evidence).getByText(saved.data!.limitations![0])).toBeTruthy()
  const original = within(evidence).getByText('完整计算记录与输入依据').closest('details')!
  original.open = true
  fireEvent(original, new Event('toggle'))
  expect(evidence.querySelector('pre')?.textContent).toContain('0.87654321')
  expect(evidence.querySelector('pre')?.textContent).toContain('12.345678')
  expect(container.querySelectorAll('.research-return-track i')).toHaveLength(2)
})

it('keeps unavailable comparison rows and their sample gap visible without drawing zero for missing returns', () => {
  const saved: SavedResearchSource = { source_id: 'computed:partial', source_type: 'computed_metric',
    data: { sample_start: '2026-01-01', sample_end: '2026-09-25', observations: 182,
      rows: [{ instrument_id: 'missing', return_pct: null, max_drawdown_pct: null }, { instrument_id: 'known', return_pct: 0, max_drawdown_pct: 0 }],
      limitations: ['一个标的尚未取得共同样本。'] } }
  const { container, rerender } = render(<EvidenceFigure source={saved} />)
  expect(screen.getByText('missing')).toBeTruthy()
  expect(screen.getByText('未取得')).toBeTruthy()
  expect(screen.getByText('0.00%')).toBeTruthy()
  expect(container.querySelectorAll('.research-return-track i')).toHaveLength(1)
  expect(screen.getByText('一个标的尚未取得共同样本。')).toBeTruthy()
  rerender(<EvidenceFigure source={{ ...saved, data: { sample_start: null, sample_end: null, observations: 0, rows: [], limitations: ['没有足够的共同实际观察日。'] } }} />)
  expect(screen.getByText('本次计算未取得可用结果。')).toBeTruthy()
  expect(screen.getByText('没有足够的共同实际观察日。')).toBeTruthy()
  expect(screen.queryByRole('img')).toBeNull()
})

it('presents six readable metrics while retaining every original field and exact value in evidence', () => {
  const saved = source()
  saved.data!.metrics = { common_observations: 273, full_n_returns: 272, full_beta_ols: 0.237987654,
    full_correlation: 0.456789012, recent_correlation: 0.678901234, full_up_capture: 0.321098765,
    full_down_capture: 0.198765432, sample_start: '2025-08-01', sample_end: '2026-09-30', internal_unknown_field: 13.7654321 }
  const { container } = render(<ResearchQuantFigure source={saved} />)
  expect(container.querySelectorAll('.research-quant-metrics dd')).toHaveLength(6)
  expect(container.textContent).toContain('全样本 · Beta（OLS）')
  expect(container.textContent).toContain('2025-08-01')
  expect(container.textContent).not.toContain('internal_unknown_field')
  expect(container.textContent).not.toContain('full_beta_ols')
  fireEvent.click(screen.getByRole('button', { name: '查看全部指标和计算依据' }))
  const dialog = screen.getByRole('dialog')
  expect(dialog.textContent).toContain('internal_unknown_field')
  expect(dialog.textContent).toContain('13.7654321')
  expect(dialog.textContent).toContain('0.237987654')
  expect(dialog.querySelectorAll('tbody tr')).toHaveLength(10)
})
