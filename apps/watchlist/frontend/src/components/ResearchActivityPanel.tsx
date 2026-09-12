import { useEffect, useState } from 'react'
import { getResearchActivity, type AskResearchAssistant, type ResearchUpdate } from '../lib/researchDossierApi'
import { RESEARCH_UPDATED } from '../lib/researchUpdates'
import ResearchUpdateCard, { updateKindLabels } from './ResearchUpdateCard'

type ActivitySnapshot = { instrumentId: string; updates: ResearchUpdate[] }
export function researchDay(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
export default function ResearchActivityPanel({ instrumentId, reviewRunId, reviewStatus, onAskAssistant, themeNames }: {
  instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant; themeNames?: Record<string, string>
}) {
  const [snapshot, setSnapshot] = useState<ActivitySnapshot | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [days, setDays] = useState(7)
  const [kind, setKind] = useState('all')
  const [includeHistorical, setIncludeHistorical] = useState(false)
  const updates = snapshot?.instrumentId === instrumentId ? snapshot.updates : null
  useEffect(() => {
    const updated = (event: Event) => { if ((event as CustomEvent<string[]>).detail.includes(instrumentId)) setRefresh(value => value + 1) }
    window.addEventListener(RESEARCH_UPDATED, updated)
    return () => window.removeEventListener(RESEARCH_UPDATED, updated)
  }, [instrumentId])
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    void getResearchActivity(instrumentId, controller.signal).then(data => {
      if (!controller.signal.aborted) setSnapshot({ instrumentId, updates: data.updates || [] })
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '研究动态读取失败') })
    return () => controller.abort()
  }, [instrumentId, reviewRunId, reviewStatus, refresh])
  const cutoff = new Date()
  cutoff.setHours(0, 0, 0, 0)
  cutoff.setDate(cutoff.getDate() - days + 1)
  const start = days === 0 ? '' : researchDay(cutoff.toISOString())
  const scoped = (updates || []).filter(update => (!days || researchDay(update.recorded_at) >= start) && (kind === 'all' || update.kind === kind))
  const historyCount = scoped.filter(update => update.superseded || update.withdrawn).length
  const visible = scoped.filter(update => includeHistorical || (!update.superseded && !update.withdrawn))
    .sort((a, b) => Date.parse(b.recorded_at) - Date.parse(a.recorded_at) || b.update_id.localeCompare(a.update_id))
  return <section className="sector-research-progress research-activity" aria-label="研究动态">
    <div className="sector-timeline-heading"><h3>研究动态</h3><div className="research-activity-filters">
      <label>浏览范围<select value={days} onChange={event => setDays(Number(event.target.value))}><option value={7}>近 7 日</option><option value={30}>近 30 日</option><option value={90}>近 90 日</option><option value={0}>全部记录</option></select></label>
      <label>更新类型<select value={kind} onChange={event => setKind(event.target.value)}><option value="all">全部类型</option>{Object.entries(updateKindLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    </div></div>
    <p className="sector-research-note">按研究形成时间排列；事件发生和信息发布日期分别保留。主题中的更新与这里是同一条记录。</p>
    {error && <p role="alert">研究动态暂时无法读取：{error}</p>}
    {updates === null && !error && <p className="sector-research-note" role="status">正在读取研究动态…</p>}
    {historyCount > 0 && <label className="research-activity-history"><input type="checkbox" checked={includeHistorical} onChange={event => setIncludeHistorical(event.target.checked)} />显示已修订或撤回的记录 · {historyCount}</label>}
    {updates !== null && <div className="research-activity-records">{visible.length ? visible.map((update, index) => <div key={update.update_id}>
      {(index === 0 || researchDay(update.recorded_at) !== researchDay(visible[index - 1].recorded_at)) && <h4 className="research-activity-date"><time dateTime={researchDay(update.recorded_at)}>{researchDay(update.recorded_at) || '时间未记录'}</time></h4>}
      <ResearchUpdateCard themeNames={themeNames} update={update} onAskAssistant={onAskAssistant} />
    </div>) : <p className="sector-event-empty">当前范围没有研究更新。检查是否完成及资料覆盖情况，请查看上方研究状态。</p>}</div>}
  </section>
}
