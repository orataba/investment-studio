// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getWatchlists, type WatchlistRecord } from './api'
import { useWatchlistForegroundRefresh } from './useWatchlistForegroundRefresh'

vi.mock('./api', () => ({ getWatchlists: vi.fn() }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.mocked(getWatchlists).mockReset()
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('synchronizes only when visible and deduplicates focus and visibility events while in flight', async () => {
  const request = deferred<WatchlistRecord[]>()
  vi.mocked(getWatchlists).mockReturnValue(request.promise)
  const synchronized = vi.fn()
  renderHook(() => useWatchlistForegroundRefresh('all-instruments', synchronized, vi.fn()))
  expect(getWatchlists).not.toHaveBeenCalled()
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  act(() => { document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('focus')) })
  expect(getWatchlists).not.toHaveBeenCalled()
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  act(() => { document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('focus')) })
  expect(getWatchlists).toHaveBeenCalledTimes(1)
  expect(synchronized).not.toHaveBeenCalled()
  await act(async () => { request.resolve([]) })
  expect(synchronized).toHaveBeenCalledExactlyOnceWith([])
})

it('discards responses after switching lists or unmounting', async () => {
  const old = deferred<WatchlistRecord[]>()
  const current = deferred<WatchlistRecord[]>()
  const afterUnmount = deferred<WatchlistRecord[]>()
  vi.mocked(getWatchlists).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise).mockReturnValueOnce(afterUnmount.promise)
  const synchronized = vi.fn()
  const { rerender, unmount } = renderHook(({ scope }) => useWatchlistForegroundRefresh(scope, synchronized, vi.fn()), {
    initialProps: { scope: 'all-instruments' },
  })
  act(() => { window.dispatchEvent(new Event('focus')) })
  rerender({ scope: 'custom-list' })
  act(() => { window.dispatchEvent(new Event('focus')) })
  await act(async () => { old.resolve([]) })
  expect(synchronized).not.toHaveBeenCalled()
  await act(async () => { current.resolve([]) })
  expect(synchronized).toHaveBeenCalledTimes(1)
  act(() => { window.dispatchEvent(new Event('focus')) })
  unmount()
  await act(async () => { afterUnmount.resolve([]) })
  expect(synchronized).toHaveBeenCalledTimes(1)
})

it('reports sync failures without refreshing rows and can refresh on the next foreground event', async () => {
  vi.mocked(getWatchlists).mockRejectedValueOnce(new Error('Directory unavailable')).mockResolvedValueOnce([])
  const synchronized = vi.fn()
  const failed = vi.fn()
  renderHook(() => useWatchlistForegroundRefresh('all-instruments', synchronized, failed))
  await act(async () => { window.dispatchEvent(new Event('focus')) })
  expect(failed).toHaveBeenCalledExactlyOnceWith('Directory unavailable')
  expect(synchronized).not.toHaveBeenCalled()
  await act(async () => { window.dispatchEvent(new Event('focus')) })
  expect(synchronized).toHaveBeenCalledTimes(1)
})
