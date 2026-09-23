import { expect, it } from 'vitest'
import { fundDetailTabs, fundDetailTabLabel, resolveFundDetailLocation } from './instrumentDetailArchitecture'

it('keeps five fund destinations and preserves each fund family’s archive vocabulary', () => {
  expect(fundDetailTabs()).toEqual(['overview', 'investment-research', 'views', 'performance', 'archive'])
  for (const type of ['public_fund', 'private_fund'] as const) {
    expect(fundDetailTabLabel(type, 'performance', 'zh-Hans', '')).toBe('业绩与风险')
    expect(fundDetailTabLabel(type, 'views', 'zh-Hans', '')).toBe('经理观点')
    expect(fundDetailTabLabel(type, 'investment-research', 'zh-Hans', '')).toBe('投资研究')
    expect(fundDetailTabLabel(type, 'archive', 'zh-Hans', '')).toBe('基金档案')
  }
  expect(fundDetailTabLabel('public_fund', 'price', 'zh-Hans', '')).toBe('费用')
  expect(fundDetailTabLabel('private_fund', 'price', 'zh-Hans', '')).toBe('条款')
})

it('routes existing fund risk and material links into the consolidated surfaces', () => {
  expect(resolveFundDetailLocation('research', null, 'public_fund').tab).toBe('views')
  expect(resolveFundDetailLocation('investment-research', null, 'private_fund').tab).toBe('investment-research')
  expect(resolveFundDetailLocation('events', null, 'private_fund').tab).toBe('investment-research')
  expect(resolveFundDetailLocation('risk', null, 'public_fund').tab).toBe('performance')
  expect(resolveFundDetailLocation('analyst', null, 'public_fund').tab).toBe('investment-research')
  expect(resolveFundDetailLocation('analyst', null, 'private_fund').tab).toBe('investment-research')
  for (const section of ['price', 'exposure', 'people', 'strategy', 'documents', 'monitoring']) {
    expect(resolveFundDetailLocation(section, null, 'public_fund')).toEqual({ tab: 'archive', section })
  }
  expect(resolveFundDetailLocation('portfolio', null, 'public_fund')).toEqual({ tab: 'archive', section: 'exposure' })
  expect(resolveFundDetailLocation('archive', 'documents', 'private_fund')).toEqual({ tab: 'archive', section: 'documents' })
  expect(resolveFundDetailLocation('archive', null, 'public_fund').section).toBe('exposure')
  expect(resolveFundDetailLocation('archive', null, 'private_fund').section).toBe('strategy')
})
