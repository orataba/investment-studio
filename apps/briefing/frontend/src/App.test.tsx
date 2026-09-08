// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import App, { ReportBody } from './App'
import type { ReportDetail } from './types'

const detail: ReportDetail = {
  report_id: 'report-v1', report_type: 'daily', report_date: '2026-09-07', title: '投研日报 | 2026-09-07', version: 1, status: 'completed', edition_role: 'preview',
  cutoff: '2026-09-07T14:45:00Z', created_at: '2026-09-07T14:45:00Z', completed_at: '2026-09-07T14:46:00Z', error: null, source_count: 1,
  window: { period_start: '2026-09-06T22:45:00+08:00', period_end: '2026-09-07T22:45:00+08:00', timezone: 'Asia/Shanghai', period_label: '过去24小时' },
  market_rows: [{ symbol: 'SPY', label: '标普500 ETF', start_date: '2026-09-03', end_date: '2026-09-04', start_close: 100, end_close: 102.5, return_pct: 2.5, source_ids: ['numeric:close1', 'numeric:close2'] }], macro_rows: [],
  coverage: { text: { bundle_count: 1 }, numeric: ['美股截至上一交易日'] },
  sources: [{ source_id: 'text:v1', source_type: 'public_document', title: '央行声明', source_name: '央行', url: 'https://example.com/statement', published_at: '2026-09-07', occurred_at: '2026-09-06' }],
  report: { sections: [{ kind: 'takeaway_section', title: '重点信息', groups: [{ title: '宏观', items: [{ title: '央行继续观察就业', tags: ['货币政策', '就业'], summary: '央行维持利率。', analysis: '下一步观察就业。', source_ids: ['text:v1'], related_market_symbols: ['SPY'] }] }, { title: '微观', items: [] }] }] },
}
beforeEach(() => { window.history.replaceState(null, '', '/?lang=zh-Hans') })
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
function page(component: React.ReactNode) { return render(<LanguageProvider enableDomTranslation={false}>{component}</LanguageProvider>) }

it('shows tags, dated market moves and direct original links without opening source text', () => {
  const source = vi.fn()
  page(<ReportBody detail={detail} onSource={source} />)
  expect(screen.getByText('2026-09-04')).toBeTruthy()
  expect(screen.getAllByText('+2.50%')).toHaveLength(2)
  expect(screen.getByText('货币政策')).toBeTruthy()
  const original = screen.getByRole('link', { name: '央行 ↗' })
  expect(original.getAttribute('href')).toBe('https://example.com/statement')
  expect(original.getAttribute('target')).toBe('_blank')
  expect(source).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '标普500 ETF' }))
  expect(source).toHaveBeenCalledWith('market-row:0')
})

it('displays macro units without rescaling and labels bound numeric citations', () => {
  const source = vi.fn()
  const report = structuredClone(detail)
  report.macro_rows = [
    { symbol: 'US_TREASURY_10Y', label: '美国10年国债收益率', value: 4.78, unit: 'percent', date: '2026-09-04', source_ids: ['numeric:treasury'] },
    { symbol: 'VIX', label: '波动率指数', value: 14.53, unit: 'index', date: '2026-09-04', source_ids: ['numeric:vix'] },
    { symbol: 'RAW', label: '原始单位指标', value: 12, unit: 'recorded-unit', date: '2026-09-04', source_ids: [] },
  ]
  report.sources.push({ source_id: 'numeric:treasury', source_type: 'numeric', symbol: 'US_TREASURY_10Y' })
  report.report!.sections[0].groups![0].items[0].source_ids = ['numeric:treasury', 'market-row:0']
  page(<ReportBody detail={report} onSource={source} />)
  expect(screen.getByText('4.78 %')).toBeTruthy()
  expect(screen.getByText('14.53 点')).toBeTruthy()
  expect(screen.getByText('12 recorded-unit')).toBeTruthy()
  expect(screen.queryByText('US_TREASURY_10Y')).toBeNull()
  fireEvent.click(screen.getAllByRole('button', { name: '美国10年国债收益率' })[0])
  expect(source).toHaveBeenCalledWith('numeric:treasury')
  fireEvent.click(screen.getAllByRole('button', { name: '标普500 ETF' })[0])
  expect(source).toHaveBeenCalledWith('market-row:0')
})

it('shows only cited stocks and preserves their original price-source index', () => {
  const source = vi.fn()
  const report = structuredClone(detail)
  report.market_rows.push(
    { ...report.market_rows[0], symbol: 'UNUSED', label: '未引用公司', asset_type: 'equity' },
    { ...report.market_rows[0], symbol: 'AAPL', label: 'Apple', asset_type: 'equity' },
  )
  report.report!.sections[0].groups![0].items[0].related_market_symbols = ['AAPL']
  page(<ReportBody detail={report} onSource={source} />)
  expect(screen.queryByText('未引用公司')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Apple' }))
  expect(source).toHaveBeenCalledWith('market-row:2')
})

it('keeps a completed edition readable when the latest version failed and shows original dates', async () => {
  const failed = { ...detail, report_id: 'report-v2', version: 2, status: 'failed', error: '缺少原文', report: null }
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => {
    if (url.endsWith('/status')) return { harness_available: true, can_generate: true, can_read_sources: true }
    if (url.includes('/sources/')) return { ...detail.sources[0], content_text: '这是保留的原文。', url: 'https://example.com/statement' }
    if (url.includes('/reports?')) return { rows: [failed, detail], total: 2 }
    if (url.endsWith('/report-v2')) return failed
    return detail
  } })))
  page(<App />)
  await screen.findByRole('heading', { name: '央行继续观察就业' })
  expect(screen.getByText('未完成')).toBeTruthy()
  expect(screen.queryByText('这是保留的原文。')).toBeNull()
  fireEvent.click(screen.getByText('来源与覆盖范围'))
  fireEvent.click(screen.getByRole('button', { name: '留存版本' }))
  await screen.findByText('这是保留的原文。')
  expect(screen.getByText('查看留存原文').closest('details')?.open).toBe(false)
  fireEvent.click(screen.getByText('查看留存原文'))
  expect(screen.getByText('查看留存原文').closest('details')?.open).toBe(true)
  expect(screen.getByText('2026-09-06')).toBeTruthy()
  expect(screen.getByRole('link', { name: '打开原始链接 ↗' }).getAttribute('href')).toBe('https://example.com/statement')
  fireEvent.click(screen.getByRole('button', { name: '关闭来源' }))
  fireEvent.click(screen.getByRole('button', { name: /2026-09-07.*版本 2/ }))
  await screen.findByText('缺少原文')
  await waitFor(() => expect(screen.queryByText('这是保留的原文。')).toBeNull())
})

it('supports the English interface while keeping the report in Chinese', async () => {
  window.history.replaceState(null, '', '/?lang=en')
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ rows: [], total: 0, harness_available: false, can_generate: true, can_read_sources: true }) })))
  page(<App />)
  await screen.findByRole('heading', { name: 'Market Briefing' })
  expect(screen.getByRole('tab', { name: 'Weekly' })).toBeTruthy()
  expect(screen.queryByText('Report generation is not configured. Completed editions remain available.')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /Generation unavailable:/ }))
  expect(screen.getByText('Report generation is not configured. Completed editions remain available.')).toBeTruthy()
  const navigation = screen.getByRole('navigation', { name: 'Workspace navigation' })
  expect(navigation.textContent).toBe('Home/Market Briefing')
  expect(navigation.querySelector('[aria-current="page"]')?.textContent).toBe('Market Briefing')
  expect(screen.getByRole('link', { name: 'Home' }).getAttribute('href')).toContain(':5172/?lang=en')
  expect(document.title).toBe('Market Briefing · Investment Studio')
})

it('translates fixed report headings while preserving source and editorial wording', () => {
  window.history.replaceState(null, '', '/?lang=en')
  page(<ReportBody detail={detail} onSource={vi.fn()} />)
  expect(screen.getByRole('heading', { name: 'Key developments' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Macro' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: '央行继续观察就业' })).toBeTruthy()
  expect(screen.getByText('央行维持利率。')).toBeTruthy()
})

it('keeps weekly selection, report identity and language in the address and restores browser navigation', async () => {
  const weekly = { ...detail, report_id: 'weekly-v1', report_type: 'weekly' }
  window.history.replaceState(null, '', '/?lang=en&type=weekly')
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => {
    if (url.endsWith('/status')) return { harness_available: true, can_generate: true, can_read_sources: true }
    if (url.includes('/reports?')) return { rows: url.includes('report_type=weekly') ? [weekly] : [detail], total: 1 }
    return url.endsWith('/weekly-v1') ? weekly : detail
  } })))
  page(<App />)
  await screen.findByRole('heading', { name: 'Weekly research briefing | 2026-09-07' })
  expect(screen.getByRole('tab', { name: 'Weekly' }).getAttribute('aria-selected')).toBe('true')
  expect(window.location.search).toBe('?lang=en&type=weekly&report=weekly-v1')
  const weeklyAddress = window.location.href
  fireEvent.click(screen.getByRole('tab', { name: 'Daily' }))
  await screen.findByRole('heading', { name: 'Daily research briefing | 2026-09-07' })
  expect(window.location.search).toContain('type=daily')
  // Selecting the current edition must not clear its loaded body.
  fireEvent.click(screen.getByRole('button', { name: /2026-09-07.*Version 1/ }))
  expect(screen.getByRole('heading', { name: 'Daily research briefing | 2026-09-07' })).toBeTruthy()
  window.history.replaceState(null, '', weeklyAddress)
  fireEvent.popState(window)
  await screen.findByRole('heading', { name: 'Weekly research briefing | 2026-09-07' })
  expect(screen.getByRole('navigation', { name: 'Workspace navigation' }).textContent).toBe('Home/Market Briefing')
})

it('shows a discovery quota failure separately from missing collection records', async () => {
  const report = structuredClone(detail)
  report.coverage.text.sources = [{ channel_id: 'x-a16z', source_name: 'X @a16z', latest_discovery: { status: 'failed', failure_reason: 'approved event_view fetch failed closed with HTTP 402: https://api.x.com/2/users/by/username/a16z' }, latest_run: null }]
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/status') ? { harness_available: true, can_generate: true, can_read_sources: true } : url.includes('/reports?') ? { rows: [report], total: 1 } : report })))
  page(<App />)
  await screen.findByText('X @a16z')
  expect(screen.getByText('来源服务额度不足，未取得本轮资料。')).toBeTruthy()
  expect(screen.getByText(/发现：失败.*采集：未保留记录/)).toBeTruthy()
})


it('keeps anonymous published editions readable without production or retained-input controls', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/status')
    ? { harness_available: false, can_generate: false, can_read_sources: false }
    : url.includes('/reports?') ? { rows: [detail], total: 1 } : detail })))
  page(<App />)
  await screen.findByRole('heading', { name: '央行继续观察就业' })
  expect(screen.queryByRole('button', { name: '生成本期' })).toBeNull()
  expect(screen.queryByRole('button', { name: '留存版本' })).toBeNull()
  expect(screen.queryByText('报告生成环境尚未配置。已完成报告仍可阅读。')).toBeNull()
  expect(screen.getByRole('link', { name: '央行 ↗' }).getAttribute('href')).toBe('https://example.com/statement')
})


it('keeps market explanations in the shared floating hint and visibly marks missing citations', () => {
  const report = structuredClone(detail)
  report.report!.sections[0].groups![0].items[0].source_ids = []
  const { container } = page(<ReportBody detail={report} onSource={vi.fn()} />)
  expect(screen.getByText('未保留来源。')).toBeTruthy()
  expect(container.querySelector('.table-note')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /市场表现口径:/ }))
  expect(screen.getByRole('tooltip').textContent).toContain('不含分红')
  expect(container.contains(screen.getByRole('tooltip'))).toBe(false)
})

it('keeps the edition frame while switching without showing an empty state or stale report', async () => {
  const next = { ...detail, report_id: 'report-v2', version: 2, report_date: '2026-09-08' }
  let resolveNext!: (value: ReportDetail) => void
  const nextResponse = new Promise<ReportDetail>(resolve => { resolveNext = resolve })
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => {
    if (url.endsWith('/status')) return { harness_available: true, can_generate: true, can_read_sources: true }
    if (url.includes('/reports?')) return { rows: [detail, next], total: 2 }
    return url.endsWith('/report-v2') ? nextResponse : detail
  } })))
  const { container } = page(<App />)
  await screen.findByRole('heading', { name: '投研日报 | 2026-09-07' })
  const frame = container.querySelector('.edition-content')
  fireEvent.click(screen.getByRole('button', { name: /2026-09-08.*版本 2/ }))
  expect(container.querySelector('.edition-content')).toBe(frame)
  expect(frame?.getAttribute('aria-busy')).toBe('true')
  expect(container.querySelector('.edition-skeleton')).toBeTruthy()
  expect(screen.queryByRole('heading', { name: '投研日报 | 2026-09-07' })).toBeNull()
  expect(screen.queryByText('尚无报告。')).toBeNull()
  await act(async () => resolveNext(next))
  await screen.findByRole('heading', { name: '投研日报 | 2026-09-08' })
  expect(container.querySelector('.edition-content')).toBe(frame)
  expect(frame?.getAttribute('aria-busy')).toBe('false')
})

it('keeps failed report reads distinct from no editions and retries inside the edition frame', async () => {
  let fail = true
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/report-v1') && fail) return { ok: false, status: 503, json: async () => ({ detail: '报告暂时无法读取。' }) }
    return { ok: true, json: async () => url.endsWith('/status')
      ? { harness_available: true, can_generate: true, can_read_sources: true }
      : url.includes('/reports?') ? { rows: [detail], total: 1 } : detail }
  }))
  const { container } = page(<App />)
  expect((await screen.findByRole('alert')).textContent).toContain('报告暂时无法读取。')
  expect(container.querySelector('.edition-skeleton')).toBeNull()
  expect(screen.queryByText('尚无报告。')).toBeNull()
  fail = false
  fireEvent.click(screen.getByRole('button', { name: '重试' }))
  await screen.findByRole('heading', { name: '央行继续观察就业' })
  expect(screen.queryByRole('alert')).toBeNull()
})

it('shows generation progress beside edition status without inserting a notice panel', async () => {
  const report = { ...detail, status: 'running', report: null }
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/status')
    ? { harness_available: true, can_generate: true, can_read_sources: true }
    : url.includes('/reports?') ? { rows: [report], total: 1 } : report })))
  const { container } = page(<App />)
  await screen.findByRole('heading', { name: '投研日报 | 2026-09-07' })
  expect(screen.getByRole('status').textContent).toBe('正在生成')
  expect(container.querySelector('.notice')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /生成进度:/ }))
  expect(screen.getByRole('tooltip').textContent).toContain('完成后此处自动更新')
})


it('opens read-only market evidence in a modal sidebar and closes back to its entry', async () => {
  const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/status')
    ? { harness_available: false, can_generate: false, can_read_sources: false }
    : url.includes('/reports?') ? { rows: [detail], total: 1 } : detail }))
  vi.stubGlobal('fetch', fetchMock)
  const { container } = page(<App />)
  const entry = await screen.findByRole('button', { name: '标普500 ETF' })
  const reportHeading = screen.getByRole('heading', { name: '央行继续观察就业' })
  entry.focus()
  fireEvent.click(entry)
  const drawer = screen.getByRole('dialog', { name: '来源原文' })
  expect(drawer.getAttribute('aria-modal')).toBe('true')
  expect(container.querySelector('.edition-content')?.contains(drawer)).toBe(false)
  expect(within(drawer).getByRole('heading', { name: '标普500 ETF' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: '央行继续观察就业' })).toBe(reportHeading)
  expect(fetchMock.mock.calls.some(([url]) => url.includes('/sources/'))).toBe(false)
  await waitFor(() => expect(drawer.contains(document.activeElement)).toBe(true))
  fireEvent.click(within(drawer).getByRole('button', { name: '关闭来源' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  await waitFor(() => expect(document.activeElement).toBe(entry))
  fireEvent.click(entry)
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(entry)
  fireEvent.click(screen.getByRole('dialog').parentElement!)
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('keeps source loading and errors inside the source sidebar without altering the report', async () => {
  let failSource!: (error: Error) => void
  const sourceResponse = new Promise((_resolve, reject) => { failSource = reject })
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => {
    if (url.endsWith('/status')) return { harness_available: true, can_generate: true, can_read_sources: true }
    if (url.includes('/sources/')) return sourceResponse
    return url.includes('/reports?') ? { rows: [detail], total: 1 } : detail
  } })))
  const { container } = page(<App />)
  await screen.findByRole('heading', { name: '央行继续观察就业' })
  fireEvent.click(screen.getByText('来源与覆盖范围'))
  const entry = screen.getByRole('button', { name: '留存版本' })
  entry.focus()
  fireEvent.click(entry)
  const drawer = screen.getByRole('dialog', { name: '来源原文' })
  expect(within(drawer).getByRole('status').textContent).toBe('加载中')
  expect(drawer.getAttribute('aria-busy')).toBe('true')
  await act(async () => failSource(new Error('留存来源暂时无法读取。')))
  expect(within(drawer).getByRole('alert').textContent).toBe('留存来源暂时无法读取。')
  expect(drawer.getAttribute('aria-busy')).toBe('false')
  expect(container.querySelector('.edition-content [role="alert"]')).toBeNull()
  expect(screen.getByRole('heading', { name: '央行继续观察就业' })).toBeTruthy()
  fireEvent.click(within(drawer).getByRole('button', { name: '关闭来源' }))
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('does not reopen the sidebar when a source response arrives after it was closed', async () => {
  let finishSource!: (value: unknown) => void
  const sourceResponse = new Promise(resolve => { finishSource = resolve })
  const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () => {
    if (url.endsWith('/status')) return { harness_available: true, can_generate: true, can_read_sources: true }
    if (url.includes('/sources/')) return sourceResponse
    return url.includes('/reports?') ? { rows: [detail], total: 1 } : detail
  } }))
  vi.stubGlobal('fetch', fetchMock)
  page(<App />)
  await screen.findByRole('heading', { name: '央行继续观察就业' })
  fireEvent.click(screen.getByText('来源与覆盖范围'))
  fireEvent.click(screen.getByRole('button', { name: '留存版本' }))
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/reports/report-v1/sources/text%3Av1'))).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '关闭来源' }))
  await act(async () => finishSource({ ...detail.sources[0], content_text: '迟到的原文。' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.queryByText('迟到的原文。')).toBeNull()
})
