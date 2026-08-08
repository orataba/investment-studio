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

  it('supports event-valued FCN and option facts', () => {
    expect(supportsTransactionInstrumentType('buy', 'fcn')).toBe(true)
    expect(supportsTransactionInstrumentType('coupon', 'fcn')).toBe(true)
    expect(supportsTransactionInstrumentType('maturity_redemption', 'fcn')).toBe(true)
    expect(supportsTransactionInstrumentType('option_write', 'option')).toBe(true)
    expect(supportsTransactionInstrumentType('option_buy_to_close', 'option')).toBe(true)
    expect(supportsTransactionInstrumentType('option_write', 'equity')).toBe(false)
  })
})
