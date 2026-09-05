import RiskPanel from '../../../../../packages/ui/src/InstrumentRiskPanel'
import { fetchJson } from '../lib/api'
const request = <T,>(path: string, init?: RequestInit) =>
  fetchJson<T>(`/api${path}`, init)
export default function InstrumentRiskPanel({
  instrumentId,
  watchlistId,
  focusInstrumentId,
  scopeLabel,
  heading,
  onAskAssistant,
  onChanged,
}: {
  instrumentId?: string
  watchlistId?: string
  focusInstrumentId?: string
  scopeLabel?: string
  heading?: string | null
  onAskAssistant?: (id: string, question: string) => void
  onChanged?: () => void
}) {
  return (
    <RiskPanel
      instrumentId={instrumentId}
      focusInstrumentId={focusInstrumentId}
      query={
        watchlistId ? `watchlist_id=${encodeURIComponent(watchlistId)}` : ''
      }
      request={request}
      scopeLabel={scopeLabel}
      heading={
        heading === undefined
          ? instrumentId
            ? '近期风险与跟进'
            : '风险关注'
          : heading
      }
      onAskAssistant={onAskAssistant}
      onChanged={onChanged}
      instrumentHref={(id) =>
        `/instruments/${encodeURIComponent(id)}?${new URLSearchParams({ tab: 'risk', ...(watchlistId ? { watchlist: watchlistId } : {}) })}`
      }
      assistantHref={(id, question) =>
        `/assistant?${new URLSearchParams({ instruments: id, question, ...(watchlistId ? { watchlist: watchlistId } : {}) })}`
      }
    />
  )
}
