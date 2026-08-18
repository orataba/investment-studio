import { describe, expect, it } from 'vitest'

import { rankInstrumentMatches } from './components/InstrumentFilterCombobox'
import type { SharedInstrumentRecord } from './lib/api'
import {
  countActiveTransactionFilters,
  transactionActivityLabel,
  transactionChangedFields,
  transactionDateLabels,
} from './lib/transactionPresentation'

function instrument(
  instrumentId: string,
  identifier: string,
  instrumentName: string,
): SharedInstrumentRecord {
  return {
    instrument_id: instrumentId,
    instrument_name: instrumentName,
    instrument_type: 'public_fund',
    currency: 'CNY',
    exchange_code: null,
    latest_market_data: [],
    quote_selection_policy: {
      trading: [],
      valuation: [],
      total_return: [],
      chart: [],
      reference: [],
    },
    coverage_state: 'complete',
    broker_identifiers: [],
    identifiers: [
      {
        identifier_type: 'ticker',
        identifier_value: identifier,
        is_primary: true,
      },
    ],
  }
}

describe('transaction presentation', () => {
  it('ranks exact and prefix identifiers ahead of name matches without rendering the full registry', () => {
    const matches = rankInstrumentMatches(
      [
        instrument('name-match', 'ZZZ', '513050 allocation sleeve'),
        instrument('prefix-match', '513050.SH', 'Overseas Internet ETF'),
        instrument('exact-match', '513050', 'Exact identifier'),
      ],
      '513050',
      2,
    )

    expect(matches.map((item) => item.instrument_id)).toEqual(['exact-match', 'prefix-match'])
  })

  it('counts only populated URL filters', () => {
    expect(
      countActiveTransactionFilters({
        account_id: 'broker',
        asset_domain: 'security',
        transaction_type: '',
        position_reference_id: 'fund-1',
        start_date: undefined,
        end_date: '2026-07-01',
      }),
    ).toBe(4)
  })

  it('uses income-event date labels for dividends and coupons only', () => {
    expect(transactionDateLabels('dividend')).toEqual({
      settlement: 'Pay / Settlement Date',
      entitlement: 'Ex / Recognition Date',
    })
    expect(transactionDateLabels('coupon')).toEqual(transactionDateLabels('dividend'))
    expect(transactionDateLabels('fee')).toEqual({
      settlement: 'Settlement Date',
      entitlement: 'Entitlement Date',
    })
  })

  it('uses fund subscription language without changing the stored transaction command', () => {
    expect(transactionActivityLabel('buy', 'public_fund')).toBe('Subscription')
    expect(transactionActivityLabel('sell', 'private_fund')).toBe('Redemption')
    expect(transactionActivityLabel('buy', 'etf')).toBe('Buy')
  })

  it('uses all four canonical option action labels', () => {
    expect(transactionActivityLabel('buy', 'option')).toBe('Option Buy to Open')
    expect(transactionActivityLabel('sell', 'option')).toBe('Option Sell to Close')
    expect(transactionActivityLabel('option_write', 'option')).toBe('Option Sell to Open')
    expect(transactionActivityLabel('option_buy_to_close', 'option')).toBe(
      'Option Buy to Close',
    )
    expect(transactionActivityLabel('buy', null, 'buy_to_close')).toBe(
      'Option Buy to Close',
    )
  })

  it('summarizes only the fields changed by an audited correction', () => {
    expect(
      transactionChangedFields({
        change_type: 'update',
        before: {
          transaction_id: 'txn-1',
          row_version: 1,
          trade_date: '2026-07-01',
          gross_amount: '250.00',
        },
        after: {
          transaction_id: 'txn-1',
          row_version: 2,
          trade_date: '2026-07-02',
          gross_amount: '250.00',
        },
      }),
    ).toEqual(['Trade date'])
  })
})
