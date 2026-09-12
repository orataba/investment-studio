import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useModalDialog } from './useModalDialog'
import InfoHint from './InfoHint'
import NoticeToast, { type NoticeToastMessage } from './NoticeToast'
import './notice-toast.css'
import './research-assistant.css'

export type ResearchAssistantReference = {
  instrument_id: string
  research_update_id?: string
  event_case_id?: string
  event_version_id?: string
  notebook_version_id?: string
  investment_view_version_id?: string
  forecast_key?: string
  forecast_version_id?: string
  theme_id?: string
  pm_note_id?: string
  pm_note_revision?: number
}

export type ResearchAssistantNote = {
  instrumentId: string
  entryId: string
  topicId: string
  title: string
  body: string
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
  renderMarkdown: (body: string) => ReactNode
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
}
type Connections = {
  assistant_available: boolean
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
  retrieved_at: string
  result: Record<string, unknown>
}
type UserRecord = { kind: 'theme' | 'investment_view' | 'research_publication'; instrument_id: string; id: string; title?: string }
type PublicSource = { source_id?: string; title?: string; url?: string; published_at?: string | null }
const toolLabels: Record<string, string> = {
  instruments: '标的研究与风险',
  comparison: '收益与风险比较',
  portfolio: '实际组合持仓',
  market: '市场状态',
  search: '公开信息检索',
  source: '公开原文',
}
const stateLabels: Record<string, string> = { queued: '等待回复', running: '回复中', draft: '研究答复', failed: '回复未完成' }
const matchingTopic = (topic: Topic, instrumentId?: string, portfolioId?: string) => topic.status !== 'archived'
  && topic.topic_id !== 'us-sector-daily-review'
  && !topic.topic_id.startsWith('instrument-events:')
  && (!instrumentId || (topic.instrument_ids.length === 1 && topic.instrument_ids[0] === instrumentId))
  && (!portfolioId || topic.portfolio_id === portfolioId)
function stamp(value: string | null | undefined) {
  if (!value) return '未知'
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))
}
function publicHref(value?: string) {
  try { const url = new URL(value || ''); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined } catch { return undefined }
}
function EvidenceDetails({ evidence }: { evidence: Evidence[] }) {
  return <details className="assistant-evidence"><summary>查阅依据（{evidence.length}）</summary>
    {evidence.map((item) => {
      const failed = item.result.available === false
      const sources = failed ? [] : item.tool === 'search' && Array.isArray(item.result.sources)
        ? item.result.sources as PublicSource[] : item.tool === 'source' ? [item.result as PublicSource] : []
      const coverage = Array.isArray(item.result.coverage) ? item.result.coverage as string[] : []
      return <div key={item.source_id}>
        <p><strong>{toolLabels[item.tool] || item.tool}{failed ? ' · 读取未完成' : ''}</strong> · 获取 {stamp(item.retrieved_at)}</p>
        {failed && <>
          {typeof item.result.reason === 'string' && <p>{item.result.reason}</p>}
          {typeof item.result.limitation === 'string' && <p>{item.result.limitation}</p>}
        </>}
        {sources.map((source, index) => {
          const href = publicHref(source.url)
          return <p key={source.source_id || index}>
            {href ? <a href={href} target="_blank" rel="noopener noreferrer" translate="no">{source.title || source.url}</a> : source.title}
            <br /><small>原文发布 {stamp(source.published_at)}</small>
          </p>
        })}
        {coverage.length > 0 && <ul>{coverage.map((gap) => <li key={gap}>{gap}</li>)}</ul>}
        <details><summary>依据明细</summary><small>{item.source_id}</small><pre className="research-evidence-json" translate="no">{JSON.stringify(item.result, null, 2)}</pre></details>
      </div>
    })}
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
  const topicsPath = instrumentId ? `/research/topics?instrument_id=${encodeURIComponent(instrumentId)}` : '/research/topics'
  const [topics, setTopics] = useState<Topic[]>([])
  const [assets, setAssets] = useState<ResearchAsset[]>([])
  const [connections, setConnections] = useState<Connections | null>(null)
  const [detail, setDetail] = useState<Conversation | null>(null)
  const [question, setQuestion] = useState(initialQuestion ?? params.get('question') ?? '')
  const [pendingReference, setPendingReference] = useState(researchReference)
  const publishedEntries = useRef(new Set<string>())
  const [portfolioId, setPortfolioId] = useState(params.get('portfolio') || '')
  const [focusIds, setFocusIds] = useState<string[]>(
    instrumentId ? [instrumentId] : params.get('instruments')?.split(',').filter(Boolean) || [],
  )
  const [historyOpen, setHistoryOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const [adoption, setAdoption] = useState<{
    entryId: string
    reference?: ResearchAssistantReference
    instrumentId: string
    title: string
    body: string
  } | null>(null)
  const questionRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)
  const dialogRef = useModalDialog(
    Boolean(onClose),
    () => onClose?.(),
    questionRef,
  )
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
          setTopics(history.filter((topic) => matchingTopic(topic, instrumentId, pagePortfolioId)))
          setAssets(catalogue.instruments)
          setConnections(available)
        }
      })
      .catch((e) => {
        if (active) setError(e.message)
      })
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
    if (!running || !selected) return
    let active = true
    const timer = window.setInterval(() => {
      read<Conversation>(`/research/topics/${encodeURIComponent(selected)}`)
        .then((value) => {
          if (active) setDetail(value)
        })
        .catch((e) => {
          if (active) setError(e.message)
        })
    }, 4000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [selected, running])

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

  function openConversation(topicId: string, clearQuestion = true) {
    const next = new URLSearchParams(params)
    if (topicId) next.set('topic', topicId)
    else next.delete('topic')
    next.delete('question')
    onParamsChange(next)
    setHistoryOpen(false)
    if (clearQuestion) setQuestion('')
    setNotice(null)
    if (!topicId) {
      setFocusIds(instrumentId ? [instrumentId] : params.get('instruments')?.split(',').filter(Boolean) || [])
      setPortfolioId(pagePortfolioId || '')
    }
  }
  async function perform(action: () => Promise<void>) {
    setBusy(true)
    setError('')
    setNotice(null)
    try {
      await action()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }
  async function ensureConversation() {
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
    setTopics((current) => [topic, ...current])
    return topic.topic_id
  }
  async function send() {
    const message = question.trim()
    if (!message || busy || running || !connections?.assistant_available) return
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
    await perform(async () => {
      const id = await ensureConversation()
      await write(`/research/topics/${id}/analysis`, {
        question: message,
        watchlist_id: listId || null,
        page_context: pageContext,
      })
      if (selected)
        setDetail(await read<Conversation>(`/research/topics/${id}`))
      else openConversation(id)
      setQuestion('')
      setPendingReference(undefined)
      setTopics((await read<Topic[]>(topicsPath)).filter((topic) => matchingTopic(topic, instrumentId, pagePortfolioId)))
    })
  }
  async function changePortfolio(value: string) {
    await perform(async () => {
      if (detail) {
        const topic = await write<Topic>(
          `/research/topics/${selected}`,
          { ...detail.topic, portfolio_id: value || null },
          'PUT',
        )
        setDetail({ ...detail, topic })
      }
      setPortfolioId(value)
    })
  }
  async function attach(file: File) {
    await perform(async () => {
      const id = await ensureConversation()
      await uploadTopicFile(id, file)
      if (selected)
        setDetail(await read<Conversation>(`/research/topics/${id}`))
      else openConversation(id, false)
      setNotice({ id: Date.now(), message: '材料已收录，发送问题后才会开始分析。', tone: 'success' })
    })
  }
  const names = Object.fromEntries(
    assets.map((asset) => [asset.instrument_id, asset.name]),
  )
  const entries = [...(detail?.entries || [])].reverse()
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
          <small>{pagePortfolioId ? connections?.portfolios.find((portfolio) => portfolio.portfolio_id === pagePortfolioId)?.portfolio_name || `组合 ${pagePortfolioId}` : instrumentId ? names[instrumentId] || instrumentId : '关注列表'} · DeepSeek</small>
          <div className="assistant-title"><h1>研究助手</h1><span>个人对话</span><InfoHint label="对话说明" detail={[
            `个人对话 · 仅自己可见${pagePortfolioId ? ' · 资料继续受组合权限保护' : ' · 明确保存的观点与主题归团队共享'}`,
            '已保存的答复反映当时查阅的资料，并非实时更新。',
          ]} /></div>
        </div>
        <div className="toolbar">
          <button onClick={() => setHistoryOpen(!historyOpen)}>历史对话</button>
          <button
            disabled={busy}
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
              <option key={p.portfolio_id} value={p.portfolio_id}>
                {p.portfolio_name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {historyOpen && (
        <nav className="assistant-history" aria-label="历史对话">
          {topics.map((topic) => (
            <button
              key={topic.topic_id}
              className={topic.topic_id === selected ? 'selected' : ''}
              onClick={() => openConversation(topic.topic_id)}
            >
              <span translate="no">{topic.title}</span>
              <small>{stamp(topic.updated_at)}</small>
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
                {['queued', 'running'].includes(entry.status) ? (
                  <p role="status">
                    {entry.status === 'queued' ? '问题已排队，等待回复。' : evidence.length
                      ? `已记录 ${evidence.length} 条查阅记录，正在整理回答…`
                      : '正在理解问题并查阅资料…'}
                  </p>
                ) : (
                  <div className="assistant-markdown" translate="no">
                    {renderMarkdown(entry.body)}
                  </div>
                )}
                {publication && publication.status !== 'not_requested' && <p className="assistant-scope" role={publication.status === 'published' ? 'status' : 'alert'}>
                  {publication.status === 'published' ? '已更新共同研究记录。' : publication.status === 'conflict' ? '研究记录已有更新，本次成果未覆盖新版本。' : '本次研究更新尚未发布。'}{publication.message && ` ${publication.message}`}
                </p>}
                {userRecords.map((record) => <p className="assistant-scope" role="status" key={record.id}>
                  已保存{record.kind === 'theme' ? '关注主题' : record.kind === 'research_publication' ? '团队研究发布指令' : '你的投资观点'}{record.title ? `：${record.title}` : '。'}
                </p>)}
                {evidence.length > 0 && <EvidenceDetails evidence={evidence} />}
                {entry.status === 'draft' && canSaveNote && saveNote && !pagePortfolioId && !detail?.topic.portfolio_id && !portfolioId && (
                  <button
                    onClick={() =>
                      setAdoption({
                        entryId: entry.entry_id,
                        reference: (entry.context_json.page_context as { research_reference?: ResearchAssistantReference } | undefined)?.research_reference,
                        instrumentId: focusIds.length === 1 ? focusIds[0] : '',
                        title: entry.title,
                        body: entry.body,
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
              void perform(async () => {
                await saveNote({ ...adoption, topicId: selected })
                announcePublication?.([adoption.instrumentId])
                setAdoption(null)
                setNotice({ id: Date.now(), message: '已保存到投资观点，并保留原对话出处。', tone: 'success' })
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
                  <option key={asset.instrument_id} value={asset.instrument_id}>
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
      {pendingReference && <p className="assistant-scope">已关联研究追踪中的{pendingReference.pm_note_id ? '投资经理观点版本' : pendingReference.research_update_id ? '研究更新版本' : pendingReference.event_version_id ? '事件版本' : pendingReference.theme_id ? '持续关注主题' : pendingReference.forecast_version_id ? '预测版本' : pendingReference.investment_view_version_id ? '历史观点' : '当前研究'}，发送后将直接读取对应判断与依据。</p>}
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
            {connections && !connections.assistant_available
              ? 'DeepSeek 尚未连接'
              : '⌘ / Ctrl + Enter 发送'}
          </small>
          <button
            type="submit"
            disabled={
              busy ||
              running ||
              !question.trim() ||
              !connections?.assistant_available ||
              Boolean(selected && !detail)
            }
          >
            {running ? '回复中…' : '发送'}
          </button>
        </div>
      </form>
    </div>
  )
  return onClose ? <div className="assistant-backdrop" onClick={(event) => {
    if (event.target === event.currentTarget) onClose()
  }}>{content}</div> : content
}
