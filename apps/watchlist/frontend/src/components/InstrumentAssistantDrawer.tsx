import { useSearchParams } from 'react-router'
import ResearchPage from '../pages/ResearchPage'
import type { ResearchReference } from '../lib/researchDossierApi'

export type InstrumentAssistantDrawerProps = {
  instrumentId: string
  watchlistId?: string
  question?: string
  researchReference?: ResearchReference
  onClose: () => void
}

export default function InstrumentAssistantDrawer({ instrumentId, watchlistId, question, researchReference, onClose }: InstrumentAssistantDrawerProps) {
  const [, setParams] = useSearchParams()
  function close() {
    setParams((params) => {
      const next = new URLSearchParams(params)
      for (const key of ['assistant', 'topic', 'question', 'instruments']) next.delete(key)
      return next
    }, { replace: true })
    onClose()
  }
  return <ResearchPage key={instrumentId} instrumentId={instrumentId} watchlistId={watchlistId} initialQuestion={question} researchReference={researchReference} onClose={close} />
}
