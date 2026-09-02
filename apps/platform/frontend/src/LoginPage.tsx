import { type FormEvent, useState } from 'react'


function destinationAfterLogin() {
  const requested = new URLSearchParams(window.location.search).get('next')
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
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

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
          ? '账号或密码不正确。'
          : '登录服务暂时不可用，请稍后再试。')
        return
      }
      window.location.assign(destinationAfterLogin())
    } catch {
      setError('无法连接登录服务，请检查网络后重试。')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel" aria-labelledby="login-title">
        <div className="login-brand">
          <span>Portfolio Operations</span>
          <strong>Yungu Workbench</strong>
        </div>
        <div className="login-copy">
          <span>Private workspace</span>
          <h1 id="login-title">登录</h1>
          <p>请输入账号和密码，进入云谷投资工作台。</p>
        </div>
        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            <span>账号</span>
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
            <span>密码</span>
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
            {submitting ? '正在登录…' : '进入工作台'}
          </button>
        </form>
      </section>
    </main>
  )
}
