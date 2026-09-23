import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { usePortfolioAccess } from '../components/PortfolioAccessProvider'
import { FormEvent, useEffect, useMemo, useRef, useState, type DragEvent as ReactDragEvent, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent, type ReactElement, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router'

import CalculationStatus from '../components/CalculationStatus'
import InfoHint from '../components/InfoHint'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  createPortfolioInstrumentUniverseRecord,
  createPortfolioTaxonomy,
  createPortfolioTaxonomyAssignment,
  createPortfolioTaxonomyNode,
  deletePortfolioInstrumentUniverseRecord,
  deletePortfolioTaxonomy,
  deletePortfolioTaxonomyNode,
  getHoldingsWorkspace,
  getPortfolioInstruments,
  getPortfolioAccountsWorkspace,
  getPortfolioTaxonomyCatalog,
  updatePortfolioTaxonomy,
  updatePortfolioTaxonomyAssignment,
  updatePortfolioTaxonomyNode,
  savePortfolioTaxonomyTargetConfiguration,
  type InstrumentCore,
  type HoldingsWorkspaceResponse,
  type PortfolioAccountsWorkspaceResponse,
  type PortfolioInstrumentUniverseRecord,
  type PortfolioTargetMemberType,
  type PortfolioTargetSetLineRecord,
  type PortfolioTargetSetRecord,
  type PortfolioTaxonomyAssignmentRecord,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioTaxonomyRecord,
  type SharedInstrumentRecord,
  type TaxonomyAssignmentScope,
} from '../lib/api'
import { formatCurrency, formatPercent } from '../lib/format'
import { completeAmountSum } from '../lib/holdingAmounts'
import {
  TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE,
  canDragTaxonomyEntity,
  canDropTaxonomyEntity,
} from '../lib/taxonomyInteractionPolicy'
import {
  formatTargetSetIntegrityNotice,
  targetSetIntegrityIssuesForTaxonomy,
} from '../lib/taxonomyTargetIntegrity'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import SecurityInstrumentPicker from '../components/SecurityInstrumentPicker'
import { getConcentration, getConcentrationSettings, type ConcentrationResponse, type ConcentrationScopeKind, type ConcentrationSettingsRecord } from '../lib/concentrationApi'
import '../components/taxonomy-features.css'

type CoverageEntity = {
  entity_id: string
  entity_kind: TaxonomyAssignmentScope | 'cash_bucket'
  label: string
  supporting_label?: string
  allocation: number | null
  market_value_base: number | null
  current_assignment: PortfolioTaxonomyAssignmentRecord | null
  current_node: PortfolioTaxonomyNodeRecord | null
  holding_state: 'held' | 'not_held'
  instrument_state: 'held' | 'former' | 'observe' | 'contract' | null
  instrument_state_label: string | null
  coverage_state: 'unassigned' | 'ambiguous' | 'assigned'
}

type PendingTaxonomyDelete =
  | {
      kind: 'taxonomy'
      portfolioId: string
      taxonomy: PortfolioTaxonomyRecord
      wasSelected: boolean
    }
  | {
      kind: 'node'
      portfolioId: string
      taxonomyId: string
      node: PortfolioTaxonomyNodeRecord
    }
  | {
      kind: 'observed-instrument'
      portfolioId: string
      entity: CoverageEntity
    }

type NodeAggregate = {
  current_entity_count: number
  current_weight: number | null
  current_value_base: number | null
  direct_assignment_count: number
}

type NodeAggregateWithCoverage = NodeAggregate & {
  held_entity_count: number
}

type TargetLineDraft = {
  target_value: string
  notes: string
}

type TargetMemberType = PortfolioTargetMemberType

type TargetScopeMember = {
  member_key: string
  target_member_type: TargetMemberType
  target_member_id: string
  taxonomy_node_id: string | null
  node: PortfolioTaxonomyNodeRecord | null
  entity: CoverageEntity | null
  label: string
  system_role?: 'cash' | 'derivatives'
}

type TargetSetDraft = {
  name: string
  status: string
  notes: string
  lines_by_member_key: Record<string, TargetLineDraft>
}

type TargetSetValidation = {
  errors: string[]
}

type AllocationBasis = 'weight' | 'risk_budget'

type NodeCreateMode = 'root' | 'sibling' | 'child'

type TaxonomyContextMenuState =
  | {
      kind: 'node'
      nodeId: string
      x: number
      y: number
    }
  | {
      kind: 'entity'
      entityId: string
      x: number
      y: number
    }
  | {
      kind: 'taxonomy'
      taxonomyId: string
      x: number
      y: number
    }

type WorkspaceFetchResult = {
  catalog: PortfolioTaxonomyCatalogResponse | null
  holdingsWorkspace: HoldingsWorkspaceResponse | null
  accountsResponse: PortfolioAccountsWorkspaceResponse | null
  instrumentsResponse: { portfolio_id: string; instruments: SharedInstrumentRecord[] } | null
  workspaceError: string | null
  supplementalNotice: string | null
}

const TAXONOMY_ROOT_ROW_ID = '__taxonomy_root__'
const TAXONOMY_DERIVATIVES_ROW_ID = '__taxonomy_derivatives__'
const TAXONOMY_CASH_ROW_ID = '__taxonomy_cash__'
const TAXONOMY_UNASSIGNED_ROW_ID = '__taxonomy_unassigned__'
const ROOT_TARGET_SCOPE_KEY = '__target_scope_root__'
const DERIVATIVES_TARGET_LABEL = 'Derivatives'
const CASH_TARGET_MEMBER_ID = '__cash__'
const CASH_TARGET_LABEL = 'Cash'
const TAXONOMY_VERSION_ERROR = 'The taxonomy page and server versions do not match. Complete this environment’s upgrade, then reload.'
const CONCENTRATION_VERSION_ERROR = 'The concentration settings and page versions do not match. Complete this environment’s upgrade, then reload.'
const EMPTY_TARGET_SET_DRAFT: TargetSetDraft = {
  name: '',
  status: 'active',
  notes: '',
  lines_by_member_key: {},
}

function configurationErrorMessage(message: string, zh: boolean) {
  if (!zh) return message
  if (message === TAXONOMY_VERSION_ERROR) return '分类页面与后台版本不一致。请完成当前环境的升级后重新加载。'
  if (message === CONCENTRATION_VERSION_ERROR) return '集中度配置与页面版本不一致。请完成当前环境的升级后重新加载。'
  return message
}

function targetScopeKey(comparatorTaxonomyNodeId: string | null) {
  return comparatorTaxonomyNodeId ?? ROOT_TARGET_SCOPE_KEY
}

function targetScopeNodeId(scopeKey: string) {
  return scopeKey === ROOT_TARGET_SCOPE_KEY ? null : scopeKey
}

function primaryIdentifier(instrument: Pick<InstrumentCore, 'instrument_id' | 'identifiers'>) {
  return instrument.identifiers.find((identifier) => identifier.is_primary)?.identifier_value ?? instrument.instrument_id
}

function isCashInstrument(instrument: Pick<InstrumentCore, 'instrument_id' | 'instrument_type'> | null | undefined) {
  if (!instrument) {
    return false
  }
  return (
    instrument.instrument_type.trim().toLowerCase() === 'cash' ||
    instrument.instrument_id.trim().toLowerCase().startsWith('cash:')
  )
}

function instrumentStateForEntity(
  holdingState: 'held' | 'not_held',
  universeRecord?: PortfolioInstrumentUniverseRecord | null,
): Pick<CoverageEntity, 'instrument_state' | 'instrument_state_label'> {
  if (holdingState === 'held') {
    return { instrument_state: 'held', instrument_state_label: 'Held' }
  }
  if ((universeRecord?.transaction_count ?? 0) > 0 || universeRecord?.source === 'transaction') {
    return { instrument_state: 'former', instrument_state_label: 'Former' }
  }
  return { instrument_state: 'observe', instrument_state_label: 'Observed' }
}

function isCashHoldingRow(row: HoldingsWorkspaceResponse['rows'][number]) {
  return isCashInstrument(row.instrument_core) || row.line_id.trim().toLowerCase().startsWith('cash:')
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

function derivativeHoldingLabel(row: HoldingsWorkspaceResponse['rows'][number]) {
  return (
    row.derivative_contract?.contract_name ??
    row.position_reference_id ??
    row.derivative_contract_id ??
    row.line_id
  )
}

function accountMonetaryBalanceBase(
  row: PortfolioAccountsWorkspaceResponse['accounts'][number],
) {
  if (
    row.derived_cash_balance_base == null ||
    row.pending_settlement_base == null
  ) {
    return null
  }
  return row.derived_cash_balance_base + row.pending_settlement_base
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

function renderInstrumentStatusCell(entity: CoverageEntity) {
  if (!entity.instrument_state || !entity.instrument_state_label) {
    return null
  }
  return (
    <span
      className={`taxonomy-instrument-status taxonomy-instrument-status-${entity.instrument_state}`}
      aria-label={entity.instrument_state_label}
      title={entity.instrument_state_label}
    >
      <span className="taxonomy-instrument-status-icon" aria-hidden="true" />
    </span>
  )
}

function renderInstrumentStatusLegendItem(kind: NonNullable<CoverageEntity['instrument_state']>, label: string, count: number) {
  return (
    <span className={`taxonomy-status-legend-item taxonomy-status-legend-item-${kind}`} aria-label={`${label} ${count}`} title={`${label} ${count}`}>
      <span className="taxonomy-instrument-status-icon" aria-hidden="true" />
      <span className="taxonomy-status-legend-label">{label}</span>
      <span className="taxonomy-status-legend-count">{count}</span>
    </span>
  )
}

function allocationBasisDraftsFromNodes(nodes: PortfolioTaxonomyNodeRecord[]) {
  return nodes.reduce<Record<string, AllocationBasis>>((drafts, node) => {
    drafts[node.taxonomy_node_id] = node.allocation_basis as AllocationBasis
    return drafts
  }, {})
}

function allocationBasisDraftsEqual(
  left: Record<string, AllocationBasis>,
  right: Record<string, AllocationBasis>,
) {
  const leftKeys = Object.keys(left)
  const rightKeys = Object.keys(right)
  if (leftKeys.length !== rightKeys.length) {
    return false
  }
  return leftKeys.every((key) => left[key] === right[key])
}

function TaxonomyModal({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}) {
  const dialogRef = useModalDialog(open, onClose)
  if (!open) {
    return null
  }

  return (
    <div className="taxonomy-modal-overlay" role="presentation" onClick={onClose}>
      <div
        ref={dialogRef}
        className="taxonomy-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="taxonomy-modal-header">
          <div>
            <div className="panel-title">{title}</div>
          </div>
          <button type="button" className="table-inline-button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="taxonomy-modal-body">{children}</div>
      </div>
    </div>
  )
}

function extractErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Request failed.'
}

function percentInputFromDecimal(value?: number | null) {
  if (value == null || Number.isNaN(value)) {
    return ''
  }
  return String(Number((value * 100).toFixed(8)))
}

function parsePercentInput(value: string) {
  const normalized = value.trim()
  if (!normalized) {
    return null
  }
  const numericValue = Number(normalized)
  if (!Number.isFinite(numericValue)) {
    return Number.NaN
  }
  return numericValue / 100
}

function concentrationLimitKey(scope: ConcentrationScopeKind, entityId: string, taxonomyId?: string | null) {
  return JSON.stringify([scope, taxonomyId ?? null, entityId])
}

function concentrationDrafts(settings: ConcentrationSettingsRecord | null) {
  return Object.fromEntries((settings?.limits ?? []).map((limit) => [concentrationLimitKey(limit.scope, limit.entity_id, limit.taxonomy_id), percentInputFromDecimal(limit.limit_weight)]))
}

function coverageEntityKey(targetScope: TaxonomyAssignmentScope, entityId: string) {
  return `${targetScope}:${entityId}`
}

function targetMemberKey(targetMemberType: TargetMemberType, targetMemberId: string) {
  return `${targetMemberType}:${targetMemberId}`
}

function isSystemCashEntity(entity: CoverageEntity) {
  return entity.entity_kind === 'cash_bucket'
}

function isCashTaxonomyNode(node: PortfolioTaxonomyNodeRecord | null | undefined) {
  if (!node) {
    return false
  }
  const normalizedName = node.node_name.trim().toLowerCase()
  const normalizedCode = (node.node_code ?? '').trim().toLowerCase()
  return normalizedCode === 'cash' || normalizedName === 'cash' || normalizedName === '现金'
}

function buildTargetSetDraft(args: {
  targetSet: PortfolioTargetSetRecord | null
  targetSetLines: PortfolioTargetSetLineRecord[]
  targetMembers: TargetScopeMember[]
  defaultName: string
}) {
  const { targetSet, targetSetLines, targetMembers, defaultName } = args
  return {
    name: targetSet?.name ?? defaultName,
    status: targetSet?.status ?? 'active',
    notes: targetSet?.notes ?? '',
    lines_by_member_key: Object.fromEntries(targetMembers.map((member) => {
      const line = targetSetLines.find((item) => targetMemberKey(item.target_member_type, item.target_member_id) === member.member_key)
      return [member.member_key, { target_value: percentInputFromDecimal(line?.target_value), notes: line?.notes ?? '' }]
    })),
  } satisfies TargetSetDraft
}

function targetLineDraftsEqual(left: TargetLineDraft, right: TargetLineDraft) {
  return left.target_value === right.target_value && left.notes === right.notes
}

function targetSetDraftsEqual(left: TargetSetDraft, right: TargetSetDraft) {
  const leftMemberKeys = Object.keys(left.lines_by_member_key)
  const rightMemberKeys = Object.keys(right.lines_by_member_key)
  return (
    left.name === right.name &&
    left.status === right.status &&
    left.notes === right.notes &&
    leftMemberKeys.length === rightMemberKeys.length &&
    leftMemberKeys.every((memberKey) => {
      const leftLine = left.lines_by_member_key[memberKey]
      const rightLine = right.lines_by_member_key[memberKey]
      return Boolean(rightLine) && targetLineDraftsEqual(leftLine, rightLine)
    })
  )
}

function targetDraftScopesEqual(
  left: Record<string, { saa: TargetSetDraft; taa: TargetSetDraft }>,
  right: Record<string, { saa: TargetSetDraft; taa: TargetSetDraft }>,
) {
  const leftScopeKeys = Object.keys(left)
  const rightScopeKeys = Object.keys(right)
  return (
    leftScopeKeys.length === rightScopeKeys.length &&
    leftScopeKeys.every((scopeKey) => {
      const leftScopeDraft = left[scopeKey]
      const rightScopeDraft = right[scopeKey]
      return (
        Boolean(rightScopeDraft) &&
        targetSetDraftsEqual(leftScopeDraft.saa, rightScopeDraft.saa) &&
        targetSetDraftsEqual(leftScopeDraft.taa, rightScopeDraft.taa)
      )
    })
  )
}

function validateTargetSetDraft(draft: TargetSetDraft, members: TargetScopeMember[], basis: AllocationBasis, zh = false) {
  const errors: string[] = []
  const securities = members.filter((member) => member.system_role !== 'cash')
  const values = securities.map((member) => ({ member, value: parsePercentInput(draft.lines_by_member_key[member.member_key]?.target_value ?? '') }))
  const cashMember = members.find((member) => member.system_role === 'cash')
  const cash = cashMember ? parsePercentInput(draft.lines_by_member_key[cashMember.member_key]?.target_value ?? '') : null
  if (!values.some(({ value }) => value != null) && cash == null) return { errors }
  if (cash != null && (!Number.isFinite(cash) || cash < 0 || cash > 1)) errors.push(zh ? '现金预留须为组合 NAV 的 0% 至 100%。' : 'Cash reserve must be between 0% and 100% of NAV.')
  for (const { member, value } of values) {
    if (value == null) errors.push(zh ? `${member.label} 尚未填写目标。请补齐本层全部成员，或清空本层目标。` : `Missing target for ${member.label}. Fill this entire level or clear all its targets.`)
    else if (!Number.isFinite(value) || value < 0 || value > 1) errors.push(zh ? `${member.label} 的目标须为 0% 至 100%。` : `Target for ${member.label} must be between 0% and 100%.`)
  }
  if (!errors.length) {
    const total = values.reduce((sum, { value }) => sum + (value ?? 0), 0)
    const allCash = cash === 1 && basis === 'weight' && total === 0
    if (values.length && !allCash && Math.abs(total - 1) > 1e-6) errors.push(zh ? '同一父层内的证券目标须合计 100%，不含现金预留。' : 'Security targets must total 100% within their parent, excluding the cash reserve.')
  }
  return { errors } satisfies TargetSetValidation
}

function sortNodes(nodes: PortfolioTaxonomyNodeRecord[]) {
  return [...nodes].sort((left, right) => {
    if (left.sort_order !== right.sort_order) {
      return left.sort_order - right.sort_order
    }
    return left.node_name.localeCompare(right.node_name) || left.taxonomy_node_id.localeCompare(right.taxonomy_node_id)
  })
}

function buildChildrenByParent(nodes: PortfolioTaxonomyNodeRecord[]) {
  const map = new Map<string | null, PortfolioTaxonomyNodeRecord[]>()
  sortNodes(nodes).forEach((node) => {
    const parentKey = node.parent_taxonomy_node_id ?? null
    const currentChildren = map.get(parentKey) ?? []
    currentChildren.push(node)
    map.set(parentKey, currentChildren)
  })
  return map
}

async function fetchWorkspace(portfolioId: string): Promise<WorkspaceFetchResult> {
  const [catalogResult, holdingsResult, accountsResult, instrumentsResult] = await Promise.allSettled([
    getPortfolioTaxonomyCatalog(portfolioId),
    getHoldingsWorkspace(portfolioId, { include_details: true }),
    getPortfolioAccountsWorkspace(portfolioId),
    getPortfolioInstruments(portfolioId),
  ])

  const supplementalMessages: string[] = []
  let catalog = catalogResult.status === 'fulfilled' ? catalogResult.value : null
  let workspaceError = catalogResult.status === 'rejected' ? extractErrorMessage(catalogResult.reason) : null
  if (catalog && (!Number.isInteger(catalog.taxonomy_configuration_version) || !Array.isArray(catalog.target_resolution))) {
    catalog = null
    workspaceError = TAXONOMY_VERSION_ERROR
  }
  const holdingsWorkspace = holdingsResult.status === 'fulfilled' ? holdingsResult.value : null
  const accountsResponse = accountsResult.status === 'fulfilled' ? accountsResult.value : null
  const instrumentsResponse = instrumentsResult.status === 'fulfilled' ? instrumentsResult.value : null

  if (holdingsResult.status === 'rejected') {
    supplementalMessages.push(`Current holdings coverage unavailable: ${extractErrorMessage(holdingsResult.reason)}`)
  }
  if (accountsResult.status === 'rejected') {
    supplementalMessages.push(`Account coverage unavailable: ${extractErrorMessage(accountsResult.reason)}`)
  }
  if (instrumentsResult.status === 'rejected') {
    supplementalMessages.push(`Instrument registry unavailable: ${extractErrorMessage(instrumentsResult.reason)}`)
  }
  return {
    catalog,
    holdingsWorkspace,
    accountsResponse,
    instrumentsResponse,
    workspaceError,
    supplementalNotice: supplementalMessages.length ? supplementalMessages.join(' ') : null,
  }
}

export default function TaxonomiesPage() {
  const zh = useLanguage().language === 'zh-Hans'
  const canEditPortfolio = Boolean(usePortfolioAccess()?.can_edit)
  const { portfolioId = '' } = useParams()
  const currentPortfolioIdRef = useRef(portfolioId)
  const workspaceRequestRef = useRef(0)
  const mountedRef = useRef(false)
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])
  const [searchParams, setSearchParams] = useSearchParams()
  const [catalog, setCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [holdingsWorkspace, setHoldingsWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [accountsResponse, setAccountsResponse] = useState<PortfolioAccountsWorkspaceResponse | null>(null)
  const [instrumentsResponse, setInstrumentsResponse] = useState<{ portfolio_id: string; instruments: SharedInstrumentRecord[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [supplementalNotice, setSupplementalNotice] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionPending, setActionPending] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<PendingTaxonomyDelete | null>(null)
  const [taxonomyName, setTaxonomyName] = useState('')
  const [taxonomyPickerOpen, setTaxonomyPickerOpen] = useState(false)
  const [rootAllocationBasisDraft, setRootAllocationBasisDraft] = useState<AllocationBasis>('weight')
  const [concentration, setConcentration] = useState<ConcentrationResponse | null>(null)
  const [concentrationError, setConcentrationError] = useState<string | null>(null)
  const [concentrationRevision, setConcentrationRevision] = useState(0)
  const [concentrationSettings, setConcentrationSettings] = useState<ConcentrationSettingsRecord | null>(null)
  const [concentrationSettingsError, setConcentrationSettingsError] = useState<string | null>(null)
  const [limitDrafts, setLimitDrafts] = useState<Record<string, string>>({})
  const [taxonomyConcentrationEnabled, setTaxonomyConcentrationEnabled] = useState(false)
  const [taxonomyRenameId, setTaxonomyRenameId] = useState<string | null>(null)
  const [taxonomyRenameName, setTaxonomyRenameName] = useState('')
  const taxonomyPickerRef = useRef<HTMLDivElement | null>(null)

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [collapsedNodeIds, setCollapsedNodeIds] = useState<Set<string>>(new Set())
  const [selectedEntityIds, setSelectedEntityIds] = useState<Set<string>>(new Set())
  const [instrumentAddNodeId, setInstrumentAddNodeId] = useState('')
  const [instrumentAddInstrumentId, setInstrumentAddInstrumentId] = useState('')

  const [newNodeName, setNewNodeName] = useState('')
  const [targetDraftsByScope, setTargetDraftsByScope] = useState<
    Record<string, { saa: TargetSetDraft; taa: TargetSetDraft }>
  >({})
  const [allocationBasisDraftsByNodeId, setAllocationBasisDraftsByNodeId] = useState<Record<string, AllocationBasis>>({})
  const [targetEditMode, setTargetEditMode] = useState(false)
  const [editingConfigurationVersion, setEditingConfigurationVersion] = useState<number | null>(null)
  const [showTaxonomyCreate, setShowTaxonomyCreate] = useState(false)
  const [showTaxonomyRename, setShowTaxonomyRename] = useState(false)
  const [showInstrumentAdd, setShowInstrumentAdd] = useState(false)
  const [showNodeCreate, setShowNodeCreate] = useState(false)
  const [showNodeEdit, setShowNodeEdit] = useState(false)
  const [nodeCreateMode, setNodeCreateMode] = useState<NodeCreateMode>('root')
  const [nodeCreateParentId, setNodeCreateParentId] = useState('')
  const [nodeCreateAnchorNodeId, setNodeCreateAnchorNodeId] = useState<string | null>(null)
  const [nodeEditId, setNodeEditId] = useState<string | null>(null)
  const [nodeEditName, setNodeEditName] = useState('')
  const [contextMenuState, setContextMenuState] = useState<TaxonomyContextMenuState | null>(null)
  const [dragTargetNodeId, setDragTargetNodeId] = useState<string | null>(null)

  currentPortfolioIdRef.current = portfolioId

  useEffect(() => {
    setPendingDelete(null)
    setTargetEditMode(false)
    setActionPending(null)
    setActionError(null)
    setNotice(null)
    setRefreshing(false)
    if (!portfolioId) {
      setCatalog(null)
      setHoldingsWorkspace(null)
      setAccountsResponse(null)
      setInstrumentsResponse(null)
      setWorkspaceError(null)
      setSupplementalNotice(null)
      setLoading(false)
      return
    }

    const requestId = ++workspaceRequestRef.current
    let cancelled = false
    setLoading(true)

    fetchWorkspace(portfolioId)
      .then((result) => {
        if (cancelled || requestId !== workspaceRequestRef.current) {
          return
        }
        setCatalog(result.catalog)
        setHoldingsWorkspace(result.holdingsWorkspace)
        setAccountsResponse(result.accountsResponse)
        setInstrumentsResponse(result.instrumentsResponse)
        setWorkspaceError(result.workspaceError)
        setSupplementalNotice(result.supplementalNotice)
      })
      .finally(() => {
        if (!cancelled && requestId === workspaceRequestRef.current) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    if (!notice) {
      return undefined
    }
    const timeoutId = window.setTimeout(() => setNotice(null), 2800)
    return () => window.clearTimeout(timeoutId)
  }, [notice])

  async function reloadWorkspace() {
    if (!portfolioId) {
      return
    }
    const requestedPortfolioId = portfolioId
    const requestId = ++workspaceRequestRef.current
    setRefreshing(true)
    try {
      const result = await fetchWorkspace(requestedPortfolioId)
      if (currentPortfolioIdRef.current !== requestedPortfolioId || requestId !== workspaceRequestRef.current) {
        return
      }
      setCatalog(result.catalog)
      setHoldingsWorkspace(result.holdingsWorkspace)
      setAccountsResponse(result.accountsResponse)
      setInstrumentsResponse(result.instrumentsResponse)
      setWorkspaceError(result.workspaceError)
      setSupplementalNotice(result.supplementalNotice)
    } finally {
      if (currentPortfolioIdRef.current === requestedPortfolioId && requestId === workspaceRequestRef.current) {
        setRefreshing(false)
      }
    }
  }

  function updateSearchParam(key: string, value: string | null) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      const normalizedValue = value && value.trim() ? value : null
      if (normalizedValue) {
        next.set(key, normalizedValue)
      } else {
        next.delete(key)
      }
      return next
    })
  }

  const taxonomies = catalog?.taxonomies ?? []
  const taxonomyNodes = catalog?.taxonomy_nodes ?? []
  const taxonomyAssignments = catalog?.taxonomy_assignments ?? []
  const requestedTaxonomyId = searchParams.get('taxonomy_id') ?? ''
  const resolvedSelectedTaxonomyId =
    taxonomies.find((taxonomy) => taxonomy.taxonomy_id === requestedTaxonomyId)?.taxonomy_id || taxonomies[0]?.taxonomy_id || ''
  const selectedTaxonomy = taxonomies.find((taxonomy) => taxonomy.taxonomy_id === resolvedSelectedTaxonomyId) ?? null
  const workspaceIdentityKey = `${portfolioId}:${resolvedSelectedTaxonomyId}`
  const workspaceIdentityRef = useRef({ key: workspaceIdentityKey, generation: 0 })
  if (workspaceIdentityRef.current.key !== workspaceIdentityKey) {
    workspaceIdentityRef.current = { key: workspaceIdentityKey, generation: workspaceIdentityRef.current.generation + 1 }
  }
  function isCurrentView(identity: typeof workspaceIdentityRef.current) {
    return mountedRef.current && workspaceIdentityRef.current === identity
  }
  useEffect(() => {
    setTargetEditMode(false)
    setEditingConfigurationVersion(null)
    setActionPending(null)
    setActionError(null)
    setNotice(null)
    setPendingDelete(null)
    setSelectedEntityIds(new Set())
    setSelectedNodeId(null)
    setCollapsedNodeIds(new Set())
    setContextMenuState(null)
    closeModalStack()
  }, [workspaceIdentityKey, canEditPortfolio])
  const selectedTaxonomyNodes = useMemo(
    () =>
      taxonomyNodes.filter(
        (node) =>
          node.taxonomy_id === resolvedSelectedTaxonomyId &&
          !isCashTaxonomyNode(node),
      ),
    [resolvedSelectedTaxonomyId, taxonomyNodes],
  )
  const selectedTaxonomyAssignments = useMemo(
    () => taxonomyAssignments.filter((assignment) => assignment.taxonomy_id === resolvedSelectedTaxonomyId),
    [resolvedSelectedTaxonomyId, taxonomyAssignments],
  )
  const holdingsRows = holdingsWorkspace?.rows ?? []
  const instrumentRows = instrumentsResponse?.instruments ?? []
  const instrumentUniverseRows = catalog?.instrument_universe ?? []
  const baseCurrency = holdingsWorkspace?.base_currency ?? accountsResponse?.base_currency ?? null
  const accountRows = accountsResponse?.accounts ?? []

  function formatReportAmount(value: number | null | undefined) {
    return value != null && baseCurrency ? formatCurrency(value, baseCurrency) : '—'
  }

  useEffect(() => {
    if (!targetEditMode) setRootAllocationBasisDraft(selectedTaxonomy?.root_allocation_basis ?? 'weight')
  }, [selectedTaxonomy, targetEditMode])

  useEffect(() => {
    let current = true
    setConcentration(null)
    setConcentrationError(null)
    if (!portfolioId || !catalog || !holdingsWorkspace?.as_of_date) return
    getConcentration(portfolioId, holdingsWorkspace.as_of_date).then((result) => {
      if (current) setConcentration(result)
    }).catch((error) => { if (current) setConcentrationError(extractErrorMessage(error)) })
    return () => { current = false }
  }, [portfolioId, holdingsWorkspace?.as_of_date, catalog?.taxonomy_configuration_version, concentrationRevision])

  useEffect(() => {
    if (targetEditMode) return
    let current = true
    setConcentrationSettings(null)
    setConcentrationSettingsError(null)
    if (!portfolioId || !catalog || !holdingsWorkspace?.as_of_date) return
    getConcentrationSettings(portfolioId, holdingsWorkspace.as_of_date).then((settings) => {
      if (!Number.isInteger(settings.latest_revision) || !Array.isArray(settings.enabled_taxonomy_ids) || !Array.isArray(settings.limits)) {
        throw new Error(CONCENTRATION_VERSION_ERROR)
      }
      if (current) setConcentrationSettings(settings)
    }).catch((error) => { if (current) setConcentrationSettingsError(extractErrorMessage(error)) })
    return () => { current = false }
  }, [portfolioId, holdingsWorkspace?.as_of_date, catalog?.taxonomy_configuration_version, concentrationRevision, targetEditMode])

  useEffect(() => {
    if (targetEditMode) return
    setLimitDrafts(concentrationDrafts(concentrationSettings))
    setTaxonomyConcentrationEnabled(concentrationSettings?.enabled_taxonomy_ids.includes(resolvedSelectedTaxonomyId) ?? false)
  }, [concentrationSettings, resolvedSelectedTaxonomyId, targetEditMode])

  const hasConcentrationChanges = Boolean(concentrationSettings) && (
    taxonomyConcentrationEnabled !== concentrationSettings!.enabled_taxonomy_ids.includes(resolvedSelectedTaxonomyId) ||
    Object.entries(limitDrafts).some(([key, value]) => value !== (concentrationDrafts(concentrationSettings)[key] ?? ''))
  )
  const concentrationValidationError = Object.values(limitDrafts).some((value) => value.trim() && (!Number.isFinite(Number(value)) || Number(value) < 0))
    ? (zh ? '集中度上限必须为非负百分比；留空表示不设上限。' : 'Concentration limits must be non-negative percentages; leave blank for no limit.') : ''

  function buildConcentrationPayload() {
    const enabled = new Set(concentrationSettings!.enabled_taxonomy_ids)
    if (taxonomyConcentrationEnabled) enabled.add(resolvedSelectedTaxonomyId)
    else enabled.delete(resolvedSelectedTaxonomyId)
    return {
      expected_revision: concentrationSettings!.latest_revision,
      effective_from: holdingsWorkspace!.as_of_date,
      enabled_taxonomy_ids: [...enabled],
      limits: Object.entries(limitDrafts).filter(([, value]) => value.trim()).map(([key, value]) => {
        const [scope, taxonomy_id, entity_id] = JSON.parse(key) as [ConcentrationScopeKind, string | null, string]
        return { scope, taxonomy_id, entity_id, limit_weight: Number(value) / 100 }
      }),
      fcn_allocations: concentrationSettings!.fcn_allocations,
    }
  }

  useEffect(() => {
    const refresh = (event: Event) => {
      if ((event as CustomEvent<{ portfolioId?: string }>).detail?.portfolioId === portfolioId) {
        setConcentrationRevision((value) => value + 1)
      }
    }
    window.addEventListener('portfolio-concentration-settings-updated', refresh)
    return () => window.removeEventListener('portfolio-concentration-settings-updated', refresh)
  }, [portfolioId])

  useEffect(() => {
    const nextDrafts = allocationBasisDraftsFromNodes(selectedTaxonomyNodes)
    setAllocationBasisDraftsByNodeId((current) => {
      if (!targetEditMode) {
        return allocationBasisDraftsEqual(current, nextDrafts) ? current : nextDrafts
      }
      const mergedDrafts = selectedTaxonomyNodes.reduce<Record<string, AllocationBasis>>((drafts, node) => {
        drafts[node.taxonomy_node_id] =
          current[node.taxonomy_node_id] ?? (node.allocation_basis as AllocationBasis)
        return drafts
      }, {})
      return allocationBasisDraftsEqual(current, mergedDrafts) ? current : mergedDrafts
    })
  }, [selectedTaxonomyNodes, targetEditMode])

  useEffect(() => {
    if (!taxonomies.length) {
      setShowTaxonomyCreate(true)
    }
  }, [taxonomies.length])

  useEffect(() => {
    setInstrumentAddInstrumentId('')
  }, [resolvedSelectedTaxonomyId])

  const nodeById = useMemo(() => {
    const lookup = new Map<string, PortfolioTaxonomyNodeRecord>()
    selectedTaxonomyNodes.forEach((node) => {
      lookup.set(node.taxonomy_node_id, node)
    })
    return lookup
  }, [selectedTaxonomyNodes])

  const childrenByParent = useMemo(() => buildChildrenByParent(selectedTaxonomyNodes), [selectedTaxonomyNodes])

  useEffect(() => {
    if (!selectedTaxonomy) {
      if (selectedNodeId !== null) {
        setSelectedNodeId(null)
      }
      setCollapsedNodeIds((current) => (current.size ? new Set() : current))
      setSelectedEntityIds((current) => (current.size ? new Set() : current))
      return
    }
    const preferredNodeId = selectedNodeId && nodeById.has(selectedNodeId) ? selectedNodeId : null
    if (preferredNodeId !== selectedNodeId) {
      setSelectedNodeId(preferredNodeId)
    }
  }, [nodeById, selectedNodeId, selectedTaxonomy])

  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) ?? null : null

  useEffect(() => {
    if (!selectedTaxonomy) {
      closeModalStack()
      setNodeCreateMode('root')
      setNodeCreateParentId('')
      setNodeCreateAnchorNodeId(null)
      setContextMenuState(null)
    }
  }, [selectedTaxonomy])

  useEffect(() => {
    if (!taxonomyPickerOpen) {
      return
    }
    function handleDocumentPointerDown(event: PointerEvent) {
      if (!taxonomyPickerRef.current?.contains(event.target as Node)) {
        setTaxonomyPickerOpen(false)
      }
    }
    document.addEventListener('pointerdown', handleDocumentPointerDown)
    return () => document.removeEventListener('pointerdown', handleDocumentPointerDown)
  }, [taxonomyPickerOpen])

  useEffect(() => {
    if (!contextMenuState) {
      return
    }
    const handleWindowPointer = () => setContextMenuState(null)
    window.addEventListener('click', handleWindowPointer)
    window.addEventListener('scroll', handleWindowPointer, true)
    return () => {
      window.removeEventListener('click', handleWindowPointer)
      window.removeEventListener('scroll', handleWindowPointer, true)
    }
  }, [contextMenuState])

  useEffect(() => {
    function handleWindowKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setContextMenuState(null)
        closeModalStack()
      }
    }

    window.addEventListener('keydown', handleWindowKeydown)
    return () => window.removeEventListener('keydown', handleWindowKeydown)
  }, [])

  useEffect(() => {
    if (!targetEditMode) {
      return
    }
    setDragTargetNodeId(null)
    setContextMenuState(null)
  }, [targetEditMode])

  const activeAssignments = useMemo(
    () =>
      selectedTaxonomyAssignments.filter(
        (assignment) => assignment.status === 'active' && nodeById.has(assignment.taxonomy_node_id),
      ),
    [nodeById, selectedTaxonomyAssignments],
  )

  const activeAssignmentsByEntityKey = useMemo(() => {
    const lookup = new Map<string, PortfolioTaxonomyAssignmentRecord[]>()
    activeAssignments.forEach((assignment) => {
      const assignmentKey = coverageEntityKey(assignment.target_scope, assignment.target_entity_id)
      const currentAssignments = lookup.get(assignmentKey) ?? []
      currentAssignments.push(assignment)
      lookup.set(assignmentKey, currentAssignments)
    })
    return lookup
  }, [activeAssignments])

  const instrumentById = useMemo(() => {
    const lookup = new Map<string, SharedInstrumentRecord>()
    instrumentRows.forEach((instrument) => {
      lookup.set(instrument.instrument_id, instrument)
    })
    return lookup
  }, [instrumentRows])

  const universeInstrumentById = useMemo(() => {
    const lookup = new Map<string, InstrumentCore>()
    instrumentUniverseRows.forEach((item) => {
      if (!item.instrument_id || !item.instrument_ref) {
        return
      }
      lookup.set(item.instrument_id, item.instrument_ref)
    })
    return lookup
  }, [instrumentUniverseRows])

  const universeRecordByInstrumentId = useMemo(() => {
    const lookup = new Map<string, PortfolioInstrumentUniverseRecord>()
    instrumentUniverseRows.forEach((item) => {
      if (item.instrument_id) {
        lookup.set(item.instrument_id, item)
      }
    })
    return lookup
  }, [instrumentUniverseRows])

  const visibleCashAccounts = useMemo(
    () =>
      accountRows.filter((accountRow) => {
        const monetaryBalance = accountMonetaryBalanceBase(accountRow)
        return (
          accountRow.account.account_category === 'cash' ||
          monetaryBalance == null ||
          (monetaryBalance != null && Math.abs(monetaryBalance) > 1e-9)
        )
      }),
    [accountRows],
  )
  const securitiesHoldingRows = useMemo(
    () =>
      holdingsRows.filter(
        (row): row is typeof row & { instrument_core: InstrumentCore } =>
          row.holding_category === 'securities' &&
          row.instrument_core !== null &&
          (row.holding_kind ?? 'position') === 'position' &&
          !isCashHoldingRow(row) &&
          !isPendingMonetaryHoldingRow(row),
      ),
    [holdingsRows],
  )
  const derivativeHoldingRows = useMemo(
    () => holdingsRows.filter((row) => row.holding_category === 'derivatives'),
    [holdingsRows],
  )
  const contractLinkedInstruments = useMemo(() => {
    const linked = new Map<string, { name: string; contracts: string[] }>()
    derivativeHoldingRows.forEach((row) => {
      const contract = row.derivative_contract
      if (contract?.contract_type !== 'fcn' || !row.quantity) return
      contract.terms.underlyings.forEach((underlying) => {
        const quote = row.fcn_risk?.underlyings.find((item) => item.instrument_id === underlying.instrument_id)
        const core = instrumentById.get(underlying.instrument_id) ?? universeInstrumentById.get(underlying.instrument_id)
        const item = linked.get(underlying.instrument_id) ?? {
          name: core?.instrument_name ?? quote?.instrument_name ?? underlying.instrument_id,
          contracts: [],
        }
        if (!item.contracts.includes(contract.contract_name)) item.contracts.push(contract.contract_name)
        linked.set(underlying.instrument_id, item)
      })
    })
    return linked
  }, [derivativeHoldingRows, instrumentById, universeInstrumentById])
  const displayedBookValueBase = useMemo(
    () =>
      holdingsWorkspace && accountsResponse ? completeAmountSum([
        ...securitiesHoldingRows.map((row) => row.market_value_base),
        ...derivativeHoldingRows.map((row) => row.market_value_base),
        ...visibleCashAccounts.map((accountRow) => accountMonetaryBalanceBase(accountRow)),
      ]) : null,
    [holdingsWorkspace, accountsResponse, derivativeHoldingRows, securitiesHoldingRows, visibleCashAccounts],
  )

  const portfolioNav = holdingsWorkspace?.totals.nav ?? null

  const currentEntities = useMemo<CoverageEntity[]>(() => {
    if (!selectedTaxonomy) {
      return []
    }

      const holdingEntities = securitiesHoldingRows.map((row) => {
        const assignments = activeAssignmentsByEntityKey.get(coverageEntityKey('instrument', row.instrument_core.instrument_id)) ?? []
        const assignment = assignments.length === 1 ? assignments[0] : null
        const currentNode = assignment ? nodeById.get(assignment.taxonomy_node_id) ?? null : null
        let coverageState: CoverageEntity['coverage_state'] = 'unassigned'
        if (assignments.length > 1) {
          coverageState = 'ambiguous'
        } else if (assignment) {
          coverageState = 'assigned'
        }

        return {
          entity_id: row.instrument_core.instrument_id,
          entity_kind: 'instrument' as const,
          label: `${primaryIdentifier(row.instrument_core)} · ${row.instrument_core.instrument_name}`,
          allocation:
            row.market_value_base != null && portfolioNav != null && portfolioNav > 1e-9
              ? row.market_value_base / portfolioNav
              : null,
          market_value_base: row.market_value_base ?? null,
          current_assignment: assignment,
          current_node: currentNode,
          holding_state: 'held',
          ...instrumentStateForEntity('held', universeRecordByInstrumentId.get(row.instrument_core.instrument_id)),
          coverage_state: coverageState,
        } satisfies CoverageEntity
      })

      const heldInstrumentIds = new Set(securitiesHoldingRows.map((row) => row.instrument_core.instrument_id))
      const visibleNonHeldInstrumentIds = new Set<string>()
      contractLinkedInstruments.forEach((_item, id) => {
        if (!heldInstrumentIds.has(id)) visibleNonHeldInstrumentIds.add(id)
      })
      instrumentUniverseRows.forEach((item) => {
        if (
          item.status === 'active' &&
          item.instrument_id &&
          !heldInstrumentIds.has(item.instrument_id) &&
          !isCashInstrument(item.instrument_ref)
        ) {
          visibleNonHeldInstrumentIds.add(item.instrument_id)
        }
      })
      activeAssignments.forEach((assignment) => {
        const assignedInstrument =
          instrumentById.get(assignment.target_entity_id) ?? universeInstrumentById.get(assignment.target_entity_id) ?? null
        if (
          assignment.target_scope === 'instrument' &&
          !heldInstrumentIds.has(assignment.target_entity_id) &&
          !assignment.target_entity_id.trim().toLowerCase().startsWith('cash:') &&
          !isCashInstrument(assignedInstrument)
        ) {
          visibleNonHeldInstrumentIds.add(assignment.target_entity_id)
        }
      })

      const nonHeldInstrumentEntities = Array.from(visibleNonHeldInstrumentIds)
        .sort((left, right) => {
          const leftInstrument = instrumentById.get(left) ?? universeInstrumentById.get(left)
          const rightInstrument = instrumentById.get(right) ?? universeInstrumentById.get(right)
          return (leftInstrument?.instrument_name ?? left).localeCompare(rightInstrument?.instrument_name ?? right)
        })
        .reduce<CoverageEntity[]>((entities, instrumentId) => {
          if (entities.some((entity) => entity.entity_id === instrumentId)) {
            return entities
          }
          const assignments =
            activeAssignmentsByEntityKey.get(coverageEntityKey('instrument', instrumentId)) ?? []
          const currentAssignment = assignments.length === 1 ? assignments[0] : null
          const currentNode = currentAssignment ? nodeById.get(currentAssignment.taxonomy_node_id) ?? null : null
          const instrument = instrumentById.get(instrumentId) ?? universeInstrumentById.get(instrumentId) ?? null
          const universeRecord = universeRecordByInstrumentId.get(instrumentId) ?? null
          const contractLink = contractLinkedInstruments.get(instrumentId)
          const explicitlyTargeted = (catalog?.target_set_lines ?? []).some((line) =>
            line.target_member_type === 'instrument' && line.target_member_id === instrumentId &&
            (catalog?.target_sets ?? []).some((target) => target.taxonomy_id === selectedTaxonomy.taxonomy_id && target.status === 'active' && target.target_set_id === line.target_set_id))
          const contractOnly = Boolean(contractLink) && !(universeRecord?.transaction_count) && universeRecord?.source !== 'manual' && !explicitlyTargeted
          let coverageState: CoverageEntity['coverage_state'] = 'unassigned'
          if (assignments.length > 1) {
            coverageState = 'ambiguous'
          } else if (currentAssignment) {
            coverageState = 'assigned'
          }
          entities.push({
            entity_id: instrumentId,
            entity_kind: 'instrument',
            label: instrument ? `${primaryIdentifier(instrument)} · ${instrument.instrument_name}` : contractLink?.name ?? instrumentId,
            supporting_label: contractLink ? `FCN: ${contractLink.contracts.join(', ')}` : undefined,
            allocation: null,
            market_value_base: null,
            current_assignment: currentAssignment,
            current_node: currentNode,
            holding_state: 'not_held',
            ...(contractOnly
              ? { instrument_state: 'contract' as const, instrument_state_label: zh ? '合约关联' : 'Contract linked' }
              : instrumentStateForEntity('not_held', universeRecord)),
            coverage_state: coverageState,
          })
          return entities
        }, [])

      return [...holdingEntities, ...nonHeldInstrumentEntities]
  }, [
    activeAssignments,
    activeAssignmentsByEntityKey,
    instrumentById,
    instrumentUniverseRows,
    nodeById,
    securitiesHoldingRows,
    selectedTaxonomy,
    portfolioNav,
    universeInstrumentById,
    universeRecordByInstrumentId,
    contractLinkedInstruments,
    catalog?.target_sets,
    catalog?.target_set_lines,
    zh,
  ])

  const coverageSummary = useMemo(() => {
    const currentEntityCount = currentEntities.length
    const unassignedEntities = currentEntities.filter((entity) => entity.coverage_state === 'unassigned')
    const ambiguousEntities = currentEntities.filter((entity) => entity.coverage_state === 'ambiguous')
    const heldEntityCount = currentEntities.filter((entity) => entity.holding_state === 'held').length
    const formerEntityCount = currentEntities.filter((entity) => entity.instrument_state === 'former').length
    const observeEntityCount = currentEntities.filter((entity) => entity.instrument_state === 'observe').length
    const assignedCount = currentEntityCount - unassignedEntities.length - ambiguousEntities.length
    return {
      currentEntityCount,
      assignedCount,
      heldEntityCount,
      formerEntityCount,
      observeEntityCount,
      unassignedEntities,
      ambiguousEntities,
      coveragePct: currentEntityCount ? assignedCount / currentEntityCount : null,
    }
  }, [currentEntities])

  const directAssignmentsByNodeId = useMemo(() => {
    const lookup = new Map<string, PortfolioTaxonomyAssignmentRecord[]>()
    activeAssignments.forEach((assignment) => {
      const currentAssignments = lookup.get(assignment.taxonomy_node_id) ?? []
      currentAssignments.push(assignment)
      lookup.set(assignment.taxonomy_node_id, currentAssignments)
    })
    return lookup
  }, [activeAssignments])

  const nodeAggregates = useMemo(() => {
    const directEntitiesByNodeId = new Map<string, CoverageEntity[]>()
    currentEntities.forEach((entity) => {
      if (!entity.current_assignment || entity.coverage_state === 'ambiguous') {
        return
      }
      const currentEntitiesForNode = directEntitiesByNodeId.get(entity.current_assignment.taxonomy_node_id) ?? []
      currentEntitiesForNode.push(entity)
      directEntitiesByNodeId.set(entity.current_assignment.taxonomy_node_id, currentEntitiesForNode)
    })

    const lookup = new Map<string, NodeAggregateWithCoverage>()

    function walk(nodeId: string): NodeAggregateWithCoverage {
      const directEntities = directEntitiesByNodeId.get(nodeId) ?? []
      const directHeldEntities = directEntities.filter((entity) => entity.holding_state === 'held')
      let currentEntityCount = directEntities.length
      let heldEntityCount = directHeldEntities.length
      const weightParts = directHeldEntities.map((entity) => entity.allocation)
      const valueParts = directHeldEntities.map((entity) => entity.market_value_base)

      ;(childrenByParent.get(nodeId) ?? []).forEach((child) => {
        const childAggregate = walk(child.taxonomy_node_id)
        currentEntityCount += childAggregate.current_entity_count
        heldEntityCount += childAggregate.held_entity_count
        if (childAggregate.held_entity_count) {
          weightParts.push(childAggregate.current_weight)
          valueParts.push(childAggregate.current_value_base)
        }
      })

      const aggregate = {
        current_entity_count: currentEntityCount,
        current_weight: heldEntityCount ? completeAmountSum(weightParts) : null,
        current_value_base: heldEntityCount ? completeAmountSum(valueParts) : null,
        direct_assignment_count: (directAssignmentsByNodeId.get(nodeId) ?? []).length,
        held_entity_count: heldEntityCount,
      }
      lookup.set(nodeId, aggregate)
      return aggregate
    }

    selectedTaxonomyNodes.forEach((node) => {
      if (!lookup.has(node.taxonomy_node_id)) {
        walk(node.taxonomy_node_id)
      }
    })
    return lookup
  }, [childrenByParent, currentEntities, directAssignmentsByNodeId, selectedTaxonomyNodes])

  const directEntitiesByNodeId = useMemo(() => {
    const lookup = new Map<string, CoverageEntity[]>()
    currentEntities.forEach((entity) => {
      if (!entity.current_assignment || entity.coverage_state === 'ambiguous') {
        return
      }
      const currentRows = lookup.get(entity.current_assignment.taxonomy_node_id) ?? []
      currentRows.push(entity)
      lookup.set(entity.current_assignment.taxonomy_node_id, currentRows)
    })
    lookup.forEach((entities) => {
      entities.sort((left, right) => left.label.localeCompare(right.label))
    })
    return lookup
  }, [currentEntities])

  const showSystemDerivativeNode = selectedTaxonomy !== null
  const showSystemCashNode = selectedTaxonomy !== null
  const derivativeAggregate = useMemo(() => {
    const currentValueBase = !holdingsWorkspace ? null : derivativeHoldingRows.length
      ? completeAmountSum(derivativeHoldingRows.map((row) => row.market_value_base))
      : 0
    return {
      current_entity_count: derivativeHoldingRows.length,
      current_weight:
        currentValueBase != null && portfolioNav != null && portfolioNav > 1e-9
          ? currentValueBase / portfolioNav
          : null,
      current_value_base: currentValueBase,
      direct_assignment_count: 0,
    } satisfies NodeAggregate
  }, [holdingsWorkspace, derivativeHoldingRows, portfolioNav])
  const cashBucketEntities = useMemo(
    () =>
      showSystemCashNode
        ? visibleCashAccounts.map((accountRow) => {
            const monetaryBalanceBase = accountMonetaryBalanceBase(accountRow)
            return {
              entity_id: accountRow.account.account_id,
              entity_kind: 'cash_bucket' as const,
              label: accountRow.account.account_name,
              allocation:
                monetaryBalanceBase != null &&
                portfolioNav != null &&
                portfolioNav > 1e-9
                  ? monetaryBalanceBase / portfolioNav
                  : null,
              market_value_base: monetaryBalanceBase,
              current_assignment: null,
              current_node: null,
              holding_state: 'held' as const,
              instrument_state: null,
              instrument_state_label: null,
              coverage_state: 'assigned' as const,
            } satisfies CoverageEntity
          })
        : [],
    [showSystemCashNode, portfolioNav, visibleCashAccounts],
  )
  const cashAggregate = useMemo(() => {
    const heldCashEntities = cashBucketEntities.filter((entity) => entity.holding_state === 'held')

    return {
      current_entity_count: cashBucketEntities.length,
      current_weight: !accountsResponse ? null : heldCashEntities.length
        ? completeAmountSum(heldCashEntities.map((entity) => entity.allocation))
        : portfolioNav != null && portfolioNav > 1e-9
          ? 0
          : null,
      current_value_base: !accountsResponse ? null : heldCashEntities.length
        ? completeAmountSum(heldCashEntities.map((entity) => entity.market_value_base))
        : 0,
      direct_assignment_count: 0,
    } satisfies NodeAggregate
  }, [accountsResponse, cashBucketEntities, portfolioNav])

  const unassignedSummary = useMemo(() => {
    const heldEntities = coverageSummary.unassignedEntities.filter(
      (entity) => entity.holding_state === 'held',
    )

    return {
      current_weight: heldEntities.length
        ? completeAmountSum(heldEntities.map((entity) => entity.allocation))
        : null,
      current_value_base: heldEntities.length
        ? completeAmountSum(heldEntities.map((entity) => entity.market_value_base))
        : null,
    }
  }, [coverageSummary.unassignedEntities])

  const nodePathByNodeId = useMemo(() => {
    const lookup = new Map<string, PortfolioTaxonomyNodeRecord[]>()

    function build(nodeId: string): PortfolioTaxonomyNodeRecord[] {
      const existing = lookup.get(nodeId)
      if (existing) {
        return existing
      }
      const node = nodeById.get(nodeId)
      if (!node) {
        return []
      }
      const path = node.parent_taxonomy_node_id ? [...build(node.parent_taxonomy_node_id), node] : [node]
      lookup.set(nodeId, path)
      return path
    }

    selectedTaxonomyNodes.forEach((node) => {
      build(node.taxonomy_node_id)
    })
    return lookup
  }, [nodeById, selectedTaxonomyNodes])

  const selectedEntityCount = selectedEntityIds.size

  const selectedTaxonomyTargetSets = useMemo(
    () => (catalog?.target_sets ?? []).filter((targetSet) => targetSet.taxonomy_id === resolvedSelectedTaxonomyId),
    [catalog?.target_sets, resolvedSelectedTaxonomyId],
  )
  const selectedTaxonomyActiveTargetSets = useMemo(
    () => selectedTaxonomyTargetSets.filter((targetSet) => targetSet.status === 'active'),
    [selectedTaxonomyTargetSets],
  )
  const selectedTaxonomyTargetSetIntegrityIssues = useMemo(
    () =>
      targetSetIntegrityIssuesForTaxonomy(
        catalog?.target_set_integrity_issues ?? [],
        resolvedSelectedTaxonomyId,
      ),
    [catalog?.target_set_integrity_issues, resolvedSelectedTaxonomyId],
  )
  const targetSetIntegrityNotice = useMemo(
    () => formatTargetSetIntegrityNotice(selectedTaxonomyTargetSetIntegrityIssues),
    [selectedTaxonomyTargetSetIntegrityIssues],
  )
  const selectedTaxonomyTargetSetLines = useMemo(
    () => (catalog?.target_set_lines ?? []).filter((line) => selectedTaxonomyTargetSets.some((targetSet) => targetSet.target_set_id === line.target_set_id)),
    [catalog?.target_set_lines, selectedTaxonomyTargetSets],
  )
  const targetSetLinesByTargetSetId = useMemo(() => {
    const lookup = new Map<string, PortfolioTargetSetLineRecord[]>()
    selectedTaxonomyTargetSetLines.forEach((line) => {
      const currentLines = lookup.get(line.target_set_id) ?? []
      currentLines.push(line)
      lookup.set(line.target_set_id, currentLines)
    })
    return lookup
  }, [selectedTaxonomyTargetSetLines])

  const selectedNodePath = useMemo(
    () => (selectedNode ? nodePathByNodeId.get(selectedNode.taxonomy_node_id) ?? [] : []),
    [nodePathByNodeId, selectedNode],
  )
  const nodeCreateAnchorNode = nodeCreateAnchorNodeId ? nodeById.get(nodeCreateAnchorNodeId) ?? null : null
  const contextMenuNode = contextMenuState?.kind === 'node' ? nodeById.get(contextMenuState.nodeId) ?? null : null
  const contextMenuEntity =
    contextMenuState?.kind === 'entity'
      ? currentEntities.find((entity) => entity.entity_id === contextMenuState.entityId) ?? null
      : null
  const contextMenuTaxonomy =
    contextMenuState?.kind === 'taxonomy'
      ? taxonomies.find((taxonomy) => taxonomy.taxonomy_id === contextMenuState.taxonomyId) ?? null
      : null
  const contextMenuStyle = contextMenuState
    ? {
        left: Math.max(8, Math.min(contextMenuState.x, window.innerWidth - 220)),
        top: Math.max(8, Math.min(contextMenuState.y, window.innerHeight - 280)),
      }
    : undefined
  const nodeCreateContextLabel =
    nodeCreateMode === 'root'
      ? 'Add Root Node'
      : nodeCreateMode === 'sibling'
        ? `Add Same-Level Node${nodeCreateAnchorNode ? ` · ${nodeCreateAnchorNode.node_name}` : ''}`
        : `Add Child Node${nodeCreateAnchorNode ? ` · ${nodeCreateAnchorNode.node_name}` : ''}`
  const editingNode = nodeEditId ? nodeById.get(nodeEditId) ?? null : null
  const scopeMembersByScopeKey = useMemo(() => {
    const lookup = new Map<string, TargetScopeMember[]>()

    const rootChildren = sortNodes(childrenByParent.get(null) ?? [])
    const rootMembers: TargetScopeMember[] = [
      ...rootChildren.map((node) => ({
          member_key: targetMemberKey('taxonomy_node', node.taxonomy_node_id),
          target_member_type: 'taxonomy_node' as const,
          target_member_id: node.taxonomy_node_id,
          taxonomy_node_id: node.taxonomy_node_id,
          node,
          entity: null,
          label: node.node_name,
        })),
    ]
    if (showSystemCashNode) {
      rootMembers.push({
        member_key: targetMemberKey('cash_bucket', CASH_TARGET_MEMBER_ID),
        target_member_type: 'cash_bucket' as const,
        target_member_id: CASH_TARGET_MEMBER_ID,
        taxonomy_node_id: null,
        node: null,
        entity: null,
        label: CASH_TARGET_LABEL,
        system_role: 'cash',
      })
    }
    if (rootMembers.length) {
      lookup.set(ROOT_TARGET_SCOPE_KEY, rootMembers)
    }

    selectedTaxonomyNodes.forEach((node) => {
      const childNodes = sortNodes(childrenByParent.get(node.taxonomy_node_id) ?? [])
      if (childNodes.length) {
        lookup.set(
          targetScopeKey(node.taxonomy_node_id),
          childNodes.map((childNode) => ({
            member_key: targetMemberKey('taxonomy_node', childNode.taxonomy_node_id),
            target_member_type: 'taxonomy_node',
            target_member_id: childNode.taxonomy_node_id,
            taxonomy_node_id: childNode.taxonomy_node_id,
            node: childNode,
            entity: null,
            label: childNode.node_name,
          })),
        )
        return
      }

      const directEntities = (directEntitiesByNodeId.get(node.taxonomy_node_id) ?? [])
        .filter((entity) => entity.instrument_state !== 'contract')
      if (!directEntities.length) {
        return
      }
      lookup.set(
        targetScopeKey(node.taxonomy_node_id),
        directEntities.map((entity) => ({
          member_key: targetMemberKey(entity.entity_kind, entity.entity_id),
          target_member_type: entity.entity_kind,
          target_member_id: entity.entity_id,
          taxonomy_node_id: null,
          node: null,
          entity,
          label: entity.label,
        })),
      )
    })

    return lookup
  }, [
    childrenByParent,
    directEntitiesByNodeId,
    selectedTaxonomyNodes,
    showSystemCashNode,
  ])
  useEffect(() => {
    if (!selectedNodePath.length) {
      return
    }
    setCollapsedNodeIds((current) => {
      let changed = false
      const next = new Set(current)
      selectedNodePath.forEach((node) => {
        if (next.delete(node.taxonomy_node_id)) {
          changed = true
        }
      })
      return changed ? next : current
    })
  }, [selectedNodePath])

  const baselineTargetDraftsByScope = useMemo(() => {
    const nextDraftsByScope: Record<string, { saa: TargetSetDraft; taa: TargetSetDraft }> = {}
    Array.from(scopeMembersByScopeKey.keys()).forEach((scopeKey) => {
      const comparatorNodeId = targetScopeNodeId(scopeKey)
      const scopeNode = comparatorNodeId ? nodeById.get(comparatorNodeId) ?? null : null
      const targetMembers = scopeMembersByScopeKey.get(scopeKey) ?? []
      const scopeTargetSets = selectedTaxonomyActiveTargetSets.filter(
        (targetSet) => (targetSet.comparator_taxonomy_node_id ?? null) === comparatorNodeId,
      )
      const saaTargetSet = scopeTargetSets.find((targetSet) => targetSet.target_set_type === 'saa') ?? null
      const taaTargetSet = scopeTargetSets.find((targetSet) => targetSet.target_set_type === 'taa') ?? null
      const scopePathLabel = scopeNode
        ? (nodePathByNodeId.get(scopeNode.taxonomy_node_id) ?? []).map((node) => node.node_name).join(' / ')
        : 'Top Level'

      nextDraftsByScope[scopeKey] = {
        saa: buildTargetSetDraft({
          targetSet: saaTargetSet,
          targetSetLines: targetSetLinesByTargetSetId.get(saaTargetSet?.target_set_id ?? '') ?? [],
          targetMembers,
          defaultName: `${selectedTaxonomy?.name ?? 'Planning'} ${scopePathLabel} SAA`,
        }),
        taa: buildTargetSetDraft({
          targetSet: taaTargetSet,
          targetSetLines: targetSetLinesByTargetSetId.get(taaTargetSet?.target_set_id ?? '') ?? [],
          targetMembers,
          defaultName: `${selectedTaxonomy?.name ?? 'Planning'} ${scopePathLabel} TAA`,
        }),
      }
    })

    return nextDraftsByScope
  }, [
    nodePathByNodeId,
    nodeById,
    scopeMembersByScopeKey,
    selectedTaxonomy,
    selectedTaxonomyActiveTargetSets,
    targetSetLinesByTargetSetId,
  ])

  useEffect(() => {
    // Selection and supplemental coverage can rebuild the baseline while the
    // user edits. Only Save/Cancel or leaving edit mode may replace that draft.
    if (targetEditMode) return
    setTargetDraftsByScope((current) =>
      targetDraftScopesEqual(current, baselineTargetDraftsByScope) ? current : baselineTargetDraftsByScope,
    )
  }, [baselineTargetDraftsByScope, targetEditMode])

  const changedTargetScopes = useMemo(() => Object.entries(targetDraftsByScope).flatMap(([scopeKey, drafts]) => {
    const members = scopeMembersByScopeKey.get(scopeKey) ?? []
    if (!members.length) return []
    const comparatorNodeId = targetScopeNodeId(scopeKey)
    const label = comparatorNodeId
      ? (nodePathByNodeId.get(comparatorNodeId) ?? []).map((node) => node.node_name).join(' / ')
      : (zh ? '根层' : 'Top Level')
    return (['saa', 'taa'] as const).flatMap((kind) => {
      const draft = drafts[kind]
      const baseline = baselineTargetDraftsByScope[scopeKey]?.[kind] ?? EMPTY_TARGET_SET_DRAFT
      if (targetSetDraftsEqual(draft, baseline)) return []
      const existingTargetSet = selectedTaxonomyActiveTargetSets.find((targetSet) =>
        (targetSet.comparator_taxonomy_node_id ?? null) === comparatorNodeId && targetSet.target_set_type === kind,
      ) ?? null
      return [{ scopeKey, comparatorNodeId, label, kind, draft, members, existingTargetSet,
        validation: validateTargetSetDraft(draft, members, comparatorNodeId ? allocationBasisDraftsByNodeId[comparatorNodeId] : rootAllocationBasisDraft, zh) }]
    })
  }), [targetDraftsByScope, scopeMembersByScopeKey, nodePathByNodeId, baselineTargetDraftsByScope,
    selectedTaxonomyActiveTargetSets, allocationBasisDraftsByNodeId, rootAllocationBasisDraft, zh])
  const changedAllocationBasisNodes = useMemo(
    () =>
      selectedTaxonomyNodes.filter((node) => {
        const draftValue = allocationBasisDraftsByNodeId[node.taxonomy_node_id]
        return Boolean(draftValue) && draftValue !== node.allocation_basis
      }),
    [allocationBasisDraftsByNodeId, selectedTaxonomyNodes],
  )
  const rootAllocationBasisChanged = rootAllocationBasisDraft !== selectedTaxonomy?.root_allocation_basis
  const hasAllocationBasisChanges = changedAllocationBasisNodes.length > 0 || rootAllocationBasisChanged
  const basisValidationErrors = [
    ...(rootAllocationBasisChanged ? [ROOT_TARGET_SCOPE_KEY] : []),
    ...changedAllocationBasisNodes.map((node) => node.taxonomy_node_id),
  ].flatMap((scopeKey) => (['saa', 'taa'] as const).flatMap((stage) => {
    const draft = targetDraftsByScope[scopeKey]?.[stage]
    return draft ? validateTargetSetDraft(draft, scopeMembersByScopeKey.get(scopeKey) ?? [], scopeKey === ROOT_TARGET_SCOPE_KEY ? rootAllocationBasisDraft : allocationBasisDraftsByNodeId[scopeKey], zh).errors : []
  }))
  const targetSaveBlockedReason = [...new Set([...changedTargetScopes.flatMap((scope) => scope.validation.errors.map(
    (message) => `${zh ? (scope.kind === 'saa' ? '战略' : '战术') : scope.kind.toUpperCase()} ${scope.label}: ${message}`,
  )), ...basisValidationErrors])].join(' ')
  const hasTargetChanges = hasAllocationBasisChanges || changedTargetScopes.length > 0
  const hasTargetsConfigurationChanges = hasTargetChanges || hasConcentrationChanges
  const configurationConflict = targetEditMode && Boolean(actionError?.match(/(?:Taxonomy configuration|Concentration settings) changed/i))
  const canSaveTargetsConfiguration = canEditPortfolio && editingConfigurationVersion != null && hasTargetsConfigurationChanges && !targetSaveBlockedReason && !concentrationValidationError && !configurationConflict

  function preventTargetEditorDrag(event: ReactDragEvent<HTMLInputElement | HTMLSelectElement>) {
    event.preventDefault()
    event.stopPropagation()
  }

  const resolvedTargets = catalog?.target_resolution?.find((item) => item.taxonomy_id === resolvedSelectedTaxonomyId)

  function memberScopeKey(member: TargetScopeMember) {
    return member.node ? targetScopeKey(member.node.parent_taxonomy_node_id ?? null) : targetScopeKey(member.entity?.current_assignment?.taxonomy_node_id ?? null)
  }

  function renderTargetCell(kind: 'saa' | 'taa', member: TargetScopeMember, editable: boolean) {
    const scopeKey = memberScopeKey(member)
    const draft = targetDraftsByScope[scopeKey]?.[kind]
    const value = draft?.lines_by_member_key[member.member_key]?.target_value ?? ''
    const scopeMembers = scopeMembersByScopeKey.get(scopeKey) ?? []
    const singleMember = scopeMembers.filter((item) => item.system_role !== 'cash').length === 1 && member.system_role !== 'cash'
    const stageBlank = !Object.values(draft?.lines_by_member_key ?? {}).some((line) => line.target_value.trim())
    if (canEditPortfolio && editable) return <input type="number" min="0" max="100" step="any" value={value} disabled={Boolean(actionPending)}
      placeholder={kind === 'saa' && singleMember && stageBlank ? '100' : '—'}
      aria-label={`${kind.toUpperCase()} target for ${member.label}`} draggable={false}
      onDragStart={preventTargetEditorDrag}
      onChange={(event) => updateDraftLine(scopeKey, kind, member.member_key, 'target_value', event.target.value)} />
    const resolved = resolvedTargets?.member_targets.find((item) => item.scope_node_id === targetScopeNodeId(scopeKey) && item.member_type === member.target_member_type && item.member_id === member.target_member_id)
    const resolvedValue = kind === 'saa' ? resolved?.strategic_value : resolved?.tactical_value
    return resolvedValue != null ? formatPercent(resolvedValue) : '—'
  }

  function renderGlobalRiskTarget(member: TargetScopeMember) {
    if (member.system_role) return '—'
    if (targetEditMode && hasTargetChanges) return <span aria-label={zh ? '全组合风险目标保存后更新' : 'Portfolio risk target updates after save'}>—</span>
    const resolved = resolvedTargets?.member_targets.find((item) => item.scope_node_id === targetScopeNodeId(memberScopeKey(member)) && item.member_type === member.target_member_type && item.member_id === member.target_member_id)
    return resolved?.tactical_global_risk_target != null ? formatPercent(resolved.tactical_global_risk_target) : '—'
  }

  function renderAllocationBasis(node: PortfolioTaxonomyNodeRecord | null) {
    const scopeKey = targetScopeKey(node?.taxonomy_node_id ?? null)
    const hasMembers = (scopeMembersByScopeKey.get(scopeKey) ?? []).some((member) => member.system_role !== 'cash')
    if (!hasMembers) return '—'
    const value = node ? allocationBasisDraftsByNodeId[node.taxonomy_node_id] ?? node.allocation_basis : rootAllocationBasisDraft
    if (!targetEditMode) return <span className="taxonomy-basis-value">{value === 'weight' ? (zh ? '权重' : 'Weight') : (zh ? '风险预算' : 'Risk budget')}</span>
    return <select aria-label={`Allocation basis for ${node?.node_name ?? selectedTaxonomy?.name}`} value={value} disabled={!canEditPortfolio || Boolean(actionPending)}
      onChange={(event) => { const next = event.target.value as AllocationBasis
        if (node) setAllocationBasisDraftsByNodeId((current) => ({ ...current, [node.taxonomy_node_id]: next }))
        else setRootAllocationBasisDraft(next)
      }}><option value="weight">{zh ? '权重' : 'Weight'}</option><option value="risk_budget">{zh ? '风险预算' : 'Risk budget'}</option></select>
  }

  function resetNodeCreateDraft() {
    setNewNodeName('')
  }

  function startNodeCreate(mode: NodeCreateMode, anchorNode?: PortfolioTaxonomyNodeRecord | null) {
    resetNodeCreateDraft()
    setShowTaxonomyCreate(false)
    setShowInstrumentAdd(false)
    setShowNodeCreate(true)
    setShowNodeEdit(false)
    setNodeCreateMode(mode)
    setContextMenuState(null)

    if (mode === 'root' || !anchorNode) {
      setNodeCreateParentId('')
      setNodeCreateAnchorNodeId(null)
      return
    }

    setSelectedNodeId(anchorNode.taxonomy_node_id)
    setNodeCreateAnchorNodeId(anchorNode.taxonomy_node_id)
    setNodeCreateParentId(mode === 'child' ? anchorNode.taxonomy_node_id : anchorNode.parent_taxonomy_node_id ?? '')
  }

  function startNodeEdit(node: PortfolioTaxonomyNodeRecord) {
    setContextMenuState(null)
    setShowTaxonomyCreate(false)
    setShowInstrumentAdd(false)
    setShowNodeCreate(false)
    setShowNodeEdit(true)
    setSelectedNodeId(node.taxonomy_node_id)
    setNodeEditId(node.taxonomy_node_id)
    setNodeEditName(node.node_name)
  }

  function startTaxonomyRename(taxonomy: PortfolioTaxonomyRecord) {
    setContextMenuState(null)
    setTaxonomyPickerOpen(false)
    setShowTaxonomyCreate(false)
    setShowInstrumentAdd(false)
    setShowNodeCreate(false)
    setShowNodeEdit(false)
    setShowTaxonomyRename(true)
    setTaxonomyRenameId(taxonomy.taxonomy_id)
    setTaxonomyRenameName(taxonomy.name)
  }

  function closeModalStack() {
    setShowTaxonomyCreate(false)
    setShowTaxonomyRename(false)
    setShowInstrumentAdd(false)
    setShowNodeCreate(false)
    setShowNodeEdit(false)
    setTaxonomyPickerOpen(false)
  }

  function handleTaxonomySelection(taxonomyId: string) {
    updateSearchParam('taxonomy_id', taxonomyId)
    setCollapsedNodeIds(new Set())
    setSelectedEntityIds(new Set())
    setTargetEditMode(false)
    closeModalStack()
    setContextMenuState(null)
  }

  function toggleNodeCollapse(nodeId: string) {
    setCollapsedNodeIds((current) => {
      const next = new Set(current)
      if (next.has(nodeId)) {
        next.delete(nodeId)
      } else {
        next.add(nodeId)
      }
      return next
    })
  }

  function revealTargetScope(scopeKey: string) {
    setSelectedNodeId(targetScopeNodeId(scopeKey))
    setCollapsedNodeIds((current) => {
      const next = new Set(current)
      next.delete(TAXONOMY_ROOT_ROW_ID)
      for (const node of nodePathByNodeId.get(scopeKey) ?? []) {
        next.delete(node.taxonomy_node_id)
      }
      return next
    })
  }

  function handleTaxonomyScopeKeydown(event: ReactKeyboardEvent<HTMLElement>) {
    const target = event.target as HTMLElement | null
    const tagName = target?.tagName?.toLowerCase()
    if (
      target?.isContentEditable ||
      tagName === 'input' ||
      tagName === 'textarea' ||
      tagName === 'select'
    ) {
      return
    }
    if (
      event.key === 'Enter' &&
      (event.metaKey || event.ctrlKey) &&
      !targetEditMode &&
      selectedNode?.is_terminal &&
      selectedEntityIds.size
    ) {
      event.preventDefault()
      void assignEntitiesToNode(selectedNode, Array.from(selectedEntityIds))
    }
  }

  function handleNodeContextMenu(event: ReactMouseEvent, node: PortfolioTaxonomyNodeRecord) {
    event.preventDefault()
    if (actionPending || targetEditMode || !canEditPortfolio) return
    setSelectedNodeId(node.taxonomy_node_id)
    setContextMenuState({
      kind: 'node',
      nodeId: node.taxonomy_node_id,
      x: event.clientX,
      y: event.clientY,
    })
  }

  function handleEntityContextMenu(event: ReactMouseEvent, entity: CoverageEntity) {
    event.preventDefault()
    if (actionPending || targetEditMode || !canEditPortfolio) return
    if (isSystemCashEntity(entity)) {
      return
    }
    if (!selectedEntityIds.has(entity.entity_id)) {
      setSelectedEntityIds(new Set([entity.entity_id]))
    }
    setContextMenuState({
      kind: 'entity',
      entityId: entity.entity_id,
      x: event.clientX,
      y: event.clientY,
    })
  }

  function toggleEntitySelection(entityId: string) {
    const entity = currentEntities.find((candidate) => candidate.entity_id === entityId)
    if (entity && isSystemCashEntity(entity)) {
      return
    }
    setSelectedEntityIds((current) => {
      const next = new Set(current)
      if (next.has(entityId)) {
        next.delete(entityId)
      } else {
        next.add(entityId)
      }
      return next
    })
  }

  function handleEntityDragStart(event: ReactDragEvent, entity: CoverageEntity) {
    const dragEnabled = canDragTaxonomyEntity({
      targetEditMode,
      lockedEntity: isSystemCashEntity(entity),
      ambiguousEntity: entity.coverage_state === 'ambiguous',
      actionPending: Boolean(actionPending),
    })
    if (!dragEnabled) {
      event.preventDefault()
      return
    }
    const dragEntityIds = selectedEntityIds.has(entity.entity_id) ? Array.from(selectedEntityIds) : [entity.entity_id]
    if (!selectedEntityIds.has(entity.entity_id)) {
      setSelectedEntityIds(new Set([entity.entity_id]))
    }
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', dragEntityIds.join(','))
  }

  function handleNodeDragOver(event: ReactDragEvent, node: PortfolioTaxonomyNodeRecord) {
    if (
      !canDropTaxonomyEntity({
        targetEditMode,
        terminalNode: node.is_terminal,
        actionPending: Boolean(actionPending),
      })
    ) {
      return
    }
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setDragTargetNodeId(node.taxonomy_node_id)
  }

  async function handleNodeDrop(event: ReactDragEvent, node: PortfolioTaxonomyNodeRecord) {
    if (
      !canDropTaxonomyEntity({
        targetEditMode,
        terminalNode: node.is_terminal,
        actionPending: Boolean(actionPending),
      })
    ) {
      setDragTargetNodeId(null)
      return
    }
    event.preventDefault()
    setDragTargetNodeId(null)
    const raw = event.dataTransfer.getData('text/plain').trim()
    const entityIds = raw
      ? raw
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean)
      : []
    await assignEntitiesToNode(node, entityIds)
  }

  function updateDraftLine(
    scopeKey: string,
    kind: 'saa' | 'taa',
    targetMemberKeyValue: string,
    field: keyof TargetLineDraft,
    value: string,
  ) {
    setTargetDraftsByScope((current) => {
      const scopeDrafts = current[scopeKey] ?? { saa: EMPTY_TARGET_SET_DRAFT, taa: EMPTY_TARGET_SET_DRAFT }
      const modeDraft = scopeDrafts[kind]
      return {
        ...current,
        [scopeKey]: {
          ...scopeDrafts,
          [kind]: {
            ...modeDraft,
            lines_by_member_key: {
              ...modeDraft.lines_by_member_key,
              [targetMemberKeyValue]: {
                ...(modeDraft.lines_by_member_key[targetMemberKeyValue] ?? {
                  target_value: '',
                  notes: '',
                }),
                [field]: value,
              },
            },
          },
        },
      }
    })
  }

  function buildTargetSetPayload(draft: TargetSetDraft, members: TargetScopeMember[]) {
    const populated = members.filter((member) => draft.lines_by_member_key[member.member_key]?.target_value.trim())
    return {
      name: draft.name.trim(), status: draft.status, notes: draft.notes || null,
      lines: populated.map((member) => ({ target_member_type: member.target_member_type, target_member_id: member.target_member_id,
        taxonomy_node_id: member.taxonomy_node_id, target_value: parsePercentInput(draft.lines_by_member_key[member.member_key].target_value),
        notes: draft.lines_by_member_key[member.member_key].notes || null })),
    }
  }

  async function handleSaveTargetsConfiguration() {
    if (!canEditPortfolio) return
    if (!portfolioId || !selectedTaxonomy) {
      return
    }

    if (actionPending || editingConfigurationVersion == null || configurationConflict || targetSaveBlockedReason || concentrationValidationError) return
    if (!hasTargetsConfigurationChanges) {
      setActionError(null)
      setNotice('No changes.')
      return
    }

    const actionView = workspaceIdentityRef.current
    setActionPending('targets-save')
    setActionError(null)
    setNotice(null)
    try {
      await savePortfolioTaxonomyTargetConfiguration(portfolioId, selectedTaxonomy.taxonomy_id, {
        expected_configuration_version: editingConfigurationVersion,
        ...(rootAllocationBasisChanged ? { root_allocation_basis: rootAllocationBasisDraft } : {}),
        node_allocation_bases: Object.fromEntries(changedAllocationBasisNodes.map((node) => [
          node.taxonomy_node_id, allocationBasisDraftsByNodeId[node.taxonomy_node_id],
        ])),
        ...(hasConcentrationChanges && concentrationSettings ? { concentration: buildConcentrationPayload() } : {}),
        target_sets: changedTargetScopes.map(({ kind, draft, members, comparatorNodeId, existingTargetSet }) => ({
          ...buildTargetSetPayload(draft, members),
          target_set_id: existingTargetSet?.target_set_id ?? null,
          comparator_taxonomy_node_id: comparatorNodeId,
          target_set_type: kind,
        })),
      })

      if (!isCurrentView(actionView)) return
      const savedParts = [
        rootAllocationBasisChanged ? (zh ? '根层配置依据' : 'root allocation basis') : '',
        changedAllocationBasisNodes.length ? (zh ? `配置依据（${changedAllocationBasisNodes.length} 层）` : `allocation bases (${changedAllocationBasisNodes.length})`) : '',
        ...changedTargetScopes.map(({ kind, label }) => `${zh ? (kind === 'saa' ? '战略' : '战术') : kind.toUpperCase()} ${label}`),
        hasConcentrationChanges ? (zh ? '集中度限额' : 'concentration limits') : '',
      ].filter(Boolean)
      setTargetEditMode(false)
      setConcentrationRevision((current) => current + 1)
      setNotice(zh ? `已保存${savedParts.join('、')}。` : `Saved ${savedParts.join(', ')}.`)
      await reloadWorkspace()
    } catch (error) {
      if (!isCurrentView(actionView)) return
      setActionError(extractErrorMessage(error))
    } finally {
      if (isCurrentView(actionView)) setActionPending(null)
    }
  }

  async function handleCreateTaxonomy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canEditPortfolio) return
    if (!portfolioId) {
      return
    }
    const actionView = workspaceIdentityRef.current
    setActionPending('taxonomy-create')
    setActionError(null)
    setNotice(null)
    try {
      const created = await createPortfolioTaxonomy(portfolioId, {
        name: taxonomyName,
        taxonomy_type: 'custom',
        purpose: null,
        root_allocation_basis: 'weight',
      })
      if (!isCurrentView(actionView)) return
      setTaxonomyName('')
      setShowTaxonomyCreate(false)
      handleTaxonomySelection(created.taxonomy_id)
      setNotice(`Created taxonomy "${created.name}".`)
      await reloadWorkspace()
    } catch (error) {
      if (!isCurrentView(actionView)) return
      setActionError(extractErrorMessage(error))
    } finally {
      if (isCurrentView(actionView)) setActionPending(null)
    }
  }

  async function handleRenameTaxonomy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canEditPortfolio) return
    const taxonomy = taxonomyRenameId ? taxonomies.find((item) => item.taxonomy_id === taxonomyRenameId) ?? null : null
    const nextName = taxonomyRenameName.trim()
    if (!portfolioId || !taxonomy || !nextName) {
      return
    }
    const actionView = workspaceIdentityRef.current
    setActionPending(`taxonomy-rename-${taxonomy.taxonomy_id}`)
    setActionError(null)
    setNotice(null)
    try {
      await updatePortfolioTaxonomy(portfolioId, taxonomy.taxonomy_id, {
        name: nextName,
      })
      if (!isCurrentView(actionView)) return
      setShowTaxonomyRename(false)
      setTaxonomyRenameId(null)
      setTaxonomyRenameName('')
      setNotice(`Renamed taxonomy to "${nextName}".`)
      await reloadWorkspace()
    } catch (error) {
      if (!isCurrentView(actionView)) return
      setActionError(extractErrorMessage(error))
    } finally {
      if (isCurrentView(actionView)) setActionPending(null)
    }
  }

  function handleDeleteTaxonomy(taxonomy: PortfolioTaxonomyRecord) {
    if (!portfolioId) {
      return
    }
    setActionError(null)
    setNotice(null)
    setContextMenuState(null)
    setPendingDelete({
      kind: 'taxonomy',
      portfolioId,
      taxonomy,
      wasSelected: resolvedSelectedTaxonomyId === taxonomy.taxonomy_id,
    })
  }

  async function handleCreateNode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canEditPortfolio) return
    if (!portfolioId || !selectedTaxonomy) {
      return
    }
    const actionView = workspaceIdentityRef.current
    setActionPending('node-create')
    setActionError(null)
    setNotice(null)
    try {
      const created = await createPortfolioTaxonomyNode(portfolioId, selectedTaxonomy.taxonomy_id, {
        node_name: newNodeName,
        parent_taxonomy_node_id: nodeCreateParentId || null,
      })
      if (!isCurrentView(actionView)) return
      resetNodeCreateDraft()
      setCollapsedNodeIds((current) => {
        const next = new Set(current)
        if (nodeCreateParentId) {
          next.delete(nodeCreateParentId)
        }
        return next
      })
      setShowNodeCreate(false)
      setNodeCreateAnchorNodeId(created.taxonomy_node_id)
      setSelectedNodeId(created.taxonomy_node_id)
      setNotice(`Added node "${created.node_name}".`)
      await reloadWorkspace()
    } catch (error) {
      if (!isCurrentView(actionView)) return
      setActionError(extractErrorMessage(error))
    } finally {
      if (isCurrentView(actionView)) setActionPending(null)
    }
  }

  async function handleSaveNodeEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canEditPortfolio) return
    if (!portfolioId || !selectedTaxonomy || !editingNode) {
      return
    }
    const actionView = workspaceIdentityRef.current
    setActionPending(`node-save-${editingNode.taxonomy_node_id}`)
    setActionError(null)
    setNotice(null)
    try {
      await updatePortfolioTaxonomyNode(portfolioId, selectedTaxonomy.taxonomy_id, editingNode.taxonomy_node_id, {
        node_name: nodeEditName,
      })
      if (!isCurrentView(actionView)) return
      setShowNodeEdit(false)
      setNotice(`Updated node "${nodeEditName}".`)
      await reloadWorkspace()
    } catch (error) {
      if (!isCurrentView(actionView)) return
      setActionError(extractErrorMessage(error))
    } finally {
      if (isCurrentView(actionView)) setActionPending(null)
    }
  }

  function handleDeleteNode(node: PortfolioTaxonomyNodeRecord) {
    if (!portfolioId || !selectedTaxonomy) {
      return
    }
    setActionError(null)
    setNotice(null)
    setContextMenuState(null)
    setPendingDelete({
      kind: 'node',
      portfolioId,
      taxonomyId: selectedTaxonomy.taxonomy_id,
      node,
    })
  }

  async function assignEntitiesToNode(targetNode: PortfolioTaxonomyNodeRecord, entityIds: string[]) {
    if (!canEditPortfolio || actionPending) return
    if (targetEditMode) {
      setContextMenuState(null)
      setActionError(TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE)
      return
    }
    if (!portfolioId || !selectedTaxonomy || !targetNode.is_terminal || !entityIds.length) {
      return
    }
    const actionView = workspaceIdentityRef.current
    setActionPending('assignment-bulk-save')
    setActionError(null)
    setNotice(null)
    let savedCount = 0
    try {
      let createdCount = 0
      let movedCount = 0
      let unchangedCount = 0
      let skippedAmbiguous = 0

      for (const entityId of entityIds) {
        const entity = currentEntities.find((candidate) => candidate.entity_id === entityId)
        if (!entity) {
          continue
        }
        if (isSystemCashEntity(entity)) {
          unchangedCount += 1
          continue
        }
        if (entity.coverage_state === 'ambiguous') {
          skippedAmbiguous += 1
          continue
        }
        if (!entity.current_assignment) {
          await createPortfolioTaxonomyAssignment(portfolioId, selectedTaxonomy.taxonomy_id, {
            target_scope: 'instrument',
            target_entity_id: entity.entity_id,
            taxonomy_node_id: targetNode.taxonomy_node_id,
          })
          createdCount += 1
          savedCount += 1
          continue
        }
        if (entity.current_assignment.taxonomy_node_id === targetNode.taxonomy_node_id) {
          unchangedCount += 1
          continue
        }
        await updatePortfolioTaxonomyAssignment(
          portfolioId,
          selectedTaxonomy.taxonomy_id,
          entity.current_assignment.assignment_id,
          {
            taxonomy_node_id: targetNode.taxonomy_node_id,
          },
        )
        movedCount += 1
        savedCount += 1
      }

      if (!isCurrentView(actionView)) return
      setSelectedEntityIds(new Set())
      setNotice(
        `Assignments updated for "${targetNode.node_name}": ${createdCount} created, ${movedCount} moved, ${unchangedCount} unchanged${
          skippedAmbiguous ? `, ${skippedAmbiguous} ambiguous skipped` : ''
        }.`,
      )
      await reloadWorkspace()
    } catch (error) {
      if (!isCurrentView(actionView)) return
      setActionError(savedCount
        ? `${zh ? `已有 ${savedCount} 项归属保存成功，其余更新未完成：` : `${savedCount} assignment(s) were saved before the remaining update failed: `}${extractErrorMessage(error)}`
        : extractErrorMessage(error))
      if (savedCount) {
        setSelectedEntityIds(new Set())
        await reloadWorkspace()
      }
    } finally {
      if (isCurrentView(actionView)) setActionPending(null)
    }
  }

  const instrumentAddExistingAssignment = selectedTaxonomyAssignments.find((item) => item.target_scope === 'instrument' && item.target_entity_id === instrumentAddInstrumentId && item.status === 'active')

  function startInstrumentAdd(node: PortfolioTaxonomyNodeRecord | null) {
    closeModalStack()
    setContextMenuState(null)
    setInstrumentAddInstrumentId('')
    setInstrumentAddNodeId(node?.is_terminal ? node.taxonomy_node_id : '')
    setShowInstrumentAdd(true)
  }

  const concentrationSettingsLoading = Boolean(holdingsWorkspace?.as_of_date && !concentrationSettings && !concentrationSettingsError)

  function beginConfigurationEdit() {
    if (!canEditPortfolio || actionPending || !selectedTaxonomy || !catalog || concentrationSettingsLoading) return
    closeModalStack()
    setContextMenuState(null)
    setTargetDraftsByScope(baselineTargetDraftsByScope)
    setAllocationBasisDraftsByNodeId(allocationBasisDraftsFromNodes(selectedTaxonomyNodes))
    setRootAllocationBasisDraft(selectedTaxonomy.root_allocation_basis)
    setLimitDrafts(concentrationDrafts(concentrationSettings))
    setTaxonomyConcentrationEnabled(concentrationSettings?.enabled_taxonomy_ids.includes(selectedTaxonomy.taxonomy_id) ?? false)
    setDragTargetNodeId(null)
    setEditingConfigurationVersion(catalog.taxonomy_configuration_version)
    setTargetEditMode(true)
  }

  function cancelConfigurationEdit() {
    if (!selectedTaxonomy) return
    setTargetDraftsByScope(baselineTargetDraftsByScope)
    setAllocationBasisDraftsByNodeId(allocationBasisDraftsFromNodes(selectedTaxonomyNodes))
    setRootAllocationBasisDraft(selectedTaxonomy.root_allocation_basis)
    setLimitDrafts(concentrationDrafts(concentrationSettings))
    setTaxonomyConcentrationEnabled(concentrationSettings?.enabled_taxonomy_ids.includes(selectedTaxonomy.taxonomy_id) ?? false)
    setActionError(null)
    setEditingConfigurationVersion(null)
    setTargetEditMode(false)
  }

  async function handleAddRegistryInstrumentToUniverse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canEditPortfolio || !portfolioId || !selectedTaxonomy || targetEditMode) return
    const instrument = instrumentById.get(instrumentAddInstrumentId)
    if (!instrument) { setActionError('Choose an instrument.'); return }
    const node = instrumentAddNodeId ? nodeById.get(instrumentAddNodeId) : null
    if (instrumentAddNodeId && !node?.is_terminal) { setActionError('Choose a leaf category.'); return }
    if (instrumentAddExistingAssignment && !node) { setActionError('This instrument already belongs to a category. Choose its destination to move it.'); return }
    const actionView = workspaceIdentityRef.current
    setActionPending('instrument-add')
    setActionError(null)
    setNotice(null)
    let observationSaved = false
    try {
      const existingEntity = currentEntities.find((item) => item.entity_id === instrument.instrument_id)
      const existingUniverse = universeRecordByInstrumentId.get(instrument.instrument_id)
      const directlySelected = existingEntity?.instrument_state === 'held' || existingEntity?.instrument_state === 'former'
        || (existingUniverse?.source === 'manual' && existingUniverse.status === 'active')
      if (!directlySelected) {
        await createPortfolioInstrumentUniverseRecord(portfolioId, { instrument_id: instrument.instrument_id })
        observationSaved = true
      }
      if (node) {
        if (instrumentAddExistingAssignment && instrumentAddExistingAssignment.taxonomy_node_id !== node.taxonomy_node_id) {
          await updatePortfolioTaxonomyAssignment(portfolioId, selectedTaxonomy.taxonomy_id, instrumentAddExistingAssignment.assignment_id, { taxonomy_node_id: node.taxonomy_node_id })
        } else if (!instrumentAddExistingAssignment) {
          await createPortfolioTaxonomyAssignment(portfolioId, selectedTaxonomy.taxonomy_id, { target_scope: 'instrument', target_entity_id: instrument.instrument_id, taxonomy_node_id: node.taxonomy_node_id })
        }
      }
      if (!isCurrentView(actionView)) return
      setInstrumentAddInstrumentId('')
      setShowInstrumentAdd(false)
      setNotice(`${instrument.instrument_name} → ${node?.node_name ?? 'Unassigned'}`)
      await reloadWorkspace()
    } catch (error) {
      if (!isCurrentView(actionView)) return
      setActionError(observationSaved && node
        ? `${zh ? '标的已加入观察范围，但分类未保存，可重试：' : 'Instrument added to the observation universe, but its classification was not saved. Retry: '}${extractErrorMessage(error)}`
        : extractErrorMessage(error))
      if (observationSaved) await reloadWorkspace()
    }
    finally { if (isCurrentView(actionView)) setActionPending(null) }
  }

  function handleDeleteObservedInstrument(entity: CoverageEntity) {
    if (!portfolioId || entity.entity_kind !== 'instrument' || entity.instrument_state !== 'observe') {
      return
    }
    if (entity.current_assignment) {
      setActionError('Remove taxonomy assignment before removing this observed instrument.')
      setNotice(null)
      return
    }
    setActionError(null)
    setNotice(null)
    setContextMenuState(null)
    setPendingDelete({ kind: 'observed-instrument', portfolioId, entity })
  }

  async function handleConfirmedDelete() {
    if (!canEditPortfolio) return
    const target = pendingDelete
    if (!target || actionPending) {
      return
    }

    const actionKey =
      target.kind === 'taxonomy'
        ? `taxonomy-delete-${target.taxonomy.taxonomy_id}`
        : target.kind === 'node'
          ? `node-delete-${target.node.taxonomy_node_id}`
          : `instrument-observe-delete-${target.entity.entity_id}`
    const actionView = workspaceIdentityRef.current
    setActionPending(actionKey)
    setActionError(null)
    setNotice(null)

    try {
      let successNotice = ''
      if (target.kind === 'taxonomy') {
        await deletePortfolioTaxonomy(
          target.portfolioId,
          target.taxonomy.taxonomy_id,
                )
        if (!isCurrentView(actionView)) return
        if (target.wasSelected) {
          updateSearchParam('taxonomy_id', null)
        }
        setTaxonomyPickerOpen(false)
        successNotice = `Deleted taxonomy "${target.taxonomy.name}".`
      } else if (target.kind === 'node') {
        await deletePortfolioTaxonomyNode(
          target.portfolioId,
          target.taxonomyId,
          target.node.taxonomy_node_id,
                )
        if (isCurrentView(actionView)) {
          setSelectedNodeId(target.node.parent_taxonomy_node_id ?? null)
        }
        successNotice = `Deleted node "${target.node.node_name}".`
      } else {
        await deletePortfolioInstrumentUniverseRecord(target.portfolioId, target.entity.entity_id)
        if (isCurrentView(actionView)) {
          setSelectedEntityIds((current) => {
            const next = new Set(current)
            next.delete(target.entity.entity_id)
            return next
          })
        }
        successNotice = `Removed observed instrument "${target.entity.label}".`
      }

      if (isCurrentView(actionView)) {
        setPendingDelete(null)
        setNotice(successNotice)
        await reloadWorkspace()
      }
    } catch (error) {
      if (isCurrentView(actionView)) {
        setActionError(extractErrorMessage(error))
      }
    } finally {
      if (isCurrentView(actionView)) {
        setActionPending(null)
      }
    }
  }

  function concentrationRow(scope: ConcentrationScopeKind, entityId: string, taxonomyId?: string) {
    return concentration?.scopes.find((item) => item.scope === scope && (scope !== 'taxonomy' || item.taxonomy_id === taxonomyId))?.rows.find((item) => item.entity_id === entityId)
  }

  function renderExposure(scope: ConcentrationScopeKind, entityId: string, taxonomyId?: string) {
    const row = concentrationRow(scope, entityId, taxonomyId)
    const weight = row?.weight ?? row?.lower_bound_weight
    return <span title={row?.coverage.join(' · ')}>{weight != null ? `${row?.weight == null ? '≥ ' : ''}${formatPercent(weight)}` : '—'}</span>
  }

  function renderConcentrationLimit(scope: ConcentrationScopeKind, entityId: string, name: string, taxonomyId?: string) {
    const row = concentrationRow(scope, entityId, taxonomyId)
    const key = concentrationLimitKey(scope, entityId, taxonomyId)
    if (targetEditMode && canEditPortfolio) return <input type="number" min="0" step="any"
      aria-label={`Concentration limit for ${name}`} value={limitDrafts[key] ?? ''} placeholder="—"
      disabled={!concentrationSettings || Boolean(actionPending)}
      onChange={(event) => setLimitDrafts((current) => ({ ...current, [key]: event.target.value }))} />
    const enabled = scope !== 'taxonomy' || taxonomyConcentrationEnabled
    const status = enabled ? row?.status : 'unconfigured'
    const title = !enabled ? (zh ? '本分类提醒已关闭' : 'Reminders for this taxonomy are off')
      : status === 'breached' ? (zh ? '已超过集中度上限' : 'Concentration limit exceeded')
      : status === 'unavailable' ? (zh ? '敞口数据不足，无法判断' : 'Insufficient exposure data to assess the limit')
      : (zh ? '集中度提醒上限，不是优化器硬约束' : 'A concentration reminder, not a hard optimizer constraint')
    return <span title={title} className={`concentration-status concentration-status-${status ?? 'unconfigured'}`}>{row?.limit_weight != null ? formatPercent(row.limit_weight) : '—'}{status === 'breached' ? ' !' : ''}</span>
  }

  function renderEntityTreeRow(entity: CoverageEntity, depth: number) {
    const lockedCashEntity = isSystemCashEntity(entity)
    const assignmentDragEnabled = canEditPortfolio && canDragTaxonomyEntity({ targetEditMode, lockedEntity: lockedCashEntity,
      ambiguousEntity: entity.coverage_state === 'ambiguous', actionPending: Boolean(actionPending) })
    const selected = !lockedCashEntity && selectedEntityIds.has(entity.entity_id)
    const member: TargetScopeMember = { member_key: targetMemberKey(entity.entity_kind, entity.entity_id),
      target_member_type: entity.entity_kind, target_member_id: entity.entity_id, taxonomy_node_id: null, node: null, entity, label: entity.label }
    const editable = targetEditMode && Boolean(entity.current_assignment) && entity.instrument_state !== 'contract'
      && Boolean(targetDraftsByScope[memberScopeKey(member)])
    return <tr key={entity.entity_id}
      className={['portfolio-tree-row', 'taxonomy-entity-row', assignmentDragEnabled ? 'taxonomy-entity-row-draggable' : 'taxonomy-entity-row-drag-locked', selected ? 'taxonomy-entity-row-active' : ''].filter(Boolean).join(' ')} data-tree-level="item"
      draggable={assignmentDragEnabled} data-assignment-drag={assignmentDragEnabled ? 'enabled' : 'disabled'}
      onDragStart={assignmentDragEnabled ? (event) => handleEntityDragStart(event, entity) : undefined}
      onDragEnd={assignmentDragEnabled ? () => setDragTargetNodeId(null) : undefined}
      onContextMenu={canEditPortfolio && !lockedCashEntity && !targetEditMode ? (event) => handleEntityContextMenu(event, entity) : undefined}>
      <td className="holding-name-cell"><div className="taxonomy-node-row" style={{ paddingLeft: `${depth * 18}px` }}>
        <span className="taxonomy-tree-toggle taxonomy-tree-toggle-empty" />
        {lockedCashEntity ? <span className="taxonomy-tree-check-slot" /> : <label className="taxonomy-tree-check-slot" aria-label={`Select ${entity.label}`}>
          <input type="checkbox" checked={selected} disabled={targetEditMode} onChange={() => toggleEntitySelection(entity.entity_id)} /></label>}
        <span className="taxonomy-level-label portfolio-tree-label" data-tree-level="item" translate="no" title={entity.label}>{entity.label}</span>{renderInstrumentStatusCell(entity)}
        {entity.supporting_label ? <span className="taxonomy-entity-supporting-label">{entity.supporting_label}</span> : null}
      </div></td>
      <td>—</td>
      <td>{formatReportAmount(entity.market_value_base)}</td>
      <td>{lockedCashEntity ? '—' : renderExposure('security', entity.entity_id)}</td>
      <td>{lockedCashEntity ? '—' : renderTargetCell('saa', member, editable)}</td>
      <td>{lockedCashEntity ? '—' : renderTargetCell('taa', member, editable)}</td>
      <td>{lockedCashEntity ? '—' : renderGlobalRiskTarget(member)}</td>
      <td>{lockedCashEntity ? '—' : renderConcentrationLimit('security', entity.entity_id, entity.label)}</td>
    </tr>
  }

  function renderDerivativeTreeRows(depth: number): ReactElement[] {
    if (!showSystemDerivativeNode) return []
    const collapsed = collapsedNodeIds.has(TAXONOMY_DERIVATIVES_ROW_ID)
    const rows: ReactElement[] = [<tr key={TAXONOMY_DERIVATIVES_ROW_ID} className="portfolio-tree-row taxonomy-system-derivatives-row taxonomy-category-row" data-tree-level="primary">
      <td><div className="taxonomy-node-row" style={{ paddingLeft: `${depth * 18}px` }}>
        <button type="button" className="taxonomy-tree-toggle" aria-label="Toggle derivatives" aria-expanded={!collapsed} onClick={() => toggleNodeCollapse(TAXONOMY_DERIVATIVES_ROW_ID)}>
          <span className={`taxonomy-tree-arrow ${collapsed ? 'taxonomy-tree-arrow-collapsed' : 'taxonomy-tree-arrow-expanded'}`} /></button>
        <span className="portfolio-tree-label" data-tree-level="primary">{zh ? '衍生品' : DERIVATIVES_TARGET_LABEL}</span></div></td>
      <td>—</td><td>{formatReportAmount(derivativeAggregate.current_value_base)}</td>
      <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
    </tr>]
    if (!collapsed) {
      if (!derivativeHoldingRows.length) rows.push(<TableStatusRow key="derivatives-empty" colSpan={8} label="No derivative holdings." />)
      derivativeHoldingRows.slice().sort((a, b) => derivativeHoldingLabel(a).localeCompare(derivativeHoldingLabel(b))).forEach((row) => {
        const contractId = row.derivative_contract?.derivative_contract_id
        const isFcn = row.derivative_contract?.contract_type === 'fcn' && contractId
        rows.push(<tr key={row.line_id} className="portfolio-tree-row taxonomy-entity-row taxonomy-entity-row-drag-locked" data-tree-level="item">
          <td><div className="taxonomy-node-row" style={{ paddingLeft: `${(depth + 1) * 18}px` }}><span className="taxonomy-tree-toggle taxonomy-tree-toggle-empty" />
            <span className="taxonomy-level-label portfolio-tree-label" data-tree-level="item" title={derivativeHoldingLabel(row)}>{derivativeHoldingLabel(row)}</span></div></td>
          <td>—</td><td>{formatReportAmount(row.market_value_base)}</td>
          <td>{isFcn ? renderExposure('fcn', contractId) : '—'}</td><td>—</td><td>—</td><td>—</td>
          <td>{isFcn ? renderConcentrationLimit('fcn', contractId, derivativeHoldingLabel(row)) : '—'}</td>
        </tr>)
      })
    }
    return rows
  }

  function renderCashTreeRows(depth: number): ReactElement[] {
    if (!showSystemCashNode) return []
    const member: TargetScopeMember = { member_key: targetMemberKey('cash_bucket', CASH_TARGET_MEMBER_ID), target_member_type: 'cash_bucket',
      target_member_id: CASH_TARGET_MEMBER_ID, taxonomy_node_id: null, node: null, entity: null, label: CASH_TARGET_LABEL, system_role: 'cash' }
    const collapsed = collapsedNodeIds.has(TAXONOMY_CASH_ROW_ID)
    const rows: ReactElement[] = [<tr key={TAXONOMY_CASH_ROW_ID} className="portfolio-tree-row taxonomy-system-cash-row taxonomy-category-row" data-tree-level="primary">
      <td><div className="taxonomy-node-row" style={{ paddingLeft: `${depth * 18}px` }}><button type="button" className="taxonomy-tree-toggle" aria-label="Toggle cash" aria-expanded={!collapsed} onClick={() => toggleNodeCollapse(TAXONOMY_CASH_ROW_ID)}>
        <span className={`taxonomy-tree-arrow ${collapsed ? 'taxonomy-tree-arrow-collapsed' : 'taxonomy-tree-arrow-expanded'}`} /></button><span className="portfolio-tree-label" data-tree-level="primary">{zh ? '现金预留' : CASH_TARGET_LABEL}</span></div></td>
      <td>NAV</td>
      <td>{formatReportAmount(cashAggregate.current_value_base)}</td>
      <td>—</td>
      <td>{renderTargetCell('saa', member, targetEditMode)}</td><td>{renderTargetCell('taa', member, targetEditMode)}</td><td>—</td><td>—</td>
    </tr>]
    if (!collapsed) {
      if (cashBucketEntities.length) cashBucketEntities.slice().sort((a, b) => a.label.localeCompare(b.label)).forEach((entity) => rows.push(renderEntityTreeRow(entity, depth + 1)))
      else rows.push(<TableStatusRow key="cash-empty" colSpan={8} label="No cash accounts." />)
    }
    return rows
  }

  function renderNodeTreeRows(parentId: string | null, depth: number): ReactElement[] {
    return (childrenByParent.get(parentId) ?? []).flatMap((node) => {
      const entities = directEntitiesByNodeId.get(node.taxonomy_node_id) ?? []
      const hasChildren = (childrenByParent.get(node.taxonomy_node_id) ?? []).length > 0 || entities.length > 0
      const collapsed = collapsedNodeIds.has(node.taxonomy_node_id)
      const selected = selectedNodeId === node.taxonomy_node_id
      const aggregate = nodeAggregates.get(node.taxonomy_node_id)
      const member: TargetScopeMember = { member_key: targetMemberKey('taxonomy_node', node.taxonomy_node_id), target_member_type: 'taxonomy_node',
        target_member_id: node.taxonomy_node_id, taxonomy_node_id: node.taxonomy_node_id, node, entity: null, label: node.node_name }
      const dropEnabled = canEditPortfolio && canDropTaxonomyEntity({ targetEditMode, terminalNode: node.is_terminal, actionPending: Boolean(actionPending) })
      return [<tr key={node.taxonomy_node_id} className={['portfolio-tree-row', 'taxonomy-category-row', selected ? 'taxonomy-node-row-active' : '', dragTargetNodeId === node.taxonomy_node_id ? 'taxonomy-drop-target-row' : ''].filter(Boolean).join(' ')} data-tree-level={depth === 1 ? 'primary' : 'nested'}
        data-assignment-drop={dropEnabled ? 'enabled' : 'disabled'}
        onContextMenu={!canEditPortfolio || targetEditMode ? undefined : (event) => handleNodeContextMenu(event, node)}
        onDragOver={dropEnabled ? (event) => handleNodeDragOver(event, node) : undefined}
        onDragLeave={dropEnabled ? () => setDragTargetNodeId(null) : undefined}
        onDrop={dropEnabled ? (event) => void handleNodeDrop(event, node) : undefined}>
        <td className="holding-name-cell"><div className="taxonomy-node-row" style={{ paddingLeft: `${depth * 18}px` }}>
          {hasChildren ? <button type="button" className="taxonomy-tree-toggle" aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${node.node_name}`} aria-expanded={!collapsed} onClick={() => toggleNodeCollapse(node.taxonomy_node_id)}>
            <span className={`taxonomy-tree-arrow ${collapsed ? 'taxonomy-tree-arrow-collapsed' : 'taxonomy-tree-arrow-expanded'}`} /></button> : <span className="taxonomy-tree-toggle taxonomy-tree-toggle-empty" />}
          <button type="button" className={`taxonomy-node-select portfolio-tree-label ${selected ? 'taxonomy-node-select-active' : ''}`} data-tree-level={depth === 1 ? 'primary' : 'nested'} translate="no" title={node.node_name} onClick={() => revealTargetScope(targetScopeKey(node.taxonomy_node_id))}>{node.node_name}</button>
          <span className="taxonomy-tree-count">{aggregate?.current_entity_count ?? 0}</span></div></td>
        <td>{renderAllocationBasis(node)}</td><td>{formatReportAmount(aggregate?.current_value_base)}</td>
        <td>{renderExposure('taxonomy', node.taxonomy_node_id, selectedTaxonomy!.taxonomy_id)}</td>
        <td>{renderTargetCell('saa', member, targetEditMode)}</td><td>{renderTargetCell('taa', member, targetEditMode)}</td>
        <td>{renderGlobalRiskTarget(member)}</td><td>{renderConcentrationLimit('taxonomy', node.taxonomy_node_id, node.node_name, selectedTaxonomy!.taxonomy_id)}</td>
      </tr>, ...(!collapsed ? [...renderNodeTreeRows(node.taxonomy_node_id, depth + 1), ...entities.map((entity) => renderEntityTreeRow(entity, depth + 1))] : [])]
    })
  }

  const pendingDeleteDialog = pendingDelete
    ? pendingDelete.kind === 'taxonomy'
        ? {
            title: 'Delete Taxonomy',
            description: 'This permanently deletes the taxonomy, its nodes, assignments, and target sets. This action cannot be undone.',
            label: 'Delete Taxonomy',
            confirmationText: pendingDelete.taxonomy.name,
          }
        : pendingDelete.kind === 'node'
          ? {
              title: 'Delete Taxonomy Node',
              description: zh
                ? '将删除此节点及全部子节点、其分类归属和相关目标配置。实际交易与持仓、已保存研究快照会保留；相关持仓将变为未分类，剩余目标不会自动调整，需重新核对并保存。此操作不可撤销。'
                : 'This deletes the node and all descendants, their assignments, and related target configuration. Transactions, holdings, and saved research snapshots are retained. Affected holdings become unassigned; remaining targets are not redistributed and must be reviewed. This action cannot be undone.',
              label: 'Delete Node',
              confirmationText: pendingDelete.node.node_name,
            }
          : {
              title: 'Remove Observed Instrument',
              description: 'This removes the instrument from the portfolio observation universe. The shared instrument is not deleted.',
              label: 'Remove Instrument',
              confirmationText: pendingDelete.entity.label,
            }
    : null

    return (
      <PortfolioWorkspaceLayout activeSection="Taxonomies" busy={loading || refreshing}>
        <div className="taxonomy-page taxonomy-page-table">
        {notice || workspaceError || actionError || supplementalNotice ? (
          <div className="page-toast-stack" role="status" aria-live="polite">
            {notice ? <div className="page-toast page-toast-success">{notice}</div> : null}
            {workspaceError ? <div className="page-toast page-toast-error">{configurationErrorMessage(workspaceError, zh)}</div> : null}
            {actionError ? <div className="page-toast page-toast-error">{actionError}</div> : null}
            {supplementalNotice ? <div className="page-toast">{supplementalNotice}</div> : null}
          </div>
        ) : null}
        {loading ? <CalculationStatus /> : null}

      {!loading && !catalog && !workspaceError ? <div className="empty-state">No data.</div> : null}

      {!loading && !workspaceError ? (
        <>
          <section className="panel taxonomy-strip-section">
            <div className="taxonomy-topbar">
              <div className="taxonomy-topbar-field" ref={taxonomyPickerRef}>
                <span>{zh ? '分类：' : 'Taxonomy:'}</span><div className="taxonomy-picker">
                  <button type="button" className="taxonomy-picker-trigger" disabled={targetEditMode || Boolean(actionPending)} onClick={() => { setContextMenuState(null); setTaxonomyPickerOpen((current) => !current) }}>
                    <span translate={selectedTaxonomy ? 'no' : undefined}>{selectedTaxonomy?.name ?? (zh ? '暂无分类' : 'No taxonomy')}</span><span className="portfolio-select-caret" aria-hidden="true" />
                  </button>
                  {taxonomyPickerOpen ? <div className="taxonomy-picker-menu">
                    {taxonomies.map((taxonomy) => <button type="button" key={taxonomy.taxonomy_id} className={`taxonomy-picker-option ${taxonomy.taxonomy_id === resolvedSelectedTaxonomyId ? 'taxonomy-picker-option-active' : ''}`}
                      translate="no" onClick={() => handleTaxonomySelection(taxonomy.taxonomy_id)}>{taxonomy.name}</button>)}
                  </div> : null}
                </div>
              </div>
              <div className="taxonomy-header-actions">
                <button type="button" className="button-secondary taxonomy-toolbar-button" disabled={!canEditPortfolio || targetEditMode || Boolean(actionPending)} onClick={() => { closeModalStack(); setShowTaxonomyCreate(true) }}>{zh ? '+ 添加分类' : '+ Add taxonomy'}</button>
                <button type="button" className="button-secondary taxonomy-toolbar-button" disabled={!canEditPortfolio || !selectedTaxonomy || targetEditMode || Boolean(actionPending)} onClick={() => startInstrumentAdd(selectedNode?.is_terminal ? selectedNode : null)}>{zh ? '+ 添加标的' : '+ Add instrument'}</button>
                <div className="taxonomy-editor-actions">
                  {targetEditMode ? <>
                    <button type="button" className="button-secondary taxonomy-toolbar-button" disabled={Boolean(actionPending)} onClick={cancelConfigurationEdit}>{zh ? '取消' : 'Cancel'}</button>
                    <button type="button" className="button-primary taxonomy-toolbar-button" onClick={() => void handleSaveTargetsConfiguration()} disabled={!canSaveTargetsConfiguration || Boolean(actionPending)}>{actionPending === 'targets-save' ? (zh ? '保存中…' : 'Saving…') : (zh ? '保存' : 'Save')}</button>
                  </> : <button type="button" className="button-primary taxonomy-toolbar-button" disabled={!canEditPortfolio || !selectedTaxonomy || refreshing || concentrationSettingsLoading || Boolean(actionPending)} onClick={beginConfigurationEdit}>{zh ? '编辑' : 'Edit'}</button>}
                </div>
              </div>
            </div>
            {showInstrumentAdd && canEditPortfolio ? <form className="taxonomy-add-instrument" onSubmit={(event) => void handleAddRegistryInstrumentToUniverse(event)} aria-label={zh ? '添加标的' : 'Add instrument'}>
              <SecurityInstrumentPicker label={zh ? '标的' : 'Instrument'} ariaLabel="Search instrument" value={instrumentAddInstrumentId} instruments={instrumentRows.filter((item) => !isCashInstrument(item))}
                portfolioId={portfolioId} onSelect={setInstrumentAddInstrumentId} onInstrumentRegistered={(instrument) => setInstrumentsResponse((current) => ({ portfolio_id: portfolioId, instruments: [...(current?.instruments ?? []).filter((item) => item.instrument_id !== instrument.instrument_id), instrument] }))} />
              <label><span>{zh ? '归入分类' : 'Assign to'}{instrumentAddExistingAssignment ? <InfoHint label={zh ? '目前归属' : 'Current assignment'} detail={nodeById.get(instrumentAddExistingAssignment.taxonomy_node_id)?.node_name ?? instrumentAddExistingAssignment.taxonomy_node_id} /> : null}</span><select aria-label="Instrument destination" value={instrumentAddNodeId} onChange={(event) => setInstrumentAddNodeId(event.target.value)}>
                <option value="">{zh ? '暂未分类' : 'Unassigned'}</option>{selectedTaxonomyNodes.filter((node) => node.is_terminal).map((node) => <option key={node.taxonomy_node_id} value={node.taxonomy_node_id}>{(nodePathByNodeId.get(node.taxonomy_node_id) ?? []).map((item) => item.node_name).join(' / ')}</option>)}
              </select></label>
              <div className="taxonomy-add-actions"><button type="submit" className="button-primary taxonomy-toolbar-button" disabled={!instrumentAddInstrumentId || Boolean(actionPending) || Boolean(instrumentAddExistingAssignment && !instrumentAddNodeId)}>
                {instrumentAddExistingAssignment && instrumentAddExistingAssignment.taxonomy_node_id !== instrumentAddNodeId ? (zh ? '移动标的' : 'Move instrument') : (zh ? '添加标的' : 'Add instrument')}
              </button><button type="button" className="button-secondary taxonomy-toolbar-button" disabled={Boolean(actionPending)} onClick={() => setShowInstrumentAdd(false)}>{zh ? '取消' : 'Cancel'}</button></div>
            </form> : null}
          </section>

          {selectedTaxonomy ? <section className="panel taxonomy-levels-section" aria-keyshortcuts="Control+Enter Meta+Enter" onKeyDown={handleTaxonomyScopeKeydown}>
            <div className="taxonomy-tree-summary taxonomy-collapsed-summary">
              <div className="taxonomy-target-summary-meta"><span>{zh ? '标的' : 'Instruments'} {coverageSummary.currentEntityCount}</span><span>{zh ? '已分类' : 'Assigned'} {coverageSummary.assignedCount}</span>
                <span className="taxonomy-status-legend" aria-label="Instrument status legend">
                  {renderInstrumentStatusLegendItem('held', zh ? '持有' : 'Held', coverageSummary.heldEntityCount)}
                  {renderInstrumentStatusLegendItem('observe', zh ? '观察' : 'Observed', coverageSummary.observeEntityCount)}
                  {renderInstrumentStatusLegendItem('former', zh ? '曾持有' : 'Former', coverageSummary.formerEntityCount)}
                </span>
              </div>
              <div className="taxonomy-tree-actions"><button type="button" className="table-inline-button" onClick={() => setCollapsedNodeIds(new Set())}>{zh ? '展开全部' : 'Expand all'}</button>
                <button type="button" className="table-inline-button" onClick={() => setCollapsedNodeIds(new Set([...selectedTaxonomyNodes.map((node) => node.taxonomy_node_id), TAXONOMY_DERIVATIVES_ROW_ID, TAXONOMY_CASH_ROW_ID, TAXONOMY_UNASSIGNED_ROW_ID]))}>{zh ? '收起全部' : 'Collapse all'}</button></div>
            </div>
            {targetEditMode && (targetSaveBlockedReason || concentrationValidationError) ? <div className="taxonomy-target-validation-message" role="alert">{targetSaveBlockedReason} {concentrationValidationError}</div> : null}
            {configurationConflict ? <div className="taxonomy-editor-notice"><span>{zh ? '草稿已保留。重新载入会放弃当前草稿，并读取最新配置。' : 'Your draft is preserved. Reloading discards this draft and reads the latest configuration.'}</span>{' '}
              <button type="button" className="table-inline-button" disabled={Boolean(actionPending)} onClick={() => { cancelConfigurationEdit(); setConcentrationRevision((value) => value + 1); void reloadWorkspace() }}>{zh ? '放弃草稿并重新载入' : 'Discard draft and reload'}</button></div> : null}
            {targetSetIntegrityNotice ? <div className="taxonomy-target-integrity-warning" role="alert">{targetSetIntegrityNotice}</div> : null}
            {concentrationError || concentrationSettingsError ? <div className="taxonomy-editor-notice">{zh ? '集中度数据不可用：' : 'Concentration unavailable: '}{[concentrationError, concentrationSettingsError].filter(Boolean).map((message) => configurationErrorMessage(message!, zh)).join(' ')}</div> : null}
            <HorizontalTableScroll className="table-shell taxonomy-tree-scroll"><table className="transactions-table taxonomy-overview-table" aria-label={zh ? '分类与资产总览' : 'Classification and asset overview'}>
              <colgroup><col style={{ width: '30%' }} /><col style={{ width: '9.5%' }} /><col style={{ width: '14%' }} /><col style={{ width: '9%' }} /><col style={{ width: '8%' }} /><col style={{ width: '8%' }} /><col style={{ width: '10%' }} /><col style={{ width: '11.5%' }} /></colgroup>
              <thead><tr><th>{zh ? '名称' : 'Name'}</th><th>{zh ? '本层依据' : 'Children’s basis'} <InfoHint label={zh ? '本层依据' : 'Children’s basis'} detail={zh
                  ? ['父行的依据决定直接子行的战略与战术目标，证券目标合计 100%。', '权重占父层可配置证券资金，风险预算占父层风险预算。根层扣除实际衍生品账面金额与现金预留；现金目标单独占组合 NAV。']
                  : ['Each parent’s basis controls its direct children’s SAA and TAA targets. Security targets total 100% within each parent.', 'Weight uses the parent’s investable security capital; risk budgets use the parent’s risk budget. At the root, actual derivative carrying value and the cash reserve are deducted. Cash targets are a separate NAV reserve.']} /></th>
                <th>{zh ? '账面金额' : 'Carrying Amount'}{baseCurrency ? ` (${baseCurrency})` : ''}</th>
                <th>{zh ? '敞口比例' : 'Exposure Ratio'} <InfoHint label={zh ? '敞口口径' : 'Exposure basis'} detail={zh
                  ? ['敞口比例=敞口金额/组合 NAV，不是账面金额对应的当前权重。证券按各账户绝对市值汇总；FCN按剩余名义本金计量，分类节点包含分配至挂钩证券的本金。', '现金和待结算不计入敞口分子。期权敞口未纳入此投影，不能解释为零；NAV仍保留其账面金额。数据不完整时，≥表示已知敞口下界。', '账面金额为证券市值、现金余额或衍生品带方向账面价值，均使用组合报告币种。']
                  : ['Exposure ratio is exposure amount / portfolio NAV, not the current carrying-value weight. Securities sum absolute market values across accounts; FCNs use remaining nominal principal, including allocations to linked securities in taxonomy nodes.', 'Cash and settlements are excluded from the numerator. Option exposure is not modeled by this projection and must not be read as zero; NAV retains its carrying amount. With incomplete data, ≥ marks the known lower bound.', 'Carrying amounts are security market values, cash balances or signed derivative carrying values, in the portfolio reporting currency.']} /></th>
                <th>{zh ? '战略目标' : 'SAA target'}</th>
                <th>{zh ? '战术目标' : 'TAA target'} <InfoHint label={zh ? '战术目标' : 'TAA target'} detail={zh ? '战术目标整层留空时继承战略；填写时须补齐本层全部成员。查看时显示实际生效的目标。' : 'A completely blank TAA level inherits SAA. A partially filled level must be completed. The read view shows effective targets.'} /></th>
                <th>{zh ? '目标风险贡献' : 'Target RC'} <InfoHint label={zh ? '目标风险贡献口径' : 'Target RC basis'} detail={zh
                  ? '使用实际生效的战术目标，沿父层风险预算推导其占全组合总风险的份额。路径包含多成员权重配置或目标不完整时无法直接推导，显示 —。修改依据或目标后，保存时更新。'
                  : 'Effective TAA targets are derived through parent risk budgets as a share of total portfolio risk. A multi-member weight allocation or incomplete targets prevents derivation, shown as —. Changes to bases or targets update after saving.'} /></th>
                <th>{zh ? '集中度上限' : 'Concentration Limit'} <InfoHint label={zh ? '集中度上限' : 'Concentration limit'} detail={zh
                  ? ['敞口比例的提醒上限，不是优化器硬约束。留空不设限，0 表示禁止正敞口。', '根行开关只控制本分类节点的提醒；单一证券和 FCN 限额仍有效。', `限额从 ${holdingsWorkspace?.as_of_date ?? '—'} 起生效。`]
                  : ['An exposure-ratio reminder, not a hard optimizer constraint. Blank means no limit; zero prohibits positive exposure.', 'The root switch controls taxonomy-node reminders only. Individual security and FCN limits remain active.', `Limits take effect from ${holdingsWorkspace?.as_of_date ?? '—'}.`]} /></th>
              </tr></thead><tbody>
                <tr className={`portfolio-tree-row taxonomy-root-row ${!selectedNode ? 'taxonomy-node-row-active' : ''}`} data-tree-level="root" onContextMenu={canEditPortfolio && !targetEditMode && !actionPending ? (event) => { event.preventDefault(); setSelectedNodeId(null); setContextMenuState({ kind: 'taxonomy', taxonomyId: selectedTaxonomy.taxonomy_id, x: event.clientX, y: event.clientY }) } : undefined}>
                  <td className="holding-name-cell"><div className="taxonomy-node-row"><button type="button" className="taxonomy-tree-toggle" aria-label={`${collapsedNodeIds.has(TAXONOMY_ROOT_ROW_ID) ? 'Expand' : 'Collapse'} ${selectedTaxonomy.name}`} aria-expanded={!collapsedNodeIds.has(TAXONOMY_ROOT_ROW_ID)} onClick={() => toggleNodeCollapse(TAXONOMY_ROOT_ROW_ID)}><span className={`taxonomy-tree-arrow ${collapsedNodeIds.has(TAXONOMY_ROOT_ROW_ID) ? 'taxonomy-tree-arrow-collapsed' : 'taxonomy-tree-arrow-expanded'}`} /></button>
                    <button type="button" className="taxonomy-node-select portfolio-tree-label" data-tree-level="root" translate="no" onClick={() => revealTargetScope(ROOT_TARGET_SCOPE_KEY)}>{selectedTaxonomy.name}</button></div></td>
                  <td>{renderAllocationBasis(null)}</td><td>{formatReportAmount(displayedBookValueBase)}</td><td>—</td><td>—</td><td>—</td><td>—</td>
                  <td><label className="taxonomy-reminder-toggle">
                    <input type="checkbox" aria-label="Taxonomy concentration reminders" checked={taxonomyConcentrationEnabled} disabled={!canEditPortfolio || !targetEditMode || !concentrationSettings || Boolean(actionPending)} onChange={(event) => setTaxonomyConcentrationEnabled(event.target.checked)} />{zh ? '分类提醒' : 'Reminders'}</label></td>
                </tr>
                {!collapsedNodeIds.has(TAXONOMY_ROOT_ROW_ID) ? <>{renderNodeTreeRows(null, 1)}{renderDerivativeTreeRows(1)}{renderCashTreeRows(1)}
                  <tr className="portfolio-tree-row taxonomy-subsection-row" data-tree-level="primary"><td><div className="taxonomy-node-row" style={{ paddingLeft: '18px' }}><button type="button" className="taxonomy-tree-toggle" aria-label="Toggle unassigned" aria-expanded={!collapsedNodeIds.has(TAXONOMY_UNASSIGNED_ROW_ID)} onClick={() => toggleNodeCollapse(TAXONOMY_UNASSIGNED_ROW_ID)}><span className={`taxonomy-tree-arrow ${collapsedNodeIds.has(TAXONOMY_UNASSIGNED_ROW_ID) ? 'taxonomy-tree-arrow-collapsed' : 'taxonomy-tree-arrow-expanded'}`} /></button><span className="portfolio-tree-label" data-tree-level="primary">{zh ? '未分类' : 'Unassigned'}</span></div></td>
                    <td>—</td><td>{formatReportAmount(unassignedSummary.current_value_base)}</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>
                  {!collapsedNodeIds.has(TAXONOMY_UNASSIGNED_ROW_ID) ? coverageSummary.unassignedEntities.slice().sort((a, b) => a.label.localeCompare(b.label)).map((entity) => renderEntityTreeRow(entity, 2)) : null}
                </> : null}
              </tbody></table></HorizontalTableScroll>
          </section> : null}
          <TaxonomyModal
            open={canEditPortfolio && showTaxonomyCreate}
            title={zh ? '添加分类' : 'New Taxonomy'}
            onClose={() => setShowTaxonomyCreate(false)}
          >
            <form className="transaction-form taxonomy-form-compact" onSubmit={(event) => void handleCreateTaxonomy(event)}>
              <div className="taxonomy-form-grid taxonomy-topbar-form-grid">
                <label>
                  <span>{zh ? '名称' : 'Name'}</span>
                  <input value={taxonomyName} onChange={(event) => setTaxonomyName(event.target.value)} required />
                </label>
              </div>
              <div className="transaction-form-footer">
                <div className="taxonomy-footer-actions">
                  <button type="button" className="toolbar-link" onClick={() => setShowTaxonomyCreate(false)}>
                    {zh ? '取消' : 'Cancel'}
                  </button>
                  <button type="submit" className="toolbar-link button-primary" disabled={actionPending === 'taxonomy-create'}>
                    {actionPending === 'taxonomy-create' ? (zh ? '创建中…' : 'Creating…') : (zh ? '创建分类' : 'Create Taxonomy')}
                  </button>
                </div>
              </div>
            </form>
          </TaxonomyModal>
          <TaxonomyModal
            open={canEditPortfolio && showTaxonomyRename && Boolean(taxonomyRenameId)}
            title="Rename Taxonomy"
            onClose={() => setShowTaxonomyRename(false)}
          >
            <form className="transaction-form taxonomy-form-compact" onSubmit={(event) => void handleRenameTaxonomy(event)}>
              <div className="taxonomy-form-grid taxonomy-node-name-grid">
                <label>
                  <span>Name</span>
                  <input value={taxonomyRenameName} onChange={(event) => setTaxonomyRenameName(event.target.value)} required autoFocus />
                </label>
              </div>
              <div className="transaction-form-footer">
                <div className="taxonomy-footer-actions">
                  <button type="button" className="toolbar-link" onClick={() => setShowTaxonomyRename(false)}>
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="toolbar-link button-primary"
                    disabled={actionPending === `taxonomy-rename-${taxonomyRenameId}` || !taxonomyRenameName.trim()}
                  >
                    {actionPending === `taxonomy-rename-${taxonomyRenameId}` ? 'Saving…' : 'Rename Taxonomy'}
                  </button>
                </div>
              </div>
            </form>
          </TaxonomyModal>
          <TaxonomyModal
            open={canEditPortfolio && showNodeCreate}
            title={nodeCreateContextLabel}
            onClose={() => setShowNodeCreate(false)}
          >
            <form className="transaction-form taxonomy-form-compact" onSubmit={(event) => void handleCreateNode(event)}>
              <div className="taxonomy-form-grid taxonomy-node-name-grid">
                <label>
                  <span>Node Name</span>
                  <input value={newNodeName} onChange={(event) => setNewNodeName(event.target.value)} required />
                </label>
              </div>
              <div className="transaction-form-footer">
                <div className="taxonomy-footer-actions">
                  <button type="button" className="toolbar-link" onClick={() => setShowNodeCreate(false)}>
                    Cancel
                  </button>
                  <button type="submit" className="toolbar-link button-primary" disabled={actionPending === 'node-create'}>
                    {actionPending === 'node-create' ? 'Adding…' : nodeCreateContextLabel}
                  </button>
                </div>
              </div>
            </form>
          </TaxonomyModal>
          <TaxonomyModal
            open={canEditPortfolio && showNodeEdit && Boolean(editingNode)}
            title="Rename Node"
            onClose={() => setShowNodeEdit(false)}
          >
            {editingNode ? (
              <form className="transaction-form taxonomy-form-compact" onSubmit={(event) => void handleSaveNodeEdit(event)}>
                <div className="taxonomy-form-grid taxonomy-node-name-grid">
                  <label>
                    <span>Node Name</span>
                    <input value={nodeEditName} onChange={(event) => setNodeEditName(event.target.value)} required />
                  </label>
                </div>
                <div className="transaction-form-footer">
                  <div className="taxonomy-footer-actions">
                    <button type="button" className="toolbar-link" onClick={() => setShowNodeEdit(false)}>
                      Cancel
                    </button>
                    <button
                      type="submit"
                      className="toolbar-link button-primary"
                      disabled={actionPending === `node-save-${editingNode.taxonomy_node_id}`}
                    >
                      {actionPending === `node-save-${editingNode.taxonomy_node_id}` ? 'Saving…' : 'Rename Node'}
                    </button>
                  </div>
                </div>
              </form>
            ) : null}
          </TaxonomyModal>
          {canEditPortfolio && contextMenuState ? (
            <div
              className="taxonomy-context-menu"
              style={contextMenuStyle}
              onClick={(event) => event.stopPropagation()}
            >
              {contextMenuTaxonomy ? (
                <>
                  <button type="button" className="taxonomy-context-menu-item" onClick={() => startNodeCreate('root')}>{zh ? '添加子分类' : 'Add child category'}</button>
                  <button type="button" className="taxonomy-context-menu-item" onClick={() => startInstrumentAdd(null)}>{zh ? '添加标的' : 'Add instrument'}</button>
                  <button type="button" className="taxonomy-context-menu-item" disabled={concentrationSettingsLoading} onClick={beginConfigurationEdit}>{zh ? '编辑目标与限额' : 'Edit targets and limits'}</button>
                  <button type="button" className="taxonomy-context-menu-item" onClick={() => startTaxonomyRename(contextMenuTaxonomy)}>
                    Rename
                  </button>
                  <button
                    type="button"
                    className="taxonomy-context-menu-item taxonomy-context-menu-item-danger"
                    onClick={() => void handleDeleteTaxonomy(contextMenuTaxonomy)}
                    disabled={actionPending === `taxonomy-delete-${contextMenuTaxonomy.taxonomy_id}`}
                  >
                    Delete Taxonomy
                  </button>
                </>
              ) : contextMenuNode ? (
                <>
                  <button type="button" className="taxonomy-context-menu-item" disabled={!contextMenuNode.is_terminal} onClick={() => startInstrumentAdd(contextMenuNode)}>{zh ? '添加标的' : 'Add instrument'}</button>
                  <button type="button" className="taxonomy-context-menu-item" disabled={concentrationSettingsLoading} onClick={beginConfigurationEdit}>{zh ? '编辑目标与限额' : 'Edit targets and limits'}</button>
                  <button type="button" className="taxonomy-context-menu-item" onClick={() => startNodeCreate('sibling', contextMenuNode)}>
                    Add Same Level
                  </button>
                  <button type="button" className="taxonomy-context-menu-item" onClick={() => startNodeCreate('child', contextMenuNode)}>
                    Add Child
                  </button>
                  <button type="button" className="taxonomy-context-menu-item" onClick={() => startNodeEdit(contextMenuNode)}>
                    Rename
                  </button>
                  {contextMenuNode.is_terminal ? (
                    <button
                      type="button"
                      className="taxonomy-context-menu-item"
                      disabled={!canEditPortfolio || targetEditMode || selectedEntityCount === 0}
                      title={targetEditMode ? TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE : undefined}
                      onClick={() => {
                        setContextMenuState(null)
                        void assignEntitiesToNode(contextMenuNode, Array.from(selectedEntityIds))
                      }}
                    >
                      Assign Selected Items Here
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="taxonomy-context-menu-item taxonomy-context-menu-item-danger"
                    onClick={() => {
                      setContextMenuState(null)
                      void handleDeleteNode(contextMenuNode)
                    }}
                  >
                    Delete Node
                  </button>
                </>
              ) : contextMenuEntity ? (
                <>
                  <button
                    type="button"
                    className="taxonomy-context-menu-item"
                    onClick={() => {
                      setSelectedEntityIds(new Set([contextMenuEntity.entity_id]))
                      setContextMenuState(null)
                    }}
                  >
                    Select Item
                  </button>
                  <button
                    type="button"
                    className="taxonomy-context-menu-item"
                    disabled={!canEditPortfolio || targetEditMode || !selectedNode?.is_terminal}
                    title={targetEditMode ? TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE : undefined}
                    onClick={() => {
                      const entityIds = selectedEntityIds.has(contextMenuEntity.entity_id)
                        ? Array.from(selectedEntityIds)
                        : [contextMenuEntity.entity_id]
                      if (!selectedEntityIds.has(contextMenuEntity.entity_id)) {
                        setSelectedEntityIds(new Set(entityIds))
                      }
                      setContextMenuState(null)
                      if (selectedNode) {
                        void assignEntitiesToNode(selectedNode, entityIds)
                      }
                    }}
                  >
                    Assign To Selected Leaf
                  </button>
                  {contextMenuEntity.instrument_state === 'observe' ? (
                    <button
                      type="button"
                      className="taxonomy-context-menu-item taxonomy-context-menu-item-danger"
                      disabled={Boolean(contextMenuEntity.current_assignment)}
                      onClick={() => void handleDeleteObservedInstrument(contextMenuEntity)}
                    >
                      Remove Observed Instrument
                    </button>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
      </div>
      <ConfirmDialog
        open={canEditPortfolio && Boolean(pendingDeleteDialog)}
        title={pendingDeleteDialog?.title ?? 'Confirm Delete'}
        description={pendingDeleteDialog?.description ?? ''}
        confirmLabel={pendingDeleteDialog?.label ?? 'Delete'}
        confirmationText={pendingDeleteDialog?.confirmationText}
        error={actionError}
        busy={Boolean(actionPending)}
        onCancel={() => setPendingDelete(null)}
        onConfirm={handleConfirmedDelete}
      />
    </PortfolioWorkspaceLayout>
  )
}
