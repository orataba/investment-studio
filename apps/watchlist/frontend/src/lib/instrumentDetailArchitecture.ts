export type WatchlistDetailInstrumentType =
  | 'public_fund'
  | 'private_fund'
  | 'etf'
  | 'equity'
  | 'index'

export type ListedDetailTab =
  | 'overview'
  | 'research'
  | 'performance'
  | 'risk'
  | 'price'
  | 'portfolio'
  | 'fundamentals'
  | 'events'
  | 'methodology'
  | 'monitoring'

export type FundDetailTabCode =
  | 'overview'
  | 'performance'
  | 'risk'
  | 'price'
  | 'exposure'
  | 'people'
  | 'strategy'
  | 'documents'
  | 'research'
  | 'monitoring'

const LISTED_TABS: Record<'etf' | 'equity' | 'index', readonly ListedDetailTab[]> = {
  etf: ['overview', 'research', 'performance', 'risk', 'price', 'portfolio', 'monitoring'],
  equity: ['overview', 'research', 'performance', 'risk', 'price', 'fundamentals', 'events', 'monitoring'],
  index: ['overview', 'research', 'performance', 'risk', 'price', 'methodology', 'monitoring'],
}

const FUND_TAB_LABELS: Record<
  'public_fund' | 'private_fund',
  Partial<Record<FundDetailTabCode, { en: string; zh: string }>>
> = {
  public_fund: {
    price: { en: 'Fees', zh: '费用' },
    exposure: { en: 'Portfolio', zh: '持仓' },
    people: { en: 'Management', zh: '管理团队' },
  },
  private_fund: {
    price: { en: 'Terms', zh: '条款' },
    exposure: { en: 'Exposure', zh: '敞口' },
    people: { en: 'Organization', zh: '机构与团队' },
  },
}

export function listedDetailTabs(
  instrumentType: 'etf' | 'equity' | 'index',
): readonly ListedDetailTab[] {
  return LISTED_TABS[instrumentType]
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
