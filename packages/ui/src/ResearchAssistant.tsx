import type { ModelWorkStatus } from './modelWork'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useModalDialog } from './useModalDialog'
import InfoHint from './InfoHint'
import NoticeToast, { type NoticeToastMessage } from './NoticeToast'
import { readRiskReferenceParams, setRiskReferenceParams, type ResearchAssistantReference } from './researchReference'
import './notice-toast.css'
import './research-assistant.css'
import ResearchRunStatus, { researchStamp, type ResearchRunExecution, type ResearchRunError } from './ResearchRunStatus'

export type { ResearchAssistantReference } from './researchReference'

export type ResearchAssistantNote = {
  instrumentId: string
  entryId: string
  topicId: string
  title: string
  body: string
  background: string
  reference?: ResearchAssistantReference
}

export type ResearchAssistantProps = {
  params: URLSearchParams
  onParamsChange: (next: URLSearchParams) => void
  watchlistId?: string
  instrumentId?: string
  initialQuestion?: string
  researchReference?: ResearchAssistantReference
  onClose?: () => void
  read: <T>(path: string) => Promise<T>
  write: <T>(path: string, value: unknown, method?: string) => Promise<T>
  uploadTopicFile: (topicId: string, file: File) => Promise<unknown>
  canSaveNote: boolean
  saveNote?: (note: ResearchAssistantNote) => Promise<unknown>
  attachmentHref: (source: string) => string
  announcePublication?: (instrumentIds: string[]) => void
  renderMarkdown: (body: string, sourceUrls: string[]) => ReactNode
  backLink?: ReactNode
}

type ResearchAsset = { instrument_id: string; name: string }
type Topic = {
  topic_id: string
  title: string
  instrument_ids: string[]
  portfolio_id: string | null
  status: string
  updated_at: string
  active_run?: { entry_id: string; status: string; created_at: string; watchlist_id?: string | null } | null
}
type Entry = {
  entry_id: string
  kind: string
  title: string
  body: string
  source: string
  context_json: Record<string, unknown>
  status: string
  created_at: string
  completed_at?: string | null
}
type Connections = {
  assistant_available: boolean
  model_work?: ModelWorkStatus
  reason?: string | null
  portfolios: Array<{ portfolio_id: string; portfolio_name: string }>
}
type ResearchPublication = {
  status: 'published' | 'not_requested' | 'failed' | 'conflict'
  instrument_ids: string[]
  message?: string
}

type Conversation = { topic: Topic; entries: Entry[] }
type Evidence = {
  source_id: string
  tool: string
  retrieved_at?: string | null
  as_of?: string | null
  recorded_at?: string | null
  result?: Record<string, unknown>
  request?: Record<string, unknown>
  source_ids?: string[]
  result_storage?: string
  citation_only?: boolean
  title?: string
  source_type?: string
  url?: string
}
type UserRecord = { kind: 'theme' | 'investment_view' | 'research_publication'; instrument_id: string; id: string; title?: string }
type PublicSource = { source_id?: string; title?: string; url?: string; published_at?: string | null }
const toolLabels: Record<string, string> = {
  instruments: '标的研究与风险',
  comparison: '收益与风险比较',
  quant: 'Python 量化计算',
  portfolio: '实际组合持仓',
  market: '市场状态',
  search: '公开信息检索',
  source: '公开原文',
}
const stateLabels: Record<string, string> = { queued: '等待回复', running: '回复中', paused: '已暂停', draft: '研究答复', failed: '回复未完成' }
const matchingTopic = (topic: Topic, instrumentId?: string, portfolioId?: string) => topic.status !== 'archived'
  && topic.topic_id !== 'us-sector-daily-review'
  && !topic.topic_id.startsWith('instrument-events:')
  && (!instrumentId || (topic.instrument_ids.length === 1 && topic.instrument_ids[0] === instrumentId))
  && (!portfolioId || topic.portfolio_id === portfolioId)
const stamp = researchStamp
function publicHref(value?: string) {
  try { const url = new URL(value || ''); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined } catch { return undefined }
}
function evidenceId(runId: string, sourceId: string) { return `assistant-evidence-${runId}-${encodeURIComponent(sourceId).replace(/[!'()*]/g, value => `%${value.charCodeAt(0).toString(16)}`)}` }
function linkedAnswer(entry: Entry, evidence: Evidence[]) {
  let body = entry.body
  for (const item of evidence) body = body.split(`[${item.source_id}]`).join(`[${item.source_id}](#${evidenceId(entry.entry_id, item.source_id)})`)
  return body
}
function evidenceUrls(evidence: Evidence[]) {
  return evidence.flatMap(item => {
    const result = item.result
    const retained = result?.retained_source as PublicSource | undefined
    const sources = Array.isArray(result?.sources) ? result.sources as PublicSource[] : []
    return [item.url, result?.url, retained?.url, ...sources.map(source => source.url), item.request?.url]
      .filter((value): value is string => typeof value === 'string' && Boolean(publicHref(value)))
  })
}
// Bound the rendered text, not the retained evidence. Large originals can contain
// years of input observations and otherwise stall layout/accessibility snapshots.
const evidencePageCharacters = 24_000
function EvidenceSnapshot({ evidence, entry }: { evidence: Evidence & { result: Record<string, unknown> }; entry: Entry }) {
  const { result, source_id: sourceId } = evidence
  // A downloaded original keeps the retained receipt and its clock, not just the
  // currently displayed page. A citation-only receipt is never promoted to read.
  const text = useMemo(() => JSON.stringify({ run_id: entry.entry_id, cutoff: entry.context_json.cutoff ?? null, ...evidence }, null, 2), [evidence, entry.entry_id, entry.context_json.cutoff])
  const [page, setPage] = useState(0)
  const [query, setQuery] = useState('')
  const [match, setMatch] = useState<number | null>(null)
  const [highlight, setHighlight] = useState<{ offset: number; length: number } | null>(null)
  const originalRef = useRef<HTMLPreElement>(null)
  const pageCount = Math.ceil(text.length / evidencePageCharacters)
  const currentPage = Math.min(page, pageCount - 1)
  const fields = useMemo(() => {
    const start = text.indexOf('\n  "result": {')
    return Object.keys(result).map(key => ({ key, offset: text.indexOf(`\n    ${JSON.stringify(key)}:`, start) })).filter(field => field.offset >= 0)
  }, [result, text])
  useEffect(() => {
    if (!highlight) return
    originalRef.current?.focus({ preventScroll: true })
    originalRef.current?.querySelector('mark')?.scrollIntoView?.({ block: 'nearest' })
  }, [highlight])
  function jump(offset: number, length: number) {
    setPage(Math.floor(offset / evidencePageCharacters))
    setHighlight({ offset, length })
  }
  const pageStart = currentPage * evidencePageCharacters
  const pageText = text.slice(pageStart, pageStart + evidencePageCharacters)
  const highlightStart = highlight ? Math.max(0, highlight.offset - pageStart) : -1
  const highlightEnd = highlight ? Math.min(pageText.length, highlight.offset + highlight.length - pageStart) : -1
  function search() {
    if (!query) return
    const start = match === null ? 0 : match + query.length
    const next = text.indexOf(query, start)
    const found = next >= 0 ? next : text.indexOf(query)
    setMatch(found)
    if (found >= 0) jump(found, query.length)
    else setHighlight(null)
  }
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: 'application/json;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `${sourceId.replace(/[^a-zA-Z0-9._-]/g, '_')}.json`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
  return <>
    <div className="research-evidence-navigation">
      <label>跳转字段 <select aria-label="跳转证据字段" defaultValue="" onChange={event => { const field = fields.find(item => String(item.offset) === event.target.value); if (field) jump(field.offset + 5, JSON.stringify(field.key).length) }}><option value="">选择字段</option>{fields.map(field => <option key={field.key} value={field.offset} translate="no">{field.key}</option>)}</select></label>
      <form onSubmit={event => { event.preventDefault(); search() }}><label>查找原文 <input value={query} onChange={event => { setQuery(event.target.value); setMatch(null) }} /></label><button type="submit" disabled={!query}>查找下一处</button>{match === -1 && <span role="status">未找到匹配内容</span>}</form>
      <button type="button" onClick={download}>下载完整证据</button>
    </div>
    {pageCount > 1 && <div className="research-evidence-pages">
      <p>原件较大，以下分段显示；完整内容保留在下载文件中。</p>
      <button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一段</button>
      <span role="status"><span>文本段</span> {currentPage + 1} / {pageCount}</span>
      <button type="button" disabled={currentPage === pageCount - 1} onClick={() => setPage(currentPage + 1)}>下一段</button>
    </div>}
    <pre ref={originalRef} tabIndex={-1} className="research-evidence-json" translate="no">{highlightStart >= 0 && highlightEnd > highlightStart ? <>{pageText.slice(0, highlightStart)}<mark>{pageText.slice(highlightStart, highlightEnd)}</mark>{pageText.slice(highlightEnd)}</> : pageText}</pre>
  </>
}
function EvidenceItem({ item, entry, read }: { item: Evidence; entry: Entry; read: ResearchAssistantProps['read'] }) {
  const [receipt, setReceipt] = useState(item)
  const result = receipt.result
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const fetching = useRef(false)
  async function load() {
    if (result || fetching.current) return
    fetching.current = true
    setLoading(true)
    setError('')
    try {
      const receipt = await read<Evidence>(`/research/runs/${encodeURIComponent(entry.entry_id)}/evidence?source_id=${encodeURIComponent(item.source_id)}`)
      setReceipt({ ...item, ...receipt, result: receipt.result || { available: false, limitation: '此历史记录未保留可展示的证据明细。' } })
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '证据读取失败，请重试。')
    } finally {
      fetching.current = false
      setLoading(false)
    }
  }
  const failed = result?.available === false
  const original = item.citation_only && result?.retained_source && typeof result.retained_source === 'object' ? result.retained_source as Record<string, unknown> : result
  const sources = failed ? [] : item.tool === 'search' && Array.isArray(result?.sources)
    ? result.sources as PublicSource[] : item.tool === 'source' && original ? [original as PublicSource] : []
  const coverage = Array.isArray(result?.coverage) ? result.coverage as string[] : []
  return <div id={evidenceId(entry.entry_id, item.source_id)} tabIndex={-1}>
    <p><strong>{item.citation_only ? '正文引用的留存来源' : toolLabels[item.tool] || item.tool}{failed ? ' · 读取未完成' : ''}</strong>{!item.citation_only && <> · <span>获取时间</span> {stamp(item.retrieved_at)}</>}</p>
    {item.citation_only && <>
      {item.as_of && <p><span>资料截至</span> {stamp(item.as_of)}</p>}
      {item.recorded_at && <p><span>资料记录时间</span> {stamp(item.recorded_at)}</p>}
      {item.retrieved_at && <p><span>获取时间</span> {stamp(item.retrieved_at)}</p>}
    </>}
    {item.title && <p translate="no">{item.title}</p>}
    {item.citation_only && <p>此来源与本次回答绑定并被正文引用；引用本身不证明已完整查阅或核验。</p>}
    <p><span>来源编号</span> <code translate="no">{item.source_id}</code>{item.tool === 'portfolio' && <> · <span>组合编号</span> <code translate="no">{String(result?.portfolio_id || entry.context_json.portfolio_id || '—')}</code> · <span>估值日期</span> <span translate="no">{String(result?.as_of_date || (result?.summary as Record<string, unknown> | undefined)?.as_of_date || (entry.context_json.page_context as Record<string, unknown> | undefined)?.as_of_date || '—')}</span></>}</p>
    {typeof result?.reason === 'string' && <p>{result.reason}</p>}{typeof result?.limitation === 'string' && <p>{result.limitation}</p>}
    {sources.map((source, index) => {
      const href = publicHref(source.url)
      return <p key={source.source_id || index}>{href ? <a href={href} target="_blank" rel="noopener noreferrer" translate="no">{source.title || source.url}</a> : source.title}<br /><small><span>原文发布时间</span> {stamp(source.published_at)}</small></p>
    })}
    {coverage.length > 0 && <ul>{coverage.map((gap) => <li key={gap}>{gap}</li>)}</ul>}
    <details onToggle={(event) => { if (event.currentTarget.open) void load() }}><summary>当时证据快照</summary>
      {loading && <p role="status">正在读取当时留存的证据…</p>}
      {error && <p role="alert">{error} <button type="button" onClick={() => void load()}>重试读取</button></p>}
      {result && <EvidenceSnapshot evidence={{ ...receipt, result }} entry={entry} />}
    </details>
  </div>
}
function EvidenceDetails({ evidence, entry, read }: { evidence: Evidence[]; entry: Entry; read: ResearchAssistantProps['read'] }) {
  return <details className="assistant-evidence"><summary>查阅依据（{evidence.length}）</summary>
    <p>以下分别列出本次回答的工具查阅记录与正文引用的留存来源。目录、绑定或引用本身不代表已核实；未关联来源的结论仍有证据限制。展开某条快照可读取当时留存的完整内容。</p>
    {evidence.map(item => <EvidenceItem key={item.source_id} item={item} entry={entry} read={read} />)}
  </details>
}

export default function ResearchAssistant({
  params,
  onParamsChange,
  watchlistId,
  instrumentId,
  initialQuestion,
  researchReference,
  onClose,
  read,
  write,
  uploadTopicFile,
  canSaveNote,
  saveNote,
  attachmentHref,
  announcePublication,
  renderMarkdown,
  backLink,
}: ResearchAssistantProps) {
  const selected = params.get('topic') || ''
  const listId = watchlistId || params.get('watchlist') || undefined
  const pagePortfolioId = params.get('portfolio') || undefined
  const scopeKey = JSON.stringify([instrumentId, pagePortfolioId, listId])
  const selectionKey = JSON.stringify([scopeKey, selected])
  const mutationContext = useRef({ scopeKey, selectionKey })
  if (mutationContext.current.selectionKey !== selectionKey) mutationContext.current = { scopeKey, selectionKey }
  const mounted = useRef(true)
  const mutationPending = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  const topicsPath = instrumentId ? `/research/topics?instrument_id=${encodeURIComponent(instrumentId)}` : '/research/topics'
  const [topics, setTopics] = useState<Topic[]>([])
  const [assets, setAssets] = useState<ResearchAsset[]>([])
  const [connections, setConnections] = useState<Connections | null>(null)
  const [detail, setDetail] = useState<Conversation | null>(null)
  const [question, setQuestion] = useState(initialQuestion ?? params.get('question') ?? '')
  const [pendingReference, setPendingReference] = useState(() => researchReference || readRiskReferenceParams(params, instrumentId || (params.get('instruments')?.split(',').length === 1 ? params.get('instruments')! : undefined)))
  const publishedEntries = useRef(new Set<string>())
  const [portfolioId, setPortfolioId] = useState(params.get('portfolio') || '')
  const [focusIds, setFocusIds] = useState<string[]>(
    instrumentId ? [instrumentId] : params.get('instruments')?.split(',').filter(Boolean) || [],
  )
  const [historyOpen, setHistoryOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(true)
  const restoreOnLoad = useRef(!selected)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const [adoption, setAdoption] = useState<{
    entryId: string
    reference?: ResearchAssistantReference
    instrumentId: string
    title: string
    body: string
    background: string
  } | null>(null)
  const questionRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)
  const dialogRef = useModalDialog(
    Boolean(onClose),
    () => onClose?.(),
    questionRef,
  )
  const unfinishedTopic = topics.find(topic => topic.active_run && (instrumentId || pagePortfolioId || !listId || topic.active_run.watchlist_id === listId))
  const otherRunning = Boolean(unfinishedTopic && unfinishedTopic.topic_id !== selected)
  const running =
    detail?.entries.some((entry) =>
      ['queued', 'running'].includes(entry.status),
    ) || false

  useEffect(() => {
    let active = true
    Promise.all([
      read<Topic[]>(topicsPath),
      read<{ instruments: ResearchAsset[] }>('/research/catalogue'),
      read<Connections>('/research/connections'),
    ])
      .then(([history, catalogue, available]) => {
        if (active) {
          const scoped = history.filter((topic) => matchingTopic(topic, instrumentId, pagePortfolioId))
          setTopics(scoped)
          const unfinished = scoped.find(topic => topic.active_run && (instrumentId || pagePortfolioId || !listId || topic.active_run.watchlist_id === listId))
          if (restoreOnLoad.current && unfinished) {
            restoreOnLoad.current = false
            openConversation(unfinished.topic_id, false)
          }
          setAssets(catalogue.instruments)
          setConnections(available)
        }
      })
      .catch((e) => {
        if (active) setError(e.message)
      })
    .finally(() => { if (active) setHistoryLoading(false) })
    return () => {
      active = false
    }
  }, [topicsPath, instrumentId, pagePortfolioId])

  useEffect(() => {
    let active = true
    setDetail(null)
    setAdoption(null)
    setError('')
    if (selected)
      read<Conversation>(`/research/topics/${encodeURIComponent(selected)}`)
        .then((value) => {
          if (active) {
            if (!matchingTopic(value.topic, instrumentId, pagePortfolioId)) {
              setError('该会话不属于当前研究范围，请选择历史对话或新建对话。')
              const next = new URLSearchParams(params)
              next.delete('topic')
              onParamsChange(next)
              return
            }
            setDetail(value)
            setFocusIds(value.topic.instrument_ids)
            setPortfolioId(value.topic.portfolio_id || '')
          }
        })
        .catch((e) => {
          if (active) setError(e.message)
        })
    return () => {
      active = false
    }
  }, [selected, instrumentId, pagePortfolioId])

  useEffect(() => {
    if (!running || !selected || connections?.model_work?.paused) return
    let active = true
    const timer = window.setInterval(() => {
      read<Conversation>(`/research/topics/${encodeURIComponent(selected)}`)
        .then((value) => {
          if (active) {
            setDetail(value)
            if (!value.entries.some(entry => ['queued', 'running'].includes(entry.status))) {
              setTopics(current => current.map(topic => topic.topic_id === selected ? { ...topic, active_run: null } : topic))
            }
          }
        })
        .catch((e) => {
          if (active) setError(e.message)
        })
    }, 4000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [selected, running, connections?.model_work?.paused])

  useEffect(() => {
    for (const entry of detail?.entries || []) {
      const publication = entry.context_json.research_publication as ResearchPublication | undefined
      if (publication?.status === 'published' && !publishedEntries.current.has(entry.entry_id)) {
        publishedEntries.current.add(entry.entry_id)
        announcePublication?.(publication.instrument_ids)
      }
      for (const record of (entry.context_json.user_records || []) as UserRecord[]) {
        const key = `${entry.entry_id}:${record.id}`
        if (publishedEntries.current.has(key)) continue
        publishedEntries.current.add(key)
        announcePublication?.([record.instrument_id])
      }
    }
  }, [detail])

  useEffect(() => {
    if (messagesRef.current)
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight
  }, [selected, detail?.entries.length, running])

  function openConversation(topicId: string, clearQuestion = true, clearReference = clearQuestion) {
    const next = new URLSearchParams(params)
    if (topicId) next.set('topic', topicId)
    else next.delete('topic')
    next.delete('question')
    if (clearReference) setRiskReferenceParams(next)
    onParamsChange(next)
    setHistoryOpen(false)
    if (clearQuestion) {
      setQuestion('')
    }
    if (clearReference) setPendingReference(undefined)
    setNotice(null)
    if (!topicId) {
      setFocusIds(instrumentId ? [instrumentId] : params.get('instruments')?.split(',').filter(Boolean) || [])
      setPortfolioId(pagePortfolioId || '')
    }
  }
  async function perform(action: (isCurrent: () => boolean, isScopeCurrent: () => boolean) => Promise<void>) {
    if (mutationPending.current) return
    mutationPending.current = true
    const context = mutationContext.current
    const isCurrent = () => mounted.current && mutationContext.current === context
    const isScopeCurrent = () => mounted.current && mutationContext.current.scopeKey === context.scopeKey
    setBusy(true)
    setError('')
    setNotice(null)
    try {
      await action(isCurrent, isScopeCurrent)
    } catch (e) {
      if (isCurrent()) setError(e instanceof Error ? e.message : '操作失败')
    } finally {
      mutationPending.current = false
      if (mounted.current) setBusy(false)
    }
  }
  async function ensureConversation(isScopeCurrent: () => boolean) {
    if (selected) {
      if (detail?.topic.topic_id !== selected || !matchingTopic(detail.topic, instrumentId, pagePortfolioId)) throw new Error('请等待当前会话读取完成。')
      return selected
    }
    const topic = await write<Topic>('/research/topics', {
      title: question.trim().slice(0, 80) || '新对话',
      question: '',
      instrument_ids: instrumentId ? [instrumentId] : focusIds,
      portfolio_id: portfolioId || null,
    })
    if (isScopeCurrent()) setTopics((current) => [topic, ...current])
    return topic.topic_id
  }
  async function send() {
    const message = question.trim()
    if (!message || busy || historyLoading || running || otherRunning || (!connections?.assistant_available || connections?.model_work?.paused)) return
    const pageContext: Record<string, string | null | ResearchAssistantReference> = {
      surface: pagePortfolioId ? 'portfolio' : instrumentId ? 'instrument' : 'watchlist',
      instrument_id: instrumentId || (focusIds.length === 1 ? focusIds[0] : null),
      watchlist_id: listId || null,
      portfolio_id: pagePortfolioId || portfolioId || null,
    }
    if (pendingReference) pageContext.research_reference = pendingReference
    for (const key of ['tab', 'currency', 'benchmark', 'start', 'end', 'holding_id', 'as_of_date', 'account_id']) {
      const value = params.get(key)
      if (value !== null) pageContext[key] = value
    }
    await perform(async (isCurrent, isScopeCurrent) => {
      const id = await ensureConversation(isScopeCurrent)
      await write(`/research/topics/${id}/analysis`, {
        question: message,
        watchlist_id: listId || null,
        page_context: pageContext,
      })
      if (selected) {
        const result = await read<Conversation>(`/research/topics/${id}`)
        if (isCurrent()) setDetail(result)
      } else if (isCurrent()) openConversation(id, false, true)
      if (isCurrent()) {
        setQuestion(current => current === question ? '' : current)
        setPendingReference(current => current === pendingReference ? undefined : current)
      }
      const history = await read<Topic[]>(topicsPath)
      if (isScopeCurrent()) setTopics(history.filter((topic) => matchingTopic(topic, instrumentId, pagePortfolioId)))
    })
  }
  async function changePortfolio(value: string) {
    await perform(async (isCurrent) => {
      if (detail) {
        const topic = await write<Topic>(
          `/research/topics/${selected}`,
          { ...detail.topic, portfolio_id: value || null },
          'PUT',
        )
        if (isCurrent()) setDetail({ ...detail, topic })
      }
      if (isCurrent()) setPortfolioId(value)
    })
  }
  async function attach(file: File) {
    await perform(async (isCurrent, isScopeCurrent) => {
      const id = await ensureConversation(isScopeCurrent)
      await uploadTopicFile(id, file)
      if (selected) {
        const result = await read<Conversation>(`/research/topics/${id}`)
        if (isCurrent()) setDetail(result)
      } else if (isCurrent()) openConversation(id, false)
      if (isCurrent()) setNotice({ id: Date.now(), message: '材料已收录，发送问题后才会开始分析。', tone: 'success' })
    })
  }
  const names = Object.fromEntries(
    assets.map((asset) => [asset.instrument_id, asset.name]),
  )
  const entries = [...(detail?.entries || [])].reverse()
  const portfolioName = connections?.portfolios.find((portfolio) => portfolio.portfolio_id === pagePortfolioId)?.portfolio_name
  const content = (
    <div
      className={`research-workbench assistant-workbench ${onClose ? 'assistant-drawer' : 'assistant-page'}`}
      ref={dialogRef}
      role={onClose ? 'dialog' : undefined}
      aria-modal={onClose ? true : undefined}
      aria-label="研究助手"
      tabIndex={-1}
    >
      <header className="assistant-heading">
        <div>
          <small>{pagePortfolioId ? <>{!portfolioName && <><span>组合</span>{' '}</>}<span translate="no">{portfolioName || pagePortfolioId}</span></> : instrumentId ? <span translate="no">{names[instrumentId] || instrumentId}</span> : '关注列表'} · DeepSeek</small>
          <div className="assistant-title"><h1>研究助手</h1><span>个人对话</span><InfoHint label="对话说明" detail={[
            `个人对话 · 仅自己可见${pagePortfolioId ? ' · 资料继续受组合权限保护' : ' · 明确保存的观点与主题归团队共享'}`,
            '已保存的答复反映当时查阅的资料，并非实时更新。',
          ]} /></div>
        </div>
        <div className="toolbar">
          <button onClick={() => setHistoryOpen(!historyOpen)}>历史对话</button>
          <button
            disabled={busy || running || otherRunning || historyLoading}
            onClick={() => {
              openConversation('')
              setDetail(null)
            }}
          >
            新对话
          </button>
          {onClose ? (
            <button onClick={onClose} aria-label="关闭研究助手">
              关闭
            </button>
          ) : backLink}
        </div>
      </header>
      <div className="assistant-scope">
        <span>
          {focusIds.length
            ? `重点标的：${focusIds.map((id) => names[id] || id).join('、')}`
            : pagePortfolioId
              ? '围绕当前组合的持仓与风险开展研究'
              : listId
              ? '从当前关注列表开始研究'
              : '可读取你的关注列表与标的研究记录'}
        </span>
        <label>
          关联组合
          <select
            aria-label="关联组合"
            value={portfolioId}
            disabled={Boolean(pagePortfolioId) || busy || running || Boolean(selected && !detail)}
            onChange={(e) => void changePortfolio(e.target.value)}
          >
            <option value="">暂不关联</option>
            {connections?.portfolios.map((p) => (
              <option key={p.portfolio_id} value={p.portfolio_id} translate="no">
                {p.portfolio_name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="assistant-scope">仅回答问题不会保存观点、创建主题、发布团队研究或修改风险事项；只有明确的保存指令才会创建共享成果。</p>
      {otherRunning && unfinishedTopic && <button type="button" onClick={() => openConversation(unfinishedTopic.topic_id, false)}>继续查看运行中的分析</button>}
      {historyOpen && (
        <nav className="assistant-history" aria-label="历史对话">
          {topics.map((topic) => (
            <button
              key={topic.topic_id}
              className={topic.topic_id === selected ? 'selected' : ''}
              onClick={() => openConversation(topic.topic_id)}
            >
              <span translate="no">{topic.title}</span>
              <small>{topic.active_run && <span>继续查看运行中的分析 · </span>}{stamp(topic.updated_at)}</small>
            </button>
          ))}
          {!topics.length && <p>{instrumentId ? '还没有当前标的的独立历史对话。' : '还没有历史对话。'}</p>}
        </nav>
      )}
      {error && (
        <p role="alert" className="error-state">
          {error}
        </p>
      )}
      <NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
      <div className="assistant-messages" ref={messagesRef} aria-live="polite">
        {!entries.length && (
          <div className="assistant-empty">
            <h2>从你正在考虑的问题开始</h2>
            {pagePortfolioId ? <>
              <p>结合当前组合持仓和已有研究，核查风险、解释变化或继续追问。也可以补充材料，在这段对话中一起分析。</p>
              <p>例如：哪些持仓的风险值得优先关注？FCN 接票后，组合的风险敞口发生了什么变化？</p>
            </> : <>
              <p>助手与研究追踪共用标的档案、当前观点、预测和原始依据。可以补充材料、挑战判断或研究新问题；有效成果会回到同一份研究记录。</p>
              <p>可以直接说“建立关注主题”或“记为我的观点”。讨论、保存的原话和后续复盘会关联保留；研究员的独立判断另行标明。</p>
            </>}
          </div>
        )}
        {entries.map((entry) => {
          const publication = entry.context_json.research_publication as ResearchPublication | undefined
          const userRecords = (entry.context_json.user_records || []) as UserRecord[]
          const evidence = (entry.context_json.tool_evidence ||
            []) as Evidence[]
          if (entry.kind !== 'analysis')
            return (
              <article className="assistant-material" key={entry.entry_id}>
                <small>对话材料</small>
                <strong translate="no">{entry.title}</strong>
                {entry.context_json.file_name ? (
                  <a
                    href={attachmentHref(entry.source)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    查看原件
                  </a>
                ) : (
                  <p translate="no">{entry.body}</p>
                )}
                <small>材料收录于 {stamp(entry.created_at)} · {String(entry.context_json.extraction || '')}</small>
              </article>
            )
          return (
            <div className="assistant-exchange" key={entry.entry_id}>
              <article className="assistant-question">
                <small>
                  你 · {stamp(entry.created_at)}
                </small>
                <p translate="no">{entry.title}</p>
              </article>
              <article className="assistant-answer">
                <small>DeepSeek · {stateLabels[entry.status] || entry.status}</small>
                <ResearchRunStatus runId={entry.entry_id} status={entry.status} createdAt={entry.created_at} completedAt={entry.completed_at} execution={entry.context_json.execution as ResearchRunExecution | undefined} error={entry.context_json.runtime_error as ResearchRunError | undefined} />
                <details className="assistant-answer-context"><summary>回答范围与证据时点</summary><p className="assistant-scope"><span>组合编号</span> <code translate="no">{String(entry.context_json.portfolio_id || '—')}</code> · <span>标的编号</span> <code translate="no">{((entry.context_json.instrument_ids || []) as string[]).join(', ') || '—'}</code> · <span>信息截至</span> {stamp(String(entry.context_json.cutoff || entry.created_at))}</p>
                <p className="assistant-scope">研究原文保留生成时的语言；历史回答和证据快照不会随当前持仓变化。</p></details>
                {['queued', 'running'].includes(entry.status) ? (
                  <p role="status">
                    {entry.status === 'queued' ? '问题已排队，等待回复。' : evidence.length
                      ? `已记录 ${evidence.length} 条查阅记录，正在整理回答…`
                      : '正在理解问题并查阅资料…'}
                  </p>
                ) : (
                  <div className="assistant-markdown" translate={entry.status === 'failed' ? 'yes' : 'no'} onClick={(event) => {
                    const anchor = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="#assistant-evidence-"]')
                    if (!anchor) return
                    const target = document.getElementById(anchor.hash.slice(1))
                    if (!target) return
                    event.preventDefault()
                    let parent = target?.parentElement
                    while (parent) { if (parent instanceof HTMLDetailsElement) parent.open = true; parent = parent.parentElement }
                    target?.focus()
                    target.scrollIntoView?.({ block: 'start' })
                  }}>
                    {renderMarkdown(linkedAnswer(entry, evidence), evidenceUrls(evidence))}
                  </div>
                )}
                {publication && publication.status !== 'not_requested' && <p className="assistant-scope" role={publication.status === 'published' ? 'status' : 'alert'}>
                  {publication.status === 'published' ? '已更新共同研究记录。' : publication.status === 'conflict' ? '研究记录已有更新，本次成果未覆盖新版本。' : '本次研究更新尚未发布。'}{publication.message && ` ${publication.message}`}
                </p>}
                {userRecords.map((record) => <p className="assistant-scope" role="status" key={record.id}>
                  已保存{record.kind === 'theme' ? '关注主题' : record.kind === 'research_publication' ? '团队研究发布指令' : '你的投资观点'}{record.title ? `：${record.title}` : '。'}
                </p>)}
                {evidence.length > 0 ? <EvidenceDetails evidence={evidence} entry={entry} read={read} /> : !['queued', 'running'].includes(entry.status) && <p className="assistant-scope">本次回答没有可展示的工具查阅记录；无法据此核实扩展范围的事实。</p>}
                {entry.status === 'failed' && <button type="button" disabled={busy || running} onClick={() => { setQuestion(entry.title); questionRef.current?.focus() }}>重新填写本次问题</button>}
                {entry.status === 'draft' && canSaveNote && saveNote && !pagePortfolioId && !detail?.topic.portfolio_id && !portfolioId && (
                  <button
                    onClick={() =>
                      setAdoption({
                        entryId: entry.entry_id,
                        reference: (entry.context_json.page_context as { research_reference?: ResearchAssistantReference } | undefined)?.research_reference,
                        instrumentId: focusIds.length === 1 ? focusIds[0] : '',
                        title: entry.title,
                        body: entry.body,
                        background: `当时讨论的问题：${entry.title}`,
                      })
                    }
                  >
                    整理为标的笔记
                  </button>
                )}
              </article>
            </div>
          )
        })}
        {adoption && canSaveNote && saveNote && (
          <form
            className="assistant-adoption"
            onSubmit={(e) => {
              e.preventDefault()
              void perform(async (isCurrent) => {
                await saveNote({ ...adoption, topicId: selected })
                announcePublication?.([adoption.instrumentId])
                if (isCurrent()) {
                  setAdoption(null)
                  setNotice({ id: Date.now(), message: '已保存到投资观点，并保留原对话出处。', tone: 'success' })
                }
              })
            }}
          >
            <h3>整理并保存笔记</h3><p>请保留你认可的判断。保存后记为你的投资观点，并注明由助手答复整理。</p>
            <label>
              保存到标的
              <select
                required
                value={adoption.instrumentId}
                onChange={(e) =>
                  setAdoption({ ...adoption, instrumentId: e.target.value })
                }
              >
                <option value="">选择标的</option>
                {assets.map((asset) => (
                  <option key={asset.instrument_id} value={asset.instrument_id} translate="no">
                    {asset.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              标题
              <input
                required
                value={adoption.title}
                onChange={(e) =>
                  setAdoption({ ...adoption, title: e.target.value })
                }
              />
            </label>
            <label>
              当时背景与依据
              <textarea rows={3} required value={adoption.background}
                onChange={(e) => setAdoption({ ...adoption, background: e.target.value })} />
            </label>
            <label>
              研究记录
              <textarea
                rows={8}
                required
                value={adoption.body}
                onChange={(e) =>
                  setAdoption({ ...adoption, body: e.target.value })
                }
              />
            </label>
            <div className="toolbar">
              <button type="submit" disabled={busy}>
                保存笔记
              </button>
              <button type="button" onClick={() => setAdoption(null)}>
                取消
              </button>
            </div>
          </form>
        )}
      </div>
      {pendingReference && <p className="assistant-scope">{pendingReference.risk_case_id ? '已关联所选风险事项，发送后核对其记录时间并读取对应风险依据。' : <>已关联研究追踪中的{pendingReference.pm_note_id ? '投资经理观点版本' : pendingReference.research_update_id ? '研究更新版本' : pendingReference.event_version_id ? '事件版本' : pendingReference.theme_id ? '持续关注主题' : pendingReference.forecast_version_id ? '预测版本' : pendingReference.investment_view_version_id ? '历史观点' : '当前研究'}，发送后将直接读取对应判断与依据。</>}</p>}
      <form
        className="assistant-composer"
        onSubmit={(e) => {
          e.preventDefault()
          void send()
        }}
      >
        <textarea
          ref={questionRef}
          aria-label="向研究助手提问"
          placeholder="输入你的问题，或继续追问…"
          rows={3}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              void send()
            }
          }}
        />
        <div className="assistant-composer-actions">
          <label className="assistant-attach">
            补充材料
            <input
              type="file"
              aria-label="补充对话材料"
              accept=".pdf,.txt,.md,.csv"
              disabled={busy || running || Boolean(selected && detail?.topic.topic_id !== selected)}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void attach(file)
                e.target.value = ''
              }}
            />
          </label>
          <small>
            {connections?.model_work?.paused ? connections.model_work.message || '研究已暂停，已保存的内容仍可查看。' : connections && !connections.assistant_available
              ? connections.reason || 'DeepSeek 尚未连接'
              : '⌘ / Ctrl + Enter 发送'}
          </small>
          <button
            type="submit"
            disabled={
              busy ||
              historyLoading ||
              running ||
              otherRunning ||
              !question.trim() ||
              !connections?.assistant_available ||
              connections?.model_work?.paused === true ||
              Boolean(selected && !detail)
            }
          >
            {connections?.model_work?.paused ? '研究已暂停' : running ? '回复中…' : '发送'}
          </button>
        </div>
      </form>
    </div>
  )
  return onClose ? <div className="assistant-backdrop" onClick={(event) => {
    if (event.target === event.currentTarget) onClose()
  }}>{content}</div> : content
}
