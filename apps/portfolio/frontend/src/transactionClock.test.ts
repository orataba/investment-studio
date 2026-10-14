import { afterEach, expect, it, vi } from 'vitest'
import { transactionTodayIso } from './lib/transactionClock'
import { buildInitialDerivativeContractDraft } from './lib/derivativeContractDraft'

afterEach(() => { vi.unstubAllEnvs(); vi.useRealTimers() })

it.each(['UTC', 'Asia/Shanghai', 'America/New_York'])('uses the declared transaction date in a %s browser environment', timezone => {
  vi.stubEnv('TZ', timezone)
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-13T22:14:00Z'))
  expect(transactionTodayIso()).toBe('2026-10-14')
  expect(buildInitialDerivativeContractDraft('option').option_expiry_date).toBe('2026-10-14')
  const fcn = buildInitialDerivativeContractDraft('fcn')
  expect([fcn.fcn_issue_date, fcn.fcn_maturity_date]).toEqual(['2026-10-14', '2026-10-14'])
})

it.each([
  ['2026-10-13T15:59:59Z', 'Asia/Shanghai', '2026-10-13'],
  ['2026-10-13T16:00:00Z', 'Asia/Shanghai', '2026-10-14'],
  ['2026-07-02T03:59:59Z', 'America/New_York', '2026-07-01'],
  ['2026-07-02T04:00:00Z', 'America/New_York', '2026-07-02'],
  ['2026-12-02T04:59:59Z', 'America/New_York', '2026-12-01'],
  ['2026-12-02T05:00:00Z', 'America/New_York', '2026-12-02'],
])('respects midnight at %s in %s', (instant, timezone, date) => {
  expect(transactionTodayIso(new Date(instant), timezone)).toBe(date)
})
