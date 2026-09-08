import { useEffect, useState } from 'react'
import { getPortfolioMembers, getPortfolioMemberCandidates, setPortfolioMember, removePortfolioMember, type PortfolioMember, type PortfolioRole } from '../lib/api'

const labels: Record<PortfolioRole, string> = { manager: '管理者', editor: '编辑者', viewer: '只读' }
export default function PortfolioMembersSettings({ portfolioId }: { portfolioId: string }) {
  const [members, setMembers] = useState<PortfolioMember[]>([])
  const [candidates, setCandidates] = useState<Array<{ user_id: string; display_name: string }>>([])
  const [userId, setUserId] = useState('')
  const [role, setRole] = useState<PortfolioRole>('viewer')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function load() {
    const [current, available] = await Promise.all([getPortfolioMembers(portfolioId), getPortfolioMemberCandidates(portfolioId)])
    setMembers(current.members); setCandidates(available.members)
  }
  useEffect(() => { void load().catch(reason => setError(reason.message)) }, [portfolioId])
  async function change(id: string, nextRole: PortfolioRole | null) {
    setBusy(true); setError(null)
    try { if (nextRole) await setPortfolioMember(portfolioId, id, nextRole); else await removePortfolioMember(portfolioId, id); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '修改权限失败') }
    finally { setBusy(false) }
  }
  return <section className="portfolio-members-settings" aria-label="组合权限">
      <h3>组合权限</h3>
      <p>仅获授权的成员可以看到此组合。只读成员可查看完整资料并使用研究助手；编辑者可维护业务数据；管理者还可变更授权。</p>
      {error && <p role="alert" className="inline-notice-error">{error}</p>}
      <table><thead><tr><th>成员</th><th>权限</th><th>操作</th></tr></thead><tbody>{members.map(member => <tr key={member.user_id}>
        <td>{member.display_name}</td><td><select aria-label={`${member.display_name}的权限`} value={member.role} disabled={busy} onChange={event => void change(member.user_id, event.target.value as PortfolioRole)}>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></td>
        <td><button type="button" disabled={busy} onClick={() => void change(member.user_id, null)}>移除权限</button></td>
      </tr>)}</tbody></table>
      <form onSubmit={event => { event.preventDefault(); if (userId) void change(userId, role) }} className="portfolio-members-add">
        <label>添加成员<select required value={userId} onChange={event => setUserId(event.target.value)}><option value="">选择团队成员</option>{candidates.filter(candidate => !members.some(member => member.user_id === candidate.user_id)).map(candidate => <option key={candidate.user_id} value={candidate.user_id}>{candidate.display_name}</option>)}</select></label>
        <label>权限<select value={role} onChange={event => setRole(event.target.value as PortfolioRole)}>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <button type="submit" disabled={!userId || busy}>授予权限</button>
      </form>
      <p>交接管理权时，先添加另一位管理者，再调整原管理者权限。</p>
    </section>
}
