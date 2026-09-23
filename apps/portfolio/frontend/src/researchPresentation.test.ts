import { describe, expect, it } from 'vitest'

import researchPageSource from './pages/ResearchPage.tsx?raw'

describe('research result presentation', () => {
  it('labels portfolio-level leaf risk contribution as look-through RC', () => {
    expect(researchPageSource).toContain('Look-through RC')
    expect(researchPageSource).toContain('Risk contributions use the same global covariance and solved asset weights as the optimizer.')
    expect(researchPageSource).not.toContain('<th>Forward RC</th>')
  })

  it('keeps stale runs distinct and governance controls off the research page', () => {
    expect(researchPageSource).toContain('Historical result — not current or execution-ready.')
    expect(researchPageSource).not.toContain('Manual PM decision required.')
    expect(researchPageSource).not.toContain('Research Eligibility')
    expect(researchPageSource).not.toContain('PM review required')
    expect(researchPageSource).toContain("row.execution_status === 'manual_review_required'")
    expect(researchPageSource).toContain('Actual vs Backtest')
    expect(researchPageSource).toContain('Model & Backtest Evidence')
    expect(researchPageSource).toContain('research-evidence-disclosure')
  })
})
