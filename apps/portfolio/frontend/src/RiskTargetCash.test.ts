import { describe, expect, it } from 'vitest'

import type {
  PortfolioTargetSetLineRecord,
  PortfolioTargetSetRecord,
  PortfolioTaxonomyNodeRecord,
} from './lib/api'
import { buildTargetGapRows } from './pages/RiskPage'

function targetLine(
  targetLineId: string,
  targetMemberType: PortfolioTargetSetLineRecord['target_member_type'],
  targetMemberId: string,
  targetWeight: number | null,
  targetRiskShare: number | null,
): PortfolioTargetSetLineRecord {
  return {
    target_line_id: targetLineId,
    target_set_id: 'saa-root',
    target_member_type: targetMemberType,
    target_member_id: targetMemberId,
    taxonomy_node_id: targetMemberType === 'taxonomy_node' ? targetMemberId : null,
    target_weight: targetWeight,
    target_risk_share: targetRiskShare,
  }
}

const targetSet: PortfolioTargetSetRecord = {
  target_set_id: 'saa-root',
  taxonomy_id: 'taxonomy-1',
  comparator_taxonomy_node_id: null,
  target_set_type: 'saa',
  name: 'Policy SAA',
  weight_enabled: true,
  risk_budget_enabled: true,
  status: 'active',
}

const riskAssetsNode: PortfolioTaxonomyNodeRecord = {
  taxonomy_node_id: 'risk-assets',
  taxonomy_id: 'taxonomy-1',
  parent_taxonomy_node_id: null,
  node_name: 'Risk Assets',
  node_code: null,
  sort_order: 1,
  is_terminal: true,
  default_target_dimension: 'risk_budget',
  status: 'active',
}
const nodeById = new Map([[riskAssetsNode.taxonomy_node_id, riskAssetsNode]])
const currentGroups = [
  {
    groupKey: 'risk-assets',
    label: 'Risk Assets',
    currentWeight: 0.65,
    currentValueBase: 650,
  },
  {
    groupKey: 'derivative_bucket:__derivatives__',
    label: 'Derivatives',
    currentWeight: 0.15,
    currentValueBase: 150,
  },
  {
    groupKey: 'cash_bucket:__cash__',
    label: 'Cash',
    currentWeight: 0.2,
    currentValueBase: 200,
  },
]
const targetLines = [
  targetLine('risk-line', 'taxonomy_node', 'risk-assets', 0.7, 1),
  targetLine('derivative-line', 'derivative_bucket', '__derivatives__', 0.1, null),
  targetLine('cash-line', 'cash_bucket', '__cash__', 0.2, null),
]
const riskBudgetEligibleNodeIds = new Set(['risk-assets'])

describe('Risk target system bucket boundary', () => {
  it('compares fixed Derivatives and Cash members in the weight target gap', () => {
    const result = buildTargetGapRows({
      targetSet,
      targetLines,
      currentGroups,
      riskSharesByGroup: new Map(),
      nodeById,
      riskBudgetEligibleNodeIds,
      dimension: 'weight',
      baseCurrency: 'USD',
    })

    expect(result.errors).toEqual([])
    expect(result.value).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ key: 'risk-assets', label: 'Risk Assets', current: 0.65, target: 0.7 }),
        expect.objectContaining({
          key: 'derivative_bucket:__derivatives__',
          label: 'Derivatives',
          current: 0.15,
          target: 0.1,
        }),
        expect.objectContaining({ key: 'cash_bucket:__cash__', label: 'Cash', current: 0.2, target: 0.2 }),
      ]),
    )
  })

  it('excludes Derivatives and Cash from risk-target completeness and comparison', () => {
    const result = buildTargetGapRows({
      targetSet,
      targetLines,
      currentGroups,
      riskSharesByGroup: new Map([['risk-assets', 1]]),
      nodeById,
      riskBudgetEligibleNodeIds,
      dimension: 'risk_budget',
      baseCurrency: 'USD',
    })

    expect(result.errors).toEqual([])
    expect(result.value).toEqual([
      expect.objectContaining({
        key: 'risk-assets',
        label: 'Risk Assets',
        current: 1,
        target: 1,
        gap: 0,
      }),
    ])
  })

  it('returns an empty risk gap when the scope has no risk-bearing members', () => {
    const result = buildTargetGapRows({
      targetSet,
      targetLines: targetLines.slice(1),
      currentGroups: currentGroups.slice(1),
      riskSharesByGroup: new Map(),
      nodeById,
      riskBudgetEligibleNodeIds: new Set(),
      dimension: 'risk_budget',
      baseCurrency: 'USD',
    })

    expect(result.errors).toEqual([])
    expect(result.value).toEqual([])
  })
})
