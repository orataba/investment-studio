import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useParams } from 'react-router'

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
import QualityWarningsNotice from '../components/QualityWarningsNotice'
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
import {
  alignReturnPointsToFrequency,
  alignReturnSeriesToFrequency,
  commonReturnDateKeys,
  dayDiff,
  localDateIso,
  pairWindowReturns,
  returnPointsInWindow,
  riskWindowStart,
  windowReturnPoints,
  type CalculationFrequency,
  type GroupReturnSeries,
  type ReturnPoint,
} from '../lib/riskReturnAlignment'
import {
  buildCorrelationMatrix,
  correlationCoverageIssue,
  returnWindowCoverage,
  sampleCorrelation,
  sampleCovariance,
  weightAtOrBefore,
  type CorrelationMatrix,
  type CorrelationMatrixBuildResult,
  type CorrelationMatrixCoverageIssue,
  type CorrelationMatrixScope,
} from '../lib/riskCorrelation'
import {
  assessRiskWindowCoverage,
  riskMinObservationsForWindow,
  windowLabel,
} from '../lib/riskWindowCoverage'

const DAYS_PER_YEAR = 365.25
const DEFAULT_RISK_LOOKBACK_DAYS = 90
const DEFAULT_RISK_ANALYTICS_LOOKBACK_DAYS = 30
const DEFAULT_RISK_MODEL_ID = 'ewma_vol_shrinkage_corr_covariance'
const MATRIX_SCOPE_CURRENT_HOLDINGS = '__current_holdings__'
const MATRIX_SCOPE_FULL_UNIVERSE = '__full_universe__'
const RISK_PAGE_SETTINGS_STORAGE_KEY = 'portfolio_ops.portfolio.risk.settings.v1'
const INSUFFICIENT_DATA_MESSAGE = 'Insufficient data.'

type RiskModelId = 'ewma_vol_shrinkage_corr_covariance' | 'ewma_covariance' | 'sample_covariance'
type RiskContributionMode = 'signed' | 'abs'

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

type PortfolioTaxonomyRecord = PortfolioTaxonomyCatalogResponse['taxonomies'][number]
type PortfolioTaxonomyAssignmentRecord = PortfolioTaxonomyCatalogResponse['taxonomy_assignments'][number]

type RiskFrequencyProfile = {
  frequency: CalculationFrequency
  statusLabel: string
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

function finiteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
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

function weightedMean(values: number[], weights: number[]) {
  const totalWeight = weights.reduce((total, weight) => total + weight, 0)
  if (totalWeight <= 0) {
    return null
  }
  return values.reduce((total, value, index) => total + value * weights[index], 0) / totalWeight
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
  const periodStartByDate = new Map<string, string | null>()
  returnPoints.forEach((point) => {
    const value = finiteNumber(point.value)
    if (point.date && point.date <= asOfDate && value != null) {
      returnsByDate.set(point.date, value)
      periodStartByDate.set(point.date, point.start_date ?? null)
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
    periodStartByDate,
    endingWeightByDate,
    latestWeight,
    observationCount: returnsByDate.size,
  } satisfies GroupReturnSeries
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
      const quantity = finiteNumber(row.quantity)
      const hasExposure =
        Math.abs(currentWeight ?? 0) > 1e-9 ||
        Math.abs(currentValueBase ?? 0) > 1e-9 ||
        Math.abs(quantity ?? 0) > 1e-9
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

function matrixReturnInputIssues({
  memberKey,
  memberLabel,
  returnPoints,
  asOfDate,
}: {
  memberKey: string
  memberLabel: string
  returnPoints: ReturnPoint[]
  asOfDate: string
}) {
  const issues: CorrelationMatrixCoverageIssue[] = []
  const missingEndDateCount = returnPoints.filter((point) => !point.date).length
  if (missingEndDateCount) {
    issues.push(
      correlationCoverageIssue({
        memberKey,
        memberLabel,
        reason: 'missing_series',
        coverageReason: `Return series contains ${missingEndDateCount} observation(s) without a period end date.`,
      }),
    )
  }
  const eligiblePoints = returnPoints.filter((point) => point.date && point.date <= asOfDate)
  const invalidDates = eligiblePoints
    .filter((point) => finiteNumber(point.value) == null)
    .map((point) => point.date)
  if (invalidDates.length) {
    issues.push(
      correlationCoverageIssue({
        memberKey,
        memberLabel,
        reason: 'missing_series',
        coverageReason: 'Return series contains non-finite observations.',
        missingDates: invalidDates,
      }),
    )
  }
  const duplicateDates = [...new Set(
    eligiblePoints
      .map((point) => point.date)
      .filter((dateKey, index, dates) => dates.indexOf(dateKey) !== index),
  )].sort()
  if (duplicateDates.length) {
    issues.push(
      correlationCoverageIssue({
        memberKey,
        memberLabel,
        reason: 'misaligned_dates',
        coverageReason: 'Return series contains duplicate period end dates.',
        missingDates: duplicateDates,
      }),
    )
  }
  const missingStartDates = eligiblePoints
    .filter((point) => finiteNumber(point.value) != null && !point.start_date)
    .map((point) => point.date)
  if (missingStartDates.length) {
    issues.push(
      correlationCoverageIssue({
        memberKey,
        memberLabel,
        reason: 'misaligned_dates',
        coverageReason: 'Return series is missing period start dates required for strict alignment.',
        missingDates: missingStartDates,
      }),
    )
  }
  const reversedPeriods = eligiblePoints
    .filter((point) => {
      const startDate = point.start_date
      return Boolean(startDate && startDate >= point.date)
    })
    .map((point) => point.date)
  if (reversedPeriods.length) {
    issues.push(
      correlationCoverageIssue({
        memberKey,
        memberLabel,
        reason: 'misaligned_dates',
        coverageReason: 'Return series contains a period start that is not before its end date.',
        missingDates: reversedPeriods,
      }),
    )
  }
  return issues
}

function buildCurrentHoldingsMatrixScope(
  holdingsWorkspace: HoldingsWorkspaceResponse | null,
): CorrelationMatrixScope {
  if (!holdingsWorkspace) {
    return {
      memberCount: 0,
      series: [],
      issues: [
        correlationCoverageIssue({
          memberKey: 'current-holdings',
          memberLabel: 'Current Holdings',
          reason: 'scope_unavailable',
          coverageReason: 'Current Holdings requires the holdings workspace.',
        }),
      ],
    }
  }
  const asOfDate = holdingsWorkspace.as_of_date
  if (!asOfDate) {
    return {
      memberCount: 0,
      series: [],
      issues: [
        correlationCoverageIssue({
          memberKey: 'current-holdings',
          memberLabel: 'Current Holdings',
          reason: 'scope_unavailable',
          coverageReason: 'Current Holdings requires a holdings as-of date.',
        }),
      ],
    }
  }
  const rows = holdingsWorkspace.rows.filter((row) => {
    if (isCashHoldingRow(row)) {
      return false
    }
    const quantity = finiteNumber(row.quantity)
    const currentWeight = finiteNumber(row.allocation)
    const currentValueBase = finiteNumber(row.market_value_base)
    return (
      Math.abs(quantity ?? 0) > 1e-9 ||
      Math.abs(currentWeight ?? 0) > 1e-9 ||
      Math.abs(currentValueBase ?? 0) > 1e-9
    )
  })
  const issues: CorrelationMatrixCoverageIssue[] = []
  const seenMembers = new Set<string>()
  const series = rows.flatMap((row): GroupReturnSeries[] => {
    const memberKey = row.instrument_core.instrument_id
    const memberLabel = holdingRiskLabel(row)
    if (!memberKey || seenMembers.has(memberKey)) {
      issues.push(
        correlationCoverageIssue({
          memberKey: memberKey || row.line_id,
          memberLabel,
          reason: 'missing_member',
          coverageReason: memberKey
            ? 'Current Holdings contains duplicate rows for this scope member.'
            : 'Current holding is missing its instrument identity.',
        }),
      )
      return []
    }
    seenMembers.add(memberKey)
    const currentWeight = finiteNumber(row.allocation)
    if (currentWeight == null) {
      issues.push(
        correlationCoverageIssue({
          memberKey,
          memberLabel,
          reason: 'missing_weight',
          coverageReason: 'Current holding is missing its current portfolio weight.',
        }),
      )
    }
    const returnPoints = row.instrument_return_series_all?.points ?? []
    if (!returnPoints.length) {
      issues.push(
        correlationCoverageIssue({
          memberKey,
          memberLabel,
          reason: 'missing_series',
          coverageReason: row.instrument_trend_reason
            ? `Full-history return series is missing; coverage reason: ${row.instrument_trend_reason}.`
            : 'Full-history return series is missing from the holdings payload.',
        }),
      )
      return []
    }
    issues.push(
      ...matrixReturnInputIssues({ memberKey, memberLabel, returnPoints, asOfDate }),
    )
    const memberSeries = returnPointsToGroupSeries({
      groupKey: memberKey,
      groupLabel: memberLabel,
      returnPoints,
      asOfDate,
      latestWeight: currentWeight ?? 0,
    })
    if (!memberSeries) {
      issues.push(
        correlationCoverageIssue({
          memberKey,
          memberLabel,
          reason: 'missing_series',
          coverageReason: 'Full-history return series has no finite observations on or before the as-of date.',
        }),
      )
      return []
    }
    return [memberSeries]
  })
  return { memberCount: rows.length, series, issues }
}

function buildFullUniverseMatrixScope({
  holdingsWorkspace,
  catalog,
}: {
  holdingsWorkspace: HoldingsWorkspaceResponse | null
  catalog: PortfolioTaxonomyCatalogResponse | null
}): CorrelationMatrixScope {
  if (!holdingsWorkspace || !catalog) {
    return {
      memberCount: 0,
      series: [],
      issues: [
        correlationCoverageIssue({
          memberKey: 'full-universe',
          memberLabel: 'Full Universe',
          reason: 'scope_unavailable',
          coverageReason: 'Full Universe requires both holdings and taxonomy workspaces.',
        }),
      ],
    }
  }
  const matrixAsOfDate = holdingsWorkspace.as_of_date
  if (!matrixAsOfDate) {
    return {
      memberCount: 0,
      series: [],
      issues: [
        correlationCoverageIssue({
          memberKey: 'full-universe',
          memberLabel: 'Full Universe',
          reason: 'scope_unavailable',
          coverageReason: 'Full Universe requires a holdings as-of date.',
        }),
      ],
    }
  }

  const universeRecords = catalog.instrument_universe.filter(
    (record) => record.status === 'active' && !isCashUniverseInstrument(record),
  )

  const currentWeightByInstrumentId = new Map<string, number>()
  holdingsWorkspace.rows
    .filter((row) => !isCashHoldingRow(row))
    .forEach((row) => {
      const currentWeight = finiteNumber(row.allocation)
      if (currentWeight != null) {
        currentWeightByInstrumentId.set(row.instrument_core.instrument_id, currentWeight)
      }
    })

  const issues: CorrelationMatrixCoverageIssue[] = []
  const seenMembers = new Set<string>()
  const series = universeRecords
    .flatMap((record): GroupReturnSeries[] => {
      const instrument = record.instrument_ref
      const memberKey = record.instrument_id
      const memberLabel = instrument?.instrument_name || memberKey
      if (!memberKey || seenMembers.has(memberKey)) {
        issues.push(
          correlationCoverageIssue({
            memberKey: memberKey || 'unknown-universe-member',
            memberLabel,
            reason: 'missing_member',
            coverageReason: memberKey
              ? 'Full Universe contains duplicate active member rows.'
              : 'Full Universe contains a member without instrument identity.',
          }),
        )
        return []
      }
      seenMembers.add(memberKey)
      if (!instrument) {
        issues.push(
          correlationCoverageIssue({
            memberKey,
            memberLabel,
            reason: 'missing_member',
            coverageReason: 'Active universe member is missing instrument metadata.',
          }),
        )
      }
      const returnPoints = record.instrument_return_series_all?.points ?? []
      if (!returnPoints.length) {
        issues.push(
          correlationCoverageIssue({
            memberKey,
            memberLabel,
            reason: 'missing_series',
            coverageReason: 'Full-history return series is missing from the active universe payload.',
          }),
        )
        return []
      }
      issues.push(
        ...matrixReturnInputIssues({
          memberKey,
          memberLabel,
          returnPoints,
          asOfDate: matrixAsOfDate,
        }),
      )
      const memberSeries = returnPointsToGroupSeries({
        groupKey: memberKey,
        groupLabel: memberLabel,
        returnPoints,
        asOfDate: matrixAsOfDate,
        latestWeight: currentWeightByInstrumentId.get(memberKey) ?? 0,
      })
      if (!memberSeries) {
        issues.push(
          correlationCoverageIssue({
            memberKey,
            memberLabel,
            reason: 'missing_series',
            coverageReason: 'Full-history return series has no finite observations on or before the as-of date.',
          }),
        )
        return []
      }
      return [memberSeries]
    })
    .sort((left, right) => {
      const weightDelta = Math.abs(right.latestWeight ?? 0) - Math.abs(left.latestWeight ?? 0)
      return weightDelta || left.groupLabel.localeCompare(right.groupLabel)
    })
  return { memberCount: universeRecords.length, series, issues }
}

function alignCorrelationMatrixScope(
  scope: CorrelationMatrixScope,
  frequency: CalculationFrequency,
  finalDate: string,
): CorrelationMatrixScope {
  const issues = [...scope.issues]
  const series = scope.series.flatMap((item): GroupReturnSeries[] => {
    const aligned = alignReturnSeriesToFrequency([item], frequency, finalDate)[0]
    if (aligned) {
      return [aligned]
    }
    issues.push(
      correlationCoverageIssue({
        memberKey: item.groupKey,
        memberLabel: item.groupLabel,
        reason: 'missing_series',
        coverageReason: `Return series has no finite ${frequency} observations on or before ${finalDate}.`,
      }),
    )
    return []
  })
  return { ...scope, series, issues }
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
      const periodStartByDate = new Map<string, string | null>()
      commonDates.forEach((dateKey) => {
        const value = members.reduce(
          (total, item) => total + ((item.latestWeight ?? 0) / groupWeight) * (item.returnsByDate.get(dateKey) ?? 0),
          0,
        )
        returnsByDate.set(dateKey, value)
        periodStartByDate.set(dateKey, members[0]?.periodStartByDate.get(dateKey) ?? null)
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
        periodStartByDate,
        endingWeightByDate,
        latestWeight: groupWeight,
        observationCount: returnsByDate.size,
        sourceMembers: members,
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

function isCashTaxonomyNode(node: PortfolioTaxonomyNodeRecord | null | undefined) {
  if (!node) {
    return false
  }
  const normalizedName = node.node_name.trim().toLowerCase()
  const normalizedCode = (node.node_code ?? '').trim().toLowerCase()
  return normalizedCode === 'cash' || normalizedName === 'cash' || normalizedName === '现金'
}

function isCashLikeTaxonomyNodeId(
  nodeId: string,
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>,
) {
  const visited = new Set<string>()
  let node = nodeById.get(nodeId) ?? null
  while (node && !visited.has(node.taxonomy_node_id)) {
    if (isCashTaxonomyNode(node)) {
      return true
    }
    visited.add(node.taxonomy_node_id)
    node = node.parent_taxonomy_node_id
      ? nodeById.get(node.parent_taxonomy_node_id) ?? null
      : null
  }
  return false
}

function isCashLikeRiskTargetLine(
  line: PortfolioTargetSetLineRecord,
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>,
  cashLikeNodeIds: ReadonlySet<string>,
) {
  return (
    line.target_member_type === 'cash_bucket' ||
    (line.target_member_type === 'taxonomy_node' &&
      (cashLikeNodeIds.has(line.target_member_id) ||
        isCashLikeTaxonomyNodeId(line.target_member_id, nodeById)))
  )
}

function isCashLikeCurrentPlanningGroup(
  group: CurrentPlanningGroup,
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>,
  cashLikeNodeIds: ReadonlySet<string>,
) {
  return (
    cashLikeNodeIds.has(group.groupKey) ||
    isCashLikeTaxonomyNodeId(group.groupKey, nodeById) ||
    (group.hasCashLikeInput && !group.hasMarketRiskInput)
  )
}

function buildCashLikeTaxonomyNodeIds(
  catalog: PortfolioTaxonomyCatalogResponse | null,
  taxonomyId: string | null | undefined,
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>,
) {
  const resolvedTaxonomyId = taxonomyId ?? ''
  const childrenByParent = new Map<string, string[]>()
  nodeById.forEach((node) => {
    if (node.status !== 'active') {
      return
    }
    const parentId = node.parent_taxonomy_node_id ?? ''
    childrenByParent.set(parentId, [...(childrenByParent.get(parentId) ?? []), node.taxonomy_node_id])
  })
  const assignmentScopesByNode = new Map<string, string[]>()
  ;(catalog?.taxonomy_assignments ?? []).forEach((assignment) => {
    if (assignment.taxonomy_id !== resolvedTaxonomyId || assignment.status !== 'active') {
      return
    }
    assignmentScopesByNode.set(assignment.taxonomy_node_id, [
      ...(assignmentScopesByNode.get(assignment.taxonomy_node_id) ?? []),
      assignment.target_scope,
    ])
  })

  const cashLikeNodeIds = new Set<string>()
  nodeById.forEach((node) => {
    if (isCashLikeTaxonomyNodeId(node.taxonomy_node_id, nodeById)) {
      cashLikeNodeIds.add(node.taxonomy_node_id)
      return
    }
    const pending = [node.taxonomy_node_id]
    const subtreeNodeIds = new Set<string>()
    while (pending.length) {
      const currentNodeId = pending.pop()!
      if (subtreeNodeIds.has(currentNodeId)) {
        continue
      }
      subtreeNodeIds.add(currentNodeId)
      pending.push(...(childrenByParent.get(currentNodeId) ?? []))
    }
    const assignmentScopes = [...subtreeNodeIds].flatMap(
      (subtreeNodeId) => assignmentScopesByNode.get(subtreeNodeId) ?? [],
    )
    if (assignmentScopes.length && assignmentScopes.every((scope) => scope === 'cash_bucket')) {
      cashLikeNodeIds.add(node.taxonomy_node_id)
    }
  })
  return cashLikeNodeIds
}

export function buildTargetGapRows({
  targetSet,
  targetLines,
  currentGroups,
  riskSharesByGroup,
  riskShareErrors = [],
  nodeById,
  cashLikeNodeIds = new Set<string>(),
  dimension,
  baseCurrency,
}: {
  targetSet: PortfolioTargetSetRecord | null
  targetLines: PortfolioTargetSetLineRecord[]
  currentGroups: CurrentPlanningGroup[]
  riskSharesByGroup: Map<string, number | null>
  riskShareErrors?: string[]
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>
  cashLikeNodeIds?: ReadonlySet<string>
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
  const eligibleTargetLines =
    dimension === 'risk_budget'
      ? targetLines.filter((line) => !isCashLikeRiskTargetLine(line, nodeById, cashLikeNodeIds))
      : targetLines
  const eligibleCurrentGroups =
    dimension === 'risk_budget'
      ? currentGroups.filter((group) => !isCashLikeCurrentPlanningGroup(group, nodeById, cashLikeNodeIds))
      : currentGroups
  if (!eligibleTargetLines.length) {
    if (dimension === 'risk_budget' && !eligibleCurrentGroups.length) {
      return riskOk([] satisfies TargetGapComparatorRow[])
    }
    return riskFail(
      dimension === 'risk_budget'
        ? `${targetSet.name} is active but has no risk-bearing target lines.`
        : `${targetSet.name} is active but has no target lines.`,
      [] satisfies TargetGapComparatorRow[],
    )
  }
  const nonNodeLines = eligibleTargetLines.filter((line) => line.target_member_type !== 'taxonomy_node')
  if (nonNodeLines.length) {
    return riskFail(
      `${targetSet.name} root target drift must be defined on taxonomy_node budgeting members; unsupported direct members: ${nonNodeLines
        .map((line) => `${line.target_member_type}:${line.target_member_id}`)
        .join(', ')}.`,
      [] satisfies TargetGapComparatorRow[],
    )
  }
  const missingTargetNodes = eligibleTargetLines.filter((line) => !nodeById.has(line.target_member_id))
  if (missingTargetNodes.length) {
    return riskFail(
      `${targetSet.name} references missing taxonomy nodes: ${missingTargetNodes.map((line) => line.target_member_id).join(', ')}.`,
      [] satisfies TargetGapComparatorRow[],
    )
  }
  if (dimension === 'risk_budget' && riskShareErrors.length) {
    return riskFail(INSUFFICIENT_DATA_MESSAGE, [] satisfies TargetGapComparatorRow[])
  }
  const targetLineErrors = eligibleTargetLines
    .filter((line) => (dimension === 'weight' ? line.target_weight : line.target_risk_share) == null)
    .map((line) => `${targetSet.name} is missing ${dimension === 'weight' ? 'target_weight' : 'target_risk_share'} for ${lineDisplayLabel(line, nodeById)}.`)
  if (targetLineErrors.length) {
    return riskFail(targetLineErrors, [] satisfies TargetGapComparatorRow[])
  }
  const targetTotal = eligibleTargetLines.reduce(
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
  if (!normalizedBaseCurrency && eligibleCurrentGroups.some((group) => group.currentValueBase != null)) {
    return riskFail(
      `${targetSet.name} target drift requires the portfolio base currency before value details can be rendered.`,
      [] satisfies TargetGapComparatorRow[],
    )
  }

  const currentGroupByKey = new Map(eligibleCurrentGroups.map((group) => [group.groupKey, group] as const))
  const lineByKey = new Map(eligibleTargetLines.map((line) => [lineDisplayKey(line), line] as const))
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
  const [matrixScopeNodeId, setMatrixScopeNodeId] = useState(MATRIX_SCOPE_CURRENT_HOLDINGS)
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

    getHoldingsWorkspace(portfolioId, { include_details: true })
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
  const cashLikePlanningNodeIds = useMemo(
    () =>
      buildCashLikeTaxonomyNodeIds(
        taxonomyCatalog,
        defaultPlanningTaxonomy?.taxonomy_id,
        defaultTaxonomyNodeById,
      ),
    [defaultPlanningTaxonomy?.taxonomy_id, defaultTaxonomyNodeById, taxonomyCatalog],
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
  const matrixUsesCurrentHoldings = matrixScopeNodeId === MATRIX_SCOPE_CURRENT_HOLDINGS
  const matrixUsesFullUniverse = matrixScopeNodeId === MATRIX_SCOPE_FULL_UNIVERSE
  const matrixUsesTaxonomy = !matrixUsesCurrentHoldings && !matrixUsesFullUniverse
  const fullUniverseRiskFrequency = useMemo(() => {
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
  const matrixRiskFrequency = matrixUsesFullUniverse ? fullUniverseRiskFrequency : portfolioRiskFrequency
  const currentHoldingsMatrixScope = useMemo(() => {
    const scope = buildCurrentHoldingsMatrixScope(holdingsWorkspace)
    const frequencyIssues = portfolioRiskFrequencyErrors.map((coverageReason) =>
      correlationCoverageIssue({
        memberKey: 'current-holdings',
        memberLabel: 'Current Holdings',
        reason: 'scope_unavailable',
        coverageReason,
      }),
    )
    return alignCorrelationMatrixScope(
      { ...scope, issues: [...scope.issues, ...frequencyIssues] },
      portfolioRiskFrequency.frequency,
      riskBasisFinalDate,
    )
  }, [holdingsWorkspace, portfolioRiskFrequency.frequency, portfolioRiskFrequencyErrors, riskBasisFinalDate])
  const fullUniverseMatrixScope = useMemo(
    () =>
      alignCorrelationMatrixScope(
        buildFullUniverseMatrixScope({ holdingsWorkspace, catalog: taxonomyCatalog }),
        fullUniverseRiskFrequency.frequency,
        riskBasisFinalDate,
      ),
    [
      fullUniverseRiskFrequency.frequency,
      holdingsWorkspace,
      riskBasisFinalDate,
      taxonomyCatalog,
    ],
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
      {
        value: MATRIX_SCOPE_CURRENT_HOLDINGS,
        label: 'Current Holdings',
        kind: 'instrument' as const,
      },
      {
        value: MATRIX_SCOPE_FULL_UNIVERSE,
        label: 'Full Universe',
        kind: 'instrument' as const,
      },
      ...matrixTaxonomyScopeOptions.map((option) => ({
        ...option,
        kind: 'taxonomy' as const,
      })),
    ],
    [matrixTaxonomyScopeOptions],
  )
  const matrixTaxonomyScopeNodeId = matrixUsesTaxonomy ? matrixScopeNodeId : ''
  useEffect(() => {
    if (!matrixScopeOptions.some((option) => option.value === matrixScopeNodeId)) {
      setMatrixScopeNodeId(MATRIX_SCOPE_CURRENT_HOLDINGS)
    }
  }, [matrixScopeNodeId, matrixScopeOptions])
  const matrixTaxonomySeriesResult = useMemo(
    () =>
      !matrixUsesTaxonomy
        ? riskOk([] satisfies GroupReturnSeries[])
        : currentHoldingsMatrixScope.issues.length
          ? riskFail(
              currentHoldingsMatrixScope.issues.map(
                (issue) => `${issue.memberLabel}: ${issue.coverageReason}`,
              ),
              [] satisfies GroupReturnSeries[],
            )
        : buildCurrentTaxonomyReturnSeries({
            instrumentSeries: currentHoldingsMatrixScope.series,
            catalog: taxonomyCatalog,
            taxonomy: defaultPlanningTaxonomy,
            scopeNodeId: matrixTaxonomyScopeNodeId,
            referenceDate: holdingsWorkspace?.as_of_date ?? null,
          }),
    [
      defaultPlanningTaxonomy,
      holdingsWorkspace?.as_of_date,
      currentHoldingsMatrixScope,
      matrixTaxonomyScopeNodeId,
      matrixUsesTaxonomy,
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
  const taxonomyMatrixScope = useMemo<CorrelationMatrixScope>(() => {
    const issues = matrixTaxonomySeriesResult.errors.map((coverageReason) =>
      correlationCoverageIssue({
        memberKey: matrixTaxonomyScopeNodeId || 'taxonomy-root',
        memberLabel: defaultPlanningTaxonomy?.name || 'Planning taxonomy',
        reason: 'scope_unavailable',
        coverageReason,
      }),
    )
    return {
      memberCount: alignedMatrixTaxonomySeries.length,
      series: alignedMatrixTaxonomySeries,
      issues,
    }
  }, [
    alignedMatrixTaxonomySeries,
    defaultPlanningTaxonomy?.name,
    matrixTaxonomyScopeNodeId,
    matrixTaxonomySeriesResult.errors,
  ])
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
  const selectedMatrixScope = matrixUsesCurrentHoldings
    ? currentHoldingsMatrixScope
    : matrixUsesFullUniverse
      ? fullUniverseMatrixScope
      : taxonomyMatrixScope
  const riskAsOfSelectionDates = useMemo(
    () =>
      [...new Set(selectedMatrixScope.series.flatMap((item) => [...item.returnsByDate.keys()]))]
        .filter(Boolean)
        .sort(),
    [selectedMatrixScope.series],
  )
  const effectiveMatrixAsOfDate =
    matrixAsOfDate || riskAsOfSelectionDates[riskAsOfSelectionDates.length - 1] || riskBasisFinalDate

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

  const selectedCorrelationMatrixResult = useMemo(
    () =>
      buildCorrelationMatrix(
        selectedMatrixScope,
        effectiveMatrixAsOfDate,
        matrixSettings.lookbackDays,
        matrixRiskFrequency.frequency,
      ),
    [effectiveMatrixAsOfDate, matrixRiskFrequency.frequency, matrixSettings.lookbackDays, selectedMatrixScope],
  )
  const selectedMatrixEmptyLabel = matrixUsesTaxonomy && !defaultPlanningTaxonomy ? 'No taxonomy.' : 'No matrix.'
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
        cashLikeNodeIds: cashLikePlanningNodeIds,
        dimension: 'weight',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootSaaTargetSet,
      cashLikePlanningNodeIds,
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
        cashLikeNodeIds: cashLikePlanningNodeIds,
        dimension: 'weight',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootTaaTargetSet,
      cashLikePlanningNodeIds,
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
        cashLikeNodeIds: cashLikePlanningNodeIds,
        dimension: 'risk_budget',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootSaaTargetSet,
      cashLikePlanningNodeIds,
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
        cashLikeNodeIds: cashLikePlanningNodeIds,
        dimension: 'risk_budget',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootTaaTargetSet,
      cashLikePlanningNodeIds,
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

  function renderCorrelationCoverageIssues(result: CorrelationMatrixBuildResult) {
    if (!result.issues.length) {
      return null
    }
    return (
      <div
        className="inline-notice inline-notice-error"
        role="alert"
        aria-label="Correlation matrix coverage issues"
      >
        <div>
          Correlation matrix unavailable. All {result.scopeMemberCount} scope members must share one
          complete aligned return window; no members or dates were dropped.
        </div>
        <ul>
          {result.issues.map((issue, index) => {
            const visibleMissingDates = issue.missingDates.slice(0, 3)
            return (
              <li
                key={`${issue.memberKey}:${issue.reason}:${issue.coverageReason}:${index}`}
              >
                <strong>{issue.memberLabel}</strong>: {issue.coverageReason}
                {issue.missingDateCount
                  ? ` Missing dates (${issue.missingDateCount} total): ${visibleMissingDates.join(', ')}${
                      issue.missingDateCount > visibleMissingDates.length ? ', ...' : ''
                    }.`
                  : ''}
              </li>
            )
          })}
        </ul>
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
    <PortfolioWorkspaceLayout
      activeSection="Risk"
      toolbarLabel="View: Risk Analytics"
      busy={workspaceLoading || benchmarkLoading}
    >
      <section className="portfolio-detail-surface risk-page-surface">
        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {workspaceSupportError ? <div className="inline-notice inline-notice-error">{workspaceSupportError}</div> : null}
        <QualityWarningsNotice warnings={holdingsWorkspace?.quality_warnings} />
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
                  {selectedCorrelationMatrixResult.issues.length
                    ? renderCorrelationCoverageIssues(selectedCorrelationMatrixResult)
                    : renderCorrelationMatrix(
                        selectedCorrelationMatrixResult.matrix,
                        selectedMatrixEmptyLabel,
                      )}
                </div>
              </div>
            </section>

          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
