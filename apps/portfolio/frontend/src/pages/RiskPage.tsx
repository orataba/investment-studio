import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useParams } from 'react-router-dom'

import BenchmarkSearchBox, {
  benchmarkInstrumentLabel,
  instrumentPrimaryIdentifier,
} from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import QualityWarningsNotice from '../components/QualityWarningsNotice'
import RollingRiskMetricChart, { type RiskChartDisplayStyle } from '../components/RollingRiskMetricChart'
import RiskTargetGapChart, { type RiskTargetGapChartRow } from '../components/RiskTargetGapChart'
import {
  clearPortfolioApiCache,
  getPortfolioInstruments,
  getPortfolioRiskWorkspace,
  getWorkspaceSummaryForPortfolio,
  type PortfolioRiskWorkspaceError,
  type PortfolioRiskWorkspaceResponse,
  type PortfolioRiskWorkspaceTargetGapRow,
  type SharedInstrumentRecord,
} from '../lib/api'
import { formatCurrency, formatLabel, formatNumber, formatPercent } from '../lib/format'

const ALL_INSTRUMENTS_SCOPE = '__all_instruments__'
const RISK_PAGE_SETTINGS_STORAGE_KEY = 'portfolio_ops.portfolio.risk.workspace.settings.v2'

const RISK_WINDOW_OPTIONS = [
  { value: 30, label: '1M' },
  { value: 90, label: '3M' },
  { value: 180, label: '6M' },
  { value: 366, label: '12M' },
  { value: 730, label: '24M' },
] as const

const CHART_STYLE_OPTIONS: Array<{ value: RiskChartDisplayStyle; label: string }> = [
  { value: 'mountain', label: 'Mountain' },
  { value: 'line', label: 'Line' },
  { value: 'dot', label: 'Dot' },
]

type RiskPageSettings = {
  rollingLookbackDays: number
  matrixLookbackDays: number
  chartStyle: RiskChartDisplayStyle
}

const DEFAULT_SETTINGS: RiskPageSettings = {
  rollingLookbackDays: 30,
  matrixLookbackDays: 30,
  chartStyle: 'mountain',
}

function normalizeLookback(value: unknown, fallback: number) {
  const numeric = typeof value === 'number' ? value : Number(value)
  return RISK_WINDOW_OPTIONS.some((option) => option.value === numeric) ? numeric : fallback
}

function normalizeChartStyle(value: unknown): RiskChartDisplayStyle {
  return CHART_STYLE_OPTIONS.some((option) => option.value === value)
    ? (value as RiskChartDisplayStyle)
    : DEFAULT_SETTINGS.chartStyle
}

function loadSettings(): RiskPageSettings {
  try {
    const raw = window.localStorage.getItem(RISK_PAGE_SETTINGS_STORAGE_KEY)
    const parsed = raw ? (JSON.parse(raw) as Record<string, unknown>) : {}
    return {
      rollingLookbackDays: normalizeLookback(parsed.rollingLookbackDays, DEFAULT_SETTINGS.rollingLookbackDays),
      matrixLookbackDays: normalizeLookback(parsed.matrixLookbackDays, DEFAULT_SETTINGS.matrixLookbackDays),
      chartStyle: normalizeChartStyle(parsed.chartStyle),
    }
  } catch {
    return DEFAULT_SETTINGS
  }
}

function saveSettings(settings: RiskPageSettings) {
  try {
    window.localStorage.setItem(RISK_PAGE_SETTINGS_STORAGE_KEY, JSON.stringify(settings))
  } catch {
    // Local display preferences are intentionally non-authoritative.
  }
}

function windowLabel(lookbackDays: number) {
  return RISK_WINDOW_OPTIONS.find((option) => option.value === lookbackDays)?.label ?? `${lookbackDays}D`
}

function modelLabel(modelId: string) {
  const labels: Record<string, string> = {
    ewma_vol_shrinkage_corr_covariance: 'EWMA + Shrinkage',
    ewma_covariance: 'EWMA',
    sample_covariance: 'Sample',
  }
  return labels[modelId] ?? formatLabel(modelId)
}

function uniqueErrorMessages(errors: PortfolioRiskWorkspaceError[]) {
  return [...new Set(errors.map((error) => error.message.trim()).filter(Boolean))]
}

function heatmapCellStyle(value: number | null | undefined, maxAbs: number): CSSProperties {
  if (value == null || !Number.isFinite(value) || !Number.isFinite(maxAbs) || maxAbs <= 0) {
    return {}
  }
  const intensity = Math.min(1, Math.max(0.08, Math.abs(value) / maxAbs))
  return value < 0
    ? { backgroundColor: `rgba(185, 28, 28, ${0.06 + intensity * 0.24})` }
    : { backgroundColor: `rgba(15, 76, 129, ${0.06 + intensity * 0.24})` }
}

function targetGapRows(
  rows: PortfolioRiskWorkspaceTargetGapRow[],
  currency: string,
): RiskTargetGapChartRow[] {
  return rows.map((row) => ({
    id: row.id,
    label: row.label,
    current: row.current,
    saaTarget: row.saa_target,
    taaTarget: row.taa_target,
    saaGap: row.saa_gap,
    taaGap: row.taa_gap,
    detail: row.current_value_base == null ? undefined : formatCurrency(row.current_value_base, currency),
  }))
}

function RiskSettingsMenu({
  label,
  lookbackDays,
  onLookbackChange,
  chartStyle,
  onChartStyleChange,
}: {
  label: string
  lookbackDays: number
  onLookbackChange: (value: number) => void
  chartStyle?: RiskChartDisplayStyle
  onChartStyleChange?: (value: RiskChartDisplayStyle) => void
}) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) {
      return undefined
    }
    function closeOnOutsidePointer(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', closeOnOutsidePointer)
    return () => document.removeEventListener('pointerdown', closeOnOutsidePointer)
  }, [open])

  return (
    <div className="portfolio-nav-chart-menu risk-settings-menu" ref={menuRef}>
      <button
        type="button"
        className={open ? 'portfolio-nav-settings-trigger portfolio-nav-settings-trigger-active' : 'portfolio-nav-settings-trigger'}
        aria-label={`${label} settings`}
        onClick={() => setOpen((current) => !current)}
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
      {open ? (
        <div className="portfolio-nav-settings-panel risk-settings-panel">
          <div className="portfolio-nav-settings-layout">
            <section className="portfolio-nav-settings-block">
              <div className="portfolio-nav-settings-block-head">
                <span>Window</span>
                <strong>{windowLabel(lookbackDays)}</strong>
              </div>
              <div className="portfolio-nav-settings-option-grid">
                {RISK_WINDOW_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={
                      option.value === lookbackDays
                        ? 'portfolio-nav-option portfolio-nav-option-active'
                        : 'portfolio-nav-option'
                    }
                    onClick={() => onLookbackChange(option.value)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </section>
            {chartStyle && onChartStyleChange ? (
              <section className="portfolio-nav-settings-block portfolio-nav-settings-block-data">
                <div className="portfolio-nav-settings-block-head">
                  <span>Display</span>
                  <strong>{formatLabel(chartStyle)}</strong>
                </div>
                <div className="portfolio-nav-settings-option-grid">
                  {CHART_STYLE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={
                        option.value === chartStyle
                          ? 'portfolio-nav-option portfolio-nav-option-active'
                          : 'portfolio-nav-option'
                      }
                      onClick={() => onChartStyleChange(option.value)}
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
}: {
  dates: string[]
  value: string
  onChange: (value: string) => void
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
        <span>Matrix as of</span>
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
          aria-label="Matrix as of"
        />
        <div className="risk-date-scrubber-endpoints">
          <span>{dates[0]}</span>
          <span>{dates[dates.length - 1]}</span>
        </div>
      </div>
    </div>
  )
}

function ErrorNotice({ errors }: { errors: PortfolioRiskWorkspaceError[] }) {
  const messages = uniqueErrorMessages(errors)
  if (!messages.length) {
    return null
  }
  return (
    <div className="inline-notice inline-notice-error">
      {messages.map((message) => (
        <div key={message}>{message}</div>
      ))}
    </div>
  )
}

function CorrelationMatrix({ matrix }: { matrix: PortfolioRiskWorkspaceResponse['matrix'] }) {
  if (matrix.status !== 'ready') {
    return <ErrorNotice errors={matrix.errors} />
  }
  if (!matrix.groups.length) {
    return <div className="price-chart-empty">No matrix.</div>
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
              <th
                className="risk-matrix-row-header"
                title={`${rowGroup.label}; ${rowGroup.observation_count} return observations`}
              >
                <span className="risk-matrix-row-label">{rowGroup.label}</span>
              </th>
              {matrix.groups.map((columnGroup, columnIndex) => {
                const cell = matrix.cells[rowIndex]?.[columnIndex]
                return (
                  <td
                    key={`${rowGroup.key}:${columnGroup.key}`}
                    className="risk-heatmap-cell"
                    style={heatmapCellStyle(cell?.value, matrix.max_abs)}
                    title={`${rowGroup.label} x ${columnGroup.label}; ${cell?.observation_count ?? 0} observations`}
                  >
                    {formatNumber(cell?.value, 2)}
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

function TargetGapPanel({
  rows,
  errors,
  currency,
  ariaLabel,
  emptyLabel,
  currentLabel,
}: {
  rows: PortfolioRiskWorkspaceTargetGapRow[]
  errors: PortfolioRiskWorkspaceError[]
  currency: string
  ariaLabel: string
  emptyLabel: string
  currentLabel?: string
}) {
  return (
    <>
      <ErrorNotice errors={errors} />
      <RiskTargetGapChart
        rows={targetGapRows(rows, currency)}
        ariaLabel={ariaLabel}
        emptyLabel={emptyLabel}
        currentLabel={currentLabel}
      />
    </>
  )
}

export default function RiskPage() {
  const { portfolioId = '' } = useParams()
  const [settings, setSettings] = useState<RiskPageSettings>(loadSettings)
  const [asOfDate, setAsOfDate] = useState('')
  const [workspace, setWorkspace] = useState<PortfolioRiskWorkspaceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [riskPolicyRevision, setRiskPolicyRevision] = useState(0)
  const [matrixScope, setMatrixScope] = useState(ALL_INSTRUMENTS_SCOPE)
  const [matrixAsOfDate, setMatrixAsOfDate] = useState('')
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkInstrumentId, setBenchmarkInstrumentId] = useState('')
  const [benchmarkSearch, setBenchmarkSearch] = useState('')

  useEffect(() => saveSettings(settings), [settings])

  useEffect(() => {
    function handleRiskPolicyUpdated(event: Event) {
      const detail = (event as CustomEvent<{ portfolioId?: string }>).detail
      if (detail?.portfolioId === portfolioId) {
        clearPortfolioApiCache()
        setRiskPolicyRevision((current) => current + 1)
      }
    }
    window.addEventListener('portfolio-risk-policy-updated', handleRiskPolicyUpdated)
    return () => window.removeEventListener('portfolio-risk-policy-updated', handleRiskPolicyUpdated)
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId) {
      setAsOfDate('')
      setError('Portfolio id is required.')
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([getWorkspaceSummaryForPortfolio(portfolioId), getPortfolioInstruments(portfolioId)])
      .then(([summary, instruments]) => {
        if (cancelled) {
          return
        }
        setAsOfDate(summary.as_of_date)
        setBenchmarkInstruments(instruments.instruments)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : 'Failed to load risk context.')
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId || !asOfDate) {
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    getPortfolioRiskWorkspace(portfolioId, {
      as_of_date: asOfDate,
      rolling_lookback_days: settings.rollingLookbackDays,
      matrix_lookback_days: settings.matrixLookbackDays,
      matrix_scope_node_id: matrixScope,
      matrix_as_of_date: matrixAsOfDate || undefined,
      benchmark_instrument_id: benchmarkInstrumentId || undefined,
    })
      .then((response) => {
        if (cancelled) {
          return
        }
        setWorkspace(response)
        if (matrixAsOfDate && !response.matrix.available_as_of_dates.includes(matrixAsOfDate)) {
          setMatrixAsOfDate('')
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setWorkspace(null)
          setError(requestError instanceof Error ? requestError.message : 'Failed to load risk workspace.')
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
  }, [
    asOfDate,
    benchmarkInstrumentId,
    matrixAsOfDate,
    matrixScope,
    portfolioId,
    riskPolicyRevision,
    settings.matrixLookbackDays,
    settings.rollingLookbackDays,
  ])

  useEffect(() => {
    if (workspace && !workspace.matrix_scope_options.some((option) => option.value === matrixScope)) {
      setMatrixScope(ALL_INSTRUMENTS_SCOPE)
      setMatrixAsOfDate('')
    }
  }, [matrixScope, workspace])

  const selectedBenchmark = benchmarkInstruments.find(
    (instrument) => instrument.instrument_id === benchmarkInstrumentId,
  )
  const benchmarkLabel = selectedBenchmark ? instrumentPrimaryIdentifier(selectedBenchmark) : null
  const qualityWarnings = useMemo(
    () => workspace?.coverage.instruments.flatMap((instrument) => instrument.warnings) ?? [],
    [workspace],
  )
  const matrixTimelineValue = matrixAsOfDate || workspace?.matrix.as_of_date || ''
  const modelMeta = workspace
    ? `${windowLabel(workspace.risk_policy.lookback_days)} ${modelLabel(workspace.risk_policy.covariance_model_id)}; ${formatLabel(workspace.risk_policy.contribution_mode)} RC`
    : ''

  return (
    <PortfolioWorkspaceLayout activeSection="Risk" toolbarLabel="View: Risk Analytics">
      <section className="portfolio-detail-surface risk-page-surface">
        {error ? <div className="inline-notice inline-notice-error">{error}</div> : null}
        <QualityWarningsNotice warnings={qualityWarnings} />
        {loading ? <CalculationStatus /> : null}
        {!loading && !workspace && !error ? <div className="empty-state">No data.</div> : null}

        {workspace ? (
          <>
            {workspace.planning_taxonomy && workspace.allocation_policy_drift.status === 'ready' ? (
            <section className="performance-section-block">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar risk-section-toolbar">
                <div>
                  <div className="panel-title">Allocation Policy Drift</div>
                  <div className="portfolio-detail-meta">
                    {workspace.planning_taxonomy
                      ? `${workspace.planning_taxonomy.name}; ${workspace.as_of_date}; ${workspace.frequency_profile.status_label}; Production Risk Model; ${modelMeta}`
                      : `No planning taxonomy; ${workspace.as_of_date}`}
                  </div>
                </div>
              </div>
              <div className="risk-target-grid">
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">Weight Target Gap</div>
                  <TargetGapPanel
                    rows={workspace.allocation_policy_drift.weight_rows}
                    errors={workspace.allocation_policy_drift.errors}
                    currency={workspace.base_currency}
                    ariaLabel="Weight target drift"
                    emptyLabel="No weight target."
                  />
                </div>
                <div className="risk-target-panel">
                  <div className="risk-matrix-panel-title">Risk Target Gap</div>
                  <TargetGapPanel
                    rows={workspace.allocation_policy_drift.risk_rows}
                    errors={workspace.allocation_policy_drift.errors}
                    currency={workspace.base_currency}
                    ariaLabel="Risk budget target gap"
                    emptyLabel="No risk target."
                    currentLabel="Risk Share"
                  />
                </div>
              </div>
            </section>
            ) : null}

            <section className="performance-section-block risk-rolling-section">
              <div className="portfolio-detail-toolbar performance-subsection-toolbar risk-section-toolbar risk-rolling-toolbar">
                <div className="risk-toolbar-primary risk-rolling-toolbar-primary">
                  <div>
                    <div className="panel-title">Rolling Risk</div>
                    <div className="portfolio-detail-meta">
                      Current static weights; canonical total return; {modelLabel(workspace.rolling.model_id)}
                    </div>
                  </div>
                  <BenchmarkSearchBox
                    instruments={benchmarkInstruments}
                    selectedInstrumentId={benchmarkInstrumentId}
                    searchValue={benchmarkSearch}
                    onSearchChange={setBenchmarkSearch}
                    onSelectInstrument={(instrument) => {
                      setBenchmarkInstrumentId(instrument.instrument_id)
                      setBenchmarkSearch(benchmarkInstrumentLabel(instrument))
                    }}
                    onClear={() => {
                      setBenchmarkInstrumentId('')
                      setBenchmarkSearch('')
                    }}
                    placeholder="Compare benchmark..."
                  />
                </div>
                <div className="risk-section-actions">
                  <RiskSettingsMenu
                    label="Rolling risk"
                    lookbackDays={settings.rollingLookbackDays}
                    onLookbackChange={(value) => setSettings((current) => ({ ...current, rollingLookbackDays: value }))}
                    chartStyle={settings.chartStyle}
                    onChartStyleChange={(value) => setSettings((current) => ({ ...current, chartStyle: value }))}
                  />
                </div>
              </div>
              <ErrorNotice errors={workspace.rolling.errors} />
              <div className="risk-rolling-grid">
                <RollingRiskMetricChart
                  title="Annualized Volatility"
                  points={workspace.rolling.portfolio_volatility_points}
                  benchmarkPoints={workspace.rolling.benchmark_volatility_points}
                  benchmarkLabel={benchmarkLabel}
                  displayStyle={settings.chartStyle}
                  formatValue={(value) => formatPercent(value)}
                  emptyLabel="Insufficient data."
                />
                <RollingRiskMetricChart
                  title="Sharpe Ratio"
                  points={workspace.rolling.portfolio_sharpe_points}
                  benchmarkPoints={workspace.rolling.benchmark_sharpe_points}
                  benchmarkLabel={benchmarkLabel}
                  displayStyle={settings.chartStyle}
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
                    <div className="portfolio-detail-meta">{workspace.frequency_profile.status_label}</div>
                  </div>
                  <label className="risk-scope-select">
                    <div className="risk-scope-select-box">
                      <select
                        value={matrixScope}
                        onChange={(event) => {
                          setMatrixScope(event.target.value)
                          setMatrixAsOfDate('')
                        }}
                        aria-label="Matrix scope"
                      >
                        {workspace.matrix_scope_options.map((option) => (
                          <option key={option.value} value={option.value}>
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
                    lookbackDays={settings.matrixLookbackDays}
                    onLookbackChange={(value) => {
                      setSettings((current) => ({ ...current, matrixLookbackDays: value }))
                      setMatrixAsOfDate('')
                    }}
                  />
                </div>
              </div>
              <RiskDateTimeline
                dates={workspace.matrix.available_as_of_dates}
                value={matrixTimelineValue}
                onChange={setMatrixAsOfDate}
              />
              <div className="risk-correlation-stack">
                <div className="risk-matrix-panel">
                  <CorrelationMatrix matrix={workspace.matrix} />
                </div>
              </div>
            </section>
          </>
        ) : null}
      </section>
    </PortfolioWorkspaceLayout>
  )
}
