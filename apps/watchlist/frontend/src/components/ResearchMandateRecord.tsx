import { useStudioAccount } from './AccountBoundary'
import { useState, type FormEvent } from 'react'
import { saveResearchMandate, type ResearchMandate, type ResearchMandateInput } from '../lib/researchDossierApi'

const fields = [
  ['mechanisms', '价格与基本面传导'], ['research_approach', '研究思路'], ['focus', '重点关注'],
  ['source_plan', '资料与日程来源'], ['gaps', '尚待补齐'],
] as const
const originLabels = { user: '用户', research: '研究员', initial: '初始方法' }
const authorLabel = (author: NonNullable<ResearchMandate['author']>) => {
  const role = originLabels[author.origin]
  return author.display_name && author.display_name !== role ? `${author.display_name}（${role}）` : role
}

export default function ResearchMandateRecord({ instrumentId, mandate, onSaved }: {
  instrumentId: string; mandate: ResearchMandate; onSaved: (value: ResearchMandate) => void
}) {
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<ResearchMandateInput>(mandate)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  async function save(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      const saved = await saveResearchMandate(instrumentId, {
        title: draft.title, background: draft.background,
        ...Object.fromEntries(fields.map(([key]) => [key, draft[key].map(row => row.trim()).filter(Boolean)])),
      } as ResearchMandateInput)
      onSaved(saved)
      setEditing(false)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '研究框架保存失败') }
    finally { setSaving(false) }
  }
  return <section aria-label="研究框架">
    <div className="research-dossier-section-heading"><h4>研究框架</h4>
      {!editing && <button type="button" disabled={!canWrite} onClick={() => { setDraft({ ...mandate, focus: mandate.user_focus ?? [] }); setEditing(true); setError('') }}>编辑研究框架</button>}
    </div>
    <p className="sector-research-note">记录本标的的研究背景、核心机制、分析方法与持续关注重点。</p>
    {editing ? <form className="research-material-form" onSubmit={event => void save(event)}>
      <label className="research-material-wide">标题<input value={draft.title} required disabled={saving} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
      <label className="research-material-wide">背景与研究边界<textarea rows={5} value={draft.background} disabled={saving} onChange={e => setDraft({ ...draft, background: e.target.value })} /></label>
      {fields.map(([key, label]) => <label key={key} className="research-material-wide">{label}（每行一项）<textarea rows={4} value={draft[key].join('\n')} disabled={saving} onChange={e => setDraft({ ...draft, [key]: e.target.value.split('\n') })} /></label>)}
      {error && <p role="alert" className="research-material-wide">{error}</p>}
      <div className="sector-research-actions research-material-wide"><button type="submit" disabled={saving || !draft.title.trim()}>{saving ? '保存中…' : '保存研究框架'}</button><button type="button" disabled={saving} onClick={() => setEditing(false)}>取消</button></div>
    </form> : <>
      <p><strong translate="no">{mandate.title}</strong></p><p className="research-dossier-text" translate="no">{mandate.background}</p>
      {Boolean(mandate.user_focus?.length) && <div><h4>用户指定重点</h4><ul className="research-dossier-list">{mandate.user_focus!.map((row, i) => <li key={i} translate="no">{row}</li>)}</ul></div>}
      {fields.map(([key, label]) => mandate[key].length > 0 && <details className="research-dossier-record" key={key} open={key === 'focus'}><summary>{label}</summary><ul className="research-dossier-list">{mandate[key].map((row, i) => <li key={i} translate="no">{row}</li>)}</ul></details>)}
      {Boolean(mandate.versions?.length) && <details className="research-dossier-record"><summary>研究框架修订历史 · {mandate.versions!.length} 次</summary>
        {mandate.versions!.map(version => <article key={version.version_id}><h4>{version.updated_at ? new Date(version.updated_at).toLocaleString('zh-CN') : '初始版本'} · {version.title}</h4><p className="research-dossier-text" translate="no">{version.background}</p>
          {version.author && <p className="sector-research-note">更新来源：{authorLabel(version.author)}</p>}
          {Boolean(version.user_focus?.length) && <div><h4>用户指定重点</h4><ul className="research-dossier-list">{version.user_focus!.map((row, i) => <li key={i} translate="no">{row}</li>)}</ul></div>}
          {fields.map(([key, label]) => version[key].length > 0 && <div key={key}><h4>{label}</h4><ul className="research-dossier-list">{version[key].map((row, i) => <li key={i} translate="no">{row}</li>)}</ul></div>)}
        </article>)}
      </details>}
      {mandate.author && <p className="sector-research-note">更新来源：{authorLabel(mandate.author)}</p>}
      <p className="sector-research-note">{mandate.updated_at ? `更新于 ${new Date(mandate.updated_at).toLocaleString('zh-CN')}` : '初始研究框架，背景材料仍需持续核实和补齐。'}</p>
    </>}
  </section>
}
