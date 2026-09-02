import { describe, expect, it } from 'vitest'

import {
  requiredDerivativeContractType,
  supportsTransactionAssetType,
} from './lib/transactionEligibility'

describe('transaction instrument eligibility', () => {
  it('treats ETFs as first-class position assets', () => {
    expect(supportsTransactionAssetType('buy', 'etf')).toBe(true)
    expect(supportsTransactionAssetType('sell', 'ETF')).toBe(true)
    expect(supportsTransactionAssetType('opening_balance', 'etf')).toBe(true)
  })

  it('supports ETF distributions and reinvestment without derivative-only events', () => {
    expect(supportsTransactionAssetType('dividend', 'etf')).toBe(true)
    expect(supportsTransactionAssetType('dividend_reinvestment', 'etf')).toBe(true)
    expect(supportsTransactionAssetType('return_of_capital', 'etf')).toBe(true)
    expect(supportsTransactionAssetType('coupon', 'etf')).toBe(false)
    expect(supportsTransactionAssetType('maturity_redemption', 'etf')).toBe(false)
  })

  it('supports event-valued FCN and option facts', () => {
    expect(supportsTransactionAssetType('buy', 'fcn')).toBe(true)
    expect(supportsTransactionAssetType('coupon', 'fcn')).toBe(true)
    expect(supportsTransactionAssetType('maturity_redemption', 'fcn')).toBe(true)
    expect(supportsTransactionAssetType('option_write', 'option')).toBe(true)
    expect(supportsTransactionAssetType('option_buy_to_close', 'option')).toBe(true)
    expect(supportsTransactionAssetType('option_write', 'equity')).toBe(false)
  })

  it('maps each lifecycle event to its Portfolio-local contract type', () => {
    expect(requiredDerivativeContractType('lifecycle_event', 'fcn_knock_in')).toBe('fcn')
    expect(requiredDerivativeContractType('lifecycle_event', 'fcn_knock_out')).toBe('fcn')
    expect(requiredDerivativeContractType('lifecycle_event', 'fcn_maturity')).toBe('fcn')
    expect(requiredDerivativeContractType('lifecycle_event', 'option_long_expiry')).toBe('option')
    expect(requiredDerivativeContractType('lifecycle_event', 'option_long_exercise')).toBe('option')
    expect(requiredDerivativeContractType('lifecycle_event', 'option_writer_cash_settlement')).toBe('option')
    expect(requiredDerivativeContractType('lifecycle_event', 'option_writer_assignment')).toBe('option')
    expect(requiredDerivativeContractType('lifecycle_event')).toBeNull()
  })

  it('rejects lifecycle events against the wrong derivative contract type', () => {
    expect(supportsTransactionAssetType('lifecycle_event', 'fcn', 'fcn_knock_in')).toBe(true)
    expect(supportsTransactionAssetType('lifecycle_event', 'option', 'fcn_knock_in')).toBe(false)
    expect(supportsTransactionAssetType('lifecycle_event', 'option', 'option_writer_cash_settlement')).toBe(true)
    expect(supportsTransactionAssetType('lifecycle_event', 'option', 'option_writer_assignment')).toBe(true)
    expect(supportsTransactionAssetType('lifecycle_event', 'fcn', 'option_writer_cash_settlement')).toBe(false)
    expect(supportsTransactionAssetType('lifecycle_event', 'option')).toBe(false)
  })

  it('matches long option and FCN maturity results to the correct contract type', () => {
    expect(
      supportsTransactionAssetType('maturity_redemption', 'option', 'option_long_expiry'),
    ).toBe(true)
    expect(
      supportsTransactionAssetType('maturity_redemption', 'option', 'option_long_exercise'),
    ).toBe(true)
    expect(
      supportsTransactionAssetType('maturity_redemption', 'fcn', 'option_long_expiry'),
    ).toBe(false)
    expect(
      supportsTransactionAssetType('maturity_redemption', 'fcn', 'fcn_knock_out'),
    ).toBe(true)
    expect(
      supportsTransactionAssetType('maturity_redemption', 'option', 'fcn_knock_out'),
    ).toBe(false)
  })
})
