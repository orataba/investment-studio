import { describe, expect, it } from 'vitest'

import type {
  DataStatus,
  InstrumentType,
  MetricFamily,
  PriceUnit,
  QuoteBasis,
  QuoteSelectionPolicy,
} from '../../../../packages/instrument-core/ts/src'
import {
  resolveQuoteBasis,
  resolveRoleQuote,
  summarizeRoleQuotes,
} from './quoteRoleResolution'

type TestPoint = {
  metric_family: MetricFamily
  quote_basis: QuoteBasis
  as_of_date: string
  value: string
  currency: string
  price_unit: PriceUnit
  price_scale: string
  status: DataStatus
}

const POLICY: QuoteSelectionPolicy = {
  trading: ['last', 'close'],
  valuation: ['close', 'last'],
  total_return: ['adjusted_close'],
  chart: ['adjusted_close'],
  reference: ['par'],
}

const CLOSE_ONLY_POLICY: QuoteSelectionPolicy = {
  trading: ['close'],
  valuation: ['close'],
  total_return: [],
  chart: ['close'],
  reference: [],
}

function point(
  overrides: Partial<TestPoint> & Pick<TestPoint, 'quote_basis' | 'as_of_date' | 'value'>,
): TestPoint {
  return {
    metric_family: 'price',
    currency: 'USD',
    price_unit: 'per_unit',
    price_scale: '1',
    status: 'complete',
    ...overrides,
  }
}

function record(
  latestMarketData: TestPoint[],
  policy = POLICY,
  identity: { instrument_type: InstrumentType; currency: string } = {
    instrument_type: 'equity',
    currency: 'USD',
  },
) {
  return {
    ...identity,
    latest_market_data: latestMarketData,
    quote_selection_policy: policy,
  }
}

describe('Platform quote-role resolution', () => {
  it('honors role policy order and chooses the latest complete observation for one identity', () => {
    const latestClose = point({
      quote_basis: 'close',
      as_of_date: '2026-07-14',
      value: '99',
    })
    const selected = resolveRoleQuote(
      record(
        [
          point({
            quote_basis: 'close',
            as_of_date: '2026-07-13',
            value: '98',
          }),
          point({
            quote_basis: 'last',
            as_of_date: '2026-07-15',
            value: '102',
          }),
          latestClose,
        ],
        POLICY,
      ),
      'valuation',
    )

    expect(selected).toEqual(latestClose)
  })

  it('uses the reference policy only after the requested role has no match', () => {
    const parPoint = point({
      quote_basis: 'par',
      as_of_date: '2026-07-14',
      value: '98.5',
    })
    const selected = resolveRoleQuote(
      record(
        [parPoint],
        POLICY,
      ),
      'valuation',
    )

    expect(selected).toEqual(parPoint)
  })

  it('ignores partial observations and falls through to the next complete policy basis', () => {
    const completeLast = point({
      quote_basis: 'last',
      as_of_date: '2026-07-14',
      value: '101',
    })
    const target = record([
      point({
        quote_basis: 'close',
        as_of_date: '2026-07-15',
        value: '102',
        status: 'partial',
      }),
      completeLast,
    ])

    expect(resolveRoleQuote(target, 'valuation')).toEqual(completeLast)
    expect(
      resolveRoleQuote(
        record([
          point({
            quote_basis: 'close',
            as_of_date: '2026-07-15',
            value: '102',
            status: 'partial',
          }),
        ], CLOSE_ONLY_POLICY),
        'valuation',
      ),
    ).toBeNull()
  })

  it('fails closed when one basis has complete observations in multiple currencies', () => {
    const target = record(
      [
        point({ quote_basis: 'close', as_of_date: '2026-07-15', value: '102' }),
        point({
          quote_basis: 'close',
          as_of_date: '2026-07-15',
          value: '95',
          currency: 'EUR',
        }),
      ],
      CLOSE_ONLY_POLICY,
    )

    expect(resolveQuoteBasis(target, 'close')).toBeNull()
    expect(resolveRoleQuote(target, 'valuation')).toBeNull()
    expect(summarizeRoleQuotes(target)).toEqual([])
  })

  it('fails closed for ambiguous or invalid metric and price-contract identity', () => {
    const ambiguousContract = record(
      [
        point({ quote_basis: 'close', as_of_date: '2026-07-15', value: '102' }),
        point({
          quote_basis: 'close',
          as_of_date: '2026-07-15',
          value: '102',
          price_unit: 'rate',
        }),
      ],
      CLOSE_ONLY_POLICY,
    )
    const wrongMetricFamily = record(
      [
        point({
          metric_family: 'nav',
          quote_basis: 'close',
          as_of_date: '2026-07-15',
          value: '102',
        }),
      ],
      CLOSE_ONLY_POLICY,
    )
    const invalidScale = record(
      [
        point({
          quote_basis: 'close',
          as_of_date: '2026-07-15',
          value: '102',
          price_scale: '',
        }),
      ],
      CLOSE_ONLY_POLICY,
    )

    expect(resolveRoleQuote(ambiguousContract, 'valuation')).toBeNull()
    expect(resolveRoleQuote(wrongMetricFamily, 'valuation')).toBeNull()
    expect(resolveRoleQuote(invalidScale, 'valuation')).toBeNull()
  })

  it('returns null when neither role nor reference policy selects available data', () => {
    const unselectedOnly = record(
      [point({ quote_basis: 'adjusted_close', as_of_date: '2026-07-15', value: '1.25' })],
      CLOSE_ONLY_POLICY,
    )

    expect(resolveRoleQuote(unselectedOnly, 'valuation')).toBeNull()
    expect(resolveRoleQuote(unselectedOnly, 'trading')).toBeNull()
    expect(summarizeRoleQuotes(unselectedOnly)).toEqual([])
  })

  it('deduplicates a basis shared by multiple roles and omits unresolved roles', () => {
    const closePoint = point({
      quote_basis: 'close',
      as_of_date: '2026-07-15',
      value: '102',
    })
    const summary = summarizeRoleQuotes(record([closePoint], CLOSE_ONLY_POLICY))

    expect(summary).toEqual([{ role: 'valuation', point: closePoint }])
  })
})
