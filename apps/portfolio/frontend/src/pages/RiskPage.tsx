import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useLocation, useParams, useSearchParams } from 'react-router'
import { useLanguage } from '../../../../../packages/ui/src/i18n'

import BenchmarkSearchBox, {
  benchmarkInstrumentLabel,
  instrumentPrimaryIdentifier,
} from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import ConcentrationPanel from '../components/ConcentrationPanel'
import usePerformanceResource from '../hooks/usePerformanceResource'
import PortfolioTailRiskPanel from '../components/PortfolioTailRiskPanel'
import InfoHint from '../components/InfoHint'
import RiskWindowDiagnostics from '../components/RiskWindowDiagnostics'
import RiskSourceCoverage from '../components/RiskSourceCoverage'
import RollingRiskMetricChart, {
  type RiskChartDisplayStyle,
} from '../components/RollingRiskMetricChart'
import RiskTargetGapChart, { type RiskTargetGapChartRow } from '../components/RiskTargetGapChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import QualityWarningsNotice from '../components/QualityWarningsNotice'
import {
  getHoldingsWorkspace,
  getPortfolioAccountsWorkspace,
  getPortfolioPerformance,
  getPortfolioInstrumentPriceChart,
  getPortfolioInstruments,
  getPortfolioTaxonomyCatalog,
  type HoldingsWorkspaceResponse,
  type InstrumentCore,
  type PortfolioAccountsWorkspaceResponse,
  type PortfolioPerformanceResponse,
  type PortfolioInstrumentPriceChartResponse,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioResolvedMemberTarget,
  type PortfolioTargetMemberType,
  type SharedInstrumentRecord,
  type TaxonomyAssignmentScope,
} from '../lib/api'
import { formatCurrency, formatLabel, formatNumber, formatPercent } from '../lib/format'
import { assessPerformanceBenchmarkBasis } from '../lib/performanceBenchmarkBasis'
import {
  alignReturnSeriesToFrequency,
  commonReturnDateKeys,
  type CalculationFrequency,
  type GroupReturnSeries,
  type ReturnPoint,
  type ReturnObservationCoverage,
} from '../lib/riskReturnAlignment'
import {
  buildCorrelationMatrix,
  correlationCoverageIssue,
  type CorrelationMatrix,
  type CorrelationMatrixCoverageIssue,
  type CorrelationMatrixScope,
} from '../lib/riskCorrelation'
import {
  windowLabel,
} from '../lib/riskWindowCoverage'
import { buildRollingRisk, realizedRiskSeries, benchmarkRiskSeries } from '../lib/rollingRisk'
import { windowIssue } from '../lib/riskWindowData'

const DEFAULT_RISK_LOOKBACK_DAYS = 90
const DEFAULT_RISK_ANALYTICS_LOOKBACK_DAYS = 30
const DEFAULT_RISK_MODEL_ID = 'ewma_vol_shrinkage_corr_covariance'
const MATRIX_SCOPE_CURRENT_HOLDINGS = '__current_holdings__'
const MATRIX_SCOPE_FULL_UNIVERSE = '__full_universe__'
const RISK_PAGE_SETTINGS_STORAGE_KEY = 'investment_studio.portfolio.risk.settings.v1'
const SYSTEM_CASH_TARGET_MEMBER_ID = '__cash__'
const SYSTEM_DERIVATIVE_TARGET_MEMBER_ID = '__derivatives__'

type RiskModelId = 'ewma_vol_shrinkage_corr_covariance' | 'ewma_covariance' | 'sample_covariance'
type RiskContributionMode = 'signed' | 'abs'


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
  const frequency = holdingsWorkspace.risk_basis?.resolved_frequency
  if (!isCalculationFrequency(frequency)) {
    return riskFail(`Risk basis response has invalid calculation frequency: ${frequency || 'missing'}.`, unavailableProfile)
  }
  return riskOk({
    frequency,
    statusLabel: `${CALCULATION_FREQUENCY_LABELS[frequency]} risk basis`,
  } satisfies RiskFrequencyProfile)
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
  const basis = assessPerformanceBenchmarkBasis(chart.chart_basis, chart.return_semantics)
  return { blocking: !basis.basis, message: basis.warning }
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

function isActiveRiskBearingHoldingRow(row: HoldingsRow): row is MarketInstrumentHoldingRow {
  return isRiskBearingHoldingRow(row) && (
    Math.abs(finiteNumber(row.quantity) ?? 0) > 1e-9 ||
    Math.abs(finiteNumber(row.allocation) ?? 0) > 1e-9 ||
    Math.abs(finiteNumber(row.market_value_base) ?? 0) > 1e-9
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

function missingGapCoverageReason(profile: HoldingsWorkspaceResponse['risk_basis'] | null, memberKey: string, coverage: ReturnObservationCoverage | undefined) {
  return !coverage && profile?.gap_instrument_ids?.includes(memberKey)
    ? 'Complete source-gap dates are missing for this member; its selected window cannot be verified.' : null
}

function returnPointsToGroupSeries({
  groupKey,
  groupLabel,
  returnPoints,
  asOfDate,
  latestWeight,
  observationCoverage,
}: {
  groupKey: string
  groupLabel: string
  returnPoints: ReturnPoint[]
  asOfDate: string
  latestWeight: number
  observationCoverage?: ReturnObservationCoverage
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
    inputPoints: returnPoints,
    observationCoverage,
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
        `Current risk cannot treat unmodeled market exposure ${holdingRiskLabel(row)} as zero risk.`,
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
      const gapCoverageReason = missingGapCoverageReason(holdingsWorkspace.risk_basis, row.instrument_core.instrument_id, row.instrument_return_series_all?.observation_coverage)
      if (gapCoverageReason) errors.push(`${label}: ${gapCoverageReason}`)
      const series = returnPointsToGroupSeries({
        groupKey: row.instrument_core.instrument_id,
        groupLabel: label,
        returnPoints: row.instrument_return_series_all?.points ?? [],
        asOfDate,
        latestWeight: currentWeight,
        observationCoverage: row.instrument_return_series_all?.observation_coverage,
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

  if (!series.some((item) => Math.abs(item.latestWeight ?? 0) > 1e-9)) {
    errors.push('Current risk requires at least one modeled market asset.')
  }

  return errors.length ? riskFail(errors, [] satisfies GroupReturnSeries[]) : riskOk(series)
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
  const rows = holdingsWorkspace.rows.filter(isActiveRiskBearingHoldingRow)
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
    const gapCoverageReason = missingGapCoverageReason(holdingsWorkspace.risk_basis, memberKey, row.instrument_return_series_all?.observation_coverage)
    if (gapCoverageReason) issues.push(windowIssue(memberKey, memberLabel, 'missing_series', gapCoverageReason))
    const memberSeries = returnPointsToGroupSeries({
      groupKey: memberKey,
      groupLabel: memberLabel,
      returnPoints,
      asOfDate,
      latestWeight: currentWeight ?? 0,
      observationCoverage: row.instrument_return_series_all?.observation_coverage,
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
      const gapCoverageReason = missingGapCoverageReason(catalog.risk_basis, memberKey, record.instrument_return_series_all?.observation_coverage)
      if (gapCoverageReason) issues.push(windowIssue(memberKey, memberLabel, 'missing_series', gapCoverageReason))
      const memberSeries = returnPointsToGroupSeries({
        groupKey: memberKey,
        groupLabel: memberLabel,
        returnPoints,
        asOfDate: matrixAsOfDate,
        latestWeight: currentWeightByInstrumentId.get(memberKey) ?? 0,
        observationCoverage: record.instrument_return_series_all?.observation_coverage,
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
}: {
  holdingsWorkspace: HoldingsWorkspaceResponse | null
  catalog: PortfolioTaxonomyCatalogResponse | null
  taxonomy: PortfolioTaxonomyRecord | null
  referenceDate: string | null
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
  if (!rows.length || Math.abs(totalRiskShare) <= 1e-12) {
    return riskFail(
      `Production forward risk shares cannot be normalized for the modeled risk sleeve; got ${formatPercent(totalRiskShare)}.`,
      [] satisfies RiskContributionRow[],
    )
  }
  if (Math.abs(totalRiskShare - 1) > 1e-6) {
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
    return accountRow.account.account_category === 'cash' || Math.abs(liquidityBase) > 1e-9
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

function targetMemberKey(memberType: PortfolioTargetMemberType, memberId: string) {
  return `${memberType}:${memberId}`
}

export function riskTargetComparisonErrors(
  workspace: HoldingsWorkspaceResponse | null,
  contributions: RiskContributionRow[],
  taxonomyId?: string,
) {
  const errors: string[] = []
  if (taxonomyId && contributions.some((row) => row.groupKey === `unassigned:${taxonomyId}`)) {
    errors.push('Portfolio risk targets cannot be compared while modeled holdings remain unclassified. The classified subset is not renormalized.')
  }
  if (workspace?.rows.some((row) => isSecurityHoldingRow(row) && row.risk_eligible !== true && (
    Math.abs(finiteNumber(row.quantity) ?? 0) > 1e-9 ||
    Math.abs(finiteNumber(row.market_value_base) ?? 0) > 1e-9 ||
    Math.abs(finiteNumber(row.allocation) ?? 0) > 1e-9
  ))) {
    errors.push('Portfolio risk targets cannot be compared while held securities are outside the production risk model. Missing risk is not zero.')
  }
  return errors
}

export function buildTargetGapRows({
  resolvedTargets, stage, currentGroups, riskSharesByGroup, riskShareErrors = [], nodeById, baseCurrency,
}: {
  resolvedTargets: PortfolioResolvedMemberTarget[]
  stage: 'saa' | 'taa'
  currentGroups: CurrentPlanningGroup[]
  riskSharesByGroup: Map<string, number | null>
  riskShareErrors?: string[]
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>
  baseCurrency: string
}) {
  const targets = resolvedTargets.filter((row) => row.scope_node_id == null && row.member_type === 'taxonomy_node'
    && (stage === 'saa' ? row.strategic_global_risk_target : row.tactical_global_risk_target) != null)
  if (!targets.length) return riskOk([] satisfies TargetGapComparatorRow[])
  if (riskShareErrors.length) return riskFail(riskShareErrors, [] satisfies TargetGapComparatorRow[])
  const currentByKey = new Map(currentGroups.map((group) => [group.groupKey, group]))
  const rows: TargetGapComparatorRow[] = []
  const errors: string[] = []
  for (const row of targets) {
    const key = row.member_id
    const group = currentByKey.get(key)
    const target = stage === 'saa' ? row.strategic_global_risk_target! : row.tactical_global_risk_target!
    const current = riskSharesByGroup.has(key) ? riskSharesByGroup.get(key) : group ? null : 0
    const label = nodeById.get(key)?.node_name ?? group?.label ?? key
    if (current == null) { errors.push(`${stage.toUpperCase()} risk target gap is missing current risk share for ${label}.`); continue }
    if (Math.abs(current) <= 1e-12 && Math.abs(target) <= 1e-12) continue
    rows.push({ key, id: `${stage}:${key}`, label, current, target, gap: current - target,
      detail: group?.currentValueBase != null && baseCurrency ? formatCurrency(group.currentValueBase, baseCurrency) : undefined })
  }
  return errors.length ? riskFail(errors, [] satisfies TargetGapComparatorRow[]) : riskOk(rows.sort((a, b) => Math.abs(b.gap ?? 0) - Math.abs(a.gap ?? 0)))
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

function RiskDateTimeline({ dates, value, onChange, label }: {
  dates: string[]; value: string; onChange: (value: string) => void; label: string
}) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  if (!dates.length) return null
  const invalid = Boolean(draft && !dates.includes(draft))
  return <div className="risk-date-selector">
    <label>
      <span>{zh ? '历史观察截止日' : 'Historical observation cutoff'}</span>
      <input type="date" value={draft} min={dates[0]} max={dates[dates.length - 1]} aria-label={label} aria-invalid={invalid}
        onChange={(event) => { const next = event.target.value; setDraft(next); if (dates.includes(next)) onChange(next) }} />
    </label>
    <button type="button" className="secondary-button" onClick={() => { const latest = dates[dates.length - 1]; setDraft(latest); onChange(latest) }}>{zh ? '最新' : 'Latest'}</button>
    {invalid ? <span role="status">{zh ? '该日没有收益观察，请选择范围内已有观察的日期。' : 'No return observation exists on this date. Choose an observed date within the available range.'}</span> : null}
  </div>
}

export default function RiskPage() {
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const concentrationDate = searchParams.get('concentration_date') || undefined
  const { hash } = useLocation()
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [holdingsWorkspace, setHoldingsWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [accountsWorkspace, setAccountsWorkspace] = useState<PortfolioAccountsWorkspaceResponse | null>(null)
  const [taxonomyCatalog, setTaxonomyCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const targetTaxonomyStorageKey = `investment_studio.portfolio.risk.target-taxonomy.${portfolioId}`
  const [targetTaxonomyId, setTargetTaxonomyId] = useState<string | null>(() => {
    try { return localStorage.getItem(targetTaxonomyStorageKey) } catch { return null }
  })
  useEffect(() => {
    try { setTargetTaxonomyId(localStorage.getItem(targetTaxonomyStorageKey)) } catch { setTargetTaxonomyId(null) }
  }, [targetTaxonomyStorageKey])
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
  const [rollingBasis, setRollingBasis] = useState<'realized' | 'current'>('realized')
  const [realizedPerformance, setRealizedPerformance] = useState<PortfolioPerformanceResponse | null>(null)
  const [realizedLoading, setRealizedLoading] = useState(false)
  const [realizedError, setRealizedError] = useState<string | null>(null)
  const rollingLoading = rollingBasis === 'realized' && realizedLoading
  const rollingError = rollingBasis === 'realized' ? realizedError : null
  const [rollingSettings, setRollingSettings] = useState<RollingRiskSettingsState>(() => loadRiskPageSettings().rolling)
  const [matrixSettings, setMatrixSettings] = useState<RiskWindowSettingsState>(() => loadRiskPageSettings().matrix)
  const [matrixScopeNodeId, setMatrixScopeNodeId] = useState(MATRIX_SCOPE_CURRENT_HOLDINGS)
  const [matrixAsOfDate, setMatrixAsOfDate] = useState('')

  useEffect(() => {
    if (holdingsWorkspace && hash === '#concentration') document.getElementById('concentration')?.scrollIntoView({ block: 'start' })
  }, [Boolean(holdingsWorkspace), portfolioId, hash])

  const riskWindowEndDate = holdingsWorkspace?.portfolio_id === portfolioId ? holdingsWorkspace.as_of_date : ''
  const matrixUsesCurrentHoldings = matrixScopeNodeId === MATRIX_SCOPE_CURRENT_HOLDINGS
  const matrixUsesFullUniverse = matrixScopeNodeId === MATRIX_SCOPE_FULL_UNIVERSE
  const matrixUsesTaxonomy = !matrixUsesCurrentHoldings && !matrixUsesFullUniverse
  const loadFullUniverseCatalog = useCallback(
    (signal: AbortSignal) => getPortfolioTaxonomyCatalog(portfolioId ?? '', {
      include_market_profile: true, as_of_date: riskWindowEndDate,
    }, signal),
    [portfolioId, riskWindowEndDate, riskPolicyRevision],
  )
  const { data: fullUniverseCatalog, error: fullUniverseError } = usePerformanceResource({
    enabled: Boolean(portfolioId && riskWindowEndDate && matrixUsesFullUniverse),
    resourceKey: `${portfolioId}:${riskWindowEndDate}:${riskPolicyRevision}`,
    load: loadFullUniverseCatalog,
    fallbackError: 'Failed to load Full Universe history.',
  })
  const fullUniverseLoading = matrixUsesFullUniverse && !fullUniverseCatalog && !fullUniverseError

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
    const controller = new AbortController()
    setWorkspaceLoading(true)
    setWorkspaceError(null)
    setWorkspaceSupportError(null)
    setHoldingsWorkspace(null)
    setAccountsWorkspace(null)
    setTaxonomyCatalog(null)

    getHoldingsWorkspace(portfolioId, { include_details: true }, controller.signal)
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

    getPortfolioAccountsWorkspace(portfolioId, undefined, controller.signal)
      .then((response) => { if (!cancelled) setAccountsWorkspace(response) })
      .catch((error) => {
        if (!cancelled) setWorkspaceSupportError((current) => [current, error instanceof Error ? error.message : 'Failed to load accounts workspace.'].filter(Boolean).join(' '))
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [portfolioId, riskPolicyRevision])

  useEffect(() => {
    if (!portfolioId) {
      setBenchmarkInstruments([])
      return
    }

    let cancelled = false
    const controller = new AbortController()
    getPortfolioInstruments(portfolioId, controller.signal)
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
      controller.abort()
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
    const controller = new AbortController()
    setBenchmarkChart(null)
    setBenchmarkLoading(true)
    setBenchmarkError(null)

    getPortfolioInstrumentPriceChart(portfolioId, benchmarkInstrumentId, {
      as_of_date: riskWindowEndDate,
      range: 'all',
    }, controller.signal)
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
      controller.abort()
    }
  }, [benchmarkInstrumentId, portfolioId, riskWindowEndDate])

  useEffect(() => {
    if (!portfolioId) { setTaxonomyCatalog(null); return }
    let cancelled = false
    const controller = new AbortController()
    getPortfolioTaxonomyCatalog(portfolioId, {}, controller.signal)
      .then((response) => { if (!cancelled) setTaxonomyCatalog(response) })
      .catch((error) => {
        if (!cancelled) setWorkspaceSupportError((current) => [current, error instanceof Error ? error.message : 'Failed to load taxonomy catalog.'].filter(Boolean).join(' '))
      })
    return () => { cancelled = true; controller.abort() }
  }, [portfolioId, riskPolicyRevision])

  useEffect(() => {
    setRealizedPerformance(null)
    setRealizedError(null)
    if (!portfolioId || !riskWindowEndDate) { setRealizedLoading(false); return }
    let cancelled = false
    const controller = new AbortController()
    setRealizedLoading(true)
    getPortfolioPerformance(portfolioId, { end_date: riskWindowEndDate }, controller.signal)
      .then((response) => { if (!cancelled) setRealizedPerformance(response) })
      .catch((error) => { if (!cancelled) setRealizedError(error instanceof Error ? error.message : 'Failed to load actual portfolio returns.') })
      .finally(() => { if (!cancelled) setRealizedLoading(false) })
    return () => { cancelled = true; controller.abort() }
  }, [portfolioId, riskWindowEndDate, riskPolicyRevision])

  const targetTaxonomies = taxonomyCatalog?.portfolio_id === portfolioId
    ? taxonomyCatalog.taxonomies.filter((taxonomy) => taxonomy.status === 'active') : []
  const targetTaxonomy = targetTaxonomies.find((taxonomy) => taxonomy.taxonomy_id === targetTaxonomyId) ?? null
  const soleTargetTaxonomyId = targetTaxonomies.length === 1 ? targetTaxonomies[0].taxonomy_id : null
  useEffect(() => {
    if (targetTaxonomyId !== null || !soleTargetTaxonomyId) return
    setTargetTaxonomyId(soleTargetTaxonomyId)
    try { localStorage.setItem(targetTaxonomyStorageKey, soleTargetTaxonomyId) } catch { /* View selection is optional persistence. */ }
  }, [soleTargetTaxonomyId, targetTaxonomyId, targetTaxonomyStorageKey])
  const matrixTaxonomy = targetTaxonomy
  const targetTaxonomyNodeById = useMemo(() => buildNodeLookup(taxonomyCatalog, targetTaxonomy?.taxonomy_id), [targetTaxonomy?.taxonomy_id, taxonomyCatalog])
  const targetResolution = taxonomyCatalog?.target_resolution?.find((item) => item.taxonomy_id === targetTaxonomy?.taxonomy_id)

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
  const fullUniverseRiskFrequency = useMemo(() => {
    const frequency = fullUniverseCatalog?.risk_basis?.resolved_frequency
    if (isCalculationFrequency(frequency)) {
      return {
        frequency,
        statusLabel:
          fullUniverseCatalog?.risk_basis?.status_label ||
          `${CALCULATION_FREQUENCY_LABELS[frequency]} risk basis`,
      } satisfies RiskFrequencyProfile
    }
    return portfolioRiskFrequency
  }, [
    portfolioRiskFrequency,
    fullUniverseCatalog?.risk_basis?.resolved_frequency,
    fullUniverseCatalog?.risk_basis?.status_label,
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
        buildFullUniverseMatrixScope({ holdingsWorkspace, catalog: fullUniverseCatalog }),
        fullUniverseRiskFrequency.frequency,
        riskBasisFinalDate,
      ),
    [
      fullUniverseRiskFrequency.frequency,
      holdingsWorkspace,
      riskBasisFinalDate,
      fullUniverseCatalog,
    ],
  )
  const currentBenchmarkChart = benchmarkChart?.portfolio_id === portfolioId && benchmarkChart.as_of_date === riskWindowEndDate && benchmarkChart.instrument_core.instrument_id === benchmarkInstrumentId ? benchmarkChart : null
  const benchmarkBasisAssessment = useMemo(
    () => benchmarkRiskBasisAssessment(currentBenchmarkChart, holdingsWorkspace?.base_currency ?? ''),
    [currentBenchmarkChart, holdingsWorkspace?.base_currency],
  )
  const selectedRollingSeries = useMemo(
    () => rollingBasis === 'realized' ? realizedRiskSeries(realizedPerformance) : rawInstrumentReturnSeries,
    [rollingBasis, realizedPerformance, rawInstrumentReturnSeries],
  )
  const rollingRiskResult = useMemo(() => {
    const messages = rollingBasis === 'current' ? currentRiskInputErrors : [
      ...(realizedError ? [realizedError] : []),
      ...(realizedPerformance && realizedPerformance.summary.risk_metric_basis !== 'market_risk_return' ? ['Actual rolling risk requires the canonical market-risk return basis.'] : []),
      ...(realizedPerformance && realizedPerformance.summary.risk_calculation_frequency !== 'daily' ? ['Actual rolling risk requires daily market-risk observations.'] : []),
      ...(realizedPerformance && realizedPerformance.base_currency !== holdingsWorkspace?.base_currency ? ['Actual portfolio returns do not match the portfolio base currency.'] : []),
    ]
    return buildRollingRisk({
      series: selectedRollingSeries, asOfDate: riskBasisFinalDate, lookbackDays: rollingSettings.lookbackDays,
      inputIssues: messages.map((message) => windowIssue('portfolio', 'Portfolio', 'scope_unavailable', message)),
      performance: rollingBasis === 'realized' ? realizedPerformance : undefined,
    })
  }, [selectedRollingSeries, riskBasisFinalDate, rollingSettings.lookbackDays, rollingBasis, realizedPerformance, realizedError, currentRiskInputErrors, holdingsWorkspace?.base_currency])
  const rollingRiskDiagnostics = rollingRiskResult.diagnostics
  const rollingVolatilityPoints = rollingRiskResult.volatilityPoints
  const rollingSharpePoints = rollingRiskResult.sharpePoints
  const benchmarkRollingRisk = useMemo(() => buildRollingRisk({
    series: benchmarkRiskSeries(benchmarkBasisAssessment.blocking ? [] : buildBenchmarkReturnPoints(currentBenchmarkChart)),
    asOfDate: riskBasisFinalDate, lookbackDays: rollingSettings.lookbackDays,
  }), [currentBenchmarkChart, benchmarkBasisAssessment.blocking, riskBasisFinalDate, rollingSettings.lookbackDays])
  const benchmarkRollingVolatilityPoints = benchmarkRollingRisk.volatilityPoints
  const benchmarkRollingSharpePoints = benchmarkRollingRisk.sharpePoints

  const matrixTaxonomyScopeOptions = useMemo(
    () => taxonomyScopeOptions(matrixTaxonomy, taxonomyCatalog),
    [matrixTaxonomy, taxonomyCatalog],
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
  const matrixScopeIsLeaf = Boolean(matrixTaxonomyScopeNodeId && matrixTaxonomy &&
    !(taxonomyCatalog?.taxonomy_nodes ?? []).some((node) => node.taxonomy_id === matrixTaxonomy.taxonomy_id && node.parent_taxonomy_node_id === matrixTaxonomyScopeNodeId))
  const scopedTaxonomyInstruments = useMemo<CorrelationMatrixScope>(() => {
    if (!matrixTaxonomyScopeNodeId || !matrixTaxonomy || !taxonomyCatalog) return currentHoldingsMatrixScope
    const nodeById = buildNodeLookup(taxonomyCatalog, matrixTaxonomy.taxonomy_id)
    const selected = new Set<string>()
    const selectionIssues: CorrelationMatrixCoverageIssue[] = []
    for (const row of holdingsWorkspace?.rows ?? []) {
      if (!isActiveRiskBearingHoldingRow(row)) continue
      const memberKey = row.instrument_core.instrument_id
      const matches = taxonomyCatalog.taxonomy_assignments.filter((assignment) => assignment.taxonomy_id === matrixTaxonomy.taxonomy_id && assignment.target_scope === 'instrument' && assignment.target_entity_id === memberKey && assignment.status === 'active')
      if (!matches.some((assignment) => resolveScopedTaxonomyNode(assignment.taxonomy_node_id, nodeById, matrixTaxonomyScopeNodeId))) continue
      selected.add(memberKey)
      if (matches.length > 1) selectionIssues.push(windowIssue(memberKey, holdingRiskLabel(row), 'scope_unavailable', 'The selected member has multiple active taxonomy assignments.'))
    }
    return { memberCount: selected.size,
      series: currentHoldingsMatrixScope.series.filter((item) => selected.has(item.groupKey)),
      issues: [...currentHoldingsMatrixScope.issues.filter((issue) => selected.has(issue.memberKey) || issue.memberKey === 'current-holdings'), ...selectionIssues],
    }
  }, [currentHoldingsMatrixScope, matrixTaxonomy, holdingsWorkspace?.rows, matrixTaxonomyScopeNodeId, taxonomyCatalog])
  const selectedMatrixScopeDescription = matrixUsesTaxonomy && !matrixScopeIsLeaf
    ? (zh ? '比较所选分类下各直接子分类的当前权重篮子。' : 'Compare current-weight baskets for the direct child classifications.')
    : (zh ? '比较所选范围内各资产的本位币收益。' : 'Compare base-currency returns of the assets in the selected scope.')
  const matrixTaxonomySeriesResult = useMemo(
    () =>
      !matrixUsesTaxonomy
        ? riskOk([] satisfies GroupReturnSeries[])
        : scopedTaxonomyInstruments.issues.length
          ? riskFail(
              scopedTaxonomyInstruments.issues.map(
                (issue) => `${issue.memberLabel}: ${issue.coverageReason}`,
              ),
              [] satisfies GroupReturnSeries[],
            )
        : matrixScopeIsLeaf
          ? riskOk(scopedTaxonomyInstruments.series)
        : buildCurrentTaxonomyReturnSeries({
            instrumentSeries: scopedTaxonomyInstruments.series,
            catalog: taxonomyCatalog,
            taxonomy: matrixTaxonomy,
            scopeNodeId: matrixTaxonomyScopeNodeId,
            referenceDate: holdingsWorkspace?.as_of_date ?? null,
          }),
    [
      matrixTaxonomy,
      holdingsWorkspace?.as_of_date,
      scopedTaxonomyInstruments,
      matrixTaxonomyScopeNodeId,
      matrixUsesTaxonomy,
      matrixScopeIsLeaf,
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
        memberLabel: matrixTaxonomy?.name || 'Taxonomy',
        reason: 'scope_unavailable',
        coverageReason,
      }),
    )
    return {
      memberCount: matrixScopeIsLeaf ? scopedTaxonomyInstruments.memberCount : alignedMatrixTaxonomySeries.length,
      series: alignedMatrixTaxonomySeries,
      issues,
    }
  }, [
    alignedMatrixTaxonomySeries,
    matrixScopeIsLeaf,
    scopedTaxonomyInstruments.memberCount,
    matrixTaxonomy?.name,
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
    // A selection made after the first paint can precede this effect. Check the
    // current state rather than overwriting it with the initial default date.
    setMatrixAsOfDate((current) => riskAsOfSelectionDates.includes(current)
      ? current
      : riskAsOfSelectionDates[riskAsOfSelectionDates.length - 1] ?? '')
  }, [riskAsOfSelectionDates])

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
  const selectedMatrixEmptyLabel = matrixUsesTaxonomy && !matrixTaxonomy ? 'No taxonomy.' : 'No matrix.'
  const topLevelRiskContributionResult = useMemo(
    () =>
      buildCanonicalTaxonomyRiskContributionRows({
        holdingsWorkspace,
        catalog: taxonomyCatalog,
        taxonomy: matrixTaxonomy,
        referenceDate: holdingsWorkspace?.as_of_date ?? null,
      }),
    [
      matrixTaxonomy,
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
        taxonomy: targetTaxonomy,
        referenceDate: holdingsWorkspace?.as_of_date ?? null,
      }),
    [
      targetTaxonomy,
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
  const targetComparisonErrors = useMemo(() => [
    ...topLevelRiskBudgetContributionResult.errors,
    ...riskTargetComparisonErrors(holdingsWorkspace, topLevelRiskBudgetContributionResult.value, targetTaxonomy?.taxonomy_id),
  ], [holdingsWorkspace, topLevelRiskBudgetContributionResult, targetTaxonomy?.taxonomy_id])
  const currentPlanningGroupsResult = useMemo(
    () =>
      buildCurrentPlanningGroups({
        holdingsWorkspace,
        accountsWorkspace,
        catalog: taxonomyCatalog,
        taxonomy: targetTaxonomy,
        referenceDate: holdingsWorkspace?.as_of_date ?? null,
      }),
    [accountsWorkspace, targetTaxonomy, holdingsWorkspace, taxonomyCatalog],
  )
  const currentPlanningGroups = currentPlanningGroupsResult.value
  const portfolioBaseCurrency = holdingsWorkspace?.base_currency ?? ''

  const saaRiskGapResult = useMemo(() => buildTargetGapRows({
    resolvedTargets: targetResolution?.member_targets ?? [], stage: 'saa', currentGroups: currentPlanningGroups,
    riskSharesByGroup: riskBudgetSharesByTopLevelGroup, riskShareErrors: targetComparisonErrors,
    nodeById: targetTaxonomyNodeById, baseCurrency: portfolioBaseCurrency,
  }), [targetResolution, currentPlanningGroups, riskBudgetSharesByTopLevelGroup, targetComparisonErrors, targetTaxonomyNodeById, portfolioBaseCurrency])
  const taaRiskGapResult = useMemo(() => buildTargetGapRows({
    resolvedTargets: targetResolution?.member_targets ?? [], stage: 'taa', currentGroups: currentPlanningGroups,
    riskSharesByGroup: riskBudgetSharesByTopLevelGroup, riskShareErrors: targetComparisonErrors,
    nodeById: targetTaxonomyNodeById, baseCurrency: portfolioBaseCurrency,
  }), [targetResolution, currentPlanningGroups, riskBudgetSharesByTopLevelGroup, targetComparisonErrors, targetTaxonomyNodeById, portfolioBaseCurrency])
  const saaRiskGapRows = saaRiskGapResult.value
  const taaRiskGapRows = taaRiskGapResult.value
  const riskTargetGapRows = useMemo(() => combineTargetGapRows(saaRiskGapRows, taaRiskGapRows), [saaRiskGapRows, taaRiskGapRows])
  const riskTargetGapErrors = [...saaRiskGapResult.errors, ...taaRiskGapResult.errors]
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
    saaRiskGapRows.length && !saaRiskGapResult.errors.length
      ? saaRiskGapRows.reduce((total, row) => total + Math.abs(row.gap ?? 0), 0)
      : null
  const taaTotalRiskGap =
    taaRiskGapRows.length && !taaRiskGapResult.errors.length
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
  const riskCoverage = holdingsWorkspace?.risk_coverage_summary ?? null
  const grossCarryingAmountDetail = zh
    ? '按标的净额化账面金额取绝对值后加总；不是账户级证券 gross、FCN 本金或期权 delta 等经济敞口。'
    : 'Sum of absolute carrying amounts after netting each instrument. This is not account-level security gross exposure, FCN principal, or option delta exposure.'
  const excludedExposure = riskCoverage
    ? riskCoverage.excluded_carrying_value + riskCoverage.excluded_liability
    : null
  const riskHealthDetail = [
    productionRiskMeta,
    ...currentRiskInputErrors,
    ...(holdingsWorkspace?.forward_risk?.errors ?? []),
    ...(matrixTaxonomy ? topLevelRiskContributionResult.errors : []),
    forwardRiskCoverageLabel,
  ]
    .filter(Boolean)
    .join(' · ')
  const forwardRiskCoverageSummary = forwardRiskCoverage
    ? `${formatNumber(forwardRiskCoverage.rows_after, 0)}/${formatNumber(
        forwardRiskCoverage.rows_before,
        0,
      )} complete observations`
    : null
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
    const labelWidth = Math.min(280, Math.max(160, Math.max(...matrix.groups.map((group) => group.label.length)) * 9))
    const matrixMinWidth = labelWidth + matrix.groups.length * 72

    return (
      <HorizontalTableScroll className="risk-matrix-scroll risk-covariance-scroll">
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
      </HorizontalTableScroll>
    )
  }

  function renderTargetGapPanel({
    rows,
    errors,
    ariaLabel,
    emptyLabel,
    currentLabel,
    showTargets,
  }: {
    rows: RiskTargetGapChartRow[]
    errors: string[]
    ariaLabel: string
    emptyLabel: string
    currentLabel?: string
    showTargets?: boolean
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
          showTargets={showTargets}
        />
      </>
    )
  }

  return (
    <PortfolioWorkspaceLayout
      activeSection="Risk"
      busy={workspaceLoading || benchmarkLoading}
    >
      <section className="portfolio-detail-surface risk-page-surface">
        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {workspaceSupportError ? <div className="inline-notice inline-notice-error">{workspaceSupportError}</div> : null}

        {workspaceLoading ? (
          <CalculationStatus />
        ) : null}

        {!workspaceLoading && !holdingsWorkspace && !workspaceError ? (
          <div className="empty-state">No data.</div>
        ) : null}

        {holdingsWorkspace ? (
          <div className="risk-page-content">
            <section className="portfolio-section-block" aria-label="Risk health">
              <div className="portfolio-detail-toolbar portfolio-section-toolbar risk-section-toolbar">
                <div>
                  <div className="panel-title portfolio-title-with-hint">
                    <span>{zh ? '当前持仓风险' : 'Current Holdings Risk'}</span>
                    <QualityWarningsNotice warnings={holdingsWorkspace.quality_warnings} />
                  </div>
                  <div
                    className="portfolio-detail-meta"
                    title={riskHealthDetail}
                    aria-label={`${holdingsWorkspace.as_of_date}; ${portfolioRiskFrequency.statusLabel}. ${riskHealthDetail}`}
                    tabIndex={0}
                  >
                    {holdingsWorkspace.as_of_date} · {holdingsWorkspace.forward_risk?.status === 'ok' ? (zh ? '模型可用' : 'Model available') : (zh ? '模型不可用' : 'Model unavailable')} · {productionRiskDescription}
                  </div>
                </div>
                {targetTaxonomies.length ? <>
                  <label className="concentration-toolbar-actions">{zh ? '分类' : 'Taxonomy'}
                    <select aria-label={zh ? '风险分类' : 'Risk taxonomy'} value={targetTaxonomy?.taxonomy_id ?? ''} onChange={(event) => {
                      setTargetTaxonomyId(event.target.value)
                      try { localStorage.setItem(targetTaxonomyStorageKey, event.target.value) } catch { /* View selection is optional persistence. */ }
                    }}>
                      <option value="">{zh ? '请选择分类' : 'Choose taxonomy'}</option>
                      {targetTaxonomies.map((taxonomy) => <option key={taxonomy.taxonomy_id} value={taxonomy.taxonomy_id} translate="no">{taxonomy.name}</option>)}
                    </select>
                  </label>
                </> : null}
              </div>
              <div className="portfolio-summary-strip risk-health-strip">
                <article
                  className={
                    holdingsWorkspace.forward_risk?.status === 'ok'
                      ? 'summary-card'
                      : 'summary-card summary-card-warning'
                  }
                >
                  <span className="summary-card-label">Modeled Portfolio Volatility</span>
                  <strong className="summary-card-value">
                    {holdingsWorkspace.forward_risk?.status === 'ok' &&
                    holdingsWorkspace.forward_risk.portfolio_volatility != null
                      ? formatPercent(holdingsWorkspace.forward_risk.portfolio_volatility)
                      : 'Unavailable'}
                  </strong>
                  {forwardRiskCoverageSummary ? (
                    <span className="portfolio-detail-meta" title={forwardRiskCoverageLabel}>
                      {forwardRiskCoverageSummary}
                    </span>
                  ) : null}
                </article>
                <article className="summary-card">
                  <span className="summary-card-label"><span>Model Coverage</span><InfoHint
                    label={zh ? '模型覆盖口径' : 'Model coverage basis'}
                    detail={zh
                      ? '建模账面总额除以建模账面总额、未建模资产与负债金额及现金与结算净额绝对值之和；不是敞口占 NAV 的比例。'
                      : 'Modeled gross carrying amount divided by modeled gross carrying amount, unmodeled asset and liability amounts, and the absolute net cash and settlement balance. This is not Exposure Ratio.'}
                  /></span>
                  <strong className="summary-card-value">
                    {riskCoverage?.coverage_ratio != null
                      ? formatPercent(riskCoverage.coverage_ratio)
                      : 'Unavailable'}
                  </strong>
                </article>
                <article className="summary-card">
                  <span className="summary-card-label"><span>Modeled Gross Carrying Amount</span><InfoHint
                    label={zh ? '账面总额口径' : 'Gross carrying amount basis'} detail={grossCarryingAmountDetail}
                  /></span>
                  <strong className="summary-card-value">
                    {riskCoverage
                      ? formatCurrency(riskCoverage.modeled_gross_exposure, holdingsWorkspace.base_currency)
                      : '—'}
                  </strong>
                  <span className="portfolio-detail-meta">
                    {riskCoverage
                      ? <><span>Net Carrying Amount</span> {formatCurrency(riskCoverage.modeled_net_exposure, holdingsWorkspace.base_currency)}</>
                      : 'Modeled carrying amount unavailable'}
                  </span>
                </article>
                {excludedExposure != null && excludedExposure !== 0 ? (
                  <article
                    className="summary-card"
                    title={`Assets ${formatCurrency(
                      riskCoverage?.excluded_carrying_value,
                      holdingsWorkspace.base_currency,
                    )}; liabilities ${formatCurrency(
                      riskCoverage?.excluded_liability,
                      holdingsWorkspace.base_currency,
                    )}`}
                  >
                    <span className="summary-card-label"><span>Unmodeled Gross Carrying Amount</span><InfoHint
                      label={zh ? '账面总额口径' : 'Gross carrying amount basis'} detail={grossCarryingAmountDetail}
                    /></span>
                    <strong className="summary-card-value">
                      {formatCurrency(excludedExposure, holdingsWorkspace.base_currency)}
                    </strong>
                  </article>
                ) : null}
                <article className="summary-card" title="Signed carrying amount of cash and pending settlements outside the covariance model.">
                  <span className="summary-card-label">Cash & Settlement</span>
                  <strong className="summary-card-value">
                    {riskCoverage
                      ? formatCurrency(riskCoverage.cash_unallocated_exposure, holdingsWorkspace.base_currency)
                      : '—'}
                  </strong>
                </article>
                <article className="summary-card">
                  <span className="summary-card-label portfolio-title-with-hint">Top 3 Modeled Concentration<InfoHint
                    label={zh ? '建模内集中度口径' : 'Modeled concentration basis'}
                    detail={`${zh ? '仅在建模证券内按账面金额绝对值归一；不以组合 NAV 为分母，不用于敞口上限判断。' : 'Normalized within modeled securities by absolute carrying amounts; not divided by portfolio NAV and not used for exposure limit checks.'}${concentrationMetrics.hhi != null ? ` HHI ${formatNumber(concentrationMetrics.hhi, 3)}` : ''}`}
                  /></span>
                  <strong className="summary-card-value">
                    {concentrationMetrics.topThree != null ? formatPercent(concentrationMetrics.topThree) : '—'}
                  </strong>
                </article>
              </div>
              <RiskSourceCoverage workspace={holdingsWorkspace} />
              {riskCoverage?.excluded_rows.length ? (
                <HorizontalTableScroll className="table-shell risk-scope-table-shell">
                  <table className="transactions-table risk-scope-table">
                    <thead>
                      <tr>
                        <th>Excluded Holding</th>
                        <th>Category</th>
                        <th>Reason</th>
                        <th className="performance-cell-number">Carrying Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {riskCoverage.excluded_rows.map((row) => (
                        <tr key={row.line_id ?? `${row.instrument_id}:${row.holding_category}`}>
                          <td translate="no">{row.instrument_name ?? row.instrument_id ?? row.line_id ?? 'N/A'}</td>
                          <td>{formatLabel(row.holding_category)}</td>
                          <td>{row.exclusion_reason ?? 'No exclusion reason recorded.'}</td>
                          <td className="performance-cell-number">
                            {formatCurrency(row.exposure_base, holdingsWorkspace.base_currency)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </HorizontalTableScroll>
              ) : null}
              {topLevelRiskContributionRows.length ? (
                <details className="risk-contribution-details">
                  <summary>{zh ? '分类风险贡献' : 'Scoped Risk Contribution'} · {topLevelRiskContributionRows.length}</summary>
                  <HorizontalTableScroll className="table-shell risk-scope-table-shell">
                    <table className="transactions-table risk-scope-table">
                      <thead>
                        <tr>
                          <th>Sleeve</th>
                          <th className="performance-cell-number"><span>Modeled Weight</span><InfoHint
                            label={zh ? '建模权重口径' : 'Modeled weight basis'}
                            detail={zh ? '纳入风险模型的证券净账面金额占组合 NAV 的比例，不在建模子集内重新归一。' : 'Signed carrying amount of modeled securities divided by full portfolio NAV, without renormalizing within the modeled subset.'}
                          /></th>
                          <th className="performance-cell-number">Forward RC</th>
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
                  </HorizontalTableScroll>
                </details>
              ) : null}
            </section>
            <ConcentrationPanel key={`${portfolioId}:${concentrationDate || holdingsWorkspace.as_of_date}`} portfolioId={portfolioId} asOfDate={concentrationDate || holdingsWorkspace.as_of_date} onAsOfDateChange={date => {
              setSearchParams(current => {
                const next = new URLSearchParams(current)
                if (date) next.set('concentration_date', date)
                else next.delete('concentration_date')
                return next
              }, { replace: true })
            }} />
            <section className="portfolio-section-block risk-rolling-section" aria-label="Rolling risk">
              <div className="portfolio-detail-toolbar portfolio-section-toolbar risk-section-toolbar risk-rolling-toolbar">
                <div className="risk-toolbar-primary risk-rolling-toolbar-primary">
                  <div>
                    <div className="panel-title">Rolling Risk</div>
                    <div className="portfolio-detail-meta">{rollingBasis === 'realized'
                      ? (zh ? '按实际持仓与资金变动计算的组合市场风险' : 'Portfolio market risk from actual holdings and cash flows')
                      : (zh ? '以当前权重回看历史，不是组合实际业绩' : 'Historical returns at current weights; not actual portfolio performance')}</div>
                  </div>
                  <label className="risk-control-label">{zh ? '风险视角' : 'Risk perspective'}
                    <select aria-label={zh ? '滚动风险视角' : 'Rolling risk perspective'} value={rollingBasis} onChange={(event) => setRollingBasis(event.target.value as 'realized' | 'current')}>
                      <option value="realized">{zh ? '实际组合' : 'Actual Portfolio'}</option>
                      <option value="current">{zh ? '当前持仓回溯' : 'Current Holdings History'}</option>
                    </select>
                  </label>
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
                  {benchmarkBasisAssessment.message ? (
                    <>
                      {benchmarkBasisAssessment.blocking ? (
                        <span className="portfolio-detail-meta">Benchmark unavailable</span>
                      ) : null}
                      <InfoHint
                        label="Benchmark comparison"
                        detail={benchmarkBasisAssessment.message}
                        tone="warning"
                      />
                    </>
                  ) : null}
                </div>
                <div className="risk-section-actions">
                  <label className="risk-control-label">{zh ? '观察窗口' : 'Observation window'}
                    <select aria-label={zh ? '滚动风险观察窗口' : 'Rolling observation window'} value={rollingSettings.lookbackDays} onChange={(event) => setRollingSettings({ ...rollingSettings, lookbackDays: Number(event.target.value) })}>
                      {RISK_WINDOW_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>
                  <RiskSettingsMenu
                    label="Rolling risk"
                    settings={rollingSettings}
                    onChange={setRollingSettings}
                    includeWindow={false}
                    includeChartStyle
                  />
                </div>
              </div>
              {benchmarkLoading ? <div className="portfolio-detail-meta">Loading</div> : null}
              {benchmarkError ? <div className="overview-benchmark-error">{benchmarkError}</div> : null}
              {rollingLoading ? <CalculationStatus /> : rollingError ? <div className="inline-notice inline-notice-error" role="alert">{rollingError}</div> : <RiskWindowDiagnostics diagnostics={rollingRiskDiagnostics} />}
              <div className="risk-rolling-grid">
                <RollingRiskMetricChart
                  title="Annualized Volatility"
                  points={rollingVolatilityPoints}
                  benchmarkPoints={benchmarkRollingVolatilityPoints}
                  benchmarkLabel={benchmarkLabel}
                  displayStyle={rollingSettings.chartStyle}
                  formatValue={(value) => formatPercent(value)}
                  emptyLabel={rollingLoading ? (zh ? '加载中' : 'Loading') : (zh ? '此窗口暂无可用估计，详见上方样本与原因。' : 'No estimate for this window. See sample coverage above.')}
                />
                <RollingRiskMetricChart
                  title="Sharpe Ratio"
                  points={rollingSharpePoints}
                  benchmarkPoints={benchmarkRollingSharpePoints}
                  benchmarkLabel={benchmarkLabel}
                  displayStyle={rollingSettings.chartStyle}
                  formatValue={(value) => formatNumber(value, 2)}
                  emptyLabel={rollingLoading ? (zh ? '加载中' : 'Loading') : (zh ? '此窗口暂无可用估计，详见上方样本与原因。' : 'No estimate for this window. See sample coverage above.')}
                />
              </div>
            </section>
            <section className="portfolio-section-block" aria-label="Correlation analysis">
              <div className="portfolio-detail-toolbar portfolio-section-toolbar risk-section-toolbar risk-matrix-toolbar">
                <div className="risk-toolbar-primary risk-matrix-toolbar-primary">
                  <div>
                    <div className="panel-title">Correlation Matrix</div>
                    <div className="portfolio-detail-meta">{selectedMatrixScopeDescription}</div>
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
                  <label className="risk-control-label">{zh ? '观察窗口' : 'Observation window'}
                    <select aria-label={zh ? '相关性观察窗口' : 'Correlation observation window'} value={matrixSettings.lookbackDays} onChange={(event) => setMatrixSettings({ lookbackDays: Number(event.target.value) })}>
                      {RISK_WINDOW_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>
                </div>
              </div>
              {fullUniverseLoading ? (
                <CalculationStatus label={zh ? '加载全域历史' : 'Loading Full Universe history'} />
              ) : fullUniverseError && matrixUsesFullUniverse ? (
                <div className="inline-notice inline-notice-error" role="alert">{fullUniverseError}</div>
              ) : (
                <>
                  <RiskDateTimeline
                    dates={riskAsOfSelectionDates}
                    value={effectiveMatrixAsOfDate}
                    onChange={setMatrixAsOfDate}
                    label="Matrix as of"
                  />
                  <RiskWindowDiagnostics diagnostics={selectedCorrelationMatrixResult.diagnostics} />
                  <div className="risk-correlation-stack">
                    <div className="risk-matrix-panel">
                      {!selectedCorrelationMatrixResult.issues.length
                        ? renderCorrelationMatrix(selectedCorrelationMatrixResult.matrix, selectedMatrixEmptyLabel)
                        : null}
                    </div>
                  </div>
                </>
              )}
            </section>
            <PortfolioTailRiskPanel key={`tail:${portfolioId}:${holdingsWorkspace.as_of_date}`} portfolioId={portfolioId} asOfDate={holdingsWorkspace.as_of_date} />
            {targetTaxonomy ? (
              <section className="portfolio-section-block" aria-label="Current drift">
                <div className="portfolio-detail-toolbar portfolio-section-toolbar risk-section-toolbar">
                  <div>
                    <div className="panel-title portfolio-title-with-hint"><span>Current Drift</span>
                      <InfoHint label={zh ? '目标偏移风险口径' : 'Target drift risk basis'} detail={zh
                        ? '风险贡献使用统一的生产模型，切换分类只改变贡献分组。即使当前未持有，已配置目标仍会显示。'
                        : 'Risk contributions use the same production model for all taxonomies. Switching taxonomy regroups those contributions. Configured targets remain visible even without current holdings.'} />
                    </div>
                    <div
                      className="portfolio-detail-meta"
                      title={`${portfolioRiskFrequency.statusLabel}; ${productionRiskMeta}`}
                    >
                      {targetTaxonomy.name}; {zh ? '估值' : 'Valuation'} {holdingsWorkspace.as_of_date}
                    </div>
                    {riskGapSummary ? <div className="portfolio-detail-meta" title="Sum of absolute eligible-sleeve risk budget gaps.">
                      {zh ? '风险预算偏移合计' : 'Eligible Risk Budget Gap'} · <span>{riskGapSummary}</span>
                    </div> : null}
                  </div>

                </div>
                {renderRiskErrors([...(targetResolution?.errors ?? []), ...currentPlanningGroupsResult.errors])}
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">{zh ? '全组合风险目标偏移' : 'Portfolio risk target gap'}</div>
                  {renderTargetGapPanel({ rows: riskTargetGapRows, errors: riskTargetGapErrors, ariaLabel: 'Risk budget target gap',
                    emptyLabel: zh ? '此分类暂无可直接推导的全组合风险目标。' : 'No directly derived portfolio risk target for this taxonomy.', currentLabel: 'Risk Share' })}
                </div>
              </section>
            ) : null}
          </div>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
