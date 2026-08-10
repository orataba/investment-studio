import {
  canonicalPriceContract,
  type InstrumentCore,
  type PriceUnit,
} from '../../../../../packages/instrument-core/ts/src'
import type { PortfolioDerivativeContractCreate } from './api'

export type TransactionPriceContractInput = {
  price_unit: PriceUnit
  price_scale: number | string
}

export type TransactionPriceContract = {
  price_unit: PriceUnit
  price_scale: number
}

const PRICE_UNITS: ReadonlySet<PriceUnit> = new Set(['per_unit', 'rate'])

function normalizePriceContract(
  contract: TransactionPriceContractInput | null | undefined,
): TransactionPriceContract | null {
  if (!contract || !PRICE_UNITS.has(contract.price_unit)) {
    return null
  }
  const priceScale = Number(contract.price_scale)
  if (!Number.isFinite(priceScale) || priceScale <= 0) {
    return null
  }
  return { price_unit: contract.price_unit, price_scale: priceScale }
}

export function resolveTransactionPriceContract(
  candidates: readonly TransactionPriceContractInput[],
): TransactionPriceContract | null {
  if (!candidates.length) {
    return null
  }
  const contracts: TransactionPriceContract[] = []
  for (const candidate of candidates) {
    const contract = normalizePriceContract(candidate)
    if (!contract) {
      return null
    }
    contracts.push(contract)
  }
  const first = contracts[0]
  return contracts.every(
    (contract) =>
      contract.price_unit === first.price_unit && contract.price_scale === first.price_scale,
  )
    ? first
    : null
}

export function transactionPriceContractForInstrument(
  instrument: InstrumentCore | null | undefined,
): TransactionPriceContract | null {
  if (!instrument) {
    return null
  }
  return normalizePriceContract(
    canonicalPriceContract(instrument.instrument_type, 'price'),
  )
}

export function transactionPriceContractForDerivative(
  derivativeContract: PortfolioDerivativeContractCreate | null | undefined,
): TransactionPriceContract | null {
  return normalizePriceContract({
    price_unit: 'per_unit',
    price_scale:
      derivativeContract?.contract_type === 'option'
        ? derivativeContract.terms.contract_multiplier
        : 1,
  })
}

export function calculateTransactionGrossAmount(
  contract: TransactionPriceContractInput | null | undefined,
  quantity: number,
  price: number,
) {
  const resolvedContract = normalizePriceContract(contract)
  if (
    !resolvedContract ||
    !Number.isFinite(quantity) ||
    !Number.isFinite(price) ||
    quantity <= 0 ||
    price <= 0
  ) {
    return null
  }

  return quantity * price * resolvedContract.price_scale
}

export function calculateTransactionUnitPrice(
  contract: TransactionPriceContractInput | null | undefined,
  quantity: number,
  grossAmount: number,
) {
  const resolvedContract = normalizePriceContract(contract)
  if (
    !resolvedContract ||
    !Number.isFinite(quantity) ||
    !Number.isFinite(grossAmount) ||
    quantity <= 0 ||
    grossAmount <= 0
  ) {
    return null
  }

  return grossAmount / quantity / resolvedContract.price_scale
}
