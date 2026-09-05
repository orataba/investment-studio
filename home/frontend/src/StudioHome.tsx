import { useEffect, useState } from 'react'

import { LanguageSelector } from '../../../packages/ui/src/i18n'
import { resolveWorkspaceUrl } from '../../../packages/ui/src/navigation'
import { appPath } from './appPath'

export type StudioApp = {
  app_id: string
  name: string
  url: string
  eyebrow: string
  description: string
}

export function StudioLinks({ apps }: { apps: StudioApp[] }) {
  return (
    <nav className="home-primary-links" aria-label="Investment workspaces">
      {apps.map((app, index) => (
        <a data-workspace-link href={['watchlist', 'portfolio', 'regime'].includes(app.app_id)
          ? resolveWorkspaceUrl(app.url, app.app_id as 'watchlist' | 'portfolio' | 'regime')
          : app.url} key={app.app_id}>
          <span className="home-link-number">{String(index + 1).padStart(2, '0')}</span>
          <span className="home-link-copy">
            <small>{app.eyebrow}</small>
            <strong>{app.name}</strong>
            <span>{app.description}</span>
          </span>
          <span className="home-link-arrow" aria-hidden="true">↗</span>
        </a>
      ))}
    </nav>
  )
}

export default function StudioHome() {
  const [apps, setApps] = useState<StudioApp[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/apps', { signal: controller.signal })
      .then(async (response): Promise<{ apps: StudioApp[] }> => {
        if (!response.ok) throw new Error('Unable to load workspaces. Please refresh the page.')
        return response.json()
      })
      .then((response) => setApps(response.apps))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : 'Unable to load workspaces.')
        }
      })
    return () => controller.abort()
  }, [])

  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' })
      .catch(() => undefined)
    window.location.assign(appPath('/login'))
  }

  return (
    <main className="studio-shell home-shell">
      <header className="home-masthead">
        <a className="home-brand" href={appPath('/')}><strong>Investment Studio</strong></a>
        <div className="home-actions">
          <LanguageSelector />
          <button className="home-logout" type="button" onClick={logout}>Sign out</button>
        </div>
      </header>
      <section className="home-intro">
        <span>Workspace</span>
        <h1>Choose where to work.</h1>
        <p>Research markets, monitor assets, and manage your portfolios.</p>
      </section>
      {error ? <p role="alert">{error}</p> : apps === null ? (
        <p role="status">Loading workspaces…</p>
      ) : apps.length === 0 ? (
        <p>No workspaces are configured.</p>
      ) : <StudioLinks apps={apps} />}
    </main>
  )
}
