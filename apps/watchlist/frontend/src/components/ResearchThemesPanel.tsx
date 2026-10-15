import { useEffect, useRef, useState, type FormEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { announceResearchPublication, RESEARCH_UPDATED } from '../lib/researchUpdates'
import { addResearchThemeNote, deleteResearchTheme, createResearchTheme, getResearchTheme, getResearchThemes, updateResearchTheme, type AskResearchAssistant, type ResearchTheme, type ResearchThemeInput, type ResearchThemeKind, type ResearchThemesResponse, type ResearchWorkingSet } from '../lib/researchDossierApi'
import ResearchUpdateCard from './ResearchUpdateCard'
import { dateLabel, SourceList } from './ResearchEvidence'
import { SavedFigure } from './ResearchModules'
import ResearchOpinionComposer from './ResearchOpinionComposer'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import './research-themes.css'
import ResearchReadingAside from './ResearchReadingAside'
import ResearchLoading from '../../../../../packages/ui/src/WorkspaceSkeleton'
import ConfirmDialog from '../../../../../packages/ui/src/ConfirmDialog'
import InfoHint from '../../../../../packages/ui/src/InfoHint'
import ResearchScopeNotice from './ResearchScopeNotice'

const statusLabel = { active: '持续关注', paused: '已暂停', closed: '已结束' }
export const themeKindLabels: Record<ResearchThemeKind, string> = { fundamental: '基本面', event: '事件', quantitative: '量化', valuation: '估值', risk: '风险', other: '综合' }
type ThemeDraft = ResearchThemeInput & { theme_id?: string }

type ThemeRecordProps = {
  theme: ResearchTheme; instrumentId: string; canWrite: boolean; saving: boolean
  onAskAssistant?: AskResearchAssistant; onEdit: () => void; onDelete: () => void; onStatus: (status: ResearchTheme['status'], reason?: string) => void
}
function themeFromHash() {
  const prefix = '#research-theme-'
  try { return window.location.hash.startsWith(prefix) ? decodeURIComponent(window.location.hash.slice(prefix.length)) : null } catch { return null }
}

function ThemeCard(props: ThemeRecordProps) {
  const { theme, instrumentId } = props
  const [open, setOpen] = useState(() => themeFromHash() === theme.theme_id)
  const [expandedSummary, setExpandedSummary] = useState(false)
  const [expandedNextCheck, setExpandedNextCheck] = useState(false)
  const [detail, setDetail] = useState<ResearchTheme | null>(null)
  const [closing, setClosing] = useState(false)
  const [closeReason, setCloseReason] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    const selectLinked = () => {
      if (themeFromHash() !== theme.theme_id) return
      setOpen(true)
      let parent = document.getElementById(`research-theme-${encodeURIComponent(theme.theme_id)}`)?.parentElement
      while (parent) { if (parent instanceof HTMLDetailsElement) parent.open = true; parent = parent.parentElement }
    }
    selectLinked()
    window.addEventListener('hashchange', selectLinked)
    return () => window.removeEventListener('hashchange', selectLinked)
  }, [theme.theme_id])
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setError('')
    void getResearchTheme(instrumentId, theme.theme_id, controller.signal).then(value => { if (!controller.signal.aborted) setDetail(value) })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '主题详情读取失败。') })
    return () => controller.abort()
  }, [open, instrumentId, theme.theme_id, theme.source_version_id, theme.revision_number, theme.last_reviewed_at])
  const { canWrite, saving, onEdit, onDelete, onStatus } = props
  const development = theme.baseline_status === 'pending' && theme.migration_origin ? '' : theme.latest_development
  return <article id={`research-theme-${encodeURIComponent(theme.theme_id)}`} className="research-theme-card">
    <header className="research-theme-card-heading"><h3 translate="no">{theme.title}</h3><div className="research-event-tags"><span className="research-status-tag">{theme.priority === 'core' ? '核心' : '重要'}</span><span className={`research-status-tag${theme.status === 'active' ? ' active' : ''}`}>{statusLabel[theme.status]}</span>{theme.pinned && <span className="research-status-tag">已固定</span>}
      <InfoHint label="主题研究状态" detail={[`${themeKindLabels[theme.kind || 'other']} · ${theme.origin === 'researcher' ? '研究员提出' : '人工建立'} · ${theme.author || '未标注作者'}`, theme.last_changed_at ? `实质更新 ${dateLabel(theme.last_changed_at)}` : '尚无实质研究更新', theme.last_reviewed_at ? `最近检查 ${dateLabel(theme.last_reviewed_at)}` : '尚未完成研究检查', theme.close_reason || '']} />
    </div></header>
    {theme.synthesis && theme.baseline_status !== 'pending' ? <section className="research-theme-current"><h4>当前认识</h4><div className={`research-theme-card-summary${!expandedSummary && theme.synthesis.length > 240 ? ' research-theme-summary-preview' : ''}`} translate="no"><ReactMarkdown remarkPlugins={[remarkGfm]}>{theme.synthesis}</ReactMarkdown></div>{theme.synthesis.length > 240 && <button type="button" className="research-support-link" aria-expanded={expandedSummary} onClick={() => setExpandedSummary(value => !value)}>{expandedSummary ? '收起当前认识' : '展开当前认识'}</button>}</section> : theme.baseline_status === 'pending' ? <p className="sector-research-note">{theme.migration_origin ? '历史主题已保留，尚未经新一轮研究复核。' : '待建立研究基线。'}</p> : <p className="sector-research-note">尚未形成主题认识。</p>}
    {theme.next_check && <div className="research-theme-card-next"><span>下一观察</span><div><p className={!expandedNextCheck && theme.next_check.length > 240 ? 'research-theme-next-preview' : undefined} translate="no">{theme.next_check}</p>{theme.next_check.length > 240 && <button type="button" className="research-support-link" aria-expanded={expandedNextCheck} onClick={() => setExpandedNextCheck(value => !value)}>{expandedNextCheck ? '收起下一观察' : '展开下一观察'}</button>}</div></div>}
    {development && <details className="research-theme-development"><summary>本次进展与补充论证{theme.last_changed_at && <> · {dateLabel(theme.last_changed_at)}</>}</summary><div className="research-theme-prose" translate="no"><ReactMarkdown remarkPlugins={[remarkGfm]}>{development}</ReactMarkdown></div></details>}
    <div className="research-theme-card-tools"><button type="button" className="research-support-link" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? '收起研究脉络' : '研究脉络与时间线'}</button>
      {canWrite && <details className="research-theme-management"><summary>管理主题</summary><div className="research-theme-actions"><button type="button" disabled={saving} onClick={onEdit}>编辑主题</button><button type="button" disabled={saving} onClick={() => onStatus(theme.status === 'active' ? 'paused' : 'active')}>{theme.status === 'active' ? '暂停后续跟踪' : '恢复后续跟踪'}</button>{theme.status !== 'closed' && <button type="button" disabled={saving} onClick={() => setClosing(value => !value)}>结束主题跟踪</button>}<button type="button" disabled={saving} onClick={onDelete}>删除主题</button></div></details>}
    </div>
    {closing && theme.status !== 'closed' && <form className="research-theme-editor" onSubmit={event => { event.preventDefault(); if (closeReason.trim()) onStatus('closed', closeReason.trim()) }}><label>结束原因<textarea required value={closeReason} onChange={event => setCloseReason(event.target.value)} /></label><div className="research-theme-actions"><button type="submit" disabled={saving || !closeReason.trim()}>保存并结束</button><button type="button" disabled={saving} onClick={() => setClosing(false)}>取消</button></div></form>}
    {open && <div className="research-theme-expanded">{error && <p role="alert">{error}{detail && '。以下保留上次有效内容。'}</p>}{detail ? <ThemeRecord {...props} theme={detail} /> : !error && <ResearchLoading />}</div>}
  </article>
}

function ThemeRecord({ theme, instrumentId, canWrite, onAskAssistant }: ThemeRecordProps) {
  const [writing, setWriting] = useState(false)
  const [noting, setNoting] = useState(false)
  const [noteBody, setNoteBody] = useState('')
  const [noteSaving, setNoteSaving] = useState(false)
  const [noteError, setNoteError] = useState('')
  const [allHistory, setAllHistory] = useState(false)
  const [showAdministration, setShowAdministration] = useState(false)
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const updates = [...(theme.updates || [])].sort((a, b) => Date.parse(b.recorded_at) - Date.parse(a.recorded_at))
  const administrative = (update: typeof updates[number]) => update.author_role === 'system' || update.change === 'organized' || update.change === 'citation_corrected'
    || Boolean(theme.migration_origin && update.reference.theme_version_id === `theme:${theme.theme_id}:1`)
  const administrationCount = updates.filter(administrative).length
  const visibleUpdates = updates.filter(update => !administrative(update))
  const timeline = [
    ...visibleUpdates.map(update => ({ id: update.update_id, at: update.recorded_at, update, note: null })),
    ...(theme.manual_notes || []).map(note => ({ id: note.note_id, at: note.created_at, update: null, note })),
  ].sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
  const evidence = theme.sources || []
  const figures = evidence.filter(source => theme.figure_source_ids?.includes(source.source_id))
  const versionId = theme.source_version_id
  const development = theme.baseline_status === 'pending' && theme.migration_origin ? '' : theme.latest_development
  const reference = { instrument_id: instrumentId, theme_id: theme.theme_id, theme_version_id: versionId, source_ids: evidence.map(source => source.source_id) }
  const background = `${theme.title}\n核心问题：${theme.question}\n当前认识：${theme.synthesis || '尚未形成'}\n最近进展：${development || ''}\n记录时间：${theme.last_changed_at || theme.updated_at}`
  async function saveNote(event: FormEvent) {
    event.preventDefault()
    if (!canWrite || noteSaving || !noteBody.trim()) return
    setNoteSaving(true); setNoteError('')
    try {
      await addResearchThemeNote(instrumentId, theme.theme_id, noteBody.trim(), theme.revision_number)
      announceResearchPublication([instrumentId]); setNoteBody(''); setNoting(false)
      setNotice({ id: Date.now(), message: '人工记录已保存。', tone: 'success' })
    } catch (reason) { setNoteError(reason instanceof Error ? reason.message : '记录保存失败。') }
    finally { setNoteSaving(false) }
  }
  return <article className="research-theme-record">
    <section className="research-theme-reading" aria-label="主题分析与演变">
        {theme.priority_reason && <p className="research-theme-reason"><span>为什么重要</span><span translate="no">{theme.priority_reason}</span></p>}
        {figures.map(source => <SavedFigure key={`${source.source_id}:${versionId}`} {...{ instrumentId, source, onAskAssistant }} themeVersionId={versionId} themeId={theme.theme_id} />)}
      {timeline.length > 0 && <section className="research-theme-evolution"><h4>主题时间线</h4><ol className="research-timeline" aria-label="主题时间线">{(allHistory ? timeline : timeline.slice(0, 3)).map(item => <li key={item.id}>{item.update ? <ResearchUpdateCard update={item.update} onAskAssistant={onAskAssistant} inTheme compact readable /> : item.note && <article className="research-theme-manual-note"><p className="sector-research-note">人工记录 · <span translate="no">{item.note.author}</span> · <time dateTime={item.note.created_at}>{dateLabel(item.note.created_at)}</time></p><p className="research-dossier-text" translate="no">{item.note.body}</p></article>}</li>)}</ol>{timeline.length > 3 && <button className="research-support-link" type="button" aria-expanded={allHistory} onClick={() => setAllHistory(value => !value)}>{allHistory ? '仅看最近演变' : `查看全部演变 · ${timeline.length}`}</button>}</section>}
    </section>
    <div className="research-theme-actions research-theme-reader-actions">
      <ResearchReadingAside label="主题背景与证据" title={`${theme.title} · 背景与证据`}>
        {theme.question && theme.question !== theme.title && <section className="research-theme-context"><h4>研究问题</h4><p translate="no">{theme.question}</p></section>}
        {theme.background && <section className="research-theme-context"><h4>研究背景</h4><div className="research-theme-prose" translate="no"><ReactMarkdown remarkPlugins={[remarkGfm]}>{theme.background}</ReactMarkdown></div></section>}
        {evidence.length ? <SourceList instrumentId={instrumentId} versionId={versionId} sources={evidence} /> : <p className="sector-research-note">尚无关联来源。</p>}
        {administrationCount > 0 && <><label className="research-theme-history-filter"><input type="checkbox" checked={showAdministration} onChange={event => setShowAdministration(event.target.checked)} />包括整理与资料修订</label>{showAdministration && updates.filter(administrative).map(update => <ResearchUpdateCard key={update.update_id} update={update} onAskAssistant={onAskAssistant} inTheme />)}</>}
      </ResearchReadingAside>
      {onAskAssistant && <button type="button" onClick={() => onAskAssistant(`请继续研究主题“${theme.title}”。\n${background}\n先读取关联原始证据和主题历史，检查反证、定价含义与下一观察；区分研究员和投资经理的判断。`, reference)}>讨论主题</button>}
      {canWrite && <><button type="button" onClick={() => setNoting(value => !value)}>添加人工记录</button><button type="button" onClick={() => setWriting(value => !value)}>写投资观点</button></>}
    </div>
    {noting && <form className="research-theme-editor" aria-label="添加人工记录" onSubmit={event => void saveNote(event)}><label>事实、分析与来源<textarea rows={5} required maxLength={20000} disabled={noteSaving} value={noteBody} onChange={event => setNoteBody(event.target.value)} /></label>{noteError && <p role="alert">{noteError}</p>}<div className="research-theme-actions"><button type="submit" disabled={noteSaving || !noteBody.trim()}>{noteSaving ? '保存中…' : '保存人工记录'}</button><button type="button" disabled={noteSaving} onClick={() => setNoting(false)}>取消</button></div></form>}
    {writing && <ResearchOpinionComposer instrumentId={instrumentId} title={`关于${theme.title}的观点`} context={{ theme_id: theme.theme_id, theme_version_id: versionId, background, source_ids: evidence.map(source => source.source_id) }} onCancel={() => setWriting(false)} onSaved={() => { setWriting(false); setNotice({ id: Date.now(), message: '投资观点已保存。', tone: 'success' }) }} />}
    <NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
  </article>
}

export default function ResearchThemesPanel({ instrumentId, reviewRunId, reviewStatus, onAskAssistant, readOnly = false, onThemeNamesChange, workingSet }: {
  instrumentId: string; reviewRunId?: string; reviewStatus?: string; onAskAssistant?: AskResearchAssistant; readOnly?: boolean; onThemeNamesChange?: (names: Record<string, string>) => void; workingSet?: ResearchWorkingSet | null
}) {
  const [snapshot, setSnapshot] = useState<{ instrumentId: string; data: ResearchThemesResponse } | null>(null)
  const data = snapshot?.instrumentId === instrumentId ? snapshot.data : null
  const [deleting, setDeleting] = useState<ResearchTheme | null>(null)
  const [draft, setDraft] = useState<ThemeDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const [refresh, setRefresh] = useState(0)
  const mutationScope = useRef<object | null>(null)
  const canWrite = Boolean(data && data.identity.team_role !== 'reader' && !readOnly)
  useEffect(() => {
    if (data) onThemeNamesChange?.(Object.fromEntries(data.themes.map(theme => [theme.theme_id, theme.title])))
  }, [data, onThemeNamesChange])
  useEffect(() => {
    mutationScope.current = {}
    setSnapshot(null); setDraft(null); setDeleting(null); setNotice(null); setSaving(false)
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
    void getResearchThemes(instrumentId, controller.signal, false).then(value => { if (!controller.signal.aborted) setSnapshot({ instrumentId, data: value }) })
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
  async function removeTheme() {
    if (!deleting || !canWrite || saving) return
    const scope = mutationScope.current
    setSaving(true); setError('')
    try {
      await deleteResearchTheme(instrumentId, deleting.theme_id, deleting.revision_number)
      announceResearchPublication([instrumentId])
      if (scope === mutationScope.current) { setDeleting(null); setNotice({ id: Date.now(), message: '主题已删除。', tone: 'success' }) }
    } catch (reason) { if (scope === mutationScope.current) setError(reason instanceof Error ? reason.message : '主题删除失败。') }
    finally { if (scope === mutationScope.current) setSaving(false) }
  }
  const active = data?.themes.filter(theme => theme.status === 'active') || []
  const inactive = data?.themes.filter(theme => theme.status !== 'active') || []
  const record = (theme: ResearchTheme) => <div key={theme.theme_id}>{workingSet && !workingSet.reviewed_theme_ids.includes(theme.theme_id) && <p className="research-theme-review-note">本轮未复核 · 判断日期保留原记录</p>}<ThemeCard key={theme.theme_id} {...{ theme, instrumentId, canWrite, saving, onAskAssistant }} onEdit={() => setDraft({ ...theme })} onDelete={() => { setDeleting(theme); setError('') }} onStatus={(status, reason) => void changeStatus(theme, status, reason)} /></div>
  return <section className="research-themes" data-research-empty={Boolean(data && !data.themes.length && !draft && !error)} aria-label="重要研究主题" aria-busy={!data && !error}>
    <div className="research-dossier-section-heading"><div><h2>重要研究主题{active.length > 0 && <span> · {active.length}</span>}</h2></div>{!readOnly && <button type="button" disabled={!canWrite || saving || Boolean(draft) || active.length >= (data?.active_limit || 10)} onClick={() => { setDraft({ title: '', kind: 'other', priority: 'important', responsible_user_id: data?.identity.user_id }); setError('') }}>添加主题</button>}</div>
    {deleting && <ConfirmDialog open title="删除研究主题" description={<>将删除“<span translate="no">{deleting?.title}</span>”及其研究时间线和人工记录。相关投资观点与原始来源仍保留。</>} confirmLabel="删除主题" busy={saving} error={error || undefined} onCancel={() => setDeleting(null)} onConfirm={removeTheme} />}
    {error && !deleting && <p role="alert">{error}</p>}<NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
    {!data && !error && <ResearchLoading />}
    {draft && canWrite && <form className="research-theme-editor" onSubmit={event => void save(event)}>
      <label>主题名称<input required disabled={saving} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
      <details><summary>研究问题与设置（可选）</summary><div className="research-theme-form-row"><label>研究类型<select value={draft.kind || 'other'} disabled={saving} onChange={event => setDraft({ ...draft, kind: event.target.value as ResearchThemeKind })}>{Object.entries(themeKindLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>重要性<select value={draft.priority || 'important'} disabled={saving} onChange={event => setDraft({ ...draft, priority: event.target.value as 'core' | 'important' })}><option value="core">核心</option><option value="important">重要</option></select></label></div>
      <label>为什么重要<input value={draft.priority_reason || ''} disabled={saving} onChange={event => setDraft({ ...draft, priority_reason: event.target.value })} /></label>
      <details><summary>补充问题与背景</summary><label>研究问题（可选）<textarea rows={2} disabled={saving} value={draft.question || ''} onChange={event => setDraft({ ...draft, question: event.target.value })} /></label><label>背景（可选）<textarea rows={3} disabled={saving} value={draft.background || ''} onChange={event => setDraft({ ...draft, background: event.target.value })} /></label></details>
      </details><label className="research-theme-pin"><input type="checkbox" checked={draft.pinned || false} disabled={saving} onChange={event => setDraft({ ...draft, pinned: event.target.checked })} />固定此主题</label>
      {!draft.theme_id && <ResearchScopeNotice instrumentId={instrumentId} />}
      <div className="research-theme-actions"><button type="submit" disabled={saving || !draft.title.trim()}>{saving ? '保存中…' : draft.theme_id ? '保存主题' : '创建主题'}</button><button type="button" disabled={saving} onClick={() => setDraft(null)}>取消</button></div>
    </form>}
    <div className="research-theme-cards">{active.map(record)}</div>
    {inactive.length > 0 && <details className="research-theme-archive"><summary>暂停或结束的主题 · {inactive.length}</summary>{inactive.map(record)}</details>}
    {data && !data.themes.length && <p className="sector-research-note">尚无重要研究主题。可添加未来六个月内可能影响定价、且能够验证和跟踪的催化剂。</p>}
  </section>
}
