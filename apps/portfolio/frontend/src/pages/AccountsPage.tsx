import { useEffect, useState } from 'react'
import { Link, Navigate, useParams, useSearchParams } from 'react-router-dom'

import CalculationStatus from '../components/CalculationStatus'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import {
  SUPPORTED_PORTFOLIO_CURRENCIES,
  createPortfolioAccount,
  updatePortfolioAccount,
  type PortfolioAccountRecord,
  getPortfolioAccountsWorkspace,
  type PortfolioAccountCreatePayload,
  type PortfolioAccountUpdatePayload,
  type PortfolioAccountPositionRecord,
  type PortfolioAccountWorkspaceAccount,
  type PortfolioAccountsWorkspaceResponse,
  type PortfolioLedgerPostingRecord,
  type PortfolioTransactionRecord,
} from '../lib/api'
import { formatCurrency, formatLabel, formatNumber, formatSignedCurrency, formatUnitPrice } from '../lib/format'

const ACCOUNT_SCOPE_OPTIONS = ['equity', 'fund', 'bond', 'other'] as const

function localTodayIso() {
  const now = new Date()
  const timezoneOffsetMs = now.getTimezoneOffset() * 60 * 1000
  return new Date(now.getTime() - timezoneOffsetMs).toISOString().slice(0, 10)
}

function primaryIdentifier(position: {
  instrument_id?: string | null
  instrument_ref?: { identifiers: Array<{ identifier_value: string; is_primary: boolean }> } | null
}) {
  return (
    position.instrument_ref?.identifiers.find((item) => item.is_primary)?.identifier_value ??
    position.instrument_ref?.identifiers[0]?.identifier_value ??
    position.instrument_id ??
    '—'
  )
}

function formatAccountInstrumentScope(account: PortfolioAccountRecord) {
  if (account.account_type !== 'securities_account') {
    return '—'
  }

  if (!account.allowed_instrument_types?.length) {
    return 'All supported'
  }

  return account.allowed_instrument_types.map((instrumentType) => formatLabel(instrumentType)).join(' / ')
}

function accountTransactionHref(portfolioId: string, accountId: string, transactionId: string) {
  const params = new URLSearchParams({
    account_id: accountId,
    transaction_id: transactionId,
  })
  return `/portfolios/${portfolioId}/transactions?${params.toString()}`
}

export default function AccountsPage() {
  const { portfolioId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const [workspace, setWorkspace] = useState<PortfolioAccountsWorkspaceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerMode, setDrawerMode] = useState<'create' | 'edit'>('create')
  const [editingAccountId, setEditingAccountId] = useState<string | null>(null)
  const [pendingCostMethodChange, setPendingCostMethodChange] = useState<PendingCostMethodChange | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [form, setForm] = useState<AccountFormState>(buildInitialAccountForm)

  async function refreshWorkspace(nextAccountId?: string | null) {
    if (!portfolioId) {
      setWorkspace(null)
      setError('Portfolio id is required.')
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const targetAccountId = nextAccountId ?? searchParams.get('account_id') ?? undefined
      const response = await getPortfolioAccountsWorkspace(portfolioId, targetAccountId || undefined)
      setWorkspace(response)
      const expectedAccountId = response.selected_account_id ?? ''
      const currentAccountId = searchParams.get('account_id') ?? ''
      if (expectedAccountId !== currentAccountId) {
        const next = new URLSearchParams(searchParams)
        if (expectedAccountId) {
          next.set('account_id', expectedAccountId)
        } else {
          next.delete('account_id')
        }
        setSearchParams(next, { replace: true })
      }
      setError(null)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Failed to load accounts workspace.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false

    if (!portfolioId) {
      setWorkspace(null)
      setError('Portfolio id is required.')
      setLoading(false)
      return () => {
        cancelled = true
      }
    }

    setLoading(true)
    getPortfolioAccountsWorkspace(portfolioId, searchParams.get('account_id') ?? undefined)
      .then((response) => {
        if (cancelled) {
          return
        }
        setWorkspace(response)
        setForm(buildInitialAccountForm(response.accounts.map((item) => item.account)))
        const expectedAccountId = response.selected_account_id ?? ''
        const currentAccountId = searchParams.get('account_id') ?? ''
        if (expectedAccountId !== currentAccountId) {
          const next = new URLSearchParams(searchParams)
          if (expectedAccountId) {
            next.set('account_id', expectedAccountId)
          } else {
            next.delete('account_id')
          }
          setSearchParams(next, { replace: true })
        }
        setError(null)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : 'Failed to load accounts workspace.')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [portfolioId, searchParams, setSearchParams])

  const accountRecords = workspace?.accounts.map((item) => item.account) ?? []
  const depositAccounts = accountRecords.filter((account) => account.account_type === 'deposit_account')
  const compatibleDepositAccounts = depositAccounts.filter(
    (account) => account.currency.toUpperCase() === form.currency.trim().toUpperCase(),
  )

  useEffect(() => {
    if (form.account_type !== 'securities_account') {
      if (
        form.default_settlement_cash_account_id ||
        form.cost_basis_method !== 'fifo' ||
        form.allowed_instrument_types.length
      ) {
        setForm((current) => ({
          ...current,
          default_settlement_cash_account_id: '',
          cost_basis_method: 'fifo',
          allowed_instrument_types: [],
        }))
      }
      return
    }

    if (
      form.default_settlement_cash_account_id &&
      compatibleDepositAccounts.some((account) => account.account_id === form.default_settlement_cash_account_id)
    ) {
      return
    }

    setForm((current) => ({
      ...current,
      default_settlement_cash_account_id: compatibleDepositAccounts[0]?.account_id ?? '',
    }))
  }, [
    compatibleDepositAccounts,
    form.account_type,
    form.allowed_instrument_types.length,
    form.cost_basis_method,
    form.default_settlement_cash_account_id,
  ])

  const visibleAccounts = workspace?.accounts ?? []
  const resolvedSelectedAccountId =
    workspace?.selected_account_id ?? visibleAccounts[0]?.account.account_id ?? ''
  const selectedAccount =
    visibleAccounts.find((item) => item.account.account_id === resolvedSelectedAccountId) ?? visibleAccounts[0] ?? null
  const editingAccount =
    visibleAccounts.find((item) => item.account.account_id === editingAccountId)?.account ?? null

  const visibleLedgerPostings = workspace?.ledger_postings ?? []
  const visiblePositions = workspace?.positions ?? []

  if (!portfolioId) {
    return <Navigate replace to="/portfolios" />
  }

  function accountPayloadFromForm(): PortfolioAccountCreatePayload {
    return {
      account_name: form.account_name.trim(),
      account_type: form.account_type,
      currency: form.currency.trim().toUpperCase(),
      institution: form.institution.trim() || null,
      default_settlement_cash_account_id:
        form.account_type === 'securities_account' ? form.default_settlement_cash_account_id || null : null,
      cost_basis_method: form.account_type === 'securities_account' ? form.cost_basis_method : null,
      allowed_instrument_types:
        form.account_type === 'securities_account' && form.allowed_instrument_types.length
          ? [...form.allowed_instrument_types]
          : null,
      opened_at: form.opened_at || null,
      closed_at: form.closed_at || null,
      status: form.status || 'active',
    }
  }

  async function applyAccountUpdate(accountId: string, payload: PortfolioAccountUpdatePayload) {
    const updated = await updatePortfolioAccount(portfolioId, accountId, payload)
    setPendingCostMethodChange(null)
    setDrawerOpen(false)
    setNotice(`Updated ${updated.account_name}.`)
    await refreshWorkspace(updated.account_id)
  }

  async function handleSaveAccount() {
    setFormError(null)
    setNotice(null)

    if (!form.account_name.trim()) {
      setFormError('Enter an account name.')
      return
    }

    const payload = accountPayloadFromForm()

    try {
      if (drawerMode === 'edit') {
        if (!editingAccountId) {
          setFormError('Select an account to edit.')
          return
        }
        const updatePayload: PortfolioAccountUpdatePayload = {
          account_name: payload.account_name,
          institution: payload.institution,
          default_settlement_cash_account_id: payload.default_settlement_cash_account_id,
          cost_basis_method: payload.cost_basis_method,
          allowed_instrument_types: payload.allowed_instrument_types,
          opened_at: payload.opened_at,
          closed_at: payload.closed_at,
          status: payload.status,
        }
        const currentCostMethod = editingAccount?.cost_basis_method ?? 'fifo'
        const nextCostMethod = updatePayload.cost_basis_method ?? currentCostMethod
        if (
          editingAccount?.account_type === 'securities_account' &&
          nextCostMethod !== currentCostMethod
        ) {
          setPendingCostMethodChange({
            accountId: editingAccountId,
            accountName: updatePayload.account_name || editingAccount.account_name,
            currentMethod: currentCostMethod,
            nextMethod: nextCostMethod,
            payload: updatePayload,
          })
          return
        }

        await applyAccountUpdate(editingAccountId, updatePayload)
        return
      }

      const created = await createPortfolioAccount(portfolioId, payload)
      setDrawerOpen(false)
      setNotice(`Added ${formatLabel(created.account_type)} ${created.account_name}.`)
      setForm(buildInitialAccountForm(accountRecords))
      await refreshWorkspace(created.account_id)
    } catch (requestError) {
      setFormError(requestError instanceof Error ? requestError.message : 'Failed to save account.')
    }
  }

  return (
    <PortfolioWorkspaceLayout
      activeSection="Accounts"
      toolbarLabel="View: Account Ledger"
      controls={
        workspace ? (
          <div className="portfolio-summary-strip">
            <article className="summary-card">
              <span className="summary-card-label">Accounts</span>
              <strong className="summary-card-value">{workspace.summary.account_count}</strong>
            </article>
            <article className="summary-card">
              <span className="summary-card-label">Ledger Postings</span>
              <strong className="summary-card-value">{workspace.summary.ledger_posting_count}</strong>
            </article>
            <article className="summary-card">
              <span className="summary-card-label">Position Lines</span>
              <strong className="summary-card-value">{workspace.summary.position_line_count}</strong>
            </article>
            <article className="summary-card summary-card-warning">
              <span className="summary-card-label">Source</span>
              <strong className="summary-card-value">Derived from Transactions</strong>
            </article>
          </div>
        ) : undefined
      }
    >
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar">
          <div className="panel-title">Accounts</div>
        </div>

        {notice ? <div className="inline-notice inline-notice-success">{notice}</div> : null}
        {loading ? <CalculationStatus label={workspace ? 'Recalculating…' : 'Loading…'} /> : null}
        {error ? <div className="error-state">{error}</div> : null}

        {!loading && !error && workspace ? (
          <>
            <section className="account-toolbar">
              <label className="account-toolbar-label">
                <span>Selected Account</span>
                <select
                  className="toolbar-select transaction-filter-input"
                  value={resolvedSelectedAccountId}
                  onChange={(event) => {
                    const next = new URLSearchParams(searchParams)
                    next.set('account_id', event.target.value)
                    setSearchParams(next, { replace: true })
                  }}
                >
                  {visibleAccounts.map((accountRow) => (
                    <option key={accountRow.account.account_id} value={accountRow.account.account_id}>
                      {accountRow.account.account_name}
                    </option>
                  ))}
                </select>
              </label>
              {selectedAccount ? (
                <div className="account-toolbar-meta">
                  <span className="portfolio-subhead-meta">
                    {formatLabel(selectedAccount.account.account_type)}
                  </span>
                  <span className="portfolio-subhead-meta">
                    Settlement: {selectedAccount.default_settlement_cash_account_name || '—'}
                  </span>
                </div>
              ) : null}
              <div className="transaction-filter-actions">
                <button
                  type="button"
                  className="toolbar-link"
                  disabled={!selectedAccount}
                  onClick={() => {
                    if (!selectedAccount) {
                      return
                    }
                    setDrawerMode('edit')
                    setEditingAccountId(selectedAccount.account.account_id)
                    setDrawerOpen(true)
                    setFormError(null)
                    setForm(buildAccountFormFromRecord(selectedAccount.account))
                  }}
                >
                  Edit Account
                </button>
                <button
                  type="button"
                  className="toolbar-link button-primary"
                  onClick={() => {
                    setDrawerMode('create')
                    setEditingAccountId(null)
                    setDrawerOpen(true)
                    setFormError(null)
                    setForm(buildInitialAccountForm(accountRecords))
                  }}
                >
                  Add Account
                </button>
              </div>
            </section>

            <section className="accounts-grid">
              <article className="panel account-directory-panel">
                <div className="panel-header">
                  <div className="panel-title">Account Directory</div>
                </div>
                <div className="table-shell">
                  <table className="accounts-table">
                    <thead>
                      <tr>
                        <th>Account</th>
                        <th>Type</th>
                        <th>Default Cash</th>
                        <th>Cost Method</th>
                        <th>Instrument Scope</th>
                        <th>Cash Balance</th>
                        <th>Position Lines</th>
                        <th>Linked Facts</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleAccounts.map((accountRow) => {
                        const isActive = accountRow.account.account_id === resolvedSelectedAccountId
                        return (
                          <tr
                            key={accountRow.account.account_id}
                            className={isActive ? 'account-row-active' : ''}
                            onClick={() => {
                              const next = new URLSearchParams(searchParams)
                              next.set('account_id', accountRow.account.account_id)
                              setSearchParams(next, { replace: true })
                            }}
                          >
                            <td className="transaction-account-cell">
                              <div className="holding-name-stack">
                                <span>{accountRow.account.account_name}</span>
                                <span className="holding-secondary">
                                  {accountRow.account.institution || accountRow.account.currency}
                                </span>
                              </div>
                            </td>
                            <td>{formatLabel(accountRow.account.account_type)}</td>
                            <td>{accountRow.default_settlement_cash_account_name || '—'}</td>
                            <td>{accountRow.account.cost_basis_method ? formatLabel(accountRow.account.cost_basis_method) : '—'}</td>
                            <td>{formatAccountInstrumentScope(accountRow.account)}</td>
                            <td>{formatCurrency(accountRow.derived_cash_balance, accountRow.account.currency)}</td>
                            <td>{accountRow.position_line_count}</td>
                            <td>
                              {accountRow.linked_transaction_count} txn / {accountRow.linked_posting_count} postings
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </article>

              <div className="account-side-stack">
                <article className="panel">
                  <div className="panel-header">
                    <div className="panel-title">Selected Account Summary</div>
                  </div>
                  {selectedAccount ? (
                    <div className="account-summary-list">
                      <div className="account-summary-row">
                        <span>Cash Balance</span>
                        <strong>
                          {formatCurrency(
                            selectedAccount.derived_cash_balance,
                            selectedAccount.account.currency,
                          )}
                        </strong>
                      </div>
                      <div className="account-summary-row">
                        <span>
                          Position Market Value (
                          {selectedAccount.position_market_value_currency ?? workspace.base_currency})
                        </span>
                        <strong>
                          {selectedAccount.position_market_value != null
                            ? formatCurrency(
                                selectedAccount.position_market_value,
                                selectedAccount.position_market_value_currency ?? workspace.base_currency,
                              )
                            : '—'}
                        </strong>
                      </div>
                      <div className="account-summary-row">
                        <span>Default Settlement</span>
                        <strong>{selectedAccount.default_settlement_cash_account_name || '—'}</strong>
                      </div>
                      <div className="account-summary-row">
                        <span>Cost Method</span>
                        <strong>{selectedAccount.account.cost_basis_method ? formatLabel(selectedAccount.account.cost_basis_method) : '—'}</strong>
                      </div>
                      <div className="account-summary-row">
                        <span>Instrument Scope</span>
                        <strong>{formatAccountInstrumentScope(selectedAccount.account)}</strong>
                      </div>
                      <div className="account-summary-row">
                        <span>Opened</span>
                        <strong>{selectedAccount.account.opened_at || '—'}</strong>
                      </div>
                      <div className="account-summary-row">
                        <span>Status</span>
                        <strong>{formatLabel(selectedAccount.account.status)}</strong>
                      </div>
                    </div>
                  ) : (
                    <div className="empty-state">No account selected.</div>
                  )}
                </article>

                <article className="panel">
                  <div className="panel-header">
                    <div className="panel-title">Account Positions</div>
                  </div>
                  {visiblePositions.length ? (
                    <div className="table-shell">
                      <table className="accounts-table">
                        <thead>
                          <tr>
                            <th>Instrument</th>
                            <th>Quantity</th>
                            <th>Cost Basis</th>
                            <th>Last Price</th>
                            <th>Market Value</th>
                          </tr>
                        </thead>
                        <tbody>
                          {visiblePositions.map((position) => (
                            <PositionRow key={`${position.account_id}-${position.instrument_id}`} position={position} />
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="empty-state">No derived positions for the selected account yet.</div>
                  )}
                </article>

                <article className="panel">
                  <div className="panel-header">
                    <div className="panel-title">Linked Transactions</div>
                    <div className="portfolio-detail-meta">
                      {workspace.linked_transactions_summary?.total_transactions ?? 0} facts
                    </div>
                  </div>
                  {workspace.linked_transactions.length ? (
                      <div className="table-shell">
                        <table className="accounts-table">
                          <thead>
                            <tr>
                              <th>Trade Date</th>
                              <th>Type</th>
                              <th>Instrument</th>
                              <th>Gross</th>
                              <th>Net Cash</th>
                              <th>Open</th>
                            </tr>
                          </thead>
                          <tbody>
                            {workspace.linked_transactions.map((transaction) => (
                              <AccountTransactionRow
                                key={transaction.transaction_id}
                                portfolioId={portfolioId}
                                transaction={transaction}
                                accountId={resolvedSelectedAccountId}
                              />
                            ))}
                          </tbody>
                        </table>
                      </div>
                  ) : (
                    <div className="empty-state">No transaction facts are linked to this account yet.</div>
                  )}
                </article>
              </div>
            </section>

            <section className="panel">
              <div className="panel-header">
                <div className="panel-title">Ledger Slice</div>
                <div className="portfolio-detail-meta">
                  {selectedAccount?.account.account_name || 'All Accounts'}
                </div>
              </div>
              <div className="table-shell">
                <table className="accounts-table">
                  <thead>
                    <tr>
                      <th>Trade Date</th>
                      <th>Settle</th>
                      <th>Posting Role</th>
                      <th>Txn Type</th>
                      <th>Instrument</th>
                      <th>Cash Delta</th>
                      <th>Quantity Delta</th>
                      <th>Cost Basis Delta</th>
                      <th>Txn</th>
                      <th>Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleLedgerPostings.map((posting) => (
                      <LedgerPostingRow key={posting.posting_id} posting={posting} />
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        ) : null}
      </section>

      {drawerOpen ? (
        <div className="transaction-drawer-backdrop" role="presentation" onClick={() => setDrawerOpen(false)}>
          <aside
            className="transaction-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Add account"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="transaction-drawer-header">
              <div>
                <div className="panel-title">{drawerMode === 'edit' ? 'Edit Account' : 'Add Account'}</div>
              </div>
              <button type="button" className="toolbar-link" onClick={() => setDrawerOpen(false)}>
                Close
              </button>
            </div>

            <div className="transaction-form">
              <div className="transaction-form-grid">
                <label>
                  <span>Account Name</span>
                  <input
                    value={form.account_name}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        account_name: event.target.value,
                      }))
                    }
                  />
                </label>

                <label>
                  <span>Account Type</span>
                  <select
                    value={form.account_type}
                    disabled={drawerMode === 'edit'}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        account_type: event.target.value,
                      }))
                    }
                  >
                    <option value="deposit_account">Deposit Account</option>
                    <option value="securities_account">Securities Account</option>
                  </select>
                </label>

                <label>
                  <span>Currency</span>
                  <select
                    value={form.currency}
                    disabled={drawerMode === 'edit'}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        currency: event.target.value,
                      }))
                    }
                  >
                    {SUPPORTED_PORTFOLIO_CURRENCIES.map((currencyCode) => (
                      <option key={currencyCode} value={currencyCode}>
                        {currencyCode}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  <span>Institution</span>
                  <input
                    value={form.institution}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        institution: event.target.value,
                      }))
                    }
                  />
                </label>

                <label>
                  <span>Opened At</span>
                  <input
                    type="date"
                    value={form.opened_at}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        opened_at: event.target.value,
                      }))
                    }
                  />
                </label>

                <label>
                  <span>Status</span>
                  <select
                    value={form.status}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        status: event.target.value,
                      }))
                    }
                  >
                    <option value="active">Active</option>
                    <option value="closed">Closed</option>
                  </select>
                </label>

                {form.account_type === 'securities_account' ? (
                  <label>
                    <span>Default Settlement Cash</span>
                    <select
                      value={form.default_settlement_cash_account_id}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          default_settlement_cash_account_id: event.target.value,
                        }))
                      }
                      disabled={compatibleDepositAccounts.length === 0}
                    >
                      {compatibleDepositAccounts.map((account) => (
                        <option key={account.account_id} value={account.account_id}>
                          {account.account_name}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                {form.account_type === 'securities_account' ? (
                  <label>
                    <span>Cost Method</span>
                    <select
                      value={form.cost_basis_method}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          cost_basis_method: event.target.value as 'moving_average' | 'fifo',
                        }))
                      }
                    >
                      <option value="fifo">FIFO</option>
                      <option value="moving_average">Moving Average</option>
                    </select>
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                {form.account_type === 'securities_account' ? (
                  <fieldset className="transaction-form-fieldset">
                    <legend>Instrument Scope</legend>
                    <div className="transaction-checkbox-grid">
                      {ACCOUNT_SCOPE_OPTIONS.map((instrumentType) => {
                        const checked = form.allowed_instrument_types.includes(instrumentType)
                        return (
                          <label key={instrumentType} className="transaction-checkbox-option">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) =>
                                setForm((current) => ({
                                  ...current,
                                  allowed_instrument_types: event.target.checked
                                    ? [...current.allowed_instrument_types, instrumentType].filter(
                                        (value, index, array) => array.indexOf(value) === index,
                                      )
                                    : current.allowed_instrument_types.filter((value) => value !== instrumentType),
                                }))
                              }
                            />
                            <span>{formatLabel(instrumentType)}</span>
                          </label>
                        )
                      })}
                    </div>
                    <div className="portfolio-detail-meta">
                      Leave empty to allow every currently supported inbound instrument type.
                    </div>
                  </fieldset>
                ) : (
                  <div className="transaction-form-spacer" />
                )}

                {form.status === 'closed' ? (
                  <label>
                    <span>Closed At</span>
                    <input
                      type="date"
                      value={form.closed_at}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          closed_at: event.target.value,
                        }))
                      }
                    />
                  </label>
                ) : (
                  <div className="transaction-form-spacer" />
                )}
              </div>

              {form.account_type === 'securities_account' && compatibleDepositAccounts.length === 0 ? (
                <div className="portfolio-detail-meta">
                  Settlement cash account required.
                </div>
              ) : null}

              {formError ? <div className="error-state transaction-form-error">{formError}</div> : null}

              <div className="transaction-form-footer">
                <button type="button" className="toolbar-link button-primary" onClick={() => void handleSaveAccount()}>
                  {drawerMode === 'edit' ? 'Update Account' : 'Save Account'}
                </button>
              </div>
            </div>
          </aside>
        </div>
      ) : null}

      {pendingCostMethodChange ? (
        <div
          className="transaction-entry-backdrop"
          role="presentation"
          onClick={() => setPendingCostMethodChange(null)}
        >
          <div
            className="transaction-entry-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Confirm cost method change"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="transaction-entry-modal-header">
              <div>
                <div className="panel-title">Confirm Cost Method Change</div>
                <div className="portfolio-detail-meta">{pendingCostMethodChange.accountName}</div>
              </div>
              <button type="button" className="toolbar-link" onClick={() => setPendingCostMethodChange(null)}>
                Close
              </button>
            </div>
            <div className="transaction-form">
              <div className="portfolio-detail-meta">
                Changing cost method from {formatCostMethodLabel(pendingCostMethodChange.currentMethod)} to{' '}
                {formatCostMethodLabel(pendingCostMethodChange.nextMethod)} will replay this account's transaction
                history and recalculate cost basis, average cost, realized gain, and unrealized gain. Market value,
                cash flows, and time-weighted return are not changed by this accounting method.
              </div>
              <div className="transaction-form-footer">
                <button type="button" className="toolbar-link" onClick={() => setPendingCostMethodChange(null)}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="toolbar-link button-primary"
                  onClick={() =>
                    void applyAccountUpdate(pendingCostMethodChange.accountId, pendingCostMethodChange.payload).catch(
                      (requestError) => {
                        setPendingCostMethodChange(null)
                        setFormError(requestError instanceof Error ? requestError.message : 'Failed to save account.')
                      },
                    )
                  }
                >
                  Recalculate and Update
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </PortfolioWorkspaceLayout>
  )
}

type AccountFormState = {
  account_name: string
  account_type: string
  currency: string
  institution: string
  default_settlement_cash_account_id: string
  cost_basis_method: 'moving_average' | 'fifo'
  allowed_instrument_types: string[]
  opened_at: string
  closed_at: string
  status: string
}

type PendingCostMethodChange = {
  accountId: string
  accountName: string
  currentMethod: 'moving_average' | 'fifo'
  nextMethod: 'moving_average' | 'fifo'
  payload: PortfolioAccountUpdatePayload
}

function formatCostMethodLabel(method: 'moving_average' | 'fifo') {
  return method === 'moving_average' ? 'Moving Average' : 'FIFO'
}

function buildInitialAccountForm(accounts: PortfolioAccountRecord[] = []): AccountFormState {
  const defaultCashAccount = accounts.find((account) => account.account_type === 'deposit_account')
  return {
    account_name: '',
    account_type: 'deposit_account',
    currency: defaultCashAccount?.currency ?? 'USD',
    institution: '',
    default_settlement_cash_account_id: defaultCashAccount?.account_id ?? '',
    cost_basis_method: 'fifo',
    allowed_instrument_types: [],
    opened_at: localTodayIso(),
    closed_at: '',
    status: 'active',
  }
}

function buildAccountFormFromRecord(account: PortfolioAccountRecord): AccountFormState {
  return {
    account_name: account.account_name,
    account_type: account.account_type,
    currency: account.currency,
    institution: account.institution ?? '',
    default_settlement_cash_account_id: account.default_settlement_cash_account_id ?? '',
    cost_basis_method: account.cost_basis_method ?? 'fifo',
    allowed_instrument_types: account.allowed_instrument_types ?? [],
    opened_at: account.opened_at ?? '',
    closed_at: account.closed_at ?? '',
    status: account.status || 'active',
  }
}

function AccountTransactionRow({
  portfolioId,
  transaction,
  accountId,
}: {
  portfolioId: string
  transaction: PortfolioTransactionRecord
  accountId: string
}) {
  return (
    <tr>
      <td>{transaction.trade_date}</td>
      <td>
        <span className="transaction-type-pill">{formatLabel(transaction.transaction_type)}</span>
      </td>
      <td className="holding-name-cell">
        {transaction.instrument_ref ? (
          <div className="holding-name-stack">
            <span>{primaryIdentifier(transaction.instrument_ref)}</span>
            <span className="holding-secondary">{transaction.instrument_ref.instrument_name}</span>
          </div>
        ) : (
          <span className="holding-secondary">Cash ledger</span>
        )}
      </td>
      <td>{formatCurrency(transaction.gross_amount, transaction.currency)}</td>
      <td className={transaction.net_cash_effect != null && transaction.net_cash_effect < 0 ? 'negative-cell' : ''}>
        {formatSignedCurrency(transaction.net_cash_effect, transaction.currency)}
      </td>
      <td>
        <Link
          className="table-inline-link"
          to={accountTransactionHref(portfolioId, accountId, transaction.transaction_id)}
        >
          Ledger Inspector
        </Link>
      </td>
    </tr>
  )
}

function PositionRow({ position }: { position: PortfolioAccountPositionRecord }) {
  return (
    <tr>
      <td className="holding-name-cell">
        <div className="holding-name-stack">
          <span>{primaryIdentifier(position)}</span>
          <span className="holding-secondary">{position.instrument_ref.instrument_name}</span>
        </div>
      </td>
      <td>{formatNumber(position.quantity, 2)}</td>
      <td>{position.cost_basis != null ? formatCurrency(position.cost_basis, position.currency) : '—'}</td>
      <td>{position.last_price != null ? formatUnitPrice(position.last_price, position.currency) : '—'}</td>
      <td>{position.market_value != null ? formatCurrency(position.market_value, position.currency) : '—'}</td>
    </tr>
  )
}

function LedgerPostingRow({ posting }: { posting: PortfolioLedgerPostingRecord }) {
  return (
    <tr>
      <td>{posting.trade_date}</td>
      <td>{posting.settlement_date}</td>
      <td>
        <span className="transaction-type-pill">{formatLabel(posting.posting_role)}</span>
      </td>
      <td>{formatLabel(posting.source_transaction_type)}</td>
      <td className="holding-name-cell">
        {posting.instrument_ref ? (
          <div className="holding-name-stack">
            <span>{primaryIdentifier(posting)}</span>
            <span className="holding-secondary">{posting.instrument_ref.instrument_name}</span>
          </div>
        ) : (
          <span className="holding-secondary">Cash ledger</span>
        )}
      </td>
      <td className={posting.cash_amount_delta != null && posting.cash_amount_delta < 0 ? 'negative-cell' : ''}>
        {formatSignedCurrency(posting.cash_amount_delta, posting.currency)}
      </td>
      <td>{formatNumber(posting.quantity_delta, 2)}</td>
      <td
        className={posting.cost_basis_delta != null && posting.cost_basis_delta < 0 ? 'negative-cell' : ''}
      >
        {posting.cost_basis_delta != null ? formatSignedCurrency(posting.cost_basis_delta, posting.currency) : '—'}
      </td>
      <td>{posting.transaction_id}</td>
      <td className="transaction-note-cell">{posting.note || '—'}</td>
    </tr>
  )
}
