import { fetchJson } from './api'

export type InstrumentTailRisk = {
  instrument_id: string
  status: 'ready' | 'partial' | 'unavailable'
  reason: string | null
  confidence: number
  lookback_days: number
  currency: string | null
  return_kind: 'total_return' | 'price_return' | null
  var: number | null
  expected_shortfall: number | null
  observation_count: number
  tail_effective_observations: number
  tail_observation_count: number
  tail_max_observation_weight: number | null
  window_start_date: string | null
  window_end_date: string | null
  first_observation_start_date: string | null
  last_observation_end_date: string | null
  missing_intervals?: Array<{ start_date: string; end_date: string }>
  missing_return_dates?: string[]
  excluded_interval_count: number
  expected_observation_count: number | null
  uncovered_leading_count: number | null
  missing_internal_count: number | null
  uncovered_trailing_count: number | null
  history_coverage_status: string
  latest_expected_date: string | null
  latest_observation_date: string | null
  source_stale: boolean
  freshness?: { data_freshness_status?: string } | null
}

export function getInstrumentTailRisk(instrumentId: string, confidence: number, lookbackDays: number, signal?: AbortSignal) {
  const query = new URLSearchParams({ confidence: String(confidence), lookback_days: String(lookbackDays) })
  return fetchJson<InstrumentTailRisk>(`/api/instruments/${encodeURIComponent(instrumentId)}/tail-risk?${query}`, { signal })
}
