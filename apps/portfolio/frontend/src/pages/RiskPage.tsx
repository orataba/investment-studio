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
  type InstrumentCore,
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
  returnPointsInWindow,
  type CalculationFrequency,
  type GroupReturnSeries,
  type ReturnPoint,
} from '../lib/riskReturnAlignment'
import {
  buildCorrelationMatrix,
  correlationCoverageIssue,
  sampleCovariance,
  type CorrelationMatrix,
  type CorrelationMatrixBuildResult,
  type CorrelationMatrixCoverageIssue,
  type CorrelationMatrixScope,
} from '../lib/riskCorrelation'
import {
  assessRiskWindowCoverage,
  windowLabel,
} from '../lib/riskWindowCoverage'

const DAYS_PER_YEAR = 365.25
const DEFAULT_RISK_LOOKBACK_DAYS = 90
const DEFAULT_RISK_ANALYTICS_LOOKBACK_DAYS = 30
const DEFAULT_RISK_MODEL_ID = 'ewma_vol_shrinkage_corr_covariance'
const MATRIX_SCOPE_CURRENT_HOLDINGS = '__current_holdings__'
const MATRIX_SCOPE_FULL_UNIVERSE = '__full_universe__'
const RISK_PAGE_SETTINGS_STORAGE_KEY = 'portfolio_ops.portfolio.risk.settings.v1'
const SYSTEM_CASH_TARGET_MEMBER_ID = '__cash__'
const SYSTEM_DERIVATIVE_TARGET_MEMBER_ID = '__derivatives__'

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
}

function isCalculationFrequency(value: string | null | undefined): value is CalculationFrequency {
  return value === 'daily'
}

export function riskFrequencyProfileFromHoldingsWorkspace(
  holdingsWorkspace: HoldingsWorkspaceResponse | null,
): RiskCalculationResult<RiskFrequencyProfile> {
  const unavailableProfile = {
    frequency: 'daily',
    statusLabel: 'Risk basis unavailable',
  } satisfies RiskFrequencyProfile
  if (!holdingsWorkspace) {
    return riskFail('Risk basis requires the holdings workspace response.', unavailableProfile)
  }
  const coverageState = holdingsWorkspace.risk_basis?.coverage_state
  const riskBearingInstrumentIds = new Set(
    holdingsWorkspace.rows
      .filter((row) => isRiskBearingHoldingRow(row))
      .map((row) => row.instrument_core.instrument_id),
  )
  const gapInstrumentIds = holdingsWorkspace.risk_basis?.gap_instrument_ids ?? []
  const scopedGapInstrumentIds = gapInstrumentIds.filter((instrumentId) =>
    riskBearingInstrumentIds.has(instrumentId),
  )
  if (
    coverageState &&
    coverageState !== 'complete' &&
    (!gapInstrumentIds.length || scopedGapInstrumentIds.length > 0)
  ) {
    return riskFail(
      holdingsWorkspace.risk_basis?.status_label ||
        `Risk basis coverage is ${coverageState}.`,
      unavailableProfile,
    )
  }
  const riskyHoldingCount = holdingsWorkspace.rows.filter((row) => isRiskBearingHoldingRow(row)).length
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
      coverageState && coverageState !== 'complete'
        ? `${CALCULATION_FREQUENCY_LABELS[frequency]} risk basis`
        : holdingsWorkspace.risk_basis?.status_label ||
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
      returns.push({
        start_date: previous.date,
        date: current.date,
        value: current.value / previous.value - 1,
      })
    }
  }
  return returns
}

export function benchmarkRiskBasisAssessment(
  chart: PortfolioInstrumentPriceChartResponse | null,
  portfolioBaseCurrency: string,
) {
  if (!chart) {
    return { blocking: false, message: null as string | null }
  }
  const baseCurrency = portfolioBaseCurrency.trim().toUpperCase()
  const benchmarkCurrency = chart.currency.trim().toUpperCase()
  if (!baseCurrency || !benchmarkCurrency || benchmarkCurrency !== baseCurrency) {
    return {
      blocking: true,
      message: `Benchmark risk comparison requires a base-currency return series; got ${benchmarkCurrency || 'unknown'} versus ${baseCurrency || 'unknown'}.`,
    }
  }
  if (chart.coverage_state === 'unavailable') {
    return {
      blocking: true,
      message: chart.selection_reason || 'Benchmark return history is unavailable.',
    }
  }
  if (!chart.return_semantics || chart.return_semantics === 'unknown') {
    return {
      blocking: true,
      message: 'Benchmark risk comparison requires confirmed price-return or total-return semantics.',
    }
  }
  if (chart.return_semantics === 'price_return') {
    return {
      blocking: false,
      message: 'Benchmark uses price returns; volatility and Sharpe exclude distributions and are not fully comparable with portfolio total returns.',
    }
  }
  return { blocking: false, message: null as string | null }
}

function buildCurrentWeightedPortfolioReturnPoints(series: GroupReturnSeries[]) {
  const activeSeries = series.filter((item) => Math.abs(item.latestWeight ?? 0) > 1e-9)
  const commonDates = commonReturnDateKeys(activeSeries)
  return commonDates.map((dateKey) => {
    const periodStarts = new Set(
      activeSeries.map((item) => item.periodStartByDate.get(dateKey) ?? null),
    )
    return {
      date: dateKey,
      value: activeSeries.reduce(
        (total, item) => total + (item.latestWeight ?? 0) * (item.returnsByDate.get(dateKey) ?? 0),
        0,
      ),
      start_date:
        periodStarts.size === 1
          ? activeSeries[0]?.periodStartByDate.get(dateKey) ?? null
          : null,
    }
  })
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
  const coverage = assessRiskWindowCoverage(
    dates,
    asOfDate,
    lookbackDays,
    frequency,
    parameters,
    windowPoints[0]?.start_date,
  )
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

type HoldingsRow = HoldingsWorkspaceResponse['rows'][number]
type MarketInstrumentHoldingRow = HoldingsRow & { instrument_core: InstrumentCore }

function holdingRiskLabel(row: HoldingsRow) {
  return row.instrument_core?.instrument_name || row.derivative_contract?.contract_name || row.position_reference_id || row.line_id
}

function isCashHoldingRow(row: HoldingsRow) {
  return row.instrument_core?.instrument_type.trim().toLowerCase() === 'cash'
}

function isPendingMonetaryHoldingRow(row: HoldingsWorkspaceResponse['rows'][number]) {
  return (
    row.holding_kind?.startsWith('pending_') === true ||
    row.holding_kind === 'settlement_receivable' ||
    row.holding_kind === 'settlement_payable' ||
    row.holding_kind === 'position_recognition_adjustment' ||
    row.line_id.trim().toLowerCase().startsWith('pending:')
  )
}

function isRiskBearingHoldingRow(row: HoldingsRow): row is MarketInstrumentHoldingRow {
  return (
    row.instrument_core !== null &&
    row.risk_eligible === true &&
    !isCashHoldingRow(row) &&
    !isPendingMonetaryHoldingRow(row)
  )
}

function isNonCashPositionHoldingRow(row: HoldingsRow): row is MarketInstrumentHoldingRow {
  return (
    row.instrument_core !== null &&
    (row.holding_kind ?? 'position') === 'position' &&
    !isCashHoldingRow(row) &&
    !isPendingMonetaryHoldingRow(row)
  )
}

function isSecurityHoldingRow(row: HoldingsRow): row is MarketInstrumentHoldingRow {
  return row.holding_category === 'securities' && isNonCashPositionHoldingRow(row)
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

export function buildCurrentInstrumentReturnSeries(holdingsWorkspace: HoldingsWorkspaceResponse | null) {
  if (!holdingsWorkspace) {
    return riskFail('Current risk requires the holdings workspace.', [] satisfies GroupReturnSeries[])
  }
  const asOfDate = holdingsWorkspace.as_of_date
  if (!asOfDate) {
    return riskFail('Current risk requires a holdings as-of date.', [] satisfies GroupReturnSeries[])
  }

  const errors: string[] = []
  const baseCurrency = holdingsWorkspace.base_currency.trim().toUpperCase()
  if (!baseCurrency) {
    return riskFail('Current risk requires the portfolio base currency.', [] satisfies GroupReturnSeries[])
  }
  holdingsWorkspace.rows.forEach((row) => {
    const currentWeight = finiteNumber(row.allocation)
    const currentValueBase = finiteNumber(row.market_value_base)
    const quantity = finiteNumber(row.quantity)
    const hasExposure =
      Math.abs(currentWeight ?? 0) > 1e-9 ||
      Math.abs(currentValueBase ?? 0) > 1e-9 ||
      Math.abs(quantity ?? 0) > 1e-9
    if (!hasExposure) {
      return
    }
    const rowCurrency = (row.derivative_contract?.currency || row.instrument_core?.currency || '')
      .trim()
      .toUpperCase()
    if (
      (isCashHoldingRow(row) || isPendingMonetaryHoldingRow(row)) &&
      rowCurrency !== baseCurrency
    ) {
      errors.push(
        `Current risk requires an FX total-return series for non-base monetary exposure ${holdingRiskLabel(row)} (${rowCurrency || 'unknown'} versus ${baseCurrency}).`,
      )
    } else if (
      row.holding_category === 'securities' &&
      row.risk_eligible !== true
    ) {
      errors.push(
        `Current risk cannot model policy-excluded market exposure ${holdingRiskLabel(row)} as zero risk.`,
      )
    }
  })
  const series = holdingsWorkspace.rows
    .filter((row) => isRiskBearingHoldingRow(row))
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
      const returnCurrency = row.instrument_core.currency.trim().toUpperCase()
      if (!returnCurrency) {
        errors.push(`Current risk requires a return currency for ${label}.`)
        return null
      }
      if (returnCurrency !== baseCurrency) {
        errors.push(
          `Current risk requires base-currency total returns; ${label} is ${returnCurrency} while the portfolio base currency is ${baseCurrency}.`,
        )
        return null
      }
      if (currentWeight == null) {
        errors.push(`Current risk requires a current portfolio weight for ${label}.`)
        return null
      }
      const inputIssues = matrixReturnInputIssues({
        memberKey: row.instrument_core.instrument_id,
        memberLabel: label,
        returnPoints: row.instrument_return_series_all?.points ?? [],
        asOfDate,
      })
      inputIssues.forEach((issue) => {
        errors.push(`${label}: ${issue.coverageReason}`)
      })
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

  const activeSeries = series.filter((item) => Math.abs(item.latestWeight ?? 0) > 1e-9)
  if (!activeSeries.length) {
    errors.push('Current risk requires at least one modeled market asset.')
  }
  const historyStarts = activeSeries
    .map((item) => [...item.returnsByDate.keys()].sort()[0] ?? '')
    .filter(Boolean)
    .sort()
  const commonHistoryStart = historyStarts[historyStarts.length - 1]
  if (commonHistoryStart) {
    const alignedDates = [
      ...new Set(
        activeSeries.flatMap((item) =>
          [...item.returnsByDate.keys()].filter(
            (dateKey) => dateKey >= commonHistoryStart && dateKey <= asOfDate,
          ),
        ),
      ),
    ].sort()
    activeSeries.forEach((item) => {
      const missingDates = alignedDates.filter((dateKey) => !item.returnsByDate.has(dateKey))
      if (missingDates.length) {
        errors.push(
          `${item.groupLabel}: Current rolling risk requires identical return dates after all active holdings have history; missing ${missingDates.length} date(s), beginning ${missingDates
            .slice(0, 3)
            .join(', ')}${missingDates.length > 3 ? ', ...' : ''}.`,
        )
      }
    })
    alignedDates.forEach((dateKey) => {
      const startsByMember = activeSeries
        .filter((item) => item.returnsByDate.has(dateKey))
        .map((item) => ({
          label: item.groupLabel,
          startDate: item.periodStartByDate.get(dateKey) ?? null,
        }))
      const distinctStarts = new Set(startsByMember.map((item) => item.startDate))
      if (distinctStarts.size > 1) {
        errors.push(
          `Current rolling risk requires one period identity for the return ending ${dateKey}; ${startsByMember
            .map((item) => `${item.label}=${item.startDate ?? 'missing'}`)
            .join(', ')}.`,
        )
      }
    })
  }

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
  const baseCurrency = holdingsWorkspace.base_currency.trim().toUpperCase()
  const issues: CorrelationMatrixCoverageIssue[] = []
  const rows = holdingsWorkspace.rows
    .filter(isRiskBearingHoldingRow)
    .filter((row) => {
      const quantity = finiteNumber(row.quantity)
      const currentWeight = finiteNumber(row.allocation)
      const currentValueBase = finiteNumber(row.market_value_base)
      return (
        Math.abs(quantity ?? 0) > 1e-9 ||
        Math.abs(currentWeight ?? 0) > 1e-9 ||
        Math.abs(currentValueBase ?? 0) > 1e-9
      )
    })
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
    const returnCurrency = row.instrument_core.currency.trim().toUpperCase()
    if (!baseCurrency || !returnCurrency || returnCurrency !== baseCurrency) {
      issues.push(
        correlationCoverageIssue({
          memberKey,
          memberLabel,
          reason: 'scope_unavailable',
          coverageReason: `Correlation requires base-currency total returns (${returnCurrency || 'unknown'} versus ${baseCurrency || 'unknown'}).`,
        }),
      )
      return []
    }
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
  const baseCurrency = holdingsWorkspace.base_currency.trim().toUpperCase()

  const universeRecords = catalog.instrument_universe.filter(
    (record) => record.status === 'active' && !isCashUniverseInstrument(record),
  )

  const currentWeightByInstrumentId = new Map<string, number>()
  holdingsWorkspace.rows
    .filter((row) => isRiskBearingHoldingRow(row))
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
        return []
      }
      const returnCurrency = instrument.currency.trim().toUpperCase()
      if (!baseCurrency || !returnCurrency || returnCurrency !== baseCurrency) {
        issues.push(
          correlationCoverageIssue({
            memberKey,
            memberLabel,
            reason: 'scope_unavailable',
            coverageReason: `Correlation requires base-currency total returns (${returnCurrency || 'unknown'} versus ${baseCurrency || 'unknown'}).`,
          }),
        )
        return []
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

export function buildCanonicalTaxonomyRiskContributionRows({
  holdingsWorkspace,
  catalog,
  taxonomy,
  referenceDate,
  eligibility = 'risk',
}: {
  holdingsWorkspace: HoldingsWorkspaceResponse | null
  catalog: PortfolioTaxonomyCatalogResponse | null
  taxonomy: PortfolioTaxonomyRecord | null
  referenceDate: string | null
  eligibility?: 'risk' | 'risk_budget'
}) {
  if (!holdingsWorkspace || !catalog || !taxonomy || !referenceDate) {
    return riskFail(
      'Current taxonomy risk contribution requires holdings, taxonomy, and an as-of date.',
      [] satisfies RiskContributionRow[],
    )
  }
  const forwardRisk = holdingsWorkspace.forward_risk
  if (forwardRisk?.status !== 'ok') {
    return riskFail(
      forwardRisk?.errors?.length
        ? forwardRisk.errors
        : 'Production forward risk contribution is unavailable.',
      [] satisfies RiskContributionRow[],
    )
  }
  const nodeById = buildNodeLookup(catalog, taxonomy.taxonomy_id)
  const grouped = new Map<string, RiskContributionRow>()
  const errors: string[] = []
  holdingsWorkspace.rows
    .filter(isRiskBearingHoldingRow)
    .filter((row) => {
      if (eligibility === 'risk_budget' && row.risk_budget_eligible !== true) {
        return false
      }
      return (
        Math.abs(finiteNumber(row.allocation) ?? 0) > 1e-9 ||
        Math.abs(finiteNumber(row.market_value_base) ?? 0) > 1e-9 ||
        Math.abs(finiteNumber(row.quantity) ?? 0) > 1e-9
      )
    })
    .forEach((row) => {
      const label = holdingRiskLabel(row)
      const riskShare = finiteNumber(row.forward_risk_share)
      const contributionToVariance = finiteNumber(row.forward_contribution_to_variance)
      const currentWeight = finiteNumber(row.allocation)
      if (row.forward_risk_status !== 'ok' || riskShare == null || contributionToVariance == null) {
        errors.push(`Production forward risk contribution is missing for ${label}.`)
        return
      }
      if (currentWeight == null) {
        errors.push(`Production forward risk contribution is missing current weight for ${label}.`)
        return
      }
      const assignmentResult = resolveActiveAssignment(
        catalog,
        taxonomy.taxonomy_id,
        'instrument',
        row.instrument_core.instrument_id,
        referenceDate,
      )
      if (assignmentResult.error) {
        errors.push(assignmentResult.error)
        return
      }
      const topLevelNode = resolveScopedTaxonomyNode(
        assignmentResult.assignment?.taxonomy_node_id,
        nodeById,
        '',
      )
      const groupKey = topLevelNode?.taxonomy_node_id ?? `unassigned:${taxonomy.taxonomy_id}`
      const groupLabel = topLevelNode?.node_name ?? 'Unassigned'
      const current = grouped.get(groupKey) ?? {
        groupKey,
        groupLabel,
        weight: 0,
        annualizedVolatility: null,
        riskShare: 0,
        contributionToVariance: 0,
        observationCount: forwardRisk.observation_count ?? 0,
      }
      current.weight = (current.weight ?? 0) + currentWeight
      current.riskShare = (current.riskShare ?? 0) + riskShare
      current.contributionToVariance = (current.contributionToVariance ?? 0) + contributionToVariance
      grouped.set(groupKey, current)
    })

  if (errors.length) {
    return riskFail(errors, [] satisfies RiskContributionRow[])
  }
  const rows = [...grouped.values()].sort(
    (left, right) =>
      Math.abs(right.riskShare ?? 0) - Math.abs(left.riskShare ?? 0) ||
      left.groupLabel.localeCompare(right.groupLabel),
  )
  const totalRiskShare = rows.reduce((total, row) => total + (row.riskShare ?? 0), 0)
  if (eligibility === 'risk_budget' && !rows.length) {
    return riskOk([] satisfies RiskContributionRow[])
  }
  if (!rows.length || Math.abs(totalRiskShare) <= 1e-12) {
    return riskFail(
      `Production forward risk shares cannot be normalized for the ${eligibility === 'risk_budget' ? 'eligible risk-budget sleeve' : 'modeled risk sleeve'}; got ${formatPercent(totalRiskShare)}.`,
      [] satisfies RiskContributionRow[],
    )
  }
  if (eligibility === 'risk_budget') {
    rows.forEach((row) => {
      row.riskShare = (row.riskShare ?? 0) / totalRiskShare
    })
  } else if (Math.abs(totalRiskShare - 1) > 1e-6) {
    return riskFail(
      `Production forward risk shares must aggregate to 100% before taxonomy grouping; got ${formatPercent(totalRiskShare)}.`,
      [] satisfies RiskContributionRow[],
    )
  }
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

export function buildRiskBudgetEligibleNodeIds({
  catalog,
  taxonomy,
  referenceDate,
}: {
  catalog: PortfolioTaxonomyCatalogResponse | null
  taxonomy: PortfolioTaxonomyRecord | null
  referenceDate: string | null
}) {
  const eligibleNodeIds = new Set<string>()
  if (!catalog || !taxonomy || !referenceDate) {
    return eligibleNodeIds
  }
  const nodeById = buildNodeLookup(catalog, taxonomy.taxonomy_id)
  const policyByNodeId = new Map<string, PortfolioTaxonomyCatalogResponse['analytics_scope_policies'][number]>()
  catalog.analytics_scope_policies
    .filter(
      (policy) =>
        policy.taxonomy_id === taxonomy.taxonomy_id &&
        !policy.superseded_by_policy_id &&
        policy.effective_from <= referenceDate &&
        (!policy.effective_to || policy.effective_to >= referenceDate),
    )
    .sort((left, right) => left.policy_version - right.policy_version)
    .forEach((policy) => policyByNodeId.set(policy.taxonomy_node_id, policy))

  nodeById.forEach((node) => {
    let candidateNodeId: string | null = node.taxonomy_node_id
    const visited = new Set<string>()
    let policy: PortfolioTaxonomyCatalogResponse['analytics_scope_policies'][number] | null = null
    while (candidateNodeId && !visited.has(candidateNodeId)) {
      visited.add(candidateNodeId)
      policy = policyByNodeId.get(candidateNodeId) ?? null
      if (policy) {
        break
      }
      candidateNodeId = nodeById.get(candidateNodeId)?.parent_taxonomy_node_id ?? '__root__'
    }
    policy ??= policyByNodeId.get('__root__') ?? null
    if (!policy?.risk_budget_eligible) {
      return
    }
    buildNodePath(node.taxonomy_node_id, nodeById).forEach((pathNode) => {
      eligibleNodeIds.add(pathNode.taxonomy_node_id)
    })
  })
  return eligibleNodeIds
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

function accountLiquidityBase(accountRow: PortfolioAccountsWorkspaceResponse['accounts'][number]) {
  const cashBalanceBase = finiteNumber(accountRow.derived_cash_balance_base)
  const pendingSettlementBase = finiteNumber(accountRow.pending_settlement_base)
  if (cashBalanceBase == null || pendingSettlementBase == null) {
    return null
  }
  return cashBalanceBase + pendingSettlementBase
}

export function buildCurrentPlanningGroups({
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
    entityId,
    valueBase,
    weightInput,
  }: {
    entityId: string
    valueBase: number | null
    weightInput: number | null
  }) {
    const assignmentResult = resolveActiveAssignment(catalog, taxonomyId, 'instrument', entityId, currentReferenceDate)
    if (assignmentResult.error) {
      errors.push(assignmentResult.error)
      return
    }
    const assignment = assignmentResult.assignment
    const topLevelNode = resolveScopedTaxonomyNode(assignment?.taxonomy_node_id, nodeById, '')
    const groupKey = topLevelNode?.taxonomy_node_id ?? `unassigned:${taxonomyId}`
    const label = topLevelNode?.node_name ?? 'Unassigned'
    const current = groups.get(groupKey) ?? {
      groupKey,
      label,
      currentWeight: null,
      currentValueBase: null,
    }
    if (weightInput != null) {
      current.currentWeight = (current.currentWeight ?? 0) + weightInput
    }
    if (valueBase != null) {
      current.currentValueBase = (current.currentValueBase ?? 0) + valueBase
    }
    groups.set(groupKey, current)
  }

  function addSystemGroup({
    memberType,
    memberId,
    label,
    valueBase,
    totalValueBase,
  }: {
    memberType: 'cash_bucket' | 'derivative_bucket'
    memberId: string
    label: string
    valueBase: number
    totalValueBase: number
  }) {
    groups.set(targetMemberKey(memberType, memberId), {
      groupKey: targetMemberKey(memberType, memberId),
      label,
      currentWeight: totalValueBase > 1e-9 ? valueBase / totalValueBase : null,
      currentValueBase: valueBase,
    })
  }

  if (!accountsWorkspace) {
    return riskFail(
      'Current drift requires the accounts workspace so cash and pending settlement are included in portfolio NAV.',
      [] satisfies CurrentPlanningGroup[],
    )
  }
  const liquidityAccounts = accountsWorkspace.accounts.filter((accountRow) => {
    const liquidityBase = accountLiquidityBase(accountRow)
    if (liquidityBase == null) {
      errors.push(
        `Current drift requires base-currency cash and pending settlement for account ${accountRow.account.account_id}.`,
      )
      return false
    }
    return accountRow.account.account_type === 'deposit_account' || Math.abs(liquidityBase) > 1e-9
  })
  const securityHoldingRows = holdingsWorkspace.rows.filter(isSecurityHoldingRow)
  const derivativeHoldingRows = holdingsWorkspace.rows.filter((row) => row.holding_category === 'derivatives')
  const valuedHoldingRows = [...securityHoldingRows, ...derivativeHoldingRows]
  const missingHoldingValueRows = valuedHoldingRows.filter((row) => finiteNumber(row.market_value_base) == null)
  if (missingHoldingValueRows.length) {
    errors.push(
      `Current drift requires market_value_base for every holding; missing: ${missingHoldingValueRows
        .map((row) => holdingRiskLabel(row))
        .join(', ')}.`,
    )
  }
  const derivativeValueBase = derivativeHoldingRows.reduce(
    (total, row) => total + (finiteNumber(row.market_value_base) ?? 0),
    0,
  )
  const cashValueBase = liquidityAccounts.reduce(
    (total, accountRow) => total + (accountLiquidityBase(accountRow) ?? 0),
    0,
  )
  const totalValueBase =
    securityHoldingRows.reduce((total, row) => total + (finiteNumber(row.market_value_base) ?? 0), 0) +
    derivativeValueBase +
    cashValueBase
  if (totalValueBase <= 1e-9 && (valuedHoldingRows.length || liquidityAccounts.length)) {
    errors.push('Current drift requires positive portfolio NAV from holdings plus account cash and pending settlement.')
  }

  securityHoldingRows.forEach((row) => {
    const valueBase = finiteNumber(row.market_value_base)
    if (valueBase == null || totalValueBase <= 1e-9) {
      return
    }
    addEntity({
      entityId: row.instrument_core.instrument_id,
      valueBase,
      weightInput: valueBase / totalValueBase,
    })
  })

  if (totalValueBase > 1e-9) {
    addSystemGroup({
      memberType: 'derivative_bucket',
      memberId: SYSTEM_DERIVATIVE_TARGET_MEMBER_ID,
      label: 'Derivatives',
      valueBase: derivativeValueBase,
      totalValueBase,
    })
    addSystemGroup({
      memberType: 'cash_bucket',
      memberId: SYSTEM_CASH_TARGET_MEMBER_ID,
      label: 'Cash',
      valueBase: cashValueBase,
      totalValueBase,
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
  if (
    line.target_member_type === 'cash_bucket' &&
    line.target_member_id === SYSTEM_CASH_TARGET_MEMBER_ID
  ) {
    return 'Cash'
  }
  if (
    line.target_member_type === 'derivative_bucket' &&
    line.target_member_id === SYSTEM_DERIVATIVE_TARGET_MEMBER_ID
  ) {
    return 'Derivatives'
  }
  return `${formatLabel(line.target_member_type)} ${line.target_member_id}`
}

export function buildTargetGapRows({
  targetSet,
  targetLines,
  currentGroups,
  riskSharesByGroup,
  riskShareErrors = [],
  nodeById,
  riskBudgetEligibleNodeIds,
  dimension,
  baseCurrency,
}: {
  targetSet: PortfolioTargetSetRecord | null
  targetLines: PortfolioTargetSetLineRecord[]
  currentGroups: CurrentPlanningGroup[]
  riskSharesByGroup: Map<string, number | null>
  riskShareErrors?: string[]
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>
  riskBudgetEligibleNodeIds: ReadonlySet<string>
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
      ? targetLines.filter(
          (line) =>
            line.target_member_type === 'taxonomy_node' &&
            riskBudgetEligibleNodeIds.has(line.target_member_id),
        )
      : targetLines
  const eligibleCurrentGroups =
    dimension === 'risk_budget'
      ? currentGroups.filter((group) => riskSharesByGroup.has(group.groupKey))
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
  const unsupportedDirectLines = eligibleTargetLines.filter(
    (line) =>
      line.target_member_type !== 'taxonomy_node' &&
      line.target_member_type !== 'cash_bucket' &&
      line.target_member_type !== 'derivative_bucket',
  )
  if (unsupportedDirectLines.length) {
    return riskFail(
      `${targetSet.name} root target drift must use taxonomy nodes plus the fixed Derivatives and Cash members; unsupported direct members: ${unsupportedDirectLines
        .map((line) => `${line.target_member_type}:${line.target_member_id}`)
        .join(', ')}.`,
      [] satisfies TargetGapComparatorRow[],
    )
  }
  const missingTargetNodes = eligibleTargetLines.filter(
    (line) => line.target_member_type === 'taxonomy_node' && !nodeById.has(line.target_member_id),
  )
  if (missingTargetNodes.length) {
    return riskFail(
      `${targetSet.name} references missing taxonomy nodes: ${missingTargetNodes.map((line) => line.target_member_id).join(', ')}.`,
      [] satisfies TargetGapComparatorRow[],
    )
  }
  if (dimension === 'risk_budget' && riskShareErrors.length) {
    return riskFail(riskShareErrors, [] satisfies TargetGapComparatorRow[])
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
          : currentGroup
            ? null
            : 0
    if (dimension === 'risk_budget' && current == null && currentGroup) {
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
  const productionRiskDescription = `${windowLabel(productionRiskSettings.lookbackDays)} ${riskModelLabel(
    productionRiskSettings.modelId,
  )}; ${formatLabel(productionRiskSettings.contributionMode)} RC; ${formatLabel(
    productionRiskPolicy?.missing_return_policy ?? 'strict',
  )}`
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
  const benchmarkBasisAssessment = useMemo(
    () => benchmarkRiskBasisAssessment(benchmarkChart, holdingsWorkspace?.base_currency ?? ''),
    [benchmarkChart, holdingsWorkspace?.base_currency],
  )
  const benchmarkReturnPointsRaw = useMemo(
    () => (benchmarkBasisAssessment.blocking ? [] : buildBenchmarkReturnPoints(benchmarkChart)),
    [benchmarkBasisAssessment.blocking, benchmarkChart],
  )
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
      buildCanonicalTaxonomyRiskContributionRows({
        holdingsWorkspace,
        catalog: taxonomyCatalog,
        taxonomy: defaultPlanningTaxonomy,
        referenceDate: holdingsWorkspace?.as_of_date ?? null,
      }),
    [
      defaultPlanningTaxonomy,
      holdingsWorkspace?.as_of_date,
      holdingsWorkspace,
      taxonomyCatalog,
    ],
  )
  const topLevelRiskContributionRows = topLevelRiskContributionResult.value
  const topLevelRiskBudgetContributionResult = useMemo(
    () =>
      buildCanonicalTaxonomyRiskContributionRows({
        holdingsWorkspace,
        catalog: taxonomyCatalog,
        taxonomy: defaultPlanningTaxonomy,
        referenceDate: holdingsWorkspace?.as_of_date ?? null,
        eligibility: 'risk_budget',
      }),
    [
      defaultPlanningTaxonomy,
      holdingsWorkspace?.as_of_date,
      holdingsWorkspace,
      taxonomyCatalog,
    ],
  )
  const riskBudgetSharesByTopLevelGroup = useMemo(
    () =>
      new Map(
        topLevelRiskBudgetContributionResult.value.map(
          (row) => [row.groupKey, row.riskShare] as const,
        ),
      ),
    [topLevelRiskBudgetContributionResult.value],
  )
  const riskBudgetEligibleNodeIds = useMemo(
    () =>
      buildRiskBudgetEligibleNodeIds({
        catalog: taxonomyCatalog,
        taxonomy: defaultPlanningTaxonomy,
        referenceDate: holdingsWorkspace?.as_of_date ?? null,
      }),
    [defaultPlanningTaxonomy, holdingsWorkspace?.as_of_date, taxonomyCatalog],
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
        riskSharesByGroup: riskBudgetSharesByTopLevelGroup,
        nodeById: defaultTaxonomyNodeById,
        riskBudgetEligibleNodeIds,
        dimension: 'weight',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootSaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      portfolioBaseCurrency,
      riskBudgetEligibleNodeIds,
      riskBudgetSharesByTopLevelGroup,
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
        riskSharesByGroup: riskBudgetSharesByTopLevelGroup,
        nodeById: defaultTaxonomyNodeById,
        riskBudgetEligibleNodeIds,
        dimension: 'weight',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootTaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      portfolioBaseCurrency,
      riskBudgetEligibleNodeIds,
      riskBudgetSharesByTopLevelGroup,
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
        riskSharesByGroup: riskBudgetSharesByTopLevelGroup,
        riskShareErrors: topLevelRiskBudgetContributionResult.errors,
        nodeById: defaultTaxonomyNodeById,
        riskBudgetEligibleNodeIds,
        dimension: 'risk_budget',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootSaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      portfolioBaseCurrency,
      riskBudgetEligibleNodeIds,
      riskBudgetSharesByTopLevelGroup,
      topLevelRiskBudgetContributionResult.errors,
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
        riskSharesByGroup: riskBudgetSharesByTopLevelGroup,
        riskShareErrors: topLevelRiskBudgetContributionResult.errors,
        nodeById: defaultTaxonomyNodeById,
        riskBudgetEligibleNodeIds,
        dimension: 'risk_budget',
        baseCurrency: portfolioBaseCurrency,
      }),
    [
      activeRootTaaTargetSet,
      currentPlanningGroups,
      defaultTaxonomyNodeById,
      portfolioBaseCurrency,
      riskBudgetEligibleNodeIds,
      riskBudgetSharesByTopLevelGroup,
      topLevelRiskBudgetContributionResult.errors,
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
  const concentrationMetrics = useMemo(() => {
    const activeRows = (holdingsWorkspace?.rows ?? []).filter((row) => {
      if (!isRiskBearingHoldingRow(row)) {
        return false
      }
      return (
        Math.abs(finiteNumber(row.quantity) ?? 0) > 1e-9 ||
        Math.abs(finiteNumber(row.market_value_base) ?? 0) > 1e-9 ||
        Math.abs(finiteNumber(row.allocation) ?? 0) > 1e-9
      )
    })
    if (activeRows.some((row) => finiteNumber(row.allocation) == null)) {
      return { topThree: null as number | null, hhi: null as number | null }
    }
    const grossWeights = activeRows
      .map((row) => Math.abs(finiteNumber(row.allocation) ?? 0))
      .filter((weight) => weight > 1e-12)
      .sort((left, right) => right - left)
    const grossTotal = grossWeights.reduce((total, weight) => total + weight, 0)
    if (grossTotal <= 1e-12) {
      return { topThree: null as number | null, hhi: null as number | null }
    }
    const normalizedWeights = grossWeights.map((weight) => weight / grossTotal)
    return {
      topThree: normalizedWeights.slice(0, 3).reduce((total, weight) => total + weight, 0),
      hhi: normalizedWeights.reduce((total, weight) => total + weight * weight, 0),
    }
  }, [holdingsWorkspace?.rows])
  const saaTotalRiskGap =
    activeRootSaaTargetSet && !saaRiskGapResult.errors.length
      ? saaRiskGapRows.reduce((total, row) => total + Math.abs(row.gap ?? 0), 0)
      : null
  const taaTotalRiskGap =
    activeRootTaaTargetSet && !taaRiskGapResult.errors.length
      ? taaRiskGapRows.reduce((total, row) => total + Math.abs(row.gap ?? 0), 0)
      : null
  const riskGapSummary = [
    ...(saaTotalRiskGap != null ? [`SAA ${formatPercent(saaTotalRiskGap)}`] : []),
    ...(taaTotalRiskGap != null ? [`TAA ${formatPercent(taaTotalRiskGap)}`] : []),
  ].join(' · ')
  const forwardRiskCoverage = holdingsWorkspace?.forward_risk?.coverage ?? null
  const forwardRiskCoverageLabel = forwardRiskCoverage
    ? `${forwardRiskCoverage.rows_after}/${forwardRiskCoverage.rows_before} complete return rows in ${
        forwardRiskCoverage.return_interval ??
        `(${forwardRiskCoverage.window_start_date} EOD, ${forwardRiskCoverage.window_end_date} EOD]`
      }; ${formatPercent(
        forwardRiskCoverage.missing_row_fraction,
      )} missing; latest ${forwardRiskCoverage.latest_complete_date ?? '—'}`
    : 'Coverage unavailable'
  const analyticsScope = holdingsWorkspace?.analytics_scope_summary ?? null
  const excludedExposure = analyticsScope
    ? analyticsScope.excluded_carrying_value + analyticsScope.excluded_liability
    : null
  const analyticsVersionLabel = analyticsScope
    ? `Policy ${analyticsScope.scope_policy_versions.join(', ') || 'none'}; configuration ${
        analyticsScope.configuration_versions.join(', ') || 'none'
      }; selection ${analyticsScope.taxonomy_selection_versions.join(', ') || 'none'}`
    : 'Analytics scope identity unavailable'
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
      return (
        <div className="risk-chart-empty risk-chart-empty-error">
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
            <section className="performance-section-block" aria-label="Risk health">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar risk-section-toolbar">
                <div>
                  <div className="panel-title">Risk Health</div>
                  <div className="portfolio-detail-meta">
                    {holdingsWorkspace.as_of_date}; {portfolioRiskFrequency.statusLabel}; {productionRiskMeta}
                  </div>
                </div>
              </div>
              <div className="portfolio-summary-strip risk-health-strip">
                <article
                  className={
                    holdingsWorkspace.forward_risk?.status === 'ok'
                      ? 'summary-card'
                      : 'summary-card summary-card-warning'
                  }
                >
                  <span className="summary-card-label">Modeled Market Sleeve Volatility</span>
                  <strong className="summary-card-value">
                    {holdingsWorkspace.forward_risk?.status === 'ok' &&
                    holdingsWorkspace.forward_risk.portfolio_volatility != null
                      ? formatPercent(holdingsWorkspace.forward_risk.portfolio_volatility)
                      : 'Unavailable'}
                  </strong>
                  <span className="portfolio-detail-meta">{forwardRiskCoverageLabel}</span>
                </article>
                <article className="summary-card">
                  <span className="summary-card-label">Model Coverage</span>
                  <strong className="summary-card-value">
                    {analyticsScope?.coverage_ratio != null
                      ? formatPercent(analyticsScope.coverage_ratio)
                      : 'Unavailable'}
                  </strong>
                  <span className="portfolio-detail-meta">Eligible gross exposure / disclosed exposure</span>
                </article>
                <article className="summary-card">
                  <span className="summary-card-label">Eligible Risk Budget Gap</span>
                  <strong className="summary-card-value">
                    {riskGapSummary || '—'}
                  </strong>
                  <span className="portfolio-detail-meta">
                    {riskGapSummary ? 'Sum of absolute sleeve gaps' : 'Comparator unavailable'}
                  </span>
                </article>
                <article className="summary-card">
                  <span className="summary-card-label">Modeled Gross Exposure</span>
                  <strong className="summary-card-value">
                    {analyticsScope
                      ? formatCurrency(analyticsScope.modeled_gross_exposure, holdingsWorkspace.base_currency)
                      : '—'}
                  </strong>
                  <span className="portfolio-detail-meta">
                    {analyticsScope
                      ? `Net ${formatCurrency(analyticsScope.modeled_net_exposure, holdingsWorkspace.base_currency)}`
                      : 'Modeled exposure unavailable'}
                  </span>
                </article>
                <article className="summary-card">
                  <span className="summary-card-label">Excluded Exposure</span>
                  <strong className="summary-card-value">
                    {excludedExposure != null
                      ? formatCurrency(excludedExposure, holdingsWorkspace.base_currency)
                      : '—'}
                  </strong>
                  <span className="portfolio-detail-meta">
                    {analyticsScope
                      ? `Assets ${formatCurrency(analyticsScope.excluded_carrying_value, holdingsWorkspace.base_currency)}; liabilities ${formatCurrency(
                          analyticsScope.excluded_liability,
                          holdingsWorkspace.base_currency,
                        )}`
                      : 'Excluded exposure unavailable'}
                  </span>
                </article>
                <article className="summary-card">
                  <span className="summary-card-label">Cash / Unallocated</span>
                  <strong className="summary-card-value">
                    {analyticsScope
                      ? formatCurrency(analyticsScope.cash_unallocated_exposure, holdingsWorkspace.base_currency)
                      : '—'}
                  </strong>
                  <span className="portfolio-detail-meta">Disclosed outside covariance risk</span>
                </article>
                <article className="summary-card">
                  <span className="summary-card-label">Top 3 Modeled Concentration</span>
                  <strong className="summary-card-value">
                    {concentrationMetrics.topThree != null ? formatPercent(concentrationMetrics.topThree) : '—'}
                  </strong>
                  <span className="portfolio-detail-meta">
                    {concentrationMetrics.hhi != null
                      ? `Eligible-sleeve normalized HHI ${formatNumber(concentrationMetrics.hhi, 3)}`
                      : 'Eligible exposure unavailable'}
                  </span>
                </article>
              </div>
              {renderRiskErrors(holdingsWorkspace.forward_risk?.errors ?? [])}
              <div className="portfolio-detail-meta risk-scope-identity">{analyticsVersionLabel}</div>
              {analyticsScope?.excluded_rows.length ? (
                <div className="table-shell risk-scope-table-shell">
                  <table className="transactions-table risk-scope-table">
                    <thead>
                      <tr>
                        <th>Excluded Holding</th>
                        <th>Category</th>
                        <th>Reason</th>
                        <th className="performance-cell-number">Exposure</th>
                      </tr>
                    </thead>
                    <tbody>
                      {analyticsScope.excluded_rows.map((row) => (
                        <tr key={row.line_id ?? `${row.instrument_id}:${row.holding_category}`}>
                          <td>{row.instrument_name ?? row.instrument_id ?? row.line_id ?? 'N/A'}</td>
                          <td>{formatLabel(row.holding_category)}</td>
                          <td>{row.exclusion_reason ?? 'No exclusion reason recorded.'}</td>
                          <td className="performance-cell-number">
                            {formatCurrency(row.exposure_base, holdingsWorkspace.base_currency)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
              <div className="portfolio-detail-toolbar performance-subsection-toolbar risk-section-toolbar risk-scoped-contribution-toolbar">
                <div>
                  <div className="panel-title">Scoped Risk Contribution</div>
                  <div className="portfolio-detail-meta">Shares sum to 100% inside the eligible modeled sleeve.</div>
                </div>
              </div>
              {renderRiskErrors(topLevelRiskContributionResult.errors)}
              {topLevelRiskContributionRows.length ? (
                <div className="table-shell risk-scope-table-shell">
                  <table className="transactions-table risk-scope-table">
                    <thead>
                      <tr>
                        <th>Sleeve</th>
                        <th className="performance-cell-number">Eligible Weight</th>
                        <th className="performance-cell-number">Risk Share</th>
                        <th className="performance-cell-number">Observations</th>
                      </tr>
                    </thead>
                    <tbody>
                      {topLevelRiskContributionRows.map((row) => (
                        <tr key={row.groupKey}>
                          <td>{row.groupLabel}</td>
                          <td className="performance-cell-number">{formatPercent(row.weight)}</td>
                          <td className="performance-cell-number">{formatPercent(row.riskShare)}</td>
                          <td className="performance-cell-number">{formatNumber(row.observationCount, 0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </section>

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
              {benchmarkBasisAssessment.message ? (
                <div
                  className={
                    benchmarkBasisAssessment.blocking
                      ? 'inline-notice inline-notice-error'
                      : 'inline-notice'
                  }
                >
                  {benchmarkBasisAssessment.message}
                </div>
              ) : null}
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
