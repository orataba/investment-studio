import { describe, expect, it } from 'vitest'

import { rankInstrumentMatches } from './components/InstrumentFilterCombobox'
import type { PortfolioTransactionRecord, SharedInstrumentRecord } from './lib/api'
import {
  buildTransactionExportRows,
  countActiveTransactionFilters,
  normalizeTransactionDecimalDraft,
  TRANSACTION_EXPORT_HEADERS,
  transactionDraftHasChanges,
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

  it('exports exact ledger facts as scale-preserving text without a Number boundary', () => {
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
      quantity: '100',
      price: '1.25',
      gross_amount: '125',
      fees: '1',
      taxes: '0',
      consideration_basis: 'source_reported',
      numeric_scale_state: 'declared',
      quantity_input_scale: 2,
      price_input_scale: 4,
      gross_amount_input_scale: 2,
      fees_input_scale: 2,
      taxes_input_scale: 2,
      currency: 'CNY',
      net_cash_effect: '-126',
      note: '=unsafe',
      revision_id: 'revision-1',
      revision_number: 1,
      lifecycle_status: 'active',
      last_mutation_id: 'mutation-1',
      last_changed_at: '2026-07-01T04:00:00Z',
      last_actor: {
        actor_id: 'local-user:test',
        display_name: 'Test User',
        actor_type: 'user',
        actor_source: 'client_asserted',
      },
    } as PortfolioTransactionRecord
    const fxTransaction: PortfolioTransactionRecord = {
      ...transaction,
      transaction_id: 'txn-fx',
      transaction_type: 'fx_conversion',
      instrument_id: null,
      instrument_ref: null,
      quantity: null,
      price: null,
      quantity_input_scale: null,
      price_input_scale: null,
      gross_amount: '100',
      gross_amount_input_scale: 2,
      counter_amount: '716.83',
      counter_amount_input_scale: 4,
      quoted_fx_rate: '7.168',
      quoted_fx_rate_input_scale: 8,
      consideration_basis: null,
      net_cash_effect: '-100',
    }

    const rows = buildTransactionExportRows([transaction, fxTransaction])

    expect(rows[0]).toEqual(TRANSACTION_EXPORT_HEADERS)
    expect(rows[1].slice(10, 18)).toEqual([
      '100.00',
      '1.2500',
      '125.00',
      '',
      '',
      '1.00',
      '0.00',
      '-126',
    ])
    expect(rows[2].slice(10, 18)).toEqual([
      '',
      '',
      '100.00',
      '716.8300',
      '7.16800000',
      '1.00',
      '0.00',
      '-100',
    ])
    expect(rows[1].slice(10, 18).every((cell) => typeof cell === 'string')).toBe(true)
    expect(rows[1][19]).toBe('=unsafe')
  })

  it('treats decimal input scale as evidence while ignoring non-evidentiary formatting', () => {
    expect(normalizeTransactionDecimalDraft('001.2500')).toBe('1.2500')
    expect(normalizeTransactionDecimalDraft('-0.00')).toBe('0.00')
    expect(
      transactionDraftHasChanges(
        { gross_amount: '0125.00', note: ' Original ', instrument_search: 'ignored' },
        { gross_amount: '125.00', note: 'Original', instrument_search: '' },
      ),
    ).toBe(false)
    expect(
      transactionDraftHasChanges(
        { gross_amount: '125.0', note: 'Original' },
        { gross_amount: '125.00', note: 'Original' },
      ),
    ).toBe(true)
    expect(
      transactionDraftHasChanges(
        { gross_amount: '126.00', note: 'Original' },
        { gross_amount: '125.00', note: 'Original' },
      ),
    ).toBe(true)
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
