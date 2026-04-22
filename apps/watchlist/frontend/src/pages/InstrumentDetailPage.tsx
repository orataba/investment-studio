import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import FundDetailPage from './FundDetailPage'
import {
  resolveInstrumentDetail,
  type InstrumentResolveResponse,
} from '../lib/api'
import { buildWatchlistPath, PLATFORM_HOME_URL } from '../lib/navigation'

export default function InstrumentDetailPage() {
  const { assetId = '' } = useParams()
  const [searchParams] = useSearchParams()
  const watchlistId = (searchParams.get('watchlist') || '').trim()
  const [instrument, setInstrument] = useState<InstrumentResolveResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function loadInstrument() {
      setLoading(true)
      setError(null)

      try {
        const response = await resolveInstrumentDetail(assetId)
        if (!cancelled) {
          setInstrument(response)
        }
      } catch (resolveError) {
        if (!cancelled) {
          setError(
            resolveError instanceof Error
              ? resolveError.message
              : 'Failed to resolve instrument detail.',
          )
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void loadInstrument()
    return () => {
      cancelled = true
    }
  }, [assetId])

  if (loading) {
    return (
      <section className="panel">
        <div className="loading-state">Loading instrument detail...</div>
      </section>
    )
  }

  if (error || !instrument) {
    return (
      <section className="panel">
        <div className="error-state">{error || 'Instrument detail unavailable.'}</div>
      </section>
    )
  }

  if (
    instrument.detail_supported &&
    instrument.detail_view_type === 'fund' &&
    instrument.detail_subject_id
  ) {
    return <FundDetailPage fundId={instrument.detail_subject_id} />
  }

  return (
    <section className="panel">
      <div className="stub-breadcrumbs">
        <a href={PLATFORM_HOME_URL} className="watchlist-breadcrumb-link">
          Home
        </a>
        <span className="watchlist-breadcrumb-separator">/</span>
        <Link to="/watchlists" className="watchlist-breadcrumb-link">
          Watchlist
        </Link>
        {watchlistId ? (
          <>
            <span className="watchlist-breadcrumb-separator">/</span>
            <Link to={buildWatchlistPath(watchlistId)} className="watchlist-breadcrumb-link">
              {watchlistId}
            </Link>
          </>
        ) : null}
        <span className="watchlist-breadcrumb-separator">/</span>
        <span className="watchlist-breadcrumb-current">{instrument.asset_name}</span>
      </div>
      <div className="panel-header">
        <div>
          <div className="panel-title">Instrument Detail</div>
          <h1 className="page-title">{instrument.asset_name}</h1>
        </div>
      </div>
      <div className="stub-body">
        <p>
          This watchlist release is fund-only. <strong>{instrument.asset_type}</strong> instruments can exist in the
          shared registry, but they do not have a local watchlist detail workspace yet.
        </p>
        <p className="muted">
          Asset ID: {instrument.requested_asset_id}
          {instrument.primary_identifier ? ` · ${instrument.primary_identifier}` : ''}
        </p>
      </div>
    </section>
  )
}
