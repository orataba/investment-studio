export type PriceRiskPeriod = 'day' | 'week' | 'month' | 'quarter'
export type PeriodLossReading = {
  period: PriceRiskPeriod
  label: string
  observations: number
  start_date: string | null
  end_date: string | null
  return_pct: number | null
  limit_pct: number | null
  breached: boolean
  limitation: string | null
}
export type RiskAsset = {
  instrument_id: string
  name: string
  instrument_type: string
  as_of_date: string | null
  attributes: Record<string, unknown>
  risk?: {
    current_drawdown?: number | null
    drawdown_summary?: { maximum?: number; valley_date?: string }
    data_quality?: { status?: string; note?: string }
    risk_change_monitor?: { rows?: Array<Record<string, unknown>> }
  }
  freshness?: string
  drawdown_limit?: number | null
  drawdown_change_pp?: number | null
  previous_observation_date?: string | null
  period_limits?: Partial<Record<PriceRiskPeriod, number | null>>
  period_readings?: PeriodLossReading[]
  price_risk_note?: string | null
  return_kind?: string | null
  price_risk_calibration?: {
    sample_start?: string
    sample_end?: string
    observations?: number
    daily_volatility_pct?: number
    manually_edited?: boolean
  }
}
export type RiskCase = {
  case_id: string
  instrument_id: string
  title: string
  body: string
  signal: string
  severity: string
  status: string
  trigger_active: boolean
  observed_on: string | null
  created_at: string
  updated_at: string
  follow_up_date: string | null
  evidence_json: Record<string, unknown>
  history_json: Array<{ at: string; action: string; detail: string }>
}
export type RiskWorkspace = { instruments: RiskAsset[]; cases: RiskCase[] }

export type RiskRequest = <T>(path: string, init?: RequestInit) => Promise<T>
export const percent = (value: number | null | undefined) =>
  value == null ? '—' : `${value.toFixed(2)}%`
export function today() {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10)
}
