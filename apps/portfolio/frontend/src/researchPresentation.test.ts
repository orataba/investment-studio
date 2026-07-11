import { describe, expect, it } from 'vitest'

import researchPageSource from './pages/ResearchPage.tsx?raw'

describe('research result presentation', () => {
  it('labels portfolio-level leaf risk contribution as look-through RC', () => {
    expect(researchPageSource).toContain('Look-through RC')
    expect(researchPageSource).toContain('hierarchical shrinkage can differ from local sleeve targets')
    expect(researchPageSource).not.toContain('<th>Forward RC</th>')
  })

  it('does not present stale runs or zero-target current holdings as executable', () => {
    expect(researchPageSource).toContain('Historical result — not current or execution-ready.')
    expect(researchPageSource).toContain(
      'A 0% solved target for a currently held instrument is not an executable liquidation instruction.',
    )
    expect(researchPageSource).toContain("row.execution_status === 'manual_review_required'")
  })
})
