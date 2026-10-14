// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResearchCurrentNumbers from './ResearchCurrentNumbers'
import type { ResearchApplicability } from '../lib/researchDossierApi'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
const read = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', async original => ({ ...await original<typeof import('../lib/api')>(), fetchJson: read }))
vi.mock('./EstimateHistoryPanel', () => ({ default: () => <p>Estimate history</p> }))
vi.mock('./ResearchFundamentalNumbers', () => ({ default: () => <p>Fundamental history</p> }))
afterEach(cleanup)
beforeEach(() => vi.resetAllMocks())
const chart = { currency: 'USD', price_series: { quote_basis: 'unadjusted_close', currency: 'USD', points: [{ date: '2026-10-01', value: 100, segment_id: 'one' }, { date: '2026-10-02', value: 101, segment_id: 'one' }, { date: '2026-10-08', value: 103, segment_id: 'two' }, { date: '2026-10-09', value: 102, segment_id: 'two' }] } }

it('reads canonical data without running research, preserves segment breaks and exposes actual history', async () => {
  read.mockImplementation(async (path: string) => path.endsWith('/chart') ? chart : { key_stats: [{ label: 'MTD Return', value: '2.00%' }], freshness: { staleness_reason: 'Latest complete session is October 9.' } })
  const { container } = render(<LanguageProvider enableDomTranslation={false}><ResearchCurrentNumbers instrumentId="stock" /></LanguageProvider>)
  await screen.findByText('2.00%')
  expect(screen.getByText('Latest complete session is October 9.')).toBeTruthy()
  expect(container.querySelectorAll('svg path')).toHaveLength(2)
  expect(read.mock.calls.every(([, options]) => !options.method)).toBe(true)
  expect(read.mock.calls.map(([path]) => path)).toEqual(['/api/instruments/stock/summary', '/api/instruments/stock/chart'])
  fireEvent.click(screen.getByRole('button', { name: '价格历史数据' }))
  expect(screen.getByRole('cell', { name: '103' })).toBeTruthy()
  expect(screen.getByRole('cell', { name: '2026-10-08' })).toBeTruthy()
})

it('does not show the previous instrument data while the new one loads and reports partial failures', async () => {
  read.mockImplementation(async (path: string) => path.endsWith('/chart') ? chart : { key_stats: [{ label: 'Old metric', value: 'old value' }] })
  const { rerender } = render(<LanguageProvider enableDomTranslation={false}><ResearchCurrentNumbers instrumentId="first" /></LanguageProvider>)
  await screen.findByText('old value')
  let resolve!: (value: unknown) => void
  read.mockImplementation((path: string) => path.endsWith('/summary') ? Promise.reject(new Error('Summary unavailable')) : new Promise(done => { resolve = done }))
  rerender(<LanguageProvider enableDomTranslation={false}><ResearchCurrentNumbers instrumentId="second" /></LanguageProvider>)
  expect(screen.queryByText('old value')).toBeNull()
  expect(screen.queryByRole('img')).toBeNull()
  await act(async () => resolve(chart))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Summary unavailable'))
  expect(screen.getByRole('img')).toBeTruthy()
})


it('defaults to the canonical return path and keeps raw prices separate', async () => {
  read.mockImplementation(async (path: string) => path.endsWith('/chart') ? { ...chart, return_series: { currency: 'USD', metadata: { label: 'Adjusted close' }, wealth_points: [{ date: '2026-10-01', value: 1, segment_id: 'first' }, { date: '2026-10-02', value: 1.1, segment_id: 'first' }, { date: '2026-10-08', value: 1, segment_id: 'second' }, { date: '2026-10-09', value: 1.02, segment_id: 'second' }] } } : {})
  const { container } = render(<LanguageProvider enableDomTranslation={false}><ResearchCurrentNumbers instrumentId="stock" /></LanguageProvider>)
  await screen.findByRole('img', { name: /收益历史/ })
  expect(container.querySelectorAll('svg path')).toHaveLength(2)
  fireEvent.click(screen.getByRole('button', { name: '原始价格' }))
  expect(screen.getByRole('img', { name: /价格历史/ })).toBeTruthy()
  expect(screen.getByText(/原始价格可能包含拆股/)).toBeTruthy()
})

it('hides the company-estimate entry only for explicitly disclosed inapplicable exposures', async () => {
  read.mockImplementation(async (path: string) => path.endsWith('/chart') ? chart : {})
  const applicability: ResearchApplicability = { schema_version: 1, instrument_type: 'etf',
    underlying: { asset_class: 'Commodities', strategy: null, status: 'documented' }, mechanisms: {},
    monitoring: { generic_news: true, numeric_change_requires_research: false, material_channels: [] }, section_order: ['quantitative'] }
  const view = (value?: ResearchApplicability) => <LanguageProvider enableDomTranslation={false}><ResearchCurrentNumbers instrumentId="registered-etf" applicability={value} /></LanguageProvider>
  const { rerender } = render(view(applicability))
  await screen.findByRole('img')
  expect(screen.queryByRole('button', { name: '分析师预期变化与历史' })).toBeNull()
  expect(screen.getByRole('button', { name: '基本面与成份历史' })).toBeTruthy()
  rerender(view({ ...applicability, underlying: { ...applicability.underlying, asset_class: 'Equity' } }))
  expect(screen.getByRole('button', { name: '分析师预期变化与历史' })).toBeTruthy()
  rerender(view({ ...applicability, underlying: { ...applicability.underlying, status: 'unknown' } }))
  expect(screen.getByRole('button', { name: '分析师预期变化与历史' })).toBeTruthy()
  rerender(view(undefined))
  expect(screen.getByRole('button', { name: '分析师预期变化与历史' })).toBeTruthy()
})
