import { useEffect, useRef, useState, type FormEvent } from 'react'
import { createInstrumentResearchNote, type InvestmentOpinionResearchContextInput } from '../lib/api'
import { announceResearchPublication } from '../lib/researchUpdates'
import { useStudioAccount } from './AccountBoundary'

type Props = {
  instrumentId: string; title: string; context: InvestmentOpinionResearchContextInput; onCancel: () => void; onSaved: () => void
}

export default function ResearchOpinionComposer(props: Props) {
  return <OpinionDraft key={props.instrumentId} {...props} />
}

function OpinionDraft({ instrumentId, title, context, onCancel, onSaved }: Props) {
  const account = useStudioAccount()
  // A research refresh must not attach a new evidence version to an older draft.
  const [origin] = useState(() => ({ title, context: { ...context, source_ids: context.source_ids?.slice() } }))
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const [body, setBody] = useState('')
  const [background, setBackground] = useState(context.background || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  async function save(event: FormEvent) {
    event.preventDefault()
    if (saving || !body.trim() || !background.trim() || account?.team_role === 'reader') return
    setSaving(true); setError('')
    const now = new Date()
    const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
    try {
      await createInstrumentResearchNote(instrumentId, { note: {
        note_date: day, note_type: 'thesis_update', title: origin.title, body: body.trim(), summary: '', source_refs: '',
        importance: 'medium', tags: [], people: '', author: account?.display_name || '', follow_up_date: null, completed_at: null,
        research_context: { ...origin.context, background: background.trim() },
      }, updated_by: 'terminal_ui' })
      announceResearchPublication([instrumentId])
      if (mounted.current) onSaved()
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : '投资观点保存失败') }
    finally { if (mounted.current) setSaving(false) }
  }
  return <form className="research-opinion-composer" aria-label="记录投资观点" onSubmit={event => void save(event)}>
    <label>我的投资观点<textarea rows={4} required autoFocus value={body} disabled={saving} onChange={event => setBody(event.target.value)} /></label>
    <label>当时背景与依据<textarea rows={3} required value={background} disabled={saving} onChange={event => setBackground(event.target.value)} /></label>
    {error && <p role="alert">{error}</p>}
    <div className="research-theme-actions"><button type="submit" disabled={saving || !body.trim() || !background.trim()}>{saving ? '保存中…' : '保存投资观点'}</button><button type="button" disabled={saving} onClick={onCancel}>取消</button></div>
  </form>
}
