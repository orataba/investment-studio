import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it } from 'vitest'
import { LanguageProvider, useLanguage } from '../../../../../packages/ui/src/i18n'
import { buildMonthlyReturnMatrixRows, type MonthlyBucket } from '../lib/monthlyReturns'
import MonthlyReturnMatrix from './MonthlyReturnMatrix'

function bucket(month: string, end: string, value: number | null, reason: string | null = null): MonthlyBucket {
  return { bucket_key: month, start_date: `${month}-01`, end_date: end, cumulative_twr: value, coverage_state: value == null ? 'partial' : 'complete', observation_count: 8, start_nav: 100, end_nav: 104, net_external_inflow: 0, absolute_change: 4, delta: 4, unavailable_reason: reason }
}

it('distinguishes completed, MTD, future and missing history while exposing the actual period on focus', () => {
  const rows = buildMonthlyReturnMatrixRows([bucket('2026-01', '2026-01-31', 0), bucket('2026-09', '2026-09-30', null, 'Return coverage is not continuous.'), bucket('2026-10', '2026-10-12', -0.04)])
  render(<LanguageProvider enableDomTranslation={false}><MonthlyReturnMatrix rows={rows} asOfDate="2026-10-12" /></LanguageProvider>)
  const table = screen.getByRole('table', { name: 'Monthly return matrix' })
  const jan = within(table).getByRole('cell', { name: /2026-01 ·/ })
  expect(jan).toHaveTextContent('0.00%')
  expect(jan).not.toHaveTextContent('MTD')
  const oct = within(table).getByRole('cell', { name: /2026-10 ·/ })
  expect(oct).toHaveTextContent('-4.00%MTD · 10-12')
  expect(oct).toHaveClass('negative-cell')
  expect(within(table).getByRole('cell', { name: /2026-11 ·/ })).toHaveTextContent('Not due')
  expect(within(table).getByRole('cell', { name: /2026-02 ·/ })).toHaveTextContent('No history')
  const sep = within(table).getByRole('cell', { name: /2026-09 ·/ })
  expect(sep).toHaveTextContent('—Incomplete')
  expect(sep).not.toHaveClass('neutral-cell')
  fireEvent.focus(sep)
  expect(screen.getByText(/2026-09-01 → 2026-09-30 · — · 8 observations/)).toHaveTextContent('Return coverage is not continuous.')
})

it('treats the final day of a calendar month as completed, without changing the supplied value', () => {
  render(<LanguageProvider enableDomTranslation={false}><MonthlyReturnMatrix rows={buildMonthlyReturnMatrixRows([bucket('2026-10', '2026-10-31', 0.0375)])} asOfDate="2026-10-31" /></LanguageProvider>)
  const cell = screen.getByRole('cell', { name: /2026-10 ·/ })
  expect(cell).toHaveTextContent('+3.75%')
  expect(cell).not.toHaveTextContent('MTD')
})

function LanguageChange() {
  const { setLanguage } = useLanguage()
  return <button onClick={() => setLanguage('zh-Hans')}>中文</button>
}

it('clears focused detail when its portfolio data, cutoff or language changes', () => {
  const original = buildMonthlyReturnMatrixRows([bucket('2026-10', '2026-10-12', -0.04)])
  const replacement = buildMonthlyReturnMatrixRows([bucket('2026-10', '2026-10-12', 0.03)])
  const view = (rows: typeof original, asOfDate: string) => <LanguageProvider enableDomTranslation={false}><LanguageChange /><MonthlyReturnMatrix rows={rows} asOfDate={asOfDate} /></LanguageProvider>
  const { rerender, container } = render(view(original, '2026-10-12'))
  const detail = () => container.querySelector('.monthly-return-detail')!
  const focus = () => fireEvent.focus(screen.getByRole('cell', { name: /2026-10 ·/ }))
  focus()
  expect(detail()).toHaveTextContent('-4.00%')
  rerender(view(replacement, '2026-10-12'))
  expect(detail()).toHaveTextContent('As of 2026-10-12')
  expect(detail()).not.toHaveTextContent('-4.00%')
  focus()
  expect(detail()).toHaveTextContent('+3.00%')
  rerender(view(replacement, '2026-10-13'))
  expect(detail()).toHaveTextContent('As of 2026-10-13')
  focus()
  fireEvent.click(screen.getByRole('button', { name: '中文' }))
  expect(detail()).toHaveTextContent('截至 2026-10-13')
  expect(detail()).not.toHaveTextContent('observations')
})
