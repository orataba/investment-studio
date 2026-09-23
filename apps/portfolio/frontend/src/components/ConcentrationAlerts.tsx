import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { getConcentration, type ConcentrationResponse } from '../lib/concentrationApi'
import { formatPercent } from '../lib/format'
import { concentrationMessage } from '../lib/concentrationText'
import './concentration.css'

export default function ConcentrationAlerts({ portfolioId, asOfDate }: { portfolioId: string; asOfDate: string }) {
  const zh = useLanguage().language === 'zh-Hans'
  const [data, setData] = useState<ConcentrationResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    setData(null); setError(null)
    getConcentration(portfolioId, asOfDate).then((value) => { if (active) setData(value) })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : String(reason)) })
    return () => { active = false }
  }, [portfolioId, asOfDate, revision])
  useEffect(() => {
    const refresh = (event: Event) => {
      if ((event as CustomEvent<{ portfolioId?: string }>).detail?.portfolioId === portfolioId) setRevision((value) => value + 1)
    }
    window.addEventListener('portfolio-concentration-settings-updated', refresh)
    return () => window.removeEventListener('portfolio-concentration-settings-updated', refresh)
  }, [portfolioId])
  const monitored = data?.scopes.filter((scope) => scope.enabled).flatMap((scope) => scope.rows.filter((row) => row.limit_weight != null).map((row) => ({ ...row, scope }))) ?? []
  const breaches = monitored.filter((row) => row.status === 'breached')
  const unavailable = monitored.filter((row) => row.status === 'unavailable')
  if (!error && !breaches.length && !unavailable.length) return null
  const href = `/portfolios/${encodeURIComponent(portfolioId)}/holdings?view=concentration&as_of_date=${encodeURIComponent(asOfDate)}`
  return <aside className="inline-notice inline-notice-warning concentration-alerts" aria-label={zh ? '集中度提醒' : 'Concentration alerts'}>
    <div><strong>{zh ? '集中度提醒' : 'Concentration alerts'}</strong> · <a href={href}>{zh ? '查看持仓敞口' : 'View holding exposures'}</a></div>
    {error ? <p>{zh ? '集中度暂不可用：' : 'Concentration unavailable: '}{concentrationMessage(error, zh)}</p> : null}
    {breaches.length ? <ul>{breaches.slice(0, 3).map((row) => <li key={`${row.scope.scope}:${row.scope.taxonomy_id}:${row.entity_id}`}>
      {row.name}{row.scope.scope === 'taxonomy' ? ` · ${row.scope.name}` : ''}：{row.weight == null ? '≥ ' : ''}{formatPercent(row.weight ?? row.lower_bound_weight)} / {zh ? '上限' : 'limit'} {formatPercent(row.limit_weight)}
    </li>)}</ul> : null}
    {breaches.length > 3 ? <p>{zh ? `共 ${breaches.length} 项超限` : `${breaches.length} limits exceeded`}</p> : null}
    {unavailable.length ? <p>{zh ? `${unavailable.length} 项已设上限缺少足够数据，暂不能判断。` : `${unavailable.length} configured limits cannot be assessed because data is incomplete.`}</p> : null}
  </aside>
}
