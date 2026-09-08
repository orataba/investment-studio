// A publication in the assistant updates the research panels already open on this page.
export const RESEARCH_UPDATED = 'watchlist:research-updated'

export type ResearchPublication = {
  status: 'published' | 'not_requested' | 'failed' | 'conflict'
  instrument_ids: string[]
  message?: string
}

export function announceResearchPublication(instrumentIds: string[]) {
  window.dispatchEvent(new CustomEvent<string[]>(RESEARCH_UPDATED, { detail: instrumentIds }))
}
