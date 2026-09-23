import { describe, expect, it } from 'vitest'

import {
  detailWorkspaceFamily,
  fundDetailTabLabel,
  listedDetailTabs,
} from './instrumentDetailArchitecture'

describe('instrument detail architecture', () => {
  it('gives every listed type prepared research tracking alongside personal views and quantitative risk', () => {
    for (const instrumentType of ['etf', 'equity', 'index'] as const) {
      expect(listedDetailTabs(instrumentType)).toEqual(['overview', 'investment-research', 'views', 'performance'])
    }
  })

  it('shares a fund workspace without erasing public and private semantics', () => {
    expect(detailWorkspaceFamily('public_fund')).toBe('fund')
    expect(detailWorkspaceFamily('private_fund')).toBe('fund')
    expect(fundDetailTabLabel('public_fund', 'price', 'en', 'Price')).toBe('Fees')
    expect(fundDetailTabLabel('private_fund', 'price', 'en', 'Price')).toBe('Terms')
  })
})
