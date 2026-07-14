import type {
  PortfolioResearchAsOfMode,
  PortfolioResearchSettingsRecord,
} from './api'

export type PortfolioResearchAsOfDraft = {
  asOfMode: PortfolioResearchAsOfMode
  asOfDate: string
}

export function resolveResearchAsOfDraft(
  settings: Pick<PortfolioResearchSettingsRecord, 'as_of_mode' | 'as_of_date' | 'pinned_as_of_date'>,
  latestAsOfDate: string,
): PortfolioResearchAsOfDraft {
  if (settings.as_of_mode === 'pinned') {
    return {
      asOfMode: 'pinned',
      asOfDate: settings.pinned_as_of_date ?? settings.as_of_date ?? latestAsOfDate,
    }
  }

  return {
    asOfMode: 'dynamic',
    asOfDate: latestAsOfDate || settings.as_of_date || '',
  }
}

export function serializeResearchAsOf({ asOfMode, asOfDate }: PortfolioResearchAsOfDraft) {
  return {
    as_of_mode: asOfMode,
    as_of_date: asOfMode === 'pinned' ? asOfDate || null : null,
  }
}
