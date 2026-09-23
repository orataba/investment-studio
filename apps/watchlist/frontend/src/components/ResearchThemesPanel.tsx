import { useEffect, useRef, useState, type FormEvent } from 'react'
import { announceResearchPublication, RESEARCH_UPDATED } from '../lib/researchUpdates'
import { createResearchTheme, getResearchThemes, updateResearchTheme, type AskResearchAssistant, type ResearchTheme, type ResearchThemeInput, type ResearchThemeKind, type ResearchThemesResponse } from '../lib/researchDossierApi'
import ResearchUpdateCard from './ResearchUpdateCard'
import { dateLabel, SourceList } from './ResearchEvidence'
import { SavedFigure } from './ResearchModules'
import ResearchOpinionComposer from './ResearchOpinionComposer'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import './research-themes.css'

const statusLabel = { active: '持续关注', paused: '已暂停', closed: '已结束' }
export const themeKindLabels: Record<ResearchThemeKind, string> = { fundamental: '基本面', event: '事件', quantitative: '量化', valuation: '估值', risk: '风险', other: '综合' }
type ThemeDraft = ResearchThemeInput & { theme_id?: string }

type ThemeRecordProps = {
  theme: ResearchTheme; instrumentId: string; canWrite: boolean; saving: boolean
  onAskAssistant?: AskResearchAssistant; onEdit: () => void; onStatus: (status: ResearchTheme['status'], reason?: string) => void
}
function ThemeRecord({ theme, instrumentId, canWrite, saving, onAskAssistant, onEdit, onStatus }: ThemeRecordProps) {
  const [closing, setClosing] = useState(false)
  const [closeReason, setCloseReason] = useState('')
  const [writing, setWriting] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [notice, setNotice] = useState('')
  const updates = [...(theme.updates || [])].sort((a, b) => Date.parse(b.recorded_at) - Date.parse(a.recorded_at))
  const evidence = theme.sources || []
  const figures = evidence.filter(source => theme.figure_source_ids?.includes(source.source_id))
  const versionId = theme.source_version_id
  const reference = { instrument_id: instrumentId, theme_id: theme.theme_id, theme_version_id: versionId, source_ids: evidence.map(source => source.source_id) }
  const background = `${theme.title}\n核心问题：${theme.question}\n当前认识：${theme.synthesis || '尚未形成'}\n最近进展：${theme.latest_development || ''}\n记录时间：${theme.last_changed_at || theme.updated_at}`
  return <article id={`research-theme-${encodeURIComponent(theme.theme_id)}`} className="research-theme-record">
    <header className="research-theme-heading"><div><span className="research-theme-kind">{themeKindLabels[theme.kind || 'other']}</span><h3 translate="no">{theme.title}</h3><span className={theme.priority === 'core' ? 'research-theme-priority' : 'sector-research-note'}>{theme.status !== 'active' ? statusLabel[theme.status] : theme.priority === 'core' ? '核心' : '重要'}</span>{theme.pinned && <span className="research-theme-kind">已固定</span>}</div><div className="research-theme-actions">
      {onAskAssistant && <button type="button" onClick={() => onAskAssistant(`请继续研究主题“${theme.title}”。\n${background}\n先读取关联原始证据和主题历史，检查反证、定价含义与下一观察；区分研究员和投资经理的判断。`, reference)}>讨论主题</button>}
      {canWrite && <button type="button" onClick={() => setWriting(value => !value)}>写投资观点</button>}
    </div></header>
    {theme.synthesis ? <p className="research-theme-synthesis research-theme-excerpt" translate="no">{theme.synthesis}</p> : <p className="sector-research-note">{theme.research_status === 'running' ? '研究员正在建立主题基线。' : theme.research_status === 'queued' ? '已排队，等待研究员建立基线。' : theme.baseline_status === 'pending' ? '待补充研究基线。' : '尚未形成主题认识。'}</p>}
    <div className="research-theme-observations">{theme.latest_development && <p className="research-theme-development research-theme-excerpt"><strong>最近变化</strong><span translate="no">{theme.latest_development}</span></p>}
    {theme.next_check && <p className="research-theme-next research-theme-excerpt"><strong>下一观察</strong><span translate="no">{theme.next_check}</span></p>}
    </div>
    <details className="research-theme-expanded" onToggle={event => setExpanded(event.currentTarget.open)}><summary>分析与时间线 <span>· {updates.length}</span></summary>
    {expanded && <>
    {theme.priority_reason && <p className="research-theme-reason"><strong>为什么重要</strong> <span translate="no">{theme.priority_reason}</span></p>}
    <div className="research-theme-clock"><span>最近记录 {dateLabel(theme.last_changed_at || theme.updated_at)}</span>{theme.last_reviewed_at && <span>最近检查 {dateLabel(theme.last_reviewed_at)}</span>}</div>
    <section className="research-theme-analysis" aria-label="完整主题分析">
      {theme.question && <p><strong>核心问题</strong> <span translate="no">{theme.question}</span></p>}
      {theme.synthesis && <p translate="no">{theme.synthesis}</p>}
      {theme.background && <p className="sector-research-note" translate="no">{theme.background}</p>}
      {figures.map(source => <SavedFigure key={`${source.source_id}:${versionId}`} {...{ instrumentId, source, onAskAssistant }} themeVersionId={versionId} themeId={theme.theme_id} />)}
      {Boolean(theme.sources?.length) && <SourceList instrumentId={instrumentId} versionId={versionId} sources={theme.sources!} />}
    </section>
    <section className="research-theme-thread" aria-label="主题时间线"><h4>{`主题时间线 · ${updates.length}`}</h4>
      {updates.length ? <ol className="research-timeline">{updates.map(update => <li key={update.update_id}><ResearchUpdateCard update={update} onAskAssistant={onAskAssistant} inTheme /></li>)}</ol> : <p className="sector-research-note">尚无主题更新。</p>}
    </section>
    {canWrite && <details className="research-theme-settings"><summary>管理主题</summary><p className="sector-research-note">{theme.origin === 'researcher' ? '研究员提出' : '人工建立'} · <span translate="no">{theme.author || '未标注作者'}</span>{theme.close_reason && ` · ${theme.close_reason}`}</p>
      <div className="research-theme-actions"><button type="button" disabled={saving} onClick={onEdit}>编辑主题</button><button type="button" disabled={saving} onClick={() => onStatus(theme.status === 'active' ? 'paused' : 'active')}>{theme.status === 'active' ? '暂停关注' : '恢复关注'}</button>{theme.status !== 'closed' && <button type="button" disabled={saving} onClick={() => setClosing(true)}>结束主题</button>}</div>
      {closing && <form className="research-theme-editor" onSubmit={event => { event.preventDefault(); if (closeReason.trim()) onStatus('closed', closeReason.trim()) }}><label>结束原因<textarea required value={closeReason} onChange={event => setCloseReason(event.target.value)} /></label><div className="research-theme-actions"><button type="submit" disabled={saving || !closeReason.trim()}>保存并结束</button><button type="button" disabled={saving} onClick={() => setClosing(false)}>取消</button></div></form>}
    </details>}
    </>}
    </details>
    {writing && <ResearchOpinionComposer instrumentId={instrumentId} title={`关于${theme.title}的观点`} context={{ theme_id: theme.theme_id, theme_version_id: versionId, background, source_ids: evidence.map(source => source.source_id) }} onCancel={() => setWriting(false)} onSaved={() => { setWriting(false); setNotice('投资观点已保存。') }} />}
    {notice && <p role="status">{notice}</p>}
  </article>
}

export default function ResearchThemesPanel({ instrumentId, reviewRunId, reviewStatus, onAskAssistant, readOnly = false }: {
  instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant; readOnly?: boolean
}) {
  const [data, setData] = useState<ResearchThemesResponse | null>(null)
  const [draft, setDraft] = useState<ThemeDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const [refresh, setRefresh] = useState(0)
  const mutationScope = useRef<object | null>(null)
  const canWrite = Boolean(data && data.identity.team_role !== 'reader' && !readOnly)
  useEffect(() => {
    mutationScope.current = {}
    setData(null); setDraft(null); setNotice(null); setSaving(false)
    return () => { mutationScope.current = null }
  }, [instrumentId])
  useEffect(() => {
    const updated = (event: Event) => { if ((event as CustomEvent<string[]>).detail.includes(instrumentId)) setRefresh(value => value + 1) }
    window.addEventListener(RESEARCH_UPDATED, updated)
    return () => window.removeEventListener(RESEARCH_UPDATED, updated)
  }, [instrumentId])
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    void getResearchThemes(instrumentId, controller.signal).then(value => { if (!controller.signal.aborted) setData(value) })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '主题读取失败') })
    return () => controller.abort()
  }, [instrumentId, reviewRunId, reviewStatus, refresh])
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft || !canWrite || saving) return
    const scope = mutationScope.current
    setSaving(true); setError(''); setNotice(null)
    try {
      const input: ResearchThemeInput = { title: draft.title.trim(), question: draft.question?.trim() || '', background: draft.background?.trim() || '', kind: draft.kind || 'other', priority: draft.priority || 'important', priority_reason: draft.priority_reason?.trim() || '', pinned: draft.pinned || false, responsible_user_id: draft.responsible_user_id ?? data?.identity.user_id }
      const result = draft.theme_id ? await updateResearchTheme(instrumentId, draft.theme_id, input) : await createResearchTheme(instrumentId, input)
      announceResearchPublication([instrumentId])
      if (scope !== mutationScope.current) return
      setNotice({ id: Date.now(), message: result.research_message || (draft.theme_id ? '主题已保存。' : '主题已建立。'), tone: 'success' })
      setDraft(null)
    } catch (reason) { if (scope === mutationScope.current) setError(reason instanceof Error ? reason.message : '主题保存失败') }
    finally { if (scope === mutationScope.current) setSaving(false) }
  }
  async function changeStatus(theme: ResearchTheme, status: ResearchTheme['status'], reason?: string) {
    if (!canWrite || saving) return
    const scope = mutationScope.current
    setSaving(true); setError('')
    try {
      await updateResearchTheme(instrumentId, theme.theme_id, { status, ...(status === 'closed' ? { close_reason: reason } : {}) })
      announceResearchPublication([instrumentId])
      if (scope === mutationScope.current) setNotice({ id: Date.now(), message: `“${theme.title}”${statusLabel[status]}。`, tone: 'success' })
    } catch (reason) { if (scope === mutationScope.current) setError(reason instanceof Error ? reason.message : '主题状态更新失败') }
    finally { if (scope === mutationScope.current) setSaving(false) }
  }
  const active = data?.themes.filter(theme => theme.status === 'active') || []
  const inactive = data?.themes.filter(theme => theme.status !== 'active') || []
  const record = (theme: ResearchTheme) => <ThemeRecord key={theme.theme_id} {...{ theme, instrumentId, canWrite, saving, onAskAssistant }} onEdit={() => setDraft({ ...theme })} onStatus={(status, reason) => void changeStatus(theme, status, reason)} />
  return <section className="research-themes" aria-label="重点主题">
    <div className="research-dossier-section-heading"><div><h2>重点主题{active.length > 0 && <span> · {active.length}</span>}</h2><span className="sector-research-note">{`按投资重要性持续研究 · 最多 ${data?.active_limit || 10} 个`}</span></div>{!readOnly && <button type="button" disabled={!canWrite || saving || Boolean(draft) || active.length >= (data?.active_limit || 10)} onClick={() => { setDraft({ title: '', kind: 'other', priority: 'important', responsible_user_id: data?.identity.user_id }); setError('') }}>建立主题</button>}</div>
    {error && <p role="alert">{error}</p>}<NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
    {!data && !error && <p role="status" className="sector-research-note">Loading</p>}
    {draft && canWrite && <form className="research-theme-editor" onSubmit={event => void save(event)}>
      <label>主题名称<input required autoFocus disabled={saving} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
      <div className="research-theme-form-row"><label>研究类型<select value={draft.kind || 'other'} disabled={saving} onChange={event => setDraft({ ...draft, kind: event.target.value as ResearchThemeKind })}>{Object.entries(themeKindLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>重要性<select value={draft.priority || 'important'} disabled={saving} onChange={event => setDraft({ ...draft, priority: event.target.value as 'core' | 'important' })}><option value="core">核心</option><option value="important">重要</option></select></label></div>
      <label>为什么重要<input value={draft.priority_reason || ''} disabled={saving} onChange={event => setDraft({ ...draft, priority_reason: event.target.value })} /></label>
      <details><summary>补充问题与背景</summary><label>研究问题（可选）<textarea rows={2} disabled={saving} value={draft.question || ''} onChange={event => setDraft({ ...draft, question: event.target.value })} /></label><label>背景（可选）<textarea rows={3} disabled={saving} value={draft.background || ''} onChange={event => setDraft({ ...draft, background: event.target.value })} /></label></details>
      <label className="research-theme-pin"><input type="checkbox" checked={draft.pinned || false} disabled={saving} onChange={event => setDraft({ ...draft, pinned: event.target.checked })} />固定此主题</label>
      <div className="research-theme-actions"><button type="submit" disabled={saving || !draft.title.trim()}>{saving ? '保存中…' : draft.theme_id ? '保存主题' : '创建并开始研究'}</button><button type="button" disabled={saving} onClick={() => setDraft(null)}>取消</button></div>
    </form>}
    <div className="research-theme-list">{active.map(record)}</div>
    {data && !active.length && <p className="sector-research-note">尚无重点主题。建立主题后，研究员将补充分析并持续更新。</p>}
    {inactive.length > 0 && <details className="research-theme-archive"><summary>暂停或结束的主题 · {inactive.length}</summary>{inactive.map(record)}</details>}
  </section>
}
