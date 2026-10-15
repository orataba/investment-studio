import { useEffect, useRef, useState, type FormEvent } from 'react'
import { getTeamResearchGuidance, saveTeamResearchGuidance, type TeamResearchGuidance, type TeamResearchRole } from '../lib/researchDossierApi'
import { useStudioAccount } from './AccountBoundary'
import { dateLabel } from './ResearchEvidence'

const roles: Record<TeamResearchRole, string> = {
  overview: '主研究员 · 总览', recent_updates: '动态专员 · 标的动态', themes: '主题专员 · 重要研究主题', quantitative: '量化专员 · 量化指标',
  risk: '风控专员', review: '事实复核员',
}
type Draft = Pick<TeamResearchGuidance, 'guidance' | 'section_skills'> & { expected_revision: number; reason: string }

export default function TeamResearchGuidanceRecord() {
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const [saved, setSaved] = useState<TeamResearchGuidance | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [latest, setLatest] = useState<TeamResearchGuidance | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const editButton = useRef<HTMLButtonElement>(null)
  const restoreEditFocus = useRef(false)
  useEffect(() => { if (!draft && restoreEditFocus.current) { restoreEditFocus.current = false; editButton.current?.focus() } }, [draft])
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    void getTeamResearchGuidance(controller.signal).then(value => { if (!controller.signal.aborted) setSaved(value) })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '团队研究指导读取失败') })
    return () => controller.abort()
  }, [refresh])
  const edit = (value: TeamResearchGuidance): Draft => ({ guidance: value.guidance, section_skills: { ...value.section_skills }, expected_revision: value.revision, reason: '' })
  const riskChanged = Boolean(draft && draft.section_skills.risk !== (latest ?? saved)?.section_skills.risk)
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft || !canWrite || busy) return
    if (riskChanged && !draft.reason.trim()) { setError('修改风控指导须填写修改原因'); return }
    setBusy(true); setError('')
    const { reason, ...value } = draft
    try { setSaved(await saveTeamResearchGuidance({ ...value, ...(riskChanged ? { reason: reason.trim() } : {}) })); setDraft(null); setLatest(null) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '团队研究指导保存失败') }
    finally { setBusy(false) }
  }
  async function readLatest() {
    setBusy(true)
    try { const value = await getTeamResearchGuidance(); setLatest(value); setDraft(current => current && ({ ...current, expected_revision: value.revision })); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : '团队研究指导读取失败') }
    finally { setBusy(false) }
  }
  return <section className="research-guidance" aria-label="团队通用研究指导">
    <div className="research-dossier-section-heading"><h4>团队通用研究指导</h4>{saved && !draft && canWrite && <button ref={editButton} type="button" onClick={() => { setDraft(edit(saved)); setLatest(null); setError('') }}>编辑团队指导</button>}</div>
    <p className="sector-research-note">用于团队所有适用标的。此处可调整分析框架、范围与重点；来源核证、访问权限和独立审核规则由系统维护。</p>
    {!saved && !error && <p role="status">Loading</p>}
    {error && <p role="alert">{error}</p>}
    {error && !draft && <button type="button" onClick={() => setRefresh(value => value + 1)}>重试读取</button>}
    {draft ? <form className="research-material-form" onSubmit={event => void save(event)}>
      <label className="research-material-wide">团队共同研究指导<textarea rows={4} value={draft.guidance} maxLength={60000} disabled={busy} onChange={event => setDraft({ ...draft, guidance: event.target.value })} /></label>
      <div className="research-material-wide research-section-skills">{Object.entries(roles).map(([key, label]) => <label key={key}>{label}<textarea rows={5} disabled={busy} value={draft.section_skills[key as TeamResearchRole] || ''} onChange={event => setDraft({ ...draft, section_skills: { ...draft.section_skills, [key]: event.target.value } })} /></label>)}</div>
      {riskChanged && <label className="research-material-wide">风控指导修改原因<input required maxLength={1000} disabled={busy} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value })} /></label>}
      <p className="research-material-wide sector-research-note">保存后用于后续研究，不会立即启动研究。</p>
      {latest && <section className="research-guidance-latest research-material-wide"><h4>最新已保存指导</h4><p translate="no">{latest.guidance || '—'}</p>{Object.entries(roles).map(([key, label]) => <details key={key}><summary>{label}</summary><p className="research-dossier-text" translate="no">{latest.section_skills[key as TeamResearchRole]}</p></details>)}<p>你的草稿已保留。核对后保存，将替换上面的最新指导。</p><button type="button" disabled={busy} onClick={() => setDraft(edit(latest))}>用最新指导替换草稿</button></section>}
      {error && <button className="research-material-wide" type="button" disabled={busy} onClick={() => void readLatest()}>读取最新指导并核对</button>}
      <div className="sector-research-actions research-material-wide"><button type="submit" disabled={busy || (riskChanged && !draft.reason.trim())}>{busy ? '保存中…' : '保存团队指导'}</button><button type="button" disabled={busy} onClick={() => { restoreEditFocus.current = true; setDraft(null); setLatest(null); setError('') }}>取消</button></div>
    </form> : saved && <>
      <p className="research-dossier-text" translate="no">{saved.guidance || '—'}</p>
      <div className="research-section-skills">{Object.entries(roles).map(([key, label]) => <details key={key}><summary>{label}</summary><p className="research-dossier-text" translate="no">{saved.section_skills[key as TeamResearchRole]}</p></details>)}</div>
      {saved.updated_at && <p className="sector-research-note">指导更新 {dateLabel(saved.updated_at)}{saved.author?.display_name && <> · <span translate="no">{saved.author.display_name}</span></>}</p>}
      {saved.versions?.length > 0 && <details className="research-dossier-record"><summary>指导修订历史</summary>{saved.versions.map(version => <article key={version.revision}><h4>{dateLabel(version.updated_at)}</h4><p className="research-dossier-text" translate="no">{version.guidance || '—'}</p>{Object.entries(roles).map(([key, label]) => <details key={key}><summary>{label}</summary><p className="research-dossier-text" translate="no">{version.section_skills[key as TeamResearchRole]}</p></details>)}</article>)}</details>}
    </>}
  </section>
}
