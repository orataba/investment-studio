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
      quantity: '100.00',
      price: '1.2500',
      gross_amount: '125.00',
      fees: '1.00',
      taxes: '0.00',
      currency: 'CNY',
      net_cash_effect: '-126.00',
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

    const rows = buildTransactionExportRows([transaction])

    expect(rows[0]).toEqual(TRANSACTION_EXPORT_HEADERS)
    expect(rows[1][10]).toBe(100)
    expect(rows[1][12]).toBe(125)
    expect(rows[1][17]).toBe('=unsafe')
  })

  it('compares decimal drafts without treating formatting-only edits as changes', () => {
    expect(normalizeTransactionDecimalDraft('001.2500')).toBe('1.25')
    expect(normalizeTransactionDecimalDraft('-0.00')).toBe('0')
    expect(
      transactionDraftHasChanges(
        { gross_amount: '125.0', note: ' Original ', instrument_search: 'ignored' },
        { gross_amount: '125.00', note: 'Original', instrument_search: '' },
      ),
    ).toBe(false)
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
