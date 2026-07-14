import { describe, expect, it } from 'vitest'

import transactionsSource from './pages/TransactionsPage.tsx?raw'

describe('transaction evidence UI contract', () => {
  it('keeps source-reported consideration authoritative', () => {
    expect(transactionsSource).toContain("consideration_basis: 'source_reported'")
    expect(transactionsSource).toContain("form.consideration_basis === 'exact_quantity_price'")
    expect(transactionsSource).toContain('exactDecimalMultiply(form.quantity.trim(), form.price.trim())')
    expect(transactionsSource).not.toContain('exactDecimalDivideHalfEven')
    expect(transactionsSource).not.toContain('autoGrossAmountFromTrade')
  })

  it('does not turn shared FX reference data into actual counter cash', () => {
    expect(transactionsSource).toContain('counter_amount: actualCounterAmount')
    expect(transactionsSource).toContain('quoted_fx_rate: resolvedQuotedFxRate || null')
    expect(transactionsSource).toContain("placeholder={sharedFxRate?.rate || 'Optional'}")
    expect(transactionsSource).not.toContain('computedCounterAmount')
    expect(transactionsSource).not.toContain('form.fx_rate')
  })
})
