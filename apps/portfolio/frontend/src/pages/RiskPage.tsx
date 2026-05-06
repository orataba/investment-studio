import { FormEvent, useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PortfolioTableViewControls, { type PortfolioTableViewOption } from '../components/PortfolioTableViewControls'
import RollingVolatilityChart, { type RollingVolatilityPoint } from '../components/RollingVolatilityChart'
import RiskTargetGapChart, { type RiskTargetGapChartRow } from '../components/RiskTargetGapChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  getHoldingsWorkspace,
  getPortfolioAccountsWorkspace,
  getPortfolioPerformance,
  getPortfolioPerformanceContribution,
  getPortfolioTaxonomyCatalog,
  type HoldingsWorkspaceResponse,
  type PortfolioAccountsWorkspaceResponse,
  type PortfolioContributionReportResponse,
  type PortfolioDailyPerformancePoint,
  type PortfolioPerformanceCoverageState,
  type PortfolioPerformanceResponse,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioTargetSetLineRecord,
  type PortfolioTargetSetRecord,
  type TaxonomyAssignmentScope,
} from '../lib/api'
import { formatCurrency, formatLabel, formatPercent, signedValueClass } from '../lib/format'

const DEFAULT_RISK_LOOKBACK_DAYS = 365
const DEFAULT_ROLLING_WINDOW_DAYS = 63
const DAYS_PER_YEAR = 365.25
const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const VOL_WINDOW_PRESETS = [21, 63, 126] as const
const RISK_VIEWS_STORAGE_KEY = 'yungu.portfolio.risk.views.v1'

type RiskDisplayMode = 'overview' | 'matrices' | 'drift' | 'contribution'
type RiskViewState = {
  displayMode: RiskDisplayMode
  rollingWindowDays: number
}

type RiskTableView = PortfolioTableViewOption & {
  state: RiskViewState
  readonly?: boolean
  createdAt?: string
  updatedAt?: string
}

type RiskViewStore = {
  activeViewId: string
  customViews: RiskTableView[]
}

const DEFAULT_RISK_VIEW_STATE: RiskViewState = {
  displayMode: 'overview',
  rollingWindowDays: DEFAULT_ROLLING_WINDOW_DAYS,
}

const SYSTEM_RISK_VIEWS: RiskTableView[] = [
  {
    id: 'overview',
    name: 'Overview',
    description: 'All risk sections.',
    readonly: true,
    state: DEFAULT_RISK_VIEW_STATE,
  },
  {
    id: 'matrices',
    name: 'Matrices',
    description: 'Volatility and covariance matrices.',
    readonly: true,
    state: { displayMode: 'matrices', rollingWindowDays: DEFAULT_ROLLING_WINDOW_DAYS },
  },
  {
    id: 'drift',
    name: 'Drift',
    description: 'Current target drift and risk budget gaps.',
    readonly: true,
    state: { displayMode: 'drift', rollingWindowDays: DEFAULT_ROLLING_WINDOW_DAYS },
  },
  {
    id: 'contribution',
    name: 'Contribution',
    description: 'All-asset risk contribution table.',
    readonly: true,
    state: { displayMode: 'contribution', rollingWindowDays: DEFAULT_ROLLING_WINDOW_DAYS },
  },
]

type PortfolioTaxonomyRecord = PortfolioTaxonomyCatalogResponse['taxonomies'][number]
type ContributionLine = PortfolioContributionReportResponse['lines'][number]
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
  latestWeight: number | null
  averageWeight: number | null
  periodReturn: number | null
  annualizedVolatility: number | null
  observationCount: number
}

type CovarianceMatrix = {
  groups: Array<{
    key: string
    label: string
    observationCount: number
    latestWeight: number | null
  }>
  cells: Array<Array<{ value: number | null; observationCount: number }>>
  maxAbs: number
}

type MonthlyVolatilityBucket = {
  bucketKey: string
  year: string
  monthIndex: number
  annualizedVolatility: number | null
  observationCount: number
  coverageState: PortfolioPerformanceCoverageState
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
  riskShare: number | null
  contributionToVariance: number | null
}

type PeriodRiskContributionRow = {
  groupKey: string
  groupLabel: string
  averageWeight: number | null
  endingWeight: number | null
  periodReturn: number | null
  annualizedVolatility: number | null
  riskShare: number | null
  contributionToVariance: number | null
  periodContribution: number | null
  totalPnl: number | null
  observationCount: number
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

function parseRollingWindow(rawValue: string | null) {
  const parsed = Number(rawValue ?? '')
  if (Number.isInteger(parsed) && parsed >= 5 && parsed <= 252) {
    return parsed
  }
  return DEFAULT_ROLLING_WINDOW_DAYS
}

function parseRiskDisplayMode(value: string | null): RiskDisplayMode | null {
  return value === 'overview' || value === 'matrices' || value === 'drift' || value === 'contribution' ? value : null
}

function normalizeRiskViewState(value: unknown): RiskViewState {
  if (!value || typeof value !== 'object') {
    return DEFAULT_RISK_VIEW_STATE
  }
  const record = value as Partial<RiskViewState>
  return {
    displayMode: parseRiskDisplayMode(typeof record.displayMode === 'string' ? record.displayMode : null) ?? DEFAULT_RISK_VIEW_STATE.displayMode,
    rollingWindowDays:
      typeof record.rollingWindowDays === 'number'
        ? parseRollingWindow(String(record.rollingWindowDays))
        : DEFAULT_RISK_VIEW_STATE.rollingWindowDays,
  }
}

function serializeRiskViewState(value: RiskViewState) {
  return JSON.stringify(normalizeRiskViewState(value))
}

function riskViewStatesEqual(left: RiskViewState, right: RiskViewState) {
  return serializeRiskViewState(left) === serializeRiskViewState(right)
}

function normalizeRiskViewStore(value: unknown): RiskViewStore {
  const record = value && typeof value === 'object' ? (value as Partial<RiskViewStore>) : {}
  const customViews = Array.isArray(record.customViews)
    ? record.customViews
        .filter((view): view is RiskTableView => Boolean(view && typeof view === 'object' && typeof view.id === 'string'))
        .map((view) => ({
          id: view.id,
          name: typeof view.name === 'string' && view.name.trim() ? view.name.trim() : 'Custom View',
          description: typeof view.description === 'string' ? view.description : null,
          readonly: false,
          createdAt: typeof view.createdAt === 'string' ? view.createdAt : undefined,
          updatedAt: typeof view.updatedAt === 'string' ? view.updatedAt : undefined,
          state: normalizeRiskViewState(view.state),
        }))
    : []
  const knownViewIds = new Set([...SYSTEM_RISK_VIEWS.map((view) => view.id), ...customViews.map((view) => view.id)])
  const activeViewId =
    typeof record.activeViewId === 'string' && knownViewIds.has(record.activeViewId)
      ? record.activeViewId
      : SYSTEM_RISK_VIEWS[0].id
  return { activeViewId, customViews }
}

function loadRiskViewStore(): RiskViewStore {
  if (typeof window === 'undefined') {
    return normalizeRiskViewStore(null)
  }
  try {
    const rawValue = window.localStorage.getItem(RISK_VIEWS_STORAGE_KEY)
    return normalizeRiskViewStore(rawValue ? JSON.parse(rawValue) : null)
  } catch {
    return normalizeRiskViewStore(null)
  }
}

function saveRiskViewStore(store: RiskViewStore) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(RISK_VIEWS_STORAGE_KEY, JSON.stringify(store))
  } catch {
    return
  }
}

function getRiskViews(store: RiskViewStore) {
  return [...SYSTEM_RISK_VIEWS, ...store.customViews]
}

function getRiskViewById(store: RiskViewStore, viewId: string) {
  return getRiskViews(store).find((view) => view.id === viewId) ?? SYSTEM_RISK_VIEWS[0]
}

function resolveRiskViewState(store: RiskViewStore, viewId: string) {
  return getRiskViewById(store, viewId).state
}

function createRiskViewId() {
  return `custom:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 8)}`
}

function finiteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function sampleStddev(values: number[]) {
  if (values.length < 2) {
    return null
  }
  const mean = values.reduce((total, value) => total + value, 0) / values.length
  const variance =
    values.reduce((total, value) => total + (value - mean) * (value - mean), 0) / (values.length - 1)
  return Math.sqrt(Math.max(variance, 0))
}

function dayDiff(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00`)
  const rightTime = Date.parse(`${right}T00:00:00`)
  if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
    return null
  }
  return Math.max(0, (rightTime - leftTime) / 86_400_000)
}

function annualizationPeriodsPerYear(dateKeys: string[], observationCount = dateKeys.length, startDate?: string | null) {
  const sortedDates = [...dateKeys].sort()
  if (observationCount < 1 || sortedDates.length < 2) {
    return null
  }
  if (startDate) {
    const elapsedDays = dayDiff(startDate, sortedDates[sortedDates.length - 1])
    return elapsedDays != null && elapsedDays > 0 ? (observationCount / elapsedDays) * DAYS_PER_YEAR : null
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

function annualizedVolatility(values: number[], dateKeys: string[] = [], startDate?: string | null) {
  const stddev = sampleStddev(values)
  const periodsPerYear = annualizationPeriodsPerYear(dateKeys, values.length, startDate)
  return stddev == null || periodsPerYear == null ? null : stddev * Math.sqrt(periodsPerYear)
}

function compoundReturn(values: number[]) {
  if (!values.length) {
    return null
  }
  return values.reduce((growthIndex, value) => growthIndex * (1 + value), 1) - 1
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

function covarianceFromMaps(left: Map<string, number>, right: Map<string, number>) {
  const pairs: Array<[number, number]> = []
  const pairDates: string[] = []
  left.forEach((leftValue, dateKey) => {
    const rightValue = right.get(dateKey)
    if (rightValue != null && Number.isFinite(leftValue) && Number.isFinite(rightValue)) {
      pairs.push([leftValue, rightValue])
      pairDates.push(dateKey)
    }
  })
  if (pairs.length < 2) {
    return { value: null, observationCount: pairs.length }
  }

  const leftMean = pairs.reduce((total, pair) => total + pair[0], 0) / pairs.length
  const rightMean = pairs.reduce((total, pair) => total + pair[1], 0) / pairs.length
  const covariance =
    pairs.reduce((total, pair) => total + (pair[0] - leftMean) * (pair[1] - rightMean), 0) / (pairs.length - 1)
  const periodsPerYear = annualizationPeriodsPerYear(pairDates, pairs.length)
  return { value: periodsPerYear == null ? null : covariance * periodsPerYear, observationCount: pairs.length }
}

function mergeCoverageState(states: PortfolioPerformanceCoverageState[]) {
  if (states.some((state) => state === 'complete')) {
    return states.some((state) => state !== 'complete') ? 'partial' : 'complete'
  }
  if (states.some((state) => state === 'partial')) {
    return 'partial'
  }
  return 'unavailable'
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

function targetMemberKey(memberType: PortfolioTargetSetLineRecord['target_member_type'], memberId: string) {
  return `${memberType}:${memberId}`
}

function buildRollingVolatilityPoints(dailySeries: PortfolioDailyPerformancePoint[], windowDays: number) {
  const returnPoints = dailySeries
    .filter((point) => finiteNumber(point.daily_twr) != null && point.return_observation_eligible)
    .slice()
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
    .map((point) => ({ date: point.as_of_date, value: point.daily_twr as number }))

  const points: RollingVolatilityPoint[] = []
  for (let index = windowDays - 1; index < returnPoints.length; index += 1) {
    const windowPoints = returnPoints.slice(index - windowDays + 1, index + 1)
    const value = annualizedVolatility(
      windowPoints.map((point) => point.value),
      windowPoints.map((point) => point.date),
    )
    if (value != null) {
      points.push({ date: returnPoints[index].date, value })
    }
  }
  return points
}

function buildMonthlyVolatilityBuckets(dailySeries: PortfolioDailyPerformancePoint[]) {
  const bucketKeys: string[] = []
  const buckets = new Map<
    string,
    {
      bucketKey: string
      year: string
      monthIndex: number
      returns: Array<{ date: string; value: number }>
      coverageStates: PortfolioPerformanceCoverageState[]
    }
  >()

  dailySeries.forEach((point) => {
    const bucketKey = point.as_of_date.slice(0, 7)
    const year = point.as_of_date.slice(0, 4)
    const monthIndex = Number(point.as_of_date.slice(5, 7)) - 1
    let bucket = buckets.get(bucketKey)
    if (!bucket) {
      bucket = { bucketKey, year, monthIndex, returns: [], coverageStates: [] }
      buckets.set(bucketKey, bucket)
      bucketKeys.push(bucketKey)
    }

    const dailyReturn = finiteNumber(point.daily_twr)
    if (dailyReturn != null && point.return_observation_eligible) {
      bucket.returns.push({ date: point.as_of_date, value: dailyReturn })
    }
    bucket.coverageStates.push(point.coverage_state)
  })

  return bucketKeys.map((bucketKey) => {
    const bucket = buckets.get(bucketKey)!
    return {
      bucketKey,
      year: bucket.year,
      monthIndex: bucket.monthIndex,
      annualizedVolatility: annualizedVolatility(
        bucket.returns.map((point) => point.value),
        bucket.returns.map((point) => point.date),
      ),
      observationCount: bucket.returns.length,
      coverageState: mergeCoverageState(bucket.coverageStates),
    } satisfies MonthlyVolatilityBucket
  })
}

function buildGroupReturnSeries(slices: ReturnSlice[], minimumObservations = 2) {
  const lookup = new Map<
    string,
    {
      groupKey: string
      groupLabel: string
      returnsByDate: Map<string, number>
      latestWeight: number | null
      weightTotal: number
      weightCount: number
      firstDate: string | null
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
        latestWeight: null,
        weightTotal: 0,
        weightCount: 0,
        firstDate: slice.as_of_date,
      }
      current.groupLabel = slice.group_label || current.groupLabel
      current.firstDate = current.firstDate == null || slice.as_of_date < current.firstDate ? slice.as_of_date : current.firstDate

      const dailyReturn = finiteNumber(slice.daily_return)
      if (dailyReturn != null && slice.return_observation_eligible) {
        current.returnsByDate.set(slice.as_of_date, dailyReturn)
      }

      const endingWeight = finiteNumber(slice.ending_weight)
      if (endingWeight != null) {
        current.latestWeight = endingWeight
      }

      const beginningWeight = finiteNumber(slice.beginning_weight)
      if (beginningWeight != null) {
        current.weightTotal += beginningWeight
        current.weightCount += 1
      }

      lookup.set(groupKey, current)
    })

  return [...lookup.values()]
    .map((item) => {
      const returnEntries = [...item.returnsByDate.entries()]
      const returns = returnEntries.map((entry) => entry[1])
      return {
        groupKey: item.groupKey,
        groupLabel: item.groupLabel,
        returnsByDate: item.returnsByDate,
        latestWeight: item.latestWeight,
        averageWeight: item.weightCount > 0 ? item.weightTotal / item.weightCount : null,
        periodReturn: compoundReturn(returns),
        annualizedVolatility: annualizedVolatility(
          returns,
          returnEntries.map((entry) => entry[0]),
          item.firstDate,
        ),
        observationCount: item.returnsByDate.size,
      }
    })
    .filter((item) => item.observationCount >= minimumObservations)
    .sort((left, right) => {
      const weightDelta = Math.abs(right.latestWeight ?? 0) - Math.abs(left.latestWeight ?? 0)
      return weightDelta || left.groupLabel.localeCompare(right.groupLabel)
    }) satisfies GroupReturnSeries[]
}

function buildCovarianceMatrix(series: GroupReturnSeries[]) {
  const groups = series.map((item) => ({
    key: item.groupKey,
    label: item.groupLabel,
    observationCount: item.observationCount,
    latestWeight: item.latestWeight,
  }))
  let maxAbs = 0
  const cells = series.map((rowSeries) =>
    series.map((columnSeries) => {
      const cell = covarianceFromMaps(rowSeries.returnsByDate, columnSeries.returnsByDate)
      if (cell.value != null) {
        maxAbs = Math.max(maxAbs, Math.abs(cell.value))
      }
      return cell
    }),
  )

  return { groups, cells, maxAbs } satisfies CovarianceMatrix
}

function buildNodeLookup(catalog: PortfolioTaxonomyCatalogResponse | null, taxonomyId: string | null | undefined) {
  return new Map(
    (catalog?.taxonomy_nodes ?? [])
      .filter((node) => node.taxonomy_id === taxonomyId && node.status === 'active')
      .map((node) => [node.taxonomy_node_id, node] as const),
  )
}

function resolveTopLevelNode(
  nodeId: string | null | undefined,
  nodeById: Map<string, PortfolioTaxonomyNodeRecord>,
) {
  if (!nodeId) {
    return null
  }
  let current = nodeById.get(nodeId) ?? null
  let guard = 0
  while (current?.parent_taxonomy_node_id && guard < 100) {
    current = nodeById.get(current.parent_taxonomy_node_id) ?? null
    guard += 1
  }
  return current
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

function addNullable(left: number | null, right: number | null) {
  if (left == null || right == null) {
    return null
  }
  return left + right
}

function buildTopLevelTaxonomySlices(
  slices: ReturnSlice[],
  catalog: PortfolioTaxonomyCatalogResponse | null,
  taxonomy: PortfolioTaxonomyRecord | null,
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

  slices.forEach((slice) => {
    const assignment = findActiveAssignment(
      catalog,
      taxonomy.taxonomy_id,
      'instrument',
      slice.group_key,
      slice.as_of_date,
    )
    const topLevelNode = resolveTopLevelNode(assignment?.taxonomy_node_id, nodeById)
    const groupKey = topLevelNode?.taxonomy_node_id ?? `unassigned:${taxonomy.taxonomy_id}`
    const groupLabel = topLevelNode?.node_name ?? 'Unassigned'
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

    current.beginning_value_base = addNullable(current.beginning_value_base, finiteNumber(slice.beginning_value_base))
    current.ending_value_base = addNullable(current.ending_value_base, finiteNumber(slice.ending_value_base))
    current.beginning_weight = addNullable(current.beginning_weight, finiteNumber(slice.beginning_weight))
    current.ending_weight = addNullable(current.ending_weight, finiteNumber(slice.ending_weight))
    current.total_pnl = addNullable(current.total_pnl, finiteNumber(slice.total_pnl))
    current.daily_contribution = addNullable(current.daily_contribution, finiteNumber(slice.daily_contribution))
    current.return_observation_eligible = current.return_observation_eligible || Boolean(slice.return_observation_eligible)
    grouped.set(aggregateKey, current)
  })

  return [...grouped.values()].map((slice) => ({
    ...slice,
    daily_return:
      slice.total_pnl != null && slice.beginning_value_base != null && slice.beginning_value_base > 1e-9
        ? slice.total_pnl / slice.beginning_value_base
        : null,
  })) satisfies ReturnSlice[]
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
  const currentCatalog = catalog
  const currentReferenceDate = referenceDate
  const nodeById = buildNodeLookup(currentCatalog, taxonomyId)
  const groups = new Map<string, CurrentPlanningGroup>()

  function addEntity({
    targetScope,
    entityId,
    valueBase,
    fallbackWeight,
  }: {
    targetScope: TaxonomyAssignmentScope
    entityId: string
    valueBase: number | null
    fallbackWeight: number | null
  }) {
    const assignment = findActiveAssignment(currentCatalog, taxonomyId, targetScope, entityId, currentReferenceDate)
    const topLevelNode = resolveTopLevelNode(assignment?.taxonomy_node_id, nodeById)
    const groupKey = topLevelNode?.taxonomy_node_id ?? `unassigned:${taxonomyId}`
    const label = topLevelNode?.node_name ?? 'Unassigned'
    const current = groups.get(groupKey) ?? {
      groupKey,
      label,
      currentWeight: null,
      currentValueBase: null,
    }
    if (fallbackWeight != null) {
      current.currentWeight = (current.currentWeight ?? 0) + fallbackWeight
    }
    if (valueBase != null) {
      current.currentValueBase = (current.currentValueBase ?? 0) + valueBase
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
            currentCatalog,
            taxonomyId,
            'cash_bucket',
            accountRow.account.account_id,
            currentReferenceDate,
          )
          return Boolean(activeCashAssignment) || Math.abs(finiteNumber(accountRow.derived_cash_balance_base) ?? 0) > 1e-9
        })
      : []
    const totalValueBase =
      holdingsWorkspace.rows.reduce((total, row) => total + (finiteNumber(row.market_value_base) ?? 0), 0) +
      cashAccounts.reduce((total, accountRow) => total + (finiteNumber(accountRow.derived_cash_balance_base) ?? 0), 0)

    holdingsWorkspace.rows.forEach((row) => {
      const valueBase = finiteNumber(row.market_value_base)
      const fallbackWeight = totalValueBase > 1e-9 && valueBase != null ? valueBase / totalValueBase : row.allocation
      addEntity({
        targetScope: 'instrument',
        entityId: row.asset_core.asset_id,
        valueBase,
        fallbackWeight,
      })
    })

    cashAccounts.forEach((accountRow) => {
      const valueBase = finiteNumber(accountRow.derived_cash_balance_base)
      addEntity({
        targetScope: 'cash_bucket',
        entityId: accountRow.account.account_id,
        valueBase,
        fallbackWeight: totalValueBase > 1e-9 && valueBase != null ? valueBase / totalValueBase : null,
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
        fallbackWeight: totalValueBase > 1e-9 && valueBase != null ? valueBase / totalValueBase : null,
      })
    })
  } else {
    const accountRows = accountsWorkspace?.accounts ?? []
    const totalValueBase = accountRows.reduce((total, accountRow) => total + accountValueBase(accountRow), 0)
    accountRows.forEach((accountRow) => {
      const valueBase = accountValueBase(accountRow)
      addEntity({
        targetScope: 'account',
        entityId: accountRow.account.account_id,
        valueBase,
        fallbackWeight: totalValueBase > 1e-9 ? valueBase / totalValueBase : null,
      })
    })
  }

  return [...groups.values()]
    .filter((group) => group.currentWeight != null || group.currentValueBase != null)
    .sort((left, right) => Math.abs(right.currentWeight ?? 0) - Math.abs(left.currentWeight ?? 0))
}

function buildRiskContributionRows(series: GroupReturnSeries[]) {
  const riskSeries = series.filter((item) => Math.abs(item.latestWeight ?? 0) > 1e-9)
  const rawWeightTotal = riskSeries.reduce((total, item) => total + Math.abs(item.latestWeight ?? 0), 0)
  if (!riskSeries.length || rawWeightTotal <= 1e-12) {
    return [] satisfies RiskContributionRow[]
  }

  const weights = riskSeries.map((item) => (item.latestWeight ?? 0) / rawWeightTotal)
  const covarianceMatrix = riskSeries.map((rowSeries) =>
    riskSeries.map((columnSeries) => covarianceFromMaps(rowSeries.returnsByDate, columnSeries.returnsByDate).value ?? 0),
  )
  const marginal = covarianceMatrix.map((row) =>
    row.reduce((total, covarianceValue, columnIndex) => total + covarianceValue * weights[columnIndex], 0),
  )
  const variance = weights.reduce((total, weight, index) => total + weight * marginal[index], 0)

  return riskSeries
    .map((item, index) => {
      const contributionToVariance = weights[index] * marginal[index]
      return {
        groupKey: item.groupKey,
        groupLabel: item.groupLabel,
        weight: weights[index],
        contributionToVariance,
        riskShare: variance > 1e-12 ? contributionToVariance / variance : null,
      } satisfies RiskContributionRow
    })
    .sort((left, right) => Math.abs(right.riskShare ?? 0) - Math.abs(left.riskShare ?? 0))
}

function buildAverageWeightRiskShareMap(series: GroupReturnSeries[]) {
  const riskSeries = series.filter(
    (item) => item.observationCount >= 2 && Math.abs(item.averageWeight ?? 0) > 1e-9,
  )
  const rawWeightTotal = riskSeries.reduce((total, item) => total + Math.abs(item.averageWeight ?? 0), 0)
  const riskShareByGroup = new Map<
    string,
    { riskShare: number | null; contributionToVariance: number | null }
  >()
  if (!riskSeries.length || rawWeightTotal <= 1e-12) {
    return riskShareByGroup
  }

  const weights = riskSeries.map((item) => (item.averageWeight ?? 0) / rawWeightTotal)
  const covarianceMatrix = riskSeries.map((rowSeries) =>
    riskSeries.map((columnSeries) => covarianceFromMaps(rowSeries.returnsByDate, columnSeries.returnsByDate).value ?? 0),
  )
  const marginal = covarianceMatrix.map((row) =>
    row.reduce((total, covarianceValue, columnIndex) => total + covarianceValue * weights[columnIndex], 0),
  )
  const variance = weights.reduce((total, weight, index) => total + weight * marginal[index], 0)

  riskSeries.forEach((item, index) => {
    const contributionToVariance = weights[index] * marginal[index]
    riskShareByGroup.set(item.groupKey, {
      contributionToVariance,
      riskShare: variance > 1e-12 ? contributionToVariance / variance : null,
    })
  })

  return riskShareByGroup
}

function buildPeriodRiskContributionRows(slices: ReturnSlice[], lines: ContributionLine[]) {
  const allSeries = buildGroupReturnSeries(slices, 1)
  const seriesByGroup = new Map(allSeries.map((series) => [series.groupKey, series] as const))
  const lineByGroup = new Map(lines.map((line) => [line.group_key, line] as const))
  const riskShareByGroup = buildAverageWeightRiskShareMap(allSeries)
  const groupKeys = new Set([...lineByGroup.keys(), ...seriesByGroup.keys()])

  return [...groupKeys]
    .map((groupKey) => {
      const line = lineByGroup.get(groupKey) ?? null
      const series = seriesByGroup.get(groupKey) ?? null
      const riskShare = riskShareByGroup.get(groupKey) ?? null
      return {
        groupKey,
        groupLabel: line?.group_label ?? series?.groupLabel ?? groupKey,
        averageWeight: line?.average_weight ?? series?.averageWeight ?? null,
        endingWeight: line?.ending_weight ?? series?.latestWeight ?? null,
        periodReturn: series?.periodReturn ?? null,
        annualizedVolatility: series?.annualizedVolatility ?? null,
        riskShare: riskShare?.riskShare ?? null,
        contributionToVariance: riskShare?.contributionToVariance ?? null,
        periodContribution: line?.period_contribution ?? null,
        totalPnl: line?.total_pnl ?? null,
        observationCount: series?.observationCount ?? 0,
      } satisfies PeriodRiskContributionRow
    })
    .sort(
      (left, right) =>
        Math.abs(right.riskShare ?? 0) - Math.abs(left.riskShare ?? 0) ||
        Math.abs(right.periodContribution ?? 0) - Math.abs(left.periodContribution ?? 0) ||
        Math.abs(right.averageWeight ?? 0) - Math.abs(left.averageWeight ?? 0) ||
        left.groupLabel.localeCompare(right.groupLabel),
    )
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
    const current = dimension === 'weight' ? currentGroup?.currentWeight ?? null : riskSharesByGroup.get(key) ?? null
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

function heatmapCellStyle(value: number | null | undefined, maxAbs: number, tone: 'volatility' | 'signed'): CSSProperties {
  if (value == null || Number.isNaN(value) || maxAbs <= 0) {
    return {}
  }
  const intensity = Math.min(1, Math.max(0.08, Math.abs(value) / maxAbs))
  if (tone === 'volatility') {
    return { backgroundColor: `rgba(180, 83, 9, ${0.06 + intensity * 0.28})` }
  }
  if (value < 0) {
    return { backgroundColor: `rgba(185, 28, 28, ${0.06 + intensity * 0.24})` }
  }
  return { backgroundColor: `rgba(15, 76, 129, ${0.06 + intensity * 0.24})` }
}

function formatCovariance(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }
  return formatPercent(value, 3)
}

function TableStatusRow({
  colSpan,
  label,
  tone = 'neutral',
}: {
  colSpan: number
  label: string
  tone?: 'neutral' | 'error'
}) {
  return (
    <tr className="table-status-row">
      <td colSpan={colSpan} className={`empty-state-cell ${tone === 'error' ? 'table-status-cell-error' : ''}`}>
        {label}
      </td>
    </tr>
  )
}

export default function RiskPage() {
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
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
  const initialRiskViewStore = useMemo(() => loadRiskViewStore(), [])
  const initialRiskViewState = useMemo(
    () => resolveRiskViewState(initialRiskViewStore, initialRiskViewStore.activeViewId),
    [initialRiskViewStore],
  )
  const [riskViewStore, setRiskViewStore] = useState<RiskViewStore>(() => initialRiskViewStore)
  const [activeRiskViewId, setActiveRiskViewId] = useState(initialRiskViewStore.activeViewId)

  const requestedAsOfDate = searchParams.get('as_of_date') ?? ''
  const appliedStartDate = searchParams.get('start_date') ?? ''
  const appliedEndDate = searchParams.get('end_date') ?? ''
  const rollingWindowDays = searchParams.get('vol_window_days')
    ? parseRollingWindow(searchParams.get('vol_window_days'))
    : initialRiskViewState.rollingWindowDays
  const riskDisplayMode = parseRiskDisplayMode(searchParams.get('risk_view')) ?? initialRiskViewState.displayMode
  const riskViews = useMemo(() => getRiskViews(riskViewStore), [riskViewStore])
  const activeRiskView = useMemo(
    () => getRiskViewById(riskViewStore, activeRiskViewId),
    [activeRiskViewId, riskViewStore],
  )
  const currentRiskViewState = useMemo<RiskViewState>(
    () => ({ displayMode: riskDisplayMode, rollingWindowDays }),
    [riskDisplayMode, rollingWindowDays],
  )
  const riskViewEdited = !riskViewStatesEqual(currentRiskViewState, activeRiskView.state)
  const riskWindowEndDate = appliedEndDate || holdingsWorkspace?.as_of_date || requestedAsOfDate
  const riskWindowStartDate = appliedStartDate || (riskWindowEndDate ? shiftIsoDate(riskWindowEndDate, -(DEFAULT_RISK_LOOKBACK_DAYS - 1)) : '')

  const [draftAsOfDate, setDraftAsOfDate] = useState(requestedAsOfDate)
  const [draftStartDate, setDraftStartDate] = useState(riskWindowStartDate)
  const [draftEndDate, setDraftEndDate] = useState(riskWindowEndDate)
  const [draftRollingWindowDays, setDraftRollingWindowDays] = useState(String(rollingWindowDays))

  useEffect(() => {
    setDraftAsOfDate(requestedAsOfDate)
  }, [requestedAsOfDate])

  useEffect(() => {
    setDraftStartDate(riskWindowStartDate)
    setDraftEndDate(riskWindowEndDate)
  }, [riskWindowEndDate, riskWindowStartDate])

  useEffect(() => {
    setDraftRollingWindowDays(String(rollingWindowDays))
  }, [rollingWindowDays])

  function updateRiskParams(nextValues: {
    as_of_date?: string | null
    start_date?: string | null
    end_date?: string | null
    vol_window_days?: string | null
    risk_view?: string | null
  }) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      Object.entries(nextValues).forEach(([key, value]) => {
        const normalizedValue = value && value.trim() ? value.trim() : null
        if (normalizedValue) {
          next.set(key, normalizedValue)
        } else {
          next.delete(key)
        }
      })
      return next
    })
  }

  function handleApplyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    updateRiskParams({
      as_of_date: draftAsOfDate || null,
      start_date: draftStartDate || null,
      end_date: draftEndDate || null,
      vol_window_days: draftRollingWindowDays || null,
    })
  }

  function handleResetFilters() {
    setDraftAsOfDate('')
    setDraftStartDate('')
    setDraftEndDate('')
    setDraftRollingWindowDays(String(DEFAULT_ROLLING_WINDOW_DAYS))
    updateRiskParams({
      as_of_date: null,
      start_date: null,
      end_date: null,
      vol_window_days: null,
    })
  }

  function applyRiskViewState(state: RiskViewState) {
    const normalized = normalizeRiskViewState(state)
    setDraftRollingWindowDays(String(normalized.rollingWindowDays))
    updateRiskParams({
      risk_view: normalized.displayMode === 'overview' ? null : normalized.displayMode,
      vol_window_days: normalized.rollingWindowDays === DEFAULT_ROLLING_WINDOW_DAYS ? null : String(normalized.rollingWindowDays),
    })
  }

  function handleSelectRiskView(viewId: string) {
    const nextView = getRiskViewById(riskViewStore, viewId)
    setActiveRiskViewId(nextView.id)
    setRiskViewStore((current) => ({ ...current, activeViewId: nextView.id }))
    applyRiskViewState(resolveRiskViewState(riskViewStore, nextView.id))
  }

  function handleSaveRiskView() {
    if (activeRiskView.readonly) {
      return
    }
    const timestamp = new Date().toISOString()
    setRiskViewStore((current) => {
      return {
        ...current,
        activeViewId: activeRiskViewId,
        customViews: current.customViews.map((view) =>
          view.id === activeRiskViewId
            ? {
                ...view,
                state: currentRiskViewState,
                updatedAt: timestamp,
              }
            : view,
        ),
      }
    })
  }

  function handleSaveRiskViewAs(name: string, description: string | null) {
    const timestamp = new Date().toISOString()
    const viewId = createRiskViewId()
    const nextView: RiskTableView = {
      id: viewId,
      name,
      description,
      readonly: false,
      createdAt: timestamp,
      updatedAt: timestamp,
      state: currentRiskViewState,
    }
    setRiskViewStore((current) => ({
      ...current,
      activeViewId: viewId,
      customViews: [...current.customViews, nextView],
    }))
    setActiveRiskViewId(viewId)
  }

  useEffect(() => {
    saveRiskViewStore(riskViewStore)
  }, [riskViewStore])

  useEffect(() => {
    if (!portfolioId) {
      setHoldingsWorkspace(null)
      setAccountsWorkspace(null)
      setTaxonomyCatalog(null)
      setWorkspaceLoading(false)
      setWorkspaceError(null)
      return
    }

    let cancelled = false
    setWorkspaceLoading(true)

    Promise.all([
      getHoldingsWorkspace(portfolioId, { as_of_date: requestedAsOfDate || undefined }),
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
  }, [portfolioId, requestedAsOfDate])

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
              : 'Failed to load asset return slices.',
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

  const rollingVolatilityPoints = useMemo(
    () => buildRollingVolatilityPoints(performanceWorkspace?.daily_series ?? [], rollingWindowDays),
    [performanceWorkspace?.daily_series, rollingWindowDays],
  )
  const monthlyVolatilityBuckets = useMemo(
    () => buildMonthlyVolatilityBuckets(performanceWorkspace?.daily_series ?? []),
    [performanceWorkspace?.daily_series],
  )
  const monthlyVolatilityBucketByKey = useMemo(
    () => new Map(monthlyVolatilityBuckets.map((bucket) => [bucket.bucketKey, bucket] as const)),
    [monthlyVolatilityBuckets],
  )
  const monthlyVolatilityYears = useMemo(
    () => [...new Set(monthlyVolatilityBuckets.map((bucket) => bucket.year))].sort((left, right) => right.localeCompare(left)),
    [monthlyVolatilityBuckets],
  )
  const maxMonthlyVolatility = useMemo(
    () =>
      Math.max(
        0,
        ...monthlyVolatilityBuckets
          .map((bucket) => bucket.annualizedVolatility)
          .filter((value): value is number => value != null),
      ),
    [monthlyVolatilityBuckets],
  )

  const instrumentSlices = instrumentContribution?.daily_slices ?? []
  const assetReturnSeries = useMemo(() => buildGroupReturnSeries(instrumentSlices), [instrumentSlices])
  const assetCovarianceMatrix = useMemo(() => buildCovarianceMatrix(assetReturnSeries), [assetReturnSeries])
  const topLevelTaxonomySlices = useMemo(
    () => buildTopLevelTaxonomySlices(instrumentSlices, taxonomyCatalog, defaultPlanningTaxonomy),
    [defaultPlanningTaxonomy, instrumentSlices, taxonomyCatalog],
  )
  const topLevelTaxonomySeries = useMemo(
    () => buildGroupReturnSeries(topLevelTaxonomySlices),
    [topLevelTaxonomySlices],
  )
  const taxonomyCovarianceMatrix = useMemo(
    () => buildCovarianceMatrix(topLevelTaxonomySeries),
    [topLevelTaxonomySeries],
  )
  const topLevelRiskContributionRows = useMemo(
    () => buildRiskContributionRows(topLevelTaxonomySeries),
    [topLevelTaxonomySeries],
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
  const periodRiskContributionRows = useMemo(
    () => buildPeriodRiskContributionRows(instrumentSlices, instrumentContribution?.lines ?? []),
    [instrumentContribution?.lines, instrumentSlices],
  )

  function renderMonthlyVolatilityMatrix() {
    if (!monthlyVolatilityYears.length) {
      return <div className="price-chart-empty">No monthly volatility buckets are available for this risk window.</div>
    }

    return (
      <div className="risk-matrix-scroll">
        <table className="risk-heatmap-table">
          <thead>
            <tr>
              <th>Year</th>
              {MONTH_LABELS.map((monthLabel) => (
                <th key={monthLabel}>{monthLabel}</th>
              ))}
              <th>Avg</th>
            </tr>
          </thead>
          <tbody>
            {monthlyVolatilityYears.map((year) => {
              const yearBuckets = MONTH_LABELS.map((_, monthIndex) =>
                monthlyVolatilityBucketByKey.get(`${year}-${String(monthIndex + 1).padStart(2, '0')}`),
              )
              const yearValues = yearBuckets
                .map((bucket) => bucket?.annualizedVolatility)
                .filter((value): value is number => value != null)
              const yearAverage = yearValues.length
                ? yearValues.reduce((total, value) => total + value, 0) / yearValues.length
                : null
              return (
                <tr key={year}>
                  <th>{year}</th>
                  {yearBuckets.map((bucket, monthIndex) => (
                    <td
                      key={`${year}:${monthIndex}`}
                      className="risk-heatmap-cell"
                      style={heatmapCellStyle(bucket?.annualizedVolatility, maxMonthlyVolatility, 'volatility')}
                      title={
                        bucket
                          ? `${bucket.bucketKey}: ${bucket.observationCount} observations, ${formatLabel(bucket.coverageState)} coverage`
                          : undefined
                      }
                    >
                      {formatPercent(bucket?.annualizedVolatility)}
                    </td>
                  ))}
                  <td
                    className="risk-heatmap-cell risk-heatmap-cell-summary"
                    style={heatmapCellStyle(yearAverage, maxMonthlyVolatility, 'volatility')}
                  >
                    {formatPercent(yearAverage)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )
  }

  function renderCovarianceMatrix(matrix: CovarianceMatrix, emptyLabel: string) {
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
                <th key={group.key} title={`${group.label}; weight ${formatPercent(group.latestWeight)}`}>
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
                      style={heatmapCellStyle(cell?.value, matrix.maxAbs, 'signed')}
                      title={`${rowGroup.label} x ${columnGroup.label}; ${cell?.observationCount ?? 0} paired observations`}
                    >
                      {formatCovariance(cell?.value)}
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

  function renderPeriodRiskContributionTable() {
    const maxAbsRiskShare = Math.max(
      0,
      ...periodRiskContributionRows
        .map((row) => row.riskShare)
        .filter((value): value is number => value != null)
        .map((value) => Math.abs(value)),
    )
    if (!periodRiskContributionRows.length) {
      return <div className="price-chart-empty">No asset risk contribution rows are available for this window.</div>
    }

    return (
      <div className="risk-matrix-scroll">
        <table className="risk-heatmap-table risk-contribution-table">
          <thead>
            <tr>
              <th>Asset</th>
              <th>Avg Weight</th>
              <th>End Weight</th>
              <th>Interval Return</th>
              <th>Annualized Vol</th>
              <th>Risk Share</th>
              <th>Return Contribution</th>
            </tr>
          </thead>
          <tbody>
            {periodRiskContributionRows.map((row) => (
              <tr key={row.groupKey}>
                <th>{row.groupLabel}</th>
                <td>{formatPercent(row.averageWeight)}</td>
                <td>{formatPercent(row.endingWeight)}</td>
                <td className={signedValueClass(row.periodReturn)} title={`${row.observationCount} return observations`}>
                  {signedPercent(row.periodReturn)}
                </td>
                <td>{formatPercent(row.annualizedVolatility)}</td>
                <td
                  className={`risk-heatmap-cell ${signedValueClass(row.riskShare)}`}
                  style={heatmapCellStyle(row.riskShare, maxAbsRiskShare, 'signed')}
                >
                  {signedPercent(row.riskShare)}
                </td>
                <td className={signedValueClass(row.periodContribution)}>{signedPercent(row.periodContribution)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  const showRiskMatrices = riskDisplayMode === 'overview' || riskDisplayMode === 'matrices'
  const showRiskDrift = riskDisplayMode === 'overview' || riskDisplayMode === 'drift'
  const showRiskContribution = riskDisplayMode === 'overview' || riskDisplayMode === 'contribution'

  return (
    <PortfolioWorkspaceLayout activeSection="Risk" toolbarLabel="View: Risk Analytics">
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Risk</div>
        </div>

        <form className="performance-filter-bar" onSubmit={handleApplyFilters}>
          <PortfolioTableViewControls
            views={riskViews}
            activeViewId={activeRiskViewId}
            edited={riskViewEdited}
            canSave={!activeRiskView.readonly}
            onSelect={handleSelectRiskView}
            onSave={handleSaveRiskView}
            onSaveAs={handleSaveRiskViewAs}
          />
          <div className="risk-filter-grid">
            <label>
              <span>As Of Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={draftAsOfDate}
                onChange={(event) => setDraftAsOfDate(event.target.value)}
              />
            </label>
            <label>
              <span>Start Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={draftStartDate}
                onChange={(event) => setDraftStartDate(event.target.value)}
              />
            </label>
            <label>
              <span>End Date</span>
              <input
                className="transaction-filter-input"
                type="date"
                value={draftEndDate}
                onChange={(event) => setDraftEndDate(event.target.value)}
              />
            </label>
            <label>
              <span>Rolling Window</span>
              <input
                className="transaction-filter-input"
                type="number"
                min={5}
                max={252}
                value={draftRollingWindowDays}
                onChange={(event) => setDraftRollingWindowDays(event.target.value)}
              />
            </label>
          </div>
          <div className="performance-filter-actions">
            <button type="submit">Apply Risk Window</button>
            <button type="button" onClick={handleResetFilters}>
              Reset
            </button>
          </div>
        </form>

        <div className="performance-inline-tabs">
          {VOL_WINDOW_PRESETS.map((days) => (
            <button
              key={days}
              type="button"
              className={`performance-inline-tab ${rollingWindowDays === days ? 'performance-inline-tab-active' : ''}`}
              onClick={() => updateRiskParams({ vol_window_days: String(days) })}
            >
              {days} obs
            </button>
          ))}
        </div>

        {!appliedStartDate && !appliedEndDate && riskWindowEndDate ? (
          <div className="inline-notice">
            Default risk window: trailing {DEFAULT_RISK_LOOKBACK_DAYS} calendar days ending {riskWindowEndDate}.
          </div>
        ) : null}

        {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
        {performanceError ? <div className="inline-notice inline-notice-error">{performanceError}</div> : null}
        {contributionError ? <div className="inline-notice inline-notice-error">{contributionError}</div> : null}

        {workspaceLoading ? (
          <CalculationStatus label="Loading holdings, accounts, taxonomy, and target context…" />
        ) : null}

        {riskDataLoading && !performanceWorkspace && !instrumentContribution ? (
          <CalculationStatus label="Building risk series, asset covariance, and risk contribution slices…" />
        ) : null}

        {!workspaceLoading && !holdingsWorkspace && !workspaceError ? (
          <div className="empty-state">No risk workspace is available for this portfolio.</div>
        ) : null}

        {holdingsWorkspace ? (
          <>
            {showRiskMatrices ? (
              <>
                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Rolling Annualized Volatility</div>
                    <div className="portfolio-detail-meta">
                      {riskWindowStartDate && riskWindowEndDate
                        ? `${riskWindowStartDate} to ${riskWindowEndDate}; ${rollingVolatilityPoints.length} rolling points`
                        : 'No active risk window'}
                    </div>
                  </div>
                  <RollingVolatilityChart points={rollingVolatilityPoints} windowDays={rollingWindowDays} />
                </section>

                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Monthly Annualized Volatility Matrix</div>
                    <div className="portfolio-detail-meta">
                      {performanceWorkspace?.summary.risk_return_observation_count ?? 0} market return observations
                    </div>
                  </div>
                  {renderMonthlyVolatilityMatrix()}
                </section>

                <section className="performance-section-block">
                  <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                    <div className="panel-title">Covariance Matrices</div>
                    <div className="portfolio-detail-meta">
                      Annualized observed-return covariance; taxonomy view uses {defaultPlanningTaxonomy?.name ?? 'the default planning taxonomy'}
                    </div>
                  </div>
                  <div className="risk-covariance-grid">
                    <div className="risk-matrix-panel">
                      <div className="risk-matrix-panel-title">All Assets</div>
                      {renderCovarianceMatrix(assetCovarianceMatrix, 'No all-asset covariance matrix is available.')}
                    </div>
                    <div className="risk-matrix-panel">
                      <div className="risk-matrix-panel-title">Taxonomy Level 1</div>
                      {renderCovarianceMatrix(
                        taxonomyCovarianceMatrix,
                        defaultPlanningTaxonomy
                          ? 'No taxonomy level-1 covariance matrix is available.'
                          : 'Configure a default planning taxonomy to build the taxonomy covariance matrix.',
                      )}
                    </div>
                  </div>
                </section>
              </>
            ) : null}

            {showRiskDrift ? (
              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">Current Portfolio Drift</div>
                  <div className="portfolio-detail-meta">
                    {defaultPlanningTaxonomy
                      ? `${defaultPlanningTaxonomy.name}; current date ${holdingsWorkspace.as_of_date}`
                      : 'Default planning taxonomy is not configured'}
                  </div>
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
            ) : null}

            {showRiskContribution ? (
              <section className="performance-section-block">
                <div className="portfolio-detail-toolbar performance-subsection-toolbar">
                  <div className="panel-title">All-Asset Risk Contribution</div>
                  <div className="portfolio-detail-meta">
                    {riskWindowStartDate && riskWindowEndDate
                      ? `${riskWindowStartDate} to ${riskWindowEndDate}; interval returns and in-window annualized volatility`
                      : 'No active risk window'}
                  </div>
                </div>
                {renderPeriodRiskContributionTable()}
              </section>
            ) : null}
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
