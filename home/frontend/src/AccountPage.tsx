import { LanguageSelector, useLanguage } from '../../../packages/ui/src/i18n'
import { type FormEvent, useEffect, useState } from 'react'
import { accountRequest, type Account, type Member, roleName, type TeamRole } from './accountApi'
import { appPath } from './appPath'
import InfoHint from '../../../packages/ui/src/InfoHint'
import HorizontalTableScroll from '../../../packages/ui/src/HorizontalTableScroll'
import NoticeToast, { type NoticeToastMessage } from '../../../packages/ui/src/NoticeToast'
import '../../../packages/ui/src/notice-toast.css'

function RoleOptions() {
  const { t } = useLanguage()
  return <>{Object.entries(roleName).map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}</>
}

export default function AccountPage() {
  const { t } = useLanguage()
  const [account, setAccount] = useState<Account | null>(null)
  const [loading, setLoading] = useState(true)
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
  useEffect(() => { refresh().catch(error => setError(error.message)).finally(() => setLoading(false)) }, [])
  async function act(work: () => Promise<void>, success = t("Saved.")) {
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
    }, t("Member created. Give the one-time link directly to that person."))
  }
  return <main className="studio-shell account-shell">
    <header className="home-masthead"><a href={appPath('/')}><strong>Investment Studio</strong></a><div className="home-actions"><LanguageSelector /><a href={appPath('/')}>{t("Back to workspaces")}</a></div></header>
    <h1>{t("Account and team")}</h1>
    <NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
    {error ? <p className="account-message" role="alert">{error}</p> : null}
    {loading ? <div className="account-skeleton" role="status" aria-label={t('Loading')} aria-busy="true"><span /><span /><span /></div> : !account ? <p><a href={appPath('/login')}>{t("Sign in")}</a></p> : <>
      <p><span translate="no">{account.team_name}</span> · {t(roleName[account.team_role])}{account.is_team_owner ? ` · ${t('Team owner')}` : ''}</p>
      {account.local_unrestricted ? <p translate="no">{t('Local access records new activity as {name}. No sign-in is required.', { name: account.display_name })}</p> : null}
      {account.mfa_required ? <p role="alert">{t("Enable two-factor authentication before using management features and business apps.")}</p> : null}
      <section className="account-section"><div className="account-section-heading"><h2>{t("Profile")}</h2><InfoHint label={t("About your profile")} detail={t("Changing your display name does not change the authorship of past views.")} /></div>
        <p>{t("Username")}：<span translate="no">{account.username}</span></p>
        <form className="account-form" onSubmit={event => { event.preventDefault(); void act(async () => { await accountRequest('/profile', 'PATCH', { display_name: displayName }); await refresh() }) }}>
          <label>{t("Display name")}<input required maxLength={200} value={displayName} onChange={event => setDisplayName(event.target.value)} /></label>
          <button disabled={busy}>{t("Save display name")}</button>
        </form>
        {!account.local_unrestricted ? <button type="button" disabled={busy} onClick={() => void act(async () => { await accountRequest('/logout-all', 'POST'); leave() })}>{t("Sign out of all devices")}</button> : null}
      </section>
      {!account.local_unrestricted ? <section className="account-section"><h2>{t("Password and two-factor authentication")}</h2>
        <p>{account.mfa_enabled ? t("Two-factor authentication is enabled. Sensitive actions require a fresh six-digit code.") : t("Use an authenticator app with time-based codes to protect your account.")}</p>
        <div className="account-form">
          <label>{t("Current password")}<input type="password" autoComplete="current-password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} /></label>
          <label>{t("Six-digit code")}<input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={otp} onChange={event => setOtp(event.target.value)} /></label>
          <label>{t("New password (at least 12 characters)")}<input type="password" autoComplete="new-password" minLength={12} value={newPassword} onChange={event => setNewPassword(event.target.value)} /></label>
          <button type="button" disabled={busy || newPassword.length < 12 || !currentPassword} onClick={() => void act(async () => { await accountRequest('/password', 'POST', { current_password: currentPassword, password: newPassword, otp: otp || undefined }); leave() })}>{t("Change password and sign out devices")}</button>
          {!account.mfa_enabled && !mfaSecret ? <button type="button" disabled={busy || !currentPassword} onClick={() => void act(async () => {
            const result = await accountRequest<{ secret: string }>('/mfa/setup', 'POST', { password: currentPassword }); setMfaSecret(result.secret)
          }, t("Add the key below to your authenticator, then enter its six-digit code to confirm."))}>{t("Set up two-factor authentication")}</button> : null}
          {mfaSecret ? <div><p>{t('Authenticator account')}：Investment Studio / <span translate="no">{account.username}</span></p><code className="account-secret" translate="no">{mfaSecret}</code><p>{t("Choose “Enter a setup key” and “Time based”, then enter the generated code above.")}</p>
            <button type="button" disabled={busy || !otp} onClick={() => void act(async () => { await accountRequest('/mfa/confirm', 'POST', { password: currentPassword, otp }); setMfaSecret(''); leave() })}>{t("Enable and sign in again")}</button></div> : null}
          {account.mfa_enabled ? <button type="button" disabled={busy || !currentPassword || !otp} onClick={() => void act(async () => { await accountRequest('/mfa/remove', 'POST', { password: currentPassword, otp }); leave() })}>{t("Disable two-factor authentication and sign out devices")}</button> : null}
        </div>
      </section> : null}
      <section className="account-section"><div className="account-section-heading"><h2>{t("Team members")}</h2><InfoHint label={t("Team and portfolio permissions")} detail={t("Research is shared with the team and retains personal authorship. Each portfolio grants access separately; new members do not receive portfolio access automatically.")} /></div>
        <HorizontalTableScroll className="account-table-wrap"><table><thead><tr><th>{t("Member")}</th><th>{t("Team role")}</th><th>{t("Status")}</th>{account.team_role === 'admin' ? <th>{t("Manage")}</th> : null}</tr></thead><tbody>
          {members.map(member => <tr key={member.user_id}><td><span translate="no">{member.display_name}</span><small><span translate="no">{member.username}</span>{member.is_team_owner ? ` · ${t('Owner')}` : ''}</small></td><td>
            {account.team_role === 'admin' && !member.is_team_owner ? <select aria-label={t('Team role for {name}', { name: member.display_name })} disabled={busy} value={member.role} onChange={event => void act(async () => { await accountRequest(`/members/${member.user_id}`, 'PATCH', { role: event.target.value }); await refresh() })}><RoleOptions /></select> : t(roleName[member.role])}
          </td><td>{member.status === 'active' ? t("Active") : member.status === 'invited' ? t("Pending activation") : t("Inactive")}</td>{account.team_role === 'admin' ? <td>
            {!member.is_team_owner ? <button disabled={busy} onClick={() => void act(async () => { await accountRequest(`/members/${member.user_id}`, 'PATCH', { active: !member.active }); await refresh() })}>{member.active ? t("Deactivate") : t("Reactivate")}</button> : null}
            {member.active && (!member.is_team_owner || member.user_id === account.user_id) ? <button disabled={busy} onClick={() => void act(async () => { const result = await accountRequest<{ activation_url: string }>(`/members/${member.user_id}/reset`, 'POST'); setLink(result.activation_url) }, t("A one-time password setup link has been created. Previous sessions are revoked."))}>{member.status === 'invited' ? t("Regenerate invitation") : t("Reset password")}</button> : null}
          </td> : null}</tr>)}
        </tbody></table></HorizontalTableScroll>
        {account.team_role === 'admin' ? <form className="account-form" onSubmit={invite}><h3>{t("Invite a member")}</h3>
          <label>{t("Username")}<input required maxLength={128} pattern="[A-Za-z0-9_.@+\-]+" value={inviteUsername} onChange={event => setInviteUsername(event.target.value)} /></label>
          <label>{t("Display name")}<input required maxLength={200} value={inviteName} onChange={event => setInviteName(event.target.value)} /></label>
          <label>{t("Team role")}<select value={inviteRole} onChange={event => setInviteRole(event.target.value as TeamRole)}><RoleOptions /></select></label>
          <button disabled={busy}>{t("Create member and invitation link")}</button>
        </form> : null}
        {link ? <div className="account-invitation"><p>{t("This link is shown only on this page and expires after 24 hours. Give it directly to the intended member; anyone holding it can set that account’s password.")}</p><textarea aria-label={t("One-time account setup link")} readOnly value={link} rows={3} /><button onClick={() => setLink('')}>{t("Hide link")}</button></div> : null}
        {account.is_team_owner && !account.local_unrestricted ? <div className="account-form"><h3>{t("Transfer team ownership")}</h3><p>{t("Enter your current password and authentication code above. Your research authorship and portfolio permissions will remain unchanged.")}</p>
          <select aria-label={t("New team owner")} value={successor} onChange={event => setSuccessor(event.target.value)}><option value="">{t("Choose the new owner")}</option>{members.filter(member => member.status === 'active' && member.user_id !== account.user_id).map(member => <option key={member.user_id} value={member.user_id} translate="no">{member.display_name}</option>)}</select>
          <button disabled={busy || !successor || !currentPassword} onClick={() => void act(async () => { await accountRequest('/team/transfer', 'POST', { user_id: successor, password: currentPassword, otp: otp || undefined }); await refresh() })}>{t("Transfer ownership")}</button>
        </div> : null}
      </section>
    </>}
  </main>
}
