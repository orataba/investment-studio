import { type FormEvent, useEffect, useState } from 'react'
import { LanguageSelector, useLanguage } from '../../../packages/ui/src/i18n'
import { withLanguage } from '../../../packages/ui/src/navigation'

function requestedLoginDestination(search: string) {
  const directPrefix = '?next='
  if (search.startsWith(directPrefix)) {
    const rawDestination = search.slice(directPrefix.length)
    if (/^(?:https?:\/\/|\/)/.test(rawDestination)) {
      return rawDestination
    }
    try {
      return decodeURIComponent(rawDestination)
    } catch {
      return null
    }
  }
  return new URLSearchParams(search).get('next')
}

export function destinationAfterLogin() {
  const requested = requestedLoginDestination(window.location.search)
  if (!requested) return '/'
  try {
    const candidate = new URL(requested, window.location.origin)
    const rootHost = window.location.hostname
    const trustedHost = candidate.hostname === rootHost
      || candidate.hostname.endsWith(`.${rootHost}`)
    if (candidate.protocol === window.location.protocol && trustedHost) {
      return candidate.href
    }
  } catch {
    return '/'
  }
  return '/'
}

export default function LoginPage() {
  const { t } = useLanguage()
  const { language } = useLanguage()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [local, setLocal] = useState(false)

  useEffect(() => {
    fetch('/api/auth/session', { credentials: 'same-origin', cache: 'no-store' })
      .then(response => response.ok ? response.json() : null)
      .then(account => { if (account?.local_unrestricted) setLocal(true) })
      .catch(() => undefined)
  }, [])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setError('')
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (!response.ok) {
        setError(response.status === 401
          ? t("The username or password is incorrect.")
          : 'Sign-in is temporarily unavailable. Please try again later.')
        return
      }
      window.location.assign(withLanguage(destinationAfterLogin(), language))
    } catch {
      setError('Unable to connect. Check your connection and try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel" aria-labelledby="login-title">
        <div className="login-brand">
          <strong>Investment Studio</strong>
          <LanguageSelector />
        </div>
        <div className="login-copy">
          <span>Private workspace</span>
          <h1 id="login-title">{local ? t("Local unrestricted access") : 'Sign in'}</h1>
          <p>{local ? t("The local workspace does not require sign-in.") : 'Enter your username and password to access Investment Studio.'}</p>
        </div>
        {local ? <a href={withLanguage(destinationAfterLogin(), language)}>{t("Open workspaces")}</a> : <form className="login-form" onSubmit={handleSubmit}>
          <label>
            <span>Username</span>
            <input
              autoComplete="username"
              autoFocus
              name="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </label>
          <label>
            <span>Password</span>
            <input
              autoComplete="current-password"
              name="password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error ? <p className="login-error" role="alert">{error}</p> : null}
          <button disabled={submitting} type="submit">
            {submitting ? 'Signing in…' : 'Enter Investment Studio'}
          </button>
        </form>}
      </section>
    </main>
  )
}
