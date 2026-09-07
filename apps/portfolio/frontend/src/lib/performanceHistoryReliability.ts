import type { PortfolioPerformanceSummary } from './api'

const MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000

function isoDateUtc(value: string | null | undefined) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return null
  }
  const timestamp = Date.parse(`${value}T00:00:00Z`)
  if (!Number.isFinite(timestamp)) {
    return null
  }
  return new Date(timestamp).toISOString().slice(0, 10) === value ? timestamp : null
}

export type PerformanceHistoryReliability = {
  startDate: string | null
  endDate: string | null
  elapsedDays: number | null
  calendarSpanDays: number | null
  annualizedReturnEligible: boolean
  sampleLabel: string
  annualizationMessage: string | null
}

export function buildPerformanceHistoryReliability(
  summary: Pick<
    PortfolioPerformanceSummary,
    | 'start_date'
    | 'end_date'
    | 'snapshot_count'
    | 'return_observation_count'
    | 'risk_return_observation_count'
    | 'annualization_eligible'
    | 'annualization_unavailable_reason'
  >,
): PerformanceHistoryReliability {
  const startTimestamp = isoDateUtc(summary.start_date)
  const endTimestamp = isoDateUtc(summary.end_date)
  const elapsedDays =
    startTimestamp != null && endTimestamp != null && endTimestamp >= startTimestamp
      ? Math.round((endTimestamp - startTimestamp) / MILLISECONDS_PER_DAY)
      : null
  const calendarSpanDays = elapsedDays == null ? null : elapsedDays + 1
  const annualizedReturnEligible = summary.annualization_eligible
  const periodLabel =
    summary.start_date && summary.end_date ? `${summary.start_date} to ${summary.end_date}` : 'Observed period unavailable'
  const spanLabel = calendarSpanDays == null ? null : `${calendarSpanDays} calendar days`
  const sampleParts = [
    periodLabel,
    spanLabel,
    `${summary.snapshot_count} snapshots`,
    `${summary.return_observation_count} return observations`,
    `${summary.risk_return_observation_count} risk observations`,
  ].filter((value): value is string => Boolean(value))

  return {
    startDate: summary.start_date,
    endDate: summary.end_date,
    elapsedDays,
    calendarSpanDays,
    annualizedReturnEligible,
    sampleLabel: sampleParts.join(' · '),
    annualizationMessage: annualizedReturnEligible
      ? null
      : summary.annualization_unavailable_reason === 'measurement_period_shorter_than_one_year'
        ? 'Insufficient history: annualized TWR and IRR / MWRR require one full calendar-anniversary year. Period TWR remains the primary return.'
        : summary.annualization_unavailable_reason === 'operational_carrying_basis_not_annualized'
          ? 'Annualized TWR and IRR / MWRR require complete fair-value valuations. Additional history alone does not make carrying-basis returns comparable.'
        : 'Annualized TWR and IRR / MWRR are unavailable until a valid measurement period is established. Period TWR remains the primary return.',
  }
}
