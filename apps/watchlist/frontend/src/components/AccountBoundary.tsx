import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { resolveWorkspaceUrl } from '../../../../../packages/ui/src/navigation'
import RequestRecovery from '../../../../../packages/ui/src/RequestRecovery'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { withLanguage } from '../../../../../packages/ui/src/navigation'
import { API_BASE_URL } from '../lib/api'
import NoticeToast, { LoadingNotice } from '../../../../../packages/ui/src/NoticeToast'

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
  const { t, language } = useLanguage()
  const [checking, setChecking] = useState(false)
  const [requestId, setRequestId] = useState<string | null>(null)
  const [account, setAccount] = useState<StudioAccount | null>(null)
  const [error, setError] = useState('')
  const [needsLogin, setNeedsLogin] = useState(false)
  const [retryToken, setRetryToken] = useState(0)
  useEffect(() => {
    let cancelled = false
    let latestRequest = 0
    async function refresh() {
      const request = ++latestRequest
      setNeedsLogin(false)
      setChecking(true)
      try {
        const response = await fetch(`${API_BASE_URL}/api/identity`, { credentials: 'include', cache: 'no-store' })
        if (cancelled || request !== latestRequest) return
        setRequestId(response.headers?.get('X-Request-ID') || null)
        if (!response.ok) {
          if (cancelled || request !== latestRequest) return
          const unauthorized = response.status === 401 || response.status === 403
          setNeedsLogin(unauthorized)
          if (unauthorized) { activeAccountId = ''; setAccount(null) }
          throw new Error(response.status === 401 ? '请登录 Investment Studio' : '暂时无法确认账号权限')
        }
        const next = await response.json() as StudioAccount
        if (cancelled || request !== latestRequest) return
        activeAccountId = next.user_id
        setAccount(next); setError('')
      } catch (reason) {
        if (!cancelled && request === latestRequest) setError(reason instanceof Error ? reason.message : '账号校验失败')
      } finally { if (!cancelled && request === latestRequest) setChecking(false) }
    }
    void refresh()
    window.addEventListener('focus', refresh)
    window.addEventListener('studio:unauthorized', refresh)
    return () => { cancelled = true; window.removeEventListener('focus', refresh); window.removeEventListener('studio:unauthorized', refresh) }
  }, [retryToken])
  const home = resolveWorkspaceUrl(undefined, 'home')
  const retry = () => setRetryToken(value => value + 1)
  if (!account) return <section className="studio-account-message">
    <LoadingNotice active={checking || !error} message={t('正在确认账号…')} />
    {error && <RequestRecovery error={error} onRetry={retry} busy={checking} requestId={requestId} />}
    {needsLogin && <a href={withLanguage(`${home}/login?next=${encodeURIComponent(window.location.href)}`, language)}>{t('前往登录')}</a>}
  </section>
  return <AccountContext.Provider value={account}>
    <NoticeToast notice={error ? { id: retryToken, tone: 'error', message: <RequestRecovery embedded error={error} onRetry={retry} busy={checking} requestId={requestId} /> } : null} durationMs={0} onDismiss={() => setError('')} />
    <div className="studio-account-bar"><span><span translate="no">{account.display_name}</span> · {t(account.local_unrestricted ? '本机全权限' : account.team_role === 'reader' ? '团队只读' : '团队协作')}</span>{!account.local_unrestricted && <a href={withLanguage(`${home}/account`, language)}>{t('账号设置')}</a>}</div>
    <div key={`${account.user_id}:${account.team_role}:${account.local_unrestricted}`}>{children}</div>
  </AccountContext.Provider>
}
