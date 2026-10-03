import { useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { amendPortfolioDerivativeContract, type PortfolioDerivativeContractRecord } from '../lib/api'
import { derivativeContractDraftFromRecord, derivativeContractFromDraft } from '../lib/derivativeContractDraft'
import { DerivativeSettlementFields } from './DerivativeSettlementFields'
import { usePortfolioAccess } from './PortfolioAccessProvider'
import { usePortfolioSession } from './PortfolioSessionProvider'
import './contract-amendment-editor.css'

export function ContractAmendmentEditor({ contract, onSaved }: { contract: PortfolioDerivativeContractRecord; onSaved: () => Promise<void> }) {
  const { t } = useLanguage()
  const [draft, setDraft] = useState(() => derivativeContractDraftFromRecord(contract))
  const reviewer = usePortfolioSession()?.display_name ?? ''
  const canEdit = Boolean(usePortfolioAccess()?.can_edit)
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return <details className="contract-amendment-editor">
    <summary>Amend confirmed contract terms</summary>
    <p>Changes are audited and replayed against recorded transactions. Product identity cannot be replaced.</p>
    <fieldset className="contract-amendment-fields" disabled={!canEdit || saving}>
    <legend>{t('Confirmed terms and audit evidence')}</legend>
    <DerivativeSettlementFields draft={draft} setDraft={setDraft} />
    <label className="transaction-ticket-field"><span>Reviewer</span><input value={reviewer} readOnly /></label>
    <label className="transaction-ticket-field contract-amendment-wide"><span>Amendment evidence</span><textarea required aria-describedby="contract-amendment-evidence-help" value={reason} onChange={event => setReason(event.target.value)} /><small id="contract-amendment-evidence-help">{t('Identify the confirmed source and why these terms need correction.')}</small></label>
    {error ? <p role="alert">{error}</p> : null}
    <button className="contract-amendment-wide" type="button" disabled={!canEdit || saving || !reviewer.trim() || !reason.trim()} onClick={async () => {
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
