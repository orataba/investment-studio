import { API_BASE_URL, fetchJson } from './api'

export type {
  RiskAsset as ResearchAsset,
  RiskWorkspace,
  RiskCase,
} from '../../../../../packages/ui/src/instrumentRisk'
export type Topic = {
  topic_id: string
  title: string
  question: string
  instrument_ids: string[]
  portfolio_id: string | null
  status: string
  conclusion: string
  next_review_date: string | null
  updated_at: string
}
export type Entry = {
  entry_id: string
  topic_id: string
  kind: string
  title: string
  body: string
  source: string
  follow_up_date: string | null
  completed_at: string | null
  context_json: Record<string, unknown>
  status: string
  created_at: string
}
export type Connections = {
  available: boolean
  research_enabled: boolean
  assistant_available: boolean
  portfolios: Array<{ portfolio_id: string; portfolio_name: string }>
}
export const readWorkbench = <T>(path: string) => fetchJson<T>(`/api${path}`)
export const writeWorkbench = <T>(
  path: string,
  value: unknown,
  method = 'POST',
) => fetchJson<T>(`/api${path}`, { method, body: JSON.stringify(value) })
export async function uploadTopicFile(
  topicId: string,
  file: File,
): Promise<Entry> {
  const body = new FormData()
  body.append('file', file)
  const response = await fetch(
    `${API_BASE_URL}/api/research/topics/${topicId}/files`,
    { method: 'POST', body, credentials: 'include' },
  )
  if (response.status === 401) window.dispatchEvent(new Event('studio:unauthorized'))
  if (!response.ok)
    throw new Error('材料上传失败，请检查文件格式和大小（最多 25 MB）')
  return response.json()
}
export const stageLabels: Record<string, string> = {
  watching: '观察中',
  researching: '研究中',
  candidate: '候选',
  paused: '暂缓',
  archived: '归档',
}
export function percent(value: number | null | undefined) {
  return value == null ? '—' : `${value.toFixed(2)}%`
}
export function today() {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10)
}
export type PortfolioContext = {
  portfolio_name?: string
  as_of_date?: string
  base_currency?: string
  totals: { nav?: number | null }
  rows: Array<{
    instrument_core?: {
      instrument_id?: string
      instrument_name?: string
      name?: string
    }
    market_value_base?: number | null
    risk_eligible?: boolean
    weight?: number | null
  }>
}
