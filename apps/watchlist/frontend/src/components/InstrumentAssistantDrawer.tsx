import { useSearchParams } from 'react-router'
import ResearchPage from '../pages/ResearchPage'

export type InstrumentAssistantDrawerProps = {
  instrumentId: string
  watchlistId?: string
  question?: string
  onClose: () => void
}

export default function InstrumentAssistantDrawer({ instrumentId, watchlistId, question, onClose }: InstrumentAssistantDrawerProps) {
  const [, setParams] = useSearchParams()
  function close() {
    setParams((params) => {
      const next = new URLSearchParams(params)
      for (const key of ['assistant', 'topic', 'question', 'instruments']) next.delete(key)
      return next
    }, { replace: true })
    onClose()
  }
  return <ResearchPage key={instrumentId} instrumentId={instrumentId} watchlistId={watchlistId} initialQuestion={question} onClose={close} />
}
