import { describe, expect, it } from 'vitest'

import { rankInstrumentMatches } from './components/InstrumentFilterCombobox'
import type { PortfolioTransactionRecord, SharedInstrumentRecord } from './lib/api'
import {
  buildTransactionExportRows,
  countActiveTransactionFilters,
  TRANSACTION_EXPORT_HEADERS,
  transactionActivityLabel,
  transactionChangedFields,
  transactionDateLabels,
  transactionTypeChoiceLabel,
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

  it('exports numeric ledger facts as numeric spreadsheet cells', () => {
    const transaction = {
      transaction_id: 'txn-1',
      transaction_sequence: 1,
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
        broker_identifiers: [],
      },
      derivative_contract_id: null,
      derivative_contract: null,
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

  it('uses fund subscription language without changing the stored transaction command', () => {
    expect(transactionActivityLabel('buy', 'fund')).toBe('Subscription')
    expect(transactionActivityLabel('sell', 'fund')).toBe('Redemption')
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

  it('names the option meaning of buy and sell before security selection', () => {
    expect(transactionTypeChoiceLabel('buy')).toBe('Buy / FCN Entry / Option Buy to Open')
    expect(transactionTypeChoiceLabel('sell')).toBe('Sell / Option Sell to Close')
    expect(transactionTypeChoiceLabel('buy', 'option')).toBe('Option Buy to Open')
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
