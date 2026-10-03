export type PriceRiskPeriod = 'day' | 'week' | 'month' | 'quarter'
export type PriceRuleCounts = Record<'configured' | 'evaluable' | 'triggered' | 'unavailable' | 'default' | 'custom' | 'unknown', number>
export type PriceRuleSummary = {
  contract_version: number
  settings_updated_at: string | null
  counts: PriceRuleCounts
  rules: Array<{ key: PriceRiskPeriod | 'drawdown'; source: 'default' | 'custom' | 'unknown' | 'none';
    state: 'not_configured' | 'unavailable' | 'triggered' | 'not_triggered'; limit_pct: number | null;
    value_pct: number | null; observations: number | null; start_date: string | null; end_date: string | null; limitation: string | null }>
}
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
  price_rule_summary?: PriceRuleSummary
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
export type RiskCaseHistory = {
  at: string; action?: string; detail?: string; case_id?: string; title?: string; run_id?: string
  actor?: { user_id?: string | null; display_name?: string | null; kind?: string | null; service_id?: string | null }
  scope?: { kind: string; id: string; name: string }
  before?: { status: string; trigger_active: boolean; risk_assessment?: unknown }
  after?: { status: string; trigger_active: boolean; risk_assessment?: unknown }
  risk_assessment?: RiskAssessment
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
  history_json?: RiskCaseHistory[]
  history_count?: number
  detail_available?: boolean
}
export type RiskWorkspace = { instruments: RiskAsset[]; cases: RiskCase[] }

export type RiskAssessment = {
  status: string
  issue_key?: string
  event_version_id?: string
  run_id?: string
  submitted_at?: string
  reviewed_at?: string
  reason?: string
}

export function riskPendingState(assessment: unknown, eventVersion: unknown): 'review' | 'verification' | null {
  if (!assessment || typeof assessment !== 'object' || !('status' in assessment) || assessment.status !== 'pending') return null
  const receipt = assessment as RiskAssessment
  const reviewedAt = typeof receipt.reviewed_at === 'string' ? Date.parse(receipt.reviewed_at) : NaN
  const submittedAt = typeof receipt.submitted_at === 'string' ? Date.parse(receipt.submitted_at) : NaN
  // A new referral retains the previous receipt, so its review must also postdate this submission.
  return typeof eventVersion === 'string' && Boolean(eventVersion.trim())
    && receipt.event_version_id === eventVersion
    && typeof receipt.run_id === 'string' && Boolean(receipt.run_id.trim())
    && typeof receipt.reason === 'string' && Boolean(receipt.reason.trim())
    && Number.isFinite(reviewedAt)
    && (receipt.submitted_at == null || (Number.isFinite(submittedAt) && reviewedAt >= submittedAt))
    ? 'verification' : 'review'
}

export type RiskRequest = <T>(path: string, init?: RequestInit) => Promise<T>
export const percent = (value: number | null | undefined) =>
  value == null ? '—' : `${value.toFixed(2)}%`
export function today() {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10)
}
