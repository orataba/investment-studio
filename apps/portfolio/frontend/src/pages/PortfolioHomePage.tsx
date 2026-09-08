import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'

import CalculationStatus from '../components/CalculationStatus'
import HoldingsSectionTables, {
  DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS,
  HOLDINGS_SECTION_COLUMN_KEYS,
  type HoldingsSectionKey,
  type HoldingsSectionVisibleColumns,
} from '../components/HoldingsSectionTables'
import HoldingsSubtotalRow from '../components/HoldingsSubtotalRow'
import HoldingsOperationalStatus from '../components/HoldingsOperationalStatus'
import PortfolioTableViewControls, { type PortfolioTableViewOption } from '../components/PortfolioTableViewControls'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import { OPTION_OUTCOME_RECORDED_EVENT } from '../components/OptionOutcomePrompt'
import QualityWarningsNotice from '../components/QualityWarningsNotice'
import DownloadFormatMenu from '../../../../../packages/ui/src/DownloadFormatMenu'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import Sparkline from '../../../../../packages/ui/src/Sparkline'
import { downloadTable, type TableCell, type TableExportFormat } from '../../../../../packages/ui/src/tableExport'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import { SerialTaskQueue } from '../../../../../packages/ui/src/serialTaskQueue'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
  formatQuantity,
  formatUnitPrice,
  signedValueClass,
} from '../lib/format'
import {
  getHoldingsWorkspace,
  getPortfolioTableViewStore,
  getPortfolioTaxonomyCatalog,
  savePortfolioTableViewStore,
  type HoldingReturnSeries,
  type HoldingsWorkspaceResponse,
  type PortfolioCalculationFrequency,
  type PortfolioHoldingRow,
  type PortfolioTaxonomyAssignmentRecord,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
} from '../lib/api'
import { baseAmountForRow, normalizedCurrency } from '../lib/holdingAmounts'
import { useHorizontalTablePan } from '../lib/useHorizontalTablePan'
import {
  holdingUsesEventValuation,
  isOptionObligationHolding,
} from '../lib/holdingPresentation'
import { buildPortfolioHoldingDetailPath } from '../lib/navigation'

type HoldingsColumnKey =
  | 'instrument'
  | 'ticker'
  | 'instrument_type'
  | 'taxonomy_top'
  | 'taxonomy_leaf'
  | 'currency'
  | 'holding_date'
  | 'quantity'
  | 'cost_method'
  | 'avg_cost_book'
  | 'last_price'
  | 'quote_date'
  | 'quote_basis'
  | 'quote_provider'
  | 'quote_status'
  | 'market_value'
  | 'market_value_base'
  | 'cost_basis'
  | 'cost_basis_base'
  | 'cost_basis_historical_base'
  | 'cost_basis_fx_rate'
  | 'current_fx_rate'
  | 'net_invested'
  | 'break_even_price'
  | 'weight'
  | 'accounts'
  | 'open_lots'
  | 'unrealized_price_pnl'
  | 'unrealized_price_pnl_base'
  | 'unrealized_fx_pnl_base'
  | 'unrealized_pnl_base'
  | 'unrealized_return'
  | 'unrealized_return_base'
  | 'instrument_return_1w'
  | 'instrument_return_1m'
  | 'instrument_return_3m'
  | 'instrument_return_6m'
  | 'instrument_return_mtd'
  | 'instrument_return_ytd'
  | 'instrument_return_1y'
  | 'instrument_current_drawdown'
  | 'instrument_max_drawdown'
  | 'instrument_holding_max_drawdown'
  | 'instrument_volatility_1m'
  | 'instrument_volatility_3m'
  | 'instrument_volatility_6m'
  | 'instrument_volatility_1y'
  | 'forward_risk_share'
  | 'price_chart_1m'
  | 'price_chart_3m'
  | 'price_chart_6m'
  | 'price_chart_1y'
  | 'coverage'

type HoldingsGroupByKey =
  | 'none'
  | 'taxonomy_top'
  | 'taxonomy_leaf'
  | 'instrument_type'
  | 'currency'
  | 'coverage'
type HoldingsSortDirection = 'asc' | 'desc'
type SortableValue = number | string | null | undefined

const HOLDINGS_COLUMNS_REQUIRING_DETAILS = new Set<HoldingsColumnKey>([
  'price_chart_1m',
  'price_chart_3m',
  'price_chart_1y',
  'instrument_current_drawdown',
  'instrument_max_drawdown',
  'instrument_volatility_1m',
  'instrument_volatility_3m',
  'instrument_volatility_6m',
  'instrument_volatility_1y',
])

type HoldingTaxonomyLabels = {
  topLevelId: string
  topLevelLabel: string
  leafId: string
  leafLabel: string
}

type HoldingsColumnContext = {
  workspace: HoldingsWorkspaceResponse
  taxonomyByInstrumentId: Map<string, HoldingTaxonomyLabels>
  riskMetrics?: HoldingsGroupRiskMetrics
}

type HoldingsGroupRiskMetrics = {
  volatility: Record<GroupVolatilityRangeKey, number | null>
  currentDrawdown: number | null
  maxDrawdown: number | null
}

type HoldingsColumnDefinition = {
  key: HoldingsColumnKey
  label: string
  align?: 'right' | 'center'
  render: (row: PortfolioHoldingRow, context: HoldingsColumnContext) => ReactNode
  sortValue: (row: PortfolioHoldingRow, context: HoldingsColumnContext) => SortableValue
  className?: (row: PortfolioHoldingRow, context: HoldingsColumnContext) => string
  total?: (rows: PortfolioHoldingRow[], context: HoldingsColumnContext) => ReactNode
  totalClassName?: (rows: PortfolioHoldingRow[], context: HoldingsColumnContext) => string
}

type HoldingsGroupAggregationKind =
  | 'none'
  | 'sum'
  | 'recomputed_ratio'
  | 'current_weight_return'
  | 'current_basket_path'
  | 'portfolio_risk_contribution'

export const HOLDINGS_GROUP_AGGREGATION_KIND: Record<
  HoldingsColumnKey,
  HoldingsGroupAggregationKind
> = {
  instrument: 'none',
  ticker: 'none',
  instrument_type: 'none',
  taxonomy_top: 'none',
  taxonomy_leaf: 'none',
  currency: 'none',
  holding_date: 'none',
  quantity: 'none',
  cost_method: 'none',
  avg_cost_book: 'none',
  last_price: 'none',
  quote_date: 'none',
  quote_basis: 'none',
  quote_provider: 'none',
  quote_status: 'none',
  current_fx_rate: 'none',
  market_value: 'none',
  market_value_base: 'sum',
  cost_basis: 'none',
  cost_basis_base: 'sum',
  cost_basis_historical_base: 'sum',
  cost_basis_fx_rate: 'none',
  net_invested: 'none',
  break_even_price: 'none',
  weight: 'sum',
  accounts: 'none',
  open_lots: 'sum',
  unrealized_price_pnl: 'none',
  unrealized_price_pnl_base: 'sum',
  unrealized_fx_pnl_base: 'sum',
  unrealized_pnl_base: 'sum',
  unrealized_return: 'none',
  unrealized_return_base: 'recomputed_ratio',
  instrument_return_1w: 'current_weight_return',
  instrument_return_1m: 'current_weight_return',
  instrument_return_3m: 'current_weight_return',
  instrument_return_6m: 'current_weight_return',
  instrument_return_mtd: 'current_weight_return',
  instrument_return_ytd: 'current_weight_return',
  instrument_return_1y: 'current_weight_return',
  instrument_current_drawdown: 'current_basket_path',
  instrument_max_drawdown: 'current_basket_path',
  instrument_holding_max_drawdown: 'none',
  instrument_volatility_1m: 'current_basket_path',
  instrument_volatility_3m: 'current_basket_path',
  instrument_volatility_6m: 'current_basket_path',
  instrument_volatility_1y: 'current_basket_path',
  forward_risk_share: 'portfolio_risk_contribution',
  price_chart_1m: 'none',
  price_chart_3m: 'none',
  price_chart_6m: 'none',
  price_chart_1y: 'none',
  coverage: 'none',
}

type HoldingsGroup = {
  key: string
  label: string
  rows: PortfolioHoldingRow[]
  marketValueBase: number
  weight: number
  openLots: number
}

type GroupVolatilityRangeKey = '1m' | '3m' | '6m' | '1y'
type GroupVolatilitySeries = {
  dates: string[]
  returns: number[]
  firstReturnStartDate: string | null
}

const DAYS_PER_YEAR = 365.25
const GROUP_METRIC_REQUIRED_VALUE_COVERAGE = 1 - 1e-9
const HOLDINGS_NAV_RECONCILIATION_RELATIVE_TOLERANCE = 1e-9
const GROUP_VOL_WINDOW_MONTHS: Record<GroupVolatilityRangeKey, number> = {
  '1m': 1,
  '3m': 3,
  '6m': 6,
  '1y': 12,
}
const GROUP_VOL_MIN_WINDOW_COVERAGE_RATIO = 0.8
const GROUP_RISK_MAX_TRAILING_STALENESS_DAYS: Record<PortfolioCalculationFrequency, number> = {
  daily: 5,
}
const GROUP_VOL_MIN_RETURN_OBSERVATIONS: Record<PortfolioCalculationFrequency, Record<GroupVolatilityRangeKey, number>> = {
  daily: {
    '1m': 10,
    '3m': 30,
    '6m': 60,
    '1y': 120,
  },
}

type HoldingsViewState = {
  columns: HoldingsColumnKey[]
  columnWidths: Partial<Record<HoldingsColumnKey, number>>
  groupBy: HoldingsGroupByKey
  sortField: HoldingsColumnKey | null
  sortDirection: HoldingsSortDirection
}

type HoldingsTableView = PortfolioTableViewOption & {
  state: HoldingsViewState
  readonly?: boolean
  createdAt?: string
  updatedAt?: string
}

type HoldingsViewStore = {
  systemViewSignature: string
  activeViewId: string
  views: HoldingsTableView[]
}

const LOCKED_HOLDINGS_COLUMN: HoldingsColumnKey = 'instrument'
const HOLDINGS_COLUMN_WIDTHS_STORAGE_KEY = 'investment_studio.portfolio.holdings.columnWidths.v4'
const HOLDINGS_VIEW_AUTOSAVE_DELAY_MS = 250
const HOLDINGS_COLUMN_MIN_WIDTH = 84
const HOLDINGS_COLUMN_MAX_WIDTH = 520

const HOLDINGS_COLUMN_GROUPS: Array<{ label: string; columns: HoldingsColumnKey[] }> = [
  {
    label: 'Identity',
    columns: ['instrument', 'ticker', 'instrument_type', 'taxonomy_top', 'taxonomy_leaf', 'currency', 'coverage'],
  },
  {
    label: 'Quote',
    columns: [
      'last_price',
      'current_fx_rate',
      'quote_date',
      'quote_basis',
      'quote_provider',
      'quote_status',
    ],
  },
  {
    label: 'Instrument Trend',
    columns: [
      'price_chart_1m',
      'price_chart_3m',
      'price_chart_6m',
      'price_chart_1y',
      'instrument_return_1w',
      'instrument_return_1m',
      'instrument_return_3m',
      'instrument_return_6m',
      'instrument_return_mtd',
      'instrument_return_ytd',
      'instrument_return_1y',
    ],
  },
  {
    label: 'Position',
    columns: [
      'holding_date',
      'quantity',
      'market_value',
      'market_value_base',
      'weight',
      'accounts',
      'open_lots',
    ],
  },
  {
    label: 'Cost',
    columns: [
      'cost_method',
      'avg_cost_book',
      'cost_basis',
      'cost_basis_base',
      'cost_basis_historical_base',
      'cost_basis_fx_rate',
      'net_invested',
      'break_even_price',
    ],
  },
  {
    label: 'P&L',
    columns: [
      'unrealized_price_pnl',
      'unrealized_price_pnl_base',
      'unrealized_fx_pnl_base',
      'unrealized_pnl_base',
      'unrealized_return',
      'unrealized_return_base',
    ],
  },
  {
    label: 'Risk',
    columns: [
      'instrument_volatility_1m',
      'instrument_volatility_3m',
      'instrument_volatility_6m',
      'instrument_volatility_1y',
      'forward_risk_share',
      'instrument_current_drawdown',
      'instrument_max_drawdown',
      'instrument_holding_max_drawdown',
    ],
  },
]

const ALL_HOLDINGS_COLUMN_KEYS = HOLDINGS_COLUMN_GROUPS.flatMap((group) => group.columns)

const DEFAULT_HOLDINGS_COLUMNS: HoldingsColumnKey[] = [
  'instrument',
  'ticker',
  'currency',
  'last_price',
  'instrument_return_1m',
  'instrument_return_mtd',
  'quantity',
  'market_value_base',
  'weight',
  'avg_cost_book',
  'break_even_price',
  'unrealized_price_pnl',
  'unrealized_fx_pnl_base',
  'unrealized_pnl_base',
  'unrealized_return_base',
]

const DEFAULT_HOLDINGS_COLUMN_WIDTHS: Record<HoldingsColumnKey, number> = {
  instrument: 320,
  ticker: 120,
  instrument_type: 116,
  taxonomy_top: 152,
  taxonomy_leaf: 176,
  currency: 92,
  holding_date: 126,
  quantity: 132,
  cost_method: 118,
  avg_cost_book: 148,
  last_price: 120,
  quote_date: 126,
  quote_basis: 126,
  quote_provider: 132,
  quote_status: 120,
  current_fx_rate: 132,
  market_value: 148,
  market_value_base: 164,
  cost_basis: 148,
  cost_basis_base: 164,
  cost_basis_historical_base: 176,
  cost_basis_fx_rate: 148,
  net_invested: 156,
  break_even_price: 148,
  weight: 104,
  accounts: 104,
  open_lots: 112,
  unrealized_price_pnl: 172,
  unrealized_price_pnl_base: 184,
  unrealized_fx_pnl_base: 176,
  unrealized_pnl_base: 184,
  unrealized_return: 156,
  unrealized_return_base: 168,
  instrument_return_1w: 128,
  instrument_return_1m: 128,
  instrument_return_3m: 128,
  instrument_return_6m: 128,
  instrument_return_mtd: 112,
  instrument_return_ytd: 112,
  instrument_return_1y: 112,
  instrument_current_drawdown: 120,
  instrument_max_drawdown: 124,
  instrument_holding_max_drawdown: 136,
  instrument_volatility_1m: 112,
  instrument_volatility_3m: 112,
  instrument_volatility_6m: 112,
  instrument_volatility_1y: 112,
  forward_risk_share: 128,
  price_chart_1m: 132,
  price_chart_3m: 132,
  price_chart_6m: 132,
  price_chart_1y: 132,
  coverage: 132,
}

const COMPACT_HOLDINGS_COLUMN_MIN_WIDTHS: Partial<Record<HoldingsColumnKey, number>> = {
  instrument: 180,
  ticker: 92,
  instrument_type: 96,
  taxonomy_top: 112,
  taxonomy_leaf: 124,
  currency: 76,
  holding_date: 104,
  quantity: 96,
  cost_method: 104,
  avg_cost_book: 104,
  last_price: 92,
  quote_date: 104,
  quote_basis: 104,
  quote_provider: 104,
  quote_status: 104,
  current_fx_rate: 104,
  market_value: 108,
  market_value_base: 116,
  cost_basis: 108,
  cost_basis_base: 116,
  cost_basis_historical_base: 128,
  cost_basis_fx_rate: 112,
  net_invested: 116,
  break_even_price: 112,
  weight: 84,
  accounts: 84,
  open_lots: 88,
  unrealized_price_pnl: 124,
  unrealized_price_pnl_base: 132,
  unrealized_fx_pnl_base: 124,
  unrealized_pnl_base: 132,
  unrealized_return: 112,
  unrealized_return_base: 120,
  instrument_return_1w: 92,
  instrument_return_1m: 92,
  instrument_return_3m: 92,
  instrument_return_6m: 92,
  instrument_return_mtd: 92,
  instrument_return_ytd: 92,
  instrument_return_1y: 92,
  instrument_current_drawdown: 100,
  instrument_max_drawdown: 100,
  instrument_holding_max_drawdown: 112,
  instrument_volatility_1m: 92,
  instrument_volatility_3m: 92,
  instrument_volatility_6m: 92,
  instrument_volatility_1y: 92,
  forward_risk_share: 104,
  price_chart_1m: 104,
  price_chart_3m: 104,
  price_chart_6m: 104,
  price_chart_1y: 104,
  coverage: 104,
}

const DEFAULT_HOLDINGS_VIEW_STATE: HoldingsViewState = {
  columns: DEFAULT_HOLDINGS_COLUMNS,
  columnWidths: {},
  groupBy: 'none',
  sortField: null,
  sortDirection: 'asc',
}

const SYSTEM_HOLDINGS_VIEWS: HoldingsTableView[] = [
  {
    id: 'default',
    name: 'Default',
    readonly: true,
    state: DEFAULT_HOLDINGS_VIEW_STATE,
  },
  {
    id: 'return-risk',
    name: 'Return & Risk',
    readonly: true,
    state: {
      columns: [
        'instrument',
        'ticker',
        'currency',
        'last_price',
        'market_value_base',
        'weight',
        'price_chart_6m',
        'instrument_return_1w',
        'instrument_return_1m',
        'instrument_return_3m',
        'instrument_return_6m',
        'instrument_return_mtd',
        'instrument_return_ytd',
        'instrument_return_1y',
        'unrealized_return_base',
        'instrument_volatility_1m',
        'instrument_volatility_3m',
        'instrument_volatility_1y',
        'forward_risk_share',
        'instrument_current_drawdown',
        'instrument_max_drawdown',
        'instrument_holding_max_drawdown',
      ],
      columnWidths: {},
      groupBy: 'none',
      sortField: 'unrealized_return_base',
      sortDirection: 'desc',
    },
  },
]

const HOLDINGS_SYSTEM_VIEW_SIGNATURE = JSON.stringify(
  SYSTEM_HOLDINGS_VIEWS.map((view) => ({
    id: view.id,
    name: view.name,
    state: view.state,
  })),
)

const HOLDINGS_GROUP_BY_OPTIONS: Array<{
  value: HoldingsGroupByKey
  label: string
}> = [
  { value: 'none', label: 'None' },
  { value: 'taxonomy_top', label: 'Taxonomy' },
  { value: 'taxonomy_leaf', label: 'Taxonomy Leaf' },
  { value: 'instrument_type', label: 'Instrument Type' },
  { value: 'currency', label: 'Currency' },
  { value: 'coverage', label: 'Coverage' },
]

function primaryIdentifier(row: PortfolioHoldingRow) {
  if (!row.instrument_core) {
    return row.derivative_contract_id ?? row.position_reference_id ?? row.line_id
  }
  return (
    row.instrument_core.identifiers.find((item) => item.is_primary)?.identifier_value ??
    row.instrument_core.identifiers[0]?.identifier_value ??
    row.instrument_core.instrument_id
  )
}

function holdingName(row: PortfolioHoldingRow) {
  return row.derivative_contract?.contract_name ?? row.instrument_core?.instrument_name ?? row.line_id
}

function holdingReferenceId(row: PortfolioHoldingRow) {
  if (
    row.holding_kind === 'settled_cash' ||
    row.holding_kind === 'restricted_cash' ||
    row.holding_kind === 'pending_subscription' ||
    row.holding_kind === 'settlement_receivable' ||
    row.holding_kind === 'settlement_payable' ||
    row.holding_kind === 'position_recognition_adjustment'
  ) {
    return row.position_reference_id ?? row.instrument_core?.instrument_id ?? row.line_id
  }
  return (
    row.derivative_contract_id ??
    row.position_reference_id ??
    row.economic_instrument_id ??
    row.instrument_core?.instrument_id ??
    row.line_id
  )
}

function holdingCurrency(row: PortfolioHoldingRow) {
  return row.derivative_contract?.currency ?? row.instrument_core?.currency ?? ''
}

function holdingAssetType(row: PortfolioHoldingRow) {
  return row.derivative_contract?.contract_type ?? row.instrument_core?.instrument_type ?? 'other'
}

function instrumentTrendCoverageLabel(row: PortfolioHoldingRow) {
  const basis = row.instrument_trend_basis ? formatLabel(row.instrument_trend_basis) : 'Unavailable basis'
  const coverage = row.instrument_trend_coverage
  const state = formatLabel(coverage?.state ?? 'unavailable')
  const observationCount = coverage?.observation_count ?? 0
  return `Trend: ${basis} · ${state} · ${observationCount} observation${observationCount === 1 ? '' : 's'}`
}

function instrumentTrendReasonLabel(row: PortfolioHoldingRow) {
  const basis = row.instrument_trend_basis ? formatLabel(row.instrument_trend_basis) : 'trend basis'
  switch (row.instrument_trend_reason) {
    case 'selected_more_complete_alternate_series':
      return `Using ${basis} because it provides more complete history than the preferred basis.`
    case 'selected_alternate_total_return_series':
      return `Using the next available policy-approved total-return basis, ${basis}.`
    case 'selected_split_adjusted_raw_price_series':
      return `Raw ${basis} history was adjusted across confirmed split ratios.`
    case 'selected_series_has_single_observation':
      return 'Trend history has only one observation.'
    case 'raw_price_split_evidence_unconfirmed':
      return 'Trend withheld because split evidence is not confirmed.'
    case 'raw_price_split_ratio_invalid':
      return 'Trend withheld because the confirmed split ratio is invalid.'
    case 'raw_price_split_fraction_treatment_insufficient':
      return 'Trend withheld because split fraction treatment is insufficient.'
    case 'raw_price_split_effective_date_invalid':
      return 'Trend withheld because the split effective date is invalid.'
    case 'selected_policy_series':
      return 'Policy-preferred trend basis selected.'
    case 'quote_policy_unavailable':
    case 'quote_series_unavailable':
      return 'Trend unavailable because no eligible quote series was found.'
    case 'total_return_basis_unavailable':
      return 'Trend unavailable because shared asset data has no confirmed total-return basis for this instrument.'
    case 'total_return_series_unavailable':
      return 'Trend unavailable because the confirmed total-return series has no usable observations.'
    default:
      return row.instrument_trend_reason
        ? `Trend status: ${formatLabel(row.instrument_trend_reason)}.`
        : 'Trend selection reason unavailable.'
  }
}

function finiteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function holdingUsesModeledZeroRisk(row: PortfolioHoldingRow) {
  return row.forward_risk_status === 'modeled_zero'
}

function holdingIsExcludedFromRisk(row: PortfolioHoldingRow) {
  return row.holding_category === 'derivatives' || row.forward_risk_status === 'excluded'
}

type NormalizedCostMethod = 'fifo' | 'moving_average' | 'mixed'

function normalizeCostMethod(value: string | null | undefined): NormalizedCostMethod {
  const normalized = String(value || 'fifo').trim().toLowerCase()
  if (normalized === 'moving_average') {
    return 'moving_average'
  }
  if (normalized === 'mixed') {
    return 'mixed'
  }
  return 'fifo'
}

function costMethodLabel(value: string | null | undefined) {
  const normalized = normalizeCostMethod(value)
  if (normalized === 'moving_average') {
    return 'Moving Avg'
  }
  if (normalized === 'mixed') {
    return 'Mixed'
  }
  return 'FIFO'
}

function signedPercent(value: number | null | undefined, digits = 2) {
  const finiteValue = finiteNumber(value)
  if (finiteValue == null) {
    return '—'
  }
  if (finiteValue > 0) {
    return `+${formatPercent(finiteValue, digits)}`
  }
  if (finiteValue < 0) {
    return `-${formatPercent(Math.abs(finiteValue), digits)}`
  }
  return formatPercent(finiteValue, digits)
}

function signedCurrency(value: number | null | undefined, currency: string) {
  const finiteValue = finiteNumber(value)
  if (finiteValue == null) {
    return '—'
  }
  const formatted = formatCurrency(Math.abs(finiteValue), currency)
  if (finiteValue > 0) {
    return `+${formatted}`
  }
  if (finiteValue < 0) {
    return `-${formatted}`
  }
  return formatted
}

function holdingMarketMetric(
  row: PortfolioHoldingRow,
  value: number | null | undefined,
) {
  return holdingUsesEventValuation(row) ? null : finiteNumber(value)
}

function signedHoldingMarketMetric(
  row: PortfolioHoldingRow,
  value: number | null | undefined,
) {
  return holdingUsesEventValuation(row) ? 'N/A' : signedPercent(value)
}

function holdingModeledRiskMetric(
  row: PortfolioHoldingRow,
  value: number | null | undefined,
) {
  return holdingUsesModeledZeroRisk(row) ? 0 : holdingMarketMetric(row, value)
}

function unsignedHoldingModeledRiskMetric(
  row: PortfolioHoldingRow,
  value: number | null | undefined,
) {
  if (holdingIsExcludedFromRisk(row)) {
    return 'N/A'
  }
  const modeledValue = holdingModeledRiskMetric(row, value)
  return holdingUsesModeledZeroRisk(row) ? (
    <span title="Modeled as 0 because this holding is outside market-price risk analytics.">
      {formatPercent(modeledValue)}
    </span>
  ) : formatPercent(modeledValue)
}

function sumNumbers(rows: PortfolioHoldingRow[], accessor: (row: PortfolioHoldingRow) => number | null | undefined) {
  let hasValue = false
  const total = rows.reduce((sum, row) => {
    const value = finiteNumber(accessor(row))
    if (value == null) {
      return sum
    }
    hasValue = true
    return sum + value
  }, 0)
  return hasValue ? total : null
}

function sumCompleteNumbers(rows: PortfolioHoldingRow[], accessor: (row: PortfolioHoldingRow) => number | null | undefined) {
  if (!rows.length) {
    return null
  }
  let total = 0
  for (const row of rows) {
    const value = finiteNumber(accessor(row))
    if (value == null) {
      return null
    }
    total += value
  }
  return total
}

function isMonetaryHoldingRow(row: PortfolioHoldingRow) {
  return row.holding_category === 'cash_and_settlement'
}

function isBaseCurrencyMonetaryHoldingRow(
  row: PortfolioHoldingRow,
  workspace: HoldingsWorkspaceResponse,
) {
  return (
    isMonetaryHoldingRow(row) &&
    normalizedCurrency(holdingCurrency(row)) ===
      normalizedCurrency(workspace.base_currency)
  )
}

function holdingUsesZeroReturnRiskCapital(
  row: PortfolioHoldingRow,
  workspace: HoldingsWorkspaceResponse,
) {
  return (
    row.holding_category === 'derivatives' ||
    holdingUsesModeledZeroRisk(row) ||
    isBaseCurrencyMonetaryHoldingRow(row, workspace)
  )
}

function nonCashHoldingRows(rows: PortfolioHoldingRow[]) {
  return rows.filter((row) => !isMonetaryHoldingRow(row))
}

function costBasisHoldingRows(rows: PortfolioHoldingRow[]) {
  return rows.filter(
    (row) => !isMonetaryHoldingRow(row) && !isOptionObligationHolding(row),
  )
}

function holdingCostMethodLabel(row: PortfolioHoldingRow) {
  return finiteNumber(row.cost_basis) == null || isMonetaryHoldingRow(row) || isOptionObligationHolding(row)
    ? null
    : costMethodLabel(row.cost_basis_method)
}

function bookAvgCost(row: PortfolioHoldingRow) {
  const quantity = finiteNumber(row.quantity)
  const costBasis = finiteNumber(row.cost_basis)
  if (quantity == null || Math.abs(quantity) <= 1e-12 || costBasis == null) {
    return null
  }
  return costBasis / quantity
}

function quoteDate(row: PortfolioHoldingRow) {
  return row.quote_as_of_date ?? null
}

function chartPointsForColumn(row: PortfolioHoldingRow, column: HoldingsColumnKey) {
  if (holdingUsesEventValuation(row)) {
    return []
  }
  switch (column) {
    case 'price_chart_1m':
      return row.price_chart_1m
    case 'price_chart_3m':
      return row.price_chart_3m
    case 'price_chart_6m':
      return row.price_chart_6m
    case 'price_chart_1y':
      return row.price_chart_1y
    default:
      return []
  }
}

function isHoldingsChartColumn(column: HoldingsColumnKey) {
  return column.startsWith('price_chart_')
}

function holdingsAlignmentClass(column: HoldingsColumnDefinition) {
  if (column.align === 'right') {
    return 'numeric-cell'
  }
  if (column.align === 'center') {
    return 'center-cell'
  }
  return ''
}

function chartReturnForColumn(row: PortfolioHoldingRow, column: HoldingsColumnKey) {
  const points = chartPointsForColumn(row, column)
  if (points.length < 2) {
    return null
  }
  const firstPoint = points.find((point) => Number.isFinite(point.value) && point.value !== 0)
  const lastPoint = points[points.length - 1]
  if (!firstPoint || !lastPoint || !Number.isFinite(lastPoint.value)) {
    return null
  }
  return (lastPoint.value - firstPoint.value) / Math.abs(firstPoint.value)
}

function positionMetric(row: PortfolioHoldingRow, value: number | null | undefined) {
  return holdingUsesEventValuation(row) ? null : finiteNumber(value)
}

function holdingHasMaterialEventValuation(row: PortfolioHoldingRow) {
  if (!holdingUsesEventValuation(row)) {
    return false
  }
  return [
    row.market_value,
    row.market_value_base,
    row.cost_basis,
    row.cost_basis_base,
    row.carrying_value,
    row.carrying_value_base,
    row.liability_value,
    row.liability_value_base,
    row.premium_basis_remaining,
  ].some((value) => {
    const amount = finiteNumber(value)
    return amount != null && Math.abs(amount) > 1e-12
  })
}

function totalsContainMaterialEventValuation(rows: PortfolioHoldingRow[]) {
  return nonCashHoldingRows(rows).some(holdingHasMaterialEventValuation)
}

function totalUnrealizedBase(rows: PortfolioHoldingRow[]) {
  if (totalsContainMaterialEventValuation(rows)) {
    return null
  }
  const nonCashRows = nonCashHoldingRows(rows)
  return nonCashRows.length
    ? sumCompleteNumbers(nonCashRows, (row) => row.unrealized_pnl_base)
    : 0
}

function totalUnrealizedPct(rows: PortfolioHoldingRow[]) {
  const nonCashRows = nonCashHoldingRows(rows)
  const costBasis = sumCompleteNumbers(nonCashRows, (row) =>
    row.cost_basis_historical_base,
  )
  const unrealized = totalUnrealizedBase(rows)
  if (costBasis == null || unrealized == null || Math.abs(costBasis) <= 1e-12) {
    return null
  }
  return unrealized / Math.abs(costBasis)
}

function totalHistoricalCostBasisBase(rows: PortfolioHoldingRow[]) {
  return sumCompleteNumbers(costBasisHoldingRows(rows), (row) =>
    row.cost_basis_historical_base,
  )
}

function totalPositionMetric(
  rows: PortfolioHoldingRow[],
  accessor: (row: PortfolioHoldingRow) => number | null | undefined,
) {
  return totalsContainMaterialEventValuation(rows)
    ? null
    : sumCompleteNumbers(nonCashHoldingRows(rows), accessor)
}

function totalMarketValueBase(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumCompleteNumbers(rows, (row) =>
    baseAmountForRow(row, workspace.base_currency, row.market_value_base, row.market_value),
  )
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.market_value ?? rowTotal : rowTotal
}

function totalCostBasisBase(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumCompleteNumbers(costBasisHoldingRows(rows), (row) =>
    baseAmountForRow(row, workspace.base_currency, row.cost_basis_base, row.cost_basis),
  )
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.cost_basis ?? rowTotal : rowTotal
}

function totalAllocation(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const rowTotal = sumCompleteNumbers(rows, (row) => row.allocation)
  return rowsCoverWorkspace(rows, workspace) ? workspace.totals.allocation ?? rowTotal : rowTotal
}

function totalForwardRiskShare(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const riskRows = rows.filter((row) => !holdingIsExcludedFromRisk(row))
  if (!riskRows.length) {
    return null
  }
  if (riskRows.every(holdingUsesModeledZeroRisk)) {
    return 0
  }
  return workspace.forward_risk?.status === 'ok'
    ? sumCompleteNumbers(riskRows, (row) =>
        holdingModeledRiskMetric(row, row.forward_risk_share),
      )
    : null
}

function rowMarketValueBase(row: PortfolioHoldingRow, workspace: HoldingsWorkspaceResponse) {
  return baseAmountForRow(row, workspace.base_currency, row.market_value_base, row.market_value)
}

function returnCurrencyForRow(
  row: PortfolioHoldingRow,
  workspace: HoldingsWorkspaceResponse,
) {
  if (holdingUsesZeroReturnRiskCapital(row, workspace)) {
    return workspace.base_currency.trim().toUpperCase()
  }
  return holdingCurrency(row).trim().toUpperCase()
}

function rowsHaveCompatibleReturnCurrency(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
) {
  const currencies = new Set(
    rows
      .filter((row) => {
        const value = rowMarketValueBase(row, workspace)
        return value != null && Math.abs(value) > 1e-12
      })
      .map((row) => returnCurrencyForRow(row, workspace)),
  )
  const baseCurrency = normalizedCurrency(workspace.base_currency)
  return currencies.size === 0 || (currencies.size === 1 && currencies.has(baseCurrency))
}

function currentBasketDenominator(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
  rowNetValue: number,
) {
  if (!rowsCoverWorkspace(rows, workspace)) {
    return Math.abs(rowNetValue) > 1e-12 ? rowNetValue : null
  }
  const nav = finiteNumber(workspace.totals.nav)
  if (nav == null || Math.abs(nav) <= 1e-12) {
    return null
  }
  const reconciliationScale = Math.max(1, Math.abs(nav), Math.abs(rowNetValue))
  return Math.abs(rowNetValue - nav) <=
    reconciliationScale * HOLDINGS_NAV_RECONCILIATION_RELATIVE_TOLERANCE
    ? nav
    : null
}

function weightedHoldingMetric(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
  accessor: (row: PortfolioHoldingRow) => number | null | undefined,
) {
  if (totalsContainMaterialEventValuation(rows)) {
    return null
  }
  const metricRows = rows
  if (!metricRows.length || !rowsHaveCompatibleReturnCurrency(metricRows, workspace)) {
    return null
  }
  const rowValues = metricRows.map((row) => rowMarketValueBase(row, workspace))
  if (rowValues.some((value) => value == null)) {
    return null
  }
  const totalAbsValue = rowValues.reduce<number>(
    (sum, value) => sum + Math.abs(value ?? 0),
    0,
  )
  if (totalAbsValue <= 1e-12) {
    return null
  }
  let eligibleAbsValue = 0
  let weightedTotal = 0
  let eligibleNetValue = 0
  for (const row of metricRows) {
    const value = rowMarketValueBase(row, workspace)
    const rawMetric = finiteNumber(accessor(row))
    const metric = rawMetric ?? (isBaseCurrencyMonetaryHoldingRow(row, workspace) ? 0 : null)
    if (value == null || metric == null) {
      continue
    }
    eligibleAbsValue += Math.abs(value)
    weightedTotal += value * metric
    eligibleNetValue += value
  }
  const denominator = currentBasketDenominator(metricRows, workspace, eligibleNetValue)
  if (
    eligibleAbsValue / totalAbsValue < GROUP_METRIC_REQUIRED_VALUE_COVERAGE ||
    denominator == null
  ) {
    return null
  }
  return weightedTotal / denominator
}

function dayDiff(left: string, right: string) {
  const leftTime = Date.parse(`${left}T00:00:00`)
  const rightTime = Date.parse(`${right}T00:00:00`)
  if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) {
    return null
  }
  return Math.max(0, (rightTime - leftTime) / 86_400_000)
}

function shiftIsoCalendarMonthsForHoldings(isoDate: string, months: number) {
  const [year, month, day] = isoDate.split('-').map(Number)
  if (!year || !month || !day) {
    return null
  }
  const targetMonth = new Date(year, month - 1 + months, 1)
  const targetMonthLastDay = new Date(
    targetMonth.getFullYear(),
    targetMonth.getMonth() + 1,
    0,
  ).getDate()
  const targetDay = Math.min(day, targetMonthLastDay)
  return `${targetMonth.getFullYear()}-${String(targetMonth.getMonth() + 1).padStart(2, '0')}-${String(targetDay).padStart(2, '0')}`
}

function sampleStddev(values: number[]) {
  if (values.length < 2) {
    return null
  }
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1)
  return Math.sqrt(Math.max(0, variance))
}

function returnSeriesForVolatilityRange(row: PortfolioHoldingRow, rangeKey: GroupVolatilityRangeKey) {
  switch (rangeKey) {
    case '1m':
      return row.instrument_return_series_1m
    case '3m':
      return row.instrument_return_series_3m
    case '6m':
      return row.instrument_return_series_6m
    case '1y':
      return row.instrument_return_series_1y
    default:
      return null
  }
}

function normalizedReturnSeries(series: HoldingReturnSeries | null | undefined) {
  const points = Array.isArray(series?.points) ? series.points : []
  return points
    .map((point) => ({
      startDate: point.start_date || null,
      date: point.date,
      value: finiteNumber(point.value),
    }))
    .filter(
      (point): point is { startDate: string | null; date: string; value: number } =>
        Boolean(point.date) && point.value != null,
    )
    .sort((left, right) => `${left.startDate || ''}|${left.date}`.localeCompare(`${right.startDate || ''}|${right.date}`))
}

function calculationFrequencyForRows(rows: PortfolioHoldingRow[]): PortfolioCalculationFrequency {
  void rows
  return 'daily'
}

export function groupedReturnSeries(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
  seriesAccessor: (row: PortfolioHoldingRow) => HoldingReturnSeries | null | undefined,
  modeledZeroRisk = false,
): GroupVolatilitySeries | null {
  if (!modeledZeroRisk && totalsContainMaterialEventValuation(rows)) {
    return null
  }
  if (
    modeledZeroRisk &&
    rows.some((row) => {
      const value = rowMarketValueBase(row, workspace)
      return (
        holdingIsExcludedFromRisk(row) &&
        row.holding_category !== 'derivatives' &&
        value != null &&
        Math.abs(value) > 1e-12
      )
    })
  ) {
    return null
  }
  const returnEligibleRows = rows
  const currencyComparableRows = modeledZeroRisk
    ? returnEligibleRows.filter(
        (row) => !holdingUsesZeroReturnRiskCapital(row, workspace),
      )
    : returnEligibleRows
  if (
    !returnEligibleRows.length ||
    !rowsHaveCompatibleReturnCurrency(currencyComparableRows, workspace)
  ) {
    return null
  }
  const rowValues = returnEligibleRows.map((row) =>
    rowMarketValueBase(row, workspace),
  )
  if (rowValues.some((value) => value == null)) {
    return null
  }
  const totalAbsValue = rowValues.reduce<number>(
    (sum, value) => sum + Math.abs(value ?? 0),
    0,
  )
  if (totalAbsValue <= 1e-12) {
    return null
  }

  const valuedRows = returnEligibleRows
    .map((row) => ({
      row,
      value: rowMarketValueBase(row, workspace),
      points:
        modeledZeroRisk && holdingUsesZeroReturnRiskCapital(row, workspace)
          ? []
          : normalizedReturnSeries(seriesAccessor(row)),
    }))
    .filter((item) => item.value != null)
  const returnRows = valuedRows.filter((item) => item.points.length >= 2)
  const zeroReturnRows = valuedRows.filter(
    (item) =>
      item.points.length < 2 &&
      (isBaseCurrencyMonetaryHoldingRow(item.row, workspace) ||
        (modeledZeroRisk &&
          holdingUsesZeroReturnRiskCapital(item.row, workspace))),
  )
  const eligibleRows = [...returnRows, ...zeroReturnRows]

  const eligibleAbsValue = eligibleRows.reduce((sum, item) => sum + Math.abs(item.value ?? 0), 0)
  const eligibleNetValue = eligibleRows.reduce((sum, item) => sum + (item.value ?? 0), 0)
  const denominator = currentBasketDenominator(
    returnEligibleRows,
    workspace,
    eligibleNetValue,
  )
  if (
    eligibleAbsValue / totalAbsValue < GROUP_METRIC_REQUIRED_VALUE_COVERAGE ||
    denominator == null
  ) {
    return null
  }
  if (!returnRows.length) {
    return zeroReturnRows.length ? { dates: [], returns: [], firstReturnStartDate: null } : null
  }
  if (
    returnRows.some((item) =>
      item.points.some(
        (point, index) =>
          !point.startDate ||
          point.startDate >= point.date ||
          (index > 0 && point.startDate !== item.points[index - 1].date),
      ),
    )
  ) {
    return null
  }

  const firstPeriodStarts = returnRows.map((item) => item.points[0]?.startDate ?? null)
  if (firstPeriodStarts.some((startDate) => !startDate)) {
    return null
  }
  const completeFirstPeriodStarts = firstPeriodStarts.filter(
    (startDate): startDate is string => startDate != null,
  )
  const commonStartDate = completeFirstPeriodStarts.reduce<string>(
    (latest, startDate) => (startDate > latest ? startDate : latest),
    '',
  )
  const periodKeysByRow = returnRows.map((item) =>
    item.points
      .filter((point) => point.startDate != null && point.startDate >= commonStartDate)
      .map((point) => `${point.startDate}|${point.date}`),
  )
  const periodKeys = periodKeysByRow[0] ?? []
  if (
    !periodKeys.length ||
    periodKeysByRow.some(
      (keys) =>
        keys.length !== periodKeys.length ||
        keys.some((periodKey, index) => periodKey !== periodKeys[index]),
    )
  ) {
    return null
  }

  const pointMaps = eligibleRows.map((item) => ({
    weight: (item.value ?? 0) / denominator,
    zeroReturn:
      item.points.length < 2 &&
      (isBaseCurrencyMonetaryHoldingRow(item.row, workspace) ||
        (modeledZeroRisk &&
          holdingUsesZeroReturnRiskCapital(item.row, workspace))),
    byPeriod: new Map(item.points.map((point) => [`${point.startDate || ''}|${point.date}`, point.value])),
  }))
  const returns: number[] = []
  const returnDates: string[] = []

  for (const periodKey of periodKeys) {
    const [, dateKey] = periodKey.split('|', 2)
    if (!dateKey) {
      continue
    }
    const portfolioReturn = pointMaps.reduce((sum, item) => {
      if (item.zeroReturn) {
        return sum
      }
      const itemReturn = item.byPeriod.get(periodKey)
      if (itemReturn == null) {
        return sum
      }
      return sum + item.weight * itemReturn
    }, 0)
    if (Number.isFinite(portfolioReturn)) {
      returns.push(portfolioReturn)
      returnDates.push(dateKey)
    }
  }

  const firstReturnDate = returnDates[0]
  const firstReturnStartDate =
    periodKeys.find((periodKey) => periodKey.endsWith(`|${firstReturnDate}`))?.split('|', 2)[0] || null

  return { dates: returnDates, returns, firstReturnStartDate }
}

function groupedVolatilitySeries(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
  rangeKey: GroupVolatilityRangeKey,
) {
  return groupedReturnSeries(
    rows,
    workspace,
    (row) => returnSeriesForVolatilityRange(row, rangeKey),
    true,
  )
}

function groupedRiskSeriesIsFresh(
  series: GroupVolatilitySeries,
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
) {
  const lastDate = series.dates[series.dates.length - 1]
  if (!lastDate || lastDate > workspace.as_of_date) {
    return false
  }
  const calculationFrequency = calculationFrequencyForRows(rows)
  const trailingStalenessDays = dayDiff(lastDate, workspace.as_of_date)
  return (
    trailingStalenessDays != null &&
    trailingStalenessDays <=
      GROUP_RISK_MAX_TRAILING_STALENESS_DAYS[calculationFrequency]
  )
}

export function groupedAnnualizedVolatility(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
  rangeKey: GroupVolatilityRangeKey,
) {
  const series = groupedVolatilitySeries(rows, workspace, rangeKey)
  const calculationFrequency = calculationFrequencyForRows(rows)
  if (
    !series ||
    series.returns.length < GROUP_VOL_MIN_RETURN_OBSERVATIONS[calculationFrequency][rangeKey] ||
    series.firstReturnStartDate == null
  ) {
    return null
  }
  const lastDate = series.dates[series.dates.length - 1]
  if (!lastDate || !groupedRiskSeriesIsFresh(series, rows, workspace)) {
    return null
  }
  const elapsedDays = dayDiff(series.firstReturnStartDate, lastDate)
  if (elapsedDays == null || elapsedDays <= 0) {
    return null
  }
  const requiredStartDate = shiftIsoCalendarMonthsForHoldings(
    workspace.as_of_date,
    -GROUP_VOL_WINDOW_MONTHS[rangeKey],
  )
  const requiredWindowDays = requiredStartDate
    ? dayDiff(requiredStartDate, workspace.as_of_date)
    : null
  if (
    requiredWindowDays == null ||
    elapsedDays < Math.floor(requiredWindowDays * GROUP_VOL_MIN_WINDOW_COVERAGE_RATIO)
  ) {
    return null
  }
  const stddev = sampleStddev(series.returns)
  return stddev == null ? null : stddev * Math.sqrt((series.returns.length / elapsedDays) * DAYS_PER_YEAR)
}

function drawdownFromReturns(returns: number[]) {
  if (!returns.length) {
    return null
  }
  let value = 1
  let peak = 1
  let maxDrawdown = 0
  for (const periodReturn of returns) {
    value *= 1 + periodReturn
    peak = Math.max(peak, value)
    if (peak > 1e-12) {
      maxDrawdown = Math.min(maxDrawdown, value / peak - 1)
    }
  }
  return {
    currentDrawdown: peak > 1e-12 ? value / peak - 1 : null,
    maxDrawdown,
  }
}

function groupedDrawdownSeries(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  return groupedReturnSeries(
    rows,
    workspace,
    (row) => row.instrument_return_series_all,
    true,
  )
}

function groupedDrawdown(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  const series = groupedDrawdownSeries(rows, workspace)
  if (series && !groupedRiskSeriesIsFresh(series, rows, workspace)) {
    return null
  }
  return series ? drawdownFromReturns(series.returns) : null
}

export function groupedCurrentDrawdown(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  return groupedDrawdown(rows, workspace)?.currentDrawdown ?? null
}

export function groupedMaxDrawdown(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  return groupedDrawdown(rows, workspace)?.maxDrawdown ?? null
}

export function buildHoldingsGroupRiskMetrics(
  rows: PortfolioHoldingRow[],
  workspace: HoldingsWorkspaceResponse,
): HoldingsGroupRiskMetrics {
  const drawdown = groupedDrawdown(rows, workspace)
  return {
    volatility: {
      '1m': groupedAnnualizedVolatility(rows, workspace, '1m'),
      '3m': groupedAnnualizedVolatility(rows, workspace, '3m'),
      '6m': groupedAnnualizedVolatility(rows, workspace, '6m'),
      '1y': groupedAnnualizedVolatility(rows, workspace, '1y'),
    },
    currentDrawdown: drawdown?.currentDrawdown ?? null,
    maxDrawdown: drawdown?.maxDrawdown ?? null,
  }
}

function rowsCoverWorkspace(rows: PortfolioHoldingRow[], workspace: HoldingsWorkspaceResponse) {
  if (rows.length !== workspace.rows.length) {
    return false
  }
  const rowIds = new Set(rows.map((row) => row.line_id))
  return workspace.rows.every((row) => rowIds.has(row.line_id))
}

function resolveNodePath(
  taxonomyNodeId: string,
  nodesById: Map<string, PortfolioTaxonomyNodeRecord>,
) {
  const path: PortfolioTaxonomyNodeRecord[] = []
  let cursor: PortfolioTaxonomyNodeRecord | undefined = nodesById.get(taxonomyNodeId)
  const seen = new Set<string>()
  while (cursor && !seen.has(cursor.taxonomy_node_id)) {
    path.unshift(cursor)
    seen.add(cursor.taxonomy_node_id)
    cursor = cursor.parent_taxonomy_node_id
      ? nodesById.get(cursor.parent_taxonomy_node_id)
      : undefined
  }
  return path
}

function resolveGroupingTaxonomy(catalog: PortfolioTaxonomyCatalogResponse | null, selectedTaxonomyId?: string) {
  const taxonomies = (catalog?.taxonomies ?? []).filter(
    (taxonomy) => taxonomy.status === 'active',
  )
  return (
    taxonomies.find((taxonomy) => taxonomy.taxonomy_id === selectedTaxonomyId) ??
    taxonomies.find(
      (taxonomy) => taxonomy.taxonomy_id === catalog?.default_planning_taxonomy_id,
    ) ??
    taxonomies.find((taxonomy) => taxonomy.planning_enabled) ??
    taxonomies[0] ??
    null
  )
}

function labelsForTaxonomyAssignment(
  assignment: PortfolioTaxonomyAssignmentRecord,
  nodesById: Map<string, PortfolioTaxonomyNodeRecord>,
  taxonomyId: string,
): HoldingTaxonomyLabels {
  const leafNode = nodesById.get(assignment.taxonomy_node_id) ?? null
  const path = leafNode ? resolveNodePath(leafNode.taxonomy_node_id, nodesById) : []
  const topLevelNode = path[0] ?? leafNode
  return {
    topLevelId: topLevelNode?.taxonomy_node_id ?? `unassigned:${taxonomyId}`,
    topLevelLabel: topLevelNode?.node_name ?? 'Unassigned',
    leafId: leafNode?.taxonomy_node_id ?? `unassigned:${taxonomyId}`,
    leafLabel: leafNode?.node_name ?? 'Unassigned',
  }
}

function buildTaxonomyLabelsByInstrumentId(
  catalog: PortfolioTaxonomyCatalogResponse | null,
  selectedTaxonomyId?: string,
) {
  const taxonomy = resolveGroupingTaxonomy(catalog, selectedTaxonomyId)
  if (!catalog || !taxonomy) {
    return new Map<string, HoldingTaxonomyLabels>()
  }

  const nodesById = new Map<string, PortfolioTaxonomyNodeRecord>(
    catalog.taxonomy_nodes
      .filter(
        (node) =>
          node.taxonomy_id === taxonomy.taxonomy_id && node.status === 'active',
      )
      .map((node) => [node.taxonomy_node_id, node]),
  )

  const labelsByInstrumentId = new Map<string, HoldingTaxonomyLabels>()
  ;[...catalog.taxonomy_assignments]
    .filter(
      (assignment) =>
        assignment.taxonomy_id === taxonomy.taxonomy_id &&
        assignment.target_scope === 'instrument' &&
        assignment.status === 'active',
    )
    .sort((left, right) => right.assignment_id.localeCompare(left.assignment_id))
    .forEach((assignment) => {
      if (!labelsByInstrumentId.has(assignment.target_entity_id)) {
        labelsByInstrumentId.set(
          assignment.target_entity_id,
          labelsForTaxonomyAssignment(assignment, nodesById, taxonomy.taxonomy_id),
        )
      }
    })

  return labelsByInstrumentId
}

function taxonomyLabelsForHoldingRow(
  row: PortfolioHoldingRow,
  taxonomyByInstrumentId: Map<string, HoldingTaxonomyLabels>,
) {
  if (row.holding_category !== 'securities' || !row.instrument_core) {
    return null
  }
  return taxonomyByInstrumentId.get(row.instrument_core.instrument_id) ?? null
}

function taxonomyDisplayLabel(
  row: PortfolioHoldingRow,
  taxonomyByInstrumentId: Map<string, HoldingTaxonomyLabels>,
  level: 'top' | 'leaf',
) {
  if (row.holding_category !== 'securities') {
    return 'N/A'
  }
  const labels = taxonomyLabelsForHoldingRow(row, taxonomyByInstrumentId)
  return level === 'top'
    ? labels?.topLevelLabel ?? 'Unassigned'
    : labels?.leafLabel ?? 'Unassigned'
}

function normalizeHoldingsColumns(columns: HoldingsColumnKey[]) {
  const seen = new Set<HoldingsColumnKey>()
  const normalized: HoldingsColumnKey[] = [LOCKED_HOLDINGS_COLUMN]
  columns.forEach((column) => {
    if (column === LOCKED_HOLDINGS_COLUMN || seen.has(column) || !ALL_HOLDINGS_COLUMN_KEYS.includes(column)) {
      return
    }
    seen.add(column)
    normalized.push(column)
  })
  return normalized
}

function clampHoldingsColumnWidth(value: number) {
  return Math.min(Math.max(value, HOLDINGS_COLUMN_MIN_WIDTH), HOLDINGS_COLUMN_MAX_WIDTH)
}

function compactTableColumnWidths<T extends string>(
  columns: T[],
  getRequestedWidth: (column: T) => number,
  getMinimumWidth: (column: T) => number,
  availableWidth: number,
) {
  const specs = columns.map((column) => {
    const minWidth = getMinimumWidth(column)
    const requestedWidth = Math.max(getRequestedWidth(column), minWidth)
    return { column, minWidth, requestedWidth }
  })
  const requestedWidth = specs.reduce((total, spec) => total + spec.requestedWidth, 0)
  const minimumWidth = specs.reduce((total, spec) => total + spec.minWidth, 0)
  const targetWidth =
    availableWidth > 0 && requestedWidth > availableWidth
      ? Math.max(minimumWidth, availableWidth)
      : requestedWidth
  const widths = {} as Record<T, number>

  if (targetWidth >= requestedWidth || requestedWidth <= minimumWidth) {
    specs.forEach((spec) => {
      widths[spec.column] = Math.round(spec.requestedWidth)
    })
  } else {
    const flexibleWidth = requestedWidth - minimumWidth
    specs.forEach((spec) => {
      const share = (spec.requestedWidth - spec.minWidth) / flexibleWidth
      widths[spec.column] = Math.round(spec.minWidth + (targetWidth - minimumWidth) * share)
    })
  }

  return {
    widths,
    totalWidth: columns.reduce((total, column) => total + widths[column], 0),
  }
}

function normalizeHoldingsColumnWidths(value: unknown) {
  const widths: Partial<Record<HoldingsColumnKey, number>> = {}
  if (!value || typeof value !== 'object') {
    return widths
  }
  Object.entries(value as Record<string, unknown>).forEach(([key, rawWidth]) => {
    if (!ALL_HOLDINGS_COLUMN_KEYS.includes(key as HoldingsColumnKey) || typeof rawWidth !== 'number' || !Number.isFinite(rawWidth)) {
      return
    }
    widths[key as HoldingsColumnKey] = clampHoldingsColumnWidth(rawWidth)
  })
  return widths
}

function loadStoredHoldingsColumnWidths() {
  if (typeof window === 'undefined') {
    return {}
  }
  try {
    const rawValue = window.localStorage.getItem(HOLDINGS_COLUMN_WIDTHS_STORAGE_KEY)
    return normalizeHoldingsColumnWidths(rawValue ? JSON.parse(rawValue) : null)
  } catch {
    return {}
  }
}

function normalizeHoldingsViewState(value: unknown): HoldingsViewState {
  if (!value || typeof value !== 'object') {
    return DEFAULT_HOLDINGS_VIEW_STATE
  }
  const record = value as Partial<HoldingsViewState>
  const sortField = parseHoldingsSortField(typeof record.sortField === 'string' ? record.sortField : null)
  return {
    columns: normalizeHoldingsColumns(Array.isArray(record.columns) ? (record.columns as HoldingsColumnKey[]) : DEFAULT_HOLDINGS_COLUMNS),
    columnWidths: normalizeHoldingsColumnWidths(record.columnWidths),
    groupBy: parseHoldingsGroupBy(typeof record.groupBy === 'string' ? record.groupBy : null),
    sortField,
    sortDirection: parseHoldingsSortDirection(typeof record.sortDirection === 'string' ? record.sortDirection : null),
  }
}

function serializeHoldingsViewState(value: HoldingsViewState) {
  const normalized = normalizeHoldingsViewState(value)
  const normalizedWidths = normalizeHoldingsColumnWidths(normalized.columnWidths)
  const columnWidths = ALL_HOLDINGS_COLUMN_KEYS.reduce<Partial<Record<HoldingsColumnKey, number>>>((result, column) => {
    const width = normalizedWidths[column]
    if (width != null) {
      result[column] = width
    }
    return result
  }, {})
  return JSON.stringify({
    columns: normalized.columns,
    columnWidths,
    groupBy: normalized.groupBy,
    sortField: normalized.sortField,
    sortDirection: normalized.sortDirection,
  })
}

function holdingsViewStatesEqual(left: HoldingsViewState, right: HoldingsViewState) {
  return serializeHoldingsViewState(left) === serializeHoldingsViewState(right)
}

function normalizeHoldingsTableView(value: unknown, readonly: boolean): HoldingsTableView | null {
  if (!value || typeof value !== 'object') {
    return null
  }
  const view = value as Partial<HoldingsTableView>
  if (typeof view.id !== 'string' || !view.id.trim()) {
    return null
  }
  return {
    id: view.id,
    name: typeof view.name === 'string' && view.name.trim() ? view.name.trim() : 'Custom View',
    description: typeof view.description === 'string' ? view.description : null,
    readonly,
    createdAt: typeof view.createdAt === 'string' ? view.createdAt : undefined,
    updatedAt: typeof view.updatedAt === 'string' ? view.updatedAt : undefined,
    state: normalizeHoldingsViewState(view.state),
  }
}

function normalizeHoldingsViewStore(value: unknown): HoldingsViewStore {
  const record = value && typeof value === 'object' ? (value as Partial<HoldingsViewStore>) : {}
  const storedViews = Array.isArray(record.views)
    ? record.views
        .map((view) => normalizeHoldingsTableView(view, false))
        .filter((view): view is HoldingsTableView => view !== null)
    : []
  const storedViewById = new Map(storedViews.map((view) => [view.id, view]))
  const storedSystemViewsAreCurrent =
    record.systemViewSignature === HOLDINGS_SYSTEM_VIEW_SIGNATURE
  const systemViews = SYSTEM_HOLDINGS_VIEWS.map((systemView) => {
    const storedView = storedViewById.get(systemView.id)
    return storedSystemViewsAreCurrent && storedView
      ? {
          ...systemView,
          state: storedView.state,
          updatedAt: storedView.updatedAt,
        }
      : systemView
  })
  const customViews = storedViews
    .filter((view) => view.id.startsWith('custom:'))
    .map((view) => ({ ...view, readonly: false }))
  const views = [...systemViews, ...customViews]
  const knownViewIds = new Set(views.map((view) => view.id))
  const activeViewId =
    typeof record.activeViewId === 'string' && knownViewIds.has(record.activeViewId)
      ? record.activeViewId
      : SYSTEM_HOLDINGS_VIEWS[0].id
  return {
    systemViewSignature: HOLDINGS_SYSTEM_VIEW_SIGNATURE,
    activeViewId,
    views,
  }
}

function getHoldingsViews(store: HoldingsViewStore) {
  return store.views
}

function getHoldingsViewById(store: HoldingsViewStore, viewId: string) {
  return getHoldingsViews(store).find((view) => view.id === viewId) ?? SYSTEM_HOLDINGS_VIEWS[0]
}

function resolveHoldingsViewState(store: HoldingsViewStore, viewId: string) {
  return getHoldingsViewById(store, viewId).state
}

function createHoldingsViewId() {
  return `custom:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 8)}`
}

function parseHoldingsSortField(value: string | null): HoldingsColumnKey | null {
  return value && ALL_HOLDINGS_COLUMN_KEYS.includes(value as HoldingsColumnKey)
    ? (value as HoldingsColumnKey)
    : null
}

function parseHoldingsSortDirection(value: string | null): HoldingsSortDirection {
  if (value === 'asc' || value === 'desc') {
    return value
  }
  return 'asc'
}

function parseHoldingsGroupBy(value: string | null): HoldingsGroupByKey {
  return HOLDINGS_GROUP_BY_OPTIONS.some((option) => option.value === value)
    ? (value as HoldingsGroupByKey)
    : DEFAULT_HOLDINGS_VIEW_STATE.groupBy
}

function compareSortableValue(left: SortableValue, right: SortableValue, direction: HoldingsSortDirection) {
  const leftMissing = left == null || left === ''
  const rightMissing = right == null || right === ''
  if (leftMissing || rightMissing) {
    if (leftMissing && rightMissing) {
      return 0
    }
    return leftMissing ? 1 : -1
  }

  if (typeof left === 'number' || typeof right === 'number') {
    const result = Number(left) - Number(right)
    return direction === 'asc' ? result : -result
  }

  const result = String(left).localeCompare(String(right), 'zh-Hans-CN')
  return direction === 'asc' ? result : -result
}

function holdingColumnExportValue(
  column: HoldingsColumnKey,
  row: PortfolioHoldingRow,
  context: HoldingsColumnContext,
): string | number | null {
  switch (column) {
    case 'instrument':
      return holdingName(row)
    case 'ticker':
      return primaryIdentifier(row)
    case 'instrument_type':
      return formatLabel(holdingAssetType(row))
    case 'taxonomy_top':
      return taxonomyDisplayLabel(row, context.taxonomyByInstrumentId, 'top')
    case 'taxonomy_leaf':
      return taxonomyDisplayLabel(row, context.taxonomyByInstrumentId, 'leaf')
    case 'currency':
      return holdingCurrency(row)
    case 'holding_date':
      return row.instrument_holding_start_date ?? null
    case 'quantity':
      return row.quantity
    case 'cost_method':
      return holdingCostMethodLabel(row)
    case 'avg_cost_book':
      return bookAvgCost(row)
    case 'last_price':
      return row.last_price
    case 'quote_date':
      return quoteDate(row)
    case 'quote_basis':
      return row.quote_basis ?? null
    case 'quote_provider':
      return row.quote_provider ?? null
    case 'quote_status':
      return row.quote_status ?? null
    case 'current_fx_rate':
      return row.cost_basis_current_fx_rate_to_base ?? null
    case 'market_value':
      return row.market_value
    case 'market_value_base':
      return baseAmountForRow(row, context.workspace.base_currency, row.market_value_base, row.market_value)
    case 'cost_basis':
      return row.cost_basis
    case 'cost_basis_base':
      return baseAmountForRow(row, context.workspace.base_currency, row.cost_basis_base, row.cost_basis)
    case 'cost_basis_historical_base':
      return row.cost_basis_historical_base ?? null
    case 'cost_basis_fx_rate':
      return row.cost_basis_fx_rate_to_base ?? null
    case 'net_invested':
      return row.net_invested ?? null
    case 'break_even_price':
      return row.break_even_price ?? null
    case 'weight':
      return row.allocation
    case 'accounts':
      return row.account_count ?? 0
    case 'open_lots':
      return row.open_position_lot_count ?? 0
    case 'unrealized_price_pnl':
      return positionMetric(row, row.unrealized_price_pnl) ?? 'N/A'
    case 'unrealized_price_pnl_base':
      return positionMetric(row, row.unrealized_price_pnl_base) ?? 'N/A'
    case 'unrealized_fx_pnl_base':
      return positionMetric(row, row.unrealized_fx_pnl_base) ?? 'N/A'
    case 'unrealized_pnl_base':
      return positionMetric(row, row.unrealized_pnl_base) ?? 'N/A'
    case 'unrealized_return':
      return positionMetric(row, row.unrealized_return) ?? 'N/A'
    case 'unrealized_return_base':
      return positionMetric(row, row.unrealized_return_base) ?? 'N/A'
    case 'instrument_return_1w':
      return holdingUsesEventValuation(row) ? 'N/A' : row.instrument_return_1w ?? null
    case 'instrument_return_1m':
      return holdingUsesEventValuation(row) ? 'N/A' : row.instrument_return_1m ?? null
    case 'instrument_return_3m':
      return holdingUsesEventValuation(row) ? 'N/A' : row.instrument_return_3m ?? null
    case 'instrument_return_6m':
      return holdingUsesEventValuation(row) ? 'N/A' : row.instrument_return_6m ?? null
    case 'instrument_return_mtd':
      return holdingUsesEventValuation(row) ? 'N/A' : row.instrument_return_mtd ?? null
    case 'instrument_return_ytd':
      return holdingUsesEventValuation(row) ? 'N/A' : row.instrument_return_ytd ?? null
    case 'instrument_return_1y':
      return holdingUsesEventValuation(row) ? 'N/A' : row.instrument_return_1y ?? null
    case 'instrument_current_drawdown':
      return holdingUsesEventValuation(row) ? 'N/A' : row.instrument_current_drawdown ?? null
    case 'instrument_max_drawdown':
      return holdingUsesEventValuation(row) ? 'N/A' : row.instrument_max_drawdown ?? null
    case 'instrument_holding_max_drawdown':
      return holdingUsesEventValuation(row) ? 'N/A' : row.instrument_holding_max_drawdown ?? null
    case 'instrument_volatility_1m':
      return holdingIsExcludedFromRisk(row) ? 'N/A' : holdingModeledRiskMetric(row, row.instrument_volatility_1m)
    case 'instrument_volatility_3m':
      return holdingIsExcludedFromRisk(row) ? 'N/A' : holdingModeledRiskMetric(row, row.instrument_volatility_3m)
    case 'instrument_volatility_6m':
      return holdingIsExcludedFromRisk(row) ? 'N/A' : holdingModeledRiskMetric(row, row.instrument_volatility_6m)
    case 'instrument_volatility_1y':
      return holdingIsExcludedFromRisk(row) ? 'N/A' : holdingModeledRiskMetric(row, row.instrument_volatility_1y)
    case 'forward_risk_share':
      return holdingIsExcludedFromRisk(row) ? 'N/A' : holdingModeledRiskMetric(row, row.forward_risk_share)
    case 'price_chart_1m':
    case 'price_chart_3m':
    case 'price_chart_6m':
    case 'price_chart_1y':
      return holdingUsesEventValuation(row)
        ? 'N/A'
        : chartPointsForColumn(row, column).map((point) => `${point.date}:${point.value}`).join(' | ')
    case 'coverage':
      return formatLabel(row.coverage_status)
    default:
      return null
  }
}

function holdingColumnSubtotalExportValue(
  column: HoldingsColumnKey,
  rows: PortfolioHoldingRow[],
  context: HoldingsColumnContext,
): string | number | null {
  if (column === 'instrument') {
    return `Securities Subtotal (${context.workspace.base_currency})`
  }
  if (HOLDINGS_GROUP_AGGREGATION_KIND[column] === 'none') {
    return null
  }
  switch (column) {
    case 'market_value_base':
      return totalMarketValueBase(rows, context.workspace)
    case 'cost_basis_base':
      return totalCostBasisBase(rows, context.workspace)
    case 'cost_basis_historical_base':
      return totalHistoricalCostBasisBase(rows)
    case 'weight':
      return totalAllocation(rows, context.workspace)
    case 'open_lots':
      return sumNumbers(rows, (row) => row.open_position_lot_count)
    case 'unrealized_price_pnl_base':
      return totalPositionMetric(rows, (row) => row.unrealized_price_pnl_base) ?? 'N/A'
    case 'unrealized_fx_pnl_base':
      return totalPositionMetric(rows, (row) => row.unrealized_fx_pnl_base) ?? 'N/A'
    case 'unrealized_pnl_base':
      return totalUnrealizedBase(rows) ?? 'N/A'
    case 'unrealized_return_base':
      return totalsContainMaterialEventValuation(rows)
        ? 'N/A'
        : totalUnrealizedPct(rows)
    case 'instrument_return_1w':
      return weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1w)
    case 'instrument_return_1m':
      return weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1m)
    case 'instrument_return_3m':
      return weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_3m)
    case 'instrument_return_6m':
      return weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_6m)
    case 'instrument_return_mtd':
      return weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_mtd)
    case 'instrument_return_ytd':
      return weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_ytd)
    case 'instrument_return_1y':
      return weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1y)
    case 'instrument_current_drawdown':
      return context.riskMetrics?.currentDrawdown ?? null
    case 'instrument_volatility_1m':
      return rows.every(holdingIsExcludedFromRisk)
        ? 'N/A'
        : context.riskMetrics?.volatility['1m'] ?? null
    case 'instrument_volatility_3m':
      return rows.every(holdingIsExcludedFromRisk)
        ? 'N/A'
        : context.riskMetrics?.volatility['3m'] ?? null
    case 'instrument_volatility_6m':
      return rows.every(holdingIsExcludedFromRisk)
        ? 'N/A'
        : context.riskMetrics?.volatility['6m'] ?? null
    case 'instrument_volatility_1y':
      return rows.every(holdingIsExcludedFromRisk)
        ? 'N/A'
        : context.riskMetrics?.volatility['1y'] ?? null
    case 'forward_risk_share':
      return rows.every(holdingIsExcludedFromRisk)
        ? 'N/A'
        : totalForwardRiskShare(rows, context.workspace)
    case 'instrument_max_drawdown':
      return context.riskMetrics?.maxDrawdown ?? null
    case 'instrument_holding_max_drawdown':
      return null
    default:
      return null
  }
}

const HOLDINGS_COLUMN_DEFINITIONS: Record<HoldingsColumnKey, HoldingsColumnDefinition> = {
  instrument: {
    key: 'instrument',
    label: 'Instrument',
    render: (row) => (
      <div
        className="holding-name-stack"
        title={`${instrumentTrendCoverageLabel(row)}. ${instrumentTrendReasonLabel(row)}`}
      >
        <span>{holdingName(row)}</span>
      </div>
    ),
    sortValue: (row) => holdingName(row),
  },
  ticker: {
    key: 'ticker',
    label: 'Ticker',
    align: 'center',
    render: (row) => <span className="ticker-pill">{primaryIdentifier(row)}</span>,
    sortValue: (row) => primaryIdentifier(row),
  },
  instrument_type: {
    key: 'instrument_type',
    label: 'Instrument Type',
    align: 'center',
    render: (row) =>
      row.holding_kind === 'pending_subscription'
        ? 'Pending Subscription'
        : row.holding_kind === 'settlement_receivable'
          ? 'Settlement Receivable'
          : row.holding_kind === 'settlement_payable'
            ? 'Settlement Payable'
            : row.holding_kind === 'position_recognition_adjustment'
              ? 'Recognition Adjustment'
              : formatLabel(holdingAssetType(row)),
    sortValue: (row) => row.holding_kind ?? holdingAssetType(row),
  },
  taxonomy_top: {
    key: 'taxonomy_top',
    label: 'Taxonomy',
    render: (row, context) => taxonomyDisplayLabel(row, context.taxonomyByInstrumentId, 'top'),
    sortValue: (row, context) => taxonomyDisplayLabel(row, context.taxonomyByInstrumentId, 'top'),
  },
  taxonomy_leaf: {
    key: 'taxonomy_leaf',
    label: 'Taxonomy Leaf',
    render: (row, context) => taxonomyDisplayLabel(row, context.taxonomyByInstrumentId, 'leaf'),
    sortValue: (row, context) => taxonomyDisplayLabel(row, context.taxonomyByInstrumentId, 'leaf'),
  },
  currency: {
    key: 'currency',
    label: 'Currency',
    align: 'center',
    render: (row) => holdingCurrency(row),
    sortValue: (row) => holdingCurrency(row),
  },
  holding_date: {
    key: 'holding_date',
    label: 'Holding Since',
    align: 'center',
    render: (row) => row.instrument_holding_start_date ?? '—',
    sortValue: (row) => row.instrument_holding_start_date,
  },
  quantity: {
    key: 'quantity',
    label: 'Quantity',
    align: 'right',
    render: (row) => formatQuantity(row.quantity),
    sortValue: (row) => row.quantity,
  },
  cost_method: {
    key: 'cost_method',
    label: 'Cost Method',
    align: 'center',
    render: (row) => holdingCostMethodLabel(row) ?? '—',
    sortValue: (row) => holdingCostMethodLabel(row),
  },
  avg_cost_book: {
    key: 'avg_cost_book',
    label: 'Book Avg Cost',
    align: 'right',
    render: (row) => formatUnitPrice(bookAvgCost(row), holdingCurrency(row)),
    sortValue: (row) => bookAvgCost(row),
  },
  last_price: {
    key: 'last_price',
    label: 'Quote',
    align: 'right',
    render: (row) => formatUnitPrice(row.last_price, holdingCurrency(row)),
    sortValue: (row) => row.last_price,
  },
  quote_date: {
    key: 'quote_date',
    label: 'Quote Date',
    align: 'center',
    render: (row) => quoteDate(row) ?? '—',
    sortValue: (row) => quoteDate(row),
  },
  quote_basis: {
    key: 'quote_basis',
    label: 'Quote Basis',
    align: 'center',
    render: (row) => (row.quote_basis ? formatLabel(row.quote_basis) : '—'),
    sortValue: (row) => row.quote_basis,
  },
  quote_provider: {
    key: 'quote_provider',
    label: 'Provider',
    align: 'center',
    render: (row) => row.quote_provider ?? '—',
    sortValue: (row) => row.quote_provider,
  },
  quote_status: {
    key: 'quote_status',
    label: 'Quote Status',
    align: 'center',
    render: (row) => (row.quote_status ? formatLabel(row.quote_status) : '—'),
    sortValue: (row) => row.quote_status,
  },
  current_fx_rate: {
    key: 'current_fx_rate',
    label: 'Current FX to Base',
    align: 'right',
    render: (row) => formatNumber(row.cost_basis_current_fx_rate_to_base, 6),
    sortValue: (row) => row.cost_basis_current_fx_rate_to_base,
  },
  market_value: {
    key: 'market_value',
    label: 'Position Value (Local)',
    align: 'right',
    render: (row) => formatCurrency(row.market_value, holdingCurrency(row)),
    sortValue: (row, context) => baseAmountForRow(row, context.workspace.base_currency, row.market_value_base, row.market_value),
  },
  market_value_base: {
    key: 'market_value_base',
    label: 'Position Value (Base)',
    align: 'right',
    render: (row, context) =>
      formatCurrency(baseAmountForRow(row, context.workspace.base_currency, row.market_value_base, row.market_value), context.workspace.base_currency),
    sortValue: (row, context) => baseAmountForRow(row, context.workspace.base_currency, row.market_value_base, row.market_value),
    total: (rows, context) => formatCurrency(totalMarketValueBase(rows, context.workspace), context.workspace.base_currency),
  },
  cost_basis: {
    key: 'cost_basis',
    label: 'Book Cost (Local)',
    align: 'right',
    render: (row) => formatCurrency(row.cost_basis, holdingCurrency(row)),
    sortValue: (row, context) => baseAmountForRow(row, context.workspace.base_currency, row.cost_basis_base, row.cost_basis),
  },
  cost_basis_base: {
    key: 'cost_basis_base',
    label: 'Book Cost (Base, Current FX)',
    align: 'right',
    render: (row, context) =>
      formatCurrency(baseAmountForRow(row, context.workspace.base_currency, row.cost_basis_base, row.cost_basis), context.workspace.base_currency),
    sortValue: (row, context) => baseAmountForRow(row, context.workspace.base_currency, row.cost_basis_base, row.cost_basis),
    total: (rows, context) => formatCurrency(totalCostBasisBase(rows, context.workspace), context.workspace.base_currency),
  },
  cost_basis_historical_base: {
    key: 'cost_basis_historical_base',
    label: 'Book Cost (Base, Trade FX)',
    align: 'right',
    render: (row, context) =>
      formatCurrency(row.cost_basis_historical_base, context.workspace.base_currency),
    sortValue: (row) => row.cost_basis_historical_base,
    total: (rows, context) =>
      formatCurrency(totalHistoricalCostBasisBase(rows), context.workspace.base_currency),
  },
  cost_basis_fx_rate: {
    key: 'cost_basis_fx_rate',
    label: 'Weighted Cost FX',
    align: 'right',
    render: (row) => formatNumber(row.cost_basis_fx_rate_to_base, 6),
    sortValue: (row) => row.cost_basis_fx_rate_to_base,
  },
  net_invested: {
    key: 'net_invested',
    label: 'Net Invested (Local)',
    align: 'right',
    render: (row) => formatCurrency(row.net_invested, holdingCurrency(row)),
    sortValue: (row) => row.net_invested,
  },
  break_even_price: {
    key: 'break_even_price',
    label: 'Break-even Price',
    align: 'right',
    render: (row) => formatUnitPrice(row.break_even_price, holdingCurrency(row)),
    sortValue: (row) => row.break_even_price,
  },
  weight: {
    key: 'weight',
    label: 'Weight',
    align: 'right',
    render: (row) => formatPercent(row.allocation),
    sortValue: (row) => row.allocation,
    total: (rows, context) => formatPercent(totalAllocation(rows, context.workspace)),
  },
  accounts: {
    key: 'accounts',
    label: 'Accounts',
    align: 'right',
    render: (row) => formatNumber(row.account_count ?? 0, 0),
    sortValue: (row) => row.account_count,
  },
  open_lots: {
    key: 'open_lots',
    label: 'Open Lots',
    align: 'right',
    render: (row) => formatNumber(row.open_position_lot_count ?? 0, 0),
    sortValue: (row) => row.open_position_lot_count,
    total: (rows) => formatNumber(sumNumbers(rows, (row) => row.open_position_lot_count), 0),
  },
  unrealized_price_pnl: {
    key: 'unrealized_price_pnl',
    label: 'Price P&L (Local)',
    align: 'right',
    render: (row) =>
      signedCurrency(positionMetric(row, row.unrealized_price_pnl), holdingCurrency(row)),
    sortValue: (row) => positionMetric(row, row.unrealized_price_pnl),
    className: (row) => signedValueClass(positionMetric(row, row.unrealized_price_pnl)),
  },
  unrealized_price_pnl_base: {
    key: 'unrealized_price_pnl_base',
    label: 'Price P&L (Base)',
    align: 'right',
    render: (row, context) =>
      signedCurrency(
        positionMetric(row, row.unrealized_price_pnl_base),
        context.workspace.base_currency,
      ),
    sortValue: (row) => positionMetric(row, row.unrealized_price_pnl_base),
    className: (row) => signedValueClass(positionMetric(row, row.unrealized_price_pnl_base)),
    total: (rows, context) =>
      signedCurrency(
        totalPositionMetric(rows, (row) => row.unrealized_price_pnl_base),
        context.workspace.base_currency,
      ),
    totalClassName: (rows) =>
      signedValueClass(totalPositionMetric(rows, (row) => row.unrealized_price_pnl_base)),
  },
  unrealized_fx_pnl_base: {
    key: 'unrealized_fx_pnl_base',
    label: 'FX P&L (Base)',
    align: 'right',
    render: (row, context) =>
      signedCurrency(
        positionMetric(row, row.unrealized_fx_pnl_base),
        context.workspace.base_currency,
      ),
    sortValue: (row) => positionMetric(row, row.unrealized_fx_pnl_base),
    className: (row) => signedValueClass(positionMetric(row, row.unrealized_fx_pnl_base)),
    total: (rows, context) =>
      signedCurrency(
        totalPositionMetric(rows, (row) => row.unrealized_fx_pnl_base),
        context.workspace.base_currency,
      ),
    totalClassName: (rows) =>
      signedValueClass(totalPositionMetric(rows, (row) => row.unrealized_fx_pnl_base)),
  },
  unrealized_pnl_base: {
    key: 'unrealized_pnl_base',
    label: 'Total Unrealized P&L (Base)',
    align: 'right',
    render: (row, context) =>
      signedCurrency(
        positionMetric(row, row.unrealized_pnl_base),
        context.workspace.base_currency,
      ),
    sortValue: (row) => positionMetric(row, row.unrealized_pnl_base),
    className: (row) => signedValueClass(positionMetric(row, row.unrealized_pnl_base)),
    total: (rows, context) =>
      signedCurrency(totalUnrealizedBase(rows), context.workspace.base_currency),
    totalClassName: (rows) => signedValueClass(totalUnrealizedBase(rows)),
  },
  unrealized_return: {
    key: 'unrealized_return',
    label: 'Price Return (Local)',
    align: 'right',
    render: (row) => signedPercent(positionMetric(row, row.unrealized_return)),
    sortValue: (row) => positionMetric(row, row.unrealized_return),
    className: (row) => signedValueClass(positionMetric(row, row.unrealized_return)),
  },
  unrealized_return_base: {
    key: 'unrealized_return_base',
    label: 'Total Unrealized Return (Base)',
    align: 'right',
    render: (row) => signedPercent(positionMetric(row, row.unrealized_return_base)),
    sortValue: (row) => positionMetric(row, row.unrealized_return_base),
    className: (row) => signedValueClass(positionMetric(row, row.unrealized_return_base)),
    total: (rows) => signedPercent(totalUnrealizedPct(rows)),
    totalClassName: (rows) => signedValueClass(totalUnrealizedPct(rows)),
  },
  instrument_return_1w: {
    key: 'instrument_return_1w',
    label: '1W Total Return',
    align: 'right',
    render: (row) => signedHoldingMarketMetric(row, row.instrument_return_1w),
    sortValue: (row) => holdingMarketMetric(row, row.instrument_return_1w),
    className: (row) => signedValueClass(holdingMarketMetric(row, row.instrument_return_1w)),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1w)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1w)),
  },
  instrument_return_1m: {
    key: 'instrument_return_1m',
    label: '1M Total Return',
    align: 'right',
    render: (row) => signedHoldingMarketMetric(row, row.instrument_return_1m),
    sortValue: (row) => holdingMarketMetric(row, row.instrument_return_1m),
    className: (row) => signedValueClass(holdingMarketMetric(row, row.instrument_return_1m)),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1m)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1m)),
  },
  instrument_return_3m: {
    key: 'instrument_return_3m',
    label: '3M Total Return',
    align: 'right',
    render: (row) => signedHoldingMarketMetric(row, row.instrument_return_3m),
    sortValue: (row) => holdingMarketMetric(row, row.instrument_return_3m),
    className: (row) => signedValueClass(holdingMarketMetric(row, row.instrument_return_3m)),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_3m)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_3m)),
  },
  instrument_return_6m: {
    key: 'instrument_return_6m',
    label: '6M Total Return',
    align: 'right',
    render: (row) => signedHoldingMarketMetric(row, row.instrument_return_6m),
    sortValue: (row) => holdingMarketMetric(row, row.instrument_return_6m),
    className: (row) => signedValueClass(holdingMarketMetric(row, row.instrument_return_6m)),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_6m)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_6m)),
  },
  instrument_return_mtd: {
    key: 'instrument_return_mtd',
    label: 'MTD Total Return',
    align: 'right',
    render: (row) => signedHoldingMarketMetric(row, row.instrument_return_mtd),
    sortValue: (row) => holdingMarketMetric(row, row.instrument_return_mtd),
    className: (row) => signedValueClass(holdingMarketMetric(row, row.instrument_return_mtd)),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_mtd)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_mtd)),
  },
  instrument_return_ytd: {
    key: 'instrument_return_ytd',
    label: 'YTD Total Return',
    align: 'right',
    render: (row) => signedHoldingMarketMetric(row, row.instrument_return_ytd),
    sortValue: (row) => holdingMarketMetric(row, row.instrument_return_ytd),
    className: (row) => signedValueClass(holdingMarketMetric(row, row.instrument_return_ytd)),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_ytd)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_ytd)),
  },
  instrument_return_1y: {
    key: 'instrument_return_1y',
    label: '1Y Total Return',
    align: 'right',
    render: (row) => signedHoldingMarketMetric(row, row.instrument_return_1y),
    sortValue: (row) => holdingMarketMetric(row, row.instrument_return_1y),
    className: (row) => signedValueClass(holdingMarketMetric(row, row.instrument_return_1y)),
    total: (rows, context) => signedPercent(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1y)),
    totalClassName: (rows, context) => signedValueClass(weightedHoldingMetric(rows, context.workspace, (row) => row.instrument_return_1y)),
  },
  instrument_current_drawdown: {
    key: 'instrument_current_drawdown',
    label: 'Current DD',
    align: 'right',
    render: (row) => signedHoldingMarketMetric(row, row.instrument_current_drawdown),
    sortValue: (row) => holdingMarketMetric(row, row.instrument_current_drawdown),
    className: (row) => signedValueClass(holdingMarketMetric(row, row.instrument_current_drawdown)),
    total: (_rows, context) => signedPercent(context.riskMetrics?.currentDrawdown),
    totalClassName: (_rows, context) => signedValueClass(context.riskMetrics?.currentDrawdown),
  },
  instrument_volatility_1m: {
    key: 'instrument_volatility_1m',
    label: '1M Vol',
    align: 'right',
    render: (row) => unsignedHoldingModeledRiskMetric(row, row.instrument_volatility_1m),
    sortValue: (row) => holdingModeledRiskMetric(row, row.instrument_volatility_1m),
    total: (rows, context) => rows.every(holdingIsExcludedFromRisk)
      ? 'N/A'
      : formatPercent(context.riskMetrics?.volatility['1m']),
  },
  instrument_volatility_3m: {
    key: 'instrument_volatility_3m',
    label: '3M Vol',
    align: 'right',
    render: (row) => unsignedHoldingModeledRiskMetric(row, row.instrument_volatility_3m),
    sortValue: (row) => holdingModeledRiskMetric(row, row.instrument_volatility_3m),
    total: (rows, context) => rows.every(holdingIsExcludedFromRisk)
      ? 'N/A'
      : formatPercent(context.riskMetrics?.volatility['3m']),
  },
  instrument_volatility_6m: {
    key: 'instrument_volatility_6m',
    label: '6M Vol',
    align: 'right',
    render: (row) => unsignedHoldingModeledRiskMetric(row, row.instrument_volatility_6m),
    sortValue: (row) => holdingModeledRiskMetric(row, row.instrument_volatility_6m),
    total: (rows, context) => rows.every(holdingIsExcludedFromRisk)
      ? 'N/A'
      : formatPercent(context.riskMetrics?.volatility['6m']),
  },
  instrument_volatility_1y: {
    key: 'instrument_volatility_1y',
    label: '1Y Vol',
    align: 'right',
    render: (row) => unsignedHoldingModeledRiskMetric(row, row.instrument_volatility_1y),
    sortValue: (row) => holdingModeledRiskMetric(row, row.instrument_volatility_1y),
    total: (rows, context) => rows.every(holdingIsExcludedFromRisk)
      ? 'N/A'
      : formatPercent(context.riskMetrics?.volatility['1y']),
  },
  forward_risk_share: {
    key: 'forward_risk_share',
    label: 'Forward RC',
    align: 'right',
    render: (row) =>
      holdingIsExcludedFromRisk(row) ? 'N/A' : holdingUsesModeledZeroRisk(row) ? (
        <span title="Modeled as 0 because this holding is outside covariance risk analytics.">
          {signedPercent(0)}
        </span>
      ) : row.forward_risk_status === 'ok' ? signedPercent(row.forward_risk_share) : '—',
    sortValue: (row) => holdingModeledRiskMetric(row, row.forward_risk_share),
    className: (row) => signedValueClass(holdingModeledRiskMetric(row, row.forward_risk_share)),
    total: (rows, context) => rows.every(holdingIsExcludedFromRisk)
      ? 'N/A'
      : signedPercent(totalForwardRiskShare(rows, context.workspace)),
    totalClassName: (rows, context) =>
      signedValueClass(totalForwardRiskShare(rows, context.workspace)),
  },
  instrument_max_drawdown: {
    key: 'instrument_max_drawdown',
    label: 'Max DD',
    align: 'right',
    render: (row) => signedHoldingMarketMetric(row, row.instrument_max_drawdown),
    sortValue: (row) => holdingMarketMetric(row, row.instrument_max_drawdown),
    className: (row) => signedValueClass(holdingMarketMetric(row, row.instrument_max_drawdown)),
    total: (_rows, context) => signedPercent(context.riskMetrics?.maxDrawdown),
    totalClassName: (_rows, context) => signedValueClass(context.riskMetrics?.maxDrawdown),
  },
  instrument_holding_max_drawdown: {
    key: 'instrument_holding_max_drawdown',
    label: 'Held Max DD',
    align: 'right',
    render: (row) => signedHoldingMarketMetric(row, row.instrument_holding_max_drawdown),
    sortValue: (row) => holdingMarketMetric(row, row.instrument_holding_max_drawdown),
    className: (row) => signedValueClass(holdingMarketMetric(row, row.instrument_holding_max_drawdown)),
    total: () => '',
    totalClassName: () => '',
  },
  price_chart_1m: {
    key: 'price_chart_1m',
    label: 'Chart 1M',
    render: (row) => holdingUsesEventValuation(row) ? 'N/A' : <Sparkline values={row.price_chart_1m} />,
    sortValue: (row) => chartReturnForColumn(row, 'price_chart_1m'),
  },
  price_chart_3m: {
    key: 'price_chart_3m',
    label: 'Chart 3M',
    render: (row) => holdingUsesEventValuation(row) ? 'N/A' : <Sparkline values={row.price_chart_3m} />,
    sortValue: (row) => chartReturnForColumn(row, 'price_chart_3m'),
  },
  price_chart_6m: {
    key: 'price_chart_6m',
    label: 'Chart 6M',
    render: (row) => holdingUsesEventValuation(row) ? 'N/A' : <Sparkline values={row.price_chart_6m} />,
    sortValue: (row) => chartReturnForColumn(row, 'price_chart_6m'),
  },
  price_chart_1y: {
    key: 'price_chart_1y',
    label: 'Chart 1Y',
    render: (row) => holdingUsesEventValuation(row) ? 'N/A' : <Sparkline values={row.price_chart_1y} />,
    sortValue: (row) => chartReturnForColumn(row, 'price_chart_1y'),
  },
  coverage: {
    key: 'coverage',
    label: 'Coverage',
    align: 'center',
    render: (row) => (
      <span
        className={`coverage-pill ${
          row.coverage_status === 'price-nav-fx' || row.coverage_status === 'cash'
            ? 'coverage-pill-live'
            : 'coverage-pill-warning'
        }`}
      >
        {formatLabel(row.coverage_status)}
      </span>
    ),
    sortValue: (row) => row.coverage_status,
  },
}

function resolveGroupForRow(
  row: PortfolioHoldingRow,
  groupBy: HoldingsGroupByKey,
  taxonomyByInstrumentId: Map<string, HoldingTaxonomyLabels>,
) {
  if (groupBy === 'taxonomy_top') {
    const taxonomy = taxonomyLabelsForHoldingRow(row, taxonomyByInstrumentId)
    return {
      key: taxonomy?.topLevelId ?? '__unassigned_taxonomy__',
      label: taxonomy?.topLevelLabel ?? 'Unassigned',
    }
  }
  if (groupBy === 'taxonomy_leaf') {
    const taxonomy = taxonomyLabelsForHoldingRow(row, taxonomyByInstrumentId)
    return {
      key: taxonomy?.leafId ?? '__unassigned_taxonomy_leaf__',
      label: taxonomy?.leafLabel ?? 'Unassigned',
    }
  }
  if (groupBy === 'instrument_type') {
    return { key: holdingAssetType(row), label: formatLabel(holdingAssetType(row)) }
  }
  if (groupBy === 'currency') {
    return { key: holdingCurrency(row), label: holdingCurrency(row) }
  }
  if (groupBy === 'coverage') {
    return { key: row.coverage_status, label: formatLabel(row.coverage_status) }
  }
  return { key: '__all__', label: 'All Holdings' }
}

function buildGroupedRows(
  rows: PortfolioHoldingRow[],
  groupBy: HoldingsGroupByKey,
  taxonomyByInstrumentId: Map<string, HoldingTaxonomyLabels>,
  workspace: HoldingsWorkspaceResponse | null,
) {
  const groups = new Map<string, HoldingsGroup>()
  rows.forEach((row) => {
    const groupRef = resolveGroupForRow(row, groupBy, taxonomyByInstrumentId)
    const current = groups.get(groupRef.key) ?? {
      key: groupRef.key,
      label: groupRef.label,
      rows: [],
      marketValueBase: 0,
      weight: 0,
      openLots: 0,
    }
    current.rows.push(row)
    current.marketValueBase += workspace ? (rowMarketValueBase(row, workspace) ?? 0) : 0
    current.weight += finiteNumber(row.allocation) ?? 0
    current.openLots += finiteNumber(row.open_position_lot_count) ?? 0
    groups.set(groupRef.key, current)
  })

  return [...groups.values()].sort((left, right) => {
    return (
      right.marketValueBase - left.marketValueBase ||
      right.weight - left.weight ||
      left.label.localeCompare(right.label, 'zh-Hans-CN')
    )
  })
}

export default function PortfolioHomePage() {
  const zh = useLanguage().language === 'zh-Hans'
  const navigate = useNavigate()
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [workspaceResponse, setWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const loadedHoldingsInputRef = useRef<{ key: string; full: boolean } | null>(null)
  const [taxonomyCatalogResponse, setTaxonomyCatalog] =
    useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const workspace = workspaceResponse?.portfolio_id === portfolioId ? workspaceResponse : null
  const taxonomyCatalog =
    taxonomyCatalogResponse?.portfolio_id === portfolioId
      ? taxonomyCatalogResponse
      : null
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [taxonomyError, setTaxonomyError] = useState<string | null>(null)
  const [riskPolicyRevision, setRiskPolicyRevision] = useState(0)
  const initialHoldingsViewStore = useMemo(() => normalizeHoldingsViewStore(null), [])
  const initialHoldingsViewState = useMemo(
    () => resolveHoldingsViewState(initialHoldingsViewStore, initialHoldingsViewStore.activeViewId),
    [initialHoldingsViewStore],
  )
  const initialHoldingsUrlSortField = useMemo(
    () =>
      parseHoldingsSortField(
        searchParams.get('holdings_sort_field') ?? searchParams.get('holdings_sort')?.replace(/_(asc|desc)$/, '') ?? null,
      ),
    [searchParams],
  )
  const [holdingsViewStore, setHoldingsViewStore] = useState<HoldingsViewStore>(() => initialHoldingsViewStore)
  const persistedHoldingsViewStoreRef = useRef<{
    portfolioId: string
    serializedStore: string
  } | null>(null)
  const currentPortfolioIdRef = useRef(portfolioId)
  currentPortfolioIdRef.current = portfolioId
  const holdingsViewSaveQueueRef = useRef(new SerialTaskQueue())
  const [holdingsViewStoreReadyPortfolioId, setHoldingsViewStoreReadyPortfolioId] =
    useState<string | null>(null)
  const [holdingsViewStoreError, setHoldingsViewStoreError] = useState<string | null>(null)
  const [holdingsViewLoadRetryToken, setHoldingsViewLoadRetryToken] = useState(0)
  const [holdingsViewSaveRetryToken, setHoldingsViewSaveRetryToken] = useState(0)
  const [activeHoldingsViewId, setActiveHoldingsViewId] = useState(initialHoldingsViewStore.activeViewId)
  const [holdingsColumns, setHoldingsColumns] = useState<HoldingsColumnKey[]>(() => initialHoldingsViewState.columns)
  const [holdingsSectionVisibleColumns, setHoldingsSectionVisibleColumns] =
    useState<HoldingsSectionVisibleColumns>(() => ({
      fcn: [...DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.fcn],
      options: [...DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.options],
      cash: [...DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.cash],
    }))
  const handleHoldingsSectionVisibleColumnsChange = useCallback(
    (sectionKey: string, columns: string[]) => {
      if (!Object.prototype.hasOwnProperty.call(DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS, sectionKey)) {
        return
      }
      const key = sectionKey as HoldingsSectionKey
      setHoldingsSectionVisibleColumns((current) =>
        JSON.stringify(current[key]) === JSON.stringify(columns)
          ? current
          : { ...current, [key]: columns },
      )
    },
    [],
  )
  const holdingsNeedsDetails = holdingsColumns.some((column) => HOLDINGS_COLUMNS_REQUIRING_DETAILS.has(column))
  const [holdingsColumnWidths, setHoldingsColumnWidths] = useState<Partial<Record<HoldingsColumnKey, number>>>(() =>
    Object.keys(initialHoldingsViewState.columnWidths).length
      ? initialHoldingsViewState.columnWidths
      : loadStoredHoldingsColumnWidths(),
  )
  const [holdingsColumnDraft, setHoldingsColumnDraft] = useState<HoldingsColumnKey[]>(() => initialHoldingsViewState.columns)
  const [holdingsColumnsOpen, setHoldingsColumnsOpen] = useState(false)
  const [holdingsGroupByOpen, setHoldingsGroupByOpen] = useState(false)
  const holdingsColumnsDialogRef = useModalDialog(
    holdingsColumnsOpen,
    () => setHoldingsColumnsOpen(false),
  )
  const holdingsGroupByDialogRef = useModalDialog(
    holdingsGroupByOpen,
    () => setHoldingsGroupByOpen(false),
  )
  const [holdingsColumnCategory, setHoldingsColumnCategory] = useState(HOLDINGS_COLUMN_GROUPS[0]?.label ?? 'Core')
  const [holdingsColumnSearch, setHoldingsColumnSearch] = useState('')
  const [holdingsColumnDropTarget, setHoldingsColumnDropTarget] = useState<HoldingsColumnKey | null>(null)
  const [holdingsColumnResizing, setHoldingsColumnResizing] = useState(false)
  const [viewToast, setViewToast] = useState<NoticeToastMessage | null>(null)
  const [holdingsSortField, setHoldingsSortField] = useState<HoldingsColumnKey | null>(
    () => initialHoldingsUrlSortField ?? initialHoldingsViewState.sortField,
  )
  const [holdingsSortDirection, setHoldingsSortDirection] = useState<HoldingsSortDirection>(() =>
    initialHoldingsUrlSortField
      ? parseHoldingsSortDirection(
          searchParams.get('holdings_sort_direction') ?? searchParams.get('holdings_sort')?.match(/_(asc|desc)$/)?.[1] ?? null,
        )
      : initialHoldingsViewState.sortDirection,
  )
  const [holdingsGroupBy, setHoldingsGroupBy] = useState<HoldingsGroupByKey>(() => {
    const rawGroupBy = searchParams.get('holdings_group_by')
    return rawGroupBy ? parseHoldingsGroupBy(rawGroupBy) : initialHoldingsViewState.groupBy
  })
  const waitingForTaxonomy = !taxonomyCatalog && !taxonomyError && (
    holdingsGroupBy.startsWith('taxonomy_') || holdingsColumns.some((column) => column.startsWith('taxonomy_'))
  )
  const holdingsColumnResizeState = useRef<{
    column: HoldingsColumnKey
    startX: number
    startWidth: number
  } | null>(null)
  const holdingsColumnResizeFrame = useRef<number | null>(null)
  const pendingHoldingsColumnResize = useRef<{ column: HoldingsColumnKey; width: number } | null>(null)
  const holdingsTablePan = useHorizontalTablePan()
  const holdingsTableShellRef = holdingsTablePan.ref
  const [holdingsTableShellWidth, setHoldingsTableShellWidth] = useState(0)
  const requestedAsOfDate = searchParams.get('as_of_date') ?? ''

  useEffect(() => {
    setHoldingsSectionVisibleColumns({
      fcn: [...DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.fcn],
      options: [...DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.options],
      cash: [...DEFAULT_HOLDINGS_SECTION_VISIBLE_COLUMNS.cash],
    })
  }, [portfolioId])

  useEffect(() => {
    function refreshAfterOptionOutcome(event: Event) {
      const detail = (event as CustomEvent<{ portfolioId?: string }>).detail
      if (detail?.portfolioId === portfolioId) {
        setRiskPolicyRevision((current) => current + 1)
      }
    }
    window.addEventListener(OPTION_OUTCOME_RECORDED_EVENT, refreshAfterOptionOutcome)
    return () => {
      window.removeEventListener(OPTION_OUTCOME_RECORDED_EVENT, refreshAfterOptionOutcome)
    }
  }, [portfolioId])

  const holdingsViews = useMemo(() => getHoldingsViews(holdingsViewStore), [holdingsViewStore])
  const activeHoldingsView = useMemo(
    () => getHoldingsViewById(holdingsViewStore, activeHoldingsViewId),
    [activeHoldingsViewId, holdingsViewStore],
  )
  const currentHoldingsViewState = useMemo<HoldingsViewState>(
    () => ({
      columns: normalizeHoldingsColumns(holdingsColumns),
      columnWidths: normalizeHoldingsColumnWidths(holdingsColumnWidths),
      groupBy: holdingsGroupBy,
      sortField: holdingsSortField,
      sortDirection: holdingsSortDirection,
    }),
    [holdingsColumnWidths, holdingsColumns, holdingsGroupBy, holdingsSortDirection, holdingsSortField],
  )
  const holdingsViewEdited = !holdingsViewStatesEqual(currentHoldingsViewState, activeHoldingsView.state)
  const selectedGroupByOption =
    HOLDINGS_GROUP_BY_OPTIONS.find((option) => option.value === holdingsGroupBy) ?? HOLDINGS_GROUP_BY_OPTIONS[0]

  useEffect(() => {
    if (
      holdingsViewStoreReadyPortfolioId !== portfolioId ||
      holdingsColumnResizing ||
      !holdingsViewEdited
    ) {
      return undefined
    }
    const timeoutId = window.setTimeout(
      () => updateHoldingsViewState(currentHoldingsViewState),
      HOLDINGS_VIEW_AUTOSAVE_DELAY_MS,
    )
    return () => window.clearTimeout(timeoutId)
  }, [
    activeHoldingsViewId,
    currentHoldingsViewState,
    holdingsColumnResizing,
    holdingsViewEdited,
    holdingsViewStoreReadyPortfolioId,
    portfolioId,
  ])

  const [groupingTaxonomyId, setGroupingTaxonomyId] = useState('')
  const groupingTaxonomy = resolveGroupingTaxonomy(taxonomyCatalog, groupingTaxonomyId)
  const taxonomyByInstrumentId = useMemo(
    () => buildTaxonomyLabelsByInstrumentId(taxonomyCatalog, groupingTaxonomyId),
    [taxonomyCatalog, groupingTaxonomyId],
  )
  const columnContext = useMemo(
    () =>
      workspace
        ? {
            workspace,
            taxonomyByInstrumentId,
          }
        : null,
    [taxonomyByInstrumentId, workspace],
  )
  const visibleColumns = useMemo(
    () => normalizeHoldingsColumns(holdingsColumns).map((column) => HOLDINGS_COLUMN_DEFINITIONS[column]),
    [holdingsColumns],
  )
  const compactHoldingsColumns = useMemo(
    () =>
      compactTableColumnWidths(
        visibleColumns.map((column) => column.key),
        (column) => holdingsColumnWidths[column] ?? DEFAULT_HOLDINGS_COLUMN_WIDTHS[column],
        (column) => COMPACT_HOLDINGS_COLUMN_MIN_WIDTHS[column] ?? HOLDINGS_COLUMN_MIN_WIDTH,
        holdingsTableShellWidth,
      ),
    [holdingsColumnWidths, holdingsTableShellWidth, visibleColumns],
  )
  const displayHoldingsColumnWidths = compactHoldingsColumns.widths
  const displayHoldingsTableMinWidth = compactHoldingsColumns.totalWidth
  const filteredHoldingsColumns = useMemo(() => {
    const searchQuery = holdingsColumnSearch.trim().toLocaleLowerCase()
    const selectedGroup =
      HOLDINGS_COLUMN_GROUPS.find((group) => group.label === holdingsColumnCategory) ??
      HOLDINGS_COLUMN_GROUPS[0] ?? { label: 'Core', columns: [] }
    const sourceGroups = searchQuery ? HOLDINGS_COLUMN_GROUPS : [selectedGroup]
    return sourceGroups.flatMap((group) =>
      group.columns
        .filter((column) => {
          if (!searchQuery) {
            return true
          }
          const definition = HOLDINGS_COLUMN_DEFINITIONS[column]
          return `${definition.label} ${column} ${group.label}`.toLocaleLowerCase().includes(searchQuery)
        })
        .map((column) => ({ column, groupLabel: group.label })),
    )
  }, [holdingsColumnCategory, holdingsColumnSearch])

  useEffect(() => {
    const element = holdingsTableShellRef.current
    if (!element) {
      return undefined
    }

    const updateWidth = () => setHoldingsTableShellWidth(Math.floor(element.clientWidth))
    updateWidth()

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateWidth)
      return () => window.removeEventListener('resize', updateWidth)
    }

    const observer = new ResizeObserver(updateWidth)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const securityRows = useMemo(
    () => (workspace?.rows ?? []).filter((row) => row.holding_category === 'securities'),
    [workspace?.rows],
  )
  const sortedSecurityRows = useMemo(() => {
    const rows = securityRows
    if (!columnContext || !holdingsSortField) {
      return rows.slice()
    }
    const sortColumn = HOLDINGS_COLUMN_DEFINITIONS[holdingsSortField]
    return rows.slice().sort((left, right) => {
      const primary = compareSortableValue(
        sortColumn.sortValue(left, columnContext),
        sortColumn.sortValue(right, columnContext),
        holdingsSortDirection,
      )
      return primary || primaryIdentifier(left).localeCompare(primaryIdentifier(right), 'zh-Hans-CN')
    })
  }, [columnContext, holdingsSortDirection, holdingsSortField, securityRows])
  const derivativeRows = useMemo(
    () => (workspace?.rows ?? []).filter((row) => row.holding_category === 'derivatives'),
    [workspace?.rows],
  )
  const cashRows = useMemo(
    () =>
      (workspace?.rows ?? []).filter(
        (row) => row.holding_category === 'cash_and_settlement',
      ),
    [workspace?.rows],
  )
  const groupedSecurityRows = useMemo(() => {
    if (holdingsGroupBy === 'none') {
      return []
    }
    return buildGroupedRows(
      sortedSecurityRows,
      holdingsGroupBy,
      taxonomyByInstrumentId,
      workspace,
    )
  }, [holdingsGroupBy, sortedSecurityRows, taxonomyByInstrumentId, workspace])

  const riskGroupingTaxonomy = holdingsGroupBy.startsWith('taxonomy_') ? taxonomyByInstrumentId : null
  const securityRiskMetrics = useMemo(() => {
    if (!workspace) return null
    const groups = holdingsGroupBy === 'none' ? [] : buildGroupedRows(
      securityRows, holdingsGroupBy, riskGroupingTaxonomy ?? new Map(), workspace,
    )
    return {
      subtotal: buildHoldingsGroupRiskMetrics(securityRows, workspace),
      groups: new Map(groups.map((group) => [group.key, buildHoldingsGroupRiskMetrics(group.rows, workspace)])),
    }
  }, [securityRows, workspace, holdingsGroupBy, riskGroupingTaxonomy])

  function updateSearchParam(key: string, value: string | null) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      const normalizedValue = value && value.trim() ? value : null
      const currentValue = current.get(key)
      if (normalizedValue === currentValue || (!normalizedValue && !currentValue)) {
        return current
      }
      if (normalizedValue) {
        next.set(key, normalizedValue)
      } else {
        next.delete(key)
      }
      return next
    })
  }

  function applyHoldingsViewState(state: HoldingsViewState) {
    const normalized = normalizeHoldingsViewState(state)
    setHoldingsColumns(normalized.columns)
    setHoldingsColumnDraft(normalized.columns)
    setHoldingsColumnWidths(normalized.columnWidths)
    setHoldingsGroupBy(normalized.groupBy)
    setHoldingsSortField(normalized.sortField)
    setHoldingsSortDirection(normalized.sortDirection)
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('holdings_sort')
      if (normalized.groupBy === 'none') {
        next.delete('holdings_group_by')
      } else {
        next.set('holdings_group_by', normalized.groupBy)
      }
      if (normalized.sortField) {
        next.set('holdings_sort_field', normalized.sortField)
        next.set('holdings_sort_direction', normalized.sortDirection)
      } else {
        next.delete('holdings_sort_field')
        next.delete('holdings_sort_direction')
      }
      return next
    })
  }

  function handleSelectHoldingsView(viewId: string) {
    const nextView = getHoldingsViewById(holdingsViewStore, viewId)
    setActiveHoldingsViewId(nextView.id)
    setHoldingsViewStore((current) => ({ ...current, activeViewId: nextView.id }))
    applyHoldingsViewState(resolveHoldingsViewState(holdingsViewStore, nextView.id))
  }

  function updateHoldingsViewState(state: HoldingsViewState) {
    const timestamp = new Date().toISOString()
    const normalizedState = normalizeHoldingsViewState(state)
    setHoldingsViewStore((current) => {
      return {
        ...current,
        activeViewId: activeHoldingsViewId,
        views: current.views.map((view) =>
          view.id === activeHoldingsViewId
            ? {
                ...view,
                state: normalizedState,
                updatedAt: timestamp,
              }
            : view,
        ),
      }
    })
  }

  function handleSaveHoldingsViewAs(name: string, description: string | null) {
    const timestamp = new Date().toISOString()
    const viewId = createHoldingsViewId()
    const nextView: HoldingsTableView = {
      id: viewId,
      name,
      description,
      readonly: false,
      createdAt: timestamp,
      updatedAt: timestamp,
      state: currentHoldingsViewState,
    }
    setHoldingsViewStore((current) => {
      return {
        ...current,
        activeViewId: viewId,
        views: [...current.views, nextView],
      }
    })
    setActiveHoldingsViewId(viewId)
    setViewToast({ id: Date.now(), message: `View saved as "${name}".`, tone: 'success' })
  }

  function handleDeleteHoldingsView(viewId: string) {
    const targetView = getHoldingsViewById(holdingsViewStore, viewId)
    if (targetView.readonly) {
      return
    }
    const fallbackView = SYSTEM_HOLDINGS_VIEWS[0]
    const deletingActiveView = targetView.id === activeHoldingsViewId
    setHoldingsViewStore((current) => ({
      ...current,
      activeViewId: deletingActiveView ? fallbackView.id : current.activeViewId,
      views: current.views.filter((view) => view.id !== targetView.id),
    }))
    if (deletingActiveView) {
      setActiveHoldingsViewId(fallbackView.id)
      applyHoldingsViewState(fallbackView.state)
    }
  }

  function handleSelectInstrument(instrumentId: string | null, holdingLineId?: string) {
    const normalizedInstrumentId = instrumentId?.trim() || null
    if (!normalizedInstrumentId || !portfolioId) {
      return
    }
    const next = new URLSearchParams(searchParams)
    next.delete('instrument_id')
    next.delete('position_lot_id')
    if (holdingLineId) {
      next.set('holding_line_id', holdingLineId)
    } else {
      next.delete('holding_line_id')
    }
    const query = next.toString()
    navigate(`${buildPortfolioHoldingDetailPath(portfolioId, normalizedInstrumentId)}${query ? `?${query}` : ''}`)
  }

  function handleHoldingsSort(field: HoldingsColumnKey) {
    let nextSortField: HoldingsColumnKey | null = field
    let nextSortDirection: HoldingsSortDirection = 'asc'
    if (holdingsSortField === field && holdingsSortDirection === 'asc') {
      nextSortDirection = 'desc'
    } else if (holdingsSortField === field && holdingsSortDirection === 'desc') {
      nextSortField = null
      nextSortDirection = 'asc'
    }
    setHoldingsSortField(nextSortField)
    setHoldingsSortDirection(nextSortDirection)
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('holdings_sort')
      if (nextSortField) {
        next.set('holdings_sort_field', nextSortField)
        next.set('holdings_sort_direction', nextSortDirection)
      } else {
        next.delete('holdings_sort_field')
        next.delete('holdings_sort_direction')
      }
      return next
    })
  }

  function handleGroupByChange(value: HoldingsGroupByKey) {
    setHoldingsGroupBy(value)
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (value === 'none') {
        next.delete('holdings_group_by')
      } else {
        next.set('holdings_group_by', value)
      }
      return next
    })
    setHoldingsGroupByOpen(false)
  }

  function handleHoldingsColumnDraftToggle(column: HoldingsColumnKey, checked: boolean) {
    setHoldingsColumnDraft((current) =>
      normalizeHoldingsColumns(checked ? [...current, column] : current.filter((item) => item !== column)),
    )
  }

  function moveHoldingsColumn(sourceColumn: HoldingsColumnKey, targetColumn: HoldingsColumnKey) {
    if (sourceColumn === LOCKED_HOLDINGS_COLUMN || sourceColumn === targetColumn) {
      return
    }
    setHoldingsColumns((current) => {
      const normalized = normalizeHoldingsColumns(current)
      const sourceIndex = normalized.indexOf(sourceColumn)
      const targetIndex = normalized.indexOf(targetColumn)
      if (sourceIndex < 0 || targetIndex < 0) {
        return normalized
      }
      const next = [...normalized]
      const [moved] = next.splice(sourceIndex, 1)
      let insertIndex = targetIndex === 0 ? 1 : targetIndex
      if (sourceIndex < targetIndex) {
        insertIndex = targetIndex
      }
      next.splice(insertIndex, 0, moved)
      return normalizeHoldingsColumns(next)
    })
  }

  function handleHoldingsColumnDragStart(event: DragEvent<HTMLTableCellElement>, column: HoldingsColumnKey) {
    if (column === LOCKED_HOLDINGS_COLUMN) {
      event.preventDefault()
      return
    }
    event.dataTransfer.setData('text/plain', column)
    event.dataTransfer.effectAllowed = 'move'
  }

  function handleHoldingsColumnDragOver(event: DragEvent<HTMLTableCellElement>, column: HoldingsColumnKey) {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setHoldingsColumnDropTarget(column)
  }

  function handleHoldingsColumnDrop(event: DragEvent<HTMLTableCellElement>, targetColumn: HoldingsColumnKey) {
    event.preventDefault()
    const sourceColumn = event.dataTransfer.getData('text/plain') as HoldingsColumnKey
    if (ALL_HOLDINGS_COLUMN_KEYS.includes(sourceColumn)) {
      moveHoldingsColumn(sourceColumn, targetColumn)
    }
    setHoldingsColumnDropTarget(null)
  }

  function handleHoldingsColumnResizeStart(
    event: ReactMouseEvent<HTMLSpanElement>,
    column: HoldingsColumnKey,
    currentWidth: number,
  ) {
    event.preventDefault()
    event.stopPropagation()
    holdingsColumnResizeState.current = {
      column,
      startX: event.clientX,
      startWidth: currentWidth,
    }
    setHoldingsColumnResizing(true)
    document.body.style.cursor = 'col-resize'
  }

  function handleDownload(format: TableExportFormat) {
    if (!workspace || !columnContext || !workspace.rows.length) {
      return
    }

    const rows: TableCell[][] = [
      [`Holdings as of ${workspace.as_of_date}`, workspace.portfolio_name, workspace.base_currency],
    ]
    const appendSection = (title: string, header: TableCell[], dataRows: TableCell[][]) => {
      if (!dataRows.length) {
        return
      }
      rows.push([], [title], header, ...dataRows)
    }
    const appendConfiguredSection = (
      title: string,
      sectionKey: HoldingsSectionKey,
      completeHeader: TableCell[],
      completeRows: TableCell[][],
    ) => {
      const allColumnKeys = HOLDINGS_SECTION_COLUMN_KEYS[sectionKey]
      const visibleIndexes = holdingsSectionVisibleColumns[sectionKey]
        .map((columnKey) => allColumnKeys.indexOf(columnKey))
        .filter((index) => index >= 0)
      appendSection(
        title,
        visibleIndexes.map((index) => completeHeader[index]),
        completeRows.map((row) => visibleIndexes.map((index) => row[index])),
      )
    }
    const securityHeader = [
      ...(holdingsGroupBy !== 'none' ? ['Security Group'] : []),
      ...visibleColumns.map((column) => column.label),
    ]
    const securityDataRows: TableCell[][] = []
    const appendSecurityRow = (row: PortfolioHoldingRow, groupLabel: string | null) => {
      securityDataRows.push([
        ...(holdingsGroupBy !== 'none' ? [groupLabel ?? 'Unassigned'] : []),
        ...visibleColumns.map((column) =>
          holdingColumnExportValue(column.key, row, columnContext),
        ),
      ])
    }
    const appendSecuritySubtotal = (
      label: string,
      subtotalRows: PortfolioHoldingRow[],
      riskMetrics: HoldingsGroupRiskMetrics | undefined,
    ) => {
      securityDataRows.push([
        ...(holdingsGroupBy !== 'none' ? [label] : []),
        ...visibleColumns.map((column, index) =>
          index === 0
            ? `${label} (${workspace.base_currency})`
            : holdingColumnSubtotalExportValue(column.key, subtotalRows, { ...columnContext, riskMetrics }),
        ),
      ])
    }
    if (holdingsGroupBy === 'none') {
      sortedSecurityRows.forEach((row) => appendSecurityRow(row, null))
    } else {
      groupedSecurityRows.forEach((group) => {
        group.rows.forEach((row) => appendSecurityRow(row, group.label))
        appendSecuritySubtotal(`${group.label} Subtotal`, group.rows, securityRiskMetrics?.groups.get(group.key))
      })
    }
    if (sortedSecurityRows.length) {
      appendSecuritySubtotal('Securities Subtotal', sortedSecurityRows, securityRiskMetrics?.subtotal)
    }
    appendSection('Securities', securityHeader, securityDataRows)

    const namesById = new Map(
      workspace.rows
        .filter((row) => row.instrument_core)
        .map((row) => [row.instrument_core!.instrument_id, row.instrument_core!.instrument_name]),
    )
    const fcnRows = derivativeRows.filter(
      (row) => row.derivative_contract?.contract_type === 'fcn',
    )
    appendConfiguredSection(
      'FCN',
      'fcn',
      [
        'Contract',
        'External Ref',
        'Account',
        'Currency',
        'Notional',
        'Underlying Terms',
        'Current Risk',
        'Annual Coupon',
        'Issue Date',
        'Final Observation',
        'Maturity',
        'Days',
        'Issuer',
        'Counterparty',
        'Remaining Basis',
        `Signed NAV Amount (${workspace.base_currency})`,
        `Historical Carrying Basis (${workspace.base_currency})`,
        `Carrying FX Translation (${workspace.base_currency})`,
        'Portfolio Weight',
        'Valuation Basis',
        'Fair Value Status',
        'Coverage',
      ],
      fcnRows.map((row) => {
        const contract = row.derivative_contract?.contract_type === 'fcn'
          ? row.derivative_contract
          : null
        return [
          contract?.contract_name ?? row.line_id,
          contract?.external_reference,
          contract?.account_id,
          holdingCurrency(row),
          contract?.terms.notional,
          contract?.terms.underlyings
            .map((underlying) => {
              const name = namesById.get(underlying.instrument_id) ?? underlying.instrument_id
              const current = row.fcn_risk?.underlyings.find(
                (item) => item.instrument_id === underlying.instrument_id,
              )
              const currency = current?.currency || 'N/A'
              return `${name}: spot ${current?.spot ?? 'N/A'} ${currency}, strike ${current?.strike_price ?? 'N/A'} ${currency} (${underlying.strike_level_pct ?? 'N/A'}%), KI ${current?.knock_in_price ?? 'N/A'} ${currency} (${underlying.knock_in_level_pct ?? 'N/A'}%), KO ${current?.knock_out_price ?? 'N/A'} ${currency} (${underlying.knock_out_level_pct ?? 'N/A'}%), initial ${current?.initial_reference_price ?? underlying.initial_reference_price ?? 'N/A'} ${currency}, ${underlying.deliverable ? 'deliverable' : 'cash settled'}`
            })
            .join('; '),
          row.fcn_risk
            ? [
                row.fcn_risk.risk_state,
                ...row.fcn_risk.underlyings
                  .filter(
                    (underlying) =>
                      underlying.instrument_id ===
                      row.fcn_risk?.delivery_buffer_underlying_instrument_id,
                  )
                  .map(
                    (underlying) =>
                      `${underlying.instrument_name}: ${underlying.distance_to_strike_pct ?? 'N/A'} vs delivery strike`,
                  ),
              ].join('; ')
            : null,
          contract?.terms.annual_coupon_rate_pct == null
            ? null
            : `${contract.terms.annual_coupon_rate_pct}%`,
          contract?.terms.issue_date,
          contract?.terms.final_observation_date,
          contract?.terms.maturity_date,
          contract?.terms.maturity_date
            ? Math.ceil(
                (Date.parse(`${contract.terms.maturity_date}T00:00:00Z`) -
                  Date.parse(`${workspace.as_of_date}T00:00:00Z`)) /
                  86_400_000,
              )
            : null,
          contract?.terms.issuer,
          contract?.terms.counterparty,
          row.cost_basis,
          rowMarketValueBase(row, workspace),
          row.carrying_value_historical_base,
          row.carrying_fx_translation_base,
          row.allocation,
          row.valuation_basis,
          row.fair_value_coverage_status,
          row.coverage_status,
        ]
      }),
    )

    const optionRows = derivativeRows.filter(
      (row) => row.derivative_contract?.contract_type === 'option',
    )
    appendConfiguredSection(
      'Options',
      'options',
      [
        'Contract',
        'External Ref',
        'Account',
        'Side',
        'Type',
        'Underlying',
        'Underlying Spot',
        'Currency',
        'Expiry',
        'Days',
        'Strike',
        'Moneyness',
        'Open Contracts',
        'Multiplier',
        'Underlying Equivalent',
        'Basis Type',
        'Remaining Basis',
        `Signed NAV Amount (${workspace.base_currency})`,
        `Historical Carrying Basis (${workspace.base_currency})`,
        `Carrying FX Translation (${workspace.base_currency})`,
        `Strike Notional (${workspace.base_currency})`,
        'Portfolio Backing',
        'Current Risk',
        'Portfolio Weight',
        'Status',
        'Valuation Basis',
        'Fair Value Status',
        'Coverage',
      ],
      optionRows.map((row) => {
        const contract = row.derivative_contract?.contract_type === 'option'
          ? row.derivative_contract
          : null
        const underlyingId = row.related_underlying_id ?? contract?.terms.underlying_instrument_id
        const written = isOptionObligationHolding(row) || row.quantity < 0
        return [
          contract?.contract_name ?? row.line_id,
          contract?.external_reference,
          contract?.account_id,
          written ? 'Written' : 'Long',
          row.option_type ?? contract?.terms.option_type,
          underlyingId
            ? row.option_risk?.underlying_name ?? namesById.get(underlyingId) ?? underlyingId
            : null,
          row.option_risk?.underlying_spot,
          holdingCurrency(row),
          row.expiry_date ?? contract?.terms.expiry_date,
          row.days_to_expiry,
          row.strike ?? contract?.terms.strike,
          row.option_risk?.moneyness_pct,
          row.open_contract_quantity ?? Math.abs(row.quantity),
          row.contract_multiplier ?? contract?.terms.contract_multiplier,
          row.required_underlying_quantity,
          written ? 'remaining_premium' : 'open_cost',
          written ? row.premium_basis_remaining : row.cost_basis,
          rowMarketValueBase(row, workspace),
          row.carrying_value_historical_base,
          row.carrying_fx_translation_base,
          row.strike_notional_base,
          row.option_risk?.backing
            ? `${row.option_risk.backing.ratio ?? 'N/A'} backed; ${row.option_risk.backing.available} available; ${row.option_risk.backing.required} required; ${row.option_risk.backing.shortfall} short`
            : null,
          row.option_risk?.risk_state,
          row.allocation,
          row.obligation_status ?? 'open',
          row.valuation_basis,
          row.fair_value_coverage_status,
          row.coverage_status,
        ]
      }),
    )

    appendConfiguredSection(
      'Cash & Settlement',
      'cash',
      [
        'Description',
        'Type',
        'Currency',
        'Account',
        'Available',
        'Local Amount',
        `Base Value (${workspace.base_currency})`,
        `FX Cost Basis (${workspace.base_currency})`,
        `Unrealized FX P&L (${workspace.base_currency})`,
        'Portfolio Weight',
        'Settlement Date',
        'Pending Until',
        'Related Instrument',
        'Settlement Status',
        'Coverage',
      ],
      cashRows.map((row) => [
        row.instrument_core?.instrument_name ?? row.line_id,
        row.holding_kind,
        holdingCurrency(row),
        (row.account_ids ?? []).join(', '),
        Boolean(row.available_for_trading),
        row.market_value,
        rowMarketValueBase(row, workspace),
        row.cost_basis_historical_base,
        row.unrealized_fx_pnl_base,
        row.allocation,
        row.settlement_date,
        row.pending_until_date,
        row.economic_instrument_ref?.instrument_name ?? row.economic_instrument_id,
        row.pending_status,
        row.coverage_status,
      ]),
    )


    downloadTable(`holdings-${workspace.portfolio_id}-${workspace.as_of_date}`, rows, format, 'Holdings')
  }

  function renderHoldingsSortHeader(column: HoldingsColumnDefinition) {
    const active = holdingsSortField === column.key
    const nextSortLabel = !active ? 'ascending' : holdingsSortDirection === 'asc' ? 'descending' : 'no sorting'
    return (
      <button
        type="button"
        draggable={false}
        className={`portfolio-table-th-label portfolio-table-th-sortable ${active ? 'portfolio-table-th-sortable-active' : ''}`}
        onClick={() => handleHoldingsSort(column.key)}
        title={`Sort ${column.label}: ${nextSortLabel}`}
        aria-label={`Sort ${column.label}: ${nextSortLabel}`}
      >
        <span>{column.label}</span>
        {active ? (
          <span className="portfolio-table-sort-indicator">{holdingsSortDirection === 'asc' ? '↑' : '↓'}</span>
        ) : null}
      </button>
    )
  }

  function renderHoldingDataRow(row: PortfolioHoldingRow) {
    if (!columnContext) {
      return null
    }
    const selectionInstrumentId =
      holdingReferenceId(row)
    return (
      <tr key={row.line_id}>
        {visibleColumns.map((column) => {
          const className = [
            holdingsAlignmentClass(column),
            isHoldingsChartColumn(column.key) ? 'chart-cell' : '',
            column.className?.(row, columnContext) ?? '',
            column.key === 'instrument' ? 'holding-name-cell' : '',
          ]
            .filter(Boolean)
            .join(' ')
          return (
            <td
              key={column.key}
              data-column-key={column.key}
              className={className || undefined}
            >
              {column.key === 'instrument' ? (
                <button
                  type="button"
                  className="holding-instrument-link"
                  onClick={() => handleSelectInstrument(selectionInstrumentId)}
                >
                  {column.render(row, columnContext)}
                </button>
              ) : (
                column.render(row, columnContext)
              )}
            </td>
          )
        })}
      </tr>
    )
  }

  function renderHoldingsSubtotalRow(rows: PortfolioHoldingRow[], label: string, className: string, key?: string) {
    if (!columnContext) {
      return null
    }
    const subtotalContext = { ...columnContext, riskMetrics: securityRiskMetrics?.subtotal }
    return (
      <HoldingsSubtotalRow
        key={key}
        className={className}
        label={label}
        cells={visibleColumns.map((column, index) => {
          const aggregationKind = HOLDINGS_GROUP_AGGREGATION_KIND[column.key]
          const canAggregate = aggregationKind !== 'none'
          const classNames = [
            holdingsAlignmentClass(column),
            isHoldingsChartColumn(column.key) ? 'chart-cell' : '',
            canAggregate ? column.totalClassName?.(rows, subtotalContext) ?? '' : '',
          ]
            .filter(Boolean)
            .join(' ')
          return {
            key: column.key,
            aggregationKind,
            className: classNames || undefined,
            content:
              index === 0 || !canAggregate
                ? undefined
                : column.total?.(rows, subtotalContext) ?? '',
          }
        })}
      />
    )
  }

  function renderHoldingsGroupRow(
    group: HoldingsGroup,
    level: 'category' | 'subgroup',
  ) {
    if (!columnContext) {
      return null
    }
    const groupContext = { ...columnContext, riskMetrics: securityRiskMetrics?.groups.get(group.key) }
    return (
      <tr className={`holdings-group-row holdings-${level}-row`}>
        {visibleColumns.map((column, index) => {
          const aggregationKind = HOLDINGS_GROUP_AGGREGATION_KIND[column.key]
          const canAggregate = aggregationKind !== 'none'
          const classNames = [
            holdingsAlignmentClass(column),
            isHoldingsChartColumn(column.key) ? 'chart-cell' : '',
            canAggregate ? column.totalClassName?.(group.rows, groupContext) ?? '' : '',
            index === 0 ? 'holdings-group-name-cell' : '',
          ]
            .filter(Boolean)
            .join(' ')
          return (
            <td
              key={column.key}
              data-column-key={column.key}
              data-aggregation-kind={aggregationKind}
              className={classNames || undefined}
            >
              {index === 0 ? (
                <div className={`holdings-group-header holdings-${level}-header`}>
                  <span className="holdings-group-title">{group.label || 'Unassigned'}</span>
                  <span className="holdings-group-count">{formatNumber(group.rows.length, 0)}</span>
                </div>
              ) : (
                canAggregate ? column.total?.(group.rows, groupContext) ?? '' : ''
              )}
            </td>
          )
        })}
      </tr>
    )
  }

  useEffect(() => {
    if (!portfolioId) {
      persistedHoldingsViewStoreRef.current = null
      setHoldingsViewStoreReadyPortfolioId(null)
      setHoldingsViewStoreError(null)
      return
    }

    let cancelled = false
    const defaultStore = normalizeHoldingsViewStore(null)
    persistedHoldingsViewStoreRef.current = null
    setHoldingsViewStoreReadyPortfolioId(null)
    setHoldingsViewStoreError(null)
    setHoldingsViewStore(defaultStore)
    setActiveHoldingsViewId(defaultStore.activeViewId)
    applyHoldingsViewState(resolveHoldingsViewState(defaultStore, defaultStore.activeViewId))
    getPortfolioTableViewStore<HoldingsViewStore>(portfolioId, 'holdings')
      .then((response) => {
        if (cancelled) {
          return
        }
        const nextStore = response.store
          ? normalizeHoldingsViewStore(response.store)
          : defaultStore
        persistedHoldingsViewStoreRef.current = {
          portfolioId,
          serializedStore: response.store
            ? JSON.stringify(response.store)
            : JSON.stringify(nextStore),
        }
        setHoldingsViewStore(nextStore)
        setActiveHoldingsViewId(nextStore.activeViewId)
        applyHoldingsViewState(resolveHoldingsViewState(nextStore, nextStore.activeViewId))
        setHoldingsViewStoreReadyPortfolioId(portfolioId)
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setHoldingsViewStoreError(
            `Holdings table views unavailable: ${
              requestError instanceof Error ? requestError.message : 'backend read failed.'
            }`,
          )
        }
      })

    return () => {
      cancelled = true
    }
  }, [holdingsViewLoadRetryToken, portfolioId])

  useEffect(() => {
    if (!portfolioId || holdingsViewStoreReadyPortfolioId !== portfolioId) {
      return
    }
    const serializedStore = JSON.stringify(holdingsViewStore)
    if (
      persistedHoldingsViewStoreRef.current?.portfolioId === portfolioId
      && persistedHoldingsViewStoreRef.current.serializedStore === serializedStore
    ) {
      return
    }
    holdingsViewSaveQueueRef.current
      .enqueue(() => savePortfolioTableViewStore(portfolioId, 'holdings', holdingsViewStore))
      .then(
        () => {
          if (currentPortfolioIdRef.current === portfolioId) {
            persistedHoldingsViewStoreRef.current = { portfolioId, serializedStore }
            setHoldingsViewStoreError(null)
          }
        },
        (requestError: unknown) => {
          if (currentPortfolioIdRef.current === portfolioId) {
            setHoldingsViewStoreError(
              `Failed to save holdings table views: ${
                requestError instanceof Error ? requestError.message : 'backend write failed.'
              }`,
            )
          }
        },
      )
  }, [
    holdingsViewSaveRetryToken,
    holdingsViewStore,
    holdingsViewStoreReadyPortfolioId,
    portfolioId,
  ])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    try {
      window.localStorage.setItem(HOLDINGS_COLUMN_WIDTHS_STORAGE_KEY, JSON.stringify(holdingsColumnWidths))
    } catch {
      return
    }
  }, [holdingsColumnWidths])

  useEffect(() => {
    function handleResizeMove(event: MouseEvent) {
      if (!holdingsColumnResizeState.current) {
        return
      }
      const { column, startX, startWidth } = holdingsColumnResizeState.current
      const nextWidth = clampHoldingsColumnWidth(startWidth + event.clientX - startX)
      pendingHoldingsColumnResize.current = { column, width: nextWidth }
      if (holdingsColumnResizeFrame.current == null) {
        holdingsColumnResizeFrame.current = window.requestAnimationFrame(() => {
          if (pendingHoldingsColumnResize.current) {
            const { column: pendingColumn, width } = pendingHoldingsColumnResize.current
            setHoldingsColumnWidths((current) => ({ ...current, [pendingColumn]: width }))
          }
          pendingHoldingsColumnResize.current = null
          holdingsColumnResizeFrame.current = null
        })
      }
    }

    function handleResizeEnd() {
      const wasResizing = Boolean(holdingsColumnResizeState.current)
      holdingsColumnResizeState.current = null
      if (holdingsColumnResizeFrame.current != null) {
        window.cancelAnimationFrame(holdingsColumnResizeFrame.current)
        holdingsColumnResizeFrame.current = null
      }
      if (pendingHoldingsColumnResize.current) {
        const { column, width } = pendingHoldingsColumnResize.current
        setHoldingsColumnWidths((current) => ({ ...current, [column]: width }))
      }
      pendingHoldingsColumnResize.current = null
      if (wasResizing) {
        setHoldingsColumnResizing(false)
      }
      document.body.style.cursor = ''
    }

    window.addEventListener('mousemove', handleResizeMove)
    window.addEventListener('mouseup', handleResizeEnd)
    return () => {
      window.removeEventListener('mousemove', handleResizeMove)
      window.removeEventListener('mouseup', handleResizeEnd)
    }
  }, [])

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
      loadedHoldingsInputRef.current = null
      setWorkspace(null)
      setError(null)
      setLoading(false)
      return
    }

    const inputKey = `${portfolioId}:${requestedAsOfDate}:${riskPolicyRevision}`
    if (loadedHoldingsInputRef.current?.key === inputKey && loadedHoldingsInputRef.current.full) {
      // Full inputs also satisfy a compact view; changing visible columns does
      // not change the valuation date or require another holdings calculation.
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)

    getHoldingsWorkspace(portfolioId, {
      as_of_date: requestedAsOfDate || undefined,
      ...(holdingsNeedsDetails ? { include_details: true } : {}),
    })
      .then((response) => {
        if (cancelled) {
          return
        }
        loadedHoldingsInputRef.current = { key: inputKey, full: response.detail_level === 'full' || holdingsNeedsDetails }
        setWorkspace(response)
        setError(null)
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          loadedHoldingsInputRef.current = null
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Failed to load holdings workspace.',
          )
          setWorkspace(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [holdingsNeedsDetails, portfolioId, requestedAsOfDate, riskPolicyRevision])

  useEffect(() => {
    if (!portfolioId) {
      setTaxonomyCatalog(null)
      setTaxonomyError(null)
      return
    }
    let cancelled = false
    setTaxonomyError(null)
    getPortfolioTaxonomyCatalog(portfolioId)
      .then((response) => {
        if (!cancelled) {
          setTaxonomyCatalog(response)
        }
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setTaxonomyCatalog(null)
          setTaxonomyError(
            requestError instanceof Error ? requestError.message : 'Failed to load current taxonomy catalog.',
          )
        }
      })
    return () => {
      cancelled = true
    }
  }, [portfolioId, riskPolicyRevision])

  return (
    <>
      <NoticeToast notice={viewToast} onDismiss={() => setViewToast(null)} />
      <PortfolioWorkspaceLayout
        activeSection="Holdings"
        busy={loading || waitingForTaxonomy}
      >
      <section className="portfolio-detail-surface holdings-surface">
        <div className="holdings-filter-bar">
          <div className="holdings-filter-group">
            <label>
              <input
                className="holdings-filter-input"
                type="date"
                aria-label="As Of Date"
                value={requestedAsOfDate || workspace?.as_of_date || ''}
                onChange={(event) => updateSearchParam('as_of_date', event.target.value || null)}
              />
            </label>
          </div>
          <div className="holdings-filter-actions">
            <QualityWarningsNotice warnings={workspace?.quality_warnings} />
            <DownloadFormatMenu
              wrapperClassName="portfolio-download-menu"
              buttonClassName="portfolio-table-toolbar-button"
              menuClassName="portfolio-download-menu-list"
              itemClassName="portfolio-download-menu-item"
              disabled={!workspace || !workspace.rows.length}
              onSelect={handleDownload}
            />
          </div>
        </div>
        {loading || waitingForTaxonomy ? <CalculationStatus /> : null}
        {loading && workspace ? (
          <div className="portfolio-detail-meta"><span>As Of Date</span>: {workspace.as_of_date}</div>
        ) : null}
        {error ? <div className="error-state">{error}</div> : null}
        {holdingsViewStoreError ? (
          <div className="inline-notice inline-notice-error holdings-view-error" role="alert">
            <span>{holdingsViewStoreError}</span>
            <button
              type="button"
              onClick={() => {
                if (holdingsViewStoreReadyPortfolioId === portfolioId) {
                  setHoldingsViewSaveRetryToken((value) => value + 1)
                } else {
                  setHoldingsViewLoadRetryToken((value) => value + 1)
                }
              }}
            >
              {holdingsViewStoreReadyPortfolioId === portfolioId ? 'Retry save' : 'Retry views'}
            </button>
          </div>
        ) : null}
        <HoldingsOperationalStatus alerts={workspace?.operational_alerts} />
        {taxonomyError ? (
          <div className="inline-notice inline-notice-warning">{taxonomyError}</div>
        ) : null}
        {!waitingForTaxonomy && !error && workspace && columnContext ? (
          <>
            {!workspace.rows.length ? (
              <div className="empty-state" role="status">
                No holdings as of {workspace.as_of_date}.
              </div>
            ) : null}
            {sortedSecurityRows.length ? (
              <section className="holdings-section" aria-labelledby="holdings-securities-heading">
                <div className="holdings-section-heading">
                  <div className="holdings-section-title">
                    <h2 id="holdings-securities-heading">Securities</h2>
                    <span className="holdings-section-count">
                      {formatNumber(sortedSecurityRows.length, 0)}
                    </span>
                  </div>
                  <div className="holdings-section-heading-actions">
                    {holdingsViewStoreReadyPortfolioId === portfolioId ? (
                      <PortfolioTableViewControls
                        views={holdingsViews}
                        activeViewId={activeHoldingsViewId}
                        canDelete
                        onSelect={handleSelectHoldingsView}
                        onSaveAs={handleSaveHoldingsViewAs}
                        onDelete={handleDeleteHoldingsView}
                      />
                    ) : (
                      <span className="portfolio-table-view-status">
                        {holdingsViewStoreError ? 'Views unavailable' : 'Loading views'}
                      </span>
                    )}
                    <button
                      type="button"
                      className="portfolio-table-toolbar-button"
                      onClick={() => {
                        setHoldingsColumnDraft(holdingsColumns)
                        setHoldingsColumnsOpen(true)
                      }}
                      aria-label="Securities Columns"
                    >
                      Columns
                    </button>
                    <button
                      type="button"
                      className="portfolio-table-toolbar-button"
                      onClick={() => setHoldingsGroupByOpen(true)}
                    >
                      Group By{'\u00A0: '}
                      {selectedGroupByOption.label}{holdingsGroupBy.startsWith('taxonomy_') && groupingTaxonomy ? ` · ${groupingTaxonomy.name}` : ''}
                    </button>
                  </div>
                </div>
                <div
                  className={`table-shell holdings-table-shell ${holdingsTablePan.isPanning ? 'is-panning' : ''}`}
                  ref={holdingsTableShellRef}
                  {...holdingsTablePan.handlers}
                >
                  <table
                    className="holdings-table holdings-main-table"
                    aria-label="Security holdings"
                    style={{ minWidth: `${displayHoldingsTableMinWidth}px` }}
                  >
                    <colgroup>
                      {visibleColumns.map((column) => (
                        <col
                          key={column.key}
                          style={{ width: `${displayHoldingsColumnWidths[column.key]}px` }}
                        />
                      ))}
                    </colgroup>
                    <thead>
                      <tr>
                        {visibleColumns.map((column) => {
                          const width = displayHoldingsColumnWidths[column.key]
                          return (
                            <th
                              key={column.key}
                              className={[
                                holdingsAlignmentClass(column),
                                isHoldingsChartColumn(column.key) ? 'chart-cell' : '',
                                column.key !== LOCKED_HOLDINGS_COLUMN ? 'holdings-column-draggable' : '',
                                holdingsColumnDropTarget === column.key ? 'holdings-column-drop-target' : '',
                              ]
                                .filter(Boolean)
                                .join(' ') || undefined}
                              scope="col"
                              aria-sort={
                                holdingsSortField === column.key
                                  ? holdingsSortDirection === 'asc'
                                    ? 'ascending'
                                    : 'descending'
                                  : 'none'
                              }
                              draggable={column.key !== LOCKED_HOLDINGS_COLUMN}
                              style={{ width: `${width}px` }}
                              onDragStart={(event) => handleHoldingsColumnDragStart(event, column.key)}
                              onDragOver={(event) => handleHoldingsColumnDragOver(event, column.key)}
                              onDragLeave={() => setHoldingsColumnDropTarget(null)}
                              onDrop={(event) => handleHoldingsColumnDrop(event, column.key)}
                              onDragEnd={() => setHoldingsColumnDropTarget(null)}
                            >
                              {renderHoldingsSortHeader(column)}
                              <span
                                className="holdings-th-resizer"
                                role="separator"
                                aria-label={`Resize ${column.label} column`}
                                aria-orientation="vertical"
                                aria-valuemin={HOLDINGS_COLUMN_MIN_WIDTH}
                                aria-valuemax={HOLDINGS_COLUMN_MAX_WIDTH}
                                aria-valuenow={Math.round(width)}
                                tabIndex={0}
                                onKeyDown={(event) => {
                                  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
                                    return
                                  }
                                  event.preventDefault()
                                  const delta = event.key === 'ArrowLeft' ? -10 : 10
                                  setHoldingsColumnWidths((current) => ({
                                    ...current,
                                    [column.key]: clampHoldingsColumnWidth((current[column.key] ?? width) + delta),
                                  }))
                                }}
                                onMouseDown={(event) => handleHoldingsColumnResizeStart(event, column.key, width)}
                              />
                            </th>
                          )
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {holdingsGroupBy !== 'none'
                        ? groupedSecurityRows.map((group) => (
                            <Fragment key={group.key}>
                              {renderHoldingsGroupRow(group, 'subgroup')}
                              {group.rows.map((row) => renderHoldingDataRow(row))}
                            </Fragment>
                          ))
                        : sortedSecurityRows.map((row) => renderHoldingDataRow(row))}
                      {renderHoldingsSubtotalRow(
                        sortedSecurityRows,
                        `Securities Subtotal (${workspace.base_currency})`,
                        'total-row holdings-section-subtotal-row',
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}
            <HoldingsSectionTables
              workspace={workspace}
              derivativeRows={derivativeRows}
              cashRows={cashRows}
              onSelectHolding={(row) => handleSelectInstrument(holdingReferenceId(row), row.line_id)}
              onVisibleColumnsChange={handleHoldingsSectionVisibleColumnsChange}
            />
          </>
        ) : null}
      </section>

      {holdingsColumnsOpen ? (
        <div className="portfolio-table-config-backdrop" onClick={() => setHoldingsColumnsOpen(false)}>
          <div
            ref={holdingsColumnsDialogRef}
            className="portfolio-table-config-modal portfolio-table-config-columns-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Choose security columns"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="portfolio-table-config-header">
              <div className="panel-title">Columns</div>
              <button type="button" onClick={() => setHoldingsColumnsOpen(false)}>
                Close
              </button>
            </div>

            <div className="portfolio-table-config-search">
              <input
                className="portfolio-table-config-search-input"
                placeholder="Search columns"
                value={holdingsColumnSearch}
                onChange={(event) => setHoldingsColumnSearch(event.target.value)}
              />
            </div>

            <div className="portfolio-table-config-grid">
              <div className="portfolio-table-config-categories">
                {HOLDINGS_COLUMN_GROUPS.map((group) => (
                  <button
                    type="button"
                    className={
                      group.label === holdingsColumnCategory
                        ? 'portfolio-table-config-category-item portfolio-table-config-category-item-active'
                        : 'portfolio-table-config-category-item'
                    }
                    key={group.label}
                    onClick={() => setHoldingsColumnCategory(group.label)}
                  >
                    {group.label}
                  </button>
                ))}
              </div>

              <div className="portfolio-table-config-fields">
                {filteredHoldingsColumns.length ? (
                  filteredHoldingsColumns.map(({ column, groupLabel }) => {
                    const locked = column === LOCKED_HOLDINGS_COLUMN
                    return (
                      <label className="portfolio-table-config-field-item" key={`${groupLabel}:${column}`}>
                        <input
                          type="checkbox"
                          checked={holdingsColumnDraft.includes(column)}
                          disabled={locked}
                          onChange={(event) => handleHoldingsColumnDraftToggle(column, event.target.checked)}
                        />
                        <div>
                          <div className="portfolio-table-config-field-label">{HOLDINGS_COLUMN_DEFINITIONS[column].label}</div>
                          <div className="portfolio-table-config-field-meta">
                            {column}
                            {holdingsColumnSearch.trim() ? ` · ${groupLabel}` : ''}
                            {locked ? ' · required' : ''}
                          </div>
                        </div>
                      </label>
                    )
                  })
                ) : (
                  <div className="portfolio-table-config-field-empty">No columns.</div>
                )}
              </div>

            </div>

            <div className="portfolio-table-config-actions portfolio-table-config-actions-sticky">
              <button
                type="button"
                onClick={() => {
                  setHoldingsColumnDraft(holdingsColumns)
                  setHoldingsColumnsOpen(false)
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="button-primary"
                onClick={() => {
                  const nextColumns = normalizeHoldingsColumns(holdingsColumnDraft)
                  setHoldingsColumns(nextColumns)
                  setHoldingsColumnDraft(nextColumns)
                  updateHoldingsViewState({
                    ...currentHoldingsViewState,
                    columns: nextColumns,
                  })
                  setHoldingsColumnsOpen(false)
                }}
              >
                Update
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {holdingsGroupByOpen ? (
        <div className="portfolio-table-config-backdrop" onClick={() => setHoldingsGroupByOpen(false)}>
          <div
            ref={holdingsGroupByDialogRef}
            className="portfolio-table-config-modal portfolio-table-config-compact-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Choose grouping"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="portfolio-table-config-header">
              <div className="panel-title">Group By</div>
              <button type="button" onClick={() => setHoldingsGroupByOpen(false)}>
                Close
              </button>
            </div>
            <div className="portfolio-table-config-body portfolio-table-config-groupby-list">
              <label>{zh ? '分类' : 'Taxonomy'} <select aria-label={zh ? '持仓分组分类' : 'Holdings grouping taxonomy'} value={groupingTaxonomy?.taxonomy_id ?? ''}
                onChange={(event) => { setGroupingTaxonomyId(event.target.value); setHoldingsGroupBy('taxonomy_top') }}>
                {!groupingTaxonomy && <option value="">{zh ? '暂无分类' : 'No taxonomy'}</option>}
                {(taxonomyCatalog?.taxonomies ?? []).filter((taxonomy) => taxonomy.status === 'active').map((taxonomy) => <option key={taxonomy.taxonomy_id} value={taxonomy.taxonomy_id}>{taxonomy.name}</option>)}
              </select></label>
              {HOLDINGS_GROUP_BY_OPTIONS.map((option) => (
                <button
                  type="button"
                  className={`portfolio-table-config-groupby-option ${option.value === holdingsGroupBy ? 'portfolio-table-config-groupby-option-active' : ''}`}
                  key={option.value}
                  onClick={() => handleGroupByChange(option.value)}
                >
                  <span>{option.label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      </PortfolioWorkspaceLayout>
    </>
  )
}
