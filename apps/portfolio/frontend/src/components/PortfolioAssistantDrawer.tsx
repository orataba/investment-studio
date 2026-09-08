import { useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ResearchAssistant from '../../../../../packages/ui/src/ResearchAssistant'
import {
  readResearchAssistant,
  researchAssistantUrl,
  uploadResearchAssistantFile,
  writeResearchAssistant,
} from '../lib/researchAssistantApi'

export default function PortfolioAssistantDrawer({ initialParams, onClose }: {
  initialParams: URLSearchParams
  onClose: () => void
}) {
  // The conversation has its own navigation; selecting a topic never changes the portfolio URL.
  const [params, setParams] = useState(() => new URLSearchParams(initialParams))
  return <ResearchAssistant
    params={params}
    onParamsChange={setParams}
    onClose={onClose}
    read={readResearchAssistant}
    write={writeResearchAssistant}
    uploadTopicFile={uploadResearchAssistantFile}
    canSaveNote={false}
    attachmentHref={researchAssistantUrl}
    renderMarkdown={(body) => <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: () => null }}>{body}</Markdown>}
  />
}
