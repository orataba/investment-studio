import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { announceResearchPublication, RESEARCH_UPDATED } from '../lib/researchUpdates'
import { createResearchTheme, getResearchThemes, updateResearchTheme, type AskResearchAssistant, type ResearchTheme, type ResearchThemeInput, type ResearchThemesResponse } from '../lib/researchDossierApi'
import ResearchUpdateCard from './ResearchUpdateCard'
import { dateLabel } from './ResearchEvidence'
import './research-themes.css'
import { fetchJson } from '../lib/api'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import ResearchFollowupClocks from './ResearchFollowupClocks'

const statusLabel = { active: '持续关注', paused: '已暂停', closed: '已结束' }
type ThemeDraft = ResearchThemeInput & { theme_id?: string }
export default function ResearchThemesPanel({ instrumentId, reviewRunId, reviewStatus, onAskAssistant, sources, onThemesLoaded }: { instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant; sources?: (ids: string[]) => ReactNode; onThemesLoaded?: (themes: ResearchTheme[]) => void }) {
  const [data, setData] = useState<ResearchThemesResponse | null>(null)
  const [draft, setDraft] = useState<ThemeDraft | null>(null)
  const [closing, setClosing] = useState<{ themeId: string; reason: string } | null>(null)
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
    void getResearchThemes(instrumentId, controller.signal).then(value => { if (!controller.signal.aborted) { setData(value); onThemesLoaded?.(value.themes) } })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '关注主题读取失败') })
    return () => controller.abort()
  }, [instrumentId, reviewRunId, reviewStatus, refresh, onThemesLoaded])

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
      setClosing(null)
      announceResearchPublication([instrumentId])
    } catch (reason) { setError(reason instanceof Error ? reason.message : '关注主题保存失败') }
    finally { setSaving(false) }
  }

  async function changeStatus(theme: ResearchTheme, status: ResearchTheme['status']) {
    setSaving(true); setError(''); setNotice(null)
    try {
      await updateResearchTheme(instrumentId, theme.theme_id, { status, ...(status === 'closed' ? { close_reason: closing?.reason.trim() } : {}) })
      setNotice({ id: Date.now(), message: `“${theme.title}”${statusLabel[status]}。既有观点和研究进展继续保留。`, tone: 'success' })
      setClosing(null)
      announceResearchPublication([instrumentId])
    } catch (reason) { setError(reason instanceof Error ? reason.message : '主题状态更新失败') }
    finally { setSaving(false) }
  }

  function themeCard(theme: ResearchTheme) {
    const updates = [...(theme.updates || [])].sort((a, b) => Date.parse(b.recorded_at) - Date.parse(a.recorded_at))
    const latest = updates.find(update => !update.superseded && !update.withdrawn && update.kind !== 'theme')
    const current = theme.current_assessment
    return <article id={`research-theme-${encodeURIComponent(theme.theme_id)}`} key={theme.theme_id} className="research-theme-record">
      <div className="research-notebook-question-heading"><h4 translate="no">{theme.title}</h4><span>{statusLabel[theme.status]}</span></div>
      <p className="research-theme-question" translate="no">{theme.question}</p>
      <p className="sector-research-note"><span translate="no">{theme.author || '未标注作者'}</span> · {theme.origin === 'researcher' ? '研究员提出' : '人工建立'} · <time dateTime={theme.created_at}>{dateLabel(theme.created_at)}</time></p>
      {current?.assessment ? <p className="research-theme-assessment"><strong>当前判断</strong> <span translate="no">{current.assessment}</span></p> : <p className="sector-research-note">尚待形成研究判断。</p>}
      {latest && <p className="research-theme-latest"><strong>最近研究记录</strong> <span translate="no">{latest.title}</span></p>}
      {current?.next_check && <p className="research-notebook-next"><strong>下一步观察</strong> <span translate="no">{current.next_check}</span></p>}
      <ResearchFollowupClocks changedAt={theme.last_changed_at} reviewedAt={theme.last_reviewed_at} reviewStatus={theme.last_review_status} />
      {theme.close_reason && <p className="sector-research-note"><strong>结束原因</strong> <span translate="no">{theme.close_reason}</span></p>}
      <details className="research-theme-thread"><summary>主题研究时间线 · {updates.length}</summary>
        {updates.length ? updates.map(update => <ResearchUpdateCard key={update.update_id} update={update} onAskAssistant={onAskAssistant} inTheme />) : <p className="sector-research-note">尚无主题更新。后续事件、判断和复盘会保留在这里。</p>}
      </details>
      <details className="research-theme-settings"><summary>背景与主题管理</summary>
        {theme.background && <p translate="no">{theme.background}</p>}
        {theme.responsible_user_id && <p className="sector-research-note">负责人：<span translate="no">{members.find(member => member.user_id === theme.responsible_user_id)?.display_name || (theme.responsible_user_id === theme.author_user_id ? theme.author : '已指定团队成员')}</span></p>}
        {Boolean(current?.source_ids.length) && sources && <details><summary>当前判断依据</summary>{sources(current!.source_ids)}</details>}
        {canWrite && <div className="research-theme-actions">
          <button type="button" disabled={saving || Boolean(draft)} onClick={() => { setDraft({ theme_id: theme.theme_id, title: theme.title, question: theme.question, background: theme.background, responsible_user_id: theme.responsible_user_id }); setNotice(null); setError('') }}>编辑主题</button>
          {theme.status === 'active' ? <button type="button" disabled={saving} onClick={() => void changeStatus(theme, 'paused')}>暂停关注</button> : <button type="button" disabled={saving} onClick={() => void changeStatus(theme, 'active')}>恢复关注</button>}
          {theme.status !== 'closed' && <button type="button" disabled={saving} onClick={() => setClosing({ themeId: theme.theme_id, reason: '' })}>结束主题</button>}
        </div>}
        {closing?.themeId === theme.theme_id && <form className="research-theme-editor" onSubmit={event => { event.preventDefault(); void changeStatus(theme, 'closed') }}>
          <label>结束原因<textarea required value={closing.reason} onChange={event => setClosing({ ...closing, reason: event.target.value })} /></label>
          <div className="research-theme-actions"><button type="submit" disabled={saving || !closing.reason.trim()}>保存并结束</button><button type="button" disabled={saving} onClick={() => setClosing(null)}>取消</button></div>
        </form>}
      </details>
      {onAskAssistant && <div className="research-theme-actions"><button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请围绕持续关注主题“${theme.title}”继续研究和讨论，读取主题下的投资经理观点与研究员进展，区分双方判断。核实新证据，复核此前判断与观察条件；有价值时补充复盘或修订经验。我的新判断只有在我明确要求保存时才记为投资观点。`, { instrument_id: instrumentId, theme_id: theme.theme_id })}>讨论这个主题</button></div>}
    </article>
  }

  const active = data?.themes.filter(theme => theme.status === 'active') || []
  const inactive = data?.themes.filter(theme => theme.status !== 'active') || []
  return <section className="research-themes" aria-label="长期主题">
    <div className="research-dossier-section-heading"><h4>长期主题{active.length > 0 && <span> · {active.length}</span>}</h4><button type="button" disabled={!canWrite || saving || Boolean(draft)} onClick={() => { setDraft({ title: '', question: '', background: '', responsible_user_id: data?.identity.user_id }); setError(''); setNotice(null) }}>建立主题</button></div>
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
    {data && !active.length && <p className="sector-research-note">暂无正在跟踪的长期主题。独立事项见下方。</p>}
    {inactive.length > 0 && <details className="research-theme-archive"><summary>暂停或结束的主题 · {inactive.length}</summary>{inactive.map(themeCard)}</details>}
  </section>
}
