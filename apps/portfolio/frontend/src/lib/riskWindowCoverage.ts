import {
  absoluteDayDiff,
  dayDiff,
  riskWindowStart,
  type CalculationFrequency,
} from './riskReturnAlignment'

const DAYS_PER_YEAR = 365.25
const RISK_MIN_WINDOW_COVERAGE_RATIO = 0.8
const RISK_MIN_OBSERVATION_COVERAGE_RATIO = 0.75
const RISK_WINDOW_MONTHS_BY_DAYS: Record<number, number> = {
  30: 1,
  90: 3,
  180: 6,
  366: 12,
  730: 24,
}
const RISK_OBSERVATIONS_PER_MONTH_BY_FREQUENCY: Record<CalculationFrequency, number> = {
  daily: 20,
  weekly: 4,
  monthly: 1,
}
const RISK_MAX_START_GAP_DAYS: Record<CalculationFrequency, number> = {
  daily: 10,
  weekly: 21,
  monthly: 45,
}
const RISK_MAX_TRAILING_STALENESS_DAYS: Record<CalculationFrequency, number> = {
  daily: 5,
  weekly: 14,
  monthly: 62,
}
const RISK_WINDOW_LABELS_BY_DAYS: Record<number, string> = {
  30: '1M',
  90: '3M',
  180: '6M',
  366: '12M',
  730: '24M',
}

export type RiskWindowCoverage = {
  ok: boolean
  observationCount: number
  error: string | null
}

export function windowLabel(lookbackDays: number) {
  return RISK_WINDOW_LABELS_BY_DAYS[lookbackDays] ?? `${lookbackDays}D`
}

export function riskMinObservationsForWindow(frequency: CalculationFrequency, lookbackDays: number) {
  const months = RISK_WINDOW_MONTHS_BY_DAYS[lookbackDays] ?? Math.max(lookbackDays, 1) / (DAYS_PER_YEAR / 12)
  const expectedObservations = months * RISK_OBSERVATIONS_PER_MONTH_BY_FREQUENCY[frequency]
  return Math.max(2, Math.ceil(expectedObservations * RISK_MIN_OBSERVATION_COVERAGE_RATIO))
}

export function minReturnObservations(
  frequency: CalculationFrequency,
  lookbackDays: number,
  parameters?: Record<string, unknown>,
) {
  const configured = parameters?.min_observations
  if (typeof configured === 'number' && Number.isFinite(configured)) {
    return Math.max(2, Math.floor(configured))
  }
  return riskMinObservationsForWindow(frequency, lookbackDays)
}

export function assessRiskWindowCoverage(
  dateKeys: string[],
  asOfDate: string,
  lookbackDays: number,
  frequency: CalculationFrequency,
  parameters?: Record<string, unknown>,
  firstPeriodStartDate?: string | null,
): RiskWindowCoverage {
  const sortedDates = [...new Set(dateKeys)].filter(Boolean).sort()
  const observationCount = sortedDates.length
  const minObservations = minReturnObservations(frequency, lookbackDays, parameters)
  if (!asOfDate) {
    return {
      ok: false,
      observationCount,
      error: 'Risk window requires an as-of date.',
    }
  }
  if (observationCount < minObservations) {
    return {
      ok: false,
      observationCount,
      error: `Risk window requires at least ${minObservations} ${frequency} observations; got ${observationCount}.`,
    }
  }
  const requiredStartDate = riskWindowStart(asOfDate, lookbackDays)
  const firstDate = sortedDates[0]
  const lastDate = sortedDates[sortedDates.length - 1]
  if (lastDate > asOfDate) {
    return {
      ok: false,
      observationCount,
      error: `Risk window contains an observation after its as-of date: ${lastDate}.`,
    }
  }
  const configuredTrailingStaleness = parameters?.max_trailing_staleness_days
  const maxTrailingStalenessDays =
    typeof configuredTrailingStaleness === 'number' && Number.isFinite(configuredTrailingStaleness)
      ? Math.max(0, Math.floor(configuredTrailingStaleness))
      : RISK_MAX_TRAILING_STALENESS_DAYS[frequency]
  const trailingStalenessDays = dayDiff(lastDate, asOfDate)
  if (trailingStalenessDays == null || trailingStalenessDays > maxTrailingStalenessDays) {
    return {
      ok: false,
      observationCount,
      error: `Risk window latest observation is ${trailingStalenessDays ?? 'unknown'} days before ${asOfDate}; maximum allowed for ${frequency} is ${maxTrailingStalenessDays} days.`,
    }
  }
  const coverageStartDate = firstPeriodStartDate || firstDate
  const startGapDays = absoluteDayDiff(coverageStartDate, requiredStartDate)
  if (startGapDays == null || startGapDays > RISK_MAX_START_GAP_DAYS[frequency]) {
    return {
      ok: false,
      observationCount,
      error: `Risk window lacks a valid ${frequency} start anchor near ${requiredStartDate}.`,
    }
  }
  const elapsedDays = dayDiff(coverageStartDate, lastDate)
  const requiredWindowDays = dayDiff(requiredStartDate, asOfDate)
  const minElapsedDays = Math.floor(
    (requiredWindowDays ?? lookbackDays) * RISK_MIN_WINDOW_COVERAGE_RATIO,
  )
  if (elapsedDays == null || elapsedDays <= 0 || elapsedDays < minElapsedDays) {
    return {
      ok: false,
      observationCount,
      error: `Risk window covers ${elapsedDays ?? 0} days; at least ${minElapsedDays} days are required for ${windowLabel(lookbackDays)}.`,
    }
  }
  return {
    ok: true,
    observationCount,
    error: null,
  }
}
