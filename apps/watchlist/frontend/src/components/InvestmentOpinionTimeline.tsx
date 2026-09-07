import { useEffect, useState, type FormEvent } from 'react'
import {
  createInstrumentResearchNote,
  deleteInstrumentResearchNote,
  updateInstrumentResearchNote,
  type InstrumentResearchNote,
  type InstrumentResearchNoteInput,
  type InstrumentResearchProfile,
  type InstrumentResearchResponse,
} from '../lib/api'
import './investment-opinion-timeline.css'

type Props = {
  instrumentId: string
  research: InstrumentResearchResponse
  onChange: (research: InstrumentResearchResponse) => void
  language?: 'en' | 'zh-Hans'
  requestedNoteDate?: string | null
  onRequestedNoteHandled?: () => void
  onOpenNote?: (date: string) => void
}

type OpinionDraft = { original?: InstrumentResearchNote; date: string; title: string; body: string; source: string }
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
  }
}

export default function InvestmentOpinionTimeline({ instrumentId, research, onChange, language = 'zh-Hans', requestedNoteDate, onRequestedNoteHandled, onOpenNote }: Props) {
  const t = (zh: string, en: string) => language === 'zh-Hans' ? zh : en
  const [draft, setDraft] = useState<OpinionDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const notes = orderedNotes(research.notes)
  const legacy = legacyFields.filter(([field]) => typeof research.profile[field] === 'string' && String(research.profile[field]).trim())

  useEffect(() => {
    setDraft(null)
    setError('')
    setNotice('')
  }, [instrumentId])

  useEffect(() => {
    if (!requestedNoteDate) return
    setDraft({ date: requestedNoteDate, title: '', body: '', source: '' })
    setError('')
    setNotice('')
    onRequestedNoteHandled?.()
  }, [requestedNoteDate, onRequestedNoteHandled])

  function begin(original?: InstrumentResearchNote) {
    setDraft(original ? { original, date: original.note_date, title: original.title === DEFAULT_TITLE ? '' : original.title,
      body: original.body || original.summary, source: original.source_refs }
      : { date: localDate(), title: '', body: '', source: '' })
    setError('')
    setNotice('')
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft) return
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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('删除失败，请重试。', 'Could not delete. Please try again.'))
    } finally {
      setSaving(false)
    }
  }

  return <section className="investment-opinion-timeline" aria-label={t('投资观点时间线', 'Investment view timeline')}>
    <header className="investment-opinion-heading">
      <div><h2>{t('投资观点', 'Investment views')}</h2><p>{t('记录你对这个标的的研究与投资判断，按时间保留观点的演变。', 'Keep your own research and investment judgments for this instrument as a dated history.')}</p></div>
      <button type="button" onClick={() => begin()} disabled={saving || Boolean(draft)}>{t('新增观点', 'Add view')}</button>
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
      <div className="investment-opinion-editor-actions">
        {draft.original && <button className="investment-opinion-delete" type="button" disabled={saving} onClick={() => void remove(draft.original!.note_id)}>{t('删除记录', 'Delete record')}</button>}
        <button type="button" onClick={() => { setDraft(null); setError('') }} disabled={saving}>{t('取消', 'Cancel')}</button>
        <button type="submit" disabled={saving}>{saving ? t('保存中…', 'Saving…') : t('保存观点', 'Save view')}</button>
      </div>
    </form>}
    {notes.length ? <ol className="investment-opinion-entries">
      {notes.map((note) => <li key={note.note_id}>
        <div className="investment-opinion-date"><time dateTime={note.note_date}>{note.note_date}</time>{note.author && <span translate="no">{note.author}</span>}{onOpenNote && <button type="button" onClick={() => onOpenNote(note.note_date)}>{t('在业绩图中查看', 'View on chart')}</button>}</div>
        <article>
          <div className="investment-opinion-entry-heading">
            <h3 translate="no">{note.title === DEFAULT_TITLE ? t('投资观点', 'Investment view') : note.title}</h3>
            <button type="button" onClick={() => begin(note)} disabled={saving || Boolean(draft)} aria-label={`${t('更正观点', 'Correct view')} · ${note.note_date} · ${note.title}`}>{t('更正', 'Correct')}</button>
          </div>
          {note.summary && note.summary !== note.body && <p className="investment-opinion-summary" translate="no">{note.summary}</p>}
          {note.body && <p className="investment-opinion-body" translate="no">{note.body}</p>}
          {note.source_refs && <p className="investment-opinion-source"><span>{t('来源', 'Source')}</span>{/^https?:\/\/\S+$/i.test(note.source_refs)
            ? <a href={note.source_refs} target="_blank" rel="noreferrer">{note.source_refs}</a> : note.source_refs}</p>}
        </article>
      </li>)}
    </ol> : <p className="investment-opinion-empty">{t('尚无观点记录。形成新的判断时，记下一条。', 'No views recorded yet. Add an entry when you form a new view.')}</p>}
    {legacy.length > 0 && <details className="investment-opinion-legacy">
      <summary>{t('原研究记录', 'Previous research record')}{research.profile.updated_at ? ` · ${research.profile.updated_at.slice(0, 10)}` : ''}</summary>
      <dl>{legacy.map(([field, zh, en]) => <div key={field}><dt>{t(zh, en)}</dt><dd translate="no">{String(research.profile[field])}</dd></div>)}</dl>
    </details>}
  </section>
}
