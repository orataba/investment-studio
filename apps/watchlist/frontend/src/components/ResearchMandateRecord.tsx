import { useStudioAccount } from './AccountBoundary'
import { useState, type FormEvent } from 'react'
import { getResearchDossier, saveResearchMandate, type ResearchMandate, type ResearchMandateInput } from '../lib/researchDossierApi'
import { dateLabel } from './ResearchEvidence'

const originLabels = { user: '用户', research: '研究员', initial: '初始方法' }
const authorLabel = (author: NonNullable<ResearchMandate['author']>) => {
  const role = originLabels[author.origin]
  return author.display_name && author.display_name !== role ? `${author.display_name}（${role}）` : role
}

export default function ResearchMandateRecord({ instrumentId, mandate, onSaved, readOnly = false }: {
  instrumentId: string; mandate: ResearchMandate; onSaved: (value: ResearchMandate) => void; readOnly?: boolean
}) {
  const canWrite = useStudioAccount()?.team_role !== 'reader' && !readOnly
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<ResearchMandateInput>({ guidance: mandate.guidance, expected_revision: mandate.revision })
  const [latest, setLatest] = useState<ResearchMandate | null>(null)
  const [saving, setSaving] = useState(false)
  const [reading, setReading] = useState(false)
  const [error, setError] = useState('')
  const guidanceVersions = (mandate.versions || []).filter((version, index, versions) => version.guidance !== (versions[index + 1]?.guidance ?? mandate.guidance))

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!canWrite || saving || reading) return
    setSaving(true)
    setError('')
    try {
      const saved = await saveResearchMandate(instrumentId, { ...draft, guidance: draft.guidance.trim() })
      onSaved(saved)
      setEditing(false)
      setLatest(null)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '研究指导保存失败') }
    finally { setSaving(false) }
  }

  async function readLatest() {
    setReading(true)
    try {
      const dossier = await getResearchDossier(instrumentId, undefined, false)
      if (!dossier.mandate) throw new Error('研究指导读取失败')
      const current = dossier.mandate
      setLatest(current)
      setDraft(value => ({ ...value, expected_revision: current.revision }))
      setError('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '研究指导读取失败') }
    finally { setReading(false) }
  }

  return <section aria-label="研究问题与指导" className="research-guidance">
    <div className="research-dossier-section-heading"><h4>研究问题与指导</h4>
      {!editing && canWrite && <button type="button" onClick={() => {
        setDraft({ guidance: mandate.guidance, expected_revision: mandate.revision }); setEditing(true); setError(''); setLatest(null)
      }}>编辑研究指导</button>}
    </div>
    <p className="sector-research-note">用一段话说明你想弄清的问题、投资期限和约束。研究会据此选择资料和方法，留空也可开始。</p>
    {editing && canWrite ? <form className="research-material-form" onSubmit={event => void save(event)}>
      <label className="research-material-wide">研究问题、目标与约束（可选）
        <textarea rows={7} maxLength={60000} value={draft.guidance} disabled={saving || reading} onChange={event => setDraft({ ...draft, guidance: event.target.value })} placeholder="例如：未来一年，盈利增长能否转化为现金回报？重点核实资本开支与定价假设，并说明哪些证据会改变判断。" />
      </label>
      <p className="sector-research-note research-material-wide">保存后用于后续研究；清空可取消已有人工补充要求。</p>
      {latest && <section className="research-guidance-latest research-material-wide" aria-label="最新已保存指导">
        <h4>最新已保存指导</h4><p className="research-dossier-text" translate={latest.guidance ? 'no' : undefined}>{latest.guidance || '未设置额外指导。'}</p>
        <p className="sector-research-note">你的草稿已保留。核对后保存，将替换上面的最新指导。</p>
        <button type="button" disabled={saving || reading} onClick={() => setDraft({ guidance: latest.guidance, expected_revision: latest.revision })}>用最新指导替换草稿</button>
      </section>}
      {error && <div className="research-material-wide"><p role="alert">{error}</p><button type="button" disabled={saving || reading} onClick={() => void readLatest()}>{reading ? '读取中…' : '读取最新指导并核对'}</button></div>}
      <div className="sector-research-actions research-material-wide"><button type="submit" disabled={saving || reading}>{saving ? '保存中…' : '保存研究指导'}</button><button type="button" disabled={saving || reading} onClick={() => setEditing(false)}>取消</button></div>
    </form> : <>
      <p className="research-dossier-text" translate={mandate.guidance ? 'no' : undefined}>{mandate.guidance || '尚未补充研究指导。可直接更新研究，也可先说明你关心的问题。'}</p>
      {Boolean(guidanceVersions.length) && <details className="research-dossier-record"><summary>指导修订历史</summary>
        {guidanceVersions.map(version => <article key={version.version_id}>
          <h4>{version.guidance_updated_at ? <><span>指导更新</span> {dateLabel(version.guidance_updated_at)}</> : <><span>历史记录时间</span> {dateLabel(version.updated_at)}</>}</h4>
          <p className="research-dossier-text" translate={version.guidance ? 'no' : undefined}>{version.guidance || '未设置额外指导。'}</p>
          {version.guidance_author && <p className="sector-research-note">更新来源：{authorLabel(version.guidance_author)}</p>}
        </article>)}
      </details>}
      {mandate.guidance_updated_at && <p className="sector-research-note">指导更新 {dateLabel(mandate.guidance_updated_at)}</p>}
    </>}
  </section>
}
