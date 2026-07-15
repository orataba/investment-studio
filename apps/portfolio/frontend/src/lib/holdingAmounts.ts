import type { PortfolioHoldingRow } from './api'

export type HoldingAmountDisplay = {
  value: number | null
  currency: string
}

function finiteAmount(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function completeAmountSum(values: ReadonlyArray<number | null | undefined>) {
  let total = 0
  for (const value of values) {
    const resolvedValue = finiteAmount(value)
    if (resolvedValue == null) {
      return null
    }
    total += resolvedValue
  }
  return total
}

export function normalizedCurrency(value: string | null | undefined) {
  return String(value || '').trim().toUpperCase()
}

export function holdingCurrencyMatchesBase(row: PortfolioHoldingRow, baseCurrency: string) {
  return normalizedCurrency(row.instrument_core.currency) === normalizedCurrency(baseCurrency)
}

export function baseAmountForRow(
  row: PortfolioHoldingRow,
  baseCurrency: string,
  baseValue: number | null | undefined,
  localValue: number | null | undefined,
) {
  const resolvedBaseValue = finiteAmount(baseValue)
  if (resolvedBaseValue != null) {
    return resolvedBaseValue
  }
  return holdingCurrencyMatchesBase(row, baseCurrency) ? finiteAmount(localValue) : null
}

export function holdingAmountForDisplay(
  row: PortfolioHoldingRow,
  baseCurrency: string,
  baseValue: number | null | undefined,
  localValue: number | null | undefined,
): HoldingAmountDisplay {
  const resolvedBaseValue = baseAmountForRow(row, baseCurrency, baseValue, localValue)
  if (resolvedBaseValue != null) {
    return {
      value: resolvedBaseValue,
      currency: normalizedCurrency(baseCurrency),
    }
  }
  return {
    value: finiteAmount(localValue),
    currency: normalizedCurrency(row.instrument_core.currency),
  }
}
