import { describe, expect, it } from 'vitest'
import {
  canonicalPriceContract,
  supportsNavHistoryImport,
} from '../../../../packages/instrument-core/ts/src'

import {
  defaultMarketDataSelection,
  formatPriceContract,
  quoteBasisOptionsForInstrument,
} from './marketDataContract'


describe('Platform market-data price identity', () => {
  it('defaults each instrument type to its canonical metric and quote basis', () => {
    expect(defaultMarketDataSelection('bond')).toEqual({
      metric_family: 'price',
      quote_basis: 'dirty_price',
    })
    expect(defaultMarketDataSelection('fx')).toEqual({
      metric_family: 'fx',
      quote_basis: 'spot',
    })
    expect(defaultMarketDataSelection('equity')).toEqual({
      metric_family: 'price',
      quote_basis: 'close',
    })
  })

  it('derives the only valid unit and scale from instrument type and metric family', () => {
    expect(canonicalPriceContract('bond', 'price')).toEqual({
      price_unit: 'percent_of_par',
      price_scale: '0.01',
    })
    expect(canonicalPriceContract('bond', 'nav')).toEqual({
      price_unit: 'per_unit',
      price_scale: '1',
    })
    expect(canonicalPriceContract('fx', 'fx')).toEqual({
      price_unit: 'rate',
      price_scale: '1',
    })
    expect(canonicalPriceContract('other', 'fx')).toEqual({
      price_unit: 'rate',
      price_scale: '1',
    })
    expect(canonicalPriceContract('fund', 'nav')).toEqual({
      price_unit: 'per_unit',
      price_scale: '1',
    })
    expect(canonicalPriceContract('equity', 'price')).toEqual({
      price_unit: 'per_unit',
      price_scale: '1',
    })
  })

  it('offers accrued interest only on the bond price-entry path', () => {
    expect(
      quoteBasisOptionsForInstrument('bond', 'price').map((option) => option.value),
    ).toContain('accrued_interest')
    expect(
      quoteBasisOptionsForInstrument('equity', 'price').map((option) => option.value),
    ).not.toContain('accrued_interest')
    expect(
      quoteBasisOptionsForInstrument('bond', 'nav').map((option) => option.value),
    ).not.toContain('accrued_interest')
  })

  it('exposes only the two canonical fund NAV series with unambiguous labels', () => {
    expect(quoteBasisOptionsForInstrument('fund', 'nav')).toEqual([
      { value: 'official_nav', label: 'Unit NAV' },
      {
        value: 'total_return_nav',
        label: 'Dividend-Reinvested Total Return NAV',
      },
    ])
  })

  it('formats the unit and scale together for quote tables', () => {
    expect(formatPriceContract('percent_of_par', '0.01')).toBe('Percent of par × 0.01')
    expect(formatPriceContract('rate', '1')).toBe('Rate × 1')
  })

  it('allows NAV history import only for funds', () => {
    expect(supportsNavHistoryImport('fund')).toBe(true)
    expect(supportsNavHistoryImport('etf')).toBe(false)
    expect(supportsNavHistoryImport('fx')).toBe(false)
  })
})
