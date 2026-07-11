import { describe, expect, it } from 'vitest'

import {
  beginDetailRequest,
  completeDetailRequest,
  createDetailRequestCoordinator,
  isDetailRequestLoaded,
} from './detailRequestCoordinator'

describe('detail request coordination', () => {
  it('marks a section loaded only after a successful response', () => {
    const coordinator = createDetailRequestCoordinator('instrument-a:1')
    const request = beginDetailRequest(coordinator, 'risk')

    expect(request).not.toBeNull()
    expect(isDetailRequestLoaded(coordinator, 'risk')).toBe(false)
    expect(completeDetailRequest(coordinator, request!, true)).toBe(true)
    expect(isDetailRequestLoaded(coordinator, 'risk')).toBe(true)
    expect(beginDetailRequest(coordinator, 'risk')).toBeNull()
  })

  it('releases a failed section for a later retry without duplicating in-flight work', () => {
    const coordinator = createDetailRequestCoordinator('instrument-a:1')
    const first = beginDetailRequest(coordinator, 'performance')

    expect(beginDetailRequest(coordinator, 'performance')).toBeNull()
    expect(completeDetailRequest(coordinator, first!, false)).toBe(true)
    expect(isDetailRequestLoaded(coordinator, 'performance')).toBe(false)

    const retry = beginDetailRequest(coordinator, 'performance')
    expect(retry?.requestId).toBeGreaterThan(first!.requestId)
  })

  it('rejects stale completions instead of letting them overwrite a retry', () => {
    const coordinator = createDetailRequestCoordinator('instrument-a:1')
    const first = beginDetailRequest(coordinator, 'exposure')!
    completeDetailRequest(coordinator, first, false)
    const retry = beginDetailRequest(coordinator, 'exposure')!

    expect(completeDetailRequest(coordinator, first, true)).toBe(false)
    expect(isDetailRequestLoaded(coordinator, 'exposure')).toBe(false)
    expect(completeDetailRequest(coordinator, retry, true)).toBe(true)
    expect(isDetailRequestLoaded(coordinator, 'exposure')).toBe(true)
  })

  it('rejects responses from an older instrument generation', () => {
    const previous = createDetailRequestCoordinator('instrument-a:1')
    const request = beginDetailRequest(previous, 'people')!
    const current = createDetailRequestCoordinator('instrument-b:1')

    expect(completeDetailRequest(current, request, true)).toBe(false)
    expect(isDetailRequestLoaded(current, 'people')).toBe(false)
  })
})
