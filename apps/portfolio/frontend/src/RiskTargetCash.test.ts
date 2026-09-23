import { describe, expect, it } from 'vitest'
import type { PortfolioResolvedMemberTarget, PortfolioTaxonomyNodeRecord } from './lib/api'
import { buildTargetGapRows } from './pages/RiskPage'

const node: PortfolioTaxonomyNodeRecord = { taxonomy_node_id: 'risk-assets', taxonomy_id: 'taxonomy-1', parent_taxonomy_node_id: null,
  node_name: 'Risk Assets', sort_order: 1, is_terminal: true, allocation_basis: 'risk_budget', status: 'active' }
const target: PortfolioResolvedMemberTarget = { scope_node_id: null, member_type: 'taxonomy_node', member_id: node.taxonomy_node_id,
  taxonomy_node_id: node.taxonomy_node_id, target_basis: 'risk_budget', strategic_value: 1, tactical_value: 1,
  strategic_source: 'saa', tactical_source: 'saa', strategic_target_set_id: 'saa-root', tactical_target_set_id: 'saa-root',
  strategic_global_risk_target: 1, tactical_global_risk_target: 1, strategic_status: 'complete', tactical_status: 'complete' }
const currentGroups = [{ groupKey: 'risk-assets', label: 'Risk Assets', currentWeight: 0.65, currentValueBase: 650 },
  { groupKey: 'cash_bucket:__cash__', label: 'Cash', currentWeight: 0.35, currentValueBase: 350 }]
const base = { resolvedTargets: [target], stage: 'taa' as const, currentGroups, riskSharesByGroup: new Map([['risk-assets', 1]]),
  nodeById: new Map([[node.taxonomy_node_id, node]]), baseCurrency: 'USD' }

describe('Risk consumes the unified portfolio target resolution', () => {
  it('uses inherited whole-level TAA risk targets and excludes cash', () => {
    const result = buildTargetGapRows(base)
    expect(result.errors).toEqual([])
    expect(result.value).toEqual([expect.objectContaining({ key: 'risk-assets', current: 1, target: 1, gap: 0 })])
  })
  it('does not turn a local weight target into a portfolio risk target', () => {
    const result = buildTargetGapRows({ ...base, resolvedTargets: [{ ...target, target_basis: 'weight', tactical_global_risk_target: null }] })
    expect(result.value).toEqual([])
  })
  it('keeps a positive target for a member without current holdings', () => {
    const result = buildTargetGapRows({ ...base, currentGroups: [], riskSharesByGroup: new Map() })
    expect(result.value).toEqual([expect.objectContaining({ key: 'risk-assets', current: 0, target: 1, gap: -1 })])
  })
  it('does not turn missing modeled risk for a held member into zero', () => {
    const result = buildTargetGapRows({ ...base, riskSharesByGroup: new Map() })
    expect(result.value).toEqual([])
    expect(result.errors).toEqual(['TAA risk target gap is missing current risk share for Risk Assets.'])
  })
  it('keeps root comparison separate from descendant targets', () => {
    const result = buildTargetGapRows({ ...base, resolvedTargets: [target, { ...target, scope_node_id: 'risk-assets', member_type: 'instrument', member_id: 'alpha' }] })
    expect(result.value).toHaveLength(1)
  })
})
