import { useEffect, useRef, useState } from 'react'
import { useLanguage } from '../../../../packages/ui/src/i18n'
import { EvidenceTime } from './reading'
import type { ReportType } from './types'

export default function GenerateEditionForm({ kind, busy, onCancel, onGenerate }: { kind: ReportType; busy: boolean; onCancel: () => void; onGenerate: (cutoff: string) => void }) {
  const { language } = useLanguage()
  const copy = (zh: string, en: string) => language === 'zh-Hans' ? zh : en
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [])
  const [custom, setCustom] = useState(false)
  const [cutoff, setCutoff] = useState('')
  const [offset, setOffset] = useState('+08:00')
  const [error, setError] = useState(false)
  const [openedAt] = useState(() => new Date().toISOString())
  const timestamp = custom ? `${cutoff}:00${offset}` : openedAt
  const valid = !custom || (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(cutoff) && !Number.isNaN(Date.parse(timestamp)))
  const timezone = offset === '+08:00' ? 'Asia/Shanghai' : 'UTC'
  return <form className="generate-panel" noValidate onKeyDown={event => { if (event.key === 'Escape' && !busy) { event.preventDefault(); onCancel() } }} onSubmit={event => {
    event.preventDefault()
    if (!valid) { setError(true); return }
    onGenerate(custom ? new Date(timestamp).toISOString() : new Date().toISOString())
  }}>
    <h2 ref={heading} tabIndex={-1}>{kind === 'daily' ? copy('生成日报', 'Generate daily report') : copy('生成周报', 'Generate weekly report')}</h2>
    <fieldset><legend>{copy('资料截止时间', 'Evidence cutoff')}</legend>
      <label className="cutoff-option"><input type="radio" name="cutoff-mode" checked={!custom} onChange={() => { setCustom(false); setError(false) }} />{copy('使用提交时的当前时间', 'Use the current time at submission')}</label>
      <label className="cutoff-option"><input type="radio" name="cutoff-mode" checked={custom} onChange={() => setCustom(true)} />{copy('自定义日期与时间', 'Choose a date and time')}</label>
    </fieldset>
    {custom && <div className="cutoff-fields"><label>{copy('日期与时间', 'Date and time')}<input type="datetime-local" value={cutoff} aria-invalid={error} aria-describedby={error ? 'cutoff-error' : undefined} onChange={event => { setCutoff(event.target.value); setError(false) }} /></label><label>{copy('时区', 'Timezone')}<select value={offset} onChange={event => setOffset(event.target.value)}><option value="+08:00">Asia/Shanghai · UTC+08:00</option><option value="+00:00">UTC · UTC+00:00</option></select></label></div>}
    {error && <p id="cutoff-error" className="field-error" role="alert">{copy('请选择完整、有效的日期与时间。', 'Choose a complete, valid date and time.')}</p>}
    <p className="generation-preview">{custom ? copy('有效截止时间', 'Effective cutoff') : copy('当前时间参考', 'Current time reference')}: {valid ? <><EvidenceTime value={timestamp} timezone={custom ? timezone : 'Asia/Shanghai'} /> · {custom ? timezone : 'Asia/Shanghai'}</> : '—'}<br />{copy('将创建新版本，保留已有报告。', 'Creates a new version and preserves existing editions.')}</p>
    <p>{kind === 'daily' ? copy('日报读取截止前 24 小时的资料。', 'Daily reports use evidence from the preceding 24 hours.') : copy('周报读取本周一至截止时刻的资料。', 'Weekly reports use evidence from Monday to the cutoff.')}</p>
    <div className="form-actions"><button type="button" onClick={onCancel} disabled={busy}>{copy('取消', 'Cancel')}</button><button className="primary-button" type="submit" disabled={busy}>{busy ? copy('正在提交…', 'Submitting…') : copy('开始生成', 'Start generation')}</button></div>
  </form>
}
