import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { announceResearchPublication, RESEARCH_UPDATED } from '../lib/researchUpdates'
import { createResearchTheme, getResearchThemes, updateResearchTheme, type AskResearchAssistant, type ResearchTheme, type ResearchThemeInput, type ResearchThemeProgress, type ResearchThemesResponse } from '../lib/researchDossierApi'
import { InvestmentOpinionContext } from './InvestmentOpinionTimeline'
import './research-themes.css'
import { fetchJson } from '../lib/api'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'

const statusLabel = { active: '持续关注', paused: '已暂停', closed: '已结束' }
type ThemeDraft = ResearchThemeInput & { theme_id?: string }
const recordedDate = (value: string) => value.replace('T', ' ').slice(0, 16)

function Progress({ progress, sources }: { progress: ResearchThemeProgress; sources?: (ids: string[]) => ReactNode }) {
  return <article className="research-theme-progress"><p className="sector-research-note">研究员 · <time dateTime={progress.recorded_at}>{recordedDate(progress.recorded_at)}</time></p>
    {progress.assessment && <p translate="no">{progress.assessment}</p>}
    {progress.next_check && <p className="research-notebook-next"><strong>下一步核实</strong> <span translate="no">{progress.next_check}</span></p>}
    {Boolean(progress.source_ids.length) && sources && <details><summary>研究依据</summary>{sources(progress.source_ids)}</details>}
  </article>
}

export default function ResearchThemesPanel({ instrumentId, reviewRunId, reviewStatus, onAskAssistant, sources }: { instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant; sources?: (ids: string[]) => ReactNode }) {
  const [data, setData] = useState<ResearchThemesResponse | null>(null)
  const [draft, setDraft] = useState<ThemeDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [members, setMembers] = useState<Array<{ user_id: string; display_name: string; active: boolean }>>([])
  const canWrite = Boolean(data && data.identity.team_role !== 'reader')
  useEffect(() => { void fetchJson<typeof members>('/api/research/members').then(setMembers).catch(() => setMembers([])) }, [])
  useEffect(() => { setData(null); setDraft(null); setNotice(null) }, [instrumentId])
  useEffect(() => {
    const updated = (event: Event) => {
      if ((event as CustomEvent<string[]>).detail.includes(instrumentId)) setRefresh(value => value + 1)
    }
    window.addEventListener(RESEARCH_UPDATED, updated)
    return () => window.removeEventListener(RESEARCH_UPDATED, updated)
  }, [instrumentId])
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    void getResearchThemes(instrumentId, controller.signal).then(value => { if (!controller.signal.aborted) setData(value) })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '关注主题读取失败') })
    return () => controller.abort()
  }, [instrumentId, reviewRunId, reviewStatus, refresh])

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft) return
    setSaving(true); setError(''); setNotice(null)
    try {
      const input: ResearchThemeInput = { title: draft.title.trim(), question: draft.question.trim(), background: draft.background?.trim() || '', responsible_user_id: draft.responsible_user_id ?? null }
      if (draft.theme_id) await updateResearchTheme(instrumentId, draft.theme_id, input)
      else await createResearchTheme(instrumentId, input)
      setDraft(null)
      setNotice({ id: Date.now(), message: '关注主题已保存。后续研究会持续跟进，有实质变化时更新。', tone: 'success' })
      announceResearchPublication([instrumentId])
    } catch (reason) { setError(reason instanceof Error ? reason.message : '关注主题保存失败') }
    finally { setSaving(false) }
  }

  async function changeStatus(theme: ResearchTheme, status: ResearchTheme['status']) {
    setSaving(true); setError(''); setNotice(null)
    try {
      await updateResearchTheme(instrumentId, theme.theme_id, { status })
      setNotice({ id: Date.now(), message: `“${theme.title}”${statusLabel[status]}。既有观点和研究进展继续保留。`, tone: 'success' })
      announceResearchPublication([instrumentId])
    } catch (reason) { setError(reason instanceof Error ? reason.message : '主题状态更新失败') }
    finally { setSaving(false) }
  }

  function themeCard(theme: ResearchTheme) {
    const progress = [...theme.research_progress].sort((a, b) => b.recorded_at.localeCompare(a.recorded_at))
    const notes = [...theme.notes].sort((a, b) => b.created_at.localeCompare(a.created_at))
    return <article key={theme.theme_id} className="research-theme-record">
      <div className="research-notebook-question-heading"><h4 translate="no">{theme.title}</h4><span>{statusLabel[theme.status]}</span></div>
      <p className="sector-research-note">{theme.author || '未标注作者'}提出 · <time dateTime={theme.created_at}>{recordedDate(theme.created_at)}</time></p>
      {theme.responsible_user_id && <p className="sector-research-note">负责人：{members.find(member => member.user_id === theme.responsible_user_id)?.display_name || (theme.responsible_user_id === theme.author_user_id ? theme.author : '已指定团队成员')}</p>}
      <p translate="no">{theme.question}</p>
      {theme.background && <details><summary>主题背景</summary><p translate="no">{theme.background}</p></details>}
      {progress[0] && <Progress progress={progress[0]} sources={sources} />}
      {progress.length > 1 && <details><summary>以往研究进展 · {progress.length - 1}</summary>{progress.slice(1).map((entry, index) => <Progress key={`${entry.run_id}:${index}`} progress={entry} sources={sources} />)}</details>}
      {notes.length > 0 && <details className="research-theme-opinions"><summary>投资经理的观点与复盘 · {notes.length}</summary>
        {notes.map(note => <article key={note.note_id}><p className="sector-research-note">{note.author || '未标注作者'} · 观点日期 {note.note_date} · 实际记录 {recordedDate(note.created_at)}</p>
          <h5 translate="no">{note.title}</h5><p translate="no">{note.body || note.summary}</p><InvestmentOpinionContext note={note} />
          {onAskAssistant && <button className="sector-event-ask" type="button" onClick={() => onAskAssistant(`请复核这个主题下的投资经理观点“${note.title}”。对照原始判断、后续结果和机制证据，保留分歧，不要改写我的原始观点。`, { instrument_id: instrumentId, theme_id: theme.theme_id, pm_note_id: note.note_id, pm_note_revision: note.revision_number })}>讨论这条观点</button>}
        </article>)}
      </details>}
      <div className="research-theme-actions">
        {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请围绕持续关注主题“${theme.title}”继续研究和讨论，读取主题下的投资经理观点与研究员进展，区分双方判断。我的新判断只有在我明确要求保存时才记为投资观点。`, { instrument_id: instrumentId, theme_id: theme.theme_id })}>讨论这个主题</button>}
        {canWrite && <>
          <button type="button" disabled={saving || Boolean(draft)} onClick={() => { setDraft({ theme_id: theme.theme_id, title: theme.title, question: theme.question, background: theme.background, responsible_user_id: theme.responsible_user_id }); setNotice(null); setError('') }}>编辑主题</button>
          {theme.status === 'active' ? <button type="button" disabled={saving} onClick={() => void changeStatus(theme, 'paused')}>暂停关注</button> : <button type="button" disabled={saving} onClick={() => void changeStatus(theme, 'active')}>恢复关注</button>}
          {theme.status !== 'closed' && <button type="button" disabled={saving} onClick={() => void changeStatus(theme, 'closed')}>结束主题</button>}
        </>}
      </div>
    </article>
  }

  const active = data?.themes.filter(theme => theme.status === 'active') || []
  const inactive = data?.themes.filter(theme => theme.status !== 'active') || []
  return <section className="research-themes" aria-label="持续关注主题">
    <div className="research-dossier-section-heading"><h3>持续关注主题</h3><button type="button" disabled={!canWrite || saving || Boolean(draft)} onClick={() => { setDraft({ title: '', question: '', background: '', responsible_user_id: data?.identity.user_id }); setError(''); setNotice(null) }}>建立主题</button></div>
    {error && <p role="alert">{error}</p>}<NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
    {draft && <form className="research-theme-editor" onSubmit={event => void save(event)}>
      <label><span>主题名称</span><input required disabled={saving} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
      <label><span>持续关注的问题</span><textarea required rows={3} disabled={saving} value={draft.question} onChange={event => setDraft({ ...draft, question: event.target.value })} /></label>
      <label><span>背景（可选）</span><textarea rows={2} disabled={saving} value={draft.background || ''} onChange={event => setDraft({ ...draft, background: event.target.value })} /></label>
      {data?.identity && <p className="sector-research-note">团队共享 · 记录人：{data.identity.display_name}</p>}
      <label><span>负责人</span><select disabled={saving} value={draft.responsible_user_id || ''} onChange={event => setDraft({ ...draft, responsible_user_id: event.target.value || null })}><option value="">暂不指定</option>{members.filter(member => member.active).map(member => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}</select></label>
      <div className="research-theme-actions"><button type="button" disabled={saving} onClick={() => setDraft(null)}>取消</button><button type="submit" disabled={saving}>{saving ? '保存中…' : '保存主题'}</button></div>
    </form>}
    {active.map(themeCard)}
    {data && !active.length && <p className="sector-research-note">尚无正在关注的主题。可以在这里建立，也可以请研究助手记录值得持续研究的问题。</p>}
    {inactive.length > 0 && <details className="research-theme-archive"><summary>暂停或结束的主题 · {inactive.length}</summary>{inactive.map(themeCard)}</details>}
  </section>
}
