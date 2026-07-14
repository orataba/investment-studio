import type {
  PortfolioAllocationResearchAsOfMode,
  PortfolioAllocationResearchSettingsRecord,
} from './api'

export type PortfolioAllocationResearchAsOfDraft = {
  asOfMode: PortfolioAllocationResearchAsOfMode
  asOfDate: string
}

export function resolveAllocationResearchAsOfDraft(
  settings: Pick<PortfolioAllocationResearchSettingsRecord, 'as_of_mode' | 'as_of_date' | 'pinned_as_of_date'>,
  latestAsOfDate: string,
): PortfolioAllocationResearchAsOfDraft {
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

export function serializeAllocationResearchAsOf({ asOfMode, asOfDate }: PortfolioAllocationResearchAsOfDraft) {
  return {
    as_of_mode: asOfMode,
    as_of_date: asOfMode === 'pinned' ? asOfDate || null : null,
  }
}
