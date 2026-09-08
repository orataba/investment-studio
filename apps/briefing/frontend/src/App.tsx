import { useEffect, useState } from 'react'
import InfoHint from '../../../../packages/ui/src/InfoHint'
import { useModalDialog } from '../../../../packages/ui/src/useModalDialog'
import { LanguageSelector, useLanguage } from '../../../../packages/ui/src/i18n'
import { resolveWorkspaceUrl, withLanguage } from '../../../../packages/ui/src/navigation'
import type { CitedItem, ReportDetail, ReportSummary, ReportType, Source } from './types'
import { safeUrl, SourceEvidence } from './SourceEvidence'

const api = (import.meta.env.VITE_API_BASE_URL || '/api/briefing').replace(/\/$/, '')
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${api}${path}`, { credentials: 'include', ...options })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`)
  }
  return response.json()
}
const pending = (status: string) => status === 'queued' || status === 'running'
const signed = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)}%`

function useCopy() {
  const { language } = useLanguage()
  return (zh: string, en: string) => language === 'zh-Hans' ? zh : en
}

function reportLocation() {
  const params = new URLSearchParams(window.location.search)
  return { kind: params.get('type') === 'weekly' ? 'weekly' as const : 'daily' as const, selected: params.get('report') || '' }
}

function Coverage({ detail }: { detail: ReportDetail }) {
  const copy = useCopy()
  const text = detail.coverage.text
  const documents = detail.coverage.documents
  const notes = Array.isArray(detail.coverage.numeric) ? detail.coverage.numeric : []
  const channels = Array.isArray(text.sources) ? text.sources as Record<string, unknown>[] : []
  const numericStatus: Record<string, string> = { available: copy('已覆盖', 'Available'), insufficient_history: copy('可比较历史不足', 'Insufficient history'), missing_comparable_prices: copy('缺少可比较价格', 'Comparable prices missing'), unavailable: copy('尚无可用数据', 'Unavailable'), unresolved_security: copy('尚未匹配到明确的上市证券', 'No confirmed listed security') }
  const channelStatus: Record<string, string> = { active: copy('可用', 'Available'), succeeded: copy('成功', 'Succeeded'), failed: copy('失败', 'Failed'), partial: copy('部分完成', 'Partially completed'), skipped: copy('本轮跳过', 'Skipped this run') }
  return <>
    <dl className="source-dates"><div><dt>{copy('最近收到资料', 'Latest receipt')}</dt><dd>{String(text.latest_received_at || '—')}</dd></div><div><dt>{copy('来源观测截止', 'Source observation cutoff')}</dt><dd>{String(text.latest_source_observed_at || '—')}</dd></div><div><dt>{copy('已接收资料包', 'Received packages')}</dt><dd>{String(text.bundle_count || 0)}</dd></div></dl>
    {documents && <><p>{copy('本期发布或源站观测', 'Published or source-observed this period')} {documents.current ?? 0} · {copy('本机补录的历史资料', 'Locally received historical material')} {documents.late_received ?? 0}</p><p>{copy('完整正文', 'Full text')} {documents.by_completeness?.full_text ?? 0} · {copy('来源节选', 'Source excerpts')} {documents.by_completeness?.source_excerpt ?? 0} · {copy('正文不可用', 'Content unavailable')} {documents.by_completeness?.unavailable ?? 0}</p></>}
    {notes.length > 0 && <><h3>{copy('数值数据覆盖', 'Numeric coverage')}</h3><ul>{notes.map((note, index) => <li key={index}>{typeof note === 'string' ? note : [note.symbol || note.dataset, note.message || note.reason || note.note || numericStatus[note.status] || note.status, note.effective_date || note.latest_date].filter(Boolean).join(' · ')}</li>)}</ul></>}
    {channels.length > 0 && <><h3>{copy('资讯渠道', 'News channels')}</h3><ul>{channels.map((channel, index) => {
      const discovery = channel.latest_discovery as { status?: string; failure_reason?: string } | undefined
      const run = channel.latest_run as { status?: string; accepted_count?: number; failed_count?: number } | undefined
      const reason = discovery?.failure_reason
      return <li key={index}>
        <strong>{String(channel.source_name || channel.channel_id || '')}</strong>
        {' · '}{copy('发现', 'Discovery')}：{channelStatus[discovery?.status || ''] || copy('未保留记录', 'No retained record')}
        {' · '}{copy('采集', 'Collection')}：{channelStatus[run?.status || ''] || copy('未保留记录', 'No retained record')}
        {run?.accepted_count != null && ` · ${copy('接收', 'Accepted')} ${run.accepted_count}`}
        {run?.failed_count != null && run.failed_count > 0 && ` · ${copy('失败', 'Failed')} ${run.failed_count}`}
        {reason && <p className="muted">{reason.includes('HTTP 402') ? copy('来源服务额度不足，未取得本轮资料。', 'Source service quota is insufficient; no material was retrieved this run.') : reason}</p>}
      </li>
    })}</ul></>}
  </>
}

export function ReportBody({ detail, onSource, canReadSources = true }: { detail: ReportDetail; onSource: (source: string) => void; canReadSources?: boolean }) {
  const copy = useCopy()
  const sectionTitles = { takeaway_section: copy('重点信息', 'Key developments'), topic_recommendations: copy('本周话题推荐', 'Weekly topics'), opportunity_leads: copy('新机会线索', 'Research leads') }
  const groupTitle = (title: string) => ({ 宏观: copy('宏观', 'Macro'), 微观: copy('微观', 'Companies and industries') }[title] || title)
  const sourceMap = new Map(detail.sources.map(source => [source.source_id, source]))
  const relatedSymbols = new Set(detail.report?.sections.flatMap(section => [
    ...(section.items || []), ...(section.groups?.flatMap(group => group.items) || []),
  ]).flatMap(item => item.related_market_symbols))
  const displayedMarketRows = detail.market_rows.map((row, index) => ({ row, index }))
    .filter(({ row }) => row.asset_type !== 'equity' || relatedSymbols.has(row.symbol))
  const numericLabels = new Map<string, string>()
  detail.market_rows.forEach((row, index) => {
    numericLabels.set(`market-row:${index}`, row.label)
    row.source_ids.forEach(id => numericLabels.set(id, row.label))
  })
  detail.macro_rows.forEach((row, index) => {
    numericLabels.set(`macro-row:${index}`, row.label)
    row.source_ids.forEach(id => numericLabels.set(id, row.label))
  })
  const units: Record<string, string> = { percent: '%', index: copy('点', 'index points') }
  const renderItem = (item: CitedItem, index: number) => <article className="briefing-item" key={item.title}>
    <span className="entry-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
    <div className="entry-content">
    <h4 translate="no">{item.title}</h4>
    <div className="entry-meta" translate="no">
      {item.tags?.map(tag => <span className="topic-tag" key={tag}>{tag}</span>)}
      {item.related_market_symbols.map(symbol => {
        const rowIndex = detail.market_rows.findIndex(row => row.symbol === symbol)
        const row = detail.market_rows[rowIndex]
        return row && <button className="market-chip" key={symbol} onClick={() => onSource(`market-row:${rowIndex}`)} title={`${row.symbol} · ${row.start_date} → ${row.end_date}`}>
          {row.label} <b className={row.return_pct < 0 ? 'negative' : 'positive'}>{signed(row.return_pct)}</b>
          <small>{row.start_date.slice(5)} → {row.end_date.slice(5)}</small>
        </button>
      })}
    </div>
    <div translate="no" className="briefing-prose">
      {[
        [copy('简述', 'Summary'), item.summary], [copy('影响', 'Implication'), item.analysis],
        [copy('切入点', 'Angle'), item.angle], [copy('本周变化', 'Why now'), item.why_now],
        [copy('讨论焦点', 'Discussion'), item.debate], [copy('线索', 'Lead'), item.clue],
        [copy('本周变化', 'This week'), item.this_week], [copy('关注方向', 'Research direction'), item.possible_opportunity],
      ].map(([label, paragraph], index) => paragraph && <p key={index}><span className="prose-label">{label}</span>{paragraph}</p>)}
    </div>
    <div className="source-links"><span>{copy('来源', 'Sources')}</span>{!item.source_ids.length && <span className="negative">{copy('未保留来源。', 'No sources were retained.')}</span>}{item.source_ids.map(id => {
      const source = sourceMap.get(id)
      const label = numericLabels.get(id) || source?.source_name || source?.title || source?.symbol || copy('来源', 'Source')
      const url = safeUrl(source?.url)
      return url ? <a key={id} href={url} target="_blank" rel="noreferrer" title={source?.title}>{label} ↗</a>
        : <button key={id} disabled={!canReadSources && !numericLabels.has(id)} onClick={() => onSource(id)} title={source?.title}>{label}</button>
    })}</div>
    </div>
  </article>
  return <>
    {detail.report?.sections.map(section => <section className="report-section" key={section.kind}>
      <h2>{sectionTitles[section.kind]}</h2>
      {section.groups?.map(group => <div className="report-group" key={group.title}>
        <h3>{groupTitle(group.title)}</h3>
        {group.items.length ? group.items.map(renderItem) : <p className="muted">{copy('本期没有新增条目。', 'No new items in this section.')}</p>}
      </div>)}
      {section.items && (section.items.length ? section.items.map(renderItem) : <p className="muted">{copy('本期没有形成新的机会线索。', 'No new opportunity leads in this period.')}</p>)}
    </section>)}
    <section className="report-section">
      <div className="section-heading"><h2>{copy('市场表现', 'Market performance')}</h2><InfoHint label={copy('市场表现口径', 'Market performance basis')} detail={copy('各市场按实际收盘日计算价格涨跌，不含分红；点击资产可查看价格口径。', 'Price changes use each market’s actual closing dates and exclude dividends. Select an asset for its price basis.')} /></div>
      {displayedMarketRows.length ? <div className="table-scroll"><table><thead><tr><th>{copy('资产', 'Asset')}</th><th>{copy('起始日', 'Start')}</th><th>{copy('截至日', 'As of')}</th><th className="number">{copy('收盘', 'Close')}</th><th className="number">{detail.report_type === 'weekly' ? copy('本周涨跌', 'Week to date') : copy('日涨跌', 'Daily change')}</th></tr></thead><tbody>
        {displayedMarketRows.map(({ row, index }) => <tr key={row.symbol}><td><button className="table-source" onClick={() => onSource(`market-row:${index}`)}>{row.label}</button><small>{row.symbol}</small></td><td>{row.start_date}</td><td>{row.end_date}</td><td className="number">{row.end_close.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td><td className={`number ${row.return_pct < 0 ? 'negative' : 'positive'}`}>{signed(row.return_pct)}</td></tr>)}
      </tbody></table></div> : <p className="muted">{copy('当前没有可计算的行情数据。', 'No market series can be calculated for this report.')}</p>}
    </section>
    {detail.macro_rows.length > 0 && <section className="report-section"><h2>{copy('宏观数据', 'Macro data')}</h2><div className="table-scroll"><table><thead><tr><th>{copy('指标', 'Indicator')}</th><th>{copy('数据日期', 'Observation date')}</th><th className="number">{copy('数值', 'Value')}</th></tr></thead><tbody>{detail.macro_rows.map((row, index) => <tr key={`${row.symbol}-${row.date}`}><td><button className="table-source" onClick={() => onSource(`macro-row:${index}`)}>{row.label}</button></td><td>{row.date}</td><td className="number">{row.value} {row.unit && (units[row.unit] || row.unit)}</td></tr>)}</tbody></table></div></section>}
  </>
}

export default function App() {
  const copy = useCopy()
  const { language } = useLanguage()
  const [kind, setKind] = useState<ReportType>(() => reportLocation().kind)
  const [rows, setRows] = useState<ReportSummary[]>([])
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = useState(() => reportLocation().selected)
  const [detail, setDetail] = useState<ReportDetail | null>(null)
  const [source, setSource] = useState<Source | null>(null)
  const [sourceId, setSourceId] = useState('')
  const [sourceError, setSourceError] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [generating, setGenerating] = useState(false)
  const [canGenerate, setCanGenerate] = useState(false)
  const [canReadSources, setCanReadSources] = useState(false)
  const [available, setAvailable] = useState<boolean | null>(null)
  const [revision, setRevision] = useState(0)
  const [limit, setLimit] = useState(30)
  const [showGenerate, setShowGenerate] = useState(false)
  const [cutoff, setCutoff] = useState('')
  const sourceOpen = Boolean(sourceId || source)
  const sourceRef = useModalDialog(sourceOpen, closeSource)
  const statusText = (status: string) => ({ queued: copy('等待生成', 'Queued'), running: copy('正在生成', 'Generating'), completed: copy('已完成', 'Completed'), failed: copy('未完成', 'Failed') }[status] || status)
  const stamp = (value: string) => new Date(value).toLocaleString(language === 'zh-Hans' ? 'zh-CN' : 'en-GB', { timeZone: detail?.window.timezone || 'Asia/Shanghai', hour12: false })

  useEffect(() => { document.title = `${language === 'zh-Hans' ? '市场简报' : 'Market Briefing'} · Investment Studio` }, [language])
  useEffect(() => {
    function restoreLocation() {
      const route = reportLocation()
      setKind(route.kind); setSelected(route.selected); setDetail(null); closeSource(); setError(''); setDetailError(''); setLimit(30); setLoading(true)
    }
    window.addEventListener('popstate', restoreLocation)
    return () => window.removeEventListener('popstate', restoreLocation)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    request<{ harness_available: boolean; can_generate: boolean; can_read_sources: boolean }>('/status', { signal: controller.signal }).then(result => { setAvailable(result.harness_available); setCanGenerate(result.can_generate); setCanReadSources(result.can_read_sources) }).catch(reason => { if (!controller.signal.aborted) setError(String(reason.message)) })
    return () => controller.abort()
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    Promise.all(Array.from({ length: Math.ceil(limit / 30) }, (_, page) => request<{ rows: ReportSummary[]; total: number }>(`/reports?report_type=${kind}&limit=30&offset=${page * 30}`, { signal: controller.signal }))).then(pages => {
      if (controller.signal.aborted) return
      const result = { rows: pages.flatMap(page => page.rows), total: pages[0].total }
      setRows(result.rows); setTotal(result.total); setLoading(false)
      if (!selected && result.rows.length) choose((result.rows.find(row => row.status === 'completed') || result.rows[0]).report_id, true)
    }).catch(reason => { if (!controller.signal.aborted) { setError(String(reason.message)); setLoading(false) } })
    return () => controller.abort()
  }, [kind, revision, limit, selected])
  useEffect(() => {
    if (!selected) { setDetail(null); setDetailLoading(false); return }
    const controller = new AbortController()
    setDetailLoading(true); setDetailError('')
    request<ReportDetail>(`/reports/${selected}`, { signal: controller.signal }).then(result => {
      if (controller.signal.aborted) return
      setDetail(result); setKind(result.report_type); setDetailLoading(false)
      const url = new URL(window.location.href); url.searchParams.set('type', result.report_type)
      window.history.replaceState(null, '', url)
    }).catch(reason => { if (!controller.signal.aborted) { setDetailError(String(reason.message)); setDetailLoading(false) } })
    return () => controller.abort()
  }, [selected, revision])
  useEffect(() => {
    if (!sourceId || !selected || !canReadSources) return
    const controller = new AbortController()
    setSource(null); setSourceError('')
    request<Source>(`/reports/${selected}/sources/${encodeURIComponent(sourceId)}`, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setSource(result)
    }).catch(reason => { if (!controller.signal.aborted) setSourceError(String(reason.message)) })
    return () => controller.abort()
  }, [selected, sourceId, canReadSources])
  useEffect(() => {
    if (!rows.some(row => pending(row.status))) return
    const timer = window.setInterval(() => setRevision(value => value + 1), 5000)
    return () => window.clearInterval(timer)
  }, [rows])
  function choose(id: string, replace = false) {
    if (id === selected) return
    setSelected(id); setDetail(null); closeSource(); setError(''); setDetailError('')
    const url = new URL(window.location.href); url.searchParams.set('report', id); url.searchParams.set('type', kind)
    if (replace) window.history.replaceState(null, '', url)
    else window.history.pushState(null, '', url)
  }
  function changeKind(value: ReportType) {
    if (value === kind) return
    setKind(value); setSelected(''); setRows([]); setDetail(null); closeSource(); setError(''); setDetailError(''); setLimit(30); setLoading(true)
    const url = new URL(window.location.href); url.searchParams.delete('report'); url.searchParams.set('type', value); window.history.pushState(null, '', url)
  }
  function closeSource() {
    setSourceId(''); setSource(null); setSourceError('')
  }
  function showSource(id: string) {
    if (!canReadSources) {
      const [kind, index] = id.split(':')
      const row = kind === 'market-row' ? detail?.market_rows[Number(index)] : kind === 'macro-row' ? detail?.macro_rows[Number(index)] : undefined
      if (row) setSource({ ...row, source_id: id, source_type: kind === 'market-row' ? 'market_row' : 'macro_row' })
      return
    }
    if (id !== sourceId) { setSource(null); setSourceError('') }
    setSourceId(id)
  }
  async function generate() {
    setGenerating(true); setError('')
    try {
      const result = await request<ReportSummary>('/reports', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ report_type: kind, cutoff: cutoff || new Date().toISOString() }) })
      choose(result.report_id); setRevision(value => value + 1); setShowGenerate(false)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setGenerating(false) }
  }
  return <main className="briefing-shell">
    <header className="briefing-masthead"><nav className="workspace-breadcrumbs" aria-label={copy('工作区导航', 'Workspace navigation')}><a data-workspace-link href={withLanguage(resolveWorkspaceUrl(import.meta.env.VITE_HOME_URL, 'home'), language)}>{copy('首页', 'Home')}</a><span aria-hidden="true">/</span><span>{copy('市场简报', 'Market Briefing')}</span><span aria-hidden="true">/</span><span aria-current="page">{kind === 'daily' ? copy('日报', 'Daily') : copy('周报', 'Weekly')}</span></nav><LanguageSelector /></header>
    <div className="briefing-heading"><div className="section-heading"><h1>{copy('市场简报', 'Market Briefing')}</h1><InfoHint label={copy('关于市场简报', 'About Market Briefing')} detail={copy('关注值得理解的变化，保留每一份判断的依据。', 'Understand meaningful changes, with the evidence behind each edition.')} /></div>{canGenerate && <div className="generation-actions"><button className="primary-button" onClick={() => setShowGenerate(value => !value)} disabled={available === false}>{copy('生成本期', 'Generate edition')}</button>{available === false && <InfoHint tone="warning" label={copy('生成不可用', 'Generation unavailable')} detail={copy('报告生成环境尚未配置。已完成报告仍可阅读。', 'Report generation is not configured. Completed editions remain available.')} />}</div>}</div>
    {showGenerate && <section className="generate-panel"><div className="section-heading"><h2>{kind === 'daily' ? copy('生成日报', 'Generate daily report') : copy('生成周报', 'Generate weekly report')}</h2><InfoHint label={copy('生成口径', 'Generation basis')} detail={copy('默认以当前时刻为截止时间。日报读取过去24小时；周报重新读取本周一至截止时刻的资料。重新生成会保留为新版本。', 'Uses the current time by default. Daily reports cover 24 hours; weekly reports reread evidence from Monday. Regeneration creates a new version.')} /></div><label>{copy('指定截止时间（含时区，可留空）', 'Cutoff timestamp (include timezone, optional)')}<input type="text" value={cutoff} onChange={event => setCutoff(event.target.value)} placeholder="2026-09-07T22:45:00+08:00" /></label><div><button className="primary-button" onClick={generate} disabled={generating}>{generating ? copy('正在提交…', 'Submitting…') : copy('开始生成', 'Start generation')}</button><button onClick={() => setShowGenerate(false)}>{copy('取消', 'Cancel')}</button></div></section>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="briefing-tabs" role="tablist" aria-label={copy('报告类型', 'Report type')}>{(['daily', 'weekly'] as const).map(type => <button role="tab" aria-selected={kind === type} key={type} onClick={() => changeKind(type)}>{type === 'daily' ? copy('日报', 'Daily') : copy('周报', 'Weekly')}</button>)}</div>
    <div className="briefing-layout"><aside className="edition-index" aria-busy={loading}><h2>{copy('历史期刊', 'Editions')}</h2>{loading && <p role="status" className="muted">{copy('加载中', 'Loading')}</p>}{rows.map(row => <button className={`edition ${selected === row.report_id ? 'selected' : ''}`} key={row.report_id} onClick={() => choose(row.report_id)}><strong>{row.report_date}</strong><span>{copy('版本', 'Version')} {row.version}<small className={row.status === 'failed' ? 'negative' : ''}>{statusText(row.status)}</small></span></button>)}{total > rows.length && <button className="load-more" onClick={() => setLimit(value => value + 30)}>{copy('更多期刊', 'More editions')}</button>}</aside>
      <div className="edition-content" aria-busy={detailLoading || loading}>
        {!detail && (loading || detailLoading || Boolean(selected && !detailError)) && <div className="edition-skeleton" role="status" aria-label={copy('加载中', 'Loading')}><div className="edition-skeleton-header" /><div className="edition-skeleton-facts" />{[0, 1, 2].map(index => <div className="edition-skeleton-section" key={index}><span /><span /><span /></div>)}</div>}
        {!detail && !selected && !loading && !error && <p className="muted">{copy('尚无报告。', 'No editions yet.')}</p>}
        {detailError && <div className="error" role="alert">{detailError} <button onClick={() => setRevision(value => value + 1)}>{copy('重试', 'Retry')}</button></div>}
      {detail && <><header className="edition-header"><p className="eyebrow">{copy('版本', 'VERSION')} {detail.version} · <span role={pending(detail.status) ? 'status' : undefined}>{statusText(detail.status)}</span>{pending(detail.status) && <InfoHint label={copy('生成进度', 'Generation progress')} detail={copy('正在整理本期材料并生成报告，完成后此处自动更新。', 'Preparing evidence and composing this edition. This page updates when it completes.')} />}{detail.edition_role === 'preview' ? ` · ${copy('本地预览', 'LOCAL PREVIEW')}` : ''}</p><h2>{detail.report_type === 'daily' ? copy('投研日报', 'Daily research briefing') : copy('投研周报', 'Weekly research briefing')} | {detail.report_date}</h2><dl className="edition-facts"><div><dt>{copy('资料窗口', 'Evidence window')}</dt><dd>{detail.window.period_start && stamp(detail.window.period_start)} — {stamp(detail.cutoff)}</dd></div><div><dt>{copy('时区', 'Timezone')}</dt><dd>{detail.window.timezone}</dd></div><div><dt>{copy('原始材料', 'Original materials')}</dt><dd>{detail.source_count}</dd></div></dl></header>
        {detail.status === 'failed' && <p className="error" role="alert">{detail.error}</p>}
        {detail.report && <ReportBody detail={detail} onSource={showSource} canReadSources={canReadSources} />}
        <details className="coverage"><summary>{copy('来源与覆盖范围', 'Sources and coverage')}</summary><p>{copy('报告保留生成时使用的原文与数值版本；日期未知与未覆盖内容不会被补写为事实。', 'Each edition retains its original evidence and numeric versions. Unknown dates and missing coverage remain explicit.')}</p>{canReadSources && <Coverage detail={detail} />}<ul className="all-sources">{detail.sources.filter(item => item.source_type === 'public_document').map(item => <li key={item.source_id}>{safeUrl(item.url) ? <a href={safeUrl(item.url)} target="_blank" rel="noreferrer" translate="no">{item.title} ↗</a> : <span translate="no">{item.title}</span>}<span>{item.source_name} · {item.published_at || copy('首发时间未知', 'Publication time unknown')}{canReadSources && <> · <button onClick={() => showSource(item.source_id)}>{copy('留存版本', 'Retained version')}</button></>}</span></li>)}</ul></details>
      </>}
      </div>
    </div>
    {sourceOpen && <div className="source-backdrop" onClick={event => {
      if (event.target === event.currentTarget) closeSource()
    }}>
      <div ref={sourceRef} role="dialog" aria-modal="true" tabIndex={-1} className="source-panel" aria-label={copy('来源原文', 'Source evidence')} aria-busy={!source && !sourceError}>
        {source ? <SourceEvidence source={source} onClose={closeSource} onSource={showSource} /> : <>
          <div className="source-panel-title"><h2>{copy('来源原文', 'Source evidence')}</h2><button onClick={closeSource} aria-label={copy('关闭来源', 'Close source')}>×</button></div>
          {sourceError ? <p role="alert" className="error">{sourceError}</p> : <p role="status" className="muted">{copy('加载中', 'Loading')}</p>}
        </>}
      </div>
    </div>}
  </main>
}
