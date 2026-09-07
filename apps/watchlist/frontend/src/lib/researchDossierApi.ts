import { fetchJson } from './api'

export type ResearchQuestion = {
  key: string
  question: string
  assessment: string
  evidence_for: string[]
  evidence_against: string[]
  next_check: string
  status: 'open' | 'supported' | 'refuted'
  source_ids: string[]
}

export type ResearchNotebook = {
  fundamental_view: string
  key_drivers: string[]
  valuation_view: string
  questions: ResearchQuestion[]
  important_changes: string[]
  next_research: string[]
  source_ids: string[]
  catalysts?: ResearchCatalyst[]
  facts?: Array<{ subject: string; metric: string; value: string; unit: string; period: string; comparison: string; uncertainty: string; source_ids: string[] }>
}

export type ResearchMandateInput = { title: string; background: string; mechanisms: string[]; research_approach: string[]; focus: string[]; source_plan: string[]; gaps: string[] }
export type ResearchMandate = ResearchMandateInput & { instrument_id: string; role: 'research_method'; entry_id: string | null; updated_at: string | null }

export type ResearchCatalyst = {
  key: string; title: string; scheduled_at: string; status: 'scheduled' | 'released' | 'cancelled'
  relevance: string; scenarios: string[]; next_check: string; outcome: string; source_ids: string[]
}

export type NotebookSource = {
  source_id: string; title?: string; url?: string; source?: string
  published_at?: string | null; retrieved_at?: string | null; recorded_at?: string | null
  metadata?: { published_at?: string | null; effective_date?: string | null }
}
export type SavedResearchNotebook = ResearchNotebook & { run_id: string; checked_at: string | null; sources?: NotebookSource[] }
export type ResearchMaterial = {
  source_id: string
  entry_id: string | null
  title: string
  body: string
  source: string
  metadata: { published_at?: string | null; effective_date?: string | null; extraction?: unknown; [key: string]: unknown }
  recorded_at: string | null
}
export type ResearchFramework = { id: string; title: string; body: string; version: string; source: string; role: 'research_method' }
export type HistoricalResearchCase = {
  case_id: string; case_title: string; source_id: string; role: string
  event: { information_window: { start: string; end: string }; verified_new_information: string; session_mapping: string }
  analysis: { causal_chain: string[]; market_interpretation: string }
  current_use: { lesson: string; similarity_requirements: string[]; important_differences: string[] }
  sources: Array<{ title: string; url: string; supports: string }>
  limitations: string[]
}
export type ResearchDossier = {
  mandate?: ResearchMandate
  prior_sources?: NotebookSource[]
  frameworks: ResearchFramework[]
  materials: ResearchMaterial[]
  historical_cases: HistoricalResearchCase[]
  historical_case_limitations?: string[]
  notebook: SavedResearchNotebook | null
  notebook_history: Array<{ run_id: string; checked_at: string | null; important_changes: string[] }>
}
export type ResearchMaterialInput = { title: string; body: string; source: string; published_at?: string; effective_date?: string }

const dossierPath = (instrumentId: string) => `/api/research/instruments/${encodeURIComponent(instrumentId)}/dossier`

export function getResearchDossier(instrumentId: string, signal?: AbortSignal) {
  return fetchJson<ResearchDossier>(`${dossierPath(instrumentId)}?include_history=true`, { signal })
}

export function saveResearchMandate(instrumentId: string, mandate: ResearchMandateInput) {
  return fetchJson<ResearchMandate>(`${dossierPath(instrumentId)}/mandate`, { method: 'PUT', body: JSON.stringify(mandate) })
}

export function addResearchMaterial(instrumentId: string, material: ResearchMaterialInput) {
  return fetchJson<ResearchMaterial>(`${dossierPath(instrumentId)}/materials`, { method: 'POST', body: JSON.stringify(material) })
}

export function uploadResearchMaterial(instrumentId: string, file: File, material: Omit<ResearchMaterialInput, 'body'>) {
  const body = new FormData()
  body.append('file', file)
  for (const [key, value] of Object.entries(material)) if (value) body.append(key, value)
  // Let the browser supply the multipart boundary instead of the JSON default.
  return fetchJson<ResearchMaterial>(`${dossierPath(instrumentId)}/files`, { method: 'POST', headers: {}, body })
}
