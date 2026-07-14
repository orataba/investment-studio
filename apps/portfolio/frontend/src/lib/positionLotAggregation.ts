import { exactDecimalSum } from './exactDecimal'

export interface AccountSlicePositionLot {
  account_id: string
  open_quantity_exact: string
  cost_basis_local_exact: string
}

export interface PositionLotAccountSlice {
  accountId: string
  quantityExact: string
  costBasisLocalExact: string
  openPositionLotCount: number
}

export function aggregatePositionLotAccountSlices(
  positionLots: readonly AccountSlicePositionLot[],
): PositionLotAccountSlice[] {
  const buckets = new Map<string, { quantities: string[]; localCosts: string[]; lotCount: number }>()

  positionLots.forEach((positionLot) => {
    const current = buckets.get(positionLot.account_id) ?? {
      quantities: [],
      localCosts: [],
      lotCount: 0,
    }
    current.quantities.push(positionLot.open_quantity_exact)
    current.localCosts.push(positionLot.cost_basis_local_exact)
    current.lotCount += 1
    buckets.set(positionLot.account_id, current)
  })

  return Array.from(buckets.entries())
    .map(([accountId, bucket]) => ({
      accountId,
      quantityExact: exactDecimalSum(bucket.quantities),
      costBasisLocalExact: exactDecimalSum(bucket.localCosts),
      openPositionLotCount: bucket.lotCount,
    }))
    .sort((left, right) => left.accountId.localeCompare(right.accountId))
}
