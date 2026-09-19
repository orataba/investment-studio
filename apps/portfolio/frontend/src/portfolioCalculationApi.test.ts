import { getPortfolioBootstrap } from './lib/bootstrap'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearPortfolioApiCache, getPortfolioAccessRecovery, getPortfolioPerformance, updatePortfolioSettings } from './lib/api'

function pendingResponse(retryAfter = '1') {
  return new Response(JSON.stringify({
    detail: {
      code: 'portfolio_calculation_pending', portfolio_id: '3', status: 'running',
      message: 'Portfolio calculations are updating. Please retry shortly.',
    },
  }), { status: 503, headers: { 'Retry-After': retryAfter } })
}

describe('background portfolio calculation requests', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    clearPortfolioApiCache()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('waits for Retry-After and returns only the completed report', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(pendingResponse('2'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ portfolio_id: '3' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const request = getPortfolioPerformance('3')
    await vi.advanceTimersByTimeAsync(1_999)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    await expect(request).resolves.toEqual({ portfolio_id: '3' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('shares the pending request beyond the result TTL and starts that TTL on completion', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(pendingResponse('70'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ portfolio_id: '3' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const first = getPortfolioPerformance('3')
    await vi.advanceTimersByTimeAsync(61_000)
    expect(getPortfolioPerformance('3')).toBe(first)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(9_000)
    await expect(first).resolves.toEqual({ portfolio_id: '3' })
    await vi.advanceTimersByTimeAsync(59_000)
    expect(getPortfolioPerformance('3')).toBe(first)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('rechecks authorization without clearing cached reports while writes still invalidate them', async () => {
    const fetchMock = vi.fn(async (_url: string) => new Response(JSON.stringify({
      portfolio_id: '3', user_id: 'alice', session_id: 'session-a',
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const report = getPortfolioPerformance('3')
    await report
    for (let check = 0; check < 2; check += 1) {
      await Promise.all([getPortfolioBootstrap('3'), getPortfolioAccessRecovery()])
      expect(getPortfolioPerformance('3')).toBe(report)
    }
    for (const path of ['/session?portfolio_id=3', '/access-recovery']) {
      expect(fetchMock.mock.calls.filter(([url]) => url.endsWith(path))).toHaveLength(2)
    }
    expect(fetchMock).toHaveBeenCalledTimes(5)

    await updatePortfolioSettings('3', { base_currency: 'CNY' })
    const refreshed = getPortfolioPerformance('3')
    expect(refreshed).not.toBe(report)
    await refreshed
    expect(fetchMock).toHaveBeenCalledTimes(7)
  })

  it('surfaces a stored calculation failure without retrying it', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { code: 'portfolio_calculation_failed', status: 'failed', message: 'Missing FX boundary.' },
    }), { status: 503, headers: { 'Retry-After': '1' } }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(getPortfolioPerformance('3')).rejects.toThrow('Missing FX boundary.')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not retry an ordinary 503 response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('Service unavailable', {
      status: 503, headers: { 'Retry-After': '1' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(getPortfolioPerformance('3')).rejects.toThrow('Service unavailable')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('never repeats a mutation even if its response contains the pending code', async () => {
    const fetchMock = vi.fn().mockResolvedValue(pendingResponse())
    vi.stubGlobal('fetch', fetchMock)

    await expect(updatePortfolioSettings('3', { base_currency: 'CNY' })).rejects.toThrow('calculations are updating')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('ends pending retries after 120 seconds and permits a later refresh', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(pendingResponse()))
    vi.stubGlobal('fetch', fetchMock)
    const request = getPortfolioPerformance('3')
    const rejection = expect(request).rejects.toThrow('已停止等待后台计算，请稍后刷新查看结果。')

    await vi.advanceTimersByTimeAsync(120_000)
    await rejection
    expect(fetchMock).toHaveBeenCalledTimes(120)
    expect(vi.getTimerCount()).toBe(0)
    fetchMock.mockImplementationOnce(() => Promise.resolve(new Response('{}', { status: 200 })))
    await expect(getPortfolioPerformance('3')).resolves.toEqual({})
    expect(fetchMock).toHaveBeenCalledTimes(121)
  })

  it('aborts an in-flight retry when the total wait expires', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(pendingResponse())
      .mockImplementationOnce((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true })
      }))
    vi.stubGlobal('fetch', fetchMock)
    const request = getPortfolioPerformance('3')
    const rejection = expect(request).rejects.toThrow('已停止等待后台计算，请稍后刷新查看结果。')

    await vi.advanceTimersByTimeAsync(120_000)
    await rejection
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
  })
})
