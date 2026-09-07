import WatchlistRiskDrawer from './WatchlistRiskDrawer'

export default function InstrumentRiskDrawer({ instrumentId, instrumentName, watchlistId, onClose, onAskAssistant }: {
  instrumentId: string
  instrumentName: string
  watchlistId?: string
  onClose: () => void
  onAskAssistant: (question: string) => void
}) {
  return <WatchlistRiskDrawer instrumentId={instrumentId} watchlistId={watchlistId} watchlistName={instrumentName}
    onClose={onClose} onAskAssistant={(_id, question) => onAskAssistant(question)} />
}
