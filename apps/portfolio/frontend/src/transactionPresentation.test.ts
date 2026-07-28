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
    quote_selection_policy: {
      trading: [],
      valuation: [],
      total_return: [],
      chart: [],
      reference: [],
    },
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
      position_effective_date: '2026-07-03',
      economic_date: '2026-07-03',
      external_flow_date: null,
      entitlement_date: null,
      acquisition_date: null,
      account: {
        account_id: 'broker',
        portfolio_id: 'portfolio-1',
        account_name: 'Broker',
        account_type: 'securities_account',
        currency: 'CNY',
        status: 'active',
      },
      settlement_cash_account: null,
      instrument_id: 'fund-1',
      instrument_ref: {
        instrument_id: 'fund-1',
        instrument_name: 'Fund One',
        instrument_type: 'fund',
        currency: 'CNY',
        identifiers: [],
      },
      quantity: 100,
      source_quantity: '100',
      price: 1.25,
      source_price: '1.25',
      gross_amount: 125,
      source_gross_amount: '125',
      counter_amount: null,
      source_counter_amount: null,
      fx_rate: null,
      source_fx_rate: null,
      fees: 1,
      source_fees: '1',
      fee_category: 'transaction_cost',
      taxes: 0,
      source_taxes: '0',
      currency: 'CNY',
      transfer_scope: null,
      transfer_object_type: null,
      transfer_group_id: null,
      counterparty_account_id: null,
      net_cash_effect: -126,
      note: '=unsafe',
      created_at: null,
      row_version: 1,
    } satisfies PortfolioTransactionRecord

    const rows = buildTransactionExportRows([transaction])
    const column = (header: string) => TRANSACTION_EXPORT_HEADERS.indexOf(header)

    expect(rows[0]).toEqual(TRANSACTION_EXPORT_HEADERS)
    expect(rows[1][column('Quantity')]).toBe(100)
    expect(rows[1][column('Gross Amount')]).toBe(125)
    expect(rows[1][column('Position Effective Date')]).toBe('2026-07-03')
    expect(rows[1][column('Economic Date')]).toBe('2026-07-03')
    expect(rows[1][column('Fee Category')]).toBe('transaction_cost')
    expect(rows[1][column('Note')]).toBe('=unsafe')
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
