import { describe, expect, it } from 'vitest'
import {
  canonicalPriceContract,
  supportsNavHistoryImport,
} from '../../../../packages/instrument-core/ts/src'

import {
  defaultMarketDataSelection,
  formatPriceContract,
  quoteBasisOptions,
} from './marketDataContract'


describe('Platform market-data price identity', () => {
  it('defaults each instrument type to its canonical metric and quote basis', () => {
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
    expect(canonicalPriceContract('fx', 'fx')).toEqual({
      price_unit: 'rate',
      price_scale: '1',
    })
    expect(canonicalPriceContract('other', 'fx')).toEqual({
      price_unit: 'rate',
      price_scale: '1',
    })
    expect(canonicalPriceContract('public_fund', 'nav')).toEqual({
      price_unit: 'per_unit',
      price_scale: '1',
    })
    expect(canonicalPriceContract('equity', 'price')).toEqual({
      price_unit: 'per_unit',
      price_scale: '1',
    })
  })

  it('does not expose bond-only quote identities', () => {
    expect(quoteBasisOptions('price').map((option) => option.value)).toEqual([
      'last',
      'close',
      'adjusted_close',
      'par',
    ])
  })

  it('exposes only the two canonical fund NAV series with unambiguous labels', () => {
    expect(quoteBasisOptions('nav')).toEqual([
      { value: 'official_nav', label: 'Unit NAV' },
      {
        value: 'total_return_nav',
        label: 'Dividend-Reinvested Total Return NAV',
      },
    ])
  })

  it('formats the unit and scale together for quote tables', () => {
    expect(formatPriceContract('per_unit', '1')).toBe('Per unit × 1')
    expect(formatPriceContract('rate', '1')).toBe('Rate × 1')
  })

  it('allows NAV history import only for funds', () => {
    expect(supportsNavHistoryImport('public_fund')).toBe(true)
    expect(supportsNavHistoryImport('private_fund')).toBe(true)
    expect(supportsNavHistoryImport('etf')).toBe(false)
    expect(supportsNavHistoryImport('fx')).toBe(false)
  })
})
