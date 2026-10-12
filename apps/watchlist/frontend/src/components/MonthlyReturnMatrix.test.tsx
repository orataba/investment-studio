// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import MonthlyReturnMatrix from './MonthlyReturnMatrix'
import { buildMonthlyReturnMatrix } from '../lib/calendarReturns'
afterEach(cleanup)

it('distinguishes a real zero, a missing observation, an unfinished month and future months', () => {
  const rows = buildMonthlyReturnMatrix([
    { date: '2025-12-31', value: 100 }, { date: '2026-01-30', value: 100 },
    { date: '2026-08-31', value: 120 }, { date: '2026-09-30', value: 120 }, { date: '2026-10-09', value: 126 },
  ])
  render(<LanguageProvider><MonthlyReturnMatrix rows={rows} today="2026-10-12" /></LanguageProvider>)
  expect(screen.getByRole('cell', { name: /^2026-01:/ }).textContent).toBe('0.00%')
  expect(screen.getByRole('cell', { name: /^2026-02:/ }).textContent).toBe('—')
  const current = screen.getByRole('cell', { name: /^2026-10:/ })
  expect(current.textContent).toContain('5.00%')
  expect(current.textContent).toContain('MTD')
  expect(current.textContent).toContain('2026-10-09')
  expect(current.title).toBe('2026-09-30 → 2026-10-09')
  expect(screen.getByRole('cell', { name: /^2026-09:/ }).textContent).not.toContain('MTD')
  expect(screen.getByRole('cell', { name: /^2026-11:/ }).textContent).toMatch(/—.*(未到期|Not due)/)
})

it('keeps a current-month gap unavailable instead of labelling it as zero MTD', () => {
  const rows = buildMonthlyReturnMatrix([{ date: '2026-08-31', value: 100 }, { date: '2026-09-30', value: 110 }])
  render(<LanguageProvider><MonthlyReturnMatrix rows={rows} today="2026-10-12" /></LanguageProvider>)
  expect(screen.getByRole('cell', { name: /^2026-10:/ }).textContent).toBe('—MTD')
})
