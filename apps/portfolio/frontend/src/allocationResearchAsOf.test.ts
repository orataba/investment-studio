import { describe, expect, it } from 'vitest'

import { resolveAllocationResearchAsOfDraft, serializeAllocationResearchAsOf } from './lib/allocationResearchAsOf'

describe('allocation research as-of settings', () => {
  it('shows the latest portfolio date and does not submit a date in dynamic mode', () => {
    const draft = resolveAllocationResearchAsOfDraft(
      {
        as_of_mode: 'dynamic',
        as_of_date: '2026-07-09',
        pinned_as_of_date: null,
      },
      '2026-07-10',
    )

    expect(draft).toEqual({ asOfMode: 'dynamic', asOfDate: '2026-07-10' })
    expect(serializeAllocationResearchAsOf(draft)).toEqual({
      as_of_mode: 'dynamic',
      as_of_date: null,
    })
  })

  it('restores and submits only the explicit pinned date in pinned mode', () => {
    const draft = resolveAllocationResearchAsOfDraft(
      {
        as_of_mode: 'pinned',
        as_of_date: '2026-05-27',
        pinned_as_of_date: '2026-05-27',
      },
      '2026-07-10',
    )

    expect(draft).toEqual({ asOfMode: 'pinned', asOfDate: '2026-05-27' })
    expect(serializeAllocationResearchAsOf(draft)).toEqual({
      as_of_mode: 'pinned',
      as_of_date: '2026-05-27',
    })
  })
})
