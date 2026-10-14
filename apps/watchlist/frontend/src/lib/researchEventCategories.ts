import type { EventSource, ResearchEventCategory } from './researchDossierApi'

export const researchCategoryLabels: Record<ResearchEventCategory, string> = {
  news: '一般动态', company_announcement: '公司公告', earnings: '业绩发布',
  earnings_call_transcript: '电话会文字稿', regulatory_filing: '监管披露',
  industry_policy: '行业政策', industry_news: '行业动态', opinion: '市场观点', meeting: '交流纪要', other: '其他',
}

const sourceTypes: Record<string, string> = {
  company_announcement: '公司公告', company_news: '公司新闻',
  earnings_call_transcript: '电话会文字稿', regulatory_filing: '监管披露',
}
const sourceChannels: Record<string, string> = {
  'fmp:press_releases': '公司公告', 'fmp:stock_news': '公司新闻',
  'fmp:earnings_transcripts': '电话会文字稿', 'fmp:sec_filings': '监管披露',
}

// Source metadata describes the materials; it does not rewrite a saved event's category.
export function researchSourceLabels(sources: EventSource[], category?: ResearchEventCategory): string[] {
  const mainLabel = category && researchCategoryLabels[category]
  return [...new Set(sources.map(source => sourceTypes[source.source_type || '']
    || sourceTypes[source.information_type || ''] || sourceChannels[source.channel_id || '']))]
    .filter((label): label is string => Boolean(label) && label !== mainLabel)
}
