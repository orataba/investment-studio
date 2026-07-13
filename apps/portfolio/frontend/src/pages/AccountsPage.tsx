import { useEffect, useMemo, useRef, useState } from 'react'
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
import {
  formatCurrency,
  formatLabel,
  formatNumber,
  formatSignedCurrency,
  formatUnitPrice,
  signedValueClass,
} from '../lib/format'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import {
  beginRequest,
  invalidateRequests,
  isRequestCurrent,
} from '../../../../../packages/ui/src/requestIdentity'
import {
  accountWorkspaceResourceId,
  resolvedAccountWorkspaceResourceId,
  resolveSettlementCashAccountId,
  resolveWorkspaceAccountSelection,
  shouldLoadAccountWorkspace,
} from '../lib/accountWorkspace'

const ACCOUNT_SCOPE_OPTIONS = ['equity', 'etf', 'fund', 'bond', 'other'] as const

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
  const requestedAccountId = searchParams.get('account_id') ?? ''
  const [workspace, setWorkspace] = useState<PortfolioAccountsWorkspaceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerMode, setDrawerMode] = useState<'create' | 'edit'>('create')
  const [editingAccountId, setEditingAccountId] = useState<string | null>(null)
  const [pendingCostMethodChange, setPendingCostMethodChange] = useState<PendingCostMethodChange | null>(null)
  const [savingCostMethodChange, setSavingCostMethodChange] = useState(false)
  const [showAllDirectTransactions, setShowAllDirectTransactions] = useState(false)
  const [showAllLedgerEntries, setShowAllLedgerEntries] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [form, setForm] = useState<AccountFormState>(buildInitialAccountForm)
  const workspaceRequestSequenceRef = useRef(0)
  const requestedAccountIdRef = useRef(requestedAccountId)
  const currentWorkspaceResourceRef = useRef(accountWorkspaceResourceId(portfolioId, requestedAccountId))
  const loadedWorkspaceResourceRef = useRef<string | null>(null)
  const inFlightWorkspaceResourceRef = useRef<string | null>(null)
  const unmountInvalidationTimerRef = useRef<number | null>(null)
  const drawerDialogRef = useModalDialog(drawerOpen, () => setDrawerOpen(false))

  requestedAccountIdRef.current = requestedAccountId
  currentWorkspaceResourceRef.current = accountWorkspaceResourceId(portfolioId, requestedAccountId)

  function closeCostMethodDialog() {
    if (!savingCostMethodChange) {
      setPendingCostMethodChange(null)
    }
  }

  const costMethodDialogRef = useModalDialog(
    Boolean(pendingCostMethodChange),
    closeCostMethodDialog,
  )

  async function loadWorkspace(targetAccountId: string) {
    const targetPortfolioId = portfolioId
    if (!targetPortfolioId) {
      setWorkspace(null)
      setError('Portfolio id is required.')
      setLoading(false)
      return
    }

    const targetResourceId = accountWorkspaceResourceId(targetPortfolioId, targetAccountId)
    requestedAccountIdRef.current = targetAccountId
    currentWorkspaceResourceRef.current = targetResourceId
    inFlightWorkspaceResourceRef.current = targetResourceId
    const request = beginRequest(workspaceRequestSequenceRef, targetResourceId)
    setLoading(true)

    try {
      const response = await getPortfolioAccountsWorkspace(targetPortfolioId, targetAccountId || undefined)
      if (
        !isRequestCurrent(workspaceRequestSequenceRef, request, currentWorkspaceResourceRef.current) ||
        response.portfolio_id !== targetPortfolioId
      ) {
        if (inFlightWorkspaceResourceRef.current === targetResourceId) {
          inFlightWorkspaceResourceRef.current = null
        }
        return
      }

      const selectedAccountId = response.selected_account_id ?? ''
      const loadedResourceId = resolvedAccountWorkspaceResourceId(
        targetPortfolioId,
        targetAccountId,
        selectedAccountId,
      )
      loadedWorkspaceResourceRef.current = loadedResourceId
      inFlightWorkspaceResourceRef.current = null
      setWorkspace(response)
      setForm(buildInitialAccountForm(response.accounts.map((item) => item.account)))
      setError(null)
      setLoading(false)

      const selectionUpdate = resolveWorkspaceAccountSelection({
        currentAccountId: requestedAccountIdRef.current,
        workspaceRequestedAccountId: targetAccountId,
        workspaceSelectedAccountId: response.selected_account_id,
      })
      if (selectionUpdate !== undefined) {
        const nextAccountId = selectionUpdate ?? ''
        requestedAccountIdRef.current = nextAccountId
        currentWorkspaceResourceRef.current = accountWorkspaceResourceId(targetPortfolioId, nextAccountId)
        setSearchParams((current) => {
          const next = new URLSearchParams(current)
          if (nextAccountId) {
            next.set('account_id', nextAccountId)
          } else {
            next.delete('account_id')
          }
          return next
        }, { replace: true })
      }
    } catch (requestError) {
      if (!isRequestCurrent(workspaceRequestSequenceRef, request, currentWorkspaceResourceRef.current)) {
        if (inFlightWorkspaceResourceRef.current === targetResourceId) {
          inFlightWorkspaceResourceRef.current = null
        }
        return
      }
      inFlightWorkspaceResourceRef.current = null
      setError(requestError instanceof Error ? requestError.message : 'Failed to load accounts workspace.')
      setLoading(false)
    }
  }

  async function refreshWorkspace(nextAccountId?: string | null) {
    const targetAccountId = nextAccountId ?? requestedAccountIdRef.current
    if (targetAccountId !== requestedAccountIdRef.current) {
      requestedAccountIdRef.current = targetAccountId
      currentWorkspaceResourceRef.current = accountWorkspaceResourceId(portfolioId, targetAccountId)
      setSearchParams((current) => {
        const next = new URLSearchParams(current)
        if (targetAccountId) {
          next.set('account_id', targetAccountId)
        } else {
          next.delete('account_id')
        }
        return next
      }, { replace: true })
    }
    await loadWorkspace(targetAccountId)
  }

  useEffect(() => {
    const resourceId = accountWorkspaceResourceId(portfolioId, requestedAccountId)
    if (!portfolioId) {
      invalidateRequests(workspaceRequestSequenceRef)
      loadedWorkspaceResourceRef.current = null
      inFlightWorkspaceResourceRef.current = null
      setWorkspace(null)
      setError('Portfolio id is required.')
      setLoading(false)
      return
    }
    if (!shouldLoadAccountWorkspace(
      resourceId,
      loadedWorkspaceResourceRef.current,
      inFlightWorkspaceResourceRef.current,
    )) {
      if (loadedWorkspaceResourceRef.current === resourceId) {
        setLoading(false)
      }
      return
    }
    void loadWorkspace(requestedAccountId)
  }, [portfolioId, requestedAccountId, setSearchParams])

  useEffect(() => {
    if (unmountInvalidationTimerRef.current != null) {
      window.clearTimeout(unmountInvalidationTimerRef.current)
      unmountInvalidationTimerRef.current = null
    }
    return () => {
      unmountInvalidationTimerRef.current = window.setTimeout(() => {
        invalidateRequests(workspaceRequestSequenceRef)
        inFlightWorkspaceResourceRef.current = null
      }, 0)
    }
  }, [])

  useEffect(() => {
    if (!notice) {
      return undefined
    }
    const timeoutId = window.setTimeout(() => setNotice(null), 2800)
    return () => window.clearTimeout(timeoutId)
  }, [notice])

  const accountRecords = useMemo(
    () => workspace?.accounts.map((item) => item.account) ?? [],
    [workspace?.accounts],
  )
  const depositAccounts = useMemo(
    () => accountRecords.filter((account) => account.account_type === 'deposit_account'),
    [accountRecords],
  )
  const compatibleDepositAccounts = useMemo(
    () => depositAccounts.filter(
      (account) => account.currency.toUpperCase() === form.currency.trim().toUpperCase(),
    ),
    [depositAccounts, form.currency],
  )
  const compatibleDepositAccountIds = useMemo(
    () => compatibleDepositAccounts.map((account) => account.account_id),
    [compatibleDepositAccounts],
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

    const nextSettlementAccountId = resolveSettlementCashAccountId(
      form.default_settlement_cash_account_id,
      compatibleDepositAccountIds,
    )
    if (nextSettlementAccountId === form.default_settlement_cash_account_id) {
      return
    }

    setForm((current) => {
      const nextAccountId = resolveSettlementCashAccountId(
        current.default_settlement_cash_account_id,
        compatibleDepositAccountIds,
      )
      if (nextAccountId === current.default_settlement_cash_account_id) {
        return current
      }
      return {
        ...current,
        default_settlement_cash_account_id: nextAccountId,
      }
    })
  }, [
    compatibleDepositAccountIds,
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
  const directTransactions = workspace?.linked_transactions ?? []
  const displayedDirectTransactions = showAllDirectTransactions
    ? directTransactions
    : directTransactions.slice(0, 8)
  const displayedLedgerPostings = showAllLedgerEntries
    ? visibleLedgerPostings
    : visibleLedgerPostings.slice(0, 12)

  useEffect(() => {
    setShowAllDirectTransactions(false)
    setShowAllLedgerEntries(false)
  }, [resolvedSelectedAccountId])

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

  async function handleConfirmCostMethodChange() {
    const pendingChange = pendingCostMethodChange
    if (!pendingChange || savingCostMethodChange) {
      return
    }
    setSavingCostMethodChange(true)
    setFormError(null)
    try {
      await applyAccountUpdate(pendingChange.accountId, pendingChange.payload)
    } catch (requestError) {
      setFormError(requestError instanceof Error ? requestError.message : 'Failed to save account.')
    } finally {
      setSavingCostMethodChange(false)
    }
  }

  async function handleSaveAccount() {
    setFormError(null)
    setNotice(null)

    if (!form.account_name.trim()) {
      setFormError('Enter an account name.')
      return
    }

    if (!SUPPORTED_PORTFOLIO_CURRENCIES.includes(form.currency as (typeof SUPPORTED_PORTFOLIO_CURRENCIES)[number])) {
      setFormError('Select an account currency.')
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

  function selectAccount(accountId: string) {
    if (accountId === requestedAccountIdRef.current) {
      return
    }
    requestedAccountIdRef.current = accountId
    currentWorkspaceResourceRef.current = accountWorkspaceResourceId(portfolioId, accountId)
    inFlightWorkspaceResourceRef.current = null
    invalidateRequests(workspaceRequestSequenceRef)
    const next = new URLSearchParams(searchParams)
    next.set('account_id', accountId)
    setSearchParams(next, { replace: true })
  }

  function openCreateAccountDrawer() {
    setDrawerMode('create')
    setEditingAccountId(null)
    setDrawerOpen(true)
    setFormError(null)
    setForm(buildInitialAccountForm(accountRecords))
  }

  function openEditAccountDrawer() {
    if (!selectedAccount) {
      return
    }
    setDrawerMode('edit')
    setEditingAccountId(selectedAccount.account.account_id)
    setDrawerOpen(true)
    setFormError(null)
    setForm(buildAccountFormFromRecord(selectedAccount.account))
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
              <span className="summary-card-label">Cash Accounts</span>
              <strong className="summary-card-value">{workspace.summary.deposit_account_count}</strong>
            </article>
            <article className="summary-card">
              <span className="summary-card-label">Securities Accounts</span>
              <strong className="summary-card-value">{workspace.summary.securities_account_count}</strong>
            </article>
            <article className="summary-card">
              <span className="summary-card-label">Ledger Entries</span>
              <strong className="summary-card-value">{workspace.summary.ledger_posting_count}</strong>
            </article>
          </div>
        ) : undefined
      }
    >
      <section className="portfolio-detail-surface">
        <div className="portfolio-detail-toolbar account-page-heading">
          <div className="account-page-title-stack">
            <div className="panel-title">Accounts</div>
            <div className="portfolio-detail-meta">
              Configure custody accounts and inspect their transaction-derived balances.
            </div>
          </div>
          <button type="button" className="toolbar-link button-primary" onClick={openCreateAccountDrawer}>
            Add Account
          </button>
        </div>

        {notice ? <div className="inline-notice inline-notice-success">{notice}</div> : null}
        {loading ? <CalculationStatus /> : null}
        {error ? <div className="error-state">{error}</div> : null}

        {!loading && !error && workspace ? (
          <>
            <div className="account-derivation-note">
              <span className="account-source-badge">Transaction-derived</span>
              <span>
                Balances, positions, and ledger entries are read-only outputs. Edit the source transaction to change them.
              </span>
            </div>

            <section className="accounts-workbench">
              <article className="panel account-directory-panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-title">Account Directory</div>
                    <div className="portfolio-detail-meta">{visibleAccounts.length} configured</div>
                  </div>
                </div>
                <nav className="account-directory-list" aria-label="Accounts">
                  {visibleAccounts.map((accountRow) => {
                    const isActive = accountRow.account.account_id === resolvedSelectedAccountId
                    return (
                      <button
                        key={accountRow.account.account_id}
                        type="button"
                        aria-current={isActive ? 'true' : undefined}
                        className={`account-directory-item${isActive ? ' account-directory-item-active' : ''}`}
                        onClick={() => selectAccount(accountRow.account.account_id)}
                      >
                        <span className="account-directory-item-head">
                          <span className="account-directory-name">{accountRow.account.account_name}</span>
                          <span className={`account-status-pill account-status-${accountRow.account.status}`}>
                            {formatLabel(accountRow.account.status)}
                          </span>
                        </span>
                        <span className="account-directory-meta">
                          {formatLabel(accountRow.account.account_type)} · {accountRow.account.currency}
                          {accountRow.account.institution ? ` · ${accountRow.account.institution}` : ''}
                        </span>
                        <span className="account-directory-stats">
                          <span>
                            <small>Account Value</small>
                            <strong>
                              {accountRow.account_value_base != null
                                ? formatCurrency(accountRow.account_value_base, workspace.base_currency)
                                : '—'}
                            </strong>
                          </span>
                          <span>
                            <small>Positions</small>
                            <strong>{accountRow.position_line_count}</strong>
                          </span>
                        </span>
                      </button>
                    )
                  })}
                </nav>
              </article>

              <article className="panel account-detail-panel">
                {selectedAccount ? (
                  <>
                    <div className="account-detail-header">
                      <div className="account-detail-identity">
                        <div className="account-detail-eyebrow">Selected Account</div>
                        <h2>{selectedAccount.account.account_name}</h2>
                        <div className="account-detail-tags">
                          <span>{formatLabel(selectedAccount.account.account_type)}</span>
                          <span>{selectedAccount.account.currency}</span>
                          <span className={`account-status-pill account-status-${selectedAccount.account.status}`}>
                            {formatLabel(selectedAccount.account.status)}
                          </span>
                        </div>
                      </div>
                      <div className="account-detail-actions">
                        <Link
                          className="toolbar-link"
                          to={`/portfolios/${portfolioId}/transactions?account_id=${encodeURIComponent(
                            selectedAccount.account.account_id,
                          )}`}
                        >
                          View Transactions
                        </Link>
                        <button type="button" className="toolbar-link" onClick={openEditAccountDrawer}>
                          Edit Account
                        </button>
                      </div>
                    </div>

                    <div className="account-balance-grid">
                      <article>
                        <span>Account Value · {workspace.base_currency}</span>
                        <strong>
                          {selectedAccount.account_value_base != null
                            ? formatCurrency(selectedAccount.account_value_base, workspace.base_currency)
                            : '—'}
                        </strong>
                      </article>
                      <article>
                        <span>Cash Balance · {selectedAccount.account.currency}</span>
                        <strong>
                          {formatCurrency(selectedAccount.derived_cash_balance, selectedAccount.account.currency)}
                        </strong>
                      </article>
                      <article>
                        <span>
                          Position Value · {selectedAccount.position_market_value_currency ?? workspace.base_currency}
                        </span>
                        <strong>
                          {selectedAccount.position_market_value != null
                            ? formatCurrency(
                                selectedAccount.position_market_value,
                                selectedAccount.position_market_value_currency ?? workspace.base_currency,
                              )
                            : '—'}
                        </strong>
                      </article>
                      <article>
                        <span>Pending Settlement · {selectedAccount.account.currency}</span>
                        <strong>
                          {formatCurrency(selectedAccount.pending_settlement, selectedAccount.account.currency)}
                        </strong>
                      </article>
                    </div>

                    <div className="account-detail-section-heading">
                      <div className="panel-title">Account Setup</div>
                    </div>
                    <dl className="account-profile-grid">
                      <div>
                        <dt>Institution</dt>
                        <dd>{selectedAccount.account.institution || '—'}</dd>
                      </div>
                      <div>
                        <dt>Default Settlement</dt>
                        <dd>{selectedAccount.default_settlement_cash_account_name || '—'}</dd>
                      </div>
                      <div>
                        <dt>Cost Method</dt>
                        <dd>
                          {selectedAccount.account.cost_basis_method
                            ? formatLabel(selectedAccount.account.cost_basis_method)
                            : '—'}
                        </dd>
                      </div>
                      <div>
                        <dt>Instrument Scope</dt>
                        <dd>{formatAccountInstrumentScope(selectedAccount.account)}</dd>
                      </div>
                      <div>
                        <dt>Opened</dt>
                        <dd>{selectedAccount.account.opened_at || '—'}</dd>
                      </div>
                      <div>
                        <dt>Closed</dt>
                        <dd>{selectedAccount.account.closed_at || '—'}</dd>
                      </div>
                    </dl>

                    <div className="account-activity-counts">
                      <span><strong>{selectedAccount.position_line_count}</strong> position lines</span>
                      <span><strong>{selectedAccount.linked_transaction_count}</strong> affecting transactions</span>
                      <span><strong>{selectedAccount.linked_posting_count}</strong> ledger entries</span>
                    </div>
                  </>
                ) : (
                  <div className="empty-state">No account.</div>
                )}
              </article>
            </section>

            <section className="panel account-data-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Open Positions</div>
                  <div className="portfolio-detail-meta">{selectedAccount?.account.account_name || 'No account selected'}</div>
                </div>
                <div className="portfolio-detail-meta">{visiblePositions.length} lines</div>
              </div>
              {visiblePositions.length ? (
                <div className="table-shell">
                  <table className="accounts-data-table accounts-responsive-table">
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
                <div className="account-empty-state">No open positions in this account.</div>
              )}
            </section>

            <section className="panel account-data-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Direct Transactions</div>
                  <div className="portfolio-detail-meta">
                    Transactions booked directly to this account; settlement-only effects remain in the ledger.
                  </div>
                </div>
                <div className="account-panel-header-actions">
                  <span className="portfolio-detail-meta">
                    {workspace.linked_transactions_summary?.total_transactions ?? 0} direct
                  </span>
                  {directTransactions.length > 8 ? (
                    <button
                      type="button"
                      className="account-inline-action"
                      onClick={() => setShowAllDirectTransactions((current) => !current)}
                    >
                      {showAllDirectTransactions ? 'Show Recent' : 'Show All'}
                    </button>
                  ) : null}
                </div>
              </div>
              {directTransactions.length ? (
                <div className="table-shell">
                  <table className="accounts-data-table accounts-responsive-table">
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
                      {displayedDirectTransactions.map((transaction) => (
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
                <div className="account-empty-state">No transactions are booked directly to this account.</div>
              )}
            </section>

            <section className="panel account-data-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Ledger Entries</div>
                  <div className="portfolio-detail-meta">Immutable postings generated by source transactions</div>
                </div>
                <div className="account-panel-header-actions">
                  <span className="portfolio-detail-meta">
                    {visibleLedgerPostings.length} entries · {selectedAccount?.account.account_name || 'No account selected'}
                  </span>
                  {visibleLedgerPostings.length > 12 ? (
                    <button
                      type="button"
                      className="account-inline-action"
                      onClick={() => setShowAllLedgerEntries((current) => !current)}
                    >
                      {showAllLedgerEntries ? 'Show Recent' : 'Show All'}
                    </button>
                  ) : null}
                </div>
              </div>
              {visibleLedgerPostings.length ? (
                <div className="table-shell">
                  <table className="accounts-data-table accounts-responsive-table account-ledger-table">
                    <thead>
                      <tr>
                        <th>Dates</th>
                        <th>Entry</th>
                        <th>Instrument</th>
                        <th>Cash Delta</th>
                        <th>Quantity Delta</th>
                        <th>Cost Basis Delta</th>
                        <th>Reference</th>
                      </tr>
                    </thead>
                    <tbody>
                      {displayedLedgerPostings.map((posting) => (
                        <LedgerPostingRow key={posting.posting_id} posting={posting} />
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="account-empty-state">No ledger entries for this account.</div>
              )}
            </section>
          </>
        ) : null}
      </section>

      {drawerOpen ? (
        <div className="transaction-drawer-backdrop" role="presentation" onClick={() => setDrawerOpen(false)}>
          <aside
            ref={drawerDialogRef}
            className="transaction-drawer"
            role="dialog"
            aria-modal="true"
            aria-label={drawerMode === 'edit' ? 'Edit account' : 'Add account'}
            tabIndex={-1}
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
                    <option value="" disabled>Select currency</option>
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
          onClick={closeCostMethodDialog}
        >
          <div
            ref={costMethodDialogRef}
            className="transaction-entry-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Confirm cost method change"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="transaction-entry-modal-header">
              <div>
                <div className="panel-title">Confirm Cost Method Change</div>
                <div className="portfolio-detail-meta">{pendingCostMethodChange.accountName}</div>
              </div>
              <button
                type="button"
                className="toolbar-link"
                disabled={savingCostMethodChange}
                onClick={closeCostMethodDialog}
              >
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
              {formError ? <div className="error-state transaction-form-error">{formError}</div> : null}
              <div className="transaction-form-footer">
                <button
                  type="button"
                  className="toolbar-link"
                  disabled={savingCostMethodChange}
                  onClick={closeCostMethodDialog}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="toolbar-link button-primary"
                  disabled={savingCostMethodChange}
                  onClick={() => void handleConfirmCostMethodChange()}
                >
                  {savingCostMethodChange ? 'Recalculating…' : 'Recalculate and Update'}
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
    currency: defaultCashAccount?.currency ?? '',
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
      <td data-label="Trade Date">{transaction.trade_date}</td>
      <td data-label="Type">
        <span className="transaction-type-pill">{formatLabel(transaction.transaction_type)}</span>
      </td>
      <td className="holding-name-cell" data-label="Instrument">
        {transaction.instrument_ref ? (
          <div className="holding-name-stack">
            <span>{primaryIdentifier(transaction.instrument_ref)}</span>
            <span className="holding-secondary">{transaction.instrument_ref.instrument_name}</span>
          </div>
        ) : (
          <span className="holding-secondary">Cash ledger</span>
        )}
      </td>
      <td data-label="Gross">{formatCurrency(transaction.gross_amount, transaction.currency)}</td>
      <td
        data-label="Net Cash"
        className={signedValueClass(transaction.net_cash_effect)}
      >
        {formatSignedCurrency(transaction.net_cash_effect, transaction.currency)}
      </td>
      <td data-label="Open">
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
      <td className="holding-name-cell" data-label="Instrument">
        <div className="holding-name-stack">
          <span>{primaryIdentifier(position)}</span>
          <span className="holding-secondary">{position.instrument_ref.instrument_name}</span>
        </div>
      </td>
      <td data-label="Quantity">{formatNumber(position.quantity, 2)}</td>
      <td data-label="Cost Basis">
        {position.cost_basis != null ? formatCurrency(position.cost_basis, position.currency) : '—'}
      </td>
      <td data-label="Last Price">
        {position.last_price != null ? formatUnitPrice(position.last_price, position.currency) : '—'}
      </td>
      <td data-label="Market Value">
        {position.market_value != null ? formatCurrency(position.market_value, position.currency) : '—'}
      </td>
    </tr>
  )
}

function LedgerPostingRow({ posting }: { posting: PortfolioLedgerPostingRecord }) {
  return (
    <tr>
      <td data-label="Dates">
        <div className="account-ledger-date-stack">
          <span>{posting.trade_date}</span>
          <span>Settles {posting.settlement_date}</span>
        </div>
      </td>
      <td data-label="Entry">
        <div className="account-ledger-entry-stack">
          <span className="transaction-type-pill">{formatLabel(posting.posting_role)}</span>
          <span>{formatLabel(posting.source_transaction_type)}</span>
        </div>
      </td>
      <td className="holding-name-cell" data-label="Instrument">
        {posting.instrument_ref ? (
          <div className="holding-name-stack">
            <span>{primaryIdentifier(posting)}</span>
            <span className="holding-secondary">{posting.instrument_ref.instrument_name}</span>
          </div>
        ) : (
          <span className="holding-secondary">Cash ledger</span>
        )}
      </td>
      <td
        data-label="Cash Delta"
        className={posting.cash_amount_delta != null && posting.cash_amount_delta < 0 ? 'negative-cell' : ''}
      >
        {formatSignedCurrency(posting.cash_amount_delta, posting.currency)}
      </td>
      <td data-label="Quantity Delta">{formatNumber(posting.quantity_delta, 2)}</td>
      <td
        data-label="Cost Basis Delta"
        className={posting.cost_basis_delta != null && posting.cost_basis_delta < 0 ? 'negative-cell' : ''}
      >
        {posting.cost_basis_delta != null ? formatSignedCurrency(posting.cost_basis_delta, posting.currency) : '—'}
      </td>
      <td className="account-ledger-reference" data-label="Reference">
        <span>{posting.transaction_id}</span>
        <span>{posting.note || 'No note'}</span>
      </td>
    </tr>
  )
}
