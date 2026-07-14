export interface AccountSlicePositionLot {
  account_id: string
  remaining_quantity: number
  remaining_cost_basis: number
  current_market_value?: number | null
  status: string
}

export interface PositionLotAccountSlice {
  accountId: string
  quantity: number
  remainingCost: number
  marketValue: number | null
  openPositionLotCount: number
}

export function aggregatePositionLotAccountSlices(
  positionLots: readonly AccountSlicePositionLot[],
): PositionLotAccountSlice[] {
  const buckets = new Map<
    string,
    PositionLotAccountSlice & { knownMarketValue: number; marketValueComplete: boolean }
  >()

  positionLots.forEach((positionLot) => {
    const current = buckets.get(positionLot.account_id) ?? {
      accountId: positionLot.account_id,
      quantity: 0,
      remainingCost: 0,
      marketValue: 0,
      knownMarketValue: 0,
      marketValueComplete: true,
      openPositionLotCount: 0,
    }
    current.quantity += positionLot.remaining_quantity
    current.remainingCost += positionLot.remaining_cost_basis
    if (positionLot.current_market_value != null) {
      current.knownMarketValue += positionLot.current_market_value
    } else if (Math.abs(positionLot.remaining_quantity) > 1e-9) {
      current.marketValueComplete = false
    }
    if (positionLot.status === 'open') {
      current.openPositionLotCount += 1
    }
    buckets.set(positionLot.account_id, current)
  })

  return Array.from(buckets.values())
    .map((bucket) => ({
      accountId: bucket.accountId,
      quantity: bucket.quantity,
      remainingCost: bucket.remainingCost,
      marketValue: bucket.marketValueComplete ? bucket.knownMarketValue : null,
      openPositionLotCount: bucket.openPositionLotCount,
    }))
    .sort((left, right) => {
      const rightMarketValue = right.marketValue ?? Number.NEGATIVE_INFINITY
      const leftMarketValue = left.marketValue ?? Number.NEGATIVE_INFINITY
      return rightMarketValue - leftMarketValue || left.accountId.localeCompare(right.accountId)
    })
}
