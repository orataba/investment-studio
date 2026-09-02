import { useEffect, useState } from 'react'

import { LanguageSelector } from '../../../../packages/ui/src/i18n'
import { appPath } from './appPath'
import { fetchJson } from './instrumentRegistryModel'

type PlatformAppsResponse = {
  apps: Array<{ app_id: string; url: string }>
}

const WATCHLIST_URL = import.meta.env.VITE_WATCHLIST_URL || '/watchlist'
const PORTFOLIO_URL = import.meta.env.VITE_PORTFOLIO_URL || '/portfolio'
const REGIME_URL = import.meta.env.VITE_REGIME_URL || 'http://127.0.0.1:3010'

export default function DataOperationsDashboard() {
  const [watchlistUrl, setWatchlistUrl] = useState(WATCHLIST_URL)
  const [portfolioUrl, setPortfolioUrl] = useState(PORTFOLIO_URL)
  const [regimeUrl, setRegimeUrl] = useState(REGIME_URL)

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
        setRegimeUrl(
          response.apps.find((app) => app.app_id === 'regime')?.url
            || REGIME_URL,
        )
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  async function logout() {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
    }).catch(() => undefined)
    window.location.assign(appPath('/login'))
  }

  return (
    <main className="platform-shell home-shell">
      <header className="home-masthead">
        <a className="home-brand" href={appPath('/')}>
          <span>Portfolio Operations</span>
          <strong>Workbench</strong>
        </a>
        <div className="home-actions">
          <LanguageSelector />
          <button className="home-logout" type="button" onClick={logout}>退出</button>
        </div>
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
        <a href={appPath('/instruments')}>
          <span className="home-link-number">03</span>
          <span className="home-link-copy">
            <small>Shared database ops</small>
            <strong>Instrument Registry</strong>
            <span>Maintain shared instruments, identifiers, FX, NAV, and market data.</span>
          </span>
          <span className="home-link-arrow" aria-hidden="true">→</span>
        </a>
        <a href={regimeUrl}>
          <span className="home-link-number">04</span>
          <span className="home-link-copy">
            <small>Market regime</small>
            <strong>Regime Dashboard</strong>
            <span>Review current market regimes, signals, and release evidence.</span>
          </span>
          <span className="home-link-arrow" aria-hidden="true">↗</span>
        </a>
      </nav>
    </main>
  )
}
