import { describe, expect, it } from 'vitest'

import {
  resolveTransactionAction,
  transactionActionGroups,
  transactionActionValue,
} from './lib/transactionActions'

describe('asset-first transaction actions', () => {
  it('uses the same asset-first action IDs as the import template', () => {
    const actionsFor = (
      assetDomain: 'security' | 'derivative' | 'cash',
      assetSubtype?: string,
    ) =>
      transactionActionGroups(assetDomain, assetSubtype)
        .flatMap((group) => group.actions)
        .map((item) => item.value)

    expect(actionsFor('security', 'equity')).toEqual([
      'buy',
      'sell',
      'dividend',
      'dividend_reinvestment',
      'return_of_capital',
      'fee',
      'tax',
      'transfer_out',
      'transfer_in',
      'opening_balance',
    ])
    expect(actionsFor('derivative', 'fcn')).toEqual([
      'entry',
      'early_exit',
      'coupon',
      'knock_in_close',
      'knock_out_close',
      'maturity_close',
      'fee',
      'tax',
      'opening_balance',
    ])
    expect(actionsFor('derivative', 'option')).toEqual([
      'buy_to_open',
      'sell_to_close',
      'sell_to_open',
      'buy_to_close',
      'expire_long',
      'cash_settle_long',
      'expire_written',
      'cash_settle_written',
      'fee',
      'tax',
      'opening_balance',
    ])
    expect(actionsFor('cash')).toEqual([
      'deposit',
      'withdrawal',
      'interest',
      'fx_conversion',
      'fee',
      'tax',
      'transfer_out',
      'transfer_in',
      'opening_balance',
    ])
  })

  it('shows only security actions inside the security domain', () => {
    const groups = transactionActionGroups('security', 'equity')
    const actions = groups.flatMap((group) => group.actions)

    expect(actions.map((item) => item.label)).toContain('Buy')
    expect(actions.map((item) => item.label)).not.toContain('Buy to Open Option')
    expect(actions.map((item) => item.transactionType)).not.toContain('option_write')
    expect(actions.map((item) => item.transactionType)).not.toContain('deposit')
  })

  it('maps all four Call open and close directions to canonical commands', () => {
    const groups = transactionActionGroups('derivative', 'option', 'call')
    const actions = groups.flatMap((group) => group.actions)

    expect(actions.filter((item) => item.label.includes('Call')).slice(0, 4)).toMatchObject([
      { label: 'Buy to Open Call', transactionType: 'buy' },
      { label: 'Sell to Close Call', transactionType: 'sell' },
      { label: 'Sell to Open Call', transactionType: 'option_write' },
      { label: 'Buy to Close Call', transactionType: 'option_buy_to_close' },
    ])
  })

  it('keeps close outcomes inside the chosen derivative subtype', () => {
    const groups = transactionActionGroups('derivative', 'fcn')
    const value = transactionActionValue(
      groups,
      'maturity_redemption',
      'fcn_knock_in',
    )

    expect(resolveTransactionAction(groups, value)).toMatchObject({
      label: 'FCN Knock-In Close',
      transactionType: 'maturity_redemption',
      lifecycleEventType: 'fcn_knock_in',
    })
  })

  it('limits a new option contract to opening actions', () => {
    const actions = transactionActionGroups('derivative', 'option', 'put', {
      newDerivativeContract: true,
    }).flatMap((group) => group.actions)

    expect(actions.map((item) => item.label)).toEqual([
      'Buy to Open Put',
      'Sell to Open Put',
      'Option Opening Balance',
    ])
    expect(actions.map((item) => item.label)).not.toContain('Buy to Close Put')
  })

  it('limits a new FCN contract to entry and opening balance', () => {
    const actions = transactionActionGroups('derivative', 'fcn', null, {
      newDerivativeContract: true,
    }).flatMap((group) => group.actions)

    expect(actions.map((item) => item.label)).toEqual([
      'FCN Entry',
      'FCN Opening Balance',
    ])
  })
})
