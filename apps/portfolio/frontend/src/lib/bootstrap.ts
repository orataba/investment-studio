import type { PortfolioAccess, PortfolioCapabilities, PortfolioSession } from './api'

export type PortfolioBootstrap = PortfolioSession & {
  capabilities: PortfolioCapabilities
  access: (PortfolioAccess & { user_id: string }) | null
}

const apiBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

/** One live identity read; never reuse the financial response cache for authority. */
export async function getPortfolioBootstrap(portfolioId: string | null, signal?: AbortSignal): Promise<PortfolioBootstrap> {
  const query = portfolioId ? `?portfolio_id=${encodeURIComponent(portfolioId)}` : ''
  const response = await fetch(`${apiBase}/api/portfolios/session${query}`, {
    credentials: 'include', cache: 'no-store', signal,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const message = typeof body.detail === 'string' ? body.detail : '暂时无法确认账号与组合权限'
    const requestId = response.headers?.get('X-Request-ID')
    throw Object.assign(new Error(requestId ? `${message} [request_id=${requestId}]` : message), { status: response.status })
  }
  return response.json()
}
