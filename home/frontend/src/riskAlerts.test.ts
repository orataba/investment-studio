import { afterEach, expect, it, vi } from 'vitest'
import apps from '../../apps.json'
import { readRiskAlerts, riskDestination } from './riskAlerts'

afterEach(() => vi.unstubAllGlobals())
it('reads all selected important risks without creating work or hiding severe items', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ alerts: [], total: 0, has_more: false })))
  vi.stubGlobal('fetch', fetcher)
  expect(await readRiskAlerts('http://127.0.0.1:5173', 3)).toEqual({ alerts: [], total: 0, has_more: false })
  expect(fetcher).toHaveBeenCalledWith('http://127.0.0.1:5173/api/risk/alerts?all=true', expect.objectContaining({ credentials: 'include', cache: 'no-store', signal: expect.any(AbortSignal) }))
})
it('keeps an unavailable source distinct from no published important risks', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 503, headers: { 'X-Request-ID': 'trace-1' } })))
  await expect(readRiskAlerts('http://127.0.0.1:5173', 0)).rejects.toThrow('HTTP 503 · trace-1')
})
it('opens the owning instrument or portfolio and rejects external destinations', () => {
  vi.stubGlobal('window', { location: { hostname: '127.0.0.1', protocol: 'http:', port: '5172' } })
  expect(riskDestination(apps, { workspace: 'watchlist', path: '/instruments/test?tab=risk' })).toBe('http://127.0.0.1:5173/instruments/test?tab=risk')
  expect(riskDestination(apps, { workspace: 'portfolio', path: '/portfolios/3/risk' })).toBe('http://127.0.0.1:5174/portfolios/3/risk')
  for (const path of ['//other.example', '/\\other.example', 'https://other.example']) expect(() => riskDestination(apps, { workspace: 'watchlist', path })).toThrow('Invalid risk destination')
})
