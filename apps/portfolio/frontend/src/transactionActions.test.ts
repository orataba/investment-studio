import { describe, expect, it } from 'vitest'

import {
  resolveTransactionAction,
  transactionActionGroups,
  transactionActionValue,
} from './lib/transactionActions'

describe('asset-first transaction actions', () => {
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

  it('keeps lifecycle selection inside the chosen derivative subtype', () => {
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
})
