import { describe, expect, it } from 'vitest'

import {
  canonicalBrokerIdentifiers,
} from '../../../../packages/instrument-core/ts/src'

import {
  formatDecimalValue,
  marketDataExportRows,
  marketDataToCsv,
  parseFxPairLabel,
  type PlatformInstrumentRecord,
  type PlatformMarketDataPoint,
  upsertInstrumentRecord,
} from './instrumentRegistryModel'

const marketPoint: PlatformMarketDataPoint = {
  metric_family: 'price',
  quote_basis: 'close',
  as_of_date: '2026-07-27',
  value: '1.2345',
  currency: 'CNY',
  price_unit: 'per_unit',
  price_scale: '1',
  provider: 'provider, "verified"',
  status: 'complete',
}

function instrument(status: 'active' | 'archived'): PlatformInstrumentRecord {
  return {
    instrument_id: 'test-instrument',
    instrument_name: 'Test Instrument',
    instrument_type: 'equity',
    currency: 'CNY',
    exchange_code: 'XSHG',
    identifiers: [
      {
        identifier_type: 'ticker',
        identifier_value: 'TEST.SH',
        is_primary: true,
      },
    ],
    broker_identifiers: [],
    latest_market_data: [marketPoint],
    quote_selection_policy: {
      trading: ['close'],
      valuation: ['close'],
      total_return: ['adjusted_close'],
      chart: ['close'],
      reference: ['close'],
    },
    coverage_state: 'complete',
    source_settings: {
      source_mode: 'api',
      source_email: '',
      source_location: '',
      source_api_profile: 'test',
      source_email_rules: [],
      expected_frequency: 'daily',
      market_calendar: 'XSHG',
      release_lag_days: 0,
      return_semantics: 'unknown',
    },
    refresh_status: {
      status: 'refreshed',
      message: '',
      requested_at: null,
      requested_by: null,
      mode: 'api',
      last_successful_requested_at: null,
    },
    lifecycle_state: {
      status,
      changed_at: null,
      changed_by: null,
    },
  }
}

describe('instrument registry view model', () => {
  it('exports typed market data as spreadsheet-safe CSV', () => {
    const csv = marketDataToCsv([marketPoint])

    expect(csv).toContain(
      'as_of_date,metric_family,quote_basis,value,currency,price_unit,price_scale,status,provider',
    )
    expect(csv).toContain('"provider, ""verified"""')
    expect(csv.split('\n')).toHaveLength(2)
  })

  it('trims display-only decimal padding without changing the stored CSV value', () => {
    expect(formatDecimalValue('1.4000000000000000')).toBe('1.4')
    expect(formatDecimalValue('0.9602000000000000')).toBe('0.9602')
    expect(formatDecimalValue('1.2300400')).toBe('1.23004')
    expect(formatDecimalValue('NA')).toBe('NA')
    expect(marketDataToCsv([{ ...marketPoint, value: '1.4000000000000000' }]))
      .toContain('1.4000000000000000')
  })

  it('uses the same market-data columns for CSV and Excel and protects text cells', () => {
    const formulaProvider = '=HYPERLINK("https://example.com")'
    const point = { ...marketPoint, value: '-1.25000000', provider: formulaProvider }

    expect(marketDataExportRows([point])).toEqual([
      [
        'as_of_date',
        'metric_family',
        'quote_basis',
        'value',
        'currency',
        'price_unit',
        'price_scale',
        'status',
        'provider',
      ],
      [
        '2026-07-27',
        'price',
        'close',
        '-1.25000000',
        'CNY',
        'per_unit',
        '1',
        'complete',
        formulaProvider,
      ],
    ])
    const csv = marketDataToCsv([point])
    expect(csv).toContain('-1.25000000')
    expect(csv).toContain("'=HYPERLINK")
  })

  it('keeps archive semantics reversible without leaking archived records to active-only views', () => {
    expect(upsertInstrumentRecord([instrument('active')], instrument('archived'), false))
      .toEqual([])
    expect(upsertInstrumentRecord([instrument('active')], instrument('archived'), true)[0])
      .toMatchObject({ lifecycle_state: { status: 'archived' } })
  })

  it('accepts only maintained, directional FX pairs', () => {
    expect(parseFxPairLabel('USD/CNY', ['USD', 'CNY', 'HKD'])).toEqual(['USD', 'CNY'])
    expect(parseFxPairLabel('USD/USD', ['USD', 'CNY'])).toBeNull()
    expect(parseFxPairLabel('EUR/CNY', ['USD', 'CNY'])).toBeNull()
  })

  it('canonicalizes reusable broker identifiers', () => {
    expect(
      canonicalBrokerIdentifiers([
        {
          broker: ' IBKR ',
          identifier_type: 'symbol',
          identifier_value: ' AAPL ',
          is_primary: true,
        },
      ]),
    ).toEqual([
      {
        broker: 'IBKR',
        identifier_type: 'symbol',
        identifier_value: 'AAPL',
        is_primary: true,
      },
    ])
    expect(() =>
      canonicalBrokerIdentifiers([
        {
          broker: 'IBKR',
          identifier_type: 'product_code',
          identifier_value: 'STK',
          is_primary: false,
        },
      ]),
    ).toThrow('exactly one primary')
  })
})
