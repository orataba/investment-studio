// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import InstrumentMonitoringMetrics from './InstrumentMonitoringMetrics'
const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ fetchJson: request }))
vi.mock('./AccountBoundary', () => ({ useCanWriteTeam: () => true }))
const asset = { instrument_id: 'a', name: 'Asset A', instrument_type: 'equity', attributes: {}, as_of_date: '2026-10-14', drawdown_limit: 10, period_limits: { day: 1, week: 3, month: 5, quarter: 8 }, price_risk_calibration: { severities: { drawdown: 'severe' } }, rule_version: 'risk-rule:a:2' }
const metric = { metric_id: 'current_drawdown', label: '当前回撤', unit: 'percent', value: -4, status: 'ready', unavailable_reason: null, as_of: '2026-10-14', observation_count: 3, window_start_date: '2026-10-01', window_end_date: '2026-10-14', definition: { sign: 'negative_percent' }, series: [{ date: '2026-10-01', value: -2, status: 'ready' }, { date: '2026-10-10', value: null, status: 'unavailable' }, { date: '2026-10-14', value: -4, status: 'ready' }] }
const snapshot = { instrument_id: 'a', metric_snapshot_id: 'snapshot-a', definition_version: 'v1', input_snapshot_version: 'input-a', as_of: '2026-10-14', calculated_at: '2026-10-14T08:00:00Z', currency: 'USD', return_kind: 'total_return', frequency: { resolved_frequency: 'daily' }, status: 'partial', metrics: [metric, { ...metric, metric_id: 'holding_period_max_drawdown', status: 'not_applicable', value: null, series: [], unavailable_reason: '单资产观察列表没有实际持有路径；实际持有期风险属于组合。' }] }
beforeEach(() => { window.history.replaceState({}, '', '/?lang=zh-Hans'); request.mockImplementation(async path => path.includes('/monitoring') ? snapshot : { instruments: [asset], cases: [] }) })
afterEach(() => { cleanup(); vi.resetAllMocks() })
it('reads backend metric identity, preserves percent units and breaks chart paths at gaps', async () => {
  const { container } = render(<LanguageProvider><InstrumentMonitoringMetrics instrumentId="a" /></LanguageProvider>)
  expect(await screen.findAllByText('-4.00%')).toBeTruthy()
  expect(screen.getByText('snapshot-a')).toBeTruthy()
  expect(screen.getByText('不适用')).toBeTruthy()
  expect(screen.getByText(/单资产观察列表没有实际持有路径/)).toBeTruthy()
  expect(container.querySelector('svg path')?.getAttribute('d')?.match(/M/g)).toHaveLength(2)
  expect(container.querySelector('svg path')?.getAttribute('d')).not.toContain('L')
  expect(screen.queryByText('0.00%')).toBeNull()
  expect(request.mock.calls.every(([, init]) => !init?.method)).toBe(true)
})
it.each([
  ['zh-Hans', '实际样本内部或尾部存在缺失／不可用收益区间，暂不计算 VaR / ES。'],
  ['en', 'Missing or unavailable intervals within or after the actual sample prevent VaR / ES.'],
])('translates unavailable tail reasons in metric cards and retained observations (%s)', async (language, explanation) => {
  window.history.replaceState({}, '', `/?lang=${language}`)
  const unavailable = { ...metric, metric_id: 'var', value: null, status: 'unavailable', unavailable_reason: 'missing_daily_return_intervals', series: [metric.series[0], { ...metric.series[1], unavailable_reason: 'missing_daily_return_intervals' }] }
  request.mockImplementation(async path => path.includes('/monitoring') ? { ...snapshot, metrics: [unavailable] } : { instruments: [], cases: [] })
  const { container } = render(<LanguageProvider><InstrumentMonitoringMetrics instrumentId="a" /></LanguageProvider>)
  expect(await screen.findByText(explanation)).toBeTruthy()
  expect(container.querySelector('tbody')?.textContent).toContain(explanation)
  expect(container.textContent).not.toContain('missing_daily_return_intervals')
})
it('saves explicit disabled periods and severe rules without resetting other original settings', async () => {
  render(<LanguageProvider><InstrumentMonitoringMetrics instrumentId="a" /></LanguageProvider>)
  await screen.findAllByText('-4.00%')
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Asset A 日跌幅复核线' }), { target: { value: '' } })
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Asset A 周跌幅复核线' }), { target: { value: '2' } })
  fireEvent.change(screen.getByLabelText('修改原因'), { target: { value: '按授权风险预算调整' } })
  fireEvent.click(screen.getByRole('button', { name: '保存指标规则' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/api/risk/rules/a', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ expected_rule_version: 'risk-rule:a:2', severities: { drawdown: 'severe' }, change_reason: '按授权风险预算调整', drawdown_limit: 10, period_limits: { day: null, week: 2, month: 5, quarter: 8 } }) })))
  expect(request.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
})
it('keeps a failed monitoring read distinct from zero risk while preserving the rule editor', async () => {
  request.mockImplementation(async path => { if (path.includes('/monitoring')) throw new Error('监测读取失败'); return { instruments: [asset], cases: [] } })
  render(<LanguageProvider><InstrumentMonitoringMetrics instrumentId="a" /></LanguageProvider>)
  expect(await screen.findByText('监测读取失败')).toBeTruthy()
  expect(screen.getByRole('spinbutton', { name: 'Asset A 高点回撤复核线' })).toBeTruthy()
  expect(screen.queryByText('-4.00%')).toBeNull()
})
it('previews historical trigger differences without saving, and clears the comparison after the proposed threshold changes', async () => {
  request.mockImplementation(async path => path.endsWith('/preview') ? { metric_snapshot_id: 'snapshot-a', rule_version: 'risk-rule:a:2', as_of: '2026-10-14', status: 'ready', note: '本次留存路径', rules: [{ key: 'day', previous_limit_pct: 1, proposed_limit_pct: 2, evaluable_observations: 20, unavailable_observations: 1, previous_trigger_count: 5, proposed_trigger_count: 2, new_trigger_dates: [], removed_trigger_dates: ['2026-10-01'] }] } : path.includes('/monitoring') ? snapshot : { instruments: [asset], cases: [] })
  render(<LanguageProvider><InstrumentMonitoringMetrics instrumentId="a" /></LanguageProvider>)
  await screen.findAllByText('-4.00%')
  fireEvent.click(screen.getByRole('button', { name: '预览历史命中差异' }))
  expect(await screen.findByText('本次留存路径')).toBeTruthy()
  expect(request.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Asset A 日跌幅复核线' }), { target: { value: '2' } })
  expect(screen.queryByText('本次留存路径')).toBeNull()
})
