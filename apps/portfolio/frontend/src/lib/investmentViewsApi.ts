import { requestPortfolioResource } from './api'

export type InvestmentViewInstrument = { instrument_id: string; instrument_name: string; instrument_type: string }
export type InvestmentViewInput = {
  view_date: string
  title: string
  body: string
  context: string
  source?: string
  instrument_ids: string[]
}
export type InvestmentView = InvestmentViewInput & {
  source: string
  portfolio_id: string
  note_id: string
  revision_number: number
  instruments: InvestmentViewInstrument[]
  author_user_id: string
  author_name: string
  created_at: string
  revised_by_user_id: string
  revised_by_name: string
  updated_at: string
  can_edit: boolean
}
export type InvestmentStance = {
  note: InvestmentView
  selected_at: string
  selected_by_user_id: string
  selected_by_name: string
}
export type InvestmentStanceResponse = { current_stance: InvestmentStance | null; stance_version: number }
export type InvestmentViewsResponse = InvestmentStanceResponse & { portfolio_id: string; notes: InvestmentView[] }

const path = (portfolioId: string) => `/api/portfolios/${encodeURIComponent(portfolioId)}/investment-views`

export function getInvestmentViews(portfolioId: string, instrumentId?: string, signal?: AbortSignal) {
  const query = instrumentId ? `?${new URLSearchParams({ instrument_id: instrumentId })}` : ''
  return requestPortfolioResource<InvestmentViewsResponse>(`${path(portfolioId)}${query}`, { signal, cache: 'no-store' })
}
export function getInvestmentViewInstruments(portfolioId: string, signal?: AbortSignal) {
  return requestPortfolioResource<{ portfolio_id: string; instruments: InvestmentViewInstrument[] }>(`${path(portfolioId)}/instruments`, { signal, cache: 'no-store' })
}
export function createInvestmentView(portfolioId: string, payload: InvestmentViewInput) {
  return requestPortfolioResource<InvestmentView>(path(portfolioId), { method: 'POST', body: JSON.stringify(payload) })
}
export function correctInvestmentView(portfolioId: string, noteId: string, payload: InvestmentViewInput & { expected_revision: number }) {
  return requestPortfolioResource<InvestmentView>(`${path(portfolioId)}/${encodeURIComponent(noteId)}`, { method: 'PUT', body: JSON.stringify(payload) })
}
export function getInvestmentViewRevisions(portfolioId: string, noteId: string, signal?: AbortSignal) {
  return requestPortfolioResource<{ portfolio_id: string; note_id: string; revisions: InvestmentView[] }>(`${path(portfolioId)}/${encodeURIComponent(noteId)}/revisions`, { signal, cache: 'no-store' })
}
export function selectInvestmentStance(portfolioId: string, payload: { note_id: string | null; revision_number: number | null; expected_version: number }) {
  return requestPortfolioResource<InvestmentStanceResponse>(`${path(portfolioId)}/current-stance`, { method: 'PUT', body: JSON.stringify(payload) })
}
