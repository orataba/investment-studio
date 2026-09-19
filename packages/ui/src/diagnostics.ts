import { resolveWorkspaceUrl } from './navigation'

/** Browser diagnostics contain timings and code locations, never user inputs. */
type BrowserEvent = {
  kind: 'navigation' | 'request' | 'error'
  path: string
  duration_ms?: number
  ttfb_ms?: number
  transfer_bytes?: number
  status?: number
  request_id?: string
  error_type?: string
  line?: number
}

export function installBrowserDiagnostics(app: 'home' | 'portfolio' | 'watchlist' | 'briefing', homeUrl: string | undefined) {
  const rawFetch = window.fetch.bind(window)
  const endpoint = new URL('/api/diagnostics/events', new URL(resolveWorkspaceUrl(homeUrl, 'home'), window.location.href)).href
  const queue: BrowserEvent[] = []
  let sending = false
  const pathname = (value: string) => {
    try { return new URL(value, window.location.href).pathname.slice(0, 250) } catch { return '/' }
  }
  const errorType = (reason: unknown, fallback: string) => {
    const name = reason instanceof Error ? reason.name : fallback
    return /^[A-Za-z0-9_.]{1,80}$/.test(name) ? name : fallback
  }
  const record = (event: BrowserEvent) => { if (queue.length < 40) queue.push(event) }
  const flush = async () => {
    if (sending || !queue.length) return
    sending = true
    const events = queue.splice(0, 20)
    try {
      // A diagnostic failure never retries, interrupts navigation, or changes auth.
      await rawFetch(endpoint, { method: 'POST', credentials: 'include', keepalive: true,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ app, events }) })
    } catch { /* Offline and signed-out pages have no server diagnostics. */ }
    finally { sending = false }
  }
  window.fetch = async (...args: Parameters<typeof fetch>) => {
    const started = performance.now()
    const input = args[0]
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    try {
      const response = await rawFetch(...args)
      record({ kind: 'request', path: pathname(url), status: response.status,
        request_id: response.headers.get('X-Request-ID') || undefined,
        duration_ms: Math.round(performance.now() - started) })
      return response
    } catch (reason) {
      record({ kind: 'request', path: pathname(url), status: 0,
        error_type: errorType(reason, 'NetworkError'),
        duration_ms: Math.round(performance.now() - started) })
      throw reason
    }
  }
  window.addEventListener('error', (event) => {
    record({ kind: 'error', path: pathname(event.filename || window.location.href),
      error_type: errorType(event.error, 'ResourceError'), line: event.lineno })
    void flush()
  })
  window.addEventListener('unhandledrejection', (event) => {
    record({ kind: 'error', path: pathname(window.location.href),
      error_type: errorType(event.reason, 'UnhandledRejection') })
    void flush()
  })
  const navigation = () => {
    const timing = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
    if (timing) record({ kind: 'navigation', path: pathname(window.location.href),
      duration_ms: Math.round(timing.domContentLoadedEventEnd),
      ttfb_ms: Math.round(timing.responseStart - timing.requestStart), transfer_bytes: timing.transferSize })
    void flush()
  }
  if (document.readyState === 'complete') navigation()
  else window.addEventListener('load', navigation, { once: true })
  window.setInterval(() => { void flush() }, 10_000)
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') void flush() })
}
