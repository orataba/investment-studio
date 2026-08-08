import { describe, expect, it } from 'vitest'

import type { PortfolioTaxonomyCatalogResponse } from './lib/api'
import { buildCanonicalTaxonomyRiskContributionRows } from './pages/RiskPage'
import { holdingFixture, holdingsWorkspaceFixture, instrumentFixture } from './test/portfolioFixtures'

const taxonomy = {
  taxonomy_id: 'risk-taxonomy',
  portfolio_id: '3',
  name: 'Risk taxonomy',
  taxonomy_type: 'allocation',
  primary_assignment_scope: 'instrument',
  planning_enabled: true,
  budgeting_level: 'root',
  root_default_target_dimension: 'risk_budget',
  status: 'active',
} as const

const catalog: PortfolioTaxonomyCatalogResponse = {
  portfolio_id: '3',
  default_planning_taxonomy_id: taxonomy.taxonomy_id,
  taxonomies: [taxonomy],
  taxonomy_nodes: [
    {
      taxonomy_node_id: 'equity',
      taxonomy_id: taxonomy.taxonomy_id,
      parent_taxonomy_node_id: null,
      node_name: 'Equity',
      sort_order: 1,
      is_terminal: true,
      default_target_dimension: 'risk_budget',
      status: 'active',
    },
    {
      taxonomy_node_id: 'diversifiers',
      taxonomy_id: taxonomy.taxonomy_id,
      parent_taxonomy_node_id: null,
      node_name: 'Diversifiers',
      sort_order: 2,
      is_terminal: true,
      default_target_dimension: 'risk_budget',
      status: 'active',
    },
  ],
  taxonomy_assignments: [
    {
      assignment_id: 'assign-alpha',
      taxonomy_id: taxonomy.taxonomy_id,
      target_scope: 'instrument',
      target_entity_id: 'alpha',
      taxonomy_node_id: 'equity',
      status: 'active',
    },
    {
      assignment_id: 'assign-beta',
      taxonomy_id: taxonomy.taxonomy_id,
      target_scope: 'instrument',
      target_entity_id: 'beta',
      taxonomy_node_id: 'equity',
      status: 'active',
    },
    {
      assignment_id: 'assign-gamma',
      taxonomy_id: taxonomy.taxonomy_id,
      target_scope: 'instrument',
      target_entity_id: 'gamma',
      taxonomy_node_id: 'diversifiers',
      status: 'active',
    },
  ],
  instrument_universe: [],
  target_sets: [],
  target_set_lines: [],
  target_set_integrity_issues: [],
  analytics_scope_policy_version: 0,
  analytics_scope_policies: [],
  analytics_taxonomy_selections: [],
}

function canonicalHolding(
  instrumentId: string,
  allocation: number,
  riskShare: number,
  contributionToVariance: number,
  riskBudgetEligible = true,
) {
  return holdingFixture({
    line_id: `holding:${instrumentId}`,
    instrument_core: instrumentFixture({
      instrument_id: instrumentId,
      instrument_name: instrumentId,
    }),
    allocation,
    market_value: allocation * 1000,
    market_value_base: allocation * 1000,
    forward_risk_share: riskShare,
    forward_contribution_to_variance: contributionToVariance,
    forward_risk_status: 'ok',
    risk_budget_eligible: riskBudgetEligible,
  })
}

describe('Canonical taxonomy risk contribution aggregation', () => {
  it('adds leaf contribution shares after the production covariance model has run once', () => {
    const workspace = holdingsWorkspaceFixture({
      rows: [
        canonicalHolding('alpha', 0.2, 0.2, 0.002),
        canonicalHolding('beta', 0.3, 0.3, 0.003),
        canonicalHolding('gamma', 0.5, 0.5, 0.005),
      ],
      forward_risk: {
        status: 'ok',
        errors: [],
        scope_name: 'Modeled Market Sleeve',
        scope_policy_versions: [1],
        configuration_versions: [2],
        total_nav: 1000,
        modeled_net_exposure: 1000,
        modeled_gross_exposure: 1000,
        excluded_carrying_value: 0,
        excluded_liability: 0,
        cash_unallocated_exposure: 0,
        coverage_ratio: 1,
        excluded_rows: [],
        calculation_frequency: 'daily',
        modeled_weight_basis: 'eligible_gross_exposure',
        portfolio_variance: 0.01,
        portfolio_volatility: 0.1,
        observation_count: 61,
      },
    })

    const result = buildCanonicalTaxonomyRiskContributionRows({
      holdingsWorkspace: workspace,
      catalog,
      taxonomy,
      referenceDate: workspace.as_of_date,
    })

    expect(result.errors).toEqual([])
    expect(result.value).toEqual([
      expect.objectContaining({
        groupKey: 'diversifiers',
        weight: 0.5,
        riskShare: 0.5,
        contributionToVariance: 0.005,
      }),
      expect.objectContaining({
        groupKey: 'equity',
        weight: 0.5,
        riskShare: 0.5,
        contributionToVariance: 0.005,
      }),
    ])
  })

  it('preserves the backend reason when production risk is unavailable', () => {
    const workspace = holdingsWorkspaceFixture({
      forward_risk: {
        status: 'unavailable',
        errors: ['Strict return coverage is stale by 8 days.'],
        scope_name: 'Modeled Market Sleeve',
        scope_policy_versions: [1],
        configuration_versions: [2],
        total_nav: 1000,
        modeled_net_exposure: 1000,
        modeled_gross_exposure: 1000,
        excluded_carrying_value: 0,
        excluded_liability: 0,
        cash_unallocated_exposure: 0,
        coverage_ratio: 1,
        excluded_rows: [],
        calculation_frequency: 'daily',
      },
    })

    const result = buildCanonicalTaxonomyRiskContributionRows({
      holdingsWorkspace: workspace,
      catalog,
      taxonomy,
      referenceDate: workspace.as_of_date,
    })

    expect(result.value).toEqual([])
    expect(result.errors).toEqual(['Strict return coverage is stale by 8 days.'])
  })

  it('renormalizes risk contribution inside the risk-budget-eligible sleeve', () => {
    const workspace = holdingsWorkspaceFixture({
      rows: [
        canonicalHolding('alpha', 0.2, 0.2, 0.002),
        canonicalHolding('beta', 0.3, 0.3, 0.003),
        canonicalHolding('gamma', 0.5, 0.5, 0.005, false),
      ],
      forward_risk: {
        status: 'ok',
        errors: [],
        scope_name: 'Modeled Market Sleeve',
        scope_policy_versions: [1],
        configuration_versions: [2],
        total_nav: 1000,
        modeled_net_exposure: 1000,
        modeled_gross_exposure: 1000,
        excluded_carrying_value: 0,
        excluded_liability: 0,
        cash_unallocated_exposure: 0,
        coverage_ratio: 1,
        excluded_rows: [],
        calculation_frequency: 'daily',
        modeled_weight_basis: 'eligible_gross_exposure',
        portfolio_variance: 0.01,
        portfolio_volatility: 0.1,
        observation_count: 61,
      },
    })

    const result = buildCanonicalTaxonomyRiskContributionRows({
      holdingsWorkspace: workspace,
      catalog,
      taxonomy,
      referenceDate: workspace.as_of_date,
      eligibility: 'risk_budget',
    })

    expect(result.errors).toEqual([])
    expect(result.value).toEqual([
      expect.objectContaining({
        groupKey: 'equity',
        weight: 0.5,
        riskShare: 1,
        contributionToVariance: 0.005,
      }),
    ])
  })
})
