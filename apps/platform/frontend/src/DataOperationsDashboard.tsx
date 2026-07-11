import { useCallback, useEffect, useState } from 'react'

import { LanguageSelector } from '../../../../packages/ui/src/i18n'

type DashboardPayload = {
  registry_name: string
  counts: {
    total: number
    active: number
    archived: number
    missing_market_data: number
    refresh_failures: number
  }
  instrument_types: Record<string, number>
  sources: Record<string, number>
  coverage: Record<string, number>
  latest_market_date: string | null
  last_refresh_at: string | null
  sync_readiness: { email: boolean; tushare: boolean }
  problems: Array<{
    instrument_id: string
    instrument_name: string
    instrument_type: string
    issue: string
    message: string
  }>
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

function siblingUrl(port: string) {
  const hostname = window.location.hostname.includes(':')
    ? `[${window.location.hostname}]`
    : window.location.hostname
  return `${window.location.protocol}//${hostname}:${port}`
}

function formatDateTime(value: string | null) {
  if (!value) return 'Not run yet'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

export default function DataOperationsDashboard() {
  const [data, setData] = useState<DashboardPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadDashboard = useCallback(async () => {
    setLoading(true)
    try {
      const response = await fetch(`${API_BASE}/api/dashboard`)
      if (!response.ok) throw new Error(await response.text())
      setData((await response.json()) as DashboardPayload)
      setError(null)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load data operations.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadDashboard()
  }, [loadDashboard])

  async function refreshSources() {
    setRefreshing(true)
    try {
      const response = await fetch(`${API_BASE}/api/instruments/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: 'all', updated_by: 'data_operations_ui' }),
      })
      if (!response.ok) throw new Error(await response.text())
      await loadDashboard()
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : 'Refresh failed.')
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <main className="platform-shell data-ops-shell">
      <header className="platform-masthead data-ops-masthead">
        <a className="data-ops-brand" href="/">
          <span>Portfolio Operations</span>
          <strong>Data Operations</strong>
        </a>
        <nav className="data-ops-nav" aria-label="Data operations navigation">
          <a className="platform-nav-link platform-nav-link-active" href="/">Overview</a>
          <a className="platform-nav-link" href="/instruments">Instrument Registry</a>
        </nav>
        <LanguageSelector />
      </header>

      <section className="data-ops-hero">
        <div>
          <div className="registry-kicker">Shared data control plane</div>
          <h1>Keep market data trusted, current, and ready for downstream use.</h1>
          <p>
            This console manages the shared instrument and market-data base consumed by
            Watchlist and Portfolio. It is an operations surface—not a third investment app.
          </p>
        </div>
        <div className="data-ops-hero-actions">
          <button type="button" className="registry-submit" disabled={refreshing} onClick={() => void refreshSources()}>
            {refreshing ? 'Refreshing…' : 'Refresh Email + Tushare'}
          </button>
          <a className="registry-submit secondary" href="/instruments">Manage instruments</a>
        </div>
      </section>

      {error ? <div className="registry-error">{error}</div> : null}
      {loading && !data ? <div className="data-ops-loading">Loading operational status…</div> : null}

      {data ? (
        <>
          <section className="data-ops-metrics" aria-label="Shared data status">
            <article><span>Active instruments</span><strong>{data.counts.active}</strong><small>{data.counts.archived} archived</small></article>
            <article><span>Latest market date</span><strong>{data.latest_market_date || '—'}</strong><small>Across current quotes</small></article>
            <article className={data.counts.missing_market_data ? 'data-ops-metric-warning' : ''}><span>Missing market data</span><strong>{data.counts.missing_market_data}</strong><small>Needs source setup or import</small></article>
            <article className={data.counts.refresh_failures ? 'data-ops-metric-warning' : ''}><span>Refresh failures</span><strong>{data.counts.refresh_failures}</strong><small>Blocked or failed instruments</small></article>
          </section>

          <section className="data-ops-grid">
            <article className="data-ops-panel">
              <div className="data-ops-panel-header"><div><span>Pipeline status</span><h2>Sources &amp; freshness</h2></div><small>Last attempt {formatDateTime(data.last_refresh_at)}</small></div>
              <div className="data-ops-source-list">
                <div><span className={data.sync_readiness.email ? 'data-ops-dot ready' : 'data-ops-dot'} /><strong>Email NAV</strong><small>{data.sync_readiness.email ? 'Ready' : 'Needs configuration'}</small></div>
                <div><span className={data.sync_readiness.tushare ? 'data-ops-dot ready' : 'data-ops-dot'} /><strong>Tushare</strong><small>{data.sync_readiness.tushare ? 'Ready' : 'Needs token'}</small></div>
              </div>
              <div className="data-ops-breakdown">
                <div><span>Instrument types</span><p>{Object.entries(data.instrument_types).map(([key, value]) => `${key.toUpperCase()} ${value}`).join(' · ')}</p></div>
                <div><span>Source ownership</span><p>{Object.entries(data.sources).map(([key, value]) => `${key} ${value}`).join(' · ')}</p></div>
                <div><span>Coverage</span><p>{Object.entries(data.coverage).map(([key, value]) => `${key} ${value}`).join(' · ')}</p></div>
              </div>
            </article>

            <aside className="data-ops-panel data-ops-consumers">
              <span>Downstream consumers</span>
              <h2>Investment workflows stay separate.</h2>
              <p>Use this console to repair shared data. Research and portfolio decisions remain in their focused workspaces.</p>
              <div><a href={siblingUrl('5173')}>Open Watchlist ↗</a><a href={siblingUrl('5174')}>Open Portfolio ↗</a></div>
            </aside>
          </section>

          <section className="data-ops-panel data-ops-exceptions">
            <div className="data-ops-panel-header"><div><span>Exceptions</span><h2>Items needing attention</h2></div><a href="/instruments">Open registry</a></div>
            {data.problems.length ? (
              <div className="registry-table-wrap"><table className="registry-table registry-table-compact"><thead><tr><th>Instrument</th><th>Type</th><th>Issue</th><th>Detail</th></tr></thead><tbody>{data.problems.map((problem) => <tr key={problem.instrument_id}><td><a href={`/instruments?instrument=${encodeURIComponent(problem.instrument_id)}`}>{problem.instrument_name}</a><small>{problem.instrument_id}</small></td><td>{problem.instrument_type.toUpperCase()}</td><td>{problem.issue.replace(/_/g, ' ')}</td><td>{problem.message}</td></tr>)}</tbody></table></div>
            ) : <div className="data-ops-clear">No current data exceptions.</div>}
          </section>
        </>
      ) : null}
    </main>
  )
}
