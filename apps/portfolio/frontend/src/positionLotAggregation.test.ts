import { describe, expect, it } from 'vitest'

import { aggregatePositionLotAccountSlices } from './lib/positionLotAggregation'

describe('published position-lot account slice aggregation', () => {
  it('sums quantity and local cost without binary floating-point arithmetic', () => {
    const [slice] = aggregatePositionLotAccountSlices([
      {
        account_id: 'broker-cny',
        open_quantity_exact: '0.1',
        cost_basis_local_exact: '100000000000000000000.00000001',
      },
      {
        account_id: 'broker-cny',
        open_quantity_exact: '0.2',
        cost_basis_local_exact: '0.00000001',
      },
    ])

    expect(slice).toEqual({
      accountId: 'broker-cny',
      quantityExact: '0.3',
      costBasisLocalExact: '100000000000000000000.00000002',
      openPositionLotCount: 2,
    })
  })

  it('keeps account custody buckets separate', () => {
    const slices = aggregatePositionLotAccountSlices([
      {
        account_id: 'broker-b',
        open_quantity_exact: '2',
        cost_basis_local_exact: '20',
      },
      {
        account_id: 'broker-a',
        open_quantity_exact: '1',
        cost_basis_local_exact: '10',
      },
    ])

    expect(slices.map((slice) => slice.accountId)).toEqual(['broker-a', 'broker-b'])
  })
})
