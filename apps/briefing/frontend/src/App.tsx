import { useEffect, useRef, useState } from 'react'
import HorizontalTableScroll from '../../../../packages/ui/src/HorizontalTableScroll'
import RequestRecovery from '../../../../packages/ui/src/RequestRecovery'
import InfoHint from '../../../../packages/ui/src/InfoHint'
import WorkspaceSwitcher from '../../../../packages/ui/src/WorkspaceSwitcher'
import { LoadingNotice } from '../../../../packages/ui/src/NoticeToast'
import '../../../../packages/ui/src/notice-toast.css'
import { useModalDialog } from '../../../../packages/ui/src/useModalDialog'
import { LanguageSelector, useLanguage } from '../../../../packages/ui/src/i18n'
import { resolveWorkspaceUrl, withLanguage } from '../../../../packages/ui/src/navigation'
import type { CitedItem, ReportDetail, ReportSummary, ReportType, Source } from './types'
import { safeUrl, SourceEvidence } from './SourceEvidence'
import GenerateEditionForm from './GenerateEditionForm'
import { EvidenceTime, ExternalLinkIcon, navigateToSection } from './reading'

const api = (import.meta.env.VITE_API_BASE_URL || '/api/briefing').replace(/\/$/, '')
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${api}${path}`, { credentials: 'include', ...options })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const message = typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`
    const requestId = response.headers?.get('X-Request-ID')
    throw new Error(requestId ? `${message} [request_id=${requestId}]` : message)
  }
  return response.json()
}
const pending = (status: string) => status === 'queued' || status === 'running'
const valueClass = (value?: string | null) => !value || /^[+−-]?[\d,.]+\s*(%|bp|bps)?$/.test(value.trim()) ? 'number' : undefined
const signed = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)}%`

function useCopy() {
  const { language } = useLanguage()
  return (zh: string, en: string) => language === 'zh-Hans' ? zh : en
}

type BriefingView = ReportType | 'industry'

function reportLocation() {
  const params = new URLSearchParams(window.location.search)
  const kind: BriefingView = params.get('type') === 'industry' ? 'industry' : params.get('type') === 'weekly' ? 'weekly' : 'daily'
  return { kind, selected: kind === 'industry' ? '' : params.get('report') || '' }
}

function IndustryResearch() {
  const copy = useCopy()
  const { language } = useLanguage()
  const watchlistUrl = resolveWorkspaceUrl(import.meta.env.VITE_WATCHLIST_URL, 'watchlist')
  // This is the registered shared identity, also resolved by the destination page.
  const reportUrl = withLanguage(`${watchlistUrl}/instruments/xlk?tab=investment-research&mode=report`, language)
  return <section className="industry-research" aria-label={copy('行业研究', 'Industry research')}>
    <article className="industry-entry">
      <div className="industry-entry-summary">
        <p className="eyebrow">XLK · {copy('信息技术', 'Information technology')}</p>
        <h2>{copy('美股科技', 'U.S. technology')}</h2>
        <p>{copy('以 XLK 实际成份为研究核心，产业链其他公司作为需求、竞争与供给背景。', 'Research centers on actual XLK constituents, with other companies providing demand, competitive and supply-chain context.')}</p>
        <a className="primary-button" data-workspace-link href={reportUrl}>{copy('阅读研究报告', 'Read research report')} <span aria-hidden="true">→</span></a>
        <p className="muted industry-access">{copy('使用团队账号阅读与继续研究。', 'Use your team account to read and continue the research.')}</p>
      </div>
      <div className="industry-entry-focus">
        <h3>{copy('研究关注', 'Research focus')}</h3>
        <ul>
          <li>{copy('当前判断、本期变化与反证', 'Current assessment, changes and counterevidence')}</li>
          <li>{copy('经营与现金流、估值与预期修订', 'Operations, cash flow, valuation and estimate revisions')}</li>
          <li>{copy('相对表现、市场广度与集中度', 'Relative performance, breadth and concentration')}</li>
          <li>{copy('重要事件、舆论分歧与下次观察', 'Key events, divergent views and what to watch next')}</li>
        </ul>
      </div>
    </article>
  </section>
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
    <dl className="source-dates"><div><dt>{copy('最近收到资料', 'Latest receipt')}</dt><dd><EvidenceTime value={typeof text.latest_received_at === 'string' ? text.latest_received_at : undefined} timezone={detail.window.timezone} /></dd></div><div><dt>{copy('来源观测截止', 'Source observation cutoff')}</dt><dd><EvidenceTime value={typeof text.latest_source_observed_at === 'string' ? text.latest_source_observed_at : undefined} timezone={detail.window.timezone} /></dd></div><div><dt>{copy('已接收资料包', 'Received packages')}</dt><dd>{String(text.bundle_count || 0)}</dd></div></dl>
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

export function CoverageSummary({ detail }: { detail: ReportDetail }) {
  const copy = useCopy()
  const channels = Array.isArray(detail.coverage.text.sources) ? detail.coverage.text.sources as Record<string, unknown>[] : []
  const gaps = channels.filter(channel => {
    const discovery = channel.latest_discovery as { status?: string } | undefined
    const run = channel.latest_run as { status?: string; failed_count?: number } | undefined
    return discovery?.status === 'failed' || run?.status === 'failed' || run?.status === 'partial' || Number(run?.failed_count || 0) > 0
  })
  const prices = [...new Set(detail.market_rows.map(row => row.end_date))].sort()
  return <section className={`coverage-summary${gaps.length ? ' coverage-summary-warning' : ''}`} aria-label={copy('阅读前的覆盖说明', 'Coverage at a glance')}>
    <strong>{gaps.length ? copy(`来源覆盖有缺口：${gaps.length}/${channels.length} 个渠道`, `Source coverage gaps: ${gaps.length}/${channels.length} channels`) : copy('来源与日期', 'Sources and dates')}</strong>
    <span>{copy('行情有效日', 'Price dates')}: {prices.length ? prices.join(' · ') : copy('没有留存行情', 'No retained prices')}</span>
    <a id="coverage-entry" href="#source-coverage" onClick={event => { event.preventDefault(); navigateToSection('source-coverage') }}>{copy('查看来源与缺口', 'Inspect sources and gaps')}</a>
    {gaps.length > 0 && <details className="coverage-gaps"><summary>{copy('未完整覆盖的渠道', 'Channels with coverage gaps')}</summary><p>{gaps.map(channel => String(channel.source_name || channel.channel_id)).join(' · ')}</p></details>}
  </section>
}

export function ReportBody({ detail, onSource, canReadSources = true }: { detail: ReportDetail; onSource: (source: string) => void; canReadSources?: boolean }) {
  const copy = useCopy()
  const sectionTitles = { takeaway_section: copy('重点信息', 'Key developments'), topic_recommendations: copy('本周话题推荐', 'Weekly topics'), opportunity_leads: copy('新机会线索', 'Research leads'), macro_data_calendar: detail.report_type === 'weekly' ? copy('本周重要宏观发布', 'Important releases this week') : copy('本期重要宏观发布', 'Important releases this period') }
  const groupTitle = (title: string) => ({ 宏观: copy('宏观', 'Macro'), 微观: copy('微观', 'Companies and industries') }[title] || title)
  const sourceMap = new Map(detail.sources.map(source => [source.source_id, source]))
  const items = detail.report?.sections.flatMap(section => [
    ...(section.items || []), ...(section.groups?.flatMap(group => group.items) || []), ...(section.rows || []),
  ]) || []
  const relatedSymbols = new Set(items.flatMap(item => item.related_market_symbols))
  const citedSources = new Set(items.flatMap(item => item.source_ids))
  const displayedMarketRows = detail.market_rows.map((row, index) => ({ row, index }))
    .filter(({ row, index }) => relatedSymbols.has(row.symbol) || citedSources.has(`market-row:${index}`) || row.source_ids.some(id => citedSources.has(id)))
  const displayedMacroRows = detail.macro_rows.map((row, index) => ({ row, index }))
    .filter(({ row, index }) => citedSources.has(`macro-row:${index}`) || row.source_ids.some(id => citedSources.has(id)))
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
  const renderSources = (item: CitedItem) => <details className="citation-list"><summary>{copy('来源', 'Sources')} · {item.source_ids.length}</summary><div className="source-links"><span>{copy('来源', 'Sources')}</span>{!item.source_ids.length && <span className="negative">{copy('未保留来源。', 'No sources were retained.')}</span>}{item.source_ids.map(id => {
    const source = sourceMap.get(id)
    const label = numericLabels.get(id) || source?.title || source?.source_name || source?.symbol || copy('来源', 'Source')
    const url = safeUrl(source?.url)
    return <div className="citation" key={id}>{url ? <a href={url} target="_blank" rel="noreferrer" translate="no">{label} <ExternalLinkIcon /></a>
      : <button disabled={!canReadSources && !numericLabels.has(id)} onClick={() => onSource(id)} translate="no">{label}</button>}{source?.source_name && <span className="citation-meta" translate="no">{source.source_name}{source.published_at && <> · <EvidenceTime value={source.published_at} timezone={detail.window.timezone} /></>}</span>}</div>
  })}</div></details>
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
    {renderSources(item)}
    </div>
  </article>
  const visibleSections = detail.report?.sections.filter(section => section.kind !== 'macro_data_calendar' || section.rows?.length) || []
  const sectionLinks = [...visibleSections.map(section => ({ id: `section-${section.kind}`, label: sectionTitles[section.kind] })), ...(displayedMarketRows.length ? [{ id: 'market-performance', label: copy('市场表现', 'Market performance') }] : []), ...(displayedMacroRows.length ? [{ id: 'macro-indicators', label: copy('相关指标', 'Related indicators') }] : []), { id: 'source-coverage', label: copy('来源覆盖', 'Source coverage') }]
  return <>
    <nav className="report-contents" aria-label={copy('报告章节', 'Report sections')}>{sectionLinks.map(section => <a key={section.id} id={`link-${section.id}`} href={`#${section.id}`} onClick={event => { event.preventDefault(); navigateToSection(section.id) }}>{section.label}</a>)}</nav>
    {detail.report?.sections.filter(section => section.kind !== 'macro_data_calendar' || section.rows?.length).map(section => <section className="report-section" id={`section-${section.kind}`} key={section.kind}>
      <h2>{sectionTitles[section.kind]}</h2>
      {section.kind === 'macro_data_calendar' && <><p className="table-note">{copy('— 表示来源未提供；不代表 0 或不适用。', '— means not provided by the source; it does not mean zero or not applicable.')}</p><HorizontalTableScroll className="table-scroll"><table className="macro-release-table"><thead><tr>
        <th>{copy('发布日期', 'Release date')}</th><th>{copy('地区 / 类别', 'Region / category')}</th><th>{copy('指标 / 事件', 'Indicator / event')}</th><th>{copy('实际 / 决定', 'Actual / decision')}</th><th>{copy('预期', 'Expected')}</th><th>{copy('前值', 'Previous')}</th><th>{copy('来源', 'Sources')}</th>
      </tr></thead><tbody>{section.rows?.map(row => <tr key={`${row.date}-${row.region}-${row.title}`}>
        <td>{row.date}</td><td translate="no">{row.region}<small>{row.category}</small></td><td translate="no">{row.title}</td><td className={valueClass(row.actual)} translate="no">{row.actual || '—'}</td><td className={valueClass(row.expected)} translate="no">{row.expected || '—'}</td><td className={valueClass(row.previous)} translate="no">{row.previous || '—'}</td><td>{renderSources(row)}</td>
      </tr>)}</tbody></table></HorizontalTableScroll></>}
      {section.groups?.map(group => <div className="report-group" key={group.title}>
        <h3>{groupTitle(group.title)}</h3>
        {group.items.length ? group.items.map(renderItem) : <p className="muted">{copy('本期没有新增条目。', 'No new items in this section.')}</p>}
      </div>)}
      {section.items && (section.items.length ? section.items.map(renderItem) : <p className="muted">{copy('本期没有形成新的机会线索。', 'No new opportunity leads in this period.')}</p>)}
    </section>)}
    {displayedMarketRows.length > 0 && <section className="report-section" id="market-performance">
      <div className="section-heading"><h2>{copy('市场表现', 'Market performance')}</h2><InfoHint label={copy('市场表现口径', 'Market performance basis')} detail={copy('各市场按实际收盘日计算价格涨跌，不含分红；点击资产可查看价格口径。', 'Price changes use each market’s actual closing dates and exclude dividends. Select an asset for its price basis.')} /></div>
      <HorizontalTableScroll className="table-scroll"><table><thead><tr><th>{copy('资产', 'Asset')}</th><th>{copy('起始日', 'Start')}</th><th>{copy('截至日', 'As of')}</th><th className="number">{copy('收盘', 'Close')}</th><th className="number">{detail.report_type === 'weekly' ? copy('本周涨跌', 'Week to date') : copy('日涨跌', 'Daily change')}</th></tr></thead><tbody>
        {displayedMarketRows.map(({ row, index }) => <tr key={row.symbol}><td><button className="table-source" onClick={() => onSource(`market-row:${index}`)}>{row.label}</button><small>{row.symbol}</small></td><td>{row.start_date}</td><td>{row.end_date}</td><td className="number">{row.end_close.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td><td className={`number ${row.return_pct < 0 ? 'negative' : 'positive'}`}>{signed(row.return_pct)}</td></tr>)}
      </tbody></table></HorizontalTableScroll>
    </section>}
    {displayedMacroRows.length > 0 && <section className="report-section" id="macro-indicators"><h2>{copy('相关宏观指标', 'Related macro indicators')}</h2><HorizontalTableScroll className="table-scroll"><table><thead><tr><th>{copy('指标', 'Indicator')}</th><th>{copy('数据日期', 'Observation date')}</th><th className="number">{copy('数值', 'Value')}</th></tr></thead><tbody>{displayedMacroRows.map(({ row, index }) => <tr key={`${row.symbol}-${row.date}`}><td><button className="table-source" onClick={() => onSource(`macro-row:${index}`)}>{row.label}</button></td><td>{row.date}</td><td className="number">{row.value} {row.unit && (units[row.unit] || row.unit)}</td></tr>)}</tbody></table></HorizontalTableScroll></section>}
  </>
}

export default function App() {
  const copy = useCopy()
  const { language, t } = useLanguage()
  const [kind, setKind] = useState<BriefingView>(() => reportLocation().kind)
  const [rows, setRows] = useState<ReportSummary[]>([])
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = useState(() => reportLocation().selected)
  const [detail, setDetail] = useState<ReportDetail | null>(null)
  const [source, setSource] = useState<Source | null>(null)
  const [sourceId, setSourceId] = useState('')
  const [sourceError, setSourceError] = useState('')
  const [sourceRevision, setSourceRevision] = useState(0)
  const [comparison, setComparison] = useState<Source | null>(null)
  const focusEdition = useRef(false)
  const generateButton = useRef<HTMLButtonElement>(null)
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
  const isIndustry = kind === 'industry'
  const sourceOpen = Boolean(sourceId || source)
  const sourceRef = useModalDialog(sourceOpen, closeSource)
  const statusText = (status: string) => ({ queued: copy('等待生成', 'Queued'), running: copy('正在生成', 'Generating'), completed: copy('已完成', 'Completed'), failed: copy('未完成', 'Failed') }[status] || status)
  const stamp = (value: string) => <EvidenceTime value={value} timezone={detail?.window.timezone} />
  const dates = Array.from(new Set(rows.map(row => row.report_date))).sort().reverse().map(date => {
    const versions = rows.filter(row => row.report_date === date).sort((a, b) => b.version - a.version)
    return { date, versions, primary: versions.find(row => row.status === 'completed') || versions[0] }
  })
  const readableAlternative = detail && rows.filter(row => row.status === 'completed' && row.report_id !== detail.report_id).sort((a, b) => Number(b.report_date === detail.report_date) - Number(a.report_date === detail.report_date) || b.report_date.localeCompare(a.report_date) || b.version - a.version)[0]

  useEffect(() => { document.title = `${language === 'zh-Hans' ? '市场简报' : 'Market Briefing'} · Investment Studio` }, [language])
  useEffect(() => {
    function restoreLocation() {
      const route = reportLocation()
      // Hash history belongs to the current article; clearing it cannot trigger a new ID-based fetch.
      if (route.kind === kind && route.selected === selected) {
        if (typeof window.history.state?.readingScrollY === 'number') {
          const entry = document.getElementById(window.history.state.readingFocusId || 'coverage-entry')
          entry?.focus({ preventScroll: true })
          window.scrollTo?.({ top: window.history.state.readingScrollY })
        }
        else if (window.location.hash) navigateToSection(window.location.hash.slice(1), false)
        return
      }
      focusEdition.current = true
      if (route.kind === 'industry') setLoading(false)
      else if (route.kind !== kind) setLoading(true)
      if (route.kind !== kind) setLimit(30)
      setKind(route.kind); setSelected(route.selected); setDetail(null); closeSource(); setError(''); setDetailError(''); setShowGenerate(false)
    }
    window.addEventListener('popstate', restoreLocation)
    return () => window.removeEventListener('popstate', restoreLocation)
  }, [kind, selected])

  useEffect(() => {
    if (kind === 'industry') return
    const controller = new AbortController()
    request<{ harness_available: boolean; can_generate: boolean; can_read_sources: boolean }>('/status', { signal: controller.signal }).then(result => { if (controller.signal.aborted) return; setAvailable(result.harness_available); setCanGenerate(result.can_generate); setCanReadSources(result.can_read_sources) }).catch(reason => { if (!controller.signal.aborted) setError(String(reason.message)) })
    return () => controller.abort()
  }, [isIndustry, revision])
  useEffect(() => {
    if (kind === 'industry') return
    const controller = new AbortController()
    setError(''); setLoading(true)
    Promise.all(Array.from({ length: Math.ceil(limit / 30) }, (_, page) => request<{ rows: ReportSummary[]; total: number }>(`/reports?report_type=${kind}&limit=30&offset=${page * 30}`, { signal: controller.signal }))).then(pages => {
      if (controller.signal.aborted) return
      const result = { rows: pages.flatMap(page => page.rows), total: pages[0].total }
      setRows(result.rows); setTotal(result.total); setLoading(false)
    }).catch(reason => { if (!controller.signal.aborted) { setError(String(reason.message)); setLoading(false) } })
    return () => controller.abort()
  }, [kind, revision, limit])
  useEffect(() => {
    if (kind === 'industry') return
    const candidates = rows.filter(row => row.report_type === kind).sort((a, b) => b.report_date.localeCompare(a.report_date) || b.version - a.version)
    const candidate = candidates.find(row => row.status === 'completed') || candidates[0]
    if (!selected && candidate) choose(candidate.report_id, true)
  }, [rows, selected, kind])
  useEffect(() => {
    if (kind === 'industry' || !selected) { setDetail(null); setDetailLoading(false); return }
    const controller = new AbortController()
    setDetailLoading(true); setDetailError('')
    request<ReportDetail>(`/reports/${selected}`, { signal: controller.signal }).then(result => {
      if (controller.signal.aborted) return
      setDetail(result); setKind(result.report_type); setDetailLoading(false)
      const url = new URL(window.location.href); url.searchParams.set('type', result.report_type)
      window.history.replaceState(window.history.state, '', url)
    }).catch(reason => { if (!controller.signal.aborted) { setDetailError(String(reason.message)); setDetailLoading(false) } })
    return () => controller.abort()
  }, [selected, revision, kind])
  useEffect(() => {
    if (!detail) return
    if (window.location.hash) navigateToSection(window.location.hash.slice(1), false)
    else if (focusEdition.current) navigateToSection('edition-heading', false)
    focusEdition.current = false
  }, [detail?.report_id])
  useEffect(() => {
    const restoreAnchor = () => { if (window.location.hash && typeof window.history.state?.readingScrollY !== 'number') navigateToSection(window.location.hash.slice(1), false) }
    window.addEventListener('hashchange', restoreAnchor)
    return () => window.removeEventListener('hashchange', restoreAnchor)
  }, [])
  useEffect(() => {
    if (kind === 'industry') return
    if (!sourceId || !selected || !canReadSources) return
    const controller = new AbortController()
    setSource(null); setSourceError('')
    request<Source>(`/reports/${selected}/sources/${encodeURIComponent(sourceId)}`, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setSource(result)
    }).catch(reason => { if (!controller.signal.aborted) setSourceError(String(reason.message)) })
    return () => controller.abort()
  }, [selected, sourceId, canReadSources, kind, sourceRevision])
  useEffect(() => {
    if (source || sourceError) sourceRef.current?.querySelector<HTMLElement>('#source-heading')?.focus({ preventScroll: true })
  }, [source, sourceError])
  useEffect(() => {
    if (isIndustry) return
    if (!rows.some(row => pending(row.status)) && !(detail && pending(detail.status))) return
    const timer = window.setInterval(() => setRevision(value => value + 1), 5000)
    return () => window.clearInterval(timer)
  }, [rows, detail?.status, kind])
  function choose(id: string, replace = false) {
    if (id === selected) return
    focusEdition.current = !replace
    setSelected(id); setDetail(null); closeSource(); setError(''); setDetailError('')
    const url = new URL(window.location.href); url.searchParams.set('report', id); url.searchParams.set('type', kind)
    if (!replace) url.hash = ''
    if (replace) window.history.replaceState(null, '', url)
    else window.history.pushState(null, '', url)
  }
  function changeKind(value: BriefingView) {
    if (value === kind) return
    setKind(value); setSelected(''); setRows([]); setDetail(null); closeSource(); setError(''); setDetailError(''); setLimit(30); setLoading(value !== 'industry'); setShowGenerate(false)
    const url = new URL(window.location.href); url.searchParams.delete('report'); url.searchParams.set('type', value); url.hash = ''; window.history.pushState(null, '', url)
  }
  function closeSource() {
    setSourceId(''); setSource(null); setSourceError(''); setComparison(null)
  }
  function showSource(id: string, retainComparison = false) {
    const marketIndex = id.startsWith('market-row:') ? Number(id.split(':')[1]) : -1
    const market = detail?.market_rows[marketIndex]
    if (market) {
      const row: Source = { ...market, source_id: id, source_type: 'market_row' }
      setComparison(row); setSourceId(''); setSource(row); setSourceError('')
      return
    }
    if (!retainComparison) setComparison(null)
    if (!canReadSources) {
      const [kind, index] = id.split(':')
      // Public editions expose the retained numeric row, including citations
      // to its underlying source IDs, without requesting private source input.
      const market = kind === 'market-row' ? detail?.market_rows[Number(index)] : detail?.market_rows.find(row => row.source_ids.includes(id))
      const macro = kind === 'macro-row' ? detail?.macro_rows[Number(index)] : detail?.macro_rows.find(row => row.source_ids.includes(id))
      const row = market || macro
      if (row) setSource({ ...row, source_id: id, source_type: market ? 'market_row' : 'macro_row' })
      return
    }
    if (id !== sourceId) { setSource(null); setSourceError('') }
    setSourceId(id)
  }
  async function generate(cutoff: string) {
    if (kind === 'industry') return
    setGenerating(true); setError('')
    try {
      const result = await request<ReportSummary>('/reports', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ report_type: kind, cutoff }) })
      if (reportLocation().kind === kind) choose(result.report_id)
      setRevision(value => value + 1); setShowGenerate(false)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setGenerating(false) }
  }
  return <main className="briefing-shell">
    <header className="briefing-masthead"><nav className="workspace-breadcrumbs" aria-label={copy('工作区导航', 'Workspace navigation')}><a data-workspace-link href={withLanguage(resolveWorkspaceUrl(import.meta.env.VITE_HOME_URL, 'home'), language)}>{copy('首页', 'Home')}</a><span aria-hidden="true">/</span><WorkspaceSwitcher current="briefing" /></nav><LanguageSelector /></header>
    <div className="briefing-heading"><div className="section-heading"><h1>{copy('市场简报', 'Market Briefing')}</h1><InfoHint label={copy('关于市场简报', 'About Market Briefing')} detail={copy('关注值得理解的变化，保留每一份判断的依据。', 'Understand meaningful changes, with the evidence behind each edition.')} /></div>{!isIndustry && canGenerate && <div className="generation-actions"><button ref={generateButton} onClick={() => setShowGenerate(value => !value)} aria-expanded={showGenerate} disabled={available === false}>{copy('生成本期', 'Generate edition')}</button>{available === false && <InfoHint tone="warning" label={copy('生成不可用', 'Generation unavailable')} detail={copy('报告生成环境尚未配置。已完成报告仍可阅读。', 'Report generation is not configured. Completed editions remain available.')} />}</div>}</div>
    {!isIndustry && showGenerate && <GenerateEditionForm kind={kind} busy={generating} onGenerate={generate} onCancel={() => { setShowGenerate(false); generateButton.current?.focus() }} /> }
    {!isIndustry && error && <RequestRecovery error={error} onRetry={() => setRevision(value => value + 1)} busy={loading} />}
    <LoadingNotice active={!isIndustry && (loading || detailLoading || generating)} message={generating ? copy('正在提交…', 'Submitting…') : copy('加载中', 'Loading')} />
    <div className="briefing-tabs" role="tablist" aria-label={copy('报告类型', 'Report type')}>{(['daily', 'weekly', 'industry'] as const).map(type => <button role="tab" aria-selected={kind === type} key={type} onClick={() => changeKind(type)}>{type === 'daily' ? copy('日报', 'Daily') : type === 'weekly' ? copy('周报', 'Weekly') : copy('行业研究', 'Industry research')}</button>)}</div>
    {isIndustry ? <IndustryResearch /> : <div className="briefing-layout"><aside className="edition-index" aria-busy={loading}><h2>{copy('历史期刊', 'Editions')}</h2>{loading && <p role="status" className="muted">{copy('加载中', 'Loading')}</p>}{dates.map(({ date, versions, primary }) => <div className="edition-date" key={date}><button className={`edition ${versions.some(row => row.report_id === selected) ? 'selected' : ''}`} aria-current={versions.some(row => row.report_id === selected) ? 'date' : undefined} onClick={() => choose(primary.report_id)}><strong>{date}</strong><span>{copy('版本', 'Version')} {primary.version}<small className={primary.status === 'failed' ? 'negative' : ''}>{statusText(primary.status)}</small></span></button>{versions.length > 1 && <details className="edition-history"><summary>{copy('版本历史', 'Version history')} · {versions.length}</summary>{versions.map(row => <button key={row.report_id} aria-current={selected === row.report_id ? 'page' : undefined} onClick={() => choose(row.report_id)} aria-label={`${date} ${copy('版本', 'Version')} ${row.version} ${statusText(row.status)}`}><span>v{row.version}</span><span className={row.status === 'failed' ? 'negative' : ''}>{statusText(row.status)}</span></button>)}</details>}</div>)}{total > rows.length && <button className="load-more" onClick={() => setLimit(value => value + 30)}>{copy('更多期刊', 'More editions')}</button>}</aside>
      <div className="edition-content" aria-busy={detailLoading || loading}>
        {!detail && (loading || detailLoading || Boolean(selected && !detailError)) && <div className="edition-skeleton" role="status" aria-label={copy('加载中', 'Loading')}><div className="edition-skeleton-header" /><div className="edition-skeleton-facts" />{[0, 1, 2].map(index => <div className="edition-skeleton-section" key={index}><span /><span /><span /></div>)}</div>}
        {!detail && !selected && !loading && !error && <p className="muted">{copy('尚无报告。', 'No editions yet.')}</p>}
        {detailError && <RequestRecovery error={detailError} onRetry={() => setRevision(value => value + 1)} busy={detailLoading} />}
      {detail && <><header className="edition-header" id="edition-heading"><p className="eyebrow">{copy('版本', 'VERSION')} {detail.version} · <span role={pending(detail.status) ? 'status' : undefined}>{statusText(detail.status)}</span>{pending(detail.status) && <InfoHint label={copy('生成进度', 'Generation progress')} detail={copy('正在整理本期材料并生成报告，完成后此处自动更新。', 'Preparing evidence and composing this edition. This page updates when it completes.')} />}{detail.edition_role === 'preview' ? ` · ${copy('本地预览', 'LOCAL PREVIEW')}` : ''}<InfoHint label={copy('报告语言与状态', 'Report language and status')} detail={detail.status === 'completed' ? copy('研究内容保留原文。生成完成不代表来源齐备，限制见覆盖说明。', 'Research stays in its original language. Completed generation does not mean complete source coverage; see coverage details.') : copy('研究内容保留原文；切换界面语言不会改写报告。', 'Research stays in its original language; changing the interface language does not translate the report.')} /></p><h2>{detail.report_type === 'daily' ? copy('投研日报', 'Daily research briefing') : copy('投研周报', 'Weekly research briefing')} | {detail.report_date}</h2><dl className="edition-facts"><div><dt>{copy('资料窗口', 'Evidence window')}</dt><dd>{detail.window.period_start && stamp(detail.window.period_start)} — {stamp(detail.cutoff)}</dd></div><div><dt>{copy('生成完成', 'Generated')}</dt><dd>{detail.completed_at ? stamp(detail.completed_at) : '—'}</dd></div><div><dt>{copy('时区', 'Timezone')}</dt><dd>{detail.window.timezone}</dd></div></dl></header>
        {detail.status === 'failed' && <div className="error" role="alert"><p>{t(detail.error || copy('这次生成未形成可读报告。', 'This attempt did not produce a readable report.'))}</p>{readableAlternative && <button onClick={() => choose(readableAlternative.report_id)}>{copy('阅读可用版本', 'Read available edition')} · {readableAlternative.report_date} · v{readableAlternative.version}</button>}</div>}
        <CoverageSummary detail={detail} />
        {detail.report && <ReportBody detail={detail} onSource={showSource} canReadSources={canReadSources} />}
        <details className="coverage" id="source-coverage"><summary>{copy('来源与覆盖范围', 'Sources and coverage')}</summary><p>{copy('报告保留生成时使用的原文与数值版本；日期未知与未覆盖内容不会被补写为事实。', 'Each edition retains its original evidence and numeric versions. Unknown dates and missing coverage remain explicit.')}</p><p>{copy('原始材料', 'Original materials')} · {detail.source_count} · {detail.window.timezone}</p>{canReadSources && <Coverage detail={detail} />}<ul className="all-sources">{detail.sources.filter(item => item.source_type === 'public_document').map(item => <li key={item.source_id}>{safeUrl(item.url) ? <a href={safeUrl(item.url)} target="_blank" rel="noreferrer" translate="no">{item.title} <ExternalLinkIcon /></a> : <span translate="no">{item.title}</span>}<span>{item.source_name} · {item.published_at ? <EvidenceTime value={item.published_at} timezone={detail.window.timezone} /> : copy('首发时间未知', 'Publication time unknown')}{canReadSources && <> · <button onClick={() => showSource(item.source_id)}>{copy('留存版本', 'Retained version')}</button></>}</span></li>)}</ul></details>
      </>}
      </div>
    </div>}
    {sourceOpen && <div className="source-backdrop" onClick={event => {
      if (event.target === event.currentTarget) closeSource()
    }}>
      <div ref={sourceRef} role="dialog" aria-modal="true" tabIndex={-1} className="source-panel" aria-label={copy('来源原文', 'Source evidence')} aria-busy={!source && !sourceError}>
        <div className="source-panel-title"><h2 id="source-heading" tabIndex={-1} translate="no">{String(source?.title || source?.label || source?.symbol || copy('来源原文', 'Source evidence'))}</h2><button onClick={closeSource} aria-label={copy('关闭来源', 'Close source')}>{copy('关闭', 'Close')}</button></div>
        {comparison && <section className="comparison-context" aria-label={copy('价格比较', 'Price comparison')}><strong translate="no">{String(comparison.label)} · {String(comparison.symbol)}</strong><p>{String(comparison.start_date)} · {String(comparison.start_close)} → {String(comparison.end_date)} · {String(comparison.end_close)}<b className={Number(comparison.return_pct) < 0 ? 'negative' : 'positive'}>{signed(Number(comparison.return_pct))}</b></p><div className="source-links"><button aria-current={source === comparison ? 'page' : undefined} onClick={() => { setSourceId(''); setSourceError(''); setSource(comparison) }}>{copy('价格摘要', 'Price summary')}</button>{canReadSources && (comparison.source_ids as string[]).map((id, index) => <button key={`${id}-${index}`} aria-current={sourceId === id ? 'page' : undefined} onClick={() => showSource(id, true)}>{index === 0 ? copy('期初来源', 'Starting source') : copy('期末来源', 'Ending source')}</button>)}</div></section>}
        {source ? <SourceEvidence source={source} onClose={closeSource} onSource={id => showSource(id, true)} canReadSources={canReadSources} showHeading={false} showSourceLinks={!comparison} timezone={detail?.window.timezone} /> : sourceError ? <div className="error" role="alert"><p>{sourceError}</p><button onClick={() => setSourceRevision(value => value + 1)}>{copy('重试', 'Retry')}</button></div> : <p role="status" className="muted">{copy('加载中', 'Loading')}</p>}
      </div>
    </div>}
  </main>
}
