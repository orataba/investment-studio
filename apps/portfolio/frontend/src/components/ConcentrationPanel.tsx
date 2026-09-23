import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import { usePortfolioAccess } from './PortfolioAccessProvider'
import InfoHint from './InfoHint'
import FcnAllocationEditor from './FcnAllocationEditor'
import {
  concentrationScopeKey, getConcentration, type ConcentrationResponse, type ConcentrationRow,
} from '../lib/concentrationApi'
import { formatCurrency, formatPercent } from '../lib/format'
import './concentration.css'
import { concentrationMessage } from '../lib/concentrationText'

export default function ConcentrationPanel({ portfolioId, asOfDate }: { portfolioId: string; asOfDate?: string }) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const text = (en: string, cn: string) => zh ? cn : en
  const canEdit = Boolean(usePortfolioAccess()?.can_edit)
  const [data, setData] = useState<ConcentrationResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const storageKey = `investment_studio.portfolio.concentration.view.${portfolioId}`
  const [selectedScope, setSelectedScope] = useState(() => { try { return localStorage.getItem(storageKey) ?? 'security' } catch { return 'security' } })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [detail, setDetail] = useState<ConcentrationRow | null>(null)
  useEffect(() => {
    try { setSelectedScope(localStorage.getItem(storageKey) ?? 'security') } catch { setSelectedScope('security') }
    setDetail(null); setExpanded(new Set()); setData(null)
  }, [storageKey])
  useEffect(() => {
    let active = true
    setLoading(true); setError(null)
    getConcentration(portfolioId, asOfDate).then((value) => { if (active) setData(value) })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : String(reason)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [portfolioId, asOfDate, revision])
  useEffect(() => {
    const updated = (event: Event) => {
      if ((event as CustomEvent<{ portfolioId?: string }>).detail?.portfolioId === portfolioId) {
        setDetail(null); setRevision((value) => value + 1)
      }
    }
    window.addEventListener('portfolio-concentration-settings-updated', updated)
    return () => window.removeEventListener('portfolio-concentration-settings-updated', updated)
  }, [portfolioId])
  const scope = data?.scopes.find((item) => concentrationScopeKey(item) === selectedScope) ?? data?.scopes[0]
  const rowById = new Map((scope?.rows ?? []).map((row) => [row.entity_id, row]))
  const childIds = new Set((scope?.rows ?? []).map((row) => row.parent_entity_id).filter(Boolean))
  const visibleRows = (scope?.rows ?? []).filter((row) => {
    let parentId = row.parent_entity_id
    const visited = new Set<string>()
    while (parentId && rowById.has(parentId)) {
      if (visited.has(parentId) || !expanded.has(parentId)) return false
      visited.add(parentId); parentId = rowById.get(parentId)!.parent_entity_id
    }
    return true
  })
  const scale = Math.max(0.01, ...visibleRows.flatMap((row) => [row.weight ?? row.lower_bound_weight ?? 0, row.limit_weight ?? 0]))
  const coverage = [...new Set([...(data?.coverage ?? []), ...(scope?.coverage ?? [])])]
  const statuses = {
    within: text('Within limit', '限额内'), breached: text('Over limit', '超限'),
    unconfigured: text('No limit', '未设上限'), unavailable: text('Unavailable', '不可用'),
  }
  return <section className="portfolio-section-block concentration-panel" aria-label={text('Concentration', '集中度')}>
    <div className="portfolio-detail-toolbar portfolio-section-toolbar risk-section-toolbar">
      <div>
        <div className="panel-title portfolio-title-with-hint"><span>{text('Concentration', '集中度')}</span>
          <InfoHint label={text('Concentration basis', '集中度口径')} detail={text('Exposure ratio is exposure amount / portfolio NAV. Direct securities use absolute market values across accounts. FCNs use remaining nominal principal, allocated once across linked securities for taxonomy grouping. Equal allocation is a management convention, not equal risk. Cash and settlements are excluded from the numerator. Option exposure is not modeled by this projection and must not be read as zero; NAV retains its carrying amount. With incomplete data, ≥ marks the known lower bound. These ratios are not current carrying-value weights, risk contributions or potential stock delivery exposures.', '敞口比例=敞口金额/组合 NAV。直接证券按账户汇总绝对市值；FCN 按剩余名义本金，在挂钩标的之间分配后归入分类，不重复计算本金。等分属于管理约定，不代表风险均分。现金和待结算不计入分子。期权敞口未纳入此投影，不能解释为零；NAV仍保留其账面金额。数据不完整时，≥表示已知敞口下界。这些比例不是账面金额对应的当前权重、风险贡献或潜在接票股票敞口。')} />
        </div>
        <div className="portfolio-detail-meta">{data ? `${data.as_of_date} · NAV ${formatCurrency(data.nav, data.base_currency)}${data.settings_effective_from ? ` · ${text('Limits effective', '限额生效')} ${data.settings_effective_from}` : ''}` : text('Principal allocation / portfolio NAV', '本金分配 / 组合 NAV')}</div>
      </div>
      <div className="concentration-toolbar-actions">
        <label><span>{text('Group by', '分组')}</span><select aria-label={text('Concentration taxonomy', '集中度分类')} value={scope ? concentrationScopeKey(scope) : selectedScope}
          onChange={(event) => { setSelectedScope(event.target.value); setExpanded(new Set()); setDetail(null); try { localStorage.setItem(storageKey, event.target.value) } catch { /* Browsing still works without storage. */ } }}>
          {(data?.scopes ?? []).map((item) => <option key={concentrationScopeKey(item)} value={concentrationScopeKey(item)}>{item.scope === 'security' ? text('Direct securities', '直接证券') : item.scope === 'fcn' ? text('Single FCN', '单 FCN') : item.name}</option>)}
        </select></label>
        <a href={`/portfolios/${encodeURIComponent(portfolioId)}/taxonomies`}>{text('Edit limits in Taxonomies', '在分类中编辑上限')}</a>
      </div>
    </div>
    {error ? <div role="alert" className="inline-notice inline-notice-error">{concentrationMessage(error, zh)}</div> : null}
    {loading ? <p role="status" className="portfolio-detail-meta">{text('Loading concentration…', '正在加载集中度…')}</p> : null}
    {data && !error ? <>
      <div className="concentration-legend"><span><i className="concentration-fill-security" />{text('Direct securities', '直接证券')}</span><span><i className="concentration-fill-fcn" />{text('FCN principal', 'FCN 本金')}</span>
        <span>{text('Options not modeled · no stock delivery scenario', '期权敞口未建模 · 不含接票情景')}</span>
        {data.status !== 'complete' || scope?.status !== 'complete' ? <span className="concentration-status concentration-status-unavailable">{text('Incomplete coverage', '覆盖不完整')}</span> : null}
      </div>
      {scope?.enabled === false ? <p className="portfolio-detail-meta">{text('Alerts for this taxonomy are off. Saved limits remain visible.', '此分类提醒已关闭，已填上限仍保留。')}</p> : null}
      {coverage.length ? <details className="concentration-coverage"><summary>{text('Coverage and calculation notes', '覆盖与计算说明')} ({coverage.length})</summary><ul>{coverage.map((note) => <li key={note}>{concentrationMessage(note, zh)}</li>)}</ul></details> : null}
      {visibleRows.length ? <HorizontalTableScroll className="concentration-table-wrap"><table className="concentration-table" aria-label={text('Concentration exposures and limits', '集中度敞口及限额')}>
        <thead><tr><th>{text('Member', '成员')}</th><th>{text('Exposure Mix', '敞口构成')}</th><th>{text('Exposure Ratio', '敞口比例')}</th><th>{text('Concentration Limit', '集中度上限')}</th><th>{text('Limit Headroom', '上限余量')} <InfoHint label={text('Limit headroom basis', '上限余量口径')} detail={text('Concentration limit minus exposure ratio, expressed in percentage points. A negative value means the limit is exceeded. It is not an available cash amount.', '集中度上限减敞口比例，以百分点表示；负数表示已超限，不是可用现金金额。')} /></th><th>{text('Status', '状态')}</th></tr></thead>
        <tbody>{visibleRows.map((row) => <tr key={row.entity_id}>
          <td><div className="concentration-row-name" style={{ paddingLeft: `${Math.min(row.depth ?? 0, 8) * 14}px` }}>
            {childIds.has(row.entity_id) ? <button type="button" aria-label={`${expanded.has(row.entity_id) ? text('Collapse', '收起') : text('Expand', '展开')} ${row.name}`} aria-expanded={expanded.has(row.entity_id)} onClick={() => setExpanded((current) => { const next = new Set(current); if (next.has(row.entity_id)) next.delete(row.entity_id); else next.add(row.entity_id); return next })}>{expanded.has(row.entity_id) ? '▾' : '▸'}</button> : null}
            <button type="button" className="concentration-source-button" onClick={() => setDetail(row)}>{row.entity_id.startsWith('unassigned:') ? text('Unclassified', '未分类') : row.name}</button>
          </div></td>
          <td className="concentration-bar-cell"><div className="concentration-track" aria-hidden="true">
            <span className="concentration-fill-security" style={{ width: `${data.nav && data.nav > 0 ? Math.min(100, (row.security_exposure_base ?? 0) / data.nav / scale * 100) : 0}%` }} />
            <span className="concentration-fill-fcn" style={{ width: `${data.nav && data.nav > 0 ? Math.min(100, (row.fcn_exposure_base ?? 0) / data.nav / scale * 100) : 0}%` }} />
            {row.limit_weight != null ? <i className="concentration-limit-marker" style={{ left: `${row.limit_weight / scale * 100}%` }} /> : null}
          </div></td>
          <td title={row.exposure_base == null ? `${text('Known exposure', '已知敞口')} ${formatCurrency(row.known_exposure_base, data.base_currency)}` : formatCurrency(row.exposure_base, data.base_currency)}>{row.weight == null && row.lower_bound_weight != null ? `≥ ${formatPercent(row.lower_bound_weight)}` : formatPercent(row.weight)}</td><td>{formatPercent(row.limit_weight)}</td><td>{formatPercent(row.headroom_weight)}</td>
          <td><span className={`concentration-status concentration-status-${row.status}`}>{scope?.enabled === false ? text('Alerts off', '提醒已关闭') : statuses[row.status]}</span>{row.coverage.length ? <InfoHint label={text('Row coverage', '此行覆盖说明')} detail={row.coverage.map((note) => concentrationMessage(note, zh)).join(' · ')} /> : null}</td>
        </tr>)}</tbody>
      </table></HorizontalTableScroll> : !loading ? <div className="risk-chart-empty">{text('No exposure in this scope.', '此范围暂无敞口。')}</div> : null}
      {canEdit && data.fcn_contracts.length > 0 ? <div hidden={scope?.scope !== 'fcn'}><FcnAllocationEditor key={`${portfolioId}:${data.as_of_date}`} portfolioId={portfolioId} data={data} /></div> : null}
    </> : null}
    {detail && data ? <SourceDrawer row={detail} currency={data.base_currency} underlyingNames={new Map(data.fcn_contracts.flatMap((contract) => contract.underlyings.map((item) => [item.instrument_id, item.name] as const)))} onClose={() => setDetail(null)} /> : null}
  </section>
}

function SourceDrawer({ row, currency, underlyingNames, onClose }: { row: ConcentrationRow; currency: string; underlyingNames: Map<string, string>; onClose: () => void }) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const dialogRef = useModalDialog(true, onClose)
  return <div className="portfolio-risk-backdrop" onClick={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <div className="portfolio-risk-drawer" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="concentration-source-title" tabIndex={-1}>
      <header className="portfolio-risk-heading"><h1 id="concentration-source-title">{row.name}</h1><button type="button" onClick={onClose}>{zh ? '关闭' : 'Close'}</button></header>
      <div className="portfolio-risk-body"><p>{zh ? '敞口来源' : 'Exposure sources'} · {row.exposure_base == null ? `≥ ${formatCurrency(row.known_exposure_base, currency)}` : formatCurrency(row.exposure_base, currency)} · {row.weight == null && row.lower_bound_weight != null ? `≥ ${formatPercent(row.lower_bound_weight)}` : formatPercent(row.weight)} NAV</p>
        {row.coverage.map((note) => <p className="concentration-coverage" key={note}>{concentrationMessage(note, zh)}</p>)}
        <ul className="concentration-source-list">{row.sources.map((source, index) => <li key={`${source.source_id}:${index}`}>
          <strong>{source.title}</strong><span>{formatCurrency(source.amount_base, currency)}{source.allocation_weight != null ? ` · ${zh ? '本金分配' : 'Principal allocation'} ${formatPercent(source.allocation_weight)}` : ''}</span>
          {source.contract_id && source.instrument_id ? <span className="portfolio-detail-meta">{underlyingNames.get(source.instrument_id) ?? source.instrument_id} · {source.allocation_method === 'custom' ? (zh ? '自定义分配' : 'Custom allocation') : (zh ? '等分本金' : 'Equal allocation')}</span> : null}
          {source.detail_path?.startsWith('/portfolios/') ? <a href={source.detail_path}>{zh ? '查看持仓' : 'View holding'}</a> : null}
        </li>)}</ul>
      </div>
    </div>
  </div>
}
