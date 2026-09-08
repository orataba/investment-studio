import { useEffect, useRef, useState } from 'react'

import {
  createPortfolioOptionOutcome,
  getPortfolioAccounts,
  getPortfolioUnresolvedOptionActions,
  type PortfolioAccountRecord,
  type PortfolioUnresolvedOptionActionsResponse,
} from '../lib/api'
import { formatNumber, formatQuantity } from '../lib/format'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import InfoHint from './InfoHint'

type OptionOutcomeKind = 'expired' | 'cash_settled' | 'physical'

type OptionOutcomePromptProps = {
  portfolioId: string
  onRecorded?: (message: string) => void
}

export const OPTION_OUTCOME_RECORDED_EVENT = 'portfolio-option-outcome-recorded'

export default function OptionOutcomePrompt({
  portfolioId,
  onRecorded,
}: OptionOutcomePromptProps) {
  const [actionsResponse, setActionsResponse] =
    useState<PortfolioUnresolvedOptionActionsResponse | null>(null)
  const [accounts, setAccounts] = useState<PortfolioAccountRecord[]>([])
  const [open, setOpen] = useState(false)
  const [selectedActionKey, setSelectedActionKey] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<OptionOutcomeKind>('expired')
  const [quantity, setQuantity] = useState('')
  const [eventDate, setEventDate] = useState('')
  const [tradeTime, setTradeTime] = useState('')
  const [settlementDate, setSettlementDate] = useState('')
  const [stockAccountId, setStockAccountId] = useState('')
  const [allowStockShort, setAllowStockShort] = useState(false)
  const [cashAccountId, setCashAccountId] = useState('')
  const [cashAmount, setCashAmount] = useState('')
  const [fees, setFees] = useState('0')
  const [taxes, setTaxes] = useState('0')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestRef = useRef<{ signature: string; key: string } | null>(null)
  const dialogRef = useModalDialog(open, () => setOpen(false))

  const actions = actionsResponse?.portfolio_id === portfolioId
    ? actionsResponse.actions
    : []
  const selectedAction =
    actions.find((item) => item.action_key === selectedActionKey) ??
    actions[0] ??
    null
  const currency = (outcome === 'physical' && selectedAction?.derivative_contract.contract_type === 'option'
    ? selectedAction.derivative_contract.terms.strike_currency : null) ?? selectedAction?.derivative_contract.currency ?? ''
  const contractSettlement = selectedAction?.derivative_contract.contract_type === 'option'
    ? selectedAction.derivative_contract.terms.settlement_type : null
  const cashAccounts = accounts.filter(
    (account) =>
      account.account_category === 'cash' &&
      account.status === 'active' &&
      account.currency === currency,
  )
  const stockAccounts = accounts.filter(
    (account) =>
      account.account_category === 'security' &&
      account.status === 'active' &&
      account.currency === currency,
  )

  useEffect(() => {
    if (!portfolioId) {
      setActionsResponse(null)
      setAccounts([])
      setOpen(false)
      return
    }
    let cancelled = false
    setError(null)
    Promise.all([
      getPortfolioUnresolvedOptionActions(portfolioId),
      getPortfolioAccounts(portfolioId),
    ])
      .then(([nextActions, nextAccounts]) => {
        if (cancelled) return
        setActionsResponse(nextActions)
        setAccounts(nextAccounts.accounts)
        setSelectedActionKey((current) =>
          nextActions.actions.some((item) => item.action_key === current)
            ? current
            : nextActions.actions[0]?.action_key ?? null,
        )
        if (!nextActions.actions.length) {
          setOpen(false)
          return
        }
        const signature = nextActions.actions
          .map((item) => `${item.action_key}:${item.open_contract_quantity}`)
          .sort()
          .join('|')
        const storageKey = `investment_studio.option_actions.seen.${portfolioId}`
        let seenSignature: string | null = null
        try {
          seenSignature = window.sessionStorage.getItem(storageKey)
          window.sessionStorage.setItem(storageKey, signature)
        } catch {
          seenSignature = null
        }
        if (seenSignature !== signature) setOpen(true)
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Failed to load expired option actions.',
          )
        }
      })
    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    if (!selectedAction) return
    const nextCurrency = selectedAction.derivative_contract.currency
    const cashAccount = accounts.find(
      (account) =>
        account.account_category === 'cash' &&
        account.status === 'active' &&
        account.currency === nextCurrency,
    )
    const stockAccount = accounts.find(
      (account) =>
        account.account_category === 'security' &&
        account.status === 'active' &&
        account.currency === nextCurrency,
    )
    setOutcome('expired')
    setQuantity(String(selectedAction.open_contract_quantity))
    setNote('')
    setEventDate(selectedAction.expiry_date)
    setTradeTime('')
    setSettlementDate(selectedAction.expiry_date)
    setCashAccountId(cashAccount?.account_id ?? '')
    setStockAccountId(stockAccount?.account_id ?? '')
    setAllowStockShort(false)
    setCashAmount('')
    setFees('0')
    setTaxes('0')
    setError(null)
  }, [accounts, selectedAction])

  async function handleSubmit() {
    if (!selectedAction) return
    const parsedQuantity = Number(quantity)
    const parsedCashAmount = Number(cashAmount)
    const parsedFees = Number(fees || 0)
    const parsedTaxes = Number(taxes || 0)
    if (
      !Number.isFinite(parsedQuantity) ||
      parsedQuantity <= 0 ||
      parsedQuantity > selectedAction.open_contract_quantity
    ) {
      setError('Enter a quantity within the open contract balance.')
      return
    }
    const resolvedEventDate = outcome === 'expired' ? selectedAction.expiry_date : eventDate
    const resolvedSettlementDate = outcome === 'expired'
      ? selectedAction.expiry_date
      : settlementDate
    if (!resolvedEventDate || !resolvedSettlementDate) {
      setError('Event and settlement dates are required.')
      return
    }
    if (outcome === 'cash_settled' && (!Number.isFinite(parsedCashAmount) || parsedCashAmount <= 0)) {
      setError('Cash settlement requires a positive settlement amount.')
      return
    }
    if (outcome === 'cash_settled' && !cashAccountId) {
      setError('Choose the cash account receiving or paying settlement.')
      return
    }
    if (outcome === 'physical' && (!stockAccountId || !cashAccountId)) {
      setError('Physical settlement requires a security account and a cash account.')
      return
    }

    const payload = {
      derivative_contract_id: selectedAction.derivative_contract_id,
      note: note.trim() || null,
      side: selectedAction.side,
      outcome,
      quantity: parsedQuantity,
      event_date: resolvedEventDate,
      trade_time: tradeTime || null,
      settlement_date: resolvedSettlementDate,
      stock_account_id: outcome === 'physical' ? stockAccountId : null,
      ...(outcome === 'physical' && allowStockShort ? { allow_stock_short: true } : {}),
      settlement_cash_account_id:
        outcome === 'cash_settled' || outcome === 'physical' ? cashAccountId : null,
      cash_settlement_amount: outcome === 'cash_settled' ? parsedCashAmount : null,
      fees: outcome === 'expired' ? 0 : parsedFees,
      taxes: outcome === 'expired' ? 0 : parsedTaxes,
    } as const
    const requestSignature = JSON.stringify(payload)
    if (requestRef.current?.signature !== requestSignature) {
      const requestId = globalThis.crypto?.randomUUID?.() ?? String(Date.now())
      requestRef.current = {
        signature: requestSignature,
        key: `option-outcome-${requestId}`,
      }
    }

    setSaving(true)
    setError(null)
    try {
      await createPortfolioOptionOutcome(portfolioId, payload, requestRef.current.key)
    } catch (requestError) {
      setError(
        requestError instanceof Error ? requestError.message : 'Failed to record option outcome.',
      )
      setSaving(false)
      return
    }

    requestRef.current = null
    const remainingActions = actions.flatMap((action) => {
      if (action.action_key !== selectedAction.action_key) return [action]
      const remainingQuantity = action.open_contract_quantity - parsedQuantity
      return remainingQuantity > 1e-9
        ? [{ ...action, open_contract_quantity: remainingQuantity }]
        : []
    })
    if (actionsResponse) {
      setActionsResponse({
        ...actionsResponse,
        action_count: remainingActions.length,
        actions: remainingActions,
      })
    }
    setSelectedActionKey(remainingActions[0]?.action_key ?? null)
    setOpen(remainingActions.length > 0)

    const message = outcome === 'physical'
      ? selectedAction.side === 'written'
        ? 'Option assignment recorded.'
        : 'Option exercise recorded.'
      : 'Option outcome recorded.'
    window.dispatchEvent(
      new CustomEvent(OPTION_OUTCOME_RECORDED_EVENT, {
        detail: { portfolioId, message },
      }),
    )
    onRecorded?.(message)

    try {
      const nextActions = await getPortfolioUnresolvedOptionActions(portfolioId)
      setActionsResponse(nextActions)
      setSelectedActionKey(nextActions.actions[0]?.action_key ?? null)
      setOpen(nextActions.actions.length > 0)
    } catch {
      // The outcome is already committed; keep the locally reconciled action list.
    }
    setSaving(false)
  }

  if (!actions.length && !open) {
    return error ? <InfoHint label="Option action check failed" detail={error} tone="warning" /> : null
  }

  return (
    <>
      {actions.length ? (
        <button
          type="button"
          className="portfolio-settings-button option-action-entry"
          onClick={() => setOpen(true)}
        >
          {formatNumber(actions.length, 0)} option{actions.length === 1 ? '' : 's'} need action
        </button>
      ) : null}
      {open && selectedAction ? (
        <div className="portfolio-table-config-backdrop" onClick={() => setOpen(false)}>
          <div
            ref={dialogRef}
            className="portfolio-table-config-modal option-outcome-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Resolve expired option"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="portfolio-table-config-header option-outcome-header">
              <div>
                <div className="panel-title">Option outcome</div>
                <p>{actions.length} expired position{actions.length === 1 ? '' : 's'} need a final record.</p>
              </div>
              <button type="button" onClick={() => setOpen(false)}>Close</button>
            </div>

            {actions.length > 1 ? (
              <div className="option-outcome-action-strip" aria-label="Expired options">
                {actions.map((action) => (
                  <button
                    key={action.action_key}
                    type="button"
                    className={action.action_key === selectedAction.action_key ? 'is-active' : ''}
                    onClick={() => setSelectedActionKey(action.action_key)}
                  >
                    <strong translate="no">{action.derivative_contract.contract_name}</strong>
                    <span>{action.side === 'written' ? 'Written' : 'Long'} · {formatQuantity(action.open_contract_quantity)}</span>
                  </button>
                ))}
              </div>
            ) : null}

            <form
              className="option-outcome-form"
              onSubmit={(submitEvent) => {
                submitEvent.preventDefault()
                void handleSubmit()
              }}
            >
              <div className="option-outcome-contract-card">
                <div><span>Contract</span><strong translate="no">{selectedAction.derivative_contract.contract_name}</strong></div>
                <div><span>Position</span><strong>{selectedAction.side === 'written' ? 'Written' : 'Long'} {formatQuantity(selectedAction.open_contract_quantity)}</strong></div>
                <div><span>Underlying</span><strong>{selectedAction.underlying_instrument_id}</strong></div>
                <div><span>Expiry</span><strong>{selectedAction.expiry_date}</strong></div>
              </div>

              <fieldset className="option-outcome-choice-grid">
                <legend>What happened?</legend>
                {([
                  ['expired', 'Expired', 'No exercise, assignment, or settlement cash.'],
                  ['cash_settled', 'Cash settled', 'Close the option and record the reviewed cash amount.'],
                  [
                    'physical',
                    selectedAction.side === 'written' ? 'Assigned' : 'Exercised',
                    'Record the option outcome and resulting stock delivery as one activity.',
                  ],
                ] as Array<[OptionOutcomeKind, string, string]>).filter(([value]) =>
                  !(value === 'physical' && contractSettlement === 'cash') &&
                  !(value === 'cash_settled' && contractSettlement === 'physical'),
                ).map(([value, label, description]) => (
                  <label key={value} className={outcome === value ? 'is-active' : ''}>
                    <input
                      type="radio"
                      name="option-outcome"
                      value={value}
                      checked={outcome === value}
                      onChange={() => setOutcome(value)}
                    />
                    <span>
                      <strong className={value === 'physical' ? 'portfolio-title-with-hint' : undefined}>
                        {label}
                        {value === 'physical' ? (
                          <InfoHint
                            label="Physical settlement"
                            detail="Stock quantity and strike cash are derived from the contract; physical delivery uses accounts in the strike currency."
                            tone="warning"
                          />
                        ) : null}
                      </strong>
                      <small>{description}</small>
                    </span>
                  </label>
                ))}
              </fieldset>

              <div className="option-outcome-fields">
                <label>
                  Settlement Evidence
                  <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Broker exercise, assignment or settlement confirmation" />
                </label>
                {!contractSettlement && <p className="holding-detail-note">Settlement terms are unconfirmed. Use the broker's actual outcome; insufficient stock does not turn physical delivery into cash settlement.</p>}
                <label>
                  Quantity
                  <input
                    type="number"
                    min="0.000000000001"
                    max={selectedAction.open_contract_quantity}
                    step="any"
                    value={quantity}
                    onChange={(changeEvent) => setQuantity(changeEvent.target.value)}
                  />
                </label>
                {outcome !== 'expired' ? (
                  <>
                    <label>
                      Event date
                      <input
                        type="date"
                        max={selectedAction.expiry_date}
                        value={eventDate}
                        onChange={(changeEvent) => setEventDate(changeEvent.target.value)}
                      />
                    </label>
                    <label>
                      Settlement date
                      <input
                        type="date"
                        min={eventDate}
                        value={settlementDate}
                        onChange={(changeEvent) => setSettlementDate(changeEvent.target.value)}
                      />
                    </label>
                    <label>
                      Trade Time (optional)
                      <input type="time" step={60} value={tradeTime} onChange={(event) => setTradeTime(event.target.value)} />
                    </label>
                  </>
                ) : null}
                {outcome === 'physical' ? (
                  <label><input type="checkbox" checked={allowStockShort} onChange={event => setAllowStockShort(event.target.checked)} />Broker confirmed a short stock position on delivery</label>
                ) : null}
                {outcome === 'physical' ? (
                  <label>
                    Security account
                    <select value={stockAccountId} onChange={(changeEvent) => setStockAccountId(changeEvent.target.value)}>
                      <option value="">Choose account</option>
                      {stockAccounts.map((account) => (
                        <option key={account.account_id} value={account.account_id}>{account.account_name} · {account.currency}</option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {outcome !== 'expired' ? (
                  <label>
                    Cash account
                    <select value={cashAccountId} onChange={(changeEvent) => setCashAccountId(changeEvent.target.value)}>
                      <option value="">Choose account</option>
                      {cashAccounts.map((account) => (
                        <option key={account.account_id} value={account.account_id}>{account.account_name} · {account.currency}</option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {outcome === 'cash_settled' ? (
                  <label>
                    Settlement amount ({currency})
                    <input type="number" min="0.00000001" step="any" value={cashAmount} onChange={(changeEvent) => setCashAmount(changeEvent.target.value)} />
                  </label>
                ) : null}
                {outcome !== 'expired' ? (
                  <>
                    <label>Fees<input type="number" min="0" step="any" value={fees} onChange={(changeEvent) => setFees(changeEvent.target.value)} /></label>
                    <label>Taxes<input type="number" min="0" step="any" value={taxes} onChange={(changeEvent) => setTaxes(changeEvent.target.value)} /></label>
                  </>
                ) : null}
              </div>

              {error ? <div className="inline-notice inline-notice-error" role="alert">{error}</div> : null}
              <div className="portfolio-table-config-actions option-outcome-actions">
                <button type="button" onClick={() => setOpen(false)}>Later</button>
                <button type="submit" className="button-primary" disabled={saving}>
                  {saving ? 'Recording…' : 'Confirm & record'}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </>
  )
}
