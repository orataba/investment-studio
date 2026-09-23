import { describe, expect, it } from 'vitest'

import type {
  PortfolioAccountsWorkspaceResponse,
  PortfolioTaxonomyCatalogResponse,
} from './lib/api'
import { buildCurrentPlanningGroups } from './pages/RiskPage'
import { fcnContractFixture, holdingFixture, holdingsWorkspaceFixture } from './test/portfolioFixtures'

const taxonomy = {
  taxonomy_id: 'planning',
  portfolio_id: '3',
  name: 'Planning',
  taxonomy_type: 'allocation',
  primary_assignment_scope: 'instrument',
  root_allocation_basis: 'weight',
  status: 'active',
} as const

const catalog: PortfolioTaxonomyCatalogResponse = {
  portfolio_id: '3',
  taxonomies: [taxonomy],
  taxonomy_nodes: [
    {
      taxonomy_node_id: 'risk-assets',
      taxonomy_id: taxonomy.taxonomy_id,
      parent_taxonomy_node_id: null,
      node_name: 'Risk Assets',
      sort_order: 1,
      is_terminal: true,
      allocation_basis: 'risk_budget',
      status: 'active',
    },
  ],
  taxonomy_assignments: [
    {
      assignment_id: 'holding-assignment',
      taxonomy_id: taxonomy.taxonomy_id,
      target_scope: 'instrument',
      target_entity_id: 'asset-1',
      taxonomy_node_id: 'risk-assets',
      status: 'active',
    },
  ],
  instrument_universe: [],
  target_sets: [],
  target_set_lines: [],
  target_set_integrity_issues: [],
  target_resolution: [],
  taxonomy_configuration_version: 0,
}

function account(
  accountId: string,
  accountType: string,
  cash: number,
  pendingSettlement: number,
  accountValue: number,
  positionMarketValue: number,
): PortfolioAccountsWorkspaceResponse['accounts'][number] {
  return {
    account: {
      account_id: accountId,
      portfolio_id: '3',
      account_name: accountId,
      account_type: accountType,
      account_category: accountType === 'deposit_account' ? 'cash' : 'security',
      currency: 'USD',
      status: 'active',
    },
    linked_transaction_count: 0,
    linked_posting_count: 0,
    derived_cash_balance: cash,
    derived_cash_balance_base: cash,
    pending_settlement: pendingSettlement,
    pending_settlement_base: pendingSettlement,
    derivative_liability: 0,
    derivative_liability_base: 0,
    open_option_obligation_count: 0,
    account_value_base: accountValue,
    valuation_coverage_state: 'complete',
    valuation_missing_components: [],
    position_line_count: positionMarketValue ? 1 : 0,
    position_market_value: positionMarketValue,
    position_market_value_currency: 'USD',
  }
}

describe('Current planning system buckets', () => {
  it('keeps derivatives and account liquidity outside the securities taxonomy', () => {
    const holdingsWorkspace = holdingsWorkspaceFixture({
      rows: [
        holdingFixture({
          allocation: 0.7,
          market_value: 700,
          market_value_base: 700,
        }),
        holdingFixture({
          line_id: 'holding:fcn-1',
          holding_category: 'derivatives',
          holding_kind: 'derivative_contract',
          position_reference_id: 'fcn-1',
          derivative_contract_id: 'fcn-1',
          derivative_contract: fcnContractFixture(),
          instrument_core: null,
          allocation: 0.1,
          market_value: 100,
          market_value_base: 100,
        }),
        holdingFixture({
          line_id: 'pending:pending_subscription:broker:asset-1:USD',
          holding_kind: 'pending_subscription',
          available_for_trading: false,
          economic_instrument_id: 'asset-1',
          instrument_core: {
            instrument_id: 'pending:pending_subscription:broker:asset-1:USD',
            instrument_name: 'Subscription receivable · Asset 1',
            instrument_type: 'other',
            currency: 'USD',
            exchange_code: null,
            identifiers: [],
            broker_identifiers: [],
          },
          allocation: 0.08,
          market_value: 80,
          market_value_base: 80,
          cost_basis: null,
          cost_basis_base: null,
        }),
      ],
    })
    const accountsWorkspace: PortfolioAccountsWorkspaceResponse = {
      portfolio_id: '3',
      base_currency: 'USD',
      summary: {
        account_count: 2,
        deposit_account_count: 1,
        securities_account_count: 1,
        ledger_posting_count: 0,
        position_line_count: 1,
        valuation_coverage_state: 'complete',
        valued_account_count: 2,
        unvalued_account_count: 0,
        open_option_obligation_count: 0,
        derivative_liability_base: 0,
      },
      derivation_boundary: {
        ledger_postings: 'fixture',
        positions: 'fixture',
        holdings: 'fixture',
        snapshot: 'fixture',
      },
      accounts: [
        account('deposit', 'deposit_account', 100, 0, 100, 0),
        account('broker', 'securities_account', 20, 80, 900, 800),
      ],
      ledger_postings: [],
      positions: [],
      option_obligations: [],
      linked_transactions: [],
    }

    const result = buildCurrentPlanningGroups({
      holdingsWorkspace,
      accountsWorkspace,
      catalog,
      taxonomy,
      referenceDate: holdingsWorkspace.as_of_date,
    })

    expect(result.errors).toEqual([])
    expect(result.value).toEqual([
      expect.objectContaining({
        groupKey: 'risk-assets',
        currentWeight: 0.7,
        currentValueBase: 700,
      }),
      expect.objectContaining({
        groupKey: 'cash_bucket:__cash__',
        currentWeight: 0.2,
        currentValueBase: 200,
      }),
      expect.objectContaining({
        groupKey: 'derivative_bucket:__derivatives__',
        currentWeight: 0.1,
        currentValueBase: 100,
      }),
    ])
    expect(result.value.reduce((total, row) => total + (row.currentValueBase ?? 0), 0)).toBe(1000)
  })
})
