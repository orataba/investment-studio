import RiskPanel from '../../../../../packages/ui/src/InstrumentRiskPanel'
import { fetchJson } from '../lib/api'
import { useCanWriteTeam } from './AccountBoundary'
import { setRiskReferenceParams, type ResearchAssistantReference } from '../../../../../packages/ui/src/researchReference'
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
  onAskAssistant?: (id: string, question: string, reference?: ResearchAssistantReference) => void
  onChanged?: () => void
}) {
  const canWrite = useCanWriteTeam()
  return (
      <RiskPanel
        canWrite={canWrite}
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
            ? '重点关注'
            : heading
        }
        onAskAssistant={onAskAssistant}
        onChanged={onChanged}
        instrumentHref={(id) =>
          `/instruments/${encodeURIComponent(id)}?${new URLSearchParams({ tab: 'investment-research', risk: '1', ...(watchlistId ? { watchlist: watchlistId } : {}) })}`
        }
        assistantHref={(id, question, reference) => {
          const params = new URLSearchParams({ instruments: id, question, ...(watchlistId ? { watchlist: watchlistId } : {}) })
          setRiskReferenceParams(params, reference)
          return `/assistant?${params}`
        }}
      />
  )
}
