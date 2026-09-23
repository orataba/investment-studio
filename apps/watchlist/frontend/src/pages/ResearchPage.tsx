import { Link, useSearchParams } from 'react-router'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ResearchAssistant, { type ResearchAssistantNote } from '../../../../../packages/ui/src/ResearchAssistant'
import { resolveWorkspaceUrl } from '../../../../../packages/ui/src/navigation'
import { API_BASE_URL, createInstrumentResearchNote } from '../lib/api'
import { useStudioAccount } from '../components/AccountBoundary'
import type { ResearchReference } from '../lib/researchDossierApi'
import { announceResearchPublication } from '../lib/researchUpdates'
import { readWorkbench, writeWorkbench, uploadTopicFile, today } from '../lib/workbenchApi'

function saveNote({ instrumentId, entryId, topicId, title, body, background, reference }: ResearchAssistantNote) {
  return createInstrumentResearchNote(instrumentId, {
    source_entry_id: entryId,
    note: {
      note_date: today(),
      note_type: 'thesis_update',
      title,
      body,
      summary: '',
      importance: 'medium',
      tags: [],
      source_refs: `Watchlist 助手对话 ${topicId} / 回复 ${entryId}`,
      people: '',
      author: '',
      follow_up_date: null,
      research_context: { background, ...(reference?.instrument_id === instrumentId ? {
        theme_id: reference.theme_id,
        research_update_id: reference.research_update_id,
        event_case_id: reference.event_case_id,
        event_version_id: reference.event_version_id,
        notebook_version_id: reference.notebook_version_id,
        theme_version_id: reference.theme_version_id,
        investment_view_version_id: reference.investment_view_version_id,
        source_ids: reference.source_ids,
        ...(reference.pm_note_id && reference.pm_note_revision ? {
          relationship: 'update',
          related_note_id: reference.pm_note_id,
          related_revision: reference.pm_note_revision,
        } : {}),
      } : {}) },
    },
  })
}

export default function ResearchPage(props: {
  watchlistId?: string
  instrumentId?: string
  initialQuestion?: string
  researchReference?: ResearchReference
  onClose?: () => void
}) {
  const account = useStudioAccount()
  const [params, setParams] = useSearchParams()
  const listId = props.watchlistId || params.get('watchlist') || undefined
  const portfolioId = params.get('portfolio') || undefined
  const portfolioSection = ['overview', 'holdings', 'performance', 'risk', 'transactions', 'accounts', 'research'].includes((params.get('tab') || '').toLowerCase())
    ? params.get('tab')!.toLowerCase() : 'overview'

  return <ResearchAssistant
    {...props}
    params={params}
    onParamsChange={(next) => setParams(next, { replace: true })}
    read={readWorkbench}
    write={writeWorkbench}
    uploadTopicFile={uploadTopicFile}
    canSaveNote={account?.team_role !== 'reader'}
    saveNote={saveNote}
    attachmentHref={(source) => `${API_BASE_URL}${source}`}
    announcePublication={announceResearchPublication}
    renderMarkdown={(body) => <Markdown remarkPlugins={[remarkGfm]} components={{ img: () => null }}>{body}</Markdown>}
    backLink={portfolioId
      ? <a data-workspace-link href={`${resolveWorkspaceUrl(import.meta.env.VITE_PORTFOLIO_URL, 'portfolio')}/portfolios/${encodeURIComponent(portfolioId)}/${portfolioSection}`}>返回组合</a>
      : <Link to={listId ? `/watchlists/${listId}` : '/watchlists'}>返回关注列表</Link>}
  />
}
