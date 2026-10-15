import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { usePortfolioAccess } from '../components/PortfolioAccessProvider'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  correctInvestmentView, createInvestmentView, getInvestmentViewInstruments,
  getInvestmentViewRevisions, getInvestmentViews, selectInvestmentStance,
  type InvestmentView, type InvestmentViewInput, type InvestmentViewInstrument, type InvestmentViewsResponse,
} from '../lib/investmentViewsApi'
import '../investment-views.css'

function today() {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10)
}
function recordedTime(value: string) { return `${new Date(value).toLocaleString('en-GB', { timeZone: 'UTC', hour12: false })} UTC` }
function errorMessage(error: unknown) { return error instanceof Error ? error.message : 'Request failed.' }

export default function InvestmentViewsPage() {
  const { portfolioId = '' } = useParams()
  const [params] = useSearchParams()
  const instrumentId = params.get('instrument_id') || ''
  return <PortfolioWorkspaceLayout activeSection="Investment Views">
    <InvestmentViewsWorkspace key={`${portfolioId}:${instrumentId}`} portfolioId={portfolioId} instrumentId={instrumentId} />
  </PortfolioWorkspaceLayout>
}

function InvestmentViewsWorkspace({ portfolioId, instrumentId }: { portfolioId: string; instrumentId: string }) {
  const { language } = useLanguage()
  const text = (en: string, zh: string) => language === 'zh-Hans' ? zh : en
  const access = usePortfolioAccess()
  const canWrite = access?.portfolio_id === portfolioId && access.can_edit
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<InvestmentViewsResponse | null>(null)
  const [instruments, setInstruments] = useState<InvestmentViewInstrument[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [draft, setDraft] = useState<InvestmentViewInput | null>(null)
  const [editing, setEditing] = useState<InvestmentView | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [history, setHistory] = useState<{ noteId: string; revisions: InvestmentView[]; loading: boolean; error: string | null } | null>(null)
  const mounted = useRef(false)
  const requestSequence = useRef(0)
  const historySequence = useRef(0)
  const writeLock = useRef(false)

  async function load(signal?: AbortSignal) {
    const sequence = ++requestSequence.current
    setLoading(true)
    setError(null)
    try {
      const response = await getInvestmentViews(portfolioId, instrumentId || undefined, signal)
      if (!mounted.current || sequence !== requestSequence.current) return
      if (response.portfolio_id !== portfolioId) throw new Error(text('The response belongs to another portfolio.', '响应的组合不一致，请重新读取。'))
      setData(response)
    } catch (failure) {
      if (!mounted.current || sequence !== requestSequence.current || signal?.aborted) return
      setData(null)
      setError(errorMessage(failure))
    } finally {
      if (mounted.current && sequence === requestSequence.current) setLoading(false)
    }
  }

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    void load(controller.signal)
    void getInvestmentViewInstruments(portfolioId, controller.signal).then((response) => {
      if (mounted.current && response.portfolio_id === portfolioId) setInstruments(response.instruments)
    }).catch((failure) => {
      if (mounted.current && !controller.signal.aborted) setCatalogError(errorMessage(failure))
    })
    return () => {
      mounted.current = false
      requestSequence.current += 1
      historySequence.current += 1
      controller.abort()
    }
    // Portfolio and asset changes remount this workspace before another response can render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [portfolioId, instrumentId])

  function openDraft(note?: InvestmentView) {
    if (writeLock.current) return
    setEditing(note ?? null)
    setDraft(note ? { view_date: note.view_date, title: note.title, body: note.body, context: note.context, source: note.source, instrument_ids: [...note.instrument_ids] }
      : { view_date: today(), title: '', body: '', context: '', source: '', instrument_ids: instrumentId ? [instrumentId] : [] })
    setSearch('')
    setSaveError(null)
    setNotice(null)
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft || !canWrite || loading || writeLock.current) return
    writeLock.current = true
    requestSequence.current += 1
    setSaving(true)
    setSaveError(null)
    try {
      if (editing) await correctInvestmentView(portfolioId, editing.note_id, { ...draft, expected_revision: editing.revision_number })
      else await createInvestmentView(portfolioId, draft)
      if (!mounted.current) return
      setDraft(null)
      setEditing(null)
      setHistory(null)
      historySequence.current += 1
      setNotice(text('Judgment saved.', '观点已保存。'))
      await load()
    } catch (failure) {
      if (mounted.current) setSaveError(errorMessage(failure))
    } finally {
      writeLock.current = false
      if (mounted.current) setSaving(false)
    }
  }

  async function selectCurrent(note: InvestmentView | null) {
    if (!data || !canWrite || writeLock.current) return
    writeLock.current = true
    requestSequence.current += 1
    setSaving(true)
    setError(null)
    try {
      const selected = await selectInvestmentStance(portfolioId, { note_id: note?.note_id ?? null,
        revision_number: note?.revision_number ?? null, expected_version: data.stance_version })
      if (!mounted.current) return
      setData((current) => current ? { ...current, ...selected } : current)
      setNotice(note ? text('Current overall view selected.', '已选定当前总体观点。') : text('Current overall view cleared.', '已取消当前总体观点。'))
    } catch (failure) {
      if (mounted.current) setError(errorMessage(failure))
    } finally {
      writeLock.current = false
      if (mounted.current) setSaving(false)
    }
  }

  async function showHistory(note: InvestmentView) {
    const sequence = ++historySequence.current
    setHistory({ noteId: note.note_id, revisions: [], loading: true, error: null })
    try {
      const response = await getInvestmentViewRevisions(portfolioId, note.note_id)
      if (mounted.current && sequence === historySequence.current && response.portfolio_id === portfolioId && response.note_id === note.note_id) {
        setHistory({ noteId: note.note_id, revisions: response.revisions, loading: false, error: null })
      }
    } catch (failure) {
      if (mounted.current && sequence === historySequence.current) setHistory({ noteId: note.note_id, revisions: [], loading: false, error: errorMessage(failure) })
    }
  }

  const current = data?.current_stance
  const matches = search.trim() ? instruments.filter((item) => `${item.instrument_name} ${item.instrument_id}`.toLowerCase().includes(search.trim().toLowerCase())).slice(0, 20) : []
  const filterOptions = instruments.some((item) => item.instrument_id === instrumentId) || !instrumentId ? instruments
    : [{ instrument_id: instrumentId, instrument_name: instrumentId, instrument_type: '' }, ...instruments]
  const isSelected = (note: InvestmentView) => current?.note.note_id === note.note_id && current.note.revision_number === note.revision_number

  function noteContent(note: InvestmentView, historical = false) {
    return <>
      <div className="investment-view-byline"><time>{note.view_date}</time><span>{note.author_name}</span><span>v{note.revision_number}</span>
        {isSelected(note) && <strong>{text('Selected overall view', '已选定总体观点')}</strong>}
      </div>
      {note.title && <h3>{note.title}</h3>}
      <p className="investment-view-body">{note.body}</p>
      <details className="investment-view-context"><summary>{text('Context and record details', '当时背景与记录信息')}</summary>
        <p>{note.context}</p>
        {note.source && <p><strong>{text('Source', '来源')}</strong> · {note.source}</p>}
        <small>{text('Recorded', '记录时间')} {recordedTime(note.created_at)} · {text('Version saved by', '本版本保存人')} {note.revised_by_name} · {recordedTime(note.updated_at)}</small>
      </details>
      {note.instruments.length > 0 && <div className="investment-view-assets">{note.instruments.map((item) => <Link key={item.instrument_id}
        to={`?${new URLSearchParams({ instrument_id: item.instrument_id })}`}>{item.instrument_name}</Link>)}</div>}
      <div className="investment-view-actions">
        {!historical && note.can_edit && canWrite && <button type="button" disabled={saving || Boolean(draft)} onClick={() => openDraft(note)}>{text('Correct', '更正')}</button>}
        {!historical && <button type="button" onClick={() => void showHistory(note)}>{text('Versions', '版本记录')} ({note.revision_number})</button>}
        {canWrite && !note.instrument_ids.length && !isSelected(note) && <button type="button" disabled={saving || loading} onClick={() => void selectCurrent(note)}>
          {text('Select as current overall view', '设为当前总体观点')} · v{note.revision_number}</button>}
      </div>
    </>
  }

  return <section className="investment-views-page" aria-busy={loading}>
    <header className="investment-views-heading"><div><h1>{text('Investment views', '投资观点')}</h1>
      <p>{text('Portfolio judgments and reasons for holding an asset. New judgments are recorded separately.', '记录组合判断与持有理由；每次新判断另增一条。')}</p></div>
      {canWrite && <button className="button-primary" type="button" disabled={saving || Boolean(draft)} onClick={() => openDraft()}>{text('New judgment', '新增观点')}</button>}
    </header>
    {notice && <p role="status" className="investment-views-notice">{notice}</p>}
    {error && <div role="alert" className="investment-views-error">{error} <button type="button" disabled={saving} onClick={() => void load()}>{text('Reload', '重新读取')}</button></div>}
    <section className="investment-current-view" aria-label={text('Current overall view', '当前总体观点')}>
      <div className="investment-views-heading"><h2>{text('Current overall view', '当前总体观点')}</h2>
        {current && canWrite && <button type="button" disabled={saving || loading} onClick={() => void selectCurrent(null)}>{text('Clear selection', '取消选定')}</button>}</div>
      {current ? <>{noteContent(current.note, true)}<small>{text('Selected by', '选定人')} {current.selected_by_name} · {recordedTime(current.selected_at)}</small></>
        : <p className="investment-views-muted">{loading ? text('Loading…', '正在读取…')
          : !data ? text('The current overall view is unavailable until the timeline is loaded.', '需成功读取时间线后才能确认当前总体观点。')
          : !instrumentId && !data.notes.some(note => !note.instrument_ids.length) ? text('Record a portfolio-wide judgment first, then select its version as the current overall view.', '先记录一条组合整体判断，再将其具体版本设为当前总体观点。')
          : text('No overall view selected. Choose a specific version of a portfolio-wide judgment.', '尚未选定。请从组合整体判断中明确选择一个版本。')}</p>}
    </section>
    {draft && <form className="investment-view-form" onSubmit={(event) => void save(event)}>
      <h2>{editing ? text(`Correct judgment · based on v${editing.revision_number}`, `更正观点 · 基于 v${editing.revision_number}`) : text('New judgment', '新增观点')}</h2>
      <p className="investment-views-muted">{text('The signed-in member is recorded as the author. Saving a view does not change allocations or create transactions.', '系统记录当前登录成员。保存观点不会修改配置或产生交易。')}</p>
      <div className="investment-view-form-row"><label>{text('Judgment date', '判断日期')}<input type="date" required disabled={saving} value={draft.view_date} onChange={(event) => setDraft({ ...draft, view_date: event.target.value })} /></label>
        <label>{text('Title (optional)', '标题（可选）')}<input maxLength={240} disabled={saving} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label></div>
      <label>{text('Judgment', '观点正文')}<textarea required maxLength={100000} rows={5} disabled={saving} value={draft.body} onChange={(event) => setDraft({ ...draft, body: event.target.value })} /></label>
      <label>{text('Context at the time', '当时背景')}<textarea required maxLength={100000} rows={3} disabled={saving} value={draft.context} onChange={(event) => setDraft({ ...draft, context: event.target.value })}
        placeholder={text('Basis, analysis date, conditions that would change the view…', '判断依据、分析日期、需要改判的条件等…')} /></label>
      <label>{text('Source (optional)', '来源（可选）')}<textarea maxLength={10000} rows={2} disabled={saving} value={draft.source ?? ''} onChange={(event) => setDraft({ ...draft, source: event.target.value })}
        placeholder={text('Document title, source URL, or dated analytical conclusion…', '材料名称、来源链接，或带日期与口径的分析结论…')} /></label>
      <fieldset disabled={saving}><legend>{text('Related assets (optional)', '关联资产（可选）')}</legend>
        <p className="investment-views-muted">{text('Leave empty for a portfolio-wide judgment. Assets need not be held in this portfolio.', '不选资产表示组合整体判断；可以记录尚未持有或已清仓的资产。')}</p>
        {catalogError && <p role="alert">{catalogError}</p>}
        <div className="investment-view-assets">{draft.instrument_ids.map((id) => <button key={id} type="button" onClick={() => setDraft({ ...draft, instrument_ids: draft.instrument_ids.filter((value) => value !== id) })}>
          {instruments.find((item) => item.instrument_id === id)?.instrument_name ?? id} <span aria-hidden="true">×</span><span className="sr-only">{text('Remove asset', '移除资产')}</span></button>)}</div>
        <label>{text('Search registered assets', '搜索已登记资产')}<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        {search.trim() && <div className="investment-asset-results">{matches.map((item) => <label key={item.instrument_id}><input type="checkbox" checked={draft.instrument_ids.includes(item.instrument_id)} onChange={(event) => setDraft({ ...draft,
          instrument_ids: event.target.checked ? [...draft.instrument_ids, item.instrument_id] : draft.instrument_ids.filter((id) => id !== item.instrument_id) })} />{item.instrument_name} <small>{item.instrument_id}</small></label>)}
          {!matches.length && <p>{text('No matching registered asset.', '未找到匹配的已登记资产。')}</p>}
          {matches.length === 20 && <small>{text('Showing the first 20 matches. Refine the search to find more.', '显示前 20 条，请缩小搜索范围。')}</small>}</div>}
      </fieldset>
      {saveError && <div role="alert" className="investment-views-error">{saveError}<p>{text('Your draft is retained. Reload the timeline to inspect newer versions before starting a new correction.', '草稿仍保留。可重新读取时间线核对最新版本，再开始新的更正。')}</p>
        <button type="button" disabled={saving} onClick={() => void load()}>{text('Reload timeline; keep draft', '重新读取时间线，保留草稿')}</button></div>}
      <div className="investment-view-actions"><button className="button-primary" type="submit" disabled={saving || loading || !draft.body.trim() || !draft.context.trim() || !draft.view_date}>{saving ? text('Saving…', '正在保存…') : text('Save judgment', '保存观点')}</button>
        <button type="button" disabled={saving} onClick={() => { setDraft(null); setEditing(null); setSaveError(null) }}>{text('Cancel', '取消')}</button></div>
    </form>}
    <div className="investment-views-heading investment-timeline-heading"><h2>{text('Judgment timeline', '判断时间线')}</h2>
      <label>{text('Related asset', '关联资产')}<select aria-label={text('Filter related asset', '筛选关联资产')} value={instrumentId} disabled={saving || Boolean(draft)} onChange={(event) => {
        const next = new URLSearchParams(params)
        if (event.target.value) next.set('instrument_id', event.target.value); else next.delete('instrument_id')
        setParams(next)
      }}><option value="">{text('All judgments', '全部观点')}</option>{filterOptions.map((item) => <option key={item.instrument_id} value={item.instrument_id}>{item.instrument_name} · {item.instrument_id}</option>)}</select></label>
    </div>
    {!loading && data && !data.notes.length && <p className="investment-views-empty">{instrumentId ? text('No judgment for this asset in this portfolio yet.', '本组合尚无该资产的观点。') : text('No judgments recorded yet.', '尚未记录投资观点。')}</p>}
    <div className="investment-view-timeline">{data?.notes.map((note) => <article className="investment-view-note" key={note.note_id}>
      {noteContent(note)}
      {history?.noteId === note.note_id && <section className="investment-view-history" aria-label={text('Revision history', '修订历史')}>
        <div className="investment-views-heading"><h4>{text('Revision history', '修订历史')}</h4><button type="button" onClick={() => { historySequence.current += 1; setHistory(null) }}>{text('Close history', '收起版本记录')}</button></div>
        {history.loading && <p role="status">{text('Loading versions…', '正在读取版本…')}</p>}{history.error && <p role="alert">{history.error}</p>}
        {history.revisions.map((revision) => <article className="investment-view-version" key={revision.revision_number}>{noteContent(revision, true)}</article>)}
      </section>}
    </article>)}</div>
  </section>
}
