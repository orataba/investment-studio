import { describe, expect, it } from 'vitest'

import {
  countActiveTransactionFilters,
  transactionActivityLabel,
  transactionChangedFields,
  transactionFieldChanges,
  transactionAuditValue,
  transactionDateLabels,
} from './lib/transactionPresentation'

describe('transaction presentation', () => {
  it('retains exact old/new values and distinguishes absent, null and empty audit fields', () => {
    const fields = transactionFieldChanges({ change_type: 'update',
      before: { gross_amount: '250.00', note: null, fee_components: [{ amount: '1.20' }] },
      after: { gross_amount: '250.10', note: '', fee_components: [{ amount: '1.25' }], trade_timezone: 'Asia/Shanghai' },
    })
    expect(fields[0]).toMatchObject({ label: 'Gross amount', before: '250.00', after: '250.10' })
    expect(transactionAuditValue(fields[1].before, fields[1].beforeRecorded)).toBe('Empty (null)')
    expect(transactionAuditValue(fields[1].after, fields[1].afterRecorded)).toBe('Empty text')
    expect(transactionAuditValue(fields[2].after, true)).toContain('1.25')
    expect(transactionAuditValue(fields[3].before, fields[3].beforeRecorded)).toBe('Not recorded')
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
    expect(
      transactionActivityLabel(
        'maturity_redemption',
        'option',
        null,
        'option_long_exercise',
      ),
    ).toBe('Option Exercised')
    expect(
      transactionActivityLabel(
        'lifecycle_event',
        'option',
        null,
        'option_writer_assignment',
      ),
    ).toBe('Option Assigned')
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
