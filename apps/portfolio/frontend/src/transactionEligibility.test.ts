import { describe, expect, it } from 'vitest'

import { supportsTransactionInstrumentType } from './lib/transactionEligibility'

describe('transaction instrument eligibility', () => {
  it('treats ETFs as first-class position assets', () => {
    expect(supportsTransactionInstrumentType('buy', 'etf')).toBe(true)
    expect(supportsTransactionInstrumentType('sell', 'ETF')).toBe(true)
    expect(supportsTransactionInstrumentType('opening_balance', 'etf')).toBe(true)
  })

  it('supports ETF distributions and reinvestment without applying bond-only events', () => {
    expect(supportsTransactionInstrumentType('dividend', 'etf')).toBe(true)
    expect(supportsTransactionInstrumentType('dividend_reinvestment', 'etf')).toBe(true)
    expect(supportsTransactionInstrumentType('return_of_capital', 'etf')).toBe(true)
    expect(supportsTransactionInstrumentType('coupon', 'etf')).toBe(false)
    expect(supportsTransactionInstrumentType('maturity_redemption', 'etf')).toBe(false)
  })
})
