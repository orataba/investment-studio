import { describe, expect, it } from 'vitest'

import { rankInstrumentMatches } from './components/InstrumentFilterCombobox'
import type { PortfolioTransactionRecord, SharedInstrumentRecord } from './lib/api'
import {
  buildTransactionExportRows,
  countActiveTransactionFilters,
  TRANSACTION_EXPORT_HEADERS,
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
    instrument_type: 'fund',
    currency: 'CNY',
    latest_market_data: [],
    coverage_state: 'complete',
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

  it('exports numeric ledger facts as numeric spreadsheet cells', () => {
    const transaction = {
      transaction_id: 'txn-1',
      portfolio_id: 'portfolio-1',
      transaction_type: 'buy',
      flow_scope: 'internal_portfolio',
      trade_date: '2026-07-01',
      trade_time: '12:00',
      trade_at: '2026-07-01T12:00:00+08:00',
      trade_timezone: 'Asia/Shanghai',
      trade_time_is_estimated: false,
      settlement_date: '2026-07-02',
      account: {
        account_id: 'broker',
        portfolio_id: 'portfolio-1',
        account_name: 'Broker',
        account_type: 'securities_account',
        currency: 'CNY',
        status: 'active',
      },
      instrument_id: 'fund-1',
      instrument_ref: {
        instrument_id: 'fund-1',
        instrument_name: 'Fund One',
        instrument_type: 'fund',
        currency: 'CNY',
        identifiers: [],
      },
      quantity: 100,
      price: 1.25,
      gross_amount: 125,
      fees: 1,
      taxes: 0,
      currency: 'CNY',
      net_cash_effect: -126,
      note: '=unsafe',
    } as PortfolioTransactionRecord

    const rows = buildTransactionExportRows([transaction])

    expect(rows[0]).toEqual(TRANSACTION_EXPORT_HEADERS)
    expect(rows[1][10]).toBe(100)
    expect(rows[1][12]).toBe(125)
    expect(rows[1][17]).toBe('=unsafe')
  })

  it('counts only populated URL filters', () => {
    expect(
      countActiveTransactionFilters({
        account_id: 'broker',
        transaction_type: '',
        instrument_id: 'fund-1',
        start_date: undefined,
        end_date: '2026-07-01',
      }),
    ).toBe(3)
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
})
