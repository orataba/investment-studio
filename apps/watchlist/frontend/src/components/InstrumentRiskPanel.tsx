import RiskPanel from '../../../../../packages/ui/src/InstrumentRiskPanel'
import { fetchJson } from '../lib/api'
import { useCanWriteTeam } from './AccountBoundary'
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
  mode = 'attention',
}: {
  instrumentId?: string
  watchlistId?: string
  focusInstrumentId?: string
  scopeLabel?: string
  heading?: string | null
  onAskAssistant?: (id: string, question: string) => void
  onChanged?: () => void
  mode?: 'attention' | 'price'
}) {
  const canWrite = useCanWriteTeam()
  return (
      <RiskPanel
        canWrite={canWrite}
        caseScope={mode === 'price' ? 'traditional' : 'all'}
        attentionLabel="重点关注"
        instrumentId={instrumentId}
        focusInstrumentId={focusInstrumentId}
        query={
          watchlistId && !instrumentId ? `watchlist_id=${encodeURIComponent(watchlistId)}` : ''
        }
        request={request}
        scopeLabel={scopeLabel}
        heading={
          heading === undefined
            ? mode === 'price' ? '价格与风险跟进' : '重点关注'
            : heading
        }
        onAskAssistant={onAskAssistant}
        onChanged={onChanged}
        instrumentHref={(id, signal) =>
          `/instruments/${encodeURIComponent(id)}?${new URLSearchParams({ tab: signal?.startsWith('sector:') ? 'events' : 'risk', ...(watchlistId ? { watchlist: watchlistId } : {}) })}`
        }
        assistantHref={(id, question) =>
          `/assistant?${new URLSearchParams({ instruments: id, question, ...(watchlistId ? { watchlist: watchlistId } : {}) })}`
        }
      />
  )
}
