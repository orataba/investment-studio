import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import usePerformanceResource from './hooks/usePerformanceResource'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe('usePerformanceResource extraction contract', () => {
  it('keeps prior data while refreshing and ignores an older late response', async () => {
    const first = deferred<string>()
    const second = deferred<string>()
    const third = deferred<string>()
    const firstLoad = () => first.promise
    const secondLoad = () => second.promise
    const thirdLoad = () => third.promise
    const { result, rerender } = renderHook(
      ({ load }) =>
        usePerformanceResource({
          enabled: true,
          resourceKey: 'portfolio-1',
          load,
          fallbackError: 'Failed.',
        }),
      { initialProps: { load: firstLoad } },
    )

    expect(result.current.loading).toBe(true)
    act(() => first.resolve('first'))
    await waitFor(() => expect(result.current.data).toBe('first'))

    rerender({ load: secondLoad })
    expect(result.current.data).toBe('first')
    expect(result.current.loading).toBe(true)
    rerender({ load: thirdLoad })

    act(() => second.resolve('stale second'))
    await act(async () => {
      await second.promise
    })
    expect(result.current.data).toBe('first')

    act(() => third.resolve('third'))
    await waitFor(() => expect(result.current.data).toBe('third'))
    expect(result.current.loading).toBe(false)
  })

  it('fails closed on rejection and clears all state when disabled', async () => {
    const request = deferred<string>()
    const load = () => request.promise
    const { result, rerender } = renderHook(
      ({ enabled }) =>
        usePerformanceResource({
          enabled,
          resourceKey: 'portfolio-1',
          load,
          fallbackError: 'Fallback failure.',
        }),
      { initialProps: { enabled: true } },
    )

    act(() => request.reject(new Error('Request failed.')))
    await waitFor(() => expect(result.current.error).toBe('Request failed.'))
    expect(result.current.data).toBeNull()
    expect(result.current.loading).toBe(false)

    rerender({ enabled: false })
    expect(result.current).toEqual({ data: null, loading: false, error: null })
  })

  it('clears prior data when the portfolio resource identity changes', async () => {
    const first = deferred<string>()
    const second = deferred<string>()
    const firstLoad = () => first.promise
    const secondLoad = () => second.promise
    const { result, rerender } = renderHook(
      ({ resourceKey, load }) =>
        usePerformanceResource({
          enabled: true,
          resourceKey,
          load,
          fallbackError: 'Failed.',
        }),
      { initialProps: { resourceKey: 'portfolio-1', load: firstLoad } },
    )

    act(() => first.resolve('portfolio one'))
    await waitFor(() => expect(result.current.data).toBe('portfolio one'))

    rerender({ resourceKey: 'portfolio-2', load: secondLoad })
    expect(result.current.data).toBeNull()
    expect(result.current.loading).toBe(true)

    act(() => second.resolve('portfolio two'))
    await waitFor(() => expect(result.current.data).toBe('portfolio two'))
  })

  it('cancels replaced, disabled and unmounted reads without accepting their late results', async () => {
    const requests: { signal: AbortSignal; request: ReturnType<typeof deferred<string>> }[] = []
    const load = (signal: AbortSignal) => {
      const request = deferred<string>()
      requests.push({ signal, request })
      return request.promise
    }
    const { result, rerender, unmount } = renderHook(
      ({ resourceKey, enabled }) => usePerformanceResource({ enabled, resourceKey, load, fallbackError: 'Failed.' }),
      { initialProps: { resourceKey: 'first-window', enabled: true } },
    )
    rerender({ resourceKey: 'second-window', enabled: true })
    expect(requests[0].signal.aborted).toBe(true)
    expect(requests[1].signal.aborted).toBe(false)
    await act(async () => { requests[0].request.resolve('obsolete window') })
    expect(result.current.data).toBeNull()
    expect(result.current.loading).toBe(true)
    rerender({ resourceKey: 'second-window', enabled: false })
    expect(requests[1].signal.aborted).toBe(true)
    await act(async () => { requests[1].request.reject(new DOMException('Aborted', 'AbortError')) })
    expect(result.current.error).toBeNull()
    rerender({ resourceKey: 'third-window', enabled: true })
    unmount()
    expect(requests[2].signal.aborted).toBe(true)
  })

})
