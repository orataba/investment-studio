import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import InstrumentRiskPanel from './InstrumentRiskPanel'
import WorkspaceToolIcon from './WorkspaceToolIcon'

export default function WatchlistRiskDrawer({
  watchlistId,
  watchlistName,
  focusInstrumentId,
  onClose,
  onAskAssistant,
  onChanged,
}: {
  watchlistId: string
  watchlistName: string
  focusInstrumentId?: string
  onClose: () => void
  onAskAssistant: (id: string, question: string) => void
  onChanged: () => void
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
            风险关注
          </h1>
          <button onClick={onClose} aria-label="关闭风险关注">
            关闭
          </button>
        </header>
        <div className="watchlist-risk-body">
          <InstrumentRiskPanel
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
