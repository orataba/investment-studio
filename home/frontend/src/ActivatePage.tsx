import { LanguageSelector, useLanguage } from '../../../packages/ui/src/i18n'
import { type FormEvent, useEffect, useState } from 'react'
import { accountRequest } from './accountApi'
import { appPath } from './appPath'

type ActivationInfo = { username: string | null; display_name: string; choose_username: boolean }

export default function ActivatePage() {
  const { t } = useLanguage()
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('token') || '')
  const [account, setAccount] = useState<ActivationInfo | null>(null)
  const [loading, setLoading] = useState(Boolean(token))
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [message, setMessage] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    // One-time secrets stay out of server logs, referrers and the visible address.
    window.history.replaceState(null, '', window.location.pathname)
    if (!token) return
    let cancelled = false
    accountRequest<ActivationInfo>('/activation-info', 'POST', { token })
      .then(info => { if (!cancelled) { setAccount(info); setUsername(info.username || '') } })
      .catch(error => { if (!cancelled) setMessage(error.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [token])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (password !== confirmation) { setMessage(t('The passwords do not match.')); return }
    setBusy(true)
    try {
      await accountRequest('/activate', 'POST', { token, password, ...(account?.choose_username ? { username } : {}) })
      setUsername(username.toLowerCase())
      setSaved(true)
      setMessage(t('Your username is {username}. Sign in with your new password.', { username: username.toLowerCase() }))
    } catch (error) { setMessage((error as Error).message) }
    finally { setBusy(false) }
  }
  return <main className="login-shell"><section className="login-panel">
    <div className="login-brand"><strong>Investment Studio</strong><LanguageSelector /></div><h1>{t('Set your account password')}</h1>
    <p>{t('Invitation and password reset links can be used once and expire after 24 hours.')}</p>
    {loading ? <p role="status">{t('Loading account…')}</p> : null}
    {!saved && account ? <form className="login-form" onSubmit={submit}>
      <p>{t('Account for {name}', { name: account.display_name })}</p>
      {account.choose_username ? <><label>{t('Username')}<input autoComplete="username" required maxLength={128} pattern="[A-Za-z0-9_.@+\-]+" value={username} onChange={event => setUsername(event.target.value)} /></label><p>{t('Choose a username to sign in with.')}</p></> : <p>{t('Username')}: <strong translate="no">{username}</strong></p>}
      <label>{t('New password (at least 8 characters)')}<input type="password" autoComplete="new-password" minLength={8} maxLength={256} required value={password} onChange={event => setPassword(event.target.value)} /></label>
      <label>{t('Confirm password')}<input type="password" autoComplete="new-password" minLength={8} maxLength={256} required value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>
      <button disabled={busy}>{busy ? t('Saving…') : t('Set password')}</button>
    </form> : null}
    {!token ? <p role="alert">{t('The link is missing its credential. Use the complete link provided by your administrator.')}</p> : null}
    {message ? <p role="status">{message}</p> : null}
    <a href={appPath('/login')}>{t('Back to sign in')}</a>
  </section></main>
}
