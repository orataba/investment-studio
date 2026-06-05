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
  getPortfolioTaxonomyCatalog,
  type HoldingsWorkspaceResponse,
  type PortfolioAccountsWorkspaceResponse,
  type PortfolioInstrumentPriceChartResponse,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioTargetSetLineRecord,
  type PortfolioTargetSetRecord,
  type SharedInstrumentRecord,
  type TaxonomyAssignmentScope,
} from '../lib/api'
import { formatCurrency, formatLabel, formatNumber, formatPercent } from '../lib/format'

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
const DEFAULT_RISK_LOOKBACK_DAYS = 90
const DEFAULT_RISK_ANALYTICS_LOOKBACK_DAYS = 30
const DEFAULT_RISK_MODEL_ID = 'ewma_vol_shrinkage_corr_covariance'
const MATRIX_SCOPE_ALL_INSTRUMENTS = '__all_instruments__'
const RISK_PAGE_SETTINGS_STORAGE_KEY = 'yungu.portfolio.risk.settings.v1'
const INSUFFICIENT_DATA_MESSAGE = 'Insufficient data.'

type RiskModelId = 'ewma_vol_shrinkage_corr_covariance' | 'ewma_covariance' | 'sample_covariance'
type RiskContributionMode = 'signed' | 'abs'
type CalculationFrequency = 'daily' | 'weekly' | 'monthly'

const SAMPLE_RISK_MODEL_ID: RiskModelId = 'sample_covariance'

type RiskWindowSettingsState = {
  lookbackDays: number
}

type RiskSettingsState = RiskWindowSettingsState & {
  modelId: RiskModelId
  contributionMode: RiskContributionMode
  parameters?: Record<string, unknown>
}

type RollingRiskSettingsState = RiskWindowSettingsState & {
  chartStyle: RiskChartDisplayStyle
}

function hasChartStyle<TSettings extends RiskWindowSettingsState>(
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

type TargetGapComparatorRow = {
  key: string
  id: string
  label: string
  current: number | null
  target: number | null
  gap: number | null
  detail?: string
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
  { value: 30, label: '1M' },
  { value: 90, label: '3M' },
  { value: 180, label: '6M' },
  { value: 366, label: '12M' },
  { value: 730, label: '24M' },
] as const

const RISK_MODEL_OPTIONS: Array<{ value: RiskModelId; label: string; detail: string }> = [
  {
    value: 'ewma_vol_shrinkage_corr_covariance',
    label: 'EWMA + Shrinkage',
    detail: 'EWMA vol + shrunk correlation',
  },
  { value: 'ewma_covariance', label: 'EWMA', detail: 'Exponentially weighted covariance' },
  { value: 'sample_covariance', label: 'Sample', detail: 'Sample covariance, n - 1' },
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

const DEFAULT_RISK_MODEL_PARAMETERS: Record<CalculationFrequency, Record<string, number>> = {
  daily: {
    decay: 0.94,
    vol_decay: 0.9945,
    corr_shrinkage: 0.15,
  },
  weekly: {
    decay: 0.94,
    vol_decay: 0.9737,
    corr_shrinkage: 0.15,
  },
  monthly: {
    decay: 0.94,
    vol_decay: 0.8909,
    corr_shrinkage: 0.15,
  },
}

const DEFAULT_ROLLING_SETTINGS: RollingRiskSettingsState = {
  lookbackDays: DEFAULT_RISK_ANALYTICS_LOOKBACK_DAYS,
  chartStyle: 'mountain',
}

const DEFAULT_MATRIX_SETTINGS: RiskWindowSettingsState = {
  lookbackDays: DEFAULT_RISK_ANALYTICS_LOOKBACK_DAYS,
}

type RiskPageStoredSettings = {
  rolling: RollingRiskSettingsState
  matrix: RiskWindowSettingsState
}

function normalizeRiskLookbackDays(value: unknown, fallback: number) {
  const numericValue = typeof value === 'number' ? value : Number(value)
  return RISK_WINDOW_OPTIONS.some((option) => option.value === numericValue) ? numericValue : fallback
}

function normalizeRiskChartStyle(value: unknown, fallback: RiskChartDisplayStyle) {
  return CHART_STYLE_OPTIONS.some((option) => option.value === value) ? (value as RiskChartDisplayStyle) : fallback
}

function normalizeRiskPageSettings(value: unknown): RiskPageStoredSettings {
  const record = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const rollingRecord =
    record.rolling && typeof record.rolling === 'object' ? (record.rolling as Record<string, unknown>) : {}
  const matrixRecord =
    record.matrix && typeof record.matrix === 'object' ? (record.matrix as Record<string, unknown>) : {}
  return {
    rolling: {
      lookbackDays: normalizeRiskLookbackDays(rollingRecord.lookbackDays, DEFAULT_ROLLING_SETTINGS.lookbackDays),
      chartStyle: normalizeRiskChartStyle(rollingRecord.chartStyle, DEFAULT_ROLLING_SETTINGS.chartStyle),
    },
    matrix: {
      lookbackDays: normalizeRiskLookbackDays(matrixRecord.lookbackDays, DEFAULT_MATRIX_SETTINGS.lookbackDays),
    },
  }
}

function loadRiskPageSettings() {
  if (typeof window === 'undefined') {
    return normalizeRiskPageSettings(null)
  }
  try {
    const rawValue = window.localStorage.getItem(RISK_PAGE_SETTINGS_STORAGE_KEY)
    return normalizeRiskPageSettings(rawValue ? JSON.parse(rawValue) : null)
  } catch {
    return normalizeRiskPageSettings(null)
  }
}

function saveRiskPageSettings(settings: RiskPageStoredSettings) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(RISK_PAGE_SETTINGS_STORAGE_KEY, JSON.stringify(settings))
  } catch {
    return
  }
}

const RISK_MAX_START_GAP_DAYS: Record<CalculationFrequency, number> = {
  daily: 10,
  weekly: 21,
  monthly: 45,
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

function absoluteDayDiff(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00`)
  const rightTime = Date.parse(`${right}T00:00:00`)
  if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
    return null
  }
  return Math.abs((rightTime - leftTime) / 86_400_000)
}

const CALCULATION_FREQUENCY_LABELS: Record<CalculationFrequency, string> = {
  daily: 'Daily',
  weekly: 'Weekly',
  monthly: 'Monthly',
}

function isCalculationFrequency(value: string | null | undefined): value is CalculationFrequency {
  return value === 'daily' || value === 'weekly' || value === 'monthly'
}

function riskFrequencyProfileFromHoldingsWorkspace(
  holdingsWorkspace: HoldingsWorkspaceResponse | null,
): RiskCalculationResult<RiskFrequencyProfile> {
  const unavailableProfile = {
    frequency: 'daily',
    statusLabel: 'Risk basis unavailable',
  } satisfies RiskFrequencyProfile
  if (!holdingsWorkspace) {
    return riskFail('Risk basis requires the holdings workspace response.', unavailableProfile)
  }
  const riskyHoldingCount = holdingsWorkspace.rows.filter((row) => !isCashHoldingRow(row)).length
  const sourceFrequencyCount = Object.values(holdingsWorkspace.risk_basis?.source_frequency_counts ?? {}).reduce(
    (total, count) => total + (Number.isFinite(count) ? count : 0),
    0,
  )
  if (riskyHoldingCount > 0 && sourceFrequencyCount < riskyHoldingCount) {
    return riskFail(
      `Risk basis is incomplete: resolved ${sourceFrequencyCount} source frequencies for ${riskyHoldingCount} active risk holdings.`,
      unavailableProfile,
    )
  }
  const frequency = holdingsWorkspace.risk_basis?.resolved_frequency
  if (!isCalculationFrequency(frequency)) {
    return riskFail(`Risk basis response has invalid calculation frequency: ${frequency || 'missing'}.`, unavailableProfile)
  }
  return riskOk({
    frequency,
    statusLabel:
      holdingsWorkspace.risk_basis?.status_label ||
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

function windowLabel(lookbackDays: number) {
  return RISK_WINDOW_OPTIONS.find((option) => option.value === lookbackDays)?.label ?? `${lookbackDays}D`
}

function riskModelLabel(modelId: RiskModelId) {
  return RISK_MODEL_OPTIONS.find((option) => option.value === modelId)?.label ?? formatLabel(modelId)
}

function isRiskModelId(value: string | null | undefined): value is RiskModelId {
  return value === 'ewma_vol_shrinkage_corr_covariance' || value === 'ewma_covariance' || value === 'sample_covariance'
}

function isRiskContributionMode(value: string | null | undefined): value is RiskContributionMode {
  return value === 'signed' || value === 'abs'
}

function numericParameter(parameters: Record<string, unknown> | undefined, key: string, fallback: number) {
  const value = parameters?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function riskModelParameters(settings: RiskSettingsState, frequency: CalculationFrequency) {
  const minObservations = riskMinObservationsForWindow(frequency, settings.lookbackDays)
  return {
    ...DEFAULT_RISK_MODEL_PARAMETERS[frequency],
    min_observations: minObservations,
    corr_min_observations: minObservations,
    ...(settings.parameters ?? {}),
  }
}

function riskMinObservationsForWindow(frequency: CalculationFrequency, lookbackDays: number) {
  const months = RISK_WINDOW_MONTHS_BY_DAYS[lookbackDays] ?? Math.max(lookbackDays, 1) / (DAYS_PER_YEAR / 12)
  const expectedObservations = months * RISK_OBSERVATIONS_PER_MONTH_BY_FREQUENCY[frequency]
  return Math.max(2, Math.ceil(expectedObservations * RISK_MIN_OBSERVATION_COVERAGE_RATIO))
}

function riskSettingsFromPolicy(policy: HoldingsWorkspaceResponse['risk_policy'] | undefined | null): RiskSettingsState {
  const modelId = isRiskModelId(policy?.covariance_model_id) ? policy.covariance_model_id : DEFAULT_RISK_SETTINGS.modelId
  const contributionMode = isRiskContributionMode(policy?.contribution_mode)
    ? policy.contribution_mode
    : DEFAULT_RISK_SETTINGS.contributionMode
  return {
    lookbackDays: Number.isFinite(policy?.lookback_days) ? Number(policy?.lookback_days) : DEFAULT_RISK_SETTINGS.lookbackDays,
    modelId,
    contributionMode,
    parameters: policy?.parameters ?? undefined,
  }
}

function minReturnObservations(
  frequency: CalculationFrequency,
  lookbackDays: number,
  parameters?: Record<string, unknown>,
) {
  const configured = numericParameter(parameters, 'min_observations', Number.NaN)
  if (Number.isFinite(configured)) {
    return Math.max(2, Math.floor(configured))
  }
  return riskMinObservationsForWindow(frequency, lookbackDays)
}

function assessRiskWindowCoverage(
  dateKeys: string[],
  asOfDate: string,
  lookbackDays: number,
  frequency: CalculationFrequency,
  parameters?: Record<string, unknown>,
) {
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
  const startGapDays = absoluteDayDiff(firstDate, requiredStartDate)
  if (startGapDays == null || startGapDays > RISK_MAX_START_GAP_DAYS[frequency]) {
    return {
      ok: false,
      observationCount,
      error: `Risk window lacks a valid ${frequency} start anchor near ${requiredStartDate}.`,
    }
  }
  const elapsedDays = dayDiff(firstDate, lastDate)
  const minElapsedDays = Math.floor(lookbackDays * RISK_MIN_WINDOW_COVERAGE_RATIO)
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

function commonReturnDateKeys(
  series: GroupReturnSeries[],
  startDate = '',
  endDate = '',
  options: { includeZeroWeight?: boolean } = {},
) {
  const activeSeries = options.includeZeroWeight
    ? series
    : series.filter((item) => Math.abs(item.latestWeight ?? 0) > 1e-9)
  if (!activeSeries.length) {
    return []
  }
  const firstSeries = activeSeries[0]
  if (!firstSeries) {
    return []
  }
  return [...firstSeries.returnsByDate.keys()]
    .filter((dateKey) => (!startDate || dateKey >= startDate) && (!endDate || dateKey <= endDate))
    .filter((dateKey) =>
      activeSeries.every((item) => {
        const value = item.returnsByDate.get(dateKey)
        return value != null && Number.isFinite(value)
      }),
    )
    .sort()
}

function buildCurrentWeightedPortfolioReturnPoints(series: GroupReturnSeries[]) {
  const activeSeries = series.filter((item) => Math.abs(item.latestWeight ?? 0) > 1e-9)
  const commonDates = commonReturnDateKeys(activeSeries)
  return commonDates.map((dateKey) => ({
    date: dateKey,
    value: activeSeries.reduce(
      (total, item) => total + (item.latestWeight ?? 0) * (item.returnsByDate.get(dateKey) ?? 0),
      0,
    ),
  }))
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

function estimateCovarianceFromValues(
  leftValues: number[],
  rightValues: number[],
  modelId: RiskModelId,
  parameters: Record<string, unknown>,
) {
  if (leftValues.length < 2 || rightValues.length !== leftValues.length) {
    return null
  }
  if (modelId === 'sample_covariance') {
    return sampleCovariance(leftValues, rightValues)
  }
  if (modelId === 'ewma_covariance') {
    return ewmaCovariance(leftValues, rightValues, numericParameter(parameters, 'decay', 0.94))
  }

  const volDecay = numericParameter(parameters, 'vol_decay', 0.9945)
  const leftVariance = ewmaCovariance(leftValues, leftValues, volDecay)
  const rightVariance = ewmaCovariance(rightValues, rightValues, volDecay)
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
  const shrunkCorrelation = correlation * (1 - numericParameter(parameters, 'corr_shrinkage', 0.15))
  return shrunkCorrelation * Math.sqrt(Math.max(leftVariance, 0)) * Math.sqrt(Math.max(rightVariance, 0))
}

function estimateCorrelationFromValues(
  leftValues: number[],
  rightValues: number[],
  modelId: RiskModelId,
  parameters: Record<string, unknown>,
) {
  if (leftValues.length < 2 || rightValues.length !== leftValues.length) {
    return null
  }
  if (modelId === 'sample_covariance') {
    return sampleCorrelation(leftValues, rightValues)
  }
  if (modelId === 'ewma_covariance') {
    const decay = numericParameter(parameters, 'decay', 0.94)
    const covariance = ewmaCovariance(leftValues, rightValues, decay)
    const leftVariance = ewmaCovariance(leftValues, leftValues, decay)
    const rightVariance = ewmaCovariance(rightValues, rightValues, decay)
    if (covariance == null || leftVariance == null || rightVariance == null || leftVariance <= 0 || rightVariance <= 0) {
      return null
    }
    return covariance / Math.sqrt(leftVariance * rightVariance)
  }

  const correlation = sampleCorrelation(leftValues, rightValues)
  return correlation == null ? null : correlation * (1 - numericParameter(parameters, 'corr_shrinkage', 0.15))
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

function annualizedVarianceFromValues(
  values: number[],
  dates: string[],
  modelId: RiskModelId,
  parameters: Record<string, unknown>,
) {
  if (values.length < 2) {
    return null
  }
  const variance =
    modelId === 'sample_covariance'
      ? sampleCovariance(values, values)
      : ewmaCovariance(
          values,
          values,
          modelId === 'ewma_covariance'
            ? numericParameter(parameters, 'decay', 0.94)
            : numericParameter(parameters, 'vol_decay', 0.9945),
        )
  const periodsPerYear = annualizationPeriodsPerYear(dates, values.length)
  return variance == null || periodsPerYear == null ? null : variance * periodsPerYear
}

function annualizedCovarianceFromValues(
  leftValues: number[],
  rightValues: number[],
  dates: string[],
  modelId: RiskModelId,
  parameters: Record<string, unknown>,
) {
  const covariance = estimateCovarianceFromValues(leftValues, rightValues, modelId, parameters)
  const periodsPerYear = annualizationPeriodsPerYear(dates, leftValues.length)
  return covariance == null || periodsPerYear == null ? null : covariance * periodsPerYear
}

function estimateWindowRisk(
  returnPoints: ReturnPoint[],
  asOfDate: string,
  lookbackDays: number,
  modelId: RiskModelId,
  parameters: Record<string, unknown>,
  frequency: CalculationFrequency,
) {
  const windowPoints = returnPointsInWindow(returnPoints, asOfDate, lookbackDays)
  const values = windowPoints.map((point) => point.value)
  const dates = windowPoints.map((point) => point.date)
  const coverage = assessRiskWindowCoverage(dates, asOfDate, lookbackDays, frequency, parameters)
  if (!coverage.ok) {
    return { volatility: null, sharpe: null, observationCount: values.length }
  }
  if (values.length < 2) {
    return { volatility: null, sharpe: null, observationCount: values.length }
  }
  const variance = annualizedVarianceFromValues(values, dates, modelId, parameters)
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
  settings: RiskWindowSettingsState,
  metric: 'volatility' | 'sharpe',
  frequency: CalculationFrequency,
) {
  const parameters: Record<string, unknown> = {}
  const sortedPoints = returnPoints.slice().sort((left, right) => left.date.localeCompare(right.date))
  const rollingPoints: RollingRiskMetricPoint[] = []
  sortedPoints.forEach((point) => {
    const risk = estimateWindowRisk(
      sortedPoints,
      point.date,
      settings.lookbackDays,
      SAMPLE_RISK_MODEL_ID,
      parameters,
      frequency,
    )
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
  parameters: Record<string, unknown>,
  frequency: CalculationFrequency,
) {
  if (left.groupKey === right.groupKey) {
    const points = windowReturnPoints(left, asOfDate, lookbackDays)
    const values = points.map((point) => point.value)
    const dates = points.map((point) => point.date)
    const coverage = assessRiskWindowCoverage(dates, asOfDate, lookbackDays, frequency, parameters)
    if (!coverage.ok) {
      return {
        value: null,
        observationCount: values.length,
      }
    }
    return {
      value: annualizedVarianceFromValues(values, dates, modelId, parameters),
      observationCount: values.length,
    }
  }

  const pairs = pairWindowReturns(left.returnsByDate, right.returnsByDate, asOfDate, lookbackDays)
  if (pairs.length < 2) {
    return { value: null, observationCount: pairs.length }
  }
  const leftWindowPoints = windowReturnPoints(left, asOfDate, lookbackDays)
  const rightWindowPoints = windowReturnPoints(right, asOfDate, lookbackDays)
  if (
    leftWindowPoints.length !== pairs.length ||
    rightWindowPoints.length !== pairs.length ||
    leftWindowPoints.some((point, index) => point.date !== pairs[index]?.date) ||
    rightWindowPoints.some((point, index) => point.date !== pairs[index]?.date)
  ) {
    return { value: null, observationCount: pairs.length }
  }
  const leftValues = pairs.map((pair) => pair.left)
  const rightValues = pairs.map((pair) => pair.right)
  const dates = pairs.map((pair) => pair.date)
  const coverage = assessRiskWindowCoverage(dates, asOfDate, lookbackDays, frequency, parameters)
  if (!coverage.ok) {
    return { value: null, observationCount: pairs.length }
  }
  if (modelId === 'ewma_vol_shrinkage_corr_covariance') {
    const leftCoverage = assessRiskWindowCoverage(
      leftWindowPoints.map((point) => point.date),
      asOfDate,
      lookbackDays,
      frequency,
      parameters,
    )
    const rightCoverage = assessRiskWindowCoverage(
      rightWindowPoints.map((point) => point.date),
      asOfDate,
      lookbackDays,
      frequency,
      parameters,
    )
    if (!leftCoverage.ok || !rightCoverage.ok) {
      return { value: null, observationCount: pairs.length }
    }
    const leftVariance = annualizedVarianceFromValues(
      leftWindowPoints.map((point) => point.value),
      leftWindowPoints.map((point) => point.date),
      modelId,
      parameters,
    )
    const rightVariance = annualizedVarianceFromValues(
      rightWindowPoints.map((point) => point.value),
      rightWindowPoints.map((point) => point.date),
      modelId,
      parameters,
    )
    const correlation = estimateCorrelationFromValues(leftValues, rightValues, modelId, parameters)
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
    value: annualizedCovarianceFromValues(leftValues, rightValues, dates, modelId, parameters),
    observationCount: pairs.length,
  }
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

function returnWindowCoverage(
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
  )
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
  lookbackDays: number,
  frequency: CalculationFrequency,
) {
  if (!asOfDate) {
    return { groups: [], cells: [], maxAbs: 0 } satisfies CorrelationMatrix
  }
  const activeSeries = series
    .map((item) => ({
      item,
      coverage: returnWindowCoverage(item, asOfDate, lookbackDays, frequency),
      weight: weightAtOrBefore(item, asOfDate),
    }))
    .filter((item) => item.coverage.ok)
    .sort((left, right) => {
      const weightDelta = Math.abs(right.weight ?? 0) - Math.abs(left.weight ?? 0)
      return weightDelta || left.item.groupLabel.localeCompare(right.item.groupLabel)
    })
  const sampleDates = commonReturnDateKeys(
    activeSeries.map(({ item }) => item),
    riskWindowStart(asOfDate, lookbackDays),
    asOfDate,
    { includeZeroWeight: true },
  )
  const sampleCoverage = assessRiskWindowCoverage(sampleDates, asOfDate, lookbackDays, frequency)
  if (!sampleCoverage.ok) {
    return { groups: [], cells: [], maxAbs: 0 } satisfies CorrelationMatrix
  }

  let maxAbs = 0
  const cells = activeSeries.map((rowSeries) =>
    activeSeries.map((columnSeries) => {
      const cell =
        rowSeries.item.groupKey === columnSeries.item.groupKey
          ? { value: 1, observationCount: sampleCoverage.observationCount }
          : correlationCellFromDates(rowSeries.item, columnSeries.item, sampleDates)
      if (cell.value != null) {
        maxAbs = Math.max(maxAbs, Math.abs(cell.value))
      }
      return cell
    }),
  )

  return {
    groups: activeSeries.map(({ item, coverage, weight }) => ({
      key: item.groupKey,
      label: item.groupLabel,
      observationCount: sampleCoverage.observationCount || coverage.observationCount,
      weight,
    })),
    cells,
    maxAbs,
  } satisfies CorrelationMatrix
}

function holdingRiskLabel(row: HoldingsWorkspaceResponse['rows'][number]) {
  return row.instrument_core.instrument_name || row.instrument_core.instrument_id || row.line_id
}

function isCashHoldingRow(row: HoldingsWorkspaceResponse['rows'][number]) {
  return row.instrument_core.instrument_type.trim().toLowerCase() === 'cash'
}

function isCashUniverseInstrument(record: PortfolioTaxonomyCatalogResponse['instrument_universe'][number]) {
  const instrument = record.instrument_ref
  return (
    record.instrument_id.trim().toLowerCase().startsWith('cash:') ||
    (instrument?.instrument_type ?? '').trim().toLowerCase() === 'cash'
  )
}

function returnPointsToGroupSeries({
  groupKey,
  groupLabel,
  returnPoints,
  asOfDate,
  latestWeight,
}: {
  groupKey: string
  groupLabel: string
  returnPoints: ReturnPoint[]
  asOfDate: string
  latestWeight: number
}): GroupReturnSeries | null {
  const returnsByDate = new Map<string, number>()
  returnPoints.forEach((point) => {
    const value = finiteNumber(point.value)
    if (point.date && point.date <= asOfDate && value != null) {
      returnsByDate.set(point.date, value)
    }
  })
  if (!returnsByDate.size) {
    return null
  }
  const endingWeightByDate = new Map<string, number>()
  returnsByDate.forEach((_value, dateKey) => {
    endingWeightByDate.set(dateKey, latestWeight)
  })
  endingWeightByDate.set(asOfDate, latestWeight)
  return {
    groupKey,
    groupLabel,
    returnsByDate,
    endingWeightByDate,
    latestWeight,
    observationCount: returnsByDate.size,
  } satisfies GroupReturnSeries
}

function latestReturnPointDate(returnPoints: ReturnPoint[] | undefined | null) {
  let latestDate = ''
  ;(returnPoints ?? []).forEach((point) => {
    const value = finiteNumber(point.value)
    if (point.date && value != null && point.date > latestDate) {
      latestDate = point.date
    }
  })
  return latestDate || null
}

function buildCurrentInstrumentReturnSeries(holdingsWorkspace: HoldingsWorkspaceResponse | null) {
  if (!holdingsWorkspace) {
    return riskFail('Current risk requires the holdings workspace.', [] satisfies GroupReturnSeries[])
  }
  const asOfDate = holdingsWorkspace.as_of_date
  if (!asOfDate) {
    return riskFail('Current risk requires a holdings as-of date.', [] satisfies GroupReturnSeries[])
  }

  const errors: string[] = []
  const series = holdingsWorkspace.rows
    .filter((row) => !isCashHoldingRow(row))
    .map((row): GroupReturnSeries | null => {
      const currentWeight = finiteNumber(row.allocation)
      const currentValueBase = finiteNumber(row.market_value_base)
      const hasExposure = Math.abs(currentWeight ?? 0) > 1e-9 || Math.abs(currentValueBase ?? 0) > 1e-9
      if (!hasExposure) {
        return null
      }
      const label = holdingRiskLabel(row)
      if (currentWeight == null) {
        errors.push(`Current risk requires a current portfolio weight for ${label}.`)
        return null
      }
      const series = returnPointsToGroupSeries({
        groupKey: row.instrument_core.instrument_id,
        groupLabel: label,
        returnPoints: row.instrument_return_series_all?.points ?? [],
        asOfDate,
        latestWeight: currentWeight,
      })
      if (!series) {
        errors.push(`Current risk requires full-history return series for ${label}.`)
        return null
      }
      return series
    })
    .filter((item): item is GroupReturnSeries => item !== null)
    .sort((left, right) => {
      const weightDelta = Math.abs(right.latestWeight ?? 0) - Math.abs(left.latestWeight ?? 0)
      return weightDelta || left.groupLabel.localeCompare(right.groupLabel)
    })

  return errors.length ? riskFail(errors, [] satisfies GroupReturnSeries[]) : riskOk(series)
}

function buildMatrixInstrumentReturnSeries({
  holdingsWorkspace,
  catalog,
}: {
  holdingsWorkspace: HoldingsWorkspaceResponse | null
  catalog: PortfolioTaxonomyCatalogResponse | null
}) {
  if (!holdingsWorkspace || !catalog) {
    return [] satisfies GroupReturnSeries[]
  }
  const fallbackAsOfDate = holdingsWorkspace.as_of_date
  if (!fallbackAsOfDate) {
    return [] satisfies GroupReturnSeries[]
  }

  const universeRecords = catalog.instrument_universe.filter(
    (record) => record.status === 'active' && record.instrument_ref && !isCashUniverseInstrument(record),
  )
  const matrixAsOfDate =
    universeRecords.reduce<string | null>((latestDate, record) => {
      const returnDate = latestReturnPointDate(record.instrument_return_series_all?.points)
      if (!returnDate) {
        return latestDate
      }
      return !latestDate || returnDate > latestDate ? returnDate : latestDate
    }, null) ?? fallbackAsOfDate

  const currentWeightByInstrumentId = new Map<string, number>()
  holdingsWorkspace.rows
    .filter((row) => !isCashHoldingRow(row))
    .forEach((row) => {
      const currentWeight = finiteNumber(row.allocation)
      if (currentWeight != null) {
        currentWeightByInstrumentId.set(row.instrument_core.instrument_id, currentWeight)
      }
    })

  return universeRecords
    .map((record): GroupReturnSeries | null => {
      const instrument = record.instrument_ref
      if (!instrument) {
        return null
      }
      const label = instrument.instrument_name || instrument.instrument_id
      const currentWeight = currentWeightByInstrumentId.get(record.instrument_id) ?? 0
      const series = returnPointsToGroupSeries({
        groupKey: record.instrument_id,
        groupLabel: label,
        returnPoints: record.instrument_return_series_all?.points ?? [],
        asOfDate: matrixAsOfDate,
        latestWeight: currentWeight,
      })
      return series
    })
    .filter((item): item is GroupReturnSeries => item !== null)
    .sort((left, right) => {
      const weightDelta = Math.abs(right.latestWeight ?? 0) - Math.abs(left.latestWeight ?? 0)
      return weightDelta || left.groupLabel.localeCompare(right.groupLabel)
    })
}

function buildCurrentTaxonomyReturnSeries({
  instrumentSeries,
  catalog,
  taxonomy,
  scopeNodeId = '',
  referenceDate,
}: {
  instrumentSeries: GroupReturnSeries[]
  catalog: PortfolioTaxonomyCatalogResponse | null
  taxonomy: PortfolioTaxonomyRecord | null
  scopeNodeId?: string
  referenceDate: string | null
}) {
  if (!catalog || !taxonomy || !referenceDate) {
    return riskOk([] satisfies GroupReturnSeries[])
  }
  if (taxonomy.primary_assignment_scope !== 'instrument') {
    return riskOk([] satisfies GroupReturnSeries[])
  }

  const nodeById = buildNodeLookup(catalog, taxonomy.taxonomy_id)
  const errors: string[] = []
  const membersByGroup = new Map<
    string,
    {
      label: string
      members: GroupReturnSeries[]
    }
  >()

  instrumentSeries.forEach((item) => {
    if (Math.abs(item.latestWeight ?? 0) <= 1e-9) {
      return
    }
    const assignmentResult = resolveActiveAssignment(
      catalog,
      taxonomy.taxonomy_id,
      'instrument',
      item.groupKey,
      referenceDate,
    )
    if (assignmentResult.error) {
      errors.push(assignmentResult.error)
      return
    }
    const scopedNode = resolveScopedTaxonomyNode(
      assignmentResult.assignment?.taxonomy_node_id,
      nodeById,
      scopeNodeId,
    )
    if (!scopedNode && scopeNodeId) {
      return
    }
    const groupKey = scopedNode?.taxonomy_node_id ?? `unassigned:${taxonomy.taxonomy_id}`
    const label = scopedNode?.node_name ?? 'Unassigned'
    const current = membersByGroup.get(groupKey) ?? { label, members: [] }
    current.members.push(item)
    membersByGroup.set(groupKey, current)
  })

  if (errors.length) {
    return riskFail(errors, [] satisfies GroupReturnSeries[])
  }

  const taxonomySeries = [...membersByGroup.entries()]
    .map(([groupKey, group]): GroupReturnSeries | null => {
      const members = group.members.filter((item) => Math.abs(item.latestWeight ?? 0) > 1e-9)
      const groupWeight = members.reduce((total, item) => total + (item.latestWeight ?? 0), 0)
      if (!members.length || Math.abs(groupWeight) <= 1e-9) {
        return null
      }
      const commonDates = commonReturnDateKeys(members)
      const returnsByDate = new Map<string, number>()
      commonDates.forEach((dateKey) => {
        const value = members.reduce(
          (total, item) => total + ((item.latestWeight ?? 0) / groupWeight) * (item.returnsByDate.get(dateKey) ?? 0),
          0,
        )
        returnsByDate.set(dateKey, value)
      })
      const endingWeightByDate = new Map<string, number>()
      returnsByDate.forEach((_value, dateKey) => {
        endingWeightByDate.set(dateKey, groupWeight)
      })
      if (referenceDate) {
        endingWeightByDate.set(referenceDate, groupWeight)
      }
      return {
        groupKey,
        groupLabel: group.label,
        returnsByDate,
        endingWeightByDate,
        latestWeight: groupWeight,
        observationCount: returnsByDate.size,
      } satisfies GroupReturnSeries
    })
    .filter((item): item is GroupReturnSeries => item !== null && item.observationCount > 0)
    .sort((left, right) => {
      const weightDelta = Math.abs(right.latestWeight ?? 0) - Math.abs(left.latestWeight ?? 0)
      return weightDelta || left.groupLabel.localeCompare(right.groupLabel)
    })

  return riskOk(taxonomySeries)
}

function buildRiskContributionRows(
  series: GroupReturnSeries[],
  asOfDate: string,
  settings: RiskSettingsState,
  frequency: CalculationFrequency,
) {
  if (!asOfDate) {
    return riskFail('Risk contribution requires an as-of date.', [] satisfies RiskContributionRow[])
  }
  const parameters = riskModelParameters(settings, frequency)
  const weightedSeries = series
    .map((item) => ({
      item,
      weight: weightAtOrBefore(item, asOfDate),
      coverage: returnWindowCoverage(item, asOfDate, settings.lookbackDays, frequency, parameters),
    }))
    .filter((item) => Math.abs(item.weight ?? 0) > 1e-9)
  const insufficientSeries = weightedSeries.filter((item) => !item.coverage.ok)
  if (insufficientSeries.length) {
    return riskFail(
      `Risk contribution requires a complete ${windowLabel(settings.lookbackDays)} ${frequency} return window for every active weighted group; insufficient: ${insufficientSeries
        .map(({ item, coverage }) => `${item.groupLabel} (${coverage.error || `${coverage.observationCount} observations`})`)
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

  const weights = activeSeries.map((item) => item.weight ?? 0)
  const firstActiveSeries = activeSeries[0]
  if (!firstActiveSeries) {
    return riskFail('Risk contribution requires at least one active weighted group.', [] satisfies RiskContributionRow[])
  }
  const startDate = riskWindowStart(asOfDate, settings.lookbackDays)
  const windowDateSets = activeSeries.map(({ item }) => ({
    item,
    dates: windowReturnPoints(item, asOfDate, settings.lookbackDays).map((point) => point.date),
  }))
  const unionDates = [...new Set(windowDateSets.flatMap((entry) => entry.dates))].sort()
  const incompleteDateSets = windowDateSets
    .map((entry) => {
      const dateSet = new Set(entry.dates)
      const missingDates = unionDates.filter((dateKey) => !dateSet.has(dateKey))
      return { ...entry, missingDates }
    })
    .filter((entry) => entry.missingDates.length > 0)
  if (incompleteDateSets.length) {
    return riskFail(
      `Risk contribution requires identical complete ${frequency} return dates for every active weighted group; missing aligned dates: ${incompleteDateSets
        .map(({ item, missingDates }) => `${item.groupLabel} (${missingDates.slice(0, 3).join(', ')}${missingDates.length > 3 ? ', ...' : ''})`)
        .join('; ')}.`,
      [] satisfies RiskContributionRow[],
    )
  }
  const commonDates = unionDates.filter((dateKey) => dateKey >= startDate && dateKey <= asOfDate)
  if (commonDates.length < 2) {
    return riskFail(
      `Risk contribution requires at least two common return dates across all active weighted groups; got ${commonDates.length}.`,
      [] satisfies RiskContributionRow[],
    )
  }
  const commonCoverage = assessRiskWindowCoverage(commonDates, asOfDate, settings.lookbackDays, frequency, parameters)
  if (!commonCoverage.ok) {
    return riskFail(
      `Risk contribution requires a complete common ${windowLabel(settings.lookbackDays)} ${frequency} return window; ${commonCoverage.error}`,
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
      const covarianceValue = annualizedCovarianceFromValues(
        leftValues,
        rightValues,
        commonDates,
        settings.modelId,
        parameters,
      )
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
        assignment.status === 'active',
    )
    .sort((left, right) => right.assignment_id.localeCompare(left.assignment_id))

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
    const nonCashHoldingRows = holdingsWorkspace.rows.filter((row) => !isCashHoldingRow(row))
    const missingHoldingValueRows = nonCashHoldingRows.filter((row) => finiteNumber(row.market_value_base) == null)
    if (missingHoldingValueRows.length) {
      errors.push(
        `Current drift requires market_value_base for every holding; missing: ${missingHoldingValueRows
          .map((row) => row.instrument_core.instrument_name || row.instrument_core.instrument_id)
          .join(', ')}.`,
      )
    }
    const totalValueBase =
      nonCashHoldingRows.reduce((total, row) => total + (finiteNumber(row.market_value_base) ?? 0), 0) +
      cashAccounts.reduce((total, accountRow) => total + (accountValueBase(accountRow) ?? 0), 0)
    if (totalValueBase <= 1e-9 && (nonCashHoldingRows.length || cashAccounts.length)) {
      errors.push('Current drift requires positive portfolio NAV from holdings plus cash account values.')
    }

    nonCashHoldingRows.forEach((row) => {
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
    return riskOk([] satisfies TargetGapComparatorRow[])
  }
  if (dimension === 'weight' && !targetSet.weight_enabled) {
    return riskOk([] satisfies TargetGapComparatorRow[])
  }
  if (dimension === 'risk_budget' && !targetSet.risk_budget_enabled) {
    return riskOk([] satisfies TargetGapComparatorRow[])
  }
  if (!targetLines.length) {
    return riskFail(`${targetSet.name} is active but has no target lines.`, [] satisfies TargetGapComparatorRow[])
  }
  const nonNodeLines = targetLines.filter((line) => line.target_member_type !== 'taxonomy_node')
  if (nonNodeLines.length) {
    return riskFail(
      `${targetSet.name} root target drift must be defined on taxonomy_node budgeting members; unsupported direct members: ${nonNodeLines
        .map((line) => `${line.target_member_type}:${line.target_member_id}`)
        .join(', ')}.`,
      [] satisfies TargetGapComparatorRow[],
    )
  }
  const missingTargetNodes = targetLines.filter((line) => !nodeById.has(line.target_member_id))
  if (missingTargetNodes.length) {
    return riskFail(
      `${targetSet.name} references missing taxonomy nodes: ${missingTargetNodes.map((line) => line.target_member_id).join(', ')}.`,
      [] satisfies TargetGapComparatorRow[],
    )
  }
  if (dimension === 'risk_budget' && riskShareErrors.length) {
    return riskFail(INSUFFICIENT_DATA_MESSAGE, [] satisfies TargetGapComparatorRow[])
  }
  const targetLineErrors = targetLines
    .filter((line) => (dimension === 'weight' ? line.target_weight : line.target_risk_share) == null)
    .map((line) => `${targetSet.name} is missing ${dimension === 'weight' ? 'target_weight' : 'target_risk_share'} for ${lineDisplayLabel(line, nodeById)}.`)
  if (targetLineErrors.length) {
    return riskFail(targetLineErrors, [] satisfies TargetGapComparatorRow[])
  }
  const targetTotal = targetLines.reduce(
    (total, line) => total + ((dimension === 'weight' ? line.target_weight : line.target_risk_share) ?? 0),
    0,
  )
  if (Math.abs(targetTotal - 1) > 1e-6) {
    return riskFail(
      `${targetSet.name} ${dimension === 'weight' ? 'target weights' : 'risk targets'} must sum to 100%; got ${formatPercent(targetTotal)}.`,
      [] satisfies TargetGapComparatorRow[],
    )
  }

  const normalizedBaseCurrency = baseCurrency.trim().toUpperCase()
  if (!normalizedBaseCurrency && currentGroups.some((group) => group.currentValueBase != null)) {
    return riskFail(
      `${targetSet.name} target drift requires the portfolio base currency before value details can be rendered.`,
      [] satisfies TargetGapComparatorRow[],
    )
  }

  const currentGroupByKey = new Map(currentGroups.map((group) => [group.groupKey, group] as const))
  const lineByKey = new Map(targetLines.map((line) => [lineDisplayKey(line), line] as const))
  const allKeys = new Set([...currentGroupByKey.keys(), ...lineByKey.keys()])
  const rows: TargetGapComparatorRow[] = []
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
      key,
      id: `${targetSet.target_set_id}:${dimension}:${key}`,
      label: currentGroup?.label ?? (line ? lineDisplayLabel(line, nodeById) : key),
      current,
      target,
      gap: current != null && target != null ? current - target : null,
      detail: currentGroup?.currentValueBase != null ? formatCurrency(currentGroup.currentValueBase, normalizedBaseCurrency) : undefined,
    })
  })

  if (errors.length) {
    return riskFail(errors, [] satisfies TargetGapComparatorRow[])
  }

  return riskOk(rows.sort((left, right) => Math.abs(right.gap ?? 0) - Math.abs(left.gap ?? 0)))
}

function combineTargetGapRows(
  saaRows: TargetGapComparatorRow[],
  taaRows: TargetGapComparatorRow[],
): RiskTargetGapChartRow[] {
  const saaByKey = new Map(saaRows.map((row) => [row.key, row] as const))
  const taaByKey = new Map(taaRows.map((row) => [row.key, row] as const))
  const keys = new Set([...saaByKey.keys(), ...taaByKey.keys()])
  return [...keys]
    .map((key) => {
      const saa = saaByKey.get(key) ?? null
      const taa = taaByKey.get(key) ?? null
      const current = saa?.current ?? taa?.current ?? null
      const saaTarget = saa?.target ?? null
      const taaTarget = taa?.target ?? null
      return {
        id: `target-gap:${key}`,
        label: saa?.label ?? taa?.label ?? key,
        current,
        saaTarget,
        taaTarget,
        saaGap: current != null && saaTarget != null ? current - saaTarget : null,
        taaGap: current != null && taaTarget != null ? current - taaTarget : null,
        detail: saa?.detail ?? taa?.detail,
      } satisfies RiskTargetGapChartRow
    })
    .sort((left, right) => {
      const leftGap = Math.max(Math.abs(left.saaGap ?? 0), Math.abs(left.taaGap ?? 0))
      const rightGap = Math.max(Math.abs(right.saaGap ?? 0), Math.abs(right.taaGap ?? 0))
      return rightGap - leftGap || left.label.localeCompare(right.label)
    })
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

function calculableCorrelationAsOfDates(
  series: GroupReturnSeries[],
  lookbackDays: number,
  frequency: CalculationFrequency,
) {
  const commonDates = commonReturnDateKeys(series, '', '', { includeZeroWeight: true })
  return commonDates.filter((asOfDate) => {
    const windowDates = commonReturnDateKeys(
      series,
      riskWindowStart(asOfDate, lookbackDays),
      asOfDate,
      { includeZeroWeight: true },
    )
    return assessRiskWindowCoverage(windowDates, asOfDate, lookbackDays, frequency).ok
  })
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

function RiskSettingsMenu<TSettings extends RiskWindowSettingsState>({
  label,
  settings,
  onChange,
  includeWindow = true,
  includeChartStyle = false,
}: {
  label: string
  settings: TSettings
  onChange: (settings: TSettings) => void
  includeWindow?: boolean
  includeChartStyle?: boolean
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
            {includeWindow ? (
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
  const selectedPosition = dates.length > 1 ? (selectedIndex / (dates.length - 1)) * 100 : 0
  return (
    <div
      className="risk-date-scrubber"
      style={{ '--risk-date-scrubber-position': `${selectedPosition}%` } as CSSProperties}
    >
      <div className="risk-date-scrubber-summary">
        <span>{label}</span>
        <strong>{selectedDate}</strong>
      </div>
      <div className="risk-date-scrubber-control">
        <div className="risk-date-scrubber-current" aria-hidden="true">
          {selectedDate}
        </div>
        <input
          type="range"
          min={0}
          max={Math.max(0, dates.length - 1)}
          value={selectedIndex}
          onChange={(event) => onChange(dates[Number(event.target.value)] ?? value)}
          aria-label={label}
        />
        <div className="risk-date-scrubber-endpoints">
          <span>{dates[0]}</span>
          <span>{dates[dates.length - 1]}</span>
        </div>
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
  const [workspaceSupportError, setWorkspaceSupportError] = useState<string | null>(null)
  const [riskPolicyRevision, setRiskPolicyRevision] = useState(0)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkInstrumentId, setBenchmarkInstrumentId] = useState('')
  const [benchmarkChart, setBenchmarkChart] = useState<PortfolioInstrumentPriceChartResponse | null>(null)
  const [benchmarkLoading, setBenchmarkLoading] = useState(false)
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null)
  const [rollingSettings, setRollingSettings] = useState<RollingRiskSettingsState>(() => loadRiskPageSettings().rolling)
  const [matrixSettings, setMatrixSettings] = useState<RiskWindowSettingsState>(() => loadRiskPageSettings().matrix)
  const [matrixScopeNodeId, setMatrixScopeNodeId] = useState(MATRIX_SCOPE_ALL_INSTRUMENTS)
  const [matrixAsOfDate, setMatrixAsOfDate] = useState('')

  const riskWindowEndDate = holdingsWorkspace?.as_of_date ?? ''

  useEffect(() => {
    saveRiskPageSettings({ rolling: rollingSettings, matrix: matrixSettings })
  }, [matrixSettings, rollingSettings])

  useEffect(() => {
    function handleRiskPolicyUpdated(event: Event) {
      const detail = (event as CustomEvent<{ portfolioId?: string }>).detail
      if (detail?.portfolioId === portfolioId) {
        setRiskPolicyRevision((current) => current + 1)
      }
    }

    window.addEventListener('portfolio-risk-policy-updated', handleRiskPolicyUpdated)
    return () => window.removeEventListener('portfolio-risk-policy-updated', handleRiskPolicyUpdated)
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId) {
      setHoldingsWorkspace(null)
      setAccountsWorkspace(null)
      setTaxonomyCatalog(null)
      setWorkspaceLoading(false)
      setWorkspaceError('Portfolio id is required.')
      setWorkspaceSupportError(null)
      return
    }

    let cancelled = false
    setWorkspaceLoading(true)
    setWorkspaceError(null)
    setWorkspaceSupportError(null)
    setHoldingsWorkspace(null)
    setAccountsWorkspace(null)
    setTaxonomyCatalog(null)

    getHoldingsWorkspace(portfolioId)
      .then((holdingsResponse) => {
        if (cancelled) {
          return
        }
        setHoldingsWorkspace(holdingsResponse)
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

    Promise.allSettled([
      getPortfolioAccountsWorkspace(portfolioId),
      getPortfolioTaxonomyCatalog(portfolioId, {
        include_market_profile: true,
        as_of_date: localDateIso(),
      }),
    ])
      .then(([accountsResult, taxonomyResult]) => {
        if (cancelled) {
          return
        }
        const supportErrors: string[] = []
        if (accountsResult.status === 'fulfilled') {
          setAccountsWorkspace(accountsResult.value)
        } else {
          setAccountsWorkspace(null)
          supportErrors.push(
            accountsResult.reason instanceof Error
              ? accountsResult.reason.message
              : 'Failed to load accounts workspace.',
          )
        }
        if (taxonomyResult.status === 'fulfilled') {
          setTaxonomyCatalog(taxonomyResult.value)
        } else {
          setTaxonomyCatalog(null)
          supportErrors.push(
            taxonomyResult.reason instanceof Error
              ? taxonomyResult.reason.message
              : 'Failed to load taxonomy catalog.',
          )
        }
        setWorkspaceSupportError(supportErrors.length ? supportErrors.join(' ') : null)
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId, riskPolicyRevision])

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
          targetSet.status === 'active',
      ),
    [defaultPlanningTaxonomy?.taxonomy_id, taxonomyCatalog?.target_sets],
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
  const portfolioRiskFrequencyResult = useMemo(
    () => riskFrequencyProfileFromHoldingsWorkspace(holdingsWorkspace),
    [holdingsWorkspace],
  )
  const portfolioRiskFrequency = portfolioRiskFrequencyResult.value
  const portfolioRiskFrequencyErrors = portfolioRiskFrequencyResult.errors
  const productionRiskPolicy = holdingsWorkspace?.risk_policy ?? null
  const productionRiskPolicyParametersKey = JSON.stringify(productionRiskPolicy?.parameters ?? {})
  const productionRiskSettings = useMemo(
    () => riskSettingsFromPolicy(productionRiskPolicy),
    [
      productionRiskPolicy?.contribution_mode,
      productionRiskPolicy?.covariance_model_id,
      productionRiskPolicy?.lookback_days,
      productionRiskPolicyParametersKey,
    ],
  )
  const driftSettings = productionRiskSettings
  const productionRiskDescription = `${windowLabel(productionRiskSettings.lookbackDays)} ${riskModelLabel(
    productionRiskSettings.modelId,
  )}; ${formatLabel(productionRiskSettings.contributionMode)} RC`
  const productionRiskMeta = `Production Risk Model; ${productionRiskDescription}`
  const riskBasisFinalDate = holdingsWorkspace?.as_of_date ?? riskWindowEndDate
  const rawInstrumentReturnSeriesResult = useMemo(
    () => buildCurrentInstrumentReturnSeries(holdingsWorkspace),
    [holdingsWorkspace],
  )
  const rawInstrumentReturnSeries = rawInstrumentReturnSeriesResult.value
  const currentRiskInputErrors = useMemo(
    () => [...portfolioRiskFrequencyErrors, ...rawInstrumentReturnSeriesResult.errors],
    [portfolioRiskFrequencyErrors, rawInstrumentReturnSeriesResult.errors],
  )
  const riskInputsReady = currentRiskInputErrors.length === 0
  const instrumentReturnSeries = useMemo(
    () =>
      riskInputsReady
        ? alignReturnSeriesToFrequency(
            rawInstrumentReturnSeries,
            portfolioRiskFrequency.frequency,
            riskBasisFinalDate,
          )
        : [],
    [portfolioRiskFrequency.frequency, rawInstrumentReturnSeries, riskBasisFinalDate, riskInputsReady],
  )
  const rawMatrixInstrumentReturnSeries = useMemo(
    () => buildMatrixInstrumentReturnSeries({ holdingsWorkspace, catalog: taxonomyCatalog }),
    [holdingsWorkspace, taxonomyCatalog],
  )
  const matrixRiskFrequency = useMemo(() => {
    const frequency = taxonomyCatalog?.risk_basis?.resolved_frequency
    if (isCalculationFrequency(frequency)) {
      return {
        frequency,
        statusLabel:
          taxonomyCatalog?.risk_basis?.status_label ||
          `${CALCULATION_FREQUENCY_LABELS[frequency]} risk basis`,
      } satisfies RiskFrequencyProfile
    }
    return portfolioRiskFrequency
  }, [
    portfolioRiskFrequency,
    taxonomyCatalog?.risk_basis?.resolved_frequency,
    taxonomyCatalog?.risk_basis?.status_label,
  ])
  const matrixInstrumentReturnSeries = useMemo(
    () => alignReturnSeriesToFrequency(rawMatrixInstrumentReturnSeries, matrixRiskFrequency.frequency, riskBasisFinalDate),
    [matrixRiskFrequency.frequency, rawMatrixInstrumentReturnSeries, riskBasisFinalDate],
  )
  const portfolioReturnPoints = useMemo(
    () => buildCurrentWeightedPortfolioReturnPoints(instrumentReturnSeries),
    [instrumentReturnSeries],
  )
  const benchmarkReturnPointsRaw = useMemo(() => buildBenchmarkReturnPoints(benchmarkChart), [benchmarkChart])
  const benchmarkReturnPoints = useMemo(
    () =>
      riskInputsReady
        ? alignReturnPointsToFrequency(
            benchmarkReturnPointsRaw,
            portfolioRiskFrequency.frequency,
            riskBasisFinalDate,
          )
        : [],
    [benchmarkReturnPointsRaw, portfolioRiskFrequency.frequency, riskBasisFinalDate, riskInputsReady],
  )
  const rollingVolatilityPoints = useMemo(
    () =>
      buildRollingMetricPoints(
        portfolioReturnPoints,
        rollingSettings,
        'volatility',
        portfolioRiskFrequency.frequency,
      ),
    [portfolioReturnPoints, portfolioRiskFrequency.frequency, rollingSettings],
  )
  const benchmarkRollingVolatilityPoints = useMemo(
    () =>
      buildRollingMetricPoints(
        benchmarkReturnPoints,
        rollingSettings,
        'volatility',
        portfolioRiskFrequency.frequency,
      ),
    [benchmarkReturnPoints, portfolioRiskFrequency.frequency, rollingSettings],
  )
  const rollingSharpePoints = useMemo(
    () =>
      buildRollingMetricPoints(
        portfolioReturnPoints,
        rollingSettings,
        'sharpe',
        portfolioRiskFrequency.frequency,
      ),
    [portfolioReturnPoints, portfolioRiskFrequency.frequency, rollingSettings],
  )
  const benchmarkRollingSharpePoints = useMemo(
    () =>
      buildRollingMetricPoints(
        benchmarkReturnPoints,
        rollingSettings,
        'sharpe',
        portfolioRiskFrequency.frequency,
      ),
    [benchmarkReturnPoints, portfolioRiskFrequency.frequency, rollingSettings],
  )

  const matrixTaxonomyScopeOptions = useMemo(
    () => taxonomyScopeOptions(defaultPlanningTaxonomy, taxonomyCatalog),
    [defaultPlanningTaxonomy, taxonomyCatalog],
  )
  const matrixScopeOptions = useMemo(
    () => [
      { value: MATRIX_SCOPE_ALL_INSTRUMENTS, label: 'All Instruments', kind: 'instrument' as const },
      ...matrixTaxonomyScopeOptions.map((option) => ({
        ...option,
        kind: 'taxonomy' as const,
      })),
    ],
    [matrixTaxonomyScopeOptions],
  )
  const matrixUsesAllInstruments = matrixScopeNodeId === MATRIX_SCOPE_ALL_INSTRUMENTS
  const matrixTaxonomyScopeNodeId = matrixUsesAllInstruments ? '' : matrixScopeNodeId
  useEffect(() => {
    if (!matrixScopeOptions.some((option) => option.value === matrixScopeNodeId)) {
      setMatrixScopeNodeId(MATRIX_SCOPE_ALL_INSTRUMENTS)
    }
  }, [matrixScopeNodeId, matrixScopeOptions])
  const matrixTaxonomySeriesResult = useMemo(
    () =>
      matrixUsesAllInstruments
        ? riskOk([] satisfies GroupReturnSeries[])
        : buildCurrentTaxonomyReturnSeries({
            instrumentSeries: matrixInstrumentReturnSeries.length ? matrixInstrumentReturnSeries : instrumentReturnSeries,
            catalog: taxonomyCatalog,
            taxonomy: defaultPlanningTaxonomy,
            scopeNodeId: matrixTaxonomyScopeNodeId,
            referenceDate: holdingsWorkspace?.as_of_date ?? null,
          }),
    [
      defaultPlanningTaxonomy,
      holdingsWorkspace?.as_of_date,
      instrumentReturnSeries,
      matrixInstrumentReturnSeries,
      matrixTaxonomyScopeNodeId,
      matrixUsesAllInstruments,
      taxonomyCatalog,
    ],
  )
  const matrixTaxonomySeries = matrixTaxonomySeriesResult.value
  const alignedMatrixTaxonomySeries = useMemo(
    () =>
      !matrixTaxonomySeriesResult.errors.length
        ? alignReturnSeriesToFrequency(
            matrixTaxonomySeries,
            matrixRiskFrequency.frequency,
            riskBasisFinalDate,
          )
        : [],
    [
      matrixTaxonomySeries,
      matrixTaxonomySeriesResult.errors.length,
      matrixRiskFrequency.frequency,
      riskBasisFinalDate,
    ],
  )
  const topLevelTaxonomySeriesResult = useMemo(
    () =>
      buildCurrentTaxonomyReturnSeries({
        instrumentSeries: instrumentReturnSeries,
        catalog: taxonomyCatalog,
        taxonomy: defaultPlanningTaxonomy,
        referenceDate: holdingsWorkspace?.as_of_date ?? null,
      }),
    [defaultPlanningTaxonomy, holdingsWorkspace?.as_of_date, instrumentReturnSeries, taxonomyCatalog],
  )
  const topLevelTaxonomySeries = topLevelTaxonomySeriesResult.value
  const alignedTopLevelTaxonomySeries = useMemo(
    () =>
      riskInputsReady && !topLevelTaxonomySeriesResult.errors.length
        ? alignReturnSeriesToFrequency(
            topLevelTaxonomySeries,
            portfolioRiskFrequency.frequency,
            riskBasisFinalDate,
          )
        : [],
    [
      portfolioRiskFrequency.frequency,
      riskBasisFinalDate,
      riskInputsReady,
      topLevelTaxonomySeries,
      topLevelTaxonomySeriesResult.errors.length,
    ],
  )
  const selectedMatrixSeries = matrixUsesAllInstruments ? matrixInstrumentReturnSeries : alignedMatrixTaxonomySeries
  const riskAsOfSelectionDates = useMemo(
    () =>
      calculableCorrelationAsOfDates(
        selectedMatrixSeries,
        matrixSettings.lookbackDays,
        matrixRiskFrequency.frequency,
      ),
    [matrixRiskFrequency.frequency, matrixSettings.lookbackDays, selectedMatrixSeries],
  )
  const effectiveMatrixAsOfDate = matrixAsOfDate || riskAsOfSelectionDates[riskAsOfSelectionDates.length - 1] || ''

  useEffect(() => {
    if (!riskAsOfSelectionDates.length) {
      if (matrixAsOfDate) {
        setMatrixAsOfDate('')
      }
      return
    }
    if (!riskAsOfSelectionDates.includes(matrixAsOfDate)) {
      setMatrixAsOfDate(riskAsOfSelectionDates[riskAsOfSelectionDates.length - 1])
    }
  }, [matrixAsOfDate, riskAsOfSelectionDates])

  const selectedCorrelationMatrix = useMemo(
    () =>
      buildCorrelationMatrix(
        selectedMatrixSeries,
        effectiveMatrixAsOfDate,
        matrixSettings.lookbackDays,
        matrixRiskFrequency.frequency,
      ),
    [effectiveMatrixAsOfDate, matrixRiskFrequency.frequency, matrixSettings.lookbackDays, selectedMatrixSeries],
  )
  const selectedMatrixErrors = matrixUsesAllInstruments ? [] : matrixTaxonomySeriesResult.errors
  const selectedMatrixEmptyLabel = matrixUsesAllInstruments ? 'No matrix.' : defaultPlanningTaxonomy ? 'No matrix.' : 'No taxonomy.'
  const topLevelRiskContributionResult = useMemo(
    () =>
      riskInputsReady && !topLevelTaxonomySeriesResult.errors.length
        ? buildRiskContributionRows(
            alignedTopLevelTaxonomySeries,
            holdingsWorkspace?.as_of_date ?? '',
            driftSettings,
            portfolioRiskFrequency.frequency,
          )
        : riskFail(
            [...currentRiskInputErrors, ...topLevelTaxonomySeriesResult.errors],
            [] satisfies RiskContributionRow[],
          ),
    [
      alignedTopLevelTaxonomySeries,
      currentRiskInputErrors,
      driftSettings,
      holdingsWorkspace?.as_of_date,
      portfolioRiskFrequency.frequency,
      riskInputsReady,
      topLevelTaxonomySeriesResult.errors,
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
  const weightTargetGapRows = useMemo(
    () => combineTargetGapRows(saaWeightGapRows, taaWeightGapRows),
    [saaWeightGapRows, taaWeightGapRows],
  )
  const riskTargetGapRows = useMemo(
    () => combineTargetGapRows(saaRiskGapRows, taaRiskGapRows),
    [saaRiskGapRows, taaRiskGapRows],
  )
  const weightTargetGapErrors = useMemo(
    () => [...saaWeightGapResult.errors, ...taaWeightGapResult.errors],
    [saaWeightGapResult.errors, taaWeightGapResult.errors],
  )
  const riskTargetGapErrors = useMemo(
    () => [...saaRiskGapResult.errors, ...taaRiskGapResult.errors],
    [saaRiskGapResult.errors, taaRiskGapResult.errors],
  )
  function renderRiskErrors(errors: string[]) {
    const uniqueErrors = [...new Set(errors.filter(Boolean))]
    if (!uniqueErrors.length) {
      return null
    }
    return (
      <div className="inline-notice inline-notice-error">
        {uniqueErrors.map((error, index) => (
          <div key={`${index}:${error}`}>{error}</div>
        ))}
      </div>
    )
  }

  function renderCorrelationMatrix(matrix: CorrelationMatrix, emptyLabel: string) {
    if (!matrix.groups.length) {
      return <div className="price-chart-empty">{emptyLabel}</div>
    }
    const matrixMinWidth = Math.max(980, 220 + matrix.groups.length * 72)

    return (
      <div className="risk-matrix-scroll risk-covariance-scroll">
        <table className="risk-heatmap-table risk-covariance-table" style={{ minWidth: `${matrixMinWidth}px` }}>
          <colgroup>
            <col className="risk-matrix-label-col" />
            {matrix.groups.map((group) => (
              <col key={group.key} className="risk-matrix-value-col" />
            ))}
          </colgroup>
          <thead>
            <tr>
              <th className="risk-matrix-corner">Group</th>
              {matrix.groups.map((group) => (
                <th className="risk-matrix-column-header" key={group.key} title={`${group.label}; weight ${formatPercent(group.weight)}`}>
                  <span className="risk-matrix-column-label">{group.label}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.groups.map((rowGroup, rowIndex) => (
              <tr key={rowGroup.key}>
                <th className="risk-matrix-row-header" title={`${rowGroup.label}; ${rowGroup.observationCount} return observations`}>
                  <span className="risk-matrix-row-label">{rowGroup.label}</span>
                </th>
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

  function renderTargetGapPanel({
    rows,
    errors,
    ariaLabel,
    emptyLabel,
    currentLabel,
  }: {
    rows: RiskTargetGapChartRow[]
    errors: string[]
    ariaLabel: string
    emptyLabel: string
    currentLabel?: string
  }) {
    const uniqueErrors = [...new Set(errors.filter(Boolean))]
    if (uniqueErrors.length && !rows.length) {
      const insufficientDataOnly = uniqueErrors.every((error) => error === INSUFFICIENT_DATA_MESSAGE)
      return (
        <div
          className={
            insufficientDataOnly ? 'risk-chart-empty' : 'risk-chart-empty risk-chart-empty-error'
          }
        >
          {uniqueErrors.map((error, index) => (
            <div key={`${index}:${error}`}>{error}</div>
          ))}
        </div>
      )
    }
    return (
      <>
        {renderRiskErrors(uniqueErrors)}
        <RiskTargetGapChart
          rows={rows}
          ariaLabel={ariaLabel}
          emptyLabel={emptyLabel}
          currentLabel={currentLabel}
        />
      </>
    )
  }

  return (
    <PortfolioWorkspaceLayout activeSection="Risk" toolbarLabel="View: Risk Analytics">
      <section className="portfolio-detail-surface risk-page-surface">
        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {workspaceSupportError ? <div className="inline-notice inline-notice-error">{workspaceSupportError}</div> : null}
        {holdingsWorkspace ? renderRiskErrors(currentRiskInputErrors) : null}

        {workspaceLoading ? (
          <CalculationStatus />
        ) : null}

        {!workspaceLoading && !holdingsWorkspace && !workspaceError ? (
          <div className="empty-state">No data.</div>
        ) : null}

        {holdingsWorkspace ? (
          <>
            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar risk-section-toolbar">
                <div>
                  <div className="panel-title">Current Drift</div>
                  <div className="portfolio-detail-meta">
                    {defaultPlanningTaxonomy
                      ? `${defaultPlanningTaxonomy.name}; ${holdingsWorkspace.as_of_date}; ${portfolioRiskFrequency.statusLabel}; ${productionRiskMeta}`
                      : 'No taxonomy'}
                  </div>
                </div>
              </div>
              {renderRiskErrors([
                ...activeRootSaaTargetSetResult.errors,
                ...activeRootTaaTargetSetResult.errors,
                ...topLevelTaxonomySeriesResult.errors,
                ...currentPlanningGroupsResult.errors,
              ])}
              <div className="risk-target-grid">
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">Weight Target Gap</div>
                  {renderTargetGapPanel({
                    rows: weightTargetGapRows,
                    errors: weightTargetGapErrors,
                    ariaLabel: 'Weight target drift',
                    emptyLabel: 'No weight target.',
                  })}
                </div>
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">Risk Target Gap</div>
                  {renderTargetGapPanel({
                    rows: riskTargetGapRows,
                    errors: riskTargetGapErrors,
                    ariaLabel: 'Risk budget target gap',
                    emptyLabel: 'No risk target.',
                    currentLabel: 'Risk Share',
                  })}
                </div>
              </div>
            </section>

            <section className="performance-section-block risk-rolling-section">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar risk-section-toolbar risk-rolling-toolbar">
                <div className="risk-toolbar-primary risk-rolling-toolbar-primary">
                  <div>
                    <div className="panel-title">Rolling Risk</div>
                  </div>
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
                </div>
                <div className="risk-section-actions">
                  <RiskSettingsMenu
                    label="Rolling risk"
                    settings={rollingSettings}
                    onChange={setRollingSettings}
                    includeChartStyle
                  />
                </div>
              </div>
              {benchmarkLoading ? <div className="portfolio-detail-meta">Loading</div> : null}
              {benchmarkError ? <div className="overview-benchmark-error">{benchmarkError}</div> : null}
              <div className="risk-rolling-grid">
                <RollingRiskMetricChart
                  title="Annualized Volatility"
                  points={rollingVolatilityPoints}
                  benchmarkPoints={benchmarkRollingVolatilityPoints}
                  benchmarkLabel={benchmarkLabel}
                  displayStyle={rollingSettings.chartStyle}
                  formatValue={(value) => formatPercent(value)}
                  emptyLabel="Insufficient data."
                />
                <RollingRiskMetricChart
                  title="Sharpe Ratio"
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
              <div className="portfolio-detail-toolbar performance-subsection-toolbar risk-section-toolbar risk-matrix-toolbar">
                <div className="risk-toolbar-primary risk-matrix-toolbar-primary">
                  <div>
                    <div className="panel-title">Correlation Matrix</div>
                  </div>
                  <label className="risk-scope-select">
                    <div className="risk-scope-select-box">
                      <select
                        value={matrixScopeNodeId}
                        onChange={(event) => setMatrixScopeNodeId(event.target.value)}
                        aria-label="Matrix scope"
                      >
                        {matrixScopeOptions.map((option) => (
                          <option key={option.value || 'taxonomy-root'} value={option.value}>
                            {option.kind === 'taxonomy' ? `Taxonomy: ${option.label}` : option.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </label>
                </div>
                <div className="risk-section-actions">
                  <RiskSettingsMenu
                    label="Correlation matrix"
                    settings={matrixSettings}
                    onChange={setMatrixSettings}
                  />
                </div>
              </div>
              <RiskDateTimeline
                dates={riskAsOfSelectionDates}
                value={effectiveMatrixAsOfDate}
                onChange={setMatrixAsOfDate}
                label="Matrix as of"
              />
              <div className="risk-correlation-stack">
                <div className="risk-matrix-panel">
                  {selectedMatrixErrors.length
                    ? renderRiskErrors(selectedMatrixErrors)
                    : renderCorrelationMatrix(selectedCorrelationMatrix, selectedMatrixEmptyLabel)}
                </div>
              </div>
            </section>

          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
