export function realizedRiskMetricsAvailable(
  observationCount: number | null | undefined,
  annualizedVolatility: number | null | undefined,
) {
  return (
    Number.isFinite(observationCount) &&
    Number(observationCount) >= 2 &&
    Number.isFinite(annualizedVolatility)
  )
}

export function realizedRiskContributionResidual(
  values: Array<number | null | undefined>,
  riskMetricsAvailable: boolean,
) {
  const finiteValues = values.filter((value): value is number => Number.isFinite(value))
  if (!riskMetricsAvailable || finiteValues.length === 0) {
    return null
  }
  return 1 - finiteValues.reduce((total, value) => total + value, 0)
}
