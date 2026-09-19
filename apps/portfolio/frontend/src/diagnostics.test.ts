import { afterEach, expect, it, vi } from 'vitest'
import { installBrowserDiagnostics } from '../../../../packages/ui/src/diagnostics'

const originalFetch = window.fetch

afterEach(() => {
  window.fetch = originalFetch
  vi.restoreAllMocks()
  vi.useRealTimers()
})

it('keeps request behavior while uploading only safe timing fields', async () => {
  vi.useFakeTimers()
  const transport = vi.fn().mockResolvedValue(new Response('{}', { status: 200, headers: { 'X-Request-ID': 'request-123' } }))
  window.fetch = transport
  Object.defineProperty(performance, 'getEntriesByType', { value: () => [], configurable: true })
  installBrowserDiagnostics('home', 'https://home.example')
  await window.fetch('/api/resource?secret=private-query', { method: 'POST', body: 'private-body' })
  await vi.advanceTimersByTimeAsync(10_000)
  expect(transport.mock.calls[0]).toEqual(['/api/resource?secret=private-query', { method: 'POST', body: 'private-body' }])
  const upload = transport.mock.calls.find(([url]) => url === 'https://home.example/api/diagnostics/events')
  expect(upload).toBeDefined()
  const body = JSON.parse(upload![1].body)
  expect(body.events[0]).toMatchObject({ path: '/api/resource', status: 200, request_id: 'request-123' })
  expect(upload![1].body).not.toContain('private-query')
  expect(upload![1].body).not.toContain('private-body')
})
