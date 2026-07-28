import { describe, expect, it } from 'vitest'

import type {
  PortfolioAccountsWorkspaceResponse,
  PortfolioTaxonomyCatalogResponse,
} from './lib/api'
import { buildCurrentPlanningGroups } from './pages/RiskPage'
import { holdingFixture, holdingsWorkspaceFixture } from './test/portfolioFixtures'

const taxonomy = {
  taxonomy_id: 'planning',
  portfolio_id: '3',
  name: 'Planning',
  taxonomy_type: 'allocation',
  primary_assignment_scope: 'instrument',
  planning_enabled: true,
  budgeting_level: 'root',
  root_default_target_dimension: 'weight',
  status: 'active',
} as const

const catalog: PortfolioTaxonomyCatalogResponse = {
  portfolio_id: '3',
  default_planning_taxonomy_id: taxonomy.taxonomy_id,
  taxonomies: [taxonomy],
  taxonomy_nodes: [
    {
      taxonomy_node_id: 'risk-assets',
      taxonomy_id: taxonomy.taxonomy_id,
      parent_taxonomy_node_id: null,
      node_name: 'Risk Assets',
      sort_order: 1,
      is_terminal: true,
      default_target_dimension: 'risk_budget',
      status: 'active',
    },
    {
      taxonomy_node_id: 'cash',
      taxonomy_id: taxonomy.taxonomy_id,
      parent_taxonomy_node_id: null,
      node_name: 'Cash',
      node_code: 'CASH',
      sort_order: 2,
      is_terminal: true,
      default_target_dimension: 'weight',
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
    {
      assignment_id: 'deposit-cash-assignment',
      taxonomy_id: taxonomy.taxonomy_id,
      target_scope: 'cash_bucket',
      target_entity_id: 'deposit',
      taxonomy_node_id: 'cash',
      status: 'active',
    },
    {
      assignment_id: 'broker-cash-assignment',
      taxonomy_id: taxonomy.taxonomy_id,
      target_scope: 'cash_bucket',
      target_entity_id: 'broker',
      taxonomy_node_id: 'cash',
      status: 'active',
    },
  ],
  instrument_universe: [],
  target_sets: [],
  target_set_lines: [],
  target_set_integrity_issues: [],
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
      currency: 'USD',
      status: 'active',
    },
    linked_transaction_count: 0,
    linked_posting_count: 0,
    derived_cash_balance: cash,
    derived_cash_balance_base: cash,
    pending_settlement: pendingSettlement,
    pending_settlement_base: pendingSettlement,
    account_value_base: accountValue,
    position_line_count: positionMarketValue ? 1 : 0,
    position_market_value: positionMarketValue,
    position_market_value_currency: 'USD',
  }
}

describe('Current planning cash and pending settlement', () => {
  it('includes deposit and broker liquidity while counting securities positions only through holdings', () => {
    const holdingsWorkspace = holdingsWorkspaceFixture({
      rows: [
        holdingFixture({
          allocation: 0.8,
          market_value: 800,
          market_value_base: 800,
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
            identifiers: [],
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
        currentWeight: 0.8,
        currentValueBase: 800,
        marketWeight: 0.8,
        cashWeight: null,
      }),
      expect.objectContaining({
        groupKey: 'cash',
        currentWeight: 0.2,
        currentValueBase: 200,
        marketWeight: null,
        cashWeight: 0.2,
      }),
    ])
    expect(result.value.reduce((total, row) => total + (row.currentValueBase ?? 0), 0)).toBe(1000)
  })
})
