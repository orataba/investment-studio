import { useState } from 'react'
import { amendPortfolioDerivativeContract, type PortfolioDerivativeContractRecord } from '../lib/api'
import { derivativeContractDraftFromRecord, derivativeContractFromDraft } from '../lib/derivativeContractDraft'
import { DerivativeSettlementFields } from './DerivativeSettlementFields'
import { usePortfolioAccess } from './PortfolioAccessProvider'
import { usePortfolioSession } from './PortfolioSessionProvider'

export function ContractAmendmentEditor({ contract, onSaved }: { contract: PortfolioDerivativeContractRecord; onSaved: () => Promise<void> }) {
  const [draft, setDraft] = useState(() => derivativeContractDraftFromRecord(contract))
  const reviewer = usePortfolioSession()?.display_name ?? ''
  const canEdit = Boolean(usePortfolioAccess()?.can_edit)
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return <details>
    <summary>Amend confirmed contract terms</summary>
    <p>Changes are audited and replayed against recorded transactions. Product identity cannot be replaced.</p>
    <fieldset disabled={!canEdit || saving}>
    <DerivativeSettlementFields draft={draft} setDraft={setDraft} />
    <label><span>Reviewer</span><input value={reviewer} readOnly /></label>
    <label><span>Amendment evidence</span><textarea value={reason} onChange={event => setReason(event.target.value)} /></label>
    {error ? <p role="alert">{error}</p> : null}
    <button type="button" disabled={!canEdit || saving || !reviewer.trim() || !reason.trim()} onClick={async () => {
      setSaving(true); setError(null)
      try {
        const amended = derivativeContractFromDraft(draft)
        await amendPortfolioDerivativeContract(contract.portfolio_id, contract, amended.terms, reason.trim(), reviewer.trim())
        await onSaved()
      } catch (caught) { setError(caught instanceof Error ? caught.message : 'Failed to amend contract.') }
      finally { setSaving(false) }
    }}>Save audited amendment</button>
    </fieldset>
    {(contract.amendments ?? []).map(item => <p key={item.row_version} translate="no">{item.changed_at} · {item.reviewed_by} · {item.reason}</p>)}
  </details>
}
