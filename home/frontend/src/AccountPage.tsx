import { LanguageSelector, useLanguage } from '../../../packages/ui/src/i18n'
import { type FormEvent, useEffect, useState } from 'react'
import { accountRequest, type Account, type Member, roleName, type TeamRole } from './accountApi'
import { appPath } from './appPath'
import InfoHint from '../../../packages/ui/src/InfoHint'
import HorizontalTableScroll from '../../../packages/ui/src/HorizontalTableScroll'
import NoticeToast, { LoadingNotice, type NoticeToastMessage } from '../../../packages/ui/src/NoticeToast'
import ConfirmDialog from '../../../packages/ui/src/ConfirmDialog'
import RequestRecovery from '../../../packages/ui/src/RequestRecovery'
import '../../../packages/ui/src/notice-toast.css'

function RoleOptions() {
  const { t } = useLanguage()
  return <>{Object.entries(roleName).map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}</>
}

type MemberAction = { member: Member } & (
  | { kind: 'role'; role: TeamRole }
  | { kind: 'active'; active: boolean }
  | { kind: 'reset' }
  | { kind: 'ownership' }
)

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
  const [busySection, setBusySection] = useState<string | null>(null)
  const busy = busySection !== null
  const [link, setLink] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [inviteName, setInviteName] = useState('')
  const [inviteRole, setInviteRole] = useState<TeamRole>('member')
  const [successor, setSuccessor] = useState('')
  const [pendingAction, setPendingAction] = useState<MemberAction | null>(null)

  async function refresh(resetProfile = false) {
    const [next, directory] = await Promise.all([accountRequest<Account>('/session'), accountRequest<{ members: Member[] }>('/members')])
    setAccount(next)
    if (resetProfile) {
      setDisplayName(next.display_name)
      setUsername(next.username)
    }
    setMembers(directory.members)
  }
  async function loadAccount() {
    setLoading(true); setError('')
    try { await refresh(true) }
    catch (error) { setError((error as Error).message) }
    finally { setLoading(false) }
  }
  useEffect(() => { void loadAccount() }, [])
  async function act(work: () => Promise<void>, success = t("Saved."), section = 'team') {
    setBusySection(section); setError(''); setNotice(null)
    try { await work(); setNotice({ id: Date.now(), message: success, tone: 'success' }) }
    catch (error) { setError((error as Error).message) }
    finally { setBusySection(null) }
  }
  function leave() { window.location.assign(appPath('/login')) }
  function propose(action: MemberAction) { setError(''); setPendingAction(action) }
  async function confirmMemberAction() {
    if (!pendingAction || busy) return
    const action = pendingAction
    await act(async () => {
      if (action.kind === 'reset') {
        const result = await accountRequest<{ activation_url: string }>(`/members/${action.member.user_id}/reset`, 'POST')
        setLink(result.activation_url)
      } else if (action.kind === 'ownership') {
        await accountRequest('/team/transfer', 'POST', { user_id: action.member.user_id, password: currentPassword })
        setCurrentPassword(''); setSuccessor('')
      } else {
        await accountRequest(`/members/${action.member.user_id}`, 'PATCH', action.kind === 'role' ? { role: action.role } : { active: action.active })
      }
      // The mutation succeeded. A subsequent read failure must not offer to
      // repeat a password reset or ownership transfer in the same dialog.
      setPendingAction(null)
      if (action.kind !== 'reset') await refresh().catch(error => {
        setError(`${t('Changes were saved, but the member list could not refresh. Reload to see the current state.')} ${(error as Error).message}`)
      })
    }, action.kind === 'reset' ? t('A one-time password setup link has been created. Previous sessions are revoked.') : t('Saved.'), action.kind === 'ownership' ? 'ownership' : 'team')
  }
  const actionLabel = pendingAction?.kind === 'role' ? t('Change team role')
    : pendingAction?.kind === 'active' ? t(pendingAction.active ? 'Reactivate' : 'Deactivate')
    : pendingAction?.kind === 'ownership' ? t('Transfer ownership')
    : t(pendingAction?.member.status === 'invited' ? 'Regenerate invitation' : 'Reset password')
  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await act(async () => {
      const result = await accountRequest<{ activation_url: string }>('/members', 'POST', { display_name: inviteName, role: inviteRole })
      setLink(result.activation_url); setInviteName(''); await refresh()
    }, t("Member created. Give the one-time link directly to that person."))
  }
  return <><main className="studio-shell account-shell">
    <header className="home-masthead"><a href={appPath('/')}><strong>Investment Studio</strong></a><div className="home-actions"><LanguageSelector /><a href={appPath('/')}>{t("Back to workspaces")}</a></div></header>
    <h1>{t("Account and team")}</h1>
    <NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
    <NoticeToast notice={error && account && !pendingAction ? { id: 0, message: error, tone: 'error' } : null} onDismiss={() => setError('')} />
    {loading ? <div className="account-skeleton" role="status" aria-busy="true" aria-label={t('Loading')}><span /><span /><span /></div> : !account ? <><RequestRecovery error={error || t('Unable to confirm your account. Please retry.')} onRetry={() => void loadAccount()} /><p><a href={appPath('/login')}>{t("Sign in")}</a></p></> : <>
      <p><span translate="no">{account.team_name}</span> · {t(roleName[account.team_role])}{account.is_team_owner ? ` · ${t('Team owner')}` : ''}</p>
      {account.local_unrestricted ? <p translate="no">{t('Local access records new activity as {name}. No sign-in is required.', { name: account.display_name })}</p> : null}
      <nav className="account-page-nav" aria-label={t("Account sections")}><a href="#profile">{t("Profile")}</a>{!account.local_unrestricted && <a href="#security">{t("Security")}</a>}<a href="#team">{t("Team members")}</a>{account.is_team_owner && !account.local_unrestricted && <a href="#ownership">{t("Transfer team ownership")}</a>}</nav>
      <section id="profile" tabIndex={-1} className="account-section"><div className="account-section-heading"><h2>{t("Profile")}</h2><InfoHint label={t("About your profile")} detail={t("Changing your username or display name preserves your research authorship and portfolio access.")} /></div>
        <form className="account-form" onSubmit={event => { event.preventDefault(); void act(async () => { await accountRequest('/profile', 'PATCH', { username, display_name: displayName }); await refresh(true) }, undefined, 'profile') }}>
          <label>{t("Username")}<input required autoComplete="username" maxLength={128} pattern="[A-Za-z0-9_.@+\-]+" value={username} onChange={event => setUsername(event.target.value)} /></label>
          <label>{t("Display name")}<input required maxLength={200} value={displayName} onChange={event => setDisplayName(event.target.value)} /></label>
          <button className="account-submit" disabled={busy}>{t("Save profile")}</button>
          <LoadingNotice active={busySection === 'profile'} message={t('Saving…')} compact />
        </form>
      </section>
      {!account.local_unrestricted ? <section id="security" tabIndex={-1} className="account-section"><h2>{t("Security")}</h2>
        <form className="account-form" onSubmit={event => { event.preventDefault(); void act(async () => {
          if (newPassword !== confirmation) throw new Error(t("The passwords do not match."))
          await accountRequest('/password', 'POST', { password: newPassword }); leave()
        }, undefined, 'security') }}>
          <label>{t("New password (at least 8 characters)")}<input required type="password" autoComplete="new-password" minLength={8} maxLength={256} value={newPassword} onChange={event => setNewPassword(event.target.value)} /></label>
          <label>{t("Confirm password")}<input required type="password" autoComplete="new-password" minLength={8} maxLength={256} value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>
          <button className="account-submit" disabled={busy}>{t("Change password and sign out devices")}</button>
        </form>
        <button type="button" disabled={busy} onClick={() => void act(async () => { await accountRequest('/logout-all', 'POST'); leave() }, undefined, 'security')}>{t("Sign out of all devices")}</button>
        <LoadingNotice active={busySection === 'security'} message={t('Saving…')} compact />
      </section> : null}
      <section id="team" tabIndex={-1} className="account-section"><div className="account-section-heading"><h2>{t("Team members")}</h2><InfoHint label={t("Team and portfolio permissions")} detail={t("Research is shared with the team and retains personal authorship. Each portfolio grants access separately; new members do not receive portfolio access automatically.")} /></div>
        <LoadingNotice active={busySection === 'team'} message={t('Saving…')} compact />
        <HorizontalTableScroll className="account-table-wrap"><table><thead><tr><th>{t("Member")}</th><th>{t("Team role")}</th><th>{t("Status")}</th>{account.team_role === 'admin' ? <th>{t("Manage")}</th> : null}</tr></thead><tbody>
          {members.map(member => <tr key={member.user_id}><td><span translate="no">{member.display_name}</span><small><span translate="no">{member.username || t("Chosen during activation")}</span>{member.is_team_owner ? ` · ${t('Owner')}` : ''}</small></td><td>
            {account.team_role === 'admin' && !member.is_team_owner ? <select aria-label={t('Team role for {name}', { name: member.display_name })} disabled={busy} value={member.role} onChange={event => propose({ kind: 'role', member, role: event.target.value as TeamRole })}><RoleOptions /></select> : t(roleName[member.role])}
          </td><td>{member.status === 'active' ? t("Active") : member.status === 'invited' ? t("Pending activation") : t("Inactive")}</td>{account.team_role === 'admin' ? <td>
            <div className="account-member-actions">{!member.is_team_owner ? <button className={member.active ? 'account-revoke' : undefined} disabled={busy} onClick={() => propose({ kind: 'active', member, active: !member.active })}>{member.active ? t("Deactivate") : t("Reactivate")}</button> : null}
            {member.active && (!member.is_team_owner || member.user_id === account.user_id) ? <button disabled={busy} onClick={() => propose({ kind: 'reset', member })}>{member.status === 'invited' ? t("Regenerate invitation") : t("Reset password")}</button> : null}</div>
          </td> : null}</tr>)}
        </tbody></table></HorizontalTableScroll>
        {account.team_role === 'admin' ? <form className="account-form" onSubmit={invite}><h3>{t("Invite a member")}</h3>
          <p>{t("The invited member chooses their own username and password.")}</p>
          <label>{t("Display name")}<input required maxLength={200} value={inviteName} onChange={event => setInviteName(event.target.value)} /></label>
          <label>{t("Team role")}<select value={inviteRole} onChange={event => setInviteRole(event.target.value as TeamRole)}><RoleOptions /></select></label>
          <button className="account-submit" disabled={busy}>{t("Create member and invitation link")}</button>
        </form> : null}
        {link ? <div className="account-invitation"><p>{t("This link is shown only on this page and expires after 24 hours. Give it directly to the intended member; anyone holding it can set that account’s password.")}</p><textarea aria-label={t("One-time account setup link")} readOnly value={link} rows={3} /><button onClick={() => setLink('')}>{t("Hide link")}</button></div> : null}
        {account.is_team_owner && !account.local_unrestricted ? <div id="ownership" tabIndex={-1} className="account-form account-ownership"><h3>{t("Transfer team ownership")}</h3><p>{t("Confirm with your current password. Your research authorship and portfolio permissions will remain unchanged.")}</p>
          <label>{t("Current password")}<input type="password" autoComplete="current-password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} /></label>
          <select aria-label={t("New team owner")} value={successor} onChange={event => setSuccessor(event.target.value)}><option value="">{t("Choose the new owner")}</option>{members.filter(member => member.status === 'active' && member.user_id !== account.user_id).map(member => <option key={member.user_id} value={member.user_id} translate="no">{member.display_name}</option>)}</select>
          <button disabled={busy || !successor || !currentPassword} onClick={() => { const member = members.find(item => item.user_id === successor); if (member) propose({ kind: 'ownership', member }) }}>{t("Transfer ownership")}</button>
          <LoadingNotice active={busySection === 'ownership'} message={t('Saving…')} compact />
        </div> : null}
      </section>
    </>}
  </main>
  <ConfirmDialog open={pendingAction !== null} title={actionLabel} confirmLabel={actionLabel} busy={busy} busyLabel={t('Saving…')} error={error}
    confirmTone={pendingAction?.kind === 'active' && pendingAction.active ? 'primary' : 'danger'}
    onCancel={() => { setPendingAction(null); setError('') }} onConfirm={confirmMemberAction}
    description={pendingAction && <div className="account-action-summary">
      <p><strong translate="no">{pendingAction.member.display_name}</strong><span translate="no">{pendingAction.member.username || t('Pending activation')}</span></p>
      {pendingAction.kind === 'role' ? <><p>{t(roleName[pendingAction.member.role])} → {t(roleName[pendingAction.role])}</p><p>{t('The new team role applies immediately. Portfolio access is managed separately.')}</p></>
        : pendingAction.kind === 'active' ? <p>{t(pendingAction.active ? 'This account can sign in again. Revoked sessions and setup links will not be restored.' : 'This account will lose access immediately. Existing sessions and setup links will be revoked; research and portfolio records are retained.')}</p>
        : pendingAction.kind === 'ownership' ? <p>{t('This member will become the team owner and administrator. You will remain an administrator; portfolio permissions stay unchanged.')}</p>
        : <p>{t('Existing sessions and setup links will be revoked. A new one-time link will be shown for this member.')}</p>}
    </div>} />
  </>
}
