export type WatchlistDetailInstrumentType =
  | 'public_fund'
  | 'private_fund'
  | 'etf'
  | 'equity'
  | 'index'
  | 'crypto'

export type ListedDetailTab = 'overview' | 'investment-research' | 'views' | 'performance'

export type FundDetailTabCode =
  | 'overview'
  | 'performance'
  | 'risk'
  | 'price'
  | 'exposure'
  | 'people'
  | 'strategy'
  | 'documents'
  | 'investment-research'
  | 'monitoring'
  | 'views'
  | 'archive'

export type FundPrimaryTab = 'overview' | 'investment-research' | 'views' | 'performance' | 'archive'
export type FundArchiveSection = 'exposure' | 'people' | 'strategy' | 'price' | 'documents' | 'monitoring'

export function fundDetailTabs(): readonly FundPrimaryTab[] {
  return ['overview', 'investment-research', 'views', 'performance', 'archive']
}

export function resolveFundDetailLocation(tab: string | null, section: string | null, instrumentType: 'public_fund' | 'private_fund'):
  { tab: FundPrimaryTab; section: FundArchiveSection } {
  const sections: FundArchiveSection[] = ['exposure', 'people', 'strategy', 'price', 'documents', 'monitoring']
  const legacySection = tab === 'portfolio' ? 'exposure' : tab
  const selectedSection = sections.includes(legacySection as FundArchiveSection) ? legacySection : section
  const resolvedSection = sections.includes(selectedSection as FundArchiveSection)
    ? selectedSection as FundArchiveSection : instrumentType === 'public_fund' ? 'exposure' : 'strategy'
  if (sections.includes(legacySection as FundArchiveSection)) return { tab: 'archive', section: resolvedSection }
  if (tab === 'risk') return { tab: 'performance', section: resolvedSection }
  if (tab === 'research') return { tab: 'views', section: resolvedSection }
  if (tab === 'analyst' || tab === 'events') return { tab: 'investment-research', section: resolvedSection }
  return { tab: fundDetailTabs().includes(tab as FundPrimaryTab) ? tab as FundPrimaryTab : 'overview', section: resolvedSection }
}

const LISTED_TABS: readonly ListedDetailTab[] = ['overview', 'investment-research', 'views', 'performance']

const FUND_TAB_LABELS: Record<
  'public_fund' | 'private_fund',
  Partial<Record<FundDetailTabCode, { en: string; zh: string }>>
> = {
  public_fund: {
    'investment-research': { en: 'Investment Research', zh: '投资研究' },
    views: { en: 'PM Views', zh: '经理观点' },
    performance: { en: 'Performance & Risk', zh: '业绩与风险' },
    archive: { en: 'Fund Archive', zh: '基金档案' },
    price: { en: 'Fees', zh: '费用' },
    exposure: { en: 'Portfolio', zh: '持仓' },
    people: { en: 'Management', zh: '管理团队' },
  },
  private_fund: {
    'investment-research': { en: 'Investment Research', zh: '投资研究' },
    views: { en: 'PM Views', zh: '经理观点' },
    performance: { en: 'Performance & Risk', zh: '业绩与风险' },
    archive: { en: 'Fund Archive', zh: '基金档案' },
    price: { en: 'Terms', zh: '条款' },
    exposure: { en: 'Exposure', zh: '敞口' },
    people: { en: 'Organization', zh: '机构与团队' },
  },
}

export function listedDetailTabs(
  _instrumentType: 'etf' | 'equity' | 'index' | 'crypto',
): readonly ListedDetailTab[] {
  return LISTED_TABS
}

export function fundDetailTabLabel(
  instrumentType: 'public_fund' | 'private_fund',
  tab: FundDetailTabCode,
  language: 'en' | 'zh-Hans',
  fallback: string,
): string {
  const label = FUND_TAB_LABELS[instrumentType][tab]
  if (!label) return fallback
  return language === 'zh-Hans' ? label.zh : label.en
}

export function detailWorkspaceFamily(
  instrumentType: WatchlistDetailInstrumentType,
): 'fund' | 'listed' {
  return instrumentType === 'public_fund' || instrumentType === 'private_fund'
    ? 'fund'
    : 'listed'
}
