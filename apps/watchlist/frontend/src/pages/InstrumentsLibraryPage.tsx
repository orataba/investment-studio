import { startTransition, useDeferredValue, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { formatLabel } from '../lib/format'
import { getSharedInstruments, type SharedInstrumentRecord } from '../lib/api'
import { PLATFORM_HOME_URL } from '../lib/navigation'

const ASSET_TYPE_OPTIONS = [
  { value: 'all', label: 'All Types' },
  { value: 'fund', label: 'Fund' },
  { value: 'etf', label: 'ETF' },
  { value: 'equity', label: 'Equity' },
  { value: 'bond', label: 'Bond' },
  { value: 'index', label: 'Index' },
  { value: 'fx', label: 'FX' },
  { value: 'cash', label: 'Cash' },
]

function primarySharedIdentifier(instrument: SharedInstrumentRecord) {
  return (
    instrument.identifiers.find((item) => item.is_primary)?.identifier_value ??
    instrument.identifiers[0]?.identifier_value ??
    instrument.asset_id
  )
}

function coverageBadgeClass(coverageState: string | undefined) {
  const normalized = String(coverageState || '').trim().toLowerCase()
  if (normalized === 'coverage') {
    return 'status-badge status-fresh'
  }
  if (normalized === 'watch' || normalized === 'candidate') {
    return 'status-badge status-pending'
  }
  return 'status-badge status-attribute'
}

export default function InstrumentsLibraryPage() {
  const [search, setSearch] = useState('')
  const [assetType, setAssetType] = useState('all')
  const deferredSearch = useDeferredValue(search)
  const [instruments, setInstruments] = useState<SharedInstrumentRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function loadInstruments() {
      setLoading(true)
      setError(null)

      try {
        const results = await getSharedInstruments({
          search: deferredSearch,
          asset_type: assetType === 'all' ? undefined : assetType,
          limit: 100,
        })
        if (cancelled) {
          return
        }
        startTransition(() => {
          setInstruments(results)
        })
      } catch (loadError) {
        if (!cancelled) {
          setError(
            loadError instanceof Error ? loadError.message : 'Failed to load shared instruments.',
          )
          setInstruments([])
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void loadInstruments()
    return () => {
      cancelled = true
    }
  }, [assetType, deferredSearch])

  const coverageCount = useMemo(
    () =>
      instruments.filter(
        (instrument) => String(instrument.coverage_state || '').trim().toLowerCase() === 'coverage',
      ).length,
    [instruments],
  )
  const uniqueTypeCount = useMemo(
    () =>
      new Set(
        instruments
          .map((instrument) => String(instrument.asset_type || '').trim().toLowerCase())
          .filter(Boolean),
      ).size,
    [instruments],
  )
  const searchStateLabel =
    loading && instruments.length
      ? 'Refreshing shared registry...'
      : loading
        ? 'Loading shared registry...'
        : deferredSearch.trim()
          ? `Search matched ${instruments.length} instruments.`
          : `Showing ${instruments.length} instruments from the shared registry.`

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
          <span className="watchlist-breadcrumb-current">Instruments</span>
        </div>
        <div className="panel-header">
          <div>
            <div className="panel-title">Shared Registry</div>
            <h1 className="page-title">Instruments</h1>
          </div>
          <div className="toolbar">
            <Link to="/watchlists" className="toolbar-link">
              Open Watchlists
            </Link>
            <Link to="/monitoring" className="toolbar-link">
              Monitoring
            </Link>
            <a href={PLATFORM_HOME_URL} className="toolbar-link">
              Platform Home
            </a>
          </div>
        </div>
        <div className="instrument-library-shell">
          <div className="instrument-library-summary">
            <div className="instrument-library-stat">
              <span className="instrument-library-stat-label">Visible</span>
              <strong>{instruments.length}</strong>
            </div>
            <div className="instrument-library-stat">
              <span className="instrument-library-stat-label">Coverage</span>
              <strong>{coverageCount}</strong>
            </div>
            <div className="instrument-library-stat">
              <span className="instrument-library-stat-label">Asset Types</span>
              <strong>{uniqueTypeCount}</strong>
            </div>
          </div>
          <div className="instrument-library-toolbar">
            <label className="form-field">
              <span>Search</span>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Name, ticker, ISIN, or internal identifier"
                className="table-input"
              />
            </label>
            <label className="form-field">
              <span>Asset Type</span>
              <select value={assetType} onChange={(event) => setAssetType(event.target.value)}>
                {ASSET_TYPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="instrument-library-note">{searchStateLabel}</p>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">Registry Results</div>
            <div className="watchlists-title">{assetType === 'all' ? 'All Instruments' : formatLabel(assetType)}</div>
          </div>
        </div>
        {error ? <div className="error-state">{error}</div> : null}
        {!error && loading && !instruments.length ? <div className="loading-state">Loading instruments...</div> : null}
        {!error && !loading && !instruments.length ? (
          <div className="empty-state">
            No instruments matched the current search. Shared assets still need to exist in Platform / Instruments before they can be referenced here.
          </div>
        ) : null}
        {!error && instruments.length ? (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Identifier</th>
                  <th>Asset Type</th>
                  <th>Currency</th>
                  <th>Coverage</th>
                  <th>Asset ID</th>
                </tr>
              </thead>
              <tbody>
                {instruments.map((instrument) => (
                  <tr key={instrument.asset_id}>
                    <td>
                      <div className="instrument-library-cell-main">{instrument.asset_name}</div>
                    </td>
                    <td>
                      <span className="ticker-pill">{primarySharedIdentifier(instrument)}</span>
                    </td>
                    <td>{formatLabel(instrument.asset_type)}</td>
                    <td>{instrument.currency}</td>
                    <td>
                      <span className={coverageBadgeClass(instrument.coverage_state)}>
                        {formatLabel(String(instrument.coverage_state || 'unassigned'))}
                      </span>
                    </td>
                    <td className="instrument-library-asset-id">{instrument.asset_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  )
}
