import { describe, expect, it } from 'vitest'

import {
  beginRequest,
  invalidateRequests,
  isRequestCurrent,
} from '../../../packages/ui/src/requestIdentity'

describe('request identity', () => {
  it('only accepts the latest request for the active resource', () => {
    const sequence = { current: 0 }
    const first = beginRequest(sequence, 'instrument-a')
    const second = beginRequest(sequence, 'instrument-b')

    expect(isRequestCurrent(sequence, first, 'instrument-b')).toBe(false)
    expect(isRequestCurrent(sequence, second, 'instrument-b', 'instrument-b')).toBe(true)
    expect(isRequestCurrent(sequence, second, 'instrument-b', 'instrument-a')).toBe(false)
  })

  it('invalidates an in-flight request when a view closes', () => {
    const sequence = { current: 0 }
    const request = beginRequest(sequence, 'instrument-a')
    invalidateRequests(sequence)

    expect(isRequestCurrent(sequence, request, 'instrument-a')).toBe(false)
  })
})
