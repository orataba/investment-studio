import { describe, expect, it } from 'vitest'

import { formatDate, formatDateTime, formatPercent } from './format'

describe('formatDate', () => {
  it('normalizes compact provider dates', () => {
    expect(formatDate('20150123')).toBe('2015-01-23')
  })

  it('keeps ISO dates on the canonical day', () => {
    expect(formatDate('2026-08-21T13:01:37Z')).toBe('2026-08-21')
  })
})

it('labels timestamp timezone independently of the browser timezone', () => {
  expect(formatDateTime('2026-10-15T14:35:00+08:00')).toContain('06:35')
  expect(formatDateTime('2026-10-15T14:35:00+08:00')).toContain('UTC')
})
it('does not display negative zero after rounding a percentage', () => {
  expect(formatPercent(-0.0001)).toBe('0.00%')
  expect(formatPercent(-0.01)).toBe('-0.01%')
})
