import { describe, expect, it } from 'vitest'

import { aggregatePositionLotAccountSlices } from './lib/positionLotAggregation'

describe('position-lot account slice aggregation', () => {
  it('marks a slice unavailable when any non-zero open quantity is unpriced', () => {
    const [slice] = aggregatePositionLotAccountSlices([
      {
        account_id: 'broker-cny',
        remaining_quantity: 100,
        remaining_cost_basis: 90,
        current_market_value: 110,
        status: 'open',
      },
      {
        account_id: 'broker-cny',
        remaining_quantity: 50,
        remaining_cost_basis: 40,
        current_market_value: null,
        status: 'open',
      },
    ])

    expect(slice).toMatchObject({
      quantity: 150,
      remainingCost: 130,
      marketValue: null,
      openPositionLotCount: 2,
    })
  })

  it('ignores an unpriced closed zero-quantity lot when open valuation is complete', () => {
    const [slice] = aggregatePositionLotAccountSlices([
      {
        account_id: 'broker-cny',
        remaining_quantity: 25,
        remaining_cost_basis: 20,
        current_market_value: 30,
        status: 'open',
      },
      {
        account_id: 'broker-cny',
        remaining_quantity: 0,
        remaining_cost_basis: 0,
        current_market_value: null,
        status: 'closed',
      },
    ])

    expect(slice?.marketValue).toBe(30)
  })
})
