import { useState } from 'react'
import { useStudioAccount } from './AccountBoundary'
import type { AskResearchAssistant, ResearchUpdate } from '../lib/researchDossierApi'
import ResearchOpinionComposer from './ResearchOpinionComposer'
import ResearchThemeComposer from './ResearchThemeComposer'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'

/** Actions always bind to the exact published record that the reader is viewing. */
export default function ResearchEntryActions({ update, onAskAssistant, inTheme = false }: {
  update: ResearchUpdate; onAskAssistant?: AskResearchAssistant; inTheme?: boolean
}) {
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const [writing, setWriting] = useState(false)
  const [creatingTheme, setCreatingTheme] = useState(false)
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const historical = Boolean(update.superseded || update.withdrawn)
  const reference = { ...update.reference, research_update_id: update.update_id }
  const displayBody = (update.kind === 'theme' && (update.details?.find(detail => detail.label === '最新进展' && detail.text)?.text || update.details?.find(detail => detail.label === '当前认识' && detail.text)?.text)) || update.body
  return <>
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
    }} onCancel={() => setWriting(false)} onSaved={() => { setWriting(false); setNotice({ id: Date.now(), message: '投资观点已保存。', tone: 'success' }) }} />}
    {creatingTheme && <ResearchThemeComposer instrumentId={reference.instrument_id} title={update.title} kind="event" background={`${update.title}\n${update.body}\n研究记录时间：${update.recorded_at}`} reference={{ research_update_id: update.update_id, event_case_id: reference.event_case_id, event_version_id: reference.event_version_id, source_ids: update.sources.flatMap(source => source.source_id ? [source.source_id] : []) }} onCancel={() => setCreatingTheme(false)} onSaved={(action, message) => { setCreatingTheme(false); setNotice({ id: Date.now(), message: message || (action === 'linked' ? '已关联主题。' : '主题已建立。'), tone: 'success' }) }} />}
    <NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
  </>
}
