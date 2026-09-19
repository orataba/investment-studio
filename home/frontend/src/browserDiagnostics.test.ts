import { afterEach, expect, it, vi } from 'vitest'
import { installBrowserDiagnostics } from '../../../packages/ui/src/diagnostics'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('uploads bounded locations and timings without query strings, inputs, or exception messages', async () => {
  vi.useFakeTimers()
  const listeners = new Map<string, (event: unknown) => void>()
  const rawFetch = vi.fn().mockResolvedValue(new Response('{}', { status: 200, headers: { 'X-Request-ID': 'test-request-123' } }))
  const win = {
    fetch: rawFetch,
    location: { href: 'http://127.0.0.1:5172/account?token=private-token', hostname: '127.0.0.1', protocol: 'http:', port: '5172', pathname: '/account' },
    addEventListener: (name: string, listener: (event: unknown) => void) => listeners.set(name, listener),
    setInterval,
  }
  vi.stubGlobal('window', win)
  vi.stubGlobal('document', { readyState: 'loading', addEventListener: vi.fn() })
  installBrowserDiagnostics('home', undefined)
  await win.fetch('/api/auth/login?search=private-search', { method: 'POST', body: 'private-password' })
  const error = new Error('private-exception-message')
  error.name = 'Private error with input'
  listeners.get('unhandledrejection')!({ reason: error })
  await vi.waitFor(() => expect(rawFetch).toHaveBeenCalledTimes(2))
  const [url, options] = rawFetch.mock.calls[1]
  expect(url).toBe('http://127.0.0.1:5172/api/diagnostics/events')
  const payload = JSON.parse(options.body)
  expect(payload.events[0]).toMatchObject({ path: '/api/auth/login', request_id: 'test-request-123', status: 200 })
  expect(payload.events[1]).toMatchObject({ path: '/account', error_type: 'UnhandledRejection' })
  expect(options.body).not.toMatch(/private-token|private-search|private-password|private-exception-message|Private error/)
  await vi.advanceTimersByTimeAsync(10_000)
  expect(rawFetch).toHaveBeenCalledTimes(2)
})
