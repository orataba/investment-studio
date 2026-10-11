// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import InstrumentRiskMonitoringPanel from './InstrumentRiskMonitoringPanel'
import type { InstrumentTailRisk } from '../lib/instrumentTailRiskApi'

const request = vi.hoisted(() => vi.fn())
vi.mock('../lib/instrumentTailRiskApi', () => ({ getInstrumentTailRisk: request }))
vi.mock('./InstrumentRiskPanel', () => ({ default: ({ instrumentId }: { instrumentId: string }) => <div>风险事项 {instrumentId}</div> }))
const data: InstrumentTailRisk = {
  instrument_id: 'a', status: 'ready', reason: null, confidence: .95, lookback_days: 1095,
  currency: 'HKD', return_kind: 'total_return', var: .0234, expected_shortfall: .0367,
  observation_count: 250, tail_effective_observations: 12.5, tail_observation_count: 13,
  tail_max_observation_weight: .08, window_start_date: '2023-10-08', window_end_date: '2026-10-07',
  first_observation_start_date: '2025-10-01', last_observation_end_date: '2026-10-07',
  excluded_interval_count: 0, expected_observation_count: 750, uncovered_leading_count: 500,
  missing_internal_count: 0, uncovered_trailing_count: 0, history_coverage_status: 'partial',
  latest_expected_date: '2026-10-07', latest_observation_date: '2026-10-07', source_stale: false,
}
function panel(id = 'a') { return <LanguageProvider><InstrumentRiskMonitoringPanel instrumentId={id} /></LanguageProvider> }
beforeEach(() => { window.history.replaceState({}, '', '/?lang=zh-Hans'); request.mockResolvedValue(data) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('shows loss percentages, actual sample, tail precision and the same risk cases', async () => {
  render(panel())
  expect(await screen.findByText('2.34%')).toBeTruthy()
  expect(screen.getByText('3.67%')).toBeTruthy()
  expect(screen.getByText('250 / 12.50')).toBeTruthy()
  expect(screen.getByText(/2025-10-01 → 2026-10-07/)).toBeTruthy()
  expect(screen.getByText(/样本未覆盖完整回看区间/)).toBeTruthy()
  expect(screen.getByText('风险事项 a')).toBeTruthy()
  expect(request).toHaveBeenCalledWith('a', .95, 1095, expect.any(AbortSignal))
})

it('does not show an unavailable low-frequency estimate as zero risk', async () => {
  request.mockResolvedValue({ ...data, status: 'unavailable', reason: 'non_daily_source', var: null, expected_shortfall: null, observation_count: 0, tail_effective_observations: 0 })
  render(panel())
  expect(await screen.findByText(/该资产没有已确认的日频收益/)).toBeTruthy()
  expect(screen.queryByText('0.00%')).toBeNull()
  expect(screen.queryByText('2.34%')).toBeNull()
})

it('discards old confidence and old-asset responses and immediately clears their numbers', async () => {
  const resolvers: Array<(value: InstrumentTailRisk) => void> = []
  request.mockImplementation(() => new Promise<InstrumentTailRisk>(resolve => resolvers.push(resolve)))
  const view = render(panel())
  await act(async () => resolvers[0](data))
  fireEvent.change(screen.getByLabelText('置信度'), { target: { value: '.99' } })
  expect(screen.queryByText('2.34%')).toBeNull()
  view.rerender(panel('b'))
  await act(async () => resolvers[1]({ ...data, var: .99 }))
  expect(screen.queryByText('99.00%')).toBeNull()
  await act(async () => resolvers[2]({ ...data, instrument_id: 'b', var: .05 }))
  expect(screen.getByText('5.00%')).toBeTruthy()
  expect(screen.getByText('风险事项 b')).toBeTruthy()
  expect(request.mock.calls[0][3].aborted).toBe(true)
})

it('surfaces stale dates and failed recalculations even when numbers exist', async () => {
  request.mockResolvedValue({ ...data, source_stale: true, freshness: { data_freshness_status: 'stale' } })
  render(panel())
  expect(await screen.findByText(/有效收益尚未覆盖应到日期/)).toBeTruthy()
  expect(screen.getByText(/收益计算尚未更新至最新数据或设置/)).toBeTruthy()
})

it('offers retry after request failure without reusing the prior result', async () => {
  request.mockRejectedValueOnce(new Error('网络中断'))
  render(panel())
  expect(await screen.findByRole('alert')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '重试' }))
  await waitFor(() => expect(screen.getByText('2.34%')).toBeTruthy())
  expect(screen.queryByRole('alert')).toBeNull()
})

it('clears a prior error when returning to an earlier parameter choice', async () => {
  request.mockRejectedValueOnce(new Error('旧错误'))
  render(panel())
  await screen.findByRole('alert')
  fireEvent.change(screen.getByLabelText('置信度'), { target: { value: '.99' } })
  await screen.findByText('2.34%')
  fireEvent.change(screen.getByLabelText('置信度'), { target: { value: '.95' } })
  await screen.findByText('2.34%')
  expect(screen.queryByRole('alert')).toBeNull()
})

it('shows unavailable tail estimates and exact missing intervals without reusing numbers', async () => {
  request.mockResolvedValue({ ...data, status: 'unavailable', reason: 'missing_daily_return_intervals', var: null,
    expected_shortfall: null, missing_intervals: [{ start_date: '2026-10-06', end_date: '2026-10-07' }] })
  render(panel())
  expect(await screen.findByText(/实际样本内部或尾部存在缺失/)).toBeTruthy()
  expect(screen.getByText('2026-10-06 → 2026-10-07')).toBeTruthy()
  expect(screen.queryByText('2.34%')).toBeNull()
})
