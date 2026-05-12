import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useParams } from 'react-router-dom'

import BenchmarkSearchBox, {
  benchmarkInstrumentLabel,
  instrumentPrimaryIdentifier,
} from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import RollingRiskMetricChart, {
  type RiskChartDisplayStyle,
  type RollingRiskMetricPoint,
} from '../components/RollingRiskMetricChart'
import RiskTargetGapChart, { type RiskTargetGapChartRow } from '../components/RiskTargetGapChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  getHoldingsWorkspace,
  getPortfolioAccountsWorkspace,
  getPortfolioInstrumentPriceChart,
  getPortfolioInstruments,
  getPortfolioPerformance,
  getPortfolioPerformanceCalculationGroups,
  getPortfolioPerformanceContribution,
  getPortfolioTaxonomyCatalog,
  type HoldingsWorkspaceResponse,
  type PortfolioAccountsWorkspaceResponse,
  type PortfolioInstrumentPriceChartResponse,
  type PortfolioPerformanceCalculationGroupsResponse,
  type PortfolioContributionReportResponse,
  type PortfolioDailyPerformancePoint,
  type PortfolioPerformanceResponse,
  type PortfolioPerformanceCoverageState,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioTargetSetLineRecord,
  type PortfolioTargetSetRecord,
  type SharedInstrumentRecord,
  type TaxonomyAssignmentScope,
} from '../lib/api'
import { formatCurrency, formatLabel, formatNumber, formatPercent, signedValueClass } from '../lib/format'

const RISK_DATA_HISTORY_DAYS = 730
const DAYS_PER_YEAR = 365.25
const DEFAULT_RISK_LOOKBACK_DAYS = 90
const DEFAULT_RISK_MODEL_ID = 'ewma_vol_shrinkage_corr_covariance'

type RiskModelId = 'ewma_vol_shrinkage_corr_covariance' | 'ewma_covariance' | 'sample_covariance'
type RiskContributionMode = 'signed' | 'abs'
type CalculationFrequency = 'daily' | 'weekly' | 'monthly'

type RiskSettingsState = {
  lookbackDays: number
  modelId: RiskModelId
  contributionMode: RiskContributionMode
}

type RollingRiskSettingsState = RiskSettingsState & {
  chartStyle: RiskChartDisplayStyle
}

function hasChartStyle<TSettings extends RiskSettingsState>(
  settings: TSettings,
): settings is TSettings & RollingRiskSettingsState {
  return 'chartStyle' in settings && CHART_STYLE_OPTIONS.some((option) => option.value === settings.chartStyle)
}

type ReturnPoint = {
  date: string
  value: number
}

type PortfolioTaxonomyRecord = PortfolioTaxonomyCatalogResponse['taxonomies'][number]
type PortfolioTaxonomyAssignmentRecord = PortfolioTaxonomyCatalogResponse['taxonomy_assignments'][number]
type ContributionSlice = PortfolioContributionReportResponse['daily_slices'][number]

type ReturnSlice = Pick<
  ContributionSlice,
  | 'as_of_date'
  | 'group_key'
  | 'group_label'
  | 'coverage_state'
  | 'market_observation_count'
  | 'return_observation_eligible'
  | 'beginning_value_base'
  | 'ending_value_base'
  | 'beginning_weight'
  | 'ending_weight'
  | 'total_pnl'
  | 'daily_return'
  | 'daily_contribution'
>

type GroupReturnSeries = {
  groupKey: string
  groupLabel: string
  returnsByDate: Map<string, number>
  endingWeightByDate: Map<string, number>
  latestWeight: number | null
  observationCount: number
}

type RiskFrequencyProfile = {
  frequency: CalculationFrequency
  statusLabel: string
}

type CorrelationMatrix = {
  groups: Array<{
    key: string
    label: string
    observationCount: number
    weight: number | null
  }>
  cells: Array<Array<{ value: number | null; observationCount: number }>>
  maxAbs: number
}

type CurrentPlanningGroup = {
  groupKey: string
  label: string
  currentWeight: number | null
  currentValueBase: number | null
  hasMarketRiskInput: boolean
  hasCashLikeInput: boolean
}

type RiskContributionRow = {
  groupKey: string
  groupLabel: string
  weight: number | null
  annualizedVolatility: number | null
  riskShare: number | null
  contributionToVariance: number | null
  observationCount: number
}

type RiskCalculationResult<T> = {
  value: T
  errors: string[]
}

function riskOk<T>(value: T): RiskCalculationResult<T> {
  return { value, errors: [] }
}

function riskFail<T>(errors: string | string[], value: T): RiskCalculationResult<T> {
  return { value, errors: Array.isArray(errors) ? errors : [errors] }
}

const RISK_WINDOW_OPTIONS = [
  { value: 30, label: '1M', detail: '30D' },
  { value: 90, label: '1Q', detail: '90D' },
  { value: 180, label: '6M', detail: '180D' },
  { value: 366, label: '1Y', detail: '366D' },
] as const

const RISK_MODEL_OPTIONS: Array<{ value: RiskModelId; label: string; detail: string }> = [
  {
    value: 'ewma_vol_shrinkage_corr_covariance',
    label: 'Research EWMA',
    detail: 'EWMA vol + shrunk correlation',
  },
  { value: 'ewma_covariance', label: 'EWMA', detail: 'Exponentially weighted covariance' },
  { value: 'sample_covariance', label: 'Sample', detail: 'Sample covariance, n - 1' },
]

const CONTRIBUTION_MODE_OPTIONS: Array<{ value: RiskContributionMode; label: string; detail: string }> = [
  { value: 'signed', label: 'Signed', detail: 'Matches research primary mode' },
  { value: 'abs', label: 'Absolute', detail: 'Alternate view when signed shares are unstable' },
]

const CHART_STYLE_OPTIONS: Array<{ value: RiskChartDisplayStyle; label: string }> = [
  { value: 'mountain', label: 'Mountain' },
  { value: 'line', label: 'Line' },
  { value: 'dot', label: 'Dot' },
]

const DEFAULT_RISK_SETTINGS: RiskSettingsState = {
  lookbackDays: DEFAULT_RISK_LOOKBACK_DAYS,
  modelId: DEFAULT_RISK_MODEL_ID,
  contributionMode: 'signed',
}

const DEFAULT_ROLLING_SETTINGS: RollingRiskSettingsState = {
  ...DEFAULT_RISK_SETTINGS,
  chartStyle: 'mountain',
}

function localDateIso(input = new Date()) {
  const year = input.getFullYear()
  const month = `${input.getMonth() + 1}`.padStart(2, '0')
  const day = `${input.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

function shiftIsoDate(isoDate: string, days: number) {
  const [year, month, day] = isoDate.split('-').map(Number)
  const nextDate = new Date(year, (month || 1) - 1, day || 1)
  nextDate.setDate(nextDate.getDate() + days)
  return localDateIso(nextDate)
}

function riskWindowStart(asOfDate: string, lookbackDays: number) {
  return shiftIsoDate(asOfDate, -Math.max(lookbackDays - 1, 0))
}

function finiteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function dayDiff(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00`)
  const rightTime = Date.parse(`${right}T00:00:00`)
  if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
    return null
  }
  return Math.max(0, (rightTime - leftTime) / 86_400_000)
}

const CALCULATION_FREQUENCY_LABELS: Record<CalculationFrequency, string> = {
  daily: 'Daily',
  weekly: 'Weekly',
  monthly: 'Monthly',
}

function isCalculationFrequency(value: string | null | undefined): value is CalculationFrequency {
  return value === 'daily' || value === 'weekly' || value === 'monthly'
}

function riskFrequencyProfileFromCalculationGroups(
  calculationGroups: PortfolioPerformanceCalculationGroupsResponse | null,
): RiskCalculationResult<RiskFrequencyProfile> {
  const unavailableProfile = {
    frequency: 'daily',
    statusLabel: 'Risk basis unavailable',
  } satisfies RiskFrequencyProfile
  if (!calculationGroups) {
    return riskFail('Risk basis requires the performance calculation-groups response.', unavailableProfile)
  }
  const frequency = calculationGroups.summary.risk_calculation_frequency
  if (!isCalculationFrequency(frequency)) {
    return riskFail(`Risk basis response has invalid calculation frequency: ${frequency || 'missing'}.`, unavailableProfile)
  }
  return riskOk({
    frequency,
    statusLabel:
      calculationGroups.summary.risk_frequency_status_label ||
      `${CALCULATION_FREQUENCY_LABELS[frequency]} risk basis`,
  } satisfies RiskFrequencyProfile)
}

function periodEndKey(dateKey: string, frequency: CalculationFrequency, finalDate: string) {
  if (frequency === 'daily') {
    return dateKey
  }
  const [year, month, day] = dateKey.split('-').map(Number)
  const current = new Date(year, (month || 1) - 1, day || 1)
  if (frequency === 'weekly') {
    const dayOfWeek = current.getDay()
    const mondayBasedDay = dayOfWeek === 0 ? 6 : dayOfWeek - 1
    current.setDate(current.getDate() + (4 - mondayBasedDay))
  } else {
    current.setMonth(current.getMonth() + 1, 0)
  }
  const resolved = localDateIso(current)
  return resolved > finalDate ? finalDate : resolved
}

function compoundReturns(values: number[]) {
  return values.reduce((growth, value) => growth * (1 + value), 1) - 1
}

function alignReturnSeriesToFrequency(
  series: GroupReturnSeries[],
  frequency: CalculationFrequency,
  finalDate: string,
) {
  if (frequency === 'daily' || !finalDate) {
    return series
  }
  return series
    .map((item) => {
      const returnBuckets = new Map<string, number[]>()
      item.returnsByDate.forEach((value, dateKey) => {
        if (!Number.isFinite(value) || dateKey > finalDate) {
          return
        }
        const bucketKey = periodEndKey(dateKey, frequency, finalDate)
        const bucket = returnBuckets.get(bucketKey) ?? []
        bucket.push(value)
        returnBuckets.set(bucketKey, bucket)
      })
      const returnsByDate = new Map<string, number>()
      returnBuckets.forEach((values, bucketKey) => {
        if (values.length) {
          returnsByDate.set(bucketKey, compoundReturns(values))
        }
      })

      const endingWeightByDate = new Map<string, number>()
      item.endingWeightByDate.forEach((weight, dateKey) => {
        if (!Number.isFinite(weight) || dateKey > finalDate) {
          return
        }
        endingWeightByDate.set(periodEndKey(dateKey, frequency, finalDate), weight)
      })

      return {
        ...item,
        returnsByDate,
        endingWeightByDate,
        observationCount: returnsByDate.size,
      } satisfies GroupReturnSeries
    })
    .filter((item) => item.observationCount > 0)
}

function alignReturnPointsToFrequency(returnPoints: ReturnPoint[], frequency: CalculationFrequency, finalDate: string) {
  if (frequency === 'daily' || !finalDate) {
    return returnPoints
  }
  const buckets = new Map<string, number[]>()
  returnPoints.forEach((point) => {
    if (!Number.isFinite(point.value) || point.date > finalDate) {
      return
    }
    const bucketKey = periodEndKey(point.date, frequency, finalDate)
    const bucket = buckets.get(bucketKey) ?? []
    bucket.push(point.value)
    buckets.set(bucketKey, bucket)
  })
  return [...buckets.entries()]
    .map(([dateKey, values]) => ({ date: dateKey, value: compoundReturns(values) }))
    .sort((left, right) => left.date.localeCompare(right.date))
}

function annualizationPeriodsPerYear(dateKeys: string[], observationCount = dateKeys.length) {
  const sortedDates = [...dateKeys].sort()
  if (observationCount < 1 || sortedDates.length < 2) {
    return null
  }
  const elapsedDays = dayDiff(sortedDates[0], sortedDates[sortedDates.length - 1])
  if (elapsedDays == null) {
    return null
  }
  const gaps = sortedDates
    .slice(1)
    .map((dateKey, index) => dayDiff(sortedDates[index], dateKey))
    .filter((value): value is number => value != null && value > 0)
    .sort((left, right) => left - right)
  const medianGap = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 1
  const observationSpanDays = elapsedDays + medianGap
  return observationSpanDays > 0 ? (observationCount / observationSpanDays) * DAYS_PER_YEAR : null
}

function annualizedMeanReturn(values: number[], periodsPerYear: number | null) {
  if (!values.length || periodsPerYear == null) {
    return null
  }
  return (values.reduce((total, value) => total + value, 0) / values.length) * periodsPerYear
}

function sqrtNonNegative(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value < -1e-12) {
    return null
  }
  return Math.sqrt(value < 0 ? 0 : value)
}

function mergeCoverageState(states: PortfolioPerformanceCoverageState[]) {
  const normalizedStates = states.filter(Boolean)
  if (!normalizedStates.length) {
    return 'unavailable' satisfies PortfolioPerformanceCoverageState
  }
  if (normalizedStates.every((state) => state === 'complete')) {
    return 'complete' satisfies PortfolioPerformanceCoverageState
  }
  if (normalizedStates.some((state) => state === 'complete' || state === 'partial')) {
    return 'partial' satisfies PortfolioPerformanceCoverageState
  }
  return 'unavailable' satisfies PortfolioPerformanceCoverageState
}

function windowLabel(lookbackDays: number) {
  return RISK_WINDOW_OPTIONS.find((option) => option.value === lookbackDays)?.label ?? `${lookbackDays}D`
}

function riskModelLabel(modelId: RiskModelId) {
  return RISK_MODEL_OPTIONS.find((option) => option.value === modelId)?.label ?? formatLabel(modelId)
}

function signedPercent(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }
  const absolute = formatPercent(Math.abs(value), digits)
  if (value > 0) {
    return `+${absolute}`
  }
  if (value < 0) {
    return `-${absolute}`
  }
  return absolute
}

function buildPortfolioReturnPoints(dailySeries: PortfolioDailyPerformancePoint[]) {
  return dailySeries
    .filter((point) => finiteNumber(point.daily_twr) != null && point.return_observation_eligible)
    .map((point) => ({ date: point.as_of_date, value: point.daily_twr as number }))
    .sort((left, right) => left.date.localeCompare(right.date))
}

function buildBenchmarkReturnPoints(chart: PortfolioInstrumentPriceChartResponse | null) {
  const points = (chart?.points ?? [])
    .filter((point) => point.date && Number.isFinite(point.value))
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
  const returns: ReturnPoint[] = []
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]
    const current = points[index]
    if (previous.value > 0 && current.value > 0) {
      returns.push({ date: current.date, value: current.value / previous.value - 1 })
    }
  }
  return returns
}

function returnPointsInWindow(returnPoints: ReturnPoint[], asOfDate: string, lookbackDays: number) {
  if (!asOfDate) {
    return []
  }
  const startDate = riskWindowStart(asOfDate, lookbackDays)
  return returnPoints.filter((point) => point.date >= startDate && point.date <= asOfDate)
}

function pairWindowReturns(
  left: Map<string, number>,
  right: Map<string, number>,
  asOfDate: string,
  lookbackDays: number,
) {
  const startDate = riskWindowStart(asOfDate, lookbackDays)
  const pairs: Array<{ date: string; left: number; right: number }> = []
  left.forEach((leftValue, dateKey) => {
    if (dateKey < startDate || dateKey > asOfDate) {
      return
    }
    const rightValue = right.get(dateKey)
    if (rightValue != null && Number.isFinite(leftValue) && Number.isFinite(rightValue)) {
      pairs.push({ date: dateKey, left: leftValue, right: rightValue })
    }
  })
  return pairs.sort((leftPair, rightPair) => leftPair.date.localeCompare(rightPair.date))
}

function weightedMean(values: number[], weights: number[]) {
  const totalWeight = weights.reduce((total, weight) => total + weight, 0)
  if (totalWeight <= 0) {
    return null
  }
  return values.reduce((total, value, index) => total + value * weights[index], 0) / totalWeight
}

function sampleCovariance(leftValues: number[], rightValues: number[]) {
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

function ewmaCovariance(leftValues: number[], rightValues: number[], decay: number) {
  if (leftValues.length < 2 || rightValues.length !== leftValues.length || decay <= 0 || decay >= 1) {
    return null
  }
  const weights = leftValues.map((_, index) => Math.pow(decay, leftValues.length - index - 1))
  const leftMean = weightedMean(leftValues, weights)
  const rightMean = weightedMean(rightValues, weights)
  const totalWeight = weights.reduce((total, weight) => total + weight, 0)
  if (leftMean == null || rightMean == null || totalWeight <= 0) {
    return null
  }
  return leftValues.reduce(
    (total, leftValue, index) => total + weights[index] * (leftValue - leftMean) * (rightValues[index] - rightMean),
    0,
  ) / totalWeight
}

function sampleCorrelation(leftValues: number[], rightValues: number[]) {
  const covariance = sampleCovariance(leftValues, rightValues)
  const leftVariance = sampleCovariance(leftValues, leftValues)
  const rightVariance = sampleCovariance(rightValues, rightValues)
  if (covariance == null || leftVariance == null || rightVariance == null || leftVariance <= 0 || rightVariance <= 0) {
    return null
  }
  return covariance / Math.sqrt(leftVariance * rightVariance)
}

function estimateCovarianceFromValues(leftValues: number[], rightValues: number[], modelId: RiskModelId) {
  if (leftValues.length < 2 || rightValues.length !== leftValues.length) {
    return null
  }
  if (modelId === 'sample_covariance') {
    return sampleCovariance(leftValues, rightValues)
  }
  if (modelId === 'ewma_covariance') {
    return ewmaCovariance(leftValues, rightValues, 0.94)
  }

  const leftVariance = ewmaCovariance(leftValues, leftValues, 0.97)
  const rightVariance = ewmaCovariance(rightValues, rightValues, 0.97)
  if (leftVariance == null || rightVariance == null || leftVariance < 0 || rightVariance < 0) {
    return null
  }
  if (leftValues === rightValues || leftValues.every((value, index) => value === rightValues[index])) {
    return leftVariance
  }
  const correlation = sampleCorrelation(leftValues, rightValues)
  if (correlation == null) {
    return null
  }
  const shrunkCorrelation = correlation * 0.85
  return shrunkCorrelation * Math.sqrt(Math.max(leftVariance, 0)) * Math.sqrt(Math.max(rightVariance, 0))
}

function estimateCorrelationFromValues(leftValues: number[], rightValues: number[], modelId: RiskModelId) {
  if (leftValues.length < 2 || rightValues.length !== leftValues.length) {
    return null
  }
  if (modelId === 'sample_covariance') {
    return sampleCorrelation(leftValues, rightValues)
  }
  if (modelId === 'ewma_covariance') {
    const covariance = ewmaCovariance(leftValues, rightValues, 0.94)
    const leftVariance = ewmaCovariance(leftValues, leftValues, 0.94)
    const rightVariance = ewmaCovariance(rightValues, rightValues, 0.94)
    if (covariance == null || leftVariance == null || rightVariance == null || leftVariance <= 0 || rightVariance <= 0) {
      return null
    }
    return covariance / Math.sqrt(leftVariance * rightVariance)
  }

  const correlation = sampleCorrelation(leftValues, rightValues)
  return correlation == null ? null : correlation * 0.85
}

function windowReturnPoints(series: GroupReturnSeries, asOfDate: string, lookbackDays: number) {
  const startDate = riskWindowStart(asOfDate, lookbackDays)
  const points: ReturnPoint[] = []
  series.returnsByDate.forEach((value, dateKey) => {
    if (dateKey >= startDate && dateKey <= asOfDate && Number.isFinite(value)) {
      points.push({ date: dateKey, value })
    }
  })
  return points.sort((left, right) => left.date.localeCompare(right.date))
}

function annualizedVarianceFromValues(values: number[], dates: string[], modelId: RiskModelId) {
  if (values.length < 2) {
    return null
  }
  const variance =
    modelId === 'sample_covariance'
      ? sampleCovariance(values, values)
      : ewmaCovariance(values, values, modelId === 'ewma_covariance' ? 0.94 : 0.97)
  const periodsPerYear = annualizationPeriodsPerYear(dates, values.length)
  return variance == null || periodsPerYear == null ? null : variance * periodsPerYear
}

function annualizedCovarianceFromValues(
  leftValues: number[],
  rightValues: number[],
  dates: string[],
  modelId: RiskModelId,
) {
  const covariance = estimateCovarianceFromValues(leftValues, rightValues, modelId)
  const periodsPerYear = annualizationPeriodsPerYear(dates, leftValues.length)
  return covariance == null || periodsPerYear == null ? null : covariance * periodsPerYear
}

function estimateWindowRisk(returnPoints: ReturnPoint[], asOfDate: string, lookbackDays: number, modelId: RiskModelId) {
  const windowPoints = returnPointsInWindow(returnPoints, asOfDate, lookbackDays)
  const values = windowPoints.map((point) => point.value)
  const dates = windowPoints.map((point) => point.date)
  if (values.length < 2) {
    return { volatility: null, sharpe: null, observationCount: values.length }
  }
  const variance = annualizedVarianceFromValues(values, dates, modelId)
  const periodsPerYear = annualizationPeriodsPerYear(dates, values.length)
  if (variance == null || periodsPerYear == null) {
    return { volatility: null, sharpe: null, observationCount: values.length }
  }
  const volatility = sqrtNonNegative(variance)
  if (volatility == null) {
    return { volatility: null, sharpe: null, observationCount: values.length }
  }
  const annualizedMean = annualizedMeanReturn(values, periodsPerYear)
  return {
    volatility,
    sharpe: annualizedMean != null && volatility > 1e-12 ? annualizedMean / volatility : null,
    observationCount: values.length,
  }
}

function buildRollingMetricPoints(
  returnPoints: ReturnPoint[],
  lookbackDays: number,
  modelId: RiskModelId,
  metric: 'volatility' | 'sharpe',
) {
  const sortedPoints = returnPoints.slice().sort((left, right) => left.date.localeCompare(right.date))
  const rollingPoints: RollingRiskMetricPoint[] = []
  sortedPoints.forEach((point) => {
    const risk = estimateWindowRisk(sortedPoints, point.date, lookbackDays, modelId)
    const value = metric === 'volatility' ? risk.volatility : risk.sharpe
    if (value != null && Number.isFinite(value)) {
      rollingPoints.push({ date: point.date, value })
    }
  })
  return rollingPoints
}

function covarianceCell(
  left: GroupReturnSeries,
  right: GroupReturnSeries,
  asOfDate: string,
  lookbackDays: number,
  modelId: RiskModelId,
) {
  if (left.groupKey === right.groupKey) {
    const points = windowReturnPoints(left, asOfDate, lookbackDays)
    const values = points.map((point) => point.value)
    const dates = points.map((point) => point.date)
    return {
      value: annualizedVarianceFromValues(values, dates, modelId),
      observationCount: values.length,
    }
  }

  const pairs = pairWindowReturns(left.returnsByDate, right.returnsByDate, asOfDate, lookbackDays)
  if (pairs.length < 2) {
    return { value: null, observationCount: pairs.length }
  }
  const leftValues = pairs.map((pair) => pair.left)
  const rightValues = pairs.map((pair) => pair.right)
  const dates = pairs.map((pair) => pair.date)
  if (modelId === 'ewma_vol_shrinkage_corr_covariance') {
    const leftWindowPoints = windowReturnPoints(left, asOfDate, lookbackDays)
    const rightWindowPoints = windowReturnPoints(right, asOfDate, lookbackDays)
    const leftVariance = annualizedVarianceFromValues(
      leftWindowPoints.map((point) => point.value),
      leftWindowPoints.map((point) => point.date),
      modelId,
    )
    const rightVariance = annualizedVarianceFromValues(
      rightWindowPoints.map((point) => point.value),
      rightWindowPoints.map((point) => point.date),
      modelId,
    )
    const correlation = estimateCorrelationFromValues(leftValues, rightValues, modelId)
    if (leftVariance == null || rightVariance == null || correlation == null) {
      return { value: null, observationCount: pairs.length }
    }
    const leftVolatility = sqrtNonNegative(leftVariance)
    const rightVolatility = sqrtNonNegative(rightVariance)
    if (leftVolatility == null || rightVolatility == null) {
      return { value: null, observationCount: pairs.length }
    }
    return {
      value: correlation * leftVolatility * rightVolatility,
      observationCount: pairs.length,
    }
  }
  return {
    value: annualizedCovarianceFromValues(leftValues, rightValues, dates, modelId),
    observationCount: pairs.length,
  }
}

function correlationCell(
  left: GroupReturnSeries,
  right: GroupReturnSeries,
  asOfDate: string,
  lookbackDays: number,
  modelId: RiskModelId,
) {
  const pairs = pairWindowReturns(left.returnsByDate, right.returnsByDate, asOfDate, lookbackDays)
  if (pairs.length < 2) {
    return { value: null, observationCount: pairs.length }
  }
  const leftValues = pairs.map((pair) => pair.left)
  const rightValues = pairs.map((pair) => pair.right)
  const correlation = estimateCorrelationFromValues(leftValues, rightValues, modelId)
  if (correlation == null) {
    return { value: null, observationCount: pairs.length }
  }
  return {
    value: correlation,
    observationCount: pairs.length,
  }
}

function returnObservationCount(series: GroupReturnSeries, asOfDate: string, lookbackDays: number) {
  const startDate = riskWindowStart(asOfDate, lookbackDays)
  let count = 0
  series.returnsByDate.forEach((value, dateKey) => {
    if (dateKey >= startDate && dateKey <= asOfDate && Number.isFinite(value)) {
      count += 1
    }
  })
  return count
}

function weightAtOrBefore(series: GroupReturnSeries, asOfDate: string) {
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

function buildCorrelationMatrix(
  series: GroupReturnSeries[],
  asOfDate: string,
  settings: RiskSettingsState,
) {
  if (!asOfDate) {
    return { groups: [], cells: [], maxAbs: 0 } satisfies CorrelationMatrix
  }
  const activeSeries = series
    .map((item) => ({
      item,
      observationCount: returnObservationCount(item, asOfDate, settings.lookbackDays),
      weight: weightAtOrBefore(item, asOfDate),
    }))
    .filter((item) => item.observationCount >= 2)
    .sort((left, right) => {
      const weightDelta = Math.abs(right.weight ?? 0) - Math.abs(left.weight ?? 0)
      return weightDelta || left.item.groupLabel.localeCompare(right.item.groupLabel)
    })

  let maxAbs = 0
  const cells = activeSeries.map((rowSeries) =>
    activeSeries.map((columnSeries) => {
      const cell =
        rowSeries.item.groupKey === columnSeries.item.groupKey
          ? { value: 1, observationCount: rowSeries.observationCount }
          : correlationCell(
              rowSeries.item,
              columnSeries.item,
              asOfDate,
              settings.lookbackDays,
              settings.modelId,
            )
      if (cell.value != null) {
        maxAbs = Math.max(maxAbs, Math.abs(cell.value))
      }
      return cell
    }),
  )

  return {
    groups: activeSeries.map(({ item, observationCount, weight }) => ({
      key: item.groupKey,
      label: item.groupLabel,
      observationCount,
      weight,
    })),
    cells,
    maxAbs,
  } satisfies CorrelationMatrix
}

function buildGroupReturnSeries(slices: ReturnSlice[]) {
  const lookup = new Map<
    string,
    {
      groupKey: string
      groupLabel: string
      returnsByDate: Map<string, number>
      endingWeightByDate: Map<string, number>
      latestWeight: number | null
    }
  >()

  slices
    .slice()
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
    .forEach((slice) => {
      const groupKey = slice.group_key
      const current = lookup.get(groupKey) ?? {
        groupKey,
        groupLabel: slice.group_label || groupKey,
        returnsByDate: new Map<string, number>(),
        endingWeightByDate: new Map<string, number>(),
        latestWeight: null,
      }
      current.groupLabel = slice.group_label || current.groupLabel

      const dailyReturn = finiteNumber(slice.daily_return)
      if (dailyReturn != null && slice.return_observation_eligible) {
        current.returnsByDate.set(slice.as_of_date, dailyReturn)
      }

      const endingWeight = finiteNumber(slice.ending_weight)
      if (endingWeight != null) {
        current.endingWeightByDate.set(slice.as_of_date, endingWeight)
        current.latestWeight = endingWeight
      }

      lookup.set(groupKey, current)
    })

  return [...lookup.values()]
    .map((item) => ({
      ...item,
      observationCount: item.returnsByDate.size,
    }))
    .filter((item) => item.observationCount > 0)
    .sort((left, right) => {
      const weightDelta = Math.abs(right.latestWeight ?? 0) - Math.abs(left.latestWeight ?? 0)
      return weightDelta || left.groupLabel.localeCompare(right.groupLabel)
    }) satisfies GroupReturnSeries[]
}

function buildRiskContributionRows(
  series: GroupReturnSeries[],
  asOfDate: string,
  settings: RiskSettingsState,
) {
  if (!asOfDate) {
    return riskFail('Risk contribution requires an as-of date.', [] satisfies RiskContributionRow[])
  }
  const weightedSeries = series
    .map((item) => ({
      item,
      weight: weightAtOrBefore(item, asOfDate),
      observationCount: returnObservationCount(item, asOfDate, settings.lookbackDays),
    }))
    .filter((item) => Math.abs(item.weight ?? 0) > 1e-9)
  const insufficientSeries = weightedSeries.filter((item) => item.observationCount < 2)
  if (insufficientSeries.length) {
    return riskFail(
      `Risk contribution requires at least two return observations for every active weighted group; insufficient: ${insufficientSeries
        .map(({ item, observationCount }) => `${item.groupLabel} (${observationCount})`)
        .join(', ')}.`,
      [] satisfies RiskContributionRow[],
    )
  }
  const activeSeries = weightedSeries
  const grossWeight = activeSeries.reduce((total, item) => total + Math.abs(item.weight ?? 0), 0)
  if (!activeSeries.length || grossWeight <= 1e-12) {
    return riskFail(
      'Risk contribution requires at least one active weighted group with valid point-in-time weight.',
      [] satisfies RiskContributionRow[],
    )
  }

  const weights = activeSeries.map((item) => (item.weight ?? 0) / grossWeight)
  const firstActiveSeries = activeSeries[0]
  if (!firstActiveSeries) {
    return riskFail('Risk contribution requires at least one active weighted group.', [] satisfies RiskContributionRow[])
  }
  const startDate = riskWindowStart(asOfDate, settings.lookbackDays)
  const commonDates = [...firstActiveSeries.item.returnsByDate.keys()]
    .filter((dateKey) => dateKey >= startDate && dateKey <= asOfDate)
    .filter((dateKey) =>
      activeSeries.every((series) => {
        const value = series.item.returnsByDate.get(dateKey)
        return value != null && Number.isFinite(value)
      }),
    )
    .sort()
  if (commonDates.length < 2) {
    return riskFail(
      `Risk contribution requires at least two common return dates across all active weighted groups; got ${commonDates.length}.`,
      [] satisfies RiskContributionRow[],
    )
  }
  const valuesByGroup = new Map(
    activeSeries.map((series) => [
      series.item.groupKey,
      commonDates.map((dateKey) => series.item.returnsByDate.get(dateKey) as number),
    ]),
  )
  const covarianceMatrix: number[][] = []
  for (const rowSeries of activeSeries) {
    const covarianceRow: number[] = []
    for (const columnSeries of activeSeries) {
      const leftValues = valuesByGroup.get(rowSeries.item.groupKey)
      const rightValues = valuesByGroup.get(columnSeries.item.groupKey)
      if (!leftValues || !rightValues) {
        return riskFail(
          `Risk contribution internal return lookup failed for ${rowSeries.item.groupLabel} x ${columnSeries.item.groupLabel}.`,
          [] satisfies RiskContributionRow[],
        )
      }
      const covarianceValue = annualizedCovarianceFromValues(leftValues, rightValues, commonDates, settings.modelId)
      if (covarianceValue == null || !Number.isFinite(covarianceValue)) {
        return riskFail(
          `Risk contribution covariance failed for ${rowSeries.item.groupLabel} x ${columnSeries.item.groupLabel}.`,
          [] satisfies RiskContributionRow[],
        )
      }
      covarianceRow.push(covarianceValue)
    }
    covarianceMatrix.push(covarianceRow)
  }
  const marginal = covarianceMatrix.map((row) =>
    row.reduce((total, covarianceValue, columnIndex) => total + covarianceValue * weights[columnIndex], 0),
  )
  const variance = weights.reduce((total, weight, index) => total + weight * marginal[index], 0)
  if (!Number.isFinite(variance) || variance <= 1e-12) {
    return riskFail(
      `Risk contribution requires positive finite portfolio variance; got ${Number.isFinite(variance) ? formatNumber(variance, 6) : 'non-finite'}.`,
      [] satisfies RiskContributionRow[],
    )
  }
  const signedContributions = activeSeries.map((_, index) => weights[index] * marginal[index])
  const absoluteContributionTotal = signedContributions.reduce((total, contribution) => total + Math.abs(contribution), 0)
  if (settings.contributionMode === 'abs' && absoluteContributionTotal <= 1e-12) {
    return riskFail(
      'Absolute risk contribution requires a positive aggregate absolute contribution.',
      [] satisfies RiskContributionRow[],
    )
  }
  const invalidOwnVariance = activeSeries.find(({ item }, index) => sqrtNonNegative(covarianceMatrix[index]?.[index]) == null)
  if (invalidOwnVariance) {
    return riskFail(
      `Risk contribution produced invalid own variance for ${invalidOwnVariance.item.groupLabel}.`,
      [] satisfies RiskContributionRow[],
    )
  }

  const rows = activeSeries
    .map(({ item }, index) => {
      const ownVariance = covarianceMatrix[index]?.[index] ?? null
      const ownVolatility = sqrtNonNegative(ownVariance) as number
      const contributionToVariance = signedContributions[index]
      const riskShare =
        settings.contributionMode === 'abs'
          ? Math.abs(contributionToVariance) / absoluteContributionTotal
          : contributionToVariance / variance
      return {
        groupKey: item.groupKey,
        groupLabel: item.groupLabel,
        weight: weights[index],
        annualizedVolatility: ownVolatility,
        riskShare,
        contributionToVariance,
        observationCount: commonDates.length,
      } satisfies RiskContributionRow
    })
    .sort((left, right) => Math.abs(right.riskShare ?? 0) - Math.abs(left.riskShare ?? 0))
  return riskOk(rows)
}

function buildNodeLookup(catalog: PortfolioTaxonomyCatalogResponse | null, taxonomyId: string | null | undefined) {
  return new Map(
    (catalog?.taxonomy_nodes ?? [])
      .filter((node) => node.taxonomy_id === taxonomyId && node.status === 'active')
      .map((node) => [node.taxonomy_node_id, node] as const),
  )
}

function buildNodePath(nodeId: string | null | undefined, nodeById: Map<string, PortfolioTaxonomyNodeRecord>) {
  const path: PortfolioTaxonomyNodeRecord[] = []
  let current = nodeId ? nodeById.get(nodeId) ?? null : null
  let guard = 0
  while (current && guard < 100) {
    path.unshift(current)
    current = current.parent_taxonomy_node_id ? nodeById.get(current.parent_taxonomy_node_id) ?? null : null
    guard += 1
  }
  return path
}

function isRecordActive(effectiveFrom?: string | null, effectiveTo?: string | null, referenceDate?: string | null) {
  if (!referenceDate) {
    return true
  }
  if (effectiveFrom && effectiveFrom > referenceDate) {
    return false
  }
  if (effectiveTo && effectiveTo < referenceDate) {
    return false
  }
  return true
}

function resolveActiveAssignment(
  catalog: PortfolioTaxonomyCatalogResponse | null,
  taxonomyId: string,
  targetScope: TaxonomyAssignmentScope,
  entityId: string,
  referenceDate: string,
): { assignment: PortfolioTaxonomyAssignmentRecord | null; error: string | null } {
  const matches = (catalog?.taxonomy_assignments ?? [])
    .filter(
      (assignment) =>
        assignment.taxonomy_id === taxonomyId &&
        assignment.target_scope === targetScope &&
        assignment.target_entity_id === entityId &&
        assignment.status === 'active' &&
        isRecordActive(assignment.effective_from, assignment.effective_to, referenceDate),
    )
    .sort(
      (left, right) =>
        (right.effective_from ?? '').localeCompare(left.effective_from ?? '') ||
        (right.effective_to ?? '').localeCompare(left.effective_to ?? '') ||
        right.assignment_id.localeCompare(left.assignment_id),
    )

  if (matches.length > 1) {
    return {
      assignment: null,
      error: `Multiple active taxonomy assignments found for ${targetScope} ${entityId} in taxonomy ${taxonomyId} on ${referenceDate}.`,
    }
  }
  return { assignment: matches[0] ?? null, error: null }
}

function resolveScopedTaxonomyNode(
  assignmentNodeId: string | null | undefined,
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>,
  scopeNodeId: string,
) {
  const path = buildNodePath(assignmentNodeId, nodeById)
  if (!path.length) {
    return null
  }
  if (!scopeNodeId) {
    return path[0]
  }
  const scopeIndex = path.findIndex((node) => node.taxonomy_node_id === scopeNodeId)
  if (scopeIndex < 0) {
    return null
  }
  return path[scopeIndex + 1] ?? path[scopeIndex] ?? null
}

function addMeasure(current: number | null, value: number | null) {
  return (current ?? 0) + (value ?? 0)
}

function buildScopedTaxonomySlices(
  slices: ReturnSlice[],
  catalog: PortfolioTaxonomyCatalogResponse | null,
  taxonomy: PortfolioTaxonomyRecord | null,
  scopeNodeId = '',
) {
  if (!catalog || !taxonomy || taxonomy.primary_assignment_scope !== 'instrument') {
    return riskOk([] satisfies ReturnSlice[])
  }

  const nodeById = buildNodeLookup(catalog, taxonomy.taxonomy_id)
  const errors: string[] = []
  const grouped = new Map<
    string,
    {
      as_of_date: string
      group_key: string
      group_label: string
      beginning_value_base: number | null
      ending_value_base: number | null
      beginning_weight: number | null
      ending_weight: number | null
      total_pnl: number | null
      daily_contribution: number | null
      coverage_state: PortfolioPerformanceCoverageState
      market_observation_count: number
      return_observation_eligible: boolean
    }
  >()
  const coverageStatesByGroup = new Map<string, PortfolioPerformanceCoverageState[]>()

  slices.forEach((slice) => {
    const assignmentResult = resolveActiveAssignment(
      catalog,
      taxonomy.taxonomy_id,
      'instrument',
      slice.group_key,
      slice.as_of_date,
    )
    if (assignmentResult.error) {
      errors.push(assignmentResult.error)
      return
    }
    const assignment = assignmentResult.assignment
    const scopedNode = resolveScopedTaxonomyNode(assignment?.taxonomy_node_id, nodeById, scopeNodeId)
    if (!scopedNode) {
      if (scopeNodeId) {
        return
      }
    }
    const beginningValue = finiteNumber(slice.beginning_value_base)
    const endingValue = finiteNumber(slice.ending_value_base)
    const beginningWeight = finiteNumber(slice.beginning_weight)
    const endingWeight = finiteNumber(slice.ending_weight)
    const totalPnl = finiteNumber(slice.total_pnl)
    const dailyContribution = finiteNumber(slice.daily_contribution)
    const marketObservationCount = finiteNumber(slice.market_observation_count)
    if (
      beginningValue == null ||
      endingValue == null ||
      beginningWeight == null ||
      endingWeight == null ||
      totalPnl == null ||
      dailyContribution == null ||
      marketObservationCount == null ||
      marketObservationCount < 0
    ) {
      errors.push(
        `Taxonomy risk aggregation has incomplete numeric contribution data for ${slice.group_label || slice.group_key} on ${slice.as_of_date}.`,
      )
      return
    }
    if (!slice.coverage_state) {
      errors.push(`Taxonomy risk aggregation is missing coverage state for ${slice.group_label || slice.group_key} on ${slice.as_of_date}.`)
      return
    }
    const groupKey = scopedNode?.taxonomy_node_id ?? `unassigned:${taxonomy.taxonomy_id}`
    const groupLabel = scopedNode?.node_name ?? 'Unassigned'
    const aggregateKey = `${slice.as_of_date}:${groupKey}`
    const current = grouped.get(aggregateKey) ?? {
      as_of_date: slice.as_of_date,
      group_key: groupKey,
      group_label: groupLabel,
      beginning_value_base: 0,
      ending_value_base: 0,
      beginning_weight: 0,
      ending_weight: 0,
      total_pnl: 0,
      daily_contribution: 0,
      coverage_state: 'complete',
      market_observation_count: 0,
      return_observation_eligible: false,
    }

    current.beginning_value_base = addMeasure(current.beginning_value_base, beginningValue)
    current.ending_value_base = addMeasure(current.ending_value_base, endingValue)
    current.beginning_weight = addMeasure(current.beginning_weight, beginningWeight)
    current.ending_weight = addMeasure(current.ending_weight, endingWeight)
    current.total_pnl = addMeasure(current.total_pnl, totalPnl)
    current.daily_contribution = addMeasure(current.daily_contribution, dailyContribution)
    current.market_observation_count += marketObservationCount
    const coverageStates = coverageStatesByGroup.get(aggregateKey) ?? []
    coverageStates.push(slice.coverage_state)
    coverageStatesByGroup.set(aggregateKey, coverageStates)
    grouped.set(aggregateKey, current)
  })

  if (errors.length) {
    return riskFail(errors, [] satisfies ReturnSlice[])
  }
  const missingCoverageState = [...grouped.values()].find(
    (slice) => !(coverageStatesByGroup.get(`${slice.as_of_date}:${slice.group_key}`) ?? []).length,
  )
  if (missingCoverageState) {
    return riskFail(
      `Taxonomy risk aggregation lost coverage state for ${missingCoverageState.group_label} on ${missingCoverageState.as_of_date}.`,
      [] satisfies ReturnSlice[],
    )
  }

  const groupedSlices = [...grouped.values()].map((slice) => {
    const dailyReturn =
      slice.total_pnl != null && slice.beginning_value_base != null && slice.beginning_value_base > 1e-9
        ? slice.total_pnl / slice.beginning_value_base
        : null
    const coverageState = mergeCoverageState(coverageStatesByGroup.get(`${slice.as_of_date}:${slice.group_key}`) as PortfolioPerformanceCoverageState[])
    return {
      ...slice,
      coverage_state: coverageState,
      daily_return: dailyReturn,
      return_observation_eligible:
        dailyReturn != null &&
        coverageState === 'complete' &&
        (slice.market_observation_count > 0 || Math.abs(dailyReturn) > 1e-12),
    }
  }) satisfies ReturnSlice[]
  return riskOk(groupedSlices)
}

function accountValueBase(accountRow: PortfolioAccountsWorkspaceResponse['accounts'][number]) {
  return finiteNumber(accountRow.account_value_base)
}

function buildCurrentPlanningGroups({
  holdingsWorkspace,
  accountsWorkspace,
  catalog,
  taxonomy,
  referenceDate,
}: {
  holdingsWorkspace: HoldingsWorkspaceResponse | null
  accountsWorkspace: PortfolioAccountsWorkspaceResponse | null
  catalog: PortfolioTaxonomyCatalogResponse | null
  taxonomy: PortfolioTaxonomyRecord | null
  referenceDate: string | null
}) {
  if (!holdingsWorkspace || !catalog || !taxonomy || !referenceDate) {
    return riskOk([] satisfies CurrentPlanningGroup[])
  }

  const taxonomyId = taxonomy.taxonomy_id
  const currentReferenceDate = referenceDate
  const nodeById = buildNodeLookup(catalog, taxonomyId)
  const groups = new Map<string, CurrentPlanningGroup>()
  const errors: string[] = []

  function addEntity({
    targetScope,
    entityId,
    valueBase,
    weightInput,
    cashLike,
  }: {
    targetScope: TaxonomyAssignmentScope
    entityId: string
    valueBase: number | null
    weightInput: number | null
    cashLike: boolean
  }) {
    const assignmentResult = resolveActiveAssignment(catalog, taxonomyId, targetScope, entityId, currentReferenceDate)
    if (assignmentResult.error) {
      errors.push(assignmentResult.error)
      return
    }
    const assignment = assignmentResult.assignment
    const topLevelNode = resolveScopedTaxonomyNode(assignment?.taxonomy_node_id, nodeById, '')
    const groupKey = topLevelNode?.taxonomy_node_id ?? `unassigned:${taxonomyId}`
    const label = topLevelNode?.node_name ?? 'Unassigned'
    const hasExposure = Math.abs(valueBase ?? 0) > 1e-9 || Math.abs(weightInput ?? 0) > 1e-9
    const current = groups.get(groupKey) ?? {
      groupKey,
      label,
      currentWeight: null,
      currentValueBase: null,
      hasMarketRiskInput: false,
      hasCashLikeInput: false,
    }
    if (weightInput != null) {
      current.currentWeight = (current.currentWeight ?? 0) + weightInput
    }
    if (valueBase != null) {
      current.currentValueBase = (current.currentValueBase ?? 0) + valueBase
    }
    if (hasExposure) {
      current.hasMarketRiskInput = current.hasMarketRiskInput || !cashLike
      current.hasCashLikeInput = current.hasCashLikeInput || cashLike
    }
    groups.set(groupKey, current)
  }

  if (taxonomy.primary_assignment_scope === 'instrument') {
    if (!accountsWorkspace) {
      return riskFail(
        'Current drift requires the accounts workspace so cash and pending settlement are included in portfolio NAV.',
        [] satisfies CurrentPlanningGroup[],
      )
    }
    const accountRows = accountsWorkspace.accounts
    const cashAccounts = taxonomy.planning_enabled
      ? accountRows.filter((accountRow) => {
          if (accountRow.account.account_type !== 'deposit_account') {
            return false
          }
          const activeCashAssignmentResult = resolveActiveAssignment(
            catalog,
            taxonomyId,
            'cash_bucket',
            accountRow.account.account_id,
            referenceDate,
          )
          if (activeCashAssignmentResult.error) {
            errors.push(activeCashAssignmentResult.error)
            return false
          }
          const valueBase = accountValueBase(accountRow)
          if (valueBase == null) {
            errors.push(`Current drift requires account_value_base for cash account ${accountRow.account.account_id}.`)
            return false
          }
          return Boolean(activeCashAssignmentResult.assignment) || Math.abs(valueBase) > 1e-9
        })
      : []
    const missingHoldingValueRows = holdingsWorkspace.rows.filter((row) => finiteNumber(row.market_value_base) == null)
    if (missingHoldingValueRows.length) {
      errors.push(
        `Current drift requires market_value_base for every holding; missing: ${missingHoldingValueRows
          .map((row) => row.instrument_core.instrument_name || row.instrument_core.instrument_id)
          .join(', ')}.`,
      )
    }
    const totalValueBase =
      holdingsWorkspace.rows.reduce((total, row) => total + (finiteNumber(row.market_value_base) ?? 0), 0) +
      cashAccounts.reduce((total, accountRow) => total + (accountValueBase(accountRow) ?? 0), 0)
    if (totalValueBase <= 1e-9 && (holdingsWorkspace.rows.length || cashAccounts.length)) {
      errors.push('Current drift requires positive portfolio NAV from holdings plus cash account values.')
    }

    holdingsWorkspace.rows.forEach((row) => {
      const valueBase = finiteNumber(row.market_value_base)
      if (valueBase == null || totalValueBase <= 1e-9) {
        return
      }
      const weightInput = valueBase / totalValueBase
      addEntity({
        targetScope: 'instrument',
        entityId: row.instrument_core.instrument_id,
        valueBase,
        weightInput,
        cashLike: false,
      })
    })

    cashAccounts.forEach((accountRow) => {
      const valueBase = accountValueBase(accountRow)
      if (valueBase == null || totalValueBase <= 1e-9) {
        return
      }
      addEntity({
        targetScope: 'cash_bucket',
        entityId: accountRow.account.account_id,
        valueBase,
        weightInput: valueBase / totalValueBase,
        cashLike: true,
      })
    })
  } else if (taxonomy.primary_assignment_scope === 'cash_bucket') {
    if (!accountsWorkspace) {
      return riskFail('Current drift requires the accounts workspace for cash-bucket taxonomies.', [] satisfies CurrentPlanningGroup[])
    }
    const cashAccounts = accountsWorkspace.accounts.filter(
      (accountRow) => accountRow.account.account_type === 'deposit_account',
    )
    const missingAccountValueRows = cashAccounts.filter((accountRow) => accountValueBase(accountRow) == null)
    if (missingAccountValueRows.length) {
      errors.push(
        `Current drift requires account_value_base for every cash account; missing: ${missingAccountValueRows
          .map((accountRow) => accountRow.account.account_id)
          .join(', ')}.`,
      )
    }
    const totalValueBase = cashAccounts.reduce((total, accountRow) => total + (accountValueBase(accountRow) ?? 0), 0)
    if (totalValueBase <= 1e-9 && cashAccounts.length) {
      errors.push('Current drift requires positive cash account value for cash-bucket taxonomies.')
    }
    cashAccounts.forEach((accountRow) => {
      const valueBase = accountValueBase(accountRow)
      if (valueBase == null || totalValueBase <= 1e-9) {
        return
      }
      addEntity({
        targetScope: 'cash_bucket',
        entityId: accountRow.account.account_id,
        valueBase,
        weightInput: valueBase / totalValueBase,
        cashLike: true,
      })
    })
  } else {
    if (!accountsWorkspace) {
      return riskFail('Current drift requires the accounts workspace for account taxonomies.', [] satisfies CurrentPlanningGroup[])
    }
    const accountRows = accountsWorkspace.accounts
    const missingAccountValueRows = accountRows.filter((accountRow) => accountValueBase(accountRow) == null)
    if (missingAccountValueRows.length) {
      errors.push(
        `Current drift requires account_value_base for every account; missing: ${missingAccountValueRows
          .map((accountRow) => accountRow.account.account_id)
          .join(', ')}.`,
      )
    }
    const totalValueBase = accountRows.reduce((total, accountRow) => total + (accountValueBase(accountRow) ?? 0), 0)
    if (totalValueBase <= 1e-9 && accountRows.length) {
      errors.push('Current drift requires positive account value for account taxonomies.')
    }
    accountRows.forEach((accountRow) => {
      const valueBase = accountValueBase(accountRow)
      if (valueBase == null || totalValueBase <= 1e-9) {
        return
      }
      const positionValue = finiteNumber(accountRow.position_market_value) ?? 0
      addEntity({
        targetScope: 'account',
        entityId: accountRow.account.account_id,
        valueBase,
        weightInput: valueBase / totalValueBase,
        cashLike: accountRow.account.account_type === 'deposit_account' && Math.abs(positionValue) <= 1e-9,
      })
    })
  }

  if (errors.length) {
    return riskFail(errors, [] satisfies CurrentPlanningGroup[])
  }

  const rows = [...groups.values()]
    .filter((group) => group.currentWeight != null || group.currentValueBase != null)
    .sort((left, right) => Math.abs(right.currentWeight ?? 0) - Math.abs(left.currentWeight ?? 0))
  return riskOk(rows)
}

function targetMemberKey(memberType: PortfolioTargetSetLineRecord['target_member_type'], memberId: string) {
  return `${memberType}:${memberId}`
}

function lineDisplayKey(line: PortfolioTargetSetLineRecord) {
  if (line.target_member_type === 'taxonomy_node') {
    return line.target_member_id
  }
  return targetMemberKey(line.target_member_type, line.target_member_id)
}

function lineDisplayLabel(line: PortfolioTargetSetLineRecord, nodeById: Map<string, PortfolioTaxonomyNodeRecord>) {
  if (line.target_member_type === 'taxonomy_node') {
    return nodeById.get(line.target_member_id)?.node_name ?? line.target_member_id
  }
  return `${formatLabel(line.target_member_type)} ${line.target_member_id}`
}

function buildTargetGapRows({
  targetSet,
  targetLines,
  currentGroups,
  riskSharesByGroup,
  riskShareErrors = [],
  nodeById,
  dimension,
  baseCurrency,
}: {
  targetSet: PortfolioTargetSetRecord | null
  targetLines: PortfolioTargetSetLineRecord[]
  currentGroups: CurrentPlanningGroup[]
  riskSharesByGroup: Map<string, number | null>
  riskShareErrors?: string[]
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>
  dimension: 'weight' | 'risk_budget'
  baseCurrency: string
}) {
  if (!targetSet) {
    return riskOk([] satisfies RiskTargetGapChartRow[])
  }
  if (dimension === 'weight' && !targetSet.weight_enabled) {
    return riskOk([] satisfies RiskTargetGapChartRow[])
  }
  if (dimension === 'risk_budget' && !targetSet.risk_budget_enabled) {
    return riskOk([] satisfies RiskTargetGapChartRow[])
  }
  if (!targetLines.length) {
    return riskFail(`${targetSet.name} is active but has no target lines.`, [] satisfies RiskTargetGapChartRow[])
  }
  const nonNodeLines = targetLines.filter((line) => line.target_member_type !== 'taxonomy_node')
  if (nonNodeLines.length) {
    return riskFail(
      `${targetSet.name} root target drift must be defined on taxonomy_node budgeting members; unsupported direct members: ${nonNodeLines
        .map((line) => `${line.target_member_type}:${line.target_member_id}`)
        .join(', ')}.`,
      [] satisfies RiskTargetGapChartRow[],
    )
  }
  const missingTargetNodes = targetLines.filter((line) => !nodeById.has(line.target_member_id))
  if (missingTargetNodes.length) {
    return riskFail(
      `${targetSet.name} references missing taxonomy nodes: ${missingTargetNodes.map((line) => line.target_member_id).join(', ')}.`,
      [] satisfies RiskTargetGapChartRow[],
    )
  }
  if (dimension === 'risk_budget' && riskShareErrors.length) {
    return riskFail(
      [`${targetSet.name} risk target gap cannot be calculated because current risk share failed.`].concat(riskShareErrors),
      [] satisfies RiskTargetGapChartRow[],
    )
  }
  const targetLineErrors = targetLines
    .filter((line) => (dimension === 'weight' ? line.target_weight : line.target_risk_share) == null)
    .map((line) => `${targetSet.name} is missing ${dimension === 'weight' ? 'target_weight' : 'target_risk_share'} for ${lineDisplayLabel(line, nodeById)}.`)
  if (targetLineErrors.length) {
    return riskFail(targetLineErrors, [] satisfies RiskTargetGapChartRow[])
  }
  const targetTotal = targetLines.reduce(
    (total, line) => total + ((dimension === 'weight' ? line.target_weight : line.target_risk_share) ?? 0),
    0,
  )
  if (Math.abs(targetTotal - 1) > 1e-6) {
    return riskFail(
      `${targetSet.name} ${dimension === 'weight' ? 'target weights' : 'risk targets'} must sum to 100%; got ${formatPercent(targetTotal)}.`,
      [] satisfies RiskTargetGapChartRow[],
    )
  }

  const normalizedBaseCurrency = baseCurrency.trim().toUpperCase()
  if (!normalizedBaseCurrency && currentGroups.some((group) => group.currentValueBase != null)) {
    return riskFail(
      `${targetSet.name} target drift requires the portfolio base currency before value details can be rendered.`,
      [] satisfies RiskTargetGapChartRow[],
    )
  }

  const currentGroupByKey = new Map(currentGroups.map((group) => [group.groupKey, group] as const))
  const lineByKey = new Map(targetLines.map((line) => [lineDisplayKey(line), line] as const))
  const allKeys = new Set([...currentGroupByKey.keys(), ...lineByKey.keys()])
  const rows: RiskTargetGapChartRow[] = []
  const errors: string[] = []

  allKeys.forEach((key) => {
    const currentGroup = currentGroupByKey.get(key) ?? null
    const line = lineByKey.get(key) ?? null
    const current =
      dimension === 'weight'
        ? currentGroup?.currentWeight ?? 0
        : riskSharesByGroup.has(key)
          ? riskSharesByGroup.get(key) ?? 0
          : currentGroup?.hasCashLikeInput && !currentGroup.hasMarketRiskInput
            ? 0
            : currentGroup
              ? null
              : 0
    if (dimension === 'risk_budget' && current == null && currentGroup?.hasMarketRiskInput) {
      errors.push(`${targetSet.name} risk target gap is missing current risk share for ${currentGroup.label}.`)
      return
    }
    const target = line ? ((dimension === 'weight' ? line.target_weight : line.target_risk_share) ?? 0) : 0
    if (Math.abs(current ?? 0) <= 1e-12 && Math.abs(target) <= 1e-12) {
      return
    }
    rows.push({
      id: `${targetSet.target_set_id}:${dimension}:${key}`,
      label: currentGroup?.label ?? (line ? lineDisplayLabel(line, nodeById) : key),
      current,
      target,
      gap: current != null && target != null ? current - target : null,
      detail: currentGroup?.currentValueBase != null ? formatCurrency(currentGroup.currentValueBase, normalizedBaseCurrency) : undefined,
    })
  })

  if (errors.length) {
    return riskFail(errors, [] satisfies RiskTargetGapChartRow[])
  }

  return riskOk(rows.sort((left, right) => Math.abs(right.gap ?? 0) - Math.abs(left.gap ?? 0)))
}

function selectUniqueTargetSet(targetSets: PortfolioTargetSetRecord[], targetSetType: 'saa' | 'taa') {
  const matches = targetSets.filter((targetSet) => targetSet.target_set_type === targetSetType)
  if (matches.length > 1) {
    return riskFail(
      `Multiple active root ${targetSetType.toUpperCase()} target sets are configured; Risk cannot choose one implicitly.`,
      null as PortfolioTargetSetRecord | null,
    )
  }
  return riskOk(matches[0] ?? null)
}

function heatmapCellStyle(value: number | null | undefined, maxAbs: number): CSSProperties {
  if (value == null || Number.isNaN(value) || maxAbs <= 0) {
    return {}
  }
  const intensity = Math.min(1, Math.max(0.08, Math.abs(value) / maxAbs))
  if (value < 0) {
    return { backgroundColor: `rgba(185, 28, 28, ${0.06 + intensity * 0.24})` }
  }
  return { backgroundColor: `rgba(15, 76, 129, ${0.06 + intensity * 0.24})` }
}

function formatCorrelation(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }
  return formatNumber(value, 2)
}

function uniqueSortedDates(slices: ReturnSlice[]) {
  return [...new Set(slices.map((slice) => slice.as_of_date).filter(Boolean))].sort()
}

function uniqueSortedSeriesDates(series: GroupReturnSeries[]) {
  const dates = new Set<string>()
  series.forEach((item) => {
    item.returnsByDate.forEach((_value, dateKey) => dates.add(dateKey))
  })
  return [...dates].sort()
}

function taxonomyScopeOptions(
  taxonomy: PortfolioTaxonomyRecord | null,
  catalog: PortfolioTaxonomyCatalogResponse | null,
) {
  if (!taxonomy || !catalog) {
    return []
  }
  const nodeById = buildNodeLookup(catalog, taxonomy.taxonomy_id)
  return [
    { value: '', label: 'Top Level' },
    ...[...nodeById.values()]
      .sort((left, right) => {
        const leftPath = buildNodePath(left.taxonomy_node_id, nodeById).map((node) => node.node_name).join(' / ')
        const rightPath = buildNodePath(right.taxonomy_node_id, nodeById).map((node) => node.node_name).join(' / ')
        return leftPath.localeCompare(rightPath)
      })
      .map((node) => ({
        value: node.taxonomy_node_id,
        label: buildNodePath(node.taxonomy_node_id, nodeById).map((item) => item.node_name).join(' / '),
      })),
  ]
}

function RiskSettingsMenu<TSettings extends RiskSettingsState>({
  label,
  settings,
  onChange,
  includeChartStyle = false,
  includeContributionMode = false,
}: {
  label: string
  settings: TSettings
  onChange: (settings: TSettings) => void
  includeChartStyle?: boolean
  includeContributionMode?: boolean
}) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const settingsMenuRef = useRef<HTMLDivElement | null>(null)
  const chartStyleSettings = includeChartStyle && hasChartStyle(settings) ? settings : null

  useEffect(() => {
    if (!settingsOpen) {
      return undefined
    }

    function handleDocumentPointerDown(event: PointerEvent) {
      if (!settingsMenuRef.current?.contains(event.target as Node)) {
        setSettingsOpen(false)
      }
    }

    document.addEventListener('pointerdown', handleDocumentPointerDown)
    return () => document.removeEventListener('pointerdown', handleDocumentPointerDown)
  }, [settingsOpen])

  return (
    <div className="portfolio-nav-chart-menu risk-settings-menu" ref={settingsMenuRef}>
      <button
        type="button"
        className={
          settingsOpen ? 'portfolio-nav-settings-trigger portfolio-nav-settings-trigger-active' : 'portfolio-nav-settings-trigger'
        }
        aria-label={`${label} settings`}
        onClick={() => setSettingsOpen((current) => !current)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 7h4" />
          <path d="M14 7h6" />
          <circle cx="11" cy="7" r="2.25" />
          <path d="M4 17h7" />
          <path d="M17 17h3" />
          <circle cx="14" cy="17" r="2.25" />
        </svg>
      </button>
      {settingsOpen ? (
        <div className="portfolio-nav-settings-panel risk-settings-panel">
          <div className="portfolio-nav-settings-layout">
            <section className="portfolio-nav-settings-block">
              <div className="portfolio-nav-settings-block-head">
                <span>Window</span>
                <strong>{windowLabel(settings.lookbackDays)}</strong>
              </div>
              <div className="portfolio-nav-settings-option-grid">
                {RISK_WINDOW_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={
                      settings.lookbackDays === option.value
                        ? 'portfolio-nav-option portfolio-nav-option-active'
                        : 'portfolio-nav-option'
                    }
                    onClick={() => onChange({ ...settings, lookbackDays: option.value })}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </section>

            <section className="portfolio-nav-settings-block">
              <div className="portfolio-nav-settings-block-head">
                <span>Method</span>
                <strong>{riskModelLabel(settings.modelId)}</strong>
              </div>
              <div className="portfolio-nav-settings-option-grid">
                {RISK_MODEL_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={
                      settings.modelId === option.value
                        ? 'portfolio-nav-option portfolio-nav-option-active'
                        : 'portfolio-nav-option'
                    }
                    onClick={() => onChange({ ...settings, modelId: option.value })}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </section>

            {includeContributionMode ? (
              <section className="portfolio-nav-settings-block portfolio-nav-settings-block-data">
                <div className="portfolio-nav-settings-block-head">
                  <span>Risk Share</span>
                  <strong>{formatLabel(settings.contributionMode)}</strong>
                </div>
                <div className="portfolio-nav-settings-option-grid">
                  {CONTRIBUTION_MODE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={
                        settings.contributionMode === option.value
                          ? 'portfolio-nav-option portfolio-nav-option-active'
                          : 'portfolio-nav-option'
                      }
                      onClick={() => onChange({ ...settings, contributionMode: option.value })}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </section>
            ) : null}

            {chartStyleSettings ? (
              <section className="portfolio-nav-settings-block portfolio-nav-settings-block-data">
                <div className="portfolio-nav-settings-block-head">
                  <span>Display</span>
                  <strong>{formatLabel(chartStyleSettings.chartStyle)}</strong>
                </div>
                <div className="portfolio-nav-settings-option-grid">
                  {CHART_STYLE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={
                        chartStyleSettings.chartStyle === option.value
                          ? 'portfolio-nav-option portfolio-nav-option-active'
                          : 'portfolio-nav-option'
                      }
                      onClick={() => onChange({ ...chartStyleSettings, chartStyle: option.value })}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </section>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function RiskDateTimeline({
  dates,
  value,
  onChange,
  label,
}: {
  dates: string[]
  value: string
  onChange: (value: string) => void
  label: string
}) {
  if (!dates.length) {
    return null
  }
  const selectedIndex = dates.includes(value) ? dates.indexOf(value) : dates.length - 1
  const selectedDate = dates[selectedIndex]
  return (
    <div className="risk-date-timeline">
      <div>
        <span>{label}</span>
        <strong>{selectedDate}</strong>
      </div>
      <input
        type="range"
        min={0}
        max={Math.max(0, dates.length - 1)}
        value={selectedIndex}
        onChange={(event) => onChange(dates[Number(event.target.value)] ?? value)}
        aria-label={label}
      />
      <div className="risk-date-timeline-endpoints">
        <span>{dates[0]}</span>
        <span>{dates[dates.length - 1]}</span>
      </div>
    </div>
  )
}

export default function RiskPage() {
  const { portfolioId = '' } = useParams()
  const [holdingsWorkspace, setHoldingsWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [accountsWorkspace, setAccountsWorkspace] = useState<PortfolioAccountsWorkspaceResponse | null>(null)
  const [taxonomyCatalog, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [workspaceLoading, setWorkspaceLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [performanceWorkspace, setPerformanceWorkspace] = useState<PortfolioPerformanceResponse | null>(null)
  const [instrumentContribution, setInstrumentContribution] = useState<PortfolioContributionReportResponse | null>(null)
  const [riskBasisWorkspace, setRiskBasisWorkspace] = useState<PortfolioPerformanceCalculationGroupsResponse | null>(null)
  const [riskDataLoading, setRiskDataLoading] = useState(false)
  const [performanceError, setPerformanceError] = useState<string | null>(null)
  const [contributionError, setContributionError] = useState<string | null>(null)
  const [riskBasisError, setRiskBasisError] = useState<string | null>(null)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkInstrumentId, setBenchmarkInstrumentId] = useState('')
  const [benchmarkChart, setBenchmarkChart] = useState<PortfolioInstrumentPriceChartResponse | null>(null)
  const [benchmarkLoading, setBenchmarkLoading] = useState(false)
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null)
  const [rollingSettings, setRollingSettings] = useState<RollingRiskSettingsState>(DEFAULT_ROLLING_SETTINGS)
  const [matrixSettings, setMatrixSettings] = useState<RiskSettingsState>(DEFAULT_RISK_SETTINGS)
  const [driftSettings, setDriftSettings] = useState<RiskSettingsState>(DEFAULT_RISK_SETTINGS)
  const [contributionSettings, setContributionSettings] = useState<RiskSettingsState>(DEFAULT_RISK_SETTINGS)
  const [matrixScopeNodeId, setMatrixScopeNodeId] = useState('')
  const [matrixAsOfDate, setMatrixAsOfDate] = useState('')
  const [contributionAsOfDate, setContributionAsOfDate] = useState('')

  const riskWindowEndDate = holdingsWorkspace?.as_of_date ?? ''
  const riskWindowStartDate = riskWindowEndDate ? shiftIsoDate(riskWindowEndDate, -(RISK_DATA_HISTORY_DAYS - 1)) : ''

  useEffect(() => {
    if (!portfolioId) {
      setHoldingsWorkspace(null)
      setAccountsWorkspace(null)
      setTaxonomyCatalog(null)
      setWorkspaceLoading(false)
      setWorkspaceError('Portfolio id is required.')
      return
    }

    let cancelled = false
    setWorkspaceLoading(true)

    Promise.all([
      getHoldingsWorkspace(portfolioId),
      getPortfolioAccountsWorkspace(portfolioId),
      getPortfolioTaxonomyCatalog(portfolioId),
    ])
      .then(([holdingsResponse, accountsResponse, taxonomyResponse]) => {
        if (cancelled) {
          return
        }
        setHoldingsWorkspace(holdingsResponse)
        setAccountsWorkspace(accountsResponse)
        setTaxonomyCatalog(taxonomyResponse)
        setWorkspaceError(null)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setWorkspaceError(requestError instanceof Error ? requestError.message : 'Failed to load risk workspace.')
          setHoldingsWorkspace(null)
          setAccountsWorkspace(null)
          setTaxonomyCatalog(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setWorkspaceLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId) {
      setBenchmarkInstruments([])
      return
    }

    let cancelled = false
    getPortfolioInstruments(portfolioId)
      .then((response) => {
        if (!cancelled) {
          setBenchmarkInstruments(response.instruments)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBenchmarkInstruments([])
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId || !riskWindowStartDate || !riskWindowEndDate) {
      setPerformanceWorkspace(null)
      setInstrumentContribution(null)
      setRiskBasisWorkspace(null)
      setRiskDataLoading(false)
      setPerformanceError(null)
      setContributionError(null)
      setRiskBasisError(null)
      return
    }

    let cancelled = false
    setRiskDataLoading(true)
    setPerformanceError(null)
    setContributionError(null)
    setRiskBasisWorkspace(null)
    setRiskBasisError(null)

    Promise.allSettled([
      getPortfolioPerformance(portfolioId, {
        start_date: riskWindowStartDate,
        end_date: riskWindowEndDate,
      }),
      getPortfolioPerformanceContribution(portfolioId, {
        start_date: riskWindowStartDate,
        end_date: riskWindowEndDate,
        axis: 'instrument',
      }),
      getPortfolioPerformanceCalculationGroups(portfolioId, {
        start_date: riskWindowStartDate,
        end_date: riskWindowEndDate,
        axis: 'instrument',
      }),
    ])
      .then(([performanceResult, contributionResult, riskBasisResult]) => {
        if (cancelled) {
          return
        }
        if (performanceResult.status === 'fulfilled') {
          setPerformanceWorkspace(performanceResult.value)
          setPerformanceError(null)
        } else {
          setPerformanceWorkspace(null)
          setPerformanceError(
            performanceResult.reason instanceof Error
              ? performanceResult.reason.message
              : 'Failed to load volatility series.',
          )
        }

        if (contributionResult.status === 'fulfilled') {
          setInstrumentContribution(contributionResult.value)
          setContributionError(null)
        } else {
          setInstrumentContribution(null)
          setContributionError(
            contributionResult.reason instanceof Error
              ? contributionResult.reason.message
              : 'Failed to load instrument return slices.',
          )
        }

        if (riskBasisResult.status === 'fulfilled') {
          setRiskBasisWorkspace(riskBasisResult.value)
          setRiskBasisError(null)
        } else {
          setRiskBasisWorkspace(null)
          setRiskBasisError(
            riskBasisResult.reason instanceof Error
              ? riskBasisResult.reason.message
              : 'Failed to load risk frequency basis.',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setRiskDataLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId, riskWindowEndDate, riskWindowStartDate])

  useEffect(() => {
    if (!portfolioId || !benchmarkInstrumentId || !riskWindowEndDate) {
      setBenchmarkChart(null)
      setBenchmarkLoading(false)
      setBenchmarkError(null)
      return
    }

    let cancelled = false
    setBenchmarkLoading(true)
    setBenchmarkError(null)

    getPortfolioInstrumentPriceChart(portfolioId, benchmarkInstrumentId, {
      as_of_date: riskWindowEndDate,
      range: 'all',
    })
      .then((response) => {
        if (!cancelled) {
          setBenchmarkChart(response)
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setBenchmarkChart(null)
          setBenchmarkError(requestError instanceof Error ? requestError.message : 'Failed to load benchmark history.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBenchmarkLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [benchmarkInstrumentId, portfolioId, riskWindowEndDate])

  const planningTaxonomies = taxonomyCatalog?.taxonomies.filter((taxonomy) => taxonomy.planning_enabled) ?? []
  const defaultPlanningTaxonomy =
    planningTaxonomies.find((taxonomy) => taxonomy.taxonomy_id === taxonomyCatalog?.default_planning_taxonomy_id) ?? null
  const defaultTaxonomyNodeById = useMemo(
    () => buildNodeLookup(taxonomyCatalog, defaultPlanningTaxonomy?.taxonomy_id),
    [defaultPlanningTaxonomy?.taxonomy_id, taxonomyCatalog],
  )
  const activeRootTargetSets = useMemo(
    () =>
      (taxonomyCatalog?.target_sets ?? []).filter(
        (targetSet) =>
          targetSet.taxonomy_id === defaultPlanningTaxonomy?.taxonomy_id &&
          !targetSet.comparator_taxonomy_node_id &&
          targetSet.status === 'active' &&
          isRecordActive(targetSet.effective_from, targetSet.effective_to, holdingsWorkspace?.as_of_date ?? null),
      ),
    [defaultPlanningTaxonomy?.taxonomy_id, holdingsWorkspace?.as_of_date, taxonomyCatalog?.target_sets],
  )
  const activeRootSaaTargetSetResult = useMemo(() => selectUniqueTargetSet(activeRootTargetSets, 'saa'), [activeRootTargetSets])
  const activeRootTaaTargetSetResult = useMemo(() => selectUniqueTargetSet(activeRootTargetSets, 'taa'), [activeRootTargetSets])
  const activeRootSaaTargetSet = activeRootSaaTargetSetResult.value
  const activeRootTaaTargetSet = activeRootTaaTargetSetResult.value
  const targetLinesByTargetSetId = useMemo(() => {
    const lookup = new Map<string, PortfolioTargetSetLineRecord[]>()
    ;(taxonomyCatalog?.target_set_lines ?? []).forEach((line) => {
      const rows = lookup.get(line.target_set_id) ?? []
      rows.push(line)
      lookup.set(line.target_set_id, rows)
    })
    return lookup
  }, [taxonomyCatalog?.target_set_lines])

  const selectedBenchmarkInstrument =
    benchmarkInstruments.find((instrument) => instrument.instrument_id === benchmarkInstrumentId) ?? null
  const benchmarkLabel = selectedBenchmarkInstrument ? instrumentPrimaryIdentifier(selectedBenchmarkInstrument) : null
  const instrumentSlices = instrumentContribution?.daily_slices ?? []
  const rawInstrumentReturnSeries = useMemo(() => buildGroupReturnSeries(instrumentSlices), [instrumentSlices])
  const riskBasisResolved = riskBasisWorkspace != null || riskBasisError != null
  const riskBasisPending = Boolean(holdingsWorkspace && riskWindowStartDate && riskWindowEndDate && !riskBasisResolved)
  const portfolioRiskFrequencyResult = useMemo(
    () =>
      riskBasisPending
        ? riskOk({
            frequency: 'daily',
            statusLabel: 'Risk basis loading',
          } satisfies RiskFrequencyProfile)
        : riskBasisError
        ? riskFail(
            riskBasisError,
            {
              frequency: 'daily',
              statusLabel: 'Risk basis unavailable',
            } satisfies RiskFrequencyProfile,
          )
        : riskFrequencyProfileFromCalculationGroups(riskBasisWorkspace),
    [riskBasisError, riskBasisPending, riskBasisWorkspace],
  )
  const portfolioRiskFrequency = portfolioRiskFrequencyResult.value
  const portfolioRiskFrequencyErrors = portfolioRiskFrequencyResult.errors
  const riskFrequencyReady = !riskBasisPending && portfolioRiskFrequencyErrors.length === 0
  const riskBasisFinalDate = holdingsWorkspace?.as_of_date ?? riskWindowEndDate
  const instrumentReturnSeries = useMemo(
    () =>
      riskFrequencyReady
        ? alignReturnSeriesToFrequency(
            rawInstrumentReturnSeries,
            portfolioRiskFrequency.frequency,
            riskBasisFinalDate,
          )
        : [],
    [portfolioRiskFrequency.frequency, rawInstrumentReturnSeries, riskBasisFinalDate, riskFrequencyReady],
  )
  const portfolioReturnPointsRaw = useMemo(
    () => buildPortfolioReturnPoints(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace?.daily_series],
  )
  const portfolioReturnPoints = useMemo(
    () =>
      riskFrequencyReady
        ? alignReturnPointsToFrequency(
            portfolioReturnPointsRaw,
            portfolioRiskFrequency.frequency,
            riskBasisFinalDate,
          )
        : [],
    [portfolioReturnPointsRaw, portfolioRiskFrequency.frequency, riskBasisFinalDate, riskFrequencyReady],
  )
  const benchmarkReturnPointsRaw = useMemo(() => buildBenchmarkReturnPoints(benchmarkChart), [benchmarkChart])
  const benchmarkReturnPoints = useMemo(
    () =>
      riskFrequencyReady
        ? alignReturnPointsToFrequency(
            benchmarkReturnPointsRaw,
            portfolioRiskFrequency.frequency,
            riskBasisFinalDate,
          )
        : [],
    [benchmarkReturnPointsRaw, portfolioRiskFrequency.frequency, riskBasisFinalDate, riskFrequencyReady],
  )
  const rollingWindow = windowLabel(rollingSettings.lookbackDays)
  const rollingVolatilityPoints = useMemo(
    () =>
      buildRollingMetricPoints(
        portfolioReturnPoints,
        rollingSettings.lookbackDays,
        rollingSettings.modelId,
        'volatility',
      ),
    [portfolioReturnPoints, rollingSettings.lookbackDays, rollingSettings.modelId],
  )
  const benchmarkRollingVolatilityPoints = useMemo(
    () =>
      buildRollingMetricPoints(
        benchmarkReturnPoints,
        rollingSettings.lookbackDays,
        rollingSettings.modelId,
        'volatility',
      ),
    [benchmarkReturnPoints, rollingSettings.lookbackDays, rollingSettings.modelId],
  )
  const rollingSharpePoints = useMemo(
    () => buildRollingMetricPoints(portfolioReturnPoints, rollingSettings.lookbackDays, rollingSettings.modelId, 'sharpe'),
    [portfolioReturnPoints, rollingSettings.lookbackDays, rollingSettings.modelId],
  )
  const benchmarkRollingSharpePoints = useMemo(
    () => buildRollingMetricPoints(benchmarkReturnPoints, rollingSettings.lookbackDays, rollingSettings.modelId, 'sharpe'),
    [benchmarkReturnPoints, rollingSettings.lookbackDays, rollingSettings.modelId],
  )

  const riskDates = useMemo(() => uniqueSortedSeriesDates(instrumentReturnSeries), [instrumentReturnSeries])

  useEffect(() => {
    if (!riskDates.length) {
      if (matrixAsOfDate) {
        setMatrixAsOfDate('')
      }
      return
    }
    if (riskDates.length && !riskDates.includes(matrixAsOfDate)) {
      setMatrixAsOfDate(riskDates[riskDates.length - 1])
    }
  }, [matrixAsOfDate, riskDates])

  useEffect(() => {
    if (!riskDates.length) {
      if (contributionAsOfDate) {
        setContributionAsOfDate('')
      }
      return
    }
    if (riskDates.length && !riskDates.includes(contributionAsOfDate)) {
      setContributionAsOfDate(riskDates[riskDates.length - 1])
    }
  }, [contributionAsOfDate, riskDates])

  const matrixTaxonomyScopeOptions = useMemo(
    () => taxonomyScopeOptions(defaultPlanningTaxonomy, taxonomyCatalog),
    [defaultPlanningTaxonomy, taxonomyCatalog],
  )
  const matrixTaxonomySlicesResult = useMemo(
    () => buildScopedTaxonomySlices(instrumentSlices, taxonomyCatalog, defaultPlanningTaxonomy, matrixScopeNodeId),
    [defaultPlanningTaxonomy, instrumentSlices, matrixScopeNodeId, taxonomyCatalog],
  )
  const matrixTaxonomySlices = matrixTaxonomySlicesResult.value
  const matrixTaxonomySeries = useMemo(() => buildGroupReturnSeries(matrixTaxonomySlices), [matrixTaxonomySlices])
  const alignedMatrixTaxonomySeries = useMemo(
    () =>
      riskFrequencyReady
        ? alignReturnSeriesToFrequency(
            matrixTaxonomySeries,
            portfolioRiskFrequency.frequency,
            riskBasisFinalDate,
          )
        : [],
    [matrixTaxonomySeries, portfolioRiskFrequency.frequency, riskBasisFinalDate, riskFrequencyReady],
  )
  const topLevelTaxonomySlicesResult = useMemo(
    () => buildScopedTaxonomySlices(instrumentSlices, taxonomyCatalog, defaultPlanningTaxonomy, ''),
    [defaultPlanningTaxonomy, instrumentSlices, taxonomyCatalog],
  )
  const topLevelTaxonomySlices = topLevelTaxonomySlicesResult.value
  const topLevelTaxonomySeries = useMemo(
    () => buildGroupReturnSeries(topLevelTaxonomySlices),
    [topLevelTaxonomySlices],
  )
  const alignedTopLevelTaxonomySeries = useMemo(
    () =>
      riskFrequencyReady
        ? alignReturnSeriesToFrequency(
            topLevelTaxonomySeries,
            portfolioRiskFrequency.frequency,
            riskBasisFinalDate,
          )
        : [],
    [portfolioRiskFrequency.frequency, riskBasisFinalDate, riskFrequencyReady, topLevelTaxonomySeries],
  )
  const instrumentCorrelationMatrix = useMemo(
    () => buildCorrelationMatrix(instrumentReturnSeries, matrixAsOfDate, matrixSettings),
    [instrumentReturnSeries, matrixAsOfDate, matrixSettings],
  )
  const taxonomyCorrelationMatrix = useMemo(
    () => buildCorrelationMatrix(alignedMatrixTaxonomySeries, matrixAsOfDate, matrixSettings),
    [alignedMatrixTaxonomySeries, matrixAsOfDate, matrixSettings],
  )
  const topLevelRiskContributionResult = useMemo(
    () =>
      riskFrequencyReady
        ? buildRiskContributionRows(alignedTopLevelTaxonomySeries, holdingsWorkspace?.as_of_date ?? '', driftSettings)
        : riskBasisPending
          ? riskOk([] satisfies RiskContributionRow[])
          : riskFail(portfolioRiskFrequencyErrors, [] satisfies RiskContributionRow[]),
    [
      alignedTopLevelTaxonomySeries,
      driftSettings,
      holdingsWorkspace?.as_of_date,
      riskBasisPending,
      portfolioRiskFrequencyErrors,
      riskFrequencyReady,
    ],
  )
  const topLevelRiskContributionRows = topLevelRiskContributionResult.value
  const riskSharesByTopLevelGroup = useMemo(
    () => new Map(topLevelRiskContributionRows.map((row) => [row.groupKey, row.riskShare] as const)),
    [topLevelRiskContributionRows],
  )
  const currentPlanningGroupsResult = useMemo(
    () =>
      buildCurrentPlanningGroups({
        holdingsWorkspace,
        accountsWorkspace,
        catalog: taxonomyCatalog,
        taxonomy: defaultPlanningTaxonomy,
        referenceDate: holdingsWorkspace?.as_of_date ?? null,
      }),
    [accountsWorkspace, defaultPlanningTaxonomy, holdingsWorkspace, taxonomyCatalog],
  )
  const currentPlanningGroups = currentPlanningGroupsResult.value
  const portfolioBaseCurrency = holdingsWorkspace?.base_currency ?? ''

  const saaWeightGapResult = useMemo(
    () =>
      buildTargetGapRows({
        targetSet: activeRootSaaTargetSet,
        targetLines: targetLinesByTargetSetId.get(activeRootSaaTargetSet?.target_set_id ?? '') ?? [],
        currentGroups: currentPlanningGroups,
        riskSharesByGroup: riskSharesByTopLevelGroup,
        nodeById: defaultTaxonomyNodeById,
        dimension: 'weight',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootSaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      portfolioBaseCurrency,
      riskSharesByTopLevelGroup,
      targetLinesByTargetSetId,
    ],
  )
  const saaWeightGapRows = saaWeightGapResult.value
  const taaWeightGapResult = useMemo(
    () =>
      buildTargetGapRows({
        targetSet: activeRootTaaTargetSet,
        targetLines: targetLinesByTargetSetId.get(activeRootTaaTargetSet?.target_set_id ?? '') ?? [],
        currentGroups: currentPlanningGroups,
        riskSharesByGroup: riskSharesByTopLevelGroup,
        nodeById: defaultTaxonomyNodeById,
        dimension: 'weight',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootTaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      portfolioBaseCurrency,
      riskSharesByTopLevelGroup,
      targetLinesByTargetSetId,
    ],
  )
  const taaWeightGapRows = taaWeightGapResult.value
  const saaRiskGapResult = useMemo(
    () =>
      buildTargetGapRows({
        targetSet: activeRootSaaTargetSet,
        targetLines: targetLinesByTargetSetId.get(activeRootSaaTargetSet?.target_set_id ?? '') ?? [],
        currentGroups: currentPlanningGroups,
        riskSharesByGroup: riskSharesByTopLevelGroup,
        riskShareErrors: topLevelRiskContributionResult.errors,
        nodeById: defaultTaxonomyNodeById,
        dimension: 'risk_budget',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootSaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      portfolioBaseCurrency,
      riskSharesByTopLevelGroup,
      topLevelRiskContributionResult.errors,
      targetLinesByTargetSetId,
    ],
  )
  const saaRiskGapRows = saaRiskGapResult.value
  const taaRiskGapResult = useMemo(
    () =>
      buildTargetGapRows({
        targetSet: activeRootTaaTargetSet,
        targetLines: targetLinesByTargetSetId.get(activeRootTaaTargetSet?.target_set_id ?? '') ?? [],
        currentGroups: currentPlanningGroups,
        riskSharesByGroup: riskSharesByTopLevelGroup,
        riskShareErrors: topLevelRiskContributionResult.errors,
        nodeById: defaultTaxonomyNodeById,
        dimension: 'risk_budget',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootTaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      portfolioBaseCurrency,
      riskSharesByTopLevelGroup,
      topLevelRiskContributionResult.errors,
      targetLinesByTargetSetId,
    ],
  )
  const taaRiskGapRows = taaRiskGapResult.value
  const instrumentRiskContributionResult = useMemo(
    () =>
      riskFrequencyReady
        ? buildRiskContributionRows(instrumentReturnSeries, contributionAsOfDate, contributionSettings)
        : riskBasisPending
          ? riskOk([] satisfies RiskContributionRow[])
          : riskFail(portfolioRiskFrequencyErrors, [] satisfies RiskContributionRow[]),
    [
      instrumentReturnSeries,
      contributionAsOfDate,
      contributionSettings,
      riskBasisPending,
      portfolioRiskFrequencyErrors,
      riskFrequencyReady,
    ],
  )
  const selectedMatrixScopeLabel =
    matrixTaxonomyScopeOptions.find((option) => option.value === matrixScopeNodeId)?.label ?? 'Top Level'

  function renderRiskErrors(errors: string[]) {
    if (!errors.length) {
      return null
    }
    return (
      <div className="inline-notice inline-notice-error">
        {errors.map((error, index) => (
          <div key={`${index}:${error}`}>{error}</div>
        ))}
      </div>
    )
  }

  function renderCorrelationMatrix(matrix: CorrelationMatrix, emptyLabel: string) {
    if (!matrix.groups.length) {
      return <div className="price-chart-empty">{emptyLabel}</div>
    }

    return (
      <div className="risk-matrix-scroll risk-covariance-scroll">
        <table className="risk-heatmap-table risk-covariance-table">
          <thead>
            <tr>
              <th>Group</th>
              {matrix.groups.map((group) => (
                <th key={group.key} title={`${group.label}; weight ${formatPercent(group.weight)}`}>
                  {group.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.groups.map((rowGroup, rowIndex) => (
              <tr key={rowGroup.key}>
                <th title={`${rowGroup.label}; ${rowGroup.observationCount} return observations`}>{rowGroup.label}</th>
                {matrix.groups.map((columnGroup, columnIndex) => {
                  const cell = matrix.cells[rowIndex]?.[columnIndex]
                  return (
                    <td
                      key={`${rowGroup.key}:${columnGroup.key}`}
                      className="risk-heatmap-cell"
                      style={heatmapCellStyle(cell?.value, matrix.maxAbs)}
                      title={`${rowGroup.label} x ${columnGroup.label}; ${cell?.observationCount ?? 0} paired observations`}
                    >
                      {formatCorrelation(cell?.value)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  function renderRiskContributionTable(result: RiskCalculationResult<RiskContributionRow[]>) {
    if (result.errors.length) {
      return renderRiskErrors(result.errors)
    }
    const rows = result.value
    const maxAbsRiskShare = Math.max(
      0,
      ...rows
        .map((row) => row.riskShare)
        .filter((value): value is number => value != null)
        .map((value) => Math.abs(value)),
    )
    if (!rows.length) {
      return <div className="price-chart-empty">No rows.</div>
    }

    return (
      <div className="risk-matrix-scroll">
        <table className="risk-heatmap-table risk-contribution-table">
          <thead>
            <tr>
              <th>Instrument</th>
              <th>Risk Weight</th>
              <th>Annualized Vol</th>
              <th>Risk Share</th>
              <th>Ann Var Ctr</th>
              <th>Obs</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.groupKey}>
                <th>{row.groupLabel}</th>
                <td>{formatPercent(row.weight)}</td>
                <td>{formatPercent(row.annualizedVolatility)}</td>
                <td
                  className={`risk-heatmap-cell ${signedValueClass(row.riskShare)}`}
                  style={heatmapCellStyle(row.riskShare, maxAbsRiskShare)}
                >
                  {signedPercent(row.riskShare)}
                </td>
                <td className={signedValueClass(row.contributionToVariance)}>
                  {formatPercent(row.contributionToVariance, 3)}
                </td>
                <td>{formatNumber(row.observationCount, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <PortfolioWorkspaceLayout activeSection="Risk" toolbarLabel="View: Risk Analytics">
      <section className="portfolio-detail-surface risk-page-surface">
        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {performanceError ? <div className="inline-notice inline-notice-error">{performanceError}</div> : null}
        {contributionError ? <div className="inline-notice inline-notice-error">{contributionError}</div> : null}
        {holdingsWorkspace && !riskDataLoading ? renderRiskErrors(portfolioRiskFrequencyErrors) : null}

        {workspaceLoading ? (
          <CalculationStatus />
        ) : null}

        {riskDataLoading && !performanceWorkspace && !instrumentContribution && !riskBasisWorkspace ? (
          <CalculationStatus />
        ) : null}

        {!workspaceLoading && !holdingsWorkspace && !workspaceError ? (
          <div className="empty-state">No data.</div>
        ) : null}

        {holdingsWorkspace ? (
          <>
            <section className="performance-section-block risk-rolling-section">
              <div className="risk-chart-controls overview-chart-controls">
                <BenchmarkSearchBox
                  instruments={benchmarkInstruments}
                  selectedInstrumentId={benchmarkInstrumentId}
                  searchValue={benchmarkSearch}
                  onSearchChange={setBenchmarkSearch}
                  onSelectInstrument={(instrument) => {
                    setBenchmarkInstrumentId(instrument.instrument_id)
                    setBenchmarkSearch(benchmarkInstrumentLabel(instrument))
                    setBenchmarkError(null)
                  }}
                  onClear={() => {
                    setBenchmarkInstrumentId('')
                    setBenchmarkSearch('')
                    setBenchmarkChart(null)
                    setBenchmarkError(null)
                  }}
                  placeholder="Compare benchmark..."
                />
                <RiskSettingsMenu
                  label="Rolling risk"
                  settings={rollingSettings}
                  onChange={setRollingSettings}
                  includeChartStyle
                />
              </div>
              {benchmarkLoading ? <div className="portfolio-detail-meta">Loading</div> : null}
              {benchmarkError ? <div className="overview-benchmark-error">{benchmarkError}</div> : null}
              <div className="risk-rolling-grid">
                <RollingRiskMetricChart
                  title="Rolling Annualized Volatility"
                  metricLabel={riskModelLabel(rollingSettings.modelId)}
                  windowLabel={rollingWindow}
                  points={rollingVolatilityPoints}
                  benchmarkPoints={benchmarkRollingVolatilityPoints}
                  benchmarkLabel={benchmarkLabel}
                  displayStyle={rollingSettings.chartStyle}
                  formatValue={(value) => formatPercent(value)}
                  emptyLabel="Insufficient data."
                />
                <RollingRiskMetricChart
                  title="Rolling Sharpe Ratio"
                  metricLabel={riskModelLabel(rollingSettings.modelId)}
                  windowLabel={rollingWindow}
                  points={rollingSharpePoints}
                  benchmarkPoints={benchmarkRollingSharpePoints}
                  benchmarkLabel={benchmarkLabel}
                  displayStyle={rollingSettings.chartStyle}
                  formatValue={(value) => formatNumber(value, 2)}
                  emptyLabel="Insufficient data."
                />
              </div>
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar risk-section-toolbar">
                <div>
                  <div className="panel-title">Correlation Matrix</div>
                  <div className="portfolio-detail-meta">
                    {matrixAsOfDate
                      ? `${matrixAsOfDate}; ${portfolioRiskFrequency.statusLabel}; ${windowLabel(matrixSettings.lookbackDays)} ${riskModelLabel(matrixSettings.modelId)}`
                      : 'No active matrix date'}
                  </div>
                </div>
                <div className="risk-section-actions">
                  <label className="risk-scope-select">
                    <span>Taxonomy Scope</span>
                    <select value={matrixScopeNodeId} onChange={(event) => setMatrixScopeNodeId(event.target.value)}>
                      {matrixTaxonomyScopeOptions.map((option) => (
                        <option key={option.value || 'root'} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <RiskSettingsMenu
                    label="Correlation matrix"
                    settings={matrixSettings}
                    onChange={setMatrixSettings}
                  />
                </div>
              </div>
              <RiskDateTimeline
                dates={riskDates}
                value={matrixAsOfDate}
                onChange={setMatrixAsOfDate}
                label="Matrix as of"
              />
              <div className="risk-correlation-stack">
                <div className="risk-matrix-panel">
                  <div className="risk-matrix-panel-title">All Instruments</div>
                  {renderCorrelationMatrix(instrumentCorrelationMatrix, 'No matrix.')}
                </div>
                <div className="risk-matrix-panel">
                  <div className="risk-matrix-panel-title">Taxonomy: {selectedMatrixScopeLabel}</div>
                  {matrixTaxonomySlicesResult.errors.length
                    ? renderRiskErrors(matrixTaxonomySlicesResult.errors)
                    : renderCorrelationMatrix(
                        taxonomyCorrelationMatrix,
                        defaultPlanningTaxonomy
                          ? 'No matrix.'
                          : 'No taxonomy.',
                      )}
                </div>
              </div>
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar risk-section-toolbar">
                <div>
                  <div className="panel-title">Current Drift</div>
                  <div className="portfolio-detail-meta">
                    {defaultPlanningTaxonomy
                      ? `${defaultPlanningTaxonomy.name}; ${holdingsWorkspace.as_of_date}; ${portfolioRiskFrequency.statusLabel}; ${windowLabel(driftSettings.lookbackDays)} ${riskModelLabel(driftSettings.modelId)}`
                      : 'No taxonomy'}
                  </div>
                </div>
                <RiskSettingsMenu
                  label="Current drift"
                  settings={driftSettings}
                  onChange={setDriftSettings}
                  includeContributionMode
                />
              </div>
              {renderRiskErrors([
                ...activeRootSaaTargetSetResult.errors,
                ...activeRootTaaTargetSetResult.errors,
                ...topLevelTaxonomySlicesResult.errors,
                ...currentPlanningGroupsResult.errors,
              ])}
              <div className="risk-target-grid">
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">SAA Weight Target Gap</div>
                  {saaWeightGapResult.errors.length ? (
                    renderRiskErrors(saaWeightGapResult.errors)
                  ) : (
                    <RiskTargetGapChart
                      rows={saaWeightGapRows}
                      ariaLabel="SAA weight target drift"
                      emptyLabel="No SAA weight target."
                    />
                  )}
                </div>
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">TAA Weight Target Gap</div>
                  {taaWeightGapResult.errors.length ? (
                    renderRiskErrors(taaWeightGapResult.errors)
                  ) : (
                    <RiskTargetGapChart
                      rows={taaWeightGapRows}
                      ariaLabel="TAA weight target drift"
                      emptyLabel="No TAA weight target."
                    />
                  )}
                </div>
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">SAA Risk Target Gap</div>
                  {saaRiskGapResult.errors.length ? (
                    renderRiskErrors(saaRiskGapResult.errors)
                  ) : (
                    <RiskTargetGapChart
                      rows={saaRiskGapRows}
                      ariaLabel="SAA risk budget target gap"
                      emptyLabel="No SAA risk target."
                      currentLabel="Risk Share"
                      targetLabel="Risk Target"
                    />
                  )}
                </div>
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">TAA Risk Target Gap</div>
                  {taaRiskGapResult.errors.length ? (
                    renderRiskErrors(taaRiskGapResult.errors)
                  ) : (
                    <RiskTargetGapChart
                      rows={taaRiskGapRows}
                      ariaLabel="TAA risk budget target gap"
                      emptyLabel="No TAA risk target."
                      currentLabel="Risk Share"
                      targetLabel="Risk Target"
                    />
                  )}
                </div>
              </div>
            </section>

            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar risk-section-toolbar">
                <div>
                  <div className="panel-title">Risk Contribution</div>
                  <div className="portfolio-detail-meta">
                    {contributionAsOfDate
                      ? `${contributionAsOfDate}; ${portfolioRiskFrequency.statusLabel}; point-in-time weights with ${windowLabel(contributionSettings.lookbackDays)} covariance`
                      : 'No active risk contribution date'}
                  </div>
                </div>
                <RiskSettingsMenu
                  label="Risk contribution"
                  settings={contributionSettings}
                  onChange={setContributionSettings}
                  includeContributionMode
                />
              </div>
              <RiskDateTimeline
                dates={riskDates}
                value={contributionAsOfDate}
                onChange={setContributionAsOfDate}
                label="Contribution as of"
              />
              {renderRiskContributionTable(instrumentRiskContributionResult)}
            </section>
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
