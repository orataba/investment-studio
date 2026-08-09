import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, useParams, useSearchParams } from 'react-router'

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
import { transactionActivityLabel } from '../lib/transactionPresentation'
import { holdingUsesEventValuation } from '../lib/holdingPresentation'

const ACCOUNT_SCOPE_OPTIONS = ['equity', 'etf', 'fund', 'bond', 'fcn', 'option', 'other'] as const
type AccountDetailTab = 'overview' | 'positions' | 'transactions' | 'ledger'

function parseAccountDetailTab(value: string | null): AccountDetailTab {
  if (value === 'positions' || value === 'transactions' || value === 'ledger') {
    return value
  }
  return 'overview'
}

function countLabel(count: number, singular: string, plural = `${singular}s`) {
  return `${formatNumber(count, 0)} ${count === 1 ? singular : plural}`
}

function formatSignedNumber(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }
  const absolute = formatNumber(Math.abs(value), digits)
  return value > 0 ? `+${absolute}` : value < 0 ? `-${absolute}` : absolute
}

function localTodayIso() {
  const now = new Date()
  const timezoneOffsetMs = now.getTimezoneOffset() * 60 * 1000
  return new Date(now.getTime() - timezoneOffsetMs).toISOString().slice(0, 10)
}

function primaryIdentifier(position: {
  instrument_id?: string | null
  instrument_ref?: { identifiers: Array<{ identifier_value: string; is_primary: boolean }> } | null
  derivative_contract_id?: string | null
  derivative_contract?: { external_reference?: string | null } | null
}) {
  return (
    position.instrument_ref?.identifiers.find((item) => item.is_primary)?.identifier_value ??
    position.instrument_ref?.identifiers[0]?.identifier_value ??
    position.instrument_id ??
    position.derivative_contract?.external_reference ??
    position.derivative_contract_id ??
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
  const accountDetailTab = parseAccountDetailTab(searchParams.get('account_tab'))
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
  const accountDetailTabs = [
    { key: 'overview', label: 'Overview', meta: 'Setup' },
    { key: 'positions', label: 'Positions', meta: String(visiblePositions.length) },
    {
      key: 'transactions',
      label: 'Transactions',
      meta: String(workspace?.linked_transactions_summary?.total_transactions ?? directTransactions.length),
    },
    { key: 'ledger', label: 'Ledger', meta: String(visibleLedgerPostings.length) },
  ] as const

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

  function selectAccountDetailTab(tab: AccountDetailTab) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (tab === 'overview') {
        next.delete('account_tab')
      } else {
        next.set('account_tab', tab)
      }
      return next
    }, { replace: true })
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
      busy={loading}
    >
      <section className="portfolio-detail-surface account-page">
        <header className="account-page-heading">
          <div className="account-page-title-stack">
            <span className="account-page-eyebrow">Custody</span>
            <h1>Accounts</h1>
            <p>Custody accounts and their transaction-derived balances.</p>
          </div>
          <div className="account-page-heading-actions">
            {workspace ? (
              <span className="account-page-inventory">
                {countLabel(workspace.summary.account_count, 'account')} ·{' '}
                {countLabel(workspace.summary.deposit_account_count, 'cash account')} ·{' '}
                {countLabel(workspace.summary.securities_account_count, 'securities account')} ·{' '}
                {countLabel(workspace.summary.open_option_obligation_count, 'open option obligation')}
              </span>
            ) : null}
            <button type="button" className="toolbar-link button-primary" onClick={openCreateAccountDrawer}>
              Add Account
            </button>
          </div>
        </header>

        {notice ? <div className="inline-notice inline-notice-success">{notice}</div> : null}
        {loading ? <CalculationStatus /> : null}
        {error ? <div className="error-state">{error}</div> : null}

        {!loading && !error && workspace ? (
          <section className="accounts-workbench">
            <aside className="account-directory-panel">
              <div className="account-directory-header">
                <div>
                  <span className="account-section-kicker">Directory</span>
                  <h2>Accounts</h2>
                </div>
                <span>{formatNumber(visibleAccounts.length, 0)} configured</span>
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
                          <small>Account value</small>
                          <strong>
                            {accountRow.account_value_base != null
                              ? formatCurrency(accountRow.account_value_base, workspace.base_currency)
                              : '—'}
                          </strong>
                        </span>
                        <span>
                          <small>Positions</small>
                          <strong>{formatNumber(accountRow.position_line_count, 0)}</strong>
                        </span>
                        <span>
                          <small>Option obligations</small>
                          <strong>{formatNumber(accountRow.open_option_obligation_count, 0)}</strong>
                        </span>
                      </span>
                    </button>
                  )
                })}
              </nav>
            </aside>

            <section className="account-detail-panel">
              {selectedAccount ? (
                <>
                  <header className="account-detail-header">
                    <div className="account-detail-identity">
                      <span className="account-detail-eyebrow">Selected account</span>
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
                        Transaction Workbench
                      </Link>
                      <button type="button" className="toolbar-link" onClick={openEditAccountDrawer}>
                        Edit Account
                      </button>
                    </div>
                  </header>

                  <div className="account-balance-grid">
                    <div>
                      <span>Account value · {workspace.base_currency}</span>
                      <strong>
                        {selectedAccount.account_value_base != null
                          ? formatCurrency(selectedAccount.account_value_base, workspace.base_currency)
                          : '—'}
                      </strong>
                    </div>
                    <div>
                      <span>Cash balance · {selectedAccount.account.currency}</span>
                      <strong>
                        {formatCurrency(selectedAccount.derived_cash_balance, selectedAccount.account.currency)}
                      </strong>
                    </div>
                    <div>
                      <span>
                        Position value · {selectedAccount.position_market_value_currency ?? workspace.base_currency}
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
                    <div>
                      <span>Pending settlement · {selectedAccount.account.currency}</span>
                      <strong>
                        {formatCurrency(selectedAccount.pending_settlement, selectedAccount.account.currency)}
                      </strong>
                    </div>
                    <div>
                      <span>Derivative liability · {workspace.base_currency}</span>
                      <strong>
                        {selectedAccount.derivative_liability_base != null
                          ? formatCurrency(
                              selectedAccount.derivative_liability_base,
                              workspace.base_currency,
                            )
                          : '—'}
                      </strong>
                    </div>
                  </div>

                  <div className="account-detail-tabbar" role="tablist" aria-label="Account detail">
                    {accountDetailTabs.map((tab) => {
                      const isActive = accountDetailTab === tab.key
                      return (
                        <button
                          key={tab.key}
                          type="button"
                          id={`account-detail-tab-${tab.key}`}
                          role="tab"
                          aria-label={`${tab.label} ${tab.meta}`}
                          aria-selected={isActive}
                          aria-controls={`account-detail-panel-${tab.key}`}
                          tabIndex={isActive ? 0 : -1}
                          className={`account-detail-tab${isActive ? ' account-detail-tab-active' : ''}`}
                          onClick={() => selectAccountDetailTab(tab.key)}
                        >
                          <span>{tab.label}</span>
                          <small>{tab.meta}</small>
                        </button>
                      )
                    })}
                  </div>

                  {accountDetailTab === 'overview' ? (
                    <div
                      id="account-detail-panel-overview"
                      className="account-tab-panel account-overview-grid"
                      role="tabpanel"
                      aria-labelledby="account-detail-tab-overview"
                    >
                      <section className="account-overview-section">
                        <div className="account-section-heading">
                          <div>
                            <span className="account-section-kicker">Configuration</span>
                            <h3>Account setup</h3>
                          </div>
                        </div>
                        <dl className="account-profile-grid">
                          <div>
                            <dt>Institution</dt>
                            <dd>{selectedAccount.account.institution || '—'}</dd>
                          </div>
                          <div>
                            <dt>Default settlement</dt>
                            <dd>{selectedAccount.default_settlement_cash_account_name || '—'}</dd>
                          </div>
                          <div>
                            <dt>Cost method</dt>
                            <dd>
                              {selectedAccount.account.cost_basis_method
                                ? formatCostMethodLabel(selectedAccount.account.cost_basis_method)
                                : '—'}
                            </dd>
                          </div>
                          <div>
                            <dt>Instrument scope</dt>
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
                      </section>

                      <aside className="account-derived-summary">
                        <div className="account-section-heading">
                          <div>
                            <span className="account-section-kicker">Accounting basis</span>
                            <h3>Transaction-derived</h3>
                          </div>
                        </div>
                        <p>
                          Balances, positions, and postings are read-only outputs. Correct the source
                          transaction when an accounting fact is wrong.
                        </p>
                        <div className="account-activity-counts">
                          <div>
                            <strong>{formatNumber(selectedAccount.position_line_count, 0)}</strong>
                            <span>Open positions</span>
                          </div>
                          <div>
                            <strong>{formatNumber(selectedAccount.linked_transaction_count, 0)}</strong>
                            <span>Affecting transactions</span>
                          </div>
                          <div>
                            <strong>{formatNumber(selectedAccount.linked_posting_count, 0)}</strong>
                            <span>Ledger entries</span>
                          </div>
                          <div>
                            <strong>{formatNumber(selectedAccount.open_option_obligation_count, 0)}</strong>
                            <span>Open option obligations</span>
                          </div>
                        </div>
                      </aside>
                    </div>
                  ) : null}

                  {accountDetailTab === 'positions' ? (
                    <section
                      id="account-detail-panel-positions"
                      className="account-tab-panel account-data-panel"
                      role="tabpanel"
                      aria-labelledby="account-detail-tab-positions"
                    >
                      <div className="account-panel-header">
                        <div>
                          <span className="account-section-kicker">Current custody</span>
                          <h3>Open positions</h3>
                        </div>
                        <span>{countLabel(visiblePositions.length, 'line')}</span>
                      </div>
                      {visiblePositions.length ? (
                        <div className="table-shell">
                          <table className="accounts-data-table accounts-responsive-table account-position-table">
                            <thead>
                              <tr>
                                <th>Instrument</th>
                                <th>Quantity / Price</th>
                                <th>Cost Basis</th>
                                <th>Market Value</th>
                                <th>Unrealized P/L</th>
                              </tr>
                            </thead>
                            <tbody>
                              {visiblePositions.map((position) => (
                                <PositionRow
                                  key={`${position.account_id}-${position.instrument_id}`}
                                  portfolioId={portfolioId}
                                  position={position}
                                />
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <div className="account-empty-state">No open positions in this account.</div>
                      )}
                    </section>
                  ) : null}

                  {accountDetailTab === 'transactions' ? (
                    <section
                      id="account-detail-panel-transactions"
                      className="account-tab-panel account-data-panel"
                      role="tabpanel"
                      aria-labelledby="account-detail-tab-transactions"
                    >
                      <div className="account-panel-header">
                        <div>
                          <span className="account-section-kicker">Source facts</span>
                          <h3>Direct transactions</h3>
                          <p>Transactions booked to this account; settlement-only effects stay in Ledger.</p>
                        </div>
                        <div className="account-panel-header-actions">
                          <span>{countLabel(directTransactions.length, 'transaction')}</span>
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
                          <table className="accounts-data-table accounts-responsive-table account-transaction-table">
                            <thead>
                              <tr>
                                <th>Trade / Recognition</th>
                                <th>Activity</th>
                                <th>Instrument</th>
                                <th>Gross / Net Cash</th>
                                <th>Source</th>
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
                  ) : null}

                  {accountDetailTab === 'ledger' ? (
                    <section
                      id="account-detail-panel-ledger"
                      className="account-tab-panel account-data-panel"
                      role="tabpanel"
                      aria-labelledby="account-detail-tab-ledger"
                    >
                      <div className="account-panel-header">
                        <div>
                          <span className="account-section-kicker">Derived postings</span>
                          <h3>Ledger entries</h3>
                          <p>Immutable postings generated from source transactions.</p>
                        </div>
                        <div className="account-panel-header-actions">
                          <span>{countLabel(visibleLedgerPostings.length, 'entry', 'entries')}</span>
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
                                <th>Effective / Dates</th>
                                <th>Posting</th>
                                <th>Instrument</th>
                                <th>Deltas</th>
                                <th>Source</th>
                              </tr>
                            </thead>
                            <tbody>
                              {displayedLedgerPostings.map((posting) => (
                                <LedgerPostingRow
                                  key={posting.posting_id}
                                  posting={posting}
                                  portfolioId={portfolioId}
                                  accountId={resolvedSelectedAccountId}
                                />
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <div className="account-empty-state">No ledger entries for this account.</div>
                      )}
                    </section>
                  ) : null}
                </>
              ) : (
                <div className="empty-state">No account.</div>
              )}
            </section>
          </section>
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
  const instrumentType = transaction.instrument_ref?.instrument_type
  return (
    <tr>
      <td data-label="Trade / Recognition">
        <div className="account-transaction-date-stack">
          <span>{transaction.trade_date}</span>
          <span>
            {transaction.position_effective_date
              ? `Position EOD ${transaction.position_effective_date}`
              : `Settles ${transaction.settlement_date}`}
          </span>
        </div>
      </td>
      <td data-label="Activity">
        <span className="transaction-type-pill">
          {transactionActivityLabel(
            transaction.transaction_type,
            instrumentType,
            transaction.option_action,
          )}
        </span>
      </td>
      <td className="holding-name-cell" data-label="Instrument">
        {transaction.instrument_ref || transaction.derivative_contract ? (
          <div className="holding-name-stack">
            <span>
              {primaryIdentifier({
                instrument_id: transaction.instrument_id,
                instrument_ref: transaction.instrument_ref,
                derivative_contract_id: transaction.derivative_contract_id,
                derivative_contract: transaction.derivative_contract,
              })}
            </span>
            <span className="holding-secondary">{transaction.derivative_contract?.contract_name ?? transaction.instrument_ref?.instrument_name}</span>
          </div>
        ) : (
          <span className="holding-secondary">Cash ledger</span>
        )}
      </td>
      <td data-label="Gross / Net Cash">
        <div className="account-transaction-cash-stack">
          <span>{formatCurrency(transaction.gross_amount, transaction.currency)}</span>
          <span className={signedValueClass(transaction.net_cash_effect)}>
            Net {formatSignedCurrency(transaction.net_cash_effect, transaction.currency)}
          </span>
        </div>
      </td>
      <td className="account-source-link" data-label="Source">
        <Link
          className="table-inline-link"
          to={accountTransactionHref(portfolioId, accountId, transaction.transaction_id)}
        >
          View
        </Link>
        <span>{transaction.transaction_id}</span>
      </td>
    </tr>
  )
}

function PositionRow({
  portfolioId,
  position,
}: {
  portfolioId: string
  position: PortfolioAccountPositionRecord
}) {
  const eventValued = holdingUsesEventValuation(position)
  const unrealizedPnl =
    !eventValued && position.market_value != null && position.cost_basis != null
      ? position.market_value - position.cost_basis
      : null

  return (
    <tr>
      <td className="holding-name-cell" data-label="Instrument">
        <div className="holding-name-stack">
          <Link
            className="table-inline-link"
            to={`/portfolios/${portfolioId}/holdings/${encodeURIComponent(position.position_reference_id)}?detail_tab=lots`}
          >
            {primaryIdentifier(position)}
          </Link>
          <span className="holding-secondary">{position.derivative_contract?.contract_name ?? position.instrument_ref?.instrument_name}</span>
        </div>
      </td>
      <td data-label="Quantity / Price">
        <div className="account-position-quantity-stack">
          <span>{formatNumber(position.quantity, 2)}</span>
          <span>
            {position.last_price != null ? formatUnitPrice(position.last_price, position.currency) : 'No price'}
          </span>
        </div>
      </td>
      <td data-label="Cost Basis">
        {position.cost_basis != null ? formatCurrency(position.cost_basis, position.currency) : '—'}
      </td>
      <td data-label="Market Value">
        {position.market_value != null ? formatCurrency(position.market_value, position.currency) : '—'}
      </td>
      <td data-label="Unrealized P/L" className={eventValued ? undefined : signedValueClass(unrealizedPnl)}>
        {eventValued ? 'N/A' : formatSignedCurrency(unrealizedPnl, position.currency)}
      </td>
    </tr>
  )
}

function LedgerPostingRow({
  posting,
  portfolioId,
  accountId,
}: {
  posting: PortfolioLedgerPostingRecord
  portfolioId: string
  accountId: string
}) {
  const hasDeltas = [
    posting.cash_amount_delta,
    posting.pending_amount_delta,
    posting.quantity_delta,
    posting.cost_basis_delta,
  ].some((value) => value != null)

  return (
    <tr>
      <td data-label="Effective / Dates">
        <div className="account-ledger-date-stack">
          <span>{posting.effective_date}</span>
          <span>Trade {posting.trade_date} · Settle {posting.settlement_date}</span>
        </div>
      </td>
      <td data-label="Posting">
        <div className="account-ledger-entry-stack">
          <span className="transaction-type-pill">{formatLabel(posting.posting_role)}</span>
          <span>
            {transactionActivityLabel(
              posting.source_transaction_type,
              posting.instrument_ref?.instrument_type,
              posting.option_action,
            )}
          </span>
        </div>
      </td>
      <td className="holding-name-cell" data-label="Instrument">
        {posting.instrument_ref || posting.derivative_contract ? (
          <div className="holding-name-stack">
            <span>
              {primaryIdentifier({
                instrument_id: posting.instrument_id,
                instrument_ref: posting.instrument_ref,
                derivative_contract_id: posting.derivative_contract_id,
                derivative_contract: posting.derivative_contract,
              })}
            </span>
            <span className="holding-secondary">{posting.derivative_contract?.contract_name ?? posting.instrument_ref?.instrument_name}</span>
          </div>
        ) : (
          <span className="holding-secondary">Cash ledger</span>
        )}
      </td>
      <td data-label="Deltas">
        {hasDeltas ? (
          <div className="account-ledger-delta-stack">
            {posting.cash_amount_delta != null ? (
              <span>
                <small>Cash</small>
                <strong className={signedValueClass(posting.cash_amount_delta)}>
                  {formatSignedCurrency(posting.cash_amount_delta, posting.currency)}
                </strong>
              </span>
            ) : null}
            {posting.pending_amount_delta != null ? (
              <span>
                <small>Pending</small>
                <strong className={signedValueClass(posting.pending_amount_delta)}>
                  {formatSignedCurrency(posting.pending_amount_delta, posting.currency)}
                </strong>
              </span>
            ) : null}
            {posting.quantity_delta != null ? (
              <span>
                <small>Quantity</small>
                <strong className={signedValueClass(posting.quantity_delta)}>
                  {formatSignedNumber(posting.quantity_delta)}
                </strong>
              </span>
            ) : null}
            {posting.cost_basis_delta != null ? (
              <span>
                <small>Cost</small>
                <strong className={signedValueClass(posting.cost_basis_delta)}>
                  {formatSignedCurrency(posting.cost_basis_delta, posting.currency)}
                </strong>
              </span>
            ) : null}
          </div>
        ) : (
          '—'
        )}
      </td>
      <td className="account-ledger-reference" data-label="Source">
        <Link
          className="table-inline-link"
          to={accountTransactionHref(portfolioId, accountId, posting.transaction_id)}
        >
          {posting.transaction_id}
        </Link>
        <span title={posting.note ?? undefined}>{posting.note || 'No note'}</span>
      </td>
    </tr>
  )
}
