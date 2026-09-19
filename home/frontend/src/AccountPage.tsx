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
  const [username, setUsername] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const [busy, setBusy] = useState(false)
  const [link, setLink] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [inviteName, setInviteName] = useState('')
  const [inviteRole, setInviteRole] = useState<TeamRole>('member')
  const [successor, setSuccessor] = useState('')

  async function refresh() {
    const [next, directory] = await Promise.all([accountRequest<Account>('/session'), accountRequest<{ members: Member[] }>('/members')])
    setAccount(next)
    setDisplayName(next.display_name)
    setUsername(next.username)
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
      const result = await accountRequest<{ activation_url: string }>('/members', 'POST', { display_name: inviteName, role: inviteRole })
      setLink(result.activation_url); setInviteName(''); await refresh()
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
      <section className="account-section"><div className="account-section-heading"><h2>{t("Profile")}</h2><InfoHint label={t("About your profile")} detail={t("Changing your username or display name preserves your research authorship and portfolio access.")} /></div>
        <form className="account-form" onSubmit={event => { event.preventDefault(); void act(async () => { await accountRequest('/profile', 'PATCH', { username, display_name: displayName }); await refresh() }) }}>
          <label>{t("Username")}<input required autoComplete="username" maxLength={128} pattern="[A-Za-z0-9_.@+\-]+" value={username} onChange={event => setUsername(event.target.value)} /></label>
          <label>{t("Display name")}<input required maxLength={200} value={displayName} onChange={event => setDisplayName(event.target.value)} /></label>
          <button disabled={busy}>{t("Save profile")}</button>
        </form>
        {!account.local_unrestricted ? <button type="button" disabled={busy} onClick={() => void act(async () => { await accountRequest('/logout-all', 'POST'); leave() })}>{t("Sign out of all devices")}</button> : null}
      </section>
      {!account.local_unrestricted ? <section className="account-section"><h2>{t("Password")}</h2>
        <form className="account-form" onSubmit={event => { event.preventDefault(); void act(async () => {
          if (newPassword !== confirmation) throw new Error(t("The passwords do not match."))
          await accountRequest('/password', 'POST', { password: newPassword }); leave()
        }) }}>
          <label>{t("New password (at least 8 characters)")}<input required type="password" autoComplete="new-password" minLength={8} maxLength={256} value={newPassword} onChange={event => setNewPassword(event.target.value)} /></label>
          <label>{t("Confirm password")}<input required type="password" autoComplete="new-password" minLength={8} maxLength={256} value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>
          <button disabled={busy}>{t("Change password and sign out devices")}</button>
        </form>
      </section> : null}
      <section className="account-section"><div className="account-section-heading"><h2>{t("Team members")}</h2><InfoHint label={t("Team and portfolio permissions")} detail={t("Research is shared with the team and retains personal authorship. Each portfolio grants access separately; new members do not receive portfolio access automatically.")} /></div>
        <HorizontalTableScroll className="account-table-wrap"><table><thead><tr><th>{t("Member")}</th><th>{t("Team role")}</th><th>{t("Status")}</th>{account.team_role === 'admin' ? <th>{t("Manage")}</th> : null}</tr></thead><tbody>
          {members.map(member => <tr key={member.user_id}><td><span translate="no">{member.display_name}</span><small><span translate="no">{member.username || t("Chosen during activation")}</span>{member.is_team_owner ? ` · ${t('Owner')}` : ''}</small></td><td>
            {account.team_role === 'admin' && !member.is_team_owner ? <select aria-label={t('Team role for {name}', { name: member.display_name })} disabled={busy} value={member.role} onChange={event => void act(async () => { await accountRequest(`/members/${member.user_id}`, 'PATCH', { role: event.target.value }); await refresh() })}><RoleOptions /></select> : t(roleName[member.role])}
          </td><td>{member.status === 'active' ? t("Active") : member.status === 'invited' ? t("Pending activation") : t("Inactive")}</td>{account.team_role === 'admin' ? <td>
            {!member.is_team_owner ? <button disabled={busy} onClick={() => void act(async () => { await accountRequest(`/members/${member.user_id}`, 'PATCH', { active: !member.active }); await refresh() })}>{member.active ? t("Deactivate") : t("Reactivate")}</button> : null}
            {member.active && (!member.is_team_owner || member.user_id === account.user_id) ? <button disabled={busy} onClick={() => void act(async () => { const result = await accountRequest<{ activation_url: string }>(`/members/${member.user_id}/reset`, 'POST'); setLink(result.activation_url) }, t("A one-time password setup link has been created. Previous sessions are revoked."))}>{member.status === 'invited' ? t("Regenerate invitation") : t("Reset password")}</button> : null}
          </td> : null}</tr>)}
        </tbody></table></HorizontalTableScroll>
        {account.team_role === 'admin' ? <form className="account-form" onSubmit={invite}><h3>{t("Invite a member")}</h3>
          <p>{t("The invited member chooses their own username and password.")}</p>
          <label>{t("Display name")}<input required maxLength={200} value={inviteName} onChange={event => setInviteName(event.target.value)} /></label>
          <label>{t("Team role")}<select value={inviteRole} onChange={event => setInviteRole(event.target.value as TeamRole)}><RoleOptions /></select></label>
          <button disabled={busy}>{t("Create member and invitation link")}</button>
        </form> : null}
        {link ? <div className="account-invitation"><p>{t("This link is shown only on this page and expires after 24 hours. Give it directly to the intended member; anyone holding it can set that account’s password.")}</p><textarea aria-label={t("One-time account setup link")} readOnly value={link} rows={3} /><button onClick={() => setLink('')}>{t("Hide link")}</button></div> : null}
        {account.is_team_owner && !account.local_unrestricted ? <div className="account-form"><h3>{t("Transfer team ownership")}</h3><p>{t("Confirm with your current password. Your research authorship and portfolio permissions will remain unchanged.")}</p>
          <label>{t("Current password")}<input type="password" autoComplete="current-password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} /></label>
          <select aria-label={t("New team owner")} value={successor} onChange={event => setSuccessor(event.target.value)}><option value="">{t("Choose the new owner")}</option>{members.filter(member => member.status === 'active' && member.user_id !== account.user_id).map(member => <option key={member.user_id} value={member.user_id} translate="no">{member.display_name}</option>)}</select>
          <button disabled={busy || !successor || !currentPassword} onClick={() => void act(async () => { await accountRequest('/team/transfer', 'POST', { user_id: successor, password: currentPassword }); await refresh() })}>{t("Transfer ownership")}</button>
        </div> : null}
      </section>
    </>}
  </main>
}
