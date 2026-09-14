import { riskWindowStart, windowReturnPoints, type CalculationFrequency, type GroupReturnSeries } from './riskReturnAlignment'
import { minReturnObservations } from './riskWindowCoverage'
import type { CorrelationMatrixCoverageIssue } from './riskCorrelation'

export type RiskWindowDiagnostics = {
  status: 'available' | 'unavailable'
  asOfDate: string
  windowStartDate: string
  observedStartDate: string | null
  observedEndDate: string | null
  periodStartDate: string | null
  observationCount: number
  requiredObservationCount: number
  scopeMemberCount: number
  issues: CorrelationMatrixCoverageIssue[]
}

export function windowIssue(memberKey: string, memberLabel: string, reason: CorrelationMatrixCoverageIssue['reason'], coverageReason: string, missingDates: string[] = []): CorrelationMatrixCoverageIssue {
  return { memberKey, memberLabel, reason, coverageReason, missingDates, missingDateCount: missingDates.length }
}

/** Inspect only periods used by this estimate. Source corruption outside it cannot invalidate it. */
export function returnWindowInputIssues(series: GroupReturnSeries, asOfDate: string, lookbackDays: number): CorrelationMatrixCoverageIssue[] {
  const start = riskWindowStart(asOfDate, lookbackDays)
  const points = (series.inputPoints ?? windowReturnPoints(series, asOfDate, lookbackDays))
    .filter((point) => !point.date || (point.date > start && point.date <= asOfDate))
  const issues: CorrelationMatrixCoverageIssue[] = []
  const add = (reason: CorrelationMatrixCoverageIssue['reason'], message: string, dates: string[] = []) =>
    issues.push(windowIssue(series.groupKey, series.groupLabel, reason, message, [...new Set(dates)].sort()))
  if (points.some((point) => !point.date)) add('missing_series', 'Return series contains an observation without a period end date.')
  const invalid = points.filter((point) => !Number.isFinite(point.value)).map((point) => point.date)
  if (invalid.length) add('missing_series', 'The selected window contains non-finite returns.', invalid)
  const dates = points.map((point) => point.date)
  const duplicates = dates.filter((date, index) => dates.indexOf(date) !== index)
  if (duplicates.length) add('misaligned_dates', 'The selected window contains duplicate period end dates.', duplicates)
  const invalidStarts = points.filter((point) => !point.start_date || point.start_date >= point.date).map((point) => point.date)
  if (invalidStarts.length) add('misaligned_dates', 'Return periods require a known start date before their end date.', invalidStarts)
  const gaps = (series.observationCoverage?.gap_dates ?? []).filter((date) =>
    (date > start && date <= asOfDate) || points.some((point) => point.start_date && date > point.start_date && date <= point.date),
  )
  if (gaps.length) add('window_coverage', 'The selected return window crosses missing source observations.', gaps)
  const unavailable = (series.unavailablePeriods ?? []).filter((period) => period.date > start && period.date <= asOfDate)
  if (unavailable.length) add('window_coverage', [...new Set(unavailable.map((period) => period.reason))].join(' '), unavailable.map((period) => period.date))
  for (const member of series.sourceMembers ?? []) issues.push(...returnWindowInputIssues(member, asOfDate, lookbackDays))
  return issues
}

export function windowDiagnostics({ asOfDate, lookbackDays, frequency, dates, firstPeriodStartDate, scopeMemberCount, issues }: {
  asOfDate: string; lookbackDays: number; frequency: CalculationFrequency; dates: string[]
  firstPeriodStartDate?: string | null; scopeMemberCount: number; issues: CorrelationMatrixCoverageIssue[]
}): RiskWindowDiagnostics {
  const ordered = [...new Set(dates)].sort()
  return { status: issues.length ? 'unavailable' : 'available', asOfDate, windowStartDate: asOfDate ? riskWindowStart(asOfDate, lookbackDays) : '',
    observedStartDate: ordered[0] ?? null, observedEndDate: ordered[ordered.length - 1] ?? null, periodStartDate: firstPeriodStartDate ?? null,
    observationCount: ordered.length, requiredObservationCount: minReturnObservations(frequency, lookbackDays), scopeMemberCount, issues }
}

/** One complete sample for the selected members; no pairwise date dropping. */
export function alignedWindowIssues(series: GroupReturnSeries[], asOfDate: string, lookbackDays: number) {
  const leaves = series.flatMap((item) => item.sourceMembers?.length ? item.sourceMembers : [item])
  const dates = [...new Set(leaves.flatMap((item) => windowReturnPoints(item, asOfDate, lookbackDays).map((point) => point.date)))].sort()
  const issues = series.flatMap((item) => returnWindowInputIssues(item, asOfDate, lookbackDays))
  for (const item of leaves) {
    const missing = dates.filter((date) => !item.returnsByDate.has(date))
    if (missing.length) issues.push(windowIssue(item.groupKey, item.groupLabel, 'misaligned_dates', 'Return dates do not match every other member in the selected window.', missing))
  }
  for (const date of dates) {
    const present = leaves.filter((item) => item.returnsByDate.has(date))
    const starts = new Set(present.map((item) => item.periodStartByDate.get(date) ?? null))
    if (starts.size > 1) for (const item of present) issues.push(windowIssue(item.groupKey, item.groupLabel, 'misaligned_dates', `Return ending ${date} does not share one period identity across the selected members.`, [date]))
  }
  return { dates, issues }
}
