export type ResearchAssistantReference = {
  instrument_id: string
  research_update_id?: string
  event_case_id?: string
  event_version_id?: string
  risk_case_id?: string
  risk_case_updated_at?: string
  notebook_version_id?: string
  investment_view_version_id?: string
  forecast_key?: string
  forecast_version_id?: string
  theme_id?: string
  pm_note_id?: string
  pm_note_revision?: number
}

export const riskReferenceParams = ['research_update_id', 'event_case_id', 'event_version_id', 'risk_case_id', 'risk_case_updated_at'] as const

export function setRiskReferenceParams(params: URLSearchParams, reference?: ResearchAssistantReference) {
  for (const key of riskReferenceParams) {
    const value = reference?.[key]
    if (value) params.set(key, value)
    else params.delete(key)
  }
}

export function readRiskReferenceParams(params: URLSearchParams, instrumentId?: string): ResearchAssistantReference | undefined {
  if (!instrumentId) return undefined
  const fields = riskReferenceParams.flatMap(key => params.get(key) ? [[key, params.get(key)!]] : [])
  return fields.length ? { instrument_id: instrumentId, ...Object.fromEntries(fields) } : undefined
}
