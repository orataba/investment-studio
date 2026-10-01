// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import FreshnessNote, { observationAge } from './FreshnessNote'

afterEach(() => { cleanup(); vi.useRealTimers() })
it('measures calendar age from the displayed reference day, across a leap day', () => {
  expect(observationAge('2026-06-15', '2026-10-01')).toBe(108)
  expect(observationAge('2024-02-28', '2024-03-01')).toBe(2)
  expect(observationAge(null, '2026-10-01')).toBeNull()
})
it('refreshes a cached age at UTC midnight without refreshing data or claiming data became current', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-01T23:59:59Z'))
  window.history.replaceState({}, '', '?lang=en')
  render(<LanguageProvider enableDomTranslation={false}><p><FreshnessNote observationDate="2026-06-15" reason="Latest observation is 90 calendar days old; the daily freshness allowance is 4 days." /></p></LanguageProvider>)
  expect(screen.getByText(/Latest observation is 108 calendar days old/).textContent).toContain('as of 2026-10-01 UTC')
  act(() => vi.advanceTimersByTime(1000))
  expect(screen.getByText(/Latest observation is 109 calendar days old/).textContent).toContain('as of 2026-10-02 UTC')
})
