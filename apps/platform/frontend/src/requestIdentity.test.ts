import { describe, expect, it } from 'vitest'

import {
  beginRequest,
  invalidateRequests,
  isRequestCurrent,
} from '../../../../packages/ui/src/requestIdentity'
import { detailForSelection, instrumentsForVisibility } from './instrumentVisibility'

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

describe('instrument visibility refreshes', () => {
  const instruments = [
    { instrument_id: 'active', lifecycle_state: { status: 'active' } },
    { instrument_id: 'archived', lifecycle_state: { status: 'archived' } },
  ]

  it('derives the visible rows from the latest inactive-toggle state', () => {
    expect(instrumentsForVisibility(instruments, false).map((item) => item.instrument_id)).toEqual(['active'])
    expect(instrumentsForVisibility(instruments, true).map((item) => item.instrument_id)).toEqual([
      'active',
      'archived',
    ])
  })

  it('never exposes one instrument detail under another selected instrument', () => {
    const detail = { instrument_id: 'instrument-a', market_data: ['a-nav'] }

    expect(detailForSelection(detail, 'instrument-a')).toBe(detail)
    expect(detailForSelection(detail, 'instrument-b')).toBeNull()
  })
})
