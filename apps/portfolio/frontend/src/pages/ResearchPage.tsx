import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PerformanceNavChart from '../components/PerformanceNavChart'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  createPortfolioResearchRun,
  getPortfolioResearchArtifactContent,
  getPortfolioResearchWorkbench,
  updatePortfolioResearchSettings,
  type PortfolioResearchArtifactContentResponse,
  type PortfolioResearchArtifactRecord,
  type PortfolioResearchBacktestMetricRecord,
  type PortfolioResearchPlanningScopeOption,
  type PortfolioResearchRunRecord,
  type PortfolioResearchTargetDimension,
  type PortfolioResearchWorkbenchResponse,
} from '../lib/api'
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatPercent,
} from '../lib/format'

const LOOKBACK_OPTIONS = [30, 60, 90, 180] as const
const TARGET_SET_OPTIONS = [
  { value: 'taa_over_saa', label: 'TAA over SAA' },
  { value: 'saa', label: 'SAA only' },
] as const
const TARGET_DIMENSION_OPTIONS = [
  { value: 'scope_default', label: 'Scope Default' },
  { value: 'weight', label: 'Weight' },
  { value: 'risk_budget', label: 'Risk Budget' },
] as const
const REBALANCE_OPTIONS = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
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

function renderBacktestMetric(metric: PortfolioResearchBacktestMetricRecord) {
  if (metric.metric_id === 'observations') {
    return formatNumber(metric.value, 0)
  }
  if (
    metric.metric_id === 'cumulative_return' ||
    metric.metric_id === 'annualized_return' ||
    metric.metric_id === 'annualized_volatility' ||
    metric.metric_id === 'max_drawdown'
  ) {
    return formatPercent(metric.value)
  }
  return formatNumber(metric.value, 3)
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
  const [artifactContent, setArtifactContent] = useState<PortfolioResearchArtifactContentResponse | null>(null)
  const [artifactLoading, setArtifactLoading] = useState(false)
  const [artifactError, setArtifactError] = useState<string | null>(null)

  const selectedRunId = searchParams.get('run_id') ?? ''
  const selectedArtifactPath = searchParams.get('artifact_path') ?? ''

  const [planningTaxonomyId, setPlanningTaxonomyId] = useState('')
  const [comparatorScopeId, setComparatorScopeId] = useState('')
  const [asOfDate, setAsOfDate] = useState('')
  const [startDate, setStartDate] = useState('')
  const [lookbackDays, setLookbackDays] = useState(String(LOOKBACK_OPTIONS[2]))
  const [targetSetMode, setTargetSetMode] = useState<'saa' | 'taa_over_saa'>('taa_over_saa')
  const [targetDimension, setTargetDimension] = useState<PortfolioResearchTargetDimension>('scope_default')
  const [rebalanceFrequency, setRebalanceFrequency] = useState<'weekly' | 'monthly' | 'quarterly'>('monthly')
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
    setStartDate(workbench.settings.start_date ?? workbench.as_of_date)
    setLookbackDays(String(workbench.settings.lookback_days))
    setTargetSetMode(workbench.settings.target_set_mode)
    setTargetDimension(workbench.settings.target_dimension)
    setRebalanceFrequency(workbench.settings.rebalance_frequency)
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

  const selectedRun = workbench?.selected_run ?? null
  const selectedArtifact = useMemo(() => {
    if (!selectedRun) {
      return null
    }
    return (
      selectedRun.artifacts.find((artifact) => artifact.path === selectedArtifactPath) ??
      selectedRun.artifacts[0] ??
      null
    )
  }, [selectedArtifactPath, selectedRun])

  useEffect(() => {
    if (!selectedRun) {
      if (selectedArtifactPath) {
        updateSearchParams({ artifact_path: null })
      }
      return
    }
    if (selectedArtifact && selectedArtifact.path === selectedArtifactPath) {
      return
    }
    updateSearchParams({ artifact_path: selectedArtifact?.path ?? null })
  }, [selectedArtifact, selectedArtifactPath, selectedRun])

  useEffect(() => {
    if (!portfolioId || !selectedArtifact) {
      setArtifactContent(null)
      setArtifactLoading(false)
      setArtifactError(null)
      return
    }

    if (selectedArtifact.preview_kind === 'binary') {
      setArtifactContent(null)
      setArtifactLoading(false)
      setArtifactError('Binary artifact preview is not available in the workspace yet.')
      return
    }

    let cancelled = false
    setArtifactLoading(true)

    getPortfolioResearchArtifactContent(portfolioId, selectedArtifact.path)
      .then((response) => {
        if (!cancelled) {
          setArtifactContent(response)
          setArtifactError(null)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setArtifactContent(null)
          setArtifactError(extractErrorMessage(error))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setArtifactLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId, selectedArtifact])

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
    return [
      {
        taxonomy_node_id: null,
        label: 'Top Level',
        path: 'Top Level',
        depth: 0,
        default_target_dimension: 'weight',
        has_children: false,
      },
    ]
  }, [planningTaxonomyId, workbench])

  const currentContext = workbench?.current_context ?? null
  const currentChartPoints = useMemo(
    () =>
      (currentContext?.chart_points ?? [])
        .filter((point) => point.value != null)
        .map((point) => ({ date: point.date, value: point.value as number })),
    [currentContext],
  )

  const selectedRunCurvePoints = useMemo(
    () => (selectedRun?.detail?.backtest_curve ?? []).map((point) => ({ date: point.date, value: point.nav })),
    [selectedRun],
  )

  const selectedRunMetricMap = useMemo(() => {
    const entries = (selectedRun?.detail?.backtest_metrics ?? []).map((metric) => [metric.metric_id, metric] as const)
    return new Map(entries)
  }, [selectedRun])

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
    return updatePortfolioResearchSettings(portfolioId, {
      planning_taxonomy_id: planningTaxonomyId || null,
      comparator_taxonomy_node_id: comparatorScopeId || null,
      as_of_date: asOfDate || null,
      start_date: startDate || null,
      lookback_days: Number(lookbackDays) || 90,
      benchmark_mode: 'none',
      run_template: 'taxonomy_backtest',
      target_set_mode: targetSetMode,
      target_dimension: targetDimension,
      rebalance_frequency: rebalanceFrequency,
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
        artifact_path: run.artifacts[0]?.path ?? null,
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
      artifact_path: run.artifacts[0]?.path ?? null,
    })
  }

  function handleSelectArtifact(artifact: PortfolioResearchArtifactRecord) {
    updateSearchParams({ artifact_path: artifact.path })
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
                {workbench.portfolio_name} · recursive sleeve backtest through {workbench.settings.as_of_date ?? workbench.as_of_date}
              </div>
            </div>
            <div className="portfolio-summary-strip">
              <div className="summary-card">
                <span className="summary-card-label">Planning Axis</span>
                <strong className="summary-card-value">{summaryPlanningName}</strong>
              </div>
              <div className="summary-card">
                <span className="summary-card-label">Scope</span>
                <strong className="summary-card-value">{summaryScopeName}</strong>
              </div>
              <div className="summary-card">
                <span className="summary-card-label">Target Layer</span>
                <strong className="summary-card-value">{targetSetMode === 'taa_over_saa' ? 'TAA over SAA' : 'SAA only'}</strong>
              </div>
              <div className="summary-card">
                <span className="summary-card-label">Rebalance</span>
                <strong className="summary-card-value">{formatLabel(rebalanceFrequency)}</strong>
              </div>
              <div className="summary-card">
                <span className="summary-card-label">Runs</span>
                <strong className="summary-card-value">{workbench.runs.length}</strong>
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Run Setup</div>
              </div>
              <div className="portfolio-detail-meta">Select sleeve scope, target mode, and rebalance cadence before launching a run.</div>
            </div>
            <form className="transaction-form taxonomy-form-compact" onSubmit={(event) => void handleSaveSettings(event)}>
              <div className="taxonomy-form-grid taxonomy-form-grid-wide research-settings-grid">
                <label>
                  <span>Planning Taxonomy</span>
                  <select
                    value={planningTaxonomyId}
                    onChange={(event) => setPlanningTaxonomyId(event.target.value)}
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
                    disabled={!planningTaxonomyId}
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
                  <span>Start Date</span>
                  <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
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
                  <span>Target Layer</span>
                  <select value={targetSetMode} onChange={(event) => setTargetSetMode(event.target.value as 'saa' | 'taa_over_saa')}>
                    {TARGET_SET_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
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
                  <span>Rebalance</span>
                  <select
                    value={rebalanceFrequency}
                    onChange={(event) => setRebalanceFrequency(event.target.value as 'weekly' | 'monthly' | 'quarterly')}
                  >
                    {REBALANCE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="taxonomy-form-span-2 transaction-notes-field">
                  <span>Notes</span>
                  <textarea
                    value={notes}
                    onChange={(event) => setNotes(event.target.value)}
                    placeholder="Optional notes for current research framing, overlay assumptions, or follow-up questions."
                  />
                </label>
              </div>
              <div className="transaction-form-footer">
                <span className="portfolio-detail-meta">
                  Research resolves sleeves recursively. Each scope uses local targets, then rolls realized member paths upward.
                </span>
                <div className="toolbar">
                  <button type="submit" className="toolbar-link" disabled={actionPending === 'save'}>
                    {actionPending === 'save' ? 'Saving…' : 'Save Settings'}
                  </button>
                  <button
                    type="button"
                    className="toolbar-link button-primary"
                    onClick={() => void handleRunResearch()}
                    disabled={actionPending === 'run'}
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
                <div className="panel-title">Current Context</div>
              </div>
              <div className="portfolio-detail-meta">
                {currentContext?.lookback_start} to {currentContext?.lookback_end}
              </div>
            </div>
            {currentContext ? (
              <>
                <div className="performance-summary-grid research-context-grid">
                  <div className="table-shell">
                    <table className="performance-summary-table">
                      <tbody>
                        <tr>
                          <th>Portfolio NAV</th>
                          <td>{formatCurrency(currentContext.nav, currentContext.base_currency)}</td>
                        </tr>
                        <tr>
                          <th>Holdings</th>
                          <td>{currentContext.holdings_count}</td>
                        </tr>
                        <tr>
                          <th>Planning Groups</th>
                          <td>{currentContext.planning_group_count}</td>
                        </tr>
                        <tr>
                          <th>Root SAA</th>
                          <td>{currentContext.planning_target_summary?.root_saa_configured ? 'Configured' : 'Not configured'}</td>
                        </tr>
                        <tr>
                          <th>Scoped Sets</th>
                          <td>{currentContext.planning_target_summary?.scoped_target_set_count ?? 0}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div className="table-shell">
                    <table className="performance-summary-table">
                      <tbody>
                        <tr>
                          <th>Selected Axis</th>
                          <td>{summaryPlanningName}</td>
                        </tr>
                        <tr>
                          <th>Scope</th>
                          <td>{summaryScopeName}</td>
                        </tr>
                        <tr>
                          <th>Start Date</th>
                          <td>{startDate || '—'}</td>
                        </tr>
                        <tr>
                          <th>Lookback</th>
                          <td>{lookbackDays}D</td>
                        </tr>
                        <tr>
                          <th>Target Dimension</th>
                          <td>{formatLabel(targetDimension)}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="holdings-chart-panel">
                  <div className="panel-header panel-header-inline">
                    <div>
                      <div className="panel-title">{currentContext.chart_label ?? 'Reference Tape'}</div>
                    </div>
                    <div className="portfolio-detail-meta">
                      {currentContext.chart_note ?? 'Recent reference tape for current research context'}
                    </div>
                  </div>
                  {currentChartPoints.length >= 2 ? (
                    <PerformanceNavChart
                      points={currentChartPoints}
                      currency={currentContext.chart_currency ?? currentContext.base_currency}
                    />
                  ) : (
                    <div className="empty-state">Not enough current-context observations for a trend line.</div>
                  )}
                </div>
              </>
            ) : (
              <div className="empty-state">Current research context is unavailable.</div>
            )}
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Run History</div>
              </div>
              <div className="portfolio-detail-meta">Select a run to inspect backtest metrics, sleeve drift, and trade suggestions.</div>
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
                  <span>{selectedRun.detail?.coverage_note ?? 'Recursive sleeve backtest.'}</span>
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
                          <th>Finished</th>
                          <td>{formatTimestamp(selectedRun.finished_at)}</td>
                        </tr>
                        <tr>
                          <th>Requested By</th>
                          <td>{selectedRun.requested_by ?? '—'}</td>
                        </tr>
                        <tr>
                          <th>Scope</th>
                          <td>{selectedRun.detail?.selected_scope?.path ?? 'Top Level'}</td>
                        </tr>
                        <tr>
                          <th>Warnings</th>
                          <td>{selectedRun.detail?.warnings.length ?? 0}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div className="table-shell">
                    <table className="performance-summary-table">
                      <tbody>
                        <tr>
                          <th>Cumulative Return</th>
                          <td>{renderBacktestMetric(selectedRunMetricMap.get('cumulative_return') ?? { metric_id: 'cumulative_return', label: '', value: null })}</td>
                        </tr>
                        <tr>
                          <th>Annualized Return</th>
                          <td>{renderBacktestMetric(selectedRunMetricMap.get('annualized_return') ?? { metric_id: 'annualized_return', label: '', value: null })}</td>
                        </tr>
                        <tr>
                          <th>Volatility</th>
                          <td>{renderBacktestMetric(selectedRunMetricMap.get('annualized_volatility') ?? { metric_id: 'annualized_volatility', label: '', value: null })}</td>
                        </tr>
                        <tr>
                          <th>Max Drawdown</th>
                          <td>{renderBacktestMetric(selectedRunMetricMap.get('max_drawdown') ?? { metric_id: 'max_drawdown', label: '', value: null })}</td>
                        </tr>
                        <tr>
                          <th>Sharpe</th>
                          <td>{renderBacktestMetric(selectedRunMetricMap.get('sharpe_ratio') ?? { metric_id: 'sharpe_ratio', label: '', value: null })}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="holdings-chart-panel">
                  <div className="panel-header panel-header-inline">
                    <div>
                      <div className="panel-title">Backtest Curve</div>
                    </div>
                    <div className="portfolio-detail-meta">
                      {selectedRun.detail?.selected_scope?.label ?? 'Top Level'} · {selectedRun.detail?.signals.find((signal) => signal.label === 'Rebalance')?.value ?? '—'}
                    </div>
                  </div>
                  {selectedRunCurvePoints.length >= 2 ? (
                    <PerformanceNavChart points={selectedRunCurvePoints} currency={workbench.base_currency} />
                  ) : (
                    <div className="empty-state">Not enough backtest observations for a curve.</div>
                  )}
                </div>

                <div className="performance-section-block">
                  <div className="panel-header panel-header-inline">
                    <div>
                      <div className="panel-title">Backtest Metrics</div>
                    </div>
                    <div className="portfolio-detail-meta">Performance and risk diagnostics from the selected run</div>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th>Metric</th>
                          <th>Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {!selectedRun.detail?.backtest_metrics.length ? (
                          <TableStatusRow colSpan={2} label="No backtest metrics recorded for the selected run." />
                        ) : (
                          selectedRun.detail.backtest_metrics.map((metric) => (
                            <tr key={metric.metric_id}>
                              <td>{metric.label}</td>
                              <td>{renderBacktestMetric(metric)}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="performance-section-block">
                  <div className="panel-header panel-header-inline">
                    <div>
                      <div className="panel-title">Weight Changes</div>
                    </div>
                    <div className="portfolio-detail-meta">Backtest-period sleeve or member weight path within the selected scope</div>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th>Member</th>
                          <th>Target Dim</th>
                          <th>Source</th>
                          <th>Start</th>
                          <th>End</th>
                          <th>Average</th>
                          <th>Range</th>
                          <th>Selected Target</th>
                          <th>Return</th>
                        </tr>
                      </thead>
                      <tbody>
                        {!selectedRun.detail?.member_summaries.length ? (
                          <TableStatusRow colSpan={9} label="No member weight path was recorded for the selected scope." />
                        ) : (
                          selectedRun.detail.member_summaries.map((member) => (
                            <tr key={`${member.member_type}:${member.member_id}`}>
                              <td>{member.label}</td>
                              <td>{formatLabel(member.selected_target_dimension ?? member.default_target_dimension ?? 'weight')}</td>
                              <td>{member.source_target_set_type ? formatLabel(member.source_target_set_type) : '—'}</td>
                              <td>{formatPercent(member.start_weight)}</td>
                              <td>{formatPercent(member.end_weight)}</td>
                              <td>{formatPercent(member.average_weight)}</td>
                              <td>
                                {formatPercent(member.min_weight)} to {formatPercent(member.max_weight)}
                              </td>
                              <td>{formatPercent(member.selected_target_value)}</td>
                              <td>{formatPercent(member.cumulative_return)}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="performance-summary-grid research-detail-grid">
                  <div className="performance-section-block">
                    <div className="panel-header panel-header-inline">
                      <div>
                        <div className="panel-title">Rebalance Suggestions</div>
                      </div>
                      <div className="portfolio-detail-meta">Latest gap versus target within the selected scope</div>
                    </div>
                    <div className="table-shell">
                      <table className="transactions-table">
                        <thead>
                          <tr>
                            <th>Member</th>
                            <th>Action</th>
                            <th>Current</th>
                            <th>Target</th>
                            <th>Gap</th>
                            <th>Current Value</th>
                          </tr>
                        </thead>
                        <tbody>
                          {!selectedRun.detail?.rebalance_suggestions.length ? (
                            <TableStatusRow colSpan={6} label="No rebalance suggestions were generated for the selected run." />
                          ) : (
                            selectedRun.detail.rebalance_suggestions.map((item) => (
                              <tr key={`${item.member_type}:${item.member_id}`}>
                                <td>{item.label}</td>
                                <td>{formatLabel(item.action)}</td>
                                <td>{formatPercent(item.current_weight)}</td>
                                <td>{formatPercent(item.target_weight)}</td>
                                <td>{formatPercent(item.gap, 3)}</td>
                                <td>{formatCurrency(item.current_value_base, item.base_currency)}</td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  <div className="performance-section-block">
                    <div className="panel-header panel-header-inline">
                      <div>
                        <div className="panel-title">Rebalance Events</div>
                      </div>
                      <div className="portfolio-detail-meta">Scheduled rebalance points and realized turnover pressure</div>
                    </div>
                    <div className="table-shell">
                      <table className="transactions-table">
                        <thead>
                          <tr>
                            <th>Date</th>
                            <th>Scope</th>
                            <th>Turnover</th>
                            <th>Max Weight Gap</th>
                            <th>Max Risk Gap</th>
                            <th>Members</th>
                          </tr>
                        </thead>
                        <tbody>
                          {!selectedRun.detail?.rebalance_events.length ? (
                            <TableStatusRow colSpan={6} label="No rebalance events were recorded for the selected run." />
                          ) : (
                            selectedRun.detail.rebalance_events.map((event) => (
                              <tr key={`${event.rebalance_date}:${event.scope_label}`}>
                                <td>{event.rebalance_date}</td>
                                <td>{event.scope_label}</td>
                                <td>{formatPercent(event.turnover, 3)}</td>
                                <td>{formatPercent(event.max_weight_gap_before_rebalance, 3)}</td>
                                <td>{formatPercent(event.max_risk_share_gap, 3)}</td>
                                <td>{event.member_count}</td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>

                <div className="performance-summary-grid research-detail-grid">
                  <div className="performance-section-block">
                    <div className="panel-header panel-header-inline">
                      <div>
                        <div className="panel-title">Findings</div>
                      </div>
                      <div className="portfolio-detail-meta">Structured conclusions from the selected run</div>
                    </div>
                    <div className="table-shell">
                      <table className="transactions-table">
                        <thead>
                          <tr>
                            <th>Title</th>
                            <th>Detail</th>
                          </tr>
                        </thead>
                        <tbody>
                          {!selectedRun.detail?.findings.length ? (
                            <TableStatusRow colSpan={2} label="No findings recorded for the selected run." />
                          ) : (
                            selectedRun.detail.findings.map((finding) => (
                              <tr key={finding.title}>
                                <td>{finding.title}</td>
                                <td className="transaction-note-cell">{finding.detail}</td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  <div className="performance-section-block">
                    <div className="panel-header panel-header-inline">
                      <div>
                        <div className="panel-title">Next Questions</div>
                      </div>
                      <div className="portfolio-detail-meta">Follow-up prompts for review, risk, or deeper construction work</div>
                    </div>
                    <div className="table-shell">
                      <table className="transactions-table">
                        <thead>
                          <tr>
                            <th>Question</th>
                          </tr>
                        </thead>
                        <tbody>
                          {!selectedRun.detail?.next_questions.length ? (
                            <TableStatusRow colSpan={1} label="No next questions recorded for the selected run." />
                          ) : (
                            selectedRun.detail.next_questions.map((question) => (
                              <tr key={question}>
                                <td className="transaction-note-cell">{question}</td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>

                {selectedRun.detail?.warnings.length ? (
                  <div className="performance-section-block">
                    <div className="panel-header panel-header-inline">
                      <div>
                        <div className="panel-title">Warnings</div>
                      </div>
                      <div className="portfolio-detail-meta">Coverage and solver warnings emitted during the run</div>
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

                <div className="performance-section-block">
                  <div className="panel-header panel-header-inline">
                    <div>
                      <div className="panel-title">Artifacts</div>
                    </div>
                    <div className="portfolio-detail-meta">Markdown, JSON, and CSV outputs produced by the run</div>
                  </div>
                  <div className="table-shell">
                    <table className="transactions-table">
                      <thead>
                        <tr>
                          <th>Label</th>
                          <th>Type</th>
                          <th>Path</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {!selectedRun.artifacts.length ? (
                          <TableStatusRow colSpan={4} label="No artifacts recorded for the selected run." />
                        ) : (
                          selectedRun.artifacts.map((artifact) => (
                            <tr
                              key={artifact.path}
                              className={selectedArtifact?.path === artifact.path ? 'research-artifact-row-active' : ''}
                            >
                              <td>{artifact.label}</td>
                              <td>{artifact.media_type}</td>
                              <td className="transaction-note-cell">{artifact.path}</td>
                              <td className="taxonomy-actions-cell">
                                <button
                                  type="button"
                                  className="table-inline-button"
                                  onClick={() => handleSelectArtifact(artifact)}
                                >
                                  {selectedArtifact?.path === artifact.path ? 'Viewing' : 'View'}
                                </button>
                              </td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="performance-section-block">
                  <div className="panel-header panel-header-inline">
                    <div>
                      <div className="panel-title">Artifact Viewer</div>
                    </div>
                    <div className="portfolio-detail-meta">{selectedArtifact?.label ?? 'No artifact selected'}</div>
                  </div>
                  {artifactLoading ? <CalculationStatus label="Loading selected research artifact…" /> : null}
                  {artifactError ? <div className="inline-notice inline-notice-error">{artifactError}</div> : null}
                  {!artifactLoading && !artifactError && artifactContent ? (
                    <div className="research-artifact-viewer">
                      <div className="research-artifact-meta">
                        <span>{artifactContent.filename}</span>
                        <span>{artifactContent.media_type}</span>
                      </div>
                      <pre className="research-artifact-pre">{artifactContent.content}</pre>
                    </div>
                  ) : null}
                  {!artifactLoading && !artifactError && !artifactContent ? (
                    <div className="empty-state">Select an artifact to preview its content.</div>
                  ) : null}
                </div>
              </>
            )}
          </section>
        </>
      ) : null}
    </PortfolioWorkspaceLayout>
  )
}
