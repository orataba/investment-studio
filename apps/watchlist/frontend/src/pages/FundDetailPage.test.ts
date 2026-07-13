import { describe, expect, it } from 'vitest'

import fundDetailPageSource from './FundDetailPage.tsx?raw'

describe('FundDetailPage research rating workflow', () => {
  it('loads and renders the append-only rating history fields', () => {
    expect(
      fundDetailPageSource.match(/getInstrumentResearchRatings\(fundId\)/g),
    ).toHaveLength(2)
    expect(fundDetailPageSource).toContain(
      'researchRatingHistory: ResearchRatingResponse[]',
    )
    expect(fundDetailPageSource).toContain('Rating History')
    expect(fundDetailPageSource).toContain('<th>Revision</th>')
    expect(fundDetailPageSource).toContain('<th>Rating</th>')
    expect(fundDetailPageSource).toContain('<th>Effective / Review</th>')
    expect(fundDetailPageSource).toContain('<th>Analyst / Confidence</th>')
    expect(fundDetailPageSource).toContain('<th>Status</th>')
    expect(fundDetailPageSource).toContain('<th>Created</th>')
    expect(fundDetailPageSource).toContain('<th>Rationale</th>')
    expect(fundDetailPageSource).toContain('row.previous_rating_revision_id')
    expect(fundDetailPageSource).toContain('row.superseded_at')
  })

  it('refreshes both current rating and history after save and reset', () => {
    expect(fundDetailPageSource).toMatch(
      /const response = await updateInstrumentResearchRating[\s\S]*?const refreshed = await refreshResearchRatingBundle\(response\)/,
    )
    expect(fundDetailPageSource).toMatch(
      /async function handleResetResearchRating\(\)[\s\S]*?const refreshed = await refreshResearchRatingBundle\(\)/,
    )
    expect(fundDetailPageSource).toMatch(
      /Promise\.allSettled\(\[[\s\S]*?getInstrumentResearchRating\(fundId\),[\s\S]*?getInstrumentResearchRatings\(fundId\)/,
    )
    expect(fundDetailPageSource).toContain(
      'The revision was saved, but ${refreshed.failedSections.join',
    )
  })

  it('refreshes on a CAS conflict without overwriting the user draft', () => {
    const conflictStart = fundDetailPageSource.indexOf(
      'if (saveError instanceof ApiRequestError && saveError.status === 409)',
    )
    const conflictEnd = fundDetailPageSource.indexOf('} else {', conflictStart)
    const conflictBlock = fundDetailPageSource.slice(conflictStart, conflictEnd)

    expect(conflictStart).toBeGreaterThan(-1)
    expect(conflictEnd).toBeGreaterThan(conflictStart)
    expect(conflictBlock).toContain('await refreshResearchRatingBundle()')
    expect(conflictBlock).toContain('expectedRevisionId: refreshed.currentRating?.rating_revision_id ?? null')
    expect(conflictBlock).toContain('Your draft inputs were preserved')
    expect(conflictBlock).not.toContain('toResearchRatingDraft')
    expect(conflictBlock).not.toContain('setResearchRatingDirty(false)')
  })
})
