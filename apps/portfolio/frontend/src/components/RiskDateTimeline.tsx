import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'

export default function RiskDateTimeline({ dates, value, onChange, label }: {
  dates: string[]; value: string; onChange: (value: string) => void; label: string
}) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  if (!dates.length) return null
  const selectedIndex = Math.max(0, dates.indexOf(value))
  const invalid = Boolean(draft && !dates.includes(draft))
  return <div className="risk-date-selector">
    <label>
      <span>{zh ? '截至' : 'As of'}</span>
      <input type="date" value={draft} min={dates[0]} max={dates[dates.length - 1]} aria-label={label} aria-invalid={invalid}
        onChange={(event) => { const next = event.target.value; setDraft(next); if (dates.includes(next)) onChange(next) }} />
    </label>
    <input className="risk-date-slider" type="range" min={0} max={dates.length - 1} step={1} value={selectedIndex}
      aria-label={zh ? '拖动查看相关性历史' : 'Correlation history timeline'} aria-valuetext={value}
      disabled={dates.length < 2} onChange={(event) => { const next = dates[Number(event.target.value)]; setDraft(next); onChange(next) }} />
    <button type="button" className="secondary-button" onClick={() => { const latest = dates[dates.length - 1]; setDraft(latest); onChange(latest) }}>{zh ? '最新' : 'Latest'}</button>
    {invalid ? <span role="status">{zh ? '该日没有收益观察，请选择范围内已有观察的日期。' : 'No return observation exists on this date. Choose an observed date within the available range.'}</span> : null}
  </div>
}

