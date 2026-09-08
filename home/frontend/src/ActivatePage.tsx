import { type FormEvent, useEffect, useState } from 'react'
import { accountRequest } from './accountApi'
import { appPath } from './appPath'

export default function ActivatePage() {
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
    if (password !== confirmation) { setMessage('两次密码不一致。'); return }
    setBusy(true)
    try {
      await accountRequest('/activate', 'POST', { token, password })
      setSaved(true)
      setMessage('密码已设置，请登录。已登录的其他设备也已退出。')
    } catch (error) { setMessage((error as Error).message) }
    finally { setBusy(false) }
  }
  return <main className="login-shell"><section className="login-panel">
    <strong>Investment Studio</strong><h1>设置账号密码</h1>
    <p>邀请和密码重置链接仅能使用一次，有效期为 24 小时。</p>
    {!saved && token ? <form className="login-form" onSubmit={submit}>
      <label>新密码<input type="password" autoComplete="new-password" minLength={12} maxLength={256} required value={password} onChange={event => setPassword(event.target.value)} /></label>
      <label>再次输入<input type="password" autoComplete="new-password" minLength={12} maxLength={256} required value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>
      <button disabled={busy}>{busy ? '正在保存…' : '设置密码'}</button>
    </form> : null}
    {!token ? <p role="alert">链接缺少凭证，请使用管理员提供的完整链接。</p> : null}
    {message ? <p role="status">{message}</p> : null}
    <a href={appPath('/login')}>返回登录</a>
  </section></main>
}
