type Workspace = 'home' | 'watchlist' | 'portfolio' | 'regime'

const ports: Record<Workspace, string> = { home: '5172', watchlist: '5173', portfolio: '5174', regime: '3011' }

export function resolveWorkspaceUrl(configured: string | undefined, workspace: Workspace) {
  const value = configured?.trim().replace(/\/$/, '')
  const location = typeof window === 'undefined' ? null : window.location
  const hostname = location?.hostname || '127.0.0.1'
  const localHost = /^(localhost|127\.0\.0\.1|\[?::1\]?)$/.test(hostname)
  if (value) {
    if (value.startsWith('/')) return value
    const url = new URL(value)
    if (!/^(localhost|127\.0\.0\.1|\[?::1\]?)$/.test(url.hostname)) return value
    // Local and LAN access use the browser's host, retaining the configured port.
    if (localHost || location?.port) {
      url.hostname = hostname
      return url.href.replace(/\/$/, '')
    }
  }
  if (!localHost && !location?.port) {
    const rootHost = hostname.replace(/^(watchlist|portfolio|regime)\./, '')
    return `${location?.protocol || 'https:'}//${workspace === 'home' ? '' : `${workspace}.`}${rootHost}`
  }
  const host = hostname.includes(':') && !hostname.startsWith('[') ? `[${hostname}]` : hostname
  return `${location?.protocol || 'http:'}//${host}:${ports[workspace]}`
}

export function withLanguage(href: string, language: string) {
  const url = new URL(href, window.location.href)
  url.searchParams.set('lang', language)
  return href.startsWith('/') ? `${url.pathname}${url.search}${url.hash}` : url.href
}
