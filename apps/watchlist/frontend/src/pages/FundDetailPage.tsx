import {
  Fragment,
  startTransition,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import { Link, useParams } from 'react-router-dom'

import LoadingOverlay from '../components/LoadingOverlay'
import {
  type CalculationFrequencyProfile,
  type CorporateActionEvent,
  type FundChartPoint,
  type FundDocumentsResponse,
  type FundLibraryItem,
  type FundNavSeriesResponse,
  type FundPeopleResponse,
  type FundPerformanceResponse,
  type FundPriceResponse,
  type FundRatingsResponse,
  type FundResearchResponse,
  type FundRiskResponse,
  type FundStrategyResponse,
  type FundSummaryResponse,
  type FundTaxonomyTreeNode,
  type FundTaxonomyTreeResponse,
  type FundExposureHoldingsResponse as FundPortfolioHoldingsResponse,
  type FundExposureResponse as FundPortfolioResponse,
  type InstrumentAttributeValuesResponse,
  type InstrumentAttributeDefinition,
  getFundTaxonomyTree,
  getInstrumentAttributes,
  getInstrumentDocuments,
  getInstrumentExposureHoldings as getInstrumentPortfolioHoldings,
  getInstrumentExposureSummary as getInstrumentPortfolioSummary,
  getInstrumentLibrary,
  getInstrumentNavSeries,
  getInstrumentPeople,
  getInstrumentPerformance,
  getInstrumentPrice,
  getInstrumentRatings,
  getInstrumentResearch,
  getInstrumentRisk,
  getInstrumentSummary,
  getInstrumentStrategy,
  uploadInstrumentDocument,
  updateFundTaxonomy,
  updateInstrumentAttributes,
  updateInstrumentDocuments,
  updateInstrumentPeople,
  updateInstrumentPrice,
  updateInstrumentResearch,
  updateInstrumentStrategy,
} from '../lib/api'
import {
  formatBoolean,
  formatCompactCurrency,
  formatDate,
  formatDateTime,
  formatLabel,
  formatNumber,
  formatPercent,
} from '../lib/format'
import { buildWatchlistPath, PLATFORM_HOME_URL } from '../lib/navigation'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import {
  beginDetailRequest,
  completeDetailRequest,
  createDetailRequestCoordinator,
} from '../lib/detailRequestCoordinator'
import { rejectedLabels, settledValue } from '../lib/settled'

type FundDetailBundle = {
  summary: FundSummaryResponse
  library: FundLibraryItem[]
  performance: FundPerformanceResponse
  risk: FundRiskResponse
  portfolio: FundPortfolioResponse
  holdings: FundPortfolioHoldingsResponse
  ratings: FundRatingsResponse
  people: FundPeopleResponse
  strategy: FundStrategyResponse
  price: FundPriceResponse
  documents: FundDocumentsResponse
  research: FundResearchResponse
  navSeries: FundNavSeriesResponse
}

type PeerComparisonMetric = NonNullable<
  NonNullable<FundPerformanceResponse['peer_comparison']>['metrics']
>[number]

type EditableKeyValueRow = {
  id: string
  key: string
  value: string
}

type PositionedPoint = FundChartPoint & {
  x: number
  y: number
}

type ChartGeometry = {
  width: number
  height: number
  paddingLeft: number
  paddingRight: number
  paddingTop: number
  paddingBottom: number
}

type EditableListRow = {
  id: string
  value: string
}

type EditableTeamRow = {
  id: string
  name: string
  role: string
  start_date: string
}

type EditableDocumentRow = {
  id: string
  title: string
  document_type: string
  as_of_date: string
  source: string
  status: string
  version_label: string
  file_name: string
  download_url: string
  file_size: string
  content_type: string
  uploaded_at: string
  notes: string
  stored_file_name: string
}

type EditableImportRow = {
  id: string
  import_type: string
  received_at: string
  source: string
  status: string
  file_name: string
}

type EditableExtractionRow = {
  id: string
  document_title: string
  extract_type: string
  status: string
  adopted_version: string
  updated_at: string
}

type TimelineNoteImportance = 'low' | 'medium' | 'high'

type ResearchTimelineNote = {
  note_id: string
  note_date: string
  title: string
  summary: string
  body: string
  importance: TimelineNoteImportance
  tags: string[]
}

type TimelineNoteDraft = {
  note_id: string
  note_date: string
  title: string
  summary: string
  body: string
  importance: TimelineNoteImportance
  tagsText: string
}

type PeopleDraft = {
  overviewRows: EditableKeyValueRow[]
  teamRows: EditableTeamRow[]
  noteRows: EditableListRow[]
}

type StrategyDraft = {
  summary: string
  investment_objective: string
  processRows: EditableListRow[]
  riskControlRows: EditableListRow[]
  noteRows: EditableListRow[]
}

type PriceDraft = {
  overviewRows: EditableKeyValueRow[]
  distribution_policy: string
  policy_text: string
  feeNoteRows: EditableListRow[]
  noteRows: EditableListRow[]
}

type DocumentsDraft = {
  currentDocumentRows: EditableDocumentRow[]
  importRows: EditableImportRow[]
  extractionRows: EditableExtractionRow[]
  noteRows: EditableListRow[]
}

type ResearchDraft = {
  overviewRows: EditableKeyValueRow[]
}

type ChartTimelineNoteContextMenu = {
  clientX: number
  clientY: number
  anchorDate: string
}

type PriceEditSection = 'table'

type DetailTab =
  | 'overview'
  | 'performance'
  | 'risk'
  | 'price'
  | 'exposure'
  | 'people'
  | 'strategy'
  | 'documents'
  | 'research'
  | 'monitoring'

type DetailKind = 'fund' | 'index'
type ChartRange = '1M' | '3M' | '6M' | 'YTD' | '1Y' | '3Y' | '5Y' | '10Y' | 'MAX' | 'CUSTOM'
type QuoteBasis = 'nav' | 'nav_with_dividend'
type ChartFrequency = 'daily' | 'weekly' | 'monthly'
type ChartDisplayStyle = 'mountain' | 'line' | 'dot'
type ChartScale = 'linear' | 'logarithmic'
type QuoteChartMenu = 'settings'
type ChartHoverPanel = 'primary' | 'drawdown'
type ChartHoverCursor = {
  xRatio: number
  y: number
}
type ChartAxisTick = {
  date: string
  xRatio: number
}

type PerformanceMetricPeriodKey = '1W' | 'MTD' | 'YTD' | '1Y' | '2Y' | '3Y' | '5Y' | 'SI'
type PerformanceMatrixMode = 'values' | 'peer_percentile' | 'peer_rank' | 'peer_median_delta'
type PerformanceMatrixRowKey =
  | 'period_return'
  | 'annualized_return'
  | 'annualized_volatility'
  | 'excess_return'
  | 'sharpe_ratio'
  | 'sortino_ratio'
  | 'calmar_ratio'
  | 'information_ratio'
  | 'tracking_error'
  | 'beta'
  | 'max_drawdown'
  | 'recovery_days'
  | 'upside_capture'
  | 'downside_capture'
type RollingRiskWindowMonths = 1 | 3 | 6 | 12
type PerformanceMetricSnapshot = {
  periodReturn: number | null
  annualizedReturn: number | null
  annualizedVolatility: number | null
  annualizedDownsideDeviation: number | null
  sharpe: number | null
  sortino: number | null
  calmar: number | null
  maxDrawdown: number | null
  recoveryDays: number | null
  recoveryOpen: boolean
}

type PerformanceRelativeSnapshot = {
  informationRatio: number | null
  trackingError: number | null
  beta: number | null
  upsideCapture: number | null
  downsideCapture: number | null
}

type PeriodicReturnPoint = {
  startDate: string
  endDate: string
  value: number
}

const PERFORMANCE_METRIC_PERIODS: Array<{
  key: PerformanceMetricPeriodKey
  label: string
}> = [
  { key: '1W', label: '1W' },
  { key: 'MTD', label: 'MTD' },
  { key: 'YTD', label: 'YTD' },
  { key: '1Y', label: '1Y' },
  { key: '2Y', label: '2Y' },
  { key: '3Y', label: '3Y' },
  { key: '5Y', label: '5Y' },
  { key: 'SI', label: 'SI' },
]

const PERFORMANCE_MATRIX_MODE_OPTIONS: Array<{
  value: PerformanceMatrixMode
  label: string
}> = [
  { value: 'values', label: 'Values' },
  { value: 'peer_percentile', label: 'Peer Percentile' },
  { value: 'peer_rank', label: 'Peer Rank' },
  { value: 'peer_median_delta', label: 'vs Median' },
]

const RISK_MATRIX_PERIOD_KEYS = new Set<PerformanceMetricPeriodKey>(['1Y', '3Y', '5Y', 'SI'])

const ROLLING_RISK_WINDOW_OPTIONS: Array<{
  months: RollingRiskWindowMonths
  label: string
}> = [
  { months: 1, label: '1M' },
  { months: 3, label: '3M' },
  { months: 6, label: '6M' },
  { months: 12, label: '1Y' },
]

type WatchlistRollingRiskSettings = {
  windowMonths: RollingRiskWindowMonths
  chartDisplayStyle: ChartDisplayStyle
}

const WATCHLIST_ROLLING_RISK_SETTINGS_STORAGE_KEY = 'portfolio_ops.watchlist.instrument.risk.rolling.settings.v1'
const DEFAULT_ROLLING_RISK_SETTINGS: WatchlistRollingRiskSettings = {
  windowMonths: 1,
  chartDisplayStyle: 'mountain',
}

function normalizeRollingRiskWindowMonths(value: unknown, fallback: RollingRiskWindowMonths) {
  return ROLLING_RISK_WINDOW_OPTIONS.some((option) => option.months === value)
    ? (value as RollingRiskWindowMonths)
    : fallback
}

function normalizeChartDisplayStyle(value: unknown, fallback: ChartDisplayStyle) {
  return value === 'mountain' || value === 'line' || value === 'dot' ? value : fallback
}

function loadWatchlistRollingRiskSettings(): WatchlistRollingRiskSettings {
  if (typeof window === 'undefined') {
    return DEFAULT_ROLLING_RISK_SETTINGS
  }

  try {
    const rawValue = window.localStorage.getItem(WATCHLIST_ROLLING_RISK_SETTINGS_STORAGE_KEY)
    const record = rawValue ? (JSON.parse(rawValue) as Record<string, unknown>) : {}
    return {
      windowMonths: normalizeRollingRiskWindowMonths(
        record.windowMonths,
        DEFAULT_ROLLING_RISK_SETTINGS.windowMonths,
      ),
      chartDisplayStyle: normalizeChartDisplayStyle(
        record.chartDisplayStyle,
        DEFAULT_ROLLING_RISK_SETTINGS.chartDisplayStyle,
      ),
    }
  } catch {
    return DEFAULT_ROLLING_RISK_SETTINGS
  }
}

function saveWatchlistRollingRiskSettings(settings: WatchlistRollingRiskSettings) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    window.localStorage.setItem(WATCHLIST_ROLLING_RISK_SETTINGS_STORAGE_KEY, JSON.stringify(settings))
  } catch {
    // Ignore storage failures; the UI still works with in-memory state.
  }
}

const TAB_ORDER: DetailTab[] = [
  'overview',
  'performance',
  'risk',
  'price',
  'exposure',
  'people',
  'strategy',
  'documents',
  'research',
  'monitoring',
]

const CORE_TABS: DetailTab[] = ['overview', 'performance', 'risk', 'price', 'exposure', 'people', 'strategy']
const INDEX_TABS: DetailTab[] = ['overview', 'performance', 'risk']

type LocalizedText = {
  en: string
  zh: string
}

const TAB_LABELS: Record<DetailTab, LocalizedText> = {
  overview: { en: 'Overview', zh: '总览' },
  performance: { en: 'Performance', zh: '业绩' },
  risk: { en: 'Risk', zh: '风险' },
  price: { en: 'Price', zh: '费用' },
  exposure: { en: 'Exposure', zh: '持仓' },
  people: { en: 'People', zh: '团队' },
  strategy: { en: 'Strategy', zh: '策略' },
  documents: { en: 'Documents', zh: '文档' },
  research: { en: 'Research', zh: '研究' },
  monitoring: { en: 'Monitoring', zh: '监控' },
}

const NAV_BASIS_LABELS: Record<string, LocalizedText> = {
  auto: { en: 'Auto', zh: '自动' },
  nav_with_dividend: { en: 'NAV with Dividends', zh: '累计净值' },
  nav: { en: 'NAV', zh: '单位净值' },
}

const NAV_BASIS_SOURCE_LABELS: Record<string, string> = {
  nav_with_dividend_series: 'NAV with Dividend Series',
  nav_series: 'NAV Series',
  manual_nav_editor: 'Manual Editor',
  shared: 'Shared Registry',
  local: 'Local Facts',
}

const PEOPLE_PRIMARY_OVERVIEW_FIELDS: Array<{
  key: string
  label: string
  type: 'date' | 'number' | 'text'
}> = [
  { key: 'inception_date', label: 'Inception Date', type: 'date' },
  { key: 'number_of_managers', label: 'Number of Managers', type: 'number' },
  { key: 'average_tenure_years', label: 'Average Tenure', type: 'number' },
  { key: 'longest_tenure_years', label: 'Longest Tenure', type: 'number' },
  { key: 'advisor', label: 'Advisor', type: 'text' },
  { key: 'sub_advisor', label: 'Sub-Advisor', type: 'text' },
]

const PEOPLE_PRIMARY_OVERVIEW_FIELD_KEYS = new Set(
  PEOPLE_PRIMARY_OVERVIEW_FIELDS.map((field) => field.key),
)

const RESEARCH_OVERVIEW_FIELDS: Array<{
  key: string
  label: string
  type: 'date' | 'text'
}> = [
  { key: 'current_view', label: 'Current View', type: 'text' },
  { key: 'research_view', label: 'Research View', type: 'text' },
  { key: 'dd_status', label: 'DD Status', type: 'text' },
  { key: 'odd_status', label: 'ODD Status', type: 'text' },
  { key: 'ic_status', label: 'IC Status', type: 'text' },
  { key: 'decision', label: 'Decision', type: 'text' },
  { key: 'next_review_date', label: 'Next Review Date', type: 'date' },
  { key: 'primary_analyst', label: 'Primary Analyst', type: 'text' },
]

const QUOTE_RANGE_OPTIONS: Array<{ value: ChartRange | 'YTD' | 'CUSTOM'; label: string }> = [
  { value: '1M', label: '1M' },
  { value: '3M', label: '3M' },
  { value: '6M', label: '6M' },
  { value: 'YTD', label: 'YTD' },
  { value: '1Y', label: '1Y' },
  { value: '3Y', label: '3Y' },
  { value: '5Y', label: '5Y' },
  { value: '10Y', label: '10Y' },
  { value: 'MAX', label: 'MAX' },
  { value: 'CUSTOM', label: 'Custom' },
]

const QUOTE_BASIS_LABELS: Record<QuoteBasis, LocalizedText> = {
  nav: { en: 'NAV', zh: '单位净值' },
  nav_with_dividend: { en: 'NAV with Dividends', zh: '累计净值' },
}

const SYSTEM_LABELS: Record<string, LocalizedText> = {
  analystStance: { en: 'Analyst Stance', zh: '投研观点' },
  basis: { en: 'Basis', zh: '口径' },
  cancel: { en: 'Cancel', zh: '取消' },
  classificationPath: { en: 'Classification Path', zh: '分类路径' },
  currentPath: { en: 'Current Path', zh: '当前路径' },
  documentTitle: { en: 'Documents', zh: '文档' },
  documentUploaded: { en: 'Document uploaded.', zh: '文档已上传。' },
  downloadPdf: { en: 'Download PDF', zh: '下载 PDF' },
  fileName: { en: 'File Name', zh: '文件名' },
  fundDetail: { en: 'Fund Detail', zh: '基金详情' },
  indexed: { en: 'Indexed to 1.00', zh: '归一到 1.00' },
  indexDetail: { en: 'Index Detail', zh: '指数详情' },
  notes: { en: 'Notes', zh: '备注' },
  noDocuments: { en: 'No documents yet.', zh: '暂无文档。' },
  noNavHistory: {
    en: 'No NAV history is available yet. Add shared market data in Database Dashboard to materialize the quote curve.',
    zh: '暂无净值历史。请先在数据库面板补充共享行情数据，生成报价曲线。',
  },
  optionalNote: { en: 'Optional note', zh: '可选备注' },
  peer: { en: 'Peer', zh: '同类' },
  pickFile: { en: 'Please choose a file to upload.', zh: '请先选择要上传的文件。' },
  regime: { en: 'Regime', zh: '体系' },
  save: { en: 'Save', zh: '保存' },
  saving: { en: 'Saving...', zh: '保存中...' },
  selectParentFirst: { en: 'Select parent first', zh: '请先选择上一级' },
  settings: { en: 'Settings', zh: '设置' },
  stopHere: { en: 'Stop here', zh: '停在此级' },
  taxonomySettings: { en: 'Taxonomy Settings', zh: '分类设置' },
  unclassified: { en: 'Unclassified', zh: '未分类' },
  unclassify: { en: 'Unclassify', zh: '取消分类' },
  uploadFailed: { en: 'Failed to upload document.', zh: '文档上传失败。' },
  unavailableBasis: {
    en: 'is unavailable for the current currency or date window. Switch Data Type, Currency, or range.',
    zh: '在当前币种或日期区间不可用。请切换数据类型、币种或区间。',
  },
  selectType: { en: 'Select type', zh: '选择类型' },
  size: { en: 'Size', zh: '大小' },
  status: { en: 'Status', zh: '状态' },
  title: { en: 'Title', zh: '标题' },
  type: { en: 'Type', zh: '类型' },
  upload: { en: 'Upload', zh: '上传' },
  uploading: { en: 'Uploading...', zh: '上传中...' },
  uploaded: { en: 'Uploaded', zh: '已上传' },
}

const SYSTEM_VALUE_LABELS: Record<string, LocalizedText> = {
  archived: { en: 'Archived', zh: '已归档' },
  cautious: { en: 'Cautious', zh: '谨慎' },
  current: { en: 'Current', zh: '当前' },
  exited: { en: 'Exited', zh: '退出' },
  fresh: { en: 'Fresh', zh: '新鲜' },
  'high conviction': { en: 'High Conviction', zh: '高置信' },
  invested: { en: 'Invested', zh: '在投' },
  partial: { en: 'Partial', zh: '部分' },
  paused: { en: 'Paused', zh: '暂停' },
  pending: { en: 'Pending', zh: '待处理' },
  pending_recalc: { en: 'Pending Recalc', zh: '待重算' },
  positive: { en: 'Positive', zh: '积极' },
  proposed: { en: 'Proposed', zh: '拟投' },
  stale: { en: 'Stale', zh: '过期' },
  unrated: { en: 'Unrated', zh: '未评级' },
  uploaded: { en: 'Uploaded', zh: '已上传' },
  watch: { en: 'Watch', zh: '观察' },
  factsheet: { en: 'Fact Sheet', zh: '要素表' },
  valuation_statement: { en: 'Valuation Statement', zh: '估值表' },
  due_diligence_report: { en: 'Due Diligence Report', zh: '尽调报告' },
  investment_memo: { en: 'Investment Memo', zh: '投资备忘录' },
  fund_contract: { en: 'Fund Contract', zh: '基金合同' },
  prospectus: { en: 'Prospectus', zh: '招募说明书' },
  periodic_report: { en: 'Monthly / Quarterly Report', zh: '月报/季报' },
  other: { en: 'Other', zh: '其他' },
  要素表: { en: 'Fact Sheet', zh: '要素表' },
  估值表: { en: 'Valuation Statement', zh: '估值表' },
  尽调报告: { en: 'Due Diligence Report', zh: '尽调报告' },
  投资备忘录: { en: 'Investment Memo', zh: '投资备忘录' },
  基金合同: { en: 'Fund Contract', zh: '基金合同' },
  招募说明书: { en: 'Prospectus', zh: '招募说明书' },
  '月报/季报': { en: 'Monthly / Quarterly Report', zh: '月报/季报' },
  公募: { en: 'Public Fund', zh: '公募' },
  股票型: { en: 'Equity', zh: '股票型' },
  标准股票型: { en: 'Standard Equity', zh: '标准股票型' },
  指数股票型: { en: 'Equity Index', zh: '指数股票型' },
  混合型: { en: 'Hybrid', zh: '混合型' },
  偏股型: { en: 'Equity-biased', zh: '偏股型' },
  灵活配置型: { en: 'Flexible Allocation', zh: '灵活配置型' },
  股债平衡型: { en: 'Balanced', zh: '股债平衡型' },
  偏债型: { en: 'Bond-biased', zh: '偏债型' },
  策略型: { en: 'Strategy', zh: '策略型' },
  债券型: { en: 'Bond', zh: '债券型' },
  纯债型: { en: 'Pure Bond', zh: '纯债型' },
  普通债券型: { en: 'Ordinary Bond', zh: '普通债券型' },
  可转债型: { en: 'Convertible Bond', zh: '可转债型' },
  指数债券型: { en: 'Bond Index', zh: '指数债券型' },
  同业存单型: { en: 'Certificate of Deposit', zh: '同业存单型' },
  QDII: { en: 'QDII', zh: 'QDII' },
  QDII房地产信托: { en: 'QDII REIT', zh: 'QDII房地产信托' },
  QDII股票型: { en: 'QDII Equity', zh: 'QDII股票型' },
  QDII混合型: { en: 'QDII Hybrid', zh: 'QDII混合型' },
  QDII商品型: { en: 'QDII Commodity', zh: 'QDII商品型' },
  QDII债券型: { en: 'QDII Bond', zh: 'QDII债券型' },
  商品型: { en: 'Commodity', zh: '商品型' },
  贵金属基金: { en: 'Precious Metals Fund', zh: '贵金属基金' },
  其他商品基金: { en: 'Other Commodity Fund', zh: '其他商品基金' },
  REITS: { en: 'REITs', zh: 'REITS' },
  FOF: { en: 'FOF', zh: 'FOF' },
  股票型FOF: { en: 'Equity FOF', zh: '股票型FOF' },
  债券型FOF: { en: 'Bond FOF', zh: '债券型FOF' },
  混合型FOF: { en: 'Hybrid FOF', zh: '混合型FOF' },
  养老目标FOF: { en: 'Pension Target FOF', zh: '养老目标FOF' },
  私募: { en: 'Private Fund', zh: '私募' },
  股票策略: { en: 'Equity Strategy', zh: '股票策略' },
  主观多头: { en: 'Discretionary Long', zh: '主观多头' },
  主观选股: { en: 'Discretionary Stock Picking', zh: '主观选股' },
  定增打新: { en: 'Private Placement / IPO', zh: '定增打新' },
  量化多头: { en: 'Quant Long', zh: '量化多头' },
  '300指增': { en: 'CSI 300 Enhanced', zh: '300指增' },
  '500指增': { en: 'CSI 500 Enhanced', zh: '500指增' },
  '1000指增': { en: 'CSI 1000 Enhanced', zh: '1000指增' },
  '2000指增': { en: 'CSI 2000 Enhanced', zh: '2000指增' },
  红利指增: { en: 'Dividend Index Enhanced', zh: '红利指增' },
  量化选股: { en: 'Quant Stock Selection', zh: '量化选股' },
  其他指增: { en: 'Other Index Enhanced', zh: '其他指增' },
  股票多空: { en: 'Equity Long/Short', zh: '股票多空' },
  股票市场中性: { en: 'Equity Market Neutral', zh: '股票市场中性' },
  债券策略: { en: 'Bond Strategy', zh: '债券策略' },
  纯债策略: { en: 'Pure Bond Strategy', zh: '纯债策略' },
  债券增强: { en: 'Bond Enhanced', zh: '债券增强' },
  债券复合: { en: 'Bond Composite', zh: '债券复合' },
  转债交易: { en: 'Convertible Bond Trading', zh: '转债交易' },
  期货及衍生品策略: { en: 'Futures & Derivatives', zh: '期货及衍生品策略' },
  主观CTA: { en: 'Discretionary CTA', zh: '主观CTA' },
  主观趋势: { en: 'Discretionary Trend', zh: '主观趋势' },
  主观套利: { en: 'Discretionary Arbitrage', zh: '主观套利' },
  主观多策略: { en: 'Discretionary Multi-Strategy', zh: '主观多策略' },
  量化CTA: { en: 'Quant CTA', zh: '量化CTA' },
  量化趋势: { en: 'Quant Trend', zh: '量化趋势' },
  量化套利: { en: 'Quant Arbitrage', zh: '量化套利' },
  量化多策略: { en: 'Quant Multi-Strategy', zh: '量化多策略' },
  期权策略: { en: 'Options Strategy', zh: '期权策略' },
  其他衍生品策略: { en: 'Other Derivatives', zh: '其他衍生品策略' },
  多资产策略: { en: 'Multi-Asset Strategy', zh: '多资产策略' },
  宏观策略: { en: 'Macro Strategy', zh: '宏观策略' },
  套利策略: { en: 'Arbitrage Strategy', zh: '套利策略' },
  复合策略: { en: 'Composite Strategy', zh: '复合策略' },
  组合基金: { en: 'Fund of Funds', zh: '组合基金' },
  MOM: { en: 'MOM', zh: 'MOM' },
  指数: { en: 'Index', zh: '指数' },
  其他: { en: 'Other', zh: '其他' },
}

const DOCUMENT_TYPE_OPTIONS = [
  'factsheet',
  'valuation_statement',
  'due_diligence_report',
  'investment_memo',
  'fund_contract',
  'prospectus',
  'periodic_report',
  'other',
]

function localize(language: string, text: LocalizedText) {
  return language === 'zh-Hans' ? text.zh : text.en
}

function localizeSystemValue(value: string | null | undefined, language: string) {
  const normalized = String(value || '').trim()
  if (!normalized) {
    return '—'
  }
  const label = SYSTEM_VALUE_LABELS[normalized] || SYSTEM_VALUE_LABELS[normalized.toLowerCase()]
  return label ? localize(language, label) : normalized
}

function localizeTaxonomyPath(value: string, language: string) {
  const trimmed = value.trim()
  if (!trimmed) {
    return ''
  }
  if (SYSTEM_VALUE_LABELS[trimmed]) {
    return localize(language, SYSTEM_VALUE_LABELS[trimmed])
  }
  return trimmed
    .split(' / ')
    .map((part) => localizeSystemValue(part, language))
    .join(' / ')
}

const MONTH_SHORT_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const ROLLING_WINDOW_MONTHS = 12
const MIN_ROLLING_RETURN_OBSERVATIONS = 3
const ROLLING_CHART_MAX_POINTS = 520

const PRIMARY_CHART_GEOMETRY: ChartGeometry = {
  width: 900,
  height: 340,
  paddingLeft: 58,
  paddingRight: 18,
  paddingTop: 24,
  paddingBottom: 40,
}

const DRAWDOWN_CHART_GEOMETRY: ChartGeometry = {
  width: 900,
  height: 120,
  paddingLeft: 58,
  paddingRight: 18,
  paddingTop: 18,
  paddingBottom: 30,
}

const CHART_HOVER_INSET = 2
const CHART_CROSSHAIR_INSET = 1

const RISK_SCATTER_GEOMETRY: ChartGeometry = {
  width: 560,
  height: 300,
  paddingLeft: 52,
  paddingRight: 20,
  paddingTop: 18,
  paddingBottom: 34,
}

const SECONDARY_SERIES_GEOMETRY: ChartGeometry = {
  width: 900,
  height: 220,
  paddingLeft: 58,
  paddingRight: 18,
  paddingTop: 18,
  paddingBottom: 40,
}

const ROLLING_RISK_CHART_WIDTH = 960
const ROLLING_RISK_CHART_HEIGHT = 260
const ROLLING_RISK_CHART_PADDING = { top: 18, right: 70, bottom: 30, left: 12 }

type RollingRiskMetricChartProps = {
  title: string
  points: FundChartPoint[]
  benchmarkPoints?: FundChartPoint[]
  benchmarkLabel?: string | null
  displayStyle: ChartDisplayStyle
  formatValue: (value: number | null | undefined) => string
  emptyLabel: string
}

function buildRollingRiskLinePath(points: Array<{ x: number; y: number }>) {
  return points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(' ')
}

function normalizeRollingRiskPoints(points: FundChartPoint[]) {
  return points
    .filter((point) => point.date && Number.isFinite(point.value))
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
}

function filterRollingRiskPointsByDateWindow(points: FundChartPoint[], startDate: string, endDate: string) {
  return points.filter((point) => point.date >= startDate && point.date <= endDate)
}

function buildRollingRiskCoordinates(
  points: FundChartPoint[],
  yMin: number,
  yMax: number,
  startDate: string,
  endDate: string,
) {
  const drawableWidth =
    ROLLING_RISK_CHART_WIDTH - ROLLING_RISK_CHART_PADDING.left - ROLLING_RISK_CHART_PADDING.right
  const drawableHeight =
    ROLLING_RISK_CHART_HEIGHT - ROLLING_RISK_CHART_PADDING.top - ROLLING_RISK_CHART_PADDING.bottom
  const range = yMax - yMin || 1
  const startTime = Date.parse(`${startDate}T00:00:00`)
  const endTime = Date.parse(`${endDate}T00:00:00`)
  const timeRange =
    Number.isFinite(startTime) && Number.isFinite(endTime) && endTime > startTime
      ? endTime - startTime
      : null

  return points.map((point, index) => {
    const pointTime = Date.parse(`${point.date}T00:00:00`)
    const xRatio =
      timeRange != null && Number.isFinite(pointTime)
        ? (pointTime - startTime) / timeRange
        : index / Math.max(points.length - 1, 1)
    const x = ROLLING_RISK_CHART_PADDING.left + Math.min(1, Math.max(0, xRatio)) * drawableWidth
    const y =
      ROLLING_RISK_CHART_PADDING.top +
      drawableHeight -
      ((point.value - yMin) / range) * drawableHeight
    return { x, y }
  })
}

function getRollingRiskYCoordinate(value: number, yMin: number, yMax: number) {
  const drawableHeight =
    ROLLING_RISK_CHART_HEIGHT - ROLLING_RISK_CHART_PADDING.top - ROLLING_RISK_CHART_PADDING.bottom
  return (
    ROLLING_RISK_CHART_PADDING.top +
    drawableHeight -
    ((value - yMin) / (yMax - yMin || 1)) * drawableHeight
  )
}

function getRollingRiskDateLabel(point: FundChartPoint | undefined) {
  return point?.date ?? '—'
}

function WatchlistRollingRiskMetricChart({
  title,
  points,
  benchmarkPoints = [],
  benchmarkLabel = null,
  displayStyle,
  formatValue,
  emptyLabel,
}: RollingRiskMetricChartProps) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const sortedPoints = useMemo(() => normalizeRollingRiskPoints(points), [points])
  const sortedBenchmarkPoints = useMemo(
    () => normalizeRollingRiskPoints(benchmarkPoints),
    [benchmarkPoints],
  )

  const chartState = useMemo(() => {
    if (sortedPoints.length < 2) {
      return null
    }

    const firstDate = sortedPoints[0].date
    const lastDate = sortedPoints[sortedPoints.length - 1].date
    const visibleBenchmarkPoints = filterRollingRiskPointsByDateWindow(
      sortedBenchmarkPoints,
      firstDate,
      lastDate,
    )
    const allValues = [
      ...sortedPoints.map((point) => point.value),
      ...visibleBenchmarkPoints.map((point) => point.value),
      0,
    ].filter((value) => Number.isFinite(value))
    const minValue = Math.min(...allValues)
    const maxValue = Math.max(...allValues)
    const padding = Math.max((maxValue - minValue) * 0.12, Math.abs(maxValue || minValue || 1) * 0.04, 0.01)
    const yMin = Math.min(0, minValue - padding)
    const yMax = maxValue + padding
    const coordinates = buildRollingRiskCoordinates(sortedPoints, yMin, yMax, firstDate, lastDate)
    const linePath = buildRollingRiskLinePath(coordinates)
    const baselineY = getRollingRiskYCoordinate(0, yMin, yMax)
    const areaPath = `${linePath} L ${coordinates[coordinates.length - 1].x.toFixed(2)} ${baselineY.toFixed(2)} L ${coordinates[0].x.toFixed(2)} ${baselineY.toFixed(2)} Z`
    const benchmarkCoordinates =
      visibleBenchmarkPoints.length > 1
        ? buildRollingRiskCoordinates(visibleBenchmarkPoints, yMin, yMax, firstDate, lastDate)
        : []
    const benchmarkLinePath =
      benchmarkCoordinates.length > 1 ? buildRollingRiskLinePath(benchmarkCoordinates) : null
    const guideValues = [yMax, yMin + (yMax - yMin) / 2, yMin]

    return {
      visibleBenchmarkPoints,
      coordinates,
      linePath,
      areaPath,
      benchmarkLinePath,
      guideValues,
      yMin,
      yMax,
    }
  }, [sortedBenchmarkPoints, sortedPoints])

  if (!chartState) {
    return (
      <section className="instrument-rolling-risk-chart" aria-label={title}>
        <div className="instrument-rolling-risk-chart-head">
          <div className="instrument-series-legend">
            <div className="instrument-series-label">
              <strong>{title}</strong>
            </div>
          </div>
        </div>
        <div className="instrument-risk-chart-empty">{emptyLabel}</div>
      </section>
    )
  }

  const resolvedChartState = chartState
  const activeIndex = Math.min(hoveredIndex ?? sortedPoints.length - 1, sortedPoints.length - 1)
  const activePoint = sortedPoints[activeIndex]
  const activeCoordinate = resolvedChartState.coordinates[activeIndex]
  const activeBenchmarkPoint =
    resolvedChartState.visibleBenchmarkPoints.find((point) => point.date === activePoint.date) ??
    resolvedChartState.visibleBenchmarkPoints
      .slice()
      .reverse()
      .find((point) => point.date <= activePoint.date) ??
    null
  const firstPoint = sortedPoints[0]
  const middlePoint = sortedPoints[Math.floor((sortedPoints.length - 1) / 2)]
  const lastPoint = sortedPoints[sortedPoints.length - 1]
  const lastCoordinate = resolvedChartState.coordinates[resolvedChartState.coordinates.length - 1]
  const hasBenchmark = Boolean(benchmarkLabel && resolvedChartState.benchmarkLinePath)

  function handlePointerMove(event: ReactMouseEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    const chartX = bounds.width > 0 ? ((event.clientX - bounds.left) / bounds.width) * ROLLING_RISK_CHART_WIDTH : 0
    const nextIndex = resolvedChartState.coordinates.reduce((bestIndex, coordinate, index) => {
      const bestDistance = Math.abs(resolvedChartState.coordinates[bestIndex].x - chartX)
      const nextDistance = Math.abs(coordinate.x - chartX)
      return nextDistance < bestDistance ? index : bestIndex
    }, 0)
    setHoveredIndex(nextIndex)
  }

  return (
    <section className="instrument-rolling-risk-chart" aria-label={title}>
      <div className="instrument-rolling-risk-chart-head">
        <div className="instrument-series-legend">
          <div className="instrument-series-label">
            <strong>{title}</strong>
            <em>{formatValue(activePoint.value)}</em>
          </div>
          {hasBenchmark ? (
            <div className="instrument-series-label instrument-series-label-benchmark-row">
              <strong>{benchmarkLabel}</strong>
              <em>{formatValue(activeBenchmarkPoint?.value)}</em>
            </div>
          ) : null}
        </div>
      </div>

      <div className="instrument-rolling-risk-chart-plot">
        {hoveredIndex != null ? (
          <div
            className={`instrument-rolling-risk-tooltip${
              activeCoordinate.x > ROLLING_RISK_CHART_WIDTH * 0.72 ? ' instrument-rolling-risk-tooltip-left' : ''
            }`}
            style={{ left: `${(activeCoordinate.x / ROLLING_RISK_CHART_WIDTH) * 100}%` }}
          >
            <span>{activePoint.date}</span>
            <strong>{formatValue(activePoint.value)}</strong>
            {hasBenchmark ? (
              <span>
                {benchmarkLabel} {formatValue(activeBenchmarkPoint?.value)}
              </span>
            ) : null}
          </div>
        ) : null}
        <svg
          className="instrument-rolling-risk-chart-svg"
          viewBox={`0 0 ${ROLLING_RISK_CHART_WIDTH} ${ROLLING_RISK_CHART_HEIGHT}`}
          preserveAspectRatio="none"
          onMouseMove={handlePointerMove}
          onMouseLeave={() => setHoveredIndex(null)}
        >
          {resolvedChartState.guideValues.map((value, index) => {
            const y =
              ROLLING_RISK_CHART_PADDING.top +
              (ROLLING_RISK_CHART_HEIGHT - ROLLING_RISK_CHART_PADDING.top - ROLLING_RISK_CHART_PADDING.bottom) -
              ((value - resolvedChartState.yMin) / (resolvedChartState.yMax - resolvedChartState.yMin || 1)) *
                (ROLLING_RISK_CHART_HEIGHT - ROLLING_RISK_CHART_PADDING.top - ROLLING_RISK_CHART_PADDING.bottom)
            return (
              <g key={`${value.toFixed(6)}:${index}`}>
                <line
                  x1={ROLLING_RISK_CHART_PADDING.left}
                  x2={ROLLING_RISK_CHART_WIDTH - ROLLING_RISK_CHART_PADDING.right}
                  y1={y}
                  y2={y}
                  className="instrument-rolling-risk-guide"
                />
                <text
                  x={ROLLING_RISK_CHART_WIDTH - ROLLING_RISK_CHART_PADDING.right + 8}
                  y={y + 4}
                  className="instrument-rolling-risk-axis-label"
                >
                  {formatValue(value)}
                </text>
              </g>
            )
          })}
          {displayStyle === 'mountain' ? (
            <path d={resolvedChartState.areaPath} className="instrument-rolling-risk-area" />
          ) : null}
          {displayStyle !== 'dot' ? (
            <path d={resolvedChartState.linePath} className="instrument-rolling-risk-line" />
          ) : null}
          {displayStyle === 'dot'
            ? resolvedChartState.coordinates.map((coordinate, index) => (
                <circle
                  key={`${sortedPoints[index].date}:dot`}
                  cx={coordinate.x}
                  cy={coordinate.y}
                  r="2.6"
                  className="instrument-rolling-risk-point"
                />
              ))
            : null}
          {resolvedChartState.benchmarkLinePath ? (
            <path
              d={resolvedChartState.benchmarkLinePath}
              className="instrument-rolling-risk-line instrument-rolling-risk-line-benchmark"
            />
          ) : null}
          <circle
            cx={lastCoordinate.x}
            cy={lastCoordinate.y}
            r="4"
            className="instrument-rolling-risk-endpoint"
          />
          {hoveredIndex != null ? (
            <line
              x1={activeCoordinate.x}
              x2={activeCoordinate.x}
              y1={ROLLING_RISK_CHART_PADDING.top}
              y2={ROLLING_RISK_CHART_HEIGHT - ROLLING_RISK_CHART_PADDING.bottom}
              className="instrument-rolling-risk-guide-line"
            />
          ) : null}
          {[
            { point: firstPoint, anchor: 'start' as const, x: ROLLING_RISK_CHART_PADDING.left },
            { point: middlePoint, anchor: 'middle' as const, x: ROLLING_RISK_CHART_WIDTH / 2 },
            {
              point: lastPoint,
              anchor: 'end' as const,
              x: ROLLING_RISK_CHART_WIDTH - ROLLING_RISK_CHART_PADDING.right,
            },
          ].map((label) => (
            <text
              key={`${getRollingRiskDateLabel(label.point)}:${label.anchor}`}
              x={label.x}
              y={ROLLING_RISK_CHART_HEIGHT - 9}
              textAnchor={label.anchor}
              className="instrument-rolling-risk-axis-label instrument-rolling-risk-x-label"
            >
              {getRollingRiskDateLabel(label.point)}
            </text>
          ))}
        </svg>
      </div>
    </section>
  )
}

function toTitleCase(value: string) {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function makeRowId(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`
}

function benchmarkLibraryLabel(item: FundLibraryItem) {
  return item.ticker_or_isin ? `${item.ticker_or_isin} · ${item.fund_name}` : item.fund_name
}

function benchmarkTypeRank(type: string) {
  return type === 'index' ? 0 : type === 'fund' ? 1 : type === 'etf' ? 2 : type === 'equity' ? 3 : 4
}

function filterBenchmarkOptions(options: FundLibraryItem[], search: string) {
  const normalizedSearch = search.trim().toLowerCase()
  if (!normalizedSearch) {
    return options
      .slice()
      .sort((left, right) => {
        const leftIdentifier = left.ticker_or_isin || left.fund_id
        const rightIdentifier = right.ticker_or_isin || right.fund_id
        return (
          benchmarkTypeRank(left.product_type) - benchmarkTypeRank(right.product_type) ||
          leftIdentifier.localeCompare(rightIdentifier) ||
          left.fund_name.localeCompare(right.fund_name)
        )
      })
      .slice(0, 12)
  }

  return options
    .filter((item) =>
      [item.fund_name, item.ticker_or_isin, item.product_type, item.fund_id, benchmarkLibraryLabel(item)]
        .join(' ')
        .toLowerCase()
        .includes(normalizedSearch),
    )
    .slice(0, 10)
}

function parseTimelineNoteImportance(value: unknown): TimelineNoteImportance {
  return value === 'high' || value === 'medium' || value === 'low' ? value : 'medium'
}

function normalizeResearchTimelineNotes(notes: Array<Record<string, unknown>> | undefined) {
  return (notes || [])
    .map((row) => {
      const noteDate = typeof row.note_date === 'string' ? row.note_date.slice(0, 10) : ''
      if (!noteDate) {
        return null
      }
      return {
        note_id:
          typeof row.note_id === 'string' && row.note_id.trim()
            ? row.note_id
            : makeRowId('timeline-note'),
        note_date: noteDate,
        title: typeof row.title === 'string' ? row.title : '',
        summary: typeof row.summary === 'string' ? row.summary : '',
        body: typeof row.body === 'string' ? row.body : '',
        importance: parseTimelineNoteImportance(row.importance),
        tags: Array.isArray(row.tags)
          ? row.tags
              .map((value) => (typeof value === 'string' ? value.trim() : ''))
              .filter(Boolean)
          : [],
      } satisfies ResearchTimelineNote
    })
    .filter((row): row is ResearchTimelineNote => row !== null)
    .sort(sortResearchTimelineNotes)
}

function sortResearchTimelineNotes(left: ResearchTimelineNote, right: ResearchTimelineNote) {
  const dateCompare = right.note_date.localeCompare(left.note_date)
  if (dateCompare !== 0) {
    return dateCompare
  }
  return left.title.localeCompare(right.title)
}

function createTimelineNoteDraft(
  noteDate: string,
  note?: ResearchTimelineNote | null,
): TimelineNoteDraft {
  return {
    note_id: note?.note_id || makeRowId('timeline-note'),
    note_date: note?.note_date || noteDate,
    title: note?.title || '',
    summary: note?.summary || '',
    body: note?.body || '',
    importance: note?.importance || 'medium',
    tagsText: note?.tags.join(', ') || '',
  }
}

function serializeTimelineNoteDraft(draft: TimelineNoteDraft): ResearchTimelineNote {
  return {
    note_id: draft.note_id,
    note_date: draft.note_date,
    title: draft.title.trim(),
    summary: draft.summary.trim(),
    body: draft.body.trim(),
    importance: draft.importance,
    tags: draft.tagsText
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean),
  }
}

function formatTimelineNoteImportance(importance: TimelineNoteImportance) {
  if (importance === 'high') {
    return 'High'
  }
  if (importance === 'low') {
    return 'Low'
  }
  return 'Medium'
}

function formatStarRating(rating: number | null | undefined) {
  if (rating == null || !Number.isFinite(rating)) {
    return '—'
  }
  const normalizedRating = Math.max(0, Math.min(5, Math.round(rating)))
  return `${'★'.repeat(normalizedRating)}${'☆'.repeat(5 - normalizedRating)}`
}

function parseManualRating(value: unknown) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return null
  }
  return Math.max(1, Math.min(5, Math.round(value)))
}

function getNumber(value: unknown) {
  return typeof value === 'number' && !Number.isNaN(value) ? value : null
}

function getString(value: unknown) {
  return typeof value === 'string' && value ? value : '—'
}

function formatPeerMetricValue(metric: PeerComparisonMetric, value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) {
    return '—'
  }
  return metric.format === 'percent' ? formatPercent(value) : formatNumber(value, 2)
}

function formatPeerRank(metric: PeerComparisonMetric) {
  if (metric.rank == null || metric.sample_count == null) {
    return '—'
  }
  return `${formatNumber(metric.rank, 0)} / ${formatNumber(metric.sample_count, 0)}`
}

function formatPeerMetricDelta(metric: PeerComparisonMetric) {
  if (
    metric.value == null ||
    metric.peer_median == null ||
    !Number.isFinite(metric.value) ||
    !Number.isFinite(metric.peer_median)
  ) {
    return '—'
  }
  const delta = metric.value - metric.peer_median
  const prefix = delta > 0 ? '+' : ''
  return metric.format === 'percent'
    ? `${prefix}${formatPercent(delta)}`
    : `${prefix}${formatNumber(delta, 2)}`
}

function getSignedMetricTone(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) {
    return 'empty'
  }
  if (value > 0) {
    return 'positive'
  }
  if (value < 0) {
    return 'negative'
  }
  return 'neutral'
}

function getPeerMetricTone(metric: PeerComparisonMetric, mode: PerformanceMatrixMode) {
  if (mode === 'peer_percentile' || mode === 'peer_rank') {
    if (metric.percentile == null || !Number.isFinite(metric.percentile)) {
      return 'neutral'
    }
    if (metric.percentile >= 75) {
      return 'positive'
    }
    if (metric.percentile < 25) {
      return 'negative'
    }
    return 'neutral'
  }
  if (
    mode === 'peer_median_delta' &&
    metric.value != null &&
    metric.peer_median != null &&
    Number.isFinite(metric.value) &&
    Number.isFinite(metric.peer_median)
  ) {
    const delta = metric.value - metric.peer_median
    const isGood = metric.direction === 'lower' ? delta < 0 : delta > 0
    const isBad = metric.direction === 'lower' ? delta > 0 : delta < 0
    return isGood ? 'positive' : isBad ? 'negative' : 'neutral'
  }
  return 'neutral'
}

function getPeerMetricKeyForMatrixCell(
  rowKey: PerformanceMatrixRowKey,
  periodKey: PerformanceMetricPeriodKey,
) {
  if (rowKey === 'period_return') {
    return (
      {
        '1W': 'return_1w',
        MTD: 'return_mtd',
        YTD: 'return_ytd',
        '1Y': 'return_1y',
      } as Partial<Record<PerformanceMetricPeriodKey, string>>
    )[periodKey] || null
  }
  if (rowKey === 'annualized_return') {
    return (
      {
        '1Y': 'return_1y',
        '3Y': 'return_3y_annualized',
        '5Y': 'return_5y_annualized',
        SI: 'annualized_return',
      } as Partial<Record<PerformanceMetricPeriodKey, string>>
    )[periodKey] || null
  }
  if (rowKey === 'annualized_volatility') {
    return periodKey === 'SI' ? 'volatility' : null
  }
  if (rowKey === 'sharpe_ratio') {
    return periodKey === 'SI' ? 'sharpe_ratio' : null
  }
  if (rowKey === 'sortino_ratio') {
    return periodKey === 'SI' ? 'sortino_ratio' : null
  }
  if (rowKey === 'calmar_ratio') {
    return periodKey === 'SI' ? 'calmar' : null
  }
  if (rowKey === 'max_drawdown') {
    return periodKey === 'SI' ? 'max_drawdown' : null
  }
  return null
}

function formatRiskComparisonValue(value: unknown) {
  return value && typeof value === 'string' ? toTitleCase(value.replace(/_/g, ' ')) : '—'
}

function getRows(value: unknown) {
  return Array.isArray(value) ? value : []
}

function getDisplayValue(value: unknown) {
  if (typeof value === 'boolean') {
    return formatBoolean(value)
  }
  if (typeof value === 'number') {
    return formatNumber(value)
  }
  return getString(value)
}

function formatPriceOverviewValue(key: string, value: unknown) {
  if (value == null || value === '') {
    return '—'
  }
  if (typeof value === 'number') {
    if (
      key.includes('ratio') ||
      key.includes('fee')
    ) {
      return `${formatNumber(value, 2)} %`
    }
    if (key.includes('investment')) {
      return formatCompactCurrency(value)
    }
    return formatNumber(value)
  }
  return String(value)
}

function formatPeopleOverviewValue(key: string, value: unknown) {
  if (value == null || value === '') {
    return '—'
  }
  if (key === 'inception_date') {
    return formatDate(value)
  }
  if (typeof value === 'number') {
    if (key.includes('tenure')) {
      return `${formatNumber(value, 1)} Years`
    }
    if (key === 'number_of_managers') {
      return formatNumber(value, 0)
    }
    return formatNumber(value)
  }
  if (typeof value === 'string' && key.includes('tenure')) {
    return value.toLowerCase().includes('year') ? value : `${value} Years`
  }
  return String(value)
}

function formatResearchOverviewValue(key: string, value: unknown) {
  if (value == null || value === '') {
    return '—'
  }
  if (key === 'next_review_date') {
    return formatDate(value)
  }
  return String(value)
}

function formatMonitoringStatus(value: unknown) {
  if (typeof value !== 'string' || !value) {
    return 'Unknown'
  }
  return toTitleCase(value)
}

function getMonitoringStatusTone(value: unknown) {
  if (typeof value !== 'string' || !value) {
    return 'status-pending'
  }
  const normalized = value.toLowerCase()
  if (
    normalized === 'fresh' ||
    normalized === 'current' ||
    normalized === 'healthy' ||
    normalized === 'ready' ||
    normalized === 'imported'
  ) {
    return 'status-fresh'
  }
  if (normalized === 'failed' || normalized === 'blocked') {
    return 'status-error'
  }
  return 'status-pending'
}

function getDocumentStatusTone(value: unknown) {
  if (typeof value !== 'string' || !value) {
    return 'status-pending'
  }
  const normalized = value.toLowerCase()
  if (
    normalized.includes('adopt') ||
    normalized.includes('complete') ||
    normalized.includes('current') ||
    normalized.includes('upload') ||
    normalized.includes('import')
  ) {
    return 'status-fresh'
  }
  if (normalized.includes('review') || normalized.includes('pending') || normalized.includes('draft')) {
    return 'status-pending'
  }
  return 'status-attribute'
}

function formatFileSize(value: unknown) {
  const numeric = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return '—'
  }
  const units = ['B', 'KB', 'MB', 'GB']
  let size = numeric
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  const digits = unitIndex === 0 || size >= 10 ? 0 : 1
  return `${size.toFixed(digits)} ${units[unitIndex]}`
}

function formatNavBasisSource(value: string | null | undefined) {
  if (!value) {
    return '—'
  }
  return NAV_BASIS_SOURCE_LABELS[value] || toTitleCase(value)
}

function cleanListRows(rows: EditableListRow[]) {
  return rows.map((row) => row.value.trim()).filter(Boolean)
}

function listRowsToTextareaValue(rows: EditableListRow[]) {
  return rows.map((row) => row.value).join('\n')
}

function textareaValueToListRows(prefix: string, value: string) {
  return value.split(/\r?\n/).map((line) => ({
    id: makeRowId(prefix),
    value: line,
  }))
}

function parseOptionalNumber(value: string) {
  const trimmed = value.trim()
  if (!trimmed) {
    return null
  }
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function toEditablePeopleDraft(people: FundPeopleResponse): PeopleDraft {
  return {
    overviewRows: Object.entries(people.overview || {}).map(([key, value]) => ({
      id: makeRowId('overview'),
      key,
      value: value == null ? '' : String(value),
    })),
    teamRows: (people.team || []).map((row) => ({
      id: makeRowId('team'),
      name: typeof row.name === 'string' ? row.name : '',
      role: typeof row.role === 'string' ? row.role : '',
      start_date: typeof row.start_date === 'string' ? row.start_date.slice(0, 10) : '',
    })),
    noteRows: (people.notes || []).map((value) => ({
      id: makeRowId('people-note'),
      value,
    })),
  }
}

function toEditableStrategyDraft(strategy: FundStrategyResponse): StrategyDraft {
  return {
    summary: strategy.summary || '',
    investment_objective: strategy.investment_objective || '',
    processRows: (strategy.process_bullets || []).map((value) => ({
      id: makeRowId('process'),
      value,
    })),
    riskControlRows: (strategy.risk_controls || []).map((value) => ({
      id: makeRowId('risk-control'),
      value,
    })),
    noteRows: (strategy.notes || []).map((value) => ({
      id: makeRowId('strategy-note'),
      value,
    })),
  }
}

function toEditablePriceDraft(price: FundPriceResponse): PriceDraft {
  return {
    overviewRows: Object.entries(price.overview || {}).map(([key, value]) => ({
      id: makeRowId('price-overview'),
      key,
      value: value == null ? '' : String(value),
    })),
    distribution_policy: price.distribution_policy || '',
    policy_text: price.policy_text || '',
    feeNoteRows: (price.fee_notes || []).map((value) => ({
      id: makeRowId('price-fee-note'),
      value,
    })),
    noteRows: (price.notes || []).map((value) => ({
      id: makeRowId('price-note'),
      value,
    })),
  }
}

function toEditableDocumentsDraft(documents: FundDocumentsResponse): DocumentsDraft {
  return {
    currentDocumentRows: (documents.current_documents || []).map((row) => ({
      id: makeRowId('document'),
      title: typeof row.title === 'string' ? row.title : '',
      document_type: typeof row.document_type === 'string' ? row.document_type : '',
      as_of_date: typeof row.as_of_date === 'string' ? row.as_of_date.slice(0, 10) : '',
      source: typeof row.source === 'string' ? row.source : '',
      status: typeof row.status === 'string' ? row.status : '',
      version_label: typeof row.version_label === 'string' ? row.version_label : '',
      file_name: typeof row.file_name === 'string' ? row.file_name : '',
      download_url: typeof row.download_url === 'string' ? row.download_url : '',
      file_size: row.file_size == null ? '' : String(row.file_size),
      content_type: typeof row.content_type === 'string' ? row.content_type : '',
      uploaded_at: typeof row.uploaded_at === 'string' ? row.uploaded_at.slice(0, 16) : '',
      notes: typeof row.notes === 'string' ? row.notes : '',
      stored_file_name: typeof row.stored_file_name === 'string' ? row.stored_file_name : '',
    })),
    importRows: (documents.recent_imports || []).map((row) => ({
      id: makeRowId('document-import'),
      import_type: typeof row.import_type === 'string' ? row.import_type : '',
      received_at: typeof row.received_at === 'string' ? row.received_at.slice(0, 16) : '',
      source: typeof row.source === 'string' ? row.source : '',
      status: typeof row.status === 'string' ? row.status : '',
      file_name: typeof row.file_name === 'string' ? row.file_name : '',
    })),
    extractionRows: (documents.extraction_reviews || []).map((row) => ({
      id: makeRowId('document-extraction'),
      document_title: typeof row.document_title === 'string' ? row.document_title : '',
      extract_type: typeof row.extract_type === 'string' ? row.extract_type : '',
      status: typeof row.status === 'string' ? row.status : '',
      adopted_version: typeof row.adopted_version === 'string' ? row.adopted_version : '',
      updated_at: typeof row.updated_at === 'string' ? row.updated_at.slice(0, 16) : '',
    })),
    noteRows: (documents.notes || []).map((value) => ({
      id: makeRowId('document-note'),
      value,
    })),
  }
}

function toEditableResearchDraft(research: FundResearchResponse): ResearchDraft {
  const overview = research.overview || {}
  return {
    overviewRows: RESEARCH_OVERVIEW_FIELDS.map((field) => {
      const value = overview[field.key]
      return {
        id: makeRowId('research-overview'),
        key: field.key,
        value: value == null ? '' : String(value),
      }
    }),
  }
}

function getOverviewDraftValue(draft: PriceDraft | null, key: string) {
  return draft?.overviewRows.find((row) => row.key === key)?.value ?? ''
}

function getPeopleOverviewDraftValue(draft: PeopleDraft | null, key: string) {
  return draft?.overviewRows.find((row) => row.key === key)?.value ?? ''
}

function getResearchOverviewDraftValue(draft: ResearchDraft | null, key: string) {
  return draft?.overviewRows.find((row) => row.key === key)?.value ?? ''
}

function upsertKeyValueRows(rows: EditableKeyValueRow[], key: string, value: string) {
  const existing = rows.find((row) => row.key === key)
  if (existing) {
    return rows.map((row) => (row.key === key ? { ...row, value } : row))
  }
  return [...rows, { id: makeRowId('overview'), key, value }]
}

function normalizeTabs(sourceTabs: string[], detailKind: DetailKind = 'fund'): DetailTab[] {
  if (detailKind === 'index') {
    return INDEX_TABS
  }

  const set = new Set<DetailTab>(CORE_TABS)

  sourceTabs.forEach((tab) => {
    const normalizedTab = tab === 'quote' || tab === 'summary' ? 'overview' : tab === 'portfolio' ? 'exposure' : tab
    if (TAB_ORDER.includes(normalizedTab as DetailTab)) {
      set.add(normalizedTab as DetailTab)
    }
  })

  return TAB_ORDER.filter((tab) => set.has(tab))
}

function defaultFundPortfolioResponse(): FundPortfolioResponse {
  return {
    allocation_blocks: {},
    style_box: null,
    liquidity_leverage: null,
    valuation_statistics: null,
    holdings_summary: null,
    snapshot_metadata: null,
  }
}

function defaultFundPortfolioHoldingsResponse(): FundPortfolioHoldingsResponse {
  return { rows: [], page: 1, page_size: 0, total_rows: 0 }
}

function defaultFundRatingsResponse(summary: FundSummaryResponse): FundRatingsResponse {
  return {
    overall_rating: summary.overall_rating ?? null,
    overall_score: null,
    analyst_stance: summary.analyst_stance || 'Unrated',
    methodology_version: 'instrument-rating/v1',
    dimension_scores: [],
    override_info: null,
  }
}

function defaultFundPeopleResponse(): FundPeopleResponse {
  return { overview: {}, team: [], notes: [] }
}

function defaultFundStrategyResponse(): FundStrategyResponse {
  return { summary: '', investment_objective: '', process_bullets: [], risk_controls: [], notes: [] }
}

function defaultFundPriceResponse(): FundPriceResponse {
  return { overview: {}, distribution_policy: '', policy_text: '', fee_notes: [], notes: [] }
}

function defaultFundDocumentsResponse(): FundDocumentsResponse {
  return { current_documents: [], recent_imports: [], extraction_reviews: [], notes: [] }
}

function defaultFundResearchResponse(): FundResearchResponse {
  return { overview: {}, manual_rating: null, timeline_notes: [] }
}

function defaultCalculationFrequencyProfile(): CalculationFrequencyProfile {
  return {
    requested_frequency: 'auto',
    resolved_frequency: 'daily',
    inferred_frequency: 'daily',
    source_frequency_counts: { daily: 0, weekly: 0, monthly: 0, unknown: 0 },
    raw_observation_count: 0,
    observation_count: 0,
    start_date: null,
    end_date: null,
    annualization_periods_per_year: null,
    largest_gap_days: null,
    gap_count: 0,
    gap_status: 'aligned',
    status_label: 'Unavailable',
  }
}

function defaultFundPerformanceResponse(): FundPerformanceResponse {
  return {
    growth_chart_series: [],
    annual_returns: [],
    trailing_returns: [],
    ranking: null,
    peer_comparison: null,
    calculation_frequency_profile: null,
    snapshot_metadata: null,
  }
}

function defaultFundRiskResponse(): FundRiskResponse {
  return {
    risk_overview: null,
    scatter_points: [],
    risk_metrics: [],
    drawdown_summary: null,
    calculation_frequency_profile: null,
    snapshot_metadata: null,
  }
}

function defaultFundNavSeriesResponse(fundId: string): FundNavSeriesResponse {
  return {
    fund_id: fundId,
    count: 0,
    nav_basis_preference: 'auto',
    nav_basis_type: null,
    nav_basis_source: 'unavailable',
    nav_basis_status: 'unavailable',
    calculation_frequency_profile: defaultCalculationFrequencyProfile(),
    series: [],
    calculation_series: [],
    rows: [],
  }
}

function buildChartLinePath(
  points: FundChartPoint[],
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
  min?: number,
  max?: number,
) {
  if (points.length < 2) {
    return ''
  }

  const { width, height, paddingLeft, paddingRight, paddingTop, paddingBottom } = geometry
  const values = points.map((point) => point.value)
  const resolvedMin = min ?? Math.min(...values)
  const resolvedMax = max ?? Math.max(...values)
  const range = resolvedMax - resolvedMin || 1

  return points
    .map((point, index) => {
      const x =
        paddingLeft +
        (index / Math.max(points.length - 1, 1)) * (width - paddingLeft - paddingRight)
      const y =
        height -
        paddingBottom -
        ((point.value - resolvedMin) / range) * (height - paddingTop - paddingBottom)
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(' ')
}

function getProjectedSeriesPoints(
  points: FundChartPoint[],
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
  min?: number,
  max?: number,
) {
  const { width, height, paddingLeft, paddingRight, paddingTop, paddingBottom } = geometry
  const values = points.map((point) => point.value)
  const resolvedMin = min ?? Math.min(...values)
  const resolvedMax = max ?? Math.max(...values)
  const range = resolvedMax - resolvedMin || 1

  return points.map((point, index) => {
    const x =
      paddingLeft +
      (index / Math.max(points.length - 1, 1)) * (width - paddingLeft - paddingRight)
    const y =
      height -
      paddingBottom -
      ((point.value - resolvedMin) / range) * (height - paddingTop - paddingBottom)
    return { x, y }
  })
}

function buildSmoothChartLinePath(
  points: FundChartPoint[],
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
  min?: number,
  max?: number,
) {
  if (points.length < 2) {
    return ''
  }

  const projectedPoints = getProjectedSeriesPoints(points, geometry, min, max)
  if (projectedPoints.length === 2) {
    return projectedPoints
      .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
      .join(' ')
  }

  const commands = [`M ${projectedPoints[0].x.toFixed(2)} ${projectedPoints[0].y.toFixed(2)}`]
  for (let index = 0; index < projectedPoints.length - 1; index += 1) {
    const previous = projectedPoints[Math.max(index - 1, 0)]
    const current = projectedPoints[index]
    const next = projectedPoints[index + 1]
    const nextNext = projectedPoints[Math.min(index + 2, projectedPoints.length - 1)]
    const control1X = current.x + (next.x - previous.x) / 6
    const control1Y = current.y + (next.y - previous.y) / 6
    const control2X = next.x - (nextNext.x - current.x) / 6
    const control2Y = next.y - (nextNext.y - current.y) / 6
    commands.push(
      [
        'C',
        control1X.toFixed(2),
        control1Y.toFixed(2),
        control2X.toFixed(2),
        control2Y.toFixed(2),
        next.x.toFixed(2),
        next.y.toFixed(2),
      ].join(' '),
    )
  }
  return commands.join(' ')
}

function buildDateScaledLinePath(
  points: FundChartPoint[],
  geometry: ChartGeometry,
  min: number,
  max: number,
  startDate: string | null | undefined,
  endDate: string | null | undefined,
  smooth = false,
) {
  const sortedPoints = sortSeriesByDate(points)
    .map((point) => {
      const parsed = parseChartDateParts(point.date)
      return parsed ? { point, time: parsed.time } : null
    })
    .filter((item): item is { point: FundChartPoint; time: number } => item !== null)

  if (sortedPoints.length < 2) {
    return ''
  }

  const parsedStart = startDate ? parseChartDateParts(startDate) : null
  const parsedEnd = endDate ? parseChartDateParts(endDate) : null
  const firstTime = parsedStart?.time ?? sortedPoints[0].time
  const lastTime = parsedEnd?.time ?? sortedPoints[sortedPoints.length - 1].time
  const timeRange = lastTime - firstTime

  if (timeRange <= 0) {
    return smooth
      ? buildSmoothChartLinePath(points, geometry, min, max)
      : buildChartLinePath(points, geometry, min, max)
  }

  const plotWidth = geometry.width - geometry.paddingLeft - geometry.paddingRight
  const projectedPoints = sortedPoints
    .filter(({ time }) => time >= firstTime && time <= lastTime)
    .map(({ point, time }) => ({
      x: geometry.paddingLeft + ((time - firstTime) / timeRange) * plotWidth,
      y: projectChartValue(point.value, min, max, geometry),
    }))

  if (projectedPoints.length < 2) {
    return ''
  }
  if (!smooth || projectedPoints.length === 2) {
    return projectedPoints
      .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
      .join(' ')
  }

  const commands = [`M ${projectedPoints[0].x.toFixed(2)} ${projectedPoints[0].y.toFixed(2)}`]
  for (let index = 0; index < projectedPoints.length - 1; index += 1) {
    const previous = projectedPoints[Math.max(index - 1, 0)]
    const current = projectedPoints[index]
    const next = projectedPoints[index + 1]
    const nextNext = projectedPoints[Math.min(index + 2, projectedPoints.length - 1)]
    const control1X = current.x + (next.x - previous.x) / 6
    const control1Y = current.y + (next.y - previous.y) / 6
    const control2X = next.x - (nextNext.x - current.x) / 6
    const control2Y = next.y - (nextNext.y - current.y) / 6
    commands.push(
      [
        'C',
        control1X.toFixed(2),
        control1Y.toFixed(2),
        control2X.toFixed(2),
        control2Y.toFixed(2),
        next.x.toFixed(2),
        next.y.toFixed(2),
      ].join(' '),
    )
  }
  return commands.join(' ')
}

function getDateScaleDomain(
  points: FundChartPoint[],
  startDate: string | null | undefined,
  endDate: string | null | undefined,
) {
  const datedPoints = sortSeriesByDate(points)
    .map((point) => {
      const parsed = parseChartDateParts(point.date)
      return parsed ? { point, time: parsed.time } : null
    })
    .filter((item): item is { point: FundChartPoint; time: number } => item !== null)

  if (!datedPoints.length) {
    return null
  }

  const parsedStart = startDate ? parseChartDateParts(startDate) : null
  const parsedEnd = endDate ? parseChartDateParts(endDate) : null
  const startTime = parsedStart?.time ?? datedPoints[0].time
  const endTime = parsedEnd?.time ?? datedPoints[datedPoints.length - 1].time

  if (endTime <= startTime) {
    return null
  }

  return { startTime, endTime }
}

function getDateScaledProjectedPoints(
  points: FundChartPoint[],
  geometry: ChartGeometry,
  min: number,
  max: number,
  startDate: string | null | undefined,
  endDate: string | null | undefined,
) {
  const sortedPoints = sortSeriesByDate(points)
  const domain = getDateScaleDomain(sortedPoints, startDate, endDate)

  if (!domain) {
    return getProjectedSeriesPoints(sortedPoints, geometry, min, max).map((point, index) => ({
      point: sortedPoints[index],
      x: point.x,
      y: point.y,
    }))
  }

  const plotWidth = geometry.width - geometry.paddingLeft - geometry.paddingRight
  const timeRange = domain.endTime - domain.startTime

  return sortedPoints
    .map((point) => {
      const parsed = parseChartDateParts(point.date)
      return parsed ? { point, time: parsed.time } : null
    })
    .filter(
      (item): item is { point: FundChartPoint; time: number } =>
        item !== null && item.time >= domain.startTime && item.time <= domain.endTime,
    )
    .map(({ point, time }) => ({
      point,
      x: geometry.paddingLeft + ((time - domain.startTime) / timeRange) * plotWidth,
      y: projectChartValue(point.value, min, max, geometry),
    }))
}

function buildDateScaledAreaPath(
  points: FundChartPoint[],
  geometry: ChartGeometry,
  min: number,
  max: number,
  startDate: string | null | undefined,
  endDate: string | null | undefined,
  baselineValue?: number,
) {
  const projectedPoints = getDateScaledProjectedPoints(points, geometry, min, max, startDate, endDate)
  if (projectedPoints.length < 2) {
    return ''
  }

  const topPath = projectedPoints
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(' ')
  const firstX = projectedPoints[0].x
  const lastX = projectedPoints[projectedPoints.length - 1].x
  const baseline = projectChartValue(baselineValue ?? min, min, max, geometry)

  return `${topPath} L ${lastX.toFixed(2)} ${baseline.toFixed(2)} L ${firstX.toFixed(2)} ${baseline.toFixed(2)} Z`
}

function buildChartAreaPath(
  points: FundChartPoint[],
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
  min?: number,
  max?: number,
  baselineValue?: number,
) {
  if (points.length < 2) {
    return ''
  }

  const { width, height, paddingLeft, paddingRight, paddingTop, paddingBottom } = geometry
  const values = points.map((point) => point.value)
  const resolvedMin = min ?? Math.min(...values)
  const resolvedMax = max ?? Math.max(...values)
  const range = resolvedMax - resolvedMin || 1

  const topPath = points
    .map((point, index) => {
      const x =
        paddingLeft +
        (index / Math.max(points.length - 1, 1)) * (width - paddingLeft - paddingRight)
      const y =
        height -
        paddingBottom -
        ((point.value - resolvedMin) / range) * (height - paddingTop - paddingBottom)
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(' ')

  const lastX = width - paddingRight
  const firstX = paddingLeft
  const resolvedBaselineValue = baselineValue ?? resolvedMin
  const baseline =
    height -
    paddingBottom -
    ((resolvedBaselineValue - resolvedMin) / range) * (height - paddingTop - paddingBottom)

  return `${topPath} L ${lastX.toFixed(2)} ${baseline.toFixed(2)} L ${firstX.toFixed(2)} ${baseline.toFixed(2)} Z`
}

function filterChartPoints(points: FundChartPoint[], range: ChartRange) {
  if (range === 'MAX' || points.length < 2) {
    return points
  }

  const latestDate = new Date(points[points.length - 1].date)
  if (Number.isNaN(latestDate.getTime())) {
    return points
  }

  const cutoff = new Date(latestDate)
  if (range === '1M') {
    cutoff.setMonth(cutoff.getMonth() - 1)
  } else if (range === '3M') {
    cutoff.setMonth(cutoff.getMonth() - 3)
  } else if (range === '6M') {
    cutoff.setMonth(cutoff.getMonth() - 6)
  } else if (range === '1Y') {
    cutoff.setFullYear(cutoff.getFullYear() - 1)
  } else if (range === '3Y') {
    cutoff.setFullYear(cutoff.getFullYear() - 3)
  } else if (range === '5Y') {
    cutoff.setFullYear(cutoff.getFullYear() - 5)
  } else if (range === '10Y') {
    cutoff.setFullYear(cutoff.getFullYear() - 10)
  }

  const filtered = points.filter((point) => {
    const current = new Date(point.date)
    return !Number.isNaN(current.getTime()) && current >= cutoff
  })

  return filtered.length >= 2 ? filtered : points.slice(-Math.min(points.length, 2))
}

function buildBasisSeries(rows: FundNavSeriesResponse['rows'], basis: QuoteBasis) {
  return [...rows]
    .sort((left, right) => left.as_of_date.localeCompare(right.as_of_date))
    .map((row) => {
      const value =
        row.selected_basis_type === basis
          ? row.selected_value ?? (basis === 'nav' ? row.nav : row.nav_with_dividend)
          : basis === 'nav'
            ? row.nav
            : row.nav_with_dividend
      return value == null ? null : { date: row.as_of_date, value }
    })
    .filter((point): point is FundChartPoint => point !== null)
}

function getAvailableQuoteBases(rows: FundNavSeriesResponse['rows']) {
  return (['nav_with_dividend', 'nav'] as QuoteBasis[]).filter(
    (basis) => buildBasisSeries(rows, basis).length > 0,
  )
}

function getRowsForCurrency(rows: FundNavSeriesResponse['rows'], currency: string) {
  return rows.filter((row) => !currency || !row.currency || row.currency === currency)
}

function resolvePreferredQuoteBasis(value: string | null | undefined): QuoteBasis | null {
  return value === 'nav' || value === 'nav_with_dividend' ? value : null
}

function resolveReturnQuoteBasis(
  rows: FundNavSeriesResponse['rows'],
  preferredBasis: string | null | undefined,
) {
  const availableBases = getAvailableQuoteBases(rows)
  const preferred = resolvePreferredQuoteBasis(preferredBasis)
  if (preferred === 'nav_with_dividend' && availableBases.includes(preferred)) {
    return preferred
  }
  if (availableBases.includes('nav_with_dividend')) {
    return 'nav_with_dividend'
  }
  return null
}

function buildQuoteSeriesContext(
  rows: FundNavSeriesResponse['rows'],
  {
    currency,
    requestedBasis,
    preferredBasis,
  }: {
    currency: string
    requestedBasis: QuoteBasis
    preferredBasis: string | null | undefined
  },
) {
  const preferred = resolvePreferredQuoteBasis(preferredBasis)
  const currencyFilteredRows = getRowsForCurrency(rows, currency)
  const scopedRows = currencyFilteredRows.length ? currencyFilteredRows : rows
  const availableBases = getAvailableQuoteBases(scopedRows)
  const activeBasis =
    availableBases.includes(requestedBasis)
      ? requestedBasis
      : preferred && availableBases.includes(preferred)
        ? preferred
        : availableBases[0] || null
  const seriesFromRows = activeBasis ? buildBasisSeries(scopedRows, activeBasis) : []

  return {
    rows: scopedRows,
    availableBases,
    activeBasis,
    basisSeries: seriesFromRows,
    latestRow: scopedRows[scopedRows.length - 1],
  }
}

function formatChangeSummary(change: number | null, changePct: number | null, precision = 4) {
  const parts = [
    change == null ? null : `${change >= 0 ? '+' : ''}${formatNumber(change, precision)}`,
    changePct == null ? null : `${changePct >= 0 ? '+' : ''}${formatPercent(changePct)}`,
  ].filter((value): value is string => Boolean(value))

  return parts.length ? parts.join(' | ') : '—'
}

function getChartValueTagLayout(
  point: PositionedPoint | null | undefined,
  geometry: ChartGeometry,
  text: string | null,
) {
  if (!point || !text) {
    return null
  }

  const height = 22
  const width = Math.max(42, text.length * 6.1 + 12)
  const tailWidth = 8
  const direction = point.x > geometry.width * 0.55 ? 'right' : 'left'
  const x =
    direction === 'right'
      ? Math.min(
          Math.max(point.x - width - tailWidth - 4, geometry.paddingLeft + 8),
          geometry.width - width - tailWidth - 8,
        )
      : Math.min(
          Math.max(point.x + tailWidth + 4, geometry.paddingLeft + tailWidth + 8),
          geometry.width - width - tailWidth - 8,
        )
  const y = Math.min(
    Math.max(point.y - height / 2, geometry.paddingTop + 6),
    geometry.height - geometry.paddingBottom - height - 6,
  )
  const bubbleX = direction === 'right' ? x : x + tailWidth
  const tailMidY = Math.min(Math.max(point.y, y + 7), y + height - 7)
  const tipX = direction === 'right' ? bubbleX + width + tailWidth : bubbleX - tailWidth
  const textX = bubbleX + width / 2
  const textY = y + 14.5
  const tailHalfHeight = 4
  const path =
    direction === 'right'
      ? [
          `M ${bubbleX} ${y}`,
          `H ${bubbleX + width}`,
          `V ${tailMidY - tailHalfHeight}`,
          `L ${tipX} ${tailMidY}`,
          `L ${bubbleX + width} ${tailMidY + tailHalfHeight}`,
          `V ${y + height}`,
          `H ${bubbleX}`,
          `V ${y}`,
          'Z',
        ].join(' ')
      : [
          `M ${bubbleX} ${y}`,
          `H ${bubbleX + width}`,
          `V ${y + height}`,
          `H ${bubbleX}`,
          `V ${tailMidY + tailHalfHeight}`,
          `L ${tipX} ${tailMidY}`,
          `L ${bubbleX} ${tailMidY - tailHalfHeight}`,
          `V ${y}`,
          'Z',
        ].join(' ')

  return { path, textX, textY }
}

function getChartTooltipAnchor(cursor: ChartHoverCursor | null | undefined, geometry: ChartGeometry) {
  if (!cursor) {
    return null
  }

  const plotBounds = getChartPlotBounds(geometry, CHART_CROSSHAIR_INSET)
  const x = plotBounds.left + plotBounds.width * cursor.xRatio
  const y = cursor.y

  return {
    left: `${(x / geometry.width) * 100}%`,
    top: `${(y / geometry.height) * 100}%`,
    transform: 'translate(18px, calc(-100% - 12px))',
  }
}

function getDrawdownAxisBounds(points: FundChartPoint[]) {
  if (!points.length) {
    return { min: -5, max: 0 }
  }

  const rawMin = Math.min(...points.map((point) => point.value))
  const paddedMin = Math.floor((rawMin - 0.4) / 0.5) * 0.5

  return {
    min: Math.min(paddedMin, -0.5),
    max: 0,
  }
}

function getPaddedAxisBounds(min: number, max: number, ratio = 0.08, minimumPadding = 0.01) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return { min: 0, max: 1 }
  }
  if (min === max) {
    const padding = Math.max(Math.abs(min) * ratio, minimumPadding)
    return { min: min - padding, max: max + padding }
  }
  const padding = Math.max((max - min) * ratio, minimumPadding)
  return { min: min - padding, max: max + padding }
}

function getYAxisStubEndX(geometry: ChartGeometry) {
  return geometry.paddingLeft - 14
}

function getYAxisLabelTextY(lineY: number, geometry: ChartGeometry, placement: 'below' | 'above' = 'below') {
  if (placement === 'above') {
    return Math.max(lineY - 6, 12)
  }
  return Math.min(lineY + 18, geometry.height - 4)
}

function getXAxisLabelY(geometry: ChartGeometry) {
  return geometry.height - 10
}

function getXAxisTickTopY(geometry: ChartGeometry) {
  return geometry.height - geometry.paddingBottom + 5
}

function getChartPlotBounds(geometry: ChartGeometry, inset = 0) {
  const left = geometry.paddingLeft + inset
  const right = geometry.width - geometry.paddingRight - inset
  const top = geometry.paddingTop + inset
  const bottom = geometry.height - geometry.paddingBottom - inset

  return {
    left,
    right,
    top,
    bottom,
    width: Math.max(right - left, 1),
    height: Math.max(bottom - top, 1),
  }
}

function getPlotXFromRatio(geometry: ChartGeometry, xRatio: number, inset = CHART_CROSSHAIR_INSET) {
  const plotBounds = getChartPlotBounds(geometry, inset)
  return plotBounds.left + plotBounds.width * Math.min(Math.max(xRatio, 0), 1)
}

function findNearestPositionedPointIndex(points: Array<FundChartPoint & { x: number }>, targetX: number) {
  if (!points.length) {
    return -1
  }
  let nearestIndex = 0
  let nearestDistance = Math.abs(points[0].x - targetX)
  for (let index = 1; index < points.length; index += 1) {
    const distance = Math.abs(points[index].x - targetX)
    if (distance < nearestDistance) {
      nearestIndex = index
      nearestDistance = distance
    }
  }
  return nearestIndex
}

function applyChartScale(points: FundChartPoint[], scale: ChartScale) {
  if (!points.length) {
    return []
  }
  if (scale === 'logarithmic') {
    const canUseLogScale = points.every((point) => point.value > 0)
    if (!canUseLogScale) {
      return points
    }
    return points.map((point) => ({
      date: point.date,
      value: Math.log10(point.value),
    }))
  }
  return points
}

function buildDrawdownSeries(points: FundChartPoint[]) {
  let runningMax = 0
  return points.map((point) => {
    runningMax = Math.max(runningMax, point.value)
    const drawdown = runningMax > 0 ? ((point.value / runningMax) - 1) * 100 : 0
    return {
      date: point.date,
      value: drawdown,
    }
  })
}

function getSeriesChangeStats(points: FundChartPoint[]) {
  if (points.length < 2) {
    return { change: null, changePct: null }
  }
  const first = points[0]
  const last = points[points.length - 1]
  const change = last.value - first.value
  return {
    change,
    changePct: first.value !== 0 ? (change / first.value) * 100 : null,
  }
}

function getLatestPointChangeStats(points: FundChartPoint[]) {
  if (points.length < 2) {
    return { change: null, changePct: null }
  }
  const latest = points[points.length - 1]
  const previous = points[points.length - 2]
  const change = latest.value - previous.value
  return {
    change,
    changePct: previous.value !== 0 ? (change / previous.value) * 100 : null,
  }
}

function getRangeWindow(points: FundChartPoint[], range: ChartRange) {
  if (!points.length) {
    return { start: '', end: '' }
  }

  const end = points[points.length - 1].date
  if (range === 'MAX' || range === 'CUSTOM') {
    return { start: points[0].date, end }
  }

  const latestDate = new Date(`${end}T00:00:00`)
  if (Number.isNaN(latestDate.getTime())) {
    return { start: points[0].date, end }
  }

  const startDate = new Date(latestDate)
  if (range === '1M') {
    startDate.setMonth(startDate.getMonth() - 1)
  } else if (range === '3M') {
    startDate.setMonth(startDate.getMonth() - 3)
  } else if (range === '6M') {
    startDate.setMonth(startDate.getMonth() - 6)
  } else if (range === 'YTD') {
    startDate.setMonth(0)
    startDate.setDate(1)
  } else if (range === '1Y') {
    startDate.setFullYear(startDate.getFullYear() - 1)
  } else if (range === '3Y') {
    startDate.setFullYear(startDate.getFullYear() - 3)
  } else if (range === '5Y') {
    startDate.setFullYear(startDate.getFullYear() - 5)
  } else if (range === '10Y') {
    startDate.setFullYear(startDate.getFullYear() - 10)
  }

  return {
    start: startDate.toISOString().slice(0, 10),
    end,
  }
}

function filterSeriesByDateWindow(
  points: FundChartPoint[],
  startDate: string,
  endDate: string,
) {
  return points.filter((point) => {
    if (startDate && point.date < startDate) {
      return false
    }
    if (endDate && point.date > endDate) {
      return false
    }
    return true
  })
}

function getMonthBucket(value: string) {
  return value.slice(0, 7)
}

function getPreviousMonthBucket(monthBucket: string) {
  const year = Number(monthBucket.slice(0, 4))
  const month = Number(monthBucket.slice(5, 7))
  if (!Number.isFinite(year) || !Number.isFinite(month) || month < 1 || month > 12) {
    return null
  }
  const previousYear = month === 1 ? year - 1 : year
  const previousMonth = month === 1 ? 12 : month - 1
  return `${previousYear}-${String(previousMonth).padStart(2, '0')}`
}

function buildMonthlyCloseSeries(points: FundChartPoint[]) {
  const sortedPoints = [...points].sort((left, right) => left.date.localeCompare(right.date))
  const monthlyPoints: FundChartPoint[] = []

  sortedPoints.forEach((point) => {
    const currentBucket = getMonthBucket(point.date)
    const previousPoint = monthlyPoints[monthlyPoints.length - 1]
    if (!previousPoint || getMonthBucket(previousPoint.date) !== currentBucket) {
      monthlyPoints.push(point)
      return
    }
    monthlyPoints[monthlyPoints.length - 1] = point
  })

  return monthlyPoints
}

function rebaseSeries(points: FundChartPoint[], baseValue = 100) {
  if (!points.length || points[0].value === 0) {
    return []
  }
  const startingValue = points[0].value
  return points.map((point) => ({
    date: point.date,
    value: (point.value / startingValue) * baseValue,
  }))
}

function buildCommonDateWindow(leftPoints: FundChartPoint[], rightPoints: FundChartPoint[]) {
  if (!leftPoints.length || !rightPoints.length) {
    return null
  }

  const sortedLeft = sortSeriesByDate(leftPoints)
  const sortedRight = sortSeriesByDate(rightPoints)
  const start = sortedLeft[0].date > sortedRight[0].date ? sortedLeft[0].date : sortedRight[0].date
  const end =
    sortedLeft[sortedLeft.length - 1].date < sortedRight[sortedRight.length - 1].date
      ? sortedLeft[sortedLeft.length - 1].date
      : sortedRight[sortedRight.length - 1].date

  if (start > end) {
    return null
  }

  return { start, end }
}

function buildCommonWindowSeries(
  points: FundChartPoint[],
  commonWindow: { start: string; end: string } | null,
) {
  if (!commonWindow) {
    return []
  }

  const sortedPoints = sortSeriesByDate(points)
  if (!sortedPoints.length) {
    return []
  }

  const startIndex = findLastPointIndexOnOrBefore(sortedPoints, commonWindow.start)
  const firstInWindowIndex = sortedPoints.findIndex((point) => point.date >= commonWindow.start)
  const anchorPoint =
    startIndex >= 0
      ? sortedPoints[startIndex]
      : firstInWindowIndex >= 0
        ? sortedPoints[firstInWindowIndex]
        : null
  if (!anchorPoint || anchorPoint.date > commonWindow.end) {
    return []
  }

  const windowPoints: FundChartPoint[] = [
    {
      date: commonWindow.start,
      value: anchorPoint.value,
    },
  ]

  sortedPoints.forEach((point) => {
    if (point.date <= commonWindow.start || point.date > commonWindow.end) {
      return
    }
    windowPoints.push(point)
  })

  const lastPoint = windowPoints[windowPoints.length - 1]
  if (lastPoint && lastPoint.date < commonWindow.end) {
    windowPoints.push({
      date: commonWindow.end,
      value: lastPoint.value,
    })
  }

  return windowPoints
}

function buildCommonRebasedSeries(
  points: FundChartPoint[],
  commonWindow: { start: string; end: string } | null,
  baseValue = 1,
) {
  const windowPoints = buildCommonWindowSeries(points, commonWindow)
  return rebaseSeries(windowPoints, baseValue)
}

function getRangeWindowWithinDateWindow(
  dateWindow: { start: string; end: string },
  range: ChartRange,
) {
  if (range === 'MAX') {
    return dateWindow
  }

  const end = dateWindow.end
  const start =
    range === 'YTD'
      ? `${end.slice(0, 4)}-01-01`
      : range === '1M'
        ? shiftIsoDate(end, { months: -1 })
        : range === '3M'
          ? shiftIsoDate(end, { months: -3 })
          : range === '6M'
            ? shiftIsoDate(end, { months: -6 })
            : range === '1Y'
              ? shiftIsoDate(end, { years: -1 })
              : range === '3Y'
                ? shiftIsoDate(end, { years: -3 })
                : range === '5Y'
                  ? shiftIsoDate(end, { years: -5 })
                  : range === '10Y'
                    ? shiftIsoDate(end, { years: -10 })
                    : dateWindow.start

  return {
    start: start && start > dateWindow.start ? start : dateWindow.start,
    end,
  }
}

function resolveCommonChartWindow(
  commonWindow: { start: string; end: string } | null,
  range: ChartRange,
  customStartDate: string,
  customEndDate: string,
) {
  if (!commonWindow) {
    return null
  }

  const resolvedWindow =
    range === 'CUSTOM'
      ? {
          start: customStartDate && customStartDate > commonWindow.start ? customStartDate : commonWindow.start,
          end: customEndDate && customEndDate < commonWindow.end ? customEndDate : commonWindow.end,
        }
      : getRangeWindowWithinDateWindow(commonWindow, range)

  return resolvedWindow.start <= resolvedWindow.end ? resolvedWindow : null
}

function resampleSeriesPreservingBounds(points: FundChartPoint[], frequency: ChartFrequency) {
  if (frequency === 'daily' || points.length < 2) {
    return points
  }

  const sampledPoints = resampleSeries(points, frequency)
  const firstPoint = points[0]
  const lastPoint = points[points.length - 1]
  const pointMap = new Map(sampledPoints.map((point) => [point.date, point] as const))
  pointMap.set(firstPoint.date, firstPoint)
  pointMap.set(lastPoint.date, lastPoint)
  return sortSeriesByDate(Array.from(pointMap.values()))
}

function getPointAtDate<T extends FundChartPoint>(points: T[], targetDate: string | undefined) {
  if (!targetDate) {
    return null
  }
  return points.find((point) => point.date === targetDate) || null
}

function getPointAtOrNearestDate(points: FundChartPoint[], targetDate: string | undefined) {
  return getPointAtDate(points, targetDate) || findNearestChartPoint(points, targetDate)
}

function buildMonthlyReturnSeries(points: FundChartPoint[]) {
  const monthlyCloses = buildMonthlyCloseSeries(points)
  const monthlyCloseByBucket = new Map(monthlyCloses.map((point) => [getMonthBucket(point.date), point] as const))
  const monthlyReturns: FundChartPoint[] = []

  for (const currentPoint of monthlyCloses) {
    const previousBucket = getPreviousMonthBucket(getMonthBucket(currentPoint.date))
    const previousPoint = previousBucket ? monthlyCloseByBucket.get(previousBucket) : null
    if (!previousPoint) {
      continue
    }
    if (previousPoint.value === 0) {
      continue
    }
    monthlyReturns.push({
      date: currentPoint.date,
      value: ((currentPoint.value / previousPoint.value) - 1) * 100,
    })
  }

  return monthlyReturns
}

function getRollingWindowPoints(
  sortedPoints: FundChartPoint[],
  endIndex: number,
  windowMonths: number,
) {
  if (endIndex <= 0 || endIndex >= sortedPoints.length) {
    return []
  }

  const endPoint = sortedPoints[endIndex]
  const targetStartDate = shiftIsoDate(endPoint.date, { months: -windowMonths })
  if (!targetStartDate) {
    return []
  }

  const startIndex = findLastPointIndexOnOrBefore(sortedPoints, targetStartDate)
  if (startIndex < 0 || startIndex >= endIndex) {
    return []
  }

  const targetDays = getDateDifferenceInDays(targetStartDate, endPoint.date)
  const actualDays = getDateDifferenceInDays(sortedPoints[startIndex].date, endPoint.date)
  if (targetDays == null || actualDays == null || targetDays <= 0 || actualDays <= 0) {
    return []
  }

  const maxActualDays = targetDays * 1.35 + 14
  if (actualDays > maxActualDays) {
    return []
  }

  return sortedPoints.slice(startIndex, endIndex + 1)
}

function buildRollingAnnualizedReturnSeries(points: FundChartPoint[], windowMonths = ROLLING_WINDOW_MONTHS) {
  const sortedPoints = sortSeriesByDate(points)
  const rollingReturns: FundChartPoint[] = []

  for (let index = 1; index < sortedPoints.length; index += 1) {
    const windowPoints = getRollingWindowPoints(sortedPoints, index, windowMonths)
    if (windowPoints.length < 2) {
      continue
    }
    const basePoint = windowPoints[0]
    const currentPoint = windowPoints[windowPoints.length - 1]
    const dayCount = getDateDifferenceInDays(basePoint.date, currentPoint.date)
    if (basePoint.value <= 0 || currentPoint.value <= 0 || dayCount == null || dayCount <= 0) {
      continue
    }
    rollingReturns.push({
      date: currentPoint.date,
      value: (Math.pow(currentPoint.value / basePoint.value, 365.25 / dayCount) - 1) * 100,
    })
  }

  return rollingReturns
}

function buildRollingAnnualizedVolatilitySeries(points: FundChartPoint[], windowMonths = ROLLING_WINDOW_MONTHS) {
  const sortedPoints = sortSeriesByDate(points)
  const rollingVolatility: FundChartPoint[] = []

  for (let index = 1; index < sortedPoints.length; index += 1) {
    const windowPoints = getRollingWindowPoints(sortedPoints, index, windowMonths)
    const windowReturns = buildPeriodicReturnSeries(windowPoints).map((point) => point.value)
    if (windowReturns.length < MIN_ROLLING_RETURN_OBSERVATIONS) {
      continue
    }
    const stdev = getSampleStandardDeviation(windowReturns)
    const periodsPerYear = inferAnnualizationPeriodsPerYear(windowPoints, windowReturns.length)
    if (stdev == null || periodsPerYear == null || periodsPerYear <= 0) {
      continue
    }
    rollingVolatility.push({
      date: sortedPoints[index].date,
      value: stdev * Math.sqrt(periodsPerYear) * 100,
    })
  }

  return rollingVolatility
}

function buildRollingSharpeSeries(points: FundChartPoint[], windowMonths = ROLLING_WINDOW_MONTHS) {
  const sortedPoints = sortSeriesByDate(points)
  const rollingSharpe: FundChartPoint[] = []

  for (let index = 1; index < sortedPoints.length; index += 1) {
    const windowPoints = getRollingWindowPoints(sortedPoints, index, windowMonths)
    const windowReturns = buildPeriodicReturnSeries(windowPoints).map((point) => point.value)
    if (windowReturns.length < MIN_ROLLING_RETURN_OBSERVATIONS) {
      continue
    }
    const stdev = getSampleStandardDeviation(windowReturns)
    const periodsPerYear = inferAnnualizationPeriodsPerYear(windowPoints, windowReturns.length)
    if (stdev == null || stdev === 0 || periodsPerYear == null || periodsPerYear <= 0) {
      continue
    }
    const mean = windowReturns.reduce((sum, value) => sum + value, 0) / windowReturns.length
    rollingSharpe.push({
      date: sortedPoints[index].date,
      value: (mean / stdev) * Math.sqrt(periodsPerYear),
    })
  }

  return rollingSharpe
}

function alignMonthlyReturnPairs(leftPoints: FundChartPoint[], rightPoints: FundChartPoint[]) {
  const leftMonthlyReturns = buildMonthlyReturnSeries(leftPoints)
  const rightMonthlyReturns = buildMonthlyReturnSeries(rightPoints)
  const rightMap = new Map(
    rightMonthlyReturns.map((point) => [getMonthBucket(point.date), { date: point.date, value: point.value / 100 }] as const),
  )

  return leftMonthlyReturns
    .map((point) => {
      const bucket = getMonthBucket(point.date)
      const rightPoint = rightMap.get(bucket)
      if (!rightPoint) {
        return null
      }
      return {
        date: point.date,
        left: point.value / 100,
        right: rightPoint.value,
      }
    })
    .filter(
      (point): point is { date: string; left: number; right: number } => point !== null,
    )
}

function getSampleCovariance(left: number[], right: number[]) {
  if (left.length < 2 || right.length < 2 || left.length !== right.length) {
    return null
  }
  const leftMean = left.reduce((sum, value) => sum + value, 0) / left.length
  const rightMean = right.reduce((sum, value) => sum + value, 0) / right.length
  const covariance =
    left.reduce((sum, value, index) => sum + ((value - leftMean) * (right[index] - rightMean)), 0) /
    (left.length - 1)
  return covariance
}

function buildRollingBetaSeries(
  points: FundChartPoint[],
  benchmarkPoints: FundChartPoint[],
  windowMonths = ROLLING_WINDOW_MONTHS,
) {
  const alignedPairs = alignMonthlyReturnPairs(points, benchmarkPoints)
  const rollingBeta: FundChartPoint[] = []

  for (let index = windowMonths - 1; index < alignedPairs.length; index += 1) {
    const windowPairs = alignedPairs.slice(index - windowMonths + 1, index + 1)
    const leftReturns = windowPairs.map((point) => point.left)
    const rightReturns = windowPairs.map((point) => point.right)
    const covariance = getSampleCovariance(leftReturns, rightReturns)
    const variance = getSampleStandardDeviation(rightReturns)
    if (covariance == null || variance == null || variance === 0) {
      continue
    }
    rollingBeta.push({
      date: windowPairs[windowPairs.length - 1].date,
      value: covariance / (variance ** 2),
    })
  }

  return rollingBeta
}

function buildMonthlyMinimumSeries(points: FundChartPoint[]) {
  const sortedPoints = [...points].sort((left, right) => left.date.localeCompare(right.date))
  const monthlyMinimums: FundChartPoint[] = []

  sortedPoints.forEach((point) => {
    const currentBucket = getMonthBucket(point.date)
    const previousPoint = monthlyMinimums[monthlyMinimums.length - 1]
    if (!previousPoint || getMonthBucket(previousPoint.date) !== currentBucket) {
      monthlyMinimums.push(point)
      return
    }
    if (point.value <= previousPoint.value) {
      monthlyMinimums[monthlyMinimums.length - 1] = point
    }
  })

  return monthlyMinimums
}

function inferAnnualizationPeriodsPerYear(points: FundChartPoint[], returnCount?: number) {
  const sortedPoints = sortSeriesByDate(points)
  const realizedReturnCount = returnCount ?? Math.max(sortedPoints.length - 1, 0)
  if (sortedPoints.length < 2 || realizedReturnCount < 1) {
    return null
  }
  const elapsedDays = getDateDifferenceInDays(
    sortedPoints[0].date,
    sortedPoints[sortedPoints.length - 1].date,
  )
  if (elapsedDays == null || elapsedDays <= 0) {
    return null
  }
  return (realizedReturnCount / elapsedDays) * 365.25
}

function getSampleStandardDeviation(values: number[]) {
  if (values.length < 2) {
    return null
  }
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const variance =
    values.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / (values.length - 1)
  return Math.sqrt(Math.max(variance, 0))
}

function buildMonthlyAnnualizedVolatilitySeries(points: FundChartPoint[]) {
  const sortedPoints = [...points].sort((left, right) => left.date.localeCompare(right.date))
  const returnCount = buildPeriodicReturnSeries(sortedPoints).length
  const periodsPerYear = inferAnnualizationPeriodsPerYear(sortedPoints, returnCount)
  if (periodsPerYear == null || periodsPerYear <= 0) {
    return []
  }
  const returnsByMonth = new Map<string, { date: string; returns: number[] }>()

  for (let index = 1; index < sortedPoints.length; index += 1) {
    const previousPoint = sortedPoints[index - 1]
    const currentPoint = sortedPoints[index]
    if (previousPoint.value === 0) {
      continue
    }
    const monthlyKey = getMonthBucket(currentPoint.date)
    const bucket = returnsByMonth.get(monthlyKey) || { date: currentPoint.date, returns: [] }
    bucket.date = currentPoint.date
    bucket.returns.push((currentPoint.value / previousPoint.value) - 1)
    returnsByMonth.set(monthlyKey, bucket)
  }

  return Array.from(returnsByMonth.entries())
    .sort((left, right) => left[0].localeCompare(right[0]))
    .map(([, bucket]) => {
      const stdev = getSampleStandardDeviation(bucket.returns)
      if (stdev == null) {
        return null
      }
      return {
        date: bucket.date,
        value: stdev * Math.sqrt(periodsPerYear) * 100,
      }
    })
    .filter((point): point is FundChartPoint => point !== null)
}

function buildMonthlyReturnMatrix(points: FundChartPoint[]) {
  const monthlyReturns = buildMonthlyReturnSeries(points)
  const monthlyCloses = buildMonthlyCloseSeries(points)
  const monthlyCloseByBucket = new Map(monthlyCloses.map((point) => [getMonthBucket(point.date), point] as const))
  const rows = new Map<number, { year: string; months: Array<number | null>; ytd: number | null }>()
  const latestCloseByYear = new Map<number, FundChartPoint>()

  monthlyReturns.forEach((point) => {
    const year = Number(point.date.slice(0, 4))
    const monthIndex = Number(point.date.slice(5, 7)) - 1
    const row = rows.get(year) || {
      year: String(year),
      months: Array.from({ length: 12 }, () => null),
      ytd: null,
    }
    row.months[monthIndex] = point.value
    rows.set(year, row)
  })

  monthlyCloses.forEach((point) => {
    latestCloseByYear.set(Number(point.date.slice(0, 4)), point)
  })

  return Array.from(rows.entries())
    .sort((left, right) => right[0] - left[0])
    .map(([year, row]) => {
      const previousYearClose = monthlyCloseByBucket.get(`${year - 1}-12`)
      const currentYearClose = latestCloseByYear.get(year)
      const ytd =
        previousYearClose && currentYearClose && previousYearClose.value !== 0
          ? ((currentYearClose.value / previousYearClose.value) - 1) * 100
          : null
      return {
        ...row,
        ytd,
      }
    })
}

function sortSeriesByDate(points: FundChartPoint[]) {
  return [...points].sort((left, right) => left.date.localeCompare(right.date))
}

function shiftIsoDate(
  value: string,
  offset: { days?: number; months?: number; years?: number },
) {
  const year = Number(value.slice(0, 4))
  const month = Number(value.slice(5, 7))
  const day = Number(value.slice(8, 10))
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) {
    return null
  }

  let targetYear = year + (offset.years || 0)
  let targetMonthIndex = month - 1 + (offset.months || 0)
  while (targetMonthIndex < 0) {
    targetMonthIndex += 12
    targetYear -= 1
  }
  while (targetMonthIndex > 11) {
    targetMonthIndex -= 12
    targetYear += 1
  }

  const lastDayOfMonth = new Date(Date.UTC(targetYear, targetMonthIndex + 1, 0)).getUTCDate()
  const targetDay = Math.min(day, lastDayOfMonth)
  const shifted = new Date(Date.UTC(targetYear, targetMonthIndex, targetDay))
  if (offset.days) {
    shifted.setUTCDate(shifted.getUTCDate() + offset.days)
  }
  return shifted.toISOString().slice(0, 10)
}

function findLastPointIndexOnOrBefore(points: FundChartPoint[], targetDate: string) {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index].date <= targetDate) {
      return index
    }
  }
  return -1
}

function findLastPointIndexBefore(points: FundChartPoint[], targetDate: string) {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index].date < targetDate) {
      return index
    }
  }
  return -1
}

function getAnchoredWindow(
  points: FundChartPoint[],
  periodKey: PerformanceMetricPeriodKey,
  referenceEndDate?: string | null,
) {
  const sortedPoints = sortSeriesByDate(points)
  if (!sortedPoints.length) {
    return []
  }

  const requestedEndDate = referenceEndDate || sortedPoints[sortedPoints.length - 1]?.date || ''
  const endIndex = findLastPointIndexOnOrBefore(sortedPoints, requestedEndDate)
  if (endIndex < 0) {
    return []
  }

  if (periodKey === 'SI') {
    return sortedPoints.slice(0, endIndex + 1)
  }

  const endPoint = sortedPoints[endIndex]
  const targetStartDate =
    periodKey === 'YTD'
      ? `${endPoint.date.slice(0, 4)}-01-01`
      : periodKey === 'MTD'
        ? `${endPoint.date.slice(0, 7)}-01`
      : periodKey === '1W'
        ? shiftIsoDate(endPoint.date, { days: -7 })
        : periodKey === '1Y'
          ? shiftIsoDate(endPoint.date, { years: -1 })
          : periodKey === '2Y'
            ? shiftIsoDate(endPoint.date, { years: -2 })
            : periodKey === '3Y'
              ? shiftIsoDate(endPoint.date, { years: -3 })
              : shiftIsoDate(endPoint.date, { years: -5 })

  if (!targetStartDate) {
    return []
  }

  const startIndex =
    periodKey === 'YTD' || periodKey === 'MTD'
      ? findLastPointIndexBefore(sortedPoints, targetStartDate)
      : findLastPointIndexOnOrBefore(sortedPoints, targetStartDate)
  if (startIndex < 0 || startIndex >= endIndex) {
    return []
  }
  return sortedPoints.slice(startIndex, endIndex + 1)
}

function getPeriodReturnFromPoints(points: FundChartPoint[]) {
  if (points.length < 2 || points[0].value === 0) {
    return null
  }
  return ((points[points.length - 1].value / points[0].value) - 1) * 100
}

function getAnnualizedReturnFromPoints(points: FundChartPoint[]) {
  if (points.length < 2 || points[0].value <= 0 || points[points.length - 1].value <= 0) {
    return null
  }
  const dayCount = getDateDifferenceInDays(points[0].date, points[points.length - 1].date)
  if (dayCount == null || dayCount <= 0) {
    return null
  }
  return (Math.pow(points[points.length - 1].value / points[0].value, 365.25 / dayCount) - 1) * 100
}

function getDownsideDeviation(values: number[]) {
  const downside = values.filter((value) => value < 0)
  if (!downside.length) {
    return null
  }
  const variance = downside.reduce((sum, value) => sum + (value ** 2), 0) / downside.length
  return Math.sqrt(Math.max(variance, 0))
}

function buildPeriodicReturnSeries(points: FundChartPoint[]) {
  const sortedPoints = sortSeriesByDate(points)
  const periodicReturns: PeriodicReturnPoint[] = []

  for (let index = 1; index < sortedPoints.length; index += 1) {
    const previousPoint = sortedPoints[index - 1]
    const currentPoint = sortedPoints[index]
    if (previousPoint.value === 0) {
      continue
    }
    periodicReturns.push({
      startDate: previousPoint.date,
      endDate: currentPoint.date,
      value: (currentPoint.value / previousPoint.value) - 1,
    })
  }

  return periodicReturns
}

function getAnnualizedVolatilityFromPoints(points: FundChartPoint[]) {
  const periodicReturns = buildPeriodicReturnSeries(points).map((point) => point.value)
  if (periodicReturns.length < 2) {
    return null
  }
  const stdev = getSampleStandardDeviation(periodicReturns)
  if (stdev == null) {
    return null
  }
  const periodsPerYear = inferAnnualizationPeriodsPerYear(points, periodicReturns.length)
  if (periodsPerYear == null || periodsPerYear <= 0) {
    return null
  }
  return stdev * Math.sqrt(periodsPerYear) * 100
}

function getAnnualizedDownsideDeviationFromPoints(points: FundChartPoint[]) {
  const periodicReturns = buildPeriodicReturnSeries(points).map((point) => point.value)
  if (periodicReturns.length < 2) {
    return null
  }
  const downsideDeviation = getDownsideDeviation(periodicReturns)
  if (downsideDeviation == null) {
    return null
  }
  const periodsPerYear = inferAnnualizationPeriodsPerYear(points, periodicReturns.length)
  if (periodsPerYear == null || periodsPerYear <= 0) {
    return null
  }
  return downsideDeviation * Math.sqrt(periodsPerYear) * 100
}

function getSharpeRatioFromPoints(points: FundChartPoint[]) {
  const periodicReturns = buildPeriodicReturnSeries(points).map((point) => point.value)
  if (periodicReturns.length < 2) {
    return null
  }
  const stdev = getSampleStandardDeviation(periodicReturns)
  if (stdev == null || stdev === 0) {
    return null
  }
  const mean = periodicReturns.reduce((sum, value) => sum + value, 0) / periodicReturns.length
  const periodsPerYear = inferAnnualizationPeriodsPerYear(points, periodicReturns.length)
  if (periodsPerYear == null || periodsPerYear <= 0) {
    return null
  }
  return (mean / stdev) * Math.sqrt(periodsPerYear)
}

function getSortinoRatioFromPoints(points: FundChartPoint[]) {
  const periodicReturns = buildPeriodicReturnSeries(points).map((point) => point.value)
  if (periodicReturns.length < 2) {
    return null
  }
  const downsideDeviation = getDownsideDeviation(periodicReturns)
  if (downsideDeviation == null || downsideDeviation === 0) {
    return null
  }
  const mean = periodicReturns.reduce((sum, value) => sum + value, 0) / periodicReturns.length
  const periodsPerYear = inferAnnualizationPeriodsPerYear(points, periodicReturns.length)
  if (periodsPerYear == null || periodsPerYear <= 0) {
    return null
  }
  return (mean / downsideDeviation) * Math.sqrt(periodsPerYear)
}

function alignPeriodicReturnPairs(
  points: FundChartPoint[],
  benchmarkPoints: FundChartPoint[],
) {
  const periodicReturns = buildPeriodicReturnSeries(points)
  const sortedBenchmarkPoints = sortSeriesByDate(benchmarkPoints)

  return periodicReturns
    .map((point) => {
      const benchmarkStartIndex = findLastPointIndexOnOrBefore(sortedBenchmarkPoints, point.startDate)
      const benchmarkEndIndex = findLastPointIndexOnOrBefore(sortedBenchmarkPoints, point.endDate)
      if (
        benchmarkStartIndex < 0 ||
        benchmarkEndIndex <= benchmarkStartIndex ||
        sortedBenchmarkPoints[benchmarkStartIndex].value === 0
      ) {
        return null
      }
      return {
        startDate: point.startDate,
        date: point.endDate,
        left: point.value,
        right:
          (sortedBenchmarkPoints[benchmarkEndIndex].value /
            sortedBenchmarkPoints[benchmarkStartIndex].value) -
          1,
      }
    })
    .filter(
      (
        point,
      ): point is {
        startDate: string
        date: string
        left: number
        right: number
      } => point !== null,
    )
}

function getAnnualizedReturnFromPeriodicValues(values: number[], periodsPerYear: number) {
  if (!values.length || periodsPerYear <= 0) {
    return null
  }
  const cumulative = values.reduce((product, value) => product * (1 + value), 1)
  if (!Number.isFinite(cumulative) || cumulative <= 0) {
    return null
  }
  return (Math.pow(cumulative, periodsPerYear / values.length) - 1) * 100
}

function getMedianValue(values: number[]) {
  if (!values.length) {
    return null
  }
  const sortedValues = [...values].sort((left, right) => left - right)
  const middleIndex = Math.floor(sortedValues.length / 2)
  if (sortedValues.length % 2 === 0) {
    return (sortedValues[middleIndex - 1] + sortedValues[middleIndex]) / 2
  }
  return sortedValues[middleIndex]
}

function getPercentileRank(values: number[], targetValue: number) {
  if (!values.length) {
    return null
  }
  const belowOrEqualCount = values.filter((value) => value <= targetValue).length
  return (belowOrEqualCount / values.length) * 100
}

function getTrailingNegativeMonthCount(points: FundChartPoint[]) {
  let count = 0
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index].value >= 0) {
      break
    }
    count += 1
  }
  return count
}

function buildPerformanceRelativeSnapshot(
  points: FundChartPoint[],
  benchmarkPoints: FundChartPoint[],
): PerformanceRelativeSnapshot {
  const alignedPairs = alignPeriodicReturnPairs(points, benchmarkPoints)
  if (alignedPairs.length < 2) {
    return {
      informationRatio: null,
      trackingError: null,
      beta: null,
      upsideCapture: null,
      downsideCapture: null,
    }
  }

  const annualizationPoints = [
    { date: alignedPairs[0].startDate, value: 1 },
    ...alignedPairs.map((point) => ({ date: point.date, value: 1 })),
  ]
  const periodsPerYear = inferAnnualizationPeriodsPerYear(annualizationPoints, alignedPairs.length)
  const activeReturns = alignedPairs.map((point) => point.left - point.right)
  const activeReturnStdev = getSampleStandardDeviation(activeReturns)
  const activeReturnMean = activeReturns.reduce((sum, value) => sum + value, 0) / activeReturns.length
  const trackingError =
    activeReturnStdev == null || periodsPerYear == null || periodsPerYear <= 0
      ? null
      : activeReturnStdev * Math.sqrt(periodsPerYear) * 100
  const informationRatio =
    activeReturnStdev == null || activeReturnStdev === 0 || periodsPerYear == null || periodsPerYear <= 0
      ? null
      : (activeReturnMean / activeReturnStdev) * Math.sqrt(periodsPerYear)

  const benchmarkReturns = alignedPairs.map((point) => point.right)
  const benchmarkVolatility = getSampleStandardDeviation(benchmarkReturns)
  const beta =
    benchmarkVolatility == null || benchmarkVolatility === 0
      ? null
      : (getSampleCovariance(
          alignedPairs.map((point) => point.left),
          benchmarkReturns,
        ) ?? NaN) /
        (benchmarkVolatility ** 2)

  const upPairs = alignedPairs.filter((point) => point.right > 0)
  const downPairs = alignedPairs.filter((point) => point.right < 0)
  const upsideBenchmarkReturn =
    periodsPerYear == null
      ? null
      : getAnnualizedReturnFromPeriodicValues(
          upPairs.map((point) => point.right),
          periodsPerYear,
        )
  const upsideFundReturn =
    periodsPerYear == null
      ? null
      : getAnnualizedReturnFromPeriodicValues(
          upPairs.map((point) => point.left),
          periodsPerYear,
        )
  const downsideBenchmarkReturn =
    periodsPerYear == null
      ? null
      : getAnnualizedReturnFromPeriodicValues(
          downPairs.map((point) => point.right),
          periodsPerYear,
        )
  const downsideFundReturn =
    periodsPerYear == null
      ? null
      : getAnnualizedReturnFromPeriodicValues(
          downPairs.map((point) => point.left),
          periodsPerYear,
        )

  return {
    informationRatio: Number.isFinite(informationRatio) ? informationRatio : null,
    trackingError: Number.isFinite(trackingError) ? trackingError : null,
    beta: Number.isFinite(beta) ? beta : null,
    upsideCapture:
      upsideFundReturn == null || upsideBenchmarkReturn == null || upsideBenchmarkReturn === 0
        ? null
        : (upsideFundReturn / upsideBenchmarkReturn) * 100,
    downsideCapture:
      downsideFundReturn == null || downsideBenchmarkReturn == null || downsideBenchmarkReturn === 0
        ? null
        : (downsideFundReturn / downsideBenchmarkReturn) * 100,
  }
}

function getDateDifferenceInDays(startDate: string, endDate: string) {
  const start = new Date(`${startDate}T00:00:00`)
  const end = new Date(`${endDate}T00:00:00`)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return null
  }
  return Math.max(Math.round((end.getTime() - start.getTime()) / 86_400_000), 0)
}

function getMaxDrawdownStatsFromPoints(points: FundChartPoint[]) {
  if (points.length < 2) {
    return {
      maxDrawdown: null,
      recoveryDays: null,
      recoveryOpen: false,
    }
  }

  let runningPeakValue = points[0].value
  let runningPeakIndex = 0
  let worstDrawdown = 0
  let worstPeakIndex = 0
  let worstTroughIndex: number | null = null

  for (let index = 1; index < points.length; index += 1) {
    const point = points[index]
    if (point.value > runningPeakValue) {
      runningPeakValue = point.value
      runningPeakIndex = index
    }
    const drawdown = runningPeakValue > 0 ? ((point.value / runningPeakValue) - 1) * 100 : 0
    if (drawdown < worstDrawdown) {
      worstDrawdown = drawdown
      worstPeakIndex = runningPeakIndex
      worstTroughIndex = index
    }
  }

  if (worstTroughIndex == null) {
    return {
      maxDrawdown: 0,
      recoveryDays: 0,
      recoveryOpen: false,
    }
  }

  const recoveryTargetValue = points[worstPeakIndex]?.value ?? null
  let recoveryDays: number | null = null
  let recoveryOpen = true

  if (recoveryTargetValue != null) {
    for (let index = worstTroughIndex + 1; index < points.length; index += 1) {
      if (points[index].value >= recoveryTargetValue) {
        recoveryDays = getDateDifferenceInDays(points[worstTroughIndex].date, points[index].date)
        recoveryOpen = false
        break
      }
    }
  }

  return {
    maxDrawdown: worstDrawdown,
    recoveryDays,
    recoveryOpen,
  }
}

function buildPerformanceMetricSnapshot(points: FundChartPoint[]): PerformanceMetricSnapshot {
  if (points.length < 2) {
    return {
      periodReturn: null,
      annualizedReturn: null,
      annualizedVolatility: null,
      annualizedDownsideDeviation: null,
      sharpe: null,
      sortino: null,
      calmar: null,
      maxDrawdown: null,
      recoveryDays: null,
      recoveryOpen: false,
    }
  }

  const periodReturn = getPeriodReturnFromPoints(points)
  const annualizedReturn = getAnnualizedReturnFromPoints(points)
  const annualizedVolatility = getAnnualizedVolatilityFromPoints(points)
  const annualizedDownsideDeviation = getAnnualizedDownsideDeviationFromPoints(points)
  const sharpe = getSharpeRatioFromPoints(points)
  const sortino = getSortinoRatioFromPoints(points)
  const drawdownStats = getMaxDrawdownStatsFromPoints(points)
  const calmar =
    annualizedReturn != null &&
    drawdownStats.maxDrawdown != null &&
    drawdownStats.maxDrawdown !== 0
      ? annualizedReturn / Math.abs(drawdownStats.maxDrawdown)
      : null

  return {
    periodReturn,
    annualizedReturn,
    annualizedVolatility,
    annualizedDownsideDeviation,
    sharpe,
    sortino,
    calmar,
    maxDrawdown: drawdownStats.maxDrawdown,
    recoveryDays: drawdownStats.recoveryDays,
    recoveryOpen: drawdownStats.recoveryOpen,
  }
}

function getHeatmapCellStyle(value: number | null, maxAbsValue: number) {
  if (value == null) {
    return undefined
  }
  const normalized = Math.min(Math.abs(value) / Math.max(maxAbsValue, 1), 1)
  if (value >= 0) {
    return {
      backgroundColor: `rgba(13, 122, 56, ${0.08 + normalized * 0.26})`,
      color: '#0d5e31',
    }
  }
  return {
    backgroundColor: `rgba(175, 0, 0, ${0.08 + normalized * 0.24})`,
    color: '#8f1d1d',
  }
}

function getWeekBucket(value: string) {
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  const day = date.getDay()
  const diff = day === 0 ? -6 : 1 - day
  date.setDate(date.getDate() + diff)
  return date.toISOString().slice(0, 10)
}

function resampleSeries(points: FundChartPoint[], frequency: ChartFrequency) {
  if (frequency === 'daily') {
    return points
  }

  const buckets = new Map<string, FundChartPoint>()
  points.forEach((point) => {
    const bucket =
      frequency === 'weekly'
        ? getWeekBucket(point.date)
        : point.date.slice(0, 7)
    buckets.set(bucket, point)
  })

  return Array.from(buckets.values()).sort((left, right) => left.date.localeCompare(right.date))
}

function buildCalculationPointSeries(series: FundNavSeriesResponse['calculation_series']) {
  return series
    .map((point) => ({ date: point.date, value: point.value ?? point.nav }))
    .sort((left, right) => left.date.localeCompare(right.date))
}

function getChartTickValues(points: FundChartPoint[], count = 5) {
  if (!points.length) {
    return []
  }
  const values = points.map((point) => point.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min
  if (range === 0) {
    return [min]
  }
  return Array.from({ length: count }, (_, index) => min + (range / (count - 1)) * index)
}

function getLogTickValues(points: FundChartPoint[], count = 5) {
  if (!points.length || points.some((point) => point.value <= 0)) {
    return getChartTickValues(points, count)
  }
  const values = points.map((point) => point.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const logMin = Math.log10(min)
  const logMax = Math.log10(max)
  if (!Number.isFinite(logMin) || !Number.isFinite(logMax) || logMin === logMax) {
    return [min]
  }
  return Array.from(
    { length: count },
    (_, index) => 10 ** (logMin + ((logMax - logMin) / Math.max(count - 1, 1)) * index),
  )
}

function getLogTickValuesFromBounds(logMin: number, logMax: number, count = 5) {
  if (!Number.isFinite(logMin) || !Number.isFinite(logMax)) {
    return []
  }
  if (logMin === logMax) {
    return [10 ** logMin]
  }
  return Array.from(
    { length: count },
    (_, index) => 10 ** (logMin + ((logMax - logMin) / Math.max(count - 1, 1)) * index),
  )
}

function getLinearTickValues(min: number, max: number, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return []
  }
  if (min === max) {
    return [min]
  }
  return Array.from({ length: count }, (_, index) => min + ((max - min) / Math.max(count - 1, 1)) * index)
}

function formatAxisNumber(value: number) {
  const digits = Math.abs(value) >= 100 ? 2 : 4
  return value.toFixed(digits)
}

function parseChartDateParts(value: string) {
  const match = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(value)
  if (!match) {
    return null
  }
  const year = Number(match[1])
  const month = Number(match[2])
  const day = match[3] ? Number(match[3]) : 1
  if (
    !Number.isFinite(year) ||
    !Number.isFinite(month) ||
    !Number.isFinite(day) ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > 31
  ) {
    return null
  }
  return {
    year,
    month,
    day,
    time: Date.UTC(year, month - 1, day),
  }
}

function getChartDateSpanDays(points: FundChartPoint[]) {
  if (points.length < 2) {
    return 0
  }
  const first = parseChartDateParts(points[0].date)
  const last = parseChartDateParts(points[points.length - 1].date)
  if (!first || !last) {
    return 0
  }
  return Math.max(0, Math.round((last.time - first.time) / 86_400_000))
}

function formatUtcChartDate(time: number) {
  const date = new Date(time)
  const year = date.getUTCFullYear()
  const month = date.getUTCMonth() + 1
  const day = date.getUTCDate()
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

function formatChartAxisTickLabel(value: string, spanDays: number, previousValue: string | null = null) {
  const parts = parseChartDateParts(value)
  if (!parts) {
    return value
  }
  const previousParts = previousValue ? parseChartDateParts(previousValue) : null
  const crossesYear = Boolean(previousParts && previousParts.year !== parts.year)
  const isFirstTick = previousParts == null
  const isYearStart = parts.month === 1 && (!previousParts || previousParts.year !== parts.year || previousParts.month !== 1)
  const month = MONTH_SHORT_LABELS[parts.month - 1] || String(parts.month).padStart(2, '0')
  if (crossesYear || (spanDays > 120 && (isFirstTick || isYearStart))) {
    return String(parts.year)
  }
  if (spanDays <= 180) {
    return `${parts.month}/${parts.day}`
  }
  return month
}

function renderChartXAxisTick(
  tick: ChartAxisTick,
  previousTick: ChartAxisTick | null,
  x: number,
  geometry: ChartGeometry,
  spanDays: number,
  keyPrefix: string,
) {
  const label = formatChartAxisTickLabel(tick.date, spanDays, previousTick?.date || null)
  const y = getXAxisLabelY(geometry)
  const tickTopY = getXAxisTickTopY(geometry)
  return (
    <g key={`${keyPrefix}-${tick.date}`}>
      <line
        className="instrument-x-axis-tick"
        x1={x}
        y1={tickTopY}
        x2={x}
        y2={tickTopY + 6}
      />
      <text className="instrument-x-axis-label" x={x} y={y}>
        {label}
      </text>
    </g>
  )
}

function getChartTickDates(points: FundChartPoint[], count = 7) {
  if (!points.length) {
    return []
  }
  const sortedPoints = sortSeriesByDate(points)
  const datedPoints = sortedPoints
    .map((point) => {
      const parsed = parseChartDateParts(point.date)
      return parsed ? { point, time: parsed.time } : null
    })
    .filter((item): item is { point: FundChartPoint; time: number } => item !== null)
  if (!datedPoints.length) {
    return sortedPoints.slice(0, count)
  }
  const targetCount = Math.min(Math.max(count, 2), datedPoints.length)
  const first = datedPoints[0]
  const last = datedPoints[datedPoints.length - 1]
  const spanDays = Math.max(0, Math.round((last.time - first.time) / 86_400_000))
  const selected: FundChartPoint[] = []
  const seenDates = new Set<string>()
  for (let index = 0; index < targetCount; index += 1) {
    const targetTime =
      first.time + ((last.time - first.time) / Math.max(targetCount - 1, 1)) * index
    const nearest = datedPoints.reduce((best, candidate) =>
      Math.abs(candidate.time - targetTime) < Math.abs(best.time - targetTime) ? candidate : best,
    )
    if (!seenDates.has(nearest.point.date)) {
      selected.push(nearest.point)
      seenDates.add(nearest.point.date)
    }
  }
  if (!seenDates.has(last.point.date)) {
    selected.push(last.point)
  }
  return selected
}

function getChartAxisTicks(points: FundChartPoint[], count = 7): ChartAxisTick[] {
  if (!points.length) {
    return []
  }
  const sortedPoints = sortSeriesByDate(points)
  const first = parseChartDateParts(sortedPoints[0].date)
  const last = parseChartDateParts(sortedPoints[sortedPoints.length - 1].date)
  if (!first || !last) {
    const targetCount = Math.min(Math.max(count, 2), sortedPoints.length)
    return Array.from({ length: targetCount }, (_, index) => {
      const pointIndex = Math.round((index / Math.max(targetCount - 1, 1)) * (sortedPoints.length - 1))
      return {
        date: sortedPoints[pointIndex].date,
        xRatio: targetCount <= 1 ? 0 : index / (targetCount - 1),
      }
    })
  }
  const targetCount = Math.min(Math.max(count, 2), Math.max(2, sortedPoints.length))
  return Array.from({ length: targetCount }, (_, index) => {
    const xRatio = targetCount <= 1 ? 0 : index / (targetCount - 1)
    return {
      date: formatUtcChartDate(first.time + (last.time - first.time) * xRatio),
      xRatio,
    }
  })
}

function getChartAxisTicksForWindow(
  startDate: string | null | undefined,
  endDate: string | null | undefined,
  count = 7,
): ChartAxisTick[] {
  const start = startDate ? parseChartDateParts(startDate) : null
  const end = endDate ? parseChartDateParts(endDate) : null
  if (!start || !end || end.time <= start.time) {
    return []
  }
  const spanDays = Math.max(1, Math.round((end.time - start.time) / 86_400_000))
  const targetCount = Math.min(Math.max(count, 2), spanDays + 1)
  return Array.from({ length: targetCount }, (_, index) => {
    const xRatio = index / Math.max(targetCount - 1, 1)
    return {
      date: formatUtcChartDate(start.time + (end.time - start.time) * xRatio),
      xRatio,
    }
  })
}

function buildChartBands(
  points: FundChartPoint[],
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
  segments = 10,
) {
  if (points.length < 2) {
    return []
  }
  const plottingWidth = geometry.width - geometry.paddingLeft - geometry.paddingRight
  const bandWidth = plottingWidth / segments
  return Array.from({ length: segments }, (_, index) => {
    if (index % 2 === 0) {
      return null
    }
    return {
      x: geometry.paddingLeft + bandWidth * index,
      width: bandWidth,
    }
  }).filter((band): band is { x: number; width: number } => band !== null)
}

function projectChartValue(
  value: number,
  min: number,
  max: number,
  geometry: ChartGeometry = PRIMARY_CHART_GEOMETRY,
) {
  const { height, paddingTop, paddingBottom } = geometry
  const range = max - min || 1
  const y =
    height -
    paddingBottom -
    ((value - min) / range) * (height - paddingTop - paddingBottom)
  return y
}

function buildDateScaledPositionedPoints(
  points: FundChartPoint[],
  min: number,
  max: number,
  geometry: ChartGeometry,
  startDate: string | null | undefined,
  endDate: string | null | undefined,
) {
  return getDateScaledProjectedPoints(points, geometry, min, max, startDate, endDate).map(
    ({ point, x, y }) => ({
      ...point,
      x,
      y,
    }),
  )
}

function findNearestChartPoint(points: FundChartPoint[], targetDate: string | undefined) {
  if (!points.length || !targetDate) {
    return null
  }
  const targetTime = new Date(`${targetDate}T00:00:00`).getTime()
  if (Number.isNaN(targetTime)) {
    return points[points.length - 1] ?? null
  }
  return points.reduce<FundChartPoint | null>((closest, point) => {
    if (!closest) {
      return point
    }
    const pointDistance = Math.abs(new Date(`${point.date}T00:00:00`).getTime() - targetTime)
    const closestDistance = Math.abs(new Date(`${closest.date}T00:00:00`).getTime() - targetTime)
    return pointDistance < closestDistance ? point : closest
  }, null)
}

function renderStackRows(items: Record<string, unknown>) {
  return Object.entries(items).map(([key, value]) => (
    <div key={key} className="stack-item">
      <span>{formatLabel(key)}</span>
      <strong>{getDisplayValue(value)}</strong>
    </div>
  ))
}

function getFrameworkValueList(values: Record<string, unknown>, key: string) {
  const value = values[key]
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean)
  }
  if (value == null) {
    return []
  }
  const text = String(value).trim()
  return text ? [text] : []
}

function formatFrameworkValue(value: unknown) {
  if (Array.isArray(value)) {
    const items = value.map((item) => String(item).trim()).filter(Boolean)
    return items.length ? items.join(', ') : '—'
  }
  if (value == null) {
    return '—'
  }
  if (typeof value === 'boolean') {
    return value ? 'Yes' : 'No'
  }
  const text = String(value).trim()
  return text || '—'
}

function getDefinitionRubricText(definition: InstrumentAttributeDefinition) {
  const rubric = definition.rubric_json || {}
  const parts = [
    definition.description,
    typeof rubric.summary === 'string' ? rubric.summary : '',
    typeof rubric.standard === 'string' ? rubric.standard : '',
  ]
    .map((value) => String(value || '').trim())
    .filter(Boolean)
  return parts.length ? parts.join(' ') : definition.attribute_key
}

function isFrameworkOptionSelected(
  attributeValues: InstrumentAttributeValuesResponse | null,
  definition: InstrumentAttributeDefinition,
  option: string,
) {
  if (!attributeValues) {
    return false
  }
  const values = getFrameworkValueList(attributeValues.values, definition.attribute_key)
  return values.includes(option)
}

function buildNextFrameworkValue(
  attributeValues: InstrumentAttributeValuesResponse | null,
  definition: InstrumentAttributeDefinition,
  option: string,
) {
  const currentValues = attributeValues
    ? getFrameworkValueList(attributeValues.values, definition.attribute_key)
    : []
  if (definition.data_type === 'multi_select') {
    return currentValues.includes(option)
      ? currentValues.filter((value) => value !== option)
      : [...currentValues, option]
  }
  return option
}

type AttributeFrameworkDomain = Extract<
  InstrumentAttributeDefinition['domain_code'],
  'research' | 'monitoring'
>

const ATTRIBUTE_DOMAIN_ORDER: AttributeFrameworkDomain[] = [
  'research',
]

const ATTRIBUTE_DOMAIN_META: Record<
  AttributeFrameworkDomain,
  {
    title: string
    note: string
    emptyState: string
  }
> = {
  research: {
    title: 'Qualitative Research Tags',
    note: '',
    emptyState: 'Complete fund taxonomy first to unlock category-specific research tags.',
  },
  monitoring: {
    title: 'Monitoring Assessment',
    note: '',
    emptyState: 'Complete fund taxonomy first to unlock category-specific monitoring labels.',
  },
}

const ATTRIBUTE_GROUP_LABELS: Record<string, string> = {
  overview_identity: 'Identity',
  research_coverage: 'Research Governance',
  research_edge: 'Edge & Philosophy',
  research_process: 'Process Repeatability',
  research_style: 'Style Tags',
  research_manager: 'People & Organization',
  research_risk: 'Risk Management',
  research_terms: 'Capacity, Liquidity & Terms',
  research_governance: 'Governance & Alignment',
  research_delivery: 'Historical Delivery',
  research_role: 'Portfolio Role',
  monitoring_risk: 'Risk Profile',
  monitoring_regime: 'Regime Fit',
  monitoring_operational: 'Operational Coverage',
  custom: 'Custom',
}

function hasAttributeValue(value: unknown) {
  if (Array.isArray(value)) {
    return value.some((item) => String(item ?? '').trim())
  }
  if (typeof value === 'boolean') {
    return true
  }
  if (value == null) {
    return false
  }
  return String(value).trim().length > 0
}

function definitionHasAssignedValue(
  values: Record<string, unknown>,
  definition: InstrumentAttributeDefinition,
) {
  return hasAttributeValue(values[definition.attribute_key])
}

function definitionMatchesApplicability(
  definition: InstrumentAttributeDefinition,
  values: Record<string, unknown>,
) {
  const applicabilityEntries = Object.entries(definition.applicability_json || {})
  if (!applicabilityEntries.length) {
    return true
  }
  return applicabilityEntries.every(([attributeKey, expectedValues]) => {
    if (!expectedValues.length) {
      return true
    }
    const currentValues = getFrameworkValueList(values, attributeKey)
    if (!currentValues.length) {
      return false
    }
    return expectedValues.some((candidate) => currentValues.includes(candidate))
  })
}

function isTaxonomyComplete(attributeValues: InstrumentAttributeValuesResponse | null) {
  return Boolean(
    hasAttributeValue(attributeValues?.taxonomy?.derived_values?.fund_regime) &&
      hasAttributeValue(attributeValues?.taxonomy?.derived_values?.fund_taxonomy_leaf),
  )
}

function buildAttributeFrameworkSections(
  attributeValues: InstrumentAttributeValuesResponse | null,
) {
  if (!attributeValues) {
    return []
  }

  const values = {
    ...(attributeValues.taxonomy?.derived_values || {}),
    ...(attributeValues.values || {}),
  }
  const classificationReady = isTaxonomyComplete(attributeValues)

  return ATTRIBUTE_DOMAIN_ORDER.map((domain) => {
    const definitions = [...attributeValues.definitions]
      .filter((definition) => definition.domain_code === domain)
      .filter((definition) => definition.attribute_key !== 'coverage_status')
      .filter(
        (definition) =>
          definitionMatchesApplicability(definition, values) ||
          definitionHasAssignedValue(values, definition),
      )
      .sort((left, right) => left.display_order - right.display_order || left.label.localeCompare(right.label))

    const groups = definitions.reduce<
      Array<{ groupCode: string; label: string; definitions: InstrumentAttributeDefinition[] }>
    >((items, definition) => {
      const groupCode = definition.group_code || 'custom'
      const current = items.find((item) => item.groupCode === groupCode)
      if (current) {
        current.definitions.push(definition)
        return items
      }
      return [
        ...items,
        {
          groupCode,
          label: ATTRIBUTE_GROUP_LABELS[groupCode] || formatLabel(groupCode),
          definitions: [definition],
        },
      ]
    }, [])

    const emptyState =
      classificationReady
        ? ATTRIBUTE_DOMAIN_META[domain].emptyState
        : domain === 'research'
          ? 'Complete fund taxonomy first to unlock category-specific research tags.'
          : 'Complete fund taxonomy first to unlock category-specific monitoring labels.'

    return {
      domain,
      title: ATTRIBUTE_DOMAIN_META[domain].title,
      note: ATTRIBUTE_DOMAIN_META[domain].note,
      emptyState,
      groups,
    }
  })
}

function EmptyPanel({ title, note }: { title: string; note: string }) {
  return (
    <section className="panel">
      <div className="instrument-section-header">
        <div>
          <div className="panel-title">{title}</div>
          <div className="instrument-section-title">{title}</div>
        </div>
      </div>
      <div className="instrument-placeholder">{note}</div>
    </section>
  )
}

type FundDetailPageProps = {
  fundId?: string
  detailKind?: DetailKind
  watchlistContext?: {
    watchlistId: string
    watchlistName?: string | null
  } | null
  corporateActions?: CorporateActionEvent[]
}

export default function FundDetailPage({
  fundId: propFundId,
  detailKind = 'fund',
  watchlistContext = null,
  corporateActions = [],
}: FundDetailPageProps = {}) {
  const { language } = useLanguage()
  const { fundId: routeFundId = 'fax' } = useParams()
  const fundId = propFundId || routeFundId
  const databaseDashboardUrl = `${PLATFORM_HOME_URL}/database-dashboard`
  const [bundle, setBundle] = useState<FundDetailBundle | null>(null)
  const [activeTab, setActiveTab] = useState<DetailTab>('overview')
  const [chartRange, setChartRange] = useState<ChartRange>('3Y')
  const [quoteBasis, setQuoteBasis] = useState<QuoteBasis>('nav_with_dividend')
  const [chartFrequency, setChartFrequency] = useState<ChartFrequency>('daily')
  const [selectedCurrency, setSelectedCurrency] = useState('USD')
  const [benchmarkFundId, setBenchmarkFundId] = useState('')
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkSearchFocused, setBenchmarkSearchFocused] = useState(false)
  const [benchmarkNavSeries, setBenchmarkNavSeries] = useState<FundNavSeriesResponse | null>(null)
  const [performanceMatrixMode, setPerformanceMatrixMode] =
    useState<PerformanceMatrixMode>('values')
  const [rollingRiskSettings, setRollingRiskSettings] = useState<WatchlistRollingRiskSettings>(
    () => loadWatchlistRollingRiskSettings(),
  )
  const rollingRiskWindowMonths = rollingRiskSettings.windowMonths
  const rollingRiskChartDisplayStyle = rollingRiskSettings.chartDisplayStyle
  const [riskSettingsOpen, setRiskSettingsOpen] = useState(false)
  const [quoteActionNotice, setQuoteActionNotice] = useState<string | null>(null)
  const [chartStartDate, setChartStartDate] = useState('')
  const [chartEndDate, setChartEndDate] = useState('')
  const [chartHoverIndex, setChartHoverIndex] = useState<number | null>(null)
  const [chartHoverPanel, setChartHoverPanel] = useState<ChartHoverPanel | null>(null)
  const [chartHoverCursor, setChartHoverCursor] = useState<ChartHoverCursor | null>(null)
  const [chartDisplayStyle, setChartDisplayStyle] = useState<ChartDisplayStyle>('mountain')
  const [chartScale, setChartScale] = useState<ChartScale>('linear')
  const [showDividendEvents, setShowDividendEvents] = useState(true)
  const [showTimelineNoteEvents, setShowTimelineNoteEvents] = useState(true)
  const [showDrawdownPanel, setShowDrawdownPanel] = useState(true)
  const [openQuoteChartMenu, setOpenQuoteChartMenu] = useState<QuoteChartMenu | null>(null)
  const [timelineNoteDraft, setTimelineNoteDraft] = useState<TimelineNoteDraft | null>(null)
  const [timelineNoteCaptureMode, setTimelineNoteCaptureMode] = useState(false)
  const [timelineNoteViewAnchorDate, setTimelineNoteViewAnchorDate] = useState<string | null>(null)
  const [chartTimelineNoteContextMenu, setChartTimelineNoteContextMenu] =
    useState<ChartTimelineNoteContextMenu | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [loadWarning, setLoadWarning] = useState<string | null>(null)
  const [detailBundleKey, setDetailBundleKey] = useState('')
  const [loadingSectionKeys, setLoadingSectionKeys] = useState<Set<string>>(() => new Set())
  const [sectionLoadErrors, setSectionLoadErrors] = useState<Partial<Record<DetailTab, string>>>({})
  const [sectionRetryToken, setSectionRetryToken] = useState(0)
  const [refreshToken, setRefreshToken] = useState(0)
  const [editingPeople, setEditingPeople] = useState(false)
  const [editingStrategy, setEditingStrategy] = useState(false)
  const [editingPriceSection, setEditingPriceSection] = useState<PriceEditSection | null>(null)
  const [editingDocuments, setEditingDocuments] = useState(false)
  const [editingResearchOverview, setEditingResearchOverview] = useState(false)
  const [peopleDraft, setPeopleDraft] = useState<PeopleDraft | null>(null)
  const [strategyDraft, setStrategyDraft] = useState<StrategyDraft | null>(null)
  const [priceDraft, setPriceDraft] = useState<PriceDraft | null>(null)
  const [documentsDraft, setDocumentsDraft] = useState<DocumentsDraft | null>(null)
  const [documentUploadFile, setDocumentUploadFile] = useState<File | null>(null)
  const [documentUploadTitle, setDocumentUploadTitle] = useState('')
  const [documentUploadType, setDocumentUploadType] = useState('')
  const [documentUploadAsOfDate, setDocumentUploadAsOfDate] = useState('')
  const [documentUploadNotes, setDocumentUploadNotes] = useState('')
  const [uploadingDocument, setUploadingDocument] = useState(false)
  const [researchDraft, setResearchDraft] = useState<ResearchDraft | null>(null)
  const [manualRatingDraft, setManualRatingDraft] = useState<number | null>(null)
  const [manualRatingDirty, setManualRatingDirty] = useState(false)
  const [savingSection, setSavingSection] = useState<string | null>(null)
  const [sectionNotice, setSectionNotice] = useState<string | null>(null)
  const [sectionError, setSectionError] = useState<string | null>(null)
  const [settingsModalOpen, setSettingsModalOpen] = useState(false)
  const [taxonomyTree, setTaxonomyTree] = useState<FundTaxonomyTreeResponse | null>(null)
  const [taxonomyDraftNodeId, setTaxonomyDraftNodeId] = useState('')
  const quoteChartMenuRef = useRef<HTMLDivElement | null>(null)
  const riskSettingsMenuRef = useRef<HTMLDivElement | null>(null)
  const productFrameworkPickerRef = useRef<HTMLDivElement | null>(null)
  const timelineNoteContextMenuRef = useRef<HTMLDivElement | null>(null)
  const detailBundleKeyRef = useRef('')
  const detailRequestCoordinatorRef = useRef(createDetailRequestCoordinator(''))

  function closeSettingsDialog() {
    if (savingSection !== 'fund_settings') {
      setSettingsModalOpen(false)
    }
  }

  function closeTimelineNoteDialog() {
    if (savingSection !== 'timeline_note') {
      setTimelineNoteDraft(null)
    }
  }

  const settingsDialogRef = useModalDialog(settingsModalOpen, closeSettingsDialog)
  const timelineNoteDialogRef = useModalDialog(Boolean(timelineNoteDraft), closeTimelineNoteDialog)
  const [productFrameworkAttributes, setProductFrameworkAttributes] =
    useState<InstrumentAttributeValuesResponse | null>(null)
  const [productFrameworkLoadError, setProductFrameworkLoadError] = useState<string | null>(null)
  const [productFrameworkRetryToken, setProductFrameworkRetryToken] = useState(0)
  const [productFrameworkSavingKey, setProductFrameworkSavingKey] = useState<string | null>(null)
  const [openProductFrameworkPickerKey, setOpenProductFrameworkPickerKey] =
    useState<string | null>(null)
  const deferredBenchmarkSearch = useDeferredValue(benchmarkSearch)

  useEffect(() => {
    if (!sectionNotice) {
      return undefined
    }
    const timeoutId = window.setTimeout(() => setSectionNotice(null), 2800)
    return () => window.clearTimeout(timeoutId)
  }, [sectionNotice])

  useEffect(() => {
    function handlePointerDown(event: PointerEvent) {
      if (!quoteChartMenuRef.current?.contains(event.target as Node)) {
        setOpenQuoteChartMenu(null)
      }
      if (!riskSettingsMenuRef.current?.contains(event.target as Node)) {
        setRiskSettingsOpen(false)
      }
      if (!productFrameworkPickerRef.current?.contains(event.target as Node)) {
        setOpenProductFrameworkPickerKey(null)
      }
      if (!timelineNoteContextMenuRef.current?.contains(event.target as Node)) {
        setChartTimelineNoteContextMenu(null)
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [])

  useEffect(() => {
    saveWatchlistRollingRiskSettings(rollingRiskSettings)
  }, [rollingRiskSettings])

  useEffect(() => {
    let cancelled = false
    const previousBundle = bundle?.summary.fund_id === fundId ? bundle : null
    const bundleKey = `${detailKind}:${fundId}:${refreshToken}`
    const requestCoordinator = createDetailRequestCoordinator(bundleKey)
    detailBundleKeyRef.current = ''
    detailRequestCoordinatorRef.current = requestCoordinator
    setDetailBundleKey('')
    setLoadingSectionKeys(new Set())
    setSectionLoadErrors({})

    async function loadFund() {
      if (!previousBundle) {
        setBundle(null)
        setLoading(true)
      }
      setError(null)
      setLoadWarning(null)

      try {
        const results = await Promise.allSettled([
          getInstrumentSummary(fundId),
          getInstrumentLibrary(),
          getInstrumentNavSeries(fundId),
          detailKind === 'index'
            ? Promise.resolve(defaultFundResearchResponse())
            : getInstrumentResearch(fundId),
        ] as const)
        const [
          summaryResult,
          libraryResult,
          navSeriesResult,
          researchResult,
        ] = results

        if (cancelled) {
          return
        }

        const summary =
          summaryResult.status === 'fulfilled' ? summaryResult.value : previousBundle?.summary
        if (!summary) {
          throw summaryResult.status === 'rejected'
            ? summaryResult.reason
            : new Error('Failed to load instrument summary.')
        }

        const failedSections = rejectedLabels(results, [
          'summary',
          'library',
          'NAV series',
          'research timeline',
        ])

        const nextTabs = normalizeTabs(summary.tabs || [], detailKind)

        detailBundleKeyRef.current = bundleKey
        setBundle({
          summary: { ...summary, tabs: nextTabs },
          library: settledValue(libraryResult, previousBundle?.library ?? []),
          performance: previousBundle?.performance ?? defaultFundPerformanceResponse(),
          risk: previousBundle?.risk ?? defaultFundRiskResponse(),
          portfolio: previousBundle?.portfolio ?? defaultFundPortfolioResponse(),
          holdings: previousBundle?.holdings ?? defaultFundPortfolioHoldingsResponse(),
          ratings: previousBundle?.ratings ?? defaultFundRatingsResponse(summary),
          people: previousBundle?.people ?? defaultFundPeopleResponse(),
          strategy: previousBundle?.strategy ?? defaultFundStrategyResponse(),
          price: previousBundle?.price ?? defaultFundPriceResponse(),
          documents: previousBundle?.documents ?? defaultFundDocumentsResponse(),
          research: settledValue(
            researchResult,
            previousBundle?.research ?? defaultFundResearchResponse(),
          ),
          navSeries: settledValue(
            navSeriesResult,
            previousBundle?.navSeries ?? defaultFundNavSeriesResponse(fundId),
          ),
        })
        setDetailBundleKey(bundleKey)
        setLoadWarning(
          failedSections.length
            ? `Some sections could not be refreshed (${failedSections.join(', ')}). Available data remains usable.`
            : null,
        )
        startTransition(() => {
          setActiveTab((current) => (nextTabs.includes(current) ? current : nextTabs[0] || 'overview'))
        })
      } catch (loadError) {
        if (!cancelled) {
          const message = loadError instanceof Error ? loadError.message : 'Failed to load detail.'
          if (previousBundle) {
            setLoadWarning(`Refresh failed. Showing the last available data. ${message}`)
          } else {
            setError(message)
          }
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void loadFund()

    return () => {
      cancelled = true
      if (detailRequestCoordinatorRef.current === requestCoordinator) {
        detailRequestCoordinatorRef.current = createDetailRequestCoordinator(`${bundleKey}:invalidated`)
        detailBundleKeyRef.current = ''
      }
    }
  }, [detailKind, fundId, refreshToken])

  useEffect(() => {
    const bundleKey = `${detailKind}:${fundId}:${refreshToken}`
    if (
      !bundle ||
      detailBundleKey !== bundleKey ||
      detailBundleKeyRef.current !== bundleKey ||
      activeTab === 'overview'
    ) {
      return undefined
    }
    const loadKey = `${bundleKey}:${activeTab}`

    type LazyRequest = {
      key: keyof FundDetailBundle
      label: string
      load: () => Promise<unknown>
    }
    let requests: LazyRequest[] = []
    if (activeTab === 'performance') {
      requests = [{ key: 'performance', label: 'performance', load: () => getInstrumentPerformance(fundId) }]
    } else if (activeTab === 'risk') {
      requests = [{ key: 'risk', label: 'risk', load: () => getInstrumentRisk(fundId) }]
    } else if (activeTab === 'price') {
      requests = [{ key: 'price', label: 'price', load: () => getInstrumentPrice(fundId) }]
    } else if (activeTab === 'exposure') {
      requests = [
        { key: 'portfolio', label: 'exposure summary', load: () => getInstrumentPortfolioSummary(fundId) },
        { key: 'holdings', label: 'holdings', load: () => getInstrumentPortfolioHoldings(fundId) },
      ]
    } else if (activeTab === 'people') {
      requests = [{ key: 'people', label: 'people', load: () => getInstrumentPeople(fundId) }]
    } else if (activeTab === 'strategy') {
      requests = [{ key: 'strategy', label: 'strategy', load: () => getInstrumentStrategy(fundId) }]
    } else if (activeTab === 'documents') {
      requests = [{ key: 'documents', label: 'documents', load: () => getInstrumentDocuments(fundId) }]
    } else if (activeTab === 'monitoring') {
      requests = [
        { key: 'performance', label: 'performance', load: () => getInstrumentPerformance(fundId) },
        { key: 'risk', label: 'risk', load: () => getInstrumentRisk(fundId) },
        { key: 'portfolio', label: 'exposure summary', load: () => getInstrumentPortfolioSummary(fundId) },
        { key: 'ratings', label: 'ratings', load: () => getInstrumentRatings(fundId) },
      ]
    }
    if (!requests.length) {
      return undefined
    }

    const coordinator = detailRequestCoordinatorRef.current
    const requestToken = beginDetailRequest(coordinator, loadKey)
    if (!requestToken) {
      return undefined
    }
    const activeRequest = requestToken

    setLoadingSectionKeys((current) => new Set(current).add(loadKey))
    setSectionLoadErrors((current) => {
      if (!current[activeTab]) {
        return current
      }
      const next = { ...current }
      delete next[activeTab]
      return next
    })

    async function loadSection() {
      const results = await Promise.allSettled(requests.map((request) => request.load()))
      const failedSections = rejectedLabels(results, requests.map((request) => request.label))
      const accepted = completeDetailRequest(
        coordinator,
        activeRequest,
        failedSections.length === 0,
      )
      if (
        !accepted ||
        detailRequestCoordinatorRef.current !== coordinator ||
        detailBundleKeyRef.current !== bundleKey
      ) {
        return
      }

      const updates: Partial<FundDetailBundle> = {}
      results.forEach((result, index) => {
        if (result.status === 'fulfilled') {
          Object.assign(updates, { [requests[index].key]: result.value })
        }
      })
      setBundle((current) => (current ? { ...current, ...updates } : current))
      setSectionLoadErrors((current) => {
        const next = { ...current }
        if (failedSections.length) {
          next[activeTab] =
            `Could not load ${failedSections.join(', ')}. Existing data remains visible; retry when the service is available.`
        } else {
          delete next[activeTab]
        }
        return next
      })
    }

    void loadSection().finally(() => {
      if (detailRequestCoordinatorRef.current === coordinator) {
        setLoadingSectionKeys((current) => {
          if (!current.has(loadKey)) {
            return current
          }
          const next = new Set(current)
          next.delete(loadKey)
          return next
        })
      }
    })
    return undefined
  }, [activeTab, detailBundleKey, detailKind, fundId, refreshToken, sectionRetryToken])

  useEffect(() => {
    setTimelineNoteDraft(null)
    setTimelineNoteCaptureMode(false)
    setTimelineNoteViewAnchorDate(null)
    setChartTimelineNoteContextMenu(null)
  }, [fundId])

  useEffect(() => {
    let cancelled = false
    setProductFrameworkAttributes(null)
    setProductFrameworkLoadError(null)
    setOpenProductFrameworkPickerKey(null)

    async function loadProductFramework() {
      try {
        const response = await getInstrumentAttributes(fundId)
        if (!cancelled) {
          setProductFrameworkAttributes(response)
        }
      } catch (loadError) {
        if (!cancelled) {
          setProductFrameworkLoadError(
            loadError instanceof Error
              ? loadError.message
              : 'The product framework service is unavailable.',
          )
        }
      }
    }

    void loadProductFramework()

    return () => {
      cancelled = true
    }
  }, [fundId, productFrameworkRetryToken, refreshToken])

  useEffect(() => {
    const assignedNodeId =
      productFrameworkAttributes?.taxonomy?.assigned_node_id ||
      bundle?.summary.taxonomy?.assigned_node_id ||
      ''
    setTaxonomyDraftNodeId(assignedNodeId)
  }, [bundle?.summary.taxonomy?.assigned_node_id, productFrameworkAttributes?.taxonomy?.assigned_node_id])

  useEffect(() => {
    if (!settingsModalOpen || taxonomyTree) {
      return
    }
    let cancelled = false

    async function loadTaxonomyTree() {
      try {
        const response = await getFundTaxonomyTree()
        if (!cancelled) {
          setTaxonomyTree(response)
        }
      } catch (loadError) {
        if (!cancelled) {
          setSectionError(loadError instanceof Error ? loadError.message : 'Failed to load fund taxonomy.')
        }
      }
    }

    void loadTaxonomyTree()

    return () => {
      cancelled = true
    }
  }, [settingsModalOpen, taxonomyTree])

  useEffect(() => {
    if (!bundle) {
      return
    }
    setPeopleDraft(toEditablePeopleDraft(bundle.people))
    setStrategyDraft(toEditableStrategyDraft(bundle.strategy))
    setPriceDraft(toEditablePriceDraft(bundle.price))
    setDocumentsDraft(toEditableDocumentsDraft(bundle.documents))
    setResearchDraft((current) =>
      editingResearchOverview && current ? current : toEditableResearchDraft(bundle.research),
    )
    if (!manualRatingDirty) {
      setManualRatingDraft(parseManualRating(bundle.research.manual_rating))
    }
  }, [bundle, editingResearchOverview, manualRatingDirty])

  useEffect(() => {
    setDocumentUploadFile(null)
    setDocumentUploadTitle('')
    setDocumentUploadType('')
    setDocumentUploadAsOfDate('')
    setDocumentUploadNotes('')
    setManualRatingDirty(false)
  }, [fundId])

  useEffect(() => {
    if (!bundle) {
      return
    }
    const currencies = Array.from(
      new Set(
        bundle.navSeries.rows.map((row) => row.currency || '')
          .map((value) => value || '')
          .filter(Boolean),
      ),
    )
    const effectiveCurrency =
      currencies.includes(selectedCurrency) ? selectedCurrency : currencies[0] || 'USD'
    const quoteContext = buildQuoteSeriesContext(bundle.navSeries.rows, {
      currency: effectiveCurrency,
      requestedBasis: quoteBasis,
      preferredBasis: bundle.navSeries.nav_basis_type,
    })
    if (quoteContext.activeBasis && quoteContext.activeBasis !== quoteBasis) {
      setQuoteBasis(quoteContext.activeBasis)
    }
  }, [bundle, quoteBasis, selectedCurrency])

  useEffect(() => {
    setChartHoverIndex(null)
    setChartHoverPanel(null)
    setChartTimelineNoteContextMenu(null)
    setTimelineNoteViewAnchorDate(null)
  }, [
    chartRange,
    quoteBasis,
    chartFrequency,
    selectedCurrency,
    chartStartDate,
    chartEndDate,
    chartDisplayStyle,
    chartScale,
    showDividendEvents,
    showDrawdownPanel,
    fundId,
  ])

  useEffect(() => {
    if (!bundle) {
      return
    }
    const currencies = Array.from(
      new Set(
        bundle.navSeries.rows.map((row) => row.currency || '')
          .map((value) => value || '')
          .filter(Boolean),
      ),
    )
    if (currencies.length && !currencies.includes(selectedCurrency)) {
      setSelectedCurrency(currencies[0])
    }
    if (!currencies.length && selectedCurrency !== 'USD') {
      setSelectedCurrency('USD')
    }
  }, [bundle, selectedCurrency])

  useEffect(() => {
    let cancelled = false

    async function loadBenchmark() {
      if (!benchmarkFundId || benchmarkFundId === fundId) {
        setBenchmarkNavSeries(null)
        return
      }

      try {
        const response = await getInstrumentNavSeries(benchmarkFundId)
        if (!cancelled) {
          setBenchmarkNavSeries(response)
        }
      } catch {
        if (!cancelled) {
          setBenchmarkNavSeries(null)
        }
      }
    }

    void loadBenchmark()

    return () => {
      cancelled = true
    }
  }, [benchmarkFundId, fundId])

  useEffect(() => {
    setBenchmarkFundId('')
    setBenchmarkSearch('')
    setBenchmarkSearchFocused(false)
    setBenchmarkNavSeries(null)
    setQuoteActionNotice(null)
    setOpenQuoteChartMenu(null)
    setRiskSettingsOpen(false)
  }, [fundId])

  useEffect(() => {
    if (!bundle || chartRange === 'CUSTOM') {
      return
    }

    const currencies = Array.from(
      new Set(
        bundle.navSeries.rows.map((row) => row.currency || '')
          .map((value) => value || '')
          .filter(Boolean),
      ),
    )
    const effectiveCurrency =
      currencies.includes(selectedCurrency) ? selectedCurrency : currencies[0] || 'USD'
    const quoteContext = buildQuoteSeriesContext(bundle.navSeries.rows, {
      currency: effectiveCurrency,
      requestedBasis: quoteBasis,
      preferredBasis: bundle.navSeries.nav_basis_type,
    })
    const navBasisSeries = quoteContext.basisSeries
    const defaultWindow = getRangeWindow(navBasisSeries, chartRange)

    setChartStartDate(defaultWindow.start)
    setChartEndDate(defaultWindow.end)
  }, [bundle, chartRange, quoteBasis, selectedCurrency])

  async function handleSavePeople() {
    if (!peopleDraft) {
      return
    }
    setSavingSection('people')
    setSectionError(null)
    setSectionNotice(null)
    try {
      await updateInstrumentPeople(fundId, {
        payload: {
          overview: Object.fromEntries(
            peopleDraft.overviewRows
              .map((row) => [row.key.trim(), row.value.trim()] as const)
              .filter(([key, value]) => key && value),
          ),
          team: peopleDraft.teamRows
            .filter((row) => row.name.trim() || row.role.trim() || row.start_date.trim())
            .map((row) => ({
              name: row.name.trim(),
              role: row.role.trim(),
              start_date: row.start_date.trim() || null,
            })),
          notes: cleanListRows(peopleDraft.noteRows),
        },
        updated_by: 'terminal_ui',
      })
      setEditingPeople(false)
      setSectionNotice('People profile saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save people profile.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleSaveStrategy() {
    if (!strategyDraft) {
      return
    }
    setSavingSection('strategy')
    setSectionError(null)
    setSectionNotice(null)
    try {
      await updateInstrumentStrategy(fundId, {
        payload: {
          summary: strategyDraft.summary.trim(),
          investment_objective: strategyDraft.investment_objective.trim(),
          process_bullets: cleanListRows(strategyDraft.processRows),
          risk_controls: cleanListRows(strategyDraft.riskControlRows),
          notes: cleanListRows(strategyDraft.noteRows),
        },
        updated_by: 'terminal_ui',
      })
      setEditingStrategy(false)
      setSectionNotice('Strategy profile saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save strategy profile.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleSavePrice() {
    if (!priceDraft) {
      return
    }
    setSavingSection('price')
    setSectionError(null)
    setSectionNotice(null)
    try {
      await updateInstrumentPrice(fundId, {
        payload: {
          overview: Object.fromEntries(
            priceDraft.overviewRows
              .map((row) => [row.key.trim(), row.value.trim()] as const)
              .filter(([key]) => key),
          ),
          distribution_policy: priceDraft.distribution_policy.trim(),
          policy_text: priceDraft.policy_text.trim(),
          fee_notes: cleanListRows(priceDraft.feeNoteRows),
          notes: cleanListRows(priceDraft.noteRows),
        },
        updated_by: 'terminal_ui',
      })
      setEditingPriceSection(null)
      setSectionNotice('Price profile saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save price profile.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleSaveDocuments() {
    if (!documentsDraft) {
      return
    }
    setSavingSection('documents')
    setSectionError(null)
    setSectionNotice(null)
    try {
      await updateInstrumentDocuments(fundId, {
        payload: {
          current_documents: documentsDraft.currentDocumentRows
            .filter((row) =>
              row.title.trim() ||
              row.document_type.trim() ||
              row.as_of_date.trim() ||
              row.source.trim() ||
              row.status.trim() ||
              row.version_label.trim() ||
              row.file_name.trim() ||
              row.notes.trim(),
            )
            .map((row) => ({
              title: row.title.trim(),
              document_type: row.document_type.trim(),
              as_of_date: row.as_of_date.trim() || null,
              source: row.source.trim(),
              status: row.status.trim(),
              version_label: row.version_label.trim(),
              file_name: row.file_name.trim(),
              download_url: row.download_url.trim(),
              file_size: row.file_size.trim() || null,
              content_type: row.content_type.trim(),
              uploaded_at: row.uploaded_at.trim() || null,
              notes: row.notes.trim(),
              stored_file_name: row.stored_file_name.trim(),
            })),
          recent_imports: documentsDraft.importRows
            .filter((row) =>
              row.import_type.trim() ||
              row.received_at.trim() ||
              row.source.trim() ||
              row.status.trim() ||
              row.file_name.trim(),
            )
            .map((row) => ({
              import_type: row.import_type.trim(),
              received_at: row.received_at.trim() || null,
              source: row.source.trim(),
              status: row.status.trim(),
              file_name: row.file_name.trim(),
            })),
          extraction_reviews: documentsDraft.extractionRows
            .filter((row) =>
              row.document_title.trim() ||
              row.extract_type.trim() ||
              row.status.trim() ||
              row.adopted_version.trim() ||
              row.updated_at.trim(),
            )
            .map((row) => ({
              document_title: row.document_title.trim(),
              extract_type: row.extract_type.trim(),
              status: row.status.trim(),
              adopted_version: row.adopted_version.trim(),
              updated_at: row.updated_at.trim() || null,
            })),
          notes: cleanListRows(documentsDraft.noteRows),
        },
        updated_by: 'terminal_ui',
      })
      setEditingDocuments(false)
      setSectionNotice('Documents profile saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save documents profile.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleUploadDocument() {
    if (!documentUploadFile) {
      setSectionError(localize(language, SYSTEM_LABELS.pickFile))
      return
    }
    setUploadingDocument(true)
    setSectionError(null)
    setSectionNotice(null)
    try {
      const uploadedDocuments = await uploadInstrumentDocument(fundId, {
        file: documentUploadFile,
        title: documentUploadTitle.trim(),
        document_type: documentUploadType.trim(),
        as_of_date: documentUploadAsOfDate,
        source: 'manual_upload',
        status: 'uploaded',
        notes: documentUploadNotes.trim(),
        updated_by: 'terminal_ui',
      })
      setBundle((current) => (current ? { ...current, documents: uploadedDocuments } : current))
      setDocumentsDraft(toEditableDocumentsDraft(uploadedDocuments))
      setDocumentUploadFile(null)
      setDocumentUploadTitle('')
      setDocumentUploadType('')
      setDocumentUploadAsOfDate('')
      setDocumentUploadNotes('')
      setSectionNotice(localize(language, SYSTEM_LABELS.documentUploaded))
    } catch (uploadError) {
      setSectionError(uploadError instanceof Error ? uploadError.message : localize(language, SYSTEM_LABELS.uploadFailed))
    } finally {
      setUploadingDocument(false)
    }
  }

  async function handleSaveResearchOverview() {
    if (!bundle || !researchDraft) {
      return
    }
    setSavingSection('research_overview')
    setSectionError(null)
    setSectionNotice(null)
    try {
      const response = await updateInstrumentResearch(fundId, {
        payload: {
          overview: Object.fromEntries(
            researchDraft.overviewRows
              .map((row) => [row.key.trim(), row.value.trim()] as const)
              .filter(([key, value]) => key && value),
          ),
          manual_rating: parseManualRating(bundle.research.manual_rating),
          timeline_notes: normalizeResearchTimelineNotes(bundle.research.timeline_notes),
        },
        updated_by: 'terminal_ui',
      })
      setBundle((current) => (current ? { ...current, research: response } : current))
      setResearchDraft(toEditableResearchDraft(response))
      setEditingResearchOverview(false)
      setSectionNotice('Research view saved.')
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save research view.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleSaveManualRating() {
    if (!bundle) {
      return
    }
    setSavingSection('manual_rating')
    setSectionError(null)
    setSectionNotice(null)
    try {
      const response = await updateInstrumentResearch(fundId, {
        payload: {
          overview: bundle.research.overview || {},
          manual_rating: manualRatingDraft,
          timeline_notes: normalizeResearchTimelineNotes(bundle.research.timeline_notes),
        },
        updated_by: 'terminal_ui',
      })
      const normalizedRating = parseManualRating(response.manual_rating)
      setBundle((current) => (current ? { ...current, research: response } : current))
      setManualRatingDraft(normalizedRating)
      setManualRatingDirty(false)
      if (!editingResearchOverview) {
        setResearchDraft(toEditableResearchDraft(response))
      }
      setSectionNotice('Rating saved.')
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save rating.')
    } finally {
      setSavingSection(null)
    }
  }

  function buildResearchPayloadWithTimelineNotes(nextTimelineNotes: ResearchTimelineNote[]) {
    return {
      overview: bundle?.research.overview || {},
      manual_rating: bundle?.research.manual_rating ?? null,
      timeline_notes: nextTimelineNotes,
    }
  }

  function openTimelineNoteEditor(noteDate: string, note?: ResearchTimelineNote | null) {
    setTimelineNoteDraft(createTimelineNoteDraft(noteDate, note))
    setTimelineNoteViewAnchorDate(null)
    setChartTimelineNoteContextMenu(null)
    setTimelineNoteCaptureMode(false)
    setOpenQuoteChartMenu(null)
    setSectionError(null)
    setSectionNotice(null)
  }

  function focusTimelineNoteInQuote(noteDate: string) {
    setActiveTab('overview')
    setChartRange('MAX')
    setChartStartDate('')
    setChartEndDate('')
    setTimelineNoteViewAnchorDate(noteDate)
    setChartTimelineNoteContextMenu(null)
    setSectionError(null)
    setQuoteActionNotice(`Research note anchored to ${formatDate(noteDate)}.`)
  }

  async function handleSaveTimelineNote() {
    if (!bundle || !timelineNoteDraft) {
      return
    }
    const serializedNote = serializeTimelineNoteDraft(timelineNoteDraft)
    if (!serializedNote.note_date) {
      setSectionError('Timeline notes require a valid note date.')
      return
    }
    if (!serializedNote.title && !serializedNote.summary && !serializedNote.body) {
      setSectionError('Add at least a title, summary, or note body before saving.')
      return
    }

    setSavingSection('timeline_note')
    setSectionError(null)
    setSectionNotice(null)
    try {
      const nextNotes = [...timelineNotes.filter((note) => note.note_id !== serializedNote.note_id), serializedNote]
        .sort(sortResearchTimelineNotes)
      const response = await updateInstrumentResearch(fundId, {
        payload: buildResearchPayloadWithTimelineNotes(nextNotes),
        updated_by: 'terminal_ui',
      })
      const normalizedNotes = normalizeResearchTimelineNotes(response.timeline_notes)
      setBundle((current) => (current ? { ...current, research: response } : current))
      setResearchDraft((current) =>
        current
          ? {
              ...current,
              timelineNotes: normalizedNotes,
            }
          : current,
      )
      setTimelineNoteDraft(null)
      setTimelineNoteViewAnchorDate(
        findNearestChartPoint(visibleNavSeries, serializedNote.note_date)?.date || serializedNote.note_date,
      )
      setSectionNotice(`Saved note for ${formatDate(serializedNote.note_date)}.`)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save timeline note.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleDeleteTimelineNote(noteId: string) {
    if (!bundle) {
      return
    }
    setSavingSection('timeline_note')
    setSectionError(null)
    setSectionNotice(null)
    try {
      const nextNotes = timelineNotes.filter((note) => note.note_id !== noteId)
      const response = await updateInstrumentResearch(fundId, {
        payload: buildResearchPayloadWithTimelineNotes(nextNotes),
        updated_by: 'terminal_ui',
      })
      const normalizedNotes = normalizeResearchTimelineNotes(response.timeline_notes)
      setBundle((current) => (current ? { ...current, research: response } : current))
      setResearchDraft((current) =>
        current
          ? {
              ...current,
              timelineNotes: normalizedNotes,
            }
          : current,
      )
      setTimelineNoteDraft((current) => (current?.note_id === noteId ? null : current))
      setTimelineNoteViewAnchorDate((current) => {
        if (!current) {
          return current
        }
        const hasRemaining = normalizedNotes.some((note) => note.note_date === current)
        return hasRemaining ? current : null
      })
      setSectionNotice('Timeline note removed.')
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to delete timeline note.')
    } finally {
      setSavingSection(null)
    }
  }

  async function handleSaveProductFrameworkValue(
    definition: InstrumentAttributeDefinition,
    value: unknown,
    options?: { closePicker?: boolean },
  ) {
    setProductFrameworkSavingKey(definition.attribute_key)
    setSectionError(null)
    setSectionNotice(null)
    try {
      const response = await updateInstrumentAttributes(fundId, {
        values: [
          {
            attribute_key: definition.attribute_key,
            value,
          },
        ],
      })
      setProductFrameworkAttributes(response)
      setBundle((current) =>
        current
          ? {
              ...current,
              summary: {
                ...current.summary,
                instrument_attributes: {
                  ...current.summary.instrument_attributes,
                  ...response.values,
                },
              },
            }
          : current,
      )
      if (options?.closePicker !== false) {
        setOpenProductFrameworkPickerKey(null)
      }
    } catch (saveError) {
      setSectionError(
        saveError instanceof Error ? saveError.message : 'Failed to update product framework labels.',
      )
    } finally {
      setProductFrameworkSavingKey(null)
    }
  }

  async function handleSaveFundSettings() {
    setSavingSection('fund_settings')
    setSectionError(null)
    setSectionNotice(null)
    try {
      const response = await updateFundTaxonomy(fundId, {
        node_id: taxonomyDraftNodeId || null,
        updated_by: 'terminal_ui',
      })
      setProductFrameworkAttributes((current) =>
        current
          ? {
              ...current,
              taxonomy: response,
            }
          : current,
      )
      setBundle((current) =>
        current
          ? {
              ...current,
              summary: {
                ...current.summary,
                taxonomy: response,
              },
            }
          : current,
      )
      setSettingsModalOpen(false)
      setSectionNotice('Fund settings saved.')
      setRefreshToken((value) => value + 1)
    } catch (saveError) {
      setSectionError(saveError instanceof Error ? saveError.message : 'Failed to save fund settings.')
    } finally {
      setSavingSection(null)
    }
  }

  const benchmarkOptions = useMemo(
    () => (bundle?.library ?? []).filter((item) => item.fund_id !== fundId),
    [bundle?.library, fundId],
  )
  const selectedBenchmark = benchmarkOptions.find((item) => item.fund_id === benchmarkFundId) || null
  const selectedBenchmarkLabel = selectedBenchmark ? benchmarkLibraryLabel(selectedBenchmark) : ''
  const benchmarkInputValue = selectedBenchmark && !benchmarkSearch ? selectedBenchmarkLabel : benchmarkSearch
  const filteredBenchmarkOptions = useMemo(
    () => filterBenchmarkOptions(benchmarkOptions, deferredBenchmarkSearch),
    [benchmarkOptions, deferredBenchmarkSearch],
  )
  const showBenchmarkResults = benchmarkSearchFocused

  if (loading) {
    return (
      <div className="terminal-page">
        <LoadingOverlay label={detailKind === 'index' ? 'Loading index detail' : 'Loading fund detail'} />
      </div>
    )
  }

  if (error || !bundle) {
    return (
      <div className="terminal-page">
        <section className="panel">
          <div className="error-state">{error || 'Detail unavailable.'}</div>
        </section>
      </div>
    )
  }

  const { summary, performance, risk, portfolio, holdings, ratings, people, strategy, price, documents, research, navSeries } = bundle
  const timelineNotes = normalizeResearchTimelineNotes(research.timeline_notes)
  const availableCurrencies = Array.from(
    new Set(
      navSeries.rows.map((row) => row.currency || '')
        .map((value) => value || '')
        .filter(Boolean),
    ),
  )
  const effectiveCurrency = availableCurrencies.includes(selectedCurrency)
    ? selectedCurrency
    : availableCurrencies[0] || 'USD'
  const quoteSeriesContext = buildQuoteSeriesContext(navSeries.rows, {
    currency: effectiveCurrency,
    requestedBasis: quoteBasis,
    preferredBasis: navSeries.nav_basis_type,
  })
  const currencyFilteredRows = quoteSeriesContext.rows
  const availableQuoteBases = quoteSeriesContext.availableBases
  const activeQuoteBasis = quoteSeriesContext.activeBasis || resolvePreferredQuoteBasis(navSeries.nav_basis_type) || 'nav'
  const returnQuoteBasis = resolveReturnQuoteBasis(currencyFilteredRows, navSeries.nav_basis_type)
  const latestQuoteRow = quoteSeriesContext.latestRow || navSeries.rows[navSeries.rows.length - 1]
  const navBasisSeries = quoteSeriesContext.basisSeries
  const returnBasisSeries = returnQuoteBasis ? buildBasisSeries(currencyFilteredRows, returnQuoteBasis) : []
  const calculationFrequencyProfile = navSeries.calculation_frequency_profile
  const calculationFrequencyStatus = calculationFrequencyProfile.status_label
  // Metrics use the backend-selected calculation series, never the zoomed or downsampled chart display series.
  const calculationBasisSeries = buildCalculationPointSeries(navSeries.calculation_series)
  const defaultWindow = getRangeWindow(navBasisSeries, chartRange)
  const effectiveStartDate = chartRange === 'CUSTOM' ? chartStartDate : defaultWindow.start
  const effectiveEndDate = chartRange === 'CUSTOM' ? chartEndDate : defaultWindow.end
  const zoomMaxIndex = Math.max(navBasisSeries.length - 1, 0)
  const rawZoomStartIndex = effectiveStartDate
    ? findLastPointIndexOnOrBefore(navBasisSeries, effectiveStartDate)
    : 0
  const rawZoomEndIndex = effectiveEndDate
    ? findLastPointIndexOnOrBefore(navBasisSeries, effectiveEndDate)
    : zoomMaxIndex
  const zoomStartIndex = Math.max(0, Math.min(rawZoomStartIndex < 0 ? 0 : rawZoomStartIndex, zoomMaxIndex))
  const zoomEndIndex =
    zoomMaxIndex <= 0
      ? 0
      : Math.max(
          Math.min(zoomStartIndex + 1, zoomMaxIndex),
          Math.min(rawZoomEndIndex < 0 ? zoomMaxIndex : rawZoomEndIndex, zoomMaxIndex),
        )
  const canUseZoom = navBasisSeries.length > 2
  const zoomSelectionLeftPct = zoomMaxIndex > 0 ? (zoomStartIndex / zoomMaxIndex) * 100 : 0
  const zoomSelectionRightPct =
    zoomMaxIndex > 0 ? ((zoomMaxIndex - zoomEndIndex) / zoomMaxIndex) * 100 : 0
  const benchmarkRowsByCurrency =
    getRowsForCurrency(benchmarkNavSeries?.rows || [], effectiveCurrency)
  const benchmarkSourceRows = benchmarkRowsByCurrency.length > 0 ? benchmarkRowsByCurrency : benchmarkNavSeries?.rows || []
  const benchmarkAvailableBases = getAvailableQuoteBases(benchmarkSourceRows)
  const activeBenchmarkBasis = benchmarkAvailableBases.includes(activeQuoteBasis)
    ? activeQuoteBasis
    : benchmarkAvailableBases[0] || null
  const benchmarkNavBasisSeries = activeBenchmarkBasis ? buildBasisSeries(benchmarkSourceRows, activeBenchmarkBasis) : []
  const benchmarkCalculationSeries = benchmarkNavSeries
    ? buildCalculationPointSeries(benchmarkNavSeries.calculation_series)
    : []
  const hasBenchmarkSelection = Boolean(selectedBenchmark)
  const rawCompareDateWindow = hasBenchmarkSelection
    ? buildCommonDateWindow(navBasisSeries, benchmarkNavBasisSeries)
    : null
  const compareDateWindow = hasBenchmarkSelection
    ? resolveCommonChartWindow(rawCompareDateWindow, chartRange, chartStartDate, chartEndDate)
    : null
  const rawCommonNavSeries = compareDateWindow ? buildCommonWindowSeries(navBasisSeries, compareDateWindow) : []
  const rawCommonBenchmarkSeries = compareDateWindow
    ? buildCommonWindowSeries(benchmarkNavBasisSeries, compareDateWindow)
    : []
  const shouldIndexCompareSeries = Boolean(
    hasBenchmarkSelection &&
      compareDateWindow &&
      rawCommonNavSeries.length > 1 &&
      rawCommonBenchmarkSeries.length > 1,
  )
  const visibleNavSeries = shouldIndexCompareSeries
    ? resampleSeriesPreservingBounds(rawCommonNavSeries, chartFrequency)
    : resampleSeries(
        filterSeriesByDateWindow(navBasisSeries, effectiveStartDate, effectiveEndDate),
        chartFrequency,
      )
  const benchmarkVisibleNavSeries = shouldIndexCompareSeries
    ? resampleSeriesPreservingBounds(rawCommonBenchmarkSeries, chartFrequency)
    : []
  const indexedNavSeries = shouldIndexCompareSeries
    ? buildCommonRebasedSeries(visibleNavSeries, compareDateWindow, 1)
    : []
  const indexedBenchmarkSeries = shouldIndexCompareSeries
    ? buildCommonRebasedSeries(benchmarkVisibleNavSeries, compareDateWindow, 1)
    : []
  const chartNavSeries =
    shouldIndexCompareSeries && indexedNavSeries.length ? indexedNavSeries : visibleNavSeries
  const chartBenchmarkSeries = shouldIndexCompareSeries
    ? indexedBenchmarkSeries
    : []
  const chartDateWindow = shouldIndexCompareSeries && compareDateWindow
    ? compareDateWindow
    : {
        start: visibleNavSeries[0]?.date || effectiveStartDate,
        end: visibleNavSeries[visibleNavSeries.length - 1]?.date || effectiveEndDate,
      }
  const drawdownSourceSeries =
    shouldIndexCompareSeries && compareDateWindow
      ? buildCommonWindowSeries(visibleNavSeries, compareDateWindow)
      : visibleNavSeries
  const benchmarkDrawdownSourceSeries =
    shouldIndexCompareSeries && compareDateWindow
      ? buildCommonWindowSeries(benchmarkVisibleNavSeries, compareDateWindow)
      : benchmarkVisibleNavSeries
  const drawdownSeries = buildDrawdownSeries(drawdownSourceSeries)
  const benchmarkDrawdownSeries = buildDrawdownSeries(benchmarkDrawdownSourceSeries)
  const latestPoint = visibleNavSeries.length ? visibleNavSeries[visibleNavSeries.length - 1] : undefined
  const periodLow =
    visibleNavSeries.length > 0 ? Math.min(...visibleNavSeries.map((point) => point.value)) : null
  const periodHigh =
    visibleNavSeries.length > 0 ? Math.max(...visibleNavSeries.map((point) => point.value)) : null
  const maxDrawdown =
    drawdownSeries.length > 0 ? Math.min(...drawdownSeries.map((point) => point.value)) : null
  const chartQuotePeriodStats = getSeriesChangeStats(chartNavSeries)
  const chartBenchmarkPeriodStats = getSeriesChangeStats(chartBenchmarkSeries)
  const latestSeriesPoint = navBasisSeries[navBasisSeries.length - 1]
  const quoteLatestStats = getLatestPointChangeStats(navBasisSeries)
  const quoteChange = quoteLatestStats.change
  const quoteChangePct = quoteLatestStats.changePct
  const availableTabs = normalizeTabs(summary.tabs || [], detailKind)
  const detailPageLabel = localize(
    language,
    detailKind === 'index' ? SYSTEM_LABELS.indexDetail : SYSTEM_LABELS.fundDetail,
  )
  const navBasisType = navSeries.nav_basis_type || summary.nav_snapshot?.nav_basis_type || 'auto'
  const selectedSeriesLabel =
    navSeries.selected_series_label ||
    summary.nav_snapshot?.selected_series_label ||
    summary.selected_series?.label ||
    null
  const selectedDateLabel =
    navSeries.selected_date_label ||
    summary.nav_snapshot?.selected_date_label ||
    summary.selected_series?.date_label ||
    'Last Quote Date'
  const navBasisLabel = selectedSeriesLabel || (NAV_BASIS_LABELS[navBasisType]
    ? localize(language, NAV_BASIS_LABELS[navBasisType])
    : toTitleCase(navBasisType))
  const quoteBasisLabel = selectedSeriesLabel || localize(language, QUOTE_BASIS_LABELS[activeQuoteBasis])
  const chartSeriesBasisLabel = shouldIndexCompareSeries
    ? localize(language, SYSTEM_LABELS.indexed)
    : quoteBasisLabel
  const quoteBasisOptions = availableQuoteBases.length
    ? availableQuoteBases
    : [activeQuoteBasis]
  const basisValue =
    latestQuoteRow?.selected_value ??
    (activeQuoteBasis === 'nav_with_dividend'
      ? latestQuoteRow?.nav_with_dividend ??
        latestSeriesPoint?.value ??
        summary.nav_snapshot?.latest_nav_with_dividend ??
        latestQuoteRow?.nav
      : latestQuoteRow?.nav ??
        latestSeriesPoint?.value ??
        summary.nav_snapshot?.latest_nav ??
        latestQuoteRow?.nav_with_dividend)
  const quoteToneClass =
    quoteChange == null
      ? ''
      : quoteChange > 0
        ? 'instrument-quote-change instrument-quote-change-positive'
        : quoteChange < 0
        ? 'instrument-quote-change instrument-quote-change-negative'
        : 'instrument-quote-change instrument-quote-change-neutral'
  const managementStats = people.overview || {}
  const peoplePrimaryOverviewFacts = PEOPLE_PRIMARY_OVERVIEW_FIELDS.map((field) => ({
    ...field,
    value: managementStats[field.key],
  }))
  const peoplePrimaryOverviewRows = peoplePrimaryOverviewFacts.map((field) => ({
    key: field.key,
    label: field.label,
    value: formatResearchOverviewValue(field.key, field.value),
  }))
  const peopleAdditionalOverviewEntries = Object.entries(managementStats).filter(
    ([key]) => !PEOPLE_PRIMARY_OVERVIEW_FIELD_KEYS.has(key),
  )
  const peopleAdditionalOverviewRows = peopleAdditionalOverviewEntries.map(([key, value]) => ({
    key,
    label: formatLabel(key),
    value: getDisplayValue(value),
  }))
  const peopleAdditionalDraftRows =
    peopleDraft?.overviewRows.filter((row) => !PEOPLE_PRIMARY_OVERVIEW_FIELD_KEYS.has(row.key.trim())) ?? []
  const peopleManagementProfileFields = PEOPLE_PRIMARY_OVERVIEW_FIELDS.filter(
    (field) => field.key !== 'advisor' && field.key !== 'sub_advisor',
  )
  const peopleManagementProfileRows = peopleManagementProfileFields.map((field) => ({
    key: field.key,
    label: field.label,
    value: formatResearchOverviewValue(field.key, managementStats[field.key]),
  }))
  const formatProfileListValue = (items: string[]) =>
    items.length ? (
      <div className="instrument-profile-line-list">
        {items.map((item, index) => (
          <div key={`${item}-${index}`}>{item}</div>
        ))}
      </div>
    ) : (
      '—'
    )
  const formatProfileFactList = (rows: Array<{ label: string; value: string }>) => {
    const visibleRows = rows.filter((row) => row.value && row.value !== '—')
    return visibleRows.length ? (
      <div className="instrument-profile-fact-list">
        {visibleRows.map((row) => (
          <div key={row.label}>
            <span>{row.label}</span>
            <strong>{row.value}</strong>
          </div>
        ))}
      </div>
    ) : (
      '—'
    )
  }
  const formatPeopleTeamValue = (rows: Array<Record<string, unknown>>) => {
    const visibleRows = rows.filter(
      (row) => getString(row.name) || getString(row.role) || getString(row.start_date),
    )
    return visibleRows.length ? (
      <div className="instrument-profile-line-list instrument-profile-team-list">
        {visibleRows.map((row, index) => {
          const role = getString(row.role)
          const startDate = formatDate(row.start_date)
          const details = [role, startDate !== '—' ? `Start ${startDate}` : ''].filter(Boolean)
          return (
            <div key={`${getString(row.name) || 'team'}-${index}`}>
              <strong>{getString(row.name) || '—'}</strong>
              {details.length ? <span>{details.join(' · ')}</span> : null}
            </div>
          )
        })}
      </div>
    ) : (
      '—'
    )
  }
  const renderPeopleOverviewValue = (
    key: string,
    fallbackValue: string,
    type: 'date' | 'number' | 'text' = 'text',
    step?: string,
  ) =>
    editingPeople && peopleDraft ? (
      <input
        className="table-input"
        type={type === 'date' ? 'date' : type === 'number' ? 'number' : 'text'}
        step={step}
        value={getPeopleOverviewDraftValue(peopleDraft, key)}
        onChange={(event) =>
          setPeopleDraft((current) =>
            current
              ? {
                  ...current,
                  overviewRows: upsertKeyValueRows(current.overviewRows, key, event.target.value),
                }
              : current,
          )
        }
      />
    ) : (
      fallbackValue || '—'
    )
  const renderPeopleManagementProfileValue = () =>
    editingPeople && peopleDraft ? (
      <div className="instrument-profile-field-grid">
        {peopleManagementProfileFields.map((field) => (
          <label key={field.key} className="instrument-profile-field">
            <span>{field.label}</span>
            <input
              className="table-input"
              type={field.type === 'date' ? 'date' : field.type === 'number' ? 'number' : 'text'}
              step={field.key === 'number_of_managers' ? '1' : field.type === 'number' ? '0.1' : undefined}
              value={getPeopleOverviewDraftValue(peopleDraft, field.key)}
              onChange={(event) =>
                setPeopleDraft((current) =>
                  current
                    ? {
                        ...current,
                        overviewRows: upsertKeyValueRows(current.overviewRows, field.key, event.target.value),
                      }
                    : current,
                )
              }
            />
          </label>
        ))}
      </div>
    ) : (
      formatProfileFactList(peopleManagementProfileRows)
    )
  const renderPeopleAdditionalFieldsValue = () =>
    editingPeople && peopleDraft ? (
      <div className="instrument-profile-inline-editor">
        <table className="terminal-table terminal-table-compact instrument-data-table instrument-profile-inline-table">
          <thead>
            <tr>
              <th>Field Key</th>
              <th>Value</th>
              <th className="instrument-table-action-col">Action</th>
            </tr>
          </thead>
          <tbody>
            {peopleAdditionalDraftRows.length ? (
              peopleAdditionalDraftRows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <input
                      className="table-input"
                      value={row.key}
                      placeholder="field_key"
                      onChange={(event) =>
                        setPeopleDraft((current) =>
                          current
                            ? {
                                ...current,
                                overviewRows: current.overviewRows.map((item) =>
                                  item.id === row.id ? { ...item, key: event.target.value } : item,
                                ),
                              }
                            : current,
                        )
                      }
                    />
                  </td>
                  <td>
                    <input
                      className="table-input"
                      value={row.value}
                      placeholder="value"
                      onChange={(event) =>
                        setPeopleDraft((current) =>
                          current
                            ? {
                                ...current,
                                overviewRows: current.overviewRows.map((item) =>
                                  item.id === row.id ? { ...item, value: event.target.value } : item,
                                ),
                              }
                            : current,
                        )
                      }
                    />
                  </td>
                  <td className="instrument-table-row-action-cell">
                    <button
                      type="button"
                      className="table-action"
                      onClick={() =>
                        setPeopleDraft((current) =>
                          current
                            ? {
                                ...current,
                                overviewRows: current.overviewRows.filter((item) => item.id !== row.id),
                              }
                            : current,
                        )
                      }
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={3} className="empty-state">
                  No additional people fields.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="editor-actions">
          <button
            type="button"
            onClick={() =>
              setPeopleDraft((current) =>
                current
                  ? {
                      ...current,
                      overviewRows: [...current.overviewRows, { id: makeRowId('overview'), key: '', value: '' }],
                    }
                  : current,
              )
            }
          >
            Add Field
          </button>
        </div>
      </div>
    ) : (
      formatProfileFactList(peopleAdditionalOverviewRows)
    )
  const renderPeopleTeamValue = () =>
    editingPeople && peopleDraft ? (
      <div className="instrument-profile-inline-editor">
        <table className="terminal-table terminal-table-compact instrument-data-table instrument-profile-inline-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Role</th>
              <th>Start Date</th>
              <th className="instrument-table-action-col">Action</th>
            </tr>
          </thead>
          <tbody>
            {peopleDraft!.teamRows.length ? (
              peopleDraft!.teamRows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <input
                      className="table-input"
                      value={row.name}
                      onChange={(event) =>
                        setPeopleDraft((current) =>
                          current
                            ? {
                                ...current,
                                teamRows: current.teamRows.map((item) =>
                                  item.id === row.id ? { ...item, name: event.target.value } : item,
                                ),
                              }
                            : current,
                        )
                      }
                    />
                  </td>
                  <td>
                    <input
                      className="table-input"
                      value={row.role}
                      onChange={(event) =>
                        setPeopleDraft((current) =>
                          current
                            ? {
                                ...current,
                                teamRows: current.teamRows.map((item) =>
                                  item.id === row.id ? { ...item, role: event.target.value } : item,
                                ),
                              }
                            : current,
                        )
                      }
                    />
                  </td>
                  <td>
                    <input
                      className="table-input"
                      type="date"
                      value={row.start_date}
                      onChange={(event) =>
                        setPeopleDraft((current) =>
                          current
                            ? {
                                ...current,
                                teamRows: current.teamRows.map((item) =>
                                  item.id === row.id ? { ...item, start_date: event.target.value } : item,
                                ),
                              }
                            : current,
                        )
                      }
                    />
                  </td>
                  <td className="instrument-table-row-action-cell">
                    <button
                      type="button"
                      className="table-action"
                      onClick={() =>
                        setPeopleDraft((current) =>
                          current
                            ? {
                                ...current,
                                teamRows: current.teamRows.filter((item) => item.id !== row.id),
                              }
                            : current,
                        )
                      }
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={4} className="empty-state">
                  No management team rows yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="editor-actions">
          <button
            type="button"
            onClick={() =>
              setPeopleDraft((current) =>
                current
                  ? {
                      ...current,
                      teamRows: [...current.teamRows, { id: makeRowId('team'), name: '', role: '', start_date: '' }],
                    }
                  : current,
              )
            }
          >
            Add Team Member
          </button>
        </div>
      </div>
    ) : (
      formatPeopleTeamValue(people.team)
    )
  const renderStrategyTextValue = (
    value: string,
    onChange: (value: string) => void,
    fallbackValue: string,
    rows = 4,
  ) =>
    editingStrategy && strategyDraft ? (
      <textarea
        className="table-input instrument-data-table-textarea"
        rows={rows}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    ) : (
      fallbackValue || '—'
    )
  const getDocumentRecordText = (row: Record<string, unknown>, key: string) => {
    const value = row[key]
    return value == null ? '' : String(value)
  }
  const getDocumentRecordFileName = (row: Record<string, unknown>) =>
    getDocumentRecordText(row, 'file_name') || getDocumentRecordText(row, 'title') || '—'
  const getDocumentRecordNotes = (row: Record<string, unknown>) => getDocumentRecordText(row, 'notes')
  const currentDocumentRows = documents.current_documents || []
  const combinedVisibleSeries = [...chartNavSeries, ...chartBenchmarkSeries]
  const canUseLogarithmicScale =
    combinedVisibleSeries.length > 0 && combinedVisibleSeries.every((point) => point.value > 0)
  const effectiveChartScale =
    chartScale === 'logarithmic' && canUseLogarithmicScale
      ? chartScale
      : 'linear'
  const scaledVisibleSeries = applyChartScale(chartNavSeries, effectiveChartScale)
  const scaledBenchmarkVisibleSeries = applyChartScale(chartBenchmarkSeries, effectiveChartScale)
  const combinedScaledSeries = [...scaledVisibleSeries, ...scaledBenchmarkVisibleSeries]
  const chartWindowTickDates = getChartAxisTicksForWindow(chartDateWindow.start, chartDateWindow.end, 8)
  const chartTickDates =
    chartWindowTickDates.length ? chartWindowTickDates : getChartAxisTicks(chartNavSeries, 8)
  const chartTickSpanDays = getDateDifferenceInDays(chartDateWindow.start, chartDateWindow.end) ?? getChartDateSpanDays(chartNavSeries)
  const chartBands = buildChartBands(chartNavSeries, PRIMARY_CHART_GEOMETRY, 10)
  const rawChartMin = combinedScaledSeries.length ? Math.min(...combinedScaledSeries.map((point) => point.value)) : 0
  const rawChartMax = combinedScaledSeries.length ? Math.max(...combinedScaledSeries.map((point) => point.value)) : 1
  const chartRenderBounds = getPaddedAxisBounds(rawChartMin, rawChartMax, 0.045, 0.01)
  const chartMin = chartRenderBounds.min
  const chartMax = chartRenderBounds.max
  const chartLinePath = buildDateScaledLinePath(
    scaledVisibleSeries,
    PRIMARY_CHART_GEOMETRY,
    chartMin,
    chartMax,
    chartDateWindow.start,
    chartDateWindow.end,
  )
  const benchmarkChartLinePath = buildDateScaledLinePath(
    scaledBenchmarkVisibleSeries,
    PRIMARY_CHART_GEOMETRY,
    chartMin,
    chartMax,
    chartDateWindow.start,
    chartDateWindow.end,
  )
  const chartAreaPath =
    chartDisplayStyle === 'mountain'
      ? buildDateScaledAreaPath(
          scaledVisibleSeries,
          PRIMARY_CHART_GEOMETRY,
          chartMin,
          chartMax,
          chartDateWindow.start,
          chartDateWindow.end,
        )
      : ''
  const chartTickValues =
    effectiveChartScale === 'logarithmic'
      ? getLogTickValuesFromBounds(chartMin, chartMax, 5)
      : getLinearTickValues(chartMin, chartMax, 5)
  const combinedDrawdownSeries = [...drawdownSeries, ...benchmarkDrawdownSeries]
  const drawdownBounds = getDrawdownAxisBounds(combinedDrawdownSeries)
  const drawdownBands = buildChartBands(drawdownSeries, DRAWDOWN_CHART_GEOMETRY, 10)
  const drawdownMin = drawdownBounds.min
  const drawdownMax = drawdownBounds.max
  const drawdownTickValues =
    drawdownMin === drawdownMax
      ? [drawdownMin]
      : [drawdownMin, drawdownMax]
  const drawdownLinePath = buildDateScaledLinePath(
    drawdownSeries,
    DRAWDOWN_CHART_GEOMETRY,
    drawdownMin,
    drawdownMax,
    chartDateWindow.start,
    chartDateWindow.end,
  )
  const benchmarkDrawdownLinePath = buildDateScaledLinePath(
    benchmarkDrawdownSeries,
    DRAWDOWN_CHART_GEOMETRY,
    drawdownMin,
    drawdownMax,
    chartDateWindow.start,
    chartDateWindow.end,
  )
  const drawdownAreaPath = buildDateScaledAreaPath(
    drawdownSeries,
    DRAWDOWN_CHART_GEOMETRY,
    drawdownMin,
    drawdownMax,
    chartDateWindow.start,
    chartDateWindow.end,
    0,
  )
  const positionedChartPoints = buildDateScaledPositionedPoints(
    scaledVisibleSeries,
    chartMin,
    chartMax,
    PRIMARY_CHART_GEOMETRY,
    chartDateWindow.start,
    chartDateWindow.end,
  )
  const positionedBenchmarkPoints = buildDateScaledPositionedPoints(
    scaledBenchmarkVisibleSeries,
    chartMin,
    chartMax,
    PRIMARY_CHART_GEOMETRY,
    chartDateWindow.start,
    chartDateWindow.end,
  )
  const positionedDrawdownPoints = buildDateScaledPositionedPoints(
    drawdownSeries,
    drawdownMin,
    drawdownMax,
    DRAWDOWN_CHART_GEOMETRY,
    chartDateWindow.start,
    chartDateWindow.end,
  )
  const positionedBenchmarkDrawdownPoints = buildDateScaledPositionedPoints(
    benchmarkDrawdownSeries,
    drawdownMin,
    drawdownMax,
    DRAWDOWN_CHART_GEOMETRY,
    chartDateWindow.start,
    chartDateWindow.end,
  )
  const activeHoverIndex =
    chartHoverIndex == null || !scaledVisibleSeries.length
      ? null
      : Math.min(Math.max(chartHoverIndex, 0), scaledVisibleSeries.length - 1)
  const displayIndex =
    !scaledVisibleSeries.length ? null : activeHoverIndex ?? scaledVisibleSeries.length - 1
  const displayedNavPoint = displayIndex == null ? undefined : chartNavSeries[displayIndex]
  const displayedDrawdownPoint =
    displayIndex == null ? undefined : getPointAtDate(positionedDrawdownPoints, displayedNavPoint?.date) || undefined
  const primaryHoverGuideX = chartHoverCursor ? getPlotXFromRatio(PRIMARY_CHART_GEOMETRY, chartHoverCursor.xRatio) : null
  const drawdownHoverGuideX = chartHoverCursor ? getPlotXFromRatio(DRAWDOWN_CHART_GEOMETRY, chartHoverCursor.xRatio) : null
  const activeChartGuidePoint = activeHoverIndex == null ? null : positionedChartPoints[activeHoverIndex]
  const activeDrawdownGuidePoint =
    activeHoverIndex == null ? null : getPointAtDate(positionedDrawdownPoints, chartNavSeries[activeHoverIndex]?.date)
  const isPrimaryHoverActive = chartHoverPanel === 'primary' && chartHoverCursor != null && activeChartGuidePoint != null
  const isDrawdownHoverActive = chartHoverPanel === 'drawdown' && chartHoverCursor != null && activeDrawdownGuidePoint != null
  const hoveredChartPoint = isPrimaryHoverActive ? activeChartGuidePoint : null
  const hoveredDrawdownPoint = isDrawdownHoverActive ? activeDrawdownGuidePoint : null
  const hoveredNavPoint = activeHoverIndex == null ? null : chartNavSeries[activeHoverIndex]
  const displayedBenchmarkBasePoint = getPointAtOrNearestDate(
    chartBenchmarkSeries,
    displayedNavPoint?.date || chartNavSeries[chartNavSeries.length - 1]?.date,
  )
  const displayedBenchmarkDrawdownBasePoint = getPointAtOrNearestDate(
    benchmarkDrawdownSeries,
    displayedNavPoint?.date || chartNavSeries[chartNavSeries.length - 1]?.date,
  )
  const hoveredBenchmarkBasePoint = activeHoverIndex == null ? null : getPointAtOrNearestDate(
    chartBenchmarkSeries,
    hoveredNavPoint?.date,
  )
  const hoveredBenchmarkDrawdownBasePoint = activeHoverIndex == null ? null : getPointAtOrNearestDate(
    benchmarkDrawdownSeries,
    hoveredNavPoint?.date,
  )
  const hoveredBenchmarkPoint =
    hoveredBenchmarkBasePoint &&
    getPointAtDate(positionedBenchmarkPoints, hoveredBenchmarkBasePoint.date)
  const hoveredBenchmarkDrawdownPoint =
    hoveredBenchmarkDrawdownBasePoint &&
    getPointAtDate(positionedBenchmarkDrawdownPoints, hoveredBenchmarkDrawdownBasePoint.date)
  const distributionRows = [...currencyFilteredRows]
    .filter((row) => row.distribution_amount != null && row.distribution_amount !== 0)
    .filter((row) => (!effectiveStartDate || row.as_of_date >= effectiveStartDate) && (!effectiveEndDate || row.as_of_date <= effectiveEndDate))
    .sort((left, right) => right.as_of_date.localeCompare(left.as_of_date))
  const latestDistribution = distributionRows[0]
  const hoveredDistribution = activeHoverIndex == null ? null : distributionRows.find((row) => row.as_of_date === hoveredNavPoint?.date)
  const distributionMarkers = !showDividendEvents
    ? []
    : distributionRows
    .map((row) => {
      const point = positionedChartPoints.find((item) => item.date === row.as_of_date)
      if (!point) {
        return null
      }
      return {
        row,
        x: point.x,
      }
    })
    .filter((marker): marker is { row: FundNavSeriesResponse['rows'][number]; x: number } => marker !== null)
  const visibleTimelineNotes = !showTimelineNoteEvents
    ? []
    : timelineNotes.filter((note) => {
      const windowStart = visibleNavSeries[0]?.date
      const windowEnd = visibleNavSeries[visibleNavSeries.length - 1]?.date
      if (!windowStart || !windowEnd) {
        return false
      }
      return note.note_date >= windowStart && note.note_date <= windowEnd
    })
  const timelineNoteMarkerGroups = !showTimelineNoteEvents
    ? []
    : Array.from(
      visibleTimelineNotes.reduce(
        (
          groups,
          note,
        ) => {
          const anchorPoint = findNearestChartPoint(visibleNavSeries, note.note_date)
          if (!anchorPoint) {
            return groups
          }
          const positionedPoint = positionedChartPoints.find((point) => point.date === anchorPoint.date)
          if (!positionedPoint) {
            return groups
          }
          const existing = groups.get(anchorPoint.date)
          if (existing) {
            existing.notes.push(note)
            return groups
          }
          groups.set(anchorPoint.date, {
            anchorDate: anchorPoint.date,
            x: positionedPoint.x,
            notes: [note],
          })
          return groups
        },
        new Map<string, { anchorDate: string; x: number; notes: ResearchTimelineNote[] }>(),
      ).values(),
    ).sort((left, right) => left.anchorDate.localeCompare(right.anchorDate))
  const hoveredTimelineNoteGroup =
    hoveredNavPoint == null
      ? null
      : timelineNoteMarkerGroups.find((group) => group.anchorDate === hoveredNavPoint.date) || null
  const selectedTimelineNoteGroup =
    timelineNoteViewAnchorDate == null
      ? null
      : timelineNoteMarkerGroups.find((group) => group.anchorDate === timelineNoteViewAnchorDate) || null
  const timelineNoteContextMenuStyle = chartTimelineNoteContextMenu
    ? {
        left:
          typeof window === 'undefined'
            ? chartTimelineNoteContextMenu.clientX
            : Math.min(chartTimelineNoteContextMenu.clientX, window.innerWidth - 220),
        top:
          typeof window === 'undefined'
            ? chartTimelineNoteContextMenu.clientY
            : Math.min(chartTimelineNoteContextMenu.clientY, window.innerHeight - 160),
      }
    : undefined
  const hoverNavValue = displayedNavPoint?.value ?? null
  const hoverBenchmarkValue = displayedBenchmarkBasePoint?.value ?? null
  const hoverDrawdownValue = displayedDrawdownPoint?.value ?? null
  const hoverBenchmarkDrawdownValue = displayedBenchmarkDrawdownBasePoint?.value ?? null
  const latestChartPoint = positionedChartPoints[positionedChartPoints.length - 1] ?? null
  const latestBenchmarkChartPoint = positionedBenchmarkPoints[positionedBenchmarkPoints.length - 1] ?? null
  const latestDrawdownPoint = positionedDrawdownPoints[positionedDrawdownPoints.length - 1] ?? null
  const latestBenchmarkDrawdownPoint =
    positionedBenchmarkDrawdownPoints[positionedBenchmarkDrawdownPoints.length - 1] ?? null
  const latestChartValueLabel =
    chartNavSeries.length > 0 ? formatNumber(chartNavSeries[chartNavSeries.length - 1].value, 4) : null
  const latestBenchmarkChartValueLabel =
    chartBenchmarkSeries.length > 0
      ? formatNumber(chartBenchmarkSeries[chartBenchmarkSeries.length - 1].value, 4)
      : null
  const latestDrawdownValueLabel =
    drawdownSeries.length > 0 ? formatPercent(drawdownSeries[drawdownSeries.length - 1].value) : null
  const latestBenchmarkDrawdownValueLabel =
    benchmarkDrawdownSeries.length > 0
      ? formatPercent(benchmarkDrawdownSeries[benchmarkDrawdownSeries.length - 1].value)
      : null
  const latestChartValueTag = getChartValueTagLayout(
    latestChartPoint,
    PRIMARY_CHART_GEOMETRY,
    latestChartValueLabel,
  )
  const latestBenchmarkChartValueTag = getChartValueTagLayout(
    latestBenchmarkChartPoint,
    PRIMARY_CHART_GEOMETRY,
    latestBenchmarkChartValueLabel,
  )
  const latestDrawdownValueTag = getChartValueTagLayout(
    latestDrawdownPoint,
    DRAWDOWN_CHART_GEOMETRY,
    latestDrawdownValueLabel,
  )
  const latestBenchmarkDrawdownValueTag = getChartValueTagLayout(
    latestBenchmarkDrawdownPoint,
    DRAWDOWN_CHART_GEOMETRY,
    latestBenchmarkDrawdownValueLabel,
  )
  const chartClipId = `quote-chart-plot-${fundId.replace(/[^a-zA-Z0-9_-]/g, '-')}`
  const drawdownClipId = `quote-drawdown-plot-${fundId.replace(/[^a-zA-Z0-9_-]/g, '-')}`
  const primaryHoverPlotBounds = getChartPlotBounds(PRIMARY_CHART_GEOMETRY, CHART_CROSSHAIR_INSET)
  const drawdownHoverPlotBounds = getChartPlotBounds(DRAWDOWN_CHART_GEOMETRY, CHART_CROSSHAIR_INSET)
  const primaryTooltipAnchor = getChartTooltipAnchor(
    isPrimaryHoverActive ? chartHoverCursor : null,
    PRIMARY_CHART_GEOMETRY,
  )
  const drawdownTooltipAnchor = getChartTooltipAnchor(
    isDrawdownHoverActive ? chartHoverCursor : null,
    DRAWDOWN_CHART_GEOMETRY,
  )
  const priceOverviewMap = price.overview || {}
  const adjustedExpenseRatio = formatPriceOverviewValue(
    'adjusted_expense_ratio',
    priceOverviewMap.adjusted_expense_ratio,
  )
  const reportedExpenseRatio = formatPriceOverviewValue(
    'total_expense_ratio',
    priceOverviewMap.total_expense_ratio,
  )
  const feesAndTermsRows = [
    { label: 'Management Fee', value: formatPriceOverviewValue('management_fee', priceOverviewMap.management_fee) },
    {
      label: 'Interest Expense Fees',
      value: formatPriceOverviewValue('interest_expense_fees', priceOverviewMap.interest_expense_fees),
    },
    { label: 'Redemption Fee', value: formatPriceOverviewValue('redemption_fee', priceOverviewMap.redemption_fee) },
    {
      label: 'Minimum Initial Investment',
      value: formatPriceOverviewValue('minimum_initial_investment', priceOverviewMap.minimum_initial_investment),
    },
  ]
  const pricePolicyRows = [
    { label: 'Distribution Policy', value: getString(price.distribution_policy) },
    { label: 'Policy Text', value: getString(price.policy_text) },
  ]
  const isEditingPrice = editingPriceSection === 'table'
  const renderPriceOverviewValue = (key: string, fallbackValue: string) =>
    isEditingPrice && priceDraft ? (
      <input
        className="table-input"
        value={getOverviewDraftValue(priceDraft, key)}
        onChange={(event) =>
          setPriceDraft((current) =>
            current
              ? {
                  ...current,
                  overviewRows: current.overviewRows.map((row) =>
                    row.key === key ? { ...row, value: event.target.value } : row,
                  ),
                }
              : current,
          )
        }
      />
    ) : (
      fallbackValue || '—'
    )
  const renderPriceTextValue = (
    value: string,
    onChange: (value: string) => void,
    fallbackValue: string,
    rows = 3,
  ) =>
    isEditingPrice && priceDraft ? (
      <textarea
        className="table-input instrument-data-table-textarea"
        rows={rows}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    ) : (
      fallbackValue || '—'
    )
  const formatPriceListValue = (items: string[]) =>
    items.length ? (
      <div className="instrument-price-line-list">
        {items.map((item) => (
          <div key={item}>{item}</div>
        ))}
      </div>
    ) : (
      '—'
    )
  const latestNavRecord = navSeries.rows[navSeries.rows.length - 1]
  const navRefreshStatus = navSeries.refresh_status || null
  const monitoringOverviewRows = [
    {
      label: 'Freshness Status',
      value: formatMonitoringStatus(summary.freshness.data_freshness_status),
      tone: getMonitoringStatusTone(summary.freshness.data_freshness_status),
    },
    {
      label: 'Coverage Status',
      value: getString(summary.instrument_attributes.coverage_status),
      tone: 'status-attribute',
    },
    { label: 'Last Fact Update', value: formatDateTime(summary.freshness.last_fact_update_at), tone: null },
    { label: 'Last Recalculated', value: formatDateTime(summary.freshness.last_recalculated_at), tone: null },
    {
      label: 'Last Snapshot',
      value: formatDateTime(summary.freshness.last_successful_snapshot_at),
      tone: null,
    },
    {
      label: selectedDateLabel,
      value: formatDate(latestNavRecord?.as_of_date || null),
      tone: null,
    },
    {
      label: 'Refresh Owner',
      value: 'Database Dashboard',
      tone: 'status-attribute',
    },
    {
      label: 'Last Update Trigger',
      value: formatDateTime(navRefreshStatus?.requested_at || null),
      tone: null,
    },
  ]
  const monitoringPipelineRows = [
    {
      domain: `${quoteBasisLabel} Series`,
      asOf: formatDate(latestNavRecord?.as_of_date || null),
      cutoff: formatDateTime(navRefreshStatus?.requested_at || latestNavRecord?.adopted_at || summary.freshness.last_fact_update_at),
      methodology: formatNavBasisSource(navSeries.nav_basis_source),
      status: formatMonitoringStatus(navRefreshStatus?.status || navSeries.nav_basis_status || summary.freshness.data_freshness_status),
      tone: getMonitoringStatusTone(navRefreshStatus?.status || navSeries.nav_basis_status || summary.freshness.data_freshness_status),
    },
    {
      domain: 'Performance Snapshot',
      asOf: formatDate(performance.snapshot_metadata?.as_of_date || null),
      cutoff: formatDateTime(performance.snapshot_metadata?.source_cutoff_at || null),
      methodology: getString(performance.snapshot_metadata?.methodology_version),
      status: performance.snapshot_metadata?.as_of_date ? 'Current' : 'Pending',
      tone: performance.snapshot_metadata?.as_of_date ? 'status-fresh' : 'status-pending',
    },
    {
      domain: 'Risk Snapshot',
      asOf: formatDate(risk.snapshot_metadata?.as_of_date || null),
      cutoff: formatDateTime(risk.snapshot_metadata?.source_cutoff_at || null),
      methodology: getString(risk.snapshot_metadata?.methodology_version),
      status: risk.snapshot_metadata?.as_of_date ? 'Current' : 'Pending',
      tone: risk.snapshot_metadata?.as_of_date ? 'status-fresh' : 'status-pending',
    },
    {
      domain: 'Portfolio Snapshot',
      asOf: formatDate(portfolio.snapshot_metadata?.as_of_date || null),
      cutoff: formatDateTime(portfolio.snapshot_metadata?.source_cutoff_at || null),
      methodology: getString(portfolio.snapshot_metadata?.methodology_version),
      status: portfolio.snapshot_metadata?.as_of_date ? 'Current' : 'Pending',
      tone: portfolio.snapshot_metadata?.as_of_date ? 'status-fresh' : 'status-pending',
    },
    {
      domain: 'Ratings',
      asOf: formatDate(summary.rating_as_of),
      cutoff: '—',
      methodology: getString(ratings.methodology_version),
      status: summary.rating_as_of ? 'Current' : 'Pending',
      tone: summary.rating_as_of ? 'status-fresh' : 'status-pending',
    },
  ]
  const productFrameworkSections = buildAttributeFrameworkSections(productFrameworkAttributes)

  const monitoringAlertRows = [
    ...(navRefreshStatus?.message ? [navRefreshStatus.message] : []),
    ...(summary.freshness.staleness_reason ? [summary.freshness.staleness_reason] : []),
    ...summary.quick_monitoring_items,
  ]
  const navSnapshotRows = [
    { label: 'Selected Basis', value: quoteBasisLabel },
    { label: 'Research Basis', value: navBasisLabel },
    { label: 'Calculation Basis', value: calculationFrequencyStatus },
    {
      label: 'Calc Observations',
      value: formatNumber(calculationFrequencyProfile.observation_count, 0),
    },
    {
      label: 'Annualization',
      value:
        calculationFrequencyProfile.annualization_periods_per_year == null
          ? '—'
          : `${formatNumber(calculationFrequencyProfile.annualization_periods_per_year, 1)} / yr`,
    },
    {
      label: 'Gaps',
      value: `${formatNumber(calculationFrequencyProfile.gap_count, 0)} gaps`,
    },
    { label: 'Basis Source', value: formatNavBasisSource(navSeries.nav_basis_source) },
    { label: 'Series Count', value: String(navSeries.count || navSeries.rows.length || 0) },
    { label: selectedDateLabel.replace(/^Last\\s+/, ''), value: formatDate(latestQuoteRow?.as_of_date || navSeries.rows[navSeries.rows.length - 1]?.as_of_date) },
    { label: 'Currency', value: getString(navSeries.rows[navSeries.rows.length - 1]?.currency || 'USD') },
  ]
  const distributionRowsSummary = [
    {
      label: 'Latest Distribution',
      value: latestDistribution ? formatNumber(latestDistribution.distribution_amount, 4) : '—',
    },
    {
      label: 'Latest Distribution Date',
      value: latestDistribution ? formatDate(latestDistribution.as_of_date) : '—',
    },
    {
      label: 'Cumulative Distribution',
      value: latestDistribution ? formatNumber(latestDistribution.cumulative_distribution, 4) : '—',
    },
    {
      label: 'Adopted At',
      value: latestDistribution ? formatDateTime(latestDistribution.adopted_at) : '—',
    },
  ]
  const performanceReferenceEndDate = calculationBasisSeries[calculationBasisSeries.length - 1]?.date || null
  const performancePeriodSnapshots = PERFORMANCE_METRIC_PERIODS.map((period) => {
    const fundWindow = getAnchoredWindow(calculationBasisSeries, period.key, performanceReferenceEndDate)
    const benchmarkWindow = getAnchoredWindow(
      benchmarkCalculationSeries,
      period.key,
      performanceReferenceEndDate,
    )
    return {
      ...period,
      fund: buildPerformanceMetricSnapshot(fundWindow),
      benchmark: benchmarkWindow.length >= 2 ? buildPerformanceMetricSnapshot(benchmarkWindow) : null,
      relative:
        benchmarkWindow.length >= 2 ? buildPerformanceRelativeSnapshot(fundWindow, benchmarkWindow) : null,
    }
  })
  const monthlyReturnMatrixRows = buildMonthlyReturnMatrix(calculationBasisSeries)
  const monthlyReturnMatrixMaxAbs = monthlyReturnMatrixRows.reduce((maxAbs, row) => {
    const rowMax = Math.max(
      ...[...row.months, row.ytd]
        .filter((value): value is number => value != null)
        .map((value) => Math.abs(value)),
      0,
    )
    return Math.max(maxAbs, rowMax)
  }, 0)
  const peerComparison = performance.peer_comparison?.status === 'ready' ? performance.peer_comparison : null
  const peerComparisonPathLabel =
    peerComparison?.peer_path?.filter(Boolean).join(' / ') ||
    performance.ranking?.peer_group ||
    'Taxonomy peers'
  const peerComparisonMetricByKey = new Map(
    (peerComparison?.metrics || []).map((metric) => [metric.metric_key, metric]),
  )
  const activePerformanceMatrixMode = peerComparison ? performanceMatrixMode : 'values'
  const formatRecoveryValue = (snapshot: PerformanceMetricSnapshot | null) => {
    if (!snapshot || snapshot.maxDrawdown == null) {
      return null
    }
    if (snapshot.maxDrawdown === 0) {
      return '0 d'
    }
    if (snapshot.recoveryOpen) {
      return 'Open'
    }
    if (snapshot.recoveryDays == null) {
      return '—'
    }
    return `${formatNumber(snapshot.recoveryDays, 0)} d`
  }
  const benchmarkMetricPrefix = selectedBenchmark ? 'BM' : null
  const buildBenchmarkNote = (value: string | null) =>
    benchmarkMetricPrefix && value ? `${benchmarkMetricPrefix} ${value}` : null
  const buildPeerPerformanceMatrixCell = (
    rowKey: PerformanceMatrixRowKey,
    periodKey: PerformanceMetricPeriodKey,
  ) => {
    const metricKey = getPeerMetricKeyForMatrixCell(rowKey, periodKey)
    const metric = metricKey ? peerComparisonMetricByKey.get(metricKey) || null : null
    if (!metric) {
      return {
        primary: '—',
        secondary: null,
        peerAvailable: false,
        tone: 'empty',
      }
    }
    if (activePerformanceMatrixMode === 'peer_percentile') {
      return {
        primary: metric.percentile == null ? '—' : `${formatNumber(metric.percentile, 0)} pct`,
        secondary: metric.quartile == null ? null : `Q${formatNumber(metric.quartile, 0)}`,
        peerAvailable: true,
        tone: getPeerMetricTone(metric, activePerformanceMatrixMode),
      }
    }
    if (activePerformanceMatrixMode === 'peer_rank') {
      return {
        primary: formatPeerRank(metric),
        secondary: metric.percentile == null ? null : `${formatNumber(metric.percentile, 0)} pct`,
        peerAvailable: true,
        tone: getPeerMetricTone(metric, activePerformanceMatrixMode),
      }
    }
    return {
      primary: formatPeerMetricDelta(metric),
      secondary: `median ${formatPeerMetricValue(metric, metric.peer_median)}`,
      peerAvailable: true,
      tone: getPeerMetricTone(metric, activePerformanceMatrixMode),
    }
  }
  const performanceMetricMatrixBaseRows = [
    {
      key: 'period_return' as const,
      label: 'Period Return',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.periodReturn == null ? '—' : formatPercent(fund.periodReturn),
        secondary:
          benchmark?.periodReturn == null ? null : buildBenchmarkNote(formatPercent(benchmark.periodReturn)),
        tone: getSignedMetricTone(fund.periodReturn),
      })),
    },
    {
      key: 'annualized_return' as const,
      label: 'Ann. Return',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.annualizedReturn == null ? '—' : formatPercent(fund.annualizedReturn),
        secondary:
          benchmark?.annualizedReturn == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.annualizedReturn)),
        tone: getSignedMetricTone(fund.annualizedReturn),
      })),
    },
    {
      key: 'annualized_volatility' as const,
      label: 'Ann. Volatility',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.annualizedVolatility == null ? '—' : formatPercent(fund.annualizedVolatility),
        secondary:
          benchmark?.annualizedVolatility == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.annualizedVolatility)),
      })),
    },
    {
      key: 'excess_return' as const,
      label: 'Excess Return',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary:
          fund.periodReturn == null || benchmark?.periodReturn == null
            ? '—'
            : formatPercent(fund.periodReturn - benchmark.periodReturn),
        secondary: null,
        tone:
          fund.periodReturn == null || benchmark?.periodReturn == null
            ? 'empty'
            : getSignedMetricTone(fund.periodReturn - benchmark.periodReturn),
      })),
    },
    {
      key: 'sharpe_ratio' as const,
      label: 'Sharpe Ratio',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.sharpe == null ? '—' : formatNumber(fund.sharpe, 2),
        secondary: benchmark?.sharpe == null ? null : buildBenchmarkNote(formatNumber(benchmark.sharpe, 2)),
      })),
    },
    {
      key: 'sortino_ratio' as const,
      label: 'Sortino Ratio',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.sortino == null ? '—' : formatNumber(fund.sortino, 2),
        secondary:
          benchmark?.sortino == null ? null : buildBenchmarkNote(formatNumber(benchmark.sortino, 2)),
      })),
    },
    {
      key: 'calmar_ratio' as const,
      label: 'Calmar Ratio',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.calmar == null ? '—' : formatNumber(fund.calmar, 2),
        secondary: benchmark?.calmar == null ? null : buildBenchmarkNote(formatNumber(benchmark.calmar, 2)),
      })),
    },
    {
      key: 'information_ratio' as const,
      label: 'Information Ratio',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ relative }) => ({
        primary: relative?.informationRatio == null ? '—' : formatNumber(relative.informationRatio, 2),
        secondary: null,
      })),
    },
    {
      key: 'tracking_error' as const,
      label: 'Tracking Error',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ relative }) => ({
        primary: relative?.trackingError == null ? '—' : formatPercent(relative.trackingError),
        secondary: null,
      })),
    },
    {
      key: 'beta' as const,
      label: 'Beta',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ relative }) => ({
        primary: relative?.beta == null ? '—' : formatNumber(relative.beta, 2),
        secondary: null,
      })),
    },
    {
      key: 'max_drawdown' as const,
      label: 'Max DD',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.maxDrawdown == null ? '—' : formatPercent(fund.maxDrawdown),
        secondary:
          benchmark?.maxDrawdown == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.maxDrawdown)),
        tone: getSignedMetricTone(fund.maxDrawdown),
      })),
    },
    {
      key: 'recovery_days' as const,
      label: 'Recovery Days',
      supportsBenchmark: true,
      cells: performancePeriodSnapshots.map(({ fund, benchmark }) => ({
        primary: formatRecoveryValue(fund) || '—',
        secondary: buildBenchmarkNote(formatRecoveryValue(benchmark)),
      })),
    },
    {
      key: 'upside_capture' as const,
      label: 'Upside Capture',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ relative }) => ({
        primary: relative?.upsideCapture == null ? '—' : formatPercent(relative.upsideCapture, 0),
        secondary: null,
      })),
    },
    {
      key: 'downside_capture' as const,
      label: 'Downside Capture',
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map(({ relative }) => ({
        primary: relative?.downsideCapture == null ? '—' : formatPercent(relative.downsideCapture, 0),
        secondary: null,
      })),
    },
  ]
  const performanceMetricMatrixRows = performanceMetricMatrixBaseRows.map((row) => {
    if (activePerformanceMatrixMode === 'values') {
      return row
    }
    return {
      ...row,
      supportsBenchmark: false,
      cells: performancePeriodSnapshots.map((period) =>
        buildPeerPerformanceMatrixCell(row.key, period.key),
      ),
    }
  })
  const riskScatterRows = risk.scatter_points
    .map((row, index) => ({
      name: getString(row.name),
      returnValue: getNumber(row.return),
      volatilityValue: getNumber(row.volatility),
      tone:
        index === 0
          ? 'investment'
          : String(row.name || '').toLowerCase().includes('category')
            ? 'category'
            : 'index',
    }))
    .filter((row) => row.returnValue != null && row.volatilityValue != null) as Array<{
    name: string
    returnValue: number
    volatilityValue: number
    tone: 'investment' | 'category' | 'index'
  }>
  const riskVolMin = riskScatterRows.length
    ? Math.min(...riskScatterRows.map((row) => row.volatilityValue)) - 1
    : 0
  const riskVolMax = riskScatterRows.length
    ? Math.max(...riskScatterRows.map((row) => row.volatilityValue)) + 1
    : 1
  const riskReturnMin = riskScatterRows.length
    ? Math.min(...riskScatterRows.map((row) => row.returnValue)) - 2
    : 0
  const riskReturnMax = riskScatterRows.length
    ? Math.max(...riskScatterRows.map((row) => row.returnValue)) + 2
    : 1
  const riskXTickValues = getLinearTickValues(riskVolMin, riskVolMax, 5)
  const riskYTickValues = getLinearTickValues(riskReturnMin, riskReturnMax, 5)
  const riskPlotWidth =
    RISK_SCATTER_GEOMETRY.width - RISK_SCATTER_GEOMETRY.paddingLeft - RISK_SCATTER_GEOMETRY.paddingRight
  const riskPlotHeight =
    RISK_SCATTER_GEOMETRY.height - RISK_SCATTER_GEOMETRY.paddingTop - RISK_SCATTER_GEOMETRY.paddingBottom
  const positionedRiskScatterRows = riskScatterRows.map((row) => ({
    ...row,
    x:
      RISK_SCATTER_GEOMETRY.paddingLeft +
      ((row.volatilityValue - riskVolMin) / Math.max(riskVolMax - riskVolMin, 1)) * riskPlotWidth,
    y:
      RISK_SCATTER_GEOMETRY.height -
      RISK_SCATTER_GEOMETRY.paddingBottom -
      ((row.returnValue - riskReturnMin) / Math.max(riskReturnMax - riskReturnMin, 1)) * riskPlotHeight,
  }))
  const riskBenchmarkLabel = selectedBenchmark
    ? selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name
    : 'Not selected'
  const riskMatrixSnapshots = performancePeriodSnapshots.filter(({ key }) => RISK_MATRIX_PERIOD_KEYS.has(key))
  const lifetimeRiskSnapshot =
    riskMatrixSnapshots.find(({ key }) => key === 'SI')?.fund || buildPerformanceMetricSnapshot(calculationBasisSeries)
  const returnDrawdownSeries = buildDrawdownSeries(calculationBasisSeries)
  const formatRecoveryStatus = (snapshot: PerformanceMetricSnapshot | null) => {
    if (!snapshot || snapshot.maxDrawdown == null) {
      return '—'
    }
    if (snapshot.maxDrawdown === 0) {
      return 'At high watermark'
    }
    return snapshot.recoveryOpen ? 'In drawdown' : 'Recovered'
  }
  const riskProfileSeries = resampleSeries(
    returnDrawdownSeries,
    calculationBasisSeries.length > 260 ? 'weekly' : 'daily',
  )
  const riskProfileBounds = getDrawdownAxisBounds(riskProfileSeries)
  const riskProfileTickValues = getLinearTickValues(riskProfileBounds.min, riskProfileBounds.max, 4)
  const riskProfileTickDates = getChartTickDates(riskProfileSeries, 6)
  const riskProfileAreaPath = buildChartAreaPath(
    riskProfileSeries,
    SECONDARY_SERIES_GEOMETRY,
    riskProfileBounds.min,
    riskProfileBounds.max,
    0,
  )
  const riskProfileLinePath = buildChartLinePath(
    riskProfileSeries,
    SECONDARY_SERIES_GEOMETRY,
    riskProfileBounds.min,
    riskProfileBounds.max,
  )
  const monthlyDrawdownSeries = buildMonthlyMinimumSeries(returnDrawdownSeries).slice(-36)
  const monthlyDrawdownBounds = getDrawdownAxisBounds(monthlyDrawdownSeries)
  const monthlyDrawdownTickValues = getLinearTickValues(monthlyDrawdownBounds.min, monthlyDrawdownBounds.max, 4)
  const monthlyDrawdownTickDates = getChartTickDates(monthlyDrawdownSeries, 6)
  const monthlyDrawdownAreaPath = buildChartAreaPath(
    monthlyDrawdownSeries,
    SECONDARY_SERIES_GEOMETRY,
    monthlyDrawdownBounds.min,
    monthlyDrawdownBounds.max,
    0,
  )
  const monthlyDrawdownLinePath = buildChartLinePath(
    monthlyDrawdownSeries,
    SECONDARY_SERIES_GEOMETRY,
    monthlyDrawdownBounds.min,
    monthlyDrawdownBounds.max,
  )
  const monthlyVolatilitySeries = buildMonthlyAnnualizedVolatilitySeries(calculationBasisSeries).slice(-36)
  const monthlyVolatilityBounds = monthlyVolatilitySeries.length
    ? getPaddedAxisBounds(
        Math.min(0, ...monthlyVolatilitySeries.map((point) => point.value)),
        Math.max(...monthlyVolatilitySeries.map((point) => point.value)),
        0.12,
        0.5,
      )
    : { min: 0, max: 1 }
  const monthlyVolatilityTickValues = getLinearTickValues(
    monthlyVolatilityBounds.min,
    monthlyVolatilityBounds.max,
    4,
  )
  const monthlyVolatilityTickDates = getChartTickDates(monthlyVolatilitySeries, 6)
  const monthlyVolatilityAreaPath = buildChartAreaPath(
    monthlyVolatilitySeries,
    SECONDARY_SERIES_GEOMETRY,
    monthlyVolatilityBounds.min,
    monthlyVolatilityBounds.max,
    0,
  )
  const monthlyVolatilityLinePath = buildChartLinePath(
    monthlyVolatilitySeries,
    SECONDARY_SERIES_GEOMETRY,
    monthlyVolatilityBounds.min,
    monthlyVolatilityBounds.max,
  )
  const riskSummaryRows = [
    {
      label: 'As Of',
      value: formatDate(risk.snapshot_metadata?.as_of_date || latestNavRecord?.as_of_date || null),
    },
    {
      label: 'Benchmark',
      value: riskBenchmarkLabel,
    },
    {
      label: 'Risk Basis',
      value: calculationFrequencyStatus,
    },
    {
      label: 'Ann. Factor',
      value:
        calculationFrequencyProfile.annualization_periods_per_year == null
          ? '—'
          : `${formatNumber(calculationFrequencyProfile.annualization_periods_per_year, 1)} / yr`,
    },
    {
      label: 'Current DD',
      value:
        returnDrawdownSeries.length > 0
          ? formatPercent(returnDrawdownSeries[returnDrawdownSeries.length - 1].value)
          : '—',
    },
    {
      label: 'Max DD',
      value:
        lifetimeRiskSnapshot.maxDrawdown == null
          ? '—'
          : formatPercent(lifetimeRiskSnapshot.maxDrawdown),
    },
    {
      label: 'Recovery Status',
      value: formatRecoveryStatus(lifetimeRiskSnapshot),
    },
    {
      label: 'Recovery Days',
      value: formatRecoveryValue(lifetimeRiskSnapshot) || '—',
    },
  ]
  const rollingRiskWindowLabel =
    ROLLING_RISK_WINDOW_OPTIONS.find((option) => option.months === rollingRiskWindowMonths)?.label ||
    `${rollingRiskWindowMonths}M`
  const rollingVolatilitySeries = buildRollingAnnualizedVolatilitySeries(
    calculationBasisSeries,
    rollingRiskWindowMonths,
  ).slice(-ROLLING_CHART_MAX_POINTS)
  const benchmarkRollingVolatilitySeries =
    selectedBenchmark && benchmarkCalculationSeries.length > 0
      ? buildRollingAnnualizedVolatilitySeries(
          benchmarkCalculationSeries,
          rollingRiskWindowMonths,
        ).slice(-ROLLING_CHART_MAX_POINTS)
      : []
  const rollingSharpeSeries = buildRollingSharpeSeries(calculationBasisSeries, rollingRiskWindowMonths).slice(
    -ROLLING_CHART_MAX_POINTS,
  )
  const benchmarkRollingSharpeSeries =
    selectedBenchmark && benchmarkCalculationSeries.length > 0
      ? buildRollingSharpeSeries(benchmarkCalculationSeries, rollingRiskWindowMonths).slice(
          -ROLLING_CHART_MAX_POINTS,
        )
      : []
  const rollingBetaSeries =
    selectedBenchmark && benchmarkCalculationSeries.length > 0
      ? buildRollingBetaSeries(calculationBasisSeries, benchmarkCalculationSeries).slice(-60)
      : []
  const rollingRiskFactRows = [
    {
      label: 'Latest Rolling Ann. Vol',
      value:
        rollingVolatilitySeries.length > 0
          ? formatPercent(rollingVolatilitySeries[rollingVolatilitySeries.length - 1].value)
          : '—',
    },
    {
      label: 'Peak Rolling Ann. Vol',
      value:
        rollingVolatilitySeries.length > 0
          ? formatPercent(Math.max(...rollingVolatilitySeries.map((point) => point.value)))
          : '—',
    },
    {
      label: 'Latest Rolling Beta',
      value:
        rollingBetaSeries.length > 0
          ? formatNumber(rollingBetaSeries[rollingBetaSeries.length - 1].value, 2)
          : selectedBenchmark
            ? 'Insufficient overlap'
            : 'No benchmark selected',
    },
    {
      label: 'Beta Benchmark',
      value: riskBenchmarkLabel,
    },
  ]
  const monthlyReturnSeries = buildMonthlyReturnSeries(calculationBasisSeries)
  const latestMonthlyReturnValue =
    monthlyReturnSeries.length > 0 ? monthlyReturnSeries[monthlyReturnSeries.length - 1].value : null
  const medianMonthlyReturnValue = getMedianValue(monthlyReturnSeries.map((point) => point.value))
  const trailingNegativeMonthCount = getTrailingNegativeMonthCount(monthlyReturnSeries)
  const latestMonthlyDrawdownValue =
    monthlyDrawdownSeries.length > 0 ? monthlyDrawdownSeries[monthlyDrawdownSeries.length - 1].value : null
  const worstMonthlyDrawdownValue =
    monthlyDrawdownSeries.length > 0 ? Math.min(...monthlyDrawdownSeries.map((point) => point.value)) : null
  const currentDrawdownValue =
    returnDrawdownSeries.length > 0 ? returnDrawdownSeries[returnDrawdownSeries.length - 1].value : null
  const latestRollingVolValue =
    rollingVolatilitySeries.length > 0 ? rollingVolatilitySeries[rollingVolatilitySeries.length - 1].value : null
  const rollingVolMedianValue = getMedianValue(rollingVolatilitySeries.map((point) => point.value))
  const rollingVolPercentile =
    latestRollingVolValue == null
      ? null
      : getPercentileRank(
          rollingVolatilitySeries.map((point) => point.value),
          latestRollingVolValue,
        )
  const latestRollingBetaValue =
    rollingBetaSeries.length > 0 ? rollingBetaSeries[rollingBetaSeries.length - 1].value : null
  const rollingBetaMedianValue = getMedianValue(rollingBetaSeries.map((point) => point.value))
  const rollingBetaPercentile =
    latestRollingBetaValue == null
      ? null
      : getPercentileRank(
          rollingBetaSeries.map((point) => point.value),
          latestRollingBetaValue,
        )
  const latest1WReturn = performancePeriodSnapshots.find(({ key }) => key === '1W')?.fund.periodReturn ?? null
  const latestMtdReturn = performancePeriodSnapshots.find(({ key }) => key === 'MTD')?.fund.periodReturn ?? null
  const structuralRiskSnapshot =
    riskMatrixSnapshots.find(({ key }) => key === '3Y') ??
    riskMatrixSnapshots.find(({ key }) => key === 'SI') ??
    null
  const structuralRiskLabel = structuralRiskSnapshot?.label || 'SI'
  const structuralFundSnapshot = structuralRiskSnapshot?.fund ?? null
  const structuralRelativeSnapshot = structuralRiskSnapshot?.relative ?? null
  const buildWatchReading = (level: string, detail: string) => `${level} · ${detail}`
  const scoreWatchLevel = (level: string) => (level === 'High' ? 2 : level === 'Elevated' ? 1 : 0)
  const volatilityWatch = (() => {
    if (latestRollingVolValue == null || rollingVolMedianValue == null || rollingVolPercentile == null) {
      return {
        level: 'N/A',
        reading: 'N/A · Need more 12M rolling history',
      }
    }
    const multiple =
      rollingVolMedianValue === 0 ? null : latestRollingVolValue / rollingVolMedianValue
    const level =
      rollingVolPercentile >= 90 || (multiple != null && multiple >= 1.4)
        ? 'High'
        : rollingVolPercentile >= 75 || (multiple != null && multiple >= 1.2)
          ? 'Elevated'
          : 'Normal'
    return {
      level,
      reading: buildWatchReading(
        level,
        `${formatPercent(latestRollingVolValue)} vs median ${formatPercent(rollingVolMedianValue)} (${formatNumber(rollingVolPercentile, 0)}th pct)`,
      ),
    }
  })()
  const drawdownPressureWatch = (() => {
    if (currentDrawdownValue == null) {
      return {
        level: 'N/A',
        reading: 'N/A · No drawdown history',
      }
    }
    const worstAbs = lifetimeRiskSnapshot.maxDrawdown == null ? null : Math.abs(lifetimeRiskSnapshot.maxDrawdown)
    const ratio = worstAbs && worstAbs > 0 ? Math.abs(currentDrawdownValue) / worstAbs : 0
    const level =
      currentDrawdownValue <= -8 || ratio >= 0.6
        ? 'High'
        : currentDrawdownValue <= -4 || ratio >= 0.35
          ? 'Elevated'
          : 'Normal'
    return {
      level,
      reading: buildWatchReading(
        level,
        `${formatPercent(currentDrawdownValue)} current${worstAbs ? `, ${formatNumber(ratio * 100, 0)}% of worst` : ''}`,
      ),
    }
  })()
  const recentLossPressureWatch = (() => {
    if (latestMonthlyReturnValue == null && latest1WReturn == null && latestMtdReturn == null) {
      return {
        level: 'N/A',
        reading: 'N/A · Need recent return history',
      }
    }
    const level =
      trailingNegativeMonthCount >= 3 ||
      (latestMonthlyReturnValue != null && latestMonthlyReturnValue <= -3) ||
      (latest1WReturn != null && latest1WReturn <= -2)
        ? 'High'
        : trailingNegativeMonthCount >= 2 ||
            (latestMonthlyReturnValue != null && latestMonthlyReturnValue <= -1.5) ||
            (latestMtdReturn != null && latestMtdReturn <= -3)
          ? 'Elevated'
          : 'Normal'
    const recentMonthlyLabel =
      latestMonthlyReturnValue == null ? '—' : formatPercent(latestMonthlyReturnValue)
    return {
      level,
      reading: buildWatchReading(
        level,
        `1W ${latest1WReturn == null ? '—' : formatPercent(latest1WReturn)}, MTD ${latestMtdReturn == null ? '—' : formatPercent(latestMtdReturn)}, latest month ${recentMonthlyLabel}, ${String(trailingNegativeMonthCount)} down month(s)`,
      ),
    }
  })()
  const betaDriftWatch = (() => {
    if (!selectedBenchmark) {
      return {
        level: 'N/A',
        reading: 'N/A · Select a benchmark',
      }
    }
    if (latestRollingBetaValue == null || rollingBetaMedianValue == null || rollingBetaPercentile == null) {
      return {
        level: 'N/A',
        reading: 'N/A · Need enough overlapping 12M windows',
      }
    }
    const absoluteDelta = Math.abs(latestRollingBetaValue - rollingBetaMedianValue)
    const level =
      absoluteDelta >= 0.35 || rollingBetaPercentile >= 90
        ? 'High'
        : absoluteDelta >= 0.2 || rollingBetaPercentile >= 75
          ? 'Elevated'
          : 'Normal'
    return {
      level,
      reading: buildWatchReading(
        level,
        `${formatNumber(latestRollingBetaValue, 2)} vs median ${formatNumber(rollingBetaMedianValue, 2)} (${formatNumber(rollingBetaPercentile, 0)}th pct)`,
      ),
    }
  })()
  const watchScore =
    scoreWatchLevel(volatilityWatch.level) +
    scoreWatchLevel(drawdownPressureWatch.level) +
    scoreWatchLevel(recentLossPressureWatch.level) +
    scoreWatchLevel(betaDriftWatch.level)
  const overallWatchLevel =
    watchScore >= 5 ? 'High' : watchScore >= 2 ? 'Elevated' : watchScore >= 0 ? 'Normal' : 'N/A'
  const latestYtdReturn = performancePeriodSnapshots.find(({ key }) => key === 'YTD')?.fund.periodReturn ?? null
  const lifetimePerformanceSnapshot =
    performancePeriodSnapshots.find(({ key }) => key === 'SI')?.fund || buildPerformanceMetricSnapshot(calculationBasisSeries)
  const overviewRatingValue =
    ratings.overall_rating == null ? '—' : formatStarRating(ratings.overall_rating)
  const overviewRatingNote =
    ratings.overall_rating == null
      ? 'Pending research'
      : summary.rating_as_of
        ? `As of ${formatDate(summary.rating_as_of)}`
        : 'Research rating'
  const researchManualRating = parseManualRating(research.manual_rating)
  const displayedManualRating = manualRatingDirty ? manualRatingDraft : researchManualRating
  const manualRatingHasChanges = manualRatingDirty && displayedManualRating !== researchManualRating
  const overviewRankingValue =
    performance.ranking
      ? [
          performance.ranking.rank == null || performance.ranking.sample_count == null
            ? null
            : `${formatNumber(performance.ranking.rank, 0)} / ${formatNumber(performance.ranking.sample_count, 0)}`,
          performance.ranking.quartile == null ? null : `Q${performance.ranking.quartile}`,
          performance.ranking.percentile == null
            ? null
            : `${formatNumber(performance.ranking.percentile, 0)} pct`,
        ]
          .filter(Boolean)
          .join(' / ') || '—'
      : '—'
  const overviewSideMetricRows = [
    {
      label: '1W Return',
      value: latest1WReturn == null ? '—' : formatPercent(latest1WReturn),
      note: 'Latest',
    },
    {
      label: 'MTD Return',
      value: latestMtdReturn == null ? '—' : formatPercent(latestMtdReturn),
      note: 'Current month',
    },
    {
      label: 'YTD Return',
      value: latestYtdReturn == null ? '—' : formatPercent(latestYtdReturn),
      note: 'Year to date',
    },
    {
      label: 'Ann. Return',
      value:
        lifetimePerformanceSnapshot.annualizedReturn == null
          ? '—'
          : formatPercent(lifetimePerformanceSnapshot.annualizedReturn),
      note: 'SI',
    },
    {
      label: 'Ann. Vol',
      value:
        lifetimePerformanceSnapshot.annualizedVolatility == null
          ? '—'
          : formatPercent(lifetimePerformanceSnapshot.annualizedVolatility),
      note: 'SI',
    },
    {
      label: 'Max DD',
      value:
        lifetimeRiskSnapshot.maxDrawdown == null ? '—' : formatPercent(lifetimeRiskSnapshot.maxDrawdown),
      note: 'SI',
    },
    {
      label: 'Current DD',
      value: currentDrawdownValue == null ? '—' : formatPercent(currentDrawdownValue),
      note: drawdownPressureWatch.level,
    },
    {
      label: 'SI Sharpe',
      value: lifetimePerformanceSnapshot.sharpe == null ? '—' : formatNumber(lifetimePerformanceSnapshot.sharpe, 2),
      note: 'rf = 0',
    },
    {
      label: 'Peer Rank',
      value: overviewRankingValue,
      note: peerComparisonPathLabel,
    },
    {
      label: 'Watch Level',
      value: overallWatchLevel,
      note: `${String(watchScore)} signal point(s)`,
    },
  ]
  const rawTaxonomyPathLabel =
    productFrameworkAttributes?.taxonomy?.path_labels?.length
      ? productFrameworkAttributes.taxonomy.path_labels.join(' / ')
      : summary.taxonomy?.path_labels?.length
        ? summary.taxonomy.path_labels.join(' / ')
        : summary.taxonomy?.assigned_label || ''
  const taxonomyPathLabel = localizeTaxonomyPath(rawTaxonomyPathLabel, language)
  const detailClassificationLabel =
    localizeTaxonomyPath(peerComparison?.peer_path?.filter(Boolean).join(' / ') || rawTaxonomyPathLabel, language)
  const taxonomyNodes = taxonomyTree?.nodes || []
  const taxonomyDraftNode =
    taxonomyNodes.find((node) => node.node_id === taxonomyDraftNodeId) || null
  const taxonomyDraftPathNodeIds = taxonomyDraftNode?.path_node_ids || []
  const taxonomyLevelSelectors = (() => {
    const selectors: Array<{
      levelIndex: number
      parentNodeId: string | null
      selectedNodeId: string
      options: FundTaxonomyTreeNode[]
      disabled: boolean
    }> = []
    let parentNodeId: string | null = null
    let chainActive = true
    const maxDepth = Math.max(taxonomyTree?.max_depth || 3, taxonomyDraftPathNodeIds.length + 1)

    for (let levelIndex = 1; levelIndex <= maxDepth; levelIndex += 1) {
      const options = chainActive
        ? taxonomyNodes.filter((node) => node.parent_node_id === parentNodeId)
        : []
      const selectedNodeId: string = chainActive ? taxonomyDraftPathNodeIds[levelIndex - 1] || '' : ''
      selectors.push({
        levelIndex,
        parentNodeId,
        selectedNodeId,
        options,
        disabled: !chainActive || options.length === 0,
      })
      chainActive = Boolean(selectedNodeId)
      parentNodeId = selectedNodeId
    }

    return selectors
  })()
  const localCurrentRiskWatchRows = [
    {
      label: 'Overall Watch',
      value: buildWatchReading(overallWatchLevel, `${String(watchScore)} signal point(s)`),
    },
    {
      label: 'Volatility Regime',
      value: volatilityWatch.reading,
    },
    {
      label: 'Drawdown Pressure',
      value: drawdownPressureWatch.reading,
    },
    {
      label: 'Recent Loss Pressure',
      value: recentLossPressureWatch.reading,
    },
    {
      label: 'Benchmark Sensitivity',
      value: betaDriftWatch.reading,
    },
    {
      label: 'Methodology',
      value: 'Heuristic watch flags based on current drawdown, rolling vol, recent losses, and beta drift.',
    },
  ]
  const localRiskFallbackFacts = localCurrentRiskWatchRows.filter((row) => row.value !== '—').slice(0, 4)
  const localRiskStructureRows = [
    {
      characteristic: 'Risk Style',
      reading:
        structuralFundSnapshot?.annualizedVolatility == null && structuralFundSnapshot?.maxDrawdown == null
          ? '—'
          : `${structuralRiskLabel} vol ${structuralFundSnapshot?.annualizedVolatility == null ? '—' : formatPercent(structuralFundSnapshot.annualizedVolatility)} · max DD ${structuralFundSnapshot?.maxDrawdown == null ? '—' : formatPercent(structuralFundSnapshot.maxDrawdown)}`,
      interpretation:
        structuralFundSnapshot?.annualizedVolatility == null || structuralFundSnapshot?.maxDrawdown == null
          ? 'Insufficient history to classify the long-run risk amplitude.'
          : structuralFundSnapshot.annualizedVolatility < 8 && Math.abs(structuralFundSnapshot.maxDrawdown) < 10
            ? 'Low-amplitude path. Capital preservation matters more than benchmark capture.'
            : structuralFundSnapshot.annualizedVolatility < 15 && Math.abs(structuralFundSnapshot.maxDrawdown) < 20
              ? 'Balanced amplitude. Drawdowns matter, but the path is still broadly manageable.'
              : 'High-amplitude path. Position sizing and liquidity discipline matter.'
    },
    {
      characteristic: 'Benchmark Dependence',
      reading:
        structuralRelativeSnapshot?.beta == null && structuralRelativeSnapshot?.trackingError == null
          ? '—'
          : `${structuralRiskLabel} beta ${structuralRelativeSnapshot?.beta == null ? '—' : formatNumber(structuralRelativeSnapshot.beta, 2)} · TE ${structuralRelativeSnapshot?.trackingError == null ? '—' : formatPercent(structuralRelativeSnapshot.trackingError)}`,
      interpretation:
        !selectedBenchmark
          ? 'No benchmark selected, so benchmark dependence is not fully specified.'
          : structuralRelativeSnapshot?.beta == null || structuralRelativeSnapshot?.trackingError == null
            ? 'Need more overlap with the current benchmark to characterize sensitivity.'
            : structuralRelativeSnapshot.beta < 0.35 && structuralRelativeSnapshot.trackingError < 5
              ? 'Low benchmark dependence. Risk is driven more by manager path than market beta.'
              : structuralRelativeSnapshot.beta < 0.8 && structuralRelativeSnapshot.trackingError < 10
                ? 'Moderate benchmark dependence. Market moves matter, but are not the whole story.'
                : 'High benchmark dependence. Benchmark direction and factor conditions matter a lot.'
    },
    {
      characteristic: 'Downside Shape',
      reading:
        structuralRelativeSnapshot?.upsideCapture == null && structuralRelativeSnapshot?.downsideCapture == null
          ? '—'
          : `${structuralRiskLabel} up ${structuralRelativeSnapshot?.upsideCapture == null ? '—' : formatPercent(structuralRelativeSnapshot.upsideCapture, 0)} · down ${structuralRelativeSnapshot?.downsideCapture == null ? '—' : formatPercent(structuralRelativeSnapshot.downsideCapture, 0)}`,
      interpretation:
        !selectedBenchmark
          ? '—'
          : structuralRelativeSnapshot?.upsideCapture == null || structuralRelativeSnapshot?.downsideCapture == null
            ? 'Capture profile needs a longer overlapping benchmark history.'
            : structuralRelativeSnapshot.downsideCapture < structuralRelativeSnapshot.upsideCapture - 15
              ? 'Downside participation is meaningfully lighter than upside participation.'
              : structuralRelativeSnapshot.downsideCapture > structuralRelativeSnapshot.upsideCapture + 15
                ? 'Downside participation is heavy relative to upside capture.'
                : 'Upside and downside participation are broadly balanced.'
    },
    {
      characteristic: 'Recovery Profile',
      reading:
        lifetimeRiskSnapshot.maxDrawdown == null
          ? '—'
          : `SI max DD ${formatPercent(lifetimeRiskSnapshot.maxDrawdown)} · recovery ${formatRecoveryValue(lifetimeRiskSnapshot) || '—'}`,
      interpretation:
        lifetimeRiskSnapshot.maxDrawdown == null
          ? 'Insufficient history to classify recovery behavior.'
          : lifetimeRiskSnapshot.recoveryOpen
            ? 'The fund is still below its prior high watermark.'
            : lifetimeRiskSnapshot.recoveryDays != null && lifetimeRiskSnapshot.recoveryDays <= 120
              ? 'Historically, major drawdowns have healed relatively quickly.'
              : lifetimeRiskSnapshot.recoveryDays != null && lifetimeRiskSnapshot.recoveryDays > 365
                ? 'Drawdowns can take a long time to repair.'
                : 'Recovery profile is moderate rather than fast.'
    },
  ]
  const drawdownSummaryRows = [
    {
      label: 'Peak Date',
      value: formatDate(risk.drawdown_summary?.peak_date),
    },
    {
      label: 'Valley Date',
      value: formatDate(risk.drawdown_summary?.valley_date),
    },
    {
      label: 'Max Duration',
      value:
        risk.drawdown_summary?.max_duration_months == null
          ? '—'
          : `${String(risk.drawdown_summary.max_duration_months)} mo`,
    },
    {
      label: 'Worst Monthly Drawdown',
      value:
        monthlyDrawdownSeries.length > 0
          ? formatPercent(Math.min(...monthlyDrawdownSeries.map((point) => point.value)))
          : '—',
    },
  ]
  const localRiskChangeRows = [
    {
      signal: 'Rolling Ann. Vol',
      current: latestRollingVolValue == null ? '—' : formatPercent(latestRollingVolValue),
      baseline: rollingVolMedianValue == null ? '—' : `Median ${formatPercent(rollingVolMedianValue)}`,
      change:
        latestRollingVolValue == null || rollingVolMedianValue == null
          ? '—'
          : `${latestRollingVolValue >= rollingVolMedianValue ? '+' : ''}${formatPercent(latestRollingVolValue - rollingVolMedianValue)}`,
      watch: volatilityWatch.level,
    },
    {
      signal: 'Rolling Beta',
      current:
        latestRollingBetaValue == null
          ? (selectedBenchmark ? '—' : 'No benchmark selected')
          : formatNumber(latestRollingBetaValue, 2),
      baseline:
        rollingBetaMedianValue == null
          ? '—'
          : `Median ${formatNumber(rollingBetaMedianValue, 2)}`,
      change:
        latestRollingBetaValue == null || rollingBetaMedianValue == null
          ? '—'
          : `${latestRollingBetaValue >= rollingBetaMedianValue ? '+' : ''}${formatNumber(latestRollingBetaValue - rollingBetaMedianValue, 2)}`,
      watch: betaDriftWatch.level,
    },
    {
      signal: 'Current DD',
      current: currentDrawdownValue == null ? '—' : formatPercent(currentDrawdownValue),
      baseline:
        lifetimeRiskSnapshot.maxDrawdown == null
          ? '—'
          : `Worst ${formatPercent(lifetimeRiskSnapshot.maxDrawdown)}`,
      change:
        currentDrawdownValue == null || lifetimeRiskSnapshot.maxDrawdown == null || lifetimeRiskSnapshot.maxDrawdown === 0
          ? '—'
          : `${formatNumber((Math.abs(currentDrawdownValue) / Math.abs(lifetimeRiskSnapshot.maxDrawdown)) * 100, 0)}% of worst`,
      watch: drawdownPressureWatch.level,
    },
    {
      signal: 'Latest Monthly Drawdown',
      current: latestMonthlyDrawdownValue == null ? '—' : formatPercent(latestMonthlyDrawdownValue),
      baseline:
        worstMonthlyDrawdownValue == null
          ? '—'
          : `Worst ${formatPercent(worstMonthlyDrawdownValue)}`,
      change:
        latestMonthlyDrawdownValue == null || worstMonthlyDrawdownValue == null || worstMonthlyDrawdownValue === 0
          ? '—'
          : `${formatNumber((Math.abs(latestMonthlyDrawdownValue) / Math.abs(worstMonthlyDrawdownValue)) * 100, 0)}% of worst`,
      watch: recentLossPressureWatch.level,
    },
    {
      signal: 'Recent Return Pressure',
      current: `1W ${latest1WReturn == null ? '—' : formatPercent(latest1WReturn)} · MTD ${latestMtdReturn == null ? '—' : formatPercent(latestMtdReturn)}`,
      baseline:
        medianMonthlyReturnValue == null
          ? '—'
          : `Median month ${formatPercent(medianMonthlyReturnValue)}`,
      change: `${String(trailingNegativeMonthCount)} trailing down month(s)`,
      watch: recentLossPressureWatch.level,
    },
  ]
  const payloadCurrentWatchRows = Array.isArray(risk.current_watch?.rows)
    ? risk.current_watch.rows
      .map((row) => {
        const label = getString(row.signal)
        const reading = getString(row.reading)
        if (label === '—' || reading === '—') {
          return null
        }
        return {
          label,
          value: reading,
        }
      })
      .filter((row): row is { label: string; value: string } => row !== null)
    : []
  const payloadRiskStructureRows = Array.isArray(risk.risk_structure?.rows)
    ? risk.risk_structure.rows
      .map((row) => {
        const characteristic = getString(row.characteristic)
        const reading = getString(row.reading)
        const interpretation = getString(row.interpretation)
        if (characteristic === '—') {
          return null
        }
        return {
          characteristic,
          reading,
          interpretation,
        }
      })
      .filter(
        (
          row,
        ): row is {
          characteristic: string
          reading: string
          interpretation: string
        } => row !== null,
      )
    : []
  const payloadRiskChangeRows = Array.isArray(risk.change_monitor?.rows)
    ? risk.change_monitor.rows
      .map((row) => {
        const signal = getString(row.signal)
        if (signal === '—') {
          return null
        }
        return {
          signal,
          current: getString(row.current),
          baseline: getString(row.baseline),
          change: getString(row.change),
          watch: getString(row.watch),
        }
      })
      .filter(
        (
          row,
        ): row is {
          signal: string
          current: string
          baseline: string
          change: string
          watch: string
        } => row !== null,
      )
    : []
  const currentRiskWatchRows = payloadCurrentWatchRows.length ? payloadCurrentWatchRows : localCurrentRiskWatchRows
  const riskFallbackFacts = currentRiskWatchRows.length ? currentRiskWatchRows.slice(0, 4) : localRiskFallbackFacts
  const riskStructureRows = payloadRiskStructureRows.length ? payloadRiskStructureRows : localRiskStructureRows
  const riskChangeRows = payloadRiskChangeRows.length ? payloadRiskChangeRows : localRiskChangeRows
  const riskMatrixRows = [
    {
      label: 'Ann. Volatility',
      supportsBenchmark: true,
      cells: riskMatrixSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.annualizedVolatility == null ? '—' : formatPercent(fund.annualizedVolatility),
        secondary:
          benchmark?.annualizedVolatility == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.annualizedVolatility)),
      })),
    },
    {
      label: 'Downside Deviation',
      supportsBenchmark: true,
      cells: riskMatrixSnapshots.map(({ fund, benchmark }) => ({
        primary:
          fund.annualizedDownsideDeviation == null ? '—' : formatPercent(fund.annualizedDownsideDeviation),
        secondary:
          benchmark?.annualizedDownsideDeviation == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.annualizedDownsideDeviation)),
      })),
    },
    {
      label: 'Tracking Error',
      supportsBenchmark: false,
      cells: riskMatrixSnapshots.map(({ relative }) => ({
        primary: relative?.trackingError == null ? '—' : formatPercent(relative.trackingError),
        secondary: null,
      })),
    },
    {
      label: 'Beta',
      supportsBenchmark: false,
      cells: riskMatrixSnapshots.map(({ relative }) => ({
        primary: relative?.beta == null ? '—' : formatNumber(relative.beta, 2),
        secondary: null,
      })),
    },
    {
      label: 'Max DD',
      supportsBenchmark: true,
      cells: riskMatrixSnapshots.map(({ fund, benchmark }) => ({
        primary: fund.maxDrawdown == null ? '—' : formatPercent(fund.maxDrawdown),
        secondary:
          benchmark?.maxDrawdown == null
            ? null
            : buildBenchmarkNote(formatPercent(benchmark.maxDrawdown)),
      })),
    },
    {
      label: 'Recovery Days',
      supportsBenchmark: true,
      cells: riskMatrixSnapshots.map(({ fund, benchmark }) => ({
        primary: formatRecoveryValue(fund) || '—',
        secondary: buildBenchmarkNote(formatRecoveryValue(benchmark)),
      })),
    },
    {
      label: 'Upside Capture',
      supportsBenchmark: false,
      cells: riskMatrixSnapshots.map(({ relative }) => ({
        primary: relative?.upsideCapture == null ? '—' : formatPercent(relative.upsideCapture, 0),
        secondary: null,
      })),
    },
    {
      label: 'Downside Capture',
      supportsBenchmark: false,
      cells: riskMatrixSnapshots.map(({ relative }) => ({
        primary: relative?.downsideCapture == null ? '—' : formatPercent(relative.downsideCapture, 0),
        secondary: null,
      })),
    },
  ]
  const renderBenchmarkSearch = (ariaLabel: string, extraClassName = '') => {
    const className = ['instrument-chart-compare', extraClassName].filter(Boolean).join(' ')
    return (
      <div className={className}>
        <label className="instrument-chart-compare-search">
          <div className="instrument-chart-compare-search-box">
            <input
              type="search"
              aria-label={ariaLabel}
              placeholder="Compare benchmark..."
              value={benchmarkInputValue}
              onFocus={() => setBenchmarkSearchFocused(true)}
              onBlur={() => window.setTimeout(() => setBenchmarkSearchFocused(false), 140)}
              onChange={(event) => {
                const nextValue = event.target.value
                setBenchmarkSearch(nextValue)
                if (selectedBenchmark && nextValue !== selectedBenchmarkLabel) {
                  setBenchmarkFundId('')
                  setBenchmarkNavSeries(null)
                }
              }}
            />
            {selectedBenchmark ? (
              <button
                type="button"
                className="instrument-chart-compare-clear"
                aria-label="Clear benchmark"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  setBenchmarkFundId('')
                  setBenchmarkSearch('')
                  setBenchmarkNavSeries(null)
                }}
              >
                ×
              </button>
            ) : (
              <button
                type="button"
                className="instrument-chart-compare-toggle"
                aria-label="Show benchmark choices"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => setBenchmarkSearchFocused((current) => !current)}
              >
                <span aria-hidden="true" />
              </button>
            )}
            {showBenchmarkResults ? (
              <div className="instrument-chart-compare-results">
                {filteredBenchmarkOptions.length ? (
                  filteredBenchmarkOptions.map((item) => (
                    <button
                      type="button"
                      key={item.fund_id}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => {
                        setBenchmarkFundId(item.fund_id)
                        setBenchmarkSearch(benchmarkLibraryLabel(item))
                        setBenchmarkSearchFocused(false)
                        setBenchmarkNavSeries(null)
                      }}
                    >
                      <strong>{item.fund_name}</strong>
                      <span>
                        {item.ticker_or_isin || item.fund_id} · {formatLabel(item.product_type)}
                      </span>
                    </button>
                  ))
                ) : (
                  <div className="instrument-chart-compare-empty">No database match</div>
                )}
              </div>
            ) : null}
          </div>
        </label>
      </div>
    )
  }
  function resolveChartPointerSelection(
    event: ReactMouseEvent<SVGSVGElement>,
    geometry: ChartGeometry,
  ) {
    if (scaledVisibleSeries.length < 2) {
      return null
    }
    const rect = event.currentTarget.getBoundingClientRect()
    if (!rect.width) {
      return null
    }
    const relativeX = ((event.clientX - rect.left) / rect.width) * geometry.width
    const relativeY = ((event.clientY - rect.top) / Math.max(rect.height, 1)) * geometry.height
    const plotBounds = getChartPlotBounds(geometry, CHART_HOVER_INSET)
    if (
      relativeX <= plotBounds.left ||
      relativeX >= plotBounds.right ||
      relativeY <= plotBounds.top ||
      relativeY >= plotBounds.bottom
    ) {
      return null
    }
    const positionedPoints =
      geometry === DRAWDOWN_CHART_GEOMETRY ? positionedDrawdownPoints : positionedChartPoints
    const resolvedIndex = findNearestPositionedPointIndex(positionedPoints, relativeX)
    if (resolvedIndex < 0) {
      return null
    }
    const selectedPositionedPoint = positionedPoints[resolvedIndex]
    const selectedXRatio =
      (selectedPositionedPoint.x - plotBounds.left) / Math.max(plotBounds.width, 1)
    return {
      index: resolvedIndex,
      point: scaledVisibleSeries[resolvedIndex],
      cursor: {
        xRatio: Math.min(Math.max(selectedXRatio, 0), 1),
        y: relativeY,
      },
    }
  }

  function updateChartHoverFromPointer(
    event: ReactMouseEvent<SVGSVGElement>,
    geometry: ChartGeometry,
    panel: ChartHoverPanel,
  ) {
    const nextSelection = resolveChartPointerSelection(event, geometry)
    if (!nextSelection) {
      clearChartHover()
      return
    }
    setChartHoverPanel(panel)
    setChartHoverCursor(nextSelection.cursor)
    setChartHoverIndex(nextSelection.index)
  }

  function clearChartHover() {
    setChartHoverCursor(null)
    setChartHoverIndex(null)
    setChartHoverPanel(null)
  }

  function handlePrimaryChartClick(event: ReactMouseEvent<SVGSVGElement>) {
    if (!timelineNoteCaptureMode) {
      return
    }
    const selection = resolveChartPointerSelection(event, PRIMARY_CHART_GEOMETRY)
    if (!selection) {
      return
    }
    setChartHoverPanel('primary')
    setChartHoverCursor(selection.cursor)
    setChartHoverIndex(selection.index)
    openTimelineNoteEditor(selection.point.date)
  }

  function handlePrimaryChartContextMenu(event: ReactMouseEvent<SVGSVGElement>) {
    const selection = resolveChartPointerSelection(event, PRIMARY_CHART_GEOMETRY)
    if (!selection) {
      return
    }
    event.preventDefault()
    setChartHoverPanel('primary')
    setChartHoverCursor(selection.cursor)
    setChartHoverIndex(selection.index)
    setChartTimelineNoteContextMenu({
      clientX: event.clientX,
      clientY: event.clientY,
      anchorDate: selection.point.date,
    })
  }

  const chartSettingsMenu =
    openQuoteChartMenu === 'settings' ? (
      <div className="instrument-chart-menu-panel instrument-chart-settings-panel">
        <div className="instrument-chart-settings-layout">
          <section className="instrument-chart-settings-block">
            <div className="instrument-chart-settings-block-head">
              <span>Series</span>
              <strong>{chartSeriesBasisLabel}</strong>
            </div>
            <div className="instrument-chart-settings-control-group">
              <span>Data Type</span>
              <div className="instrument-chart-settings-option-grid">
                {quoteBasisOptions.map((basis) => (
                  <button
                    key={basis}
                    type="button"
                    className={
                      activeQuoteBasis === basis
                        ? 'instrument-chart-option instrument-chart-option-active'
                        : 'instrument-chart-option'
                    }
                    onClick={() => setQuoteBasis(basis)}
                  >
                    {localize(language, QUOTE_BASIS_LABELS[basis])}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="instrument-chart-settings-block">
            <div className="instrument-chart-settings-block-head">
              <span>Display</span>
              <strong>{effectiveCurrency}</strong>
            </div>
            <div className="instrument-chart-settings-field-grid">
              <label className="instrument-chart-settings-field">
                <span>Frequency</span>
                <select
                  value={chartFrequency}
                  onChange={(event) => setChartFrequency(event.target.value as ChartFrequency)}
                >
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </label>
              <label className="instrument-chart-settings-field">
                <span>Currency</span>
                <select value={effectiveCurrency} onChange={(event) => setSelectedCurrency(event.target.value)}>
                  {(availableCurrencies.length ? availableCurrencies : [effectiveCurrency]).map((currency) => (
                    <option key={currency} value={currency}>
                      {currency}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="instrument-chart-settings-control-group">
              <span>Chart Style</span>
              <div className="instrument-chart-settings-option-grid">
                {(['mountain', 'line', 'dot'] as ChartDisplayStyle[]).map((style) => (
                  <button
                    key={style}
                    type="button"
                    className={
                      chartDisplayStyle === style
                        ? 'instrument-chart-option instrument-chart-option-active'
                        : 'instrument-chart-option'
                    }
                    onClick={() => setChartDisplayStyle(style)}
                  >
                    {toTitleCase(style)}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="instrument-chart-settings-block instrument-chart-settings-block-data">
            <div className="instrument-chart-settings-block-head">
              <span>Events & Data</span>
              <strong>Chart overlays</strong>
            </div>
            <div className="instrument-chart-settings-option-grid">
              <button
                type="button"
                className={
                  showDividendEvents
                    ? 'instrument-chart-option instrument-chart-option-active'
                    : 'instrument-chart-option'
                }
                onClick={() => setShowDividendEvents((current) => !current)}
              >
                Dividends
              </button>
              <button
                type="button"
                className={
                  showTimelineNoteEvents
                    ? 'instrument-chart-option instrument-chart-option-active'
                    : 'instrument-chart-option'
                }
                onClick={() => setShowTimelineNoteEvents((current) => !current)}
              >
                Research Notes
              </button>
              <button
                type="button"
                className={
                  showDrawdownPanel
                    ? 'instrument-chart-option instrument-chart-option-active'
                    : 'instrument-chart-option'
                }
                onClick={() => setShowDrawdownPanel((current) => !current)}
              >
                Drawdown
              </button>
            </div>
          </section>
        </div>
      </div>
    ) : null

  const riskSettingsMenu = (
    <div className="instrument-chart-menu instrument-chart-settings-menu instrument-risk-settings-menu" ref={riskSettingsMenuRef}>
      <button
        type="button"
        className={
          riskSettingsOpen
            ? 'instrument-chart-settings-trigger instrument-chart-settings-trigger-active'
            : 'instrument-chart-settings-trigger'
        }
        aria-label="Rolling risk settings"
        onClick={() => setRiskSettingsOpen((current) => !current)}
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
      {riskSettingsOpen ? (
        <div className="instrument-chart-menu-panel instrument-chart-settings-panel instrument-risk-settings-panel">
          <div className="instrument-chart-settings-layout">
            <section className="instrument-chart-settings-block">
              <div className="instrument-chart-settings-block-head">
                <span>Window</span>
                <strong>{rollingRiskWindowLabel}</strong>
              </div>
              <div className="instrument-chart-settings-option-grid">
                {ROLLING_RISK_WINDOW_OPTIONS.map((option) => (
                  <button
                    key={option.months}
                    type="button"
                    className={
                      option.months === rollingRiskWindowMonths
                        ? 'instrument-chart-option instrument-chart-option-active'
                        : 'instrument-chart-option'
                    }
                    onClick={() =>
                      setRollingRiskSettings((current) => ({ ...current, windowMonths: option.months }))
                    }
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </section>

            <section className="instrument-chart-settings-block instrument-chart-settings-block-data">
              <div className="instrument-chart-settings-block-head">
                <span>Display</span>
                <strong>{toTitleCase(rollingRiskChartDisplayStyle)}</strong>
              </div>
              <div className="instrument-chart-settings-option-grid">
                {(['mountain', 'line', 'dot'] as ChartDisplayStyle[]).map((style) => (
                  <button
                    key={style}
                    type="button"
                    className={
                      rollingRiskChartDisplayStyle === style
                        ? 'instrument-chart-option instrument-chart-option-active'
                        : 'instrument-chart-option'
                    }
                    onClick={() =>
                      setRollingRiskSettings((current) => ({ ...current, chartDisplayStyle: style }))
                    }
                  >
                    {toTitleCase(style)}
                  </button>
                ))}
              </div>
            </section>
          </div>
        </div>
      ) : null}
    </div>
  )

  return (
    <div className="terminal-page instrument-detail-page">
      <section className="panel instrument-detail-shell">
        {loadWarning ? (
          <div className="inline-notice" role="status">
            {loadWarning}
          </div>
        ) : null}
        <div className="instrument-detail-topbar">
          <div className="instrument-detail-breadcrumbs">
            <a href={PLATFORM_HOME_URL} className="instrument-detail-backlink">
              Home
            </a>
            <span className="instrument-detail-breadcrumb-separator">/</span>
            <Link to="/watchlists" className="instrument-detail-backlink">
              Watchlist
            </Link>
            {watchlistContext?.watchlistId ? (
              <>
                <span className="instrument-detail-breadcrumb-separator">/</span>
                <Link
                  to={buildWatchlistPath(watchlistContext.watchlistId)}
                  className="instrument-detail-backlink"
                >
                  {watchlistContext.watchlistName || watchlistContext.watchlistId}
                </Link>
              </>
            ) : null}
            <span className="instrument-detail-breadcrumb-separator">/</span>
            <span className="instrument-detail-breadcrumb-current">{summary.ticker_or_isin}</span>
          </div>
          <div className="instrument-detail-actions">
            <button type="button" onClick={() => setSettingsModalOpen(true)}>
              {localize(language, SYSTEM_LABELS.settings)}
            </button>
            <button type="button">{localize(language, SYSTEM_LABELS.downloadPdf)}</button>
          </div>
        </div>
        <div className="instrument-detail-hero">
          <div className="instrument-detail-headline">
            <div className="instrument-detail-eyebrow">{detailPageLabel}</div>
            <h1 className="instrument-detail-title">
              {summary.fund_name} <span>{summary.ticker_or_isin}</span>
            </h1>
            <div className="instrument-detail-badges">
              {detailClassificationLabel ? (
                <span className="context-chip" data-portfolio-ops-i18n-ignore="true">
                  {localize(language, SYSTEM_LABELS.peer)}: {detailClassificationLabel}
                </span>
              ) : null}
              <span className="context-chip" data-portfolio-ops-i18n-ignore="true">
                {localize(language, SYSTEM_LABELS.basis)}: {navBasisLabel}
              </span>
              <span className="context-chip" data-portfolio-ops-i18n-ignore="true">
                Risk basis: {calculationFrequencyStatus}
              </span>
              <span className="context-chip" data-portfolio-ops-i18n-ignore="true">
                {localize(language, SYSTEM_LABELS.analystStance)}:{' '}
                {localizeSystemValue(summary.analyst_stance, language)}
              </span>
            </div>
          </div>
        </div>

        <div className="instrument-detail-tabs-row" data-portfolio-ops-i18n-ignore="true">
          <div className="instrument-detail-tabs">
            {availableTabs.map((tab) => (
              <button
                key={tab}
                type="button"
                className={tab === activeTab ? 'instrument-detail-tab instrument-detail-tab-active' : 'instrument-detail-tab'}
                onClick={() => setActiveTab(tab)}
              >
                {localize(language, TAB_LABELS[tab])}
              </button>
            ))}
          </div>
        </div>

        {sectionError ? <div className="inline-notice inline-notice-error">{sectionError}</div> : null}
        {sectionNotice ? <div className="inline-notice inline-notice-success">{sectionNotice}</div> : null}
        {sectionLoadErrors[activeTab] ? (
          <div className="inline-notice inline-notice-error instrument-section-load-error" role="alert">
            <span>{sectionLoadErrors[activeTab]}</span>
            <button
              type="button"
              onClick={() => setSectionRetryToken((current) => current + 1)}
            >
              {language === 'zh-Hans' ? '重试' : 'Retry'}
            </button>
          </div>
        ) : null}
        {loadingSectionKeys.has(`${detailBundleKey}:${activeTab}`) ? (
          <div className="inline-notice" role="status">
            Loading {localize(language, TAB_LABELS[activeTab]).toLowerCase()} data…
          </div>
        ) : null}
      </section>

      {settingsModalOpen ? (
        <div
          className="instrument-modal-backdrop"
          onClick={closeSettingsDialog}
        >
          <div
            ref={settingsDialogRef}
            className="instrument-modal instrument-settings-modal"
            role="dialog"
            aria-modal="true"
            aria-label={localize(language, SYSTEM_LABELS.settings)}
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="instrument-modal-header">
              <div>
                <div className="panel-title">{localize(language, SYSTEM_LABELS.settings)}</div>
                <div className="instrument-quote-source-title">{localize(language, SYSTEM_LABELS.taxonomySettings)}</div>
              </div>
              <div className="toolbar">
                <button
                  type="button"
                  disabled={savingSection === 'fund_settings'}
                  onClick={closeSettingsDialog}
                >
                  {localize(language, SYSTEM_LABELS.cancel)}
                </button>
                <button
                  type="button"
                  className="button-primary"
                  onClick={() => void handleSaveFundSettings()}
                  disabled={savingSection === 'fund_settings'}
                >
                  {savingSection === 'fund_settings'
                    ? localize(language, SYSTEM_LABELS.saving)
                    : localize(language, SYSTEM_LABELS.save)}
                </button>
              </div>
            </div>
            <div className="instrument-settings-body">
              <section className="instrument-settings-section">
                <div className="instrument-settings-section-header">
                  <div>
                    <div className="instrument-settings-title">{localize(language, SYSTEM_LABELS.classificationPath)}</div>
                    <div className="instrument-settings-current-path">
                      <span>{localize(language, SYSTEM_LABELS.currentPath)}</span>
                      <strong>{taxonomyPathLabel}</strong>
                    </div>
                  </div>
                  {taxonomyDraftNodeId ? (
                    <button
                      type="button"
                      className="instrument-settings-text-action"
                      onClick={() => setTaxonomyDraftNodeId('')}
                      disabled={!taxonomyTree}
                    >
                      {localize(language, SYSTEM_LABELS.unclassify)}
                    </button>
                  ) : null}
                </div>
                <div className="instrument-settings-taxonomy-stack">
                  {taxonomyTree ? (
                    taxonomyLevelSelectors.map((selector) => (
                      <label key={`taxonomy-level-${selector.levelIndex}`} className="instrument-settings-taxonomy-row">
                        <span className="instrument-settings-taxonomy-label">
                          {selector.levelIndex === 1
                            ? localize(language, SYSTEM_LABELS.regime)
                            : `${language === 'zh-Hans' ? '层级' : 'Level'} ${selector.levelIndex - 1}`}
                        </span>
                        <select
                          className="instrument-settings-taxonomy-select"
                          value={selector.selectedNodeId}
                          disabled={selector.disabled}
                          onChange={(event) =>
                            setTaxonomyDraftNodeId(event.target.value || selector.parentNodeId || '')
                          }
                        >
                          <option value="">
                            {selector.disabled
                              ? localize(language, SYSTEM_LABELS.selectParentFirst)
                              : selector.parentNodeId
                                ? localize(language, SYSTEM_LABELS.stopHere)
                                : localize(language, SYSTEM_LABELS.unclassified)}
                          </option>
                          {selector.options.map((node) => (
                            <option key={node.node_id} value={node.node_id}>
                              {localizeSystemValue(node.label, language)}
                            </option>
                          ))}
                        </select>
                      </label>
                    ))
                  ) : (
                    <div className="instrument-settings-loading">Loading taxonomy...</div>
                  )}
                </div>
              </section>
            </div>
          </div>
        </div>
      ) : null}

      {activeTab === 'overview' ? (
        <>
          {corporateActions.length ? (
            <section className="panel instrument-corporate-actions-panel">
              <div className="instrument-section-header">
                <div>
                  <div className="panel-title">Corporate Actions</div>
                  <div className="instrument-section-title">Unit adjustments</div>
                </div>
                <span className="muted">Adjusted series handles returns; confirmed events adjust portfolio units.</span>
              </div>
              <div className="table-shell">
                <table className="instrument-data-table">
                  <thead>
                    <tr>
                      <th>Effective</th>
                      <th>Record</th>
                      <th>Action</th>
                      <th>Ratio</th>
                      <th>Rounding</th>
                      <th>Status</th>
                      <th>Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...corporateActions].reverse().map((event) => (
                      <tr key={event.corporate_action_event_id}>
                        <td>{formatDate(event.effective_date)}</td>
                        <td>{formatDate(event.record_date)}</td>
                        <td>{event.action_type === 'share_split' ? 'Share split' : formatLabel(event.action_type)}</td>
                        <td>{String(event.new_units)} : {String(event.old_units)}</td>
                        <td>{formatLabel(event.quantity_rounding)}</td>
                        <td><span className={`status-badge status-${event.status}`}>{formatLabel(event.status)}</span></td>
                        <td>{formatLabel(event.source)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {corporateActions.some((event) => event.status === 'detected') ? (
                <div className="instrument-corporate-action-warning">
                  Detected events are informational only and never change portfolio quantities until issuer,
                  exchange, or depository evidence confirms ratio and fractional treatment.
                </div>
              ) : null}
            </section>
          ) : null}
          <section className="panel instrument-quote-panel">
            <div className="instrument-chart-shell">
              <div className="instrument-chart-header">
                <div className="instrument-quote-summary-main instrument-quote-summary-main-compact">
                  <div className="instrument-quote-summary-topline">
                    <div className="instrument-quote-primary-block">
                      <div className="instrument-quote-value">
                        {basisValue != null ? formatNumber(basisValue, 4) : '—'}
                      </div>
                      <div className={quoteToneClass}>
                        {formatChangeSummary(quoteChange, quoteChangePct)}
                      </div>
                    </div>
                    <div className="instrument-quote-rating-block">
                      <span>Rating</span>
                      <strong>{overviewRatingValue}</strong>
                      <em>{overviewRatingNote}</em>
                    </div>
                  </div>
                  <div className="instrument-quote-meta">
                    <div className="instrument-quote-asof">
                      As of {formatDate(latestSeriesPoint?.date || navSeries.rows[navSeries.rows.length - 1]?.as_of_date)}
                    </div>
                  </div>
                </div>
                <div className="instrument-quote-facts">
                  <div className="instrument-quote-facts-grid">
                    {overviewSideMetricRows.map((row) => (
                      <div key={row.label} className="instrument-quote-fact">
                        <span>{row.label}</span>
                        <strong>{row.value}</strong>
                        <em>{row.note}</em>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="instrument-chart-main">
                <div className="instrument-quote-control-bar">
                  <div className="instrument-quote-toolbar-primary">
                    {renderBenchmarkSearch('Compare benchmark')}
                  </div>
                </div>

                {quoteActionNotice ? <div className="instrument-quote-action-notice">{quoteActionNotice}</div> : null}

                <div className="instrument-chart-stage instrument-chart-stage-interactive">
                  {scaledVisibleSeries.length > 1 ? (
                    <>
                    <div className="instrument-chart-series-head">
                      <div className="instrument-series-legend">
                        <div className="instrument-series-label">
                          <strong>{summary.ticker_or_isin}</strong>
                          <span>{chartSeriesBasisLabel}</span>
                          <em className={`instrument-series-change-${getSignedMetricTone(chartQuotePeriodStats.changePct ?? chartQuotePeriodStats.change)}`}>
                            {formatChangeSummary(chartQuotePeriodStats.change, chartQuotePeriodStats.changePct)}
                          </em>
                        </div>
                        {selectedBenchmark ? (
                          <div className="instrument-series-label instrument-series-label-benchmark-row">
                            <strong>{selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name}</strong>
                            <span>Compare</span>
                            <em className={`instrument-series-change-${getSignedMetricTone(chartBenchmarkPeriodStats.changePct ?? chartBenchmarkPeriodStats.change)}`}>
                              {formatChangeSummary(chartBenchmarkPeriodStats.change, chartBenchmarkPeriodStats.changePct)}
                            </em>
                          </div>
                        ) : null}
                      </div>
                      <div className="instrument-chart-series-meta">
                        <span>{effectiveCurrency}</span>
                        <div className="instrument-chart-menu instrument-chart-settings-menu" ref={quoteChartMenuRef}>
                          <button
                            type="button"
                            className={
                              openQuoteChartMenu === 'settings'
                                ? 'instrument-chart-settings-trigger instrument-chart-settings-trigger-active'
                                : 'instrument-chart-settings-trigger'
                            }
                            aria-label="Chart settings"
                            onClick={() =>
                              setOpenQuoteChartMenu((current) => (current === 'settings' ? null : 'settings'))
                            }
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
                          {chartSettingsMenu}
                        </div>
                      </div>
                    </div>

                    <div className="instrument-chart-plot-shell">
                    <svg
                      viewBox={`0 0 ${PRIMARY_CHART_GEOMETRY.width} ${PRIMARY_CHART_GEOMETRY.height}`}
                      className="instrument-line-chart"
                      style={{ cursor: timelineNoteCaptureMode || isPrimaryHoverActive ? 'crosshair' : 'default' }}
                      role="img"
                      aria-label="Interactive NAV chart"
                      onMouseMove={(event) =>
                        updateChartHoverFromPointer(event, PRIMARY_CHART_GEOMETRY, 'primary')
                      }
                      onClick={handlePrimaryChartClick}
                      onContextMenu={handlePrimaryChartContextMenu}
                      onMouseLeave={clearChartHover}
                    >
                      <defs>
                        <clipPath id={chartClipId}>
                          <rect
                            x={primaryHoverPlotBounds.left}
                            y={primaryHoverPlotBounds.top}
                            width={primaryHoverPlotBounds.width}
                            height={primaryHoverPlotBounds.height}
                          />
                        </clipPath>
                      </defs>
                      {chartBands.map((band) => (
                        <rect
                          key={`band-${band.x.toFixed(2)}`}
                          className="instrument-chart-band"
                          x={band.x}
                          y={PRIMARY_CHART_GEOMETRY.paddingTop}
                          width={band.width}
                          height={
                            PRIMARY_CHART_GEOMETRY.height -
                            PRIMARY_CHART_GEOMETRY.paddingTop -
                            PRIMARY_CHART_GEOMETRY.paddingBottom
                          }
                        />
                      ))}

                      {chartTickValues.map((tick, index) => {
                        const projectedY = projectChartValue(
                          effectiveChartScale === 'logarithmic' ? Math.log10(tick) : tick,
                          chartMin,
                          chartMax,
                          PRIMARY_CHART_GEOMETRY,
                        )
                        const isBottomTick = index === 0
                        return (
                          <g key={`y-${tick.toFixed(6)}`}>
                            <line
                              className={
                                isBottomTick
                                  ? 'instrument-gridline instrument-gridline-axis-stub instrument-gridline-emphasis'
                                  : 'instrument-gridline instrument-gridline-axis-stub'
                              }
                              x1="8"
                              y1={projectedY}
                              x2={String(getYAxisStubEndX(PRIMARY_CHART_GEOMETRY))}
                              y2={projectedY}
                            />
                            <line
                              className={
                                isBottomTick
                                  ? 'instrument-gridline instrument-gridline-emphasis'
                                  : 'instrument-gridline'
                              }
                              x1={String(PRIMARY_CHART_GEOMETRY.paddingLeft)}
                              y1={projectedY}
                              x2={String(PRIMARY_CHART_GEOMETRY.width - PRIMARY_CHART_GEOMETRY.paddingRight)}
                              y2={projectedY}
                            />
                            <text
                              className="instrument-y-axis-label"
                              x={String(getYAxisStubEndX(PRIMARY_CHART_GEOMETRY))}
                              y={getYAxisLabelTextY(projectedY, PRIMARY_CHART_GEOMETRY, isBottomTick ? 'above' : 'below')}
                            >
                              {formatAxisNumber(tick)}
                            </text>
                          </g>
                        )
                      })}

                      {chartTickDates.map((tick, index) => {
                        return renderChartXAxisTick(
                          tick,
                          chartTickDates[index - 1] || null,
                          getPlotXFromRatio(PRIMARY_CHART_GEOMETRY, tick.xRatio, 0),
                          PRIMARY_CHART_GEOMETRY,
                          chartTickSpanDays,
                          'x',
                        )
                      })}

                      {chartDisplayStyle === 'mountain' ? <path d={chartAreaPath} className="instrument-line-area" /> : null}
                      {chartDisplayStyle !== 'dot' ? <path d={chartLinePath} className="instrument-line-path" /> : null}
                      {chartDisplayStyle === 'dot'
                        ? positionedChartPoints.map((point) => (
                            <circle
                              key={`dot-${point.date}`}
                              className="instrument-line-dot"
                              cx={point.x}
                              cy={point.y}
                              r="2.2"
                            />
                          ))
                        : null}
                      {scaledBenchmarkVisibleSeries.length > 1 ? (
                        <path d={benchmarkChartLinePath} className="instrument-line-path instrument-line-path-benchmark" />
                      ) : null}

                      {distributionMarkers.map((marker) => (
                        <g key={`dist-${marker.row.as_of_date}`}>
                          <circle
                            className="instrument-distribution-dot"
                            cx={marker.x}
                            cy={PRIMARY_CHART_GEOMETRY.height - PRIMARY_CHART_GEOMETRY.paddingBottom - 14}
                            r="7"
                          />
                          <text
                            className="instrument-distribution-label"
                            x={marker.x}
                            y={PRIMARY_CHART_GEOMETRY.height - PRIMARY_CHART_GEOMETRY.paddingBottom - 10.5}
                          >
                            D
                          </text>
                        </g>
                      ))}
                      {timelineNoteMarkerGroups.map((group) => (
                        <g
                          key={`note-${group.anchorDate}`}
                          className="instrument-chart-note-marker"
                          onClick={(event) => {
                            event.stopPropagation()
                            setTimelineNoteViewAnchorDate(group.anchorDate)
                            setChartTimelineNoteContextMenu(null)
                          }}
                        >
                          <circle
                            className="instrument-chart-note-dot"
                            cx={group.x}
                            cy={PRIMARY_CHART_GEOMETRY.height - PRIMARY_CHART_GEOMETRY.paddingBottom - 34}
                            r="7"
                          />
                          <text
                            className="instrument-chart-note-label"
                            x={group.x}
                            y={PRIMARY_CHART_GEOMETRY.height - PRIMARY_CHART_GEOMETRY.paddingBottom - 29.5}
                          >
                            ★
                          </text>
                          {group.notes.length > 1 ? (
                            <text
                              className="instrument-chart-note-count"
                              x={group.x + 8}
                              y={PRIMARY_CHART_GEOMETRY.height - PRIMARY_CHART_GEOMETRY.paddingBottom - 38}
                            >
                              {group.notes.length}
                            </text>
                          ) : null}
                        </g>
                      ))}

                      {primaryHoverGuideX != null ? (
                        <g clipPath={`url(#${chartClipId})`}>
                          <line
                            className="instrument-hover-line"
                            x1={primaryHoverGuideX}
                            y1={String(primaryHoverPlotBounds.top)}
                            x2={primaryHoverGuideX}
                            y2={String(primaryHoverPlotBounds.bottom)}
                          />
                          {isPrimaryHoverActive && chartHoverCursor ? (
                            <line
                              className="instrument-hover-line"
                              x1={String(primaryHoverPlotBounds.left)}
                              y1={chartHoverCursor.y}
                              x2={String(primaryHoverPlotBounds.right)}
                              y2={chartHoverCursor.y}
                            />
                          ) : null}
                          {isPrimaryHoverActive && hoveredChartPoint ? (
                            <circle
                              className="instrument-hover-point"
                              cx={hoveredChartPoint.x}
                              cy={hoveredChartPoint.y}
                              r="4"
                            />
                          ) : null}
                          {isPrimaryHoverActive && hoveredBenchmarkPoint ? (
                            <circle
                              className="instrument-hover-point instrument-hover-point-benchmark"
                              cx={hoveredBenchmarkPoint.x}
                              cy={hoveredBenchmarkPoint.y}
                              r="4"
                            />
                          ) : null}
                        </g>
                      ) : null}

                      {latestChartValueTag && latestChartValueLabel ? (
                        <g className="instrument-value-tag instrument-value-tag-primary">
                          <path d={latestChartValueTag.path} />
                          <text
                            x={latestChartValueTag.textX}
                            y={latestChartValueTag.textY}
                          >
                            {latestChartValueLabel}
                          </text>
                        </g>
                      ) : null}
                      {latestBenchmarkChartValueTag && latestBenchmarkChartValueLabel ? (
                        <g className="instrument-value-tag instrument-value-tag-benchmark">
                          <path d={latestBenchmarkChartValueTag.path} />
                          <text
                            x={latestBenchmarkChartValueTag.textX}
                            y={latestBenchmarkChartValueTag.textY}
                          >
                            {latestBenchmarkChartValueLabel}
                          </text>
                        </g>
                      ) : null}
                    </svg>
                    {primaryTooltipAnchor && hoveredNavPoint ? (
                      <div className="instrument-chart-tooltip-layer">
                        <div className="instrument-chart-tooltip" style={primaryTooltipAnchor}>
                          <div className="instrument-chart-tooltip-date">
                            {formatDate(hoveredNavPoint.date)}
                          </div>
                          <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-primary">
                            <span className="instrument-chart-tooltip-series-label">
                              <i className="instrument-chart-tooltip-swatch" />
                                {chartSeriesBasisLabel}
                            </span>
                            <strong>{formatNumber(hoverNavValue, 4)}</strong>
                          </div>
                          {selectedBenchmark && hoverBenchmarkValue != null ? (
                            <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-benchmark">
                              <span className="instrument-chart-tooltip-series-label">
                                <i className="instrument-chart-tooltip-swatch" />
                                {selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name}
                              </span>
                              <strong>{formatNumber(hoverBenchmarkValue, 4)}</strong>
                            </div>
                          ) : null}
                          {showDrawdownPanel ? (
                            <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-primary">
                              <span className="instrument-chart-tooltip-series-label">
                                <i className="instrument-chart-tooltip-swatch" />
                                Drawdown
                              </span>
                              <strong>{formatPercent(hoverDrawdownValue)}</strong>
                            </div>
                          ) : null}
                          {selectedBenchmark && hoverBenchmarkDrawdownValue != null && showDrawdownPanel ? (
                            <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-benchmark">
                              <span className="instrument-chart-tooltip-series-label">
                                <i className="instrument-chart-tooltip-swatch" />
                                {selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name} Drawdown
                              </span>
                              <strong>{formatPercent(hoverBenchmarkDrawdownValue)}</strong>
                            </div>
                          ) : null}
                          {hoveredDistribution ? (
                            <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-event">
                              <span className="instrument-chart-tooltip-series-label">
                                <i className="instrument-chart-tooltip-swatch" />
                                Dividend
                              </span>
                              <strong>{formatNumber(hoveredDistribution.distribution_amount, 4)}</strong>
                            </div>
                          ) : null}
                          {hoveredTimelineNoteGroup?.notes.length ? (
                            <div className="instrument-chart-tooltip-events">
                              {hoveredTimelineNoteGroup.notes.slice(0, 3).map((note) => (
                                <div
                                  key={note.note_id}
                                  className="instrument-chart-tooltip-row instrument-chart-tooltip-row-note"
                                >
                                  <span className="instrument-chart-tooltip-series-label">
                                    <i className="instrument-chart-tooltip-swatch" />
                                    {note.title || note.summary || 'Research note'}
                                  </span>
                                  <strong>{formatTimelineNoteImportance(note.importance)}</strong>
                                </div>
                              ))}
                              {hoveredTimelineNoteGroup.notes.length > 3 ? (
                                <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-note instrument-chart-tooltip-row-note-more">
                                  <span>{`+${hoveredTimelineNoteGroup.notes.length - 3} more notes`}</span>
                                </div>
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    ) : null}
                    </div>

                    {chartTimelineNoteContextMenu ? (
                      <div
                        ref={timelineNoteContextMenuRef}
                        className="instrument-chart-context-menu"
                        style={timelineNoteContextMenuStyle}
                      >
                        <button
                          type="button"
                          className="instrument-chart-context-menu-item"
                          onClick={() => openTimelineNoteEditor(chartTimelineNoteContextMenu.anchorDate)}
                        >
                          Add note at {formatDate(chartTimelineNoteContextMenu.anchorDate)}
                        </button>
                        {timelineNoteMarkerGroups.some(
                          (group) => group.anchorDate === chartTimelineNoteContextMenu.anchorDate,
                        ) ? (
                          <button
                            type="button"
                            className="instrument-chart-context-menu-item"
                            onClick={() => {
                              setTimelineNoteViewAnchorDate(chartTimelineNoteContextMenu.anchorDate)
                              setChartTimelineNoteContextMenu(null)
                            }}
                          >
                            View notes on this date
                          </button>
                        ) : null}
                      </div>
                    ) : null}

                    {selectedTimelineNoteGroup?.notes.length ? (
                      <div className="instrument-chart-note-panel">
                        <div className="instrument-chart-note-panel-header">
                          <div>
                            <strong>Research Notes</strong>
                            <span>{formatDate(selectedTimelineNoteGroup.anchorDate)}</span>
                          </div>
                          <div className="toolbar">
                            <button
                              type="button"
                              onClick={() => openTimelineNoteEditor(selectedTimelineNoteGroup.anchorDate)}
                            >
                              Add Note
                            </button>
                            <button
                              type="button"
                              onClick={() => setTimelineNoteViewAnchorDate(null)}
                            >
                              Close
                            </button>
                          </div>
                        </div>
                        <div className="table-shell instrument-research-table-shell">
                          <table className="terminal-table terminal-table-compact instrument-research-table instrument-chart-note-table">
                            <thead>
                              <tr>
                                <th>Date</th>
                                <th>Importance</th>
                                <th>Title</th>
                                <th>Summary</th>
                                <th className="instrument-table-action-col" aria-label="Note actions" />
                              </tr>
                            </thead>
                            <tbody>
                              {selectedTimelineNoteGroup.notes.map((note) => (
                                <tr key={note.note_id}>
                                  <td>{formatDate(note.note_date)}</td>
                                  <td>{formatTimelineNoteImportance(note.importance)}</td>
                                  <td>{note.title || 'Untitled'}</td>
                                  <td>{note.summary || note.body || '—'}</td>
                                  <td className="instrument-table-row-action-cell">
                                    <div className="instrument-table-inline-actions instrument-table-inline-actions-compact">
                                      <button
                                        type="button"
                                        className="table-action"
                                        onClick={() => openTimelineNoteEditor(note.note_date, note)}
                                      >
                                        Edit
                                      </button>
                                      <button
                                        type="button"
                                        className="table-action"
                                        onClick={() => void handleDeleteTimelineNote(note.note_id)}
                                        disabled={savingSection === 'timeline_note'}
                                      >
                                        Delete
                                      </button>
                                    </div>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    ) : null}

                    {showDrawdownPanel ? (
                      <div className="instrument-drawdown-shell">
                        <div className="instrument-drawdown-header">
                          <span>Drawdown</span>
                          <div className="instrument-drawdown-header-values">
                            <strong className="instrument-drawdown-header-value instrument-drawdown-header-value-primary">
                              {hoverDrawdownValue == null ? '—' : `${formatPercent(hoverDrawdownValue)}`}
                            </strong>
                            {selectedBenchmark ? (
                              <strong className="instrument-drawdown-header-value instrument-drawdown-header-value-benchmark">
                                {hoverBenchmarkDrawdownValue == null ? '—' : `${formatPercent(hoverBenchmarkDrawdownValue)}`}
                              </strong>
                            ) : null}
                          </div>
                        </div>
                        <div className="instrument-chart-plot-shell instrument-chart-plot-shell-drawdown">
                        <svg
                          viewBox={`0 0 ${DRAWDOWN_CHART_GEOMETRY.width} ${DRAWDOWN_CHART_GEOMETRY.height}`}
                          className="instrument-drawdown-chart"
                          style={{ cursor: isDrawdownHoverActive ? 'crosshair' : 'default' }}
                          role="img"
                          aria-label="Drawdown chart"
                          onMouseMove={(event) =>
                            updateChartHoverFromPointer(event, DRAWDOWN_CHART_GEOMETRY, 'drawdown')
                          }
                          onMouseLeave={clearChartHover}
                        >
                          <defs>
                            <clipPath id={drawdownClipId}>
                              <rect
                                x={drawdownHoverPlotBounds.left}
                                y={drawdownHoverPlotBounds.top}
                                width={drawdownHoverPlotBounds.width}
                                height={drawdownHoverPlotBounds.height}
                              />
                            </clipPath>
                          </defs>
                          {drawdownBands.map((band) => (
                            <rect
                              key={`drawdown-band-${band.x.toFixed(2)}`}
                              className="instrument-chart-band"
                              x={band.x}
                              y={DRAWDOWN_CHART_GEOMETRY.paddingTop}
                              width={band.width}
                              height={
                                DRAWDOWN_CHART_GEOMETRY.height -
                                DRAWDOWN_CHART_GEOMETRY.paddingTop -
                                DRAWDOWN_CHART_GEOMETRY.paddingBottom
                              }
                            />
                          ))}
                          {drawdownTickValues.map((tick, index) => {
                            const projectedY = projectChartValue(
                              tick,
                              drawdownMin,
                              drawdownMax,
                              DRAWDOWN_CHART_GEOMETRY,
                            )
                            const isBottomTick = index === 0
                            return (
                              <g key={`dd-${tick.toFixed(6)}`}>
                                <line
                                  className={
                                    isBottomTick
                                      ? 'instrument-gridline instrument-gridline-axis-stub instrument-gridline-emphasis'
                                      : 'instrument-gridline instrument-gridline-axis-stub'
                                  }
                                  x1="8"
                                  y1={projectedY}
                                  x2={String(getYAxisStubEndX(DRAWDOWN_CHART_GEOMETRY))}
                                  y2={projectedY}
                                />
                                <line
                                  className={
                                    isBottomTick
                                      ? 'instrument-gridline instrument-gridline-emphasis'
                                      : 'instrument-gridline'
                                  }
                                  x1={String(DRAWDOWN_CHART_GEOMETRY.paddingLeft)}
                                  y1={projectedY}
                                  x2={String(DRAWDOWN_CHART_GEOMETRY.width - DRAWDOWN_CHART_GEOMETRY.paddingRight)}
                                  y2={projectedY}
                                />
                                <text
                                  className="instrument-y-axis-label"
                                  x={String(getYAxisStubEndX(DRAWDOWN_CHART_GEOMETRY))}
                                  y={getYAxisLabelTextY(
                                    projectedY,
                                    DRAWDOWN_CHART_GEOMETRY,
                                    isBottomTick ? 'above' : 'below',
                                  )}
                                >
                                  {formatPercent(tick, 1)}
                                </text>
                              </g>
                            )
                          })}
                          <path d={drawdownAreaPath} className="instrument-drawdown-area" />
                          <path d={drawdownLinePath} className="instrument-drawdown-line" />
                          {benchmarkDrawdownSeries.length > 1 ? (
                            <path
                              d={benchmarkDrawdownLinePath}
                              className="instrument-drawdown-line instrument-drawdown-line-benchmark"
                            />
                          ) : null}
                          {drawdownHoverGuideX != null ? (
                            <g clipPath={`url(#${drawdownClipId})`}>
                              <line
                                className="instrument-hover-line instrument-hover-line-drawdown"
                                x1={drawdownHoverGuideX}
                                y1={String(drawdownHoverPlotBounds.top)}
                                x2={drawdownHoverGuideX}
                                y2={String(drawdownHoverPlotBounds.bottom)}
                              />
                              {isDrawdownHoverActive && chartHoverCursor ? (
                                <line
                                  className="instrument-hover-line instrument-hover-line-drawdown"
                                  x1={String(drawdownHoverPlotBounds.left)}
                                  y1={chartHoverCursor.y}
                                  x2={String(drawdownHoverPlotBounds.right)}
                                  y2={chartHoverCursor.y}
                                />
                              ) : null}
                              {isDrawdownHoverActive && hoveredDrawdownPoint ? (
                                <circle
                                  className="instrument-hover-point instrument-hover-point-drawdown"
                                  cx={hoveredDrawdownPoint.x}
                                  cy={hoveredDrawdownPoint.y}
                                  r="3.5"
                                />
                              ) : null}
                              {isDrawdownHoverActive && hoveredBenchmarkDrawdownPoint ? (
                                <circle
                                  className="instrument-hover-point instrument-hover-point-benchmark"
                                  cx={hoveredBenchmarkDrawdownPoint.x}
                                  cy={hoveredBenchmarkDrawdownPoint.y}
                                  r="3.5"
                                />
                              ) : null}
                            </g>
                          ) : null}

                          {latestDrawdownValueTag && latestDrawdownValueLabel ? (
                            <g className="instrument-value-tag instrument-value-tag-drawdown">
                              <path d={latestDrawdownValueTag.path} />
                              <text
                                x={latestDrawdownValueTag.textX}
                                y={latestDrawdownValueTag.textY}
                              >
                                {latestDrawdownValueLabel}
                              </text>
                            </g>
                          ) : null}
                          {latestBenchmarkDrawdownValueTag && latestBenchmarkDrawdownValueLabel ? (
                            <g className="instrument-value-tag instrument-value-tag-benchmark">
                              <path d={latestBenchmarkDrawdownValueTag.path} />
                              <text
                                x={latestBenchmarkDrawdownValueTag.textX}
                                y={latestBenchmarkDrawdownValueTag.textY}
                              >
                                {latestBenchmarkDrawdownValueLabel}
                              </text>
                            </g>
                          ) : null}
                        </svg>
                        {drawdownTooltipAnchor && hoveredNavPoint ? (
                          <div className="instrument-chart-tooltip-layer">
                            <div className="instrument-chart-tooltip" style={drawdownTooltipAnchor}>
                              <div className="instrument-chart-tooltip-date">
                                {formatDate(hoveredNavPoint.date)}
                              </div>
                              <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-primary">
                                <span className="instrument-chart-tooltip-series-label">
                                  <i className="instrument-chart-tooltip-swatch" />
                                    {chartSeriesBasisLabel}
                                </span>
                                <strong>{formatNumber(hoverNavValue, 4)}</strong>
                              </div>
                              {selectedBenchmark && hoverBenchmarkValue != null ? (
                                <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-benchmark">
                                  <span className="instrument-chart-tooltip-series-label">
                                    <i className="instrument-chart-tooltip-swatch" />
                                    {selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name}
                                  </span>
                                  <strong>{formatNumber(hoverBenchmarkValue, 4)}</strong>
                                </div>
                              ) : null}
                              {showDrawdownPanel ? (
                                <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-primary">
                                  <span className="instrument-chart-tooltip-series-label">
                                    <i className="instrument-chart-tooltip-swatch" />
                                    Drawdown
                                  </span>
                                  <strong>{formatPercent(hoverDrawdownValue)}</strong>
                                </div>
                              ) : null}
                              {selectedBenchmark && hoverBenchmarkDrawdownValue != null && showDrawdownPanel ? (
                                <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-benchmark">
                                  <span className="instrument-chart-tooltip-series-label">
                                    <i className="instrument-chart-tooltip-swatch" />
                                    {selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name} Drawdown
                                  </span>
                                  <strong>{formatPercent(hoverBenchmarkDrawdownValue)}</strong>
                                </div>
                              ) : null}
                              {hoveredDistribution ? (
                                <div className="instrument-chart-tooltip-row instrument-chart-tooltip-row-event">
                                  <span className="instrument-chart-tooltip-series-label">
                                    <i className="instrument-chart-tooltip-swatch" />
                                    Dividend
                                  </span>
                                  <strong>{formatNumber(hoveredDistribution.distribution_amount, 4)}</strong>
                                </div>
                              ) : null}
                            </div>
                          </div>
                        ) : null}
                        </div>
                      </div>
                    ) : null}
                    {canUseZoom ? (
                      <div className="instrument-chart-zoom">
                        <div className="instrument-chart-zoom-meta">
                          <span>Period</span>
                          <strong>
                            {formatDate(effectiveStartDate)} - {formatDate(effectiveEndDate)}
                          </strong>
                        </div>
                        <div className="instrument-chart-zoom-track">
                          <div
                            className="instrument-chart-zoom-selection"
                            style={{
                              left: `${zoomSelectionLeftPct}%`,
                              right: `${zoomSelectionRightPct}%`,
                            }}
                          />
                          <input
                            type="range"
                            min={0}
                            max={zoomMaxIndex}
                            value={zoomStartIndex}
                            aria-label="Chart period start"
                            onChange={(event) => {
                              const nextIndex = Math.min(Number(event.target.value), zoomEndIndex - 1)
                              const nextPoint = navBasisSeries[Math.max(nextIndex, 0)]
                              if (nextPoint) {
                                setChartRange('CUSTOM')
                                setChartStartDate(nextPoint.date)
                              }
                            }}
                          />
                          <input
                            type="range"
                            min={0}
                            max={zoomMaxIndex}
                            value={zoomEndIndex}
                            aria-label="Chart period end"
                            onChange={(event) => {
                              const nextIndex = Math.max(Number(event.target.value), zoomStartIndex + 1)
                              const nextPoint = navBasisSeries[Math.min(nextIndex, zoomMaxIndex)]
                              if (nextPoint) {
                                setChartRange('CUSTOM')
                                setChartEndDate(nextPoint.date)
                              }
                            }}
                          />
                        </div>
                      </div>
                    ) : null}
                    </>
                  ) : (
                    <div className="instrument-placeholder">
                      {navSeries.rows.length
                        ? `${quoteBasisLabel} ${localize(language, SYSTEM_LABELS.unavailableBasis)}`
                        : localize(language, SYSTEM_LABELS.noNavHistory)}
                    </div>
                  )}
                </div>
              </div>

            </div>
          </section>

          {timelineNoteDraft ? (
            <div
              className="instrument-modal-backdrop"
              onClick={closeTimelineNoteDialog}
            >
              <div
                ref={timelineNoteDialogRef}
                className="instrument-modal instrument-timeline-note-modal"
                role="dialog"
                aria-modal="true"
                aria-label="Timeline Note"
                tabIndex={-1}
                onClick={(event) => event.stopPropagation()}
              >
                <div className="instrument-modal-header">
                  <div>
                    <div className="panel-title">Research Note</div>
                    <div className="instrument-quote-source-title">
                      Anchored to {formatDate(timelineNoteDraft.note_date)}
                    </div>
                  </div>
                  <div className="toolbar">
                    {timelineNotes.some((note) => note.note_id === timelineNoteDraft.note_id) ? (
                      <button
                        type="button"
                        onClick={() => void handleDeleteTimelineNote(timelineNoteDraft.note_id)}
                        disabled={savingSection === 'timeline_note'}
                      >
                        Delete
                      </button>
                    ) : null}
                    <button
                      type="button"
                      disabled={savingSection === 'timeline_note'}
                      onClick={closeTimelineNoteDialog}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      onClick={() => void handleSaveTimelineNote()}
                      disabled={savingSection === 'timeline_note'}
                    >
                      {savingSection === 'timeline_note' ? 'Saving...' : 'Save Note'}
                    </button>
                  </div>
                </div>
                <div className="form-grid form-grid-2 instrument-quote-source-grid">
                  <label className="form-field">
                    <span>Date</span>
                    <input
                      type="date"
                      value={timelineNoteDraft.note_date}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                note_date: event.target.value,
                              }
                            : current,
                        )
                      }
                    />
                  </label>
                  <label className="form-field">
                    <span>Importance</span>
                    <select
                      value={timelineNoteDraft.importance}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                importance: parseTimelineNoteImportance(event.target.value),
                              }
                            : current,
                        )
                      }
                    >
                      <option value="low">Low</option>
                      <option value="medium">Medium</option>
                      <option value="high">High</option>
                    </select>
                  </label>
                  <label className="form-field detail-span-2">
                    <span>Title</span>
                    <input
                      value={timelineNoteDraft.title}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                title: event.target.value,
                              }
                            : current,
                        )
                      }
                      placeholder="Brief headline for what mattered on this date"
                    />
                  </label>
                  <label className="form-field detail-span-2">
                    <span>Summary</span>
                    <textarea
                      rows={3}
                      value={timelineNoteDraft.summary}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                summary: event.target.value,
                              }
                            : current,
                        )
                      }
                      placeholder="Short takeaway shown in the chart tooltip."
                    />
                  </label>
                  <label className="form-field detail-span-2">
                    <span>Body</span>
                    <textarea
                      rows={6}
                      value={timelineNoteDraft.body}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                body: event.target.value,
                              }
                            : current,
                        )
                      }
                      placeholder="Longer context, supporting evidence, or follow-up items."
                    />
                  </label>
                  <label className="form-field detail-span-2">
                    <span>Tags</span>
                    <input
                      value={timelineNoteDraft.tagsText}
                      onChange={(event) =>
                        setTimelineNoteDraft((current) =>
                          current
                            ? {
                                ...current,
                                tagsText: event.target.value,
                              }
                            : current,
                        )
                      }
                      placeholder="event, manager change, liquidity, drawdown"
                    />
                  </label>
                </div>
              </div>
            </div>
          ) : null}

        </>
      ) : null}

      {activeTab === 'performance' ? (
        <section className="panel instrument-performance-shell">
          <div className="instrument-price-topline" />

          <section className="instrument-performance-section instrument-performance-section-metrics">
            <div className="instrument-performance-section-header">
              <div className="instrument-performance-title-group">
                <div className="panel-title">Performance</div>
                <div className="instrument-performance-title-row">
                  <div className="instrument-section-title">Metrics Matrix</div>
                  {renderBenchmarkSearch('Performance benchmark', 'instrument-performance-benchmark-select')}
                </div>
              </div>
              <div className="instrument-performance-matrix-controls">
                <div className="instrument-performance-view-toggle" role="group" aria-label="Metrics matrix view">
                  {PERFORMANCE_MATRIX_MODE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={option.value === activePerformanceMatrixMode ? 'instrument-performance-toggle-active' : undefined}
                      disabled={option.value !== 'values' && !peerComparison}
                      onClick={() => setPerformanceMatrixMode(option.value)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="instrument-performance-section-body">
              <div className="table-shell instrument-performance-table-shell">
                <table className="terminal-table terminal-table-compact instrument-metrics-table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      {PERFORMANCE_METRIC_PERIODS.map((period) => (
                        <th key={period.key}>{period.label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {performanceMetricMatrixRows.map((row) => (
                      <tr
                        key={row.key}
                        className={
                          row.supportsBenchmark
                            ? 'instrument-metrics-row-with-note'
                            : 'instrument-metrics-row-single'
                        }
                      >
                        <td className="instrument-metrics-row-label">{row.label}</td>
                        {row.cells.map((cell, index) => (
                          <td key={`${row.key}-${PERFORMANCE_METRIC_PERIODS[index]?.key || index}`}>
                            <div
                              className={`instrument-metrics-cell${
                                'tone' in cell ? ` instrument-metrics-cell-${cell.tone}` : ''
                              }`}
                            >
                              <strong>{cell.primary}</strong>
                              {cell.secondary ? (
                                <span className="instrument-metrics-cell-note">{cell.secondary}</span>
                              ) : null}
                            </div>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="instrument-performance-section instrument-performance-section-monthly">
            <div className="instrument-performance-section-header">
              <div>
                <div className="panel-title">Performance</div>
                <div className="instrument-section-title">Monthly Return Matrix</div>
              </div>
            </div>
            <div className="instrument-performance-section-body">
              {monthlyReturnMatrixRows.length ? (
                <div className="table-shell instrument-performance-table-shell">
                  <table className="terminal-table terminal-table-compact instrument-heatmap-table">
                    <thead>
                      <tr>
                        <th>Year</th>
                        {MONTH_SHORT_LABELS.map((label) => (
                          <th key={label}>{label}</th>
                        ))}
                        <th>Yearly / YTD</th>
                      </tr>
                    </thead>
                    <tbody>
                      {monthlyReturnMatrixRows.map((row) => (
                        <tr key={row.year}>
                          <td className="instrument-heatmap-row-label">{row.year}</td>
                          {row.months.map((value, index) => (
                            <td
                              key={`${row.year}-${MONTH_SHORT_LABELS[index]}`}
                              className={`instrument-heatmap-cell${value == null ? ' instrument-heatmap-cell-empty' : ''}`}
                              style={getHeatmapCellStyle(value, monthlyReturnMatrixMaxAbs)}
                            >
                              {value == null ? '—' : formatPercent(value, 2)}
                            </td>
                          ))}
                          <td
                            className={`instrument-heatmap-cell${row.ytd == null ? ' instrument-heatmap-cell-empty' : ''}`}
                            style={getHeatmapCellStyle(row.ytd, monthlyReturnMatrixMaxAbs)}
                          >
                            {row.ytd == null ? '—' : formatPercent(row.ytd, 2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="instrument-fallback-block">
                  <div className="instrument-fallback-copy">
                    Monthly return matrix will appear once month-end total-return NAV history is available.
                  </div>
                </div>
              )}
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'risk' ? (
        <section className="panel instrument-risk-shell">
          <div className="instrument-price-topline" />
          <section className="instrument-risk-section instrument-risk-section-rolling">
            <div className="instrument-risk-section-header instrument-risk-rolling-header">
              <div className="instrument-performance-title-group">
                <div className="panel-title">Risk</div>
                <div className="instrument-performance-title-row">
                  <div className="instrument-section-title">Rolling Risk</div>
                  {renderBenchmarkSearch('Risk benchmark', 'instrument-performance-benchmark-select')}
                </div>
              </div>
              <div className="instrument-risk-section-actions">
                {riskSettingsMenu}
              </div>
            </div>
            <div className="instrument-risk-section-body">
              <div className="instrument-risk-visual-grid instrument-risk-rolling-grid">
                <WatchlistRollingRiskMetricChart
                  title="Annualized Volatility"
                  points={rollingVolatilitySeries}
                  benchmarkPoints={benchmarkRollingVolatilitySeries}
                  benchmarkLabel={selectedBenchmark ? selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name : null}
                  displayStyle={rollingRiskChartDisplayStyle}
                  formatValue={(value) => formatPercent(value)}
                  emptyLabel={`Insufficient ${rollingRiskWindowLabel} total-return NAV history.`}
                />
                <WatchlistRollingRiskMetricChart
                  title="Sharpe Ratio"
                  points={rollingSharpeSeries}
                  benchmarkPoints={benchmarkRollingSharpeSeries}
                  benchmarkLabel={selectedBenchmark ? selectedBenchmark.ticker_or_isin || selectedBenchmark.fund_name : null}
                  displayStyle={rollingRiskChartDisplayStyle}
                  formatValue={(value) => formatNumber(value, 2)}
                  emptyLabel={`Insufficient ${rollingRiskWindowLabel} total-return NAV history.`}
                />
              </div>
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'price' ? (
        <section className="panel instrument-price-shell instrument-edit-surface">
          <div className="instrument-price-topline" />

          <section className="instrument-price-section">
            <div className="instrument-price-section-header">
              <div>
                <div className="instrument-section-title">Expense Ratios &amp; Fees</div>
              </div>
              <div className="toolbar">
                {isEditingPrice ? (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setPriceDraft(toEditablePriceDraft(bundle.price))
                        setEditingPriceSection(null)
                        setSectionError(null)
                        setSectionNotice(null)
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      onClick={() => void handleSavePrice()}
                      disabled={savingSection === 'price'}
                    >
                      {savingSection === 'price' ? 'Saving...' : 'Save'}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setPriceDraft(toEditablePriceDraft(bundle.price))
                      setEditingPriceSection('table')
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
            <div className="instrument-price-section-body">
              <div className="table-shell instrument-price-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table instrument-price-schedule-table">
                  <tbody>
                    <tr>
                      <td className="instrument-price-schedule-label">Adjusted Expense Ratio</td>
                      <td className="instrument-price-schedule-value">
                        {renderPriceOverviewValue('adjusted_expense_ratio', adjustedExpenseRatio)}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-price-schedule-label">Reported Expense Ratio</td>
                      <td className="instrument-price-schedule-value">
                        {renderPriceOverviewValue('total_expense_ratio', reportedExpenseRatio)}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-price-schedule-label">Management Fee</td>
                      <td className="instrument-price-schedule-value">
                        {renderPriceOverviewValue('management_fee', feesAndTermsRows[0]?.value || '—')}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-price-schedule-label">Interest Expense Fees</td>
                      <td className="instrument-price-schedule-value">
                        {renderPriceOverviewValue('interest_expense_fees', feesAndTermsRows[1]?.value || '—')}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-price-schedule-label">Redemption Fee</td>
                      <td className="instrument-price-schedule-value">
                        {renderPriceOverviewValue('redemption_fee', feesAndTermsRows[2]?.value || '—')}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-price-schedule-label">Minimum Initial Investment</td>
                      <td className="instrument-price-schedule-value">
                        {renderPriceOverviewValue('minimum_initial_investment', feesAndTermsRows[3]?.value || '—')}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-price-schedule-label">Distribution Policy</td>
                      <td
                        className={
                          isEditingPrice
                            ? 'instrument-price-schedule-value instrument-price-schedule-prose'
                            : 'instrument-price-schedule-value instrument-data-table-prose instrument-price-schedule-prose'
                        }
                      >
                        {renderPriceTextValue(
                          priceDraft?.distribution_policy ?? '',
                          (value) =>
                            setPriceDraft((current) =>
                              current ? { ...current, distribution_policy: value } : current,
                            ),
                          pricePolicyRows[0].value,
                          2,
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-price-schedule-label">Policy Text</td>
                      <td
                        className={
                          isEditingPrice
                            ? 'instrument-price-schedule-value instrument-price-schedule-prose'
                            : 'instrument-price-schedule-value instrument-data-table-prose instrument-price-schedule-prose'
                        }
                      >
                        {renderPriceTextValue(
                          priceDraft?.policy_text ?? '',
                          (value) =>
                            setPriceDraft((current) => (current ? { ...current, policy_text: value } : current)),
                          pricePolicyRows[1].value,
                          5,
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-price-schedule-label">Fee Notes</td>
                      <td className="instrument-price-schedule-value instrument-price-schedule-prose">
                        {isEditingPrice && priceDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={4}
                            value={listRowsToTextareaValue(priceDraft.feeNoteRows)}
                            onChange={(event) =>
                              setPriceDraft((current) =>
                                current
                                  ? {
                                      ...current,
                                      feeNoteRows: textareaValueToListRows('price-fee-note', event.target.value),
                                    }
                                  : current,
                              )
                            }
                          />
                        ) : (
                          formatPriceListValue(price.fee_notes)
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-price-schedule-label">Notes</td>
                      <td className="instrument-price-schedule-value instrument-price-schedule-prose">
                        {isEditingPrice && priceDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={4}
                            value={listRowsToTextareaValue(priceDraft.noteRows)}
                            onChange={(event) =>
                              setPriceDraft((current) =>
                                current
                                  ? { ...current, noteRows: textareaValueToListRows('price-note', event.target.value) }
                                  : current,
                              )
                            }
                          />
                        ) : (
                          formatPriceListValue(price.notes)
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'exposure' ? (
        <section className="detail-grid">
          <section className="panel">
            <div className="instrument-section-header">
              <div>
                <div className="panel-title">Exposure</div>
                <div className="instrument-section-title">Allocation</div>
              </div>
            </div>
            <div className="stack-list">
              {getRows(portfolio.allocation_blocks.asset_allocation).length ? (
                getRows(portfolio.allocation_blocks.asset_allocation).map((item, index) => (
                  <div key={`${String(item.name)}-${index}`} className="stack-item">
                    <span>{getString(item.name)}</span>
                    <strong>{formatPercent(getNumber(item.investment))}</strong>
                  </div>
                ))
              ) : (
                <div className="instrument-placeholder">
                  Exposure analytics will be rebuilt by product type later.
                </div>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="instrument-section-header">
              <div>
                <div className="panel-title">Exposure</div>
                <div className="instrument-section-title">Style & Holdings</div>
              </div>
            </div>
            <div className="stack-list">
              {portfolio.style_box ? renderStackRows(portfolio.style_box) : null}
              {portfolio.holdings_summary ? renderStackRows(portfolio.holdings_summary) : null}
              {!portfolio.style_box && !portfolio.holdings_summary ? (
                <div className="instrument-placeholder">No exposure summary available.</div>
              ) : null}
            </div>
          </section>

          <section className="panel detail-span-2">
            <div className="instrument-section-header">
              <div>
                <div className="panel-title">Holdings</div>
                <div className="instrument-section-title">Current Positions</div>
              </div>
            </div>
            <div className="table-shell">
              <table className="terminal-table terminal-table-compact">
                <thead>
                  <tr>
                    <th>Holding</th>
                    <th>Issuer</th>
                    <th>Weight</th>
                    <th>Market Value</th>
                    <th>Change</th>
                    <th>Maturity</th>
                    <th>Rating</th>
                    <th>Eff. Dur.</th>
                    <th>YTW</th>
                    <th>Sector</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.rows.length ? (
                    holdings.rows.slice(0, 12).map((row, index) => (
                      <tr key={`${String(row.holding_name)}-${index}`}>
                        <td>{getString(row.holding_name)}</td>
                        <td>{getString(row.issuer_name)}</td>
                        <td>{formatPercent(getNumber(row.portfolio_weight))}</td>
                        <td>{formatCompactCurrency(getNumber(row.market_value))}</td>
                        <td>{formatPercent(getNumber(row.share_change_pct))}</td>
                        <td>{formatDate(row.maturity_date)}</td>
                        <td>{getString(row.credit_rating)}</td>
                        <td>{formatNumber(getNumber(row.effective_duration))}</td>
                        <td>{formatPercent(getNumber(row.yield_to_worst))}</td>
                        <td>{getString(row.sector)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={10} className="empty-state">
                        No holdings available.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'people' ? (
        <section className="instrument-people-shell instrument-edit-surface">
          <div className="instrument-price-topline" />

          <section className="instrument-people-section">
            <div className="instrument-people-section-header">
              <div>
                <div className="instrument-section-title">People</div>
              </div>
              <div className="toolbar">
                {editingPeople ? (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setPeopleDraft(toEditablePeopleDraft(bundle!.people))
                        setEditingPeople(false)
                        setSectionError(null)
                        setSectionNotice(null)
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      onClick={() => void handleSavePeople()}
                      disabled={savingSection === 'people'}
                    >
                      {savingSection === 'people' ? 'Saving...' : 'Save'}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setPeopleDraft(toEditablePeopleDraft(bundle!.people))
                      setEditingPeople(true)
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
            <div className="instrument-people-section-body">
              <div className="table-shell instrument-profile-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table instrument-profile-table">
                  <tbody>
                    <tr>
                      <td className="instrument-profile-label">Management Company</td>
                      <td className="instrument-profile-value">
                        {renderPeopleOverviewValue(
                          'advisor',
                          formatResearchOverviewValue('advisor', managementStats.advisor),
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-profile-label">Sub-Advisor</td>
                      <td className="instrument-profile-value">
                        {renderPeopleOverviewValue(
                          'sub_advisor',
                          formatResearchOverviewValue('sub_advisor', managementStats.sub_advisor),
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-profile-label">Management Profile</td>
                      <td className="instrument-profile-value instrument-profile-prose">
                        {renderPeopleManagementProfileValue()}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-profile-label">Fund Managers / Research Team</td>
                      <td className="instrument-profile-value instrument-profile-prose">{renderPeopleTeamValue()}</td>
                    </tr>
                    {editingPeople || peopleAdditionalOverviewRows.length ? (
                      <tr>
                        <td className="instrument-profile-label">Additional Fields</td>
                        <td className="instrument-profile-value instrument-profile-prose">
                          {renderPeopleAdditionalFieldsValue()}
                        </td>
                      </tr>
                    ) : null}
                    <tr>
                      <td className="instrument-profile-label">Notes</td>
                      <td className="instrument-profile-value instrument-profile-prose">
                        {editingPeople && peopleDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={4}
                            value={listRowsToTextareaValue(peopleDraft.noteRows)}
                            onChange={(event) =>
                              setPeopleDraft((current) =>
                                current
                                  ? { ...current, noteRows: textareaValueToListRows('people-note', event.target.value) }
                                  : current,
                              )
                            }
                          />
                        ) : (
                          formatProfileListValue(people.notes)
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </section>
      ) : null}

      {false && activeTab === 'people' ? (
        <section className="instrument-people-shell instrument-edit-surface">
          <div className="instrument-price-topline" />
          <div className="instrument-people-page-header">
            <div>
              <div className="panel-title">People</div>
              <div className="instrument-section-title">People</div>
            </div>
            <div className="toolbar">
              {editingPeople ? (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      setPeopleDraft(toEditablePeopleDraft(bundle!.people))
                      setEditingPeople(false)
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="button-primary"
                    onClick={() => void handleSavePeople()}
                    disabled={savingSection === 'people'}
                  >
                    {savingSection === 'people' ? 'Saving...' : 'Save People'}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setPeopleDraft(toEditablePeopleDraft(bundle!.people))
                    setEditingPeople(true)
                    setSectionError(null)
                    setSectionNotice(null)
                  }}
                >
                  Edit People
                </button>
              )}
            </div>
          </div>

          <section className="instrument-people-section">
            <div className="instrument-people-section-header">
              <div>
                <div className="panel-title">People</div>
                <div className="instrument-section-title">Overview</div>
              </div>
              {editingPeople ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setPeopleDraft((current) =>
                        current
                          ? {
                              ...current,
                              overviewRows: [...current.overviewRows, { id: makeRowId('overview'), key: '', value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Field
                  </button>
                </div>
              ) : null}
            </div>

            <div className="instrument-people-section-body">
              <div className="detail-grid detail-grid-tight instrument-people-columns">
                <div>
                  <div className="table-shell instrument-people-table-shell">
                    <table className="terminal-table terminal-table-compact instrument-data-table">
                      <thead>
                        <tr>
                          <th>Field</th>
                          <th>Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {editingPeople && peopleDraft
                          ? PEOPLE_PRIMARY_OVERVIEW_FIELDS.map((field) => (
                              <tr key={field.key}>
                                <td>{field.label}</td>
                                <td className="instrument-data-table-value">
                                  <input
                                    className="table-input"
                                    type={field.type === 'date' ? 'date' : field.type === 'number' ? 'number' : 'text'}
                                    step={
                                      field.key === 'number_of_managers' ? '1' : field.type === 'number' ? '0.1' : undefined
                                    }
                                    value={getPeopleOverviewDraftValue(peopleDraft, field.key)}
                                    onChange={(event) =>
                                      setPeopleDraft((current) =>
                                        current
                                          ? {
                                              ...current,
                                              overviewRows: upsertKeyValueRows(
                                                current.overviewRows,
                                                field.key,
                                                event.target.value,
                                              ),
                                            }
                                          : current,
                                      )
                                    }
                                  />
                                </td>
                              </tr>
                            ))
                          : peoplePrimaryOverviewRows.map((row) => (
                              <tr key={row.key}>
                                <td>{row.label}</td>
                                <td className="instrument-data-table-value">{row.value}</td>
                              </tr>
                            ))}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div>
                  <div className="table-shell instrument-people-table-shell">
                    {editingPeople && peopleDraft ? (
                      <table className="terminal-table terminal-table-compact instrument-data-table">
                        <thead>
                          <tr>
                            <th>Field Key</th>
                            <th>Value</th>
                            <th className="instrument-table-action-col">Action</th>
                          </tr>
                        </thead>
                        <tbody>
                          {peopleAdditionalDraftRows.length ? (
                            peopleAdditionalDraftRows.map((row) => (
                              <tr key={row.id}>
                                <td>
                                  <input
                                    className="table-input"
                                    value={row.key}
                                    placeholder="field_key"
                                    onChange={(event) =>
                                      setPeopleDraft((current) =>
                                        current
                                          ? {
                                              ...current,
                                              overviewRows: current.overviewRows.map((item) =>
                                                item.id === row.id ? { ...item, key: event.target.value } : item,
                                              ),
                                            }
                                          : current,
                                      )
                                    }
                                  />
                                </td>
                                <td>
                                  <input
                                    className="table-input"
                                    value={row.value}
                                    placeholder="value"
                                    onChange={(event) =>
                                      setPeopleDraft((current) =>
                                        current
                                          ? {
                                              ...current,
                                              overviewRows: current.overviewRows.map((item) =>
                                                item.id === row.id ? { ...item, value: event.target.value } : item,
                                              ),
                                            }
                                          : current,
                                      )
                                    }
                                  />
                                </td>
                                <td className="instrument-table-row-action-cell">
                                  <button
                                    type="button"
                                    className="table-action"
                                    onClick={() =>
                                      setPeopleDraft((current) =>
                                        current
                                          ? {
                                              ...current,
                                              overviewRows: current.overviewRows.filter((item) => item.id !== row.id),
                                            }
                                          : current,
                                      )
                                    }
                                  >
                                    Remove
                                  </button>
                                </td>
                              </tr>
                            ))
                          ) : (
                            <tr>
                              <td colSpan={3} className="empty-state">
                                No additional people fields.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    ) : peopleAdditionalOverviewRows.length ? (
                      <table className="terminal-table terminal-table-compact instrument-data-table">
                        <thead>
                          <tr>
                            <th>Field</th>
                            <th>Value</th>
                          </tr>
                        </thead>
                        <tbody>
                          {peopleAdditionalOverviewRows.map((row) => (
                            <tr key={row.key}>
                              <td>{row.label}</td>
                              <td className="instrument-data-table-value">{row.value}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <div className="instrument-placeholder instrument-people-placeholder">
                        No additional people fields.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section className="instrument-people-section">
            <div className="instrument-people-section-header">
              <div>
                <div className="panel-title">People</div>
                <div className="instrument-section-title">Management Team</div>
              </div>
              {editingPeople ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setPeopleDraft((current) =>
                        current
                          ? {
                              ...current,
                              teamRows: [
                                ...current.teamRows,
                                { id: makeRowId('team'), name: '', role: '', start_date: '' },
                              ],
                            }
                          : current,
                      )
                    }
                  >
                    Add Team Member
                  </button>
                </div>
              ) : null}
            </div>
            <div className="table-shell instrument-people-table-shell">
              <table className="terminal-table terminal-table-compact instrument-data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Role</th>
                    <th>Start Date</th>
                    {editingPeople ? <th className="instrument-table-action-col">Action</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {editingPeople && peopleDraft ? (
                    peopleDraft!.teamRows.length ? (
                      peopleDraft!.teamRows.map((row) => (
                        <tr key={row.id}>
                          <td>
                            <input
                              className="table-input"
                              value={row.name}
                              onChange={(event) =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        teamRows: current.teamRows.map((item) =>
                                          item.id === row.id ? { ...item, name: event.target.value } : item,
                                        ),
                                      }
                                    : current,
                                )
                              }
                            />
                          </td>
                          <td>
                            <input
                              className="table-input"
                              value={row.role}
                              onChange={(event) =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        teamRows: current.teamRows.map((item) =>
                                          item.id === row.id ? { ...item, role: event.target.value } : item,
                                        ),
                                      }
                                    : current,
                                )
                              }
                            />
                          </td>
                          <td>
                            <input
                              className="table-input"
                              type="date"
                              value={row.start_date}
                              onChange={(event) =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        teamRows: current.teamRows.map((item) =>
                                          item.id === row.id ? { ...item, start_date: event.target.value } : item,
                                        ),
                                      }
                                    : current,
                                )
                              }
                            />
                          </td>
                          <td className="instrument-table-row-action-cell">
                            <button
                              type="button"
                              className="table-action"
                              onClick={() =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        teamRows: current.teamRows.filter((item) => item.id !== row.id),
                                      }
                                    : current,
                                )
                              }
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={4} className="empty-state">
                          No management team rows yet.
                        </td>
                      </tr>
                    )
                  ) : people.team.length ? (
                    people.team.map((row, index) => (
                      <tr key={`${String(row.name)}-${index}`}>
                        <td>{getString(row.name)}</td>
                        <td>{getString(row.role)}</td>
                        <td>{formatDate(row.start_date)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={3} className="empty-state">
                        No management team recorded yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="instrument-people-section">
            <div className="instrument-people-section-header">
              <div>
                <div className="panel-title">People</div>
                <div className="instrument-section-title">Notes</div>
              </div>
              {editingPeople ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setPeopleDraft((current) =>
                        current
                          ? {
                              ...current,
                              noteRows: [...current.noteRows, { id: makeRowId('people-note'), value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Note
                  </button>
                </div>
              ) : null}
            </div>
            <div className="table-shell instrument-people-table-shell">
              {editingPeople && peopleDraft ? (
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Note</th>
                      <th className="instrument-table-action-col">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {peopleDraft!.noteRows.length ? (
                      peopleDraft!.noteRows.map((row) => (
                        <tr key={row.id}>
                          <td>
                            <textarea
                              className="table-input instrument-data-table-textarea"
                              value={row.value}
                              rows={2}
                              onChange={(event) =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        noteRows: current.noteRows.map((item) =>
                                          item.id === row.id ? { ...item, value: event.target.value } : item,
                                        ),
                                      }
                                    : current,
                                )
                              }
                            />
                          </td>
                          <td className="instrument-table-row-action-cell">
                            <button
                              type="button"
                              className="table-action"
                              onClick={() =>
                                setPeopleDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        noteRows: current.noteRows.filter((item) => item.id !== row.id),
                                      }
                                    : current,
                                )
                              }
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={2} className="empty-state">
                          No notes yet.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              ) : people.notes.length ? (
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {people.notes.map((item) => (
                      <tr key={item}>
                        <td className="instrument-data-table-prose">{item}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="instrument-placeholder instrument-people-placeholder">No people notes yet.</div>
              )}
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'strategy' ? (
        <section className="instrument-strategy-shell instrument-edit-surface">
          <div className="instrument-price-topline" />

          <section className="instrument-strategy-section">
            <div className="instrument-strategy-section-header">
              <div>
                <div className="instrument-section-title">Strategy</div>
              </div>
              <div className="toolbar">
                {editingStrategy ? (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setStrategyDraft(toEditableStrategyDraft(bundle!.strategy))
                        setEditingStrategy(false)
                        setSectionError(null)
                        setSectionNotice(null)
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      onClick={() => void handleSaveStrategy()}
                      disabled={savingSection === 'strategy'}
                    >
                      {savingSection === 'strategy' ? 'Saving...' : 'Save'}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setStrategyDraft(toEditableStrategyDraft(bundle!.strategy))
                      setEditingStrategy(true)
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
            <div className="instrument-strategy-section-body">
              <div className="table-shell instrument-profile-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table instrument-profile-table">
                  <tbody>
                    <tr>
                      <td className="instrument-profile-label">Investment Scope / Objective</td>
                      <td className="instrument-profile-value instrument-profile-prose">
                        {renderStrategyTextValue(
                          strategyDraft?.investment_objective ?? '',
                          (value) =>
                            setStrategyDraft((current) =>
                              current ? { ...current, investment_objective: value } : current,
                            ),
                          strategy.investment_objective,
                          5,
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-profile-label">Investment Strategy</td>
                      <td className="instrument-profile-value instrument-profile-prose">
                        {renderStrategyTextValue(
                          strategyDraft?.summary ?? '',
                          (value) =>
                            setStrategyDraft((current) => (current ? { ...current, summary: value } : current)),
                          strategy.summary,
                          6,
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-profile-label">Investment Process</td>
                      <td className="instrument-profile-value instrument-profile-prose">
                        {editingStrategy && strategyDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={4}
                            value={listRowsToTextareaValue(strategyDraft.processRows)}
                            onChange={(event) =>
                              setStrategyDraft((current) =>
                                current
                                  ? { ...current, processRows: textareaValueToListRows('process', event.target.value) }
                                  : current,
                              )
                            }
                          />
                        ) : (
                          formatProfileListValue(strategy.process_bullets)
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-profile-label">Risk Controls</td>
                      <td className="instrument-profile-value instrument-profile-prose">
                        {editingStrategy && strategyDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={4}
                            value={listRowsToTextareaValue(strategyDraft.riskControlRows)}
                            onChange={(event) =>
                              setStrategyDraft((current) =>
                                current
                                  ? {
                                      ...current,
                                      riskControlRows: textareaValueToListRows('risk-control', event.target.value),
                                    }
                                  : current,
                              )
                            }
                          />
                        ) : (
                          formatProfileListValue(strategy.risk_controls)
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td className="instrument-profile-label">Notes</td>
                      <td className="instrument-profile-value instrument-profile-prose">
                        {editingStrategy && strategyDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={4}
                            value={listRowsToTextareaValue(strategyDraft.noteRows)}
                            onChange={(event) =>
                              setStrategyDraft((current) =>
                                current
                                  ? {
                                      ...current,
                                      noteRows: textareaValueToListRows('strategy-note', event.target.value),
                                    }
                                  : current,
                              )
                            }
                          />
                        ) : (
                          formatProfileListValue(strategy.notes)
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </section>
      ) : null}

      {false && activeTab === 'strategy' ? (
        <section className="instrument-strategy-shell instrument-edit-surface">
          <div className="instrument-price-topline" />
          <div className="instrument-strategy-page-header">
            <div>
              <div className="panel-title">Strategy</div>
              <div className="instrument-section-title">Strategy</div>
            </div>
            <div className="toolbar">
              {editingStrategy ? (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      setStrategyDraft(toEditableStrategyDraft(bundle!.strategy))
                      setEditingStrategy(false)
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="button-primary"
                    onClick={() => void handleSaveStrategy()}
                    disabled={savingSection === 'strategy'}
                  >
                    {savingSection === 'strategy' ? 'Saving...' : 'Save Strategy'}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setStrategyDraft(toEditableStrategyDraft(bundle!.strategy))
                    setEditingStrategy(true)
                    setSectionError(null)
                    setSectionNotice(null)
                  }}
                >
                  Edit Strategy
                </button>
              )}
            </div>
          </div>

          <section className="instrument-strategy-section">
            <div className="instrument-strategy-section-header">
              <div>
                <div className="panel-title">Strategy</div>
                <div className="instrument-section-title">Core Statements</div>
              </div>
            </div>
            <div className="instrument-strategy-section-body">
              <div className="table-shell instrument-strategy-table-shell">
                <table className="terminal-table terminal-table-compact instrument-data-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Investment Thesis</td>
                      <td className={editingStrategy ? '' : 'instrument-data-table-prose'}>
                        {editingStrategy && strategyDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={6}
                            value={strategyDraft!.summary}
                            onChange={(event) =>
                              setStrategyDraft((current) =>
                                current ? { ...current, summary: event.target.value } : current,
                              )
                            }
                          />
                        ) : (
                          strategy.summary || 'No strategy summary yet.'
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td>Investment Objective</td>
                      <td className={editingStrategy ? '' : 'instrument-data-table-prose'}>
                        {editingStrategy && strategyDraft ? (
                          <textarea
                            className="table-input instrument-data-table-textarea"
                            rows={5}
                            value={strategyDraft!.investment_objective}
                            onChange={(event) =>
                              setStrategyDraft((current) =>
                                current ? { ...current, investment_objective: event.target.value } : current,
                              )
                            }
                          />
                        ) : (
                          strategy.investment_objective || 'No investment objective yet.'
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="instrument-strategy-section">
            <div className="instrument-strategy-section-header">
              <div>
                <div className="panel-title">Strategy</div>
                <div className="instrument-section-title">Process</div>
              </div>
              {editingStrategy ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setStrategyDraft((current) =>
                        current
                          ? {
                              ...current,
                              processRows: [...current.processRows, { id: makeRowId('process'), value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Bullet
                  </button>
                </div>
              ) : null}
            </div>
            <div className="instrument-strategy-section-body">
              <div className="table-shell instrument-strategy-table-shell">
                {editingStrategy && strategyDraft ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Process Item</th>
                        <th className="instrument-table-action-col">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategyDraft!.processRows.length ? (
                        strategyDraft!.processRows.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <textarea
                                className="table-input instrument-data-table-textarea"
                                rows={2}
                                value={row.value}
                                onChange={(event) =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          processRows: current.processRows.map((item) =>
                                            item.id === row.id ? { ...item, value: event.target.value } : item,
                                          ),
                                        }
                                      : current,
                                  )
                                }
                              />
                            </td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="table-action"
                                onClick={() =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          processRows: current.processRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={2} className="empty-state">
                            No process bullets yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                ) : strategy.process_bullets.length ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Process Item</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategy.process_bullets.map((item) => (
                        <tr key={item}>
                          <td className="instrument-data-table-prose">{item}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="instrument-placeholder instrument-strategy-placeholder">No process bullets yet.</div>
                )}
              </div>
            </div>
          </section>

          <section className="instrument-strategy-section">
            <div className="instrument-strategy-section-header">
              <div>
                <div className="panel-title">Strategy</div>
                <div className="instrument-section-title">Risk Controls</div>
              </div>
              {editingStrategy ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setStrategyDraft((current) =>
                        current
                          ? {
                              ...current,
                              riskControlRows: [
                                ...current.riskControlRows,
                                { id: makeRowId('risk-control'), value: '' },
                              ],
                            }
                          : current,
                      )
                    }
                  >
                    Add Control
                  </button>
                </div>
              ) : null}
            </div>
            <div className="instrument-strategy-section-body">
              <div className="table-shell instrument-strategy-table-shell">
                {editingStrategy && strategyDraft ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Risk Control</th>
                        <th className="instrument-table-action-col">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategyDraft!.riskControlRows.length ? (
                        strategyDraft!.riskControlRows.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <textarea
                                className="table-input instrument-data-table-textarea"
                                rows={2}
                                value={row.value}
                                onChange={(event) =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          riskControlRows: current.riskControlRows.map((item) =>
                                            item.id === row.id ? { ...item, value: event.target.value } : item,
                                          ),
                                        }
                                      : current,
                                  )
                                }
                              />
                            </td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="table-action"
                                onClick={() =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          riskControlRows: current.riskControlRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={2} className="empty-state">
                            No risk controls yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                ) : strategy.risk_controls.length ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Risk Control</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategy.risk_controls.map((item) => (
                        <tr key={item}>
                          <td className="instrument-data-table-prose">{item}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="instrument-placeholder instrument-strategy-placeholder">No risk controls yet.</div>
                )}
              </div>
            </div>
          </section>

          <section className="instrument-strategy-section">
            <div className="instrument-strategy-section-header">
              <div>
                <div className="panel-title">Strategy</div>
                <div className="instrument-section-title">Notes</div>
              </div>
              {editingStrategy ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setStrategyDraft((current) =>
                        current
                          ? {
                              ...current,
                              noteRows: [...current.noteRows, { id: makeRowId('strategy-note'), value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Note
                  </button>
                </div>
              ) : null}
            </div>
            <div className="instrument-strategy-section-body">
              <div className="table-shell instrument-strategy-table-shell">
                {editingStrategy && strategyDraft ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Note</th>
                        <th className="instrument-table-action-col">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategyDraft!.noteRows.length ? (
                        strategyDraft!.noteRows.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <textarea
                                className="table-input instrument-data-table-textarea"
                                rows={2}
                                value={row.value}
                                onChange={(event) =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          noteRows: current.noteRows.map((item) =>
                                            item.id === row.id ? { ...item, value: event.target.value } : item,
                                          ),
                                        }
                                      : current,
                                  )
                                }
                              />
                            </td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="table-action"
                                onClick={() =>
                                  setStrategyDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          noteRows: current.noteRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={2} className="empty-state">
                            No notes yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                ) : strategy.notes.length ? (
                  <table className="terminal-table terminal-table-compact instrument-data-table">
                    <thead>
                      <tr>
                        <th>Note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategy.notes.map((item) => (
                        <tr key={item}>
                          <td className="instrument-data-table-prose">{item}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="instrument-placeholder instrument-strategy-placeholder">No strategy notes yet.</div>
                )}
              </div>
            </div>
          </section>
        </section>
      ) : null}

      {activeTab === 'documents' ? (
        <section className="instrument-documents-shell instrument-edit-surface" data-portfolio-ops-i18n-ignore="true">
          <div className="instrument-price-topline" />

          <section className="instrument-documents-section">
            <div className="instrument-documents-section-header">
              <div>
                <div className="instrument-section-title">{localize(language, SYSTEM_LABELS.documentTitle)}</div>
              </div>
              <div className="toolbar">
                <label className="instrument-documents-header-upload">
                  <input
                    key={documentUploadFile ? 'document-header-upload-selected' : 'document-header-upload-empty'}
                    type="file"
                    onChange={(event) => {
                      const nextFile = event.target.files?.[0] || null
                      setDocumentUploadFile(nextFile)
                      if (nextFile && !documentUploadTitle.trim()) {
                        setDocumentUploadTitle(nextFile.name.replace(/\.[^.]+$/, ''))
                      }
                    }}
                  />
                  {localize(language, SYSTEM_LABELS.upload)}
                </label>
              </div>
            </div>

            {documentUploadFile ? (
              <form
                className="instrument-documents-upload instrument-documents-upload-active"
                onSubmit={(event) => {
                  event.preventDefault()
                  void handleUploadDocument()
                }}
              >
                  <label>
                    <span>{localize(language, SYSTEM_LABELS.title)}</span>
                    <input
                      className="table-input"
                      value={documentUploadTitle}
                      placeholder={localize(language, SYSTEM_LABELS.title)}
                      onChange={(event) => setDocumentUploadTitle(event.target.value)}
                    />
                  </label>
                  <label>
                    <span>{localize(language, SYSTEM_LABELS.type)}</span>
                    <select
                      className="table-input"
                      value={documentUploadType}
                      onChange={(event) => setDocumentUploadType(event.target.value)}
                    >
                      <option value="">{localize(language, SYSTEM_LABELS.selectType)}</option>
                      {DOCUMENT_TYPE_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {localizeSystemValue(option, language)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="instrument-documents-upload-notes">
                    <span>{localize(language, SYSTEM_LABELS.notes)}</span>
                    <input
                      className="table-input"
                      value={documentUploadNotes}
                      placeholder={localize(language, SYSTEM_LABELS.optionalNote)}
                      onChange={(event) => setDocumentUploadNotes(event.target.value)}
                    />
                  </label>
                  <div className="instrument-documents-upload-actions">
                    <div className="instrument-documents-upload-file" title={documentUploadFile.name}>
                      {documentUploadFile.name}
                    </div>
                    <button type="submit" className="button-primary" disabled={uploadingDocument}>
                      {uploadingDocument
                        ? localize(language, SYSTEM_LABELS.uploading)
                        : localize(language, SYSTEM_LABELS.upload)}
                    </button>
                  </div>
              </form>
            ) : null}

            <div className="table-shell instrument-documents-list-shell">
              <table className="terminal-table terminal-table-compact instrument-data-table instrument-documents-list-table">
                <thead>
                  <tr>
                    <th>{localize(language, SYSTEM_LABELS.fileName)}</th>
                    <th>{localize(language, SYSTEM_LABELS.title)}</th>
                    <th>{localize(language, SYSTEM_LABELS.type)}</th>
                    <th>{localize(language, SYSTEM_LABELS.notes)}</th>
                    <th>{localize(language, SYSTEM_LABELS.status)}</th>
                    <th>{localize(language, SYSTEM_LABELS.size)}</th>
                    <th>{localize(language, SYSTEM_LABELS.uploaded)}</th>
                  </tr>
                </thead>
                <tbody>
                  {currentDocumentRows.length ? (
                    currentDocumentRows.map((row, index) => {
                      const fileName = getDocumentRecordFileName(row)
                      const downloadUrl = getDocumentRecordText(row, 'download_url')
                      const notes = getDocumentRecordNotes(row)
                      return (
                        <tr key={`${fileName}-${index}`}>
                          <td>
                            {downloadUrl ? (
                              <a href={downloadUrl} target="_blank" rel="noreferrer" className="table-link">
                                {fileName}
                              </a>
                            ) : (
                              fileName
                            )}
                          </td>
                          <td>{getDocumentRecordText(row, 'title') || '—'}</td>
                          <td>{localizeSystemValue(getDocumentRecordText(row, 'document_type'), language)}</td>
                          <td className="instrument-data-table-prose">{notes || '—'}</td>
                          <td>
                            <span className={`status-badge ${getDocumentStatusTone(row.status)}`}>
                              {localizeSystemValue(getDocumentRecordText(row, 'status'), language)}
                            </span>
                          </td>
                          <td>{formatFileSize(row.file_size)}</td>
                          <td>{formatDateTime(row.uploaded_at)}</td>
                        </tr>
                      )
                    })
                  ) : (
                    <tr>
                      <td colSpan={7} className="empty-state">
                        {localize(language, SYSTEM_LABELS.noDocuments)}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </section>
      ) : null}

      {false && activeTab === 'documents' ? (
        <section className="instrument-documents-shell instrument-edit-surface">
          <div className="instrument-price-topline" />
          <div className="instrument-section-header instrument-documents-page-header">
            <div>
              <div className="panel-title">Documents</div>
            </div>
            <div className="toolbar">
              {editingDocuments ? (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      setDocumentsDraft(toEditableDocumentsDraft(bundle!.documents))
                      setEditingDocuments(false)
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="button-primary"
                    onClick={() => void handleSaveDocuments()}
                    disabled={savingSection === 'documents'}
                  >
                    {savingSection === 'documents' ? 'Saving...' : 'Save Documents'}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setDocumentsDraft(toEditableDocumentsDraft(bundle!.documents))
                    setEditingDocuments(true)
                    setSectionError(null)
                    setSectionNotice(null)
                  }}
                >
                  Edit Documents
                </button>
              )}
            </div>
          </div>

          <section className="instrument-documents-section">
            <div className="instrument-documents-section-header">
              <div>
                <div className="panel-title">Documents</div>
                <div className="instrument-section-title">Current Adopted Documents</div>
              </div>
            </div>
            <div className="table-shell instrument-documents-table-shell">
              <table className="terminal-table terminal-table-compact instrument-documents-table">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Type</th>
                    <th>As Of</th>
                    <th>Source</th>
                    <th>Status</th>
                    <th>Version</th>
                    {editingDocuments ? <th className="instrument-table-action-col" aria-label="Row actions" /> : null}
                  </tr>
                </thead>
                <tbody>
                  {editingDocuments && documentsDraft ? (
                    documentsDraft!.currentDocumentRows.length ? (
                      documentsDraft!.currentDocumentRows.map((row) => (
                        <tr key={row.id}>
                          <td><input className="table-input" value={row.title} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, title: event.target.value } : item) } : current)} /></td>
                          <td><input className="table-input" value={row.document_type} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, document_type: event.target.value } : item) } : current)} /></td>
                          <td><input className="table-input" type="date" value={row.as_of_date} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, as_of_date: event.target.value } : item) } : current)} /></td>
                          <td><input className="table-input" value={row.source} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, source: event.target.value } : item) } : current)} /></td>
                          <td><input className="table-input" value={row.status} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, status: event.target.value } : item) } : current)} /></td>
                          <td><input className="table-input" value={row.version_label} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, currentDocumentRows: current.currentDocumentRows.map((item) => item.id === row.id ? { ...item, version_label: event.target.value } : item) } : current)} /></td>
                          <td className="instrument-table-row-action-cell">
                            <button
                              type="button"
                              className="instrument-row-remove"
                              aria-label="Remove document row"
                              title="Remove row"
                              onClick={() =>
                                setDocumentsDraft((current) =>
                                  current
                                    ? {
                                        ...current,
                                        currentDocumentRows: current.currentDocumentRows.filter((item) => item.id !== row.id),
                                      }
                                    : current,
                                )
                              }
                            >
                              ×
                            </button>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr><td colSpan={7} className="empty-state">No adopted documents yet.</td></tr>
                    )
                  ) : documents.current_documents.length ? (
                    documents.current_documents.map((row, index) => (
                      <tr key={`${String(row.title)}-${index}`}>
                        <td>{getString(row.title)}</td>
                        <td>{getString(row.document_type)}</td>
                        <td>{formatDate(row.as_of_date)}</td>
                        <td>{getString(row.source)}</td>
                        <td><span className={`status-badge ${getDocumentStatusTone(row.status)}`}>{getString(row.status)}</span></td>
                        <td>{getString(row.version_label)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr><td colSpan={6} className="empty-state">No adopted documents yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            {editingDocuments ? (
              <div className="instrument-table-inline-actions">
                <button
                  type="button"
                  className="table-action"
                  onClick={() =>
                    setDocumentsDraft((current) =>
                      current
                        ? {
                            ...current,
                            currentDocumentRows: [
                              ...current.currentDocumentRows,
                              {
                                id: makeRowId('document'),
                                title: '',
                                document_type: '',
                                as_of_date: '',
                                source: '',
                                status: '',
                                version_label: '',
                                file_name: '',
                                download_url: '',
                                file_size: '',
                                content_type: '',
                                uploaded_at: '',
                                notes: '',
                                stored_file_name: '',
                              },
                            ],
                          }
                        : current,
                    )
                  }
                >
                  + Add document row
                </button>
              </div>
            ) : null}
          </section>

          <div className="instrument-documents-columns">
            <section className="instrument-documents-section">
              <div className="instrument-documents-section-header">
                <div>
                  <div className="panel-title">Documents</div>
                  <div className="instrument-section-title">Recent Imports</div>
                </div>
              </div>
              <div className="table-shell instrument-documents-table-shell">
                <table className="terminal-table terminal-table-compact instrument-documents-table">
                  <thead>
                    <tr>
                      <th>Import Type</th>
                      <th>Received At</th>
                      <th>Source</th>
                      <th>Status</th>
                      <th>File</th>
                      {editingDocuments ? <th className="instrument-table-action-col" aria-label="Row actions" /> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {editingDocuments && documentsDraft ? (
                      documentsDraft!.importRows.length ? (
                        documentsDraft!.importRows.map((row) => (
                          <tr key={row.id}>
                            <td><input className="table-input" value={row.import_type} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, importRows: current.importRows.map((item) => item.id === row.id ? { ...item, import_type: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" type="datetime-local" value={row.received_at} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, importRows: current.importRows.map((item) => item.id === row.id ? { ...item, received_at: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.source} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, importRows: current.importRows.map((item) => item.id === row.id ? { ...item, source: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.status} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, importRows: current.importRows.map((item) => item.id === row.id ? { ...item, status: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.file_name} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, importRows: current.importRows.map((item) => item.id === row.id ? { ...item, file_name: event.target.value } : item) } : current)} /></td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="instrument-row-remove"
                                aria-label="Remove import row"
                                title="Remove row"
                                onClick={() =>
                                  setDocumentsDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          importRows: current.importRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                ×
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr><td colSpan={6} className="empty-state">No import records yet.</td></tr>
                      )
                    ) : documents.recent_imports.length ? (
                      documents.recent_imports.map((row, index) => (
                        <tr key={`${String(row.file_name)}-${index}`}>
                          <td>{getString(row.import_type)}</td>
                          <td>{formatDateTime(row.received_at)}</td>
                          <td>{getString(row.source)}</td>
                          <td><span className={`status-badge ${getDocumentStatusTone(row.status)}`}>{getString(row.status)}</span></td>
                          <td>{getString(row.file_name)}</td>
                        </tr>
                      ))
                    ) : (
                      <tr><td colSpan={5} className="empty-state">No import records yet.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {editingDocuments ? (
                <div className="instrument-table-inline-actions">
                  <button
                    type="button"
                    className="table-action"
                    onClick={() =>
                      setDocumentsDraft((current) =>
                        current
                          ? {
                              ...current,
                              importRows: [
                                ...current.importRows,
                                {
                                  id: makeRowId('document-import'),
                                  import_type: '',
                                  received_at: '',
                                  source: '',
                                  status: '',
                                  file_name: '',
                                },
                              ],
                            }
                          : current,
                      )
                    }
                  >
                    + Add import row
                  </button>
                </div>
              ) : null}
            </section>

            <section className="instrument-documents-section">
              <div className="instrument-documents-section-header">
                <div>
                  <div className="panel-title">Documents</div>
                  <div className="instrument-section-title">Extraction Review</div>
                </div>
              </div>
              <div className="table-shell instrument-documents-table-shell">
                <table className="terminal-table terminal-table-compact instrument-documents-table">
                  <thead>
                    <tr>
                      <th>Document</th>
                      <th>Extract Type</th>
                      <th>Status</th>
                      <th>Adopted Version</th>
                      <th>Updated At</th>
                      {editingDocuments ? <th className="instrument-table-action-col" aria-label="Row actions" /> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {editingDocuments && documentsDraft ? (
                      documentsDraft!.extractionRows.length ? (
                        documentsDraft!.extractionRows.map((row) => (
                          <tr key={row.id}>
                            <td><input className="table-input" value={row.document_title} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, extractionRows: current.extractionRows.map((item) => item.id === row.id ? { ...item, document_title: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.extract_type} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, extractionRows: current.extractionRows.map((item) => item.id === row.id ? { ...item, extract_type: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.status} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, extractionRows: current.extractionRows.map((item) => item.id === row.id ? { ...item, status: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" value={row.adopted_version} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, extractionRows: current.extractionRows.map((item) => item.id === row.id ? { ...item, adopted_version: event.target.value } : item) } : current)} /></td>
                            <td><input className="table-input" type="datetime-local" value={row.updated_at} onChange={(event) => setDocumentsDraft((current) => current ? { ...current, extractionRows: current.extractionRows.map((item) => item.id === row.id ? { ...item, updated_at: event.target.value } : item) } : current)} /></td>
                            <td className="instrument-table-row-action-cell">
                              <button
                                type="button"
                                className="instrument-row-remove"
                                aria-label="Remove extraction review row"
                                title="Remove row"
                                onClick={() =>
                                  setDocumentsDraft((current) =>
                                    current
                                      ? {
                                          ...current,
                                          extractionRows: current.extractionRows.filter((item) => item.id !== row.id),
                                        }
                                      : current,
                                  )
                                }
                              >
                                ×
                              </button>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr><td colSpan={6} className="empty-state">No extraction reviews yet.</td></tr>
                      )
                    ) : documents.extraction_reviews.length ? (
                      documents.extraction_reviews.map((row, index) => (
                        <tr key={`${String(row.document_title)}-${index}`}>
                          <td>{getString(row.document_title)}</td>
                          <td>{getString(row.extract_type)}</td>
                          <td><span className={`status-badge ${getDocumentStatusTone(row.status)}`}>{getString(row.status)}</span></td>
                          <td>{getString(row.adopted_version)}</td>
                          <td>{formatDateTime(row.updated_at)}</td>
                        </tr>
                      ))
                    ) : (
                      <tr><td colSpan={5} className="empty-state">No extraction reviews yet.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {editingDocuments ? (
                <div className="instrument-table-inline-actions">
                  <button
                    type="button"
                    className="table-action"
                    onClick={() =>
                      setDocumentsDraft((current) =>
                        current
                          ? {
                              ...current,
                              extractionRows: [
                                ...current.extractionRows,
                                {
                                  id: makeRowId('document-extraction'),
                                  document_title: '',
                                  extract_type: '',
                                  status: '',
                                  adopted_version: '',
                                  updated_at: '',
                                },
                              ],
                            }
                          : current,
                      )
                    }
                  >
                    + Add review row
                  </button>
                </div>
              ) : null}
            </section>
          </div>

          <section className="instrument-documents-section">
            <div className="instrument-documents-section-header">
              <div>
                <div className="panel-title">Documents</div>
                <div className="instrument-section-title">Notes</div>
              </div>
              {editingDocuments ? (
                <div className="toolbar">
                  <button
                    type="button"
                    onClick={() =>
                      setDocumentsDraft((current) =>
                        current
                          ? {
                              ...current,
                              noteRows: [...current.noteRows, { id: makeRowId('document-note'), value: '' }],
                            }
                          : current,
                      )
                    }
                  >
                    Add Note
                  </button>
                </div>
              ) : null}
            </div>
            {editingDocuments && documentsDraft ? (
              <div className="instrument-documents-notes-editor">
                {documentsDraft!.noteRows.length ? (
                  <div className="instrument-documents-notes instrument-inline-list">
                    {documentsDraft!.noteRows.map((row) => (
                      <div key={row.id} className="instrument-inline-list-row">
                        <textarea
                          className="form-textarea instrument-inline-list-textarea"
                          value={row.value}
                          rows={2}
                          onChange={(event) =>
                            setDocumentsDraft((current) =>
                              current
                                ? {
                                    ...current,
                                    noteRows: current.noteRows.map((item) =>
                                      item.id === row.id ? { ...item, value: event.target.value } : item,
                                    ),
                                  }
                                : current,
                            )
                          }
                        />
                        <button
                          type="button"
                          className="table-action"
                          onClick={() =>
                            setDocumentsDraft((current) =>
                              current
                                ? {
                                    ...current,
                                    noteRows: current.noteRows.filter((item) => item.id !== row.id),
                                  }
                                : current,
                            )
                          }
                        >
                          Remove
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="instrument-placeholder instrument-documents-placeholder">No notes yet.</div>
                )}
              </div>
            ) : documents.notes.length ? (
              <div className="instrument-documents-notes">
                <ul className="bullet-list">
                  {documents.notes.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="instrument-placeholder instrument-documents-placeholder">No document notes yet.</div>
            )}
          </section>
        </section>
      ) : null}

      {activeTab === 'research' ? (
        <section className="instrument-research-shell instrument-edit-surface">
          <div className="instrument-price-topline" />
          <div className="instrument-section-header instrument-research-page-header">
            <div>
              <div className="panel-title">Research</div>
            </div>
          </div>

          <section className="instrument-research-section">
            <div className="instrument-research-section-header">
              <div>
                <div className="instrument-section-title">Rating</div>
              </div>
              {manualRatingHasChanges ? (
                <div className="toolbar">
                  <button
                    type="button"
                    className="button-primary"
                    onClick={() => void handleSaveManualRating()}
                    disabled={savingSection === 'manual_rating'}
                  >
                    {savingSection === 'manual_rating' ? 'Saving...' : 'Save'}
                  </button>
                </div>
              ) : null}
            </div>
            <div className="instrument-manual-rating-row">
              <div className="instrument-manual-rating-picker" role="radiogroup" aria-label="Manual rating">
                {[1, 2, 3, 4, 5].map((value) => {
                  const selected = displayedManualRating === value
                  const active = displayedManualRating != null && value <= displayedManualRating
                  return (
                    <button
                      key={value}
                      type="button"
                      className={
                        active
                          ? 'instrument-manual-rating-star instrument-manual-rating-star-active'
                          : 'instrument-manual-rating-star'
                      }
                      aria-checked={selected}
                      role="radio"
                      onClick={() => {
                        const nextRating = selected ? null : value
                        setManualRatingDraft(nextRating)
                        setManualRatingDirty(nextRating !== researchManualRating)
                        setSectionError(null)
                        setSectionNotice(null)
                      }}
                    >
                      {active ? '★' : '☆'}
                    </button>
                  )
                })}
              </div>
            </div>
          </section>

          {productFrameworkAttributes === null ? (
            <section className="instrument-research-section">
              <div className="instrument-research-section-header">
                <div>
                  <div className="panel-title">Research Framework</div>
                  <div className="instrument-section-title">Classification</div>
                </div>
              </div>
              {productFrameworkLoadError ? (
                <div
                  className="instrument-placeholder instrument-research-placeholder instrument-framework-load-error"
                  role="alert"
                >
                  <div>
                    <strong>
                      {language === 'zh-Hans'
                        ? '研究框架加载失败'
                        : 'Research framework unavailable'}
                    </strong>
                    <span>
                      {language === 'zh-Hans'
                        ? `属性服务未返回数据：${productFrameworkLoadError}`
                        : `The attribute service did not return data: ${productFrameworkLoadError}`}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setProductFrameworkRetryToken((current) => current + 1)}
                  >
                    {language === 'zh-Hans' ? '重试' : 'Retry'}
                  </button>
                </div>
              ) : (
                <div className="instrument-placeholder instrument-research-placeholder">
                  Loading product framework...
                </div>
              )}
            </section>
          ) : (
            productFrameworkSections.map((section) => (
              <section key={section.domain} className="instrument-research-section">
                <div className="instrument-research-section-header">
                  <div>
                    <div className="panel-title">Research Framework</div>
                    <div className="instrument-section-title">{section.title}</div>
                  </div>
                </div>
                {section.groups.length ? (
                  <div className="table-shell instrument-research-table-shell instrument-product-tags-table-shell">
                    <table className="terminal-table terminal-table-compact instrument-research-table instrument-product-tags-table">
                      <thead>
                        <tr>
                          <th>Field</th>
                          <th>Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {section.groups.map((group) => (
                          <Fragment key={`${section.domain}-${group.groupCode}`}>
                            <tr className="instrument-product-tags-group-row">
                              <td colSpan={2}>
                                <div className="instrument-product-tags-group-label">
                                  {group.label}
                                </div>
                              </td>
                            </tr>
                            {group.definitions.map((definition) => {
                              const rubricText = getDefinitionRubricText(definition)
                              return (
                                <tr key={definition.attribute_key}>
                                  <td className="instrument-product-tags-table-label-cell">
                                    <div className="instrument-product-tags-field">
                                      <span>{definition.label}</span>
                                      {rubricText ? (
                                        <span className="instrument-product-tags-help" tabIndex={0}>
                                          ?
                                          <span className="instrument-product-tags-tooltip">
                                            {rubricText}
                                          </span>
                                        </span>
                                      ) : null}
                                    </div>
                                  </td>
                                  <td className="instrument-product-tags-table-value-cell">
                                    <div
                                      className="instrument-product-tags-picker"
                                      ref={
                                        openProductFrameworkPickerKey === definition.attribute_key
                                          ? productFrameworkPickerRef
                                          : undefined
                                      }
                                    >
                                      <button
                                        type="button"
                                        className={`instrument-product-tags-picker-trigger${
                                          openProductFrameworkPickerKey === definition.attribute_key
                                            ? ' instrument-product-tags-picker-trigger-active'
                                            : ''
                                        }`}
                                        disabled={productFrameworkSavingKey === definition.attribute_key}
                                        onClick={() =>
                                          setOpenProductFrameworkPickerKey((current) =>
                                            current === definition.attribute_key
                                              ? null
                                              : definition.attribute_key,
                                          )
                                        }
                                      >
                                        <span>
                                          {productFrameworkSavingKey === definition.attribute_key
                                            ? 'Saving...'
                                            : formatFrameworkValue(
                                                productFrameworkAttributes.values[definition.attribute_key],
                                              )}
                                        </span>
                                      </button>
                                      {openProductFrameworkPickerKey === definition.attribute_key ? (
                                        <div className="instrument-product-tags-picker-panel">
                                          <div className="instrument-product-tags-picker-meta">
                                            {definition.data_type === 'multi_select'
                                              ? 'Select one or more'
                                              : 'Select one'}
                                          </div>
                                          <div className="instrument-product-tags-picker-options">
                                            <button
                                              type="button"
                                              className="instrument-product-tags-picker-option"
                                              disabled={productFrameworkSavingKey === definition.attribute_key}
                                              onClick={() =>
                                                void handleSaveProductFrameworkValue(
                                                  definition,
                                                  definition.data_type === 'multi_select' ? [] : null,
                                                  {
                                                    closePicker:
                                                      definition.data_type !== 'multi_select',
                                                  },
                                                )
                                              }
                                            >
                                              <span className="instrument-product-tags-picker-check" />
                                              <span className="instrument-product-tags-picker-label">
                                                Clear
                                              </span>
                                            </button>
                                            {definition.options.map((option) => {
                                              const selected = isFrameworkOptionSelected(
                                                productFrameworkAttributes,
                                                definition,
                                                option,
                                              )
                                              return (
                                                <button
                                                  key={option}
                                                  type="button"
                                                  className={`instrument-product-tags-picker-option${
                                                    selected
                                                      ? ' instrument-product-tags-picker-option-selected'
                                                      : ''
                                                  }`}
                                                  disabled={productFrameworkSavingKey === definition.attribute_key}
                                                  onClick={() =>
                                                    void handleSaveProductFrameworkValue(
                                                      definition,
                                                      buildNextFrameworkValue(
                                                        productFrameworkAttributes,
                                                        definition,
                                                        option,
                                                      ),
                                                      {
                                                        closePicker:
                                                          definition.data_type !== 'multi_select',
                                                      },
                                                    )
                                                  }
                                                >
                                                  <span className="instrument-product-tags-picker-check">
                                                    {selected ? '✓' : ''}
                                                  </span>
                                                  <span className="instrument-product-tags-picker-label">
                                                    {option}
                                                  </span>
                                                </button>
                                              )
                                            })}
                                          </div>
                                        </div>
                                      ) : null}
                                    </div>
                                  </td>
                                </tr>
                              )
                            })}
                          </Fragment>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="instrument-placeholder instrument-research-placeholder">
                    {section.emptyState}
                  </div>
                )}
              </section>
            ))
          )}

          <section className="instrument-research-section">
            <div className="instrument-research-section-header">
              <div>
                <div className="panel-title">Research</div>
                <div className="instrument-section-title">Current Research View</div>
              </div>
              <div className="toolbar">
                {editingResearchOverview ? (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setResearchDraft(toEditableResearchDraft(bundle.research))
                        setEditingResearchOverview(false)
                        setSectionError(null)
                        setSectionNotice(null)
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      onClick={() => void handleSaveResearchOverview()}
                      disabled={savingSection === 'research_overview'}
                    >
                      {savingSection === 'research_overview' ? 'Saving...' : 'Save'}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setResearchDraft(toEditableResearchDraft(bundle.research))
                      setEditingResearchOverview(true)
                      setSectionError(null)
                      setSectionNotice(null)
                    }}
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
            {editingResearchOverview && researchDraft ? (
              <div className="instrument-research-section-body">
                <div className="instrument-research-facts-grid instrument-research-facts-grid-edit">
                  {RESEARCH_OVERVIEW_FIELDS.map((field) => (
                    <label key={field.key} className="instrument-research-fact instrument-research-fact-edit">
                      <span>{field.label}</span>
                      <input
                        className="form-input instrument-inline-value-input"
                        type={field.type === 'date' ? 'date' : 'text'}
                        value={getResearchOverviewDraftValue(researchDraft, field.key)}
                        onChange={(event) =>
                          setResearchDraft((current) =>
                            current
                              ? {
                                  ...current,
                                  overviewRows: upsertKeyValueRows(
                                    current.overviewRows,
                                    field.key,
                                    event.target.value,
                                  ),
                                }
                              : current,
                          )
                        }
                      />
                    </label>
                  ))}
                </div>
              </div>
            ) : (
              <div className="instrument-research-facts-grid">
                {RESEARCH_OVERVIEW_FIELDS.map((field) => (
                  <div key={field.key} className="instrument-research-fact">
                    <span>{field.label}</span>
                    <strong>{formatResearchOverviewValue(field.key, bundle.research.overview?.[field.key])}</strong>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="instrument-research-section">
            <div className="instrument-research-section-header">
              <div>
                <div className="panel-title">Research</div>
                <div className="instrument-section-title">Research Notes</div>
              </div>
              <div className="toolbar">
                <button
                  type="button"
                  onClick={() => openTimelineNoteEditor(latestPoint?.date || latestNavRecord?.as_of_date || '')}
                >
                  Add Note
                </button>
              </div>
            </div>
            {timelineNotes.length ? (
              <div className="table-shell instrument-research-table-shell">
                <table className="terminal-table terminal-table-compact instrument-research-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Importance</th>
                      <th>Title</th>
                      <th>Summary</th>
                      <th>Tags</th>
                      <th className="instrument-table-action-col" aria-label="Timeline note actions" />
                    </tr>
                  </thead>
                  <tbody>
                    {timelineNotes.map((note) => (
                      <tr key={note.note_id}>
                        <td>{formatDate(note.note_date)}</td>
                        <td>{formatTimelineNoteImportance(note.importance)}</td>
                        <td>{note.title || 'Untitled'}</td>
                        <td>{note.summary || note.body || '—'}</td>
                        <td>{note.tags.length ? note.tags.join(', ') : '—'}</td>
                        <td className="instrument-table-row-action-cell">
                          <div className="instrument-table-inline-actions instrument-table-inline-actions-compact">
                            <button
                              type="button"
                              className="table-action"
                              onClick={() => focusTimelineNoteInQuote(note.note_date)}
                            >
                              Open in Overview
                            </button>
                            <button
                              type="button"
                              className="table-action"
                              onClick={() => openTimelineNoteEditor(note.note_date, note)}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="table-action"
                              onClick={() => void handleDeleteTimelineNote(note.note_id)}
                              disabled={savingSection === 'timeline_note'}
                            >
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="instrument-placeholder instrument-research-placeholder">
                No research notes yet.
              </div>
            )}
          </section>
        </section>
      ) : null}

      {activeTab === 'monitoring' ? (
        <section className="instrument-monitoring-shell">
          <div className="instrument-price-topline" />
          <div className="instrument-section-header instrument-monitoring-page-header">
            <div>
              <div className="panel-title">Monitoring</div>
            </div>
          </div>

          <section className="instrument-monitoring-section">
            <div className="instrument-monitoring-section-header">
              <div>
                <div className="panel-title">Monitoring</div>
                <div className="instrument-section-title">Status Overview</div>
              </div>
            </div>
            <div className="instrument-monitoring-facts-grid">
              {monitoringOverviewRows.map((row) => (
                <div key={row.label} className="instrument-monitoring-fact">
                  <span>{row.label}</span>
                  {row.tone ? (
                    <strong>
                      <span className={`status-badge ${row.tone}`}>{row.value}</span>
                    </strong>
                  ) : (
                    <strong>{row.value}</strong>
                  )}
                </div>
              ))}
            </div>
          </section>

          <section className="instrument-monitoring-section">
            <div className="instrument-monitoring-section-header">
              <div>
                <div className="panel-title">Monitoring</div>
                <div className="instrument-section-title">Pipeline Status</div>
              </div>
            </div>
            <div className="table-shell instrument-monitoring-table-shell">
              <table className="terminal-table terminal-table-compact instrument-monitoring-table">
                <thead>
                  <tr>
                    <th>Domain</th>
                    <th>As Of</th>
                    <th>Source Cutoff</th>
                    <th>Methodology</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {monitoringPipelineRows.map((row) => (
                    <tr key={row.domain}>
                      <td>{row.domain}</td>
                      <td>{row.asOf}</td>
                      <td>{row.cutoff}</td>
                      <td>{row.methodology}</td>
                      <td>
                        <span className={`status-badge ${row.tone}`}>{row.status}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="instrument-monitoring-section">
            <div className="instrument-monitoring-section-header">
              <div>
                <div className="panel-title">Monitoring</div>
                <div className="instrument-section-title">Open Items</div>
              </div>
            </div>
            {monitoringAlertRows.length ? (
              <div className="instrument-monitoring-notes">
                <ul className="bullet-list">
                  {monitoringAlertRows.map((item, index) => (
                    <li key={`${item}-${index}`}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="instrument-placeholder instrument-monitoring-placeholder">No monitoring items yet.</div>
            )}
          </section>
        </section>
      ) : null}

    </div>
  )
}
