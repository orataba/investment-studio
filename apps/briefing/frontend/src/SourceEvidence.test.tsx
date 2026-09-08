// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import { SourceEvidence } from './SourceEvidence'
import type { Source } from './types'

beforeEach(() => { window.history.replaceState(null, '', '/?lang=zh-Hans') })
afterEach(cleanup)
function page(source: Source, onSource = vi.fn()) {
  return render(<LanguageProvider enableDomTranslation={false}><SourceEvidence source={source} onClose={vi.fn()} onSource={onSource} /></LanguageProvider>)
}

it('shows the actual price comparison, adjustment basis and bound endpoint sources', () => {
  const onSource = vi.fn()
  page({ source_id: 'market-row:0', source_type: 'market_row', symbol: 'SPY', label: '标普500 ETF（SPY）',
    start_date: '2026-09-03', end_date: '2026-09-04', start_close: 773.17, end_close: 770.19,
    return_pct: -0.38542623226456296, return_basis: 'split_adjusted_price', source_ids: ['numeric:start', 'numeric:end'],
  }, onSource)
  expect(screen.getByRole('heading', { name: '标普500 ETF（SPY）' })).toBeTruthy()
  expect(screen.getByText('2026-09-03')).toBeTruthy()
  expect(screen.getByText('2026-09-04')).toBeTruthy()
  expect(screen.getByText('773.17')).toBeTruthy()
  expect(screen.getByText('770.19')).toBeTruthy()
  expect(screen.getByText('-0.39%')).toBeTruthy()
  expect(screen.queryByText(/拆股复权价格涨跌，不含分红总回报/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /价格口径:/ }))
  expect(screen.getByRole('tooltip').textContent).toContain('拆股复权价格涨跌，不含分红总回报。')
  fireEvent.click(screen.getByRole('button', { name: '查看截至价格来源' }))
  expect(onSource).toHaveBeenCalledWith('numeric:end')
})

it('presents retained numeric fields and provider without exposing storage metadata', () => {
  const { container } = page({ source_id: 'numeric:price', source_type: 'numeric', symbol: 'SPY', date: '2026-09-03',
    open: 767.9, high: 774.03, low: 767.45, close: 773.17, adjusted_close: 773.17, volume: 43531600,
    source: 'fmp_eod_bulk', ohlc_adjustment: 'split_adjusted', adjusted_close_adjustment: 'split_and_dividend_adjusted',
    observed_at: '2026-09-05T08:35:54.404048+08:00', available_at: '2026-09-05T08:35:54.404048+08:00',
    raw_ref: 'numeric/raw/private-location.gz', _snapshot_key: 'internal-key', unexpected_field: { debug: 'internal detail' },
  })
  expect(screen.getByText('Financial Modeling Prep (FMP)')).toBeTruthy()
  expect(screen.getByText('开盘价')).toBeTruthy()
  expect(screen.getByText('767.9')).toBeTruthy()
  expect(screen.getByText('43,531,600')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /价格口径:/ }))
  expect(screen.getByRole('tooltip').textContent).toContain('复权收盘价包含拆股和分红调整。')
  expect(screen.getAllByText('2026-09-05T08:35:54.404048+08:00')).toHaveLength(2)
  expect(container.querySelector('pre')).toBeNull()
  expect(container.textContent).not.toMatch(/raw_ref|private-location|internal-key|unexpected_field|internal detail/)
})

it.each([['percent', '%'], ['index', '点']])('keeps macro values in their recorded %s unit', (unit, label) => {
  const onSource = vi.fn()
  page({ source_id: 'macro-row:0', source_type: 'macro_row', label: '美国10年国债收益率', symbol: 'US_TREASURY_10Y', date: '2026-09-04', value: 4.78, unit, source_ids: ['numeric:macro'] }, onSource)
  expect(screen.getByText('4.78')).toBeTruthy()
  expect(screen.getByText(label)).toBeTruthy()
  expect(screen.getByText('2026-09-04')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '查看原始数值来源' }))
  expect(onSource).toHaveBeenCalledWith('numeric:macro')
})

it('preserves text and distinct clocks without inventing a time for date-only or unknown dates', () => {
  const { container } = page({ source_id: 'text:version', source_type: 'public_document', title: '央行声明', source_name: '央行',
    published_at: '2026-09-07', observed_at: '2026-09-07T11:15:00+08:00', received_at: '2026-09-08T07:50:00+08:00',
    content_text: '这是来源原文。', content_completeness: 'source_excerpt', url: 'javascript:alert(1)',
  })
  expect(screen.getByText('这是来源原文。').getAttribute('translate')).toBe('no')
  expect(screen.getByText('查看留存原文').closest('details')?.open).toBe(false)
  expect(screen.getByText('2026-09-07')).toBeTruthy()
  expect(screen.getByText('—')).toBeTruthy()
  expect(screen.getByText('2026-09-07T11:15:00+08:00')).toBeTruthy()
  expect(screen.getByText('2026-09-08T07:50:00+08:00')).toBeTruthy()
  expect(screen.queryByText('来源提供的节选，未包含完整正文。')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /来源完整性:/ }))
  expect(screen.getByText('来源提供的节选，未包含完整正文。')).toBeTruthy()
  expect(container.querySelector('a')).toBeNull()
})
