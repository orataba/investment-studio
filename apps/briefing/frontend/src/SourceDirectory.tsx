import { useState } from 'react'
import { useLanguage } from '../../../../packages/ui/src/i18n'
import type { Source } from './types'
import { safeUrl } from './SourceEvidence'
import { EvidenceTime, ExternalLinkIcon } from './reading'
import { completenessLabels, sourceCompleteness } from './sourceCompleteness'

/** A bounded reading list over this edition's retained source identities. */
export default function SourceDirectory({ sources, timezone, canReadSources, onSource }: {
  sources: Source[]; timezone: string; canReadSources: boolean; onSource: (id: string) => void
}) {
  const { language } = useLanguage()
  const copy = (zh: string, en: string) => language === 'zh-Hans' ? zh : en
  const [query, setQuery] = useState('')
  const [completeness, setCompleteness] = useState('all')
  const [page, setPage] = useState(0)
  const documents = sources.filter(item => item.source_type === 'public_document')
  const search = query.trim().toLocaleLowerCase()
  const matches = documents.filter(item => (!search || [item.title, item.source_name, item.url].some(value =>
    String(value || '').toLocaleLowerCase().includes(search))) && (completeness === 'all'
    || sourceCompleteness(item.content_completeness) === completeness))
  const pageCount = Math.max(1, Math.ceil(matches.length / 30))
  const currentPage = Math.min(page, pageCount - 1)
  const labels = completenessLabels(copy)
  return <section className="source-directory" aria-label={copy('材料目录', 'Material directory')}>
    <div className="source-directory-controls">
      <label>{copy('查找材料', 'Find material')}<input type="search" value={query}
        placeholder={copy('标题、发布者或链接', 'Title, publisher or link')}
        onChange={event => { setQuery(event.target.value); setPage(0) }} /></label>
      <label>{copy('正文覆盖', 'Text coverage')}<select value={completeness}
        onChange={event => { setCompleteness(event.target.value); setPage(0) }}>
        <option value="all">{copy('全部材料', 'All material')}</option>
        {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      {(query || completeness !== 'all') && <button onClick={() => { setQuery(''); setCompleteness('all'); setPage(0) }}>{copy('清除筛选', 'Clear filters')}</button>}
    </div>
    <p className="muted" role="status">{copy(`匹配 ${matches.length} / ${documents.length} 份材料`, `${matches.length} of ${documents.length} materials match`)}</p>
    {!matches.length && <p>{copy('没有匹配的材料。', 'No matching material.')}</p>}
    <ul className="all-sources">{matches.slice(currentPage * 30, (currentPage + 1) * 30).map(item => <li key={item.source_id}>
      {safeUrl(item.url) ? <a href={safeUrl(item.url)} target="_blank" rel="noreferrer" translate="no">{item.title} <ExternalLinkIcon /></a> : <span translate="no">{item.title}</span>}
      <span><span translate="no">{item.source_name}</span> · {item.published_at ? <EvidenceTime value={item.published_at} timezone={timezone} /> : copy('首发时间未知', 'Publication time unknown')}
        {' · '}{labels[sourceCompleteness(item.content_completeness)]}{sourceCompleteness(item.content_completeness) === 'unknown' && <> (<span translate="no">{item.content_completeness}</span>)</>}
        {canReadSources && <> · <button onClick={() => onSource(item.source_id)}>{copy('留存版本', 'Retained version')}</button></>}
      </span>
    </li>)}</ul>
    {pageCount > 1 && <nav className="source-directory-pagination" aria-label={copy('材料目录分页', 'Material directory pages')}>
      <button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{copy('上一页', 'Previous')}</button>
      <span>{currentPage + 1} / {pageCount}</span>
      <button disabled={currentPage + 1 >= pageCount} onClick={() => setPage(currentPage + 1)}>{copy('下一页', 'Next')}</button>
    </nav>}
  </section>
}
