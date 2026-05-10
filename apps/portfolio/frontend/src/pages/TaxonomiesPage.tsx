import { FormEvent, useEffect, useMemo, useState, type DragEvent as ReactDragEvent, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  createPortfolioTaxonomy,
  createPortfolioTaxonomyAssignment,
  createPortfolioTaxonomyNode,
  createPortfolioTargetSet,
  deletePortfolioTaxonomy,
  deletePortfolioTaxonomyNode,
  deletePortfolioTargetSet,
  getHoldingsWorkspace,
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
  type PortfolioTargetSetLineRecord,
  type PortfolioTargetSetRecord,
  type PortfolioTaxonomyAssignmentRecord,
  type PortfolioTaxonomyCatalogResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioTaxonomyRecord,
  type TaxonomyAssignmentScope,
} from '../lib/api'
import { formatCurrency, formatLabel, formatPercent } from '../lib/format'

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
  coverage_state: 'unassigned' | 'ambiguous' | 'selected' | 'other'
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
}

type TargetSetDraft = {
  name: string
  effective_from: string
  effective_to: string
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

type WorkspaceFetchResult = {
  catalog: PortfolioTaxonomyCatalogResponse | null
  holdingsWorkspace: HoldingsWorkspaceResponse | null
  accountsResponse: PortfolioAccountsWorkspaceResponse | null
  workspaceError: string | null
  supplementalNotice: string | null
}

const TAXONOMY_ROOT_ROW_ID = '__taxonomy_root__'
const TAXONOMY_UNASSIGNED_ROW_ID = '__taxonomy_unassigned__'
const ROOT_TARGET_SCOPE_KEY = '__target_scope_root__'
const EMPTY_TARGET_SET_DRAFT: TargetSetDraft = {
  name: '',
  effective_from: '',
  effective_to: '',
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

function primaryIdentifier(instrument: InstrumentCore) {
  return instrument.identifiers.find((identifier) => identifier.is_primary)?.identifier_value ?? instrument.instrument_id
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
  if (!open) {
    return null
  }

  return (
    <div className="taxonomy-modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="taxonomy-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
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

function allowedDimensionsForBudgetingLevel(budgetingLevel?: string | null) {
  return {
    weight: budgetingLevel === 'weight' || budgetingLevel === 'weight_and_risk_budget',
    risk_budget: budgetingLevel === 'risk_budget' || budgetingLevel === 'weight_and_risk_budget',
  }
}

function normalizeRootDefaultTargetDimension(
  planningEnabled: boolean,
  budgetingLevel: string,
  preferred: 'weight' | 'risk_budget',
) {
  if (!planningEnabled) {
    return preferred
  }
  if (budgetingLevel === 'weight') {
    return 'weight'
  }
  if (budgetingLevel === 'risk_budget') {
    return 'risk_budget'
  }
  return preferred
}

function planningConfigError(
  planningEnabled: boolean,
  budgetingLevel: string,
  rootDefaultTargetDimension: 'weight' | 'risk_budget',
) {
  if (!planningEnabled) {
    return null
  }
  if (!budgetingLevel) {
    return 'Planning-enabled taxonomies must select a budgeting level before targets can be edited.'
  }
  if (budgetingLevel === 'weight' && rootDefaultTargetDimension !== 'weight') {
    return 'Weight-only taxonomies must use Weight as the root default target dimension.'
  }
  if (budgetingLevel === 'risk_budget' && rootDefaultTargetDimension !== 'risk_budget') {
    return 'Risk-budget-only taxonomies must use Risk Budget as the root default target dimension.'
  }
  return null
}

function percentInputFromDecimal(value?: number | null) {
  if (value == null || Number.isNaN(value)) {
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
          target_weight: percentInputFromDecimal(targetLine?.target_weight),
          target_risk_share: percentInputFromDecimal(targetLine?.target_risk_share),
          notes: targetLine?.notes ?? '',
        },
      ]
    }),
  ) as Record<string, TargetLineDraft>

  return {
    name: targetSet?.name ?? defaultName,
    effective_from: targetSet?.effective_from ?? '',
    effective_to: targetSet?.effective_to ?? '',
    weight_enabled: targetSet?.weight_enabled ?? defaultWeightEnabled,
    risk_budget_enabled: targetSet?.risk_budget_enabled ?? defaultRiskBudgetEnabled,
    status: targetSet?.status ?? 'active',
    notes: targetSet?.notes ?? '',
    lines_by_member_key: linesByMemberKey,
  } satisfies TargetSetDraft
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
      } else if (Number.isNaN(parsedWeight) || parsedWeight < 0) {
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
      } else if (Number.isNaN(parsedRisk) || parsedRisk < 0) {
        errors.push(`Invalid target risk budget for ${member.label}.`)
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
    warnings.push('Target risk budget total is not 100% within the selected scope.')
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
  const [catalogResult, holdingsResult, accountsResult] = await Promise.allSettled([
    getPortfolioTaxonomyCatalog(portfolioId),
    getHoldingsWorkspace(portfolioId),
    getPortfolioAccountsWorkspace(portfolioId),
  ])

  const supplementalMessages: string[] = []
  const catalog = catalogResult.status === 'fulfilled' ? catalogResult.value : null
  const holdingsWorkspace = holdingsResult.status === 'fulfilled' ? holdingsResult.value : null
  const accountsResponse = accountsResult.status === 'fulfilled' ? accountsResult.value : null

  if (holdingsResult.status === 'rejected') {
    supplementalMessages.push(`Current holdings coverage unavailable: ${extractErrorMessage(holdingsResult.reason)}`)
  }
  if (accountsResult.status === 'rejected') {
    supplementalMessages.push(`Account coverage unavailable: ${extractErrorMessage(accountsResult.reason)}`)
  }

  return {
    catalog,
    holdingsWorkspace,
    accountsResponse,
    workspaceError: catalogResult.status === 'rejected' ? extractErrorMessage(catalogResult.reason) : null,
    supplementalNotice: supplementalMessages.length ? supplementalMessages.join(' ') : null,
  }
}

export default function TaxonomiesPage() {
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [catalog, setCatalog] = useState<PortfolioTaxonomyCatalogResponse | null>(null)
  const [holdingsWorkspace, setHoldingsWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [accountsResponse, setAccountsResponse] = useState<PortfolioAccountsWorkspaceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [supplementalNotice, setSupplementalNotice] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionPending, setActionPending] = useState<string | null>(null)

  const [taxonomyName, setTaxonomyName] = useState('')
  const [taxonomyType, setTaxonomyType] = useState('custom')
  const [taxonomyPurpose, setTaxonomyPurpose] = useState('')
  const [taxonomyScope, setTaxonomyScope] = useState<TaxonomyAssignmentScope>('instrument')
  const [taxonomyPlanningEnabled, setTaxonomyPlanningEnabled] = useState(false)
  const [taxonomyBudgetingLevel, setTaxonomyBudgetingLevel] = useState('')
  const [taxonomyRootDefaultTargetDimension, setTaxonomyRootDefaultTargetDimension] = useState<'weight' | 'risk_budget'>('weight')

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [collapsedNodeIds, setCollapsedNodeIds] = useState<Set<string>>(new Set())
  const [selectedEntityIds, setSelectedEntityIds] = useState<Set<string>>(new Set())
  const [entitySearch, setEntitySearch] = useState('')
  const [entityFilter, setEntityFilter] = useState<'all' | 'unassigned' | 'selected' | 'other' | 'ambiguous'>('all')

  const [selectedTaxonomyName, setSelectedTaxonomyName] = useState('')
  const [selectedTaxonomyType, setSelectedTaxonomyType] = useState('custom')
  const [selectedTaxonomyPurpose, setSelectedTaxonomyPurpose] = useState('')
  const [selectedTaxonomyPlanningEnabled, setSelectedTaxonomyPlanningEnabled] = useState(false)
  const [selectedTaxonomyBudgetingLevel, setSelectedTaxonomyBudgetingLevel] = useState('')
  const [selectedTaxonomyRootDefaultTargetDimension, setSelectedTaxonomyRootDefaultTargetDimension] = useState<'weight' | 'risk_budget'>('weight')
  const [selectedTaxonomyStatus, setSelectedTaxonomyStatus] = useState('active')
  const [selectedTaxonomyEffectiveFrom, setSelectedTaxonomyEffectiveFrom] = useState('')
  const [selectedTaxonomyEffectiveTo, setSelectedTaxonomyEffectiveTo] = useState('')

  const [newNodeName, setNewNodeName] = useState('')
  const [newNodeCode, setNewNodeCode] = useState('')
  const [newNodeSortOrder, setNewNodeSortOrder] = useState('')
  const [newNodeDefaultTargetDimension, setNewNodeDefaultTargetDimension] = useState<'weight' | 'risk_budget'>('weight')
  const [targetDraftsByScope, setTargetDraftsByScope] = useState<
    Record<string, { saa: TargetSetDraft; taa: TargetSetDraft }>
  >({})
  const [activeTargetScopeKey, setActiveTargetScopeKey] = useState(ROOT_TARGET_SCOPE_KEY)
  const [showTaxonomyCreate, setShowTaxonomyCreate] = useState(false)
  const [showTaxonomyDetails, setShowTaxonomyDetails] = useState(false)
  const [showNodeCreate, setShowNodeCreate] = useState(false)
  const [showNodeEdit, setShowNodeEdit] = useState(false)
  const [nodeCreateMode, setNodeCreateMode] = useState<NodeCreateMode>('root')
  const [nodeCreateParentId, setNodeCreateParentId] = useState('')
  const [nodeCreateAnchorNodeId, setNodeCreateAnchorNodeId] = useState<string | null>(null)
  const [nodeEditId, setNodeEditId] = useState<string | null>(null)
  const [nodeEditName, setNodeEditName] = useState('')
  const [nodeEditCode, setNodeEditCode] = useState('')
  const [nodeEditSortOrder, setNodeEditSortOrder] = useState('')
  const [nodeEditDefaultTargetDimension, setNodeEditDefaultTargetDimension] = useState<'weight' | 'risk_budget'>('weight')
  const [contextMenuState, setContextMenuState] = useState<TaxonomyContextMenuState | null>(null)
  const [dragTargetNodeId, setDragTargetNodeId] = useState<string | null>(null)

  useEffect(() => {
    if (!portfolioId) {
      setCatalog(null)
      setHoldingsWorkspace(null)
      setAccountsResponse(null)
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
  const resolvedSelectedTaxonomyId =
    taxonomies.find((taxonomy) => taxonomy.taxonomy_id === requestedTaxonomyId)?.taxonomy_id ??
    taxonomies[0]?.taxonomy_id ??
    ''
  const selectedTaxonomy = taxonomies.find((taxonomy) => taxonomy.taxonomy_id === resolvedSelectedTaxonomyId) ?? null
  const selectedTaxonomyNodes = useMemo(
    () => taxonomyNodes.filter((node) => node.taxonomy_id === resolvedSelectedTaxonomyId),
    [resolvedSelectedTaxonomyId, taxonomyNodes],
  )
  const selectedTaxonomyAssignments = useMemo(
    () => taxonomyAssignments.filter((assignment) => assignment.taxonomy_id === resolvedSelectedTaxonomyId),
    [resolvedSelectedTaxonomyId, taxonomyAssignments],
  )
  const referenceDate = holdingsWorkspace?.as_of_date ?? null
  const holdingsRows = holdingsWorkspace?.rows ?? []
  const baseCurrency = holdingsWorkspace?.base_currency ?? 'CNY'
  const accountRows = accountsResponse?.accounts ?? []

  useEffect(() => {
    if (!taxonomies.length) {
      setShowTaxonomyCreate(true)
    }
  }, [taxonomies.length])

  useEffect(() => {
    if (taxonomyScope !== 'instrument') {
      setTaxonomyPlanningEnabled(false)
      setTaxonomyBudgetingLevel('')
    }
  }, [taxonomyScope])

  useEffect(() => {
    if (!selectedTaxonomy) {
      setSelectedTaxonomyName('')
      setSelectedTaxonomyType('custom')
      setSelectedTaxonomyPurpose('')
      setSelectedTaxonomyPlanningEnabled(false)
      setSelectedTaxonomyBudgetingLevel('')
      setSelectedTaxonomyRootDefaultTargetDimension('weight')
      setSelectedTaxonomyStatus('active')
      setSelectedTaxonomyEffectiveFrom('')
      setSelectedTaxonomyEffectiveTo('')
      return
    }
    setSelectedTaxonomyName(selectedTaxonomy.name)
    setSelectedTaxonomyType(selectedTaxonomy.taxonomy_type)
    setSelectedTaxonomyPurpose(selectedTaxonomy.purpose ?? '')
    setSelectedTaxonomyPlanningEnabled(selectedTaxonomy.planning_enabled)
    setSelectedTaxonomyBudgetingLevel(selectedTaxonomy.budgeting_level ?? '')
    setSelectedTaxonomyRootDefaultTargetDimension(selectedTaxonomy.root_default_target_dimension ?? 'weight')
    setSelectedTaxonomyStatus(selectedTaxonomy.status)
    setSelectedTaxonomyEffectiveFrom(selectedTaxonomy.effective_from ?? '')
    setSelectedTaxonomyEffectiveTo(selectedTaxonomy.effective_to ?? '')
  }, [selectedTaxonomy])

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

  const collapsibleNodeIds = useMemo(
    () =>
      new Set(
        selectedTaxonomyNodes
          .filter((node) => (childrenByParent.get(node.taxonomy_node_id) ?? []).length > 0)
          .map((node) => node.taxonomy_node_id),
      ),
    [childrenByParent, selectedTaxonomyNodes],
  )

  useEffect(() => {
    if (!selectedTaxonomy) {
      setSelectedNodeId(null)
      setCollapsedNodeIds(new Set())
      setSelectedEntityIds(new Set())
      return
    }
    const preferredNodeId = selectedNodeId && nodeById.has(selectedNodeId) ? selectedNodeId : null
    setSelectedNodeId(preferredNodeId)
  }, [childrenByParent, nodeById, selectedNodeId, selectedTaxonomy])

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
        if (selectedNode?.is_terminal && selectedEntityIds.size) {
          event.preventDefault()
          void assignEntitiesToNode(selectedNode, Array.from(selectedEntityIds))
        }
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
  }, [selectedNode, selectedEntityIds])

  const activeAssignments = useMemo(
    () =>
      selectedTaxonomyAssignments.filter(
        (assignment) =>
          assignment.status === 'active' &&
          isRecordActive(assignment.effective_from, assignment.effective_to, referenceDate),
      ),
    [referenceDate, selectedTaxonomyAssignments],
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
      const includeCashBuckets = selectedTaxonomy.planning_enabled
      const visibleCashAccounts = accountRows.filter((accountRow) => {
        if (accountRow.account.account_type !== 'deposit_account') {
          return false
        }
        const assignmentKey = coverageEntityKey('cash_bucket', accountRow.account.account_id)
        return (accountRow.derived_cash_balance_base ?? 0) !== 0 || activeAssignmentsByEntityKey.has(assignmentKey)
      })
      const totalEntityValueBase =
        holdingsRows.reduce((total, row) => total + (row.market_value_base ?? 0), 0) +
        (includeCashBuckets
          ? visibleCashAccounts.reduce((total, accountRow) => total + (accountRow.derived_cash_balance_base ?? 0), 0)
          : 0)

      const holdingEntities = holdingsRows.map((row) => {
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
          supporting_label: row.market_value_base != null ? `${row.instrument_core.currency} · ${formatCurrency(row.market_value_base, baseCurrency)}` : row.instrument_core.currency,
          allocation:
            row.market_value_base != null && totalEntityValueBase > 1e-9
              ? row.market_value_base / totalEntityValueBase
              : row.allocation ?? null,
          market_value_base: row.market_value_base ?? null,
          current_assignment: assignment,
          current_node: currentNode,
          coverage_state: coverageState,
        } satisfies CoverageEntity
      })

      if (!includeCashBuckets) {
        return holdingEntities
      }

      const cashEntities = visibleCashAccounts.map((accountRow) => {
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
          target_scope: 'cash_bucket' as const,
          label: `${accountRow.account.account_name} · Cash`,
          supporting_label: `${accountRow.account.currency} · ${formatCurrency(accountRow.derived_cash_balance, accountRow.account.currency)}`,
          allocation:
            accountRow.derived_cash_balance_base != null && totalEntityValueBase > 1e-9
              ? accountRow.derived_cash_balance_base / totalEntityValueBase
              : null,
          market_value_base: accountRow.derived_cash_balance_base ?? null,
          current_assignment: assignment,
          current_node: currentNode,
          coverage_state: coverageState,
        } satisfies CoverageEntity
      })

      return [...holdingEntities, ...cashEntities]
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
            supporting_label: `${accountRow.account.currency} · ${formatCurrency(accountRow.derived_cash_balance, accountRow.account.currency)}`,
            allocation: null,
            market_value_base: accountRow.derived_cash_balance_base ?? null,
            current_assignment: assignment,
            current_node: currentNode,
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
        coverage_state: coverageState,
      }
    })
  }, [
    accountRows,
    activeAssignmentsByEntityKey,
    baseCurrency,
    holdingsRows,
    nodeById,
    selectedNodeScopeIds,
    selectedTaxonomy,
  ])

  const coverageSummary = useMemo(() => {
    const currentEntityCount = currentEntities.length
    const unassignedEntities = currentEntities.filter((entity) => entity.coverage_state === 'unassigned')
    const ambiguousEntities = currentEntities.filter((entity) => entity.coverage_state === 'ambiguous')
    const assignedCount = currentEntityCount - unassignedEntities.length - ambiguousEntities.length
    return {
      currentEntityCount,
      assignedCount,
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

  const taxonomyAssignedSummary = useMemo(() => {
    let weightTotal = 0
    let valueTotal = 0
    let weightSeen = false
    let valueSeen = false

    currentEntities.forEach((entity) => {
      if (!entity.current_assignment || entity.coverage_state === 'ambiguous') {
        return
      }
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
  const selectedEntityCount = selectedEntityIds.size

  const planningTaxonomies = taxonomies.filter((taxonomy) => taxonomy.planning_enabled)
  const defaultPlanningTaxonomy =
    planningTaxonomies.find((taxonomy) => taxonomy.taxonomy_id === catalog?.default_planning_taxonomy_id) ?? null
  const selectedTaxonomyTargetSets = useMemo(
    () => (catalog?.target_sets ?? []).filter((targetSet) => targetSet.taxonomy_id === resolvedSelectedTaxonomyId),
    [catalog?.target_sets, resolvedSelectedTaxonomyId],
  )
  const selectedTaxonomyActiveTargetSets = useMemo(
    () =>
      selectedTaxonomyTargetSets.filter(
        (targetSet) =>
          targetSet.status === 'active' && isRecordActive(targetSet.effective_from, targetSet.effective_to, referenceDate),
      ),
    [referenceDate, selectedTaxonomyTargetSets],
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

  const selectedNodePath = selectedNode ? nodePathByNodeId.get(selectedNode.taxonomy_node_id) ?? [] : []
  const taxonomyCreatePlanningError = planningConfigError(
    taxonomyPlanningEnabled,
    taxonomyBudgetingLevel,
    taxonomyRootDefaultTargetDimension,
  )
  const taxonomyDetailsPlanningError = planningConfigError(
    selectedTaxonomyPlanningEnabled,
    selectedTaxonomyBudgetingLevel,
    selectedTaxonomyRootDefaultTargetDimension,
  )
  const nodeCreateAnchorNode = nodeCreateAnchorNodeId ? nodeById.get(nodeCreateAnchorNodeId) ?? null : null
  const contextMenuNode = contextMenuState?.kind === 'node' ? nodeById.get(contextMenuState.nodeId) ?? null : null
  const contextMenuEntity =
    contextMenuState?.kind === 'entity'
      ? currentEntities.find((entity) => entity.entity_id === contextMenuState.entityId) ?? null
      : null
  const nodeCreateContextLabel =
    nodeCreateMode === 'root'
      ? 'Add Root Node'
      : nodeCreateMode === 'sibling'
        ? `Add Same-Level Node${nodeCreateAnchorNode ? ` · ${nodeCreateAnchorNode.node_name}` : ''}`
        : `Add Child Node${nodeCreateAnchorNode ? ` · ${nodeCreateAnchorNode.node_name}` : ''}`
  const editingNode = nodeEditId ? nodeById.get(nodeEditId) ?? null : null
  const allowedTargetDimensions = allowedDimensionsForBudgetingLevel(selectedTaxonomy?.budgeting_level)
  const scopeMembersByScopeKey = useMemo(() => {
    const lookup = new Map<string, TargetScopeMember[]>()

    const rootChildren = sortNodes(childrenByParent.get(null) ?? [])
    if (rootChildren.length) {
      lookup.set(
        ROOT_TARGET_SCOPE_KEY,
        rootChildren.map((node) => ({
          member_key: targetMemberKey('taxonomy_node', node.taxonomy_node_id),
          target_member_type: 'taxonomy_node',
          target_member_id: node.taxonomy_node_id,
          taxonomy_node_id: node.taxonomy_node_id,
          node,
          entity: null,
          label: node.node_name,
        })),
      )
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
  }, [childrenByParent, directEntitiesByNodeId, selectedTaxonomyNodes])
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

    setTargetDraftsByScope(nextDraftsByScope)
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

  function formatDraftSum(value: number | null) {
    return value != null ? `${value.toFixed(2)}%` : '—'
  }

  function renderDefaultTargetCell(node: PortfolioTaxonomyNodeRecord) {
    return (
      <select
        className="taxonomy-default-target-select"
        value={node.default_target_dimension}
        onChange={(event) => void handleUpdateNodeDefaultTarget(node, event.target.value as 'weight' | 'risk_budget')}
        disabled={actionPending === `node-default-target-${node.taxonomy_node_id}`}
      >
        <option value="weight">Weight</option>
        <option value="risk_budget">Risk Budget</option>
      </select>
    )
  }

  function renderRootDefaultTargetCell() {
    if (!selectedTaxonomy) {
      return '—'
    }
    return (
      <select
        className="taxonomy-default-target-select"
        value={selectedTaxonomy.root_default_target_dimension}
        onChange={(event) => void handleUpdateRootDefaultTarget(event.target.value as 'weight' | 'risk_budget')}
        disabled={actionPending === `taxonomy-root-default-${selectedTaxonomy.taxonomy_id}`}
      >
        <option value="weight">Weight</option>
        <option value="risk_budget">Risk Budget</option>
      </select>
    )
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
        return (
          <input
            type="number"
            step="0.01"
            min="0"
            value={lineDraft.target_weight}
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
      return (
        <input
          type="number"
          step="0.01"
          min="0"
          value={lineDraft.target_risk_share}
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
    setNewNodeCode('')
    setNewNodeSortOrder('')
    setNewNodeDefaultTargetDimension('weight')
  }

  function resetNodeEditDraft() {
    setNodeEditId(null)
    setNodeEditName('')
    setNodeEditCode('')
    setNodeEditSortOrder('')
    setNodeEditDefaultTargetDimension('weight')
  }

  function startNodeCreate(mode: NodeCreateMode, anchorNode?: PortfolioTaxonomyNodeRecord | null) {
    resetNodeCreateDraft()
    setShowNodeCreate(true)
    setShowNodeEdit(false)
    setNodeCreateMode(mode)
    setContextMenuState(null)

    if (mode === 'root' || !anchorNode) {
      setNodeCreateParentId('')
      setNodeCreateAnchorNodeId(null)
      setNewNodeDefaultTargetDimension('weight')
      return
    }

    setSelectedNodeId(anchorNode.taxonomy_node_id)
    setNodeCreateAnchorNodeId(anchorNode.taxonomy_node_id)
    setNodeCreateParentId(mode === 'child' ? anchorNode.taxonomy_node_id : anchorNode.parent_taxonomy_node_id ?? '')
    setNewNodeDefaultTargetDimension(anchorNode.default_target_dimension)
  }

  function startNodeEdit(node: PortfolioTaxonomyNodeRecord) {
    setContextMenuState(null)
    setShowTaxonomyCreate(false)
    setShowTaxonomyDetails(false)
    setShowNodeCreate(false)
    setShowNodeEdit(true)
    setSelectedNodeId(node.taxonomy_node_id)
    setNodeEditId(node.taxonomy_node_id)
    setNodeEditName(node.node_name)
    setNodeEditCode(node.node_code ?? '')
    setNodeEditSortOrder(String(node.sort_order))
    setNodeEditDefaultTargetDimension(node.default_target_dimension)
  }

  function closeModalStack() {
    setShowTaxonomyCreate(false)
    setShowTaxonomyDetails(false)
    setShowNodeCreate(false)
    setShowNodeEdit(false)
  }

  function handleTaxonomySelection(taxonomyId: string) {
    updateSearchParam('taxonomy_id', taxonomyId)
    setCollapsedNodeIds(new Set())
    setSelectedEntityIds(new Set())
    setEntityFilter('all')
    setEntitySearch('')
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

  function expandAllNodes() {
    setCollapsedNodeIds(new Set())
  }

  function collapseAllNodes() {
    setCollapsedNodeIds(new Set(collapsibleNodeIds))
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
    if (entity.coverage_state === 'ambiguous') {
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
    if (!node.is_terminal) {
      return
    }
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setDragTargetNodeId(node.taxonomy_node_id)
  }

  async function handleNodeDrop(event: ReactDragEvent, node: PortfolioTaxonomyNodeRecord) {
    if (!node.is_terminal) {
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

  function buildTargetSetPayload(draft: TargetSetDraft) {
    return {
      name: draft.name.trim(),
      effective_from: draft.effective_from || null,
      effective_to: draft.effective_to || null,
      weight_enabled: draft.weight_enabled,
      risk_budget_enabled: draft.risk_budget_enabled,
      status: draft.status,
      notes: draft.notes || null,
      lines: currentScopeMembers.map((member) => {
        const lineDraft = draft.lines_by_member_key[member.member_key] ?? {
          target_weight: '',
          target_risk_share: '',
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

  async function handleSaveTargetSet(kind: 'saa' | 'taa') {
    if (!portfolioId || !selectedTaxonomy || !hasTargetScope) {
      return
    }
    const draft = kind === 'saa' ? saaDraft : taaDraft
    const validation = kind === 'saa' ? saaValidation : taaValidation
    const existingTargetSet = kind === 'saa' ? activeSaaTargetSet : activeTaaTargetSet
    if (!validation.savable) {
      setActionError(validation.errors[0] ?? 'Current scope target set is invalid.')
      setNotice(null)
      return
    }
    setActionPending(`target-${kind}-save`)
    setActionError(null)
    setNotice(null)
    try {
    const payload = buildTargetSetPayload(draft)
      if (existingTargetSet) {
        await updatePortfolioTargetSet(portfolioId, selectedTaxonomy.taxonomy_id, existingTargetSet.target_set_id, payload)
        setNotice(`Saved ${kind.toUpperCase()} targets for ${currentScopeLabel}.`)
      } else {
        await createPortfolioTargetSet(portfolioId, selectedTaxonomy.taxonomy_id, {
          comparator_taxonomy_node_id: activeComparatorScopeNodeId,
          target_set_type: kind,
          ...payload,
        })
        setNotice(`Created ${kind.toUpperCase()} targets for ${currentScopeLabel}.`)
      }
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleDeleteTargetSet(kind: 'saa' | 'taa') {
    if (!portfolioId || !selectedTaxonomy) {
      return
    }
    const existingTargetSet = kind === 'saa' ? activeSaaTargetSet : activeTaaTargetSet
    if (!existingTargetSet) {
      return
    }
    if (!window.confirm(`Delete ${kind.toUpperCase()} target set "${existingTargetSet.name}"?`)) {
      return
    }
    setActionPending(`target-${kind}-delete`)
    setActionError(null)
    setNotice(null)
    try {
      await deletePortfolioTargetSet(portfolioId, selectedTaxonomy.taxonomy_id, existingTargetSet.target_set_id)
      setNotice(`Deleted ${kind.toUpperCase()} targets for ${currentScopeLabel}.`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleCreateTaxonomy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!portfolioId) {
      return
    }
    if (taxonomyCreatePlanningError) {
      setActionError(taxonomyCreatePlanningError)
      setNotice(null)
      return
    }
    setActionPending('taxonomy-create')
    setActionError(null)
    setNotice(null)
    try {
      const created = await createPortfolioTaxonomy(portfolioId, {
        name: taxonomyName,
        taxonomy_type: taxonomyType,
        purpose: taxonomyPurpose || null,
        primary_assignment_scope: taxonomyScope,
        planning_enabled: taxonomyPlanningEnabled,
        budgeting_level: taxonomyBudgetingLevel || null,
        root_default_target_dimension: normalizeRootDefaultTargetDimension(
          taxonomyPlanningEnabled,
          taxonomyBudgetingLevel,
          taxonomyRootDefaultTargetDimension,
        ),
      })
      setTaxonomyName('')
      setTaxonomyType('custom')
      setTaxonomyPurpose('')
      setTaxonomyScope('instrument')
      setTaxonomyPlanningEnabled(false)
      setTaxonomyBudgetingLevel('')
      setTaxonomyRootDefaultTargetDimension('weight')
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

  async function handleSaveSelectedTaxonomy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!portfolioId || !selectedTaxonomy) {
      return
    }
    if (taxonomyDetailsPlanningError) {
      setActionError(taxonomyDetailsPlanningError)
      setNotice(null)
      return
    }
    setActionPending(`taxonomy-save-${selectedTaxonomy.taxonomy_id}`)
    setActionError(null)
    setNotice(null)
    try {
      await updatePortfolioTaxonomy(portfolioId, selectedTaxonomy.taxonomy_id, {
        name: selectedTaxonomyName,
        taxonomy_type: selectedTaxonomyType,
        purpose: selectedTaxonomyPurpose || null,
        planning_enabled: selectedTaxonomyPlanningEnabled,
        budgeting_level: selectedTaxonomyPlanningEnabled ? selectedTaxonomyBudgetingLevel || null : null,
        root_default_target_dimension: normalizeRootDefaultTargetDimension(
          selectedTaxonomyPlanningEnabled,
          selectedTaxonomyBudgetingLevel,
          selectedTaxonomyRootDefaultTargetDimension,
        ),
        effective_from: selectedTaxonomyEffectiveFrom || null,
        effective_to: selectedTaxonomyEffectiveTo || null,
        status: selectedTaxonomyStatus,
      })
      setNotice(`Saved taxonomy "${selectedTaxonomyName}".`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleUpdateDefaultPlanningTaxonomy(taxonomyId: string | null) {
    if (!portfolioId) {
      return
    }
    setActionPending(`default-planning-${taxonomyId ?? 'clear'}`)
    setActionError(null)
    setNotice(null)
    try {
      await updatePortfolioDefaultPlanningTaxonomy(portfolioId, { taxonomy_id: taxonomyId })
      setNotice(
        taxonomyId == null
          ? 'Cleared default planning taxonomy.'
          : `Default planning taxonomy set to "${planningTaxonomies.find((item) => item.taxonomy_id === taxonomyId)?.name ?? taxonomyId}".`,
      )
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleDeleteTaxonomy(taxonomy: PortfolioTaxonomyRecord) {
    if (!portfolioId) {
      return
    }
    if (!window.confirm(`Delete taxonomy "${taxonomy.name}"?`)) {
      return
    }
    setActionPending(`taxonomy-delete-${taxonomy.taxonomy_id}`)
    setActionError(null)
    setNotice(null)
    try {
      await deletePortfolioTaxonomy(portfolioId, taxonomy.taxonomy_id)
      if (resolvedSelectedTaxonomyId === taxonomy.taxonomy_id) {
        updateSearchParam('taxonomy_id', null)
      }
      setNotice(`Deleted taxonomy "${taxonomy.name}".`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
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
        node_code: newNodeCode || null,
        parent_taxonomy_node_id: nodeCreateParentId || null,
        sort_order: newNodeSortOrder ? Number(newNodeSortOrder) : null,
        default_target_dimension: newNodeDefaultTargetDimension,
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
        node_code: nodeEditCode || null,
        sort_order: nodeEditSortOrder ? Number(nodeEditSortOrder) : null,
        default_target_dimension: nodeEditDefaultTargetDimension,
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

  async function handleUpdateNodeDefaultTarget(
    node: PortfolioTaxonomyNodeRecord,
    defaultTargetDimension: 'weight' | 'risk_budget',
  ) {
    if (!portfolioId || !selectedTaxonomy || node.default_target_dimension === defaultTargetDimension) {
      return
    }
    setActionPending(`node-default-target-${node.taxonomy_node_id}`)
    setActionError(null)
    setNotice(null)
    try {
      await updatePortfolioTaxonomyNode(portfolioId, selectedTaxonomy.taxonomy_id, node.taxonomy_node_id, {
        default_target_dimension: defaultTargetDimension,
      })
      setNotice(`Updated default target for "${node.node_name}".`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleUpdateRootDefaultTarget(defaultTargetDimension: 'weight' | 'risk_budget') {
    if (!portfolioId || !selectedTaxonomy || selectedTaxonomy.root_default_target_dimension === defaultTargetDimension) {
      return
    }
    setActionPending(`taxonomy-root-default-${selectedTaxonomy.taxonomy_id}`)
    setActionError(null)
    setNotice(null)
    try {
      await updatePortfolioTaxonomy(portfolioId, selectedTaxonomy.taxonomy_id, {
        root_default_target_dimension: defaultTargetDimension,
      })
      setNotice(`Updated root default target for "${selectedTaxonomy.name}".`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleDeleteNode(node: PortfolioTaxonomyNodeRecord) {
    if (!portfolioId || !selectedTaxonomy) {
      return
    }
    if (!window.confirm(`Delete node "${node.node_name}"?`)) {
      return
    }
    setActionPending(`node-delete-${node.taxonomy_node_id}`)
    setActionError(null)
    setNotice(null)
    try {
      await deletePortfolioTaxonomyNode(portfolioId, selectedTaxonomy.taxonomy_id, node.taxonomy_node_id)
      setSelectedNodeId(node.parent_taxonomy_node_id ?? null)
      setNotice(`Deleted node "${node.node_name}".`)
      await reloadWorkspace()
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function assignEntitiesToNode(targetNode: PortfolioTaxonomyNodeRecord, entityIds: string[]) {
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

  function renderEntityTreeRow(entity: CoverageEntity, depth: number) {
    const selected = selectedEntityIds.has(entity.entity_id)
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
      Boolean(selectedTaxonomy?.planning_enabled) &&
      Boolean(entity.current_assignment) &&
      Boolean(targetDraftsByScope[scopeKey])
    const isTargetScopeMember = currentScopeMemberKeySet.has(targetMember.member_key)
    return (
      <tr
        key={entity.entity_id}
        className={[selected ? 'taxonomy-entity-row-active' : '', isTargetScopeMember ? 'taxonomy-scope-row' : '']
          .filter(Boolean)
          .join(' ') || undefined}
        draggable={entity.coverage_state !== 'ambiguous'}
        onDragStart={(event) => handleEntityDragStart(event, entity)}
        onContextMenu={(event) => handleEntityContextMenu(event, entity)}
      >
        <td className="holding-name-cell">
          <div className="taxonomy-node-row taxonomy-hierarchy-row">
            <span style={{ width: `${depth * 18}px`, flex: '0 0 auto' }} />
            <span className="taxonomy-tree-toggle taxonomy-tree-toggle-empty" />
            <label className="taxonomy-tree-check-slot" aria-label={`Select ${entity.label}`}>
              <input type="checkbox" checked={selected} onChange={() => toggleEntitySelection(entity.entity_id)} />
            </label>
            <span className="taxonomy-level-label">{entity.label}</span>
            </div>
          </td>
          <td>—</td>
          <td>{renderTargetCell('saa', 'weight', targetMember, editable)}</td>
          <td>{renderTargetCell('saa', 'risk_budget', targetMember, editable)}</td>
          <td>{renderTargetCell('taa', 'weight', targetMember, editable)}</td>
          <td>{renderTargetCell('taa', 'risk_budget', targetMember, editable)}</td>
          <td>{entity.allocation != null ? formatPercent(entity.allocation) : '—'}</td>
        <td>{entity.market_value_base != null ? formatCurrency(entity.market_value_base, baseCurrency) : '—'}</td>
      </tr>
    )
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
        Boolean(selectedTaxonomy?.planning_enabled) && Boolean(targetDraftsByScope[parentScopeKey])
      const isDropTarget = dragTargetNodeId === node.taxonomy_node_id
      const isCollapsed = collapsedNodeIds.has(node.taxonomy_node_id)
      const rowClassName = [
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
          onContextMenu={(event) => handleNodeContextMenu(event, node)}
          onDragOver={(event) => handleNodeDragOver(event, node)}
          onDragLeave={() => setDragTargetNodeId((current) => (current === node.taxonomy_node_id ? null : current))}
          onDrop={(event) => void handleNodeDrop(event, node)}
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

  return (
    <PortfolioWorkspaceLayout activeSection="Taxonomies" toolbarLabel="Page: Taxonomies">
      <div className="taxonomy-page taxonomy-page-table">
      {notice ? <div className="inline-notice inline-notice-success">{notice}</div> : null}
      {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
      {actionError ? <div className="inline-notice inline-notice-error">{actionError}</div> : null}
      {supplementalNotice ? <div className="inline-notice">{supplementalNotice}</div> : null}
      {loading ? <CalculationStatus /> : null}

      {!loading && !catalog && !workspaceError ? <div className="empty-state">No data.</div> : null}

      {!workspaceError ? (
        <>
          <section className="panel taxonomy-strip-section">
            <div className="taxonomy-topbar">
              <label className="taxonomy-topbar-field">
                <span>Taxonomy</span>
                <select value={resolvedSelectedTaxonomyId} onChange={(event) => handleTaxonomySelection(event.target.value)}>
                  {!taxonomies.length ? <option value="">No taxonomy</option> : null}
                  {taxonomies.map((taxonomy) => (
                    <option key={taxonomy.taxonomy_id} value={taxonomy.taxonomy_id}>
                      {taxonomy.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="taxonomy-header-actions">
                <button
                  type="button"
                  className="toolbar-link button-primary"
                  onClick={() => {
                    setShowTaxonomyDetails(false)
                    setContextMenuState(null)
                    setShowTaxonomyCreate(true)
                  }}
                >
                  Add Taxonomy
                </button>
                {selectedTaxonomy?.planning_enabled ? (
                  <button
                    type="button"
                    className="table-inline-button"
                    onClick={() =>
                      void handleUpdateDefaultPlanningTaxonomy(
                        catalog?.default_planning_taxonomy_id === selectedTaxonomy.taxonomy_id ? null : selectedTaxonomy.taxonomy_id,
                      )
                    }
                  >
                    {catalog?.default_planning_taxonomy_id === selectedTaxonomy.taxonomy_id ? 'Clear Default' : 'Set Default'}
                  </button>
                ) : null}
                {selectedTaxonomy ? (
                  <button
                    type="button"
                    className="table-inline-button"
                    onClick={() => {
                      setShowTaxonomyCreate(false)
                      setContextMenuState(null)
                      setShowTaxonomyDetails(true)
                    }}
                  >
                    Taxonomy Details
                  </button>
                ) : null}
              </div>
            </div>
            <div className="taxonomy-collapsed-summary taxonomy-collapsed-summary-muted">
              <span>{taxonomies.length} taxonomies</span>
              <span>{planningTaxonomies.length} planning enabled</span>
              <span>{defaultPlanningTaxonomy ? `Default: ${defaultPlanningTaxonomy.name}` : 'No default planning axis'}</span>
              {selectedTaxonomy ? <span>Selected: {selectedTaxonomy.name}</span> : null}
              {referenceDate ? <span>As of {referenceDate}</span> : null}
              <span>{coverageSummary.coveragePct == null ? 'Coverage —' : `Coverage ${formatPercent(coverageSummary.coveragePct)}`}</span>
            </div>
          </section>

          {selectedTaxonomy ? (
            <section className="panel taxonomy-levels-section">
              <div className="taxonomy-tree-toolbar taxonomy-tree-toolbar-compact">
                <div className="taxonomy-header-actions">
                  {selectedNode ? (
                    <button type="button" className="table-inline-button" onClick={() => startNodeCreate('sibling', selectedNode)}>
                      Add Same Level
                    </button>
                  ) : null}
                  {selectedNode ? (
                    <button type="button" className="table-inline-button" onClick={() => startNodeCreate('child', selectedNode)}>
                      Add Child
                    </button>
                  ) : null}
                  <button type="button" className="table-inline-button" onClick={expandAllNodes}>
                    Expand All
                  </button>
                  <button
                    type="button"
                    className="table-inline-button"
                    onClick={collapseAllNodes}
                    disabled={collapsibleNodeIds.size === 0}
                  >
                    Collapse All
                  </button>
                </div>
                <div className="taxonomy-header-actions">
                  <span className="portfolio-detail-meta">{currentScopeLabel}</span>
                  {selectedTaxonomy.planning_enabled ? (
                    <>
                      <button
                        type="button"
                        className="table-inline-button"
                        onClick={() => void handleSaveTargetSet('saa')}
                        disabled={!hasTargetScope || !saaValidation.savable || actionPending === 'target-saa-save'}
                      >
                        {actionPending === 'target-saa-save'
                          ? 'Saving SAA…'
                          : `Save SAA · W ${formatDraftSum(saaValidation.weight_sum_pct)} · R ${formatDraftSum(saaValidation.risk_sum_pct)}`}
                      </button>
                      <button
                        type="button"
                        className="table-inline-button"
                        onClick={() => void handleSaveTargetSet('taa')}
                        disabled={!hasTargetScope || !taaValidation.savable || actionPending === 'target-taa-save'}
                      >
                        {actionPending === 'target-taa-save'
                          ? 'Saving TAA…'
                          : `Save TAA · W ${formatDraftSum(taaValidation.weight_sum_pct)} · R ${formatDraftSum(taaValidation.risk_sum_pct)}`}
                      </button>
                    </>
                  ) : null}
                </div>
              </div>

              <div className="taxonomy-collapsed-summary taxonomy-tree-summary">
                <span>{selectedNode ? `Selected: ${selectedNode.node_name}` : 'Selected: Top Level'}</span>
                <span>{coverageSummary.currentEntityCount ? `${coverageSummary.assignedCount} / ${coverageSummary.currentEntityCount} assigned` : 'No current entities'}</span>
                <span>{coverageSummary.unassignedEntities.length} unassigned</span>
              </div>

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
                      <td>{renderRootDefaultTargetCell()}</td>
                      <td>—</td>
                      <td>—</td>
                      <td>—</td>
                      <td>—</td>
                      <td>{taxonomyAssignedSummary.current_weight != null ? formatPercent(taxonomyAssignedSummary.current_weight) : '—'}</td>
                      <td>{taxonomyAssignedSummary.current_value_base != null ? formatCurrency(taxonomyAssignedSummary.current_value_base, baseCurrency) : '—'}</td>
                    </tr>

                    {!collapsedNodeIds.has(TAXONOMY_ROOT_ROW_ID) ? (
                      <>
                        {selectedTaxonomyNodes.length ? (
                          renderNodeTreeRows(null, 1)
                        ) : (
                          <tr className="table-status-row">
                            <td colSpan={8} className="empty-state-cell">
                              <span>No nodes.</span>{' '}
                              <button type="button" className="table-inline-button" onClick={() => startNodeCreate('root')}>
                                Add Root
                              </button>
                            </td>
                          </tr>
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
                              <span className="taxonomy-level-label">Without Classification</span>
                            </div>
                          </td>
                          <td>—</td>
                          <td>—</td>
                          <td>—</td>
                          <td>—</td>
                          <td>—</td>
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

              {selectedTaxonomy.planning_enabled &&
              (saaValidation.errors.length ||
                saaValidation.warnings.length ||
                taaValidation.errors.length ||
                taaValidation.warnings.length) ? (
                <div className="taxonomy-target-validation-grid">
                  <div className="taxonomy-target-validation-row">
                    <span className="taxonomy-target-validation-label">SAA</span>
                    <span className={saaValidation.errors.length ? 'taxonomy-target-validation-error' : saaValidation.warnings.length ? 'taxonomy-target-validation-warning' : 'taxonomy-target-validation-ok'}>
                      {saaValidation.errors.length
                        ? saaValidation.errors[0]
                        : saaValidation.warnings.length
                          ? `${saaValidation.warnings[0]} Current totals: W ${formatDraftSum(saaValidation.weight_sum_pct)} · R ${formatDraftSum(saaValidation.risk_sum_pct)}.`
                          : ''}
                    </span>
                  </div>
                  <div className="taxonomy-target-validation-row">
                    <span className="taxonomy-target-validation-label">TAA</span>
                    <span className={taaValidation.errors.length ? 'taxonomy-target-validation-error' : taaValidation.warnings.length ? 'taxonomy-target-validation-warning' : 'taxonomy-target-validation-ok'}>
                      {taaValidation.errors.length
                        ? taaValidation.errors[0]
                        : taaValidation.warnings.length
                          ? `${taaValidation.warnings[0]} Current totals: W ${formatDraftSum(taaValidation.weight_sum_pct)} · R ${formatDraftSum(taaValidation.risk_sum_pct)}.`
                          : ''}
                    </span>
                  </div>
                </div>
              ) : null}
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
                  <span>Type</span>
                  <select value={taxonomyType} onChange={(event) => setTaxonomyType(event.target.value)}>
                    <option value="custom">Custom</option>
                    <option value="risk_sleeve">Risk Sleeve (Semantic)</option>
                    <option value="instrument_class">Instrument Class</option>
                    <option value="sector">Sector</option>
                    <option value="issuer">Issuer</option>
                    <option value="factor">Factor</option>
                    <option value="region">Region</option>
                  </select>
                </label>
                <label>
                  <span>Assignment Scope</span>
                  <select value={taxonomyScope} onChange={(event) => setTaxonomyScope(event.target.value as TaxonomyAssignmentScope)}>
                    <option value="instrument">Instrument</option>
                    <option value="account">Account</option>
                    <option value="cash_bucket">Cash Bucket</option>
                  </select>
                </label>
                <label>
                  <span>Budgeting Level</span>
                    <select
                      value={taxonomyBudgetingLevel}
                      disabled={taxonomyScope !== 'instrument'}
                      onChange={(event) => {
                        const value = event.target.value
                        setTaxonomyBudgetingLevel(value)
                        if (value) {
                          setTaxonomyPlanningEnabled(true)
                        }
                        setTaxonomyRootDefaultTargetDimension((current) =>
                          normalizeRootDefaultTargetDimension(Boolean(value), value, current),
                        )
                      }}
                    >
                    <option value="">None</option>
                    <option value="weight">Weight</option>
                    <option value="risk_budget">Risk Budget</option>
                    <option value="weight_and_risk_budget">Weight + Risk Budget</option>
                  </select>
                </label>
                <label>
                  <span>Root Default Target</span>
                  <select
                    value={taxonomyRootDefaultTargetDimension}
                    onChange={(event) =>
                      setTaxonomyRootDefaultTargetDimension(event.target.value as 'weight' | 'risk_budget')
                    }
                  >
                    <option value="weight">Weight</option>
                    <option value="risk_budget">Risk Budget</option>
                  </select>
                </label>
                <label className="taxonomy-form-span-2">
                  <span>Purpose</span>
                  <input value={taxonomyPurpose} onChange={(event) => setTaxonomyPurpose(event.target.value)} />
                </label>
                <label className="taxonomy-checkbox-field taxonomy-form-span-2">
                  <span>Planning Enabled</span>
                  <div className="transaction-checkbox-option">
                    <input
                      type="checkbox"
                      checked={taxonomyPlanningEnabled}
                      disabled={taxonomyScope !== 'instrument'}
                      onChange={(event) => {
                        const enabled = event.target.checked
                        setTaxonomyPlanningEnabled(enabled)
                        if (!enabled) {
                          setTaxonomyBudgetingLevel('')
                        }
                        setTaxonomyRootDefaultTargetDimension((current) =>
                          normalizeRootDefaultTargetDimension(enabled, enabled ? taxonomyBudgetingLevel : '', current),
                        )
                      }}
                    />
                    <span>Allow this taxonomy to feed Risk and Review defaults.</span>
                  </div>
                </label>
              </div>
              {taxonomyCreatePlanningError ? (
                <div className="inline-notice inline-notice-error">{taxonomyCreatePlanningError}</div>
              ) : null}
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
            open={showTaxonomyDetails && Boolean(selectedTaxonomy)}
            title="Taxonomy Details"
            onClose={() => setShowTaxonomyDetails(false)}
          >
            {selectedTaxonomy ? (
              <form className="transaction-form taxonomy-form-compact" onSubmit={(event) => void handleSaveSelectedTaxonomy(event)}>
                <div className="taxonomy-form-grid taxonomy-form-grid-wide">
                  <label>
                    <span>Name</span>
                    <input value={selectedTaxonomyName} onChange={(event) => setSelectedTaxonomyName(event.target.value)} required />
                  </label>
                  <label>
                    <span>Type</span>
                    <select value={selectedTaxonomyType} onChange={(event) => setSelectedTaxonomyType(event.target.value)}>
                      <option value="custom">Custom</option>
                      <option value="risk_sleeve">Risk Sleeve (Semantic)</option>
                      <option value="instrument_class">Instrument Class</option>
                      <option value="sector">Sector</option>
                      <option value="issuer">Issuer</option>
                      <option value="factor">Factor</option>
                      <option value="region">Region</option>
                    </select>
                  </label>
                  <label>
                    <span>Status</span>
                    <select value={selectedTaxonomyStatus} onChange={(event) => setSelectedTaxonomyStatus(event.target.value)}>
                      <option value="active">Active</option>
                      <option value="archived">Archived</option>
                    </select>
                  </label>
                  <label>
                    <span>Budgeting Level</span>
                    <select
                      value={selectedTaxonomyBudgetingLevel}
                      disabled={!selectedTaxonomyPlanningEnabled}
                      onChange={(event) => {
                        const value = event.target.value
                        setSelectedTaxonomyBudgetingLevel(value)
                        if (value) {
                          setSelectedTaxonomyPlanningEnabled(true)
                        }
                        setSelectedTaxonomyRootDefaultTargetDimension((current) =>
                          normalizeRootDefaultTargetDimension(Boolean(value), value, current),
                        )
                      }}
                    >
                      <option value="">None</option>
                      <option value="weight">Weight</option>
                      <option value="risk_budget">Risk Budget</option>
                      <option value="weight_and_risk_budget">Weight + Risk Budget</option>
                    </select>
                  </label>
                  <label>
                    <span>Root Default Target</span>
                    <select
                      value={selectedTaxonomyRootDefaultTargetDimension}
                      onChange={(event) =>
                        setSelectedTaxonomyRootDefaultTargetDimension(event.target.value as 'weight' | 'risk_budget')
                      }
                    >
                      <option value="weight">Weight</option>
                      <option value="risk_budget">Risk Budget</option>
                    </select>
                  </label>
                  <label>
                    <span>Effective From</span>
                    <input type="date" value={selectedTaxonomyEffectiveFrom} onChange={(event) => setSelectedTaxonomyEffectiveFrom(event.target.value)} />
                  </label>
                  <label>
                    <span>Effective To</span>
                    <input type="date" value={selectedTaxonomyEffectiveTo} onChange={(event) => setSelectedTaxonomyEffectiveTo(event.target.value)} />
                  </label>
                  <label className="taxonomy-form-span-2">
                    <span>Purpose</span>
                    <input value={selectedTaxonomyPurpose} onChange={(event) => setSelectedTaxonomyPurpose(event.target.value)} />
                  </label>
                  <label className="taxonomy-checkbox-field">
                    <span>Planning Enabled</span>
                    <div className="transaction-checkbox-option">
                      <input
                        type="checkbox"
                        checked={selectedTaxonomyPlanningEnabled}
                        disabled={selectedTaxonomy.primary_assignment_scope !== 'instrument'}
                        onChange={(event) => {
                          const enabled = event.target.checked
                          setSelectedTaxonomyPlanningEnabled(enabled)
                          if (!enabled) {
                            setSelectedTaxonomyBudgetingLevel('')
                          }
                          setSelectedTaxonomyRootDefaultTargetDimension((current) =>
                            normalizeRootDefaultTargetDimension(
                              enabled,
                              enabled ? selectedTaxonomyBudgetingLevel : '',
                              current,
                            ),
                          )
                        }}
                      />
                      <span>Planning Axis</span>
                    </div>
                  </label>
                </div>
                {taxonomyDetailsPlanningError ? (
                  <div className="inline-notice inline-notice-error">{taxonomyDetailsPlanningError}</div>
                ) : null}
                <div className="transaction-form-footer">
                  <div className="taxonomy-footer-actions">
                    <button
                      type="button"
                      className="toolbar-link"
                      onClick={() => void handleDeleteTaxonomy(selectedTaxonomy)}
                      disabled={actionPending === `taxonomy-delete-${selectedTaxonomy.taxonomy_id}`}
                    >
                      Delete Taxonomy
                    </button>
                    <button
                      type="submit"
                      className="toolbar-link button-primary"
                      disabled={actionPending === `taxonomy-save-${selectedTaxonomy.taxonomy_id}`}
                    >
                      {actionPending === `taxonomy-save-${selectedTaxonomy.taxonomy_id}` ? 'Saving…' : 'Save Taxonomy'}
                    </button>
                  </div>
                </div>
              </form>
            ) : null}
          </TaxonomyModal>
          <TaxonomyModal
            open={showNodeCreate}
            title={nodeCreateContextLabel}
            onClose={() => setShowNodeCreate(false)}
          >
            <form className="transaction-form taxonomy-form-compact" onSubmit={(event) => void handleCreateNode(event)}>
              <div className="taxonomy-form-grid taxonomy-inline-create-grid">
                <label>
                  <span>Node Name</span>
                  <input value={newNodeName} onChange={(event) => setNewNodeName(event.target.value)} required />
                </label>
                <label>
                  <span>Node Code</span>
                  <input value={newNodeCode} onChange={(event) => setNewNodeCode(event.target.value)} />
                </label>
                <label>
                  <span>Sort Order</span>
                  <input
                    type="number"
                    value={newNodeSortOrder}
                    placeholder="Auto"
                    onChange={(event) => setNewNodeSortOrder(event.target.value)}
                  />
                </label>
                <label>
                  <span>Default Target</span>
                  <select
                    value={newNodeDefaultTargetDimension}
                    onChange={(event) => setNewNodeDefaultTargetDimension(event.target.value as 'weight' | 'risk_budget')}
                  >
                    <option value="weight">Weight</option>
                    <option value="risk_budget">Risk Budget</option>
                  </select>
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
                <div className="taxonomy-form-grid taxonomy-inline-create-grid">
                  <label>
                    <span>Node Name</span>
                    <input value={nodeEditName} onChange={(event) => setNodeEditName(event.target.value)} required />
                  </label>
                  <label>
                    <span>Node Code</span>
                    <input value={nodeEditCode} onChange={(event) => setNodeEditCode(event.target.value)} />
                  </label>
                  <label>
                    <span>Sort Order</span>
                    <input
                      type="number"
                      value={nodeEditSortOrder}
                      onChange={(event) => setNodeEditSortOrder(event.target.value)}
                    />
                  </label>
                  <label>
                    <span>Default Target</span>
                    <select
                      value={nodeEditDefaultTargetDimension}
                      onChange={(event) => setNodeEditDefaultTargetDimension(event.target.value as 'weight' | 'risk_budget')}
                    >
                      <option value="weight">Weight</option>
                      <option value="risk_budget">Risk Budget</option>
                    </select>
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
                      {actionPending === `node-save-${editingNode.taxonomy_node_id}` ? 'Saving…' : 'Save Node'}
                    </button>
                  </div>
                </div>
              </form>
            ) : null}
          </TaxonomyModal>
          {contextMenuState ? (
            <div
              className="taxonomy-context-menu"
              style={{ left: contextMenuState.x, top: contextMenuState.y }}
              onClick={(event) => event.stopPropagation()}
            >
              {contextMenuNode ? (
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
                      disabled={selectedEntityCount === 0}
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
                    disabled={!selectedNode?.is_terminal}
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
                </>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
      </div>
    </PortfolioWorkspaceLayout>
  )
}
