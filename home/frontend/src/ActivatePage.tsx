import { LanguageSelector, useLanguage } from '../../../packages/ui/src/i18n'
import { type FormEvent, useEffect, useState } from 'react'
import { accountRequest } from './accountApi'
import { appPath } from './appPath'

export default function ActivatePage() {
  const { t } = useLanguage()
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('token') || '')
  useEffect(() => {
    // Keep one-time secrets out of server logs, referrers and the visible address.
    window.history.replaceState(null, '', window.location.pathname)
  }, [])
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [message, setMessage] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (password !== confirmation) { setMessage(t("The passwords do not match.")); return }
    setBusy(true)
    try {
      await accountRequest('/activate', 'POST', { token, password })
      setSaved(true)
      setMessage(t("Your password has been set. Sign in to continue. Other devices have been signed out."))
    } catch (error) { setMessage((error as Error).message) }
    finally { setBusy(false) }
  }
  return <main className="login-shell"><section className="login-panel">
    <div className="login-brand"><strong>Investment Studio</strong><LanguageSelector /></div><h1>{t("Set your account password")}</h1>
    <p>{t("Invitation and password reset links can be used once and expire after 24 hours.")}</p>
    {!saved && token ? <form className="login-form" onSubmit={submit}>
      <label>{t("New password")}<input type="password" autoComplete="new-password" minLength={12} maxLength={256} required value={password} onChange={event => setPassword(event.target.value)} /></label>
      <label>{t("Confirm password")}<input type="password" autoComplete="new-password" minLength={12} maxLength={256} required value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>
      <button disabled={busy}>{busy ? t("Saving…") : t("Set password")}</button>
    </form> : null}
    {!token ? <p role="alert">{t("The link is missing its credential. Use the complete link provided by your administrator.")}</p> : null}
    {message ? <p role="status">{message}</p> : null}
    <a href={appPath('/login')}>{t("Back to sign in")}</a>
  </section></main>
}
