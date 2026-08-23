import { describe, expect, it } from 'vitest'

import { formatDate } from './format'

describe('formatDate', () => {
  it('normalizes compact provider dates', () => {
    expect(formatDate('20150123')).toBe('2015-01-23')
  })

  it('keeps ISO dates on the canonical day', () => {
    expect(formatDate('2026-08-21T13:01:37Z')).toBe('2026-08-21')
  })
})
