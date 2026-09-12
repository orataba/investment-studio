import { useEffect, useState } from 'react'
import { getResearchActivity, type ResearchActivityResponse } from './researchDossierApi'
import { RESEARCH_UPDATED } from './researchUpdates'

export type ResearchActivityState = { data: ResearchActivityResponse | null; error: string }

export function useResearchActivity(instrumentId: string, reviewRunId?: string, reviewStatus?: string, enabled = true): ResearchActivityState {
  const [snapshot, setSnapshot] = useState<{ instrumentId: string; data: ResearchActivityResponse } | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    if (!enabled) return
    const updated = (event: Event) => {
      if ((event as CustomEvent<string[]>).detail.includes(instrumentId)) setRefresh(value => value + 1)
    }
    window.addEventListener(RESEARCH_UPDATED, updated)
    return () => window.removeEventListener(RESEARCH_UPDATED, updated)
  }, [instrumentId, enabled])
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    setError('')
    void getResearchActivity(instrumentId, controller.signal).then(data => {
      if (!controller.signal.aborted) setSnapshot({ instrumentId, data })
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '研究记录读取失败') })
    return () => controller.abort()
  }, [instrumentId, reviewRunId, reviewStatus, refresh, enabled])
  return { data: snapshot?.instrumentId === instrumentId ? snapshot.data : null, error }
}
