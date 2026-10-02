import {
  buildCorrelationMatrix,
  type CorrelationMatrix,
  type CorrelationMatrixBuildResult,
  type CorrelationMatrixScope,
} from './riskCorrelation'
import { riskWindowStart } from './riskReturnAlignment'

type CorrelationPair = { key: string; value: number }

export type CorrelationObservation = {
  asOfDate: string
  windowStartDate: string
  observedStartDate: string | null
  observedEndDate: string | null
  firstPeriodStartDate: string | null
  averageCorrelation: number | null
  observationCount: number
  pairs: CorrelationPair[]
}

export type CorrelationHistoryObservation = {
  current: CorrelationObservation
  previous: CorrelationObservation
  memberCount: number
  pairCount: number
  risingPairCount: number | null
  averageChange: number | null
  status: 'available' | 'unavailable'
  unavailableReason: 'too_few_members' | 'current_window' | 'previous_window' | 'history_incomplete' | 'overlapping_periods' | 'flat_baseline' | null
  attention: boolean
  referenceWindowCount: number
  referenceChangeCount: number
  levelUpperFence: number | null
  changeUpperFence: number | null
}

// A full year of prior monthly observations is the disclosed product reference,
// not a statistical significance or crisis-prediction sample-size claim.
export const CORRELATION_REFERENCE_MONTHS = 12

function matrixPairs(matrix: CorrelationMatrix): CorrelationPair[] {
  return matrix.groups.flatMap((left, leftIndex) =>
    matrix.groups.slice(leftIndex + 1).flatMap((right, offset) => {
      const value = matrix.cells[leftIndex]?.[leftIndex + 1 + offset]?.value
      return typeof value === 'number' && Number.isFinite(value)
        ? [{ key: JSON.stringify([left.key, right.key].sort()), value }]
        : []
    }),
  ).sort((left, right) => left.key.localeCompare(right.key))
}

function observation(result: CorrelationMatrixBuildResult): CorrelationObservation {
  const pairs = result.issues.length ? [] : matrixPairs(result.matrix)
  const expectedPairCount = result.scopeMemberCount * (result.scopeMemberCount - 1) / 2
  return {
    asOfDate: result.diagnostics.asOfDate,
    windowStartDate: result.diagnostics.windowStartDate,
    observedStartDate: result.diagnostics.observedStartDate,
    observedEndDate: result.diagnostics.observedEndDate,
    firstPeriodStartDate: result.diagnostics.periodStartDate,
    averageCorrelation: pairs.length && pairs.length === expectedPairCount
      ? pairs.reduce((sum, pair) => sum + pair.value, 0) / pairs.length
      : null,
    observationCount: result.alignedObservationCount,
    pairs,
  }
}

function comparablePairSet(current: CorrelationObservation, previous: CorrelationObservation) {
  return current.averageCorrelation !== null && previous.averageCorrelation !== null
    && current.pairs.length === previous.pairs.length
    && current.pairs.every((pair, index) => pair.key === previous.pairs[index]?.key)
}

function nonOverlapping(current: CorrelationObservation, previous: CorrelationObservation) {
  return current.firstPeriodStartDate !== null && previous.observedEndDate !== null
    && current.firstPeriodStartDate >= previous.observedEndDate
}

function arithmeticPrecision(samples: CorrelationObservation[], pairCount: number) {
  // Pearson estimates reduce N observations, then the basket mean reduces P
  // pairs. Four reductions cover differences of those means and quartiles.
  // This is a floating-point error scale, not a minimum economic change.
  return Number.EPSILON * 4 * (Math.max(1, ...samples.map((sample) => sample.observationCount)) + pairCount)
}

function upperFence(values: number[], precision: number) {
  const sorted = [...values].sort((left, right) => left - right)
  const quantile = (probability: number) => {
    const index = probability * (sorted.length - 1)
    const lower = Math.floor(index)
    return sorted[lower] + (sorted[Math.ceil(index)] - sorted[lower]) * (index - lower)
  }
  const q1 = quantile(0.25)
  const q3 = quantile(0.75)
  return { value: q3 + 1.5 * (q3 - q1), dispersed: q3 - q1 > precision }
}

/**
 * Exploratory broad-rise observation, not a production risk model or loss forecast.
 * Uses one fixed basket and non-overlapping calendar-month return samples. Both
 * the mean pair-correlation level and its increase must exceed the prior year's
 * Tukey upper fence (Q3 + 1.5 IQR), with a majority of unique pairs rising.
 * Reference: https://www.itl.nist.gov/div898/handbook/eda/section3/boxplot.htm
 * No significance claim is made: pair correlations and monthly changes are dependent.
 */
export function buildCorrelationHistory(
  scope: CorrelationMatrixScope,
  asOfDate: string,
): CorrelationHistoryObservation {
  const current = observation(buildCorrelationMatrix(scope, asOfDate, 30, 'daily'))
  const previousDate = asOfDate ? riskWindowStart(asOfDate, 30) : ''
  const previous = observation(buildCorrelationMatrix(scope, previousDate, 30, 'daily'))
  const comparable = comparablePairSet(current, previous) && nonOverlapping(current, previous)
  const pairCount = scope.memberCount * (scope.memberCount - 1) / 2
  const precision = arithmeticPrecision([current, previous], pairCount)
  const result: CorrelationHistoryObservation = {
    current,
    previous,
    memberCount: scope.memberCount,
    pairCount,
    risingPairCount: comparable
      ? current.pairs.filter((pair, index) => pair.value - previous.pairs[index].value > precision).length
      : null,
    averageChange: comparable ? current.averageCorrelation! - previous.averageCorrelation! : null,
    status: 'unavailable',
    unavailableReason: null,
    attention: false,
    referenceWindowCount: 0,
    referenceChangeCount: 0,
    levelUpperFence: null,
    changeUpperFence: null,
  }
  if (scope.memberCount < 3) return { ...result, unavailableReason: 'too_few_members' }
  if (current.averageCorrelation === null) return { ...result, unavailableReason: 'current_window' }
  if (previous.averageCorrelation === null) return { ...result, unavailableReason: 'previous_window' }
  if (!comparable) return { ...result, unavailableReason: 'overlapping_periods' }

  const reference = [previous]
  for (let index = 1; index < CORRELATION_REFERENCE_MONTHS; index += 1) {
    const date = riskWindowStart(reference[index - 1].asOfDate, 30)
    reference.push(observation(buildCorrelationMatrix(scope, date, 30, 'daily')))
  }
  result.referenceWindowCount = reference.filter((sample) => sample.averageCorrelation !== null).length
  const changes: number[] = []
  let overlaps = false
  reference.slice(0, -1).forEach((sample, index) => {
    const older = reference[index + 1]
    if (!comparablePairSet(sample, older)) return
    if (!nonOverlapping(sample, older)) {
      overlaps = true
      return
    }
    changes.push(sample.averageCorrelation! - older.averageCorrelation!)
  })
  result.referenceChangeCount = changes.length
  if (result.referenceWindowCount !== CORRELATION_REFERENCE_MONTHS) return { ...result, unavailableReason: 'history_incomplete' }
  if (overlaps) return { ...result, unavailableReason: 'overlapping_periods' }
  if (changes.length !== CORRELATION_REFERENCE_MONTHS - 1) return { ...result, unavailableReason: 'history_incomplete' }

  const referencePrecision = arithmeticPrecision([current, ...reference], pairCount)
  const level = upperFence(reference.map((sample) => sample.averageCorrelation!), referencePrecision)
  const change = upperFence(changes, referencePrecision)
  result.levelUpperFence = level.value
  result.changeUpperFence = change.value
  if (!level.dispersed || !change.dispersed) return { ...result, unavailableReason: 'flat_baseline' }
  result.status = 'available'
  result.attention = current.averageCorrelation - level.value > referencePrecision
    && result.averageChange! > referencePrecision && result.averageChange! - change.value > referencePrecision
    && result.risingPairCount! > result.pairCount / 2
  return result
}
