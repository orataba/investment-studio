export type TeamRole = 'admin' | 'member' | 'reader'
export type Account = {
  user_id: string
  username: string
  display_name: string
  team_name: string
  team_role: TeamRole
  is_team_owner: boolean
  mfa_enabled: boolean
  mfa_required: boolean
  local_unrestricted?: boolean
}
export type Member = {
  user_id: string
  username: string
  display_name: string
  role: TeamRole
  active: boolean
  status: 'active' | 'invited' | 'inactive'
  is_team_owner: boolean
}
export const roleName: Record<TeamRole, string> = { admin: '团队管理员', member: '研究成员', reader: '只读成员' }

export async function accountRequest<T = unknown>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/auth${path}`, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  })
  if (!response.ok) {
    const value = await response.json().catch(() => ({}))
    throw new Error(typeof value.detail === 'string' ? value.detail : '操作未完成，请检查输入后重试。')
  }
  return response.status === 204 ? undefined as T : response.json()
}
