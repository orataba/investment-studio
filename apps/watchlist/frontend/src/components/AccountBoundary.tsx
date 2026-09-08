import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { resolveWorkspaceUrl } from '../../../../../packages/ui/src/navigation'
import { API_BASE_URL } from '../lib/api'

export type StudioAccount = { user_id: string; display_name: string; team_id: string; team_role: 'admin' | 'member' | 'reader'; local_unrestricted?: boolean }
const AccountContext = createContext<StudioAccount | null>(null)
let activeAccountId = ''
export const accountStorageKey = (key: string) => `${key}:${activeAccountId}`
export const useStudioAccount = () => useContext(AccountContext)
export const useCanWriteTeam = () => {
  const account = useStudioAccount()
  return Boolean(account?.local_unrestricted) || account?.team_role !== 'reader'
}

export default function AccountBoundary({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<StudioAccount | null>(null)
  const [error, setError] = useState('')
  const [needsLogin, setNeedsLogin] = useState(false)
  useEffect(() => {
    let cancelled = false
    let latestRequest = 0
    async function refresh() {
      const request = ++latestRequest
      setNeedsLogin(false)
      try {
        const response = await fetch(`${API_BASE_URL}/api/identity`, { credentials: 'include', cache: 'no-store' })
        if (!response.ok) {
          if (cancelled || request !== latestRequest) return
          setNeedsLogin(response.status === 401)
          throw new Error(response.status === 401 ? '请登录 Investment Studio' : '暂时无法确认账号权限')
        }
        const next = await response.json() as StudioAccount
        if (cancelled || request !== latestRequest) return
        activeAccountId = next.user_id
        setAccount(next); setError('')
      } catch (reason) {
        if (!cancelled && request === latestRequest) { activeAccountId = ''; setAccount(null); setError(reason instanceof Error ? reason.message : '账号校验失败') }
      }
    }
    void refresh()
    window.addEventListener('focus', refresh)
    window.addEventListener('studio:unauthorized', refresh)
    return () => { cancelled = true; window.removeEventListener('focus', refresh); window.removeEventListener('studio:unauthorized', refresh) }
  }, [])
  const home = resolveWorkspaceUrl(undefined, 'home')
  if (!account) return <section className="studio-account-message"><p>{error || '正在确认账号…'}</p>{needsLogin && <a href={`${home}/login?next=${encodeURIComponent(window.location.href)}`}>前往登录</a>}</section>
  return <AccountContext.Provider value={account}>
    <div className="studio-account-bar"><span>{account.display_name} · {account.local_unrestricted ? '本机全权限' : `团队${account.team_role === 'reader' ? '只读' : '协作'}`}</span>{!account.local_unrestricted && <a href={`${home}/account`}>账号设置</a>}</div>
    <div key={`${account.user_id}:${account.team_role}:${account.local_unrestricted}`}>{children}</div>
  </AccountContext.Provider>
}
