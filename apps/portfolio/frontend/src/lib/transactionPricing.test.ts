import { describe, expect, it } from 'vitest'

import {
  calculateTransactionGrossAmount,
  calculateTransactionUnitPrice,
  resolveTransactionPriceContract,
} from './transactionPricing'

describe('transaction pricing scale', () => {
  const perUnitContract = { price_unit: 'per_unit' as const, price_scale: '1' }
  const percentOfParContract = {
    price_unit: 'percent_of_par' as const,
    price_scale: '0.01',
  }

  it('prices percent of par against face quantity using the API scale', () => {
    expect(
      calculateTransactionGrossAmount(percentOfParContract, 1_000, 98.5),
    ).toBe(985)
    expect(
      calculateTransactionUnitPrice(percentOfParContract, 1_000, 985),
    ).toBe(98.5)
  })

  it('keeps ordinary assets on quantity times unit price', () => {
    expect(calculateTransactionGrossAmount(perUnitContract, 3, 98.5)).toBe(295.5)
    expect(calculateTransactionUnitPrice(perUnitContract, 3, 295.5)).toBe(98.5)
  })

  it('fails closed when API price contracts are missing, invalid, or inconsistent', () => {
    expect(calculateTransactionGrossAmount(null, 3, 98.5)).toBeNull()
    expect(
      calculateTransactionGrossAmount(
        { price_unit: 'per_unit', price_scale: 0 },
        3,
        98.5,
      ),
    ).toBeNull()
    expect(
      resolveTransactionPriceContract([perUnitContract, percentOfParContract]),
    ).toBeNull()
    expect(
      resolveTransactionPriceContract([
        perUnitContract,
        { price_unit: 'per_unit', price_scale: 0 },
      ]),
    ).toBeNull()
  })
})
