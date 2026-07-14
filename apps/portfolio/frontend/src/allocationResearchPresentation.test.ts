import { describe, expect, it } from 'vitest'

import allocationLabPageSource from './pages/AllocationLabPage.tsx?raw'

describe('allocation research result presentation', () => {
  it('labels portfolio-level leaf risk contribution as look-through RC', () => {
    expect(allocationLabPageSource).toContain('Look-through RC')
    expect(allocationLabPageSource).toContain('hierarchical shrinkage can differ from local sleeve targets')
    expect(allocationLabPageSource).not.toContain('<th>Forward RC</th>')
  })

  it('does not present stale runs or zero-target current holdings as executable', () => {
    expect(allocationLabPageSource).toContain('Historical result — not current or execution-ready.')
    expect(allocationLabPageSource).toContain(
      'A 0% solved target for a currently held instrument is not an executable liquidation instruction.',
    )
    expect(allocationLabPageSource).toContain("row.execution_status === 'manual_review_required'")
  })
})
