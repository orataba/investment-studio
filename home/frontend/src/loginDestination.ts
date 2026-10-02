/** Standard Nginx cannot escape an arbitrary URI as a query value. Its dedicated
 * fragment handoff carries the complete URI; normalize once before language/bootstrap. */
export function normalizeProxyLoginDestination() {
  const { pathname, hash, search } = window.location
  if (!pathname.endsWith('/login') || !hash.startsWith('#next=')) return
  const query = new URLSearchParams(search)
  query.set('next', hash.slice('#next='.length))
  window.history.replaceState(window.history.state, '', `${pathname}?${query}`)
}

export function destinationAfterLogin() {
  const requested = new URLSearchParams(window.location.search).get('next')
  if (!requested) return '/'
  try {
    const candidate = new URL(requested, window.location.origin)
    const rootHost = window.location.hostname
    const trustedHost = candidate.hostname === rootHost
      || candidate.hostname.endsWith(`.${rootHost}`)
    if (candidate.protocol === window.location.protocol && trustedHost) return candidate.href
  } catch {
    return '/'
  }
  return '/'
}
