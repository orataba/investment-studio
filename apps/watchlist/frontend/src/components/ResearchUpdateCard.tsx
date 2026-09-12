import { useState, type FormEvent } from 'react'
import { useStudioAccount } from './AccountBoundary'
import type { AskResearchAssistant, ResearchUpdate } from '../lib/researchDossierApi'
import { SourceList, dateLabel, hasTimeZone } from './ResearchEvidence'

export const updateKindLabels: Record<ResearchUpdate['kind'], string> = {
  event: '事件', question: '问题进展', judgment: '研究判断', schedule: '观察日程', forecast: '预测', review: '复盘', lesson: '研究经验', theme: '主题', opinion: '投资观点',
}
const followUpLabels = { none: '暂不跟进', watch: '继续跟进', resolved: '已结束跟进' }

export function updateStatusLabel(update: ResearchUpdate) {
  if (!update.status) return ''
  if (update.kind === 'schedule' && update.status === 'scheduled') {
    if (!update.scheduled_at || (update.scheduled_at.length !== 10 && !hasTimeZone(update.scheduled_at))) return '时间待核实'
    const now = update.superseded || update.withdrawn ? new Date(update.recorded_at) : new Date()
    const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
    const elapsed = update.scheduled_at.length === 10 ? update.scheduled_at < day : Date.parse(update.scheduled_at) <= now.getTime()
    return elapsed ? '结果待核实' : '预定事件'
  }
  const labels: Partial<Record<ResearchUpdate['kind'], Record<string, string>>> = {
    forecast: { active: '持续观察', confirmed: '结果已出现', refuted: '预测未成立', expired: '观察期已结束', withdrawn: '已撤回' },
    question: { open: '继续研究', supported: '当前证据支持', refuted: '当前证据不支持' },
    schedule: { released: '已发布', cancelled: '已取消' },
    theme: { active: '持续关注', paused: '已暂停', closed: '已结束' },
  }
  return labels[update.kind]?.[update.status] || ''
}

export default function ResearchUpdateCard({ update, onAskAssistant, themeNames = {}, inTheme = false }: {
  update: ResearchUpdate; onAskAssistant?: AskResearchAssistant; themeNames?: Record<string, string>; inTheme?: boolean
}) {
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const [expandedBody, setExpandedBody] = useState(false)
  const [writing, setWriting] = useState(false)
  const [opinion, setOpinion] = useState('')
  const historical = Boolean(update.superseded || update.withdrawn)
  const statusLabel = updateStatusLabel(update)
  const reference = { ...update.reference, research_update_id: update.update_id }
  function saveOpinion(event: FormEvent) {
    event.preventDefault()
    if (!opinion.trim()) return
    onAskAssistant?.(`请将我对研究更新“${update.title}”的以下判断保存为我的投资观点，关联本条研究更新及其事件或主题，保留我的原意和作者归属。以下是我的判断：\n${opinion.trim()}`, reference)
    setWriting(false)
  }
  const body = <>
    {update.body && <p className={`research-update-body${!expandedBody && update.body.length > 220 ? ' research-update-excerpt' : ''}`} translate="no">{update.body}</p>}
    {update.body.length > 220 && <button className="sector-event-ask research-update-expand" type="button" aria-expanded={expandedBody} onClick={() => setExpandedBody(value => !value)}>{expandedBody ? '收起分析' : '展开分析'}</button>}
    {update.next_check && <p className="research-update-next"><strong>下一步观察</strong> <span translate="no">{update.next_check}</span></p>}
    {update.scheduled_at && <p className="sector-event-dates"><span>预定时间 <time dateTime={update.scheduled_at}>{dateLabel(update.scheduled_at)}</time></span></p>}
    {(update.occurred_at || update.published_at) && <p className="sector-event-dates">
      {update.occurred_at && <span>事件发生 <time dateTime={update.occurred_at}>{dateLabel(update.occurred_at)}</time></span>}
      {update.published_at && <span>信息发布 <time dateTime={update.published_at}>{dateLabel(update.published_at)}</time></span>}
    </p>}
    {Boolean(update.details?.length || update.sources.length) && <details className="research-update-evidence"><summary>分析与研究依据</summary>
      {update.details?.filter(detail => detail.text).map((detail, index) => <div key={`${detail.label}:${index}`}><strong>{detail.label}</strong><p translate="no">{detail.text}</p></div>)}
      <SourceList instrumentId={reference.instrument_id} sources={update.sources.map((source, index) => ({ ...source, source_id: source.source_id || `${update.update_id}:${index}` }))} />
    </details>}
    {onAskAssistant && <div className="research-update-actions">
      <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请${historical ? '按当时信息复核这条历史研究更新' : '继续分析这条研究更新'}“${update.title}”。读取它对应的原始证据、相关事件及主题，区分新增事实、机制判断和市场定价；核实后说明是否需要改变认识或继续跟进。${historical ? '此版本已被修订或撤回，请区分当时判断与当前结论。' : ''}`, reference)}>{historical ? '讨论当时判断' : '追问这条更新'}</button>
      {canWrite && !historical && <>
        <button type="button" className="sector-event-ask" onClick={() => setWriting(value => !value)}>写投资观点</button>
        {!inTheme && update.theme_ids.length === 0 && update.kind !== 'theme' && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请基于研究更新“${update.title}”建立值得持续研究的主题并跟踪更新。先读取该更新和现有主题，明确投资意义、核心问题、当前判断及下一步观察；已有对应主题时更新该主题，避免重复。如果这只是无需后续跟进的一次性事项，请说明理由，不强行建立主题。`, reference)}>请助手建立主题</button>}
      </>}
    </div>}
    {writing && <form className="research-update-opinion" onSubmit={saveOpinion}>
      <label>我的投资判断<textarea rows={3} value={opinion} onChange={event => setOpinion(event.target.value)} required /></label>
      <p className="sector-research-note">将交给研究助手保存，并关联这条更新；助手完成保存后会显示结果。</p>
      <div className="research-theme-actions"><button type="submit" disabled={!opinion.trim()}>交给助手保存</button><button type="button" onClick={() => setWriting(false)}>取消</button></div>
    </form>}
  </>
  return <article className={`research-update-card${historical ? ' research-update-historical' : ''}`} data-update-id={update.update_id}>
    <div className="research-update-meta"><span>{updateKindLabels[update.kind]}{update.analysis_depth === 'brief' && <> · <span>简讯</span></>}</span>
      <span translate={update.author === '研究员' || !update.author ? undefined : 'no'}>{update.author || (update.author_role === 'researcher' ? '研究员' : '未标注作者')}</span>
      <span>{update.author_role === 'user' ? '人工判断' : '研究员记录'}</span>
      <time dateTime={update.recorded_at}>{dateLabel(update.recorded_at)}</time>
      {update.information_type === 'rumor' && <span className="sector-research-limitation">传闻 · 待证实</span>}
      {update.information_type === 'opinion' && <span>来源观点</span>}
      {statusLabel && <span>{statusLabel}</span>}
      {!historical && update.follow_up && <span>{followUpLabels[update.follow_up]}</span>}
      {historical && <strong>{update.withdrawn ? '已撤回 · 仅供追溯' : '已修订 · 当时版本'}</strong>}
    </div>
    <h4 translate="no">{update.title}</h4>
    {update.withdrawn && update.withdrawal_reason && <p className="sector-research-limitation" translate="no">{update.withdrawal_reason}</p>}
    {update.withdrawn_at && <p className="sector-research-note">撤回时间 <time dateTime={update.withdrawn_at}>{dateLabel(update.withdrawn_at)}</time></p>}
    {!inTheme && update.theme_ids.length > 0 && <div className="research-update-themes">{update.theme_ids.map(id => <a key={id} href={`#research-theme-${encodeURIComponent(id)}`} onClick={() => {
      let ancestor = document.getElementById(`research-theme-${encodeURIComponent(id)}`)?.parentElement
      while (ancestor) { if (ancestor instanceof HTMLDetailsElement) ancestor.open = true; ancestor = ancestor.parentElement }
    }} translate={themeNames[id] ? 'no' : undefined}>{themeNames[id] || '关联主题'}</a>)}</div>}
    {historical ? <details className="research-update-old"><summary>查看当时记录</summary>{body}</details> : body}
  </article>
}
