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
  getPortfolioPerformanceContribution,
  getPortfolioTaxonomyCatalog,
  type HoldingsWorkspaceResponse,
  type PortfolioAccountsWorkspaceResponse,
  type PortfolioInstrumentPriceChartResponse,
  type PortfolioContributionReportResponse,
  type PortfolioDailyPerformancePoint,
  type PortfolioPerformanceResponse,
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
type ContributionSlice = PortfolioContributionReportResponse['daily_slices'][number]

type ReturnSlice = Pick<
  ContributionSlice,
  | 'as_of_date'
  | 'group_key'
  | 'group_label'
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
  { value: 'sample_covariance', label: 'Sample', detail: 'Plain sample covariance' },
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

const CALCULATION_FREQUENCY_RANK: Record<CalculationFrequency, number> = {
  daily: 0,
  weekly: 1,
  monthly: 2,
}

function inferObservationFrequency(dateKeys: string[]): CalculationFrequency {
  const sortedDates = [...new Set(dateKeys)].sort()
  if (sortedDates.length < 2) {
    return 'daily'
  }
  const gaps = sortedDates
    .slice(1)
    .map((dateKey, index) => dayDiff(sortedDates[index], dateKey))
    .filter((value): value is number => value != null && value > 0)
  if (!gaps.length) {
    return 'daily'
  }
  const sortedGaps = [...gaps].sort((left, right) => left - right)
  const medianGap = sortedGaps[Math.floor(sortedGaps.length / 2)]
  const dailyLikeCount = gaps.filter((gap) => gap <= 3).length
  const weeklyLikeCount = gaps.filter((gap) => gap >= 4 && gap <= 10).length
  const monthlyLikeCount = gaps.filter((gap) => gap >= 18 && gap <= 45).length

  if (gaps.length < 5) {
    if (dailyLikeCount) {
      return 'daily'
    }
    if (weeklyLikeCount === gaps.length) {
      return 'weekly'
    }
    if (monthlyLikeCount === gaps.length) {
      return 'monthly'
    }
    return 'daily'
  }

  if (medianGap <= 3) {
    return 'daily'
  }
  if (medianGap <= 10) {
    return 'weekly'
  }
  if (dailyLikeCount && monthlyLikeCount < gaps.length * 0.6) {
    return 'daily'
  }
  return 'monthly'
}

function defaultCalculationFrequency(series: GroupReturnSeries[]): CalculationFrequency {
  return series
    .map((item) => inferObservationFrequency([...item.returnsByDate.keys()]))
    .reduce<CalculationFrequency>(
      (current, next) =>
        CALCULATION_FREQUENCY_RANK[next] > CALCULATION_FREQUENCY_RANK[current] ? next : current,
      'daily',
    )
}

function riskFrequencyProfile(series: GroupReturnSeries[]): RiskFrequencyProfile {
  const frequency = defaultCalculationFrequency(series)
  const frequencies = series.map((item) => inferObservationFrequency([...item.returnsByDate.keys()]))
  const unique = new Set(frequencies)
  const mixLabel =
    unique.size > 1
      ? unique.has('daily') && unique.has('weekly') && !unique.has('monthly')
        ? 'mixed daily/weekly data'
        : 'mixed frequencies'
      : `${CALCULATION_FREQUENCY_LABELS[frequency].toLowerCase()} data`
  return {
    frequency,
    statusLabel: `${CALCULATION_FREQUENCY_LABELS[frequency]} risk basis - ${mixLabel}`,
  }
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

function populationCovariance(leftValues: number[], rightValues: number[]) {
  if (leftValues.length < 2 || rightValues.length !== leftValues.length) {
    return null
  }
  const leftMean = leftValues.reduce((total, value) => total + value, 0) / leftValues.length
  const rightMean = rightValues.reduce((total, value) => total + value, 0) / rightValues.length
  return (
    leftValues.reduce((total, leftValue, index) => total + (leftValue - leftMean) * (rightValues[index] - rightMean), 0) /
    leftValues.length
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
  const covariance = populationCovariance(leftValues, rightValues)
  const leftVariance = populationCovariance(leftValues, leftValues)
  const rightVariance = populationCovariance(rightValues, rightValues)
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
    return populationCovariance(leftValues, rightValues)
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
      ? populationCovariance(values, values)
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
  const volatility = Math.sqrt(Math.max(variance, 0))
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
    return {
      value: correlation * Math.sqrt(Math.max(leftVariance, 0)) * Math.sqrt(Math.max(rightVariance, 0)),
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
    value: Math.max(-1, Math.min(1, correlation)),
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
    return [] satisfies RiskContributionRow[]
  }
  const activeSeries = series
    .map((item) => ({
      item,
      weight: weightAtOrBefore(item, asOfDate),
      observationCount: returnObservationCount(item, asOfDate, settings.lookbackDays),
    }))
    .filter((item) => item.observationCount >= 2 && Math.abs(item.weight ?? 0) > 1e-9)
  const grossWeight = activeSeries.reduce((total, item) => total + Math.abs(item.weight ?? 0), 0)
  if (!activeSeries.length || grossWeight <= 1e-12) {
    return [] satisfies RiskContributionRow[]
  }

  const firstActiveSeries = activeSeries[0]
  if (!firstActiveSeries) {
    return [] satisfies RiskContributionRow[]
  }
  const weights = activeSeries.map((item) => (item.weight ?? 0) / grossWeight)
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
    return [] satisfies RiskContributionRow[]
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
      const leftValues = valuesByGroup.get(rowSeries.item.groupKey) ?? []
      const rightValues = valuesByGroup.get(columnSeries.item.groupKey) ?? []
      const covarianceValue = annualizedCovarianceFromValues(leftValues, rightValues, commonDates, settings.modelId)
      if (covarianceValue == null || !Number.isFinite(covarianceValue)) {
        return [] satisfies RiskContributionRow[]
      }
      covarianceRow.push(covarianceValue)
    }
    covarianceMatrix.push(covarianceRow)
  }
  const marginal = covarianceMatrix.map((row) =>
    row.reduce((total, covarianceValue, columnIndex) => total + covarianceValue * weights[columnIndex], 0),
  )
  const variance = weights.reduce((total, weight, index) => total + weight * marginal[index], 0)
  const signedContributions = activeSeries.map((_, index) => weights[index] * marginal[index])
  const absoluteContributionTotal = signedContributions.reduce((total, contribution) => total + Math.abs(contribution), 0)

  return activeSeries
    .map(({ item }, index) => {
      const ownValues = valuesByGroup.get(item.groupKey) ?? []
      const ownVariance = annualizedVarianceFromValues(ownValues, commonDates, settings.modelId)
      const contributionToVariance = signedContributions[index]
      const riskShare =
        settings.contributionMode === 'abs'
          ? absoluteContributionTotal > 1e-12
            ? Math.abs(contributionToVariance) / absoluteContributionTotal
            : null
          : variance > 1e-12
            ? contributionToVariance / variance
            : null
      return {
        groupKey: item.groupKey,
        groupLabel: item.groupLabel,
        weight: weights[index],
        annualizedVolatility: ownVariance != null ? Math.sqrt(Math.max(ownVariance, 0)) : null,
        riskShare,
        contributionToVariance,
        observationCount: commonDates.length,
      } satisfies RiskContributionRow
    })
    .sort((left, right) => Math.abs(right.riskShare ?? 0) - Math.abs(left.riskShare ?? 0))
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

function findActiveAssignment(
  catalog: PortfolioTaxonomyCatalogResponse | null,
  taxonomyId: string,
  targetScope: TaxonomyAssignmentScope,
  entityId: string,
  referenceDate: string,
) {
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

  return matches.length === 1 ? matches[0] : null
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
    return []
  }

  const nodeById = buildNodeLookup(catalog, taxonomy.taxonomy_id)
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
      return_observation_eligible: boolean
    }
  >()
  const eligibilityByGroup = new Map<string, boolean[]>()

  slices.forEach((slice) => {
    const assignment = findActiveAssignment(
      catalog,
      taxonomy.taxonomy_id,
      'instrument',
      slice.group_key,
      slice.as_of_date,
    )
    const scopedNode = resolveScopedTaxonomyNode(assignment?.taxonomy_node_id, nodeById, scopeNodeId)
    if (!scopedNode) {
      if (scopeNodeId) {
        return
      }
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
      return_observation_eligible: false,
    }

    current.beginning_value_base = addMeasure(current.beginning_value_base, finiteNumber(slice.beginning_value_base))
    current.ending_value_base = addMeasure(current.ending_value_base, finiteNumber(slice.ending_value_base))
    current.beginning_weight = addMeasure(current.beginning_weight, finiteNumber(slice.beginning_weight))
    current.ending_weight = addMeasure(current.ending_weight, finiteNumber(slice.ending_weight))
    current.total_pnl = addMeasure(current.total_pnl, finiteNumber(slice.total_pnl))
    current.daily_contribution = addMeasure(current.daily_contribution, finiteNumber(slice.daily_contribution))
    const eligibility = eligibilityByGroup.get(aggregateKey) ?? []
    eligibility.push(Boolean(slice.return_observation_eligible))
    eligibilityByGroup.set(aggregateKey, eligibility)
    grouped.set(aggregateKey, current)
  })

  return [...grouped.values()].map((slice) => {
    const dailyReturn =
      slice.total_pnl != null && slice.beginning_value_base != null && slice.beginning_value_base > 1e-9
        ? slice.total_pnl / slice.beginning_value_base
        : null
    const eligibility = eligibilityByGroup.get(`${slice.as_of_date}:${slice.group_key}`) ?? []
    return {
      ...slice,
      daily_return: dailyReturn,
      return_observation_eligible: dailyReturn != null && eligibility.length > 0 && eligibility.every(Boolean),
    }
  }) satisfies ReturnSlice[]
}

function accountValueBase(accountRow: PortfolioAccountsWorkspaceResponse['accounts'][number]) {
  const explicitValue = finiteNumber(accountRow.account_value_base)
  if (explicitValue != null) {
    return explicitValue
  }
  return (finiteNumber(accountRow.derived_cash_balance_base) ?? 0) + (finiteNumber(accountRow.position_market_value) ?? 0)
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
    return []
  }

  const taxonomyId = taxonomy.taxonomy_id
  const currentReferenceDate = referenceDate
  const nodeById = buildNodeLookup(catalog, taxonomyId)
  const groups = new Map<string, CurrentPlanningGroup>()

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
    const assignment = findActiveAssignment(catalog, taxonomyId, targetScope, entityId, currentReferenceDate)
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
    const accountRows = accountsWorkspace?.accounts ?? []
    const cashAccounts = taxonomy.planning_enabled
      ? accountRows.filter((accountRow) => {
          if (accountRow.account.account_type !== 'deposit_account') {
            return false
          }
          const activeCashAssignment = findActiveAssignment(
            catalog,
            taxonomyId,
            'cash_bucket',
            accountRow.account.account_id,
            referenceDate,
          )
          return Boolean(activeCashAssignment) || Math.abs(finiteNumber(accountRow.derived_cash_balance_base) ?? 0) > 1e-9
        })
      : []
    const totalValueBase =
      holdingsWorkspace.rows.reduce((total, row) => total + (finiteNumber(row.market_value_base) ?? 0), 0) +
      cashAccounts.reduce((total, accountRow) => total + (finiteNumber(accountRow.derived_cash_balance_base) ?? 0), 0)

    holdingsWorkspace.rows.forEach((row) => {
      const valueBase = finiteNumber(row.market_value_base)
      const weightInput = totalValueBase > 1e-9 && valueBase != null ? valueBase / totalValueBase : row.allocation
      addEntity({
        targetScope: 'instrument',
        entityId: row.instrument_core.instrument_id,
        valueBase,
        weightInput,
        cashLike: false,
      })
    })

    cashAccounts.forEach((accountRow) => {
      const valueBase = finiteNumber(accountRow.derived_cash_balance_base)
      addEntity({
        targetScope: 'cash_bucket',
        entityId: accountRow.account.account_id,
        valueBase,
        weightInput: totalValueBase > 1e-9 && valueBase != null ? valueBase / totalValueBase : null,
        cashLike: true,
      })
    })
  } else if (taxonomy.primary_assignment_scope === 'cash_bucket') {
    const cashAccounts = (accountsWorkspace?.accounts ?? []).filter(
      (accountRow) => accountRow.account.account_type === 'deposit_account',
    )
    const totalValueBase = cashAccounts.reduce(
      (total, accountRow) => total + (finiteNumber(accountRow.derived_cash_balance_base) ?? 0),
      0,
    )
    cashAccounts.forEach((accountRow) => {
      const valueBase = finiteNumber(accountRow.derived_cash_balance_base)
      addEntity({
        targetScope: 'cash_bucket',
        entityId: accountRow.account.account_id,
        valueBase,
        weightInput: totalValueBase > 1e-9 && valueBase != null ? valueBase / totalValueBase : null,
        cashLike: true,
      })
    })
  } else {
    const accountRows = accountsWorkspace?.accounts ?? []
    const totalValueBase = accountRows.reduce((total, accountRow) => total + accountValueBase(accountRow), 0)
    accountRows.forEach((accountRow) => {
      const valueBase = accountValueBase(accountRow)
      const positionValue = finiteNumber(accountRow.position_market_value) ?? 0
      addEntity({
        targetScope: 'account',
        entityId: accountRow.account.account_id,
        valueBase,
        weightInput: totalValueBase > 1e-9 ? valueBase / totalValueBase : null,
        cashLike: accountRow.account.account_type === 'deposit_account' && Math.abs(positionValue) <= 1e-9,
      })
    })
  }

  return [...groups.values()]
    .filter((group) => group.currentWeight != null || group.currentValueBase != null)
    .sort((left, right) => Math.abs(right.currentWeight ?? 0) - Math.abs(left.currentWeight ?? 0))
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
  nodeById,
  dimension,
  baseCurrency,
}: {
  targetSet: PortfolioTargetSetRecord | null
  targetLines: PortfolioTargetSetLineRecord[]
  currentGroups: CurrentPlanningGroup[]
  riskSharesByGroup: Map<string, number | null>
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>
  dimension: 'weight' | 'risk_budget'
  baseCurrency: string
}) {
  if (!targetSet) {
    return []
  }
  if (dimension === 'weight' && !targetSet.weight_enabled) {
    return []
  }
  if (dimension === 'risk_budget' && !targetSet.risk_budget_enabled) {
    return []
  }

  const currentGroupByKey = new Map(currentGroups.map((group) => [group.groupKey, group] as const))
  const lineByKey = new Map(targetLines.map((line) => [lineDisplayKey(line), line] as const))
  const allKeys = new Set([...currentGroupByKey.keys(), ...lineByKey.keys()])
  const rows: RiskTargetGapChartRow[] = []

  allKeys.forEach((key) => {
    const currentGroup = currentGroupByKey.get(key) ?? null
    const line = lineByKey.get(key) ?? null
    const current =
      dimension === 'weight'
        ? currentGroup?.currentWeight ?? null
        : riskSharesByGroup.has(key)
          ? riskSharesByGroup.get(key) ?? null
          : currentGroup?.hasCashLikeInput && !currentGroup.hasMarketRiskInput
            ? 0
            : null
    const target = line ? (dimension === 'weight' ? line.target_weight ?? null : line.target_risk_share ?? null) : null
    if (current == null && target == null) {
      return
    }
    rows.push({
      id: `${targetSet.target_set_id}:${dimension}:${key}`,
      label: currentGroup?.label ?? (line ? lineDisplayLabel(line, nodeById) : key),
      current,
      target,
      gap: current != null && target != null ? current - target : null,
      detail: currentGroup?.currentValueBase != null ? formatCurrency(currentGroup.currentValueBase, baseCurrency) : undefined,
    })
  })

  return rows.sort((left, right) => Math.abs(right.gap ?? 0) - Math.abs(left.gap ?? 0))
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
                    title={option.detail}
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
                    title={option.detail}
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
                      title={option.detail}
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
  const [riskDataLoading, setRiskDataLoading] = useState(false)
  const [performanceError, setPerformanceError] = useState<string | null>(null)
  const [contributionError, setContributionError] = useState<string | null>(null)
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
      setRiskDataLoading(false)
      setPerformanceError(null)
      setContributionError(null)
      return
    }

    let cancelled = false
    setRiskDataLoading(true)
    setPerformanceError(null)
    setContributionError(null)

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
    ])
      .then(([performanceResult, contributionResult]) => {
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
  const activeRootSaaTargetSet =
    activeRootTargetSets.find((targetSet) => targetSet.target_set_type === 'saa') ?? null
  const activeRootTaaTargetSet =
    activeRootTargetSets.find((targetSet) => targetSet.target_set_type === 'taa') ?? null
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
  const portfolioRiskFrequency = useMemo(() => riskFrequencyProfile(rawInstrumentReturnSeries), [rawInstrumentReturnSeries])
  const instrumentReturnSeries = useMemo(
    () =>
      alignReturnSeriesToFrequency(
        rawInstrumentReturnSeries,
        portfolioRiskFrequency.frequency,
        holdingsWorkspace?.as_of_date ?? riskWindowEndDate,
      ),
    [holdingsWorkspace?.as_of_date, portfolioRiskFrequency.frequency, rawInstrumentReturnSeries, riskWindowEndDate],
  )
  const portfolioReturnPointsRaw = useMemo(
    () => buildPortfolioReturnPoints(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace?.daily_series],
  )
  const portfolioReturnPoints = useMemo(
    () =>
      alignReturnPointsToFrequency(
        portfolioReturnPointsRaw,
        portfolioRiskFrequency.frequency,
        holdingsWorkspace?.as_of_date ?? riskWindowEndDate,
      ),
    [holdingsWorkspace?.as_of_date, portfolioReturnPointsRaw, portfolioRiskFrequency.frequency, riskWindowEndDate],
  )
  const benchmarkReturnPointsRaw = useMemo(() => buildBenchmarkReturnPoints(benchmarkChart), [benchmarkChart])
  const benchmarkReturnPoints = useMemo(
    () =>
      alignReturnPointsToFrequency(
        benchmarkReturnPointsRaw,
        portfolioRiskFrequency.frequency,
        holdingsWorkspace?.as_of_date ?? riskWindowEndDate,
      ),
    [benchmarkReturnPointsRaw, holdingsWorkspace?.as_of_date, portfolioRiskFrequency.frequency, riskWindowEndDate],
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
    if (riskDates.length && !riskDates.includes(matrixAsOfDate)) {
      setMatrixAsOfDate(riskDates[riskDates.length - 1])
    }
  }, [matrixAsOfDate, riskDates])

  useEffect(() => {
    if (riskDates.length && !riskDates.includes(contributionAsOfDate)) {
      setContributionAsOfDate(riskDates[riskDates.length - 1])
    }
  }, [contributionAsOfDate, riskDates])

  const matrixTaxonomyScopeOptions = useMemo(
    () => taxonomyScopeOptions(defaultPlanningTaxonomy, taxonomyCatalog),
    [defaultPlanningTaxonomy, taxonomyCatalog],
  )
  const matrixTaxonomySlices = useMemo(
    () => buildScopedTaxonomySlices(instrumentSlices, taxonomyCatalog, defaultPlanningTaxonomy, matrixScopeNodeId),
    [defaultPlanningTaxonomy, instrumentSlices, matrixScopeNodeId, taxonomyCatalog],
  )
  const matrixTaxonomySeries = useMemo(() => buildGroupReturnSeries(matrixTaxonomySlices), [matrixTaxonomySlices])
  const alignedMatrixTaxonomySeries = useMemo(
    () =>
      alignReturnSeriesToFrequency(
        matrixTaxonomySeries,
        portfolioRiskFrequency.frequency,
        holdingsWorkspace?.as_of_date ?? riskWindowEndDate,
      ),
    [holdingsWorkspace?.as_of_date, matrixTaxonomySeries, portfolioRiskFrequency.frequency, riskWindowEndDate],
  )
  const topLevelTaxonomySlices = useMemo(
    () => buildScopedTaxonomySlices(instrumentSlices, taxonomyCatalog, defaultPlanningTaxonomy, ''),
    [defaultPlanningTaxonomy, instrumentSlices, taxonomyCatalog],
  )
  const topLevelTaxonomySeries = useMemo(
    () => buildGroupReturnSeries(topLevelTaxonomySlices),
    [topLevelTaxonomySlices],
  )
  const alignedTopLevelTaxonomySeries = useMemo(
    () =>
      alignReturnSeriesToFrequency(
        topLevelTaxonomySeries,
        portfolioRiskFrequency.frequency,
        holdingsWorkspace?.as_of_date ?? riskWindowEndDate,
      ),
    [holdingsWorkspace?.as_of_date, portfolioRiskFrequency.frequency, riskWindowEndDate, topLevelTaxonomySeries],
  )
  const instrumentCorrelationMatrix = useMemo(
    () => buildCorrelationMatrix(instrumentReturnSeries, matrixAsOfDate, matrixSettings),
    [instrumentReturnSeries, matrixAsOfDate, matrixSettings],
  )
  const taxonomyCorrelationMatrix = useMemo(
    () => buildCorrelationMatrix(alignedMatrixTaxonomySeries, matrixAsOfDate, matrixSettings),
    [alignedMatrixTaxonomySeries, matrixAsOfDate, matrixSettings],
  )
  const topLevelRiskContributionRows = useMemo(
    () => buildRiskContributionRows(alignedTopLevelTaxonomySeries, holdingsWorkspace?.as_of_date ?? '', driftSettings),
    [alignedTopLevelTaxonomySeries, driftSettings, holdingsWorkspace?.as_of_date],
  )
  const riskSharesByTopLevelGroup = useMemo(
    () => new Map(topLevelRiskContributionRows.map((row) => [row.groupKey, row.riskShare] as const)),
    [topLevelRiskContributionRows],
  )
  const currentPlanningGroups = useMemo(
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

  const saaWeightGapRows = useMemo(
    () =>
      buildTargetGapRows({
        targetSet: activeRootSaaTargetSet,
        targetLines: targetLinesByTargetSetId.get(activeRootSaaTargetSet?.target_set_id ?? '') ?? [],
        currentGroups: currentPlanningGroups,
        riskSharesByGroup: riskSharesByTopLevelGroup,
        nodeById: defaultTaxonomyNodeById,
        dimension: 'weight',
        baseCurrency: holdingsWorkspace?.base_currency ?? 'USD',
      }),
    [
      activeRootSaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      holdingsWorkspace?.base_currency,
      riskSharesByTopLevelGroup,
      targetLinesByTargetSetId,
    ],
  )
  const taaWeightGapRows = useMemo(
    () =>
      buildTargetGapRows({
        targetSet: activeRootTaaTargetSet,
        targetLines: targetLinesByTargetSetId.get(activeRootTaaTargetSet?.target_set_id ?? '') ?? [],
        currentGroups: currentPlanningGroups,
        riskSharesByGroup: riskSharesByTopLevelGroup,
        nodeById: defaultTaxonomyNodeById,
        dimension: 'weight',
        baseCurrency: holdingsWorkspace?.base_currency ?? 'USD',
      }),
    [
      activeRootTaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      holdingsWorkspace?.base_currency,
      riskSharesByTopLevelGroup,
      targetLinesByTargetSetId,
    ],
  )
  const saaRiskGapRows = useMemo(
    () =>
      buildTargetGapRows({
        targetSet: activeRootSaaTargetSet,
        targetLines: targetLinesByTargetSetId.get(activeRootSaaTargetSet?.target_set_id ?? '') ?? [],
        currentGroups: currentPlanningGroups,
        riskSharesByGroup: riskSharesByTopLevelGroup,
        nodeById: defaultTaxonomyNodeById,
        dimension: 'risk_budget',
        baseCurrency: holdingsWorkspace?.base_currency ?? 'USD',
      }),
    [
      activeRootSaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      holdingsWorkspace?.base_currency,
      riskSharesByTopLevelGroup,
      targetLinesByTargetSetId,
    ],
  )
  const taaRiskGapRows = useMemo(
    () =>
      buildTargetGapRows({
        targetSet: activeRootTaaTargetSet,
        targetLines: targetLinesByTargetSetId.get(activeRootTaaTargetSet?.target_set_id ?? '') ?? [],
        currentGroups: currentPlanningGroups,
        riskSharesByGroup: riskSharesByTopLevelGroup,
        nodeById: defaultTaxonomyNodeById,
        dimension: 'risk_budget',
        baseCurrency: holdingsWorkspace?.base_currency ?? 'USD',
      }),
    [
      activeRootTaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      holdingsWorkspace?.base_currency,
      riskSharesByTopLevelGroup,
      targetLinesByTargetSetId,
    ],
  )
  const instrumentRiskContributionRows = useMemo(
    () => buildRiskContributionRows(instrumentReturnSeries, contributionAsOfDate, contributionSettings),
    [instrumentReturnSeries, contributionAsOfDate, contributionSettings],
  )
  const selectedMatrixScopeLabel =
    matrixTaxonomyScopeOptions.find((option) => option.value === matrixScopeNodeId)?.label ?? 'Top Level'

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

  function renderRiskContributionTable(rows: RiskContributionRow[]) {
    const maxAbsRiskShare = Math.max(
      0,
      ...rows
        .map((row) => row.riskShare)
        .filter((value): value is number => value != null)
        .map((value) => Math.abs(value)),
    )
    if (!rows.length) {
      return <div className="price-chart-empty">No instrument risk contribution rows are available for this as-of date and lookback.</div>
    }

    return (
      <div className="risk-matrix-scroll">
        <table className="risk-heatmap-table risk-contribution-table">
          <thead>
            <tr>
              <th>Instrument</th>
              <th title="Normalized within instruments that participate in covariance risk. Cash is excluded unless modelled as a market factor.">
                Risk Weight
              </th>
              <th>Annualized Vol</th>
              <th>Risk Share</th>
              <th title="Signed annualized component contribution to variance before risk-share normalization.">Ann Var Ctr</th>
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

        {workspaceLoading ? (
          <CalculationStatus label="Loading holdings, accounts, taxonomy, and target context…" />
        ) : null}

        {riskDataLoading && !performanceWorkspace && !instrumentContribution ? (
          <CalculationStatus label="Building return series, correlations, and risk contribution slices…" />
        ) : null}

        {!workspaceLoading && !holdingsWorkspace && !workspaceError ? (
          <div className="empty-state">No risk workspace is available for this portfolio.</div>
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
              {benchmarkLoading ? <div className="portfolio-detail-meta">Loading benchmark risk path…</div> : null}
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
                  emptyLabel="Not enough return observations for a rolling volatility curve."
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
                  emptyLabel="Not enough return observations for a rolling Sharpe curve."
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
                  {renderCorrelationMatrix(instrumentCorrelationMatrix, 'No all-instrument correlation matrix is available.')}
                </div>
                <div className="risk-matrix-panel">
                  <div className="risk-matrix-panel-title">Taxonomy: {selectedMatrixScopeLabel}</div>
                  {renderCorrelationMatrix(
                    taxonomyCorrelationMatrix,
                    defaultPlanningTaxonomy
                      ? 'No taxonomy correlation matrix is available for this scope.'
                      : 'Configure a default planning taxonomy to build the taxonomy correlation matrix.',
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
                      : 'Default planning taxonomy is not configured'}
                  </div>
                </div>
                <RiskSettingsMenu
                  label="Current drift"
                  settings={driftSettings}
                  onChange={setDriftSettings}
                  includeContributionMode
                />
              </div>
              <div className="risk-target-grid">
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">SAA Weight Target Gap</div>
                  <RiskTargetGapChart
                    rows={saaWeightGapRows}
                    ariaLabel="SAA weight target drift"
                    emptyLabel="No active SAA weight target is configured."
                  />
                </div>
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">TAA Weight Target Gap</div>
                  <RiskTargetGapChart
                    rows={taaWeightGapRows}
                    ariaLabel="TAA weight target drift"
                    emptyLabel="No active TAA weight target is configured."
                  />
                </div>
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">SAA Risk Target Gap</div>
                  <RiskTargetGapChart
                    rows={saaRiskGapRows}
                    ariaLabel="SAA risk budget target gap"
                    emptyLabel="No active SAA risk target is configured or risk shares are unavailable."
                    currentLabel="Risk Share"
                    targetLabel="Risk Target"
                  />
                </div>
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">TAA Risk Target Gap</div>
                  <RiskTargetGapChart
                    rows={taaRiskGapRows}
                    ariaLabel="TAA risk budget target gap"
                    emptyLabel="No active TAA risk target is configured or risk shares are unavailable."
                    currentLabel="Risk Share"
                    targetLabel="Risk Target"
                  />
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
              {renderRiskContributionTable(instrumentRiskContributionRows)}
            </section>
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
