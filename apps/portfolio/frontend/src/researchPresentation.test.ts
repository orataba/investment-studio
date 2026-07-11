import { describe, expect, it } from 'vitest'

import researchPageSource from './pages/ResearchPage.tsx?raw'

describe('research result presentation', () => {
  it('labels portfolio-level leaf risk contribution as look-through RC', () => {
    expect(researchPageSource).toContain('Look-through RC')
    expect(researchPageSource).toContain('hierarchical shrinkage can differ from local sleeve targets')
    expect(researchPageSource).not.toContain('<th>Forward RC</th>')
  })
})
