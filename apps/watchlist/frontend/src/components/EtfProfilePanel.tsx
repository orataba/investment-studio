import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useEffect, useState } from 'react'
import { getInstrumentReferenceData, type InstrumentReferenceData } from '../lib/api'

type Row = Record<string, unknown>
const object = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(object) : []
const text = (value: unknown) => typeof value === 'string' || typeof value === 'number' ? String(value) : ''
function numeric(value: unknown) {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(typeof value === 'string' ? value.replace(/%$/, '') : value)
  return Number.isFinite(parsed) ? parsed : null
}
function percentage(value: unknown) {
  const number = numeric(value)
  return number === null ? '—' : `${number.toLocaleString('en-US', { maximumFractionDigits: 2 })}%`
}
function sourceDate(value: unknown, zh: boolean) {
  const raw = text(value)
  if (!raw) return zh ? '未披露' : 'Not disclosed'
  if (/^\d{8}$/.test(raw)) return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6)}`
  if (raw.length === 10) return raw
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(raw)) return `${raw} ${zh ? '（时区未披露）' : '(timezone not disclosed)'}`
  return new Date(raw).toLocaleString(zh ? 'zh-CN' : 'en-US', { hour12: false, timeZoneName: 'short' })
}
function webLink(value: unknown) {
  try { const url = new URL(text(value)); return ['http:', 'https:'].includes(url.protocol) ? url.href : null } catch { return null }
}

export default function EtfProfilePanel({ instrumentId, language }: { instrumentId: string; language: 'zh-Hans' | 'en' }) {
  const zh = language === 'zh-Hans'
  const [snapshot, setSnapshot] = useState<{ instrumentId: string; value: InstrumentReferenceData } | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setError('')
    void getInstrumentReferenceData(instrumentId).then((value) => {
      if (active) setSnapshot({ instrumentId, value })
    }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : 'ETF资料读取失败') })
    return () => { active = false }
  }, [instrumentId])
  const reference = snapshot?.instrumentId === instrumentId ? snapshot.value : null
  const info = object(reference?.sections.fund_info)
  const holdings = rows(reference?.sections.holdings)
  const fmp = reference?.provider === 'fmp'
  const count = numeric(info.holdingsCount)
  const assets = numeric(info.assetsUnderManagement)
  const website = webLink(info.website)
  const facts = [
    [zh ? '发行商 / 管理人' : 'Provider / manager', text(info.etfCompany || info.management)],
    [zh ? '资产类别' : 'Asset class', text(info.assetClass || info.fund_type)],
    [zh ? '成立日期' : 'Inception', info.inceptionDate || info.found_date ? sourceDate(info.inceptionDate || info.found_date, zh) : ''],
    [zh ? '注册地' : 'Domicile', text(info.domicile)],
    [fmp ? zh ? '总费率' : 'Expense ratio' : zh ? '管理费率' : 'Management fee', numeric(fmp ? info.expenseRatio : info.m_fee) === null ? '' : percentage(fmp ? info.expenseRatio : info.m_fee)],
    [zh ? '披露持仓数' : 'Reported holdings', count === null ? '' : count.toLocaleString('en-US')],
    [zh ? '资产规模' : 'Assets under management', assets === null ? '' : `${assets.toLocaleString('en-US', { notation: 'compact', maximumFractionDigits: 2 })} · ${zh ? '规模币种未单独披露' : 'AUM currency not separately disclosed'}`],
    [zh ? '跟踪基准' : 'Benchmark', text(info.benchmark)],
  ].filter(([, value]) => value)
  const sectorWeights = rows(reference?.sections.sector_weights)
  const countryWeights = rows(reference?.sections.country_weights)
  return <section className="panel listed-etf-profile" aria-label={zh ? 'ETF概况与持仓' : 'ETF profile and holdings'}>
    <div className="listed-etf-profile-heading"><h3>{zh ? 'ETF概况' : 'ETF profile'}</h3>{website && <a href={website} target="_blank" rel="noopener noreferrer">{zh ? '基金官网' : 'Fund website'} ↗</a>}</div>
    {error ? <p role="alert">{zh ? '基金资料暂时无法读取：' : 'Fund reference unavailable: '}{error}</p>
      : !reference ? <p className="listed-etf-source">{zh ? '加载中' : 'Loading'}</p> : <>
        {facts.length > 0 ? <dl className="listed-etf-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl> : <p className="listed-etf-source">{zh ? '尚未取得基金概况，不能据此推断基金结构。' : 'Fund profile has not been collected.'}</p>}
        <p className="listed-etf-source">{reference.provider.toUpperCase()} · {zh ? '资料采集' : 'Collected'} {sourceDate(reference.fetched_at, zh)}{info.updatedAt ? ` · ${zh ? '来源更新' : 'Source updated'} ${sourceDate(info.updatedAt, zh)}` : ''}</p>
        {Object.keys(reference.section_errors).length > 0 && <details className="listed-etf-details"><summary>{zh ? '资料覆盖限制' : 'Reference coverage limits'}</summary><ul>{Object.values(reference.section_errors).map((message) => <li key={message}>{message}</li>)}</ul></details>}
        {(info.description || info.benchmark || info.isin) && <details className="listed-etf-details"><summary>{zh ? '基金说明' : 'Fund description'}</summary>
          {info.isin ? <p>ISIN · {text(info.isin)}</p> : null}{info.description ? <p>{text(info.description)}</p> : null}{info.benchmark ? <p>{zh ? '跟踪基准' : 'Benchmark'} · {text(info.benchmark)}</p> : null}
        </details>}
        <details className="listed-etf-details"><summary>{zh ? '持仓与分布' : 'Holdings and exposures'}{holdings.length > 0 ? ` · ${zh ? '已保存' : 'Saved'} ${holdings.length} ${zh ? '行' : 'rows'}` : ''}</summary>
          <p className="listed-etf-source">{zh ? '持仓是来源披露的快照；资料采集或更新时间不等于持仓报告期。明细行数与披露持仓数口径可能不同。' : 'Holdings are disclosed snapshots. Collection and source-update times are not reporting periods; saved rows and reported holding counts may differ.'}</p>
          {fmp && count !== null && holdings.length < count && <p className="listed-etf-source">{zh ? `当前仅保存部分明细（${holdings.length} 行），基金资料披露 ${count} 项持仓。` : `Only part of the holdings is saved (${holdings.length} rows); the fund reports ${count} holdings.`}</p>}
          {holdings.length ? <HorizontalTableScroll className="table-shell listed-etf-holdings"><table className="listed-data-table"><thead><tr><th>{zh ? '标的' : 'Holding'}</th><th>{zh ? '名称' : 'Name'}</th><th>{fmp ? zh ? '权重' : 'Weight' : zh ? '占股票市值比' : 'Share of equity holdings'}</th><th>{zh ? '报告期' : 'Reporting period'}</th><th>{fmp ? zh ? '来源更新' : 'Source updated' : zh ? '披露日期' : 'Publication date'}</th></tr></thead><tbody>{holdings.map((holding, index) => <tr key={`${holding.asset || holding.symbol}:${index}`}>
            <td>{text(fmp ? holding.asset : holding.symbol) || '—'}</td><td>{text(holding.name) || '—'}</td><td>{percentage(fmp ? holding.weightPercentage : holding.stk_mkv_ratio)}</td><td>{sourceDate(holding.end_date, zh)}</td><td>{sourceDate(fmp ? holding.updatedAt : holding.ann_date, zh)}</td>
          </tr>)}</tbody></table></HorizontalTableScroll> : <p className="listed-etf-source">{zh ? '尚未取得可展示的持仓明细。' : 'No holdings have been collected.'}</p>}
          {(sectorWeights.length > 0 || countryWeights.length > 0) && <div className="listed-etf-exposures">{[{ title: zh ? '行业分布' : 'Sector exposure', data: sectorWeights, key: 'sector' }, { title: zh ? '国家 / 地区分布' : 'Country exposure', data: countryWeights, key: 'country' }].filter((group) => group.data.length > 0).map((group) => <section key={group.key}><h4>{group.title}</h4><dl>{group.data.map((item, index) => <div key={`${item[group.key]}:${index}`}><dt>{text(item[group.key])}</dt><dd>{percentage(item.weightPercentage)}</dd></div>)}</dl></section>)}</div>}
        </details>
      </>}
  </section>
}
