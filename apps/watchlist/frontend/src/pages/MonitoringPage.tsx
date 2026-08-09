import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import LoadingOverlay from '../components/LoadingOverlay'
import {
  executeInstrumentRecalc,
  getMonitoringDashboard,
  type MonitoringInstrumentRecord,
  type MonitoringDashboardResponse,
  type MonitoringMembership,
  type MonitoringRecalcJobRecord,
  type MonitoringWatchlistSummary,
} from '../lib/api'
import {
  buildInstrumentDetailPath,
  buildWatchlistPath,
  PLATFORM_HOME_URL,
} from '../lib/navigation'

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return '—'
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZoneName: 'short',
  }).format(parsed)
}

function formatDate(value: string | null | undefined) {
  return value || '—'
}

function formatLabel(value: string | null | undefined) {
  if (!value) {
    return '—'
  }
  return value
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function statusBadgeClass(status: string | null | undefined) {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'fresh' || normalized === 'completed') {
    return 'status-badge status-fresh'
  }
  if (normalized === 'failed' || normalized === 'error') {
    return 'status-badge status-error'
  }
  if (
    normalized === 'queued' ||
    normalized === 'running' ||
    normalized === 'pending_recalc' ||
    normalized === 'stale' ||
    normalized === 'partial' ||
    normalized === 'unavailable'
  ) {
    return 'status-badge status-pending'
  }
  return 'status-badge status-attribute'
}

function matchesSelectedWatchlist(
  watchlists: MonitoringMembership[],
  selectedWatchlistId: string,
) {
  if (selectedWatchlistId === 'all') {
    return true
  }
  return watchlists.some((item) => item.watchlist_id === selectedWatchlistId)
}

function resolveDetailPath(
  instrument: Pick<MonitoringInstrumentRecord, 'instrument_id' | 'primary_watchlist_id' | 'watchlists'>,
) {
  const watchlistId = instrument.primary_watchlist_id || instrument.watchlists[0]?.watchlist_id
  if (!watchlistId) {
    return null
  }
  return buildInstrumentDetailPath(instrument.instrument_id, watchlistId)
}

function resolveJobDetailPath(instrument: MonitoringRecalcJobRecord) {
  const watchlistId = instrument.primary_watchlist_id || instrument.watchlists[0]?.watchlist_id
  if (!watchlistId) {
    return null
  }
  return buildInstrumentDetailPath(instrument.instrument_id, watchlistId)
}

function InstrumentLink({ instrument }: { instrument: MonitoringInstrumentRecord | MonitoringRecalcJobRecord }) {
  const detailPath =
    'recalc_job_id' in instrument ? resolveJobDetailPath(instrument) : resolveDetailPath(instrument)
  if (!detailPath) {
    return <span>{instrument.instrument_name}</span>
  }
  return (
    <Link className="table-link" to={detailPath}>
      {instrument.instrument_name}
    </Link>
  )
}

function MembershipCell({ watchlists }: { watchlists: MonitoringMembership[] }) {
  if (!watchlists.length) {
    return <span>—</span>
  }
  return (
    <div className="monitoring-membership-list">
      {watchlists.map((item) => (
        <Link
          key={`${item.watchlist_id}-${item.name}`}
          className="monitoring-membership-link"
          to={buildWatchlistPath(item.watchlist_id)}
        >
          {item.name}
        </Link>
      ))}
    </div>
  )
}

function OverviewStat({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone?: string
}) {
  return (
    <div className="monitoring-overview-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      {tone ? <span className={statusBadgeClass(tone)}>{formatLabel(tone)}</span> : null}
    </div>
  )
}

function OpenDetailAction({
  detailPath,
}: {
  detailPath: string | null
}) {
  if (!detailPath) {
    return <span className="monitoring-action-placeholder">—</span>
  }
  return (
    <Link className="table-action" to={detailPath}>
      Open Detail
    </Link>
  )
}

export default function MonitoringPage() {
  const [monitoringSearchParams, setMonitoringSearchParams] = useSearchParams()
  const [dashboard, setDashboard] = useState<MonitoringDashboardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedWatchlistId, setSelectedWatchlistId] = useState(
    () => monitoringSearchParams.get('watchlist') || 'all',
  )

  useEffect(() => {
    setSelectedWatchlistId(monitoringSearchParams.get('watchlist') || 'all')
  }, [monitoringSearchParams])

  useEffect(() => {
    setMonitoringSearchParams(
      (current) => {
        const next = new URLSearchParams(current)
        if (selectedWatchlistId === 'all') {
          next.delete('watchlist')
        } else {
          next.set('watchlist', selectedWatchlistId)
        }
        return next.toString() === current.toString() ? current : next
      },
      { replace: true },
    )
  }, [selectedWatchlistId, setMonitoringSearchParams])
  const [recalculatingInstrumentIds, setRecalculatingInstrumentIds] = useState<string[]>([])
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; message: string } | null>(
    null,
  )

  useEffect(() => {
    if (!notice) {
      return undefined
    }
    const timeoutId = window.setTimeout(() => setNotice(null), 2800)
    return () => window.clearTimeout(timeoutId)
  }, [notice])

  async function loadDashboard(isRefresh = false) {
    if (isRefresh) {
      setRefreshing(true)
    } else {
      setLoading(true)
    }
    setError(null)
    try {
      const response = await getMonitoringDashboard()
      setDashboard(response)
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to load monitoring dashboard.',
      )
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    void loadDashboard()
  }, [])

  const selectedWatchlist = useMemo(() => {
    if (!dashboard || selectedWatchlistId === 'all') {
      return null
    }
    return (
      dashboard.watchlists.find((item) => item.watchlist_id === selectedWatchlistId) || null
    )
  }, [dashboard, selectedWatchlistId])

  const visibleWatchlists = useMemo(() => {
    if (!dashboard) {
      return []
    }
    if (selectedWatchlistId === 'all') {
      return dashboard.watchlists
    }
    return dashboard.watchlists.filter((item) => item.watchlist_id === selectedWatchlistId)
  }, [dashboard, selectedWatchlistId])

  const visibleNeedsAttentionInstruments = useMemo(() => {
    if (!dashboard) {
      return []
    }
    return dashboard.needs_attention_instruments.filter((item) =>
      matchesSelectedWatchlist(item.watchlists, selectedWatchlistId),
    )
  }, [dashboard, selectedWatchlistId])

  const visibleMissingLabelInstruments = useMemo(() => {
    if (!dashboard) {
      return []
    }
    return dashboard.missing_label_instruments.filter((item) =>
      matchesSelectedWatchlist(item.watchlists, selectedWatchlistId),
    )
  }, [dashboard, selectedWatchlistId])

  const visibleOpenRecalcJobs = useMemo(() => {
    if (!dashboard) {
      return []
    }
    return dashboard.open_recalc_jobs.filter((item) =>
      matchesSelectedWatchlist(item.watchlists, selectedWatchlistId),
    )
  }, [dashboard, selectedWatchlistId])

  const instrumentRecalcState = useMemo(() => {
    const stateMap = new Map<string, { queued: boolean; running: boolean; failed: boolean }>()
    for (const item of visibleOpenRecalcJobs) {
      const current = stateMap.get(item.instrument_id) || {
        queued: false,
        running: false,
        failed: false,
      }
      const normalized = String(item.job_status || '').toLowerCase()
      if (normalized === 'queued') {
        current.queued = true
      } else if (normalized === 'running') {
        current.running = true
      } else if (normalized === 'failed') {
        current.failed = true
      }
      stateMap.set(item.instrument_id, current)
    }
    return stateMap
  }, [visibleOpenRecalcJobs])

  const overviewRows = useMemo(() => {
    if (!dashboard) {
      return []
    }

    const failedRecalcCount =
      selectedWatchlistId === 'all'
        ? dashboard.overview.failed_recalc_job_count
        : visibleOpenRecalcJobs.filter((item) => item.job_status === 'failed').length

    const summary = selectedWatchlist
      ? {
          watchlist_count: 1,
          unique_instrument_count: selectedWatchlist.item_count,
          needs_refresh_count: selectedWatchlist.needs_refresh_count,
          missing_quote_count: selectedWatchlist.missing_quote_count,
          missing_label_count: selectedWatchlist.missing_label_count,
          open_recalc_job_count: selectedWatchlist.open_recalc_job_count,
        }
      : dashboard.overview

    return [
      { label: 'Watchlists', value: summary.watchlist_count },
      { label: 'Unique Products', value: summary.unique_instrument_count },
      {
        label: 'Needs Refresh',
        value: summary.needs_refresh_count,
        tone: 'pending_recalc',
      },
      {
        label: 'Missing Quote',
        value: summary.missing_quote_count,
        tone: 'unavailable',
      },
      { label: 'Missing Labels', value: summary.missing_label_count, tone: 'partial' },
      {
        label: 'Open Recalc',
        value: summary.open_recalc_job_count,
        tone: failedRecalcCount ? 'failed' : 'queued',
      },
    ]
  }, [dashboard, selectedWatchlist, selectedWatchlistId, visibleOpenRecalcJobs])

  async function handleRecalcNow(instrumentId: string) {
    setNotice(null)
    setRecalculatingInstrumentIds((current) =>
      current.includes(instrumentId) ? current : [...current, instrumentId],
    )
    try {
      await executeInstrumentRecalc(instrumentId, {
        job_type: 'performance',
        trigger_type: 'monitoring_dashboard',
        trigger_ref_type: 'watchlist',
        trigger_ref_id: selectedWatchlistId === 'all' ? null : selectedWatchlistId,
      })
      setNotice({
        tone: 'success',
        message: 'Recalc requested. Monitoring snapshot has been refreshed.',
      })
      await loadDashboard(true)
    } catch (requestError) {
      setNotice({
        tone: 'error',
        message:
          requestError instanceof Error
            ? requestError.message
            : 'Failed to enqueue recalc job.',
      })
    } finally {
      setRecalculatingInstrumentIds((current) => current.filter((item) => item !== instrumentId))
    }
  }

  return (
    <div className="terminal-page">
      <section className="panel">
        <div className="watchlist-breadcrumbs">
          <a href={PLATFORM_HOME_URL} className="watchlist-breadcrumb-link">
            Home
          </a>
          <span className="watchlist-breadcrumb-separator">/</span>
          <Link to="/watchlists" className="watchlist-breadcrumb-link">
            Watchlist
          </Link>
          <span className="watchlist-breadcrumb-separator">/</span>
          <span className="watchlist-breadcrumb-current">Monitoring</span>
        </div>
        <div className="panel-header">
          <div>
            <div className="panel-title">Monitoring</div>
            <h1 className="page-title">Product Pool Monitoring</h1>
            <div className="monitoring-page-note">
              Focused on data freshness, missing quote coverage, missing taxonomy or
              required research labels, and open recalc work.
            </div>
          </div>
          <div className="toolbar">
            <label className="monitoring-toolbar-field">
              <span>Watchlist</span>
              <select
                className="toolbar-select"
                value={selectedWatchlistId}
                onChange={(event) => setSelectedWatchlistId(event.target.value)}
              >
                <option value="all">All Watchlists</option>
                {dashboard?.watchlists.map((item) => (
                  <option key={item.watchlist_id} value={item.watchlist_id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" onClick={() => void loadDashboard(true)}>
              {refreshing ? 'Refreshing...' : 'Refresh'}
            </button>
            <Link to="/watchlists" className="toolbar-link">
              Watchlists
            </Link>
            <a href={PLATFORM_HOME_URL} className="toolbar-link">
              Platform Home
            </a>
          </div>
        </div>
        {notice ? (
          <div
            className={`inline-notice ${
              notice.tone === 'error' ? 'inline-notice-error' : 'inline-notice-success'
            }`}
          >
            {notice.message}
          </div>
        ) : null}
        {error ? <div className="error-state">{error}</div> : null}
        {loading && !dashboard ? <LoadingOverlay label="Loading monitoring dashboard" /> : null}
        {!loading && dashboard ? (
          <div className="monitoring-overview-grid">
            {overviewRows.map((item) => (
              <OverviewStat
                key={item.label}
                label={item.label}
                value={item.value}
                tone={item.tone}
              />
            ))}
          </div>
        ) : null}
        {!loading && dashboard ? (
          <div className="monitoring-generated-at">
            Snapshot generated at {formatDateTime(dashboard.generated_at)}.
          </div>
        ) : null}
      </section>

      {dashboard ? (
        <>
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Monitoring</div>
                <div className="watchlists-title">Watchlist Queue</div>
              </div>
            </div>
            {visibleWatchlists.length ? (
              <div className="table-shell">
                <table className="monitoring-table">
                  <thead>
                    <tr>
                      <th>Watchlist</th>
                      <th>Items</th>
                      <th>Needs Refresh</th>
                      <th>Missing Quote</th>
                      <th>Missing Labels</th>
                      <th>Open Recalc</th>
                      <th>Last Activity</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleWatchlists.map((item: MonitoringWatchlistSummary) => (
                      <tr key={item.watchlist_id}>
                        <td>
                          <Link className="table-link" to={buildWatchlistPath(item.watchlist_id)}>
                            {item.name}
                          </Link>
                        </td>
                        <td>{item.item_count}</td>
                        <td>{item.needs_refresh_count}</td>
                        <td>{item.missing_quote_count}</td>
                        <td>{item.missing_label_count}</td>
                        <td>{item.open_recalc_job_count}</td>
                        <td>{formatDateTime(item.last_activity_at)}</td>
                        <td>
                          <div className="monitoring-actions">
                            <Link className="table-action" to={buildWatchlistPath(item.watchlist_id)}>
                              Open Watchlist
                            </Link>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state">No watchlists available yet.</div>
            )}
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Monitoring</div>
                <div className="watchlists-title">Needs Refresh / Missing Quote</div>
              </div>
            </div>
            {visibleNeedsAttentionInstruments.length ? (
              <div className="table-shell">
                <table className="monitoring-table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Identifier</th>
                      <th>Watchlists</th>
                      <th>Freshness</th>
                      <th>Latest Quote Date</th>
                      <th>Reason</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleNeedsAttentionInstruments.map((item: MonitoringInstrumentRecord) => {
                      const detailPath = resolveDetailPath(item)
                      const recalcState = instrumentRecalcState.get(item.instrument_id)
                      const recalcPending = Boolean(recalcState?.queued || recalcState?.running)
                      const recalculating = recalculatingInstrumentIds.includes(item.instrument_id)
                      return (
                        <tr key={item.instrument_id}>
                          <td>
                            <div className="monitoring-primary-cell">
                              <InstrumentLink instrument={item} />
                              {item.management_firm_name ? (
                                <span className="monitoring-secondary-text">
                                  {item.management_firm_name}
                                </span>
                              ) : null}
                            </div>
                          </td>
                          <td>{item.ticker_or_isin || item.instrument_id}</td>
                          <td>
                            <MembershipCell watchlists={item.watchlists} />
                          </td>
                          <td>
                            <span className={statusBadgeClass(item.data_freshness_status)}>
                              {formatLabel(item.data_freshness_status)}
                            </span>
                          </td>
                          <td>{formatDate(item.latest_quote_date)}</td>
                          <td className="monitoring-reason-cell">
                            {item.issue_flags.includes('missing_quote') ? (
                              <div className="monitoring-reason-line">Missing latest quote.</div>
                            ) : null}
                            {item.staleness_reason ? (
                              <div className="monitoring-secondary-text">{item.staleness_reason}</div>
                            ) : null}
                          </td>
                          <td>
                            <div className="monitoring-actions">
                              <OpenDetailAction detailPath={detailPath} />
                              <button
                                type="button"
                                className="table-action"
                                onClick={() => void handleRecalcNow(item.instrument_id)}
                                disabled={recalculating || recalcPending}
                              >
                                {recalculating
                                  ? 'Recalculating...'
                                  : recalcPending
                                    ? 'Queued'
                                    : 'Recalc Now'}
                              </button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state">No refresh or quote coverage issues right now.</div>
            )}
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Monitoring</div>
                <div className="watchlists-title">Missing Taxonomy / Research Labels</div>
              </div>
            </div>
            {visibleMissingLabelInstruments.length ? (
              <div className="table-shell">
                <table className="monitoring-table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Watchlists</th>
                      <th>Missing Labels</th>
                      <th>Freshness</th>
                      <th>Latest Quote Date</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleMissingLabelInstruments.map((item: MonitoringInstrumentRecord) => (
                      <tr key={`${item.instrument_id}-missing-labels`}>
                        <td>
                          <div className="monitoring-primary-cell">
                            <InstrumentLink instrument={item} />
                            <span className="monitoring-secondary-text">
                              {item.ticker_or_isin || item.instrument_id}
                            </span>
                          </div>
                        </td>
                        <td>
                          <MembershipCell watchlists={item.watchlists} />
                        </td>
                        <td className="monitoring-missing-tags-cell">
                          <strong>{item.missing_attribute_count}</strong>
                          <span>{item.missing_attribute_labels.join(' / ')}</span>
                        </td>
                        <td>
                          <span className={statusBadgeClass(item.data_freshness_status)}>
                            {formatLabel(item.data_freshness_status)}
                          </span>
                        </td>
                        <td>{formatDate(item.latest_quote_date)}</td>
                        <td>
                          <div className="monitoring-actions">
                            <OpenDetailAction detailPath={resolveDetailPath(item)} />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state">
                No key taxonomy or research labels missing right now.
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Monitoring</div>
                <div className="watchlists-title">Open Recalc Jobs</div>
              </div>
            </div>
            {visibleOpenRecalcJobs.length ? (
              <div className="table-shell">
                <table className="monitoring-table">
                  <thead>
                    <tr>
                      <th>Instrument</th>
                      <th>Job</th>
                      <th>Status</th>
                      <th>Enqueued</th>
                      <th>Watchlists</th>
                      <th>Trigger / Error</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleOpenRecalcJobs.map((item: MonitoringRecalcJobRecord) => {
                      const detailPath = resolveJobDetailPath(item)
                      const normalizedStatus = String(item.job_status || '').toLowerCase()
                      const recalculating = recalculatingInstrumentIds.includes(item.instrument_id)
                      const canRetry = normalizedStatus === 'failed'
                      return (
                        <tr key={item.recalc_job_id}>
                          <td>
                            <div className="monitoring-primary-cell">
                              <InstrumentLink instrument={item} />
                              <span className="monitoring-secondary-text">{item.instrument_id}</span>
                            </div>
                          </td>
                          <td>{formatLabel(item.job_type)}</td>
                          <td>
                            <span className={statusBadgeClass(item.job_status)}>
                              {formatLabel(item.job_status)}
                            </span>
                          </td>
                          <td>{formatDateTime(item.enqueued_at)}</td>
                          <td>
                            <MembershipCell watchlists={item.watchlists} />
                          </td>
                          <td className="monitoring-reason-cell">
                            <div>{formatLabel(item.trigger_type)}</div>
                            {item.error_message ? (
                              <div className="monitoring-secondary-text">{item.error_message}</div>
                            ) : null}
                          </td>
                          <td>
                            <div className="monitoring-actions">
                              <OpenDetailAction detailPath={detailPath} />
                              {canRetry ? (
                                <button
                                  type="button"
                                  className="table-action"
                                  onClick={() => void handleRecalcNow(item.instrument_id)}
                                  disabled={recalculating}
                                >
                                  {recalculating ? 'Recalculating...' : 'Recalc Now'}
                                </button>
                              ) : (
                                <span className="monitoring-action-placeholder">
                                  {normalizedStatus === 'running' ? 'Running' : 'Queued'}
                                </span>
                              )}
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state">No queued, running, or failed recalc jobs.</div>
            )}
          </section>
        </>
      ) : null}
    </div>
  )
}
