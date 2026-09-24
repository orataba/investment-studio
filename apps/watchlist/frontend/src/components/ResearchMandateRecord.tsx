import { useStudioAccount } from './AccountBoundary'
import { useState, type FormEvent } from 'react'
import { saveResearchMandate, type ResearchMandate, type ResearchMandateInput, type ResearchReportPreferences } from '../lib/researchDossierApi'

const fields = [
  ['mechanisms', '价格与基本面传导'], ['research_approach', '研究思路'], ['focus', '重点关注'],
  ['user_constraints', '用户指定的范围与方法约束'], ['source_plan', '资料与日程来源'], ['gaps', '尚待补齐'],
  ['user_methods', '人工指定的研究方法'],
] as const
const defaultPreferences: ResearchReportPreferences = { priority_modules: [], hidden_modules: [], summary_focus: [], detail_level: 'standard' }
const originLabels = { user: '用户', research: '研究员', initial: '初始方法' }
const authorLabel = (author: NonNullable<ResearchMandate['author']>) => {
  const role = originLabels[author.origin]
  return author.display_name && author.display_name !== role ? `${author.display_name}（${role}）` : role
}

export default function ResearchMandateRecord({ instrumentId, mandate, onSaved, readOnly = false, availableModules = [] }: {
  instrumentId: string; mandate: ResearchMandate; onSaved: (value: ResearchMandate) => void; readOnly?: boolean; availableModules?: Array<{ id: string; title: string; version: string }>
}) {
  const canWrite = useStudioAccount()?.team_role !== 'reader' && !readOnly
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<ResearchMandateInput>(mandate)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const preferences = draft.report_preferences || defaultPreferences
  const updatePreferences = (update: Partial<ResearchReportPreferences>) => setDraft({ ...draft, report_preferences: { ...preferences, ...update } })
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!canWrite) return
    setSaving(true)
    setError('')
    try {
      const saved = await saveResearchMandate(instrumentId, {
        title: draft.title, background: draft.background,
        ...(draft.module_focus ? { module_focus: draft.module_focus } : {}),
        ...(draft.report_preferences ? { report_preferences: { ...draft.report_preferences, summary_focus: draft.report_preferences.summary_focus.map(row => row.trim()).filter(Boolean) } } : {}),
        ...Object.fromEntries(fields.map(([key]) => [key, (draft[key] || []).map(row => row.trim()).filter(Boolean)])),
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
    {editing && canWrite ? <form className="research-material-form" onSubmit={event => void save(event)}>
      <label className="research-material-wide">标题<input value={draft.title} required disabled={saving} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
      <label className="research-material-wide">背景与研究边界<textarea rows={3} value={draft.background} disabled={saving} onChange={e => setDraft({ ...draft, background: e.target.value })} /></label>
      {fields.map(([key, label]) => <label key={key} className="research-material-wide">{label}（每行一项）<textarea rows={Math.min(5, Math.max(2, (draft[key] || []).length))} value={(draft[key] || []).join('\n')} disabled={saving} onChange={e => setDraft({ ...draft, [key]: e.target.value.split('\n') })} /></label>)}
      <fieldset className="research-material-wide research-module-focus"><legend>额外研究重点</legend>
        <p className="sector-research-note">在自动采用的领域方法之外，指定需要持续研究的领域与原因。移除额外重点不会删除已经保存的研究。</p>
        {(draft.module_focus || []).map((module, index) => <div className="research-module-focus-row" key={index}>
          <label>研究领域<select aria-label={`研究领域 ${index + 1}`} value={module.module_id} disabled={saving} onChange={event => setDraft({ ...draft, module_focus: draft.module_focus!.map((item, row) => row === index ? { ...item, module_id: event.target.value, source_ids: [], selected_by: null } : item) })}>
            {!availableModules.some(item => item.id === module.module_id) && <option value={module.module_id}>{module.module_id}</option>}
            {availableModules.filter(item => item.id === module.module_id || !draft.module_focus?.some(selected => selected.module_id === item.id)).map(item => <option value={item.id} key={item.id}>{item.title}</option>)}
          </select></label>
          <label>选择原因<textarea aria-label={`选择原因 ${index + 1}`} value={module.reason} required rows={2} disabled={saving} onChange={event => setDraft({ ...draft, module_focus: draft.module_focus!.map((item, row) => row === index ? { ...item, reason: event.target.value } : item) })} /></label>
          <button type="button" disabled={saving} onClick={() => setDraft({ ...draft, module_focus: draft.module_focus!.filter((_, row) => row !== index) })}>移除重点</button>
        </div>)}
        <button type="button" disabled={saving || availableModules.every(item => draft.module_focus?.some(module => module.module_id === item.id))} onClick={() => {
          const next = availableModules.find(item => !draft.module_focus?.some(module => module.module_id === item.id))
          if (next) setDraft({ ...draft, module_focus: [...(draft.module_focus || []), { module_id: next.id, reason: '', source_ids: [], selected_by: null }] })
        }}>添加研究领域</button>
      </fieldset>
      <fieldset className="research-material-wide research-report-preferences"><legend>报告阅读偏好</legend>
        <label>摘要重点（每行一项）<textarea rows={3} disabled={saving} value={preferences.summary_focus.join('\n')} onChange={event => updatePreferences({ summary_focus: event.target.value.split('\n') })} /></label>
        <label>研究篇幅<select disabled={saving} value={preferences.detail_level} onChange={event => updatePreferences({ detail_level: event.target.value as ResearchReportPreferences['detail_level'] })}><option value="concise">简明</option><option value="standard">标准</option><option value="detailed">深入</option></select></label>
        <h5>优先阅读的领域</h5>
        <div className="research-preference-options">{availableModules.map(module => <label key={module.id}><input type="checkbox" checked={preferences.priority_modules.includes(module.id)} disabled={saving} onChange={event => updatePreferences({ priority_modules: event.target.checked ? [...preferences.priority_modules, module.id] : preferences.priority_modules.filter(key => key !== module.id) })} />{module.title}</label>)}</div>
        {preferences.priority_modules.length > 1 && <ol className="research-preference-order">{preferences.priority_modules.map((key, index) => <li key={key}><span>{availableModules.find(module => module.id === key)?.title || key}</span><button type="button" aria-label={`提前${availableModules.find(module => module.id === key)?.title || key}`} disabled={saving || index === 0} onClick={() => { const order = [...preferences.priority_modules]; [order[index - 1], order[index]] = [order[index], order[index - 1]]; updatePreferences({ priority_modules: order }) }}>上移</button></li>)}</ol>}
        <h5>放入补充分析的领域</h5>
        <p className="sector-research-note">默认全文展示；移入补充分析后仍可从研究档案阅读。变化、风险和重点主题始终保留。</p>
        <div className="research-preference-options">{availableModules.filter(module => !['market-quantitative', 'events-expectations'].includes(module.id)).map(module => <label key={module.id}><input type="checkbox" checked={preferences.hidden_modules.includes(module.id)} disabled={saving} onChange={event => updatePreferences({ hidden_modules: event.target.checked ? [...preferences.hidden_modules, module.id] : preferences.hidden_modules.filter(key => key !== module.id) })} />{module.title}</label>)}</div>
      </fieldset>
      {error && <p role="alert" className="research-material-wide">{error}</p>}
      <div className="sector-research-actions research-material-wide"><button type="submit" disabled={saving || !draft.title.trim()}>{saving ? '保存中…' : '保存研究框架'}</button><button type="button" disabled={saving} onClick={() => setEditing(false)}>取消</button></div>
    </form> : <>
      <p><strong translate="no">{mandate.title}</strong></p><p className="research-dossier-text" translate="no">{mandate.background}</p>
      {Boolean(mandate.module_focus?.length) && <details className="research-dossier-record"><summary>指定的研究领域</summary><ul className="research-dossier-list">{mandate.module_focus!.map(module => <li key={module.module_id}>{availableModules.find(item => item.id === module.module_id)?.title || module.module_id} · {module.reason}</li>)}</ul></details>}
      {Boolean(mandate.user_focus?.length) && <div><h4>用户指定重点</h4><ul className="research-dossier-list">{mandate.user_focus!.map((row, i) => <li key={i} translate="no">{row}</li>)}</ul></div>}
      {mandate.report_preferences && <section className="research-preferences-summary"><h4>报告阅读偏好</h4><p>研究篇幅：{{ concise: '简明', standard: '标准', detailed: '深入' }[mandate.report_preferences.detail_level]}</p>{mandate.report_preferences.summary_focus.length > 0 && <ul>{mandate.report_preferences.summary_focus.map((item, index) => <li key={index} translate="no">{item}</li>)}</ul>}{mandate.report_preferences.priority_modules.length > 0 && <p>优先领域：{mandate.report_preferences.priority_modules.map(key => availableModules.find(module => module.id === key)?.title || key).join('、')}</p>}</section>}
      {fields.map(([key, label]) => (mandate[key] || []).length > 0 && <details className="research-dossier-record" key={key} open={key === 'focus'}><summary>{label}</summary><ul className="research-dossier-list">{(mandate[key] || []).map((row, i) => <li key={i} translate="no">{row}</li>)}</ul></details>)}
      {Boolean(mandate.versions?.length) && <details className="research-dossier-record"><summary>研究框架修订历史 · {mandate.versions!.length} 次</summary>
        {mandate.versions!.map(version => <article key={version.version_id}><h4>{version.updated_at ? new Date(version.updated_at).toLocaleString('zh-CN') : '初始版本'} · {version.title}</h4><p className="research-dossier-text" translate="no">{version.background}</p>
          {version.author && <p className="sector-research-note">更新来源：{authorLabel(version.author)}</p>}
          {Boolean(version.user_focus?.length) && <div><h4>用户指定重点</h4><ul className="research-dossier-list">{version.user_focus!.map((row, i) => <li key={i} translate="no">{row}</li>)}</ul></div>}
          {fields.map(([key, label]) => (version[key] || []).length > 0 && <div key={key}><h4>{label}</h4><ul className="research-dossier-list">{(version[key] || []).map((row, i) => <li key={i} translate="no">{row}</li>)}</ul></div>)}
        </article>)}
      </details>}
      {mandate.author && <p className="sector-research-note">更新来源：{authorLabel(mandate.author)}</p>}
      <p className="sector-research-note">{mandate.updated_at ? `更新于 ${new Date(mandate.updated_at).toLocaleString('zh-CN')}` : '初始研究框架，背景材料仍需持续核实和补齐。'}</p>
    </>}
  </section>
}
