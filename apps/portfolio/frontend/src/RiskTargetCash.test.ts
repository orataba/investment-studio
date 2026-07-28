import { describe, expect, it } from 'vitest'

import { buildTargetGapRows } from './pages/RiskPage'
import type {
  PortfolioTargetSetLineRecord,
  PortfolioTargetSetRecord,
  PortfolioTaxonomyNodeRecord,
} from './lib/api'

function node(
  taxonomyNodeId: string,
  nodeName: string,
  overrides: Partial<PortfolioTaxonomyNodeRecord> = {},
): PortfolioTaxonomyNodeRecord {
  return {
    taxonomy_node_id: taxonomyNodeId,
    taxonomy_id: 'taxonomy-1',
    parent_taxonomy_node_id: null,
    node_name: nodeName,
    node_code: null,
    sort_order: 1,
    is_terminal: true,
    default_target_dimension: 'weight',
    status: 'active',
    ...overrides,
  }
}

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

const riskAssetsNode = node('risk-assets', 'Risk Assets')
const cashNode = node('cash-node', 'Cash', { node_code: 'CASH', sort_order: 2 })
const cashChildNode = node('cash-child', 'Operating Liquidity', {
  parent_taxonomy_node_id: 'cash-node',
  sort_order: 3,
})
const assignmentCashNode = node('reserve-node', 'Reserve', { sort_order: 4 })
const nodeById = new Map(
  [riskAssetsNode, cashNode, cashChildNode, assignmentCashNode].map((item) => [item.taxonomy_node_id, item] as const),
)
const currentGroups = [
  {
    groupKey: 'risk-assets',
    label: 'Risk Assets',
    currentWeight: 0.7,
    currentValueBase: 700,
    hasMarketRiskInput: true,
    hasCashLikeInput: false,
  },
  {
    groupKey: 'cash-node',
    label: 'Cash',
    currentWeight: 0.3,
    currentValueBase: 300,
    hasMarketRiskInput: false,
    hasCashLikeInput: true,
  },
]

describe('Risk target cash boundary', () => {
  it('filters direct cash buckets, cash nodes, and descendants before risk completeness and totals', () => {
    const result = buildTargetGapRows({
      targetSet,
      targetLines: [
        targetLine('risk-line', 'taxonomy_node', 'risk-assets', 0.7, 1),
        targetLine('cash-node-line', 'taxonomy_node', 'cash-node', 0.3, null),
        targetLine('cash-child-line', 'taxonomy_node', 'cash-child', 0, 0),
        targetLine('cash-bucket-line', 'cash_bucket', '__cash__', 0, 0),
      ],
      currentGroups,
      riskSharesByGroup: new Map([
        ['risk-assets', 1],
        ['cash-node', null],
      ]),
      nodeById,
      dimension: 'risk_budget',
      baseCurrency: 'USD',
    })

    expect(result.errors).toEqual([])
    expect(result.value).toHaveLength(1)
    expect(result.value[0]).toMatchObject({
      key: 'risk-assets',
      label: 'Risk Assets',
      current: 1,
      target: 1,
    })
  })

  it('retains cash taxonomy nodes in the weight target gap', () => {
    const result = buildTargetGapRows({
      targetSet,
      targetLines: [
        targetLine('risk-line', 'taxonomy_node', 'risk-assets', 0.7, 1),
        targetLine('cash-node-line', 'taxonomy_node', 'cash-node', 0.3, null),
      ],
      currentGroups,
      riskSharesByGroup: new Map(),
      nodeById,
      dimension: 'weight',
      baseCurrency: 'USD',
    })

    expect(result.errors).toEqual([])
    expect(result.value.map((row) => row.label)).toEqual(expect.arrayContaining(['Risk Assets', 'Cash']))
  })

  it('compares one direct cash bucket against aggregate current cash without double-counting taxonomy groups', () => {
    const result = buildTargetGapRows({
      targetSet,
      targetLines: [
        targetLine('risk-line', 'taxonomy_node', 'risk-assets', 1, 1),
        targetLine('cash-bucket-line', 'cash_bucket', '__cash__', 0, 0),
      ],
      currentGroups: [
        {
          ...currentGroups[0],
          marketWeight: 0.7,
          marketValueBase: 700,
          cashWeight: 0,
          cashValueBase: 0,
        },
        {
          ...currentGroups[1],
          marketWeight: 0,
          marketValueBase: 0,
          cashWeight: 0.3,
          cashValueBase: 300,
        },
      ],
      riskSharesByGroup: new Map(),
      nodeById,
      dimension: 'weight',
      baseCurrency: 'USD',
    })

    expect(result.errors).toEqual([])
    expect(result.value).toHaveLength(2)
    expect(result.value.find((row) => row.label === 'Risk Assets')).toMatchObject({
      current: 0.7,
      target: 1,
    })
    expect(result.value.find((row) => row.label === 'Cash')).toMatchObject({
      key: 'cash_bucket:__cash__',
      current: 0.3,
      target: 0,
    })
  })

  it('filters an all-cash assignment subtree even when its label is not Cash', () => {
    const result = buildTargetGapRows({
      targetSet,
      targetLines: [
        targetLine('risk-line', 'taxonomy_node', 'risk-assets', 0.7, 1),
        targetLine('reserve-line', 'taxonomy_node', 'reserve-node', 0.3, null),
      ],
      currentGroups,
      riskSharesByGroup: new Map([['risk-assets', 1]]),
      nodeById,
      cashLikeNodeIds: new Set(['reserve-node']),
      dimension: 'risk_budget',
      baseCurrency: 'USD',
    })

    expect(result.errors).toEqual([])
    expect(result.value.map((row) => row.key)).toEqual(['risk-assets'])
  })

  it('returns an unavailable empty risk gap when a scope contains no risky members', () => {
    const result = buildTargetGapRows({
      targetSet,
      targetLines: [],
      currentGroups: [currentGroups[1]],
      riskSharesByGroup: new Map(),
      nodeById,
      dimension: 'risk_budget',
      baseCurrency: 'USD',
    })

    expect(result.errors).toEqual([])
    expect(result.value).toEqual([])
  })
})
