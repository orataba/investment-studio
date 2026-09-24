import { useState } from 'react'
import { useStudioAccount } from './AccountBoundary'
import type { AskResearchAssistant, ResearchUpdate } from '../lib/researchDossierApi'
import { SourceList, dateLabel, hasTimeZone } from './ResearchEvidence'
import ResearchOpinionComposer from './ResearchOpinionComposer'
import ResearchThemeComposer from './ResearchThemeComposer'
import ResearchReadingAside from './ResearchReadingAside'
const questionTrackingLabels = { active: '跟进中', paused: '已暂停', closed: '已结束' }

export const updateKindLabels: Record<ResearchUpdate['kind'], string> = {
  event: '事件', question: '问题进展', judgment: '研究判断', schedule: '观察日程', forecast: '预测', review: '复盘', lesson: '研究经验', theme: '主题', opinion: '投资观点',
}
const followUpLabels = { none: '当时无需跟进', watch: '当时安排跟进', resolved: '已结束跟进' }

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
    forecast: { active: '当时待验证', confirmed: '结果已出现', refuted: '预测未成立', expired: '观察期已结束', withdrawn: '已撤回' },
    question: { open: '当时待验证', supported: '当时证据支持', refuted: '当时证据不支持' },
    schedule: { released: '已发布', cancelled: '已取消' },
    theme: { active: '持续关注', paused: '已暂停', closed: '已结束' },
    lesson: { withdrawn: '经验已停用' },
  }
  return labels[update.kind]?.[update.status] || ''
}

export default function ResearchUpdateCard({ update, onAskAssistant, themeNames = {}, inTheme = false, compact = false, readable = false }: {
  update: ResearchUpdate; onAskAssistant?: AskResearchAssistant; themeNames?: Record<string, string>; inTheme?: boolean; compact?: boolean; readable?: boolean
}) {
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const [expandedBody, setExpandedBody] = useState(false)
  const [writing, setWriting] = useState(false)
  const [creatingTheme, setCreatingTheme] = useState(false)
  const [notice, setNotice] = useState('')
  const [entryOpen, setEntryOpen] = useState(false)
  const historical = Boolean(update.superseded || update.withdrawn)
  const citationCorrected = update.change === 'citation_corrected'
  const correction = citationCorrected ? update.citation_correction : undefined
  const organized = update.change === 'organized'
  const statusLabel = updateStatusLabel(update)
  const reference = { ...update.reference, research_update_id: update.update_id }
  const sourceVersion = reference.pm_note_id && reference.pm_note_revision ? `pm:${reference.pm_note_id}:${reference.pm_note_revision}` : reference.theme_version_id || reference.notebook_version_id || reference.investment_view_version_id
  const themeLead = update.kind === 'theme' ? (update.details?.find(detail => detail.label === '最新进展' && detail.text)?.text || update.details?.find(detail => detail.label === '当前认识' && detail.text)?.text) : undefined
  const displayBody = themeLead || update.body
  const body = <>
    {displayBody && (!compact || !themeLead) && <p className={`research-update-body${!readable && !compact && !expandedBody && displayBody.length > 220 ? ' research-update-excerpt' : ''}`} translate="no">{displayBody}</p>}
    {!readable && !compact && displayBody.length > 220 && <button className="sector-event-ask research-update-expand" type="button" aria-expanded={expandedBody} onClick={() => setExpandedBody(value => !value)}>{expandedBody ? '收起分析' : '展开分析'}</button>}
    {Boolean(update.risk_channels?.length) && <p className="sector-research-note">影响渠道：<span translate="no">{update.risk_channels!.join("、")}</span></p>}
    {update.impact_analysis && <p className="research-update-impact"><strong>影响分析</strong><span translate="no">{update.impact_analysis}</span></p>}
    {update.action_condition && <p className="research-update-impact"><strong>应对条件</strong><span translate="no">{update.action_condition}</span></p>}
    {update.next_check && <p className="research-update-next"><strong>{citationCorrected ? '原下一步观察' : '下一步观察'}</strong> <span translate="no">{update.next_check}</span></p>}
    {update.kind === 'question' && update.tracking_status && <p className="sector-research-note"><strong>当时跟踪安排</strong> <span>{questionTrackingLabels[update.tracking_status]}</span>{update.tracking_reason && <> · <span translate="no">{update.tracking_reason}</span></>}</p>}
    {update.kind === 'lesson' && update.status === 'withdrawn' && update.withdrawal_reason && <p className="sector-research-note" translate="no">{update.withdrawal_reason}</p>}
    {update.scheduled_at && <p className="sector-event-dates"><span>预定时间 <time dateTime={update.scheduled_at}>{dateLabel(update.scheduled_at)}</time></span></p>}
    {(update.occurred_at || update.published_at) && <p className="sector-event-dates">
      {update.occurred_at && <span>事件发生 <time dateTime={update.occurred_at}>{dateLabel(update.occurred_at)}</time></span>}
      {update.published_at && <span>信息发布 <time dateTime={update.published_at}>{dateLabel(update.published_at)}</time></span>}
    </p>}
    {readable && update.details?.filter(detail => detail.text && !['最新进展', '当前认识'].includes(detail.label) && detail.text !== update.body && detail.text !== update.next_check).map((detail, index) => <div className="research-update-detail" key={index}><h5>{detail.label}</h5><p translate="no">{detail.text}</p></div>)}
    {Boolean(update.sources.length || (!readable && update.details?.length)) && (readable ? <ResearchReadingAside label="分析与研究依据" title={update.title}><SourceList instrumentId={reference.instrument_id} versionId={sourceVersion} sources={update.sources.map((source, index) => ({ ...source, source_id: source.source_id || `${update.update_id}:${index}` }))} /></ResearchReadingAside> : <details className="research-update-evidence"><summary>分析与研究依据</summary>
      {update.details?.filter(detail => detail.text).map((detail, index) => <div key={`${detail.label}:${index}`}><strong>{detail.label}</strong><p translate="no">{detail.text}</p></div>)}
      <SourceList instrumentId={reference.instrument_id} versionId={sourceVersion} sources={update.sources.map((source, index) => ({ ...source, source_id: source.source_id || `${update.update_id}:${index}` }))} />
    </details>)}
    <div className="research-update-actions">
      {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请${historical ? '按当时信息复核这条历史研究更新' : '继续分析这条研究更新'}“${update.title}”。读取它对应的原始证据、相关事件及主题，区分新增事实、机制判断和市场定价；核实后说明是否需要改变认识或继续跟进。${historical ? '此版本已被修订或撤回，请区分当时判断与当前结论。' : ''}`, reference)}>{historical ? '讨论当时判断' : '追问这条更新'}</button>}
      {canWrite && !historical && <>
        <button type="button" className="sector-event-ask" onClick={() => setWriting(value => !value)}>写投资观点</button>
        {!inTheme && update.theme_ids.length === 0 && update.kind === 'event' && <button type="button" className="sector-event-ask" onClick={() => setCreatingTheme(value => !value)}>转为跟踪主题</button>}
      </>}
    </div>
    {writing && <ResearchOpinionComposer instrumentId={reference.instrument_id} title={`关于${update.title}的观点`} context={{
      research_update_id: update.update_id, theme_id: reference.theme_id || update.theme_ids[0], event_case_id: reference.event_case_id, event_version_id: reference.event_version_id,
      theme_version_id: reference.theme_version_id, notebook_version_id: reference.notebook_version_id, investment_view_version_id: reference.investment_view_version_id,
      source_ids: update.sources.flatMap(source => source.source_id ? [source.source_id] : []), background: `${update.title}\n${displayBody}\n研究记录时间：${update.recorded_at}`,
    }} onCancel={() => setWriting(false)} onSaved={() => { setWriting(false); setNotice('投资观点已保存。') }} />}
    {creatingTheme && <ResearchThemeComposer instrumentId={reference.instrument_id} title={update.title} kind="event" background={`${update.title}\n${update.body}\n研究记录时间：${update.recorded_at}`} reference={{ research_update_id: update.update_id, event_case_id: reference.event_case_id, event_version_id: reference.event_version_id, source_ids: update.sources.flatMap(source => source.source_id ? [source.source_id] : []) }} onCancel={() => setCreatingTheme(false)} onSaved={(action, message) => { setCreatingTheme(false); setNotice(message || (action === 'linked' ? '已关联主题。' : '主题已建立。')) }} />}
    {notice && <p role="status">{notice}</p>}
  </>
  return <article className={`research-update-card${historical ? ' research-update-historical' : ''}${readable ? ' research-update-readable' : ''}`} data-update-id={update.update_id}>
    <div className="research-update-meta"><span>{updateKindLabels[update.kind]}{update.analysis_depth === 'brief' && <> · <span>简讯</span></>}</span>
      <span translate={update.author === '研究员' || update.author_role === 'system' || !update.author ? undefined : 'no'}>{update.author || (update.author_role === 'researcher' ? '研究员' : '未标注作者')}</span>
      <span>{citationCorrected ? '引用修正' : organized ? '纳入主题' : update.author_role === 'system' ? '系统记录修订' : update.author_role === 'user' ? '人工判断' : '研究员记录'}</span>
      <span>{citationCorrected && <>修正时间 </>}<time dateTime={update.recorded_at}>{dateLabel(update.recorded_at)}</time></span>
      {update.information_type === 'rumor' && <span className="sector-research-limitation">传闻 · 待证实</span>}
      {update.information_type === 'opinion' && <span>来源观点</span>}
      {update.direction && <span className={`research-impact-${update.direction}`}>{{ risk: '风险', opportunity: '机会', uncertain: '影响待定' }[update.direction]}</span>}
      {update.urgency && update.urgency !== 'monitor' && <span className="research-impact-risk">{update.urgency === 'immediate' ? '需立即复核' : '近期复核'}</span>}
      {update.impact_level === 'major' && <span>重大影响</span>}
      {statusLabel && <span>{statusLabel}</span>}
      {!historical && update.follow_up && <span>{followUpLabels[update.follow_up]}</span>}
      {historical && <strong>{update.withdrawn ? '已撤回 · 仅供追溯' : '已修订 · 当时版本'}</strong>}
    </div>
    <h4 translate="no">{update.title}</h4>
    {compact && themeLead && <p className="research-update-body" translate="no">{themeLead}</p>}
    {organized && update.organization_revision?.original_recorded_at && <p className="sector-research-note">原判断时间 <time dateTime={update.organization_revision.original_recorded_at}>{dateLabel(update.organization_revision.original_recorded_at)}</time></p>}
    {correction && <>
      <p className="sector-research-note">原判断时间 <time dateTime={correction.original_recorded_at}>{dateLabel(correction.original_recorded_at)}</time></p>
      {correction.reason && <p className="sector-research-note"><strong>修正说明</strong> <span translate="no">{correction.reason}</span></p>}
    </>}
    {update.withdrawn && update.withdrawal_reason && <p className="sector-research-limitation" translate="no">{update.withdrawal_reason}</p>}
    {update.withdrawn_at && <p className="sector-research-note">撤回时间 <time dateTime={update.withdrawn_at}>{dateLabel(update.withdrawn_at)}</time></p>}
    {!inTheme && update.theme_ids.length > 0 && <div className="research-update-themes">{update.theme_ids.map(id => <a key={id} href={`#research-theme-${encodeURIComponent(id)}`} onClick={() => {
      let ancestor = document.getElementById(`research-theme-${encodeURIComponent(id)}`)?.parentElement
      while (ancestor) { if (ancestor instanceof HTMLDetailsElement) ancestor.open = true; ancestor = ancestor.parentElement }
    }} translate={themeNames[id] ? 'no' : undefined}>{themeNames[id] || '关联主题'}</a>)}</div>}
    {!readable && (historical || compact) ? <details className="research-update-old" onToggle={event => setEntryOpen(event.currentTarget.open)}><summary>{historical ? '查看当时记录' : '阅读这条记录'}</summary>{(!compact || entryOpen) && body}</details> : body}
  </article>
}
