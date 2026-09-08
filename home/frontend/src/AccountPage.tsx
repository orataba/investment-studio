import { type FormEvent, useEffect, useState } from 'react'
import { accountRequest, type Account, type Member, roleName, type TeamRole } from './accountApi'
import { appPath } from './appPath'
import InfoHint from '../../../packages/ui/src/InfoHint'
import NoticeToast, { type NoticeToastMessage } from '../../../packages/ui/src/NoticeToast'
import '../../../packages/ui/src/notice-toast.css'

function RoleOptions() {
  return <>{Object.entries(roleName).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</>
}

export default function AccountPage() {
  const [account, setAccount] = useState<Account | null>(null)
  const [members, setMembers] = useState<Member[]>([])
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const [busy, setBusy] = useState(false)
  const [link, setLink] = useState('')
  const [mfaSecret, setMfaSecret] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [inviteName, setInviteName] = useState('')
  const [inviteUsername, setInviteUsername] = useState('')
  const [inviteRole, setInviteRole] = useState<TeamRole>('member')
  const [successor, setSuccessor] = useState('')

  async function refresh() {
    const next = await accountRequest<Account>('/session')
    setAccount(next)
    setDisplayName(next.display_name)
    const directory = await accountRequest<{ members: Member[] }>('/members')
    setMembers(directory.members)
  }
  useEffect(() => { refresh().catch(error => setError(error.message)) }, [])
  async function act(work: () => Promise<void>, success = '已保存。') {
    setBusy(true); setError(''); setNotice(null)
    try { await work(); setNotice({ id: Date.now(), message: success, tone: 'success' }) }
    catch (error) { setError((error as Error).message) }
    finally { setBusy(false) }
  }
  function leave() { window.location.assign(appPath('/login')) }
  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await act(async () => {
      const result = await accountRequest<{ activation_url: string }>('/members', 'POST', { username: inviteUsername, display_name: inviteName, role: inviteRole })
      setLink(result.activation_url); setInviteName(''); setInviteUsername(''); await refresh()
    }, '成员已建立。请把一次性链接直接交给本人。')
  }
  return <main className="studio-shell account-shell">
    <header className="home-masthead"><a href={appPath('/')}><strong>Investment Studio</strong></a><a href={appPath('/')}>返回工作台</a></header>
    <h1>账号与团队</h1>
    <NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
    {error ? <p className="account-message" role="alert">{error}</p> : null}
    {!account ? <p>请先<a href={appPath('/login')}>登录</a>，再管理账号。</p> : <>
      <p>{account.team_name} · {roleName[account.team_role]}{account.is_team_owner ? ' · 团队拥有者' : ''}</p>
      {account.local_unrestricted ? <p>本机全权限 · 当前以 {account.display_name} 记录新操作，无需登录。</p> : null}
      {account.mfa_required ? <p role="alert">请先启用二步验证，再使用管理功能和业务应用。</p> : null}
      <section className="account-section"><div className="account-section-heading"><h2>个人资料</h2><InfoHint label="个人资料说明" detail="显示名更改不会改变历史观点的人员归属。" /></div>
        <p>登录名：{account.username}</p>
        <form className="account-form" onSubmit={event => { event.preventDefault(); void act(async () => { await accountRequest('/profile', 'PATCH', { display_name: displayName }); await refresh() }) }}>
          <label>显示名<input required maxLength={200} value={displayName} onChange={event => setDisplayName(event.target.value)} /></label>
          <button disabled={busy}>保存显示名</button>
        </form>
        {!account.local_unrestricted ? <button type="button" disabled={busy} onClick={() => void act(async () => { await accountRequest('/logout-all', 'POST'); leave() })}>退出全部设备</button> : null}
      </section>
      {!account.local_unrestricted ? <section className="account-section"><h2>密码与二步验证</h2>
        <p>{account.mfa_enabled ? '二步验证已启用。敏感操作需要新的六位验证码。' : '可使用支持动态验证码的验证器应用，保护账号登录。'}</p>
        <div className="account-form">
          <label>当前密码<input type="password" autoComplete="current-password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} /></label>
          <label>六位验证码<input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={otp} onChange={event => setOtp(event.target.value)} /></label>
          <label>新密码（至少 12 位）<input type="password" autoComplete="new-password" minLength={12} value={newPassword} onChange={event => setNewPassword(event.target.value)} /></label>
          <button type="button" disabled={busy || newPassword.length < 12 || !currentPassword} onClick={() => void act(async () => { await accountRequest('/password', 'POST', { current_password: currentPassword, password: newPassword, otp: otp || undefined }); leave() })}>修改密码并退出设备</button>
          {!account.mfa_enabled && !mfaSecret ? <button type="button" disabled={busy || !currentPassword} onClick={() => void act(async () => {
            const result = await accountRequest<{ secret: string }>('/mfa/setup', 'POST', { password: currentPassword }); setMfaSecret(result.secret)
          }, '请在验证器中添加下方密钥，再输入生成的六位验证码并确认。')}>设置二步验证</button> : null}
          {mfaSecret ? <div><p>验证器账户：Investment Studio / {account.username}</p><code className="account-secret">{mfaSecret}</code><p>选择“输入设置密钥”和“基于时间”，添加后填写上方验证码。</p>
            <button type="button" disabled={busy || !otp} onClick={() => void act(async () => { await accountRequest('/mfa/confirm', 'POST', { password: currentPassword, otp }); setMfaSecret(''); leave() })}>确认启用并重新登录</button></div> : null}
          {account.mfa_enabled ? <button type="button" disabled={busy || !currentPassword || !otp} onClick={() => void act(async () => { await accountRequest('/mfa/remove', 'POST', { password: currentPassword, otp }); leave() })}>关闭二步验证并退出设备</button> : null}
        </div>
      </section> : null}
      <section className="account-section"><div className="account-section-heading"><h2>团队成员</h2><InfoHint label="团队与组合权限" detail="团队共享研究成果并保留个人署名。组合内容由各组合单独授权，新成员不会自动获得组合访问权。" /></div>
        <div className="account-table-wrap"><table><thead><tr><th>成员</th><th>团队角色</th><th>状态</th>{account.team_role === 'admin' ? <th>管理</th> : null}</tr></thead><tbody>
          {members.map(member => <tr key={member.user_id}><td>{member.display_name}<small>{member.username}{member.is_team_owner ? ' · 拥有者' : ''}</small></td><td>
            {account.team_role === 'admin' && !member.is_team_owner ? <select aria-label={`${member.display_name}的团队角色`} disabled={busy} value={member.role} onChange={event => void act(async () => { await accountRequest(`/members/${member.user_id}`, 'PATCH', { role: event.target.value }); await refresh() })}><RoleOptions /></select> : roleName[member.role]}
          </td><td>{member.status === 'active' ? '已启用' : member.status === 'invited' ? '待激活' : '已停用'}</td>{account.team_role === 'admin' ? <td>
            {!member.is_team_owner ? <button disabled={busy} onClick={() => void act(async () => { await accountRequest(`/members/${member.user_id}`, 'PATCH', { active: !member.active }); await refresh() })}>{member.active ? '停用' : '恢复'}</button> : null}
            {member.active && (!member.is_team_owner || member.user_id === account.user_id) ? <button disabled={busy} onClick={() => void act(async () => { const result = await accountRequest<{ activation_url: string }>(`/members/${member.user_id}/reset`, 'POST'); setLink(result.activation_url) }, '已生成一次性密码设置链接，原登录会话已撤销。')}>{member.status === 'invited' ? '重新生成邀请' : '重置密码'}</button> : null}
          </td> : null}</tr>)}
        </tbody></table></div>
        {account.team_role === 'admin' ? <form className="account-form" onSubmit={invite}><h3>邀请成员</h3>
          <label>登录名<input required maxLength={128} pattern="[A-Za-z0-9_.@+\-]+" value={inviteUsername} onChange={event => setInviteUsername(event.target.value)} /></label>
          <label>显示名<input required maxLength={200} value={inviteName} onChange={event => setInviteName(event.target.value)} /></label>
          <label>团队角色<select value={inviteRole} onChange={event => setInviteRole(event.target.value as TeamRole)}><RoleOptions /></select></label>
          <button disabled={busy}>建立成员并生成邀请链接</button>
        </form> : null}
        {link ? <div className="account-invitation"><p>此链接仅显示在当前页面，有效期 24 小时。请直接交给对应成员；访问者可以设置该账号密码。</p><textarea aria-label="一次性账号设置链接" readOnly value={link} rows={3} /><button onClick={() => setLink('')}>隐藏链接</button></div> : null}
        {account.is_team_owner && !account.local_unrestricted ? <div className="account-form"><h3>团队拥有者交接</h3><p>交接需填写上方当前密码及二步验证码。你的原有研究署名和组合权限不会随交接更改。</p>
          <select aria-label="接任团队拥有者" value={successor} onChange={event => setSuccessor(event.target.value)}><option value="">选择接任者</option>{members.filter(member => member.status === 'active' && member.user_id !== account.user_id).map(member => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}</select>
          <button disabled={busy || !successor || !currentPassword} onClick={() => void act(async () => { await accountRequest('/team/transfer', 'POST', { user_id: successor, password: currentPassword, otp: otp || undefined }); await refresh() })}>完成拥有者交接</button>
        </div> : null}
      </section>
    </>}
  </main>
}
