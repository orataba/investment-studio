import { useEffect, useState } from 'react'

import { LanguageSelector, useLanguage } from '../../../packages/ui/src/i18n'
import { resolveWorkspaceUrl } from '../../../packages/ui/src/navigation'
import { appPath } from './appPath'
import { accountRequest } from './accountApi'
import NoticeToast from '../../../packages/ui/src/NoticeToast'
import '../../../packages/ui/src/notice-toast.css'

export type StudioApp = {
  app_id: string
  name: string
  url: string
  eyebrow: string
  description: string
}

export function StudioLinks({ apps }: { apps: StudioApp[] }) {
  const { t } = useLanguage()
  return (
    <nav className="home-primary-links" aria-label={t('Investment workspaces')}>
      {apps.map((app, index) => (
        <a data-workspace-link data-workspace={app.app_id} href={['watchlist', 'portfolio', 'regime', 'briefing'].includes(app.app_id)
          ? resolveWorkspaceUrl(app.url, app.app_id as 'watchlist' | 'portfolio' | 'regime' | 'briefing')
          : app.url} key={app.app_id}>
          <span className="home-link-number">{String(index + 1).padStart(2, '0')}</span>
          <span className="home-link-copy">
            <small>{t(app.eyebrow)}</small>
            <strong>{t(app.name)}</strong>
            <span>{t(app.description)}</span>
          </span>
          <svg className="home-link-arrow" aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M6 18 18 6M6 6h12v12" /></svg>
        </a>
      ))}
    </nav>
  )
}

export async function signOut() {
  await accountRequest('/logout', 'POST')
  window.location.assign(appPath('/login'))
}

export default function StudioHome() {
  const { t } = useLanguage()
  const [apps, setApps] = useState<StudioApp[] | null>(null)
  const [error, setError] = useState('')
  const [logoutError, setLogoutError] = useState('')
  const [signingOut, setSigningOut] = useState(false)
  const [account, setAccount] = useState<{ display_name: string, local_unrestricted?: boolean } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/auth/session', { signal: controller.signal, credentials: 'same-origin' })
      .then(response => response.ok ? response.json() : null)
      .then(setAccount).catch(() => undefined)
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
    if (signingOut) return
    setSigningOut(true)
    setLogoutError('')
    try {
      await signOut()
    } catch {
      setLogoutError(t('Sign-out failed. Your session may still be active. Please try again.'))
    } finally {
      setSigningOut(false)
    }
  }

  return (
    <main className="studio-shell home-shell">
      <NoticeToast notice={error ? { id: 0, message: error, tone: 'error' } : null} onDismiss={() => setError('')} />
      <NoticeToast notice={logoutError ? { id: 1, message: logoutError, tone: 'error' } : null} onDismiss={() => setLogoutError('')} />
      <header className="home-masthead">
        <a className="home-brand" href={appPath('/')}><strong>Investment Studio</strong></a>
        <div className="home-actions">
          <LanguageSelector />
          {account ? <>
            <a href={appPath('/account')}><span translate="no">{account.display_name}</span> · {t("Account and team")}</a>
            {account.local_unrestricted ? <span>{t("Local unrestricted access")}</span> : <button className="home-logout" type="button" onClick={logout} disabled={signingOut}>{signingOut ? t('Signing out…') : t('Sign out')}</button>}
          </> : <a href={appPath('/login')}>{t("Sign in")}</a>}
        </div>
      </header>
      <section className="home-intro">
        <span>Workspace</span>
        <h1>Choose where to work.</h1>
        <p>Research markets, monitor assets, and manage your portfolios.</p>
      </section>
      {error ? null : apps === null ? (
        <div className="home-links-skeleton" role="status" aria-label={t('Loading')}>{[0, 1, 2, 3].map(key => <span key={key} />)}</div>
      ) : apps.length === 0 ? (
        <p>No workspaces are configured.</p>
      ) : <StudioLinks apps={apps} />}
    </main>
  )
}
