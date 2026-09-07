import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import InstrumentRiskPanel from './InstrumentRiskPanel'
import { WorkspaceToolIcon } from '../../../../../packages/ui/src/WorkspaceTools'

export default function WatchlistRiskDrawer({
  watchlistId,
  watchlistName,
  instrumentId,
  focusInstrumentId,
  onClose,
  onAskAssistant,
  onChanged,
}: {
  watchlistId?: string
  watchlistName: string
  instrumentId?: string
  focusInstrumentId?: string
  onClose: () => void
  onAskAssistant: (id: string, question: string) => void
  onChanged?: () => void
}) {
  const dialogRef = useModalDialog(true, onClose)
  return (
    <div
      className="assistant-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={dialogRef}
        className="watchlist-risk-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="watchlist-risk-title"
        tabIndex={-1}
      >
        <header className="watchlist-risk-heading">
          <h1 id="watchlist-risk-title">
            <WorkspaceToolIcon kind="risk" />
            风险提示
          </h1>
          <button onClick={onClose} aria-label="关闭风险提示">
            关闭
          </button>
        </header>
        <div className="watchlist-risk-body">
          <InstrumentRiskPanel
            instrumentId={instrumentId}
            watchlistId={watchlistId}
            scopeLabel={watchlistName}
            heading={null}
            focusInstrumentId={focusInstrumentId}
            onAskAssistant={onAskAssistant}
            onChanged={onChanged}
          />
        </div>
      </div>
    </div>
  )
}
