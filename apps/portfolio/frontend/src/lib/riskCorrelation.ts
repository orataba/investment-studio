import {
  riskWindowStart,
  windowReturnPoints,
  type CalculationFrequency,
  type GroupReturnSeries,
} from './riskReturnAlignment'
import { assessRiskWindowCoverage } from './riskWindowCoverage'
import { returnWindowInputIssues, windowDiagnostics, type RiskWindowDiagnostics } from './riskWindowData'

export type CorrelationMatrix = {
  groups: Array<{
    key: string
    label: string
    observationCount: number
    weight: number | null
  }>
  cells: Array<Array<{ value: number | null; observationCount: number }>>
  maxAbs: number
}

export type CorrelationMatrixCoverageIssueReason =
  | 'scope_unavailable'
  | 'missing_member'
  | 'missing_weight'
  | 'missing_series'
  | 'window_coverage'
  | 'misaligned_dates'
  | 'calculation_unavailable'

export type CorrelationMatrixCoverageIssue = {
  memberKey: string
  memberLabel: string
  reason: CorrelationMatrixCoverageIssueReason
  coverageReason: string
  missingDates: string[]
  missingDateCount: number
}

export type CorrelationMatrixScope = {
  memberCount: number
  series: GroupReturnSeries[]
  issues: CorrelationMatrixCoverageIssue[]
}

export type CorrelationMatrixBuildResult = {
  matrix: CorrelationMatrix
  scopeMemberCount: number
  alignedObservationCount: number
  issues: CorrelationMatrixCoverageIssue[]
  diagnostics: RiskWindowDiagnostics
}

export function sampleCovariance(leftValues: number[], rightValues: number[]) {
  if (leftValues.length < 2 || rightValues.length !== leftValues.length) {
    return null
  }
  const leftMean = leftValues.reduce((total, value) => total + value, 0) / leftValues.length
  const rightMean = rightValues.reduce((total, value) => total + value, 0) / rightValues.length
  return (
    leftValues.reduce((total, leftValue, index) => total + (leftValue - leftMean) * (rightValues[index] - rightMean), 0) /
    (leftValues.length - 1)
  )
}

export function sampleCorrelation(leftValues: number[], rightValues: number[]) {
  const covariance = sampleCovariance(leftValues, rightValues)
  const leftVariance = sampleCovariance(leftValues, leftValues)
  const rightVariance = sampleCovariance(rightValues, rightValues)
  if (covariance == null || leftVariance == null || rightVariance == null || leftVariance <= 0 || rightVariance <= 0) {
    return null
  }
  return covariance / Math.sqrt(leftVariance * rightVariance)
}

function correlationCellFromDates(
  left: GroupReturnSeries,
  right: GroupReturnSeries,
  sampleDates: string[],
) {
  const leftValues = sampleDates.map((dateKey) => left.returnsByDate.get(dateKey))
  const rightValues = sampleDates.map((dateKey) => right.returnsByDate.get(dateKey))
  const leftFiniteValues = leftValues.filter(
    (value): value is number => typeof value === 'number' && Number.isFinite(value),
  )
  const rightFiniteValues = rightValues.filter(
    (value): value is number => typeof value === 'number' && Number.isFinite(value),
  )
  if (leftFiniteValues.length !== sampleDates.length || rightFiniteValues.length !== sampleDates.length) {
    return { value: null, observationCount: sampleDates.length }
  }
  const correlation = sampleCorrelation(leftFiniteValues, rightFiniteValues)
  if (correlation == null) {
    return { value: null, observationCount: sampleDates.length }
  }
  return {
    value: correlation,
    observationCount: sampleDates.length,
  }
}

export function returnWindowCoverage(
  series: GroupReturnSeries,
  asOfDate: string,
  lookbackDays: number,
  frequency: CalculationFrequency,
  parameters?: Record<string, unknown>,
) {
  const points = windowReturnPoints(series, asOfDate, lookbackDays)
  return assessRiskWindowCoverage(
    points.map((point) => point.date),
    asOfDate,
    lookbackDays,
    frequency,
    parameters,
    points[0]?.start_date,
  )
}

export function weightAtOrBefore(series: GroupReturnSeries, asOfDate: string) {
  let selectedDate = ''
  let selectedWeight: number | null = null
  series.endingWeightByDate.forEach((weight, dateKey) => {
    if (dateKey <= asOfDate && dateKey >= selectedDate) {
      selectedDate = dateKey
      selectedWeight = weight
    }
  })
  return selectedWeight
}

function emptyCorrelationMatrix() {
  return { groups: [], cells: [], maxAbs: 0 } satisfies CorrelationMatrix
}

export function correlationCoverageIssue({
  memberKey,
  memberLabel,
  reason,
  coverageReason,
  missingDates = [],
}: {
  memberKey: string
  memberLabel: string
  reason: CorrelationMatrixCoverageIssueReason
  coverageReason: string
  missingDates?: string[]
}): CorrelationMatrixCoverageIssue {
  return {
    memberKey,
    memberLabel,
    reason,
    coverageReason,
    missingDates,
    missingDateCount: missingDates.length,
  }
}

export function buildCorrelationMatrix(
  scope: CorrelationMatrixScope,
  asOfDate: string,
  lookbackDays: number,
  frequency: CalculationFrequency,
): CorrelationMatrixBuildResult {
  const scopeIssues = [...scope.issues]
  const diagnostics = (dates: string[], issues: CorrelationMatrixCoverageIssue[]) => windowDiagnostics({
    asOfDate, lookbackDays, frequency, dates, scopeMemberCount: scope.memberCount, issues,
    firstPeriodStartDate: dates.length ? scope.series[0]?.periodStartByDate.get(dates[0]) : null,
  })
  if (!asOfDate) {
    scopeIssues.push(
      correlationCoverageIssue({
        memberKey: 'scope',
        memberLabel: 'Selected scope',
        reason: 'scope_unavailable',
        coverageReason: 'Correlation matrix requires an as-of date.',
      }),
    )
    return {
      matrix: emptyCorrelationMatrix(),
      scopeMemberCount: scope.memberCount,
      alignedObservationCount: 0,
      issues: scopeIssues,
      diagnostics: diagnostics([], scopeIssues),
    }
  }
  if (scope.memberCount < 2) {
    scopeIssues.push(
      correlationCoverageIssue({
        memberKey: 'scope',
        memberLabel: 'Selected scope',
        reason: 'scope_unavailable',
        coverageReason: 'Correlation comparison requires at least two members in the selected scope.',
      }),
    )
  }

  const assessedSeries = scope.series
    .map((item) => ({
      item,
      coverage: returnWindowCoverage(item, asOfDate, lookbackDays, frequency),
      weight: weightAtOrBefore(item, asOfDate),
    }))
    .sort((left, right) => {
      const weightDelta = Math.abs(right.weight ?? 0) - Math.abs(left.weight ?? 0)
      return weightDelta || left.item.groupLabel.localeCompare(right.item.groupLabel)
    })
  assessedSeries.forEach(({ item, coverage }) => {
    scopeIssues.push(...returnWindowInputIssues(item, asOfDate, lookbackDays))
    if (!coverage.ok) {
      scopeIssues.push(
        correlationCoverageIssue({
          memberKey: item.groupKey,
          memberLabel: item.groupLabel,
          reason: 'window_coverage',
          coverageReason: coverage.error || 'Return window coverage is incomplete.',
        }),
      )
    }
  })

  assessedSeries.forEach(({ item }) => {
    const sourceMembers = item.sourceMembers ?? []
    if (sourceMembers.length <= 1) {
      return
    }
    const memberDates = [
      ...new Set(
        sourceMembers.flatMap((member) =>
          windowReturnPoints(member, asOfDate, lookbackDays).map((point) => point.date),
        ),
      ),
    ].sort()
    sourceMembers.forEach((member) => {
      const memberDateSet = new Set(
        windowReturnPoints(member, asOfDate, lookbackDays).map((point) => point.date),
      )
      const missingDates = memberDates.filter((dateKey) => !memberDateSet.has(dateKey))
      if (missingDates.length) {
        scopeIssues.push(
          correlationCoverageIssue({
            memberKey: member.groupKey,
            memberLabel: member.groupLabel,
            reason: 'misaligned_dates',
            coverageReason: `${item.groupLabel} contains members with different return dates.`,
            missingDates,
          }),
        )
      }
    })
    memberDates.forEach((dateKey) => {
      const starts = new Set(
        sourceMembers
          .filter((member) => member.returnsByDate.has(dateKey))
          .map((member) => member.periodStartByDate.get(dateKey) ?? null),
      )
      if (starts.size <= 1) {
        return
      }
      sourceMembers
        .filter((member) => member.returnsByDate.has(dateKey))
        .forEach((member) => {
          scopeIssues.push(
            correlationCoverageIssue({
              memberKey: member.groupKey,
              memberLabel: member.groupLabel,
              reason: 'misaligned_dates',
              coverageReason: `${item.groupLabel} contains mismatched period starts for the return ending ${dateKey}.`,
              missingDates: [dateKey],
            }),
          )
        })
    })
  })

  const windowStartDate = riskWindowStart(asOfDate, lookbackDays)
  const sampleDates = [
    ...new Set(
      assessedSeries.flatMap(({ item }) =>
        windowReturnPoints(item, asOfDate, lookbackDays).map((point) => point.date),
      ),
    ),
  ].filter((dateKey) => dateKey > windowStartDate && dateKey <= asOfDate).sort()
  assessedSeries.forEach(({ item }) => {
    const memberDateSet = new Set(windowReturnPoints(item, asOfDate, lookbackDays).map((point) => point.date))
    const missingDates = sampleDates.filter((dateKey) => !memberDateSet.has(dateKey))
    if (missingDates.length) {
      scopeIssues.push(
        correlationCoverageIssue({
          memberKey: item.groupKey,
          memberLabel: item.groupLabel,
          reason: 'misaligned_dates',
          coverageReason: 'Return dates do not match every other member in the selected scope.',
          missingDates,
        }),
      )
    }
  })
  sampleDates.forEach((dateKey) => {
    const periodStarts = new Set(
      assessedSeries
        .filter(({ item }) => item.returnsByDate.has(dateKey))
        .map(({ item }) => item.periodStartByDate.get(dateKey) ?? null),
    )
    if (periodStarts.size <= 1) {
      return
    }
    assessedSeries.forEach(({ item }) => {
      if (!item.returnsByDate.has(dateKey)) {
        return
      }
      const actualStart = item.periodStartByDate.get(dateKey) ?? null
      scopeIssues.push(
        correlationCoverageIssue({
          memberKey: item.groupKey,
          memberLabel: item.groupLabel,
          reason: 'misaligned_dates',
          coverageReason: `Return ending ${dateKey} starts at ${actualStart ?? 'an unknown date'}; scope members do not share one period identity.`,
          missingDates: [dateKey],
        }),
      )
    })
  })

  const sampleCoverage = assessRiskWindowCoverage(
    sampleDates, asOfDate, lookbackDays, frequency, undefined,
    sampleDates.length ? assessedSeries[0]?.item.periodStartByDate.get(sampleDates[0]) : null,
  )
  if (!sampleCoverage.ok && assessedSeries.length) {
    scopeIssues.push(
      correlationCoverageIssue({
        memberKey: 'scope',
        memberLabel: 'Selected scope',
        reason: 'window_coverage',
        coverageReason: sampleCoverage.error || 'The common return window is incomplete.',
      }),
    )
  }
  if (assessedSeries.length !== scope.memberCount && !scopeIssues.length) {
    scopeIssues.push(correlationCoverageIssue({
      memberKey: 'scope', memberLabel: 'Selected scope', reason: 'missing_member',
      coverageReason: 'The supplied return series do not include every member of the selected scope.',
    }))
  }
  if (scopeIssues.length) {
    return {
      matrix: emptyCorrelationMatrix(),
      scopeMemberCount: scope.memberCount,
      alignedObservationCount: 0,
      issues: scopeIssues,
      diagnostics: diagnostics(sampleDates, scopeIssues),
    }
  }

  let maxAbs = 0
  const calculationIssues: CorrelationMatrixCoverageIssue[] = []
  const cells = assessedSeries.map((rowSeries) =>
    assessedSeries.map((columnSeries) => {
      const cell = correlationCellFromDates(rowSeries.item, columnSeries.item, sampleDates)
      if (cell.value != null) {
        maxAbs = Math.max(maxAbs, Math.abs(cell.value))
      } else {
        calculationIssues.push(
          correlationCoverageIssue({
            memberKey: `${rowSeries.item.groupKey}:${columnSeries.item.groupKey}`,
            memberLabel: `${rowSeries.item.groupLabel} × ${columnSeries.item.groupLabel}`,
            reason: 'calculation_unavailable',
            coverageReason: 'Correlation is undefined because at least one member has zero return variation in this window.',
          }),
        )
      }
      return cell
    }),
  )

  if (calculationIssues.length) {
    return {
      matrix: emptyCorrelationMatrix(),
      scopeMemberCount: scope.memberCount,
      alignedObservationCount: sampleDates.length,
      issues: calculationIssues,
      diagnostics: diagnostics(sampleDates, calculationIssues),
    }
  }

  return {
    matrix: {
      groups: assessedSeries.map(({ item, coverage, weight }) => ({
        key: item.groupKey,
        label: item.groupLabel,
        observationCount: sampleCoverage.observationCount || coverage.observationCount,
        weight,
      })),
      cells,
      maxAbs,
    },
    scopeMemberCount: scope.memberCount,
    alignedObservationCount: sampleDates.length,
    issues: [],
    diagnostics: diagnostics(sampleDates, []),
  }
}
