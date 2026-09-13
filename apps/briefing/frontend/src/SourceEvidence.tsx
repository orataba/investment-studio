import InfoHint from '../../../../packages/ui/src/InfoHint'
import { useLanguage } from '../../../../packages/ui/src/i18n'
import type { Source } from './types'

export const safeUrl = (value?: string) => {
  try { const url = new URL(value || ''); return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined } catch { return undefined }
}

export function SourceEvidence({ source, onClose, onSource, canReadSources = true }: { source: Source; onClose: () => void; onSource: (sourceId: string) => void; canReadSources?: boolean }) {
  const { language } = useLanguage()
  const copy = (zh: string, en: string) => language === 'zh-Hans' ? zh : en
  const display = (value: unknown) => typeof value === 'number' ? value.toLocaleString(language === 'zh-Hans' ? 'zh-CN' : 'en-GB', { maximumFractionDigits: 8 }) : typeof value === 'string' && value ? value : '—'
  const unit = ({ percent: '%', index: copy('点', 'index points'), basis_points: copy('基点', 'basis points') } as Record<string, string>)[String(source.unit)] || display(source.unit)
  const basis = (value: unknown) => ({
    split_adjusted_price: copy('拆股复权价格涨跌，不含分红总回报。', 'Split-adjusted price change; excludes dividend total returns.'),
    unadjusted_price: copy('未复权价格涨跌。', 'Unadjusted price change.'),
    split_adjusted: copy('开高低收价格按拆股复权，不含分红。', 'Open, high, low and close are split adjusted, excluding dividends.'),
    unadjusted: copy('开高低收为未复权价格。', 'Open, high, low and close are unadjusted.'),
    split_and_dividend_adjusted: copy('复权收盘价包含拆股和分红调整。', 'Adjusted close includes split and dividend adjustments.'),
  } as Record<string, string>)[String(value)]
  const provider = String(source.source || source.source_dataset || '')
  const providerNames: Record<string, string> = {
    cboe: 'Cboe', cboe_official_vix_history: 'Cboe', fred: 'FRED', fred_market_context: 'FRED',
    us_treasury: copy('美国财政部', 'US Treasury'), daily_treasury_yield_curve: copy('美国财政部', 'US Treasury'), daily_treasury_real_yield_curve: copy('美国财政部', 'US Treasury'),
    hang_seng_indexes_official_chart: copy('恒生指数公司', 'Hang Seng Indexes'),
  }
  const providerName = source.source_name || (provider === 'fmp' || provider.startsWith('fmp_') ? 'Financial Modeling Prep (FMP)' : providerNames[provider])
  const originalUrl = safeUrl(source.url)
  const market = source.source_type === 'market_row'
  const macro = source.source_type === 'macro_row'
  const numeric = source.source_type === 'numeric'
  const document = source.source_type === 'public_document'
  const sourceIds = Array.isArray(source.source_ids) ? source.source_ids.filter((id): id is string => typeof id === 'string') : []
  const fields: [string, unknown][] = market ? [
    [copy('起始日', 'Start date'), source.start_date], [copy('起始收盘价', 'Starting close'), source.start_close],
    [copy('截至日', 'End date'), source.end_date], [copy('截至收盘价', 'Ending close'), source.end_close],
    [copy('区间涨跌', 'Price change'), typeof source.return_pct === 'number' ? `${source.return_pct > 0 ? '+' : ''}${source.return_pct.toFixed(2)}%` : null],
  ] : macro || numeric ? [
    [copy('数据日期', 'Observation date'), source.date],
    ...(source.value != null ? [[copy('数值', 'Value'), source.value], [copy('单位', 'Unit'), unit]] as [string, unknown][] : [
      [copy('开盘价', 'Open'), source.open], [copy('最高价', 'High'), source.high], [copy('最低价', 'Low'), source.low], [copy('收盘价', 'Close'), source.close],
      ...(source.adjusted_close != null ? [[copy('复权收盘价', 'Adjusted close'), source.adjusted_close]] as [string, unknown][] : []),
      ...(source.volume != null ? [[copy('成交量', 'Volume'), source.volume]] as [string, unknown][] : []),
    ] as [string, unknown][]),
  ] : []
  const clocks: [string, unknown][] = document ? [
    [copy('首发', 'Published'), source.published_at], [copy('发生', 'Occurred'), source.occurred_at],
    [copy('源站观测', 'Source observed'), source.observed_at],
    [copy('本机接收', 'Received'), source.received_at],
  ] : numeric ? [
    [copy('采集时间', 'Observed'), source.observed_at], [copy('记录可用时间', 'Recorded availability'), source.available_at],
    ...(source.received_at ? [[copy('本机接收', 'Received'), source.received_at]] as [string, unknown][] : []),
  ] : []

  const priceBasis = market ? [
    basis(source.return_basis) || copy('这条记录未注明价格复权口径。', 'The price adjustment basis is not specified.'),
    copy('涨跌按截至收盘价除以起始收盘价，再减去1计算。', 'Change is ending close divided by starting close, minus one.'),
  ] : numeric && source.value == null ? [
    basis(source.ohlc_adjustment) || copy('这条记录未注明价格复权口径。', 'The price adjustment basis is not specified.'),
    source.adjusted_close != null ? basis(source.adjusted_close_adjustment) : undefined,
  ].filter((value): value is string => Boolean(value)) : []

  return <>
    <div className="source-panel-title"><div className="source-heading-text"><h2 translate="no">{display(source.title || source.label || source.symbol || copy('保留来源', 'Retained source'))}</h2>{priceBasis.length > 0 && <InfoHint label={copy('价格口径', 'Price basis')} detail={priceBasis} tone={(market ? basis(source.return_basis) : basis(source.ohlc_adjustment)) ? 'info' : 'warning'} />}{source.content_completeness === 'source_excerpt' && <InfoHint tone="warning" label={copy('来源完整性', 'Source completeness')} detail={copy('来源提供的节选，未包含完整正文。', 'Source excerpt; the full article is not included.')} />}</div><button onClick={onClose} aria-label={copy('关闭来源', 'Close source')}>×</button></div>
    {providerName && <p>{copy('来源', 'Source')} · <span translate="no">{providerName}</span></p>}
    {originalUrl && <a href={originalUrl} target="_blank" rel="noreferrer">{copy('打开原始链接', 'Open original link')} ↗</a>}
    {fields.length > 0 && <dl className="source-dates">{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd translate="no">{display(value)}</dd></div>)}</dl>}
    {canReadSources && sourceIds.length > 0 && <div className="source-links">{sourceIds.map((id, index) => <button key={`${id}-${index}`} onClick={() => onSource(id)}>{market ? index === 0 ? copy('查看起始价格来源', 'View starting price source') : copy('查看截至价格来源', 'View ending price source') : copy('查看原始数值来源', 'View original value source')}</button>)}</div>}
    {clocks.length > 0 && <dl className="source-dates">{clocks.map(([label, value]) => <div key={label}><dt>{label}</dt><dd translate="no">{display(value)}</dd></div>)}</dl>}
    {source.withdrawn === true && <p className="error" role="alert">{copy('这份来源已撤回；这里保留报告使用的版本。', 'This source was withdrawn; the version used by the report is retained here.')}</p>}
    {source.content_text ? <details className="retained-original"><summary>{copy('查看留存原文', 'Read retained text')}</summary><div className="original-text" translate="no">{source.content_text}</div></details> : !market && !macro && !numeric && <p className="muted">{copy('这份来源没有可读取的正文。', 'No readable text is available for this source.')}</p>}
  </>
}
