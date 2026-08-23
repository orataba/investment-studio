import { describe, expect, it } from 'vitest'

import {
  detailWorkspaceFamily,
  fundDetailTabLabel,
  listedDetailTabs,
} from './instrumentDetailArchitecture'

describe('instrument detail architecture', () => {
  it('keeps ETF, equity, and index research surfaces distinct', () => {
    expect(listedDetailTabs('etf')).toContain('portfolio')
    expect(listedDetailTabs('etf')).toContain('research')
    expect(listedDetailTabs('etf')).not.toContain('fundamentals')
    expect(listedDetailTabs('equity')).toContain('fundamentals')
    expect(listedDetailTabs('equity')).toContain('events')
    expect(listedDetailTabs('equity')).toContain('research')
    expect(listedDetailTabs('index')).toContain('methodology')
    expect(listedDetailTabs('index')).toContain('research')
  })

  it('shares a fund workspace without erasing public and private semantics', () => {
    expect(detailWorkspaceFamily('public_fund')).toBe('fund')
    expect(detailWorkspaceFamily('private_fund')).toBe('fund')
    expect(fundDetailTabLabel('public_fund', 'price', 'en', 'Price')).toBe('Fees')
    expect(fundDetailTabLabel('private_fund', 'price', 'en', 'Price')).toBe('Terms')
  })
})
