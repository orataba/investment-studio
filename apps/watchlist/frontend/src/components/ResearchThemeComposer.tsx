import { useEffect, useState, type FormEvent } from 'react'
import { createResearchTheme, getResearchThemes, updateResearchTheme, type ResearchTheme, type ResearchThemeInput, type ResearchThemeKind } from '../lib/researchDossierApi'
import { announceResearchPublication } from '../lib/researchUpdates'

export default function ResearchThemeComposer({ instrumentId, title, background, kind, reference, onCancel, onSaved }: {
  instrumentId: string; title: string; background: string; kind: ResearchThemeKind; reference: ResearchThemeInput['reference']; onCancel: () => void; onSaved: (action: 'created' | 'linked', message?: string) => void
}) {
  const [name, setName] = useState(title)
  const [question, setQuestion] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [themes, setThemes] = useState<ResearchTheme[]>([])
  const [selectedTheme, setSelectedTheme] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    void getResearchThemes(instrumentId, controller.signal).then(data => { if (!controller.signal.aborted) setThemes(data.themes.filter(theme => theme.status === 'active')) })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '已有主题读取失败') })
    return () => controller.abort()
  }, [instrumentId])
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!selectedTheme && !name.trim()) return
    setSaving(true); setError('')
    try {
      const result = selectedTheme ? await updateResearchTheme(instrumentId, selectedTheme, { reference })
        : await createResearchTheme(instrumentId, { title: name.trim(), question: question.trim(), kind, background, reference })
      onSaved(selectedTheme ? 'linked' : 'created', result.research_message); announceResearchPublication([instrumentId])
    } catch (reason) { setError(reason instanceof Error ? reason.message : '主题创建失败') }
    finally { setSaving(false) }
  }
  return <form className="research-theme-editor" aria-label="从研究建立主题" onSubmit={event => void save(event)}>
    {themes.length > 0 && <label>跟踪主题<select value={selectedTheme} disabled={saving} onChange={event => setSelectedTheme(event.target.value)}><option value="">建立新主题</option>{themes.map(theme => <option key={theme.theme_id} value={theme.theme_id}>{theme.title}</option>)}</select></label>}
    {!selectedTheme && <><label>主题名称<input required value={name} disabled={saving} onChange={event => setName(event.target.value)} /></label><label>希望验证的问题（可选）<textarea rows={2} value={question} disabled={saving} onChange={event => setQuestion(event.target.value)} /></label></>}
    <details><summary>关联的研究背景</summary><p translate="no">{background}</p></details>
    {error && <p role="alert">{error}</p>}
    <div className="research-theme-actions"><button type="submit" disabled={saving || (!selectedTheme && !name.trim())}>{saving ? '保存中…' : selectedTheme ? '关联并继续研究' : '创建并开始研究'}</button><button type="button" disabled={saving} onClick={onCancel}>取消</button></div>
  </form>
}
