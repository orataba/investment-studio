import { useEffect, useState, type FormEvent } from 'react'
import {
  createInstrumentResearchNote,
  deleteInstrumentResearchNote,
  getInstrumentResearch,
  updateInstrumentResearchNote,
  type InvestmentOpinionResearchContextInput,
  type InstrumentResearchNote,
  type InstrumentResearchNoteInput,
  type InstrumentResearchProfile,
  type InstrumentResearchResponse,
} from '../lib/api'
import { getResearchThemes, type AskResearchAssistant, type ResearchTheme } from '../lib/researchDossierApi'
import { announceResearchPublication, RESEARCH_UPDATED } from '../lib/researchUpdates'
import { SourceList } from './ResearchEvidence'
import './investment-opinion-timeline.css'

type Props = {
  instrumentId: string
  research: InstrumentResearchResponse
  onChange: (research: InstrumentResearchResponse) => void
  language?: 'en' | 'zh-Hans'
  requestedNoteDate?: string | null
  onRequestedNoteHandled?: () => void
  onOpenNote?: (date: string) => void
  onAskAssistant?: AskResearchAssistant
}

type OpinionDraft = { original?: InstrumentResearchNote; date: string; title: string; body: string; source: string; context?: InvestmentOpinionResearchContextInput; contextEdited?: boolean }
const DEFAULT_TITLE = '投资观点'

const legacyFields: Array<[keyof InstrumentResearchProfile, string, string]> = [
  ['current_view', '原投资判断', 'Previous investment view'],
  ['thesis', '投资逻辑', 'Investment thesis'],
  ['why_now', '入场考虑', 'Timing'],
  ['edge_assessment', '研究判断', 'Research assessment'],
  ['valuation_framework', '估值考虑', 'Valuation'],
  ['catalysts', '催化因素', 'Catalysts'],
  ['key_risks', '主要风险', 'Key risks'],
  ['disconfirming_evidence', '反证', 'Disconfirming evidence'],
  ['open_questions', '待核实问题', 'Open questions'],
  ['monitoring_plan', '跟踪计划', 'Monitoring plan'],
  ['people_assessment', '人员与治理', 'People and governance'],
  ['portfolio_role', '组合角色', 'Portfolio role'],
  ['time_horizon', '投资期限', 'Time horizon'],
  ['decision_rationale', '决策依据', 'Decision rationale'],
  ['primary_analyst', '研究人员', 'Analyst'],
]

function orderedNotes(notes: InstrumentResearchNote[]) {
  return [...notes].sort((a, b) => b.note_date.localeCompare(a.note_date) || b.created_at.localeCompare(a.created_at))
}

export function latestInvestmentOpinion(research: InstrumentResearchResponse): { title: string; body: string; noteDate: string | null } | null {
  const latest = orderedNotes(research.notes)[0]
  if (latest) return { title: latest.title, body: latest.body || latest.summary, noteDate: latest.note_date }
  const body = research.profile.current_view || research.profile.thesis
  return body.trim() ? { title: DEFAULT_TITLE, body, noteDate: research.profile.updated_at?.slice(0, 10) || null } : null
}

function localDate() {
  const date = new Date()
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function noteInput(draft: OpinionDraft, analyst: string): InstrumentResearchNoteInput {
  const original = draft.original
  return {
    note_date: draft.date,
    note_type: original?.note_type ?? 'thesis_update',
    title: draft.title.trim() || DEFAULT_TITLE,
    body: draft.body.trim(),
    source_refs: draft.source.trim(),
    summary: original?.summary ?? '',
    importance: original?.importance ?? 'medium',
    tags: original?.tags ?? [],
    people: original?.people ?? '',
    author: original?.author ?? analyst,
    follow_up_date: original?.follow_up_date ?? null,
    completed_at: original?.completed_at ?? null,
    ...(draft.contextEdited ? { research_context: draft.context } : {}),
  }
}

function editableContext(note: InstrumentResearchNote): InvestmentOpinionResearchContextInput | undefined {
  if (!note.research_context) return undefined
  const fields: Array<keyof InvestmentOpinionResearchContextInput> = ['theme_id', 'research_update_id', 'event_case_id', 'event_version_id', 'related_note_id', 'related_revision', 'relationship', 'background', 'horizon', 'verification', 'invalidation', 'outcome', 'mechanism_assessment', 'alternative_explanations', 'lesson', 'applicability', 'limitations', 'source_ids']
  return Object.fromEntries(fields.filter(key => key in note.research_context!).map(key => [key, note.research_context![key]]))
}

export function InvestmentOpinionContext({ note, language = 'zh-Hans', instrumentId }: { note: InstrumentResearchNote; language?: 'en' | 'zh-Hans'; instrumentId?: string }) {
  const t = (zh: string, en: string) => language === 'zh-Hans' ? zh : en
  const context = note.research_context
  if (!context) return null
  const fields: Array<[string, string | undefined]> = [
    [t('当时背景与依据', 'Background and reasoning'), context.background], [t('判断期限', 'Horizon'), context.horizon],
    [t('验证条件', 'What to verify'), context.verification], [t('改判条件', 'What would change the view'), context.invalidation],
    [t('结果', 'Outcome'), context.outcome], [t('机制复核', 'Mechanism assessment'), context.mechanism_assessment],
    [t('其他解释', 'Alternative explanations'), context.alternative_explanations?.join('\n')],
    [t('经验', 'Lesson'), context.lesson], [t('适用条件', 'Applicability'), context.applicability], [t('局限', 'Limitations'), context.limitations],
  ]
  const populated = fields.filter(([, value]) => value?.trim())
  if (!populated.length && !context.source_quote && !context.source_ids?.length) return null
  return <details className="investment-opinion-context"><summary>{t('背景、验证与经验', 'Context, verification and lessons')}</summary>
    <dl>{populated.map(([label, value]) => <div key={label}><dt>{label}</dt><dd translate="no">{value}</dd></div>)}</dl>
    {context.source_quote && <p className="investment-opinion-source" translate="no"><span>{t('原话', 'Original statement')}</span>{context.source_quote}</p>}
    {context.sources?.length ? <SourceList sources={context.sources} instrumentId={instrumentId} versionId={`pm:${note.note_id}:${note.revision_number}`} /> : Boolean(context.source_ids?.length) && <p className="investment-opinion-source"><span>{t('资料引用', 'Evidence references')}</span>{context.source_ids!.join(' · ')}</p>}
  </details>
}

export default function InvestmentOpinionTimeline({ instrumentId, research, onChange, language = 'zh-Hans', requestedNoteDate, onRequestedNoteHandled, onOpenNote, onAskAssistant }: Props) {
  const t = (zh: string, en: string) => language === 'zh-Hans' ? zh : en
  const [draft, setDraft] = useState<OpinionDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [themes, setThemes] = useState<ResearchTheme[]>([])
  const [identityName, setIdentityName] = useState('')
  const [identityId, setIdentityId] = useState<string | null>(null)
  const [canWrite, setCanWrite] = useState(false)
  const [localUnrestricted, setLocalUnrestricted] = useState(false)
  const [themesError, setThemesError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const notes = orderedNotes(research.notes)
  const legacy = legacyFields.filter(([field]) => typeof research.profile[field] === 'string' && String(research.profile[field]).trim())

  useEffect(() => {
    setDraft(null)
    setError('')
    setNotice('')
  }, [instrumentId])

  useEffect(() => {
    const controller = new AbortController()
    setThemesError('')
    void getResearchThemes(instrumentId, controller.signal).then(value => {
      if (!controller.signal.aborted) { setThemes(value.themes); setIdentityName(value.identity.display_name); setIdentityId(value.identity.user_id); setCanWrite(Boolean(value.identity.local_unrestricted) || value.identity.team_role !== 'reader'); setLocalUnrestricted(Boolean(value.identity.local_unrestricted)) }
    }).catch(reason => { if (!controller.signal.aborted) setThemesError(reason instanceof Error ? reason.message : t('主题读取失败', 'Could not load themes')) })
    return () => controller.abort()
  }, [instrumentId, refresh])

  useEffect(() => {
    let cancelled = false
    const updated = (event: Event) => {
      if (!(event as CustomEvent<string[]>).detail.includes(instrumentId)) return
      setRefresh(value => value + 1)
      void getInstrumentResearch(instrumentId).then(value => { if (!cancelled) onChange(value) })
        .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : t('观点刷新失败', 'Could not refresh views')) })
    }
    window.addEventListener(RESEARCH_UPDATED, updated)
    return () => { cancelled = true; window.removeEventListener(RESEARCH_UPDATED, updated) }
  }, [instrumentId, onChange])

  useEffect(() => {
    if (!requestedNoteDate || !canWrite) return
    setDraft({ date: requestedNoteDate, title: '', body: '', source: '' })
    setError('')
    setNotice('')
    onRequestedNoteHandled?.()
  }, [requestedNoteDate, onRequestedNoteHandled, canWrite])

  function begin(original?: InstrumentResearchNote) {
    setDraft(original ? { original, date: original.note_date, title: original.title === DEFAULT_TITLE ? '' : original.title,
      body: original.body || original.summary, source: original.source_refs, context: editableContext(original) }
      : { date: localDate(), title: '', body: '', source: '' })
    setError('')
    setNotice('')
  }

  function continueView(original: InstrumentResearchNote) {
    setDraft({ date: localDate(), title: '', body: '', source: '', contextEdited: true,
      context: { relationship: 'update', related_note_id: original.note_id, related_revision: original.revision_number, ...(original.research_context?.theme_id ? { theme_id: original.research_context.theme_id } : {}) } })
    setError('')
    setNotice('')
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft || !canWrite) return
    if (!draft.body.trim()) {
      setError(t('请填写投资观点。', 'Enter an investment view.'))
      return
    }
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const payload = { note: noteInput(draft, research.profile.primary_analyst), updated_by: 'terminal_ui' }
      const response = draft.original
        ? await updateInstrumentResearchNote(instrumentId, draft.original.note_id, payload)
        : await createInstrumentResearchNote(instrumentId, payload)
      onChange(response)
      setDraft(null)
      setNotice(t('投资观点已保存。', 'Investment view saved.'))
      announceResearchPublication([instrumentId])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('保存失败，请重试。', 'Could not save. Please try again.'))
    } finally {
      setSaving(false)
    }
  }

  async function remove(noteId: string) {
    setSaving(true)
    setError('')
    setNotice('')
    try {
      onChange(await deleteInstrumentResearchNote(instrumentId, noteId))
      setDraft(null)
      setNotice(t('记录已删除。', 'Record deleted.'))
      announceResearchPublication([instrumentId])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('删除失败，请重试。', 'Could not delete. Please try again.'))
    } finally {
      setSaving(false)
    }
  }

  return <section className="investment-opinion-timeline" aria-label={t('投资观点时间线', 'Investment view timeline')}>
    <header className="investment-opinion-heading">
      <div><h2>{t('投资观点', 'Investment views')}</h2><p>{t('记录你对这个标的的研究与投资判断，按时间保留观点的演变。', 'Keep your own research and investment judgments for this instrument as a dated history.')}</p></div>
      <button type="button" onClick={() => begin()} disabled={!canWrite || saving || Boolean(draft)}>{t('新增观点', 'Add view')}</button>
    </header>
    {error && <p className="investment-opinion-message" role="alert">{error}</p>}
    {notice && <p className="investment-opinion-message" role="status">{notice}</p>}
    {draft && <form className="investment-opinion-editor" onSubmit={(event) => void save(event)}>
      <h3>{draft.original ? t('更正记录', 'Correct record') : t('新增观点', 'Add view')}</h3>
      {draft.original && <p className="investment-opinion-hint">{t('更正这条记录；新的判断请另增一条。', 'Correct this record; add a separate entry for a new view.')}</p>}
      <div className="investment-opinion-form-row">
        <label><span>{t('日期', 'Date')}</span><input type="date" value={draft.date} required disabled={saving} onChange={(event) => setDraft({ ...draft, date: event.target.value })} /></label>
        <label><span>{t('标题（可选）', 'Title (optional)')}</span><input value={draft.title} disabled={saving} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      </div>
      <label><span>{t('观点', 'View')}</span><textarea rows={5} value={draft.body} required autoFocus disabled={saving} onChange={(event) => setDraft({ ...draft, body: event.target.value })} /></label>
      <label><span>{t('来源（可选）', 'Source (optional)')}</span><input value={draft.source} disabled={saving} placeholder={t('链接、报告或讨论出处', 'Link, report or discussion reference')} onChange={(event) => setDraft({ ...draft, source: event.target.value })} /></label>
      {identityName && <p className="investment-opinion-hint">{t('团队共享 · 作者', 'Shared with team · Author')}：{draft.original?.author || identityName}</p>}
      <details className="investment-opinion-context"><summary>{t('关联主题与验证条件（可选）', 'Theme and verification (optional)')}</summary>
        {themesError ? <p role="alert">{themesError}</p> : <label><span>{t('持续关注主题', 'Research theme')}</span><select value={draft.context?.theme_id || ''} disabled={saving} onChange={event => setDraft({ ...draft, contextEdited: true, context: { ...draft.context, theme_id: event.target.value || null } })}>
          <option value="">{t('暂不关联', 'No theme')}</option>{themes.map(theme => <option key={theme.theme_id} value={theme.theme_id}>{theme.title}{theme.status !== 'active' ? ` · ${theme.status === 'paused' ? t('已暂停', 'Paused') : t('已结束', 'Closed')}` : ''}</option>)}
        </select></label>}
        {([['background', t('当时背景与依据', 'Background and reasoning')], ['horizon', t('判断期限', 'Horizon')], ['verification', t('验证条件', 'What to verify')], ['invalidation', t('改判条件', 'What would change the view')]] as const).map(([key, label]) => <label key={key}><span>{label}</span><textarea rows={2} value={draft.context?.[key] || ''} disabled={saving} onChange={event => setDraft({ ...draft, contextEdited: true, context: { ...draft.context, [key]: event.target.value } })} /></label>)}
      </details>
      <div className="investment-opinion-editor-actions">
        {draft.original && <button className="investment-opinion-delete" type="button" disabled={saving} onClick={() => void remove(draft.original!.note_id)}>{t('删除记录', 'Delete record')}</button>}
        <button type="button" onClick={() => { setDraft(null); setError('') }} disabled={saving}>{t('取消', 'Cancel')}</button>
        <button type="submit" disabled={saving}>{saving ? t('保存中…', 'Saving…') : t('保存观点', 'Save view')}</button>
      </div>
    </form>}
    {notes.length ? <ol className="investment-opinion-entries">
      {notes.map((note) => <li key={note.note_id}>
        <div className="investment-opinion-date"><time dateTime={note.note_date}>{note.note_date}</time><span translate="no">{note.author || t('未标注作者', 'Author not recorded')}</span><span>{t('实际记录', 'Recorded')} <time dateTime={note.created_at}>{note.created_at.replace('T', ' ').slice(0, 16)}</time></span>{onOpenNote && <button type="button" onClick={() => onOpenNote(note.note_date)}>{t('在业绩图中查看', 'View on chart')}</button>}</div>
        <article>
          <div className="investment-opinion-entry-heading">
            <h3 translate="no">{note.title === DEFAULT_TITLE ? t('投资观点', 'Investment view') : note.title}</h3>
            <button type="button" onClick={() => begin(note)} disabled={!canWrite || (!localUnrestricted && note.author_user_id !== identityId) || saving || Boolean(draft)} aria-label={`${t('更正观点', 'Correct view')} · ${note.note_date} · ${note.title}`}>{t('更正', 'Correct')}</button>
          </div>
          {note.summary && note.summary !== note.body && <p className="investment-opinion-summary" translate="no">{note.summary}</p>}
          {note.body && <p className="investment-opinion-body" translate="no">{note.body}</p>}
          {note.research_context && <p className="investment-opinion-source">
            {note.research_context.relationship && note.research_context.relationship !== 'initial' && <span>{({ update: t('判断修订', 'Updated view'), review: t('复盘', 'Review'), lesson: t('经验', 'Lesson') })[note.research_context.relationship]}</span>}
            {note.research_context.theme_id && <span>{t('主题', 'Theme')}：{themes.find(theme => theme.theme_id === note.research_context?.theme_id)?.title || t('已关联持续关注主题', 'Linked research theme')}</span>}
            {note.research_context.related_note_id && <span>{t('关联原观点', 'Related view')}：{research.notes.find(item => item.note_id === note.research_context?.related_note_id)?.title || t('历史记录', 'Historical record')}{note.research_context.related_revision ? ` · v${note.research_context.related_revision}` : ''}</span>}
            {note.research_context.recorded_via?.startsWith('assistant') && <span>{t('助手整理', 'Organized by assistant')}</span>}
          </p>}
          <InvestmentOpinionContext note={note} language={language} instrumentId={instrumentId} />
          {note.source_refs && <p className="investment-opinion-source"><span>{t('来源', 'Source')}</span>{/^https?:\/\/\S+$/i.test(note.source_refs)
            ? <a href={note.source_refs} target="_blank" rel="noreferrer">{note.source_refs}</a> : note.source_refs}</p>}
          <div className="investment-opinion-followups"><button type="button" disabled={!canWrite || saving || Boolean(draft)} onClick={() => continueView(note)}>{t('补充判断', 'Add a follow-up view')}</button>
            {onAskAssistant && <button type="button" onClick={() => onAskAssistant(t(`请围绕这条投资经理观点继续讨论和复核：${note.title}。请区分原始判断、后续结果和机制是否得到支持，保留不同意见；只有我明确要求保存时，才把我的新判断或复盘记录下来。`, `Continue discussing and reviewing this PM view: ${note.title}. Distinguish the original judgment, subsequent outcomes and evidence for the mechanism. Keep differing opinions; save my new view or review only when I explicitly ask.`), { instrument_id: instrumentId, pm_note_id: note.note_id, pm_note_revision: note.revision_number, ...(note.research_context?.theme_id ? { theme_id: note.research_context.theme_id } : {}) })}>{t('与助手讨论 / 复盘', 'Discuss / review with assistant')}</button>}
          </div>
        </article>
      </li>)}
    </ol> : <p className="investment-opinion-empty">{t('尚无观点记录。形成新的判断时，记下一条。', 'No views recorded yet. Add an entry when you form a new view.')}</p>}
    {legacy.length > 0 && <details className="investment-opinion-legacy">
      <summary>{t('原研究记录', 'Previous research record')}{research.profile.updated_at ? ` · ${research.profile.updated_at.slice(0, 10)}` : ''}</summary>
      <dl>{legacy.map(([field, zh, en]) => <div key={field}><dt>{t(zh, en)}</dt><dd translate="no">{String(research.profile[field])}</dd></div>)}</dl>
    </details>}
  </section>
}
