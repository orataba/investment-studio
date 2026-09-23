import { useCallback, useState } from 'react'
import type { AskResearchAssistant, CurrentResearchFollowup, ResearchTheme, ResearchUpdate } from '../lib/researchDossierApi'
import { useResearchActivity } from '../lib/useResearchActivity'
import ResearchThemesPanel from './ResearchThemesPanel'
import ResearchActivityPanel from './ResearchActivityPanel'
import ResearchFollowupClocks from './ResearchFollowupClocks'
import ResearchQuestionTracking from './ResearchQuestionTracking'

const kindLabels = { event: '事件跟进', question: '研究问题', forecast: '预测验证', schedule: '观察日程' }

function CurrentFollowup({ followup, onAskAssistant }: { followup: CurrentResearchFollowup; onAskAssistant?: AskResearchAssistant }) {
  const [expanded, setExpanded] = useState(false)
  const ask = (update: ResearchUpdate) => onAskAssistant?.(
    `请继续跟进“${update.title}”，读取当前事项及其关联研究，核实最新证据，对照原判断和下一步观察条件。说明仍有哪些问题未解决、判断是否改变；证据不足时明确说明。`,
    { ...update.reference, research_update_id: update.update_id },
  )
  return <article className="research-current-followup" data-followup-id={followup.followup_id}>
    <div className="research-notebook-question-heading"><h4 translate="no">{followup.title}</h4><span>{kindLabels[followup.kind]}</span></div>
    {followup.assessment ? <p className={`research-theme-assessment${!expanded && followup.assessment.length > 220 ? ' research-update-excerpt' : ''}`}><strong>当前判断</strong> <span translate="no">{followup.assessment}</span></p> : <p className="sector-research-note">尚待形成研究判断。</p>}
    {followup.assessment.length > 220 && <button type="button" className="sector-event-ask" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? '收起分析' : '展开分析'}</button>}
    {followup.next_check && <p className="research-notebook-next"><strong>下一步观察</strong> <span translate="no">{followup.next_check}</span></p>}
    <ResearchFollowupClocks changedAt={followup.last_changed_at} reviewedAt={followup.last_reviewed_at} reviewStatus={followup.last_review_status} />
    {followup.related_updates.length > 0 && <details className="research-current-related"><summary>关联问题与验证 · {followup.related_updates.length}</summary>
      {followup.related_updates.map(update => <div key={update.update_id}>
        <h5 translate="no">{update.title}</h5><p translate="no">{update.body}</p>
        {update.next_check && <p className="research-notebook-next"><strong>下一步观察</strong> <span translate="no">{update.next_check}</span></p>}
        {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => ask(update)}>继续核查</button>}
      </div>)}
    </details>}
    {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => ask(followup.latest_update)}>继续跟进此事项</button>}
    <ResearchQuestionTracking update={followup.latest_update} onAskAssistant={onAskAssistant} />
  </article>
}

export default function ResearchTrackingPanel({ instrumentId, reviewRunId, reviewStatus, onAskAssistant, readOnly = false }: {
  instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant; readOnly?: boolean
}) {
  const state = useResearchActivity(instrumentId, reviewRunId, reviewStatus)
  const [themeNames, setThemeNames] = useState<Record<string, string>>({})
  const onThemesLoaded = useCallback((themes: ResearchTheme[]) => setThemeNames(Object.fromEntries(themes.map(theme => [theme.theme_id, theme.title]))), [])
  const followups = state.data?.current_followups || []
  return <>
    <section className="research-current-tracking" aria-label="当前跟踪">
      <h3>当前跟踪</h3>
      <p className="sector-research-note">更新此标的研究时，一并复核当前跟踪。长期主题积累判断；独立事项解决后退出，记录仍保留。</p>
      <ResearchThemesPanel {...{ instrumentId, reviewRunId, reviewStatus, onAskAssistant, onThemesLoaded, readOnly }} />
      <section className="research-current-followups" aria-label="独立跟进事项">
        <h4>独立跟进事项{followups.length > 0 && <span> · {followups.length}</span>}</h4>
        {state.error && <p role="alert">当前跟进暂时无法读取：{state.error}</p>}
        {!state.data && !state.error && <p className="sector-research-note" role="status">正在读取当前跟进…</p>}
        {followups.map(followup => <CurrentFollowup key={followup.followup_id} {...{ followup, onAskAssistant }} />)}
        {state.data && !followups.length && <p className="sector-research-note">当前没有正在跟踪的独立事项。暂停或结束的事项保留在下方研究动态。</p>}
      </section>
    </section>
    <ResearchActivityPanel {...{ instrumentId, onAskAssistant, themeNames, state }} />
  </>
}
