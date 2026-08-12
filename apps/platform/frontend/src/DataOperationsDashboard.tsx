import { useEffect, useState } from 'react'

import { LanguageSelector } from '../../../../packages/ui/src/i18n'
import { fetchJson } from './instrumentRegistryModel'

type PlatformAppsResponse = {
  apps: Array<{ app_id: string; url: string }>
}

const WATCHLIST_URL = import.meta.env.VITE_WATCHLIST_URL || '/watchlist'
const PORTFOLIO_URL = import.meta.env.VITE_PORTFOLIO_URL || '/portfolio'

export default function DataOperationsDashboard() {
  const [watchlistUrl, setWatchlistUrl] = useState(WATCHLIST_URL)
  const [portfolioUrl, setPortfolioUrl] = useState(PORTFOLIO_URL)

  useEffect(() => {
    let cancelled = false
    fetchJson<PlatformAppsResponse>('/api/apps')
      .then((response) => {
        if (cancelled) return
        setWatchlistUrl(
          response.apps.find((app) => app.app_id === 'watchlist')?.url
            || WATCHLIST_URL,
        )
        setPortfolioUrl(
          response.apps.find((app) => app.app_id === 'portfolio')?.url
            || PORTFOLIO_URL,
        )
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main className="platform-shell home-shell">
      <header className="home-masthead">
        <a className="home-brand" href="/">
          <span>Portfolio Operations</span>
          <strong>Workbench</strong>
        </a>
        <LanguageSelector />
      </header>

      <section className="home-intro">
        <span>Workspace</span>
        <h1>Choose where to work.</h1>
        <p>
          Open an investment workspace or maintain the shared instrument database.
        </p>
      </section>

      <nav className="home-primary-links" aria-label="Investment workspaces">
        <a href={watchlistUrl}>
          <span className="home-link-number">01</span>
          <span className="home-link-copy">
            <small>Research and monitoring</small>
            <strong>Watchlist</strong>
            <span>Review funds, indexes, watchlists, and instrument research.</span>
          </span>
          <span className="home-link-arrow" aria-hidden="true">↗</span>
        </a>
        <a href={portfolioUrl}>
          <span className="home-link-number">02</span>
          <span className="home-link-copy">
            <small>Portfolio management</small>
            <strong>Portfolio</strong>
            <span>Manage holdings, transactions, performance, risk, and research.</span>
          </span>
          <span className="home-link-arrow" aria-hidden="true">↗</span>
        </a>
      </nav>

      <a className="home-registry-link" href="/instruments">
        <span className="home-link-number">03</span>
        <span className="home-registry-link-copy">
          <strong>Instrument Registry</strong>
          <small>Shared instruments and market data</small>
        </span>
        <span className="home-link-arrow" aria-hidden="true">→</span>
      </a>
    </main>
  )
}
