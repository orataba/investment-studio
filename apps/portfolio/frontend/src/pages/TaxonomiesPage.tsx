import { FormEvent, useEffect, useMemo, useRef, useState, type DragEvent as ReactDragEvent, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  createPortfolioInstrumentUniverseRecord,
  createPortfolioTaxonomy,
  createPortfolioTaxonomyAssignment,
  createPortfolioTaxonomyNode,
  createPortfolioTargetSet,
  deletePortfolioInstrumentUniverseRecord,
  deletePortfolioTaxonomy,
  deletePortfolioTaxonomyNode,
  deletePortfolioTargetSet,
  getHoldingsWorkspace,
  getPortfolioInstruments,
  getPortfolioAccountsWorkspace,
  getPortfolioTaxonomyCatalog,
  updatePortfolioDefaultPlanningTaxonomy,
  updatePortfolioTaxonomy,
  updatePortfolioTaxonomyAssignment,
  updatePortfolioTaxonomyNode,
  updatePortfolioTargetSet,
  type InstrumentCore,
  type HoldingsWorkspaceResponse,
  type PortfolioAccountsWorkspaceResponse,
  type PortfolioInstrumentUniverseRecord,
  type PortfolioTargetSetLineRecord,
  type PortfolioTargetSetRecord,
  type PortfolioTaxonomyAssignmentRecord,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioTaxonomyRecord,
  type SharedInstrumentRecord,
  type TaxonomyAssignmentScope,
} from '../lib/api'
import { formatCurrency, formatLabel, formatPercent } from '../lib/format'
import {
  TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE,
  canDragTaxonomyEntity,
  canDropTaxonomyEntity,
  targetDimensionEnabledForDraft,
} from '../lib/taxonomyInteractionPolicy'
import {
  formatTargetSetIntegrityNotice,
  targetSetIntegrityIssuesForTaxonomy,
} from '../lib/taxonomyTargetIntegrity'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'

type TreeRow = PortfolioTaxonomyNodeRecord & {
  depth: number
  has_children: boolean
  child_count: number
}

type CoverageEntity = {
  entity_id: string
  target_scope: TaxonomyAssignmentScope
  label: string
  supporting_label: string
  allocation: number | null
  market_value_base: number | null
  current_assignment: PortfolioTaxonomyAssignmentRecord | null
  current_node: PortfolioTaxonomyNodeRecord | null
  holding_state: 'held' | 'not_held'
  instrument_state: 'held' | 'former' | 'observe' | null
  instrument_state_label: string | null
  coverage_state: 'unassigned' | 'ambiguous' | 'selected' | 'other'
}

type PendingTaxonomyDelete =
  | {
      kind: 'target-set'
      portfolioId: string
      targetKind: 'saa' | 'taa'
      taxonomyId: string
      targetSet: PortfolioTargetSetRecord
      scopeLabel: string
    }
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

type TargetLineDraft = {
  target_weight: string
  target_risk_share: string
  notes: string
}

type TargetMemberType = 'taxonomy_node' | TaxonomyAssignmentScope

type TargetScopeMember = {
  member_key: string
  target_member_type: TargetMemberType
  target_member_id: string
  taxonomy_node_id: string | null
  node: PortfolioTaxonomyNodeRecord | null
  entity: CoverageEntity | null
  label: string
  system_role?: 'cash'
}

type TargetSetDraft = {
  name: string
  weight_enabled: boolean
  risk_budget_enabled: boolean
  status: string
  notes: string
  lines_by_member_key: Record<string, TargetLineDraft>
}

type TargetSetValidation = {
  savable: boolean
  errors: string[]
  warnings: string[]
  weight_sum_pct: number | null
  risk_sum_pct: number | null
}

type DefaultTargetDimension = 'weight' | 'risk_budget'

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
const TAXONOMY_CASH_ROW_ID = '__taxonomy_cash__'
const TAXONOMY_UNASSIGNED_ROW_ID = '__taxonomy_unassigned__'
const ROOT_TARGET_SCOPE_KEY = '__target_scope_root__'
const CASH_TARGET_MEMBER_ID = '__cash__'
const CASH_TARGET_LABEL = 'Cash'
const EMPTY_TARGET_SET_DRAFT: TargetSetDraft = {
  name: '',
  weight_enabled: false,
  risk_budget_enabled: false,
  status: 'active',
  notes: '',
  lines_by_member_key: {},
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

function defaultTargetDraftsFromNodes(nodes: PortfolioTaxonomyNodeRecord[]) {
  return nodes.reduce<Record<string, DefaultTargetDimension>>((drafts, node) => {
    drafts[node.taxonomy_node_id] = node.default_target_dimension as DefaultTargetDimension
    return drafts
  }, {})
}

function defaultTargetDraftsEqual(
  left: Record<string, DefaultTargetDimension>,
  right: Record<string, DefaultTargetDimension>,
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
  modalClassName,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  modalClassName?: string
}) {
  const dialogRef = useModalDialog(open, onClose)
  if (!open) {
    return null
  }

  return (
    <div className="taxonomy-modal-overlay" role="presentation" onClick={onClose}>
      <div
        ref={dialogRef}
        className={['taxonomy-modal', modalClassName].filter(Boolean).join(' ')}
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

const PLANNING_BUDGETING_LEVEL = 'weight_and_risk_budget'

function percentInputFromDecimal(value?: number | null) {
  if (value == null || !Number.isFinite(value)) {
    return ''
  }
  return String(Number((value * 100).toFixed(2)))
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

function coverageEntityKey(targetScope: TaxonomyAssignmentScope, entityId: string) {
  return `${targetScope}:${entityId}`
}

function targetMemberKey(targetMemberType: TargetMemberType, targetMemberId: string) {
  return `${targetMemberType}:${targetMemberId}`
}

function isSyntheticCashTargetMember(member: Pick<TargetScopeMember, 'target_member_type' | 'target_member_id' | 'system_role'>) {
  return member.system_role === 'cash' || (member.target_member_type === 'cash_bucket' && member.target_member_id === CASH_TARGET_MEMBER_ID)
}

function isSystemCashEntity(entity: CoverageEntity, taxonomy: PortfolioTaxonomyRecord | null | undefined) {
  return taxonomy?.primary_assignment_scope === 'instrument' && entity.target_scope === 'cash_bucket'
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
  defaultWeightEnabled: boolean
  defaultRiskBudgetEnabled: boolean
}) {
  const { targetSet, targetSetLines, targetMembers, defaultName, defaultWeightEnabled, defaultRiskBudgetEnabled } = args
  const linesByMemberKey = Object.fromEntries(
    targetMembers.map((member) => {
      const targetLine = targetSetLines.find(
        (item) =>
          targetMemberKey(item.target_member_type, item.target_member_id) === member.member_key ||
          (member.taxonomy_node_id != null && item.taxonomy_node_id === member.taxonomy_node_id),
      )
      return [
        member.member_key,
        {
          target_weight: isSyntheticCashTargetMember(member) && targetLine?.target_weight == null ? '0' : percentInputFromDecimal(targetLine?.target_weight),
          target_risk_share: isSyntheticCashTargetMember(member) ? '0' : percentInputFromDecimal(targetLine?.target_risk_share),
          notes: targetLine?.notes ?? '',
        },
      ]
    }),
  ) as Record<string, TargetLineDraft>

  return {
    name: targetSet?.name ?? defaultName,
    weight_enabled: targetDimensionEnabledForDraft(targetSet?.weight_enabled, defaultWeightEnabled),
    risk_budget_enabled: targetDimensionEnabledForDraft(targetSet?.risk_budget_enabled, defaultRiskBudgetEnabled),
    status: targetSet?.status ?? 'active',
    notes: targetSet?.notes ?? '',
    lines_by_member_key: linesByMemberKey,
  } satisfies TargetSetDraft
}

function targetLineDraftsEqual(left: TargetLineDraft, right: TargetLineDraft) {
  return (
    left.target_weight === right.target_weight &&
    left.target_risk_share === right.target_risk_share &&
    left.notes === right.notes
  )
}

function targetSetDraftsEqual(left: TargetSetDraft, right: TargetSetDraft) {
  const leftMemberKeys = Object.keys(left.lines_by_member_key)
  const rightMemberKeys = Object.keys(right.lines_by_member_key)
  return (
    left.name === right.name &&
    left.weight_enabled === right.weight_enabled &&
    left.risk_budget_enabled === right.risk_budget_enabled &&
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

function validateTargetSetDraft(
  draft: TargetSetDraft,
  targetMembers: TargetScopeMember[],
  allowedDimensions: { weight: boolean; risk_budget: boolean },
) {
  const errors: string[] = []
  const warnings: string[] = []
  let weightSum = 0
  let riskSum = 0
  let weightSeen = false
  let riskSeen = false

  if (!draft.weight_enabled && !draft.risk_budget_enabled) {
    errors.push('Enable at least one target dimension.')
  }
  if (draft.weight_enabled && !allowedDimensions.weight) {
    errors.push('Current taxonomy budgeting level does not allow weight targets.')
  }
  if (draft.risk_budget_enabled && !allowedDimensions.risk_budget) {
    errors.push('Current taxonomy budgeting level does not allow risk-budget targets.')
  }

  targetMembers.forEach((member) => {
    const lineDraft = draft.lines_by_member_key[member.member_key] ?? { target_weight: '', target_risk_share: '', notes: '' }
    if (draft.weight_enabled) {
      const parsedWeight = parsePercentInput(lineDraft.target_weight)
      if (parsedWeight == null) {
        errors.push(`Missing target weight for ${member.label}.`)
      } else if (!Number.isFinite(parsedWeight) || parsedWeight < 0) {
        errors.push(`Invalid target weight for ${member.label}.`)
      } else {
        weightSum += parsedWeight
        weightSeen = true
      }
    }
    if (draft.risk_budget_enabled) {
      const parsedRisk = parsePercentInput(lineDraft.target_risk_share)
      if (parsedRisk == null) {
        errors.push(`Missing target risk budget for ${member.label}.`)
      } else if (!Number.isFinite(parsedRisk) || parsedRisk < 0) {
        errors.push(`Invalid target risk budget for ${member.label}.`)
      } else if (isSyntheticCashTargetMember(member)) {
        if (Math.abs(parsedRisk) > 0.0005) {
          errors.push('Cash risk budget must be 0%.')
        }
      } else {
        riskSum += parsedRisk
        riskSeen = true
      }
    }
  })

  if (draft.weight_enabled && weightSeen && Math.abs(weightSum - 1) > 0.0005) {
    warnings.push('Target weight total is not 100% within the selected scope.')
  }
  if (draft.risk_budget_enabled && riskSeen && Math.abs(riskSum - 1) > 0.0005) {
    warnings.push('Non-cash target risk budget total is not 100% within the selected scope.')
  }

  return {
    savable: errors.length === 0,
    errors,
    warnings,
    weight_sum_pct: draft.weight_enabled && weightSeen ? weightSum * 100 : null,
    risk_sum_pct: draft.risk_budget_enabled && riskSeen ? riskSum * 100 : null,
  } satisfies TargetSetValidation
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
    getHoldingsWorkspace(portfolioId),
    getPortfolioAccountsWorkspace(portfolioId),
    getPortfolioInstruments(portfolioId),
  ])

  const supplementalMessages: string[] = []
  const catalog = catalogResult.status === 'fulfilled' ? catalogResult.value : null
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
    workspaceError: catalogResult.status === 'rejected' ? extractErrorMessage(catalogResult.reason) : null,
    supplementalNotice: supplementalMessages.length ? supplementalMessages.join(' ') : null,
  }
}

export default function TaxonomiesPage() {
  const { portfolioId = '' } = useParams()
  const currentPortfolioIdRef = useRef(portfolioId)
  const [searchParams, setSearchParams] = useSearchParams()
  const [catalog, setCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [holdingsWorkspace, setHoldingsWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [accountsResponse, setAccountsResponse] = useState<PortfolioAccountsWorkspaceResponse | null>(null)
  const [instrumentsResponse, setInstrumentsResponse] = useState<{ portfolio_id: string; instruments: SharedInstrumentRecord[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [supplementalNotice, setSupplementalNotice] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionPending, setActionPending] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<PendingTaxonomyDelete | null>(null)

  const [taxonomyName, setTaxonomyName] = useState('')
  const [taxonomyScope, setTaxonomyScope] = useState<TaxonomyAssignmentScope>('instrument')
  const [taxonomyPickerOpen, setTaxonomyPickerOpen] = useState(false)
  const [taxonomyRenameId, setTaxonomyRenameId] = useState<string | null>(null)
  const [taxonomyRenameName, setTaxonomyRenameName] = useState('')
  const taxonomyPickerRef = useRef<HTMLDivElement | null>(null)

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [collapsedNodeIds, setCollapsedNodeIds] = useState<Set<string>>(new Set())
  const [selectedEntityIds, setSelectedEntityIds] = useState<Set<string>>(new Set())
  const [entitySearch, setEntitySearch] = useState('')
  const [entityFilter, setEntityFilter] = useState<'all' | 'unassigned' | 'selected' | 'other' | 'ambiguous'>('all')
  const [instrumentAddSearch, setInstrumentAddSearch] = useState('')
  const [instrumentAddInstrumentId, setInstrumentAddInstrumentId] = useState('')

  const [newNodeName, setNewNodeName] = useState('')
  const [targetDraftsByScope, setTargetDraftsByScope] = useState<
    Record<string, { saa: TargetSetDraft; taa: TargetSetDraft }>
  >({})
  const [defaultTargetDraftsByNodeId, setDefaultTargetDraftsByNodeId] = useState<Record<string, DefaultTargetDimension>>({})
  const [activeTargetScopeKey, setActiveTargetScopeKey] = useState(ROOT_TARGET_SCOPE_KEY)
  const [targetEditMode, setTargetEditMode] = useState(false)
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
    setActionPending(null)
    setActionError(null)
    setNotice(null)
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

    let cancelled = false
    setLoading(true)

    fetchWorkspace(portfolioId)
      .then((result) => {
        if (cancelled) {
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
        if (!cancelled) {
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
    setLoading(true)
    try {
      const result = await fetchWorkspace(portfolioId)
      setCatalog(result.catalog)
      setHoldingsWorkspace(result.holdingsWorkspace)
      setAccountsResponse(result.accountsResponse)
      setInstrumentsResponse(result.instrumentsResponse)
      setWorkspaceError(result.workspaceError)
      setSupplementalNotice(result.supplementalNotice)
    } finally {
      setLoading(false)
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
  const defaultPlanningTaxonomyId =
    taxonomies.find((taxonomy) => taxonomy.taxonomy_id === catalog?.default_planning_taxonomy_id)?.taxonomy_id ?? ''
  const resolvedSelectedTaxonomyId =
    taxonomies.find((taxonomy) => taxonomy.taxonomy_id === requestedTaxonomyId)?.taxonomy_id ||
    defaultPlanningTaxonomyId ||
    taxonomies[0]?.taxonomy_id ||
    ''
  const selectedTaxonomy = taxonomies.find((taxonomy) => taxonomy.taxonomy_id === resolvedSelectedTaxonomyId) ?? null
  const selectedTaxonomyNodes = useMemo(
    () =>
      taxonomyNodes.filter(
        (node) =>
          node.taxonomy_id === resolvedSelectedTaxonomyId &&
          !(selectedTaxonomy?.primary_assignment_scope === 'instrument' && isCashTaxonomyNode(node)),
      ),
    [resolvedSelectedTaxonomyId, selectedTaxonomy?.primary_assignment_scope, taxonomyNodes],
  )
  const selectedTaxonomyAssignments = useMemo(
    () => taxonomyAssignments.filter((assignment) => assignment.taxonomy_id === resolvedSelectedTaxonomyId),
    [resolvedSelectedTaxonomyId, taxonomyAssignments],
  )
  const holdingsRows = holdingsWorkspace?.rows ?? []
  const instrumentRows = instrumentsResponse?.instruments ?? []
  const instrumentUniverseRows = catalog?.instrument_universe ?? []
  const baseCurrency = holdingsWorkspace?.base_currency ?? 'CNY'
  const accountRows = accountsResponse?.accounts ?? []

  useEffect(() => {
    const nextDrafts = defaultTargetDraftsFromNodes(selectedTaxonomyNodes)
    setDefaultTargetDraftsByNodeId((current) => {
      if (!targetEditMode) {
        return defaultTargetDraftsEqual(current, nextDrafts) ? current : nextDrafts
      }
      const mergedDrafts = selectedTaxonomyNodes.reduce<Record<string, DefaultTargetDimension>>((drafts, node) => {
        drafts[node.taxonomy_node_id] =
          current[node.taxonomy_node_id] ?? (node.default_target_dimension as DefaultTargetDimension)
        return drafts
      }, {})
      return defaultTargetDraftsEqual(current, mergedDrafts) ? current : mergedDrafts
    })
  }, [selectedTaxonomyNodes, targetEditMode])

  useEffect(() => {
    if (!taxonomies.length) {
      setShowTaxonomyCreate(true)
    }
  }, [taxonomies.length])

  useEffect(() => {
    setInstrumentAddSearch('')
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

  const descendantNodeIdsByNodeId = useMemo(() => {
    const lookup = new Map<string, Set<string>>()

    function walk(nodeId: string): Set<string> {
      const descendants = new Set<string>()
      ;(childrenByParent.get(nodeId) ?? []).forEach((child) => {
        descendants.add(child.taxonomy_node_id)
        const nested = walk(child.taxonomy_node_id)
        nested.forEach((nestedNodeId) => descendants.add(nestedNodeId))
      })
      lookup.set(nodeId, descendants)
      return descendants
    }

    selectedTaxonomyNodes.forEach((node) => {
      if (!lookup.has(node.taxonomy_node_id)) {
        walk(node.taxonomy_node_id)
      }
    })
    return lookup
  }, [childrenByParent, selectedTaxonomyNodes])

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
      const target = event.target as HTMLElement | null
      const tagName = target?.tagName?.toLowerCase()
      if (target?.isContentEditable || tagName === 'input' || tagName === 'textarea' || tagName === 'select') {
        return
      }
      if (event.key === 'Escape') {
        setContextMenuState(null)
        closeModalStack()
        return
      }
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        if (!targetEditMode && selectedNode?.is_terminal && selectedEntityIds.size) {
          event.preventDefault()
          void assignEntitiesToNode(selectedNode, Array.from(selectedEntityIds))
        }
        return
      }
      if (targetEditMode) {
        return
      }
      if (event.key === 'Tab' && selectedNode) {
        event.preventDefault()
        startNodeCreate('child', selectedNode)
        return
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        startNodeCreate(selectedNode ? 'sibling' : 'root', selectedNode)
      }
    }

    window.addEventListener('keydown', handleWindowKeydown)
    return () => window.removeEventListener('keydown', handleWindowKeydown)
  }, [selectedNode, selectedEntityIds, targetEditMode])

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

  const selectedNodeScopeIds = useMemo(() => {
    if (!selectedNode) {
      return null
    }
    const ids = new Set<string>([selectedNode.taxonomy_node_id])
    ;(descendantNodeIdsByNodeId.get(selectedNode.taxonomy_node_id) ?? new Set()).forEach((nodeId) => ids.add(nodeId))
    return ids
  }, [descendantNodeIdsByNodeId, selectedNode])

  const currentEntities = useMemo<CoverageEntity[]>(() => {
    if (!selectedTaxonomy) {
      return []
    }

    if (selectedTaxonomy.primary_assignment_scope === 'instrument') {
      const includeCashBuckets = true
      const visibleCashAccounts = accountRows.filter((accountRow) => {
        return accountRow.account.account_type === 'deposit_account'
      })
      const holdingRowsForEntities = includeCashBuckets
        ? holdingsRows.filter((row) => !isCashHoldingRow(row))
        : holdingsRows
      const totalEntityValueBase =
        holdingRowsForEntities.reduce((total, row) => total + (row.market_value_base ?? 0), 0) +
        (includeCashBuckets
          ? visibleCashAccounts.reduce((total, accountRow) => total + (accountRow.derived_cash_balance_base ?? 0), 0)
          : 0)

      const holdingEntities = holdingRowsForEntities.map((row) => {
        const assignments = activeAssignmentsByEntityKey.get(coverageEntityKey('instrument', row.instrument_core.instrument_id)) ?? []
        const assignment = assignments.length === 1 ? assignments[0] : null
        const currentNode = assignment ? nodeById.get(assignment.taxonomy_node_id) ?? null : null
        let coverageState: CoverageEntity['coverage_state'] = 'unassigned'
        if (assignments.length > 1) {
          coverageState = 'ambiguous'
        } else if (assignment && selectedNodeScopeIds?.has(assignment.taxonomy_node_id)) {
          coverageState = 'selected'
        } else if (assignment) {
          coverageState = 'other'
        }

        return {
          entity_id: row.instrument_core.instrument_id,
          target_scope: 'instrument' as const,
          label: `${primaryIdentifier(row.instrument_core)} · ${row.instrument_core.instrument_name}`,
          supporting_label: row.instrument_core.currency,
          allocation:
            row.market_value_base != null && totalEntityValueBase > 1e-9
              ? row.market_value_base / totalEntityValueBase
              : row.allocation ?? null,
          market_value_base: row.market_value_base ?? null,
          current_assignment: assignment,
          current_node: currentNode,
          holding_state: 'held',
          ...instrumentStateForEntity('held', universeRecordByInstrumentId.get(row.instrument_core.instrument_id)),
          coverage_state: coverageState,
        } satisfies CoverageEntity
      })

      const heldInstrumentIds = new Set(holdingRowsForEntities.map((row) => row.instrument_core.instrument_id))
      const visibleNonHeldInstrumentIds = new Set<string>()
      instrumentUniverseRows.forEach((item) => {
        if (
          item.status === 'active' &&
          item.instrument_id &&
          !heldInstrumentIds.has(item.instrument_id) &&
          !(includeCashBuckets && isCashInstrument(item.instrument_ref))
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
          !(includeCashBuckets && (assignment.target_entity_id.trim().toLowerCase().startsWith('cash:') || isCashInstrument(assignedInstrument)))
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
          let coverageState: CoverageEntity['coverage_state'] = 'unassigned'
          if (assignments.length > 1) {
            coverageState = 'ambiguous'
          } else if (currentAssignment && selectedNodeScopeIds?.has(currentAssignment.taxonomy_node_id)) {
            coverageState = 'selected'
          } else if (currentAssignment) {
            coverageState = 'other'
          }
          entities.push({
            entity_id: instrumentId,
            target_scope: 'instrument',
            label: instrument ? `${primaryIdentifier(instrument)} · ${instrument.instrument_name}` : instrumentId,
            supporting_label: instrument?.currency ?? '',
            allocation: null,
            market_value_base: null,
            current_assignment: currentAssignment,
            current_node: currentNode,
            holding_state: 'not_held',
            ...instrumentStateForEntity('not_held', universeRecord),
            coverage_state: coverageState,
          })
          return entities
        }, [])

      if (!includeCashBuckets) {
        return [...holdingEntities, ...nonHeldInstrumentEntities]
      }

      const cashEntities = visibleCashAccounts.map((accountRow) => {
        return {
          entity_id: accountRow.account.account_id,
          target_scope: 'cash_bucket' as const,
          label: accountRow.account.account_name,
          supporting_label: accountRow.account.currency,
          allocation:
            accountRow.derived_cash_balance_base != null && totalEntityValueBase > 1e-9
              ? accountRow.derived_cash_balance_base / totalEntityValueBase
              : null,
          market_value_base: accountRow.derived_cash_balance_base ?? null,
          current_assignment: null,
          current_node: null,
          holding_state: 'held',
          instrument_state: null,
          instrument_state_label: null,
          coverage_state: 'other',
        } satisfies CoverageEntity
      })

      return [...holdingEntities, ...nonHeldInstrumentEntities, ...cashEntities]
    }

    if (selectedTaxonomy.primary_assignment_scope === 'cash_bucket') {
      return accountRows
        .filter((accountRow) => accountRow.account.account_type === 'deposit_account')
        .map((accountRow): CoverageEntity => {
          const assignments =
            activeAssignmentsByEntityKey.get(coverageEntityKey('cash_bucket', accountRow.account.account_id)) ?? []
          const assignment = assignments.length === 1 ? assignments[0] : null
          const currentNode = assignment ? nodeById.get(assignment.taxonomy_node_id) ?? null : null
          let coverageState: CoverageEntity['coverage_state'] = 'unassigned'
          if (assignments.length > 1) {
            coverageState = 'ambiguous'
          } else if (assignment && selectedNodeScopeIds?.has(assignment.taxonomy_node_id)) {
            coverageState = 'selected'
          } else if (assignment) {
            coverageState = 'other'
          }

          return {
            entity_id: accountRow.account.account_id,
            target_scope: 'cash_bucket',
            label: `${accountRow.account.account_name} · Cash`,
            supporting_label: accountRow.account.currency,
            allocation: null,
            market_value_base: accountRow.derived_cash_balance_base ?? null,
            current_assignment: assignment,
            current_node: currentNode,
            holding_state: 'held',
            instrument_state: null,
            instrument_state_label: null,
            coverage_state: coverageState,
          }
        })
    }

    return accountRows.map((accountRow): CoverageEntity => {
      const assignments =
        activeAssignmentsByEntityKey.get(coverageEntityKey('account', accountRow.account.account_id)) ?? []
      const assignment = assignments.length === 1 ? assignments[0] : null
      const currentNode = assignment ? nodeById.get(assignment.taxonomy_node_id) ?? null : null
      let coverageState: CoverageEntity['coverage_state'] = 'unassigned'
      if (assignments.length > 1) {
        coverageState = 'ambiguous'
      } else if (assignment && selectedNodeScopeIds?.has(assignment.taxonomy_node_id)) {
        coverageState = 'selected'
      } else if (assignment) {
        coverageState = 'other'
      }

      return {
        entity_id: accountRow.account.account_id,
        target_scope: 'account',
        label: accountRow.account.account_name,
        supporting_label: `${accountRow.account.currency} · ${formatLabel(accountRow.account.account_type)}`,
        allocation: null,
        market_value_base: null,
        current_assignment: assignment,
        current_node: currentNode,
        holding_state: 'held',
        instrument_state: null,
        instrument_state_label: null,
        coverage_state: coverageState,
      }
    })
  }, [
    accountRows,
    activeAssignments,
    activeAssignmentsByEntityKey,
    baseCurrency,
    holdingsRows,
    instrumentById,
    instrumentUniverseRows,
    nodeById,
    selectedNodeScopeIds,
    selectedTaxonomy,
    universeInstrumentById,
    universeRecordByInstrumentId,
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

    const lookup = new Map<string, NodeAggregate>()

    function walk(nodeId: string): NodeAggregate {
      const directEntities = directEntitiesByNodeId.get(nodeId) ?? []
      let currentEntityCount = directEntities.length
      let currentWeightTotal = 0
      let currentWeightSeen = false
      let currentValueBaseTotal = 0
      let currentValueSeen = false

      directEntities.forEach((entity) => {
        if (entity.allocation != null) {
          currentWeightTotal += entity.allocation
          currentWeightSeen = true
        }
        if (entity.market_value_base != null) {
          currentValueBaseTotal += entity.market_value_base
          currentValueSeen = true
        }
      })

      ;(childrenByParent.get(nodeId) ?? []).forEach((child) => {
        const childAggregate = walk(child.taxonomy_node_id)
        currentEntityCount += childAggregate.current_entity_count
        if (childAggregate.current_weight != null) {
          currentWeightTotal += childAggregate.current_weight
          currentWeightSeen = true
        }
        if (childAggregate.current_value_base != null) {
          currentValueBaseTotal += childAggregate.current_value_base
          currentValueSeen = true
        }
      })

      const aggregate = {
        current_entity_count: currentEntityCount,
        current_weight: currentWeightSeen ? currentWeightTotal : null,
        current_value_base: currentValueSeen ? currentValueBaseTotal : null,
        direct_assignment_count: (directAssignmentsByNodeId.get(nodeId) ?? []).length,
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

  const showSystemCashNode = selectedTaxonomy?.primary_assignment_scope === 'instrument'
  const cashBucketEntities = useMemo(
    () => currentEntities.filter((entity) => entity.target_scope === 'cash_bucket'),
    [currentEntities],
  )
  const cashAggregate = useMemo(() => {
    let currentWeightTotal = 0
    let currentWeightSeen = false
    let currentValueBaseTotal = 0
    let currentValueSeen = false

    cashBucketEntities.forEach((entity) => {
      if (entity.allocation != null) {
        currentWeightTotal += entity.allocation
        currentWeightSeen = true
      }
      if (entity.market_value_base != null) {
        currentValueBaseTotal += entity.market_value_base
        currentValueSeen = true
      }
    })

    return {
      current_entity_count: cashBucketEntities.length,
      current_weight: currentWeightSeen ? currentWeightTotal : null,
      current_value_base: currentValueSeen ? currentValueBaseTotal : null,
      direct_assignment_count: cashBucketEntities.length,
    } satisfies NodeAggregate
  }, [cashBucketEntities])

  const taxonomyCurrentSummary = useMemo(() => {
    let weightTotal = 0
    let valueTotal = 0
    let weightSeen = false
    let valueSeen = false

    currentEntities.forEach((entity) => {
      if (entity.allocation != null) {
        weightTotal += entity.allocation
        weightSeen = true
      }
      if (entity.market_value_base != null) {
        valueTotal += entity.market_value_base
        valueSeen = true
      }
    })

    return {
      current_weight: weightSeen ? weightTotal : null,
      current_value_base: valueSeen ? valueTotal : null,
    }
  }, [currentEntities])

  const unassignedSummary = useMemo(() => {
    let weightTotal = 0
    let valueTotal = 0
    let weightSeen = false
    let valueSeen = false

    coverageSummary.unassignedEntities.forEach((entity) => {
      if (entity.allocation != null) {
        weightTotal += entity.allocation
        weightSeen = true
      }
      if (entity.market_value_base != null) {
        valueTotal += entity.market_value_base
        valueSeen = true
      }
    })

    return {
      current_weight: weightSeen ? weightTotal : null,
      current_value_base: valueSeen ? valueTotal : null,
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

  const filteredEntities = useMemo(() => {
    const normalizedSearch = entitySearch.trim().toLowerCase()
    return currentEntities.filter((entity) => {
      if (entityFilter !== 'all' && entity.coverage_state !== entityFilter) {
        return false
      }
      if (!normalizedSearch) {
        return true
      }
      return (
        entity.label.toLowerCase().includes(normalizedSearch) ||
        entity.supporting_label.toLowerCase().includes(normalizedSearch) ||
        (entity.current_node?.node_name ?? '').toLowerCase().includes(normalizedSearch)
      )
    })
  }, [currentEntities, entityFilter, entitySearch])
  const currentInstrumentEntityIds = useMemo(
    () =>
      new Set(
        currentEntities
          .filter((entity) => entity.target_scope === 'instrument')
          .map((entity) => entity.entity_id),
      ),
    [currentEntities],
  )
  const registryInstrumentOptions = useMemo(() => {
    if (selectedTaxonomy?.primary_assignment_scope !== 'instrument') {
      return []
    }
    const normalizedSearch = instrumentAddSearch.trim().toLowerCase()
    const options = instrumentRows
      .filter((instrument) => {
        if (currentInstrumentEntityIds.has(instrument.instrument_id)) {
          return false
        }
        if (activeAssignmentsByEntityKey.has(coverageEntityKey('instrument', instrument.instrument_id))) {
          return false
        }
        if (!normalizedSearch) {
          return true
        }
        return (
          instrument.instrument_id.toLowerCase().includes(normalizedSearch) ||
          instrument.instrument_name.toLowerCase().includes(normalizedSearch) ||
          instrument.currency.toLowerCase().includes(normalizedSearch) ||
          primaryIdentifier(instrument).toLowerCase().includes(normalizedSearch)
        )
      })
      .sort(
        (left, right) =>
          left.instrument_name.localeCompare(right.instrument_name) ||
          primaryIdentifier(left).localeCompare(primaryIdentifier(right)) ||
          left.instrument_id.localeCompare(right.instrument_id),
      )
      .slice(0, 80)
    const selectedInstrument = instrumentAddInstrumentId ? instrumentById.get(instrumentAddInstrumentId) ?? null : null
    if (selectedInstrument && !options.some((instrument) => instrument.instrument_id === selectedInstrument.instrument_id)) {
      return [selectedInstrument, ...options]
    }
    return options
  }, [
    activeAssignmentsByEntityKey,
    currentInstrumentEntityIds,
    instrumentAddInstrumentId,
    instrumentAddSearch,
    instrumentById,
    instrumentRows,
    selectedTaxonomy?.primary_assignment_scope,
  ])
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
  const allowedTargetDimensions = useMemo(
    () => ({
      weight: Boolean(selectedTaxonomy?.planning_enabled),
      risk_budget: Boolean(selectedTaxonomy?.planning_enabled),
    }),
    [selectedTaxonomy?.planning_enabled],
  )
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

      const directEntities = directEntitiesByNodeId.get(node.taxonomy_node_id) ?? []
      if (!directEntities.length) {
        return
      }
      lookup.set(
        targetScopeKey(node.taxonomy_node_id),
        directEntities.map((entity) => ({
          member_key: targetMemberKey(entity.target_scope, entity.entity_id),
          target_member_type: entity.target_scope,
          target_member_id: entity.entity_id,
          taxonomy_node_id: null,
          node: null,
          entity,
          label: entity.label,
        })),
      )
    })

    return lookup
  }, [childrenByParent, directEntitiesByNodeId, selectedTaxonomyNodes, showSystemCashNode])
  const selectedComparatorScopeNode =
    selectedNode && scopeMembersByScopeKey.has(targetScopeKey(selectedNode.taxonomy_node_id))
      ? selectedNode
      : selectedNode?.parent_taxonomy_node_id
        ? (nodeById.get(selectedNode.parent_taxonomy_node_id) ?? null)
        : null
  const activeTargetLinesByMode = useMemo(() => {
    const lookup = {
      saa: new Map<string, PortfolioTargetSetLineRecord>(),
      taa: new Map<string, PortfolioTargetSetLineRecord>(),
    }
    selectedTaxonomyActiveTargetSets.forEach((targetSet) => {
      const lineLookup = targetSet.target_set_type === 'saa' ? lookup.saa : lookup.taa
      ;(targetSetLinesByTargetSetId.get(targetSet.target_set_id) ?? []).forEach((line) => {
        lineLookup.set(targetMemberKey(line.target_member_type, line.target_member_id), line)
      })
    })
    return lookup
  }, [selectedTaxonomyActiveTargetSets, targetSetLinesByTargetSetId])

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

  useEffect(() => {
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
          defaultWeightEnabled: allowedTargetDimensions.weight,
          defaultRiskBudgetEnabled: allowedTargetDimensions.risk_budget,
        }),
        taa: buildTargetSetDraft({
          targetSet: taaTargetSet,
          targetSetLines: targetSetLinesByTargetSetId.get(taaTargetSet?.target_set_id ?? '') ?? [],
          targetMembers,
          defaultName: `${selectedTaxonomy?.name ?? 'Planning'} ${scopePathLabel} TAA`,
          defaultWeightEnabled: allowedTargetDimensions.weight,
          defaultRiskBudgetEnabled: allowedTargetDimensions.risk_budget,
        }),
      }
    })

    setTargetDraftsByScope((current) =>
      targetDraftScopesEqual(current, nextDraftsByScope) ? current : nextDraftsByScope,
    )
  }, [
    allowedTargetDimensions.risk_budget,
    allowedTargetDimensions.weight,
    nodePathByNodeId,
    nodeById,
    scopeMembersByScopeKey,
    selectedTaxonomy,
    selectedTaxonomyActiveTargetSets,
    targetSetLinesByTargetSetId,
  ])

  useEffect(() => {
    const preferredScopeNode = selectedComparatorScopeNode?.taxonomy_node_id ?? null
    const preferredScopeKey = targetScopeKey(preferredScopeNode)
    setActiveTargetScopeKey((current) => {
      if (targetDraftsByScope[current]) {
        return current
      }
      if (targetDraftsByScope[preferredScopeKey]) {
        return preferredScopeKey
      }
      const firstScopeKey = Object.keys(targetDraftsByScope)[0] ?? ROOT_TARGET_SCOPE_KEY
      return firstScopeKey
    })
  }, [selectedComparatorScopeNode, targetDraftsByScope])

  const activeComparatorScopeNodeId = targetScopeNodeId(activeTargetScopeKey)
  const activeComparatorScopeNode = activeComparatorScopeNodeId
    ? nodeById.get(activeComparatorScopeNodeId) ?? null
    : null
  const currentScopeMembers = useMemo(
    () => scopeMembersByScopeKey.get(activeTargetScopeKey) ?? [],
    [activeTargetScopeKey, scopeMembersByScopeKey],
  )
  const currentScopeLabel = activeComparatorScopeNode
    ? currentScopeMembers.some((member) => member.entity)
      ? `${activeComparatorScopeNode.node_name} Instruments`
      : `${activeComparatorScopeNode.node_name} Children`
    : 'Top Level Children'
  const currentScopeTargetSets = useMemo(
    () =>
      selectedTaxonomyActiveTargetSets.filter(
        (targetSet) => (targetSet.comparator_taxonomy_node_id ?? null) === activeComparatorScopeNodeId,
      ),
    [activeComparatorScopeNodeId, selectedTaxonomyActiveTargetSets],
  )
  const activeSaaTargetSet = currentScopeTargetSets.find((targetSet) => targetSet.target_set_type === 'saa') ?? null
  const activeTaaTargetSet = currentScopeTargetSets.find((targetSet) => targetSet.target_set_type === 'taa') ?? null
  const currentScopeDrafts = targetDraftsByScope[activeTargetScopeKey] ?? {
    saa: EMPTY_TARGET_SET_DRAFT,
    taa: EMPTY_TARGET_SET_DRAFT,
  }
  const saaDraft = currentScopeDrafts.saa
  const taaDraft = currentScopeDrafts.taa
  const hasTargetScope = currentScopeMembers.length > 0
  const activeScopePathLabel = activeComparatorScopeNode
    ? (nodePathByNodeId.get(activeComparatorScopeNode.taxonomy_node_id) ?? []).map((node) => node.node_name).join(' / ')
    : 'Top Level'
  const activeBaselineDrafts = useMemo(
    () => ({
      saa: buildTargetSetDraft({
        targetSet: activeSaaTargetSet,
        targetSetLines: targetSetLinesByTargetSetId.get(activeSaaTargetSet?.target_set_id ?? '') ?? [],
        targetMembers: currentScopeMembers,
        defaultName: `${selectedTaxonomy?.name ?? 'Planning'} ${activeScopePathLabel} SAA`,
        defaultWeightEnabled: allowedTargetDimensions.weight,
        defaultRiskBudgetEnabled: allowedTargetDimensions.risk_budget,
      }),
      taa: buildTargetSetDraft({
        targetSet: activeTaaTargetSet,
        targetSetLines: targetSetLinesByTargetSetId.get(activeTaaTargetSet?.target_set_id ?? '') ?? [],
        targetMembers: currentScopeMembers,
        defaultName: `${selectedTaxonomy?.name ?? 'Planning'} ${activeScopePathLabel} TAA`,
        defaultWeightEnabled: allowedTargetDimensions.weight,
        defaultRiskBudgetEnabled: allowedTargetDimensions.risk_budget,
      }),
    }),
    [
      activeSaaTargetSet,
      activeScopePathLabel,
      activeTaaTargetSet,
      allowedTargetDimensions.risk_budget,
      allowedTargetDimensions.weight,
      currentScopeMembers,
      selectedTaxonomy?.name,
      targetSetLinesByTargetSetId,
    ],
  )
  const saaDraftChanged = !targetSetDraftsEqual(saaDraft, activeBaselineDrafts.saa)
  const taaDraftChanged = !targetSetDraftsEqual(taaDraft, activeBaselineDrafts.taa)
  const saaValidation = useMemo(
    () => validateTargetSetDraft(saaDraft, currentScopeMembers, allowedTargetDimensions),
    [allowedTargetDimensions, currentScopeMembers, saaDraft],
  )
  const taaValidation = useMemo(
    () => validateTargetSetDraft(taaDraft, currentScopeMembers, allowedTargetDimensions),
    [allowedTargetDimensions, currentScopeMembers, taaDraft],
  )
  const currentScopeMemberKeySet = useMemo(
    () => new Set(currentScopeMembers.map((member) => member.member_key)),
    [currentScopeMembers],
  )
  const changedDefaultTargetNodes = useMemo(
    () =>
      selectedTaxonomyNodes.filter((node) => {
        const draftValue = defaultTargetDraftsByNodeId[node.taxonomy_node_id]
        return Boolean(draftValue) && draftValue !== node.default_target_dimension
      }),
    [defaultTargetDraftsByNodeId, selectedTaxonomyNodes],
  )
  const hasDefaultTargetChanges = changedDefaultTargetNodes.length > 0
  const targetSaveBlockedReason = [
    hasTargetScope && saaDraftChanged ? targetValidationMessage('saa', saaValidation) : '',
    hasTargetScope && taaDraftChanged ? targetValidationMessage('taa', taaValidation) : '',
  ]
    .filter(Boolean)
    .join(' ')
  const hasTargetsConfigurationChanges = hasDefaultTargetChanges || (hasTargetScope && (saaDraftChanged || taaDraftChanged))
  const canSaveTargetsConfiguration = hasTargetsConfigurationChanges && !targetSaveBlockedReason

  function preventTargetEditorDrag(event: ReactDragEvent<HTMLInputElement | HTMLSelectElement>) {
    event.preventDefault()
    event.stopPropagation()
  }

  function renderDefaultTargetCell(node: PortfolioTaxonomyNodeRecord) {
    const draftValue = defaultTargetDraftsByNodeId[node.taxonomy_node_id] ?? (node.default_target_dimension as DefaultTargetDimension)
    if (!targetEditMode || !selectedTaxonomy?.planning_enabled) {
      return <span className="taxonomy-default-target-label">{draftValue === 'risk_budget' ? 'Risk Budget' : 'Weight'}</span>
    }
    return (
      <select
        className="taxonomy-default-target-select"
        value={draftValue}
        draggable={false}
        onDragStart={preventTargetEditorDrag}
        onChange={(event) =>
          setDefaultTargetDraftsByNodeId((current) => ({
            ...current,
            [node.taxonomy_node_id]: event.target.value as DefaultTargetDimension,
          }))
        }
        disabled={Boolean(actionPending)}
      >
        <option value="weight">Weight</option>
        <option value="risk_budget">Risk Budget</option>
      </select>
    )
  }

  function renderFixedWeightDefaultTargetCell() {
    return <span className="taxonomy-default-target-label">Weight</span>
  }

  function renderTargetCell(
    kind: 'saa' | 'taa',
    dimension: 'weight' | 'risk_budget',
    targetMember: TargetScopeMember,
    editable: boolean,
  ) {
    const scopeKey =
      targetMember.target_member_type === 'taxonomy_node'
        ? targetScopeKey(targetMember.node?.parent_taxonomy_node_id ?? null)
        : targetScopeKey(targetMember.entity?.current_assignment?.taxonomy_node_id ?? null)
    const draft = targetDraftsByScope[scopeKey]?.[kind] ?? EMPTY_TARGET_SET_DRAFT
    if (isSyntheticCashTargetMember(targetMember) && dimension === 'risk_budget') {
      return <span className="taxonomy-fixed-target-value">0.00%</span>
    }
    const lineDraft = draft.lines_by_member_key[targetMember.member_key] ?? {
      target_weight: '',
      target_risk_share: '',
      notes: '',
    }
    if (editable) {
      if (dimension === 'weight') {
        if (!draft.weight_enabled) {
          return '—'
        }
        const targetWeightMissing = !lineDraft.target_weight.trim()
        return (
          <input
            className={targetWeightMissing ? 'taxonomy-target-input-missing' : undefined}
            type="number"
            step="0.01"
            min="0"
            value={lineDraft.target_weight}
            placeholder="Required"
            aria-label={`${kind.toUpperCase()} target weight for ${targetMember.label}`}
            aria-invalid={targetWeightMissing}
            draggable={false}
            onDragStart={preventTargetEditorDrag}
            onFocus={() => setActiveTargetScopeKey(scopeKey)}
            onChange={(event) =>
              updateDraftLine(scopeKey, kind, targetMember.member_key, 'target_weight', event.target.value)
            }
          />
        )
      }
      if (!draft.risk_budget_enabled) {
        return '—'
      }
      const targetRiskMissing = !lineDraft.target_risk_share.trim()
      return (
        <input
          className={targetRiskMissing ? 'taxonomy-target-input-missing' : undefined}
          type="number"
          step="0.01"
          min="0"
          value={lineDraft.target_risk_share}
          placeholder="Required"
          aria-label={`${kind.toUpperCase()} target risk budget for ${targetMember.label}`}
          aria-invalid={targetRiskMissing}
          draggable={false}
          onDragStart={preventTargetEditorDrag}
          onFocus={() => setActiveTargetScopeKey(scopeKey)}
          onChange={(event) =>
            updateDraftLine(scopeKey, kind, targetMember.member_key, 'target_risk_share', event.target.value)
          }
        />
      )
    }

    const activeLine = activeTargetLinesByMode[kind].get(targetMember.member_key)
    if (dimension === 'weight') {
      return activeLine?.target_weight != null ? formatPercent(activeLine.target_weight) : '—'
    }
    return activeLine?.target_risk_share != null ? formatPercent(activeLine.target_risk_share) : '—'
  }

  function resetNodeCreateDraft() {
    setNewNodeName('')
  }

  function resetNodeEditDraft() {
    setNodeEditId(null)
    setNodeEditName('')
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
    setEntityFilter('all')
    setEntitySearch('')
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

  function handleNodeContextMenu(event: ReactMouseEvent, node: PortfolioTaxonomyNodeRecord) {
    event.preventDefault()
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
    if (isSystemCashEntity(entity, selectedTaxonomy)) {
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
    if (entity && isSystemCashEntity(entity, selectedTaxonomy)) {
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
      lockedEntity: isSystemCashEntity(entity, selectedTaxonomy),
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
                  target_weight: '',
                  target_risk_share: '',
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

  function buildTargetSetPayload(draft: TargetSetDraft, targetMembers: TargetScopeMember[] = currentScopeMembers) {
    return {
      name: draft.name.trim(),
      weight_enabled: draft.weight_enabled,
      risk_budget_enabled: draft.risk_budget_enabled,
      status: draft.status,
      notes: draft.notes || null,
      lines: targetMembers.map((member) => {
        const lineDraft = draft.lines_by_member_key[member.member_key] ?? {
          target_weight: '',
          target_risk_share: isSyntheticCashTargetMember(member) ? '0' : '',
          notes: '',
        }
        const targetWeight = parsePercentInput(lineDraft.target_weight)
        const targetRiskShare = parsePercentInput(lineDraft.target_risk_share)
        return {
          target_member_type: member.target_member_type,
          target_member_id: member.target_member_id,
          taxonomy_node_id: member.taxonomy_node_id,
          target_weight: draft.weight_enabled ? (targetWeight == null ? null : targetWeight) : null,
          target_risk_share: draft.risk_budget_enabled ? (targetRiskShare == null ? null : targetRiskShare) : null,
          notes: lineDraft.notes || null,
        }
      }),
    }
  }

  function targetValidationMessage(kind: 'saa' | 'taa', validation: TargetSetValidation) {
    return [...validation.errors, ...validation.warnings]
      .map((message) => `${kind.toUpperCase()} ${currentScopeLabel}: ${message}`)
      .join(' ')
  }

  async function handleSaveTargetsConfiguration() {
    if (!portfolioId || !selectedTaxonomy) {
      return
    }

    const targetKindsToSave: Array<'saa' | 'taa'> = []
    if (hasTargetScope && saaDraftChanged) {
      const message = targetValidationMessage('saa', saaValidation)
      if (message) {
        setActionError(null)
        setNotice(null)
        return
      }
      targetKindsToSave.push('saa')
    }
    if (hasTargetScope && taaDraftChanged) {
      const message = targetValidationMessage('taa', taaValidation)
      if (message) {
        setActionError(null)
        setNotice(null)
        return
      }
      targetKindsToSave.push('taa')
    }

    if (!hasDefaultTargetChanges && !targetKindsToSave.length) {
      setActionError(null)
      setNotice('No changes.')
      return
    }

    setActionPending('targets-save')
    setActionError(null)
    setNotice(null)
    try {
      if (
        targetKindsToSave.length &&
        selectedTaxonomy.planning_enabled &&
        selectedTaxonomy.budgeting_level !== PLANNING_BUDGETING_LEVEL
      ) {
        await updatePortfolioTaxonomy(portfolioId, selectedTaxonomy.taxonomy_id, {
          budgeting_level: PLANNING_BUDGETING_LEVEL,
        })
      }

      for (const node of changedDefaultTargetNodes) {
        const draftValue = defaultTargetDraftsByNodeId[node.taxonomy_node_id]
        if (!draftValue) {
          continue
        }
        await updatePortfolioTaxonomyNode(portfolioId, selectedTaxonomy.taxonomy_id, node.taxonomy_node_id, {
          default_target_dimension: draftValue,
        })
      }

      for (const kind of targetKindsToSave) {
        const draft = kind === 'saa' ? saaDraft : taaDraft
        const existingTargetSet = kind === 'saa' ? activeSaaTargetSet : activeTaaTargetSet
        const payload = buildTargetSetPayload(draft)
        if (existingTargetSet) {
          await updatePortfolioTargetSet(portfolioId, selectedTaxonomy.taxonomy_id, existingTargetSet.target_set_id, payload)
        } else {
          await createPortfolioTargetSet(portfolioId, selectedTaxonomy.taxonomy_id, {
            comparator_taxonomy_node_id: activeComparatorScopeNodeId,
            target_set_type: kind,
            ...payload,
          })
        }
      }

      const savedParts = [
        changedDefaultTargetNodes.length ? `default targets (${changedDefaultTargetNodes.length})` : '',
        ...targetKindsToSave.map((kind) => `${kind.toUpperCase()} ${currentScopeLabel}`),
      ].filter(Boolean)
      setTargetEditMode(false)
      setNotice(`Saved ${savedParts.join(', ')}.`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  function handleDeleteTargetSet(kind: 'saa' | 'taa') {
    if (!portfolioId || !selectedTaxonomy) {
      return
    }
    const existingTargetSet = kind === 'saa' ? activeSaaTargetSet : activeTaaTargetSet
    if (!existingTargetSet) {
      return
    }
    setActionError(null)
    setNotice(null)
    setPendingDelete({
      kind: 'target-set',
      portfolioId,
      targetKind: kind,
      taxonomyId: selectedTaxonomy.taxonomy_id,
      targetSet: existingTargetSet,
      scopeLabel: currentScopeLabel,
    })
  }

  async function handleCreateTaxonomy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!portfolioId) {
      return
    }
    setActionPending('taxonomy-create')
    setActionError(null)
    setNotice(null)
    try {
      const created = await createPortfolioTaxonomy(portfolioId, {
        name: taxonomyName,
        taxonomy_type: 'custom',
        purpose: null,
        primary_assignment_scope: taxonomyScope,
        planning_enabled: false,
        budgeting_level: null,
        root_default_target_dimension: 'weight',
      })
      setTaxonomyName('')
      setTaxonomyScope('instrument')
      setShowTaxonomyCreate(false)
      handleTaxonomySelection(created.taxonomy_id)
      setNotice(`Created taxonomy "${created.name}".`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleRenameTaxonomy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const taxonomy = taxonomyRenameId ? taxonomies.find((item) => item.taxonomy_id === taxonomyRenameId) ?? null : null
    const nextName = taxonomyRenameName.trim()
    if (!portfolioId || !taxonomy || !nextName) {
      return
    }
    setActionPending(`taxonomy-rename-${taxonomy.taxonomy_id}`)
    setActionError(null)
    setNotice(null)
    try {
      await updatePortfolioTaxonomy(portfolioId, taxonomy.taxonomy_id, { name: nextName })
      setShowTaxonomyRename(false)
      setTaxonomyRenameId(null)
      setTaxonomyRenameName('')
      setNotice(`Renamed taxonomy to "${nextName}".`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleDefaultTaxonomySelection(taxonomyId: string) {
    const taxonomy = taxonomies.find((item) => item.taxonomy_id === taxonomyId) ?? null
    setTaxonomyPickerOpen(false)
    handleTaxonomySelection(taxonomyId)
    if (!portfolioId || !taxonomy) {
      return
    }
    if (taxonomy.primary_assignment_scope !== 'instrument') {
      setActionError('Only instrument taxonomies can be used as the default taxonomy for planning.')
      setNotice(null)
      return
    }
    setActionPending(`default-taxonomy-${taxonomyId}`)
    setActionError(null)
    setNotice(null)
    try {
      if (!taxonomy.planning_enabled || taxonomy.budgeting_level !== PLANNING_BUDGETING_LEVEL) {
        await updatePortfolioTaxonomy(portfolioId, taxonomy.taxonomy_id, {
          planning_enabled: true,
          budgeting_level: PLANNING_BUDGETING_LEVEL,
        })
      }
      await updatePortfolioDefaultPlanningTaxonomy(portfolioId, { taxonomy_id: taxonomyId })
      setNotice(`Default taxonomy set to "${taxonomy.name}".`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
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
    if (!portfolioId || !selectedTaxonomy) {
      return
    }
    setActionPending('node-create')
    setActionError(null)
    setNotice(null)
    try {
      const created = await createPortfolioTaxonomyNode(portfolioId, selectedTaxonomy.taxonomy_id, {
        node_name: newNodeName,
        parent_taxonomy_node_id: nodeCreateParentId || null,
      })
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
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleSaveNodeEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!portfolioId || !selectedTaxonomy || !editingNode) {
      return
    }
    setActionPending(`node-save-${editingNode.taxonomy_node_id}`)
    setActionError(null)
    setNotice(null)
    try {
      await updatePortfolioTaxonomyNode(portfolioId, selectedTaxonomy.taxonomy_id, editingNode.taxonomy_node_id, {
        node_name: nodeEditName,
      })
      setShowNodeEdit(false)
      setNotice(`Updated node "${nodeEditName}".`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
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
    if (targetEditMode) {
      setContextMenuState(null)
      setActionError(TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE)
      return
    }
    if (!portfolioId || !selectedTaxonomy || !targetNode.is_terminal || !entityIds.length) {
      return
    }
    setActionPending('assignment-bulk-save')
    setActionError(null)
    setNotice(null)
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
        if (isSystemCashEntity(entity, selectedTaxonomy)) {
          unchangedCount += 1
          continue
        }
        if (entity.coverage_state === 'ambiguous') {
          skippedAmbiguous += 1
          continue
        }
        if (!entity.current_assignment) {
          await createPortfolioTaxonomyAssignment(portfolioId, selectedTaxonomy.taxonomy_id, {
            target_scope: entity.target_scope,
            target_entity_id: entity.entity_id,
            taxonomy_node_id: targetNode.taxonomy_node_id,
          })
          createdCount += 1
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
      }

      setSelectedEntityIds(new Set())
      setNotice(
        `Assignments updated for "${targetNode.node_name}": ${createdCount} created, ${movedCount} moved, ${unchangedCount} unchanged${
          skippedAmbiguous ? `, ${skippedAmbiguous} ambiguous skipped` : ''
        }.`,
      )
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleAddRegistryInstrumentToUniverse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!portfolioId || !selectedTaxonomy || selectedTaxonomy.primary_assignment_scope !== 'instrument') {
      return
    }
    const instrument = instrumentAddInstrumentId ? instrumentById.get(instrumentAddInstrumentId) ?? null : null
    if (!instrument) {
      setActionError('Choose an instrument.')
      setNotice(null)
      return
    }

    setActionPending('instrument-add')
    setActionError(null)
    setNotice(null)
    try {
      await createPortfolioInstrumentUniverseRecord(portfolioId, {
        instrument_id: instrument.instrument_id,
      })
      setInstrumentAddSearch('')
      setInstrumentAddInstrumentId('')
      setShowInstrumentAdd(false)
      setNotice(`Added ${instrument.instrument_name} to unassigned instruments.`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  function handleDeleteObservedInstrument(entity: CoverageEntity) {
    if (!portfolioId || entity.target_scope !== 'instrument' || entity.instrument_state !== 'observe') {
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
    const target = pendingDelete
    if (!target || actionPending) {
      return
    }

    const actionKey =
      target.kind === 'target-set'
        ? `target-${target.targetKind}-delete`
        : target.kind === 'taxonomy'
          ? `taxonomy-delete-${target.taxonomy.taxonomy_id}`
          : target.kind === 'node'
            ? `node-delete-${target.node.taxonomy_node_id}`
            : `instrument-observe-delete-${target.entity.entity_id}`
    setActionPending(actionKey)
    setActionError(null)
    setNotice(null)

    try {
      let successNotice = ''
      if (target.kind === 'target-set') {
        await deletePortfolioTargetSet(
          target.portfolioId,
          target.taxonomyId,
          target.targetSet.target_set_id,
        )
        successNotice = `Deleted ${target.targetKind.toUpperCase()} targets for ${target.scopeLabel}.`
      } else if (target.kind === 'taxonomy') {
        await deletePortfolioTaxonomy(target.portfolioId, target.taxonomy.taxonomy_id)
        if (target.wasSelected && currentPortfolioIdRef.current === target.portfolioId) {
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
        if (currentPortfolioIdRef.current === target.portfolioId) {
          setSelectedNodeId(target.node.parent_taxonomy_node_id ?? null)
        }
        successNotice = `Deleted node "${target.node.node_name}".`
      } else {
        await deletePortfolioInstrumentUniverseRecord(target.portfolioId, target.entity.entity_id)
        if (currentPortfolioIdRef.current === target.portfolioId) {
          setSelectedEntityIds((current) => {
            const next = new Set(current)
            next.delete(target.entity.entity_id)
            return next
          })
        }
        successNotice = `Removed observed instrument "${target.entity.label}".`
      }

      setPendingDelete(null)
      if (currentPortfolioIdRef.current === target.portfolioId) {
        setNotice(successNotice)
        await reloadWorkspace()
      }
    } catch (error) {
      if (currentPortfolioIdRef.current === target.portfolioId) {
        setActionError(extractErrorMessage(error))
      }
    } finally {
      if (currentPortfolioIdRef.current === target.portfolioId) {
        setActionPending(null)
      }
    }
  }

  function renderEntityTreeRow(entity: CoverageEntity, depth: number) {
    const lockedCashEntity = isSystemCashEntity(entity, selectedTaxonomy)
    const assignmentDragEnabled = canDragTaxonomyEntity({
      targetEditMode,
      lockedEntity: lockedCashEntity,
      ambiguousEntity: entity.coverage_state === 'ambiguous',
      actionPending: Boolean(actionPending),
    })
    const selected = !lockedCashEntity && selectedEntityIds.has(entity.entity_id)
    const targetMember: TargetScopeMember = {
      member_key: targetMemberKey(entity.target_scope, entity.entity_id),
      target_member_type: entity.target_scope,
      target_member_id: entity.entity_id,
      taxonomy_node_id: null,
      node: null,
      entity,
      label: entity.label,
    }
    const scopeKey = targetScopeKey(entity.current_assignment?.taxonomy_node_id ?? null)
    const editable =
      targetEditMode &&
      Boolean(selectedTaxonomy?.planning_enabled) &&
      Boolean(entity.current_assignment) &&
      Boolean(targetDraftsByScope[scopeKey])
    const isTargetScopeMember = currentScopeMemberKeySet.has(targetMember.member_key)
    return (
      <tr
        key={entity.entity_id}
        className={['taxonomy-entity-row', assignmentDragEnabled ? 'taxonomy-entity-row-draggable' : 'taxonomy-entity-row-drag-locked', selected ? 'taxonomy-entity-row-active' : '', isTargetScopeMember ? 'taxonomy-scope-row' : '']
          .filter(Boolean)
          .join(' ') || undefined}
        draggable={assignmentDragEnabled}
        data-assignment-drag={assignmentDragEnabled ? 'enabled' : 'disabled'}
        aria-describedby={targetEditMode ? 'taxonomy-target-edit-lock-message' : undefined}
        title={targetEditMode ? TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE : undefined}
        onDragStart={assignmentDragEnabled ? (event) => handleEntityDragStart(event, entity) : undefined}
        onDragEnd={assignmentDragEnabled ? () => setDragTargetNodeId(null) : undefined}
        onContextMenu={!lockedCashEntity && !targetEditMode ? (event) => handleEntityContextMenu(event, entity) : undefined}
      >
        <td className="holding-name-cell">
          <div className="taxonomy-node-row taxonomy-hierarchy-row">
            <span style={{ width: `${depth * 18}px`, flex: '0 0 auto' }} />
            <span className="taxonomy-tree-toggle taxonomy-tree-toggle-empty" />
            {lockedCashEntity ? (
              <span className="taxonomy-tree-check-slot" />
            ) : (
              <label className="taxonomy-tree-check-slot" aria-label={`Select ${entity.label}`}>
                <input type="checkbox" checked={selected} onChange={() => toggleEntitySelection(entity.entity_id)} />
              </label>
            )}
            <span className="taxonomy-level-label">{entity.label}</span>
            {renderInstrumentStatusCell(entity)}
            {entity.supporting_label ? <span className="taxonomy-entity-supporting-label">{entity.supporting_label}</span> : null}
          </div>
        </td>
        <td />
        <td>{renderTargetCell('saa', 'weight', targetMember, editable)}</td>
        <td>{renderTargetCell('saa', 'risk_budget', targetMember, editable)}</td>
        <td>{renderTargetCell('taa', 'weight', targetMember, editable)}</td>
        <td>{renderTargetCell('taa', 'risk_budget', targetMember, editable)}</td>
        <td>{entity.allocation != null ? formatPercent(entity.allocation) : '—'}</td>
        <td>{entity.market_value_base != null ? formatCurrency(entity.market_value_base, baseCurrency) : '—'}</td>
      </tr>
    )
  }

  function renderCashTreeRows(depth: number): Array<JSX.Element> {
    if (!showSystemCashNode) {
      return []
    }
    const cashTargetMember: TargetScopeMember = {
      member_key: targetMemberKey('cash_bucket', CASH_TARGET_MEMBER_ID),
      target_member_type: 'cash_bucket',
      target_member_id: CASH_TARGET_MEMBER_ID,
      taxonomy_node_id: null,
      node: null,
      entity: null,
      label: CASH_TARGET_LABEL,
      system_role: 'cash',
    }
    const editable = targetEditMode && Boolean(selectedTaxonomy?.planning_enabled) && Boolean(targetDraftsByScope[ROOT_TARGET_SCOPE_KEY])
    const isTargetScopeMember = currentScopeMemberKeySet.has(cashTargetMember.member_key)
    const isCollapsed = collapsedNodeIds.has(TAXONOMY_CASH_ROW_ID)
    const rows: Array<JSX.Element> = [
      <tr
        key={TAXONOMY_CASH_ROW_ID}
        className={['taxonomy-node-table-row', 'taxonomy-system-cash-row', 'taxonomy-node-depth-1', isTargetScopeMember ? 'taxonomy-scope-row' : '']
          .filter(Boolean)
          .join(' ')}
      >
        <td className="holding-name-cell">
          <div className="taxonomy-node-row taxonomy-hierarchy-row">
            <span style={{ width: `${depth * 18}px`, flex: '0 0 auto' }} />
            <button
              type="button"
              className="taxonomy-tree-toggle"
              onClick={() => toggleNodeCollapse(TAXONOMY_CASH_ROW_ID)}
            >
              <span className={`taxonomy-tree-arrow ${isCollapsed ? 'taxonomy-tree-arrow-collapsed' : 'taxonomy-tree-arrow-expanded'}`} />
            </button>
            <span className="taxonomy-level-label">{CASH_TARGET_LABEL}</span>
          </div>
        </td>
        <td>{renderFixedWeightDefaultTargetCell()}</td>
        <td>{renderTargetCell('saa', 'weight', cashTargetMember, editable)}</td>
        <td>{renderTargetCell('saa', 'risk_budget', cashTargetMember, editable)}</td>
        <td>{renderTargetCell('taa', 'weight', cashTargetMember, editable)}</td>
        <td>{renderTargetCell('taa', 'risk_budget', cashTargetMember, editable)}</td>
        <td>{cashAggregate.current_weight != null ? formatPercent(cashAggregate.current_weight) : '—'}</td>
        <td>{cashAggregate.current_value_base != null ? formatCurrency(cashAggregate.current_value_base, baseCurrency) : '—'}</td>
      </tr>,
    ]

    if (!isCollapsed) {
      if (cashBucketEntities.length) {
        cashBucketEntities
          .slice()
          .sort((left, right) => left.label.localeCompare(right.label))
          .forEach((entity) => rows.push(renderEntityTreeRow(entity, depth + 1)))
      } else {
        rows.push(<TableStatusRow key={`${TAXONOMY_CASH_ROW_ID}-empty`} colSpan={8} label="No cash accounts." />)
      }
    }
    return rows
  }

  function renderNodeTreeRows(parentId: string | null, depth: number): Array<JSX.Element> {
    const rows: Array<JSX.Element> = []
    ;(childrenByParent.get(parentId) ?? []).forEach((node) => {
      const childCount = (childrenByParent.get(node.taxonomy_node_id) ?? []).length
      const treeRow: TreeRow = {
        ...node,
        depth,
        has_children: childCount > 0,
        child_count: childCount,
      }
      const aggregate = nodeAggregates.get(node.taxonomy_node_id)
      const selected = selectedNode?.taxonomy_node_id === node.taxonomy_node_id
      const parentScopeKey = targetScopeKey(node.parent_taxonomy_node_id ?? null)
      const nodeTargetMember: TargetScopeMember = {
        member_key: targetMemberKey('taxonomy_node', node.taxonomy_node_id),
        target_member_type: 'taxonomy_node',
        target_member_id: node.taxonomy_node_id,
        taxonomy_node_id: node.taxonomy_node_id,
        node,
        entity: null,
        label: node.node_name,
      }
      const isTargetScopeChild = currentScopeMemberKeySet.has(nodeTargetMember.member_key)
      const isEditableInTree =
        targetEditMode && Boolean(selectedTaxonomy?.planning_enabled) && Boolean(targetDraftsByScope[parentScopeKey])
      const assignmentDropEnabled = canDropTaxonomyEntity({
        targetEditMode,
        terminalNode: node.is_terminal,
        actionPending: Boolean(actionPending),
      })
      const isDropTarget = dragTargetNodeId === node.taxonomy_node_id
      const isCollapsed = collapsedNodeIds.has(node.taxonomy_node_id)
      const rowClassName = [
        'taxonomy-node-table-row',
        `taxonomy-node-depth-${Math.min(depth, 3)}`,
        treeRow.has_children ? 'taxonomy-node-branch-row' : '',
        selected ? 'taxonomy-node-row-active' : '',
        isTargetScopeChild ? 'taxonomy-scope-row' : '',
        isDropTarget ? 'taxonomy-drop-target-row' : '',
      ]
        .filter(Boolean)
        .join(' ')

      rows.push(
        <tr
          key={node.taxonomy_node_id}
          className={rowClassName || undefined}
          data-assignment-drop={assignmentDropEnabled ? 'enabled' : 'disabled'}
          aria-describedby={targetEditMode ? 'taxonomy-target-edit-lock-message' : undefined}
          onContextMenu={targetEditMode ? undefined : (event) => handleNodeContextMenu(event, node)}
          onDragOver={assignmentDropEnabled ? (event) => handleNodeDragOver(event, node) : undefined}
          onDragLeave={assignmentDropEnabled ? () => setDragTargetNodeId((current) => (current === node.taxonomy_node_id ? null : current)) : undefined}
          onDrop={assignmentDropEnabled ? (event) => void handleNodeDrop(event, node) : undefined}
        >
          <td className="holding-name-cell">
            <div className="taxonomy-node-row taxonomy-hierarchy-row">
              <span style={{ width: `${depth * 18}px`, flex: '0 0 auto' }} />
              {treeRow.has_children || (directEntitiesByNodeId.get(node.taxonomy_node_id) ?? []).length ? (
                <button
                  type="button"
                  className="taxonomy-tree-toggle"
                  onClick={() => toggleNodeCollapse(node.taxonomy_node_id)}
                >
                  <span className={`taxonomy-tree-arrow ${isCollapsed ? 'taxonomy-tree-arrow-collapsed' : 'taxonomy-tree-arrow-expanded'}`} />
                </button>
              ) : (
                <span className="taxonomy-tree-toggle taxonomy-tree-toggle-empty" />
              )}
              <button
                type="button"
                className={`taxonomy-node-select ${selected ? 'taxonomy-node-select-active' : ''}`}
                onClick={() => setSelectedNodeId(node.taxonomy_node_id)}
              >
                <span>{node.node_name}</span>
              </button>
            </div>
          </td>
          <td>{renderDefaultTargetCell(node)}</td>
          <td>{renderTargetCell('saa', 'weight', nodeTargetMember, isEditableInTree)}</td>
          <td>{renderTargetCell('saa', 'risk_budget', nodeTargetMember, isEditableInTree)}</td>
          <td>{renderTargetCell('taa', 'weight', nodeTargetMember, isEditableInTree)}</td>
          <td>{renderTargetCell('taa', 'risk_budget', nodeTargetMember, isEditableInTree)}</td>
          <td>{aggregate?.current_weight != null ? formatPercent(aggregate.current_weight) : '—'}</td>
          <td>{aggregate?.current_value_base != null ? formatCurrency(aggregate.current_value_base, baseCurrency) : '—'}</td>
        </tr>,
      )

      if (!isCollapsed) {
        rows.push(...renderNodeTreeRows(node.taxonomy_node_id, depth + 1))
        ;(directEntitiesByNodeId.get(node.taxonomy_node_id) ?? []).forEach((entity) => {
          rows.push(renderEntityTreeRow(entity, depth + 1))
        })
      }
    })
    return rows
  }

  const pendingDeleteDialog = pendingDelete
    ? pendingDelete.kind === 'target-set'
      ? {
          title: `Delete ${pendingDelete.targetKind.toUpperCase()} Target Set`,
          description: `This permanently deletes the target set for ${pendingDelete.scopeLabel}. This action cannot be undone.`,
          label: 'Delete Target Set',
          confirmationText: pendingDelete.targetSet.name,
        }
      : pendingDelete.kind === 'taxonomy'
        ? {
            title: 'Delete Taxonomy',
            description: 'This permanently deletes the taxonomy, its nodes, assignments, and target sets. This action cannot be undone.',
            label: 'Delete Taxonomy',
            confirmationText: pendingDelete.taxonomy.name,
          }
        : pendingDelete.kind === 'node'
          ? {
              title: 'Delete Taxonomy Node',
              description: 'This permanently deletes the node and may affect its descendants and assignments. This action cannot be undone.',
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
      <PortfolioWorkspaceLayout activeSection="Taxonomies" toolbarLabel="Page: Taxonomies">
        <div className="taxonomy-page taxonomy-page-table">
        {notice || workspaceError || actionError || supplementalNotice ? (
          <div className="page-toast-stack" role="status" aria-live="polite">
            {notice ? <div className="page-toast page-toast-success">{notice}</div> : null}
            {workspaceError ? <div className="page-toast page-toast-error">{workspaceError}</div> : null}
            {actionError ? <div className="page-toast page-toast-error">{actionError}</div> : null}
            {supplementalNotice ? <div className="page-toast">{supplementalNotice}</div> : null}
          </div>
        ) : null}
        {loading ? <CalculationStatus /> : null}

      {!loading && !catalog && !workspaceError ? <div className="empty-state">No data.</div> : null}

      {!workspaceError ? (
        <>
          <section className="panel taxonomy-strip-section">
            <div className="taxonomy-topbar">
              <div className="taxonomy-topbar-field" ref={taxonomyPickerRef}>
                <span>Default Taxonomy:</span>
                <div className="taxonomy-picker">
                  <button
                    type="button"
                    className="taxonomy-picker-trigger"
                    onClick={() => {
                      setContextMenuState(null)
                      setTaxonomyPickerOpen((current) => !current)
                    }}
                    disabled={targetEditMode || actionPending?.startsWith('default-taxonomy-')}
                    title={targetEditMode ? TARGET_EDIT_ASSIGNMENT_LOCK_MESSAGE : undefined}
                  >
                    <span>{selectedTaxonomy?.name ?? 'No taxonomy'}</span>
                    <span className="taxonomy-picker-caret" aria-hidden="true" />
                  </button>
                  {taxonomyPickerOpen ? (
                    <div className="taxonomy-picker-menu">
                      {taxonomies.length ? (
                        taxonomies.map((taxonomy) => (
                          <button
                            type="button"
                            key={taxonomy.taxonomy_id}
                            className={`taxonomy-picker-option ${
                              taxonomy.taxonomy_id === resolvedSelectedTaxonomyId ? 'taxonomy-picker-option-active' : ''
                            }`}
                            onClick={() => void handleDefaultTaxonomySelection(taxonomy.taxonomy_id)}
                            onContextMenu={(event) => {
                              event.preventDefault()
                              setContextMenuState({
                                kind: 'taxonomy',
                                taxonomyId: taxonomy.taxonomy_id,
                                x: event.clientX,
                                y: event.clientY,
                              })
                            }}
                          >
                            {taxonomy.name}
                          </button>
                        ))
                      ) : (
                        <div className="taxonomy-picker-empty">No taxonomy</div>
                      )}
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="taxonomy-header-actions">
                <button
                  type="button"
                  className="toolbar-link button-primary"
                  onClick={() => {
                    setShowInstrumentAdd(false)
                    setShowTaxonomyRename(false)
                    setTaxonomyPickerOpen(false)
                    setContextMenuState(null)
                    setShowTaxonomyCreate(true)
                  }}
                  disabled={targetEditMode}
                >
                  Add Taxonomy
                </button>
                {selectedTaxonomy ? (
                  <button
                    type="button"
                    className="toolbar-link button-primary"
                    onClick={() => {
                      setShowTaxonomyCreate(false)
                      setShowTaxonomyRename(false)
                      setShowInstrumentAdd(true)
                      setTaxonomyPickerOpen(false)
                      setContextMenuState(null)
                    }}
                    disabled={targetEditMode || selectedTaxonomy.primary_assignment_scope !== 'instrument'}
                  >
                    Add Instrument
                  </button>
                ) : null}
              </div>
            </div>
          </section>

          {selectedTaxonomy ? (
            <section className="panel taxonomy-levels-section">
              <div className="taxonomy-collapsed-summary taxonomy-tree-summary taxonomy-target-summary-row">
                <div className="taxonomy-target-summary-meta">
                  {targetEditMode ? (
                    <span
                      id="taxonomy-target-edit-lock-message"
                      className="taxonomy-target-edit-lock-message"
                      role="status"
                    >
                      Target edit mode · edit values/type only; structure and assignment moves locked
                    </span>
                  ) : null}
                  <span>
                    {selectedTaxonomy.primary_assignment_scope === 'instrument' ? 'Instruments' : 'Items'} {coverageSummary.currentEntityCount}
                  </span>
                  <span>Assigned {coverageSummary.assignedCount}</span>
                  {selectedTaxonomy.primary_assignment_scope === 'instrument' ? (
                    <span className="taxonomy-status-legend" aria-label="Instrument status legend">
                      {renderInstrumentStatusLegendItem('held', 'Held', coverageSummary.heldEntityCount)}
                      {renderInstrumentStatusLegendItem('observe', 'Observed', coverageSummary.observeEntityCount)}
                      {renderInstrumentStatusLegendItem('former', 'Former', coverageSummary.formerEntityCount)}
                    </span>
                  ) : null}
                  {coverageSummary.ambiguousEntities.length ? <span>Ambiguous {coverageSummary.ambiguousEntities.length}</span> : null}
                </div>
                <div className="taxonomy-header-actions">
                  {selectedTaxonomy.planning_enabled && !targetEditMode ? (
                    <button
                      type="button"
                      className="table-inline-button"
                      onClick={() => {
                        setDefaultTargetDraftsByNodeId(defaultTargetDraftsFromNodes(selectedTaxonomyNodes))
                        setDragTargetNodeId(null)
                        setContextMenuState(null)
                        setTargetEditMode(true)
                      }}
                      disabled={!hasTargetScope && !selectedTaxonomyNodes.length}
                    >
                      Edit Targets
                    </button>
                  ) : null}
                  {selectedTaxonomy.planning_enabled && targetEditMode ? (
                    <>
                      <button
                        type="button"
                        className="table-inline-button"
                        onClick={() => void handleSaveTargetsConfiguration()}
                        disabled={!canSaveTargetsConfiguration || Boolean(actionPending)}
                      >
                        {actionPending === 'targets-save' ? 'Saving...' : 'Save'}
                      </button>
                      <button
                        type="button"
                        className="table-inline-button"
                        onClick={() => {
                          setTargetEditMode(false)
                          setDefaultTargetDraftsByNodeId(defaultTargetDraftsFromNodes(selectedTaxonomyNodes))
                          void reloadWorkspace()
                        }}
                        disabled={Boolean(actionPending)}
                      >
                        Cancel
                      </button>
                    </>
                  ) : null}
                </div>
              </div>

              {targetSetIntegrityNotice ? (
                <div className="taxonomy-target-integrity-warning" role="alert">
                  <strong>Target budgets need attention.</strong>
                  <span>{targetSetIntegrityNotice}</span>
                </div>
              ) : null}

              <div className="table-shell">
                <table className="transactions-table taxonomy-tree-table taxonomy-levels-table">
                  <thead>
                    <tr>
                      <th>Levels</th>
                      <th>Default Target</th>
                      <th>SAA Weight</th>
                      <th>SAA Risk</th>
                      <th>TAA Weight</th>
                      <th>TAA Risk</th>
                      <th>Actual Weight</th>
                      <th>Actual Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className={!selectedNode ? 'taxonomy-node-row-active taxonomy-hierarchy-root-row' : 'taxonomy-hierarchy-root-row'}>
                      <td className="holding-name-cell">
                        <div className="taxonomy-node-row taxonomy-hierarchy-row">
                          <button type="button" className="taxonomy-tree-toggle" onClick={() => toggleNodeCollapse(TAXONOMY_ROOT_ROW_ID)}>
                            <span
                              className={`taxonomy-tree-arrow ${
                                collapsedNodeIds.has(TAXONOMY_ROOT_ROW_ID) ? 'taxonomy-tree-arrow-collapsed' : 'taxonomy-tree-arrow-expanded'
                              }`}
                            />
                          </button>
                          <button
                            type="button"
                            className={`taxonomy-node-select ${!selectedNode ? 'taxonomy-node-select-active' : ''}`}
                            onClick={() => setSelectedNodeId(null)}
                          >
                            <span>{selectedTaxonomy.name}</span>
                          </button>
                        </div>
                      </td>
                      <td />
                      <td />
                      <td />
                      <td />
                      <td />
                      <td>{taxonomyCurrentSummary.current_weight != null ? formatPercent(taxonomyCurrentSummary.current_weight) : '—'}</td>
                      <td>{taxonomyCurrentSummary.current_value_base != null ? formatCurrency(taxonomyCurrentSummary.current_value_base, baseCurrency) : '—'}</td>
                    </tr>

                    {!collapsedNodeIds.has(TAXONOMY_ROOT_ROW_ID) ? (
                      <>
                        {selectedTaxonomyNodes.length ? (
                          <>
                            {renderNodeTreeRows(null, 1)}
                            {renderCashTreeRows(1)}
                          </>
                        ) : (
                          <>
                            <tr className="table-status-row">
                              <td colSpan={8} className="empty-state-cell">
                                <span>No nodes.</span>{' '}
                                <button
                                  type="button"
                                  className="table-inline-button"
                                  onClick={() => startNodeCreate('root')}
                                  disabled={targetEditMode}
                                >
                                  Add Root
                                </button>
                              </td>
                            </tr>
                            {renderCashTreeRows(1)}
                          </>
                        )}
                        <tr className="taxonomy-subsection-row">
                          <td className="holding-name-cell">
                            <div className="taxonomy-node-row taxonomy-hierarchy-row">
                              <span style={{ width: '18px', flex: '0 0 auto' }} />
                              <button
                                type="button"
                                className="taxonomy-tree-toggle"
                                onClick={() => toggleNodeCollapse(TAXONOMY_UNASSIGNED_ROW_ID)}
                              >
                                <span
                                  className={`taxonomy-tree-arrow ${
                                    collapsedNodeIds.has(TAXONOMY_UNASSIGNED_ROW_ID)
                                      ? 'taxonomy-tree-arrow-collapsed'
                                      : 'taxonomy-tree-arrow-expanded'
                                  }`}
                                />
                              </button>
                              <span className="taxonomy-level-label">Unassigned</span>
                            </div>
                          </td>
                          <td />
                          <td />
                          <td />
                          <td />
                          <td />
                          <td>{unassignedSummary.current_weight != null ? formatPercent(unassignedSummary.current_weight) : '—'}</td>
                          <td>{unassignedSummary.current_value_base != null ? formatCurrency(unassignedSummary.current_value_base, baseCurrency) : '—'}</td>
                        </tr>
                        {!collapsedNodeIds.has(TAXONOMY_UNASSIGNED_ROW_ID) ? (
                          coverageSummary.unassignedEntities.length ? (
                            coverageSummary.unassignedEntities
                              .slice()
                              .sort((left, right) => left.label.localeCompare(right.label))
                              .map((entity) => renderEntityTreeRow(entity, 2))
                          ) : (
                            <TableStatusRow colSpan={8} label="No unassigned items." />
                          )
                        ) : null}
                      </>
                    ) : null}
                  </tbody>
                </table>
              </div>

            </section>
          ) : null}
          <TaxonomyModal
            open={showTaxonomyCreate}
            title="New Taxonomy"
            onClose={() => setShowTaxonomyCreate(false)}
          >
            <form className="transaction-form taxonomy-form-compact" onSubmit={(event) => void handleCreateTaxonomy(event)}>
              <div className="taxonomy-form-grid taxonomy-topbar-form-grid">
                <label>
                  <span>Name</span>
                  <input value={taxonomyName} onChange={(event) => setTaxonomyName(event.target.value)} required />
                </label>
                <label>
                  <span>Assignment Scope</span>
                  <select value={taxonomyScope} onChange={(event) => setTaxonomyScope(event.target.value as TaxonomyAssignmentScope)}>
                    <option value="instrument">Instrument</option>
                    <option value="account">Account</option>
                    <option value="cash_bucket">Cash Bucket</option>
                  </select>
                </label>
              </div>
              <div className="transaction-form-footer">
                <div className="taxonomy-footer-actions">
                  <button type="button" className="toolbar-link" onClick={() => setShowTaxonomyCreate(false)}>
                    Cancel
                  </button>
                  <button type="submit" className="toolbar-link button-primary" disabled={actionPending === 'taxonomy-create'}>
                    {actionPending === 'taxonomy-create' ? 'Creating…' : 'Create Taxonomy'}
                  </button>
                </div>
              </div>
            </form>
          </TaxonomyModal>
          <TaxonomyModal
            open={showTaxonomyRename && Boolean(taxonomyRenameId)}
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
            open={showInstrumentAdd && selectedTaxonomy?.primary_assignment_scope === 'instrument'}
            title="Add Instrument"
            onClose={() => setShowInstrumentAdd(false)}
            modalClassName="taxonomy-instrument-picker-modal"
          >
            <form
              className="transaction-form taxonomy-form-compact taxonomy-instrument-picker-form"
              onSubmit={(event) => void handleAddRegistryInstrumentToUniverse(event)}
            >
              <div className="taxonomy-instrument-picker">
                <label className="taxonomy-instrument-picker-search">
                  <span>Instrument</span>
                  <input
                    type="search"
                    value={instrumentAddSearch}
                    placeholder="Search instrument..."
                    autoFocus
                    onChange={(event) => {
                      setInstrumentAddSearch(event.target.value)
                      setInstrumentAddInstrumentId('')
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !instrumentAddInstrumentId && registryInstrumentOptions[0]) {
                        event.preventDefault()
                        const instrument = registryInstrumentOptions[0]
                        setInstrumentAddInstrumentId(instrument.instrument_id)
                        setInstrumentAddSearch(`${primaryIdentifier(instrument)} · ${instrument.instrument_name}`)
                      }
                    }}
                  />
                  {instrumentAddSearch || instrumentAddInstrumentId ? (
                    <button
                      type="button"
                      className="taxonomy-instrument-picker-clear"
                      onClick={() => {
                        setInstrumentAddSearch('')
                        setInstrumentAddInstrumentId('')
                      }}
                    >
                      Clear
                    </button>
                  ) : null}
                </label>
                <div className="taxonomy-instrument-picker-results" role="listbox" aria-label="Instrument results">
                  {registryInstrumentOptions.length ? (
                    registryInstrumentOptions.map((instrument) => {
                      const selected = instrument.instrument_id === instrumentAddInstrumentId
                      return (
                        <button
                          type="button"
                          key={instrument.instrument_id}
                          className={`taxonomy-instrument-picker-option ${selected ? 'taxonomy-instrument-picker-option-active' : ''}`}
                          onClick={() => {
                            setInstrumentAddInstrumentId(instrument.instrument_id)
                            setInstrumentAddSearch(`${primaryIdentifier(instrument)} · ${instrument.instrument_name}`)
                          }}
                          role="option"
                          aria-selected={selected}
                        >
                          <strong>{instrument.instrument_name}</strong>
                          <span>
                            {primaryIdentifier(instrument)} · {formatLabel(instrument.instrument_type)} · {instrument.currency}
                          </span>
                        </button>
                      )
                    })
                  ) : (
                    <div className="taxonomy-instrument-picker-empty">No database match</div>
                  )}
                </div>
              </div>
              <div className="transaction-form-footer">
                <div className="taxonomy-footer-actions">
                  <button type="button" className="toolbar-link" onClick={() => setShowInstrumentAdd(false)}>
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="toolbar-link button-primary"
                    disabled={actionPending === 'instrument-add' || !instrumentAddInstrumentId}
                  >
                    {actionPending === 'instrument-add' ? 'Adding…' : 'Add Instrument'}
                  </button>
                </div>
              </div>
            </form>
          </TaxonomyModal>
          <TaxonomyModal
            open={showNodeCreate}
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
            open={showNodeEdit && Boolean(editingNode)}
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
          {contextMenuState ? (
            <div
              className="taxonomy-context-menu"
              style={contextMenuStyle}
              onClick={(event) => event.stopPropagation()}
            >
              {contextMenuTaxonomy ? (
                <>
                  <button type="button" className="taxonomy-context-menu-item" onClick={() => setContextMenuState(null)}>
                    Keep Selected
                  </button>
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
                  <button type="button" className="taxonomy-context-menu-item" onClick={() => setContextMenuState(null)}>
                    Keep Selected
                  </button>
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
                      disabled={targetEditMode || selectedEntityCount === 0}
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
                    disabled={targetEditMode || !selectedNode?.is_terminal}
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
        open={Boolean(pendingDeleteDialog)}
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
