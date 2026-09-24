import { useEffect, useState } from 'react'
import { getSavedResearchSource, type AskResearchAssistant, type NotebookSource, type ResearchModule, type ResearchPlan, type SavedResearchNotebook, type SavedResearchSource } from '../lib/researchDossierApi'
import { ComputedEvidence, SourceList, dateLabel } from './ResearchEvidence'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ResearchQuantFigure from './ResearchQuantFigure'
import ResearchOpinionComposer from './ResearchOpinionComposer'
import ResearchThemeComposer from './ResearchThemeComposer'
import { useStudioAccount } from './AccountBoundary'

const coverageLabels = { supported: '依据较充分', partial: '部分覆盖', insufficient: '证据不足' }
const moduleTitles: Record<string, string> = {
  'identity-structure': '范围与结构', 'business-fundamentals': '经营与现金流', 'equity-aggregation': '成份与整体基本面',
  'rates-credit': '利率与信用', 'commodity-supply-demand': '供需与持有成本', 'fund-strategy': '策略与管理',
  'market-quantitative': '市场与量化', 'events-expectations': '事件、预期与争议', 'crypto-network': '供给、网络与流动性',
  'pricing-compensation': '定价与风险补偿', 'product-implementation': '产品费用与实施',
}

type ReportSection = 'fundamentals' | 'quantitative' | 'events'
const reportSection = (key: string): ReportSection => key === 'market-quantitative' ? 'quantitative' : key === 'events-expectations' ? 'events' : 'fundamentals'
// Exposure-specific methods, rather than the wrapper's instrument_type, name the chapter.
export function fundamentalSectionTitle(plan?: ResearchPlan) {
  const selected = new Set(plan?.modules.map(module => module.id))
  return ([['commodity-supply-demand', '供需与定价'], ['rates-credit', '利率与信用'],
    ['fund-strategy', '策略与回报来源'], ['crypto-network', '网络经济与定价'],
    ['business-fundamentals', '经营与定价'], ['equity-aggregation', '成份与定价']] as const)
    .find(([key]) => selected.has(key))?.[1] || '基本面与定价'
}

// Only saved numeric results choose the figure; prose cannot supply chart values or markup.
export function EvidenceFigure({ source }: { source: SavedResearchSource }) {
  const snapshot = source.snapshot
  const holdings = Array.isArray(snapshot?.top_holdings) ? snapshot.top_holdings.filter((row): row is { symbol: string; name?: string; weight_percent: number } => Boolean(row) && typeof row.symbol === 'string' && typeof row.weight_percent === 'number' && Number.isFinite(row.weight_percent)) : []
  const holdingsMin = Math.min(0, ...holdings.map(row => row.weight_percent))
  const holdingsMax = Math.max(100, ...holdings.map(row => row.weight_percent))
  const holdingsRange = holdingsMax - holdingsMin
  const rows = source.data?.rows?.filter(row => typeof row.return_pct === 'number' && Number.isFinite(row.return_pct)) || []
  const maximum = Math.max(1, ...rows.map(row => Math.abs(row.return_pct!)))
  return <figure className="research-evidence-figure">
    <figcaption>{source.title || '留存数值依据'}</figcaption>
    {source.data?.analysis_kind === 'python_quant' && <ResearchQuantFigure source={source} captioned />}
    {rows.length > 0 && <div className="research-return-chart" role="img" aria-label="共同样本区间收益对比">
      {rows.map(row => <div className="research-return-row" key={row.instrument_id}>
        <span translate="no">{row.name || row.instrument_id}</span>
        <div className="research-return-track"><i className={row.return_pct! < 0 ? 'negative' : 'positive'} style={{ width: `${Math.abs(row.return_pct!) / maximum * 50}%`, left: row.return_pct! < 0 ? `${50 - Math.abs(row.return_pct!) / maximum * 50}%` : '50%' }} /></div>
        <strong>{row.return_pct! > 0 ? '+' : ''}{row.return_pct!.toFixed(2)}%</strong>
      </div>)}
    </div>}
    {holdings.length > 0 && <>
      <p className="sector-research-note">持仓日期 {dateLabel(typeof snapshot?.holdings_as_of === 'string' ? snapshot.holdings_as_of : null)} · 采集观察日 {dateLabel(typeof snapshot?.holdings_observed_on === 'string' ? snapshot.holdings_observed_on : null)}</p>
      <div className="research-holdings-chart" role="img" aria-label="已披露主要成份权重">{holdings.map(row => <div className="research-return-row" key={row.symbol}><span translate="no">{row.symbol}</span><div className="research-holding-track"><i style={{ left: `${(Math.min(0, row.weight_percent) - holdingsMin) / holdingsRange * 100}%`, width: `${Math.abs(row.weight_percent) / holdingsRange * 100}%` }} /></div><strong>{row.weight_percent.toFixed(2)}%</strong></div>)}</div>
      <p className="sector-research-note">权重标尺 {holdingsMin}% 至 {holdingsMax}%。已留存的主要成份，权重未重新归一化。采集观察日不等于持仓报告期或首次披露日期。</p>
    </>}
    {source.source_type === 'analyst_estimate_changes' && <div className="research-estimate-evidence">
      <p className="sector-research-note">快照采集：{dateLabel(source.previous_snapshot?.collected_at)} → {dateLabel(source.current_snapshot?.collected_at)}。仅为采集区间内的同财期共识变化，不能确定精确调整日期。</p>
      {source.changes?.length ? <table><thead><tr><th>公司 / 预测财期</th><th>指标</th><th>前次 → 本次</th><th>变化</th></tr></thead><tbody>{source.changes.map((change, index) => <tr key={index}><td>{change.symbol}<small>{change.frequency} · {change.target_period_end}</small></td><td>{change.metric === 'revenue_avg' ? '平均营收预期' : change.metric === 'eps_avg' ? '平均每股收益预期' : change.metric}</td><td>{change.previous_value.toLocaleString()} → {change.current_value.toLocaleString()} {change.currency || '币种待核实'}{change.metric === 'eps_avg' && '/股'}{change.current_currency_status === 'inferred_from_reporting_currency' && <small>币种按财报币种推定</small>}</td><td>{change.delta_pct === null ? '未取得' : `${change.delta_pct.toFixed(2)}%`}{change.analyst_count_changed && <small>分析师样本有变化</small>}</td></tr>)}</tbody></table> : <p className="sector-research-note">本份快照未保存可比变化。</p>}
    </div>}
    {source.data ? <ComputedEvidence source={source} /> : !holdings.length && source.source_type !== 'analyst_estimate_changes' ? <p className="sector-research-note">此记录未包含支持当前图形的数值结构，可在模块来源中查看已保存依据。</p> : null}
  </figure>
}

export function SavedFigure({ instrumentId, notebookVersionId, themeVersionId, source, themeId, onAskAssistant }: { instrumentId: string; notebookVersionId?: string; themeVersionId?: string; source: NotebookSource; themeId?: string; onAskAssistant?: AskResearchAssistant }) {
  const [saved, setSaved] = useState<SavedResearchSource | null>(null)
  const [error, setError] = useState('')
  const [writing, setWriting] = useState(false)
  const [creatingTheme, setCreatingTheme] = useState(false)
  const [notice, setNotice] = useState('')
  const canWrite = useStudioAccount()?.team_role !== 'reader'
  const versionId = themeVersionId || notebookVersionId
  const versionReference = themeVersionId ? { theme_version_id: themeVersionId } : { notebook_version_id: notebookVersionId }
  useEffect(() => {
    const controller = new AbortController()
    setSaved(null); setError('')
    void getSavedResearchSource(instrumentId, source.source_id, controller.signal, versionId).then(value => {
      if (!controller.signal.aborted) setSaved(value)
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '数值依据读取失败') })
    return () => controller.abort()
  }, [instrumentId, versionId, source.source_id])
  const reference = { instrument_id: instrumentId, ...versionReference, theme_id: themeId, source_ids: [source.source_id] }
  const background = `图表：${source.title || '研究图表'}\n资料截至：${source.as_of || saved?.as_of || '未标注'}${saved?.data?.summary ? `\n${saved.data.summary}` : ''}`
  return error ? <p role="alert">数值依据暂时无法读取：{error}</p> : saved ? <div className="research-saved-figure"><EvidenceFigure source={saved} />
    <div className="research-theme-actions">
      {onAskAssistant && <button type="button" onClick={() => onAskAssistant(`请分析这份已留存的数值证据。\n${background}\n读取精确来源与计算口径，检查数据、假设、反例及对当前判断的意义；不要用当前新数据替换当时的图表。`, reference)}>讨论这张图表</button>}
      {canWrite && <><button type="button" onClick={() => setWriting(value => !value)}>基于图表记录观点</button>{!themeId && <button type="button" onClick={() => setCreatingTheme(value => !value)}>基于数据建立主题</button>}</>}
    </div>
    {writing && <ResearchOpinionComposer instrumentId={instrumentId} title={`关于${source.title || '研究图表'}的观点`} context={{ theme_id: themeId, ...versionReference, source_ids: [source.source_id], background }} onCancel={() => setWriting(false)} onSaved={() => { setWriting(false); setNotice('投资观点已保存。') }} />}
    {creatingTheme && <ResearchThemeComposer instrumentId={instrumentId} title={source.title || '量化研究'} kind="quantitative" background={background} reference={{ ...versionReference, source_ids: [source.source_id] }} onCancel={() => setCreatingTheme(false)} onSaved={(action, message) => { setCreatingTheme(false); setNotice(message || (action === 'linked' ? '已关联主题。' : '主题已建立。')) }} />}
    {notice && <p role="status">{notice}</p>}
  </div> : <p className="sector-research-note" role="status">Loading</p>
}

export default function ResearchModules({ instrumentId, notebook, plan, onAskAssistant, section }: {
  instrumentId: string; notebook?: SavedResearchNotebook | null; plan?: ResearchPlan; onAskAssistant?: AskResearchAssistant; section?: ReportSection
}) {
  const modules = (notebook?.modules || []).filter(module => !section || reportSection(module.key) === section)
  // Chapters contain published analysis. Unanswered method questions belong to settings,
  // not empty report chapters; saved chapters survive a later change in methods.
  const anchor = (key: string) => `research-module-${notebook?.version_id || 'pending'}-${key}`
  const keys = [...new Set([...(plan?.modules.map(module => module.id).filter(key => modules.some(module => module.key === key)) || []), ...modules.map(module => module.key)])]
  if (!keys.length) return section === 'events' ? null : <p className="research-report-empty">{section === 'quantitative' ? '尚未形成可供判断的量化分析，不能据此推断风险较低。' : '尚未形成完整分析。已保存的结论、跟踪主题和原始材料仍可查阅。'}</p>
  return <section className="research-domain-modules" aria-label="分领域研究">
    <div className={`research-module-layout${keys.length === 1 ? ' research-module-layout-single' : ''}`}>{keys.length > 1 && <nav className="research-module-index" aria-label="研究领域目录">{keys.map((key, index) => <a key={key} href={`#${anchor(key)}`}><span>{String(index + 1).padStart(2, '0')}</span>{plan?.modules.find(module => module.id === key)?.title || moduleTitles[key] || key}</a>)}</nav>}<div className="research-module-articles">
    {keys.map(key => {
      const method = plan?.modules.find(module => module.id === key)
      const result: ResearchModule | undefined = modules.find(module => module.key === key)
      const title = method?.title || moduleTitles[key] || key
      const sources = (notebook?.sources || []).filter(source => (result?.source_ids.includes(source.source_id) || result?.figure_source_ids.includes(source.source_id)))
      const figures = sources.filter(source => result?.figure_source_ids.includes(source.source_id))
      return <article key={key} id={anchor(key)} className="research-domain-module">
        <header><h3>{title}</h3><span className={`research-coverage-badge ${result?.coverage || 'pending'}`}>{result ? coverageLabels[result.coverage] : method?.applicability === 'unconfirmed' ? '适用范围待核实' : '待建立研究'}</span></header>
        {result ? <>
          {method && result.method_version !== method.version && <p className="sector-research-limitation">方法已更新，待复核；以下保留原方法下的研究判断。</p>}
          {result.summary && <p className="research-module-summary" translate="no">{result.summary}</p>}
          {result.analysis && <div className="research-module-analysis" translate="no"><ReactMarkdown remarkPlugins={[remarkGfm]}>{result.analysis}</ReactMarkdown></div>}
          <p className="sector-research-note">分析更新 {dateLabel(result.updated_at)} · 证据截至 {dateLabel(result.evidence_as_of)}{result.method_version && ` · 方法版本 ${result.method_version}`}</p>
          {figures.map(source => <SavedFigure key={`${source.source_id}:${notebook?.version_id || ''}`} instrumentId={instrumentId} notebookVersionId={notebook?.version_id} source={source} onAskAssistant={onAskAssistant} />)}
          {result.gaps.length > 0 && <div className="research-module-gaps"><strong>尚待核实</strong><ul>{result.gaps.map((gap, index) => <li key={index} translate="no">{gap}</li>)}</ul></div>}
          {result.next_check && <p className="research-notebook-next"><strong>下一步核实</strong> <span translate="no">{result.next_check}</span></p>}
          {sources.length > 0 && <details className="research-module-sources"><summary>依据与原文 · {sources.length}</summary><SourceList instrumentId={instrumentId} versionId={notebook?.version_id} sources={sources} /></details>}
          {onAskAssistant && <button type="button" className="sector-event-ask" onClick={() => onAskAssistant(`请复核“${title}”模块（${key}）的判断：${result.summary}\n${result.analysis}\n请核实证据、反例和下一步检查，保留其他领域的已有研究。`, { instrument_id: instrumentId, notebook_version_id: notebook?.version_id })}>追问这一领域</button>}
        </> : <p className="sector-research-note" translate="no">{method?.reason || '尚未保存这一领域的研究判断。'}</p>}
      </article>
    })}</div></div>
  </section>
}
