import { describe, expect, it } from 'vitest'

import {
  detailWorkspaceFamily,
  fundDetailTabLabel,
  fundDetailTabs,
  listedDetailTabs,
  resolveFundDetailLocation,
  resolveListedDetailTab,
} from './instrumentDetailArchitecture'

describe('instrument detail architecture', () => {
  it('gives every listed type prepared research tracking alongside personal views and quantitative risk', () => {
    for (const instrumentType of ['etf', 'equity', 'index', 'crypto'] as const) {
      expect(listedDetailTabs(instrumentType)).toEqual(['overview', 'risk', 'investment-research', 'views', 'info'])
    }
    expect(fundDetailTabs()).toEqual(listedDetailTabs('etf'))
  })

  it('shares a fund workspace without erasing public and private semantics', () => {
    expect(detailWorkspaceFamily('public_fund')).toBe('fund')
    expect(detailWorkspaceFamily('private_fund')).toBe('fund')
    expect(fundDetailTabLabel('public_fund', 'price', 'en', 'Price')).toBe('Fees')
    expect(fundDetailTabLabel('private_fund', 'price', 'en', 'Price')).toBe('Terms')
  })

  it('keeps historical links and fund sections while giving risk its own page', () => {
    for (const type of ['public_fund', 'private_fund'] as const) {
      expect(resolveFundDetailLocation('performance', 'people', type)).toEqual({ tab: 'overview', section: 'people' })
      expect(resolveFundDetailLocation('archive', 'documents', type)).toEqual({ tab: 'info', section: 'documents' })
      expect(resolveFundDetailLocation('risk', 'price', type)).toEqual({ tab: 'risk', section: 'price' })
      for (const section of ['exposure', 'people', 'strategy', 'price', 'documents', 'monitoring'] as const) {
        expect(resolveFundDetailLocation(section, null, type)).toEqual({ tab: 'info', section })
      }
      expect(resolveFundDetailLocation('portfolio', null, type)).toEqual({ tab: 'info', section: 'exposure' })
    }
    expect(resolveListedDetailTab('performance')).toBe('overview')
    expect(resolveListedDetailTab('archive')).toBe('info')
    expect(resolveListedDetailTab('risk')).toBe('risk')
    expect(resolveListedDetailTab('research')).toBe('views')
    expect(resolveListedDetailTab('events')).toBe('investment-research')
    expect(resolveListedDetailTab(null)).toBe('overview')
    expect(resolveListedDetailTab('unknown')).toBe('overview')
  })
})
