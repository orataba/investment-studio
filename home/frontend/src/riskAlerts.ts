import type { ModelWorkStatus } from '../../../packages/ui/src/modelWork'
import type { RiskAssessmentSnapshot, RiskPendingRevision } from '../../../packages/ui/src/instrumentRisk'
import { resolveWorkspaceUrl } from '../../../packages/ui/src/navigation'
import { readWithDeadline } from '../../../packages/ui/src/readDeadline'
import type { StudioApp } from './StudioHome'

export type RiskAlert = {
  case_id: string; title: string; instrument_id: string; instrument_name: string
  impact_level: string | null; urgency: string | null; updated_at: string; reviewed_at: string | null
  confidence: string | null; as_of: string | null
  alert_state?: 'assessed' | 'assessment_pending' | 'unverified_urgent'
  event_version_id?: string | null
  last_assessed_snapshot?: RiskAssessmentSnapshot | null
  pending_revision?: RiskPendingRevision | null
  target: { workspace: 'watchlist' | 'portfolio'; path: string }
}
export type RiskAlerts = { alerts: RiskAlert[]; total: number; has_more: boolean; selection_note: string; model_work?: ModelWorkStatus }

export function riskDestination(apps: StudioApp[], target: RiskAlert['target']) {
  if (!['watchlist', 'portfolio'].includes(target.workspace) || !target.path.startsWith('/') || target.path.startsWith('//') || target.path.includes('\\')) throw new Error('Invalid risk destination')
  return `${resolveWorkspaceUrl(apps.find(app => app.app_id === target.workspace)?.url, target.workspace)}${target.path}`
}

export async function readRiskAlerts(base: string, offset: number, signal?: AbortSignal): Promise<RiskAlerts> {
  return readWithDeadline(async readSignal => {
    const response = await fetch(`${base}/api/risk/alerts?limit=3&offset=${offset}`, { credentials: 'include', signal: readSignal, cache: 'no-store' })
    if (!response.ok) {
      const requestId = response.headers.get('X-Request-ID')
      throw new Error(`HTTP ${response.status}${requestId ? ` · ${requestId}` : ''}`)
    }
    return response.json()
  }, signal)
}
