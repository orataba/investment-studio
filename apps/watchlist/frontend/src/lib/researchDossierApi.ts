import { fetchJson, type InstrumentResearchNote } from './api'
import type { ResearchAssistantReference } from '../../../../../packages/ui/src/researchReference'

// Published research keeps the clock actually retained with its source evidence.
type EstimateSnapshot = { observation_id?: string; collected_at?: string; run_id?: string; read_at?: string | null; cutoff?: string | null }
export type EventSource = {
  source_id?: string; url?: string; title?: string; published_at?: string | null
  published_at_raw?: string | null; retrieved_at?: string | null; discovered_at?: string | null; time_status?: string
  source_type?: string; document_id?: string; version_id?: string
  as_of?: string | null
  measurement?: {
    current?: { date: string; volatility_pct: number } | null
    previous?: { date: string; volatility_pct: number } | null
    change_pp?: number | null; five_session_change_pp?: number | null
    historical_reference?: { start_date: string; end_date: string; median_pct: number; minimum_pct: number; maximum_pct: number } | null
  }
  methodology?: { half_life_sessions?: number; annualization?: number } | null
  current_snapshot?: EstimateSnapshot | null
  previous_snapshot?: EstimateSnapshot | null
  changes?: Array<{
    symbol: string; name?: string | null; frequency: string; target_period_end: string; metric: string; currency: string | null
    previous_value: number; current_value: number; delta_pct: number | null
    previous_collected_at: string | null; current_collected_at: string | null; analyst_count_changed: boolean | null
    current_currency_status?: string
  }>
}

export type ResearchQuestion = {
  key: string
  question: string
  assessment: string
  evidence_for: string[]
  evidence_against: string[]
  next_check: string
  status: 'open' | 'supported' | 'refuted'
  tracking_status?: 'active' | 'paused' | 'closed'
  tracking_reason?: string
  source_ids: string[]
}

export type ResearchReference = ResearchAssistantReference
export type AskResearchAssistant = (question: string, reference?: ResearchReference) => void
export type ResearchRevision = { version_id?: string; created_at?: string | null; updated_at?: string | null; source_run_id?: string }
export type InvestmentView = ResearchRevision & {
  direction: string; horizon: string; attractiveness: string; risk: string; conviction: string
  invalidation?: string; next_check?: string
  assumptions: string[]; source_ids: string[]; versions?: InvestmentView[]
}
export type ResearchForecast = ResearchRevision & {
  key: string; claim: string; variable: string; horizon: string; observation_condition: string
  assumptions: string[]; invalidation: string; review_on?: string | null; status: 'active' | 'confirmed' | 'refuted' | 'expired' | 'withdrawn'
  source_ids: string[]; versions?: ResearchForecast[]
}
export type ResearchForecastReview = ResearchRevision & {
  key: string; forecast_key: string; forecast_version_id: string; outcome: string; mechanism_assessment: string
  alternative_explanations: string[]; source_ids: string[]; versions?: ResearchForecastReview[]
}
export type ResearchLesson = ResearchRevision & {
  key: string; lesson: string; applicability: string; limitations: string
  status?: 'active' | 'withdrawn'; withdrawal_reason?: string
  forecast_key: string | null; forecast_version_id: string | null; source_ids: string[]; versions?: ResearchLesson[]
}

export type ResearchNotebook = {
  investment_view?: InvestmentView | null
  forecasts?: ResearchForecast[]
  forecast_reviews?: ResearchForecastReview[]
  lessons?: ResearchLesson[]
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
export type ResearchMandate = ResearchMandateInput & ResearchRevision & {
  instrument_id: string; role: 'research_method'; entry_id: string | null; updated_at: string | null; versions?: ResearchMandate[]
  readonly user_focus?: string[]; readonly author?: { origin: 'user' | 'research' | 'initial'; display_name?: string } | null
}

export type ResearchCatalyst = {
  key: string; title: string; scheduled_at: string; status: 'scheduled' | 'released' | 'cancelled'
  relevance: string; scenarios: string[]; next_check: string; outcome: string; source_ids: string[]
}

export type NotebookSource = {
  source_id: string; title?: string; url?: string; source?: string
  document_id?: string; version_id?: string; instrument_id?: string
  source_type?: string; as_of?: string | null; run_cutoff?: string | null
  published_at?: string | null; retrieved_at?: string | null; recorded_at?: string | null
  metadata?: { published_at?: string | null; effective_date?: string | null }
  pm_binding_note?: string
}
export type SavedResearchNotebook = ResearchNotebook & ResearchRevision & { run_id: string; checked_at: string | null; sources?: NotebookSource[] }
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
  notebook_history: Array<{ run_id: string; checked_at: string | null; important_changes: string[]; notebook?: SavedResearchNotebook }>
}
export type ResearchMaterialInput = { title: string; body: string; source: string; published_at?: string; effective_date?: string }

export type ResearchThemeInput = {
  title: string; question: string; background?: string; status?: 'active' | 'paused' | 'closed'; responsible_user_id?: string | null; close_reason?: string
}
export type ResearchThemeProgress = {
  run_id: string; recorded_at: string; assessment: string; next_check: string; status: string; source_ids: string[]
}
export type ResearchTheme = ResearchThemeInput & {
  theme_id: string; instrument_id: string; status: 'active' | 'paused' | 'closed'; author_user_id: string; author: string
  created_at: string; updated_at: string; revision_number: number
  notes: InstrumentResearchNote[]; research_progress: ResearchThemeProgress[]
  origin?: 'user' | 'researcher'; close_reason?: string; theme_key?: string; updates?: ResearchUpdate[]
  last_changed_at?: string | null
  current_questions?: Array<ResearchUpdate & {
    last_reviewed_at?: string | null; last_changed_at?: string | null
    last_review_status?: 'reviewed' | 'insufficient_evidence' | null
  }>
}
export type ResearchThemesResponse = {
  identity: { user_id: string; display_name: string; mode: 'account'; local_unrestricted?: boolean; team_id?: string; team_role?: 'admin' | 'member' | 'reader' }
  themes: ResearchTheme[]
}
const themesPath = (instrumentId: string) => `/api/research/instruments/${encodeURIComponent(instrumentId)}/themes`
export function getResearchThemes(instrumentId: string, signal?: AbortSignal) {
  return fetchJson<ResearchThemesResponse>(themesPath(instrumentId), { signal })
}
export function createResearchTheme(instrumentId: string, theme: ResearchThemeInput) {
  return fetchJson<ResearchTheme>(themesPath(instrumentId), { method: 'POST', body: JSON.stringify(theme) })
}
export function updateResearchTheme(instrumentId: string, themeId: string, theme: Partial<ResearchThemeInput>) {
  return fetchJson<ResearchTheme>(`${themesPath(instrumentId)}/${encodeURIComponent(themeId)}`, { method: 'PATCH', body: JSON.stringify(theme) })
}

const dossierPath = (instrumentId: string) => `/api/research/instruments/${encodeURIComponent(instrumentId)}/dossier`

export function getResearchDossier(instrumentId: string, signal?: AbortSignal) {
  return fetchJson<ResearchDossier>(`${dossierPath(instrumentId)}?include_history=true`, { signal })
}

export function getSavedResearchSource(instrumentId: string, sourceId: string, signal?: AbortSignal, versionId?: string) {
  return fetchJson<SavedResearchSource>(`${dossierPath(instrumentId)}?source_id=${encodeURIComponent(sourceId)}${versionId ? `&version_id=${encodeURIComponent(versionId)}` : ''}`, { signal })
}

export type SavedComparison = {
  sample_start?: string | null; sample_end?: string | null; observations?: number; currency?: string
  method?: string; limitations?: string[]
  rows?: Array<{ instrument_id: string; name?: string; return_pct: number | null; max_drawdown_pct: number | null;
    correlation_to_target?: number | null; excess_return_pp?: number | null; return_kind?: string; quote_basis?: string }>
}

export type SavedResearchSource = NotebookSource & {
  text?: string; body?: string
  snapshot?: Record<string, unknown>; company?: Record<string, unknown>
  data?: SavedComparison & Record<string, unknown> & {
    current?: { date: string; volatility_pct: number } | null
    previous?: { date: string; volatility_pct: number } | null
    change_pp?: number | null; five_session_change_pp?: number | null
    limitations?: string[]
    available?: boolean; sample_return_pct?: number | null; frequency?: string; return_kind?: string; quote_basis?: string
    comparisons?: Array<{ instrument_id: string; name: string; role: string; comparison: SavedComparison; note?: string }>
    series?: Array<{ series_id: string; unit: string | null; currency: string | null; observations: number;
      first: { date: string; value: number } | null; latest: { date: string; value: number } | null; change: number | null; change_unit: string | null }>
  }
  methodology?: ({ half_life_sessions?: number; annualization?: number } & Record<string, unknown>) | string
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

export type ResearchUpdate = {
  update_id: string; kind: 'event' | 'question' | 'forecast' | 'review' | 'lesson' | 'theme' | 'opinion' | 'judgment' | 'schedule'
  title: string; body: string; recorded_at: string; theme_ids: string[]; author: string; author_role: 'researcher' | 'user' | 'system'
  sources: EventSource[]; reference: ResearchReference
  occurred_at?: string | null; published_at?: string | null; next_check?: string
  follow_up?: 'none' | 'watch' | 'resolved'; analysis_depth?: 'brief' | 'analysis'
  direction?: 'risk' | 'opportunity' | 'uncertain'; confidence?: string; change?: string; information_type?: 'fact' | 'opinion' | 'rumor' | null
  details?: Array<{ label: string; text: string }>; superseded?: boolean; withdrawn?: boolean
  status?: string; scheduled_at?: string; withdrawal_reason?: string; withdrawn_at?: string
  tracking_status?: 'active' | 'paused' | 'closed' | null; tracking_reason?: string
  citation_correction?: {
    source_run_id: string; source_notebook_version_id: string; source_update_id: string
    reason: string; corrected_at: string; original_recorded_at: string
  }
}
export type CurrentResearchFollowup = {
  followup_id: string; kind: 'event' | 'question' | 'forecast' | 'schedule'
  title: string; assessment: string; next_check: string; theme_ids: string[]
  latest_update: ResearchUpdate; related_updates: ResearchUpdate[]
  last_changed_at: string; last_reviewed_at?: string | null
  last_review_status?: 'reviewed' | 'insufficient_evidence' | null
  last_review_summary?: string
}
export type ResearchActivityResponse = {
  instrument_id: string; updates: ResearchUpdate[]; current_followups: CurrentResearchFollowup[]
}
export function getResearchActivity(instrumentId: string, signal?: AbortSignal) {
  return fetchJson<ResearchActivityResponse>(`/api/research/instruments/${encodeURIComponent(instrumentId)}/activity`, { signal })
}
