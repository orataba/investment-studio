import { useState } from 'react'
import { amendPortfolioDerivativeContract, type PortfolioDerivativeContractRecord } from '../lib/api'
import { derivativeContractDraftFromRecord, derivativeContractFromDraft } from '../lib/derivativeContractDraft'
import { DerivativeSettlementFields } from './DerivativeSettlementFields'

export function ContractAmendmentEditor({ contract, onSaved }: { contract: PortfolioDerivativeContractRecord; onSaved: () => Promise<void> }) {
  const [draft, setDraft] = useState(() => derivativeContractDraftFromRecord(contract))
  const [reviewer, setReviewer] = useState('')
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return <details>
    <summary>Amend confirmed contract terms</summary>
    <p>Changes are audited and replayed against recorded transactions. Product identity cannot be replaced.</p>
    <DerivativeSettlementFields draft={draft} setDraft={setDraft} />
    <label><span>Reviewer</span><input value={reviewer} onChange={event => setReviewer(event.target.value)} /></label>
    <label><span>Amendment evidence</span><textarea value={reason} onChange={event => setReason(event.target.value)} /></label>
    {error ? <p role="alert">{error}</p> : null}
    <button type="button" disabled={saving || !reviewer.trim() || !reason.trim()} onClick={async () => {
      setSaving(true); setError(null)
      try {
        const amended = derivativeContractFromDraft(draft)
        await amendPortfolioDerivativeContract(contract.portfolio_id, contract, amended.terms, reason.trim(), reviewer.trim())
        await onSaved()
      } catch (caught) { setError(caught instanceof Error ? caught.message : 'Failed to amend contract.') }
      finally { setSaving(false) }
    }}>Save audited amendment</button>
    {(contract.amendments ?? []).map(item => <p key={item.row_version} translate="no">{item.changed_at} · {item.reviewed_by} · {item.reason}</p>)}
  </details>
}
