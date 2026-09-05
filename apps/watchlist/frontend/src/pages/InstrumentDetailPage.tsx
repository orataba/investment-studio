import { LanguageSelector } from '../../../../../packages/ui/src/i18n'
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'

import PrivateFundDetailPage from './PrivateFundDetailPage'
import PublicFundDetailPage from './PublicFundDetailPage'
import ListedInstrumentDetailPage from './ListedInstrumentDetailPage'
import LoadingOverlay from '../components/LoadingOverlay'
import {
  getWatchlistDetail,
  resolveInstrumentDetail,
  type InstrumentResolveResponse,
  type WatchlistDetail,
} from '../lib/api'
import { buildWatchlistPath, HOME_URL } from '../lib/navigation'

type WatchlistBreadcrumbContext = {
  watchlistId: string
  watchlistName: string
}

export default function InstrumentDetailPage() {
  const { instrumentId = '' } = useParams()
  const [searchParams] = useSearchParams()
  const watchlistId = (searchParams.get('watchlist') || '').trim()
  const [instrument, setInstrument] = useState<InstrumentResolveResponse | null>(null)
  const [watchlistContext, setWatchlistContext] = useState<WatchlistBreadcrumbContext | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function loadInstrument() {
      setLoading(true)
      setError(null)

      try {
        const [response, watchlist] = await Promise.all([
          resolveInstrumentDetail(instrumentId),
          watchlistId
            ? getWatchlistDetail(watchlistId).catch(() => null as WatchlistDetail | null)
            : Promise.resolve(null),
        ])
        if (!cancelled) {
          setInstrument(response)
          setWatchlistContext(
            watchlistId
              ? {
                  watchlistId,
                  watchlistName: watchlist?.name || watchlistId,
                }
              : null,
          )
        }
      } catch (resolveError) {
        if (!cancelled) {
          setError(
            resolveError instanceof Error
              ? resolveError.message
              : 'Failed to resolve detail.',
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
  }, [instrumentId, watchlistId])

  if (loading) {
    return <LoadingOverlay label="Loading detail" />
  }

  if (error || !instrument) {
    return (
      <section className="panel">
        <div className="error-state">{error || 'Detail unavailable.'}</div>
      </section>
    )
  }

  if (
    instrument.detail_supported &&
    ['etf', 'equity', 'index'].includes(instrument.instrument_type) &&
    instrument.detail_subject_id
  ) {
    return (
      <ListedInstrumentDetailPage
        instrument={instrument}
        watchlistContext={watchlistContext}
      />
    )
  }

  if (
    instrument.detail_supported &&
    instrument.instrument_type === 'public_fund' &&
    instrument.detail_subject_id
  ) {
    return (
      <PublicFundDetailPage
        fundId={instrument.detail_subject_id}
        watchlistContext={watchlistContext}
        corporateActions={instrument.corporate_actions}
      />
    )
  }

  if (
    instrument.detail_supported &&
    instrument.instrument_type === 'private_fund' &&
    instrument.detail_subject_id
  ) {
    return (
      <PrivateFundDetailPage
        fundId={instrument.detail_subject_id}
        watchlistContext={watchlistContext}
        corporateActions={instrument.corporate_actions}
      />
    )
  }

  return (
    <section className="panel">
      <div className="instrument-detail-breadcrumbs">
        <a data-workspace-link href={HOME_URL} className="watchlist-breadcrumb-link">
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
              {watchlistContext?.watchlistName || watchlistId}
            </Link>
          </>
        ) : null}
        <span className="watchlist-breadcrumb-separator">/</span>
        <span className="watchlist-breadcrumb-current" translate="no">{instrument.instrument_name}</span>
        <LanguageSelector />
      </div>
      <div className="panel-header">
        <div>
          <div className="panel-title">Unsupported Detail</div>
          <h1 className="page-title" translate="no">{instrument.instrument_name}</h1>
        </div>
      </div>
      <div className="instrument-detail-body">
        <p>
          Watchlist supports public fund, private fund, ETF, equity, and index detail workspaces.{' '}
          <strong>{instrument.instrument_type}</strong> instruments can exist in shared asset data, but they do not
          have a local watchlist detail workspace yet.
        </p>
        <p className="muted">
          Instrument ID: {instrument.requested_instrument_id}
          {instrument.primary_identifier ? ` · ${instrument.primary_identifier}` : ''}
        </p>
      </div>
    </section>
  )
}
