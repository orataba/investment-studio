import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  createPortfolioResearchRun,
  getPortfolioTaxonomyCatalog,
  getPortfolioResearchWorkbench,
  updatePortfolioResearchSettings,
  type PortfolioResearchCapitalMode,
  type PortfolioResearchPlanningScopeOption,
  type PortfolioResearchRunRecord,
  type PortfolioResearchTargetDimension,
  type PortfolioResearchWorkbenchResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioTaxonomyRecord,
} from '../lib/api'
import {
  formatLabel,
  formatPercent,
} from '../lib/format'

const LOOKBACK_OPTIONS = [90, 180, 366, 730] as const
const TARGET_DIMENSION_OPTIONS = [
  { value: 'scope_default', label: 'Scope Default' },
  { value: 'weight', label: 'Weight' },
  { value: 'risk_budget', label: 'Risk Budget' },
] as const
const CAPITAL_MODE_OPTIONS = [
  { value: 'unit_notional', label: 'Unit Notional' },
  { value: 'fixed_gross', label: 'Fixed Gross' },
  { value: 'target_volatility', label: 'Target Volatility' },
] as const

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

function extractErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Request failed.'
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return '—'
  }
  return value.replace('T', ' ').replace('Z', ' UTC')
}

function resolveStatusLabel(status: string) {
  if (status === 'completed') {
    return 'Completed'
  }
  if (status === 'failed') {
    return 'Failed'
  }
  if (status === 'running') {
    return 'Running'
  }
  return formatLabel(status)
}

function formatResearchDimension(value: string | null | undefined) {
  if (!value) {
    return '—'
  }
  return formatLabel(value)
}

function formatSolverKind(value: string | null | undefined) {
  if (!value) {
    return '—'
  }
  if (value === 'weight') {
    return 'Weight'
  }
  if (value === 'risk-budget') {
    return 'Risk Budget'
  }
  if (value === 'single-member') {
    return 'Single Member'
  }
  if (value === 'fallback-insufficient-history') {
    return 'Fallback: Insufficient History'
  }
  if (value === 'fallback-solver') {
    return 'Fallback: Solver'
  }
  return formatLabel(value.replace(/-/g, '_'))
}

function sortTaxonomyNodes(nodes: PortfolioTaxonomyNodeRecord[]) {
  return [...nodes].sort((left, right) => {
    if (left.sort_order !== right.sort_order) {
      return left.sort_order - right.sort_order
    }
    return left.node_name.localeCompare(right.node_name) || left.taxonomy_node_id.localeCompare(right.taxonomy_node_id)
  })
}

function buildPlanningScopeOptions(
  taxonomy: PortfolioTaxonomyRecord,
  nodes: PortfolioTaxonomyNodeRecord[],
): PortfolioResearchPlanningScopeOption[] {
  const activeNodes = sortTaxonomyNodes(
    nodes.filter((node) => node.taxonomy_id === taxonomy.taxonomy_id && node.status === 'active'),
  )
  const childrenByParent = new Map<string | null, PortfolioTaxonomyNodeRecord[]>()
  activeNodes.forEach((node) => {
    const parentKey = node.parent_taxonomy_node_id ?? null
    const currentChildren = childrenByParent.get(parentKey) ?? []
    currentChildren.push(node)
    childrenByParent.set(parentKey, currentChildren)
  })

  const options: PortfolioResearchPlanningScopeOption[] = [
    {
      taxonomy_node_id: null,
      label: 'Top Level',
      path: 'Top Level',
      depth: 0,
      default_target_dimension: taxonomy.root_default_target_dimension ?? 'weight',
      has_children: Boolean(childrenByParent.get(null)?.length),
    },
  ]
  const visited = new Set<string>()

  function appendNode(node: PortfolioTaxonomyNodeRecord, parentPath: string, depth: number) {
    if (visited.has(node.taxonomy_node_id)) {
      return
    }
    visited.add(node.taxonomy_node_id)
    const path = `${parentPath} / ${node.node_name}`
    options.push({
      taxonomy_node_id: node.taxonomy_node_id,
      label: node.node_name,
      path,
      depth,
      default_target_dimension: node.default_target_dimension ?? 'weight',
      has_children: Boolean(childrenByParent.get(node.taxonomy_node_id)?.length),
    })
    ;(childrenByParent.get(node.taxonomy_node_id) ?? []).forEach((child) => appendNode(child, path, depth + 1))
  }

  ;(childrenByParent.get(null) ?? []).forEach((node) => appendNode(node, 'Top Level', 1))
  activeNodes.forEach((node) => appendNode(node, 'Top Level', 1))
  return options
}

export default function ResearchPage() {
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [workbench, setWorkbench] = useState<PortfolioResearchWorkbenchResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [actionPending, setActionPending] = useState<'save' | 'run' | null>(null)
  const [dynamicScopeOptions, setDynamicScopeOptions] = useState<PortfolioResearchPlanningScopeOption[] | null>(null)
  const [scopeOptionsLoading, setScopeOptionsLoading] = useState(false)
  const [scopeOptionsError, setScopeOptionsError] = useState<string | null>(null)

  const selectedRunId = searchParams.get('run_id') ?? ''

  const [planningTaxonomyId, setPlanningTaxonomyId] = useState('')
  const [comparatorScopeId, setComparatorScopeId] = useState('')
  const [asOfDate, setAsOfDate] = useState('')
  const [lookbackDays, setLookbackDays] = useState(String(LOOKBACK_OPTIONS[2]))
  const [targetDimension, setTargetDimension] = useState<PortfolioResearchTargetDimension>('scope_default')
  const [capitalMode, setCapitalMode] = useState<PortfolioResearchCapitalMode>('unit_notional')
  const [grossExposure, setGrossExposure] = useState('')
  const [targetVolatilityPct, setTargetVolatilityPct] = useState('')
  const [maxGrossExposure, setMaxGrossExposure] = useState('')
  const [notes, setNotes] = useState('')

  function updateSearchParams(updates: Record<string, string | null>) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      let changed = false
      Object.entries(updates).forEach(([key, value]) => {
        const normalized = value && value.trim() ? value : null
        const currentValue = current.get(key)
        if (normalized === currentValue || (!normalized && !currentValue)) {
          return
        }
        changed = true
        if (normalized) {
          next.set(key, normalized)
        } else {
          next.delete(key)
        }
      })
      return changed ? next : current
    })
  }

  async function reloadWorkbench(nextRunId?: string | null) {
    if (!portfolioId) {
      setWorkbench(null)
      setLoading(false)
      setWorkspaceError(null)
      return
    }

    setLoading(true)
    try {
      const response = await getPortfolioResearchWorkbench(portfolioId, nextRunId || undefined)
      setWorkbench(response)
      setWorkspaceError(null)
    } catch (error) {
      setWorkbench(null)
      setWorkspaceError(extractErrorMessage(error))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void reloadWorkbench(selectedRunId || null)
  }, [portfolioId, selectedRunId])

  useEffect(() => {
    if (!workbench) {
      return
    }
    setPlanningTaxonomyId(workbench.settings.planning_taxonomy_id ?? '')
    setComparatorScopeId(workbench.settings.comparator_taxonomy_node_id ?? '')
    setAsOfDate(workbench.settings.as_of_date ?? workbench.as_of_date)
    setLookbackDays(String(workbench.settings.lookback_days))
    setTargetDimension(workbench.settings.target_dimension)
    setCapitalMode(workbench.settings.capital_mode)
    setGrossExposure(workbench.settings.gross_exposure != null ? String(workbench.settings.gross_exposure) : '')
    setTargetVolatilityPct(
      workbench.settings.target_volatility != null ? String(workbench.settings.target_volatility * 100) : '',
    )
    setMaxGrossExposure(
      workbench.settings.max_gross_exposure != null ? String(workbench.settings.max_gross_exposure) : '',
    )
    setNotes(workbench.settings.notes ?? '')
  }, [workbench])

  useEffect(() => {
    if (!workbench) {
      return
    }
    if (planningTaxonomyId !== (workbench.settings.planning_taxonomy_id ?? '')) {
      setComparatorScopeId('')
    }
  }, [planningTaxonomyId, workbench])

  useEffect(() => {
    const savedPlanningTaxonomyId = workbench?.settings.planning_taxonomy_id ?? ''
    if (!portfolioId || !workbench || !planningTaxonomyId || planningTaxonomyId === savedPlanningTaxonomyId) {
      setDynamicScopeOptions(null)
      setScopeOptionsLoading(false)
      setScopeOptionsError(null)
      return
    }

    let cancelled = false
    setScopeOptionsLoading(true)
    setScopeOptionsError(null)

    getPortfolioTaxonomyCatalog(portfolioId)
      .then((catalog) => {
        if (cancelled) {
          return
        }
        const taxonomy = catalog.taxonomies.find(
          (item) => item.taxonomy_id === planningTaxonomyId && item.planning_enabled,
        )
        if (!taxonomy) {
          throw new Error('Selected planning taxonomy is unavailable.')
        }
        setDynamicScopeOptions(buildPlanningScopeOptions(taxonomy, catalog.taxonomy_nodes))
      })
      .catch((error) => {
        if (!cancelled) {
          setDynamicScopeOptions(null)
          setScopeOptionsError(extractErrorMessage(error))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setScopeOptionsLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [planningTaxonomyId, portfolioId, workbench])

  const selectedRun = workbench?.selected_run ?? null

  const selectedScopeOptions = useMemo<PortfolioResearchPlanningScopeOption[]>(() => {
    if (!workbench) {
      return []
    }
    if (!planningTaxonomyId) {
      return []
    }
    if (planningTaxonomyId === (workbench.settings.planning_taxonomy_id ?? '')) {
      return workbench.planning_scope_options
    }
    return dynamicScopeOptions ?? []
  }, [dynamicScopeOptions, planningTaxonomyId, workbench])

  const selectedRunSignalMap = useMemo(() => {
    const entries = (selectedRun?.detail?.signals ?? []).map((signal) => [signal.label, signal.value] as const)
    return new Map(entries)
  }, [selectedRun])
  const memberTargets = selectedRun?.detail?.member_targets ?? []
  const leafTargets = selectedRun?.detail?.leaf_targets ?? []
  const solvedTargets = leafTargets.length ? leafTargets : memberTargets
  const solveEvent = selectedRun?.detail?.solve_event ?? null
  const savedPlanningTaxonomyId = workbench?.settings.planning_taxonomy_id ?? ''
  const scopeOptionsPending = Boolean(
    planningTaxonomyId && planningTaxonomyId !== savedPlanningTaxonomyId && !dynamicScopeOptions && !scopeOptionsError,
  )
  const scopeActionBlocked = scopeOptionsLoading || scopeOptionsPending || Boolean(scopeOptionsError)

  const summaryPlanningName =
    workbench?.planning_taxonomy_options.find((item) => item.taxonomy_id === planningTaxonomyId)?.name ??
    workbench?.settings.planning_taxonomy_name ??
    'Not configured'

  const summaryScopeName =
    selectedScopeOptions.find((item) => (item.taxonomy_node_id ?? '') === comparatorScopeId)?.label ??
    workbench?.settings.comparator_taxonomy_node_name ??
    'Top Level'

  async function persistSettings() {
    if (!portfolioId) {
      return null
    }
    const parsedGrossExposure = grossExposure.trim() ? Number(grossExposure) : null
    const parsedTargetVolatility = targetVolatilityPct.trim() ? Number(targetVolatilityPct) / 100 : null
    const parsedMaxGrossExposure = maxGrossExposure.trim() ? Number(maxGrossExposure) : null
    const frozenTaxonomyNodeIds =
      planningTaxonomyId === savedPlanningTaxonomyId ? (workbench?.settings.frozen_taxonomy_node_ids ?? []) : []
    return updatePortfolioResearchSettings(portfolioId, {
      planning_taxonomy_id: planningTaxonomyId || null,
      comparator_taxonomy_node_id: comparatorScopeId || null,
      as_of_date: asOfDate || null,
      lookback_days: Number(lookbackDays) || 90,
      target_dimension: targetDimension,
      capital_mode: capitalMode,
      gross_exposure: capitalMode === 'fixed_gross' ? parsedGrossExposure : null,
      target_volatility: capitalMode === 'target_volatility' ? parsedTargetVolatility : null,
      max_gross_exposure: capitalMode === 'target_volatility' ? parsedMaxGrossExposure : null,
      frozen_taxonomy_node_ids: frozenTaxonomyNodeIds,
      notes: notes || null,
    })
  }

  async function handleSaveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!portfolioId) {
      return
    }

    setActionPending('save')
    setActionError(null)
    setNotice(null)
    try {
      await persistSettings()
      setNotice('Research settings updated.')
      await reloadWorkbench(selectedRunId || null)
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  async function handleRunResearch() {
    if (!portfolioId) {
      return
    }

    setActionPending('run')
    setActionError(null)
    setNotice(null)
    try {
      await persistSettings()
      const run = await createPortfolioResearchRun(portfolioId, { requested_by: 'workspace-ui' })
      updateSearchParams({
        run_id: run.research_run_id,
      })
      setNotice('Research run completed.')
      await reloadWorkbench(run.research_run_id)
    } catch (error) {
      setActionError(extractErrorMessage(error))
    } finally {
      setActionPending(null)
    }
  }

  function handleSelectRun(run: PortfolioResearchRunRecord) {
    updateSearchParams({
      run_id: run.research_run_id,
    })
  }

  return (
    <PortfolioWorkspaceLayout activeSection="Research" toolbarLabel="View: Research Workbench">
      {notice ? <div className="inline-notice inline-notice-success">{notice}</div> : null}
      {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
      {actionError ? <div className="inline-notice inline-notice-error">{actionError}</div> : null}
      {loading && !workbench ? (
        <CalculationStatus label="Building research workbench from current holdings, planning context, and run history…" />
      ) : null}

      {!loading && !workbench && !workspaceError ? (
        <div className="empty-state">No research workbench is available for this portfolio.</div>
      ) : null}

      {workbench ? (
        <>
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Research</div>
              </div>
              <div className="portfolio-detail-meta">
                {workbench.portfolio_name} · target weights as of {workbench.settings.as_of_date ?? workbench.as_of_date}
              </div>
            </div>
            <div className="performance-summary-grid research-context-grid">
              <div className="table-shell">
                <table className="performance-summary-table">
                  <tbody>
                    <tr>
                      <th>Planning Axis</th>
                      <td>{summaryPlanningName}</td>
                    </tr>
                    <tr>
                      <th>Scope</th>
                      <td>{summaryScopeName}</td>
                    </tr>
                    <tr>
                      <th>Target Layer</th>
                      <td>Active TAA</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className="table-shell">
                <table className="performance-summary-table">
                  <tbody>
                    <tr>
                      <th>Target Dimension</th>
                      <td>{formatLabel(targetDimension)}</td>
                    </tr>
                    <tr>
                      <th>Lookback</th>
                      <td>{lookbackDays}D</td>
                    </tr>
                    <tr>
                      <th>Runs</th>
                      <td>{workbench.runs.length}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Run Setup</div>
              </div>
            </div>
            <form className="transaction-form taxonomy-form-compact" onSubmit={(event) => void handleSaveSettings(event)}>
              <div className="taxonomy-form-grid taxonomy-form-grid-wide research-settings-grid">
                <label>
                  <span>Planning Taxonomy</span>
                  <select
                    value={planningTaxonomyId}
                    onChange={(event) => {
                      setDynamicScopeOptions(null)
                      setScopeOptionsError(null)
                      setPlanningTaxonomyId(event.target.value)
                    }}
                  >
                    <option value="">None</option>
                    {workbench.planning_taxonomy_options.map((taxonomy) => (
                      <option key={taxonomy.taxonomy_id} value={taxonomy.taxonomy_id}>
                        {taxonomy.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Scope</span>
                  <select
                    value={comparatorScopeId}
                    onChange={(event) => setComparatorScopeId(event.target.value)}
                    disabled={!planningTaxonomyId || scopeOptionsLoading}
                  >
                    <option value="">Top Level</option>
                    {selectedScopeOptions
                      .filter((option) => option.taxonomy_node_id)
                      .map((option) => (
                        <option key={option.taxonomy_node_id ?? option.path} value={option.taxonomy_node_id ?? ''}>
                          {option.path}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  <span>As Of Date</span>
                  <input type="date" value={asOfDate} onChange={(event) => setAsOfDate(event.target.value)} />
                </label>
                <label>
                  <span>Lookback</span>
                  <select value={lookbackDays} onChange={(event) => setLookbackDays(event.target.value)}>
                    {LOOKBACK_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {option}D
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Target Dimension</span>
                  <select
                    value={targetDimension}
                    onChange={(event) => setTargetDimension(event.target.value as PortfolioResearchTargetDimension)}
                  >
                    {TARGET_DIMENSION_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Capital Mode</span>
                  <select
                    value={capitalMode}
                    onChange={(event) => setCapitalMode(event.target.value as PortfolioResearchCapitalMode)}
                  >
                    {CAPITAL_MODE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Target Vol (%)</span>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    value={targetVolatilityPct}
                    onChange={(event) => setTargetVolatilityPct(event.target.value)}
                    disabled={capitalMode !== 'target_volatility'}
                    placeholder="7.0"
                  />
                </label>
                <label>
                  <span>Gross Exposure</span>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={grossExposure}
                    onChange={(event) => setGrossExposure(event.target.value)}
                    disabled={capitalMode !== 'fixed_gross'}
                    placeholder="1.00"
                  />
                </label>
                <label>
                  <span>Max Gross</span>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={maxGrossExposure}
                    onChange={(event) => setMaxGrossExposure(event.target.value)}
                    disabled={capitalMode !== 'target_volatility'}
                    placeholder="1.00"
                  />
                </label>
                <label className="taxonomy-form-span-2 transaction-notes-field">
                  <span>Notes</span>
                  <textarea
                    value={notes}
                    onChange={(event) => setNotes(event.target.value)}
                    placeholder=""
                  />
                </label>
              </div>
              {scopeOptionsError ? <div className="inline-notice inline-notice-error">{scopeOptionsError}</div> : null}
              <div className="transaction-form-footer">
                <span className="portfolio-detail-meta">
                  {scopeOptionsLoading
                    ? 'Loading scope tree for the selected planning taxonomy.'
                    : 'Research resolves current target weights from holdings, taxonomy, active targets, covariance lookback, and capital overlay.'}
                </span>
                <div className="toolbar">
                  <button type="submit" className="toolbar-link" disabled={actionPending === 'save' || scopeActionBlocked}>
                    {actionPending === 'save' ? 'Saving…' : 'Save Settings'}
                  </button>
                  <button
                    type="button"
                    className="toolbar-link button-primary"
                    onClick={() => void handleRunResearch()}
                    disabled={actionPending === 'run' || scopeActionBlocked}
                  >
                    {actionPending === 'run' ? 'Running…' : 'Run Research'}
                  </button>
                </div>
              </div>
            </form>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Run History</div>
              </div>
            </div>
            <div className="table-shell">
              <table className="transactions-table">
                <thead>
                  <tr>
                    <th>Requested</th>
                    <th>Status</th>
                    <th>Scope</th>
                    <th>Window</th>
                    <th>Mode</th>
                    <th>Headline</th>
                  </tr>
                </thead>
                <tbody>
                  {!workbench.runs.length ? (
                    <TableStatusRow colSpan={6} label="No research runs have been recorded for this portfolio yet." />
                  ) : (
                    workbench.runs.map((run) => (
                      <tr
                        key={run.research_run_id}
                        className={selectedRun?.research_run_id === run.research_run_id ? 'research-run-row-active' : ''}
                        onClick={() => handleSelectRun(run)}
                      >
                        <td>{formatTimestamp(run.requested_at)}</td>
                        <td>{resolveStatusLabel(run.status)}</td>
                        <td>{run.detail?.selected_scope?.label ?? 'Top Level'}</td>
                        <td>{run.as_of_date ?? '—'} · {run.lookback_days}D</td>
                        <td>{run.detail?.signals.find((signal) => signal.label === 'Target Layer')?.value ?? '—'}</td>
                        <td className="transaction-note-cell">{run.headline ?? '—'}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Selected Run</div>
              </div>
              <div className="portfolio-detail-meta">
                {selectedRun ? `${selectedRun.research_run_id} · ${resolveStatusLabel(selectedRun.status)}` : 'No run selected'}
              </div>
            </div>
            {!selectedRun ? (
              <div className="empty-state">Run Research above, then inspect the selected run here.</div>
            ) : (
              <>
                <div className="research-run-headline-band">
                  <strong>{selectedRun.detail?.headline ?? selectedRun.headline ?? 'Research run detail'}</strong>
                  <span>{selectedRun.detail?.coverage_note ?? 'Current target weight solve.'}</span>
                </div>
                {selectedRun.error_message ? (
                  <div className="inline-notice inline-notice-error">{selectedRun.error_message}</div>
                ) : null}

                <div className="performance-summary-grid research-detail-grid">
                  <div className="table-shell">
                    <table className="performance-summary-table">
                      <tbody>
                        <tr>
                          <th>Requested</th>
                          <td>{formatTimestamp(selectedRun.requested_at)}</td>
                        </tr>
                        <tr>
                          <th>As Of Date</th>
                          <td>{selectedRun.as_of_date ?? '—'}</td>
                        </tr>
                        <tr>
                          <th>Scope</th>
                          <td>{selectedRun.detail?.selected_scope?.path ?? 'Top Level'}</td>
                        </tr>
                        <tr>
                          <th>Target Dimension</th>
                          <td>{selectedRunSignalMap.get('Target Dimension') ?? formatResearchDimension(solveEvent?.target_dimension)}</td>
                        </tr>
                        <tr>
                          <th>Solver</th>
                          <td>{selectedRunSignalMap.get('Solver') ?? formatSolverKind(solveEvent?.solver_detail ?? solveEvent?.solver_kind)}</td>
                        </tr>
                        <tr>
                          <th>Covariance</th>
                          <td>
                            {solveEvent?.covariance_model
                              ? `${formatLabel(solveEvent.covariance_model)} · ${solveEvent.covariance_observations ?? 0}`
                              : '—'}
                          </td>
                        </tr>
                        <tr>
                          <th>RC Mode</th>
                          <td>{solveEvent?.risk_contribution_mode ? formatLabel(solveEvent.risk_contribution_mode) : '—'}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div className="table-shell">
                    <table className="performance-summary-table">
                      <tbody>
                        <tr>
                          <th>Target Volatility</th>
                          <td>{selectedRunSignalMap.get('Target Volatility') ?? '—'}</td>
                        </tr>
                        <tr>
                          <th>Estimated Volatility</th>
                          <td>{selectedRunSignalMap.get('Estimated Volatility') ?? '—'}</td>
                        </tr>
                        <tr>
                          <th>Gross Exposure</th>
                          <td>{selectedRunSignalMap.get('Gross Exposure') ?? '—'}</td>
                        </tr>
                        <tr>
                          <th>Largest Weight Gap</th>
                          <td>{selectedRunSignalMap.get('Largest Weight Gap') ?? '—'}</td>
                        </tr>
                        <tr>
                          <th>Largest Risk Gap</th>
                          <td>{selectedRunSignalMap.get('Largest Risk Gap') ?? '—'}</td>
                        </tr>
                        <tr>
                          <th>Rows</th>
                          <td>{solvedTargets.length}</td>
                        </tr>
                        <tr>
                          <th>Warnings</th>
                          <td>{selectedRun.detail?.warnings.length ?? 0}</td>
                        </tr>
                        <tr>
                          <th>Lookback</th>
                          <td>{selectedRunSignalMap.get('Lookback') ?? '—'}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="performance-section-block">
                  <div className="panel-header panel-header-inline">
                    <div>
                      <div className="panel-title">Solved Target Weights</div>
                    </div>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th>Member</th>
                          <th>Scope</th>
                          <th>Current Weight</th>
                          <th>Target Weight</th>
                          <th>Gap</th>
                          <th>Local RC</th>
                          <th>Local Risk Target</th>
                          <th>Target Dim</th>
                          <th>Source</th>
                        </tr>
                      </thead>
                      <tbody>
                        {!solvedTargets.length ? (
                          <TableStatusRow colSpan={9} label="No solved target weights were recorded for the selected run." />
                        ) : (
                          solvedTargets.map((member) => (
                            <tr key={`${member.scope_path ?? 'Top Level'}:${member.member_type}:${member.member_id}`}>
                              <td>{member.label}</td>
                              <td>{member.scope_path ?? 'Top Level'}</td>
                              <td>{formatPercent(member.current_weight)}</td>
                              <td>{formatPercent(member.target_weight)}</td>
                              <td>{formatPercent(member.weight_change, 3)}</td>
                              <td>{formatPercent(member.current_risk_share)}</td>
                              <td>{formatPercent(member.configured_risk_share)}</td>
                              <td>{formatResearchDimension(member.selected_target_dimension ?? member.default_target_dimension)}</td>
                              <td>{member.source_target_set_type ? formatLabel(member.source_target_set_type) : '—'}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                {selectedRun.detail?.warnings.length ? (
                  <div className="performance-section-block">
                    <div className="panel-header panel-header-inline">
                      <div>
                        <div className="panel-title">Warnings</div>
                      </div>
                    </div>
                    <div className="table-shell">
                      <table className="transactions-table">
                        <thead>
                          <tr>
                            <th>Message</th>
                          </tr>
                        </thead>
                        <tbody>
                          {selectedRun.detail.warnings.map((warning) => (
                            <tr key={warning}>
                              <td className="transaction-note-cell">{warning}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : null}
              </>
            )}
          </section>
        </>
      ) : null}
    </PortfolioWorkspaceLayout>
  )
}
